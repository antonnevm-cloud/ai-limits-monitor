from claude_usage_manager.database import SCHEMA_VERSION, Database
from claude_usage_manager.models import ManualResetCredits, ManualResetItem, QuotaSnapshot, QuotaWindow


def _db(tmp_path):
    return Database(tmp_path / "usage.db")


def test_migration_sets_version(tmp_path):
    db = _db(tmp_path)
    version = db._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION
    # Повторное открытие идемпотентно.
    db.close()
    db2 = Database(tmp_path / "usage.db")
    assert db2._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    db2.close()


def test_pragmas(tmp_path):
    db = _db(tmp_path)
    mode = db._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"
    assert db._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    db.close()


def test_thread_rules_crud(tmp_path):
    db = _db(tmp_path)
    db.set_title_override("S1", "Имя")
    db.set_hidden("S1", True)
    db.set_pinned("S1", True)
    rules = db.thread_rules()
    assert rules["S1"]["title_override"] == "Имя"
    assert rules["S1"]["hidden"] is True and rules["S1"]["pinned"] is True

    # Снятие всех признаков удаляет правило.
    db.set_title_override("S1", "")
    db.set_hidden("S1", False)
    db.set_pinned("S1", False)
    assert "S1" not in db.thread_rules()
    db.close()


def test_grouping_rules(tmp_path):
    db = _db(tmp_path)
    db.set_group(["A", "B"], "Группа")
    rules = db.thread_rules()
    assert rules["A"]["group_name"] == "Группа" and rules["B"]["group_name"] == "Группа"
    db.clear_group_by_name("Группа")
    assert not any(r.get("group_name") for r in db.thread_rules().values())
    db.close()


def test_import_aliases(tmp_path):
    db = _db(tmp_path)
    imported = db.import_aliases({"S1": "Псевдоним", "S2": "  ", "S3": "Другой"})
    assert imported == 2
    rules = db.thread_rules()
    assert rules["S1"]["title_override"] == "Псевдоним"
    # Повторный импорт не перетирает существующий override.
    again = db.import_aliases({"S1": "Изменено"})
    assert again == 0
    assert db.thread_rules()["S1"]["title_override"] == "Псевдоним"
    db.close()


def test_quota_dedup(tmp_path):
    db = _db(tmp_path)
    snap = QuotaSnapshot(five_hour=10.0, weekly=20.0, fable_weekly=1.0, source="test")
    assert db.record_quota(snap) is True
    # Идентичный снимок в пределах 10 минут не пишется.
    assert db.record_quota(QuotaSnapshot(five_hour=10.0, weekly=20.0, fable_weekly=1.0, source="test")) is False
    # Изменившийся снимок того же провайдера пишется.
    assert db.record_quota(QuotaSnapshot(five_hour=11.0, weekly=20.0, fable_weekly=1.0, source="test")) is True
    # Другой провайдер не участвует в дедупликации первого.
    assert db.record_quota(QuotaSnapshot(weekly=20.0, source="codexbar:codex", provider="codex")) is True
    assert len(db.quota_samples()) == 3
    assert len(db.quota_samples(provider="codex")) == 1
    db.close()


def test_grok_observation_timestamp_does_not_duplicate_unchanged_quota(tmp_path):
    import datetime as dt
    db = _db(tmp_path)
    now = dt.datetime.now(dt.timezone.utc)
    def sample(percent, observed, reset):
        return QuotaSnapshot(provider="grok", source="grok:local-billing-log", weekly=percent,
            windows=[QuotaWindow("grok:weekly", "Grok", percent, resets_at=reset, window_minutes=10080,
                                 details={"observed_at": observed.isoformat(), "shared_pool": True})])
    reset = now + dt.timedelta(days=4)
    assert db.record_quota(sample(10, now, reset))
    assert not db.record_quota(sample(10, now + dt.timedelta(minutes=2), reset))
    assert db.record_quota(sample(11, now + dt.timedelta(minutes=3), reset))
    assert db.record_quota(sample(11, now + dt.timedelta(minutes=4), reset + dt.timedelta(days=7)))
    assert len(db.quota_samples(provider="grok")) == 3
    db.close()


def test_codex_native_source_is_available_to_cache_and_history(tmp_path):
    from claude_usage_manager.quota_monitor_model import cached_snapshot

    db = _db(tmp_path)
    codexbar = QuotaSnapshot(
        provider="codex",
        source="codexbar:codex",
        main_window_id="primary",
        windows=[QuotaWindow("primary", "Недельная квота", 61, window_minutes=10080, role="primary")],
    )
    native = QuotaSnapshot(
        provider="codex",
        source="codex-app-server:account/rateLimits/read",
        main_window_id="primary",
        windows=[QuotaWindow("primary", "Недельная квота", 3, window_minutes=10080, role="primary")],
    )
    assert db.record_quota(codexbar)
    assert db.record_quota(native)
    rows = db.quota_samples(provider="codex")
    assert [row["source"] for row in rows] == [codexbar.source, native.source]
    cached = cached_snapshot(db, "codex")
    assert cached.source == native.source
    assert cached.weekly == 3
    db.close()


def test_antigravity_direct_source_retains_legacy_history(tmp_path):
    from claude_usage_manager.quota_monitor_model import cached_snapshot
    db = _db(tmp_path)
    def snapshot(source, used):
        return QuotaSnapshot(provider="antigravity", source=source, main_window_id="ag-group:gemini:weekly", windows=[QuotaWindow("ag-group:gemini:weekly", "Gemini Models", used, window_minutes=10080, role="group")])
    assert db.record_quota(snapshot("codexbar:antigravity", 70))
    assert db.record_quota(snapshot("antigravity:language-server", 22))
    rows = db.quota_samples(provider="antigravity")
    assert [r["source"] for r in rows] == ["codexbar:antigravity", "antigravity:language-server"]
    result = cached_snapshot(db, "antigravity")
    assert result.source == "antigravity:language-server"
    assert result.weekly == 22
    db.close()


def test_quota_sample_timestamp_is_collection_time(tmp_path):
    import datetime as dt

    db = _db(tmp_path)
    old = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
    assert db.record_quota(
        QuotaSnapshot(weekly=20.0, source="codexbar:claude", updated_at=old)
    )
    recorded = dt.datetime.fromisoformat(db.quota_samples(provider="claude")[0]["ts_utc"])

    assert recorded > old
    assert (dt.datetime.now(dt.timezone.utc) - recorded).total_seconds() < 10
    db.close()


def test_settings_and_state(tmp_path):
    db = _db(tmp_path)
    assert db.get_setting("x", "def") == "def"
    db.set_setting("x", "1")
    assert db.get_setting("x") == "1"
    db.set_state("k", "v")
    assert db.get_state("k") == "v"
    db.close()


def test_migration_v2_fixes_old_codex_primary_window(tmp_path):
    db = _db(tmp_path)
    db._conn.execute(
        "INSERT INTO quota_samples (ts_utc, five_hour, weekly, fable_weekly, source) VALUES (?, ?, ?, ?, ?)",
        ("2026-08-05T12:00:00+00:00", 86.0, 0.0, None, "codexbar:codex"),
    )
    db._conn.execute("PRAGMA user_version=1")
    db._conn.commit()
    db.close()
    db = _db(tmp_path)
    row = db.quota_samples(provider="codex")[0]
    assert row["five_hour"] is None
    assert row["weekly"] == 86.0
    db.close()

def test_quota_windows_roundtrip_v3(tmp_path):
    import datetime as dt
    db = _db(tmp_path)
    snap = QuotaSnapshot(
        provider="antigravity", source="codexbar:antigravity", login_method="Pro",
        main_window_id="model-a", updated_at=dt.datetime.now(dt.timezone.utc),
        windows=[
            QuotaWindow("model-a", "Model A", 12.5, role="model"),
            QuotaWindow("model-b", "Model B", 3.0, role="model"),
        ],
    )
    assert db.record_quota(snap) is True
    row = db.quota_samples(provider="antigravity")[-1]
    assert row["main_window_id"] == "model-a"
    assert row["login_method"] == "Pro"
    assert [(w.id, w.used_percent, w.role) for w in row["windows"]] == [
        ("model-a", 12.5, "model"), ("model-b", 3.0, "model")
    ]
    db.close()


def test_manual_reset_credits_roundtrip_v4(tmp_path):
    import datetime as dt

    db = _db(tmp_path)
    first = dt.datetime(2026, 9, 5, 10, 0, tzinfo=dt.timezone.utc)
    second = dt.datetime(2026, 9, 6, 10, 0, tzinfo=dt.timezone.utc)
    snapshot = QuotaSnapshot(
        provider="codex",
        source="codexbar:codex",
        manual_resets=ManualResetCredits(
            provider="codex",
            state="available",
            available_count=2,
            source="codex-app-server:account/rateLimits/read",
            updated_at=first,
            freshness="live",
            details_complete=True,
            error="source temporarily unavailable",
            items=[
                ManualResetItem("second", "codex", second, description="second reset"),
                ManualResetItem("first", "codex", first, description="first reset"),
            ],
        ),
    )

    assert db.record_quota(snapshot) is True
    row = db.quota_samples(provider="codex")[-1]
    restored = row["manual_resets"]
    assert restored.available_count == 2
    assert [item.id for item in restored.items] == ["first", "second"]
    assert restored.items[0].expires_at == first
    assert restored.items[0].description == "first reset"
    assert restored.error == "source temporarily unavailable"
    assert db.record_quota(snapshot) is False
    db.close()
