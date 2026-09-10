from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from agent_eval.agent_adapters import model_adapter
from agent_eval.env_config import effective_environment, load_root_env, update_root_env


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

# Limitations of the pinned Multica v0.4.36 backends. Keeping these explicit
# prevents discovery and preflight checks from advertising a contract that the
# underlying Agent cannot honour.
RUNTIME_MANAGED_MODEL_AGENTS = frozenset({"mcode", "qwenpaw", "zeroclaw"})
UNSUPPORTED_SKILL_INJECTION_AGENTS = frozenset({"dim", "hermes", "zeroclaw"})

# Live-certified on glm-4.5-air. Other installed adapters keep their existing
# model/Skill contract but are not advertised as Subagent-certified until the
# strict probe has been run for them.
SUBAGENT_TRANSPORTS = {
    "claude": "native_agent_tool",
    "codebuddy": "native_agent_tool",
    "codex": "native_spawn_agent",
    "opencode": "native_task_tool",
    "openclaw": "isolated_openclaw_agent_exec",
    "justdo": "isolated_justdo_child_process",
}

def _default_project_root() -> Path:
    # backend/src/agent_eval/runtime.py -> parents[2] = backend
    return Path(__file__).resolve().parents[2]


def load_agent_paths(project_root: Path | None = None) -> dict[str, str]:
    """Load user-selected Agent executables from repository-root ``.env``."""
    root = project_root or _default_project_root()
    raw = load_root_env(root).get("AGENT_PATHS_JSON", "").strip()
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(name): str(executable).strip()
        for name, executable in value.items()
        if str(name) in SUPPORTED_AGENTS and str(executable).strip()
    }


def save_agent_path(
    agent: str,
    executable: str | None,
    *,
    project_root: Path | None = None,
) -> dict[str, str]:
    """Persist one executable override, or remove it when the value is empty."""
    normalized = normalize_agent(agent)
    root = project_root or _default_project_root()
    paths = load_agent_paths(root)
    value = str(executable or "").strip().strip('"')
    if value:
        if len(value) > 4096 or any(char in value for char in "\r\n\0"):
            raise ValueError("Invalid Agent executable path")
        detected = shutil.which(value)
        if detected is None:
            raise ValueError(f"Agent executable was not found or is not executable: {value}")
        paths[normalized] = str(Path(detected).resolve())
    else:
        paths.pop(normalized, None)

    update_root_env(root, {
        "AGENT_PATHS_JSON": (
            json.dumps(paths, ensure_ascii=False, separators=(",", ":")) if paths else None
        )
    })
    return paths


def normalize_agent(value: str) -> str:
    normalized = AGENT_ALIASES.get(value.strip().lower(), value.strip().lower())
    if not normalized:
        raise ValueError("Agent name cannot be empty")
    if normalized not in SUPPORTED_AGENTS:
        raise ValueError(
            f"Unsupported Agent {value!r}; run 'agent-eval agents' for the supported list"
        )
    return normalized


def default_agent_command(agent: str, project_root: Path | None = None) -> str:
    normalized = normalize_agent(agent)
    configured_paths = load_agent_paths(project_root)
    if normalized in configured_paths:
        return configured_paths[normalized]
    if normalized == "justdo":
        configured = effective_environment(project_root or _default_project_root()).get(
            "JUSTDO_AGENT_EXECUTABLE", ""
        ).strip()
        if configured:
            return configured
    if normalized == "justdo":
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
    normalized = normalize_agent(agent)
    if normalized in UNSUPPORTED_SKILL_INJECTION_AGENTS:
        raise ValueError(
            f"Agent {normalized!r} has no direct Skill injection adapter in the local "
            "evaluation runtime"
        )
    root = SKILL_ROOTS[normalized]
    return f"{root}/{skill_name}"


def agent_capabilities(agent: str) -> dict[str, object]:
    normalized = normalize_agent(agent)
    adapter = model_adapter(normalized)
    model_selection = normalized not in RUNTIME_MANAGED_MODEL_AGENTS
    skill_injection = normalized not in UNSUPPORTED_SKILL_INJECTION_AGENTS
    return {
        "agent": normalized,
        "backend_agent": backend_agent(normalized),
        "model_selection": model_selection,
        "model_source": "request" if model_selection else "runtime_managed",
        "skill_injection": skill_injection,
        "skill_root": SKILL_ROOTS.get(normalized) if skill_injection else None,
        "specified_model_and_skill_evaluation": model_selection and skill_injection,
        "subagent_supported": normalized in SUBAGENT_TRANSPORTS,
        "subagent_transport": SUBAGENT_TRANSPORTS.get(normalized),
        "model_adapter": adapter.public_dict(),
    }


def validate_evaluation_capabilities(
    agent: str, *, require_model_selection: bool = True
) -> None:
    capabilities = agent_capabilities(agent)
    if not capabilities["skill_injection"]:
        raise ValueError(
            f"Agent {capabilities['agent']!r} cannot be evaluated with a specified Skill: "
            "the local runtime has no direct Skill injection adapter"
        )
    if require_model_selection and not capabilities["model_selection"]:
        raise ValueError(
            f"Agent {capabilities['agent']!r} cannot be evaluated with a specified model: "
            "the model is managed by the Agent runtime; use "
            "--no-require-model-verification only when that limitation is acceptable"
        )


def find_skill_up(project_root: Path) -> Path:
    environment = effective_environment(project_root)
    repository = project_root.resolve().parent if project_root.resolve().name.lower() == "backend" else project_root.resolve()
    configured = environment.get("SKILLUP_EXECUTABLE", "").strip()
    candidates = []
    if configured:
        configured_path = Path(configured).expanduser()
        candidates.append(configured_path if configured_path.is_absolute() else repository / configured_path)
    candidates.extend([
        project_root
        / ".tools"
        / ("windows" if os.name == "nt" else "linux")
        / ("skill-up.exe" if os.name == "nt" else "skill-up")
    ])
    discovered = shutil.which("skill-up")
    if discovered:
        candidates.append(Path(discovered))
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError("skill-up was not found; run the platform setup script")


def find_multica_runtime(project_root: Path) -> Path:
    environment = effective_environment(project_root)
    repository = project_root.resolve().parent if project_root.resolve().name.lower() == "backend" else project_root.resolve()
    configured = environment.get("MULTICA_EXECUTABLE", "").strip()
    paths = []
    if configured:
        configured_path = Path(configured).expanduser()
        paths.append(configured_path if configured_path.is_absolute() else repository / configured_path)
    paths.extend([
        project_root
        / ".runtime"
        / ("windows" if os.name == "nt" else "linux")
        / "bin"
        / ("multica-eval-runtime.exe" if os.name == "nt" else "multica-eval-runtime")
    ])
    for path in paths:
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError(
        f"Local Multica evaluation runtime was not found: {paths[0]}; run setup first"
    )
