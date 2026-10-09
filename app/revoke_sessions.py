"""One-off: end every session of the given users (#47). AWS: run-task on the crawl task definition."""
import sys

from app import db
from app.main import cut_sessions


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m app.revoke_sessions <sub> [<sub> ...]", file=sys.stderr)
        return 2
    with db.connect() as conn:
        cut_sessions(conn, *argv)
    for sub in argv:
        print(f"revoked sessions of {sub}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
