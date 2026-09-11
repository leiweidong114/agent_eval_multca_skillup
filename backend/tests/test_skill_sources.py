from agent_eval.skill_sources import list_external_skills, resolve_external_skill


def test_external_skill_roots_are_resolved_from_the_root_env(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    skill = tmp_path / "private-skills" / "schematic-large"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Private schematic Skill\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        'EXTERNAL_SKILL_PATHS_JSON=["private-skills"]\n',
        encoding="utf-8",
    )

    assert resolve_external_skill(backend, "schematic-large") == skill.resolve()
    assert list_external_skills(backend) == [{
        "name": "schematic-large",
        "identifier": "schematic-large",
        "source": "external",
        "path": str(skill.resolve()),
        "has_skill_md": True,
    }]


def test_external_skill_identifier_cannot_escape_a_configured_root(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    (tmp_path / ".env").write_text(
        'EXTERNAL_SKILL_PATHS_JSON=["private-skills"]\n',
        encoding="utf-8",
    )

    assert resolve_external_skill(backend, "../outside") is None
