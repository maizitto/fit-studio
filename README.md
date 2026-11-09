# FIT Studio

A local dashboard for importing workout files, inspecting the data, and exporting structured JSON for AI analysis.

For about a year, I have been using my own export workflow to turn my workouts into structured data and have AI analyze my training. I decided to connect that workflow to a simple dashboard, so people who do not want to work with scripts can use it too.

The dashboard is new; it builds on that existing export workflow. Import your files, check the sessions and splits, then take the exported data to the AI assistant you already use.

<img src="preview.jpg?raw=true" alt="FIT Studio dashboard" width="1200">

*The screenshot uses synthetic example workouts. The current interface is in Italian.*

## Getting started

You need **Python 3.10 or later**. You can install it from [python.org](https://www.python.org/downloads/).

### macOS

1. Download this repository using **Code → Download ZIP**, then extract it.
2. Double-click **`start.command`**. The first launch creates a virtual environment and installs the dependencies.
3. Your browser opens at [http://127.0.0.1:8765](http://127.0.0.1:8765).

The terminal stays open while the app is running. Press **Ctrl+C** to stop it.

### Manual setup

macOS or Linux:

```bash
git clone https://github.com/maizitto/fit-studio.git
cd fit-studio
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

Windows, from the extracted project folder:

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python run.py
```

There is no JavaScript build step. To use another port, add `--port 8766`. Add `--no-browser` to start the server without opening a browser tab.

## Using the dashboard

- **Importa file**: import one or more FIT, JSON, or JSONL files. **Scegli cartella** imports a folder. Drag and drop also works.
- Filter sessions by sport, date, or text. The overview shows distance per session and active time per sport. Open a session for heart rate, pace/speed, power, splits, and data-quality notes.
- Select sessions and open **Esporta**. Choose a compact or detailed export, preview its contents, and download JSON or JSONL.
- **Carica esempio** loads six synthetic workouts so you can try the app without your own files.

## Supported data

- FIT activity files, with CRC validation. Multisport files are separated when their session boundaries can be identified.
- Compact HealthKitExporter JSON and `fit-recap` archives containing a `workouts` array.
- Earlier HealthKitExporter JSON with `session_summary` / `laps_summary`, or `session` / `lap` / `record`, including BotField values.
- FIT Studio archives and JSONL/NDJSON files with one workout per line.

Duplicate detection uses sport, start time, distance, and active duration. Similar workouts are not automatically merged. A malformed file does not stop the other files in a batch; each individual file is imported atomically.

Limits: **32 MB per file**, **5,000 sessions**, and approximately **160 MB of decoded JSON per running server**.

## Structured exports

| Profile | Contents |
| --- | --- |
| **Compact for AI** | Session summaries, splits or swim laps, units, provenance, and quality information. |
| **Detailed** | Adds available sample series and decoded source data. For FIT, this includes session, lap, record, length, and timer-event messages; it is not a replacement for the original FIT file. |

JSON uses an archive with a `workouts` array. JSONL contains one workout per line. The schemas are identified as `fit-studio/archive/1` and `fit-studio/workout/1`.

Known identifiers and GPS coordinates are excluded by default. You can include them explicitly. This filter does not anonymize the remaining workout dates, measurements, or unrecognized text fields.

### Splits and missing data

- Running, cycling, walking, and hiking use **1,000 m splits plus the final remainder**. For example, 6,420 m becomes six 1,000 m splits and one 420 m split.
- Split timing uses cumulative-distance boundaries, interpolated between samples. Coherent FIT timer events exclude pauses. When only source-lap summaries are usable, timing and averages can be apportioned across the overlapping distance; these estimates are identified in `split_quality`.
- Session averages alone never generate split times. Maxima and elevation gains are not apportioned proportionally. Original lap summaries remain available in the detailed export.
- Swimming retains the source's laps or sets. Missing pool length and unknown recovery times stay unknown.
- Missing measurements remain `null`. Older compact files may already use zero for missing values; the importer flags that ambiguity.
- Units and source limitations travel with the data. The app does not invent heart-rate zones, readiness scores, or physiological drift metrics.

## Local storage

The first launch downloads Python dependencies. During use, files are processed on your computer; the dashboard has no telemetry or external AI calls. The server listens only on `127.0.0.1`.

**The library is held in memory.** Refreshing the page keeps it, but stopping or restarting the server clears it. Export and reimport an archive to keep your work. Imported source files are never modified.

This public app runs independently of the original iOS app and private coaching setup.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

The tests use synthetic data, including a CRC-valid binary FIT fixture. They cover imports, kilometre boundaries, pauses, missing measurements, multisport separation, duplicates, export/reimport, identifier filtering, and API access checks.
