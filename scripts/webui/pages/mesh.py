"""Mesh WiFi detail page — WDS mesh topology and peer monitoring.

Shows the router (AP) and all mesh satellite nodes (STA) with signal
quality, bitrate, and traffic counters. Uses the heartbeat subscription
system for on-demand SSH polling.
"""

from __future__ import annotations

from nicegui import ui

from scripts.webui import theme
from scripts.webui.data import get_mesh_nodes
from scripts.webui.heartbeat import signal_quality


def register() -> None:
    @ui.page("/mesh")
    def mesh_page() -> None:
        with theme.page_shell("mesh"):
            _mesh_content()


def _mesh_content() -> None:
    """Render the mesh network dashboard."""
    from scripts.webui.manager import get_metric_cache, get_subscription_manager, resolve_node_ip

    mgr = get_subscription_manager()
    cache = get_metric_cache()
    mesh_ap, mesh_stas = get_mesh_nodes()
    all_nodes = [mesh_ap] + mesh_stas

    theme.page_header("Mesh Network", "WDS mesh topology and peer status")
    with ui.row().classes("items-center gap-1"):
        theme.help_tooltip(
            "Your mesh network uses WDS (Wireless Distribution System) to connect "
            "multiple nodes wirelessly. The AP (Access Point) is the central hub. "
            "STA (Station) nodes connect to it as satellites. All nodes share the "
            "same network so devices can roam freely."
        )

    topology_container = ui.column().classes("w-full")
    cards_container = ui.row().classes("w-full gap-4 flex-wrap")

    def _subscribe() -> None:
        for node_id in all_nodes:
            ip = resolve_node_ip(node_id)
            if ip:
                mgr.subscribe(node_id, "mesh", ttl_seconds=30)

    def _refresh() -> None:
        _subscribe()

        node_data = {}
        for node_id in all_nodes:
            node_data[node_id] = cache.get(node_id, "mesh")

        topology_container.clear()
        with topology_container:
            _render_topology(node_data)

        cards_container.clear()
        with cards_container:
            for node_id in all_nodes:
                _render_peer_card(node_id, node_data.get(node_id))

    _refresh()
    ui.timer(5.0, _refresh)

    with ui.row().classes("gap-3 mt-4"):
        ui.button(
            "Refresh Now", icon="refresh", on_click=_refresh,
        ).classes("outline-btn")

    batman_container = ui.column().classes("w-full mt-6")
    _render_batman_section(batman_container)


def _render_topology(node_data: dict) -> None:
    """Render a topology overview card showing AP -> STA connections."""
    mesh_ap, mesh_stas = get_mesh_nodes()
    with ui.card().classes("w-full"):
        theme.card_title("Topology")

        ap_data = node_data.get(mesh_ap)
        ap_peers = 0
        if ap_data and ap_data.success:
            ap_peers = len(ap_data.data.get("stations", []))

        with ui.row().classes("w-full items-center justify-center gap-8 py-4 flex-wrap"):
            with ui.column().classes("items-center gap-1"):
                ui.icon("cell_tower", size="xl").style(f"color: {theme.ACCENT}")
                ui.label(mesh_ap).classes("text-sm font-mono font-semibold").style(
                    f"color: {theme.TEXT_PRIMARY}"
                )
                ui.label("WDS AP").classes("text-xs").style(
                    f"color: {theme.TEXT_SECONDARY}"
                )
                ui.badge(f"{ap_peers} peer{'s' if ap_peers != 1 else ''}",
                         color="blue").props("outline")

            for sta_id in mesh_stas:
                sta_data = node_data.get(sta_id)
                connected = False
                sig_dbm = None
                if sta_data and sta_data.success:
                    stations = sta_data.data.get("stations", [])
                    connected = len(stations) > 0
                    if stations:
                        sig = stations[0].get("signal")
                        if sig:
                            sig_dbm = int(sig)

                link_color = theme.COLOR_SUCCESS if connected else theme.COLOR_ERROR
                ui.icon("arrow_forward", size="md").style(f"color: {link_color}")

                with ui.column().classes("items-center gap-1"):
                    icon_name = "wifi" if connected else "wifi_off"
                    ui.icon(icon_name, size="xl").style(
                        f"color: {theme.COLOR_SUCCESS if connected else theme.COLOR_ERROR}"
                    )
                    ui.label(sta_id).classes("text-sm font-mono font-semibold").style(
                        f"color: {theme.TEXT_PRIMARY}"
                    )
                    ui.label("WDS STA").classes("text-xs").style(
                        f"color: {theme.TEXT_SECONDARY}"
                    )
                    if sig_dbm is not None:
                        q = signal_quality(sig_dbm)
                        color = theme.signal_color(q)
                        ui.label(f"{sig_dbm} dBm").classes(
                            "text-xs font-mono"
                        ).style(f"color: {color}")


def _render_peer_card(node_id: str, cached) -> None:
    """Render a detailed card for a single mesh node."""
    mesh_ap, _ = get_mesh_nodes()
    is_ap = node_id == mesh_ap

    with ui.card().classes("flex-1 min-w-[300px]"):
        with ui.row().classes("items-center gap-2 w-full"):
            if cached and cached.success:
                role = cached.data.get("role", "ap" if is_ap else "sta")
                peers = cached.data.get("peers", [])
                has_peers = len(peers) > 0
                status = "connected" if has_peers else "disconnected"
                theme.connection_indicator(status)
                theme.card_title(f"{node_id} ({role.upper()})")
            else:
                theme.connection_indicator("disconnected")
                theme.card_title(node_id)

        ui.separator().classes("my-2").style(f"background: {theme.ACCENT_DIM}")

        if not cached or not cached.success:
            err = cached.error if cached else "Not reachable"
            ui.label(f"Error: {err}").classes("text-xs").style(
                f"color: {theme.COLOR_ERROR}"
            )
            return

        ifaces = cached.data.get("interfaces", [])
        for iface in ifaces:
            ssid = iface.get("ssid", "")
            channel = iface.get("channel", "")
            if ssid:
                theme.metric_row("SSID", ssid)
            if channel:
                theme.metric_row("Channel", channel)
            theme.metric_row("Mode", iface.get("type", "?"))

        peers = cached.data.get("peers", [])
        if peers:
            with ui.row().classes("items-center gap-1"):
                theme.section_label(f"{'Connected Stations' if is_ap else 'AP Connection'}")
                theme.help_tooltip(
                    "Bitrate is the negotiated link speed between nodes -- "
                    "actual throughput is usually lower. Signal quality determines "
                    "how fast and reliable the connection is."
                )
            for peer in peers:
                mac = peer.get("mac", "?")
                sig = peer.get("signal", "")
                tx_br = peer.get("tx_bitrate", "")
                rx_br = peer.get("rx_bitrate", "")
                ct = peer.get("connected_time", "")

                theme.metric_row("Peer MAC", mac)
                if sig:
                    dbm = int(sig)
                    q = signal_quality(dbm)
                    theme.metric_row("Signal", f"{sig} dBm ({q})")
                if tx_br:
                    theme.metric_row("TX Bitrate", tx_br)
                if rx_br:
                    theme.metric_row("RX Bitrate", rx_br)
                if ct:
                    minutes = int(ct) // 60
                    theme.metric_row("Connected", f"{minutes} min")
        elif is_ap:
            ui.label("No stations connected").classes("text-xs").style(
                f"color: {theme.TEXT_DISABLED}"
            )

        ui.label(f"Updated: {cached.collected_at}").classes(
            "text-xs mt-2"
        ).style(f"color: {theme.TEXT_DISABLED}")


# ── Batman Mode section ──────────────────────────────────────────────


def _render_batman_section(container) -> None:
    """Render the Batman Mode section with status, toggle, and originators."""
    with container:
        with ui.card().classes("w-full"):
            with ui.row().classes("items-center gap-2 w-full"):
                ui.icon("hub", size="md").style(f"color: {theme.ACCENT}")
                theme.card_title("Batman Mode")
                theme.help_tooltip(
                    "Batman is a smart routing layer that sits on top of your existing "
                    "WiFi links. It finds the fastest path between any two nodes, and "
                    "automatically reroutes traffic if a link goes down. All your existing "
                    "connections keep working -- batman just makes them smarter."
                )
                ui.space()
                batman_badge = ui.badge("Checking...").props("outline")

            ui.separator().classes("my-2").style(f"background: {theme.ACCENT_DIM}")

            status_column = ui.column().classes("w-full gap-2")
            originators_column = ui.column().classes("w-full mt-3")

            async def _check_status() -> None:
                import httpx
                from scripts.webui.data import get_api_base_url
                try:
                    async with httpx.AsyncClient() as client:
                        resp = await client.get(
                            f"{get_api_base_url()}/api/batman/status",
                            timeout=15.0,
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            any_active = any(
                                v.get("active", False)
                                for v in data.values()
                                if isinstance(v, dict)
                            )
                            active_count = sum(
                                1 for v in data.values()
                                if isinstance(v, dict) and v.get("active")
                            )
                            total = len(data)

                            if any_active:
                                batman_badge.set_text(f"Active ({active_count}/{total} nodes)")
                                batman_badge.props("outline color=green")
                            else:
                                batman_badge.set_text("Inactive")
                                batman_badge.props("outline color=grey")

                            status_column.clear()
                            with status_column:
                                for node_id, info in data.items():
                                    if not isinstance(info, dict):
                                        continue
                                    active = info.get("active", False)
                                    icon_name = "check_circle" if active else "cancel"
                                    color = "#4ade80" if active else theme.TEXT_DISABLED
                                    with ui.row().classes("items-center gap-2"):
                                        ui.icon(icon_name, size="xs").style(f"color: {color}")
                                        ui.label(node_id).classes("text-sm font-mono").style(
                                            f"color: {theme.TEXT_PRIMARY}"
                                        )
                                        if active:
                                            origs = info.get("originators", [])
                                            ui.label(
                                                f"{len(origs)} peer{'s' if len(origs) != 1 else ''}"
                                            ).classes("text-xs").style(
                                                f"color: {theme.TEXT_SECONDARY}"
                                            )
                                        elif info.get("error"):
                                            ui.label("unreachable").classes("text-xs").style(
                                                f"color: {theme.TEXT_DISABLED}"
                                            )

                            originators_column.clear()
                            with originators_column:
                                all_origs = []
                                for node_id, info in data.items():
                                    if isinstance(info, dict) and info.get("active"):
                                        for orig in info.get("originators", []):
                                            orig["from_node"] = node_id
                                            all_origs.append(orig)

                                if all_origs:
                                    with ui.row().classes("items-center gap-1"):
                                        theme.section_label("Originators")
                                        theme.help_tooltip(
                                            "Originators are other mesh nodes that batman knows "
                                            "about. TQ (Transmission Quality) ranges from 0-255 -- "
                                            "higher is better."
                                        )
                                    columns = [
                                        {"name": "node", "label": "From", "field": "from_node", "align": "left"},
                                        {"name": "mac", "label": "Peer MAC", "field": "mac", "align": "left"},
                                        {"name": "tq", "label": "TQ", "field": "tq", "align": "center"},
                                        {"name": "last_seen", "label": "Last Seen", "field": "last_seen", "align": "center"},
                                        {"name": "next_hop", "label": "Next Hop", "field": "next_hop", "align": "left"},
                                        {"name": "interface", "label": "Interface", "field": "interface", "align": "left"},
                                    ]
                                    ui.table(
                                        columns=columns, rows=all_origs,
                                    ).classes("w-full").props("dense flat bordered")
                        else:
                            batman_badge.set_text("Error")
                            batman_badge.props("outline color=red")
                except Exception:
                    batman_badge.set_text("Unavailable")
                    batman_badge.props("outline color=grey")

            async def _toggle_batman(enable: bool) -> None:
                import httpx
                from scripts.webui.data import get_api_base_url
                action = "enable" if enable else "disable"
                try:
                    async with httpx.AsyncClient() as client:
                        resp = await client.post(
                            f"{get_api_base_url()}/api/batman/{action}",
                            timeout=60.0,
                        )
                        if resp.status_code == 200:
                            result = resp.json()
                            ok = result.get("succeeded", 0)
                            total = result.get("total", 0)
                            ui.notify(
                                f"Batman {action}: {ok}/{total} nodes",
                                type="positive" if ok == total else "warning",
                            )
                        else:
                            ui.notify(f"Batman {action} failed: {resp.status_code}", type="negative")
                except Exception as exc:
                    ui.notify(f"Batman {action} failed: {exc}", type="negative")
                await _check_status()

            with ui.row().classes("gap-3 mt-2"):
                ui.button(
                    "Enable Batman", icon="play_arrow",
                    on_click=lambda: _toggle_batman(True),
                ).classes("action-btn")
                ui.button(
                    "Disable Batman", icon="stop",
                    on_click=lambda: _toggle_batman(False),
                ).classes("outline-btn")
                ui.button(
                    "Refresh Status", icon="refresh",
                    on_click=_check_status,
                ).classes("outline-btn")

            ui.run_javascript("", timeout=0)
            ui.timer(0.5, _check_status, once=True)
