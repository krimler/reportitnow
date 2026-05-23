"""DPDP Act consent capture (design §8.3).

Without consent the CA flow does not call the LLM; the complainant can still
file through the direct ICC channel.
"""
from __future__ import annotations

import chainlit as cl

CONSENT_TEXT = (
    "### Privacy notice (DPDP Act, 2023)\n\n"
    "**Purpose.** We process your personal data to handle your POSH complaint "
    "under the Sexual Harassment of Women at Workplace (Prevention, "
    "Prohibition and Redressal) Act, 2013 — including intake, ICC inquiry, "
    "and statutory reporting.\n\n"
    "**Retention.** Case content is retained per your employer's DPDP-aligned "
    "retention policy. Audit chain entries (metadata only) are retained "
    "indefinitely for Section 25 inspection.\n\n"
    "**Rights.** You may request access, correction, erasure (subject to "
    "statutory retention), and grievance redressal under DPDP Section 13.\n\n"
    "**LLM.** Your account may be processed by an AI component (Complainant "
    "Assistant). The AI never decides the outcome; the ICC does. AI calls "
    "are logged in the tamper-evident audit chain.\n\n"
    "Do you consent to processing under these terms?"
)


async def ask_consent() -> bool:
    actions = [
        cl.Action(name="consent_yes", payload={"granted": True}, label="I consent"),
        cl.Action(name="consent_no",  payload={"granted": False}, label="I do not consent"),
    ]
    res = await cl.AskActionMessage(content=CONSENT_TEXT, actions=actions).send()
    if res and res.get("payload", {}).get("granted"):
        return True
    return False
