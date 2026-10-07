"""Retrieval recall for the bid search: how often the expected documents are in the top 3 / top 20.

    python scripts/recall.py --groups g-docs [--file tests/recall.yaml] [--min 0.90] [--samples]

Format: see tests/recall.yaml. Exit code: 0 at or above --min (top-3 hit rate), 1 below it, 2 when
there is nothing to score. The LLM pick is deliberately not run: this measures retrieval.
"""
import argparse
import sys
from pathlib import Path

import yaml

from app import db
from app.main import User
from app.search import search


def identifiers(case_ids: list[int]) -> dict[int, set[str]]:
    with db.connect() as conn:
        rows = conn.execute("select c.id, d.external_id, d.title from cases c join documents d on d.id = c.document_id "
                            "where c.id = any(%s)", (case_ids,)).fetchall()
    return {i: {ext, title} for i, ext, title in rows}


def evaluate(queries: list[dict], user: User) -> list[dict]:
    results = []
    for q in queries:
        found = search(user, q["bid_text"], q.get("filters") or {})
        ids = identifiers([c["id"] for c in found])
        ranked = [ids.get(c["id"], set()) for c in found]
        want = set(q["expected"])
        hit = lambda n: any(want & r for r in ranked[:n])  # noqa: E731
        results.append({"id": q["id"], "top3": hit(3), "top20": hit(20), "expected": q["expected"], "returned": len(found)})
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", default=str(Path(__file__).resolve().parent.parent / "tests" / "recall.yaml"))
    ap.add_argument("--groups", required=True, help="comma-separated groups of the user the search runs as")
    ap.add_argument("--min", type=float, default=0.90, help="minimum top-3 hit rate")
    ap.add_argument("--samples", action="store_true", help="also score the entries marked sample: true")
    args = ap.parse_args(argv)
    entries = yaml.safe_load(Path(args.file).read_text())["queries"]
    queries = [q for q in entries if args.samples or not q.get("sample")]
    skipped = len(entries) - len(queries)
    if skipped:
        print(f"{skipped} sample entr{'y' if skipped == 1 else 'ies'} skipped (use --samples)")
    if not queries:
        print("nothing to score: add real queries to the file")
        return 2
    user = User("recall", "recall", frozenset(g.strip() for g in args.groups.split(",") if g.strip()), frozenset(["user"]))
    results = evaluate(queries, user)
    top3 = sum(r["top3"] for r in results) / len(results)
    top20 = sum(r["top20"] for r in results) / len(results)
    print(f"queries: {len(results)}  top-3: {top3:.0%}  top-20: {top20:.0%}  (minimum top-3: {args.min:.0%})")
    for r in results:
        if not r["top3"]:
            print(f"MISS {r['id']}: expected {r['expected']}, {'in top 20 only' if r['top20'] else 'not in top 20'}, {r['returned']} returned")
    return 0 if top3 >= args.min else 1


if __name__ == "__main__":
    sys.exit(main())
