"""Non-mutating service checks, with no credentials in their output."""
from pathlib import Path

import httpx

from agent_eval.database import database_health
from agent_eval.failure import describe_evaluation_failure
from agent_eval.model_config import resolve_model_profile


def check_database(root: Path) -> dict:
    try:
        return database_health(root)
    except Exception as exc:
        return {"status": "failed", "error": type(exc).__name__,
                "suggested_action": "Check DATABASE_URL and PostgreSQL connectivity"}


def check_litellm(root: Path, *, model: str | None = None, timeout: float = 30) -> dict:
    try:
        profile = resolve_model_profile(root, model_override=model)
        with httpx.Client(timeout=timeout) as client:
            headers = {"Authorization": "Bearer " + profile.environment[profile.api_key_env]}
            response = client.get(profile.api_base + "/models", headers=headers)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                raise ValueError("Invalid model catalog response")
            result = {"status": "ok", "catalog_access": True, "visible_models": len(data["data"]),
                      "inference": "not_tested", "dashboard_login": "not_tested_separate_from_api"}
            if model:
                response = client.post(profile.api_base + "/chat/completions", headers=headers,
                                       json={"model": model, "messages": [{"role": "user", "content": "HI"}]})
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or data.get("error") or not data.get("choices"):
                    raise ValueError("HTTP 200 response contains no model output")
                result.update(inference="ok", model=model, usage=data.get("usage"))
            return result
    except Exception as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        # Error body may contain credentials from provider diagnostics; report a safe summary.
        failure = describe_evaluation_failure(str(exc), returncode=1, status_code=status,
                                             component="litellm_connectivity") or {}
        failure.pop("technical_detail", None)
        return {"status": "failed", "status_code": status, "failure": failure,
                "error_type": type(exc).__name__}
