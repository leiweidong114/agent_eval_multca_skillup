from __future__ import annotations

import json
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

import yaml
import httpx

from agent_eval.agent_adapters import AGENT_MODEL_ADAPTERS, model_adapter
from agent_eval.env_config import effective_environment, load_root_env, update_root_env
from agent_eval.failure import describe_evaluation_failure
from agent_eval.schematic_tasks import normalize_schematic_task_profiles


PROFILE_PROTOCOLS = frozenset(
    {"openai_compatible", "openai_chat", "openai_responses", "anthropic_messages"}
)
PROFILE_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
CREDENTIAL_ENV_NAMES = (
    "LITELLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN", "MINIMAX_API_KEY", "GOOGLE_API_KEY",
    "GEMINI_API_KEY", "XAI_API_KEY", "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY",
    "ZHIPUAI_API_KEY", "OPENROUTER_API_KEY",
)


@dataclass(frozen=True)
class ResolvedModelProfile:
    name: str
    model: str
    api_base: str
    environment: dict[str, str]
    agent_args: tuple[str, ...]
    agent_models: dict[str, str] = field(default_factory=dict)
    gateway_models: dict[str, str] = field(default_factory=dict)
    protocol: str = "openai_compatible"
    api_key_env: str = "LITELLM_API_KEY"
    context_window: int = 200000
    max_output_tokens: int = 32000

    def model_for_agent(self, agent: str) -> str:
        configured = self.agent_models.get(agent)
        if configured:
            return configured
        if agent == "openclaw":
            return "main"
        if agent == "claude" and self.api_base:
            # Claude Code accepts its stable family aliases at the CLI layer;
            # the corresponding environment mapping below selects the actual
            # LiteLLM deployment without requiring a native Anthropic model.
            return "claude-sonnet-4-6"
        if agent == "codebuddy" and self.api_base:
            # A per-run models.json entry uses the provider model id verbatim,
            # so CodeBuddy sends that exact id to the LiteLLM gateway instead
            # of resolving a similarly named model from the user's Token Plan.
            return f"custom-local:{self.model}"
        if agent == "opencode" and self.api_base:
            return f"litellm/{self.gateway_model_for_agent(agent)}"
        return self.model

    def gateway_model_for_agent(self, agent: str) -> str:
        """Model id emitted on the Agent's HTTP request to LiteLLM."""
        return self.gateway_models.get(agent) or self.model


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError(f"Model configuration must be a mapping: {path}")
    return value


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write_yaml(path: Path, value: dict[str, Any]) -> None:
    _atomic_write(path, yaml.safe_dump(value, allow_unicode=True, sort_keys=False))


def load_runtime_settings(project_root: Path) -> dict[str, Any]:
    """Load mutable Web/CLI preferences from repository-root ``.env``."""
    environment = effective_environment(project_root)
    settings: dict[str, Any] = {}
    for setting, variable in (
        ("judge_model", "LITELLM_JUDGE_MODEL"),
        ("agent_test_model", "AGENT_TEST_MODEL"),
    ):
        value = str(environment.get(variable) or "").strip()
        if value:
            settings[setting] = value
    raw_skills = str(environment.get("SCHEMATIC_SKILLS_JSON") or "").strip()
    legacy_skills: list[str] | None = None
    if raw_skills:
        try:
            skills = json.loads(raw_skills)
        except ValueError:
            skills = None
        if isinstance(skills, list):
            legacy_skills = [
                str(item).strip() for item in skills if str(item).strip()
            ][:8]
            settings["schematic_skills"] = legacy_skills
    raw_profiles = str(environment.get("SCHEMATIC_TASK_PROFILES_JSON") or "").strip()
    try:
        configured_profiles = json.loads(raw_profiles) if raw_profiles else {}
    except ValueError:
        configured_profiles = {}
    settings["schematic_task_profiles"] = normalize_schematic_task_profiles(
        configured_profiles,
        legacy_skills=legacy_skills,
        legacy_evaluator=str(environment.get("DEFAULT_SCHEMATIC_EVALUATOR") or "").strip() or None,
    )
    return settings


def save_runtime_settings(project_root: Path, values: Mapping[str, object]) -> dict[str, Any]:
    """Persist model choices without changing credentials or provider routing."""
    settings: dict[str, Any] = {}
    for name in ("judge_model", "agent_test_model"):
        value = str(values.get(name) or "").strip()
        if not value:
            raise ValueError(f"{name} is required")
        if len(value) > 300 or any(char in value for char in "\r\n\0"):
            raise ValueError(f"Invalid {name}")
        settings[name] = value
    profiles = normalize_schematic_task_profiles(
        values.get("schematic_task_profiles"),
        legacy_skills=(
            values.get("schematic_skills")
            if isinstance(values.get("schematic_skills"), list)
            else None
        ),
    )
    for task_type, task_profile in profiles.items():
        profile_skills = task_profile["skills"]
        if not profile_skills or len(profile_skills) > 8:
            raise ValueError(f"{task_type}.skills must contain 1 to 8 Skill identifiers")
        if any(
            len(item) > 300 or any(char in item for char in "\r\n\0")
            for item in profile_skills
        ):
            raise ValueError(f"Invalid Skill identifier in {task_type}")
        evaluator_id = str(task_profile["evaluator_id"])
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", evaluator_id):
            raise ValueError(f"Invalid evaluator_id in {task_type}")
    raw_skills = profiles["block_to_schematic"]["skills"]
    if not isinstance(raw_skills, list) or not raw_skills:
        raise ValueError("schematic_skills is required")
    schematic_skills = list(dict.fromkeys(str(item).strip() for item in raw_skills if str(item).strip()))
    if not schematic_skills or len(schematic_skills) > 8:
        raise ValueError("schematic_skills must contain 1 to 8 Skill identifiers")
    if any(len(item) > 300 or any(char in item for char in "\r\n\0") for item in schematic_skills):
        raise ValueError("Invalid schematic_skills")
    settings["schematic_skills"] = schematic_skills
    settings["schematic_task_profiles"] = profiles
    update_root_env(project_root, {
        "LITELLM_JUDGE_MODEL": settings["judge_model"],
        "AGENT_TEST_MODEL": settings["agent_test_model"],
        "SCHEMATIC_SKILLS_JSON": json.dumps(schematic_skills, ensure_ascii=False),
        "DEFAULT_SCHEMATIC_EVALUATOR": profiles["block_to_schematic"]["evaluator_id"],
        "SCHEMATIC_TASK_PROFILES_JSON": json.dumps(
            profiles, ensure_ascii=False, separators=(",", ":")
        ),
    })
    return settings


def _validate_profile_name(name: str) -> str:
    normalized = name.strip()
    if not PROFILE_NAME_RE.fullmatch(normalized):
        raise ValueError(
            "Profile name must start with a letter and contain only letters, numbers, _ or -"
        )
    return normalized


def _validate_env_name(name: str) -> str:
    normalized = name.strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized):
        raise ValueError("api_key_env must be a valid environment variable name")
    return normalized


def _positive_int(value: object, *, field_name: str, default: int) -> int:
    if value in (None, ""):
        return default
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{field_name} must be greater than zero")
    return parsed


def load_model_config(project_root: Path) -> dict[str, Any]:
    config_dir = project_root / "config"
    config = _read_yaml(config_dir / "models.yaml")
    raw_profiles = load_root_env(project_root).get("MODEL_PROFILES_JSON", "").strip()
    if raw_profiles:
        try:
            profiles = json.loads(raw_profiles)
        except ValueError as exc:
            raise ValueError("MODEL_PROFILES_JSON must be valid JSON") from exc
        if not isinstance(profiles, dict):
            raise ValueError("MODEL_PROFILES_JSON must be a JSON object")
        config = _merge(config, {"profiles": profiles})
    default_profile = load_root_env(project_root).get("LITELLM_PROFILE", "").strip()
    if default_profile:
        config["default_profile"] = default_profile
    return config


def _normalized_base_url(value: str) -> tuple[str, str]:
    raw = value.strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Model profile api_base must be an absolute HTTP(S) URL")
    path = parsed.path.rstrip("/")
    openai_path = path if path.endswith("/v1") else f"{path}/v1"
    openai_base = urlunsplit((parsed.scheme, parsed.netloc, openai_path, "", ""))
    anthropic_path = path[:-3] if path.endswith("/v1") else path
    anthropic_base = urlunsplit((parsed.scheme, parsed.netloc, anthropic_path, "", ""))
    return openai_base.rstrip("/"), anthropic_base.rstrip("/")


def _codebuddy_custom_model(model: str) -> str:
    raw_leaf = model.rsplit("/", 1)[-1]
    leaf = raw_leaf.lower()
    known = {
        "minimax-m3": "MiniMax-M3",
        "minimax-m2.7": "MiniMax-M2.7",
        "minimax-m2.5": "MiniMax-M2.5",
        "minimax-m2.1": "MiniMax-M2.1",
        "grok-4.5": "grok-4.5",
    }
    # CodeBuddy loads this alias from the isolated models.json generated for
    # each run. Known ids retain their canonical spelling; any other LiteLLM
    # deployment can safely use its leaf id because the compatibility proxy
    # rewrites it to the full gateway model id before forwarding the request.
    resolved = known.get(leaf, raw_leaf)
    return f"custom-local:{resolved}"


def resolve_model_profile(
    project_root: Path,
    *,
    profile_name: str | None = None,
    model_override: str | None = None,
    agent: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ResolvedModelProfile:
    config = load_model_config(project_root)
    source_environment = effective_environment(project_root, environ)
    requested_profile = profile_name or source_environment.get("LITELLM_PROFILE")
    unified_litellm = config.get("litellm")
    profiles = config.get("profiles") or {}
    if requested_profile in (None, "", "litellm") and isinstance(unified_litellm, dict):
        selected = "litellm"
        profile = dict(unified_litellm)
    else:
        selected = requested_profile or str(config.get("default_profile") or "").strip()
        if not selected or not isinstance(profiles, dict) or selected not in profiles:
            raise ValueError(f"Unknown or missing model profile: {selected or '<empty>'}")
        raw_profile = profiles[selected]
        profile = dict(raw_profile) if isinstance(raw_profile, dict) else raw_profile
    if not isinstance(profile, dict):
        raise ValueError(f"Model profile must be a mapping: {selected}")

    if selected == "litellm":
        scalar_overrides = {
            "model": "LITELLM_MODEL",
            "api_base": "LITELLM_API_BASE",
            "protocol": "LITELLM_PROTOCOL",
            "context_window": "LITELLM_CONTEXT_WINDOW",
            "max_output_tokens": "LITELLM_MAX_OUTPUT_TOKENS",
        }
        for field_name, variable in scalar_overrides.items():
            value = str(source_environment.get(variable) or "").strip()
            if value:
                profile[field_name] = value
        if "LITELLM_REASONING" in source_environment:
            profile["reasoning"] = str(source_environment["LITELLM_REASONING"]).strip().lower() in {
                "1", "true", "yes", "on"
            }
        for field_name, variable in (
            ("agent_models", "LITELLM_AGENT_MODELS_JSON"),
            ("gateway_models", "LITELLM_GATEWAY_MODELS_JSON"),
        ):
            raw_mapping = str(source_environment.get(variable) or "").strip()
            if raw_mapping:
                try:
                    parsed_mapping = json.loads(raw_mapping)
                except ValueError as exc:
                    raise ValueError(f"{variable} must be valid JSON") from exc
                if not isinstance(parsed_mapping, dict):
                    raise ValueError(f"{variable} must be a JSON object")
                profile[field_name] = parsed_mapping

    default_model = source_environment.get("LITELLM_MODEL") if selected == "litellm" else None
    model = (model_override or default_model or str(profile.get("model") or "")).strip()
    if not model:
        raise ValueError(f"Model profile has no model: {selected}")
    if selected == "litellm" and "no-thinking" in model.lower():
        raise ValueError(
            "The unified LiteLLM gateway keeps reasoning enabled; select a reasoning model"
        )
    reasoning_enabled = bool(profile.get("reasoning", True))
    if selected == "litellm" and not reasoning_enabled:
        raise ValueError("The unified LiteLLM gateway must keep reasoning enabled")
    if str(profile.get("type") or "").strip().lower() == "native":
        return ResolvedModelProfile(
            selected, model, "", {}, (), {}, {}, protocol="native", api_key_env=""
        )
    protocol = str(profile.get("protocol") or "openai_compatible").strip().lower()
    if protocol not in PROFILE_PROTOCOLS:
        raise ValueError(
            f"Unsupported model profile protocol {protocol!r}; expected one of "
            f"{', '.join(sorted(PROFILE_PROTOCOLS))}"
        )
    if agent and protocol != "openai_compatible":
        adapter_protocol = model_adapter(agent).client_protocol
        compatible_protocol = {
            "openai_chat": "openai_compatible",
            "openai_responses": "openai_responses",
            "anthropic_messages": "anthropic_messages",
        }[protocol]
        if adapter_protocol != compatible_protocol:
            raise ValueError(
                f"Model profile {selected!r} exposes {protocol}, but Agent {agent!r} "
                f"requires {adapter_protocol}; use an openai_compatible gateway profile"
            )
    env_api_base = source_environment.get("LITELLM_API_BASE") if selected == "litellm" else None
    api_base = str(env_api_base or profile.get("api_base") or "").strip()
    openai_base, anthropic_base = _normalized_base_url(api_base)

    key_name = _validate_env_name(str(profile.get("api_key_env") or "LITELLM_API_KEY"))
    context_window = _positive_int(
        profile.get("context_window"), field_name="context_window", default=200000
    )
    max_output_tokens = _positive_int(
        profile.get("max_output_tokens"), field_name="max_output_tokens", default=32000
    )
    api_key = str(
        source_environment.get(key_name)
        or ""
    ).strip()
    if not api_key:
        raise ValueError(
            f"Model profile {selected!r} requires {key_name}; set it in repository-root .env"
        )

    agent_models = profile.get("agent_models") or {}
    gateway_models = profile.get("gateway_models") or {}
    if not isinstance(agent_models, dict) or not isinstance(gateway_models, dict):
        raise ValueError(f"agent_models and gateway_models must be mappings: {selected}")
    agent_models = {
        str(name).strip().lower(): str(value).strip()
        for name, value in agent_models.items()
        if str(name).strip() and str(value).strip()
    }
    gateway_models = {
        str(name).strip().lower(): str(value).strip()
        for name, value in gateway_models.items()
        if str(name).strip() and str(value).strip()
    }
    configured_model = str(profile.get("model") or "").strip()
    if selected == "litellm":
        # The unified protocol adapter targets the requested deployment exactly.
        # Legacy default-model aliases must not silently redirect that selection.
        gateway_models = {}
    if model != configured_model and agent:
        # Agent/gateway mappings describe aliases for the configured default
        # deployment. They must never replace an explicit CLI model choice.
        gateway_models.pop(agent, None)
    if agent == "codebuddy":
        derived_codebuddy_model = _codebuddy_custom_model(model)
        if model_override or default_model:
            agent_models["codebuddy"] = derived_codebuddy_model
        elif "codebuddy" not in agent_models:
            agent_models["codebuddy"] = derived_codebuddy_model
    gateway_model = gateway_models.get(agent or "", model)

    # Multica backends launch different Agent CLIs. These aliases cover the
    # OpenAI-compatible and Anthropic-compatible conventions used by them.
    environment = {
        "LITELLM_API_KEY": api_key,
        "OPENAI_API_KEY": api_key,
        "OPENAI_BASE_URL": openai_base,
        "ANTHROPIC_API_KEY": api_key,
        "ANTHROPIC_AUTH_TOKEN": api_key,
        "ANTHROPIC_BASE_URL": anthropic_base,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": gateway_model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": gateway_model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": gateway_model,
        "MINIMAX_API_KEY": api_key,
        "MINIMAX_BASE_URL": openai_base,
        "GOOGLE_API_KEY": api_key,
        "GEMINI_API_KEY": api_key,
        "GOOGLE_GEMINI_BASE_URL": openai_base,
        "XAI_API_KEY": api_key,
        "DEEPSEEK_API_KEY": api_key,
        "DASHSCOPE_API_KEY": api_key,
        "MOONSHOT_API_KEY": api_key,
        "KIMI_API_KEY": api_key,
        "ZHIPUAI_API_KEY": api_key,
        "OPENROUTER_API_KEY": api_key,
        "AGENT_EVAL_PROVIDER_PROTOCOL": protocol,
        "AGENT_EVAL_PROVIDER_BASE_URL": api_base,
        "AGENT_EVAL_PROVIDER_MODEL": gateway_model,
        "AGENT_EVAL_REASONING_ENABLED": "true" if reasoning_enabled else "false",
    }
    environment[key_name] = api_key
    # Claude Code 2.1.248 --bare explicitly authenticates with
    # ANTHROPIC_API_KEY. Keep both Anthropic forms: LiteLLM accepts x-api-key
    # while older Claude releases may still prefer the bearer token variable.
    environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(
        {
            "provider": {
                "litellm": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "LiteLLM",
                    "options": {
                        "baseURL": openai_base,
                        "apiKey": "{env:LITELLM_API_KEY}",
                    },
                    "models": {
                        gateway_model: {
                            "name": gateway_model,
                            "reasoning": True,
                            "limit": {
                                "context": context_window,
                                "output": max_output_tokens,
                            },
                        }
                    },
                }
            }
        },
        ensure_ascii=False,
    )
    agent_args: tuple[str, ...] = ()
    if agent == "claude":
        # Prevent user keychain/apiKeyHelper settings from overriding the
        # gateway credentials supplied for this isolated evaluation process.
        # Session-title and other background traffic can reject non-Anthropic
        # gateway aliases after the actual task has already completed.
        # Keep Claude's own model identity on a family alias; the per-run
        # compatibility proxy rewrites every HTTP request to gateway_model.
        claude_cli_model = agent_models.get("claude") or "sonnet"
        environment["ANTHROPIC_DEFAULT_SONNET_MODEL"] = claude_cli_model
        environment["ANTHROPIC_DEFAULT_OPUS_MODEL"] = claude_cli_model
        environment["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = claude_cli_model
        environment["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        # The evaluation already uses an isolated CLAUDE_CONFIG_DIR, so
        # ``--bare`` is unnecessary and would remove Claude Code's native
        # Agent tool. Keep all built-ins available for subagent evaluations.
        agent_args = ("--tools", "default", "--forward-subagent-text")
    elif agent == "codex":
        # Codex with an existing ChatGPT login otherwise keeps using the
        # built-in OpenAI provider even when OPENAI_BASE_URL is set.
        agent_args = (
            "-c",
            'model_provider="litellm"',
            "-c",
            'model_providers.litellm.name="LiteLLM"',
            "-c",
            f'model_providers.litellm.base_url="{openai_base}"',
            "-c",
            'model_providers.litellm.env_key="LITELLM_API_KEY"',
            "-c",
            'model_providers.litellm.wire_api="responses"',
            "-c",
            'model_reasoning_effort="high"',
            "-c",
            'web_search="disabled"',
        )
    return ResolvedModelProfile(
        selected,
        model,
        api_base,
        environment,
        agent_args,
        agent_models,
        gateway_models,
        protocol=protocol,
        api_key_env=key_name,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
    )


def load_env_secrets(project_root: Path) -> dict[str, str]:
    """Compatibility alias: deployment values now come only from root ``.env``."""
    return load_root_env(project_root)


def resolve_config_secret(
    project_root: Path,
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    return str(effective_environment(project_root, environ).get(name) or "").strip()


def _profile_compatible_agents(protocol: str) -> list[str]:
    if protocol == "openai_compatible":
        return sorted(AGENT_MODEL_ADAPTERS)
    expected = {
        "openai_chat": "openai_compatible",
        "openai_responses": "openai_responses",
        "anthropic_messages": "anthropic_messages",
    }.get(protocol)
    return sorted(
        name
        for name, adapter in AGENT_MODEL_ADAPTERS.items()
        if adapter.client_protocol == expected
    )


def list_model_profiles(project_root: Path) -> list[dict[str, Any]]:
    """Return editable, non-secret provider profiles and their Agent coverage."""
    config = load_model_config(project_root)
    environment = load_root_env(project_root)
    raw_profiles = config.get("profiles") or {}
    profiles = dict(raw_profiles) if isinstance(raw_profiles, dict) else {}
    if isinstance(config.get("litellm"), dict):
        profiles["litellm"] = config["litellm"]
    raw_local_profiles = environment.get("MODEL_PROFILES_JSON", "").strip()
    try:
        local_profiles = json.loads(raw_local_profiles) if raw_local_profiles else {}
    except ValueError:
        local_profiles = {}
    if not isinstance(profiles, dict):
        return []
    result: list[dict[str, Any]] = []
    for name, raw in sorted(profiles.items()):
        if not isinstance(raw, dict):
            continue
        profile_type = str(raw.get("type") or "compatible").strip().lower()
        protocol = "native" if profile_type == "native" else str(
            raw.get("protocol") or "openai_compatible"
        ).strip().lower()
        key_name = "" if profile_type == "native" else str(
            raw.get("api_key_env") or "LITELLM_API_KEY"
        ).strip()
        result.append(
            {
                "name": name,
                "type": profile_type,
                "model": str(raw.get("model") or ""),
                "api_base": str(raw.get("api_base") or ""),
                "api_key_env": key_name,
                "api_key_configured": bool(
                    key_name and resolve_config_secret(project_root, key_name)
                ),
                "protocol": protocol,
                "context_window": _positive_int(
                    raw.get("context_window"), field_name="context_window", default=200000
                ),
                "max_output_tokens": _positive_int(
                    raw.get("max_output_tokens"),
                    field_name="max_output_tokens",
                    default=32000,
                ),
                "agent_models": dict(raw.get("agent_models") or {}),
                "gateway_models": dict(raw.get("gateway_models") or {}),
                "compatible_agents": (
                    [] if protocol == "native" else _profile_compatible_agents(protocol)
                ),
                "supports_all_evaluation_agents": (
                    protocol == "openai_compatible"
                ),
                "source": "local" if name in local_profiles else "built_in",
                "is_default": name == str(environment.get("LITELLM_PROFILE") or "litellm"),
            }
        )
    return result


def _normalize_agent_mapping(value: object, *, field_name: str) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    result: dict[str, str] = {}
    for raw_name, raw_model in value.items():
        name = str(raw_name).strip().lower()
        model = str(raw_model).strip()
        adapter = model_adapter(name)
        if not adapter.evaluation_supported:
            raise ValueError(f"{field_name} cannot target excluded Agent {name!r}")
        if model:
            result[name] = model
    return result


def _store_secret(project_root: Path, name: str, value: str) -> None:
    update_root_env(project_root, {name: value})


def save_model_profile(
    project_root: Path,
    name: str,
    values: Mapping[str, object],
    *,
    api_key: str | None = None,
    make_default: bool = False,
) -> dict[str, Any]:
    """Create/update a provider profile in repository-root ``.env``."""
    normalized_name = _validate_profile_name(name)
    model = str(values.get("model") or "").strip()
    if not model:
        raise ValueError("model cannot be empty")
    api_base = str(values.get("api_base") or "").strip()
    _normalized_base_url(api_base)
    protocol = str(values.get("protocol") or "openai_compatible").strip().lower()
    if protocol not in PROFILE_PROTOCOLS:
        raise ValueError(f"Unsupported protocol: {protocol}")
    key_name = _validate_env_name(
        str(values.get("api_key_env") or "LITELLM_API_KEY")
    )
    profile: dict[str, Any] = {
        "type": "compatible",
        "model": model,
        "api_base": api_base.rstrip("/"),
        "api_key_env": key_name,
        "protocol": protocol,
        "context_window": _positive_int(
            values.get("context_window"), field_name="context_window", default=200000
        ),
        "max_output_tokens": _positive_int(
            values.get("max_output_tokens"),
            field_name="max_output_tokens",
            default=32000,
        ),
    }
    agent_models = _normalize_agent_mapping(
        values.get("agent_models"), field_name="agent_models"
    )
    gateway_models = _normalize_agent_mapping(
        values.get("gateway_models"), field_name="gateway_models"
    )
    if agent_models:
        profile["agent_models"] = agent_models
    if gateway_models:
        profile["gateway_models"] = gateway_models

    environment = load_root_env(project_root)
    raw_profiles = environment.get("MODEL_PROFILES_JSON", "").strip()
    try:
        profiles = json.loads(raw_profiles) if raw_profiles else {}
    except ValueError as exc:
        raise ValueError("MODEL_PROFILES_JSON must be valid JSON") from exc
    if not isinstance(profiles, dict):
        raise ValueError("MODEL_PROFILES_JSON must be a JSON object")
    profiles[normalized_name] = profile
    updates: dict[str, str | None] = {
        "MODEL_PROFILES_JSON": json.dumps(profiles, ensure_ascii=False, separators=(",", ":")),
    }
    if make_default:
        updates["LITELLM_PROFILE"] = normalized_name
    update_root_env(project_root, updates)
    if api_key is not None and api_key.strip():
        _store_secret(project_root, key_name, api_key.strip())
    return next(
        item for item in list_model_profiles(project_root) if item["name"] == normalized_name
    )


def delete_model_profile(project_root: Path, name: str) -> bool:
    """Delete a custom profile from repository-root ``.env``."""
    normalized_name = _validate_profile_name(name)
    environment = load_root_env(project_root)
    raw_profiles = environment.get("MODEL_PROFILES_JSON", "").strip()
    try:
        profiles = json.loads(raw_profiles) if raw_profiles else {}
    except ValueError as exc:
        raise ValueError("MODEL_PROFILES_JSON must be valid JSON") from exc
    if not isinstance(profiles, dict) or normalized_name not in profiles:
        return False
    del profiles[normalized_name]
    updates: dict[str, str | None] = {
        "MODEL_PROFILES_JSON": (
            json.dumps(profiles, ensure_ascii=False, separators=(",", ":"))
            if profiles else None
        )
    }
    if environment.get("LITELLM_PROFILE") == normalized_name:
        updates["LITELLM_PROFILE"] = "litellm"
    update_root_env(project_root, updates)
    return True


def describe_model_config(project_root: Path) -> dict[str, Any]:
    config = load_model_config(project_root)
    profiles = config.get("profiles") or {}
    unified = config.get("litellm")
    selected = load_root_env(project_root).get("LITELLM_PROFILE", "").strip()
    if isinstance(unified, dict) and selected in {"", "litellm"}:
        default_name = "litellm"
        default = unified
    else:
        default_name = selected or str(config.get("default_profile") or "").strip()
        default = profiles.get(default_name, {}) if isinstance(profiles, dict) else {}
    key_name = (
        str(default.get("api_key_env") or "LITELLM_API_KEY")
        if isinstance(default, dict)
        else "LITELLM_API_KEY"
    )
    profile_models = {
        name: value.get("model")
        for name, value in profiles.items()
        if isinstance(value, dict) and value.get("model")
    } if isinstance(profiles, dict) else {}
    if isinstance(unified, dict) and unified.get("model"):
        profile_models["litellm"] = unified["model"]
    common = {
        "default_profile": default_name or None,
        "default_model": (
            resolve_config_secret(project_root, "LITELLM_MODEL")
            if default_name == "litellm" else None
        ) or (default.get("model") if isinstance(default, dict) else None),
        "api_base": (
            resolve_config_secret(project_root, "LITELLM_API_BASE")
            if default_name == "litellm" else None
        ) or (default.get("api_base") if isinstance(default, dict) else None),
        "api_key_env": key_name,
        "api_key_configured": bool(resolve_config_secret(project_root, key_name)),
    }
    if isinstance(unified, dict) and default_name == "litellm":
        common.pop("default_profile", None)
        common.update(
            configuration_mode="unified_litellm",
            gateway="litellm",
            reasoning_enabled=bool(unified.get("reasoning", True)),
            legacy_profiles_hidden=True,
            model_adapter_agent_count=len(AGENT_MODEL_ADAPTERS),
        )
        return common
    return {
        "configuration_mode": "legacy_profiles",
        **common,
        "profiles": sorted(profiles) if isinstance(profiles, dict) else [],
        "profile_models": profile_models,
        "profile_types": {
            name: str(value.get("type") or "compatible")
            for name, value in profiles.items()
            if isinstance(value, dict)
        } if isinstance(profiles, dict) else {},
        "profile_protocols": {
            name: (
                "native" if str(value.get("type") or "").lower() == "native"
                else str(value.get("protocol") or "openai_compatible")
            )
            for name, value in profiles.items()
            if isinstance(value, dict)
        } if isinstance(profiles, dict) else {},
        "supported_profile_protocols": sorted(PROFILE_PROTOCOLS),
        "model_adapter_agent_count": len(AGENT_MODEL_ADAPTERS),
    }


def discover_available_models(
    project_root: Path,
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Read LiteLLM's OpenAI-compatible model catalog without exposing keys."""
    config = load_model_config(project_root)
    profiles = config.get("profiles") or {}
    models: dict[str, dict[str, Any]] = {}
    gateways: dict[str, list[str]] = {}
    errors: list[dict[str, str]] = []
    if isinstance(config.get("litellm"), dict):
        resolved = resolve_model_profile(project_root)
        profiles = {"litellm": {**config["litellm"], "api_base": resolved.api_base}}
    for name, value in profiles.items():
        if not isinstance(value, dict):
            continue
        configured_model = str(value.get("model") or "").strip()
        if str(value.get("type") or "").lower() == "native":
            if configured_model:
                models.setdefault(
                    configured_model,
                    {"id": configured_model, "source": "native", "profiles": []},
                )["profiles"].append(name)
            continue
        api_base = str(value.get("api_base") or "").strip()
        if not api_base:
            continue
        openai_base, _ = _normalized_base_url(api_base)
        gateways.setdefault(openai_base, []).append(name)
        if configured_model:
            models.setdefault(
                configured_model,
                {"id": configured_model, "source": "configured", "profiles": []},
            )["profiles"].append(name)
    with httpx.Client(timeout=8.0, transport=transport) as client:
        for base_url, profile_names in gateways.items():
            first = profiles[profile_names[0]]
            key_name = str(first.get("api_key_env") or "LITELLM_API_KEY")
            api_key = resolve_config_secret(project_root, key_name)
            if not api_key:
                errors.append({"api_base": base_url, "error": f"{key_name} is not configured"})
                continue
            try:
                response = client.get(
                    f"{base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                response.raise_for_status()
                payload = response.json()
                rows = payload.get("data", []) if isinstance(payload, dict) else []
                for row in rows:
                    model_id = str(row.get("id") or "").strip() if isinstance(row, dict) else ""
                    if not model_id:
                        continue
                    item = models.setdefault(
                        model_id,
                        {"id": model_id, "source": "litellm", "profiles": []},
                    )
                    item["source"] = "litellm"
                    item["owned_by"] = row.get("owned_by")
                    item["profiles"] = sorted(set(item["profiles"] + profile_names))
            except (httpx.HTTPError, ValueError) as exc:
                errors.append({"api_base": base_url, "error": str(exc)})
    default_profile = str(config.get("default_profile") or "")
    result = sorted(models.values(), key=lambda item: (item["source"] != "litellm", item["id"].lower()))
    for item in result:
        item["profiles"] = sorted(set(item["profiles"]))
        exact_profiles = [
            name
            for name in item["profiles"]
            if isinstance(profiles.get(name), dict)
            and str(profiles[name].get("model") or "").strip() == item["id"]
        ]
        item["profile"] = (
            default_profile
            if default_profile in exact_profiles
            else (
                exact_profiles[0]
                if exact_profiles
                else (default_profile if default_profile in item["profiles"] else (item["profiles"][0] if item["profiles"] else None))
            )
        )
    return {
        "models": result,
        "litellm_available": any(item["source"] == "litellm" for item in result),
        "gateways": [{"api_base": base, "profiles": names} for base, names in gateways.items()],
        "errors": errors,
    }


def refresh_litellm_model_catalog(
    project_root: Path,
    *,
    transport: httpx.BaseTransport | None = None,
    probe_timeout: float = 15.0,
    probe_workers: int = 8,
    probe_agent: str | None = None,
) -> dict[str, Any]:
    """Refresh the catalog and retain only models that complete a real inference."""
    discovered = discover_available_models(project_root, transport=transport)
    visible_models = [
        item for item in discovered.get("models", []) if item.get("source") == "litellm"
    ]

    def probe(item: dict[str, Any]) -> dict[str, Any]:
        model_id = str(item["id"])
        started = time.perf_counter()
        try:
            profile = resolve_model_profile(project_root, model_override=model_id)
            endpoint_name = "chat/completions"
            endpoint = profile.api_base.rstrip("/") + f"/{endpoint_name}"
            api_key = profile.environment.get(profile.api_key_env, "")
            request_body = (
                {"model": model_id, "input": "HI", "stream": False}
                if endpoint_name == "responses"
                else {
                    "model": profile.gateway_model_for_agent("direct"),
                    "messages": [{"role": "user", "content": "HI"}],
                    "stream": False,
                }
            )
            with httpx.Client(timeout=probe_timeout, transport=transport) as client:
                response = client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=request_body,
                )
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            if response.is_success:
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("error") or not (
                    payload.get("choices") if endpoint_name == "chat/completions"
                    else payload.get("output") or payload.get("output_text")
                ):
                    raise ValueError("Model returned HTTP 200 but no inference output (invalid/empty response)")
                return {
                    "id": model_id,
                    "owned_by": item.get("owned_by"),
                    "available": True,
                    "enabled": "no-thinking" not in model_id.lower(),
                    "reasoning": None,
                    "reasoning_policy": "provider_default_not_disabled",
                    "probe_scope": "text_only_not_tool_or_agent_validation",
                    "probe_endpoint": endpoint_name,
                    "probe_status_code": response.status_code,
                    "probe_latency_ms": latency_ms,
                }
            failure = describe_evaluation_failure(
                response.text,
                returncode=1,
                status_code=response.status_code,
                component="litellm_model_probe",
            )
            if failure:
                failure.pop("technical_detail", None)
            return {
                "id": model_id,
                "available": False,
                "probe_status_code": response.status_code,
                "probe_latency_ms": latency_ms,
                "failure": failure,
            }
        except Exception as exc:
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            failure = describe_evaluation_failure(
                str(exc), returncode=1, component="litellm_model_probe"
            )
            return {
                "id": model_id,
                "available": False,
                "probe_status_code": None,
                "probe_latency_ms": latency_ms,
                "failure": failure,
            }

    probes: list[dict[str, Any]] = []
    safe_workers = max(1, min(int(probe_workers), len(visible_models) or 1, 16))
    with ThreadPoolExecutor(max_workers=safe_workers) as pool:
        futures = {pool.submit(probe, item): item for item in visible_models}
        for future in as_completed(futures):
            probes.append(future.result())
    probes.sort(key=lambda item: str(item["id"]).lower())
    models = [item for item in probes if item["available"]]
    unavailable = [item for item in probes if not item["available"]]
    snapshot = {
        "schema_version": 1,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed" if models else "failed",
        "source": "LiteLLM /v1/models + /v1/chat/completions",
        "catalog_visible_only": False,
        "connectivity_tested": True,
        "probe_agent": probe_agent,
        "probe_endpoint": "chat/completions",
        "probe_prompt": "HI",
        "probe_workers": safe_workers,
        "visible_model_count": len(visible_models),
        "available_model_count": len(models),
        "unavailable_model_count": len(unavailable),
        "note": (
            "models contains only deployments that returned a successful real inference. "
            "Agent-specific protocol compatibility must still be verified with check-agent."
        ),
        "model_count": len(models),
        "models": models,
        "unavailable_models": unavailable,
        "errors": discovered.get("errors") or [],
    }
    # Agent-specific protocol probes are transient views. Do not replace the
    # shared Chat Completions catalog with (for example) a Codex-only result.
    if probe_agent is None:
        _atomic_write(
            project_root / "config" / "litellm-models.json",
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        )
    return snapshot


def load_litellm_model_catalog(project_root: Path) -> dict[str, Any]:
    """Load the last non-secret LiteLLM model catalog snapshot."""
    path = project_root / "config" / "litellm-models.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"LiteLLM model catalog does not exist: {path}; run 'agent-eval models --refresh'"
        )
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("models"), list):
        raise ValueError(f"Invalid LiteLLM model catalog: {path}")
    return value


def write_openclaw_profile_config(
    path: Path,
    profile: ResolvedModelProfile,
    *,
    workspace: Path | None = None,
    api_base_override: str | None = None,
) -> None:
    openai_base, _ = _normalized_base_url(api_base_override or profile.api_base)
    gateway_model = profile.gateway_model_for_agent("openclaw")
    primary = f"litellm/{gateway_model}"
    config = {
        "models": {
            "mode": "replace",
            "providers": {
                "litellm": {
                    "baseUrl": openai_base,
                    "api": "openai-completions",
                    "apiKey": "${LITELLM_API_KEY}",
                    "auth": "api-key",
                    "timeoutSeconds": 1800,
                    "models": [
                        {
                            "id": gateway_model,
                            "name": gateway_model,
                            "api": "openai-completions",
                            "input": ["text"],
                            "compat": {"supportsUsageInStreaming": True},
                            "reasoning": True,
                            "contextWindow": profile.context_window,
                            "maxTokens": profile.max_output_tokens,
                        }
                    ],
                }
            },
        },
        "agents": {
            "defaults": {"model": {"primary": primary}},
            "entries": {
                "main": {
                    "identity": {"name": "main"},
                    "model": {"primary": primary},
                    # The packaged JustDo runtime owns a native Gateway
                    # dispatcher. Keep its native spawn/wait/status tools
                    # visible so pipeline evaluations exercise real subagents.
                    "tools": {
                        "allow": [
                            "read",
                            "write",
                            "edit",
                            "apply_patch",
                            "exec",
                            "process",
                            "session_status",
                            "sessions_spawn",
                            "sessions_yield",
                            "subagents",
                        ]
                    },
                    **({"workspace": str(workspace)} if workspace is not None else {}),
                }
            },
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def write_codebuddy_profile_config(
    path: Path,
    profile: ResolvedModelProfile,
    *,
    endpoint: str | None = None,
) -> None:
    """Write an isolated OpenAI-compatible CodeBuddy model without persisting a key."""
    openai_base, _ = _normalized_base_url(profile.api_base)
    cli_model = profile.model_for_agent("codebuddy").removeprefix("custom-local:")
    config = {
        "models": [
            {
                "id": cli_model,
                "name": cli_model,
                "vendor": "LiteLLM",
                "url": endpoint or f"{openai_base}/chat/completions",
                "apiKey": "${LITELLM_API_KEY}",
                "maxInputTokens": profile.context_window,
                "maxOutputTokens": profile.max_output_tokens,
                "supportsToolCall": True,
                "supportsImages": True,
                "supportsReasoning": True,
            }
        ]
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
