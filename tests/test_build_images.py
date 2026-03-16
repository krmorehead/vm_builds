"""Tests for build-images.sh script.

Run with: pytest tests/test_build_images.py -v
"""

import subprocess
import sys
from pathlib import Path


def test_build_images_shell_script_syntax():
    """Test that build-images.sh has valid shell syntax."""
    script_path = Path(__file__).parent.parent / "scripts" / "build-images.sh"

    # Check file exists
    assert script_path.exists(), "build-images.sh script should exist"

    # Validate shell syntax
    result = subprocess.run(
        ["bash", "-n", str(script_path)],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0, f"Shell syntax error: {result.stderr}"


def test_jellyfin_build_function_exists():
    """Test that the jellyfin build function is defined in build-images.sh."""
    script_path = Path(__file__).parent.parent / "scripts" / "build-images.sh"

    content = script_path.read_text()

    # Check for Jellyfin build function
    assert "build_jellyfin_lxc()" in content, "build_jellyfin_lxc function should exist"
    assert "cleanup_jellyfin_build()" in content, "cleanup_jellyfin_build function should exist"
    assert "JELLYFIN_OUTPUT_NAME=" in content, "JELLYFIN_OUTPUT_NAME variable should exist"
    assert "jellyfin-debian-12-amd64.tar.zst" in content, "Jellyfin template filename should exist"


def test_jellyfin_build_call_in_main():
    """Test that the jellyfin build function is called in the main execution section."""
    script_path = Path(__file__).parent.parent / "scripts" / "build-images.sh"

    content = script_path.read_text()

    # Find the main execution section (should_build calls)
    lines = content.split('\n')
    should_build_section = []
    in_should_build = False

    for line in lines:
        if "should_build" in line and "&&" in line:
            in_should_build = True
        elif in_should_build and line.strip() == "":
            break

        if in_should_build:
            should_build_section.append(line)

    should_build_code = '\n'.join(should_build_section)

    # Check that jellyfin build is called
    assert "should_build jellyfin && build_jellyfin_lxc" in should_build_code, \
        "Jellyfin build should be called in main execution section"


def test_jellyfin_variables_in_group_vars():
    """Test that Jellyfin variables are defined in group_vars/all.yml."""
    group_vars_path = Path(__file__).parent.parent / "inventory" / "group_vars" / "all.yml"

    assert group_vars_path.exists(), "group_vars/all.yml should exist"

    content = group_vars_path.read_text()

    # Check for required Jellyfin variables
    assert "jellyfin_lxc_template:" in content, "jellyfin_lxc_template should be defined"
    assert "jellyfin_lxc_template_path:" in content, "jellyfin_lxc_template_path should be defined"
    assert "jellyfin_ct_ip_offset: 15" in content, "jellyfin_ct_ip_offset should be 15"
    assert "jellyfin_media_path: /mnt/media" in content, "jellyfin_media_path should be defined"


def test_jellyfin_ct_id_exists():
    """Test that jellyfin_ct_id is defined in group_vars/all.yml."""
    group_vars_path = Path(__file__).parent.parent / "inventory" / "group_vars" / "all.yml"

    content = group_vars_path.read_text()

    # Check for Jellyfin CT ID
    assert "jellyfin_ct_id: 300" in content, "jellyfin_ct_id should be 300"