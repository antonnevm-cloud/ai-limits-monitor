# Компоненты AI Limits Monitor

Код AI Limits Monitor распространяется по Apache License 2.0 с условием Commons Clause 1.0. В этой папке есть полный текст `AI-Limits-Monitor-LICENSE.txt` и уведомление об авторстве и исходном проекте `AI-Limits-Monitor-NOTICE.txt`. Сторонние компоненты распространяются на собственных условиях.

Сборка включает интерпретатор Python 3.12, библиотеки Qt 6.11.1, PySide6-Essentials и shiboken6, Pillow для изображений, openpyxl и et_xmlfile для таблиц. Программа загружается через PyInstaller. Установщик Windows создан с помощью Inno Setup. В этом каталоге рядом лежат лицензии из поставок соответствующих версий. В `Qt-third-party.txt` собраны лицензии сторонних компонентов Qt Base, Qt SVG и Qt Image Formats по перечням из исходного выпуска Qt 6.11.1.

`Python.txt` содержит лицензию Python, условия для распространяемых файлов Microsoft Visual C++ (VCRUNTIME140, MSVCP140) и лицензии bzip2, libffi, OpenSSL (Apache 2.0) и Tcl/Tk. `Python-incorporated-software.txt` содержит лицензии встроенного в Python кода: expat (pyexpat), libmpdec (_decimal), zlib, HACL* (хэш-функции python312.dll) и других компонентов. SQLite передан в общественное достояние. `libaom.txt` содержит лицензию и патентную лицензию кодека AV1 из модуля AVIF библиотеки Pillow.

Qt, PySide6 и shiboken6 поставляются как отдельные динамические библиотеки по GNU Lesser General Public License версии 3. Их можно заменить совместимыми сборками в каталоге установленного приложения. Тексты LGPL и упомянутой в ней GNU General Public License находятся в `LGPL-3.0-only.txt` и `GPL-3.0-only.txt`. Файл `LicenseRef-Qt-Commercial.txt` в папках PySide6-Essentials и shiboken6 взят из их поставки без изменений; программа использует эти библиотеки по LGPL v3, а не по коммерческой лицензии.

Исходный код соответствующих версий библиотек доступен у разработчиков. [Qt 6.11.1](https://download.qt.io/archive/qt/6.11/6.11.1/submodules/) и [Qt for Python 6.11.1](https://github.com/qtproject/pyside-pyside-setup/tree/v6.11.1).

Исходные тексты загрузчика и установщика опубликованы разработчиками. [PyInstaller 6.22.3](https://github.com/pyinstaller/pyinstaller/tree/v6.22.3) и [Inno Setup 6.7.4](https://github.com/jrsoftware/issrc/tree/is-6_7_4).

Значки Claude, Codex, Grok и Antigravity обозначают соответствующие клиенты. Это товарные знаки Anthropic PBC, OpenAI, xAI и Google LLC, они принадлежат правообладателям, и лицензия AI Limits Monitor на них не распространяется.
