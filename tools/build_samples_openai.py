"""Run the 30 sample prompts against an OpenAI model, in-process.

Mirrors tools/build_samples.py but:
  - Drives the FastAPI app via TestClient (no need to `./run.sh up`)
  - Targets an OpenAI chat-completions model selected by --model
  - Writes per-model cache + per-model samples_<label>.tex
  - Reuses the existing prompt set, scoring policy, and LaTeX renderer

Usage:
    KEY=$(tr -d '\n\r ' < ../conf)
    .venv/bin/python -m tools.build_samples_openai \\
        --model gpt-3.5-turbo --label gpt-3.5-turbo --api-key "$KEY"

The seed fixture case must already be in data/reportitnow.db (run
`python -m tools.seed_samples` once first).

Per-model artefacts:
    .run/samples_cache_<label>.json   cached best-of-N picks
    samples_<label>.tex               appendix-style LaTeX (repo evidence,
                                      not paper-included; the paper carries
                                      only samples_commentary.tex)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True,
                   help="OpenAI chat-completions model id (e.g. gpt-4o)")
    p.add_argument("--label", required=True,
                   help="Filename-safe slug used for cache + tex output")
    p.add_argument("--api-key", required=True)
    p.add_argument("--endpoint", default="https://api.openai.com/v1")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--attempts", type=int, default=5)
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--reroll", action="append", default=[],
                   help="role_key=P1,P3 to force re-run those prompts; "
                        "or `all` to ignore the cache entirely")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    # CRUCIAL: env must be set BEFORE any fastapi_app import — Settings()
    # is cached and reads env exactly once.
    os.environ["LLM_ENDPOINT"] = args.endpoint
    os.environ["LLM_MODEL"] = args.model
    os.environ["LLM_API_KEY"] = args.api_key
    os.environ["LLM_REQUEST_TIMEOUT_S"] = str(args.timeout)
    os.environ["LLM_STUB_MODE"] = "0"

    from fastapi.testclient import TestClient
    from fastapi_app import config as cfg
    cfg._settings = None  # force re-read with the env we just set
    from fastapi_app.main import app
    from fastapi_app.db import models as m
    from fastapi_app.db.session import get_session_factory

    # Honour --attempts by patching the module constant build_samples.best_of_n
    # reads. (Cleaner than re-implementing best_of_n locally.)
    import tools.build_samples as bs
    bs.ATTEMPTS_PER_PROMPT = args.attempts
    from tools.build_samples import ROLES, _login, best_of_n, _render_role_block

    # Find seeded fixture case
    with get_session_factory()() as db:
        case = db.execute(
            m.Case.__table__.select().order_by(m.Case.id.desc()).limit(1)
        ).first()
    case_id_root = case.id if case else None
    if case_id_root is None:
        print("No case in DB. Run `python -m tools.seed_samples` first.")
        sys.exit(1)
    print(f"Using fixture case_id={case_id_root}")

    cache_path = ROOT / ".run" / f"samples_cache_{args.label}.json"
    out_path = ROOT / f"samples_{args.label}.tex"

    rerolls: dict[str, set[int]] = {}
    reroll_all = False
    for spec in args.reroll:
        if spec == "all":
            reroll_all = True
            continue
        if "=" in spec:
            rk, ps = spec.split("=", 1)
            rerolls[rk] = {int(p.lstrip("P")) for p in ps.split(",") if p}

    cached: dict[str, list[dict]] = {}
    if cache_path.exists() and not args.no_cache and not reroll_all:
        try:
            cached = json.loads(cache_path.read_text())
            print(f"Loaded cache with roles: {list(cached.keys())}")
        except Exception:
            cached = {}

    results: dict[str, list[dict]] = {}
    start = time.time()
    client = TestClient(app)

    for role_key, role_cfg in ROLES.items():
        print(f"\n=== {role_cfg['title']} ({role_key}) ===")
        token = _login(client, *role_cfg["login"])
        case_id = case_id_root if role_cfg["use_case_id"] else None
        per_role: list[dict] = []
        cached_role = cached.get(role_key, [])
        for j, prompt in enumerate(role_cfg["prompts"], start=1):
            use_cached = (
                not reroll_all
                and j - 1 < len(cached_role)
                and j not in rerolls.get(role_key, set())
            )
            if use_cached:
                per_role.append(cached_role[j - 1])
                print(f"P{j}: {prompt.text}  [cached]")
                continue
            print(f"P{j}: {prompt.text}")
            picked, dbg = best_of_n(client, token, role_cfg["chat_role"],
                                    prompt, case_id)
            per_role.append(picked)
            print(f"  -> picked: len={len(picked.get('reply', ''))} dbg={dbg}",
                  flush=True)
        results[role_key] = per_role
        elapsed = time.time() - start
        print(f"  cumulative time: {elapsed:.0f}s", flush=True)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote cache → {cache_path}")

    # Render with model-aware header. _render_role_block hardcodes
    # "Qwen3-30B-A3B, best-of-{ATTEMPTS_PER_PROMPT}" in its column header;
    # swap that token after rendering.
    qwen_marker = f"Qwen3-30B-A3B, best-of-{args.attempts}"
    new_marker = f"{args.model}, best-of-{args.attempts}"
    blocks: list[str] = []
    for rk, role_cfg in ROLES.items():
        rendered = _render_role_block(rk, role_cfg, results[rk])
        rendered = rendered.replace(qwen_marker, new_marker)
        blocks.append(rendered)

    header = (
        f"% Auto-generated by tools/build_samples_openai.py for model {args.model}.\n"
        f"% Do not edit by hand — replies are verbatim output (truncated for\n"
        f"% length) from {args.model} via OpenAI, against the seed fixture case.\n"
        f"%\n"
        f"% Per-model outputs are repo evidence; the paper itself carries only\n"
        f"% the cross-model commentary in samples_commentary.tex.\n"
        f"\\section*{{Illustrative Role Interactions --- {args.model}}}\n"
        f"\\label{{app:samples_{args.label}}}\n\n"
    )
    out_path.write_text(header + "\n".join(blocks))
    print(f"Wrote {out_path}")
    print(f"Total time: {(time.time() - start) / 60:.1f} min")


if __name__ == "__main__":
    main()
