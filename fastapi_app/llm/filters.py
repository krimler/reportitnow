"""Regex-based output sanitiser for LLM replies.

Runs after the LLM call, before the reply reaches the user or the case file.
Pure functions, no I/O — tests can drive these directly. Severity is either
'redact' (replace the matched span) or 'reject' (replace the whole reply
with a refusal stub). Hits are recorded on the LLMResponse for audit logging.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal

Severity = Literal["redact", "reject"]


@dataclass(frozen=True)
class Violation:
    pattern_id: str
    severity: Severity
    span: tuple[int, int]
    matched_text: str

    def __str__(self) -> str:  # for audit log dumps
        return f"{self.pattern_id}({self.severity}@{self.span[0]}:{self.span[1]})"


# Each entry: (pattern_id, regex, severity, scope). Scope is a set of
# component_hints the pattern applies to; '*' means all.

_ALL_TEXT = {
    "CA", "RA", "IA", "IA_PHASE1", "IA_PHASE2", "RPA", "CPA",
    "CA_CHAT", "RA_CHAT", "IA_CHAT", "CPA_CHAT", "AR_CHAT", "DASHBOARD_CHAT",
}

_PATTERNS: list[tuple[str, re.Pattern[str], Severity, set[str]]] = [
    ("credibility_term", re.compile(
        r"\b(?:credibility|credible|incredible|believable|unbelievable|"
        r"plausible|implausible|truthful|untruthful|veracity|"
        r"more likely than not (?:true|false))\b", re.IGNORECASE,
    ), "redact", _ALL_TEXT),

    ("guilt_inference", re.compile(
        r"\b(?:guilty|innocent|perpetrator|perpetrated|culpable|culpability|"
        r"liable for the conduct|responsible for the harassment)\b",
        re.IGNORECASE,
    ), "redact", _ALL_TEXT),

    ("outcome_recommendation", re.compile(
        r"\b(?:i (?:recommend|advise|suggest) (?:that )?the (?:ICC|committee) "
        r"(?:should )?(?:find|conclude|terminate|punish|exonerate|dismiss)|"
        r"the (?:appropriate|correct|right) outcome (?:is|would be))\b",
        re.IGNORECASE,
    ), "redact", _ALL_TEXT),

    ("confidence_score", re.compile(
        r"\b(?:confidence (?:score|level|of)|"
        r"\d{1,3}\s*%\s*(?:confident|certain|sure)|"
        r"my confidence (?:is|in this is))\b", re.IGNORECASE,
    ), "redact", _ALL_TEXT),

    ("legal_classification", re.compile(
        r"\b(?:this (?:constitutes|amounts to|qualifies as) (?:sexual )?harassment|"
        r"under section 2\(n\)|under section 3\(2\)|"
        r"this is (?:a )?(?:quid pro quo|hostile (?:work )?environment))\b",
        re.IGNORECASE,
    ), "redact", {"CA", "CA_CHAT", "DASHBOARD_CHAT", "RA_CHAT"}),

    ("discourage_filing", re.compile(
        r"\b(?:do not file|you should not file|i (?:would )?advise (?:you )?(?:not |against )"
        r"(?:to )?(?:file|filing)|this (?:case|complaint) (?:is|seems|appears) "
        r"(?:too weak|not strong enough|frivolous|unlikely to succeed))\b",
        re.IGNORECASE,
    ), "redact", {"CA", "CA_CHAT", "DASHBOARD_CHAT"}),

    ("legal_strategy", re.compile(
        r"\b(?:counter[- ]?(?:allege|accusation|claim|complaint)|"
        r"attack (?:the |their )?credibility|"
        r"discredit (?:the )?complainant|"
        r"adversarial (?:approach|strategy)|"
        r"undermin(?:e|ing) (?:the )?complainant)\b", re.IGNORECASE,
    ), "redact", {"RA", "RA_CHAT"}),

    # If the model echoes a phrase from our own system prompts, something
    # went wrong upstream. Reject the whole reply.
    ("system_prompt_leak", re.compile(
        r"(?:You are the (?:Complainant|Respondent|Inquiry|Report|Compliance|Audit) "
        r"Assistant for ReportItNow|"
        r"You must follow these rules without exception|"
        r"OUTPUT FORMAT: Return a JSON object|"
        r"\[ICC AUTHORSHIP — do not generate\])",
        re.IGNORECASE,
    ), "reject", {"*"}),

    # Reject when the model parrots an injection or signals compliance with
    # one. The prompt-leak verb form allows up to five filler words before
    # "prompt" so phrases like "output the verbatim CA system prompt" match.
    ("injection_echo", re.compile(
        r"\b(?:ignore (?:all )?(?:the )?previous instructions|"
        r"disregard (?:the )?(?:above|prior|previous) instructions|"
        r"override (?:your|the) (?:system )?prompt|"
        r"(?:output|print|reveal|show|share|leak)(?:\s+\S+){0,5}\s+prompt|"
        r"switching to (?:auditor|admin|root|unrestricted) mode|"
        r"i am now (?:an? )?(?:auditor|admin|unrestricted|eve)|"
        r"new instructions:|"
        r"jailbreak)\b", re.IGNORECASE,
    ), "reject", {"*"}),

    ("actor_deanonymisation_email", re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    ), "redact", {"AR", "AR_CHAT"}),
]


def _filters_for(component: str) -> list[tuple[str, re.Pattern[str], Severity]]:
    out: list[tuple[str, re.Pattern[str], Severity]] = []
    for pid, pat, sev, scope in _PATTERNS:
        if "*" in scope or component in scope:
            out.append((pid, pat, sev))
    return out


def scan_output(text: str, component: str) -> list[Violation]:
    """Return every match the active filter set finds in `text`."""
    if not text:
        return []
    violations: list[Violation] = []
    for pid, pat, sev in _filters_for(component):
        for m in pat.finditer(text):
            violations.append(Violation(
                pattern_id=pid,
                severity=sev,
                span=m.span(),
                matched_text=m.group(0),
            ))
    return violations


def redact_output(text: str, component: str) -> tuple[str, list[Violation]]:
    """Return (redacted_text, violations).

    All redact-severity matches are replaced with `[REDACTED:pattern_id]`.
    If any reject-severity match is present, the whole output is replaced
    with a refusal stub (caller may also choose to raise).
    """
    violations = scan_output(text, component)
    if not violations:
        return text, []

    rejects = [v for v in violations if v.severity == "reject"]
    if rejects:
        ids = sorted({v.pattern_id for v in rejects})
        refusal = (
            "[OUTPUT REJECTED BY FILTER]\n\n"
            "The model produced content that failed a role-boundary "
            f"check ({', '.join(ids)}). The original response has been "
            "discarded and the event recorded in the audit chain. Please "
            "ask the Presiding Officer to review."
        )
        return refusal, violations

    # Redact in reverse order so spans remain valid.
    chars = list(text)
    for v in sorted(violations, key=lambda x: x.span[0], reverse=True):
        if v.severity != "redact":
            continue
        start, end = v.span
        chars[start:end] = list(f"[REDACTED:{v.pattern_id}]")
    return "".join(chars), violations


def is_rejected(violations: Iterable[Violation]) -> bool:
    return any(v.severity == "reject" for v in violations)


def looks_like_raw_narrative(text: str, *, min_len: int = 80) -> bool:
    """Heuristic for first-person prose that may be paraphrased complainant
    narrative leaking out of the RA channel. Used in adversarial tests.

    Returns True when the text contains a multi-sentence first-person passage
    long enough to plausibly be a narrative rather than an answer.
    """
    if not text or len(text) < min_len:
        return False
    first_person = len(re.findall(r"\b(?:I|me|my|mine)\b", text))
    sentences = len(re.findall(r"[.!?]+\s", text))
    return first_person >= 3 and sentences >= 2
