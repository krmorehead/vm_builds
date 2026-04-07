"""Fleet Nodes page — comprehensive fleet monitoring dashboard.

Shows fleet health overview, per-node status cards with resource gauges,
alerts panel, service matrix, and metric history sparklines.
Live-updates via periodic timer refresh.
"""

from __future__ import annotations

from nicegui import ui

from scripts.webui import data, theme


def register() -> None:
    @ui.page("/nodes")
    def nodes_page() -> None:
        from scripts.webui.app import get_state_dir

        state_dir = get_state_dir()

        with theme.page_shell("nodes"):
            theme.page_header("Fleet Nodes", "Real-time fleet health and service monitoring")

            with ui.column().classes("w-full gap-4") as live_container:
                pass

            _render_live_content(live_container, state_dir)

            auto_refresh = ui.timer(
                5.0, lambda: _render_live_content(live_container, state_dir)
            )

            with ui.row().classes("items-center gap-3 mt-2"):
                ui.switch("Auto-refresh (5s)", value=True).bind_value(
                    auto_refresh, "active"
                )


def _render_live_content(container: ui.column, state_dir: data.Path) -> None:
    """Re-render the entire live content area (health, alerts, nodes, matrix, table)."""
    container.clear()
    with container:
        nodes_list = data.load_node_registry(state_dir)
        health = data.compute_fleet_health(nodes_list)
        alerts = data.compute_alerts(nodes_list)

        _health_banner(health)

        if alerts:
            _alerts_panel(alerts)

        theme.section_label("Node Status")
        _node_cards(nodes_list, state_dir)

        if any(n.services for n in nodes_list):
            theme.section_label("Service Matrix")
            _service_matrix(nodes_list)

        theme.section_label("Node Details")
        _detail_table(nodes_list)


# ── Health banner ────────────────────────────────────────────────────


def _health_banner(health: data.FleetHealth) -> None:
    score_color = theme.health_score_color(health.health_score)
    with ui.card().classes("w-full"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-4"):
            with ui.row().classes("items-center gap-4"):
                ui.circular_progress(
                    value=health.health_score / 100,
                    show_value=False,
                    size="lg",
                ).props(f'color="{score_color}" thickness=0.2')
                with ui.column().classes("gap-0"):
                    ui.label("Fleet Health").classes("text-lg font-semibold").style(
                        f"color: {theme.TEXT_PRIMARY}"
                    )
                    ui.label(f"Score: {health.health_score}/100").classes(
                        "text-sm font-mono"
                    ).style(f"color: {score_color}")

            with ui.row().classes("gap-8 flex-wrap"):
                theme.stat_value(str(health.online_nodes), "Online")
                theme.stat_value(str(health.total_nodes), "Total")
                theme.stat_value(str(health.total_services), "Services")

        if health.total_nodes > 0:
            ui.separator().classes("my-2").style(f"background: {theme.ACCENT_DIM}")
            with ui.row().classes("w-full gap-6 flex-wrap"):
                with ui.column().classes("flex-1 min-w-[200px] gap-1"):
                    ui.label("Fleet Disk").classes("text-xs uppercase tracking-wider").style(
                        f"color: {theme.TEXT_SECONDARY}"
                    )
                    theme.metric_bar(
                        "avg", health.avg_disk_pct,
                        data.usage_level(health.avg_disk_pct),
                    )
                    if health.worst_disk_pct > 0:
                        ui.label(
                            f"Worst: {health.worst_disk_node} ({health.worst_disk_pct:.0f}%)"
                        ).classes("text-xs").style(
                            f"color: {theme.usage_color(data.usage_level(health.worst_disk_pct))}"
                        )
                with ui.column().classes("flex-1 min-w-[200px] gap-1"):
                    ui.label("Fleet Memory").classes("text-xs uppercase tracking-wider").style(
                        f"color: {theme.TEXT_SECONDARY}"
                    )
                    theme.metric_bar(
                        "avg", health.avg_memory_pct,
                        data.usage_level(health.avg_memory_pct),
                    )
                    if health.worst_memory_pct > 0:
                        ui.label(
                            f"Worst: {health.worst_memory_node} ({health.worst_memory_pct:.0f}%)"
                        ).classes("text-xs").style(
                            f"color: {theme.usage_color(data.usage_level(health.worst_memory_pct))}"
                        )


# ── Alerts panel ─────────────────────────────────────────────────────


def _alerts_panel(alerts: list[data.NodeAlert]) -> None:
    critical = [a for a in alerts if a.severity == "critical"]
    warnings = [a for a in alerts if a.severity == "warning"]
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("notifications_active").classes("text-lg").style(
                f"color: {theme.COLOR_WARNING}"
            )
            theme.card_title(f"Alerts ({len(alerts)})")
        if critical:
            for alert in critical:
                _alert_row(alert)
        if warnings:
            for alert in warnings[:10]:
                _alert_row(alert)
            if len(warnings) > 10:
                ui.label(f"... and {len(warnings) - 10} more warnings").classes(
                    "text-xs mt-1"
                ).style(f"color: {theme.TEXT_DISABLED}")


def _alert_row(alert: data.NodeAlert) -> None:
    color = theme.severity_color(alert.severity)
    icon = theme.severity_icon(alert.severity)
    with ui.row().classes("items-center gap-2 py-1"):
        ui.icon(icon, size="sm").style(f"color: {color}")
        ui.label(alert.hostname).classes("font-mono text-sm font-medium").style(
            f"color: {theme.TEXT_PRIMARY}"
        )
        ui.label(alert.message).classes("text-sm").style(f"color: {color}")


# ── Node cards ───────────────────────────────────────────────────────


def _node_cards(nodes: list[data.RegisteredNode], state_dir: data.Path) -> None:
    if not nodes:
        ui.label("No nodes registered yet").classes("text-sm").style(
            f"color: {theme.TEXT_SECONDARY}"
        )
        return
    with ui.row().classes("w-full gap-4 flex-wrap"):
        for node in nodes:
            _single_node_card(node, state_dir)


def _single_node_card(node: data.RegisteredNode, state_dir: data.Path) -> None:
    with ui.card().classes("flex-1 min-w-[260px] max-w-[380px]"):
        with ui.row().classes("items-center gap-2 w-full"):
            theme.status_dot(node.status)
            ui.label(node.hostname).classes("text-base font-semibold font-mono").style(
                f"color: {theme.TEXT_PRIMARY}"
            )
            ui.space()
            if node.version:
                ui.badge(f"v{node.version}", color="blue").props("outline dense")

        with ui.row().classes("gap-4 mt-1"):
            ui.label(node.last_ip or "no IP").classes("text-xs font-mono").style(
                f"color: {theme.TEXT_SECONDARY}"
            )
            ui.label(data.format_uptime(node.uptime_seconds)).classes("text-xs").style(
                f"color: {theme.TEXT_SECONDARY}"
            )
            ui.label(data.format_last_seen_relative(node.last_seen)).classes(
                "text-xs"
            ).style(f"color: {theme.TEXT_DISABLED}")

        ui.separator().classes("my-2").style(f"background: {theme.ACCENT_DIM}")

        theme.metric_bar(
            "Disk", node.disk_usage_pct,
            data.usage_level(node.disk_usage_pct),
        )
        theme.metric_bar(
            "Mem", node.memory_usage_pct,
            data.usage_level(node.memory_usage_pct),
        )

        svc_count = len(node.services)
        svc_text = f"{svc_count} service{'s' if svc_count != 1 else ''} running"
        ui.label(svc_text).classes("text-xs mt-1").style(
            f"color: {theme.COLOR_SUCCESS if svc_count > 0 else theme.TEXT_DISABLED}"
        )

        history = data.load_metric_history(state_dir, node.node_id, max_entries=30)
        if len(history) >= 2:
            _sparkline(history)


def _sparkline(history: list[data.MetricSnapshot]) -> None:
    disk_data = [s.disk_usage_pct for s in history]
    mem_data = [s.memory_usage_pct for s in history]
    ui.echart({
        "grid": {"top": 5, "bottom": 5, "left": 5, "right": 5},
        "xAxis": {"type": "category", "show": False, "data": list(range(len(history)))},
        "yAxis": {"type": "value", "show": False, "min": 0, "max": 100},
        "series": [
            {
                "type": "line", "data": disk_data,
                "smooth": True, "symbol": "none",
                "lineStyle": {"width": 1.5, "color": theme.ACCENT},
                "areaStyle": {"color": f"rgba(20, 184, 166, 0.08)"},
                "name": "Disk",
            },
            {
                "type": "line", "data": mem_data,
                "smooth": True, "symbol": "none",
                "lineStyle": {"width": 1.5, "color": theme.COLOR_INFO},
                "areaStyle": {"color": f"rgba(96, 165, 250, 0.08)"},
                "name": "Mem",
            },
        ],
        "tooltip": {
            "trigger": "axis",
            "backgroundColor": "rgba(10, 22, 44, 0.9)",
            "borderColor": theme.ACCENT_DIM,
            "textStyle": {"color": theme.TEXT_PRIMARY, "fontSize": 11},
        },
    }).classes("w-full h-16 mt-1")


# ── Service matrix ───────────────────────────────────────────────────


def _service_matrix(nodes: list[data.RegisteredNode]) -> None:
    svc_names, matrix = data.compute_service_matrix(nodes)
    if not svc_names:
        return
    hostnames = [n.hostname for n in nodes]

    columns = [
        {"name": "service", "label": "Service", "field": "service", "align": "left"},
    ]
    for h in hostnames:
        columns.append({"name": h, "label": h, "field": h, "align": "center"})

    rows = []
    for svc_name in svc_names:
        row: dict = {"service": svc_name}
        for h in hostnames:
            entry = matrix[svc_name].get(h)
            if entry and entry.running:
                row[h] = f"\u25cf {entry.vm_type}:{entry.vmid}"
            else:
                row[h] = "\u2014"
        rows.append(row)

    ui.table(
        columns=columns,
        rows=rows,
        row_key="service",
    ).classes("w-full")


# ── Detail table ─────────────────────────────────────────────────────


def _detail_table(nodes: list[data.RegisteredNode]) -> ui.table:
    table = ui.table(
        columns=[
            {"name": "hostname", "label": "Hostname", "field": "hostname", "align": "left", "sortable": True},
            {"name": "ip", "label": "IP", "field": "ip", "align": "left"},
            {"name": "status", "label": "Status", "field": "status", "align": "center"},
            {"name": "uptime", "label": "Uptime", "field": "uptime", "align": "center"},
            {"name": "disk", "label": "Disk", "field": "disk", "align": "center"},
            {"name": "memory", "label": "Memory", "field": "memory", "align": "center"},
            {"name": "services", "label": "Services", "field": "services", "align": "left"},
            {"name": "version", "label": "Version", "field": "version", "align": "center"},
            {"name": "last_seen", "label": "Last Seen", "field": "last_seen", "align": "center"},
        ],
        rows=[],
        row_key="hostname",
        selection="single",
    ).classes("w-full")

    rows: list[dict] = []
    for n in nodes:
        rows.append({
            "hostname": n.hostname,
            "ip": n.last_ip or "--",
            "status": data.format_node_status(n.status),
            "uptime": data.format_uptime(n.uptime_seconds),
            "disk": f"{n.disk_usage_pct}%" if n.disk_usage_pct else "--",
            "memory": f"{n.memory_usage_pct}%" if n.memory_usage_pct else "--",
            "services": ", ".join(n.services) if n.services else "--",
            "version": n.version or "--",
            "last_seen": n.last_seen or "--",
        })
    table.rows = rows
    return table


# ── Refresh (kept for programmatic triggers) ────────────────────────
