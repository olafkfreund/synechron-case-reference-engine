import re
import unicodedata

from psycopg.rows import dict_row

from app import db

# Clients are dicts: name, aliases, anonymised_label, referenceable. Names are literals.
# Words inside a name may be joined by any spacing or dash ("Acme Bank", "Acme-Bank", "AcmeBank",
# "jane@acmebank.co.uk"). Boundaries are lookarounds on \w, not \b, so "C++ Ltd" still matches.
# Possessives need no special case: "Acme's" matches "Acme" and the "'s" stays.
_JOIN = r"[\s\-‐‑–—]*"


def load_clients(conn=None) -> list[dict]:
    sql = "select id, name, aliases, anonymised_label, referenceable from clients"
    if conn:
        return conn.cursor(row_factory=dict_row).execute(sql).fetchall()
    with db.connect() as c:
        return load_clients(c)


def fold(s: str) -> str:
    """Case, accents and width folded ("Société" = "SOCIETE", "Straße" = "strasse", "İ" = "i")."""
    s = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", s).casefold())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _names(c) -> list[str]:
    return [n.strip() for n in [c["name"], *c["aliases"]] if n.strip()]


def _pattern(name: str) -> re.Pattern:
    body = _JOIN.join(re.escape(w) for w in name.split())
    # short all-caps aliases ("ACB") match case-sensitively so they don't rewrite ordinary words
    flags = 0 if name.isupper() and len(name) <= 5 else re.I
    return re.compile(rf"(?<!\w){body}(?!\w)", flags)


def _by_length(pairs):
    return sorted(pairs, key=lambda p: len(p[0]), reverse=True)  # "Acme Bank UK" before "Acme Bank"


def has_name(text: str, names: list[str]) -> bool:
    t = fold(text)
    return any(_pattern(fold(n)).search(t) for n in names)


def apply(text: str, clients) -> str:
    """Replace names/aliases of non-referenceable clients with their anonymised label.

    Best effort on the text as written; blocked() is the fail-closed check that must run after it.
    """
    text = unicodedata.normalize("NFKC", text)
    pairs = [(n, c["anonymised_label"]) for c in clients if not c["referenceable"] for n in _names(c)]
    for name, label in _by_length(pairs):
        text = _pattern(unicodedata.normalize("NFKC", name)).sub(label, text)
    return text


def blocked(text: str, clients) -> list[str]:
    """Non-referenceable names or aliases still present, matched on folded text (fails closed)."""
    t = fold(text)
    names = [n for c in clients if not c["referenceable"] for n in _names(c)]
    return sorted({n for n in names if _pattern(fold(n)).search(t)})


def scrub(text: str, clients) -> str:
    """Remove every client name, referenceable or not, for outgoing web queries.

    Returns folded text: case and accents do not matter to a search engine.
    """
    t = fold(text)
    for name, _ in _by_length([(n, None) for c in clients for n in _names(c)]):
        t = _pattern(fold(name)).sub(" ", t)
    return " ".join(t.split())


def _key(s: str) -> str:
    return " ".join(re.sub(r"[\W_]+", " ", fold(s)).split())


def resolve(mention: str | None, clients) -> int | None:
    """The registry id whose name or alias is exactly this mention (folded), else None."""
    k = _key(mention or "")
    return next((c["id"] for c in clients if k and k in {_key(n) for n in _names(c)}), None)


def unlisted(organisations: list[str], clients) -> list[str]:
    """Organisations named in a case that are not exactly a registered name or alias.

    Exact (folded) equality, not "contains": "Acme Insurance" must not hide behind alias "Acme".
    """
    known = {_key(n) for c in clients for n in _names(c)}
    return sorted({o.strip() for o in organisations if o.strip() and _key(o) not in known})
