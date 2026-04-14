"""Display Transfer Service — handler-registry architecture for remote display streaming.

Each display app registers a DisplayHandler that knows how to enter/exit its
viewstream. The DisplayTransferService manages the registry, resolves conflicts
between mutually exclusive display apps, and provides viewstream URL discovery.

Three concrete handler types cover all current and future apps:

    QemuVncHandler     — QEMU VMs via Proxmox VNC Unix socket + host-side websockify
    WaylandVncHandler  — LXC containers running sway + wayvnc headless Wayland
    WebViewHandler     — Services with HTTP web UIs (no lifecycle management)
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Callable, Protocol, runtime_checkable

from scripts.webui.data import DisplayAppConfig

log = logging.getLogger("vm_builds.display_transfer")

ALREADY_RUNNING = "already running"
SSH_STATUS_TIMEOUT = 10
SSH_GUEST_OP_TIMEOUT = 30
PROXMOX_STATUS_RUNNING = "status: running"


# ── Models ────────────────────────────────────────────────────────────


class DisplayType(Enum):
    """How the viewstream is rendered in the browser."""
    VNC = "vnc"
    WEB = "web"


@dataclass
class TransferResult:
    """Outcome of an enter/exit operation."""
    success: bool
    viewstream_url: str | None = None
    display_type: DisplayType = DisplayType.VNC
    error: str | None = None


@dataclass
class HandlerMetadata:
    """Serializable metadata returned by list_handlers."""
    display_type: str
    conflicts_with: list[str]


# ── Handler Protocol ──────────────────────────────────────────────────


SshExecFn = Callable[[str, str, int], tuple[bool, str]]


@runtime_checkable
class DisplayHandler(Protocol):
    """Contract for display stream entry/exit strategies."""

    @property
    def app_id(self) -> str: ...

    @property
    def display_type(self) -> DisplayType: ...

    @property
    def conflicts_with(self) -> list[str]: ...

    def get_viewstream_url(self, host_ip: str) -> str | None: ...

    def enter(self, host_ip: str) -> TransferResult: ...

    def exit(self, host_ip: str) -> TransferResult: ...

    def is_active(self, host_ip: str) -> bool: ...


# ── Concrete Handlers ─────────────────────────────────────────────────


class _VncHandlerBase:
    """Shared base for VNC-streaming handlers (QEMU and Wayland).

    Provides common property wiring and viewstream URL construction.
    Subclasses implement enter/exit/is_active with their specific
    management commands (qm vs pct).
    """

    def __init__(
        self,
        app_id: str,
        vnc_ws_port: int,
        conflicts: list[str],
        ssh_exec: SshExecFn,
    ) -> None:
        self._app_id = app_id
        self._vnc_ws_port = vnc_ws_port
        self._conflicts = list(conflicts)
        self._ssh = ssh_exec

    @property
    def app_id(self) -> str:
        return self._app_id

    @property
    def display_type(self) -> DisplayType:
        return DisplayType.VNC

    @property
    def conflicts_with(self) -> list[str]:
        return list(self._conflicts)

    def get_viewstream_url(self, host_ip: str) -> str | None:
        return f"ws://{host_ip}:{self._vnc_ws_port}"

    def _make_enter_result(self, ok: bool, out: str, host_ip: str) -> TransferResult:
        url = self.get_viewstream_url(host_ip) if ok else None
        return TransferResult(
            success=ok, viewstream_url=url,
            display_type=DisplayType.VNC,
            error=out if not ok else None,
        )

    def _make_exit_result(self, ok: bool, out: str) -> TransferResult:
        return TransferResult(success=ok, error=out if not ok else None)

    def _check_status(self, host_ip: str, cmd: str) -> bool:
        ok, out = self._ssh(host_ip, cmd, SSH_STATUS_TIMEOUT)
        return ok and PROXMOX_STATUS_RUNNING in out


class QemuVncHandler(_VncHandlerBase):
    """QEMU VM display via Proxmox VNC Unix socket + host-side websockify."""

    def __init__(
        self,
        app_id: str,
        vmid: str,
        vnc_ws_port: int,
        conflicts: list[str],
        ssh_exec: SshExecFn,
    ) -> None:
        super().__init__(app_id, vnc_ws_port, conflicts, ssh_exec)
        self._vmid = vmid

    def enter(self, host_ip: str) -> TransferResult:
        ok, out = self._ssh(host_ip, f"qm start {self._vmid}", SSH_GUEST_OP_TIMEOUT)
        if not ok and ALREADY_RUNNING in out:
            ok = True
        return self._make_enter_result(ok, out, host_ip)

    def exit(self, host_ip: str) -> TransferResult:
        ok, out = self._ssh(host_ip, f"qm stop {self._vmid}", SSH_GUEST_OP_TIMEOUT)
        return self._make_exit_result(ok, out)

    def is_active(self, host_ip: str) -> bool:
        return self._check_status(host_ip, f"qm status {self._vmid}")


class WaylandVncHandler(_VncHandlerBase):
    """LXC container with sway + wayvnc headless Wayland display."""

    def __init__(
        self,
        app_id: str,
        ct_id: str,
        vnc_ws_port: int,
        conflicts: list[str],
        ssh_exec: SshExecFn,
    ) -> None:
        super().__init__(app_id, vnc_ws_port, conflicts, ssh_exec)
        self._ct_id = ct_id

    def enter(self, host_ip: str) -> TransferResult:
        ok, out = self._ssh(host_ip, f"pct start {self._ct_id}", SSH_GUEST_OP_TIMEOUT)
        if not ok and ALREADY_RUNNING in out:
            ok = True
        return self._make_enter_result(ok, out, host_ip)

    def exit(self, host_ip: str) -> TransferResult:
        ok, out = self._ssh(host_ip, f"pct stop {self._ct_id}", SSH_GUEST_OP_TIMEOUT)
        return self._make_exit_result(ok, out)

    def is_active(self, host_ip: str) -> bool:
        return self._check_status(host_ip, f"pct status {self._ct_id}")


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
    def display_type(self) -> DisplayType:
        return DisplayType.WEB

    @property
    def conflicts_with(self) -> list[str]:
        return []

    def get_viewstream_url(self, host_ip: str) -> str | None:
        return f"http://{host_ip}:{self._service_port}{self._service_path}"

    def enter(self, host_ip: str) -> TransferResult:
        return TransferResult(
            success=True,
            viewstream_url=self.get_viewstream_url(host_ip),
            display_type=DisplayType.WEB,
        )

    def exit(self, host_ip: str) -> TransferResult:
        return TransferResult(success=True)

    def is_active(self, host_ip: str) -> bool:
        return True


# ── Handler Factory ───────────────────────────────────────────────────


_HANDLER_BUILDERS: dict[str, Callable[[DisplayAppConfig, SshExecFn], DisplayHandler]] = {
    "qemu_vnc": lambda cfg, ssh: QemuVncHandler(
        app_id=cfg.app_id, vmid=cfg.vmid, vnc_ws_port=cfg.vnc_ws_port,
        conflicts=cfg.conflicts, ssh_exec=ssh,
    ),
    "wayland_vnc": lambda cfg, ssh: WaylandVncHandler(
        app_id=cfg.app_id, ct_id=cfg.ct_id, vnc_ws_port=cfg.vnc_ws_port,
        conflicts=cfg.conflicts, ssh_exec=ssh,
    ),
    "web_view": lambda cfg, _ssh: WebViewHandler(
        app_id=cfg.app_id, service_port=cfg.service_port,
        service_path=cfg.service_path,
    ),
}

HANDLER_TYPES: frozenset[str] = frozenset(_HANDLER_BUILDERS.keys())


def build_handler(config: DisplayAppConfig, ssh_exec: SshExecFn) -> DisplayHandler:
    """Build a concrete DisplayHandler from a DisplayAppConfig."""
    builder = _HANDLER_BUILDERS.get(config.handler_type)
    if builder is None:
        raise ValueError(f"Unknown handler_type: {config.handler_type!r}")
    return builder(config, ssh_exec)


# ── Display Transfer Service ─────────────────────────────────────────


class DisplayTransferService:
    """Registry and orchestrator for display stream handlers.

    Manages handler registration, conflict resolution between mutually
    exclusive display apps, and viewstream URL discovery. Composed into
    BaseManager alongside SubscriptionManager and MetricCache.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, DisplayHandler] = {}

    def register(self, handler: DisplayHandler) -> None:
        """Add a handler to the registry."""
        self._handlers[handler.app_id] = handler
        log.info("Registered display handler: %s (%s)", handler.app_id, handler.display_type.value)

    def get_handler(self, app_id: str) -> DisplayHandler | None:
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
                display_type=h.display_type.value,
                conflicts_with=h.conflicts_with,
            ))
            for h in self._handlers.values()
        }
