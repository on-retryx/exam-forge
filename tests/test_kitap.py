"""kitap.py için testler (yalnız standart kütüphane).

Çalıştır: python3 -m unittest discover -s tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
KITAP = KOK / "skills" / "soru-kitabi" / "bin" / "kitap.py"


def calistir(*args, env=None):
    e = dict(os.environ)
    e["HOME"] = env.get("HOME") if env and "HOME" in env else e.get("HOME", "")
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(KITAP), *map(str, args)],
                          capture_output=True, text=True, env=e)


def soru(i, zorluk="orta", tip="test", sayfa=2, parca=1, sik=5, **ek):
    s = {
        "id": f"p{parca}-q{i:02d}",
        "bolum": "Hücre",
        "tip": tip,
        "zorluk": zorluk,
        "soru": f"Mitokondrinin görevi {i} nedir?",
        "cevap": "A",
        "aciklama": "Açıklama.",
        "kaynak": {"pdf": "ders.pdf", "bolum": "Hücre", "sayfa": sayfa},
        "alinti": "Mitokondri enerji üretir.",
    }
    if tip == "test":
        s["secenekler"] = {h: f"şık {h}{i}" for h in "ABCDE"[:sik]}
    elif tip == "dogru_yanlis":
        s["cevap"] = "Doğru"
    elif tip == "bosluk":
        s["soru"] = f"Enerji ____ içinde üretilir ({i})."
        s["cevap"] = "mitokondri"
    elif tip == "klasik":
        s["cevap"] = "Enerji üretimi."
    s.update(ek)
    return s


def parca(sorular, **ek):
    v = {"kaynak": "ders.pdf", "parca": 1, "format": "test",
         "sayfa_baslangic": 1, "sayfa_bitis": 20, "sorular": sorular}
    v.update(ek)
    return v


class Temel(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.d = Path(self.td.name)
        self.env = {"HOME": str(self.d), "SORU_KITABI_AYARLAR": ""}

    def tearDown(self):
        self.td.cleanup()

    def yaz(self, ad, veri):
        p = self.d / ad
        p.write_text(json.dumps(veri, ensure_ascii=False), encoding="utf-8")
        return p

    def k(self, *args):
        return calistir(*args, env=self.env)


class Ayarlar(Temel):
    def test_varsayilanlar(self):
        r = self.k("ayarlar")
        self.assertEqual(r.returncode, 0, r.stderr)
        a = json.loads(r.stdout)
        self.assertEqual(a["test_sik_sayisi"], 5)
        self.assertNotIn("_aciklama", a)

    def test_kullanici_ust_yazimi(self):
        cfg = self.d / ".config" / "soru-kitabi"
        cfg.mkdir(parents=True)
        (cfg / "ayarlar.json").write_text('{"test_sik_sayisi": 4, "modeller": {"yazar": "opus"}}')
        a = json.loads(self.k("ayarlar").stdout)
        self.assertEqual(a["test_sik_sayisi"], 4)
        self.assertEqual(a["modeller"]["yazar"], "opus")
        self.assertEqual(a["modeller"]["dogrulayici"], "opus")

    def test_gecersiz_oran(self):
        p = self.yaz("a.json", {"zorluk_oranlari": {"kolay": 0.5, "orta": 0.5, "zor": 0.5}})
        self.env["SORU_KITABI_AYARLAR"] = str(p)
        r = self.k("ayarlar")
        self.assertEqual(r.returncode, 2)


class Plan(Temel):
    def plan(self, sayfa):
        pdf = self.d / "ders.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        r = self.k("plan", pdf, "--sayfa", f"ders.pdf={sayfa}")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["pdfler"][0]

    def test_kisa_pdf_iki_secenek(self):
        p = self.plan(12)
        self.assertEqual(p["soru_secenekleri"], [10, 15])
        self.assertEqual(p["dagilim"]["10"], {"kolay": 2, "orta": 5, "zor": 3})
        self.assertEqual(len(p["parcalar"]), 1)
        self.assertEqual(p["parcalar"][0]["en_az"], 6)

    def test_uzun_pdf_parcalanir_ve_toplam_tutar(self):
        p = self.plan(47)
        self.assertEqual(p["soru_secenekleri"], [47])
        self.assertEqual(len(p["parcalar"]), 3)
        self.assertEqual(p["parcalar"][-1]["sayfa_bitis"], 47)
        toplam = sum(sum(x["dagilim_secenekleri"]["47"].values()) for x in p["parcalar"])
        self.assertEqual(toplam, 47)
        d = p["dagilim"]["47"]
        self.assertEqual(sum(d.values()), 47)

    def test_sinir_25_tek_parca(self):
        self.assertEqual(len(self.plan(25)["parcalar"]), 1)


class Kontrol(Temel):
    def test_gecerli_parca(self):
        ss = [soru(1, "kolay"), soru(2, "orta"), soru(3, "zor")]
        p = self.yaz("p.json", parca(ss))
        r = self.k("kontrol", p, "--format", "test", "--beklenen", "kolay=1,orta=1,zor=1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("KONTROL TAMAM", r.stdout)

    def test_gonderme_yakalanir(self):
        p = self.yaz("p.json", parca([soru(1, soru="Slayta göre mitokondri nedir?")]))
        r = self.k("kontrol", p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("gönderme", r.stdout)

    def test_ortuk_gonderme_yakalanir(self):
        p = self.yaz("p.json", parca([soru(1, soru="Okyanuslar için verilen değer neyi ifade eder?")]))
        self.assertEqual(self.k("kontrol", p).returncode, 1)
        p = self.yaz("p.json", parca([soru(1, soru="Aşağıda verilenlerden hangisi doğrudur?")]))
        self.assertEqual(self.k("kontrol", p).returncode, 0, "meşru soru kökü engellenmemeli")

    def test_sik_sayisi_ayardan(self):
        p = self.yaz("p.json", parca([soru(1, sik=4)]))
        self.assertEqual(self.k("kontrol", p).returncode, 1)
        self.assertEqual(self.k("kontrol", p, "--sik", "4").returncode, 0)

    def test_format_disi_tip(self):
        p = self.yaz("p.json", parca([soru(1, tip="klasik")]))
        r = self.k("kontrol", p, "--format", "test")
        self.assertEqual(r.returncode, 1)

    def test_sayfa_aralik_disi(self):
        p = self.yaz("p.json", parca([soru(1, sayfa=40)]))
        self.assertEqual(self.k("kontrol", p).returncode, 1)

    def test_bosluk_ve_dogru_yanlis(self):
        ss = [soru(1, tip="bosluk"), soru(2, tip="dogru_yanlis"), soru(3, tip="klasik")]
        p = self.yaz("p.json", parca(ss, format="karma-klasik"))
        self.assertEqual(self.k("kontrol", p, "--format", "karma-klasik").returncode, 0)
        ss[1]["cevap"] = "Evet"
        p = self.yaz("p.json", parca(ss, format="karma-klasik"))
        self.assertEqual(self.k("kontrol", p, "--format", "karma-klasik").returncode, 1)

    def test_ince_sayfa_azaltmasi(self):
        # 10 beklenen (2/5/3), en_az 6; 8 soru dagilim(8) = kolay 2, orta 3, zor 2 (±1)
        z = ["kolay"] * 2 + ["orta"] * 4 + ["zor"] * 2
        ss = [soru(i + 1, zz) for i, zz in enumerate(z)]
        bek = "kolay=2,orta=5,zor=3"
        p = self.yaz("p.json", parca(ss))
        r = self.k("kontrol", p, "--beklenen", bek, "--en-az", "6")
        self.assertEqual(r.returncode, 1, "gerekçesiz azaltma reddedilmeli")
        p = self.yaz("p.json", parca(ss, azaltma_gerekcesi="s. 3-9 yalnız başlık slaytı"))
        r = self.k("kontrol", p, "--beklenen", bek, "--en-az", "6")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("UYARI", r.stdout)
        r = self.k("kontrol", p, "--beklenen", bek, "--en-az", "9")
        self.assertEqual(r.returncode, 1, "alt sınırın altı reddedilmeli")
        r = self.k("kontrol", p, "--beklenen", bek)
        self.assertEqual(r.returncode, 1, "--en-az yoksa azaltma yok")


class Akis(Temel):
    def test_onayla_yerlestir_birlestir(self):
        ss = [soru(1, "kolay"), soru(2, "orta")]
        p = self.yaz("parca-1.json", parca(ss))
        dv = self.yaz("dogrulama-1-tur1.json", {"sonuclar": [
            {"id": "p1-q01", "durum": "PASS"},
            {"id": "p1-q02", "durum": "FAIL", "geri_bildirim": "B de doğru"}]})
        r = self.k("onayla", p, dv)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(r.stdout)["kalan"][0]["id"], "p1-q02")

        cikti = self.d / "kitap.json"
        r = self.k("birlestir", self.d, "--cikti", cikti, "--baslik", "B", "--format", "test", "--pdf", "ders.pdf")
        self.assertEqual(r.returncode, 2, "doğrulanmamış soru varken birleştirme reddedilmeli")

        yanlis = self.yaz("d-bad.json", {"sorular": [soru(2, "zor")]})
        self.assertEqual(self.k("yerlestir", p, yanlis).returncode, 2, "zorluk değişemez")
        kilitli = self.yaz("d-lock.json", {"sorular": [soru(1, "kolay")]})
        self.assertEqual(self.k("yerlestir", p, kilitli).returncode, 2, "doğrulanmış soru değişemez")

        yeni = self.yaz("duzeltme-1-tur1.json", {"sorular": [soru(2, "orta", soru="Yeni soru nedir?")]})
        self.assertEqual(self.k("yerlestir", p, yeni).returncode, 0)
        dv2 = self.yaz("dogrulama-1-tur2.json", {"sonuclar": [{"id": "p1-q02", "durum": "PASS"}]})
        self.assertEqual(self.k("onayla", p, dv2).returncode, 0)
        veri = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(veri["sorular"][1]["tur"], 2)

        r = self.k("birlestir", self.d, "--cikti", cikti, "--baslik", "B", "--format", "test", "--pdf", "ders.pdf")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(json.loads(cikti.read_text(encoding="utf-8"))["sorular"]), 2)

    def test_render_tarayici_yoksa_html(self):
        ss = [dict(soru(1, "kolay"), dogrulandi=True)]
        kitap = self.yaz("kitap.json", {"baslik": "Deneme", "kaynak_pdf": "ders.pdf", "format": "test",
                                         "tarih": "2026-11-05", "sorular": ss})
        self.env["PATH"] = "/nonexistent"
        self.env["SORU_KITABI_CHROME"] = ""
        if sys.platform == "darwin" and any(Path(p).exists() for p in [
                "/Applications/Google Chrome.app", "/Applications/Chromium.app", "/Applications/Microsoft Edge.app"]):
            self.skipTest("bu makinede tarayıcı kurulu; HTML yedek yolu test edilemiyor")
        r = self.k("render", kitap, self.d / "deneme-sorular.pdf")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertTrue((self.d / "deneme-sorular.html").exists())

    def test_render_dogrulanmamis_reddedilir(self):
        kitap = self.yaz("kitap.json", {"baslik": "D", "format": "test", "sorular": [soru(1)]})
        r = self.k("render", kitap, self.d / "x.pdf")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
