"""Minimal NiceGUI server for the kiosk Home Hub.

Runs inside the kiosk LXC container, serving only the /hub page.
Reads service URLs from /opt/kiosk/config.json.

Usage:
    python3 /opt/kiosk/webui/kiosk_server.py [--port 8080]
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

from nicegui import ui

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR.parent.parent) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR.parent.parent))

from scripts.webui.data import load_kiosk_config  # noqa: E402


def create_app(config_path: Path | None = None) -> None:
    """Register the hub page with pre-loaded URLs."""
    urls = load_kiosk_config(config_path)

    from scripts.webui import theme
    from scripts.webui.pages.hub import render_hub

    @ui.page("/")
    @ui.page("/hub")
    def hub_page() -> None:
        theme.apply_theme()
        ui.add_head_html(theme.HOVER_CARD_STYLES)
        render_hub(urls=urls)


def main() -> None:
    parser = argparse.ArgumentParser(description="Kiosk Home Hub server")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--config", type=str, default=None)
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else None
    create_app(config_path=config_path)

    storage_secret = os.environ.get("WEBUI_STORAGE_SECRET") or secrets.token_hex(32)
    ui.run(
        title="Home Hub",
        host="127.0.0.1",
        port=args.port,
        dark=True,
        reload=False,
        show=False,
        storage_secret=storage_secret,
    )


if __name__ == "__main__":
    main()
