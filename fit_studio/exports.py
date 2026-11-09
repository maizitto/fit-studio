"""Explicit export profiles and conservative removal of known location/ID fields."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

IDENTIFIERS = {"id", "hk_uuid", "uuid", "user_id", "athlete_id", "athlete_name", "serial_number",
               "source_activity_id", "source_sha256", "file", "filename", "source_file", "device_id",
               "friendly_name", "email", "name", "title", "notes", "description"}
LOCATION_BLOCKS = {"route", "routes", "gps", "coordinates", "track", "trackpoints", "route_points", "polyline", "bounding_box"}


def is_location(key):
    key = key.lower()
    return (key in LOCATION_BLOCKS or key in {"lat", "lon", "lng", "latitude", "longitude"}
            or key.endswith(("_lat", "_long", "_lon", "_latitude", "_longitude")))


def redact(value, include_gps=False, include_identifiers=False):
    if isinstance(value, list):
        return [redact(item, include_gps, include_identifiers) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    # Columnar structures need both header and rows filtered together.
    columnar = {}
    for header, fields in value.items():
        if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
            continue
        rows_key = {"fields": "rows", "lap_fields": "laps", "swim_set_fields": "swim_sets",
                    "record_fields": "records", "sample_fields": "samples"}.get(header)
        if rows_key and isinstance(value.get(rows_key), list):
            if any(not isinstance(row, list) or len(row) != len(fields) for row in value[rows_key]):
                raise ValueError("Matrice nei dati sorgente non coerente: impossibile filtrare i campi in sicurezza.")
            keep = [i for i, field in enumerate(fields)
                    if (include_gps or not is_location(field))
                    and (include_identifiers or field.lower() not in IDENTIFIERS)]
            columnar[header] = [fields[i] for i in keep]
            columnar[rows_key] = [[row[i] for i in keep] for row in value[rows_key]]
    for key, item in value.items():
        if not include_gps and is_location(key):
            continue
        if not include_identifiers and key.lower() in IDENTIFIERS:
            continue
        if key in columnar:
            result[key] = redact(columnar[key], include_gps, include_identifiers)
        else:
            result[key] = redact(item, include_gps, include_identifiers)
    return result


def export_workout(workout, profile="compact", include_gps=False, include_identifiers=False):
    item = copy.deepcopy(workout)
    if profile == "compact":
        for key in ["source_data", "samples", "record", "records", "route", "routes", "gps", "track", "trackpoints"]:
            item.pop(key, None)
    item["schema"] = "fit-studio/workout/1"
    return redact(item, include_gps, include_identifiers)


def build_export(workouts, profile="compact", format="json", include_gps=False, include_identifiers=False):
    if profile not in {"compact", "detailed"} or format not in {"json", "jsonl"}:
        raise ValueError("Profilo o formato di export non valido.")
    items = [export_workout(w, profile, include_gps, include_identifiers) for w in workouts]
    if format == "jsonl":
        text = "\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":"), allow_nan=False) for item in items) + "\n"
    else:
        document = {"schema": "fit-studio/archive/1", "generated": datetime.now(timezone.utc).isoformat(),
                    "profile": profile, "count": len(items),
                    "conventions": {"missing": "null means unavailable; source legacy-zero warnings still apply",
                                    "history_coverage": "imported_files_only", "physiological_metrics": "not inferred",
                                    "location_fields_included": include_gps, "known_identifiers_included": include_identifiers},
                    "workouts": items}
        text = json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return text.encode("utf-8")
