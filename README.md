# soru-kitabi

Ders notu PDF'lerinden **yalnız o kaynağa dayanan** ve **bağımsız bir doğrulayıcıdan geçmiş** sorularla PDF soru kitabı üreten bir Claude Code eklentisi.

Yapay zekâyla soru üretmenin asıl sorunu hız değil, güven. Model, kaynakta olmayan bir bilgiyi soruya ekleyebilir, iki doğru şıklı soru yazabilir ya da cevap anahtarını yanlış verebilir. soru-kitabi bunu iki kuralla çözer:

1. **Her soru kaynağa bağlıdır.** Her soru, cevabı destekleyen sayfa numarasını ve o sayfadan birebir alıntıyı taşır.
2. **Doğrulanmamış soru kitaba girmez.** Ayrı bir doğrulayıcı ajan PDF sayfalarını kendisi açar ve her soruyu tek tek denetler. Geçemeyen soru yeniden yazılır. Bu kural araç tarafından zorlanır; atlanamaz.

## Ne üretir

- **Formatlar:** Test (4 ya da 5 şıklı) · Klasik (açık uçlu) · Karma klasik (klasik + boşluk doldurma + doğru/yanlış) · Tam karma (karma klasik + test)
- **Zorluk:** kolay %25, orta %40, zor %35 (ayarlanabilir)
- **Kitap düzeni:** kapak, zorluk dağılımı, içindekiler; bölümler kaynaktaki sırayla, her bölümde kolaydan zora; her bölüm sonunda cevap anahtarı (cevap, kısa açıklama, kaynak sayfa)

## Nasıl çalışır

```
PDF'ler ──► Plan ──► Yazarlar (paralel, parça başına bir ajan)
                         │
                         ▼
                 Mekanik kontrol (kitap.py)
                         │
                         ▼
             Doğrulayıcı (sayfaları kendisi açar) ──FAIL──► Yeniden yazım ─┐
                         │ PASS                                            │
                         ▼                                                 │
                 Birleştirme ──► PDF kitap        ◄────────────────────────┘
```

- **Orkestratör** (ana oturum): işi böler, ajanları paralel çalıştırır, kuyruğu yönetir.
- **soru-yazari** (varsayılan Sonnet): yalnız kendi sayfa aralığını okur ve soruları yazar.
- **soru-dogrulayici** (varsayılan Opus): alıntıyı sayfada arar. Dış bilgi, birden çok doğru şık, yanlış cevap, zayıf çeldirici, kaynağa gönderme ("slayta göre" vb.) ve yanlış zorluk etiketi için FAIL verir.
- **kitap.py**: plan, mekanik kontrol, onay, birleştirme ve PDF basımı. Yalnız Python standart kütüphanesini kullanır.

Uzun PDF'ler ~20 sayfalık parçalara bölünür. 20 sayfadan kısa bir PDF'ten 10 ya da 15 soru çıkar; daha uzun PDF'lerde sayfa başına bir soru düşer. Sayfalar çok az bilgi taşıyorsa yazar soru sayısını gerekçesiyle azaltabilir; doğrulayıcı bu gerekçeyi de denetler.

## Kurulum

Gerekenler: [Claude Code](https://claude.com/claude-code), Python 3.9+, PDF basımı için Google Chrome, Chromium ya da Microsoft Edge. Tarayıcı yoksa kitap HTML olarak çıkar; tarayıcıda açıp PDF olarak kaydedebilirsin.

Claude Code içinde:

```
/plugin marketplace add on-retryx/soru-kitabi
/plugin install soru-kitabi@retryx
```

## Kullanım

PDF'leri sohbete sürükle ya da yollarını yaz ve iste:

```
/soru-kitabi ~/Dersler/hafta3.pdf ~/Dersler/hafta4.pdf
```

ya da doğal dille: "Bu iki PDF'ten soru kitabı hazırla, hoca özellikle 2. bölümü vurguladı."

Araç formatı her seferinde sorar. Alanı (tıp, hukuk, mühendislik…) ve önemli notları (vurgulanan konular, kapsam dışı yerler) da sorar. Kitap, varsayılan olarak PDF'in yanına `<ad>-sorular.pdf` adıyla yazılır.

Denemek için [`ornek/su-dongusu.pdf`](ornek/su-dongusu.pdf) dosyasını kullanabilirsin: bu araç için yazılmış 4 sayfalık, telif sorunu olmayan bir örnek ders notu. Araçtan çıkan kitap [`ornek/su-dongusu-sorular.pdf`](ornek/su-dongusu-sorular.pdf) dosyasında: 10 test sorusu, 3'ü doğrulayıcıdan ilk turda geçemeyip yeniden yazıldı (zorluk etiketi, bilgi tekrarı, örtük kaynak göndermesi).

## Ayarlar

Varsayılanlar [`skills/soru-kitabi/ayarlar.json`](skills/soru-kitabi/ayarlar.json) dosyasındadır. Değiştirmek için `~/.config/soru-kitabi/ayarlar.json` oluştur ve yalnız değiştirmek istediğin anahtarları yaz:

```json
{
  "cikti_klasoru": "~/Masaüstü/Sınav Hazırlığı",
  "alan": "hukuk",
  "test_sik_sayisi": 4,
  "ekonomik_mod": true
}
```

| Anahtar | Varsayılan | Ne işe yarar |
|---|---|---|
| `alan` | boş | Soruların üslubu için ders alanı; boşsa sorulur |
| `cikti_klasoru` | boş | Boşsa kitap PDF'in yanına yazılır |
| `zorluk_oranlari` | 0.25 / 0.40 / 0.35 | Kolay / orta / zor oranı (toplam 1) |
| `kisa_pdf_sayfa_esigi` | 20 | Bundan kısa PDF'lere sabit seçenekli soru sayısı |
| `kisa_pdf_soru_secenekleri` | [10, 15] | Kısa PDF'te yazarın seçebileceği adetler |
| `uzun_pdf_sayfa_basina_soru` | 1.0 | Uzun PDF'te sayfa başına soru |
| `parca_esigi` / `parca_boyu` | 25 / 20 | Bundan uzun PDF'ler bu boyda parçalara bölünür |
| `ince_sayfa_alt_oran` | 0.6 | İnce sayfa azaltmasında inilebilecek en düşük oran |
| `test_sik_sayisi` | 5 | Test sorularında şık sayısı (4 ya da 5) |
| `ekonomik_mod` | false | Doğrulayıcıyı da Sonnet ile çalıştırır |
| `modeller` | sonnet / opus | Yazar ve doğrulayıcı modelleri |
| `konu_degistir_turu` / `en_cok_tur` | 4 / 6 | Geçemeyen soru için konu değiştirme ve kullanıcıya sorma eşikleri |
| `chrome` | boş | Tarayıcı yolu (ya da `SORU_KITABI_CHROME` ortam değişkeni) |

Geçerli ayarları görmek için: `python3 skills/soru-kitabi/bin/kitap.py ayarlar`

## Maliyet

Doğrulayıcı her sayfayı yeniden açtığı için araç token harcar. Örnek ders notu (4 sayfa, 10 soru, 4 doğrulama turu) API fiyatlarıyla yaklaşık 2,2 $ ve 6 dakika tuttu. 60 sayfalık bir PDF, Claude Pro planında oturum limitinin kayda değer bir kısmını tüketebilir. Limitin darsa `ekonomik_mod` ayarını aç ya da PDF'leri tek tek işle. Ekonomik modda doğrulama biraz daha az titiz olur.

## Gizlilik ve telif

- Araç yalnız senin verdiğin dosyaları okur. Web araması yapmaz, başka kaynak kullanmaz.
- Üretilen sorular senin çalışma malzemendir. Araç bunları başka bir yere kopyalamaz.
- Yalnız kullanma hakkın olan materyali yükle. Üretilen sorular kaynaktan kısa alıntılar içerir.
- Doğrulayıcı hataları büyük ölçüde yakalar ama bir yapay zekâdır. Kitabı sınava hazırlık için kullan; resmî sınav sorusu olarak kullanmadan önce bir insan kontrol etmeli.

## Geliştirme

```
python3 -m unittest discover -s tests
claude plugin validate .
```

Yerelde denemek için: `claude --plugin-dir /yol/soru-kitabi`

## Lisans

[MIT](LICENSE) · Retry-X

---

## English summary

**soru-kitabi** is a Claude Code plugin that turns lecture-note PDFs into a printable question book (multiple choice, open-ended, fill-in-the-blank, true/false). Every question cites its source page with a verbatim quote. A separate verifier agent opens the pages itself and checks each question for outside knowledge, multiple correct answers, wrong keys and source references. Unverified questions cannot enter the book. The interface and output are in Turkish. Install with `/plugin marketplace add on-retryx/soru-kitabi` and `/plugin install soru-kitabi@retryx`. MIT licensed.
