"""
Bail Application PDF generator for Project Mukti.

Renders a court-ready application from a BailEligibilityReport dict using
ReportLab. Falls back to standard fonts if Noto Sans is unavailable, but
attempts to register a Devanagari-capable font for Hindi output.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate,
    Paragraph, Spacer, Table, TableStyle, KeepTogether,
)


# ---------------------------------------------------------------------------
# Font registration — try Noto Sans Devanagari, fall back to Helvetica.
# ---------------------------------------------------------------------------

_FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf", "NotoSansDevanagari"),
    ("/usr/share/fonts/noto/NotoSansDevanagari-Regular.ttf", "NotoSansDevanagari"),
    ("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", "NotoSans"),
    ("/usr/share/fonts/TTF/NotoSans-Regular.ttf", "NotoSans"),
]

_BOLD_CANDIDATES = [
    ("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf", "NotoSansDevanagari-Bold"),
    ("/usr/share/fonts/noto/NotoSansDevanagari-Bold.ttf", "NotoSansDevanagari-Bold"),
    ("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", "NotoSans-Bold"),
]

_BASE_FONT = "Helvetica"
_BOLD_FONT = "Helvetica-Bold"


def _register_fonts() -> None:
    global _BASE_FONT, _BOLD_FONT
    for path, name in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                _BASE_FONT = name
                break
            except Exception:
                continue
    for path, name in _BOLD_CANDIDATES:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                _BOLD_FONT = name
                break
            except Exception:
                continue


_register_fonts()


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def _make_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]
    return {
        "header": ParagraphStyle(
            "header", parent=base, fontName=_BOLD_FONT, fontSize=14,
            alignment=1, spaceAfter=8,
        ),
        "title": ParagraphStyle(
            "title", parent=base, fontName=_BOLD_FONT, fontSize=12,
            alignment=1, spaceAfter=14,
        ),
        "section_label": ParagraphStyle(
            "section_label", parent=base, fontName=_BOLD_FONT, fontSize=11,
            spaceBefore=8, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "body", parent=base, fontName=_BASE_FONT, fontSize=10,
            leading=14,
        ),
        "ground": ParagraphStyle(
            "ground", parent=base, fontName=_BASE_FONT, fontSize=10,
            leading=14, leftIndent=18, bulletIndent=4,
        ),
        "summary": ParagraphStyle(
            "summary", parent=base, fontName=_BASE_FONT, fontSize=10,
            leading=14, leftIndent=8, rightIndent=8, spaceBefore=4, spaceAfter=4,
        ),
        "disclaimer": ParagraphStyle(
            "disclaimer", parent=base, fontName=_BASE_FONT, fontSize=8,
            leading=11, textColor=colors.HexColor("#7a1010"),
            fontStyle="italic", leftIndent=8, rightIndent=8,
        ),
        "signature": ParagraphStyle(
            "signature", parent=base, fontName=_BASE_FONT, fontSize=10,
            spaceBefore=20,
        ),
        "footer": ParagraphStyle(
            "footer", parent=base, fontName=_BASE_FONT, fontSize=8,
            alignment=1, textColor=colors.grey,
        ),
    }


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _bordered_box(content: list, *, border_color=colors.black, padding=6) -> Table:
    """Wrap a flowable list in a single-cell bordered table."""
    tbl = Table([[content]], colWidths=[16 * cm])
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.7, border_color),
        ("LEFTPADDING", (0, 0), (-1, -1), padding),
        ("RIGHTPADDING", (0, 0), (-1, -1), padding),
        ("TOPPADDING", (0, 0), (-1, -1), padding),
        ("BOTTOMPADDING", (0, 0), (-1, -1), padding),
    ]))
    return tbl


def _applicant_block(report: dict, styles: dict, language: str) -> list:
    label_value_pairs = [
        ("Accused Name", report.get("accused_name") or "—"),
        ("FIR Number", report.get("fir_number") or "—"),
        ("Police Station", report.get("police_station") or "—"),
        ("Date of Arrest", report.get("date_of_arrest") or "—"),
        ("Days in Custody", str(report.get("days_in_custody", "—"))),
    ]
    if language == "hi":
        # Hindi labels for Devanagari rendering
        hi_labels = {
            "Accused Name": "अभियुक्त का नाम",
            "FIR Number": "एफआईआर संख्या",
            "Police Station": "थाना",
            "Date of Arrest": "गिरफ्तारी की तिथि",
            "Days in Custody": "हिरासत के दिन",
        }
        label_value_pairs = [(hi_labels[k], v) for k, v in label_value_pairs]

    data = [[Paragraph(f"<b>{label}</b>", styles["body"]), Paragraph(str(value), styles["body"])]
            for label, value in label_value_pairs]
    tbl = Table(data, colWidths=[5 * cm, 11 * cm])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [Paragraph("APPLICANT DETAILS", styles["section_label"]), tbl]


def _grounds_block(report: dict, styles: dict) -> list:
    grounds = report.get("bail_grounds") or []
    flowables = [Paragraph("GROUNDS FOR BAIL", styles["section_label"])]
    if not grounds:
        flowables.append(Paragraph("No automated grounds were identified.", styles["body"]))
        return flowables
    for i, g in enumerate(grounds, 1):
        flowables.append(Paragraph(f"{i}. {g}", styles["ground"]))
    return flowables


def _citations_block(report: dict, styles: dict) -> list:
    sections = report.get("applicable_sections") or []
    rows = [[Paragraph("<b>BNSS Section</b>", styles["body"]),
             Paragraph("<b>Applicability</b>", styles["body"])]]
    if sections:
        # Pair each applicable section with its corresponding ground description.
        grounds = report.get("bail_grounds") or []
        for sec, ground in zip(sections, grounds):
            rows.append([Paragraph(sec, styles["body"]), Paragraph(ground, styles["body"])])
        # If there are unmatched sections, list them.
        for sec in sections[len(grounds):]:
            rows.append([Paragraph(sec, styles["body"]),
                         Paragraph("See grounds above.", styles["body"])])
    else:
        rows.append([Paragraph("—", styles["body"]),
                     Paragraph("No statutory provisions auto-flagged.", styles["body"])])

    tbl = Table(rows, colWidths=[5 * cm, 11 * cm])
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [Paragraph("LEGAL CITATIONS", styles["section_label"]), tbl]


def _summary_block(report: dict, styles: dict) -> list:
    summary = report.get("plain_language_summary") or "(Summary unavailable.)"
    paragraphs = [Paragraph(p.replace("\n", "<br/>"), styles["summary"])
                  for p in summary.split("\n\n") if p.strip()]
    box = _bordered_box(paragraphs, border_color=colors.HexColor("#1f4e79"))
    return [Paragraph("AI-ASSISTED PLAIN-LANGUAGE SUMMARY", styles["section_label"]), box]


def _disclaimer_block(report: dict, styles: dict) -> list:
    text = report.get("disclaimer") or ""
    box = _bordered_box(
        [Paragraph(f"<i>{text}</i>", styles["disclaimer"])],
        border_color=colors.red,
    )
    return [Paragraph("DISCLAIMER", styles["section_label"]), box]


def _signature_block(styles: dict) -> list:
    return [
        Spacer(1, 0.6 * cm),
        Paragraph("Reviewed and approved by: _______________________ (Advocate)",
                  styles["signature"]),
        Paragraph("Date: ___________________  Bar Council Reg. No.: ___________________",
                  styles["body"]),
    ]


def _on_page_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(_BASE_FONT, 8)
    canvas.setFillColor(colors.grey)
    footer_text = (
        "Generated by Project Mukti — AI-assisted, human-reviewed. "
        "Not a substitute for legal counsel."
    )
    canvas.drawCentredString(A4[0] / 2, 1.2 * cm, footer_text)
    canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_bail_pdf(
    report: dict,
    output_path: str,
    *,
    court_name: str = "[Court Name]",
    language: str = "en",
    approved: bool = False,
) -> str:
    """
    Render a bail application PDF.
    `approved` enforces the human-in-the-loop gate — without it, no PDF is emitted.
    """
    if not approved:
        raise PermissionError(
            "PDF generation blocked: report has not been marked approved by a human reviewer."
        )

    output_path = str(output_path)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    styles = _make_styles()

    doc = BaseDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=f"Bail Application — {report.get('fir_number','')}",
        author="Project Mukti",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin,
                  doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=_on_page_footer)])

    story = []

    # 1. Header
    story.append(Paragraph(f"IN THE COURT OF {court_name.upper()}", styles["header"]))
    # 2. Title
    title_text = (
        "APPLICATION FOR BAIL UNDER SECTION 479/480 BNSS"
        if language == "en"
        else "धारा 479/480 बीएनएसएस के तहत जमानत के लिए आवेदन"
    )
    story.append(Paragraph(title_text, styles["title"]))

    # 3. Applicant block
    story.extend(_applicant_block(report, styles, language))
    story.append(Spacer(1, 0.4 * cm))

    # 4. Grounds
    story.extend(_grounds_block(report, styles))
    story.append(Spacer(1, 0.3 * cm))

    # 5. Citations table
    story.extend(_citations_block(report, styles))
    story.append(Spacer(1, 0.3 * cm))

    # 6. AI summary
    story.extend(_summary_block(report, styles))
    story.append(Spacer(1, 0.3 * cm))

    # 7. Disclaimer (red bordered)
    story.extend(_disclaimer_block(report, styles))

    # 8. Signature line
    story.extend(_signature_block(styles))

    doc.build(story)
    return output_path
