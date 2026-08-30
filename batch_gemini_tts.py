from pathlib import Path
from datetime import datetime, timedelta, timezone
import argparse
import json
import os
import random
import time
import wave

from google import genai
from google.genai import types

from pipeline.cache import (
    cache_metadata_path,
    validate_cached_output,
    write_cache_metadata,
)
from pipeline.config import load_project_environment
from pipeline.job_state import JobState, JobStateStore
from pipeline.provider_errors import (
    ProviderErrorType,
    classify_provider_error,
    parse_retry_after_seconds,
)


PROJECT_DIR = Path(__file__).resolve().parent

DEFAULT_INPUT_DIR = (
    PROJECT_DIR / "tts_continuation_chunks"
)

DEFAULT_OUTPUT_DIR = (
    PROJECT_DIR / "tts_continuation_audio"
)

DEFAULT_FINAL_FILE = (
    PROJECT_DIR / "podcast_continuation_complete.wav"
)

DEFAULT_REPORT_FILE = (
    PROJECT_DIR / "tts_generation_report.json"
)

SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2

DEFAULT_SILENCE_MS = 450
DEFAULT_MAX_RETRIES = 6


class GeminiTTSDailyQuotaError(RuntimeError):
    def __init__(self, provider_error: Exception) -> None:
        super().__init__("GEMINI_TTS_DAILY_QUOTA_EXHAUSTED")
        self.retry_after_seconds = parse_retry_after_seconds(provider_error)


class GeminiTTSModelError(RuntimeError):
    pass


TTS_INSTRUCTION = """
Aşağıdaki Türkçe metni aynen oku.

Okuma stili:
- Doğal, gerçekçi ve sıcak bir ses kullan.
- Profesyonel bir sesli kitap anlatıcısı gibi oku.
- Sakin, düşünceli ve akıcı bir anlatım kullan.
- Orta-yavaş bir konuşma hızı kullan.
- Türkçe kelimeleri açık ve doğal telaffuz et.
- Özel isimleri mümkün olduğunca doğru telaffuz et.
- Noktalama işaretlerinde doğal duraklamalar yap.
- Paragraflar arasında kısa ve doğal bir duraklama kullan.
- Alıntıları doğal fakat abartısız bir tonla oku.
- Duygulu fakat teatral olmayan bir anlatım kullan.

Kesin kurallar:
- Metni özetleme.
- Metni değiştirme.
- Metni yeniden ifade etme.
- Metne yeni bilgi ekleme.
- Giriş veya kapanış cümlesi ekleme.
- Başlık ya da açıklama ekleme.
- Yalnızca verilen metni oku.

Okunacak metin:
""".strip()

def is_daily_quota_error(error: Exception) -> bool:
    """Gemini günlük istek kotasının dolduğunu tespit eder."""

    message = str(error).lower()

    daily_markers = (
        "requestsperdayperprojectpermodel",
        "generate requests per day",
        "requests per day",
        "tokens per day",
        "generaterequestsperday",
        "quota exceeded for quota metric",
        "daily quota",
        "rpd",
        "tpd",
    )

    return any(
        marker in message
        for marker in daily_markers
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "TTS metin parçalarını Gemini kullanarak WAV "
            "dosyalarına dönüştürür ve tek ses dosyasında birleştirir."
        )
    )

    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=(
            "tts_chunk_*.txt dosyalarının bulunduğu klasör. "
            "Varsayılan: tts_text_chunks"
        ),
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=(
            "Üretilen WAV dosyalarının kaydedileceği klasör. "
            "Varsayılan: tts_audio"
        ),
    )

    parser.add_argument(
        "--final-file",
        type=Path,
        default=DEFAULT_FINAL_FILE,
        help=(
            "Birleştirilmiş WAV dosyasının yolu. "
            "Varsayılan: podcast_complete.wav"
        ),
    )

    parser.add_argument(
        "--voice",
        type=str,
        default=None,
        help=(
            "Gemini TTS ses adı. Belirtilmezse .env içindeki "
            "GEMINI_TTS_VOICE veya Kore kullanılır."
        ),
    )

    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help=(
            "Gemini TTS modeli. Belirtilmezse .env içindeki "
            "GEMINI_TTS_MODEL kullanılır."
        ),
    )

    parser.add_argument(
        "--silence-ms",
        type=int,
        default=DEFAULT_SILENCE_MS,
        help=(
            "Ses parçaları arasındaki sessizlik süresi. "
            "Varsayılan: 450 milisaniye"
        ),
    )

    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help="Her parça için azami API denemesi. Varsayılan: 6",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Daha önce üretilmiş WAV dosyalarını yeniden üretir.",
    )

    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Parçaları üretir fakat tek WAV dosyasında birleştirmez.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Sıralanmış dosyaların yalnızca ilk N tanesini "
            "kontrol eder; mevcut WAV'lar da bu sınıra dahildir."
        ),
    )

    parser.add_argument(
        "--job-dir",
        type=Path,
        default=None,
        help=(
            "Mevcut job_status.json durumunun bulunduğu iş klasörü. "
            "Verilirse TTS ilerlemesi bu dosyaya checkpoint edilir."
        ),
    )

    parser.add_argument(
        "--report-file",
        type=Path,
        default=DEFAULT_REPORT_FILE,
        help="TTS checkpoint raporunun JSON dosyası.",
    )

    parser.add_argument(
        "--target-minutes",
        type=float,
        default=None,
        help=(
            "Bu çalıştırmada üretilecek yaklaşık yeni ses süresi. "
            "Mevcut WAV'lar hedefe sayılmaz."
        ),
    )

    parser.add_argument(
        "--words-per-minute",
        type=float,
        default=150.0,
        help="Hedef süre hesabında tahmini okuma hızı. Varsayılan: 150",
    )

    return parser.parse_args()


def save_pcm_as_wav(
    output_file: Path,
    pcm_data: bytes,
) -> None:
    """Ham PCM ses verisini WAV dosyasına kaydeder."""

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_file = output_file.with_suffix(".wav.tmp")

    with wave.open(str(temporary_file), "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(SAMPLE_WIDTH)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm_data)

    temporary_file.replace(output_file)


def validate_wav(wav_file: Path) -> dict:
    """WAV dosyasının temel teknik özelliklerini doğrular."""

    if not wav_file.exists():
        raise FileNotFoundError(
            f"WAV dosyası bulunamadı: {wav_file}"
        )

    if wav_file.stat().st_size <= 44:
        raise ValueError(
            f"WAV dosyası boş veya geçersiz: {wav_file}"
        )

    with wave.open(str(wav_file), "rb") as audio:
        channels = audio.getnchannels()
        sample_width = audio.getsampwidth()
        sample_rate = audio.getframerate()
        frame_count = audio.getnframes()

    if channels != CHANNELS:
        raise ValueError(
            f"Beklenmeyen kanal sayısı: {channels}"
        )

    if sample_width != SAMPLE_WIDTH:
        raise ValueError(
            f"Beklenmeyen sample width: {sample_width}"
        )

    if sample_rate != SAMPLE_RATE:
        raise ValueError(
            f"Beklenmeyen örnekleme hızı: {sample_rate}"
        )

    duration_seconds = frame_count / sample_rate

    if duration_seconds <= 0:
        raise ValueError(
            f"Ses süresi geçersiz: {wav_file}"
        )

    return {
        "channels": channels,
        "sample_width": sample_width,
        "sample_rate": sample_rate,
        "frames": frame_count,
        "duration_seconds": round(duration_seconds, 3),
        "file_size_bytes": wav_file.stat().st_size,
    }


def get_tts_cache_settings(voice_name: str) -> dict:
    return {
        "voice": voice_name,
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "sample_width": SAMPLE_WIDTH,
    }


def validate_resumable_wav(
    *,
    source_text: str,
    output_file: Path,
    model: str,
    voice_name: str,
) -> tuple[dict, str]:
    audio_details = validate_wav(output_file)
    metadata_file = cache_metadata_path(output_file)

    if not metadata_file.exists():
        return audio_details, "legacy_technical_only"

    cache_valid, cache_reason = validate_cached_output(
        metadata_file=metadata_file,
        source_text=source_text,
        output_file=output_file,
        provider="gemini_tts",
        model=model,
        settings=get_tts_cache_settings(voice_name),
    )
    if not cache_valid:
        raise ValueError(
            "Mevcut WAV cache metadata ile uyuşmuyor; "
            f"dosya korunarak atlanmadı: {cache_reason}"
        )

    return audio_details, "metadata_validated"


def checkpoint_tts_state(
    *,
    state_store: JobStateStore,
    state: JobState,
    status: str,
    total_chunks: int,
    completed_chunks: int,
    paused_at: str | None = None,
    error_message: str | None = None,
    resume_after: str | None = None,
    final_file: Path | None = None,
) -> None:
    state.tts_chunks_total = total_chunks
    state.tts_chunks_completed = completed_chunks

    if status == "paused_quota":
        state_store.pause_for_quota(
            state,
            stage="tts_generating",
            provider="gemini_tts",
            paused_at=paused_at or "next_incomplete_tts_chunk",
            resume_after=resume_after,
            error_message=error_message,
        )
        return

    if final_file is not None:
        state.outputs["podcast"] = str(final_file)

    state_store.checkpoint(
        state,
        status=status,
        stage=status,
        paused_at=None,
        paused_provider=None,
        resume_after=None,
        last_error_type=None,
        last_error_message=None,
    )


def extract_audio_data(response) -> tuple[bytes, str | None]:
    """Gemini yanıtındaki ilk ses verisini bulur."""

    if not response.candidates:
        raise ValueError(
            "Gemini herhangi bir ses adayı döndürmedi."
        )

    candidate = response.candidates[0]

    if not candidate.content:
        raise ValueError(
            "Gemini adayında content bulunamadı."
        )

    for part in candidate.content.parts:
        if (
            part.inline_data is not None
            and part.inline_data.data
        ):
            return (
                part.inline_data.data,
                part.inline_data.mime_type,
            )

    raise ValueError(
        "Gemini yanıtında ses verisi bulunamadı."
    )


def should_retry(error: Exception) -> bool:
    """Hatanın geçici olup olmadığını yaklaşık olarak belirler."""

    message = str(error).lower()

    retryable_patterns = (
        "429",
        "rate limit",
        "resource exhausted",
        "temporarily unavailable",
        "503",
        "500",
        "502",
        "504",
        "timeout",
        "timed out",
        "connection",
        "server error",
        "internal error",
    )

    return any(
        pattern in message
        for pattern in retryable_patterns
    )


def generate_audio(
    client: genai.Client,
    source_text: str,
    output_file: Path,
    model: str,
    voice_name: str,
    max_retries: int,
) -> dict:
    """Bir metin parçası için Gemini TTS sesi üretir."""

    prompt = TTS_INSTRUCTION + "\n\n" + source_text

    for attempt in range(1, max_retries + 1):
        print(
            f"  API denemesi: {attempt}/{max_retries}"
        )

        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=(
                                types.PrebuiltVoiceConfig(
                                    voice_name=voice_name,
                                )
                            )
                        )
                    ),
                ),
            )

            audio_data, mime_type = extract_audio_data(
                response
            )

            save_pcm_as_wav(
                output_file=output_file,
                pcm_data=audio_data,
            )

            audio_details = validate_wav(output_file)

            audio_details["mime_type"] = mime_type
            audio_details["attempts"] = attempt

            return audio_details

        except Exception as error:
            print(
                f"  Deneme başarısız: "
                f"{type(error).__name__}: {error}"
            )

            if is_daily_quota_error(error):
                raise GeminiTTSDailyQuotaError(error) from error

            if (
                classify_provider_error(error)
                == ProviderErrorType.MODEL_NOT_FOUND
            ):
                raise GeminiTTSModelError(
                    "GEMINI_TTS_MODEL_NOT_FOUND"
                ) from error

            if attempt >= max_retries:
                raise

            if not should_retry(error):
                raise

            base_wait = min(60, 2 ** attempt)
            random_wait = random.uniform(0.5, 2.0)
            wait_seconds = base_wait + random_wait

            print(
                f"  {wait_seconds:.1f} saniye sonra "
                "tekrar denenecek."
            )

            time.sleep(wait_seconds)

    raise RuntimeError(
        "Beklenmeyen TTS üretim hatası."
    )


def create_silence_frames(
    duration_ms: int,
) -> bytes:
    """Belirtilen süre için PCM sessizlik üretir."""

    frame_count = int(
        SAMPLE_RATE * duration_ms / 1000
    )

    bytes_per_frame = CHANNELS * SAMPLE_WIDTH

    return b"\x00" * (
        frame_count * bytes_per_frame
    )


def combine_wav_files(
    wav_files: list[Path],
    output_file: Path,
    silence_ms: int,
) -> dict:
    """WAV dosyalarını aralarında sessizlikle birleştirir."""

    if not wav_files:
        raise ValueError(
            "Birleştirilecek WAV dosyası bulunamadı."
        )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_file = output_file.with_suffix(".wav.tmp")
    silence_frames = create_silence_frames(silence_ms)

    total_frames = 0

    with wave.open(str(temporary_file), "wb") as output_wav:
        output_wav.setnchannels(CHANNELS)
        output_wav.setsampwidth(SAMPLE_WIDTH)
        output_wav.setframerate(SAMPLE_RATE)

        for index, wav_file in enumerate(wav_files):
            validate_wav(wav_file)

            with wave.open(str(wav_file), "rb") as input_wav:
                frames = input_wav.readframes(
                    input_wav.getnframes()
                )

            output_wav.writeframes(frames)

            total_frames += (
                len(frames)
                // (CHANNELS * SAMPLE_WIDTH)
            )

            if index < len(wav_files) - 1:
                output_wav.writeframes(silence_frames)

                total_frames += (
                    len(silence_frames)
                    // (CHANNELS * SAMPLE_WIDTH)
                )

    temporary_file.replace(output_file)

    result = validate_wav(output_file)
    result["input_files"] = len(wav_files)
    result["silence_ms_between_chunks"] = silence_ms
    result["calculated_total_frames"] = total_frames

    return result


def write_report(
    report: dict,
    report_file: Path = DEFAULT_REPORT_FILE,
) -> None:
    """Üretim raporunu JSON olarak kaydeder."""

    report_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = report_file.with_suffix(
        report_file.suffix + ".tmp"
    )
    temporary_file.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary_file.replace(report_file)


def main() -> None:
    args = parse_arguments()

    load_project_environment(PROJECT_DIR / ".env")

    api_key = os.getenv(
        "GEMINI_API_KEY",
        "",
    ).strip()

    model = (
        args.model
        or os.getenv(
            "GEMINI_TTS_MODEL",
            "gemini-2.5-flash-preview-tts",
        )
    )

    voice_name = (
        args.voice
        or os.getenv(
            "GEMINI_TTS_VOICE",
            "Kore",
        )
    )

    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    final_file = args.final_file.resolve()
    report_file = args.report_file.resolve()

    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY bulunamadı. "
            ".env dosyasını kontrol et."
        )

    if not input_dir.exists():
        raise FileNotFoundError(
            f"TTS metin klasörü bulunamadı: {input_dir}"
        )

    if args.silence_ms < 0:
        raise ValueError(
            "--silence-ms negatif olamaz."
        )

    if args.max_retries < 1:
        raise ValueError(
            "--max-retries en az 1 olmalıdır."
        )

    if args.target_minutes is not None and args.target_minutes <= 0:
        raise ValueError("--target-minutes sıfırdan büyük olmalıdır.")
    if args.words_per_minute <= 0:
        raise ValueError("--words-per-minute sıfırdan büyük olmalıdır.")

    all_text_files = sorted(
        input_dir.glob("tts_chunk_*.txt")
    )
    text_files = all_text_files

    if args.limit is not None:
        if args.limit < 1:
            raise ValueError(
                "--limit en az 1 olmalıdır."
            )

        text_files = text_files[:args.limit]

    if not all_text_files:
        raise FileNotFoundError(
            f"TTS metin parçası bulunamadı: {input_dir}"
        )

    state_store = None
    state = None
    if args.job_dir is not None:
        state_store = JobStateStore(args.job_dir.resolve())
        if not state_store.status_file.exists():
            raise FileNotFoundError(
                f"Job durum dosyası bulunamadı: {state_store.status_file}"
            )
        state = state_store.load()
        checkpoint_tts_state(
            state_store=state_store,
            state=state,
            status="tts_generating",
            total_chunks=len(all_text_files),
            completed_chunks=state.tts_chunks_completed,
        )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    report = {
        "started_at": datetime.now().isoformat(
            timespec="seconds"
        ),
        "finished_at": None,
        "model": model,
        "voice": voice_name,
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "sample_width": SAMPLE_WIDTH,
        "input_directory": str(input_dir),
        "output_directory": str(output_dir),
        "final_file": str(final_file),
        "silence_ms": args.silence_ms,
        "target_minutes": args.target_minutes,
        "words_per_minute": args.words_per_minute,
        "chunks": [],
        "final_audio": None,
        "partial_audio": None,
        "status": "running",
    }

    write_report(report, report_file)

    print(f"Girdi klasörü: {input_dir}")
    print(f"Çıktı klasörü: {output_dir}")
    print(f"Model: {model}")
    print(f"Ses: {voice_name}")
    print(f"Toplam metin parçası: {len(text_files)}")
    print(f"Parçalar arası sessizlik: {args.silence_ms} ms")
    print()

    client = genai.Client(api_key=api_key)

    successful_wav_files = []
    failure_detected = False
    quota_paused = False
    daily_target_reached = False
    generated_words = 0
    daily_word_target = (
        args.target_minutes * args.words_per_minute
        if args.target_minutes is not None
        else None
    )

    try:
        for index, text_file in enumerate(
            text_files,
            start=1,
        ):
            output_file = (
                output_dir
                / f"{text_file.stem}.wav"
            )

            chunk_report = {
                "index": index,
                "source_file": text_file.name,
                "output_file": output_file.name,
                "status": None,
                "characters": None,
                "words": None,
                "audio": None,
                "cache_validation": None,
                "error": None,
            }

            print(
                f"[{index}/{len(text_files)}] "
                f"{text_file.name}"
            )

            try:
                source_text = text_file.read_text(
                    encoding="utf-8-sig",
                ).strip()

                if not source_text:
                    raise ValueError(
                        "Metin parçası boş."
                    )

                chunk_report["characters"] = len(
                    source_text
                )

                chunk_report["words"] = len(
                    source_text.split()
                )

                if (
                    output_file.exists()
                    and not args.overwrite
                ):
                    (
                        audio_details,
                        cache_validation,
                    ) = validate_resumable_wav(
                        source_text=source_text,
                        output_file=output_file,
                        model=model,
                        voice_name=voice_name,
                    )

                    chunk_report["status"] = "skipped"
                    chunk_report["audio"] = audio_details
                    chunk_report["cache_validation"] = (
                        cache_validation
                    )

                    successful_wav_files.append(
                        output_file
                    )

                    print(
                        "  Zaten mevcut, tekrar üretilmedi."
                    )

                else:
                    if (
                        daily_word_target is not None
                        and generated_words >= daily_word_target
                    ):
                        daily_target_reached = True
                        break

                    print(
                        f"  Karakter: {len(source_text):,}"
                    )

                    audio_details = generate_audio(
                        client=client,
                        source_text=source_text,
                        output_file=output_file,
                        model=model,
                        voice_name=voice_name,
                        max_retries=args.max_retries,
                    )

                    chunk_report["status"] = "generated"
                    chunk_report["audio"] = audio_details
                    chunk_report["cache_validation"] = (
                        "metadata_created"
                    )

                    write_cache_metadata(
                        metadata_file=cache_metadata_path(
                            output_file
                        ),
                        source_text=source_text,
                        output_file=output_file,
                        provider="gemini_tts",
                        model=model,
                        settings=get_tts_cache_settings(
                            voice_name
                        ),
                        validation=audio_details,
                    )

                    successful_wav_files.append(
                        output_file
                    )
                    generated_words += chunk_report["words"]

                    print(
                        "  Oluşturuldu: "
                        f"{audio_details['duration_seconds']:.1f} saniye"
                    )
            except GeminiTTSDailyQuotaError as error:
                    chunk_report["status"] = "paused_quota"
                    chunk_report["error"] = str(error)

                    report["chunks"].append(
                        chunk_report
                    )

                    quota_paused = True

                    if state_store is not None and state is not None:
                        checkpoint_tts_state(
                            state_store=state_store,
                            state=state,
                            status="paused_quota",
                            total_chunks=len(all_text_files),
                            completed_chunks=len(successful_wav_files),
                            paused_at=text_file.stem,
                            error_message=str(error),
                            resume_after=(
                                (
                                    datetime.now(timezone.utc)
                                    + timedelta(
                                        seconds=error.retry_after_seconds
                                    )
                                ).isoformat()
                                if error.retry_after_seconds is not None
                                else None
                            ),
                        )

                    write_report(report, report_file)

                    print()
                    print(
                        "  Gemini TTS günlük kotası doldu."
                    )
                    print(
                        "  Tamamlanan WAV dosyaları korundu."
                    )
                    print(
                        "  İşlem bir sonraki chunk'tan "
                        "devam edecek."
                    )

                    failure_detected = True
                    break

            except GeminiTTSModelError as error:
                chunk_report["status"] = "failed_configuration"
                chunk_report["error"] = str(error)
                report["chunks"].append(chunk_report)
                failure_detected = True
                write_report(report, report_file)
                print(
                    "  Gemini TTS modeli bulunamadı veya "
                    "generateContent desteklemiyor."
                )
                break

            except Exception as error:
                failure_detected = True

                chunk_report["status"] = "failed"
                chunk_report["error"] = (
                    f"{type(error).__name__}: {error}"
                )

                print(
                    f"  BAŞARISIZ: {chunk_report['error']}"
                )

            report["chunks"].append(chunk_report)
            write_report(report, report_file)

            if state_store is not None and state is not None:
                checkpoint_tts_state(
                    state_store=state_store,
                    state=state,
                    status="tts_generating",
                    total_chunks=len(all_text_files),
                    completed_chunks=len(successful_wav_files),
                )

            print()

    finally:
        client.close()

    all_successful = (
        len(successful_wav_files)
        == len(all_text_files)
    )

    if (
        (quota_paused or daily_target_reached)
        and successful_wav_files
    ):
        partial_file = final_file.with_name(
            final_file.stem + "_partial" + final_file.suffix
        )
        partial_details = combine_wav_files(
            wav_files=successful_wav_files,
            output_file=partial_file,
            silence_ms=args.silence_ms,
        )
        report["partial_audio"] = partial_details
        report["partial_audio"]["file"] = str(partial_file)
        if state_store is not None and state is not None:
            state.outputs["partial_podcast"] = str(partial_file)
            state_store.save(state)
        print(f"Kısmi podcast: {partial_file}")

    if all_successful and not args.no_merge:
        print("Ses parçaları birleştiriliyor...")

        final_details = combine_wav_files(
            wav_files=successful_wav_files,
            output_file=final_file,
            silence_ms=args.silence_ms,
        )

        report["final_audio"] = final_details

        print(
            f"Final dosya: {final_file}"
        )

        print(
            "Toplam ses süresi: "
            f"{final_details['duration_seconds'] / 60:.2f} dakika"
        )

    elif not all_successful and (failure_detected or quota_paused):
        print(
            "Bazı parçalar başarısız olduğu için final WAV "
            "oluşturulmadı."
        )

        print(
            "Scripti tekrar çalıştırdığında başarılı parçalar "
            "atlanacak ve eksik parçalar yeniden denenecek."
        )

    elif not all_successful:
        print(
            "Sınırlı veya günlük hedefli çalışma tamamlandı; "
            "tüm parçalar hazır olmadığı için final WAV oluşturulmadı."
        )

    report["finished_at"] = datetime.now().isoformat(
        timespec="seconds"
    )

    if quota_paused:
        report["status"] = "paused_quota"
    elif any(
        chunk["status"] == "failed_configuration"
        for chunk in report["chunks"]
    ):
        report["status"] = "failed_configuration"
    elif daily_target_reached:
        report["status"] = "daily_target_reached"
    elif all_successful:
        report["status"] = "completed"
    elif failure_detected:
        report["status"] = "partially_failed"
    else:
        report["status"] = "incomplete"

    write_report(report, report_file)

    if (
        state_store is not None
        and state is not None
        and not quota_paused
    ):
        configuration_failed = report["status"] == "failed_configuration"
        checkpoint_tts_state(
            state_store=state_store,
            state=state,
            status=(
                "failed_configuration"
                if configuration_failed
                else (
                    "completed"
                    if all_successful
                    else "tts_generating"
                )
            ),
            total_chunks=len(all_text_files),
            completed_chunks=len(successful_wav_files),
            final_file=(
                final_file
                if all_successful and not args.no_merge
                else None
            ),
        )

    generated_count = sum(
        chunk["status"] == "generated"
        for chunk in report["chunks"]
    )

    skipped_count = sum(
        chunk["status"] == "skipped"
        for chunk in report["chunks"]
    )

    failed_count = sum(
        chunk["status"] in {"failed", "failed_configuration"}
        for chunk in report["chunks"]
    )

    paused_count = sum(
        chunk["status"] == "paused_quota"
        for chunk in report["chunks"]
    )

    print()
    print("Toplu TTS işlemi tamamlandı.")
    print(f"Yeni oluşturulan: {generated_count}")
    print(f"Atlanan: {skipped_count}")
    print(f"Başarısız: {failed_count}")
    print(f"Kota nedeniyle duraklatılan: {paused_count}")
    print(f"Rapor: {report_file}")

    if failed_count:
        raise RuntimeError(
            f"{failed_count} TTS parçası üretilemedi."
        )

    if quota_paused:
        print(
            "İşlem kota nedeniyle güvenli biçimde "
            "duraklatıldı. Aynı komutla devam edebilirsin."
        )


if __name__ == "__main__":
    main()