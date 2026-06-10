"""PDF report generation for press review digests."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fpdf import FPDF

_FONTS_DIR = Path(__file__).parent / "fonts"

_RO_MONTHS = [
    "", "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie",
    "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie",
]


def _period_label(date_start: str, date_end: str, period: str) -> str:
    """Human-readable Romanian period label for the report header."""
    if period == "all":
        if date_start and date_end and date_start != date_end:
            ys, ms, ds = (int(x) for x in date_start.split("-"))
            ye, me, de = (int(x) for x in date_end.split("-"))
            return f"{ds} {_RO_MONTHS[ms]} {ys} \u2013 {de} {_RO_MONTHS[me]} {ye}"
        return "Toate articolele"
    y, m, d = (int(x) for x in date_start.split("-"))
    if period == "day":
        return f"{d} {_RO_MONTHS[m]} {y}"
    if period == "month":
        return f"{_RO_MONTHS[m].capitalize()} {y}"
    # week
    ey, em, ed = (int(x) for x in date_end.split("-"))
    if m == em:
        return f"{d} - {ed} {_RO_MONTHS[m]} {y}"
    return f"{d} {_RO_MONTHS[m]} - {ed} {_RO_MONTHS[em]} {y}"


class _Report(FPDF):
    def __init__(self, period_label: str, generated_at: str):
        super().__init__()
        self._period_label = period_label
        self._generated_at = generated_at
        # Register DejaVu Sans (bundled in app/fonts/) — full Unicode, covers all Romanian diacritics
        self.add_font("DejaVu", style="",  fname=str(_FONTS_DIR / "DejaVuSans.ttf"))
        self.add_font("DejaVu", style="B", fname=str(_FONTS_DIR / "DejaVuSans-Bold.ttf"))
        self.add_font("DejaVu", style="I", fname=str(_FONTS_DIR / "DejaVuSans-Oblique.ttf"))

    def header(self):
        self.set_font("DejaVu", "B", 9)
        self.set_text_color(60, 60, 60)
        self.cell(0, 8, "AIRI@UTCN \u2014 Revista Presei", align="L")
        self.set_font("DejaVu", "", 8)
        self.set_text_color(140, 140, 140)
        self.cell(0, 8, self._period_label, align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(220, 220, 220)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3)

    def footer(self):
        self.set_y(-13)
        self.set_font("DejaVu", "", 7)
        self.set_text_color(160, 160, 160)
        self.cell(0, 8, f"Generat: {self._generated_at}   |   Pagina {self.page_no()}", align="C")


def build_pdf(digest: dict[str, Any]) -> bytes:
    """Build a PDF from a digest dict (same schema as /api/digest).

    Returns raw PDF bytes.
    """
    period = digest.get("period", "day")
    date_start = digest.get("date_start", digest.get("date", ""))
    date_end   = digest.get("date_end",   digest.get("date", ""))
    period_lbl = _period_label(date_start, date_end, period)

    generated_at = datetime.now(tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    pdf = _Report(period_lbl, generated_at)
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    # Title block
    pdf.set_font("DejaVu", "B", 18)
    pdf.set_text_color(17, 19, 24)
    pdf.cell(0, 10, "Revista Presei", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("DejaVu", "", 12)
    pdf.set_text_color(90, 96, 112)
    pdf.cell(0, 7, period_lbl, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    total = digest.get("total_articles", 0)
    n_sources = len(digest.get("entries", []))
    pdf.set_font("DejaVu", "", 9)
    pdf.set_text_color(140, 140, 140)
    pdf.cell(0, 6, f"{total} articole din {n_sources} surse", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # Entries
    for entry in digest.get("entries", []):
        source = entry.get("source", "")
        articles = entry.get("articles", [])

        # Source heading
        pdf.set_fill_color(245, 246, 248)
        pdf.set_draw_color(220, 220, 220)
        pdf.set_font("DejaVu", "B", 10)
        pdf.set_text_color(17, 19, 24)
        pdf.cell(0, 8, f"  {source}  ({len(articles)})", fill=True,
                 border="LRB", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        for art in articles:
            url = art.get("url", "")

            # Article title — clickable link
            pdf.set_font("DejaVu", "B", 9)
            pdf.set_text_color(26, 110, 248)
            pdf.multi_cell(0, 5, art.get("title", ""),
                           link=url, new_x="LMARGIN", new_y="NEXT")

            # Date
            pub = art.get("published_date", "")[:10]
            if pub:
                pdf.set_font("DejaVu", "", 7.5)
                pdf.set_text_color(154, 160, 176)
                pdf.cell(0, 4, pub, new_x="LMARGIN", new_y="NEXT")

            # Snippet
            snippet = art.get("snippet", "")
            if snippet:
                pdf.set_font("DejaVu", "", 8)
                pdf.set_text_color(90, 96, 112)
                pdf.multi_cell(0, 4.5, snippet[:220] + ("\u2026" if len(snippet) > 220 else ""),
                               new_x="LMARGIN", new_y="NEXT")

            # Keywords + persons tags
            kws     = art.get("matched_keywords", [])
            persons = art.get("matched_persons", [])
            tags = [f"[{k}]" for k in kws] + [f"@{p}" for p in persons]
            if tags:
                pdf.set_font("DejaVu", "I", 7.5)
                pdf.set_text_color(100, 100, 140)
                pdf.cell(0, 4, "  ".join(tags), new_x="LMARGIN", new_y="NEXT")

            # URL — truncated display, full URL as clickable link
            if url:
                pdf.set_font("DejaVu", "", 7)
                pdf.set_text_color(100, 130, 200)
                display = url[:90] + ("\u2026" if len(url) > 90 else "")
                pdf.cell(0, 4, display, link=url, new_x="LMARGIN", new_y="NEXT")

            pdf.ln(3)
            # Thin separator between articles
            pdf.set_draw_color(235, 235, 235)
            pdf.line(pdf.l_margin + 4, pdf.get_y(), pdf.w - pdf.r_margin - 4, pdf.get_y())
            pdf.ln(3)

        pdf.ln(4)

    return bytes(pdf.output())
