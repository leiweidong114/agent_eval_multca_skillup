"""Migrate legacy local configuration files into repository-root .env."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "src"))

from agent_eval.env_config import load_root_env, update_root_env


LEGACY_FILES = (
    "config/local.yaml",
    "config/secrets.env",
    "config/litellm.env",
    "config/runtime-settings.json",
    "config/agent-paths.json",
)


def _yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return value if isinstance(value, dict) else {}


def _json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[name] = value
    return result


def collect_legacy_values() -> dict[str, str]:
    updates: dict[str, str] = {}
    models = _yaml(BACKEND_ROOT / "config" / "models.yaml")
    litellm = models.get("litellm") or {}
    for field, variable in {
        "model": "LITELLM_MODEL", "api_base": "LITELLM_API_BASE",
        "protocol": "LITELLM_PROTOCOL", "reasoning": "LITELLM_REASONING",
        "context_window": "LITELLM_CONTEXT_WINDOW",
        "max_output_tokens": "LITELLM_MAX_OUTPUT_TOKENS",
    }.items():
        if field in litellm:
            value = litellm[field]
            updates[variable] = str(value).lower() if isinstance(value, bool) else str(value)
    for field, variable in (
        ("agent_models", "LITELLM_AGENT_MODELS_JSON"),
        ("gateway_models", "LITELLM_GATEWAY_MODELS_JSON"),
    ):
        if isinstance(litellm.get(field), dict):
            updates[variable] = json.dumps(litellm[field], ensure_ascii=False, separators=(",", ":"))
    updates["LITELLM_PROFILE"] = "litellm"
    updates.setdefault("LITELLM_PROTOCOL", "openai_compatible")
    updates.setdefault("LITELLM_CONTEXT_WINDOW", "200000")
    updates.setdefault("LITELLM_MAX_OUTPUT_TOKENS", "32000")
    updates.setdefault("MODEL_PROFILES_JSON", "{}")
    updates.setdefault("SCHEMATIC_SKILLS_JSON", json.dumps([
        "schematic-pipeline", "signal-interface-generation",
        "schematic-layout-codegen", "schematic-web-apply",
    ], ensure_ascii=False, separators=(",", ":")))
    updates.setdefault("LITELLM_USERNAME", "admin")
    updates.setdefault("LITELLM_PASSWORD", "")
    updates.setdefault("DATABASE_URL", "")

    database = (_yaml(BACKEND_ROOT / "config" / "database.yaml").get("database") or {})
    database_mapping = {
        "enabled": "DATABASE_ENABLED", "host": "DATABASE_HOST",
        "port": "DATABASE_PORT", "name": "DATABASE_NAME", "user": "DATABASE_USER",
        "sslmode": "DATABASE_SSLMODE", "connect_timeout_seconds": "DATABASE_CONNECT_TIMEOUT_SECONDS",
    }
    for field, variable in database_mapping.items():
        if field in database:
            updates[variable] = str(database[field]).lower() if isinstance(database[field], bool) else str(database[field])
    trace = database.get("trace") or {}
    privacy = database.get("privacy") or {}
    for field, variable in {
        "enabled": "DATABASE_TRACE_ENABLED", "include_content": "DATABASE_TRACE_INCLUDE_CONTENT",
        "lookaround_seconds": "DATABASE_TRACE_LOOKAROUND_SECONDS", "limit": "DATABASE_TRACE_LIMIT",
    }.items():
        if field in trace:
            updates[variable] = str(trace[field]).lower() if isinstance(trace[field], bool) else str(trace[field])
    for field, variable in {
        "retention_days": "DATABASE_RETENTION_DAYS", "max_content_chars": "DATABASE_MAX_CONTENT_CHARS",
    }.items():
        if field in privacy:
            updates[variable] = str(privacy[field])

    local = _yaml(BACKEND_ROOT / "config" / "local.yaml")
    for name, value in (local.get("secrets") or {}).items():
        updates[str(name)] = str(value)
    profiles = local.get("profiles")
    if isinstance(profiles, dict) and profiles:
        updates["MODEL_PROFILES_JSON"] = json.dumps(profiles, ensure_ascii=False, separators=(",", ":"))
    if local.get("default_profile"):
        updates["LITELLM_PROFILE"] = str(local["default_profile"])
    judge = (local.get("scoring") or {}).get("llm_judge") or {}
    for field, variable in (("enabled", "LLM_JUDGE_ENABLED"), ("required", "LLM_JUDGE_REQUIRED")):
        if field in judge:
            updates[variable] = str(judge[field]).lower()

    for filename in ("secrets.env", "litellm.env"):
        updates.update(_env(BACKEND_ROOT / "config" / filename))
    scoring = (_yaml(BACKEND_ROOT / "config" / "scoring.yaml").get("llm_judge") or {})
    for field, variable in (
        ("enabled", "LLM_JUDGE_ENABLED"), ("required", "LLM_JUDGE_REQUIRED"),
        ("model", "LITELLM_JUDGE_MODEL"), ("timeout_seconds", "LLM_JUDGE_TIMEOUT_SECONDS"),
        ("max_evidence_chars", "LLM_JUDGE_MAX_EVIDENCE_CHARS"),
        ("temperature", "LLM_JUDGE_TEMPERATURE"),
    ):
        if field in scoring:
            value = scoring[field]
            updates.setdefault(variable, str(value).lower() if isinstance(value, bool) else str(value))
    updates.setdefault("AGENT_EVAL_WORKERS", "2")
    updates.setdefault("MODEL_AGENT_EVAL_DATA_DIR", "")
    updates.setdefault("PRISM_ADMIN_USERNAME", "admin")
    updates.setdefault("PRISM_ADMIN_PASSWORD", "")
    updates.setdefault("PRISM_SECURE_COOKIES", "0")
    updates.setdefault("PRISM_ALLOW_ONLINE_BENCHMARK_INSTALL", "0")
    runtime = _json(BACKEND_ROOT / "config" / "runtime-settings.json")
    if runtime.get("judge_model"):
        updates["LITELLM_JUDGE_MODEL"] = str(runtime["judge_model"])
    if runtime.get("agent_test_model"):
        updates["AGENT_TEST_MODEL"] = str(runtime["agent_test_model"])
    if isinstance(runtime.get("schematic_skills"), list):
        updates["SCHEMATIC_SKILLS_JSON"] = json.dumps(runtime["schematic_skills"], ensure_ascii=False)
    paths = _json(BACKEND_ROOT / "config" / "agent-paths.json")
    if paths:
        updates["AGENT_PATHS_JSON"] = json.dumps(paths, ensure_ascii=False, separators=(",", ":"))
        if paths.get("justdo"):
            updates["JUSTDO_AGENT_EXECUTABLE"] = str(paths["justdo"])
    return updates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove-legacy", action="store_true")
    args = parser.parse_args()
    existing = load_root_env(BACKEND_ROOT)
    migrated = {name: value for name, value in collect_legacy_values().items() if name not in existing}
    if "DATABASE_PASSWORD" not in existing and existing.get("LITELLM_DATABASE_PASSWORD"):
        migrated["DATABASE_PASSWORD"] = existing["LITELLM_DATABASE_PASSWORD"]
    update_root_env(BACKEND_ROOT, migrated)
    removed: list[str] = []
    if args.remove_legacy:
        for relative in LEGACY_FILES:
            path = BACKEND_ROOT / relative
            if path.is_file():
                path.unlink()
                removed.append(relative)
    print(f"Migrated {len(migrated)} variable(s) to {BACKEND_ROOT.parent / '.env'}")
    if removed:
        print("Removed legacy files: " + ", ".join(removed))


if __name__ == "__main__":
    main()
