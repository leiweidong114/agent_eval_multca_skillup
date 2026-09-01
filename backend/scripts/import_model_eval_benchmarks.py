from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "src"))

from maeval.webapp.benchmarks import seed_catalog  # noqa: E402
from maeval.webapp.db import Database  # noqa: E402


BENCHMARK_COLUMNS = (
    "name",
    "description",
    "source_url",
    "license",
    "status",
    "item_count",
    "metadata_json",
    "installed_at",
    "version",
    "source_revision",
    "content_sha256",
    "task_type",
    "language",
    "official",
    "prompt_template_version",
    "scorer_version",
    "visibility",
    "slug",
)
ITEM_COLUMNS = (
    "benchmark_id",
    "item_key",
    "category",
    "prompt",
    "expected_json",
    "scorer_type",
    "metadata_json",
    "access_level",
)
VERSION_COLUMNS = (
    "benchmark_id",
    "version",
    "source_revision",
    "content_sha256",
    "item_count",
    "importer_version",
    "installed_at",
    "metadata_json",
)


def import_installed_official_benchmarks(source_db: Path, target_data_dir: Path) -> dict[str, object]:
    if not source_db.is_file():
        raise FileNotFoundError(f"source database not found: {source_db}")

    target_data_dir.mkdir(parents=True, exist_ok=True)
    target_db = Database(target_data_dir / "maeval.db")
    seed_catalog(target_db)
    source = sqlite3.connect(f"file:{source_db.resolve().as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    imported: list[dict[str, object]] = []
    try:
        benchmarks = source.execute(
            """SELECT * FROM benchmarks
            WHERE official=1 AND status='installed' AND item_count>0 ORDER BY id"""
        ).fetchall()
        with target_db.connect() as destination:
            for benchmark in benchmarks:
                benchmark_id = str(benchmark["id"])
                assignments = ",".join(f"{column}=?" for column in BENCHMARK_COLUMNS)
                destination.execute(
                    f"UPDATE benchmarks SET {assignments},owner_user_id=NULL WHERE id=?",
                    tuple(benchmark[column] for column in BENCHMARK_COLUMNS) + (benchmark_id,),
                )
                destination.execute(
                    "DELETE FROM benchmark_items WHERE benchmark_id=?", (benchmark_id,)
                )
                items = source.execute(
                    "SELECT * FROM benchmark_items WHERE benchmark_id=? ORDER BY id",
                    (benchmark_id,),
                ).fetchall()
                placeholders = ",".join("?" for _ in ITEM_COLUMNS)
                destination.executemany(
                    f"INSERT INTO benchmark_items({','.join(ITEM_COLUMNS)}) VALUES({placeholders})",
                    [tuple(item[column] for column in ITEM_COLUMNS) for item in items],
                )
                destination.execute(
                    "DELETE FROM benchmark_versions WHERE benchmark_id=?", (benchmark_id,)
                )
                versions = source.execute(
                    "SELECT * FROM benchmark_versions WHERE benchmark_id=? ORDER BY id",
                    (benchmark_id,),
                ).fetchall()
                version_placeholders = ",".join("?" for _ in VERSION_COLUMNS)
                destination.executemany(
                    f"INSERT INTO benchmark_versions({','.join(VERSION_COLUMNS)}) VALUES({version_placeholders})",
                    [tuple(version[column] for column in VERSION_COLUMNS) for version in versions],
                )
                imported.append({"id": benchmark_id, "item_count": len(items)})
    finally:
        source.close()
    return {
        "source": str(source_db.resolve()),
        "target": str((target_data_dir / "maeval.db").resolve()),
        "benchmark_count": len(imported),
        "item_count": sum(int(item["item_count"]) for item in imported),
        "benchmarks": imported,
    }


def main() -> None:
    default_source = BACKEND_ROOT.parent.parent / "model-agent-eval" / "data" / "maeval.db"
    parser = argparse.ArgumentParser(
        description="Copy installed public question banks without users, secrets, providers, or history."
    )
    parser.add_argument("--source-db", type=Path, default=default_source)
    parser.add_argument(
        "--target-data-dir", type=Path, default=BACKEND_ROOT / "model_eval_data"
    )
    args = parser.parse_args()
    print(
        json.dumps(
            import_installed_official_benchmarks(args.source_db, args.target_data_dir),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
