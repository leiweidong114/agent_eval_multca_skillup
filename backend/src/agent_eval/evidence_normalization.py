from __future__ import annotations

import json
from typing import Any


_TELEMETRY_PREFIX = "AGENT_EVAL_TELEMETRY_JSON:"


def _normalized_transcript(value: object) -> list[dict[str, Any]]:
    """Remove transport copies without hiding distinct conversation turns."""
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        content = item.get("content")
        content_text = content if isinstance(content, str) else json.dumps(
            content, ensure_ascii=False, sort_keys=True, default=str
        )
        if content_text.strip().startswith(_TELEMETRY_PREFIX):
            continue
        identity = (
            str(item.get("role") or ""),
            str(item.get("turn") or ""),
            content_text,
        )
        if identity in seen:
            continue
        seen.add(identity)
        normalized.append(item)
    return normalized


def _session_identity(value: dict[str, Any]) -> tuple[str, str, str]:
    transcript = _normalized_transcript(value.get("transcript"))
    return (
        str(value.get("session_id") or ""),
        str(value.get("final_message") or ""),
        json.dumps(transcript, ensure_ascii=False, sort_keys=True, default=str),
    )


def normalize_session_evidence(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize one session payload in place and return it."""
    if "transcript" in value:
        value["transcript"] = _normalized_transcript(value.get("transcript"))
    return value


def normalize_report_evidence(report: dict[str, Any]) -> dict[str, Any]:
    """Deduplicate Skill-Up's agent/workspace copies in old and new reports."""
    for iteration in report.get("results") or []:
        if not isinstance(iteration, dict):
            continue
        for case in iteration.get("case_results") or []:
            if not isinstance(case, dict):
                continue
            candidates: list[dict[str, Any]] = []
            single = case.get("session_result")
            if isinstance(single, dict):
                candidates.append(single)
            candidates.extend(
                item for item in (case.get("session_results") or [])
                if isinstance(item, dict)
            )
            unique: list[dict[str, Any]] = []
            seen: set[tuple[str, str, str]] = set()
            for candidate in candidates:
                normalized = normalize_session_evidence(candidate)
                identity = _session_identity(normalized)
                if identity in seen:
                    continue
                seen.add(identity)
                unique.append(normalized)
            case.pop("session_result", None)
            case.pop("session_results", None)
            if len(unique) == 1:
                case["session_result"] = unique[0]
            elif unique:
                case["session_results"] = unique
    return report
