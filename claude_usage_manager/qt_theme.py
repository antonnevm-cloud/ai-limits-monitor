"""Qt theme for Claude Usage Manager."""
from __future__ import annotations

COLORS = {
    "bg": "#090C10",
    "sidebar": "#111720",
    "control": "#151C25",
    "panel": "#18202A",
    "panel_alt": "#1E2833",
    "elevated": "#26323E",
    "table": "#141B23",
    "table_alt": "#1A232D",
    "header": "#222D38",
    "project": "#202D39",
    "project_text": "#E1E9F1",
    "border": "#374553",
    "border_soft": "#2A3642",
    "text": "#F0F3F7",
    "muted": "#B2BCC7",
    "faint": "#83909E",
    "accent": "#566D82",
    "accent_hover": "#647D93",
    "accent_pressed": "#4A6074",
    "selection": "#2B3E50",
    "selection_hover": "#253544",
    "success": "#6FAE9A",
    "warning": "#C2A46C",
    "danger": "#BE7B82",
}


def build_stylesheet() -> str:
    c = COLORS
    return f"""
    * {{
        font-family: "Segoe UI Variable Text", "Segoe UI";
        font-size: 10pt;
        color: {c['text']};
    }}
    QMainWindow, QWidget#Root {{ background: {c['bg']}; }}
    QWidget {{ background: transparent; }}

    QLabel#AppTitle {{
        font-family: "Segoe UI Variable Display", "Segoe UI";
        font-size: 19pt;
        font-weight: 700;
        color: {c['text']};
    }}
    QLabel#PageTitle {{
        font-family: "Segoe UI Variable Display", "Segoe UI";
        font-size: 15pt;
        font-weight: 650;
        color: {c['text']};
    }}
    QLabel#Subtitle, QLabel#Muted, QLabel#FieldLabel, QLabel#StatusText {{
        color: {c['muted']};
        font-size: 10pt;
    }}
    QLabel#FieldLabel {{ font-size: 9pt; }}
    QLabel#WarningText {{ color: {c['warning']}; }}

    QFrame#Sidebar {{
        background: {c['sidebar']};
        border: 1px solid {c['border_soft']};
        border-radius: 10px;
    }}
    QFrame#ControlPanel {{
        background: {c['control']};
        border: 1px solid {c['border_soft']};
        border-radius: 10px;
    }}
    QFrame#ContentPanel {{
        background: {c['panel']};
        border: 1px solid {c['border']};
        border-radius: 10px;
    }}
    QFrame#MetricCard {{
        background: {c['panel_alt']};
        border: 1px solid {c['border']};
        border-radius: 9px;
    }}
    QLabel#MetricLabel {{
        color: {c['muted']};
        font-size: 9pt;
    }}
    QLabel#MetricValue {{
        font-family: "Segoe UI Variable Display", "Segoe UI";
        color: {c['text']};
        font-size: 17pt;
        font-weight: 700;
    }}
    QLabel#MetricNote {{ color: {c['faint']}; font-size: 8.5pt; }}

    QLineEdit, QComboBox, QDateTimeEdit {{
        min-height: 36px;
        padding: 0 10px;
        background: {c['panel_alt']};
        border: 1px solid {c['border']};
        border-radius: 7px;
        selection-background-color: {c['selection']};
        color: {c['text']};
    }}
    QLineEdit:focus, QComboBox:focus, QDateTimeEdit:focus {{
        border-color: {c['accent_hover']};
    }}
    QComboBox::drop-down, QDateTimeEdit::drop-down {{
        border: none;
        width: 28px;
    }}
    QComboBox QAbstractItemView {{
        background: {c['elevated']};
        border: 1px solid {c['border']};
        selection-background-color: {c['selection']};
        padding: 5px;
        outline: none;
    }}

    QPushButton {{
        min-height: 36px;
        padding: 0 14px;
        background: {c['panel_alt']};
        border: 1px solid {c['border']};
        border-radius: 7px;
        color: {c['text']};
    }}
    QPushButton:hover {{ background: {c['elevated']}; border-color: {c['accent']}; }}
    QPushButton:pressed {{ background: {c['selection']}; }}
    QPushButton:disabled {{ color: {c['faint']}; background: {c['panel']}; }}
    QPushButton#PrimaryButton {{
        background: {c['accent']};
        border-color: {c['accent']};
        font-weight: 650;
    }}
    QPushButton#PrimaryButton:hover {{ background: {c['accent_hover']}; }}
    QPushButton#PrimaryButton:pressed {{ background: {c['accent_pressed']}; }}
    QPushButton#NavButton {{
        min-height: 40px;
        padding: 0 12px;
        text-align: left;
        border: none;
        border-radius: 7px;
        background: transparent;
        color: {c['muted']};
    }}
    QPushButton#NavButton:hover {{ background: {c['panel_alt']}; color: {c['text']}; }}
    QPushButton#NavButton[active="true"] {{
        background: {c['selection']};
        color: {c['text']};
        font-weight: 650;
    }}
    QPushButton#ToggleButton:checked {{
        background: {c['selection']};
        border-color: {c['accent']};
    }}

    QTreeWidget, QTableWidget {{
        background: {c['table']};
        alternate-background-color: {c['table_alt']};
        font-size: 11pt;
        border: none;
        border-radius: 8px;
        gridline-color: {c['border_soft']};
        outline: none;
        selection-background-color: {c['selection']};
        selection-color: {c['text']};
    }}
    QTreeWidget::item, QTableWidget::item {{
        min-height: 40px;
        padding: 5px 8px;
        border-bottom: 1px solid {c['border_soft']};
    }}
    QTreeWidget::item:hover, QTableWidget::item:hover {{ background: {c['selection_hover']}; }}
    QHeaderView::section {{
        min-height: 43px;
        padding: 0 9px;
        background: {c['header']};
        color: {c['muted']};
        border: none;
        border-bottom: 1px solid {c['border']};
        font-size: 10pt;
        font-weight: 650;
    }}
    QTreeWidget::branch {{ background: transparent; }}

    QMenu {{
        background: {c['elevated']};
        border: 1px solid {c['border']};
        border-radius: 7px;
        padding: 6px;
    }}
    QMenu::item {{ padding: 8px 26px 8px 10px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {c['selection']}; }}
    QMenu::separator {{ height: 1px; background: {c['border']}; margin: 5px 8px; }}

    QScrollBar:vertical {{ width: 10px; background: transparent; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 34px; }}
    QScrollBar::handle:vertical:hover {{ background: {c['accent']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ height: 10px; background: transparent; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 4px; min-width: 34px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    QProgressBar {{
        min-height: 3px; max-height: 3px;
        background: {c['border_soft']}; border: none; border-radius: 1px;
    }}
    QProgressBar::chunk {{ background: {c['accent']}; border-radius: 1px; }}

    QToolTip {{
        background: {c['elevated']};
        color: {c['text']};
        border: 1px solid {c['border']};
        padding: 5px;
    }}
    """