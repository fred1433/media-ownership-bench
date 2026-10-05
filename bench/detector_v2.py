"""Point 11 of the external review, run OFFLINE only (no paid call in this delivery).
The measured workflow chose its extra search with evidence_state() in arms.py: it pooled current and former
owners, ignored dates, and took the first chain top in alphabetical order. This version reads the same extracted
claims and picks the missing link with dates and current/historical status. It prints, per case and pass, the
extra query each version would send. Whether the new query finds the missing document was NOT measured."""
import json, re, sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import RUNS, load_cases, norm

PAST = re.compile(r"\b(formerly|previously|former|was owned|sold (it|the station)|until \d{4}|originally|founded|predecessor|acquired .* in (19|200|201[0-9]))\b", re.I)

def year(s):
    m = re.search(r"(19|20)\d{2}", s or ""); return int(m.group()) if m else None

def v1_query(case, b):
    q = [f"{case['media']['name']} {w.replace('who owns ', '')} parent company owner" if w.startswith("who owns") else
         f"{case['media']['name']} {'chief executive' if 'leader' in w else 'owner licensee'}" for k, w in b["flags"] if k == "missing"]
    return q[:2]

def v2(case, claims):
    m = norm(case["media"]["name"]); cs = norm(case["media"].get("callsign") or "~~")
    about = lambda x: bool(norm(x)) and (m in norm(x) or norm(x) in m or cs in norm(x))
    closed = [year(c["date_in_doc"]) for c in claims if c["relation"] == "acquisition_closed" and year(c["date_in_doc"])]
    cutoff = max(closed) if closed else None
    def current(c):
        if PAST.search(c["quote"]): return False
        y = year(c["date_in_doc"])
        return not (cutoff and y and y < cutoff)
    cur = [c for c in claims if current(c)]
    owners = [c for c in cur if c["relation"] in ("licensee_of",) and about(c["object"])] or \
             [c for c in cur if c["relation"] == "owns" and about(c["object"])]
    if not owners:
        return ["legal owner", f"{case['media']['name']} licensee legal owner {case['target_date'][:4]}"]
    owners.sort(key=lambda c: year(c["date_in_doc"]) or 0, reverse=True)
    node = owners[0]["subject"]; seen = {norm(node)}; path = [node]
    up = {}
    for c in cur:
        if c["relation"] == "subsidiary_of": up.setdefault(norm(c["subject"]), []).append(c["object"])
        if c["relation"] == "owns" and not about(c["object"]): up.setdefault(norm(c["object"]), []).append(c["subject"])
    tops = {norm(c["subject"]) for c in cur if c["relation"] in ("controlled_by", "nonprofit_no_owner")}
    while norm(node) in up:
        nxt = up[norm(node)][0]
        if norm(nxt) in seen: break
        seen.add(norm(nxt)); path.append(nxt); node = nxt
    corp = re.compile(r"\b(inc|llc|corp|corporation|company|co|holdings?|group|media|broadcasting|network|networks|foundation|trust|fund|lp|league|enterprises|capital|partners|communications|university)\b", re.I)
    is_person = not corp.search(node) and len(node.split()) <= 4
    jv = any(c["relation"] == "joint_venture_partner_of" for c in cur)
    if not is_person and not jv and norm(node) not in tops and not any(norm(node) in t or t in norm(node) for t in tops):
        return [f"who owns {node}", f"{node} parent company controlling shareholder {case['target_date'][:4]}"]
    return [None, None]

def main():
    out = []
    for case in load_cases():
        for run in (1, 2, 3):
            b = json.loads((RUNS / "B" / f"{case['id']}_r{run}.json").read_text())
            what, q = v2(case, b["claims"])
            out.append({"case": case["id"], "run": run, "v1_queries": v1_query(case, b), "v2_missing": what, "v2_query": q})
    (RUNS / "detector_v2_offline.json").write_text(json.dumps(out, indent=1))
    for o in out:
        if o["run"] == 1: print(o["case"], "| v1:", o["v1_queries"], "| v2:", o["v2_query"])

if __name__ == "__main__":
    main()
