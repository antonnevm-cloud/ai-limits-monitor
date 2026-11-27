"""Small native Qt timeline chart without external plotting dependencies."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .models import TimelineBucket
from .qt_theme import COLORS
from .utils import format_tokens


class TimelineChart(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._buckets: list[TimelineBucket] = []
        self.setMinimumHeight(190)

    def set_buckets(self, buckets: list[TimelineBucket]) -> None:
        self._buckets = list(buckets)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(COLORS["panel"]))
        if not self._buckets or not any(b.total_tokens for b in self._buckets):
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "Нет данных за выбранный период")
            return

        left, top, right, bottom = 48.0, 20.0, 18.0, 34.0
        area = QRectF(left, top, max(1.0, self.width() - left - right), max(1.0, self.height() - top - bottom))
        maximum = max(bucket.total_tokens for bucket in self._buckets) or 1

        painter.setPen(QPen(QColor(COLORS["border"]), 1))
        painter.drawLine(area.bottomLeft(), area.bottomRight())
        for index in range(1, 4):
            y = area.bottom() - area.height() * index / 4
            painter.setPen(QPen(QColor(COLORS["border_soft"]), 1, Qt.DotLine))
            painter.drawLine(area.left(), y, area.right(), y)

        count = len(self._buckets)
        gap = 4.0
        bar_width = max(3.0, (area.width() - gap * max(0, count - 1)) / max(1, count))
        painter.setBrush(QColor(COLORS["accent"]))
        painter.setPen(Qt.NoPen)
        for index, bucket in enumerate(self._buckets):
            height = area.height() * bucket.total_tokens / maximum
            x = area.left() + index * (bar_width + gap)
            rect = QRectF(x, area.bottom() - height, bar_width, height)
            painter.drawRoundedRect(rect, min(3.0, bar_width / 3), min(3.0, bar_width / 3))

        painter.setPen(QColor(COLORS["muted"]))
        painter.setFont(QFont("Segoe UI Variable Text", 8))
        painter.drawText(QRectF(0, top - 4, left - 8, 20), Qt.AlignRight | Qt.AlignVCenter, format_tokens(maximum))
        if count <= 14:
            for index, bucket in enumerate(self._buckets):
                x = area.left() + index * (bar_width + gap)
                painter.save()
                painter.translate(x + bar_width / 2, area.bottom() + 8)
                painter.rotate(-30 if count > 8 else 0)
                painter.drawText(QRectF(-45, 0, 90, 24), Qt.AlignHCenter | Qt.AlignTop, bucket.label)
                painter.restore()
        else:
            for index in (0, count // 2, count - 1):
                bucket = self._buckets[index]
                x = area.left() + index * (bar_width + gap)
                painter.drawText(QRectF(x - 50, area.bottom() + 7, 100, 22), Qt.AlignHCenter | Qt.AlignTop, bucket.label)