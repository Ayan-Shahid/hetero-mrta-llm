#!/usr/bin/env python3
"""Check every entry of paper/refs.bib against Crossref (needs internet).

    python scripts/check_refs.py            # report: DOI found / matches / differs / no DOI (URL only)
    python scripts/check_refs.py --fix      # write confirmed DOIs (and https://doi.org/ links) into refs.bib

An entry is confirmed when Crossref returns a work whose title matches with similarity >= 0.92.
arXiv, OpenReview and PMLR papers usually have no DOI; they keep their official URL.
"""

import argparse
import difflib
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

BIB = Path(__file__).resolve().parents[1] / "paper" / "refs.bib"
ENTRY = re.compile(r"@(\w+)\{([^,]+),(.*?)\n\}", re.S)


def field(body: str, name: str) -> str | None:
    m = re.search(rf"\b{name}\s*=\s*\{{((?:[^{{}}]|\{{[^{{}}]*\}})*)\}}", body, re.S)
    return m.group(1).strip() if m else None


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"[{}\\]", "", s.lower())).strip()


def crossref(title: str) -> dict | None:
    q = urllib.parse.urlencode({"query.bibliographic": title, "rows": 3,
                                "select": "DOI,title,container-title,issued,type"})
    req = urllib.request.Request(f"https://api.crossref.org/works?{q}",
                                 headers={"User-Agent": "refs-check/1.0 (course project)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        items = json.load(r)["message"]["items"]
    best, score = None, 0.0
    for it in items:
        t = (it.get("title") or [""])[0]
        s = difflib.SequenceMatcher(None, norm(t), norm(title)).ratio()
        if s > score:
            best, score = it, s
    if best is None:
        return None
    best["score"] = score
    return best


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fix", action="store_true")
    args = ap.parse_args()
    text = BIB.read_text()
    new_text = text
    for kind, key, body in ENTRY.findall(text):
        title = field(body, "title") or ""
        doi = field(body, "doi")
        try:
            hit = crossref(title)
        except Exception as err:  # noqa: BLE001
            print(f"{key:28} ERROR {err}")
            continue
        time.sleep(0.5)
        if hit is None or hit["score"] < 0.92:
            status = "no Crossref match (keep URL)" if not doi else "DOI not confirmed by title search"
            print(f"{key:28} {status}")
            continue
        found = hit["DOI"].lower()
        venue = (hit.get("container-title") or [""])[0]
        if doi and doi.lower() == found:
            print(f"{key:28} OK      {found}  ({venue})")
            if args.fix:
                new_text = new_text.replace(body, re.sub(r",?\s*verify\s*=\s*\{[^}]*\}", "", body))
            continue
        print(f"{key:28} {'DIFFERS' if doi else 'FOUND  '} {found}  ({venue}; match {hit['score']:.2f})"
              + (f"  [bib has {doi}]" if doi else ""))
        if args.fix:
            fixed = re.sub(r",?\s*verify\s*=\s*\{[^}]*\}", "", body)
            if doi:
                fixed = re.sub(r"doi\s*=\s*\{[^}]*\}", f"doi       = {{{found}}}", fixed)
            else:
                fixed = fixed.rstrip() + f",\n  doi       = {{{found}}}"
            link = f"https://doi.org/{found}"
            if re.search(r"\burl\s*=", fixed):
                fixed = re.sub(r"url\s*=\s*\{[^}]*\}", f"url       = {{{link}}}", fixed)
            else:
                fixed = fixed.rstrip() + f",\n  url       = {{{link}}}"
            new_text = new_text.replace(body, fixed)
    if args.fix:
        BIB.write_text(new_text)
        print(f"updated {BIB}")


if __name__ == "__main__":
    main()
