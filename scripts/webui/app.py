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

from scripts.webui import data, manager
from scripts.webui.data import PROJECT_ROOT
from scripts.webui.pages import (
    bridge, containers, dashboard, deploy, environment, hosts, hub, images,
    launch, mesh, nodes, router, services, timeline, viewer,
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
    bridge.register()
    containers.register()
    mesh.register()
    router.register()
    launch.register()
    timeline.register()
    viewer.register()


# ── Manager integration ──────────────────────────────────────────────


def _env_node_resolver(node_id: str) -> str | None:
    """Resolve a node_id to an IP via the active .env / test.env file."""
    env = load_active_env()
    known = data.get_known_hosts(env)
    for h in known:
        if h.name == node_id:
            return h.ip
    return None


def _validate_callhome_token(token: str) -> bool:
    """Auth validator for manager mutation endpoints."""
    private_key = _get_callhome_private_key()
    if not private_key:
        return True
    return data.validate_callhome_token(token, private_key)


def _init_manager() -> None:
    """Initialize the shared manager with an env-based node resolver.

    Safe to call multiple times (e.g., from main() and register_api() in tests).
    """
    manager.init(_env_node_resolver, auth_validator=_validate_callhome_token)


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
    """Register REST endpoints (call-home + heartbeat via manager).

    Ensures manager is initialized so heartbeat routes work even when
    called from tests that bypass main().
    """
    _init_manager()

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
            ch_raw = body.get("container_health")
            ch = None
            if ch_raw and isinstance(ch_raw, dict):
                ch = data.ContainerHealth(
                    container_id=ch_raw.get("container_id", ""),
                    systemd_services=ch_raw.get("systemd_services", {}),
                    listening_ports=ch_raw.get("listening_ports", []),
                    ready=ch_raw.get("ready", False),
                    extensions=ch_raw.get("extensions", {}),
                )
            checkin = data.NodeCheckin(
                node_id=body["node_id"],
                hostname=body["hostname"],
                local_ips=body.get("local_ips", []),
                uptime_seconds=body.get("uptime_seconds", 0),
                services=body.get("services", []),
                disk_usage_pct=body.get("disk_usage_pct", 0),
                memory_usage_pct=body.get("memory_usage_pct", 0),
                version=body.get("version", ""),
                container_health=ch,
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
        result = []
        for n in nodes_list:
            entry: dict = {
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
            if n.container_health:
                entry["container_health"] = {
                    "container_id": n.container_health.container_id,
                    "ready": n.container_health.ready,
                    "systemd_services": n.container_health.systemd_services,
                    "listening_ports": n.container_health.listening_ports,
                    "extensions": n.container_health.extensions,
                }
            result.append(entry)
        return JSONResponse(result)

    async def _api_fleet_ready(request: StarletteRequest) -> JSONResponse:
        services_param = request.query_params.get("services", "")
        if not services_param:
            return JSONResponse(
                {"error": "Missing 'services' query parameter"},
                status_code=400,
            )
        expected = [s.strip() for s in services_param.split(",") if s.strip()]
        state_dir = get_state_dir()
        result = data.check_fleet_readiness(state_dir, expected)
        return JSONResponse(result)

    async def _api_fleet_stale(request: StarletteRequest) -> JSONResponse:
        """Circuit breaker: detect services whose heartbeat went stale."""
        services_param = request.query_params.get("services", "")
        if not services_param:
            return JSONResponse(
                {"error": "Missing 'services' query parameter"},
                status_code=400,
            )
        expected = [s.strip() for s in services_param.split(",") if s.strip()]
        try:
            max_age = int(request.query_params.get("max_age_seconds", "120"))
        except (ValueError, TypeError):
            return JSONResponse(
                {"error": "max_age_seconds must be an integer"},
                status_code=400,
            )
        state_dir = get_state_dir()
        result = data.check_fleet_staleness(state_dir, expected, max_age)
        status_code = 409 if result["has_stale"] else 200
        return JSONResponse(result, status_code=status_code)

    async def _api_fleet_health(request: StarletteRequest) -> JSONResponse:
        state_dir = get_state_dir()
        nodes_list = data.load_node_registry(state_dir)
        health = data.compute_fleet_health(nodes_list)
        return JSONResponse({
            "total_nodes": health.total_nodes,
            "online_nodes": health.online_nodes,
            "stale_nodes": health.stale_nodes,
            "offline_nodes": health.offline_nodes,
            "total_services": health.total_services,
            "avg_disk_pct": health.avg_disk_pct,
            "avg_memory_pct": health.avg_memory_pct,
            "health_score": health.health_score,
        })

    async def _api_container_ready(request: StarletteRequest) -> JSONResponse:
        container_id = request.path_params.get("container_id", "")
        if not container_id:
            return JSONResponse(
                {"error": "Missing container_id"},
                status_code=400,
            )
        state_dir = get_state_dir()
        result = data.check_container_ready(state_dir, container_id)
        return JSONResponse(result)

    async def _api_events(request: StarletteRequest):
        """SSE stream of fleet events (check-ins, readiness transitions)."""
        from starlette.responses import StreamingResponse

        queue = data.event_bus.subscribe()

        async def _event_generator():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30.0)
                        yield f"event: {event.get('type', 'message')}\ndata: {_json.dumps(event)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                data.event_bus.unsubscribe(queue)

        return StreamingResponse(
            _event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def _api_timeline_start(request: StarletteRequest) -> JSONResponse:
        tl = data.start_timeline()
        return JSONResponse({"status": "started", "start_time": tl.start_time})

    async def _api_timeline_stop(request: StarletteRequest) -> JSONResponse:
        state_dir = get_state_dir()
        tl = data.stop_timeline()
        if tl:
            data.save_timeline(state_dir, tl)
            return JSONResponse({
                "status": "stopped",
                "duration": tl.duration,
                "services": len(tl.services),
            })
        return JSONResponse({"status": "no_active_timeline"})

    async def _api_timeline_current(request: StarletteRequest) -> JSONResponse:
        tl = data.get_active_timeline()
        if not tl:
            return JSONResponse({"active": False})
        import time as _time
        services = {}
        for sid, svc in tl.services.items():
            entry: dict = {"service_id": sid}
            if svc.first_checkin is not None:
                entry["checkin_offset"] = round(svc.first_checkin - tl.start_time, 2)
            if svc.ready_at is not None:
                entry["ready_offset"] = round(svc.ready_at - tl.start_time, 2)
            services[sid] = entry
        return JSONResponse({
            "active": True,
            "elapsed": round(_time.monotonic() - tl.start_time, 2),
            "services": services,
        })

    import asyncio as _asyncio  # noqa: E402 — used by SSE endpoint

    app.routes.insert(0, Route("/api/checkin", _api_checkin, methods=["POST"]))
    app.routes.insert(0, Route("/api/nodes", _api_nodes, methods=["GET"]))
    app.routes.insert(0, Route("/api/fleet/ready", _api_fleet_ready, methods=["GET"]))
    app.routes.insert(0, Route("/api/fleet/stale", _api_fleet_stale, methods=["GET"]))
    app.routes.insert(0, Route("/api/fleet/health", _api_fleet_health, methods=["GET"]))
    app.routes.insert(0, Route(
        "/api/container/{container_id}/ready",
        _api_container_ready, methods=["GET"],
    ))
    app.routes.insert(0, Route("/api/events", _api_events, methods=["GET"]))
    app.routes.insert(0, Route("/api/timeline/start", _api_timeline_start, methods=["POST"]))
    app.routes.insert(0, Route("/api/timeline/stop", _api_timeline_stop, methods=["POST"]))
    app.routes.insert(0, Route("/api/timeline/current", _api_timeline_current, methods=["GET"]))

    manager.register_api(app)


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
        default=9001,
        help="Port to serve on (default: 9001)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="API-only mode: bind 0.0.0.0, skip browser open, no UI pages",
    )
    return parser.parse_args(argv)


def _start_heartbeat_poller() -> None:
    """Launch the shared manager's heartbeat background poller."""
    manager.start_poller()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    env_path = Path(args.env) if args.env else None
    configure(env_path=env_path)
    data.set_server_port(args.port)
    _init_manager()
    if not args.headless:
        register_pages()
    register_api()
    app.on_startup(_start_heartbeat_poller)
    storage_secret = os.environ.get("WEBUI_STORAGE_SECRET") or secrets.token_hex(32)
    bind_host = "0.0.0.0" if args.headless else args.host
    ui.run(
        title="vm_builds",
        host=bind_host,
        port=args.port,
        dark=True,
        reload=False,
        show=not args.headless,
        storage_secret=storage_secret,
    )


if __name__ == "__main__":
    main()
