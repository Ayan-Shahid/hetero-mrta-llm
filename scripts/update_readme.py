#!/usr/bin/env python3
"""Copy generated tables into README.md (between <!-- BEGIN name --> and <!-- END name --> markers)
and keep a small sample of agent trajectories in logs/sample/ for the repository.

    python scripts/update_readme.py
"""

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCKS = {
    "gap_resolution": "results/tables/gap_resolution.md",
    "main_results": "results/tables/main_results_compact.md",
    "classical": "results/tables/E1_classical.md",
}


def table_only(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.startswith("|")]
    return "\n".join(lines)


def main() -> None:
    readme = ROOT / "README.md"
    text = readme.read_text()
    for name, rel in BLOCKS.items():
        path = ROOT / rel
        if not path.exists():
            print(f"skip {name}: {rel} not found")
            continue
        body = table_only(path.read_text())
        pattern = re.compile(rf"(<!-- BEGIN {name}[^>]*-->\n).*?(\n<!-- END {name} -->)", re.S)
        if not pattern.search(text):
            print(f"skip {name}: no markers in README")
            continue
        text = pattern.sub(lambda m: m.group(1) + body + m.group(2), text)
        print(f"updated {name} from {rel}")
    readme.write_text(text)

    # a small, representative sample of trajectories to commit (logs/ itself is large and git-ignored)
    sample = ROOT / "logs" / "sample"
    sample.mkdir(parents=True, exist_ok=True)
    picked = 0
    for model_dir in sorted(p for p in (ROOT / "logs").iterdir() if p.is_dir() and p.name not in ("sample", "pilot")):
        for exp in ("E3", "E2", "E5"):
            files = sorted((model_dir / exp).glob("*__r0.jsonl")) if (model_dir / exp).exists() else []
            for f in files[:: max(1, len(files) // 6)][:6]:
                dst = sample / model_dir.name / exp / f.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(f, dst)
                picked += 1
    print(f"copied {picked} trajectories to {sample}")


if __name__ == "__main__":
    sys.exit(main())
