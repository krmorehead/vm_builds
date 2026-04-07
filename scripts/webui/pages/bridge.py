"""WiFi Bridge detail page — real-time WDS link monitoring.

Shows AP and STA sides of the dedicated WiFi bridge with signal quality,
traffic throughput, and pairing status. Uses the heartbeat subscription
system for on-demand SSH polling.
"""

from __future__ import annotations

from nicegui import ui

from scripts.webui import theme
from scripts.webui.data import get_bridge_nodes
from scripts.webui.heartbeat import signal_quality


def register() -> None:
    @ui.page("/bridge")
    def bridge_page() -> None:
        with theme.page_shell("bridge"):
            _bridge_content()


def _bridge_content() -> None:
    """Render the bridge dashboard with auto-refreshing metrics."""
    from scripts.webui.manager import get_metric_cache, get_subscription_manager, resolve_node_ip

    mgr = get_subscription_manager()
    cache = get_metric_cache()
    bridge_nodes = get_bridge_nodes()

    theme.page_header("WiFi Bridge", "Dedicated WDS link monitoring")
    with ui.row().classes("items-center gap-1"):
        theme.help_tooltip(
            "WDS (Wireless Distribution System) creates a transparent Layer-2 bridge "
            "using 4-address mode. One side is the AP (access point), the other is the "
            "STA (station). Together they extend your wired network over WiFi."
        )

    banner_container = ui.column().classes("w-full")
    cards_container = ui.row().classes("w-full gap-4 flex-wrap")
    traffic_container = ui.column().classes("w-full")
    detail_container = ui.column().classes("w-full")

    tx_history: list[float] = []
    rx_history: list[float] = []

    def _subscribe() -> None:
        for node in bridge_nodes:
            ip = resolve_node_ip(node["node_id"])
            if ip:
                mgr.subscribe(node["node_id"], "bridge", ttl_seconds=30)

    def _refresh() -> None:
        _subscribe()

        node_data = {}
        for node in bridge_nodes:
            cached = cache.get(node["node_id"], "bridge")
            node_data[node["node_id"]] = cached

        banner_container.clear()
        with banner_container:
            _render_link_banner(node_data)

        cards_container.clear()
        with cards_container:
            for node in bridge_nodes:
                _render_node_card(node, node_data.get(node["node_id"]))

        _update_traffic_history(node_data, tx_history, rx_history)
        traffic_container.clear()
        with traffic_container:
            _render_traffic_card(tx_history, rx_history)

        detail_container.clear()
        with detail_container:
            _render_detail_table(node_data)

    _refresh()

    timer = ui.timer(5.0, _refresh)

    with ui.row().classes("gap-3 mt-4"):
        ui.button(
            "Refresh Now", icon="refresh", on_click=_refresh,
        ).classes("outline-btn")
        ui.button(
            "Restart WiFi", icon="restart_alt",
            on_click=lambda: _bridge_action("all"),
        ).classes("outline-btn")
        with ui.element("span"):
            ui.button(
                "Force Re-pair", icon="sync",
                on_click=lambda: _bridge_action("sta"),
            ).classes("outline-btn")
            theme.help_tooltip(
                "Restarts WiFi on the STA side only, forcing it to re-associate "
                "with the AP. Useful when the link is stuck or signal is poor."
            )
        ui.button(
            "Deploy Bridge", icon="rocket_launch",
            on_click=lambda: _deploy_bridge(),
        ).classes("action-btn")


def _render_link_banner(node_data: dict) -> None:
    """Show overall link status: connected/disconnected with signal."""
    ap_data = node_data.get("bridge-1")
    sta_data = node_data.get("bridge-2")

    ap_connected = False
    sta_connected = False
    signal_dbm = None
    uptime_seconds = 0

    if ap_data and ap_data.success:
        stations = ap_data.data.get("stations", [])
        ap_connected = len(stations) > 0
        if stations:
            sig = stations[0].get("signal")
            if sig:
                signal_dbm = int(sig)
            ct = stations[0].get("connected_time", 0)
            uptime_seconds = int(ct) if ct else 0

    if sta_data and sta_data.success:
        stations = sta_data.data.get("stations", [])
        sta_connected = len(stations) > 0
        if not signal_dbm and stations:
            sig = stations[0].get("signal")
            if sig:
                signal_dbm = int(sig)

    linked = ap_connected or sta_connected
    bridge_nodes = get_bridge_nodes()
    ap_label = bridge_nodes[0]["label"] if len(bridge_nodes) > 0 else "Bridge 1"
    sta_label = bridge_nodes[1]["label"] if len(bridge_nodes) > 1 else "Bridge 2"

    with ui.card().classes("w-full"):
        if linked:
            sig_text = ""
            sig_color = theme.ACCENT
            if signal_dbm is not None:
                q = signal_quality(signal_dbm)
                sig_text = f"{signal_dbm} dBm ({q})"
                if signal_dbm > -50:
                    sig_color = "#2dd4bf"
                elif signal_dbm > -60:
                    sig_color = "#4ade80"
                elif signal_dbm > -70:
                    sig_color = "#fbbf24"
                elif signal_dbm > -80:
                    sig_color = "#fb923c"
                else:
                    sig_color = "#ef4444"

            with ui.row().classes("items-center justify-center gap-4 py-3 w-full"):
                with ui.column().classes("items-center gap-0"):
                    ui.icon("cell_tower", size="lg").style(f"color: {theme.ACCENT}")
                    ui.label(f"{ap_label} (AP)").classes("text-xs font-mono").style(
                        f"color: {theme.TEXT_PRIMARY}"
                    )
                with ui.column().classes("items-center gap-0 flex-1"):
                    ui.label(f"◄{'━' * 6} {sig_text} {'━' * 6}►").classes(
                        "text-xs font-mono tracking-tight"
                    ).style(f"color: {sig_color}")
                    time_str = ""
                    if uptime_seconds > 0:
                        mins = uptime_seconds // 60
                        hrs = mins // 60
                        if hrs > 0:
                            time_str = f"linked {hrs}h {mins % 60}m"
                        else:
                            time_str = f"linked {mins}m"
                    if time_str:
                        ui.label(time_str).classes("text-xs").style(
                            f"color: {theme.TEXT_SECONDARY}"
                        )
                with ui.column().classes("items-center gap-0"):
                    ui.icon("router", size="lg").style(f"color: {theme.ACCENT}")
                    ui.label(f"{sta_label} (STA)").classes("text-xs font-mono").style(
                        f"color: {theme.TEXT_PRIMARY}"
                    )
        else:
            with ui.row().classes("items-center justify-center gap-4 py-3 w-full"):
                with ui.column().classes("items-center gap-0"):
                    ui.icon("cell_tower", size="lg").style(f"color: {theme.TEXT_DISABLED}")
                    ui.label(f"{ap_label} (AP)").classes("text-xs font-mono").style(
                        f"color: {theme.TEXT_DISABLED}"
                    )
                ui.label("- - - not linked - - -").classes("text-xs font-mono").style(
                    f"color: {theme.TEXT_DISABLED}"
                )
                with ui.column().classes("items-center gap-0"):
                    ui.icon("router", size="lg").style(f"color: {theme.TEXT_DISABLED}")
                    ui.label(f"{sta_label} (STA)").classes("text-xs font-mono").style(
                        f"color: {theme.TEXT_DISABLED}"
                    )


def _render_node_card(node: dict, cached) -> None:
    """Render a card for one side of the bridge (AP or STA)."""
    label = node["label"]

    with ui.card().classes("flex-1 min-w-[320px]"):
        with ui.row().classes("items-center gap-2 w-full"):
            if cached and cached.success:
                role = cached.data.get("bridge", {}).get("role", node["default_role"])
                paired = cached.data.get("bridge", {}).get("paired", False)
                status = "connected" if paired else "disconnected"
                theme.connection_indicator(status)
                theme.card_title(f"{label} ({role.upper()})")
                theme.help_tooltip(
                    "AP = Access Point (sends the signal). "
                    "STA = Station (receives the signal). "
                    "Paired = both ends see each other."
                )
                ui.space()
                if paired:
                    ui.badge("Paired", color="green").props("outline")
                else:
                    ui.badge("Unpaired", color="red").props("outline")
            else:
                theme.connection_indicator("disconnected")
                theme.card_title(label)
                ui.space()
                ui.badge("No Data", color="grey").props("outline")

        ui.separator().classes("my-2").style(f"background: {theme.ACCENT_DIM}")

        if not cached or not cached.success:
            err = cached.error if cached else "Not reachable"
            ui.label(f"Error: {err}").classes("text-xs").style(
                f"color: {theme.COLOR_ERROR}"
            )
            return

        ifaces = cached.data.get("interfaces", [])
        for iface in ifaces:
            iface_name = iface.get("name", "?")
            iface_type = iface.get("type", "?")
            ssid = iface.get("ssid", "")
            channel = iface.get("channel", "")
            theme.metric_row("Interface", iface_name)
            theme.metric_row("Mode", iface_type)
            if ssid:
                theme.metric_row("SSID", ssid)
            if channel:
                theme.metric_row("Channel", channel)

        stations = cached.data.get("stations", [])
        if stations:
            ui.separator().classes("my-2").style(f"background: {theme.ACCENT_DIM}")
            with ui.row().classes("items-center gap-1"):
                theme.section_label("Link Quality")
                theme.help_tooltip(
                    "Signal: Excellent (> -50 dBm), Good (-50 to -60), "
                    "Fair (-60 to -70), Weak (-70 to -80), Poor (< -80). "
                    "Bitrate is the negotiated link speed, not actual throughput."
                )
            for sta in stations:
                sig = sta.get("signal")
                if sig:
                    q = signal_quality(int(sig))
                    theme.metric_row("Signal", f"{sig} dBm ({q})")
                tx_br = sta.get("tx_bitrate", "")
                rx_br = sta.get("rx_bitrate", "")
                if tx_br:
                    theme.metric_row("TX Bitrate", tx_br)
                if rx_br:
                    theme.metric_row("RX Bitrate", rx_br)
                tx_p = sta.get("tx_packets")
                rx_p = sta.get("rx_packets")
                if tx_p is not None:
                    theme.metric_row("TX Packets", f"{tx_p:,}")
                if rx_p is not None:
                    theme.metric_row("RX Packets", f"{rx_p:,}")
                tx_b = sta.get("tx_bytes")
                rx_b = sta.get("rx_bytes")
                if tx_b is not None:
                    theme.metric_row("TX Bytes", _format_bytes(tx_b))
                if rx_b is not None:
                    theme.metric_row("RX Bytes", _format_bytes(rx_b))
                tx_f = sta.get("tx_failed")
                tx_r = sta.get("tx_retries")
                if tx_f is not None:
                    theme.metric_row("TX Failed", str(tx_f))
                if tx_r is not None:
                    theme.metric_row("TX Retries", str(tx_r))

        ui.label(f"Updated: {cached.collected_at}").classes(
            "text-xs mt-2"
        ).style(f"color: {theme.TEXT_DISABLED}")


def _render_traffic_card(tx_history: list[float], rx_history: list[float]) -> None:
    """Render the traffic throughput sparkline card."""
    with ui.card().classes("w-full"):
        theme.card_title("Traffic")
        theme.card_subtitle("Approximate throughput based on packet counter deltas")
        theme.traffic_sparkline(tx_history, rx_history)


def _render_detail_table(node_data: dict) -> None:
    """Render a combined metrics table for both bridge nodes."""
    bridge_nodes = get_bridge_nodes()
    rows: list[dict] = []
    for node in bridge_nodes:
        cached = node_data.get(node["node_id"])
        if not cached or not cached.success:
            rows.append({
                "node": node["label"],
                "role": "--",
                "paired": "--",
                "signal": "--",
                "tx_rate": "--",
                "rx_rate": "--",
                "updated": cached.error if cached else "unreachable",
            })
            continue

        bridge_info = cached.data.get("bridge", {})
        stations = cached.data.get("stations", [])
        sig = "--"
        tx_rate = "--"
        rx_rate = "--"
        if stations:
            s = stations[0]
            sig_val = s.get("signal", "")
            sig = f"{sig_val} dBm" if sig_val else "--"
            tx_rate = s.get("tx_bitrate", "--")
            rx_rate = s.get("rx_bitrate", "--")

        rows.append({
            "node": node["label"],
            "role": bridge_info.get("role", "--").upper(),
            "paired": "Yes" if bridge_info.get("paired") else "No",
            "signal": sig,
            "tx_rate": tx_rate,
            "rx_rate": rx_rate,
            "updated": cached.collected_at,
        })

    ui.table(
        columns=[
            {"name": "node", "label": "Node", "field": "node", "align": "left"},
            {"name": "role", "label": "Role", "field": "role", "align": "center"},
            {"name": "paired", "label": "Paired", "field": "paired", "align": "center"},
            {"name": "signal", "label": "Signal", "field": "signal", "align": "center"},
            {"name": "tx_rate", "label": "TX Rate", "field": "tx_rate", "align": "center"},
            {"name": "rx_rate", "label": "RX Rate", "field": "rx_rate", "align": "center"},
            {"name": "updated", "label": "Updated", "field": "updated", "align": "center"},
        ],
        rows=rows,
        row_key="node",
    ).classes("w-full")


def _update_traffic_history(
    node_data: dict, tx_history: list[float], rx_history: list[float],
) -> None:
    """Append the latest TX/RX byte totals from station data."""
    total_tx = 0
    total_rx = 0
    for cached in node_data.values():
        if not cached or not cached.success:
            continue
        for sta in cached.data.get("stations", []):
            total_tx += sta.get("tx_bytes", 0)
            total_rx += sta.get("rx_bytes", 0)

    tx_history.append(total_tx / 1024 / 1024)
    rx_history.append(total_rx / 1024 / 1024)
    if len(tx_history) > 60:
        tx_history.pop(0)
    if len(rx_history) > 60:
        rx_history.pop(0)


def _format_bytes(b: int) -> str:
    """Format byte count as human-readable string."""
    if b < 1024:
        return f"{b} B"
    if b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    if b < 1024 * 1024 * 1024:
        return f"{b / 1024 / 1024:.1f} MB"
    return f"{b / 1024 / 1024 / 1024:.2f} GB"


async def _bridge_action(target: str) -> None:
    """Restart WiFi on bridge nodes via the manager API."""
    import httpx

    from scripts.webui.data import get_api_base_url

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{get_api_base_url()}/api/bridge/restart-wifi",
                json={"target": target},
                timeout=20.0,
            )
            if resp.status_code == 200:
                results = resp.json()
                ok_count = sum(1 for r in results.values() if r.get("success"))
                ui.notify(
                    f"WiFi restart: {ok_count}/{len(results)} nodes succeeded",
                    type="positive" if ok_count == len(results) else "warning",
                )
            else:
                ui.notify(f"Restart failed: {resp.status_code}", type="negative")
    except Exception as exc:
        ui.notify(f"Restart failed: {exc}", type="negative")


def _deploy_bridge() -> None:
    """Navigate to the deploy page with the bridge tag pre-selected."""
    from nicegui import app as nicegui_app
    nicegui_app.storage.general["selected_tags"] = ["bridge"]
    ui.navigate.to("/services")
