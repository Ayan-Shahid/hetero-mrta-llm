"""Benchmark v2: fixed split, deterministic generation, feasibility by construction."""

from mrta.benchmark import load
from mrta.checker import check
from mrta.scenarios import generate
from mrta.solvers import SOLVERS


def test_benchmark_split_is_fixed():
    recs = load("data/benchmark", split="all")
    assert len(recs) == 50
    assert len([r for r in recs if r.split == "dev"]) == 10
    assert {r.tier for r in load("data/benchmark", split="test", tiers="L")} == {"L"}


def test_v2_generator_is_deterministic_and_feasible():
    a, b = generate("S", 11, version=2), generate("S", 11, version=2)
    assert a.model_dump() == b.model_dump()
    res = SOLVERS["milp"](a, time_limit=30)
    assert res.plan is not None and check(a, res.plan).feasible


def test_v1_generator_unchanged():
    # Phase 1 numbers stay reproducible: the default generator is still v1.
    assert generate("M", 7).model_dump() == generate("M", 7, version=1).model_dump()
