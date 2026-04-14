"""Host state model and persistence for the Manager source-of-truth system.

Each physical Proxmox host has a HostState that tracks hardware inventory,
bridge topology, and container assignments. The HostStateStore persists
these to disk and is composed into BaseManager alongside SubscriptionManager
and MetricCache.

No framework imports — pure data + file I/O.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Value objects ─────────────────────────────────────────────────────


@dataclass
class PciDevice:
    """A single PCI device on a Proxmox host."""

    bdf: str
    device_type: str
    vendor_device: str
    driver: str
    assigned_to: int | None = None
    iommu_group: int | None = None


@dataclass
class WifiPhy:
    """A single WiFi PHY, possibly namespace-moved into a container."""

    name: str
    pci_device: str = ""
    namespace: str = "host"
    driver: str = ""


@dataclass
class IgpuInfo:
    """Integrated GPU state on a Proxmox host."""

    vendor: str
    driver: str
    pci_address: str
    render_device: str
    render_gid: int
    video_gid: int


@dataclass
class BridgeInfo:
    """A single Proxmox network bridge."""

    name: str
    role: str
    physical_nics: list[str] = field(default_factory=list)
    has_carrier: bool = True


@dataclass
class ContainerInfo:
    """A single LXC container or VM on a Proxmox host."""

    vmid: int
    service_type: str
    hostname: str
    state: str
    ip: str
    bridge: str
    hardware: list[str] = field(default_factory=list)
    last_deployed: str = ""
    image_version: str = ""


# ── Composed sub-models ──────────────────────────────────────────────


@dataclass
class HardwareInventory:
    """Immutable between reboots. Detected by infrastructure roles."""

    pci_devices: list[PciDevice] = field(default_factory=list)
    wifi_phys: list[WifiPhy] = field(default_factory=list)
    igpu: IgpuInfo | None = None


@dataclass
class BridgeTopology:
    """Bridge layout on a host. Changes when proxmox_bridges runs."""

    bridges: list[BridgeInfo] = field(default_factory=list)
    wan_bridge: str = ""
    container_bridge: str = ""


# ── Root state object ────────────────────────────────────────────────


@dataclass
class HostState:
    """Complete state for one physical Proxmox host.

    Composed from HardwareInventory, BridgeTopology, and a container
    registry. Query methods live here so callers don't reach into
    sub-models.
    """

    hostname: str
    ip: str
    wol_capable: bool
    last_updated: str
    hardware: HardwareInventory = field(default_factory=HardwareInventory)
    bridges: BridgeTopology = field(default_factory=BridgeTopology)
    containers: dict[int, ContainerInfo] = field(default_factory=dict)
    mesh_established: bool = False

    # ── Query methods ─────────────────────────────────────────────

    def phy_for_container(self, vmid: int) -> list[WifiPhy]:
        """Return WiFi PHYs that have been namespace-moved into a container."""
        return [
            p for p in self.hardware.wifi_phys
            if p.namespace == f"container:{vmid}"
        ]

    def container_exists(self, vmid: int) -> bool:
        return vmid in self.containers

    def container_running(self, vmid: int) -> bool:
        ct = self.containers.get(vmid)
        return ct is not None and ct.state == "running"

    def wifi_phys_on_host(self) -> list[WifiPhy]:
        """PHYs still in the host namespace (not moved into a container)."""
        return [p for p in self.hardware.wifi_phys if p.namespace == "host"]

    def wifi_phys_in_containers(self) -> list[WifiPhy]:
        """PHYs that have been namespace-moved into any container."""
        return [
            p for p in self.hardware.wifi_phys
            if p.namespace.startswith("container:")
        ]

    def image_versions(self) -> dict[str, str]:
        """Return {service_type: version} for all containers with known versions."""
        return {
            ct.service_type: ct.image_version
            for ct in self.containers.values()
            if ct.image_version
        }

    # ── Serialization ─────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["containers"] = {
            str(k): v for k, v in raw["containers"].items()
        }
        return raw

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HostState:
        hw_raw = data.get("hardware", {})
        hw = HardwareInventory(
            pci_devices=[PciDevice(**d) for d in hw_raw.get("pci_devices", [])],
            wifi_phys=[WifiPhy(**d) for d in hw_raw.get("wifi_phys", [])],
            igpu=IgpuInfo(**hw_raw["igpu"]) if hw_raw.get("igpu") else None,
        )
        br_raw = data.get("bridges", {})
        br = BridgeTopology(
            bridges=[BridgeInfo(**d) for d in br_raw.get("bridges", [])],
            wan_bridge=br_raw.get("wan_bridge", ""),
            container_bridge=br_raw.get("container_bridge", ""),
        )
        containers: dict[int, ContainerInfo] = {}
        for k, v in data.get("containers", {}).items():
            containers[int(k)] = ContainerInfo(**v)

        return cls(
            hostname=data["hostname"],
            ip=data["ip"],
            wol_capable=data["wol_capable"],
            last_updated=data["last_updated"],
            hardware=hw,
            bridges=br,
            containers=containers,
            mesh_established=data.get("mesh_established", False),
        )


# ── Persistence component ────────────────────────────────────────────


class HostStateStore:
    """Load/save HostState to disk.

    Same composition pattern as SubscriptionManager and MetricCache.
    Composed into BaseManager. Thread-safe via a lock and atomic writes.
    Compound read-modify-write operations hold the lock for the full
    cycle to prevent lost updates.
    """

    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir / "host_states"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _read(self, hostname: str) -> HostState | None:
        """Read without locking — caller MUST hold self._lock."""
        path = self._dir / f"{hostname}.json"
        if not path.exists():
            return None
        return HostState.from_dict(json.loads(path.read_text()))

    def _write(self, state: HostState) -> None:
        """Write atomically without locking — caller MUST hold self._lock."""
        path = self._dir / f"{state.hostname}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state.to_dict(), indent=2) + "\n")
        tmp.replace(path)

    def _stamp(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def get(self, hostname: str) -> HostState | None:
        with self._lock:
            return self._read(hostname)

    def save(self, state: HostState) -> None:
        with self._lock:
            self._write(state)

    def delete(self, hostname: str) -> None:
        path = self._dir / f"{hostname}.json"
        with self._lock:
            path.unlink(missing_ok=True)

    def list_hosts(self) -> list[str]:
        return sorted(p.stem for p in self._dir.glob("*.json"))

    def get_or_create(self, hostname: str, ip: str, wol_capable: bool = True) -> HostState:
        """Return existing state or create a minimal empty one."""
        with self._lock:
            existing = self._read(hostname)
            if existing is not None:
                return existing
            state = HostState(
                hostname=hostname,
                ip=ip,
                wol_capable=wol_capable,
                last_updated=self._stamp(),
            )
            self._write(state)
            return state

    def update_hardware(self, hostname: str, hardware_data: dict[str, Any]) -> HostState | None:
        """Merge hardware inventory into an existing host state."""
        with self._lock:
            state = self._read(hostname)
            if state is None:
                return None
            if "pci_devices" in hardware_data:
                state.hardware.pci_devices = [
                    PciDevice(**d) for d in hardware_data["pci_devices"]
                ]
            if "wifi_phys" in hardware_data:
                state.hardware.wifi_phys = [
                    WifiPhy(**d) for d in hardware_data["wifi_phys"]
                ]
            if "igpu" in hardware_data:
                igpu_raw = hardware_data["igpu"]
                state.hardware.igpu = IgpuInfo(**igpu_raw) if igpu_raw else None
            state.last_updated = self._stamp()
            self._write(state)
            return state

    def update_bridges(self, hostname: str, bridge_data: dict[str, Any]) -> HostState | None:
        """Merge bridge topology into an existing host state."""
        with self._lock:
            state = self._read(hostname)
            if state is None:
                return None
            if "bridges" in bridge_data:
                state.bridges.bridges = [
                    BridgeInfo(**d) for d in bridge_data["bridges"]
                ]
            if "wan_bridge" in bridge_data:
                state.bridges.wan_bridge = bridge_data["wan_bridge"]
            if "container_bridge" in bridge_data:
                state.bridges.container_bridge = bridge_data["container_bridge"]
            state.last_updated = self._stamp()
            self._write(state)
            return state

    def register_container(
        self, hostname: str, vmid: int, container_data: dict[str, Any],
    ) -> HostState | None:
        """Add or update a container in the host's registry."""
        with self._lock:
            state = self._read(hostname)
            if state is None:
                return None
            state.containers[vmid] = ContainerInfo(
                vmid=vmid,
                service_type=container_data.get("service_type", ""),
                hostname=container_data.get("hostname", ""),
                state=container_data.get("state", "running"),
                ip=container_data.get("ip", ""),
                bridge=container_data.get("bridge", ""),
                hardware=container_data.get("hardware", []),
                last_deployed=container_data.get("last_deployed", ""),
            )
            state.last_updated = self._stamp()
            self._write(state)
            return state

    def deregister_container(self, hostname: str, vmid: int) -> HostState | None:
        """Remove a container from the host's registry."""
        with self._lock:
            state = self._read(hostname)
            if state is None:
                return None
            state.containers.pop(vmid, None)
            state.last_updated = self._stamp()
            self._write(state)
            return state

    def update_phy_namespace(
        self, hostname: str, phy_name: str, namespace: str,
    ) -> HostState | None:
        """Update which namespace a WiFi PHY lives in (upsert)."""
        with self._lock:
            state = self._read(hostname)
            if state is None:
                return None
            for phy in state.hardware.wifi_phys:
                if phy.name == phy_name:
                    phy.namespace = namespace
                    break
            else:
                state.hardware.wifi_phys.append(
                    WifiPhy(name=phy_name, namespace=namespace),
                )
            state.last_updated = self._stamp()
            self._write(state)
            return state
