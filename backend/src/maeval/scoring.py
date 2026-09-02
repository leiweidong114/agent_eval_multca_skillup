from __future__ import annotations

import json
import ast
import re
import tempfile
from pathlib import Path

from .adapters import _run_process
from .models import AdapterResult, ScoreResult, Task


def score_result(
    task: Task,
    adapter_result: AdapterResult,
    workdir: Path | None,
) -> ScoreResult:
    # A repository agent may finish editing files but time out while emitting
    # its final chat response. Command tasks judge the resulting artifact.
    if not adapter_result.ok and task.scorer.type != "command":
        return ScoreResult(
            passed=False,
            score=0.0,
            detail=f"adapter failed: {adapter_result.error}",
        )
    scorer = task.scorer
    actual = _strip_gateway_footer(adapter_result.text).strip()
    if scorer.type == "multiple_choice":
        expected = str(scorer.expected).strip().upper()
        match = re.match(
            r"^\s*(?:answer\s*[:\-]\s*)?[\(\[]?([A-Z0-9]+)[\)\].,:]?\b",
            actual,
            re.IGNORECASE,
        )
        observed = match.group(1).upper() if match else None
        passed = observed == expected
        return ScoreResult(
            passed,
            float(passed),
            f"expected_choice={expected!r}; observed_choice={observed!r}",
        )
    if scorer.type == "exact":
        expected = str(scorer.expected).strip()
        passed = actual == expected
        return ScoreResult(
            passed,
            float(passed),
            f"expected={expected!r}; actual={actual[:500]!r}",
        )
    if scorer.type == "contains":
        expected = str(scorer.expected)
        passed = expected in actual
        return ScoreResult(passed, float(passed), f"contains={expected!r}")
    if scorer.type == "numeric_answer":
        expected = _normalize_number(str(scorer.expected))
        candidates = re.findall(r"-?[\d,]+(?:\.\d+)?", actual)
        observed = _normalize_number(candidates[-1]) if candidates else None
        passed = observed == expected
        return ScoreResult(
            passed,
            float(passed),
            f"expected_number={expected!r}; observed={observed!r}",
        )
    if scorer.type == "regex":
        passed = re.search(str(scorer.expected), actual, re.MULTILINE) is not None
        return ScoreResult(
            passed, float(passed), f"regex={scorer.expected!r}"
        )
    if scorer.type == "python_expression":
        try:
            actual_code = _safe_expression(actual)
            expected_code = _safe_expression(str(scorer.expected))
            cases = [
                {"value": -5, "low": 0, "high": 10},
                {"value": 0, "low": 0, "high": 10},
                {"value": 4, "low": 0, "high": 10},
                {"value": 10, "low": 0, "high": 10},
                {"value": 15, "low": 0, "high": 10},
                {"value": -3, "low": -2, "high": 7},
            ]
            scope = {"min": min, "max": max}
            passed = all(
                eval(actual_code, {"__builtins__": {}}, scope | case)
                == eval(expected_code, {"__builtins__": {}}, scope | case)
                for case in cases
            )
        except (SyntaxError, ValueError, TypeError, NameError) as exc:
            return ScoreResult(False, 0.0, f"invalid expression: {exc}")
        return ScoreResult(
            passed,
            float(passed),
            f"expected_semantics={scorer.expected!r}; actual={actual!r}",
        )
    if scorer.type == "json":
        try:
            parsed = json.loads(_extract_json(actual))
        except (json.JSONDecodeError, ValueError) as exc:
            return ScoreResult(False, 0.0, f"invalid JSON: {exc}")
        passed = parsed == scorer.expected
        return ScoreResult(
            passed,
            float(passed),
            f"expected_json={json.dumps(scorer.expected, ensure_ascii=False)}",
        )
    if scorer.type == "command":
        if workdir is None:
            return ScoreResult(False, 0.0, "command scorer requires workdir")
        if not scorer.command:
            return ScoreResult(False, 0.0, "command scorer has no command")
        command = [
            part.format(workdir=str(workdir), fixture=str(task.fixture))
            for part in scorer.command
        ]
        try:
            code, stdout, stderr, duration_ms = _run_process(
                command,
                cwd=workdir,
                timeout_seconds=scorer.timeout_seconds,
            )
        except (OSError, TimeoutError) as exc:
            return ScoreResult(False, 0.0, str(exc))
        adapter_note = (
            f"; adapter_warning={adapter_result.error!r}"
            if not adapter_result.ok
            else ""
        )
        detail = (
            f"exit_code={code}; stdout={stdout[-1000:]!r}; "
            f"stderr={stderr[-1000:]!r}{adapter_note}"
        )
        return ScoreResult(
            passed=code == 0,
            score=float(code == 0),
            detail=detail,
            test_duration_ms=duration_ms,
        )
    if scorer.type == "code_command":
        if not scorer.command:
            return ScoreResult(False, 0.0, "code_command scorer has no command")
        try:
            code_text = _extract_code(actual)
        except ValueError as exc:
            return ScoreResult(False, 0.0, f"invalid code response: {exc}")
        with tempfile.TemporaryDirectory(prefix="maeval-code-") as directory:
            solution_dir = Path(directory)
            response_file = solution_dir / "solution.py"
            response_file.write_text(code_text, encoding="utf-8")
            command = [
                part.format(
                    solution_dir=str(solution_dir),
                    response_file=str(response_file),
                    fixture=str(task.fixture),
                )
                for part in scorer.command
            ]
            try:
                code, stdout, stderr, duration_ms = _run_process(
                    command,
                    cwd=solution_dir,
                    timeout_seconds=scorer.timeout_seconds,
                )
            except (OSError, TimeoutError) as exc:
                return ScoreResult(False, 0.0, str(exc))
        detail = (
            f"exit_code={code}; stdout={stdout[-1000:]!r}; "
            f"stderr={stderr[-1000:]!r}"
        )
        return ScoreResult(
            passed=code == 0,
            score=float(code == 0),
            detail=detail,
            test_duration_ms=duration_ms,
        )
    if scorer.type == "humaneval":
        if not isinstance(scorer.expected, dict):
            return ScoreResult(False, 0.0, "humaneval metadata is missing")
        try:
            code_text = _extract_code(actual)
        except ValueError as exc:
            return ScoreResult(False, 0.0, f"invalid code response: {exc}")
        test_code = str(scorer.expected.get("test") or "")
        entry_point = str(scorer.expected.get("entry_point") or "")
        if not test_code or not entry_point:
            return ScoreResult(False, 0.0, "humaneval test metadata is invalid")
        with tempfile.TemporaryDirectory(prefix="maeval-humaneval-") as directory:
            solution = Path(directory) / "solution.py"
            solution.write_text(
                code_text
                + "\n\n"
                + test_code
                + f"\n\ncheck({entry_point})\n",
                encoding="utf-8",
            )
            try:
                code, stdout, stderr, duration_ms = _run_process(
                    ["python", "-I", str(solution)],
                    cwd=Path(directory),
                    timeout_seconds=scorer.timeout_seconds,
                )
            except (OSError, TimeoutError) as exc:
                return ScoreResult(False, 0.0, str(exc))
        return ScoreResult(
            code == 0,
            float(code == 0),
            f"exit_code={code}; stdout={stdout[-500:]!r}; stderr={stderr[-500:]!r}",
            test_duration_ms=duration_ms,
        )
    if scorer.type == "mbpp":
        if not isinstance(scorer.expected, dict):
            return ScoreResult(False, 0.0, "MBPP test metadata is missing")
        try:
            code_text = _extract_code(actual)
        except ValueError as exc:
            return ScoreResult(False, 0.0, f"invalid code response: {exc}")
        imports = scorer.expected.get("test_imports") or []
        tests = scorer.expected.get("test_list") or []
        if not isinstance(imports, list) or not isinstance(tests, list) or not tests:
            return ScoreResult(False, 0.0, "MBPP test metadata is invalid")
        with tempfile.TemporaryDirectory(prefix="maeval-mbpp-") as directory:
            solution = Path(directory) / "solution.py"
            solution.write_text(
                "\n".join(str(line) for line in imports)
                + "\n"
                + code_text
                + "\n\n"
                + "\n".join(str(test) for test in tests)
                + "\n",
                encoding="utf-8",
            )
            try:
                code, stdout, stderr, duration_ms = _run_process(
                    ["python", "-I", str(solution)],
                    cwd=Path(directory),
                    timeout_seconds=scorer.timeout_seconds,
                )
            except (OSError, TimeoutError) as exc:
                return ScoreResult(False, 0.0, str(exc))
        return ScoreResult(
            code == 0,
            float(code == 0),
            f"exit_code={code}; stdout={stdout[-500:]!r}; stderr={stderr[-500:]!r}",
            test_duration_ms=duration_ms,
        )
    return ScoreResult(False, 0.0, f"unknown scorer type: {scorer.type}")


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            content = lines[1:-1]
            if content and content[0].strip().lower() in {"json", "json5"}:
                content = content[1:]
            return "\n".join(content)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    start = stripped.find("[")
    end = stripped.rfind("]")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    raise ValueError("no JSON object or array found")


def _extract_code(text: str) -> str:
    stripped = text.strip()
    fence = re.search(
        r"```(?:python|py)?\s*\n(?P<code>.*?)```",
        stripped,
        re.IGNORECASE | re.DOTALL,
    )
    if fence:
        return fence.group("code").strip() + "\n"
    if "```" in stripped:
        raise ValueError("unclosed or unsupported code fence")
    if not stripped:
        raise ValueError("empty response")
    return stripped + "\n"


def _safe_expression(text: str):
    tree = ast.parse(text.strip(), mode="eval")
    allowed_names = {"value", "low", "high", "min", "max"}
    allowed_nodes = (
        ast.Expression,
        ast.Call,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.UnaryOp,
        ast.USub,
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            raise ValueError(f"unsupported syntax: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in allowed_names:
            raise ValueError(f"unsupported name: {node.id}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in {
                "min",
                "max",
            }:
                raise ValueError("only min/max calls are allowed")
    return compile(tree, "<expression>", "eval")


def _normalize_number(value: str) -> str:
    value = value.replace(",", "").strip()
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer():
        return str(int(number))
    return format(number, ".12g")


def _strip_gateway_footer(text: str) -> str:
    """Remove known gateway-injected notices while preserving raw evidence."""
    markers = (
        "\n公益不保证稳定性",
        "\n注意：\nGrok官方限制账户并发",
        "\n官网地址：https://OpenOneApi.com",
    )
    end = len(text)
    for marker in markers:
        index = text.find(marker)
        if index >= 0:
            end = min(end, index)
    return text[:end]
