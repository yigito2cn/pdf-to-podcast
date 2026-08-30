import difflib
import json
import os
import random
import re
import time
from pathlib import Path

from google import genai

from pipeline.cache import (
    cache_metadata_path,
    validate_cached_output,
    write_cache_metadata,
)
from pipeline.config import load_project_environment


DEFAULT_MODEL = "gemini-3.7-flash"
DEFAULT_MAX_CHARACTERS = 5000
DEFAULT_MAX_ATTEMPTS = 6
DEFAULT_REQUEST_INTERVAL_SECONDS = 4.0

RETRYABLE_CODES = {
    408,
    429,
    500,
    502,
    503,
    504,
}


class GeminiQuotaError(RuntimeError):
    """
    Gemini kotası bütün tekrar denemelerine rağmen
    kullanılamadığında oluşturulur.
    """


CLEANING_INSTRUCTIONS = """
Aşağıdaki metin bir Türkçe kitaptan PDF yoluyla çıkarılmıştır.
Metin daha sonra sesli anlatım üretiminde kullanılacaktır.

Görevin metni yeniden yazmak değil, yalnızca PDF ve OCR
kaynaklı açık hataları düzeltmektir.

Kurallar:

1. Metnin anlamını, içeriğini, sırasını ve yazarın üslubunu koru.
2. Özetleme yapma.
3. Metni kısaltma.
4. Yeni bilgi, açıklama veya yorum ekleme.
5. Başlıkları ve madde işaretlerini koru.
6. Yanlış birleşmiş kelimeleri ayır.
7. Yanlış bölünmüş kelimeleri birleştir.
8. Açık PDF ve OCR hatalarını bağlama göre düzelt.
9. Eksik veya gereksiz boşlukları düzelt.
10. Noktalama işaretlerini yalnızca açık hata varsa düzelt.
11. Tarihlerdeki açık OCR hatalarını düzelt.
12. Örneğin "l 776" ifadesi bağlam gerektiriyorsa "1776" yap.
13. Özel isimleri, kitap isimlerini ve alıntıları koru.
14. Gerçek paragrafları ve madde yapısını koru.
15. Emin olmadığın kelimeleri tahmin ederek değiştirme.
16. Cümlenin eksik başını veya sonunu kendin tamamlama.
17. Yinelenen metni yalnızca açıkça aynı metnin OCR tekrarıysa kaldır.
18. Yalnızca temizlenmiş metni döndür.
19. Açıklama, Markdown veya kod bloğu ekleme.
""".strip()


def get_error_code(error: Exception) -> int | None:
    """Gemini hatasından HTTP durum kodunu çıkarmaya çalışır."""

    code = getattr(error, "code", None)

    if isinstance(code, int):
        return code

    message = str(error)

    match = re.search(
        r"\b(408|429|500|502|503|504)\b",
        message,
    )

    if match:
        return int(match.group(1))

    return None


def is_retryable_error(error: Exception) -> bool:
    """Hatanın tekrar denenebilir olup olmadığını belirler."""

    status_code = get_error_code(error)

    if status_code in RETRYABLE_CODES:
        return True

    message = str(error).lower()

    markers = (
        "too_many_requests",
        "resource_exhausted",
        "resource exhausted",
        "quota exceeded",
        "rate limit",
        "retry in",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "connection reset",
        "connection error",
        "internal error",
        "server error",
    )

    return any(marker in message for marker in markers)


def get_retry_delay(
    error: Exception,
    attempt: int,
) -> float:
    """
    Gemini hata mesajındaki önerilen bekleme süresini okur.

    Süre bulunamazsa kontrollü exponential backoff kullanır.
    """

    message = str(error)

    patterns = (
        r"please\s+retry\s+in\s+([0-9]+(?:\.[0-9]+)?)s",
        r"retry\s+in\s+([0-9]+(?:\.[0-9]+)?)s",
        r"retry\s+after\s+([0-9]+(?:\.[0-9]+)?)",
    )

    for pattern in patterns:
        match = re.search(
            pattern,
            message,
            flags=re.IGNORECASE,
        )

        if match:
            recommended_delay = float(match.group(1))

            return max(
                15.0,
                recommended_delay + 5.0,
            )

    exponential_delay = min(
        120.0,
        float(2 ** attempt),
    )

    jitter = random.uniform(1.0, 3.0)

    return max(
        15.0,
        exponential_delay + jitter,
    )


def split_long_paragraph(
    paragraph,
    max_characters,
):
    """
    Uzun bir paragrafı mümkün olduğunca cümlelerden böler.
    """

    sentences = re.split(
        r"(?<=[.!?])\s+",
        paragraph.strip(),
    )

    parts = []
    current_part = ""

    for sentence in sentences:
        sentence = sentence.strip()

        if not sentence:
            continue

        candidate = (
            sentence
            if not current_part
            else current_part + " " + sentence
        )

        if len(candidate) <= max_characters:
            current_part = candidate
            continue

        if current_part:
            parts.append(current_part)
            current_part = ""

        if len(sentence) <= max_characters:
            current_part = sentence
            continue

        words = sentence.split()
        word_group = ""

        for word in words:
            candidate = (
                word
                if not word_group
                else word_group + " " + word
            )

            if len(candidate) <= max_characters:
                word_group = candidate
            else:
                if word_group:
                    parts.append(word_group)

                word_group = word

        if word_group:
            current_part = word_group

    if current_part:
        parts.append(current_part)

    return parts


def split_text_into_chunks(
    text,
    max_characters= DEFAULT_MAX_CHARACTERS,
) :
    """Metni paragraf yapısını koruyarak Gemini parçalarına ayırır."""

    paragraphs = re.split(
        r"\n\s*\n",
        text.strip(),
    )

    units = []

    for paragraph in paragraphs:
        paragraph = paragraph.strip()

        if not paragraph:
            continue

        if len(paragraph) <= max_characters:
            units.append(paragraph)
        else:
            units.extend(
                split_long_paragraph(
                    paragraph=paragraph,
                    max_characters=max_characters,
                )
            )

    chunks = []
    current_chunk = ""

    for unit in units:
        candidate = (
            unit
            if not current_chunk
            else current_chunk + "\n\n" + unit
        )

        if len(candidate) <= max_characters:
            current_chunk = candidate
            continue

        if current_chunk:
            chunks.append(current_chunk.strip())

        current_chunk = unit

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks


def build_prompt(
    text: str,
    chunk_number: int,
    total_chunks: int,
) -> str:
    """Gemini için kontrollü temizleme promptu oluşturur."""

    return (
        CLEANING_INSTRUCTIONS
        + "\n\n"
        + f"Metin parçası: {chunk_number}/{total_chunks}"
        + "\n\n"
        + "TEMİZLENECEK METİN:\n"
        + text
    )


def clean_response(text: str) -> str:
    """Gemini yanıtındaki güvenli biçimsel kalıntıları temizler."""

    text = text.strip()

    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()

        if lines:
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    text = text.replace(chr(173), "")

    text = re.sub(
        r"[ \t]{2,}",
        " ",
        text,
    )

    text = re.sub(
        r"[ \t]+([,.;:!?])",
        r"\1",
        text,
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    return text.strip()


def calculate_similarity(
    original_text: str,
    cleaned_text: str,
) -> float:
    """Orijinal ve temiz metin arasındaki karakter benzerliğini hesaplar."""

    return difflib.SequenceMatcher(
        None,
        original_text,
        cleaned_text,
    ).ratio()


def clean_single_chunk(
    client,
    model_name: str,
    chunk_text: str,
    chunk_number: int,
    total_chunks: int,
    max_attempts: int,
) -> str:
    """Tek bir metin parçasını Gemini ile temizler."""

    prompt = build_prompt(
        text=chunk_text,
        chunk_number=chunk_number,
        total_chunks=total_chunks,
    )

    for attempt in range(1, max_attempts + 1):
        print(
            f"      API denemesi: "
            f"{attempt}/{max_attempts}"
        )

        try:
            interaction = client.interactions.create(
                model=model_name,
                input=prompt,
                store=False,
            )

            cleaned_text = interaction.output_text

            if not cleaned_text:
                raise RuntimeError(
                    "Gemini boş yanıt döndürdü."
                )

            cleaned_text = clean_response(cleaned_text)

            if not cleaned_text:
                raise RuntimeError(
                    "Temizleme sonucunda boş metin oluştu."
                )

            return cleaned_text

        except Exception as error:
            print()
            print(
                f"      API hatası: "
                f"{type(error).__name__}"
            )

            if not is_retryable_error(error):
                raise

            if attempt >= max_attempts:
                raise GeminiQuotaError(
                    "Gemini kotası şu anda kullanılamıyor. "
                    "Tamamlanan parçalar kaydedildi. "
                    "Program daha sonra aynı çalışma klasörüyle "
                    "yeniden çalıştırıldığında kaldığı yerden "
                    "devam edecektir."
                ) from error

            wait_seconds = get_retry_delay(
                error=error,
                attempt=attempt,
            )

            print(
                "      Kota veya geçici servis sınırı algılandı."
            )
            print(
                f"      {wait_seconds:.1f} saniye beklenecek..."
            )

            time.sleep(wait_seconds)

    raise RuntimeError(
        "Gemini metin temizliği tamamlanamadı."
    )


def write_progress_report(
    report_file: Path,
    model_name: str,
    input_file: Path,
    output_file: Path,
    total_chunks: int,
    source_characters: int,
    report_entries: list[dict],
    status: str,
) -> None:
    """Gemini temizleme ilerleme raporunu kaydeder."""

    report = {
        "status": status,
        "model": model_name,
        "input_file": str(input_file),
        "output_file": str(output_file),
        "total_chunks": total_chunks,
        "source_characters": source_characters,
        "chunks": report_entries,
    }

    report_file.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def clean_text_file_with_gemini(
    input_file,
    output_file,
    work_directory,
    max_characters: int = DEFAULT_MAX_CHARACTERS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    request_interval_seconds: float = (
        DEFAULT_REQUEST_INTERVAL_SECONDS
    ),
) -> dict:
    """
    Ön temizlenmiş metni Gemini ile temizler.

    Her chunk ayrı kaydedilir. İşlem yarıda kalırsa daha önce
    tamamlanmış chunk dosyaları yeniden işlenmez.
    """

    input_file = Path(input_file)
    output_file = Path(output_file)
    work_directory = Path(work_directory)

    if not input_file.exists():
        raise FileNotFoundError(
            f"Ön temizlenmiş dosya bulunamadı: {input_file}"
        )

    load_project_environment()

    api_key = os.getenv("GEMINI_API_KEY")

    model_name = os.getenv(
        "GEMINI_MODEL",
        DEFAULT_MODEL,
    )

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY bulunamadı. "
            ".env dosyasını kontrol et."
        )

    source_text = input_file.read_text(
        encoding="utf-8-sig",
    ).strip()

    if not source_text:
        raise ValueError(
            "Gemini'ye gönderilecek metin boş."
        )

    chunks = split_text_into_chunks(
        text=source_text,
        max_characters=max_characters,
    )

    if not chunks:
        raise RuntimeError(
            "Gemini için metin parçası oluşturulamadı."
        )

    chunk_input_directory = (
        work_directory / "gemini_text_chunks"
    )

    chunk_output_directory = (
        work_directory / "gemini_cleaned_chunks"
    )

    chunk_input_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    chunk_output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_file = (
        work_directory
        / "gemini_text_cleaning_report.json"
    )

    report_entries = []
    cleaned_parts = []
    total_chunks = len(chunks)

    with genai.Client(api_key=api_key) as client:
        for index, original_chunk in enumerate(
            chunks,
            start=1,
        ):
            input_chunk_file = (
                chunk_input_directory
                / f"chunk_{index:04d}.txt"
            )

            cleaned_chunk_file = (
                chunk_output_directory
                / f"chunk_{index:04d}_cleaned.txt"
            )

            input_chunk_file.write_text(
                original_chunk,
                encoding="utf-8",
            )

            print(
                f"      Metin parçası: "
                f"{index}/{total_chunks}"
            )

            if cleaned_chunk_file.exists():
                metadata_file = cache_metadata_path(
                    cleaned_chunk_file
                )
                if metadata_file.exists():
                    cache_valid, cache_reason = (
                        validate_cached_output(
                            metadata_file=metadata_file,
                            source_text=original_chunk,
                            output_file=cleaned_chunk_file,
                            provider="gemini",
                            model=model_name,
                            settings={
                                "max_characters": max_characters
                            },
                        )
                    )
                    if not cache_valid:
                        raise RuntimeError(
                            "Mevcut Gemini chunk cache metadata "
                            "ile uyuşmuyor; dosya korundu: "
                            f"{cache_reason}"
                        )

                cleaned_chunk = cleaned_chunk_file.read_text(
                    encoding="utf-8-sig",
                ).strip()

                if cleaned_chunk:
                    print(
                        "      Daha önce temizlenmiş, "
                        "tekrar işlenmedi."
                    )

                    cleaned_parts.append(cleaned_chunk)

                    similarity = calculate_similarity(
                        original_chunk,
                        cleaned_chunk,
                    )

                    length_change_percent = (
                        (
                            len(cleaned_chunk)
                            - len(original_chunk)
                        )
                        / len(original_chunk)
                        * 100
                    )

                    report_entries.append(
                        {
                            "chunk": index,
                            "status": "existing",
                            "original_characters": len(
                                original_chunk
                            ),
                            "cleaned_characters": len(
                                cleaned_chunk
                            ),
                            "length_change_percent": round(
                                length_change_percent,
                                2,
                            ),
                            "similarity": round(
                                similarity,
                                4,
                            ),
                        }
                    )

                    write_progress_report(
                        report_file=report_file,
                        model_name=model_name,
                        input_file=input_file,
                        output_file=output_file,
                        total_chunks=total_chunks,
                        source_characters=len(source_text),
                        report_entries=report_entries,
                        status="running",
                    )

                    continue

            try:
                cleaned_chunk = clean_single_chunk(
                    client=client,
                    model_name=model_name,
                    chunk_text=original_chunk,
                    chunk_number=index,
                    total_chunks=total_chunks,
                    max_attempts=max_attempts,
                )

            except GeminiQuotaError:
                write_progress_report(
                    report_file=report_file,
                    model_name=model_name,
                    input_file=input_file,
                    output_file=output_file,
                    total_chunks=total_chunks,
                    source_characters=len(source_text),
                    report_entries=report_entries,
                    status="paused_quota",
                )

                raise

            temporary_chunk_file = cleaned_chunk_file.with_suffix(
                cleaned_chunk_file.suffix + ".tmp"
            )
            temporary_chunk_file.write_text(
                cleaned_chunk,
                encoding="utf-8",
            )
            temporary_chunk_file.replace(cleaned_chunk_file)

            write_cache_metadata(
                metadata_file=cache_metadata_path(
                    cleaned_chunk_file
                ),
                source_text=original_chunk,
                output_file=cleaned_chunk_file,
                provider="gemini",
                model=model_name,
                settings={"max_characters": max_characters},
            )

            similarity = calculate_similarity(
                original_chunk,
                cleaned_chunk,
            )

            length_change_percent = (
                (
                    len(cleaned_chunk)
                    - len(original_chunk)
                )
                / len(original_chunk)
                * 100
            )

            print(
                f"      Karakter değişimi: "
                f"{length_change_percent:+.2f}%"
            )
            print(
                f"      Benzerlik: "
                f"{similarity:.4f}"
            )

            if abs(length_change_percent) > 10:
                print(
                    "      Uyarı: Metin uzunluğu yüzde "
                    "10'dan fazla değişti."
                )

            cleaned_parts.append(cleaned_chunk)

            report_entries.append(
                {
                    "chunk": index,
                    "status": "cleaned",
                    "original_characters": len(
                        original_chunk
                    ),
                    "cleaned_characters": len(
                        cleaned_chunk
                    ),
                    "length_change_percent": round(
                        length_change_percent,
                        2,
                    ),
                    "similarity": round(
                        similarity,
                        4,
                    ),
                }
            )

            write_progress_report(
                report_file=report_file,
                model_name=model_name,
                input_file=input_file,
                output_file=output_file,
                total_chunks=total_chunks,
                source_characters=len(source_text),
                report_entries=report_entries,
                status="running",
            )

            if index < total_chunks:
                print(
                    f"      Sonraki istekten önce "
                    f"{request_interval_seconds:.1f} saniye "
                    "bekleniyor..."
                )

                time.sleep(request_interval_seconds)

    final_text = "\n\n".join(
        cleaned_parts
    ).strip()

    final_text = clean_response(final_text)

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_file.write_text(
        final_text,
        encoding="utf-8",
    )

    write_progress_report(
        report_file=report_file,
        model_name=model_name,
        input_file=input_file,
        output_file=output_file,
        total_chunks=total_chunks,
        source_characters=len(source_text),
        report_entries=report_entries,
        status="completed",
    )

    return {
        "model": model_name,
        "total_chunks": total_chunks,
        "source_characters": len(source_text),
        "cleaned_characters": len(final_text),
        "output_file": str(output_file),
        "report_file": str(report_file),
    }