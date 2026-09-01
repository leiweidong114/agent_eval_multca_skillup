from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

import yaml


@dataclass(frozen=True)
class ResolvedModelProfile:
    name: str
    model: str
    api_base: str
    environment: dict[str, str]
    agent_args: tuple[str, ...]

    def model_for_agent(self, agent: str) -> str:
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
            return f"litellm/{self.model}"
        return self.model


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
        return ResolvedModelProfile(selected, model, "", {}, ())
    api_base = str(profile.get("api_base") or "").strip()
    openai_base, anthropic_base = _normalized_base_url(api_base)

    key_name = str(profile.get("api_key_env") or "LITELLM_API_KEY").strip()
    local_secrets = config.get("secrets") or {}
    source_environment = environ if environ is not None else os.environ
    api_key = str(source_environment.get(key_name) or local_secrets.get(key_name) or "").strip()
    if not api_key:
        raise ValueError(
            f"Model profile {selected!r} requires {key_name}; set it in the environment "
            "or config/local.yaml"
        )

    # Multica backends launch different Agent CLIs. These aliases cover the
    # OpenAI-compatible and Anthropic-compatible conventions used by them.
    environment = {
        "LITELLM_API_KEY": api_key,
        "OPENAI_API_KEY": api_key,
        "OPENAI_BASE_URL": openai_base,
        "ANTHROPIC_API_KEY": api_key,
        "ANTHROPIC_AUTH_TOKEN": api_key,
        "ANTHROPIC_BASE_URL": anthropic_base,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "MINIMAX_API_KEY": api_key,
        "MINIMAX_BASE_URL": openai_base,
    }
    if agent == "claude":
        # LiteLLM virtual keys are bearer tokens. Claude Code gives
        # ANTHROPIC_API_KEY precedence and would send it as x-api-key, so use
        # ANTHROPIC_AUTH_TOKEN exclusively for this backend.
        environment.pop("ANTHROPIC_API_KEY")
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
                        model: {
                            "name": model,
                            "reasoning": True,
                            "limit": {"context": 200000, "output": 32000},
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
    return ResolvedModelProfile(selected, model, api_base, environment, agent_args)


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
    secrets = config.get("secrets") or {}
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
        "api_key_configured": bool(os.environ.get(key_name) or secrets.get(key_name)),
        "profiles": sorted(profiles) if isinstance(profiles, dict) else [],
        "profile_models": profile_models,
        "profile_types": {
            name: str(value.get("type") or "compatible")
            for name, value in profiles.items()
            if isinstance(value, dict)
        } if isinstance(profiles, dict) else {},
    }


def write_openclaw_profile_config(path: Path, profile: ResolvedModelProfile) -> None:
    openai_base, _ = _normalized_base_url(profile.api_base)
    primary = f"litellm/{profile.model}"
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
                            "id": profile.model,
                            "name": profile.model,
                            "api": "openai-completions",
                            "input": ["text"],
                            "compat": {"supportsUsageInStreaming": True},
                            "reasoning": True,
                            "contextWindow": 200000,
                            "maxTokens": 32000,
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
                }
            ],
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def write_codebuddy_profile_config(path: Path, profile: ResolvedModelProfile) -> None:
    """Write an isolated OpenAI-compatible CodeBuddy model without persisting a key."""
    openai_base, _ = _normalized_base_url(profile.api_base)
    config = {
        "models": [
            {
                "id": profile.model,
                "name": profile.model,
                "vendor": "LiteLLM",
                "url": f"{openai_base}/chat/completions",
                "apiKey": "${LITELLM_API_KEY}",
                "maxInputTokens": 200000,
                "maxOutputTokens": 32000,
                "supportsToolCall": True,
                "supportsImages": True,
                "supportsReasoning": True,
            }
        ]
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
