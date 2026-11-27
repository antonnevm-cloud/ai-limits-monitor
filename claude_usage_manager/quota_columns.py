"""Столбцы лимитов компактного монитора."""

import datetime as dt

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QSizePolicy

from .qt_theme import COLORS


REMAINING_COLORS = ((20, '#E07A70'), (50, '#D9B36A'))
TIME_LABELS = {'5 ч': '5 ч', 'День': 'день', 'Неделя': 'нед'}


def percent_text(value):
    return '\u2014' if value is None else f'{value:.0f}%'


def manual_reset_suffix(credits=None):
    if credits is not None and credits.state != 'unknown':
        count = credits.available_count
        if isinstance(count, int) and count > 0:
            return f'/{count}'
    return ''


def primary_cell(row):
    return row.long or row.short


def cell_label(cell):
    return TIME_LABELS.get(cell.label, '')


def limit_suffix(row, cell, credits):
    if cell is primary_cell(row) and credits and credits.state == 'available':
        return manual_reset_suffix(credits)
    return ''


def reset_countdown(value, now=None):
    if value is None:
        return ''
    now = now or dt.datetime.now(dt.timezone.utc)
    seconds = (value.astimezone(dt.timezone.utc) - now.astimezone(dt.timezone.utc)).total_seconds()
    if seconds <= 0:
        return ''
    days, rest = divmod(int(seconds // 60), 1440)
    hours, minutes = divmod(rest, 60)
    if days:
        return f'{days}д {hours}ч'
    if hours:
        return f'{hours}ч {minutes}м'
    return f'{max(1, minutes)}м'


class QuotaColumns(QFrame):
    """Каждому окну лимита соответствует столбец с процентом и сроком сброса."""

    def __init__(self, delta):
        super().__init__()
        self.delta = delta
        self.setObjectName('QuotaColumns')
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        grid = QGridLayout(self)
        grid.setContentsMargins(7, 3, 7, 3)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(1)
        self.headlines = []
        self.countdowns = []
        for column in range(2):
            headline = QLabel()
            headline.setTextFormat(Qt.TextFormat.RichText)
            headline.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            countdown = QLabel()
            countdown.setObjectName('QuotaCountdown')
            countdown.setStyleSheet(f"color:{COLORS['muted']};font-size:{8 + delta:g}pt;")
            for line, label in enumerate((headline, countdown)):
                label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                grid.addWidget(label, line, column, Qt.AlignmentFlag.AlignRight)
            self.headlines.append(headline)
            self.countdowns.append(countdown)

    @staticmethod
    def _set_label(label, text):
        visible = bool(text)
        if label.text() == text and label.isHidden() == (not visible):
            return False
        label.setText(text)
        label.setVisible(visible)
        return True

    def set_values(self, row, state, now):
        credits = state.snapshot.manual_resets
        cells = [cell for cell in (row.short, row.long) if cell is not None]
        changed = False
        for index, label in enumerate(self.headlines):
            text = ''
            if index < len(cells):
                cell = cells[index]
                suffix = limit_suffix(row, cell, credits)
                prefix = cell_label(cell)
                if prefix:
                    text = f'<span style="color:{COLORS["muted"]};font-size:{9 + self.delta:g}pt">{prefix}</span> '
                text += (f'<span style="color:{remaining_color(cell.remaining)};'
                         f'font-size:{10.5 + self.delta:g}pt;font-weight:600">'
                         f'{percent_text(cell.remaining)}{suffix}</span>')
            elif index == 0:
                text = percent_text(None)
            changed |= self._set_label(label, text)
        return self.set_countdowns(row, now) or changed

    def set_countdowns(self, row, now):
        cells = [cell for cell in (row.short, row.long) if cell is not None]
        changed = False
        for index, label in enumerate(self.countdowns):
            text = reset_countdown(cells[index].resets_at, now) if index < len(cells) else ''
            changed |= self._set_label(label, text)
        return changed


def remaining_color(value):
    if value is None:
        return COLORS['muted']
    return next((color for limit, color in REMAINING_COLORS if value < limit), '#DCE3EA')
