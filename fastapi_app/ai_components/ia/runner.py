"""Inquiry Assistant.

Phase 1 enumerates every distinct evidence item in the record.
Phase 2 synthesises (timeline, contradiction table, neutral summary) from
the structured list only — the raw record is not passed in, which keeps
middle-of-document evidence from getting under-weighted.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from fastapi_app.ai_components.common import audit_ai_call, persist_draft_document
from fastapi_app.db import models as m
from fastapi_app.llm import call_llm, load_prompt
from fastapi_app.schemas.ai_io import (
    IAPhase1Input,
    IAPhase1Output,
    IAPhase2Input,
    IAPhase2Output,
    EvidenceItem,
)


def run_ia_phase1(
    db: Session,
    *,
    payload: IAPhase1Input,
    actor: m.User,
    entity_id: int,
) -> IAPhase1Output:
    system_prompt = load_prompt("IA_PHASE1")
    record_blob = "\n\n".join(
        f"[page {p.page_no}] {p.text}" for p in payload.record
    )
    user_input = (
        f"continuation_from_id: {payload.continuation_from_id}\n\n"
        f"RECORD:\n{record_blob}"
    )
    resp = call_llm(
        system_prompt=system_prompt,
        user_input=user_input,
        component_hint="IA_PHASE1",
        max_tokens=4000,
    )
    try:
        parsed = json.loads(resp.text)
    except json.JSONDecodeError:
        parsed = {"evidence_items": [], "_continued": False}
    items = [EvidenceItem(**ei) for ei in parsed.get("evidence_items", [])]

    draft = persist_draft_document(
        db,
        case_id=payload.case_id,
        doc_type="ia_phase1_evidence_list",
        content={"evidence_items": [ei.model_dump() for ei in items]},
        ai_component="IA",
    )

    audit_ai_call(
        db,
        entity_id=entity_id,
        component_id="IA",
        actor_user_id=actor.id,
        case_id=payload.case_id,
        input_payload={
            "case_id": payload.case_id,
            "pages": len(payload.record),
            "continuation_from_id": payload.continuation_from_id,
        },
        output_payload={"draft_doc_id": draft.id, "n_items": len(items)},
        model_id=resp.model,
        prompt_hash=resp.prompt_hash,
    )
    db.commit()

    return IAPhase1Output(
        evidence_items=items,
        _continued=bool(parsed.get("_continued", False)),
        stubbed=resp.stubbed,
    )


def run_ia_phase2(
    db: Session,
    *,
    payload: IAPhase2Input,
    actor: m.User,
    entity_id: int,
) -> IAPhase2Output:
    system_prompt = load_prompt("IA_PHASE2")
    user_input = (
        f"EVIDENCE LIST (refer to items by id):\n"
        f"{json.dumps([e.model_dump() for e in payload.evidence_items], indent=2)}\n\n"
        f"SECTION 3(2) CIRCUMSTANCES REFERENCE LIST:\n"
        f"{payload.section_3_2_circumstances}\n"
    )
    resp = call_llm(
        system_prompt=system_prompt,
        user_input=user_input,
        component_hint="IA_PHASE2",
        max_tokens=4000,
    )
    try:
        parsed = json.loads(resp.text)
    except json.JSONDecodeError:
        parsed = {}

    out = IAPhase2Output(
        timeline=_sanitise_timeline(parsed.get("timeline", []) or []),
        contradiction_table=_sanitise_contradictions(
            parsed.get("contradiction_table", []) or []
        ),
        neutral_factual_summary=str(parsed.get(
            "neutral_factual_summary",
            "[Phase 2 synthesis pending review]",
        )),
        section_3_2_relevance_flags=_sanitise_flags(
            parsed.get("section_3_2_relevance_flags", []) or []
        ),
        bsa_section_63_review_required=_sanitise_bsa_list(
            parsed.get("bsa_section_63_review_required"),
            evidence_items=payload.evidence_items,
        ),
        stubbed=resp.stubbed,
    )

    draft = persist_draft_document(
        db,
        case_id=payload.case_id,
        doc_type="ia_phase2_synthesis",
        content=out.model_dump(),
        ai_component="IA",
    )

    audit_ai_call(
        db,
        entity_id=entity_id,
        component_id="IA",
        actor_user_id=actor.id,
        case_id=payload.case_id,
        input_payload={
            "case_id": payload.case_id,
            "n_evidence_items": len(payload.evidence_items),
        },
        output_payload={"draft_doc_id": draft.id},
        model_id=resp.model,
        prompt_hash=resp.prompt_hash,
    )
    db.commit()
    return out


# --- LLM-drift sanitisers ----------------------------------------------------
#
# Live LLMs sometimes drift from the prompted JSON shape: renaming `point` to
# `factual_point`, returning a bool where a list was asked for, putting a
# nested dict where a flat list was expected. These helpers normalise common
# drift patterns into the schema-required form so a single misshapen field
# doesn't crash the whole Phase 2 pipeline. The audit log captures the raw
# output (via output_hash) for forensic review when drift is suspected.

def _sanitise_timeline(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        date = entry.get("date") or entry.get("when") or entry.get("timestamp") or ""
        ev = entry.get("evidence_ids") or entry.get("ids") or entry.get("evidence") or []
        if isinstance(ev, int):
            ev = [ev]
        if not isinstance(ev, list):
            ev = []
        out.append({"date": str(date), "evidence_ids": [int(x) for x in ev if str(x).isdigit() or isinstance(x, int)]})
    return out


def _sanitise_contradictions(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        point = (
            entry.get("point") or entry.get("factual_point")
            or entry.get("issue") or entry.get("topic") or ""
        )
        sides = entry.get("sides") or entry.get("positions") or []
        if not isinstance(sides, list):
            # Some LLMs return a/b split: {"side_a": [1], "side_b": [2]}.
            a = entry.get("side_a") or entry.get("evidence_ids_a")
            b = entry.get("side_b") or entry.get("evidence_ids_b")
            sides = []
            if a:
                sides.append({"position": "side_a", "evidence_ids": a if isinstance(a, list) else [a]})
            if b:
                sides.append({"position": "side_b", "evidence_ids": b if isinstance(b, list) else [b]})
        out.append({"point": str(point), "sides": sides})
    return out


def _sanitise_flags(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    if isinstance(raw, str):
        return [raw] if raw else []
    return []


def _sanitise_bsa_list(raw, *, evidence_items) -> list[int]:
    """The schema requires a list[int] of evidence IDs flagged for Section 63.

    Common LLM drift:
      - returns True/False (interpreted as "do/don't review")
      - returns a string of comma-separated ids
      - returns the full evidence-item dicts instead of ids
    Fallback for True/non-list: re-derive from the input's `digital_evidence_flag`.
    """
    if isinstance(raw, list):
        out = []
        for x in raw:
            if isinstance(x, int):
                out.append(x)
            elif isinstance(x, dict) and "id" in x:
                try:
                    out.append(int(x["id"]))
                except (TypeError, ValueError):
                    pass
            elif isinstance(x, str) and x.isdigit():
                out.append(int(x))
        return out
    if isinstance(raw, str):
        return [int(p) for p in raw.replace(",", " ").split() if p.isdigit()]
    if isinstance(raw, bool):
        if not raw:
            return []
        return [ei.id for ei in evidence_items if getattr(ei, "digital_evidence_flag", False)]
    # None / other → derive from input flags
    return [ei.id for ei in evidence_items if getattr(ei, "digital_evidence_flag", False)]
