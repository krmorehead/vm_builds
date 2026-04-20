"""Centralized Logs status page — rsyslog collector health.

Renders a dashboard of rsyslog containers across all hosts using
heartbeat data for service status and listening port verification.
"""

from __future__ import annotations

from nicegui import ui

from scripts.webui import data, theme


def register() -> None:
    @ui.page("/logs")
    def logs_page() -> None:
        from scripts.webui.app import get_state_dir

        state_dir = get_state_dir()
        with theme.page_shell("logs"):
            _logs_content(state_dir)


def _fetch_rsyslog_data(state_dir) -> list[dict]:
    """Fetch rsyslog container data from the node registry.

    In the 4-tier model, individual containers are nested inside the
    host-level heartbeat under extensions.containers.
    """
    nodes = data.load_node_registry(state_dir)
    collectors = []
    for n in nodes:
        if not n.container_health:
            continue
        nested = n.container_health.extensions.get("containers", {})
        for cid, ct in nested.items():
            if "rsyslog" not in cid.lower():
                continue
            services = ct.get("systemd_services", {})
            ports = ct.get("listening_ports", [])
            collectors.append({
                "node": n.node_id,
                "container_id": cid,
                "ready": ct.get("ready", False),
                "rsyslog_running": services.get("rsyslog") == "running",
                "tcp_port": 514 in ports,
                "udp_port": 514 in ports,
                "last_seen": ct.get("last_seen", n.last_seen),
            })
    return collectors


def _logs_content(state_dir) -> None:
    """Render the rsyslog status dashboard."""
    theme.page_header("Centralized Logs", "rsyslog collector health and status")

    status_container = ui.column().classes("w-full gap-4")

    def _refresh() -> None:
        status_container.clear()
        collectors = _fetch_rsyslog_data(state_dir)
        with status_container:
            if not collectors:
                ui.label("No rsyslog containers found in fleet heartbeats.").style(
                    f"color: {theme.TEXT_DISABLED}"
                )
                return

            running = sum(1 for c in collectors if c["rsyslog_running"])
            total = len(collectors)
            listening = sum(1 for c in collectors if c["tcp_port"])

            with ui.row().classes("gap-4 flex-wrap"):
                _stat_card("Collectors", f"{running}/{total}", running == total)
                _stat_card("Listening (TCP 514)", f"{listening}/{total}", listening == total)

            for c in sorted(collectors, key=lambda x: x["node"]):
                _collector_card(c)

    ui.timer(0.1, _refresh, once=True)
    ui.timer(30, _refresh)


def _stat_card(label: str, value: str, healthy: bool) -> None:
    """Render a compact stat card."""
    color = theme.COLOR_SUCCESS if healthy else theme.COLOR_WARNING
    with ui.card().classes("p-4").style(
        f"background: {theme.BG_CARD}; border-left: 3px solid {color};"
    ):
        ui.label(value).classes("text-2xl font-bold").style(f"color: {color}")
        ui.label(label).classes("text-xs").style(f"color: {theme.TEXT_SECONDARY}")


def _collector_card(collector: dict) -> None:
    """Render a single rsyslog collector status card."""
    is_running = collector["rsyslog_running"]
    color = theme.COLOR_SUCCESS if is_running else theme.COLOR_ERROR

    with ui.card().classes("w-full p-4").style(
        f"background: {theme.BG_CARD}; border: 1px solid {theme.BORDER};"
    ):
        with ui.row().classes("items-center gap-3 w-full"):
            ui.icon("article" if is_running else "error_outline").classes("text-xl").style(
                f"color: {color}"
            )
            with ui.column().classes("gap-0 flex-1"):
                ui.label(collector["node"]).classes("font-medium").style(
                    f"color: {theme.TEXT_PRIMARY}"
                )
                ui.label(collector["container_id"]).classes("text-xs").style(
                    f"color: {theme.TEXT_SECONDARY}"
                )
            if collector["tcp_port"]:
                ui.badge("TCP 514", color="green").props("outline")
            else:
                ui.badge("TCP 514", color="red").props("outline")
            ui.badge(
                "Running" if is_running else "Stopped",
                color="green" if is_running else "red",
            )
