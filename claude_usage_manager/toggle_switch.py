"""Переключатель-«таблетка» для панели настроек виджета."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton, QWidget

SWITCH_WIDTH = 32
SWITCH_HEIGHT = 18
KNOB_MARGIN = 2.0
ON_COLOR = "#4C9AF0"
OFF_COLOR = "#3A4656"
KNOB_COLOR = "#F3F6FA"


class ToggleSwitch(QAbstractButton):
    """Включено или выключено; сигнал `toggled(bool)` приходит от QAbstractButton."""

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(SWITCH_WIDTH, SWITCH_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        track = QColor(ON_COLOR if self.isChecked() else OFF_COLOR)
        painter.setBrush(track)
        radius = self.height() / 2.0
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), radius, radius)
        knob = self.height() - 2 * KNOB_MARGIN
        left = self.width() - knob - KNOB_MARGIN if self.isChecked() else KNOB_MARGIN
        painter.setBrush(QColor(KNOB_COLOR))
        painter.drawEllipse(QRectF(left, KNOB_MARGIN, knob, knob))
