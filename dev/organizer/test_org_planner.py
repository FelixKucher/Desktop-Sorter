# Tests für org_planner.py – realistischer, unordentlicher „Dokumente“-Ordner (DE + EN).
# Aufruf: python test_org_planner.py   (Exit-Code 0 = alles bestanden)
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import unittest
import zipfile
import zlib
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import org_planner  # noqa: E402
from org_core import ORG_CATEGORIES, ORG_JUNK, ORG_KEEP, ORG_PROTECT, ORG_REVIEW  # noqa: E402
from org_planner import (OrgMove, OrgPlan, org_analyze, org_apply_moves,  # noqa: E402
                         org_backup, org_trash, org_undo)

NOW = time.time()
DAY = 86400.0
PROGRAMS = ["Steam", "Mozilla Firefox (x64 de)", "VLC media player"]


# ---------------------------------------------------------------------------
# Datei-Bausteine
# ---------------------------------------------------------------------------
def make_pdf(text: str) -> bytes:
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    lines = safe.split("\n")
    ops = b"BT /F1 11 Tf 60 780 Td "
    for i, ln in enumerate(lines):
        if i:
            ops += b"0 -14 Td "
        ops += b"(" + ln.encode("cp1252", "replace") + b") Tj "
    ops += b"ET"
    data = zlib.compress(ops)
    stream = b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + \
        b"\nendstream"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        stream,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for i, body in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return bytes(out)


def make_docx(text: str) -> bytes:
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"><Override PartName="/word/document.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.'
                   'wordprocessingml.document.main+xml"/></Types>')
        z.writestr("word/document.xml",
                   '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.'
                   'openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
                   + text.replace("&", "&amp;").replace("<", "&lt;") +
                   '</w:t></w:r></w:p></w:body></w:document>')
    return buf.getvalue()


def _pack_val(E, typ, val) -> bytes:
    if typ == 2:
        return val.encode() + b"\x00"
    if typ == 3:
        return struct.pack(E + "%dH" % len(val), *val)
    if typ == 4:
        return struct.pack(E + "%dI" % len(val), *val)
    if typ == 5:
        flat = [x for pair in val for x in pair]
        return struct.pack(E + "%dI" % len(flat), *flat)
    raise ValueError(typ)


def build_tiff(ifd0, exif=None, gps=None) -> bytes:
    E = "<"
    ifd0 = list(ifd0)
    blocks = [ifd0]
    if exif is not None:
        ifd0.append((0x8769, 4, [0]))
        blocks.append(list(exif))
    if gps is not None:
        ifd0.append((0x8825, 4, [0]))
        blocks.append(list(gps))
    sizes = [2 + 12 * len(b) + 4 for b in blocks]
    offs = [8]
    for s in sizes[:-1]:
        offs.append(offs[-1] + s)
    k = 1
    for i, (tag, typ, val) in enumerate(ifd0):
        if tag in (0x8769, 0x8825) and val == [0]:
            ifd0[i] = (tag, typ, [offs[k]])
            k += 1
    data_off = offs[-1] + sizes[-1]
    data = bytearray()
    out = bytearray(b"II*\x00" + struct.pack(E + "I", 8))
    for b in blocks:
        ent = bytearray(struct.pack(E + "H", len(b)))
        for tag, typ, val in sorted(b, key=lambda t: t[0]):
            raw = _pack_val(E, typ, val)
            size = {2: 1, 3: 2, 4: 4, 5: 8}[typ]
            cnt = len(raw) // size
            if len(raw) <= 4:
                fld = raw.ljust(4, b"\x00")
            else:
                fld = struct.pack(E + "I", data_off + len(data))
                data += raw
                if len(data) % 2:
                    data += b"\x00"
            ent += struct.pack(E + "HHI", tag, typ, cnt) + fld
        ent += struct.pack(E + "I", 0)
        out += ent
    out += data
    return bytes(out)


def _seg(marker: int, payload: bytes) -> bytes:
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


def make_jpeg(dt=None, gps=None, make="Canon", model="Canon EOS 80D", w=4000, h=3000,
              salt: str = "") -> bytes:
    tiff = b""
    if dt or make:
        ifd0 = []
        if make:
            ifd0 += [(0x010F, 2, make), (0x0110, 2, model)]
        exif = [(0x9003, 2, dt)] if dt else None
        g = None
        if gps:
            lat, lon = gps
            g = [(1, 2, "N" if lat >= 0 else "S"),
                 (2, 5, [(int(abs(lat) * 10000), 10000), (0, 1), (0, 1)]),
                 (3, 2, "E" if lon >= 0 else "W"),
                 (4, 5, [(int(abs(lon) * 10000), 10000), (0, 1), (0, 1)])]
        tiff = build_tiff(ifd0, exif=exif, gps=g)
    out = b"\xff\xd8" + _seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    if tiff:
        out += _seg(0xE1, b"Exif\x00\x00" + tiff)
    out += _seg(0xFE, ("salt:" + salt).encode("utf-8"))
    out += _seg(0xDB, b"\x00" + bytes(range(64)))
    out += _seg(0xC0, b"\x08" + struct.pack(">HH", h, w) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    out += _seg(0xDA, b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00") + b"\x12\x34\x56" * 40
    return out + b"\xff\xd9"


def _png_chunk(ctype: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data))


def make_png(w: int, h: int, salt: str = "") -> bytes:
    out = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    out += _png_chunk(b"tEXt", b"Comment\x00" + salt.encode("utf-8"))
    out += _png_chunk(b"IDAT", zlib.compress(b"\x00" * 64))
    return out + _png_chunk(b"IEND", b"")


def write(root: Path, rel: str, data, age_days: float = 30.0) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    p.write_bytes(data)
    t = NOW - age_days * DAY
    os.utime(str(p), (t, t))
    return p


ROME = (41.9028, 12.4964)
MALLORCA = (39.5696, 2.6502)
BERLIN = (52.5200, 13.4050)


def build_tree(base: Path, name: str = "Dokumente") -> Path:
    """Baut einen unordentlichen Dokumente-Ordner (> 80 Dateien)."""
    root = base / name
    root.mkdir(parents=True)
    W = lambda rel, data, age=30.0: write(root, rel, data, age)  # noqa: E731
    pdf = lambda rel, text, age=30.0: W(rel, make_pdf(text + "\n" + rel), age)  # noqa: E731

    # --- Bewerbung / Lebenslauf (inkl. Versionen und Duplikate) ---
    cv = make_pdf("Lebenslauf\nMax Mustermann\nBerufserfahrung\nAusbildung")
    W("Lebenslauf_Max_Mustermann.pdf", cv, 400)
    W("Downloads/Lebenslauf_Max_Mustermann (1).pdf", cv, 100)
    W("Alt/Bewerbung 2020/Lebenslauf_Max_Mustermann.pdf", cv, 200)
    W("Lebenslauf_final.docx", make_docx("Lebenslauf Version eins"), 300)
    W("Lebenslauf_final_v2.docx", make_docx("Lebenslauf Version zwei mit mehr Text"), 200)
    W("Lebenslauf_final_v2 (1).docx", make_docx("Lebenslauf Version drei, aktuell"), 100)
    W("~$benslauf_final_v2.docx", b"\x00" * 162, 50)
    W("Bewerbung_Anschreiben_Siemens.docx", make_docx("Sehr geehrte Damen und Herren, "
                                                     "hiermit bewerbe ich mich"), 90)
    pdf("CV_English_2023.pdf", "Curriculum Vitae\nWork experience\nEducation", 500)
    W("Alt/Bewerbung 2020/Anschreiben_alt.docx", make_docx("Anschreiben 2020"), 900)

    # --- Zeugnisse & Urkunden ---
    pdf("Arbeitszeugnis_Firma_X.pdf", "Arbeitszeugnis\nHerr Mustermann war bei uns tätig", 700)
    pdf("scan0001.pdf", "Zeugnis der allgemeinen Hochschulreife\nAbiturzeugnis\n"
                        "Gesamtnote 1,7\nSchuljahr 2012", 800)
    pdf("Geburtsurkunde.pdf", "Geburtsurkunde\nStandesamt Berlin", 1500)
    neutral = make_pdf("Seite 1 von 1\n\n")
    W("scan_0042.pdf", neutral, 900)                     # älter, oberste Ebene
    W("Zeugnisse/Abiturzeugnis.pdf", neutral, 100)       # gleiche Bytes, wichtig
    pdf("Zeugnisse/Bachelorzeugnis.pdf", "Bachelorzeugnis\nHochschule", 600)

    # --- Rechnungen, Gehalt, Steuer, Bank, Verträge, Versicherung ---
    pdf("Rechnung_Amazon_2021.pdf", "Rechnung\nRechnungsnummer 123\nBetrag 49,99 EUR", 1000)
    pdf("Rechnung_Telekom_2022-03.pdf", "Rechnung März 2022", 900)
    pdf("Invoice_2023_0042.pdf", "Invoice\nTotal due 120 USD", 600)
    pdf("Rechnung_IKEA_2024.pdf", "Rechnung IKEA", 200)
    pdf("Downloads/Rechnung_Otto_2022.pdf", "Rechnung Otto", 800)
    pdf("Gehaltsabrechnung_2023_01.pdf", "Entgeltabrechnung Januar 2023\nBrutto Netto", 600)
    pdf("Gehaltsabrechnung_2023_02.pdf", "Entgeltabrechnung Februar 2023\nBrutto Netto", 570)
    pdf("Payslip_March_2024.pdf", "Payslip March 2024\nGross Net", 200)
    pdf("Steuerbescheid_2022.pdf", "Bescheid für 2022 über Einkommensteuer\nFinanzamt", 300)
    pdf("Mietvertrag_Wohnung.pdf", "Mietvertrag\nVermieter Mieter", 1200)
    pdf("Arbeitsvertrag_2021.pdf", "Arbeitsvertrag\nArbeitgeber Arbeitnehmer", 1000)
    pdf("Kfz-Versicherung_Police.pdf", "Versicherungsschein\nKfz-Haftpflicht", 400)
    for m in range(1, 5):
        pdf("Kontoauszug_2023_%02d.pdf" % m, "Kontoauszug Nr. %d\nSaldo" % m, 600 - m * 30)
    pdf("Downloads/handbuch_drucker.pdf", "Bedienungsanleitung Drucker", 700)
    W("Notizen.txt", "Milch, Brot, Eier – Einkauf am Samstag", 20)
    W("Einkaufsliste.txt", "Tomaten\nNudeln\nKäse", 15)
    W("Rezept Apfelkuchen.docx", make_docx("Apfelkuchen: 500 g Mehl, 3 Eier"), 400)

    # --- Projekt (Git) mit Duplikaten darin ---
    W("Projekte/meinprojekt/.git/HEAD", "ref: refs/heads/main\n", 100)
    W("Projekte/meinprojekt/.git/config", "[core]\n", 100)
    W("Projekte/meinprojekt/README.md", "# Mein Projekt\n", 100)
    W("Projekte/meinprojekt/src/main.py", "print('hallo')\n", 100)
    W("Projekte/meinprojekt/src/utils.py", "def f():\n    return 1\n", 100)
    W("Projekte/meinprojekt/data/messung.csv", "a,b\n1,2\n3,4\n", 100)
    W("Projekte/meinprojekt/data/messung_kopie.csv", "a,b\n1,2\n3,4\n", 90)
    W("Projekte/meinprojekt/Lebenslauf_Vorlage.docx", make_docx("Vorlage Lebenslauf"), 100)
    hosting = make_pdf("Rechnung Hosting 2021\nServer")
    W("Projekte/meinprojekt/docs/Rechnung_Hosting_2021.pdf", hosting, 500)
    W("Rechnung_Hosting_2021.pdf", hosting, 900)          # lose Kopie (älter!)

    # --- Fotos: zwei Urlaube + Zuhause ---
    for i in range(8):
        t = time.mktime((2023, 7, 10, 9 + i * 10, 0, 0, 0, 0, -1))
        dt = time.strftime("%Y:%m:%d %H:%M:%S", time.localtime(t))
        W("IMG_%04d.JPG" % (1001 + i), make_jpeg(dt, ROME, salt="rom%d" % i), 400)
    for i in range(7):
        t = time.mktime((2024, 8, 3, 10 + i * 8, 0, 0, 0, 0, -1))
        dt = time.strftime("%Y:%m:%d %H:%M:%S", time.localtime(t))
        W("Urlaub Mallorca/IMG_%04d.JPG" % (2001 + i), make_jpeg(dt, MALLORCA, salt="mal%d" % i),
          60)
    homes = [(2023, 3, 1), (2023, 4, 15), (2023, 5, 20), (2023, 9, 2), (2023, 10, 10),
             (2023, 11, 11), (2024, 1, 6), (2024, 2, 14)]
    for i, (y, mo, d) in enumerate(homes):
        W("IMG_%04d.JPG" % (3001 + i),
          make_jpeg("%04d:%02d:%02d 15:00:00" % (y, mo, d), BERLIN, salt="home%d" % i), 300)
    # Kopie eines Urlaubsfotos (neuer) in einem Sicherungsordner
    (root / "Handy Backup").mkdir()
    shutil.copyfile(str(root / "IMG_1001.JPG"), str(root / "Handy Backup" / "IMG_1001.JPG"))
    os.utime(str(root / "Handy Backup" / "IMG_1001.JPG"), (NOW - 10 * DAY, NOW - 10 * DAY))
    # Ausweis-Scans (Handyfoto mit EXIF) – Dokument, nicht Urlaubsfoto
    W("Personalausweis.jpg", make_jpeg("2022:05:05 10:00:00", BERLIN, "Apple", "iPhone 13",
                                       salt="perso"), 500)
    W("Personalausweis_Rueckseite.jpg", make_jpeg("2022:05:05 10:01:00", BERLIN, "Apple",
                                                  "iPhone 13", salt="perso2"), 500)
    W("Reisepass_scan.png", make_png(1240, 1754, "pass"), 500)
    # Chat-Bilder & Bildschirmfotos
    W("IMG-20240512-WA0001.jpg", make_jpeg(None, None, make="", salt="wa1"), 100)
    W("IMG-20240513-WA0002.jpg", make_jpeg(None, None, make="", salt="wa2"), 100)
    W("WhatsApp Image 2024-05-14 at 10.00.00.jpeg", make_jpeg(None, None, make="", salt="wa3"), 100)
    W("Screenshot 2024-03-01 101010.png", make_png(1920, 1080, "s1"), 200)
    W("Bildschirmfoto 2023-11-11 um 10.00.00.png", make_png(2560, 1440, "s2"), 300)
    W("image.png", make_png(1920, 1080, "s3"), 250)

    # --- Installer, Abbilder, Reste ---
    W("SteamSetup.exe", b"MZ" + b"steam" * 50, 200)
    W("Firefox Setup 128.0.exe", b"MZ" + b"firefox" * 50, 200)
    W("vlc-3.0.20-win64.exe", b"MZ" + b"vlc" * 80, 200)
    W("GIMP-2.10.38-setup.exe", b"MZ" + b"gimp" * 60, 20)
    W("ubuntu-24.04-desktop-amd64.iso", b"\x00" * 10 + b"CD001" + b"iso" * 40, 300)
    W(".~lock.Mietvertrag.odt#", "Max,PC,01.01.2024", 50)
    W("~WRL0001.tmp", b"\x01" * 300, 50)
    W("Film.mp4.crdownload", b"\x02" * 500, 40)
    W("Neues Textdokument.txt", b"", 30)
    W("Thumbs.db", b"\x03" * 64, 30)

    # --- Sonstiges ---
    W("Song.mp3", b"ID3" + b"\x04" * 300, 300)
    W("Archiv.zip", b"PK\x05\x06" + b"\x00" * 18, 300)
    W("Modell.stl", "solid x\nendsolid x\n", 300)
    W("Artikel.html", "<html><body>Artikel</body></html>", 300)
    W("Artikel_files/style.css", "body{}", 300)
    W("Spiele/MeinSpiel/MeinSpiel.exe", b"MZ" + b"game" * 40, 300)
    W("Spiele/MeinSpiel/engine.dll", b"MZ" + b"dll" * 40, 300)
    W("Spiele/MeinSpiel/data/level1.json", '{"level": 1}', 300)
    W("Downloads/Steuererklaerung_2021_Belege.pdf",
      make_pdf("Einkommensteuererklärung 2021\nFinanzamt Belege"), 700)
    (root / "Neuer Ordner").mkdir()
    (root / "Alt" / "leer").mkdir(parents=True)
    return root


def snapshot(root: Path) -> dict:
    out = {}
    for dp, dns, fns in os.walk(str(root)):
        rel_d = os.path.relpath(dp, str(root)).replace(os.sep, "/")
        out["D:" + rel_d] = None
        for fn in fns:
            p = os.path.join(dp, fn)
            with open(p, "rb") as fh:
                out["F:" + os.path.relpath(p, str(root)).replace(os.sep, "/")] = \
                    hashlib.sha256(fh.read()).hexdigest()
    return out


def by_rel(plan: OrgPlan) -> dict:
    return {f.rel: f for f in plan.files}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class PlannerAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="orgplan_"))
        cls.root = build_tree(cls.tmp)
        cls.stages = []
        cls.before = snapshot(cls.root)
        cls.plan = org_analyze(cls.root, programs=PROGRAMS, now=NOW,
                               progress=lambda s, d, t: cls.stages.append(s))
        cls.f = by_rel(cls.plan)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def test_tree_size_and_no_changes(self):
        self.assertGreaterEqual(len(self.plan.files), 80)
        self.assertEqual(snapshot(self.root), self.before, "Analyse darf nichts verändern")
        self.assertNotIn("error", self.plan.stats)

    def test_progress_stages(self):
        for st in org_planner.ORG_PLAN_STAGES:
            self.assertIn(st, self.stages)

    def test_no_protected_file_in_junk(self):
        for f in self.plan.junk:
            self.assertEqual(f.tier, ORG_JUNK)
            orig = f.meta.get("orig_category", f.category)
            if ORG_CATEGORIES.get(orig, {}).get("tier") == ORG_PROTECT or \
                    f.meta.get("orig_tier") == ORG_PROTECT:
                self.assertTrue(f.dup_of, f.rel)
                keeper = self.f[f.dup_of]
                self.assertNotEqual(keeper.tier, ORG_JUNK)
                self.assertTrue(keeper.path.is_file())
        for f in self.plan.files:
            if f.tier == ORG_JUNK:
                self.assertIn(f.category, ("temp", "duplicate", "installer"), f.rel)
        for f in self.plan.protect:
            self.assertNotIn(f, self.plan.junk)

    def test_classification(self):
        f = self.f
        exp = {
            "Lebenslauf_Max_Mustermann.pdf": ("cv", ORG_PROTECT),
            "CV_English_2023.pdf": ("cv", ORG_PROTECT),
            "Bewerbung_Anschreiben_Siemens.docx": ("cv", ORG_PROTECT),
            "Arbeitszeugnis_Firma_X.pdf": ("certificate", ORG_PROTECT),
            "scan0001.pdf": ("certificate", ORG_PROTECT),
            "Geburtsurkunde.pdf": ("certificate", ORG_PROTECT),
            "Steuerbescheid_2022.pdf": ("tax", ORG_PROTECT),
            "Gehaltsabrechnung_2023_01.pdf": ("payslip", ORG_PROTECT),
            "Payslip_March_2024.pdf": ("payslip", ORG_PROTECT),
            "Mietvertrag_Wohnung.pdf": ("contract", ORG_PROTECT),
            "Kfz-Versicherung_Police.pdf": ("insurance", ORG_PROTECT),
            "Personalausweis.jpg": ("id", ORG_PROTECT),
            "Personalausweis_Rueckseite.jpg": ("id", ORG_PROTECT),
            "Reisepass_scan.png": ("id", ORG_PROTECT),
            "Rechnung_Amazon_2021.pdf": ("invoice", ORG_KEEP),
            "Kontoauszug_2023_01.pdf": ("bank", ORG_KEEP),
            "IMG_1001.JPG": ("photo_personal", ORG_PROTECT),
            "IMG_3001.JPG": ("photo_personal", ORG_PROTECT),
            "IMG-20240512-WA0001.jpg": ("photo_chat", ORG_REVIEW),
            "WhatsApp Image 2024-05-14 at 10.00.00.jpeg": ("photo_chat", ORG_REVIEW),
            "Screenshot 2024-03-01 101010.png": ("screenshot", ORG_REVIEW),
            "image.png": ("screenshot", ORG_REVIEW),
            "Notizen.txt": ("document", ORG_REVIEW),
            "Song.mp3": ("audio", ORG_KEEP),
            "Archiv.zip": ("archive", ORG_REVIEW),
            "Modell.stl": ("cad", ORG_KEEP),
            "GIMP-2.10.38-setup.exe": ("installer", ORG_REVIEW),
            "ubuntu-24.04-desktop-amd64.iso": ("installer", ORG_REVIEW),
        }
        for rel, (cat, tier) in exp.items():
            self.assertEqual((f[rel].category, f[rel].tier), (cat, tier), rel)
        self.assertIn("Erkannt: Zeugnisse & Urkunden (Inhalt: zeugnis, abiturzeugnis)",
                      f["scan0001.pdf"].reasons)
        self.assertIn("Erkannt: Lebenslauf & Bewerbung (Dateiname: lebenslauf; "
                      "Inhalt: berufserfahrung)", f["Lebenslauf_Max_Mustermann.pdf"].reasons)
        self.assertTrue(any("Dateiname" in r for r in f["Geburtsurkunde.pdf"].reasons))
        self.assertEqual(f["Steuerbescheid_2022.pdf"].year, 2022)

    def test_junk(self):
        f = self.f
        for rel in ("SteamSetup.exe", "Firefox Setup 128.0.exe", "vlc-3.0.20-win64.exe",
                    "~$benslauf_final_v2.docx", ".~lock.Mietvertrag.odt#", "~WRL0001.tmp",
                    "Film.mp4.crdownload", "Neues Textdokument.txt", "Thumbs.db"):
            self.assertEqual(f[rel].tier, ORG_JUNK, rel)
            self.assertIn(f[rel], self.plan.junk)
        self.assertTrue(any("bereits installiert" in r for r in f["SteamSetup.exe"].reasons))
        self.assertGreater(self.plan.stats["junk_bytes"], 0)

    def test_project_untouched(self):
        self.assertIn("Projekte/meinprojekt", self.plan.projects)
        proj = [x for x in self.plan.files if x.rel.startswith("Projekte/meinprojekt/")]
        self.assertGreaterEqual(len(proj), 6)
        moved = {id(m.file) for m in self.plan.moves}
        for x in proj:
            self.assertTrue(x.in_project, x.rel)
            self.assertEqual((x.category, x.tier, x.target), ("code", ORG_KEEP, ""))
            self.assertFalse(x.dup_of)
            self.assertFalse(x.version_group)
            self.assertNotIn(id(x), moved)
            self.assertNotIn(x, self.plan.junk)
        # Duplikate im Projekt werden nicht als Gruppe gemeldet
        for g in self.plan.dup_groups:
            self.assertFalse(all(m.in_project for m in g))

    def test_duplicates_and_keeper(self):
        f = self.f
        # geschützte Datei wird behalten, obwohl die Kopie älter + flacher ist
        self.assertEqual(f["scan_0042.pdf"].dup_of, "Zeugnisse/Abiturzeugnis.pdf")
        self.assertEqual(f["Zeugnisse/Abiturzeugnis.pdf"].tier, ORG_PROTECT)
        self.assertFalse(f["Zeugnisse/Abiturzeugnis.pdf"].dup_of)
        for rel in ("Downloads/Lebenslauf_Max_Mustermann (1).pdf",
                    "Alt/Bewerbung 2020/Lebenslauf_Max_Mustermann.pdf"):
            self.assertEqual(f[rel].dup_of, "Lebenslauf_Max_Mustermann.pdf")
            self.assertEqual(f[rel].meta.get("orig_category"), "cv")
        self.assertEqual(f["Lebenslauf_Max_Mustermann.pdf"].tier, ORG_PROTECT)
        self.assertEqual(f["Handy Backup/IMG_1001.JPG"].dup_of, "IMG_1001.JPG")
        self.assertEqual(f["Rechnung_Hosting_2021.pdf"].dup_of,
                         "Projekte/meinprojekt/docs/Rechnung_Hosting_2021.pdf")
        self.assertFalse(f["Projekte/meinprojekt/data/messung_kopie.csv"].dup_of)
        keepers = [g[0].rel for g in self.plan.dup_groups]
        self.assertIn("Zeugnisse/Abiturzeugnis.pdf", keepers)
        self.assertGreater(self.plan.stats["dup_bytes"], 0)

    def test_versions(self):
        groups = [g for k, g in self.plan.versions.items() if "lebenslauf" in k.lower()]
        self.assertEqual(len(groups), 1, self.plan.versions.keys())
        g = groups[0]
        self.assertEqual(sorted(m.rel for m in g),
                         ["Lebenslauf_final.docx", "Lebenslauf_final_v2 (1).docx",
                          "Lebenslauf_final_v2.docx"])
        latest = [m for m in g if m.version_latest]
        self.assertEqual([m.rel for m in latest], ["Lebenslauf_final_v2 (1).docx"])
        mv = {m.file.rel: m for m in self.plan.moves}
        for m in g:
            if not m.version_latest:
                self.assertEqual(m.tier, ORG_REVIEW)
                self.assertNotEqual(m.tier, ORG_JUNK)
            self.assertEqual(mv[m.rel].dest_rel, "Bewerbung")   # bleiben nebeneinander
        self.assertNotIn("~$benslauf_final_v2.docx", [m.rel for m in g])

    def test_photo_events(self):
        f = self.f
        self.assertEqual(f["IMG_1001.JPG"].event, "2023-07-10 bis 07-13 – Reise")
        self.assertEqual(f["Urlaub Mallorca/IMG_2001.JPG"].event, "2024-08-03 bis 08-05 – Reise")
        self.assertEqual(f["IMG_3001.JPG"].event, "2023-03 März")
        mv = {m.file.rel: m for m in self.plan.moves}
        self.assertEqual(mv["IMG_1001.JPG"].dest_rel,
                         "Bilder/Fotos/2023/2023-07-10 bis 07-13 – Reise")
        self.assertEqual(mv["IMG_3001.JPG"].dest_rel, "Bilder/Fotos/2023/2023-03 März")
        self.assertTrue(mv["IMG_1001.JPG"].checked)

    def test_moves_depth0_and_targets(self):
        self.assertTrue(self.plan.moves)
        for m in self.plan.moves:
            self.assertEqual(m.file.depth, 0, m.file.rel)
            self.assertNotEqual(m.file.tier, ORG_JUNK)
            self.assertFalse(m.file.in_project)
            self.assertFalse(m.dest_rel.startswith("Dokumente/"), m.dest_rel)  # Wurzel-Alias
        mv = {m.file.rel: m for m in self.plan.moves}
        self.assertEqual(mv["Lebenslauf_Max_Mustermann.pdf"].dest_rel, "Bewerbung")
        self.assertTrue(mv["Lebenslauf_Max_Mustermann.pdf"].checked)
        self.assertEqual(mv["Steuerbescheid_2022.pdf"].dest_rel, "Steuer/2022")
        self.assertEqual(mv["Gehaltsabrechnung_2023_01.pdf"].dest_rel, "Gehalt & Rente/2023")
        self.assertEqual(mv["Rechnung_Amazon_2021.pdf"].dest_rel, "Rechnungen/2021")
        self.assertEqual(mv["Personalausweis.jpg"].dest_rel, "Ausweise")
        self.assertEqual(mv["Screenshot 2024-03-01 101010.png"].dest_rel, "Bilder/Bildschirmfotos")
        self.assertTrue(mv["Screenshot 2024-03-01 101010.png"].checked)
        self.assertFalse(mv["Notizen.txt"].checked)
        self.assertFalse(mv["GIMP-2.10.38-setup.exe"].checked)
        self.assertNotIn("Artikel.html", mv)                  # Webseite bleibt bei Ordner
        self.assertNotIn("Zeugnisse/Abiturzeugnis.pdf", mv)   # Unterordner (Standard)
        self.assertEqual(self.plan.stats["move_count"], len(self.plan.moves))

    def test_empty_dirs(self):
        names = {Path(d).name for d in self.plan.empty_dirs}
        self.assertIn("Neuer Ordner", names)
        self.assertIn("leer", names)

    def test_stats(self):
        st = self.plan.stats
        for k in ("files", "bytes", "junk_bytes", "dup_bytes", "protect_count", "move_count",
                  "review_count", "seconds"):
            self.assertIn(k, st)
        self.assertEqual(st["files"], len(self.plan.files))
        self.assertEqual(st["protect_count"], len(self.plan.protect))


class PlannerOptions(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="orgplan_"))

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_reorganize_subfolders(self):
        root = build_tree(self.tmp)
        plan = org_analyze(root, reorganize_subfolders=True, programs=PROGRAMS, now=NOW)
        mv = {m.file.rel: m for m in plan.moves}
        self.assertIn("Urlaub Mallorca/IMG_2001.JPG", mv)
        self.assertEqual(mv["Urlaub Mallorca/IMG_2001.JPG"].dest_rel,
                         "Bilder/Fotos/2024/2024-08-03 bis 08-05 – Reise")
        self.assertIn("Downloads/Rechnung_Otto_2022.pdf", mv)
        for rel in mv:
            self.assertFalse(rel.startswith("Projekte/meinprojekt/"), rel)
            self.assertFalse(rel.startswith("Spiele/MeinSpiel/"), rel)   # Programmordner
            self.assertFalse(rel.startswith("Artikel_files/"), rel)
        # bereits richtig einsortiert? Zeugnisse/… → Zielordner „Zeugnisse & Urkunden“
        self.assertEqual(mv["Zeugnisse/Bachelorzeugnis.pdf"].dest_rel, "Zeugnisse & Urkunden")

    def test_already_in_place_not_moved(self):
        root = self.tmp / "Ordner"
        write(root, "Dokumente/Bewerbung/Lebenslauf.pdf", make_pdf("Lebenslauf"), 10)
        write(root, "Dokumente/Bewerbung/Unter/Lebenslauf_alt_2019.pdf", make_pdf("Lebenslauf 2"))
        write(root, "Lebenslauf_neu.pdf", make_pdf("Lebenslauf 3"))
        plan = org_analyze(root, reorganize_subfolders=True, programs=[], now=NOW)
        mv = {m.file.rel: m.dest_rel for m in plan.moves}
        self.assertEqual(mv, {"Lebenslauf_neu.pdf": "Dokumente/Bewerbung"})

    def test_protected_paths(self):
        root = build_tree(self.tmp)
        prot = {str(root / "Film.mp4.crdownload"),
                str(root / "Downloads" / "Lebenslauf_Max_Mustermann (1).pdf"),
                str(root / "Alt")}
        plan = org_analyze(root, programs=PROGRAMS, now=NOW, protected_paths=prot)
        f = by_rel(plan)
        for rel in ("Film.mp4.crdownload", "Downloads/Lebenslauf_Max_Mustermann (1).pdf",
                    "Alt/Bewerbung 2020/Lebenslauf_Max_Mustermann.pdf",
                    "Alt/Bewerbung 2020/Anschreiben_alt.docx"):
            self.assertEqual(f[rel].tier, ORG_PROTECT, rel)
            self.assertNotIn(f[rel], plan.junk)
            self.assertFalse(f[rel].dup_of)
        n, fails = org_trash([f["Film.mp4.crdownload"]], lambda p: True)
        self.assertEqual(n, 0)

    def test_root_alias_and_other_root(self):
        root = build_tree(self.tmp, "Durcheinander")
        plan = org_analyze(root, programs=PROGRAMS, now=NOW, extract_text=False)
        mv = {m.file.rel: m.dest_rel for m in plan.moves}
        self.assertEqual(mv["Lebenslauf_Max_Mustermann.pdf"], "Dokumente/Bewerbung")
        self.assertEqual(plan.stats.get("text_read"), 0)
        # ohne Text: scan0001.pdf nicht mehr als Zeugnis erkannt, aber nie Müll
        self.assertNotEqual(by_rel(plan)["scan0001.pdf"].tier, ORG_JUNK)

    def test_non_recursive(self):
        root = build_tree(self.tmp)
        plan = org_analyze(root, recursive=False, programs=PROGRAMS, now=NOW)
        self.assertTrue(all(f.depth == 0 for f in plan.files))
        for d in plan.empty_dirs:
            self.assertEqual(Path(d).parent.resolve(), root.resolve())

    def test_root_is_project(self):
        root = self.tmp / "repo"
        write(root, ".git/HEAD", "ref")
        write(root, "Lebenslauf.pdf", make_pdf("Lebenslauf"))
        write(root, "a.tmp", "x", 30)
        plan = org_analyze(root, programs=[], now=NOW)
        self.assertTrue(plan.stats.get("root_is_project"))
        self.assertEqual(plan.moves, [])
        self.assertEqual(plan.junk, [])
        self.assertTrue(all(f.in_project for f in plan.files))

    def test_cancel(self):
        root = build_tree(self.tmp)
        calls = {"n": 0}

        def cancelled():
            calls["n"] += 1
            return calls["n"] > 3
        plan = org_analyze(root, programs=PROGRAMS, now=NOW, cancelled=cancelled)
        self.assertTrue(plan.stats.get("cancelled"))
        self.assertEqual(plan.moves, [])
        self.assertEqual(plan.junk, [])

    def test_bad_inputs_never_raise(self):
        plan = org_analyze(self.tmp / "gibtsnicht")
        self.assertIn("error", plan.stats)
        self.assertEqual(plan.moves, [])
        plan = org_analyze(None)                      # type: ignore[arg-type]
        self.assertIsInstance(plan, OrgPlan)
        f = self.tmp / "datei.txt"
        f.write_text("x")
        self.assertIsInstance(org_analyze(f), OrgPlan)
        self.assertEqual(org_trash(None, lambda p: True)[0], 0)
        self.assertEqual(org_trash([object()], lambda p: True)[0], 0)
        n, msgs = org_undo(self.tmp / "nix.json")
        self.assertEqual(n, 0)
        self.assertTrue(msgs)
        n, fails = org_apply_moves(OrgPlan(root=self.tmp / "weg"), [], self.tmp / "j.json")
        self.assertEqual(n, 0)
        res = org_backup([object()], self.tmp / "tresor")
        self.assertEqual(res["copied"], 0)
        self.assertEqual(len(res["failed"]), 1)
        # Fortschritt/cancelled, die Ausnahmen werfen
        root = build_tree(self.tmp / "x")

        def boom(*a):
            raise RuntimeError("x")
        plan = org_analyze(root, programs=[], now=NOW, progress=boom, cancelled=boom)
        self.assertTrue(plan.moves)

    def test_text_budget(self):
        root = build_tree(self.tmp)
        old = org_planner.ORG_PLAN_TEXT_BUDGET
        org_planner.ORG_PLAN_TEXT_BUDGET = 0.0
        try:
            plan = org_analyze(root, programs=PROGRAMS, now=NOW)
        finally:
            org_planner.ORG_PLAN_TEXT_BUDGET = old
        self.assertGreater(plan.stats["text_skipped"], 0)
        self.assertEqual(by_rel(plan)["Lebenslauf_Max_Mustermann.pdf"].category, "cv")


class PlannerApplyUndo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="orgplan_"))
        self.root = build_tree(self.tmp)
        self.journal = self.tmp / "journal" / "journal-1.json"

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_apply_then_undo_restores_exact_tree(self):
        before = snapshot(self.root)
        plan = org_analyze(self.root, reorganize_subfolders=True, programs=PROGRAMS, now=NOW)
        n, fails = org_apply_moves(plan, plan.moves, self.journal)
        self.assertEqual(fails, [])
        self.assertEqual(n, len(plan.moves))
        after = snapshot(self.root)
        self.assertNotEqual(after, before)
        self.assertTrue((self.root / "Bewerbung" / "Lebenslauf_Max_Mustermann.pdf").is_file())
        self.assertTrue((self.root / "Bewerbung" / "Lebenslauf_final.docx").is_file())
        # nichts verloren: gleiche Menge an Inhalten
        self.assertEqual(sorted(v for k, v in before.items() if k.startswith("F:")),
                         sorted(v for k, v in after.items() if k.startswith("F:")))
        data = json.loads(self.journal.read_text("utf-8"))
        self.assertEqual(len(data["moves"]), n)
        self.assertTrue(data["created"])
        # OrgFile zeigt auf den neuen Ort
        self.assertTrue(all(m.file.path.is_file() for m in plan.moves))
        r, msgs = org_undo(self.journal)
        self.assertEqual(r, n)
        self.assertEqual(msgs, [])
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(self.journal.exists())
        self.assertTrue((self.journal.parent / "journal-1.undone.json").exists())

    def test_name_collision_never_overwrites(self):
        plan = org_analyze(self.root, programs=PROGRAMS, now=NOW)
        write(self.root, "Bewerbung/Lebenslauf_Max_Mustermann.pdf", "anderer Inhalt")
        mv = [m for m in plan.moves if m.file.rel == "Lebenslauf_Max_Mustermann.pdf"]
        n, fails = org_apply_moves(plan, mv, self.journal)
        self.assertEqual((n, fails), (1, []))
        self.assertEqual((self.root / "Bewerbung/Lebenslauf_Max_Mustermann.pdf").read_text(),
                         "anderer Inhalt")
        self.assertTrue((self.root / "Bewerbung/Lebenslauf_Max_Mustermann (2).pdf").is_file())

    def test_undo_occupied_and_moved_again(self):
        plan = org_analyze(self.root, programs=PROGRAMS, now=NOW)
        mv = {m.file.rel: m for m in plan.moves}
        sel = [mv["Notizen.txt"], mv["Einkaufsliste.txt"]]
        n, fails = org_apply_moves(plan, sel, self.journal)
        self.assertEqual(n, 2)
        write(self.root, "Notizen.txt", "neu angelegt")                     # Platz belegt
        os.rename(str(self.root / "Sonstiges" / "Einkaufsliste.txt"),       # weiterverschoben
                  str(self.root / "Einkaufsliste_umbenannt.txt"))
        r, msgs = org_undo(self.journal)
        self.assertEqual(r, 1)
        self.assertEqual((self.root / "Notizen.txt").read_text(), "neu angelegt")
        self.assertTrue((self.root / "Notizen (wiederhergestellt).txt").is_file())
        self.assertTrue(any("inzwischen verschoben" in m for m in msgs), msgs)

    def test_path_traversal_guard(self):
        plan = org_analyze(self.root, programs=PROGRAMS, now=NOW)
        outside = self.tmp / "draussen"
        outside.mkdir()
        try:
            os.symlink(str(outside), str(self.root / "link"))
            have_link = True
        except (OSError, NotImplementedError):
            have_link = False
        f = by_rel(plan)["Notizen.txt"]
        bad = ["../draussen", "../../x", "/etc", "C:/Windows", "a/../../draussen",
               "Bewerbung/../../draussen", "\\\\server\\share", "CON", "x:y", "a/ b",
               None, 5]
        if have_link:
            bad.append("link/sub")
        before = snapshot(self.root)
        n, fails = org_apply_moves(plan, [OrgMove(f, d, True) for d in bad], self.journal)
        self.assertEqual(n, 0)
        self.assertEqual(len(fails), len(bad))
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(snapshot(self.root), before)

    def test_journal_crash_safety(self):
        plan = org_analyze(self.root, programs=PROGRAMS, now=NOW)
        before = snapshot(self.root)
        real = org_planner._org_plan_rename_noreplace
        calls = {"n": 0}

        class Crash(BaseException):
            pass

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == 6:
                raise Crash()
            return real(src, dst)
        org_planner._org_plan_rename_noreplace = flaky
        try:
            with self.assertRaises(Crash):
                org_apply_moves(plan, plan.moves, self.journal)
        finally:
            org_planner._org_plan_rename_noreplace = real
        data = json.loads(self.journal.read_text("utf-8"))   # gültiges JSON trotz Absturz
        self.assertEqual(len(data["moves"]), 6)             # 5 erledigt + 1 Absicht
        self.assertNotEqual(snapshot(self.root), before)
        r, msgs = org_undo(self.journal)
        self.assertEqual(r, 5)
        self.assertEqual(snapshot(self.root), before)

    def test_truncated_journal_repair(self):
        plan = org_analyze(self.root, programs=PROGRAMS, now=NOW)
        before = snapshot(self.root)
        n, _ = org_apply_moves(plan, plan.moves[:4], self.journal)
        self.assertEqual(n, 4)
        raw = self.journal.read_bytes()
        self.journal.write_bytes(raw[:-10])                  # abgeschnittenes Ende
        r, msgs = org_undo(self.journal)
        self.assertGreaterEqual(r, 3)
        if r == 3:                                          # letzter Eintrag unlesbar
            pass
        else:
            self.assertEqual(snapshot(self.root), before)

    def test_existing_foreign_journal_not_overwritten(self):
        plan = org_analyze(self.root, programs=PROGRAMS, now=NOW)
        self.journal.parent.mkdir(parents=True)
        self.journal.write_text('{"root": "/anderswo", "created": [], "moves": [["a","b"]]}')
        n, fails = org_apply_moves(plan, plan.moves, self.journal)
        self.assertEqual(n, 0)
        self.assertTrue(fails)
        self.assertIn("anderswo", self.journal.read_text())

    def test_second_apply_appends_and_undo_restores_both(self):
        before = snapshot(self.root)
        plan = org_analyze(self.root, programs=PROGRAMS, now=NOW)
        half = len(plan.moves) // 2
        n1, f1 = org_apply_moves(plan, plan.moves[:half], self.journal)
        n2, f2 = org_apply_moves(plan, plan.moves[half:], self.journal)
        self.assertEqual((f1, f2), ([], []))
        data = json.loads(self.journal.read_text("utf-8"))
        self.assertEqual(len(data["moves"]), n1 + n2)
        r, msgs = org_undo(self.journal)
        self.assertEqual(r, n1 + n2)
        self.assertEqual(snapshot(self.root), before)

    def test_undo_ignores_entries_outside_root(self):
        outside = self.tmp / "geheim.txt"
        outside.write_text("bleibt")
        self.journal.parent.mkdir(parents=True)
        self.journal.write_text(json.dumps({
            "root": str(self.root), "created": [str(self.tmp)],
            "moves": [[str(self.root / "x.txt"), str(outside)], ["a"], 5]}))
        r, msgs = org_undo(self.journal)
        self.assertEqual(r, 0)
        self.assertEqual(outside.read_text(), "bleibt")
        self.assertTrue(self.tmp.is_dir())
        self.assertEqual(len(msgs), 3)

    def test_trash_duplicate_after_keeper_was_moved(self):
        plan = org_analyze(self.root, programs=PROGRAMS, now=NOW)
        f = by_rel(plan)
        keeper = f["Lebenslauf_Max_Mustermann.pdf"]
        dup = f["Downloads/Lebenslauf_Max_Mustermann (1).pdf"]
        mv = [m for m in plan.moves if m.file is keeper]
        self.assertEqual(org_apply_moves(plan, mv, self.journal)[0], 1)
        self.assertEqual(keeper.rel, "Bewerbung/Lebenslauf_Max_Mustermann.pdf")
        n, fails = org_trash([dup], lambda p: (os.unlink(p), True)[1])
        self.assertEqual((n, fails), (1, []))
        self.assertTrue(keeper.path.is_file())

    def test_apply_refuses_project_and_missing(self):
        plan = org_analyze(self.root, programs=PROGRAMS, now=NOW)
        f = by_rel(plan)
        proj = f["Projekte/meinprojekt/README.md"]
        gone = f["Notizen.txt"]
        os.unlink(str(gone.path))
        n, fails = org_apply_moves(plan, [OrgMove(proj, "Sonstiges", True),
                                          OrgMove(gone, "Sonstiges", True)], self.journal)
        self.assertEqual(n, 0)
        self.assertEqual(len(fails), 2)
        self.assertTrue(proj.path.is_file())


class PlannerTrashBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="orgplan_"))
        self.root = build_tree(self.tmp)
        self.plan = org_analyze(self.root, programs=PROGRAMS, now=NOW)
        self.f = by_rel(self.plan)
        self.bin = self.tmp / "papierkorb"
        self.bin.mkdir()

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def trash_fn(self, p):
        shutil.move(p, str(self.bin / (str(len(os.listdir(str(self.bin)))) + "_" +
                                       os.path.basename(p))))
        return True

    def test_trash_safety(self):
        f = self.f
        cv = f["Lebenslauf_Max_Mustermann.pdf"]
        cv_dup = f["Downloads/Lebenslauf_Max_Mustermann (1).pdf"]
        proj = f["Projekte/meinprojekt/README.md"]
        n, fails = org_trash([cv, proj], self.trash_fn)
        self.assertEqual(n, 0)
        self.assertEqual(len(fails), 2)
        self.assertTrue(cv.path.is_file() and proj.path.is_file())
        # Kopie eines geschützten Originals: erlaubt, solange das Original da ist
        n, fails = org_trash([cv_dup], self.trash_fn)
        self.assertEqual((n, fails), (1, []))
        # gesamter Müll: alles außer Geschütztem geht
        n, fails = org_trash(self.plan.junk, self.trash_fn)
        self.assertGreater(n, 5)
        for x in self.plan.protect:
            self.assertTrue(x.path.is_file(), x.rel)
        self.assertTrue(cv.path.is_file())
        self.assertTrue(f["Zeugnisse/Abiturzeugnis.pdf"].path.is_file())
        # nicht mehr vorhanden
        n, fails = org_trash([cv_dup], self.trash_fn)
        self.assertEqual(n, 0)
        self.assertTrue(any("Nicht mehr vorhanden" in x for x in fails))

    def test_trash_refuses_dup_without_original(self):
        f = self.f
        keeper = f["Lebenslauf_Max_Mustermann.pdf"]
        d1 = f["Downloads/Lebenslauf_Max_Mustermann (1).pdf"]
        os.rename(str(keeper.path), str(self.tmp / "weg.pdf"))
        n, fails = org_trash([d1], self.trash_fn)
        self.assertEqual(n, 0)
        self.assertTrue(d1.path.is_file())
        # Original + Kopie in einem Aufruf: es bleibt immer eins übrig
        os.rename(str(self.tmp / "weg.pdf"), str(keeper.path))
        n, fails = org_trash([keeper, d1], self.trash_fn)
        self.assertEqual(n, 1)
        self.assertTrue(keeper.path.is_file())

    def test_trash_modified_and_failing_fn(self):
        lock = self.f["~WRL0001.tmp"]
        with open(str(lock.path), "ab") as fh:
            fh.write(b"mehr")
        n, fails = org_trash([lock], self.trash_fn)
        self.assertEqual(n, 0)
        tmpf = self.f["Thumbs.db"]

        def bad(p):
            raise OSError("kaputt")
        n, fails = org_trash([tmpf], bad)
        self.assertEqual(n, 0)
        n, fails = org_trash([tmpf], lambda p: False)
        self.assertEqual(n, 0)
        self.assertTrue(tmpf.path.is_file())

    def test_backup_incremental_and_verified(self):
        vault = self.tmp / "Tresor"
        prot = self.plan.protect
        self.assertGreater(len(prot), 20)
        prog = []
        res = org_backup(prot, vault, progress=lambda s, d, t: prog.append((d, t)))
        self.assertEqual(res["failed"], [])
        self.assertEqual(res["copied"], len(prot))
        self.assertTrue(prog)
        self.assertTrue((vault / "Dokumente/Bewerbung/Lebenslauf_Max_Mustermann.pdf").is_file())
        self.assertTrue((vault / "Dokumente/Steuer/2022/Steuerbescheid_2022.pdf").is_file())
        self.assertTrue((vault / "Bilder/Fotos/2023/2023-07-10 bis 07-13 – Reise/IMG_1001.JPG")
                        .is_file())
        for x in prot:
            hits = list(vault.rglob(x.path.name))
            self.assertTrue(hits, x.rel)
        vault_before = snapshot(vault)
        res2 = org_backup(prot, vault)
        self.assertEqual((res2["copied"], res2["skipped"], res2["failed"]), (0, len(prot), []))
        self.assertEqual(snapshot(vault), vault_before)
        # gleicher Name, anderer Inhalt → "(2)", Vorhandenes bleibt unverändert
        cv = self.f["Lebenslauf_Max_Mustermann.pdf"]
        with open(str(cv.path), "ab") as fh:
            fh.write(b"%neu")
        res3 = org_backup([cv], vault)
        self.assertEqual(res3["copied"], 1)
        self.assertTrue((vault / "Dokumente/Bewerbung/Lebenslauf_Max_Mustermann (2).pdf").is_file())
        self.assertEqual(snapshot(vault)["F:Dokumente/Bewerbung/Lebenslauf_Max_Mustermann.pdf"],
                         vault_before["F:Dokumente/Bewerbung/Lebenslauf_Max_Mustermann.pdf"])
        self.assertEqual(org_backup([cv], vault)["copied"], 0)

    def test_backup_corrupt_copy_detected(self):
        vault = self.tmp / "Tresor"
        cv = self.f["Steuerbescheid_2022.pdf"]
        real = shutil.copy2

        def corrupt(src, dst, *a, **k):
            r = real(src, dst, *a, **k)
            with open(dst, "ab") as fh:
                fh.write(b"X")
            return r
        org_planner.shutil.copy2 = corrupt
        try:
            res = org_backup([cv], vault)
        finally:
            org_planner.shutil.copy2 = real
        self.assertEqual(res["copied"], 0)
        self.assertEqual(len(res["failed"]), 1)
        leftovers = [p for p in vault.rglob("*") if p.is_file()]
        self.assertEqual(leftovers, [])
        self.assertTrue(cv.path.is_file())


class PlannerPerformance(unittest.TestCase):
    def test_30000_files(self):
        tmp = Path(tempfile.mkdtemp(prefix="orgplan_perf_"))
        try:
            root = tmp / "Dokumente"
            names = ["Rechnung_%d_2023.pdf", "IMG_%04d.JPG", "Notiz %d.txt", "Lebenslauf_v%d.docx",
                     "Screenshot %d.png", "song%d.mp3", "data_%d.csv", "setup%d.exe",
                     "foto%d.jpg", "Kontoauszug_%d.pdf"]
            n = 0
            for d in range(300):
                sub = root / ("Ordner %d" % d) / ("Unter %d" % (d % 7))
                sub.mkdir(parents=True)
                for i in range(100):
                    nm = names[(d + i) % len(names)] % (d * 100 + i)
                    (sub / nm).write_bytes(b"x" * (n % 997 + 1) + str(n).encode())
                    n += 1
            self.assertEqual(n, 30000)
            t0 = time.monotonic()
            plan = org_analyze(root, programs=PROGRAMS, extract_text=False, now=NOW,
                               reorganize_subfolders=True)
            dt = time.monotonic() - t0
            print("\n  30.000 Dateien analysiert in %.1f s (%d Verschiebungen)"
                  % (dt, len(plan.moves)))
            self.assertEqual(len(plan.files), 30000)
            self.assertNotIn("error", plan.stats)
            self.assertLess(dt, 30.0)
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)


class PlannerCodeRules(unittest.TestCase):
    def test_names_and_syntax(self):
        src = (HERE / "org_planner.py").read_text("utf-8")
        tree = ast.parse(src)
        ok = ("org_", "Org", "ORG_", "_org_", "_ORG_")
        for node in tree.body:
            names = []
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
            for nm in names:
                self.assertTrue(nm.startswith(ok), nm)
        for node in ast.walk(tree):
            self.assertNotEqual(type(node).__name__, "Match")
        self.assertIn("from __future__ import annotations", src)
        # keine Kollision mit Namen anderer Module
        mine = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        for other in HERE.glob("org_*.py"):
            if other.name == "org_planner.py":
                continue
            t = ast.parse(other.read_text("utf-8"))
            theirs = {n.name for n in t.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
            for n in t.body:
                if isinstance(n, ast.Assign):
                    theirs |= {x.id for x in n.targets if isinstance(x, ast.Name)}
            self.assertEqual(mine & theirs, set(), other.name)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    res = unittest.TextTestRunner(verbosity=1).run(suite)
    print("SUMMARY: %d Tests, %d Fehler, %d Ausnahmen, %d übersprungen (Python %s)"
          % (res.testsRun, len(res.failures), len(res.errors), len(res.skipped),
             sys.version.split()[0]))
    sys.exit(0 if res.wasSuccessful() else 1)
