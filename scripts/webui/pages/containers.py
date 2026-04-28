"""Container & VM management page — list, start, stop, restart guests.

Shows all LXC containers and QEMU VMs. Uses Fleet domain objects for
heartbeat-based guest info and falls back to the Manager API for real-time
pct/qm guest lists when available.
"""

from __future__ import annotations

import httpx
from nicegui import ui

from scripts.webui import data, theme
from scripts.webui.api_client import api
from scripts.webui.data import Fleet, Labels, PageTitles, Routes


async def _fetch_guests() -> list[dict]:
    """Fetch the guest list from the local manager API."""
    result = await api.get_json("/api/guests", timeout=15)
    if isinstance(result, dict):
        return result.get("guests", [])
    return []


async def _guest_action(vmid: str, action: str) -> dict:
    """Send a start/stop/restart action to the manager API."""
    try:
        resp = await api.post(f"/api/guests/{vmid}/{action}", timeout=30)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, OSError) as exc:
        return {"success": False, "error": str(exc)}


def _status_color(status: str) -> str:
    status_lower = status.lower()
    if status_lower == "running":
        return theme.COLOR_SUCCESS
    if status_lower == "stopped":
        return theme.TEXT_SECONDARY
    return theme.COLOR_WARNING


def _type_icon(guest_type: str) -> str:
    return "dns" if guest_type == "lxc" else "computer"


async def _render_containers(fleet: Fleet) -> None:
    """Render the container management dashboard."""
    with ui.column().classes("w-full max-w-[1200px] mx-auto px-6 py-6 gap-5"):
        theme.page_header(
            PageTitles.CONTAINERS,
            "Manage all guests on this Proxmox host",
        )

        with ui.row().classes("items-center gap-2"):
            theme.help_tooltip(
                "Shows all LXC containers and QEMU virtual machines running "
                "on this Proxmox host. You can start, stop, or restart any "
                "guest from here. Changes take effect immediately."
            )

        if fleet.has_telemetry and fleet.total_guests > 0:
            _fleet_guest_summary(fleet)

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
                            "The manager could not reach the Proxmox host via PVE API. "
                            "Check that HOST_IP is set in config.json and the PVE API "
                            "token is configured."
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
            Labels.REFRESH, icon="refresh", on_click=refresh,
        ).classes("outline-btn")

        await refresh()


def _fleet_guest_summary(fleet: Fleet) -> None:
    """Show aggregate guest counts from heartbeat telemetry."""
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center gap-3"):
            ui.icon("widgets").classes("text-lg").style(f"color: {theme.ACCENT}")
            theme.card_title("Fleet Guest Overview")
        with ui.row().classes("gap-6 mt-2"):
            theme.stat_value(str(fleet.total_guests), "Total Guests")
            vms = sum(len(h.vms) for h in fleet.hosts)
            cts = sum(len(h.containers) for h in fleet.hosts)
            theme.stat_value(str(vms), "VMs")
            theme.stat_value(str(cts), "Containers")
        if fleet.host_count > 1:
            with ui.row().classes("gap-4 mt-2 flex-wrap"):
                for h in fleet.hosts:
                    if h.guest_count > 0:
                        ui.label(f"{h.name}: {h.guest_count}").classes(
                            "text-xs font-mono"
                        ).style(f"color: {theme.TEXT_SECONDARY}")


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
    @ui.page(Routes.CONTAINERS)
    async def containers_page() -> None:
        import asyncio
        from scripts.webui.app import get_state_dir, load_active_env

        state_dir = get_state_dir()
        env = load_active_env()
        fleet = await asyncio.to_thread(data.build_fleet, env, state_dir)

        with theme.page_shell("containers"):
            ui.add_head_html(theme.HOVER_CARD_STYLES)
            await _render_containers(fleet)
