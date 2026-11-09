#!/usr/bin/env python3
"""Run FIT Studio locally; no frontend compilation or hosted services required."""
import argparse
import sys
import threading
import time
import urllib.request
import webbrowser


def main():
    parser = argparse.ArgumentParser(description="FIT Studio — importa, controlla ed esporta i tuoi allenamenti.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Scegli una porta tra 1024 e 65535.")
    try:
        import uvicorn
        from fit_studio.app import app
    except ImportError:
        print("Dipendenze mancanti. Avvia start.command oppure esegui: python3 -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{args.port}"
    print(f"\n  FIT Studio → {url}\n  I dati restano in memoria su questo computer. Ctrl+C per terminare.\n", flush=True)
    if not args.no_browser:
        def open_browser():
            for _ in range(40):
                try:
                    with urllib.request.urlopen(url + "/api/status", timeout=.3):
                        webbrowser.open(url)
                        return
                except OSError:
                    time.sleep(.2)
        threading.Thread(target=open_browser, daemon=True).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", proxy_headers=False)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
