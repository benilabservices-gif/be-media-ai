"""PDF du prompt de conception (fpdf2, pur Python : rien à installer sur le serveur)."""

from datetime import datetime

from fpdf import FPDF
from fpdf.enums import XPos, YPos

# Les polices intégrées au PDF couvrent le Latin-1 (accents français compris) : les quelques
# signes typographiques hors Latin-1 sont remplacés par leur équivalent simple
_REPLACEMENTS = str.maketrans(
    {
        chr(0x2019): "'",  # apostrophe typographique
        chr(0x2018): "'",
        chr(0x201C): '"',
        chr(0x201D): '"',
        chr(0x2014): "-",  # tiret cadratin
        chr(0x2013): "-",  # tiret demi-cadratin
        chr(0x2026): "...",
        chr(0x0153): "oe",
        chr(0x0152): "OE",
        chr(0x202F): " ",  # espace fine insécable (séparateur des milliers en français)
        chr(0x20AC): "EUR",
    }
)


def _latin1(text: str) -> str:
    return text.translate(_REPLACEMENTS).encode("latin-1", "replace").decode("latin-1")


def _paragraph(pdf: FPDF, height: float, text: str) -> None:
    # Retour à la marge gauche après chaque bloc (par défaut, fpdf2 reste à droite du texte)
    pdf.multi_cell(0, height, _latin1(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def render_prompt_pdf(*, title: str, subtitle: str, prompt: str) -> bytes:
    """Une section par paragraphe du prompt ; sa première ligne (en capitales) sert de titre."""
    pdf = FPDF(format="A4")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_title(_latin1(title))
    pdf.set_author("BENILAB Digital360")
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 15)
    _paragraph(pdf, 8, title)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(110, 110, 110)
    _paragraph(pdf, 5, subtitle)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    for section in prompt.strip().split("\n\n"):
        heading, _, body = section.partition("\n")
        pdf.set_font("Helvetica", "B", 10.5)
        _paragraph(pdf, 6, heading)
        if body:
            pdf.set_font("Helvetica", "", 10)
            _paragraph(pdf, 5.2, body)
        pdf.ln(3)

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(130, 130, 130)
    _paragraph(pdf, 4, f"Généré le {datetime.now():%d/%m/%Y à %H:%M} - BENILAB Digital360")
    return bytes(pdf.output())
