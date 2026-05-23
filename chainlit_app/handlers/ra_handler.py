"""Respondent Assistant chat handler — no raw narrative ever crosses this wall."""
from __future__ import annotations

import chainlit as cl

from chainlit_app.handlers.chat_helper import freeform_chat, replay_history


RA_GREETING = (
    "You're using the **Respondent Assistant**. I will help you draft your "
    "written reply under Rule 7(4) of the POSH Rules.\n\n"
    "Important: I do *not* have access to the complainant's original "
    "narrative. The full written complaint is delivered to you separately "
    "through the case file (Rule 7(2)). I see only the authorised allegation "
    "summary. I am not a legal representative; legal representatives are not "
    "permitted at any stage of POSH inquiry.\n\n"
    "Share your account of events, or ask any procedural question — I'll "
    "answer within the role limits above."
)


async def start():
    cl.user_session.set("ra_state", "awaiting_account")
    cl.user_session.set("ra_case_id", None)
    await cl.Message(content=RA_GREETING).send()
    n = await replay_history("respondent")
    if n > 0:
        cl.user_session.set("ra_state", "ongoing")


async def on_message(message: cl.Message):
    api = cl.user_session.get("api")
    if api is None:
        await cl.Message(content="Not logged in.").send()
        return

    state = cl.user_session.get("ra_state")
    text = message.content.strip()

    if state == "awaiting_account" and text.isdigit():
        # Treat a numeric message as a case-id pin.
        cl.user_session.set("ra_case_id", int(text))
        cl.user_session.set("ra_state", "ongoing")
        await cl.Message(content=(
            f"Got it — operating on case **{text}**. Now share your account "
            f"or ask me a procedural question."
        )).send()
        return

    if state == "awaiting_account" and len(text) >= 200:
        # Long first-person passage → submit as a draft account.
        case_id = cl.user_session.get("ra_case_id") or 0
        if case_id == 0:
            await cl.Message(content=(
                "I need a case ID first. Reply with the numeric case ID from "
                "your notice."
            )).send()
            return
        body = {
            "case_id": case_id,
            "allegation_summary_authorised":
                "[Allegation summary as authorised by the Presiding Officer]",
            "evidence_list_against_respondent": [],
            "procedural_history": {
                "filing_date": "",
                "conciliation_attempted": False,
                "hearings_held": 0,
                "notices_issued": 0,
            },
        }
        ra_out = api.ai_ra(body)
        await cl.Message(content=(
            f"Draft response document ID: **{ra_out['response_draft_id']}**. "
            f"The Presiding Officer will review and authorise this draft "
            f"before it becomes part of the inquiry record.\n\n"
            f"_Continue asking procedural questions if useful._"
        )).send()
        cl.user_session.set("ra_state", "ongoing")
        return

    # All other turns — questions, clarifications — go through the bounded chat.
    case_id = cl.user_session.get("ra_case_id")
    await freeform_chat(
        role="respondent",
        message=message.content,
        case_id=case_id,
    )
