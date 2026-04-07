"""Container & VM management page — list, start, stop, restart guests.

Provides a real-time view of all LXC containers and QEMU VMs on the
Proxmox host. The manager SSHes to the host and runs pct/qm commands.
"""

from __future__ import annotations

import httpx
from nicegui import ui

from scripts.webui import theme
from scripts.webui.data import get_api_base_url


async def _fetch_guests() -> list[dict]:
    """Fetch the guest list from the local manager API."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{get_api_base_url()}/api/guests", timeout=15)
            if resp.status_code == 200:
                return resp.json().get("guests", [])
    except Exception:
        pass
    return []


async def _guest_action(vmid: str, action: str) -> dict:
    """Send a start/stop/restart action to the manager API."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{get_api_base_url()}/api/guests/{vmid}/{action}", timeout=30,
            )
            return resp.json()
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _status_color(status: str) -> str:
    status_lower = status.lower()
    if status_lower == "running":
        return theme.COLOR_SUCCESS
    if status_lower == "stopped":
        return theme.COLOR_ERROR
    return theme.COLOR_WARNING


def _type_icon(guest_type: str) -> str:
    return "dns" if guest_type == "lxc" else "computer"


async def _render_containers() -> None:
    """Render the container management dashboard."""
    with ui.column().classes("w-full max-w-[1200px] mx-auto px-6 py-6 gap-5"):
        theme.page_header(
            "Containers & VMs",
            "Manage all guests on this Proxmox host",
        )

        with ui.row().classes("items-center gap-2"):
            theme.help_tooltip(
                "Shows all LXC containers and QEMU virtual machines running "
                "on this Proxmox host. You can start, stop, or restart any "
                "guest from here. Changes take effect immediately."
            )

        guest_container = ui.column().classes("w-full gap-3")

        async def refresh() -> None:
            guest_container.clear()
            guests = await _fetch_guests()
            if not guests:
                with guest_container:
                    with ui.card().classes("w-full"):
                        ui.label("No guests found or host unreachable").style(
                            f"color: {theme.TEXT_SECONDARY}"
                        )
                        theme.help_tooltip(
                            "The manager could not reach the Proxmox host via SSH. "
                            "Check that HOST_IP is set in config.json and the kiosk's "
                            "SSH key is authorized on the host."
                        )
                return

            cts = [g for g in guests if g["type"] == "lxc"]
            vms = [g for g in guests if g["type"] == "qemu"]

            with guest_container:
                if cts:
                    theme.section_label("LXC Containers")
                    for guest in sorted(cts, key=lambda g: int(g["vmid"])):
                        _render_guest_card(guest, refresh)
                if vms:
                    theme.section_label("QEMU Virtual Machines")
                    for guest in sorted(vms, key=lambda g: int(g["vmid"])):
                        _render_guest_card(guest, refresh)

                with ui.row().classes("w-full justify-between items-center mt-2"):
                    ct_running = sum(1 for g in cts if g["status"].lower() == "running")
                    vm_running = sum(1 for g in vms if g["status"].lower() == "running")
                    ui.label(
                        f"{ct_running}/{len(cts)} containers running  ·  "
                        f"{vm_running}/{len(vms)} VMs running"
                    ).classes("text-xs").style(f"color: {theme.TEXT_DISABLED}")

        ui.button(
            "Refresh", icon="refresh", on_click=refresh,
        ).classes("outline-btn")

        await refresh()


def _render_guest_card(guest: dict, refresh_callback) -> None:
    vmid = guest["vmid"]
    name = guest.get("name", "unknown")
    status = guest.get("status", "unknown")
    guest_type = guest.get("type", "lxc")
    color = _status_color(status)

    card_style = (
        f"background: {theme.BG_CARD}; "
        f"border: 1px solid {theme.BORDER}; "
        f"border-left: 4px solid {color} !important; "
        "border-radius: 10px; padding: 0.75rem 1.25rem;"
    )

    with ui.element("div").style(card_style).classes("w-full"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-3"):
            with ui.row().classes("items-center gap-3"):
                ui.icon(_type_icon(guest_type)).style(f"color: {color}")
                with ui.column().classes("gap-0"):
                    ui.label(name).classes("text-sm font-medium").style(
                        f"color: {theme.TEXT_PRIMARY}"
                    )
                    ui.label(
                        f"VMID {vmid}  ·  {guest_type.upper()}"
                    ).classes("text-xs font-mono").style(
                        f"color: {theme.TEXT_SECONDARY}"
                    )

            with ui.row().classes("items-center gap-2"):
                ui.badge(status.capitalize()).classes("text-xs").props(
                    f'outline color="{color}"'
                )

                is_running = status.lower() == "running"
                if is_running:
                    ui.button(
                        icon="restart_alt",
                        on_click=lambda v=vmid: _do_action(v, "restart", refresh_callback),
                    ).props("flat dense round").tooltip("Restart").style(
                        f"color: {theme.COLOR_WARNING}"
                    )
                    ui.button(
                        icon="stop",
                        on_click=lambda v=vmid: _do_action(v, "stop", refresh_callback),
                    ).props("flat dense round").tooltip("Stop").style(
                        f"color: {theme.COLOR_ERROR}"
                    )
                else:
                    ui.button(
                        icon="play_arrow",
                        on_click=lambda v=vmid: _do_action(v, "start", refresh_callback),
                    ).props("flat dense round").tooltip("Start").style(
                        f"color: {theme.COLOR_SUCCESS}"
                    )


async def _do_action(vmid: str, action: str, refresh_callback) -> None:
    ui.notify(f"{action.capitalize()}ing VMID {vmid}...", type="info")
    result = await _guest_action(vmid, action)
    if result.get("success"):
        ui.notify(f"VMID {vmid} {action} succeeded", type="positive")
    else:
        ui.notify(
            f"VMID {vmid} {action} failed: {result.get('error', result.get('output', 'unknown'))}",
            type="negative",
        )
    await refresh_callback()


def register() -> None:
    @ui.page("/containers")
    async def containers_page() -> None:
        with theme.page_shell("containers"):
            ui.add_head_html(theme.HOVER_CARD_STYLES)
            await _render_containers()
