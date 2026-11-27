"""Local Claude Code Mod quota snapshot; never contacts Anthropic.

The installed AI Limits Local Bridge plugin writes a constrained JSON file
from Anthropic's documented session.measure hook. It uses the same schema as
the documented Claude Code statusLine. Reuse its bounded validator, preserving
both rate-limit reset timestamps and zero-percent usage.
"""
from __future__ import annotations

import datetime as dt
import pathlib

from .claude_local import read_statusline_quota
from .models import QuotaSnapshot
from .utils import data_dir

SOURCE = "claude-code:official-mod"
DATA_FILE = "claude-mod-quota-v1.json"


def mod_snapshot_path() -> pathlib.Path:
    return data_dir() / DATA_FILE


def read_mod_quota(
    *, path: pathlib.Path | None = None, now: dt.datetime | None = None,
) -> QuotaSnapshot:
    snapshot = read_statusline_quota(path=path or mod_snapshot_path(), now=now)
    snapshot.source = SOURCE
    snapshot.provider = "claude"
    snapshot.login_method = "Claude Code · Mods (локально)"
    return snapshot
