from pathlib import Path

import pytest
from PIL import ImageFont

FONTS = Path("/usr/share/fonts/truetype/aptos")
TTFS = [FONTS / f"{name}.ttf" for name in ("Aptos", "Aptos-SemiBold", "Aptos-Bold", "Aptos-Mono")]


@pytest.mark.parametrize("ttf", TTFS, ids=lambda p: p.stem)
def test_aptos_font_exists_and_measures_text(ttf):
    assert ttf.is_file()
    assert ImageFont.truetype(str(ttf), 20).getlength("Core banking") > 0
