from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AgentModelAdapter:
    """Declarative model/provider injection contract for one Multica runtime."""

    agent: str
    model_selection: str
    provider_injection: str
    client_protocol: str
    skill_root: str | None
    evaluation_supported: bool = True
    note: str = ""

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


_SKILL_ROOTS = {
    "antigravity": ".agents/skills",
    "claude": ".claude/skills",
    "codebuddy": ".codebuddy/skills",
    "codex": ".agents/skills",
    "copilot": ".github/skills",
    "cursor": ".cursor/skills",
    "deveco": ".deveco/skills",
    "grok": ".grok/skills",
    "kimi": ".kimi/skills",
    "kiro": ".kiro/skills",
    "justdo": "skills",
    "omp": ".omp/skills",
    "dsh": ".dsh/skills",
    "openclaw": "skills",
    "opencode": ".opencode/skills",
    "pi": ".pi/skills",
    "qoder": ".qoder/skills",
    "qoderclicn": ".qoder/skills",
    "qwen": ".qwen/skills",
    "reasonix": ".reasonix/skills",
    "traecli": ".traecli/skills",
}


def _adapter(
    agent: str,
    *,
    selection: str = "runtime_model_argument",
    injection: str = "process_environment",
    protocol: str = "openai_compatible",
    supported: bool = True,
    note: str = "",
) -> AgentModelAdapter:
    return AgentModelAdapter(
        agent=agent,
        model_selection=selection,
        provider_injection=injection,
        client_protocol=protocol,
        skill_root=_SKILL_ROOTS.get(agent),
        evaluation_supported=supported,
        note=note,
    )


# The 21 entries below are the supported evaluation surface for arbitrary model
# aliases. An adapter being present means the evaluator can inject a model and a
# Skill without changing the user's global configuration. Runtime/provider
# availability is still verified at execution time.
AGENT_MODEL_ADAPTERS: dict[str, AgentModelAdapter] = {
    "antigravity": _adapter("antigravity"),
    "claude": _adapter(
        "claude", selection="cli_model_alias", injection="isolated_config_and_proxy",
        protocol="anthropic_messages",
    ),
    "codebuddy": _adapter(
        "codebuddy", selection="generated_model_alias", injection="isolated_config_and_proxy",
    ),
    "codex": _adapter(
        "codex", selection="cli_model_argument", injection="cli_provider_override",
        protocol="openai_responses",
    ),
    "copilot": _adapter("copilot"),
    "cursor": _adapter("cursor"),
    "deveco": _adapter("deveco"),
    "dsh": _adapter("dsh"),
    "grok": _adapter("grok", selection="acp_session_model"),
    "kimi": _adapter("kimi", selection="acp_session_model"),
    "kiro": _adapter("kiro", selection="acp_session_model"),
    "justdo": _adapter(
        "justdo", selection="generated_agent_profile", injection="isolated_config_and_proxy",
    ),
    "omp": _adapter("omp"),
    "openclaw": _adapter(
        "openclaw", selection="generated_agent_profile", injection="isolated_config_and_proxy",
    ),
    "opencode": _adapter("opencode", injection="inline_provider_config"),
    "pi": _adapter("pi"),
    "qoder": _adapter("qoder", selection="acp_session_model"),
    "qoderclicn": _adapter("qoderclicn", selection="acp_session_model"),
    "qwen": _adapter("qwen"),
    "reasonix": _adapter("reasonix", selection="acp_session_model"),
    "traecli": _adapter("traecli", selection="acp_session_model"),
}


EXCLUDED_AGENT_ADAPTERS: dict[str, AgentModelAdapter] = {
    "dim": _adapter("dim", selection="acp_session_model", supported=False,
                    note="Skill injection is not implemented"),
    "hermes": _adapter("hermes", selection="acp_session_model", supported=False,
                       note="Skill injection is not implemented"),
    "mcode": _adapter("mcode", selection="runtime_managed", supported=False,
                      note="Model is managed by the Agent runtime"),
    "qwenpaw": _adapter("qwenpaw", selection="runtime_managed", supported=False,
                        note="Model selection mutates the persistent Agent profile"),
    "zeroclaw": _adapter("zeroclaw", selection="runtime_managed", supported=False,
                         note="Model and Skill are managed by the Agent profile"),
}


def model_adapter(agent: str) -> AgentModelAdapter:
    if agent in AGENT_MODEL_ADAPTERS:
        return AGENT_MODEL_ADAPTERS[agent]
    if agent in EXCLUDED_AGENT_ADAPTERS:
        return EXCLUDED_AGENT_ADAPTERS[agent]
    raise ValueError(f"No model adapter is registered for Agent {agent!r}")


def supported_evaluation_agents() -> tuple[str, ...]:
    return tuple(AGENT_MODEL_ADAPTERS)
