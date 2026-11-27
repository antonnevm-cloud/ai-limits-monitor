"""Полоса у бокового края окна без рамки: тянется мышью и меняет только ширину."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter
from PySide6.QtWidgets import QWidget

GRIP_WIDTH = 5
HANDLE_WIDTH = 3.0
HANDLE_HEIGHT = 36.0
HANDLE_ALPHA = 80


class EdgeGrip(QWidget):
    """Невидимая полоса на левом или правом краю окна; при наведении показывает короткую ручку.

    Сама размеры не меняет: сообщает смещение курсора по горизонтали (в пикселях экрана)
    от точки нажатия, а окно решает, какой ширины быть.
    """

    drag_started = Signal()
    dragged = Signal(int)
    drag_finished = Signal()

    def __init__(self, edge: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if edge not in ("left", "right"):
            raise ValueError(edge)
        self.edge = edge
        self._origin: int | None = None
        self.setFixedWidth(GRIP_WIDTH)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip("Потяните, чтобы изменить ширину")

    @property
    def dragging(self) -> bool:
        return self._origin is not None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._origin = event.globalPosition().toPoint().x()
        self.drag_started.emit()
        self.update()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._origin is None:
            event.ignore()
            return
        self.dragged.emit(event.globalPosition().toPoint().x() - self._origin)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._origin is None or event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._origin = None
        self.drag_finished.emit()
        self.update()
        event.accept()

    def enterEvent(self, event: QEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        if not (self.underMouse() or self.dragging):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, HANDLE_ALPHA))
        height = min(HANDLE_HEIGHT, float(self.height()))
        painter.drawRoundedRect(
            QRectF((self.width() - HANDLE_WIDTH) / 2.0, (self.height() - height) / 2.0, HANDLE_WIDTH, height),
            HANDLE_WIDTH / 2.0,
            HANDLE_WIDTH / 2.0,
        )
