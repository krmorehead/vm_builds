"""Deployment Timeline page — visualizes per-service provisioning and readiness timing."""

from __future__ import annotations

from nicegui import ui

from scripts.webui import data, theme
from scripts.webui.data import PageTitles


def register() -> None:
    @ui.page("/timeline")
    def timeline_page() -> None:
        from scripts.webui.app import get_state_dir

        state_dir = get_state_dir()

        with theme.page_shell("timeline"):
            theme.page_header(PageTitles.TIMELINE, "Per-service provisioning and readiness timing")

            active = data.get_active_timeline()
            if active:
                with ui.card().classes("w-full"):
                    with ui.row().classes("items-center gap-2"):
                        ui.spinner(size="sm")
                        theme.card_title("Active Deployment")
                    elapsed = data.time.monotonic() - active.start_time
                    ui.label(f"Running for {elapsed:.0f}s").classes("text-sm").style(
                        f"color: {theme.TEXT_SECONDARY}"
                    )
                    _render_gantt(active)

                ui.timer(3.0, lambda: _refresh_active(active_container, state_dir))

            active_container = ui.column().classes("w-full")

            timelines = data.load_timelines(state_dir, max_count=5)
            if timelines:
                theme.section_label("Recent Deployments")
                for tl in timelines:
                    _render_saved_timeline(tl)

            if not active and not timelines:
                ui.label("No deployment timelines recorded yet.").classes(
                    "text-sm mt-4"
                ).style(f"color: {theme.TEXT_SECONDARY}")
                ui.label(
                    "Timelines are recorded automatically during deployments "
                    "when the callhome API server is running."
                ).classes("text-xs").style(f"color: {theme.TEXT_DISABLED}")


def _refresh_active(container: ui.column, state_dir) -> None:
    """Refresh the active timeline display."""
    container.clear()
    active = data.get_active_timeline()
    if not active:
        return
    with container:
        _render_gantt(active)


def _render_gantt(timeline: data.DeployTimeline) -> None:
    """Render a Gantt-style chart showing service readiness timing."""
    if not timeline.services:
        ui.label("No services have checked in yet.").classes("text-sm").style(
            f"color: {theme.TEXT_SECONDARY}"
        )
        return

    now_offset = data.time.monotonic() - timeline.start_time
    max_time = max(
        now_offset,
        max(
            (s.ready_at or s.first_checkin or timeline.start_time) - timeline.start_time
            for s in timeline.services.values()
        ),
    )
    if max_time <= 0:
        max_time = 1.0

    sorted_services = sorted(
        timeline.services.values(),
        key=lambda s: s.first_checkin or float("inf"),
    )

    with ui.column().classes("w-full gap-1 mt-2"):
        for svc in sorted_services:
            checkin_offset = (
                (svc.first_checkin - timeline.start_time) if svc.first_checkin else None
            )
            ready_offset = (
                (svc.ready_at - timeline.start_time) if svc.ready_at else None
            )

            with ui.row().classes("items-center gap-2 w-full"):
                ui.label(svc.service_id).classes("font-mono text-xs w-28 text-right").style(
                    f"color: {theme.TEXT_PRIMARY}"
                )

                bar_width_pct = 100
                if ready_offset is not None:
                    bar_width_pct = min(100, (ready_offset / max_time) * 100)
                elif checkin_offset is not None:
                    bar_width_pct = min(100, (checkin_offset / max_time) * 100)

                bar_color = theme.COLOR_SUCCESS if ready_offset else theme.COLOR_WARNING
                ui.element("div").classes("h-4 rounded").style(
                    f"width: {bar_width_pct}%; background: {bar_color}; min-width: 4px"
                )

                timing = ""
                if ready_offset is not None:
                    timing = f"{ready_offset:.1f}s"
                elif checkin_offset is not None:
                    timing = f"{checkin_offset:.1f}s (waiting)"
                else:
                    timing = "pending"

                ui.label(timing).classes("text-xs font-mono").style(
                    f"color: {theme.TEXT_SECONDARY}"
                )


def _render_saved_timeline(tl: dict) -> None:
    """Render a completed saved timeline from JSON."""
    duration = tl.get("duration", 0)
    services = tl.get("services", {})

    with ui.card().classes("w-full"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("timeline").classes("text-lg").style(f"color: {theme.ACCENT}")
            theme.card_title(f"Deployment ({duration:.0f}s total)")

        if not services:
            ui.label("No service data recorded.").classes("text-sm").style(
                f"color: {theme.TEXT_SECONDARY}"
            )
            return

        max_time = max(
            svc.get("ready_offset", svc.get("checkin_offset", 0))
            for svc in services.values()
        )
        if max_time <= 0:
            max_time = duration or 1.0

        sorted_svcs = sorted(
            services.items(),
            key=lambda kv: kv[1].get("checkin_offset", float("inf")),
        )

        with ui.column().classes("w-full gap-1 mt-2"):
            for sid, svc in sorted_svcs:
                checkin = svc.get("checkin_offset")
                ready = svc.get("ready_offset")

                with ui.row().classes("items-center gap-2 w-full"):
                    ui.label(sid).classes("font-mono text-xs w-28 text-right").style(
                        f"color: {theme.TEXT_PRIMARY}"
                    )

                    bar_pct = 100
                    if ready is not None:
                        bar_pct = min(100, (ready / max_time) * 100)
                    elif checkin is not None:
                        bar_pct = min(100, (checkin / max_time) * 100)

                    bar_color = theme.COLOR_SUCCESS if ready else theme.COLOR_WARNING
                    ui.element("div").classes("h-4 rounded").style(
                        f"width: {bar_pct}%; background: {bar_color}; min-width: 4px"
                    )

                    timing = f"{ready:.1f}s" if ready is not None else "incomplete"
                    ui.label(timing).classes("text-xs font-mono").style(
                        f"color: {theme.TEXT_SECONDARY}"
                    )
