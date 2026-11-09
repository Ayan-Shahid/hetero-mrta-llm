"""The agent: every ablation preset runs end to end; prompts list only enabled tools."""

import pytest

from mrta.checker import check
from mrta.gaps.g3_language import MissionOwner
from mrta.llm.agent import AGENT_PRESETS, AgentConfig, run_agent, system_prompt
from mrta.llm.client import make_client
from mrta.scenarios import generate, render


def test_prompt_lists_only_enabled_tools():
    p = system_prompt(AgentConfig(g1_solvers=False, g3_clarify=False))
    assert "- solve " not in p and "ask_human" not in p and "draft_model" in p
    assert "write the plan yourself" in p


@pytest.mark.parametrize("preset", sorted(AGENT_PRESETS))
def test_every_preset_runs_end_to_end(preset):
    scn = generate("S", 4, version=2)
    client = make_client("mock-oracle")
    res = run_agent(client, scn, render(scn, "informal"), "informal", config=preset, human=MissionOwner(scn))
    assert res.plan is not None, (preset, res.status, res.error)
    assert any(ev.get("type") == "step" for ev in res.trace)
    assert res.trace[-1]["type"] == "end"
    if AGENT_PRESETS[preset].g1_solvers:
        assert check(scn, res.plan).feasible
