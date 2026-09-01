from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .models import RunRecord


def write_reports(records: list[RunRecord], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "results.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    fields = list(records[0].to_dict()) if records else []
    with (output_dir / "results.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if fields:
            writer.writeheader()
            for record in records:
                row = record.to_dict()
                row["tags"] = ",".join(row["tags"])
                row["changed_files"] = ",".join(row["changed_files"])
                writer.writerow(row)

    (output_dir / "summary.md").write_text(
        build_markdown_summary(records), encoding="utf-8"
    )


def build_markdown_summary(records: Iterable[RunRecord]) -> str:
    records = list(records)
    lines = [
        "# Evaluation Summary",
        "",
        f"Total evaluated runs: {len(records)}",
        "",
        "| Candidate | Runs | Pass rate | Avg score | Median wall | "
        "Median API | Avg cost |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    groups: dict[str, list[RunRecord]] = defaultdict(list)
    for record in records:
        groups[record.candidate_id].append(record)
    for candidate_id in sorted(groups):
        group = groups[candidate_id]
        pass_rate = sum(item.passed for item in group) / len(group)
        avg_score = statistics.fmean(item.score for item in group)
        median_wall = statistics.median(
            item.wall_duration_ms for item in group
        )
        api_values = [
            item.duration_api_ms
            for item in group
            if item.duration_api_ms is not None
        ]
        median_api = (
            f"{statistics.median(api_values) / 1000:.2f}s"
            if api_values
            else "n/a"
        )
        costs = [item.cost_usd for item in group if item.cost_usd is not None]
        avg_cost = f"${statistics.fmean(costs):.4f}" if costs else "n/a"
        lines.append(
            f"| {candidate_id} | {len(group)} | {pass_rate:.1%} | "
            f"{avg_score:.3f} | {median_wall / 1000:.2f}s | "
            f"{median_api} | {avg_cost} |"
        )

    lines.extend(
        [
            "",
            "## Per-task results",
            "",
            "| Candidate | Task | Repeat | Passed | Score | Wall | "
            "API | Changed files |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for record in sorted(
        records, key=lambda item: (item.task_id, item.candidate_id, item.repeat)
    ):
        api = (
            f"{record.duration_api_ms / 1000:.2f}s"
            if record.duration_api_ms is not None
            else "n/a"
        )
        lines.append(
            f"| {record.candidate_id} | {record.task_id} | {record.repeat} | "
            f"{'yes' if record.passed else 'no'} | {record.score:.2f} | "
            f"{record.wall_duration_ms / 1000:.2f}s | {api} | "
            f"{len(record.changed_files)} |"
        )
    lines.extend(_category_section(records))
    lines.extend(_pairwise_section(records))
    lines.append("")
    return "\n".join(lines)


def _category_section(records: list[RunRecord]) -> list[str]:
    groups: dict[tuple[str, str], list[RunRecord]] = defaultdict(list)
    for record in records:
        category = record.tags[0] if record.tags else "uncategorized"
        groups[(record.candidate_id, category)].append(record)
    lines = [
        "",
        "## Results by primary category",
        "",
        "| Candidate | Category | Runs | Pass rate | Avg score |",
        "|---|---|---:|---:|---:|",
    ]
    for (candidate, category), group in sorted(groups.items()):
        pass_rate = sum(item.passed for item in group) / len(group)
        avg_score = statistics.fmean(item.score for item in group)
        lines.append(
            f"| {candidate} | {category} | {len(group)} | "
            f"{pass_rate:.1%} | {avg_score:.3f} |"
        )
    return lines


def _pairwise_section(records: list[RunRecord]) -> list[str]:
    candidates = sorted({record.candidate_id for record in records})
    if len(candidates) != 2:
        return []
    by_key: dict[tuple[str, int], dict[str, RunRecord]] = defaultdict(dict)
    for record in records:
        by_key[(record.task_id, record.repeat)][record.candidate_id] = record
    first, second = candidates
    first_wins = second_wins = ties = 0
    comparable = 0
    for pair in by_key.values():
        if first not in pair or second not in pair:
            continue
        comparable += 1
        left = pair[first]
        right = pair[second]
        left_value = (int(left.passed), left.score)
        right_value = (int(right.passed), right.score)
        if left_value > right_value:
            first_wins += 1
        elif right_value > left_value:
            second_wins += 1
        else:
            ties += 1
    return [
        "",
        "## Paired capability comparison",
        "",
        "Only pass/fail and task score determine a win; latency is reported "
        "separately and never breaks a capability tie.",
        "",
        f"- Comparable task-runs: {comparable}",
        f"- {first} wins: {first_wins}",
        f"- {second} wins: {second_wins}",
        f"- Ties: {ties}",
    ]
