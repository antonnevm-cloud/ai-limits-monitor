"""Entry point for the unified AI Limits Monitor application."""
# Compatibility note for tests/docs: the legacy entry point used "quota_monitor import main".
import sys


def _use_parent_console():
    """Показать вывод служебного режима в терминале, из которого запущена программа.

    Программа для Windows собрана без собственной консоли, иначе виджет открывал бы окно терминала.
    Потоки, которые передал вызывающий процесс, остаются как есть.
    """
    if sys.platform != "win32" or sys.stdout is not None:
        return
    import ctypes

    if not ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
        return
    try:
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
    except OSError:
        return
    if sys.stderr is None:
        sys.stderr = sys.stdout


def _run(args):
    if args == ["--version"]:
        _use_parent_console()
        from claude_usage_manager import APP_NAME, APP_VERSION
        print(f"{APP_NAME} {APP_VERSION}")
        return 0
    if args == ["--smoke-test"]:
        _use_parent_console()
        from claude_usage_manager.smoke import run
    elif args[:1] == ["--claude-statusline"]:
        from claude_usage_manager.claude_local import statusline_main as run
        return run(args[1:])
    elif args[:1] == ["--antigravity-statusline"]:
        from claude_usage_manager.antigravity_quota import antigravity_statusline_main
        return antigravity_statusline_main(args[1:])
    elif args == ["--backend"]:
        from claude_usage_manager.backend import main as run
    else:
        from claude_usage_manager.unified_monitor import main as run
    return run()


def main():
    try:
        return _run(sys.argv[1:])
    except Exception:
        # Программа без консоли показала бы окно с ошибкой, в том числе при каждом вызове строки состояния.
        if sys.stderr is not None:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
