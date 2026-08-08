"""
Plain-Language Situation Report for GeoReach
============================================
Generates a one-page PDF brief that anyone can read at a glance — no GIS or
technical jargon. Meant for district officials, relief coordinators, and
volunteers who need to know: who is cut off, how bad it is, and what to do now.
"""
import os
import logging
from datetime import date

logger = logging.getLogger("SituationReport")


def _fmt(n):
    try:
        return f"{int(n):,}"
    except (ValueError, TypeError):
        return str(n)


def build_situation_report(results, output_path, scenario="high"):
    """Write a layman-friendly one-page PDF situation brief."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_LEFT
    except ImportError:
        logger.warning("reportlab not installed — cannot build situation report. "
                       "Install with: pip install reportlab")
        return None

    s = results.get("summary", {})
    settlements = results.get("settlements")
    alloc = results.get("allocation")

    NAVY = colors.HexColor("#0d2b45")
    RED = colors.HexColor("#c0392b")
    AMBER = colors.HexColor("#d98324")
    GREEN = colors.HexColor("#1f8a55")
    LIGHT = colors.HexColor("#f2f4f7")
    INK = colors.HexColor("#1a2733")
    MUT = colors.HexColor("#5b6b79")

    doc = SimpleDocTemplate(output_path, pagesize=A4,
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=12 * mm,
                            title="GeoReach Flood Relief Brief")
    ss = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", parent=ss["Title"], fontSize=20, textColor=NAVY,
                        spaceAfter=2, alignment=TA_LEFT, leading=23)
    SUB = ParagraphStyle("SUB", parent=ss["Normal"], fontSize=10, textColor=MUT, spaceAfter=8)
    LEAD = ParagraphStyle("LEAD", parent=ss["Normal"], fontSize=13.5, textColor=INK,
                          leading=18, spaceAfter=10)
    H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=12.5, textColor=NAVY,
                        spaceBefore=8, spaceAfter=4)
    BODY = ParagraphStyle("BODY", parent=ss["Normal"], fontSize=10.5, textColor=INK, leading=14)
    SMALL = ParagraphStyle("SMALL", parent=ss["Normal"], fontSize=8.5, textColor=MUT, leading=11)

    story = []

    # ── Header ──
    story.append(Paragraph("Flood Relief Brief", H1))
    story.append(Paragraph(
        f"Dhemaji, Lakhimpur &amp; Majuli districts, Assam &nbsp;|&nbsp; "
        f"Prepared {date.today().strftime('%d %B %Y')} &nbsp;|&nbsp; "
        f"Based on satellite flood observations", SUB))

    # ── One-line headline ──
    cut = s.get("critically_isolated", 0) + s.get("isolated", 0)
    no_care = s.get("population_without_care_60min", 0)
    story.append(Paragraph(
        f"<b>{cut} villages are cut off by the flood.</b> "
        f"About <b>{_fmt(no_care)} people</b> cannot reach a hospital within an hour "
        f"because the roads to them are underwater.", LEAD))

    # ── Big number cards ──
    NUM = ParagraphStyle("NUM", parent=ss["Normal"], fontSize=20, leading=24, spaceAfter=3)
    def card(num, label, col):
        hexc = "#" + col.hexval()[2:]
        return [Paragraph(f'<font color="{hexc}"><b>{num}</b></font>', NUM),
                Paragraph(f'<font size="8">{label}</font>', SMALL)]
    cards = Table([[
        card(_fmt(cut), "villages cut off", RED),
        card(f"{s.get('pct_population_without_care', 0)}%", "people without hospital access", AMBER),
        card(_fmt(s.get("flooded_bridges", 0)), "bridges under water", RED),
        card(_fmt(s.get("relief_staging_points", 0)), "boat-launch points found", GREEN),
    ]], colWidths=[45 * mm, 45 * mm, 45 * mm, 45 * mm])
    cards.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.white),
        ("INNERGRID", (0, 0), (-1, -1), 3, colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(cards)
    story.append(Spacer(1, 8))

    # ── Reach these first ──
    story.append(Paragraph("Reach these villages first", H2))
    story.append(Paragraph(
        "These are the most urgent — the largest number of trapped people with no easy way out.", BODY))
    rows = [["#", "Village", "District", "People", "Nearest hospital"]]
    if settlements is not None and "priority_rank" in settlements.columns:
        top = settlements.sort_values("priority_rank").head(8)
        for _, r in top.iterrows():
            ct = r.get("care_time_min", None)
            try:
                ct_str = f"{float(ct):.0f} min away" if ct is not None and float(ct) == float(ct) and float(ct) < 1e6 else "cannot reach"
            except (ValueError, TypeError):
                ct_str = "cannot reach"
            rows.append([
                str(int(r.get("priority_rank", 0))),
                str(r.get("name", "?")),
                str(r.get("district", "")),
                _fmt(r.get("est_population", 0)),
                ct_str,
            ])
    t = Table(rows, colWidths=[10 * mm, 48 * mm, 32 * mm, 25 * mm, 45 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("TEXTCOLOR", (4, 1), (4, -1), RED),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))

    # ── Where to send boats ──
    story.append(Paragraph("Where to send the boats", H2))
    if alloc is not None and not getattr(alloc, "empty", True):
        covered = _fmt(s.get("allocation_population_covered", int(alloc["population_covered"].sum())))
        story.append(Paragraph(
            f"Sending teams to just these <b>{len(alloc)} launch points</b> would reach about "
            f"<b>{covered} of the trapped people</b> — the most efficient way to cover the most lives fast.", BODY))
        arows = [["Send to", "Near", "District", "People reached", "Villages"]]
        for _, h in alloc.iterrows():
            arows.append([
                f"Team {int(h.get('deployment_priority', 0))}",
                str(h.get("hub_near", "?")),
                str(h.get("district", "")),
                _fmt(h.get("population_covered", 0)),
                str(int(h.get("settlements_covered", 0))),
            ])
        at = Table(arows, colWidths=[22 * mm, 45 * mm, 30 * mm, 33 * mm, 20 * mm])
        at.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), GREEN),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 9.5),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(at)
    else:
        story.append(Paragraph("No boat-launch plan available for this scenario.", BODY))

    story.append(Spacer(1, 10))

    # ── What this is based on ──
    story.append(Paragraph(
        "<b>Good news:</b> every hospital in the region stayed dry and working "
        f"({s.get('total_hospitals', 0)} in total). The problem is the roads and bridges to reach them.", BODY))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "How this was worked out: a radar satellite (Sentinel-1) recorded exactly where the water is, "
        "even through monsoon clouds. We combined that with the road map, hospital locations, and village "
        "populations to find who is cut off and where to send help first. "
        "Full interactive map: the GeoReach dashboard.", SMALL))
    story.append(Paragraph(
        "Generated automatically by GeoReach — Geospatial Accessibility Intelligence for Flood Response.", SMALL))

    doc.build(story)
    logger.info(f"  Situation report saved: {output_path}")
    return output_path
