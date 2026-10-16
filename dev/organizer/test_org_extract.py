# Tests für org_extract – alle Testdateien werden hier programmatisch erzeugt.
from __future__ import annotations

import os
import random
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
import zipfile
import zlib
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import org_extract as ox  # noqa: E402
from org_extract import (org_extract_text, org_image_size, org_pdf_page_count,  # noqa: E402
                         org_read_exif)

try:
    from PySide6.QtCore import QCoreApplication, QThread
    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False

try:
    import PySide6.QtPdf  # noqa: F401
    HAVE_QTPDF = True
except Exception:  # pragma: no cover
    HAVE_QTPDF = False

LEBENSLAUF = "Lebenslauf – Jürgen Müller, geboren 1990"


# ---------------------------------------------------------------------------
# PDF-Bausteine
# ---------------------------------------------------------------------------
def pdf_stream(data: bytes, extra: bytes = b"", flate: bool = False) -> bytes:
    if flate:
        data = zlib.compress(data)
        extra += b" /Filter /FlateDecode"
    return b"<< /Length %d%s >>\nstream\n" % (len(data), extra) + data + b"\nendstream"


def build_pdf(objs, root: int = 1, trailer_extra: bytes = b"", trailer: bool = True,
              version: bytes = b"1.4") -> bytes:
    out = bytearray(b"%PDF-" + version + b"\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for i, body in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    if trailer:
        x = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        for o in offs:
            out += b"%010d 00000 n \n" % o
        out += b"trailer\n<< /Size %d /Root %d 0 R%s >>\nstartxref\n%d\n%%%%EOF\n" % (
            len(objs) + 1, root, trailer_extra, x)
    return bytes(out)


HELV = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"


def simple_pdf(content: bytes, flate: bool = True, font: bytes = HELV,
               trailer_extra: bytes = b"", extra_filter: bytes = b"") -> bytes:
    if extra_filter:
        cstream = b"<< /Length %d /Filter %s >>\nstream\n" % (len(content), extra_filter) \
            + content + b"\nendstream"
    else:
        cstream = pdf_stream(content, flate=flate)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        cstream,
        font,
    ]
    return build_pdf(objs, trailer_extra=trailer_extra)


# "Lebenslauf – Jürgen Müller, geboren 1990": – als WinAnsi 0x96 (oktal \226),
# ü einmal als Oktal-Escape, einmal als Rohbyte 0xFC.
CONTENT_LEBENSLAUF = (b"BT /F1 12 Tf 72 760 Td (Lebenslauf \\226 J\\374rgen M\xfcller, "
                      b"geboren 1990) Tj 0 -16 Td [(Zeug) 20 (nis) -300 (Abitur)] TJ ET")


def multipage_pdf(n: int) -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None, HELV]
    kids = []
    for i in range(n):
        text = ("Seite %d " % (i + 1)) + "Inhalt " * 18
        content = b"BT /F1 10 Tf 50 700 Td (" + text.encode() + b") Tj ET"
        objs.append(b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 3 0 R >> >> "
                    b"/Contents %d 0 R >>" % (len(objs) + 2))
        kids.append(len(objs))
        objs.append(pdf_stream(content, flate=True))
    objs[1] = b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids) + \
        b"] /Count %d >>" % n
    return build_pdf(objs)


def objstm_pdf() -> bytes:
    """PDF 1.5: Type0-Font mit ToUnicode, Seitenbaum + Font im Objektstrom."""
    text = "Gehaltsabrechnung März 2024"
    letters = []
    for ch in text:
        if ch not in letters and not ch.isdigit() and ch not in (" ", "ä"):
            letters.append(ch)
    codes = {ch: i + 1 for i, ch in enumerate(letters)}
    for d in "0123456789":
        codes[d] = 0x50 + int(d)
    codes["ä"] = 0x60
    codes[" "] = 0x61
    hexs = "".join("%04X" % codes[ch] for ch in text)
    content = ("BT /F1 11 Tf 50 750 Td <%s> Tj ET" % hexs).encode()
    bfchar = "".join("<%04X> <%04X>\n" % (codes[ch], ord(ch)) for ch in letters)
    cmap = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
            "1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
            "%d beginbfchar\n%sendbfchar\n"
            "2 beginbfrange\n<0050> <0059> <0030>\n<0060> <0061> [<00E4> <0020>]\nendbfrange\n"
            "endcmap CMapName currentdict /CMap defineresource pop end end"
            % (len(letters), bfchar)).encode()
    inner = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> "
           b"/Contents 4 0 R >>",
        5: b"<< /Type /Font /Subtype /Type0 /BaseFont /ABCDEF+Calibri /Encoding /Identity-H "
           b"/DescendantFonts [7 0 R] /ToUnicode 6 0 R >>",
        7: b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /ABCDEF+Calibri >>",
    }
    header = b""
    body = b""
    for num, ob in inner.items():
        header += b"%d %d " % (num, len(body))
        body += ob + b"\n"
    stm = header + body
    objs = [
        b"<< /Type /Foo >>",                                       # 1 (Platzhalter, wird
        b"<< /Type /Foo >>",                                       # 2  im Objektstrom
        b"<< /Type /Foo >>",                                       # 3  definiert)
        pdf_stream(content, flate=True),                           # 4
        b"<< /Type /Foo >>",                                       # 5
        pdf_stream(cmap, flate=True),                              # 6
        b"<< /Type /Foo >>",                                       # 7
        pdf_stream(stm, b" /Type /ObjStm /N %d /First %d" % (len(inner), len(header)),
                   flate=True),                                    # 8
        pdf_stream(b"\x00" * 12, b" /Type /XRef /Size 10 /Root 1 0 R /W [1 2 1]"),   # 9
    ]
    # Platzhalter entfernen: Objekte 1,2,3,5,7 nur im Objektstrom
    out = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    for i, body_ in enumerate(objs, 1):
        if i in inner:
            continue
        out += b"%d 0 obj\n" % i + body_ + b"\nendobj\n"
    out += b"startxref\n0\n%%EOF\n"
    return bytes(out)


def lzw_encode(data: bytes) -> bytes:
    """Kleiner LZW-Kodierer (nur 9-Bit-Codes, reicht für kurze Ströme)."""
    d = {bytes([i]): i for i in range(256)}
    nxt = 258
    acc = 0
    nbits = 0
    out = bytearray()

    def emit(code):
        nonlocal acc, nbits
        acc = (acc << 9) | code
        nbits += 9
        while nbits >= 8:
            nbits -= 8
            out.append((acc >> nbits) & 0xFF)
        acc &= (1 << nbits) - 1

    emit(256)
    w = b""
    for c in data:
        wc = w + bytes([c])
        if wc in d:
            w = wc
        else:
            emit(d[w])
            d[wc] = nxt
            nxt += 1
            w = bytes([c])
    if w:
        emit(d[w])
    emit(257)
    if nbits:
        out.append((acc << (8 - nbits)) & 0xFF)
    assert nxt < 500
    return bytes(out)


# ---------------------------------------------------------------------------
# EXIF-/Bild-Bausteine
# ---------------------------------------------------------------------------
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


def build_tiff(order: str, ifd0, exif=None, gps=None, exif_ptr=None, gps_ptr=None) -> bytes:
    E = "<" if order == "II" else ">"
    ifd0 = list(ifd0)
    blocks = [ifd0]
    if exif is not None:
        ifd0.append((0x8769, 4, [0]))
        blocks.append(list(exif))
    elif exif_ptr is not None:
        ifd0.append((0x8769, 4, [exif_ptr]))
    if gps is not None:
        ifd0.append((0x8825, 4, [0]))
        blocks.append(list(gps))
    elif gps_ptr is not None:
        ifd0.append((0x8825, 4, [gps_ptr]))
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
    out = bytearray((b"II*\x00" if order == "II" else b"MM\x00*") + struct.pack(E + "I", 8))
    for b in blocks:
        ent = bytearray(struct.pack(E + "H", len(b)))
        for tag, typ, val in sorted(b, key=lambda t: t[0]):
            raw = _pack_val(E, typ, val)
            size = {2: 1, 3: 2, 4: 4, 5: 8}[typ]
            cnt = len(raw) // size
            if len(raw) <= 4:
                field = raw.ljust(4, b"\x00")
            else:
                field = struct.pack(E + "I", data_off + len(data))
                data += raw
                if len(data) % 2:
                    data += b"\x00"
            ent += struct.pack(E + "HHI", tag, typ, cnt) + field
        ent += struct.pack(E + "I", 0)
        out += ent
    out += data
    return bytes(out)


def jpeg_seg(marker: int, payload: bytes) -> bytes:
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


def build_jpeg(tiff: bytes = b"", w: int = 640, h: int = 480, sof: int = 0xC0,
               pad_app: int = 0) -> bytes:
    out = b"\xff\xd8" + jpeg_seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    if tiff:
        out += jpeg_seg(0xE1, b"Exif\x00\x00" + tiff)
    if pad_app:
        out += jpeg_seg(0xE2, b"ICC_PROFILE\x00" + b"\x55" * pad_app)
    out += jpeg_seg(0xDB, b"\x00" + bytes(range(64)))
    out += jpeg_seg(sof, b"\x08" + struct.pack(">HH", h, w) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    out += jpeg_seg(0xDA, b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00") + b"\x12\x34\x56" * 50
    return out + b"\xff\xd9"


def full_tiff(order: str) -> bytes:
    return build_tiff(order, [
        (0x010F, 2, "Canon"), (0x0110, 2, "Canon EOS 80D"), (0x0131, 2, "GIMP 2.10"),
        (0x0132, 2, "2024:01:01 12:00:00"), (0x0112, 3, [6])],
        exif=[(0x9003, 2, "2023:07:14 09:30:15"), (0x9004, 2, "2023:07:14 09:31:00"),
              (0xA002, 4, [4000]), (0xA003, 4, [3000])],
        gps=[(1, 2, "N"), (2, 5, [(47, 1), (48, 1), (0, 1)]),
             (3, 2, "E"), (4, 5, [(13, 1), (0, 1), (0, 100)])])


def png_chunk(ctype: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data))


def build_png(w: int, h: int, exif: bytes = b"") -> bytes:
    out = b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    if exif:
        out += png_chunk(b"eXIf", exif)
    out += png_chunk(b"IDAT", zlib.compress(b"\x00" * (w * 3 + 1)))
    return out + png_chunk(b"IEND", b"")


def riff_webp(chunk: bytes, extra: bytes = b"") -> bytes:
    body = b"WEBP" + chunk + extra
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ---------------------------------------------------------------------------
class OrgExtractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="orgx_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def w(self, name: str, data: bytes) -> Path:
        p = self.tmp / name
        p.write_bytes(data)
        return p

    def zipfile_(self, name: str, members: dict) -> Path:
        p = self.tmp / name
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
            for n, data in members.items():
                zf.writestr(n, data)
        return p

    def no_qt(self):
        return mock.patch.object(ox, "ORG_EXTRACT_USE_QTPDF", False)

    # ------------------------------------------------------------------ PDF
    def test_pdf_flate_german(self):
        p = self.w("cv.pdf", simple_pdf(CONTENT_LEBENSLAUF, flate=True))
        with self.no_qt():
            t = org_extract_text(p)
        self.assertIn(LEBENSLAUF, t)
        self.assertIn("Zeugnis Abitur", t)

    def test_pdf_uncompressed_operators(self):
        content = (b"BT /F1 10 Tf 14 TL 50 700 Td (Arbeitsvertrag zwischen) Tj T* "
                   b"(der Firma) Tj (Muster GmbH) ' 1 2 (und Herrn Meier) \" ET")
        p = self.w("vertrag.pdf", simple_pdf(content, flate=False))
        with self.no_qt():
            t = org_extract_text(p)
        self.assertEqual(t, "Arbeitsvertrag zwischen der Firma Muster GmbH und Herrn Meier")

    def test_pdf_hex_strings(self):
        content = b"BT /F1 10 Tf 50 700 Td <4C6562656E736C617566> Tj 0 -12 Td <4dFC 6c6c 6572> Tj ET"
        p = self.w("hex.pdf", simple_pdf(content, flate=True))
        with self.no_qt():
            self.assertEqual(org_extract_text(p), "Lebenslauf Müller")

    def test_pdf_objstm_type0_tounicode(self):
        p = self.w("gehalt.pdf", objstm_pdf())
        with self.no_qt():
            self.assertEqual(org_extract_text(p), "Gehaltsabrechnung März 2024")
        self.assertEqual(org_pdf_page_count(p), 1)

    def test_pdf_differences_encoding(self):
        font = (b"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman /Encoding "
                b"<< /Type /Encoding /BaseEncoding /WinAnsiEncoding "
                b"/Differences [1 /adieresis /germandbls 3 /uni00FC] >> >>")
        content = b"BT /F1 10 Tf 50 700 Td (Stra\\002e M\\001rz \\003ber) Tj ET"
        p = self.w("diff.pdf", simple_pdf(content, font=font))
        with self.no_qt():
            self.assertEqual(org_extract_text(p), "Straße März über")

    def test_pdf_form_xobject_and_inline_image(self):
        form = pdf_stream(b"BT /F1 10 Tf 0 0 Td (Zeugnis der Reife) Tj ET",
                          b" /Type /XObject /Subtype /Form /BBox [0 0 100 100] "
                          b"/Resources << /Font << /F1 5 0 R >> >>", flate=True)
        content = (b"q BI /W 4 /H 1 /BPC 8 /CS /G ID \x28\x29\x5c\xff EI Q "
                   b"BT /F1 10 Tf 50 700 Td (Abschluss:) Tj ET q /X1 Do Q")
        objs = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 /Resources << /Font << /F1 5 0 R >> "
            b"/XObject << /X1 6 0 R >> >> >>",                       # geerbte Ressourcen
            b"<< /Type /Page /Parent 2 0 R /Contents [4 0 R] >>",
            pdf_stream(content, flate=True),
            HELV,
            form,
        ]
        p = self.w("form.pdf", build_pdf(objs))
        with self.no_qt():
            self.assertEqual(org_extract_text(p), "Abschluss: Zeugnis der Reife")

    def test_pdf_first_pages_only_and_max_chars(self):
        p = self.w("multi.pdf", multipage_pdf(5))
        with self.no_qt():
            t = org_extract_text(p)
            self.assertIn("Seite 1", t)
            self.assertIn("Seite 3", t)
            self.assertNotIn("Seite 5", t)
            short = org_extract_text(p, max_chars=50)
        self.assertLessEqual(len(short), 50)
        self.assertTrue(short.startswith("Seite 1"))
        self.assertEqual(org_pdf_page_count(p), 5)

    def test_pdf_tree_at_end_of_big_file(self):
        rnd = random.Random(7)
        filler = bytes(rnd.getrandbits(8) for _ in range(3 * 1024 * 1024 + 500000))
        objs = [
            pdf_stream(CONTENT_LEBENSLAUF, flate=True),                         # 1
            pdf_stream(filler, b" /Type /XObject /Subtype /Image /Filter /DCTDecode"),  # 2
            HELV,                                                               # 3
            b"<< /Type /Page /Parent 5 0 R /Resources << /Font << /F1 3 0 R >> >> "
            b"/Contents 1 0 R >>",                                              # 4
            b"<< /Type /Pages /Kids [4 0 R 7 0 R] /Count 2 >>",                 # 5
            b"<< /Type /Catalog /Pages 5 0 R >>",                               # 6
            b"<< /Type /Page /Parent 5 0 R /Contents 1 0 R >>",                 # 7
        ]
        p = self.w("big.pdf", build_pdf(objs, root=6))
        self.assertGreater(p.stat().st_size, 3 * 1024 * 1024)
        with self.no_qt():
            self.assertIn(LEBENSLAUF, org_extract_text(p))
        self.assertEqual(org_pdf_page_count(p), 2)

    def test_pdf_runlength_and_a85(self):
        import base64
        content = b"BT /F1 10 Tf 50 700 Td (Versicherungsschein Nr. 123) Tj ET"
        rl = bytes([len(content) - 1]) + content + b"\x80"
        p = self.w("rl.pdf", simple_pdf(rl, extra_filter=b"/RunLengthDecode"))
        a85 = base64.a85encode(zlib.compress(content), adobe=True)[2:]
        p2 = self.w("a85.pdf", simple_pdf(a85, extra_filter=b"[/ASCII85Decode /FlateDecode]"))
        with self.no_qt():
            self.assertEqual(org_extract_text(p), "Versicherungsschein Nr. 123")
            self.assertEqual(org_extract_text(p2), "Versicherungsschein Nr. 123")

    def test_pdf_broken_inputs(self):
        good = simple_pdf(CONTENT_LEBENSLAUF + b" " + b"BT (Mehr Text hier) Tj ET " * 200)
        cases = {
            "trunc.pdf": good[:len(good) // 2],
            "trunc2.pdf": good[:300],
            "garbage.pdf": bytes(random.Random(1).getrandbits(8) for _ in range(20000)),
            "empty.pdf": b"",
            "header_only.pdf": b"%PDF-1.7\n",
            "nested.pdf": b"%PDF-1.4\n1 0 obj\n" + b"[" * 5000 + b"<<" * 5000 + b"\nendobj\n",
            "loop.pdf": build_pdf([b"<< /Type /Catalog /Pages 2 0 R >>",
                                   b"<< /Type /Pages /Kids [2 0 R 3 0 R] /Count 1 >>",
                                   b"<< /Type /Pages /Kids [2 0 R] /Parent 2 0 R >>",
                                   b"<< /Length 4 0 R >>\nstream\nabc\nendstream"]),
        }
        with self.no_qt():
            for name, data in cases.items():
                p = self.w(name, data)
                t = org_extract_text(p)
                self.assertIsInstance(t, str, name)
                n = org_pdf_page_count(p)
                self.assertTrue(n is None or isinstance(n, int), name)
            self.assertIn("Lebenslauf", org_extract_text(self.tmp / "trunc.pdf"))
            self.assertEqual(org_extract_text(self.tmp / "garbage.pdf"), "")
        self.assertIsNone(org_pdf_page_count(self.tmp / "garbage.pdf"))

    def test_pdf_page_tree_missing(self):
        pdf = simple_pdf(CONTENT_LEBENSLAUF)
        no_trailer = pdf[:pdf.index(b"xref")]
        no_tree = no_trailer.replace(b"/Type /Catalog", b"/Type /Katalog")
        p = self.w("notrailer.pdf", no_tree)
        with self.no_qt():
            self.assertIn(LEBENSLAUF, org_extract_text(p))
        self.assertEqual(org_pdf_page_count(p), 1)
        # gar kein Seitenobjekt: Inhaltsströme werden direkt gelesen
        orphan = build_pdf([pdf_stream(CONTENT_LEBENSLAUF, flate=True)], trailer=False)
        p2 = self.w("orphan.pdf", orphan)
        with self.no_qt():
            self.assertIn(LEBENSLAUF, org_extract_text(p2))

    def test_pdf_encrypted_returns_empty(self):
        p = self.w("enc.pdf", simple_pdf(CONTENT_LEBENSLAUF, trailer_extra=b" /Encrypt 9 0 R"))
        with self.no_qt():
            self.assertEqual(org_extract_text(p), "")
        self.assertEqual(org_pdf_page_count(p), 1)

    def test_pdf_page_count_linearized(self):
        data = (b"%PDF-1.6\n1 0 obj\n<< /Linearized 1 /L 1234 /N 7 /T 99 >>\nendobj\n")
        self.assertEqual(org_pdf_page_count(self.w("lin.pdf", data)), 7)

    # ------------------------------------------------------- Threads / QtPdf
    def test_pdf_in_threading_thread(self):
        p = self.w("thread.pdf", simple_pdf(CONTENT_LEBENSLAUF))
        results = []
        errors = []

        def work():
            try:
                for _ in range(5):
                    results.append(org_extract_text(p))
            except Exception as e:   # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=work) for _ in range(4)]
        with self.no_qt():                       # Patch nur im Hauptthread
            for t in threads:
                t.start()
            for t in threads:
                t.join(60)
        self.assertFalse(errors)
        self.assertEqual(len(results), 20)
        self.assertTrue(all(LEBENSLAUF in r for r in results))

    @unittest.skipUnless(HAVE_QT, "PySide6 fehlt")
    def test_pdf_in_qthread_without_app(self):
        self.assertIsNone(QCoreApplication.instance())
        p = self.w("qthread.pdf", simple_pdf(CONTENT_LEBENSLAUF))

        class Worker(QThread):
            def __init__(self):
                super().__init__()
                self.result = None
                self.error = None

            def run(self):
                try:
                    self.result = org_extract_text(p)
                except Exception as e:   # pragma: no cover
                    self.error = e

        wk = Worker()
        wk.start()
        self.assertTrue(wk.wait(60000))
        self.assertIsNone(wk.error)
        self.assertIn(LEBENSLAUF, wk.result)

    @unittest.skipUnless(HAVE_QTPDF, "QtPdf fehlt")
    def test_qtpdf_fallback_in_worker_threads(self):
        # LZW kann der reine Python-Leser nicht → Rückfall auf QtPdf (pdfium)
        content = b"BT /F1 12 Tf 72 700 Td (Mietvertrag Wohnung Berlin) Tj ET"
        p = self.w("lzw.pdf", simple_pdf(lzw_encode(content), extra_filter=b"/LZWDecode"))
        with self.no_qt():
            self.assertEqual(org_extract_text(p), "")
        self.assertIsNone(QCoreApplication.instance())
        out = {}

        def work(key):
            out[key] = org_extract_text(p)

        ts = [threading.Thread(target=work, args=(i,)) for i in range(3)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)
        for i in range(3):
            self.assertEqual(out.get(i), "Mietvertrag Wohnung Berlin")

        class Worker(QThread):
            res = None

            def run(self):
                Worker.res = ox._org_pdf_text_qt(p, 4000)

        wk = Worker()
        wk.start()
        self.assertTrue(wk.wait(60000))
        self.assertIn("Mietvertrag Wohnung Berlin", Worker.res)
        # kaputte Datei: QtPdf liefert "" statt Absturz/Ausnahme
        bad = self.w("bad_qt.pdf", b"%PDF-1.4\n garbage \x00\xff")
        self.assertEqual(org_extract_text(bad), "")

    # --------------------------------------------------------------- Office
    def test_docx(self):
        W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        doc = ('<?xml version="1.0" encoding="UTF-8"?><w:document %s><w:body>'
               '<w:p><w:pPr><w:pStyle w:val="Title"/><w:tabs><w:tab w:val="left" w:pos="100"/></w:tabs></w:pPr>'
               '<w:r><w:t>Lebens</w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>lauf</w:t></w:r></w:p>'
               '<w:p><w:r><w:t xml:space="preserve">Jürgen Müller &amp; Co</w:t></w:r>'
               '<w:r><w:tab/></w:r><w:r><w:t>Berufserfahrung</w:t></w:r></w:p>'
               '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> '
               'HYPERLINK "http://x" </w:instrText></w:r><w:r><w:t>Link</w:t></w:r></w:p>'
               '<w:p><w:del><w:r><w:delText>Gelöscht</w:delText></w:r></w:del></w:p>'
               '</w:body></w:document>' % W).encode("utf-8")
        hdr = ('<w:hdr %s><w:p><w:r><w:t>Max Kopfzeile</w:t></w:r></w:p></w:hdr>' % W).encode()
        p = self.zipfile_("Lebenslauf_final_v2 (1).docx", {
            "[Content_Types].xml": b'<?xml version="1.0"?><Types/>',
            "_rels/.rels": b'<?xml version="1.0"?><Relationships/>',
            "word/document.xml": doc, "word/header1.xml": hdr})
        t = org_extract_text(p)
        self.assertIn("Lebenslauf", t)
        self.assertIn("Jürgen Müller & Co Berufserfahrung", t)
        self.assertIn("Link", t)
        self.assertIn("Max Kopfzeile", t)
        self.assertNotIn("HYPERLINK", t)
        self.assertNotIn("Gelöscht", t)
        # docm/dotx gleich behandelt
        shutil.copy(p, self.tmp / "vorlage.dotx")
        self.assertIn("Lebenslauf", org_extract_text(self.tmp / "vorlage.dotx"))

    def test_xlsx(self):
        sst = (b'<?xml version="1.0"?><sst count="3"><si><t>Lohnsteuerbescheinigung</t></si>'
               b'<si><r><t>Brut</t></r><r><t>to</t></r></si><si><t>Netto &lt;2024&gt;</t></si></sst>')
        p = self.zipfile_("lohn.xlsx", {"[Content_Types].xml": b"<Types/>",
                                        "xl/sharedStrings.xml": sst})
        self.assertEqual(org_extract_text(p), "Lohnsteuerbescheinigung Brutto Netto <2024>")

    def test_pptx_slide_order_and_limit(self):
        words = {1: "Eins", 2: "Zwei", 3: "Drei", 4: "Vier", 5: "Fünf", 6: "Sechs", 10: "Zehn"}
        members = {"[Content_Types].xml": b"<Types/>"}
        for n, wd in sorted(words.items(), key=lambda kv: -kv[0]):
            members["ppt/slides/slide%d.xml" % n] = (
                '<p:sld><p:txBody><a:p><a:r><a:t>%s</a:t></a:r></a:p></p:txBody></p:sld>' % wd
            ).encode()
        members["ppt/slides/_rels/slide1.xml.rels"] = b"<Relationships/>"
        p = self.zipfile_("vortrag.pptx", members)
        self.assertEqual(org_extract_text(p), "Eins Zwei Drei Vier Fünf")

    def test_odt(self):
        content = ('<?xml version="1.0"?><office:document-content><office:body><office:text>'
                   '<text:h>Arbeitszeugnis</text:h><text:p>Frau<text:s/>Erika<text:tab/>'
                   'Mustermann</text:p></office:text></office:body></office:document-content>').encode()
        p = self.zipfile_("zeugnis.odt", {"mimetype": b"application/vnd.oasis.opendocument.text",
                                          "content.xml": content})
        self.assertEqual(org_extract_text(p), "Arbeitszeugnis Frau Erika Mustermann")

    def test_zip_guards(self):
        bomb = b"<w:t>" + b"A" * 3000000 + b"</w:t>"
        p = self.zipfile_("bomb.docx", {"word/document.xml": bomb})
        info = zipfile.ZipFile(p).getinfo("word/document.xml")
        self.assertGreater(info.file_size / info.compress_size, 200)
        self.assertEqual(org_extract_text(p), "")
        zi = zipfile.ZipInfo("a.xml")
        zi.file_size, zi.compress_size = 31 * 1024 * 1024, 10 * 1024 * 1024
        self.assertFalse(ox._org_zip_member_ok(zi))
        zi.file_size, zi.compress_size = 200000, 20000
        self.assertTrue(ox._org_zip_member_ok(zi))
        zi.flag_bits = 0x1
        self.assertFalse(ox._org_zip_member_ok(zi))
        # verschlüsselter Eintrag (Flag in lokalem und zentralem Header gesetzt)
        p2 = self.zipfile_("enc.docx", {"word/document.xml": b"<w:t>Geheim</w:t>"})
        raw = bytearray(p2.read_bytes())
        for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            i = raw.find(sig)
            while i >= 0:
                raw[i + off] |= 0x1
                i = raw.find(sig, i + 4)
        p2.write_bytes(bytes(raw))
        self.assertEqual(org_extract_text(p2), "")
        # keine ZIP-Datei / kaputtes ZIP
        self.assertEqual(org_extract_text(self.w("fake.docx", b"\xd0\xcf\x11\xe0" + b"\x00" * 600)), "")
        good = self.zipfile_("cut.xlsx", {"xl/sharedStrings.xml": b"<sst><si><t>X</t></si></sst>"})
        self.assertEqual(org_extract_text(self.w("cut2.xlsx", good.read_bytes()[:40])), "")

    # ----------------------------------------------------------------- RTF
    def test_rtf(self):
        rtf = (b"{\\rtf1\\ansi\\ansicpg1252\\deff0{\\fonttbl{\\f0\\fswiss Arial;}}"
               b"{\\colortbl;\\red0\\green0\\blue0;}{\\*\\generator Riched20;}"
               b"{\\info{\\title Geheimtitel}}\\viewkind4\\uc1\\pard\\f0\\fs20 "
               b"Zeugnis f\\'fcr J\\'fcrgen\\par Gr\\u246?\\u223?e\\tab {\\b fett}"
               b"{\\pict\\wmetafile8 0123456789abcdef}\\endash Ende\\par}")
        p = self.w("zeugnis.rtf", rtf)
        self.assertEqual(org_extract_text(p),
                         "Zeugnis für Jürgen Größe fett–Ende")
        self.assertEqual(org_extract_text(self.w("nortf.rtf", b"kein rtf")), "")
        deep = b"{\\rtf1 " + b"{" * 3000 + b"tief" + b"}" * 3000 + b"}"
        self.assertEqual(org_extract_text(self.w("deep.rtf", deep)), "tief")

    # ---------------------------------------------------------- Klartext
    def test_text_encodings(self):
        s = "Kündigung der Wohnung – Straße 5"
        self.assertEqual(org_extract_text(self.w("a.txt", s.encode("cp1252"))), s)
        self.assertEqual(org_extract_text(self.w("b.txt", s.encode("utf-16"))), s)
        self.assertEqual(org_extract_text(self.w("c.txt", s.encode("utf-16-le"))), s)
        self.assertEqual(org_extract_text(self.w("d.md", b"\xef\xbb\xbf" + s.encode())), s)
        self.assertEqual(org_extract_text(self.w("e.csv", ("Name;Ort\n" + s).encode())),
                         "Name;Ort " + s)
        # utf-8, Leselimit 256 KB schneidet ein Umlaut-Zeichen
        big = ("x" + "ä" * 140000).encode("utf-8")
        t = org_extract_text(self.w("big.log", big), max_chars=5)
        self.assertEqual(t, "xääää")
        rnd = random.Random(3)
        self.assertEqual(org_extract_text(self.w("bin.txt", bytes(rnd.getrandbits(8) for _ in range(4096)))), "")
        self.assertEqual(org_extract_text(self.w("ws.txt", b"  a \t\r\n\n  b  ")), "a b")

    def test_html_xml_json(self):
        page = ('<html><head><title>Bewerbung</title><style>body{color:red}</style>'
                '<script>var x="<b>";</script></head><body><!-- Kommentar -->'
                '<p>Sehr geehrte&nbsp;Damen &amp; Herren,<br>M&uuml;ller</p></body></html>')
        t = org_extract_text(self.w("x.html", page.encode("cp1252")))
        self.assertEqual(t, "Bewerbung Sehr geehrte Damen & Herren, Müller")
        self.assertEqual(org_extract_text(self.w("x.xml", b"<a><b>Eins</b><c>Zwei</c></a>")),
                         "Eins Zwei")
        self.assertEqual(org_extract_text(self.w("x.json", b'{"k": "Wert"}')), '{"k": "Wert"}')

    def test_eml(self):
        msg = EmailMessage()
        msg["Subject"] = "Ihr Steuerbescheid Übersicht"
        msg["From"] = "finanzamt@example.de"
        msg.set_content("Sehr geehrter Herr Müller, anbei der Bescheid.", charset="utf-8",
                        cte="base64")
        t = org_extract_text(self.w("mail.eml", msg.as_bytes()))
        self.assertIn("Ihr Steuerbescheid Übersicht", t)
        self.assertIn("Sehr geehrter Herr Müller, anbei der Bescheid.", t)

    # -------------------------------------------------------------- EXIF
    def test_exif_jpeg_little_endian(self):
        p = self.w("le.jpg", build_jpeg(full_tiff("II")))
        ex = org_read_exif(p)
        self.assertEqual(ex["datetime"], "2023-07-14 09:30:15")
        self.assertEqual(ex["make"], "Canon")
        self.assertEqual(ex["model"], "Canon EOS 80D")
        self.assertEqual(ex["software"], "GIMP 2.10")
        self.assertEqual(ex["orientation"], 6)
        self.assertEqual((ex["width"], ex["height"]), (4000, 3000))
        self.assertAlmostEqual(ex["gps"][0], 47.8, places=5)
        self.assertAlmostEqual(ex["gps"][1], 13.0, places=5)

    def test_exif_jpeg_big_endian_south_west(self):
        tiff = build_tiff("MM", [(0x010F, 2, "NIKON CORPORATION"), (0x0110, 2, "NIKON D750")],
                          exif=[(0x9004, 2, "2019:12:24 18:05:00")],
                          gps=[(1, 2, "S"), (2, 5, [(22, 1), (54, 1), (3000, 100)]),
                               (3, 2, "W"), (4, 5, [(43, 1), (10, 1), (45, 1)])])
        ex = org_read_exif(self.w("be.jpg", build_jpeg(tiff)))
        self.assertEqual(ex["datetime"], "2019-12-24 18:05:00")
        self.assertEqual(ex["make"], "NIKON CORPORATION")
        self.assertAlmostEqual(ex["gps"][0], -22.908333, places=5)
        self.assertAlmostEqual(ex["gps"][1], -43.179167, places=5)
        self.assertEqual(ex.get("width"), None)

    def test_exif_fallbacks_and_invalid(self):
        tiff = build_tiff("II", [(0x0132, 2, "2015:05:01 07:08:09"), (0x0100, 4, [800]),
                                 (0x0101, 4, [600])],
                          exif=[(0x9003, 2, "0000:00:00 00:00:00")],
                          gps=[(1, 2, "N"), (2, 5, [(0, 0), (0, 0), (0, 0)]),
                               (3, 2, "E"), (4, 5, [(0, 0), (0, 0), (0, 0)])])
        ex = org_read_exif(self.w("fb.jpg", build_jpeg(tiff)))
        self.assertEqual(ex["datetime"], "2015-05-01 07:08:09")
        self.assertEqual((ex["width"], ex["height"]), (800, 600))
        self.assertNotIn("gps", ex)
        tiff = build_tiff("II", [(0x010F, 2, "X")],
                          gps=[(1, 2, "N"), (2, 5, [(47, 0), (1, 1), (0, 1)]),
                               (3, 2, "E"), (4, 5, [(13, 1)])])
        self.assertNotIn("gps", org_read_exif(self.w("gpsbad.jpg", build_jpeg(tiff))))

    def test_exif_loops_and_garbage(self):
        tiff = build_tiff("II", [(0x010F, 2, "Loop")], exif_ptr=8, gps_ptr=0x7FFFFFF0)
        t0 = time.time()
        ex = org_read_exif(self.w("loop.jpg", build_jpeg(tiff)))
        self.assertLess(time.time() - t0, 2)
        self.assertEqual(ex, {"make": "Loop"})
        # IFD zeigt auf sich selbst über ExifIFD und GPS gleichzeitig
        tiff = build_tiff("MM", [(0x0110, 2, "Self")], exif_ptr=8, gps_ptr=8)
        self.assertEqual(org_read_exif(self.w("loop2.jpg", build_jpeg(tiff))), {"model": "Self"})
        rnd = random.Random(5)
        junk = b"Exif\x00\x00" + bytes(rnd.getrandbits(8) for _ in range(3000))
        jpg = b"\xff\xd8" + jpeg_seg(0xE1, junk) + b"\xff\xd9"
        self.assertEqual(org_read_exif(self.w("junk.jpg", jpg)), {})
        full = build_jpeg(full_tiff("II"))
        for cut in (3, 12, 30, 60, 120, 200):
            self.assertIsInstance(org_read_exif(self.w("cut%d.jpg" % cut, full[:cut])), dict)
        self.assertEqual(org_read_exif(self.w("cut12.jpg", full[:12])), {})
        huge = build_tiff("II", [(0x010F, 2, "A" * 100)])
        bad = bytearray(huge)
        struct.pack_into("<H", bad, 8, 65000)               # absurde Eintragsanzahl
        self.assertEqual(org_read_exif(self.w("bad.dng", bytes(bad))), {})
        for i in range(200):                                  # Zufallsmutationen
            mut = bytearray(full)
            for _ in range(10):
                mut[rnd.randrange(len(mut))] = rnd.getrandbits(8)
            self.assertIsInstance(org_read_exif(self.w("mut.jpg", bytes(mut))), dict)

    def test_exif_other_containers(self):
        tiff = full_tiff("MM")
        ex = org_read_exif(self.w("e.png", build_png(10, 10, exif=tiff)))
        self.assertEqual(ex["make"], "Canon")
        self.assertEqual(org_read_exif(self.w("noexif.png", build_png(10, 10))), {})
        ex = org_read_exif(self.w("raw.dng", tiff))
        self.assertEqual(ex["datetime"], "2023-07-14 09:30:15")
        ex = org_read_exif(self.w("raw.nef", full_tiff("II")))
        self.assertEqual(ex["model"], "Canon EOS 80D")
        heic = (struct.pack(">I", 24) + b"ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 3000 +
                b"\x00\x00\x00\x06Exif\x00\x00" + full_tiff("MM") + b"\x00" * 100)
        ex = org_read_exif(self.w("img.heic", heic))
        self.assertEqual(ex["datetime"], "2023-07-14 09:30:15")
        self.assertAlmostEqual(ex["gps"][0], 47.8, places=5)
        webp = riff_webp(b"VP8X" + struct.pack("<I", 10) + b"\x08\x00\x00\x00" + b"\x00" * 6,
                         b"EXIF" + struct.pack("<I", len(tiff)) + tiff)
        self.assertEqual(org_read_exif(self.w("e.webp", webp))["model"], "Canon EOS 80D")
        self.assertEqual(org_read_exif(self.w("doc.txt", b"hallo welt, kein Bild")), {})

    # ---------------------------------------------------------- Bildgröße
    def test_image_sizes(self):
        cases = {
            "a.png": (build_png(123, 45), (123, 45)),
            "a.gif": (b"GIF89a" + struct.pack("<HH", 320, 200) + b"\x00" * 20, (320, 200)),
            "a.bmp": (b"BM" + b"\x00" * 12 + struct.pack("<Iii", 40, 1024, -768) + b"\x00" * 40,
                      (1024, 768)),
            "os2.bmp": (b"BM" + b"\x00" * 12 + struct.pack("<IHH", 12, 64, 32) + b"\x00" * 40,
                        (64, 32)),
            "lossy.webp": (riff_webp(b"VP8 " + struct.pack("<I", 30) + b"\x00\x00\x00" +
                                     b"\x9d\x01\x2a" + struct.pack("<HH", 1920, 1080) + b"\x00" * 20),
                           (1920, 1080)),
            "lossless.webp": (riff_webp(b"VP8L" + struct.pack("<I", 10) + b"\x2f" +
                                        struct.pack("<I", 299 | (199 << 14)) + b"\x00" * 10),
                              (300, 200)),
            "ext.webp": (riff_webp(b"VP8X" + struct.pack("<I", 10) + b"\x00" * 4 +
                                   (4031).to_bytes(3, "little") + (3023).to_bytes(3, "little")),
                         (4032, 3024)),
            "base.jpg": (build_jpeg(full_tiff("II"), 640, 480), (640, 480)),
            "prog.jpg": (build_jpeg(b"", 1600, 1200, sof=0xC2), (1600, 1200)),
            "bigapp.jpg": (build_jpeg(full_tiff("MM"), 800, 600, pad_app=60000), (800, 600)),
        }
        for name, (data, exp) in cases.items():
            self.assertEqual(org_image_size(self.w(name, data)), exp, name)
        many = b"\xff\xd8" + b"".join(jpeg_seg(0xE2, b"\x00" * 60000) for _ in range(12))
        many += jpeg_seg(0xC0, b"\x08" + struct.pack(">HH", 10, 20) + b"\x01\x01\x11\x00")
        self.assertEqual(org_image_size(self.w("many.jpg", many)), (20, 10))
        jpg = build_jpeg(b"", 640, 480)
        self.assertIsNone(org_image_size(self.w("cut.jpg", jpg[:30])))
        self.assertIsNone(org_image_size(self.w("x.png", b"\x89PNG\r\n\x1a\n")))
        self.assertIsNone(org_image_size(self.w("x.dat", b"nichts")))
        self.assertIsNone(org_image_size(self.w("zero.gif", b"GIF89a\x00\x00\x00\x00" + b"\x00" * 10)))

    # ------------------------------------------------- Fehlerfälle allgemein
    def test_missing_dir_permission_and_misc(self):
        missing = self.tmp / "gibt_es_nicht.pdf"
        d = self.tmp / "ordner.pdf"
        d.mkdir(exist_ok=True)
        for p in (missing, d, self.tmp / "ordner.pdf"):
            self.assertEqual(org_extract_text(p), "")
            self.assertEqual(org_read_exif(p), {})
            self.assertIsNone(org_image_size(p))
            self.assertIsNone(org_pdf_page_count(p))
        for ext in (".docx", ".txt", ".rtf", ".jpg", ".xlsx"):
            self.assertEqual(org_extract_text(self.tmp / ("fehlt" + ext)), "")
        files = {
            "p.pdf": simple_pdf(CONTENT_LEBENSLAUF), "p.txt": b"Text", "p.rtf": b"{\\rtf1 x}",
            "p.jpg": build_jpeg(full_tiff("II")), "p.docx": b"PK",
        }
        paths = [self.w(n, b) for n, b in files.items()]
        with mock.patch("builtins.open", side_effect=PermissionError("Zugriff verweigert")):
            for p in paths:
                self.assertEqual(org_extract_text(p), "")
                self.assertEqual(org_read_exif(p), {})
                self.assertIsNone(org_image_size(p))
                self.assertIsNone(org_pdf_page_count(p))
        # str-Pfad, max_chars <= 0, unbekannte Endung, None
        self.assertIn("Lebenslauf", org_extract_text(str(paths[0])))
        self.assertEqual(org_extract_text(paths[0], 0), "")
        self.assertEqual(org_extract_text(self.w("x.exe", b"MZ\x90\x00Lebenslauf")), "")
        self.assertEqual(org_extract_text(None), "")
        self.assertEqual(org_read_exif(None), {})
        self.assertIsNone(org_image_size(None))
        self.assertIsNone(org_pdf_page_count(None))

    def test_names_prefixed(self):
        import inspect
        for name, obj in vars(ox).items():
            if name.startswith("__"):
                continue
            if inspect.ismodule(obj):
                continue
            if getattr(obj, "__module__", "org_extract") != "org_extract" and not name.isupper() \
                    and not name.startswith(("ORG_", "_ORG_")):
                continue                                  # importierte Namen (Path)
            self.assertTrue(name.startswith(("org_", "Org", "ORG_", "_org_", "_ORG_")), name)

    def test_performance_many_files(self):
        p = self.w("perf.pdf", simple_pdf(CONTENT_LEBENSLAUF))
        j = self.w("perf.jpg", build_jpeg(full_tiff("II")))
        t0 = time.time()
        with self.no_qt():
            for _ in range(300):
                org_extract_text(p)
                org_read_exif(j)
                org_image_size(j)
        self.assertLess(time.time() - t0, 10)


if __name__ == "__main__":
    prog = unittest.main(exit=False, verbosity=1)
    res = prog.result
    print("SUMMARY: %d Tests, %d Fehler, %d Ausnahmen, %d übersprungen (Python %s)" % (
        res.testsRun, len(res.failures), len(res.errors), len(res.skipped),
        sys.version.split()[0]))
    sys.exit(0 if res.wasSuccessful() else 1)
