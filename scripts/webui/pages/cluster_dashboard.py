"""Cluster Manager dashboard — fleet view scoped to this cluster's child Managers.

Unlike the SuperManager dashboard (dashboard.py), this page does not depend on
env files, state directories, or nodes.json. All data comes from the
ClusterManager's in-memory _fleet_nodes registry (populated by child Manager
heartbeats).
"""

from __future__ import annotations

from nicegui import ui

from scripts.webui import manager, theme
from scripts.webui.data import Labels, PageTitles, Routes, format_uptime, usage_level
from scripts.webui.pages.display_shared import render_app_console_links


def register() -> None:
    @ui.page(Routes.FLEET)
    def cluster_fleet_page() -> None:
        with theme.cluster_page_shell("fleet"):
            theme.page_header(PageTitles.CLUSTER_FLEET, "Nodes managed by this cluster")

            with ui.column().classes("w-full gap-4") as fleet_container:
                pass

            _render_fleet(fleet_container)

            auto_refresh = ui.timer(5.0, lambda: _render_fleet(fleet_container))

            with ui.row().classes("items-center gap-3 mt-2"):
                ui.switch(Labels.AUTO_REFRESH, value=True).bind_value(
                    auto_refresh, "active",
                )

    @ui.page(Routes.FLEET_DETAIL)
    def cluster_node_detail(node_id: str) -> None:
        with theme.cluster_page_shell("fleet"):
            mgr = manager.get_instance()
            if not mgr.supports_fleet:
                ui.label(Labels.NOT_CLUSTER_MANAGER).classes("text-xl")
                return

            nodes = mgr.get_fleet_nodes()
            entry = nodes.get(node_id)
            if not entry:
                theme.page_header(f"Node: {node_id}", Labels.NOT_FOUND_IN_CLUSTER)
                ui.button(Labels.BACK_TO_FLEET, icon="arrow_back",
                          on_click=lambda: ui.navigate.to(Routes.FLEET)).classes("mt-4")
                return

            payload = entry.get("payload", {})
            hostname = payload.get("hostname", node_id)
            theme.page_header(f"Node: {hostname}", f"Detail view for {node_id}")

            _display_url = mgr.get_child_display_url(node_id)
            if _display_url is not None:
                _fleet_detail_back = Routes.FLEET_DETAIL.replace("{node_id}", node_id)
                _kiosk_target = Routes.REMOTE_KIOSK.replace("{node_id}", node_id) + f"?back={_fleet_detail_back}"
                with ui.row().classes("items-center gap-2 mb-2"):
                    ui.button(
                        Labels.OPEN_KIOSK, icon="cast",
                        on_click=lambda t=_kiosk_target: ui.navigate.to(t),
                    ).classes("action-btn")

            with ui.card().classes("w-full"):
                theme.card_title("Resources")
                disk = payload.get("disk_usage_pct", 0)
                mem = payload.get("memory_usage_pct", 0)
                uptime = payload.get("uptime_seconds", 0)
                with ui.row().classes("gap-6"):
                    _resource_gauge("Disk", disk)
                    _resource_gauge("Memory", mem)
                    ui.label(f"Uptime: {format_uptime(uptime)}").classes("text-sm")

            ips = payload.get("local_ips", [])
            if ips:
                with ui.card().classes("w-full mt-4"):
                    theme.card_title("Network")
                    for ip in ips:
                        ui.label(ip).classes("font-mono text-sm")

            ch = payload.get("container_health")
            if ch and isinstance(ch, dict):
                containers = ch.get("extensions", {}).get("containers", {})
                if containers:
                    with ui.card().classes("w-full mt-4"):
                        theme.card_title("Containers")
                        for ct_name, ct_data in containers.items():
                            ready = ct_data.get("ready", False)
                            dot = theme.COLOR_SUCCESS if ready else theme.COLOR_ERROR
                            with ui.row().classes("items-center gap-2 mt-1"):
                                ui.icon("circle", size="8px").style(f"color: {dot}")
                                ui.label(ct_name).classes("font-mono text-sm")
                                if ready:
                                    ui.badge("ready", color="green").props("outline")
                                else:
                                    ui.badge("not ready", color="red").props("outline")

            last_seen = entry.get("received_at", "unknown")
            ui.label(f"Last heartbeat: {last_seen}").classes("text-xs mt-4").style(
                f"color: {theme.TEXT_SECONDARY}",
            )
            ui.button(Labels.BACK_TO_FLEET, icon="arrow_back",
                      on_click=lambda: ui.navigate.to(Routes.FLEET)).classes("mt-4")


def _render_fleet(container: ui.column) -> None:
    container.clear()
    with container:
        mgr = manager.get_instance()
        if not mgr.supports_fleet:
            ui.label("Not a Cluster Manager").classes("text-xl")
            return

        nodes = mgr.get_fleet_nodes()
        if not nodes:
            with ui.card().classes("w-full"):
                theme.card_title("Fleet")
                ui.label("No child Managers have checked in yet.").classes(
                    "text-sm",
                ).style(f"color: {theme.TEXT_SECONDARY}")
            return

        with ui.card().classes("w-full"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("device_hub").classes("text-xl").style(f"color: {theme.ACCENT}")
                theme.card_title("Fleet")
                ui.badge(f"{len(nodes)} nodes").props("outline")

        for nid, entry in nodes.items():
            payload = entry.get("payload", {})
            hostname = payload.get("hostname", nid)
            disk = payload.get("disk_usage_pct", 0)
            mem = payload.get("memory_usage_pct", 0)
            ips = payload.get("local_ips", [])
            last_seen = entry.get("received_at", "")

            with ui.card().classes("w-full cursor-pointer").on(
                "click", lambda _, n=nid: ui.navigate.to(Routes.FLEET_DETAIL.replace("{node_id}", n)),
            ):
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("circle", size="8px").style(
                            f"color: {theme.COLOR_SUCCESS}",
                        )
                        ui.label(hostname).classes("font-mono font-bold")
                        if ips:
                            ui.label(ips[0]).classes("font-mono text-xs").style(
                                f"color: {theme.TEXT_SECONDARY}",
                            )
                    with ui.row().classes("items-center gap-4"):
                        ui.label(f"D:{disk:.0f}%").classes("text-xs")
                        ui.label(f"M:{mem:.0f}%").classes("text-xs")
                        if mgr.get_child_display_url(nid) is not None:
                            kiosk_target = Routes.REMOTE_KIOSK.replace("{node_id}", nid) + f"?back={Routes.FLEET}"
                            with ui.link(
                                target=kiosk_target,
                            ).style("text-decoration: none;").on(
                                "click.stop", lambda: None,
                            ):
                                ui.icon("cast_connected").style(
                                    f"color: {theme.ACCENT}; cursor: pointer;"
                                )
                        render_app_console_links(nid, back=Routes.FLEET)

                ch = payload.get("container_health")
                if ch and isinstance(ch, dict):
                    containers = ch.get("extensions", {}).get("containers", {})
                    if containers:
                        ready_count = sum(
                            1 for c in containers.values() if c.get("ready")
                        )
                        with ui.row().classes("gap-2 mt-1"):
                            ui.label(
                                f"{ready_count}/{len(containers)} containers ready",
                            ).classes("text-xs").style(
                                f"color: {theme.TEXT_SECONDARY}",
                            )

                if last_seen:
                    ui.label(f"Last seen: {last_seen}").classes("text-xs").style(
                        f"color: {theme.TEXT_DISABLED}",
                    )


def _resource_gauge(label: str, pct: float) -> None:
    color = theme.usage_color(usage_level(pct))
    with ui.column().classes("items-center"):
        ui.circular_progress(
            value=pct / 100, show_value=False, size="60px", color=color,
        )
        ui.label(f"{pct:.0f}%").classes("text-sm font-bold")
        ui.label(label).classes("text-xs").style(f"color: {theme.TEXT_SECONDARY}")


