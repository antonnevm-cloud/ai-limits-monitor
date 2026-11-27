import datetime as dt
import os
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from claude_usage_manager.forecast_qt import ForecastBar, ForecastPopup
from claude_usage_manager.models import QuotaWindow
from claude_usage_manager.quota_monitor import scaled_style
from claude_usage_manager.week_forecast import RATE_WEIGHTED, build_forecast


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def forecast(used=70):
    now = dt.datetime(2026, 11, 23, 12, tzinfo=dt.timezone.utc)
    week = QuotaWindow("secondary", "week", used, resets_at=now + dt.timedelta(days=3), window_minutes=10080)
    return build_forecast(week, now=now)


class Section(QWidget):
    def __init__(self):
        super().__init__()
        self.mouse_presses = 0
        self.mouse_releases = 0

    def mousePressEvent(self, event):
        self.mouse_presses += 1
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.mouse_releases += 1
        super().mouseReleaseEvent(event)


@pytest.fixture
def section(app):
    window = Section()
    layout = QVBoxLayout(window)
    before = QPushButton("До прогноза")
    bar = ForecastBar()
    bar.set_forecast(forecast())
    after = QPushButton("После прогноза")
    for widget in (before, bar, after):
        layout.addWidget(widget)
    window.show()
    window.activateWindow()
    app.processEvents()
    yield window, before, bar, after
    window.close()


def test_entire_forecast_is_one_click_target(section):
    window, _, bar, _ = section
    button = bar.chart_button
    clicked = QSignalSpy(bar.clicked)
    QTest.mouseMove(window, QPoint(1, 1))
    QTest.mouseMove(button, button.rect().center())
    QTest.mouseClick(button, Qt.MouseButton.RightButton)
    assert clicked.count() == 0
    assert bar.toolTip() == button.toolTip() == "Нажми, чтобы открыть расход за неделю по проектам."
    assert button.accessibleName() == "Расход за неделю по проектам"
    for part in (bar.bar, bar.now_label, bar.end_label, bar.action_label):
        position = part.mapTo(bar, part.rect().center())
        target = bar.childAt(position)
        assert target is button
        QTest.mouseClick(target, Qt.MouseButton.LeftButton, pos=target.mapFrom(bar, position))
    assert clicked.count() == 4
    assert window.mouse_presses == window.mouse_releases == 0


def test_forecast_supports_tab_enter_and_space_and_keeps_focus_on_refresh(section):
    _, before, bar, _ = section
    button = bar.chart_button
    before.setFocus()
    QTest.keyClick(before, Qt.Key.Key_Tab)
    assert button.hasFocus()
    clicked = QSignalSpy(bar.clicked)
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
        QTest.keyClick(button, key)
    assert clicked.count() == 3
    bar.set_chart_open(True)
    bar.set_forecast(forecast(75))
    assert button.hasFocus() and button.isChecked()
    assert "закрыть" in button.toolTip() and "25%" in bar.now_label.text()
    bar.set_chart_open(False)
    assert "открыть" in button.toolTip() and clicked.count() == 3


def test_narrow_forecast_wraps_axis_labels_without_clipping(app):
    bar = ForecastBar()
    bar.set_forecast(forecast())
    bar.setStyleSheet(scaled_style(2))
    bar.ensurePolished()
    bar.show()
    try:
        for narrow in (False, True):
            width = bar.minimumSizeHint().width() + 4 if narrow else bar.sizeHint().width() + 12
            bar.resize(width, bar.heightForWidth(width))
            app.processEvents()
            now, end = bar.now_label, bar.end_label
            assert (end.mapTo(bar, QPoint()).y() > now.mapTo(bar, QPoint()).y()) == narrow
            for label in (now, end):
                assert label.width() >= label.sizeHint().width()
                assert bar.rect().contains(QRect(label.mapTo(bar, QPoint()), label.size()))
    finally:
        bar.close()


def test_disabled_chart_keeps_forecast_and_skips_the_command(section):
    window, before, bar, after = section
    clicked = QSignalSpy(bar.clicked)
    bar.set_chart_open(True)
    bar.set_chart_enabled(False)
    assert bar.now_label.isVisible() and "30%" in bar.now_label.text()
    assert bar.action_label.isHidden() and not bar.chart_button.isChecked()
    assert not bar.chart_button.isEnabled() and not bar.toolTip() and not bar.chart_button.toolTip()
    before.setFocus()
    QTest.keyClick(before, Qt.Key.Key_Tab)
    assert after.hasFocus()
    QTest.mouseClick(bar.chart_button, Qt.MouseButton.LeftButton)
    assert clicked.count() == window.mouse_releases == window.mouse_presses == 0
    bar.set_chart_enabled(True)
    assert bar.chart_button.isEnabled() and bar.action_label.isVisible()
    QTest.mouseClick(bar.chart_button, Qt.MouseButton.LeftButton)
    assert clicked.count() == 1


def test_popup_closes_with_its_button_or_escape(app):
    popup = ForecastPopup()
    closed = QSignalSpy(popup.closed)
    popup.set_data(forecast(), None)
    assert popup.close_button.accessibleName() == "Закрыть график"
    try:
        popup.show()
        app.processEvents()
        assert popup.isVisible() and popup.tiles[0][1].text() == "70%"
        QTest.mouseMove(popup.close_button)
        assert popup.isVisible() and closed.count() == 0
        QTest.mouseClick(popup.close_button, Qt.MouseButton.LeftButton)
        assert not popup.isVisible() and closed.count() == 1
        popup.show()
        popup.close_button.setFocus()
        QTest.keyClick(popup.close_button, Qt.Key.Key_Escape)
        assert not popup.isVisible() and closed.count() == 2
    finally:
        popup.close()


def test_popup_metric_names_and_long_daily_rates_fit_at_largest_font(app):
    popup = ForecastPopup()
    popup.setStyleSheet(scaled_style(2))
    data = replace(forecast(), method=RATE_WEIGHTED, rate_per_day=315.7, allowed_per_day=12345678.0)
    popup.set_data(data, None)
    popup.show()
    app.processEvents()
    try:
        assert popup.tiles[1][1].text() == "~315,7% в сутки"
        assert popup.tiles[2][1].text() == "12345678% в сутки"
        for caption, value in popup.tiles:
            for label in (caption, value):
                assert label.width() >= label.sizeHint().width() and label.height() >= label.sizeHint().height()
                assert popup.rect().contains(QRect(label.mapTo(popup, QPoint()), label.size()))
            caption_rect = QRect(caption.mapTo(popup, QPoint()), caption.size())
            value_rect = QRect(value.mapTo(popup, QPoint()), value.size())
            assert not caption_rect.intersects(value_rect)
    finally:
        popup.close()
