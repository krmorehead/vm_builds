#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -d .venv ]]; then
    echo "ERROR: .venv not found. Run ./scripts/setup.sh first." >&2
    exit 1
fi
source .venv/bin/activate

if [[ -f .env ]]; then
    set -a; source .env; set +a
elif [[ -f test.env ]]; then
    set -a; source test.env; set +a
else
    echo "ERROR: No .env or test.env found. Copy test.env to .env and configure." >&2
    exit 1
fi

python3 build.py "$@"
