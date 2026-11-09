"""Distance conservation, real boundaries and explicit timing-estimate regressions."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fit_studio.demo import demo_workouts
from fit_studio.exports import build_export
from fit_studio.importers import parse_upload
from fit_studio.splits import boundaries


def activity(distance=6400, duration=1920, laps=None, samples=None, elapsed=None):
    item = {"sport": "running", "ts": "2026-11-08T08:00:00Z", "v": 1,
        "session": {"dist_m": distance, "dur_s": duration, "elapsed_s": duration if elapsed is None else elapsed},
        "lap_fields": ["i", "dist_m", "dur_s", "fc_avg", "fc_max", "power"],
        "laps": laps if laps is not None else [[1, distance, duration, 140, 175, 250]],
        "metric_units": {"session_pace": "min/km"}, "quality": {"warnings": []}}
    if samples is not None:
        item["samples"] = {"fields": ["t_s", "distance_m", "fc", "power_w"], "rows": samples}
    return item


def parse(source):
    return parse_upload(json.dumps(source).encode(), "synthetic.json")[0]


def column(item, name):
    index = item["lap_fields"].index(name)
    return [row[index] for row in item["laps"]]


class KilometreSplitTests(unittest.TestCase):
    def test_6400_is_six_kilometres_and_400_not_six_equal_laps(self):
        item = parse(activity())
        self.assertEqual(column(item, "dist_m"), [1000] * 6 + [400])
        self.assertEqual(column(item, "dur_s"), [300] * 6 + [120])
        self.assertEqual(column(item, "pace"), ["5:00"] * 7)
        self.assertEqual(sum(column(item, "dur_s")), 1920)

    def test_1070_metre_laps_are_rebinned_without_equalizing_pace(self):
        laps = [[i + 1, 1070, seconds, 130 + i * 5, 170, 250] for i, seconds in enumerate([300, 330, 360, 390, 420, 450])]
        item = parse(activity(6420, 2250, laps=laps))
        self.assertEqual(column(item, "dist_m"), [1000] * 6 + [420])
        self.assertAlmostEqual(column(item, "dur_s")[0], 300 * 1000 / 1070, places=5)
        self.assertNotEqual(column(item, "dur_s")[0], column(item, "dur_s")[1])
        self.assertAlmostEqual(sum(column(item, "dur_s")), 2250, places=4)
        self.assertIn("dur_s", item["split_quality"][0]["estimated_metrics"])

    def test_mixed_1028_1070_laps_keep_the_actual_remainder(self):
        laps = [[1, 1028, 310, 130, 160, 200], [2, 1070, 400, 150, 180, 250], [3, 1042, 350, 140, 170, 220]]
        item = parse(activity(3140, 1060, laps=laps))
        self.assertEqual(column(item, "dist_m"), [1000, 1000, 1000, 140])
        self.assertAlmostEqual(sum(column(item, "dur_s")), 1060, places=4)

    def test_exact_kilometres_do_not_add_an_empty_last_split(self):
        self.assertEqual(column(parse(activity(6000, 1800)), "dist_m"), [1000] * 6)

    def test_under_one_kilometre_and_fractional_remainder(self):
        self.assertEqual(column(parse(activity(820, 300)), "dist_m"), [820])
        item = parse(activity(6400.4, 1920))
        self.assertEqual(column(item, "dist_m"), [1000] * 6 + [400.4])
        self.assertAlmostEqual(sum(column(item, "dist_m")), 6400.4)

    def test_sample_distance_crossings_determine_unequal_real_times(self):
        source = activity(2400, 850, samples=[[0, 0, 100, 200], [270, 900, 120, 220],
            [370, 1100, 140, 240], [700, 2000, 150, 250], [850, 2400, 160, 260]])
        item = parse(source)
        self.assertEqual(column(item, "dist_m"), [1000, 1000, 400])
        self.assertEqual(column(item, "dur_s"), [320, 380, 150])
        self.assertEqual(item["split_quality"][0]["duration_basis"], "distance_samples_active_time")
        # Sparse sensor support must not be filled with the original lap average.
        self.assertEqual(column(item, "fc_avg"), [None, None, None])

    def test_heart_rate_is_weighted_by_time_across_boundaries(self):
        source = activity(2400, 30, samples=[[0, 0, 100, 0], [10, 800, 120, 100], [20, 1600, 140, 200], [30, 2400, 160, 300]])
        item = parse(source)
        self.assertEqual(column(item, "dur_s"), [12.5, 12.5, 5])
        self.assertEqual(column(item, "fc_avg"), [104, 128, 140])
        self.assertEqual(column(item, "power"), [20, 140, 200])

    def test_timer_events_exclude_pauses_without_rescaling(self):
        source = activity(2000, 60, elapsed=90, samples=[[0, 0, 100, 200], [15, 500, 110, 200],
            [45, 500, 120, 200], [60, 1000, 130, 200], [90, 2000, 140, 200]])
        source["source_data"] = {"event": [
            {"event": "timer", "event_type": "start", "timestamp": "2026-11-08T08:00:00Z"},
            {"event": "timer", "event_type": "stop", "timestamp": "2026-11-08T08:00:15Z"},
            {"event": "timer", "event_type": "start", "timestamp": "2026-11-08T08:00:45Z"},
            {"event": "timer", "event_type": "stop_all", "timestamp": "2026-11-08T08:01:30Z"}]}
        item = parse(source)
        self.assertEqual(column(item, "dur_s"), [30, 30])
        self.assertEqual([q["elapsed_s"] for q in item["split_quality"]], [60, 30])

    def test_pause_without_events_uses_declared_lap_timer_as_estimate(self):
        source = activity(2000, 60, elapsed=90, samples=[[0, 0, 100, 200], [45, 1000, 130, 200], [90, 2000, 140, 200]])
        item = parse(source)
        self.assertEqual(column(item, "dur_s"), [30, 30])
        self.assertEqual(item["split_quality"][0]["duration_basis"], "source_lap_summary")
        self.assertIn("dur_s", item["split_quality"][0]["estimated_metrics"])

    def test_no_split_time_is_inferred_from_session_average(self):
        source = activity(laps=[[1, 6400, None, 140, 170, 200]])
        item = parse(source)
        self.assertEqual(column(item, "dist_m"), [1000] * 6 + [400])
        self.assertEqual(column(item, "dur_s"), [None] * 7)
        self.assertEqual(column(item, "pace"), [None] * 7)

    def test_maximum_is_not_duplicated_into_each_fragment(self):
        item = parse(activity())
        self.assertEqual(column(item, "fc_max"), [None] * 7)

    def test_uncovered_tail_is_not_extrapolated(self):
        item = parse(activity(2400, 720, laps=[[1, 2000, 600, 140, 170, 200]]))
        self.assertEqual(column(item, "dist_m"), [1000, 1000, 400])
        self.assertEqual(column(item, "dur_s"), [300, 300, None])

    def test_source_laps_and_old_segment_quality_are_preserved_separately(self):
        source = activity()
        source["lap_metric_quality"] = [{"i": 1, "source": "original"}]
        source["segment_analysis"] = [[1, 2, 3]]
        item = parse(source)
        self.assertNotIn("lap_metric_quality", item)
        self.assertNotIn("segment_analysis", item)
        original = item["source_data"]["original_lap_summary"]
        self.assertEqual(original["laps"], source["laps"])
        self.assertEqual(original["lap_metric_quality"], source["lap_metric_quality"])

    def test_correct_existing_splits_keep_measured_quality(self):
        source = activity(1400, 420, laps=[[1, 1000, 300, 130, 150, 200], [2, 400, 120, 150, 170, 220]])
        source["lap_metric_quality"] = [{"i": 1, "source": "measured"}]
        item = parse(source)
        self.assertEqual(item["laps"], source["laps"])
        self.assertEqual(item["lap_metric_quality"], source["lap_metric_quality"])

    def test_compact_export_reimport_preserves_split_metrics_and_policy(self):
        first = parse(activity())
        second = parse_upload(build_export([first]), "archive.json")[0]
        for key in ["lap_fields", "laps", "split_policy", "split_quality"]:
            self.assertEqual(second[key], first[key])

    def test_demo_uses_kilometres_and_remainder(self):
        running = [item for item in demo_workouts() if item["sport"] == "running"]
        for item in running:
            distances = column(item, "dist_m")
            self.assertTrue(all(distance == 1000 for distance in distances[:-1]))
            self.assertLessEqual(distances[-1], 1000)
            self.assertAlmostEqual(sum(distances), item["session"]["dist_m"])
            self.assertAlmostEqual(sum(column(item, "dur_s")), item["session"]["dur_s"])
        self.assertEqual(column(next(item for item in running if item["session"]["dist_m"] == 6420), "dist_m"), [1000] * 6 + [420])

    def test_swim_and_non_distance_activities_are_unchanged(self):
        source = activity(); source["sport"] = "swimming"
        result = parse(source)
        self.assertEqual(result["laps"], source["laps"])
        self.assertNotIn("split_policy", result)

    def test_extreme_distance_does_not_allocate_unbounded_splits(self):
        self.assertEqual(boundaries(1e30), [])


if __name__ == "__main__":
    unittest.main()
