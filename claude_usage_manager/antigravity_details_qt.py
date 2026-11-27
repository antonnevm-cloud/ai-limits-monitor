"""Квоты из последнего снимка Antigravity в раскрытой карточке."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QScrollArea, QWidget

from .cache_threads_qt import ElidedLabel, SCROLLBAR_WIDTH, invalidate_parent_layouts
from .quota_columns import percent_text
from .quota_monitor_model import MonitorRow


class AntigravityDetails(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CacheScroll")
        self.setAccessibleName("Лимиты Antigravity")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)
        body = QWidget()
        body.setObjectName("CacheBody")
        self.grid = QGridLayout(body)
        self.grid.setContentsMargins(8, 4, 8, 8)
        self.grid.setHorizontalSpacing(5)
        self.grid.setVerticalSpacing(2)
        self.grid.setColumnStretch(0, 1)
        self.setWidget(body)
        self._labels = []
        self._lines = None
        self.content_width = 0

    def set_rows(self, rows: list[MonitorRow]) -> bool:
        lines = []
        for row in rows:
            cells = [cell for cell in (row.short, row.long) if cell is not None and cell.remaining is not None]
            if not cells:
                continue
            if not lines:
                lines.append(("QuotaLegend", ("Лимит", "Остаток", "Сброс")))
            title = {"Gemini Models": "Gemini", "Claude and GPT models": "Claude и GPT"}.get(row.name, row.name)
            lines.append(("QuotaGroup", (title,)))
            for cell in cells:
                reset = cell.resets_at.astimezone().strftime("%d.%m %H:%M") if cell.resets_at else "нет даты"
                lines.append(("QuotaDetail", (cell.label, percent_text(cell.remaining), reset)))
        if not lines:
            lines.append(("QuotaEmpty", ("Пока нет данных о лимитах.",)))
        if lines == self._lines:
            return False

        shape = [kind for kind, _ in lines]
        if self._lines is None or shape != [kind for kind, _ in self._lines]:
            while self.grid.count():
                label = self.grid.takeAt(0).widget()
                label.hide()
                label.deleteLater()
            self._labels.clear()
            for index, (kind, values) in enumerate(lines):
                labels = []
                for column in range(len(values)):
                    label = ElidedLabel() if kind == "QuotaGroup" else QLabel()
                    label.setObjectName(kind)
                    label.setTextFormat(Qt.TextFormat.PlainText)
                    label.setWordWrap(kind == "QuotaEmpty" or (kind == "QuotaDetail" and column == 2))
                    label.setAlignment((Qt.AlignmentFlag.AlignLeft if column == 0 else Qt.AlignmentFlag.AlignRight)
                                       | Qt.AlignmentFlag.AlignVCenter)
                    self.grid.addWidget(label, index, column, 1, 3 if len(values) == 1 else 1)
                    label.show()
                    labels.append(label)
                self._labels.append(labels)
        for (_, values), labels in zip(lines, self._labels):
            for value, label in zip(values, labels):
                if isinstance(label, ElidedLabel):
                    label.set_full_text(value)
                    label.setToolTip(value)
                    label.setAccessibleName(value)
                else:
                    label.setText(value)
                label.ensurePolished()
        self._lines = lines
        self.grid.activate()
        self.setMinimumWidth(self.grid.minimumSize().width() + SCROLLBAR_WIDTH)
        self.set_height_cap(None)
        return True

    def natural_height(self) -> int:
        width = self.content_width or self.viewport().width()
        return max(self.grid.minimumSize().height(), self.grid.heightForWidth(max(1, width - SCROLLBAR_WIDTH)))

    def set_height_cap(self, cap: int | None):
        natural = self.natural_height()
        height = natural if cap is None else min(natural, cap)
        if self.minimumHeight() != height or self.maximumHeight() != height:
            self.setFixedHeight(height)
            invalidate_parent_layouts(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if height < natural else Qt.FocusPolicy.NoFocus)
