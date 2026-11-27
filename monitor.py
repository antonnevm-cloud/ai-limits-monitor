"""Entry point for the unified AI Limits Monitor application."""
# Compatibility note for tests/docs: the legacy entry point used "quota_monitor import main".
import sys


def main():
    args = sys.argv[1:]
    if args == ["--version"]:
        from claude_usage_manager import APP_NAME, APP_VERSION
        print(f"{APP_NAME} {APP_VERSION}")
        return 0
    if args == ["--smoke-test"]:
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

if __name__ == "__main__":
    raise SystemExit(main())
