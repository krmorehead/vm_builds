"""Fleet Nodes page — overview + per-host detail views.

The list page shows all hosts from the Fleet domain object with live
telemetry. Each card is clickable to open a Proxmox-inspired detail view.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from scripts.webui import data, theme
from scripts.webui.data import Fleet, Host, HostBucket, Labels, PageTitles, Routes
from scripts.webui import manager
from scripts.webui.pages.display_shared import render_app_console_links


def register() -> None:
    @ui.page(Routes.NODES)
    def nodes_page() -> None:
        from scripts.webui.app import get_state_dir, load_active_env

        state_dir = get_state_dir()
        env = load_active_env()

        with theme.page_shell("nodes"):
            theme.page_header(PageTitles.NODES, "Real-time fleet health and service monitoring")

            with ui.column().classes("w-full gap-4") as live_container:
                pass

            _render_live_content(live_container, env, state_dir)

            auto_refresh = ui.timer(
                5.0, lambda: _render_live_content(live_container, env, state_dir)
            )

            with ui.row().classes("items-center gap-3 mt-2"):
                ui.switch("Auto-refresh (5s)", value=True).bind_value(
                    auto_refresh, "active"
                )

    @ui.page(Routes.NODE_DETAIL)
    def node_detail_page(hostname: str) -> None:
        from scripts.webui.app import get_state_dir, load_active_env

        state_dir = get_state_dir()
        env = load_active_env()

        with theme.page_shell("nodes"):
            with ui.column().classes("w-full gap-4") as detail_container:
                pass

            _render_detail(detail_container, hostname, env, state_dir)

            auto_refresh = ui.timer(
                5.0, lambda: _render_detail(detail_container, hostname, env, state_dir)
            )
            with ui.row().classes("items-center gap-3 mt-2"):
                ui.switch("Auto-refresh (5s)", value=True).bind_value(
                    auto_refresh, "active"
                )


# ── Fleet list page ──────────────────────────────────────────────────


def _render_live_content(
    container: ui.column,
    env: dict[str, str],
    state_dir: Path,
    *,
    probe: bool = False,
) -> None:
    fleet = data.build_fleet(env, state_dir, probe=probe)
    container.clear()
    with container:
        nodes = data.load_node_registry(state_dir)
        alerts = data.compute_alerts(nodes)

        _health_banner(fleet)

        if alerts:
            _alerts_panel(alerts)

        theme.section_label(Labels.NODE_STATUS)
        _node_cards_by_bucket(fleet, state_dir)

        if fleet.total_guests > 0:
            theme.section_label(Labels.SERVICE_MATRIX)
            _service_matrix(nodes)

        theme.section_label("Node Details")
        _detail_table(fleet)
    _maybe_kickstart(fleet)


def _health_banner(fleet: Fleet) -> None:
    score = fleet.health_score
    score_color = theme.health_score_color(score)
    with ui.card().classes("w-full"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-4"):
            with ui.row().classes("items-center gap-4"):
                ui.circular_progress(
                    value=score / 100,
                    show_value=False,
                    size="lg",
                ).props(f'color="{score_color}" thickness=0.2')
                with ui.column().classes("gap-0"):
                    ui.label(Labels.FLEET_HEALTH).classes("text-lg font-semibold").style(
                        f"color: {theme.TEXT_PRIMARY}"
                    )
                    ui.label(f"Score: {score}/100").classes(
                        "text-sm font-mono"
                    ).style(f"color: {score_color}")

            with ui.row().classes("gap-8 flex-wrap"):
                theme.stat_value(str(fleet.online_count), "Online")
                if not fleet.has_telemetry and fleet.reachable_count > 0:
                    theme.stat_value(str(fleet.reachable_count), "Reachable")
                theme.stat_value(str(fleet.host_count), "Total")
                theme.stat_value(str(fleet.total_guests), "Guests")

        if fleet.has_telemetry:
            ui.separator().classes("my-2").style(f"background: {theme.ACCENT_DIM}")
            with ui.row().classes("w-full gap-6 flex-wrap"):
                with ui.column().classes("flex-1 min-w-[200px] gap-1"):
                    ui.label("Fleet Disk").classes("text-xs uppercase tracking-wider").style(
                        f"color: {theme.TEXT_SECONDARY}"
                    )
                    theme.metric_bar(
                        "avg", fleet.avg_disk_pct,
                        data.usage_level(fleet.avg_disk_pct),
                    )
                    wd = fleet.worst_disk
                    if wd and wd.disk_pct > 0:
                        ui.label(
                            f"Worst: {wd.name} ({wd.disk_pct:.0f}%)"
                        ).classes("text-xs").style(
                            f"color: {theme.usage_color(data.usage_level(wd.disk_pct))}"
                        )
                with ui.column().classes("flex-1 min-w-[200px] gap-1"):
                    ui.label("Fleet Memory").classes("text-xs uppercase tracking-wider").style(
                        f"color: {theme.TEXT_SECONDARY}"
                    )
                    theme.metric_bar(
                        "avg", fleet.avg_memory_pct,
                        data.usage_level(fleet.avg_memory_pct),
                    )
                    wm = fleet.worst_memory
                    if wm and wm.memory_pct > 0:
                        ui.label(
                            f"Worst: {wm.name} ({wm.memory_pct:.0f}%)"
                        ).classes("text-xs").style(
                            f"color: {theme.usage_color(data.usage_level(wm.memory_pct))}"
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


# ── Node cards (grouped by bucket) ───────────────────────────────────

_BUCKET_LABELS: dict[str, str] = {
    HostBucket.TEST: Labels.BUCKET_TEST,
    HostBucket.LAB: Labels.BUCKET_LAB,
    HostBucket.PRODUCTION: Labels.BUCKET_PRODUCTION,
}

_BUCKET_ORDER = [HostBucket.PRODUCTION, HostBucket.LAB, HostBucket.TEST]


def _node_cards_by_bucket(fleet: Fleet, state_dir: Path) -> None:
    if not fleet.hosts:
        ui.label("No hosts configured").classes("text-sm").style(
            f"color: {theme.TEXT_SECONDARY}"
        )
        return

    _add_host_form(state_dir)

    buckets_present = {h.bucket or HostBucket.DEFAULT for h in fleet.hosts}
    ordered = [b for b in _BUCKET_ORDER if b in buckets_present]
    uncategorized = buckets_present - set(_BUCKET_ORDER)
    ordered.extend(sorted(uncategorized))

    for bucket in ordered:
        hosts = fleet.hosts_by_bucket(bucket)
        if not hosts:
            continue
        label = _BUCKET_LABELS.get(bucket, bucket.title())
        ui.label(f"{label} ({len(hosts)})").classes(
            "text-xs uppercase tracking-wider font-bold mt-4"
        ).style(f"color: {theme.ACCENT}")
        with ui.row().classes("w-full gap-4 flex-wrap"):
            for host in hosts:
                _single_node_card(host, state_dir)


def _add_host_form(state_dir: Path) -> None:
    """Collapsible inline form for manual host registration."""
    with ui.expansion(Labels.ADD_HOST, icon="add_circle_outline").classes(
        "w-full mb-2"
    ):
        name_input = ui.input("Hostname", placeholder="e.g. edge-01").classes("w-64")
        vpn_input = ui.input("VPN IP", placeholder="e.g. 10.0.0.5").classes("w-64")
        ip_input = ui.input("Provisioning IP (optional)", placeholder="e.g. 192.168.1.100").classes("w-64")
        mac_input = ui.input("MAC (optional)", placeholder="aa:bb:cc:dd:ee:ff").classes("w-64")
        bucket_select = ui.select(
            options={
                "": "Auto-detect from IP",
                HostBucket.TEST: Labels.BUCKET_TEST,
                HostBucket.LAB: Labels.BUCKET_LAB,
                HostBucket.PRODUCTION: Labels.BUCKET_PRODUCTION,
            },
            value="",
            label="Bucket",
        ).classes("w-64")
        result_label = ui.label("").classes("text-xs mt-1")
        result_label.set_visibility(False)

        async def _submit() -> None:
            name = name_input.value.strip() if name_input.value else ""
            vpn_ip = vpn_input.value.strip() if vpn_input.value else ""
            if not name or not vpn_ip:
                result_label.text = "Hostname and VPN IP are required"
                result_label.style(f"color: {theme.COLOR_ERROR}")
                result_label.set_visibility(True)
                return
            provisioning_ip = ip_input.value.strip() if ip_input.value else ""
            registry = data.HostRegistry(state_dir)
            rec = registry.register(
                name,
                provisioning_ip or vpn_ip,
                mac=mac_input.value.strip() if mac_input.value else "",
                bucket=bucket_select.value or "",
                vpn_ip=vpn_ip,
                source="manual",
            )
            result_label.text = f"Registered {rec.name} ({rec.bucket}) — VPN: {rec.vpn_ip}"
            result_label.style(f"color: {theme.COLOR_SUCCESS}")
            result_label.set_visibility(True)
            name_input.value = ""
            ip_input.value = ""
            mac_input.value = ""
            vpn_input.value = ""

        ui.button(Labels.REGISTER, icon="add", on_click=_submit).classes(
            "action-btn mt-2"
        ).props("dense")


def _single_node_card(host: Host, state_dir: Path) -> None:
    with ui.card().classes(
        "flex-1 min-w-[260px] max-w-[380px] cursor-pointer hover:brightness-110"
    ).on("click", lambda _, n=host.name: ui.navigate.to(Routes.NODE_DETAIL.replace("{hostname}", n))):
        with ui.row().classes("items-center gap-2 w-full"):
            theme.status_dot(host.status)
            ui.label(host.name).classes("text-base font-semibold font-mono").style(
                f"color: {theme.TEXT_PRIMARY}"
            )
            ui.space()
            if host.version:
                ui.badge(f"v{host.version}", color="blue").props("outline dense")

        with ui.row().classes("gap-4 mt-1"):
            ui.label(host.reachable_ip or "no VPN IP").classes("text-xs font-mono").style(
                f"color: {theme.TEXT_SECONDARY}"
            )
            if host.registered:
                ui.label(host.uptime).classes("text-xs").style(
                    f"color: {theme.TEXT_SECONDARY}"
                )
            ui.label(host.last_seen_relative).classes("text-xs").style(
                f"color: {theme.TEXT_DISABLED}"
            )
        if host.status == "reachable":
            ui.label("API up · no heartbeat").classes("text-xs").style(
                f"color: {theme.COLOR_WARNING}"
            )

        ui.separator().classes("my-2").style(f"background: {theme.ACCENT_DIM}")

        theme.metric_bar("Disk", host.disk_pct, data.usage_level(host.disk_pct))
        theme.metric_bar("Mem", host.memory_pct, data.usage_level(host.memory_pct))

        svc_count = host.guest_count
        svc_text = f"{svc_count} guest{'s' if svc_count != 1 else ''} running"
        ui.label(svc_text).classes("text-xs mt-1").style(
            f"color: {theme.COLOR_SUCCESS if svc_count > 0 else theme.TEXT_DISABLED}"
        )

        if host.telemetry:
            history = data.load_metric_history(state_dir, host.telemetry.node_id, max_entries=30)
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
                "areaStyle": {"color": theme.CHART_AREA_TEAL},
                "name": "Disk",
            },
            {
                "type": "line", "data": mem_data,
                "smooth": True, "symbol": "none",
                "lineStyle": {"width": 1.5, "color": theme.COLOR_INFO},
                "areaStyle": {"color": theme.CHART_AREA_BLUE},
                "name": "Mem",
            },
        ],
        "tooltip": {
            "trigger": "axis",
            "backgroundColor": theme.CHART_BG,
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

    ui.table(columns=columns, rows=rows, row_key="service").classes("w-full")


def _maybe_kickstart(fleet: data.Fleet) -> None:
    """Trigger auto-kickstart for stale hosts in the background."""
    import asyncio

    if not any(not h.online and h.reachable_ip for h in fleet.hosts):
        return
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, data.auto_kickstart_stale_fleet, fleet)


# ── Detail table ─────────────────────────────────────────────────────


def _detail_table(fleet: Fleet) -> None:
    table = ui.table(
        columns=[
            {"name": "hostname", "label": "Hostname", "field": "hostname", "align": "left", "sortable": True},
            {"name": "ip", "label": "IP", "field": "ip", "align": "left"},
            {"name": "status", "label": "Status", "field": "status", "align": "center"},
            {"name": "uptime", "label": "Uptime", "field": "uptime", "align": "center"},
            {"name": "disk", "label": "Disk", "field": "disk", "align": "center"},
            {"name": "memory", "label": "Memory", "field": "memory", "align": "center"},
            {"name": "guests", "label": "Guests", "field": "guests", "align": "center"},
            {"name": "version", "label": "Version", "field": "version", "align": "center"},
            {"name": "last_seen", "label": "Last Seen", "field": "last_seen", "align": "center"},
        ],
        rows=[],
        row_key="hostname",
        selection="single",
    ).classes("w-full")

    rows: list[dict] = []
    for h in fleet.hosts:
        rows.append({
            "hostname": h.name,
            "ip": h.reachable_ip or "--",
            "status": data.format_node_status(h.status),
            "uptime": h.uptime,
            "disk": f"{h.disk_pct:.0f}%" if h.disk_pct > 0 else "--",
            "memory": f"{h.memory_pct:.0f}%" if h.memory_pct > 0 else "--",
            "guests": str(h.guest_count) if h.telemetry else "--",
            "version": h.version or "--",
            "last_seen": h.last_seen_relative,
        })
    table.rows = rows


# ── Per-host detail page ─────────────────────────────────────────────


def _render_detail(
    container: ui.column,
    hostname: str,
    env: dict[str, str],
    state_dir: Path,
) -> None:
    container.clear()
    with container:
        fleet = data.build_fleet(env, state_dir, probe=False)
        host = fleet.get_host(hostname)

        with ui.row().classes("items-center gap-2 w-full"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to(Routes.NODES)).props(
                "flat round dense"
            ).style(f"color: {theme.TEXT_SECONDARY}")
            ui.label(PageTitles.NODE_DETAIL).classes("text-lg font-semibold").style(
                f"color: {theme.TEXT_PRIMARY}"
            )

        if not host:
            ui.label(f"Host '{hostname}' not found").classes("text-sm").style(
                f"color: {theme.COLOR_ERROR}"
            )
            return

        _detail_header(host)
        _detail_resources(host)
        _detail_guests(host)
        _detail_network(host)
        _detail_deploy_history(host)
        _detail_extensions(host)

        if host.telemetry:
            history = data.load_metric_history(state_dir, host.telemetry.node_id, max_entries=60)
            if len(history) >= 2:
                _detail_sparklines(history)


def _detail_header(host: Host) -> None:
    with ui.card().classes("w-full"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-4"):
            with ui.row().classes("items-center gap-3"):
                theme.status_dot(host.status)
                ui.label(host.name).classes("text-xl font-bold font-mono").style(
                    f"color: {theme.TEXT_PRIMARY}"
                )
            with ui.row().classes("items-center gap-2"):
                if host.version:
                    ui.badge(f"v{host.version}", color="blue").props("outline")
                if not host.wol_capable:
                    ui.badge("No WoL", color="orange").props("outline")
                if host.healthy:
                    ui.badge("Healthy", color="green").props("outline")
                else:
                    ui.badge("Unhealthy", color="red").props("outline")

        mgr = manager.try_get_instance()
        if mgr:
            with ui.row().classes("items-center gap-2 mt-2"):
                node_detail_back = Routes.NODE_DETAIL.replace("{hostname}", host.name)
                display_url = mgr.get_child_display_url(host.name)
                if display_url:
                    kiosk_target = Routes.REMOTE_KIOSK.replace("{node_id}", host.name) + f"?back={node_detail_back}"
                    ui.button(
                        Labels.OPEN_KIOSK, icon="cast_connected",
                        on_click=lambda t=kiosk_target: ui.navigate.to(t),
                    ).props("flat dense").style(f"color: {theme.ACCENT}")
                render_app_console_links(host.name, back=node_detail_back)

        if host.status == "reachable":
            ui.label("API reachable — no callhome heartbeat").classes(
                "text-xs mt-1"
            ).style(f"color: {theme.COLOR_WARNING}")

        for warn in host.warnings:
            ui.label(warn).classes("text-xs mt-1").style(f"color: {theme.COLOR_WARNING}")
        for err in host.errors:
            ui.label(err).classes("text-xs mt-1").style(f"color: {theme.COLOR_ERROR}")

        with ui.row().classes("gap-6 mt-2 flex-wrap"):
            _detail_stat("VPN", host.reachable_ip or "--")
            if host.provisioning_ip:
                _detail_stat("Provisioning IP", host.provisioning_ip)
            _detail_stat("Status", host.status.title())
            _detail_stat("Uptime", host.uptime)
            _detail_stat("Last Seen", host.last_seen_relative)
            _detail_stat("Guests", str(host.guest_count))

        can_kickstart = host.reachable or host.reachable_ip
        if can_kickstart and not host.online:
            _kickstart_button(host)


def _kickstart_button(host: Host) -> None:
    """Button to restart callhome on a host's containers via HTTP."""
    result_label = ui.label("").classes("text-xs mt-1")
    result_label.set_visibility(False)

    async def _do_kickstart() -> None:
        btn.disable()
        spinner.set_visibility(True)
        result_label.set_visibility(False)

        import asyncio
        result = await asyncio.get_event_loop().run_in_executor(
            None, data.kickstart_callhome, host,
        )

        spinner.set_visibility(False)
        btn.enable()
        result_label.set_visibility(True)
        if result.success:
            result_label.text = result.message
            result_label.style(f"color: {theme.COLOR_SUCCESS}")
            if result.errors:
                for err in result.errors:
                    ui.label(err).classes("text-xs").style(
                        f"color: {theme.COLOR_WARNING}"
                    )
        else:
            result_label.text = result.message
            result_label.style(f"color: {theme.COLOR_ERROR}")

    with ui.row().classes("items-center gap-2 mt-2"):
        btn = ui.button(
            "Kickstart Heartbeat",
            icon="restart_alt",
            on_click=_do_kickstart,
        ).classes("action-btn").props("dense")
        spinner = ui.spinner(size="sm")
        spinner.set_visibility(False)
        via = host.reachable_ip
        ui.label(f"via {via}").classes("text-xs").style(
            f"color: {theme.TEXT_DISABLED}"
        )


def _detail_stat(label: str, value: str) -> None:
    with ui.column().classes("gap-0"):
        ui.label(label).classes("text-xs uppercase tracking-wider").style(
            f"color: {theme.TEXT_SECONDARY}"
        )
        ui.label(value).classes("text-sm font-mono font-medium").style(
            f"color: {theme.TEXT_PRIMARY}"
        )


def _detail_resources(host: Host) -> None:
    if not host.telemetry:
        return
    with ui.card().classes("w-full"):
        theme.card_title("Resources")
        with ui.row().classes("w-full gap-6 flex-wrap"):
            with ui.column().classes("flex-1 min-w-[200px] gap-1"):
                ui.label("Disk Usage").classes("text-xs uppercase tracking-wider").style(
                    f"color: {theme.TEXT_SECONDARY}"
                )
                theme.metric_bar("Disk", host.disk_pct, data.usage_level(host.disk_pct))
            with ui.column().classes("flex-1 min-w-[200px] gap-1"):
                ui.label("Memory Usage").classes("text-xs uppercase tracking-wider").style(
                    f"color: {theme.TEXT_SECONDARY}"
                )
                theme.metric_bar("Memory", host.memory_pct, data.usage_level(host.memory_pct))


def _detail_guests(host: Host) -> None:
    if not host.guests:
        return
    with ui.card().classes("w-full"):
        theme.card_title(f"Guests ({host.guest_count})")
        ui.table(
            columns=[
                {"name": "vmid", "label": "VMID", "field": "vmid", "align": "left", "sortable": True},
                {"name": "name", "label": "Name", "field": "name", "align": "left"},
                {"name": "type", "label": "Type", "field": "type", "align": "center"},
                {"name": "status", "label": "Status", "field": "status", "align": "center"},
            ],
            rows=[{
                "vmid": g.vmid,
                "name": g.name,
                "type": g.vm_type.upper(),
                "status": "Running" if g.running else "Stopped",
            } for g in sorted(host.guests, key=lambda g: int(g.vmid) if g.vmid.isdigit() else 0)],
            row_key="vmid",
        ).classes("w-full")


def _detail_network(host: Host) -> None:
    ext_net = host.extensions.get("network", {})
    local_ips = host.local_ips
    if not ext_net and not local_ips:
        return
    with ui.card().classes("w-full"):
        theme.card_title("Network")
        if local_ips:
            for ip in local_ips:
                ui.label(ip).classes("text-sm font-mono").style(
                    f"color: {theme.TEXT_PRIMARY}"
                )
        if ext_net:
            ifaces = ext_net.get("interfaces", [])
            if ifaces:
                ui.separator().classes("my-2").style(f"background: {theme.ACCENT_DIM}")
                for iface in ifaces:
                    iface_name = iface if isinstance(iface, str) else iface.get("name", "")
                    if iface_name:
                        ui.label(iface_name).classes("text-xs font-mono").style(
                            f"color: {theme.TEXT_SECONDARY}"
                        )


def _detail_deploy_history(host: Host) -> None:
    if not host.deploys:
        return
    with ui.card().classes("w-full"):
        theme.card_title("Deploy History")
        recent = host.deploys[-10:][::-1]
        with ui.column().classes("gap-1"):
            for r in recent:
                color = data.exit_code_color(r.exit_code)
                label = data.exit_code_label(r.exit_code)
                with ui.row().classes("items-center gap-2"):
                    ui.badge(label, color=color)
                    ui.label(", ".join(r.tags)).classes("text-sm").style(
                        f"color: {theme.TEXT_PRIMARY}"
                    )
                    ui.label(f"{r.timestamp}  ·  {r.duration_seconds}s").classes(
                        "text-xs"
                    ).style(f"color: {theme.TEXT_DISABLED}")


def _detail_extensions(host: Host) -> None:
    exts = host.extensions
    if not exts:
        return
    with ui.card().classes("w-full"):
        theme.card_title("Extensions")
        for ext_name, ext_data in exts.items():
            if ext_name == "network":
                continue
            with ui.expansion(ext_name, icon="extension").classes("w-full"):
                if isinstance(ext_data, dict):
                    for k, v in ext_data.items():
                        ui.label(f"{k}: {v}").classes("text-sm font-mono").style(
                            f"color: {theme.TEXT_PRIMARY}"
                        )
                else:
                    ui.label(str(ext_data)).classes("text-sm font-mono").style(
                        f"color: {theme.TEXT_PRIMARY}"
                    )


def _detail_sparklines(history: list[data.MetricSnapshot]) -> None:
    with ui.card().classes("w-full"):
        theme.card_title("Resource History")
        disk_data = [s.disk_usage_pct for s in history]
        mem_data = [s.memory_usage_pct for s in history]
        ui.echart({
            "grid": {"top": 20, "bottom": 25, "left": 40, "right": 20},
            "xAxis": {"type": "category", "show": False, "data": list(range(len(history)))},
            "yAxis": {"type": "value", "min": 0, "max": 100, "axisLabel": {"formatter": "{value}%"}},
            "legend": {
                "data": ["Disk", "Memory"],
                "textStyle": {"color": theme.TEXT_SECONDARY, "fontSize": 11},
            },
            "series": [
                {
                    "type": "line", "data": disk_data, "name": "Disk",
                    "smooth": True, "symbol": "none",
                    "lineStyle": {"width": 2, "color": theme.ACCENT},
                    "areaStyle": {"color": theme.CHART_AREA_TEAL_STRONG},
                },
                {
                    "type": "line", "data": mem_data, "name": "Memory",
                    "smooth": True, "symbol": "none",
                    "lineStyle": {"width": 2, "color": theme.COLOR_INFO},
                    "areaStyle": {"color": theme.CHART_AREA_BLUE_STRONG},
                },
            ],
            "tooltip": {
                "trigger": "axis",
                "backgroundColor": theme.CHART_BG,
                "borderColor": theme.ACCENT_DIM,
                "textStyle": {"color": theme.TEXT_PRIMARY, "fontSize": 11},
            },
        }).classes("w-full h-48")
