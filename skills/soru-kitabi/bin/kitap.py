#!/usr/bin/env python3
"""soru-kitabi yardımcı aracı (yalnız standart kütüphane).

Komutlar:
  ayarlar                             Geçerli ayarları JSON olarak yaz
  sayfa <pdf>                         Sayfa sayısı
  sayfalar <pdf> <3-12> --cikti DIR   Sayfaları PNG'ye çevir (pymupdf ya da pdftoppm varsa)
  plan <pdf>... --cikti plan.json     Soru adedi, zorluk dağılımı, parçalar
       [--sayfa dosya.pdf=N ...]      sayfa sayısı okunamazsa elle ver
  kontrol <parca.json> [--format F] [--beklenen kolay=2,orta=5,zor=3] [--sik 4|5]
       [--en-az N]                    ince sayfalar için gerekçeli azaltmaya izin ver
  onayla <parca.json> <dogrulama.json>   PASS alan soruları işaretle
  yerlestir <parca.json> <duzeltme.json> Aynı id'li soruları yenisiyle değiştir
  birlestir <calisma_dizini> --cikti kitap.json --baslik B --format F --pdf P
  render <kitap.json> <cikti.pdf>     HTML + Chrome/Chromium/Edge headless ile PDF

Ayarlar: varsayılanlar ../ayarlar.json; üzerine sırasıyla
~/.config/soru-kitabi/ayarlar.json ve SORU_KITABI_AYARLAR ortam değişkeninin
gösterdiği dosya yazılır (yalnız verilen anahtarlar değişir).
"""
import argparse
import datetime as dt
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent


def _birlestir_sozluk(taban, ust):
    for k, v in ust.items():
        if isinstance(v, dict) and isinstance(taban.get(k), dict):
            _birlestir_sozluk(taban[k], v)
        else:
            taban[k] = v
    return taban


def ayarlari_yukle():
    ayar = json.loads((KOK / "ayarlar.json").read_text(encoding="utf-8"))
    ayar.pop("_aciklama", None)
    adaylar = [Path.home() / ".config" / "soru-kitabi" / "ayarlar.json"]
    if os.environ.get("SORU_KITABI_AYARLAR"):
        adaylar.append(Path(os.environ["SORU_KITABI_AYARLAR"]).expanduser())
    for p in adaylar:
        if p.is_file():
            try:
                ust = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"HATA: {p} okunamadı: {e}", file=sys.stderr)
                sys.exit(2)
            ust.pop("_aciklama", None)
            _birlestir_sozluk(ayar, ust)
    oran = ayar["zorluk_oranlari"]
    if abs(sum(oran.values()) - 1) > 1e-6 or set(oran) != {"kolay", "orta", "zor"}:
        print("HATA: zorluk_oranlari kolay/orta/zor olmalı ve toplamı 1 etmeli", file=sys.stderr)
        sys.exit(2)
    if ayar["test_sik_sayisi"] not in (4, 5):
        print("HATA: test_sik_sayisi 4 ya da 5 olmalı", file=sys.stderr)
        sys.exit(2)
    return ayar


AYAR = ayarlari_yukle()
ORAN = AYAR["zorluk_oranlari"]
ZORLUKLAR = ["kolay", "orta", "zor"]
ZORLUK_AD = {"kolay": "Kolay", "orta": "Orta", "zor": "Zor"}
FORMATLAR = {
    "test": {"test"},
    "klasik": {"klasik"},
    "karma-klasik": {"klasik", "bosluk", "dogru_yanlis"},
    "tam-karma": {"klasik", "bosluk", "dogru_yanlis", "test"},
}
FORMAT_AD = {
    "test": "Test (çoktan seçmeli)",
    "klasik": "Klasik",
    "karma-klasik": "Karma Klasik",
    "tam-karma": "Tam Karma",
}
TIP_AD = {
    "test": "Çoktan seçmeli",
    "klasik": "Klasik",
    "bosluk": "Boşluk doldurma",
    "dogru_yanlis": "Doğru / Yanlış",
}
SIKLAR = ["A", "B", "C", "D", "E"]
SIK_SAYI = {4: ["A", "B", "C", "D"], 5: SIKLAR}
YASAK = [
    r"\bpdf", r"slayt", r"sunum(da|a|un)", r"\bnotlar(da|a|ın)", r"ders not",
    r"metne göre", r"metinde", r"kaynağa göre", r"kaynakta", r"yukarıdaki (metin|kaynak|bölüm)",
    r"bu bölümde", r"bölümüne göre", r"dokümanda", r"belgede", r"sayfa \d", r"\bs\. ?\d",
    r"yazara göre", r"tabloya göre", r"şekle göre", r"görselde",
    r"için verilen", r"yukarıda verilen", r"için belirtilen", r"yukarıda belirtilen",
]
TARAYICI_ADAYLARI = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
SABLON = KOK / "sablon"


def tarayici_bul():
    adaylar = [os.environ.get("SORU_KITABI_CHROME"), AYAR.get("chrome")] + TARAYICI_ADAYLARI
    for c in adaylar:
        if not c:
            continue
        p = Path(c).expanduser()
        if p.is_file():
            return str(p)
        w = shutil.which(c)
        if w:
            return w
    return None


def hata(msg):
    print(f"HATA: {msg}", file=sys.stderr)
    sys.exit(2)


# ---------- sayfa ----------
def sayfa_sayisi(pdf: Path):
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["mdls", "-raw", "-name", "kMDItemNumberOfPages", str(pdf)],
                capture_output=True, text=True, timeout=15,
            ).stdout.strip()
            if out.isdigit() and int(out) > 0:
                return int(out)
        except Exception:
            pass
    try:
        data = pdf.read_bytes()
        counts = [int(m) for m in re.findall(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", data)]
        counts += [int(m) for m in re.findall(rb"/Count\s+(\d+)[^>]*?/Type\s*/Pages\b", data)]
        if counts:
            return max(counts)
        n = len(re.findall(rb"/Type\s*/Page\b", data))
        if n:
            return n
    except Exception:
        pass
    return None


def cmd_sayfalar(a):
    """Sayfa aralığını PNG'ye çevir (Read aracı PDF sayfası çizemediğinde yedek yol)."""
    pdf = Path(a.pdf).expanduser().resolve()
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", a.aralik)
    if not m:
        hata("aralık '3' ya da '3-12' biçiminde olmalı")
    bas, son = int(m.group(1)), int(m.group(2) or m.group(1))
    hedef = Path(a.cikti).expanduser()
    hedef.mkdir(parents=True, exist_ok=True)
    try:
        import fitz  # pymupdf, isteğe bağlı
        doc = fitz.open(str(pdf))
        son = min(son, doc.page_count)
        for i in range(bas, son + 1):
            yol = hedef / f"{pdf.stem}-s{i:03d}.png"
            doc[i - 1].get_pixmap(dpi=a.dpi).save(str(yol))
            print(yol)
        return
    except ImportError:
        pass
    if shutil.which("pdftoppm"):
        r = subprocess.run(["pdftoppm", "-png", "-r", str(a.dpi), "-f", str(bas), "-l", str(son),
                            str(pdf), str(hedef / f"{pdf.stem}-s")], capture_output=True, text=True)
        if r.returncode != 0:
            hata(f"pdftoppm başarısız: {r.stderr[-400:]}")
        for p in sorted(hedef.glob(f"{pdf.stem}-s*.png")):
            print(p)
        return
    print("HATA: sayfa görüntüsü için araç yok. Birini kur: 'pip install pymupdf' ya da "
          "'brew install poppler' (Linux: 'apt-get install poppler-utils').", file=sys.stderr)
    sys.exit(4)


def cmd_sayfa(a):
    n = sayfa_sayisi(Path(a.pdf).expanduser())
    if n is None:
        hata("sayfa sayısı okunamadı; plan komutunda --sayfa dosya.pdf=N ile ver")
    print(n)


# ---------- plan ----------
def dagilim(toplam):
    d = {z: math.floor(toplam * ORAN[z]) for z in ZORLUKLAR}
    d["orta"] += toplam - sum(d.values())
    return d


def bol(sayi, agirliklar):
    """sayi'yı ağırlıklara en büyük kalan yöntemiyle böl."""
    t = sum(agirliklar)
    ham = [sayi * w / t for w in agirliklar]
    taban = [math.floor(x) for x in ham]
    kalan = sayi - sum(taban)
    sira = sorted(range(len(ham)), key=lambda i: ham[i] - taban[i], reverse=True)
    for i in sira[:kalan]:
        taban[i] += 1
    return taban


def parcalar(sayfa):
    if sayfa <= AYAR["parca_esigi"]:
        return [(1, sayfa)]
    n = math.ceil(sayfa / AYAR["parca_boyu"])
    boy = bol(sayfa, [1] * n)
    out, bas = [], 1
    for b in boy:
        out.append((bas, bas + b - 1))
        bas += b
    return out


def pdf_plani(pdf: Path, sayfa: int):
    stem = pdf.stem
    kisa = sayfa < AYAR["kisa_pdf_sayfa_esigi"]
    secenekler = (list(AYAR["kisa_pdf_soru_secenekleri"]) if kisa
                  else [max(1, round(sayfa * AYAR["uzun_pdf_sayfa_basina_soru"]))])
    araliklar = parcalar(sayfa)
    agirlik = [b - a + 1 for a, b in araliklar]
    plan_parcalar = []
    for i, (a, b) in enumerate(araliklar, 1):
        plan_parcalar.append({"no": i, "sayfa_baslangic": a, "sayfa_bitis": b, "dagilim_secenekleri": {}})
    for toplam in secenekler:
        d = dagilim(toplam)
        per = {z: bol(d[z], agirlik) for z in ZORLUKLAR}
        for i, p in enumerate(plan_parcalar):
            pd = {z: per[z][i] for z in ZORLUKLAR}
            p["dagilim_secenekleri"][str(toplam)] = pd
    sonuc = {
        "pdf": str(pdf),
        "ad": pdf.name,
        "stem": stem,
        "sayfa": sayfa,
        "soru_secenekleri": secenekler,
        "karar": (f"yazar {' veya '.join(map(str, secenekler))} seçer" if kisa else "sabit"),
        "dagilim": {str(t): dagilim(t) for t in secenekler},
        "parcalar": plan_parcalar,
    }
    alt = AYAR["ince_sayfa_alt_oran"]
    for p in plan_parcalar:
        en_kucuk = min(sum(d.values()) for d in p["dagilim_secenekleri"].values())
        p["en_az"] = max(1, math.ceil(en_kucuk * alt))
    return sonuc


def cmd_plan(a):
    elle = {}
    for s in a.sayfa or []:
        k, _, v = s.rpartition("=")
        elle[Path(k).name] = int(v)
    planlar = []
    for p in a.pdfler:
        pdf = Path(p).expanduser().resolve()
        if not pdf.exists():
            hata(f"dosya yok: {pdf}")
        n = elle.get(pdf.name) or sayfa_sayisi(pdf)
        if not n:
            hata(f"{pdf.name}: sayfa sayısı okunamadı; --sayfa '{pdf.name}=N' ver")
        planlar.append(pdf_plani(pdf, n))
    stems = [p["stem"] for p in planlar]
    if len(set(stems)) != len(stems):
        hata("aynı ada sahip iki PDF var; çıktı adları çakışır")
    sonuc = {
        "olusturma": dt.datetime.now().isoformat(timespec="minutes"),
        # Read aracının 'pages' parametresi pdftoppm ister; yoksa ajanlar tüm PDF'i okur
        # ya da 'sayfalar' komutuyla PNG'ye çevirir.
        "read_pages_destegi": bool(shutil.which("pdftoppm")),
        "pdfler": planlar,
    }
    txt = json.dumps(sonuc, ensure_ascii=False, indent=2)
    if a.cikti:
        Path(a.cikti).expanduser().write_text(txt, encoding="utf-8")
    print(txt)


# ---------- kontrol ----------
def oku(p):
    try:
        return json.loads(Path(p).expanduser().read_text(encoding="utf-8"))
    except Exception as e:
        hata(f"{p} okunamadı: {e}")


def yaz(p, veri):
    Path(p).expanduser().write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")


def soru_hatalari(s, fmt=None, aralik=None, sik=None):
    h = []
    sid = s.get("id", "?")
    for alan in ["id", "bolum", "tip", "zorluk", "soru", "cevap", "aciklama", "kaynak", "alinti"]:
        if not s.get(alan):
            h.append(f"{sid}: '{alan}' eksik")
    tip = s.get("tip")
    if tip not in TIP_AD:
        h.append(f"{sid}: geçersiz tip '{tip}'")
    if fmt and tip not in FORMATLAR[fmt]:
        h.append(f"{sid}: '{tip}' tipi '{fmt}' formatında kullanılamaz")
    if s.get("zorluk") not in ZORLUKLAR:
        h.append(f"{sid}: geçersiz zorluk '{s.get('zorluk')}'")
    if tip == "test":
        sec = s.get("secenekler") or {}
        izinli = [SIK_SAYI[sik]] if sik else list(SIK_SAYI.values())
        if sorted(sec.keys()) not in izinli or not all(str(v).strip() for v in sec.values()):
            h.append(f"{sid}: test sorusunda tam {sik or '4 ya da 5'} dolu şık olmalı ({'/'.join(''.join(x) for x in izinli)})")
        if s.get("cevap") not in sec:
            h.append(f"{sid}: test cevabı mevcut şıklardan biri olmalı")
        if len({str(v).strip().lower() for v in sec.values()}) != len(sec):
            h.append(f"{sid}: aynı metinli şıklar var")
    elif s.get("secenekler"):
        h.append(f"{sid}: yalnız test sorusunda şık olur")
    if tip == "dogru_yanlis" and s.get("cevap") not in ("Doğru", "Yanlış"):
        h.append(f"{sid}: doğru/yanlış cevabı 'Doğru' ya da 'Yanlış' olmalı")
    if tip == "bosluk" and "____" not in str(s.get("soru", "")):
        h.append(f"{sid}: boşluk doldurmada soru metninde '____' olmalı")
    metin = " ".join([str(s.get("soru", ""))] + [str(v) for v in (s.get("secenekler") or {}).values()])
    for y in YASAK:
        m = re.search(y, metin, re.IGNORECASE)
        if m:
            h.append(f"{sid}: kaynağa gönderme yapan ifade: '{m.group(0)}'")
    k = s.get("kaynak") or {}
    if not isinstance(k, dict) or not k.get("pdf") or not k.get("bolum") or not isinstance(k.get("sayfa"), int):
        h.append(f"{sid}: kaynak {{pdf, bolum, sayfa(int)}} olmalı")
    elif aralik and not (aralik[0] <= k["sayfa"] <= aralik[1]):
        h.append(f"{sid}: kaynak sayfası {k['sayfa']} parça aralığı {aralik} dışında")
    return h


def cmd_kontrol(a):
    veri = oku(a.json)
    sorular = veri.get("sorular")
    if not isinstance(sorular, list) or not sorular:
        hata("'sorular' listesi boş ya da yok")
    fmt = a.format or veri.get("format")
    if fmt and fmt not in FORMATLAR:
        hata(f"geçersiz format '{fmt}'")
    aralik = None
    if veri.get("sayfa_baslangic") and veri.get("sayfa_bitis"):
        aralik = (veri["sayfa_baslangic"], veri["sayfa_bitis"])
    hatalar = []
    ids = [s.get("id") for s in sorular]
    if len(set(ids)) != len(ids):
        hatalar.append("tekrarlanan id var")
    metinler = [str(s.get("soru", "")).strip().lower() for s in sorular]
    if len(set(metinler)) != len(metinler):
        hatalar.append("birebir aynı soru metni tekrarlanmış")
    for s in sorular:
        hatalar += soru_hatalari(s, fmt, aralik, a.sik or AYAR["test_sik_sayisi"])
    say = {z: sum(1 for s in sorular if s.get("zorluk") == z) for z in ZORLUKLAR}
    if a.beklenen:
        secenekler = [b for b in a.beklenen.split("|")]
        uyan = False
        for b in secenekler:
            bek = {k: int(v) for k, v in (x.split("=") for x in b.split(","))}
            if all(say[z] == bek.get(z, 0) for z in ZORLUKLAR):
                uyan = True
        azaltma = False
        if not uyan and a.en_az:
            toplam = len(sorular)
            ust = max(sum(int(v) for _, v in (x.split("=") for x in b.split(","))) for b in secenekler)
            gerekce = str(veri.get("azaltma_gerekcesi", "")).strip()
            hedef = dagilim(toplam)
            if a.en_az <= toplam < ust and gerekce and all(abs(say[z] - hedef[z]) <= 1 for z in ZORLUKLAR):
                uyan = azaltma = True
            elif toplam < a.en_az:
                hatalar.append(f"soru sayısı {toplam}, ince sayfa alt sınırı {a.en_az}")
            elif not gerekce and toplam < ust:
                hatalar.append("beklenenden az soru var ama 'azaltma_gerekcesi' yok")
            elif toplam < ust:
                hatalar.append(f"azaltılmış {toplam} soruda zorluk dağılımı {hedef} (±1) olmalı, {say} var")
        if not uyan:
            hatalar.append(f"zorluk adetleri {say} beklenenle ({a.beklenen}) uyuşmuyor")
        elif azaltma:
            print(f"UYARI: ince sayfa azaltması kabul edildi ({len(sorular)} soru); doğrulayıcı gerekçeyi denetlemeli")
    if fmt in ("karma-klasik", "tam-karma"):
        eksik = FORMATLAR[fmt] - {s.get("tip") for s in sorular}
        if eksik and len(sorular) >= 8:
            hatalar.append(f"'{fmt}' formatında bu tipler hiç yok: {sorted(eksik)}")
    print(json.dumps({"soru": len(sorular), "zorluk": say,
                      "tip": {t: sum(1 for s in sorular if s.get('tip') == t) for t in TIP_AD}},
                     ensure_ascii=False))
    if hatalar:
        print("KONTROL BAŞARISIZ:")
        for x in hatalar:
            print(" -", x)
        sys.exit(1)
    print("KONTROL TAMAM")


# ---------- onayla / yerlestir ----------
def cmd_onayla(a):
    veri = oku(a.parca)
    sonuc = oku(a.dogrulama)
    durum = {r["id"]: r for r in sonuc.get("sonuclar", [])}
    gecen, kalan = [], []
    for s in veri["sorular"]:
        r = durum.get(s["id"])
        if r is None:
            if not s.get("dogrulandi"):
                kalan.append({"id": s["id"], "geri_bildirim": "doğrulayıcı bu soruyu değerlendirmedi"})
            continue
        if r.get("durum") == "PASS":
            s["dogrulandi"] = True
            s.pop("geri_bildirim", None)
            gecen.append(s["id"])
        else:
            s["dogrulandi"] = False
            s["geri_bildirim"] = r.get("geri_bildirim", "")
            kalan.append({"id": s["id"], "geri_bildirim": s["geri_bildirim"]})
    yaz(a.parca, veri)
    print(json.dumps({"gecen": len(gecen), "kalan": kalan}, ensure_ascii=False, indent=2))
    sys.exit(0 if not kalan else 1)


def cmd_yerlestir(a):
    veri = oku(a.parca)
    yeni = {s["id"]: s for s in oku(a.duzeltme).get("sorular", [])}
    idx = {s["id"]: i for i, s in enumerate(veri["sorular"])}
    for sid, s in yeni.items():
        if sid not in idx:
            hata(f"{sid} parçada yok; düzeltme mevcut id'yi kullanmalı")
        eski = veri["sorular"][idx[sid]]
        if eski.get("dogrulandi"):
            hata(f"{sid} zaten doğrulanmış; değiştirilemez")
        if s.get("zorluk") != eski.get("zorluk"):
            hata(f"{sid}: yeni soru aynı zorlukta olmalı ({eski.get('zorluk')})")
        s["dogrulandi"] = False
        s["tur"] = eski.get("tur", 1) + 1
        veri["sorular"][idx[sid]] = s
    yaz(a.parca, veri)
    print(f"{len(yeni)} soru yerleştirildi")


# ---------- birlestir ----------
def cmd_birlestir(a):
    d = Path(a.dizin).expanduser()
    dosyalar = sorted(d.glob("parca-*.json"), key=lambda p: int(re.findall(r"\d+", p.stem)[0]))
    dosyalar = [p for p in dosyalar if re.fullmatch(r"parca-\d+", p.stem)]
    if not dosyalar:
        hata("parca-N.json bulunamadı")
    sorular, dogrulanmamis = [], []
    for p in dosyalar:
        for s in oku(p)["sorular"]:
            if not s.get("dogrulandi"):
                dogrulanmamis.append(s["id"])
            sorular.append(s)
    if dogrulanmamis:
        hata(f"doğrulanmamış sorular var, kitap birleştirilemez: {dogrulanmamis}")
    kitap = {
        "baslik": a.baslik,
        "kaynak_pdf": a.pdf,
        "format": a.format,
        "tarih": dt.date.today().isoformat(),
        "sorular": sorular,
    }
    yaz(a.cikti, kitap)
    print(f"{len(sorular)} doğrulanmış soru -> {a.cikti}")


# ---------- render ----------
def bolumle(sorular):
    """Bölümleri kaynaktaki ilk sayfaya göre sırala, içinde kolaydan zora."""
    gruplar = {}
    for s in sorular:
        gruplar.setdefault(s["bolum"].strip(), []).append(s)
    sira = sorted(gruplar.items(), key=lambda kv: min(x["kaynak"]["sayfa"] for x in kv[1]))
    out = []
    for ad, ss in sira:
        ss.sort(key=lambda x: (ZORLUKLAR.index(x["zorluk"]), x["kaynak"]["sayfa"]))
        out.append((ad, ss))
    return out


def e(x):
    return html.escape(str(x))


def soru_html(no, s):
    tip = s["tip"]
    parts = [f'<article class="soru"><header><span class="no">{no}</span>'
             f'<span class="rozet {s["zorluk"]}">{ZORLUK_AD[s["zorluk"]]}</span>'
             f'<span class="tip">{TIP_AD[tip]}</span></header>']
    metin = e(s["soru"]).replace("____", '<span class="bosluk"></span>')
    parts.append(f'<p class="metin">{metin}</p>')
    if tip == "test":
        parts.append('<ol class="siklar">' + "".join(
            f'<li><b>{k})</b> {e(s["secenekler"][k])}</li>' for k in SIKLAR if k in s["secenekler"]) + "</ol>")
    elif tip == "dogru_yanlis":
        parts.append('<p class="dy"><span>☐ Doğru</span><span>☐ Yanlış</span></p>')
    elif tip == "klasik":
        parts.append('<div class="yazi-alani"></div>')
    parts.append("</article>")
    return "".join(parts)


def cevap_html(no, s):
    k = s["kaynak"]
    cevap = s["cevap"]
    if s["tip"] == "test":
        cevap = f'{cevap}) {s["secenekler"][cevap]}'
    return (f'<div class="cevap"><span class="no">{no}</span><div>'
            f'<p class="dogru">{e(cevap)}</p>'
            f'<p class="aciklama">{e(s["aciklama"])}</p>'
            f'<p class="kaynakca">Kaynak: {e(k["pdf"])} · Bölüm: {e(k["bolum"])} · s. {e(k["sayfa"])}</p>'
            f'</div></div>')


def cmd_render(a):
    kitap = oku(a.json)
    sorular = kitap["sorular"]
    bad = [s["id"] for s in sorular if not s.get("dogrulandi")]
    if bad:
        hata(f"doğrulanmamış soru kitaba giremez: {bad}")
    hatalar = []
    for s in sorular:
        hatalar += soru_hatalari(s, kitap.get("format"))
    if hatalar:
        hata("render öncesi kontrol: " + "; ".join(hatalar[:10]))
    bolumler = bolumle(sorular)
    say = {z: sum(1 for s in sorular if s["zorluk"] == z) for z in ZORLUKLAR}
    toplam = len(sorular)

    icerik, toc, no = [], [], 1
    for bi, (ad, ss) in enumerate(bolumler, 1):
        ilk = no
        govde = []
        cevaplar = []
        for s in ss:
            govde.append(soru_html(no, s))
            cevaplar.append(cevap_html(no, s))
            no += 1
        toc.append(f'<li><span class="b-no">{bi:02d}</span><span class="b-ad">{e(ad)}</span>'
                   f'<span class="b-ara">Soru {ilk}–{no - 1}</span></li>')
        icerik.append(
            f'<section class="bolum"><div class="bolum-bant"><span>Bölüm {bi:02d}</span>'
            f'<h2>{e(ad)}</h2><small>{len(ss)} soru</small></div>'
            f'<div class="sorular">{"".join(govde)}</div></section>'
            f'<section class="anahtar"><div class="anahtar-bant"><span>Bölüm {bi:02d}</span>'
            f'<h3>Cevap Anahtarı · {e(ad)}</h3></div>{"".join(cevaplar)}</section>')

    tarih = dt.date.fromisoformat(kitap.get("tarih", dt.date.today().isoformat()))
    aylar = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos",
             "Eylül", "Ekim", "Kasım", "Aralık"]
    tarih_tr = f"{tarih.day} {aylar[tarih.month - 1]} {tarih.year}"
    bar = "".join(f'<span class="{z}" style="flex:{max(say[z], 0.001)}"></span>' for z in ZORLUKLAR)
    leg = "".join(f'<li><i class="{z}"></i>{ZORLUK_AD[z]} <b>{say[z]}</b></li>' for z in ZORLUKLAR)

    sablon = (SABLON / "kitap.html").read_text(encoding="utf-8")
    css = (SABLON / "kitap.css").read_text(encoding="utf-8")
    sayfa = (sablon
             .replace("{{CSS}}", css)
             .replace("{{BASLIK}}", e(kitap["baslik"]))
             .replace("{{KAYNAK}}", e(kitap.get("kaynak_pdf", "")))
             .replace("{{FORMAT}}", e(FORMAT_AD.get(kitap.get("format"), kitap.get("format", ""))))
             .replace("{{TARIH}}", tarih_tr)
             .replace("{{TOPLAM}}", str(toplam))
             .replace("{{BOLUM_SAYISI}}", str(len(bolumler)))
             .replace("{{BAR}}", bar)
             .replace("{{LEJANT}}", leg)
             .replace("{{TOC}}", "".join(toc))
             .replace("{{ICERIK}}", "".join(icerik)))

    cikti = Path(a.cikti).expanduser().resolve()
    cikti.parent.mkdir(parents=True, exist_ok=True)
    if cikti.exists():
        damga = dt.datetime.fromtimestamp(cikti.stat().st_mtime).strftime("%Y-%m-%d-%H%M")
        yedek = cikti.with_name(f"{cikti.stem}-{damga}.pdf")
        cikti.rename(yedek)
        print(f"eski kitap korundu: {yedek.name}")
    with tempfile.TemporaryDirectory() as td:
        h = Path(td) / "kitap.html"
        h.write_text(sayfa, encoding="utf-8")
        if a.html:
            shutil.copy(h, Path(a.html).expanduser())
        tarayici = tarayici_bul()
        if not tarayici:
            yedek_html = cikti.with_suffix(".html")
            shutil.copy(h, yedek_html)
            print(f"HATA: Chrome, Chromium ya da Edge bulunamadı. Kitap HTML olarak yazıldı: {yedek_html}\n"
                  "Tarayıcıda açıp 'PDF olarak yazdır' ile kaydedebilir ya da SORU_KITABI_CHROME "
                  "ortam değişkenine tarayıcı yolunu verebilirsin.", file=sys.stderr)
            sys.exit(3)
        r = subprocess.run([tarayici, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                            f"--user-data-dir={td}/profil", "--no-first-run",
                            f"--print-to-pdf={cikti}", h.as_uri()],
                           capture_output=True, text=True, timeout=180)
    if not cikti.exists():
        hata(f"PDF üretilemedi: {r.stderr[-800:]}")
    print(f"{toplam} soru, {len(bolumler)} bölüm -> {cikti}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ayarlar"); p.set_defaults(f=lambda a: print(json.dumps(AYAR, ensure_ascii=False, indent=2)))
    p = sub.add_parser("sayfa"); p.add_argument("pdf"); p.set_defaults(f=cmd_sayfa)
    p = sub.add_parser("sayfalar"); p.add_argument("pdf"); p.add_argument("aralik")
    p.add_argument("--cikti", required=True); p.add_argument("--dpi", type=int, default=110)
    p.set_defaults(f=cmd_sayfalar)
    p = sub.add_parser("plan"); p.add_argument("pdfler", nargs="+"); p.add_argument("--cikti")
    p.add_argument("--sayfa", action="append"); p.set_defaults(f=cmd_plan)
    p = sub.add_parser("kontrol"); p.add_argument("json"); p.add_argument("--format", choices=list(FORMATLAR))
    p.add_argument("--beklenen", help="kolay=2,orta=5,zor=3 ya da seçenekler için '|' ile ayır")
    p.add_argument("--sik", type=int, choices=[4, 5], help="test sorularında zorunlu şık sayısı (varsayılan: ayar)")
    p.add_argument("--en-az", type=int, help="ince sayfa için gerekçeli azaltmada en az soru (plan: en_az)")
    p.set_defaults(f=cmd_kontrol)
    p = sub.add_parser("onayla"); p.add_argument("parca"); p.add_argument("dogrulama"); p.set_defaults(f=cmd_onayla)
    p = sub.add_parser("yerlestir"); p.add_argument("parca"); p.add_argument("duzeltme"); p.set_defaults(f=cmd_yerlestir)
    p = sub.add_parser("birlestir"); p.add_argument("dizin"); p.add_argument("--cikti", required=True)
    p.add_argument("--baslik", required=True); p.add_argument("--format", required=True, choices=list(FORMATLAR))
    p.add_argument("--pdf", required=True); p.set_defaults(f=cmd_birlestir)
    p = sub.add_parser("render"); p.add_argument("json"); p.add_argument("cikti")
    p.add_argument("--html", help="ara HTML'i de buraya kopyala"); p.set_defaults(f=cmd_render)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
