"""WiFi Bridge detail page — real-time WDS link monitoring.

Shows AP and STA sides of the dedicated WiFi bridge with signal quality,
traffic throughput, and pairing status. Uses the heartbeat subscription
system for on-demand SSH polling.
"""

from __future__ import annotations

from nicegui import ui

from scripts.webui import theme
from scripts.webui.data import Labels, PageTitles, Routes, get_bridge_nodes
from scripts.webui.heartbeat import signal_quality


def register() -> None:
    @ui.page("/bridge")
    def bridge_page() -> None:
        with theme.page_shell("bridge"):
            _bridge_content()


def _bridge_content() -> None:
    """Render the bridge dashboard with auto-refreshing metrics."""
    from scripts.webui.metric_controller import MetricPageController

    bridge_nodes = get_bridge_nodes()

    theme.page_header(PageTitles.BRIDGE, "Dedicated WDS link monitoring")
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

    def _on_refresh(node_data: dict) -> None:
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

    ctrl = MetricPageController(
        "bridge",
        [n["node_id"] for n in bridge_nodes],
        on_refresh=_on_refresh,
    )
    ctrl.start_timer()

    with ui.row().classes("gap-3 mt-4"):
        ui.button(
            Labels.REFRESH_NOW, icon="refresh", on_click=ctrl.refresh,
        ).classes("outline-btn")
        ui.button(
            Labels.RESTART_WIFI, icon="restart_alt",
            on_click=lambda: _bridge_action("all"),
        ).classes("outline-btn")
        with ui.element("span"):
            ui.button(
                Labels.FORCE_REPAIR, icon="sync",
                on_click=lambda: _bridge_action("sta"),
            ).classes("outline-btn")
            theme.help_tooltip(
                "Restarts WiFi on the STA side only, forcing it to re-associate "
                "with the AP. Useful when the link is stuck or signal is poor."
            )
        ui.button(
            Labels.DEPLOY_BRIDGE, icon="rocket_launch",
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

    link_summary = _extract_link_summary(ap_data, sta_data)

    with ui.card().classes("w-full"):
        if linked:
            sig_text = ""
            sig_color = theme.ACCENT
            if signal_dbm is not None:
                q = signal_quality(signal_dbm)
                sig_text = f"{signal_dbm} dBm ({q})"
                sig_color = theme.signal_color(q)

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
                    if link_summary:
                        ui.label(link_summary).classes("text-xs font-bold").style(
                            f"color: {theme.ACCENT}"
                        )
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
                    ui.badge("Unpaired", color="orange").props("outline")
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

        _render_link_config(cached.data)

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


_BAND_LABELS = {"2g": "2.4 GHz", "5g": "5 GHz", "6g": "6 GHz"}
_WIDTH_LABELS = {"160": "160 MHz", "80": "80 MHz", "40": "40 MHz", "20": "20 MHz"}


def _extract_link_summary(ap_data, sta_data) -> str:
    """Build a one-line summary of the negotiated link config (e.g., '6 GHz HE160 ch1')."""
    for src in (ap_data, sta_data):
        if not src or not src.success:
            continue
        status = src.data.get("script_status", {})
        band = status.get("BAND", "")
        htmode = status.get("HTMODE", "")
        channel = status.get("CHANNEL", "")
        if band and band != "unknown" and htmode and htmode != "unknown":
            band_label = _BAND_LABELS.get(band, band)
            parts = [band_label, htmode]
            if channel and channel not in ("unknown", "auto"):
                parts.append(f"ch{channel}")
            return " · ".join(parts)
    return ""


def _render_link_config(data: dict) -> None:
    """Render the negotiated link configuration section (band, htmode, channel, width)."""
    status = data.get("script_status", {})
    band = status.get("BAND", "")
    htmode = status.get("HTMODE", "")
    channel = status.get("CHANNEL", "")
    width = status.get("WIDTH_MHZ", "")
    driver = status.get("DRIVER", "")
    noscan = status.get("NOSCAN", "")
    power_save = status.get("POWER_SAVE", "")

    if not band or band == "unknown":
        return

    ui.separator().classes("my-2").style(f"background: {theme.ACCENT_DIM}")
    with ui.row().classes("items-center gap-1"):
        theme.section_label("Negotiated Link")
        theme.help_tooltip(
            "Link parameters selected by cross-endpoint negotiation. "
            "Both bridge endpoints report their hardware capabilities, "
            "and the system picks the best shared band, channel width, "
            "and channel for maximum throughput."
        )

    band_label = _BAND_LABELS.get(band, band)
    theme.metric_row("Band", band_label)

    if htmode and htmode != "unknown":
        theme.metric_row("HT Mode", htmode)

    if width:
        width_label = _WIDTH_LABELS.get(width, f"{width} MHz")
        theme.metric_row("Width", width_label)

    if channel and channel not in ("unknown", "auto"):
        theme.metric_row("Channel", str(channel))

    if driver and driver != "unknown":
        theme.metric_row("Driver", driver)

    if noscan == "1":
        theme.metric_row("Co-ex Scan", "Disabled (dedicated link)")
    elif noscan:
        theme.metric_row("Co-ex Scan", "Enabled")

    if power_save and "off" in power_save.lower():
        theme.metric_row("Power Save", "Off (performance)")
    elif power_save and power_save != "unknown":
        theme.metric_row("Power Save", power_save)


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
                "band": "--",
                "htmode": "--",
                "signal": "--",
                "tx_rate": "--",
                "rx_rate": "--",
                "updated": cached.error if cached else "unreachable",
            })
            continue

        bridge_info = cached.data.get("bridge", {})
        status = cached.data.get("script_status", {})
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

        band_raw = status.get("BAND", "--")
        band_display = _BAND_LABELS.get(band_raw, band_raw) if band_raw != "--" else "--"
        htmode_val = status.get("HTMODE", "--")

        rows.append({
            "node": node["label"],
            "role": bridge_info.get("role", "--").upper(),
            "paired": "Yes" if bridge_info.get("paired") else "No",
            "band": band_display,
            "htmode": htmode_val if htmode_val and htmode_val != "unknown" else "--",
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
            {"name": "band", "label": "Band", "field": "band", "align": "center"},
            {"name": "htmode", "label": "HT Mode", "field": "htmode", "align": "center"},
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

    from scripts.webui.api_client import api

    try:
        resp = await api.post(
            "/api/bridge/restart-wifi",
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
    except (httpx.HTTPError, OSError) as exc:
        ui.notify(f"Restart failed: {exc}", type="negative")


def _deploy_bridge() -> None:
    """Navigate to the deploy page with the bridge tag pre-selected."""
    from nicegui import app as nicegui_app
    nicegui_app.storage.general["selected_tags"] = ["bridge"]
    ui.navigate.to(Routes.SERVICES)
