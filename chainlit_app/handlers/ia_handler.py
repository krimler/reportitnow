"""Inquiry Assistant (ICC view) handler."""
from __future__ import annotations

import chainlit as cl

from chainlit_app.handlers.chat_helper import freeform_chat, replay_history


IA_GREETING = (
    "You're in the **ICC view** with access to the Inquiry Assistant (IA).\n\n"
    "IA runs in two phases:\n"
    "  • **Phase 1 (extraction)** enumerates every distinct piece of evidence "
    "in the record as a structured list.\n"
    "  • **Phase 2 (synthesis)** produces a timeline, contradiction table, "
    "and neutral factual summary — operating on the structured list only, "
    "never on the raw record (position-bias mitigation).\n\n"
    "Use the action buttons for structured runs. For procedural questions, "
    "just type — I'll answer within the IA's role limits (no credibility "
    "scoring, no outcome recommendations)."
)


async def start():
    actions = [
        cl.Action(name="ia_phase1",     payload={"phase": 1},      label="IA Phase 1 (extract)"),
        cl.Action(name="ia_phase2",     payload={"phase": 2},      label="IA Phase 2 (synthesise)"),
        cl.Action(name="rpa_draft",     payload={"action": "rpa"}, label="Draft Section 13 report"),
        cl.Action(name="authorise_doc", payload={"action": "auth"}, label="Authorise pending draft (PO)"),
    ]
    await cl.Message(content=IA_GREETING, actions=actions).send()
    role = "presiding_officer" if "presiding_officer" in (cl.user_session.get("roles") or []) else "icc_member"
    await replay_history(role)


async def on_message(message: cl.Message):
    case_id = cl.user_session.get("ia_case_id")
    text = message.content.strip()
    if text.isdigit():
        cl.user_session.set("ia_case_id", int(text))
        await cl.Message(content=(
            f"Operating on case **{text}** for IA tasks."
        )).send()
        return
    role = "presiding_officer" if "presiding_officer" in cl.user_session.get("roles", []) else "icc_member"
    await freeform_chat(role=role, message=message.content, case_id=case_id)


@cl.action_callback("ia_phase1")
async def _ia_phase1(action):
    await cl.Message(content=(
        "IA Phase 1 needs a case ID and an inquiry record. POC: paste the "
        "case ID in chat, then upload the record as text."
    )).send()


@cl.action_callback("ia_phase2")
async def _ia_phase2(action):
    await cl.Message(content=(
        "IA Phase 2 uses the structured evidence list from Phase 1. "
        "It does NOT receive the original record (position-bias mitigation)."
    )).send()


@cl.action_callback("rpa_draft")
async def _rpa(action):
    await cl.Message(content=(
        "Drafts the Section 13 report with four distribution copies. "
        "Findings (sec 7) and conclusions (sec 8) remain placeholders — "
        "those are ICC authorship, not AI authorship."
    )).send()


@cl.action_callback("authorise_doc")
async def _authorise(action):
    await cl.Message(content=(
        "Paste the document ID in chat and I'll call /documents/{id}/authorise "
        "as you, the Presiding Officer."
    )).send()
