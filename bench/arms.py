"""The three arms (A, B, C) and the secondary bare-API control."""
import hashlib, json, os, time
import httpx
from openai import OpenAI
from google import genai
from google.genai import types as gt
from common import (ANSWER_SCHEMA, SYSTEM, TARGET_DATE, OPENAI_MODEL, GEMINI_MODEL, EFFORT, LEDGER, PRIVATE,
                    openai_cost, gemini_cost, user_prompt, norm, SDK)

oai = OpenAI(timeout=600, max_retries=0)
gem = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
FMT = {"format": {"type": "json_schema", "name": "ownership_answer", "schema": ANSWER_SCHEMA, "strict": True}}


def _resp_dict(r):
    return json.loads(r.model_dump_json())


def call_openai(arm, case_id, run, instructions, inp, *, tools=None, effort=EFFORT, fmt=FMT, reserve=0.6, tag="final"):
    LEDGER.guard(reserve)
    t0 = time.time(); retries = 0
    while True:
        try:
            kw = dict(model=OPENAI_MODEL, instructions=instructions, input=inp, reasoning={"effort": effort}, text=fmt)
            if tools:
                kw["tools"] = tools; kw["include"] = ["web_search_call.action.sources"]
            r = oai.responses.create(**kw)
            break
        except Exception as e:  # one retry on transient errors, recorded
            retries += 1
            if retries > 1: raise
            time.sleep(5)
    d = _resp_dict(r)
    items = d.get("output", [])
    ws = [i for i in items if i.get("type") == "web_search_call"]
    actions = [((i.get("action") or {}).get("type")) for i in ws]
    usage = d.get("usage") or {}
    usd = openai_cost(usage, search_calls=len(ws))
    LEDGER.add(provider="openai", arm=arm, case=case_id, run=run, step=tag, model_requested=OPENAI_MODEL,
               model_returned=d.get("model"), usage=usage, web_search_calls=len(ws), usd=usd)
    return {"text": r.output_text, "raw": d, "usage": usage, "usd": usd, "latency_s": round(time.time() - t0, 1),
            "web_search": {"tool_enabled": bool(tools), "calls_executed": len(ws), "actions": actions, "forced": False},
            "model_returned": d.get("model"), "retries": retries}


def arm_a(case, run):
    """A: native web search, date injected, defined output."""
    out = call_openai("A", case["id"], run, SYSTEM + "\nUse web search to find current evidence.", user_prompt(case),
                      tools=[{"type": "web_search"}], reserve=1.0)
    return out


def arm_bare(case, run):
    """Secondary control: same model, same prompt, no tools."""
    return call_openai("bare", case["id"], run, SYSTEM, user_prompt(case), reserve=0.3)


# ---------------- B: bounded workflow ----------------
def exa_search(query, case_id, n=5, chars=4000):
    LEDGER.guard(0.05)
    r = httpx.post("https://api.exa.ai/search", timeout=60, headers={"x-api-key": os.environ["EXA_API_KEY"]},
                   json={"query": query, "numResults": n, "type": "auto",
                         "contents": {"text": {"maxCharacters": chars}}})
    r.raise_for_status(); d = r.json()
    usd = (d.get("costDollars") or {}).get("total", 0.0)
    LEDGER.add(provider="exa", arm="B", case=case_id, run=0, step="search", query=query, usd=usd)
    return [{"url": x["url"], "title": x.get("title"), "published": x.get("publishedDate"), "text": x.get("text") or ""}
            for x in d.get("results", [])]


def queries_for(case):
    m = case["media"]; name = m["name"]; cs = m.get("callsign")
    q = [f"{name} owner parent company", f"{name} sold acquisition 2026", f"{name} chief executive 2026"]
    if cs:
        q.insert(0, f"{cs} FCC licensee ownership report")
        q.append(f"{cs} {m.get('market') or ''} station owner")
    return q


def collect(case):
    """Discovery: fixed query templates, cached once per case (shared by B and C, billed once)."""
    PRIVATE.mkdir(parents=True, exist_ok=True)
    p = PRIVATE / f"docs_{case['id']}.json"
    if p.exists():
        return json.loads(p.read_text())
    docs, seen = [], set()
    for q in queries_for(case):
        for d in exa_search(q, case["id"]):
            if d["url"] in seen: continue
            seen.add(d["url"]); d["query"] = q; docs.append(d)
    for i, d in enumerate(docs):
        d["id"] = f"D{i+1}"; d["sha256"] = hashlib.sha256(d["text"].encode()).hexdigest()[:16]
    p.write_text(json.dumps(docs, indent=1))
    return docs


RELATIONS = ["licensee_of", "owns", "subsidiary_of", "controlled_by", "joint_venture_partner_of", "operates_under_agreement",
             "chief_executive_of", "other_officer_of", "renamed_from", "acquisition_announced", "acquisition_approved",
             "acquisition_closed", "sale_abandoned", "nonprofit_no_owner", "other"]
CLAIMS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["claims"], "properties": {"claims": {
    "type": "array", "items": {"type": "object", "additionalProperties": False,
        "required": ["doc", "subject", "relation", "object", "quote", "date_in_doc"],
        "properties": {"doc": {"type": "string"}, "subject": {"type": "string"},
                       "relation": {"type": "string", "enum": RELATIONS}, "object": {"type": "string"},
                       "quote": {"type": "string"}, "date_in_doc": {"type": ["string", "null"]}}}}}}

EXTRACT_SYS = f"""Extract ownership evidence from the documents. Today's date is {TARGET_DATE}.
Return one claim per relation stated in a document: subject, relation, object, the verbatim quote (copied exactly, at most 40 words) and the date the document gives for that fact if any.
Direction: 'X licensee_of STATION', 'X owns Y', 'Y subsidiary_of X', 'Y controlled_by PERSON', 'PERSON chief_executive_of ENTITY', 'BUYER acquisition_announced TARGET'.
Only claims about the outlet in question, its owners, parents, operators, controlling persons, executives and transactions. Do not infer; skip anything not stated."""


# Intervention tested after the first measurement (diagnosis: the facts were in the documents, the extractor
# kept only claims about the outlet itself, so links above the first owner never reached the solver).
EXTRACT_SYS_V2 = EXTRACT_SYS.replace(
    "Only claims about the outlet in question, its owners, parents, operators, controlling persons, executives and transactions.",
    "Follow the chain upward: keep claims about the outlet AND about every entity named as its owner, licensee, operator, parent "
    "or controller, then about their own parents, controlling persons, chief executives and transactions, at every level. "
    "A link between two upper entities (for example 'Company X subsidiary_of Holding Y' or 'Holding Y controlled_by Person Z') "
    "is wanted even if the outlet is not named in that sentence.")


def doc_block(docs, chars=3500):
    return "\n\n".join(f"[{d['id']}] {d['url']}\nTitle: {d.get('title')}\nPublished: {d.get('published')}\n{d['text'][:chars]}" for d in docs)


def extract(case, docs, run, tag="extract", sys_prompt=None, arm="B"):
    fmt = {"format": {"type": "json_schema", "name": "claims", "schema": CLAIMS_SCHEMA, "strict": True}}
    out = call_openai(arm, case["id"], run, sys_prompt or EXTRACT_SYS, f"Outlet: {case['media']['name']}\n\n{doc_block(docs)}",
                      fmt=fmt, tag=tag, reserve=0.6)
    claims = json.loads(out["text"])["claims"]
    url = {d["id"]: d["url"] for d in docs}
    for c in claims: c["url"] = url.get(c["doc"])
    return claims, out


def evidence_state(case, claims):
    """Trigger rules on the state of the evidence, never on model confidence."""
    m = norm(case["media"]["name"]); cs = norm(case["media"].get("callsign") or "~~")
    def about_outlet(x): x = norm(x); return bool(x) and (m in x or x in m or cs in x)
    owner_claims = [c for c in claims if (c["relation"] in ("licensee_of", "owns") and about_outlet(c["object"]))
                    or (c["relation"] == "subsidiary_of" and about_outlet(c["subject"]))]
    owners = {norm(c["subject"] if c["relation"] != "subsidiary_of" else c["object"]) for c in owner_claims}
    parents = {}
    for c in claims:
        if c["relation"] == "subsidiary_of": parents.setdefault(norm(c["subject"]), set()).add(norm(c["object"]))
        if c["relation"] == "owns" and not about_outlet(c["object"]): parents.setdefault(norm(c["object"]), set()).add(norm(c["subject"]))
    flags = []
    if not owner_claims: flags.append(("missing", "legal owner of the outlet"))
    if len(owners) > 1: flags.append(("conflict", f"several owners named: {sorted(owners)}"))
    for child, ps in parents.items():
        if len(ps) > 1 and not any(c["relation"] == "joint_venture_partner_of" for c in claims):
            flags.append(("conflict", f"several parents named for {child}: {sorted(ps)}"))
    tx = [c for c in claims if c["relation"].startswith("acquisition_") or c["relation"] == "sale_abandoned"]
    if any(c["relation"] == "acquisition_announced" for c in tx) and not any(c["relation"] in ("acquisition_closed", "sale_abandoned") for c in tx):
        flags.append(("conflict", "a transaction is announced but no closing or abandonment is documented"))
    # top of chain: an owner with no parent claim and no statement that it is the top
    tops = [o for o in owners if o not in parents]
    if owners and tops and not any(c["relation"] in ("controlled_by", "nonprofit_no_owner") for c in claims):
        flags.append(("missing", f"who owns {sorted(tops)[0]}"))
    if not any(c["relation"] == "chief_executive_of" for c in claims):
        flags.append(("missing", "the leader named in the leader definition"))
    return flags


def arm_b(case, run, max_extra_queries=2, sys_prompt=None, arm="B"):
    """B: explicit search and extraction, recorded dossier, conditional extra search, defined stop."""
    docs = collect(case)
    claims, ex = extract(case, docs, run, sys_prompt=sys_prompt, arm=arm)
    steps = [{"step": "extract", "usd": ex["usd"], "latency_s": ex["latency_s"], "model_returned": ex["model_returned"]}]
    flags = evidence_state(case, claims)
    extra = []
    missing = [f for f in flags if f[0] == "missing"][:max_extra_queries]
    if missing:  # one targeted round, template queries, cached per case
        p = PRIVATE / f"extra_{case['id']}.json"
        if p.exists():
            extra = json.loads(p.read_text())
        else:
            seen = {d["url"] for d in docs}
            for _, what in missing:
                q = f"{case['media']['name']} {what.replace('who owns ', '') + ' parent company owner' if what.startswith('who owns') else ('chief executive' if 'leader' in what else 'owner licensee')}"
                for d in exa_search(q, case["id"], n=4):
                    if d["url"] in seen: continue
                    seen.add(d["url"]); d["query"] = q; extra.append(d)
            for i, d in enumerate(extra):
                d["id"] = f"E{i+1}"; d["sha256"] = hashlib.sha256(d["text"].encode()).hexdigest()[:16]
            p.write_text(json.dumps(extra, indent=1))
        if extra:
            c2, ex2 = extract(case, extra, run, tag="extract_extra", sys_prompt=sys_prompt, arm=arm)
            claims += c2
            steps.append({"step": "extract_extra", "usd": ex2["usd"], "latency_s": ex2["latency_s"]})
        flags = evidence_state(case, claims)
    conflict = any(f[0] == "conflict" for f in flags)
    effort = "high" if conflict else EFFORT
    dossier = solver_input(case, claims, flags)
    out = call_openai(arm, case["id"], run, SYSTEM, dossier, effort=effort, reserve=0.6)
    steps.append({"step": "solve", "effort": effort, "usd": out["usd"], "latency_s": out["latency_s"]})
    out.update({"dossier": dossier, "claims": claims, "flags": flags, "steps": steps,
                "usd_total": sum(s["usd"] for s in steps), "latency_total_s": round(sum(s["latency_s"] for s in steps), 1),
                "escalated": conflict, "extra_docs": len(extra),
                "web_search": {"tool_enabled": False, "calls_executed": 0, "forced": False,
                               "exa_queries": len(queries_for(case)) + (len(missing) if missing else 0)}})
    return out


def solver_input(case, claims, flags):
    lines = [user_prompt(case), "", "Evidence dossier (claims extracted from retrieved documents; cite the URL and quote):"]
    for c in claims:
        lines.append(f"- {c['subject']} | {c['relation']} | {c['object']} | date: {c['date_in_doc']} | {c['url']} | \"{c['quote']}\"")
    lines.append("")
    lines.append("Evidence state checks: " + ("; ".join(f"{k}: {v}" for k, v in flags) if flags else "none"))
    lines.append("Answer only from this dossier. Where it does not establish a field, mark it not_established.")
    return "\n".join(lines)


def arm_c(case, run, dossier, arm="C"):
    """C: the exact dossier given to B's final solver, handed to another model without tools."""
    LEDGER.guard(0.2)
    t0 = time.time(); retries = 0
    while True:
        try:
            r = gem.models.generate_content(model=GEMINI_MODEL, contents=dossier, config=gt.GenerateContentConfig(
                system_instruction=SYSTEM, response_mime_type="application/json", response_json_schema=ANSWER_SCHEMA))
            break
        except Exception:
            retries += 1
            if retries > 1: raise
            time.sleep(5)
    usage = r.usage_metadata.model_dump() if r.usage_metadata else {}
    usd = gemini_cost(usage)
    LEDGER.add(provider="gemini", arm=arm, case=case["id"], run=run, step="solve", model_requested=GEMINI_MODEL,
               model_returned=r.model_version, usage={k: v for k, v in usage.items() if isinstance(v, int)}, usd=usd)
    return {"text": r.text, "usage": {k: v for k, v in usage.items() if isinstance(v, int)}, "usd": usd,
            "latency_s": round(time.time() - t0, 1), "model_returned": r.model_version, "retries": retries,
            "web_search": {"tool_enabled": False, "calls_executed": 0, "forced": False}}
