from __future__ import annotations

import os
import shutil
from pathlib import Path


SUPPORTED_AGENTS = (
    "antigravity", "claude", "codebuddy", "codex", "copilot", "cursor",
    "deveco", "dim", "dsh", "grok", "hermes", "kimi", "kiro", "mcode",
    "justdo", "omp", "openclaw", "opencode", "pi", "qoder", "qoderclicn", "qwen",
    "qwenpaw", "reasonix", "traecli", "zeroclaw",
)

AGENT_ALIASES = {
    "claude_code": "claude",
    "qwen_code": "qwen",
    "qodercli": "qoder",
}

AGENT_COMMANDS = {
    "antigravity": "agy",
    "claude": "claude",
    "codebuddy": "codebuddy",
    "codex": "codex",
    "copilot": "copilot",
    "cursor": "cursor-agent",
    "deveco": "deveco",
    "dim": "dim",
    "dsh": "dsh",
    "grok": "grok",
    "hermes": "hermes",
    "kimi": "kimi",
    "kiro": "kiro-cli",
    "justdo": "JustDo-agent",
    "mcode": "mcode",
    "omp": "omp",
    "openclaw": "openclaw",
    "opencode": "opencode",
    "pi": "pi",
    "qoder": "qodercli",
    "qoderclicn": "qoderclicn",
    "qwen": "qwen",
    "qwenpaw": "qwenpaw",
    "reasonix": "reasonix",
    "traecli": "traecli",
    "zeroclaw": "zeroclaw",
}

SKILL_ROOTS = {
    "antigravity": ".agents/skills",
    "claude": ".claude/skills",
    "codebuddy": ".codebuddy/skills",
    # The local runtime keeps the user's existing CODEX_HOME/auth untouched;
    # Codex discovers project-scoped Skills from .agents/skills.
    "codex": ".agents/skills",
    "copilot": ".github/skills",
    "cursor": ".cursor/skills",
    "deveco": ".deveco/skills",
    "grok": ".grok/skills",
    "kimi": ".kimi/skills",
    "kiro": ".kiro/skills",
    "justdo": "skills",
    "mcode": ".minimax/skills",
    "omp": ".omp/skills",
    "dsh": ".dsh/skills",
    "openclaw": "skills",
    "opencode": ".opencode/skills",
    "pi": ".pi/skills",
    "qoder": ".qoder/skills",
    "qoderclicn": ".qoder/skills",
    "qwen": ".qwen/skills",
    "qwenpaw": "skill_pool",
    "reasonix": ".reasonix/skills",
    "traecli": ".traecli/skills",
}


def normalize_agent(value: str) -> str:
    normalized = AGENT_ALIASES.get(value.strip().lower(), value.strip().lower())
    if not normalized:
        raise ValueError("Agent name cannot be empty")
    if normalized not in SUPPORTED_AGENTS:
        raise ValueError(
            f"Unsupported Agent {value!r}; run 'agent-eval agents' for the supported list"
        )
    return normalized


def default_agent_command(agent: str) -> str:
    normalized = normalize_agent(agent)
    if normalized == "justdo":
        configured = os.environ.get("JUSTDO_AGENT_EXECUTABLE", "").strip()
        if configured:
            return configured
        if os.name == "nt":
            appdata = os.environ.get("APPDATA", "").strip()
            if appdata:
                candidate = Path(appdata) / "JustDo" / "multica" / "development" / "JustDo-agent.exe"
                if candidate.is_file():
                    return str(candidate)
    return AGENT_COMMANDS.get(normalized, normalized)


def backend_agent(agent: str) -> str:
    """Return the Multica backend used to execute a user-facing Agent choice."""
    normalized = normalize_agent(agent)
    return "openclaw" if normalized == "justdo" else normalized


def skill_target(agent: str, skill_name: str) -> str:
    root = SKILL_ROOTS.get(normalize_agent(agent), ".agents/skills")
    return f"{root}/{skill_name}"


def find_skill_up(project_root: Path) -> Path:
    roots = [project_root]
    if project_root.name == "backend":
        # Keep existing developer installations working during the backend/
        # directory migration. Fresh setup scripts install under backend/.
        roots.append(project_root.parent)
    candidates = [
        root
        / ".tools"
        / ("windows" if os.name == "nt" else "linux")
        / ("skill-up.exe" if os.name == "nt" else "skill-up")
        for root in roots
    ]
    discovered = shutil.which("skill-up")
    if discovered:
        candidates.append(Path(discovered))
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError("skill-up was not found; run the platform setup script")


def find_multica_runtime(project_root: Path) -> Path:
    roots = [project_root]
    if project_root.name == "backend":
        roots.append(project_root.parent)
    paths = [
        root
        / ".runtime"
        / ("windows" if os.name == "nt" else "linux")
        / "bin"
        / ("multica-eval-runtime.exe" if os.name == "nt" else "multica-eval-runtime")
        for root in roots
    ]
    for path in paths:
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError(
        f"Local Multica evaluation runtime was not found: {paths[0]}; run setup first"
    )
