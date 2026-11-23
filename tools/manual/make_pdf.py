"""SeismoVerse user manual as PDF, from the content the Word generator wrote (make_manual.js ->
SeismoVerse_User_Manual.json). Usage: python make_pdf.py <content.json> <shots dir> <icon png> <out.pdf>"""
import html
import json
import os
import re
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, ListFlowable, ListItem,
                                NextPageTemplate, PageBreak, PageTemplate, Paragraph, Spacer, Table,
                                TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.lib.utils import ImageReader

CONTENT, SHOTS, ICON, OUT = sys.argv[1:5]
data = json.load(open(CONTENT, encoding="utf-8"))
VERSION = data["version"]

FONTS = r"C:\Windows\Fonts"
for name, file in (("Calibri", "calibri.ttf"), ("Calibri-Bold", "calibrib.ttf"),
                   ("Calibri-Italic", "calibrii.ttf"), ("Calibri-BoldItalic", "calibriz.ttf"),
                   ("Consolas", "consola.ttf")):
    pdfmetrics.registerFont(TTFont(name, os.path.join(FONTS, file)))
pdfmetrics.registerFontFamily("Calibri", normal="Calibri", bold="Calibri-Bold", italic="Calibri-Italic",
                              boldItalic="Calibri-BoldItalic")

TEAL = colors.HexColor("#1F8A7E")
DARK = colors.HexColor("#1B2A33")
MUTED = colors.HexColor("#5B6B73")
TEXT = colors.HexColor("#222222")
LINE = colors.HexColor("#B8C4C9")
NOTE_BG = colors.HexColor("#E8F4F2")

body = ParagraphStyle("body", fontName="Calibri", fontSize=11, leading=15, textColor=TEXT, spaceAfter=6)
cell = ParagraphStyle("cell", parent=body, fontSize=10, leading=13, spaceAfter=0)
cell_head = ParagraphStyle("cellhead", parent=cell, fontName="Calibri-Bold", textColor=colors.white)
h1 = ParagraphStyle("h1", fontName="Calibri-Bold", fontSize=18, leading=22, textColor=TEAL, spaceAfter=10)
h2 = ParagraphStyle("h2", fontName="Calibri-Bold", fontSize=14, leading=18, textColor=DARK, spaceBefore=12,
                    spaceAfter=6)
h3 = ParagraphStyle("h3", fontName="Calibri-Bold", fontSize=12, leading=15, textColor=DARK, spaceBefore=8,
                    spaceAfter=4)
caption = ParagraphStyle("caption", parent=body, fontName="Calibri-Italic", fontSize=9, leading=11,
                         textColor=MUTED, alignment=TA_CENTER, spaceAfter=10)
note = ParagraphStyle("note", parent=body, spaceAfter=0)


def markup(text):
    """**bold** and `code` -> reportlab mini-HTML (the rest escaped)."""
    out, last = [], 0
    for m in re.finditer(r"\*\*([^*]+)\*\*|`([^`]+)`", text):
        out.append(html.escape(text[last:m.start()], quote=False))
        if m.group(1) is not None:
            out.append(f"<b>{html.escape(m.group(1), quote=False)}</b>")
        else:
            out.append(f'<font name="Consolas" size="9.5">{html.escape(m.group(2), quote=False)}</font>')
        last = m.end()
    out.append(html.escape(text[last:], quote=False))
    return "".join(out)


class Doc(BaseDocTemplate):
    def afterFlowable(self, f):
        if isinstance(f, Paragraph) and getattr(f, "toc_level", None) is not None:
            text = f.getPlainText()
            key = f"h{self.seq.nextf('heading')}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=f.toc_level, closed=f.toc_level > 0)
            self.notify("TOCEntry", (f.toc_level, text, self.page, key))


def footer(canv, doc):
    canv.saveState()
    w, _h = A4
    canv.setStrokeColor(LINE)
    canv.setLineWidth(0.5)
    canv.line(2.54 * cm, 1.8 * cm, w - 2.54 * cm, 1.8 * cm)
    canv.setFont("Calibri", 9)
    canv.setFillColor(MUTED)
    canv.drawString(2.54 * cm, 1.35 * cm, f"SeismoVerse {VERSION} - User Manual")
    canv.drawRightString(w - 2.54 * cm, 1.35 * cm, f"Page {doc.page}")
    canv.restoreState()


doc = Doc(OUT, pagesize=A4, leftMargin=2.54 * cm, rightMargin=2.54 * cm, topMargin=2.54 * cm,
          bottomMargin=2.54 * cm, title="SeismoVerse User Manual", author="Arnab Kole",
          subject=f"SeismoVerse {VERSION} user manual")
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
doc.addPageTemplates([PageTemplate("cover", [frame]), PageTemplate("body", [frame], onPage=footer)])
WIDTH = doc.width

story = []
# cover
story += [Spacer(1, 3.2 * cm), Image(ICON, 4 * cm, 4 * cm), Spacer(1, 1 * cm),
          Paragraph("SeismoVerse", ParagraphStyle("t", fontName="Calibri-Bold", fontSize=36, leading=42,
                                                   textColor=DARK, alignment=TA_CENTER)),
          Paragraph("User Manual", ParagraphStyle("s", fontName="Calibri", fontSize=20, leading=26,
                                                  textColor=TEAL, alignment=TA_CENTER)),
          Spacer(1, 0.3 * cm),
          Paragraph("Live seismic data acquisition, viewing and recording",
                    ParagraphStyle("s2", fontName="Calibri", fontSize=12, leading=16, textColor=MUTED,
                                   alignment=TA_CENTER)),
          Spacer(1, 4.5 * cm)]
centre = ParagraphStyle("c", fontName="Calibri", fontSize=12, leading=18, textColor=DARK, alignment=TA_CENTER)
story += [Paragraph(f"Version {VERSION}", centre), Paragraph("<b>Author:</b> Arnab Kole", centre),
          Paragraph("<b>Supported:</b> Rohit Santra", centre),
          Paragraph('<font color="#5B6B73">November 2026</font>', centre),
          NextPageTemplate("body"), PageBreak()]
# contents
toc = TableOfContents()
toc.levelStyles = [ParagraphStyle("toc1", fontName="Calibri-Bold", fontSize=12, leading=18, textColor=DARK,
                                  leftIndent=0, firstLineIndent=0),
                   ParagraphStyle("toc2", fontName="Calibri", fontSize=10.5, leading=14, textColor=TEXT,
                                  leftIndent=18, firstLineIndent=0)]
story += [Paragraph("Contents", ParagraphStyle("ct", parent=h1, fontSize=20, leading=24, spaceAfter=14)), toc]

first = True
for b in data["model"]:
    k = b["k"]
    if k == "h1":
        story.append(PageBreak())
        p = Paragraph(markup(b["t"]), h1)
        p.toc_level = 0
        story.append(p)
        first = False
    elif k in ("h2", "h3"):
        p = Paragraph(markup(b["t"]), h2 if k == "h2" else h3)
        if k == "h2":
            p.toc_level = 1
        story.append(p)
    elif k == "p":
        story.append(Paragraph(markup(b["t"]), body))
    elif k in ("bullets", "steps"):
        items = [ListItem(Paragraph(markup(t), body), leftIndent=16) for t in b["items"]]
        if k == "bullets":
            story.append(ListFlowable(items, bulletType="bullet", start="•", leftIndent=16, bulletFontName="Calibri",
                                      bulletFontSize=11))
        else:
            story.append(ListFlowable(items, bulletType="1", leftIndent=18, bulletFontName="Calibri",
                                      bulletFontSize=11, bulletFormat="%s."))
    elif k == "note":
        t = Table([[Paragraph(f'<font color="#1F8A7E"><b>{b["label"]}:</b></font> {markup(b["t"])}', note)]],
                  colWidths=[WIDTH])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NOTE_BG),
                               ("LINEBEFORE", (0, 0), (0, -1), 3, TEAL),
                               ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                               ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
        story += [Spacer(1, 4), t, Spacer(1, 8)]
    elif k == "img":
        path = os.path.join(SHOTS, b["file"])
        iw, ih = ImageReader(path).getSize()
        max_w = min(WIDTH, b["maxW"] * 0.75)         # px at 96 dpi -> points
        w = min(max_w, iw * 0.75)
        h = ih * w / iw
        if h > 15 * cm:
            h = 15 * cm
            w = iw * h / ih
        story.append(KeepTogether([Spacer(1, 4), Image(path, w, h), Spacer(1, 4),
                                   Paragraph(html.escape(b["caption"]), caption)]))
    elif k == "table":
        total = sum(b["widths"])
        cols = [WIDTH * x / total for x in b["widths"]]
        rows = [[Paragraph(markup(t), cell_head) for t in b["header"]]]
        rows += [[Paragraph(markup(t), cell) for t in r] for r in b["rows"]]
        t = Table(rows, colWidths=cols, repeatRows=1)
        style = [("BACKGROUND", (0, 0), (-1, 0), TEAL), ("GRID", (0, 0), (-1, -1), 0.5, LINE),
                 ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                 ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 4),
                 ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
        for i in range(2, len(rows), 2):
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F3F7F8")))
        t.setStyle(TableStyle(style))
        if len(rows) <= 9:                       # a short table stays on one page
            story += [KeepTogether([t]), Spacer(1, 10)]
        else:
            story += [t, Spacer(1, 10)]
    else:
        raise ValueError(k)

doc.multiBuild(story)
print("written", OUT, os.path.getsize(OUT), "bytes")
