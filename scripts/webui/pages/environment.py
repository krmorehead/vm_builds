"""Environment management page — view and edit .env values."""

from __future__ import annotations

from nicegui import ui

from scripts.webui import data, theme
from scripts.webui.data import Labels, PageTitles, Routes


def register() -> None:
    @ui.page(Routes.ENVIRONMENT)
    def environment_page() -> None:
        from scripts.webui.app import get_env_path

        env_path = get_env_path()
        env_result: dict[str, data.EnvResult | None] = {"current": None}
        editing: dict[str, str | None] = {"key": None}

        with theme.page_shell("environment"):
            theme.page_header(PageTitles.ENVIRONMENT, f"Managing: {env_path.name}")

            status_label = ui.label("").classes("text-sm")
            table = ui.table(
                columns=[
                    {"name": "variable", "label": "Variable", "field": "variable", "sortable": True, "align": "left"},
                    {"name": "value", "label": "Value", "field": "value", "align": "left"},
                    {"name": "status", "label": "Status", "field": "status", "align": "center"},
                ],
                rows=[],
                row_key="variable",
                selection="single",
            ).classes("w-full")

            with ui.row().classes("gap-2 items-center"):
                edit_label = ui.label("").classes("text-sm font-mono").style("display: none")
                edit_input = ui.input(placeholder="Enter value...").style("display: none").classes("w-80")
                edit_hint = ui.label("Press Enter to apply").classes("text-xs").style(
                    f"display: none; color: {theme.TEXT_DISABLED}"
                )

            with ui.row().classes("gap-3"):
                ui.button(Labels.VALIDATE, icon="check_circle", on_click=lambda: _refresh_table()).classes("outline-btn")
                ui.button(Labels.SAVE, icon="save", on_click=lambda: _save_env()).classes("action-btn")
                ui.button(Labels.CREATE_ENV, icon="add", on_click=lambda: _create_env()).classes("outline-btn")

            def _load_from_file() -> None:
                if env_path.exists():
                    env_result["current"] = data.load_environment(env_path)
                else:
                    env_result["current"] = None
                _render_table()

            def _refresh_table() -> None:
                er = env_result["current"]
                if er is not None:
                    import build
                    er.missing = build.validate_env(er.values)
                    er.warnings = build.warn_multi_host(er.values)
                _render_table()

            def _render_table() -> None:
                er = env_result["current"]
                rows: list[dict] = []

                if er is None:
                    theme.status_text(status_label, "No env file found. Click 'Create .env' to generate one.", "error")
                    tmpl = data.get_env_template()
                    for v in tmpl:
                        rows.append({
                            "variable": v.name,
                            "value": v.example if not v.sensitive else "",
                            "status": "TEMPLATE",
                        })
                    table.rows = rows
                    return

                tmpl = data.get_env_template()
                tmpl_names = {v.name for v in tmpl}

                for v in tmpl:
                    value = er.values.get(v.name, "")
                    if v.name in er.missing:
                        st = "MISSING"
                    elif not value and v.required:
                        st = "EMPTY"
                    elif value:
                        st = "OK"
                        value = "****" if v.sensitive else value
                    else:
                        st = "optional"
                    rows.append({"variable": v.name, "value": value, "status": st})

                _sensitive_fragments = {"PASSWORD", "TOKEN", "SECRET", "KEY"}
                for key, value in er.values.items():
                    if key not in tmpl_names:
                        masked = any(frag in key.upper() for frag in _sensitive_fragments)
                        rows.append({
                            "variable": key,
                            "value": "****" if masked and value else value,
                            "status": "custom",
                        })

                table.rows = rows

                if er.missing:
                    theme.status_text(
                        status_label,
                        f"Missing {len(er.missing)} required variable(s): {', '.join(er.missing)}",
                        "error",
                    )
                elif er.warnings:
                    theme.status_text(status_label, f"Warnings: {'; '.join(er.warnings)}", "warning")
                else:
                    theme.status_text(status_label, "All required variables present.", "success")

            def _on_row_select(e) -> None:
                if e.selection:
                    row = e.selection[0]
                    editing["key"] = row["variable"]
                    edit_label.text = f"{editing['key']}:"
                    edit_label.style("display: block")
                    er = env_result["current"]
                    current_val = ""
                    if er:
                        current_val = er.values.get(editing["key"], "")
                    edit_input.value = current_val
                    edit_input.style("display: block")
                    edit_hint.style("display: block")

            def _on_edit_submit(e) -> None:
                key = editing["key"]
                if key:
                    er = env_result["current"]
                    if er is None:
                        env_result["current"] = data.EnvResult(values={}, missing=[], warnings=[])
                        er = env_result["current"]
                    er.values[key] = edit_input.value
                    editing["key"] = None
                    edit_label.style("display: none")
                    edit_input.style("display: none")
                    edit_hint.style("display: none")
                    _refresh_table()

            table.on("selection", _on_row_select)
            edit_input.on("keydown.enter", _on_edit_submit)

            def _save_env() -> None:
                er = env_result["current"]
                if er and er.values:
                    data.save_environment(env_path, er.values)
                    ui.notify("Environment saved.", type="positive")
                    _load_from_file()
                else:
                    ui.notify("Nothing to save.", type="warning")

            def _create_env() -> None:
                tmpl = data.get_env_template()
                env: dict[str, str] = {}
                for v in tmpl:
                    env[v.name] = "" if v.sensitive else v.example
                data.save_environment(env_path, env)
                ui.notify(f"Created {env_path}", type="positive")
                _load_from_file()

            _load_from_file()
