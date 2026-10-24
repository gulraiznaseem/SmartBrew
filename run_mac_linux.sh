#!/usr/bin/env bash
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
[ -d .venv ] || "$PY" -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
( sleep 2; (command -v open >/dev/null && open http://127.0.0.1:5001) || (command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:5001) ) >/dev/null 2>&1 &
python run.py --reset
