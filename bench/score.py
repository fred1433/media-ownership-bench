"""Scorer v2 (after the external review). Usage: python bench/score.py [--fetch-free]
Reads runs/, reference/cases.json, reference/conventions_v2.json, reference/entities.json, reference/adjudications.json.
Default: no network; citation checks come from the published runs/citation_checks.json.
--fetch-free: cited pages missing from the checks are fetched with a plain HTTP GET (no paid API), text kept private.

Two adjudications per answer (review point 5):
- coverage: is the record complete end to end? (complete = every field matches the record with a located
  passage, or is a justified blank)
- solver decision at constant evidence (workflow arms B, C, B2, C2 only): was the solver's choice right given the
  dossier it received?

What 'located' means (review point 3, mechanical part only): the cited passage is found in the document, it names
the entity, it states the relation (role word for a leader, ownership or control vocabulary otherwise), it is not
cut just before a qualifier ('except ...', 'unless ...'), and with several parents each named branch has its own
passage. Relevance to the target date is NOT checked mechanically: hence 'matches the record, passage located',
never 'supported'."""
import json, os, re, statistics, sys, unicodedata
from pathlib import Path
import httpx, jsonschema
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import RUNS, PRIVATE, ROOT, load_cases, ANSWER_SCHEMA

FIELDS = ["legal_owner", "ultimate_parent", "controlling_person", "leader"]
CONV = json.loads((ROOT / "reference" / "conventions_v2.json").read_text())["cases"]
ENT = {k: v for k, v in json.loads((ROOT / "reference" / "entities.json").read_text()).items() if not k.startswith("_")}
ADJ = {k: v for k, v in json.loads((ROOT / "reference" / "adjudications.json").read_text()).items() if not k.startswith("_")}
CHECKS_PATH = RUNS / "citation_checks.json"
CHECKS = {f"{c['url']}\n{c['quote']}": c for c in json.loads(CHECKS_PATH.read_text())} if CHECKS_PATH.exists() else {}
# Checks done by reading the page by other means when a script is refused (each with how it was done)
for m in json.loads((ROOT / "reference" / "manual_checks.json").read_text()):
    CHECKS[f"{m['url']}\n{m['quote']}"] = {**m, "manual": True}
PRIVATE.mkdir(parents=True, exist_ok=True)
CACHE_P = PRIVATE / "source_cache.json"
CACHE = json.loads(CACHE_P.read_text()) if CACHE_P.exists() else {}
FETCH_FREE = "--fetch-free" in sys.argv
TARGET_YEAR = 2026


def nl(s):
    s = (s or "").replace("’", "'").replace("‘", "'")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()

NAME2ID = {}
for eid, names in ENT.items():
    for n in names:
        k = nl(n)
        assert NAME2ID.get(k, eid) == eid, f"alias {n} maps to two entities"
        NAME2ID[k] = eid

def ids_of(name):
    for cand in [name] + [p.strip() for p in re.split(r" — | - | \(|;|:", name)]:
        if nl(cand) in NAME2ID: return NAME2ID[nl(cand)]
    return None

def surname(alias):
    t = [x for x in nl(alias).split() if x not in ("jr", "sr", "ii", "iii", "family", "the")]
    return t[-1] if t else ""

PERSONS = {"raul_alarcon", "david_ellison", "larry_ellison", "perry_sook", "nancie_smith", "dennis_thatcher", "jeff_bezos",
           "jeff_donofrio", "jimmy_pitaro", "stephanie_wells", "kelli_turner"}

def names_entity(quote, eid):
    q = " " + nl(quote) + " "
    if any(" " + nl(a) + " " in q for a in ENT[eid]): return True
    if eid in PERSONS or eid == "ellison_family":
        return any(" " + surname(a) + " " in q for a in ENT[eid] if surname(a))
    return False

REL = {
    "legal_owner": r"licens|own|subsidiar|parent|holding|acqui|property of|publish|d b a|doing business|operat|\bfm\b|\btv\b",
    "ultimate_parent": r"own|subsidiar|parent|holding|control|stockholder|acqui|interest|percent|\d ?%|\d percent",
    "controller": r"control|own|majority|shareholder|stockholder|voting|interest|percent|\d ?%",
    "none": r"no entity or individual|no individual|no person|nonprofit|non profit|not for profit|no capital stock|no shareholder|more than 5|widely held|no member|charitable|501 c",
}
QUALB = re.compile(r"\b(except as noted below|except as set forth|except as otherwise|other than as|unless otherwise)\W{0,3}$", re.I)
QUAL = re.compile(r"^\W{0,3}(except|other than|unless|provided that|subject to|but not|save for)\b", re.I)

def free_fetch(url):
    if url in CACHE: return CACHE[url]
    if not FETCH_FREE: return None
    txt = None
    try:
        ua = "media-ownership-bench research script (theaipipe.com)" if "sec.gov" in url else "Mozilla/5.0 (Macintosh) Chrome/126 Safari/537.36"
        r = httpx.get(url, timeout=40, follow_redirects=True, headers={"User-Agent": ua})
        if r.status_code == 200 and ("pdf" in r.headers.get("content-type", "") or r.content[:4] == b"%PDF"):
            import io, pypdf
            t = " ".join((p.extract_text() or "") for p in pypdf.PdfReader(io.BytesIO(r.content)).pages)
            if len(t.strip()) > 500: txt = t
        elif r.status_code == 200 and "html" in r.headers.get("content-type", ""):
            t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.S)
            t = re.sub(r"<[^>]+>", " ", t); t = re.sub(r"&nbsp;|&#160;", " ", t); t = re.sub(r"&amp;", "&", t)
            if len(t.strip()) > 500: txt = t
    except Exception:
        pass
    CACHE[url] = txt
    return txt

def located(quote, text):
    if not text or not quote: return False, None
    t = nl(text); parts = [nl(p) for p in re.split(r"\.\.\.|…", quote) if len(nl(p)) > 3]
    if not parts or not all(p in t for p in parts):
        import difflib
        ok = True
        for p in parts:
            if p in t: continue
            sm = difflib.SequenceMatcher(None, t, p, autojunk=False); b = sm.find_longest_match(0, len(t), 0, len(p))
            if b.size < 0.8 * len(p): ok = False
        return ok, None
    i0 = t.index(parts[0]); after = t[t.index(parts[-1]) + len(parts[-1]):][:60]
    before = t[max(0, i0 - 45):i0]
    return True, bool(QUAL.match(after) or QUALB.search(before))

def check(url, quote):
    key = f"{url}\n{quote}"
    c = dict(CHECKS.get(key) or {})
    if c.get("manual"): return c
    if "qualifier_cut" not in c or not c:
        txt = CACHE.get(url) if url in CACHE else free_fetch(url)
        if txt is not None:
            found, qual = located(quote, txt)
            c.update({"url": url, "quote": quote, "fetched": True, "passage_found": found, "qualifier_cut": qual})
        elif not c:
            c = {"url": url, "quote": quote, "fetched": False, "passage_found": False, "qualifier_cut": None}
        else:
            c.setdefault("qualifier_cut", None)
    CHECKS[key] = c
    return c

def support(field, cites, eids, rel_key, role=None):
    """Returns (label, reason, details). eids: entities the passage must name (any of them)."""
    det = []
    for ct in cites:
        c = check(ct["url"], ct["quote"])
        rel = re.search(role if role else REL[rel_key], nl(ct["quote"])) is not None
        nm = (not eids) or any(names_entity(ct["quote"], e) for e in eids)
        det.append({"url": ct["url"], "fetched": c["fetched"], "passage_found": c["passage_found"], "names_entity": nm,
                    "relation_shown": rel, "qualifier_cut": c.get("qualifier_cut")})
    good = [d for d in det if d["passage_found"] and d["names_entity"] and d["relation_shown"] and not d["qualifier_cut"]]
    if good: return "match_located", None, det
    if not det: return "match_weak", "no citation", det
    if not any(d["fetched"] for d in det): return "match_unverifiable", "cited pages refused a plain fetch", det
    if any(d["qualifier_cut"] for d in det if d["passage_found"]): return "match_weak", "passage cut before its qualifier", det
    if not any(d["passage_found"] for d in det): return "match_weak", "passage not found in the cited document", det
    if not any(d["passage_found"] and d["names_entity"] for d in det): return "match_weak", "passage does not name the entity", det
    return "match_weak", "passage does not state the relation", det

def field_verdict(cid, field, ans):
    cv = CONV[cid]; adj = ADJ.get(f"{cid}:{field}", {})
    a = ans[field]; ms = a["status"]
    names = a.get("names") if field != "leader" else ([a["name"]] if a.get("name") else [])
    ids = [ids_of(n) for n in names]
    unmatched = [n for n, i in zip(names, ids) if i is None]
    out = {"status": ms, "names": names, "ids": ids}
    if field == "legal_owner":
        if ms != "established": return {**out, "verdict": "abstain_avoidable"}
        if unmatched or not ids or any(i not in cv["owner"] for i in ids):
            return {**out, "verdict": "error", "why": "names an entity other than the legal owner" + (f" ({unmatched[0]})" if unmatched else "")}
        lab, why, det = support(field, a["citations"], cv["owner"], "legal_owner")
        return {**out, "verdict": lab, "why": why, "citations": det}
    if field == "ultimate_parent":
        if ms == "not_established": return {**out, "verdict": "abstain_avoidable"}
        if ms == "none":
            if cv.get("self_owned"):
                lab, why, det = support(field, a["citations"], [], "none")
                return {**out, "verdict": lab, "why": why, "citations": det}
            return {**out, "verdict": "error", "why": "says no parent"}
        if "paramount_ambiguous" in ids:
            return {**out, "verdict": "match_weak", "why": "ambiguous name: 'Paramount' may be Paramount Global (below the media group) or Paramount Skydance"}
        if unmatched: return {**out, "verdict": "error", "why": f"unrecognized name: {unmatched[0]}"}
        if "branches" in cv:
            bmap = {e: b for b, es in cv["branches"].items() for e in es}
            if any(i not in bmap for i in ids): return {**out, "verdict": "error", "why": "names an entity that is not an owner"}
            named = sorted({bmap[i] for i in ids})
            if cv["controlling_branch"] not in named: return {**out, "verdict": "error", "why": "misses the controlling owner"}
            dets, labs = [], []
            for b in named:
                lab, why, det = support(field, a["citations"], cv["branches"][b], "ultimate_parent"); dets += det; labs.append((b, lab, why))
            bad = [x for x in labs if x[1] != "match_located"]
            if not bad: return {**out, "verdict": "match_located", "citations": dets, "branches": named}
            return {**out, "verdict": "match_weak" if any(x[1] == "match_weak" for x in bad) else "match_unverifiable",
                    "why": f"no located passage for the {bad[0][0]} branch", "citations": dets, "branches": named}
        if any(i not in cv["parent_ok"] for i in ids):
            return {**out, "verdict": "error", "why": "names an entity outside the chain from the media group to the top"}
        lab, why, det = support(field, a["citations"], [i for i in ids], "ultimate_parent")
        return {**out, "verdict": lab, "why": why, "citations": det}
    if field == "controlling_person":
        rs = cv["controller"]["status"]; acc = cv["controller"].get("ids", []) + cv["controller"].get("accept_also", []) + adj.get("accept_also_ids", [])
        if rs in ("none",):
            if ms in ("none", "not_applicable"):
                lab, why, det = support(field, a["citations"], [], "none")
                return {**out, "verdict": lab, "why": why, "citations": det}
            if ms == "not_established": return {**out, "verdict": "abstain_avoidable"}
            return {**out, "verdict": "error", "why": "names a controller where the record has none"}
        if rs == "established":
            if ms == "not_established": return {**out, "verdict": "abstain_avoidable"}
            if ms != "established" or unmatched or not ids or any(i not in acc for i in ids):
                return {**out, "verdict": "error", "why": "wrong or missing controller"}
            lab, why, det = support(field, a["citations"], ids, "controller")
            return {**out, "verdict": lab, "why": why, "citations": det}
        # record not established
        if ms == "not_established": return {**out, "verdict": "abstain_justified"}
        if ms == "established" and ids and not unmatched and all(i in acc for i in ids):
            lab, why, det = support(field, a["citations"], ids, "controller")
            return {**out, "verdict": lab, "why": why, "citations": det, "note": "accepted by adjudication"}
        return {**out, "verdict": "unsupported", "why": "asserted where the record is not established"}
    # leader
    ld = cv["leader"]
    if ms != "established": return {**out, "verdict": "abstain_avoidable"}
    if unmatched or not ids or ids[0] not in ld["ids"]: return {**out, "verdict": "error", "why": "wrong person"}
    lab, why, det = support(field, a["citations"], ld["ids"], "leader", role=ld["role"])
    return {**out, "verdict": lab, "why": why, "citations": det}

GOOD = ("match_located", "abstain_justified")

def dossier_decision(cid, field, v, bout):
    """Solver decision at constant evidence (B, C, B2, C2)."""
    cv = CONV[cid]
    want = {"legal_owner": cv["owner"], "ultimate_parent": cv["parent_ok"] + sum(cv.get("branches", {}).values(), []),
            "controlling_person": cv["controller"].get("ids", []), "leader": cv["leader"].get("ids", [])}[field]
    claims = bout["claims"]
    mention = [c for c in claims if any(names_entity(c["subject"] + " " + c["object"] + " " + c["quote"], e) for e in want)] if want else []
    def dated_only(cs):
        ys = [int(m.group()) for c in cs for m in [re.search(r"(19|20)\d{2}", c.get("date_in_doc") or "")] if m]
        return bool(cs) and len(ys) == len(cs) and max(ys) < TARGET_YEAR - 1
    if v["verdict"] == "error": return "decision error at constant evidence"
    if v["verdict"] == "unsupported": return "asserted beyond the dossier"
    if v["verdict"] == "abstain_justified": return "right decision (blank)"
    if v["verdict"] == "abstain_avoidable":
        if field == "controlling_person" and cv["controller"]["status"] == "none":
            return "held back on 'none' (dossier does not say it)" if not any(re.search(REL["none"], nl(c["quote"])) for c in claims) else "held back on 'none' although the dossier says it"
        if not mention: return "defensible blank: the dossier lacks the fact (coverage)"
        if dated_only(mention): return "defensible blank: only dated evidence in the dossier"
        return "held back although the dossier states it"
    cited = {c["url"] for c in (v.get("citations") or [])}
    used = [c for c in mention if c.get("url") in cited]
    if used and dated_only(used): return "right value, currency not supported by its citation"
    return "right decision"

def score_output(case, path):
    d = json.loads(path.read_text())
    out = {"case": case["id"], "arm": d["arm"], "run": d["run"], "usd": d.get("usd_total", d.get("usd")),
           "latency_s": d.get("latency_total_s", d.get("latency_s")), "web_search": d.get("web_search"),
           "escalated": d.get("escalated"), "raw": f"runs/{d['arm']}/{path.name}"}
    if d["arm"] in ("B", "B2"):
        out["latency_steps"] = {s["step"]: s["latency_s"] for s in d.get("steps", [])}
    try:
        ans = json.loads(d["text"]); jsonschema.validate(ans, ANSWER_SCHEMA); out["format_valid"] = True
    except Exception:
        out.update(format_valid=False, fields={}, complete=False, asserted_error=False); return out
    f = {k: field_verdict(case["id"], k, ans) for k in FIELDS}
    if d["arm"] in ("B", "C", "B2", "C2"):
        bout = json.loads((RUNS / ("B" if d["arm"] in ("B", "C") else "B2") / path.name).read_text())
        for k in FIELDS: f[k]["decision"] = dossier_decision(case["id"], k, f[k], bout)
    v = [x["verdict"] for x in f.values()]
    out.update(fields=f, complete=all(x in GOOD for x in v), asserted_error="error" in v,
               weak=v.count("match_weak"), unverifiable=v.count("match_unverifiable"), unsupported=v.count("unsupported"),
               abstain_justified=v.count("abstain_justified"), abstain_avoidable=v.count("abstain_avoidable"),
               answer={k: ans[k] for k in ("legal_owner", "operator", "ultimate_parent", "controlling_person", "leader")})
    return out

def main():
    cases = {c["id"]: c for c in load_cases()}
    rows = []
    for arm in ["A", "B", "C", "B2", "C2", "bare"]:
        for p in sorted((RUNS / arm).glob("*.json")):
            cid = p.stem.rsplit("_r", 1)[0]
            if cid in cases: rows.append(score_output(cases[cid], p))
    if CACHE: CACHE_P.write_text(json.dumps(CACHE))
    CHECKS_PATH.write_text(json.dumps(sorted([c for c in CHECKS.values() if not c.get("manual")], key=lambda c: (c["url"], c["quote"])), indent=1))
    agg = {}
    for arm in ["A", "B", "C", "B2", "C2", "bare"]:
        r = [x for x in rows if x["arm"] == arm]
        if not r: continue
        pc = {}
        for x in r: pc.setdefault(x["case"], []).append(x)
        n = max(len(v) for v in pc.values())
        comp = sum(x["complete"] for x in r); usd = [x["usd"] for x in r]; lat = [x["latency_s"] for x in r]
        dec = {}
        for x in r:
            for fv in x["fields"].values():
                if "decision" in fv: dec[fv["decision"]] = dec.get(fv["decision"], 0) + 1
        agg[arm] = {"outputs": len(r), "runs_per_case": n, "cases": len(pc),
                    "cases_all_runs_complete": sum(all(x["complete"] for x in v) and len(v) == n for v in pc.values()),
                    "complete_outputs": comp, "outputs_with_asserted_error": sum(x["asserted_error"] for x in r),
                    "weak_fields": sum(x.get("weak", 0) for x in r), "unverifiable_fields": sum(x.get("unverifiable", 0) for x in r),
                    "unsupported_fields": sum(x.get("unsupported", 0) for x in r),
                    "abstain_justified_fields": sum(x.get("abstain_justified", 0) for x in r),
                    "abstain_avoidable_fields": sum(x.get("abstain_avoidable", 0) for x in r),
                    "format_valid": sum(x["format_valid"] for x in r),
                    "per_case": {k: f"{sum(x['complete'] for x in v)}/{len(v)}" for k, v in pc.items()},
                    "usd_total": round(sum(usd), 4), "usd_per_attempt": round(sum(usd) / len(usd), 4),
                    "latency_median_s": statistics.median(lat), "latency_range_s": [min(lat), max(lat)],
                    "decisions": dec or None}
    (RUNS / "scores.json").write_text(json.dumps({"scorer": "v2", "aggregate": agg, "outputs": rows}, indent=1))
    for arm, a in agg.items():
        print(arm, {k: a[k] for k in ("cases_all_runs_complete", "complete_outputs", "outputs_with_asserted_error", "weak_fields", "unverifiable_fields", "abstain_avoidable_fields", "abstain_justified_fields")}, a["decisions"] or "")

if __name__ == "__main__":
    main()
