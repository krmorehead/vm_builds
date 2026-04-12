"""Thin HTTP client for Manager API calls.

Single responsibility: HTTP transport to the local Manager API.
All page modules use this instead of constructing httpx clients directly.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from scripts.webui.data import get_api_base_url

_log = logging.getLogger("vm_builds.api_client")


class ApiClient:
    """Async HTTP client for the local Manager API.

    Centralises base URL resolution, timeout defaults, and error handling
    so page modules don't repeat httpx boilerplate.

    Each request creates a short-lived ``AsyncClient``.  NiceGUI manages
    its own event loop lifecycle, so a long-lived client causes cleanup
    errors when the loop closes between page renders or during shutdown.
    """

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        return get_api_base_url()

    async def get(
        self, path: str, *, timeout: float | None = None, **kwargs: Any,
    ) -> httpx.Response:
        """GET ``{base_url}{path}`` with default timeout."""
        async with httpx.AsyncClient() as client:
            return await client.get(
                f"{self.base_url}{path}",
                timeout=timeout or self._timeout,
                **kwargs,
            )

    async def post(
        self,
        path: str,
        *,
        json: dict | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """POST ``{base_url}{path}`` with optional JSON body."""
        async with httpx.AsyncClient() as client:
            return await client.post(
                f"{self.base_url}{path}",
                json=json,
                timeout=timeout or self._timeout,
                **kwargs,
            )

    async def get_json(
        self, path: str, *, timeout: float | None = None,
    ) -> dict | list | None:
        """GET and return parsed JSON, or ``None`` on any transport error."""
        try:
            resp = await self.get(path, timeout=timeout)
            if resp.is_success:
                return resp.json()
            _log.debug("GET %s returned %d", path, resp.status_code)
        except (httpx.HTTPError, OSError) as exc:
            _log.debug("GET %s failed: %s", path, exc)
        return None

    async def post_json(
        self,
        path: str,
        *,
        json: dict | None = None,
        timeout: float | None = None,
    ) -> dict | None:
        """POST and return parsed JSON, or ``None`` on any transport error."""
        try:
            resp = await self.post(path, json=json, timeout=timeout)
            if resp.is_success:
                return resp.json()
            _log.debug("POST %s returned %d", path, resp.status_code)
        except (httpx.HTTPError, OSError) as exc:
            _log.debug("POST %s failed: %s", path, exc)
        return None


# Module-level singleton — pages import this directly.
api = ApiClient()
