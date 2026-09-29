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
    "qwenpaw", "reasonix", "traecli", "zcode", "zeroclaw",
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
    "zcode": "zcode",
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
    "zcode": ".zcode/skills",
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
        if detected is None and normalized == "zcode" and Path(value).is_file():
            detected = str(Path(value).resolve())
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


def justdo_agent_command(
    project_root: Path | None = None, *, transport: str = "auto"
) -> str:
    """Resolve the JustDo executable for an explicit CLI or HTTP invocation."""
    root = project_root or _default_project_root()
    normalized_transport = str(transport or "auto").strip().lower()
    if normalized_transport not in {"auto", "cli", "http"}:
        raise ValueError("justdo_transport must be auto, cli, or http")
    environment = effective_environment(root)
    if normalized_transport in {"auto", "http"} and environment.get("JUSTDO_HTTP_URL", "").strip():
        platform_dir = "windows" if os.name == "nt" else "linux"
        binary = "justdo-http-agent.exe" if os.name == "nt" else "justdo-http-agent"
        proxy = root / ".runtime" / platform_dir / "bin" / binary
        if proxy.is_file():
            return str(proxy)
        if normalized_transport == "http":
            raise FileNotFoundError(
                f"JustDo HTTP proxy was not found: {proxy}; run the platform build script"
            )
    if normalized_transport == "http":
        raise ValueError("JUSTDO_HTTP_URL must be configured before using JustDo HTTP")
    configured_paths = load_agent_paths(root)
    if "justdo" in configured_paths and shutil.which(configured_paths["justdo"]):
        return configured_paths["justdo"]
    configured = environment.get("JUSTDO_AGENT_EXECUTABLE", "").strip()
    if configured and shutil.which(configured):
        return configured
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            candidate = Path(appdata) / "JustDo" / "multica" / "development" / "JustDo-agent.exe"
            if candidate.is_file():
                return str(candidate)
    return AGENT_COMMANDS["justdo"]


def zcode_agent_command(
    project_root: Path | None = None, *, transport: str = "auto"
) -> str:
    """Resolve zcode-app-cli, the bundled runtime, or the Desktop task bridge."""
    root = project_root or _default_project_root()
    normalized_transport = str(transport or "auto").strip().lower()
    if normalized_transport not in {"auto", "app-cli", "desktop", "desktop-ui"}:
        raise ValueError("zcode_transport must be auto, app-cli, desktop, or desktop-ui")
    environment = effective_environment(root)
    configured_paths = load_agent_paths(root)

    def existing(value: str | None) -> str | None:
        candidate = str(value or "").strip().strip('"')
        if not candidate:
            return None
        discovered = shutil.which(candidate)
        if discovered:
            return str(Path(discovered).resolve())
        path = Path(candidate).expanduser()
        return str(path.resolve()) if path.is_file() else None

    app_cli = (
        existing(environment.get("ZCODE_APP_CLI_EXECUTABLE"))
        or existing(configured_paths.get("zcode"))
        or existing("zcode")
        or existing("zcode-app-cli")
    )
    desktop = existing(environment.get("ZCODE_DESKTOP_CLI_EXECUTABLE"))
    if desktop is None and os.name == "nt":
        roots = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ZCode",
            Path(os.environ.get("LOCALAPPDATA", "")) / "ZCode",
            Path(os.environ.get("ProgramFiles", "")) / "ZCode",
        ]
        for install_root in roots:
            for relative in (
                Path("resources/glm/zcode.cjs"),
                Path("resources/app/resources/glm/zcode.cjs"),
            ):
                candidate = install_root / relative
                if candidate.is_file():
                    desktop = str(candidate.resolve())
                    break
            if desktop:
                break
        if desktop is None:
            try:
                import winreg

                registry_roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
                uninstall_paths = (
                    r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                    r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
                )
                for hive in registry_roots:
                    for uninstall_path in uninstall_paths:
                        try:
                            with winreg.OpenKey(hive, uninstall_path) as uninstall_key:
                                for index in range(winreg.QueryInfoKey(uninstall_key)[0]):
                                    with winreg.OpenKey(uninstall_key, winreg.EnumKey(uninstall_key, index)) as item:
                                        try:
                                            display_name = str(winreg.QueryValueEx(item, "DisplayName")[0])
                                        except OSError:
                                            continue
                                        if display_name.casefold().split()[0] != "zcode":
                                            continue
                                        try:
                                            icon = str(winreg.QueryValueEx(item, "DisplayIcon")[0]).strip('"')
                                        except OSError:
                                            icon = ""
                                        install_root = Path(icon).parent if icon else None
                                        candidate = install_root / "resources" / "glm" / "zcode.cjs" if install_root else None
                                        if candidate and candidate.is_file():
                                            desktop = str(candidate.resolve())
                                            break
                        except OSError:
                            continue
                        if desktop:
                            break
                    if desktop:
                        break
            except (ImportError, OSError, IndexError):
                pass
    if normalized_transport == "app-cli":
        return app_cli or AGENT_COMMANDS["zcode"]
    if normalized_transport == "desktop-ui":
        bridge = Path(__file__).resolve().parent / "zcode_desktop_bridge" / "bridge.cjs"
        if not desktop:
            return environment.get("ZCODE_DESKTOP_CLI_EXECUTABLE", "zcode")
        return str(bridge)
    if normalized_transport == "desktop":
        return desktop or environment.get("ZCODE_DESKTOP_CLI_EXECUTABLE", "zcode")
    return app_cli or desktop or AGENT_COMMANDS["zcode"]


def zcode_desktop_runtime(project_root: Path | None = None) -> str | None:
    """Resolve only the ZCode Desktop bundled runtime, without falling back."""
    root = project_root or _default_project_root()
    command = zcode_agent_command(root, transport="desktop")
    discovered = shutil.which(str(command or ""))
    path = Path(discovered).resolve() if discovered else Path(str(command or "")).expanduser().resolve()
    return str(path) if path.is_file() and path.suffix.lower() in {".js", ".cjs"} else None


def resolve_project_executable(value: str, project_root: Path) -> str:
    """Resolve a command or repository-relative executable before changing cwd."""
    command = str(value or "").strip().strip('"') or "node"
    path = Path(command).expanduser()
    if path.is_absolute():
        return str(path.resolve()) if path.is_file() else command
    # The CLI treats backend/ as its project root, while .env paths are often
    # written relative to the repository root (for example
    # backend/.runtime/windows/node/node.exe). Support both conventions before
    # the Agent changes cwd to its isolated run workspace.
    for base in (project_root, project_root.parent):
        candidate = (base / path).resolve()
        if candidate.is_file():
            return str(candidate)
    discovered = shutil.which(command)
    if discovered:
        return str(Path(discovered).resolve())
    return command


def zcode_builtin_provider_config(executable: str) -> Path | None:
    """Locate the provider catalog beside a ZCode Desktop bundled runtime."""
    runtime_path = Path(str(executable or "")).expanduser()
    if runtime_path.suffix.lower() not in {".js", ".cjs"}:
        return None
    candidates = [
        runtime_path.parent / "provider" / "zcode-builtin.json",
        runtime_path.parent.parent / "config" / "provider" / "zcode-builtin.json",
    ]
    return next((item.resolve() for item in candidates if item.is_file()), None)


def default_agent_command(agent: str, project_root: Path | None = None) -> str:
    normalized = normalize_agent(agent)
    root = project_root or _default_project_root()
    configured_paths = load_agent_paths(project_root)
    if normalized == "justdo":
        return justdo_agent_command(root, transport="auto")
    if normalized == "zcode":
        return zcode_agent_command(root, transport="auto")
    if normalized in configured_paths and shutil.which(configured_paths[normalized]):
        return configured_paths[normalized]
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
