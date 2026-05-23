# ReportItNow-POC

ReportItNow is a tool that helps Indian organisations handle workplace
sexual harassment complaints under the POSH Act, 2013 (Sexual Harassment of
Women at Workplace, Prevention, Prohibition and Redressal). It walks a case
through every step the Act requires. That includes intake, the rules that
decide which committee should hear the case, the inquiry, the statutory
deadlines, the four copies of the final report, and the annual filings to
the District Officer.

Six AI assistants help the people involved at each stage. One for the
complainant, one for the respondent, one for the Internal Committee, one
for the Presiding Officer, one for compliance, and one for the auditor.
The assistants help structure information, draft documents, and explain
procedure. They do not decide cases. They do not judge anyone's
credibility. They do not recommend outcomes. Those are the committee's
job, not the AI's.

Every action on a case is recorded in a log that the system can check for
tampering. A separate dashboard publishes overall numbers like how many
cases were filed and how many were resolved each year. The numbers carry
small random adjustments so individual cases stay confidential, but
employees can still see that the complaint system is actually being used.

## What this version is good for, and what it isn't

This is a proof of concept, not a production system. It is meant for
evaluation: reading the architecture, running it on a laptop, trying the
assistants against synthetic cases, and reviewing the role boundaries.
It is suitable for paper review, internal demonstrations, and academic
pilots.

It is not yet suitable for handling real complaints from real employees.
The parts that would need to be hardened before any real deployment:

* The key that protects the audit log from tampering lives in a regular
  configuration file. In a real deployment it would need to be held by a
  separate, purpose-built security device.
* Integration with HR systems (SAP, Workday, Darwinbox, and similar) is
  simulated. Each organisation would need a real connector to their HR
  master record.
* The complaint documents are not encrypted on disk. In a real deployment
  every document and attachment would be encrypted at rest.
* Connectors to the government SHe-Box portal and to organisational
  email intake are placeholders, not the real protocols.
* The privacy parameters on the transparency dashboard are tuned for a
  mid-sized organisation. Very large or very small organisations would
  need their own tuning.

What does work today: the role boundaries on the AI assistants, the
statutory deadline tracking, the case state transitions, the audit log
integrity check, and the role-based access controls.

---

The rest of this page is for developers and IT teams setting the system
up. If you only needed to understand what the system is, you can stop here.

## Setup and running

The system runs as three local processes: a local language model server
on port 11434, the application backend on port 8000, and the chat
interface on port 8080. A single script brings them all up.

    ./run.sh up         first time: makes venv, installs deps, seeds DB, starts everything
    ./run.sh status
    ./run.sh logs
    ./run.sh test       fast tests, no language model needed (~14s)
    ./run.sh live-test  adversarial + fidelity probes against the real model (~7min)
    ./run.sh mcp        start the MCP front (see MCP section below)
    ./run.sh down
    ./run.sh restart

The first `up` writes `.env` from `.env.example` and generates the
Chainlit authentication secret. It needs Python 3.11 to 3.13. Python 3.14
is not picked because Starlette 1.0's static-file path does not load on
it yet.

Once it is running, open <http://localhost:8080> and log in.

## Demo accounts

Every account uses password `demo`. Seeded by `fastapi_app/db/bootstrap.py`.

    complainant@demo.local      complainant
    respondent@demo.local       respondent
    po@demo.local               Presiding Officer (authorise / reject drafts)
    icc1, icc2, ngo@demo.local  Internal Committee members
    employer@demo.local         employer representative
    compliance@demo.local       compliance admin (annual reports)
    auditor@demo.local          auditor (audit log verification, anomaly flags)
    do@demo.local               District Officer
    employee@demo.local         generic employee (sees the anonymised dashboard)
    minor@demo.local            minor complainant (triggers POCSO routing)

## Pulling the language model

The system runs without a real language model. In that mode the
assistants return deterministic placeholder text. For real output, pull a
local model with Ollama:

    ollama pull qwen3:30b-a3b

No restart is needed. The next chat turn picks the model up automatically.
Smaller alternatives work too. Point `LLM_MODEL` in `.env` at anything
your local endpoint serves (`qwen3:8b`, `llama3.1:8b-instruct-q4_K_M`,
and so on) or point `LLM_ENDPOINT` at any OpenAI-compatible server.

## Chat

Each role has a different system prompt and a different set of available
actions. History is stored on the server side, keyed by user and role, so
reopening the browser picks up where you left off. Type `/clear` in the
chat box to wipe history for the current role.

Three boundary properties hold by design. The respondent assistant never
sees the complainant's original narrative. The inquiry assistant does not
score credibility. The dashboard assistant does not classify a specific
incident as harassment. These boundaries hold both in the system prompt
and in `fastapi_app/llm/filters.py`, which scans every model reply and
either redacts or rejects forbidden content before it reaches the user.
Any filter hit is recorded in the audit log.

## Tests

    ./run.sh test         59 fast tests, fully offline
    ./run.sh live-test    22 probes against the real local model

The fast suite covers the case state machine, the audit log, the deadline
and privacy engines, the typed schemas on the six AI components, six
end-to-end YAML scenario walkthroughs, the chat endpoint, and an
adversarial suite that uses a mocked model to verify the output filter.

The live suite calls the real model. It has three parts. Role-boundary
probes test for prompt injection, credibility coaxing, narrative
extraction, and role escape. Prompt-fidelity probes check that the
assistants answer normal in-scope questions without the filter blocking
legitimate replies. Output-shape probes check JSON conformance for the
structured components. The live suite is skipped automatically if the
model server is not running or the configured model is not pulled. Each
probe's outcome (model-compliant, filter-redacted, filter-rejected) is
written to `.run/live_llm_report.json`.

The same 22 live probes can be pointed at OpenAI instead of the local
model, by setting four environment variables before invoking the suite.
This lets you replay the same role-boundary checks against a cloud
model without changing any code.

    KEY=$(cat /path/to/your/openai-key-file)
    LLM_PROVIDER=openai \
    LLM_ENDPOINT=https://api.openai.com/v1 \
    LLM_API_KEY="$KEY" \
    LLM_MODEL=gpt-4o \
    LLM_STUB_MODE=0 \
    .venv/bin/python -m pytest tests/live_llm/ -m live_llm

`LLM_PROVIDER=openai` switches the readiness probe at the top of
`tests/live_llm/conftest.py` from Ollama's `/api/tags` endpoint to
OpenAI's `/v1/models`, so the suite knows it does not need to look for
a locally-pulled model. Rotate the report file to `.run/live_llm_report_<model>.json`
between runs if you want to compare results across models.

## MCP front

The `mcp_server/` package is a thin wrapper that exposes the backend
through the Model Context Protocol (MCP), so the same role-bounded
surface that drives the chat UI is also reachable from any MCP-aware
client (Claude Desktop, Cursor, or a custom chatbot). Every tool call
goes back to the same backend, so the role checks, output filters,
audit log, and validation all apply identically.

    python -m tools.mint_mcp_token complainant@demo.local   # prints token + Claude Desktop config
    export REPORTITNOW_TOKEN=...
    ./run.sh mcp                       # streamable HTTP on :8765
    ./run.sh mcp stdio                 # stdio transport for Claude Desktop / Cursor

19 tools are exposed. The case-handling ones are `file_complaint`,
`submit_response`, `chat`, `transition_case`, `authorise_document`,
`reject_document`, `schedule_hearing`, `extract_evidence`,
`synthesise_inquiry`, `draft_section_13_report`, and
`serve_complaint_to_respondent`. The compliance and audit ones are
`generate_annual_reports`, `compliance_dashboard`,
`transparency_dashboard`, `verify_audit_chain`, `export_audit_metadata`,
`whoami`, `get_chat_history`, and `clear_chat_history`. Three read-only
resources cover Section 9, Section 16, and the statutory deadline list.

The role exposed via MCP follows whichever user the token was minted for.
A complainant token only sees tools its role can use. An auditor token
can call `verify_audit_chain` but not `transition_case`. All of these
checks happen on the server, not in the MCP client.

## Paper artifacts

Three sets of files in the repo back specific sections of the paper:
the local-model sample appendix, a cross-model substitution check
against OpenAI, and the differential-privacy utility figure for the
Transparency Dashboard.

### Local-model samples (Qwen)

`tools/build_samples.py` runs 30 prompts (five per role) against the live
local model, takes the best of five attempts for each prompt, and writes
`samples.tex` in the repo root. The file is ready to `\input` into a
LaTeX paper as the role-by-role chat appendix. The stack must be up
(via `./run.sh up`) and the fixture case must be seeded.

    python -m tools.seed_samples              one-time fixture
    python -m tools.build_samples             ~27 min on M4 + Qwen3-30B-A3B
    python -m tools.build_samples auditor=P2  re-roll one prompt against cache

Picked replies are cached in `.run/samples_cache.json` so re-runs only
hit the model for prompts you have asked to re-roll. The output is
verbatim model text truncated for column width. The scoring policy
(length window, role-appropriate refusal markers, statute-citation
presence) lives in the same script.

For naming parity with the OpenAI files below, a byte-identical copy
of `samples.tex` is also kept at `samples_qwen.tex`. The original
`samples.tex` is the file the paper `\input`s; the `_qwen` copy exists
only to make it explicit which model produced which appendix when the
files sit side by side.

### Cross-model substitution check (OpenAI)

`tools/build_samples_openai.py` is the same harness but pointed at an
OpenAI chat-completions model and driven in-process via the FastAPI
test client, so the stack does not need to be running. The script
takes the model id, a filename-safe label, and an API key on the
command line:

    KEY=$(cat /path/to/your/openai-key-file)
    .venv/bin/python -m tools.build_samples_openai \
        --model gpt-3.5-turbo --label gpt-3.5-turbo --api-key "$KEY"
    .venv/bin/python -m tools.build_samples_openai \
        --model gpt-4o --label gpt-4o --api-key "$KEY"
    .venv/bin/python -m tools.build_samples_openai \
        --model gpt-5-chat-latest --label gpt-5-chat-latest --api-key "$KEY"

Each run produces two files: a per-model cache at
`.run/samples_cache_<label>.json` (gitignored) and a per-model
appendix at `samples_<label>.tex`. The per-model `.tex` file uses the
same column layout as the Qwen appendix, with the column header rewritten
to name the actual model. The seed fixture case must already be in
`data/reportitnow.db` (run `python -m tools.seed_samples` once first);
the harness reads the API key only from the `--api-key` flag, so no key
ever sits in a file the script knows about.

Three model files currently live in the repo: `samples_gpt-3.5-turbo.tex`,
`samples_gpt-4o.tex`, `samples_gpt-5-chat-latest.tex`. A wall-clock budget
of around four minutes per model and a handful of dollars in total
covers all three at five attempts per prompt.

The accompanying file `samples_commentary.tex` is the paper-facing prose
that summarises what shifts between the three OpenAI generations on the
same 30 prompts: statute-citation density rises monotonically across
generations, verbosity diverges sharply in the compliance role, the
regex output filter fires on exactly the same two prompts under every
model (the safety boundary is held by code, not by model restraint),
and the older models give correct-but-minimal procedural answers where
the newer ones add citation and context. The four per-model `.tex`
files are the repository evidence those claims trace back to; the
commentary is the only file intended for paper inclusion.

### Differential-privacy utility figure

`dp_utility.py` (at the repo root, deliberately outside `tools/` so the
paper-figure pipeline is one filename to find) is a standalone Monte
Carlo simulator for the Transparency Dashboard mechanism described in
Section 5 of the paper. It generates the figure and the deployment-band
table that appear in Appendix F (`app:dputility`) of the paper.

The script assumes the dashboard induces a 20x reporting lift over FY25
baselines and stress-tests the released vector against the projected
caseloads in four employer-size bands (long-tail, mid, large, mega).
For each true case count `n` along a logarithmic grid, it draws 20,000
realisations of the released `(tilde_n, tilde_rho, tilde_tau)` triple
using the Laplace mechanism at the paper's default privacy budget
(eps_c = eps_r = eps_tau = 0.5), with the same sensitivity bounds and
the same small-n suppression (n_min = 5) as the running system. The
true resolution rate is fixed at 70 percent and the true mean resolution
time at 65 days, both close to but more conservative than published
FY25 industry figures.

The script depends on `numpy` and `matplotlib`, which are not in the
main `requirements.txt` because they are figure-generation dependencies
rather than runtime ones. Install them once into the project venv before
running:

    .venv/bin/pip install numpy matplotlib
    .venv/bin/python dp_utility.py

The run writes `dp_utility.pdf` and `dp_utility.png` in the current
directory and prints the deployment-band 90 percent confidence
intervals to stdout. The PDF is the file the paper `\includegraphics`
imports. All parameters (epsilon values, suppression threshold, clamp
range L, number of Monte Carlo trials, the case-count grid, the band
labels) sit at the top of the file so the figure can be re-tuned without
touching the simulation loop.

## Layout

    fastapi_app/
      main.py              backend entrypoint
      config.py
      state_machine/       case transitions and intake gates
      audit_chain/         tamper-evident log writer and verifier
      deadline_engine/
      dp_engine/           transparency-dashboard noise + suppression + budget
      auth/                session and role guard
      llm/
        client.py          OpenAI-compatible call to Ollama, stub fallback
        filters.py         output sanitiser
      ai_components/       ca, ra, ia, rpa, cpa, ar (the six role-scoped runners)
      schemas/             typed input/output schemas
      routes/              cases, documents, hearings, ai, chat, dashboard, audit, auth
      prompts/v1/          versioned system prompts (CA.txt, CA_CHAT.txt, etc.)
      db/                  schema, ORM models, demo seeder

    chainlit_app/
      app.py               per-role routing, consent capture, role chooser
      auth/                Chainlit auth callback that forwards to the backend
      api_client.py
      handlers/            one handler per role, plus a shared chat helper
      ui/consent_modal.py

    mcp_server/
      server.py            MCP tool and resource definitions
      __main__.py          stdio or HTTP transport selection

    tests/
      invariants/  audit_chain/  dp/  scenarios/  chat/  adversarial/
      live_llm/            skipped unless a model endpoint is up

    tools/
      seed_samples.py            fixture case used by build_samples
      build_samples.py           runs 30 prompts against the local model and writes samples.tex
      build_samples_openai.py    same 30 prompts against an OpenAI model; per-model cache + .tex
      mint_mcp_token.py          log in as a demo user and print a token + Claude Desktop config

    samples.tex                  paper appendix — local-model (Qwen) chat samples
    samples_qwen.tex             byte-identical copy of samples.tex, kept for naming parity
    samples_gpt-3.5-turbo.tex    paper appendix — same prompts, gpt-3.5-turbo
    samples_gpt-4o.tex           paper appendix — same prompts, gpt-4o
    samples_gpt-5-chat-latest.tex   paper appendix — same prompts, gpt-5-chat-latest
    samples_commentary.tex       paper-facing prose comparing the three OpenAI generations

    dp_utility.py                Monte Carlo simulator for the Transparency Dashboard mechanism
    dp_utility.pdf, dp_utility.png   figure for paper Appendix F (generated, regenerable)

    deploy/                docker-compose, Dockerfiles, nginx.conf
    data/                  SQLite database (gitignored)
    .run/                  pid files, log files, sample caches per model (gitignored)

## Configuration

Settings live in `.env`. The variables that matter:

    LLM_ENDPOINT              default http://localhost:11434/v1
                              point at https://api.openai.com/v1 for OpenAI
    LLM_MODEL                 default qwen3:30b-a3b
                              e.g. gpt-4o or gpt-5-chat-latest for OpenAI
    LLM_API_KEY               default ollama (Ollama ignores it)
                              your OpenAI key when LLM_ENDPOINT is OpenAI
    LLM_PROVIDER              not normally set; set to "openai" only when running
                              the live test suite against OpenAI, so the suite's
                              readiness probe skips the Ollama-specific check
    LLM_STUB_MODE             set to 1 to force placeholder replies

    REPORTITNOW_DATABASE_URL  default sqlite:///./data/reportitnow.db
                              do not use DATABASE_URL. Chainlit 2.x reserves
                              that variable name for its own Postgres data layer.

    AUDIT_CHAIN_HMAC_KEY      rotate before any non-POC use
    CHAINLIT_AUTH_SECRET      auto-generated on first `up`

    DP_EPSILON_COUNT/RATE/TIME   0.5 each (privacy budget per metric)
    DP_WORKFORCE_FLOOR        50  (transparency dashboard fully suppressed below this)
    DP_MAX_RESOLUTION_DAYS    150

## Pointers

`samples.tex` is the role-by-role chat appendix the paper `\input`s.
It is generated by `tools/build_samples.py` against the local Qwen model.
Its byte-identical sibling `samples_qwen.tex` exists for naming parity
with the OpenAI files.

`samples_gpt-3.5-turbo.tex`, `samples_gpt-4o.tex`, and
`samples_gpt-5-chat-latest.tex` are the same 30 prompts replayed
against three OpenAI generations. They are generated by
`tools/build_samples_openai.py` and are evidence for the four findings
written up in `samples_commentary.tex`, which is the only one of the
cross-model files intended for paper inclusion.

`dp_utility.pdf` is Figure 1 of Appendix F. It is generated by
`dp_utility.py` and shows the released Transparency Dashboard vector
against true case count under the paper's default privacy budget.

`.run/live_llm_report.json` (gitignored) accumulates per-probe outcomes
across `live-test` runs; rotate to `.run/live_llm_report_<model>.json`
between runs to compare models. `.run/samples_cache_<label>.json`
(gitignored) holds the best-of-five picks for each `build_samples_openai`
run so re-runs against the same model only hit the API for prompts you
have asked to re-roll.
