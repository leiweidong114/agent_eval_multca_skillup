from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import SKILLS_ROOT


REGISTRY_ROOT = SKILLS_ROOT / ".registry"
COMPOSED_ROOT = SKILLS_ROOT.parent / ".runtime" / "composed-skills"
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
MAX_FILES = 500
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")


def _safe_name(value: str) -> str:
    value = value.strip().lower()
    if not NAME_RE.fullmatch(value):
        raise ValueError("Skill name must use lowercase letters, digits and hyphens")
    return value


def _metadata_path(name: str) -> Path:
    return REGISTRY_ROOT / name / "versions.json"


def _load_versions(name: str) -> list[dict[str, Any]]:
    path = _metadata_path(name)
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _save_versions(name: str, versions: list[dict[str, Any]]) -> None:
    path = _metadata_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(versions, ensure_ascii=False, indent=2), encoding="utf-8")


def upload_skill(name: str, archive: bytes) -> dict[str, Any]:
    name = _safe_name(name)
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise ValueError("Skill archive exceeds 20 MB")
    digest = hashlib.sha256(archive).hexdigest()
    version = digest[:12]
    versions = _load_versions(name)
    existing = next((item for item in versions if item["version"] == version), None)
    if existing:
        return existing
    REGISTRY_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="skill-upload-") as temp:
        temp_root = Path(temp)
        archive_path = temp_root / "skill.zip"
        archive_path.write_bytes(archive)
        extract_root = temp_root / "extract"
        extract_root.mkdir()
        try:
            with zipfile.ZipFile(archive_path) as bundle:
                members = bundle.infolist()
                if len(members) > MAX_FILES:
                    raise ValueError("Skill archive contains too many files")
                total = 0
                for member in members:
                    path = Path(member.filename.replace("\\", "/"))
                    if path.is_absolute() or ".." in path.parts:
                        raise ValueError("Skill archive contains an unsafe path")
                    if ((member.external_attr >> 16) & 0o170000) == 0o120000:
                        raise ValueError("Symbolic links are not allowed in Skill archives")
                    total += member.file_size
                    if total > MAX_UNPACKED_BYTES:
                        raise ValueError("Expanded Skill exceeds 50 MB")
                    if member.is_dir():
                        continue
                    target = extract_root.joinpath(*path.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
        except zipfile.BadZipFile as exc:
            raise ValueError("Uploaded file is not a valid ZIP archive") from exc
        candidates = list(extract_root.glob("SKILL.md")) + list(extract_root.glob("*/SKILL.md"))
        if len(candidates) != 1:
            raise ValueError("ZIP must contain exactly one root Skill with SKILL.md")
        source_root = candidates[0].parent
        destination = REGISTRY_ROOT / name / version
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_root, destination)
    item = {
        "name": name, "version": version, "skill_id": f"{name}@{version}",
        "path": str(destination), "sha256": digest,
        "uploaded_at": datetime.now().isoformat(), "file_count": len(members),
    }
    versions.append(item)
    _save_versions(name, versions)
    return item


def list_uploaded_skills() -> list[dict[str, Any]]:
    if not REGISTRY_ROOT.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for directory in sorted(REGISTRY_ROOT.iterdir()):
        if directory.is_dir():
            result.extend(_load_versions(directory.name))
    return result


def resolve_skill(identifier: str) -> Path | None:
    if "@" in identifier:
        name, version = identifier.split("@", 1)
        if not NAME_RE.fullmatch(name) or not re.fullmatch(r"[a-f0-9]{12}", version):
            return None
        candidate = (REGISTRY_ROOT / name / version).resolve()
        root = REGISTRY_ROOT.resolve()
    else:
        candidate = (SKILLS_ROOT / identifier).resolve()
        root = SKILLS_ROOT.resolve()
    if candidate != root and root in candidate.parents and (candidate / "SKILL.md").is_file():
        return candidate
    return None


def compose_skills(identifiers: list[str]) -> Path:
    """Build a deterministic Skill bundle that delegates to multiple Skills."""
    if not 2 <= len(identifiers) <= 8:
        raise ValueError("Combined evaluation requires between 2 and 8 Skills")
    resolved: list[tuple[str, Path]] = []
    hasher = hashlib.sha256()
    for identifier in identifiers:
        path = resolve_skill(identifier)
        if path is None:
            raise ValueError(f"Skill not found: {identifier}")
        skill_md = (path / "SKILL.md").read_bytes()
        hasher.update(identifier.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(skill_md)
        resolved.append((identifier, path))
    bundle_name = f"combined-{hasher.hexdigest()[:12]}"
    destination = COMPOSED_ROOT / bundle_name
    if (destination / "SKILL.md").is_file():
        return destination
    temporary = COMPOSED_ROOT / f".{bundle_name}-{datetime.now().timestamp():.0f}"
    temporary.mkdir(parents=True, exist_ok=False)
    lines = [
        "---",
        f"name: {bundle_name}",
        "description: Coordinate multiple selected Skills for one evaluation task.",
        "---",
        "",
        "# Combined Skill Evaluation",
        "",
        "Use every relevant sub-Skill below to complete the user's task. Read each",
        "sub-Skill's `SKILL.md` before acting, reconcile overlapping instructions,",
        "and produce one coherent final result.",
        "",
        "## Selected Skills",
        "",
    ]
    for index, (identifier, source) in enumerate(resolved, start=1):
        folder = f"{index:02d}-{re.sub(r'[^A-Za-z0-9._-]+', '-', identifier)}"
        shutil.copytree(source, temporary / "skills" / folder)
        lines.append(f"- `{identifier}`: `skills/{folder}/SKILL.md`")
    (temporary / "SKILL.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    COMPOSED_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        temporary.replace(destination)
    except FileExistsError:
        shutil.rmtree(temporary, ignore_errors=True)
    return destination


def delete_skill_version(name: str, version: str) -> bool:
    name = _safe_name(name)
    if not re.fullmatch(r"[a-f0-9]{12}", version):
        raise ValueError("Invalid Skill version")
    versions = _load_versions(name)
    remaining = [item for item in versions if item["version"] != version]
    if len(remaining) == len(versions):
        return False
    target = (REGISTRY_ROOT / name / version).resolve()
    if REGISTRY_ROOT.resolve() not in target.parents:
        raise ValueError("Unsafe Skill version path")
    shutil.rmtree(target)
    _save_versions(name, remaining)
    return True
