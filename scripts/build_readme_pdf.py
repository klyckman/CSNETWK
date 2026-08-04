"""Render the submission README Markdown as a polished PDF artifact."""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    XPreformatted,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "README.md"
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "pdf" / "README.pdf"
PAGE_WIDTH, PAGE_HEIGHT = A4
LEFT_MARGIN = 17 * mm
RIGHT_MARGIN = 17 * mm
TOP_MARGIN = 18 * mm
BOTTOM_MARGIN = 17 * mm
CONTENT_WIDTH = PAGE_WIDTH - LEFT_MARGIN - RIGHT_MARGIN


def _register_fonts() -> tuple[str, str, str]:
    windows_fonts = Path("C:/Windows/Fonts")
    candidates = {
        "body": (windows_fonts / "segoeui.ttf", "Helvetica"),
        "bold": (windows_fonts / "segoeuib.ttf", "Helvetica-Bold"),
        "mono": (windows_fonts / "consola.ttf", "Courier"),
    }
    names: dict[str, str] = {}
    for role, (path, fallback) in candidates.items():
        if path.exists():
            name = f"MTGNP-{role}"
            pdfmetrics.registerFont(TTFont(name, str(path)))
            names[role] = name
        else:
            names[role] = fallback
    return names["body"], names["bold"], names["mono"]


BODY_FONT, BOLD_FONT, MONO_FONT = _register_fonts()


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "Title",
            parent=base["Title"],
            fontName=BOLD_FONT,
            fontSize=24,
            leading=29,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#172554"),
            spaceAfter=12,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontName=BOLD_FONT,
            fontSize=14,
            leading=18,
            textColor=colors.HexColor("#1D4ED8"),
            spaceBefore=12,
            spaceAfter=6,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "H3",
            parent=base["Heading3"],
            fontName=BOLD_FONT,
            fontSize=11,
            leading=14,
            textColor=colors.HexColor("#334155"),
            spaceBefore=9,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName=BODY_FONT,
            fontSize=9.2,
            leading=13,
            textColor=colors.HexColor("#1F2937"),
            spaceAfter=5,
        ),
        "bullet": ParagraphStyle(
            "Bullet",
            parent=base["BodyText"],
            fontName=BODY_FONT,
            fontSize=8.8,
            leading=12,
            leftIndent=2,
            spaceAfter=2,
        ),
        "code": ParagraphStyle(
            "Code",
            parent=base["Code"],
            fontName=MONO_FONT,
            fontSize=7.2,
            leading=9.2,
            leftIndent=6,
            rightIndent=6,
            borderColor=colors.HexColor("#CBD5E1"),
            borderWidth=0.5,
            borderPadding=6,
            backColor=colors.HexColor("#F8FAFC"),
            spaceBefore=3,
            spaceAfter=7,
        ),
        "table": ParagraphStyle(
            "Table",
            parent=base["BodyText"],
            fontName=BODY_FONT,
            fontSize=6.8,
            leading=8.5,
            textColor=colors.HexColor("#1F2937"),
        ),
        "table_head": ParagraphStyle(
            "TableHead",
            parent=base["BodyText"],
            fontName=BOLD_FONT,
            fontSize=6.8,
            leading=8.5,
            textColor=colors.white,
        ),
    }


STYLES = _styles()


def _inline_markup(text: str) -> str:
    escaped = html.escape(text.strip())
    escaped = re.sub(
        r"`([^`]+)`",
        lambda match: f'<font name="{MONO_FONT}">{match.group(1)}</font>',
        escaped,
    )
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    return escaped


def _table_rows(lines: list[str]) -> list[list[str]]:
    return [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in lines
    ]


def _build_table(lines: list[str]) -> Table:
    raw_rows = _table_rows(lines)
    rows = [raw_rows[0], *raw_rows[2:]]
    column_count = max(len(row) for row in rows)
    normalized = [row + [""] * (column_count - len(row)) for row in rows]
    is_matrix = column_count >= 5
    if is_matrix:
        widths = [CONTENT_WIDTH * 0.28] + [CONTENT_WIDTH * 0.18] * 4
    elif column_count == 3:
        widths = [CONTENT_WIDTH * 0.22, CONTENT_WIDTH * 0.18, CONTENT_WIDTH * 0.60]
    else:
        widths = [CONTENT_WIDTH / column_count] * column_count
    data = []
    for row_index, row in enumerate(normalized):
        style = STYLES["table_head"] if row_index == 0 else STYLES["table"]
        data.append([Paragraph(_inline_markup(cell), style) for cell in row])
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#94A3B8")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _parse_markdown(markdown: str) -> list[object]:
    lines = markdown.splitlines()
    story: list[object] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        if stripped.startswith("```"):
            language = stripped[3:].strip()
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            label = "Diagram source" if language == "mermaid" else "Command / example"
            story.append(
                KeepTogether(
                    [
                        Paragraph(label, STYLES["h3"]),
                        XPreformatted(html.escape("\n".join(code_lines)), STYLES["code"]),
                    ]
                )
            )
            index += 1
            continue
        if stripped.startswith("# "):
            story.append(Spacer(1, 8))
            story.append(Paragraph(_inline_markup(stripped[2:]), STYLES["title"]))
            story.append(
                Paragraph(
                    "Magic: The Gathering Multiplayer Network Protocol v1.0",
                    ParagraphStyle(
                        "Subtitle",
                        parent=STYLES["body"],
                        alignment=TA_CENTER,
                        fontSize=10,
                        textColor=colors.HexColor("#64748B"),
                        spaceAfter=10,
                    ),
                )
            )
            index += 1
            continue
        if stripped.startswith("## "):
            heading = stripped[3:]
            if heading == "AI Usage":
                story.append(PageBreak())
            story.append(Paragraph(_inline_markup(heading), STYLES["h2"]))
            index += 1
            continue
        if stripped.startswith("### "):
            story.append(Paragraph(_inline_markup(stripped[4:]), STYLES["h3"]))
            index += 1
            continue
        if stripped.startswith("|") and index + 1 < len(lines) and re.match(
            r"^\s*\|?\s*:?-+", lines[index + 1]
        ):
            table_lines = [line, lines[index + 1]]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            story.extend([_build_table(table_lines), Spacer(1, 7)])
            continue
        if re.match(r"^[-*] ", stripped):
            items: list[ListItem] = []
            while index < len(lines) and re.match(r"^\s*[-*] ", lines[index]):
                content = re.sub(r"^\s*[-*] ", "", lines[index])
                index += 1
                continuation: list[str] = []
                while index < len(lines):
                    candidate = lines[index]
                    if not candidate.strip() or re.match(r"^\s*[-*] ", candidate):
                        break
                    if candidate.startswith("  "):
                        continuation.append(candidate.strip())
                        index += 1
                        continue
                    break
                if continuation:
                    content = " ".join([content, *continuation])
                items.append(ListItem(Paragraph(_inline_markup(content), STYLES["bullet"])))
            story.append(
                ListFlowable(
                    items,
                    bulletType="bullet",
                    bulletFontName=BODY_FONT,
                    bulletFontSize=7,
                    leftIndent=14,
                    bulletOffsetY=1,
                    spaceAfter=5,
                )
            )
            continue
        if re.match(r"^\d+\. ", stripped):
            items = []
            while index < len(lines) and re.match(r"^\s*\d+\. ", lines[index]):
                content = re.sub(r"^\s*\d+\. ", "", lines[index])
                index += 1
                continuation = []
                while index < len(lines):
                    candidate = lines[index]
                    if not candidate.strip() or re.match(r"^\s*\d+\. ", candidate):
                        break
                    if candidate.startswith("  "):
                        continuation.append(candidate.strip())
                        index += 1
                        continue
                    break
                if continuation:
                    content = " ".join([content, *continuation])
                items.append(ListItem(Paragraph(_inline_markup(content), STYLES["bullet"])))
            story.append(
                ListFlowable(
                    items,
                    bulletType="1",
                    bulletFontName=BODY_FONT,
                    bulletFontSize=8,
                    leftIndent=18,
                    spaceAfter=5,
                )
            )
            continue

        paragraph_lines = [stripped]
        index += 1
        while index < len(lines):
            candidate = lines[index].strip()
            if (
                not candidate
                or candidate.startswith("#")
                or candidate.startswith("```")
                or candidate.startswith("|")
                or re.match(r"^[-*] ", candidate)
                or re.match(r"^\d+\. ", candidate)
            ):
                break
            paragraph_lines.append(candidate)
            index += 1
        story.append(
            Paragraph(_inline_markup(" ".join(paragraph_lines)), STYLES["body"])
        )
    return story


def _draw_page(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont(BODY_FONT, 7.5)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawRightString(
        PAGE_WIDTH - RIGHT_MARGIN,
        9 * mm,
        f"Page {document.page}",
    )
    canvas.restoreState()


def build_pdf(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = BaseDocTemplate(
        str(destination),
        pagesize=A4,
        leftMargin=LEFT_MARGIN,
        rightMargin=RIGHT_MARGIN,
        topMargin=TOP_MARGIN,
        bottomMargin=BOTTOM_MARGIN,
        title="MTGNP README",
        author="CSNETWK MTGNP Project Group",
        subject="Build, run, architecture, testing, work distribution, and AI usage",
    )
    frame = Frame(
        LEFT_MARGIN,
        BOTTOM_MARGIN,
        CONTENT_WIDTH,
        PAGE_HEIGHT - TOP_MARGIN - BOTTOM_MARGIN,
        id="content",
    )
    document.addPageTemplates(
        [PageTemplate(id="main", frames=[frame], onPageEnd=_draw_page)]
    )
    story = _parse_markdown(source.read_text(encoding="utf-8"))
    document.build(story)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    build_pdf(args.input.resolve(), args.output.resolve())
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
