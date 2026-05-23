#!/usr/bin/env bash
# Stack orchestration. See README for full docs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/.venv"
RUN_DIR="$SCRIPT_DIR/.run"
FASTAPI_PID="$RUN_DIR/fastapi.pid"
CHAINLIT_PID="$RUN_DIR/chainlit.pid"
OLLAMA_PID="$RUN_DIR/ollama.pid"
FASTAPI_LOG="$RUN_DIR/fastapi.log"
CHAINLIT_LOG="$RUN_DIR/chainlit.log"
OLLAMA_LOG="$RUN_DIR/ollama.log"
MCP_PID="$RUN_DIR/mcp.pid"
MCP_LOG="$RUN_DIR/mcp.log"

mkdir -p "$RUN_DIR"

if [ -t 1 ]; then
    C_BOLD='\033[1m'; C_DIM='\033[2m'; C_GREEN='\033[32m'
    C_YEL='\033[33m'; C_RED='\033[31m'; C_RESET='\033[0m'
else
    C_BOLD=''; C_DIM=''; C_GREEN=''; C_YEL=''; C_RED=''; C_RESET=''
fi
say()  { printf "${C_BOLD}▸${C_RESET} %s\n" "$*"; }
ok()   { printf "${C_GREEN}✓${C_RESET} %s\n" "$*"; }
warn() { printf "${C_YEL}⚠${C_RESET} %s\n" "$*"; }
err()  { printf "${C_RED}✗${C_RESET} %s\n" "$*" >&2; }

# We don't `source .env` — secrets contain $-chars and other bytes that the
# shell would expand. Read literally and only export keys we use here.
load_env() {
    [ -f .env ] || return 0
    while IFS= read -r line; do
        case "$line" in ""|\#*) continue ;; esac
        local key="${line%%=*}"
        local val="${line#*=}"
        if [ "${val:0:1}" = '"' ] && [ "${val: -1}" = '"' ]; then
            val="${val:1:${#val}-2}"
        elif [ "${val:0:1}" = "'" ] && [ "${val: -1}" = "'" ]; then
            val="${val:1:${#val}-2}"
        fi
        case "$key" in
            LLM_ENDPOINT|LLM_MODEL|LLM_STUB_MODE|\
            REPORTITNOW_DATABASE_URL|\
            FASTAPI_HOST|FASTAPI_PORT|FASTAPI_BASE_URL)
                export "$key=$val"
                ;;
            # DATABASE_URL is reserved by Chainlit; let Python read it
            # from .env via REPORTITNOW_DATABASE_URL instead.
            DATABASE_URL) ;;
        esac
    done <.env
}

ensure_env_file() {
    if [ ! -f .env ]; then
        say "creating .env from .env.example"
        cp .env.example .env
        warn "edit .env and set AUDIT_CHAIN_HMAC_KEY before deploying anywhere real"
    fi
    # Older .envs used DATABASE_URL — Chainlit grabs that name for itself.
    if grep -qE "^DATABASE_URL=" .env && ! grep -qE "^REPORTITNOW_DATABASE_URL=" .env; then
        say "renaming DATABASE_URL → REPORTITNOW_DATABASE_URL in .env"
        python - <<'PY'
import pathlib, re
p = pathlib.Path(".env")
t = p.read_text()
t = re.sub(r"^DATABASE_URL=", "REPORTITNOW_DATABASE_URL=", t, flags=re.M)
p.write_text(t)
PY
    fi
}

ensure_chainlit_secret() {
    # Chainlit refuses to start password auth without this. Write it single-
    # quoted so the high-entropy bytes survive shell loading.
    if grep -E "^CHAINLIT_AUTH_SECRET='?[^']" .env >/dev/null 2>&1; then
        return 0
    fi
    say "generating CHAINLIT_AUTH_SECRET"
    local raw
    raw=$(python -m chainlit create-secret 2>/dev/null \
            | tr -d '\r' \
            | grep -E '^CHAINLIT_AUTH_SECRET=' \
            | head -1 \
            | sed -E 's/^CHAINLIT_AUTH_SECRET="?//; s/"?$//') || true
    if [ -z "${raw:-}" ]; then
        raw=$(python -c 'import secrets; print(secrets.token_urlsafe(64))')
    fi
    # Replace existing (possibly empty) line, or append.
    python - "$raw" <<'PY'
import pathlib, re, sys
secret = sys.argv[1]
quoted = "CHAINLIT_AUTH_SECRET='" + secret + "'"
p = pathlib.Path(".env")
text = p.read_text()
if re.search(r"^CHAINLIT_AUTH_SECRET=.*$", text, flags=re.M):
    text = re.sub(r"^CHAINLIT_AUTH_SECRET=.*$", quoted, text, flags=re.M)
else:
    if not text.endswith("\n"):
        text += "\n"
    text += quoted + "\n"
p.write_text(text)
PY
    ok "wrote CHAINLIT_AUTH_SECRET to .env"
}

# Avoid Python 3.14: Starlette 1.0's static-file path doesn't work on it yet
# (anyio/sniffio loop detection). 3.11–3.13 are fine.
pick_python() {
    for cand in python3.13 python3.12 python3.11 python3; do
        if command -v "$cand" >/dev/null 2>&1; then
            local v; v=$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
            case "$v" in
                3.11|3.12|3.13) echo "$cand"; return 0 ;;
            esac
        fi
    done
    err "no compatible Python (3.11–3.13) found. Install python3.13 (e.g. brew install python@3.13)."
    return 1
}

ensure_venv() {
    if [ ! -d "$VENV_DIR" ]; then
        local py; py=$(pick_python) || return 1
        say "creating venv at $VENV_DIR using $py"
        "$py" -m venv "$VENV_DIR"
    fi
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
    if ! python -c "import fastapi, chainlit" >/dev/null 2>&1; then
        say "installing requirements"
        pip install --quiet --upgrade pip
        pip install --quiet -r requirements.txt
    fi
}

ensure_db() {
    # Path inside DATABASE_URL ("sqlite:///./data/reportitnow.db" → ./data/...)
    local db_path="${DATABASE_URL#sqlite:///}"
    db_path="${db_path:-./data/reportitnow.db}"
    if [ ! -f "$db_path" ]; then
        say "bootstrapping demo DB at $db_path"
        python -m fastapi_app.db.bootstrap >/dev/null
        ok "seeded"
    else
        ok "DB already exists at $db_path"
    fi
}

endpoint_alive() {
    local url="${LLM_ENDPOINT:-http://localhost:11434/v1}"
    # Ollama serves /api/tags; LM Studio + vLLM serve /v1/models. Try both.
    local base="${url%/v1}"
    if curl -fsS -m 2 "$base/api/tags" >/dev/null 2>&1; then return 0; fi
    if curl -fsS -m 2 "$url/models"    >/dev/null 2>&1; then return 0; fi
    return 1
}

start_ollama_if_possible() {
    if endpoint_alive; then
        ok "LLM endpoint reachable at ${LLM_ENDPOINT:-http://localhost:11434/v1}"
        return 0
    fi
    if ! command -v ollama >/dev/null 2>&1; then
        warn "no LLM endpoint and 'ollama' CLI not installed — enabling stub mode"
        export LLM_STUB_MODE=1
        return 0
    fi
    say "starting 'ollama serve' in the background"
    nohup ollama serve >"$OLLAMA_LOG" 2>&1 &
    echo $! >"$OLLAMA_PID"
    # Wait up to 10s for the endpoint to come up.
    for _ in $(seq 1 20); do
        if endpoint_alive; then
            ok "Ollama up (pid $(cat "$OLLAMA_PID"))"
            return 0
        fi
        sleep 0.5
    done
    warn "Ollama did not become reachable; enabling stub mode"
    export LLM_STUB_MODE=1
}

pull_model_if_needed() {
    [ "${LLM_STUB_MODE:-0}" = "1" ] && return 0
    command -v ollama >/dev/null 2>&1 || return 0
    local model="${LLM_MODEL:-qwen3:30b-a3b}"
    if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$model"; then
        ok "Ollama model $model available"
        return 0
    fi
    # We deliberately do NOT auto-pull: the Qwen3 image is multi-GB and would
    # block 'up' for a long time. The LLM client falls back to stub mode on
    # any LLM error, so the POC still works end-to-end without the model.
    warn "Ollama model '$model' is not pulled — backend will run in stub mode."
    echo "       Pull when you have bandwidth:  ollama pull $model"
}

pid_alive() {
    local pidfile="$1"
    [ -f "$pidfile" ] || return 1
    local pid; pid=$(cat "$pidfile" 2>/dev/null || true)
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

start_fastapi() {
    if pid_alive "$FASTAPI_PID"; then
        ok "FastAPI already running (pid $(cat "$FASTAPI_PID"))"
        return 0
    fi
    say "starting FastAPI on ${FASTAPI_HOST:-127.0.0.1}:${FASTAPI_PORT:-8000}"
    nohup python -m uvicorn fastapi_app.main:app \
        --host "${FASTAPI_HOST:-127.0.0.1}" \
        --port "${FASTAPI_PORT:-8000}" \
        >"$FASTAPI_LOG" 2>&1 &
    echo $! >"$FASTAPI_PID"
    # Wait for /health.
    local base="${FASTAPI_BASE_URL:-http://127.0.0.1:8000}"
    for _ in $(seq 1 30); do
        if curl -fsS -m 1 "$base/health" >/dev/null 2>&1; then
            ok "FastAPI healthy (pid $(cat "$FASTAPI_PID"))"
            return 0
        fi
        sleep 0.5
    done
    err "FastAPI failed to come up; see $FASTAPI_LOG"
    return 1
}

start_chainlit() {
    if pid_alive "$CHAINLIT_PID"; then
        ok "Chainlit already running (pid $(cat "$CHAINLIT_PID"))"
        return 0
    fi
    say "starting Chainlit on 0.0.0.0:8080"
    nohup python -m chainlit run chainlit_app/app.py \
        --host 0.0.0.0 --port 8080 --headless \
        >"$CHAINLIT_LOG" 2>&1 &
    echo $! >"$CHAINLIT_PID"
    for _ in $(seq 1 30); do
        if curl -fsS -m 1 "http://127.0.0.1:8080/" >/dev/null 2>&1; then
            ok "Chainlit up (pid $(cat "$CHAINLIT_PID"))"
            return 0
        fi
        sleep 0.5
    done
    err "Chainlit failed to come up; see $CHAINLIT_LOG"
    return 1
}

stop_pidfile() {
    local pidfile="$1" name="$2"
    if ! pid_alive "$pidfile"; then
        rm -f "$pidfile"
        return 0
    fi
    local pid; pid=$(cat "$pidfile")
    say "stopping $name (pid $pid)"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || { rm -f "$pidfile"; ok "$name stopped"; return 0; }
        sleep 0.25
    done
    warn "$name did not exit, sending SIGKILL"
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$pidfile"
}

cmd_up() {
    ensure_env_file
    load_env
    ensure_venv
    ensure_chainlit_secret
    load_env   # reload to pick up the newly written secret
    ensure_db
    start_ollama_if_possible
    pull_model_if_needed
    start_fastapi
    start_chainlit
    printf "\n${C_BOLD}ReportItNow-POC is up.${C_RESET}\n"
    echo "  Chainlit UI : http://localhost:8080"
    echo "  FastAPI docs: http://localhost:${FASTAPI_PORT:-8000}/docs"
    echo "  Login       : complainant@demo.local / demo  (or any seeded role)"
    [ "${LLM_STUB_MODE:-0}" = "1" ] && warn "running in LLM stub mode (no real model)"
    printf "${C_DIM}Tail logs with: ./run.sh logs${C_RESET}\n"
}

cmd_down() {
    stop_pidfile "$MCP_PID"      MCP
    stop_pidfile "$CHAINLIT_PID" Chainlit
    stop_pidfile "$FASTAPI_PID"  FastAPI
    if [ -f "$OLLAMA_PID" ]; then
        stop_pidfile "$OLLAMA_PID" Ollama
    fi
    ok "all down"
}

cmd_status() {
    for name_pid in "FastAPI:$FASTAPI_PID" "Chainlit:$CHAINLIT_PID" \
                     "MCP:$MCP_PID" "Ollama:$OLLAMA_PID"; do
        local name="${name_pid%%:*}" pidfile="${name_pid##*:}"
        if pid_alive "$pidfile"; then
            printf "  ${C_GREEN}●${C_RESET} %-9s pid %s\n" "$name" "$(cat "$pidfile")"
        else
            printf "  ${C_DIM}○${C_RESET} %-9s stopped\n" "$name"
        fi
    done
}

cmd_logs() {
    local files=()
    [ -f "$FASTAPI_LOG" ]  && files+=("$FASTAPI_LOG")
    [ -f "$CHAINLIT_LOG" ] && files+=("$CHAINLIT_LOG")
    [ -f "$MCP_LOG" ]      && files+=("$MCP_LOG")
    [ ${#files[@]} -eq 0 ] && { warn "no logs yet"; return 0; }
    tail -n 50 -F "${files[@]}"
}

cmd_mcp() {
    load_env
    ensure_venv
    if pid_alive "$MCP_PID"; then
        ok "MCP already running (pid $(cat "$MCP_PID"))"
        return 0
    fi
    if [ -z "${REPORTITNOW_TOKEN:-}" ]; then
        warn "REPORTITNOW_TOKEN not set in env."
        echo "       Mint one with: python -m tools.mint_mcp_token <email>"
        echo "       Then export REPORTITNOW_TOKEN=... and rerun."
        return 1
    fi
    local transport="${2:-http}"
    case "$transport" in
        http|stdio) ;;
        *) err "unknown MCP transport: $transport (use http or stdio)"; return 1 ;;
    esac
    if [ "$transport" = "http" ]; then
        say "starting MCP server (streamable HTTP on :8765)"
        REPORTITNOW_API="${FASTAPI_BASE_URL:-http://127.0.0.1:8000}" \
        nohup python -m mcp_server --http >"$MCP_LOG" 2>&1 &
        echo $! >"$MCP_PID"
        ok "MCP up (pid $(cat "$MCP_PID")) — http://127.0.0.1:8765/mcp"
    else
        say "running MCP server in stdio mode (foreground; Ctrl-C to exit)"
        REPORTITNOW_API="${FASTAPI_BASE_URL:-http://127.0.0.1:8000}" \
        python -m mcp_server
    fi
}

cmd_test() {
    load_env
    ensure_venv
    # pytest.ini enforces `-m "not live_llm"` so live tests don't run here.
    python -m pytest tests/ -v
}

cmd_live_test() {
    load_env
    ensure_venv
    if ! endpoint_alive; then
        warn "LLM endpoint $LLM_ENDPOINT not reachable; live tests will skip"
    fi
    # -o overrides pytest.ini's addopts so we run ONLY the live_llm tests.
    python -m pytest tests/live_llm/ -v -m live_llm -o addopts=""
}

usage() {
    cat <<EOF
Usage: ./run.sh {up|down|restart|status|logs|test|live-test|mcp [http|stdio]}

  up               bootstrap (first run) + start FastAPI + Chainlit (+ Ollama if available)
  down             stop everything started by 'up' (incl. MCP)
  restart          down then up
  status           show which processes are running
  logs             tail FastAPI + Chainlit + MCP logs (Ctrl-C to exit)
  test             run the fast pytest suite (skips live LLM tests)
  live-test        run the live-LLM probes (needs a real LLM endpoint + model pulled)
  mcp [transport]  start the MCP front (transport: http (default) or stdio)
                   needs REPORTITNOW_TOKEN — mint with
                   python -m tools.mint_mcp_token <email>
EOF
}

case "${1:-}" in
    up)        cmd_up ;;
    down)      cmd_down ;;
    restart)   cmd_down; cmd_up ;;
    status)    cmd_status ;;
    logs)      cmd_logs ;;
    test)      cmd_test ;;
    live-test) cmd_live_test ;;
    mcp)       cmd_mcp "$@" ;;
    "")        usage; exit 1 ;;
    *)         err "unknown command: $1"; usage; exit 1 ;;
esac
