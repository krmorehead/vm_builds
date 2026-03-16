"""Tests for jellyfin-lxc molecule scenario.

Run with: pytest tests/test_jellyfin_molecule_scenario.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestJellyfinMoleculeScenario:
    """Test jellyfin-lxc molecule scenario structure and content."""

    def test_molecule_directory_exists(self):
        """Test that jellyfin-lxc molecule scenario directory exists."""
        scenario_path = Path(__file__).parent.parent / "molecule" / "jellyfin-lxc"

        assert scenario_path.exists(), "jellyfin-lxc scenario directory should exist"
        assert (scenario_path / "molecule.yml").exists(), "molecule.yml should exist"
        assert (scenario_path / "converge.yml").exists(), "converge.yml should exist"
        assert (scenario_path / "verify.yml").exists(), "verify.yml should exist"
        assert (scenario_path / "cleanup.yml").exists(), "cleanup.yml should exist"

    def test_molecule_yml_structure(self):
        """Test that molecule.yml contains required configuration."""
        molecule_file = Path(__file__).parent.parent / "molecule" / "jellyfin-lxc" / "molecule.yml"

        content = molecule_file.read_text()

        # Check for required sections
        assert "dependency:" in content, "Should have dependency section"
        assert "driver:" in content, "Should have driver section"
        assert "platforms:" in content, "Should have platforms section"
        assert "provisioner:" in content, "Should have provisioner section"
        assert "verifier:" in content, "Should have verifier section"
        assert "scenario:" in content, "Should have scenario section"

        # Check for platform groups
        assert "media_nodes" in content, "Should target media_nodes group"
        assert "proxmox" in content, "Should include proxmox group"

        # Check for provisioner env
        assert "JELLYFIN_ADMIN_PASSWORD:" in content, "Should pass JELLYFIN_ADMIN_PASSWORD env var"

        # Check for test sequence
        assert "converge" in content, "Should include converge in test sequence"
        assert "verify" in content, "Should include verify in test sequence"
        assert "cleanup" in content, "Should include cleanup in test sequence"

    def test_converge_yml_structure(self):
        """Test that converge.yml contains required provision and configure plays."""
        converge_file = Path(__file__).parent.parent / "molecule" / "jellyfin-lxc" / "converge.yml"

        content = converge_file.read_text()

        # Check for required plays (configure targets media_nodes, not dynamic
        # group, because jellyfin_configure needs host-side igpu facts via pct exec)
        assert "- name: Provision Jellyfin LXC container" in content, "Should have provision play"
        assert "hosts: media_nodes" in content, "Provision play should target media_nodes"
        assert "- name: Configure Jellyfin" in content, "Should have configure play"

        # Check for roles
        assert "- jellyfin_lxc" in content, "Should include jellyfin_lxc role"
        assert "- jellyfin_configure" in content, "Should include jellyfin_configure role"

    def test_verify_yml_structure(self):
        """Test that verify.yml contains comprehensive Jellyfin verification."""
        verify_file = Path(__file__).parent.parent / "molecule" / "jellyfin-lxc" / "verify.yml"

        content = verify_file.read_text()

        # Check for group reconstruction
        assert "Reconstruct jellyfin dynamic group" in content, "Should start with group reconstruction"

        # Check for container state verification
        assert "Container state" in content, "Should verify container state"
        assert "Check container is running" in content, "Should check if container is running"
        assert "pct status" in content, "Should use pct status command"

        # Check for container configuration verification
        assert "Container config" in content, "Should verify container configuration"
        assert "onboot" in content, "Should check onboot setting"
        assert "startup order" in content, "Should check startup order"

        # Check for iGPU device mounting verification
        assert "iGPU device mounting" in content, "Should verify iGPU device mounting"
        assert "/dev/dri/renderD128" in content, "Should check iGPU device path"

        # Check for media path mounting verification
        assert "Media path mounting" in content, "Should verify media path mounting"
        assert "/media" in content, "Should check media path"

        # Check for Jellyfin service health checks
        assert "Jellyfin service health checks" in content, "Should verify Jellyfin service"
        assert "systemctl is-active jellyfin" in content, "Should check Jellyfin service status"
        assert ":8096" in content, "Should check web interface port"

        # Check for VA-API verification
        assert "VA-API hardware acceleration" in content, "Should verify VA-API"
        assert "vainfo" in content, "Should use vainfo for VA-API check"

        # Check for render group verification
        assert "iGPU render group access" in content, "Should verify render group access"
        assert "render" in content, "Should check render group membership"

        # Check for configuration files verification
        assert "Configuration files" in content, "Should verify config files exist"
        assert "network.xml" in content, "Should check network config"
        assert "transcoding.xml" in content, "Should check transcoding config"
        assert "library.xml" in content, "Should check library config"

        # Check for admin user verification
        assert "Admin user setup" in content, "Should verify admin user"

    def test_cleanup_yml_structure(self):
        """Test that cleanup.yml contains proper container cleanup."""
        cleanup_file = Path(__file__).parent.parent / "molecule" / "jellyfin-lxc" / "cleanup.yml"

        content = cleanup_file.read_text()

        # Check for required sections
        assert "- name: Destroy Jellyfin container" in content, "Should have destroy play"
        assert "hosts: media_nodes" in content, "Cleanup should target media_nodes"

        # Check for container destruction
        assert "pct stop" in content, "Should stop container before destruction"
        assert "pct destroy" in content, "Should destroy container"
        assert "--purge" in content, "Should purge container completely"
        assert "jellyfin_ct_id: 300" in content, "Should use correct container ID"

    def test_full_integration_verification(self):
        """Test that default molecule scenario includes Jellyfin verification."""
        default_verify_file = Path(__file__).parent.parent / "molecule" / "default" / "verify.yml"

        content = default_verify_file.read_text()

        # Check for Jellyfin verification section
        assert "Verify Jellyfin media server" in content, "Should have Jellyfin verification in default scenario"
        assert "hosts: media_nodes" in content, "Should target media_nodes in default scenario"
        assert "jellyfin_ct_id" in content, "Should reference Jellyfin container ID"
        assert "/dev/dri/renderD128" in content, "Should verify iGPU device in default scenario"
        assert ":8096" in content, "Should verify web interface in default scenario"

    def test_cleanup_rollback_integration(self):
        """Test that playbooks/cleanup.yml includes Jellyfin rollback."""
        cleanup_file = Path(__file__).parent.parent / "playbooks" / "cleanup.yml"

        content = cleanup_file.read_text()

        # Check for Jellyfin rollback play
        assert "Rollback Jellyfin container" in content, "Should have Jellyfin rollback play"
        assert "tags: [jellyfin-rollback, never]" in content, "Should have proper rollback tags"
        assert "jellyfin_ct_id" in content, "Should reference Jellyfin container ID in rollback"

        # Check for template cleanup
        assert "jellyfin-*.tar.zst" in content, "Should clean up Jellyfin template cache"
