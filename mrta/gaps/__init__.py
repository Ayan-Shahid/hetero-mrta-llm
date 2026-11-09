"""Gap-specific components, one module per research gap (see README, "Gap-resolution table").

g1_portfolio  Gap 1: solver-backed allocation with a verified solver portfolio

Each component is switched on or off through ``mrta.llm.agent.AgentConfig``.
"""

from .g1_portfolio import milp_budget, solve_portfolio

__all__ = ["milp_budget", "solve_portfolio"]