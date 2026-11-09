"""Distance-based kilometre splits. Recorded laps are never divided into equal laps.

Distance boundaries come first: 1000, 2000, ... and the remaining distance.
Timing uses cumulative-distance samples when a reliable active-time timeline is
available. Otherwise source-lap duration/means can be apportioned, explicitly as
estimates; missing data is not reconstructed from the session average.
"""
from bisect import bisect_left, bisect_right
import copy
from datetime import datetime, timezone
import math

FIELDS = ["i", "fc_avg", "fc_max", "power", "cad", "pace", "stance_ms",
          "vosc_mm", "step_mm", "vr_pct", "asc_m", "desc_m", "dist_m", "dur_s"]
AVERAGES = ["fc_avg", "power", "cad", "stance_ms", "vosc_mm", "step_mm", "vr_pct"]


def valid(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def boundaries(total):
    if not valid(total) or total <= 0 or total > 10_000_000:
        return []
    full = int(total // 1000)
    result = [float(i * 1000) for i in range(full + 1)]
    if total - full * 1000 > 0.000001:
        result.append(float(total))
    return result


def timestamp(value):
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc)
    except (ValueError, AttributeError, TypeError):
        return None


def active_intervals(workout, elapsed):
    duration = workout["session"].get("dur_s")
    if not valid(elapsed) or not valid(duration):
        return None
    # FIT timer events are the only source used to locate pauses here.
    source = workout.get("source_data", {})
    events = source.get("event", []) if isinstance(source, dict) else []
    start = timestamp(workout.get("ts"))
    timer = []
    if start:
        for event in events:
            stamp = timestamp(event.get("timestamp"))
            if event.get("event") == "timer" and stamp:
                seconds = (stamp - start).total_seconds()
                if 0 <= seconds <= elapsed:
                    timer.append((seconds, event.get("event_type")))
    timer.sort(key=lambda item: item[0])
    if timer and timer[0][1] == "start" and timer[0][0] <= 1:
        intervals, opened = [], None
        for seconds, kind in timer:
            if kind == "start" and opened is None:
                opened = seconds
            elif kind in {"stop", "stop_all", "stop_disable", "stop_disable_all"} and opened is not None:
                intervals.append((opened, seconds))
                opened = None
        if opened is not None:
            intervals.append((opened, elapsed))
        if abs(sum(b - a for a, b in intervals) - duration) <= 1:
            return intervals
    if abs(elapsed - duration) <= .5:
        return [(0, elapsed)]
    return None


def overlap(start, end, intervals):
    return sum(max(0, min(end, b) - max(start, a)) for a, b in intervals)


def sample_rows(workout):
    samples = workout.get("samples", {})
    fields = samples.get("fields", [])
    if "t_s" not in fields or "distance_m" not in fields:
        return [], []
    time_index, distance_index = fields.index("t_s"), fields.index("distance_m")
    rows = [row for row in samples.get("rows", []) if valid(row[time_index]) and row[time_index] >= 0]
    rows.sort(key=lambda row: row[time_index])
    points = [(row[time_index], row[distance_index]) for row in rows if valid(row[distance_index]) and row[distance_index] >= 0]
    if len(points) < 2 or any(b[0] <= a[0] or b[1] < a[1] for a, b in zip(points, points[1:])):
        return [], []
    return rows, points


def from_samples(workout, cuts):
    rows, points = sample_rows(workout)
    if not points:
        return None
    sample = workout["samples"]
    fields = sample["fields"]
    ti = fields.index("t_s")
    times = [row[ti] for row in rows]
    distances = [point[1] for point in points]
    elapsed = workout["session"].get("elapsed_s")
    if not valid(elapsed):
        elapsed = workout.get("timing", {}).get("elapsed_s")
    active = active_intervals(workout, elapsed)

    def at_distance(target):
        if target == 0 and distances[0] == 0:
            return 0.0
        index = bisect_left(distances, target)
        if index == len(points):
            return None
        if distances[index] == target:
            # The final split includes any recorded stationary time at the end.
            if target == cuts[-1] and distances[-1] == target and valid(elapsed) and elapsed >= points[-1][0]:
                return elapsed
            return points[index][0]
        if index == 0:
            return None
        t0, d0 = points[index - 1]
        t1, d1 = points[index]
        return t0 + (target - d0) / (d1 - d0) * (t1 - t0)

    boundary_times = [at_distance(cut) for cut in cuts]
    result = []
    for start, end in zip(boundary_times, boundary_times[1:]):
        item = {"metrics": {}, "coverage": {}, "duration": None, "elapsed_s": None}
        result.append(item)
        if start is None or end is None or end < start:
            continue
        item["elapsed_s"] = end - start
        if active is None:
            continue
        seconds = overlap(start, end, active)
        if seconds <= 0:
            continue
        item["duration"] = seconds
        first = max(0, bisect_right(times, start) - 1)
        last = min(len(rows) - 1, bisect_left(times, end))
        for output, column in [("fc_avg", "fc"), ("power", "power_w"), ("cad", "cad")]:
            if column not in fields:
                continue
            ci = fields.index(column)
            weighted = covered = 0.0
            maximum = None
            for i in range(first, last):
                value = rows[i][ci]
                gap = times[i + 1] - times[i]
                if not valid(value) or value < 0 or (column in {"fc", "cad"} and value == 0) or not 0 < gap <= 30:
                    continue
                support = overlap(max(start, times[i]), min(end, times[i + 1]), active)
                if support > 0:
                    covered += support
                    weighted += value * support
                    maximum = value if maximum is None else max(maximum, value)
            coverage = min(100, covered / seconds * 100)
            item["coverage"][output] = round(coverage, 1)
            if covered > 0 and coverage >= 70:
                item["metrics"][output] = weighted / covered
                if output == "fc_avg":
                    item["metrics"]["fc_max"] = maximum
    return result


def from_laps(workout, cuts):
    fields = workout.get("lap_fields", [])
    rows = workout.get("laps", [])
    if "dist_m" not in fields:
        return None
    segments, position = [], 0.0
    for row in rows:
        data = dict(zip(fields, row))
        distance = data.get("dist_m")
        # An unknown lap distance prevents locating all later laps reliably.
        if not valid(distance) or distance < 0:
            return None
        if distance > 0:
            segments.append((position, position + distance, data))
            position += distance
    if not segments:
        return None
    result, index = [], 0
    for start, end in zip(cuts, cuts[1:]):
        while index < len(segments) and segments[index][1] <= start:
            index += 1
        parts, cursor = [], index
        while cursor < len(segments) and segments[cursor][0] < end:
            a, b, data = segments[cursor]
            distance = max(0, min(end, b) - max(start, a))
            if distance:
                duration = data.get("dur_s")
                allocated = duration * distance / (b - a) if valid(duration) and duration >= 0 else None
                parts.append((data, distance, allocated, abs(distance - (b - a)) < .000001))
            cursor += 1
        item = {"metrics": {}, "duration": None, "estimated": False}
        result.append(item)
        if abs(sum(part[1] for part in parts) - (end - start)) > .000001:
            continue
        item["estimated"] = any(not part[3] for part in parts)
        if all(part[2] is not None for part in parts):
            item["duration"] = sum(part[2] for part in parts)
        for metric in AVERAGES:
            if item["duration"] and all(valid(part[0].get(metric)) for part in parts):
                item["metrics"][metric] = sum(data[metric] * duration for data, _, duration, _ in parts) / item["duration"]
        # Maxima and elevation gains cannot be apportioned by distance.
        if parts and all(part[3] for part in parts):
            for metric in ["fc_max", "asc_m", "desc_m"]:
                if all(valid(part[0].get(metric)) for part in parts):
                    values = [part[0][metric] for part in parts]
                    item["metrics"][metric] = max(values) if metric == "fc_max" else sum(values)
    return result


def normalize_splits(workout):
    if workout.get("sport") not in {"running", "cycling", "walking", "hiking"}:
        return workout
    total = workout["session"].get("dist_m")
    distance_basis = "session_distance"
    if not valid(total):
        fields, rows = workout.get("lap_fields", []), workout.get("laps", [])
        if "dist_m" in fields and rows:
            source_distances = [row[fields.index("dist_m")] for row in rows]
            if all(valid(value) and value >= 0 for value in source_distances):
                total = sum(source_distances)
                distance_basis = "source_lap_distance_sum"
    cuts = boundaries(total)
    if not cuts:
        return workout
    fields, rows = workout.get("lap_fields", []), workout.get("laps", [])
    expected = [b - a for a, b in zip(cuts, cuts[1:])]
    # Preserve already exact source splits, including their richer metric quality.
    if "dist_m" in fields and len(rows) == len(expected):
        distances = [row[fields.index("dist_m")] for row in rows]
        if all(valid(a) and abs(a - b) < .000001 for a, b in zip(distances, expected)):
            workout.setdefault("split_policy", {"version": 1, "distance_m": 1000, "method": "source_kilometre_laps", "distance_basis": distance_basis})
            return workout
    source_laps = from_laps(workout, cuts)
    samples = from_samples(workout, cuts)
    qualities, normalized = [], []
    any_estimated = False
    for i, (start, end) in enumerate(zip(cuts, cuts[1:])):
        lap = source_laps[i] if source_laps else {"metrics": {}, "duration": None, "estimated": False}
        sample = samples[i] if samples else {"metrics": {}, "duration": None, "coverage": {}, "elapsed_s": None}
        metrics = dict(lap["metrics"])
        metrics.update(sample["metrics"])
        # Never turn a sparse observed metric into an apparently measured lap mean.
        for name, coverage in sample["coverage"].items():
            if coverage < 70:
                metrics.pop(name, None)
                if name == "fc_avg":
                    metrics.pop("fc_max", None)
        seconds = sample["duration"] if sample["duration"] is not None else lap["duration"]
        estimated = [name for name in lap["metrics"] if name not in sample["metrics"] and lap["estimated"] and name in metrics]
        if sample["duration"] is None and seconds is not None and lap["estimated"]:
            estimated.append("dur_s")
        if seconds is not None and seconds > 0:
            pace_seconds = int(math.floor(seconds * 1000 / (end - start) + .5))
            metrics["pace"] = f"{pace_seconds // 60}:{pace_seconds % 60:02d}"
            if "dur_s" in estimated:
                estimated.append("pace")
        else:
            metrics["pace"] = None
        metrics.update({"i": i + 1, "dist_m": round(end - start, 6), "dur_s": round(seconds, 6) if seconds is not None else None})
        normalized.append([round(metrics[field], 1) if valid(metrics.get(field)) and field not in {"i", "dist_m", "dur_s"} else metrics.get(field) for field in FIELDS])
        qualities.append({"i": i + 1, "start_m": start, "end_m": end,
                          "duration_basis": "distance_samples_active_time" if sample["duration"] is not None else "source_lap_summary" if seconds is not None else "unavailable",
                          "elapsed_s": round(sample["elapsed_s"], 3) if sample["elapsed_s"] is not None else None,
                          "estimated_metrics": estimated, "sample_coverage_pct": sample["coverage"]})
        any_estimated |= bool(estimated)
    original = {"lap_fields": copy.deepcopy(fields), "laps": copy.deepcopy(rows)}
    for key in ["lap_metric_quality", "segment_analysis", "segment_analysis_fields", "segment_analysis_method"]:
        if key in workout:
            original[key] = workout.pop(key)
    source = workout.setdefault("source_data", {})
    if not isinstance(source, dict):
        source = workout["source_data"] = {"original": source}
    source["original_lap_summary"] = original
    workout["lap_fields"], workout["laps"] = list(FIELDS), normalized
    workout["split_quality"] = qualities
    workout["split_policy"] = {"version": 1, "distance_m": 1000, "method": "cumulative_distance_boundaries", "distance_basis": distance_basis,
                                "boundary_timing": "linear_interpolation_between_distance_samples",
                                "summary_estimate_assumption": "constant_mean_within_each_original_lap",
                                "minimum_sample_metric_coverage_pct": 70}
    quality = workout.setdefault("quality", {})
    quality["segmentation_source"] = "distance_1km"
    if any_estimated:
        quality.setdefault("warnings", []).append({"code": "estimated_splits", "level": "info",
            "message": "Split da 1.000 m. Alcune metriche sono stimate dai lap originali; il metodo è indicato nell’export."})
    if any(row[-1] is None for row in normalized):
        quality.setdefault("warnings", []).append({"code": "missing_split_times", "level": "info",
            "message": "Tempi di alcuni split non disponibili: mancano campioni o durate dei lap sufficienti."})
    duration = workout["session"].get("dur_s")
    if valid(duration) and all(row[-1] is not None for row in normalized):
        delta = round(sum(row[-1] for row in normalized) - duration, 3)
        quality["split_duration_delta_s"] = delta
        if abs(delta) > 1:
            quality.setdefault("warnings", []).append({"code": "split_duration_mismatch", "level": "warning",
                "message": "La durata ricostruita degli split differisce dalla sessione; i tempi non sono stati riscalati."})
    data_quality = workout.get("data_quality")
    if isinstance(data_quality, dict):
        original["data_quality"] = copy.deepcopy(data_quality)
        data_quality["lap_bounds_source"] = "distance_1km"
        data_quality["lap_distance_sum_m"] = total
        data_quality["lap_distance_delta_m"] = 0 if distance_basis == "session_distance" else None
        data_quality["lap_duration_sum_s"] = sum(row[-1] for row in normalized) if all(row[-1] is not None for row in normalized) else None
        data_quality["lap_duration_delta_s"] = abs(quality["split_duration_delta_s"]) if "split_duration_delta_s" in quality else None
    return workout
