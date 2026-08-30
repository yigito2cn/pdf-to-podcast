import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pymupdf

from pipeline.job_state import (
    JobState,
    JobStateStore,
    calculate_file_sha256,
    create_job_id,
)
from pipeline.edge_tts_provider import (
    DEFAULT_EDGE_VOICE,
    EdgeTTSPartialError,
    generate_edge_tts,
)
from pipeline.config import PRESERVE_API_KEYS_ENV
from pipeline.text_provider import clean_text_with_fallback
from split_text_for_tts import prepare_tts_chunks
from txt_to_readaloud_pdf import create_pdf


PROJECT_DIR = Path(__file__).resolve().parent
WORK_ROOT = PROJECT_DIR / "work"
OUTPUT_ROOT = PROJECT_DIR / "output"



def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "PDF metnini çıkarır, ön temizler ve Gemini ile "
            "seslendirmeye uygun hale getirir."
        )
    )

    parser.add_argument(
        "--pdf",
        type=Path,
        default=None,
        help=(
            "İşlenecek PDF dosyası. Belirtilmezse proje veya "
            "input klasöründeki ilk PDF kullanılır."
        ),
    )

    parser.add_argument(
        "--start-page",
        type=int,
        default=None,
        help="Başlangıç PDF sayfası. Sayım 1'den başlar.",
    )

    parser.add_argument(
        "--end-page",
        type=int,
        default=None,
        help="Bitiş PDF sayfası. Bu sayfa işleme dahildir.",
    )

    parser.add_argument(
        "--stage",
        choices=("all", "extract", "text", "tts"),
        default="all",
        help=(
            "all: extraction, temizlik ve ses; "
            "extract: extraction ve ön temizlik; "
            "text: mevcut precleaned_text.txt üzerinden temiz metin; "
            "tts: mevcut job clean_text.txt üzerinden ses."
        ),
    )

    parser.add_argument(
        "--job-dir",
        type=Path,
        default=None,
        help=(
            "--stage text kullanılırken mevcut çalışma "
            "klasörünün yolu."
        ),
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Mevcut ara dosyalar bulunsa bile extraction ve "
            "Gemini temizliğini yeniden çalıştırır."
        ),
    )

    parser.add_argument(
        "--gemini-api-key",
        default=None,
        help="Gemini API anahtarı; diske veya rapora yazılmaz.",
    )

    parser.add_argument(
        "--groq-api-key",
        default=None,
        help="Groq API anahtarı; diske veya rapora yazılmaz.",
    )

    parser.add_argument(
        "--tts-limit",
        type=int,
        default=None,
        help="Bu çalıştırmada kontrol edilecek ilk N TTS chunk.",
    )

    parser.add_argument(
        "--edge-voice",
        default=DEFAULT_EDGE_VOICE,
        help="Gemini TTS kullanılamazsa Edge TTS Türkçe ses adı.",
    )

    parser.add_argument(
        "--target-minutes",
        type=float,
        default=None,
        help="Bu çalıştırmada hedeflenen yaklaşık yeni ses süresi.",
    )

    parser.add_argument(
        "--words-per-minute",
        type=float,
        default=150.0,
        help="Hedef süre hesabında tahmini Türkçe okuma hızı.",
    )

    return parser.parse_args()


def print_header() -> None:
    print()
    print("=" * 68)
    print("PDF TO PODCAST")
    print("=" * 68)
    print()
    print(
        "PDF metnini temizleyip seslendirmeye uygun "
        "bir metne dönüştürür."
    )
    print()


def find_pdf(explicit_pdf: Path | None) -> Path:
    """PDF dosyasını komut satırı, proje veya input klasöründe bulur."""

    if explicit_pdf is not None:
        explicit_pdf = explicit_pdf.resolve()

        if not explicit_pdf.exists():
            raise FileNotFoundError(
                f"PDF dosyası bulunamadı: {explicit_pdf}"
            )

        return explicit_pdf

    candidates = sorted(PROJECT_DIR.glob("*.pdf"))

    if not candidates:
        candidates = sorted(
            (PROJECT_DIR / "input").glob("*.pdf")
        )

    if not candidates:
        raise FileNotFoundError(
            "Proje veya input klasöründe PDF bulunamadı."
        )

    return candidates[0].resolve()


def ask_page_number(
    prompt: str,
    default_value: int,
    minimum: int,
    maximum: int,
) -> int:
    """Kullanıcıdan güvenli biçimde sayfa numarası alır."""

    while True:
        raw_value = input(
            f"{prompt} [{default_value}]: "
        ).strip()

        if not raw_value:
            value = default_value
        else:
            try:
                value = int(raw_value)
            except ValueError:
                print("Lütfen geçerli bir tam sayı girin.")
                continue

        if value < minimum or value > maximum:
            print(
                f"Sayfa numarası {minimum}-{maximum} "
                "arasında olmalıdır."
            )
            continue

        return value


def create_job_name(
    pdf_file: Path,
    start_page: int,
    end_page: int,
) -> str:
    """Dosya sistemine uygun çalışma klasörü adı oluşturur."""

    safe_stem = re.sub(
        r"[^A-Za-z0-9ÇĞİÖŞÜçğıöşü_-]+",
        "_",
        pdf_file.stem,
    ).strip("_")

    return (
        f"{safe_stem}_pages_"
        f"{start_page}_{end_page}"
    )


def extract_pdf_text(
    pdf_file: Path,
    start_page: int,
    end_page: int,
) -> str:
    """Belirtilen fiziksel PDF sayfalarından metin çıkarır."""

    extracted_pages = []

    with pymupdf.open(pdf_file) as document:
        total_pages = len(document)

        if start_page < 1:
            raise ValueError(
                "Başlangıç sayfası en az 1 olmalıdır."
            )

        if end_page < start_page:
            raise ValueError(
                "Bitiş sayfası başlangıç sayfasından "
                "küçük olamaz."
            )

        if end_page > total_pages:
            raise ValueError(
                f"Bitiş sayfası PDF'in toplam "
                f"{total_pages} sayfa sayısını aşıyor."
            )

        for page_number in range(
            start_page,
            end_page + 1,
        ):
            page = document[page_number - 1]

            page_text = page.get_text(
                "text",
                sort=True,
            ).strip()

            extracted_pages.append(
                f"\n\n[[PDF_PAGE_{page_number}]]\n\n"
                f"{page_text}"
            )

    return "".join(extracted_pages).strip()


def normalize_header_for_comparison(line: str) -> str:
    """Tekrarlanan sayfa üst başlıklarının karşılaştırmasını kolaylaştırır."""

    normalized = re.sub(
        r"\s+",
        "",
        line,
    ).upper()

    translation = str.maketrans(
        {
            "İ": "I",
            "Ü": "U",
            "Ş": "S",
            "Ğ": "G",
            "Ö": "O",
            "Ç": "C",
        }
    )

    return normalized.translate(translation)


def preclean_pdf_text(text: str) -> str:
    """
    Gemini öncesinde güvenli, kural tabanlı PDF temizliği yapar.
    """

    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    text = text.replace(chr(173), "")

    text = re.sub(
        r"\[\[PDF_PAGE_\d+\]\]",
        "\n",
        text,
    )

    raw_lines = text.splitlines()

    repeated_headers = {
        "STATUENDISESI",
        "SNOPLUK",
        "SEVGISIZLIK",
    }

    cleaned_lines = []

    for raw_line in raw_lines:
        line = re.sub(
            r"[ \t]+",
            " ",
            raw_line.strip(),
        )

        if not line:
            cleaned_lines.append("")
            continue

        if re.fullmatch(r"\d{1,4}", line):
            continue

        normalized_header = (
            normalize_header_for_comparison(line)
        )

        if normalized_header in repeated_headers:
            continue

        if re.search(
            r"\bISBN\b",
            line,
            flags=re.IGNORECASE,
        ):
            continue

        if re.search(
            r"https?://|www\.|@\w+",
            line,
            flags=re.IGNORECASE,
        ):
            continue

        cleaned_lines.append(line)

    output_parts = []
    current_paragraph = ""

    for line in cleaned_lines:
        if not line:
            if current_paragraph:
                output_parts.append(
                    current_paragraph.strip()
                )
                current_paragraph = ""

            continue

        if not current_paragraph:
            current_paragraph = line
            continue

        if current_paragraph.endswith("-"):
            current_paragraph = (
                current_paragraph[:-1] + line
            )
        else:
            current_paragraph += " " + line

    if current_paragraph:
        output_parts.append(
            current_paragraph.strip()
        )

    cleaned_text = "\n\n".join(output_parts)

    cleaned_text = re.sub(
        r"[ \t]{2,}",
        " ",
        cleaned_text,
    )

    cleaned_text = re.sub(
        r"\n{3,}",
        "\n\n",
        cleaned_text,
    )

    return cleaned_text.strip()


def run_extraction_stage(
    pdf_file: Path,
    start_page: int,
    end_page: int,
    work_directory: Path,
    force: bool,
) -> tuple[Path, Path]:
    """PDF extraction ve ön temizleme aşamalarını çalıştırır."""

    raw_text_file = (
        work_directory / "raw_text.txt"
    )

    precleaned_text_file = (
        work_directory / "precleaned_text.txt"
    )

    if (
        raw_text_file.exists()
        and precleaned_text_file.exists()
        and not force
    ):
        print(
            "[1/3] Extraction dosyaları zaten mevcut, "
            "tekrar oluşturulmadı."
        )

        return raw_text_file, precleaned_text_file

    print("[1/3] PDF sayfaları okunuyor...")

    raw_text = extract_pdf_text(
        pdf_file=pdf_file,
        start_page=start_page,
        end_page=end_page,
    )

    raw_text_file.write_text(
        raw_text,
        encoding="utf-8",
    )

    print(
        f"      Çıkarılan karakter: "
        f"{len(raw_text):,}"
    )
    print(f"      Ham metin: {raw_text_file}")
    print()

    print("[2/3] PDF metni ön temizleniyor...")

    precleaned_text = preclean_pdf_text(
        raw_text
    )

    precleaned_text_file.write_text(
        precleaned_text,
        encoding="utf-8",
    )

    print(
        f"      Ham karakter: "
        f"{len(raw_text):,}"
    )
    print(
        f"      Temiz karakter: "
        f"{len(precleaned_text):,}"
    )
    print(
        f"      Kalan soft hyphen: "
        f"{precleaned_text.count(chr(173))}"
    )
    print(
        f"      Ön temizlenmiş metin: "
        f"{precleaned_text_file}"
    )
    print()

    return raw_text_file, precleaned_text_file


def run_gemini_stage(
    precleaned_text_file: Path,
    work_directory: Path,
    output_directory: Path,
    force: bool,
    state_store: JobStateStore | None = None,
    state: JobState | None = None,
) -> Path:
    """Gemini ve Groq fallback metin aşamasını çalıştırır."""

    final_output_file = (
        output_directory / "clean_text.txt"
    )

    if force and final_output_file.exists():
        final_output_file.unlink()

    print("[3/3] Metin Gemini/Groq fallback ile düzeltiliyor...")

    result = clean_text_with_fallback(
        input_file=precleaned_text_file,
        output_file=final_output_file,
        work_directory=work_directory,
        state_store=state_store,
        state=state,
    )

    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    readaloud_file = output_directory / "readaloud.pdf"
    create_pdf(
        input_file=final_output_file,
        output_file=readaloud_file,
        title=work_directory.name,
    )

    if state_store is not None and state is not None:
        state.outputs["readaloud_pdf"] = str(readaloud_file)
        state_store.save(state)

    print()
    print(f"Metin durumu: {result['status']}")
    print(f"Sağlayıcı: {result['provider']}")
    print(
        f"Metin parçası: "
        f"{result['total_chunks']}"
    )
    print(
        f"Final metin: "
        f"{final_output_file}"
    )
    print(f"Read Aloud PDF: {readaloud_file}")

    return final_output_file


def resolve_text_stage_job(
    job_directory: Path | None,
) -> Path:
    """Text-only aşaması için çalışma klasörünü belirler."""

    if job_directory is not None:
        job_directory = job_directory.resolve()

        if not job_directory.exists():
            raise FileNotFoundError(
                f"Çalışma klasörü bulunamadı: "
                f"{job_directory}"
            )

        return job_directory

    work_directories = sorted(
        (
            directory
            for directory in WORK_ROOT.glob("*")
            if directory.is_dir()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    if not work_directories:
        raise FileNotFoundError(
            "Devam edilecek çalışma klasörü bulunamadı."
        )

    return work_directories[0]


def initialize_job(
    *,
    pdf_file: Path,
    start_page: int,
    end_page: int,
) -> tuple[Path, Path, JobStateStore, JobState]:
    pdf_sha256 = calculate_file_sha256(pdf_file)
    job_id = create_job_id(pdf_sha256, start_page, end_page)
    legacy_directory = WORK_ROOT / create_job_name(
        pdf_file,
        start_page,
        end_page,
    )
    work_directory = (
        legacy_directory
        if legacy_directory.exists()
        else WORK_ROOT / job_id
    )
    state_store = JobStateStore(work_directory)
    state = state_store.load_or_create(
        JobState(
            job_id=job_id,
            pdf_file=str(pdf_file),
            pdf_sha256=pdf_sha256,
            start_page=start_page,
            end_page=end_page,
        )
    )
    output_directory = resolve_output_directory(
        state=state,
        work_directory=work_directory,
    )
    return (
        work_directory,
        output_directory,
        state_store,
        state,
    )


def get_output_directory(start_page: int, end_page: int) -> Path:
    return OUTPUT_ROOT / f"{start_page}-{end_page}"


def resolve_output_directory(
    *,
    state: JobState,
    work_directory: Path,
) -> Path:
    output_directory = get_output_directory(
        state.start_page,
        state.end_page,
    )
    legacy_output_directory = OUTPUT_ROOT / work_directory.name

    output_directory.mkdir(parents=True, exist_ok=True)
    if legacy_output_directory != output_directory:
        for legacy_file in legacy_output_directory.glob("*"):
            target_file = output_directory / legacy_file.name
            if legacy_file.is_file() and not target_file.exists():
                shutil.copy2(legacy_file, target_file)

    return output_directory


def run_tts_stage(
    *,
    clean_text_file: Path,
    work_directory: Path,
    output_directory: Path,
    state_store: JobStateStore,
    state: JobState,
    limit: int | None = None,
    edge_voice: str = DEFAULT_EDGE_VOICE,
    target_minutes: float | None = None,
    words_per_minute: float = 150.0,
) -> int:
    chunk_directory = work_directory / "tts_chunks"
    audio_directory = work_directory / "tts_audio"
    manifest_file = work_directory / "tts_manifest.json"
    report_file = work_directory / "tts_generation_report.json"
    final_file = output_directory / "podcast.wav"

    try:
        chunk_result = prepare_tts_chunks(
            input_file=clean_text_file,
            output_dir=chunk_directory,
            manifest_file=manifest_file,
        )
    except RuntimeError as error:
        if not (
            "Mevcut TTS chunk güncel kaynakla uyuşmuyor" in str(error)
            or "hiçbir dosya silinmedi" in str(error)
        ):
            raise
        source_suffix = calculate_file_sha256(clean_text_file)[:12]
        chunk_directory = work_directory / f"tts_chunks_{source_suffix}"
        audio_directory = work_directory / f"tts_audio_{source_suffix}"
        manifest_file = work_directory / f"tts_manifest_{source_suffix}.json"
        report_file = (
            work_directory / f"tts_generation_report_{source_suffix}.json"
        )
        chunk_result = prepare_tts_chunks(
            input_file=clean_text_file,
            output_dir=chunk_directory,
            manifest_file=manifest_file,
        )
    state.tts_chunks_total = chunk_result["total_chunks"]
    state_store.checkpoint(
        state,
        status="tts_chunked",
        stage="tts_chunked",
    )

    command = [
        sys.executable,
        str(PROJECT_DIR / "batch_gemini_tts.py"),
        "--input-dir",
        str(chunk_directory),
        "--output-dir",
        str(audio_directory),
        "--final-file",
        str(final_file),
        "--report-file",
        str(report_file),
        "--job-dir",
        str(work_directory),
    ]
    if limit is not None:
        command.extend(("--limit", str(limit)))
    if target_minutes is not None:
        command.extend(("--target-minutes", str(target_minutes)))
        command.extend(("--words-per-minute", str(words_per_minute)))

    completed = subprocess.run(command, check=False)
    current_state = state_store.load()
    should_try_edge = (
        completed.returncode != 0
        or current_state.status == "paused_quota"
    )
    if should_try_edge:
        edge_final_file = output_directory / "podcast_edge.mp3"
        try:
            edge_result = generate_edge_tts(
                input_directory=chunk_directory,
                output_directory=work_directory / "edge_audio",
                final_file=edge_final_file,
                voice=edge_voice,
                limit=limit,
                target_minutes=target_minutes,
                words_per_minute=words_per_minute,
            )
            current_state = state_store.load()
            current_state.tts_chunks_total = edge_result[
                "source_total_chunks"
            ]
            current_state.tts_chunks_completed = edge_result[
                "completed_chunks"
            ]
            current_state.outputs["podcast"] = str(edge_final_file)
            state_store.checkpoint(
                current_state,
                status="completed",
                stage="completed",
                paused_at=None,
                paused_provider=None,
                resume_after=None,
                last_error_type=None,
                last_error_message=None,
            )
        except Exception as edge_error:
            current_state = state_store.load()
            if isinstance(edge_error, EdgeTTSPartialError):
                current_state.tts_chunks_completed = (
                    edge_error.completed_chunks
                )
                current_state.outputs["partial_podcast_edge"] = str(
                    edge_error.partial_file
                )
            current_state.last_error_message = (
                "Gemini TTS kullanılamadı; Edge TTS de "
                f"tamamlanamadı: {type(edge_error).__name__}: {edge_error}"
            )
            if current_state.status == "paused_quota":
                state_store.save(current_state)
            else:
                current_state.last_error_type = "tts_unavailable"
                state_store.checkpoint(
                    current_state,
                    status="completed_text_only",
                    stage="completed_text_only",
                )
    return completed.returncode


def main() -> None:
    args = parse_arguments()

    if args.gemini_api_key:
        os.environ["GEMINI_API_KEY"] = args.gemini_api_key
    if args.groq_api_key:
        os.environ["GROQ_API_KEY"] = args.groq_api_key
    if args.gemini_api_key or args.groq_api_key:
        os.environ[PRESERVE_API_KEYS_ENV] = "1"

    print_header()

    WORK_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    if args.stage in ("text", "tts"):
        work_directory = resolve_text_stage_job(
            args.job_dir
        )

        state_store = JobStateStore(work_directory)
        state = (
            state_store.load()
            if state_store.status_file.exists()
            else None
        )

        if args.stage == "tts":
            if state is None:
                raise FileNotFoundError(
                    f"Job durum dosyası bulunamadı: "
                    f"{state_store.status_file}"
                )
            output_directory = resolve_output_directory(
                state=state,
                work_directory=work_directory,
            )
            clean_text_file = output_directory / "clean_text.txt"
            if not clean_text_file.exists():
                raise FileNotFoundError(
                    f"Temiz metin bulunamadı: {clean_text_file}"
                )
            run_tts_stage(
                clean_text_file=clean_text_file,
                work_directory=work_directory,
                output_directory=output_directory,
                state_store=state_store,
                state=state,
                limit=args.tts_limit,
                edge_voice=args.edge_voice,
                target_minutes=args.target_minutes,
                words_per_minute=args.words_per_minute,
            )
            return

        precleaned_text_file = (
            work_directory / "precleaned_text.txt"
        )

        if not precleaned_text_file.exists():
            raise FileNotFoundError(
                f"Ön temizlenmiş metin bulunamadı: "
                f"{precleaned_text_file}"
            )

        if state is None:
            raise FileNotFoundError(
                f"Job durum dosyası bulunamadı: "
                f"{state_store.status_file}"
            )

        output_directory = resolve_output_directory(
            state=state,
            work_directory=work_directory,
        )

        run_gemini_stage(
            precleaned_text_file=precleaned_text_file,
            work_directory=work_directory,
            output_directory=output_directory,
            force=args.force,
            state_store=state_store if state is not None else None,
            state=state,
        )

        return

    pdf_file = find_pdf(args.pdf)

    with pymupdf.open(pdf_file) as document:
        total_pages = len(document)

    print(f"PDF bulundu: {pdf_file.name}")
    print()
    print(
        f"PDF toplam sayfa sayısı: "
        f"{total_pages}"
    )
    print()
    print(
        "Not: Sayfalar fiziksel PDF sayfalarıdır."
    )
    print()

    start_page = args.start_page

    if start_page is None:
        start_page = ask_page_number(
            prompt="Başlangıç sayfası",
            default_value=1,
            minimum=1,
            maximum=total_pages,
        )

    end_page = args.end_page

    if end_page is None:
        end_page = ask_page_number(
            prompt="Bitiş sayfası",
            default_value=min(
                start_page + 2,
                total_pages,
            ),
            minimum=start_page,
            maximum=total_pages,
        )

    if end_page < start_page:
        raise ValueError(
            "Bitiş sayfası başlangıç sayfasından "
            "küçük olamaz."
        )

    (
        work_directory,
        output_directory,
        state_store,
        state,
    ) = initialize_job(
        pdf_file=pdf_file,
        start_page=start_page,
        end_page=end_page,
    )

    work_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    print()
    print("=" * 68)
    print("ÇALIŞMA BİLGİLERİ")
    print("=" * 68)
    print()
    print(f"PDF: {pdf_file.name}")
    print(
        f"Sayfa aralığı: "
        f"{start_page}-{end_page}"
    )
    print(
        f"İşlenecek sayfa sayısı: "
        f"{end_page - start_page + 1}"
    )
    print(
        f"Çalışma klasörü: "
        f"{work_directory}"
    )
    print(
        f"Çıktı klasörü: "
        f"{output_directory}"
    )
    print()

    _, precleaned_text_file = run_extraction_stage(
        pdf_file=pdf_file,
        start_page=start_page,
        end_page=end_page,
        work_directory=work_directory,
        force=args.force,
    )

    state_store.checkpoint(
        state,
        status="precleaned",
        stage="precleaned",
    )

    if args.stage == "extract":
        print()
        print(
            "Extraction aşaması tamamlandı. "
            "Gemini temizliği çalıştırılmadı."
        )
        return

    clean_text_file = run_gemini_stage(
        precleaned_text_file=precleaned_text_file,
        work_directory=work_directory,
        output_directory=output_directory,
        force=args.force,
        state_store=state_store,
        state=state,
    )

    state = state_store.load()
    if args.stage == "all" and state.status == "text_completed":
        run_tts_stage(
            clean_text_file=clean_text_file,
            work_directory=work_directory,
            output_directory=output_directory,
            state_store=state_store,
            state=state,
            limit=args.tts_limit,
            edge_voice=args.edge_voice,
            target_minutes=args.target_minutes,
            words_per_minute=args.words_per_minute,
        )

    print()
    print("=" * 68)
    print("İŞLEM TAMAMLANDI")
    print("=" * 68)
    print()
    print(
        "Metin seslendirme aşaması için hazır."
    )
    print(
        f"Çıktı klasörü: "
        f"{output_directory}"
    )


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print()
        print(
            "İşlem kullanıcı tarafından iptal edildi."
        )
        raise SystemExit(1)

    except Exception as error:
        print()
        print("İşlem sırasında hata oluştu:")
        print(type(error).__name__)
        print(error)
        raise SystemExit(1)