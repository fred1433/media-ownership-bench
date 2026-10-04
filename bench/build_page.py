"""Build site/index.html from site_src/index.template.html and the measured files (no number typed by hand)."""
import json, statistics, sys
from collections import Counter
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import ROOT, RUNS, load_cases

S = json.loads((RUNS / "scores.json").read_text())
LOC = json.loads((RUNS / "locate.json").read_text())
L = [json.loads(l) for l in (RUNS / "ledger.jsonl").read_text().splitlines() if l.strip()]
cases = load_cases()

STRUCT = {
    "kfrc": "Reorganized group, renamed parent, chain through trusts",
    "krzz": "Licensing LLC under a parent in Chapter 11",
    "kpix": "Merger closed 2025, renamed parent, family control",
    "wfaa": "Acquisition closed March 2026, appeal pending",
    "wfxp_mission": "Licensee differs from operator",
    "wapo": "Private holding LLC, outside FCC scope",
    "espn": "Three owners, outside FCC scope",
    "kpfa": "Nonprofit, no owner",
}
ORDER = ["wapo", "kpix", "wfaa", "kfrc", "krzz", "wfxp_mission", "espn", "kpfa"]

def short(r):
    up = r["ultimate_parent"]
    parents = ", ".join(e["name"] for e in up.get("entities", [])) if up["status"] in ("single", "multiple") else up["status"].replace("_", " ")
    cp = r["controlling_person"]
    ctrl = ", ".join(cp["names"]) if cp["status"] == "established" else {"none": "none", "not_applicable": "none (widely held)", "not_established": "not established"}[cp["status"]]
    ld = r["leader"]
    return {"owner": r["legal_owner"]["name"], "parent": parents, "control": ctrl,
            "leader": ld["name"] if ld["status"] == "established" else "not established", "leader_role": ld.get("role")}

outs = {}
for o in S["outputs"]:
    outs.setdefault(o["case"], {}).setdefault(o["arm"], []).append(o)

def mark(o):
    if o["asserted_error"]: return "x"
    if o["complete_supported"]: return "ok"
    return "gap"

def fieldcell(v):
    n = (v.get("names") or [None])[0]
    if n: n = n.replace(" \u2014 ", ", ").replace("\u2014", ", ").replace("\u2013", "-")  # model text quoted verbatim otherwise
    return {"v": v["verdict"], "n": n}

# per-case cost on the same perimeter as the totals: B includes its retrieval (Exa, shared by the three passes),
# C is reconstructed as B's retrieval and extraction plus the Gemini solve.
exa_case = Counter()
for r in L:
    if r["provider"] == "exa" and r["arm"] == "B": exa_case[r["case"]] += r["usd"]
def b_pre_case(cid, arm="B"):
    v = []
    for run in (1, 2, 3):
        p = RUNS / arm / f"{cid}_r{run}.json"
        if p.exists():
            d = json.loads(p.read_text()); v.append(sum(x["usd"] for x in d["steps"] if x["step"] != "solve"))
    return statistics.mean(v) if v else 0.0

rows = []
for cid in ORDER:
    c = next(x for x in cases if x["id"] == cid)
    arms = {}
    for arm in ["A", "B", "C", "B2", "C2", "bare"]:
        lst = sorted(outs.get(cid, {}).get(arm, []), key=lambda o: o["run"])
        arms[arm] = {"marks": [mark(o) for o in lst],
                     "usd": (round(sum(o["usd"] for o in lst) / len(lst) + (exa_case[cid] / 3 if arm in ("B", "B2", "C", "C2") else 0)
                                   + (b_pre_case(cid, "B" if arm == "C" else "B2") if arm in ("C", "C2") else 0), 3) if lst else None),
                     "fields": [{k: fieldcell(v) for k, v in o["fields"].items()} for o in lst]}
    rows.append({"id": cid, "name": c["media"]["name"], "market": c["media"].get("market"), "kind": c["media"]["kind"],
                 "scope": c["scope"], "structure": STRUCT[cid], "ref": short(c["reference"]),
                 "leader_def": c["leader_definition"], "reused": c.get("reused_from_oakland", False), "arms": arms})

agg = S["aggregate"]
A = [o for o in S["outputs"] if o["arm"] == "A"]
ws = [o["web_search"]["calls_executed"] for o in A]
loc = Counter((r["arm"], r["stage"]) for r in LOC)
prov = Counter(); calls = Counter()
for r in L: prov[r["provider"]] += r["usd"]; calls[r["provider"]] += 1
pilot = sum(r["usd"] for r in L if str(r.get("case", "")).startswith("smoke"))
b_pre = []
for o in S["outputs"]:
    if o["arm"] == "B":
        d = json.loads((RUNS / "B" / f"{o['case']}_r{o['run']}.json").read_text())
        b_pre.append(sum(x["usd"] for x in d["steps"] if x["step"] != "solve"))
exa_b = sum(r["usd"] for r in L if r["provider"] == "exa" and r["arm"] == "B" and not str(r["case"]).startswith("smoke"))
exa_per_case_run = exa_b / 24  # retrieval is shared by the three passes, spread here per attempt
esc = sum(1 for o in S["outputs"] if o["arm"] == "B" and o.get("escalated"))

def arm_summary(k):
    a = agg[k]
    return {"cases_all": a["cases_all_runs_complete"], "complete": a["complete_supported_outputs"], "outputs": a["outputs"],
            "errors": a["outputs_with_asserted_error"], "unsupported": a["unsupported_fields"], "unverifiable": a.get("unverifiable_fields", 0),
            "gap_avoidable": a["abstain_avoidable_fields"], "gap_justified": a["abstain_justified_fields"], "format": a["format_valid"],
            "usd_attempt": a["usd_per_attempt"], "usd_complete": a["usd_per_complete_supported"],
            "lat_med": a["latency_median_s"], "lat_range": a["latency_range_s"]}

summ = {k: arm_summary(k) for k in ["A", "B", "C", "B2", "C2", "bare"]}
# workflow attempts carry their retrieval: Exa spread per attempt
summ["B"]["usd_attempt_full"] = round(summ["B"]["usd_attempt"] + exa_per_case_run, 3)
summ["B"]["usd_complete_full"] = round(summ["B"]["usd_attempt_full"] * 24 / summ["B"]["complete"], 3) if summ["B"]["complete"] else None
c_full = statistics.mean(b_pre) + exa_per_case_run + summ["C"]["usd_attempt"]
summ["C"]["usd_attempt_full"] = round(c_full, 3)
summ["C"]["usd_complete_full"] = round(c_full * 24 / summ["C"]["complete"], 3) if summ["C"]["complete"] else None

DATA = {
    "rows": rows, "summary": summ,
    "search": {"median": statistics.median(ws), "min": min(ws), "max": max(ws)},
    "locate": {arm: {st: loc[(arm, st)] for st in ("retrieval", "extraction", "solving")} for arm in ("B", "C", "B2", "C2")},
    "escalated_B": esc,
    "solving_reasons": dict(Counter(r["reason"] for r in LOC if r["arm"] == "B" and r["stage"] == "solving")),
    "bare_blank": summ["bare"]["gap_avoidable"] + summ["bare"]["gap_justified"],
    "cost": {"openai": round(prov["openai"], 2), "gemini": round(prov["gemini"], 2), "exa": round(prov["exa"], 2),
             "total": round(sum(prov.values()), 2), "pilot": round(pilot, 2),
             "check": round(sum(r["usd"] for r in L if r["arm"] == "score"), 2),
             "calls": dict(calls)},
}
tpl = (ROOT / "site_src" / "index.template.html").read_text()
html = tpl.replace("/*DATA*/null", json.dumps(DATA, ensure_ascii=False))
(ROOT / "site" / "index.html").write_text(html)
print(json.dumps({k: v for k, v in DATA.items() if k != "rows"}, indent=1))
