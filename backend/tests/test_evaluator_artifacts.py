from pathlib import Path

import pytest

from agent_eval.evaluators.artifacts import build_artifact_manifest
from agent_eval.evaluators.protocol import EvaluationEvidence


def evidence_for(root: Path) -> EvaluationEvidence:
    return EvaluationEvidence(
        deterministic_scores={},
        process_metrics={},
        skill_usage={},
        skill_quality={},
        results=[],
        interactions=[],
        artifact_root=str(root),
        artifact_manifest=build_artifact_manifest(root),
    )


def test_artifact_manifest_uses_scoped_relative_paths(tmp_path):
    artifact = tmp_path / "out" / "sheets.json"
    artifact.parent.mkdir()
    artifact.write_text('{"sheets": []}', encoding="utf-8")

    evidence = evidence_for(tmp_path)

    assert evidence.artifact_manifest == ({
        "path": "out/sheets.json",
        "size_bytes": artifact.stat().st_size,
        "suffix": ".json",
    },)
    assert evidence.resolve_artifact("out/sheets.json") == artifact.resolve()


def test_artifact_resolution_rejects_escape_and_missing_file(tmp_path):
    evidence = evidence_for(tmp_path)

    with pytest.raises(ValueError, match="escapes"):
        evidence.resolve_artifact("../secret.txt")
    with pytest.raises(FileNotFoundError, match="does not exist"):
        evidence.resolve_artifact("missing.json")
