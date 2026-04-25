"""Proxmox VE REST API client — stdlib only, no SSH.

Every method talks HTTPS to the PVE API on port 8006 using a
PVEAPIToken for auth.

The kiosk container can reach its Proxmox host via HOST_IP:8006.
SSL verification is disabled because PVE uses a self-signed cert.
"""

from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


class PveApiError(Exception):
    """Raised when the PVE API returns an error."""

    def __init__(self, message: str, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class PveApiClient:
    """Thin wrapper around the Proxmox VE REST API.

    Parameters
    ----------
    host : str
        IP or hostname of the Proxmox node (e.g. ``10.10.10.2``).
    node : str
        PVE node name (e.g. ``home``, ``ai``).
    token : str
        Full API token string: ``root@pam!ansible=<secret>``.
    timeout : int
        Default HTTP timeout in seconds for individual requests.
    """

    def __init__(
        self,
        host: str,
        node: str,
        token: str,
        timeout: int = 30,
    ) -> None:
        self._base = f"https://{host}:8006/api2/json"
        self._node = node
        self._auth = f"PVEAPIToken={token}"
        self._timeout = timeout

    # ── low-level helpers ──────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        timeout: int | None = None,
    ) -> dict:
        url = f"{self._base}{path}"
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode()

        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", self._auth)
        if body is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            resp = urllib.request.urlopen(
                req, context=_SSL_CTX, timeout=timeout or self._timeout,
            )
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode()[:500]
            except (OSError, UnicodeDecodeError, AttributeError):
                pass
            msg = f"PVE API {method} {path}: HTTP {exc.code}"
            if err_body:
                try:
                    detail = json.loads(err_body).get("message", err_body)
                except (json.JSONDecodeError, AttributeError):
                    detail = err_body
                msg = f"{msg} — {detail.strip()}"
            raise PveApiError(msg, status=exc.code, body=err_body) from exc
        except urllib.error.URLError as exc:
            raise PveApiError(f"PVE API {method} {path}: {exc.reason}") from exc

    def _get(self, path: str, timeout: int | None = None) -> dict:
        return self._request("GET", path, timeout=timeout)

    def _post(
        self, path: str, data: dict | None = None, timeout: int | None = None,
    ) -> dict:
        return self._request("POST", path, data=data, timeout=timeout)

    def _put(
        self, path: str, data: dict | None = None, timeout: int | None = None,
    ) -> dict:
        return self._request("PUT", path, data=data, timeout=timeout)

    def _delete(self, path: str, timeout: int | None = None) -> dict:
        return self._request("DELETE", path, timeout=timeout)

    # ── node-level ─────────────────────────────────────────────────

    def node_status(self) -> dict:
        """Host-level metrics: CPU, memory, disk, uptime.

        Returns the ``data`` dict from ``GET /nodes/{node}/status``.
        """
        resp = self._get(f"/nodes/{self._node}/status")
        return resp.get("data", {})

    # ── container (LXC) operations ────────────────────────────────

    def ct_list(self) -> list[dict]:
        """List all LXC containers on this node."""
        resp = self._get(f"/nodes/{self._node}/lxc")
        return resp.get("data", [])

    def ct_status(self, vmid: int) -> dict | None:
        """Get container status.  Returns ``None`` if the CT doesn't exist."""
        try:
            resp = self._get(f"/nodes/{self._node}/lxc/{vmid}/status/current")
            return resp.get("data")
        except PveApiError as exc:
            if exc.status in (404, 500):
                return None
            raise

    def ct_config(self, vmid: int) -> dict:
        """Get container configuration."""
        resp = self._get(f"/nodes/{self._node}/lxc/{vmid}/config")
        return resp.get("data", {})

    def ct_start(self, vmid: int) -> str:
        """Start a container.  Returns the task UPID."""
        resp = self._post(f"/nodes/{self._node}/lxc/{vmid}/status/start")
        return resp.get("data", "")

    def ct_stop(self, vmid: int) -> str:
        """Stop a container.  Returns the task UPID."""
        resp = self._post(f"/nodes/{self._node}/lxc/{vmid}/status/stop")
        return resp.get("data", "")

    def ct_destroy(self, vmid: int, purge: bool = True) -> str:
        """Destroy a container.  Returns the task UPID."""
        path = f"/nodes/{self._node}/lxc/{vmid}"
        if purge:
            path += "?purge=1&destroy-unreferenced-disks=1"
        resp = self._delete(path)
        return resp.get("data", "")

    def ct_create(self, vmid: int, **kwargs: str | int | bool) -> str:
        """Create a container.  Returns the task UPID.

        Pass PVE API parameters as keyword args (e.g.
        ``ostemplate``, ``hostname``, ``memory``, ``cores``,
        ``rootfs``, ``net0``, ``unprivileged``, ``onboot``,
        ``features``, ``nameserver``, ``ostype``, ``startup``).
        """
        params: dict[str, str] = {"vmid": str(vmid)}
        for k, v in kwargs.items():
            if v is not None and v != "":
                params[k] = str(v)
        resp = self._post(f"/nodes/{self._node}/lxc", data=params)
        return resp.get("data", "")

    def ct_set(self, vmid: int, **kwargs: str | int | bool) -> None:
        """Update container configuration."""
        params: dict[str, str] = {}
        for k, v in kwargs.items():
            if v is not None and v != "":
                params[k] = str(v)
        if params:
            self._put(f"/nodes/{self._node}/lxc/{vmid}/config", data=params)

    def ct_interfaces(self, vmid: int) -> list[dict]:
        """Get container network interfaces (like ``ip addr`` inside)."""
        try:
            resp = self._get(f"/nodes/{self._node}/lxc/{vmid}/interfaces")
            return resp.get("data", [])
        except PveApiError:
            return []

    # ── QEMU VM operations ────────────────────────────────────────

    def vm_list(self) -> list[dict]:
        """List all QEMU VMs on this node."""
        resp = self._get(f"/nodes/{self._node}/qemu")
        return resp.get("data", [])

    def vm_status(self, vmid: int) -> dict | None:
        """Get VM status.  Returns ``None`` if the VM doesn't exist."""
        try:
            resp = self._get(f"/nodes/{self._node}/qemu/{vmid}/status/current")
            return resp.get("data")
        except PveApiError as exc:
            if exc.status in (404, 500):
                return None
            raise

    def vm_start(self, vmid: int) -> str:
        resp = self._post(f"/nodes/{self._node}/qemu/{vmid}/status/start")
        return resp.get("data", "")

    def vm_stop(self, vmid: int) -> str:
        resp = self._post(f"/nodes/{self._node}/qemu/{vmid}/status/stop")
        return resp.get("data", "")

    # ── task polling ──────────────────────────────────────────────

    def task_status(self, upid: str) -> dict:
        """Get the status of an async task by UPID."""
        encoded = urllib.parse.quote(upid, safe="")
        resp = self._get(f"/nodes/{self._node}/tasks/{encoded}/status")
        return resp.get("data", {})

    def task_log(self, upid: str, start: int = 0, limit: int = 50) -> list[dict]:
        """Get task log lines.  Returns list of ``{n, t}`` dicts."""
        encoded = urllib.parse.quote(upid, safe="")
        qs = urllib.parse.urlencode({"start": start, "limit": limit})
        resp = self._get(
            f"/nodes/{self._node}/tasks/{encoded}/log?{qs}",
        )
        return resp.get("data", [])

    def wait_for_task(
        self,
        upid: str,
        timeout: int = 60,
        poll_interval: float = 2.0,
        log_interval: float = 15.0,
    ) -> dict:
        """Poll a task UPID until completion or timeout.

        Logs progress every ``log_interval`` seconds using the PVE task
        log, so long-running operations (template extraction) are visible
        in real time instead of silently blocking.
        """
        _log = logging.getLogger("vm_builds.pve_task")
        deadline = time.monotonic() + timeout
        last_log_time = 0.0
        last_log_line = 0
        parts = upid.split(":")
        task_type = f"{parts[5]}:{parts[6]}" if len(parts) > 6 else "task"
        while time.monotonic() < deadline:
            status = self.task_status(upid)
            if status.get("status") == "stopped":
                elapsed = timeout - (deadline - time.monotonic())
                exitstatus = status.get("exitstatus", "")
                if exitstatus and exitstatus != "OK":
                    _log.error(
                        "[%s] FAILED after %.0fs: %s",
                        task_type, elapsed, exitstatus,
                    )
                    raise PveApiError(
                        f"Task failed: {exitstatus}",
                        body=json.dumps(status),
                    )
                _log.info("[%s] completed in %.0fs", task_type, elapsed)
                return status
            now = time.monotonic()
            if now - last_log_time >= log_interval:
                last_log_time = now
                elapsed = timeout - (deadline - now)
                remaining = deadline - now
                try:
                    lines = self.task_log(upid, start=last_log_line, limit=10)
                    if lines:
                        last_log_line += len(lines)
                        latest = lines[-1].get("t", "").strip()
                        _log.info(
                            "[%s] %.0fs elapsed, %.0fs remaining — %s",
                            task_type, elapsed, remaining, latest,
                        )
                    else:
                        _log.info(
                            "[%s] %.0fs elapsed, %.0fs remaining (waiting)",
                            task_type, elapsed, remaining,
                        )
                except PveApiError:
                    _log.info(
                        "[%s] %.0fs elapsed, %.0fs remaining",
                        task_type, elapsed, remaining,
                    )
            time.sleep(poll_interval)
        raise PveApiError(
            f"Task timed out after {timeout}s: {upid}",
            body=upid,
        )

    # ── convenience: combined operations ──────────────────────────

    def ct_unlock(self, vmid: int) -> None:
        """Remove any lock from a container (e.g. stale 'create' lock)."""
        try:
            self._put(
                f"/nodes/{self._node}/lxc/{vmid}/config",
                data={"delete": "lock"},
            )
        except PveApiError:
            pass

    def ct_stop_and_destroy(self, vmid: int, timeout: int = 60) -> None:
        """Stop (if running) then destroy a container, waiting for each."""
        st = self.ct_status(vmid)
        if st is None:
            return
        if st.get("lock"):
            self.ct_unlock(vmid)
        if st.get("status") == "running":
            upid = self.ct_stop(vmid)
            if upid:
                self.wait_for_task(upid, timeout=timeout)
        upid = self.ct_destroy(vmid)
        if upid:
            self.wait_for_task(upid, timeout=timeout)

    def ct_create_and_start(
        self,
        vmid: int,
        timeout: int = 300,
        start: bool = True,
        **kwargs: str | int | bool,
    ) -> dict:
        """Create a container, wait for creation, optionally start it.

        Returns the final task status for the create operation.
        """
        upid = self.ct_create(vmid, **kwargs)
        result = self.wait_for_task(upid, timeout=timeout)
        if start:
            start_upid = self.ct_start(vmid)
            if start_upid:
                self.wait_for_task(start_upid, timeout=60)
        return result

    def ct_has_ip(self, vmid: int, iface: str = "eth0") -> bool:
        """Check if a container interface has an IPv4 address."""
        interfaces = self.ct_interfaces(vmid)
        for ifc in interfaces:
            if ifc.get("name") != iface:
                continue
            for addr in ifc.get("ip-addresses", []):
                if addr.get("ip-address-type") == "inet":
                    ip = addr.get("ip-address", "")
                    if ip and ip != "127.0.0.1":
                        return True
        return False

    def guest_type(self, vmid: int) -> str | None:
        """Determine if a VMID is an LXC container or QEMU VM.

        Returns ``"lxc"``, ``"qemu"``, or ``None``.
        """
        if self.ct_status(vmid) is not None:
            return "lxc"
        if self.vm_status(vmid) is not None:
            return "qemu"
        return None

    # ── backup / vzdump ───────────────────────────────────────────

    def vzdump(
        self,
        vmid: int,
        compress: str = "zstd",
        mode: str = "stop",
        timeout: int = 600,
    ) -> str:
        """Run vzdump on a container and wait for completion.

        Returns the UPID of the completed task.
        """
        data = {
            "vmid": str(vmid),
            "compress": compress,
            "mode": mode,
            "storage": "local",
        }
        resp = self._post(f"/nodes/{self._node}/vzdump", data=data)
        upid = resp.get("data", "")
        if not upid:
            raise PveApiError(f"vzdump returned no UPID for CT {vmid}")
        self.wait_for_task(upid, timeout=timeout)
        return upid

    _DUMP_DIR = "/var/lib/vz/dump"

    def vzdump_find_archive(self, vmid: int) -> str:
        """Find the most recent vzdump archive path for a VMID.

        Searches via the storage content API for backup content type.
        Returns the full path to the ``.tar.zst`` (or ``.tar.gz``) archive.
        """
        try:
            qs = urllib.parse.urlencode({"content": "backup"})
            resp = self._get(f"/nodes/{self._node}/storage/local/content?{qs}")
            files = resp.get("data", [])
            matches = [
                f for f in files
                if f"vzdump-lxc-{vmid}-" in f.get("volid", "")
            ]
            if matches:
                matches.sort(key=lambda f: f.get("ctime", 0), reverse=True)
                volid = matches[0]["volid"]
                filename = volid.split("/")[-1] if "/" in volid else volid.split(":")[-1]
                return f"{self._DUMP_DIR}/{filename}"
        except PveApiError:
            pass
        raise PveApiError(f"No vzdump archive found for CT {vmid}")

    def storage_content_list(
        self,
        storage: str = "local",
        content: str = "vztmpl",
    ) -> list[dict]:
        """List storage content filtered by type."""
        qs = urllib.parse.urlencode({"content": content})
        resp = self._get(f"/nodes/{self._node}/storage/{storage}/content?{qs}")
        return resp.get("data", [])
