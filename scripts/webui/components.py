"""Shared interactive UI components used by multiple pages.

Keeps theme.py for pure styling and data.py for pure business logic.
Components here combine both — they create UI elements with data-driven behavior.
"""

from __future__ import annotations

import asyncio

from nicegui import ui

from scripts.webui import data


def test_ssh_from_table(table: ui.table) -> None:
    """Launch an SSH connectivity test for the selected table row.

    Used by hosts and nodes pages. Expects rows to have an "ip" field.
    """
    sel = table.selected
    if not sel:
        ui.notify("Select a row first.", type="warning")
        return
    ip = sel[0].get("ip", "")
    if not ip or ip == "--":
        ui.notify("No IP address for this entry.", type="warning")
        return

    async def _run() -> None:
        result = await asyncio.to_thread(data.test_ssh_connection, ip)
        if result.success:
            ui.notify(f"SSH to {ip}: OK", type="positive")
        else:
            ui.notify(f"SSH to {ip}: {result.error}", type="negative")

    asyncio.create_task(_run())
