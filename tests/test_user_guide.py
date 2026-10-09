"""User guide checks: relative links resolve, bold labels exist in the templates."""
import html
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs/user-guide"
PAGES = sorted(GUIDE.glob("*.md")) if GUIDE.is_dir() else []
# Bold used for structure, not for UI labels.
NOT_LABELS = {"What it's for", "Steps", "What you see", "Which page is mine:"}
WILD = "\x01"


def _ws(s):
    return " ".join(s.split())


def _template_runs():
    """Text runs between tags; each Jinja {{ }} becomes WILD."""
    text = "\n".join(p.read_text() for p in sorted((ROOT / "app/templates").glob("*.html")))
    text = re.sub(r"\{#.*?#\}|\{%.*?%\}", "", text, flags=re.S)
    text = re.sub(r"\{\{.*?\}\}", WILD, text, flags=re.S)
    text = html.unescape(text)
    flat = _ws(re.sub(r"<[^>]*>", " ", text))
    runs = [_ws(r) for r in re.split(r"<[^>]*>", text)]
    return flat, [r for r in runs if r]


FLAT, RUNS = _template_runs()


def _label_ok(label):
    label = _ws(label)
    if label in FLAT:
        return True
    for run in RUNS:
        if not re.search(r"\w", run.replace(WILD, "")):
            continue  # all wildcard: would match any label
        pat = ".+?".join(re.escape(part) for part in run.split(WILD))
        if re.search(pat, label):
            return True
    return False


def test_readme_guide_link_resolves():
    # Until the guide exists (plan step 3) there is nothing to check.
    for target in re.findall(r"\]\((docs/user-guide/[^)#\s]+)", (ROOT / "README.md").read_text()):
        assert (ROOT / target).exists(), f"README.md: broken link {target}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_links_resolve(page):
    for target in re.findall(r"\]\(([^)#\s]+)", page.read_text()):
        if target.startswith(("http", "mailto:")):
            continue
        assert (page.parent / target).exists(), f"{page.name}: broken link {target}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_bold_labels_exist_in_templates(page):
    for label in re.findall(r"\*\*(.+?)\*\*", page.read_text()):
        if _ws(label) in NOT_LABELS:
            continue
        assert _label_ok(label), f"{page.name}: no such label in templates: {label!r}"


def test_label_matcher():
    assert _label_ok("Download all (Word)")
    assert _label_ok("Due for re-review within 30 days")
    assert not _label_ok("No such button")
