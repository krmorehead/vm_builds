"""Tests for jellyfin_configure role.

Run with: pytest tests/test_jellyfin_configure_role.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestJellyfinConfigureRole:
    """Test jellyfin_configure role structure and configuration."""

    def test_role_directory_exists(self):
        """Test that jellyfin_configure role directory structure exists."""
        role_path = Path(__file__).parent.parent / "roles" / "jellyfin_configure"

        assert role_path.exists(), "jellyfin_configure role directory should exist"
        assert (role_path / "defaults" / "main.yml").exists(), "defaults/main.yml should exist"
        assert (role_path / "tasks" / "main.yml").exists(), "tasks/main.yml should exist"
        assert (role_path / "meta" / "main.yml").exists(), "meta/main.yml should exist"

    def test_defaults_main_variables(self):
        """Test that defaults/main.yml contains required variables."""
        defaults_file = Path(__file__).parent.parent / "roles" / "jellyfin_configure" / "defaults" / "main.yml"

        content = defaults_file.read_text()

        # Check for required variables
        assert "jellyfin_admin_password:" in content, "jellyfin_admin_password should be defined"
        assert "lookup('env', 'JELLYFIN_ADMIN_PASSWORD')" in content, "Should lookup env variable"
        assert "default('', true)" in content, "Should have empty string default"

    def test_tasks_main_structure(self):
        """Test that tasks/main.yml contains expected configuration sections."""
        tasks_file = Path(__file__).parent.parent / "roles" / "jellyfin_configure" / "tasks" / "main.yml"

        content = tasks_file.read_text()

        # Check for required sections
        assert "# ── Verify Jellyfin service is available" in content, "Should verify service availability"
        assert "# ── Configure iGPU access" in content, "Should configure iGPU access"
        assert "# ── Generate admin password if not provided" in content, "Should handle password generation"
        assert "# ── Configure Jellyfin server settings" in content, "Should configure server settings"
        assert "# ── Configure admin user" in content, "Should configure admin user"
        assert "# ── Enable and start Jellyfin service" in content, "Should manage service state"

        # Check for key tasks
        assert "pct exec" in content, "Should use pct_exec for container commands"
        assert "systemctl is-enabled jellyfin" in content, "Should verify Jellyfin service"
        assert "vainfo" in content, "Should verify VA-API drivers"
        assert "igpu_render_gid" in content, "Should use iGPU render GID"
        assert "igpu_render_device" in content, "Should use iGPU render device"
        assert "openssl rand" in content, "Should generate random password"
        assert "jellyfin_static_ip" in content, "Should reference static IP"

        # Check for configuration files
        assert "network.xml" in content, "Should configure network settings"
        assert "transcoding.xml" in content, "Should configure transcoding settings"
        assert "library.xml" in content, "Should configure media library paths"
        assert "/media" in content, "Should mount media path"

        # Check for iGPU configuration
        assert "groupadd -g {{ igpu_render_gid }} render" in content, "Should create render group"
        assert "usermod -a -G render jellyfin" in content, "Should add jellyfin user to render group"
        assert "EnableHardwareEncoding>true</EnableHardwareEncoding" in content, "Should enable hardware encoding"
        assert "HardwareAccelerationType>vaapi</HardwareAccelerationType" in content, "Should use VA-API acceleration"

    def test_meta_main_structure(self):
        """Test that meta/main.yml contains required metadata."""
        meta_file = Path(__file__).parent.parent / "roles" / "jellyfin_configure" / "meta" / "main.yml"

        content = meta_file.read_text()

        # Check for required metadata
        assert "role_name: jellyfin_configure" in content, "Should define role name"
        assert "description:" in content, "Should have description"
        assert "min_ansible_version: \"2.15\"" in content, "Should specify minimum Ansible version"
        assert "platforms:" in content, "Should specify supported platforms"
        assert "Debian" in content, "Should support Debian platform"
        assert "bookworm" in content, "Should support Debian bookworm"

    def test_siteyml_integration(self):
        """Test that site.yml includes jellyfin_configure configure play."""
        site_yml = Path(__file__).parent.parent / "playbooks" / "site.yml"

        content = site_yml.read_text()

        # Check for configure play (targets media_nodes, not dynamic group,
        # because jellyfin_configure needs host-side igpu facts via pct exec)
        assert "Configure Jellyfin" in content, "Should have Jellyfin configure play"
        assert "tags: [media]" in content, "Configure play should be tagged [media]"
        assert "jellyfin_configure" in content, "Configure play should include jellyfin_configure role"

    def test_password_handling(self):
        """Test that password generation logic is implemented."""
        tasks_file = Path(__file__).parent.parent / "roles" / "jellyfin_configure" / "tasks" / "main.yml"

        content = tasks_file.read_text()

        # Check for password handling logic
        assert "_jellyfin_admin_password" in content, "Should have password variable"
        assert "when: jellyfin_admin_password | length > 0" in content, "Should handle provided password"
        assert "when: jellyfin_admin_password | length == 0" in content, "Should handle empty password"
        assert "openssl rand -base64 24" in content, "Should generate random password"
        assert "no_log: true" in content, "Should hide password in logs"

    def test_igpu_configuration(self):
        """Test that iGPU configuration is properly implemented."""
        tasks_file = Path(__file__).parent.parent / "roles" / "jellyfin_configure" / "tasks" / "main.yml"

        content = tasks_file.read_text()

        # Check for iGPU group creation
        assert "groupadd -g {{ igpu_render_gid }} render" in content, "Should create render group with GID"

        # Check for user group assignment
        assert "usermod -a -G render jellyfin" in content, "Should add jellyfin user to render group"

        # Check for VA-API verification
        assert "vainfo" in content, "Should verify VA-API drivers work"

    def test_media_path_configuration(self):
        """Test that media paths are properly configured."""
        tasks_file = Path(__file__).parent.parent / "roles" / "jellyfin_configure" / "tasks" / "main.yml"

        content = tasks_file.read_text()

        # Check for media path mounting
        assert "/media" in content, "Should configure /media path"
        assert "library.xml" in content, "Should configure library settings"
        assert "CollectionFolder" in content, "Should set up collection folders"
        assert "<Path>/media</Path>" in content, "Should mount media in library"

    def test_service_management(self):
        """Test that service management is implemented."""
        tasks_file = Path(__file__).parent.parent / "roles" / "jellyfin_configure" / "tasks" / "main.yml"

        content = tasks_file.read_text()

        # Check for service management
        assert "systemctl enable jellyfin" in content, "Should enable Jellyfin service"
        assert "systemctl start jellyfin" in content, "Should start Jellyfin service"
        assert "curl -s http://127.0.0.1:8096" in content, "Should verify web interface readiness"

    def test_configuration_files(self):
        """Test that all required configuration files are created."""
        tasks_file = Path(__file__).parent.parent / "roles" / "jellyfin_configure" / "tasks" / "main.yml"

        content = tasks_file.read_text()

        # Check for XML configuration files
        assert "network.xml" in content, "Should create network configuration"
        assert "transcoding.xml" in content, "Should create transcoding configuration"
        assert "library.xml" in content, "Should create library configuration"

        # Check for key configuration elements
        assert "<Port>8096</Port>" in content, "Should configure web port"
        assert "<EnableHardwareEncoding>true</EnableHardwareEncoding>" in content, "Should enable hardware encoding"
        assert "<VaApiDevice>{{ igpu_render_device }}</VaApiDevice>" in content, "Should set VA-API device"
