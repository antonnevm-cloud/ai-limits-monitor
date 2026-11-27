"""Прогноз недели в виджете: полоса прогноза в карточке агента и всплывающий график расхода по проектам."""
from __future__ import annotations

import html

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QStackedLayout, QToolButton, QVBoxLayout, QWidget,
)

from .qt_theme import COLORS
from .week_forecast import HALF_LIFE_HOURS, OTHER_LABEL, RATE_WEIGHTED, WEEKDAYS, ChartSeries, WeekChart, WeekForecast, clock_text

ACCENT = "#4C9AF0"
DANGER = "#D0463C"
TRACK = "#26323E"
SERIES_COLORS = ("#7F77DD", "#1D9E75", "#D85A30", "#378ADD", "#C9A227", "#B45FA0", "#5FA0B4", "#9C8F6E")
OTHER_COLOR = "#6B7785"
SHORT_COLOR = "#E5675E"  # лимита не хватит до сброса
POPUP_WIDTH = 450
CHART_TITLE = "Расход за неделю по проектам"
DAY_PHRASES = ("в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье")
FOOTNOTE = "Доли по проектам оценочные: токены проектов распределяют прирост недельного процента."

# Поле графика в условных единицах макета (430 x 196).
VIEW_W, VIEW_H = 430.0, 196.0
X0, X1, Y0, Y1 = 34.0, 414.0, 170.0, 14.0


def number(value: float, digits: int = 0) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def series_color(index: int, name: str) -> str:
    if name == OTHER_LABEL:
        return OTHER_COLOR
    if index < len(SERIES_COLORS):
        return SERIES_COLORS[index]
    # После восьми проектов цвета не повторяются: оттенок идет по золотому углу.
    return QColor.fromHsv(int((index * 137.508) % 360), 150, 200).name()


LEGEND_NAME_LIMIT = 28


# --------------------------------------------------------------------------- #
# Тексты (без Qt, проверяются отдельно)
# --------------------------------------------------------------------------- #
def status_text(forecast: WeekForecast) -> str:
    """Сколько часов не хватит или сколько примерно останется к сбросу."""
    if forecast.rate_per_day is None:
        return "мало данных для прогноза"
    hours = forecast.short_hours
    if hours is not None:
        return f"не хватит на {hours} ч"
    return f"к сбросу останется ~{number(round(forecast.remaining_at_reset or 0.0))}%"


def rate_text(forecast: WeekForecast) -> str:
    """Темп прогноза и его расчет для примечания графика."""
    if forecast.rate_per_day is None:
        return "Темпа пока нет: с начала недели прошло мало времени или расхода не было."
    if forecast.method == RATE_WEIGHTED:
        return (
            f"Темп ~{number(forecast.rate_per_day, 1)}% в сутки: прирост по часам с начала недели "
            f"({clock_text(forecast.week_start)}), свежие часы весят больше, вес часа вдвое меньше каждые "
            f"{number(HALF_LIFE_HOURS)} ч. Прогноз - прямая при этом темпе до сброса."
        )
    return (
        f"Средний темп {number(forecast.rate_per_day, 1)}% в сутки с начала недели ({clock_text(forecast.week_start)}). "
        "Прогноз - прямая при этом темпе до сброса."
    )


def rate_value(forecast: WeekForecast) -> str:
    """Плитка «Темп» графика: «~5,2% в сутки» у взвешенного темпа (оценка), «5,2% в сутки» у среднего."""
    if forecast.rate_per_day is None:
        return "мало данных"
    mark = "~" if forecast.method == RATE_WEIGHTED else ""
    return f"{mark}{number(forecast.rate_per_day, 1)}% в сутки"


def exhausted_text(forecast: WeekForecast) -> str:
    """"кончится вс 02:00" по прогнозу; если лимит уже исчерпан - так и сказано, без текущего времени."""
    if forecast.used >= 100.0:
        return "лимит исчерпан"
    return f"кончится {clock_text(forecast.exhausted_at)}"


def axis_texts(forecast: WeekForecast) -> tuple[str, str]:
    """Две подписи под полосой: сейчас слева, сброс справа (при нехватке перед ним время, когда лимит кончится)."""
    now = f"сейчас · осталось {number(forecast.remaining)}%"
    reset = f"сброс {clock_text(forecast.resets_at)}"
    if forecast.exhausted_at is not None and forecast.short_hours is not None:
        return now, f"{exhausted_text(forecast)} · {reset}"
    return now, reset


def reset_remaining_text(forecast: WeekForecast) -> str | None:
    """Сколько примерно останется к сбросу («~46%»); None, если лимита не хватит или данных мало."""
    if forecast.rate_per_day is None or forecast.short_hours is not None:
        return None
    return f"~{number(round(forecast.remaining_at_reset or 0.0))}%"


def reset_html(forecast: WeekForecast) -> str:
    """Правая подпись под полосой: «сброс пт 20:00» и цветом остаток к сбросу или время, когда лимит кончится."""
    reset = f"сброс {clock_text(forecast.resets_at)}"
    if forecast.exhausted_at is not None and forecast.short_hours is not None:
        return f'<span style="color:{SHORT_COLOR}">{exhausted_text(forecast)}</span> · {reset}'
    left = reset_remaining_text(forecast)
    return reset if left is None else f'{reset} <span style="color:{COLORS["success"]}">{left}</span>'


def chart_info(chart: WeekChart, highlighted: int = -1) -> str:
    """Строка под легендой: общий вывод или подробности по выбранному проекту."""
    series = chart.series
    if not series:
        return "Расход этой недели пока не разложился по проектам."
    days = len(series[0].cumulative) - 1
    if 0 <= highlighted < len(series):
        item = series[highlighted]
        peak = max(range(days), key=lambda day: item.growth(day))
        total = chart.forecast.used or 1.0
        parts = ", ".join(f"{chart.day_names[day]} +{number(item.growth(day))}%" for day in range(days))
        return (
            f"{item.name}: {number(item.total)}% недели ({number(item.total / total * 100)}% всего расхода). "
            f"Пик: {chart.day_names[peak]}, +{number(item.growth(peak))}%. По дням: {parts}."
        )
    totals = [sum(item.growth(day) for item in series) for day in range(days)]
    sharp = max(range(days), key=lambda day: totals[day])
    leader = max(series, key=lambda item: item.growth(sharp))
    biggest = max(series, key=lambda item: item.total)
    phrase = DAY_PHRASES[WEEKDAYS.index(chart.day_names[sharp])]
    return (
        f"Резче всего неделя росла {phrase}: +{number(totals[sharp])}% за сутки, из них {leader.name} +{number(leader.growth(sharp))}%. "
        f"Больше всего за неделю: {biggest.name}, {number(biggest.total)}%."
    )


# --------------------------------------------------------------------------- #
# Полоса прогноза
# --------------------------------------------------------------------------- #
class SegmentBar(QWidget):
    """Полоса недели: прошло (сплошная), хватит (бледная), лимит кончился (красная)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(8)
        self.now_fraction = 0.0
        self.exhaust_fraction: float | None = None

    def set_values(self, now_fraction: float, exhaust_fraction: float | None) -> None:
        self.now_fraction = now_fraction
        self.exhaust_fraction = exhaust_fraction
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = float(self.width()), float(self.height())
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, width, height), height / 2, height / 2)
        painter.setClipPath(clip)
        painter.fillRect(QRectF(0, 0, width, height), QColor(TRACK))
        now_x = width * self.now_fraction
        end = self.exhaust_fraction if self.exhaust_fraction is not None else 1.0
        end_x = width * max(self.now_fraction, min(1.0, end))
        painter.fillRect(QRectF(0, 0, now_x, height), QColor(ACCENT))
        faded = QColor(ACCENT)
        faded.setAlphaF(0.35)
        painter.fillRect(QRectF(now_x, 0, end_x - now_x, height), faded)
        if self.exhaust_fraction is not None and end_x < width:
            painter.fillRect(QRectF(end_x, 0, width - end_x, height), QColor(DANGER))


class ForecastBar(QFrame):
    """Полоса и подписи прогноза образуют одну кнопку открытия расхода по проектам."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Forecast")
        self.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation)
        stack = QStackedLayout(self)
        stack.setContentsMargins(2, 2, 2, 2)
        stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self.chart_button = QPushButton()
        self.chart_button.setObjectName("ForecastAction")
        self.chart_button.setCheckable(True)
        self.chart_button.setAutoDefault(True)
        self.chart_button.setAccessibleName(CHART_TITLE)
        self.chart_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.chart_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chart_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.chart_button.setStyleSheet(f"""
            QPushButton#ForecastAction {{
                min-height:0; padding:0; background:{COLORS['control']};
                border:1px solid {COLORS['border_soft']}; border-radius:6px;
            }}
            QPushButton#ForecastAction:hover {{background:{COLORS['panel_alt']}; border-color:{COLORS['accent']};}}
            QPushButton#ForecastAction:checked, QPushButton#ForecastAction:pressed {{background:{COLORS['selection']};}}
            QPushButton#ForecastAction:focus {{border-color:{ACCENT};}}
            QPushButton#ForecastAction:disabled {{background:transparent; border-color:transparent;}}
        """)
        self.chart_button.clicked.connect(self.clicked)
        stack.addWidget(self.chart_button)
        self.setFocusProxy(self.chart_button)

        # Подписи поверх кнопки пропускают нажатие и сохраняют свои цвета при отключении графика.
        content = QWidget()
        content.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        stack.addWidget(content)
        stack.setCurrentWidget(content)
        box = QVBoxLayout(content)
        box.setContentsMargins(10, 5, 10, 5)
        box.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.bar = SegmentBar()
        top.addWidget(self.bar, 1)
        self.action_label = QLabel("Расход")
        self.action_label.setObjectName("ForecastAxis")
        self.action_label.setStyleSheet(f"color:{COLORS['project_text']}; font-weight:600;")
        top.addWidget(self.action_label)
        box.addLayout(top)
        bottom = QFormLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setHorizontalSpacing(0)
        bottom.setVerticalSpacing(1)
        bottom.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        bottom.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.now_label = QLabel()
        # Отступ в размере подписи участвует в расчете переноса строки.
        self.now_label.setContentsMargins(0, 0, 8, 0)
        self.end_label = QLabel()
        self.end_label.setTextFormat(Qt.TextFormat.RichText)
        self.end_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for label in (self.now_label, self.end_label):
            label.setObjectName("ForecastAxis")
        bottom.addRow(self.now_label, self.end_label)
        box.addLayout(bottom)
        self.forecast: WeekForecast | None = None
        self.chart_button.toggled.connect(self._update_action_hint)
        self._update_action_hint()

    def set_forecast(self, forecast: WeekForecast) -> None:
        self.forecast = forecast
        now, _reset = axis_texts(forecast)
        self.now_label.setText(now)
        self.end_label.setText(reset_html(forecast))
        self.bar.set_values(forecast.now_fraction, forecast.exhaust_fraction if forecast.short_hours is not None else None)

    def set_chart_enabled(self, enabled: bool) -> None:
        if self.chart_button.isEnabled() == enabled:
            return
        self.chart_button.setEnabled(enabled)
        self.chart_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus if enabled else Qt.FocusPolicy.NoFocus)
        self.chart_button.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.action_label.setVisible(enabled)
        if not enabled:
            self.set_chart_open(False)
        self._update_action_hint()

    def set_chart_open(self, opened: bool) -> None:
        self.chart_button.setChecked(opened and self.chart_button.isEnabled())

    def _update_action_hint(self) -> None:
        hint = ""
        if self.chart_button.isEnabled():
            hint = "Нажми, чтобы закрыть график." if self.chart_button.isChecked() else "Нажми, чтобы открыть расход за неделю по проектам."
        self.setToolTip(hint)
        self.chart_button.setToolTip(hint)


# --------------------------------------------------------------------------- #
# График
# --------------------------------------------------------------------------- #
class WeekChartView(QWidget):
    """Стопка по проектам: накопленный процент недели, красная зона, пунктир прогноза."""

    highlight_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(int(POPUP_WIDTH * VIEW_H / VIEW_W) - 20)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self.chart: WeekChart | None = None
        self.highlighted = -1

    def sizeHint(self) -> QSize:
        return QSize(POPUP_WIDTH - 28, int((POPUP_WIDTH - 28) * VIEW_H / VIEW_W))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return int(width * VIEW_H / VIEW_W)

    def set_chart(self, chart: WeekChart | None) -> None:
        self.chart = chart
        self.update()

    def set_highlighted(self, index: int) -> None:
        if index != self.highlighted:
            self.highlighted = index
            self.update()

    # координаты ---------------------------------------------------------- #
    @staticmethod
    def _x(days: float) -> float:
        return X0 + (X1 - X0) * days / 7.0

    @staticmethod
    def _y(percent: float) -> float:
        return Y0 - (Y0 - Y1) * percent / 100.0

    def _scale(self) -> float:
        return self.width() / VIEW_W

    def _lower(self, chart: WeekChart, index: int, point: int) -> float:
        return sum(chart.series[j].cumulative[point] for j in range(index))

    def series_at(self, position: QPointF) -> int:
        """Проект под курсором или -1."""
        chart = self.chart
        if chart is None or not chart.series:
            return -1
        scale = self._scale()
        x, y = position.x() / scale, position.y() / scale
        days = (x - X0) / (X1 - X0) * 7.0
        points = [fraction * 7.0 for fraction in chart.boundaries]
        if days < points[0] or days > points[-1]:
            return -1
        for point in range(len(points) - 1):
            if points[point] <= days <= points[point + 1]:
                span = points[point + 1] - points[point] or 1.0
                t = (days - points[point]) / span
                break
        else:
            return -1
        percent = (Y0 - y) / (Y0 - Y1) * 100.0
        for index in range(len(chart.series)):
            low = self._lower(chart, index, point) * (1 - t) + self._lower(chart, index, point + 1) * t
            top = low + chart.series[index].cumulative[point] * (1 - t) + chart.series[index].cumulative[point + 1] * t
            if low <= percent <= top:
                return index
        return -1

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        index = self.series_at(event.position())
        if index != self.highlighted:
            self.highlight_changed.emit(index)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        if self.highlighted != -1:
            self.highlight_changed.emit(-1)
        super().leaveEvent(event)

    # рисование ----------------------------------------------------------- #
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self._scale(), self._scale())
        chart = self.chart
        muted, secondary, border = QColor(COLORS["faint"]), QColor(COLORS["muted"]), QColor(COLORS["border"])
        font = QFont(self.font())
        font.setPixelSize(11)
        painter.setFont(font)
        for percent in (0, 25, 50, 75, 100):
            painter.setPen(muted)
            painter.drawText(QPointF(0, self._y(percent) + 4), f"{percent}%")
            painter.setPen(QPen(border, 0.6))
            painter.drawLine(QPointF(X0, self._y(percent)), QPointF(X1, self._y(percent)))
        if chart is None:
            return
        forecast = chart.forecast
        now_days = forecast.now_fraction * 7.0
        exhaust = forecast.exhaust_fraction if forecast.short_hours is not None else None
        if exhaust is not None:
            zone = QColor(DANGER)
            zone.setAlphaF(0.07)
            painter.fillRect(QRectF(self._x(exhaust * 7.0), Y1, X1 - self._x(exhaust * 7.0), Y0 - Y1), zone)
        pen = QPen(muted, 0.8)
        pen.setDashPattern([2.5, 5.0])
        painter.setPen(pen)
        painter.drawLine(QPointF(X0, Y0), QPointF(X1, Y1))
        points = [fraction * 7.0 for fraction in chart.boundaries]
        for index, item in enumerate(chart.series):
            upper = QPolygonF()
            lower = []
            for point, days in enumerate(points):
                low = self._lower(chart, index, point)
                upper.append(QPointF(self._x(days), self._y(low + item.cumulative[point])))
                lower.append(QPointF(self._x(days), self._y(low)))
            for position in reversed(lower):
                upper.append(position)
            color = QColor(series_color(index, item.name))
            color.setAlphaF(0.85 if self.highlighted < 0 or self.highlighted == index else 0.25)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawPolygon(upper)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(secondary, 1.0)
        pen.setDashPattern([3.0, 3.0])
        painter.setPen(pen)
        painter.drawLine(QPointF(self._x(now_days), Y1), QPointF(self._x(now_days), Y0))
        pen = QPen(QColor(ACCENT), 2.0)
        pen.setDashPattern([2.0, 2.0])
        painter.setPen(pen)
        if exhaust is not None:
            painter.drawLine(QPointF(self._x(now_days), self._y(forecast.used)), QPointF(self._x(exhaust * 7.0), self._y(100)))
            painter.setPen(QPen(QColor(DANGER), 3.5))
            painter.drawLine(QPointF(self._x(exhaust * 7.0), self._y(100)), QPointF(X1, self._y(100)))
        elif forecast.rate_per_day is not None:
            left_days = max(0.0, (forecast.resets_at - forecast.now).total_seconds() / 86400)
            painter.drawLine(QPointF(self._x(now_days), self._y(forecast.used)), QPointF(X1, self._y(min(100.0, forecast.used + forecast.rate_per_day * left_days))))
        painter.setPen(muted)
        for day, name in enumerate(chart.day_names):
            width = painter.fontMetrics().horizontalAdvance(name.capitalize())
            painter.drawText(QPointF(self._x(day + 0.5) - width / 2, 188), name.capitalize())
        painter.setPen(secondary)
        metrics = painter.fontMetrics()
        text = f"сейчас: {number(forecast.used)}%"
        width = metrics.horizontalAdvance(text)
        # Подпись не уходит за левый край поля: в начале недели она стоит справа от линии "сейчас".
        now_x = self._x(now_days) - 4 - width
        if now_x < X0:
            now_x = min(self._x(now_days) + 4, X1 - width)
        painter.drawText(QPointF(now_x, 26 if exhaust is None else 14), text)
        reset_text = f"сброс {clock_text(forecast.resets_at)}"
        reset_x = X1 - 2 - metrics.horizontalAdvance(reset_text)
        reset_y = self._y(100) + 14
        painter.setPen(QColor(DANGER))
        if exhaust is not None:
            text = exhausted_text(forecast)
            width = metrics.horizontalAdvance(text)
            left = max(X0, min(self._x(exhaust * 7.0) - width / 2, X1 - width))
            if left + width + 6 > reset_x:  # подписи сошлись: сброс уходит строкой ниже
                reset_y += 14
            painter.drawText(QPointF(left, 30), text)
        painter.setPen(QColor(DANGER) if exhaust is not None else secondary)
        painter.drawText(QPointF(reset_x, reset_y), reset_text)
        painter.setPen(muted)
        painter.drawText(QPointF(X0 + 6, self._y(100) + 14), "равномерный темп")


class LegendChip(QFrame):
    """Чип легенды: цвет проекта, имя, процент; наведение подсвечивает проект на графике."""

    hovered = Signal(int)

    def __init__(self, index: int, item: ChartSeries, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.index = index
        self.color = series_color(index, item.name)
        self.setObjectName("LegendChip")
        self.setMouseTracking(True)
        box = QHBoxLayout(self)
        box.setContentsMargins(9, 4, 9, 4)
        box.setSpacing(6)
        swatch = QFrame()
        swatch.setFixedSize(10, 10)
        swatch.setStyleSheet(f"background:{self.color};border-radius:3px;border:none;")
        box.addWidget(swatch)
        # Имя папки показывается как текст; длинное сокращается, чтобы процент оставался виден.
        name = item.name if len(item.name) <= LEGEND_NAME_LIMIT else item.name[:LEGEND_NAME_LIMIT - 1] + "…"
        self.text = QLabel(f"{html.escape(name)} <b>{number(item.total)}%</b>")
        self.text.setTextFormat(Qt.TextFormat.RichText)
        if name != item.name:
            self.setToolTip(html.escape(item.name))
        self.text.setObjectName("LegendText")
        box.addWidget(self.text)
        self.set_active(False)  # рамка нужна до расчета размера, иначе содержимое потом срезается

    def set_active(self, active: bool) -> None:
        self.setStyleSheet(f"QFrame#LegendChip{{border:1px solid {self.color if active else COLORS['border_soft']};border-radius:12px;}}")

    def enterEvent(self, event) -> None:
        self.hovered.emit(self.index)
        super().enterEvent(event)


class ForecastPopup(QDialog):
    """Окно недельного расхода с закрытием кнопкой или клавишей Escape."""

    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle(CHART_TITLE)
        self.rejected.connect(self.closed)
        # Окно без фона, скругленная рамка с фоном внутри: у прозрачного окна верхнего уровня фон из стиля не рисуется.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(POPUP_WIDTH)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.body = QFrame()
        self.body.setObjectName("ForecastPopup")
        outer.addWidget(self.body)
        self.chart: WeekChart | None = None
        self.forecast: WeekForecast | None = None
        self._chips: list[LegendChip] = []
        self.highlighted = -1
        box = QVBoxLayout(self.body)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(10)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.title = QLabel(CHART_TITLE)
        self.title.setObjectName("PopupHeading")
        self.close_button = QToolButton()
        self.close_button.setObjectName("PopupClose")
        self.close_button.setText("×")
        self.close_button.setAccessibleName("Закрыть график")
        self.close_button.setMinimumSize(28, 28)
        self.close_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.close_button.setStyleSheet(f"QToolButton#PopupClose:focus {{border:1px solid {ACCENT}; border-radius:4px;}}")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setToolTip("Закрыть график (Esc)")
        self.close_button.clicked.connect(self.reject)
        head.addWidget(self.title, 1)
        head.addWidget(self.close_button)
        box.addLayout(head)
        summary = QFrame()
        summary.setObjectName("PopupTile")
        metrics = QFormLayout(summary)
        metrics.setContentsMargins(10, 6, 10, 6)
        metrics.setVerticalSpacing(2)
        metrics.setHorizontalSpacing(12)
        metrics.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        metrics.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        metrics.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.tiles: list[tuple[QLabel, QLabel]] = []
        for _ in range(3):
            caption, value = QLabel(), QLabel()
            caption.setObjectName("TileCaption")
            value.setObjectName("TileValue")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            metrics.addRow(caption, value)
            self.tiles.append((caption, value))
        box.addWidget(summary)
        self.view = WeekChartView()
        self.view.highlight_changed.connect(self.set_highlighted)
        box.addWidget(self.view)
        self.legend = QWidget()
        self.legend.setObjectName("PopupLegend")
        box.addWidget(self.legend)
        self.info = QLabel()
        self.info.setObjectName("PopupInfo")
        self.info.setWordWrap(True)
        self.info.setMinimumHeight(34)
        box.addWidget(self.info)
        self.note = QLabel(FOOTNOTE)
        self.note.setObjectName("PopupNote")
        self.note.setWordWrap(True)
        box.addWidget(self.note)

    def set_data(self, forecast: WeekForecast, chart: WeekChart | None) -> None:
        # Фоновое обновление сохраняет выбранный проект и не пересобирает легенду без изменений.
        previous = self.chart
        selected = (previous.series[self.highlighted].name
                    if previous is not None and 0 <= self.highlighted < len(previous.series) else None)
        same_legend = (previous is not None and chart is not None
                       and [(s.name, round(s.total)) for s in previous.series]
                       == [(s.name, round(s.total)) for s in chart.series])
        self.forecast = forecast
        self.chart = chart
        short = forecast.short_hours is not None
        self.note.setText(f"{FOOTNOTE} {rate_text(forecast)}")
        values = (
            ("Израсходовано", f"{number(forecast.used)}%", False),
            ("Темп", rate_value(forecast), False),
            ("Можно до сброса", f"{number(forecast.allowed_per_day)}% в сутки" if forecast.allowed_per_day is not None else "-", short),
        )
        for (caption, value), (name, text, red) in zip(self.tiles, values):
            caption.setText(name)
            value.setText(text)
            value.setProperty("short", red)
            value.style().unpolish(value)
            value.style().polish(value)
        self.view.set_chart(chart)
        self.view.setVisible(chart is not None)
        if not same_legend:
            self._rebuild_legend()
        names = [item.name for item in chart.series] if chart is not None else []
        self.set_highlighted(names.index(selected) if selected in names else -1)

    def _rebuild_legend(self) -> None:
        for chip in self._chips:
            chip.setParent(None)
            chip.deleteLater()
        self._chips = []
        chart = self.chart
        items = chart.series if chart is not None else ()
        x = y = 0
        gap, row_height = 6, 0
        width = POPUP_WIDTH - 28
        for index, item in enumerate(items):
            chip = LegendChip(index, item, self.legend)
            chip.hovered.connect(self.set_highlighted)
            chip.ensurePolished()
            chip.text.ensurePolished()
            chip.adjustSize()
            size = chip.sizeHint()
            if x and x + size.width() > width:
                x, y = 0, y + row_height + gap
            chip.setGeometry(x, y, size.width(), size.height())
            chip.show()
            x += size.width() + gap
            row_height = size.height()
            self._chips.append(chip)
        self.legend.setFixedHeight(y + row_height if items else 0)
        self.legend.setVisible(bool(items))

    def set_highlighted(self, index: int) -> None:
        self.highlighted = index
        self.view.set_highlighted(index)
        for chip in self._chips:
            chip.set_active(chip.index == index)
        if self.chart is not None:
            self.info.setText(chart_info(self.chart, index))
        else:
            self.info.setText("Для графика пока мало замеров недельного лимита.")
        # Высота переносимых строк зависит от фактической фиксированной ширины окна.
        self.ensurePolished()
        box = self.layout()
        box.activate()
        self.setFixedHeight(max(box.totalMinimumSize().height(), box.totalHeightForWidth(self.width())))
