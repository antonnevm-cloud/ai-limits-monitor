"""Compact, explicitly manual editor for Claude Reset for free offers.

The widget never performs network requests or accesses Claude credentials.
"""
from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .claude_local import parse_manual_resets


class ClaudeManualResetsDialog(QDialog):
    """Edit individually expiring offers with a date picker, not raw syntax."""

    MAX_OFFERS = 30

    def __init__(self, existing: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ClaudeManualResetsDialog")
        self.setWindowTitle("Claude · бонусные сбросы")
        self.resize(510, 405)
        self.setMinimumSize(440, 345)
        self.setMaximumWidth(640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("Бонусные сбросы Claude")
        title.setObjectName("ClaudeResetsTitle")
        title.setStyleSheet("font-size: 13pt; font-weight: 700;")
        layout.addWidget(title)

        intro = QLabel(
            "Внеси доступные Reset for free из Claude → Settings → Usage. "
            "Каждая строка списка — один бонус, а не обычный сброс лимита."
        )
        intro.setObjectName("ClaudeResetsIntro")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        header = QHBoxLayout()
        header.addWidget(QLabel("Доступные бонусы"))
        header.addStretch()
        self.count_label = QLabel()
        self.count_label.setObjectName("ClaudeResetsCount")
        header.addWidget(self.count_label)
        layout.addLayout(header)

        self.offers = QListWidget(self)
        self.offers.setObjectName("ClaudeResetsList")
        self.offers.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.offers.setMinimumHeight(92)
        layout.addWidget(self.offers, 1)

        date_row = QHBoxLayout()
        date_row.setSpacing(7)
        self.expiry = QDateTimeEdit(self)
        self.expiry.setObjectName("ClaudeResetsExpiry")
        self.expiry.setCalendarPopup(True)
        self.expiry.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.expiry.setDateTime(QDateTime.currentDateTime().addDays(1))
        self.expiry.setMinimumWidth(145)
        date_row.addWidget(self.expiry, 1)

        self.add_button = QPushButton("Добавить по дате")
        self.add_button.setObjectName("ClaudeResetsAddDate")
        self.add_button.clicked.connect(self.add_dated_offer)
        date_row.addWidget(self.add_button)
        layout.addLayout(date_row)

        actions = QHBoxLayout()
        actions.setSpacing(7)
        self.add_without_expiry_button = QPushButton("Без срока")
        self.add_without_expiry_button.clicked.connect(self.add_without_expiry)
        actions.addWidget(self.add_without_expiry_button)
        self.replace_button = QPushButton("Изменить срок")
        self.replace_button.clicked.connect(self.replace_selected_date)
        actions.addWidget(self.replace_button)
        self.remove_button = QPushButton("Удалить выбранный")
        self.remove_button.clicked.connect(self.remove_selected)
        actions.addWidget(self.remove_button)
        layout.addLayout(actions)

        warning = QLabel(
            "Это ручной учёт, не автоматическая проверка Anthropic. "
            "Использованный бонус удали самостоятельно. "
            "Просроченные записи исчезнут из счётчика автоматически."
        )
        warning.setObjectName("ClaudeResetsWarning")
        warning.setWordWrap(True)
        warning.setStyleSheet("font-size: 9pt;")
        layout.addWidget(warning)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        for raw in existing.splitlines():
            raw = raw.strip()
            if raw:
                self._append(raw)
        self.offers.itemSelectionChanged.connect(self._update_controls)
        self._update_controls()

    def _append(self, text: str) -> None:
        label = (
            "Без срока действия"
            if text == "-"
            else "До " + dt.datetime.strptime(text, "%Y-%m-%d %H:%M").strftime(
                "%d.%m.%Y %H:%M"
            )
        )
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, text)
        self.offers.addItem(item)

    def _date_value(self) -> str | None:
        value = self.expiry.dateTime().toString("yyyy-MM-dd HH:mm")
        try:
            parse_manual_resets(value)
        except ValueError as error:
            QMessageBox.warning(self, "Claude · срок сброса", str(error))
            return None
        return value

    def _at_capacity(self) -> bool:
        if self.offers.count() < self.MAX_OFFERS:
            return False
        QMessageBox.warning(
            self, "Claude · сбросы",
            f"Можно учесть не более {self.MAX_OFFERS} бонусных сбросов.",
        )
        return True

    def add_dated_offer(self) -> None:
        if self._at_capacity():
            return
        value = self._date_value()
        if value is None:
            return
        self._append(value)
        self._update_controls()

    def add_without_expiry(self) -> None:
        if self._at_capacity():
            return
        self._append("-")
        self._update_controls()

    def replace_selected_date(self) -> None:
        item = self.offers.currentItem()
        if item is None:
            return
        value = self._date_value()
        if value is None:
            return
        item.setData(Qt.ItemDataRole.UserRole, value)
        item.setText(
            "До " + dt.datetime.strptime(value, "%Y-%m-%d %H:%M").strftime(
                "%d.%m.%Y %H:%M"
            )
        )
        self._update_controls()

    def remove_selected(self) -> None:
        index = self.offers.currentRow()
        if index < 0:
            return
        self.offers.takeItem(index)
        self._update_controls()

    def _update_controls(self) -> None:
        count = self.offers.count()
        self.count_label.setText(f"Добавлено: {count}")
        self.add_button.setEnabled(count < self.MAX_OFFERS)
        self.add_without_expiry_button.setEnabled(count < self.MAX_OFFERS)
        selected = self.offers.currentItem() is not None
        self.replace_button.setEnabled(selected)
        self.remove_button.setEnabled(selected)

    def value_text(self) -> str:
        """The existing storage contract: one local deadline or '-' per line."""
        return "\n".join(
            self.offers.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.offers.count())
        )
