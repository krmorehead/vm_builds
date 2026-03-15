#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f .venv/bin/activate ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

echo "Installing Python dependencies..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

echo "Installing Ansible Galaxy dependencies..."
ansible-galaxy install -r requirements.yml --force

if ! command -v sshpass &>/dev/null; then
    echo ""
    echo "WARNING: sshpass is not installed."
    echo "  Install it with: sudo apt install sshpass"
fi

if [ ! -f .env ]; then
    echo ""
    echo "WARNING: No .env file found."
    echo "  Copy test.env to .env and fill in real values:"
    echo "  cp test.env .env"
fi

echo ""
echo "Setup complete. Activate the environment with:"
echo "  source .venv/bin/activate"
