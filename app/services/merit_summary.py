"""Merit summaries for the officer: run the extract-then-summarise pipeline for each merit criterion, store the result and
read it back with the applicant's own words restored.

- The AI sees redacted text only (the llm passed to the pipeline is a GuardedLLM built from the redaction outcome).
- Every AI call goes through the shared client: cache (llm_cache), retry with backoff, and the same trace as every other call.
- Each generation is a new version; earlier versions are kept. Nothing here marks, scores, ranks or compares anything.
- An AI failure for one criterion leaves that criterion with the passages code found; the others are unaffected, and the
  officer can regenerate one criterion.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.logging_utils import get_logger
from app.pipeline import merit_claims as mc
from app.services.access import Actor, application_for_staff, require_role
from app.services.audit import write_audit
from app.services.errors import Conflict, NotFound
from app.store.base import Store, one

log = get_logger(__name__)
APPLICATION_SOURCE = "application_text"


def source_label(source: str, rows: dict[str, dict[str, Any]]) -> str:
    if source == APPLICATION_SOURCE:
        return "Application form"
    row = rows.get(source.split(":", 1)[-1])
    if not row:
        return "Document"
    return f"{(row.get('declared_type') or 'document').replace('_', ' ').capitalize()} · {row.get('file_name')}"


def reading_order(documents: list[dict[str, Any]]) -> list[str]:
    """Document sources in the order the applicant gave them (upload time, then file name). Used everywhere a document is
    numbered, so "Referee letter 1" is the same letter in the prompt, in the summary and on the tabs."""
    live = [d for d in documents if not d.get("superseded")]
    return [f"document:{d['id']}" for d in sorted(live, key=lambda d: (str(d.get("uploaded_at") or ""), str(d.get("file_name") or ""), d["id"]))]


def kind_of(row: dict[str, Any]) -> str:
    return (row.get("declared_type") or "document").strip().lower().replace("_", " ")


def cfg_docs(rule_code: str) -> list[str] | None:
    return mc.criterion_config(rule_code).get("documents")


def _criterion_text(text: str) -> str:
    return text.split(" Weight on the form")[0].strip()


def latest_version(store: Store, application_id: str, rule_code: str) -> int:
    runs = store.select("merit_runs", eq={"application_id": application_id, "rule_code": rule_code})
    return max((r["version"] for r in runs), default=0)


def generate(store: Store, actor: Actor, app: dict[str, Any], redaction: Any, documents: list[dict[str, Any]], pack: Any,
             llm: Any, settings: Settings, codes: list[str] | None = None) -> dict[str, Any]:
    """Run the pipeline for each merit criterion (or `codes`). Never raises: one criterion failing leaves the others alone."""
    from redaction.pipeline import GuardedLLM

    raw = redaction.ai_texts() if redaction.llm_allowed else {}
    order = ([APPLICATION_SOURCE] if APPLICATION_SOURCE in raw else []) + [s for s in reading_order(documents) if s in raw]
    texts = {s: raw[s] for s in order + [s for s in raw if s not in order]}
    rows = {d["id"]: d for d in documents}
    kinds = {f"document:{d['id']}": kind_of(d) for d in documents}
    readable = {s.split(":", 1)[-1] for s in texts}
    unreadable = [f"document:{d['id']}" for d in documents if not d.get("superseded") and d["id"] not in readable]
    labels = mc.plain_names(list(texts) + unreadable, kinds)
    guard = GuardedLLM(llm, redaction) if (llm is not None and redaction.llm_allowed) else None
    out: dict[str, Any] = {}
    for rule in pack.rules:
        if (rule.params or {}).get("section") != "merit" or (codes and rule.rule_code not in codes):
            continue
        try:
            built = mc.build_criterion(guard, rule.rule_code, _criterion_text(rule.rule_text), texts, labels, pack.version,
                                       threshold=settings.quote_fuzzy_threshold, min_fuzzy_length=settings.quote_min_fuzzy_length,
                                       unreadable=[u for u in unreadable if not cfg_docs(rule.rule_code) or kinds.get(u) in cfg_docs(rule.rule_code)],
                                       kinds=kinds)
            out[rule.rule_code] = _persist(store, actor, app, rule, built, guard.model_name if guard else "none")
        except Exception as exc:   # storing the summary failed: say so in the audit log, leave the criterion without one
            log.warning("merit summary failed for %s: %s", rule.rule_code, type(exc).__name__)
            out[rule.rule_code] = {"status": "failed", "error": type(exc).__name__}
    return out


def _persist(store: Store, actor: Actor, app: dict[str, Any], rule: Any, built: dict[str, Any], model: str) -> dict[str, Any]:
    app_id, code = app["id"], rule.rule_code
    version = latest_version(store, app_id, code) + 1
    base = {"application_id": app_id, "rule_code": code, "version": version}
    store.insert("merit_runs", base | {
        "status": built["status"], "error": built["error"], "model": model, "overview": built.get("overview") or [],
        "claims_prompt_version": mc.CLAIMS_PROMPT_VERSION, "summary_prompt_version": mc.SUMMARY_PROMPT_VERSION,
        "coverage": built["coverage"],
        "dropped": [{"kind": "claim", **d} for d in built["dropped_claims"]] + [{"kind": "bullet", **d} for d in built["dropped_items"]],
    })
    if built["claims"]:
        store.insert("merit_claims", [base | {
            "claim_id": c["claim_id"], "source": c["source"], "document_id": c["document_id"], "passage_id": c["passage_id"],
            "quote": c["quote"], "restatement": c["restatement"], "theme": c["theme"], "verified": True,
            "subject": c.get("subject") or "", "attribute": c.get("attribute") or "",
        } for c in built["claims"]])
    if built["items"]:
        store.insert("merit_summary_items", [base | {
            "item_id": it["item_id"], "theme": it["theme"], "text": it["text"], "claim_ids": it["claim_ids"],
            "documents_also": it["documents_also"], "differs": it["differs"], "position": n,
        } for n, it in enumerate(built["items"])])
    if built["passages"]:
        store.insert("merit_coverage", [base | {
            "passage_id": p.passage_id, "source": p.source, "document_id": p.source.split(":", 1)[1] if p.source.startswith("document:") else None,
            "used": p.passage_id in built["used_passages"], "span_start": p.start, "span_end": p.end, "text": p.text, "page": p.page,
        } for p in built["passages"]])
    # Ids, counts and codes only: never the applicant's words.
    write_audit(store, actor, "merit.summary_generated", application_id=app_id, details={
        "rule_code": code, "version": version, "status": built["status"], "passages": len(built["passages"]),
        "claims": len(built["claims"]), "dropped_claims": len(built["dropped_claims"]), "bullets": len(built["items"]),
        "dropped_bullets": len(built["dropped_items"]), "prompt_versions": [mc.CLAIMS_PROMPT_VERSION, mc.SUMMARY_PROMPT_VERSION]})
    return {"status": built["status"], "version": version, "bullets": len(built["items"]), "claims": len(built["claims"])}


def regenerate(store: Store, actor: Actor, application_id: str, rule_code: str, llm: Any, settings: Settings) -> dict[str, Any]:
    """The officer asked for this criterion again (a new version; the old one is kept)."""
    from app.pipeline.rules_loader import load_rule_pack
    from app.services.redaction_service import run_and_store

    require_role(actor, "officer", "admin")
    app = application_for_staff(store, actor, application_id)
    pack = load_rule_pack(store, app["rule_pack_id"])
    if rule_code not in {r.rule_code for r in pack.rules if (r.params or {}).get("section") == "merit"}:
        raise NotFound("That is not a merit criterion")
    redaction = run_and_store(store, actor, application_id, settings)["outcome"]
    if not redaction.llm_allowed:
        raise Conflict("The redaction check has to be approved before the AI can read the documents.")
    documents = [d for d in store.select("documents", eq={"application_id": application_id}) if not d.get("superseded")]
    return generate(store, actor, app, redaction, documents, pack, llm, settings, [rule_code])[rule_code]


# ---------------------------------------------------------------- reading (officer view: the applicant's own words)

def _referee_names(store: Store, app_id: str, restorer: Any) -> dict[str, str]:
    """document source -> the referee's name as the letter gives it (restored for display; the AI only saw a token)."""
    from app.services.review import latest_run

    run = latest_run(store, app_id)
    if not run:
        return {}
    out = {}
    for f in store.select("fact_extractions", eq={"run_id": run["id"], "fact_key": "referee_name"}):
        if f.get("source") and f.get("fact_value") and f["fact_value"] != "not stated":
            out[f["source"]] = restorer.text(f["fact_value"])
    return out


def views(store: Store, app: dict[str, Any], restorer: Any, labels: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """rule code -> the latest summary, with the applicant's own words restored and located for the document viewer."""
    app_id = app["id"]
    runs = store.select("merit_runs", eq={"application_id": app_id})
    if not runs:
        return {}
    texts = labels if labels is not None else restorer.source_texts()
    rows = {d["id"]: d for d in store.select("documents", eq={"application_id": app_id})}
    kinds = {f"document:{i}": kind_of(d) for i, d in rows.items()}
    order = [APPLICATION_SOURCE] + reading_order(list(rows.values()))
    names = mc.plain_names(order, kinds)
    files = {k: rows[k.split(":", 1)[1]].get("file_name") for k in kinds}
    referees = _referee_names(store, app_id, restorer)
    pos = {s: i for i, s in enumerate(order)}
    out: dict[str, Any] = {}
    for code in sorted({r["rule_code"] for r in runs}, key=lambda c: (len(c), c)):
        run = max((r for r in runs if r["rule_code"] == code), key=lambda r: r["version"])
        v = run["version"]
        eq = {"application_id": app_id, "rule_code": code, "version": v}
        claims = {c["claim_id"]: c for c in store.select("merit_claims", eq=eq)}
        items = sorted(store.select("merit_summary_items", eq=eq), key=lambda i: i["position"])
        passages = sorted(store.select("merit_coverage", eq=eq), key=lambda p: (pos.get(p["source"], 99), p["span_start"]))
        cfg = mc.criterion_config(code)

        def doc(src: str) -> dict[str, Any]:
            return {"source": src, "name": names.get(src, "Document"), "file_name": files.get(src)}

        def quote(src: str, text: str) -> dict[str, Any]:
            return {"text": restorer.original(text, src), "span": restorer.locate(text, src)} | doc(src)

        def theme_label(tid: str) -> str:
            if tid.startswith("src:"):
                src = tid[4:]
                kind = kinds.get(src, "")
                if kind == "referee letter":
                    who = referees.get(src)
                    return f"{names.get(src, 'Referee letter')}: {who}" if who else names.get(src, "Referee letter")
                if kind == "resume":
                    return "Referees named in the resume"
                return names.get(src, "Document")
            return mc.theme_label(cfg, tid)

        theme_ids = list(dict.fromkeys(i["theme"] for i in items))
        if cfg.get("group_by") != "source":
            theme_ids = [t for t in mc.theme_order(cfg) if t in theme_ids]
        themes = []
        for tid in theme_ids:
            its = [i for i in items if i["theme"] == tid]
            srcs = sorted({claims[c]["source"] for i in its for c in i["claim_ids"] if c in claims}, key=lambda s: pos.get(s, 99))
            themes.append({"id": tid, "label": theme_label(tid), "from": [doc(s) for s in srcs], "items": [{
                "item_id": i["item_id"], "text": restorer.text(i["text"]), "differs": bool(i["differs"]),
                "quotes": [quote(claims[c]["source"], claims[c]["quote"]) | {"claim_id": c} for c in i["claim_ids"] if c in claims],
            } for i in its]})
        coverage = run.get("coverage") or []
        read = [c for c in coverage if c.get("readable")]
        out[code] = {
            "version": v, "status": run["status"], "error": run.get("error"), "generated_at": run.get("created_at"),
            "label": cfg.get("label"), "overview": [restorer.text(x) for x in run.get("overview") or []], "themes": themes,
            "other_passages": [quote(p["source"], p["text"]) | {"passage_id": p["passage_id"]} for p in passages if not p["used"]],
            "all_quotes": [quote(p["source"], p["text"]) | {"passage_id": p["passage_id"]} for p in passages],
            "could_not_verify": [{"kind": d["kind"], "reason": d["reason"]} for d in run.get("dropped") or []],
            "coverage": {"with_matches": sum(1 for c in read if c["passages"] > 0), "document_count": len(coverage),
                         "documents": [doc(c["source"]) | {"passages": c["passages"], "readable": c["readable"]} for c in coverage]},
            "versions": sorted({r["version"] for r in runs if r["rule_code"] == code}),
        }
    return out
