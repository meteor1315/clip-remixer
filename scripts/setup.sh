#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v ffmpeg >/dev/null || { echo 'Installer FFmpeg : brew install ffmpeg'; exit 1; }
command -v ffprobe >/dev/null || { echo 'ffprobe manque : réinstaller FFmpeg'; exit 1; }
PYTHON="${PYTHON:-python3}"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ requis (brew install python@3.12)"'
"$PYTHON" -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
echo 'Installation terminée. Lancer : bash scripts/run.sh'
