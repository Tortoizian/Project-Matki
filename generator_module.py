"""
Bail Application PDF generator for Project Mukti.

Produces a court-ready formal legal application (not a report) structured as:
  1. Court Header
  2. Case Title — State vs. Accused
  3. Application Clause
  4. Brief Facts
  5. Grounds for Bail
  6. Custody Proof
  7. Prayer
  8. Undertaking
  9. Verification / Signature Block

Serif fonts (FreeSerif -> DejaVu Serif -> Times-Roman) are used throughout
to match the appearance of a legal brief. All user-supplied text is sanitised
through _safe_text() before hitting ReportLab to prevent tofu characters.
"""

from __future__ import annotations

import logging
import os
import unicodedata
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, HRFlowable, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)

logger = logging.getLogger("mukti.generator")

# ---------------------------------------------------------------------------
# Unicode sanitiser
# ---------------------------------------------------------------------------

_UNICODE_MAP = str.maketrans({
    "‘": "'",   # left single quotation mark
    "’": "'",   # right single quotation mark
    "“": '"',   # left double quotation mark
    "”": '"',   # right double quotation mark
    "–": "-",   # en dash
    "—": "--",  # em dash
    "…": "...", # ellipsis
    " ": " ",   # non-breaking space
    "•": "*",   # bullet
    "‒": "-",   # figure dash
    "―": "--",  # horizontal bar
})


def _safe_text(text: str) -> str:
    if not text:
        return ""
    text = text.translate(_UNICODE_MAP)
    return unicodedata.normalize("NFKC", text)


# ---------------------------------------------------------------------------
# Font registration — serif first (legal brief look), Unicode-capable preferred
# ---------------------------------------------------------------------------

_SERIF_CANDIDATES = [
    ("/usr/share/fonts/truetype/freefont/FreeSerif.ttf",      "FreeSerif"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",      "DejaVuSerif"),
    ("/usr/share/fonts/dejavu/DejaVuSerif.ttf",               "DejaVuSerif"),
    ("/usr/share/fonts/TTF/DejaVuSerif.ttf",                  "DejaVuSerif"),
    ("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf", "LiberationSerif"),
]

_SERIF_BOLD_CANDIDATES = [
    ("/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",  "FreeSerif-Bold"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", "DejaVuSerif-Bold"),
    ("/usr/share/fonts/dejavu/DejaVuSerif-Bold.ttf",          "DejaVuSerif-Bold"),
    ("/usr/share/fonts/TTF/DejaVuSerif-Bold.ttf",             "DejaVuSerif-Bold"),
    ("/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf", "LiberationSerif-Bold"),
]

_SERIF_ITALIC_CANDIDATES = [
    ("/usr/share/fonts/truetype/freefont/FreeSerifItalic.ttf",      "FreeSerif-Italic"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",     "DejaVuSerif-Italic"),
    ("/usr/share/fonts/dejavu/DejaVuSerif-Italic.ttf",              "DejaVuSerif-Italic"),
    ("/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf", "LiberationSerif-Italic"),
]

# ReportLab built-in Times always exists — used as last resort
_BASE_FONT   = "Times-Roman"
_BOLD_FONT   = "Times-Bold"
_ITALIC_FONT = "Times-Italic"


def _try_register(candidates: list[tuple[str, str]]) -> Optional[str]:
    for path, name in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                return name
            except Exception as exc:
                logger.warning("Font load failed %s: %s", path, exc)
    return None


def _register_fonts() -> None:
    global _BASE_FONT, _BOLD_FONT, _ITALIC_FONT
    reg = _try_register(_SERIF_CANDIDATES)
    if reg:
        _BASE_FONT = reg
        logger.info("PDF serif font: %s", reg)
    else:
        logger.warning("No serif TTF found — using Times-Roman (ASCII only). "
                       "Install fonts-freefont-ttf for best results.")

    reg_bold = _try_register(_SERIF_BOLD_CANDIDATES)
    if reg_bold:
        _BOLD_FONT = reg_bold

    reg_italic = _try_register(_SERIF_ITALIC_CANDIDATES)
    if reg_italic:
        _ITALIC_FONT = reg_italic


_register_fonts()


# ---------------------------------------------------------------------------
# Style sheet
# ---------------------------------------------------------------------------

def _make_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]

    def S(name, **kw) -> ParagraphStyle:
        return ParagraphStyle(name, parent=base, **kw)

    return {
        # Court name centred, bold, underlined via HTML tag in caller
        "court_header": S("court_header",
            fontName=_BOLD_FONT, fontSize=13, alignment=TA_CENTER,
            spaceAfter=4, leading=18),

        # "State vs. Accused" centred
        "case_title": S("case_title",
            fontName=_BOLD_FONT, fontSize=12, alignment=TA_CENTER,
            spaceBefore=6, spaceAfter=6, leading=16),

        # Sub-line under case title (FIR details etc.)
        "case_subtitle": S("case_subtitle",
            fontName=_BASE_FONT, fontSize=10, alignment=TA_CENTER,
            spaceAfter=10, leading=14),

        # Section number + heading (e.g. "3. BRIEF FACTS")
        "section_heading": S("section_heading",
            fontName=_BOLD_FONT, fontSize=11,
            spaceBefore=14, spaceAfter=4, leading=15,
            underlineProportion=0),

        # Normal justified body text
        "body": S("body",
            fontName=_BASE_FONT, fontSize=10.5,
            leading=15, alignment=TA_JUSTIFY),

        # Indented ground items
        "ground_item": S("ground_item",
            fontName=_BASE_FONT, fontSize=10.5,
            leading=15, alignment=TA_JUSTIFY,
            leftIndent=22, firstLineIndent=-14),

        # Prayer lines — slightly larger, justified
        "prayer_body": S("prayer_body",
            fontName=_BASE_FONT, fontSize=10.5,
            leading=16, alignment=TA_JUSTIFY,
            leftIndent=18),

        # Italic for undertaking / verification text
        "italic_body": S("italic_body",
            fontName=_ITALIC_FONT, fontSize=10,
            leading=14, alignment=TA_JUSTIFY),

        # Disclaimer — small red italic
        "disclaimer": S("disclaimer",
            fontName=_ITALIC_FONT, fontSize=8.5,
            leading=12, textColor=colors.HexColor("#7a1010"),
            alignment=TA_JUSTIFY),

        # Signature blanks
        "signature": S("signature",
            fontName=_BASE_FONT, fontSize=10.5,
            spaceBefore=18, leading=15),

        # Footer
        "footer": S("footer",
            fontName=_BASE_FONT, fontSize=8,
            alignment=TA_CENTER, textColor=colors.grey),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _section_heading(number: int, title: str, styles: dict) -> list:
    return [
        Spacer(1, 0.15 * cm),
        HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#aaaaaa")),
        Paragraph(f"{number}. {title}", styles["section_heading"]),
    ]


def _on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont(_BASE_FONT, 8)
    canvas.setFillColor(colors.grey)
    canvas.drawCentredString(
        A4[0] / 2, 1.1 * cm,
        "Generated by Project Mukti -- AI-assisted, must be reviewed by a licensed advocate before filing."
    )
    canvas.drawRightString(A4[0] - 2 * cm, 1.1 * cm, f"Page {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# 9 Section builders
# ---------------------------------------------------------------------------

def _s1_court_header(court_name: str, styles: dict) -> list:
    """Section 1 — Court name."""
    court = _safe_text(court_name)
    return [
        Paragraph(f"<u>IN THE COURT OF THE LEARNED {court.upper()}</u>",
                  styles["court_header"]),
    ]


def _s2_case_title(report: dict, styles: dict) -> list:
    """Section 2 — State vs. Accused + FIR details."""
    accused = _safe_text(report.get("accused_name") or "Unknown Accused")
    fir     = _safe_text(report.get("fir_number") or "—")
    ps      = _safe_text(report.get("police_station") or "—")
    sections = ", ".join(report.get("sections_charged") or
                         report.get("applicable_sections") or [])
    sections = _safe_text(sections) or "—"

    is_nb   = bool(report.get("is_non_bailable_case", False))
    nb_tag  = " [NON-BAILABLE]" if is_nb else ""

    return [
        Spacer(1, 0.2 * cm),
        Paragraph(f"STATE  vs.  {accused.upper()}{nb_tag}", styles["case_title"]),
        Paragraph(
            f"FIR No.: {fir}  |  Police Station: {ps}  |  Sections: {sections}",
            styles["case_subtitle"],
        ),
        HRFlowable(width="100%", thickness=1.2, color=colors.black),
        Spacer(1, 0.2 * cm),
    ]


def _s3_application_clause(report: dict, styles: dict) -> list:
    """Section 3 — Formal application opening clause."""
    accused  = _safe_text(report.get("accused_name") or "the accused/applicant")
    fir      = _safe_text(report.get("fir_number") or "—")
    deadline = int(report.get("statutory_deadline", 60))
    is_nb    = bool(report.get("is_non_bailable_case", False))

    if is_nb:
        basis = (
            f"Section 437 and Section 187 of the Bharatiya Nagarik Suraksha Sanhita, 2023 "
            f"(BNSS), the charge sheet having not been filed within the mandatory "
            f"{deadline}-day period"
        )
    else:
        basis = "Section 480/483 of the Bharatiya Nagarik Suraksha Sanhita, 2023 (BNSS)"

    text = (
        f"Most respectfully showeth that the present application is being filed on behalf "
        f"of {accused}, the accused/applicant in the above-captioned matter (FIR No. {fir}), "
        f"under {basis}. "
        f"The applicant is currently in judicial/police custody and seeks "
        f"{'bail/default bail' if is_nb else 'bail'} on the grounds set out hereunder."
    )
    return (
        _section_heading(3, "APPLICATION CLAUSE", styles)
        + [Paragraph(text, styles["body"])]
    )


def _s4_brief_facts(report: dict, styles: dict) -> list:
    """Section 4 — Brief facts of arrest and case."""
    accused  = _safe_text(report.get("accused_name") or "The accused")
    fir      = _safe_text(report.get("fir_number") or "—")
    ps       = _safe_text(report.get("police_station") or "the concerned police station")
    doa      = _safe_text(report.get("date_of_arrest") or "an unknown date")
    days     = int(report.get("days_in_custody") or 0)
    cs_filed = bool(report.get("charge_sheet_filed", False))
    narrative = _safe_text(report.get("case_narrative") or "")

    cs_status = (
        "The charge sheet has been filed in this matter."
        if cs_filed
        else "The charge sheet has NOT been filed in this matter as of the date of this application."
    )

    paras = [
        f"That {accused} was arrested on {doa} by {ps} in connection with FIR No. {fir}. "
        f"Since the date of arrest, the applicant has been in continuous judicial/police custody "
        f"for approximately {days} day(s).",

        f"That {cs_status}",
    ]
    if narrative:
        paras.append(f"That the background facts of the case, as extracted from the FIR, are as follows: "
                     f"{narrative[:800]}")

    return (
        _section_heading(4, "BRIEF FACTS", styles)
        + [Paragraph(p, styles["body"]) for p in paras]
    )


def _s5_grounds(report: dict, styles: dict) -> list:
    """Section 5 — Numbered grounds for bail."""
    grounds  = report.get("bail_grounds") or []
    is_nb    = bool(report.get("is_non_bailable_case", False))
    deadline = int(report.get("statutory_deadline", 60))

    flowables = _section_heading(5, "GROUNDS FOR BAIL", styles)

    if not grounds:
        flowables.append(Paragraph(
            "No automated statutory grounds were identified. A qualified advocate should "
            "assess the facts and identify applicable grounds.",
            styles["body"],
        ))
        return flowables

    for i, g in enumerate(grounds, 1):
        flowables.append(Paragraph(f"({i})  {_safe_text(g)}", styles["ground_item"]))

    if is_nb:
        flowables.append(Paragraph(
            f"({len(grounds)+1})  That even in cases involving non-bailable offences, "
            f"Section 187 BNSS confers an indefeasible right to default bail where the "
            f"charge sheet is not filed within the mandatory {deadline}-day period. "
            f"This right cannot be curtailed on account of the gravity of the alleged offence "
            f"(Ref: Satender Kumar Antil v. CBI, 2022 SCC).",
            styles["ground_item"],
        ))

    return flowables


def _s6_custody_proof(report: dict, styles: dict) -> list:
    """Section 6 — Clear custody-duration statement."""
    accused  = _safe_text(report.get("accused_name") or "The accused")
    doa      = _safe_text(report.get("date_of_arrest") or "—")
    days     = int(report.get("days_in_custody") or 0)
    deadline = int(report.get("statutory_deadline", 60))
    cs_filed = bool(report.get("charge_sheet_filed", False))

    exceeded = days > deadline and not cs_filed
    exceeded_by = days - deadline if exceeded else 0

    lines = [
        f"That {accused} has been in continuous custody since {doa}, "
        f"amounting to <b>{days} day(s)</b> as on the date of this application.",
    ]
    if exceeded:
        lines.append(
            f"That the statutory deadline for filing the charge sheet under Section 187 BNSS "
            f"was <b>{deadline} days</b>. The said deadline has been exceeded by "
            f"<b>{exceeded_by} day(s)</b> without any charge sheet being filed, "
            f"entitling the applicant to default bail as a matter of right."
        )

    return (
        _section_heading(6, "PERIOD OF CUSTODY", styles)
        + [Paragraph(ln, styles["body"]) for ln in lines]
    )


def _s7_prayer(report: dict, styles: dict) -> list:
    """Section 7 — The formal Prayer."""
    accused  = _safe_text(report.get("accused_name") or "the applicant")
    is_nb    = bool(report.get("is_non_bailable_case", False))
    days     = int(report.get("days_in_custody") or 0)
    deadline = int(report.get("statutory_deadline", 60))
    cs_filed = bool(report.get("charge_sheet_filed", False))

    if is_nb and not cs_filed and days > deadline:
        # Default bail prayer — statutory right cannot be denied
        specific_prayer = (
            f"(i)  grant default bail to {accused} forthwith under Section 187 BNSS, "
            f"the charge sheet having not been filed within the mandatory {deadline}-day "
            f"statutory period, rendering continued detention unlawful;"
        )
        alt = (
            "(ii)  in the alternative, if default bail is not granted, release the applicant "
            "on such bail and such terms and conditions as this Hon'ble Court may deem fit "
            "and proper in the circumstances of the case;"
        )
    elif is_nb:
        specific_prayer = (
            f"(i)  release {accused} on bail under Section 437/439 BNSS on such terms "
            f"and conditions as this Hon'ble Court may deem fit, taking into account "
            f"the grounds of personal liberty and the facts stated above;"
        )
        alt = (
            "(ii)  in the alternative, release the applicant on personal/surety bond "
            "of a reasonable amount pending trial;"
        )
    else:
        specific_prayer = (
            f"(i)  release {accused} on bail under Section 436/480 BNSS, bail being a "
            f"matter of right in respect of the bailable offences charged in this FIR;"
        )
        alt = (
            "(ii)  in the alternative, release the applicant on such bail and such terms "
            "and conditions as this Hon'ble Court may deem fit and proper;"
        )

    preamble = (
        "It is, therefore, most humbly prayed that this Hon'ble Court may be pleased to:"
    )
    relief_3 = (
        "(iii)  pass any other order or direction as this Hon'ble Court may deem fit "
        "and proper in the interest of justice."
    )

    return (
        _section_heading(7, "PRAYER", styles)
        + [
            Paragraph(preamble, styles["body"]),
            Spacer(1, 0.2 * cm),
            Paragraph(specific_prayer, styles["prayer_body"]),
            Paragraph(alt,            styles["prayer_body"]),
            Paragraph(relief_3,       styles["prayer_body"]),
            Spacer(1, 0.15 * cm),
            Paragraph("And for this act of kindness the applicant shall ever pray.",
                      styles["italic_body"]),
        ]
    )


def _s8_undertaking(styles: dict) -> list:
    """Section 8 — Undertaking by the accused / surety."""
    lines = [
        "The applicant/accused hereby undertakes that, if released on bail:",
        "(i)  He/She shall not directly or indirectly make any inducement, threat, or "
             "promise to any person acquainted with the facts of the case, and shall not "
             "tamper with evidence in any manner.",
        "(ii)  He/She shall not leave the jurisdiction of this Court without prior "
              "written permission of this Hon'ble Court.",
        "(iii) He/She shall appear before the concerned police station/court on every "
               "date of hearing and whenever so directed.",
        "(iv)  He/She shall surrender his/her passport, if any, to this Hon'ble Court.",
    ]
    return (
        _section_heading(8, "UNDERTAKING", styles)
        + [Paragraph(ln, styles["body" if i == 0 else "ground_item"])
           for i, ln in enumerate(lines)]
    )


def _s9_verification(report: dict, styles: dict) -> list:
    """Section 9 — Verification, AI disclaimer, and signature block."""
    accused  = _safe_text(report.get("accused_name") or "the accused")
    fir      = _safe_text(report.get("fir_number") or "—")
    disclaimer = _safe_text(report.get("disclaimer") or
        "This document was generated with AI assistance and identifies potentially "
        "applicable legal provisions for review by a licensed advocate. It does NOT "
        "constitute legal advice. All applications must be reviewed and signed by a "
        "qualified legal professional before filing. Visit the District Legal Services "
        "Authority (DLSA / Zila Vidhi Seva Pradhikaran) for free legal aid.")

    sig_table_data = [
        [
            Paragraph("<b>Deponent / Petitioner</b>", styles["body"]),
            Paragraph("<b>Counsel for the Applicant</b>", styles["body"]),
        ],
        [
            Paragraph(f"{accused}\n(Signature / Thumb Impression)", styles["body"]),
            Paragraph("_______________________________\n(Advocate's Signature)",
                      styles["body"]),
        ],
        [
            Paragraph("Place: _______________\nDate:  _______________", styles["body"]),
            Paragraph("Name: _______________\nBar Council Enrolment No.: _______________\n"
                      "Mobile: _______________", styles["body"]),
        ],
    ]
    sig_tbl = Table(sig_table_data, colWidths=[8 * cm, 8 * cm])
    sig_tbl.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEABOVE",     (0, 1), (-1, 1), 0.5, colors.grey),
    ]))

    verification_text = (
        f"I, {accused}, do hereby verify that the contents of the foregoing application "
        f"in FIR No. {fir} are true and correct to the best of my knowledge and belief "
        f"and nothing material has been concealed therefrom."
    )

    return (
        _section_heading(9, "VERIFICATION & SIGNATURE", styles)
        + [
            Paragraph(verification_text, styles["italic_body"]),
            Spacer(1, 0.5 * cm),
            sig_tbl,
            Spacer(1, 0.6 * cm),
            HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#aaaaaa")),
            Spacer(1, 0.2 * cm),
            Paragraph(f"<i>{disclaimer}</i>", styles["disclaimer"]),
        ]
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_bail_pdf(
    report: dict,
    output_path: str,
    *,
    court_name: str = "the Learned Metropolitan Magistrate / Sessions Judge",
    language: str = "en",
    approved: bool = False,
) -> str:
    """
    Render a formal court bail application PDF.
    `approved=True` is required — enforces the human-in-the-loop gate.
    """
    if not approved:
        raise PermissionError(
            "PDF generation blocked: report has not been approved by a human reviewer."
        )

    output_path = str(output_path)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    styles = _make_styles()

    doc = BaseDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=2.5 * cm, rightMargin=2.5 * cm,
        topMargin=2.2 * cm,  bottomMargin=2.2 * cm,
        title=f"Bail Application - {report.get('fir_number', '')}",
        author="Project Mukti",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin,
                  doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=_on_page)])

    story: list = []

    # Section 1 — Court Header
    story += _s1_court_header(court_name, styles)

    # Section 2 — Case Title
    story += _s2_case_title(report, styles)

    # Section 3 — Application Clause
    story += _s3_application_clause(report, styles)

    # Section 4 — Brief Facts
    story += _s4_brief_facts(report, styles)

    # Section 5 — Grounds for Bail
    story += _s5_grounds(report, styles)

    # Section 6 — Custody Proof
    story += _s6_custody_proof(report, styles)

    # Section 7 — Prayer
    story += _s7_prayer(report, styles)

    # Section 8 — Undertaking
    story += _s8_undertaking(styles)

    # Section 9 — Verification & Signature Block
    story += _s9_verification(report, styles)

    doc.build(story)
    return output_path
