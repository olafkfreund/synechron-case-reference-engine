import re
from dataclasses import dataclass, field
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

T = TypeVar("T")

# Word emits curly quotes, typographic dashes, soft hyphens and zero-width spaces;
# fold them so a quote copied from the markdown still matches.
_FOLD = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    "…": "...", "­": None, "​": None,
})
MIN_QUOTE_WORDS = 4  # shorter quotes ("the") match anywhere unless they contain the value


def _norm(s: str) -> str:
    return " ".join(s.translate(_FOLD).casefold().split())


def quote_in(text: str, quote: str) -> bool:
    q = _norm(quote)
    if not q:
        return False
    # also try with PDF line-break hyphenation removed ("trans-\nformation")
    return q in _norm(text) or q in _norm(re.sub(r"-[ \t]*\n\s*", "", text))


_MD = [(re.compile(p), r) for p, r in (
    (r"!?\[([^\]\[]*)\]\([^)]*\)", r"\1"),       # [text](url), ![alt](url)
    (r"\[(?!\d+\])([^\]\[]+)\]\[[^\]\[]*\]", r"\1"),  # [text][ref]; "[1][2]" citations stay
    (r"<(https?://[^>\s]+)>", r"\1"),              # <url>
    (r"\*\*|__|`", ""),                           # strong, code (also inside a word: **Zor**pale)
    (r"(?<!\w)([*_])(?=\S)(.+?)(?<=\S)\1(?!\w)", r"\2"),  # single * or _ around words; snake_case stays
    (r"(?m)^[ \t]*(?:#+|>+)[ \t]*", ""),             # heading and blockquote markers
    (r'""(.+?)""', r'"\1"'),
)]
_WRAP = {'"': '"', "\u201c": "\u201d"}


def fence(code: str) -> str:
    """A code fence one backtick longer than any run inside, so the code cannot close it early."""
    ticks = "`" * max(3, max((len(r) for r in re.findall(r"`+", code)), default=0) + 1)
    return f"{ticks}\n{code.strip(chr(10))}\n{ticks}"


def plain(s: str) -> str:
    """Markdown to plain text for display and render only; the stored quote and quote_in() stay verbatim.
    Never for a technical note: code must stay exact."""
    if len(s) > 2000:  # shortcut: a quote is ~80 words; longer input skips the Markdown rules (never cut: a cut could split a name)
        return " ".join(s.split())
    for _ in range(5):  # until the links stop changing, so [[x](y)](javascript:z) cannot leave a link behind
        t = _MD[1][0].sub(_MD[1][1], _MD[0][0].sub(_MD[0][1], s))
        if t == s:
            break
        s = t
    for rx, r in _MD[2:]:
        s = rx.sub(r, s)
    s = " ".join(s.split())
    if len(s) > 1 and _WRAP.get(s[0]) == s[-1] and s[0] not in s[1:-1] and s[-1] not in s[1:-1]:
        s = s[1:-1].strip()
    return s


# a comma is dropped only as a thousands separator (last group of 3 digits): "1,500" is 1500, but
# "1,5" stays as written, so it can't license 15 (#44); every other token is read exactly as main did
_THOUSANDS = re.compile(r"\d+(?:,\d+)*,\d{3}(?:\.\d+)?")


def numbers(s: object) -> set[str]:
    return {n.replace(",", "") if _THOUSANDS.fullmatch(n) else n
            for n in re.findall(r"\d+(?:[.,]\d+)*", "" if s is None else str(s))}


def sourced(value: object, quote: str, text: str) -> bool:
    """True when the quote is in the document and backs the value it is attached to."""
    if not quote_in(text, quote):
        return False
    if not numbers(value) <= numbers(quote):  # "12 to 1 days" against "12 days to 3 days"
        return False
    return len(_norm(quote).split()) >= MIN_QUOTE_WORDS or _norm(str(value)) in _norm(quote)


class _Model(BaseModel):
    # strict structured outputs require additionalProperties: false on every object
    model_config = ConfigDict(extra="forbid")


class Sourced(_Model, Generic[T]):
    value: T | None = None
    source_quote: str = ""
    # set by extract.py only; hidden from the LLM schema so the model cannot vouch for itself
    unsourced: SkipJsonSchema[bool] = False
    # the member document this quote is from, in a merged case (#55); never from the LLM
    document_id: SkipJsonSchema[int | None] = None
    by_reviewer: SkipJsonSchema[bool] = False  # a reviewer's own text, no quote (#198); never from the LLM


class Outcome(_Model):
    metric: str
    value: str
    source_quote: str = ""
    unsourced: SkipJsonSchema[bool] = False
    document_id: SkipJsonSchema[int | None] = None  # see Sourced


PLACEHOLDER_METRICS = {"metric", "outcome", "result", "value"}


def vague_metric(o: Outcome) -> bool:
    """No usable metric name: empty, a placeholder word, or a copy of the value (#85)."""
    m = o.metric.strip().lower()
    return not m or m in PLACEHOLDER_METRICS or m == o.value.strip().lower()


class Period(_Model):
    start: str | None = None
    end: str | None = None
    source_quote: str = ""
    unsourced: SkipJsonSchema[bool] = False
    document_id: SkipJsonSchema[int | None] = None  # see Sourced


class Diagram(_Model):
    """A reviewer-uploaded image (#200); written by the diagram worker, never by the LLM."""
    id: int
    caption: str
    status: Literal["pending", "ready", "rejected"]
    reason: str = ""
    width: int = 0
    height: int = 0
    image_id: int | None = None  # the document_images candidate it came from (#212)
    research_image_id: int | None = None  # the research_images row it came from (#231): attached once per case
    credit: str = ""
    rights_by: str = ""  # who confirmed the right to use a web image, when and on what basis (#231): each attach keeps its own record
    rights_at: str = ""
    rights_basis: str = ""  # "Image: <title>, <host>, <url>, retrieved <date>" for a web image whose rights a reviewer confirmed (#231)


class Prose(_Model):
    """A written section built from the points; `flags` lists what the checker could not source."""
    text: str = ""
    by_reviewer: bool = False
    flags: list[str] = []


Zone = Literal["channels", "experience", "integration", "core", "data", "platform", "external"]


class ArchComponent(_Model):
    id: str
    name: str = Field(max_length=40)
    role: str = Field("", max_length=70)
    zone: Zone
    kind: Literal["user", "app", "service", "queue", "store", "external", "platform"]
    cite: str = ""  # a numbered point such as "solution 2"


class ArchFlow(_Model):
    src: str
    dst: str
    label: str = Field("", max_length=40)
    mode: Literal["api", "event", "batch", "file", "ui", "data"]
    cite: str = ""


class ArchCallout(_Model):
    text: str = Field(max_length=60)
    outcome: int  # index into ReferenceCase.outcomes


class Architecture(_Model):
    """The solution architecture diagram (#246); built by app/archdiagram.py inputs, never by the LLM schema."""
    title: str = Field("Solution architecture", max_length=80)
    components: list[ArchComponent] = []
    flows: list[ArchFlow] = []
    callouts: list[ArchCallout] = []
    by_reviewer: bool = False
    flags: list[str] = []

    @model_validator(mode="after")
    def _caps_and_endpoints(self) -> "Architecture":
        # trimmed, never rejected (like summary); a component with no cite and no tech match is dropped by check_architecture
        self.components = self.components[:12]
        ids = {c.id for c in self.components}
        self.flows = [f for f in self.flows if f.src in ids and f.dst in ids][:14]
        self.callouts = self.callouts[:3]
        return self

    def text(self) -> str:
        return " ".join([self.title, *(f"{c.name} {c.role}" for c in self.components),
                         *(f.label for f in self.flows), *(c.text for c in self.callouts)])


class ReferenceCase(_Model):
    title: Sourced[str]
    client_mention: Sourced[str] = Sourced[str]()  # raw name as written; client_id is resolved later
    industry: Sourced[str] = Sourced[str]()
    region: Sourced[str] = Sourced[str]()
    engagement_type: Sourced[str] = Sourced[str]()
    challenge: Sourced[str] = Sourced[str]()
    solution: Sourced[str] = Sourced[str]()
    implementation: Sourced[str] = Sourced[str]()
    configuration: Sourced[str] = Sourced[str]()
    lessons_learned: Sourced[str] = Sourced[str]()
    capabilities: list[Sourced[str]] = []
    tech_stack: list[Sourced[str]] = []
    tech_references: list[Sourced[str]] = []  # standards, protocols, interfaces and versioned products, as written
    outcomes: list[Outcome] = []
    # every point the document makes per POINT_FIELDS field, beyond the one-line headline above
    points: SkipJsonSchema[dict[str, list[Sourced[str]]]] = {}
    prose: SkipJsonSchema[dict[str, Prose]] = {}  # written from the points by app/writeup.py; never read back by the LLM
    duration_months: Sourced[int] = Sourced[int]()
    team_size: Sourced[int] = Sourced[int]()
    period: Period = Period()
    # synthesised by the LLM, so no verbatim quote; its numbers must come from sourced quotes
    summary: str = Field("", description="At most 80 words. Use only numbers that appear in the source quotes.")
    # every organisation the document names, compared with the client registry at review time
    # (comes free with extraction, so viewing a case costs no LLM call); never searched or rendered
    organisations: list[str] = Field([], description="Every company, bank or other organisation named in the document, as written.")
    # reviewer notes set by extract.py only (hidden from the LLM, like `unsourced`)
    needs_attention: SkipJsonSchema[list[str]] = []
    diagrams: SkipJsonSchema[list[Diagram]] = []
    architecture: SkipJsonSchema[Architecture | None] = None
    dismissed_images: SkipJsonSchema[list[int]] = []  # document_images candidates the reviewer turned down (#212)
    basis: SkipJsonSchema[Literal["delivered", "engagement", "capability"]] = "delivered"
    extraction_version: SkipJsonSchema[int] = 1  # bump EXTRACTION_VERSION when extraction output changes
    basis_reason: SkipJsonSchema[str] = ""  # "executed contract" | "source marked executed"

    @field_validator("summary")
    @classmethod
    def _max_80_words(cls, v: str) -> str:
        if len(v.split()) > 80:
            return " ".join(v.split()[:80])  # trimmed, never rejected
        return v

    def quotes(self) -> list[str]:
        items = [self.title, self.client_mention, self.industry, self.region, self.engagement_type,
                 self.challenge, self.solution, self.implementation, self.configuration,
                 self.lessons_learned, *self.capabilities, *self.tech_stack, *self.tech_references,
                 *(p for ps in self.points.values() for p in ps),
                 self.duration_months, self.team_size, *self.outcomes, self.period]
        # an unsourced quote must not vouch for summary numbers
        return [i.source_quote for i in items if i.source_quote and not i.unsourced]

    def summary_sourced(self) -> bool:
        """Every number in the summary appears in some source quote (else it launders invented numbers)."""
        return numbers(self.summary) <= set().union(*map(numbers, self.quotes()))

    def search_text(self) -> str:
        """Field values only (no quotes, keys, or client name) for cases.search_text."""
        parts = [self.title.value, self.industry.value, self.region.value,
                 self.engagement_type.value, self.challenge.value, self.solution.value,
                 self.implementation.value, self.configuration.value, self.lessons_learned.value,
                 *(c.value for c in self.capabilities), *(t.value for t in self.tech_stack), *(t.value for t in self.tech_references),
                 *(p.value for ps in self.points.values() for p in ps),
                 *(f"{o.metric} {o.value}" for o in self.outcomes), self.summary,
                 *(p.text for p in self.prose.values()),
                 self.architecture.text() if self.architecture else ""]
        return " ".join(p for p in parts if p)


def llm_schema(model: type[BaseModel] = ReferenceCase) -> dict:
    """JSON schema for structured outputs: pydantic defaults stripped (they leak hidden fields)."""
    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k != "default"}
        return [strip(v) for v in o] if isinstance(o, list) else o
    return strip(model.model_json_schema())


class Item(_Model):
    """One fact as the model states it; code assembles the strict ReferenceCase from a list of these."""
    # lenient: a stray key or a number for a text never fails the whole reply
    # "required": enforced-schema mode lets a model omit any optional key, and then it returns nothing
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True,
                              json_schema_extra={"additionalProperties": False, "required": ["field", "value", "quote"]})
    field: str = ""
    value: str = ""
    quote: str = ""

    @field_validator("field", "value", "quote", mode="before")
    @classmethod
    def _null_is_empty(cls, v):  # small models often emit null; one null must not fail the whole reply
        return "" if v is None else v


class Ref(_Model):
    """One technical reference as the references pass states it; lenient like Item."""
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True,
                              json_schema_extra={"additionalProperties": False, "required": ["value", "quote"]})
    value: str = ""
    quote: str = ""

    @field_validator("value", "quote", mode="before")
    @classmethod
    def _null_is_empty(cls, v):
        return "" if v is None else v


class References(_Model):
    model_config = ConfigDict(json_schema_extra={"required": ["items"]})
    items: list[Ref] = []


class Extraction(_Model):
    model_config = ConfigDict(json_schema_extra={"required": ["items", "summary"]})
    items: list[Item] = []
    summary: str = Field("", description="At most 80 words. Use only numbers that appear in the quotes.")


_TEXTS = ("title", "client_mention", "industry", "region", "engagement_type", "challenge", "solution",
          "implementation", "configuration", "lessons_learned")
POINT_FIELDS = ("challenge", "solution", "implementation", "configuration")
MAX_POINTS = 12
_INTS = ("duration_months", "team_size")
MAX_INT_CHARS = 12  # "1,000,000" fits; a longer run is a runaway reply, not a count (#131)
FIELDS = (*_TEXTS, *_INTS, "period_start", "period_end", "capability", "technology", "outcome", "organisation")


def assemble(x: Extraction) -> tuple[ReferenceCase, list[str]]:
    """Build the case from flat items. Malformed or unknown items are skipped and counted."""
    c = ReferenceCase(title=Sourced[str]())
    period, bad = Period(), 0
    for it in x.items:
        f, v, q = it.field, it.value.strip(), it.quote.strip()
        if not v:
            bad += 1
        elif f in _INTS:
            # the first number only: joining digits would turn "18 months to 2 years" into an invented 182
            first = re.search(r"\d[\d,]*", v)
            if not first or len(first.group()) > MAX_INT_CHARS:  # int() would raise past 4300 digits (#131)
                bad += 1
            elif getattr(c, f).value is None:  # first value wins
                setattr(c, f, Sourced[int](value=int(first.group().replace(",", "")), source_quote=q))
        elif f in _TEXTS:
            if not getattr(c, f).value:
                setattr(c, f, Sourced[str](value=v, source_quote=q))
            pts = c.points.setdefault(f, []) if f in POINT_FIELDS else None
            if pts is not None and len(pts) < MAX_POINTS and _norm(v) not in {_norm(p.value) for p in pts}:
                pts.append(Sourced[str](value=v, source_quote=q))
        elif f in ("period_start", "period_end"):
            if getattr(period, f[7:]) is None:
                setattr(period, f[7:], v)
                period.source_quote = period.source_quote or q
        elif f == "capability":
            c.capabilities.append(Sourced[str](value=v, source_quote=q))
        elif f == "technology":
            c.tech_stack.append(Sourced[str](value=v, source_quote=q))
        elif f == "outcome":
            metric, sep, val = v.partition(":")
            c.outcomes.append(Outcome(metric=metric.strip() if sep else "",
                                      value=(val if sep else v).strip(), source_quote=q))
        elif f == "organisation":
            if v not in c.organisations:
                c.organisations.append(v)
        else:
            bad += 1
    c.period = period
    notes = [f"{bad} malformed or unknown item(s) skipped"] if bad else []
    words = x.summary.split()
    if len(words) > 80:
        notes.append("summary trimmed to 80 words")
    c.summary = " ".join(words[:80])  # assignment skips the validator
    return c, notes


# --- the architecture check (#246) ---------------------------------------------------------------
_GENERIC = frozenset("the and for with service services system systems layer platform engine store app api portal queue hub".split())
DEFAULT_TITLE = "Solution architecture"
CITE_POINTS = ("solution", "implementation", "configuration")


@dataclass
class Evidence:
    """What an architecture may cite: label -> (value, quote), the technology text, every quote, and the outcome quotes (None = unsourced)."""
    cites: dict[str, tuple[str, str]]
    tech: str = ""
    quotes: list[str] = field(default_factory=list)
    outcomes: list[str | None] = field(default_factory=list)
    delivered: bool = False  # callouts are allowed
    strict: bool = False  # prune_architecture drops a row whose cite is no actual source (a brief has no technology to fall back on)

    def quote(self, cite: str) -> str | None:
        """The sourced quote a cite such as "solution 2" or "source 3" names, else None."""
        kind, _, n = cite.strip().lower().partition(" ")
        if not n.isdigit() or int(n) < 1:
            return None
        hit = self.cites.get(f"{kind} {int(n)}")
        return hit[1] if hit else None


def evidence_of_case(case: "ReferenceCase") -> Evidence:
    cites = {f"{f} {i}": (p.value, p.source_quote) for f in CITE_POINTS for i, p in enumerate(case.points.get(f, []), 1)
             if p.source_quote and not p.unsourced}
    cites |= {f"reference {i}": (r.value, r.source_quote) for i, r in enumerate(case.tech_references, 1) if r.source_quote and not r.unsourced}
    return Evidence(cites, " ".join(_tech_values(case)), case.quotes(), [None if o.unsourced else o.source_quote for o in case.outcomes],
                    case.basis == "delivered")


def evidence_of_quotes(quotes: list[str]) -> Evidence:
    """A brief's numbered quotes, cited as "source 1", "source 2"..."""
    return Evidence({f"source {i}": (q, q) for i, q in enumerate(quotes, 1) if q}, "", [q for q in quotes if q], strict=True)


def _ev(src) -> Evidence:
    return src if isinstance(src, Evidence) else evidence_of_case(src)


def _words(s: str) -> set[str]:
    return {w for w in (x.rstrip(".-") for x in re.findall(r"[a-z0-9][a-z0-9.+#-]*", _norm(s))) if len(w) >= 3 and w not in _GENERIC}


def _tech_values(case: "ReferenceCase") -> list[str]:
    return [t.value for t in (*case.tech_stack, *case.tech_references) if t.value and not t.unsourced]


def _tokens(s: str) -> set[str]:
    return {w.rstrip(".-") for w in re.findall(r"[a-z0-9][a-z0-9.+#-]*", _norm(s))}


def _known(token: str, vocab: set[str]) -> bool:
    """A word the vocabulary uses, in any form: "validation" for "validates" (five letters shared), exact for short words."""
    t = token.rstrip(".-").casefold()
    return any(t == v or (len(t) >= 5 and len(v) >= 5 and t[:5] == v[:5]) for v in vocab)


# words a component name may use with no quote behind them: they name a kind of part, not a product
ARCH_VOCAB = frozenset("""api apis cluster clusters topic topics job jobs table tables bucket worker workers function application applications exception exceptions view views service services system systems layer platform engine store app apps portal queue hub bus event events
data database web mobile gateway adapter adapters console ledger scheduler cache broker registry monitor monitoring
gateway engine processor pipeline lake warehouse server client channel channels connector connectors interface interfaces
module manager management workflow orchestrator router repository storage identity auth authentication core
integration reporting analytics dashboard notification notifications messaging stream streaming batch file files
user users customer customers staff internal external legacy cloud network security backup""".split())


# ordinary words that open a role, label or callout ("Validate claims"): a capital there proves nothing; any other capitalised
# word is checked. Base forms: -s, -es, -ed, -ing and -ies endings are removed before the lookup.
OPENERS = frozenset("""submit send receive route publish save check validate verify authorise authorize serve transform train prepare run review
store read write fetch load sync push pull stream notify alert issue approve reject match enrich clean normalise normalize aggregate calculate
compute forecast predict score classify extract convert generate render display show list search index query expose host deploy schedule
trigger call invoke process handle accept record log monitor archive upload download import export map ingest collect track report hold keep
carry provide support manage control orchestrate coordinate cache queue buffer open close create update delete remove capture deliver feed
request respond reply tell give get set put end start stop pass forward relay secure encrypt protect post translate propose rate buy sell
pay bill charge book reserve assign allocate dispatch plan optimise optimize detect flag escalate resolve confirm sign verify look find
take offer kept told read left unchanged incoming outgoing low high new old all each any the one two three four five users customer customers""".split())


def _opener(word: str) -> bool:
    w = word.casefold()
    return any(c in OPENERS for c in (w, w[:-1], w[:-2], w[:-3], w[:-3] + "e", w[:-2] + "e", w[:-3] + "y", w[:-1] + "e", w[:-4]))


def _prose(text, section_quotes, case_quotes):
    """check_prose without the flag for an ordinary opening word (OPENERS)."""
    from app.writeup import check_prose
    first = re.match(r"\s*([A-Za-z]+)", text)
    if first and _opener(first.group(1)):
        text = text[first.end():]  # the rest is still checked, the opening word is not
    return check_prose(text, section_quotes, case_quotes)


def unsupported_terms(name: str, vocab: set[str]) -> list[str]:
    """Words in a component name that the cited quote, the tech stack and the references never use and that are not generic
    architecture words: any word of 3 letters or more, and any capitalised word, acronym or word with a digit. Only an ordinary
    opener ("The", "Using") at the start is exempt (a product is not one)."""
    from app.writeup import COMMON_STARTS
    known = vocab | ARCH_VOCAB
    out = []
    for i, w in enumerate(re.split(r"[\s/&-]+", name)):
        bare = w.strip(".,;:()[]\"'")
        if not bare:
            continue
        marked = bare[0].isupper() or any(ch.isdigit() for ch in bare)
        if len(bare) < 3 and not marked:
            continue
        if bare.casefold() in COMMON_STARTS:  # "for", "and", "The": never a product
            continue
        if not _known(bare, known):
            out.append(bare)
    return out


def tech_match(ev, name: str) -> bool:
    return bool(_words(name) & _words(_ev(ev).tech))


def prune_architecture(case, arch: "Architecture | None" = None) -> None:
    """Drop a component with neither a cite nor a technology match, and the flows that lose an end (decision 2).
    `case` is a ReferenceCase (its own architecture) or an Evidence (then `arch`)."""
    ev, a = _ev(case), arch or getattr(case, "architecture", None)
    if a:
        a.components = [c for c in a.components if (ev.quote(c.cite) is not None if ev.strict else c.cite.strip() or tech_match(ev, c.name))]
        ids = {c.id for c in a.components}
        a.flows = [f for f in a.flows if f.src in ids and f.dst in ids and (not ev.strict or ev.quote(f.cite) is not None)]


def check_architecture(case, arch: "Architecture | None" = None) -> list[str]:
    """What in the architecture no sourced point supports; empty means clean. The case is not changed.
    `case` is a ReferenceCase (its own architecture) or an Evidence (then `arch`)."""
    ev, a = _ev(case), arch or getattr(case, "architecture", None)
    if not a:
        return []
    allq, flags, tech = ev.quotes, [], ev.tech
    for c in a.components:
        q = ev.quote(c.cite) if c.cite.strip() else None
        who = f"component '{c.name}'"
        if c.cite.strip() and q is None:
            flags.append(f"{who} cites '{c.cite}', which is no sourced point")
        if not c.cite.strip() and not tech_match(ev, c.name):
            flags.append(f"{who} has no cite and matches no technology")
        if c.cite.strip() and not _words(c.name) & (_words(q or "") | _words(tech)):
            flags.append(f"{who} matches neither its cited point nor the technology")  # one flag when there is no cite either: above
        flags += [f"{who} names '{t}', which neither its cited point nor the technology uses"
                  for t in unsupported_terms(c.name, _tokens(q or "") | _tokens(tech))]
        flags += [f"{who}: {f}" for t in (c.name, c.role) for f in _prose(t, [q] if q else [], allq)]
    names = {c.id: c.name for c in a.components}
    for f in a.flows:
        q = ev.quote(f.cite) if f.cite.strip() else None
        who = f"flow '{names.get(f.src, f.src)}' to '{names.get(f.dst, f.dst)}'"
        if not f.cite.strip() or q is None:
            flags.append(f"{who} cites no sourced point" if not f.cite.strip() else f"{who} cites '{f.cite}', which is no sourced point")
        flags += [f"{who}: {m}" for m in _prose(f.label, [q] if q else [], allq)]
    for co in a.callouts:
        o = ev.outcomes[co.outcome] if 0 <= co.outcome < len(ev.outcomes) else None
        if o is None:
            flags.append(f"callout '{co.text}' points at outcome {co.outcome}, which is no sourced outcome")
            continue
        flags += [f"callout '{co.text}': {m}" for m in _prose(co.text, [o], allq)]
    if a.title != DEFAULT_TITLE:
        flags += [f"title: {m}" for m in _prose(a.title, allq, allq)]
    return list(dict.fromkeys(flags))
