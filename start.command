#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if ! python3 -c 'import sys; assert sys.version_info >= (3, 10)' 2>/dev/null; then
  echo "Serve Python 3.10 o successivo. Installa Python e riapri questo file."
  read -r -p "Premi Invio per chiudere. "
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import fastapi, uvicorn, fitparse' 2>/dev/null; then
  echo "Preparazione di FIT Studio: scarico le dipendenze, solo al primo avvio."
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python run.py "$@"
