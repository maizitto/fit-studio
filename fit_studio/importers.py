"""Normalize FIT, HealthKitExporter and FIT Studio JSON without guessed thresholds.

Source measurements remain nullable. No readiness, TRIMP, HR-zone or physiological
drift calculation is performed here. Detailed exports retain decoded source data;
they are not a lossless replacement for the original binary FIT file.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

import fitparse

from .splits import normalize_splits

LAP_FIELDS = ["i", "fc_avg", "fc_max", "power", "cad", "pace", "stance_ms",
              "vosc_mm", "step_mm", "vr_pct", "asc_m", "desc_m", "dist_m", "dur_s"]
SWIM_FIELDS = ["i", "dist_m", "laps", "stroke", "dur_s", "pace_100m",
               "fc_avg", "strokes", "swolf", "cal", "rest_s"]
SAMPLE_FIELDS = ["t_s", "fc", "power_w", "speed_mps", "cad", "distance_m", "altitude_m", "lat", "lon"]
SPORTS = {"run": "running", "corsa": "running", "bike": "cycling", "riding": "cycling",
          "ciclismo": "cycling", "swim": "swimming", "nuoto": "swimming",
          "walk": "walking", "camminata": "walking"}


class ImportProblem(ValueError):
    """A readable error for an unsupported or inconsistent source file."""


def finite(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (float, int)) and math.isfinite(value):
        return value
    return None


def number(value, digits=1):
    value = finite(value)
    return round(value, digits) if value is not None else None


def pace(speed, distance=1000):
    speed = finite(speed)
    if speed is None or speed <= 0:
        return None
    seconds = int(math.floor(distance / speed + 0.5))
    return f"{seconds // 60}:{seconds % 60:02d}"


def parse_time(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def iso(value):
    date = parse_time(value)
    return date.isoformat().replace("+00:00", "Z") if date else None


def clean(value):
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, bytes):
        return value.hex()
    return value


def unbox(value):
    if isinstance(value, dict):
        if "value" in value and set(value) <= {"value", "units", "unit", "display"}:
            return unbox(value["value"])
        return {k: unbox(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unbox(v) for v in value]
    return value


def first(*values):
    return next((v for v in values if v is not None), None)


def warning(code, message, level="warning"):
    return {"code": code, "message": message, "level": level}


def validate_matrix(fields, rows, label):
    if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
        raise ImportProblem(f"Intestazione {label} non valida.")
    if len(fields) > 128 or len(set(fields)) != len(fields):
        raise ImportProblem(f"Colonne {label} duplicate o troppo numerose.")
    if not isinstance(rows, list) or any(not isinstance(r, list) or len(r) != len(fields) for r in rows):
        raise ImportProblem(f"Le righe {label} non corrispondono alle colonne dichiarate.")


def normalized_sport(value):
    sport = str(value or "unknown").lower().replace(" ", "_")
    return SPORTS.get(sport, sport)


def finish(workout, filename, digest, source_format):
    workout = clean(workout)
    session = workout.get("session")
    if not isinstance(session, dict):
        raise ImportProblem("Il riepilogo della sessione non è un oggetto JSON.")
    timestamp = iso(workout.get("ts"))
    if not timestamp:
        raise ImportProblem("Data di inizio mancante o non valida: impossibile identificare la seduta.")
    workout["ts"] = timestamp
    workout["sport"] = normalized_sport(workout.get("sport"))
    for fields_key, rows_key in [("lap_fields", "laps"), ("swim_set_fields", "swim_sets")]:
        if fields_key in workout or rows_key in workout:
            validate_matrix(workout.get(fields_key), workout.get(rows_key), rows_key)
    if "samples" in workout:
        sample = workout["samples"]
        if not isinstance(sample, dict):
            raise ImportProblem("Serie temporale non valida.")
        validate_matrix(sample.get("fields"), sample.get("rows"), "campioni")
    for key in ["dur_s", "elapsed_s", "moving_s", "dist_m", "fc_avg", "fc_max", "power_avg", "cal"]:
        if key in session:
            session[key] = finite(session[key])
            if session[key] is not None and session[key] < 0:
                raise ImportProblem(f"Valore negativo non valido: {key}.")
    quality = workout.get("quality", {})
    quality = quality if isinstance(quality, dict) else {"source_quality": quality}
    warnings = quality.get("warnings", [])
    warnings = [w for w in warnings if isinstance(w, dict) and isinstance(w.get("message"), str)] if isinstance(warnings, list) else []
    missing = [key for key in ["dur_s", "dist_m", "fc_avg"] if session.get(key) is None]
    if missing and not any(w.get("code") == "missing_metrics" for w in warnings):
        warnings.append(warning("missing_metrics", "Alcune misure di sessione non sono disponibili; restano null nell’export.", "info"))
    quality.update({"warnings": warnings, "missing_session_fields": missing,
                    "history_coverage": "imported_files_only"})
    workout["quality"] = quality
    units = workout.setdefault("metric_units", {})
    if not isinstance(units, dict):
        raise ImportProblem("metric_units deve essere un oggetto.")
    units.setdefault("heart_rate", "bpm")
    units.setdefault("distance", "m")
    units.setdefault("duration", "s")
    units.setdefault("power", "W")
    units.setdefault("session_pace", "unknown")
    units.setdefault("lap_pace", "min/km")
    units.setdefault("swim_set_pace", "min/100m")
    provenance = workout.get("provenance", {})
    if not isinstance(provenance, dict):
        provenance = {"source_provenance": provenance}
    provenance.update({"import_format": source_format, "file": PurePosixPath(filename.replace('\\', '/')).name,
                       "source_sha256": digest, "extractor_version": "fit-studio/0.1.0"})
    workout["provenance"] = provenance
    # Exact identity only: similar activities are not silently merged.
    uuid = session.get("hk_uuid")
    identity = [workout["sport"], timestamp, session.get("dist_m"), session.get("dur_s")]
    workout["id"] = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()[:24]
    if uuid:
        workout["source_activity_id"] = str(uuid)
    workout["v"] = 1
    return normalize_splits(workout)


def from_messages(session, laps, records, lengths, filename, digest, source_format, events=None):
    """Read source summaries directly; do not replace missing data with zero."""
    sport = normalized_sport(session.get("sport"))
    swim = sport == "swimming"
    start = parse_time(session.get("start_time"))
    if start is None:
        raise ImportProblem("La sessione non contiene una data di inizio valida.")
    duration = finite(session.get("total_timer_time"))
    distance = finite(session.get("total_distance"))
    speed = first(finite(session.get("enhanced_avg_speed")), finite(session.get("avg_speed")))
    speed_source = "source_average_speed"
    if speed is None and distance is not None and duration is not None and duration > 0:
        speed = distance / duration
        speed_source = "timer_distance_ratio"
    fields = {"dur_s": "total_timer_time", "elapsed_s": "total_elapsed_time", "moving_s": "total_moving_time",
              "dist_m": "total_distance", "fc_avg": "avg_heart_rate", "fc_max": "max_heart_rate",
              "power_avg": "avg_power", "cal": "total_calories", "asc_m": "total_ascent", "desc_m": "total_descent"}
    out = {"sport": sport, "ts": iso(start), "session": {k: number(session.get(v)) for k, v in fields.items()},
           "metric_units": {"session_pace": "min/100m" if swim else "min/km", "lap_pace": "min/km",
                            "cadence": "rpm" if sport == "cycling" else "steps/min"},
           "quality": {"warnings": [], "duration_basis": "source_total_timer_time", "pace_basis": speed_source,
                       "hr_summary_basis": "source_session_summary", "segmentation_source": "recorded_laps"},
           "source_data": {"session": session, "lap": laps, "record": records, "length": lengths, "event": events or []}}
    out["session"]["pace_avg"] = pace(speed, 100 if swim else 1000)
    out["session"]["pace_s_km"] = number(1000 / speed) if speed and speed > 0 else None
    warnings = out["quality"]["warnings"]
    if swim:
        out["swim_pool_m"] = number(session.get("pool_length"))
        out["swim_set_fields"] = list(SWIM_FIELDS)
        out["swim_sets"] = []
        for i, lap in enumerate(laps):
            seconds = finite(lap.get("total_timer_time"))
            dist = finite(lap.get("total_distance"))
            count = first(finite(lap.get("num_active_lengths")), finite(lap.get("num_lengths")))
            strokes = first(finite(lap.get("total_cycles")), finite(lap.get("total_strokes")))
            lap_speed = first(finite(lap.get("enhanced_avg_speed")), finite(lap.get("avg_speed")))
            if lap_speed is None and seconds and dist is not None:
                lap_speed = dist / seconds
            # This is the recorded clock gap, not an inferred recovery purpose.
            end = parse_time(lap.get("timestamp"))
            next_start = parse_time(laps[i + 1].get("start_time")) if i + 1 < len(laps) else None
            rest = number(max(0, (next_start - end).total_seconds())) if end and next_start else None
            swolf = number((seconds + strokes) / count, 0) if count and seconds is not None and strokes is not None else None
            out["swim_sets"].append([i + 1, number(dist), number(count, 0), lap.get("swim_stroke"),
                number(seconds), pace(lap_speed, 100), number(lap.get("avg_heart_rate")), number(strokes, 0),
                swolf, number(lap.get("total_calories")), rest])
        out["quality"]["swim_rows_are"] = "recorded FIT/JSON laps, not inferred prescribed sets"
        out["quality"]["rest_basis"] = "clock_gap_between_recorded_laps; last_or_unknown=null"
        if lengths:
            out["swim_lengths"] = {"observed": len(lengths), "active": sum(l.get("length_type") == "active" for l in lengths),
                                   "idle": sum(l.get("length_type") == "idle" for l in lengths)}
        if out["swim_pool_m"] is None:
            warnings.append(warning("unknown_pool", "Lunghezza della piscina non disponibile.", "info"))
    else:
        out["lap_fields"] = list(LAP_FIELDS)
        out["laps"] = []
        for i, lap in enumerate(laps):
            seconds, dist = finite(lap.get("total_timer_time")), finite(lap.get("total_distance"))
            lap_speed = first(finite(lap.get("enhanced_avg_speed")), finite(lap.get("avg_speed")))
            if lap_speed is None and seconds and dist is not None:
                lap_speed = dist / seconds
            cadence = first(finite(lap.get("avg_running_cadence")), finite(lap.get("avg_cadence")))
            if cadence is not None:
                cadence += finite(lap.get("avg_fractional_cadence")) or 0
                if sport == "running":
                    cadence *= 2
            out["laps"].append([i + 1, number(lap.get("avg_heart_rate")), number(lap.get("max_heart_rate")),
                number(lap.get("avg_power")), number(cadence), pace(lap_speed), number(lap.get("avg_stance_time")),
                number(lap.get("avg_vertical_oscillation")), number(lap.get("avg_step_length")), number(lap.get("avg_vertical_ratio")),
                number(lap.get("total_ascent")), number(lap.get("total_descent")), number(dist), number(seconds)])
    rows = []
    for record in records:
        timestamp = parse_time(record.get("timestamp"))
        if timestamp is None:
            continue
        cadence = first(finite(record.get("running_cadence")), finite(record.get("cadence")))
        if cadence is not None and sport == "running":
            cadence *= 2
        lat, lon = finite(record.get("position_lat")), finite(record.get("position_long"))
        # Only the binary FIT contract guarantees semicircle units. For a legacy
        # JSON without coordinate units, keep the original in source_data instead
        # of inventing a conversion in the normalized samples.
        lat = lat * 180 / 2**31 if lat is not None and source_format == "fit" else None
        lon = lon * 180 / 2**31 if lon is not None and source_format == "fit" else None
        rows.append([round((timestamp - start).total_seconds(), 2), number(record.get("heart_rate")),
            number(record.get("power")), number(first(record.get("enhanced_speed"), record.get("speed")), 3),
            number(cadence), number(record.get("distance")), number(first(record.get("enhanced_altitude"), record.get("altitude"))),
            number(lat, 6), number(lon, 6)])
    rows.sort(key=lambda row: row[0])
    out["samples"] = {"fields": list(SAMPLE_FIELDS), "rows": rows}
    hr_times = [r[0] for r in rows if r[1] is not None and r[1] > 0]
    gaps = sum(b - a > 30 for a, b in zip(hr_times, hr_times[1:]))
    out["quality"].update({"hr_sample_count": len(hr_times), "hr_gaps_over_30s": gaps})
    if gaps:
        warnings.append(warning("hr_gaps", f"La serie cardiaca ha {gaps} intervalli senza campioni per più di 30 secondi."))
    if not rows:
        warnings.append(warning("no_samples", "Il file contiene riepiloghi, senza una serie temporale visualizzabile.", "info"))
    distances = [finite(l.get("total_distance")) for l in laps]
    if laps and all(d is not None for d in distances) and distance is not None:
        delta = round(sum(distances) - distance, 1)
        out["quality"]["lap_distance_delta_m"] = delta
        if abs(delta) > max(10, distance * .01):
            warnings.append(warning("lap_distance_mismatch", "La somma delle distanze dei lap differisce dalla sessione."))
    return finish(out, filename, digest, source_format)


def message_groups(data, filename, digest, source_format):
    sessions = data.get("session", [])
    sessions = sessions if isinstance(sessions, list) else [sessions]
    if not sessions or not all(isinstance(s, dict) for s in sessions):
        raise ImportProblem("Nessuna sessione di allenamento trovata.")
    laps, records, lengths = (data.get(key, []) for key in ["lap", "record", "length"])
    events = data.get("event", [])
    if any(not isinstance(group, list) or any(not isinstance(v, dict) for v in group) for group in [laps, records, lengths, events]):
        raise ImportProblem("Messaggi lap, record o length non validi.")
    result = []
    for session in sessions:
        if len(sessions) == 1:
            own_laps, own_records, own_lengths, own_events = laps, records, lengths, events
        else:
            start = parse_time(session.get("start_time"))
            end = parse_time(session.get("timestamp"))
            elapsed = finite(session.get("total_elapsed_time"))
            if start and not end and elapsed is not None:
                end = start + timedelta(seconds=elapsed)
            if not start or not end or end <= start:
                raise ImportProblem("FIT multisport: intervalli delle sessioni incompleti; nessuna fusione automatica.")
            def in_session(message):
                timestamp = parse_time(first(message.get("start_time"), message.get("timestamp")))
                return timestamp is not None and start <= timestamp < end
            own_laps = [l for l in laps if in_session(l)]
            own_records = [r for r in records if in_session(r)]
            own_lengths = [l for l in lengths if in_session(l)]
            own_events = [event for event in events if in_session(event)]
        result.append(from_messages(session, own_laps, own_records, own_lengths, filename, digest, source_format, own_events))
    return result


def from_compact(source, filename, digest):
    if not isinstance(source.get("session"), dict) or not source.get("sport"):
        raise ImportProblem("Formato JSON non riconosciuto: atteso un allenamento con sport e session.")
    out = copy.deepcopy(source)
    # Avoid nesting an entire prior detailed export at every round trip.
    out.setdefault("source_data", copy.deepcopy({k: v for k, v in source.items() if k != "source_data"}))
    quality = out.get("quality") if isinstance(out.get("quality"), dict) else {}
    warnings = quality.get("warnings") if isinstance(quality.get("warnings"), list) else []
    if not out.get("metric_units", {}).get("session_pace"):
        warnings.append(warning("unknown_pace_unit", "L’unità del passo di sessione non è dichiarata dalla fonte; non viene indovinata."))
    if source.get("v") == 1 and not source.get("metric_quality") and not source.get("quality"):
        warnings.append(warning("legacy_zeros", "Export compatto precedente: alcuni zeri nei lap possono indicare misure mancanti."))
    if source.get("data_quality"):
        quality["source_data_quality"] = source["data_quality"]
        coverage = finite(source["data_quality"].get("hr_coverage_pct")) if isinstance(source["data_quality"], dict) else None
        if coverage is not None and coverage < 70:
            warnings.append(warning("partial_hr", "La fonte dichiara una copertura cardiaca parziale."))
    quality["warnings"] = warnings
    out["quality"] = quality
    return finish(out, filename, digest, "compact_json")


def parse_json_document(document, filename, digest):
    document = unbox(document)
    if isinstance(document, list):
        result = []
        for item in document:
            result.extend(parse_json_document(item, filename, digest))
        return result
    if not isinstance(document, dict):
        raise ImportProblem("Il JSON deve contenere un allenamento o una lista di allenamenti.")
    if isinstance(document.get("workouts"), list):
        return parse_json_document(document["workouts"], filename, digest)
    if isinstance(document.get("session_summary"), dict):
        return message_groups({"session": [document["session_summary"]], "lap": document.get("laps_summary", [])}, filename, digest, "healthkit_summary")
    if isinstance(document.get("session"), list) or (isinstance(document.get("session"), dict) and "start_time" in document["session"]):
        return message_groups(document, filename, digest, "healthkit_full")
    return [from_compact(document, filename, digest)]


def parse_upload(data: bytes, filename: str):
    digest = hashlib.sha256(data).hexdigest()
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix == ".fit":
        try:
            fit = fitparse.FitFile(io.BytesIO(data), check_crc=True)
            messages = {key: [m.get_values() for m in fit.get_messages(key)] for key in ["session", "lap", "record", "length", "event"]}
            return message_groups(messages, filename, digest, "fit")
        except ImportProblem:
            raise
        except Exception as error:
            raise ImportProblem("FIT non leggibile o controllo CRC fallito. Il file originale non viene modificato.") from error
    if suffix not in {".json", ".jsonl", ".ndjson"}:
        raise ImportProblem("Formato non supportato. Scegli file .fit, .json o .jsonl.")
    try:
        text = data.decode("utf-8-sig")
        document = [json.loads(line) for line in text.splitlines() if line.strip()] if suffix in {".jsonl", ".ndjson"} else json.loads(text)
        result = parse_json_document(document, filename, digest)
        if not result:
            raise ImportProblem("Il file non contiene allenamenti.")
        return result
    except ImportProblem:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise ImportProblem("JSON non valido o struttura non supportata. Nessun dato del file è stato importato.") from error
