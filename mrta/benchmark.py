"""Load benchmark records and the fixed dev/test split.

A record is one data/benchmark/<id>.json file: {"tier", "seed", "split", "scenario", "briefs"}.
The split is fixed when the benchmark is generated (data/benchmark/splits.json); the test split
is never used for prompt or threshold choices.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .schema import Scenario

TIER_ORDER = {"S": 0, "M": 1, "L": 2}


@dataclass
class Record:
    id: str
    tier: str
    seed: int
    split: str
    scenario: Scenario
    briefs: dict[str, str]


def load(bench: str | Path = "data/benchmark", split: str = "all", tiers: str | set[str] = "",
         limit: int = 0, ids: list[str] | None = None) -> list[Record]:
    """split: dev | test | all; tiers: "S,M" or a set; limit: max scenarios per tier (0 = all)."""
    bench = Path(bench)
    if isinstance(tiers, str):
        tiers = {t.strip() for t in tiers.split(",") if t.strip()}
    split_of: dict[str, str] = {}
    sp = bench / "splits.json"
    if sp.exists():
        data = json.loads(sp.read_text())
        for name in ("dev", "test"):
            for sid in data.get(name, []):
                split_of[sid] = name
    out: list[Record] = []
    per_tier: dict[str, int] = {}
    for f in sorted(bench.glob("*.json")):
        if f.name == "splits.json":
            continue
        rec = json.loads(f.read_text())
        if "scenario" not in rec:
            continue
        scn = Scenario.model_validate(rec["scenario"])
        r = Record(id=scn.id, tier=rec["tier"], seed=int(rec.get("seed", 0)),
                   split=rec.get("split") or split_of.get(scn.id, "test"), scenario=scn, briefs=rec["briefs"])
        if tiers and r.tier not in tiers:
            continue
        if split != "all" and r.split != split:
            continue
        if ids and r.id not in ids:
            continue
        per_tier[r.tier] = per_tier.get(r.tier, 0) + 1
        if limit and per_tier[r.tier] > limit:
            continue
        out.append(r)
    out.sort(key=lambda r: (TIER_ORDER.get(r.tier, 9), r.seed))
    return out
