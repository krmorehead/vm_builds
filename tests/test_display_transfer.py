"""Tests for the DisplayTransferService handler-registry architecture.

Tests DisplayHandler and DisplayTransferService against the REAL Proxmox
PVE API on the primary host. No stubs — ct_start, ct_stop, ct_status are
real operations that complete in <1s and are trivially reversible.
"""

import os
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
from scripts.webui.pve_api import PveApiClient

_DESKTOP_CFG = DISPLAY_APP_CONFIGS["desktop"]
_KODI_CFG = DISPLAY_APP_CONFIGS["kodi"]
_MOONLIGHT_CFG = DISPLAY_APP_CONFIGS["moonlight"]

_HOST = os.environ.get("PRIMARY_HOST", "192.168.86.201")
_TOKEN = os.environ.get("HOME_API_TOKEN", "")


@pytest.fixture(scope="module")
def pve() -> PveApiClient:
    """Real PVE API client for the primary host."""
    token_str = f"root@pam!ansible={_TOKEN}"
    return PveApiClient(host=_HOST, node="home", token=token_str, timeout=5)


# ── TransferResult (pure data) ───────────────────────────────────────


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


# ── DisplayAppConfig (pure data) ─────────────────────────────────────


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


# ── DisplayHandler against real PVE API ──────────────────────────────


class TestDisplayHandler:
    def test_protocol_compliance(self, pve):
        h = DisplayHandler("desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY, ["kodi"], pve=pve)
        assert isinstance(h, DisplayHandlerProtocol)

    def test_properties(self, pve):
        h = DisplayHandler(
            "desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY,
            _DESKTOP_CFG.conflicts, pve=pve,
        )
        assert h.app_id == "desktop"
        assert h.handler_type == "container_display"
        assert h.conflicts_with == _DESKTOP_CFG.conflicts

    def test_viewstream_url(self, pve):
        h = DisplayHandler("desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY, [], pve=pve)
        assert h.get_viewstream_url("10.0.0.1") == f"http://10.0.0.1:{Ports.DESKTOP_DISPLAY}"

    def test_enter_starts_container(self, pve):
        h = DisplayHandler("desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY, [], pve=pve)
        result = h.enter(_HOST)
        assert result.success is True
        assert result.viewstream_url == f"http://{_HOST}:{Ports.DESKTOP_DISPLAY}"

    def test_is_active_reflects_real_status(self, pve):
        h = DisplayHandler("desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY, [], pve=pve)
        active = h.is_active(_HOST)
        assert isinstance(active, bool)
        status = pve.ct_status(int(_DESKTOP_CFG.ct_id))
        expected = (status or {}).get("status") == "running"
        assert active == expected

    def test_no_pve_returns_failure(self):
        h = DisplayHandler("desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY, [])
        result = h.enter("10.0.0.1")
        assert result.success is False
        assert "No PVE API" in result.error
        assert h.is_active("10.0.0.1") is False


class TestDisplayHandlerKodi:
    def test_protocol_compliance(self, pve):
        h = DisplayHandler("kodi", _KODI_CFG.ct_id, Ports.KODI_DISPLAY, [], pve=pve)
        assert isinstance(h, DisplayHandlerProtocol)

    def test_properties(self, pve):
        h = DisplayHandler("kodi", _KODI_CFG.ct_id, Ports.KODI_DISPLAY, _KODI_CFG.conflicts, pve=pve)
        assert h.app_id == "kodi"
        assert h.handler_type == "container_display"
        assert h.conflicts_with == _KODI_CFG.conflicts

    def test_viewstream_url(self, pve):
        h = DisplayHandler("kodi", _KODI_CFG.ct_id, Ports.KODI_DISPLAY, [], pve=pve)
        assert h.get_viewstream_url("10.0.0.1") == f"http://10.0.0.1:{Ports.KODI_DISPLAY}"

    def test_enter_starts_container(self, pve):
        h = DisplayHandler("kodi", _KODI_CFG.ct_id, Ports.KODI_DISPLAY, [], pve=pve)
        result = h.enter(_HOST)
        assert result.success is True

    def test_is_active_reflects_real_status(self, pve):
        h = DisplayHandler("kodi", _KODI_CFG.ct_id, Ports.KODI_DISPLAY, [], pve=pve)
        import time
        for _ in range(5):
            active = h.is_active(_HOST)
            status = pve.ct_status(int(_KODI_CFG.ct_id))
            expected = (status or {}).get("status") == "running"
            if active == expected:
                break
            time.sleep(1)
        assert active == expected


# ── WebViewHandler (pure logic, no infra) ────────────────────────────


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


# ── build_handler factory ────────────────────────────────────────────


class TestBuildHandler:
    def test_container_display_desktop(self, pve):
        h = build_handler(_DESKTOP_CFG, pve=pve)
        assert isinstance(h, DisplayHandler)
        assert h.app_id == "desktop"

    def test_container_display_kodi(self, pve):
        h = build_handler(_KODI_CFG, pve=pve)
        assert isinstance(h, DisplayHandler)
        assert h.app_id == "kodi"

    def test_web_view(self):
        cfg = DisplayAppConfig(app_id="ha", handler_type="web_view",
                               service_port=8123)
        h = build_handler(cfg)
        assert isinstance(h, WebViewHandler)

    def test_unknown_handler_type(self):
        cfg = DisplayAppConfig(app_id="x", handler_type="nonexistent")
        with pytest.raises(ValueError, match="Unknown handler_type"):
            build_handler(cfg)


# ── DisplayTransferService with real PVE ─────────────────────────────


class TestDisplayTransferService:
    def _make_service(self, pve) -> DisplayTransferService:
        svc = DisplayTransferService()
        svc.register(DisplayHandler(
            "desktop", _DESKTOP_CFG.ct_id, Ports.DESKTOP_DISPLAY,
            _DESKTOP_CFG.conflicts, pve=pve,
        ))
        svc.register(DisplayHandler(
            "kodi", _KODI_CFG.ct_id, Ports.KODI_DISPLAY,
            _KODI_CFG.conflicts, pve=pve,
        ))
        svc.register(DisplayHandler(
            "moonlight", _MOONLIGHT_CFG.ct_id, Ports.MOONLIGHT_DISPLAY,
            _MOONLIGHT_CFG.conflicts, pve=pve,
        ))
        return svc

    def test_register_and_lookup(self, pve):
        svc = self._make_service(pve)
        assert svc.get_handler("desktop") is not None
        assert svc.get_handler("kodi") is not None
        assert svc.get_handler("nonexistent") is None

    def test_list_handlers_metadata(self, pve):
        svc = self._make_service(pve)
        meta = svc.list_handlers()
        assert meta["desktop"]["handler_type"] == "container_display"
        assert "kodi" in meta["desktop"]["conflicts_with"]
        assert meta["kodi"]["handler_type"] == "container_display"

    def test_enter_unknown_app(self, pve):
        svc = self._make_service(pve)
        result = svc.enter("nonexistent", _HOST)
        assert result.success is False
        assert "No handler" in result.error

    def test_exit_unknown_app(self, pve):
        svc = self._make_service(pve)
        result = svc.exit("nonexistent", _HOST)
        assert result.success is False

    def test_enter_desktop_succeeds(self, pve):
        svc = self._make_service(pve)
        result = svc.enter("desktop", _HOST)
        assert result.success is True
        assert result.viewstream_url is not None

    def test_get_viewstream_url(self, pve):
        svc = self._make_service(pve)
        assert svc.get_viewstream_url("desktop", "10.0.0.1") == f"http://10.0.0.1:{Ports.DESKTOP_DISPLAY}"
        assert svc.get_viewstream_url("nonexistent", "10.0.0.1") is None

    def test_is_active_real(self, pve):
        svc = self._make_service(pve)
        active = svc.is_active("desktop", _HOST)
        assert isinstance(active, bool)

    def test_list_active_real(self, pve):
        svc = self._make_service(pve)
        active = svc.list_active(_HOST)
        assert isinstance(active, list)
        for app_id in active:
            assert app_id in ("desktop", "kodi", "moonlight")


# ── DISPLAY_APP_CONFIGS integration ──────────────────────────────────


class TestDisplayAppConfigsIntegration:
    """Verify that data.DISPLAY_APP_CONFIGS entries are valid and consistent."""

    def test_all_configs_build_handlers(self, pve):
        for app_id, cfg in DISPLAY_APP_CONFIGS.items():
            h = build_handler(cfg, pve=pve)
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

    def test_handler_type_is_string(self, pve):
        """handler_type must be a plain string, not an enum — API JSON serialization depends on this."""
        for app_id, cfg in DISPLAY_APP_CONFIGS.items():
            h = build_handler(cfg, pve=pve)
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
        assert desktop.target_hosts == ["home"], "Desktop LXC is only on home"
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
