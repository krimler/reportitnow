"""MCP front for the ReportItNow FastAPI surface.

Configuration via env:
    REPORTITNOW_API     base URL of the FastAPI backend (default http://127.0.0.1:8000)
    REPORTITNOW_TOKEN   bearer token; mint one with `python -m tools.mint_mcp_token`

The MCP server is a thin proxy: every tool call goes back to FastAPI, so the
role checks, output filters, audit chain, and Pydantic schemas behave the same
whether the caller is Chainlit, curl, or an MCP client.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP


API = os.environ.get("REPORTITNOW_API", "http://127.0.0.1:8000")
TOKEN = os.environ.get("REPORTITNOW_TOKEN", "")

mcp = FastMCP("reportitnow")


def _headers() -> dict[str, str]:
    if not TOKEN:
        raise RuntimeError(
            "REPORTITNOW_TOKEN is not set. Mint one with "
            "`python -m tools.mint_mcp_token <email>`."
        )
    return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def _get(path: str, **params) -> Any:
    r = httpx.get(f"{API}{path}", headers=_headers(), params=params or None, timeout=120)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict | None = None, **params) -> Any:
    r = httpx.post(f"{API}{path}", headers=_headers(), json=body or {},
                   params=params or None, timeout=180)
    r.raise_for_status()
    return r.json()


def _delete(path: str, **params) -> Any:
    r = httpx.delete(f"{API}{path}", headers=_headers(), params=params or None, timeout=60)
    r.raise_for_status()
    return r.json()


# --- Identity ---------------------------------------------------------------

@mcp.tool()
def whoami() -> dict:
    """Return the authenticated user's id, email, display name, and held roles.

    Useful at the start of a session for the model to understand which tools
    it can legitimately call (each tool's role check is enforced by FastAPI).
    """
    return _get("/auth/whoami")


# --- Complainant flow -------------------------------------------------------

@mcp.tool()
def file_complaint(
    narrative: str,
    respondent_email: str,
    incident_date: str | None = None,
    age_band: str = "25_34",
    employment_status: str = "employee",
    workplace_location_type: str = "office",
) -> dict:
    """File a new POSH complaint.

    Creates a case, runs the Complainant Assistant to structure the narrative,
    and returns the case id, intake-gate routing (icc/lcc/pocso_police), and
    the draft document id awaiting Presiding Officer authorisation.

    `narrative` must be the complainant's own words — they are preserved
    verbatim alongside the structured version (no-narrative-loss invariant).
    """
    case = _post("/cases", {
        "incident_date": incident_date or date.today().isoformat(),
        "incident_continuing": False,
        "respondent_email": respondent_email,
    })
    if case.get("routed_to") != "icc":
        return {
            "case": case,
            "note": f"Case routed to {case['routed_to']}; CA not run on this branch.",
        }
    ca_out = _post("/ai/CA/structure_narrative", {
        "case_id": case["id"],
        "raw_narrative": narrative,
        "complainant_metadata": {
            "age_band": age_band,
            "employment_status": employment_status,
            "workplace_location_type": workplace_location_type,
        },
        "entity_config": {"jurisdiction": "IN-TN", "gender_scope": "inclusive"},
    })
    return {"case": case, "ca": ca_out}


# --- Respondent flow --------------------------------------------------------

@mcp.tool()
def submit_response(
    case_id: int,
    allegation_summary_authorised: str,
    procedural_history_filing_date: str,
    conciliation_attempted: bool = False,
    hearings_held: int = 0,
    notices_issued: int = 0,
    evidence_to_submit: list[dict] | None = None,
    witnesses_proposed: list[dict] | None = None,
) -> dict:
    """Run the Respondent Assistant for the named case.

    The RA never receives the complainant's raw narrative — the
    `allegation_summary_authorised` field must be the PO-cleared summary, not
    a paraphrase of the complaint document. The full complaint reaches the
    respondent separately through the case file (Rule 7(2)), not via the RA.
    """
    return _post("/ai/RA/draft_response", {
        "case_id": case_id,
        "allegation_summary_authorised": allegation_summary_authorised,
        "evidence_list_against_respondent": [],
        "procedural_history": {
            "filing_date": procedural_history_filing_date,
            "conciliation_attempted": conciliation_attempted,
            "hearings_held": hearings_held,
            "notices_issued": notices_issued,
        },
    })


# --- ICC / Presiding Officer ------------------------------------------------

@mcp.tool()
def authorise_document(doc_id: int) -> dict:
    """Presiding Officer authorisation of a draft document. Required before
    a draft can be referenced in a state transition (e.g. moving to Filed)."""
    return _post(f"/documents/{doc_id}/authorise")


@mcp.tool()
def reject_document(doc_id: int) -> dict:
    """Presiding Officer rejects a draft. Keeps draft status, appends an audit
    entry for visibility."""
    return _post(f"/documents/{doc_id}/reject")


@mcp.tool()
def transition_case(case_id: int, target: str, reason: str,
                    payload: dict | None = None) -> dict:
    """Move a case to a new state. Allowed targets follow the design's state
    graph (filed, conciliation, conciliation_breached, inquiry, report,
    employer_action, appeal, closed, terminated). FastAPI enforces role +
    invariants + audit log inside one transaction."""
    return _post(f"/cases/{case_id}/transition", {
        "target": target, "reason": reason, "payload": payload or {},
    })


@mcp.tool()
def schedule_hearing(
    case_id: int,
    scheduled_for: str,
    members: list[dict],
    notice_issued_on: str | None = None,
    complainant_present: bool | None = None,
    respondent_present: bool | None = None,
    notice_period_days: int = 15,
) -> dict:
    """Schedule and record a hearing. `members` is a list of
    {user_id, role_at_hearing, present}. Rule 7(7) quorum is checked
    server-side: refused if fewer than three ICC members including the
    Presiding Officer are marked present."""
    return _post("/hearings", {
        "case_id": case_id,
        "scheduled_for": scheduled_for,
        "notice_issued_on": notice_issued_on,
        "members": members,
        "complainant_present": complainant_present,
        "respondent_present": respondent_present,
        "notice_period_days": notice_period_days,
    })


@mcp.tool()
def extract_evidence(case_id: int, record_pages: list[dict],
                     continuation_from_id: int = 0) -> dict:
    """IA Phase 1: enumerate every distinct evidence item in the record.
    `record_pages` is a list of {page_no, text}. Returns a structured
    evidence list with id, source, claim, locator, and a digital-evidence
    flag for Section 63 BSA review."""
    return _post("/ai/IA/phase1_extract", {
        "case_id": case_id,
        "record": record_pages,
        "continuation_from_id": continuation_from_id,
    })


@mcp.tool()
def synthesise_inquiry(case_id: int, evidence_items: list[dict],
                       section_3_2_circumstances: list[str] | None = None) -> dict:
    """IA Phase 2: build a timeline + contradiction table + neutral summary
    from the Phase 1 evidence list. The original record is NOT passed in —
    this is the position-bias mitigation. No credibility, no findings."""
    return _post("/ai/IA/phase2_synthesise", {
        "case_id": case_id,
        "evidence_items": evidence_items,
        "section_3_2_circumstances": section_3_2_circumstances or [],
    })


@mcp.tool()
def draft_section_13_report(
    case_id: int,
    inquiry_record_summary: dict,
    procedural_history: dict,
    committee_composition: dict,
    outcome: str,
    quorum_attestation: bool = True,
) -> dict:
    """RPA: format the inquiry record into the Section 13 report (four
    distribution copies). `outcome` is set by the ICC ('proved' or
    'not_proved'); findings + conclusions are left as placeholders for
    committee authorship."""
    return _post("/ai/RPA/draft_report", {
        "case_id": case_id,
        "inquiry_record_summary": inquiry_record_summary,
        "procedural_history": procedural_history,
        "committee_composition": committee_composition,
        "quorum_attestation": quorum_attestation,
        "outcome": outcome,
    })


@mcp.tool()
def serve_complaint_to_respondent(case_id: int) -> dict:
    """Rule 7(2) service: PO authorises delivery of the authorised written
    complaint to the respondent through the case file (separate from the RA
    channel, which never sees the full narrative)."""
    return _post(f"/cases/{case_id}/serve_complaint_to_respondent")


# --- Compliance -------------------------------------------------------------

@mcp.tool()
def generate_annual_reports(fiscal_year: str, training_sessions: int = 0) -> dict:
    """Build the four annual outputs: Section 21 / Rule 14 ICC report,
    Section 22 employer report, Board's Report extract under Rule 8(5)(x),
    and the state portal payload. Aggregates only — no case content."""
    return _post("/ai/CPA/build_annual_reports", body={},
                 fiscal_year=fiscal_year, training_sessions=training_sessions)


@mcp.tool()
def compliance_dashboard(fiscal_year: str, training_sessions: int = 0) -> dict:
    """Exact statutory figures for the named fiscal year. Restricted to
    employer / ICC / PO / Compliance Admin / DO / auditor."""
    return _get("/dashboard/compliance",
                fiscal_year=fiscal_year, training_sessions=training_sessions)


@mcp.tool()
def transparency_dashboard(fiscal_year: str, training_sessions: int = 0) -> dict:
    """Differentially-private aggregates for the named fiscal year. Available
    to all employees subject to the workforce-size floor."""
    return _get("/dashboard/transparency",
                fiscal_year=fiscal_year, training_sessions=training_sessions)


# --- Audit ------------------------------------------------------------------

@mcp.tool()
def verify_audit_chain() -> dict:
    """Walk the HMAC-chained audit log for this entity and report the first
    divergence (if any) by seq, timestamp, and component_id."""
    return _get("/audit/verify")


@mcp.tool()
def export_audit_metadata() -> list[dict]:
    """Metadata-only export of the audit chain for the authenticated entity.
    Includes seq, timestamps, component ids, and hash chain — never case
    content. Suitable for a Section 25 inspection deliverable."""
    return _get("/audit/export")


# --- Chat -------------------------------------------------------------------

@mcp.tool()
def chat(role: str, message: str, case_id: int | None = None) -> dict:
    """Send one freeform turn to the role-bounded chat.

    `role` is the assistant persona to invoke: complainant, respondent,
    icc_member, presiding_officer, compliance_admin, employer, auditor,
    employee, or district_officer. The server checks the authenticated user
    holds that role. Persistent history is loaded and updated automatically;
    pass `/clear` as the message to wipe history for that role.
    """
    body = {"role": role, "message": message, "history": []}
    if case_id is not None:
        body["case_id"] = case_id
    return _post("/ai/chat", body)


@mcp.tool()
def get_chat_history(role: str, limit: int = 50) -> dict:
    """Fetch persisted chat turns for (current user, role)."""
    return _get("/ai/chat/history", role=role, limit=limit)


@mcp.tool()
def clear_chat_history(role: str) -> dict:
    """Wipe persisted chat turns for (current user, role)."""
    return _delete("/ai/chat/history", role=role)


# --- Resources --------------------------------------------------------------
#
# A small set of read-only resources. Clients can pull these as context
# without invoking a tool, which is useful for chat-style integrations.

@mcp.resource("reportitnow://policy/section-9")
def policy_section_9() -> str:
    """Section 9 (POSH Act) — filing rights, plain-language summary."""
    return (
        "Section 9 of the POSH Act gives any aggrieved person three months "
        "from the date of the incident to make a written complaint to the "
        "Internal Committee, extendable by three further months for "
        "sufficient cause. The 'aggrieved' definition (Section 2(a)) reaches "
        "anyone in the workplace — employees, contractors, interns, "
        "visitors, and clients — regardless of direct employment. Where the "
        "complainant cannot write the complaint themselves, the Section 9 "
        "proviso permits an oral-to-written pipeline; the Presiding Officer "
        "authorises the converted document before it is treated as the "
        "written complaint."
    )


@mcp.resource("reportitnow://policy/section-16")
def policy_section_16() -> str:
    """Section 16 — confidentiality and its single proviso."""
    return (
        "Section 16 prohibits public disclosure of complaint contents, "
        "party identities, proceedings, and recommendations, notwithstanding "
        "the RTI Act. The proviso permits sharing information about justice "
        "secured to the victim without disclosing identifying particulars. "
        "The Transparency Dashboard is built inside this proviso: it "
        "releases noised aggregate counts so employees can see the system "
        "is being used, without revealing anything about individual cases."
    )


@mcp.resource("reportitnow://policy/deadlines")
def policy_deadlines() -> str:
    """Statutory deadlines enforced by the state machine."""
    return (
        "Complaint filing: 3 months from the incident (Section 9), "
        "extendable by 3 more.\n"
        "Respondent notice: 7 business days from filing (Rule 7(2)).\n"
        "Respondent reply: 10 business days from notice (Rule 7(4)).\n"
        "Inquiry completion: 90 calendar days from filing (Section 11(4)).\n"
        "Report: 10 calendar days after inquiry (Section 13(1)).\n"
        "Employer action: 60 calendar days after report (Section 13(4)).\n"
        "Appeal: 90 calendar days after report (Section 18(2)).\n"
        "Annual report: 31 January for the prior calendar year "
        "(Section 21 / Rule 14)."
    )
