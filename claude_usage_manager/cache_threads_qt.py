"""Qt-виджеты списка тредов с таймерами остывания кэша для компактного монитора."""
from __future__ import annotations

import datetime as dt
import html
import time
import weakref
from collections import deque

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPainterPath, QPen, QResizeEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from .cache_threads import COLLAPSED_THREADS, CacheThread, cache_hue, format_remaining
from .qt_theme import COLORS
from .thread_activity import ACTIVITY_NEEDS_REPLY, ACTIVITY_UNREAD, ACTIVITY_WORKING
from .pricing import format_money
from .thread_usage import ThreadSpend
from .thread_view import ThreadView

CACHE_BLOCK_MIN_WIDTH = 250
RING_SIZE = 46
RING_WIDTH = 4.0
SCROLLBAR_WIDTH = 6  # ширина полосы прокрутки из стиля #CacheScroll
CHIP_FILL = QColor(120, 140, 160, 38)
GLUTTON_COLOR = "#E5675E"
CHIP_ICON_SIZE = 11
QWIDGETSIZE_MAX = (1 << 24) - 1  # снять фиксированную ширину
ROW_HEIGHT = 66
# Компактный вид: тред в две строки (название; проект и короткие чипы), кольцо меньше.
COMPACT_ROW_HEIGHT = 44
COMPACT_RING_SIZE = 36
COMPACT_RING_WIDTH = 3.0
# Компактно: индикатор состояния треда внутри кольца над временем (решение владельца 12.11.2026)
RING_STATE_SIZE = 6.0  # диаметр точки или бегущего кружка
RING_STATE_PEN = 1.4
RING_STATE_ABOVE = 6.0  # центр индикатора выше центра кольца
RING_STATE_SHIFT = 3.0  # время ниже центра, чтобы пара стояла по центру
COMPACT_RING_FONT = 5.0  # цифры в кольце мелкие и обычной жирности (выбор владельца 12.11.2026)
WARN_SECONDS = 120
WARN_COLOR = "#E0B04A"
MIN_VISIBLE_ROWS = 3
ROWS_PER_BATCH = 12
BATCHED_LIST_MIN = ROWS_PER_BATCH * 4
ROWS_BATCH_DELAY_MS = 8
DOT_COLUMN = 14
HEAD_SPACING = 6  # между точкой состояния и названием
DOT_SIZE = 10
SPINNER_PERIOD = 1.0
SPINNER_INTERVAL_MS = 33
SPINNER_SPAN = 110.0
SPINNER_WIDTH = 2.0
SPINNER_TRACK_ALPHA = 70
URGENT_FRACTION = 0.1
ACTIVITY_COLORS = {ACTIVITY_WORKING: "#8E9AA8", ACTIVITY_NEEDS_REPLY: "#E8913A", ACTIVITY_UNREAD: "#4C9AF0"}
ACTIVITY_TEXTS = {
    ACTIVITY_WORKING: "Тред работает",
    ACTIVITY_NEEDS_REPLY: "Тред ждет вашего ответа",
    ACTIVITY_UNREAD: "Ход завершен, ответ не просмотрен",
}
AGENT_NAMES = {"claude": "Claude Code", "codex": "Codex", "grok": "Grok Build", "antigravity": "Antigravity"}
EMPTY_TEXT = "Нет тредов с живым кэшем"


def ttl_text(seconds: int) -> str:
    return "1 час" if seconds == 3600 else f"{seconds // 60} мин"


def tooltip_text(thread: CacheThread) -> str:
    start = thread.started_at.astimezone().strftime("%H:%M:%S")
    end = thread.expires_at.astimezone().strftime("%H:%M:%S")
    lines = [
        f"{AGENT_NAMES.get(thread.agent, thread.agent)} · {thread.project} · {thread.environment}",
        thread.title,
    ]
    if thread.activity in ACTIVITY_TEXTS:
        lines.append(ACTIVITY_TEXTS[thread.activity])
    lines.append(f"Запрос отправлен в {start}, кэш остынет в {end} (срок {ttl_text(thread.ttl_seconds)}).")
    if thread.basis:
        lines.append(thread.basis)
    elif thread.estimated:
        lines.append("Срок кэша в журнале не указан, показана оценка.")
    return "\n".join(lines)


class ElidedLabel(QLabel):
    """Метка, которая сокращает текст многоточием вместо расширения окна."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = ""
        # Названия тредов приходят из журналов: только текст, без разметки HTML и загрузки картинок.
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._apply()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply()

    def _apply(self) -> None:
        width = self.width()
        shown = self._full if width <= 1 else self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, width)
        if shown != self.text():
            self.setText(shown)


class LinkButton(QPushButton):
    """Компактная кнопка с названием действия для списка и панели вида."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("CacheLink")
        self.setAccessibleName(text)
        self.setAutoDefault(False)
        self.setFlat(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(24)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.modifiers() == Qt.KeyboardModifier.NoModifier and event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
            if not event.isAutoRepeat():
                self.click()
            event.accept()
            return
        super().keyPressEvent(event)


class CacheRing(QWidget):
    """Кольцо таймера: дуга пропорциональна остатку срока, цвет от красного к зелёному, внутри оставшееся время."""

    def __init__(
        self, parent: QWidget | None = None, size: int = RING_SIZE, width: float = RING_WIDTH, font_pt: float = 8.0, bold: bool = True
    ) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._ring_width = width
        self._font_pt = font_pt
        self._bold = bold
        self._activity = ""
        self._fraction = 0.0
        self._text = ""
        self._cooled = True

    def set_state(self, fraction: float, text: str, cooled: bool) -> None:
        if (fraction, text, cooled) == (self._fraction, self._text, self._cooled):
            return
        self._fraction, self._text, self._cooled = fraction, text, cooled
        self.update()

    @property
    def text(self) -> str:
        return self._text

    @property
    def activity(self) -> str:
        return self._activity

    @property
    def animating(self) -> bool:
        return SpinnerClock.instance().has(self)

    def set_activity(self, activity: str) -> None:
        """Компактно состояние треда рисует само кольцо: бегущий блик - работает, значок на краю - ждет ответа или не просмотрен."""
        if activity == self._activity:
            return
        self._activity = activity
        if activity == ACTIVITY_WORKING and not explicitly_hidden(self):
            SpinnerClock.instance().attach(self)
        else:
            SpinnerClock.instance().detach(self)
        self.update()

    def showEvent(self, event) -> None:
        if self._activity == ACTIVITY_WORKING:
            SpinnerClock.instance().attach(self)
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        SpinnerClock.instance().detach(self)
        super().hideEvent(event)

    @property
    def fraction(self) -> float:
        return self._fraction

    @staticmethod
    def fill_color(fraction: float) -> QColor:
        return QColor.fromHslF(cache_hue(fraction) / 360.0, 0.62, 0.48)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        inset = self._ring_width / 2.0 + 1.0
        rect = QRectF(inset, inset, self.width() - 2 * inset, self.height() - 2 * inset)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(COLORS["control"]), self._ring_width))
        painter.drawEllipse(rect)
        if self._fraction > 0:
            pen = QPen(self.fill_color(self._fraction), self._ring_width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, 90 * 16, -int(min(1.0, self._fraction) * 360 * 16))
        font = QFont(self.font())
        font.setPointSizeF(self._font_pt)
        font.setBold(self._bold and not self._cooled)
        painter.setFont(font)
        color = ACTIVITY_COLORS.get(self._activity)
        shift = 0.0
        if color is not None:
            # индикатор состояния внутри кольца над временем, время чуть ниже центра
            shift = RING_STATE_SHIFT
            center = QPointF(self.width() / 2.0, self.height() / 2.0 - RING_STATE_ABOVE)
            radius = RING_STATE_SIZE / 2.0
            if self._activity == ACTIVITY_WORKING:
                track = QColor(color)
                track.setAlpha(SPINNER_TRACK_ALPHA)
                painter.setPen(QPen(track, RING_STATE_PEN))
                painter.drawEllipse(center, radius, radius)
                arc = QPen(QColor(color), RING_STATE_PEN)
                arc.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(arc)
                start = 90.0 - spinner_angle(time.monotonic())
                painter.drawArc(QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius), int(start * 16), int(-SPINNER_SPAN * 16))
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(color))
                painter.drawEllipse(center, radius, radius)
                painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QColor(COLORS["faint"] if self._cooled else COLORS["text"]))
        painter.drawText(QRectF(0, shift, self.width(), self.height()), Qt.AlignmentFlag.AlignCenter, self._text)


def explicitly_hidden(widget: QWidget) -> bool:
    """Скрыт ли виджет явно (hide()); новый виджет до первого показа тоже isHidden(), но анимацию ему запускать надо."""
    return widget.isHidden() and widget.testAttribute(Qt.WidgetAttribute.WA_WState_ExplicitShowHide)


def spinner_angle(seconds: float) -> float:
    """Угол начала бегущей дуги в градусах (0..360) для момента `seconds`; все индикаторы вращаются синхронно."""
    return (seconds % SPINNER_PERIOD) / SPINNER_PERIOD * 360.0


class SpinnerClock(QObject):
    """Один таймер на все кольца: кадры у всех точек общие, поэтому они вращаются в одной фазе и с одной скоростью."""

    _instance: "SpinnerClock | None" = None

    def __init__(self) -> None:
        super().__init__()
        self._dots: "weakref.WeakSet[QWidget]" = weakref.WeakSet()  # точки и кольца с бегущей дугой
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(SPINNER_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    @classmethod
    def instance(cls) -> "SpinnerClock":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def attach(self, dot: QWidget) -> None:
        self._dots.add(dot)
        if not self._timer.isActive():
            self._timer.start()

    def detach(self, dot: QWidget) -> None:
        self._dots.discard(dot)
        if not self._dots:
            self._timer.stop()

    def has(self, dot: QWidget) -> bool:
        return dot in self._dots

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def _tick(self) -> None:
        for dot in list(self._dots):
            try:
                dot.update()
            except RuntimeError:
                self._dots.discard(dot)
        if not self._dots:
            self._timer.stop()


class StateDot(QWidget):
    """Точка состояния треда: серое кольцо с бегущей дугой - работает, оранжевая - ждет ответа, синяя - ход завершен и не просмотрен."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(DOT_COLUMN, DOT_COLUMN)
        self._activity = ""

    @property
    def activity(self) -> str:
        return self._activity

    @property
    def animating(self) -> bool:
        return SpinnerClock.instance().has(self)

    def set_state(self, activity: str) -> None:
        if activity == self._activity:
            return
        self._activity = activity
        if activity == ACTIVITY_WORKING and not explicitly_hidden(self):
            SpinnerClock.instance().attach(self)
        else:
            SpinnerClock.instance().detach(self)
        self.update()

    def showEvent(self, event) -> None:
        if self._activity == ACTIVITY_WORKING:
            SpinnerClock.instance().attach(self)
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        SpinnerClock.instance().detach(self)
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        color_name = ACTIVITY_COLORS.get(self._activity)
        if color_name is None:
            return
        color = QColor(color_name)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        offset = (DOT_COLUMN - DOT_SIZE) / 2.0
        if self._activity != ACTIVITY_WORKING:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QRectF(offset, offset, DOT_SIZE, DOT_SIZE))
            return
        half = SPINNER_WIDTH / 2.0
        ring = QRectF(offset + half, offset + half, DOT_SIZE - SPINNER_WIDTH, DOT_SIZE - SPINNER_WIDTH)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        track = QColor(color)
        track.setAlpha(SPINNER_TRACK_ALPHA)
        painter.setPen(QPen(track, SPINNER_WIDTH))
        painter.drawEllipse(ring)
        pen = QPen(color, SPINNER_WIDTH)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        start = 90.0 - spinner_angle(time.monotonic())
        painter.drawArc(ring, int(start * 16), int(-SPINNER_SPAN * 16))


def distribute_height_caps(naturals: list[int], excess: int, minimum: int) -> list[int]:
    """Урезает высоты блоков на `excess` пикселей поровну, но не ниже `minimum` (или собственной высоты, если она меньше)."""
    caps = list(naturals)
    floor = [min(value, minimum) for value in naturals]
    while excess > 0:
        room = [index for index, value in enumerate(caps) if value > floor[index]]
        if not room:
            break
        share = max(1, -(-excess // len(room)))
        for index in room:
            cut = min(share, caps[index] - floor[index], excess)
            caps[index] -= cut
            excess -= cut
            if excess <= 0:
                break
    return caps


def glutton_id(threads: list[CacheThread], spend: dict[str, ThreadSpend]) -> str | None:
    """Тред с наибольшей оценкой доли недели; без сравнения (меньше двух тредов с оценкой) «прожорливого» нет."""
    shares = [(spend[t.session_id].share, t.session_id) for t in threads if t.session_id in spend and spend[t.session_id].share]
    if len(shares) < 2:
        return None
    return max(shares)[1]


class ChipIcon(QWidget):
    """Значок внутри чипа: монета («перезапись») или пламя («прожорливый»)."""

    def __init__(self, kind: str, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if kind not in ("coin", "flame"):
            raise ValueError(kind)
        self.kind = kind
        self.color = color
        self.setFixedSize(CHIP_ICON_SIZE, CHIP_ICON_SIZE)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(self.color), 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.kind == "coin":
            painter.drawEllipse(QPointF(5.5, 5.5), 4.4, 4.4)
            painter.drawLine(QPointF(5.5, 3.2), QPointF(5.5, 7.8))
            painter.drawLine(QPointF(3.9, 4.4), QPointF(7.1, 4.4))
            painter.drawLine(QPointF(3.9, 6.6), QPointF(7.1, 6.6))
            return
        flame = QPainterPath()
        flame.moveTo(5.5, 0.8)
        flame.cubicTo(7.5, 3.0, 9.6, 4.6, 9.6, 7.0)
        flame.cubicTo(9.6, 9.0, 7.8, 10.4, 5.5, 10.4)
        flame.cubicTo(3.2, 10.4, 1.4, 9.0, 1.4, 7.0)
        flame.cubicTo(1.4, 5.4, 2.6, 4.4, 3.4, 3.2)
        flame.cubicTo(4.0, 4.0, 4.4, 4.2, 4.8, 4.0)
        flame.cubicTo(4.6, 2.8, 4.8, 1.8, 5.5, 0.8)
        painter.drawPath(flame)


class Chip(QFrame):
    """Плашка с коротким текстом и необязательным значком: расход, цена перезаписи, «прожорливый»."""

    def __init__(self, icon: str | None = None, danger: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ChipDanger" if danger else "Chip")
        box = QHBoxLayout(self)
        box.setContentsMargins(5, 1, 5, 1)
        box.setSpacing(3)
        self.icon: ChipIcon | None = None
        if icon:
            self.icon = ChipIcon(icon, GLUTTON_COLOR if danger else COLORS["muted"])
            box.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setObjectName("ChipTextDanger" if danger else "ChipText")
        box.addWidget(self.label)

    def set_text(self, text: str) -> None:
        self.label.setText(text)


class BellIcon(QWidget):
    """Маленький колокольчик перед предупреждением об остывании."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(WARN_COLOR)
        pen = QPen(color, 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        bell = QPainterPath()
        bell.moveTo(2.5, 10.5)
        bell.cubicTo(3.5, 9.5, 4.0, 8.5, 4.0, 6.5)
        bell.cubicTo(4.0, 4.5, 5.2, 3.0, 7.0, 3.0)
        bell.cubicTo(8.8, 3.0, 10.0, 4.5, 10.0, 6.5)
        bell.cubicTo(10.0, 8.5, 10.5, 9.5, 11.5, 10.5)
        bell.closeSubpath()
        painter.drawPath(bell)
        painter.drawLine(QPointF(5.8, 12.2), QPointF(8.2, 12.2))


class CacheThreadRow(QFrame):
    """Строка треда: кольцо таймера, название, проект, расход и предупреждение.

    Что из этого показывать, задает `view`; скрытые части не создаются в раскладке.
    """

    def __init__(
        self,
        thread: CacheThread,
        now: dt.datetime,
        view: ThreadView | None = None,
        pinned: bool = False,
        parent: QWidget | None = None,
        spend: ThreadSpend | None = None,
        glutton: bool = False,
    ) -> None:
        super().__init__(parent)
        self.view = view or ThreadView()
        self.pinned = pinned
        self.setObjectName("CacheRow")
        # Компактно: вторая строка - проект и короткие чипы, среда и пояснения в подсказке.
        self.compact = self.view.compact
        self.setMinimumHeight(COMPACT_ROW_HEIGHT if self.compact else ROW_HEIGHT)
        box = QHBoxLayout(self)
        box.setContentsMargins(*((10, 6, 10, 6) if self.compact else (12, 10, 12, 10)))
        box.setSpacing(8 if self.compact else 10)
        ring = CacheRing(size=COMPACT_RING_SIZE, width=COMPACT_RING_WIDTH, font_pt=COMPACT_RING_FONT, bold=False) if self.compact else CacheRing()
        self.ring: CacheRing | None = ring if self.view.timer else None
        if self.ring is not None:
            # компактно кольцо по центру двух строк, в подробном виде - по верху
            box.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignVCenter if self.compact else Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(HEAD_SPACING)
        self.dot = StateDot()
        # компактно состояние показывает кольцо, отдельного столбца под точку нет: текст встает вплотную к кольцу
        self.dot_in_ring = self.compact and self.ring is not None
        if self.dot_in_ring:
            self.dot.setParent(self)
            self.dot.hide()
        else:
            head.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title = ElidedLabel()
        self.title.setObjectName("CacheTitle")
        if self.view.title:
            head.addWidget(self.title, 1)
        else:
            self.title.setParent(self)
            self.title.hide()
            head.addStretch(1)
        text.addLayout(head)
        self.project = ElidedLabel()
        self.project.setObjectName("CacheProject")
        if self.view.project and not self.compact:
            text.addWidget(self.project)
        elif not self.view.project:
            self.project.setParent(self)
            self.project.hide()
        self.share_chip = Chip()
        self.cost_chip = Chip()
        self.rewrite_chip = Chip("coin")
        # В компактном виде «прожорливый» - значок пламени в конце строки названия: в строке чипов он сбил бы столбцы.
        self.glutton_mini = Chip("flame", danger=True)
        self.glutton_mini.label.hide()
        self.glutton_mini.setVisible(False)
        if self.compact:
            head.addWidget(self.glutton_mini, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            self.glutton_mini.setParent(self)
        self.chips_row = QWidget()
        chips_box = QHBoxLayout(self.chips_row)
        # вторая строка начинается под названием: без кольца точка состояния висит в своем столбце слева
        indent = DOT_COLUMN + HEAD_SPACING if self.compact and not self.dot_in_ring else 0
        chips_box.setContentsMargins(indent, 2 if self.compact else 3, 0, 0)
        chips_box.setSpacing(3)
        if self.compact and self.view.project:
            chips_box.addWidget(self.project, 1)
            chips_box.addSpacing(3)
        for chip in (self.share_chip, self.cost_chip, self.rewrite_chip):
            chips_box.addWidget(chip)
        if not (self.compact and self.view.project):
            chips_box.addStretch(1)
        self.chips_row.setVisible(False)
        text.addWidget(self.chips_row)
        self.glutton_chip = Chip("flame", danger=True)
        self.glutton_chip.set_text("прожорливый")
        self.glutton_row = QWidget()
        glutton_box = QHBoxLayout(self.glutton_row)
        glutton_box.setContentsMargins(0, 3, 0, 0)
        glutton_box.setSpacing(0)
        glutton_box.addWidget(self.glutton_chip)
        glutton_box.addStretch(1)
        self.glutton_row.setVisible(False)
        text.addWidget(self.glutton_row)
        self.warn_icon = BellIcon()
        self.warn_label = QLabel()
        self.warn_label.setObjectName("CacheWarn")
        self.warn_row = QWidget()
        warn_box = QHBoxLayout(self.warn_row)
        warn_box.setContentsMargins(0, 3, 0, 0)
        warn_box.setSpacing(5)
        warn_box.addWidget(self.warn_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        warn_box.addWidget(self.warn_label, 1)
        self.warn_row.setVisible(False)
        text.addWidget(self.warn_row)
        text.addStretch(1)
        box.addLayout(text, 1)
        self.thread = None
        self._spend_state = None
        self.update_thread(thread, now, spend, glutton)

    @property
    def warning(self) -> bool:
        """Показано ли предупреждение об остывании."""
        return not self.warn_row.isHidden()

    def update_thread(self, thread: CacheThread, now: dt.datetime, spend: ThreadSpend | None = None, glutton: bool = False) -> bool:
        """Обновляет строку; True, если изменилась ее раскладка (появились или пропали чипы, значок, предупреждение)."""
        layout_changed = (spend, glutton) != self._spend_state
        if layout_changed:
            self._spend_state = spend, glutton
            self._apply_spend(spend, glutton)
        if thread != self.thread:
            self.thread = thread
            self.title.set_full_text(thread.title)
            if self.compact:
                self.project.set_full_text(thread.project)
            else:
                self.project.set_full_text(f"{thread.project} · {thread.environment}" if thread.environment else thread.project)
            tip = tooltip_text(thread)
            if "<" in tip or "&" in tip:
                # Подсказка Qt тоже понимает HTML: название из журнала показывается как текст.
                tip = "<p style='white-space:pre-wrap'>" + html.escape(tip).replace("\n", "<br>") + "</p>"
            for widget in (self, self.title, self.project, self.ring, self.dot, self.warn_row):
                if widget is not None:
                    widget.setToolTip(tip)
        return self.refresh(now) or layout_changed

    def _apply_spend(self, spend: ThreadSpend | None, glutton: bool) -> bool:
        """Чипы расхода, цены перезаписи и значок «прожорливый»; True, если набор видимых частей изменился."""
        usage = self.view.usage
        share = spend.share if spend is not None and usage in ("pct", "both") else None
        self.share_chip.setVisible(share is not None)
        if share is not None:
            suffix = "" if self.compact else " нед"
            self.share_chip.set_text(("<0,1%" if share < 0.05 else f"~{share:.1f}%".replace(".", ",")) + suffix)
            self.share_chip.setToolTip(
                "Оценка доли недельного лимита: недельный процент аккаунта поделен между тредами по стоимости их токенов.\n"
                "Расход вне этого компьютера и вне журналов сюда не попадает."
            )
        cost_text = ""
        if spend is not None and usage in ("usd", "both"):
            if spend.cost > 0:
                cost_text = format_money(spend.cost) + ("+" if spend.unpriced_tokens else "")
            elif spend.unpriced_tokens:
                cost_text = "нет цены"
        self.cost_chip.setVisible(bool(cost_text))
        if cost_text:
            self.cost_chip.set_text(cost_text)
            self.cost_chip.setToolTip(
                "Сколько стоили бы токены этого треда за неделю лимита по ценам API. Это условная цифра, а не трата по подписке."
                + ("\nУ части токенов нет опубликованной цены, они не учтены." if spend is not None and spend.unpriced_tokens else "")
            )
        rewrite = spend.rewrite if spend is not None and self.view.rewrite else None
        self.rewrite_chip.setVisible(rewrite is not None)
        if rewrite is not None:
            self.rewrite_chip.set_text(("" if self.compact else "перезапись ") + format_money(rewrite))
            self.rewrite_chip.setToolTip(
                ("Цена перезаписи кэша. " if self.compact else "")
                + "Во что обойдется возврат кэша в строй: если написать после остывания, контекст последнего ответа будет записан в кэш заново "
                "по цене записи (у Codex без платной записи - по цене входа)."
            )
        project_in_chips = self.compact and self.view.project
        show_chips = share is not None or bool(cost_text) or rewrite is not None or project_in_chips
        show_glutton = glutton and self.view.glutton
        glutton_tip = "Больше всех остальных тредов съел недельного лимита (по оценке)."
        if self.compact:
            changed = show_chips == self.chips_row.isHidden() or show_glutton == self.glutton_mini.isHidden()
            self.glutton_mini.setVisible(show_glutton)
            self.glutton_mini.setToolTip("Прожорливый: " + glutton_tip[0].lower() + glutton_tip[1:])
            self.glutton_row.setVisible(False)
        else:
            changed = show_chips == self.chips_row.isHidden() or show_glutton == self.glutton_row.isHidden()
            self.glutton_row.setVisible(show_glutton)
            if show_glutton:
                self.glutton_chip.setToolTip(glutton_tip)
        self.chips_row.setVisible(show_chips)
        return changed

    def refresh(self, now: dt.datetime) -> bool:
        remaining = self.thread.remaining(now)
        if self.ring is not None:
            self.ring.set_state(
                self.thread.fraction(now),
                format_remaining(remaining, self.thread.estimated),
                remaining <= 0,
            )
        self.dot.set_state(self.thread.activity)
        if self.dot_in_ring:
            self.ring.set_activity(self.thread.activity)
        show = self.view.warning and 0 < remaining <= WARN_SECONDS
        if show:
            self.warn_label.setText(f"Остынет через {format_remaining(remaining, self.thread.estimated)}. Нужно ли написать?")
        if show == self.warning:
            return False
        self.warn_row.setVisible(show)
        self.setProperty("warn", show)
        self.style().unpolish(self)
        self.style().polish(self)
        self.layout().invalidate()
        self.layout().activate()
        return True


def invalidate_parent_layouts(widget: QWidget) -> None:
    """Сразу пересчитывает размеры вложенного блока, карточки и окна."""
    widget.updateGeometry()
    parent = widget.parentWidget()
    while parent is not None:
        if parent.layout() is not None:
            parent.layout().invalidate()
        parent.updateGeometry()
        parent = parent.parentWidget()


class CacheThreadsBlock(QFrame):
    """Список тредов одного агента; строки обновляются по секундам без пересборки.

    Показывает первые `limit` тредов и ссылку «ещё N»; по ссылке (`more_clicked`)
    владелец окна переключает показ всех тредов, ссылка «свернуть» (`less_clicked`)
    возвращает короткий список. Без тредов показывается `message`. Строки лежат в
    прокручиваемой области: окно ограничивает ее высоту (`set_height_cap`), когда
    тредов больше, чем помещается на экране.
    """

    more_clicked = Signal()
    less_clicked = Signal()
    restore_clicked = Signal()
    content_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CacheBlock")
        self.setMinimumWidth(CACHE_BLOCK_MIN_WIDTH)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 4)
        self._outer.setSpacing(2)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("CacheScroll")
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.viewport().setAutoFillBackground(False)
        self._body = QWidget()
        self._body.setObjectName("CacheBody")
        self._box = QVBoxLayout(self._body)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(0)
        self._scroll.setWidget(self._body)
        self._outer.addWidget(self._scroll)
        self.rows: list[CacheThreadRow] = []
        self.link: LinkButton | None = None
        self.restore: LinkButton | None = None
        self.note: QLabel | None = None
        self._state: tuple | None = None
        self._cap: int | None = None
        self._show_all = False
        self._pending_rows = deque()
        self._row_timer = QTimer(self)
        self._row_timer.setSingleShot(True)
        self._row_timer.timeout.connect(self._append_pending_rows)

    @property
    def thread_keys(self) -> list[str]:
        return [row.thread.session_id for row in self.rows]

    def natural_height(self) -> int:
        """Высота списка без ограничения."""
        height = self._box.sizeHint().height()
        if self._pending_rows:
            row_height = COMPACT_ROW_HEIGHT if self._pending_rows[0][1].compact else ROW_HEIGHT
            height += len(self._pending_rows) * row_height
        return height

    def list_height(self) -> int:
        """Высота списка сейчас (с учетом ограничения)."""
        return self._scroll.maximumHeight()

    def set_height_cap(self, cap: int | None) -> None:
        """Ограничивает высоту списка в пикселях; None снимает ограничение. Ниже трех строк не сжимается."""
        self._cap = cap
        natural = self.natural_height()
        height = natural if cap is None else max(min(natural, cap), min(natural, MIN_VISIBLE_ROWS * ROW_HEIGHT))
        if self._scroll.minimumHeight() == height and self._scroll.maximumHeight() == height:
            return
        self._scroll.setFixedHeight(height)
        # Размеры вложенных раскладок обновляются через очередь событий; окну нужна актуальная высота сразу.
        self._outer.invalidate()
        self._outer.activate()
        invalidate_parent_layouts(self)

    def set_content(
        self,
        threads: list[CacheThread],
        now: dt.datetime,
        *,
        show_all: bool = False,
        limit: int = COLLAPSED_THREADS,
        message: str = "",
        view: ThreadView | None = None,
        pinned: frozenset[str] | set[str] = frozenset(),
        hidden_count: int = 0,
        spend: dict[str, ThreadSpend] | None = None,
    ) -> bool:
        """Обновляет список. Возвращает True, если состав строк изменился (нужна подгонка высоты).

        Параметр view управляет составом строки. В pinned передаются закрепленные треды.
        При ненулевом hidden_count под списком доступна кнопка возврата скрытых тредов.
        """
        view = view or ThreadView()
        spend = spend or {}
        greedy = glutton_id(threads, spend) if view.glutton else None
        visible = threads if show_all else threads[:limit]
        hidden = len(threads) - len(visible)
        can_collapse = show_all and len(threads) > limit
        note = "" if threads else (message or EMPTY_TEXT)
        shown_pinned = frozenset(t.session_id for t in visible if t.session_id in pinned)
        restorable = hidden_count if view.pin_hide else 0
        state = (tuple(t.session_id for t in visible), hidden, can_collapse, note, view, shown_pinned, restorable)
        link_focused = self.link is not None and self.link.hasFocus()
        restore_focused = self.restore is not None and self.restore.hasFocus()
        changed = state != self._state
        if changed:
            # При раскрытии сохраняем уже видимые строки и их раскладку.
            kept = 0
            for row, thread in zip(self.rows, visible):
                if (row.thread.session_id != thread.session_id or row.view != view
                        or row.pinned != (thread.session_id in shown_pinned)):
                    break
                kept += 1
            self._remove_rows_after(kept)
        row_changed = False
        for row, thread in zip(self.rows, visible):
            row_changed |= row.update_thread(thread, now, spend.get(thread.session_id), thread.session_id == greedy)
        self._pending_rows = deque(
            (thread, view, thread.session_id in shown_pinned, spend.get(thread.session_id), thread.session_id == greedy)
            for thread in visible[len(self.rows):]
        )
        if not changed and not row_changed:
            return False
        if note and self.note is None:
            self.note = QLabel(note)
            self.note.setObjectName("CacheMore")
            self.note.setWordWrap(True)
            self._box.addWidget(self.note)
        elif note:
            self.note.setText(note)
        elif self.note is not None:
            self._remove_widget(self._box, self.note)
            self.note = None
        self._show_all = show_all
        link_text = f"Показать еще {hidden}" if hidden else "Свернуть список" if can_collapse else ""
        self.link = self._update_link(self.link, link_text, self._link_clicked)
        restore_text = f"Вернуть скрытые ({restorable})" if restorable else ""
        self.restore = self._update_link(self.restore, restore_text, self.restore_clicked)
        if (link_focused or restore_focused) and changed:
            action = self.restore if restore_focused and self.restore is not None else self.link
            if action is not None and not action.hasFocus():
                action.setFocus(Qt.FocusReason.OtherFocusReason)
        self._state = state
        if changed:
            if self._pending_rows:
                count = ROWS_PER_BATCH if len(visible) > BATCHED_LIST_MIN else len(self._pending_rows)
                self._append_rows(count, now)
            else:
                self._set_last_row(self.rows[-1] if self.rows else None, True)
        self._align_chips()
        self._fit_width()
        self.set_height_cap(self._cap)
        return True

    def _link_clicked(self) -> None:
        (self.less_clicked if self._show_all else self.more_clicked).emit()

    def _update_link(self, link, text, clicked):
        if not text:
            if link is not None:
                self._remove_widget(self._outer, link)
            return None
        if link is None:
            link = LinkButton(text)
            link.clicked.connect(clicked)
            self._outer.insertWidget(1 if clicked == self._link_clicked else self._outer.count(),
                                     link, 0, Qt.AlignmentFlag.AlignLeft)
        elif link.text() != text:
            link.setText(text)
            link.setAccessibleName(text)
        return link

    def _append_pending_rows(self) -> None:
        if not self._pending_rows:
            return
        self._append_rows(ROWS_PER_BATCH, dt.datetime.now(dt.timezone.utc))
        self._align_chips()
        self._fit_width()
        self.set_height_cap(self._cap)
        self.content_changed.emit()

    def _append_rows(self, count: int, now: dt.datetime) -> None:
        if self.rows:
            self._set_last_row(self.rows[-1], False)
        for _ in range(min(count, len(self._pending_rows))):
            thread, view, pinned, spend, glutton = self._pending_rows.popleft()
            row = CacheThreadRow(thread, now, view, pinned, self._body, spend, glutton)
            self.rows.append(row)
            self._box.addWidget(row)
        self._set_last_row(self.rows[-1], True)
        if self._pending_rows:
            self._row_timer.start(ROWS_BATCH_DELAY_MS)
        else:
            self._row_timer.stop()

    @staticmethod
    def _set_last_row(row: CacheThreadRow | None, last: bool) -> None:
        if row is not None and row.property("last") != last:
            row.setProperty("last", last)
            row.style().unpolish(row)
            row.style().polish(row)

    def _align_chips(self) -> None:
        """Компактно: чипы одного вида одной ширины во всех строках, столбцы стоят ровно друг под другом.

        Если чип какого-то вида есть хотя бы в одной строке, в остальных строках его место остается пустым.
        """
        rows = [row for row in self.rows if row.compact]
        for name in ("share_chip", "cost_chip", "rewrite_chip"):
            chips = [getattr(row, name) for row in rows]
            shown = [chip for chip in chips if not chip.isHidden()]
            width = max((chip.sizeHint().width() for chip in shown), default=0)
            for chip in chips:
                policy = chip.sizePolicy()
                if policy.retainSizeWhenHidden() != bool(shown):
                    policy.setRetainSizeWhenHidden(bool(shown))
                    chip.setSizePolicy(policy)
                if shown and (chip.minimumWidth() != width or chip.maximumWidth() != width):
                    chip.setFixedWidth(width)
                elif not shown and chip.maximumWidth() != QWIDGETSIZE_MAX:
                    chip.setMinimumWidth(0)
                    chip.setMaximumWidth(QWIDGETSIZE_MAX)

    def _fit_width(self) -> bool:
        """Ширина блока не меньше самой широкой строки: прокрутка по горизонтали выключена, и без этого чипы и значки обрезались бы."""
        self._box.activate()
        width = max([CACHE_BLOCK_MIN_WIDTH] + [row.minimumSizeHint().width() for row in self.rows]) + SCROLLBAR_WIDTH
        if width == self.minimumWidth():
            return False
        self.setMinimumWidth(width)
        invalidate_parent_layouts(self)
        return True

    def tick(self, now: dt.datetime) -> None:
        if any([row.refresh(now) for row in self.rows]):
            # Размеры вложенных раскладок обновляются через очередь событий; окну нужна актуальная высота сразу.
            self._box.invalidate()
            self._box.activate()
            self._outer.invalidate()
            invalidate_parent_layouts(self)
            self.content_changed.emit()

    def _remove_rows_after(self, count: int) -> None:
        self._row_timer.stop()
        for row in reversed(self.rows[count:]):
            self._remove_widget(self._box, row)
        del self.rows[count:]

    @staticmethod
    def _remove_widget(layout, widget) -> None:
        layout.removeWidget(widget)
        widget.hide()
        widget.deleteLater()
