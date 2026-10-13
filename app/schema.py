import re
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator
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


class Prose(_Model):
    """A written section built from the points; `flags` lists what the checker could not source."""
    text: str = ""
    by_reviewer: bool = False
    flags: list[str] = []


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
    dismissed_images: SkipJsonSchema[list[int]] = []  # document_images candidates the reviewer turned down (#212)
    basis: SkipJsonSchema[Literal["delivered", "engagement"]] = "delivered"
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
                 *(p.text for p in self.prose.values())]
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
FIELDS = (*_TEXTS, *_INTS, "period_start", "period_end", "capability", "technology", "outcome", "organisation", "reference")


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
        elif f == "reference":
            if _norm(v) not in {_norm(r.value or "") for r in c.tech_references}:
                c.tech_references.append(Sourced[str](value=v, source_quote=q))
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
