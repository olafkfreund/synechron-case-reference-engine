import re
from typing import Generic, TypeVar

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


def numbers(s: object) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d+(?:[.,]\d+)*", str(s or ""))}


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


class Outcome(_Model):
    metric: str
    value: str
    source_quote: str = ""
    unsourced: SkipJsonSchema[bool] = False


class Period(_Model):
    start: str | None = None
    end: str | None = None
    source_quote: str = ""
    unsourced: SkipJsonSchema[bool] = False


class ReferenceCase(_Model):
    title: Sourced[str]
    client_mention: Sourced[str] = Sourced[str]()  # raw name as written; client_id is resolved later
    industry: Sourced[str] = Sourced[str]()
    region: Sourced[str] = Sourced[str]()
    engagement_type: Sourced[str] = Sourced[str]()
    challenge: Sourced[str] = Sourced[str]()
    solution: Sourced[str] = Sourced[str]()
    capabilities: list[Sourced[str]] = []
    tech_stack: list[Sourced[str]] = []
    outcomes: list[Outcome] = []
    duration_months: Sourced[int] = Sourced[int]()
    team_size: Sourced[int] = Sourced[int]()
    period: Period = Period()
    # synthesised by the LLM, so no verbatim quote; its numbers must come from sourced quotes
    summary: str = Field("", description="At most 80 words. Use only numbers that appear in the source quotes.")

    @field_validator("summary")
    @classmethod
    def _max_80_words(cls, v: str) -> str:
        if len(v.split()) > 80:
            raise ValueError("summary must be at most 80 words")
        return v

    def quotes(self) -> list[str]:
        items = [self.title, self.client_mention, self.industry, self.region, self.engagement_type,
                 self.challenge, self.solution, *self.capabilities, *self.tech_stack,
                 self.duration_months, self.team_size, *self.outcomes, self.period]
        return [i.source_quote for i in items if i.source_quote]

    def summary_sourced(self) -> bool:
        """Every number in the summary appears in some source quote (else it launders invented numbers)."""
        return numbers(self.summary) <= set().union(*map(numbers, self.quotes()))

    def search_text(self) -> str:
        """Field values only (no quotes, keys, or client name) for cases.search_text."""
        parts = [self.title.value, self.industry.value, self.region.value,
                 self.engagement_type.value, self.challenge.value, self.solution.value,
                 *(c.value for c in self.capabilities), *(t.value for t in self.tech_stack),
                 *(f"{o.metric} {o.value}" for o in self.outcomes), self.summary]
        return " ".join(p for p in parts if p)


def llm_schema(model: type[BaseModel] = ReferenceCase) -> dict:
    """JSON schema for structured outputs: pydantic defaults stripped (they leak hidden fields)."""
    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k != "default"}
        return [strip(v) for v in o] if isinstance(o, list) else o
    return strip(model.model_json_schema())
