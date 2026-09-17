from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from agent_eval.evaluators.protocol import EvaluationEvidence


ARTIFACT_CONTRACTS: dict[str, tuple[str, ...]] = {
    "example-marker": ("/artifacts/verification.txt",),
    "api-test-suite-builder": (
        "/outputs/api-suite/tests/test_api.py",
        "/outputs/api-suite/load/k6-smoke.js",
        "/outputs/api-suite/reports/coverage.json",
        "/outputs/api-suite/reports/smoke-results.json",
        "/outputs/api-suite/manifest.json",
    ),
    "docx": (
        "/outputs/incident-report.docx",
        "/outputs/document-validation.json",
        "/outputs/document-manifest.json",
    ),
    "xlsx": (
        "/outputs/regional-sales-report.xlsx",
        "/outputs/workbook-validation.json",
        "/outputs/workbook-manifest.json",
    ),
    "webapp-testing": (
        "/outputs/web-e2e/automation.py",
        "/outputs/web-e2e/screenshot.png",
        "/outputs/web-e2e/console.log",
        "/outputs/web-e2e/results.json",
    ),
    "signal-interface-generation": (
        "/out/catalog.json",
        "/out/sheets.json",
    ),
    "schematic-layout-codegen": (
        "/out/frags/S1/base.txt",
        "/out/frags/S1/slices.json",
        "/out/layout/S1.json",
    ),
    "schematic-web-apply": ("/out/apply_result.json",),
}


def _with_skill_paths(evidence: EvaluationEvidence) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in evidence.artifact_manifest:
        path = str(item.get("path") or "").replace("\\", "/")
        if "/with_skill/outputs/" in "/" + path:
            result[path] = path
    return result


def _matching(paths: dict[str, str], suffix: str) -> str | None:
    values = sorted(path for path in paths if path.endswith(suffix))
    return values[0] if values else None


def _json(evidence: EvaluationEvidence, path: str | None) -> Any:
    if not path:
        return None
    try:
        return json.loads(evidence.resolve_artifact(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _valid_office_package(evidence: EvaluationEvidence, path: str | None, member: str) -> bool:
    if not path:
        return False
    try:
        with zipfile.ZipFile(evidence.resolve_artifact(path)) as archive:
            return member in archive.namelist() and archive.testzip() is None
    except (OSError, ValueError, zipfile.BadZipFile):
        return False


def _responses(evidence: EvaluationEvidence) -> str:
    return "\n".join(
        str(case.get("response") or "")
        for iteration in evidence.results
        for case in iteration.get("case_results") or []
        if case.get("configuration") == "with_skill"
    )


def evaluate_skill_result(skill_name: str, evidence: EvaluationEvidence) -> dict[str, Any]:
    paths = _with_skill_paths(evidence)
    required = ARTIFACT_CONTRACTS.get(skill_name, ())
    found = {suffix: _matching(paths, suffix) for suffix in required}
    cases = [
        case
        for iteration in evidence.results
        for case in iteration.get("case_results") or []
        if case.get("configuration") == "with_skill"
    ]
    checks: list[dict[str, Any]] = [
        {"name": "with_skill_cases_passed", "passed": bool(cases) and all(case.get("status") == "PASS" for case in cases), "required": True, "weight": 30},
        {"name": "selected_skill_executed", "passed": bool(evidence.skill_usage.get("all_selected_skills_read") and evidence.skill_usage.get("all_selected_skills_observed")), "required": True, "weight": 20},
    ]
    if required:
        checks.append({
            "name": "required_artifacts_exist",
            "passed": all(found.values()),
            "required": True,
            "weight": 30,
            "artifacts": found,
        })
    else:
        checks.append({"name": "agent_tool_evidence", "passed": int(evidence.process_metrics.get("tool_calls") or 0) > 0, "required": True, "weight": 30})

    structure_ok = True
    structure_detail: dict[str, Any] = {}
    if skill_name == "docx":
        structure_ok = _valid_office_package(evidence, found.get("/outputs/incident-report.docx"), "word/document.xml")
    elif skill_name == "xlsx":
        structure_ok = _valid_office_package(evidence, found.get("/outputs/regional-sales-report.xlsx"), "xl/workbook.xml")
    elif skill_name == "api-test-suite-builder":
        coverage = _json(evidence, found.get("/outputs/api-suite/reports/coverage.json"))
        smoke = _json(evidence, found.get("/outputs/api-suite/reports/smoke-results.json"))
        structure_ok = isinstance(coverage, dict) and isinstance(smoke, dict) and "status" in json.dumps(smoke)
    elif skill_name == "webapp-testing":
        result = _json(evidence, found.get("/outputs/web-e2e/results.json"))
        structure_ok = isinstance(result, dict) and "cleanup" in json.dumps(result) and "sha256" in json.dumps(result)
    elif skill_name == "example-marker":
        marker = found.get("/artifacts/verification.txt")
        try:
            structure_ok = bool(marker and "MULTICA_SKILL_UP_OK" in evidence.resolve_artifact(marker).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            structure_ok = False
    elif skill_name == "schematic-rationality-analysis-writer":
        response = _responses(evidence)
        structure_ok = all(token in response for token in ("stored", "uuid", "sessionId"))
        structure_detail["database_write_claim"] = structure_ok
    elif skill_name == "signal-interface-generation":
        sheets = _json(evidence, found.get("/out/sheets.json"))
        structure_ok = bool(
            isinstance(sheets, dict)
            and isinstance(sheets.get("sheets"), list)
            and sheets["sheets"]
        )
    elif skill_name == "schematic-layout-codegen":
        layout = _json(evidence, found.get("/out/layout/S1.json"))
        metrics = (layout.get("metrics") or layout) if isinstance(layout, dict) else {}
        structure_ok = bool(
            isinstance(layout, dict)
            and int(metrics.get("component_overlap_count") or 0) == 0
            and int(metrics.get("unrouted_net_count") or 0) == 0
            and int(evidence.process_metrics.get("subagent_calls") or 0) > 0
        )
    elif skill_name == "schematic-web-apply":
        applied = _json(evidence, found.get("/out/apply_result.json"))
        structure_ok = bool(
            isinstance(applied, dict)
            and applied.get("project_id")
            and int(applied.get("sheet_count") or 0) > 0
            and str(applied.get("url") or "").startswith("http")
        )
    checks.append({"name": "deliverable_structure_valid", "passed": structure_ok, "required": True, "weight": 20, **structure_detail})
    score = round(sum(check["weight"] for check in checks if check["passed"]), 2)
    failed = [check["name"] for check in checks if check["required"] and not check["passed"]]
    return {
        "accepted": not failed,
        "score": score,
        "checks": checks,
        "failed_checks": failed,
        "artifact_count": len(paths),
        "skill": skill_name,
    }
