"""Loopback-only API. Imported content is held in memory until the process ends."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .demo import demo_workouts
from .exports import build_export
from .importers import ImportProblem, parse_upload

STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_SESSION_BYTES = 160 * 1024 * 1024
MAX_WORKOUTS = 5000


class WorkoutStore:
    def __init__(self):
        self.items = {}
        self.sizes = {}
        self.lock = threading.Lock()

    def add(self, workouts):
        with self.lock:
            additions = {w["id"]: w for w in workouts if w["id"] not in self.items}
            sizes = {key: len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) for key, value in additions.items()}
            if len(self.items) + len(additions) > MAX_WORKOUTS or sum(self.sizes.values()) + sum(sizes.values()) > MAX_SESSION_BYTES:
                raise ImportProblem("Sessione troppo grande. Esporta i dati e avvia una nuova sessione prima di continuare.")
            self.items.update(additions)
            self.sizes.update(sizes)
            return {"added": list(additions), "duplicates": len(workouts) - len(additions)}


def listing(workout):
    return {key: workout.get(key) for key in ["id", "sport", "ts", "session", "quality", "provenance", "metric_units"]} | {
        "lap_count": len(workout.get("laps", workout.get("swim_sets", []))),
        "sample_count": len(workout.get("samples", {}).get("rows", []))}


def create_app():
    app = FastAPI(title="FIT Studio", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = WorkoutStore()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        if request.method not in {"GET", "HEAD"}:
            origin = request.headers.get("origin")
            if origin and (urlparse(origin).netloc != request.headers.get("host") or urlparse(origin).scheme != "http"):
                return JSONResponse({"detail": "Richiesta da un’origine diversa non consentita."}, status_code=403)
            if request.headers.get("x-fit-studio") != "1":
                return JSONResponse({"detail": "Richiesta locale non valida."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    @app.get("/")
    def home():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    def status():
        return {"name": "FIT Studio", "version": "0.1.0", "storage": "memory", "network": "loopback"}

    @app.get("/api/workouts")
    def workouts():
        store = app.state.store
        with store.lock:
            return {"workouts": [listing(w) for w in sorted(store.items.values(), key=lambda w: w["ts"], reverse=True)]}

    @app.get("/api/workouts/{workout_id}")
    def workout(workout_id: str):
        with app.state.store.lock:
            item = app.state.store.items.get(workout_id)
            if item is None:
                raise HTTPException(404, "Allenamento non trovato.")
            # Full decoded sources are available only through the explicit detailed export.
            return {k: v for k, v in item.items() if k != "source_data"}

    @app.post("/api/import")
    async def import_file(request: Request, filename: str):
        if not filename or len(filename) > 1024:
            raise HTTPException(400, "Nome del file non valido.")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_FILE_BYTES:
                raise HTTPException(413, "File troppo grande: il limite è 32 MB per file.")
        if not data:
            raise HTTPException(400, "Il file è vuoto.")
        # Parsing is CPU-bound; leave the event loop responsive during a FIT import.
        from starlette.concurrency import run_in_threadpool
        try:
            parsed = await run_in_threadpool(parse_upload, bytes(data), filename)
            return app.state.store.add(parsed)
        except ImportProblem as error:
            raise HTTPException(422, str(error)) from None

    @app.post("/api/demo")
    def demo():
        return app.state.store.add(demo_workouts())

    @app.delete("/api/workouts/{workout_id}")
    def remove(workout_id: str):
        store = app.state.store
        with store.lock:
            if workout_id not in store.items:
                raise HTTPException(404, "Allenamento non trovato.")
            del store.items[workout_id]
            store.sizes.pop(workout_id, None)
        return {"removed": True}

    async def prepare_export(request):
        try:
            options = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "Opzioni di export non valide.") from None
        if not isinstance(options, dict):
            raise HTTPException(400, "Opzioni di export non valide.")
        ids = options.get("ids", [])
        if not isinstance(ids, list) or not ids or len(ids) > MAX_WORKOUTS or any(not isinstance(i, str) for i in ids):
            raise HTTPException(400, "Seleziona almeno un allenamento.")
        ids = list(dict.fromkeys(ids))
        with app.state.store.lock:
            if any(i not in app.state.store.items for i in ids):
                raise HTTPException(404, "Uno degli allenamenti selezionati non è più disponibile.")
            items = [app.state.store.items[i] for i in ids]
        for key in ["include_gps", "include_identifiers"]:
            if key in options and not isinstance(options[key], bool):
                raise HTTPException(400, "Opzioni di privacy non valide.")
        try:
            content = build_export(items, profile=options.get("profile", "compact"), format=options.get("format", "json"),
                include_gps=options.get("include_gps", False), include_identifiers=options.get("include_identifiers", False))
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        return content, options, len(items)

    @app.post("/api/export/preview")
    async def export_preview(request: Request):
        content, options, count = await prepare_export(request)
        if options.get("format", "json") == "json":
            display = json.dumps(json.loads(content), ensure_ascii=False, indent=2)
        else:
            display = content.decode()
        return {"bytes": len(content), "count": count, "preview": display[:14000], "truncated": len(display) > 14000}

    @app.post("/api/export")
    async def export(request: Request):
        content, options, _ = await prepare_export(request)
        format = options.get("format", "json")
        profile = options.get("profile", "compact")
        return Response(content, media_type="application/x-ndjson" if format == "jsonl" else "application/json",
                        headers={"Content-Disposition": f'attachment; filename="fit-studio-{profile}.{format}"'})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
