import difflib
import hashlib
import json
import os
import random
import re
import time
from pathlib import Path

from groq import Groq

from pipeline.cache import (
    cache_metadata_path,
    validate_cached_output,
    write_cache_metadata,
)
from pipeline.config import load_project_environment


PROJECT_DIRECTORY = (
    Path(__file__).resolve().parent.parent
)

DEFAULT_MODEL = "qwen/qwen3.6-27b"
DEFAULT_MAX_CHARACTERS = 2200
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_REQUEST_INTERVAL_SECONDS = 4.0


MIN_LENGTH_RATIO = 0.90
MAX_LENGTH_RATIO = 1.10
MIN_SIMILARITY_RATIO = 0.90
USE_ORIGINAL_ON_VALIDATION_FAILURE = True
RETRYABLE_STATUS_CODES = {
    408,
    429,
    500,
    502,
    503,
    504,
}


class GroqQuotaError(RuntimeError):
    """
    Groq kotası veya geçici API sınırı
    kullanılamadığında oluşur.
    """


class GroqRequestTooLargeError(RuntimeError):
    """
    Groq isteği model veya TPM sınırını
    aştığında oluşur.
    """


class GroqAuthenticationError(RuntimeError):
    """
    Groq API anahtarı geçersiz veya eksikse oluşur.
    """


class GroqModelError(RuntimeError):
    """
    Groq modeli bulunamadığında veya hesaptan
    erişilemediğinde oluşur.
    """


class GroqValidationError(RuntimeError):
    """
    Groq çıktısı kalite kontrolünden
    geçmediğinde oluşur.
    """

    def __init__(
        self,
        message,
        candidate_text="",
        validation=None,
    ):
        super().__init__(message)

        self.candidate_text = candidate_text
        self.validation = validation or {}


CLEANING_INSTRUCTIONS = """
Görev: Türkçe PDF ve OCR metnini mümkün olan en az
değişiklikle düzeltmek.

Bu bir yeniden yazma, özetleme veya metni iyileştirme
görevi değildir.

Kesin kurallar:

1. Metnin anlamını, içeriğini, sırasını ve yazarın üslubunu koru.
2. Doğru yazılmış kelimeleri değiştirme.
3. Sözcükleri eş anlamlılarıyla değiştirme.
4. Cümle yapısını değiştirme.
5. Fiil zamanlarını ve çekimlerini değiştirme.
6. Metni modernleştirme veya daha akıcı hale getirme.
7. Metni kısaltma veya özetleme.
8. Yeni bilgi, açıklama veya yorum ekleme.
9. Yalnızca kesin PDF ve OCR hatalarını düzelt.
10. Yanlış bölünmüş kelimeleri birleştir.
11. Yanlış birleşmiş kelimeleri ayır.
12. Açık karakter hatalarını bağlama göre düzelt.
13. Eksik veya gereksiz boşlukları düzelt.
14. Noktalama işaretlerini yalnızca kesin hata varsa düzelt.
15. Özel isimleri, kitap adlarını, tarihleri ve alıntıları koru.
16. Emin olmadığın kelimeyi değiştirme.
17. Eksik bir cümlenin başını veya sonunu tamamlama.
18. Kaynak metindeki doğru kelimeleri başka kelimelerle değiştirme.
19. Kaynak metindeki paragraf ve madde yapısını koru.
20. Her düzeltmede mümkün olan en az sayıda karakteri değiştir.
21. Çıktıya başlık, açıklama, analiz veya Markdown ekleme.
22. Düşünme sürecini veya muhakemeni yazma.
23. Yalnızca düzeltilmiş metni döndür.

Örnek 1:

Girdi:
Batı'nın statü anlayışı l 776'dan bu yana giderek artan bir
oranda maddi başarıyla bir tutulur olmuştur.

Doğru çıktı:
Batı'nın statü anlayışı 1776'dan bu yana giderek artan bir
oranda maddi başarıyla bir tutulur olmuştur.

Örnek 2:

Girdi:
Bu roma nındaki anlatıcı restorana gir di.

Doğru çıktı:
Bu romanındaki anlatıcı restorana girdi.

Yanlış çıktı:
Bu romandaki anlatıcı restorana girer.

Örnek 3:

Girdi:
Akşam yeme ği için sözleşmiştir.

Doğru çıktı:
Akşam yemeği için sözleşmiştir.
""".strip()


def calculate_text_hash(text):
    """
    Kaynak metnin SHA-256 özetini üretir.
    """

    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def get_error_status_code(error):
    """
    API hata nesnesinden HTTP durum kodunu
    çıkarmaya çalışır.
    """

    status_code = getattr(
        error,
        "status_code",
        None,
    )

    if isinstance(status_code, int):
        return status_code

    response = getattr(
        error,
        "response",
        None,
    )

    if response is not None:
        response_status = getattr(
            response,
            "status_code",
            None,
        )

        if isinstance(response_status, int):
            return response_status

    match = re.search(
        r"\b(400|401|403|408|413|429|500|502|503|504)\b",
        str(error),
    )

    if match:
        return int(
            match.group(1)
        )

    return None


def is_authentication_error(error):
    """
    Geçersiz veya eksik API anahtarı
    hatalarını tanır.
    """

    status_code = get_error_status_code(
        error
    )

    message = str(error).lower()

    markers = (
        "invalid api key",
        "invalid_api_key",
        "api key not valid",
        "authentication failed",
        "authentication error",
        "unauthorized",
    )

    return (
        status_code in (401, 403)
        or any(
            marker in message
            for marker in markers
        )
    )


def is_model_error(error):
    """
    Model bulunamadı veya modele erişilemiyor
    hatalarını tanır.
    """

    status_code = get_error_status_code(
        error
    )

    message = str(error).lower()

    markers = (
        "model_not_found",
        "model not found",
        "does not exist",
        "do not have access",
        "model is not available",
    )

    return (
        status_code == 404
        or any(
            marker in message
            for marker in markers
        )
    )


def is_request_too_large_error(
    error: Exception,
) -> bool:
    """
    Hatanın gerçek bir bağlam veya istek boyutu
    aşımı olup olmadığını belirler.

    Dakikalık token kotası, yani TPM 429 hatası,
    istek boyutu hatası değildir.
    """

    status_code = get_error_status_code(error)
    message = str(error).lower()

    return (
        status_code == 413
        or "request too large" in message
        or "maximum context length" in message
        or "context_length_exceeded" in message
        or "context window exceeded" in message
        or "prompt is too long" in message
    )


def is_retryable_error(error):
    """
    API hatasının tekrar denenebilir olup
    olmadığını belirler.
    """

    status_code = get_error_status_code(
        error
    )

    if status_code in RETRYABLE_STATUS_CODES:
        return True

    message = str(error).lower()

    retryable_markers = (
        "rate limit",
        "rate_limit",
        "too many requests",
        "resource exhausted",
        "quota exceeded",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "connection error",
        "connection reset",
        "server error",
        "internal error",
        "service unavailable",
    )

    return any(
        marker in message
        for marker in retryable_markers
    )


def get_retry_delay(
    error: Exception,
    attempt: int,
) -> float:
    """
    Groq hata mesajındaki önerilen bekleme süresini okur.

    Saniye ve milisaniye biçimlerini destekler.
    Süre bulunamazsa exponential backoff kullanır.
    """

    message = str(error)

    millisecond_patterns = (
        r"try again in\s+([0-9]+(?:\.[0-9]+)?)ms",
        r"retry in\s+([0-9]+(?:\.[0-9]+)?)ms",
        r"retry after\s+([0-9]+(?:\.[0-9]+)?)ms",
    )

    for pattern in millisecond_patterns:
        match = re.search(
            pattern,
            message,
            flags=re.IGNORECASE,
        )

        if match:
            milliseconds = float(
                match.group(1)
            )

            return max(
                1.5,
                milliseconds / 1000.0 + 0.75,
            )

    second_patterns = (
        r"try again in\s+([0-9]+(?:\.[0-9]+)?)s",
        r"retry in\s+([0-9]+(?:\.[0-9]+)?)s",
        r"retry after\s+([0-9]+(?:\.[0-9]+)?)s",
    )

    for pattern in second_patterns:
        match = re.search(
            pattern,
            message,
            flags=re.IGNORECASE,
        )

        if match:
            seconds = float(
                match.group(1)
            )

            return max(
                2.0,
                seconds + 1.0,
            )

    exponential_delay = min(
        60.0,
        float(2 ** attempt),
    )

    jitter = random.uniform(1.0, 3.0)

    return exponential_delay + jitter


def split_long_paragraph(
    paragraph,
    max_characters,
):
    """
    Uzun paragrafı mümkün olduğunca cümle
    sınırlarından böler.
    """

    paragraph = paragraph.strip()

    if not paragraph:
        return []

    if len(paragraph) <= max_characters:
        return [paragraph]

    sentences = re.split(
        r"(?<=[.!?])\s+",
        paragraph,
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
            else current_part
            + " "
            + sentence
        )

        if len(candidate) <= max_characters:
            current_part = candidate
            continue

        if current_part:
            parts.append(
                current_part
            )

            current_part = ""

        if len(sentence) <= max_characters:
            current_part = sentence
            continue

        words = sentence.split()
        word_group = ""

        for word in words:
            word_candidate = (
                word
                if not word_group
                else word_group
                + " "
                + word
            )

            if (
                len(word_candidate)
                <= max_characters
            ):
                word_group = word_candidate
                continue

            if word_group:
                parts.append(
                    word_group
                )

            if len(word) <= max_characters:
                word_group = word
            else:
                for start in range(
                    0,
                    len(word),
                    max_characters,
                ):
                    word_part = word[
                        start:
                        start + max_characters
                    ]

                    if (
                        len(word_part)
                        == max_characters
                    ):
                        parts.append(
                            word_part
                        )
                    else:
                        word_group = word_part

        if word_group:
            current_part = word_group

    if current_part:
        parts.append(
            current_part
        )

    return parts


def split_text_into_chunks(
    text,
    max_characters=DEFAULT_MAX_CHARACTERS,
):
    """
    Metni paragraf ve cümle sınırlarını
    koruyarak parçalara ayırır.
    """

    if max_characters < 500:
        raise ValueError(
            "max_characters en az 500 olmalıdır."
        )

    text = text.replace(
        "\r\n",
        "\n",
    )

    text = text.replace(
        "\r",
        "\n",
    )

    paragraphs = re.split(
        r"\n\s*\n",
        text.strip(),
    )

    units = []

    for paragraph in paragraphs:
        paragraph = paragraph.strip()

        if not paragraph:
            continue

        units.extend(
            split_long_paragraph(
                paragraph=paragraph,
                max_characters=max_characters,
            )
        )

    chunks = []
    current_parts = []
    current_length = 0

    for unit in units:
        separator_length = (
            2 if current_parts else 0
        )

        candidate_length = (
            current_length
            + separator_length
            + len(unit)
        )

        if candidate_length <= max_characters:
            current_parts.append(
                unit
            )

            current_length = candidate_length
            continue

        if current_parts:
            chunks.append(
                "\n\n".join(
                    current_parts
                ).strip()
            )

        current_parts = [unit]
        current_length = len(unit)

    if current_parts:
        chunks.append(
            "\n\n".join(
                current_parts
            ).strip()
        )

    return [
        chunk
        for chunk in chunks
        if chunk
    ]


def build_messages(
    text,
    chunk_number,
    total_chunks,
):
    """
    Groq Chat Completions mesajlarını oluşturur.
    """

    user_message = (
        f"Metin parçası: "
        f"{chunk_number}/{total_chunks}"
        + "\n\n"
        + "TEMİZLENECEK METİN:\n"
        + text
    )

    return [
        {
            "role": "system",
            "content": CLEANING_INSTRUCTIONS,
        },
        {
            "role": "user",
            "content": user_message,
        },
    ]


def clean_response(text):
    """
    Model yanıtındaki düşünme ve biçimsel
    kalıntıları temizler.
    """

    text = text.strip()

    text = re.sub(
        r"<think>.*?</think>",
        "",
        text,
        flags=(
            re.IGNORECASE
            | re.DOTALL
        ),
    ).strip()

    if text.lower().startswith("<think>"):
        raise GroqValidationError(
            "Groq yalnızca düşünme metni döndürdü.",
            candidate_text=text,
        )

    if (
        text.startswith("```")
        and text.endswith("```")
    ):
        lines = text.splitlines()

        if lines:
            lines = lines[1:]

        if (
            lines
            and lines[-1].strip() == "```"
        ):
            lines = lines[:-1]

        text = "\n".join(
            lines
        ).strip()

    prefixes = (
        "Düzeltilmiş metin:",
        "Temizlenmiş metin:",
        "Çıktı:",
        "Düzeltilmiş hali:",
        "İşte düzeltilmiş metin:",
    )

    for prefix in prefixes:
        if text.lower().startswith(
            prefix.lower()
        ):
            text = text[
                len(prefix):
            ].lstrip()

            break

    text = text.replace(
        chr(173),
        "",
    )

    text = text.replace(
        "\r\n",
        "\n",
    )

    text = text.replace(
        "\r",
        "\n",
    )

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


def estimate_max_completion_tokens(
    chunk_text,
):
    """
    Temizlenmiş metnin kaynak metne yakın
    uzunlukta olacağı varsayımıyla güvenli
    çıktı token bütçesi hesaplar.
    """

    estimated_input_tokens = max(
        1,
        len(chunk_text) // 3,
    )

    output_budget = int(
        estimated_input_tokens * 1.35
    ) + 256

    return max(
        512,
        min(
            output_budget,
            2048,
        ),
    )


def calculate_similarity(
    original_text,
    cleaned_text,
):
    """
    Orijinal ve temizlenmiş metnin karakter
    benzerliğini hesaplar.
    """

    return difflib.SequenceMatcher(
        None,
        original_text,
        cleaned_text,
    ).ratio()


def validate_cleaned_text(
    original_text,
    cleaned_text,
):
    """
    Model çıktısını otomatik kalite
    kontrollerinden geçirir.
    """

    if not original_text:
        raise GroqValidationError(
            "Kaynak metin boş."
        )

    if not cleaned_text:
        raise GroqValidationError(
            "Groq boş metin döndürdü."
        )

    length_ratio = (
        len(cleaned_text)
        / len(original_text)
    )

    similarity = calculate_similarity(
        original_text,
        cleaned_text,
    )

    forbidden_prefixes = (
        "düzeltilmiş metin",
        "temizlenmiş metin",
        "işte düzeltilmiş",
        "açıklama:",
        "analiz:",
        "gerekçe:",
    )

    lower_output = (
        cleaned_text
        .strip()
        .lower()
    )

    added_explanation = any(
        lower_output.startswith(
            prefix
        )
        for prefix in forbidden_prefixes
    )

    contains_thinking = (
        "<think>" in lower_output
        or "</think>" in lower_output
    )

    validation = {
        "valid": True,
        "original_characters": len(
            original_text
        ),
        "cleaned_characters": len(
            cleaned_text
        ),
        "length_ratio": round(
            length_ratio,
            4,
        ),
        "similarity_ratio": round(
            similarity,
            4,
        ),
        "added_explanation": (
            added_explanation
        ),
        "contains_thinking": (
            contains_thinking
        ),
        "reasons": [],
    }

    if not (
        MIN_LENGTH_RATIO
        <= length_ratio
        <= MAX_LENGTH_RATIO
    ):
        validation["valid"] = False

        validation["reasons"].append(
            "length_ratio_out_of_range"
        )

    if similarity < MIN_SIMILARITY_RATIO:
        validation["valid"] = False

        validation["reasons"].append(
            "similarity_below_threshold"
        )

    if added_explanation:
        validation["valid"] = False

        validation["reasons"].append(
            "model_added_explanation"
        )

    if contains_thinking:
        validation["valid"] = False

        validation["reasons"].append(
            "model_returned_thinking"
        )

    return validation


def clean_single_chunk(
    client,
    model_name,
    chunk_text,
    chunk_number,
    total_chunks,
    max_attempts,
):
    """
    Tek bir metin parçasını Groq üzerinden
    temizler.

    Yalnızca geçici API hataları yeniden denenir.
    Kalite hatası yeniden API'ye gönderilmez.
    """

    messages = build_messages(
        text=chunk_text,
        chunk_number=chunk_number,
        total_chunks=total_chunks,
    )

    completion_token_limit = (
        estimate_max_completion_tokens(
            chunk_text
        )
    )

    for attempt in range(
        1,
        max_attempts + 1,
    ):
        print(
            f"      Groq denemesi: "
            f"{attempt}/{max_attempts}"
        )

        print(
            f"      Çıktı token sınırı: "
            f"{completion_token_limit}"
        )

        try:
            response = (
                client
                .chat
                .completions
                .create(
                    model=model_name,
                    messages=messages,
                    temperature=0.01,
                    max_completion_tokens=(
                        completion_token_limit
                    ),
                    reasoning_effort="none",
                )
            )

        except Exception as error:
            status_code = (
                get_error_status_code(
                    error
                )
            )

            print(
                f"      Groq API hatası "
                f"(Kod: {status_code}): "
                f"{type(error).__name__}"
            )

            if is_authentication_error(
                error
            ):
                raise GroqAuthenticationError(
                    "Groq API anahtarı geçersiz "
                    "veya eksik."
                ) from error

            if is_model_error(error):
                raise GroqModelError(
                    "Groq modeli bulunamadı veya "
                    "bu hesap üzerinden erişilemiyor: "
                    f"{model_name}"
                ) from error

            if is_request_too_large_error(
                error
            ):
                raise GroqRequestTooLargeError(
                    "Groq isteği token sınırını aştı. "
                    "GROQ_MAX_CHARACTERS değerini "
                    "azaltın."
                ) from error

            if not is_retryable_error(
                error
            ):
                raise

            if attempt >= max_attempts:
                raise GroqQuotaError(
                    "Groq maksimum API deneme "
                    "sayısına ulaştı. "
                    f"Son hata: {error}"
                ) from error

            delay = get_retry_delay(
                error=error,
                attempt=attempt,
            )

            print(
                f"      {delay:.1f} saniye "
                "sonra yeniden denenecek..."
            )

            time.sleep(
                delay
            )

            continue

        content = (
            response
            .choices[0]
            .message
            .content
        )

        if not content:
            raise GroqValidationError(
                "Groq boş yanıt döndürdü."
            )

        cleaned_text = clean_response(
            content
        )

        validation = validate_cleaned_text(
            original_text=chunk_text,
            cleaned_text=cleaned_text,
        )

        print(
            f"      Uzunluk oranı: "
            f"{validation['length_ratio']:.4f}"
        )

        print(
            f"      Benzerlik: "
            f"{validation['similarity_ratio']:.4f}"
        )

        if validation["valid"]:
            return (
                cleaned_text,
                validation,
            )

        print(
            "      Çıktı kalite kontrolünden geçmedi:"
        )

        for reason in validation["reasons"]:
            print(
                f"        - {reason}"
            )

        raise GroqValidationError(
            "Groq çıktısı kalite kontrolünden "
            "geçmedi: "
            + ", ".join(
                validation["reasons"]
            ),
            candidate_text=cleaned_text,
            validation=validation,
        )

    raise GroqQuotaError(
        "Groq metin temizliği tamamlanamadı."
    )


def clean_source_chunk_with_subchunks(
    client,
    model_name,
    source_chunk,
    source_chunk_number,
    total_source_chunks,
    max_characters,
    max_attempts,
    request_interval_seconds,
    subchunk_cache_directory=None,
):
    """
    Bir ana Gemini chunk'ını Groq sınırlarına uygun
    alt parçalara böler.

    Bir alt parça kalite kontrolünden geçmezse kaynak
    alt parça korunur. Böylece tek bir problemli cevap
    bütün kitabın işlenmesini durdurmaz.
    """

    subchunks = split_text_into_chunks(
        text=source_chunk,
        max_characters=max_characters,
    )

    if not subchunks:
        raise GroqValidationError(
            "Groq alt parçası oluşturulamadı."
        )

    cleaned_subchunks = []
    validation_entries = []

    total_subchunks = len(subchunks)

    if total_subchunks > 1:
        print(
            f"      Ana parça {total_subchunks} "
            "Groq alt parçasına bölündü."
        )

    for subchunk_index, subchunk in enumerate(
        subchunks,
        start=1,
    ):
        if total_subchunks > 1:
            print(
                f"      Alt parça: "
                f"{subchunk_index}/{total_subchunks}"
            )

        cached_output_file = None
        cached_metadata_file = None
        if subchunk_cache_directory is not None:
            subchunk_cache_directory = Path(
                subchunk_cache_directory
            )
            subchunk_cache_directory.mkdir(
                parents=True,
                exist_ok=True,
            )
            cached_output_file = (
                subchunk_cache_directory
                / f"chunk_{source_chunk_number:04d}_"
                f"subchunk_{subchunk_index:03d}.txt"
            )
            cached_metadata_file = cache_metadata_path(
                cached_output_file
            )

        cache_valid = False
        if cached_output_file is not None:
            cache_valid, _ = validate_cached_output(
                metadata_file=cached_metadata_file,
                source_text=subchunk,
                output_file=cached_output_file,
                provider="groq",
                model=model_name,
                settings={"max_characters": max_characters},
            )

        if cache_valid:
            selected_text = cached_output_file.read_text(
                encoding="utf-8-sig"
            ).strip()
            validation = validate_cleaned_text(
                original_text=subchunk,
                cleaned_text=selected_text,
            )
            cached_metadata = json.loads(
                cached_metadata_file.read_text(encoding="utf-8")
            )
            status = cached_metadata.get(
                "validation", {}
            ).get("selection_status", "cleaned")
            candidate_text = selected_text
            print("      Doğrulanmış alt parça cache'i kullanıldı.")

        else:
            try:
                (
                    cleaned_subchunk,
                    validation,
                ) = clean_single_chunk(
                    client=client,
                    model_name=model_name,
                    chunk_text=subchunk,
                    chunk_number=source_chunk_number,
                    total_chunks=total_source_chunks,
                    max_attempts=max_attempts,
                )

                selected_text = cleaned_subchunk
                status = "cleaned"
                candidate_text = cleaned_subchunk

            except GroqValidationError as error:
                candidate_text = getattr(
                    error,
                    "candidate_text",
                    "",
                )

                failed_validation = getattr(
                    error,
                    "validation",
                    {},
                )

                if not USE_ORIGINAL_ON_VALIDATION_FAILURE:
                    failed_validation.update(
                        {
                            "source_chunk": (
                                source_chunk_number
                            ),
                            "subchunk": subchunk_index,
                            "total_subchunks": (
                                total_subchunks
                            ),
                            "subchunk_original_text": (
                                subchunk
                            ),
                        }
                    )

                    error.validation = failed_validation
                    raise

                print(
                    "      Uyarı: Groq çıktısı güvenli "
                    "bulunmadı."
                )
                print(
                    "      Kaynak alt parça değişmeden "
                    "korunacak."
                )

                selected_text = subchunk
                status = "original_fallback"

                validation = validate_cleaned_text(
                    original_text=subchunk,
                    cleaned_text=subchunk,
                )

                validation["fallback_reason"] = str(
                    error
                )

                validation["rejected_candidate"] = (
                    candidate_text
                )

                validation[
                    "rejected_candidate_validation"
                ] = failed_validation

            except GroqQuotaError as error:
                error.paused_at = (
                    f"text_chunk_{source_chunk_number:04d}_"
                    f"subchunk_{subchunk_index:03d}"
                )
                raise

            if cached_output_file is not None:
                temporary_file = cached_output_file.with_suffix(
                    cached_output_file.suffix + ".tmp"
                )
                temporary_file.write_text(
                    selected_text,
                    encoding="utf-8",
                )
                temporary_file.replace(cached_output_file)
                cache_validation = {
                    **validation,
                    "selection_status": status,
                }
                write_cache_metadata(
                    metadata_file=cached_metadata_file,
                    source_text=subchunk,
                    output_file=cached_output_file,
                    provider="groq",
                    model=model_name,
                    settings={"max_characters": max_characters},
                    validation=cache_validation,
                )


        cleaned_subchunks.append(
            selected_text
        )

        validation_entry = {
            "subchunk": subchunk_index,
            "status": status,
            "original_characters": len(
                subchunk
            ),
            "cleaned_characters": len(
                selected_text
            ),
            "length_ratio": validation[
                "length_ratio"
            ],
            "similarity_ratio": validation[
                "similarity_ratio"
            ],
            "valid": validation["valid"],
            "reasons": validation["reasons"],
        }

        if status == "original_fallback":
            validation_entry[
                "fallback_reason"
            ] = validation.get(
                "fallback_reason",
                "",
            )

            validation_entry[
                "rejected_candidate"
            ] = candidate_text

            validation_entry[
                "rejected_candidate_validation"
            ] = validation.get(
                "rejected_candidate_validation",
                {},
            )

        validation_entries.append(
            validation_entry
        )

        if subchunk_index < total_subchunks:
            time.sleep(
                max(
                    0.0,
                    request_interval_seconds,
                )
            )

    combined_text = "\n\n".join(
        cleaned_subchunks
    ).strip()

    combined_validation = validate_cleaned_text(
        original_text=source_chunk,
        cleaned_text=combined_text,
    )

    fallback_subchunks = [
        entry["subchunk"]
        for entry in validation_entries
        if (
            entry["status"]
            == "original_fallback"
        )
    ]

    combined_validation[
        "original_fallback_subchunks"
    ] = fallback_subchunks

    combined_validation[
        "has_original_fallback"
    ] = bool(fallback_subchunks)

    if not combined_validation["valid"]:
        raise GroqValidationError(
            "Birleştirilen Groq alt parçaları "
            "kalite kontrolünden geçmedi: "
            + ", ".join(
                combined_validation["reasons"]
            ),
            candidate_text=combined_text,
            validation=combined_validation,
        )

    return (
        combined_text,
        combined_validation,
        validation_entries,
    )


def read_cleaned_candidate(
    candidate_file,
    original_chunk,
):
    """
    Var olan temizlenmiş dosyayı okur ve güncel
    kaynak chunk ile uyumlu olup olmadığını kontrol eder.
    """

    if not candidate_file.exists():
        return None, None

    try:
        candidate_text = (
            candidate_file.read_text(
                encoding="utf-8-sig"
            ).strip()
        )

    except (
        OSError,
        UnicodeDecodeError,
    ):
        return None, None

    if not candidate_text:
        return None, None

    try:
        validation = validate_cleaned_text(
            original_text=original_chunk,
            cleaned_text=candidate_text,
        )

    except GroqValidationError:
        return None, None

    if not validation["valid"]:
        return None, validation

    return (
        candidate_text,
        validation,
    )


def write_review_file(
    review_file,
    chunk_index,
    total_chunks,
    original_chunk,
    error,
):
    """
    Kalite kontrolünden geçmeyen kaynak ve aday
    çıktıyı inceleme dosyasına kaydeder.
    """

    candidate_text = getattr(
        error,
        "candidate_text",
        "",
    )

    validation = getattr(
        error,
        "validation",
        {},
    )

    review_content = (
        "GROQ KALİTE KONTROLÜ BAŞARISIZ\n"
        + "=" * 68
        + "\n\n"
        + f"Parça: {chunk_index}/{total_chunks}\n"
        + f"Hata: {error}\n\n"
        + "DOĞRULAMA SONUCU\n"
        + "-" * 68
        + "\n"
        + json.dumps(
            validation,
            ensure_ascii=False,
            indent=2,
        )
        + "\n\n"
        + "ORİJİNAL ANA PARÇA\n"
        + "-" * 68
        + "\n"
        + original_chunk
        + "\n\n"
        + "GROQ ADAY ÇIKTISI\n"
        + "-" * 68
        + "\n"
        + (
            candidate_text
            if candidate_text
            else "[Aday çıktı alınamadı]"
        )
        + "\n"
    )

    review_file.write_text(
        review_content,
        encoding="utf-8",
    )


def load_source_chunks(
    input_file,
    work_directory,
    max_characters,
):
    """
    Varsa mevcut Gemini giriş chunk düzenini kullanır.

    Gemini chunk klasörü yoksa giriş metnini
    Groq için yeniden böler.
    """

    gemini_input_directory = (
        work_directory
        / "gemini_text_chunks"
    )

    existing_input_files = []

    if gemini_input_directory.exists():
        existing_input_files = sorted(
            gemini_input_directory.glob(
                "chunk_*.txt"
            )
        )

    if existing_input_files:
        chunks = []

        for chunk_file in existing_input_files:
            chunk_text = (
                chunk_file.read_text(
                    encoding="utf-8-sig"
                ).strip()
            )

            if chunk_text:
                chunks.append(
                    chunk_text
                )

        if chunks:
            print(
                "      Mevcut Gemini chunk "
                "düzeni kullanılıyor."
            )

            return chunks

    source_text = input_file.read_text(
        encoding="utf-8-sig"
    ).strip()

    return split_text_into_chunks(
        text=source_text,
        max_characters=max_characters,
    )


def clean_text_file_with_groq(
    input_file,
    output_file,
    work_directory,
    max_characters=DEFAULT_MAX_CHARACTERS,
    max_attempts=DEFAULT_MAX_ATTEMPTS,
    request_interval_seconds=(
        DEFAULT_REQUEST_INTERVAL_SECONDS
    ),
):
    """
    Metni Groq üzerinden temizler.

    Var olan ve güncel kaynakla uyumlu Gemini
    çıktılarını korur.

    Gemini sonucu olmayan parçaları Groq ile
    tamamlar.

    Büyük Gemini chunk'larını Groq TPM sınırları
    için daha küçük alt parçalara böler.
    """

    input_file = Path(
        input_file
    ).resolve()

    output_file = Path(
        output_file
    ).resolve()

    work_directory = Path(
        work_directory
    ).resolve()

    load_project_environment(PROJECT_DIRECTORY / ".env")
    request_interval_seconds = float(
        os.getenv(
            "GROQ_REQUEST_DELAY_SECONDS",
            str(request_interval_seconds),
        )
    )

    api_key = os.getenv(
        "GROQ_API_KEY",
        "",
    ).strip()

    model_name = os.getenv(
        "GROQ_TEXT_MODEL",
        DEFAULT_MODEL,
    ).strip()

    env_max_attempts = os.getenv(
        "GROQ_MAX_ATTEMPTS",
        "",
    ).strip()

    env_request_interval = os.getenv(
        "GROQ_REQUEST_DELAY",
        "",
    ).strip()

    env_max_characters = os.getenv(
        "GROQ_MAX_CHARACTERS",
        "",
    ).strip()

    if env_max_characters:
        try:
            max_characters = int(
                env_max_characters
            )

        except ValueError:
            print(
                "      Uyarı: GROQ_MAX_CHARACTERS "
                "geçersiz. "
                f"{max_characters} kullanılacak."
            )

    if env_max_attempts:
        try:
            max_attempts = int(
                env_max_attempts
            )

        except ValueError:
            print(
                "      Uyarı: GROQ_MAX_ATTEMPTS "
                "geçersiz. "
                f"{max_attempts} kullanılacak."
            )

    if env_request_interval:
        try:
            request_interval_seconds = float(
                env_request_interval
            )

        except ValueError:
            print(
                "      Uyarı: GROQ_REQUEST_DELAY "
                "geçersiz. "
                f"{request_interval_seconds} kullanılacak."
            )

    if not api_key:
        raise GroqAuthenticationError(
            "GROQ_API_KEY ortam değişkeni bulunamadı."
        )

    if not model_name:
        raise GroqModelError(
            "GROQ_TEXT_MODEL boş bırakılamaz."
        )

    if not input_file.exists():
        raise FileNotFoundError(
            "Groq giriş dosyası bulunamadı: "
            f"{input_file}"
        )

    if max_attempts < 1:
        raise ValueError(
            "max_attempts en az 1 olmalıdır."
        )

    if max_characters < 500:
        raise ValueError(
            "max_characters en az 500 olmalıdır."
        )

    source_text = input_file.read_text(
        encoding="utf-8-sig"
    ).strip()

    if not source_text:
        raise ValueError(
            "Groq ile temizlenecek giriş metni boş."
        )

    chunks = load_source_chunks(
        input_file=input_file,
        work_directory=work_directory,
        max_characters=max_characters,
    )

    if not chunks:
        raise RuntimeError(
            "Groq için metin parçası oluşturulamadı."
        )

    groq_input_directory = (
        work_directory
        / "groq_text_chunks"
    )

    groq_output_directory = (
        work_directory
        / "groq_cleaned_chunks"
    )

    gemini_output_directory = (
        work_directory
        / "gemini_cleaned_chunks"
    )

    groq_metadata_directory = (
        work_directory
        / "groq_chunk_metadata"
    )

    review_directory = (
        work_directory
        / "groq_review_required"
    )

    for directory in (
        groq_input_directory,
        groq_output_directory,
        groq_metadata_directory,
        review_directory,
    ):
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    client = Groq(
        api_key=api_key
    )

    total_chunks = len(chunks)

    cleaned_chunks = []
    report_entries = []

    newly_generated_count = 0
    existing_gemini_count = 0
    existing_groq_count = 0
    invalid_existing_count = 0
    original_fallback_count = 0

    print(
        f"      Groq modeli: "
        f"{model_name}"
    )

    print(
        f"      Alt parça karakter sınırı: "
        f"{max_characters}"
    )

    print(
        f"      Toplam ana metin parçası: "
        f"{total_chunks}"
    )

    for index, original_chunk in enumerate(
        chunks,
        start=1,
    ):
        input_chunk_file = (
            groq_input_directory
            / f"chunk_{index:04d}.txt"
        )

        groq_cleaned_file = (
            groq_output_directory
            / f"chunk_{index:04d}_cleaned.txt"
        )

        gemini_cleaned_file = (
            gemini_output_directory
            / f"chunk_{index:04d}_cleaned.txt"
        )

        metadata_file = (
            groq_metadata_directory
            / f"chunk_{index:04d}.json"
        )

        review_file = (
            review_directory
            / f"chunk_{index:04d}_review.txt"
        )

        source_hash = calculate_text_hash(
            original_chunk
        )

        input_chunk_file.write_text(
            original_chunk,
            encoding="utf-8",
        )

        print()
        print(
            f"      Metin parçası: "
            f"{index}/{total_chunks}"
        )

        existing_text = None
        validation = None
        selected_provider = None

        (
            gemini_candidate,
            gemini_validation,
        ) = read_cleaned_candidate(
            candidate_file=gemini_cleaned_file,
            original_chunk=original_chunk,
        )

        if gemini_candidate:
            existing_text = gemini_candidate
            validation = gemini_validation
            selected_provider = "gemini"

            existing_gemini_count += 1

            print(
                "      Doğrulanmış mevcut Gemini "
                "çıktısı kullanıldı."
            )

        elif (
            gemini_cleaned_file.exists()
        ):
            invalid_existing_count += 1

            print(
                "      Mevcut Gemini çıktısı güncel "
                "chunk ile eşleşmedi."
            )

            if gemini_validation:
                print(
                    f"      Gemini benzerliği: "
                    f"{gemini_validation['similarity_ratio']:.4f}"
                )

        if existing_text is None:
            (
                groq_candidate,
                groq_validation,
            ) = read_cleaned_candidate(
                candidate_file=groq_cleaned_file,
                original_chunk=original_chunk,
            )

            if groq_candidate:
                existing_text = groq_candidate
                validation = groq_validation
                selected_provider = "groq"

                existing_groq_count += 1

                print(
                    "      Doğrulanmış mevcut Groq "
                    "çıktısı kullanıldı."
                )

            elif groq_cleaned_file.exists():
                invalid_existing_count += 1

                print(
                    "      Mevcut Groq çıktısı güncel "
                    "chunk ile eşleşmedi."
                )

                if groq_validation:
                    print(
                        f"      Groq benzerliği: "
                        f"{groq_validation['similarity_ratio']:.4f}"
                    )

        if existing_text is not None:
            cleaned_chunks.append(
                existing_text
            )

            report_entries.append(
                {
                    "chunk": index,
                    "provider": selected_provider,
                    "status": "existing",
                    "source_hash": source_hash,
                    "original_characters": len(
                        original_chunk
                    ),
                    "cleaned_characters": len(
                        existing_text
                    ),
                    "length_ratio": validation[
                        "length_ratio"
                    ],
                    "similarity_ratio": validation[
                        "similarity_ratio"
                    ],
                    "valid": validation[
                        "valid"
                    ],
                    "reasons": validation[
                        "reasons"
                    ],
                }
            )

            continue

        try:
            (
                cleaned_chunk,
                validation,
                subchunk_validations,
            ) = clean_source_chunk_with_subchunks(
                client=client,
                model_name=model_name,
                source_chunk=original_chunk,
                source_chunk_number=index,
                total_source_chunks=total_chunks,
                max_characters=max_characters,
                max_attempts=max_attempts,
                request_interval_seconds=(
                    request_interval_seconds
                ),
                subchunk_cache_directory=(
                    work_directory
                    / "groq_cleaned_subchunks"
                ),
            )

        except GroqValidationError as error:
            write_review_file(
                review_file=review_file,
                chunk_index=index,
                total_chunks=total_chunks,
                original_chunk=original_chunk,
                error=error,
            )

            raise GroqValidationError(
                f"{index}. parça kalite kontrolünden "
                "geçmedi. İnceleme dosyası: "
                f"{review_file}",
                candidate_text=getattr(
                    error,
                    "candidate_text",
                    "",
                ),
                validation=getattr(
                    error,
                    "validation",
                    {},
                ),
            ) from error
        fallback_subchunks = [
            entry
            for entry in subchunk_validations
            if (
                entry.get("status")
                == "original_fallback"
            )
        ]

        original_fallback_count += len(
            fallback_subchunks
        )

        if fallback_subchunks:
            print(
                f"      Uyarı: {len(fallback_subchunks)} "
                "alt parçada kaynak metin korundu."
            )

        groq_cleaned_file.write_text(
            cleaned_chunk,
            encoding="utf-8",
        )

        metadata = {
            "provider": "groq",
            "model": model_name,
            "source_hash": source_hash,
            "chunk": index,
            "original_characters": len(
                original_chunk
            ),
            "cleaned_characters": len(
                cleaned_chunk
            ),
            "length_ratio": validation[
                "length_ratio"
            ],
            "similarity_ratio": validation[
                "similarity_ratio"
            ],
            "subchunks": subchunk_validations,
        }

        metadata_file.write_text(
            json.dumps(
                metadata,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        cleaned_chunks.append(
            cleaned_chunk
        )

        newly_generated_count += 1

        report_entries.append(
            {
                "chunk": index,
                "provider": "groq",
                "status": "generated",
                "source_hash": source_hash,
                "original_characters": len(
                    original_chunk
                ),
                "cleaned_characters": len(
                    cleaned_chunk
                ),
                "length_ratio": validation[
                    "length_ratio"
                ],
                "similarity_ratio": validation[
                    "similarity_ratio"
                ],
                "valid": validation[
                    "valid"
                ],
                "reasons": validation[
                    "reasons"
                ],
                "subchunks": (
                    subchunk_validations
                ),
            }
        )

        if index < total_chunks:
            time.sleep(
                max(
                    0.0,
                    request_interval_seconds,
                )
            )

    final_text = "\n\n".join(
        cleaned_chunks
    ).strip()

    if not final_text:
        raise RuntimeError(
            "Birleştirilmiş Groq metni boş."
        )

    output_file.write_text(
        final_text,
        encoding="utf-8",
    )

    report_file = (
        work_directory
        / "groq_text_cleaning_report.json"
    )

    report_data = {
        "provider": "hybrid_gemini_groq",
        "groq_model": model_name,
        "input_file": str(input_file),
        "output_file": str(output_file),
        "total_chunks": total_chunks,
        "existing_gemini_chunks": (
            existing_gemini_count
        ),
        "existing_groq_chunks": (
            existing_groq_count
        ),
        "newly_generated_groq_chunks": (
            newly_generated_count
        ),
        "invalid_existing_chunks": (
            invalid_existing_count
        ),
        "source_characters": len(
            source_text
        ),
        "cleaned_characters": len(
            final_text
        ),
        "length_ratio": round(
            len(final_text)
            / len(source_text),
            4,
        ),
        "chunks": report_entries,
        "original_fallback_subchunks": (
            original_fallback_count
        ),
    }

    report_file.write_text(
        json.dumps(
            report_data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print(
        "      Groq metin temizliği tamamlandı."
    )

    print(
        f"      Mevcut Gemini parçası: "
        f"{existing_gemini_count}"
    )

    print(
        f"      Mevcut Groq parçası: "
        f"{existing_groq_count}"
    )

    print(
        f"      Yeni Groq parçası: "
        f"{newly_generated_count}"
    )

    print(
        f"      Geçersiz eski çıktı: "
        f"{invalid_existing_count}"
    )

    print(
        f"      Final metin: "
        f"{output_file}"
    )

    print(
        f"      Rapor: "
        f"{report_file}"
    )
    print(
        f"      Kaynağa geri dönülen alt parça: "
        f"{original_fallback_count}"
    )

    return {
        "provider": "hybrid_gemini_groq",
        "model": model_name,
        "total_chunks": total_chunks,
        "existing_gemini_chunks": (
            existing_gemini_count
        ),
        "existing_groq_chunks": (
            existing_groq_count
        ),
        "newly_generated_chunks": (
            newly_generated_count
        ),
        "invalid_existing_chunks": (
            invalid_existing_count
        ),
        "source_characters": len(
            source_text
        ),
        "cleaned_characters": len(
            final_text
        ),
        "output_file": str(
            output_file
        ),
        "report_file": str(
            report_file
        ),
        "original_fallback_subchunks": (
            original_fallback_count
        ),
    }