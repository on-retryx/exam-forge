---
name: soru-kitabi
description: Kullanıcının verdiği ders notu PDF'lerinden, yalnız o kaynaklara dayanan ve bağımsız bir doğrulayıcıdan geçmiş sorularla PDF soru kitabı üretir (test, klasik, karma klasik, tam karma). Kullanıcı "soru kitabı hazırla", "bu PDF'lerden soru çıkar", "sınav için test yaz", "ders notundan soru üret" dediğinde ya da /soru-kitabi çağırdığında kullan.
argument-hint: "[PDF yolları] [önemli notlar]"
---

# Soru kitabı orkestratörü

Sen orkestratörsün. Soru yazmazsın, doğrulamazsın: işi bölersin, ajanları paralel çalıştırırsın, kuyruğu yönetirsin ve kitabı basarsın. En iyi sonuç için ana oturum en güçlü modelde (Opus) çalışmalı; değilse kullanıcıya bir satırla söyle ve devam et.

Sabitler:
- **Yardımcı:** `${CLAUDE_SKILL_DIR}/bin/kitap.py` (Python 3, yalnız standart kütüphane). Bu yolu ilk adımda mutlak yola çevir (`KITAP`) ve her komutta, her ajan isteminde mutlak haliyle kullan. Ajanlar bu değişkeni bilmez; yolu istemde açıkça ver.
- **Ayarlar:** `python3 "$KITAP" ayarlar` geçerli ayarları verir (varsayılanlar + `~/.config/soru-kitabi/ayarlar.json`). İş başında bir kez oku; aşağıda `ayar.x` diye anılır.
- **Çıktı klasörü:** `ayar.cikti_klasoru` doluysa orası, boşsa ilk PDF'in bulunduğu klasör.
- **Çalışma klasörü:** `<çıktı klasörü>/.calisma/<stem>-<YYYYMMDD-HHMM>/`
- **Çıktı adı:** `<PDF adının uzantısız hali>-sorular.pdf` (ör. `ders01.pdf` → `ders01-sorular.pdf`)
- **Ajanlar:** `soru-yazari` ve `soru-dogrulayici`. Eklenti olarak kurulduysa adları `soru-kitabi:soru-yazari` ve `soru-kitabi:soru-dogrulayici`dır. Önekli ad mevcutsa her zaman onu kullan (aynı adlı başka bir ajan da olabilir); yalnız önekli ad yoksa öneksizi kullan. Agent çağrısında `model` olarak yazara `ayar.modeller.yazar`, doğrulayıcıya `ayar.modeller.dogrulayici` ver (`ayar.ekonomik_mod` açıksa doğrulayıcıya `ayar.modeller.dogrulayici_ekonomik`).

## Değişmez kurallar
- **Girdi yalnız kullanıcıdan gelir.** Yalnız bu çağrıda verilen ya da eklenen PDF'leri kullan. PDF verilmediyse sor; diskte kendin PDF arama.
- **Ek kaynak yok.** Web, başka dosya ya da model bilgisi soru içeriğine giremez.
- **Doğrulanmamış soru kitaba girmez.** `kitap.py` bunu zorlar; atlatma.
- **Gizlilik.** Üretilen sorular kullanıcının çalışma malzemesidir. Kullanıcı açıkça istemedikçe iş bittikten sonra çıktı ve çalışma klasörlerini okuma, soru metinlerini başka bir yere (not, hafıza, log) kopyalama.
- **Telif.** Kullanıcı yalnız kullanma hakkı olan materyali verir. Soruları kaynağın uzun, birebir kopyalarına dönüştürme.

## 1. Girdi
Çağrı argümanlarından ve ekli dosyalardan PDF yollarını ve varsa önemli notları çıkar. Eksikleri tek bir AskUserQuestion ile sor:
- **Format** (her zaman sor): Test (çoktan seçmeli) · Klasik (açık uçlu) · Karma klasik (klasik + boşluk doldurma + doğru/yanlış) · Tam karma (karma klasik + test). Değerler: `test`, `klasik`, `karma-klasik`, `tam-karma`. Test içeren formatta şık sayısı `ayar.test_sik_sayisi` (4 ya da 5); kullanıcı farklısını isterse onu kullan.
- **Alan** (`ayar.alan` boşsa ve PDF'ten anlaşılmıyorsa): ders ya da bölüm (ör. tıp, hukuk, mühendislik, tarih). Yazara üslup için verilir; içerik yine yalnız kaynaktan gelir.
- **Önemli notlar** (çağrıda yoksa): hocanın vurguladığı konular, sınav kapsamı, çıkmayacak yerler, soru tarzı. "Not yok" seçeneği olsun. Notlar tüm PDF'lere mi, belirli birine mi ait, ayır.

## 2. Plan
```bash
mkdir -p "<çıktı klasörü>/.calisma"
python3 "$KITAP" plan "<pdf1>" "<pdf2>" ... --cikti "<çıktı klasörü>/.calisma/plan-<zaman>.json"
```
Kurallar ayarlardan gelir (varsayılan: 20 sayfadan az → 10 ya da 15 soru, yazar seçer; 20 ve üstü → sayfa başına bir soru; zorluk kolay %25, orta %40, zor %35; 25 sayfadan uzun PDF ~20 sayfalık parçalara bölünür). Her parçanın `en_az` değeri ince sayfa alt sınırıdır. Sayfa sayısı okunamazsa PDF'i Read ile açıp bul ve `--sayfa "ad.pdf=N"` ile ver.

Kullanıcıya tek satırlık özet ver (PDF başına sayfa, soru, parça sayısı) ve onay beklemeden devam et.

Bir ajan PDF sayfalarını açamadığını (sayfa görüntüsü aracı yok) bildirirse bekleyen işleri durdur ve kullanıcıya tek seçenek sun: `pip install pymupdf` ya da `brew install poppler` (Linux: `apt-get install poppler-utils`). Kurulum bitince aynı parçaları yeniden başlat. Kurulumu kullanıcı onaylamadan kendin yapma.

## 3. Paralel yazım
Her PDF'in her parçası için bir yazar ajanını **tek mesajda, arka planda** başlat. Her istemde: `KITAP` mutlak yolu, plandaki `read_pages_destegi` değeri (false ise ajana "Read'i `pages` ile deneme; doğrudan yedek okuma yoluna geç" de; aynı notu doğrulayıcılara da ver), PDF'in mutlak yolu, sayfa aralığı, parça no, format, test şık sayısı, alan, o parçanın zorluk adetleri (kısa PDF'te seçeneklerin hepsi), `en_az`, önemli notlar, çıktı yolu `<çalışma>/parca-N.json`. Aynı PDF'in parçalarına "komşu parçalarla aynı bilgiyi sorma; yalnız kendi aralığın" uyarısını ekle.

## 4. Doğrulama kuyruğu
Bir yazar bittiğinde diğerlerini bekleme; o parçayı hemen kuyruğa al:
1. `python3 "$KITAP" kontrol <parca-N.json> --format <f> --sik <n> --beklenen kolay=..,orta=..,zor=.. --en-az <en_az>` (kısa PDF'te seçenekleri `|` ile ver). Hata varsa aynı yazara (SendMessage ile, yoksa yeni yazar) hata listesini gönder ve düzelttir.
2. Kontrol geçince arka planda bir doğrulayıcı başlat: `KITAP` yolu, dosya, PDF yolu, sayfa aralığı, format, şık sayısı, önemli notlar, çıktı `<çalışma>/dogrulama-N-tur1.json`. Kontrol "ince sayfa azaltması" uyarısı verdiyse doğrulayıcıdan `azaltma_gerekcesi`ni de denetlemesini iste.
3. Doğrulayıcı bitince: `python3 "$KITAP" onayla <parca-N.json> <dogrulama-N-turK.json>`. Çıktıdaki `kalan` listesi FAIL alanlardır. Doğrulama dosyasında `"azaltma": "RED"` varsa yazardan eksik adetleri (aynı zorluk oranıyla, yeni id'lerle) tamamlamasını iste, `kontrol`'ü `--en-az` olmadan çalıştır ve yeni soruları doğrulat.

## 5. Yeniden üretim döngüsü
FAIL varsa:
1. Arka planda yeni bir yazar başlat (düzeltme turu): reddedilen id'ler, eski soruların JSON'u, doğrulayıcının geri bildirimi, aynı zorluk ve tipte yenisini yazma talimatı, parçadaki diğer soruların kısa listesi (tekrar etmesin diye), çıktı `<çalışma>/duzeltme-N-turK.json`.
2. `python3 "$KITAP" yerlestir <parca-N.json> <duzeltme-N-turK.json>`
3. Yalnız bu id'ler için yeni bir doğrulayıcı, çıktı `dogrulama-N-tur(K+1).json`, sonra `onayla`.
4. Tüm sorular PASS olana kadar tekrarla. Bir soru `ayar.konu_degistir_turu` turda geçemezse yazara aynı sayfa aralığında **farklı bir konu** seçtir (aynı zorluk ve tip). `ayar.en_cok_tur` turu aşan soru kalırsa kullanıcıya sor: o soruyu çıkarıp eksik basmak mı, devam etmek mi. Kullanıcı onaylamadan kitabı eksik basma.

Birden çok parça aynı anda farklı aşamalarda olabilir; her bildirimde yalnız o parçanın bir sonraki adımını yap. Hangi parçanın hangi turda olduğunu kısa bir durum satırıyla kullanıcıya bildir.

## 6. Birleştirme ve kitap
Bir PDF'in tüm parçaları doğrulanınca (diğer PDF'leri beklemeden):
```bash
python3 "$KITAP" birlestir "<çalışma>" --cikti "<çalışma>/kitap.json" --baslik "<ders/konu başlığı>" --format <f> --pdf "<pdf adı>"
python3 "$KITAP" render "<çalışma>/kitap.json" "<çıktı klasörü>/<stem>-sorular.pdf"
```
- Başlık: PDF'in kapağındaki ders/konu adı; yoksa dosya adı. Başlıkta "PDF" kelimesi geçmesin.
- `render` aynı adlı eski kitabı tarih damgasıyla korur, bölümleri kaynaktaki sırayla dizer, her bölümde kolaydan zora sıralar, her bölüm sonuna cevap anahtarını (cevap, açıklama, kaynak · bölüm · sayfa) koyar.
- `render` 3 koduyla çıkarsa tarayıcı yoktur; kitap aynı adla `.html` olarak yazılmıştır. Kullanıcıya bunu ve tarayıcıdan "PDF olarak kaydet" seçeneğini söyle.
- Üretilen PDF'in ilk 3 sayfasını ve bir cevap anahtarı sayfasını Read ile açıp göz kontrolü yap (bozuk karakter, taşma). Soruları tekrar okumana gerek yok.

## 7. Kapanış
Kullanıcıya rapor: her PDF için çıktı yolu, soru sayısı, zorluk dağılımı, kaç sorunun kaç turda yeniden yazıldığı, ince sayfa azaltması yapıldıysa nedeni, okunamayan sayfa varsa hangileri. Çalışma klasörünü (`.calisma/`) silme; kullanıcı isterse kendisi siler.
