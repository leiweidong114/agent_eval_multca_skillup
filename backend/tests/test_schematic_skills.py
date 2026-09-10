import importlib.util
import json
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_schematic_skills_define_isolated_justdo_subagents():
    for path in (
        BACKEND / "skills/schematic-pipeline/SKILL.md",
        BACKEND / "skills/schematic-layout-codegen/SKILL.md",
    ):
        text = path.read_text(encoding="utf-8")
        assert 'runtime="subagent"' in text
        assert 'context="isolated"' in text
        assert "省略 `agentId` 与 `model`" in text
        assert '禁止使用 `context="fork"`' in text or '禁止 `context="fork"`' in text
        assert "同一时间只有 1 个子任务活动" in text


def test_validate_sheets_rejects_pin_on_multiple_nets():
    module = _load(
        "validate_sheets",
        BACKEND / "skills/signal-interface-generation/scripts/validate_sheets.py",
    )
    devices = [
        {"library_id": "MCU:X", "pins": [{"number": "1"}, {"number": "2"}]},
        {"library_id": "Device:R", "pins": [{"number": "1"}, {"number": "2"}]},
    ]
    sheets = {"sheets": [{
        "sheet_id": "S1",
        "main_chip": {"instance": "U1", "library_id": "MCU:X"},
        "components": [
            {"instance": "U1", "library_id": "MCU:X", "pins": ["1", "2"], "is_main": True},
            {"instance": "R1", "library_id": "Device:R", "pins": ["1", "2"], "group": "INPUT", "is_main": False},
        ],
        "nets": [
            {"name": "A", "type": "signal", "priority": 1,
             "pins": [{"instance": "U1", "pin": "1"}, {"instance": "R1", "pin": "1"}]},
            {"name": "B", "type": "signal", "priority": 1,
             "pins": [{"instance": "U1", "pin": "2"}, {"instance": "R1", "pin": "1"}]},
        ],
    }]}
    problems = module.validate(sheets, devices)
    assert any("同时属于网络 A 和 B" in problem for problem in problems)


def test_validate_sheets_allows_digit_prefixed_power_rail():
    module = _load(
        "validate_sheets_power_rail",
        BACKEND / "skills/signal-interface-generation/scripts/validate_sheets.py",
    )
    assert module.NET_NAME_RE.fullmatch("3V3")
    assert module.NET_NAME_RE.fullmatch("5V_USB")


def test_layout_refuses_placeholder_subagent_fragment(tmp_path):
    module = _load(
        "layout_sheet",
        BACKEND / "skills/schematic-layout-codegen/scripts/layout_sheet.py",
    )
    (tmp_path / "slices.json").write_text(json.dumps({"slices": [{
        "slice_id": "INPUT", "file_id": "INPUT", "nets": [{
            "name": "ADC", "pins": [{"instance": "U1", "pin": "1"}],
        }],
    }]}), encoding="utf-8")
    (tmp_path / "INPUT.py").write_text("# subagent: placeholder\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        module._fragment_files(tmp_path)


def test_layout_accepts_completed_subagent_fragment(tmp_path):
    module = _load(
        "layout_sheet_completed",
        BACKEND / "skills/schematic-layout-codegen/scripts/layout_sheet.py",
    )
    (tmp_path / "slices.json").write_text(json.dumps({"slices": [{
        "slice_id": "INPUT", "file_id": "INPUT", "nets": [{
            "name": "ADC", "pins": [{"instance": "U1", "pin": "1"}],
        }],
    }]}), encoding="utf-8")
    fragment = tmp_path / "INPUT.py"
    fragment.write_text("connect([pin(circuit.U1, 1)], circuit.ADC)\n", encoding="utf-8")
    assert module._fragment_files(tmp_path) == [fragment]


def test_layout_accepts_dunder_getattr_for_digit_prefixed_network(tmp_path):
    module = _load(
        "layout_sheet_numeric_net",
        BACKEND / "skills/schematic-layout-codegen/scripts/layout_sheet.py",
    )
    (tmp_path / "slices.json").write_text(json.dumps({"slices": [{
        "slice_id": "POWER", "file_id": "POWER", "nets": [{
            "name": "3V3", "pins": [
                {"instance": "U1", "pin": "2"}, {"instance": "R1", "pin": "1"},
            ],
        }],
    }]}), encoding="utf-8")
    fragment = tmp_path / "POWER.py"
    fragment.write_text(
        'connect([pin(circuit.U1, 2), pin(circuit.R1, 1)], circuit.__getattr__("3V3"))\n',
        encoding="utf-8",
    )
    assert module._fragment_files(tmp_path) == [fragment]


def test_layout_rejects_networks_from_another_slice(tmp_path):
    module = _load(
        "layout_sheet_wrong_slice",
        BACKEND / "skills/schematic-layout-codegen/scripts/layout_sheet.py",
    )
    (tmp_path / "slices.json").write_text(json.dumps({"slices": [{
        "slice_id": "INPUT", "file_id": "INPUT", "nets": [{
            "name": "ADC", "pins": [
                {"instance": "U1", "pin": "1"}, {"instance": "R1", "pin": "2"},
            ],
        }],
    }]}), encoding="utf-8")
    (tmp_path / "INPUT.py").write_text(
        "connect([pin(circuit.U1, 1)], circuit.WRONG)\n", encoding="utf-8"
    )
    with pytest.raises(SystemExit):
        module._fragment_files(tmp_path)


def test_codegen_omits_groups_without_assigned_networks():
    module = _load(
        "codegen_base",
        BACKEND / "skills/schematic-layout-codegen/scripts/codegen_base.py",
    )
    sheet = {
        "components": [
            {"instance": "U1", "is_main": True},
            {"instance": "R1", "group": "A", "is_main": False},
            {"instance": "R2", "group": "B", "is_main": False},
        ],
        "nets": [{"name": "N", "pins": [
            {"instance": "U1", "pin": "1"}, {"instance": "R1", "pin": "1"},
        ]}],
    }
    assert [item["slice_id"] for item in module.pick_slices(sheet)] == ["A"]


def test_bundled_script_keeps_parent_relative_out_path_in_workspace(tmp_path):
    module = _load(
        "fetch_catalog_workspace_path",
        BACKEND / "skills/signal-interface-generation/scripts/fetch_catalog.py",
    )
    module.__file__ = str(
        tmp_path / ".agents/skills/pipeline/skills/signal/scripts/fetch_catalog.py"
    )
    resolved = module._workspace_path(Path("../../../../out/catalog.json"))
    assert resolved == tmp_path / "out/catalog.json"
