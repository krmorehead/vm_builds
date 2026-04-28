"""Service selection page — pick services to deploy by tag."""

from __future__ import annotations

from nicegui import app as nicegui_app, ui

from scripts.webui import data, theme
from scripts.webui.data import Labels, PageTitles, Routes


def register() -> None:
    @ui.page(Routes.SERVICES)
    def services_page() -> None:
        services = data.get_service_tags()
        profiles = data.get_deploy_profiles()
        checkboxes: dict[str, ui.checkbox] = {}

        with theme.page_shell("services"):
            theme.page_header(PageTitles.SERVICES)

            with ui.row().classes("gap-4 items-center"):
                profile_options = {p.name: p.name for p in profiles}
                ui.select(
                    options=profile_options,
                    label="Deploy Profile",
                    on_change=lambda e: _apply_profile(e.value),
                ).classes("w-64")

            host_summary = ui.label("Target hosts: none selected").classes("text-sm").style(
                f"color: {theme.TEXT_SECONDARY}"
            )

            current_category = ""
            for svc in services:
                if svc.category != current_category:
                    current_category = svc.category
                    theme.section_label(current_category)
                label = f"{svc.tag}  —  {svc.description}"
                hosts_str = ", ".join(svc.hosts)
                suffix = f"  [{hosts_str}]"
                if svc.is_opt_in:
                    suffix += "  (opt-in)"
                cb = ui.checkbox(
                    label + suffix,
                    value=False,
                    on_change=lambda _: _update_summary(),
                )
                checkboxes[svc.tag] = cb

            with ui.row().classes("gap-3 mt-4"):
                ui.button(Labels.SELECT_ALL, icon="select_all", on_click=lambda: _select_all()).classes("subtle-btn")
                ui.button(Labels.DESELECT_ALL, icon="deselect", on_click=lambda: _deselect_all()).classes("subtle-btn")
                ui.button(
                    f"{Labels.DEPLOY_SELECTED} \u2192",
                    icon="rocket_launch",
                    on_click=lambda: _deploy_selected(),
                ).classes("action-btn")

            def _apply_profile(name: str) -> None:
                profile = next((p for p in profiles if p.name == name), None)
                if not profile:
                    return
                for svc in services:
                    checkboxes[svc.tag].value = svc.tag in profile.tags

            def _apply_preselection() -> None:
                pre = nicegui_app.storage.general.get("selected_tags", [])
                if pre:
                    for svc in services:
                        checkboxes[svc.tag].value = svc.tag in pre
                    nicegui_app.storage.general["selected_tags"] = []
                    _update_summary()

            def _select_all() -> None:
                for svc in services:
                    if not svc.is_opt_in:
                        checkboxes[svc.tag].value = True

            def _deselect_all() -> None:
                for svc in services:
                    checkboxes[svc.tag].value = False

            def _get_selected() -> list[str]:
                return [tag for tag, cb in checkboxes.items() if cb.value]

            def _update_summary() -> None:
                selected = _get_selected()
                if selected:
                    hosts = data.get_hosts_for_tags(selected)
                    host_summary.text = f"Target hosts: {', '.join(hosts)}"
                    host_summary.style(f"color: {theme.TEXT_PRIMARY}")
                else:
                    host_summary.text = "Target hosts: none selected"
                    host_summary.style(f"color: {theme.TEXT_SECONDARY}")

            def _deploy_selected() -> None:
                selected = _get_selected()
                if not selected:
                    ui.notify("No services selected.", type="warning")
                    return
                nicegui_app.storage.general["selected_tags"] = selected
                ui.navigate.to(Routes.DEPLOY)

            _apply_preselection()
