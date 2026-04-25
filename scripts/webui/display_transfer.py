"""Display Transfer Service — handler-registry architecture for remote display streaming.

Each display app registers a DisplayHandler that knows how to enter/exit its
viewstream. The DisplayTransferService manages the registry, resolves conflicts
between mutually exclusive display apps, and provides viewstream URL discovery.

Two concrete handler types cover all current and future apps:

    DisplayHandler  — LXC containers running KasmVNC Xvnc (via PVE API)
    WebViewHandler  — Services with HTTP web UIs (no lifecycle management)
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from scripts.webui.data import DisplayAppConfig
from scripts.webui.pve_api import PveApiError

log = logging.getLogger("vm_builds.display_transfer")

ALREADY_RUNNING = "already running"


# ── Models ────────────────────────────────────────────────────────────


@dataclass
class TransferResult:
    """Outcome of an enter/exit operation."""
    success: bool
    viewstream_url: str | None = None
    error: str | None = None


@dataclass
class HandlerMetadata:
    """Serializable metadata returned by list_handlers."""
    handler_type: str
    conflicts_with: list[str]


# ── Handler Protocol ──────────────────────────────────────────────────


PveApiLike = Any


@runtime_checkable
class DisplayHandlerProtocol(Protocol):
    """Contract for display stream entry/exit strategies."""

    @property
    def app_id(self) -> str: ...

    @property
    def handler_type(self) -> str: ...

    @property
    def conflicts_with(self) -> list[str]: ...

    def get_viewstream_url(self, host_ip: str) -> str | None: ...

    def enter(self, host_ip: str) -> TransferResult: ...

    def exit(self, host_ip: str) -> TransferResult: ...

    def is_active(self, host_ip: str) -> bool: ...


# ── Concrete Handlers ─────────────────────────────────────────────────


class DisplayHandler:
    """Manages display streaming for LXC containers with KasmVNC.

    Uses the Proxmox REST API for container lifecycle (start/stop/status)
    and http://{host}:{port} for viewstream URL discovery. Conflict
    resolution is handled by DisplayTransferService.
    """

    def __init__(
        self,
        app_id: str,
        ct_id: str,
        port: int,
        conflicts: list[str],
        pve: PveApiLike | None = None,
    ) -> None:
        self._app_id = app_id
        self._ct_id = ct_id
        self._port = port
        self._conflicts = list(conflicts)
        self._pve = pve

    @property
    def app_id(self) -> str:
        return self._app_id

    @property
    def handler_type(self) -> str:
        return "container_display"

    @property
    def conflicts_with(self) -> list[str]:
        return list(self._conflicts)

    def get_viewstream_url(self, host_ip: str) -> str | None:
        return f"http://{host_ip}:{self._port}"

    def enter(self, host_ip: str) -> TransferResult:
        if not self._pve:
            return TransferResult(success=False, error="No PVE API configured")
        try:
            self._pve.ct_start(int(self._ct_id))
            return TransferResult(success=True, viewstream_url=self.get_viewstream_url(host_ip))
        except (PveApiError, OSError, TimeoutError) as exc:
            if ALREADY_RUNNING in str(exc):
                return TransferResult(success=True, viewstream_url=self.get_viewstream_url(host_ip))
            return TransferResult(success=False, error=str(exc)[:300])

    def exit(self, host_ip: str) -> TransferResult:
        if not self._pve:
            return TransferResult(success=False, error="No PVE API configured")
        try:
            self._pve.ct_stop(int(self._ct_id))
            return TransferResult(success=True)
        except (PveApiError, OSError, TimeoutError) as exc:
            return TransferResult(success=False, error=str(exc)[:300])

    def is_active(self, host_ip: str) -> bool:
        if not self._pve:
            return False
        try:
            status = self._pve.ct_status(int(self._ct_id))
            return (status or {}).get("status") == "running"
        except (PveApiError, OSError, TimeoutError):
            return False


class WebViewHandler:
    """Service with an HTTP web UI — no lifecycle management needed."""

    def __init__(
        self,
        app_id: str,
        service_port: int,
        service_path: str = "/",
    ) -> None:
        self._app_id = app_id
        self._service_port = service_port
        self._service_path = service_path

    @property
    def app_id(self) -> str:
        return self._app_id

    @property
    def handler_type(self) -> str:
        return "web_view"

    @property
    def conflicts_with(self) -> list[str]:
        return []

    def get_viewstream_url(self, host_ip: str) -> str | None:
        return f"http://{host_ip}:{self._service_port}{self._service_path}"

    def enter(self, host_ip: str) -> TransferResult:
        return TransferResult(
            success=True,
            viewstream_url=self.get_viewstream_url(host_ip),
        )

    def exit(self, _host_ip: str) -> TransferResult:
        return TransferResult(success=True)

    def is_active(self, _host_ip: str) -> bool:
        return True


# ── Handler Factory ───────────────────────────────────────────────────


_HANDLER_BUILDERS: dict[str, Callable[..., DisplayHandlerProtocol]] = {
    "container_display": lambda cfg, pve=None, **_kw: DisplayHandler(
        app_id=cfg.app_id, ct_id=cfg.ct_id, port=cfg.display_port,
        conflicts=cfg.conflicts, pve=pve,
    ),
    "web_view": lambda cfg, **_kw: WebViewHandler(
        app_id=cfg.app_id, service_port=cfg.service_port,
        service_path=cfg.service_path,
    ),
}

HANDLER_TYPES: frozenset[str] = frozenset(_HANDLER_BUILDERS.keys())


def build_handler(
    config: DisplayAppConfig,
    pve: PveApiLike | None = None,
) -> DisplayHandlerProtocol:
    """Build a concrete DisplayHandler from a DisplayAppConfig."""
    builder = _HANDLER_BUILDERS.get(config.handler_type)
    if builder is None:
        raise ValueError(f"Unknown handler_type: {config.handler_type!r}")
    return builder(config, pve=pve)


# ── Display Transfer Service ─────────────────────────────────────────


class DisplayTransferService:
    """Registry and orchestrator for display stream handlers.

    Manages handler registration, conflict resolution between mutually
    exclusive display apps, and viewstream URL discovery. Composed into
    BaseManager alongside SubscriptionManager and MetricCache.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, DisplayHandlerProtocol] = {}

    def register(self, handler: DisplayHandlerProtocol) -> None:
        """Add a handler to the registry."""
        self._handlers[handler.app_id] = handler
        log.info("Registered display handler: %s (%s)", handler.app_id, handler.handler_type)

    def get_handler(self, app_id: str) -> DisplayHandlerProtocol | None:
        """Look up a handler by app_id."""
        return self._handlers.get(app_id)

    def enter(self, app_id: str, host_ip: str) -> TransferResult:
        """Start a display app, resolving conflicts first.

        Any running app in the handler's conflicts_with list is exited
        before the requested app is entered.
        """
        handler = self._handlers.get(app_id)
        if not handler:
            return TransferResult(success=False, error=f"No handler registered for {app_id!r}")

        for conflict_id in handler.conflicts_with:
            conflict = self._handlers.get(conflict_id)
            if conflict and conflict.is_active(host_ip):
                log.info("Conflict resolution: exiting %s before entering %s", conflict_id, app_id)
                result = conflict.exit(host_ip)
                if not result.success:
                    log.warning("Failed to exit conflicting app %s: %s", conflict_id, result.error)
                    return TransferResult(
                        success=False,
                        error=f"Cannot stop conflicting app {conflict_id}: {result.error}",
                    )

        return handler.enter(host_ip)

    def exit(self, app_id: str, host_ip: str) -> TransferResult:
        """Stop a display app."""
        handler = self._handlers.get(app_id)
        if not handler:
            return TransferResult(success=False, error=f"No handler registered for {app_id!r}")
        return handler.exit(host_ip)

    def get_viewstream_url(self, app_id: str, host_ip: str) -> str | None:
        """Get the viewstream URL for a display app without starting it."""
        handler = self._handlers.get(app_id)
        if not handler:
            return None
        return handler.get_viewstream_url(host_ip)

    def is_active(self, app_id: str, host_ip: str) -> bool:
        """Check if a display app is currently running."""
        handler = self._handlers.get(app_id)
        if not handler:
            return False
        return handler.is_active(host_ip)

    def list_active(self, host_ip: str) -> list[str]:
        """Return app_ids of all currently active display apps on a host."""
        return [
            h.app_id for h in self._handlers.values()
            if h.is_active(host_ip)
        ]

    def list_handlers(self) -> dict[str, dict]:
        """Return JSON-serializable metadata for all registered handlers."""
        return {
            h.app_id: asdict(HandlerMetadata(
                handler_type=h.handler_type,
                conflicts_with=h.conflicts_with,
            ))
            for h in self._handlers.values()
        }
