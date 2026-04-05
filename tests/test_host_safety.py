"""Static linter: catch destructive operations on non-recoverable hosts.

Scans all Ansible YAML files for patterns that could crash or shut down
a Proxmox host without a proper safety gate. This prevents regressions
like the modprobe -r amdgpu incident that kernel-panicked a single-GPU
AMD host (ai) connected via USB ethernet — unrecoverable without
physical access.

Run with: pytest tests/test_host_safety.py -v

Checked patterns:
  1. modprobe -r amdgpu / modprobe -r i915 without VGA count guard
  2. shutdown / poweroff / halt / init 0 in cleanup or molecule files
  3. Broad-scope plays (hosts: proxmox) containing GPU driver unloads

Safe patterns (allowed):
  - modprobe -r amdgpu gated on VGA count >= 2 (in same task block)
  - modprobe -r iwlwifi / iwlmvm (WiFi, not dangerous)
  - modprobe -r wireguard (network module, not dangerous)
  - PCI bus rescan (echo 1 > /sys/bus/pci/rescan) — always safe
"""

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

YAML_DIRS = [
    REPO_ROOT / "molecule",
    REPO_ROOT / "playbooks",
    REPO_ROOT / "roles",
    REPO_ROOT / "tasks",
]

GPU_DRIVER_UNLOAD = re.compile(r"modprobe\s+-r\s+(amdgpu|i915)\b")

HOST_SHUTDOWN = re.compile(
    r"\b(shutdown|poweroff|halt|init\s+0|systemctl\s+poweroff)\b"
)

VGA_COUNT_GUARD = re.compile(
    r"(vga_count|VGA compatible controller|lspci.*grep.*VGA)", re.IGNORECASE
)

SAFE_MODPROBE_UNLOADS = {"iwlwifi", "iwlmvm", "wireguard", "vfio_pci", "vfio-pci"}

SHELL_DIRS = [
    REPO_ROOT / "scripts",
    REPO_ROOT / "roles",
]


def _collect_yaml_files() -> list[Path]:
    """Collect all .yml files from playbook/molecule/role/task dirs."""
    files = []
    for d in YAML_DIRS:
        if d.exists():
            files.extend(d.rglob("*.yml"))
    return sorted(files)


VENDORED_DIRS = {".image-builder-cache"}


def _collect_shell_files() -> list[Path]:
    """Collect all .sh files from scripts/ and roles/*/files/.

    Excludes vendored/third-party directories that we don't control.
    """
    files = []
    for d in SHELL_DIRS:
        if d.exists():
            for f in d.rglob("*.sh"):
                if not any(v in f.parts for v in VENDORED_DIRS):
                    files.append(f)
    return sorted(files)


def _file_has_vga_guard(content: str) -> bool:
    """Check if the file contains a VGA controller count check."""
    return bool(VGA_COUNT_GUARD.search(content))


def _get_broad_scope_plays(filepath: Path) -> list[dict]:
    """Return plays that target all proxmox hosts (broad scope)."""
    docs = yaml.safe_load_all(filepath.read_text())
    plays = []
    for doc in docs:
        if not isinstance(doc, list):
            continue
        for play in doc:
            if not isinstance(play, dict):
                continue
            hosts = play.get("hosts", "")
            if isinstance(hosts, str) and "proxmox" in hosts:
                if "gaming_nodes" not in hosts:
                    plays.append(play)
    return plays


def _extract_commands_from_tasks(tasks: list) -> list[str]:
    """Recursively extract shell/command strings from task structures.

    Handles block/rescue/always nesting and all command module variants.
    """
    commands = []
    if not isinstance(tasks, list):
        return commands
    for task in tasks:
        if not isinstance(task, dict):
            continue
        for key in ("ansible.builtin.shell", "ansible.builtin.command",
                    "ansible.builtin.raw", "shell", "command", "raw"):
            cmd = task.get(key, "")
            if isinstance(cmd, dict):
                cmd = cmd.get("cmd", "")
            if isinstance(cmd, str) and cmd:
                commands.append(cmd)
        for nested_key in ("block", "rescue", "always"):
            nested = task.get(nested_key, [])
            if isinstance(nested, list):
                commands.extend(_extract_commands_from_tasks(nested))
    return commands


class TestNoUnguardedGPUDriverUnload:
    """modprobe -r amdgpu/i915 must have a VGA count gate."""

    def test_collect_files(self):
        files = _collect_yaml_files()
        assert len(files) > 0, "No YAML files found to scan"

    def test_no_ungated_gpu_unload_in_broad_scope(self):
        """Broad-scope plays (hosts: proxmox*) must not unload GPU drivers."""
        violations = []
        for filepath in _collect_yaml_files():
            content = filepath.read_text()
            if not GPU_DRIVER_UNLOAD.search(content):
                continue

            plays = _get_broad_scope_plays(filepath)
            for play in plays:
                all_tasks = play.get("tasks", []) + play.get("pre_tasks", []) + play.get("post_tasks", [])
                commands = _extract_commands_from_tasks(all_tasks)
                for cmd in commands:
                    if GPU_DRIVER_UNLOAD.search(cmd):
                        rel = filepath.relative_to(REPO_ROOT)
                        violations.append(f"{rel}: broad-scope GPU unload")

        assert not violations, (
            "GPU driver unload (modprobe -r amdgpu/i915) found in broad-scope "
            "plays targeting all proxmox hosts. This WILL kernel-panic single-GPU "
            "AMD hosts. Either:\n"
            "  1. Remove the modprobe -r (PCI rescan is sufficient for E2E cleanup)\n"
            "  2. Gate on VGA count >= 2\n"
            "  3. Scope the play to a narrow host group (e.g., gaming_nodes)\n"
            "Violations:\n  - " + "\n  - ".join(violations)
        )

    def test_gpu_unload_always_has_vga_guard(self):
        """Every file with modprobe -r amdgpu/i915 must check VGA count."""
        violations = []
        for filepath in _collect_yaml_files():
            content = filepath.read_text()
            matches = GPU_DRIVER_UNLOAD.findall(content)
            if not matches:
                continue

            if not _file_has_vga_guard(content):
                plays = _get_broad_scope_plays(filepath)
                broad_hosts = [p.get("hosts", "") for p in plays]
                if any("proxmox" in h for h in broad_hosts):
                    rel = filepath.relative_to(REPO_ROOT)
                    violations.append(
                        f"{rel}: modprobe -r {', '.join(set(matches))} "
                        f"without VGA count check"
                    )

        assert not violations, (
            "Files with GPU driver unload in broad-scope plays must gate on "
            "VGA controller count >= 2. On single-GPU AMD hosts, modprobe -r "
            "amdgpu causes a kernel panic.\n"
            "Violations:\n  - " + "\n  - ".join(violations)
        )


class TestYAMLValidity:
    """All YAML files in scanned directories must parse without errors."""

    def test_all_yaml_files_parse(self):
        """YAML syntax errors would hide dangerous commands from the safety linter."""
        failures = []
        for filepath in _collect_yaml_files():
            try:
                list(yaml.safe_load_all(filepath.read_text()))
            except yaml.YAMLError as e:
                rel = filepath.relative_to(REPO_ROOT)
                failures.append(f"{rel}: {e}")
        assert not failures, (
            "YAML parse errors found. These files are invisible to the safety "
            "linter — dangerous commands in them would go undetected.\n"
            "Failures:\n  - " + "\n  - ".join(failures)
        )


class TestNoHostShutdownInAutomation:
    """No shutdown/poweroff commands in cleanup or molecule files."""

    def test_no_shutdown_in_molecule(self):
        """Molecule files must never shut down Proxmox hosts."""
        violations = []
        molecule_dir = REPO_ROOT / "molecule"
        if not molecule_dir.exists():
            return
        for filepath in molecule_dir.rglob("*.yml"):
            content = filepath.read_text()
            for match in HOST_SHUTDOWN.finditer(content):
                line_num = content[:match.start()].count("\n") + 1
                rel = filepath.relative_to(REPO_ROOT)
                violations.append(f"{rel}:{line_num}: '{match.group()}'")

        assert not violations, (
            "Host shutdown/poweroff commands found in molecule files. "
            "Shutting down a Proxmox host during testing can brick non-WoL "
            "hosts (USB ethernet = no remote recovery).\n"
            "Violations:\n  - " + "\n  - ".join(violations)
        )

    def test_no_shutdown_in_cleanup(self):
        """Cleanup playbooks must never shut down Proxmox hosts."""
        violations = []
        for filepath in _collect_yaml_files():
            if "cleanup" not in filepath.name:
                continue
            content = filepath.read_text()
            for match in HOST_SHUTDOWN.finditer(content):
                line_num = content[:match.start()].count("\n") + 1
                rel = filepath.relative_to(REPO_ROOT)
                violations.append(f"{rel}:{line_num}: '{match.group()}'")

        assert not violations, (
            "Host shutdown/poweroff commands found in cleanup files. "
            "Cleanup must only destroy VMs/containers and remove config "
            "files — never shut down the host itself.\n"
            "Violations:\n  - " + "\n  - ".join(violations)
        )


class TestModprobeRPatternSafety:
    """Catalog all modprobe -r usage and verify each is safe."""

    def test_all_modprobe_r_usage_is_known_safe(self):
        """Every modprobe -r must be for a safe module or properly guarded."""
        violations = []
        modprobe_r = re.compile(r"modprobe\s+-r\s+(\S+)")

        for filepath in _collect_yaml_files():
            content = filepath.read_text()
            for match in modprobe_r.finditer(content):
                module = match.group(1)
                if module in SAFE_MODPROBE_UNLOADS:
                    continue
                if module in ("i915", "amdgpu"):
                    if _file_has_vga_guard(content):
                        continue
                    plays = _get_broad_scope_plays(filepath)
                    if not any("proxmox" in p.get("hosts", "") for p in plays):
                        continue
                    rel = filepath.relative_to(REPO_ROOT)
                    line_num = content[:match.start()].count("\n") + 1
                    violations.append(
                        f"{rel}:{line_num}: modprobe -r {module} "
                        f"in broad-scope play without VGA guard"
                    )
                    continue
                if module.rstrip(";") in SAFE_MODPROBE_UNLOADS:
                    continue
                rel = filepath.relative_to(REPO_ROOT)
                line_num = content[:match.start()].count("\n") + 1
                violations.append(
                    f"{rel}:{line_num}: modprobe -r {module} "
                    f"(unknown module — review for safety)"
                )

        assert not violations, (
            "Unrecognized or unguarded modprobe -r found. Each modprobe -r "
            "must be either:\n"
            "  - A safe module (WiFi, wireguard)\n"
            "  - A GPU driver gated on VGA count >= 2\n"
            "  - In a narrow-scope play (not targeting all proxmox hosts)\n"
            "Violations:\n  - " + "\n  - ".join(violations)
        )


class TestShellScriptSafety:
    """Shell scripts in roles/*/files/ and scripts/ must not contain
    unguarded destructive operations."""

    def test_shell_files_found(self):
        files = _collect_shell_files()
        assert len(files) > 0, "No shell scripts found to scan"

    def test_no_ungated_gpu_unload_in_shell(self):
        """Shell scripts must not unload GPU drivers without a VGA count guard."""
        violations = []
        for filepath in _collect_shell_files():
            content = filepath.read_text()
            for match in GPU_DRIVER_UNLOAD.finditer(content):
                if _file_has_vga_guard(content):
                    continue
                rel = filepath.relative_to(REPO_ROOT)
                line_num = content[:match.start()].count("\n") + 1
                violations.append(
                    f"{rel}:{line_num}: modprobe -r {match.group(1)} "
                    f"without VGA count check"
                )
        assert not violations, (
            "GPU driver unload in shell scripts without VGA count guard.\n"
            "Violations:\n  - " + "\n  - ".join(violations)
        )

    def test_no_shutdown_in_shell_scripts(self):
        """Shell scripts must never shut down or power off hosts.

        VM/container shutdown commands (pct shutdown, qm shutdown, ssh
        ... shutdown) are safe and excluded from this check.
        """
        vm_shutdown = re.compile(
            r"(pct\s+shutdown|qm\s+(shutdown|guest\s+cmd\s+\S+\s+shutdown)|ssh\s+.*shutdown)",
            re.IGNORECASE,
        )
        violations = []
        for filepath in _collect_shell_files():
            for i, line in enumerate(filepath.read_text().splitlines(), 1):
                if HOST_SHUTDOWN.search(line) and not vm_shutdown.search(line):
                    if line.lstrip().startswith("#"):
                        continue
                    rel = filepath.relative_to(REPO_ROOT)
                    violations.append(
                        f"{rel}:{i}: '{line.strip()[:80]}'"
                    )
        assert not violations, (
            "Host shutdown/poweroff commands found in shell scripts.\n"
            "Violations:\n  - " + "\n  - ".join(violations)
        )
