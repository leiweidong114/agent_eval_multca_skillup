import io
import zipfile

import pytest

import app.skill_registry as registry


def bundle(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return output.getvalue()


def test_uploaded_skill_is_content_versioned_and_resolvable(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY_ROOT", tmp_path / ".registry")
    monkeypatch.setattr(registry, "SKILLS_ROOT", tmp_path)
    data = bundle({"demo/SKILL.md": "---\nname: demo\ndescription: test\n---\n", "demo/scripts/run.py": "print('ok')"})
    first = registry.upload_skill("demo", data)
    second = registry.upload_skill("demo", data)
    assert first["version"] == second["version"]
    assert registry.resolve_skill(first["skill_id"]).is_dir()
    assert registry.delete_skill_version("demo", first["version"])


def test_uploaded_skill_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY_ROOT", tmp_path / ".registry")
    with pytest.raises(ValueError, match="unsafe path"):
        registry.upload_skill("demo", bundle({"../SKILL.md": "bad"}))
