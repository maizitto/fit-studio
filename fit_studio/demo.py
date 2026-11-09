"""Synthetic examples. No personal workouts are distributed with the app."""
import math

from .importers import LAP_FIELDS, SWIM_FIELDS, SAMPLE_FIELDS, finish


def demo_workouts():
    specs = [
        ("running", "2026-11-08T08:15:00Z", 10180, 3240, 146, 256, 10),
        ("swimming", "2026-11-07T17:30:00Z", 1800, 2460, 132, None, 6),
        ("cycling", "2026-11-06T07:40:00Z", 42600, 5820, 139, 184, 8),
        ("running", "2026-11-04T11:05:00Z", 6420, 2220, 137, 235, 6),
        ("running", "2026-11-02T08:00:00Z", 14500, 4920, 143, 248, 14),
        ("swimming", "2026-10-31T16:20:00Z", 1500, 2100, None, None, 5),
    ]
    result = []
    for n, (sport, ts, dist, duration, hr, power, count) in enumerate(specs):
        swim = sport == "swimming"
        pace_s = duration / dist * (100 if swim else 1000)
        seconds = round(pace_s)
        item = {"sport": sport, "ts": ts, "session": {"dist_m": dist, "dur_s": duration,
            "elapsed_s": duration + (300 if swim else 0), "fc_avg": hr, "fc_max": hr + 22 if hr else None,
            "power_avg": power, "cal": round(duration / 60 * 10), "asc_m": None if swim else 75 + n * 12,
            "pace_avg": f"{seconds // 60}:{seconds % 60:02}", "pace_s_km": round(duration / dist * 1000, 1)},
            "metric_units": {"session_pace": "min/100m" if swim else "min/km", "cadence": "rpm" if sport == "cycling" else "steps/min"},
            "quality": {"warnings": [], "synthetic": True, "segmentation_source": "synthetic_example"}}
        if n == 3:
            item["quality"]["warnings"].append({"code": "partial_hr", "level": "warning", "message": "Esempio di copertura cardiaca parziale: 64% del tempo attivo."})
            item["data_quality"] = {"hr_coverage_pct": 64}
        if swim:
            item["swim_pool_m"] = 25
            item["swim_set_fields"] = list(SWIM_FIELDS)
            item["swim_sets"] = [[i + 1, dist / count, round(dist / count / 25), "freestyle", round(duration / count),
                item["session"]["pace_avg"], hr + i if hr else None, 112, 50, 50, 30 if i < count - 1 else None] for i in range(count)]
        else:
            distances = [1000] * (dist // 1000)
            if dist % 1000:
                distances.append(dist % 1000)
            item["lap_fields"] = list(LAP_FIELDS)
            item["laps"] = [[i + 1, hr + round(math.sin(i) * 5), hr + 15, power + round(math.cos(i) * 12),
                86 if sport == "cycling" else 170 + i % 4, item["session"]["pace_avg"],
                260 if sport == "running" else None, 83 if sport == "running" else None,
                1120 if sport == "running" else None, 7.4 if sport == "running" else None,
                8, 6, lap_distance, duration * lap_distance / dist] for i, lap_distance in enumerate(distances)]
        item["samples"] = {"fields": list(SAMPLE_FIELDS), "rows": []}
        for i in range(121):
            fraction = i / 120
            observed_hr = round(hr - 16 * math.exp(-fraction * 14) + math.sin(i * .23) * 4 + fraction * 5) if hr else None
            if n == 3 and 40 < i < 85:
                observed_hr = None
            item["samples"]["rows"].append([round(duration * fraction), observed_hr,
                round(power + math.sin(i * .32) * 24) if power else None,
                round(dist / duration * (1 + math.sin(i * .18) * .06), 3),
                86 if sport == "cycling" else (171 if sport == "running" else None),
                round(dist * fraction, 1), None if swim else round(110 + math.sin(i * .04) * 12, 1), None, None])
        result.append(finish(item, "esempio-sintetico.json", f"demo-{n}", "demo"))
    return result
