import argparse
import os
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
)


PROJECT_DIR = Path(__file__).resolve().parent

DEFAULT_INPUT = (
    PROJECT_DIR / "podcast_final_text_verified.txt"
)

DEFAULT_OUTPUT = (
    PROJECT_DIR / "readaloud_document.pdf"
)


def find_font():
    """
    Türkçe karakterleri destekleyen bir Windows fontu bulur.
    """

    possible_fonts = [
        Path(
            "C:/Windows/Fonts/segoeui.ttf"
        ),
        Path(
            "C:/Windows/Fonts/arial.ttf"
        ),
        Path(
            "C:/Windows/Fonts/calibri.ttf"
        ),
        Path(
            "C:/Windows/Fonts/DejaVuSans.ttf"
        ),
        Path(
            "C:/Windows/Fonts/NotoSans-Regular.ttf"
        ),
    ]

    for font_path in possible_fonts:
        if font_path.exists():
            return font_path

    raise FileNotFoundError(
        "Türkçe karakterleri destekleyen bir font bulunamadı. "
        "Segoe UI, Arial, Calibri, DejaVu Sans veya "
        "Noto Sans fontlarından birini yükleyin."
    )


def add_page_number(canvas, document):
    """
    Her PDF sayfasının altına sayfa numarası ekler.
    """

    canvas.saveState()

    canvas.setFont(
        "DocumentFont",
        9,
    )

    page_number = canvas.getPageNumber()

    canvas.drawCentredString(
        A4[0] / 2,
        12 * mm,
        str(page_number),
    )

    canvas.restoreState()


def create_pdf(
    input_file,
    output_file,
    title,
):
    """
    UTF-8 TXT dosyasını Edge Read Aloud ile uyumlu,
    metin tabanlı bir PDF'e dönüştürür.
    """

    input_file = Path(input_file)
    output_file = Path(output_file)

    if not input_file.exists():
        raise FileNotFoundError(
            f"TXT dosyası bulunamadı: {input_file}"
        )

    source_text = input_file.read_text(
        encoding="utf-8-sig"
    )

    if not source_text.strip():
        raise ValueError(
            "TXT dosyası boş."
        )

    font_path = find_font()

    pdfmetrics.registerFont(
        TTFont(
            "DocumentFont",
            str(font_path),
        )
    )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_file = output_file.with_suffix(
        output_file.suffix + ".tmp"
    )

    document = BaseDocTemplate(
        str(temporary_file),
        pagesize=A4,
        leftMargin=22 * mm,
        rightMargin=22 * mm,
        topMargin=20 * mm,
        bottomMargin=22 * mm,
        title=title,
        author="PDF to Podcast",
        subject="Read Aloud için hazırlanmış metin",
    )

    frame = Frame(
        document.leftMargin,
        document.bottomMargin,
        document.width,
        document.height,
        id="main_frame",
    )

    page_template = PageTemplate(
        id="readaloud_template",
        frames=[frame],
        onPage=add_page_number,
    )

    document.addPageTemplates(
        [page_template]
    )

    title_style = ParagraphStyle(
        name="Title",
        fontName="DocumentFont",
        fontSize=18,
        leading=24,
        alignment=TA_CENTER,
        spaceAfter=14 * mm,
    )

    heading_style = ParagraphStyle(
        name="Heading",
        fontName="DocumentFont",
        fontSize=14,
        leading=19,
        spaceBefore=7 * mm,
        spaceAfter=4 * mm,
        keepWithNext=True,
    )

    body_style = ParagraphStyle(
        name="Body",
        fontName="DocumentFont",
        fontSize=11.5,
        leading=18,
        alignment=TA_JUSTIFY,
        spaceAfter=4 * mm,
        splitLongWords=False,
        allowWidows=False,
        allowOrphans=False,
    )

    bullet_style = ParagraphStyle(
        name="Bullet",
        parent=body_style,
        leftIndent=7 * mm,
        firstLineIndent=-4 * mm,
        bulletIndent=2 * mm,
        spaceAfter=4 * mm,
    )

    story = []

    story.append(
        Paragraph(
            escape(title),
            title_style,
        )
    )

    paragraphs = source_text.replace(
        "\r\n",
        "\n",
    ).replace(
        "\r",
        "\n",
    ).split(
        "\n\n"
    )

    for paragraph in paragraphs:
        paragraph = paragraph.strip()

        if not paragraph:
            continue

        lines = [
            line.strip()
            for line in paragraph.splitlines()
            if line.strip()
        ]

        paragraph = " ".join(lines)

        if paragraph.startswith("- "):
            bullet_text = paragraph[2:].strip()

            story.append(
                Paragraph(
                    escape(bullet_text),
                    bullet_style,
                    bulletText="•",
                )
            )

            continue

        letters = [
            character
            for character in paragraph
            if character.isalpha()
        ]

        uppercase_ratio = 0.0

        if letters:
            uppercase_ratio = (
                sum(
                    character.isupper()
                    for character in letters
                )
                / len(letters)
            )

        is_heading = (
            len(paragraph) <= 80
            and (
                uppercase_ratio > 0.75
                or not paragraph.endswith(
                    (".", "?", "!", ";", ":")
                )
            )
        )

        selected_style = (
            heading_style
            if is_heading
            else body_style
        )

        story.append(
            Paragraph(
                escape(paragraph),
                selected_style,
            )
        )

    document.build(story)
    temporary_file.replace(output_file)

    return {
        "input": str(input_file),
        "output": str(output_file),
        "characters": len(source_text),
        "font": str(font_path),
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Temizlenmiş TXT dosyasını Microsoft Edge "
            "Read Aloud için PDF'e dönüştürür."
        )
    )

    parser.add_argument(
        "input_file",
        nargs="?",
        type=Path,
        default=DEFAULT_INPUT,
        help=(
            "Dönüştürülecek TXT dosyası. "
            "Varsayılan: podcast_final_text_verified.txt"
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=(
            "Oluşturulacak PDF dosyası. "
            "Varsayılan: readaloud_document.pdf"
        ),
    )

    parser.add_argument(
        "--title",
        default="Okuma Metni",
        help="PDF belgesi için başlık. Varsayılan: Okuma Metni",
    )

    args = parser.parse_args()

    result = create_pdf(
        input_file=args.input_file,
        output_file=args.output,
        title=args.title,
    )

    print("PDF oluşturuldu:")
    print(f"  Girdi: {result['input']}")
    print(f"  Çıktı: {result['output']}")
    print(f"  Karakter: {result['characters']}")
    print(f"  Font: {result['font']}")


if __name__ == "__main__":
    main()