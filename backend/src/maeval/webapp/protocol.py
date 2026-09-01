from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict
from typing import Any

from .db import Database


TRACKS = {
    "model_direct": "基础模型：无工具、统一提示和采样参数",
    "reference_agent": "参考 Agent：统一工具、提示、环境与资源预算",
    "native_agent": "原生 Agent：比较 Claude Code/OpenClaw 完整产品",
}

SUITES = [
    {
        "id": "core-reasoning-short",
        "name": "核心推理短测",
        "description": "数学、科学与事实性；每个已安装题库抽样 30 题。",
        "track": "model_direct",
        "benchmark_ids": ["gsm8k", "arc-challenge", "truthfulqa"],
        "sample_limit": 30,
        "repeats": 1,
        "allow_unsafe_code": False,
    },
    {
        "id": "coding-short",
        "name": "代码能力短测",
        "description": "HumanEval 与 MBPP，每个已安装题库抽样 20 题。",
        "track": "model_direct",
        "benchmark_ids": ["humaneval", "mbpp-sanitized"],
        "sample_limit": 20,
        "repeats": 1,
        "allow_unsafe_code": True,
    },
    {
        "id": "reference-agent-smoke",
        "name": "统一 Agent 冒烟",
        "description": "相同无工具补丁执行器下比较 API 模型。",
        "track": "reference_agent",
        "benchmark_ids": ["repo-repair"],
        "sample_limit": 1,
        "repeats": 1,
        "allow_unsafe_code": False,
    },
    {
        "id": "native-agent-smoke",
        "name": "原生 Agent 冒烟",
        "description": "比较 Claude Code 与 OpenClaw 完整产品链路。",
        "track": "native_agent",
        "benchmark_ids": ["repo-repair"],
        "sample_limit": 1,
        "repeats": 1,
        "allow_unsafe_code": False,
    },
]


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def select_items(
    db: Database,
    benchmark_id: str,
    limit: int | None,
    strategy: str,
    seed: int,
    shared_only: bool = False,
) -> list[dict[str, Any]]:
    rows = db.rows(
        "SELECT * FROM benchmark_items WHERE benchmark_id=?"
        + (" AND access_level='shared'" if shared_only else "")
        + " ORDER BY id",
        (benchmark_id,),
    )
    if not limit or limit >= len(rows):
        return rows
    rng = random.Random(f"{seed}:{benchmark_id}")
    if strategy == "ordered":
        return rows[:limit]
    if strategy == "random":
        rng.shuffle(rows)
        return rows[:limit]
    if strategy != "stratified":
        raise ValueError(f"unsupported sampling strategy: {strategy}")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row.get("category") or "uncategorized"].append(row)
    categories = sorted(groups)
    rng.shuffle(categories)
    for group in groups.values():
        rng.shuffle(group)
    selected: list[dict[str, Any]] = []
    while categories and len(selected) < limit:
        remaining: list[str] = []
        for category in categories:
            if groups[category] and len(selected) < limit:
                selected.append(groups[category].pop())
            if groups[category]:
                remaining.append(category)
        categories = remaining
    return selected


def build_manifest(
    providers: list[dict[str, Any]],
    benchmarks: list[dict[str, Any]],
    protocol: dict[str, Any],
) -> dict[str, Any]:
    provider_specs = []
    for provider in providers:
        provider_specs.append(
            {
                "id": provider["id"],
                "name": provider["name"],
                "kind": provider["kind"],
                "model": provider["model"],
                "base_url": provider["base_url"],
                "settings": json.loads(provider["settings_json"] or "{}"),
            }
        )
    benchmark_specs = [
        {
            key: benchmark.get(key)
            for key in (
                "id",
                "name",
                "version",
                "source_revision",
                "content_sha256",
                "item_count",
                "prompt_template_version",
                "scorer_version",
            )
        }
        for benchmark in benchmarks
    ]
    manifest = {
        "schema_version": 1,
        "providers": provider_specs,
        "benchmarks": benchmark_specs,
        "protocol": protocol,
    }
    manifest["config_hash"] = canonical_hash(manifest)
    return manifest


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower)


def wilson_interval(passed: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    rate = passed / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((rate * (1 - rate) + z * z / (4 * total)) / total) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def summarize_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["provider_id"], row["benchmark_id"])].append(row)
    output = []
    for (_, _), group in grouped.items():
        completed = len(group)
        passed = sum(int(row["passed"]) for row in group)
        errors = sum(row["status"] in {"error", "blocked", "incompatible"} for row in group)
        latencies = [float(row["wall_duration_ms"]) for row in group if row["wall_duration_ms"] is not None]
        low, high = wilson_interval(passed, completed)
        output.append(
            {
                "provider_id": group[0]["provider_id"],
                "name": group[0]["provider_name"],
                "model": group[0]["model"],
                "benchmark_id": group[0]["benchmark_id"],
                "benchmark_name": group[0]["benchmark_name"],
                "completed": completed,
                "passed": passed,
                "score": passed / completed if completed else 0,
                "ci95_low": low,
                "ci95_high": high,
                "error_rate": errors / completed if completed else 0,
                "p50_latency_ms": percentile(latencies, 0.5),
                "p95_latency_ms": percentile(latencies, 0.95),
                "tokens": sum((row.get("input_tokens") or 0) + (row.get("output_tokens") or 0) for row in group),
                "cost_usd": sum(float(row.get("cost_usd") or 0) for row in group),
                "cost_coverage": sum(row.get("cost_usd") is not None for row in group) / completed if completed else 0,
                "sample_size_status": "insufficient" if completed < 30 else ("exploratory" if completed < 100 else "adequate"),
            }
        )
    return sorted(output, key=lambda row: (row["provider_id"], row["benchmark_id"]))


def paired_comparison(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_provider: dict[int, dict[tuple[int, int], bool]] = defaultdict(dict)
    names: dict[int, str] = {}
    for row in rows:
        by_provider[row["provider_id"]][(row["benchmark_item_id"], row["repeat"])] = bool(row["passed"])
        names[row["provider_id"]] = row["provider_name"]
    providers = sorted(by_provider)
    comparisons = []
    for index, left in enumerate(providers):
        for right in providers[index + 1 :]:
            shared = set(by_provider[left]) & set(by_provider[right])
            left_wins = sum(by_provider[left][key] and not by_provider[right][key] for key in shared)
            right_wins = sum(by_provider[right][key] and not by_provider[left][key] for key in shared)
            ties = len(shared) - left_wins - right_wins
            discordant = left_wins + right_wins
            if discordant:
                tail = sum(
                    math.comb(discordant, k)
                    for k in range(min(left_wins, right_wins) + 1)
                ) / (2**discordant)
                p_value = min(1.0, 2 * tail)
            else:
                p_value = 1.0
            comparisons.append(
                {
                    "left_id": left,
                    "left_name": names[left],
                    "right_id": right,
                    "right_name": names[right],
                    "paired_items": len(shared),
                    "left_wins": left_wins,
                    "right_wins": right_wins,
                    "ties": ties,
                    "discordant_pairs": discordant,
                    "mcnemar_exact_p": p_value,
                    "significant_005": p_value < 0.05,
                    "evidence_status": "insufficient" if len(shared) < 30 else ("exploratory" if len(shared) < 100 else "adequate"),
                }
            )
    return comparisons
