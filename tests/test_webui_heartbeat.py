"""Tests for scripts/webui/heartbeat.py — subscription lifecycle,
SSH collectors (real hardware), parsers, and metric cache.

Collector tests SSH to REAL hosts from test.env. If a host is down,
the test fails — that's intentional (anti-fake-test doctrine).

Parser tests are pure functions with fixture data — no mocks needed.

Run with: pytest tests/test_webui_heartbeat.py -v
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.webui.heartbeat import (
    HeartbeatCache,
    HeartbeatSubscription,
    MetricCache,
    SubscriptionManager,
    _parse_brctl,
    _parse_dhcp_leases,
    _parse_iw_dev,
    _parse_key_value,
    _parse_station_dump,
    _parse_system_info,
    _parse_uci_wireless,
    _ssh_exec,
    collect_batman_metrics,
    collect_bridge_metrics,
    collect_mesh_metrics,
    collect_router_metrics,
    collect_wifi_metrics,
    signal_percentage,
    signal_quality,
)



# ── SubscriptionManager lifecycle ────────────────────────────────────


class TestSubscriptionManager:
    def test_subscribe_creates_new(self):
        mgr = SubscriptionManager()
        sub = mgr.subscribe("node1", "wifi", ttl_seconds=30)
        assert sub.node_id == "node1"
        assert sub.metric_type == "wifi"
        assert sub.subscription_id
        assert sub.expires_at > datetime.now()

    def test_subscribe_refreshes_existing(self):
        mgr = SubscriptionManager()
        sub1 = mgr.subscribe("node1", "wifi", ttl_seconds=10)
        old_expiry = sub1.expires_at
        time.sleep(0.01)
        sub2 = mgr.subscribe("node1", "wifi", ttl_seconds=30)
        assert sub1.subscription_id == sub2.subscription_id
        assert sub2.expires_at > old_expiry

    def test_subscribe_different_metric_types(self):
        mgr = SubscriptionManager()
        sub1 = mgr.subscribe("node1", "wifi")
        sub2 = mgr.subscribe("node1", "bridge")
        assert sub1.subscription_id != sub2.subscription_id

    def test_unsubscribe(self):
        mgr = SubscriptionManager()
        sub = mgr.subscribe("node1", "wifi")
        assert mgr.unsubscribe(sub.subscription_id)
        assert not mgr.is_subscribed("node1", "wifi")

    def test_unsubscribe_nonexistent(self):
        mgr = SubscriptionManager()
        assert not mgr.unsubscribe("fake-id")

    def test_get_active_nodes(self):
        mgr = SubscriptionManager()
        mgr.subscribe("node1", "wifi", ttl_seconds=30)
        mgr.subscribe("node2", "bridge", ttl_seconds=30)
        active = mgr.get_active_nodes()
        assert ("node1", "wifi") in active
        assert ("node2", "bridge") in active

    def test_get_active_nodes_excludes_expired(self):
        mgr = SubscriptionManager()
        mgr.subscribe("node1", "wifi", ttl_seconds=0.001)
        time.sleep(0.01)
        active = mgr.get_active_nodes()
        assert len(active) == 0

    def test_cleanup_expired(self):
        mgr = SubscriptionManager()
        mgr.subscribe("node1", "wifi", ttl_seconds=0.001)
        mgr.subscribe("node2", "bridge", ttl_seconds=30)
        time.sleep(0.01)
        removed = mgr.cleanup_expired()
        assert removed == 1
        subs = mgr.list_subscriptions()
        assert len(subs) == 1
        assert subs[0].node_id == "node2"

    def test_is_subscribed(self):
        mgr = SubscriptionManager()
        mgr.subscribe("node1", "wifi", ttl_seconds=30)
        assert mgr.is_subscribed("node1", "wifi")
        assert not mgr.is_subscribed("node1", "bridge")
        assert not mgr.is_subscribed("node2", "wifi")

    def test_list_subscriptions(self):
        mgr = SubscriptionManager()
        mgr.subscribe("a", "wifi")
        mgr.subscribe("b", "bridge")
        subs = mgr.list_subscriptions()
        assert len(subs) == 2
        node_ids = {s.node_id for s in subs}
        assert node_ids == {"a", "b"}

    def test_multi_subscriber_same_target(self):
        """Multiple subscriptions to the same node+metric coalesce."""
        mgr = SubscriptionManager()
        sub1 = mgr.subscribe("node1", "wifi")
        sub2 = mgr.subscribe("node1", "wifi")
        assert sub1.subscription_id == sub2.subscription_id
        assert len(mgr.list_subscriptions()) == 1


# ── MetricCache ──────────────────────────────────────────────────────


class TestMetricCache:
    def test_store_and_get(self):
        cache = MetricCache()
        entry = HeartbeatCache(
            node_id="node1", metric_type="wifi",
            data={"signal": -55}, collected_at="2026-01-01T00:00:00",
        )
        cache.store(entry)
        result = cache.get("node1", "wifi")
        assert result is not None
        assert result.data["signal"] == -55

    def test_get_nonexistent(self):
        cache = MetricCache()
        assert cache.get("nothing", "wifi") is None

    def test_clear_specific_node(self):
        cache = MetricCache()
        cache.store(HeartbeatCache("a", "wifi", {}, "t1"))
        cache.store(HeartbeatCache("b", "wifi", {}, "t2"))
        cache.clear(node_id="a")
        assert cache.get("a", "wifi") is None
        assert cache.get("b", "wifi") is not None

    def test_clear_all(self):
        cache = MetricCache()
        cache.store(HeartbeatCache("a", "wifi", {}, "t1"))
        cache.store(HeartbeatCache("b", "bridge", {}, "t2"))
        cache.clear()
        assert cache.all_entries() == []

    def test_all_entries(self):
        cache = MetricCache()
        cache.store(HeartbeatCache("a", "wifi", {}, "t1"))
        cache.store(HeartbeatCache("b", "bridge", {}, "t2"))
        assert len(cache.all_entries()) == 2

    def test_overwrite(self):
        cache = MetricCache()
        cache.store(HeartbeatCache("a", "wifi", {"v": 1}, "t1"))
        cache.store(HeartbeatCache("a", "wifi", {"v": 2}, "t2"))
        assert cache.get("a", "wifi").data["v"] == 2
        assert len(cache.all_entries()) == 1


# ── Real SSH collector tests ──────────────────────────────────────────
# These test against actual hardware from test.env. If a host is down,
# the test fails — that's the point.


@pytest.mark.integration
class TestRealSSH:
    """Verify _ssh_exec works against real Proxmox hosts."""

    @pytest.fixture()
    def env(self, test_env):
        return test_env

    def test_ssh_to_primary_host(self, env):
        """Basic SSH works to the primary Proxmox host."""
        ip = env["PRIMARY_HOST"]
        ok, out = _ssh_exec(ip, "hostname", timeout=10)
        assert ok, f"SSH to primary host ({ip}) failed: {out}"
        assert out.strip(), "hostname returned empty"

    def test_ssh_to_bridge_1(self, env):
        ip = env.get("BRIDGE_1_HOST", "")
        assert ip, "BRIDGE_1_HOST not set in test.env"
        ok, out = _ssh_exec(ip, "hostname", timeout=10)
        assert ok, f"SSH to bridge-1 ({ip}) failed: {out}"

    def test_ssh_to_bridge_2(self, env):
        ip = env.get("BRIDGE_2_HOST", "")
        assert ip, "BRIDGE_2_HOST not set in test.env"
        ok, out = _ssh_exec(ip, "hostname", timeout=10)
        assert ok, f"SSH to bridge-2 ({ip}) failed: {out}"

    def test_ssh_to_mesh_2(self, env):
        ip = env.get("MESH_2_HOST", "")
        assert ip, "MESH_2_HOST not set in test.env"
        ok, out = _ssh_exec(ip, "hostname", timeout=10)
        assert ok, f"SSH to mesh2 ({ip}) failed: {out}"

    def test_ssh_failure_with_bad_host(self):
        """Non-routable IP returns failure, not an exception."""
        ok, out = _ssh_exec("192.0.2.1", "hostname", timeout=3)
        assert not ok


@pytest.mark.integration
class TestRealWifiCollectors:
    """Test WiFi/bridge/mesh collectors against real hardware.

    Requires WiFi containers to be deployed (molecule converge).
    The host-side wrapper scripts forward wifi_setup.sh calls
    into the container via pct exec.
    """

    @pytest.fixture()
    def env(self, test_env):
        return test_env

    def test_bridge_1_wifi_metrics(self, env):
        """collect_wifi_metrics against bridge-1 returns real data."""
        ip = env.get("BRIDGE_1_HOST", "")
        assert ip, "BRIDGE_1_HOST not set"
        result = collect_wifi_metrics(ip)
        assert result.success, (
            f"collect_wifi_metrics failed on bridge-1 ({ip}): {result.error}. "
            "Is the bridge container deployed? Run molecule converge first."
        )
        assert "script_status" in result.data or result.data["interfaces"], (
            "No WiFi data from bridge-1. wifi_setup.sh wrapper or container may be missing."
        )

    def test_bridge_2_wifi_metrics(self, env):
        """collect_wifi_metrics against bridge-2 returns real data."""
        ip = env.get("BRIDGE_2_HOST", "")
        assert ip, "BRIDGE_2_HOST not set"
        result = collect_wifi_metrics(ip)
        assert result.success, (
            f"collect_wifi_metrics failed on bridge-2 ({ip}): {result.error}. "
            "Is the bridge container deployed? Run molecule converge first."
        )

    def test_bridge_1_bridge_metrics(self, env):
        """collect_bridge_metrics against bridge-1 returns bridge data."""
        ip = env.get("BRIDGE_1_HOST", "")
        assert ip, "BRIDGE_1_HOST not set"
        result = collect_bridge_metrics(ip)
        assert result.success, f"collect_bridge_metrics failed: {result.error}"
        assert "bridge" in result.data
        assert result.data["bridge"]["role"] in ("ap", "sta", "unknown", "unconfigured")

    def test_mesh_2_wifi_metrics(self, env):
        """collect_wifi_metrics against mesh2 returns real data."""
        ip = env.get("MESH_2_HOST", "")
        assert ip, "MESH_2_HOST not set"
        result = collect_wifi_metrics(ip)
        assert result.success, (
            f"collect_wifi_metrics failed on mesh2 ({ip}): {result.error}. "
            "Is the mesh container deployed? Run molecule converge first."
        )

    def test_mesh_2_mesh_metrics(self, env):
        """collect_mesh_metrics against mesh2 returns mesh data."""
        ip = env.get("MESH_2_HOST", "")
        assert ip, "MESH_2_HOST not set"
        result = collect_mesh_metrics(ip)
        assert result.success, f"collect_mesh_metrics failed on mesh2: {result.error}"
        assert result.data["role"] in ("ap", "sta", "unknown")


# ── Parser fixture data ──────────────────────────────────────────────
# Pure function tests — no SSH, no mocks, just string-in data-out.


IW_DEV_OUTPUT = """phy#0
\tInterface wlan0
\t\tifindex 3
\t\twdev 0x1
\t\taddr 00:11:22:33:44:55
\t\tssid MyBridge
\t\ttype AP
\t\tchannel 36 (5180 MHz), width: 80 MHz, center1: 5210 MHz
\t\ttxpower 20.00 dBm
"""

STATION_DUMP_OUTPUT = """=== wlan0 ===
Station aa:bb:cc:dd:ee:ff (on wlan0)
\tsignal:\t\t-52 dBm
\tsignal avg:\t-50 dBm
\ttx bitrate:\t866.7 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 2
\trx bitrate:\t400.0 MBit/s VHT-MCS 7 80MHz VHT-NSS 1
\trx bytes:\t1234567
\ttx bytes:\t7654321
\trx packets:\t1000
\ttx packets:\t2000
\ttx retries:\t50
\ttx failed:\t3
\tconnected time:\t3600 seconds
"""

UCI_WIRELESS_OUTPUT = """wireless.radio0=wifi-device
wireless.radio0.type='mac80211'
wireless.radio0.channel='36'
wireless.radio0.band='5g'
wireless.radio0.htmode='VHT80'
wireless.wds0=wifi-iface
wireless.wds0.mode='ap'
wireless.wds0.ssid='MyBridge'
wireless.wds0.wds='1'
"""

BRCTL_OUTPUT = """bridge name\tbridge id\t\tSTP enabled\tinterfaces
br-lan\t\t8000.001122334455\tyes\t\teth0
\t\t\t\t\t\t\twlan0
"""

DHCP_LEASES_OUTPUT = """1711111111 aa:bb:cc:dd:ee:ff 10.10.10.100 laptop *
1711111222 11:22:33:44:55:66 10.10.10.101 phone *
"""

SYSTEM_INFO_OUTPUT = """ 14:30:00 up  5 days, 12:30,  1 user,  load average: 0.10, 0.15, 0.12
MemTotal:       512000 kB
MemAvailable:   384000 kB
/dev/root        240M    128M    100M  57% /
"""


# ── Parser unit tests ────────────────────────────────────────────────


class TestParseKeyValue:
    def test_basic(self):
        result = _parse_key_value("PHY=phy0\nMODE=ap\nSSID=test\n")
        assert result["phy"] == "phy0"
        assert result["mode"] == "ap"
        assert result["ssid"] == "test"

    def test_skips_separator_lines(self):
        result = _parse_key_value("PHY=phy0\n---STATION_DUMP---\nIFACE=wlan0\n")
        assert result["phy"] == "phy0"
        assert result["iface"] == "wlan0"
        assert "---station_dump---" not in result

    def test_empty_input(self):
        assert _parse_key_value("") == {}

    def test_lowercase_keys(self):
        result = _parse_key_value("BAND=5g\nWIFI=up\n")
        assert result["band"] == "5g"
        assert result["wifi"] == "up"

    def test_values_with_equals(self):
        result = _parse_key_value("KEY=value=with=equals\n")
        assert result["key"] == "value=with=equals"


class TestParseIwDev:
    def test_single_interface(self):
        ifaces = _parse_iw_dev(IW_DEV_OUTPUT)
        assert len(ifaces) == 1
        assert ifaces[0]["name"] == "wlan0"
        assert ifaces[0]["type"] == "AP"
        assert ifaces[0]["addr"] == "00:11:22:33:44:55"

    def test_empty(self):
        assert _parse_iw_dev("") == []

    def test_multiple_interfaces(self):
        multi = IW_DEV_OUTPUT + "\n\tInterface wlan1\n\t\ttype managed\n"
        ifaces = _parse_iw_dev(multi)
        assert len(ifaces) == 2
        assert ifaces[1]["name"] == "wlan1"


class TestParseStationDump:
    def test_parse_station(self):
        stations = _parse_station_dump(STATION_DUMP_OUTPUT)
        assert len(stations) == 1
        sta = stations[0]
        assert sta["mac"] == "aa:bb:cc:dd:ee:ff"
        assert sta["signal"] == "-52"
        assert sta["signal_avg"] == "-50"
        assert sta["tx_packets"] == 2000
        assert sta["tx_retries"] == 50
        assert sta["tx_failed"] == 3

    def test_empty(self):
        assert _parse_station_dump("") == []


class TestParseUciWireless:
    def test_parse(self):
        result = _parse_uci_wireless(UCI_WIRELESS_OUTPUT)
        assert result["channel"] == "36"
        assert result["band"] == "5g"
        assert result["mode"] == "ap"

    def test_empty(self):
        assert _parse_uci_wireless("") == {}


class TestParseBrctl:
    def test_parse(self):
        bridges = _parse_brctl(BRCTL_OUTPUT)
        assert len(bridges) >= 1
        assert bridges[0]["name"] == "br-lan"

    def test_empty(self):
        assert _parse_brctl("") == []


class TestParseDhcpLeases:
    def test_parse(self):
        leases = _parse_dhcp_leases(DHCP_LEASES_OUTPUT)
        assert len(leases) == 2
        assert leases[0]["hostname"] == "laptop"
        assert leases[1]["ip"] == "10.10.10.101"

    def test_empty(self):
        assert _parse_dhcp_leases("") == []


class TestParseSystemInfo:
    def test_parse(self):
        info = _parse_system_info(SYSTEM_INFO_OUTPUT)
        assert "load" in info
        assert info["mem_total_kb"] == 512000
        assert info["mem_avail_kb"] == 384000
        assert info["disk_usage"] == "57"


# ── Signal quality helpers ───────────────────────────────────────────


class TestSignalQuality:
    @pytest.mark.parametrize("dbm,expected", [
        (-30, "excellent"),
        (-50, "excellent"),
        (-55, "good"),
        (-65, "fair"),
        (-75, "weak"),
        (-85, "poor"),
        (-95, "poor"),
    ])
    def test_quality(self, dbm, expected):
        assert signal_quality(dbm) == expected

    @pytest.mark.parametrize("dbm,expected", [
        (-30, 100),
        (-90, 0),
        (-60, 50),
    ])
    def test_percentage(self, dbm, expected):
        assert signal_percentage(dbm) == expected

    def test_percentage_clamp(self):
        assert signal_percentage(-100) == 0
        assert signal_percentage(-10) == 100


# ── Batman metrics ──────────────────────────────────────────────────


class TestBatmanOriginatorParsing:
    def test_parse_originator_line(self):
        output = (
            "[B.A.T.M.A.N. adv 2023.0]\n"
            "   Originator        last-seen (#/255) Nexthop           [   IF]\n"
            " * aa:bb:cc:dd:ee:ff    0.904s   (254) cc:dd:ee:ff:00:11 [  wlan0]\n"
            " * 11:22:33:44:55:66    1.200s   (200) 77:88:99:aa:bb:cc [  wlan0]\n"
        )
        from scripts.webui.heartbeat import _parse_batman_originators
        result = _parse_batman_originators(output)
        assert len(result) == 2
        assert result[0]["mac"] == "aa:bb:cc:dd:ee:ff"
        assert result[0]["tq"] == 254
        assert result[0]["last_seen"] == "0.904"
        assert result[0]["next_hop"] == "cc:dd:ee:ff:00:11"
        assert result[0]["interface"] == "wlan0"
        assert result[1]["mac"] == "11:22:33:44:55:66"
        assert result[1]["tq"] == 200

    def test_parse_empty_output(self):
        from scripts.webui.heartbeat import _parse_batman_originators
        assert _parse_batman_originators("") == []
        assert _parse_batman_originators("No batman nodes in range") == []

    def test_parse_interfaces(self):
        output = "wlan0: active\nbat0: active\n"
        from scripts.webui.heartbeat import _parse_batman_interfaces
        result = _parse_batman_interfaces(output)
        assert len(result) == 2
        assert result[0]["name"] == "wlan0"
        assert result[0]["status"] == "active"

    def test_parse_interfaces_empty(self):
        from scripts.webui.heartbeat import _parse_batman_interfaces
        assert _parse_batman_interfaces("") == []


class TestParseGuestList:
    """Pure unit tests for parse_guest_list — no infrastructure needed."""

    def test_normal_output(self):
        from scripts.webui.heartbeat import parse_guest_list
        output = (
            "VMID       Status     Name\n"
            "101        running    wireguard\n"
            "102        running    pihole\n"
            "401        stopped    kiosk\n"
        )
        result = parse_guest_list(output)
        assert len(result) == 3
        assert result[0] == {"vmid": "101", "status": "running", "name": "wireguard"}
        assert result[2] == {"vmid": "401", "status": "stopped", "name": "kiosk"}

    def test_empty_string(self):
        from scripts.webui.heartbeat import parse_guest_list
        assert parse_guest_list("") == []

    def test_header_only(self):
        from scripts.webui.heartbeat import parse_guest_list
        assert parse_guest_list("VMID       Status     Name\n") == []

    def test_partial_lines_skipped(self):
        from scripts.webui.heartbeat import parse_guest_list
        output = (
            "VMID       Status     Name\n"
            "101        running    wireguard\n"
            "bad\n"
            "200        running    homeassistant\n"
        )
        result = parse_guest_list(output)
        assert len(result) == 2
        assert result[0]["vmid"] == "101"
        assert result[1]["vmid"] == "200"

    def test_qm_list_format(self):
        """qm list has VMID NAME STATUS columns (different order from pct)."""
        from scripts.webui.heartbeat import parse_guest_list
        output = (
            "      VMID NAME                 STATUS     MEM(MB)    BOOTDISK(GB) PID\n"
            "       100 openwrt-router       stopped    512                0.50 0\n"
            "       400 desktop              running    4096              32.00 194969\n"
        )
        result = parse_guest_list(output)
        assert len(result) == 2
        assert result[0] == {"vmid": "100", "name": "openwrt-router", "status": "stopped"}
        assert result[1] == {"vmid": "400", "name": "desktop", "status": "running"}

    def test_pct_list_with_lock_column(self):
        """pct list has an optional Lock column between Status and Name."""
        from scripts.webui.heartbeat import parse_guest_list
        output = (
            "VMID       Status     Lock         Name\n"
            "101        running                 wireguard\n"
            "102        running                 pihole\n"
        )
        result = parse_guest_list(output)
        assert len(result) == 2
        assert result[0] == {"vmid": "101", "status": "running", "name": "wireguard"}
        assert result[1] == {"vmid": "102", "status": "running", "name": "pihole"}


@pytest.mark.integration
class TestCollectBatmanMetricsReal:
    """Batman collector tests against real hardware.

    These tests require bridge containers to be provisioned (wrapper scripts
    are deployed by openwrt_bridge_lxc and removed during molecule cleanup).
    """

    @pytest.fixture()
    def env(self, test_env):
        return test_env

    @staticmethod
    def _wrapper_exists(ip: str) -> bool:
        """Check if batman_trigger.sh exists inside the bridge container."""
        ok, _ = _ssh_exec(
            ip, "pct exec 104 -- test -x /usr/sbin/batman_trigger.sh", timeout=5,
        )
        return ok

    def test_batman_status_from_bridge_1(self, env):
        """Batman status is queryable on bridge-1."""
        ip = env.get("BRIDGE_1_HOST", "")
        assert ip, "BRIDGE_1_HOST not set"
        if not self._wrapper_exists(ip):
            pytest.skip(
                "batman_trigger.sh wrapper not deployed on bridge-1 "
                "(run molecule converge to provision bridge containers)"
            )
        result = collect_batman_metrics(ip)
        assert result.success, (
            f"collect_batman_metrics failed on bridge-1 ({ip}): {result.error}. "
            "batman_trigger.sh wrapper may be missing on the Proxmox host."
        )
        assert "active" in result.data
        assert isinstance(result.data["active"], bool)

    def test_batman_status_from_bridge_2(self, env):
        """Batman status is queryable on bridge-2."""
        ip = env.get("BRIDGE_2_HOST", "")
        assert ip, "BRIDGE_2_HOST not set"
        if not self._wrapper_exists(ip):
            pytest.skip(
                "batman_trigger.sh wrapper not deployed on bridge-2 "
                "(run molecule converge to provision bridge containers)"
            )
        result = collect_batman_metrics(ip)
        assert result.success, f"collect_batman_metrics failed on bridge-2: {result.error}"
        assert "active" in result.data
