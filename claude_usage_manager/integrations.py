"""Снятие интеграций монитора с клиентов: при отключении строки состояния и при удалении программы."""
from __future__ import annotations

import json
from pathlib import Path

from . import autostart, provider_settings
from .utils import data_dir, get_logger


def _receipt_settings(folder: Path) -> list[Path]:
    paths = []
    for receipt in folder.glob("*.json") if folder.is_dir() else ():
        try:
            value = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and isinstance(value.get("settings"), str):
            paths.append(Path(value["settings"]))
    return paths


def _unique(paths) -> list[Path]:
    seen, result = set(), []
    for path in paths:
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def remove_all() -> dict[str, str]:
    """Вернуть прежние строки состояния Claude Code и agy и снять автозапуск этой копии."""
    from .antigravity_quota import remove_antigravity_statusline
    from .claude_local import remove_statusline_command

    report: dict[str, str] = {}
    connections = provider_settings.load_connections()
    claude = [provider_settings.provider_profile("claude", connections["claude"]) / "settings.json"]
    claude.append(Path.home() / ".claude" / "settings.json")
    claude.extend(_receipt_settings(data_dir() / "claude-hooks"))
    for settings in _unique(claude):
        try:
            report[str(settings)] = remove_statusline_command(settings_path=settings)
        except (OSError, ValueError) as error:
            report[str(settings)] = f"error: {error}"
    agy = [provider_settings.antigravity_cli_profile(connections["antigravity"]) / "settings.json"]
    agy.extend(_receipt_settings(data_dir() / "antigravity-statusline"))
    for settings in _unique(agy):
        try:
            report[str(settings)] = remove_antigravity_statusline(cli_profile=settings.parent)
        except (OSError, ValueError) as error:
            report[str(settings)] = f"error: {error}"
    if autostart.supported():
        try:
            autostart.set_enabled(False)
            report["autostart"] = "off"
        except OSError as error:
            report["autostart"] = f"error: {error}"
    return report


def uninstall_main() -> int:
    """Вызывается деинсталлятором. Ошибки не мешают удалению программы."""
    try:
        for target, result in remove_all().items():
            get_logger().info("Удаление программы: %s -> %s", target, result)
    except Exception:  # noqa: BLE001
        get_logger().exception("Удаление программы: интеграции сняты не полностью")
    return 0
