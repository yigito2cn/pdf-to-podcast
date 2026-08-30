# `podcast_from_pdf.py` Kullanım Yardımı

Bu program seçilen PDF sayfalarını metne dönüştürür, metni temizler ve mümkünse podcast sesi üretir.

Ana işlem sırası:

```text
PDF -> metin çıkarma -> mekanik ön temizlik
-> Gemini metin temizliği -> gerekirse Groq fallback
-> clean_text.txt -> Read Aloud PDF
-> Gemini TTS -> gerekirse Edge TTS fallback
-> final veya kısmi podcast
```

Tamamlanan metin ve ses parçaları kota veya bağlantı hatasında korunur. Aynı job daha sonra kaldığı yerden sürdürülebilir.

## 1. Terminali Hazırlama

PowerShell açın ve proje klasörüne geçin:

```powershell
cd C:\Workdir\Develop\pdf_to_podcast
```

Sanal ortamı etkinleştirin:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

Terminal satırının başında `(.venv)` görünmelidir.

Sanal ortam henüz kurulmadıysa:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r .\requirements.txt
```

Edge TTS seslerini birleştirmek için FFmpeg kontrolü:

```powershell
ffmpeg -version
```

## 2. API Anahtarları

Proje kökündeki `.env` dosyasında en az şu alanlar bulunmalıdır:

```ini
GEMINI_API_KEY=gemini_anahtariniz
GROQ_API_KEY=groq_anahtariniz
```

İsteğe bağlı model ve ses ayarları:

```ini
GEMINI_MODEL=gemini-3.7-flash
GEMINI_TTS_MODEL=gemini-2.5-flash-preview-tts
GEMINI_TTS_VOICE=Kore
GROQ_TEXT_MODEL=qwen/qwen3.6-27b
```

`.env` dosyasını paylaşmayın veya Git deposuna eklemeyin.

Anahtarları `.env` yerine yalnız açık terminal oturumunda tanımlamak için:

```powershell
$geminiSecure = Read-Host "Gemini API anahtarı" -AsSecureString
$groqSecure = Read-Host "Groq API anahtarı" -AsSecureString

$env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new("", $geminiSecure).Password
$env:GROQ_API_KEY = [System.Net.NetworkCredential]::new("", $groqSecure).Password
```

Anahtarları doğrudan `--gemini-api-key` veya `--groq-api-key` ile vermek de mümkündür; ancak bu yöntem anahtarları PowerShell komut geçmişinde bırakabilir.

## 3. PDF Dosyasını Hazırlama

PDF dosyasını `input` klasörüne koyun:

```text
input\kitap.pdf
```

Mevcut PDF dosyalarını listelemek için:

```powershell
Get-ChildItem .\input -Filter *.pdf
```

Programdaki sayfa numaraları fiziksel PDF sayfalarıdır ve 1'den başlar. Mevcut `input\kitap.pdf` dosyası 337 fiziksel sayfadır.

## 4. Önerilen İlk Çalıştırma

Mevcut `kitap.pdf` dosyasının 8-334 aralığını tüm pipeline ile işlemek için:

```powershell
python .\podcast_from_pdf.py `
    --pdf ".\input\kitap.pdf" `
    --start-page 8 `
    --end-page 334
```

Bu komut metin çıkarma, temizleme, Read Aloud PDF, TTS chunk üretimi ve ses üretimini çalıştırır.

## 5. Günlük 15-20 Dakika Modu

Bir çalıştırmada yaklaşık 20 dakikalık yeni ses üretmek için:

```powershell
python .\podcast_from_pdf.py `
    --pdf ".\input\kitap.pdf" `
    --start-page 8 `
    --end-page 334 `
    --target-minutes 20
```

Tahmini okuma hızını değiştirmek için:

```powershell
python .\podcast_from_pdf.py `
    --pdf ".\input\kitap.pdf" `
    --start-page 8 `
    --end-page 334 `
    --target-minutes 20 `
    --words-per-minute 145
```

Günlük hedef yaklaşık bir değerdir. Program mevcut WAV dosyalarını hedefe saymaz; yalnız o çalıştırmada yeni üretilen metin parçalarını sayar.

## 6. Aşamaları Ayrı Çalıştırma

### Yalnız PDF extraction ve ön temizlik

```powershell
python .\podcast_from_pdf.py `
    --pdf ".\input\kitap.pdf" `
    --start-page 8 `
    --end-page 334 `
    --stage extract
```

Bu aşama API kullanmaz. Ham ve ön temizlenmiş metni `work` altındaki job klasörüne yazar.

### Mevcut job üzerinde metin temizliğine devam

```powershell
python .\podcast_from_pdf.py `
    --stage text `
    --job-dir ".\work\JOB_KLASORU"
```

Gemini kullanılamazsa Groq denenir. İkisi de kullanılamazsa mevcut en iyi metin korunur ve Read Aloud PDF üretilir.

### Mevcut job üzerinde ses üretimine devam

```powershell
python .\podcast_from_pdf.py `
    --stage tts `
    --job-dir ".\work\JOB_KLASORU"
```

Günlük 20 dakika sınırıyla devam etmek için:

```powershell
python .\podcast_from_pdf.py `
    --stage tts `
    --job-dir ".\work\JOB_KLASORU" `
    --target-minutes 20
```

### Sınırlı TTS testi

İlk 3 sıralı TTS chunk'ını kontrol etmek için:

```powershell
python .\podcast_from_pdf.py `
    --stage tts `
    --job-dir ".\work\JOB_KLASORU" `
    --tts-limit 3
```

`--tts-limit 3`, ilk 3 eksik chunk anlamına gelmez. İlk 3 sıralı chunk kontrol edilir; mevcut ses dosyaları da bu sınıra dahildir.

## 7. Job Klasörünü Bulma

En son değiştirilen job klasörlerini görmek için:

```powershell
Get-ChildItem .\work -Directory |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 10 Name, LastWriteTime
```

En son job klasörünü değişkene almak için:

```powershell
$job = Get-ChildItem .\work -Directory |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

$job.FullName
```

Son job üzerinde ses üretimine devam etmek için:

```powershell
python .\podcast_from_pdf.py `
    --stage tts `
    --job-dir $job.FullName `
    --target-minutes 20
```

## 8. Job Durumunu Kontrol Etme

Durum dosyasını okumak için:

```powershell
Get-Content "$($job.FullName)\job_status.json" -Raw
```

Önemli durumlar:

| Durum | Anlamı |
|---|---|
| `created` | Job oluşturuldu |
| `precleaned` | PDF metni çıkarıldı ve ön temizlendi |
| `text_cleaning` | AI metin temizliği sürüyor |
| `paused_quota` | Sağlayıcı kotası doldu; mevcut sonuçlar korundu |
| `text_completed` | Temiz metin hazır |
| `tts_chunked` | TTS metin parçaları hazır |
| `tts_generating` | Ses üretimi sürüyor veya eksik parçalar var |
| `completed_text_only` | Ses üretilemedi; TXT ve PDF hazır |
| `completed` | Final ses çıktısı hazır |

`paused_at` kaldığı chunk'ı, `resume_after` ise sağlayıcının bildirdiği tahmini devam zamanını gösterir.

## 9. Çıktılar

Kullanıcı çıktıları:

```text
output\<job_id>\
    clean_text.txt
    readaloud.pdf
    podcast.wav
    podcast_partial.wav
    podcast_edge.mp3
```

Her dosya her job'da bulunmayabilir:

- `clean_text.txt`: temizlenmiş veya güvenli fallback metni
- `readaloud.pdf`: Microsoft Edge Read Aloud ile okunabilir metin tabanlı PDF
- `podcast.wav`: tamamlanan Gemini TTS sesi
- `podcast_partial.wav`: kota veya günlük hedef anında mevcut WAV parçalarının birleşimi
- `podcast_edge.mp3`: Gemini TTS kullanılamadığında Edge TTS fallback çıktısı

Ara dosyalar ve resume verileri:

```text
work\<job_id>\
    job_status.json
    raw_text.txt
    precleaned_text.txt
    gemini_text_chunks\
    gemini_cleaned_chunks\
    groq_cleaned_subchunks\
    tts_chunks\
    tts_audio\
    edge_audio\
    tts_manifest.json
    tts_generation_report.json
```

Bu dosyaları ve klasörleri manuel olarak silmeyin. Resume mekanizması bunları kullanır.

## 10. Kota Dolduğunda

Program kota durumunda tamamlanan chunk'ları ve ses dosyalarını korur. `job_status.json` içinde durum `paused_quota` olur.

Kota yenilendikten sonra aynı ilk komutu yeniden çalıştırabilirsiniz:

```powershell
python .\podcast_from_pdf.py `
    --pdf ".\input\kitap.pdf" `
    --start-page 8 `
    --end-page 334 `
    --target-minutes 20
```

Aynı PDF hash'i ve sayfa aralığı aynı job'ı bulur. Geçerli tamamlanmış chunk'lar yeniden API'ye gönderilmez.

Alternatif olarak yalnız duraklayan aşamayı `--stage text` veya `--stage tts` ile sürdürebilirsiniz.

## 11. `--force` Kullanımı

Extraction ve metin temizliği ara dosyalarını yeniden değerlendirmek için:

```powershell
python .\podcast_from_pdf.py `
    --pdf ".\input\kitap.pdf" `
    --start-page 8 `
    --end-page 334 `
    --force
```

`--force` normal resume için gerekli değildir. Yalnız giriş veya temizleme davranışını bilinçli olarak yeniden çalıştırmak istediğinizde kullanın.

## 12. Edge TTS Sesini Değiştirme

Varsayılan Türkçe Edge sesi `tr-TR-EmelNeural` değeridir. Başka bir ses kullanmak için:

```powershell
python .\podcast_from_pdf.py `
    --stage tts `
    --job-dir ".\work\JOB_KLASORU" `
    --edge-voice "tr-TR-AhmetNeural"
```

Edge TTS internet bağlantısı gerektirir fakat Gemini API kotasını kullanmaz.

## 13. Yardım ve Test Komutları

CLI seçeneklerini görmek için:

```powershell
python .\podcast_from_pdf.py --help
```

Offline regresyon testlerini çalıştırmak için:

```powershell
python -m unittest discover -s tests -v
```

Gerçek proje envanterini yenilemek için:

```powershell
python .\scripts\project_inventory.py
```

## 14. Sık Karşılaşılan Sorunlar

### `GEMINI_API_KEY bulunamadı`

`.env` dosyasında `GEMINI_API_KEY` alanını veya mevcut terminalde `$env:GEMINI_API_KEY` değerini kontrol edin.

```powershell
if ($env:GEMINI_API_KEY) { "Gemini key present" } else { "Gemini key missing" }
```

Anahtarın kendisini terminale yazdırmayın.

### `GROQ_API_KEY ortam değişkeni bulunamadı`

Groq anahtarını `.env` içine ekleyin veya mevcut terminalde güvenli giriş yöntemiyle tanımlayın. Groq kullanılamazsa program mevcut preclean metni korur.

### `Job durum dosyası bulunamadı`

`--stage tts` için verdiğiniz klasörde `job_status.json` bulunmalıdır. Doğru job klasörünü bölüm 7'deki komutla bulun.

### `Mevcut TTS chunk güncel kaynakla uyuşmuyor`

Program mevcut chunk'ı silmez. Bu hata aynı job klasöründe kaynak metin veya chunk ayarlarının değiştiğini gösterir. Eski dosyaları manuel silmek yerine yeni sayfa aralığı/job kullanın veya önce yedek alın.

### `paused_quota`

Bu bir veri kaybı veya kalıcı başarısızlık değildir. `resume_after` zamanından sonra aynı komutu yeniden çalıştırın.

### Ses üretilemedi

Önce aşağıdaki çıktıları kontrol edin:

```powershell
Get-ChildItem .\output -Recurse -File |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 20 FullName, Length, LastWriteTime
```

Ses olmasa bile `clean_text.txt` ve `readaloud.pdf` üretilmiş olmalıdır.
