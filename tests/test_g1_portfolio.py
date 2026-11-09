"""G1: the verified solver portfolio."""

from mrta.checker import check
from mrta.gaps.g1_portfolio import milp_budget, solve_portfolio
from mrta.scenarios import generate
from mrta.solvers import SOLVERS


def test_portfolio_is_feasible_and_never_worse_than_heuristics():
    scn = generate("M", 3, version=2)
    res = solve_portfolio(scn)
    assert res.plan is not None and check(scn, res.plan).feasible
    for name in ("auction", "hungarian"):
        h = SOLVERS[name](scn)
        if h.plan is not None and check(scn, h.plan).feasible:
            assert res.makespan <= check(scn, h.plan).schedule.makespan + 1e-6


def test_milp_budget_grows_with_size_and_is_capped():
    s, l_ = generate("S", 0, version=2), generate("L", 0, version=2)
    assert milp_budget(s) < milp_budget(l_) <= 60
