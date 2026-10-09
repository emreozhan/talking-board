# ema-tts

Panoya kopyaladığın metni bir kısayol tuşuyla kuyruğa ekleyip yerelde, internetsiz seslendiren küçük bir Windows aracı. Ses modeli [EMA Lightning](https://huggingface.co/canberkkkkkk/ema-lightning) (Türkçe, tek ses, Apache 2.0).

## Kullanım

1. `start.bat` ile başlat. Ekranın sağ altında yuvarlak bir logo çıkar (gri: model yükleniyor, koyu: hazır, mavi: okuyor, turuncu: duraklatıldı, kırmızı: hata).
2. Metni kopyala, **Ctrl+Alt+Space**'e bas. Metin kuyruğa eklenir ve sırayla okunur. Logonun halkası yeşil yanarsa eklendi, kırmızı yanarsa pano boştur ya da metin zaten kuyruktadır.
3. Logoya tıkla: kuyruk paneli açılır. Her satırdaki ✕ o metni siler; okunmakta olanı silersen hemen susar. Panelde Duraklat/Devam ve Tümünü sil de var.
4. Logoyu sürükleyerek taşıyabilirsin; yeri hatırlanır. Sağ tık menüsünden çıkış yapılır.

Agent cevapları için markdown temizlenir: başlık, madde, kalın/italik işaretleri atılır, kod blokları "Kod bloğu" diye geçilir, URL'ler "bağlantı" diye okunur.

## Kurulum

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` CUDA 12.8 torch'unu çeker (NVIDIA GPU için). Model ağırlıkları (~34 MB) ilk açılışta Hugging Face'ten indirilir. Model yalnızca ilk çalıştırmada bir kez ısınma için uzun sürer (~40 sn), sonrasında açılış birkaç saniyedir.

## Ayarlar

`config.example.json` dosyasını `config.json` olarak kopyalayıp düzenle:

| Alan | Açıklama |
| --- | --- |
| `hotkey` | pynput biçiminde kısayol, ör. `<ctrl>+<alt>+<space>`. AltGr, Alt sayılır. |
| `speed` | Okuma hızı, 0.25–4 |
| `sample_rate` | 48000, 24000, 16000 veya 8000 |
| `max_chars` | Modelin tek seferde okuduğu en uzun parça |
| `use_lightning` | `torch.compile` ile hızlı yol; Windows'ta genelde Triton olmadığı için kapalı |

Hatalar `ema-tts.log` dosyasına yazılır.

## Test

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```
