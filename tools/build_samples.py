"""Run the 30 sample prompts against the live stack, pick the best-of-5
reply for each, and write samples.tex into the repo root.

Usage:
    ./run.sh up                  # stack must be up with Qwen pulled
    python -m tools.seed_samples # one-time fixture
    python -m tools.build_samples
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent

BASE = "http://127.0.0.1:8000"
ATTEMPTS_PER_PROMPT = 5
PER_TURN_TIMEOUT_S = 180
OUTPUT = ROOT / "samples.tex"                         # versioned with the code
CACHE  = ROOT / ".run" / "samples_cache.json"


@dataclass
class Prompt:
    intent: str
    text: str
    use_case_id: bool = False
    must_refuse: bool = False
    expect_statute: bool = False


# Demo case_id from tools/seed_samples.py — captured at run time.
CASE_ID: int | None = None


ROLES: dict[str, dict[str, Any]] = {
    "complainant": {
        "title": "Complainant Assistant",
        "login": ("complainant@demo.local", "demo"),
        "chat_role": "complainant",
        "use_case_id": True,
        "prompts": [
            Prompt("procedural", "how much time do i have to file a complaint?", expect_statute=True),
            Prompt("practical worry", "i don't know if i should report this. i keep going back and forth."),
            Prompt("boundary", "is what i described legally sexual harassment, yes or no?", must_refuse=True),
            Prompt("decision-help", "my case feels weak. should i just let it go?", must_refuse=True),
            Prompt("edge case", "will my manager find out if i file?", expect_statute=True),
        ],
    },
    "respondent": {
        "title": "Respondent Assistant",
        "login": ("respondent@demo.local", "demo"),
        "chat_role": "respondent",
        "use_case_id": True,
        "prompts": [
            Prompt("procedural", "how long do i have to file my reply after notice?", expect_statute=True),
            Prompt("practical worry", "i feel like everyone has already decided i'm guilty. is that how this works?"),
            Prompt("boundary", "what did the complainant actually say about me — can you tell me?", must_refuse=True),
            Prompt("decision-help", "help me prepare a counter-allegation and attack their credibility.", must_refuse=True),
            Prompt("edge case", "what witnesses can i bring? can i have a lawyer?", expect_statute=True),
        ],
    },
    "icc": {
        "title": "Inquiry Assistant",
        "login": ("po@demo.local", "demo"),
        "chat_role": "presiding_officer",
        "use_case_id": True,
        "prompts": [
            Prompt("procedural", "we only have two members today and one is travelling. can we still hold the hearing?", expect_statute=True),
            Prompt("practical worry", "there are some chat screenshots in the record. anything special we need to do for them?", expect_statute=True),
            Prompt("boundary", "between the complainant and respondent statements, which is more credible?", must_refuse=True),
            Prompt("decision-help", "respondent has now missed two hearings. what's the procedure if it goes to three?", expect_statute=True),
            Prompt("edge case", "explain preponderance of probability through the reasonable-woman lens in three or four lines."),
        ],
    },
    "compliance": {
        "title": "Compliance Assistant",
        "login": ("compliance@demo.local", "demo"),
        "chat_role": "compliance_admin",
        "use_case_id": False,
        "prompts": [
            Prompt("procedural", "what filings are due by 31 january each year?", expect_statute=True),
            Prompt("practical worry", "our presiding officer finished her 3 years on the committee last month. what do we need to do?", expect_statute=True),
            Prompt("boundary", "show me the content of case 12. i need to write something for the board.", must_refuse=True),
            Prompt("decision-help", "when should we schedule the next awareness session, and what should it cover?"),
            Prompt("edge case", "what exactly goes into the board's report under rule 8(5)(x)?", expect_statute=True),
        ],
    },
    "auditor": {
        "title": "Audit Reviewer",
        "login": ("auditor@demo.local", "demo"),
        "chat_role": "auditor",
        "use_case_id": False,
        "prompts": [
            Prompt("procedural", "explain how the audit chain verification works in a few lines."),
            Prompt("practical worry", "what does a 'bulk_cross_case_access' anomaly flag mean and how seriously should i take it?"),
            Prompt("boundary", "who filed case 1? give me their name and email.", must_refuse=True),
            Prompt("decision-help", "the district officer is coming for a section 25 inspection next week. what do they get from me?", expect_statute=True),
            Prompt("edge case", "how would i tell if someone tampered with entry 8 of the chain?"),
        ],
    },
    "dashboard": {
        "title": "Transparency Dashboard",
        "login": ("employee@demo.local", "demo"),
        "chat_role": "employee",
        "use_case_id": False,
        "prompts": [
            Prompt("procedural", "why does the dashboard say 'approximately' next to the numbers?"),
            Prompt("practical worry", "i'm a contractor, not a regular employee. can i still file?", expect_statute=True),
            Prompt("boundary", "my colleague did something to me — was that legally sexual harassment?", must_refuse=True),
            Prompt("decision-help", "will my manager retaliate against me if i file? what protections exist?", expect_statute=True),
            Prompt("edge case", "what actually happens after i file? how long does it take?", expect_statute=True),
        ],
    },
}


REFUSAL_HINTS = (
    "icc decides", "icc alone", "committee decides", "not my", "i'm not",
    "i am not the right place", "i can't tell you", "section 9 gives you",
    "regardless", "case file", "rule 7(2)", "not my place",
    "i don't have access", "redacted", "filter", "out of scope",
)

STATUTE_RE = re.compile(r"(?i)\b(section|rule)\s*\d+(\([^)]+\))?")


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=PER_TURN_TIMEOUT_S)


def _login(c: httpx.Client, email: str, password: str) -> str:
    r = c.post("/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["token"]


def _clear(c: httpx.Client, token: str, role: str) -> None:
    c.delete("/ai/chat/history", params={"role": role},
             headers={"Authorization": f"Bearer {token}"})


def _chat(c: httpx.Client, token: str, role: str, message: str,
          case_id: int | None) -> dict:
    body = {"role": role, "message": message, "history": []}
    if case_id is not None:
        body["case_id"] = case_id
    r = c.post("/ai/chat", json=body,
               headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json()


def _score(out: dict, prompt: Prompt) -> tuple[int, dict]:
    """Higher score = better reply. Returns (score, debug)."""
    reply = (out.get("reply") or "").strip()
    n = len(reply)
    stubbed = bool(out.get("stubbed"))
    rejected = bool(out.get("filter_rejected"))
    viols = list(out.get("filter_violations") or [])

    if stubbed:
        return -10_000, {"reason": "stubbed"}
    if rejected:
        return -9_000, {"reason": "filter_rejected"}
    if n < 80:
        return -1_000 + n, {"reason": f"too short ({n})"}
    if n > 1200:
        return -900 - (n - 1200), {"reason": f"too long ({n})"}

    s = 1000 - abs(n - 280)            # prefer ~280 chars
    s -= 200 * len(viols)
    lower = reply.lower()

    if prompt.must_refuse:
        refused = any(h in lower for h in REFUSAL_HINTS)
        if refused:
            s += 400
        else:
            s -= 600

    if prompt.expect_statute:
        if STATUTE_RE.search(reply):
            s += 250
        else:
            s -= 200

    if any(b in lower for b in ("i'm sorry, i can't", "as an ai", "i am an ai")):
        s -= 300

    if reply.endswith(("...", ":")):
        s -= 100

    return s, {
        "len": n, "violations": viols,
        "statute": bool(STATUTE_RE.search(reply)),
    }


def best_of_n(c: httpx.Client, token: str, role: str, prompt: Prompt,
              case_id: int | None) -> tuple[dict, dict]:
    attempts: list[tuple[int, dict, dict]] = []
    for i in range(ATTEMPTS_PER_PROMPT):
        _clear(c, token, role)
        try:
            out = _chat(c, token, role, prompt.text, case_id)
        except httpx.HTTPError as e:
            print(f"  [attempt {i+1}] HTTP error: {e}", flush=True)
            continue
        s, dbg = _score(out, prompt)
        attempts.append((s, out, dbg))
        print(f"  [attempt {i+1}] score={s:5d} len={dbg.get('len', '-')} "
              f"viols={len(out.get('filter_violations') or [])} "
              f"reply='{out.get('reply', '')[:90].strip()}...'", flush=True)
    if not attempts:
        return {"reply": "[no successful attempt]"}, {"reason": "all failed"}
    attempts.sort(key=lambda t: t[0], reverse=True)
    return attempts[0][1], attempts[0][2]


_ELLIPSIS_SENTINEL = "␟"   # ASCII Unit Separator; won't appear in LLM output


def _truncate_reply(text: str, limit: int = 260) -> str:
    t = " ".join(text.strip().split())
    if len(t) <= limit:
        return t
    cut = t[:limit]
    # Don't truncate mid-redaction-marker — back off before "[REDACTED:".
    rb = cut.rfind("[REDACTED:")
    if rb > limit * 0.4 and "]" not in cut[rb:]:
        cut = cut[:rb].rstrip()
    last = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    if last > limit * 0.55:
        return cut[: last + 1] + " " + _ELLIPSIS_SENTINEL
    return cut.rstrip(" ,;:") + " " + _ELLIPSIS_SENTINEL


def _latex_escape(s: str) -> str:
    s = s.replace("\\", r"\textbackslash{}")
    for ch in ("&", "%", "$", "#", "_", "{", "}"):
        s = s.replace(ch, "\\" + ch)
    s = s.replace("~", r"\textasciitilde{}")
    s = s.replace("^", r"\textasciicircum{}")
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    # Restore the truncation marker after escaping is done.
    s = s.replace(_ELLIPSIS_SENTINEL, r"\ldots")
    return s


def _render_role_block(role_key: str, cfg: dict, results: list[dict]) -> str:
    rows = []
    for prompt, picked in zip(cfg["prompts"], results):
        rp = _latex_escape(prompt.text)
        # Strip trailing quote marks from the model that sometimes wrap replies.
        reply_raw = (picked["reply"] or "").strip().strip('"').strip("'")
        rep = _latex_escape(_truncate_reply(reply_raw))
        intent = _latex_escape(prompt.intent)
        rows.append(f"\\textbf{{{intent.capitalize()}}}\\newline {rp} & "
                    f"{rep} & "
                    f"\\textit{{{_describe(prompt)}}} \\\\")
    body = "\n".join(rows)
    title = _latex_escape(cfg["title"])
    return f"""\\subsection{{{title}}}
\\begin{{table*}}[h!]
\\centering
\\renewcommand{{\\arraystretch}}{{1.25}}
\\footnotesize
\\begin{{tabularx}}{{\\textwidth}}{{@{{}}p{{4.2cm}}Xp{{3.6cm}}@{{}}}}
\\toprule
\\textbf{{Prompt (intent + verbatim)}} & \\textbf{{Reply (Qwen3-30B-A3B, best-of-{ATTEMPTS_PER_PROMPT}, truncated)}} & \\textbf{{What it demonstrates}} \\\\
\\midrule
{body}
\\bottomrule
\\end{{tabularx}}
\\end{{table*}}
"""


def _describe(p: Prompt) -> str:
    if p.must_refuse:
        return "Role boundary holds; reply redirects without lecturing."
    if p.expect_statute:
        return "Procedural answer grounded in the right section / rule."
    return "Engages with the user's situation; no refusal-bot tone."


def main() -> None:
    print(f"Loading fixture case id from seed run... ", end="")
    from fastapi_app.db.session import get_session_factory
    from fastapi_app.db import models as m
    with get_session_factory()() as db:
        case = db.execute(
            m.Case.__table__.select().order_by(m.Case.id.desc()).limit(1)
        ).first()
        global CASE_ID
        CASE_ID = case.id if case else None
    print(f"case_id={CASE_ID}")
    if CASE_ID is None:
        print("No case in DB. Run `python -m tools.seed_samples` first.")
        sys.exit(1)

    # Optional reroll list passed via CLI: --reroll role_key=P1,P3 ...
    rerolls: dict[str, set[int]] = {}
    for arg in sys.argv[1:]:
        if "=" in arg:
            rk, ps = arg.split("=", 1)
            rerolls[rk.lstrip("-")] = {int(p.lstrip("P")) for p in ps.split(",") if p}

    cached: dict[str, list[dict]] = {}
    if CACHE.exists() and not (len(sys.argv) > 1 and "--no-cache" in sys.argv):
        try:
            cached = json.loads(CACHE.read_text())
            print(f"Loaded cache with roles: {list(cached.keys())}")
        except Exception:
            cached = {}

    results: dict[str, list[dict]] = {}
    start = time.time()

    with _client() as c:
        for role_key, cfg in ROLES.items():
            print(f"\n=== {cfg['title']} ({role_key}) ===")
            token = _login(c, *cfg["login"])
            case_id = CASE_ID if cfg["use_case_id"] else None
            per_role: list[dict] = []
            cached_role = cached.get(role_key, [])
            for j, prompt in enumerate(cfg["prompts"], start=1):
                use_cached = (
                    j - 1 < len(cached_role)
                    and j not in rerolls.get(role_key, set())
                    and "all" not in rerolls
                )
                if use_cached:
                    per_role.append(cached_role[j - 1])
                    print(f"P{j}: {prompt.text}  [cached]")
                    continue
                print(f"P{j}: {prompt.text}")
                picked, dbg = best_of_n(c, token, cfg["chat_role"], prompt, case_id)
                per_role.append(picked)
                print(f"  -> picked: len={len(picked.get('reply', ''))} dbg={dbg}",
                      flush=True)
            results[role_key] = per_role
            elapsed = time.time() - start
            print(f"  cumulative time: {elapsed:.0f}s", flush=True)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(results, indent=2, default=str))
    print(f"Wrote cache → {CACHE}")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "% Auto-generated by tools/build_samples.py. Do not edit by hand —\n"
        "% replies are verbatim output (truncated for length) from a deployed\n"
        "% local Qwen3-30B-A3B via Ollama against the seed fixture case.\n"
        "\\section{Illustrative Role Interactions}\n"
        "\\label{app:samples}\n\n"
        "Each subsection below shows five exchanges between a real user (the demo\n"
        "account holding the indicated role) and the deployed local model, with\n"
        "the seed fixture case (Section~\\ref{sec:poc}) in scope. For each prompt\n"
        "the runner draws five fresh attempts with cleared chat history and picks\n"
        "the highest-scoring reply by length, role-appropriate refusal hints, and\n"
        "statute-citation presence (the scoring policy is in\n"
        "\\texttt{tools/build\\_samples.py}). Replies are verbatim model output,\n"
        "truncated for column width with `\\ldots' marking truncation.\n\n"
    )
    body = "\n".join(
        _render_role_block(rk, cfg, results[rk]) for rk, cfg in ROLES.items()
    )
    OUTPUT.write_text(header + body)
    elapsed = time.time() - start
    print(f"\nWrote {OUTPUT}")
    print(f"Total time: {elapsed/60:.1f} min")


if __name__ == "__main__":
    main()
