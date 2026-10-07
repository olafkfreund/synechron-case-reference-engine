"""Try extraction models on real documents and print metrics only (never document text, quotes or values).

    python scripts/eval_extraction.py --docs ~/presale --models ollama_chat/qwen3:14b [--data-class confidential]

Runs the real code path (to_markdown, triage, extract.build) on each document, nothing is stored. The
data policy applies: a third-party model on confidential documents is refused (exit 2). --docs must be
outside the repository, so real documents cannot be committed by accident. Needs the database only to
check approvals for a third-party model on confidential documents. EXTRACT_MODEL_OPTIONS may not set
`destination` here (it would override the policy for every model in --models); in Docker use --network host.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

from app import extract, ingest
from app.llm import PolicyError, profile

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {".docx", ".pptx", ".pdf"}
SCALARS = ("title", "client_mention", "industry", "region", "engagement_type", "challenge", "solution",
           "duration_months", "team_size")
OLLAMA = "http://localhost:11434"


def ollama_ps(base: str) -> list[dict]:
    try:
        return httpx.get(f"{base}/api/ps", timeout=5).json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return []  # no Ollama there: nothing to unload or measure


def unload_all(base: str) -> None:
    for m in ollama_ps(base):
        httpx.post(f"{base}/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=60)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--docs", required=True, type=Path)
    ap.add_argument("--models", required=True, nargs="+")
    ap.add_argument("--data-class", default="confidential", choices=["confidential", "sanitised", "public"])
    a = ap.parse_args(argv)
    docs_dir = a.docs.expanduser().resolve()
    if docs_dir == ROOT or ROOT in docs_dir.parents:
        print("refused: --docs must be outside the repository", file=sys.stderr)
        return 2
    files = sorted(p for p in docs_dir.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES)
    if not files:
        print("no .docx/.pptx/.pdf documents found", file=sys.stderr)
        return 2
    inherited = os.environ.get("EXTRACT_MODEL_OPTIONS")
    if inherited and "destination" in json.loads(inherited):
        print("refused: EXTRACT_MODEL_OPTIONS may not set destination here", file=sys.stderr)
        return 2
    texts, failed = {}, False
    for p in files:
        try:
            texts[p] = ingest.to_markdown(p.read_bytes(), p.name)
        except Exception as e:  # noqa: BLE001 - type only: a message could echo document text
            print(f"{p.name[:30]} CONVERT_FAILED {type(e).__name__}")
            failed = True
    for model in a.models:
        os.environ["EXTRACT_MODEL"] = model
        if inherited:
            os.environ["EXTRACT_MODEL_OPTIONS"] = inherited
        elif model.startswith("ollama"):  # fresh per model: nothing carries over from the previous one
            os.environ["EXTRACT_MODEL_OPTIONS"] = json.dumps({"think": False, "num_ctx": 24576, "repeat_penalty": 1.05})
        else:
            os.environ.pop("EXTRACT_MODEL_OPTIONS", None)
        base = profile("EXTRACT_MODEL").get("api_base") or os.environ.get("OLLAMA_API_BASE") or OLLAMA
        if model.startswith("ollama"):
            unload_all(base)
        rows = []
        for p, text in texts.items():
            t = time.time()
            try:
                kind = ingest.triage_text(text, a.data_class).kind
                case = extract.build(text, a.data_class)
            except PolicyError as e:
                print(f"refused by the data policy: {e}", file=sys.stderr)
                return 2
            except Exception as e:  # noqa: BLE001 - type only: a message could echo document text
                print(f"{model} {p.name[:30]} FAILED {type(e).__name__}")
                failed = True
                continue
            filled = [n for n in SCALARS if getattr(case, n).value not in (None, "")]
            items = [getattr(case, n) for n in filled] + case.capabilities + case.tech_stack + case.outcomes
            sourced = sum(not i.unsourced for i in items)
            ps = ollama_ps(base) if model.startswith("ollama") else []
            vram = ps[0].get("size_vram", 0) / 1e9 if ps else 0
            secs = time.time() - t
            rows.append((sourced, len(items), secs, vram))
            print(f"{model} {p.name[:30]} kind={kind} sourced={sourced}/{len(items)} caps={len(case.capabilities)} "
                  f"tech={len(case.tech_stack)} outcomes={len(case.outcomes)} notes={case.needs_attention} "
                  f"{secs:.0f}s vram={vram:.1f}GB\n    fields: {filled}", flush=True)
        if rows:
            n = len(rows)
            print(f"== {model}: {n}/{len(files)} documents, mean sourced {sum(r[0] for r in rows) / n:.1f}, "
                  f"mean items {sum(r[1] for r in rows) / n:.1f}, mean {sum(r[2] for r in rows) / n:.0f}s, "
                  f"max vram {max(r[3] for r in rows):.1f}GB")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
