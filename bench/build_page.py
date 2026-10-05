"""Build site/index.html from site_src/index.template.html and the measured files (no number typed by hand)."""
import json, statistics, sys, re
from collections import Counter
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import ROOT, RUNS, load_cases

S = json.loads((RUNS / "scores.json").read_text())
S1 = json.loads((RUNS / "scores_v1.json").read_text())
CONV = json.loads((ROOT / "reference" / "conventions_v2.json").read_text())["cases"]
ENT = json.loads((ROOT / "reference" / "entities.json").read_text())
L = [json.loads(l) for l in (RUNS / "ledger.jsonl").read_text().splitlines() if l.strip()]
DET = json.loads((RUNS / "detector_v2_offline.json").read_text())
cases = {c["id"]: c for c in load_cases()}
GH = "https://github.com/fred1433/media-ownership-bench/blob/master/"

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
REVEALS = {
    "wfaa": "asks for the CEO of Nexstar Media Group, the acquirer to be found",
    "kpix": "asks for the CEO of Paramount Skydance Corporation, the media group to be found",
    "kfrc": "asks for the CEO of Audacy, Inc., the media group to be found",
    "krzz": "asks for the CEO of Spanish Broadcasting System, the parent to be found",
    "wfxp_mission": "asks for the president of Mission Broadcasting, the licensee to be found",
    "kpfa": "asks for the executive director of Pacifica Foundation, the licensee to be found",
}
ORDER = ["wapo", "kpix", "wfaa", "kfrc", "krzz", "wfxp_mission", "espn", "kpfa"]
first = lambda eid: ENT[eid][0]

def expects(cid):
    cv = CONV[cid]; ref = cases[cid]["reference"]
    if "branches" in cv:
        parent = "The Walt Disney Company (controlling, 72%); Hearst (18%) and the NFL (10%) may be named as further owners"
    elif cv.get("self_owned"):
        parent = f"none above {first(cv['owner'][0])}, or that entity itself"
    else:
        span = [first(e) for e in cv["parent_ok"]]
        parent = span[0] if len(span) == 1 else f"any of: {', '.join(span)}"
    c = cv["controller"]
    ctrl = {"none": "none", "not_established": "not established in the record; a blank is right"}.get(c["status"]) or ", ".join(first(e) for e in c["ids"][:1])
    if c.get("accept_also"): ctrl += f" ({first(c['accept_also'][0])} also accepted)"
    ld = cv["leader"]
    return {"legal_owner": first(cv["owner"][0]), "ultimate_parent": parent, "controlling_person": ctrl,
            "leader": f"{first(ld['ids'][0])} ({ld['role'].split('|')[0]}{', as of ' + ref['leader'].get('as_of') if ref['leader'].get('as_of') else ''})"}

CONVTXT = {
    "legal_owner": "The entity that legally holds the outlet (the FCC licensee for a station). Any other name in the answer is an error.",
    "ultimate_parent": "Right when every name lies on the documented chain from the media group up to the top of the control chain. A person or family counts only where a primary source calls it the ultimate parent. With several owners, the controlling one must be named and each named owner needs its own passage.",
    "controlling_person": "The person or family that controls the top of the chain, or none. For a widely held company, none when its latest proxy shows no controlling holder.",
    "leader": "Only the person matching this case's leader definition; the passage must show the role.",
}
WHYTXT = {"match_located": "matches the record, passage located", "match_weak": "matches the record, but", "match_unverifiable": "matches the record, but",
          "error": "wrong", "unsupported": "asserted where the record is not established", "abstain_avoidable": "blank, though the record has it",
          "abstain_justified": "blank, as the record"}

def raw_answer(arm, cid, run):
    return json.loads(json.loads((RUNS / arm / f"{cid}_r{run}.json").read_text())["text"])

def claim_dates(arm, cid, run):
    if arm not in ("B", "C"): return {}
    b = json.loads((RUNS / "B" / f"{cid}_r{run}.json").read_text())
    d = {}
    for c in b["claims"]:
        if c.get("url") and c.get("date_in_doc"): d.setdefault(c["url"], c["date_in_doc"])
    return d

out_by = {(o["arm"], o["case"], o["run"]): o for o in S["outputs"]}
def mark(o): return "x" if o["asserted_error"] else ("ok" if o["complete"] else "gap")

# costs: two allocations of the same measured spend
exa_case = Counter()
for r in L:
    if r["provider"] == "exa" and r["arm"] == "B": exa_case[r["case"]] += r["usd"]
def bsteps(cid, run, arm="B"):
    d = json.loads((RUNS / arm / f"{cid}_r{run}.json").read_text()); return d["steps"]

rows = []
for cid in ORDER:
    arms = {}
    for arm in ["A", "B", "C"]:
        runs = []
        for run in (1, 2, 3):
            o = out_by[(arm, cid, run)]; ans = raw_answer(arm, cid, run); dates = claim_dates(arm, cid, run)
            fields = {}
            for f, v in o["fields"].items():
                cits = ans[f]["citations"]
                checks = v.get("citations") or []
                cl = []
                for i, ct in enumerate(cits[:3]):
                    ck = checks[i] if i < len(checks) else {}
                    cl.append({"url": ct["url"], "q": ct["quote"][:260], "date": dates.get(ct["url"]),
                               "found": ck.get("passage_found"), "fetched": ck.get("fetched")})
                nm = ans[f].get("names") if f != "leader" else ([ans[f]["name"]] if ans[f].get("name") else [])
                nm = [re.sub(r"\s*[—–]\s*", ", ", n) for n in (nm or [])]
                fields[f] = {"v": v["verdict"], "why": v.get("why"), "names": nm, "status": ans[f]["status"], "dec": v.get("decision"), "cites": cl}
            runs.append({"mark": mark(o), "fields": fields, "raw": GH + o["raw"]})
        usd_pass = statistics.mean(out_by[(arm, cid, r)]["usd"] for r in (1, 2, 3))
        if arm == "B": usd_pass += exa_case[cid] / 3
        if arm == "C": usd_pass += exa_case[cid] / 3 + statistics.mean(sum(s["usd"] for s in bsteps(cid, r) if s["step"] != "solve") for r in (1, 2, 3))
        arms[arm] = {"runs": runs, "usd": round(usd_pass, 3)}
    c = cases[cid]
    rows.append({"id": cid, "name": c["media"]["name"], "market": c["media"].get("market"), "structure": STRUCT[cid],
                 "reused": c.get("reused_from_oakland", False), "leader_def": c["leader_definition"], "question": c["question"],
                 "expects": expects(cid), "arms": arms})

def summ(agg, k, v2=True):
    a = agg[k]
    if v2:
        return {"cases_all": a["cases_all_runs_complete"], "complete": a["complete_outputs"], "errors": a["outputs_with_asserted_error"],
                "weak": a["weak_fields"], "unverifiable": a["unverifiable_fields"], "gap": a["abstain_avoidable_fields"],
                "gap_ok": a["abstain_justified_fields"], "outputs": a["outputs"], "decisions": a.get("decisions")}
    return {"cases_all": a["cases_all_runs_complete"], "complete": a["complete_supported_outputs"], "errors": a["outputs_with_asserted_error"],
            "gap": a["abstain_avoidable_fields"]}

SUM = {k: summ(S["aggregate"], k) for k in ["A", "B", "C", "B2", "C2", "bare"]}
SUM1 = {k: summ(S1["aggregate"], k, False) for k in ["A", "B", "C"]}

# latency per step (seconds), and the two cost allocations
lat = {"A": [], "B_extract": [], "B_solve": [], "C_solve": [], "C_rebuilt": []}
cost = {"A": [], "B_shared": [], "B_new": [], "C_shared": [], "C_new": []}
for cid in ORDER:
    for run in (1, 2, 3):
        a = json.loads((RUNS / "A" / f"{cid}_r{run}.json").read_text())
        lat["A"].append(a["latency_s"]); cost["A"].append(a["usd"])
        st = bsteps(cid, run); ex = sum(s["latency_s"] for s in st if s["step"] != "solve"); so = sum(s["latency_s"] for s in st if s["step"] == "solve")
        cc = json.loads((RUNS / "C" / f"{cid}_r{run}.json").read_text())
        lat["B_extract"].append(ex); lat["B_solve"].append(so); lat["C_solve"].append(cc["latency_s"]); lat["C_rebuilt"].append(ex + cc["latency_s"])
        pre = sum(s["usd"] for s in st if s["step"] != "solve"); bsolve = sum(s["usd"] for s in st if s["step"] == "solve")
        cost["B_shared"].append(pre + bsolve + exa_case[cid] / 3); cost["B_new"].append(pre + bsolve + exa_case[cid])
        cost["C_shared"].append(pre + cc["usd"] + exa_case[cid] / 3); cost["C_new"].append(pre + cc["usd"] + exa_case[cid])
LAT = {k: {"med": round(statistics.median(v)), "min": round(min(v)), "max": round(max(v))} for k, v in lat.items()}
COST = {k: round(statistics.mean(v), 3) for k, v in cost.items()}
for k, arm in (("A", "A"), ("B_shared", "B"), ("C_shared", "C")):
    COST[k + "_per_complete"] = round(sum(cost[k]) / SUM[arm]["complete"], 2) if SUM[arm]["complete"] else None

prov = Counter(); calls = Counter()
for r in L: prov[r["provider"]] += r["usd"]; calls[r["provider"]] += 1
extra = [{"case": r["case"], "query": r["query"], "usd": r["usd"]} for r in L if r["provider"] == "exa" and r.get("query")
         and r["case"] in ("wfxp_mission", "kpix", "wfaa") and "parent company owner" in r["query"]]
det = {d["case"]: d["v2_query"] for d in DET if d["run"] == 1}
A_out = [o for o in S["outputs"] if o["arm"] == "A"]
whys = Counter(v.get("why") for o in A_out for v in o["fields"].values() if v["verdict"] in ("match_weak", "match_unverifiable"))

DATA = {"rows": rows, "sum": SUM, "sum1": SUM1, "lat": LAT, "cost": COST, "reveals": REVEALS, "conv": CONVTXT, "whytxt": WHYTXT,
        "search": {"median": statistics.median(o["web_search"]["calls_executed"] for o in A_out),
                   "min": min(o["web_search"]["calls_executed"] for o in A_out), "max": max(o["web_search"]["calls_executed"] for o in A_out)},
        "escalated_B": sum(1 for o in S["outputs"] if o["arm"] == "B" and o.get("escalated")),
        "extra": extra, "det": det, "a_whys": dict(whys),
        "bill": {"openai": round(prov["openai"], 2), "gemini": round(prov["gemini"], 2), "exa": round(prov["exa"], 2),
                 "total": round(sum(prov.values()), 2), "pilot": round(sum(r["usd"] for r in L if str(r.get("case", "")).startswith("smoke")), 2),
                 "check": round(sum(r["usd"] for r in L if r["arm"] == "score"), 2), "calls": dict(calls), "runs": sum(v["outputs"] for v in SUM.values())}}
tpl = (ROOT / "site_src" / "index.template.html").read_text()
html = tpl.replace("/*DATA*/null", json.dumps(DATA, ensure_ascii=False))
assert "—" not in html, "em dash in page"
(ROOT / "site" / "index.html").write_text(html)
print(json.dumps({k: DATA[k] for k in ("sum", "sum1", "lat", "cost", "search", "escalated_B", "extra", "det", "a_whys", "bill")}, indent=0)[:4000])
