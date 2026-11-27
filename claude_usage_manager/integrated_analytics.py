"""Integration adapter that embeds the existing analytics window in AI Limits Monitor."""
from __future__ import annotations

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QTreeWidgetItemIterator

from .claude_desktop import read_best_local_claude_quota
from .database import Database
from .models import QuotaSnapshot
from .provider_settings import provider_enabled
from .qt_gui import MainWindow, apply_windows_dark_frame

_QUOTA_PROVIDERS = ("claude", "codex", "grok", "antigravity")


class IntegratedAnalyticsWindow(MainWindow):
    """Full AI Usage analytics window without a second tray icon or process."""

    def __init__(self, db: Database) -> None:
        super().__init__(
            db,
            start_hidden=True,
            auto_calculate=False,
            enable_tray=False,
        )
        apply_windows_dark_frame(self)

    def _cached_quota(self, provider: str) -> QuotaSnapshot:
        if provider == "claude" and provider_enabled(provider):
            return read_best_local_claude_quota()
        return super()._cached_quota(provider)

    def reload_cached_quotas(self) -> None:
        """Reload the current quotas used by the compact monitor."""
        new_cache = {
            provider: self._cached_quota(provider)
            for provider in _QUOTA_PROVIDERS
        }
        def signature(cache):
            return tuple((provider, snapshot.source, snapshot.five_hour, snapshot.weekly, snapshot.fable_weekly,
                          tuple((w.id, w.used_percent, w.resets_at, w.window_minutes, w.role)
                                for w in snapshot.windows))
                         for provider, snapshot in sorted(cache.items()))
        changed = signature(new_cache) != signature(self._quota_cache)
        self._quota_cache = new_cache
        if self.result is None or not changed:
            return
        tree = self.overview_tree
        iterator = QTreeWidgetItemIterator(tree)
        expanded, selected = set(), set()
        while iterator.value():
            item = iterator.value()
            key = repr(item.data(0, Qt.ItemDataRole.UserRole))
            if item.isExpanded(): expanded.add(key)
            if item.isSelected(): selected.add(key)
            iterator += 1
        tree_scroll = (tree.verticalScrollBar().value(), tree.horizontalScrollBar().value())
        tables = (self.threads_table, self.projects_table, self.models_table)
        table_states = []
        for table in tables:
            keys = {repr(table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole) or table.item(index.row(), 0).text())
                    for index in table.selectionModel().selectedRows() if table.item(index.row(), 0)}
            table_states.append((table.horizontalHeader().sortIndicatorSection(), table.horizontalHeader().sortIndicatorOrder(),
                                 keys, table.verticalScrollBar().value(), table.horizontalScrollBar().value()))
        shown_ids = {self.threads_table.item(i, 0).data(Qt.ItemDataRole.UserRole)
                     for i in range(self.threads_table.rowCount()) if self.threads_table.item(i, 0)}
        all_ids = {row.session_id for row in self.result.threads}
        filtered = [row for row in self.result.threads if row.session_id in shown_ids] if shown_ids != all_ids else None
        self.setUpdatesEnabled(False)
        try:
            self._apply_quota_to_result(self.result)
            self._render_metrics(self.result)
            self._render_overview(self.result)
            self._render_threads(self.result, filtered)
            self._render_projects(self.result)
            self._render_models(self.result)
            iterator = QTreeWidgetItemIterator(tree)
            while iterator.value():
                item = iterator.value()
                key = repr(item.data(0, Qt.ItemDataRole.UserRole))
                item.setExpanded(key in expanded)
                item.setSelected(key in selected)
                iterator += 1
            tree.verticalScrollBar().setValue(tree_scroll[0]); tree.horizontalScrollBar().setValue(tree_scroll[1])
            for table, (column, order, keys, vertical, horizontal) in zip(tables, table_states):
                if column >= 0: table.sortItems(column, order)
                for index in range(table.rowCount()):
                    item = table.item(index, 0)
                    if item and repr(item.data(Qt.ItemDataRole.UserRole) or item.text()) in keys:
                        table.selectionModel().select(table.model().index(index, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
                table.verticalScrollBar().setValue(vertical); table.horizontalScrollBar().setValue(horizontal)
            self.status.setText(self._status_text(self.result))
        finally:
            self.setUpdatesEnabled(True)

    def open_and_refresh(self) -> None:
        """Bring analytics to front and calculate it from the current local data."""
        self.reload_cached_quotas()
        self.show_window()
        self.calculate()
