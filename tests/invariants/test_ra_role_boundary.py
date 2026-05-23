"""Section 16 wall: RAInput must not allow raw_narrative."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from fastapi_app.schemas.ai_io import RAInput, ProceduralHistory


def test_ra_input_has_no_raw_narrative_field():
    assert "raw_narrative" not in RAInput.model_fields
    assert "complaint_narrative_raw" not in RAInput.model_fields
    assert "narrative_verbatim" not in RAInput.model_fields


def test_ra_input_with_extra_raw_narrative_field_rejected_at_validation():
    """Pydantic forbids extras by default for our schema; verify."""
    with pytest.raises(ValidationError):
        RAInput.model_validate({
            "case_id": 1,
            "allegation_summary_authorised": "summary",
            "evidence_list_against_respondent": [],
            "procedural_history": {
                "filing_date": "2025-01-01",
                "conciliation_attempted": False,
                "hearings_held": 0,
                "notices_issued": 0,
            },
            "raw_narrative": "this should be rejected",  # extra
        })
