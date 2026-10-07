"""One-off task: apply the schema (and the app's database role) as the schema owner."""
from app import db


def main() -> None:
    db.init()
    print("schema applied", flush=True)


if __name__ == "__main__":
    main()
