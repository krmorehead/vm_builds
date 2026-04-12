"""Tests for host_state.py — pure Python, no NiceGUI or Ansible required.

Covers: dataclass serialization, HostState query methods, HostStateStore CRUD,
and atomic write safety.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from scripts.webui.host_state import (
    BridgeInfo,
    BridgeTopology,
    ContainerInfo,
    HardwareInventory,
    HostState,
    HostStateStore,
    IgpuInfo,
    PciDevice,
    WifiPhy,
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def store(state_dir: Path) -> HostStateStore:
    return HostStateStore(state_dir)


def _make_phy(name: str = "phy0", namespace: str = "host") -> WifiPhy:
    return WifiPhy(name=name, pci_device="02:00.0", namespace=namespace, driver="iwlwifi")


def _make_pci(bdf: str = "02:00.0", device_type: str = "wifi") -> PciDevice:
    return PciDevice(
        bdf=bdf, device_type=device_type, vendor_device="8086:2725",
        driver="iwlwifi", assigned_to=None, iommu_group=1,
    )


def _make_igpu() -> IgpuInfo:
    return IgpuInfo(
        vendor="intel", driver="i915", pci_address="00:02.0",
        render_device="/dev/dri/renderD128", render_gid=104, video_gid=44,
    )


def _make_bridge(name: str = "vmbr0", role: str = "wan") -> BridgeInfo:
    return BridgeInfo(name=name, role=role, physical_nics=["enp1s0"], has_carrier=True)


def _make_container(vmid: int = 102, service_type: str = "pihole") -> ContainerInfo:
    return ContainerInfo(
        vmid=vmid, service_type=service_type, hostname=service_type,
        state="running", ip="10.10.10.10", bridge="vmbr1",
        hardware=[], last_deployed="2026-04-10T12:00:00Z",
    )


def _make_host_state(hostname: str = "home") -> HostState:
    return HostState(
        hostname=hostname,
        ip="192.168.86.201",
        wol_capable=True,
        last_updated="2026-04-10T12:00:00Z",
        hardware=HardwareInventory(
            pci_devices=[_make_pci()],
            wifi_phys=[_make_phy(), _make_phy("phy1", "container:103")],
            igpu=_make_igpu(),
        ),
        bridges=BridgeTopology(
            bridges=[_make_bridge("vmbr0", "wan"), _make_bridge("vmbr1", "lan")],
            wan_bridge="vmbr0",
            container_bridge="vmbr_ct",
        ),
        containers={
            102: _make_container(102, "pihole"),
            103: _make_container(103, "openwrt_mesh"),
        },
        mesh_established=True,
    )


# ── Dataclass serialization round-trip ───────────────────────────────


class TestSerialization:
    def test_pci_device_round_trip(self) -> None:
        original = _make_pci()
        rebuilt = PciDevice(**json.loads(json.dumps(original.__dict__)))
        assert rebuilt == original

    def test_wifi_phy_round_trip(self) -> None:
        original = _make_phy()
        rebuilt = WifiPhy(**json.loads(json.dumps(original.__dict__)))
        assert rebuilt == original

    def test_igpu_round_trip(self) -> None:
        original = _make_igpu()
        rebuilt = IgpuInfo(**json.loads(json.dumps(original.__dict__)))
        assert rebuilt == original

    def test_bridge_round_trip(self) -> None:
        original = _make_bridge()
        rebuilt = BridgeInfo(**json.loads(json.dumps(original.__dict__)))
        assert rebuilt == original

    def test_container_round_trip(self) -> None:
        original = _make_container()
        rebuilt = ContainerInfo(**json.loads(json.dumps(original.__dict__)))
        assert rebuilt == original

    def test_host_state_full_round_trip(self) -> None:
        original = _make_host_state()
        raw = original.to_dict()
        json_str = json.dumps(raw)
        rebuilt = HostState.from_dict(json.loads(json_str))
        assert rebuilt.hostname == original.hostname
        assert rebuilt.ip == original.ip
        assert rebuilt.wol_capable == original.wol_capable
        assert rebuilt.mesh_established == original.mesh_established
        assert len(rebuilt.hardware.pci_devices) == len(original.hardware.pci_devices)
        assert len(rebuilt.hardware.wifi_phys) == len(original.hardware.wifi_phys)
        assert rebuilt.hardware.igpu == original.hardware.igpu
        assert len(rebuilt.bridges.bridges) == len(original.bridges.bridges)
        assert rebuilt.bridges.wan_bridge == original.bridges.wan_bridge
        assert len(rebuilt.containers) == len(original.containers)
        assert 102 in rebuilt.containers
        assert 103 in rebuilt.containers

    def test_host_state_without_igpu(self) -> None:
        state = _make_host_state()
        state.hardware.igpu = None
        raw = state.to_dict()
        rebuilt = HostState.from_dict(raw)
        assert rebuilt.hardware.igpu is None

    def test_host_state_empty_containers(self) -> None:
        state = _make_host_state()
        state.containers = {}
        raw = state.to_dict()
        rebuilt = HostState.from_dict(raw)
        assert rebuilt.containers == {}

    def test_container_keys_are_ints_after_round_trip(self) -> None:
        state = _make_host_state()
        raw = state.to_dict()
        assert "102" in raw["containers"]
        rebuilt = HostState.from_dict(raw)
        assert 102 in rebuilt.containers
        assert isinstance(list(rebuilt.containers.keys())[0], int)


# ── HostState query methods ──────────────────────────────────────────


class TestHostStateQueries:
    def test_phy_for_container_finds_moved_phy(self) -> None:
        state = _make_host_state()
        phys = state.phy_for_container(103)
        assert len(phys) == 1
        assert phys[0].name == "phy1"

    def test_phy_for_container_returns_empty_for_unknown(self) -> None:
        state = _make_host_state()
        assert state.phy_for_container(999) == []

    def test_container_exists(self) -> None:
        state = _make_host_state()
        assert state.container_exists(102) is True
        assert state.container_exists(999) is False

    def test_container_running(self) -> None:
        state = _make_host_state()
        assert state.container_running(102) is True
        state.containers[102].state = "stopped"
        assert state.container_running(102) is False
        assert state.container_running(999) is False

    def test_wifi_phys_on_host(self) -> None:
        state = _make_host_state()
        on_host = state.wifi_phys_on_host()
        assert len(on_host) == 1
        assert on_host[0].name == "phy0"

    def test_wifi_phys_in_containers(self) -> None:
        state = _make_host_state()
        in_ct = state.wifi_phys_in_containers()
        assert len(in_ct) == 1
        assert in_ct[0].name == "phy1"
        assert in_ct[0].namespace == "container:103"

    def test_all_phys_on_host_when_none_moved(self) -> None:
        state = _make_host_state()
        state.hardware.wifi_phys = [_make_phy("phy0", "host"), _make_phy("phy1", "host")]
        assert len(state.wifi_phys_on_host()) == 2
        assert len(state.wifi_phys_in_containers()) == 0


# ── HostStateStore CRUD ──────────────────────────────────────────────


class TestHostStateStore:
    def test_get_unknown_returns_none(self, store: HostStateStore) -> None:
        assert store.get("nonexistent") is None

    def test_save_and_get(self, store: HostStateStore) -> None:
        state = _make_host_state()
        store.save(state)
        loaded = store.get("home")
        assert loaded is not None
        assert loaded.hostname == "home"
        assert loaded.ip == "192.168.86.201"
        assert len(loaded.containers) == 2

    def test_delete(self, store: HostStateStore) -> None:
        state = _make_host_state()
        store.save(state)
        assert store.get("home") is not None
        store.delete("home")
        assert store.get("home") is None

    def test_delete_nonexistent_is_safe(self, store: HostStateStore) -> None:
        store.delete("ghost")

    def test_list_hosts(self, store: HostStateStore) -> None:
        assert store.list_hosts() == []
        store.save(_make_host_state("alpha"))
        store.save(_make_host_state("beta"))
        assert store.list_hosts() == ["alpha", "beta"]

    def test_get_or_create_new(self, store: HostStateStore) -> None:
        state = store.get_or_create("new-host", "10.0.0.1", wol_capable=False)
        assert state.hostname == "new-host"
        assert state.ip == "10.0.0.1"
        assert state.wol_capable is False
        assert store.get("new-host") is not None

    def test_get_or_create_existing(self, store: HostStateStore) -> None:
        original = _make_host_state()
        store.save(original)
        returned = store.get_or_create("home", "0.0.0.0")
        assert returned.ip == "192.168.86.201"

    def test_update_hardware(self, store: HostStateStore) -> None:
        store.save(_make_host_state())
        result = store.update_hardware("home", {
            "pci_devices": [
                {"bdf": "03:00.0", "device_type": "gpu", "vendor_device": "8086:1234",
                 "driver": "i915", "assigned_to": None, "iommu_group": 0},
            ],
            "wifi_phys": [
                {"name": "phy0", "pci_device": "02:00.0", "namespace": "host", "driver": "iwlwifi"},
            ],
        })
        assert result is not None
        assert len(result.hardware.pci_devices) == 1
        assert result.hardware.pci_devices[0].bdf == "03:00.0"
        assert len(result.hardware.wifi_phys) == 1

    def test_update_hardware_unknown_host(self, store: HostStateStore) -> None:
        assert store.update_hardware("ghost", {"pci_devices": []}) is None

    def test_update_bridges(self, store: HostStateStore) -> None:
        store.save(_make_host_state())
        result = store.update_bridges("home", {
            "bridges": [
                {"name": "vmbr0", "role": "wan", "physical_nics": ["eth0"], "has_carrier": True},
            ],
            "wan_bridge": "vmbr0",
            "container_bridge": "vmbr_ct",
        })
        assert result is not None
        assert len(result.bridges.bridges) == 1

    def test_register_container(self, store: HostStateStore) -> None:
        store.save(_make_host_state())
        result = store.register_container("home", 500, {
            "service_type": "netdata",
            "hostname": "netdata",
            "state": "running",
            "ip": "10.10.10.40",
            "bridge": "vmbr1",
        })
        assert result is not None
        assert 500 in result.containers
        assert result.containers[500].service_type == "netdata"

    def test_deregister_container(self, store: HostStateStore) -> None:
        store.save(_make_host_state())
        result = store.deregister_container("home", 102)
        assert result is not None
        assert 102 not in result.containers
        assert 103 in result.containers

    def test_deregister_nonexistent_container(self, store: HostStateStore) -> None:
        store.save(_make_host_state())
        result = store.deregister_container("home", 999)
        assert result is not None
        assert len(result.containers) == 2

    def test_update_phy_namespace(self, store: HostStateStore) -> None:
        store.save(_make_host_state())
        result = store.update_phy_namespace("home", "phy0", "container:104")
        assert result is not None
        phy0 = next(p for p in result.hardware.wifi_phys if p.name == "phy0")
        assert phy0.namespace == "container:104"

    def test_update_phy_namespace_upserts_unknown_phy(self, store: HostStateStore) -> None:
        store.save(_make_host_state())
        result = store.update_phy_namespace("home", "phy35", "container:104")
        assert result is not None
        phy35 = next(p for p in result.hardware.wifi_phys if p.name == "phy35")
        assert phy35.namespace == "container:104"

    def test_update_phy_namespace_unknown_host(self, store: HostStateStore) -> None:
        assert store.update_phy_namespace("ghost", "phy0", "host") is None


# ── Concurrent write safety ──────────────────────────────────────────


class TestConcurrency:
    def test_concurrent_saves_dont_corrupt(self, store: HostStateStore) -> None:
        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                state = _make_host_state("home")
                state.ip = f"10.0.0.{i}"
                store.save(state)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        loaded = store.get("home")
        assert loaded is not None
        assert loaded.hostname == "home"

    def test_concurrent_reads_during_write(self, store: HostStateStore) -> None:
        store.save(_make_host_state())
        results: list[HostState | None] = []
        errors: list[Exception] = []

        def reader() -> None:
            try:
                results.append(store.get("home"))
            except Exception as exc:
                errors.append(exc)

        def writer() -> None:
            try:
                state = _make_host_state()
                state.ip = "10.0.0.99"
                store.save(state)
            except Exception as exc:
                errors.append(exc)

        threads = []
        for _ in range(10):
            threads.append(threading.Thread(target=reader))
            threads.append(threading.Thread(target=writer))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert all(r is not None for r in results)
