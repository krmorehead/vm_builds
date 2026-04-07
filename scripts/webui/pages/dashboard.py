"""Dashboard home page — aggregates status from all subsystems."""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from scripts.webui import data, theme


def register() -> None:
    @ui.page("/")
    def dashboard_page() -> None:
        from scripts.webui.app import get_env_path, get_images_dir, get_state_dir, load_active_env

        env_path = get_env_path()
        images_dir = get_images_dir()
        state_dir = get_state_dir()
        env = load_active_env()

        with theme.page_shell("dashboard"):
            _env_banner(env_path)
            theme.page_header("vm_builds", "Build Menu Dashboard")

            with ui.row().classes("w-full gap-4 flex-wrap"):
                _host_card(env)
                _image_card(images_dir)
                _deploy_card(state_dir)

            with ui.column().classes("w-full") as fleet_container:
                _fleet_card(state_dir)

            ui.timer(
                10.0,
                lambda: _refresh_fleet(fleet_container, state_dir),
            )

            _history_section(state_dir)

            theme.section_label("Quick Actions")
            with ui.row().classes("gap-3"):
                ui.button(
                    "Full Deploy",
                    icon="rocket_launch",
                    on_click=lambda: _full_deploy(),
                ).classes("action-btn")
                ui.button(
                    "Build Images",
                    icon="build",
                    on_click=lambda: ui.navigate.to("/images"),
                ).classes("outline-btn")
                ui.button(
                    "Check Hosts",
                    icon="dns",
                    on_click=lambda: ui.navigate.to("/hosts"),
                ).classes("outline-btn")
                ui.button(
                    "Deploy Timeline",
                    icon="timeline",
                    on_click=lambda: ui.navigate.to("/timeline"),
                ).classes("outline-btn")


def _refresh_fleet(container: ui.column, state_dir: Path) -> None:
    """Re-render the fleet card with fresh data."""
    container.clear()
    with container:
        _fleet_card(state_dir)


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
    full = next((p for p in profiles if p.name == "Full Deploy"), None)
    if full:
        nicegui_app.storage.general["selected_tags"] = full.tags
    ui.navigate.to("/services")


def _host_card(env: dict[str, str]) -> None:
    hosts = data.get_known_hosts(env)
    with ui.card().classes("flex-1 min-w-[280px]"):
        theme.card_title("Hosts")
        theme.card_subtitle(f"{len(hosts)} configured")
        for h in hosts:
            with ui.row().classes("items-center gap-2"):
                ui.label(h.name).classes("font-mono text-sm").style(
                    f"color: {theme.TEXT_PRIMARY}"
                )
                ui.label(h.ip).classes("text-xs").style(
                    f"color: {theme.TEXT_SECONDARY}"
                )
                if h.is_lan:
                    ui.badge("LAN", color="blue").props("outline")
                if not h.wol_capable:
                    ui.badge("No WoL", color="red").props("outline")


def _image_card(images_dir: Path) -> None:
    imgs = data.get_image_status(images_dir)
    built = sum(1 for i in imgs if i.exists)
    total = len(imgs)
    color = "green" if built == total else "amber" if built > 0 else "red"
    with ui.card().classes("flex-1 min-w-[280px]"):
        theme.card_title("Images")
        ui.badge(f"{built}/{total} built", color=color).classes("text-sm")


def _deploy_card(state_dir: Path) -> None:
    history = data.load_deploy_history(state_dir)
    with ui.card().classes("flex-1 min-w-[280px]"):
        theme.card_title("Last Deploy")
        if not history:
            ui.label("No deployments yet").classes("text-sm").style(
                f"color: {theme.TEXT_SECONDARY}"
            )
        else:
            last = history[-1]
            color = "green" if last.exit_code == 0 else "red"
            badge_text = "success" if last.exit_code == 0 else f"failed (rc={last.exit_code})"
            ui.badge(badge_text, color=color)
            ui.label(f"Tags: {', '.join(last.tags)}").classes("text-xs").style(
                f"color: {theme.TEXT_SECONDARY}"
            )
            ui.label(f"{last.timestamp}  ·  {last.duration_seconds}s").classes("text-xs").style(
                f"color: {theme.TEXT_DISABLED}"
            )


def _fleet_card(state_dir: Path) -> None:
    nodes = data.load_node_registry(state_dir)
    health = data.compute_fleet_health(nodes)
    alerts = data.compute_alerts(nodes)
    critical_count = sum(1 for a in alerts if a.severity == "critical")

    with ui.card().classes("w-full"):
        with ui.row().classes("w-full items-center justify-between flex-wrap gap-3"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("device_hub").classes("text-xl").style(f"color: {theme.ACCENT}")
                theme.card_title("Fleet")
            if health.total_nodes > 0:
                score_color = theme.health_score_color(health.health_score)
                ui.badge(
                    f"Health {health.health_score}/100", color="green"
                ).classes("text-sm").style(
                    f"background: {score_color} !important"
                )

        text, level = data.fleet_summary(nodes)
        lbl = ui.label(text).classes("text-sm mt-2")
        theme.status_text(lbl, text, level)

        if health.total_nodes > 0:
            with ui.row().classes("gap-6 mt-2"):
                ui.label(f"{health.total_services} services").classes("text-xs").style(
                    f"color: {theme.TEXT_SECONDARY}"
                )
                if health.avg_disk_pct > 0:
                    disk_color = theme.usage_color(data.usage_level(health.avg_disk_pct))
                    ui.label(f"Disk avg {health.avg_disk_pct:.0f}%").classes(
                        "text-xs"
                    ).style(f"color: {disk_color}")
                if health.avg_memory_pct > 0:
                    mem_color = theme.usage_color(data.usage_level(health.avg_memory_pct))
                    ui.label(f"Memory avg {health.avg_memory_pct:.0f}%").classes(
                        "text-xs"
                    ).style(f"color: {mem_color}")

        if critical_count > 0:
            ui.label(
                f"{critical_count} critical alert{'s' if critical_count != 1 else ''}"
            ).classes("text-xs mt-1").style(f"color: {theme.COLOR_ERROR}")

        with ui.row().classes("mt-2"):
            ui.button(
                "View Fleet Dashboard",
                icon="monitoring",
                on_click=lambda: ui.navigate.to("/nodes"),
            ).classes("subtle-btn")


def _history_section(state_dir: Path) -> None:
    history = data.load_deploy_history(state_dir)
    if not history:
        return
    theme.section_label("Recent History")
    recent = history[-5:][::-1]
    with ui.column().classes("gap-1"):
        for r in recent:
            color = "green" if r.exit_code == 0 else "red"
            with ui.row().classes("items-center gap-2"):
                ui.badge(f"rc={r.exit_code}", color=color)
                ui.label(", ".join(r.tags)).classes("text-sm").style(
                    f"color: {theme.TEXT_PRIMARY}"
                )
                ui.label(f"{r.timestamp}  ·  {r.duration_seconds}s").classes(
                    "text-xs"
                ).style(f"color: {theme.TEXT_DISABLED}")
