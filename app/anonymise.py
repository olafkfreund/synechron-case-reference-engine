import re
import unicodedata

from psycopg.rows import dict_row

from app import db

# Clients are dicts: name, aliases, anonymised_label, referenceable. Names are literals.
# Words inside a name may be joined by any spacing or dash ("Acme Bank", "Acme-Bank", "AcmeBank",
# "jane@acmebank.co.uk"). Boundaries are lookarounds on \w, not \b, so "C++ Ltd" still matches.
# Possessives need no special case: "Acme's" matches "Acme" and the "'s" stays.
# A comma joins words too, a word's last dot is optional and apostrophe variants are one ("Acme, Inc." = "Acme Inc")
# (#163), and a dot after a word is optional (#188).
_SEP = r"[\s,\-‐‑–—]"
_JOIN = _SEP + "*"
_APOS = "'’‘ʼ′"
_SIGNS = "™℠®"  # replaced before NFKC, which turns ™ into "TM" (#187)


def load_clients(conn=None) -> list[dict]:
    sql = "select id, name, aliases, anonymised_label, referenceable from clients"
    if conn:
        return conn.cursor(row_factory=dict_row).execute(sql).fetchall()
    with db.connect() as c:
        return load_clients(c)


def _no_format(s: str) -> str:
    """Drop format characters (Cf: soft hyphen, zero-width, BOM, bidi controls) (#125). Most have no glyph;
    the few that shape text (ZWJ/ZWNJ in emoji or Indic scripts, Arabic number signs) lose that shaping."""
    return "".join(ch for ch in s if unicodedata.category(ch) != "Cf")


def _visible(s: str) -> str:
    """NFKC, minus format characters. For matching only: NFKC rewrites 10⁶ as 106 (#165)."""
    return _no_format(unicodedata.normalize("NFKC", s))


def fold(s: str, me: str = "") -> str:
    """Case, accents and width folded ("Société" = "SOCIETE", "Straße" = "strasse", "İ" = "i"). Trademark signs become a
    space, so "Zorpale™ Bank" and "Acme®Zorpale Bank" still match (#187). Enclosing marks become `me`: "" joins
    "Z⃝orpale"; blocked() and scrub() also try " " for a mark between two words, "A⃝Zorpale Bank"."""
    s = "".join(" " if ch in _SIGNS else me if unicodedata.category(ch) == "Me" else ch for ch in s)  # Me: combining 0
    s = unicodedata.normalize("NFKD", _visible(s).casefold())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _names(c) -> list[str]:
    return [n.strip() for n in [c["name"], *c["aliases"]] if n.strip()]


def _loose(word: str) -> str:
    """An escaped word with an optional trailing dot and any apostrophe. Only for names with a word character:
    "." alone would become an empty match. Inner dots stay, or "U.S." would match "us" (#163)."""
    if word.endswith(r"\."):
        word += "?"
    return "".join(f"[{_APOS}]" if ch in _APOS else ch for ch in word)


def _pattern(name: str) -> re.Pattern:
    words = [w for w in re.split(_SEP + "+", name) if w] or [name]  # "-" alone stays literal, never empty
    loose = _loose if re.search(r"\w", name) else str
    join = r"\.?" + _JOIN if loose is _loose else _JOIN  # "St. James" = "St James"; the last word's dot stays text (#188)
    body = join.join(loose(re.escape(w)) for w in words)
    return re.compile(rf"(?<!\w){body}(?!\w)", re.I)


def _by_length(pairs):
    return sorted(pairs, key=lambda p: len(p[0]), reverse=True)  # "Acme Bank UK" before "Acme Bank"


def has_name(text: str, names: list[str]) -> bool:
    t = fold(text)
    return any(_pattern(fold(n)).search(t) for n in names)


def _replace_once(text: str, pairs) -> str:
    """One pass: match on fold(text) with blocked()'s patterns, replace the matching original characters."""
    folded, at = [], []  # at[j] = index in text of folded[j]; fold(ch) per char equals fold(text) (#166)
    for i, ch in enumerate(text):
        f = fold(ch)
        folded.append(f)
        at += [i] * len(f)
    folded = "".join(folded)
    spans = []
    for name, label in _by_length(pairs):
        for m in _pattern(fold(name)).finditer(folded):  # the patterns blocked() uses, on the text it sees
            if m.end() == m.start():
                continue
            s, e = at[m.start()], at[m.end() - 1] + 1
            while e < len(text) and not fold(text[e]):  # a trailing combining accent goes with the name
                e += 1
            if all(e <= s2 or s >= e2 for s2, e2, _ in spans):  # longer names came first and win
                spans.append((s, e, label))
    for s, e, label in sorted(spans, reverse=True):  # right to left, by slicing: a "\" in a label is text
        text = text[:s] + label + text[e:]
    return text


def apply(text: str, clients) -> str:
    """Replace names/aliases of non-referenceable clients, and referenceable names/aliases that contain one, with the client's anonymised label.

    Matches on fold(text) with blocked()'s patterns and replaces the matching original characters, so the rest keeps its
    case and accents. blocked() is still the fail-closed check that must run after it.
    """
    text = _no_format(unicodedata.normalize("NFC", text))  # NFC joins split accents; not NFKC, which turns 10⁶ into 106 (#165)
    pairs = [(n, c["anonymised_label"]) for c in clients if not c["referenceable"] for n in _names(c)]
    # one pattern for all protected names: folded text is lowercase, so re.I matches blocked() exactly
    prot = "|".join(_pattern(fold(n)).pattern for n, _ in pairs)
    contains = re.compile(prot, re.I).search if prot else lambda _: None
    pairs += [(n, c["anonymised_label"]) for c in clients if c["referenceable"] for n in _names(c) if contains(fold(n))]
    for _ in range(3):  # a label can re-form a name a longer hit hid ("Zorp Bank Ltd"); ponytail: 3 rounds suffice
        new = _replace_once(text, pairs)
        if new == text:
            break
        text = new
    return text


def blocked(text: str, clients) -> list[str]:
    """Non-referenceable names or aliases still present, matched on folded text (fails closed)."""
    ts = (fold(text), fold(text, " "))  # shortcut: apply() only replaces the "" reading, so "A⃝Zorpale" is withheld
    names = [n for c in clients if not c["referenceable"] for n in _names(c)]
    return sorted({n for n in names if any(_pattern(fold(n)).search(t) for t in ts)})


def scrub(text: str, clients) -> str:
    """Remove every client name, referenceable or not, for outgoing web queries.

    Returns folded text: case and accents do not matter to a search engine.
    """
    t = fold(text, " ")  # a mark between words is a separator here; a mark inside a word was missed before #187 too
    for name, _ in _by_length([(n, None) for c in clients for n in _names(c)]):
        t = _pattern(fold(name)).sub(" ", t)
    return " ".join(t.split())


def _key(s: str) -> str:
    return " ".join(re.sub(r"[\W_]+", " ", fold(s)).split())


def resolve(mention: str | None, clients) -> int | None:
    """The registry id whose name or alias is exactly this mention (folded), else None."""
    k = _key(mention or "")
    return next((c["id"] for c in clients if k and k in {_key(n) for n in _names(c)}), None)


def shown(name: str | None, label: str | None, referenceable: bool | None, linked: bool) -> str:
    """How a case's client is shown: its name only if linked and referenceable, its label if linked, else "a client"."""
    return name if linked and referenceable else (label if linked else "a client")


def unlisted(organisations: list[str], clients) -> list[str]:
    """Organisations named in a case that are not exactly a registered name or alias.

    Exact (folded) equality, not "contains": "Acme Insurance" must not hide behind alias "Acme".
    """
    known = {_key(n) for c in clients for n in _names(c)}
    return sorted({o.strip() for o in organisations if o.strip() and _key(o) not in known})
