"""Полоса у бокового края окна без рамки: тянется мышью и меняет только ширину."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter
from PySide6.QtWidgets import QWidget

from .qt_theme import COLORS

GRIP_WIDTH = 12
HANDLE_WIDTH = 4.0
HANDLE_HEIGHT = 36.0


class EdgeGrip(QWidget):
    """Боковая ручка изменения ширины окна.

    Пока кнопка мыши нажата, Qt направляет сюда и события за пределами ручки.
    Смещение от нажатия передается окну в логических пикселях.
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
        self.setAccessibleName("Левый край окна" if edge == "left" else "Правый край окна")
        self.setToolTip("Зажми левую кнопку мыши и потяни край влево или вправо, чтобы изменить ширину окна.")

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
        painter = QPainter(self)
        # Windows пропускает мышь сквозь полностью прозрачные пиксели окна.
        # Тонкая подложка сохраняет захват даже на скругленных углах окна.
        painter.fillRect(self.rect(), QColor(9, 12, 16, 16))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        color = COLORS["text"] if self.underMouse() or self.dragging else COLORS["faint"]
        painter.setBrush(QColor(color))
        height = min(HANDLE_HEIGHT, float(self.height()))
        painter.drawRoundedRect(
            QRectF((self.width() - HANDLE_WIDTH) / 2.0, (self.height() - height) / 2.0, HANDLE_WIDTH, height),
            HANDLE_WIDTH / 2.0,
            HANDLE_WIDTH / 2.0,
        )
