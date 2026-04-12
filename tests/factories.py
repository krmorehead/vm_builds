"""Shared test factories for vm_builds test suite.

Single responsibility: construct test data objects with sensible defaults.
All test files import from here instead of reinventing ``_make_*`` helpers.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.webui import data

FIXTURES = Path(__file__).parent / "fixtures"


# ── Domain object factories ──────────────────────────────────────────


def make_telemetry(
    node_id: str = "test-node",
    *,
    last_ip: str = "192.168.86.201",
    disk_usage_pct: float = 25.0,
    memory_usage_pct: float = 40.0,
    uptime_seconds: float = 3600.0,
    status: str = "online",
    services: list[str] | None = None,
    **kwargs: Any,
) -> data.HostTelemetry:
    """Build a ``HostTelemetry`` with sensible defaults."""
    return data.HostTelemetry(
        node_id=node_id,
        last_ip=last_ip,
        local_ips=[last_ip],
        first_seen="2026-04-10T00:00:00",
        last_seen="2026-04-10T00:01:00",
        uptime_seconds=uptime_seconds,
        services=services or [],
        disk_usage_pct=disk_usage_pct,
        memory_usage_pct=memory_usage_pct,
        version="1.2.0",
        status=status,
        **kwargs,
    )


def make_host(
    name: str = "test-host",
    ip: str = "192.168.86.201",
    *,
    telemetry: data.HostTelemetry | None = None,
    wol_capable: bool = True,
    **kwargs: Any,
) -> data.Host:
    """Build a ``Host`` with optional telemetry attached."""
    host = data.Host(
        name=name,
        ip=ip,
        wol_capable=wol_capable,
        **kwargs,
    )
    if telemetry:
        host.apply_telemetry(telemetry)
    return host


def make_deploy_record(
    *,
    tags: list[str] | None = None,
    exit_code: int = 0,
    duration_seconds: float = 120.0,
    **kwargs: Any,
) -> data.DeployRecord:
    """Build a ``DeployRecord`` with sensible defaults."""
    return data.DeployRecord(
        timestamp=kwargs.pop("timestamp", "2026-04-10T12:00:00"),
        tags=tags or ["infra"],
        env_file=kwargs.pop("env_file", ".env"),
        exit_code=exit_code,
        duration_seconds=duration_seconds,
        **kwargs,
    )


def make_registered_node(
    node_id: str = "test-node",
    hostname: str = "test-host",
    *,
    disk_usage_pct: float = 25.0,
    memory_usage_pct: float = 40.0,
    status: str = "online",
    **kwargs: Any,
) -> data.RegisteredNode:
    """Build a ``RegisteredNode`` with sensible defaults."""
    return data.RegisteredNode(
        node_id=node_id,
        hostname=hostname,
        last_ip="192.168.86.201",
        local_ips=["192.168.86.201"],
        first_seen="2026-04-10T00:00:00",
        last_seen="2026-04-10T00:01:00",
        uptime_seconds=3600.0,
        services=[],
        disk_usage_pct=disk_usage_pct,
        memory_usage_pct=memory_usage_pct,
        version="1.2.0",
        status=status,
        **kwargs,
    )


def minimal_env(
    *,
    primary_host: str = "192.168.86.201",
    api_token: str = "FAKE_TOKEN_FOR_TESTS",
    **extra: str,
) -> str:
    """Build a minimal ``.env`` content string for tests."""
    lines = [
        f"PRIMARY_HOST={primary_host}",
        f"HOME_API_TOKEN={api_token}",
    ]
    for k, v in extra.items():
        lines.append(f"{k}={v}")
    return "\n".join(lines) + "\n"


SAMPLE_CHECKIN = {
    "node_id": "test-node",
    "hostname": "test-host",
    "local_ips": ["192.168.86.201"],
    "uptime_seconds": 3600,
    "services": [],
    "disk_usage_pct": 25,
    "memory_usage_pct": 40,
    "version": "1.2.0",
}


# ── NiceGUI test harness ────────────────────────────────────────────


@asynccontextmanager
async def webui_context(
    tmp_path: Path,
    *,
    env_file: str = "complete.env",
    pages: list | None = None,
    **storage_overrides: Any,
):
    """Shared NiceGUI user simulation context.

    Registers the given page modules (or a default set), configures
    storage paths, and yields the ``user`` simulation object.

    Usage::

        async with webui_context(tmp_path, pages=[dashboard, hosts]) as user:
            await user.open(Routes.DASHBOARD)
            await user.should_see("Hosts")
    """
    from nicegui import app as nicegui_app
    from nicegui.testing import user_simulation
    from scripts.webui.pages import (
        bridge, dashboard, deploy, environment, hosts, hub, images, mesh,
        nodes, router, services,
    )

    default_pages = [
        dashboard, environment, hosts, nodes, services,
        deploy, images, hub, bridge, mesh, router,
    ]

    async with user_simulation() as user:
        for page_module in (pages or default_pages):
            page_module.register()
        nicegui_app.storage.general["env_path"] = str(FIXTURES / env_file)
        nicegui_app.storage.general["images_dir"] = str(tmp_path / "images")
        nicegui_app.storage.general["state_dir"] = str(tmp_path / "state")
        nicegui_app.storage.general["selected_tags"] = []
        nicegui_app.storage.general.update(storage_overrides)
        yield user
