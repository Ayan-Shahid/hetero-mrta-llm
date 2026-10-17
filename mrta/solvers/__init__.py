"""Classical baselines: exact MILP, iterated Hungarian assignment, SSI auction."""

from .auction import solve_auction
from .common import SolveResult
from .hungarian import solve_hungarian
from .milp import solve_milp

SOLVERS = {"milp": solve_milp, "hungarian": solve_hungarian, "auction": solve_auction}

__all__ = ["SOLVERS", "SolveResult", "solve_auction", "solve_hungarian", "solve_milp"]
