# Сборка AI Limits Monitor

Windows 10 версии 1809 и новее и Windows 11 используют установщик x64. Запусти `AI-Limits-Monitor-2.0.2-windows-x64-setup.exe`. Он устанавливает программу в `%LOCALAPPDATA%\Programs\AI Limits Monitor` и создает ярлык в меню "Пуск". Ярлык на рабочем столе и запуск при входе в Windows включаются по желанию. Удалить программу можно в разделе установленных приложений.

На macOS 27 с процессором Apple Silicon распакуй `AI-Limits-Monitor-<версия>-macos-arm64.zip` и перенеси `AI Limits Monitor.app` в "Программы". Сборка использует локальную подпись. При первом запуске macOS может попросить подтвердить открытие приложения. [Открытие приложений на macOS](https://support.apple.com/ru-ru/102445).

Python, Qt и нужные библиотеки входят в дистрибутив. Лицензии находятся в `_internal/licenses` на Windows и в `Contents/Resources/licenses` внутри приложения на macOS.

## Сборка из исходного кода

Команды ниже запускаются из корня репозитория. В отдельном окружении установи зависимости из `requirements-build.txt`. В автоматической сборке используются Python 3.12.10 и Inno Setup 6.7.4. [Загрузка Inno Setup](https://github.com/jrsoftware/issrc/releases/tag/is-6_7_4).

### Windows x64

```powershell
python -m venv .pytest_tmp\build-python
$python = '.\.pytest_tmp\build-python\Scripts\python.exe'
& $python -m pip install --only-binary=:all: -r requirements-build.txt
if ($LASTEXITCODE) { throw 'Установка зависимостей прервана' }
.\packaging\build-installer.ps1 -Python $python
$setup = Get-ChildItem -LiteralPath release -Filter '*-windows-x64-setup.exe' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
& $python packaging/check_distribution.py $setup.FullName
```

Параметр `-Compiler` задает путь к `ISCC.exe`, если Inno Setup лежит отдельно. `-OutputDirectory` задает каталог готовых файлов, `-WorkDirectory` задает рабочий каталог. Проверка запускает настоящий установщик с `/PORTABLE=1` в отдельном временном каталоге. Для переносимой установки передай установщику `/PORTABLE=1` и `/DIR` с полным путем к выбранной папке.

Чтобы проверить уже опубликованный установщик на чистой машине GitHub, запусти сборочный процесс для коммита выпуска и укажи его метку в поле `windows_release`. Задание Windows сверит контрольную сумму файла, запустит переносимую и обычную установку и проверит появление виджета и окна подключений при первом запуске.

### macOS

```bash
set -e
python3.12 -m venv .pytest_tmp/build-python
python=.pytest_tmp/build-python/bin/python
"$python" -m pip install --only-binary=:all: -r requirements-build.txt
"$python" packaging/build.py
"$python" packaging/check_distribution.py release/*-macos-"$(uname -m)".zip
```

Сборка сохраняет приложение с динамическими библиотеками в архив ZIP. Сценарий GitHub Actions (автоматической сборки) `distributions.yml` работает вручную или по метке версии `v*`. Он собирает Windows x64 и macOS 27 на Apple Silicon, устанавливает или распаковывает каждый файл и проверяет версию, графические компоненты и обмен со строкой состояния Claude Code. Готовые файлы появляются в результатах запуска. [Исполнитель xcode-27](https://github.blog/changelog/2026-10-15-xcode-27-runner-image-generally-available/).
