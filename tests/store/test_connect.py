"""The store connects the way the pooler requires (spec 2026-09-11 §1)."""

from __future__ import annotations

import pytest

from rehoboam.config import Settings
from rehoboam.store import SCHEMA, StoreUnconfigured, connect, resolve_dsn


def test_schema_name_is_rehoboam():
    assert SCHEMA == "rehoboam"


def test_connect_yields_dict_rows_without_prepared_statements(store_dsn):
    with connect(store_dsn) as conn:
        assert conn.prepare_threshold is None
        row = conn.execute("select 1 as one").fetchone()
    assert row == {"one": 1}


def test_connect_commits_on_clean_exit_and_rolls_back_on_error(store_dsn):
    with connect(store_dsn) as conn:
        conn.execute("create table t_commit (x integer)")
        conn.execute("insert into t_commit values (1)")
    with pytest.raises(RuntimeError):
        with connect(store_dsn) as conn:
            conn.execute("insert into t_commit values (2)")
            raise RuntimeError("abort")
    with connect(store_dsn) as conn:
        assert conn.execute("select count(*) as n from t_commit").fetchone()["n"] == 1


def test_resolve_dsn_prefers_the_argument(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://env")
    assert resolve_dsn("postgresql://arg") == "postgresql://arg"


def test_resolve_dsn_falls_back_to_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    # Settings.model_config["env_file"] is resolved once, at first import of
    # rehoboam.config — which the suite's top-level conftest.py triggers
    # before this test's chdir runs, freezing it to this worktree's real
    # .env. Overriding it here (rather than relying on chdir alone) is what
    # actually keeps this test off the real DATABASE_URL.
    monkeypatch.setitem(Settings.model_config, "env_file", tmp_path / ".env")
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://env")
    assert resolve_dsn() == "postgresql://env"


def test_resolve_dsn_raises_when_unset(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(Settings.model_config, "env_file", tmp_path / ".env")
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(StoreUnconfigured):
        resolve_dsn()
