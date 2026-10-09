# ema-tts

Panoya kopyaladığın metni bir kısayol tuşuyla kuyruğa ekleyip yerelde, internetsiz seslendiren küçük bir Windows aracı. Ses modeli [EMA Lightning](https://huggingface.co/canberkkkkkk/ema-lightning) (Türkçe, tek ses, Apache 2.0).

## Kullanım

1. `start.bat` ile başlat. Ekranın sağ altında yuvarlak bir logo çıkar (gri: model yükleniyor, koyu: hazır, mavi: okuyor, turuncu: duraklatıldı, kırmızı: hata).
2. Metni kopyala, **Alt+Shift+1**'e bas. Metin kuyruğa eklenir ve sırayla okunur. Logonun halkası yeşil yanarsa eklendi, kırmızı yanarsa pano boştur ya da metin zaten kuyruktadır.
3. Logoya tıkla: kuyruk paneli açılır. Her satırdaki ✕ o metni siler; okunmakta olanı silersen hemen susar. Panelde Duraklat/Devam ve Tümünü sil de var.
4. Logoyu sürükleyerek taşıyabilirsin; yeri hatırlanır. Sağ tık menüsünden çıkış yapılır.

Kısayollar (sol Alt + Shift + rakam, hepsi sol elle basılır):

| Kısayol | Eylem |
| --- | --- |
| Alt+Shift+1 | Panodaki metni kuyruğa ekle |
| Alt+Shift+2 | Duraklat / devam et |
| Alt+Shift+3 | Okunmakta olanı atla (kuyruktan siler) |
| Alt+Shift+4 | Paneli aç / kapat |
| Alt+Shift+5 | Kuyruğu temizle |

Kısayollar Windows'a `RegisterHotKey` ile kaydedilir; bu yüzden başka bir programla çakışmazlar. Bir kısayolu başka bir program zaten kullanıyorsa uygulama yine açılır, panelin durum satırında ve `ema-tts.log`'da "Alt+Shift+1 başka bir program tarafından kullanılıyor (hotkeys.add_clipboard)" gibi bir uyarı çıkar. O zaman `config.json`'dan başka bir kısayol seç.

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
| `hotkeys.add_clipboard` | Panodakini kuyruğa ekler, varsayılan `<alt>+<shift>+1` |
| `hotkeys.pause_resume` | Duraklatır / devam ettirir, varsayılan `<alt>+<shift>+2` |
| `hotkeys.skip` | Okunmakta olanı atlar, varsayılan `<alt>+<shift>+3` |
| `hotkeys.toggle_panel` | Paneli açar / kapatır, varsayılan `<alt>+<shift>+4` |
| `hotkeys.clear` | Kuyruğu temizler, varsayılan `<alt>+<shift>+5` |
| `voice.speed` | Okuma hızı, 0.25–4 |
| `voice.sample_rate` | 48000, 24000, 16000 veya 8000 |
| `voice.max_chars` | Modelin tek seferde okuduğu en uzun parça |
| `voice.use_lightning` | `torch.compile` ile hızlı yol; Windows'ta genelde Triton olmadığı için kapalı |
| `server.enabled` | Yerel `/speak` uç noktasını aç/kapat |
| `server.port` | Uç noktanın portu (yalnızca `127.0.0.1`), varsayılan 47821 |
| `server.auto_read` | Agent mesajları kuyruğa eklensin mi; sağ tık menüsündeki "Agent mesajlarını oku" ile de değişir |

Kısayol biçimi: değiştiriciler `<ctrl>`, `<alt>`, `<shift>`, `<win>`; tuş olarak harf, rakam, `<f1>`–`<f24>`, `<space>` ve `<insert>`, `<delete>`, `<home>`, `<end>`, `<page_up>`, `<page_down>`, `<pause>`, `<scroll_lock>`, ok tuşları, medya tuşları (`<media_play_pause>` vb.); parçalar `+` ile birleşir, ör. `<ctrl>+<shift>+<f9>`. Boş dizge (`""`) o eylemi kapatır. Ctrl+Alt kombinasyonlarından kaçın: Türkçe Q klavyede AltGr, Ctrl+Alt sayılır ve @ € { } [ ] gibi karakterleri yazmayı engeller.

Hatalar `ema-tts.log` dosyasına yazılır.

## Agent mesajlarını otomatik okuma

Uygulama açıkken `127.0.0.1:47821` üzerinde küçük bir uç nokta dinler. Claude Code'un Stop hook'u her cevabın sonunda metni buraya yollar; metin kuyruğa eklenip okunur.

```powershell
python hooks/install_claude_hook.py              # ~/.claude/settings.json'a Stop hook'unu ekler
python hooks/install_claude_hook.py --uninstall  # kaldırır
```

Kurulum mevcut ayarları korur, yazmadan önce `settings.json.bak-ema-tts` yedeği alır; tekrar çalıştırmak girdiyi çoğaltmaz. Hook uygulama kapalıyken ya da hata olduğunda sessizce çıkar, Claude'u bekletmez. Okumayı geçici olarak kapatmak için logonun sağ tık menüsünden "Agent mesajlarını oku" işaretini kaldır. Hook portu `config.json`'dan okur; `EMA_TTS_PORT` ortam değişkeniyle geçersiz kılınabilir.

Başka araçlar da metin yollayabilir:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:47821/speak -ContentType 'application/json' -Body '{"text": "Merhaba"}'
```

Yanıtlar: `202 {"id": n}` eklendi, `202 {"ignored": true}` okuma kapalı, `409` boş ya da zaten kuyrukta, `400` hatalı JSON, `403` tarayıcı/yabancı host, `413` gövde 256 KB'tan büyük, `415` Content-Type `application/json` değil.

## Test

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```
