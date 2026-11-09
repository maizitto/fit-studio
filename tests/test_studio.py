"""Portable synthetic regressions; no private FIT files, network or personal archive."""
import copy
import json
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from fit_studio.app import create_app
from fit_studio.demo import demo_workouts
from fit_studio.exports import build_export
from fit_studio.importers import ImportProblem, parse_upload, pace


def compact(**changes):
    source = {"v": 1, "sport": "running", "ts": "2026-11-08T08:00:00Z",
              "session": {"dist_m": 5000, "dur_s": 1800, "fc_avg": None, "hk_uuid": "private-uuid"},
              "lap_fields": ["i", "dist_m", "fc_avg", "pace"], "laps": [[1, 5000, None, "6:00"]],
              "metric_units": {"session_pace": "min/km"}, "quality": {"warnings": []}}
    source.update(changes)
    return source


def crc16(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc


def synthetic_fit():
    # A real CRC-valid binary FIT activity, with one lap and two HR records.
    def definition(local, global_number, fields):
        return bytes([0x40 | local, 0, 0]) + struct.pack("<H", global_number) + bytes([len(fields)]) + b"".join(bytes(field) for field in fields)
    start = 1163000000
    session_fields = [(2, 4, 0x86), (253, 4, 0x86), (5, 1, 0), (7, 4, 0x86), (8, 4, 0x86), (9, 4, 0x86), (16, 1, 2)]
    lap_fields = [(2, 4, 0x86), (253, 4, 0x86), (8, 4, 0x86), (9, 4, 0x86), (15, 1, 2)]
    record_fields = [(253, 4, 0x86), (3, 1, 2), (5, 4, 0x86)]
    body = definition(0, 18, session_fields) + bytes([0]) + struct.pack("<IIBIIIB", start, start + 330, 1, 330000, 300000, 100000, 140)
    body += definition(1, 19, lap_fields) + bytes([1]) + struct.pack("<IIIIB", start, start + 330, 300000, 100000, 140)
    body += definition(2, 20, record_fields)
    body += bytes([2]) + struct.pack("<IBI", start, 130, 0)
    body += bytes([2]) + struct.pack("<IBI", start + 30, 145, 10000)
    header = struct.pack("<BBHI4s", 14, 0x20, 2140, len(body), b".FIT")
    header += struct.pack("<H", crc16(header))
    data = header + body
    return data + struct.pack("<H", crc16(data))


class ImportTests(unittest.TestCase):
    def parse(self, obj):
        return parse_upload(json.dumps(obj).encode(), "synthetic.json")

    def test_real_fit_decoding_preserves_timer_elapsed_and_samples(self):
        result = parse_upload(synthetic_fit(), "activity.fit")[0]
        self.assertEqual(result["sport"], "running")
        self.assertEqual(result["session"]["dur_s"], 300)
        self.assertEqual(result["session"]["elapsed_s"], 330)
        self.assertEqual(result["session"]["dist_m"], 1000)
        self.assertEqual(len(result["samples"]["rows"]), 2)
        self.assertEqual(result["laps"][0][-1], 300)

    def test_corrupt_fit_is_rejected(self):
        data = bytearray(synthetic_fit())
        data[-1] ^= 0xFF
        with self.assertRaises(ImportProblem):
            parse_upload(bytes(data), "broken.fit")

    def test_pace_rolls_over_and_missing_speed_stays_missing(self):
        self.assertEqual(pace(1000 / 359.6), "6:00")
        self.assertEqual(pace(100 / 119.6, 100), "2:00")
        self.assertIsNone(pace(None))
        self.assertIsNone(pace(0))

    def test_missing_values_not_changed_to_zero(self):
        item = self.parse(compact())[0]
        self.assertIsNone(item["session"]["fc_avg"])
        self.assertIsNone(item["laps"][0][2])
        self.assertIn("fc_avg", item["quality"]["missing_session_fields"])

    def test_legacy_pace_unit_is_not_guessed(self):
        item = compact(); item.pop("metric_units")
        result = self.parse(item)[0]
        self.assertEqual(result["metric_units"]["session_pace"], "unknown")
        self.assertIn("unknown_pace_unit", [w["code"] for w in result["quality"]["warnings"]])

    def test_legacy_zero_warning(self):
        item = compact(); item.pop("quality")
        result = self.parse(item)[0]
        self.assertIn("legacy_zeros", [w["code"] for w in result["quality"]["warnings"]])

    def test_invalid_or_duplicate_matrix_columns_are_rejected(self):
        for fields, rows in [(["i", "pace"], [[1]]), (["i", "i"], [[1, 2]])]:
            with self.subTest(fields=fields), self.assertRaises(ImportProblem):
                self.parse(compact(lap_fields=fields, laps=rows))

    def test_healthkit_botfield_summary(self):
        item = {"session_summary": {"sport": {"value": "running", "units": None},
            "start_time": {"value": "2026-11-08T08:00:00Z"}, "total_timer_time": {"value": 300},
            "total_distance": {"value": 1000}}, "laps_summary": []}
        result = self.parse(item)[0]
        self.assertEqual(result["session"]["pace_avg"], "5:00")
        self.assertIsNone(result["session"]["fc_avg"])

    def test_swim_missing_pool_and_rest_are_not_fabricated(self):
        item = {"session": [{"sport": "swimming", "start_time": "2026-11-08T08:00:00Z", "total_timer_time": 300,
                              "total_distance": 200}], "lap": [{"total_timer_time": 300, "total_distance": 200}]}
        result = self.parse(item)[0]
        self.assertIsNone(result["swim_pool_m"])
        self.assertIsNone(result["swim_sets"][0][-1])
        self.assertIsNone(result["swim_sets"][0][8])
        self.assertEqual(result["metric_units"]["session_pace"], "min/100m")

    def test_multi_session_source_does_not_mix_laps(self):
        item = {"session": [{"sport": "swimming", "start_time": "2026-11-08T08:00:00Z", "timestamp": "2026-11-08T08:10:00Z"},
                            {"sport": "running", "start_time": "2026-11-08T08:10:00Z", "timestamp": "2026-11-08T08:20:00Z"}],
                "lap": [{"start_time": "2026-11-08T08:00:00Z", "total_distance": 400},
                        {"start_time": "2026-11-08T08:10:00Z", "total_distance": 1500}]}
        first, second = self.parse(item)
        self.assertEqual(len(first["swim_sets"]), 1)
        self.assertEqual(len(second["laps"]), 2)
        di = second["lap_fields"].index("dist_m")
        self.assertEqual([row[di] for row in second["laps"]], [1000, 500])

    def test_jsonl_roundtrip_and_bom(self):
        items = demo_workouts()
        exported = build_export(items, format="jsonl")
        restored = parse_upload(b"\xef\xbb\xbf" + exported, "archive.jsonl")
        self.assertEqual(len(restored), len(items))
        self.assertEqual(restored[0]["session"], items[0]["session"])

    def test_invalid_json_and_non_workout_are_rejected(self):
        for raw in [b"{", b"{}", b"[]", b'{"workouts": []}', b'{"sport":"running", "session":{}}']:
            with self.subTest(raw=raw), self.assertRaises(ImportProblem):
                parse_upload(raw, "invalid.json")


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.workout = parse_upload(json.dumps(compact()).encode(), "private-file.json")[0]
        self.workout["samples"] = {"fields": ["t_s", "fc", "lat", "lon"], "rows": [[0, None, 45.1, 9.1]]}
        self.workout["source_data"]["record"] = [{"timestamp": "2026-11-08T08:00:00Z", "position_lat": 123, "serial_number": 999}]

    def test_compact_drops_streams_but_preserves_null_and_quality(self):
        result = json.loads(build_export([self.workout]))["workouts"][0]
        self.assertNotIn("samples", result)
        self.assertNotIn("source_data", result)
        self.assertIsNone(result["session"]["fc_avg"])
        self.assertIn("quality", result)

    def test_default_export_filters_ids_and_coordinates_recursively(self):
        exported = build_export([self.workout], profile="detailed")
        text = exported.decode()
        self.assertNotIn("private-uuid", text)
        self.assertNotIn("private-file.json", text)
        self.assertNotIn("position_lat", text)
        self.assertNotIn("serial_number", text)
        item = json.loads(exported)["workouts"][0]
        self.assertEqual(item["samples"]["fields"], ["t_s", "fc"])
        self.assertEqual(item["samples"]["rows"], [[0, None]])

    def test_explicit_options_preserve_known_identifiers_and_coordinates(self):
        exported = build_export([self.workout], profile="detailed", include_gps=True, include_identifiers=True).decode()
        self.assertIn("private-file.json", exported)
        self.assertIn("position_lat", exported)
        self.assertIn("private-uuid", exported)

    def test_export_does_not_mutate_source(self):
        before = copy.deepcopy(self.workout)
        build_export([self.workout], profile="detailed")
        self.assertEqual(self.workout, before)

    def test_inconsistent_source_matrix_is_not_silently_exported(self):
        self.workout["source_data"]["samples"] = {"fields": ["t_s", "lat"], "rows": [[0]]}
        with self.assertRaises(ValueError):
            build_export([self.workout], profile="detailed")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app())
        self.headers = {"X-Fit-Studio": "1"}

    def test_real_user_journey_import_duplicate_inspect_preview_export_reimport(self):
        body = json.dumps(compact()).encode()
        response = self.client.post("/api/import?filename=session.json", content=body, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        workout_id = response.json()["added"][0]
        duplicate = self.client.post("/api/import?filename=renamed.json", content=body, headers=self.headers)
        self.assertEqual(duplicate.json()["duplicates"], 1)
        self.assertEqual(len(self.client.get("/api/workouts").json()["workouts"]), 1)
        self.assertEqual(self.client.get(f"/api/workouts/{workout_id}").status_code, 200)
        options = {"ids": [workout_id], "profile": "compact", "format": "json"}
        preview = self.client.post("/api/export/preview", json=options, headers=self.headers)
        self.assertEqual(preview.json()["count"], 1)
        exported = self.client.post("/api/export", json=options, headers=self.headers)
        self.assertIn("attachment", exported.headers["content-disposition"])
        self.assertEqual(len(parse_upload(exported.content, "roundtrip.json")), 1)
        self.assertEqual(self.client.delete(f"/api/workouts/{workout_id}", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get("/api/workouts").json()["workouts"], [])

    def test_multifile_json_is_atomic_on_invalid_workout(self):
        body = json.dumps({"workouts": [compact(), {"bad": True}]}).encode()
        response = self.client.post("/api/import?filename=batch.json", content=body, headers=self.headers)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/api/workouts").json()["workouts"], [])

    def test_demo_is_explicit_synthetic_and_idempotent(self):
        self.assertEqual(len(self.client.get("/api/workouts").json()["workouts"]), 0)
        first = self.client.post("/api/demo", headers=self.headers)
        self.assertEqual(len(first.json()["added"]), 6)
        second = self.client.post("/api/demo", headers=self.headers)
        self.assertEqual(second.json()["duplicates"], 6)
        self.assertTrue(all(w["quality"]["synthetic"] for w in self.client.get("/api/workouts").json()["workouts"]))

    def test_cross_origin_and_requests_without_local_header_are_rejected(self):
        self.assertEqual(self.client.post("/api/demo").status_code, 403)
        self.assertEqual(self.client.post("/api/demo", headers={**self.headers, "Origin": "https://untrusted.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/demo", headers={**self.headers, "Origin": "http://testserver"}).status_code, 200)

    def test_empty_export_missing_id_and_bad_options(self):
        for body in [{"ids": []}, {"ids": ["missing"]}, {"ids": [12]}]:
            response = self.client.post("/api/export", json=body, headers=self.headers)
            self.assertIn(response.status_code, [400, 404])

    def test_page_and_assets_have_local_content_policy(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("FIT Studio", response.text)
        self.assertIn("connect-src 'self'", response.headers["content-security-policy"])
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)


if __name__ == "__main__":
    unittest.main()
