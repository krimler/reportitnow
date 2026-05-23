"""Typed Pydantic I/O for the six AI components.

The role boundaries live here, not in the prompts: RAInput has no
raw_narrative; IAPhase2Input has no record; CPAInput sees only aggregates;
ARInput sees only metadata. Output schemas have no credibility / guilt /
outcome fields. A prompt-level instruction can't cross any of this.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# --- Shared building blocks --------------------------------------------------

class EvidenceRef(BaseModel):
    document_id: int
    label: str | None = None


class EvidenceSubmission(BaseModel):
    description: str
    expected_form: Literal["document", "exhibit", "testimony"]


class WitnessRef(BaseModel):
    name: str
    relation: str | None = None


class Objection(BaseModel):
    ground: str
    detail: str


class TimelineEntry(BaseModel):
    date: str
    evidence_ids: list[int]


class Contradiction(BaseModel):
    point: str
    sides: list[dict]  # each {position, evidence_ids}


class EvidenceItem(BaseModel):
    id: int
    source: Literal[
        "complainant_statement", "respondent_statement", "witness_statement",
        "document", "exhibit", "digital_evidence",
    ]
    claim: str
    locator: str
    digital_evidence_flag: bool = False


class RecordPage(BaseModel):
    page_no: int
    text: str


class ProceduralHistory(BaseModel):
    filing_date: str
    conciliation_attempted: bool
    hearings_held: int
    notices_issued: int


class CommitteeComposition(BaseModel):
    presiding_officer: str
    members: list[str]
    external_member_present: bool


class AggregatedCaseData(BaseModel):
    complaints_received: int
    complaints_disposed: int
    complaints_pending_over_90d: int
    nature_of_action_taken: list[dict]


class ICCTenureState(BaseModel):
    constituted_on: str
    tenure_expires_on: str
    days_to_expiry: int


class TrainingCalendar(BaseModel):
    workshops_conducted: int
    next_scheduled: str | None = None


class TimeWindow(BaseModel):
    start: str
    end: str


class AuditMeta(BaseModel):
    seq: int
    timestamp: str
    component_id: str
    actor_hash: str  # hashed actor id, never plaintext


class ComplainantMetadata(BaseModel):
    age_band: Literal["under_18", "18_24", "25_34", "35_44", "45_plus"]
    employment_status: Literal[
        "employee", "contractor", "intern", "visitor", "client", "vendor"
    ]
    gender: str | None = None
    workplace_location_type: Literal[
        "office", "remote", "client_site", "transportation", "off_site_event", "virtual"
    ] = "office"


class EntityConfig(BaseModel):
    jurisdiction: str
    gender_scope: Literal["statutory", "inclusive"] = "inclusive"
    holiday_calendar_id: int | None = None


# --- Complainant Assistant ---------------------------------------------------

class CAInput(BaseModel):
    case_id: int
    raw_narrative: str
    complainant_metadata: ComplainantMetadata
    entity_config: EntityConfig


class CAOutput(BaseModel):
    structured_complaint_draft_id: int
    pocso_flag: bool = False
    employer_respondent_flag: bool = False
    cross_org_flag: bool = False
    stubbed: bool = False


# --- Respondent Assistant ----------------------------------------------------
# NOTE: RAInput intentionally has NO `raw_narrative` field. This enforces the
# Section 16 confidentiality wall at the type layer.

class RAInput(BaseModel):
    """Respondent Assistant input — extras forbidden so no smuggled
    `raw_narrative` field can ever traverse this schema."""
    model_config = {"extra": "forbid"}

    case_id: int
    allegation_summary_authorised: str = Field(
        ...,
        description=(
            "PO-authorised summary only. The raw complainant narrative is not "
            "passed to the RA — the respondent receives the full written "
            "complaint independently through the case file (Rule 7(2))."
        ),
    )
    evidence_list_against_respondent: list[EvidenceRef] = []
    procedural_history: ProceduralHistory


class RAOutput(BaseModel):
    response_draft_id: int
    evidence_to_submit: list[EvidenceSubmission] = []
    witnesses_proposed: list[WitnessRef] = []
    procedural_objections: list[Objection] = []
    stubbed: bool = False
    # Forbidden by schema: credibility scores, legal strategy, counter-allegations.


# --- Inquiry Assistant (Phase 1 + Phase 2) ----------------------------------

class IAPhase1Input(BaseModel):
    case_id: int
    record: list[RecordPage]
    continuation_from_id: int = 0


class IAPhase1Output(BaseModel):
    evidence_items: list[EvidenceItem]
    continued: bool = Field(default=False, alias="_continued")
    stubbed: bool = False

    model_config = {"populate_by_name": True}


class IAPhase2Input(BaseModel):
    """Phase 2 receives ONLY the evidence list from Phase 1.

    The original record is intentionally absent: position-bias mitigation is
    architectural, not advisory. Extras are forbidden so `record` cannot be
    smuggled in.
    """
    model_config = {"extra": "forbid"}

    case_id: int
    evidence_items: list[EvidenceItem]
    section_3_2_circumstances: list[str] = []


class IAPhase2Output(BaseModel):
    timeline: list[TimelineEntry]
    contradiction_table: list[Contradiction]
    neutral_factual_summary: str
    section_3_2_relevance_flags: list[str] = []
    bsa_section_63_review_required: list[int] = []
    stubbed: bool = False
    # Forbidden by schema: findings, credibility, outcome.


# --- Report Assistant --------------------------------------------------------

class ReportDocument(BaseModel):
    recipient: Literal["employer", "district_officer", "complainant", "respondent"]
    structured_sections: dict
    draft_pending_icc_review: bool = True


class RPAInput(BaseModel):
    case_id: int
    inquiry_record_summary: IAPhase2Output
    procedural_history: ProceduralHistory
    committee_composition: CommitteeComposition
    quorum_attestation: bool = Field(..., description="Must be True per Rule 7(7)")
    outcome: Literal["proved", "not_proved"]


class RPAOutput(BaseModel):
    report_employer_copy: ReportDocument
    report_do_copy: ReportDocument
    report_complainant_copy: ReportDocument
    report_respondent_copy: ReportDocument
    report_document_ids: list[int] = []
    stubbed: bool = False


# --- Compliance Assistant ----------------------------------------------------

class Section21Report(BaseModel):
    fy: str
    complaints_received: int
    complaints_disposed: int
    complaints_pending_over_90d: int
    workshops_conducted: int
    nature_of_action_taken: list[dict] = []
    nil_report: bool = False


class Section22Report(BaseModel):
    fy: str
    complaints_filed: int
    complaints_disposed: int


class BoardReportExtract(BaseModel):
    fy: str
    complaints_received: int
    complaints_disposed: int
    complaints_pending_over_90d: int


class CPAInput(BaseModel):
    entity_id: int
    fiscal_year: str
    aggregated_case_data: AggregatedCaseData
    icc_tenure_state: ICCTenureState
    training_calendar: TrainingCalendar


class CPAOutput(BaseModel):
    section_21_report: Section21Report
    section_22_report: Section22Report
    board_report_extract: BoardReportExtract
    state_portal_payload: dict
    stubbed: bool = False


# --- Audit Reviewer ----------------------------------------------------------

class ChainVerificationOut(BaseModel):
    is_valid: bool
    entries_checked: int
    first_divergence_at: int | None = None


class AnomalyFlag(BaseModel):
    kind: Literal[
        "high_frequency_access",
        "out_of_scope_case_access",
        "after_hours_non_po_access",
        "bulk_cross_case_access",
    ]
    detail: str
    actor_hash: str


class ARInput(BaseModel):
    entity_id: int
    query_window: TimeWindow


class AROutput(BaseModel):
    chain_verification_result: ChainVerificationOut
    access_anomaly_flags: list[AnomalyFlag] = []
    # Forbidden by schema: actor plaintext identities, case content.
