import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Фиксируем mtime тестовых журналов далеко в будущем, чтобы оптимизация scan_logs
# по времени модификации не отсекала файлы (в тестах используются будущие даты).
_FUTURE_MTIME = dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc).timestamp()


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.utime(path, (_FUTURE_MTIME, _FUTURE_MTIME))


@pytest.fixture
def make_claude_root(tmp_path):
    """Создаёт временный корень .claude с projects/ и возвращает его путь."""
    root = tmp_path / "claude"
    (root / "projects").mkdir(parents=True)

    def _factory(project: str, filename: str, records: list[dict]) -> Path:
        target = root / "projects" / project / filename
        write_jsonl(target, records)
        return target

    _factory.root = root
    return _factory


@pytest.fixture(scope="session", autouse=True)
def _app_log_in_session_temp(tmp_path_factory):
    """Журнал приложения на время тестов - во временном каталоге сессии, а не в живом app.log.

    Иначе `get_logger()` (его зовут `Database`, сканеры и окна) открывал
    %LOCALAPPDATA%\\ClaudeUsageManager\\app.log работающего виджета, и туда попадали нарочные
    ошибки тестов (CLA-8). Журнал настраивается один раз за процесс.
    Файл создается в отдельном временном каталоге pytest.
    """
    from claude_usage_manager import utils

    assert not utils._LOGGER_READY, "журнал приложения настроен до изоляции тестов и пишет в живой app.log"
    log_file = tmp_path_factory.mktemp("app-log") / "app.log"
    patch = pytest.MonkeyPatch()
    patch.setattr(utils, "log_path", lambda: log_file)
    logger = utils.setup_logging()
    yield log_file
    patch.undo()
    files = [Path(h.baseFilename) for h in logger.handlers if isinstance(h, logging.FileHandler)]
    assert files == [log_file], f"журнал приложения пишет не только во временный каталог: {files}"


@pytest.fixture(autouse=True)
def _destroy_qt_windows_after_test():
    """После теста удаляет все окна Qt, которые он оставил, пока жив QApplication (CLA-8).

    Тесты создают окна (`MonitorWindow`, строки тредов, всплывающие панели) и не удаляют их:
    `quit_monitor()` только прячет окно, и оно живет после теста (`gc.collect()` его не находит,
    ссылки держит сторона C++). Вне цикла
    событий `deleteLater()` не срабатывает и копится до чужого `QTest.qWait`. К концу полного
    прогона оставались десятки окон, их разрушение после QApplication роняло Python при выходе
    с кодом 0xC0000005 при зеленых тестах. Порядок важен: дождаться фоновых задач пула, выполнить
    накопленные отложенные удаления, затем синхронно удалить оставшиеся окна.
    """
    yield
    widgets = sys.modules.get("PySide6.QtWidgets")
    app = widgets.QApplication.instance() if widgets else None
    if app is None:
        return
    import shiboken6
    from PySide6.QtCore import QEvent, QThreadPool

    QThreadPool.globalInstance().waitForDone(5000)
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    for window in app.topLevelWidgets():
        if shiboken6.isValid(window):  # дочернее окно уже удалено вместе с родителем
            shiboken6.delete(window)


@pytest.fixture(autouse=True)
def _isolate_desktop_and_statusline_quota(monkeypatch, tmp_path):
    """Never inspect the user's real Claude usage files during tests."""
    from claude_usage_manager import claude_local, claude_desktop, claude_mod_bridge
    monkeypatch.setenv("AI_LIMITS_DATA_DIR", str(tmp_path / "monitor-data"))

    monkeypatch.setattr(claude_local, "statusline_path", lambda: tmp_path / "no-statusline.json")
    monkeypatch.setattr(claude_mod_bridge, "mod_snapshot_path", lambda: tmp_path / "no-mod.json")
    monkeypatch.setattr(claude_desktop, "desktop_history_paths", lambda: ())


@pytest.fixture(autouse=True)
def _never_start_live_agent_clis(monkeypatch):
    """Тесты не запускают боевые `grok agent stdio` и `codex app-server` (регрессия по ревью CLA-5).

    Без подмены `refresh_states` и `fetch_quota` шли в настоящие CLI владельца: медленно и с побочными
    запросами. Тесты, которым нужен запуск, подменяют `Popen` и пути сами (они сильнее этой фикстуры).
    """
    from claude_usage_manager import antigravity_quota, grok_provider, quota, quota_monitor_model

    def unavailable(*args, **kwargs):
        raise OSError("В тесте нужен имитатор установленного клиента Antigravity")

    monkeypatch.setattr(antigravity_quota, "run_hidden", unavailable)

    monkeypatch.setattr(grok_provider, "grok_cli", lambda: None)
    monkeypatch.setattr(quota, "codex_cli", lambda: None)

    def reset():
        grok_provider.reset_cli_cache()
        quota.reset_codex_rate_limits_cache()
        quota_monitor_model._LAST_LIVE.clear()
        quota_monitor_model._LAST_REASON.clear()

    reset()
    yield
    reset()
