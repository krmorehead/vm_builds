"""Router detail page — WAN, LAN, WiFi, Firewall, and System status.

Monitors the OpenWrt router VM via the heartbeat subscription system.
Displays interface status, DHCP leases, firewall zones, and system
resource usage.
"""

from __future__ import annotations

from nicegui import ui

from scripts.webui import theme
from scripts.webui.data import format_uptime, get_router_node
from scripts.webui.heartbeat import signal_quality


def register() -> None:
    @ui.page("/router")
    def router_page() -> None:
        with theme.page_shell("router"):
            _router_content()


def _router_content() -> None:
    """Render the router dashboard."""
    from scripts.webui.manager import get_metric_cache, get_subscription_manager, resolve_node_ip

    mgr = get_subscription_manager()
    cache = get_metric_cache()
    router_node = get_router_node()

    theme.page_header("Router", "OpenWrt router status and management")
    with ui.row().classes("items-center gap-1"):
        theme.help_tooltip(
            "This shows your OpenWrt router's status. WAN is your internet connection. "
            "LAN is your local network. The firewall controls what traffic is allowed "
            "between networks. DHCP automatically assigns IP addresses to devices."
        )

    wan_container = ui.column().classes("w-full")
    mid_row = ui.row().classes("w-full gap-4 flex-wrap")
    wifi_container = ui.column().classes("w-full")
    system_container = ui.column().classes("w-full")

    def _subscribe() -> None:
        ip = resolve_node_ip(router_node)
        if ip:
            mgr.subscribe(router_node, "router", ttl_seconds=30)
            mgr.subscribe(router_node, "wifi", ttl_seconds=30)

    def _refresh() -> None:
        _subscribe()
        router_data = cache.get(router_node, "router")
        wifi_data = cache.get(router_node, "wifi")

        wan_container.clear()
        with wan_container:
            _render_wan_card(router_data)

        mid_row.clear()
        with mid_row:
            _render_lan_card(router_data)
            _render_firewall_card(router_data)

        wifi_container.clear()
        with wifi_container:
            _render_wifi_card(wifi_data)

        system_container.clear()
        with system_container:
            _render_system_card(router_data)

    _refresh()
    ui.timer(5.0, _refresh)

    with ui.row().classes("gap-3 mt-4"):
        ui.button(
            "Refresh Now", icon="refresh", on_click=_refresh,
        ).classes("outline-btn")


# ── WAN card ─────────────────────────────────────────────────────────

def _render_wan_card(cached) -> None:
    """WAN interface status card."""
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center gap-2"):
            if cached and cached.success and cached.data.get("wan", {}).get("up") == "true":
                theme.connection_indicator("connected")
                theme.card_title("WAN")
            else:
                theme.connection_indicator("disconnected")
                theme.card_title("WAN")
            theme.help_tooltip("WAN = Wide Area Network -- your connection to the internet.")

        ui.separator().classes("my-2").style(f"background: {theme.ACCENT_DIM}")

        if not cached or not cached.success:
            err = cached.error if cached else "Not reachable"
            ui.label(f"Router unreachable: {err}").classes("text-xs").style(
                f"color: {theme.COLOR_ERROR}"
            )
            return

        wan = cached.data.get("wan", {})
        if wan:
            theme.metric_row("Protocol", wan.get("proto", "--"))
            theme.metric_row("IP Address", wan.get("ip", "--"))
            wan_up = wan.get("up", "")
            status = "Up" if wan_up == "true" else ("Down" if wan_up else "--")
            theme.metric_row("Status", status)
            uptime = wan.get("uptime", "")
            if uptime and uptime.isdigit():
                theme.metric_row("Uptime", format_uptime(int(uptime)))
        else:
            ui.label("No WAN data available").classes("text-xs").style(
                f"color: {theme.TEXT_DISABLED}"
            )


# ── LAN card ─────────────────────────────────────────────────────────

def _render_lan_card(cached) -> None:
    """LAN interface and DHCP card."""
    with ui.card().classes("flex-1 min-w-[320px]"):
        with ui.row().classes("items-center gap-2"):
            theme.card_title("LAN")
            theme.help_tooltip(
                "LAN = Local Area Network -- your home network. "
                "DHCP assigns IP addresses to devices automatically so "
                "you don't have to configure each one manually."
            )
        ui.separator().classes("my-2").style(f"background: {theme.ACCENT_DIM}")

        if not cached or not cached.success:
            ui.label("No data").classes("text-xs").style(
                f"color: {theme.TEXT_DISABLED}"
            )
            return

        lan = cached.data.get("lan", {})
        if lan:
            theme.metric_row("IP Address", lan.get("ip", "--"))
            theme.metric_row("Netmask", lan.get("netmask", "--"))
            dhcp_start = lan.get("dhcp_start", "")
            dhcp_limit = lan.get("dhcp_limit", "")
            if dhcp_start and dhcp_limit:
                theme.metric_row("DHCP Range", f".{dhcp_start} - .{int(dhcp_start) + int(dhcp_limit) - 1}")

        lease_count = cached.data.get("dhcp_lease_count", 0)
        theme.metric_row("Active Leases", str(lease_count))

        leases = cached.data.get("dhcp_leases", [])
        if leases:
            theme.section_label("DHCP Leases")
            rows = [
                {
                    "hostname": l.get("hostname", "?"),
                    "ip": l.get("ip", "?"),
                    "mac": l.get("mac", "?"),
                }
                for l in leases[:10]
            ]
            ui.table(
                columns=[
                    {"name": "hostname", "label": "Host", "field": "hostname", "align": "left"},
                    {"name": "ip", "label": "IP", "field": "ip", "align": "left"},
                    {"name": "mac", "label": "MAC", "field": "mac", "align": "left"},
                ],
                rows=rows,
                row_key="mac",
            ).classes("w-full")


# ── Firewall card ────────────────────────────────────────────────────

def _render_firewall_card(cached) -> None:
    """Firewall zones summary card."""
    with ui.card().classes("flex-1 min-w-[320px]"):
        with ui.row().classes("items-center gap-2"):
            theme.card_title("Firewall")
            theme.help_tooltip(
                "Firewall zones control what traffic is allowed between networks. "
                "Your LAN zone allows devices to talk freely. The WAN zone blocks "
                "unsolicited incoming traffic to keep your network safe."
            )
        ui.separator().classes("my-2").style(f"background: {theme.ACCENT_DIM}")

        if not cached or not cached.success:
            ui.label("No data").classes("text-xs").style(
                f"color: {theme.TEXT_DISABLED}"
            )
            return

        fw = cached.data.get("firewall_zones", "")
        if fw:
            ui.code(fw).classes("w-full text-xs").style(
                f"background: rgba(4, 10, 22, 0.8); color: {theme.TEXT_PRIMARY}; "
                "max-height: 200px; overflow-y: auto;"
            )
        else:
            ui.label("No firewall data").classes("text-xs").style(
                f"color: {theme.TEXT_DISABLED}"
            )

        arp_count = cached.data.get("arp_client_count", 0)
        theme.metric_row("ARP Clients", str(arp_count))


# ── WiFi card ────────────────────────────────────────────────────────

def _render_wifi_card(cached) -> None:
    """WiFi WDS AP status and connected stations."""
    with ui.card().classes("w-full"):
        theme.card_title("WiFi (WDS AP)")
        ui.separator().classes("my-2").style(f"background: {theme.ACCENT_DIM}")

        if not cached or not cached.success:
            err = cached.error if cached else "No WiFi data"
            ui.label(err).classes("text-xs").style(
                f"color: {theme.TEXT_DISABLED}"
            )
            return

        ifaces = cached.data.get("interfaces", [])
        for iface in ifaces:
            with ui.row().classes("items-center gap-2 w-full"):
                ui.icon("wifi", size="sm").style(f"color: {theme.ACCENT}")
                ui.label(iface.get("name", "?")).classes(
                    "text-sm font-mono font-semibold"
                ).style(f"color: {theme.TEXT_PRIMARY}")

            ssid = iface.get("ssid", "")
            channel = iface.get("channel", "")
            mode = iface.get("type", "")
            if ssid:
                theme.metric_row("SSID", ssid)
            if channel:
                theme.metric_row("Channel", channel)
            if mode:
                theme.metric_row("Mode", mode)
            ui.separator().classes("my-1").style(f"background: {theme.ACCENT_DIM}")

        stations = cached.data.get("stations", [])
        if stations:
            theme.section_label(f"{len(stations)} Connected Station{'s' if len(stations) != 1 else ''}")
            for sta in stations:
                mac = sta.get("mac", "?")
                iface_name = sta.get("interface", "")
                sig = sta.get("signal", "")
                tx_br = sta.get("tx_bitrate", "")

                with ui.row().classes("items-center gap-2"):
                    theme.connection_indicator("connected")
                    ui.label(f"{mac}").classes("text-xs font-mono").style(
                        f"color: {theme.TEXT_PRIMARY}"
                    )
                    if iface_name:
                        ui.label(f"on {iface_name}").classes("text-xs").style(
                            f"color: {theme.TEXT_SECONDARY}"
                        )

                if sig:
                    dbm = int(sig)
                    q = signal_quality(dbm)
                    theme.metric_row("Signal", f"{sig} dBm ({q})")
                if tx_br:
                    theme.metric_row("TX Bitrate", tx_br)
        else:
            ui.label("No stations connected").classes("text-xs").style(
                f"color: {theme.TEXT_DISABLED}"
            )


# ── System card ──────────────────────────────────────────────────────

def _render_system_card(cached) -> None:
    """System resource usage card."""
    with ui.card().classes("w-full"):
        theme.card_title("System")
        ui.separator().classes("my-2").style(f"background: {theme.ACCENT_DIM}")

        if not cached or not cached.success:
            ui.label("No data").classes("text-xs").style(
                f"color: {theme.TEXT_DISABLED}"
            )
            return

        sys_info = cached.data.get("system", {})
        if not sys_info:
            ui.label("No system data").classes("text-xs").style(
                f"color: {theme.TEXT_DISABLED}"
            )
            return

        with ui.row().classes("w-full gap-6 flex-wrap"):
            uptime = sys_info.get("uptime_str", "")
            if uptime:
                with ui.column().classes("items-center gap-0"):
                    ui.label(uptime).classes("text-lg font-mono").style(
                        f"color: {theme.TEXT_PRIMARY}"
                    )
                    ui.label("Uptime").classes("text-xs").style(
                        f"color: {theme.TEXT_SECONDARY}"
                    )

            load = sys_info.get("load", "")
            if load:
                with ui.column().classes("items-center gap-0"):
                    ui.label(load).classes("text-lg font-mono").style(
                        f"color: {theme.TEXT_PRIMARY}"
                    )
                    ui.label("Load Avg").classes("text-xs").style(
                        f"color: {theme.TEXT_SECONDARY}"
                    )

            mem_total = sys_info.get("mem_total_kb")
            mem_avail = sys_info.get("mem_avail_kb")
            if mem_total and mem_avail:
                used_pct = round((1 - mem_avail / mem_total) * 100)
                level = "ok" if used_pct < 80 else ("warning" if used_pct < 95 else "critical")
                theme.metric_bar("Memory", used_pct, level)

            disk = sys_info.get("disk_usage")
            if disk:
                disk_pct = int(disk)
                level = "ok" if disk_pct < 80 else ("warning" if disk_pct < 95 else "critical")
                theme.metric_bar("Disk", disk_pct, level)

        if cached.collected_at:
            ui.label(f"Updated: {cached.collected_at}").classes(
                "text-xs mt-2"
            ).style(f"color: {theme.TEXT_DISABLED}")
