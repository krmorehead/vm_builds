"""WiFi Bridge detail page — real-time WDS link monitoring.

Shows AP and STA sides of the dedicated WiFi bridge with signal quality,
traffic throughput, and pairing status. Includes a setup guide explaining
roles, pairing, and negotiation. Uses the heartbeat subscription system
for on-demand HTTP polling.
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


# ── Page layout ───────────────────────────────────────────────────────


def _bridge_content() -> None:
    """Render the bridge dashboard with auto-refreshing metrics."""
    from scripts.webui.metric_controller import MetricPageController

    bridge_nodes = get_bridge_nodes()

    theme.page_header(PageTitles.BRIDGE, "Dedicated wireless backhaul link")

    _render_setup_guide()

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

    _render_actions(ctrl)


# ── Setup guide (collapsible) ─────────────────────────────────────────


def _render_setup_guide() -> None:
    """Collapsible card explaining how the bridge works and how to set it up."""
    with ui.expansion(
        Labels.BRIDGE_HOW_IT_WORKS, icon="help_outline",
    ).classes("w-full mb-2").style(
        f"background: {theme.BG_CARD}; border: 1px solid {theme.BORDER}; "
        f"border-radius: 8px;"
    ):
        with ui.column().classes("gap-4 pa-3"):
            _render_role_diagram()
            _render_setup_steps()


_ROLE_EXPLANATIONS = {
    "AP": (
        "cell_tower",
        "Broadcaster",
        "Sends the WiFi signal. Plug this mini-PC into your main network "
        "switch. It creates a wireless access point that the receiver connects to.",
    ),
    "STA": (
        "router",
        "Receiver",
        "Receives the WiFi signal and extends your network. Plug this mini-PC "
        "into a switch at the remote location. It connects wirelessly to the "
        "broadcaster and bridges traffic over the cable.",
    ),
}


def _render_role_diagram() -> None:
    """Visual diagram showing AP → WiFi → STA with plain-language labels."""
    bridge_nodes = get_bridge_nodes()
    ap_node = next((n for n in bridge_nodes if n["default_role"] == "ap"), None)
    sta_node = next((n for n in bridge_nodes if n["default_role"] == "sta"), None)
    ap_name = ap_node["label"] if ap_node else "Bridge 1"
    sta_name = sta_node["label"] if sta_node else "Bridge 2"

    with ui.row().classes("items-center justify-center gap-6 py-3 w-full flex-wrap"):
        for role_key, node_name in [("AP", ap_name), ("STA", sta_name)]:
            icon, human_role, description = _ROLE_EXPLANATIONS[role_key]
            with ui.card().classes("pa-3 min-w-[220px] flex-1").style(
                f"border: 1px solid {theme.ACCENT_DIM};"
            ):
                with ui.row().classes("items-center gap-2"):
                    ui.icon(icon, size="md").style(f"color: {theme.ACCENT}")
                    with ui.column().classes("gap-0"):
                        ui.label(f"{node_name} — {human_role}").classes(
                            "text-sm font-semibold"
                        ).style(f"color: {theme.TEXT_PRIMARY}")
                        ui.label(role_key).classes("text-xs font-mono").style(
                            f"color: {theme.TEXT_SECONDARY}"
                        )
                ui.label(description).classes("text-xs mt-2").style(
                    f"color: {theme.TEXT_SECONDARY}"
                )


_SETUP_STEPS = [
    (
        "rocket_launch",
        Labels.BRIDGE_STEP_DEPLOY,
        "Uses Ansible to create and configure bridge containers on both hosts. "
        "The system automatically detects your WiFi hardware capabilities and "
        "negotiates the best band, channel width, and channel.",
    ),
    (
        "autorenew",
        Labels.BRIDGE_STEP_NEGOTIATE,
        "Both endpoints report what their WiFi hardware supports (bands, "
        "channel widths, HE/VHT modes). The system picks the fastest common "
        "configuration — preferring 6 GHz, then 5 GHz, then 2.4 GHz.",
    ),
    (
        "link",
        Labels.BRIDGE_STEP_PAIR,
        "The broadcaster (AP) starts its access point. The receiver (STA) "
        "automatically connects using the shared passphrase. Once paired, "
        "traffic flows transparently between both sides.",
    ),
]


def _render_setup_steps() -> None:
    """Numbered steps showing the deploy → negotiate → pair flow."""
    theme.section_label("Setup Process")
    for i, (icon, step_title, step_desc) in enumerate(_SETUP_STEPS, 1):
        with ui.row().classes("items-start gap-3 py-1"):
            with ui.column().classes("items-center gap-0").style("min-width: 32px;"):
                ui.label(str(i)).classes("text-sm font-bold").style(
                    f"color: {theme.ACCENT}; background: {theme.ACCENT_DIM}; "
                    "border-radius: 50%; width: 28px; height: 28px; "
                    "display: flex; align-items: center; justify-content: center;"
                )
            with ui.column().classes("gap-0 flex-1"):
                with ui.row().classes("items-center gap-1"):
                    ui.icon(icon, size="xs").style(f"color: {theme.ACCENT}")
                    ui.label(step_title).classes("text-sm font-semibold").style(
                        f"color: {theme.TEXT_PRIMARY}"
                    )
                ui.label(step_desc).classes("text-xs").style(
                    f"color: {theme.TEXT_SECONDARY}"
                )


# ── Actions bar ───────────────────────────────────────────────────────


def _render_actions(ctrl) -> None:
    """Render the action buttons with clear labels and tooltips."""
    with ui.row().classes("gap-3 mt-4 flex-wrap"):
        ui.button(
            Labels.REFRESH_NOW, icon="refresh", on_click=ctrl.refresh,
        ).classes("outline-btn")

        with ui.element("span"):
            ui.button(
                Labels.RESTART_WIFI, icon="restart_alt",
                on_click=lambda: _bridge_action("all"),
            ).classes("outline-btn")
            theme.help_tooltip(
                "Restarts WiFi on both the broadcaster and receiver. "
                "Use when the link is unstable or after changing physical locations."
            )

        with ui.element("span"):
            ui.button(
                Labels.FORCE_REPAIR, icon="sync",
                on_click=lambda: _bridge_action("sta"),
            ).classes("outline-btn")
            theme.help_tooltip(
                "Restarts WiFi on the receiver only, forcing it to reconnect "
                "to the broadcaster. Useful when the link is stuck."
            )

        with ui.element("span"):
            ui.button(
                Labels.DEPLOY_BRIDGE, icon="rocket_launch",
                on_click=lambda: _deploy_bridge(),
            ).classes("action-btn")
            theme.help_tooltip(
                "Runs the full bridge deployment: creates containers on both "
                "hosts, negotiates the best WiFi settings, and pairs the link. "
                "Use for initial setup or to rebuild after hardware changes."
            )

        with ui.element("span"):
            ui.button(
                Labels.SWAP_ROLES, icon="swap_horiz",
                on_click=lambda: _swap_roles_dialog(),
            ).classes("outline-btn")
            theme.help_tooltip(
                "Swap which host is the broadcaster (AP) and which is the "
                "receiver (STA). Use when you want to reverse the link direction."
            )


# ── Link banner ───────────────────────────────────────────────────────


_BAND_LABELS = {"2g": "2.4 GHz", "5g": "5 GHz", "6g": "6 GHz"}
_WIDTH_LABELS = {"160": "160 MHz", "80": "80 MHz", "40": "40 MHz", "20": "20 MHz"}


def _render_link_banner(node_data: dict) -> None:
    """Show overall link status: connected/disconnected with signal."""
    bridge_nodes = get_bridge_nodes()
    ap_node = next((n for n in bridge_nodes if n["default_role"] == "ap"), None)
    sta_node = next((n for n in bridge_nodes if n["default_role"] == "sta"), None)
    ap_data = node_data.get(ap_node["node_id"]) if ap_node else None
    sta_data = node_data.get(sta_node["node_id"]) if sta_node else None

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
    ap_label = ap_node["label"] if ap_node else "Bridge 1"
    sta_label = sta_node["label"] if sta_node else "Bridge 2"
    link_summary = _extract_link_summary(ap_data, sta_data)

    with ui.card().classes("w-full"):
        if linked:
            sig_text = ""
            sig_color = theme.ACCENT
            if signal_dbm is not None:
                q = signal_quality(signal_dbm)
                sig_text = f"{signal_dbm} dBm ({q})"
                sig_color = theme.signal_color(q)

            with ui.row().classes(
                "items-center justify-center gap-4 py-3 w-full"
            ):
                with ui.column().classes("items-center gap-0"):
                    ui.icon("cell_tower", size="lg").style(
                        f"color: {theme.ACCENT}"
                    )
                    ui.label(f"{ap_label}").classes(
                        "text-xs font-semibold"
                    ).style(f"color: {theme.TEXT_PRIMARY}")
                    ui.label("Broadcaster").classes("text-xs").style(
                        f"color: {theme.TEXT_SECONDARY}"
                    )
                with ui.column().classes("items-center gap-0 flex-1"):
                    _bar = "\u2501" * 6
                    ui.label(
                        f"\u25c4{_bar} {sig_text} {_bar}\u25ba"
                    ).classes(
                        "text-xs font-mono tracking-tight"
                    ).style(f"color: {sig_color}")
                    if link_summary:
                        ui.label(link_summary).classes(
                            "text-xs font-bold"
                        ).style(f"color: {theme.ACCENT}")
                    time_str = _format_uptime(uptime_seconds)
                    if time_str:
                        ui.label(time_str).classes("text-xs").style(
                            f"color: {theme.TEXT_SECONDARY}"
                        )
                with ui.column().classes("items-center gap-0"):
                    ui.icon("router", size="lg").style(
                        f"color: {theme.ACCENT}"
                    )
                    ui.label(f"{sta_label}").classes(
                        "text-xs font-semibold"
                    ).style(f"color: {theme.TEXT_PRIMARY}")
                    ui.label("Receiver").classes("text-xs").style(
                        f"color: {theme.TEXT_SECONDARY}"
                    )
        else:
            _render_disconnected_banner(ap_label, sta_label, ap_data, sta_data)


def _render_disconnected_banner(
    ap_label: str, sta_label: str, ap_data, sta_data,
) -> None:
    """Render the not-linked banner with diagnostic hints."""
    ap_reachable = ap_data and ap_data.success
    sta_reachable = sta_data and sta_data.success

    with ui.column().classes("items-center gap-2 py-3 w-full"):
        with ui.row().classes("items-center justify-center gap-4 w-full"):
            with ui.column().classes("items-center gap-0"):
                color = theme.COLOR_WARNING if ap_reachable else theme.TEXT_DISABLED
                ui.icon("cell_tower", size="lg").style(f"color: {color}")
                ui.label(ap_label).classes("text-xs font-semibold").style(
                    f"color: {color}"
                )
                status_text = "Online" if ap_reachable else "Offline"
                ui.label(status_text).classes("text-xs").style(
                    f"color: {theme.TEXT_SECONDARY}"
                )
            ui.label("- - - not linked - - -").classes(
                "text-xs font-mono"
            ).style(f"color: {theme.TEXT_DISABLED}")
            with ui.column().classes("items-center gap-0"):
                color = theme.COLOR_WARNING if sta_reachable else theme.TEXT_DISABLED
                ui.icon("router", size="lg").style(f"color: {color}")
                ui.label(sta_label).classes("text-xs font-semibold").style(
                    f"color: {color}"
                )
                status_text = "Online" if sta_reachable else "Offline"
                ui.label(status_text).classes("text-xs").style(
                    f"color: {theme.TEXT_SECONDARY}"
                )

        if not ap_reachable and not sta_reachable:
            ui.label(
                "Both bridge hosts are offline. Deploy the bridge first "
                "using the Deploy Bridge button below."
            ).classes("text-xs text-center mt-1").style(
                f"color: {theme.TEXT_SECONDARY}"
            )
        elif not ap_reachable or not sta_reachable:
            offline = ap_label if not ap_reachable else sta_label
            ui.label(
                f"{offline} is offline. Check that the host is powered on "
                "and the bridge container is running."
            ).classes("text-xs text-center mt-1").style(
                f"color: {theme.TEXT_SECONDARY}"
            )
        else:
            ui.label(
                "Both hosts are online but not paired. "
                "Try Force Re-pair to reconnect the receiver."
            ).classes("text-xs text-center mt-1").style(
                f"color: {theme.TEXT_SECONDARY}"
            )


# ── Node cards ────────────────────────────────────────────────────────


def _render_node_card(node: dict, cached) -> None:
    """Render a card for one side of the bridge (AP or STA)."""
    label = node["label"]
    default_role = node["default_role"]

    with ui.card().classes("flex-1 min-w-[320px]"):
        with ui.row().classes("items-center gap-2 w-full"):
            if cached and cached.success:
                role = cached.data.get("bridge", {}).get("role", default_role)
                paired = cached.data.get("bridge", {}).get("paired", False)
                status = "connected" if paired else "disconnected"
                theme.connection_indicator(status)
                human_role = "Broadcaster" if role == "ap" else "Receiver"
                theme.card_title(f"{label}")
                ui.badge(
                    human_role, color="teal" if role == "ap" else "blue",
                ).props("outline")
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

        ui.separator().classes("my-2").style(
            f"background: {theme.ACCENT_DIM}"
        )

        if not cached or not cached.success:
            err = cached.error if cached else "Not reachable"
            ui.label(f"Error: {err}").classes("text-xs").style(
                f"color: {theme.COLOR_ERROR}"
            )
            return

        _render_link_config(cached.data)
        _render_interfaces(cached.data)
        _render_link_quality(cached.data)

        ui.label(f"Updated: {cached.collected_at}").classes(
            "text-xs mt-2"
        ).style(f"color: {theme.TEXT_DISABLED}")


def _render_interfaces(data: dict) -> None:
    """Render WiFi interface details."""
    ifaces = data.get("interfaces", [])
    if not ifaces:
        return
    ui.separator().classes("my-2").style(f"background: {theme.ACCENT_DIM}")
    theme.section_label("WiFi Interface")
    for iface in ifaces:
        theme.metric_row("Interface", iface.get("name", "?"))
        mode = iface.get("type", "?")
        human_mode = {"AP": "Broadcaster", "managed": "Receiver"}.get(mode, mode)
        theme.metric_row("Mode", f"{human_mode} ({mode})")
        ssid = iface.get("ssid", "")
        if ssid:
            theme.metric_row("SSID", ssid)
        channel = iface.get("channel", "")
        if channel:
            theme.metric_row("Channel", channel)


def _render_link_quality(data: dict) -> None:
    """Render signal strength and bitrate metrics."""
    stations = data.get("stations", [])
    if not stations:
        return
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


# ── Negotiated link config ────────────────────────────────────────────


def _extract_link_summary(ap_data, sta_data) -> str:
    """Build a one-line summary like '6 GHz · HE160 · ch1'."""
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
            return " \u00b7 ".join(parts)
    return ""


def _render_link_config(data: dict) -> None:
    """Render the negotiated link configuration section."""
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
            "The system automatically tested both endpoints' WiFi hardware "
            "and picked the fastest settings they both support. Higher bands "
            "(6 GHz > 5 GHz > 2.4 GHz) and wider channels mean more speed."
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


# ── Traffic card ──────────────────────────────────────────────────────


def _render_traffic_card(
    tx_history: list[float], rx_history: list[float],
) -> None:
    """Render the traffic throughput sparkline card."""
    with ui.card().classes("w-full"):
        theme.card_title("Traffic")
        theme.card_subtitle(
            "Approximate throughput based on packet counter deltas"
        )
        theme.traffic_sparkline(tx_history, rx_history)


# ── Detail table ──────────────────────────────────────────────────────


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
        band_display = (
            _BAND_LABELS.get(band_raw, band_raw) if band_raw != "--" else "--"
        )
        htmode_val = status.get("HTMODE", "--")
        raw_role = bridge_info.get("role", "--")
        role_display = {
            "ap": "Broadcaster",
            "sta": "Receiver",
        }.get(raw_role, raw_role.upper() if raw_role != "--" else "--")

        rows.append({
            "node": node["label"],
            "role": role_display,
            "paired": "Yes" if bridge_info.get("paired") else "No",
            "band": band_display,
            "htmode": (
                htmode_val
                if htmode_val and htmode_val != "unknown"
                else "--"
            ),
            "signal": sig,
            "tx_rate": tx_rate,
            "rx_rate": rx_rate,
            "updated": cached.collected_at,
        })

    ui.table(
        columns=[
            {
                "name": "node", "label": "Node",
                "field": "node", "align": "left",
            },
            {
                "name": "role", "label": "Role",
                "field": "role", "align": "center",
            },
            {
                "name": "paired", "label": "Paired",
                "field": "paired", "align": "center",
            },
            {
                "name": "band", "label": "Band",
                "field": "band", "align": "center",
            },
            {
                "name": "htmode", "label": "HT Mode",
                "field": "htmode", "align": "center",
            },
            {
                "name": "signal", "label": "Signal",
                "field": "signal", "align": "center",
            },
            {
                "name": "tx_rate", "label": "TX Rate",
                "field": "tx_rate", "align": "center",
            },
            {
                "name": "rx_rate", "label": "RX Rate",
                "field": "rx_rate", "align": "center",
            },
            {
                "name": "updated", "label": "Updated",
                "field": "updated", "align": "center",
            },
        ],
        rows=rows,
        row_key="node",
    ).classes("w-full")


# ── Helpers ───────────────────────────────────────────────────────────


def _update_traffic_history(
    node_data: dict,
    tx_history: list[float],
    rx_history: list[float],
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


def _format_uptime(seconds: int) -> str:
    """Format uptime seconds into a human-readable string."""
    if seconds <= 0:
        return ""
    mins = seconds // 60
    hrs = mins // 60
    if hrs > 0:
        return f"linked {hrs}h {mins % 60}m"
    return f"linked {mins}m"


# ── Actions ───────────────────────────────────────────────────────────


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


async def _swap_roles() -> None:
    """Switch AP and STA roles on both bridge endpoints."""
    import httpx

    from scripts.webui.api_client import api

    bridge_nodes = get_bridge_nodes()
    results: list[str] = []
    for node in bridge_nodes:
        current_role = node["default_role"]
        new_role = "sta" if current_role == "ap" else "ap"
        try:
            resp = await api.post(
                f"/api/wifi/mode/{node['node_id']}/{new_role}",
                timeout=30.0,
            )
            if resp.status_code == 200:
                r = resp.json()
                if r.get("success"):
                    results.append(
                        f"{node['label']}: {current_role} \u2192 {new_role}"
                    )
                else:
                    results.append(f"{node['label']}: failed")
            else:
                results.append(f"{node['label']}: error {resp.status_code}")
        except (httpx.HTTPError, OSError) as exc:
            results.append(f"{node['label']}: {exc}")

    ui.notify(
        "Role swap: " + ", ".join(results),
        type="positive" if all("→" in r for r in results) else "warning",
    )


def _swap_roles_dialog() -> None:
    """Show a confirmation dialog before swapping AP/STA roles."""
    bridge_nodes = get_bridge_nodes()
    ap_node = next(
        (n for n in bridge_nodes if n["default_role"] == "ap"), None,
    )
    sta_node = next(
        (n for n in bridge_nodes if n["default_role"] == "sta"), None,
    )
    if not ap_node or not sta_node:
        ui.notify("Cannot determine current roles", type="warning")
        return

    with ui.dialog() as dialog, ui.card().style(
        f"background: {theme.BG_CARD}; min-width: 350px;"
    ):
        theme.card_title("Swap Bridge Roles")
        ui.label(
            f"This will switch {ap_node['label']} from Broadcaster to "
            f"Receiver, and {sta_node['label']} from Receiver to Broadcaster."
        ).classes("text-sm my-2").style(f"color: {theme.TEXT_SECONDARY}")
        ui.label(
            "The link will briefly disconnect while the roles are swapped."
        ).classes("text-xs").style(f"color: {theme.COLOR_WARNING}")
        with ui.row().classes("justify-end gap-2 mt-3"):
            ui.button(Labels.CANCEL, on_click=dialog.close).classes(
                "outline-btn"
            )
            ui.button(
                "Swap Roles",
                icon="swap_horiz",
                on_click=lambda: (dialog.close(), _do_swap()),
            ).classes("action-btn")

    dialog.open()


def _do_swap() -> None:
    """Execute the role swap asynchronously."""
    import asyncio

    asyncio.ensure_future(_swap_roles())
