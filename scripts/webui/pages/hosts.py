"""Host connectivity page — probe hosts and display status."""

from __future__ import annotations

import asyncio

from nicegui import ui

from scripts.webui import data, theme
from scripts.webui.data import Labels, PageTitles
from scripts.webui.components import test_api_from_table


def register() -> None:
    @ui.page("/hosts")
    def hosts_page() -> None:
        from scripts.webui.app import load_active_env

        env = load_active_env()
        hosts_list = data.get_known_hosts(env)
        statuses: dict[str, data.HostStatus] = {}

        with theme.page_shell("hosts"):
            theme.page_header(PageTitles.HOSTS)

            status_label = ui.label("").classes("text-sm")

            table = ui.table(
                columns=[
                    {"name": "host", "label": "Host", "field": "host", "align": "left"},
                    {"name": "ip", "label": "VPN IP", "field": "ip", "align": "left"},
                    {"name": "status", "label": "Status", "field": "status", "align": "center"},
                    {"name": "latency", "label": "Latency", "field": "latency", "align": "center"},
                    {"name": "wol", "label": "WoL", "field": "wol", "align": "center"},
                    {"name": "notes", "label": "Notes", "field": "notes", "align": "left"},
                ],
                rows=[],
                row_key="host",
                selection="single",
            ).classes("w-full")

            spinner = ui.spinner("dots", size="lg")
            spinner.visible = False

            def _populate_table() -> None:
                rows: list[dict] = []
                for h in hosts_list:
                    st = statuses.get(h.name)
                    if st:
                        status = "\u2713 Reachable" if st.reachable else "\u2717 Unreachable"
                        latency = f"{st.latency_ms}ms" if st.latency_ms else ""
                        notes = st.error if not st.reachable else ""
                    else:
                        status = "--"
                        latency = ""
                        notes = ""
                    wol = "Yes" if h.wol_capable else "No WoL"
                    if not h.wol_capable:
                        notes = (notes + " " if notes else "") + "\u26a0 Physical power-on only"
                    rows.append({
                        "host": h.name,
                        "ip": h.ip,
                        "status": status,
                        "latency": latency,
                        "wol": wol,
                        "notes": notes,
                    })
                table.rows = rows
                _update_status_summary()

            def _update_status_summary() -> None:
                if not statuses:
                    theme.status_text(status_label, f"{len(hosts_list)} hosts — click Probe All to check connectivity", "info")
                    return
                reachable = sum(1 for s in statuses.values() if s.reachable)
                total = len(statuses)
                if reachable == total:
                    theme.status_text(status_label, f"All {total} hosts reachable", "success")
                else:
                    theme.status_text(status_label, f"{reachable}/{total} reachable — {total - reachable} unreachable", "error")

            async def _probe_hosts() -> None:
                spinner.visible = True
                results = await asyncio.to_thread(data.probe_all_hosts, hosts_list)
                for s in results:
                    statuses[s.host.name] = s
                spinner.visible = False
                _populate_table()

            with ui.row().classes("gap-3"):
                ui.button(Labels.PROBE_ALL, icon="wifi_find", on_click=_probe_hosts).classes("action-btn")
                ui.button(Labels.TEST_API, icon="terminal", on_click=lambda: test_api_from_table(table)).classes("outline-btn")

            _populate_table()
