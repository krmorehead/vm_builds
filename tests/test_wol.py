"""Tests for scripts/wol.sh — WoL host inclusion safety.

Validates that hosts which cannot be recovered via Wake-on-LAN
(e.g., USB-only ethernet) are never included in the WoL script.

Run with: pytest tests/test_wol.py -v
"""

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WOL_SCRIPT = REPO_ROOT / "scripts" / "wol.sh"
HOSTS_FILE = REPO_ROOT / "inventory" / "hosts.yml"
HOST_VARS_DIR = REPO_ROOT / "inventory" / "host_vars"


def _parse_wol_hosts() -> set[str]:
    """Extract host aliases from HOST_MAC and LAN_HOST_MAC in wol.sh."""
    content = WOL_SCRIPT.read_text()
    hosts = set()
    for match in re.finditer(r'\[(\w+)\]="[0-9a-fA-F:]{17}"', content):
        hosts.add(match.group(1))
    return hosts


def _get_all_inventory_hosts() -> list[str]:
    """Get all host names from the inventory."""
    data = yaml.safe_load(HOSTS_FILE.read_text())
    hosts = set()
    proxmox = data.get("all", {}).get("children", {}).get("proxmox", {})
    for group in proxmox.get("children", {}).values():
        for host in group.get("hosts", {}) or {}:
            hosts.add(host)
    return sorted(hosts)


def _get_host_var(hostname: str, var: str):
    """Read a single variable from a host's host_vars file."""
    host_file = HOST_VARS_DIR / f"{hostname}.yml"
    if not host_file.exists():
        return None
    data = yaml.safe_load(host_file.read_text())
    return data.get(var)


class TestWolHostExclusion:
    """Non-WoL hosts must never appear in wol.sh."""

    def test_wol_script_exists(self):
        assert WOL_SCRIPT.exists(), "scripts/wol.sh not found"

    def test_every_host_has_wol_capable_var(self):
        """Every inventory host must declare wol_capable."""
        for host in _get_all_inventory_hosts():
            val = _get_host_var(host, "wol_capable")
            assert val is not None, (
                f"{host} is missing wol_capable in host_vars. "
                "Every host MUST declare wol_capable (true/false)."
            )

    def test_non_wol_hosts_excluded_from_script(self):
        """Hosts with wol_capable=false must not appear in wol.sh."""
        wol_hosts = _parse_wol_hosts()
        for host in _get_all_inventory_hosts():
            if not _get_host_var(host, "wol_capable"):
                assert host not in wol_hosts, (
                    f"{host} has wol_capable=false but appears in "
                    "scripts/wol.sh HOST_MAC or LAN_HOST_MAC. "
                    "Non-WoL hosts cannot be recovered if shut down."
                )

    def test_ai_not_in_wol_script(self):
        """ai uses USB ethernet — hard check it's excluded."""
        wol_hosts = _parse_wol_hosts()
        assert "ai" not in wol_hosts, (
            "ai must NOT be in wol.sh. It uses USB ethernet which "
            "does not support Wake-on-LAN. Including it masks a "
            "fundamental recoverability gap."
        )

    def test_wol_capable_hosts_are_in_script(self):
        """Hosts with wol_capable=true should be in wol.sh."""
        wol_hosts = _parse_wol_hosts()
        for host in _get_all_inventory_hosts():
            if _get_host_var(host, "wol_capable"):
                assert host in wol_hosts, (
                    f"{host} has wol_capable=true but is not in "
                    "scripts/wol.sh. WoL-capable hosts should be "
                    "registered for remote recovery."
                )

    def test_parsed_hosts_non_empty(self):
        """Guard against regex drift silently returning an empty set.

        If _parse_wol_hosts() finds zero hosts, the exclusion tests become
        vacuously true and stop catching real problems.
        """
        wol_hosts = _parse_wol_hosts()
        assert len(wol_hosts) > 0, (
            "_parse_wol_hosts() returned an empty set. Either wol.sh "
            "has no HOST_MAC/LAN_HOST_MAC entries or the regex in "
            "_parse_wol_hosts() no longer matches the file format."
        )
