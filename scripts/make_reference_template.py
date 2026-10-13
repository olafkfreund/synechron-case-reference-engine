"""Write the placeholder Word template. Marketing's real template replaces brand/reference.docx
and must use the same context: cases[] with title, client, note (empty unless an engagement), summary, details[{label,value}],
blocks[{heading,text}], lists[{heading,bullets[]}], diagrams[{image,caption}]; and industry (or None): heading, disclaimer,
groups[{title, lead, quotes[{quote, n}]}], sources[{n, title, publisher, url, date}], note
(statements[{text}] is the flat fallback). The optional second path gets the brief template (brand/brief.docx): brief with title, disclaimer,
takeaways[str], themes[{heading, text}], sources[{n, title, publisher, url, date}], appendix[{n, quote}], note.
Usage: make_reference_template.py <out.docx> [<brief.docx>]"""
import sys

from docx import Document
from docx.shared import Pt, RGBColor

d = Document()
run = d.sections[0].header.paragraphs[0].add_run("DRAFT TEMPLATE – replace with the marketing template")
run.bold, run.font.size, run.font.color.rgb = True, Pt(20), RGBColor(0xC0, 0, 0)

d.add_paragraph("{%p for c in cases %}")
d.add_heading("{{ c.title }}", 1).paragraph_format.page_break_before = True  # one case per page
d.add_paragraph().add_run("{{ c.client }}").italic = True
d.add_paragraph("{%p if c.note %}")
d.add_paragraph("{{ c.note }}")
d.add_paragraph("{%p endif %}")
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
d.add_paragraph("{%p for d in c.diagrams %}")
d.add_paragraph("{{ d.image }}")
d.add_paragraph("{{ d.caption }}", style="Caption")
d.add_paragraph("{%p endfor %}")
d.add_paragraph("{%p endfor %}")
d.add_paragraph("{%p if industry %}")
d.add_heading("{{ industry.heading }}", 1).paragraph_format.page_break_before = True
d.add_paragraph().add_run("{{ industry.disclaimer }}").italic = True
d.add_paragraph("{%p for g in industry.groups %}")
d.add_heading("{{ g.title }}", 2)
d.add_paragraph().add_run("{{ g.lead }}").italic = True
d.add_paragraph("{%p for q in g.quotes %}")
d.add_paragraph("\u201c{{ q.quote }}\u201d [{{ q.n }}]", style="List Bullet")
d.add_paragraph("{%p endfor %}")
d.add_paragraph("{%p endfor %}")
d.add_paragraph("{%p if industry.sources %}")
d.add_heading("Sources", 2)
d.add_paragraph("{%p for s in industry.sources %}")
d.add_paragraph("[{{ s.n }}] {{ s.title }}, {{ s.publisher }}, {{ s.url }}, retrieved {{ s.date }}")
d.add_paragraph("{%p endfor %}")
d.add_paragraph("{%p endif %}")
d.add_paragraph("{%p if industry.note %}")
d.add_paragraph("{{ industry.note }}")
d.add_paragraph("{%p endif %}")
d.add_paragraph("{%p endif %}")
d.save(sys.argv[1])

if len(sys.argv) > 2:
    b = Document()
    run = b.sections[0].header.paragraphs[0].add_run("DRAFT TEMPLATE – replace with the marketing template")
    run.bold, run.font.size, run.font.color.rgb = True, Pt(20), RGBColor(0xC0, 0, 0)
    b.add_heading("{{ brief.title }}", 1)
    b.add_paragraph().add_run("{{ brief.disclaimer }}").italic = True
    b.add_paragraph("{%p if brief.takeaways %}")
    b.add_heading("Key takeaways", 2)
    b.add_paragraph("{%p for t in brief.takeaways %}")
    b.add_paragraph("{{ t }}", style="List Bullet")
    b.add_paragraph("{%p endfor %}")
    b.add_paragraph("{%p endif %}")
    b.add_paragraph("{%p for t in brief.themes %}")
    b.add_heading("{{ t.heading }}", 2)
    b.add_paragraph("{{ t.text }}")
    b.add_paragraph("{%p endfor %}")
    b.add_paragraph("{%p if brief.sources %}")
    b.add_heading("Sources", 2)
    b.add_paragraph("{%p for s in brief.sources %}")
    b.add_paragraph("[{{ s.n }}] {{ s.title }}, {{ s.publisher }}, {{ s.url }}, retrieved {{ s.date }}")
    b.add_paragraph("{%p endfor %}")
    b.add_heading("Quotes", 2)
    b.add_paragraph("{%p for q in brief.appendix %}")
    b.add_paragraph("\u201c{{ q.quote }}\u201d [{{ q.n }}]")
    b.add_paragraph("{%p endfor %}")
    b.add_paragraph("{%p endif %}")
    b.add_paragraph("{%p if brief.note %}")
    b.add_paragraph("{{ brief.note }}")
    b.add_paragraph("{%p endif %}")
    b.save(sys.argv[2])
