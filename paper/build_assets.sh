#!/usr/bin/env bash
# Copy the generated tables, figures and numbers into paper/, then zip it for Overleaf.
#   bash paper/build_assets.sh            -> paper/tables/*.tex, paper/figures/*, paper/numbers.tex, paper.zip
set -e
cd "$(dirname "$0")/.."
python experiments/paper_numbers.py
mkdir -p paper/tables paper/figures
cp results/tables/*.tex paper/tables/ 2>/dev/null || true
cp results/figures/*.png paper/figures/ 2>/dev/null || true
cp docs/img/architecture.pdf paper/figures/
(cd paper && rm -f ../paper.zip && zip -qr ../paper.zip main.tex refs.bib numbers.tex tables figures)
echo "paper assets ready; upload paper.zip to Overleaf (New Project > Upload Project)"
