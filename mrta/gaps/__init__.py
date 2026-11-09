"""Gap-specific components, one module per research gap (see README, "Gap-resolution table").

g1_portfolio  Gap 1: solver-backed allocation with a verified solver portfolio
g2_verify     Gap 2: model sanity checks, independent cross-check, plan verification gate
g3_language   Gap 3: staged extraction from informal briefs and targeted clarification

Each component is switched on or off through ``mrta.llm.agent.AgentConfig``.
"""

from .g1_portfolio import milp_budget, solve_portfolio
from .g2_verify import diff_models, plan_violations, sanity_problems
from .g3_language import MissionOwner, staged_extraction

__all__ = ["MissionOwner", "diff_models", "milp_budget", "plan_violations", "sanity_problems",
           "solve_portfolio", "staged_extraction"]
