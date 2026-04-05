"""NiceGUI web application for vm_builds project management.

Single entry point that registers all pages and runs the server.
When imported by the NiceGUI test plugin, pages are registered at module level.
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from nicegui import app, ui

from scripts.webui import data
from scripts.webui.data import PROJECT_ROOT
from scripts.webui.pages import (
    dashboard, deploy, environment, hosts, hub, images, nodes, services,
)


def _resolve_env_path() -> Path:
    env = PROJECT_ROOT / ".env"
    if env.exists():
        return env
    test_env = PROJECT_ROOT / "test.env"
    if test_env.exists():
        return test_env
    return env


def configure(
    env_path: Path | None = None,
    images_dir: Path | None = None,
    state_dir: Path | None = None,
) -> None:
    """Set global paths used by all pages."""
    resolved_env = env_path or _resolve_env_path()
    resolved_images = images_dir or (PROJECT_ROOT / "images")
    resolved_state = state_dir or (PROJECT_ROOT / ".state")

    app.storage.general["env_path"] = str(resolved_env)
    app.storage.general["images_dir"] = str(resolved_images)
    app.storage.general["state_dir"] = str(resolved_state)
    app.storage.general.setdefault("selected_tags", [])


def get_env_path() -> Path:
    raw = app.storage.general.get("env_path", "")
    if not raw:
        return Path()
    return Path(raw)


def get_images_dir() -> Path:
    return Path(app.storage.general.get("images_dir", ""))


def get_state_dir() -> Path:
    return Path(app.storage.general.get("state_dir", ""))


def load_active_env() -> dict[str, str]:
    """Load the active env file and return its values (empty dict if missing)."""
    raw = app.storage.general.get("env_path", "")
    if raw:
        env_path = Path(raw)
        if env_path.is_file():
            return data.load_environment(env_path).values
    return {}


def register_pages() -> None:
    """Register all @ui.page routes."""
    dashboard.register()
    environment.register()
    hosts.register()
    nodes.register()
    services.register()
    deploy.register()
    images.register()
    hub.register()


def _get_callhome_private_key() -> str:
    """Load the call-home private key from the active env file."""
    raw = app.storage.general.get("env_path", "")
    if raw:
        env_path = Path(raw)
        if env_path.is_file():
            env = data.load_environment(env_path).values
            return env.get("CALLHOME_PRIVATE_KEY", "")
    return ""


def register_api() -> None:
    """Register FastAPI REST endpoints for the call-home system.

    Uses Starlette route handlers directly because NiceGUI's decorator
    wrappers interfere with FastAPI's Pydantic body parsing.
    """
    import json as _json
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def _api_checkin(request: StarletteRequest) -> JSONResponse:
        state_dir = get_state_dir()

        token = request.headers.get("x-callhome-token", "")
        private_key = _get_callhome_private_key()
        if private_key:
            if not data.validate_callhome_token(token, private_key):
                return JSONResponse({"error": "unauthorized"}, status_code=403)
        else:
            logging.getLogger("vm_builds.callhome").warning(
                "CALLHOME_PRIVATE_KEY not set — check-in accepted without authentication"
            )

        try:
            body = await request.json()
            checkin = data.NodeCheckin(
                node_id=body["node_id"],
                hostname=body["hostname"],
                local_ips=body.get("local_ips", []),
                uptime_seconds=body.get("uptime_seconds", 0),
                services=body.get("services", []),
                disk_usage_pct=body.get("disk_usage_pct", 0),
                memory_usage_pct=body.get("memory_usage_pct", 0),
                version=body.get("version", ""),
            )
        except (KeyError, TypeError, _json.JSONDecodeError) as exc:
            return JSONResponse(
                {"error": f"Invalid payload: {exc}"},
                status_code=400,
            )
        remote_ip = (
            checkin.local_ips[0] if checkin.local_ips
            else (request.client.host if request.client else "unknown")
        )
        node = data.register_checkin(state_dir, checkin, remote_ip)
        return JSONResponse({
            "status": "ok",
            "node_id": node.node_id,
            "last_seen": node.last_seen,
        })

    async def _api_nodes(request: StarletteRequest) -> JSONResponse:
        private_key = _get_callhome_private_key()
        if private_key:
            token = request.headers.get("x-callhome-token", "")
            if not data.validate_callhome_token(token, private_key):
                return JSONResponse({"error": "unauthorized"}, status_code=403)

        state_dir = get_state_dir()
        nodes_list = data.load_node_registry(state_dir)
        return JSONResponse([
            {
                "node_id": n.node_id,
                "hostname": n.hostname,
                "last_ip": n.last_ip,
                "status": n.status,
                "last_seen": n.last_seen,
                "uptime_seconds": n.uptime_seconds,
                "services": n.services,
                "disk_usage_pct": n.disk_usage_pct,
                "memory_usage_pct": n.memory_usage_pct,
                "version": n.version,
            }
            for n in nodes_list
        ])

    app.routes.insert(0, Route("/api/checkin", _api_checkin, methods=["POST"]))
    app.routes.insert(0, Route("/api/nodes", _api_nodes, methods=["GET"]))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="vm_builds web UI")
    parser.add_argument(
        "--env",
        default=None,
        help="Path to env file (default: .env, fallback test.env)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to serve on (default: 8080)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    env_path = Path(args.env) if args.env else None
    configure(env_path=env_path)
    register_pages()
    register_api()
    storage_secret = os.environ.get("WEBUI_STORAGE_SECRET") or secrets.token_hex(32)
    ui.run(
        title="vm_builds",
        host=args.host,
        port=args.port,
        dark=True,
        reload=False,
        show=True,
        storage_secret=storage_secret,
    )


if __name__ == "__main__":
    main()
