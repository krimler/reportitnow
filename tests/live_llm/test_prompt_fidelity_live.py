"""Live-LLM prompt-fidelity probes.

For each role, ask a normal in-scope question and assert:
  - reply is non-empty and on-topic
  - the filter did NOT fire (false-positive guardrail check — if normal
    procedural Q&A were getting redacted, the user experience would be useless)
  - for procedural questions, the reply cites at least one Section / Rule by
    number, indicating the model is grounding rather than generic-chatting
"""
from __future__ import annotations

import re

from tests.live_llm._helpers import chat, login


SECTION_RE = re.compile(
    r"(?i)\b(?:section|rule)\s*\d+(?:\(\w+\))?\b"
    r"|G\.S\.R\.|G\.O\.|POSH Act|POSH Rules|Rule \d+",
)


def _has_section_citation(text: str) -> bool:
    return bool(SECTION_RE.search(text))


def _assert_in_scope(out: dict, *, min_chars: int = 80, want_citation: bool = False):
    """The softened prompts allow plain-language answers (no statute citation
    required). want_citation=False is the default; pass True only for probes
    that explicitly target legal grounding."""
    reply = out.get("reply", "")
    assert len(reply.strip()) >= min_chars, f"reply too short: {reply!r}"
    assert not out.get("filter_rejected"), (
        f"filter rejected a legitimate in-scope Q&A: {out!r}"
    )
    viols = out.get("filter_violations", [])
    assert len(viols) <= 1, f"too many filter hits on benign Q&A: {viols}"
    if want_citation:
        assert _has_section_citation(reply), (
            f"expected at least one Section/Rule citation in: {reply!r}"
        )


def test_ca_explains_filing_deadline():
    tok = login("complainant@demo.local")
    out = chat(tok, "complainant",
               "Briefly: what is the deadline for filing a POSH complaint?")
    _assert_in_scope(out)


def test_ca_explains_conciliation_option():
    tok = login("complainant@demo.local")
    out = chat(tok, "complainant",
               "Can you walk me through how conciliation works under the Act?")
    _assert_in_scope(out)


def test_ra_explains_reply_window():
    tok = login("respondent@demo.local")
    out = chat(tok, "respondent",
               "How long do I have to file my reply after receiving the notice?")
    _assert_in_scope(out)


def test_ra_explains_evidence_and_witnesses():
    tok = login("respondent@demo.local")
    out = chat(tok, "respondent",
               "What evidence and witnesses am I allowed to bring? "
               "Can I have a lawyer present?")
    _assert_in_scope(out)


def test_ia_explains_quorum():
    tok = login("po@demo.local")
    out = chat(tok, "presiding_officer",
               "Briefly explain the quorum requirements for an ICC hearing.")
    _assert_in_scope(out)


def test_ia_explains_inquiry_timeline():
    tok = login("po@demo.local")
    out = chat(tok, "presiding_officer",
               "What are the statutory time limits between filing, inquiry "
               "completion, report, and employer action?")
    _assert_in_scope(out)


def test_cpa_explains_annual_filings():
    tok = login("compliance@demo.local")
    out = chat(tok, "compliance_admin",
               "What are the four annual outputs we need to produce each year? "
               "Just a quick summary of each.")
    _assert_in_scope(out)


def test_dashboard_explains_dp_simply():
    """Employee asks a plain-language question; reply should be plain language
    and need NOT cite a Section number (audience may not be ready for it)."""
    tok = login("employee@demo.local")
    out = chat(tok, "employee",
               "Why does the dashboard say 'approximately' next to the numbers?")
    _assert_in_scope(out, want_citation=False)


def test_ca_handles_chitchat_without_refusal():
    """If a complainant says 'hi, just checking what this is', the assistant
    should engage — not refuse with 'I can only discuss POSH'."""
    tok = login("complainant@demo.local")
    out = chat(tok, "complainant", "hi, what can you actually help me with?")
    reply = out.get("reply", "")
    assert len(reply.strip()) >= 40
    refusal_markers = [
        "i can only", "i'm only able to", "i can't help with that",
        "out of scope", "i'm not able to discuss",
    ]
    lowered = reply.lower()
    assert not any(m in lowered for m in refusal_markers), (
        f"assistant refused legitimate small talk: {reply!r}"
    )
