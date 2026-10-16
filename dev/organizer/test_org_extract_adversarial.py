# Adversariale Tests für org_extract (Prüfer-Runde): Grenzfälle, Windows-Pfade,
# Cloud-Platzhalter, pathologische Eingaben, Leistung bei 60 000 Dateien,
# und: das Modul darf Benutzerdateien niemals verändern.
from __future__ import annotations

import hashlib
import io
import os
import random
import re
import shutil
import stat
import struct
import sys
import tempfile
import threading
import time
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import org_extract as ox  # noqa: E402
from org_extract import (org_extract_text, org_image_size, org_pdf_page_count,  # noqa: E402
                         org_read_exif)
from test_org_extract import (CONTENT_LEBENSLAUF, build_jpeg, build_pdf, build_png,  # noqa: E402
                              build_tiff, full_tiff, jpeg_seg, pdf_stream, png_chunk,
                              simple_pdf)

HELV = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"


def _naive_clean(text: str, max_chars: int) -> str:
    """Referenz: die ursprüngliche (langsame) Bereinigung."""
    text = text.replace("\xad", "")
    text = ox._ORG_RE_CTRL.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _mkzip(members: dict) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in members.items():
            z.writestr(k, v)
    return b.getvalue()


def _fake_stat(attrs: int, mode: int = stat.S_IFREG | 0o644):
    class St:
        st_mode = mode
        st_size = 1000
        st_mtime = 0.0
        st_file_attributes = attrs
    return St()


class OrgExtractAdversarial(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="orgx_adv_"))
        cls._qt = ox.ORG_EXTRACT_USE_QTPDF

    @classmethod
    def tearDownClass(cls):
        ox.ORG_EXTRACT_USE_QTPDF = cls._qt
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        ox.ORG_EXTRACT_USE_QTPDF = False          # reiner Python-Pfad prüfen

    def w(self, name: str, data: bytes) -> Path:
        p = self.tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    # ------------------------------------------------------------------ Klartext
    def test_cp1252_umlaut_as_last_char_not_lost(self):
        # Fehler (behoben): „Menü“ in cp1252 wurde zu „Men“ (utf-8-Abschneide-Toleranz
        # galt auch für vollständig gelesene Dateien)
        self.assertEqual(org_extract_text(self.w("menu.txt", "Menü".encode("cp1252"))), "Menü")
        self.assertEqual(org_extract_text(self.w("gruss.txt", "Viele Grüße".encode("cp1252"))),
                         "Viele Grüße")
        self.assertEqual(org_extract_text(self.w("strasse.txt", b"Hauptstra\xdfe")),
                         "Hauptstraße")

    def test_utf8_cut_at_first_read_boundary(self):
        # Mehrbyte-Zeichen genau an der 32-KB-Grenze des ersten Lesens
        first = ox._ORG_TEXT_READ_FIRST
        body = b"a" * (first - 1) + "ü".encode("utf-8") + ("Zeugnis " * 5000).encode()
        t = org_extract_text(self.w("cut.txt", body), 4000)
        self.assertEqual(len(t), 4000)
        self.assertNotIn("Ã", t)

    def test_whitespace_heavy_text_reads_further(self):
        # Ein CSV/Log mit 100 KB Leerraum vor dem Inhalt: Nachlesen bis 256 KB
        body = b" \r\n\t" * 25000 + "Lebenslauf Jürgen Müller".encode("utf-8")
        t = org_extract_text(self.w("spaces.txt", body))
        self.assertEqual(t, "Lebenslauf Jürgen Müller")

    def test_utf16_be_without_bom_short(self):
        t = org_extract_text(self.w("be.txt", "Zeugnis für Anna".encode("utf-16-be")))
        self.assertEqual(t, "Zeugnis für Anna")

    def test_clean_chunked_equals_naive(self):
        rnd = random.Random(7)
        alphabet = ["a", "ä", "ß", " ", "  ", "\n", "\t", "\r\n", "\x00", "\x07", "\xad",
                    "\ufffd", "\ue000", "\u200b", "\xa0", "Lebenslauf", "€", "😀", "\x0c"]
        for k in range(300):
            n = rnd.choice([0, 1, 5, 100, 16383, 16384, 16385, 40000])
            parts = []
            size = 0
            while size < n:
                piece = rnd.choice(alphabet)
                parts.append(piece)
                size += len(piece)
            text = "".join(parts)
            for mc in (1, 7, 4000, 9000, 100000):
                self.assertEqual(ox._org_clean(text, mc), _naive_clean(text, mc),
                                 "Iteration %d, max_chars %d" % (k, mc))

    def test_max_chars_bounds(self):
        p = self.w("long.txt", ("Kündigung Mietvertrag " * 30000).encode("utf-8"))
        self.assertEqual(len(org_extract_text(p, 1)), 1)
        self.assertEqual(len(org_extract_text(p, 4000)), 4000)
        big = org_extract_text(p, 10 ** 7)                 # nie mehr als 256 KB gelesen
        self.assertTrue(200000 < len(big) <= 262144)
        self.assertEqual(org_extract_text(p, -5), "")

    # ------------------------------------------------------------------ HTML
    def test_html_pathological_is_linear(self):
        cases = {
            "comments.html": b"<!--" * 65536,              # vorher ~84 s
            "scripts.html": b"<script>x" * 29000,          # vorher ~35 s
            "lt.html": b"<" * 262144,
            "ltgt.xml": b"<a " * 87000,
            "styles.htm": b"<STYLE a=1>" * 23000,
        }
        for name, data in cases.items():
            t0 = time.time()
            r = org_extract_text(self.w(name, data))
            self.assertIsInstance(r, str)
            self.assertLess(time.time() - t0, 2.0, name)

    def test_html_strip_correctness(self):
        doc = ("<html><head><TITLE>Arbeitszeugnis</TITLE><SCRIPT type='x'>var a='<b>';"
               "</SCRIPT ><style>p{}</style></head><body><!-- Kommentar --><p>Frau "
               "Erika Mustermann</p><p>Note: sehr&nbsp;gut &amp; zuverl&auml;ssig</p>"
               "<script>offen ohne Ende").encode("utf-8")
        t = org_extract_text(self.w("zeugnis.html", doc))
        self.assertEqual(t, "Arbeitszeugnis Frau Erika Mustermann Note: sehr gut & zuverlässig")

    def test_html_lt_in_text_does_not_eat_content(self):
        t = org_extract_text(self.w("lt2.html", b"<p>Wenn a < b dann Lebenslauf</p>"))
        self.assertIn("Lebenslauf", t)

    # ------------------------------------------------------------------ E-Mail
    def test_eml_unknown_charset(self):
        eml = (b"Subject: =?utf-8?q?Bewerbung_J=C3=BCrgen?=\r\nFrom: a@b.de\r\n"
               b"Content-Type: text/plain; charset=unknown-8bit\r\n\r\n"
               b"Sehr geehrte Damen und Herren, anbei mein Lebenslauf. Gr\xfc\xdfe\r\n")
        t = org_extract_text(self.w("m.eml", eml))
        self.assertTrue(t.startswith("Bewerbung Jürgen"), t)
        self.assertIn("Lebenslauf. Grüße", t)
        self.assertNotIn("Content-Type", t)

    # ------------------------------------------------------------------ Office
    def test_docx_main_part_from_rels(self):
        rels = (b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
                b'package/2006/relationships"><Relationship Id="rId1" Target="word/document2.xml" '
                b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                b'officeDocument"/></Relationships>')
        doc = ('<w:document><w:body><w:p><w:r><w:t>Lebens</w:t></w:r><w:proofErr/>'
               '<w:bookmarkStart w:id="0"/><w:r><w:rPr><w:b/></w:rPr><w:t>lauf</w:t></w:r>'
               '</w:p><w:p><w:r><w:t>Jürgen Müller</w:t></w:r></w:p></w:body></w:document>')
        p = self.w("cv.docx", _mkzip({"_rels/.rels": rels,
                                      "word/document2.xml": doc.encode("utf-8")}))
        self.assertEqual(org_extract_text(p), "Lebenslauf Jürgen Müller")

    def test_docx_utf16_xml(self):
        doc = '\ufeff<w:document><w:p><w:r><w:t>Zeugnis Grüße</w:t></w:r></w:p></w:document>'
        p = self.w("u16.docx", _mkzip({"word/document.xml": doc.encode("utf-16-le")}))
        self.assertEqual(org_extract_text(p), "Zeugnis Grüße")

    def test_xlsx_inline_strings_without_shared_strings(self):
        sheet = (b'<worksheet><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Kontoauszug'
                 b'</t></is></c><c r="B1" t="inlineStr"><is><t>M\xc3\xa4rz</t></is></c>'
                 b'<c r="C1"><v>2024</v></c></row></sheetData></worksheet>')
        p = self.w("konto.xlsx", _mkzip({"xl/worksheets/sheet1.xml": sheet}))
        self.assertEqual(org_extract_text(p), "Kontoauszug März 2024")

    def test_office_wrong_content_is_neutral(self):
        for name, data in (("html.docx", b"<html>Fehlerseite</html>"),
                           ("empty.xlsx", b""),
                           ("pdf.pptx", simple_pdf(CONTENT_LEBENSLAUF)),
                           ("zipnoxml.odt", _mkzip({"a.txt": b"x"}))):
            self.assertEqual(org_extract_text(self.w(name, data)), "", name)

    # ------------------------------------------------------------------ RTF
    def test_rtf_wordpad_real_world(self):
        rtf = (b"{\\rtf1\\ansi\\ansicpg1252\\deff0\\nouicompat\\deflang1031{\\fonttbl{\\f0\\fnil"
               b"\\fcharset0 Calibri;}}\r\n{\\*\\generator Riched20 10.0.19041}\\viewkind4\\uc1 \r\n"
               b"\\pard\\sa200\\sl276\\slmult1\\f0\\fs22\\lang7 Lebenslauf J\\'fcrgen M\\u252?ller"
               b"\\par\r\nStra\\'dfe 5\\tab Zeugnis\\par\r\n}\r\n\x00")
        self.assertEqual(org_extract_text(self.w("cv.rtf", rtf)),
                         "Lebenslauf Jürgen Müller Straße 5 Zeugnis")

    # ------------------------------------------------------------------ PDF
    def test_pdf_encrypt_word_in_page_text_is_not_encryption(self):
        c = b"BT /F1 12 Tf 72 700 Td (Hinweis /Encrypt << Lebenslauf Anna Schmidt) Tj ET"
        p = self.w("encword.pdf", simple_pdf(c, flate=False))
        self.assertIn("Lebenslauf Anna Schmidt", org_extract_text(p))

    def test_pdf_really_encrypted_is_empty_without_qt(self):
        c = b"BT /F1 12 Tf 72 700 Td (Geheimer Text der nicht lesbar ist) Tj ET"
        p = self.w("enc.pdf", simple_pdf(c, flate=False, trailer_extra=b" /Encrypt 9 0 R"))
        self.assertEqual(org_extract_text(p), "")
        # Querverweis-Strom (PDF 1.5) statt klassischem Trailer
        objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> "
                b"/Contents 4 0 R >>",
                pdf_stream(c), HELV,
                b"<< /Type /XRef /Size 7 /Root 1 0 R /Encrypt 9 0 R /W [1 2 1] /Length 0 >>"
                b"\nstream\n\nendstream"]
        p2 = self.w("enc15.pdf", build_pdf(objs, trailer=False))
        self.assertEqual(org_extract_text(p2), "")

    def test_pdf_raw_deflate_without_zlib_header(self):
        co = zlib.compressobj(wbits=-15)
        raw = co.compress(b"BT /F1 12 Tf 72 700 Td (Mietvertrag Wohnung M\\374nchen) Tj ET") + \
            co.flush()
        objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> "
                b"/Contents 4 0 R >>",
                b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(raw) + raw +
                b"\nendstream", HELV]
        self.assertEqual(org_extract_text(self.w("rawdefl.pdf", build_pdf(objs))),
                         "Mietvertrag Wohnung München")

    def test_pdf_ligature_via_tounicode(self):
        cmap = (b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
                b"1 begincodespacerange <00> <FF> endcodespacerange "
                b"2 beginbfchar <1F> <00660069> <1E> <00FC> endbfchar endcmap end end")
        font = (b"<< /Type /Font /Subtype /TrueType /BaseFont /ABCDEF+Calibri "
                b"/Encoding /WinAnsiEncoding /ToUnicode 6 0 R >>")
        c = b"BT /F1 11 Tf 1 0 0 1 72 700 Tm [(Zerti\x1fkat f\x1er Weiterbildung)] TJ ET"
        objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> "
                b"/Contents 4 0 R >>",
                pdf_stream(c, flate=True), font, pdf_stream(cmap, flate=True)]
        self.assertEqual(org_extract_text(self.w("lig.pdf", build_pdf(objs))),
                         "Zertifikat für Weiterbildung")

    def test_pdf_disguised_and_tiny(self):
        for name, data in (("html.pdf", b"<html><body>404 Not Found</body></html>"),
                           ("pct.pdf", b"%PDF"), ("nul.pdf", b"\x00" * 5000),
                           ("only_hdr.pdf", b"%PDF-1.7\n%%EOF")):
            p = self.w(name, data)
            self.assertEqual(org_extract_text(p), "", name)
            self.assertIsNone(org_pdf_page_count(p), name)

    # ------------------------------------------------------------------ EXIF/Bild
    def test_exif_beyond_first_read_window(self):
        # 3 × 60 KB ICC/Herstellerdaten vor dem EXIF-Block → zweiter Lesevorgang nötig
        pad = b"".join(jpeg_seg(0xE2, b"ICC_PROFILE\x00" + b"\x11" * 60000) for _ in range(3))
        j = build_jpeg(full_tiff("MM"))
        k = j.find(b"\xff\xe1")
        data = j[:k] + pad + j[k:] + os.urandom(600 * 1024)
        r = org_read_exif(self.w("late_exif.jpg", data))
        self.assertEqual(r.get("datetime"), "2023-07-14 09:30:15")
        self.assertEqual(r.get("gps"), (47.8, 13.0))

    def test_exif_absent_reads_once(self):
        data = build_jpeg() + os.urandom(800 * 1024)       # z. B. WhatsApp-Bild ohne EXIF
        p = self.w("whatsapp.jpg", data)
        calls = []
        real = ox._org_read

        def spy(path, limit, offset=0):
            calls.append(limit)
            return real(path, limit, offset)
        with mock.patch.object(ox, "_org_read", spy):
            self.assertEqual(org_read_exif(p), {})
        self.assertEqual(calls, [ox._ORG_EXIF_READ_FIRST])

    def test_png_exif_after_large_idat(self):
        exif = full_tiff("II")
        png = b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 50, 40, 8, 2, 0, 0, 0))
        png += png_chunk(b"IDAT", os.urandom(200 * 1024)) + png_chunk(b"eXIf", exif)
        png += png_chunk(b"IEND", b"")
        self.assertEqual(org_read_exif(self.w("late.png", png)).get("make"), "Canon")

    def test_gps_negative_srational_with_ref(self):
        def srat(vals):
            return b"".join(struct.pack("<ii", a, b) for a, b in vals)
        # eigenes TIFF: GPS-Lat als SRATIONAL (Typ 10) mit negativem Wert UND Ref „S“
        lat = srat([(-33, 1), (52, 1), (0, 1)])
        lon = srat([(151, 1), (12, 1), (0, 1)])
        ent = []
        base = 8 + 2 + 12 * 1 + 4                      # IFD0 mit 1 Eintrag (GPS-Zeiger)
        gps_off = base
        gps_entries = 4
        data_off = gps_off + 2 + 12 * gps_entries + 4
        ifd0 = struct.pack("<H", 1) + struct.pack("<HHII", 0x8825, 4, 1, gps_off) + b"\0\0\0\0"
        g = struct.pack("<H", gps_entries)
        g += struct.pack("<HHI", 1, 2, 2) + b"S\0\0\0"
        g += struct.pack("<HHII", 2, 10, 3, data_off)
        g += struct.pack("<HHI", 3, 2, 2) + b"E\0\0\0"
        g += struct.pack("<HHII", 4, 10, 3, data_off + 24)
        g += b"\0\0\0\0"
        tiff = b"II*\x00" + struct.pack("<I", 8) + ifd0 + g + lat + lon
        del ent
        r = org_read_exif(self.w("syd.jpg", build_jpeg(tiff)))
        self.assertIn("gps", r)
        self.assertLess(r["gps"][0], 0)                # Süden bleibt Süden
        self.assertGreater(r["gps"][1], 151)

    def test_jpeg_size_with_fill_bytes_between_segments(self):
        j = build_jpeg(full_tiff("II"), w=3024, h=4032)
        i = j.find(b"\xff\xdb")
        j = j[:i] + b"\x00\x00\x00" + j[i:]
        self.assertEqual(org_image_size(self.w("pad.jpg", j)), (3024, 4032))

    def test_image_mislabeled_extensions(self):
        # Inhalt zählt, nicht die Endung (z. B. PNG als .jpg aus Messenger)
        p = self.w("eigentlich_png.jpg", build_png(321, 123))
        self.assertEqual(org_image_size(p), (321, 123))
        self.assertEqual(org_read_exif(p), {})

    # ------------------------------------------------------------------ Pfade
    def test_windows_long_path_prefix(self):
        long_p = "C:\\Users\\Jürgen\\Documents\\" + "Unterordner\\" * 25 + "Lebenslauf.pdf"
        self.assertTrue(len(long_p) > 260)
        r = ox._org_fs_path(long_p, nt=True)
        self.assertTrue(r.startswith("\\\\?\\C:\\Users\\Jürgen\\"), r)
        unc = "\\\\nas\\familie\\" + "Fotos\\" * 45 + "Oma.jpg"
        self.assertTrue(ox._org_fs_path(unc, nt=True).startswith("\\\\?\\UNC\\nas\\familie\\"))
        self.assertEqual(ox._org_fs_path("C:\\kurz.pdf", nt=True), "C:\\kurz.pdf")
        pre = "\\\\?\\C:\\" + "x\\" * 200 + "a.pdf"
        self.assertEqual(ox._org_fs_path(pre, nt=True), pre)
        self.assertEqual(ox._org_fs_path(long_p, nt=False), long_p)

    def test_windows_style_paths_never_raise(self):
        for raw in ("C:\\Users\\Jürgen\\Documents\\Lebenslauf.PDF",
                    "\\\\server\\share\\Zeugnis.docx", "D:Urlaub.jpg", "CON.txt", "NUL",
                    "C:\\" + "a" * 300 + ".txt", "", ".", b"/tmp/x.pdf", 12345, None):
            self.assertEqual(org_extract_text(raw), "", repr(raw))
            self.assertEqual(org_read_exif(raw), {}, repr(raw))
            self.assertIsNone(org_image_size(raw), repr(raw))
            self.assertIsNone(org_pdf_page_count(raw), repr(raw))

    def test_unicode_and_long_file_names(self):
        names = ["Urlaub 🏖️ Mallorca 2019 – Oma & Opa.jpg", "Lebenslauf_final_v2 (1).PDF",
                 "Größe Übersicht ß.TXT", "x" * 200 + "é.jpg", "  Leerzeichen  .txt",
                 "ДокументЗеугнис.txt"]
        for n in names:
            try:
                n.encode(sys.getfilesystemencoding())
            except UnicodeEncodeError:
                continue
            if n.lower().endswith(".jpg"):
                p = self.w(n, build_jpeg(full_tiff("II")))
                self.assertEqual(org_read_exif(p).get("make"), "Canon", n)
                self.assertEqual(org_image_size(p), (640, 480), n)
            elif n.lower().endswith(".pdf"):
                p = self.w(n, simple_pdf(CONTENT_LEBENSLAUF))
                self.assertIn("Jürgen Müller", org_extract_text(p), n)
                self.assertEqual(org_pdf_page_count(p), 1)
            else:
                p = self.w(n, "Zeugnis für Jürgen".encode("cp1252"))
                self.assertEqual(org_extract_text(p), "Zeugnis für Jürgen", n)

    def test_directory_with_file_extension(self):
        d = self.tmp / "Bewerbung.pdf"
        d.mkdir(exist_ok=True)
        (self.tmp / "Fotos.jpg").mkdir(exist_ok=True)
        self.assertEqual(org_extract_text(d), "")
        self.assertIsNone(org_pdf_page_count(d))
        self.assertEqual(org_read_exif(self.tmp / "Fotos.jpg"), {})
        self.assertIsNone(org_image_size(self.tmp / "Fotos.jpg"))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "keine FIFOs")
    def test_fifo_does_not_block(self):
        f = self.tmp / "pipe.txt"
        if not f.exists():
            os.mkfifo(str(f))
        res = []
        t = threading.Thread(target=lambda: res.append(
            (org_extract_text(f), org_read_exif(f), org_image_size(f))), daemon=True)
        t.start()
        t.join(5)
        self.assertFalse(t.is_alive(), "FIFO blockiert den Worker")
        self.assertEqual(res, [("", {}, None)])

    # ------------------------------------------------------------------ Cloud
    def test_cloud_placeholder_is_never_opened(self):
        pdf = self.w("onedrive_cv.pdf", simple_pdf(CONTENT_LEBENSLAUF))
        jpg = self.w("onedrive_foto.jpg", build_jpeg(full_tiff("II")))
        opened = []
        real_open = open

        def spy_open(f, *a, **k):
            opened.append(f)
            return real_open(f, *a, **k)
        for attrs in (0x400000, 0x40000, 0x1000):        # RECALL_ON_DATA_ACCESS/OPEN, OFFLINE
            with mock.patch.object(ox.os, "stat", lambda p, *a, **k: _fake_stat(attrs)), \
                    mock.patch("builtins.open", spy_open):
                self.assertEqual(org_extract_text(pdf), "")
                self.assertIsNone(org_pdf_page_count(pdf))
                self.assertEqual(org_read_exif(jpg), {})
                self.assertIsNone(org_image_size(jpg))
        self.assertEqual(opened, [])
        # angeheftet (PINNED 0x80000) = lokal vorhanden → wird gelesen
        with mock.patch.object(ox.os, "stat", lambda p, *a, **k: _fake_stat(0x80000 | 0x20)):
            self.assertIn("Jürgen", org_extract_text(pdf))
            self.assertEqual(org_image_size(jpg), (640, 480))
        # Schalter aus → wird gelesen
        with mock.patch.object(ox, "ORG_EXTRACT_SKIP_CLOUD", False), \
                mock.patch.object(ox.os, "stat", lambda p, *a, **k: _fake_stat(0x400000)):
            self.assertIn("Jürgen", org_extract_text(pdf))

    def test_cloud_placeholder_skips_qtpdf_too(self):
        ox.ORG_EXTRACT_USE_QTPDF = True
        p = self.w("cloud_scan.pdf", simple_pdf(b"q Q", flate=False))
        with mock.patch.object(ox.os, "stat", lambda p, *a, **k: _fake_stat(0x400000)), \
                mock.patch.object(ox, "_org_pdf_text_qt",
                                  side_effect=AssertionError("QtPdf darf nicht laufen")):
            self.assertEqual(org_extract_text(p), "")

    def test_stat_errors_are_neutral(self):
        p = self.w("x.txt", b"Hallo")
        for exc in (PermissionError("verweigert"), OSError(22, "ungueltig"), ValueError("nul")):
            with mock.patch.object(ox.os, "stat", side_effect=exc):
                self.assertEqual(org_extract_text(p), "")
                self.assertEqual(org_read_exif(p), {})
                self.assertIsNone(org_image_size(p))

    # ------------------------------------------------------------------ Datensicherheit
    def test_never_modifies_user_files(self):
        files = {
            "Lebenslauf.pdf": simple_pdf(CONTENT_LEBENSLAUF),
            "Oma_80_Geburtstag.jpg": build_jpeg(full_tiff("II")),
            "Zeugnis.docx": _mkzip({"word/document.xml": b"<w:t>Zeugnis</w:t>"}),
            "Notizen.txt": "Grüße".encode("cp1252"),
            "Brief.rtf": b"{\\rtf1 Hallo}",
            "Bild.png": build_png(4, 4, full_tiff("MM")),
        }
        d = self.tmp / "sicher"
        paths = [self.w("sicher/" + n, b) for n, b in files.items()]
        before = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns,
                      p.stat().st_size) for p in paths}
        listing = sorted(os.listdir(str(d)))
        for _ in range(3):
            for p in paths:
                org_extract_text(p)
                org_read_exif(p)
                org_image_size(p)
                org_pdf_page_count(p)
        after = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns,
                     p.stat().st_size) for p in paths}
        self.assertEqual(before, after)
        self.assertEqual(sorted(os.listdir(str(d))), listing)   # keine Temp-/Lock-Dateien

    # ------------------------------------------------------------------ Threads
    def test_threads_give_same_results_as_sequential(self):
        paths = [
            self.w("t/cv.pdf", simple_pdf(CONTENT_LEBENSLAUF)),
            self.w("t/a.txt", ("Kündigung " * 3000).encode("cp1252")),
            self.w("t/b.html", b"<p>Arbeitsvertrag</p><script>x</script>"),
            self.w("t/c.docx", _mkzip({"word/document.xml": b"<w:t>Zeugnis</w:t>"})),
            self.w("t/d.rtf", b"{\\rtf1\\ansi Gr\\'fc\\'dfe}"),
            self.w("t/e.jpg", build_jpeg(full_tiff("II"))),
        ]
        expected = [(org_extract_text(p), org_read_exif(p), org_image_size(p)) for p in paths]
        errors = []

        def work(seed):
            rnd = random.Random(seed)
            for _ in range(60):
                i = rnd.randrange(len(paths))
                p = paths[i]
                got = (org_extract_text(p), org_read_exif(p), org_image_size(p))
                if got != expected[i]:
                    errors.append((p.name, got))
        ts = [threading.Thread(target=work, args=(s,)) for s in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)
        self.assertEqual(errors, [])

    # ------------------------------------------------------------------ Leistung
    def test_performance_60k_files(self):
        root = self.tmp / "perf60k"
        root.mkdir(exist_ok=True)
        jpg = build_jpeg(full_tiff("II"))
        txt = ("Rechnung Nr. 4711 Stromabrechnung Grüße " * 40).encode("cp1252")
        paths = []
        for i in range(60000):
            m = i % 4
            if m == 0:
                name, data = "IMG_%05d.jpg" % i, jpg
            elif m == 1:
                name, data = "Notiz_%05d.txt" % i, txt
            elif m == 2:
                name, data = "setup_%05d.exe" % i, b"MZ"
            else:
                name, data = "Lied_%05d.mp3" % i, b"ID3"
            p = root / name
            with open(str(p), "wb") as fh:
                fh.write(data)
            paths.append(p)
        t0 = time.time()
        n_text = n_exif = 0
        for p in paths:
            if p.suffix == ".jpg":
                if org_read_exif(p).get("make") == "Canon":
                    n_exif += 1
                org_image_size(p)
            else:
                if org_extract_text(p):
                    n_text += 1
        dt = time.time() - t0
        self.assertEqual(n_exif, 15000)
        self.assertEqual(n_text, 15000)
        self.assertLess(dt, 60.0, "60 000 Dateien dauerten %.1f s" % dt)

    def test_large_text_files_fast(self):
        p = self.w("gross.log", ("2024-01-01 INFO Dienst gestartet äöü\n" * 20000).encode())
        t0 = time.time()
        for _ in range(500):
            org_extract_text(p)
        # vorher ~7,6 ms pro Datei (ganze 256 KB bereinigt), jetzt deutlich darunter
        self.assertLess((time.time() - t0) / 500, 0.004)

    # ------------------------------------------------------------------ Namensregel
    def test_new_top_level_names_have_prefix(self):
        import inspect
        for name, obj in vars(ox).items():
            if name.startswith("__") or inspect.ismodule(obj):
                continue
            if getattr(obj, "__module__", "org_extract") != "org_extract" and \
                    not name.isupper() and not name.startswith(("ORG_", "_ORG_")):
                continue
            self.assertTrue(name.startswith(("org_", "Org", "ORG_", "_org_", "_ORG_")), name)
        # importierte Module dürfen im großen Programm keine bestehenden Namen überdecken
        src = Path(ox.__file__).read_text(encoding="utf-8")
        imported = set(re.findall(r"^import (\w+)", src, re.M))
        self.assertTrue(imported <= {"base64", "binascii", "codecs", "html", "os", "re",
                                     "stat", "struct", "threading", "zipfile", "zlib"},
                        imported)


if __name__ == "__main__":
    prog = unittest.main(exit=False, verbosity=1)
    res = prog.result
    print("SUMMARY: %d Tests, %d Fehler, %d Ausnahmen, %d übersprungen (Python %s)" % (
        res.testsRun, len(res.failures), len(res.errors), len(res.skipped),
        sys.version.split()[0]))
    sys.exit(0 if res.wasSuccessful() else 1)
