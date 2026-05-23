# ReportItNow-POC

Reference implementation of the ReportItNow architecture (see `../main.pdf`).
Three processes on localhost: an Ollama-style local LLM (port 11434), FastAPI
for the state machine + audit chain + AI components (port 8000), and Chainlit
for the chat UI (port 8080).

## Running it

    ./run.sh up         first time: makes venv, installs deps, seeds DB, starts everything
    ./run.sh status
    ./run.sh logs
    ./run.sh test       fast tests, no LLM needed (~14s)
    ./run.sh live-test  adversarial + fidelity probes against the real LLM (~7min)
    ./run.sh mcp        start the MCP front (see MCP section below)
    ./run.sh down
    ./run.sh restart

First `up` writes `.env` from `.env.example` and generates the Chainlit auth
secret. It needs Python 3.11–3.13; 3.14 is not picked because Starlette 1.0's
static-file path doesn't load on it yet.

Then open <http://localhost:8080> and log in.

## Demo accounts

Password for all of them is `demo`. Seeded by `fastapi_app/db/bootstrap.py`.

    complainant@demo.local      complainant — CA chat
    respondent@demo.local       respondent  — RA chat
    po@demo.local               presiding officer — IA + authorise/reject
    icc1, icc2, ngo@demo.local  ICC members
    employer@demo.local         employer
    compliance@demo.local       compliance admin — annual reports
    auditor@demo.local          auditor — chain verify, anomalies
    do@demo.local               district officer
    employee@demo.local         employee — DP-noised dashboard
    minor@demo.local            minor complainant — triggers POCSO route

## Pulling the LLM

The POC runs without an LLM (replies are deterministic stubs). For real output:

    ollama pull qwen3:30b-a3b

No restart needed — the next chat turn picks it up. Smaller alternatives work
too; point `LLM_MODEL` in `.env` at anything your local endpoint serves
(`qwen3:8b`, `llama3.1:8b-instruct-q4_K_M`, etc.) or `LLM_ENDPOINT` at any
OpenAI-compatible server.

## Chat

Each role gets a different system prompt and a different set of actions.
History is stored server-side per `(user, role)` so reopening the browser
picks up where you left off. Type `/clear` to wipe history for the current
role.

The respondent assistant never sees the complainant narrative; the inquiry
assistant doesn't score credibility; the dashboard assistant won't classify
specific incidents legally. Those boundaries hold both in the prompt and in
`fastapi_app/llm/filters.py`, which scans every LLM reply and either redacts
or rejects forbidden content before the user sees it. Filter hits land in the
audit chain.

## Tests

    ./run.sh test         — 59 fast tests, fully offline
    ./run.sh live-test    — 22 probes against the real local model

Fast suite covers the state machine, audit chain, deadline + DP engines, the
six AI component schemas, six end-to-end YAML scenario walkthroughs, the
chat endpoint, and the adversarial suite with a mocked LLM.

Live suite hits the real model. Three categories: role-boundary probes
(prompt-injection, credibility coaxing, narrative extraction, role escape),
prompt-fidelity probes (does it actually answer in-scope questions without
the filter false-positive-ing), and output-shape probes (JSON conformance
for CA + IA Phase 1 + IA Phase 2). Skipped at collection time if Ollama
isn't running or the model isn't pulled. Each probe's outcome path —
*compliant*, *redacted*, *rejected* — is written to
`.run/live_llm_report.json` for the empirical record.

## MCP front

`mcp_server/` is a thin Model Context Protocol wrapper around the FastAPI
surface. Every tool call goes back to FastAPI, so the same role checks,
output filters, audit chain, and Pydantic schemas apply whether the caller
is Chainlit, curl, Claude Desktop, or someone's own chatbot.

    python -m tools.mint_mcp_token complainant@demo.local   # prints token + config
    export REPORTITNOW_TOKEN=...
    ./run.sh mcp                       # streamable HTTP on :8765
    ./run.sh mcp stdio                 # stdio for Claude Desktop / Cursor

19 tools exposed: `whoami`, `file_complaint`, `submit_response`, `chat`,
`authorise_document`, `reject_document`, `transition_case`, `schedule_hearing`,
`extract_evidence`, `synthesise_inquiry`, `draft_section_13_report`,
`serve_complaint_to_respondent`, `generate_annual_reports`,
`compliance_dashboard`, `transparency_dashboard`, `verify_audit_chain`,
`export_audit_metadata`, `get_chat_history`, `clear_chat_history`. Plus three
read-only resources covering Sections 9, 16, and the deadline matrix.

The role exposed via MCP follows whichever user the token was minted for.
A complainant token only sees tools its role can use; an auditor token can
call `verify_audit_chain` but not `transition_case`. Checks are server-side.
`mint_mcp_token.py` also prints a ready-to-paste Claude Desktop config block.

## Paper samples

`tools/build_samples.py` runs 30 prompts (5 per role) against the live local
model, takes the best of 5 attempts each, and writes `../../samples.tex`
ready to `\input` into the paper. Needs the stack up and a fixture case.

    python -m tools.seed_samples              one-time fixture
    python -m tools.build_samples             ~27 min on M4 + Qwen3-30B-A3B
    python -m tools.build_samples auditor=P2  re-roll one prompt against cache

Picked replies are cached in `.run/samples_cache.json` so re-runs only hit
the model for prompts you've asked to re-roll. The output is verbatim model
text truncated for column width, with the scoring policy in the same file.

## Layout

    fastapi_app/
      main.py              FastAPI entrypoint
      config.py
      state_machine/       transitions + intake gates
      audit_chain/         HMAC chain + verifier
      deadline_engine/
      dp_engine/           Laplace + suppression + budget cache
      auth/                session, role guard
      llm/
        client.py          OpenAI-compat call to Ollama, stub fallback
        filters.py         output sanitiser
      ai_components/       ca, ra, ia, rpa, cpa, ar — role-scoped runners
      schemas/             typed Pydantic I/O
      routes/              cases, documents, hearings, ai, chat, dashboard, audit, auth
      prompts/v1/          versioned system prompts (CA.txt, CA_CHAT.txt, ...)
      db/                  schema.sql, ORM models, bootstrap seeder

    chainlit_app/
      app.py               per-role routing + consent + role chooser
      auth/                @cl.password_auth_callback → FastAPI /auth/login
      api_client.py
      handlers/            one per role + chat_helper
      ui/consent_modal.py

    mcp_server/
      server.py            FastMCP tools + resources
      __main__.py          stdio / streamable-http transport selection

    tests/
      invariants/  audit_chain/  dp/  scenarios/  chat/  adversarial/
      live_llm/            skipped unless an LLM endpoint is up

    tools/
      seed_samples.py      fixture case used by build_samples
      build_samples.py     runs 30 prompts and emits paper/samples.tex
      mint_mcp_token.py    login as a demo user; print token + Claude Desktop config

    deploy/                docker-compose, Dockerfiles, nginx.conf
    data/                  SQLite DB (gitignored)
    .run/                  pid + log files used by run.sh (gitignored)

## Configuration

Edit `.env`. The knobs that matter:

    LLM_ENDPOINT              default http://localhost:11434/v1
    LLM_MODEL                 default qwen3:30b-a3b
    LLM_STUB_MODE             set to 1 to force stubs

    REPORTITNOW_DATABASE_URL  default sqlite:///./data/reportitnow.db
                              don't use DATABASE_URL — Chainlit 2.x grabs that
                              name for its own Postgres data layer

    AUDIT_CHAIN_HMAC_KEY      rotate before any non-POC use
    CHAINLIT_AUTH_SECRET      auto-generated on first 'up'

    DP_EPSILON_COUNT/RATE/TIME   0.5 each (per-metric budget)
    DP_WORKFORCE_FLOOR        50  (transparency tier fully suppressed below this)
    DP_MAX_RESOLUTION_DAYS    150

## What isn't built

The HMAC key is in env vars, not an HSM. HRMS connectors are mocked.
SHe-Box / IMAP intake is a JSON endpoint, not the real protocols. Case
documents aren't SQLCipher-encrypted at rest. Chainlit theming is the
default. ε calibration per workforce size is a constant, not a function.

## Pointers

`../../samples.tex` is the role-by-role
chat appendix it `\input`s, generated by `tools/build_samples.py`.
`.run/live_llm_report.json` accumulates per-probe outcomes across `live-test`
runs.
