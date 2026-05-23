"""Complainant Assistant chat handler."""
from __future__ import annotations

from datetime import date

import chainlit as cl

from chainlit_app.handlers.chat_helper import freeform_chat, replay_history


CA_GREETING = (
    "Hello — I'm the **Complainant Assistant**. I'll help you describe what "
    "happened in your own words and structure it as a written complaint that "
    "the Internal Committee (ICC) can act on under Section 9.\n\n"
    "Before we start: your account is the legal record. I will preserve your "
    "exact wording. I will not classify what happened legally — that's the "
    "ICC's job — and I will not discourage you from filing.\n\n"
    "When you're ready, share what happened in as much detail as you'd like. "
    "I'll ask follow-ups for: **when**, **where**, **who**, **what** (in your "
    "words), **pattern** (one incident or continuing), and your **relationship "
    "to the workplace**.\n\n"
    "_You can also ask me general questions about the POSH process at any "
    "time — type a question and I'll answer within role limits._"
)


async def start():
    cl.user_session.set("ca_state", "awaiting_narrative")
    cl.user_session.set("ca_case_id", None)
    await cl.Message(content=CA_GREETING).send()
    # Replay any prior conversation from earlier browser sessions.
    n = await replay_history("complainant")
    if n > 0:
        cl.user_session.set("ca_state", "ongoing")


def _looks_like_narrative_submission(text: str) -> bool:
    """A long, first-person, multi-sentence message is treated as narrative.

    Short / question-shaped messages route through freeform chat instead.
    """
    stripped = text.strip()
    if "?" in stripped[:200] and len(stripped) < 400:
        return False
    return len(stripped) >= 150


async def on_message(message: cl.Message):
    api = cl.user_session.get("api")
    if api is None:
        await cl.Message(content="Not logged in. Reload to log in.").send()
        return

    state = cl.user_session.get("ca_state")

    if state == "awaiting_narrative" and _looks_like_narrative_submission(message.content):
        case = api.create_case({
            "incident_date": date.today().isoformat(),
            "incident_continuing": False,
        })
        cl.user_session.set("ca_case_id", case["id"])

        if case.get("routed_to") == "pocso_police":
            await cl.Message(content=(
                f"⚠️ This case has been routed to the **POCSO mandatory "
                f"police reporting** path because the complainant is a minor. "
                f"The Presiding Officer has been notified. Case ID: {case['id']}."
            )).send()
            return
        if case.get("routed_to") == "lcc":
            await cl.Message(content=(
                f"This case has been flagged for the **Local Committee (LCC)** "
                f"because the named respondent is the employer (Section 6). "
                f"Case ID: {case['id']}."
            )).send()

        body = {
            "case_id": case["id"],
            "raw_narrative": message.content,
            "complainant_metadata": {
                "age_band": "25_34",
                "employment_status": "employee",
                "workplace_location_type": "office",
            },
            "entity_config": {
                "jurisdiction": "IN-TN",
                "gender_scope": "inclusive",
            },
        }
        ca_out = api.ai_ca(body)
        msg = (
            f"Thank you. I've prepared a structured draft of your complaint "
            f"(document ID **{ca_out['structured_complaint_draft_id']}**). "
            f"The verbatim original is preserved separately.\n\n"
            f"Next: the **Presiding Officer** must authorise this draft "
            f"before it becomes a Section 9 written complaint and the case "
            f"can move to *Filed*. You'll be notified when that happens.\n\n"
            f"_You can ask me any follow-up questions about what happens next._"
        )
        await cl.Message(content=msg).send()
        cl.user_session.set("ca_state", "ongoing")
        return

    # Anything else — questions, follow-ups, post-submission chat — routes
    # through the role-bounded /ai/chat endpoint.
    case_id = cl.user_session.get("ca_case_id")
    await freeform_chat(
        role="complainant",
        message=message.content,
        case_id=case_id,
    )
