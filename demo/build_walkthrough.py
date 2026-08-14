"""
Build the shareable demo walkthrough from the app's own mock data.

The page is a static replica of the Fiori worklist and object page, driven by the
real output `core/batch_adjudicate.py` wrote into webapp/localService/mockdata/.
Reading it from there rather than retyping it means the demo cannot drift from
what the pipeline actually produced.

    python demo/build_walkthrough.py

Writes demo/walkthrough.html.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOCKDATA = ROOT / "webapp" / "localService" / "mockdata"
TEMPLATE = ROOT / "demo" / "walkthrough.template.html"
OUTPUT = ROOT / "demo" / "walkthrough.html"

# batch_adjudicate.py stores AgentRationale as rationale[:2000]. Anything at the
# cap was cut, usually mid-sentence, so the page says so rather than presenting a
# severed sentence as the whole assessment.
RATIONALE_CAP = 2000


def load(name: str) -> list[dict]:
    return json.loads((MOCKDATA / name).read_text(encoding="utf-8"))


def main() -> None:
    cases = load("AdjudicationCase.json")
    evidence = load("EvidenceItem.json")

    for case in cases:
        case["RationaleTruncated"] = len(case.get("AgentRationale", "")) >= RATIONALE_CAP

    payload = json.dumps({"cases": cases, "evidence": evidence}, ensure_ascii=False)

    # The blob lands inside <script type="application/json">, so the only
    # sequence that could break out of the element is a literal "</script".
    payload = payload.replace("</", "<\\/")

    html = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload)
    OUTPUT.write_text(html, encoding="utf-8")

    truncated = sum(1 for c in cases if c["RationaleTruncated"])
    print(f"{OUTPUT.relative_to(ROOT)}  "
          f"({len(cases)} cases, {len(evidence)} evidence items, "
          f"{OUTPUT.stat().st_size / 1024:.0f} KB)")
    if truncated:
        print(f"  note: {truncated} rationale(s) hit the {RATIONALE_CAP}-char cap "
              f"and are flagged as truncated in the page")


if __name__ == "__main__":
    main()
