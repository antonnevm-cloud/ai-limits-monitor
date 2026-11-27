"""SQLite-хранилище: снимки квот, правила тредов, настройки, состояние.

Хранит только производные данные. Тексты переписки (prompt/response/content)
в базу не попадают никогда.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import ManualResetCredits, ManualResetItem, QuotaSnapshot, QuotaWindow
from .provider_settings import provider_config_key
from .utils import database_path, get_logger, parse_iso

SCHEMA_VERSION = 5

_MIN_QUOTA_INTERVAL = dt.timedelta(minutes=10)


class Database:
    """Потокобезопасная обёртка над одним соединением SQLite."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path else database_path()
        self._lock = threading.RLock()
        self._log = get_logger()
        self._conn = sqlite3.connect(
            self._path, check_same_thread=False, timeout=30.0
        )
        self._conn.row_factory = sqlite3.Row
        self._configure()
        self._migrate()

    # ------------------------------------------------------------------ #
    # Инициализация и миграции
    # ------------------------------------------------------------------ #
    def _configure(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.execute("PRAGMA synchronous=NORMAL")

    def _migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                self._create_v1()
                version = 1
            if version < 2:
                # До 3.1.0 Codex primary (10080 минут = неделя) ошибочно
                # сохранялся как five_hour. Новый формат Codex никогда не
                # пишет five_hour, поэтому старые строки распознаются однозначно.
                self._conn.execute(
                    "UPDATE quota_samples SET weekly = five_hour, five_hour = NULL "
                    "WHERE LOWER(source) = 'codexbar:codex' AND five_hour IS NOT NULL"
                )
                version = 2
            if version < 3:
                columns = {row[1] for row in self._conn.execute("PRAGMA table_info(quota_samples)").fetchall()}
                if "windows_json" not in columns:
                    self._conn.execute("ALTER TABLE quota_samples ADD COLUMN windows_json TEXT NOT NULL DEFAULT '[]'")
                if "main_window_id" not in columns:
                    self._conn.execute("ALTER TABLE quota_samples ADD COLUMN main_window_id TEXT NOT NULL DEFAULT ''")
                if "login_method" not in columns:
                    self._conn.execute("ALTER TABLE quota_samples ADD COLUMN login_method TEXT NOT NULL DEFAULT ''")
                version = 3
            if version < 4:
                columns = {row[1] for row in self._conn.execute("PRAGMA table_info(quota_samples)").fetchall()}
                if "manual_resets_json" not in columns:
                    self._conn.execute(
                        "ALTER TABLE quota_samples ADD COLUMN manual_resets_json TEXT NOT NULL DEFAULT '{}'"
                    )
                version = 4
            if version < 5:
                if not self._conn.in_transaction:
                    self._conn.execute("BEGIN IMMEDIATE")
                columns = {row[1] for row in self._conn.execute("PRAGMA table_info(quota_samples)")}
                if "profile_key" not in columns:
                    self._conn.execute("ALTER TABLE quota_samples ADD COLUMN profile_key TEXT NOT NULL DEFAULT ''")
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._conn.commit()
            self._log.info("База готова, версия схемы %s", SCHEMA_VERSION)

    def _create_v1(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS quota_samples (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc        TEXT NOT NULL,
                five_hour     REAL,
                weekly        REAL,
                fable_weekly  REAL,
                source        TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_quota_ts ON quota_samples(ts_utc);

            CREATE TABLE IF NOT EXISTS thread_rules (
                session_id     TEXT PRIMARY KEY,
                title_override TEXT,
                hidden         INTEGER NOT NULL DEFAULT 0,
                pinned         INTEGER NOT NULL DEFAULT 0,
                group_name     TEXT,
                updated_at     TEXT
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS app_state (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
            finally:
                self._conn.close()

    # ------------------------------------------------------------------ #
    # Снимки квот
    # ------------------------------------------------------------------ #
    @staticmethod
    def _windows_json(snapshot: QuotaSnapshot) -> str:
        payload = [{
            "id": window.id, "title": window.title, "used_percent": window.used_percent,
            "resets_at": window.resets_at.astimezone(dt.timezone.utc).isoformat() if window.resets_at else None,
            "window_minutes": window.window_minutes, "role": window.role,
            "informational": window.informational,
            "details": window.details,
        } for window in snapshot.windows]
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decode_windows(raw: str | None) -> list[QuotaWindow]:
        try: payload = json.loads(raw or "[]")
        except (TypeError, json.JSONDecodeError): return []
        out=[]
        for item in payload if isinstance(payload,list) else []:
            if not isinstance(item,dict) or not item.get("id"): continue
            out.append(QuotaWindow(
                id=str(item["id"]), title=str(item.get("title") or item["id"]),
                used_percent=item.get("used_percent"), resets_at=parse_iso(item.get("resets_at")),
                window_minutes=item.get("window_minutes"), role=str(item.get("role") or ""),
                informational=bool(item.get("informational",False)),
                details=item.get("details") if isinstance(item.get("details"),dict) else {},
            ))
        return out

    @staticmethod
    def _manual_resets_json(snapshot: QuotaSnapshot) -> str:
        credits = snapshot.manual_resets
        payload = {
            "provider": credits.provider,
            "state": credits.state,
            "available_count": credits.available_count,
            "source": credits.source,
            "updated_at": credits.updated_at.astimezone(dt.timezone.utc).isoformat()
            if credits.updated_at else None,
            "freshness": credits.freshness,
            "details_complete": credits.details_complete,
            "nearest_expiry_hint": credits.nearest_expiry_hint.astimezone(dt.timezone.utc).isoformat()
            if credits.nearest_expiry_hint else None,
            "error": credits.error,
            "items": [{
                "id": item.id,
                "provider": item.provider,
                "expires_at": item.expires_at.astimezone(dt.timezone.utc).isoformat()
                if item.expires_at else None,
                "granted_at": item.granted_at.astimezone(dt.timezone.utc).isoformat()
                if item.granted_at else None,
                "status": item.status,
                "reset_type": item.reset_type,
                "title": item.title,
                "description": item.description,
            } for item in credits.sorted_items()],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decode_manual_resets(raw: str | None, provider: str = "") -> ManualResetCredits:
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        items: list[ManualResetItem] = []
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            items.append(ManualResetItem(
                id=str(item.get("id") or ""),
                provider=str(item.get("provider") or provider),
                expires_at=parse_iso(item.get("expires_at")),
                granted_at=parse_iso(item.get("granted_at")),
                status=str(item.get("status") or "unknown"),
                reset_type=str(item.get("reset_type") or ""),
                title=str(item.get("title") or ""),
                description=str(item.get("description") or ""),
            ))
        count = payload.get("available_count")
        try:
            count = int(count) if count is not None else None
        except (TypeError, ValueError):
            count = None
        credits = ManualResetCredits(
            provider=str(payload.get("provider") or provider),
            state=str(payload.get("state") or "unknown"),
            available_count=count,
            items=items,
            source=str(payload.get("source") or ""),
            updated_at=parse_iso(payload.get("updated_at")),
            freshness=str(payload.get("freshness") or "unknown"),
            details_complete=bool(payload.get("details_complete", False)),
            nearest_expiry_hint=parse_iso(payload.get("nearest_expiry_hint")),
            error=str(payload.get("error") or ""),
        )
        credits.items = credits.sorted_items()
        return credits

    def record_quota(self, snapshot: QuotaSnapshot, *, profile_key: str | None = None) -> bool:
        """Пишет provider-specific snapshot; arbitrary windows сохраняются JSON-ом."""
        with self._lock:
            source = snapshot.source or (f"unknown:{snapshot.provider}" if snapshot.provider else "")
            if profile_key is None:
                profile_key = provider_config_key(snapshot.provider) if snapshot.provider in ("claude", "codex", "grok", "antigravity") else ""
            windows_json = self._windows_json(snapshot)
            manual_resets_json = self._manual_resets_json(snapshot)
            row = self._conn.execute(
                "SELECT ts_utc, five_hour, weekly, fable_weekly, windows_json, main_window_id, login_method, manual_resets_json "
                "FROM quota_samples WHERE source = ? AND profile_key = ? ORDER BY id DESC LIMIT 1", (source, profile_key),
            ).fetchone()
            now = dt.datetime.now(dt.timezone.utc)
            if row is not None:
                previous_windows = row["windows_json"] or "[]"
                comparison_windows = windows_json
                if source == "grok:local-billing-log":
                    def without_observation(value):
                        try:
                            windows = json.loads(value)
                            for window in windows:
                                if isinstance(window, dict) and isinstance(window.get("details"), dict):
                                    window["details"].pop("observed_at", None)
                            return windows
                        except (ValueError, TypeError):
                            return value
                    previous_windows = without_observation(previous_windows)
                    comparison_windows = without_observation(windows_json)
                same = (_close(row["five_hour"],snapshot.five_hour) and _close(row["weekly"],snapshot.weekly)
                        and _close(row["fable_weekly"],snapshot.fable_weekly) and previous_windows == comparison_windows
                        and (row["main_window_id"] or "") == snapshot.main_window_id and (row["login_method"] or "") == snapshot.login_method
                        and (row["manual_resets_json"] or "{}") == manual_resets_json)
                last_ts=parse_iso(row["ts_utc"]) or now
                if same and (now-last_ts) < _MIN_QUOTA_INTERVAL: return False
            self._conn.execute(
                "INSERT INTO quota_samples (ts_utc,five_hour,weekly,fable_weekly,source,windows_json,main_window_id,login_method,manual_resets_json,profile_key) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (now.isoformat(), snapshot.five_hour, snapshot.weekly, snapshot.fable_weekly, source, windows_json, snapshot.main_window_id, snapshot.login_method, manual_resets_json, profile_key),
            )
            self._conn.commit(); return True

    def quota_samples(self, start_utc: dt.datetime | None = None, end_utc: dt.datetime | None = None, provider: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            query="SELECT ts_utc,five_hour,weekly,fable_weekly,source,windows_json,main_window_id,login_method,manual_resets_json FROM quota_samples"; params=[]; clauses=[]
            if provider:
                provider_key = provider.casefold()
                if provider_key in ("claude", "codex", "grok", "antigravity"):
                    clauses.append("profile_key = ?")
                    params.append(provider_config_key(provider_key))
                if provider_key == "cursor":
                    clauses.append("(LOWER(source) = ? OR LOWER(source) LIKE ?)")
                    params.extend(["codexbar:cursor", "cursor:%"])
                elif provider_key == "grok":
                    # Current Grok Build source; preserve legacy Grok rows only for history.
                    clauses.append("LOWER(source) = ?")
                    params.append("grok:local-billing-log")
                elif provider_key == "claude":
                    # Keep historical rows for analytics; cache chooses new source only.
                    clauses.append("LOWER(source) IN (?, ?, ?)")
                    params.extend([
                        "codexbar:claude", "claude-code:statusline",
                        "claude-code:official-mod",
                    ])
                elif provider_key == "codex":
                    # Native Codex app-server samples are first-class cache/history.
                    clauses.append("LOWER(source) IN (?, ?)")
                    params.extend([
                        "codexbar:codex",
                        "codex-app-server:account/ratelimits/read",
                    ])
                elif provider_key == "antigravity":
                    # Legacy samples are retained for historical comparisons.
                    clauses.append("LOWER(source) IN (?, ?, ?, ?)")
                    params.extend(["codexbar:antigravity", "antigravity:language-server", "antigravity:cli-usage", "antigravity:statusline"])
                else:
                    clauses.append("LOWER(source) = ?")
                    params.append(f"codexbar:{provider_key}")
            if start_utc is not None: clauses.append("ts_utc >= ?"); params.append(start_utc.astimezone(dt.timezone.utc).isoformat())
            if end_utc is not None: clauses.append("ts_utc <= ?"); params.append(end_utc.astimezone(dt.timezone.utc).isoformat())
            if clauses: query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY ts_utc ASC"
            rows=[]
            for raw in self._conn.execute(query,params).fetchall():
                row=dict(raw); row["windows"]=self._decode_windows(row.get("windows_json")); row["manual_resets"]=self._decode_manual_resets(row.get("manual_resets_json"), provider or ""); rows.append(row)
            return rows

    # ------------------------------------------------------------------ #
    # Правила тредов
    # ------------------------------------------------------------------ #
    def thread_rules(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            rules: dict[str, dict[str, Any]] = {}
            for row in self._conn.execute("SELECT * FROM thread_rules").fetchall():
                rules[row["session_id"]] = {
                    "title_override": row["title_override"] or "",
                    "hidden": bool(row["hidden"]),
                    "pinned": bool(row["pinned"]),
                    "group_name": row["group_name"] or "",
                }
            return rules

    def _upsert_rule(self, session_id: str, **fields: Any) -> None:
        stamp = dt.datetime.now(dt.timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO thread_rules (session_id, updated_at) VALUES (?, ?) "
                "ON CONFLICT(session_id) DO NOTHING",
                (session_id, stamp),
            )
            for name, value in fields.items():
                self._conn.execute(
                    f"UPDATE thread_rules SET {name}=?, updated_at=? WHERE session_id=?",
                    (value, stamp, session_id),
                )
            self._conn.commit()
            self._cleanup_rule(session_id)

    def _cleanup_rule(self, session_id: str) -> None:
        """Удаляет пустое правило (все поля по умолчанию)."""
        row = self._conn.execute(
            "SELECT title_override, hidden, pinned, group_name "
            "FROM thread_rules WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if row is None:
            return
        if (
            not (row["title_override"] or "")
            and not row["hidden"]
            and not row["pinned"]
            and not (row["group_name"] or "")
        ):
            self._conn.execute("DELETE FROM thread_rules WHERE session_id=?", (session_id,))
            self._conn.commit()

    def set_title_override(self, session_id: str, title: str) -> None:
        self._upsert_rule(session_id, title_override=title.strip())

    def set_hidden(self, session_id: str, hidden: bool) -> None:
        self._upsert_rule(session_id, hidden=1 if hidden else 0)

    def set_pinned(self, session_id: str, pinned: bool) -> None:
        self._upsert_rule(session_id, pinned=1 if pinned else 0)

    def set_group(self, session_ids: list[str], group_name: str) -> None:
        name = group_name.strip()
        for session_id in session_ids:
            self._upsert_rule(session_id, group_name=name)

    def clear_group(self, session_ids: list[str]) -> None:
        for session_id in session_ids:
            self._upsert_rule(session_id, group_name="")

    def clear_group_by_name(self, group_name: str) -> None:
        with self._lock:
            ids = [
                r["session_id"]
                for r in self._conn.execute(
                    "SELECT session_id FROM thread_rules WHERE group_name=?",
                    (group_name,),
                ).fetchall()
            ]
        for session_id in ids:
            self._upsert_rule(session_id, group_name="")

    def import_aliases(self, aliases: dict[str, str]) -> int:
        """Импортирует псевдонимы v1.0 как title_override там, где правила ещё нет."""
        imported = 0
        with self._lock:
            existing = {
                r["session_id"]
                for r in self._conn.execute(
                    "SELECT session_id FROM thread_rules WHERE title_override IS NOT NULL "
                    "AND title_override <> ''"
                ).fetchall()
            }
        for session_id, title in aliases.items():
            if session_id in existing or not str(title).strip():
                continue
            self.set_title_override(session_id, str(title))
            imported += 1
        return imported

    # ------------------------------------------------------------------ #
    # Настройки и состояние
    # ------------------------------------------------------------------ #
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_state(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM app_state WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO app_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()


def _close(left: float | None, right: float | None, tol: float = 1e-6) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return abs(float(left) - float(right)) <= tol
