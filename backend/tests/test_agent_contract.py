from agent_eval.agent_contract import REQUIRED_AGENT_EVIDENCE, describe_agent_contract
from agent_eval.runtime import SUPPORTED_AGENTS


def test_all_supported_agents_publish_the_evaluation_contract() -> None:
    for agent in SUPPORTED_AGENTS:
        contract = describe_agent_contract(agent)
        assert contract["agent"] == agent
        assert set(contract["required_for_certification"]) == set(REQUIRED_AGENT_EVIDENCE)
        assert contract["certification_policy"]["model_selection"]
        assert contract["certification_policy"]["telemetry"]
