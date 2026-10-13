import hashlib
from pathlib import Path

import pytest
from PIL import ImageFont

FONTS = Path(__file__).resolve().parent.parent / "brand" / "fonts"
TTFS = sorted(FONTS.glob("IBMPlexSans-*.ttf"))


def test_three_weights_and_licence():
    assert [p.stem for p in TTFS] == ["IBMPlexSans-Medium", "IBMPlexSans-Regular", "IBMPlexSans-SemiBold"]
    assert (FONTS / "OFL.txt").is_file()


@pytest.mark.parametrize("ttf", TTFS, ids=lambda p: p.stem)
def test_font_loads_and_matches_sha(ttf):
    assert ImageFont.truetype(str(ttf), 20).getlength("Core banking") > 0
    sums = dict(l.split()[::-1] for l in (FONTS / "SHA256SUMS").read_text().splitlines())
    assert sums[ttf.name] == hashlib.sha256(ttf.read_bytes()).hexdigest()
