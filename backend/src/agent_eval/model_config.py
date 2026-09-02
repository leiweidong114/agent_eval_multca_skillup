from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

import yaml
import httpx


@dataclass(frozen=True)
class ResolvedModelProfile:
    name: str
    model: str
    api_base: str
    environment: dict[str, str]
    agent_args: tuple[str, ...]
    agent_models: dict[str, str] = field(default_factory=dict)
    gateway_models: dict[str, str] = field(default_factory=dict)

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
            return f"litellm/{self.model}"
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
        return ResolvedModelProfile(selected, model, "", {}, (), {}, {})
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
    }
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
        wire_api = str(profile.get("codex_wire_api") or "responses").strip().lower()
        if wire_api not in {"responses", "chat"}:
            raise ValueError(
                f"Unsupported codex_wire_api {wire_api!r} in model profile {selected!r}"
            )
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
            f'model_providers.litellm.wire_api="{wire_api}"',
        )
    return ResolvedModelProfile(
        selected, model, api_base, environment, agent_args, agent_models, gateway_models
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
