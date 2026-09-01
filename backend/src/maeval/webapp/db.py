from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS providers (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kind TEXT NOT NULL,
 model TEXT NOT NULL, base_url TEXT, api_key_cipher TEXT, settings_json TEXT NOT NULL DEFAULT '{}',
 owner_user_id INTEGER, shared INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS benchmarks (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, source_url TEXT,
 license TEXT, status TEXT NOT NULL DEFAULT 'available', item_count INTEGER NOT NULL DEFAULT 0,
 metadata_json TEXT NOT NULL DEFAULT '{}', installed_at TEXT,
 version TEXT NOT NULL DEFAULT 'unversioned', source_revision TEXT,
 content_sha256 TEXT, task_type TEXT NOT NULL DEFAULT 'qa', language TEXT NOT NULL DEFAULT 'en',
 official INTEGER NOT NULL DEFAULT 1, prompt_template_version TEXT NOT NULL DEFAULT '1',
 scorer_version TEXT NOT NULL DEFAULT '1', owner_user_id INTEGER,
 visibility TEXT NOT NULL DEFAULT 'public', slug TEXT);
CREATE TABLE IF NOT EXISTS benchmark_items (
 id INTEGER PRIMARY KEY AUTOINCREMENT, benchmark_id TEXT NOT NULL, item_key TEXT NOT NULL,
 category TEXT, prompt TEXT NOT NULL, expected_json TEXT, scorer_type TEXT NOT NULL,
 metadata_json TEXT NOT NULL DEFAULT '{}', access_level TEXT NOT NULL DEFAULT 'private',
 UNIQUE(benchmark_id,item_key));
CREATE TABLE IF NOT EXISTS experiments (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, status TEXT NOT NULL,
 provider_ids_json TEXT NOT NULL, benchmark_ids_json TEXT NOT NULL, repeats INTEGER NOT NULL,
 sample_limit INTEGER, concurrency INTEGER NOT NULL, allow_unsafe_code INTEGER NOT NULL DEFAULT 0,
 track TEXT NOT NULL DEFAULT 'model_direct', random_seed INTEGER NOT NULL DEFAULT 42,
 sampling_strategy TEXT NOT NULL DEFAULT 'stratified', budget_json TEXT NOT NULL DEFAULT '{}',
 manifest_json TEXT NOT NULL DEFAULT '{}', config_hash TEXT,
 owner_user_id INTEGER, result_dir TEXT,
 total_jobs INTEGER NOT NULL DEFAULT 0, completed_jobs INTEGER NOT NULL DEFAULT 0,
 passed_jobs INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS results (
 id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id INTEGER NOT NULL, provider_id INTEGER NOT NULL,
 benchmark_item_id INTEGER NOT NULL, repeat INTEGER NOT NULL, status TEXT NOT NULL,
 passed INTEGER NOT NULL, score REAL NOT NULL, response_text TEXT, error TEXT,
 wall_duration_ms INTEGER, duration_api_ms INTEGER, input_tokens INTEGER, output_tokens INTEGER,
 cost_usd REAL, actual_model TEXT, detail TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS benchmark_versions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, benchmark_id TEXT NOT NULL, version TEXT NOT NULL,
 source_revision TEXT, content_sha256 TEXT NOT NULL, item_count INTEGER NOT NULL,
 importer_version TEXT NOT NULL, installed_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
 UNIQUE(benchmark_id,version,content_sha256));
CREATE TABLE IF NOT EXISTS experiment_items (
 experiment_id INTEGER NOT NULL, benchmark_item_id INTEGER NOT NULL, selection_order INTEGER NOT NULL,
 PRIMARY KEY(experiment_id,benchmark_item_id));
CREATE TABLE IF NOT EXISTS audit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, action TEXT NOT NULL,
 entity_type TEXT NOT NULL, entity_id TEXT, owner_user_id INTEGER,
 details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
 display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','evaluator','viewer')),
 active INTEGER NOT NULL DEFAULT 1, auth_source TEXT NOT NULL DEFAULT 'local',
 external_id TEXT, last_login_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at TEXT NOT NULL,
 expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS user_settings (
 user_id INTEGER PRIMARY KEY, health_check_enabled INTEGER NOT NULL DEFAULT 0,
 health_check_interval_minutes INTEGER NOT NULL DEFAULT 60,
 theme TEXT NOT NULL DEFAULT 'forest', language TEXT NOT NULL DEFAULT 'zh-CN',
 last_health_check_at TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS provider_health_checks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, provider_id INTEGER NOT NULL,
 owner_user_id INTEGER NOT NULL, ok INTEGER, message TEXT,
 actual_model TEXT, duration_ms INTEGER, source TEXT NOT NULL DEFAULT 'manual',
 checked_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_results_experiment ON results(experiment_id);
CREATE INDEX IF NOT EXISTS idx_results_experiment_provider ON results(experiment_id,provider_id);
CREATE INDEX IF NOT EXISTS idx_benchmark_items_benchmark_category ON benchmark_items(benchmark_id,category);
CREATE INDEX IF NOT EXISTS idx_experiment_items_experiment_order ON experiment_items(experiment_id,selection_order);
CREATE INDEX IF NOT EXISTS idx_audit_events_created_at ON audit_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_provider_health_owner ON provider_health_checks(owner_user_id,checked_at DESC);
"""

MIGRATION_COLUMNS = {
    "benchmarks": {
        "version": "TEXT NOT NULL DEFAULT 'unversioned'",
        "source_revision": "TEXT",
        "content_sha256": "TEXT",
        "task_type": "TEXT NOT NULL DEFAULT 'qa'",
        "language": "TEXT NOT NULL DEFAULT 'en'",
        "official": "INTEGER NOT NULL DEFAULT 1",
        "prompt_template_version": "TEXT NOT NULL DEFAULT '1'",
        "scorer_version": "TEXT NOT NULL DEFAULT '1'",
        "owner_user_id": "INTEGER",
        "visibility": "TEXT NOT NULL DEFAULT 'public'",
        "slug": "TEXT",
    },
    "benchmark_items": {
        "access_level": "TEXT NOT NULL DEFAULT 'private'",
    },
    "experiments": {
        "track": "TEXT NOT NULL DEFAULT 'model_direct'",
        "random_seed": "INTEGER NOT NULL DEFAULT 42",
        "sampling_strategy": "TEXT NOT NULL DEFAULT 'stratified'",
        "budget_json": "TEXT NOT NULL DEFAULT '{}'",
        "manifest_json": "TEXT NOT NULL DEFAULT '{}'",
        "config_hash": "TEXT",
        "owner_user_id": "INTEGER",
        "result_dir": "TEXT",
    },
    "providers": {
        "owner_user_id": "INTEGER",
        "shared": "INTEGER NOT NULL DEFAULT 0",
    },
    "audit_events": {
        "owner_user_id": "INTEGER",
    },
    "results": {
        "cost_usd": "REAL",
    },
    "users": {
        "auth_source": "TEXT NOT NULL DEFAULT 'local'",
        "external_id": "TEXT",
        "last_login_at": "TEXT",
    },
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript(SCHEMA)
            self._migrate(db)
            db.execute("PRAGMA optimize")

    @staticmethod
    def _migrate(db: sqlite3.Connection) -> None:
        for table, columns in MIGRATION_COLUMNS.items():
            existing = {
                row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, definition in columns.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        db.execute("CREATE INDEX IF NOT EXISTS idx_providers_owner ON providers(owner_user_id,id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_experiments_owner ON experiments(owner_user_id,id DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_owner ON audit_events(owner_user_id,id DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_benchmarks_owner ON benchmarks(owner_user_id,id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_provider_health_owner ON provider_health_checks(owner_user_id,checked_at DESC)")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def rows(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args).fetchall()]

    def row(self, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def execute(self, sql: str, args: tuple[Any, ...] = ()) -> int:
        with self.connect() as db:
            cursor = db.execute(sql, args)
            return int(cursor.lastrowid or 0)


def decode_json_fields(row: dict[str, Any], *fields: str) -> dict[str, Any]:
    for field in fields:
        if field in row:
            row[field.removesuffix("_json")] = json.loads(row.pop(field) or "{}")
    return row
