#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -d .venv ]]; then
    echo "ERROR: .venv not found. Run ./scripts/setup.sh first." >&2
    exit 1
fi
source .venv/bin/activate

python scripts/webui/app.py "$@"
