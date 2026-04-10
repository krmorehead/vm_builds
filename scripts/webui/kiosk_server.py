"""Kiosk server — per-host manager and Home Hub UI.

Runs inside the kiosk LXC container on every Proxmox host. Serves the
Home Hub dashboard and infrastructure detail pages (bridge, mesh, router).
Also acts as the host's local manager: collects metrics from sibling
containers via SSH, accepts heartbeat subscriptions, and handles batman
trigger API calls.

Reads service URLs, node IPs, and MESH_KEY from /opt/kiosk/config.json.

Usage:
    python3 /opt/kiosk/webui/kiosk_server.py [--port 9001]
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from collections.abc import Callable
from pathlib import Path

from nicegui import app, ui

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.webui import manager  # noqa: E402
from scripts.webui.data import load_kiosk_config  # noqa: E402


def _build_node_resolver(config: dict) -> Callable[[str], str | None]:
    """Build a node IP resolver from config.json NODE_IPS."""
    node_ips = config.get("NODE_IPS", {})

    def resolver(node_id: str) -> str | None:
        return node_ips.get(node_id)

    return resolver


_kiosk_config: dict = {}


def create_app(config_path: Path | None = None) -> None:
    """Register pages, init manager, mount API endpoints.

    NodeManager by default (per-host scope). Set IS_CLUSTER_MANAGER=true
    in config.json to get ClusterManager (subnet-scoped fleet view).
    """
    global _kiosk_config
    urls = load_kiosk_config(config_path)
    _kiosk_config = urls

    is_cluster = str(urls.get("IS_CLUSTER_MANAGER", "")).lower() in ("true", "1", "yes")
    mgr_class = manager.ClusterManager if is_cluster else manager.NodeManager

    manager.init(
        _build_node_resolver(urls),
        config=urls,
        manager_class=mgr_class,
    )
    manager.register_api(app)

    from scripts.webui import theme
    from scripts.webui.pages.hub import render_hub

    @ui.page("/")
    @ui.page("/hub")
    def hub_page() -> None:
        theme.apply_theme()
        ui.add_head_html(theme.HOVER_CARD_STYLES)
        render_hub(urls=urls)

    if is_cluster:
        from scripts.webui.pages import cluster_dashboard
        cluster_dashboard.register()

    from scripts.webui.pages.bridge import _bridge_content
    from scripts.webui.pages.containers import _render_containers
    from scripts.webui.pages.mesh import _mesh_content
    from scripts.webui.pages.router import _router_content

    @ui.page("/bridge")
    def kiosk_bridge() -> None:
        with theme.kiosk_page_shell("bridge"):
            ui.add_head_html(theme.HOVER_CARD_STYLES)
            _bridge_content()

    @ui.page("/mesh")
    def kiosk_mesh() -> None:
        with theme.kiosk_page_shell("mesh"):
            ui.add_head_html(theme.HOVER_CARD_STYLES)
            _mesh_content()

    @ui.page("/router")
    def kiosk_router() -> None:
        with theme.kiosk_page_shell("router"):
            ui.add_head_html(theme.HOVER_CARD_STYLES)
            _router_content()

    @ui.page("/containers")
    async def kiosk_containers() -> None:
        from scripts.webui.data import Fleet
        with theme.kiosk_page_shell("containers"):
            ui.add_head_html(theme.HOVER_CARD_STYLES)
            await _render_containers(Fleet([]))

    from scripts.webui.pages import launch, viewer
    launch.register()
    viewer.register()


def main() -> None:
    parser = argparse.ArgumentParser(description="Kiosk Home Hub server")
    parser.add_argument("--port", type=int, default=9001)
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument(
        "--host", default="0.0.0.0",
        help="Bind address (default: 0.0.0.0 for API access from other hosts)",
    )
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else None
    create_app(config_path=config_path)

    def _on_startup() -> None:
        manager.start_poller()

    app.on_startup(_on_startup)

    storage_secret = os.environ.get("WEBUI_STORAGE_SECRET") or secrets.token_hex(32)
    ui.run(
        title="Home Hub",
        host=args.host,
        port=args.port,
        dark=True,
        reload=False,
        show=False,
        storage_secret=storage_secret,
    )


if __name__ == "__main__":
    main()
