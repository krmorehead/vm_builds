"""Tests for jellyfin_lxc role.

Run with: pytest tests/test_jellyfin_lxc_role.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestJellyfinLxcRole:
    """Test jellyfin_lxc role structure and variables."""

    def test_role_directory_exists(self):
        """Test that jellyfin_lxc role directory structure exists."""
        role_path = Path(__file__).parent.parent / "roles" / "jellyfin_lxc"

        assert role_path.exists(), "jellyfin_lxc role directory should exist"
        assert (role_path / "defaults" / "main.yml").exists(), "defaults/main.yml should exist"
        assert (role_path / "tasks" / "main.yml").exists(), "tasks/main.yml should exist"
        assert (role_path / "meta" / "main.yml").exists(), "meta/main.yml should exist"

    def test_defaults_main_variables(self):
        """Test that defaults/main.yml contains required variables."""
        defaults_file = Path(__file__).parent.parent / "roles" / "jellyfin_lxc" / "defaults" / "main.yml"

        content = defaults_file.read_text()

        # Check for required variables
        assert "jellyfin_ct_hostname: jellyfin" in content, "jellyfin_ct_hostname should be defined"
        assert "jellyfin_ct_memory: 2048" in content, "jellyfin_ct_memory should be 2048"
        assert "jellyfin_ct_cores: 2" in content, "jellyfin_ct_cores should be 2"
        assert "jellyfin_ct_disk: \"8\"" in content, "jellyfin_ct_disk should be 8"
        assert "jellyfin_ct_template: \"{{ jellyfin_lxc_template }}\"" in content, "jellyfin_ct_template should reference variable"
        assert "jellyfin_ct_onboot: true" in content, "jellyfin_ct_onboot should be true"
        assert "jellyfin_ct_startup_order: 5" in content, "jellyfin_ct_startup_order should be 5"
        assert "jellyfin_ct_ip_offset: \"{{ jellyfin_ct_ip_offset | default(15) }}\"" in content, "jellyfin_ct_ip_offset should default to 15"
        assert "jellyfin_media_path: \"{{ jellyfin_media_path }}\"" in content, "jellyfin_media_path should reference variable"

    def test_tasks_main_structure(self):
        """Test that tasks/main.yml contains expected structure."""
        tasks_file = Path(__file__).parent.parent / "roles" / "jellyfin_lxc" / "tasks" / "main.yml"

        content = tasks_file.read_text()

        # Check for required sections
        assert "# ── Verify Jellyfin template exists" in content, "Should have template verification section"
        assert "# ── Determine container network" in content, "Should have network determination section"
        assert "# ── Build device and media mount entries" in content, "Should have mount entries section"
        assert "# ── Provision via shared proxmox_lxc role" in content, "Should have provisioning section"

        # Check for key tasks
        assert "ansible.builtin.stat" in content, "Should check template file exists"
        assert "ansible.builtin.fail" in content, "Should fail if template missing"
        assert "ansible.builtin.set_fact" in content, "Should set computed facts"
        assert "ansible.builtin.include_role" in content, "Should include proxmox_lxc role"
        assert "proxmox_lxc" in content, "Should include proxmox_lxc role"

        # Check for expected variables
        assert "lxc_ct_id: \"{{ jellyfin_ct_id }}\"" in content, "Should pass container ID"
        assert "lxc_ct_hostname: \"{{ jellyfin_ct_hostname }}\"" in content, "Should pass hostname"
        assert "lxc_ct_dynamic_group: jellyfin" in content, "Should set dynamic group to jellyfin"
        assert "lxc_ct_mount_entries: \"{{ _jellyfin_mount_entries }}\"" in content, "Should pass mount entries"

    def test_meta_main_structure(self):
        """Test that meta/main.yml contains required metadata."""
        meta_file = Path(__file__).parent.parent / "roles" / "jellyfin_lxc" / "meta" / "main.yml"

        content = meta_file.read_text()

        # Check for required metadata
        assert "role_name: jellyfin_lxc" in content, "Should define role name"
        assert "description:" in content, "Should have description"
        assert "min_ansible_version: \"2.15\"" in content, "Should specify minimum Ansible version"
        assert "platforms:" in content, "Should specify supported platforms"
        assert "Debian" in content, "Should support Debian platform"
        assert "bookworm" in content, "Should support Debian bookworm"

    def test_siteyml_integration(self):
        """Test that site.yml includes jellyfin_lxc provision and configure plays."""
        site_yml = Path(__file__).parent.parent / "playbooks" / "site.yml"

        content = site_yml.read_text()

        # Check for provision play
        assert "Provision Jellyfin LXC" in content, "Should have Jellyfin provision play"
        assert "hosts: media_nodes" in content, "Provision play should target media_nodes"
        assert "tags: [media]" in content, "Provision play should be tagged [media]"
        assert "jellyfin_lxc" in content, "Provision play should include jellyfin_lxc role"

        # Check for configure play (targets media_nodes, not dynamic group,
        # because jellyfin_configure needs host-side igpu facts via pct exec)
        assert "Configure Jellyfin" in content, "Should have Jellyfin configure play"
        assert "jellyfin_configure" in content, "Configure play should reference jellyfin_configure role"

        # Check for deploy_stamp
        assert "deploy_stamp_play: jellyfin_lxc" in content, "Should include deploy_stamp for jellyfin_lxc"

    def test_group_reconstruction_file(self):
        """Test that reconstruct_jellyfin_group.yml exists and has proper structure."""
        reconstruct_file = Path(__file__).parent.parent / "tasks" / "reconstruct_jellyfin_group.yml"

        assert reconstruct_file.exists(), "reconstruct_jellyfin_group.yml should exist"

        content = reconstruct_file.read_text()

        # Check for required sections
        assert "# Reusable task file: reconstruct the jellyfin dynamic group" in content, "Should have proper header"
        assert "Verify Jellyfin container is running" in content, "Should verify container status"
        assert "Add Jellyfin containers to dynamic group" in content, "Should add hosts to dynamic group"

        # Check for key tasks
        assert "pct status" in content, "Should use pct status to check container"
        assert "ansible.builtin.add_host" in content, "Should use add_host to populate dynamic group"
        assert "community.proxmox.proxmox_pct_remote" in content, "Should use pct_remote connection"
        assert "proxmox_vmid: \"{{ jellyfin_ct_id | default(300) }}\"" in content, "Should pass correct VMID"

    def test_mount_entries_logic(self):
        """Test that mount entries are properly constructed for iGPU and media."""
        tasks_file = Path(__file__).parent.parent / "roles" / "jellyfin_lxc" / "tasks" / "main.yml"

        content = tasks_file.read_text()

        # Check for mount entry construction
        assert "_jellyfin_mount_entries" in content, "Should construct mount entries variable"
        assert "igpu_render_device" in content, "Should reference iGPU device"
        assert "jellyfin_media_path" in content, "Should reference media path"
        assert "mp=" in content, "Should create proper mount point syntax"

    def test_ip_computation(self):
        """Test that container IP is computed correctly from LAN gateway."""
        tasks_file = Path(__file__).parent.parent / "roles" / "jellyfin_lxc" / "tasks" / "main.yml"

        content = tasks_file.read_text()

        # Check for IP computation logic
        assert "_jellyfin_ct_ip" in content, "Should compute container IP"
        assert "jellyfin_ct_ip_offset" in content, "Should use IP offset"
        assert "_lan_gateway" in content, "Should reference LAN gateway"
