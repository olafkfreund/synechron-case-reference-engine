"""Write the placeholder Word template. Marketing's real template replaces brand/reference.docx
and must use the same context: cases[] with title, client, summary, details[{label,value}],
blocks[{heading,text}], lists[{heading,bullets[]}]. Usage: make_reference_template.py <out.docx>"""
import sys

from docx import Document
from docx.shared import Pt, RGBColor

d = Document()
run = d.sections[0].header.paragraphs[0].add_run("DRAFT TEMPLATE – replace with the marketing template")
run.bold, run.font.size, run.font.color.rgb = True, Pt(20), RGBColor(0xC0, 0, 0)

d.add_paragraph("{%p for c in cases %}")
d.add_heading("{{ c.title }}", 1).paragraph_format.page_break_before = True  # one case per page
d.add_paragraph().add_run("{{ c.client }}").italic = True
d.add_paragraph("{{ c.summary }}")
d.add_paragraph("{%p for x in c.details %}")
d.add_paragraph("{{ x.label }}: {{ x.value }}")
d.add_paragraph("{%p endfor %}")
d.add_paragraph("{%p for b in c.blocks %}")
d.add_heading("{{ b.heading }}", 2)
d.add_paragraph("{{ b.text }}")
d.add_paragraph("{%p endfor %}")
d.add_paragraph("{%p for l in c.lists %}")
d.add_heading("{{ l.heading }}", 2)
d.add_paragraph("{%p for i in l.bullets %}")
d.add_paragraph("{{ i }}", style="List Bullet")
d.add_paragraph("{%p endfor %}")
d.add_paragraph("{%p endfor %}")
d.add_paragraph("{%p endfor %}")
d.save(sys.argv[1])
