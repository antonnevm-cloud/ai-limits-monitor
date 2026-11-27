"""Проверка установленного приложения на изолированном профиле без входа."""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import tempfile


def run() -> int:
    from PySide6.QtWidgets import QApplication
    with tempfile.TemporaryDirectory(prefix="ai-limits-check-") as directory:
        root = Path(directory)
        os.environ.update({
            "HOME": directory, "USERPROFILE": directory,
            "APPDATA": directory, "LOCALAPPDATA": directory,
            "AI_LIMITS_DATA_DIR": str(root / "monitor"),
            "CLAUDE_CONFIG_DIR": str(root / ".claude"),
            "CODEX_HOME": str(root / ".codex"), "GROK_HOME": str(root / ".grok"),
            "QT_QPA_PLATFORM": "offscreen",
        })
        from .backend import BackendService
        from .connections_dialog import ConnectionsDialog
        from .database import Database
        from .provider_settings import load_connections, save_connections
        from .quota_monitor_model import PROVIDERS
        from .unified_monitor import UnifiedMonitorWindow
        from .utils import get_logger

        app = QApplication.instance() or QApplication([])
        settings = load_connections()
        expected = {"claude", "codex", "grok", "antigravity"}
        if set(settings) != expected or set(PROVIDERS) != expected:
            raise RuntimeError("Неверный набор подключений")
        save_connections({name: replace(connection, enabled=False) for name, connection in settings.items()})
        db = Database()
        service = BackendService(serve_http=False)
        try:
            service.start()
            window = UnifiedMonitorWindow(db, auto_refresh=False, collector=service)
            dialog = ConnectionsDialog(window)
            app.processEvents()
            if set(service.states()) != expected or any(state.live for state in service.states().values()):
                raise RuntimeError("Неверное состояние отключенных источников")
            dialog.reject()
            window.hide()
        finally:
            service.stop()
            db.close()
            logger = get_logger()
            for handler in logger.handlers[:]:
                logger.removeHandler(handler)
                handler.close()
    print("AI Limits Monitor: smoke OK")
    return 0
