"""Dashboard home page — thin UI layer over Fleet/Host domain objects."""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from scripts.webui import data, theme
from scripts.webui.data import Fleet, HostBucket, Labels, PageTitles, Routes


def register() -> None:
    @ui.page("/")
    def dashboard_page() -> None:
        from scripts.webui.app import get_env_path, get_images_dir, get_state_dir, load_active_env

        env_path = get_env_path()
        images_dir = get_images_dir()
        state_dir = get_state_dir()
        env = load_active_env()
        fleet = data.build_fleet(env, state_dir)

        with theme.page_shell("dashboard"):
            _env_banner(env_path)
            theme.page_header(PageTitles.DASHBOARD, "Build Menu Dashboard")

            with ui.row().classes("w-full gap-4 flex-wrap"):
                _host_card(fleet)
                _image_card(images_dir)
                _deploy_card(fleet)

            with ui.column().classes("w-full") as fleet_container:
                _fleet_card(fleet, state_dir)

            ui.timer(
                10.0,
                lambda: _refresh_fleet(fleet_container, env, state_dir),
            )

            _history_section(fleet)

            theme.section_label("Quick Actions")
            with ui.row().classes("gap-3"):
                ui.button(
                    Labels.FULL_DEPLOY,
                    icon="rocket_launch",
                    on_click=lambda: _full_deploy(),
                ).classes("action-btn")
                ui.button(
                    Labels.BUILD_IMAGES,
                    icon="build",
                    on_click=lambda: ui.navigate.to(Routes.IMAGES),
                ).classes("outline-btn")
                ui.button(
                    Labels.CHECK_HOSTS,
                    icon="dns",
                    on_click=lambda: ui.navigate.to(Routes.HOSTS),
                ).classes("outline-btn")
                ui.button(
                    PageTitles.TIMELINE,
                    icon="timeline",
                    on_click=lambda: ui.navigate.to(Routes.TIMELINE),
                ).classes("outline-btn")


def _refresh_fleet(
    container: ui.column,
    env: dict[str, str],
    state_dir: Path,
) -> None:
    container.clear()
    with container:
        fleet = data.build_fleet(env, state_dir)
        _fleet_card(fleet, state_dir)


def _env_banner(env_path: Path) -> None:
    name = env_path.name
    if name == ".env":
        ui.badge("PRODUCTION (.env)", color="red").classes("text-sm px-4 py-1")
    elif "test" in name:
        ui.badge("TEST (test.env)", color="blue").classes("text-sm px-4 py-1")
    else:
        ui.badge(name, color="grey").classes("text-sm px-4 py-1")


def _full_deploy() -> None:
    from nicegui import app as nicegui_app
    profiles = data.get_deploy_profiles()
    full = next((p for p in profiles if p.name == Labels.FULL_DEPLOY), None)
    if full:
        nicegui_app.storage.general["selected_tags"] = full.tags
    ui.navigate.to(Routes.SERVICES)


def _host_card(fleet: Fleet) -> None:
    """Hosts with live status indicators from telemetry."""
    with ui.card().classes("flex-1 min-w-[280px]"):
        with ui.row().classes("items-center justify-between w-full"):
            theme.card_title("Hosts")
            theme.card_subtitle(f"{fleet.host_count} configured")
            if fleet.has_telemetry:
                ui.badge(
                    f"{fleet.online_count}/{fleet.host_count} online",
                    color="green" if fleet.online_count == fleet.host_count else "orange",
                ).props("outline")
        for h in fleet.hosts:
            with ui.row().classes("items-center gap-2 mt-1"):
                dot_color = theme.status_color(h.status)
                ui.icon("circle", size="8px").style(f"color: {dot_color}")
                host_label = ui.label(h.name).classes("font-mono text-sm cursor-pointer")
                host_label.style(f"color: {theme.TEXT_PRIMARY}")
                host_name = h.name
                host_label.on("click", lambda _, n=host_name: ui.navigate.to(f"/nodes/{n}"))
                ui.label(h.ip).classes("text-xs").style(f"color: {theme.TEXT_SECONDARY}")
                if h.telemetry:
                    ui.label(f"D:{h.disk_pct:.0f}%").classes("text-xs").style(
                        f"color: {theme.usage_color(data.usage_level(h.disk_pct))}"
                    )
                    ui.label(f"M:{h.memory_pct:.0f}%").classes("text-xs").style(
                        f"color: {theme.usage_color(data.usage_level(h.memory_pct))}"
                    )
                if h.is_lan:
                    ui.badge("LAN", color="blue").props("outline")
                if not h.wol_capable:
                    ui.badge("No WoL", color="orange").props("outline")


def _image_card(images_dir: Path) -> None:
    imgs = data.get_image_status(images_dir)
    built = sum(1 for i in imgs if i.exists)
    total = len(imgs)
    color = "green" if built == total else "amber" if built > 0 else "red"
    with ui.card().classes("flex-1 min-w-[280px]"):
        theme.card_title("Images")
        ui.badge(f"{built}/{total} built", color=color).classes("text-sm")


def _deploy_card(fleet: Fleet) -> None:
    """Most recent deploy status derived from fleet health."""
    last = fleet.last_deploy
    with ui.card().classes("flex-1 min-w-[280px]"):
        theme.card_title("Last Deploy")
        if not last:
            ui.label("No deployments yet").classes("text-sm").style(
                f"color: {theme.TEXT_SECONDARY}"
            )
        else:
            if fleet.healthy:
                ui.badge("success", color="green")
            else:
                ui.badge(data.exit_code_label(last.exit_code), color="red")
                for err in fleet.errors:
                    ui.label(err).classes("text-xs mt-1").style(
                        f"color: {theme.COLOR_ERROR}"
                    )
            ui.label(f"Tags: {', '.join(last.tags)}").classes("text-xs").style(
                f"color: {theme.TEXT_SECONDARY}"
            )
            ui.label(f"{last.timestamp}  ·  {last.duration_seconds}s").classes("text-xs").style(
                f"color: {theme.TEXT_DISABLED}"
            )


def _fleet_card(fleet: Fleet, state_dir: Path) -> None:
    """Fleet overview card — all data from Fleet domain object."""
    nodes = data.load_node_registry(state_dir)
    alerts = data.compute_alerts(nodes)
    critical_count = sum(1 for a in alerts if a.severity == "critical")

    with ui.card().classes("w-full"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-3"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("device_hub").classes("text-xl").style(f"color: {theme.ACCENT}")
                theme.card_title("Fleet")
            if fleet.has_telemetry:
                score_color = theme.health_score_color(fleet.health_score)
                ui.badge(
                    f"Health {fleet.health_score}/100", color="green"
                ).classes("text-sm").style(f"background: {score_color} !important")

        if fleet.has_telemetry:
            if fleet.online_count == fleet.host_count:
                summary = f"All {fleet.online_count} nodes online"
                level = "success"
            else:
                summary = f"{fleet.online_count} online, {fleet.offline_count} offline"
                level = "warning"
            lbl = ui.label(summary).classes("text-sm mt-2")
            theme.status_text(lbl, summary, level)

            guest_word = "guest" if fleet.total_guests == 1 else "guests"
            with ui.row().classes("gap-6 mt-2"):
                ui.label(f"{fleet.total_guests} {guest_word} running").classes("text-xs").style(
                    f"color: {theme.TEXT_SECONDARY}"
                )
                if fleet.avg_disk_pct > 0:
                    disk_color = theme.usage_color(data.usage_level(fleet.avg_disk_pct))
                    ui.label(f"Disk avg {fleet.avg_disk_pct:.0f}%").classes(
                        "text-xs"
                    ).style(f"color: {disk_color}")
                if fleet.avg_memory_pct > 0:
                    mem_color = theme.usage_color(data.usage_level(fleet.avg_memory_pct))
                    ui.label(f"Memory avg {fleet.avg_memory_pct:.0f}%").classes(
                        "text-xs"
                    ).style(f"color: {mem_color}")
        else:
            reachable = fleet.reachable_count
            if reachable == fleet.host_count:
                summary = f"All {reachable} hosts reachable — no heartbeats yet"
                level = "warning"
            elif reachable > 0:
                unreachable = fleet.host_count - reachable
                summary = f"{reachable} reachable, {unreachable} unreachable — no heartbeats"
                level = "warning"
            else:
                summary = f"{fleet.host_count} hosts configured — waiting for heartbeats"
                level = "disabled"
            lbl = ui.label(summary).classes("text-sm mt-2")
            theme.status_text(lbl, summary, level)
            with ui.row().classes("gap-2 mt-1 flex-wrap"):
                for h in fleet.hosts:
                    badge_color = "orange" if h.reachable else "grey"
                    ui.badge(h.name, color=badge_color).props("outline")

        bucket_parts: list[str] = []
        for bucket_id, label in [
            (HostBucket.PRODUCTION, Labels.BUCKET_PRODUCTION),
            (HostBucket.LAB, Labels.BUCKET_LAB),
            (HostBucket.TEST, Labels.BUCKET_TEST),
        ]:
            count = len(fleet.hosts_by_bucket(bucket_id))
            if count:
                bucket_parts.append(f"{count} {label}")
        if bucket_parts:
            ui.label(" · ".join(bucket_parts)).classes("text-xs mt-1").style(
                f"color: {theme.TEXT_SECONDARY}"
            )

        if critical_count > 0:
            ui.label(
                f"{critical_count} critical alert{'s' if critical_count != 1 else ''}"
            ).classes("text-xs mt-1").style(f"color: {theme.COLOR_ERROR}")

        with ui.row().classes("items-center gap-3 mt-3"):
            ui.button(
                Labels.VIEW_FLEET,
                icon="lan",
                on_click=lambda: ui.navigate.to(Routes.NODES),
            ).classes("subtle-btn")


def _history_section(fleet: Fleet) -> None:
    """Recent deploy history with per-record health labels."""
    all_deploys = [d for h in fleet.hosts for d in h.deploys]
    seen: set[str] = set()
    unique: list[data.DeployRecord] = []
    for d in all_deploys:
        key = f"{d.timestamp}-{d.exit_code}"
        if key not in seen:
            seen.add(key)
            unique.append(d)
    if not unique:
        return
    theme.section_label("Recent History")
    recent = unique[-5:][::-1]
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
