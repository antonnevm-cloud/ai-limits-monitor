"""Встроенная справка AI Limits Monitor."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from .author_credit import github_link_button
from .qt_theme import COLORS


if sys.platform == "darwin":
    _PLATFORM = {
        "install": """
        <h2>Установка в macOS</h2>
        <p>В разделе Releases (выпуски) проекта AI Limits Monitor скачай архив
        с окончанием <b>macos-arm64.zip</b>, распакуй его и перенеси программу в папку
        "Программы".</p>""",
        "autostart": "",
        "uninstall": """
        <h2>Удаление</h2>
        <p>Перед удалением отключи строки состояния кнопкой <b>Отключить строку состояния</b>
        в окне подключений, затем перемести программу в Корзину. История и настройки
        остаются в папке <b>~/Library/Application Support/ClaudeUsageManager</b>.</p>""",
        "tray": "значок AI Limits Monitor в строке меню или в Dock",
        "licenses": "<b>Contents/Resources/licenses</b> внутри программы",
        "data": "Чтобы открыть папку по умолчанию, выбери в Finder команду "
                "<b>Переход к папке</b> и вставь <b>~/Library/Application Support/ClaudeUsageManager</b>.",
    }
else:
    _PLATFORM = {
        "install": """
        <h2>Установка в Windows</h2>
        <p>В разделе Releases (выпуски) проекта AI Limits Monitor скачай установщик
        с окончанием <b>windows-x64-setup.exe</b>. Он подходит для 64-разрядной Windows 10
        версии 1809 и новее и для Windows 11. Запусти его под своей учетной записью.</p>""",
        "autostart": """
        <h2>Запуск при входе в Windows</h2>
        <p>Пункт меню <b>Запускать при входе в Windows</b> включает и выключает автозапуск.
        При автозапуске виджет появляется, не забирая клавиатуру у открытой программы.</p>""",
        "uninstall": """
        <h2>Удаление</h2>
        <p>Удали программу в разделе Параметры > Приложения. Монитор вернет прежние
        строки состояния Claude Code и agy и снимет свой автозапуск. История и настройки
        остаются в папке <b>%LOCALAPPDATA%\\ClaudeUsageManager</b>, ее можно удалить вручную.</p>""",
        "tray": "значок AI Limits Monitor рядом с часами Windows",
        "licenses": "<b>_internal\\licenses</b> установленной программы",
        "data": "Чтобы открыть папку по умолчанию, вставь "
                "<b>%LOCALAPPDATA%\\ClaudeUsageManager</b> в адресную строку Проводника.",
    }

_TOPICS = (
    ("Начало работы", _PLATFORM["install"] + """
        <h2>Как подключить сервисы</h2>
        <ol>
          <li>При первом запуске откроется окно подключений. Позже его можно открыть
          из меню виджета командой <b>Подключения и вход</b>.</li>
          <li>Все четыре источника включены сразу: монитор опрашивает найденные клиенты.
          На вкладке программы, которой ты не пользуешься, сними флажок
          <b>Включить источник</b>. Если клиент еще не установлен, кнопка
          <b>Страница установки</b> откроет сайт его разработчика.</li>
          <li>Нажми <b>Войти</b> и следуй подсказкам официального клиента.
          Вернись в окно подключений и нажми <b>Проверить</b>.</li>
          <li>Для Claude Code подключи строку состояния по инструкции на вкладке
          "Источники" этой справки. Нажми <b>Сохранить</b>, затем <b>Обновить</b> в виджете.</li>
        </ol>""" + _PLATFORM["autostart"] + _PLATFORM["uninstall"]),
    ("Источники", """
        <h2>Claude Code</h2>
        <p>На вкладке Claude Code нажми <b>Подключить строку состояния</b>. Открой новый
        диалог в клиенте и поработай в нем. Проценты появятся, когда клиент передаст
        измерение. Если в профиле уже настроена своя команда строки состояния,
        появится сообщение о конфликте. В стандартном профиле монитор также читает свежие
        измерения из локальной истории Claude Desktop. Кнопка <b>Отключить строку состояния</b>
        возвращает настройку, которая была в профиле до подключения.</p>
        <h2>Codex</h2>
        <p>Монитор запрашивает лимиты подписки у установленного клиента Codex.
        Если ты сменил учетную запись, проверь вход в нужном профиле и обнови данные.</p>
        <h2>Grok Build</h2>
        <p>Клиент Grok Build сообщает недельный лимит. Если он временно недоступен,
        монитор может показать последнее измерение из журнала Grok.</p>
        <h2>Antigravity</h2>
        <p>В стандартном профиле монитор читает группы квот из отчета <b>/usage</b>
        программы <b>agy</b> (клиент Antigravity для командной строки).
        Для другого профиля или при недоступном отчете нажми <b>Подключить строку состояния</b>
        и открой новую сессию в клиенте. Кнопка <b>Проверить</b> проверяет запуск agy.
        Вход в учетную запись проверь в самом Antigravity.</p>
    """),
    ("Виджет", """
        <h2>Как читать проценты</h2>
        <p>Число на карточке показывает остаток за указанный период. Например, 70%
        за неделю означает, что израсходовано 30%. Под процентом видно время до
        сброса, если его сообщил сервис. У разных групп моделей могут быть свои лимиты.</p>
        <h2>Что открывается по щелчку</h2>
        <p><b>Стрелка рядом с именем</b> раскрывает список диалогов у Claude Code,
        Codex и Grok Build. Чтобы включить список, отметь в меню <b>Таймеры кэша тредов</b>.
        Знак ~ у таймера означает примерный срок хранения контекста диалога.
        У Antigravity стрелка показывает группы или модели с их квотами и известными
        сроками сброса.</p>
        <p><b>Полоса прогноза</b> показывает, хватит ли недельного остатка при текущем
        темпе. Она видна в раскрытой карточке Claude Code, Codex или Grok Build, когда
        включены таймеры кэша тредов и клиент сообщил недельный лимит. Прогноз включен
        по умолчанию. У Claude Code и Codex нажми на полосу, чтобы посмотреть расход
        за неделю по проектам.
        В меню пункт <b>Подробно по лимитам</b> открывает все доступные периоды и группы.</p>
        <h2>Как подогнать и вернуть окно</h2>
        <p>Чтобы передвинуть виджет, потяни его за заголовок. Чтобы изменить ширину,
        потяни за ручку на левом или правом краю. Виджет запомнит ширину, когда отпустишь
        кнопку мыши. Команда <b>Скрыть</b> убирает окно. Чтобы вернуть его, нажми
        """ + _PLATFORM["tray"] + """.</p>
        <p>В меню выбери <b>Настроить вид</b>, чтобы поменять размер шрифта или
        выключить прогноз. Клавиша <b>F1</b> в виджете открывает эту справку.</p>
    """),
    ("История", """
        <h2>Куда ушел лимит</h2>
        <p>Нажми <b>Аналитика</b>, выбери агента и период, затем <b>Рассчитать</b>.
        В разделах "Проекты", "Треды" и "Модели" видны сведения, которые сохранил
        выбранный клиент. Токены (фрагменты текста) показывают объем запросов и ответов.
        Условная стоимость оценивает этот объем по тарифам моделей. Сумму к оплате
        смотри в своем сервисе.</p>
        <p>Расход лимита за период виден в <b>процентных пунктах</b>. Например, при
        снижении остатка с 80% до 65% без сброса расход составил 15 процентных пунктов.
        Историю квот монитор накапливает из измерений. Для периода до первых замеров
        данных о расходе лимита может не быть.</p>
        <h2>Где лежат данные</h2>
        <p>Настройки и история измерений хранятся на этом компьютере. """ + _PLATFORM["data"] + """
        Переносимая копия хранит данные в папке <b>data</b> рядом с программой.
        Локальную историю работы монитор читает из выбранных профилей клиентов.</p>
        <p>Вход и хранение учетных данных берет на себя официальный клиент.
        Чтобы прекратить опрос одного сервиса, открой <b>Подключения и вход</b>,
        сними на его вкладке флажок <b>Включить источник</b> и нажми <b>Сохранить</b>.</p>
        <h2>Лицензии</h2>
        <p>Программа использует Qt 6 и Qt for Python по лицензии GNU LGPL версии 3,
        а также Python и другие компоненты на их собственных условиях. Тексты лицензий
        и уведомлений лежат в папке """ + _PLATFORM["licenses"] + """.</p>
    """),
    ("Нет данных", """
        <h2>Как отличить пустое значение от нуля</h2>
        <p>Прочерк означает, что для этого лимита сейчас нет числа. При исчерпанном
        лимите клиент может сообщить <b>0%</b>. Наведи указатель на точку состояния
        возле имени программы, чтобы узнать источник показаний.</p>
        <p><b>Онлайн</b> означает, что ответ получен от источника. <b>Локальные данные</b>
        взяты из записей клиента. <b>Кэш</b> означает, что на экране последнее сохраненное
        измерение монитора. Остаток мог измениться после этого замера.</p>
        <h2>Где проверить пути и вход</h2>
        <ol>
          <li>Открой <b>Подключения и вход</b>, выбери программу и убедись, что
          включен нужный источник. Обычно программа и каталог данных находятся автоматически.</li>
          <li>Для нестандартной установки нажми <b>Выбрать</b> у поля "Программа" и
          укажи файл клиента, например <b>codex.exe</b>. В поле "Каталог данных" укажи папку профиля, в котором
          ты работаешь. У Antigravity отдельно указываются программа agy, приложение
          для входа, каталог истории и каталог настроек agy.</li>
          <li>Нажми <b>Проверить</b>. Если клиент просит войти, воспользуйся кнопкой
          <b>Войти</b>. После проверки нажми <b>Сохранить</b> и <b>Обновить</b>.
          Чтобы вернуть автоматический поиск, нажми <b>Сбросить пути на авто</b>
          и сохрани изменения.</li>
        </ol>
    """),
)


class HelpDialog(QDialog):
    """Справка по работе с виджетом, доступная без подключенных клиентов."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Справка")
        self.setObjectName("HelpDialog")
        area = self.screen().availableGeometry()
        self.resize(min(700, area.width() * 9 // 10), min(540, area.height() * 9 // 10))
        self.setStyleSheet(f"""
            QDialog#HelpDialog {{ background: {COLORS['bg']}; }}
            QTabWidget::pane {{ border: 1px solid {COLORS['border']}; }}
            QTabBar::tab {{
                background: {COLORS['sidebar']}; color: {COLORS['muted']};
                padding: 7px 10px; margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background: {COLORS['panel']}; color: {COLORS['text']};
                border-bottom: 2px solid {COLORS['accent_hover']};
            }}
            QTabBar::tab:focus {{ border-bottom: 2px solid {COLORS['text']}; }}
            QTextBrowser {{
                background: {COLORS['panel']}; color: {COLORS['text']};
                border: none; font-size: 11pt;
                selection-background-color: {COLORS['selection']};
            }}
            QLabel#HelpAuthor {{ color: {COLORS['muted']}; font-size: 9pt; }}
            QPushButton#HelpCredit {{
                background: transparent; color: #e4a653; font-size: 9pt; text-decoration: underline;
                border: 1px solid transparent; border-radius: 3px; padding: 1px 3px; min-height: 18px;
            }}
            QPushButton#HelpCredit:hover {{ color: {COLORS['text']}; }}
            QPushButton#HelpCredit:focus {{ border-color: #e4a653; }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 10)
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Разделы справки")
        layout.addWidget(self.tabs, 1)
        for title, content in _TOPICS:
            page = QTextBrowser()
            page.setAccessibleName(title)
            page.setTabChangesFocus(True)
            page.setOpenLinks(False)
            page.setOpenExternalLinks(False)
            page.document().setDocumentMargin(16)
            page.document().setDefaultStyleSheet("""
                h2 { font-size: 125%; margin: 12px 0 5px; }
                p, ol { margin: 4px 0 10px; }
                li { margin-bottom: 4px; }
            """)
            page.setHtml(content)
            self.tabs.addTab(page, title)
        footer = QHBoxLayout()
        footer.setSpacing(3)
        author = QLabel("by onetone ·")
        author.setObjectName("HelpAuthor")
        footer.addWidget(author)
        credit = github_link_button("HelpCredit")
        footer.addWidget(credit, 0, Qt.AlignmentFlag.AlignLeft)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons, 1)
        layout.addLayout(footer)
