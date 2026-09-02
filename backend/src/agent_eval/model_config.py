from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

import yaml
import httpx

from agent_eval.agent_adapters import AGENT_MODEL_ADAPTERS, model_adapter


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
            return "sonnet"
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
    return _merge(
        _read_yaml(config_dir / "models.yaml"),
        _read_yaml(config_dir / "local.yaml"),
    )


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
    selected = profile_name or str(config.get("default_profile") or "").strip()
    profiles = config.get("profiles") or {}
    if not selected or not isinstance(profiles, dict) or selected not in profiles:
        raise ValueError(f"Unknown or missing model profile: {selected or '<empty>'}")
    profile = profiles[selected]
    if not isinstance(profile, dict):
        raise ValueError(f"Model profile must be a mapping: {selected}")

    model = (model_override or str(profile.get("model") or "")).strip()
    if not model:
        raise ValueError(f"Model profile has no model: {selected}")
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
    api_base = str(profile.get("api_base") or "").strip()
    openai_base, anthropic_base = _normalized_base_url(api_base)

    key_name = _validate_env_name(str(profile.get("api_key_env") or "LITELLM_API_KEY"))
    context_window = _positive_int(
        profile.get("context_window"), field_name="context_window", default=200000
    )
    max_output_tokens = _positive_int(
        profile.get("max_output_tokens"), field_name="max_output_tokens", default=32000
    )
    local_secrets = config.get("secrets") or {}
    env_secrets = load_env_secrets(project_root)
    source_environment = environ if environ is not None else os.environ
    api_key = str(
        source_environment.get(key_name)
        or local_secrets.get(key_name)
        or env_secrets.get(key_name)
        or ""
    ).strip()
    if not api_key:
        raise ValueError(
            f"Model profile {selected!r} requires {key_name}; set it in the environment "
            "or config/local.yaml"
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
    if agent == "codebuddy":
        derived_codebuddy_model = _codebuddy_custom_model(model)
        if model_override:
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
        agent_args = ("--bare",)
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
    """Load a simple ignored KEY=value file without mutating process globals."""
    path = project_root / "config" / "secrets.env"
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            result[key] = value.strip().strip('"').strip("'")
    return result


def resolve_config_secret(
    project_root: Path,
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    config = load_model_config(project_root)
    secrets = config.get("secrets") or {}
    env_secrets = load_env_secrets(project_root)
    source_environment = environ if environ is not None else os.environ
    return str(source_environment.get(name) or secrets.get(name) or env_secrets.get(name) or "").strip()


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
    local = _read_yaml(project_root / "config" / "local.yaml")
    profiles = config.get("profiles") or {}
    local_profiles = local.get("profiles") or {}
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
                "is_default": name == str(config.get("default_profile") or ""),
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
    path = project_root / "config" / "secrets.env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    output: list[str] = []
    replaced = False
    for line in lines:
        if re.match(rf"^\s*{re.escape(name)}\s*=", line):
            if not replaced:
                output.append(f"{name}={value}")
                replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(f"{name}={value}")
    _atomic_write(path, "\n".join(output).rstrip() + "\n")


def save_model_profile(
    project_root: Path,
    name: str,
    values: Mapping[str, object],
    *,
    api_key: str | None = None,
    make_default: bool = False,
) -> dict[str, Any]:
    """Create/update an ignored local provider profile with atomic writes."""
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

    path = project_root / "config" / "local.yaml"
    local = _read_yaml(path)
    profiles = local.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("Local profiles configuration must be an object")
    profiles[normalized_name] = profile
    if make_default:
        local["default_profile"] = normalized_name
    _write_yaml(path, local)
    if api_key is not None and api_key.strip():
        _store_secret(project_root, key_name, api_key.strip())
    return next(
        item for item in list_model_profiles(project_root) if item["name"] == normalized_name
    )


def delete_model_profile(project_root: Path, name: str) -> bool:
    """Delete a local profile/override; built-in profiles themselves are immutable."""
    normalized_name = _validate_profile_name(name)
    path = project_root / "config" / "local.yaml"
    local = _read_yaml(path)
    profiles = local.get("profiles") or {}
    if not isinstance(profiles, dict) or normalized_name not in profiles:
        return False
    del profiles[normalized_name]
    if profiles:
        local["profiles"] = profiles
    else:
        local.pop("profiles", None)
    if local.get("default_profile") == normalized_name:
        local.pop("default_profile", None)
    _write_yaml(path, local)
    return True


def describe_model_config(project_root: Path) -> dict[str, Any]:
    config = load_model_config(project_root)
    default_name = str(config.get("default_profile") or "").strip()
    profiles = config.get("profiles") or {}
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
    return {
        "default_profile": default_name or None,
        "default_model": default.get("model") if isinstance(default, dict) else None,
        "api_base": default.get("api_base") if isinstance(default, dict) else None,
        "api_key_env": key_name,
        "api_key_configured": bool(resolve_config_secret(project_root, key_name)),
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
    if not isinstance(profiles, dict):
        profiles = {}
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
            "list": [
                {
                    "id": "main",
                    "default": True,
                    "identity": {"name": "main"},
                    "model": {"primary": primary},
                    **({"workspace": str(workspace)} if workspace is not None else {}),
                }
            ],
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
