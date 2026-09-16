#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
import urllib.error
import urllib.request
import uuid


DEFAULT_URL = "http://127.0.0.1:8631/api/schematicRationalityAnalysis"


def synthetic_result() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "overall_score": random.randint(70, 95),
        "electrical_correctness": random.randint(70, 95),
        "component_selection": random.randint(70, 95),
        "signal_integrity": random.randint(65, 92),
        "power_integrity": random.randint(70, 96),
        "protection_completeness": random.randint(65, 94),
        "layout_readability": random.randint(70, 96),
        "unrouted_net_count": random.randint(0, 3),
        "overlap_count": random.randint(0, 2),
        "erc_error_count": random.randint(0, 4),
        "warning_count": random.randint(0, 6),
        "quality_level": "synthetic_test",
        "summary": "由 schematic-rationality-analysis-writer 生成的链路测试数据，不代表真实工程结论。",
        "issues": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("SCHEMATIC_RATIONALITY_API_URL", DEFAULT_URL))
    parser.add_argument(
        "--session-id",
        default=(
            os.environ.get("AGENT_EVAL_SESSION_ID", "")
            or os.environ.get("AGENT_EVAL_RUN_ID", "")
        ),
    )
    parser.add_argument("--create-user", default="synthetic-user")
    parser.add_argument("--user-name", default="Synthetic Test User")
    parser.add_argument("--project-id", default="synthetic-project")
    parser.add_argument("--board-num", default="BOARD-TEST")
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    session_id = args.session_id.strip()
    if not session_id:
        parser.error(
            "a correlation ID is required via --session-id, "
            "AGENT_EVAL_SESSION_ID, or AGENT_EVAL_RUN_ID"
        )
    payload = {
        "uuid": str(uuid.uuid4()),
        "status": "completed",
        "createUser": args.create_user,
        "checkType": "synthetic_skill_test",
        "checkMessage": "Skill-generated schematic quality pipeline test",
        "userName": args.user_name,
        "hscopeProjectId": args.project_id,
        "boardNum": args.board_num,
        "sessionId": session_id,
        "resultText": synthetic_result(),
    }
    request = urllib.request.Request(
        args.url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "agent-eval-schematic-rationality-skill/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:  # noqa: S310
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"schematicRationalityAnalysis returned HTTP {exc.code}: {detail}") from exc
    if result.get("status") != "stored":
        raise SystemExit(f"unexpected response: {json.dumps(result, ensure_ascii=False)}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
