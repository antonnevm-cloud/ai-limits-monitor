import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pathlib

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from claude_usage_manager.database import Database
from claude_usage_manager.models import (
    AnalysisResult, ModelRow, Period, ProjectRow, QuotaSnapshot, QuotaWindow,
    ThreadRow, TimelineBucket,
)
from claude_usage_manager.qt_gui import MainWindow
from claude_usage_manager.qt_theme import COLORS, build_stylesheet


def app():
    return QApplication.instance() or QApplication([])


def test_analytics_refresh_preserves_expansion_sort_selection_and_filter(tmp_path, monkeypatch):
    from dataclasses import replace
    from claude_usage_manager.integrated_analytics import IntegratedAnalyticsWindow

    qapp = app()
    db = Database(tmp_path / "ui.sqlite")
    window = IntegratedAnalyticsWindow(db)
    result = sample_result()
    result.threads.append(replace(result.threads[0], session_id="s2", title="Другой", project="other"))
    result.projects.append(ProjectRow("other", 1, 500, 2.0, 10.0))
    window.result = result
    window._render_overview(result)
    window._render_threads(result)
    window._render_projects(result)
    window._render_models(result)
    first = window.overview_tree.topLevelItem(0)
    first.setExpanded(True)
    project_key = first.data(0, Qt.ItemDataRole.UserRole)
    window._show_threads_for("project", "demo")
    window.threads_table.sortItems(0, Qt.SortOrder.AscendingOrder)
    window.threads_table.selectRow(0)
    monkeypatch.setattr(window, "_cached_quota", lambda provider: QuotaSnapshot(provider=provider, weekly=12))
    window.reload_cached_quotas()
    assert any(window.overview_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == project_key
               and window.overview_tree.topLevelItem(i).isExpanded() for i in range(window.overview_tree.topLevelItemCount()))
    assert window.threads_table.rowCount() == 1
    assert window.threads_table.item(0, 0).data(Qt.ItemDataRole.UserRole) == "s1"
    assert window.threads_table.horizontalHeader().sortIndicatorSection() == 0
    assert window.threads_table.selectionModel().selectedRows()
    assert window.cards["weekly"].value.text() == "12%"

    monkeypatch.setattr(window, "_cached_quota", lambda provider: QuotaSnapshot(
        provider=provider, weekly=13 if provider == "codex" else 12,
    ))
    recalculations = []
    monkeypatch.setattr(window, "_apply_quota_to_result", recalculations.append)
    window.reload_cached_quotas()
    assert recalculations == []
    assert window._quota_cache["codex"].weekly == 13
    window.close(); db.close(); qapp.processEvents()


def sample_result():
    import datetime as dt
    now = dt.datetime(2026, 8, 5, 12, 0, tzinfo=dt.timezone.utc)
    period = Period("today", "Сегодня", now.astimezone(), now.astimezone())
    thread = ThreadRow(
        session_id="s1", title="Проверка интерфейса", project="demo",
        agent="claude", models=["claude-opus-5"], cost=40.0,
        total_tokens=1_000_000, first_activity=now, last_activity=now,
        member_ids=["s1"], share=100.0, weekly_pp=5.0,
    )
    return AnalysisResult(
        period=period, agent="claude", threads=[thread],
        projects=[ProjectRow("demo", 1, 1_000_000, 40.0, 100.0)],
        model_rows=[ModelRow("claude-opus-5", total_tokens=1_000_000, cost=40.0, share=100.0)],
        timeline=[TimelineBucket("12:00", now, 1_000_000, 40.0)],
        total_tokens=1_000_000, total_cost=40.0, work_threads=1,
        quota=QuotaSnapshot(five_hour=20.0, weekly=60.0),
        weekly_consumed_pp=5.0, generated_at=now,
        meta={"events": [], "session_costs": {"s1": 40.0}},
    )


def test_qt_window_structure(tmp_path):
    qapp = app()
    db = Database(tmp_path / "ui.sqlite")
    window = MainWindow(db, auto_calculate=False, enable_tray=False)
    assert [button.text() for button in window.nav_buttons] == [
        "Обзор", "Треды", "Проекты", "По времени", "Модели"
    ]
    assert len(window.cards) == 6
    assert [window.agent_combo.itemText(i) for i in range(window.agent_combo.count())] == [
        "Claude Code", "Codex", "Grok Build", "Antigravity"
    ]
    assert window.minimumWidth() == 1160
    assert window.minimumHeight() == 720
    window.close()
    db.close()
    qapp.processEvents()


def test_qt_renders_project_hierarchy_and_pp(tmp_path):
    qapp = app()
    db = Database(tmp_path / "ui.sqlite")
    window = MainWindow(db, auto_calculate=False, enable_tray=False)
    result = sample_result()
    result.all_threads = [
        *result.threads,
        ThreadRow(
            session_id="s2", title="Еще один тред", project="demo",
            agent="claude", models=["claude-sonnet-5"], cost=2.0,
            total_tokens=50, first_activity=result.threads[0].first_activity,
            last_activity=result.threads[0].last_activity, member_ids=["s2"],
        ),
    ]
    window._analysis_finished(result)
    assert window.overview_tree.topLevelItemCount() == 1
    project = window.overview_tree.topLevelItem(0)
    assert project.text(0) == "demo"
    assert project.text(6) == "≈5,00 п.п."
    assert project.childCount() == 1
    assert window.threads_table.rowCount() == 1
    assert window.projects_table.rowCount() == 1
    assert window.models_table.rowCount() == 1
    assert window.threads_table.horizontalHeaderItem(0).text() == "Тред"
    assert window.threads_table.horizontalHeader().sectionResizeMode(0).name == "Interactive"
    assert window.projects_table.item(0, 0).data(Qt.ItemDataRole.UserRole) == "demo"
    assert window.models_table.item(0, 0).data(Qt.ItemDataRole.UserRole) == "claude-opus-5"
    assert window.projects_table.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    assert window.models_table.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    window._show_threads_for("project", "demo")
    assert window.stack.currentIndex() == 1
    assert window.threads_table.rowCount() == 2
    window._show_threads_for("model", "claude-opus-5")
    assert window.threads_table.rowCount() == 1
    window.close()
    db.close()
    qapp.processEvents()


def test_qt_module_does_not_import_tkinter():
    source = pathlib.Path("claude_usage_manager/qt_gui.py").read_text(encoding="utf-8")
    assert "tkinter" not in source.casefold()


def test_qt_cached_quota_replaces_false_unavailable(tmp_path):
    import datetime as dt

    qapp = app()
    qapp.setStyleSheet(build_stylesheet())
    db = Database(tmp_path / "ui.sqlite")
    now = dt.datetime.now(dt.timezone.utc)
    snapshot = QuotaSnapshot(
        five_hour=9.0, weekly=64.0, source="claude-code:statusline",
        updated_at=now, main_window_id="secondary",
        windows=[
            QuotaWindow("primary", "Пятичасовое окно", 9.0,
                        resets_at=now + dt.timedelta(hours=2),
                        window_minutes=300, role="primary"),
            QuotaWindow("secondary", "Недельная квота", 64.0,
                        resets_at=now + dt.timedelta(days=3),
                        window_minutes=10080, role="secondary"),
        ],
    )
    db.record_quota(snapshot)
    window = MainWindow(db, auto_calculate=False, enable_tray=False)
    result = sample_result()
    result.warnings.append(
        "Квоты недоступны (нет свежих данных локального источника)."
    )
    window._analysis_finished(result)
    assert window.cards["weekly"].value.text() == "64%"
    assert "Недельная квота 64%" in window.status.text()
    assert "недоступ" not in window.status.text().casefold()
    window.close()
    db.close()
    qapp.processEvents()


def test_integrated_analytics_reads_claude_mod_without_database_quota(tmp_path, monkeypatch):
    import datetime as dt

    from claude_usage_manager import claude_mod_bridge
    from claude_usage_manager.claude_local import ingest_statusline
    from claude_usage_manager.integrated_analytics import IntegratedAnalyticsWindow

    qapp = app()
    db = Database(tmp_path / "ui.sqlite")
    path = tmp_path / "mod.json"
    monkeypatch.setattr(claude_mod_bridge, "mod_snapshot_path", lambda: path)
    now = dt.datetime.now(dt.timezone.utc)
    short = {"used_percentage": 8, "resets_at": (now + dt.timedelta(hours=1)).timestamp()}
    weekly = {"used_percentage": 51, "resets_at": (now + dt.timedelta(days=1)).timestamp()}
    assert ingest_statusline({"rate_limits": {"five_hour": short, "seven_day": weekly}}, path=path, now=now)

    window = IntegratedAnalyticsWindow(db)
    result = sample_result()
    window._analysis_finished(result)

    assert result.quota.source == claude_mod_bridge.SOURCE
    assert db.quota_samples(provider="claude") == []
    assert window.cards["weekly"].value.text() == "51%"
    assert window.cards["five"].value.text() == "8%"

    assert ingest_statusline({"rate_limits": {"five_hour": short}}, path=path, now=now)
    window.reload_cached_quotas()
    assert window.cards["weekly"].label.text() == "Недельная квота"
    assert window.cards["weekly"].value.text() == "-"
    assert window.cards["five"].value.text() == "8%"
    window.close()
    db.close()
    qapp.processEvents()


def test_qt_cached_quota_ignores_informational_tail(tmp_path):
    import datetime as dt

    qapp = app()
    db = Database(tmp_path / "ui.sqlite")
    valid = QuotaSnapshot(
        provider="antigravity",
        source="codexbar:antigravity",
        login_method="Antigravity",
        updated_at=dt.datetime(2026, 8, 12, 14, 0, tzinfo=dt.timezone.utc),
        main_window_id="ag-group:gemini:weekly",
        windows=[
            QuotaWindow(
                "ag-group:gemini:weekly",
                "Gemini Models",
                4.0,
                window_minutes=10080,
                role="group",
            )
        ],
    )
    informational = QuotaSnapshot(
        provider="antigravity",
        source="codexbar:antigravity",
        login_method="offline",
        updated_at=dt.datetime(2026, 8, 12, 15, 0, tzinfo=dt.timezone.utc),
        main_window_id="primary",
        windows=[
            QuotaWindow(
                "primary",
                "Нет данных",
                0.0,
                role="primary",
                informational=True,
            )
        ],
    )
    assert db.record_quota(valid)
    assert db.record_quota(informational)

    window = MainWindow(db, auto_calculate=False, enable_tray=False)
    cached = window._cached_quota("antigravity")

    assert cached.main_window_id == "ag-group:gemini:weekly"
    assert cached.weekly == 4.0
    assert cached.login_method == "Antigravity"
    window.close()
    db.close()
    qapp.processEvents()


def test_qt_disables_analysis_inputs_while_worker_is_running(tmp_path):
    qapp = app()
    db = Database(tmp_path / "ui.sqlite")
    window = MainWindow(db, auto_calculate=False, enable_tray=False)
    window.thread_pool = type("Pool", (), {"start": lambda _self, _worker: None})()

    window.calculate()

    assert window._busy
    assert all(
        not widget.isEnabled()
        for widget in (
            window.agent_combo,
            window.period_combo,
            window.start_edit,
            window.end_edit,
            window.search_edit,
            window.hidden_button,
        )
    )
    window._finish_busy()
    assert window.agent_combo.isEnabled()
    assert window.period_combo.isEnabled()
    assert not window.start_edit.isEnabled()
    assert not window.end_edit.isEnabled()
    assert window.search_edit.isEnabled()
    assert window.hidden_button.isEnabled()
    window.close()
    db.close()
    qapp.processEvents()


def test_qt_reports_local_quota_fallback(tmp_path):
    import datetime as dt

    qapp = app()
    db = Database(tmp_path / "ui.sqlite")
    window = MainWindow(db, auto_calculate=False, enable_tray=False)
    fallback = QuotaSnapshot(
        provider="claude",
        five_hour=6.0,
        weekly=3.0,
        fallback_source="Локальная история Claude Code",
        updated_at=dt.datetime.now(dt.timezone.utc),
    )
    window._quota_cache["claude"] = fallback
    result = sample_result()
    result.agent = "claude"
    window._analysis_finished(result)
    assert "локальная история" in window.status.text().casefold()
    window.close()
    db.close()
    qapp.processEvents()


def test_qt_table_typography_and_layer_contrast(tmp_path):
    qapp = app()
    qapp.setStyleSheet(build_stylesheet())
    db = Database(tmp_path / "ui.sqlite")
    window = MainWindow(db, auto_calculate=False, enable_tray=False)
    window.show()
    qapp.processEvents()
    assert window.overview_tree.font().pointSizeF() == 11.0
    assert window.threads_table.font().pointSizeF() == 11.0
    assert window.threads_table.horizontalHeader().font().pointSizeF() == 10.0
    assert window.threads_table.verticalHeader().defaultSectionSize() == 42
    layer_keys = (
        "bg", "sidebar", "control", "panel", "panel_alt",
        "table", "table_alt", "header", "project", "selection",
    )
    assert len({COLORS[key] for key in layer_keys}) == len(layer_keys)
    window.close()
    db.close()
    qapp.processEvents()


def test_analysis_worker_does_not_fetch_quota_in_main_analysis():
    source = pathlib.Path("claude_usage_manager/qt_gui.py").read_text(encoding="utf-8")
    worker_source = source[source.index("class AnalysisWorker"):source.index("class MetricCard")]
    assert "collect_quota=False" in worker_source
    assert "collect_quota=True" not in worker_source


def test_qt_quota_background_tracks_all_supported_sources():
    source = pathlib.Path("claude_usage_manager/qt_gui.py").read_text(encoding="utf-8")
    assert 'self._quota_queue = ["claude", "codex", "grok", "antigravity"]' in source
    assert 'def _collect_next_quota' in source
    assert 'Лимит, п.п.' in source

def test_qt_antigravity_model_quota_columns(tmp_path):
    import datetime as dt
    qapp = app()
    db = Database(tmp_path / "ui.sqlite")
    window = MainWindow(db, auto_calculate=False, enable_tray=False)
    result = sample_result()
    result.agent = "antigravity"
    result.threads[0].agent = "antigravity"
    result.threads[0].models = ["Gemini 3.1 Pro (High)"]
    result.model_rows = [ModelRow("Gemini 3.1 Pro (High)", total_tokens=1_000_000, share=100.0)]
    reset = dt.datetime(2026, 8, 9, 14, 18, 44, tzinfo=dt.timezone.utc)
    snap = QuotaSnapshot(
        provider="antigravity", source="codexbar:antigravity", login_method="Pro",
        main_window_id="model-gemini-pro-agent", windows=[
            QuotaWindow("model-gemini-pro-agent", "Gemini 3.1 Pro (High)", 23.0, reset, role="model")
        ],
    )
    window._quota_cache["antigravity"] = snap
    window._analysis_finished(result)
    assert window.models_table.columnCount() == 11
    assert window.models_table.isColumnHidden(8) is False
    assert window.models_table.item(0, 8).text() == "23,00%"
    assert window.cards["weekly"].value.text() == "23,00%"
    window.close(); db.close(); qapp.processEvents()
