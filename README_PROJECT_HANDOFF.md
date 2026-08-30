# PDF to Podcast

## Proje devir, durum ve otomasyon rehberi

**Son güncelleme:** 30 Ağustos 2026
**Proje klasörü:** `C:\Workdir\Develop\pdf_to_podcast`
**Python ortamı:** `.venv`
**Ana hedef:** Kullanıcının bir PDF yükleyip sayfa aralığı seçmesiyle, mümkün olan en iyi temiz metni ve mümkünse 15-20 dakikalık podcast sesini otomatik üretmek.

---

## 0. Uygulanan genel pipeline durumu

30 Ağustos 2026 P0 uygulamasıyla ana giriş noktası tek komutta birleştirildi:

Ayrıntılı kurulum, çalıştırma, resume ve sorun giderme rehberi: [`HELP_PODCAST_FROM_PDF.md`](HELP_PODCAST_FROM_PDF.md)

```powershell
.\.venv\Scripts\python.exe .\podcast_from_pdf.py `
  --pdf .\input\kitap.pdf `
  --start-page 8 `
  --end-page 334 `
  --gemini-api-key "GEMINI_KEY" `
  --groq-api-key "GROQ_KEY" `
  --target-minutes 20
```

API anahtarları job state, cache, rapor veya çıktı dosyalarına yazılmaz. Komut satırı geçmişinde anahtar bırakmamak için anahtarlar `.env` veya process environment üzerinden de verilebilir.

Tek komut şu akışı yürütür:

```text
PDF extraction -> güvenli preclean -> Gemini -> Groq fallback
-> clean_text.txt -> readaloud.pdf -> TTS chunk
-> Gemini WAV -> Edge TTS MP3 fallback -> final veya kısmi podcast
```

Yeni job kimliği PDF SHA-256, sayfa aralığı ve pipeline sürümünden deterministik üretilir. Mevcut eski isimli job klasörü bulunursa taşınmadan kullanılmaya devam edilir.

Resume komutları:

```powershell
# Yalnız metin ve Read Aloud PDF
.\.venv\Scripts\python.exe .\podcast_from_pdf.py `
  --stage text `
  --job-dir .\work\JOB_ID

# Mevcut job üzerinde yalnız ses üretimine devam
.\.venv\Scripts\python.exe .\podcast_from_pdf.py `
  --stage tts `
  --job-dir .\work\JOB_ID `
  --target-minutes 20
```

Kalıcı state dosyası `work\<job_id>\job_status.json` dosyasıdır. Groq alt chunk, Gemini chunk, Gemini TTS WAV ve Edge TTS MP3 cache'leri kaynak/model/ayar hash'iyle doğrulanır. Metadata'sız legacy çıktılar otomatik silinmez.

Gerçek dosya ağacı ve README karşılaştırması `project_inventory.json` içindedir. Rapor şu komutla yenilenir:

```powershell
.\.venv\Scripts\python.exe .\scripts\project_inventory.py
```

Offline regresyon paketi:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

P0 öncesi yedek proje dışındadır:

```text
C:\Workdir\Develop\pdf_to_podcast_backups\pdf_to_podcast_pre_p0_20260830_115546.zip
SHA-256: EDA1B0F5943B9CB98AAA8A4D8A90C425D732A4D883DF63F8A3C0DAA25F85E525
```

---

## 1. Bu README'nin amacı

Bu dosya, projeyi VS Code Copilot Agent'a devretmek için hazırlanmıştır. Agent projede çok sayıda deneysel, ara ve üretim dosyası bulunduğunu bilmelidir. Önce mevcut kodu ve dosya ağacını incelemeli, çalışan parçaları korumalı ve değişiklikleri küçük testlerle doğrulamalıdır.

Proje başlangıçta yalnızca Gemini ile PDF metni temizleme deneyi olarak başladı. Daha sonra Gemini TTS, resume mekanizması, Groq metin fallback'i, kalite kontrolleri, chunk sistemi, kısmi sonuç koruma ve offline OCR seçenekleri eklendi.

Nihai hedef tek bir kitaba özel script değil, farklı PDF'lerde kullanılabilen genel bir uygulamadır.

---

## 2. Nihai kullanıcı deneyimi

Kullanıcıdan yalnızca şunlar istenmelidir:

1. Gemini API anahtarı
2. Groq API anahtarı
3. PDF dosyası
4. Başlangıç sayfası
5. Bitiş sayfası
6. İstenirse ses, hedef süre ve çıktı tercihleri

Kullanıcı terminal komutlarıyla uğraşmamalıdır. Uygulama bütün sağlayıcı ve fallback kararlarını otomatik vermelidir.

Beklenen akış:

```text
PDF yükle
  ↓
Sayfa aralığını seç
  ↓
Sayfa bazlı metin çıkar
  ↓
Güvenli mekanik ön temizleme
  ↓
Gemini ile bağlamsal OCR düzeltme
  ↓ Gemini kotası yoksa veya dolarsa
Groq ile eksik parçaları tamamlama
  ↓ Groq da kullanılamıyorsa
Offline OCR / güvenli preclean sonucu
  ↓
Doğrulama ve kalite raporu
  ↓
Gemini TTS ile ses üretimi
  ↓ kota veya kimlik doğrulama sorunu
Ücretsiz veya yerel TTS fallback
  ↓
Mevcut ses parçalarını koru
  ↓
Yeterli parça varsa 15-20 dakikalık podcast üret
  ↓
Tamamlanamadıysa işi duraklat ve ertesi gün aynı yerden devam et
```

Her zaman en az bir yararlı çıktı bırakılmalıdır:

1. Final podcast WAV veya MP3
2. Kısmi podcast ve tamamlanan ses parçaları
3. TTS'ye hazır temiz TXT
4. Edge Read Aloud ile okunabilecek temiz, metin tabanlı PDF
5. En kötü durumda güvenli mekanik ön temizleme sonucu ve inceleme raporu

---

## 3. Projenin gelişim geçmişi

### 3.1 İlk aşama: PDF'den metin çıkarma

İlk pipeline PyMuPDF kullanarak seçilen PDF sayfalarından metin çıkardı.

Test edilen kitap:

```text
8735-Statu_Endishesi-Alain_De_Botton-Ahu_Sila_Bayer-2003-337s.pdf
```

Test edilen geniş aralık:

```text
PDF sayfaları: 8-334
```

Örnek job klasörü:

```text
work\8735-Statu_Endishesi-Alain_De_Botton-Ahu_Sila_Bayer-2003-337s_pages_8_334
```

PDF'nin mevcut text layer'ında şu sorunlar görüldü:

- Soft hyphen karakterleri
- Satır sonunda bölünmüş kelimeler
- Yanlış birleşmiş kelimeler
- Yanlış ayrılmış kelimeler
- Koşan üstbilgi ve altbilgiler
- Sayfa numaralarının metne karışması
- Harf aralıklı bölüm başlıkları
- Şema, reklam ve görsel sayfalarının düz metin gibi çıkarılması
- Bozuk karakter veya rakam tanıma

Örnekler:

```text
l 776                  -> 1776
top­ lumsal            -> toplumsal
roma nındaki           -> romanındaki
gir di                  -> girdi
yeme ği                 -> yemeği
çeşitlilikgöstermiştir  -> çeşitlilik göstermiştir
top1 umun               -> toplumun
```

Bazı düzeltmeler kesin ve mekaniktir. Bazıları ise bağlam veya model gerektirir.

### 3.2 Denetim aşaması

`audit_book_text.py` ile kitap metni denetlendi. `book_audit.txt` raporunda 8-50 aralığındaki 43 sayfada 964 şüpheli bulgu görüldü.

Ana bulgu türleri:

```text
multiple_spaces                    368
soft_hyphen                        269
possible_split_word_without_hyphen 209
missing_space_after_punctuation     71
isolated_letter_between_words       25
hyphenated_line_break               10
letter_digit_mix                     8
digit_letter_mix                     3
letter_space_three_digits            1
```

Önemli sonuçlar:

- `soft_hyphen` güvenle düzeltilebilir.
- Normal satır sonlarını kelime birleşmesi sanan kural çok fazla yanlış pozitif üretti.
- `isolated_letter_between_words` kuralı `bir o kadar` gibi doğru ifadeleri hata olarak işaretledi.
- Noktalama sonrası boşluk kuralı `M.Ö.`, `J.H.`, `I.M.`, `J.B.` ve `s.nob` gibi kısaltmaları yanlış işaretledi.
- Bazı görsel veya reklam sayfalarının text layer'ı tamamen bozuktu.
- Şema ve tablo sayfaları normal paragraf algoritmasıyla işlenmemelidir.

Bu nedenle kurallar üç sınıfa ayrılmalıdır:

```text
safe_fixes
candidate_fixes
manual_review
```

### 3.3 Gemini ile metin temizleme

İlk bağlamsal düzeltme sağlayıcısı Gemini idi.

Kullanılan veya test edilen ayarlar:

```ini
GEMINI_MODEL=gemini-3.6-flash
GEMINI_TTS_MODEL=gemini-2.5-flash-preview-tts
GEMINI_TTS_VOICE=Kore
```

Gemini metni chunk'lara bölerek temizledi. İş yeniden başlatıldığında tamamlanan chunk'lar atlandı.

Bir test işinde toplam 91 Gemini metin chunk'ı oluştu. İlk 20 chunk başarıyla temizlendi. 21. chunk'ta Gemini rate limit veya kota hatası verdi.

Önemli davranış:

```text
chunk 1-20: korunuyor
chunk 21: kota nedeniyle duruyor
aynı komut tekrar çalıştırılınca 1-20 atlanıyor
```

Gemini için görülen hata türleri:

- `429 RateLimitError`
- Günlük kota yetersizliği
- Eski veya geçersiz ortam anahtarı nedeniyle `401 UNAUTHENTICATED`
- API key geçersiz olduğunda `400 API_KEY_INVALID`

Gemini kota hatasında uzun retry yerine fallback sağlayıcısına geçilmesi hedeflenmiştir.

### 3.4 Yerel Ollama deneyi

Yerel modeller test edildi:

```text
qwen3:4b
gemma3:4b
```

Qwen uzun düşünme çıktısı üretti. Gemma daha hızlıydı ancak kaynak cümleyi yeniden yazdı.

Örnek model dönüşümleri:

```text
bir tutulur olmuştur -> örtüşmüştür
bu yana              -> beri
```

Bu değişiklikler OCR temizliği için fazla yorumlayıcı bulundu. Bu nedenle Ollama pipeline'dan çıkarıldı.

### 3.5 Groq fallback

Gemini kotası dolduğunda eksik metin chunk'larını Groq ile tamamlamak için fallback eklendi.

Kurulu paket:

```text
groq 1.7.0
```

Çalışan Groq modeli:

```ini
GROQ_TEXT_MODEL=qwen/qwen3.6-27b
GROQ_MAX_ATTEMPTS=4
GROQ_REQUEST_DELAY=4.0
GROQ_MAX_CHARACTERS=2200
```

Qwen'in düşünme çıktısı şu parametreyle kapatıldı:

```python
reasoning_effort="none"
```

Başarılı Groq test çıktısı:

```text
Orijinal:
Batı'nın statü anlayışı l 776'dan bu yana giderek artan bir
oranda maddi başarıyla bir tutulur olmuştur.

Bu roma nındaki anlatıcı restorana gir di. Akşam yeme ği
için sözleşmiştir.

Groq:
Batı'nın statü anlayışı 1776'dan bu yana giderek artan bir
oranda maddi başarıyla bir tutulur olmuştur.

Bu romanındaki anlatıcı restorana girdi. Akşam yemeği
için sözleşmiştir.
```

Test metrikleri:

```text
Uzunluk oranı: 0.9779
Benzerlik:     0.9832
```

Groq'nun 8.000 TPM sınırına takılmamak için:

- Ana Gemini chunk düzeni korundu.
- Büyük ana chunk'lar 2.200 karakterlik Groq alt chunk'larına bölündü.
- `max_completion_tokens` dinamik hesaplandı.
- Başarılı ana chunk'lar diske kaydedildi.
- Mevcut Gemini ve Groq çıktıları doğrulanarak tekrar kullanıldı.

Kalite eşikleri:

```python
MIN_LENGTH_RATIO = 0.90
MAX_LENGTH_RATIO = 1.10
MIN_SIMILARITY_RATIO = 0.90
```

Kalite kontrolünden geçmeyen model sonucu kabul edilmez. Güvenli davranış olarak kaynak alt chunk aynen korunur ve raporlanır.

Örnek:

```text
Chunk 58 alt parça 2:
Benzerlik: 0.6628
Sonuç: Groq çıktısı reddedildi, kaynak metin korundu.

Chunk 61 alt parça 1:
Benzerlik: 0.7367
Sonuç: Groq çıktısı reddedildi, kaynak metin korundu.

Chunk 66 alt parça 2 ve 3:
Uzunluk veya benzerlik düşük
Sonuç: kaynak metin korundu.
```

Groq üretimi 70. ana chunk civarında günlük token sınırına ulaştı:

```text
TPD limit: 200000
Kullanılan: 199475
Yeni istek: 1611
Önerilen bekleme: yaklaşık 7 dakika 49 saniye
```

Mevcut durumda iş yeniden başlatıldığında doğrulanmış Gemini ve Groq sonuçları atlanarak eksik yerden devam edebilir.

### 3.6 Offline OCR alternatifi

Bulut metin servisleri kullanılamadığında aşağıdaki alternatif değerlendirildi:

```text
OCRmyPDF + Tesseract tur+eng
```

Amaç:

- PDF'nin bozuk text layer'ını kullanmak yerine sayfaları yeniden OCR etmek
- Aranabilir PDF üretmek
- Sidecar TXT almak
- Edge Read Aloud için temiz PDF oluşturmak

Önerilen offline akış:

```text
PDF sayfa aralığı
  ↓
OCRmyPDF --force-ocr
  ↓
Tesseract tur+eng
  ↓
Searchable PDF + sidecar TXT
  ↓
Güvenli mekanik temizlik
```

Windows'ta bunun WSL Ubuntu üzerinden çalıştırılması düşünülmüştür.

### 3.7 Gemini TTS

Metin temizliğinden sonra Gemini TTS ile WAV üretimi eklendi.

Kullanılan ayarlar:

```ini
GEMINI_TTS_MODEL=gemini-2.5-flash-preview-tts
GEMINI_TTS_VOICE=Kore
```

Teknik ses ayarları:

```text
Sample rate: 24000 Hz
Channels: 1
Sample width: 2 byte
Parçalar arası sessizlik: 450 ms
Format: WAV
```

TTS chunk sistemi metni koruyarak oluşturuldu:

```text
TTS chunk sayısı: 307
Kaynak normalize karakter sayısı: 284169
Chunk normalize karakter sayısı: 284169
Metin tam korunuyor: True
```

Klasörler:

```text
tts_continuation_chunks
tts_continuation_audio
```

Dosya aralığı:

```text
tts_chunk_0001.txt
tts_chunk_0307.txt
```

İlk ses üretimi başarıyla test edildi:

```text
Model: gemini-2.5-flash-preview-tts
Ses: Kore
Karakter: 887
Oluşturulan ses süresi: 57.8 saniye
```

İlk 10 WAV dosyasının resume kontrolü de başarıyla geçti:

```text
Yeni oluşturulan: 0
Atlanan: 10
Başarısız: 0
```

TTS scripti mevcut geçerli WAV dosyalarını atlar ve yalnızca eksikleri üretir.

### 3.8 TTS kimlik doğrulama sorunu ve çözümü

Bir aşamada batch script şu hatayı verdi:

```text
401 UNAUTHENTICATED
ACCESS_TOKEN_TYPE_UNSUPPORTED
```

Doğrudan TTS testi başarılıydı. Sorun PowerShell ortamında kalan eski `GEMINI_API_KEY` değeriydi. `.env` dosyasındaki geçerli anahtarın üzerine yazıyordu.

Çözüm:

```python
load_dotenv(
    PROJECT_DIR / ".env",
    override=True,
)

api_key = os.getenv(
    "GEMINI_API_KEY",
    "",
).strip()

client = genai.Client(
    api_key=api_key,
)
```

PowerShell oturumunda eski anahtar da kaldırıldı:

```powershell
Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
```

Bu düzeltmeden sonra batch TTS başarılı oldu.

---

## 4. Bilinen ana dosyalar

Agent öncelikle gerçek dosya ağacını listelemeli ve aşağıdaki dosyaların mevcut sürümlerini incelemelidir.

### Ana giriş noktaları

```text
podcast_from_pdf.py
batch_gemini_tts.py
```

### Metin işleme modülleri

```text
pipeline\gemini_text_cleaner.py
pipeline\groq_text_cleaner.py
pipeline\text_provider.py
pipeline\provider_errors.py
pipeline\text_precleaner.py
pipeline\text_precleaner_v2.py             # varsa
pipeline\ocr_fallback.py                    # varsa
```

### Denetim ve test dosyaları

```text
audit_book_text.py
test_groq_cleaner.py
test_gemini_tts.py                          # varsa
txt_to_readaloud_pdf.py                     # varsa
```

### Üretim ve ara klasörler

```text
input\
work\
tts_continuation_chunks\
tts_continuation_audio\
book_audit\
```

### Önemli çıktı dosyaları

```text
podcast_continuation_text.txt
podcast_continuation_complete.wav
tts_generation_report.json
book_audit.txt
book_audit.json
```

### Job içindeki muhtemel dosya ve klasörler

```text
precleaned_text.txt
gemini_text_chunks\
gemini_cleaned_chunks\
groq_text_chunks\
groq_cleaned_chunks\
groq_cleaned_subchunks\
groq_chunk_metadata\
groq_review_required\
groq_text_cleaning_report.json
hybrid_cleaned_text.txt
ai_cleaned_text.txt
```

Agent hiçbir klasör adını varsayarak silmemelidir. Önce mevcut ağacı ve dosya tarihlerini incelemelidir.

---

## 5. Mevcut kritik kod davranışları

### 5.1 Resume zorunludur

Her aşama kaldığı yerden devam edebilmelidir:

- Mevcut metin chunk'ı yeniden oluşturulmamalı.
- Mevcut temizlenmiş chunk kaynakla doğrulanmadan kullanılmamalı.
- Mevcut WAV teknik olarak doğrulanmadan atlanmamalı.
- Başarılı dosyalar hata veya kota durumunda korunmalı.
- Tek bir başarısız chunk tüm işi kullanılmaz hale getirmemeli.

### 5.2 Kaynak doğrulaması

Yalnızca aynı ada sahip olması yeterli değildir. Eski chunk düzenleri değiştiği için bir ara aşamada 21 ve 91 chunk düzenleri görüldü. Bu nedenle eski temiz çıktı yanlış kaynak chunk'a denk gelebilir.

İdeal doğrulama:

```text
source_hash
provider
model
created_at
validation metrics
```

Her temizlenmiş chunk yanında metadata tutulmalıdır. Kaynak hash değişmişse çıktı yeniden işlenmelidir.

### 5.3 Belirsiz düzeltmede kaynak korunmalıdır

Model çıktısı şu kontrollerden geçmezse model sonucu kullanılmamalıdır:

- Uzunluk oranı 0.90-1.10 dışında
- Benzerlik 0.90 altında
- `<think>` içeriği var
- Açıklama veya ön ek eklenmiş
- Çıktı boş veya yarım
- Metin belirgin biçimde yeniden yazılmış

Bu durumda:

```text
Kaynak alt parça aynen korunur
Şüpheli model çıktısı review dosyasına yazılır
İşlem sonraki parçayla devam eder
```

### 5.4 Kota hataları ile kod hataları ayrılmalıdır

Fallback yalnızca uygun hata türlerinde çalışmalıdır.

```text
429 veya günlük kota     -> sonraki sağlayıcı veya paused_quota
401 geçersiz credential  -> yapılandırma kontrolü, sonra uygun fallback
404 model bulunamadı     -> model seçimi hatası
413 istek fazla büyük    -> chunk küçültme
500-504                  -> kontrollü retry
Syntax/dosya hatası      -> dur ve açık hata ver
```

### 5.5 Günlük kota işlem durumu

Kota dolduğunda uygulama bunu başarısız iş olarak değil, duraklatılmış iş olarak kaydetmelidir:

```json
{
  "status": "paused_quota",
  "provider": "groq",
  "paused_at_chunk": 70,
  "completed_chunks": 69,
  "resume_after": null
}
```

API'nin önerdiği bekleme süresi varsa saklanmalıdır. `7m49.152s` gibi süreler doğru parse edilmelidir.

---

## 6. `batch_gemini_tts.py` mevcut durumu

Scriptin çalışan özellikleri:

- `.env` dosyasını `override=True` ile yükleme
- Gemini API key ile açık client oluşturma
- TXT chunk'larını sıralı okuma
- WAV üretme
- WAV teknik doğrulama
- Geçici dosyadan atomik kayıt
- Mevcut WAV'ı atlama
- Retry
- Rapor yazma
- WAV birleştirme
- `--limit`
- `--overwrite`
- `--no-merge`

Düzeltilmesi veya teyit edilmesi gerekenler:

1. `PROJECT_DIR` yalnızca bir kez tanımlanmalı.
2. Global ve `main()` içindeki çift Gemini client kaldırılmalı.
3. Yalnızca `main()` içinde client oluşturulmalı ve `finally` ile kapatılmalı.
4. `is_daily_quota_error()` gerçekten `generate_audio()` içinde çağrılmalı.
5. Günlük kota durumunda `RuntimeError("GEMINI_TTS_DAILY_QUOTA_EXHAUSTED")` veya daha iyi özel exception üretilmeli.
6. `paused_quota` durumu finalde `partially_failed` olarak ezilmemeli.
7. Rapor her chunk sonrasında checkpoint olarak yazılmalı.
8. Kota duraklaması traceback yerine kontrollü çıkış olmalı.
9. `--limit` davranışı açık biçimde tanımlanmalı. Şu an ilk N dosyayı kontrol ediyor, ilk N eksik dosyayı üretmiyor.
10. Kullanılmayan `base64` ve benzeri importlar temizlenmeli.
11. AFC uyarısı ses üretimini engellemiyor. Çalışan çağrı sırf uyarıyı kaldırmak için bozulmamalı.

---

## 7. Provider ve fallback matrisi

### PDF'den temiz metne

```text
1. Güvenli mekanik preclean
2. Gemini text cleaner
3. Gemini kota veya rate limit verirse Groq
4. Groq kota verirse işi paused_quota olarak kaydet
5. Kullanıcı offline fallback seçmişse OCRmyPDF + Tesseract
6. Hiçbir AI kullanılamıyorsa precleaned TXT ve Read Aloud PDF üret
```

### Temiz metinden sese

```text
1. Gemini TTS
2. Gemini kotası veya kimlik sorunu varsa ücretsiz TTS fallback
3. Fallback de yoksa temiz PDF üret ve Edge Read Aloud'a hazırla
4. Üretilmiş WAV'ları her durumda koru
5. Yeterli ses süresi varsa kısmi podcast oluştur
```

### Olası TTS fallback seçenekleri

Değerlendirilen seçenekler:

```text
Edge TTS
OpenVox
OpenVoice
Yerel Türkçe TTS motorları
```

Şu ana kadar en pratik ücretsiz fallback `edge-tts` olarak değerlendirildi. Ancak tamamen offline değildir. OpenVoice V2'nin Türkçe yerel desteği belirsiz veya yetersiz kabul edildi. OpenVox tarafında ücretsiz kullanım limitleri olabilir.

Agent, mevcut tarih ve güncel paket durumuna göre Türkçe kalitesi iyi, mümkünse offline ve ücretsiz bir TTS fallback araştırmalıdır. Bu araştırma ayrı bir branch veya izole testle yapılmalı, çalışan Gemini TTS bozulmamalıdır.

---

## 8. Günlük 15-20 dakikalık podcast hedefi

Tam kitabı tek günde üretmek zorunlu değildir. Sistem günlük kullanım bütçesine göre çalışmalıdır.

Önerilen günlük mod:

```text
Hedef süre: 15-20 dakika
Tahmini Türkçe okuma hızı: yapılandırılabilir
Günlük metin miktarı: süre hedefinden hesaplanır
Sadece gerekli sayıda TTS chunk seçilir
```

Yeni ayarlar önerilir:

```ini
DAILY_TARGET_MINUTES=20
DAILY_MAX_TEXT_CHARACTERS=
DAILY_MAX_TTS_CHUNKS=
STOP_ON_DAILY_QUOTA=true
CREATE_PARTIAL_AUDIO=true
CREATE_READALOUD_PDF=true
```

Program şu çıktıları günlük tarihli klasörlerde saklayabilir:

```text
outputs\2026-08-30\
  clean_text.txt
  readaloud.pdf
  audio_chunks\
  podcast_partial.wav
  job_status.json
  review_required.txt
```

Bir sonraki gün aynı job çalıştırıldığında tamamlanmış chunk'ları atlayıp sıradaki eksik metin veya ses chunk'ından devam etmelidir.

---

## 9. Yeni genel job veri modeli

Her PDF ve sayfa aralığı için kararlı bir job kimliği oluşturulmalıdır:

```text
PDF dosya hash'i
başlangıç sayfası
bitiş sayfası
pipeline sürümü
```

Örnek klasör:

```text
work\<job_id>\
```

Önerilen `job_status.json`:

```json
{
  "job_id": "...",
  "pdf_file": "...",
  "pdf_sha256": "...",
  "start_page": 8,
  "end_page": 334,
  "status": "paused_quota",
  "stage": "tts",
  "text_provider_primary": "gemini",
  "text_provider_fallback": "groq",
  "tts_provider_primary": "gemini",
  "tts_provider_fallback": "edge",
  "text_chunks_total": 91,
  "text_chunks_completed": 69,
  "tts_chunks_total": 307,
  "tts_chunks_completed": 10,
  "paused_at": "tts_chunk_0011",
  "last_error_type": "daily_quota",
  "created_at": "...",
  "updated_at": "..."
}
```

Bu dosya uygulamanın tek gerçek durum kaynağı olmalıdır.

---

## 10. Agent için zorunlu çalışma yaklaşımı

Agent aşağıdaki sırayla ilerlemelidir.

### Aşama 1: Envanter

Önce dosya ağacını çıkar:

```powershell
Get-ChildItem -Recurse -File |
    Select-Object FullName, Length, LastWriteTime
```

Şunları belirle:

- Hangi dosyalar ana üretim kodu
- Hangi dosyalar eski deney
- Hangi dosyalar test
- Hangi klasörler geçici çıktı
- Aynı işlevin kaç farklı kopyası var
- Import edilen gerçek modüller hangileri

### Aşama 2: Testleri çalıştır

Mevcut çalışan davranışları bozmadan önce testler oluştur veya toparla:

```text
test_precleaner.py
test_gemini_fallback.py
test_groq_cleaner.py
test_chunk_resume.py
test_tts_chunk_integrity.py
test_gemini_tts.py
test_wav_resume.py
test_job_state.py
```

Özellikle şu regresyon örnekleri test edilmelidir:

```text
l 776 -> 1776
roma nındaki -> romanındaki
gir di -> girdi
yeme ği -> yemeği
bir tutulur olmuştur ifadesi korunmalı
bu yana ifadesi beri yapılmamalı
```

### Aşama 3: Dosya konsolidasyonu

Aynı işlevi yapan deneysel dosyaları hemen silme. Şu sınıflara ayır:

```text
src veya pipeline: aktif kod
tests: testler
scripts: yardımcı komutlar
archive: eski deneyler
work: job verisi
outputs: kullanıcı çıktıları
```

### Aşama 4: Tek giriş noktası

Nihai tek komut hedefi:

```powershell
python .\podcast_from_pdf.py
```

veya ileride UI:

```text
PDF seç
Sayfa aralığı gir
API key gir
Başlat
```

### Aşama 5: State machine

Pipeline aşamaları açık bir state machine olmalıdır:

```text
created
pdf_extracted
precleaned
text_cleaning
text_paused_quota
text_completed
tts_chunked
tts_generating
tts_paused_quota
tts_completed
merged
completed
completed_text_only
failed_configuration
failed_unrecoverable
```

### Aşama 6: Fallback ve partial output

Her aşama başarısız olduğunda bir sonraki yararlı çıktı seviyesine düşmelidir.

```text
Final ses yoksa kısmi ses
Kısmi ses yoksa temiz TXT
Temiz TXT varsa Read Aloud PDF
AI clean yoksa precleaned TXT
```

---

## 11. Güvenlik ve secret yönetimi

API key'ler hiçbir zaman kaynak koda, rapora veya Git deposuna yazılmamalıdır.

`.gitignore` içinde en az şunlar olmalıdır:

```gitignore
.env
.venv/
__pycache__/
*.pyc
work/
tts_continuation_audio/
*.wav
```

Kullanıcı API key'leri UI'dan girerse:

- Varsayılan olarak diske düz metin yazma.
- Kayıt seçeneği verilecekse güvenli credential storage kullan.
- Loglarda yalnızca `key present`, uzunluk veya son dört karakter gibi güvenli tanı bilgileri kullanılmalı.

---

## 12. Çıktı kalite ilkeleri

### Metin için

- Kaynak anlam korunmalı.
- Yeniden yazma yapılmamalı.
- Hiçbir paragraf sessizce kaybolmamalı.
- Model sonucu kabul edilmezse kaynak korunmalı.
- Başlıklar, alıntılar ve özel isimler mümkün olduğunca korunmalı.
- Şema veya görsel sayfaları ayrıca işaretlenmeli.

### Ses için

- Metin chunk toplamı kaynak temiz metni normalize edildiğinde birebir temsil etmeli.
- WAV teknik özellikleri doğrulanmalı.
- Boş veya çok kısa WAV geçerli sayılmamalı.
- Dosya oluşturma atomik olmalı.
- Aynı chunk yeniden üretilmeden önce hash veya metadata kontrolü yapılmalı.
- TTS ayarları değişirse eski ses cache'i geçersiz sayılmalı.

---

## 13. Şu an bilinen çalışan komutlar

### Groq cleaner test

```powershell
python .\test_groq_cleaner.py
```

### Mevcut job üzerinde Groq devamı

```powershell
python -c "import os; from pathlib import Path; from pipeline.groq_text_cleaner import clean_text_file_with_groq; job=Path(os.environ['TEST_JOB']); result=clean_text_file_with_groq(input_file=job/'precleaned_text.txt', output_file=job/'hybrid_cleaned_text.txt', work_directory=job); print(result)"
```

### TTS chunk bütünlük kontrolü

```powershell
python -c "from pathlib import Path; import re; source=Path('podcast_continuation_text.txt').read_text(encoding='utf-8-sig'); files=sorted(Path('tts_continuation_chunks').glob('tts_chunk_*.txt')); chunks=' '.join(p.read_text(encoding='utf-8-sig') for p in files); norm=lambda x: re.sub(r'\s+', ' ', x).strip(); print('TTS chunk sayısı:', len(files)); print('Metin tam korunuyor:', norm(source)==norm(chunks)); print('Kaynak:', len(norm(source))); print('Chunklar:', len(norm(chunks)))"
```

### Tek veya sınırlı TTS testi

```powershell
python .\batch_gemini_tts.py `
    --input-dir .\tts_continuation_chunks `
    --output-dir .\tts_continuation_audio `
    --final-file .\podcast_continuation_complete.wav `
    --limit 10 `
    --no-merge
```

### Tüm eksik sesleri üretme

```powershell
python .\batch_gemini_tts.py `
    --input-dir .\tts_continuation_chunks `
    --output-dir .\tts_continuation_audio `
    --final-file .\podcast_continuation_complete.wav `
    --no-merge
```

### Tamamlandığında birleştirme

```powershell
python .\batch_gemini_tts.py `
    --input-dir .\tts_continuation_chunks `
    --output-dir .\tts_continuation_audio `
    --final-file .\podcast_continuation_complete.wav
```

---

## 14. Öncelikli yapılacaklar

### P0, kritik

1. [x] Gerçek dosya ağacını ve aktif importları incele.
2. [x] `batch_gemini_tts.py` içindeki çift client ve çift `PROJECT_DIR` durumunu doğrula ve gereksiz dotenv/importları temizle.
3. [x] Gemini TTS günlük kota algılamasını üretim akışına bağla.
4. [x] `paused_quota` durumunun başka final durumlarıyla ezilmesini engelle.
5. [x] Aşamaları atomik `job_status.json` checkpoint'leriyle izle.
6. [x] Groq subchunk cache'ini tamamla; ana chunk ortasındaki başarılı alt chunk'ları koru.
7. [x] Gemini, Groq, Gemini TTS ve Edge TTS için ortak kaynak hash cache doğrulaması ekle.
8. [x] Hata veya kotada kısmi ses, clean TXT ve Read Aloud PDF üret.

### P1, genel kullanım

1. Tek kitaba özel sabit yolları kaldır.
2. PDF upload ve sayfa aralığı girişini genelleştir.
3. Farklı PDF türlerinde test yap:
   - Doğal text layer
   - Taranmış PDF
   - Karma görsel ve metin
   - İki sütunlu kitap
   - Bol dipnotlu kitap
4. Sayfa kalite sınıflandırması ekle.
5. OCRmyPDF fallback'i opsiyonel olarak bağla.
6. Edge Read Aloud PDF üretimini otomatikleştir.
7. Günlük 15-20 dakika hedef modu ekle.
8. Final çıktı klasör yapısını standardize et.

### P2, kullanıcı arayüzü

1. PDF seçici
2. Başlangıç ve bitiş sayfası
3. Gemini ve Groq API key alanları
4. Hedef süre seçimi
5. Ses seçimi
6. İlerleme çubuğu
7. Duraklat, devam et ve iptal
8. Kota uyarıları
9. Temiz TXT, Read Aloud PDF ve ses indirme düğmeleri
10. Job geçmişi

---

## 15. Kabul kriterleri

Proje aşağıdaki koşullar gerçekleştiğinde ilk genel sürüme hazır sayılabilir:

- Yeni bir PDF kullanıcı tarafından seçilebiliyor.
- Sayfa aralığı girilebiliyor.
- Job kimliği ve klasörü otomatik oluşuyor.
- Metin ön temizliği çevrimdışı çalışıyor.
- Gemini kota verirse Groq otomatik devreye giriyor.
- Groq kota verirse iş duraklatılıyor ve devam edebiliyor.
- Metin tamamlanmasa bile mevcut kaliteli sonuç korunuyor.
- TTS chunk'ları kaynak metni eksiksiz temsil ediyor.
- Gemini TTS mevcut WAV'ları atlayarak devam ediyor.
- TTS kotası dolarsa iş duraklıyor veya fallback çalışıyor.
- En azından temiz TXT ve Read Aloud PDF her işte üretiliyor.
- 15-20 dakikalık günlük üretim modu çalışıyor.
- Tüm durumlar `job_status.json` ve raporlarda görülebiliyor.
- API key'ler loglara veya Git'e sızmıyor.

---

## 16. Agent'a başlangıç talimatı

VS Code Copilot Agent bu projeyi devraldığında şu istemle başlatılabilir:

```text
README_PROJECT_HANDOFF.md dosyasını tamamen oku.

Ardından proje klasöründeki gerçek dosya ağacını çıkar ve README'de adı geçen dosyaların mevcut olup olmadığını doğrula. Aktif import zincirini belirle. Hiçbir üretim veya work dosyasını silme.

Önce mevcut testleri çalıştır ve çalışan davranışların bir regresyon listesini oluştur. Daha sonra TODO listesindeki P0 maddelerini küçük ve ayrı değişiklikler halinde uygula. Her değişiklikten sonra ilgili testi çalıştır.

Birinci hedef, Gemini metin kotası dolduğunda Groq ile devam eden, iki sağlayıcı da kota verdiğinde işi paused_quota durumunda kaydedip ertesi gün kaldığı yerden sürdüren state yapısını sağlamlaştırmaktır.

İkinci hedef, Gemini TTS'nin mevcut WAV dosyalarını doğrulayarak devam etmesi, günlük kota dolduğunda kontrollü durması ve mümkünse ücretsiz Türkçe TTS fallback'e geçmesidir.

Üçüncü hedef, herhangi bir ses üretilemese bile temiz TXT ve Edge Read Aloud uyumlu PDF üretmektir.

Kod değişikliklerinden önce kısa plan ver. Kullanıcıdan her küçük adım için onay isteme. Çalışan dosyaları yedekle veya Git üzerinden değişiklikleri izlenebilir tut.
```

---

## 17. Son durum özeti

Şu ana kadar başarıyla doğrulananlar:

```text
PDF metni çıkarıldı.
Kitap metni denetim raporu üretildi.
Gemini ile ilk metin chunk'ları temizlendi.
Gemini kotasında resume çalıştı.
Groq API ve qwen/qwen3.6-27b doğrulandı.
Qwen reasoning kapatıldı.
Groq OCR düzeltme testi başarılı oldu.
Groq büyük chunk alt bölme sistemi çalıştı.
Groq mevcut Gemini ve Groq sonuçlarını doğrulayarak kullandı.
Şüpheli Groq sonuçlarında kaynak metne dönüş çalıştı.
Groq günlük kotasında tamamlanmış ana chunk'lar korundu.
307 TTS metin chunk'ı oluşturuldu.
TTS chunk toplamının kaynak metni tam koruduğu doğrulandı.
Gemini TTS doğrudan test edildi.
Gemini TTS ile ilk WAV üretildi.
İlk 10 WAV için resume ve skip davranışı doğrulandı.
```

Henüz tamamlanması gereken ana iş:

```text
Bütün sistemi tek genel job yöneticisinde birleştirmek.
Kota ve fallback state'lerini kalıcılaştırmak.
TTS günlük kota davranışını tamamlamak.
Kısmi podcast ve Read Aloud PDF fallback'ini otomatikleştirmek.
Başka PDF'lerle regresyon testleri yapmak.
Basit kullanıcı arayüzü eklemek.
```

Projenin temel prensibi şudur:

> Hiçbir kota veya sağlayıcı hatası tamamlanmış işi çöpe atmamalı. Uygulama her çalıştırmada mümkün olan en yararlı sonucu bırakmalı ve ertesi gün aynı noktadan devam edebilmelidir.
