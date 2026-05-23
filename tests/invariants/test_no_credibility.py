"""§14: no AI component may produce credibility / guilt / outcome.

Enforced at the schema layer: the output models simply have no such fields,
so any LLM text containing them cannot be persisted in structured form.
"""
from __future__ import annotations

from fastapi_app.schemas.ai_io import (
    AROutput,
    CAOutput,
    CPAOutput,
    IAPhase1Output,
    IAPhase2Output,
    RAOutput,
    RPAOutput,
)

FORBIDDEN_FIELDS = {
    "credibility",
    "credibility_score",
    "confidence",
    "guilt",
    "guilty",
    "outcome_recommendation",
    "outcome_suggested",
}


def _all_field_names(model_cls) -> set[str]:
    return set(model_cls.model_fields.keys())


def test_no_credibility_or_outcome_fields_anywhere():
    for cls in [
        CAOutput, RAOutput,
        IAPhase1Output, IAPhase2Output,
        RPAOutput, CPAOutput, AROutput,
    ]:
        fields = _all_field_names(cls)
        leaked = fields & FORBIDDEN_FIELDS
        assert not leaked, f"{cls.__name__} leaks: {leaked}"


def test_ia_phase2_prompt_forbids_credibility():
    from fastapi_app.llm.client import load_prompt
    p = load_prompt("IA_PHASE2").lower()
    assert "do not assess credibility" in p
    assert "do not infer guilt" in p
    assert "do not produce a confidence score" in p
