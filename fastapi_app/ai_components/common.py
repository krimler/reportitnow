"""Shared helpers for AI components: draft-document persistence + audit log."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from fastapi_app.audit_chain import append_entry, sha256_hex
from fastapi_app.db import models as m


def persist_draft_document(
    db: Session,
    *,
    case_id: int,
    doc_type: str,
    content: str | dict[str, Any],
    ai_component: str,
) -> m.CaseDocument:
    """Create a draft document (is_draft=True) and return it. Does not commit."""
    if isinstance(content, str):
        blob = content.encode("utf-8")
    else:
        blob = json.dumps(content, sort_keys=True, default=str).encode("utf-8")
    doc = m.CaseDocument(
        case_id=case_id,
        doc_type=doc_type,
        content_blob=blob,
        content_hash=sha256_hex(blob),
        ai_component=ai_component,
        is_draft=True,
        created_at=datetime.utcnow(),
    )
    db.add(doc)
    db.flush()
    return doc


def audit_ai_call(
    db: Session,
    *,
    entity_id: int,
    component_id: str,
    actor_user_id: int,
    case_id: int | None,
    input_payload: Any,
    output_payload: Any,
    model_id: str,
    prompt_hash: str,
) -> None:
    """Log an AI invocation in the audit chain, binding model + prompt hashes."""
    enriched = {
        "output": output_payload,
        "model_id": model_id,
        "prompt_hash": prompt_hash,
    }
    append_entry(
        db,
        entity_id=entity_id,
        component_id=component_id,
        actor_user_id=actor_user_id,
        case_id=case_id,
        input_payload=input_payload,
        output_payload=enriched,
    )
