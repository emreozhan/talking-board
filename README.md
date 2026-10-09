# ema-tts

Panoya kopyaladığın metni bir kısayol tuşuyla kuyruğa ekleyip yerelde, internetsiz seslendiren küçük bir Windows aracı. Ses modeli [EMA Lightning](https://huggingface.co/canberkkkkkk/ema-lightning) (Türkçe, tek ses, Apache 2.0).

## Kullanım

1. `start.bat` ile başlat. Ekranın sağ altında yuvarlak bir logo çıkar (gri: model yükleniyor, koyu: hazır, mavi: okuyor, turuncu: duraklatıldı, kırmızı: hata).
2. Metni kopyala, **Ctrl+Alt+Space**'e bas. Metin kuyruğa eklenir ve sırayla okunur. Logonun halkası yeşil yanarsa eklendi, kırmızı yanarsa pano boştur ya da metin zaten kuyruktadır.
3. Logoya tıkla: kuyruk paneli açılır. Her satırdaki ✕ o metni siler; okunmakta olanı silersen hemen susar. Panelde Duraklat/Devam ve Tümünü sil de var.
4. Logoyu sürükleyerek taşıyabilirsin; yeri hatırlanır. Sağ tık menüsünden çıkış yapılır.
5. Sağ tık menüsündeki **Ayarlar…** ya da paneldeki ⚙ ses ayarlarını açar. Değişiklikler hemen duyulur (hız, ses rengi, cihaz bir sonraki metinde; ses seviyesi anında). **Dene** örnek bir cümleyi kuyruğa eklemeden okur, **Kaydet** yalnızca `voice` bölümünü `config.json`'a yazar.

Agent cevapları için markdown temizlenir: başlık, madde, kalın/italik işaretleri atılır, kod blokları "Kod bloğu" diye geçilir, URL'ler "bağlantı" diye okunur.

## Kurulum

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` CUDA 12.8 torch'unu çeker (NVIDIA GPU için). Model ağırlıkları (~34 MB) ilk açılışta Hugging Face'ten indirilir. Model yalnızca ilk çalıştırmada bir kez ısınma için uzun sürer (~40 sn), sonrasında açılış birkaç saniyedir.

## Ayarlar

`voice` bölümü Ayarlar penceresinden düzenlenir. Elle düzenlemek için `config.example.json` dosyasını `config.json` olarak kopyala:

| Alan | Açıklama |
| --- | --- |
| `hotkeys.add_clipboard` | pynput biçiminde kısayol, ör. `<ctrl>+<alt>+<space>`. AltGr, Alt sayılır. |
| `voice.speed` | Okuma hızı. Pencerede 0.5–2 (0.05 adım), dosyada 0.25–4 |
| `voice.volume` | Ses seviyesi, 0–100 (%). Çalarken de değişir |
| `voice.seed` | `null`: her okumada rastgele tını ve tonlama. Sayı (0–2147483647): aynı metin her seferinde aynı okunur. Hazırlar: Pes `16`, Doğal `1`, Tiz `23` (perdeyi yaklaşık bir yarım ton kaydırır) |
| `voice.output_device` | Çıkış cihazının adı; `null` sistem varsayılanı. Cihaz bulunamazsa varsayılana düşülür |
| `voice.sample_rate` | 48000, 24000 veya 16000 (dosyada 8000 de geçer) |
| `voice.max_chars` | Uzun metinlerin bölündüğü en uzun parça, 50–1000 karakter |
| `voice.use_lightning` | `torch.compile` ile hızlı yol; Windows'ta genelde Triton olmadığı için kapalı. Yeniden başlatma gerekir |

Hatalar `ema-tts.log` dosyasına yazılır.

## Test

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```
