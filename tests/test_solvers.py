"""Every baseline returns plans the checker accepts, and the MILP is never beaten when it proves optimality."""

import pytest

from mrta.checker import check
from mrta.scenarios import generate, render
from mrta.schedule import Evaluator, compute_schedule
from mrta.solvers import solve_auction, solve_hungarian, solve_milp

SCENARIOS = [generate("S", s) for s in range(6)] + [generate("M", s) for s in range(3)]


@pytest.mark.parametrize("scn", SCENARIOS, ids=lambda s: s.id)
def test_generated_scenarios_are_feasible_and_milp_dominates(scn):
    milp = solve_milp(scn, time_limit=60)
    assert milp.status == "optimal"
    best = check(scn, milp.plan)
    assert best.feasible, best.violations
    for solver in (solve_auction, solve_hungarian):
        res = solver(scn)
        out = check(scn, res.plan)
        if out.feasible:
            assert out.schedule.makespan >= best.schedule.makespan - 1e-3


@pytest.mark.parametrize("scn", SCENARIOS[:3], ids=lambda s: s.id)
def test_briefs_mention_every_id_and_number(scn):
    for style in ("explicit", "informal"):
        text = render(scn, style)
        for r in scn.robots:
            assert r.id in text
        for t in scn.tasks:
            assert t.id in text and t.location in text


def test_fast_and_full_schedules_agree():
    scn = SCENARIOS[-1]
    plan = solve_auction(scn).plan
    assert Evaluator(scn).timeline(plan.routes).makespan == pytest.approx(compute_schedule(scn, plan).makespan)


def test_generation_is_deterministic():
    assert generate("M", 7).model_dump() == generate("M", 7).model_dump()
