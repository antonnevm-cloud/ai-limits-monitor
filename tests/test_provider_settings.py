import json
import os
import plistlib
from dataclasses import replace
from pathlib import Path

import pytest

from claude_usage_manager import provider_settings as settings


@pytest.fixture
def empty_user(tmp_path, monkeypatch):
    for key in (*settings.PROFILE_VARIABLES.values(), "GROK_BIN_DIR", "CODEX_INSTALL_DIR", "NPM_CONFIG_PREFIX"):
        monkeypatch.delenv(key, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    can_run = settings._can_run
    monkeypatch.setattr(settings, "_can_run", lambda path: path.is_relative_to(tmp_path) and can_run(path))
    for key in ("APPDATA", "LOCALAPPDATA", "ProgramFiles", "PATH", "AI_LIMITS_DATA_DIR"):
        monkeypatch.setenv(key, str(tmp_path / key))
    return home


def _program(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    path.chmod(0o755)
    return path


def test_empty_settings_and_native_profiles(empty_user, monkeypatch, tmp_path):
    defaults = settings.load_connections()
    assert set(defaults) == {"claude", "codex", "grok", "antigravity"}
    assert all(settings.provider_enabled(key) for key in defaults)
    assert all(settings.provider_config_key(key) == "" for key in defaults)
    for key, variable in settings.PROFILE_VARIABLES.items():
        assert settings.provider_profile(key) == empty_user / ("." + key)
        monkeypatch.setenv(variable, str(tmp_path / key))
        assert settings.provider_profile(key) == tmp_path / key
    assert settings.provider_profile("antigravity") == empty_user / ".gemini/antigravity"
    assert settings.antigravity_cli_profile() == empty_user / ".gemini/antigravity-cli"
    assert not (settings.utils.data_dir() / "connections.json").exists()


def test_connection_round_trip_and_profile_identity(empty_user, tmp_path):
    connections = settings.load_connections()
    executable = _program(tmp_path / "bin" / settings.binary_name("codex"))
    profile = tmp_path / "account-a"
    settings.save_connections(connections | {"codex": settings.Connection(False, str(executable), str(profile))})
    selected = settings.load_connections()["codex"]
    assert not settings.provider_enabled("codex")
    assert settings.provider_profile("codex") == profile
    assert settings.provider_executable("codex") == executable
    assert settings.provider_environment("codex")["CODEX_HOME"] == str(profile)
    assert os.environ.get("CODEX_HOME") is None
    key = settings.provider_config_key("codex")
    assert key and str(profile) not in key
    normalized = replace(selected, enabled=True, profile=str(tmp_path / "other" / ".." / "account-a"))
    settings.save_connections(connections | {"codex": normalized})
    assert settings.provider_config_key("codex") == key
    settings.save_connections(connections | {"codex": replace(selected, profile=str(tmp_path / "account-b"))})
    assert settings.provider_config_key("codex") != key
    settings.save_connections(connections)
    assert settings.provider_config_key("codex") == ""


@pytest.mark.parametrize("provider,variable", settings.PROFILE_VARIABLES.items())
def test_native_profile_variable_separates_accounts(empty_user, tmp_path, monkeypatch, provider, variable):
    standard = settings.provider_profile(provider)
    monkeypatch.setenv(variable, str(standard.parent / "alias" / ".." / standard.name))
    assert settings.provider_config_key(provider) == ""
    first, second = tmp_path / "account-one", tmp_path / "account-two"
    monkeypatch.setenv(variable, str(first))
    first_key = settings.provider_config_key(provider)
    assert first_key
    monkeypatch.setenv(variable, str(second))
    assert first_key != settings.provider_config_key(provider)
    connections = settings.load_connections()
    settings.save_connections(connections | {provider: settings.Connection(profile=str(first))})
    assert settings.provider_config_key(provider) == first_key


def test_profile_selection_does_not_implicitly_install_a_client(empty_user, tmp_path, monkeypatch):
    profile = tmp_path / "downloaded"
    name = settings.binary_name("grok")
    _program(profile / "bin" / name)
    monkeypatch.setenv("GROK_HOME", str(profile))
    assert settings.provider_executable("grok") is None
    installed = _program(tmp_path / "tools" / name)
    monkeypatch.setenv("GROK_BIN_DIR", str(installed.parent))
    assert settings.provider_executable("grok") == installed
    assert settings.provider_executable("grok", settings.Connection(executable=str(tmp_path / name))) is None
    assert settings.provider_executable("grok", settings.Connection(executable=str(_program(tmp_path / "other.cmd")))) is None


@pytest.mark.parametrize("source", ["PATH", "NPM_CONFIG_PREFIX"])
def test_windows_clients_in_custom_global_npm_folder(empty_user, tmp_path, monkeypatch, source):
    monkeypatch.setattr(settings.sys, "platform", "win32")
    prefix = tmp_path / "Инструменты npm"
    modules = prefix / "node_modules"
    claude = _program(modules / "@anthropic-ai/claude-code/bin/claude.exe")
    codex = _program(modules / "@openai/codex/node_modules/@openai/codex-win32-x64"
                     / "vendor/x86_64-pc-windows-msvc/codex/codex.exe")
    if source == "PATH":
        _program(prefix / "claude.cmd")
        _program(prefix / "codex.cmd")
    monkeypatch.setenv(source, str(prefix))
    assert settings.provider_executable("claude") == claude
    assert settings.provider_executable("codex") == codex


def test_paths_checked_before_writing_and_failed_replace_is_atomic(empty_user, tmp_path, monkeypatch):
    original = settings.load_connections()
    settings.save_connections(original)
    path = settings.utils.data_dir() / "connections.json"
    body = path.read_bytes()
    with pytest.raises(ValueError):
        settings.save_connections(original | {"claude": settings.Connection(profile="relative")})
    with pytest.raises(ValueError):
        settings.save_connections(original | {"codex": settings.Connection(executable=str(tmp_path / "cmd.exe"))})

    def fail(source, destination):
        assert Path(source).parent == Path(destination).parent
        assert json.loads(Path(source).read_text(encoding="utf-8"))["codex"]["enabled"] is False
        raise PermissionError

    monkeypatch.setattr(settings.os, "replace", fail)
    with pytest.raises(PermissionError):
        settings.save_connections(original | {"codex": settings.Connection(enabled=False)})
    assert path.read_bytes() == body
    assert list(path.parent.iterdir()) == [path]


def test_corrupt_file_disables_automatic_clients(empty_user):
    (settings.utils.data_dir() / "connections.json").write_text("not valid", encoding="utf-8")
    assert all(not value.enabled for value in settings.load_connections().values())
    with pytest.raises(ValueError, match="connections.json"):
        settings.load_connections(strict=True)


def test_antigravity_windows_and_mac_bundle_lookup(empty_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings.sys, "platform", "win32")
    local = Path(os.environ["LOCALAPPDATA"])
    expected = _program(local / "Programs/Antigravity/Antigravity.exe")
    agy = _program(local / "agy/bin/agy.exe")
    assert settings.antigravity_app() == expected
    assert settings.provider_executable("antigravity") == agy
    custom = settings.Connection(profile=str(tmp_path / "alternate/antigravity"))
    assert settings.antigravity_cli_profile(custom) == tmp_path / "alternate/antigravity-cli"
    assert settings.antigravity_cli_profile(replace(custom, cli_profile=str(tmp_path / "cli"))) == tmp_path / "cli"

    monkeypatch.setattr(settings.sys, "platform", "darwin")
    app = empty_user / "Applications/Antigravity.app"
    _program(app / "Contents/MacOS/Antigravity")
    with (app / "Contents/Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleExecutable": "Antigravity"}, stream)
    assert settings.antigravity_app() == app
    assert settings.antigravity_app(replace(custom, application=str(tmp_path / "missing.app"))) is None
    mac_agy = _program(empty_user / ".local/bin/agy")
    assert settings.provider_executable("antigravity") == mac_agy
