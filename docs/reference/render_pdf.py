"""Render the reference playbook (markdown) to PDF.

Run with an interpreter that has ReportLab installed:

    python docs/reference/render_pdf.py docs/reference/jev-playbook.md docs/reference/jev-playbook.pdf

The converter is deliberately small: headings, paragraphs, bullets, fenced code and
pipe tables are enough for a reference document, and anything fancier would be a lie
about how faithful a re-render is compared with the original article.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
)

INLINE_CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
BULLET = re.compile(r"^\s*[*\-]\s+")
NUMBERED = re.compile(r"^\s*\d+/\s+")


def inline(text: str) -> str:
    """Markdown inline -> ReportLab mini-HTML. Order matters: escape first."""
    out = html.escape(text, quote=False)
    out = INLINE_CODE.sub(lambda m: f'<font face="Courier" size="8.5">{m.group(1)}</font>', out)
    out = BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", out)
    out = ITALIC.sub(lambda m: f"<i>{m.group(1)}</i>", out)
    out = LINK.sub(lambda m: f'{m.group(1)} <font size="7" color="#555555">({m.group(2)})</font>', out)
    return out


def build_styles():
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "Body", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.6,
        leading=13.4, alignment=TA_LEFT, spaceAfter=5, textColor=colors.HexColor("#1a1a1a"),
    )
    return {
        "title": ParagraphStyle(
            "Title", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=19,
            leading=23, spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "H2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13,
            leading=16, spaceBefore=14, spaceAfter=6, textColor=colors.HexColor("#111111"),
        ),
        "h3": ParagraphStyle(
            "H3", parent=styles["Heading3"], fontName="Helvetica-Bold", fontSize=10.8,
            leading=14, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#333333"),
        ),
        "body": body,
        "bullet": ParagraphStyle(
            "Bullet", parent=body, leftIndent=12, bulletIndent=2, spaceAfter=3,
        ),
        "meta": ParagraphStyle(
            "Meta", parent=body, fontSize=8.6, leading=11.5, textColor=colors.HexColor("#555555"),
        ),
        "code": ParagraphStyle(
            "Code", fontName="Courier", fontSize=7.6, leading=9.4,
            backColor=colors.HexColor("#f4f4f4"), borderPadding=4, textColor=colors.HexColor("#202020"),
        ),
    }


def render(source: Path, target: Path) -> int:
    styles = build_styles()
    lines = source.read_text(encoding="utf-8").splitlines()
    flow: list = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            fence = stripped[3:].strip()
            block: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            flow.append(Spacer(1, 3))
            flow.append(Preformatted("\n".join(block) or " ", styles["code"]))
            flow.append(Spacer(1, 5))
            continue

        if stripped.startswith("|"):
            rows: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = lines[i].strip()
                if not re.fullmatch(r"\|[\s\-:|]+\|", row):
                    cells = [c.strip() for c in row.strip("|").split("|")]
                    rows.append("  ".join(cells))
                i += 1
            flow.append(Spacer(1, 3))
            flow.append(Preformatted("\n".join(rows), styles["code"]))
            flow.append(Spacer(1, 5))
            continue

        if stripped in {"---", "***", "* * *"}:
            flow.append(Spacer(1, 6))
            i += 1
            continue

        if not stripped:
            i += 1
            continue

        if stripped.startswith("### "):
            flow.append(Paragraph(inline(stripped[4:].strip("* ")), styles["h3"]))
        elif stripped.startswith("## "):
            flow.append(Paragraph(inline(stripped[3:].strip("* ")), styles["h2"]))
        elif stripped.startswith("# "):
            flow.append(Paragraph(inline(stripped[2:]), styles["title"]))
            flow.append(Spacer(1, 2))
        elif BULLET.match(stripped):
            flow.append(
                Paragraph(inline(BULLET.sub("", stripped)), styles["bullet"], bulletText="\u2022")
            )
        elif NUMBERED.match(stripped):
            flow.append(Paragraph(inline(NUMBERED.sub("", stripped)), styles["bullet"]))
        elif stripped.startswith("> "):
            flow.append(Paragraph(inline(stripped[2:]), styles["meta"]))
        else:
            flow.append(Paragraph(inline(stripped), styles["body"]))
        i += 1

    doc = SimpleDocTemplate(
        str(target), pagesize=A4,
        leftMargin=20 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=16 * mm,
        title="Jev engineering: the reference playbook",
        author="Mr. Buzzoni (@Mr_Buzzoni)",
        subject="System One decision models, calibrated confidence and cascades",
    )

    def on_page(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#888888"))
        canvas.drawString(20 * mm, 10 * mm, "jev-harness - reference material")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, str(document.page))
        canvas.restoreState()

    doc.build(flow, onFirstPage=on_page, onLaterPages=on_page)
    return 0


if __name__ == "__main__":
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/reference/jev-playbook.md")
    dst = Path(sys.argv[2] if len(sys.argv) > 2 else "docs/reference/jev-playbook.pdf")
    raise SystemExit(render(src, dst))
