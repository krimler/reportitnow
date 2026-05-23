"""Live-LLM test setup.

Every test in this directory is implicitly tagged @pytest.mark.live_llm via the
module-level pytestmark below, so the default `pytest` run (which uses
`-m "not live_llm"` via pytest.ini) skips them.

We also:
  - skip the whole module at collection time if the LLM endpoint isn't
    reachable or the model isn't pulled (so CI without a GPU doesn't fail)
  - flip LLM_STUB_MODE to 0 so the LLM client actually calls Ollama
  - run bootstrap_seed() once so the demo accounts exist in the test DB
  - log every probe's outcome to .run/live_llm_report.json for empirical record
"""
from __future__ import annotations

import json
import os
import pathlib

import httpx
import pytest


LLM_ENDPOINT = os.environ.get("LLM_ENDPOINT", "http://localhost:11434/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3:30b-a3b")


def pytest_collection_modifyitems(config, items):
    """Auto-mark every test collected under this directory as live_llm.

    Module-level `pytestmark` in conftest doesn't propagate to sibling test
    files — only this hook does. The fast suite (`addopts = -m "not live_llm"`
    in pytest.ini) skips everything tagged here.
    """
    here = str(pathlib.Path(__file__).parent)
    mark = pytest.mark.live_llm
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(mark)


def _endpoint_ready() -> tuple[bool, str]:
    base = LLM_ENDPOINT.rstrip("/")
    base = base[:-3] if base.endswith("/v1") else base
    try:
        r = httpx.get(f"{base}/api/tags", timeout=3)
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
        if LLM_MODEL not in names:
            return False, f"model {LLM_MODEL} not pulled; available: {names}"
        return True, "ready"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


_READY, _REASON = _endpoint_ready()


@pytest.fixture(scope="session", autouse=True)
def _live_llm_session():
    if not _READY:
        pytest.skip(
            f"live LLM not available: {_REASON}",
            allow_module_level=True,
        )
    # The parent conftest forced stub mode for the regular suite. We need real
    # LLM calls here.
    os.environ["LLM_STUB_MODE"] = "0"
    from fastapi_app import config as cfg
    cfg._settings = None
    # Seed the demo users so /auth/login works with the bootstrap emails.
    from fastapi_app.db.bootstrap import seed as bootstrap_seed
    bootstrap_seed()
    yield
    cfg._settings = None


@pytest.fixture(scope="session")
def report_path() -> pathlib.Path:
    p = pathlib.Path(".run/live_llm_report.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        p.unlink()  # fresh report per session
    return p


@pytest.fixture
def record_probe(report_path):
    """Append (test_id, attack, path, hits, reply_excerpt) per probe."""
    def _append(entry: dict):
        existing = []
        if report_path.exists():
            try:
                existing = json.loads(report_path.read_text())
            except Exception:
                existing = []
        existing.append(entry)
        report_path.write_text(json.dumps(existing, indent=2, default=str))
    return _append
