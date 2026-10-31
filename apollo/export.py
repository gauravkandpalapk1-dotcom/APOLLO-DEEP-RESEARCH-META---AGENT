"""Markdown -> PDF with fpdf2. Deliberately simple: headings, paragraphs, list items, plain-text links."""
import os
import re

FONT_CANDIDATES = [
    "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def _plain(line: str) -> str:
    line = re.sub(r"\[\[(\d+)\]\]\([^)]+\)", r"[\1]", line)       # clickable citations -> [n]
    line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", line)    # links -> text (url)
    return line.replace("**", "").replace("*", "")


def markdown_to_pdf(md: str) -> bytes:
    from fpdf import FPDF
    pdf = FPDF()
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    font = next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)
    if font:
        pdf.add_font("Body", "", font)
        family, clean = "Body", (lambda t: t)
    else:  # core fonts only cover latin-1
        family, clean = "Helvetica", (lambda t: t.encode("latin-1", "replace").decode("latin-1"))

    for raw in md.splitlines():
        line = raw.rstrip()
        if not line:
            pdf.ln(3)
            continue
        size, indent = 11, 0
        if line.startswith("# "):
            size, line = 20, line[2:]
        elif line.startswith("## "):
            size, line = 15, line[3:]
            pdf.ln(2)
        elif re.match(r"^(- |\d+\. )", line):
            indent = 4
        pdf.set_font(family, "", size)
        pdf.set_x(pdf.l_margin + indent)
        pdf.multi_cell(pdf.epw - indent, size * 0.55, clean(_plain(line)))
    out = pdf.output()
    return bytes(out) if not isinstance(out, str) else out.encode("latin-1")
