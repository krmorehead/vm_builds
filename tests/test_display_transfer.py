"""Tests for the DisplayTransferService handler-registry architecture.

Exercises the handler protocol, concrete handlers, factory, conflict
resolution, and service registry. SSH calls use a stub since they would
execute commands on remote Proxmox hosts — an irreversible side effect.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.webui.data import DISPLAY_APP_CONFIGS, Ports
from scripts.webui.data import DisplayAppConfig
from scripts.webui.display_transfer import (
    DisplayHandler,
    DisplayHandlerProtocol,
    DisplayTransferService,
    TransferResult,
    WebViewHandler,
    build_handler,
)

_DESKTOP_CFG = DISPLAY_APP_CONFIGS["desktop"]
_KODI_CFG = DISPLAY_APP_CONFIGS["kodi"]
_MOONLIGHT_CFG = DISPLAY_APP_CONFIGS["moonlight"]


# ── Stub SSH function ─────────────────────────────────────────────────
# WHY: _ssh_exec runs real SSH commands on remote Proxmox hosts (pct start,
# pct stop, etc.) — irreversible infrastructure side effects.
# HOW: The stub records calls and returns configurable (ok, output) tuples,
# letting us verify handler logic without touching hardware.


class SshStub:
    """Records SSH calls and returns pre-configured responses."""

    def __init__(self, default_ok: bool = True, default_output: str = ""):
        self.calls: list[tuple[str, str, int]] = []
        self._responses: dict[str, tuple[bool, str]] = {}
        self._default = (default_ok, default_output)

    def set_response(self, cmd_fragment: str, ok: bool, output: str) -> None:
        self._responses[cmd_fragment] = (ok, output)

    def __call__(self, host: str, cmd: str, timeout: int) -> tuple[bool, str]:
        self.calls.append((host, cmd, timeout))
        for fragment, response in self._responses.items():
            if fragment in cmd:
                return response
        return self._default


# ── TransferResult ────────────────────────────────────────────────────


class TestTransferResult:
    def test_defaults(self):
        r = TransferResult(success=True)
        assert r.success is True
        assert r.viewstream_url is None
        assert r.error is None

    def test_with_all_fields(self):
        r = TransferResult(
            success=False,
            viewstream_url="http://10.0.0.1:6081",
            error="connection refused",
        )
        assert r.success is False
        assert r.viewstream_url == "http://10.0.0.1:6081"
        assert r.error == "connection refused"


# ── DisplayAppConfig ──────────────────────────────────────────────────


class TestDisplayAppConfig:
    def test_required_fields(self):
        cfg = DisplayAppConfig(app_id="test", handler_type="container_display")
        assert cfg.app_id == "test"
        assert cfg.handler_type == "container_display"
        assert cfg.conflicts == []

    def test_mutable_defaults_isolation(self):
        a = DisplayAppConfig(app_id="a", handler_type="container_display")
        b = DisplayAppConfig(app_id="b", handler_type="container_display")
        a.conflicts.append("x")
        assert b.conflicts == []


# ── DisplayHandler (unified container handler) ───────────────────────


class TestDisplayHandler:
    def test_protocol_compliance(self):
        ssh = SshStub()
        h = DisplayHandler("desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY, ["kodi"], ssh)
        assert isinstance(h, DisplayHandlerProtocol)

    def test_properties(self):
        ssh = SshStub()
        h = DisplayHandler(
            "desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY,
            _DESKTOP_CFG.conflicts, ssh,
        )
        assert h.app_id == "desktop"
        assert h.handler_type == "container_display"
        assert h.conflicts_with == _DESKTOP_CFG.conflicts

    def test_viewstream_url(self):
        ssh = SshStub()
        h = DisplayHandler("desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY, [], ssh)
        assert h.get_viewstream_url("10.0.0.1") == f"http://10.0.0.1:{Ports.DESKTOP_DISPLAY}"

    def test_enter_success(self):
        ssh = SshStub(default_ok=True, default_output="")
        h = DisplayHandler("desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY, [], ssh)
        result = h.enter("10.0.0.1")
        assert result.success is True
        assert result.viewstream_url == f"http://10.0.0.1:{Ports.DESKTOP_DISPLAY}"
        assert ("10.0.0.1", f"pct start {_DESKTOP_CFG.ct_id}", 30) in ssh.calls

    def test_enter_already_running(self):
        ssh = SshStub(default_ok=False, default_output="CT 400 already running")
        h = DisplayHandler("desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY, [], ssh)
        result = h.enter("10.0.0.1")
        assert result.success is True
        assert result.viewstream_url == f"http://10.0.0.1:{Ports.DESKTOP_DISPLAY}"

    def test_enter_failure(self):
        ssh = SshStub(default_ok=False, default_output="container locked")
        h = DisplayHandler("desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY, [], ssh)
        result = h.enter("10.0.0.1")
        assert result.success is False
        assert result.viewstream_url is None
        assert result.error == "container locked"

    def test_exit(self):
        ssh = SshStub(default_ok=True)
        h = DisplayHandler("desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY, [], ssh)
        result = h.exit("10.0.0.1")
        assert result.success is True
        assert ("10.0.0.1", f"pct stop {_DESKTOP_CFG.ct_id}", 30) in ssh.calls

    def test_is_active_true(self):
        ssh = SshStub()
        ssh.set_response("pct status", True, "status: running")
        h = DisplayHandler("desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY, [], ssh)
        assert h.is_active("10.0.0.1") is True

    def test_is_active_false(self):
        ssh = SshStub()
        ssh.set_response("pct status", True, "status: stopped")
        h = DisplayHandler("desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY, [], ssh)
        assert h.is_active("10.0.0.1") is False

    def test_is_active_ssh_failure(self):
        ssh = SshStub(default_ok=False)
        h = DisplayHandler("desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY, [], ssh)
        assert h.is_active("10.0.0.1") is False


class TestDisplayHandlerKodi:
    """Test DisplayHandler with Kodi-specific config."""

    def test_protocol_compliance(self):
        ssh = SshStub()
        h = DisplayHandler("kodi", _KODI_CFG.ct_id, Ports.KODI_DISPLAY, [], ssh)
        assert isinstance(h, DisplayHandlerProtocol)

    def test_properties(self):
        ssh = SshStub()
        h = DisplayHandler("kodi", _KODI_CFG.ct_id, Ports.KODI_DISPLAY, _KODI_CFG.conflicts, ssh)
        assert h.app_id == "kodi"
        assert h.handler_type == "container_display"
        assert h.conflicts_with == _KODI_CFG.conflicts

    def test_viewstream_url(self):
        ssh = SshStub()
        h = DisplayHandler("kodi", _KODI_CFG.ct_id, Ports.KODI_DISPLAY, [], ssh)
        assert h.get_viewstream_url("10.0.0.1") == f"http://10.0.0.1:{Ports.KODI_DISPLAY}"

    def test_enter_success(self):
        ssh = SshStub(default_ok=True)
        h = DisplayHandler("kodi", _KODI_CFG.ct_id, Ports.KODI_DISPLAY, [], ssh)
        result = h.enter("10.0.0.1")
        assert result.success is True
        assert result.viewstream_url == f"http://10.0.0.1:{Ports.KODI_DISPLAY}"

    def test_enter_already_running(self):
        ssh = SshStub(default_ok=False, default_output="already running")
        h = DisplayHandler("kodi", _KODI_CFG.ct_id, Ports.KODI_DISPLAY, [], ssh)
        result = h.enter("10.0.0.1")
        assert result.success is True

    def test_enter_failure(self):
        ssh = SshStub(default_ok=False, default_output="container locked")
        h = DisplayHandler("kodi", _KODI_CFG.ct_id, Ports.KODI_DISPLAY, [], ssh)
        result = h.enter("10.0.0.1")
        assert result.success is False
        assert result.error == "container locked"

    def test_exit(self):
        ssh = SshStub(default_ok=True)
        h = DisplayHandler("kodi", _KODI_CFG.ct_id, Ports.KODI_DISPLAY, [], ssh)
        result = h.exit("10.0.0.1")
        assert result.success is True
        assert ("10.0.0.1", f"pct stop {_KODI_CFG.ct_id}", 30) in ssh.calls

    def test_is_active_true(self):
        ssh = SshStub()
        ssh.set_response("pct status", True, "status: running")
        h = DisplayHandler("kodi", _KODI_CFG.ct_id, Ports.KODI_DISPLAY, [], ssh)
        assert h.is_active("10.0.0.1") is True

    def test_is_active_false(self):
        ssh = SshStub()
        ssh.set_response("pct status", True, "status: stopped")
        h = DisplayHandler("kodi", _KODI_CFG.ct_id, Ports.KODI_DISPLAY, [], ssh)
        assert h.is_active("10.0.0.1") is False

    def test_is_active_ssh_failure(self):
        ssh = SshStub(default_ok=False)
        h = DisplayHandler("kodi", _KODI_CFG.ct_id, Ports.KODI_DISPLAY, [], ssh)
        assert h.is_active("10.0.0.1") is False


# ── WebViewHandler ────────────────────────────────────────────────────


class TestWebViewHandler:
    def test_protocol_compliance(self):
        h = WebViewHandler("homeassistant", 8123)
        assert isinstance(h, DisplayHandlerProtocol)

    def test_properties(self):
        h = WebViewHandler("homeassistant", 8123, "/dashboard")
        assert h.app_id == "homeassistant"
        assert h.conflicts_with == []

    def test_viewstream_url(self):
        h = WebViewHandler("homeassistant", 8123, "/dash")
        assert h.get_viewstream_url("10.0.0.1") == "http://10.0.0.1:8123/dash"

    def test_enter_always_succeeds(self):
        h = WebViewHandler("homeassistant", 8123)
        result = h.enter("10.0.0.1")
        assert result.success is True
        assert "http://10.0.0.1:8123/" in result.viewstream_url

    def test_exit_is_noop(self):
        h = WebViewHandler("homeassistant", 8123)
        result = h.exit("10.0.0.1")
        assert result.success is True

    def test_is_active_always_true(self):
        h = WebViewHandler("homeassistant", 8123)
        assert h.is_active("10.0.0.1") is True


# ── build_handler factory ─────────────────────────────────────────────


class TestBuildHandler:
    def test_container_display_desktop(self):
        ssh = SshStub()
        h = build_handler(_DESKTOP_CFG, ssh)
        assert isinstance(h, DisplayHandler)
        assert h.app_id == "desktop"

    def test_container_display_kodi(self):
        ssh = SshStub()
        h = build_handler(_KODI_CFG, ssh)
        assert isinstance(h, DisplayHandler)
        assert h.app_id == "kodi"

    def test_web_view(self):
        ssh = SshStub()
        cfg = DisplayAppConfig(app_id="ha", handler_type="web_view",
                               service_port=8123)
        h = build_handler(cfg, ssh)
        assert isinstance(h, WebViewHandler)

    def test_unknown_handler_type(self):
        ssh = SshStub()
        cfg = DisplayAppConfig(app_id="x", handler_type="nonexistent")
        with pytest.raises(ValueError, match="Unknown handler_type"):
            build_handler(cfg, ssh)


# ── DisplayTransferService ────────────────────────────────────────────


class TestDisplayTransferService:
    def _make_service(self, ssh: SshStub | None = None) -> DisplayTransferService:
        ssh = ssh or SshStub()
        svc = DisplayTransferService()
        svc.register(DisplayHandler(
            "desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY,
            _DESKTOP_CFG.conflicts, ssh,
        ))
        svc.register(DisplayHandler(
            "kodi", _KODI_CFG.ct_id, Ports.KODI_DISPLAY,
            _KODI_CFG.conflicts, ssh,
        ))
        svc.register(DisplayHandler(
            "moonlight", _MOONLIGHT_CFG.ct_id, Ports.MOONLIGHT_DISPLAY,
            _MOONLIGHT_CFG.conflicts, ssh,
        ))
        return svc

    def test_register_and_lookup(self):
        svc = self._make_service()
        assert svc.get_handler("desktop") is not None
        assert svc.get_handler("kodi") is not None
        assert svc.get_handler("nonexistent") is None

    def test_list_handlers_metadata(self):
        svc = self._make_service()
        meta = svc.list_handlers()
        assert meta["desktop"]["handler_type"] == "container_display"
        assert "kodi" in meta["desktop"]["conflicts_with"]
        assert meta["kodi"]["handler_type"] == "container_display"

    def test_enter_unknown_app(self):
        svc = self._make_service()
        result = svc.enter("nonexistent", "10.0.0.1")
        assert result.success is False
        assert "No handler" in result.error

    def test_exit_unknown_app(self):
        svc = self._make_service()
        result = svc.exit("nonexistent", "10.0.0.1")
        assert result.success is False

    def test_enter_with_conflict_resolution(self):
        ssh = SshStub()
        ssh.set_response(f"pct status {_KODI_CFG.ct_id}", True, "status: running")
        ssh.set_response(f"pct stop {_KODI_CFG.ct_id}", True, "")
        ssh.set_response(f"pct start {_DESKTOP_CFG.ct_id}", True, "")
        svc = self._make_service(ssh)

        result = svc.enter("desktop", "10.0.0.1")
        assert result.success is True
        stop_calls = [c for c in ssh.calls if f"pct stop {_KODI_CFG.ct_id}" in c[1]]
        assert len(stop_calls) == 1, "Should have stopped kodi before starting desktop"

    def test_enter_fails_when_conflict_exit_fails(self):
        ssh = SshStub()
        ssh.set_response(f"pct status {_KODI_CFG.ct_id}", True, "status: running")
        ssh.set_response(f"pct stop {_KODI_CFG.ct_id}", False, "stop failed")
        svc = self._make_service(ssh)

        result = svc.enter("desktop", "10.0.0.1")
        assert result.success is False
        assert "Cannot stop conflicting app" in result.error
        start_calls = [c for c in ssh.calls if "pct start" in c[1]]
        assert len(start_calls) == 0, "Should not start desktop when conflict exit fails"

    def test_enter_no_conflict_when_not_active(self):
        ssh = SshStub()
        ssh.set_response("pct status", True, "status: stopped")
        ssh.set_response(f"pct start {_DESKTOP_CFG.ct_id}", True, "")
        svc = self._make_service(ssh)

        result = svc.enter("desktop", "10.0.0.1")
        assert result.success is True
        pct_stop_calls = [c for c in ssh.calls if c[1].startswith("pct stop")]
        assert len(pct_stop_calls) == 0

    def test_get_viewstream_url(self):
        svc = self._make_service()
        assert svc.get_viewstream_url("desktop", "10.0.0.1") == f"http://10.0.0.1:{Ports.DESKTOP_DISPLAY}"
        assert svc.get_viewstream_url("nonexistent", "10.0.0.1") is None

    def test_is_active(self):
        ssh = SshStub()
        ssh.set_response(f"pct status {_DESKTOP_CFG.ct_id}", True, "status: running")
        svc = self._make_service(ssh)
        assert svc.is_active("desktop", "10.0.0.1") is True

    def test_list_active(self):
        ssh = SshStub()
        ssh.set_response(f"pct status {_DESKTOP_CFG.ct_id}", True, "status: running")
        ssh.set_response(f"pct status {_KODI_CFG.ct_id}", True, "status: stopped")
        ssh.set_response(f"pct status {_MOONLIGHT_CFG.ct_id}", True, "status: running")
        svc = self._make_service(ssh)

        active = svc.list_active("10.0.0.1")
        assert "desktop" in active
        assert "moonlight" in active
        assert "kodi" not in active


# ── DISPLAY_APP_CONFIGS integration ───────────────────────────────────


class TestDisplayAppConfigsIntegration:
    """Verify that data.DISPLAY_APP_CONFIGS entries are valid and consistent."""

    def test_all_configs_build_handlers(self):
        ssh = SshStub()
        for app_id, cfg in DISPLAY_APP_CONFIGS.items():
            h = build_handler(cfg, ssh)
            assert h.app_id == app_id

    def test_configs_have_labels(self):
        for app_id, cfg in DISPLAY_APP_CONFIGS.items():
            assert cfg.label, f"{app_id} missing label"
            assert cfg.icon, f"{app_id} missing icon"

    def test_conflict_references_are_valid(self):
        all_ids = set(DISPLAY_APP_CONFIGS.keys())
        for app_id, cfg in DISPLAY_APP_CONFIGS.items():
            for c in cfg.conflicts:
                assert c in all_ids, (
                    f"{app_id} references unknown conflict {c!r}"
                )

    def test_display_ports_unique(self):
        ports = [cfg.display_port for cfg in DISPLAY_APP_CONFIGS.values()
                 if cfg.display_port > 0]
        assert len(ports) == len(set(ports)), "Display ports must be unique"

    def test_handler_types_are_known(self):
        from scripts.webui.display_transfer import HANDLER_TYPES
        for app_id, cfg in DISPLAY_APP_CONFIGS.items():
            assert cfg.handler_type in HANDLER_TYPES, (
                f"{app_id} has unknown handler_type {cfg.handler_type!r}"
            )

    def test_handler_type_is_string(self):
        """handler_type must be a plain string, not an enum — API JSON serialization depends on this."""
        ssh = SshStub()
        for app_id, cfg in DISPLAY_APP_CONFIGS.items():
            h = build_handler(cfg, ssh)
            assert isinstance(h.handler_type, str), (
                f"{app_id} handler_type is {type(h.handler_type).__name__}, expected str"
            )

    def test_target_hosts_field_present(self):
        """Every DisplayAppConfig must have a target_hosts list (empty = all hosts)."""
        for app_id, cfg in DISPLAY_APP_CONFIGS.items():
            assert isinstance(cfg.target_hosts, list), (
                f"{app_id} target_hosts must be a list"
            )

    def test_target_hosts_constrained_apps(self):
        """Apps with hardware constraints must specify target_hosts."""
        desktop = DISPLAY_APP_CONFIGS["desktop"]
        assert desktop.target_hosts == [], "Desktop LXC is on all hosts"
        kodi = DISPLAY_APP_CONFIGS["kodi"]
        assert kodi.target_hosts == ["home"], "Kodi is only on home"
        moonlight = DISPLAY_APP_CONFIGS["moonlight"]
        assert moonlight.target_hosts == ["mesh1"], "Moonlight is only on mesh1"

    def test_kiosk_available_everywhere(self):
        """Kiosk has no target_hosts constraint — available on all nodes."""
        kiosk = DISPLAY_APP_CONFIGS["kiosk"]
        assert kiosk.target_hosts == [], "Kiosk should be available everywhere"

    def test_transfer_result_has_no_display_type(self):
        """TransferResult must not carry display_type — it was removed in the KasmVNC migration."""
        r = TransferResult(success=True)
        assert not hasattr(r, "display_type"), "TransferResult should not have display_type"

    def test_all_managed_apps_use_ct_id(self):
        """All container_display apps must have a ct_id set."""
        for app_id, cfg in DISPLAY_APP_CONFIGS.items():
            if cfg.handler_type == "container_display":
                assert cfg.ct_id, f"{app_id} is container_display but missing ct_id"
