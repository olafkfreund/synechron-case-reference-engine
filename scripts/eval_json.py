"""Count how the model's JSON replies fare, per call and mode. Counts only: never reply text, quotes or errors (#245).

    python scripts/eval_json.py --research-id 16 --brief-id 3 --calls claims,brief --n 20 --mode json

Run it inside the worker container (DATABASE_URL, DRAFT_MODEL). Reads the stored pages of a research row and the stored
quotes of a brief, writes nothing to the database. --mode replaces the DRAFT_MODEL mode for this process only.
Output per call: success, echo (failed after the retry), other (invalid or failed otherwise), retries (echoes seen),
mean claims (or themes) per successful reply, mean seconds.
"""
import argparse
import json
import os
import time
import uuid

from app import anonymise, brief, db, llm, research


def measure(call, n: int) -> dict:
    """call() makes one complete_json call and returns its reply. Only classes and counts leave this function."""
    r = {"success": 0, "echo": 0, "other": 0, "retries": 0, "size": 0, "seconds": 0.0}
    for _ in range(n):
        before, t = llm.ECHO_RETRIES, time.monotonic()
        try:
            reply = call()
            r["success"] += 1
            r["size"] += len(getattr(reply, "claims", None) or getattr(reply, "themes", None) or [])
        except Exception as e:  # noqa: BLE001 - the message can quote the document; keep the class only
            r["echo" if "schema-echo retry" in str(e) else "other"] += 1
        r["seconds"] += time.monotonic() - t
        r["retries"] += llm.ECHO_RETRIES - before
    return r


def nonce(user: str) -> str:
    return f"{user}\n<!-- {uuid.uuid4().hex} -->"  # defeats the prompt cache between runs


def claims_call(research_id: int):
    with db.connect() as c:
        query, results = c.execute("select query, results from research where id=%s", (research_id,)).fetchone()
    pages = results.get("pages", [])
    seen, errs = [], []
    real = research.complete_json

    def spy(alias, system, user, model_cls, **kw):
        try:
            reply = real(alias, system, nonce(user), model_cls, **kw)
        except Exception as e:
            errs.append(e)  # extract_claims swallows the error; keep it so measure() can classify it
            raise
        seen.append(reply)
        return reply
    research.complete_json = spy

    def call():
        seen.clear(), errs.clear()
        note = research.extract_claims(query, pages)[1]
        if errs:
            raise errs[0]
        if not seen:
            raise ValueError(note or "no reply")
        return seen[0]
    return call


def brief_call(brief_id: int):
    with db.connect() as c:
        quotes = c.execute("select quotes from briefs where id=%s", (brief_id,)).fetchone()[0]
        clients = anonymise.load_clients(c)
    texts = brief.quote_texts(quotes[:brief.MAX_QUOTES], clients)
    msg = "<quotes>\n" + "\n".join(f"[{i}] {t}" for i, t in enumerate(texts, 1)) + "\n</quotes>"
    return lambda: llm.complete_json("DRAFT_MODEL", brief.BRIEF_SYSTEM, nonce(msg), brief.Brief, data_class="public", shape_hint=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--research-id", type=int)
    ap.add_argument("--brief-id", type=int)
    ap.add_argument("--calls", default="claims,brief")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--mode", choices=["json", "schema"])
    a = ap.parse_args(argv)
    if a.mode:
        os.environ["DRAFT_MODEL_OPTIONS"] = json.dumps({**llm.profile("DRAFT_MODEL"), "mode": a.mode})
    makers = {"claims": (a.research_id, claims_call), "brief": (a.brief_id, brief_call)}
    for name in a.calls.split(","):
        if name not in makers or makers[name][0] is None:
            print(f"{name}: needs --{'research' if name == 'claims' else 'brief'}-id (calls: claims, brief)")
            return 2
        r = measure(makers[name][1](makers[name][0]), a.n)
        ok = r["success"] or 1
        print(f"{name} mode={a.mode or 'default'} n={a.n} success={r['success']} echo={r['echo']} other={r['other']} "
              f"retries={r['retries']} per_reply={r['size'] / ok:.1f} seconds={r['seconds'] / a.n:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
