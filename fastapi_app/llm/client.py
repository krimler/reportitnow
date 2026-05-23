"""OpenAI-compatible LLM client (default: local Qwen on Ollama).

Endpoint contract (Ollama / LM Studio / vLLM / etc. all expose this):

    POST {LLM_ENDPOINT}/chat/completions
    {
      "model": "...",
      "messages": [{"role": "system", "content": "..."},
                   {"role": "user",   "content": "..."}],
      "temperature": ...,
      "max_tokens":  ...
    }

If `LLM_STUB_MODE=1` (or the endpoint is unreachable) we return a deterministic
stub keyed to the prompt hash, so the rest of the stack stays exercisable
offline.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from fastapi_app.audit_chain import sha256_hex
from fastapi_app.config import get_settings
from fastapi_app.llm.filters import is_rejected, redact_output, scan_output

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_hash: str
    stubbed: bool
    filter_violations: list = field(default_factory=list)
    filter_rejected: bool = False


def load_prompt(component: str, version: str = "v1") -> str:
    """Load a versioned system prompt from fastapi_app/prompts/<version>/<component>.txt."""
    path = PROMPTS_DIR / version / f"{component}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def _stub_response(system_prompt: str, user_input: str, component_hint: str) -> str:
    """Deterministic, schema-shaped stub based on component_hint.

    Returns plausibly-shaped JSON for components that expect JSON, and a short
    factual stub for free-text components. The exact text is keyed off a hash
    of the inputs so reruns are stable for tests.
    """
    digest = hashlib.sha256((system_prompt + user_input).encode()).hexdigest()[:8]
    if component_hint == "CA":
        return json.dumps({
            "structured_complaint": {
                "narrative_verbatim_preserved": True,
                "incidents": [{
                    "when": "2025-12-15 ~14:00",
                    "where": "Office, 4th floor meeting room",
                    "who": ["respondent (named)"],
                    "what_in_users_words": user_input[:280] or "[stub: complainant narrative]",
                    "pattern": "single incident",
                    "witnesses": [],
                }],
                "relationship_to_workplace": "employee",
            },
            "_stub_digest": digest,
        })
    if component_hint == "RA":
        return json.dumps({
            "response": {
                "account_of_events": "[respondent's account placeholder]",
                "evidence_to_submit": [],
                "witnesses_proposed": [],
                "procedural_objections": [],
            },
            "_stub_digest": digest,
        })
    if component_hint == "IA_PHASE1":
        return json.dumps({
            "evidence_items": [
                {"id": 1, "source": "complainant_statement",
                 "claim": "Incident occurred on the stated date",
                 "locator": "p.1 para.1", "digital_evidence_flag": False},
                {"id": 2, "source": "respondent_statement",
                 "claim": "Disputes the alleged conduct",
                 "locator": "p.3 para.2", "digital_evidence_flag": False},
            ],
            "_continued": False,
            "_stub_digest": digest,
        })
    if component_hint == "IA_PHASE2":
        return json.dumps({
            "timeline": [{"date": "2025-12-15", "evidence_ids": [1, 2]}],
            "contradiction_table": [{
                "point": "Whether the conduct occurred",
                "sides": [{"position": "alleged",   "evidence_ids": [1]},
                          {"position": "disputed", "evidence_ids": [2]}],
            }],
            "neutral_factual_summary": (
                "The record contains a complainant statement alleging the conduct "
                "described, and a respondent statement disputing it. No credibility "
                "assessment is made."
            ),
            "section_3_2_relevance_flags": ["3(2)(iv)_hostile_environment"],
            "bsa_section_63_review_required": [],
            "_stub_digest": digest,
        })
    if component_hint == "RPA":
        return json.dumps({
            "report": {
                "parties_and_committee": "[populated from case record]",
                "procedural_history":    "[populated from state machine ledger]",
                "allegations":           "[from written complaint]",
                "respondent_reply":      "[from respondent's filed reply]",
                "evidence_considered":   "[from inquiry record]",
                "standard_of_proof":     "preponderance of probability through the reasonable-person lens (gender-inclusive deployment)",
                "findings_of_fact":      "[ICC AUTHORSHIP — do not generate]",
                "conclusions":           "[ICC AUTHORSHIP — do not generate]",
                "section_15_compensation_template": {
                    "mental_trauma": None, "loss_of_career_opportunity": None,
                    "medical_expenses": None, "respondent_income_status": None,
                    "lump_sum_or_instalments": None,
                },
                "recommendations": "[for ICC completion]",
            },
            "distribution_copies": ["employer", "district_officer", "complainant", "respondent"],
            "_stub_digest": digest,
        })
    if component_hint == "CPA":
        return json.dumps({"_stub_digest": digest, "note": "CPA output assembled from structured DB queries; see /reports endpoints."})
    if component_hint == "AR":
        return json.dumps({"_stub_digest": digest, "note": "AR output assembled from chain verification; see /audit endpoints."})
    if component_hint.endswith("_CHAT") or component_hint == "CHAT":
        # Plain-text chat stub. Keep it short and role-aware via system prompt
        # hints, so the stubbed conversation still demonstrates the boundaries.
        role_hint = component_hint.replace("_CHAT", "").lower() or "general"
        return (
            f"[stub reply • role={role_hint} • digest={digest}]\n\n"
            f"I'm the {role_hint.upper()} chat assistant running in stub mode "
            f"(no local LLM reachable). I see your message: "
            f"\"{user_input[:160].strip()}\". In production this reply would "
            f"come from the local Qwen model with the role-bounded system "
            f"prompt at fastapi_app/prompts/v1/{role_hint.upper()}_CHAT.txt."
        )
    return f"[stub:{component_hint}:{digest}] {user_input[:120]}"


def call_llm(
    *,
    system_prompt: str,
    user_input: str,
    component_hint: str = "generic",
    temperature: float = 0.2,
    max_tokens: int = 2048,
    history: list[dict[str, str]] | None = None,
) -> LLMResponse:
    """Synchronous LLM call. Used inside FastAPI handlers (which are sync here).

    `history`, if given, is a list of prior turns as
    ``{"role": "user"|"assistant", "content": "..."}`` that is interleaved
    between the system prompt and the current user_input. This lets the chat
    endpoint hand multi-turn context to the model.
    """
    settings = get_settings()
    history = history or []
    history_blob = "".join(f"\n[{m['role']}] {m['content']}" for m in history)
    prompt_hash = sha256_hex(system_prompt + "␟" + history_blob + "␟" + user_input)

    if settings.llm_stub_mode == 1:
        text = _stub_response(system_prompt, user_input, component_hint)
        return _wrap_with_filters(
            text, model="stub", prompt_hash=prompt_hash,
            stubbed=True, component_hint=component_hint,
        )

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for turn in history:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": user_input})

    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    url = settings.llm_endpoint.rstrip("/") + "/chat/completions"
    try:
        resp = httpx.post(
            url, headers=headers, json=payload,
            timeout=settings.llm_request_timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        return _wrap_with_filters(
            text,
            model=data.get("model", settings.llm_model),
            prompt_hash=prompt_hash,
            stubbed=False,
            component_hint=component_hint,
        )
    except (httpx.HTTPError, httpx.ConnectError, KeyError, json.JSONDecodeError):
        # Endpoint unreachable or shape unexpected — fall back to stub so the
        # state machine and tests still progress.
        text = _stub_response(system_prompt, user_input, component_hint)
        return _wrap_with_filters(
            text, model="stub-fallback", prompt_hash=prompt_hash,
            stubbed=True, component_hint=component_hint,
        )


def _wrap_with_filters(
    text: str, *,
    model: str,
    prompt_hash: str,
    stubbed: bool,
    component_hint: str,
) -> LLMResponse:
    """Apply role-bounded output filters before returning to callers.

    `redact_output` either redacts in-place or — for reject-severity hits —
    replaces the entire text with a refusal stub. Either way the violations
    list is attached to the LLMResponse so callers can audit-log them.
    """
    sanitised, violations = redact_output(text, component_hint)
    return LLMResponse(
        text=sanitised,
        model=model,
        prompt_hash=prompt_hash,
        stubbed=stubbed,
        filter_violations=[str(v) for v in violations],
        filter_rejected=is_rejected(violations),
    )
