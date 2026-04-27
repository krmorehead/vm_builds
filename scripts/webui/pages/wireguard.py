"""WireGuard VPN status page — peer connectivity and tunnel health.

Renders a dashboard of WireGuard containers across all hosts using
heartbeat extensions for real-time peer counts and tunnel state.
"""

from __future__ import annotations

from nicegui import ui

from scripts.webui import data, theme
from scripts.webui.data import PageTitles


def register() -> None:
    @ui.page("/wireguard")
    def wireguard_page() -> None:
        from scripts.webui.app import get_state_dir

        state_dir = get_state_dir()
        with theme.page_shell("wireguard"):
            _wireguard_content(state_dir)


def _fetch_wireguard_data(state_dir) -> list[dict]:
    """Fetch WireGuard container data from the node registry.

    In the 4-tier model, individual containers are nested inside the
    host-level heartbeat under extensions.containers.
    """
    nodes = data.load_node_registry(state_dir)
    tunnels = []
    for n in nodes:
        if not n.container_health:
            continue
        nested = n.container_health.extensions.get("containers", {})
        for cid, ct in nested.items():
            if "wireguard" not in cid.lower():
                continue
            ext = ct.get("extensions", {})
            wg_ext = ext.get("wireguard", {})
            tunnels.append({
                "node": n.node_id,
                "container_id": cid,
                "ready": ct.get("ready", False),
                "interfaces": wg_ext.get("interfaces", []),
                "peer_count": wg_ext.get("peer_count", 0),
                "status": "up" if wg_ext.get("interfaces") else "down",
                "last_seen": ct.get("last_seen", n.last_seen),
            })
    return tunnels


def _wireguard_content(state_dir) -> None:
    """Render the WireGuard status dashboard."""
    theme.page_header(PageTitles.WIREGUARD, "Tunnel status and peer connectivity")

    status_container = ui.column().classes("w-full gap-4")

    def _refresh() -> None:
        status_container.clear()
        tunnels = _fetch_wireguard_data(state_dir)
        with status_container:
            if not tunnels:
                ui.label("No WireGuard containers found in fleet heartbeats.").style(
                    f"color: {theme.TEXT_DISABLED}"
                )
                return

            up_count = sum(1 for t in tunnels if t["status"] == "up")
            total = len(tunnels)
            total_peers = sum(t["peer_count"] for t in tunnels)

            with ui.row().classes("gap-4 flex-wrap"):
                _stat_card("Tunnels", f"{up_count}/{total}", up_count == total)
                _stat_card("Total Peers", str(total_peers), total_peers > 0)

            for t in sorted(tunnels, key=lambda x: x["node"]):
                _tunnel_card(t)

    ui.timer(0.1, _refresh, once=True)
    ui.timer(30, _refresh)


def _stat_card(label: str, value: str, healthy: bool) -> None:
    """Render a compact stat card."""
    color = theme.COLOR_SUCCESS if healthy else theme.COLOR_WARNING
    with ui.card().classes("p-4").style(
        f"background: {theme.BG_CARD}; border-left: 3px solid {color};"
    ):
        ui.label(value).classes("text-2xl font-bold").style(f"color: {color}")
        ui.label(label).classes("text-xs").style(f"color: {theme.TEXT_SECONDARY}")


def _tunnel_card(tunnel: dict) -> None:
    """Render a single WireGuard tunnel status card."""
    is_up = tunnel["status"] == "up"
    color = theme.COLOR_SUCCESS if is_up else theme.COLOR_ERROR

    with ui.card().classes("w-full p-4").style(
        f"background: {theme.BG_CARD}; border: 1px solid {theme.BORDER};"
    ):
        with ui.row().classes("items-center gap-3 w-full"):
            ui.icon("vpn_lock").classes("text-xl").style(f"color: {color}")
            with ui.column().classes("gap-0 flex-1"):
                ui.label(f"{tunnel['node']}").classes("font-medium").style(
                    f"color: {theme.TEXT_PRIMARY}"
                )
                ui.label(tunnel["container_id"]).classes("text-xs").style(
                    f"color: {theme.TEXT_SECONDARY}"
                )
            if tunnel["interfaces"]:
                for iface in tunnel["interfaces"]:
                    ui.badge(iface, color="green").props("outline")
            ui.badge(
                f"{tunnel['peer_count']} peers",
                color="blue" if tunnel["peer_count"] > 0 else "grey",
            ).props("outline")
            ui.badge(
                "UP" if is_up else "DOWN",
                color="green" if is_up else "red",
            )
