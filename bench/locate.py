"""Locate where each avoidable abstention or error of the workflow arms first becomes observable:
retrieval (the reference answer is in no retrieved document), extraction (in a document but in no extracted claim),
or solving (in the dossier given to the solver, which still abstained or answered wrong)."""
import json, sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import RUNS, PRIVATE, load_cases, norm
from score import ADJ, names_of

def ref_names(case, field):
    r = case["reference"]; extra = ADJ.get(f"{case['id']}:{field}", {}).get("extra_aliases", []) + ADJ.get(f"{case['id']}:{field}", {}).get("accept_also", [])
    if field == "legal_owner": n = names_of(r["legal_owner"])
    elif field == "ultimate_parent": n = [x for e in r["ultimate_parent"].get("entities", []) for x in names_of(e)]
    elif field == "controlling_person": n = r["controlling_person"].get("names", [])
    else: n = ([r["leader"]["name"]] if r["leader"].get("name") else []) + r["leader"].get("aliases", [])
    return n + extra

def key_tokens(names):
    out = set()
    for n in names:
        t = [x for x in norm(n).split() if len(x) >= 4]
        if t: out.add(max(t, key=len) if len(t) == 1 else " ".join(t[-2:]) if len(t) > 1 else t[0])
    return out

def present(tokens, text):
    t = norm(text); return any(k in t for k in tokens)

def main():
    cases = {c["id"]: c for c in load_cases()}
    scores = json.loads((RUNS / "scores.json").read_text())["outputs"]
    rows = []
    for s in scores:
        if s["arm"] not in ("B", "C", "B2", "C2"): continue
        case = cases[s["case"]]
        docs = json.loads((PRIVATE / f"docs_{case['id']}.json").read_text())
        ex = PRIVATE / f"extra_{case['id']}.json"
        if ex.exists(): docs += json.loads(ex.read_text())
        doc_text = " ".join(d["text"][:3500] for d in docs)
        barm = "B2" if s["arm"] in ("B2", "C2") else "B"
        b = json.loads((RUNS / barm / f"{case['id']}_r{s['run']}.json").read_text())
        claims_text = " ".join(f"{c['subject']} {c['object']} {c['quote']}" for c in b["claims"])
        for f, v in s["fields"].items():
            if v["verdict"] not in ("abstain_avoidable", "error"): continue
            if case["reference"][f if f != "ultimate_parent" else "ultimate_parent"].get("status") in ("none", "not_applicable") and f == "controlling_person":
                stage = "solving"  # nothing to retrieve: the answer is 'none'
            else:
                toks = key_tokens(ref_names(case, f))
                stage = "retrieval" if not present(toks, doc_text) else ("extraction" if not present(toks, claims_text) else "solving")
            reason = None
            if stage == "solving":
                if f == "controlling_person": reason = "no_controller"   # record says none / not applicable
                elif f == "ultimate_parent": reason = "parent_not_chained"
                else: reason = "dated_source_only"                      # Post owner, KPFA leader
            rows.append({"arm": s["arm"], "case": case["id"], "run": s["run"], "field": f, "verdict": v["verdict"], "stage": stage, "reason": reason})
    (RUNS / "locate.json").write_text(json.dumps(rows, indent=1))
    agg = {}
    for r in rows:
        agg.setdefault(r["arm"], {}).setdefault(r["stage"], 0); agg[r["arm"]][r["stage"]] += 1
    print(json.dumps(agg))
    by = {}
    for r in rows:
        if r["arm"] == "B": by.setdefault((r["case"], r["field"]), set()).add(r["stage"])
    for k, v in sorted(by.items()): print(k, v)

if __name__ == "__main__":
    main()
