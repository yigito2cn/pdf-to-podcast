import argparse
import json
import re
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent

DEFAULT_INPUT_FILE = PROJECT_DIR / "podcast_final_text_verified.txt"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "tts_text_chunks"
DEFAULT_MANIFEST_FILE = PROJECT_DIR / "tts_text_manifest.json"

DEFAULT_MAX_CHARACTERS = 2800
DEFAULT_MIN_CHARACTERS = 500


ABBREVIATIONS = {
    "Dr.",
    "Doç.",
    "Prof.",
    "Sn.",
    "Bay.",
    "Bayan.",
    "Bkz.",
    "bkz.",
    "vb.",
    "vs.",
    "örn.",
    "Örn.",
    "çev.",
    "Çev.",
    "haz.",
    "Haz.",
    "No.",
    "no.",
    "s.",
    "ss.",
    "M.",
    "Mr.",
    "Mrs.",
    "Ms.",
    "St.",
    "Ltd.",
    "Inc.",
}


def normalize_text(text):
    """
    Satır sonlarını ve gereksiz boşlukları normalize eder.
    Paragraf yapısını korur.
    """
    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    text = text.replace(chr(173), "")

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def protect_abbreviations(text):
    """
    Bilinen kısaltmalardaki noktaları geçici olarak korur.
    Böylece Dr., Prof. ve vb. ifadeleri yanlışlıkla
    cümle sonu olarak değerlendirilmez.
    """
    protected_text = text
    replacements = {}

    for index, abbreviation in enumerate(
        sorted(ABBREVIATIONS, key=len, reverse=True),
        start=1,
    ):
        token = f"__ABBR_{index}__"

        if abbreviation in protected_text:
            protected_text = protected_text.replace(abbreviation, token)
            replacements[token] = abbreviation

    return protected_text, replacements


def restore_abbreviations(text, replacements):
    """Geçici kısaltma işaretlerini geri getirir."""
    restored_text = text

    for token, abbreviation in replacements.items():
        restored_text = restored_text.replace(token, abbreviation)

    return restored_text


def split_paragraph_into_sentences(paragraph):
    """
    Bir paragrafı cümle sınırlarına göre böler.

    Nokta, soru işareti ve ünlemden sonraki kapanış
    tırnaklarını ve parantezlerini cümle içinde korur.
    """

    paragraph = paragraph.strip()

    if not paragraph:
        return []

    protected_text, replacements = protect_abbreviations(
        paragraph
    )

    sentence_pattern = re.compile(
        r"""
        .+?
        (?:
            [.!?]+
            ["”’')\]]*
            (?=
                \s+
                [A-ZÇĞİÖŞÜ0-9"“‘(]
                |
                $
            )
            |
            $
        )
        """,
        flags=re.VERBOSE | re.DOTALL,
    )

    matches = sentence_pattern.findall(
        protected_text
    )

    cleaned_sentences = []

    for sentence in matches:
        sentence = restore_abbreviations(
            sentence,
            replacements,
        )

        sentence = re.sub(
            r"\s+",
            " ",
            sentence,
        ).strip()

        if sentence:
            cleaned_sentences.append(sentence)

    if not cleaned_sentences:
        restored_paragraph = restore_abbreviations(
            protected_text,
            replacements,
        ).strip()

        if restored_paragraph:
            cleaned_sentences.append(
                restored_paragraph
            )

    return cleaned_sentences


def split_text_into_sentences(text):
    """
    Metni paragraf ve cümle yapısını koruyarak böler.

    Her kayıt:
    - sentence alanında cümleyi
    - paragraph_end alanında paragraf bitişini saklar
    """
    paragraphs = re.split(r"\n\s*\n", text)
    sentence_records = []

    for paragraph in paragraphs:
        paragraph = paragraph.strip()

        if not paragraph:
            continue

        sentences = split_paragraph_into_sentences(paragraph)

        for index, sentence in enumerate(sentences):
            is_last_sentence = index == len(sentences) - 1

            sentence_records.append(
                {
                    "sentence": sentence,
                    "paragraph_end": is_last_sentence,
                }
            )

    return sentence_records


def split_long_sentence(sentence, max_characters):
    """
    Tek başına azami uzunluğu aşan bir cümleyi,
    mümkün olduğunca noktalı virgül, iki nokta ve
    virgül sınırlarından böler.
    """
    if len(sentence) <= max_characters:
        return [sentence]

    clauses = re.split(r"(?<=[;:,])\s+", sentence)
    parts = []
    current_part = ""

    for clause in clauses:
        clause = clause.strip()

        if not clause:
            continue

        if current_part:
            candidate = current_part + " " + clause
        else:
            candidate = clause

        if len(candidate) <= max_characters:
            current_part = candidate
            continue

        if current_part:
            parts.append(current_part)
            current_part = ""

        if len(clause) <= max_characters:
            current_part = clause
            continue

        words = clause.split()
        word_part = ""

        for word in words:
            if word_part:
                word_candidate = word_part + " " + word
            else:
                word_candidate = word

            if len(word_candidate) <= max_characters:
                word_part = word_candidate
            else:
                if word_part:
                    parts.append(word_part)
                word_part = word

        if word_part:
            current_part = word_part

    if current_part:
        parts.append(current_part)

    return parts


def expand_long_sentences(sentence_records, max_characters):
    """Azami uzunluktan büyük cümleleri güvenli alt parçalara ayırır."""
    expanded_records = []

    for record in sentence_records:
        sentence = record["sentence"]
        paragraph_end = record["paragraph_end"]

        sentence_parts = split_long_sentence(
            sentence,
            max_characters,
        )

        for index, sentence_part in enumerate(sentence_parts):
            is_last_part = index == len(sentence_parts) - 1

            expanded_records.append(
                {
                    "sentence": sentence_part,
                    "paragraph_end": paragraph_end and is_last_part,
                }
            )

    return expanded_records


def build_tts_chunks(
    sentence_records,
    max_characters,
    min_characters,
):
    """
    Cümleleri bölmeden TTS parçaları oluşturur.

    Cümle sonlarını korur ve paragraf bitişlerinde
    iki satır sonu kullanır.
    """

    chunks = []
    current_text = ""

    for record in sentence_records:
        sentence = record["sentence"].strip()
        paragraph_end = record["paragraph_end"]

        if not sentence:
            continue

        if current_text:
            separator = " "
        else:
            separator = ""

        candidate = (
            current_text
            + separator
            + sentence
        )

        if (
            current_text
            and len(candidate) > max_characters
        ):
            chunks.append(
                current_text.strip()
            )

            current_text = sentence
        else:
            current_text = candidate

        if paragraph_end:
            current_text = current_text.rstrip()

            if (
                len(current_text) >= min_characters
            ):
                chunks.append(
                    current_text.strip()
                )

                current_text = ""
            else:
                current_text += "\n\n"

    if current_text.strip():
        chunks.append(
            current_text.strip()
        )

    return chunks


def merge_small_final_chunk(chunks, max_characters, min_characters):
    """
    Son chunk gereğinden küçükse ve önceki chunk'a
    sığıyorsa iki parçayı birleştirir.
    """
    if len(chunks) < 2:
        return chunks

    last_chunk = chunks[-1]

    if len(last_chunk) >= min_characters:
        return chunks

    previous_chunk = chunks[-2]

    combined = previous_chunk.rstrip() + "\n\n" + last_chunk.lstrip()

    if len(combined) <= max_characters:
        chunks[-2] = combined
        chunks.pop()

    return chunks


def remove_old_tts_chunks(output_dir):
    """Önceki çalıştırmadan kalan TTS metin parçalarını siler."""
    for old_file in output_dir.glob("tts_chunk_*.txt"):
        old_file.unlink()


def validate_chunks(chunks, max_characters):
    """Oluşturulan parçaların temel kalite kontrollerini yapar."""
    warnings = []

    for index, chunk in enumerate(chunks, start=1):
        if not chunk.strip():
            warnings.append(f"Chunk {index} boş.")

        if len(chunk) > max_characters:
            warnings.append(
                f"Chunk {index}, karakter sınırını aşıyor: {len(chunk)}"
            )

        if chr(173) in chunk:
            warnings.append(f"Chunk {index} içinde soft hyphen var.")

        if re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]-\s*\n[a-zçğıöşü]", chunk):
            warnings.append(
                f"Chunk {index} içinde satır sonu kelime bölünmesi olabilir."
            )

    return warnings


def save_chunks(chunks, output_dir):
    """TTS parçalarını ayrı UTF-8 dosyaları olarak kaydeder."""
    output_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []

    for index, chunk in enumerate(chunks, start=1):
        output_path = output_dir / f"tts_chunk_{index:04d}.txt"

        chunk_text = chunk.strip()

        if output_path.exists():
            existing_text = output_path.read_text(
                encoding="utf-8-sig"
            ).strip()
            if existing_text != chunk_text:
                raise RuntimeError(
                    "Mevcut TTS chunk güncel kaynakla uyuşmuyor; "
                    f"dosya korundu: {output_path}"
                )
        else:
            temporary_file = output_path.with_suffix(".txt.tmp")
            temporary_file.write_text(
                chunk_text,
                encoding="utf-8",
            )
            temporary_file.replace(output_path)

        saved_files.append(output_path)

        word_count = len([word for word in chunk.split() if word])

        print(
            f"Kaydedildi: {output_path.name} | "
            f"{len(chunk)} karakter | "
            f"{word_count} kelime"
        )

    expected_names = {path.name for path in saved_files}
    unexpected_files = [
        path
        for path in output_dir.glob("tts_chunk_*.txt")
        if path.name not in expected_names
    ]
    if unexpected_files:
        raise RuntimeError(
            "Çıktı klasöründe güncel metinde bulunmayan TTS "
            "chunk dosyaları var; hiçbir dosya silinmedi: "
            + ", ".join(path.name for path in unexpected_files)
        )

    return saved_files


def save_manifest(
    chunks,
    saved_files,
    input_file,
    manifest_file,
    max_characters,
):
    """
    TTS işlemi için chunk sırasını ve özelliklerini
    JSON manifest dosyasına kaydeder.
    """
    manifest_chunks = []

    for index, item in enumerate(zip(chunks, saved_files), start=1):
        chunk, file_path = item

        word_count = len([word for word in chunk.split() if word])

        manifest_chunks.append(
            {
                "index": index,
                "file": str(file_path),
                "characters": len(chunk),
                "words": word_count,
                "audio_file": f"tts_chunk_{index:04d}.mp3",
            }
        )

    manifest = {
        "source_file": str(input_file),
        "maximum_characters": max_characters,
        "total_chunks": len(chunks),
        "total_characters": sum(len(chunk) for chunk in chunks),
        "chunks": manifest_chunks,
    }

    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = manifest_file.with_suffix(
        manifest_file.suffix + ".tmp"
    )
    temporary_file.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary_file.replace(manifest_file)


def prepare_tts_chunks(
    *,
    input_file,
    output_dir,
    manifest_file,
    max_characters=DEFAULT_MAX_CHARACTERS,
    min_characters=DEFAULT_MIN_CHARACTERS,
):
    input_file = Path(input_file).resolve()
    output_dir = Path(output_dir).resolve()
    manifest_file = Path(manifest_file).resolve()

    if not input_file.is_file():
        raise FileNotFoundError(f"Girdi dosyası bulunamadı: {input_file}")
    if max_characters < 200:
        raise ValueError("max_characters en az 200 olmalıdır.")
    if min_characters < 0 or min_characters >= max_characters:
        raise ValueError(
            "min_characters sıfır veya daha büyük ve "
            "max_characters değerinden küçük olmalıdır."
        )

    source_text = normalize_text(
        input_file.read_text(encoding="utf-8-sig")
    )
    if not source_text:
        raise ValueError("Girdi metni boş.")

    sentence_records = split_text_into_sentences(source_text)
    sentence_records = expand_long_sentences(
        sentence_records=sentence_records,
        max_characters=max_characters,
    )
    chunks = build_tts_chunks(
        sentence_records=sentence_records,
        max_characters=max_characters,
        min_characters=min_characters,
    )
    chunks = merge_small_final_chunk(
        chunks=chunks,
        max_characters=max_characters,
        min_characters=min_characters,
    )
    if not chunks:
        raise RuntimeError("Hiçbir TTS chunk oluşturulamadı.")

    warnings = validate_chunks(chunks, max_characters)
    saved_files = save_chunks(chunks, output_dir)
    save_manifest(
        chunks=chunks,
        saved_files=saved_files,
        input_file=input_file,
        manifest_file=manifest_file,
        max_characters=max_characters,
    )
    return {
        "source_characters": len(source_text),
        "total_chunks": len(chunks),
        "saved_files": saved_files,
        "warnings": warnings,
        "manifest_file": manifest_file,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Doğrulanmış podcast metnini cümle "
            "sınırlarına göre TTS parçalarına ayırır."
        )
    )

    parser.add_argument(
        "input_file",
        nargs="?",
        type=Path,
        default=DEFAULT_INPUT_FILE,
        help=(
            "TTS için bölünecek metin dosyası. "
            "Varsayılan: podcast_final_text_verified.txt"
        ),
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=(
            "TTS chunk dosyalarının kaydedileceği klasör. "
            "Varsayılan: tts_text_chunks"
        ),
    )

    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_FILE,
        help="Oluşturulacak JSON manifest dosyası.",
    )

    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARACTERS,
        help=(
            "Her TTS parçası için azami karakter sayısı. "
            f"Varsayılan: {DEFAULT_MAX_CHARACTERS}"
        ),
    )

    parser.add_argument(
        "--min-chars",
        type=int,
        default=DEFAULT_MIN_CHARACTERS,
        help=(
            "Mümkün olduğunda hedeflenen asgari parça "
            f"uzunluğu. Varsayılan: {DEFAULT_MIN_CHARACTERS}"
        ),
    )

    args = parser.parse_args()

    input_file = args.input_file.resolve()
    output_dir = args.output_dir.resolve()
    manifest_file = args.manifest.resolve()

    if not input_file.exists():
        raise FileNotFoundError(f"Girdi dosyası bulunamadı: {input_file}")

    if not input_file.is_file():
        raise ValueError(f"Girdi yolu dosya değil: {input_file}")

    if args.max_chars < 200:
        raise ValueError("max-chars en az 200 olmalıdır.")

    if args.min_chars < 0:
        raise ValueError("min-chars negatif olamaz.")

    if args.min_chars >= args.max_chars:
        raise ValueError("min-chars, max-chars değerinden küçük olmalıdır.")

    result = prepare_tts_chunks(
        input_file=input_file,
        output_dir=output_dir,
        manifest_file=manifest_file,
        max_characters=args.max_chars,
        min_characters=args.min_chars,
    )

    print()
    print("TTS metin parçalama tamamlandı.")
    print(f"Kaynak: {input_file}")
    print(f"Toplam kaynak karakteri: {result['source_characters']}")
    print(f"Oluşturulan TTS chunk: {result['total_chunks']}")
    print(f"Çıktı klasörü: {output_dir}")
    print(f"Manifest: {manifest_file}")

    if result["warnings"]:
        print()
        print("Kontrol uyarıları:")

        for warning in result["warnings"]:
            print(f"- {warning}")
    else:
        print()
        print("Tüm TTS parçaları temel kontrolden geçti.")


if __name__ == "__main__":
    main()