<div align="center">
  <h1><img src="docs/images/cover.svg" width="880" alt="AI Limits Monitor, виджет квот R1"></h1>
  <p>Квоты Claude Code, Codex, Grok Build и Antigravity в одном виджете Windows.</p>
  <p><a href="https://github.com/antonnevm-cloud/ai-limits-monitor/releases/tag/preview-2.0.5"><img src="docs/images/download-windows.svg" width="228" height="44" alt="Скачать для Windows"></a></p>
  <p><sub>Windows 10 и 11 x64 · Предварительная версия 2.0.5 · <a href="https://github.com/antonnevm-cloud">by onetone</a></sub></p>
</div>

<p align="center">
  <a href="docs/images/widget.png"><img src="docs/images/widget.png" width="28%" alt="Компактный виджет с квотами четырех агентов и недельным прогнозом"></a>
  <a href="docs/images/analytics.png"><img src="docs/images/analytics.png" width="69%" alt="Детальная аналитика с расходом по проектам и диалогам"></a>
</p>

<p align="center"><sub>Интерфейс 2.0.6 на тестовых данных. Нажмите на обзорный снимок, чтобы увеличить его.</sub></p>

**На этой странице**

[Установка](#установка) · [Установка клиентов](#установка-клиентов) · [Подключения и вход](#подключения-и-вход) · [Проверка источника Claude](#проверка-источника-claude)

[Виджет](#виджет) · [Квоты и полные сбросы](#квоты-и-полные-сбросы) · [Диалоги и кэш](#диалоги-и-кэш) · [Недельный прогноз](#недельный-прогноз) · [Меню](#меню)

[Настроить вид](#настроить-вид) · [Все настройки](#все-настройки) · [Ручной учет бонусных сбросов Claude](#ручной-учет-бонусных-сбросов-claude)

[Аналитика](#аналитика) · [Подробно по лимитам](#подробно-по-лимитам) · [Встроенная справка](#встроенная-справка) · [Данные и лицензия](#данные-и-лицензия)

## Установка

1. В [выпуске 2.0.5](https://github.com/antonnevm-cloud/ai-limits-monitor/releases/tag/preview-2.0.5) скачайте `AI-Limits-Monitor-2.0.5-windows-x64-setup.exe`. Требуется 64-разрядная Windows 10 версии 1809 или новее либо Windows 11.
2. Запустите установщик. Можно выбрать ярлык на рабочем столе и запуск при входе в Windows. Откройте **AI Limits Monitor** из меню "Пуск".
3. В окне **Подключения и вход** включите нужных агентов. Порядок входа и проверки приведен [ниже](#подключения-и-вход).

В установщике есть среда запуска виджета. Python и Node.js для самого виджета устанавливать не нужно. Для источников квот нужны их официальные клиенты.

> В предварительной 2.0.5 зафиксирован сценарий запуска с окном Windows Terminal, при котором закрытие терминала завершает виджет.

## Установка клиентов

Установите официальные клиенты тех сервисов, которые хотите видеть в виджете. Откройте инструкции для Windows на сайтах разработчиков.

| Сервис | Что установить |
| --- | --- |
| Claude Code | Claude Code для Windows. После установки монитор ищет `claude.exe`. [Инструкция Anthropic](https://code.claude.com/docs/en/setup#install-on-native-windows) |
| Codex | Codex для командной строки. Монитору нужен исполняемый файл `codex.exe`. [Инструкция OpenAI](https://learn.chatgpt.com/docs/codex/cli) |
| Grok Build | Grok Build для Windows. В разделе Install (Установка) выберите Windows (PowerShell). После установки монитор ищет `grok.exe`. [Инструкция Grok Build](https://docs.x.ai/build/overview#install) |
| Antigravity | Приложение Antigravity для входа и клиент командной строки `agy` для квот. [Загрузка Antigravity для Windows](https://antigravity.google/download?os=windows) · [Установка agy](https://antigravity.google/docs/getting-started?tab=cli) |

## Подключения и вход

Окно **Подключения** открывается при первом запуске. Чтобы вернуться в него, нажмите **Меню** в виджете и выберите **Подключения и вход**.

<img src="docs/images/elements/connections/menu-button.png" width="85" alt="Кнопка Меню в виджете">
<img src="docs/images/elements/connections/menu-connections.png" width="355" alt="Пункт Подключения и вход">

На вкладке нужного клиента отметьте **Включить источник**. Чтобы перестать опрашивать этот клиент, снимите флажок и сохраните настройки. Можно включать клиентов по одному.

<img src="docs/images/elements/connections/connection-tabs.png" width="381" alt="Четыре вкладки окна Подключения">

<img src="docs/images/elements/connections/enable-source.png" width="196" alt="Флажок Включить источник">

### Программа и профиль

Обычно пути находятся автоматически. Путь, показанный серым в пустом поле, монитор выбрал сам. Наведите указатель на поле, чтобы прочитать путь целиком.

<img src="docs/images/elements/connections/program.png" width="704" alt="Поле Программа и кнопка Выбрать">

В поле **Программа** укажите исполняемый файл клиента, если он установлен в нестандартной папке. Кнопка **Выбрать** справа открывает выбор файла. Для Codex на Windows подходит также `codex-x86_64-pc-windows-msvc.exe`.

<img src="docs/images/elements/connections/data-folder.png" width="704" alt="Поле Каталог данных и кнопка Выбрать">

В поле **Каталог данных** укажите папку того профиля, в котором работаете. Кнопка **Выбрать** здесь открывает выбор папки. Для Claude Code, Codex и Grok Build выбранный профиль используется и при запуске входа из окна.

<img src="docs/images/elements/connections/reset-paths.png" width="704" alt="Кнопка Сбросить пути на авто">

**Сбросить пути на авто** очищает ручные пути текущей вкладки. После сброса проверьте найденные пути и нажмите **Сохранить**.

### Вход и проверка

<img src="docs/images/elements/connections/install-page.png" width="198" alt="Кнопка Страница установки">

**Страница установки** появляется, если монитор не нашел программу клиента. На вкладке Antigravity она появляется и при отсутствии приложения для входа. Кнопка открывает официальный сайт разработчика.

<img src="docs/images/elements/connections/sign-in.png" width="90" alt="Кнопка Войти">

**Войти** запускает вход средствами официального клиента. Вход и хранение учетных данных выполняет сам клиент. Когда закончите вход в терминале, браузере или приложении, вернитесь в окно подключений.

<img src="docs/images/elements/connections/sign-in-open.png" width="424" alt="Сообщение после открытия штатного входа">

<img src="docs/images/elements/connections/sign-in-failed.png" width="377" alt="Сообщение об ошибке открытия входа">

Если вход не открылся, проверьте имя и путь к программе. У Antigravity проверьте и **Приложение для входа**.

<img src="docs/images/elements/connections/check.png" width="128" alt="Кнопка Проверить">

**Проверить** запускает короткую проверку выбранного клиента. Пока идет проверка, кнопка недоступна. Результат появится под кнопками.

<img src="docs/images/elements/connections/checking.png" width="288" alt="Проверка через официальный клиент">

### Claude Code

<img src="docs/images/elements/connections/tab-claude.png" width="126" alt="Вкладка Claude Code">

Проверьте вход в профиль Claude Code и подключите **строку состояния**. Через нее Claude Code передает монитору пятичасовой и недельный лимиты и известное время их сброса. Для стандартного профиля виджет также может брать свежие измерения из локальной истории Claude Desktop или поддерживаемого дополнения Claude.

| Ответ кнопки **Проверить** | Что он значит |
| --- | --- |
| <img src="docs/images/elements/connections/check-login-ok.png" width="204" alt="Клиент подтвердил вход"> | Клиент подтвердил, что в выбранном профиле выполнен вход. Этот же результат показывается на вкладке Codex. |
| <img src="docs/images/elements/connections/check-login-failed.png" width="205" alt="Клиент не подтвердил вход"> | Воспользуйтесь кнопкой **Войти** для нужного профиля, завершите вход и повторите проверку. |

<img src="docs/images/elements/connections/connect-statusline.png" width="704" alt="Кнопка Подключить строку состояния на вкладке Claude Code">

**Подключить строку состояния** сразу добавляет обработчик монитора в `settings.json` выбранного профиля Claude Code. Откройте новый диалог и дождитесь первого ответа модели. Для подписки Pro или Max проценты появятся, когда Claude Code передаст данные о лимитах.

### Codex

<img src="docs/images/elements/connections/tab-codex.png" width="79" alt="Вкладка Codex">

Войдите в учетную запись ChatGPT через клиент Codex. Кнопка **Проверить** проверяет вход и показывает один из двух результатов, приведенных выше для Claude Code. Сам виджет запрашивает у клиента Codex доступные периоды квот подписки и время их сброса.

### Grok Build

<img src="docs/images/elements/connections/tab-grok.png" width="107" alt="Вкладка Grok Build">

Выполните штатный вход в Grok Build. Кнопка **Проверить** запрашивает доступ к лимиту. Grok Build сообщает недельный лимит. Если запросить его временно не удается, виджет может показать последний замер из журнала Grok.

| Ответ кнопки **Проверить** | Что он значит |
| --- | --- |
| <img src="docs/images/elements/connections/check-grok-ok.png" width="278" alt="Grok Build подтвердил доступ к лимиту"> | Клиент успешно ответил на запрос доступа к лимиту. |
| <img src="docs/images/elements/connections/check-grok-failed.png" width="299" alt="Grok Build не подтвердил доступ к лимиту"> | Проверьте профиль и вход в Grok Build, затем повторите проверку. |

### Antigravity

<img src="docs/images/elements/connections/tab-antigravity.png" width="110" alt="Вкладка Antigravity">

У Antigravity четыре пути. Крестик в заполненном поле очищает путь и возвращает автоматический поиск для этого поля.

<img src="docs/images/elements/connections/agy-program.png" width="704" alt="Поле Программа agy и кнопка Выбрать">

**Программа agy** указывает на `agy.exe`. В стандартном профиле монитор читает группы квот из отчета `/usage`. Для этого нужна стабильная версия `agy` 1.1.11 или новее.

<img src="docs/images/elements/connections/agy-history.png" width="704" alt="Поле Каталог истории и кнопка Выбрать">

**Каталог истории** задает папку локальных данных Antigravity. Обычный путь в Windows выглядит как `%USERPROFILE%\.gemini\antigravity`.

<img src="docs/images/elements/connections/agy-login-app.png" width="704" alt="Поле Приложение для входа и кнопка Выбрать">

**Приложение для входа** указывает на `Antigravity.exe`. При стандартной настройке **Войти** запускает найденное приложение. Если Вы вручную указали рабочий `agy.exe` и оставили поле приложения пустым, откроется `agy`. Следуйте подсказкам открывшегося клиента.

<img src="docs/images/elements/connections/agy-settings-folder.png" width="704" alt="Поле Каталог настроек agy и кнопка Выбрать">

**Каталог настроек agy** указывает на папку с настройками клиента командной строки. По умолчанию монитор ищет папку `antigravity-cli` рядом с каталогом истории. При подключении строки состояния монитор записывает обработчик в `settings.json` из этой папки.

<img src="docs/images/elements/connections/agy-sign-in-profile.png" width="704" alt="Подсказка Antigravity при открытии входа с отдельным профилем">

Если выбраны отдельные каталоги, выполните вход в своем профиле `agy`. Указанные в мониторе пути служат для чтения истории и настройки строки состояния этого профиля.

| Ответ кнопки **Проверить** | Что он значит |
| --- | --- |
| <img src="docs/images/elements/connections/check-agy-ok.png" width="370" alt="Программа agy отвечает"> | Клиент `agy` сообщил свою версию. Сам вход в учетную запись проверьте в Antigravity. |
| <img src="docs/images/elements/connections/check-agy-failed.png" width="253" alt="Не удалось проверить версию agy"> | Выбранный клиент не смог сообщить версию. Проверьте, что запускается нужный `agy.exe`. |
| <img src="docs/images/elements/connections/check-agy-app-only.png" width="433" alt="Приложение найдено, agy отсутствует"> | Монитор нашел приложение для входа. Чтобы получить квоты, установите `agy` или укажите путь к нему. |

<img src="docs/images/elements/connections/agy-connect-statusline.png" width="704" alt="Кнопка Подключить строку состояния на вкладке Antigravity">

Подключите **строку состояния**, если `/usage` недоступен или Вы используете отдельный профиль. Затем откройте новую сессию `agy` в том же профиле и дождитесь первого измерения.

### Сообщения о путях, проверке и строке состояния

| Что показано | Как прочитать |
| --- | --- |
| <img src="docs/images/elements/connections/status-found.png" width="322" alt="Программа и каталог данных найдены"> | Монитор нашел исполняемый файл и папку профиля. Вход проверяется отдельной кнопкой **Проверить**. |
| <img src="docs/images/elements/connections/status-missing.png" width="364" alt="Программа и каталог данных не найдены"> | Установите клиент или укажите пути вручную. При первом входе клиент может сам создать стандартную папку профиля. |
| <img src="docs/images/elements/connections/agy-status-found.png" width="440" alt="Программа agy, каталог и приложение найдены"> | У Antigravity строка дополнительно сообщает, найдено ли приложение для входа. |
| <img src="docs/images/elements/connections/status-disabled.png" width="210" alt="Подключение выключено"> | Чтобы получать данные от этого клиента, поставьте флажок **Включить источник** и сохраните настройки. |
| <img src="docs/images/elements/connections/status-full-path.png" width="264" alt="Подсказка указать полный путь"> | Введите полный путь или воспользуйтесь кнопкой **Выбрать**. |
| <img src="docs/images/elements/connections/status-program-name.png" width="337" alt="Подсказка выбрать исполняемый файл клиента"> | Выберите исполняемый файл клиента, указанный в сообщении. |
| <img src="docs/images/elements/connections/check-start-failed.png" width="323" alt="Не удалось запустить выбранную программу"> | Проверьте, что выбранный файл запускается в Windows и доступен Вашей учетной записи. |
| <img src="docs/images/elements/connections/check-crash.png" width="227" alt="Клиент завершился с ошибкой"> | Откройте клиент напрямую и проверьте сообщение об ошибке. |
| <img src="docs/images/elements/connections/check-timeout.png" width="229" alt="Клиент не ответил за 15 секунд"> | Дождитесь завершения входа или первоначального запуска клиента и повторите проверку. |
| <img src="docs/images/elements/connections/statusline-installed.png" width="401" alt="Строка состояния подключена"> | Откройте новую сессию в выбранном профиле клиента. |
| <img src="docs/images/elements/connections/statusline-already.png" width="255" alt="Строка состояния уже подключена"> | Запись уже есть. Для получения свежего замера поработайте в выбранном профиле. |
| <img src="docs/images/elements/connections/statusline-conflict.png" width="478" alt="Найдена другая настройка строки состояния"> | Монитор оставил Вашу команду в профиле. Чтобы использовать обе команды Claude Code, добавьте обработчик монитора в свою команду вручную. |
| <img src="docs/images/elements/connections/statusline-failed.png" width="505" alt="Не удалось подключить строку состояния"> | Проверьте путь к профилю и доступ к его `settings.json`. У Antigravity этот файл находится в **Каталоге настроек agy**. |

### Сохранение

<img src="docs/images/elements/connections/save-cancel.png" width="204" alt="Кнопки Сохранить и Отмена">

**Сохранить** применяет настройки всех вкладок и запускает обновление виджета. **Отмена** закрывает окно без сохранения его настроек. Команда **Подключить строку состояния** меняет файл клиента сразу при нажатии.

<img src="docs/images/elements/connections/refresh.png" width="110" alt="Кнопка Обновить в виджете">

Чтобы повторить запрос квот после работы в клиенте, нажмите **Обновить**.

## Проверка источника Claude

<img src="docs/images/elements/connections/menu-claude-source.png" width="355" alt="Пункт меню Claude, проверить источник">

Откройте **Меню** и выберите **Claude · проверить источник...**. Окно **Claude · проверка данных** сверяет запись строки состояния в `settings.json` текущего профиля Claude Code и читает последний снимок, если он есть. Проверка выполняется по локальным файлам и работает без сети.

| Что показано | Что это значит |
| --- | --- |
| <img src="docs/images/elements/connections/claude-source-disconnected.png" width="353" alt="Источник не подключен к текущей версии"> | Для текущего профиля требуется подключить локальную строку состояния. Откройте вкладку **Claude Code** в окне подключений или выберите пункт меню ниже. |
| <img src="docs/images/elements/connections/claude-source-waiting.png" width="353" alt="Настройка сохранена, данных еще нет"> | Откройте новый диалог Claude Code, дождитесь ответа модели и обновите виджет. |
| <img src="docs/images/elements/connections/claude-source-no-quota.png" width="353" alt="Обработчик запускался, актуальных лимитов в снимке нет"> | Файл снимка есть, но пригодных данных о лимитах в нем сейчас нет. Проверьте профиль и подписку Pro или Max, затем получите новый ответ модели. |
| <img src="docs/images/elements/connections/claude-source-fresh.png" width="353" alt="Последний снимок Claude Code с датой и остатками"> | Время последнего измерения и остаток за 5 часов и 7 дней. Прочерк означает, что для этого периода нет пригодного числа. |
| <img src="docs/images/elements/connections/claude-source-stale.png" width="353" alt="Последний снимок Claude Code устарел"> | На экране последнее измерение. Поработайте в Claude Code, чтобы обновить его. |
| <img src="docs/images/elements/connections/claude-source-error.png" width="353" alt="Не удалось проверить локальный источник"> | Проверьте доступ к файлу settings.json нужного профиля и формат настроек. Подробности сбоя указаны в сообщении. |

<img src="docs/images/elements/connections/claude-source-ok.png" width="68" alt="Кнопка OK в окне проверки">

**OK** закрывает результат проверки.

<img src="docs/images/elements/connections/menu-claude-statusline.png" width="355" alt="Пункт меню Claude, подключить локальные лимиты">

**Claude · подключить локальные лимиты...** открывает подтверждение настройки для текущего профиля Claude Code. Этот пункт пригодится, если проверка показала, что запись строки состояния отсутствует или относится к прежней версии монитора.

<img src="docs/images/elements/connections/claude-statusline-confirm.png" width="353" alt="Подтверждение подключения локальной строки состояния Claude">

<img src="docs/images/elements/connections/claude-statusline-confirm-buttons.png" width="135" alt="Кнопки подтверждения и отказа">

Подтвердите подключение кнопкой **Yes** (Да), чтобы добавить обработчик. **No** (Нет) закрывает вопрос. При обнаружении другой команды строки состояния монитор оставит ее в профиле.

## Виджет

| Фрагмент | Где и как работает |
| --- | --- |
| <img src="docs/images/elements/widget/widget-title.png" width="130" alt="Знак R1 и заголовок AI Limits"> | Потяните за заголовок, чтобы переместить окно. |
| <img src="docs/images/elements/widget/edge-grip.png" width="26" alt="Ручка изменения ширины на краю окна"> | Потяните за ручку на левом или правом краю. Ширина запоминается. |
| <img src="docs/images/elements/widget/clock.png" width="52" alt="Часы в заголовке"> | Часы справа вверху показывают местное время. |
| <img src="docs/images/elements/widget/hide.png" width="99" alt="Кнопка Скрыть"> | **Скрыть** убирает виджет в область уведомлений. Чтобы вернуть окно, нажмите значок рядом с часами Windows. |
| <img src="docs/images/elements/widget/refresh.png" width="123" alt="Кнопка Обновить"> | **Обновить** запрашивает свежие данные квот. |
| <img src="docs/images/elements/widget/analytics.png" width="129" alt="Кнопка Аналитика"> | **Аналитика** открывает отчет за выбранные даты. |
| <img src="docs/images/elements/widget/menu.png" width="95" alt="Кнопка Меню"> | **Меню** открывает список команд. Тот же список доступен из значка в области уведомлений. |
| <img src="docs/images/elements/widget/footer.png" width="244" alt="Сводка источников и интервал опроса внизу"> | Внизу видны число источников в состоянии "онлайн", число локальных и сохраненных снимков и интервал опроса Codex и Antigravity. |
| <img src="docs/images/elements/widget/author.png" width="87" alt="Подпись by onetone"> | Подпись автора в нижней строке. |
| <img src="docs/images/elements/widget/github.png" width="60" alt="Ссылка GitHub"> | Ссылка на проект в нижней строке. |

## Квоты и полные сбросы

| Фрагмент | Где и что показывает |
| --- | --- |
| <img src="docs/images/elements/widget/dot-online.png" width="24" alt="Зеленая точка"> | Зеленая точка рядом с именем агента означает состояние "онлайн". |
| <img src="docs/images/elements/widget/dot-local.png" width="24" alt="Голубая точка"> | Голубая точка означает резервные локальные данные. |
| <img src="docs/images/elements/widget/dot-cached.png" width="24" alt="Желтая точка"> | Желтая точка означает последний сохраненный снимок квоты. |
| <img src="docs/images/elements/widget/dot-offline.png" width="24" alt="Серая точка"> | Серая точка означает отсутствие данных. Наведите указатель на точку, чтобы увидеть источник, время замера или причину сбоя. |
| <img src="docs/images/elements/widget/running-count.png" width="37" alt="Единица в скобках после имени агента"> | Число в скобках после имени - сколько показываемых диалогов сейчас выполняется. В примере работает один диалог. |
| <img src="docs/images/elements/widget/limit-short.png" width="76" alt="Пятичасовой остаток 64 процента"> | `5 ч 64%` справа в карточке означает остаток пятичасовой квоты. `нед` обозначает недельную квоту. При остатке ниже 50% число желтое, ниже 20% красное. |
| <img src="docs/images/elements/widget/limit-low.png" width="81" alt="Недельный остаток 12 процентов красным"> | Красное число показывает малый остаток. `0%` означает, что лимит исчерпан. |
| <img src="docs/images/elements/widget/limit-missing.png" width="35" alt="Прочерк вместо процента квоты"> | Прочерк означает, что значения пока нет. |
| <img src="docs/images/elements/widget/reset-timer.png" width="76" alt="Под процентом время 2 часа 15 минут"> | Время под процентом показывает, через сколько обновится именно это окно квоты. Строка появляется, когда известен срок сброса. |
| <img src="docs/images/elements/widget/limit-week-reset.png" width="101" alt="Остаток недели 29 процентов и суффикс дробь 2"> | В `нед 29%/2` осталось 29% недельного лимита, а `/2` означает два доступных **полных сброса**. Число добавляется при известном положительном остатке сбросов к основному показателю. |
| <img src="docs/images/elements/widget/payment.png" width="88" alt="Пятый день декабря и оплата 20 долларов"> | Дата и сумма под именем агента относятся к следующему ежемесячному платежу. Они рассчитываются из даты последней оплаты и суммы, введенных в настройках. |
| <img src="docs/images/elements/widget/arrow.png" width="59" alt="Стрелка раскрытия карточки"> | У Claude Code, Codex и Grok Build стрелка открывает диалоги, если включены их таймеры. У Antigravity открываются группы или модели с квотами. |

У Codex и Grok зеленое состояние может сохраняться до 15 минут после короткого сбоя источника. В этот период подсказка у точки указывает время последнего замера.

### Когда появляется "успей использовать"

Напоминание появляется **под карточкой агента**, если есть доступный полный сброс с известной датой сгорания и до ближайшего срока осталось **меньше 14 суток**.

<img src="docs/images/elements/widget/reset-warning-amber.png" width="295" alt="Желтая строка, ближайший полный сброс сгорит через 9 дней">

**Желтая строка** появляется, когда до ближайшего срока осталось **не меньше 3 суток** и **меньше 14 суток**. В примере доступно два сброса, а ближайший сгорит через 9 дней.

<img src="docs/images/elements/widget/reset-warning-red.png" width="317" alt="Красная строка, ближайший полный сброс сгорит через 2 дня">

**Красная строка** появляется, когда осталось **меньше 3 суток**. В обеих строках указаны местная дата и время ближайшего сгорания. Ровно за 14 суток, при неизвестном сроке или с момента его истечения эта строка не показывается.

### Раскрытая карточка Antigravity

| Фрагмент | Где и что показывает |
| --- | --- |
| <img src="docs/images/elements/widget/ag-group.png" width="77" alt="Заголовок группы Gemini"> | Название группы или модели. В свернутой карточке показывается выбранная группа. Режим выбора задается в общих настройках. |
| <img src="docs/images/elements/widget/ag-period.png" width="50" alt="Пятичасовой период в раскрытой карточке"> | **Лимит** обозначает период квоты, например пять часов или неделю. |
| <img src="docs/images/elements/widget/ag-remaining.png" width="61" alt="Остаток квоты группы Antigravity"> | **Остаток** показывает доступную часть квоты этой группы и периода. |
| <img src="docs/images/elements/widget/ag-reset.png" width="88" alt="Местные дата и время сброса Antigravity"> | **Сброс** показывает местные дату и время. Когда источник их не передал, в ячейке указано "нет даты". |

## Диалоги и кэш

Внешняя дуга показывает оставшееся время жизни кэша контекста. Дуга становится короче, от зеленой при остатке от 60% срока переходит через желтый к красному. Маленький индикатор состояния в компактном режиме расположен **внутри кольца над временем**, в полном режиме - рядом с названием диалога.

| Фрагмент | Где и что показывает |
| --- | --- |
| <img src="docs/images/elements/widget/ring-working.gif" width="78" alt="Серая дуга вращается внутри зеленого кольца кэша"> | Серый индикатор **вращается**, пока агент выполняет запрос. В примере кэш еще живет 57 минут. |
| <img src="docs/images/elements/widget/ring-approx.png" width="78" alt="Оранжевая точка и примерный остаток 24 минуты"> | Оранжевая точка означает ожидание Вашего ответа. Знак `~` перед таймером означает примерное время остывания. |
| <img src="docs/images/elements/widget/ring-unread.png" width="78" alt="Синяя точка готового непросмотренного ответа"> | Синяя точка означает, что ход завершен, ответ еще не просмотрен. |
| <img src="docs/images/elements/widget/ring-cooling.png" width="78" alt="У красного кольца остается 1 минута 20 секунд"> | Числа внутри кольца показывают минуты и секунды до остывания. Пустой индикатор состояния означает, что отдельный статус диалога не распознан. |
| <img src="docs/images/elements/widget/ring-cooled.png" width="78" alt="Серое кольцо с подписью Остыл"> | Подпись `остыл` и серая окружность означают, что срок кэша вышел. Недавно остывшие диалоги обычно остаются в списке до десяти минут. |
| <img src="docs/images/elements/widget/thread-title.png" width="146" alt="Заголовок диалога Сборка отчета"> | Верхняя строка - название диалога. |
| <img src="docs/images/elements/widget/project.png" width="147" alt="Имя проекта Отчеты магазина под диалогом"> | Под названием - проект. Среда запуска и время последнего запроса видны в подсказке строки. |
| <img src="docs/images/elements/widget/share.png" width="74" alt="Оценочная доля недельной квоты 15,1 процента"> | `~15,1%` - оценка части недельной квоты, потраченной этим диалогом. Расчет использует расход аккаунта и распределяет его по стоимости токенов из локальной истории. |
| <img src="docs/images/elements/widget/cost.png" width="67" alt="Условная стоимость 16 долларов 30 центов"> | `$16,30` - условная стоимость токенов по опубликованным тарифам доступа к моделям. Суффикс `+` сообщает, что часть токенов осталась без цены. Оплату подписки показывает поставщик. |
| <img src="docs/images/elements/widget/rewrite.png" width="78" alt="Значок монеты и стоимость перезаписи 28 центов"> | Значок монеты показывает оценку цены повторной записи контекста в кэш после остывания. |
| <img src="docs/images/elements/widget/glutton.png" width="50" alt="Красное пламя у диалога"> | Пламя отмечает диалог с наибольшей оценкой расхода недельной квоты в списке этого агента. |
| <img src="docs/images/elements/widget/threads-more.png" width="148" alt="Показать еще диалоги"> | **Показать еще** раскрывает остальные строки. |
| <img src="docs/images/elements/widget/threads-less.png" width="132" alt="Свернуть список диалогов"> | **Свернуть список** возвращает краткий вид. |
| <img src="docs/images/elements/widget/threads-restore.png" width="155" alt="Вернуть скрытые диалоги"> | **Вернуть скрытые** появляется, когда список учитывает правила скрытия. Возвращает скрытые диалоги этого агента. |

<img src="docs/images/elements/widget/cache-warning.png" width="253" alt="Колокольчик и напоминание Остынет через 1:20">

**Колокольчик** появляется под диалогом, если включено предупреждение и до остывания осталось не больше двух минут.

## Недельный прогноз

| Фрагмент | Где и что показывает |
| --- | --- |
| <img src="docs/images/elements/widget/forecast-band.png" width="400" alt="Ярко-синяя, приглушенно-синяя и красная части прогноза"> | Полоса идет от начала недели до сброса. Ярко-синяя часть заканчивается в текущем времени. Приглушенно-синяя показывает, сколько еще продержится лимит. Красная отмечает время после прогнозируемого исчерпания и до сброса. |
| <img src="docs/images/elements/widget/forecast-now.png" width="136" alt="Сейчас осталось 29 процентов"> | Слева под полосой виден остаток недельной квоты сейчас. |
| <img src="docs/images/elements/widget/forecast-enough.png" width="136" alt="Время сброса и примерный остаток 49 процентов зеленым"> | Зеленое число после времени сброса - ожидаемый остаток при текущем темпе. |
| <img src="docs/images/elements/widget/forecast-shortage.png" width="210" alt="Красное время исчерпания и время сброса"> | Красное время рядом с `кончится` показывает, когда ожидается исчерпание. `сброс` показывает время обновления квоты. |
| <img src="docs/images/elements/widget/forecast-open.png" width="62" alt="Слово Расход на полосе прогноза"> | Нажмите на полосу или **Расход**, чтобы открыть график по проектам. |
| <img src="docs/images/elements/widget/forecast-hint.png" width="366" alt="Подсказка Нажми, чтобы открыть расход за неделю по проектам"> | При наведении на полосу появляется подсказка. График открывается по щелчку. |

<img src="docs/images/elements/widget/forecast-chart.png" width="426" alt="Поле графика недельного расхода с долями проектов">

На графике слева направо идут дни недели лимита. Высота области показывает накопленный процент. Цвета разделяют оценочный расход по проектам.

| Фрагмент | Что показывает |
| --- | --- |
| <img src="docs/images/elements/widget/forecast-used.png" width="72" alt="Показатель Израсходовано"> | **Израсходовано** - использованный процент недели на момент замера. |
| <img src="docs/images/elements/widget/forecast-rate.png" width="147" alt="Показатель Темп"> | **Темп** - средний или рассчитанный по замерам расход в сутки. При недостатке замеров показывается подпись "мало данных". |
| <img src="docs/images/elements/widget/forecast-allowed.png" width="130" alt="Показатель Можно до сброса"> | **Можно до сброса** - сколько можно тратить в сутки, чтобы остатка хватило. |
| <img src="docs/images/elements/widget/forecast-legend.png" width="147" alt="Цвет и имя проекта в легенде"> | Легенда связывает цвет с проектом. Наведите указатель на проект, чтобы выделить его долю и увидеть пояснение. |
| <img src="docs/images/elements/widget/forecast-close.png" width="76" alt="Крестик закрытия графика"> | Закрывает график. То же делает клавиша Escape. |

## Меню

| Фрагмент | Действие |
| --- | --- |
| <img src="docs/images/elements/widget/menu-view.png" width="138" alt="Настроить вид"> | Открывает панель вида виджета. |
| <img src="docs/images/elements/widget/menu-settings.png" width="137" alt="Все настройки"> | Открывает общие настройки окна, агентов и платежей. |
| <img src="docs/images/elements/widget/menu-connections.png" width="184" alt="Подключения и вход"> | Открывает настройки источников и штатного входа в клиенты. |
| <img src="docs/images/elements/widget/menu-timers.png" width="211" alt="Таймеры кэша тредов"> | Включает или выключает отображение диалогов с таймерами кэша. |
| <img src="docs/images/elements/widget/menu-show.png" width="119" alt="Показать"> | Возвращает скрытый виджет на экран. |
| <img src="docs/images/elements/widget/menu-refresh.png" width="127" alt="Обновить"> | Запрашивает обновление квот, как верхняя кнопка виджета. |
| <img src="docs/images/elements/widget/menu-limits.png" width="198" alt="Подробно по лимитам"> | Открывает доступные окна и пулы квот каждого агента. |
| <img src="docs/images/elements/widget/menu-analytics.png" width="187" alt="Детальная аналитика"> | Открывает историю расхода по выбранному периоду. |
| <img src="docs/images/elements/widget/menu-claude-link.png" width="284" alt="Claude, подключить локальные лимиты"> | Настраивает передачу локальных лимитов от Claude Code через его строку состояния. |
| <img src="docs/images/elements/widget/menu-claude-check.png" width="216" alt="Claude, проверить источник"> | Показывает, откуда получены квоты Claude, и помогает проверить настройки передачи. |
| <img src="docs/images/elements/widget/menu-claude-resets.png" width="223" alt="Claude, сбросы вручную"> | Открывает ручной учет доступных полных сбросов Claude. |
| <img src="docs/images/elements/widget/menu-help.png" width="113" alt="Справка"> | Открывает встроенное руководство. То же делает F1. |
| <img src="docs/images/elements/widget/menu-quit.png" width="94" alt="Выход"> | Завершает работу виджета. |

## Настроить вид

Откройте меню виджета и выберите **Настроить вид**. Изменения сохраняются сразу. Чтобы увидеть их в списке тредов (чатов и сеансов), раскройте карточку Claude, Codex или Grok. Для списков должна быть включена команда **Таймеры кэша тредов**.

| Фрагмент интерфейса | Что меняет |
| --- | --- |
| <img src="docs/images/elements/settings/view-close.png" alt="Заголовок Вид тредов и кнопка закрытия" width="348"> | **×** закрывает панель **Вид тредов**. |
| <img src="docs/images/elements/settings/view-compact.png" alt="Переключатель Компактно" width="348"> | **Компактно** размещает название треда и строку с проектом и показателями расхода плотнее. Кольцо таймера становится меньше. |
| <img src="docs/images/elements/settings/view-timer.png" alt="Переключатель Время жизни кэша" width="348"> | **Время жизни кэша** показывает кольцо с обратным отсчетом до истечения срока кэша. |
| <img src="docs/images/elements/settings/view-title.png" alt="Переключатель Название треда" width="348"> | **Название треда** показывает название чата или сеанса в строке. |
| <img src="docs/images/elements/settings/view-project.png" alt="Переключатель Проект и среда" width="348"> | **Проект и среда** показывает проект и приложение или способ запуска треда. |
| <img src="docs/images/elements/settings/view-usage.png" alt="Поле Расход треда" width="348"><br><img src="docs/images/elements/settings/view-usage-options.png" alt="Варианты расхода треда" width="120"> | **Расход треда** показывает оценку его доли недельного лимита, оценку стоимости токенов по тарифам для разработчиков в долларах или обе цифры. Выберите **нет**, чтобы скрыть расход. |
| <img src="docs/images/elements/settings/view-rewrite.png" alt="Переключатель Цена остывания" width="348"> | **Цена остывания** показывает расчетную стоимость повторной записи контекста после истечения срока кэша, когда есть данные для расчета. |
| <img src="docs/images/elements/settings/view-warning.png" alt="Переключатель Предупреждение" width="348"> | **Предупреждение** выделяет строку в последние две минуты жизни кэша. |
| <img src="docs/images/elements/settings/view-glutton.png" alt="Переключатель Подсветка прожорливого" width="348"> | **Подсветка прожорливого** отмечает тред с наибольшей оценкой расхода недельного лимита. Значок появляется, если есть положительные оценки хотя бы для двух тредов. |
| <img src="docs/images/elements/settings/view-pin_hide.png" alt="Переключатель Учитывать правила тредов" width="348"> | **Учитывать правила тредов** поднимает закрепленные треды вверх и убирает скрытые из списка. |
| <img src="docs/images/elements/settings/view-terminal.png" alt="Переключатель Claude Code из терминала" width="348"> | **Claude Code из терминала** включает в список сеансы Claude Code из терминала и запуски из скриптов. Они также учитываются в счетчике работающих тредов. |
| <img src="docs/images/elements/settings/view-forecast.png" alt="Переключатель Прогноз недели" width="348"> | **Прогноз недели** добавляет полосу прогноза к раскрытой карточке, когда есть данные о недельном лимите. |
| <img src="docs/images/elements/settings/view-chart.png" alt="Поле График расхода" width="330"><br><img src="docs/images/elements/settings/view-chart-options.png" alt="Варианты открытия графика расхода" width="126"> | **График расхода** открывает график по щелчку на полосе прогноза. Чтобы отключить открытие, выберите **выключен**. |
| <img src="docs/images/elements/settings/view-font.png" alt="Поле Размер шрифта" width="348"><br><img src="docs/images/elements/settings/view-font-options.png" alt="Варианты размера шрифта" width="150"> | **Размер шрифта** меняет размер текста в виджете, этой панели и графике. Вариант **как было** возвращает обычный размер. |
| <img src="docs/images/elements/settings/view-opacity.png" alt="Ползунок Прозрачность окна" width="348"> | **Прозрачность окна** задает видимость окна вместе с текстом от 20 до 100% с шагом 10%. При 100% окно непрозрачно. |
| <img src="docs/images/elements/settings/view-all-settings.png" alt="Ссылка Все настройки" width="135"> | **Все настройки** открывает одноименное окно. |

## Все настройки

Откройте **Все настройки** из меню виджета или по ссылке в панели **Вид тредов**.

| Фрагмент интерфейса | Что меняет |
| --- | --- |
| <img src="docs/images/elements/settings/all-refresh.png" alt="Поле Опрос Codex и Antigravity" width="496"><br><img src="docs/images/elements/settings/all-refresh-options.png" alt="Интервалы опроса от 15 секунд до 5 минут" width="299"> | **Опрос Codex и Antigravity** задает, как часто виджет получает данные об их лимитах. |
| <img src="docs/images/elements/settings/all-opacity.png" alt="Поле Прозрачность окна" width="496"><br><img src="docs/images/elements/settings/all-opacity-options.png" alt="Варианты прозрачности от 20 до 100 процентов" width="299"> | **Прозрачность окна** задает то же значение, что и ползунок в **Настроить вид**. В этом окне значение меняется после сохранения. |
| <img src="docs/images/elements/settings/all-topmost.png" alt="Флажок Поверх остальных окон" width="222"> | **Поверх остальных окон** удерживает виджет поверх окон других программ. |
| <img src="docs/images/elements/settings/all-providers.png" alt="Флажки Показывать для Claude, Codex, Grok и Antigravity" width="496"> | **Показывать** управляет карточками компактного виджета. Отметьте Claude, Codex, Grok или Antigravity, чтобы их карточки были видны. |
| <img src="docs/images/elements/settings/all-connections.png" alt="Кнопка Подключения и вход" width="496"> | **Подключения и вход** открывает отдельное окно, где можно выбрать путь к клиенту или профилю и включить либо выключить источник данных. |
| <img src="docs/images/elements/settings/all-antigravity-group.png" alt="Поле Antigravity, группа в заголовке" width="496"><br><img src="docs/images/elements/settings/all-antigravity-group-options.png" alt="Автовыбор и список групп Antigravity" width="488"> | **Antigravity · группа в заголовке** появляется при наличии групповых лимитов. Выберите группу вручную или оставьте **Авто: минимальный остаток**. Автовыбор показывает группу с наименьшим остатком на неделю. Если недельных лимитов нет, сравниваются пятичасовые. Если выбранная вручную группа пропадет из данных, сработает автовыбор. |
| <img src="docs/images/elements/settings/all-antigravity-mode.png" alt="Поле Antigravity, модель по умолчанию" width="496"><br><img src="docs/images/elements/settings/all-antigravity-mode-options.png" alt="Четыре режима выбора модели Antigravity" width="488"> | **Antigravity · модель по умолчанию** появляется, когда групповых лимитов нет. **Авто: самая используемая сегодня** берет модель с наибольшим расходом токенов за день. **Авто: последняя использованная** берет последнюю модель из истории на этом компьютере за последние семь дней. **Фиксированная модель** показывает выбранную ниже модель. **Самый израсходованный лимит** показывает модель с наименьшим процентом остатка. |
| <img src="docs/images/elements/settings/all-antigravity-model.png" alt="Поле выбора конкретной модели Antigravity" width="496"><br><img src="docs/images/elements/settings/all-antigravity-model-options.png" alt="Список доступных моделей Antigravity" width="488"> | Список моделей заполняется по данным о лимитах. Выбранная модель влияет на заголовок в режиме **Фиксированная модель**. Если ее лимит недоступен, виджет попробует показать последнюю использованную модель, затем модель с наименьшим остатком. |
| <img src="docs/images/elements/settings/all-payment-date.png" alt="Дата последней оплаты 18.10" width="261"> | **Последняя оплата** принимает дату в формате **ДД.ММ** по каждому агенту. Следующий платеж приходится на то же число следующего месяца или на его последний день, если такого числа нет. |
| <img src="docs/images/elements/settings/all-payment-amount.png" alt="Сумма подписки 20 долларов" width="261"> | **Сумма** хранит число и валюту, например **20 $**. Заполните дату и сумму, чтобы платеж был виден в карточке. Чтобы убрать его, очистите оба поля. |
| <img src="docs/images/elements/settings/all-save-only.png" alt="Кнопка Сохранить" width="129"> | **Сохранить** применяет общие настройки. |
| <img src="docs/images/elements/settings/all-cancel.png" alt="Кнопка Отмена" width="105"> | **Отмена** закрывает окно и отбрасывает правки. |

## Ручной учет бонусных сбросов Claude

Откройте меню виджета и выберите **Claude · сбросы вручную...**. В окно **Claude · бонусные сбросы** внесите бонусы **Reset for free** (бесплатный сброс) из раздела **Usage** (расход) в настройках Claude. Каждая строка учитывает один бонус. После использования бонуса удалите его вручную. Записи со сроком автоматически перестают учитываться, когда срок истекает.

| Фрагмент интерфейса | Что делает |
| --- | --- |
| <img src="docs/images/elements/settings/resets-list.png" alt="Список Доступные бонусы и счетчик Добавлено" width="478"> | **Доступные бонусы** показывает добавленные записи и их число. В списке можно выбрать одну запись для изменения срока или удаления. Всего можно добавить до 30 бонусов. |
| <img src="docs/images/elements/settings/resets-expiration.png" alt="Поле даты и времени окончания бонуса" width="420"> | Введите местные дату и время окончания. Нажмите правый край поля, чтобы выбрать день в календаре. |
| <img src="docs/images/elements/settings/resets-calendar.png" alt="Календарь выбора даты окончания бонуса" width="262"> | Выберите день в календаре. Срок должен быть в будущем. |
| <img src="docs/images/elements/settings/resets-add-date.png" alt="Кнопка Добавить по дате" width="184"> | **Добавить по дате** добавляет один бонус с указанным сроком. |
| <img src="docs/images/elements/settings/resets-no-expiry.png" alt="Кнопка Без срока" width="201"> | **Без срока** добавляет бонус без даты окончания. |
| <img src="docs/images/elements/settings/resets-change.png" alt="Кнопка Изменить срок" width="201"> | **Изменить срок** заменяет срок выбранной записи датой и временем из поля. Так можно указать срок и для записи **Без срока действия**. |
| <img src="docs/images/elements/settings/resets-delete.png" alt="Кнопка Удалить выбранный" width="202"> | **Удалить выбранный** убирает выбранную запись из списка. |
| <img src="docs/images/elements/settings/resets-save-only.png" alt="Кнопка Сохранить" width="129"> | **Сохранить** записывает список для текущего профиля Claude и обновляет счетчик в карточке. |
| <img src="docs/images/elements/settings/resets-cancel.png" alt="Кнопка Отмена" width="105"> | **Отмена** закрывает окно и отбрасывает правки. |

## Аналитика

Нажмите **Аналитика** в виджете или **Детальная аналитика** в меню значка рядом с часами Windows. Окно **AI Usage** показывает расход по локальной истории выбранного агента. На снимках тестовые данные.

### Фильтры и расчет

| Фрагмент | Что показывает или делает |
| --- | --- |
| <img src="docs/images/elements/analytics/control-agent.png" width="172" alt="Поле Агент"><br><img src="docs/images/elements/analytics/agent-options.png" width="168" alt="Варианты поля Агент"> | **Агент** выбирает источник данных. В списке доступны Claude Code, Codex, Grok Build и Antigravity. |
| <img src="docs/images/elements/analytics/control-period.png" width="222" alt="Поле Период"><br><img src="docs/images/elements/analytics/period-options.png" width="218" alt="Варианты поля Период"> | **Период** задает диапазон расчета. Есть готовые варианты для дня, недели и месяца, скользящие интервалы 5 часов, 24 часа и 7 дней, а также **Произвольный период**. |
| <img src="docs/images/elements/analytics/control-start.png" width="195" alt="Поле Начало"> | **Начало** задает первую дату и время произвольного периода по местным часам. Граница входит в расчет. |
| <img src="docs/images/elements/analytics/control-end.png" width="354" alt="Поле Конец"> | **Конец** задает последнюю дату и время произвольного периода по местным часам. Граница входит в расчет. |
| <img src="docs/images/elements/analytics/search.png" width="329" alt="Поиск по тредам и проектам"> | **Поиск** находит совпадения в названии диалога или проекта независимо от регистра. Он сужает список **Тредов** и ветки **Обзора**. Карточки и сводки считают весь набор за выбранный период. |
| <img src="docs/images/elements/analytics/hidden.png" width="255" alt="Кнопка Показывать скрытые"> | **Показывать скрытые** включает скрытые диалоги в списки и суммы. |
| <img src="docs/images/elements/analytics/control-calculate.png" width="149" alt="Кнопка Рассчитать"> | **Рассчитать** перечитывает локальную историю. Нажмите после изменения дат или текста поиска. В поиске также работает **Enter**. Смена агента, периода или показа скрытых запускает расчет сразу. |
| <img src="docs/images/elements/analytics/control-export.png" width="122" alt="Кнопка Экспорт"> | **Экспорт** сохраняет результат последнего расчета в файл. |
| <img src="docs/images/elements/analytics/calculation-status.png" width="515" alt="Строка состояния расчета"> | Внизу окна указаны время расчета, число проектов и показанных тредов, время снимка квоты и сообщения источника. |

### Карточки над вкладками

| Фрагмент | Что показывает или делает |
| --- | --- |
| <img src="docs/images/elements/analytics/card-tokens.png" width="261" alt="Карточка Токены"> | **Токены** показывают объем обработки за выбранный период, включая запросы, ответы и операции с кэшем, если эти счетчики доступны. |
| <img src="docs/images/elements/analytics/card-cost.png" width="260" alt="Карточка Условная стоимость"><br><img src="docs/images/elements/analytics/metrics-antigravity-cost.png" width="260" alt="Стоимость Antigravity без цены"> | **Условная стоимость** оценивает расход в долларах по тарифам моделей. Фактическую сумму оплаты показывает поставщик. Для Antigravity при отсутствии цены в карточке виден прочерк, в таблицах $0,00. |
| <img src="docs/images/elements/analytics/card-threads.png" width="261" alt="Карточка Треды"> | **Треды** показывают число рабочих диалогов. Объединенная задача считается одной строкой. Под числом видны проекты. |
| <img src="docs/images/elements/analytics/card-claude-week.png" width="260" alt="Недельная квота Claude Code"><br><img src="docs/images/elements/analytics/card-codex-week.png" width="260" alt="Недельная квота Codex"> | **Недельная квота** в Claude Code и Codex показывает использованный процент текущего основного окна. 71% в аналитике соответствует 29% остатка в виджете. |
| <img src="docs/images/elements/analytics/card-claude-five-hour.png" width="261" alt="Пятичасовое окно Claude Code"> | **Пятичасовое окно** показывает, сколько уже использовано в текущем пятичасовом окне Claude Code. |
| <img src="docs/images/elements/analytics/card-period-consumption.png" width="260" alt="Карточка Расход лимита"> | **Расход лимита** оценивает расход основного окна за выбранный период в п.п. (процентных пунктах). Если остаток снизился с 80% до 65% без сброса, расход равен 15 п.п. **н/д** означает, что для оценки не хватает замеров. |
| <img src="docs/images/elements/analytics/card-codex-spark.png" width="261" alt="Карточка Codex Spark"> | **Codex Spark** показывает использованный процент отдельного недельного пула, когда эта квота доступна. |
| <img src="docs/images/elements/analytics/card-grok-week.png" width="260" alt="Карточка Grok неделя"> | **Grok · неделя** показывает использованный процент общей недельной квоты Grok Build. |
| <img src="docs/images/elements/analytics/card-grok-reset.png" width="261" alt="Карточка Сброс недели"> | **Сброс недели** показывает ближайший известный сброс Grok Build по местному времени. |
| <img src="docs/images/elements/analytics/card-antigravity-model.png" width="260" alt="Основное окно модели Antigravity"><br><img src="docs/images/elements/analytics/card-antigravity-group.png" width="260" alt="Основное окно группы Gemini"> | В первой карточке **Antigravity** виден использованный процент основного окна. В заголовке карточки указано имя модели или группы. |
| <img src="docs/images/elements/analytics/card-antigravity-pools.png" width="261" alt="Счетчик Модельных пулов"><br><img src="docs/images/elements/analytics/card-antigravity-group-pools.png" width="261" alt="Модельных пулов при наличии только групп"> | **Модельных пулов** показывает число полученных окон моделей и ближайший известный сброс. Если источник передал только групповые окна, здесь и в столбцах лимитов отдельных моделей стоят прочерки. |
| <img src="docs/images/elements/analytics/card-antigravity-consumption.png" width="260" alt="Расход лимитов моделей Antigravity"><br><img src="docs/images/elements/analytics/card-antigravity-group-consumption.png" width="260" alt="Расход четырех окон групп Antigravity"> | **Расход лимитов** показывает число отдельных окон, для которых удалось рассчитать расход. В примере с групповыми данными замеры есть у четырех окон двух групп. Их остатки и сроки сброса видны в **Подробно по лимитам**. |

### Вкладки аналитики

Чтобы отсортировать таблицу, нажмите заголовок столбца.

#### Обзор

<img src="docs/images/elements/analytics/tab-overview.png" width="172" alt="Вкладка Обзор">

| Фрагмент | Что показывает или делает |
| --- | --- |
| <img src="docs/images/elements/analytics/column-overview-project.png" width="342" alt="Столбец Проект / тред"> | **Проект / тред** показывает проект и его диалоги. Раскройте проект для просмотра диалогов. Двойной щелчок по диалогу открывает детализацию. |
| <img src="docs/images/elements/analytics/column-overview-models.png" width="286" alt="Столбец Модели"> | **Модели** перечисляет модели диалога или проекта. |
| <img src="docs/images/elements/analytics/column-thread-count.png" width="95" alt="Столбец Тредов"> | **Тредов** показывает число диалогов в видимой ветке проекта. |
| <img src="docs/images/elements/analytics/column-tokens.png" width="122" alt="Столбец Токены"> | **Токены** показывает объем обработки в строке. Для модели это сумма ввода, вывода, создания и чтения кэша. |
| <img src="docs/images/elements/analytics/column-cost.png" width="122" alt="Столбец Стоимость"> | **Стоимость** показывает условную цену расхода в строке. |
| <img src="docs/images/elements/analytics/column-share.png" width="92" alt="Столбец Доля"> | **Доля** считается от общей стоимости периода, а при ее отсутствии от токенов. |
| <img src="docs/images/elements/analytics/column-limit.png" width="129" alt="Столбец Лимит в процентных пунктах"> | **Лимит, п.п.** оценивает расход квоты проекта или диалога. Например, доля 64% от общего расхода 25 п.п. дает проекту около 16 п.п. Для Antigravity оценка приводится по отдельным пулам, когда есть замеры. |
| <img src="docs/images/elements/analytics/column-activity.png" width="214" alt="Столбец Последняя активность"> | **Последняя активность** показывает время последней записи в локальной истории. Для проекта берется самая поздняя запись среди диалогов в его ветке. |
| <img src="docs/images/elements/analytics/overview-pinned-mark.png" width="27" alt="Звезда закрепления"> | **★** отмечает закрепленный диалог. |
| <img src="docs/images/elements/analytics/overview-subagent-mark.png" width="54" alt="Число субагентов у диалога"> | **(+2)** означает двух субагентов. |

#### Треды и действия с диалогами

<img src="docs/images/elements/analytics/tab-threads.png" width="172" alt="Вкладка Треды">

В списке есть отдельные диалоги и объединенные задачи. **Модели**, **Токены**, **Стоимость**, **Доля** и **Лимит, п.п.** читаются так же, как в **Обзоре**.

| Фрагмент | Что показывает или делает |
| --- | --- |
| <img src="docs/images/elements/analytics/column-thread.png" width="274" alt="Столбец Тред"> | **Тред** показывает название диалога или объединенной задачи. |
| <img src="docs/images/elements/analytics/column-project.png" width="168" alt="Столбец Проект"> | **Проект** показывает, к какому проекту относится диалог. |
| <img src="docs/images/elements/analytics/column-subagents.png" width="110" alt="Столбец Субагенты"> | **Субагенты** показывает их число. Для диалогов без субагентов ячейка пустая. |
| <img src="docs/images/elements/analytics/column-thread-start.png" width="172" alt="Столбец Начало"> | **Начало** показывает время первой зарегистрированной активности. |
| <img src="docs/images/elements/analytics/column-thread-end.png" width="172" alt="Столбец Конец"> | **Конец** показывает время последней зарегистрированной активности. |

Щелкните по диалогу правой кнопкой в **Обзоре** или **Тредах**, чтобы открыть меню.

| Фрагмент | Что показывает или делает |
| --- | --- |
| <img src="docs/images/elements/analytics/menu-thread-rename.png" width="192" alt="Команда Переименовать"> | **Переименовать** задает свое имя для одного диалога. |
| <img src="docs/images/elements/analytics/menu-thread-hide.png" width="192" alt="Команда Скрыть"> | **Скрыть** убирает выделенные диалоги из обычного расчета. |
| <img src="docs/images/elements/analytics/menu-thread-show.png" width="192" alt="Команда Показать"> | **Показать** возвращает скрытые диалоги. Чтобы добраться до команды, включите **Показывать скрытые** и выделите нужные строки. |
| <img src="docs/images/elements/analytics/menu-thread-pin.png" width="192" alt="Команда Закрепить"> | **Закрепить** отмечает выделенные диалоги звездочкой. |
| <img src="docs/images/elements/analytics/menu-thread-unpin.png" width="192" alt="Команда Открепить"> | **Открепить** снимает отметку. |
| <img src="docs/images/elements/analytics/menu-thread-group.png" width="192" alt="Команда Объединить в задачу"> | **Объединить в задачу** собирает выделенные диалоги в одну строку с общим названием. |
| <img src="docs/images/elements/analytics/menu-thread-ungroup.png" width="192" alt="Команда Снять объединение"> | **Снять объединение** возвращает отдельные строки. |
| <img src="docs/images/elements/analytics/menu-thread-open-detail.png" width="192" alt="Команда Открыть детализацию"> | **Открыть детализацию** показывает сведения о выбранном диалоге. Туда же ведет двойной щелчок по строке. |

| Фрагмент | Что показывает или делает |
| --- | --- |
| <img src="docs/images/elements/analytics/thread-field-agent.png" width="296" alt="Поле Агент в детализации"> | **Агент** указывает, из чьей истории взят диалог. В этом же окне собраны его показатели из **Обзора** и **Тредов**. |
| <img src="docs/images/elements/analytics/thread-detail-time.png" width="460" alt="Таблица Динамика внутри выбранного периода"> | **Динамика внутри выбранного периода** разбивает токены и условную стоимость только этого диалога по интервалам. |
| <img src="docs/images/elements/analytics/thread-detail-close.png" width="76" alt="Кнопка Close"> | **Close** (Закрыть) возвращает в основное окно. |

#### Проекты

<img src="docs/images/elements/analytics/tab-projects.png" width="172" alt="Вкладка Проекты">

Каждая строка суммирует диалоги одного проекта. Повторяющиеся столбцы читаются так же, как в **Обзоре**.

<img src="docs/images/elements/analytics/project-model-menu.png" width="147" alt="Команда Показать треды">

Чтобы открыть диалоги проекта или модели, щелкните правой кнопкой по его названию и выберите **Показать треды**.

#### По времени

<img src="docs/images/elements/analytics/tab-timeline.png" width="172" alt="Вкладка По времени">

<img src="docs/images/elements/analytics/timeline-chart.png" width="910" alt="График расхода токенов по часам">

Высота столбца на графике соответствует числу токенов за интервал.

| Фрагмент | Что показывает или делает |
| --- | --- |
| <img src="docs/images/elements/analytics/column-interval.png" width="246" alt="Столбец Интервал"> | **Интервал** показывает начало часа для периода до 48 часов включительно. Для более длинного периода показывает дату. |
| <img src="docs/images/elements/analytics/column-interval-cost.png" width="135" alt="Столбец Стоимость по времени"> | **Стоимость** распределяется по интервалам пропорционально токенам диалогов и дает оценку. |

#### Модели

<img src="docs/images/elements/analytics/tab-models.png" width="172" alt="Вкладка Модели">

| Фрагмент | Что показывает или делает |
| --- | --- |
| <img src="docs/images/elements/analytics/column-model.png" width="211" alt="Столбец Модель"> | **Модель** показывает название модели из истории. |
| <img src="docs/images/elements/analytics/column-input.png" width="127" alt="Столбец Ввод"> | **Ввод** показывает токены запросов. |
| <img src="docs/images/elements/analytics/column-output.png" width="111" alt="Столбец Вывод"> | **Вывод** показывает токены ответов. |
| <img src="docs/images/elements/analytics/column-cache-written.png" width="132" alt="Столбец Кэш создан"> | **Кэш создан** показывает токены, записанные в кэш. |
| <img src="docs/images/elements/analytics/column-cache-read.png" width="144" alt="Столбец Кэш прочитан"> | **Кэш прочитан** показывает токены, повторно прочитанные из кэша. |
| <img src="docs/images/elements/analytics/column-model-limit.png" width="92" alt="Столбец Лимит модели"> | **Лимит** показывает использованный процент текущего окна модели Antigravity. |
| <img src="docs/images/elements/analytics/column-model-pp.png" width="122" alt="Столбец П.п. периода"> | **П.п. периода** дает оценку расхода окна модели Antigravity по замерам за выбранный период. |
| <img src="docs/images/elements/analytics/column-model-reset.png" width="177" alt="Столбец Сброс"> | **Сброс** показывает время обновления окна модели Antigravity по местным часам. |

Поля окна Antigravity заполняются при наличии данных по конкретной модели.

### Экспорт отчета

Нажмите **Экспорт**, укажите имя файла и выберите формат.

| Фрагмент | Что показывает или делает |
| --- | --- |
| <img src="docs/images/elements/analytics/export-excel.png" width="162" alt="Формат Excel"> | **Excel (.xlsx)** сохраняет сводку и листы по диалогам, проектам, времени и моделям. |
| <img src="docs/images/elements/analytics/export-html.png" width="162" alt="Формат HTML"> | **HTML (.html)** (HyperText Markup Language, язык разметки веб-страниц) создает страницу с карточками и таблицами. |
| <img src="docs/images/elements/analytics/export-json.png" width="162" alt="Формат JSON"> | **JSON (.json)** (JavaScript Object Notation, формат структурированных данных) сохраняет данные для последующей обработки, включая окна квот. |
| <img src="docs/images/elements/analytics/export-csv.png" width="162" alt="Формат CSV"> | **CSV (.csv)** (Comma-Separated Values, формат значений с разделителями) выгружает список тредов для электронной таблицы. |

## Подробно по лимитам

В меню значка рядом с часами Windows выберите **Подробно по лимитам**. Окно показывает остатки квот, отдельные пулы и группы.

| Фрагмент | Что показывает или делает |
| --- | --- |
| <img src="docs/images/elements/analytics/limits-polling-caption.png" width="357" alt="Подпись с интервалом опроса"> | Под заголовком указан интервал опроса Codex и Antigravity. |
| <img src="docs/images/elements/analytics/limits-refresh.png" width="104" alt="Кнопка Обновить"> | **Обновить** запрашивает свежие данные. |
| <img src="docs/images/elements/analytics/limits-open-settings.png" width="109" alt="Кнопка Настройки"> | **Настройки** открывает выбор строк, интервала и положения окна. |
| <img src="docs/images/elements/analytics/limits-column-agent.png" width="106" alt="Колонка Агент / модель"> | **Агент / модель** показывает название агента, модели или группы. Подписка видна в заголовке секции, если источник ее передал. |
| <img src="docs/images/elements/analytics/limits-column-short.png" width="118" alt="Колонка Короткий / день"><br><img src="docs/images/elements/analytics/limits-gauge-short.png" width="208" alt="Пятичасовое окно с остатком и временем сброса"> | **Короткий / день** показывает короткое окно, например 5 часов. |
| <img src="docs/images/elements/analytics/limits-column-long.png" width="116" alt="Колонка Долгий / неделя"><br><img src="docs/images/elements/analytics/limits-gauge-long.png" width="209" alt="Недельное окно с остатком и датой сброса"> | **Долгий / неделя** показывает длинное окно, например неделю. |
| <img src="docs/images/elements/analytics/limits-gauge-missing.png" width="205" alt="Ячейка без окна"> | **Нет окна** означает, что для этого окна нет данных. |
| <img src="docs/images/elements/analytics/limits-gauge-no-reset.png" width="209" alt="Остаток без срока сброса"> | **Сброс не указан** означает, что остаток известен, а источник не передал срок сброса. |

Полоса показывает остаток. Она зеленая выше 45%, желтая выше 20% и до 45% включительно, красная при 20% или меньше. Число округляется до целого. 0% означает, что лимит исчерпан.

### Состояния источника

| Фрагмент | Что показывает или делает |
| --- | --- |
| <img src="docs/images/elements/analytics/limits-source-live.png" width="32" alt="Индикатор LIVE"> | **LIVE** означает, что источник считается доступным. |
| <img src="docs/images/elements/analytics/limits-source-cached.png" width="116" alt="Индикатор КЭШ с датой снимка"> | **КЭШ** обозначает сохраненный снимок. Рядом показаны дата и время замера. |
| <img src="docs/images/elements/analytics/limits-source-local.png" width="273" alt="Индикатор ЛОКАЛЬНЫЕ ДАННЫЕ"> | **ЛОКАЛЬНЫЕ ДАННЫЕ** указывает запасной источник на компьютере. |
| <img src="docs/images/elements/analytics/limits-source-empty.png" width="95" alt="Индикатор НЕТ ДАННЫХ"> | **НЕТ ДАННЫХ** означает, что снимка квоты нет. |

### Окна и пулы агентов

| Фрагмент | Что показывает или делает |
| --- | --- |
| <img src="docs/images/elements/analytics/limits-pool-claude.png" width="668" alt="Короткое и недельное окна Claude"> | **Claude** показывает основное пятичасовое и недельное окна. В примере доступно 64% и 29% соответственно. |
| <img src="docs/images/elements/analytics/limits-pool-fable.png" width="668" alt="Отдельная недельная квота Fable"> | **Fable** в примере занимает дополнительное недельное окно с остатком 88%. |
| <img src="docs/images/elements/analytics/limits-pool-codex.png" width="668" alt="Короткое и недельное окна Codex"> | **Codex** показывает основные короткое и недельное окна. |
| <img src="docs/images/elements/analytics/limits-pool-spark-short.png" width="668" alt="Короткое окно Codex Spark"> | **Codex Spark** может иметь отдельное короткое окно. В примере доступно 79%. |
| <img src="docs/images/elements/analytics/limits-pool-spark-weekly.png" width="668" alt="Недельное окно Codex Spark"> | Недельный **Codex Spark** отображается на отдельной строке. В примере доступно 92%. |
| <img src="docs/images/elements/analytics/limits-pool-grok.png" width="668" alt="Недельное окно Grok"> | **Grok** показывает общее недельное окно. В примере с резервными локальными данными доступно 62%. |
| <img src="docs/images/elements/analytics/limits-group-gemini.png" width="668" alt="Группа Gemini Models"> | При групповых данных Antigravity объединяет окна в одной строке. У **Gemini Models** на снимке осталось 67% пятичасовой и 28% недельной квоты. |
| <img src="docs/images/elements/analytics/limits-group-claude-gpt.png" width="668" alt="Группа Claude and GPT models"> | **Claude and GPT models** показывает остатки другой группы. В примере 83% пятичасовой и 61% недельной квоты. |
| <img src="docs/images/elements/analytics/limits-pool-antigravity-model.png" width="668" alt="Отдельный модельный пул Antigravity"> | При данных по отдельным моделям **Модельный пул** показывает квоту конкретной модели и известный срок сброса. |
| <img src="docs/images/elements/analytics/limits-pool-antigravity-console.png" width="668" alt="Консольный лимит Antigravity"> | **Консольный лимит** появляется отдельной строкой, если источник передал общую квоту вместо групп или отдельных моделей. В примере время сброса не указано. |

### Кредиты ручного сброса

<img src="docs/images/elements/analytics/limits-claude-credits.png" width="668" alt="Два кредита ручного сброса Claude с датами">

**Кредиты ручного сброса** показывают количество, известные сроки по каждому кредиту, источник и актуальность. **0 доступно** означает отсутствие кредитов. **Нет данных** означает, что источник не сообщил число. Если известна только часть сроков, выводится число кредитов без даты и ближайший известный срок. Наведите указатель, чтобы увидеть подсказку. При сроке меньше трех суток выделение желтое. При сроке меньше 24 часов красное.

<img src="docs/images/elements/analytics/limits-codex-credits.png" width="668" alt="Кредит ручного сброса Codex с выделенным срочным сроком">

Такая же панель доступна у Codex, когда есть сведения о кредитах.

### Строка состояния

| Фрагмент | Что показывает или делает |
| --- | --- |
| <img src="docs/images/elements/analytics/limits-footer-counts.png" width="363" alt="Время снимка и счетчики источников"> | В строке видны время последнего снимка, число строк, локальных и сохраненных источников. **live 2/4** означает два доступных источника из четырех. |
| <img src="docs/images/elements/analytics/limits-footer-version.png" width="89" alt="Версия окна подробных лимитов"> | Справа указана версия окна подробных лимитов. |

### Настройки подробных лимитов

| Фрагмент | Что показывает или делает |
| --- | --- |
| <img src="docs/images/elements/analytics/limits-settings-polling.png" width="532" alt="Поле Опрос Codex и Antigravity"><br><img src="docs/images/elements/analytics/limits-settings-interval.png" width="289" alt="Варианты интервала опроса"> | **Опрос Codex и Antigravity** предлагает 15 или 30 секунд, 1, 2 или 5 минут. |
| <img src="docs/images/elements/analytics/limits-settings-topmost.png" width="199" alt="Флажок Поверх остальных окон"> | **Поверх остальных окон** удерживает это окно поверх других. |
| <img src="docs/images/elements/analytics/limits-settings-agent-box.png" width="185" alt="Флажок агента"> | Флажок агента переключает все его строки в списке **Агент / модель**. |
| <img src="docs/images/elements/analytics/limits-settings-pool-box.png" width="185" alt="Флажок отдельного пула"> | Флажок внутри группы переключает отдельное окно или пул. Включите нужные строки дополнительных пулов Claude и Codex, чтобы видеть их в окне. |
| <img src="docs/images/elements/analytics/limits-settings-show-all.png" width="133" alt="Кнопка Показать все"> | **Показать все** включает все строки. |
| <img src="docs/images/elements/analytics/limits-settings-hide-all.png" width="118" alt="Кнопка Скрыть все"> | **Скрыть все** выключает все строки. |
| <img src="docs/images/elements/analytics/limits-settings-apply.png" width="70" alt="Кнопка Save"> | **Save** (Сохранить) применяет настройки. |
| <img src="docs/images/elements/analytics/limits-settings-cancel.png" width="83" alt="Кнопка Cancel"> | **Cancel** (Отмена) закрывает окно без применения. |

## Встроенная справка

<img src="docs/images/elements/connections/menu-help.png" width="355" alt="Пункт меню Справка и клавиша F1">

Нажмите **F1** в активном виджете или выберите **Меню → Справка**. Справка доступна без интернета и открывается даже без подключенных клиентов.

| Вкладка | Что в ней |
| --- | --- |
| <img src="docs/images/elements/connections/help-tab-welcome.png" width="142" alt="Вкладка Начало работы"> | Установка виджета и подключение первого клиента. |
| <img src="docs/images/elements/connections/help-tab-sources.png" width="107" alt="Вкладка Источники"> | Откуда берутся лимиты каждого клиента и когда нужна строка состояния. |
| <img src="docs/images/elements/connections/help-tab-widget.png" width="82" alt="Вкладка Виджет"> | Проценты остатка, время сброса, стрелки, прогноз и управление окном. |
| <img src="docs/images/elements/connections/help-tab-history.png" width="90" alt="Вкладка История"> | Расход квоты, данные аналитики и папка с настройками и измерениями. |
| <img src="docs/images/elements/connections/help-tab-no-data.png" width="112" alt="Вкладка Нет данных"> | Как отличить пустое значение от нуля и где проверить пути и вход. |

<img src="docs/images/elements/connections/help-project.png" width="150" alt="Кнопка GitHub в справке">

**GitHub** открывает исходный проект в браузере.

<img src="docs/images/elements/connections/help-close.png" width="106" alt="Кнопка Закрыть в справке">

**Закрыть** возвращает в виджет.

## Данные и лицензия

Настройки и измерения хранятся в `%LOCALAPPDATA%\ClaudeUsageManager`. Историю диалогов виджет читает из выбранных локальных профилей. Вход и хранение учетных данных выполняет официальный клиент агента.

[Лицензия](LICENSE) объединяет Apache 2.0 и Commons Clause 1.0. Можно пользоваться программой, делать форки, менять код и распространять сборки бесплатно. При распространении сохраняйте лицензию и [NOTICE](NOTICE) с псевдонимом автора и ссылкой на исходный проект. Для продажи программы или ее перепаковки, подпадающей под условия лицензии, нужно отдельное разрешение. Условия платного распространения можно согласовать через [GitHub](https://github.com/antonnevm-cloud).

[Сборка из исходников для Windows](packaging/README.md#windows-x64) · [Лицензии компонентов](packaging/licenses/README.md)
