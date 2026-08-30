import re
from pathlib import Path


REPEATED_HEADERS = set()


def fix_pdf_word_breaks(text):
    """
    PDF kaynaklı soft hyphen karakterlerini ve
    satır sonunda bölünmüş kelimeleri düzeltir.
    """
    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")

    # Unicode soft hyphen karakterini kaldır.
    text = text.replace(chr(173), "")

    # Satır sonunda tireyle bölünmüş kelimeleri birleştir.
    #
    # Örnek:
    # seri-
    # liyorken
    #
    # Sonuç:
    # seriliyorken
    text = re.sub(
        r"([^\W\d_])-[ \t]*\n[ \t]*([^\W\d_])",
        r"\1\2",
        text,
        flags=re.UNICODE,
    )

    return text


def remove_page_markers(text):
    """
    PDF çıkarma modülünün eklediği sayfa ayraçlarını kaldırır.
    """
    text = re.sub(
        r"={20,}",
        "\n",
        text,
    )

    text = re.sub(
        r"PDF SAYFASI:\s*\d+",
        "\n",
        text,
        flags=re.IGNORECASE,
    )

    text = text.replace(
        "[Bu sayfadan okunabilir metin çıkarılamadı.]",
        "",
    )

    return text


def normalize_for_comparison(text):
    """
    Bir satırı başlık karşılaştırması için sadeleştirir.
    """
    text = text.upper()

    replacements = {
        "İ": "I",
        "Ü": "U",
        "Ş": "S",
        "Ğ": "G",
        "Ö": "O",
        "Ç": "C",
    }

    for original, replacement in replacements.items():
        text = text.replace(
            original,
            replacement,
        )

    text = re.sub(
        r"\s+",
        "",
        text,
    )

    return text


def remove_repeated_headers(lines):
    """
    Tekrarlanan kitap veya bölüm üst başlıklarını kaldırır.
    """
    normalized_headers = {
        normalize_for_comparison(header)
        for header in REPEATED_HEADERS
    }

    cleaned_lines = []

    for line in lines:
        normalized_line = normalize_for_comparison(
            line.strip()
        )

        if normalized_line in normalized_headers:
            continue

        cleaned_lines.append(line)

    return cleaned_lines


def remove_page_numbers(lines):
    """
    Tek başına duran PDF veya kitap sayfa numaralarını kaldırır.
    """
    cleaned_lines = []

    for line in lines:
        stripped = line.strip()

        if not stripped:
            cleaned_lines.append("")
            continue

        # Örnek: 9 veya 337
        if re.fullmatch(
            r"\d{1,4}",
            stripped,
        ):
            continue

        # Örnek: - 9 -, – 9 – veya — 9 —
        if re.fullmatch(
            r"[-–—]\s*\d{1,4}\s*[-–—]",
            stripped,
        ):
            continue

        cleaned_lines.append(line)

    return cleaned_lines


def normalize_lines(text):
    """
    Satır başı ve sonundaki boşlukları kaldırır.
    Fazla yatay boşlukları teke indirir.
    """
    normalized_lines = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            normalized_lines.append("")
            continue

        line = re.sub(
            r"[ \t]+",
            " ",
            line,
        )

        normalized_lines.append(line)

    return normalized_lines


def looks_like_heading(line):
    """
    Kısa ve çoğunlukla büyük harfli satırları başlık olarak tanır.
    """
    if not line:
        return False

    if len(line) > 80:
        return False

    letters = [
        character
        for character in line
        if character.isalpha()
    ]

    if not letters:
        return False

    uppercase_count = sum(
        character.isupper()
        for character in letters
    )

    uppercase_ratio = (
        uppercase_count / len(letters)
    )

    return uppercase_ratio > 0.80


def join_wrapped_lines(lines):
    """
    PDF'nin görsel satır sonlarını doğal paragraflara dönüştürür.

    Madde işaretlerini ve başlıkları ayrı paragraf olarak korur.
    """

    paragraphs = []
    current_paragraph = ""

    for line in lines:
        stripped = line.strip()

        if not stripped:
            if current_paragraph:
                paragraphs.append(
                    current_paragraph.strip()
                )
                current_paragraph = ""

            continue

        is_bullet = re.match(
            r"^[-•]\s+",
            stripped,
        )

        if is_bullet:
            if current_paragraph:
                paragraphs.append(
                    current_paragraph.strip()
                )

            current_paragraph = stripped
            continue

        if looks_like_heading(stripped):
            if current_paragraph:
                paragraphs.append(
                    current_paragraph.strip()
                )
                current_paragraph = ""

            paragraphs.append(stripped)
            continue

        if not current_paragraph:
            current_paragraph = stripped
            continue

        if current_paragraph.endswith("-"):
            current_paragraph = (
                current_paragraph[:-1]
                + stripped
            )
        else:
            current_paragraph += (
                " " + stripped
            )

    if current_paragraph:
        paragraphs.append(
            current_paragraph.strip()
        )

    return "\n\n".join(paragraphs)


def final_cleanup(text):
    """
    Güvenli boşluk ve noktalama temizliği uygular.
    """
    text = text.replace(chr(173), "")

    text = re.sub(
        r"[ \t]{2,}",
        " ",
        text,
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    text = re.sub(
        r"[ \t]+([,.;:!?])",
        r"\1",
        text,
    )

    text = re.sub(
        r"(?<=\w)'[ \t]+(?=\w)",
        "'",
        text,
    )

    return text.strip()


def preclean_text(text):
    """
    Ham PDF metnine tüm güvenli ön temizleme aşamalarını uygular.
    """
    text = remove_page_markers(text)
    text = fix_pdf_word_breaks(text)

    lines = normalize_lines(text)
    lines = remove_page_numbers(lines)
    lines = remove_repeated_headers(lines)

    cleaned_text = join_wrapped_lines(lines)
    cleaned_text = final_cleanup(cleaned_text)

    return cleaned_text


def preclean_text_file(
    input_file,
    output_file,
):
    """
    Ham TXT dosyasını temizler ve yeni TXT dosyasına kaydeder.
    """
    input_file = Path(input_file)
    output_file = Path(output_file)

    if not input_file.exists():
        raise FileNotFoundError(
            f"Ham metin dosyası bulunamadı: {input_file}"
        )

    raw_text = input_file.read_text(
        encoding="utf-8-sig"
    )

    if not raw_text.strip():
        raise ValueError(
            "Ham PDF metni boş."
        )

    cleaned_text = preclean_text(
        raw_text
    )

    if not cleaned_text:
        raise RuntimeError(
            "Ön temizleme sonucunda boş metin oluştu."
        )

    remaining_soft_hyphens = (
        cleaned_text.count(chr(173))
    )

    if remaining_soft_hyphens:
        raise RuntimeError(
            "Ön temizlenmiş metinde soft hyphen kaldı."
        )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_file.write_text(
        cleaned_text,
        encoding="utf-8",
    )

    return {
        "input_file": str(input_file),
        "output_file": str(output_file),
        "raw_characters": len(raw_text),
        "cleaned_characters": len(cleaned_text),
        "removed_characters": (
            len(raw_text) - len(cleaned_text)
        ),
        "soft_hyphens_remaining": (
            remaining_soft_hyphens
        ),
    }