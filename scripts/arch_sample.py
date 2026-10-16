"""Dev helper: render an Architecture JSON file to sample.png, sample.svg and sample.pptx.

    python scripts/arch_sample.py tests/fixtures/arch/typical.json OUT_DIR
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pptx import Presentation  # noqa: E402
from pptx.util import Inches  # noqa: E402

from app.archdiagram import layout, to_mermaid, to_pptx_shapes, to_png, to_svg  # noqa: E402
from app.schema import Architecture  # noqa: E402

src, out = Path(sys.argv[1]), Path(sys.argv[2])
arch = Architecture.model_validate_json(src.read_text())
sc = layout(arch, "Source: Demo reference case · reviewed 2026-01-01")
out.mkdir(parents=True, exist_ok=True)
(out / "sample.png").write_bytes(to_png(sc))
(out / "sample.svg").write_text(to_svg(sc, arch.title))
(out / "sample.mmd").write_text(to_mermaid(arch))
prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
to_pptx_shapes(prs.slides.add_slide(prs.slide_layouts[6]), sc, Inches(.4), Inches(.3), Inches(12.5), Inches(7))
prs.save(out / "sample.pptx")
