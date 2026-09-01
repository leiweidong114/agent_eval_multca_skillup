from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx

from agent_eval.agent_config import describe_agents
from agent_eval.database import database_health
from agent_eval.model_config import resolve_model_profile
from agent_eval.runtime import find_multica_runtime, find_skill_up


def run_doctor(project_root: Path, *, agent: str | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def record(name: str, required: bool, action) -> Any:
        try:
            value = action()
            checks.append({"name": name, "status": "ok", "required": required, "detail": value})
            return value
        except Exception as exc:
            checks.append(
                {"name": name, "status": "error", "required": required, "detail": str(exc)}
            )
            return None

    record("skill_up", True, lambda: str(find_skill_up(project_root)))
    record("multica_runtime", True, lambda: str(find_multica_runtime(project_root)))
    frontend = project_root.parent / "frontend" / "dist" / "index.html"
    record(
        "frontend_dist",
        True,
        lambda: str(frontend) if frontend.is_file() else (_ for _ in ()).throw(
            FileNotFoundError(f"Frontend build is missing: {frontend}")
        ),
    )
    profile = record(
        "model_profile",
        True,
        lambda: resolve_model_profile(project_root, agent=agent).__dict__,
    )
    if profile and profile.get("api_base"):
        def probe_litellm() -> dict[str, Any]:
            base = str(profile["api_base"]).rstrip("/")
            url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
            key = profile["environment"]["LITELLM_API_KEY"]
            response = httpx.get(
                url,
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            response.raise_for_status()
            models = response.json().get("data") or []
            names = {str(item.get("id")) for item in models if isinstance(item, dict)}
            return {"url": url, "configured_model": profile["model"], "advertised": profile["model"] in names}

        record("litellm", True, probe_litellm)
    database = database_health(project_root)
    database_required = database.get("status") != "disabled"
    checks.append(
        {
            "name": "database",
            "status": "ok" if database.get("status") in {"ok", "disabled"} else "error",
            "required": database_required,
            "detail": database,
        }
    )
    agents = describe_agents(project_root)
    selected = next((item for item in agents if item["agent"] == agent), None) if agent else None
    checks.append(
        {
            "name": "agent",
            "status": "ok" if not selected or selected["available"] else "warning",
            "required": False,
            "detail": selected or {"available_agents": [item["agent"] for item in agents if item["available"]]},
        }
    )
    ok = not any(item["required"] and item["status"] == "error" for item in checks)
    return {"status": "ok" if ok else "error", "project_root": str(project_root), "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a Windows offline Agent Eval deployment")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--agent")
    args = parser.parse_args()
    result = run_doctor(args.project_root.resolve(), agent=args.agent)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
