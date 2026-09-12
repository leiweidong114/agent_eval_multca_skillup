from __future__ import annotations

import json
import re
from typing import Any

from agent_eval.evaluators.protocol import EvaluationEvidence


SCHEMATIC_URL = re.compile(
    r"https?://[^\s`\"']+/static_schematic/[^\s`\"']+/shell\.html"
)


def _artifact(evidence: EvaluationEvidence, suffix: str) -> tuple[str | None, Any]:
    candidates = [
        str(item.get("path") or "")
        for item in evidence.artifact_manifest
        if str(item.get("path") or "").replace("\\", "/").endswith(suffix)
        and "/with_skill/outputs/"
        in "/" + str(item.get("path") or "").replace("\\", "/")
    ]
    candidates.sort(
        key=lambda path: ("/agent/run/" not in path.replace("\\", "/"), len(path))
    )
    for path in candidates:
        try:
            return path, json.loads(
                evidence.resolve_artifact(path).read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return None, None


def _layouts(evidence: EvaluationEvidence) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for item in evidence.artifact_manifest:
        path = str(item.get("path") or "").replace("\\", "/")
        if (
            "/with_skill/outputs/workspace/out/layout/" not in "/" + path
            or not path.endswith(".json")
        ):
            continue
        try:
            payload = json.loads(
                evidence.resolve_artifact(path).read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            result.append((path, payload))
    return result


def _response_text(evidence: EvaluationEvidence) -> str:
    values: list[str] = []
    for iteration in evidence.results:
        for case in iteration.get("case_results") or []:
            values.append(str(case.get("response") or ""))
            for session in case.get("session_results") or []:
                values.append(str(session.get("final_message") or ""))
    return "\n".join(values)


def evaluate_result(
    evidence: EvaluationEvidence, *, task_type: str = "block_to_schematic"
) -> dict[str, object]:
    """Strictly validate delivered schematic artifacts, not process exit alone."""
    sheets_path, sheets = _artifact(evidence, "/out/sheets.json")
    apply_path, apply_result = _artifact(evidence, "/out/apply_result.json")
    layouts = _layouts(evidence)
    response_text = _response_text(evidence)

    cases = [
        case
        for iteration in evidence.results
        for case in iteration.get("case_results") or []
    ]
    cases_passed = bool(cases) and all(case.get("status") == "PASS" for case in cases)
    sheets_valid = bool(
        isinstance(sheets, dict)
        and isinstance(sheets.get("sheets"), list)
        and sheets["sheets"]
    )
    layout_valid = bool(layouts) and all(
        int((payload.get("metrics") or payload).get("component_overlap_count") or 0)
        == 0
        and int((payload.get("metrics") or payload).get("unrouted_net_count") or 0)
        == 0
        for _, payload in layouts
    )
    apply_valid = bool(
        isinstance(apply_result, dict)
        and apply_result.get("project_id")
        and int(apply_result.get("sheet_count") or 0) > 0
    )
    verification = (
        apply_result.get("url_verification") if isinstance(apply_result, dict) else {}
    )
    url = str(apply_result.get("url") or "") if isinstance(apply_result, dict) else ""
    url_verified = bool(
        SCHEMATIC_URL.fullmatch(url)
        and (
            apply_result.get("url_verified") is True
            or (
                isinstance(verification, dict)
                and verification.get("status") == "ok"
                and int(verification.get("http_status") or 0) == 200
            )
        )
        and url in response_text
    )
    skill_used = bool(
        evidence.skill_usage.get("all_selected_skills_read")
        and evidence.skill_usage.get("all_selected_skills_observed")
    )
    subagent_used = int(evidence.process_metrics.get("subagent_calls") or 0) > 0

    if task_type == "block_to_signal_list":
        checks = [
            {"name": "cases_passed", "passed": cases_passed, "required": True, "weight": 20},
            {"name": "selected_skills_executed", "passed": skill_used, "required": True, "weight": 20},
            {"name": "sheets_json_valid", "passed": sheets_valid, "required": True, "weight": 60, "path": sheets_path},
        ]
    else:
        sheet_required = task_type == "block_to_schematic"
        checks = [
            {"name": "cases_passed", "passed": cases_passed, "required": True, "weight": 10},
            {"name": "selected_skills_executed", "passed": skill_used, "required": True, "weight": 10},
            {"name": "sheets_json_valid", "passed": sheets_valid, "required": sheet_required, "weight": 15 if sheet_required else 0, "path": sheets_path},
            {"name": "subagent_execution_verified", "passed": subagent_used, "required": True, "weight": 15 if sheet_required else 20},
            {"name": "layout_artifacts_valid", "passed": layout_valid, "required": True, "weight": 20 if sheet_required else 25, "paths": [path for path, _ in layouts]},
            {"name": "apply_result_valid", "passed": apply_valid, "required": True, "weight": 15 if sheet_required else 20, "path": apply_path},
            {"name": "schematic_url_verified", "passed": url_verified, "required": True, "weight": 15, "url": url or None},
        ]
    score = round(sum(item["weight"] for item in checks if item["passed"]), 2)
    accepted = all(item["passed"] for item in checks if item["required"])
    return {
        "accepted": accepted,
        "score": score,
        "checks": checks,
        "failed_checks": [
            item["name"]
            for item in checks
            if item["required"] and not item["passed"]
        ],
        "iteration_count": len(evidence.results),
        "artifact_count": len(evidence.artifact_manifest),
        "url": url or None,
        "task_type": task_type,
    }
