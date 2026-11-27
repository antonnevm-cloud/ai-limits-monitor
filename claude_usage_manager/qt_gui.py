
"""Modern PySide6 user interface for Claude Usage Manager."""
from __future__ import annotations

import ctypes
import datetime as dt
import sys
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QDateTime,
    QObject,
    QPoint,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QBrush, QColor, QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, APP_VERSION
from .analysis import build_timeline
from .database import Database
from .exports import export
from .icon import ensure_icon
from .models import AnalysisResult, Period, QuotaSnapshot, ThreadRow
from .periods import PRESETS, build_period, parse_custom
from .providers import AGENTS, AGENT_LABELS, MultiAgentAnalyzer
from .qt_chart import TimelineChart
from .qt_theme import COLORS, build_stylesheet
from .quota import fetch_quota, quota_consumed, quota_window_consumed, weekly_consumed
from .quota_monitor_model import cached_snapshot
from .utils import (
    estimate_project_weekly_pp,
    format_cost,
    format_percent,
    format_pp,
    format_tokens,
    local_dt_text,
    local_hm,
    now_local,
)

USER_ROLE = int(Qt.ItemDataRole.UserRole)


class SortItem(QTableWidgetItem):
    """A QTableWidgetItem that sorts by a typed hidden value."""

    def __init__(self, text: str, sort_value: Any = None) -> None:
        super().__init__(text)
        self.sort_value = text.casefold() if sort_value is None else sort_value

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, SortItem):
            try:
                return self.sort_value < other.sort_value
            except TypeError:
                return str(self.sort_value) < str(other.sort_value)
        return super().__lt__(other)


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class QuotaWorker(QRunnable):
    def __init__(self, provider: str, db: Database) -> None:
        super().__init__()
        self.provider = provider
        self.db = db
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            snapshot = fetch_quota(self.provider)
            if snapshot.has_data():
                self.db.record_quota(snapshot)
        except Exception:
            self.signals.failed.emit(traceback.format_exc())
            return
        self.signals.finished.emit(snapshot)


class AnalysisWorker(QRunnable):
    """Runs the existing analyzer outside the UI thread."""

    def __init__(
        self,
        analyzer: MultiAgentAnalyzer,
        agent: str,
        period: Period,
        search: str,
        show_hidden: bool,
    ) -> None:
        super().__init__()
        self.analyzer = analyzer
        self.agent = agent
        self.period = period
        self.search = search
        self.show_hidden = show_hidden
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.analyzer.analyze(
                self.agent,
                self.period,
                self.search,
                self.show_hidden,
                collect_quota=False,
            )
        except Exception:
            self.signals.failed.emit(traceback.format_exc())
            return
        self.signals.finished.emit(result)


class MetricCard(QFrame):
    def __init__(self, label: str, note: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MetricCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(92)
        self.setMaximumHeight(104)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 10)
        layout.setSpacing(2)

        self.label = QLabel(label)
        self.label.setObjectName("MetricLabel")
        self.label.setWordWrap(True)
        self.value = QLabel("—")
        self.value.setObjectName("MetricValue")
        self.note = QLabel(note)
        self.note.setObjectName("MetricNote")
        self.note.setWordWrap(True)

        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(self.note)

    def set_value(self, value: str, note: str | None = None) -> None:
        self.value.setText(value)
        if note is not None:
            self.note.setText(note)


class DetailDialog(QDialog):
    def __init__(self, row: ThreadRow, result: AnalysisResult, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Тред — {row.title}")
        self.resize(790, 650)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        title = QLabel(row.title)
        title.setObjectName("PageTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        info = QGridLayout()
        info.setHorizontalSpacing(24)
        info.setVerticalSpacing(8)
        fields = [
            ("Агент", AGENT_LABELS.get(row.agent, row.agent)),
            ("Проект", row.project),
            ("Модели", ", ".join(row.models) or "—"),
            ("Токены", format_tokens(row.total_tokens)),
            ("Условная стоимость", format_cost(row.cost)),
            ("Доля периода", format_percent(row.share)),
            ("Лимит, п.п.", row.limit_pp_text or format_pp(row.weekly_pp)),
            ("Субагенты", str(row.subagents)),
            ("Первая активность", local_dt_text(row.first_activity)),
            ("Последняя активность", local_dt_text(row.last_activity)),
        ]
        for index, (name, value) in enumerate(fields):
            key = QLabel(name)
            key.setObjectName("Muted")
            val = QLabel(value)
            val.setWordWrap(True)
            info.addWidget(key, index, 0, Qt.AlignmentFlag.AlignTop)
            info.addWidget(val, index, 1)
        info.setColumnStretch(1, 1)
        layout.addLayout(info)

        caption = QLabel("Динамика внутри выбранного периода")
        caption.setObjectName("PageTitle")
        layout.addWidget(caption)
        table = make_table(["Интервал", "Токены", "Стоимость"], stretch_column=0)
        members = set(row.member_ids or [row.session_id])
        buckets = build_timeline(
            result.period,
            result.meta.get("events", []),
            result.meta.get("session_costs", {}),
            members,
        )
        fill_table(
            table,
            [
                [
                    SortItem(bucket.label),
                    SortItem(format_tokens(bucket.total_tokens), bucket.total_tokens),
                    SortItem(format_cost(bucket.estimated_cost), bucket.estimated_cost),
                ]
                for bucket in buckets
            ],
        )
        layout.addWidget(table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    """Modern shell around the existing analysis and persistence layers."""

    def __init__(
        self,
        db: Database,
        *,
        start_hidden: bool = False,
        auto_calculate: bool = True,
        enable_tray: bool = True,
    ) -> None:
        super().__init__()
        self.db = db
        self.analyzer = MultiAgentAnalyzer(db)
        self.result: AnalysisResult | None = None
        self.last_period: Period | None = None
        self._thread_by_id: dict[str, ThreadRow] = {}
        self._worker: AnalysisWorker | None = None
        self._quota_worker: QuotaWorker | None = None
        self._quota_queue: list[str] = []
        self._quota_cache: dict[str, QuotaSnapshot] = {
            provider: self._cached_quota(provider)
            for provider in ("claude", "codex", "grok", "antigravity")
        }
        self._busy = False
        self._quit_requested = False
        self.thread_pool = QThreadPool.globalInstance()
        self.tray: QSystemTrayIcon | None = None

        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setMinimumSize(1160, 720)
        self.resize(1480, 880)
        self.setWindowIcon(QIcon(str(ensure_icon())))
        self._build_ui()
        if enable_tray:
            self._build_tray()
            self.quota_timer = QTimer(self)
            self.quota_timer.setInterval(10 * 60 * 1000)
            self.quota_timer.timeout.connect(self._collect_quota_background)
            self.quota_timer.start()
            QTimer.singleShot(1500, self._collect_quota_background)

        self._signals_enabled = False
        self._set_period("today")
        self._signals_enabled = True
        self._update_header_context()

        if start_hidden:
            self.hide()
        else:
            self.show()
        if auto_calculate:
            self.calculate()

    # ------------------------------- construction
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(12)

        outer.addLayout(self._build_header())
        outer.addWidget(self._build_controls())
        outer.addLayout(self._build_metrics())
        outer.addLayout(self._build_body(), 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        outer.addWidget(self.progress)

        self.status = QLabel("Готово")
        self.status.setObjectName("StatusText")
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.status)

    def _build_header(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(1)
        title = QLabel("AI Usage")
        title.setObjectName("AppTitle")
        subtitle = QLabel("Локальная аналитика расхода AI-агентов")
        subtitle.setObjectName("Subtitle")
        left.addWidget(title)
        left.addWidget(subtitle)
        layout.addLayout(left)
        layout.addStretch(1)
        self.header_context = QLabel("Claude Code  ·  Сегодня")
        self.header_context.setObjectName("Muted")
        layout.addWidget(self.header_context, 0, Qt.AlignmentFlag.AlignBottom)
        return layout

    def _build_controls(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("ControlPanel")
        grid = QGridLayout(panel)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(9)

        self.agent_combo = QComboBox()
        for key, label in AGENTS:
            self.agent_combo.addItem(label, key)
        self.agent_combo.setMinimumWidth(150)

        self.period_combo = QComboBox()
        for key, label in PRESETS:
            self.period_combo.addItem(label, key)
        self.period_combo.setMinimumWidth(190)

        self.start_edit = QDateTimeEdit()
        self.end_edit = QDateTimeEdit()
        for editor in (self.start_edit, self.end_edit):
            editor.setDisplayFormat("dd.MM.yyyy HH:mm")
            editor.setCalendarPopup(True)
            editor.setMinimumWidth(170)

        self.calculate_button = QPushButton("Рассчитать")
        self.calculate_button.setObjectName("PrimaryButton")
        self.calculate_button.setMinimumWidth(118)
        self.export_button = QPushButton("Экспорт")
        self.export_button.setMinimumWidth(96)
        self.export_button.setEnabled(False)

        controls = [
            ("Агент", self.agent_combo),
            ("Период", self.period_combo),
            ("Начало", self.start_edit),
            ("Конец", self.end_edit),
        ]
        for column, (name, widget) in enumerate(controls):
            field = QVBoxLayout()
            field.setSpacing(4)
            label = QLabel(name)
            label.setObjectName("FieldLabel")
            field.addWidget(label)
            field.addWidget(widget)
            grid.addLayout(field, 0, column)

        grid.addWidget(self.calculate_button, 0, 4, Qt.AlignmentFlag.AlignBottom)
        grid.addWidget(self.export_button, 0, 5, Qt.AlignmentFlag.AlignBottom)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск по тредам и проектам")
        self.hidden_button = QPushButton("Показывать скрытые")
        self.hidden_button.setObjectName("ToggleButton")
        self.hidden_button.setCheckable(True)
        grid.addWidget(self.search_edit, 1, 0, 1, 4)
        grid.addWidget(self.hidden_button, 1, 4, 1, 2)
        grid.setColumnStretch(3, 1)

        self.agent_combo.currentIndexChanged.connect(
            lambda _index: self._agent_changed()
        )
        self.period_combo.currentIndexChanged.connect(
            lambda _index: self._period_changed()
        )
        self.calculate_button.clicked.connect(lambda _checked=False: self.calculate())
        self.export_button.clicked.connect(lambda _checked=False: self.export_report())
        self.search_edit.returnPressed.connect(self.calculate)
        self.hidden_button.toggled.connect(
            lambda _checked: self.calculate()
        )
        return panel

    def _build_metrics(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(9)
        specs = [
            ("tokens", "Токены", "за выбранный период"),
            ("cost", "Условная стоимость", "сравнительный эквивалент"),
            ("threads", "Треды", "рабочие треды"),
            ("weekly", "Недельная квота", "текущее значение"),
            ("five", "Пятичасовое окно", "текущее значение"),
            ("consumed", "Расход недели", "оценка за период"),
        ]
        self.cards: dict[str, MetricCard] = {}
        for key, label, note in specs:
            card = MetricCard(label, note)
            self.cards[key] = card
            layout.addWidget(card, 1)
        return layout

    def _build_body(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(12)
        nav = QFrame()
        nav.setObjectName("Sidebar")
        nav.setFixedWidth(168)
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(8, 10, 8, 10)
        nav_layout.setSpacing(4)

        self.stack = QStackedWidget()
        pages = [
            ("Обзор", self._build_overview_page()),
            ("Треды", self._build_threads_page()),
            ("Проекты", self._build_projects_page()),
            ("По времени", self._build_timeline_page()),
            ("Модели", self._build_models_page()),
        ]
        self.nav_buttons: list[QPushButton] = []
        for index, (label, page) in enumerate(pages):
            button = QPushButton(label)
            button.setObjectName("NavButton")
            button.setProperty("active", index == 0)
            button.clicked.connect(
                lambda _checked=False, i=index: self._show_page(i)
            )
            nav_layout.addWidget(button)
            self.nav_buttons.append(button)
            self.stack.addWidget(page)
        nav_layout.addStretch(1)
        version = QLabel(f"v{APP_VERSION}")
        version.setObjectName("Muted")
        nav_layout.addWidget(version)
        layout.addWidget(nav)
        layout.addWidget(self.stack, 1)
        return layout

    def _page(self, title: str, subtitle: str, content: QWidget) -> QWidget:
        panel = QFrame()
        panel.setObjectName("ContentPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(9)
        heading = QLabel(title)
        heading.setObjectName("PageTitle")
        caption = QLabel(subtitle)
        caption.setObjectName("Subtitle")
        caption.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(caption)
        layout.addWidget(content, 1)
        return panel

    def _build_overview_page(self) -> QWidget:
        self.overview_tree = QTreeWidget()
        self.overview_tree.setHeaderLabels(
            [
                "Проект / тред",
                "Модели",
                "Тредов",
                "Токены",
                "Стоимость",
                "Доля",
                "Лимит, п.п.",
                "Последняя активность",
            ]
        )
        configure_tree(self.overview_tree, stretch_column=0)
        header = self.overview_tree.header()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.overview_tree.setColumnWidth(1, 190)
        for column, width in (
            (2, 74),
            (3, 105),
            (4, 96),
            (5, 74),
            (6, 92),
            (7, 168),
        ):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.overview_tree.setColumnWidth(column, width)
        self.overview_tree.itemDoubleClicked.connect(
            self._overview_double_clicked
        )
        self.overview_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.overview_tree.customContextMenuRequested.connect(
            lambda point: self._thread_context_menu(self.overview_tree, point)
        )
        return self._page(
            "Обзор",
            "Проекты раскрываются в треды. Лимит, п.п. являются оценкой.",
            self.overview_tree,
        )

    def _build_threads_page(self) -> QWidget:
        self.threads_table = make_table(
            [
                "Тред",
                "Проект",
                "Модели",
                "Токены",
                "Стоимость",
                "Доля",
                "Лимит, п.п.",
                "Субагенты",
                "Начало",
                "Конец",
            ],
            stretch_column=0,
        )
        self.threads_table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.threads_table.customContextMenuRequested.connect(
            lambda point: self._thread_context_menu(self.threads_table, point)
        )
        self.threads_table.cellDoubleClicked.connect(
            lambda *_args: self._open_selected_detail()
        )
        return self._page(
            "Треды",
            "Отдельные сессии и объединённые задачи.",
            self.threads_table,
        )

    def _build_projects_page(self) -> QWidget:
        self.projects_table = make_table(
            ["Проект", "Тредов", "Токены", "Стоимость", "Доля", "Лимит, п.п."],
            stretch_column=0,
        )
        self.projects_table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.projects_table.customContextMenuRequested.connect(
            self._project_context_menu
        )
        return self._page(
            "Проекты",
            "Суммарный расход всех тредов внутри проекта.",
            self.projects_table,
        )

    def _build_timeline_page(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.timeline_chart = TimelineChart()
        self.timeline_table = make_table(
            ["Интервал", "Токены", "Стоимость"], stretch_column=0
        )
        layout.addWidget(self.timeline_chart)
        layout.addWidget(self.timeline_table, 1)
        return self._page(
            "По времени",
            "Почасовая или дневная динамика периода.",
            content,
        )

    def _build_models_page(self) -> QWidget:
        self.models_table = make_table(
            [
                "Модель",
                "Ввод",
                "Вывод",
                "Кэш создан",
                "Кэш прочитан",
                "Токены",
                "Стоимость",
                "Доля",
                "Лимит",
                "П.п. периода",
                "Сброс",
            ],
            stretch_column=0,
        )
        self.models_table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.models_table.customContextMenuRequested.connect(
            self._model_context_menu
        )
        return self._page(
            "Модели",
            "Распределение расхода по моделям.",
            self.models_table,
        )

    # ------------------------------- controls
    def _show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for current, button in enumerate(self.nav_buttons):
            button.setProperty("active", current == index)
            button.style().unpolish(button)
            button.style().polish(button)

    def _agent_changed(self) -> None:
        if not getattr(self, "_signals_enabled", False):
            return
        self._update_header_context()
        self.calculate()

    def _period_changed(self) -> None:
        if not getattr(self, "_signals_enabled", False):
            return
        self._set_period(str(self.period_combo.currentData()))
        self._update_header_context()
        self.calculate()

    def _set_period(self, key: str) -> None:
        period = build_period(key, now_local())
        self.start_edit.setDateTime(QDateTime(period.start_local))
        self.end_edit.setDateTime(QDateTime(period.end_local))
        custom = key == "custom"
        self.start_edit.setEnabled(custom)
        self.end_edit.setEnabled(custom)

    def _set_analysis_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self.agent_combo,
            self.period_combo,
            self.start_edit,
            self.end_edit,
            self.search_edit,
            self.hidden_button,
        ):
            widget.setEnabled(enabled)
        if enabled:
            custom = str(self.period_combo.currentData()) == "custom"
            self.start_edit.setEnabled(custom)
            self.end_edit.setEnabled(custom)

    def _current_period(self) -> Period:
        key = str(self.period_combo.currentData())
        if key != "custom":
            return build_period(key, now_local())
        start = self.start_edit.dateTime().toPython().strftime("%Y-%m-%d %H:%M")
        end = self.end_edit.dateTime().toPython().strftime("%Y-%m-%d %H:%M")
        return parse_custom(start, end, now_local())

    def _update_header_context(self) -> None:
        self.header_context.setText(
            f"{self.agent_combo.currentText()}  ·  {self.period_combo.currentText()}"
        )

    # ------------------------------- analysis
    @Slot()
    def calculate(self) -> None:
        if self._busy or not getattr(self, "_signals_enabled", False):
            return
        try:
            period = self._current_period()
        except ValueError as error:
            QMessageBox.warning(self, APP_NAME, str(error))
            return
        self.last_period = period
        agent = str(self.agent_combo.currentData())
        self._busy = True
        self._set_analysis_controls_enabled(False)
        self.calculate_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.progress.show()
        self.status.setText(
            f"Рассчитываю: {AGENT_LABELS.get(agent, agent)}, "
            f"{period.label.lower()}…"
        )
        worker = AnalysisWorker(
            self.analyzer,
            agent,
            period,
            self.search_edit.text().strip(),
            self.hidden_button.isChecked(),
        )
        worker.signals.finished.connect(self._analysis_finished)
        worker.signals.failed.connect(self._analysis_failed)
        self._worker = worker
        self.thread_pool.start(worker)

    def _cached_quota(self, provider: str) -> QuotaSnapshot:
        labels = {
            "claude": ("Недельная квота", "Пятичасовое окно", "Fable"),
            "codex": ("Недельная квота", "Codex Spark", "Codex Spark"),
            "antigravity": ("Лимит модели", "Модельные пулы", ""),
            "grok": ("Grok · неделя", "Следующий сброс", ""),
        }
        main, secondary, extra = labels.get(provider, ("Квота", "Доп. квота", ""))
        snapshot = cached_snapshot(self.db, provider)
        snapshot.main_label = snapshot.main_label or main
        snapshot.secondary_label = snapshot.secondary_label or secondary
        snapshot.extra_label = snapshot.extra_label or extra
        main_window = snapshot.main_window()
        if main_window is not None:
            snapshot.weekly = main_window.used_percent
            snapshot.weekly_resets_at = main_window.resets_at
            snapshot.main_label = main_window.title if provider == "antigravity" else main
        return snapshot

    @staticmethod
    def _model_key(value: str) -> str:
        return "".join(ch for ch in value.casefold() if ch.isalnum())

    @staticmethod
    def _window_short(title: str) -> str:
        text = title.replace("Gemini ", "G").replace("Claude ", "").replace(" (Thinking)", "")
        return text.replace(" (High)", " H").replace(" (Medium)", " M").replace(" (Low)", " L")

    def _limit_text(self, values: dict[str, float], snapshot: QuotaSnapshot) -> str:
        if not values:
            return ""
        by_id = {window.id: window for window in snapshot.windows}
        parts = []
        for window_id, value in sorted(values.items(), key=lambda item: item[1], reverse=True):
            window = by_id.get(window_id)
            label = self._window_short(window.title) if window else window_id
            parts.append(f"{label}: {format_pp(value)}")
        return "; ".join(parts)

    def _apply_quota_to_result(self, result: AnalysisResult) -> None:
        if result.agent not in {"claude", "codex", "grok", "antigravity"}:
            return
        snapshot = self._quota_cache.get(result.agent) or QuotaSnapshot(provider=result.agent)
        result.quota = snapshot
        result.quota_consumed_by_window = {}
        thread_rows = result.all_threads or result.threads
        now_utc = dt.datetime.now(dt.timezone.utc)
        includes_now = result.period.start_utc <= now_utc <= result.period.end_utc
        windows = snapshot.windows
        if result.agent == "claude":
            current = snapshot.weekly if includes_now else None
            calculated = weekly_consumed(result.period, self.db, current, current_source=snapshot.source)
            if calculated is not None:
                result.weekly_consumed_pp = calculated
            if result.weekly_consumed_pp is not None:
                result.quota_consumed_by_window["secondary"] = result.weekly_consumed_pp
        elif not windows and snapshot.weekly is not None:
            current = snapshot.weekly if includes_now else None
            calculated = quota_consumed(result.period, self.db, current, result.agent)
            if calculated is not None:
                result.weekly_consumed_pp = calculated
        for window in windows:
            if result.agent == "claude" and window.id == "secondary":
                continue
            current = window.used_percent if includes_now else None
            consumed = quota_window_consumed(
                result.period, self.db, current, result.agent, window.id, current_source=snapshot.source,
            )
            if consumed is not None:
                result.quota_consumed_by_window[window.id] = consumed
        main_id = snapshot.main_window_id
        if result.agent != "claude" and main_id in result.quota_consumed_by_window:
            result.weekly_consumed_pp = result.quota_consumed_by_window[main_id]

        for row in thread_rows:
            row.weekly_pp = None; row.quota_pp_by_window = {}; row.limit_pp_text = ""
        for project in result.projects:
            project.quota_pp_by_window = {}; project.limit_pp_text = ""

        if result.agent != "antigravity":
            for row in thread_rows:
                row.weekly_pp = result.weekly_consumed_pp * row.share / 100 if result.weekly_consumed_pp is not None else None
                row.limit_pp_text = format_pp(row.weekly_pp) if row.weekly_pp is not None else ""
            for project in result.projects:
                value = result.weekly_consumed_pp * project.share / 100 if result.weekly_consumed_pp is not None else None
                project.limit_pp_text = format_pp(value) if value is not None else ""
            return

        model_windows = [window for window in windows if window.role == "model"]
        for window in model_windows:
            consumed = result.quota_consumed_by_window.get(window.id)
            keys = {self._model_key(window.title)}
            matching = [row for row in thread_rows if any(self._model_key(model) in keys for model in row.models)]
            if consumed is not None and matching:
                basis = sum(row.total_tokens for row in matching)
                for row in matching:
                    value = consumed * row.total_tokens / basis if basis else 0.0
                    row.quota_pp_by_window[window.id] = value
            for model in result.model_rows:
                if self._model_key(model.model) == self._model_key(window.title):
                    model.quota_percent = window.used_percent
                    model.quota_pp = consumed
                    model.quota_reset = window.resets_at
                    model.quota_window_id = window.id
        for row in thread_rows:
            row.limit_pp_text = self._limit_text(row.quota_pp_by_window, snapshot)
        by_project = {project.project: project for project in result.projects}
        for row in thread_rows:
            project = by_project.get(row.project)
            if not project:
                continue
            for window_id, value in row.quota_pp_by_window.items():
                project.quota_pp_by_window[window_id] = project.quota_pp_by_window.get(window_id, 0.0) + value
        for project in result.projects:
            project.limit_pp_text = self._limit_text(project.quota_pp_by_window, snapshot)

    def _status_text(self, result: AnalysisResult) -> str:
        parts = [
            f"Обновлено {local_hm(result.generated_at)}",
            f"{len(result.projects)} проектов",
            f"{len(result.threads)} тредов",
        ]
        warnings = [
            warning for warning in result.warnings
            if not warning.startswith("Квоты недоступны")
        ]
        if result.agent in {"claude", "codex", "grok", "antigravity"}:
            quota = result.quota
            if quota.has_data():
                precision = 2 if result.agent == "antigravity" else 0
                percent = quota.weekly
                if result.agent == "claude" and percent is None:
                    percent = quota.five_hour
                quota_value = format_percent(percent, precision) if percent is not None else "-"
                stamp = local_hm(quota.updated_at)
                parts.append(f"{quota.main_label or 'Квота'} {quota_value}" + (f" · снимок {stamp}" if stamp else ""))
            elif quota.error:
                parts.append(quota.error)
            else:
                parts.append("Квота обновляется отдельно")
            if quota.fallback_source:
                parts.append(f"{quota.fallback_source} · основной источник недоступен")
        parts.extend(warnings)
        return "  ·  ".join(parts)

    @Slot(object)
    def _analysis_finished(self, result: AnalysisResult) -> None:
        self._finish_busy()
        if self._quit_requested:
            return
        self.result = result
        self._apply_quota_to_result(result)
        source_rows = result.all_threads or result.threads
        self._thread_by_id = {row.session_id: row for row in source_rows}
        self._render_metrics(result)
        self._render_overview(result)
        self._render_threads(result)
        self._render_projects(result)
        self._render_timeline(result)
        self._render_models(result)
        self.status.setText(self._status_text(result))

    @Slot(str)
    def _analysis_failed(self, details: str) -> None:
        self._finish_busy()
        if self._quit_requested:
            return
        self.status.setText("Расчёт завершился с ошибкой")
        QMessageBox.critical(
            self,
            APP_NAME,
            "Не удалось сформировать отчёт.\n\n" + details[-1200:],
        )

    def _finish_busy(self) -> None:
        self._busy = False
        self.progress.hide()
        self._set_analysis_controls_enabled(True)
        self.calculate_button.setEnabled(True)
        self.export_button.setEnabled(self.result is not None)
        self._worker = None
        self._maybe_finish_quit()

    # ------------------------------- rendering
    def _render_metrics(self, result: AnalysisResult) -> None:
        quota = result.quota; agent = result.agent; main = quota.main_window()
        self.cards["tokens"].set_value(format_tokens(result.total_tokens), result.period.label)
        self.cards["cost"].set_value("—", "Antigravity не отдаёт API-стоимость") if agent == "antigravity" else self.cards["cost"].set_value(format_cost(result.total_cost), "не является оплатой")
        self.cards["threads"].set_value(str(result.work_threads), f"{len(result.projects)} проектов")
        self.cards["weekly"].label.setText("Недельная квота" if agent == "claude" else quota.main_label or "Основной лимит")
        precision = 2 if agent == "antigravity" else 0
        weekly = main.used_percent if agent != "claude" and main else quota.weekly
        weekly_value = format_percent(weekly, precision) if weekly is not None else "-"
        weekly_note = quota.login_method if weekly is not None else ""
        self.cards["weekly"].set_value(weekly_value, weekly_note or quota.error or "ожидание обновления")
        if agent == "claude":
            self.cards["five"].label.setText("Пятичасовое окно"); second_value = format_percent(quota.five_hour,0) if quota.five_hour is not None else "—"; second_note = "текущее окно"
        elif agent == "codex":
            self.cards["five"].label.setText(quota.extra_label or "Codex Spark"); second_value = format_percent(quota.fable_weekly,0) if quota.fable_weekly is not None else "—"; second_note = "дополнительный недельный пул" if quota.fable_weekly is not None else "нет данных"
        elif agent == "grok":
            resets=[window.resets_at for window in quota.windows if window.resets_at]
            self.cards["five"].label.setText("Сброс недели"); second_value = local_dt_text(min(resets)) if resets else "—"; second_note = quota.login_method or "Grok Build"
        else:
            model_windows=[window for window in quota.windows if window.role == "model"]; resets=[window.resets_at for window in model_windows if window.resets_at]
            self.cards["five"].label.setText("Модельных пулов"); second_value=str(len(model_windows)) if model_windows else "—"; second_note=("ближайший сброс " + local_dt_text(min(resets))) if resets else "лимиты по моделям во вкладке «Модели»"
        self.cards["five"].set_value(second_value,second_note)
        self.cards["consumed"].label.setText("Расход лимита" if agent != "antigravity" else "Расход лимитов")
        if agent == "antigravity":
            known=len(result.quota_consumed_by_window); value=f"{known} пул." if known else "—"; note="п.п. разных моделей не суммируются; см. «Модели»" if known else "история накапливается снимками"
            self.cards["consumed"].set_value(value,note)
        else:
            self.cards["consumed"].set_value(format_pp(result.weekly_consumed_pp), "за выбранный период; история накапливается снимками")

    def _render_overview(self, result: AnalysisResult) -> None:
        tree = self.overview_tree
        tree.setSortingEnabled(False)
        tree.clear()
        rows_by_project: dict[str, list[ThreadRow]] = {}
        for row in result.threads:
            rows_by_project.setdefault(row.project or "Без проекта", []).append(row)
        projects = {row.project: row for row in result.projects}
        ordered = sorted(
            rows_by_project,
            key=lambda name: (
                projects[name].cost if name in projects else 0.0,
                projects[name].total_tokens if name in projects else 0,
            ),
            reverse=True,
        )
        project_brush = QBrush(QColor(COLORS["project"]))
        project_foreground = QBrush(QColor(COLORS["project_text"]))
        for project_name in ordered:
            rows = sorted(
                rows_by_project[project_name],
                key=lambda row: (row.cost, row.total_tokens),
                reverse=True,
            )
            project = projects.get(project_name)
            share = project.share if project else 0.0
            weekly_pp = estimate_project_weekly_pp(
                share, result.weekly_consumed_pp
            )
            last = max(
                (row.last_activity for row in rows if row.last_activity),
                default=None,
            )
            models = sorted({model for row in rows for model in row.models})
            parent = QTreeWidgetItem(
                [
                    project_name,
                    ", ".join(models[:3]) + ("…" if len(models) > 3 else ""),
                    str(len(rows)),
                    format_tokens(
                        project.total_tokens
                        if project
                        else sum(row.total_tokens for row in rows)
                    ),
                    format_cost(
                        project.cost if project else sum(row.cost for row in rows)
                    ),
                    format_percent(share),
                    (project.limit_pp_text or format_pp(weekly_pp)) if project else format_pp(weekly_pp),
                    local_dt_text(last),
                ]
            )
            parent.setData(0, USER_ROLE, ("project", project_name))
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            for column in range(tree.columnCount()):
                parent.setBackground(column, project_brush)
                parent.setForeground(column, project_foreground)
            tree.addTopLevelItem(parent)

            for row in rows:
                title = ("★ " if row.pinned else "") + row.title
                if row.subagents:
                    title += f"  (+{row.subagents})"
                child = QTreeWidgetItem(
                    [
                        title,
                        ", ".join(row.models),
                        "",
                        format_tokens(row.total_tokens),
                        format_cost(row.cost),
                        format_percent(row.share),
                        row.limit_pp_text or format_pp(row.weekly_pp),
                        local_dt_text(row.last_activity),
                    ]
                )
                child.setData(0, USER_ROLE, ("thread", row.session_id))
                parent.addChild(child)
        tree.setSortingEnabled(True)

    def _render_threads(
        self, result: AnalysisResult, rows: list[ThreadRow] | None = None
    ) -> None:
        source_rows = result.threads if rows is None else rows
        rendered = []
        for row in source_rows:
            values = [
                SortItem(("★ " if row.pinned else "") + row.title),
                SortItem(row.project),
                SortItem(", ".join(row.models)),
                SortItem(format_tokens(row.total_tokens), row.total_tokens),
                SortItem(format_cost(row.cost), row.cost),
                SortItem(format_percent(row.share), row.share),
                SortItem(row.limit_pp_text or format_pp(row.weekly_pp), row.weekly_pp or max(row.quota_pp_by_window.values(), default=-1)),
                SortItem(
                    str(row.subagents) if row.subagents else "", row.subagents
                ),
                SortItem(
                    local_dt_text(row.first_activity),
                    row.first_activity
                    or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                ),
                SortItem(
                    local_dt_text(row.last_activity),
                    row.last_activity
                    or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                ),
            ]
            values[0].setData(USER_ROLE, row.session_id)
            rendered.append(values)
        fill_table(self.threads_table, rendered)
        self.threads_table.sortItems(4, Qt.SortOrder.DescendingOrder)

    def _render_projects(self, result: AnalysisResult) -> None:
        fill_table(
            self.projects_table,
            [
                [
                    SortItem(project.project),
                    SortItem(str(project.threads), project.threads),
                    SortItem(
                        format_tokens(project.total_tokens), project.total_tokens
                    ),
                    SortItem(format_cost(project.cost), project.cost),
                    SortItem(format_percent(project.share), project.share),
                    SortItem(
                        project.limit_pp_text or format_pp(estimate_project_weekly_pp(project.share, result.weekly_consumed_pp)),
                        max(project.quota_pp_by_window.values(), default=(estimate_project_weekly_pp(project.share, result.weekly_consumed_pp) or -1)),
                    ),
                ]
                for project in result.projects
            ],
        )
        for row_index, project in enumerate(result.projects):
            item = self.projects_table.item(row_index, 0)
            if item is not None:
                item.setData(USER_ROLE, project.project)
        self.projects_table.sortItems(3, Qt.SortOrder.DescendingOrder)

    def _render_timeline(self, result: AnalysisResult) -> None:
        self.timeline_chart.set_buckets(result.timeline)
        fill_table(
            self.timeline_table,
            [
                [
                    SortItem(bucket.label),
                    SortItem(
                        format_tokens(bucket.total_tokens), bucket.total_tokens
                    ),
                    SortItem(
                        format_cost(bucket.estimated_cost), bucket.estimated_cost
                    ),
                ]
                for bucket in result.timeline
            ],
        )

    def _render_models(self, result: AnalysisResult) -> None:
        antigravity = result.agent == "antigravity"
        for column in (8, 9, 10):
            self.models_table.setColumnHidden(column, not antigravity)
        fill_table(
            self.models_table,
            [[
                SortItem(model.model), SortItem(format_tokens(model.input_tokens),model.input_tokens),
                SortItem(format_tokens(model.output_tokens),model.output_tokens), SortItem(format_tokens(model.cache_creation),model.cache_creation),
                SortItem(format_tokens(model.cache_read),model.cache_read), SortItem(format_tokens(model.total_tokens),model.total_tokens),
                SortItem(format_cost(model.cost),model.cost), SortItem(format_percent(model.share),model.share),
                SortItem(format_percent(model.quota_percent,2) if model.quota_percent is not None else "—", model.quota_percent if model.quota_percent is not None else -1),
                SortItem(format_pp(model.quota_pp), model.quota_pp if model.quota_pp is not None else -1),
                SortItem(local_dt_text(model.quota_reset), model.quota_reset or dt.datetime.min.replace(tzinfo=dt.timezone.utc)),
            ] for model in result.model_rows],
        )
        for row_index, model in enumerate(result.model_rows):
            item = self.models_table.item(row_index, 0)
            if item is not None:
                item.setData(USER_ROLE, model.model)
        self.models_table.sortItems(5 if antigravity else 6, Qt.SortOrder.DescendingOrder)

    # ------------------------------- thread actions
    def _overview_double_clicked(
        self, item: QTreeWidgetItem, _column: int
    ) -> None:
        data = item.data(0, USER_ROLE)
        if isinstance(data, tuple) and data[0] == "thread":
            self._open_detail(self._thread_by_id.get(data[1]))
        elif item.childCount():
            item.setExpanded(not item.isExpanded())

    def _show_threads_for(self, kind: str, value: str) -> None:
        if self.result is None:
            return
        source_rows = self.result.all_threads or self.result.threads
        needle = value.casefold()
        if kind == "project":
            rows = [row for row in source_rows if row.project.casefold() == needle]
            label = f"проекту «{value}»"
        else:
            rows = [
                row for row in source_rows
                if any(model.casefold() == needle for model in row.models)
            ]
            label = f"модели «{value}»"
        self._show_page(1)
        self._render_threads(self.result, rows)
        self.status.setText(
            f"Показаны треды по {label}: {len(rows)} за выбранный период"
        )

    def _project_context_menu(self, point: QPoint) -> None:
        item = self.projects_table.itemAt(point)
        if item is None:
            return
        self.projects_table.selectRow(item.row())
        project = item.data(USER_ROLE)
        if not isinstance(project, str) or not project:
            return
        menu = QMenu(self)
        show_threads = menu.addAction("Показать треды")
        if menu.exec(self.projects_table.viewport().mapToGlobal(point)) is show_threads:
            self._show_threads_for("project", project)

    def _model_context_menu(self, point: QPoint) -> None:
        item = self.models_table.itemAt(point)
        if item is None:
            return
        self.models_table.selectRow(item.row())
        model = item.data(USER_ROLE)
        if not isinstance(model, str) or not model:
            return
        menu = QMenu(self)
        show_threads = menu.addAction("Показать треды")
        if menu.exec(self.models_table.viewport().mapToGlobal(point)) is show_threads:
            self._show_threads_for("model", model)

    def _selected_threads(self, source: QWidget | None = None) -> list[ThreadRow]:
        rows: list[ThreadRow] = []
        if source is self.overview_tree:
            for item in self.overview_tree.selectedItems():
                data = item.data(0, USER_ROLE)
                if isinstance(data, tuple) and data[0] == "thread":
                    row = self._thread_by_id.get(data[1])
                    if row:
                        rows.append(row)
        else:
            for index in self.threads_table.selectionModel().selectedRows():
                item = self.threads_table.item(index.row(), 0)
                if item:
                    row = self._thread_by_id.get(str(item.data(USER_ROLE)))
                    if row:
                        rows.append(row)
        return list({row.session_id: row for row in rows}.values())

    def _thread_context_menu(self, source: QWidget, point: QPoint) -> None:
        rows = self._selected_threads(source)
        if not rows:
            return
        menu = QMenu(self)
        rename = menu.addAction("Переименовать")
        rename.setEnabled(len(rows) == 1)
        hide = menu.addAction(
            "Показать" if any(row.hidden for row in rows) else "Скрыть"
        )
        pin = menu.addAction(
            "Открепить" if any(row.pinned for row in rows) else "Закрепить"
        )
        menu.addSeparator()
        group = menu.addAction("Объединить в задачу")
        group.setEnabled(len(rows) >= 2)
        ungroup = menu.addAction("Снять объединение")
        menu.addSeparator()
        detail = menu.addAction("Открыть детализацию")
        action = menu.exec(source.mapToGlobal(point))
        if action is rename:
            self._rename_thread(rows[0])
        elif action is hide:
            self._set_hidden(rows, not any(row.hidden for row in rows))
        elif action is pin:
            self._set_pinned(rows, not any(row.pinned for row in rows))
        elif action is group:
            self._group_threads(rows)
        elif action is ungroup:
            self._ungroup_threads(rows)
        elif action is detail:
            self._open_detail(rows[0])

    def _rename_thread(self, row: ThreadRow) -> None:
        text, accepted = QInputDialog.getText(
            self,
            "Переименовать тред",
            "Новое название",
            text=row.title,
        )
        if accepted and text.strip():
            self.db.set_title_override(row.session_id, text.strip())
            self.calculate()

    def _set_hidden(self, rows: list[ThreadRow], hidden: bool) -> None:
        for row in rows:
            for session_id in row.member_ids or [row.session_id]:
                self.db.set_hidden(session_id, hidden)
            self.db.set_hidden(row.session_id, hidden)
        self.calculate()

    def _set_pinned(self, rows: list[ThreadRow], pinned: bool) -> None:
        for row in rows:
            self.db.set_pinned(row.session_id, pinned)
        self.calculate()

    def _group_threads(self, rows: list[ThreadRow]) -> None:
        text, accepted = QInputDialog.getText(
            self, "Объединить треды", "Название задачи"
        )
        if accepted and text.strip():
            self.db.set_group(
                [row.session_id for row in rows], text.strip()
            )
            self.calculate()

    def _ungroup_threads(self, rows: list[ThreadRow]) -> None:
        for row in rows:
            if row.is_group and row.group_name:
                self.db.clear_group_by_name(row.group_name)
            else:
                self.db.clear_group([row.session_id])
        self.calculate()

    def _open_selected_detail(self) -> None:
        rows = self._selected_threads(self.threads_table)
        if rows:
            self._open_detail(rows[0])

    def _open_detail(self, row: ThreadRow | None) -> None:
        if row is not None and self.result is not None:
            DetailDialog(row, self.result, self).exec()

    # ------------------------------- export/tray
    def export_report(self) -> None:
        if self.result is None:
            return
        default = (
            f"{self.result.agent}-usage-{self.result.period.key}-"
            f"{now_local():%Y%m%d-%H%M}.xlsx"
        )
        path, selected = QFileDialog.getSaveFileName(
            self,
            "Экспорт отчёта",
            str(Path.home() / "Desktop" / default),
            "Excel (*.xlsx);;HTML (*.html);;JSON (*.json);;CSV (*.csv)",
        )
        if not path:
            return
        suffix = Path(path).suffix.casefold().lstrip(".")
        mapping = {
            "Excel (*.xlsx)": "xlsx",
            "HTML (*.html)": "html",
            "JSON (*.json)": "json",
            "CSV (*.csv)": "csv",
        }
        fmt = (
            suffix
            if suffix in {"xlsx", "html", "json", "csv"}
            else mapping.get(selected, "xlsx")
        )
        target = Path(path)
        if target.suffix.casefold() != f".{fmt}":
            target = target.with_suffix(f".{fmt}")
        try:
            export(self.result, target, fmt)
        except Exception as error:
            QMessageBox.critical(
                self,
                APP_NAME,
                f"Не удалось экспортировать отчёт:\n{error}",
            )
            return
        self.status.setText(f"Отчёт сохранён: {target}")

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(QIcon(str(ensure_icon())), self)
        menu = QMenu()
        show_action = QAction("Открыть", self)
        show_action.triggered.connect(self.show_window)
        refresh_action = QAction("Обновить отчёт", self)
        refresh_action.triggered.connect(self.calculate)
        today_action = QAction("Claude за сегодня", self)
        today_action.triggered.connect(self._calculate_today)
        exit_action = QAction("Выход", self)
        exit_action.triggered.connect(self.quit_application)
        menu.addAction(show_action)
        menu.addAction(refresh_action)
        menu.addAction(today_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip(f"{APP_NAME} {APP_VERSION}")
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _collect_quota_background(self) -> None:
        if self._quit_requested:
            return
        if self._quota_worker is not None or self._quota_queue:
            return
        self._quota_queue = ["claude", "codex", "grok", "antigravity"]
        self._collect_next_quota()

    def _collect_next_quota(self) -> None:
        if self._quit_requested:
            self._quota_queue.clear()
            self._maybe_finish_quit()
            return
        if self._quota_worker is not None or not self._quota_queue:
            return
        provider = self._quota_queue.pop(0)
        worker = QuotaWorker(provider, self.db)
        worker.signals.finished.connect(self._quota_collected)
        worker.signals.failed.connect(self._quota_failed)
        self._quota_worker = worker
        self.thread_pool.start(worker)

    def _refresh_tray_quota(self) -> None:
        if self.tray is None:
            return
        parts = []
        for provider, label in (("claude", "Claude"), ("codex", "Codex"), ("grok", "Grok"), ("antigravity", "Antigravity")):
            snapshot = self._quota_cache.get(provider)
            if snapshot and snapshot.weekly is not None:
                precision = 2 if provider == "antigravity" else 0
                parts.append(f"{label}: {format_percent(snapshot.weekly, precision)}")
        line = " · ".join(parts) if parts else "квоты обновляются"
        self.tray.setToolTip(f"{APP_NAME} {APP_VERSION}\n{line}\n{now_local():%H:%M}")

    @Slot(object)
    def _quota_collected(self, snapshot) -> None:
        self._quota_worker = None
        source = str(snapshot.source or "")
        provider = snapshot.provider or (source.rsplit(":", 1)[-1] if ":" in source else "")
        if snapshot.has_data() and provider:
            self._quota_cache[provider] = snapshot
            if self.result is not None and self.result.agent == provider:
                self._apply_quota_to_result(self.result)
                self._render_metrics(self.result)
                self._render_overview(self.result)
                self._render_threads(self.result)
                self._render_projects(self.result)
                self.status.setText(self._status_text(self.result))
        self._refresh_tray_quota()
        if self._quit_requested:
            self._quota_queue.clear()
            self._maybe_finish_quit()
        elif self._quota_queue:
            QTimer.singleShot(50, self._collect_next_quota)

    @Slot(str)
    def _quota_failed(self, _details: str) -> None:
        self._quota_worker = None
        if self._quit_requested:
            self._quota_queue.clear()
            self._maybe_finish_quit()
        elif self._quota_queue:
            QTimer.singleShot(50, self._collect_next_quota)
        else:
            QTimer.singleShot(60_000, self._collect_quota_background)

    def _calculate_today(self) -> None:
        self.agent_combo.setCurrentIndex(
            self.agent_combo.findData("claude")
        )
        self.period_combo.setCurrentIndex(
            self.period_combo.findData("today")
        )
        self.show_window()
        self.calculate()

    def _tray_activated(
        self, reason: QSystemTrayIcon.ActivationReason
    ) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_window()

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_application(self) -> None:
        self._quit_requested = True
        if self.tray is not None:
            self.tray.hide()
        if hasattr(self, "quota_timer"):
            self.quota_timer.stop()
        self._quota_queue.clear()
        self._maybe_finish_quit()

    def _has_active_workers(self) -> bool:
        return self._worker is not None or self._quota_worker is not None or bool(self._quota_queue)

    def _maybe_finish_quit(self) -> None:
        if self._quit_requested and not self._has_active_workers():
            app = QApplication.instance()
            if app:
                app.quit()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._quit_requested or self.tray is None:
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray.showMessage(
            APP_NAME,
            "Программа продолжает работать в системном трее.",
            QSystemTrayIcon.MessageIcon.Information,
            1600,
        )


def make_table(
    headers: list[str], stretch_column: int = 0
) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(
        QAbstractItemView.SelectionBehavior.SelectRows
    )
    table.setSelectionMode(
        QAbstractItemView.SelectionMode.ExtendedSelection
    )
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSortingEnabled(True)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(42)
    table.setShowGrid(False)
    header = table.horizontalHeader()
    for column in range(len(headers)):
        header.setSectionResizeMode(
            column, QHeaderView.ResizeMode.Interactive
        )
    table.resizeColumnsToContents()
    if 0 <= stretch_column < len(headers):
        table.setColumnWidth(stretch_column, max(220, table.columnWidth(stretch_column)))
    header.setStretchLastSection(False)
    return table


def configure_tree(tree: QTreeWidget, stretch_column: int = 0) -> None:
    tree.setSelectionMode(
        QAbstractItemView.SelectionMode.ExtendedSelection
    )
    tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    tree.setUniformRowHeights(True)
    tree.setIndentation(18)
    tree.setRootIsDecorated(True)
    tree.setAnimated(False)
    header = tree.header()
    for column in range(tree.columnCount()):
        header.setSectionResizeMode(
            column, QHeaderView.ResizeMode.Interactive
        )
    if 0 <= stretch_column < tree.columnCount():
        tree.setColumnWidth(stretch_column, 240)


def fill_table(
    table: QTableWidget, rows: list[list[QTableWidgetItem]]
) -> None:
    sorting = table.isSortingEnabled()
    table.setSortingEnabled(False)
    table.setRowCount(len(rows))
    for row_index, values in enumerate(rows):
        for column, item in enumerate(values):
            if column > 0:
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight
                    | Qt.AlignmentFlag.AlignVCenter
                )
            table.setItem(row_index, column, item)
    table.setSortingEnabled(sorting)


def apply_windows_dark_frame(window: QMainWindow) -> None:
    """Use native DWM title bar while keeping the Qt client area styled."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        dwm = ctypes.windll.dwmapi
        dark = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark)
        )
        for attribute, color in (
            (35, COLORS["bg"]),
            (36, COLORS["text"]),
            (34, COLORS["border"]),
        ):
            value = color.lstrip("#")
            red = int(value[:2], 16)
            green = int(value[2:4], 16)
            blue = int(value[4:], 16)
            native = ctypes.c_uint(
                red | (green << 8) | (blue << 16)
            )
            dwm.DwmSetWindowAttribute(
                hwnd,
                attribute,
                ctypes.byref(native),
                ctypes.sizeof(native),
            )
    except Exception:
        return


def run_qt_gui(db: Database, *, start_hidden: bool = False) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setStyleSheet(build_stylesheet())
    window = MainWindow(db, start_hidden=start_hidden)
    apply_windows_dark_frame(window)
    if not start_hidden:
        window.show_window()
    return app.exec()
