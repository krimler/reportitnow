"""Adversarial-test fixtures.

The shared trick: monkeypatch `fastapi_app.llm.client.call_llm` with a function
that returns whatever adversarial text the test wants. The output-filter layer
and schema layer then have to defend.

Tests for prompt-injection input meanwhile send the malicious payload via the
normal API path and verify the *input* the LLM would see (or that the output
filter still catches if the LLM played along).
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from fastapi_app.llm import client as llm_client


@dataclass
class AdversarialLLM:
    """Stand-in for `call_llm`. The test sets `next_text` to what an adversarial
    model would emit; the wrapper still runs the output filters through
    `_wrap_with_filters`, so we are testing our defences, not the LLM."""
    next_text: str = ""
    last_user_input: str | None = None
    last_component: str | None = None
    last_system_prompt: str | None = None

    def __call__(self, *, system_prompt, user_input, component_hint="generic",
                 temperature=0.2, max_tokens=2048, history=None):
        self.last_user_input = user_input
        self.last_component = component_hint
        self.last_system_prompt = system_prompt
        from fastapi_app.audit_chain import sha256_hex
        prompt_hash = sha256_hex(system_prompt + "␟" + user_input)
        return llm_client._wrap_with_filters(
            self.next_text or "[empty adversarial output]",
            model="adversarial-mock",
            prompt_hash=prompt_hash,
            stubbed=False,
            component_hint=component_hint,
        )


@pytest.fixture
def adv_llm(monkeypatch):
    mock = AdversarialLLM()
    monkeypatch.setattr(llm_client, "call_llm", mock)

    # The AI component runners and chat endpoint import `call_llm` from
    # `fastapi_app.llm` (the package __init__ re-exports it). Patch there too.
    import fastapi_app.llm as llm_pkg
    monkeypatch.setattr(llm_pkg, "call_llm", mock)

    # And in every module that has already done `from fastapi_app.llm import call_llm`.
    import fastapi_app.ai_components.ca.runner as ca_runner
    import fastapi_app.ai_components.ra.runner as ra_runner
    import fastapi_app.ai_components.ia.runner as ia_runner
    import fastapi_app.ai_components.rpa.runner as rpa_runner
    import fastapi_app.ai_components.cpa.runner as cpa_runner
    import fastapi_app.routes.chat as chat_route
    for mod in (ca_runner, ra_runner, ia_runner, rpa_runner, cpa_runner, chat_route):
        monkeypatch.setattr(mod, "call_llm", mock)

    return mock
