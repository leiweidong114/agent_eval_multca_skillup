from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path[:0] = [str(BACKEND_ROOT), str(BACKEND_ROOT / "src")]

from agent_eval.env_config import apply_root_env  # noqa: E402

apply_root_env(PROJECT_ROOT)

from app.schematic_data_client import (  # noqa: E402
    RATIONALITY_COLLECTION,
    SchematicDataClient,
)


def build_record(session_id: str, employee_no: str) -> dict[str, str]:
    result_text = {
        "schemaVersion": "1.0",
        "totalSuccessRate": 92.5,
        "checks": [
            {"name": "器件完整性", "successRate": 96},
            {"name": "网络连通性", "successRate": 94},
            {"name": "电源规则", "successRate": 91},
            {"name": "接口规则", "successRate": 90},
            {"name": "标注规范", "successRate": 93},
            {"name": "布局合理性", "successRate": 88},
        ],
        "summary": "用于验证 Java insert、MongoDB 查询和历史会话指标展示链路。",
    }
    return {
        "uuid": f"diagram-lint-e2e-{uuid.uuid4().hex}",
        "status": "completed",
        "createUser": employee_no,
        "createTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "checkType": "hscope_diagram_lint",
        "checkMessage": "原理图生成轨迹六项指标端到端测试",
        "userName": "评测系统测试用户",
        "hscopeProjectId": "agent-eval-e2e",
        "boardNum": "BOARD-DIAGRAM-LINT-E2E",
        "sessionId": session_id,
        "resultText": json.dumps(result_text, ensure_ascii=False),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Insert and verify one hscope_diagram_lint record")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--employee-no", default="100001")
    args = parser.parse_args()

    client = SchematicDataClient()
    record = build_record(args.session_id, args.employee_no)
    inserted = client.insert_record(record, collection_name=RATIONALITY_COLLECTION)
    matches = client.find_rationality_records([args.session_id])
    verified = any(item.get("uuid") == record["uuid"] for item in matches)
    print(json.dumps({
        "status": "ok" if verified else "verification_failed",
        "session_id": args.session_id,
        "uuid": record["uuid"],
        "insert_response": inserted,
        "matched_records": len(matches),
        "verified": verified,
    }, ensure_ascii=False, indent=2, default=str))
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
