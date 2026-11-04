#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -x .venv/bin/python ]] || { echo 'Exécuter : bash scripts/setup.sh'; exit 1; }
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
exec .venv/bin/python -m streamlit run app.py --server.address 127.0.0.1
