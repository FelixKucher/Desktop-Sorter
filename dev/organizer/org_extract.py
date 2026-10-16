# ===========================================================================
# ORDNER-ASSISTENT – sichere Text- und Metadaten-Extraktion
# ===========================================================================
# Liest Textanfänge (PDF, Office, RTF, Text/HTML/E-Mail), EXIF-Daten und
# Bildgrößen – ohne externe Bibliotheken, threadsicher (läuft im QThread-Worker),
# robust gegen kaputte/abgeschnittene Dateien, Zip-Bomben und Offset-Schleifen.
# Öffentliche Funktionen werfen nie Ausnahmen, sondern liefern neutrale Werte.
# Alle Namen beginnen mit org_ / Org / ORG_ bzw. _org_ (privat).
from __future__ import annotations

import base64
import binascii
import codecs
import html
import os
import re
import stat
import struct
import threading
import zipfile
import zlib
from pathlib import Path

# ---------- Einstellungen ----------
# QtPdf (pdfium) nur als Rückfall, wenn der reine Python-Leser (fast) nichts
# findet (z. B. verschlüsselte PDFs mit leerem Kennwort, exotische Fonts).
# Im Worker-Thread ohne QGuiApplication getestet; alle Aufrufe werden über eine
# Sperre serialisiert. Auf False setzen, um QtPdf komplett zu meiden.
ORG_EXTRACT_USE_QTPDF = True
ORG_EXTRACT_QTPDF_MAX_BYTES = 200 * 1024 * 1024

ORG_EXTRACT_TEXT_EXTS = frozenset({
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".log", ".html", ".htm", ".xhtml",
    ".xml", ".json", ".eml", ".tex"})
ORG_EXTRACT_WORD_EXTS = frozenset({".docx", ".docm", ".dotx", ".dotm"})
ORG_EXTRACT_XL_EXTS = frozenset({".xlsx", ".xlsm", ".xltx", ".xltm"})
ORG_EXTRACT_PPT_EXTS = frozenset({".pptx", ".pptm", ".potx", ".ppsx"})
ORG_EXTRACT_ODF_EXTS = frozenset({".odt", ".ods", ".odp", ".ott", ".ots", ".otp"})

# Cloud-Platzhalter (OneDrive „Nur online verfügbar“ u. ä.) nicht lesen: jedes Öffnen
# würde die Datei aus dem Internet herunterladen (keine Netzwerkzugriffe!).
ORG_EXTRACT_SKIP_CLOUD = True
# FILE_ATTRIBUTE_OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS
ORG_EXTRACT_CLOUD_ATTRS = 0x1000 | 0x40000 | 0x400000

_ORG_TEXT_READ_MAX = 256 * 1024
_ORG_TEXT_READ_FIRST = 32 * 1024
_ORG_EXIF_READ_FIRST = 128 * 1024
_ORG_RTF_READ_MAX = 2 * 1024 * 1024
_ORG_ZIP_MEMBER_MAX = 30 * 1024 * 1024
_ORG_ZIP_RATIO_MAX = 200
_ORG_ZIP_XML_READ = 3 * 1024 * 1024
_ORG_PDF_HEAD = 2 * 1024 * 1024
_ORG_PDF_TAIL = 1024 * 1024
_ORG_PDF_STREAM_MAX = 8 * 1024 * 1024
_ORG_PDF_TOTAL_MAX = 48 * 1024 * 1024
_ORG_IMG_HEAD = 512 * 1024

_org_qt_lock = threading.Lock()


# ===========================================================================
# Allgemeine Helfer
# ===========================================================================
_ORG_RE_CTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffd\ue000-\uf8ff\u200b\ufeff]")


def _org_ws(text: str) -> str:
    """Leerraum zusammenfassen (str.split ist ~10x schneller als re.sub);
    Rand-Leerzeichen bleiben als ein Leerzeichen erhalten (Stückgrenzen)."""
    j = " ".join(text.split())
    if not j:
        return " " if text else ""
    if text[0].isspace():
        j = " " + j
    if text[-1].isspace():
        j += " "
    return j


def _org_clean_part(text: str) -> str:
    text = text.replace("\xad", "")
    if _ORG_RE_CTRL.search(text):
        text = _ORG_RE_CTRL.sub(" ", text)
    return _org_ws(text)


def _org_clean(text: str, max_chars: int) -> str:
    """Steuerzeichen entfernen, Leerraum zusammenfassen, kürzen.
    Stückweise, damit lange Texte (256 KB) nicht komplett bereinigt werden."""
    if not text or max_chars <= 0:
        return ""
    step = max(16384, max_chars * 2)
    if len(text) <= step:
        return _org_clean_part(text).strip()[:max_chars]
    parts = []
    got = 0
    i = 0
    n = len(text)
    while i < n and got <= max_chars + 1:
        part = _org_clean_part(text[i:i + step])
        parts.append(part)
        got += len(part)
        i += step
    return _org_ws("".join(parts)).strip()[:max_chars]


def _org_fs_path(path, nt=None) -> str:
    """Windows: lange Pfade (>= 248 Zeichen) mit \\\\?\\ versehen, sonst unverändert."""
    s = os.fspath(path)
    if not isinstance(s, str):
        return s
    if nt is None:
        nt = os.name == "nt"
    if not nt or len(s) < 248 or s.startswith("\\\\?\\"):
        return s
    import ntpath
    if os.name == "nt" and not ntpath.isabs(s):
        s = ntpath.abspath(s)
    s = ntpath.normpath(s)
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    if len(s) >= 3 and s[1] == ":" and s[2] == "\\":
        return "\\\\?\\" + s
    return s


def _org_file_ok(path, st=None) -> bool:
    """Nur normale, lokal vorhandene Dateien lesen (keine Ordner, Pipes, Geräte,
    keine Cloud-Platzhalter, die beim Öffnen heruntergeladen würden)."""
    try:
        if st is None:
            st = os.stat(path)
        if not stat.S_ISREG(st.st_mode):
            return False
        if ORG_EXTRACT_SKIP_CLOUD:
            attrs = getattr(st, "st_file_attributes", 0) or 0
            if attrs & ORG_EXTRACT_CLOUD_ATTRS:
                return False
        return True
    except (OSError, ValueError, TypeError, OverflowError):
        return False


def _org_prepare(path):
    """Pfad prüfen/umwandeln → str für open() oder None, wenn nicht lesbar."""
    try:
        p = _org_fs_path(path)
    except (TypeError, ValueError):
        return None
    if not _org_file_ok(p):
        return None
    return p


def _org_read(path, limit: int, offset: int = 0) -> bytes:
    try:
        with open(path, "rb") as fh:
            if offset:
                fh.seek(offset)
            return fh.read(limit)
    except (OSError, ValueError, TypeError, OverflowError):
        return b""


def _org_unhex(raw: bytes) -> bytes:
    h = re.sub(rb"[^0-9A-Fa-f]", b"", raw)
    if len(h) % 2:
        h += b"0"
    try:
        return binascii.unhexlify(h)
    except (binascii.Error, ValueError):
        return b""


# ===========================================================================
# Klartext, HTML, E-Mail
# ===========================================================================
def _org_decode_bytes(data: bytes, truncated: bool = True) -> str:
    """utf-8 (BOM) → utf-16 (BOM/Heuristik) → cp1252.
    truncated=False: Datei vollständig gelesen → kein abgeschnittenes utf-8-Zeichen
    am Ende möglich (sonst ginge z. B. das „ü“ in cp1252 „Menü“ verloren)."""
    if not data:
        return ""
    if data.startswith(codecs.BOM_UTF8):
        return data[3:].decode("utf-8", "replace")
    if data.startswith(codecs.BOM_UTF32_LE) or data.startswith(codecs.BOM_UTF32_BE):
        return data.decode("utf-32", "replace")
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        return data[:len(data) - (len(data) % 2)].decode("utf-16", "replace")
    sample = data[:4096]
    zeros = sample.count(0)
    if len(sample) >= 4 and zeros > len(sample) // 4:
        odd = sample[1::2].count(0)
        even = sample[0::2].count(0)
        enc = "utf-16-le" if odd > even else "utf-16-be"
        return data[:len(data) - (len(data) % 2)].decode(enc, "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        # abgeschnittenes Mehrbyte-Zeichen am Ende (Leselimit) → trotzdem utf-8
        if truncated and e.end >= len(data) and e.start >= len(data) - 3:
            try:
                return data[:e.start].decode("utf-8")
            except UnicodeDecodeError:
                pass
    return data.decode("cp1252", "replace")


_ORG_RE_HTML_OPEN = re.compile(r"<(script|style|noscript|svg)\b|<!--", re.I)
_ORG_RE_HTML_TAG = re.compile(r"<[^<>]{0,4000}>")


def _org_html_drop(text: str) -> str:
    """Skripte, Styles, Kommentare entfernen – linear (kein Rückverfolgen bei
    fehlenden End-Tags; Rest ab einem offenen Block wird verworfen)."""
    out = []
    pos = 0
    low = None
    n = len(text)
    while pos < n:
        m = _ORG_RE_HTML_OPEN.search(text, pos)
        if m is None:
            out.append(text[pos:])
            break
        out.append(text[pos:m.start()])
        out.append(" ")
        if m.group(1) is None:
            end = text.find("-->", m.end())
            pos = n if end < 0 else end + 3
        else:
            if low is None:
                low = text.lower()
            end = low.find("</" + m.group(1).lower(), m.end())
            if end < 0:
                pos = n
            else:
                gt = text.find(">", end)
                pos = n if gt < 0 else gt + 1
    return "".join(out)


def _org_strip_markup(text: str) -> str:
    text = _org_html_drop(text)
    text = _ORG_RE_HTML_TAG.sub(" ", text)
    return html.unescape(text)


def _org_eml_text(data: bytes) -> str:
    """E-Mail: Betreff + Text-Teil (MIME/Base64/QP dekodiert)."""
    try:
        import email
        import email.policy
        msg = email.message_from_bytes(data, policy=email.policy.default)
        parts = []
        for h in ("subject", "from", "date"):
            v = msg.get(h)
            if v:
                parts.append(str(v))
        body = msg.get_body(preferencelist=("plain", "html"))
        if body is not None:
            try:
                content = body.get_content()
            except Exception:                      # unbekannter Zeichensatz u. ä.
                content = body.get_payload(decode=True) or b""
            if isinstance(content, bytes):
                content = _org_decode_bytes(content, False)
            if body.get_content_subtype() == "html":
                content = _org_strip_markup(content)
            parts.append(content)
        if parts:
            return " ".join(parts)
    except Exception:
        pass
    return _org_decode_bytes(data)


def _org_plain_text(path, ext: str, max_chars: int = 4000) -> str:
    markup = ext in (".html", ".htm", ".xhtml", ".xml")
    # Klartext: zuerst nur einen Anfang lesen (spart E/A bei großen Logs/CSVs);
    # reicht er nach dem Bereinigen nicht, bis 256 KB nachlesen.
    first = _ORG_TEXT_READ_MAX if (markup or ext == ".eml") else \
        min(_ORG_TEXT_READ_MAX, max(_ORG_TEXT_READ_FIRST, max_chars * 8))
    limit = first
    while True:
        data = _org_read(path, limit)
        if not data:
            return ""
        if ext == ".eml":
            return _org_eml_text(data)
        text = _org_decode_bytes(data, len(data) >= limit)
        if text:
            bad = len(_ORG_RE_CTRL.findall(text[:8192]))
            if bad > len(text[:8192]) * 0.05:
                return ""                          # Binärdaten mit Text-Endung
        if markup:
            text = _org_strip_markup(text)
        text = _org_clean(text, max_chars)
        if len(data) < limit or limit >= _ORG_TEXT_READ_MAX or len(text) >= max_chars:
            return text
        limit = _ORG_TEXT_READ_MAX


# ===========================================================================
# Office (OOXML / ODF) über zipfile
# ===========================================================================
_ORG_RE_XML_DROP = re.compile(
    rb"<(w:instrText|w:delText|mc:Fallback|w:fldData|w:rPr|w:pPr|a:rPr|a:pPr)\b[^>]*?(?:/>|>.*?</\1>)",
    re.S)
_ORG_RE_XML_BREAK = re.compile(
    rb"</(?:w:p|a:p|text:p|text:h|si|c|table:table-cell|w:tc|a:tc|text:list-item)>"
    rb"|<(?:w:tab|w:br|w:cr|a:br|text:tab|text:s|text:line-break)\b[^>]*>")
_ORG_RE_XML_TAG = re.compile(rb"<[^>]*>")
_ORG_RE_PPT_SLIDE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_ORG_RE_DOCX_HEADER = re.compile(r"^word/header(\d+)\.xml$")


def _org_zip_member_ok(info: zipfile.ZipInfo) -> bool:
    """Schutz vor Zip-Bomben und verschlüsselten Einträgen."""
    if info.flag_bits & 0x1:
        return False
    if info.file_size > _ORG_ZIP_MEMBER_MAX:
        return False
    if info.file_size > 64 * 1024:
        if info.compress_size <= 0:
            return False
        if info.file_size / float(info.compress_size) > _ORG_ZIP_RATIO_MAX:
            return False
    return True


def _org_zip_read(zf: zipfile.ZipFile, name: str, limit: int = _ORG_ZIP_XML_READ) -> bytes:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return b""
    if not _org_zip_member_ok(info):
        return b""
    try:
        with zf.open(info) as m:
            return m.read(limit)
    except Exception:
        return b""


def _org_xml_text(raw: bytes) -> str:
    if not raw:
        return ""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):          # XML in UTF-16 (selten)
        raw = raw[:len(raw) - len(raw) % 2].decode("utf-16", "replace").encode("utf-8")
    raw = _ORG_RE_XML_DROP.sub(b"", raw)
    raw = _ORG_RE_XML_BREAK.sub(b" ", raw)
    raw = _ORG_RE_XML_TAG.sub(b"", raw)
    return html.unescape(raw.decode("utf-8", "replace"))


_ORG_RE_OOXML_REL = re.compile(rb"<(?:\w+:)?Relationship\b[^>]*>")
_ORG_RE_OOXML_TARGET = re.compile(rb"Target=[\"']/?([^\"']+)[\"']")


def _org_ooxml_main(zf: zipfile.ZipFile, names, default: str) -> str:
    """Hauptteil laut _rels/.rels (manche Programme nennen ihn document2.xml o. ä.)."""
    if default in names:
        return default
    for m in _ORG_RE_OOXML_REL.finditer(_org_zip_read(zf, "_rels/.rels", 64 * 1024)):
        tag = m.group(0)
        if b"/officeDocument\"" not in tag and b"/officeDocument'" not in tag:
            continue
        t = _ORG_RE_OOXML_TARGET.search(tag)
        if t:
            name = t.group(1).decode("utf-8", "replace")
            if name in names:
                return name
    return default


def _org_office_text(path, ext: str, max_chars: int) -> str:
    # XML-Lesemenge an max_chars koppeln (Formatierungs-XML ist ~20-50x länger als Text)
    xml_limit = min(_ORG_ZIP_XML_READ, max(512 * 1024, max_chars * 100))
    try:
        with open(path, "rb") as fh:
            with zipfile.ZipFile(fh) as zf:
                names = zf.namelist()
                parts = []
                if ext in ORG_EXTRACT_WORD_EXTS:
                    heads = sorted((int(m.group(1)), n) for n in names
                                   for m in [_ORG_RE_DOCX_HEADER.match(n)] if m)
                    for _, n in heads[:2]:
                        parts.append(_org_xml_text(_org_zip_read(zf, n, 256 * 1024))[:300])
                    main = _org_ooxml_main(zf, set(names), "word/document.xml")
                    parts.append(_org_xml_text(_org_zip_read(zf, main, xml_limit)))
                elif ext in ORG_EXTRACT_XL_EXTS:
                    t = _org_xml_text(_org_zip_read(zf, "xl/sharedStrings.xml", xml_limit))
                    if not t.strip():
                        # ohne gemeinsame Zeichenketten (inlineStr): erstes Blatt lesen
                        t = _org_xml_text(_org_zip_read(zf, "xl/worksheets/sheet1.xml",
                                                        xml_limit))
                    parts.append(t)
                elif ext in ORG_EXTRACT_PPT_EXTS:
                    slides = sorted((int(m.group(1)), n) for n in names
                                    for m in [_ORG_RE_PPT_SLIDE.match(n)] if m)
                    total = 0
                    for _, n in slides[:5]:
                        t = _org_xml_text(_org_zip_read(zf, n, 1024 * 1024))
                        parts.append(t)
                        total += len(t)
                        if total > max_chars * 2:
                            break
                elif ext in ORG_EXTRACT_ODF_EXTS:
                    parts.append(_org_xml_text(_org_zip_read(zf, "content.xml", xml_limit)))
                return " ".join(p for p in parts if p)
    except Exception:
        return ""


# ===========================================================================
# RTF
# ===========================================================================
_ORG_RE_RTF = re.compile(
    rb"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z'])|([{}])|[\r\n]+|([^\\{}\r\n]+)")
_ORG_RTF_SKIP = frozenset({
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "objdata", "themedata",
    "colorschememapping", "datastore", "latentstyles", "rsidtbl", "listtable",
    "listoverridetable", "generator", "xmlnstbl", "mmathPr", "fldinst", "filetbl",
    "revtbl", "xe", "bkmkstart", "bkmkend", "pgdsctbl", "wgrffmtfilter", "operator",
    "shppict", "nonshppict", "blipuid", "factoidname", "sp", "sn", "sv", "private",
    "listtext", "pntext", "pntxtb", "pntxta", "userprops", "docvar", "template",
    "footnote", "annotation", "atnid", "atnauthor", "header", "footer", "headerl",
    "headerr", "headerf", "footerl", "footerr", "footerf", "passwordhash"})
_ORG_RTF_BREAK = frozenset({"par", "line", "tab", "cell", "row", "sect", "page",
                            "emspace", "enspace", "qmspace"})
_ORG_RTF_CHARS = {"emdash": "\u2014", "endash": "\u2013", "bullet": "\u2022",
                  "lquote": "\u2018", "rquote": "\u2019", "ldblquote": "\u201c",
                  "rdblquote": "\u201d"}


def _org_rtf_text(path, max_chars: int) -> str:
    data = _org_read(path, _ORG_RTF_READ_MAX)
    if not data.lstrip().startswith(b"{\\rtf"):
        return ""
    out = []
    count = 0
    stack = []
    ignorable = False
    ucskip = 1
    curskip = 0
    cp = "cp1252"
    limit = max_chars * 2 + 200
    for m in _ORG_RE_RTF.finditer(data):
        word, arg, hexc, sym, brace, text = m.groups()
        if brace is not None:
            curskip = 0
            if brace == b"{":
                if len(stack) < 1000:
                    stack.append((ignorable, ucskip))
            elif stack:
                ignorable, ucskip = stack.pop()
        elif sym is not None:
            curskip = 0
            if sym == b"*":
                ignorable = True
            elif ignorable:
                continue
            elif sym == b"~":
                out.append(" ")
            elif sym in (b"{", b"}", b"\\"):
                out.append(sym.decode("latin-1"))
            elif sym in (b"\r", b"\n"):
                out.append(" ")
            elif sym == b"_":
                out.append("-")
        elif word is not None:
            w = word.decode("ascii")
            if w in _ORG_RTF_SKIP:
                ignorable = True
                curskip = 0
                continue
            if w == "ansicpg" and arg:
                try:
                    codecs.lookup("cp" + arg.decode("ascii"))
                    cp = "cp" + arg.decode("ascii")
                except (LookupError, UnicodeDecodeError):
                    pass
            elif w == "mac":
                cp = "mac_roman"
            elif w == "uc" and arg:
                ucskip = max(0, min(int(arg), 8))
            if ignorable:
                continue
            if w == "u" and arg:
                n = int(arg)
                if n < 0:
                    n += 65536
                if 0 <= n < 0x110000:
                    out.append(chr(n))
                    count += 1
                curskip = ucskip
                continue
            curskip = 0
            if w in _ORG_RTF_BREAK:
                out.append(" ")
            elif w in _ORG_RTF_CHARS:
                out.append(_ORG_RTF_CHARS[w])
        elif hexc is not None:
            if curskip > 0:
                curskip -= 1
            elif not ignorable:
                out.append(bytes([int(hexc, 16)]).decode(cp, "replace"))
                count += 1
        elif text is not None:
            if curskip > 0:
                cut = min(curskip, len(text))
                text = text[cut:]
                curskip -= cut
            if text and not ignorable:
                out.append(text.decode(cp, "replace"))
                count += len(text)
        if count > limit:
            break
    return "".join(out)


# ===========================================================================
# PDF (reiner Python-Leser, nur zlib)
# ===========================================================================
class _org_PdfName(str):
    """PDF-Name (/Name) – unterscheidet sich so von Zeichenketten (bytes)."""
    __slots__ = ()


class _org_PdfRef(tuple):
    """Indirekte Referenz (Nummer, Generation)."""
    __slots__ = ()


_ORG_PDF_WSB = b"\x00\t\n\x0c\r "
_ORG_RE_PDF_WS = re.compile(rb"(?:[\x00\t\n\x0c\r ]+|%[^\r\n]*)*")
_ORG_RE_PDF_NUM = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)")
_ORG_RE_PDF_REFTAIL = re.compile(rb"[\x00\t\n\x0c\r ]+(\d{1,5})[\x00\t\n\x0c\r ]+R(?![A-Za-z0-9_])")
_ORG_RE_PDF_NAME = re.compile(rb"/([^\x00\t\n\x0c\r ()<>\[\]{}/%]*)")
_ORG_RE_PDF_KW = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*")
_ORG_RE_PDF_NAMEHEX = re.compile(rb"#([0-9A-Fa-f]{2})")
_ORG_RE_PDF_LITSPECIAL = re.compile(rb"[()\\]")
_ORG_RE_PDF_OBJ = re.compile(rb"(?:(?<=[\x00\t\n\x0c\r >])|\A)(\d{1,10})[\x00\t\n\x0c\r ]+(\d{1,5})[\x00\t\n\x0c\r ]+obj(?![A-Za-z])")
_ORG_RE_PDF_STREAMKW = re.compile(rb"[\x00\t\n\x0c\r ]*stream(?:\r\n|\n|\r)?")
_ORG_RE_PDF_ROOT = re.compile(rb"/Root[\x00\t\n\x0c\r ]+(\d{1,10})[\x00\t\n\x0c\r ]+\d{1,5}[\x00\t\n\x0c\r ]+R")
_ORG_RE_PDF_ENCRYPT = re.compile(rb"/Encrypt[\x00\t\n\x0c\r ]*(?:\d+[\x00\t\n\x0c\r ]+\d+[\x00\t\n\x0c\r ]+R|<<)")
_ORG_RE_PDF_XREFTYPE = re.compile(rb"/Type[\x00\t\n\x0c\r ]*/XRef(?![A-Za-z])")
_ORG_RE_PDF_TYPEPAGE = re.compile(rb"/Type[\x00\t\n\x0c\r ]*/Page(?![A-Za-z])")
_ORG_RE_PDF_TYPEPAGES = re.compile(rb"/Type[\x00\t\n\x0c\r ]*/Pages(?![A-Za-z])")
_ORG_RE_PDF_LINEAR = re.compile(rb"/Linearized\b[^>]{0,400}?/N[\x00\t\n\x0c\r ]+(\d{1,7})")
_ORG_RE_PDF_EI = re.compile(rb"[\x00\t\n\x0c\r ]EI(?=[\x00\t\n\x0c\r ]|$)")
_ORG_RE_PDF_CTOK = re.compile(
    rb"(?P<ws>[\x00\t\n\x0c\r ]+|%[^\r\n]*)"
    rb"|(?P<num>[+-]?(?:\d+\.?\d*|\.\d+))"
    rb"|(?P<name>/[^\x00\t\n\x0c\r ()<>\[\]{}/%]*)"
    rb"|(?P<dopen><<)|(?P<dclose>>>)"
    rb"|(?P<hex><[0-9A-Fa-f\x00\t\n\x0c\r ]*>)"
    rb"|(?P<aopen>\[)|(?P<aclose>\])"
    rb"|(?P<lit>\()"
    rb"|(?P<op>[A-Za-z'\"*][A-Za-z0-9'\"*]*)"
    rb"|(?P<other>.)", re.S)
_ORG_PDF_ESC = {0x6E: b"\n", 0x72: b"\r", 0x74: b"\t", 0x62: b"\b", 0x66: b"\f",
                0x28: b"(", 0x29: b")", 0x5C: b"\\"}

# WinAnsi (cp1252) und MacRoman als Grundtabellen für einfache Fonts
_ORG_PDF_WINANSI = tuple(
    ("" if c == "\ufffd" else c) for c in
    (bytes([i]).decode("cp1252", "replace") for i in range(256)))
_ORG_PDF_MACROMAN = tuple(bytes([i]).decode("mac_roman", "replace") for i in range(256))
# häufige Glyphennamen aus /Differences (inkl. deutscher Umlaute)
_ORG_PDF_GLYPHS = {
    "space": " ", "exclam": "!", "quotedbl": '"', "numbersign": "#", "dollar": "$",
    "percent": "%", "ampersand": "&", "quotesingle": "'", "quoteright": "\u2019",
    "quoteleft": "\u2018", "parenleft": "(", "parenright": ")", "asterisk": "*",
    "plus": "+", "comma": ",", "hyphen": "-", "minus": "\u2212", "period": ".",
    "slash": "/", "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "colon": ":",
    "semicolon": ";", "less": "<", "equal": "=", "greater": ">", "question": "?",
    "at": "@", "bracketleft": "[", "backslash": "\\", "bracketright": "]",
    "underscore": "_", "braceleft": "{", "bar": "|", "braceright": "}",
    "adieresis": "\u00e4", "odieresis": "\u00f6", "udieresis": "\u00fc",
    "Adieresis": "\u00c4", "Odieresis": "\u00d6", "Udieresis": "\u00dc",
    "germandbls": "\u00df", "eacute": "\u00e9", "egrave": "\u00e8", "agrave": "\u00e0",
    "aacute": "\u00e1", "ccedilla": "\u00e7", "Eacute": "\u00c9", "ecircumflex": "\u00ea",
    "endash": "\u2013", "emdash": "\u2014", "quotedblleft": "\u201c",
    "quotedblright": "\u201d", "quotedblbase": "\u201e", "quotesinglbase": "\u201a",
    "bullet": "\u2022", "ellipsis": "\u2026", "Euro": "\u20ac", "section": "\u00a7",
    "degree": "\u00b0", "fi": "fi", "fl": "fl", "ff": "ff", "ffi": "ffi", "ffl": "ffl",
    "multiply": "\u00d7", "copyright": "\u00a9", "registered": "\u00ae",
    "trademark": "\u2122", "sterling": "\u00a3", "paragraph": "\u00b6",
    "nbspace": " ", "uni00A0": " ", "guillemotleft": "\u00ab", "guillemotright": "\u00bb",
}
_ORG_PDF_UCS2_ENCODINGS = frozenset({"UniGB-UCS2-H", "UniJIS-UCS2-H", "UniKS-UCS2-H",
                                     "UniCNS-UCS2-H", "UniJIS-UTF16-H", "UniGB-UTF16-H"})


def _org_pdf_skip(raw: bytes) -> str:
    return ""


def _org_pdf_winansi(raw: bytes) -> str:
    t = _ORG_PDF_WINANSI
    return "".join([t[c] for c in raw])


def _org_pdf_literal(data: bytes, pos: int):
    """Literal-String ab '(' bei pos → (bytes, Endposition). Escapes inkl. Oktal."""
    i = pos + 1
    n = len(data)
    depth = 1
    out = bytearray()
    while i < n:
        m = _ORG_RE_PDF_LITSPECIAL.search(data, i)
        if m is None:
            out += data[i:]
            return bytes(out), n
        j = m.start()
        out += data[i:j]
        c = data[j]
        if c == 0x5C:
            if j + 1 >= n:
                return bytes(out), n
            e = data[j + 1]
            if e in _ORG_PDF_ESC:
                out += _ORG_PDF_ESC[e]
                i = j + 2
            elif 0x30 <= e <= 0x37:
                k = j + 1
                v = 0
                while k < n and k < j + 4 and 0x30 <= data[k] <= 0x37:
                    v = v * 8 + (data[k] - 0x30)
                    k += 1
                out.append(v & 0xFF)
                i = k
            elif e == 0x0D:
                i = j + 3 if data[j + 2:j + 3] == b"\n" else j + 2
            elif e == 0x0A:
                i = j + 2
            else:
                out.append(e)
                i = j + 2
        elif c == 0x28:
            depth += 1
            out.append(c)
            i = j + 1
        else:
            depth -= 1
            if depth == 0:
                return bytes(out), j + 1
            out.append(c)
            i = j + 1
    return bytes(out), n


def _org_pdf_name_decode(raw: bytes) -> str:
    if b"#" in raw:
        raw = _ORG_RE_PDF_NAMEHEX.sub(lambda m: bytes([int(m.group(1), 16)]), raw)
    return raw.decode("latin-1")


def _org_pdf_value(data: bytes, pos: int, depth: int = 0):
    """Ein PDF-Objekt ab pos parsen → (Wert, Endposition). Nie Ausnahme."""
    n = len(data)
    pos = _ORG_RE_PDF_WS.match(data, pos).end()
    if pos >= n or depth > 40:
        return None, pos
    c = data[pos]
    if c == 0x2F:                                             # /Name
        m = _ORG_RE_PDF_NAME.match(data, pos)
        return _org_PdfName(_org_pdf_name_decode(m.group(1))), m.end()
    if c == 0x3C:                                             # << oder <hex>
        if data[pos + 1:pos + 2] == b"<":
            d = {}
            pos += 2
            for _ in range(20000):
                pos = _ORG_RE_PDF_WS.match(data, pos).end()
                if pos >= n:
                    break
                if data[pos:pos + 2] == b">>":
                    pos += 2
                    break
                if data[pos] != 0x2F:
                    _, np = _org_pdf_value(data, pos, depth + 1)
                    pos = np if np > pos else pos + 1
                    continue
                m = _ORG_RE_PDF_NAME.match(data, pos)
                key = _org_pdf_name_decode(m.group(1))
                v, np = _org_pdf_value(data, m.end(), depth + 1)
                d[key] = v
                pos = np if np > m.end() else m.end() + 1
            return d, pos
        end = data.find(b">", pos)
        if end < 0:
            end = n
        return _org_unhex(data[pos + 1:end]), end + 1
    if c == 0x5B:                                             # [ Array ]
        arr = []
        pos += 1
        for _ in range(200000):
            pos = _ORG_RE_PDF_WS.match(data, pos).end()
            if pos >= n:
                break
            if data[pos] == 0x5D:
                pos += 1
                break
            v, np = _org_pdf_value(data, pos, depth + 1)
            if np <= pos:
                pos += 1
                continue
            arr.append(v)
            pos = np
        return arr, pos
    if c == 0x28:
        return _org_pdf_literal(data, pos)
    m = _ORG_RE_PDF_NUM.match(data, pos)
    if m:
        tok = m.group()
        if b"." not in tok:
            iv = int(tok)
            r = _ORG_RE_PDF_REFTAIL.match(data, m.end())
            if r and iv >= 0:
                return _org_PdfRef((iv, int(r.group(1)))), r.end()
            return iv, m.end()
        try:
            return float(tok), m.end()
        except ValueError:
            return 0.0, m.end()
    m = _ORG_RE_PDF_KW.match(data, pos)
    if m:
        kw = m.group()
        if kw == b"true":
            return True, m.end()
        if kw == b"false":
            return False, m.end()
        return None, m.end()
    return None, pos + 1


def _org_pdf_inflate(raw: bytes, limit: int):
    try:
        return zlib.decompressobj().decompress(raw, limit)
    except zlib.error:
        pass
    # beschädigter Strom (oder roher Deflate ohne zlib-Kopf): so viel wie möglich retten
    best = None
    for wbits in (15, -15):
        d = zlib.decompressobj(wbits)
        out = bytearray()
        for i in range(0, len(raw), 512):
            room = limit - len(out)
            if room <= 0:
                break
            try:
                out += d.decompress(raw[i:i + 512], room)
            except zlib.error:
                break
        if out and (best is None or len(out) > len(best)):
            best = bytes(out)
    return best


def _org_pdf_runlength(raw: bytes, limit: int) -> bytes:
    out = bytearray()
    i = 0
    n = len(raw)
    while i < n and len(out) < limit:
        b = raw[i]
        if b == 128:
            break
        if b < 128:
            out += raw[i + 1:i + 2 + b]
            i += 2 + b
        else:
            if i + 1 >= n:
                break
            out += raw[i + 1:i + 2] * (257 - b)
            i += 2
    return bytes(out[:limit])


def _org_cmap_parse(data: bytes):
    """ToUnicode-CMap → (maps {Länge: {Code: Text}}, große Bereiche, Codelängen)."""
    maps = {}
    ranges = {}
    lens = set()

    def dst_text(raw: bytes) -> str:
        if len(raw) == 1:
            return chr(raw[0])
        if len(raw) % 2:
            raw = raw[:-1]
        return raw.decode("utf-16-be", "replace")

    for blk in re.finditer(rb"begincodespacerange(.*?)endcodespacerange", data, re.S):
        for lo in re.finditer(rb"<([0-9A-Fa-f\s]+)>\s*<[0-9A-Fa-f\s]+>", blk.group(1)):
            L = len(_org_unhex(lo.group(1)))
            if 1 <= L <= 4:
                lens.add(L)
    for blk in re.finditer(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for m in re.finditer(rb"<([0-9A-Fa-f\s]+)>\s*(?:<([0-9A-Fa-f\s]*)>|/([^\s<\[/]+))",
                             blk.group(1)):
            src = _org_unhex(m.group(1))
            L = len(src)
            if not 1 <= L <= 4:
                continue
            if m.group(2) is not None:
                txt = dst_text(_org_unhex(m.group(2)))
            else:
                txt = _ORG_PDF_GLYPHS.get(m.group(3).decode("latin-1"), "")
            maps.setdefault(L, {})[int.from_bytes(src, "big")] = txt
            lens.add(L)
    for blk in re.finditer(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for m in re.finditer(
                rb"<([0-9A-Fa-f\s]+)>\s*<([0-9A-Fa-f\s]+)>\s*(?:<([0-9A-Fa-f\s]*)>|\[([^\]]*)\])",
                blk.group(1)):
            lo_b = _org_unhex(m.group(1))
            L = len(lo_b)
            if not 1 <= L <= 4:
                continue
            lo = int.from_bytes(lo_b, "big")
            hi = int.from_bytes(_org_unhex(m.group(2)), "big")
            if hi < lo or hi - lo > 0xFFFF:
                continue
            lens.add(L)
            mp = maps.setdefault(L, {})
            if m.group(4) is not None:
                for k, item in enumerate(re.findall(rb"<([0-9A-Fa-f\s]*)>", m.group(4))):
                    if lo + k > hi:
                        break
                    mp[lo + k] = dst_text(_org_unhex(item))
                continue
            base = dst_text(_org_unhex(m.group(3)))
            if not base:
                continue
            if hi - lo <= 256:
                head, last = base[:-1], ord(base[-1])
                for k in range(hi - lo + 1):
                    if last + k < 0x110000:
                        mp[lo + k] = head + chr(last + k)
            else:
                ranges.setdefault(L, []).append((lo, hi, base))
    return maps, ranges, sorted(lens, reverse=True) or [1]


def _org_cmap_decode(cmap, raw: bytes, base) -> str:
    maps, ranges, lens = cmap
    out = []
    i = 0
    n = len(raw)
    minlen = lens[-1]
    while i < n:
        for L in lens:
            if i + L > n:
                continue
            code = int.from_bytes(raw[i:i + L], "big")
            s = maps.get(L, {}).get(code)
            if s is None:
                for lo, hi, dst in ranges.get(L, ()):
                    if lo <= code <= hi:
                        c = ord(dst[-1]) + code - lo
                        s = dst[:-1] + chr(c) if c < 0x110000 else ""
                        break
            if s is not None:
                out.append(s)
                i += L
                break
        else:
            if base is not None:
                out.append(base[raw[i]])
                i += 1
            else:
                i += minlen
    return "".join(out)


class _org_PdfDoc:
    """Minimaler, fehlertoleranter PDF-Leser über einem Byte-Puffer (je Aufruf
    eine eigene Instanz → threadsicher)."""

    def __init__(self, data: bytes):
        self.data = data
        self.offsets = {}
        for m in _ORG_RE_PDF_OBJ.finditer(data):
            self.offsets[int(m.group(1))] = m.end()   # spätere Fassung gewinnt
        self.compressed = {}
        self._objstm_done = False
        self._cache = {}
        self._endpos = {}
        self._streams = {}
        self._fonts = {}
        self._inflated = 0
        self.encrypted = _org_pdf_is_encrypted(data)
        # Zustand der Textausgabe
        self._sep = ""
        self._last_len = 0
        self._tm_y = None
        self._count = 0
        self._limit = 0

    # ----- Objekte -----
    def obj(self, num):
        if num in self._cache:
            return self._cache[num]
        self._cache[num] = None                       # Schutz vor Zyklen
        val = None
        off = self.offsets.get(num)
        if off is not None:
            val, end = _org_pdf_value(self.data, off)
            self._endpos[num] = end
        else:
            self._load_objstm()
            comp = self.compressed.get(num)
            if comp is not None:
                val, _ = _org_pdf_value(comp[0], comp[1])
        self._cache[num] = val
        return val

    def resolve(self, v):
        for _ in range(8):
            if isinstance(v, _org_PdfRef):
                v = self.obj(v[0])
            else:
                break
        return v if not isinstance(v, _org_PdfRef) else None

    def stream(self, num):
        if num in self._streams:
            return self._streams[num]
        self._streams[num] = None
        d = self.obj(num)
        if not isinstance(d, dict) or num not in self._endpos:
            return None
        data = self.data
        m = _ORG_RE_PDF_STREAMKW.match(data, self._endpos[num])
        if m is None:
            return None
        start = m.end()
        raw = None
        length = self.resolve(d.get("Length"))
        if isinstance(length, int) and 0 <= length <= len(data) - start:
            tail = data[start + length:start + length + 40].lstrip(_ORG_PDF_WSB)
            if tail.startswith(b"endstream"):
                raw = data[start:start + length]
        if raw is None:
            e = data.find(b"endstream", start)
            raw = data[start:e] if e >= 0 else data[start:]
            if raw.endswith(b"\r\n"):
                raw = raw[:-2]
            elif raw.endswith(b"\n") or raw.endswith(b"\r"):
                raw = raw[:-1]
        out = self._decode(raw, d)
        self._streams[num] = out
        return out

    def _decode(self, raw: bytes, d: dict):
        f = self.resolve(d.get("Filter"))
        if f is None:
            filters = []
        elif isinstance(f, list):
            filters = [self.resolve(x) for x in f]
        else:
            filters = [f]
        out = raw
        for fl in filters:
            room = min(_ORG_PDF_STREAM_MAX, _ORG_PDF_TOTAL_MAX - self._inflated)
            if room <= 0:
                return None
            if fl in ("FlateDecode", "Fl"):
                out = _org_pdf_inflate(out, room)
            elif fl in ("ASCIIHexDecode", "AHx"):
                e = out.find(b">")
                out = _org_unhex(out[:e] if e >= 0 else out)
            elif fl in ("ASCII85Decode", "A85"):
                e = out.find(b"~>")
                body = (out[:e] if e >= 0 else out).strip()
                if body.startswith(b"<~"):
                    body = body[2:]
                try:
                    out = base64.a85decode(b"<~" + body + b"~>", adobe=True)
                except (ValueError, binascii.Error):
                    return None
            elif fl in ("RunLengthDecode", "RL"):
                out = _org_pdf_runlength(out, room)
            else:
                return None                           # Bildfilter u. a. – kein Text
            if out is None:
                return None
            self._inflated += len(out)
        return out

    def _load_objstm(self):
        if self._objstm_done:
            return
        self._objstm_done = True
        seen = 0
        for num, off in list(self.offsets.items()):
            if b"/ObjStm" not in self.data[off:off + 600]:
                continue
            seen += 1
            if seen > 300:
                break
            d = self.obj(num)
            if not isinstance(d, dict) or d.get("Type") != "ObjStm":
                continue
            buf = self.stream(num)
            first = self.resolve(d.get("First"))
            count = self.resolve(d.get("N"))
            if not buf or not isinstance(first, int) or not isinstance(count, int):
                continue
            if not 0 < first <= len(buf):
                continue
            ints = [int(x) for x in re.findall(rb"\d+", buf[:first])]
            for k in range(0, min(len(ints) // 2, count, 100000) * 2, 2):
                onum, ooff = ints[k], ints[k + 1]
                if onum not in self.offsets and onum not in self.compressed:
                    if first + ooff < len(buf):
                        self.compressed[onum] = (buf, first + ooff)

    def _typed_objects(self, regex, max_parse: int = 3000):
        """Objekte, deren Text zum Muster passt (direkt und aus Objektströmen)."""
        res = []
        data = self.data
        for num, off in sorted(self.offsets.items(), key=lambda kv: kv[1]):
            if regex.search(data, off, off + 400):
                res.append(num)
                if len(res) >= max_parse:
                    return res
        self._load_objstm()
        for num in sorted(self.compressed):
            buf, off = self.compressed[num]
            if regex.search(buf, off, off + 400):
                res.append(num)
                if len(res) >= max_parse:
                    break
        return res

    def root(self):
        last = None
        for m in _ORG_RE_PDF_ROOT.finditer(self.data):
            last = m
        if last is not None:
            cat = self.obj(int(last.group(1)))
            if isinstance(cat, dict):
                return cat
        for num in self._typed_objects(re.compile(rb"/Type[\x00\t\n\x0c\r ]*/Catalog"), 20):
            cat = self.obj(num)
            if isinstance(cat, dict):
                return cat
        return None

    # ----- Seiten -----
    def pages(self, limit: int):
        result = []
        cat = self.root()
        top = self.resolve(cat.get("Pages")) if isinstance(cat, dict) else None
        if isinstance(top, dict):
            stack = [(top, None)]
            visited = set()
            steps = 0
            while stack and len(result) < limit and steps < 20000:
                steps += 1
                node, inherited = stack.pop()
                if not isinstance(node, dict):
                    continue
                res = node.get("Resources", inherited)
                kids = self.resolve(node.get("Kids"))
                if isinstance(kids, list) and node.get("Type") != "Page":
                    for kid in reversed(kids):
                        if isinstance(kid, _org_PdfRef):
                            if kid[0] in visited:
                                continue
                            visited.add(kid[0])
                        stack.append((self.resolve(kid), res))
                elif "Contents" in node or node.get("Type") == "Page":
                    result.append((node, res))
        if not result:                                 # Seitenbaum fehlt/abgeschnitten
            for num in self._typed_objects(_ORG_RE_PDF_TYPEPAGE, 500):
                node = self.obj(num)
                if isinstance(node, dict) and node.get("Type") == "Page":
                    result.append((node, node.get("Resources")))
                    if len(result) >= limit:
                        break
        return result

    def page_count(self):
        cat = self.root()
        top = self.resolve(cat.get("Pages")) if isinstance(cat, dict) else None
        if isinstance(top, dict):
            c = self.resolve(top.get("Count"))
            if isinstance(c, int) and 0 < c < 1000000:
                return c
        m = _ORG_RE_PDF_LINEAR.search(self.data, 0, 4096)
        if m and 0 < int(m.group(1)) < 1000000:
            return int(m.group(1))
        best = 0
        for num in self._typed_objects(_ORG_RE_PDF_TYPEPAGES, 2000):
            node = self.obj(num)
            if isinstance(node, dict) and node.get("Type") == "Pages":
                c = self.resolve(node.get("Count"))
                if isinstance(c, int) and best < c < 1000000:
                    best = c
        if best:
            return best
        cnt = len(set(self._typed_objects(_ORG_RE_PDF_TYPEPAGE, 100000)))
        return cnt or None

    # ----- Fonts -----
    def font_decoder(self, fonts, name):
        if not isinstance(fonts, dict):
            return None
        ref = fonts.get(name)
        key = ref[0] if isinstance(ref, _org_PdfRef) else None
        if key is not None and key in self._fonts:
            return self._fonts[key]
        dec = self._make_decoder(self.resolve(ref))
        if key is not None:
            self._fonts[key] = dec
        return dec

    def _make_decoder(self, fd):
        if not isinstance(fd, dict):
            return None
        cmap = None
        tu = fd.get("ToUnicode")
        if isinstance(tu, _org_PdfRef):
            raw = self.stream(tu[0])
            if raw:
                parsed = _org_cmap_parse(raw)
                if parsed[0] or parsed[1]:
                    cmap = parsed
        if fd.get("Subtype") == "Type0":
            if cmap is not None:
                return lambda b, c=cmap: _org_cmap_decode(c, b, None)
            enc = self.resolve(fd.get("Encoding"))
            if enc in _ORG_PDF_UCS2_ENCODINGS:
                return lambda b: b[:len(b) - len(b) % 2].decode("utf-16-be", "replace")
            return _org_pdf_skip                       # Glyphen-IDs ohne Zuordnung
        enc = self.resolve(fd.get("Encoding"))
        table = _ORG_PDF_WINANSI
        diffs = None
        if enc == "MacRomanEncoding":
            table = _ORG_PDF_MACROMAN
        elif isinstance(enc, dict):
            if self.resolve(enc.get("BaseEncoding")) == "MacRomanEncoding":
                table = _ORG_PDF_MACROMAN
            diffs = self.resolve(enc.get("Differences"))
        if isinstance(diffs, list):
            table = list(table)
            code = 0
            for item in diffs[:2000]:
                if isinstance(item, int) and not isinstance(item, bool):
                    code = item
                elif isinstance(item, _org_PdfName):
                    if 0 <= code < 256:
                        ch = _org_pdf_glyph(item)
                        if ch is not None:
                            table[code] = ch
                    code += 1
            table = tuple(table)
        if cmap is not None:
            return lambda b, c=cmap, t=table: _org_cmap_decode(c, b, t)
        if table is _ORG_PDF_WINANSI:
            return _org_pdf_winansi
        return lambda b, t=table: "".join([t[c] for c in b])

    # ----- Text -----
    def _emit(self, s: str, out: list):
        if not s:
            return
        sep = self._sep
        if out and (sep in ("line", "word") or
                    (sep == "same" and not (self._last_len <= 1 and len(s) <= 1))):
            out.append(" ")
        self._sep = ""
        out.append(s)
        self._last_len = len(s.strip())
        self._count += len(s)

    def _soft(self, kind: str):
        if self._sep != "line":
            self._sep = kind

    def run_content(self, content: bytes, resources, out: list, depth: int = 0):
        res = self.resolve(resources)
        res = res if isinstance(res, dict) else {}
        fonts = self.resolve(res.get("Font"))
        xobjs = None
        decoder = None
        ops = []
        arrays = []
        pos = 0
        n = len(content)
        tok = _ORG_RE_PDF_CTOK
        forms = 0
        while pos < n:
            if self._count >= self._limit:
                return
            m = tok.match(content, pos)
            if m is None:
                pos += 1
                continue
            kind = m.lastgroup
            pos = m.end()
            if kind == "ws" or kind == "other" or kind == "dopen" or kind == "dclose":
                continue
            if kind == "num":
                try:
                    ops.append(float(m.group()))
                except ValueError:
                    ops.append(0.0)
            elif kind == "lit":
                s, pos = _org_pdf_literal(content, m.start())
                ops.append(s)
            elif kind == "hex":
                ops.append(_org_unhex(m.group()[1:-1]))
            elif kind == "name":
                ops.append(_org_PdfName(_org_pdf_name_decode(m.group()[1:])))
            elif kind == "aopen":
                if len(arrays) < 32:
                    arrays.append(ops)
                    ops = []
            elif kind == "aclose":
                if arrays:
                    arr = ops
                    ops = arrays.pop()
                    ops.append(arr)
            else:                                         # Operator
                op = m.group()
                if arrays:                                # kaputtes Array → verwerfen
                    ops = arrays[0]
                    arrays = []
                if op == b"Tf":
                    if len(ops) >= 2 and isinstance(ops[-2], _org_PdfName):
                        decoder = self.font_decoder(fonts, ops[-2])
                elif op == b"Tj" or op == b"'" or op == b'"':
                    if op != b"Tj":
                        self._sep = "line"
                    if ops and isinstance(ops[-1], bytes):
                        self._emit((decoder or _org_pdf_winansi)(ops[-1]), out)
                elif op == b"TJ":
                    if ops and isinstance(ops[-1], list):
                        dec = decoder or _org_pdf_winansi
                        for el in ops[-1]:
                            if isinstance(el, bytes):
                                self._emit(dec(el), out)
                            elif isinstance(el, float) and el < -200:
                                self._soft("word")
                elif op == b"Td" or op == b"TD":
                    if len(ops) >= 2 and isinstance(ops[-1], float):
                        if abs(ops[-1]) > 0.01:
                            self._sep = "line"
                        else:
                            self._soft("same")
                elif op == b"T*":
                    self._sep = "line"
                elif op == b"Tm":
                    if len(ops) >= 6 and isinstance(ops[-1], float):
                        y = ops[-1]
                        if self._tm_y is not None and abs(y - self._tm_y) < 0.5:
                            self._soft("same")
                        else:
                            self._sep = "line"
                        self._tm_y = y
                elif op == b"ET":
                    self._soft("same")
                elif op == b"BI":
                    e = _ORG_RE_PDF_EI.search(content, pos)
                    pos = e.end() if e else n
                elif op == b"Do":
                    if depth < 3 and forms < 30 and ops and isinstance(ops[-1], _org_PdfName):
                        forms += 1
                        if xobjs is None:
                            xobjs = self.resolve(res.get("XObject"))
                            if not isinstance(xobjs, dict):
                                xobjs = {}
                        ref = xobjs.get(ops[-1])
                        if isinstance(ref, _org_PdfRef):
                            xd = self.obj(ref[0])
                            if isinstance(xd, dict) and xd.get("Subtype") == "Form":
                                sub = self.stream(ref[0])
                                if sub:
                                    self.run_content(sub, xd.get("Resources", res), out,
                                                     depth + 1)
                ops = []
                continue
            if len(ops) > 5000:
                ops = ops[-50:]
        # abgeschnittener Strom: noch offene Zeichenketten trotzdem ausgeben
        pending = (arrays[0] if arrays else []) + ops
        for el in pending:
            items = el if isinstance(el, list) else [el]
            for it in items:
                if isinstance(it, bytes):
                    self._emit((decoder or _org_pdf_winansi)(it), out)

    def text(self, max_chars: int) -> str:
        if self.encrypted:
            return ""
        out = []
        self._count = 0
        self._limit = max_chars * 2 + 200
        pages = self.pages(8)
        if pages:
            for idx, (page, res) in enumerate(pages):
                if idx >= 3 and self._count >= 200:
                    break
                c = page.get("Contents")
                cr = self.resolve(c)
                if isinstance(cr, list):
                    refs = cr
                elif isinstance(c, _org_PdfRef):
                    refs = [c]
                else:
                    refs = []
                chunks = []
                for r in refs[:64]:
                    if isinstance(r, _org_PdfRef):
                        s = self.stream(r[0])
                        if s:
                            chunks.append(s)
                if chunks:
                    self.run_content(b"\n".join(chunks), res, out)
                self._sep = "line"
                if self._count >= self._limit:
                    break
        else:
            # kein Seitenbaum: Inhaltsströme direkt durchsuchen
            tried = 0
            for num, off in sorted(self.offsets.items(), key=lambda kv: kv[1]):
                if tried >= 40 or self._count >= self._limit:
                    break
                head = self.data[off:off + 300]
                if b"stream" not in head or b"/Type" in head.split(b"stream")[0]:
                    continue
                tried += 1
                s = self.stream(num)
                if s and b"BT" in s and (b"Tj" in s or b"TJ" in s):
                    self.run_content(s, None, out)
                    self._sep = "line"
        return "".join(out)


def _org_pdf_is_encrypted(data: bytes) -> bool:
    """/Encrypt nur im Trailer bzw. Querverweis-Strom werten – nicht, wenn das
    Wort zufällig im (unkomprimierten) Seitentext steht."""
    for m in _ORG_RE_PDF_ENCRYPT.finditer(data):
        s = m.start()
        before = data[max(0, s - 1024):s]
        lp = before.rfind(b"(")
        if lp > before.rfind(b")") and b"\n" not in before[lp:]:
            continue                                # steht in einem Text-Literal "( … )"
        if b"trailer" in before:
            return True
        win = data[max(0, s - 4096):s + 4096]
        if _ORG_RE_PDF_XREFTYPE.search(win) or b"/Root" in win:
            return True
    return False


def _org_pdf_glyph(name: str):
    if name in _ORG_PDF_GLYPHS:
        return _ORG_PDF_GLYPHS[name]
    base = name.split(".", 1)[0]
    if len(base) == 1:
        return base
    if base in _ORG_PDF_GLYPHS:
        return _ORG_PDF_GLYPHS[base]
    m = re.match(r"^(?:uni|u)([0-9A-Fa-f]{4,6})$", base)
    if m:
        v = int(m.group(1), 16)
        if v < 0x110000 and not 0xD800 <= v <= 0xDFFF:
            return chr(v)
    return None


def _org_pdf_load(path, head: int, tail: int):
    """Anfang + Ende einer PDF lesen (Seitenbaum liegt oft am Dateiende)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(0)
            if size <= head + tail:
                return fh.read(), size
            h = fh.read(head)
            fh.seek(size - tail)
            return h + b"\n" + fh.read(tail), size
    except (OSError, ValueError, TypeError, OverflowError):
        return b"", 0


def _org_pdf_text_qt(path, max_chars: int) -> str:
    """Rückfall über QtPdf (pdfium). Ohne QGuiApplication im Worker-Thread getestet;
    Aufrufe serialisiert. Liefert "" wenn QtPdf fehlt oder scheitert."""
    try:
        from PySide6.QtPdf import QPdfDocument
    except Exception:
        return ""
    with _org_qt_lock:
        doc = None
        try:
            doc = QPdfDocument(None)
            doc.load(str(path))
            pages = doc.pageCount()
            parts = []
            total = 0
            for i in range(min(pages, 3)):
                t = doc.getAllText(i).text()
                parts.append(t)
                total += len(t)
                if total >= max_chars:
                    break
            return " ".join(parts)
        except Exception:
            return ""
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
                del doc


def _org_pdf_text(path, max_chars: int, qt_path=None) -> str:
    data, size = _org_pdf_load(path, _ORG_PDF_HEAD, _ORG_PDF_TAIL)
    if not data:
        return ""
    text = ""
    try:
        text = _org_clean(_org_PdfDoc(data).text(max_chars), max_chars)
    except Exception:
        text = ""
    if len(text) < 20 and ORG_EXTRACT_USE_QTPDF and size <= ORG_EXTRACT_QTPDF_MAX_BYTES \
            and b"%PDF" in data[:1024]:
        qt = _org_clean(_org_pdf_text_qt(qt_path or path, max_chars), max_chars)
        if len(qt) > len(text):
            text = qt
    return text


# ===========================================================================
# Öffentliche Text-API
# ===========================================================================
def org_extract_text(path: Path, max_chars: int = 4000) -> str:
    """Die ersten max_chars lesbaren Zeichen (Leerraum zusammengefasst) oder ""."""
    try:
        if max_chars <= 0:
            return ""
        p = Path(path)
        ext = p.suffix.lower()
        office = ext in ORG_EXTRACT_WORD_EXTS or ext in ORG_EXTRACT_XL_EXTS \
            or ext in ORG_EXTRACT_PPT_EXTS or ext in ORG_EXTRACT_ODF_EXTS
        if not (office or ext in (".pdf", ".rtf") or ext in ORG_EXTRACT_TEXT_EXTS):
            return ""
        fp = _org_prepare(p)                   # Ordner/Pipe/Cloud-Platzhalter → ""
        if fp is None:
            return ""
        if ext == ".pdf":
            return _org_pdf_text(fp, max_chars, str(p))
        if office:
            return _org_clean(_org_office_text(fp, ext, max_chars), max_chars)
        if ext == ".rtf":
            return _org_clean(_org_rtf_text(fp, max_chars), max_chars)
        return _org_clean(_org_plain_text(fp, ext, max_chars), max_chars)
    except Exception:
        pass
    return ""


def org_pdf_page_count(path) -> int | None:
    """Seitenzahl (best effort) oder None."""
    try:
        path = _org_prepare(path)
        if path is None:
            return None
        for head in (1024 * 1024, 4 * 1024 * 1024):
            data, size = _org_pdf_load(path, head, 1024 * 1024)
            if not data:
                return None
            n = _org_PdfDoc(data).page_count()
            if n:
                return n
            if size <= head + 1024 * 1024:
                break
    except Exception:
        pass
    return None


# ===========================================================================
# EXIF
# ===========================================================================
_ORG_TIFF_TYPES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 13: 4}
_ORG_EXIF_IFD0 = frozenset({0x010F, 0x0110, 0x0131, 0x0132, 0x0112, 0x0100, 0x0101,
                            0x8769, 0x8825})
_ORG_EXIF_SUB = frozenset({0x9003, 0x9004, 0xA002, 0xA003})
_ORG_EXIF_GPS = frozenset({1, 2, 3, 4})
_ORG_RE_EXIF_DT = re.compile(r"^\s*(\d{4})[:\-./](\d{2})[:\-./](\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")


def _org_tiff_ifd(buf: bytes, e: str, off: int, wanted, visited: set) -> dict:
    """Ein IFD lesen (nur gewünschte Tags). off relativ zu buf[0]."""
    res = {}
    n = len(buf)
    if off < 8 or off + 2 > n or off in visited:
        return res
    visited.add(off)
    try:
        count = struct.unpack_from(e + "H", buf, off)[0]
    except struct.error:
        return res
    if count > 1000:
        return res
    for i in range(count):
        ent = off + 2 + 12 * i
        if ent + 12 > n:
            break
        tag, typ, cnt = struct.unpack_from(e + "HHI", buf, ent)
        if tag not in wanted:
            continue
        size = _ORG_TIFF_TYPES.get(typ)
        if not size or cnt == 0 or cnt > 4096:
            continue
        total = size * cnt
        if total <= 4:
            voff = ent + 8
        else:
            voff = struct.unpack_from(e + "I", buf, ent + 8)[0]
        if voff + total > n:
            continue
        raw = buf[voff:voff + total]
        try:
            if typ == 2:
                res[tag] = raw.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
            elif typ == 3:
                res[tag] = list(struct.unpack(e + "%dH" % cnt, raw))
            elif typ in (4, 13):
                res[tag] = list(struct.unpack(e + "%dI" % cnt, raw))
            elif typ == 9:
                res[tag] = list(struct.unpack(e + "%di" % cnt, raw))
            elif typ in (5, 10):
                fmt = e + ("%dI" if typ == 5 else "%di") % (2 * cnt)
                v = struct.unpack(fmt, raw)
                res[tag] = [(v[k], v[k + 1]) for k in range(0, len(v), 2)]
            else:
                res[tag] = raw
        except struct.error:
            continue
    return res


def _org_exif_dt(v) -> str:
    if not isinstance(v, str):
        return ""
    m = _ORG_RE_EXIF_DT.match(v.replace("\x00", ""))
    if not m:
        return ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh, mi, ss = (int(m.group(k) or 0) for k in (4, 5, 6))
    if not (1800 <= y <= 2200 and 1 <= mo <= 12 and 1 <= d <= 31
            and hh < 24 and mi < 60 and ss < 61):
        return ""
    return "%04d-%02d-%02d %02d:%02d:%02d" % (y, mo, d, hh, mi, ss)


def _org_gps_coord(vals, ref):
    if not isinstance(vals, list) or not vals:
        return None
    total = 0.0
    for k, (num, den) in enumerate(vals[:3]):
        if den == 0:
            if num == 0:
                continue
            return None
        total += (num / float(den)) / (60.0 ** k)
    if isinstance(ref, str) and ref[:1].upper() in ("S", "W"):
        total = -abs(total)                        # auch bei negativem SRATIONAL
    elif isinstance(ref, str) and ref[:1].upper() in ("N", "E"):
        total = abs(total)
    return total


def _org_tiff_parse(buf: bytes) -> dict:
    """TIFF-Struktur (II*/MM*) ab buf[0] → EXIF-Ergebnis-Dict."""
    if len(buf) < 8:
        return {}
    if buf[:2] == b"II":
        e = "<"
    elif buf[:2] == b"MM":
        e = ">"
    else:
        return {}
    magic, ifd0_off = struct.unpack_from(e + "HI", buf, 2)
    if magic not in (42, 0x4F52, 0x5352, 0x55):
        return {}
    visited = set()
    ifd0 = _org_tiff_ifd(buf, e, ifd0_off, _ORG_EXIF_IFD0, visited)
    sub = {}
    gps = {}
    ptr = ifd0.get(0x8769)
    if isinstance(ptr, list) and ptr:
        sub = _org_tiff_ifd(buf, e, ptr[0], _ORG_EXIF_SUB, visited)
    ptr = ifd0.get(0x8825)
    if isinstance(ptr, list) and ptr:
        gps = _org_tiff_ifd(buf, e, ptr[0], _ORG_EXIF_GPS, visited)
    out = {}
    for tag in (0x9003, 0x9004):
        dt = _org_exif_dt(sub.get(tag))
        if dt:
            out["datetime"] = dt
            break
    else:
        dt = _org_exif_dt(ifd0.get(0x0132))
        if dt:
            out["datetime"] = dt
    for tag, key in ((0x010F, "make"), (0x0110, "model"), (0x0131, "software")):
        v = ifd0.get(tag)
        if isinstance(v, str):
            v = _ORG_RE_CTRL.sub("", v).strip()
            if v:
                out[key] = v
    v = ifd0.get(0x0112)
    if isinstance(v, list) and v and 1 <= v[0] <= 8:
        out["orientation"] = v[0]
    for keys, src in (((0xA002, 0xA003), sub), ((0x0100, 0x0101), ifd0)):
        w, h = src.get(keys[0]), src.get(keys[1])
        if isinstance(w, list) and isinstance(h, list) and w and h and w[0] > 0 and h[0] > 0:
            out["width"], out["height"] = int(w[0]), int(h[0])
            break
    lat = _org_gps_coord(gps.get(2), gps.get(1))
    lon = _org_gps_coord(gps.get(4), gps.get(3))
    if lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180 \
            and not (lat == 0 and lon == 0):
        out["gps"] = (round(lat, 6), round(lon, 6))
    return out


def _org_exif_from_payload(payload: bytes) -> dict:
    if payload.startswith(b"Exif\x00\x00"):
        payload = payload[6:]
    return _org_tiff_parse(payload)


def _org_exif_jpeg(buf: bytes, short=None) -> dict:
    """short (Liste): erhält True, wenn der Puffer vor dem Bildbeginn endete
    (dann lohnt erneutes Lesen mit größerem Puffer)."""
    pos = 2
    n = len(buf)
    for _ in range(200):
        if pos + 4 > n:
            if short is not None:
                short.append(True)
            break
        if buf[pos] != 0xFF:
            nxt = buf.find(b"\xff", pos)                # Neusynchronisieren
            if nxt < 0:
                break
            pos = nxt
            continue
        marker = buf[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker in (0xD9, 0xDA):
            break
        seglen = struct.unpack_from(">H", buf, pos + 2)[0]
        if seglen < 2:
            break
        if marker == 0xE1 and buf[pos + 4:pos + 10] == b"Exif\x00\x00":
            if pos + 2 + seglen > n and short is not None:
                short.append(True)
                return {}
            res = _org_tiff_parse(buf[pos + 10:pos + 2 + seglen])
            if res:
                return res
        pos += 2 + seglen
    return {}


def _org_exif_png(buf: bytes, short=None) -> dict:
    pos = 8
    n = len(buf)
    for _ in range(10000):
        if pos + 8 > n:
            if short is not None:
                short.append(True)
            break
        length, ctype = struct.unpack_from(">I4s", buf, pos)
        if ctype == b"eXIf":
            if pos + 8 + length > n and short is not None:
                short.append(True)
                return {}
            return _org_exif_from_payload(buf[pos + 8:pos + 8 + length])
        if ctype == b"IEND":
            break
        pos += 12 + length
    return {}


def _org_exif_webp(buf: bytes) -> dict:
    pos = 12
    n = len(buf)
    for _ in range(1000):
        if pos + 8 > n:
            break
        ctype, length = struct.unpack_from("<4sI", buf, pos)
        if ctype == b"EXIF":
            return _org_exif_from_payload(buf[pos + 8:pos + 8 + length])
        pos += 8 + length + (length & 1)
    return {}


def _org_exif_search(buf: bytes) -> dict:
    """HEIC/HEIF/CR3 u. a.: EXIF-Block im Kopf suchen."""
    start = 0
    for _ in range(8):
        i = buf.find(b"Exif\x00\x00", start)
        if i < 0:
            break
        res = _org_tiff_parse(buf[i + 6:i + 6 + 1024 * 1024])
        if res:
            return res
        start = i + 6
    for sig in (b"II*\x00", b"MM\x00*"):
        start = 0
        for _ in range(8):
            i = buf.find(sig, start)
            if i < 0:
                break
            res = _org_tiff_parse(buf[i:i + 1024 * 1024])
            if res:
                return res
            start = i + 4
    return {}


def org_read_exif(path: Path) -> dict:
    """EXIF-Basisdaten: datetime, make, model, software, gps, orientation, width, height."""
    try:
        path = _org_prepare(path)
        if path is None:
            return {}
        # erst 128 KB (EXIF steht bei JPEG/PNG fast immer am Anfang), nur bei Bedarf 512 KB
        buf = _org_read(path, _ORG_EXIF_READ_FIRST)
        if len(buf) < 12:
            return {}
        full = len(buf) < _ORG_EXIF_READ_FIRST
        if buf[:3] == b"\xff\xd8\xff" or buf[:8] == b"\x89PNG\r\n\x1a\n":
            fn = _org_exif_jpeg if buf[:1] == b"\xff" else _org_exif_png
            if full:                               # ganze Datei gelesen
                return fn(buf)
            short = []
            res = fn(buf, short)
            if res or not short:
                return res
            return fn(_org_read(path, _ORG_IMG_HEAD))
        if not full:
            buf = _org_read(path, _ORG_IMG_HEAD)
        if buf[:4] in (b"II*\x00", b"MM\x00*", b"IIRO", b"IIU\x00"):
            return _org_tiff_parse(buf)
        if buf[:4] == b"RIFF" and buf[8:12] == b"WEBP":
            return _org_exif_webp(buf)
        if buf[4:8] == b"ftyp":
            return _org_exif_search(buf)
    except Exception:
        pass
    return {}


# ===========================================================================
# Bildgröße aus Dateikopf
# ===========================================================================
_ORG_JPEG_SOF = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB,
                           0xCD, 0xCE, 0xCF})


def _org_jpeg_size(fh):
    """Segmente überspringen (seek), höchstens wenige KB lesen."""
    pos = 2
    for _ in range(500):
        fh.seek(pos)
        hdr = fh.read(4)
        if len(hdr) < 2:
            return None
        if hdr[0] != 0xFF:
            # Füllbytes/Müll zwischen Segmenten: bis zum nächsten 0xFF vorspulen
            fh.seek(pos)
            chunk = fh.read(4096)
            k = chunk.find(b"\xff")
            if k <= 0:
                return None
            pos += k
            continue
        marker = hdr[1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker in (0xD9, 0xDA) or len(hdr) < 4:
            return None
        seglen = struct.unpack(">H", hdr[2:4])[0]
        if seglen < 2:
            return None
        if marker in _ORG_JPEG_SOF:
            d = fh.read(5)
            if len(d) < 5:
                return None
            h, w = struct.unpack(">xHH", d)
            return (w, h) if w > 0 and h > 0 else None
        pos += 2 + seglen
        if pos > 64 * 1024 * 1024:
            return None
    return None


def org_image_size(path: Path) -> tuple | None:
    """(Breite, Höhe) aus dem Dateikopf von JPEG/PNG/GIF/BMP/WebP, sonst None."""
    try:
        path = _org_prepare(path)
        if path is None:
            return None
        with open(path, "rb") as fh:
            head = fh.read(64)
            if head[:3] == b"\xff\xd8\xff":
                return _org_jpeg_size(fh)
            if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR" and len(head) >= 24:
                w, h = struct.unpack(">II", head[16:24])
                return (w, h) if w > 0 and h > 0 else None
            if head[:6] in (b"GIF87a", b"GIF89a") and len(head) >= 10:
                w, h = struct.unpack("<HH", head[6:10])
                return (w, h) if w > 0 and h > 0 else None
            if head[:2] == b"BM" and len(head) >= 26:
                hsize = struct.unpack("<I", head[14:18])[0]
                if hsize == 12:
                    w, h = struct.unpack("<HH", head[18:22])
                else:
                    w, h = struct.unpack("<ii", head[18:26])
                w, h = abs(w), abs(h)
                return (w, h) if w > 0 and h > 0 else None
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP" and len(head) >= 30:
                chunk = head[12:16]
                if chunk == b"VP8 " and head[23:26] == b"\x9d\x01\x2a":
                    w, h = struct.unpack("<HH", head[26:30])
                    w, h = w & 0x3FFF, h & 0x3FFF
                elif chunk == b"VP8L" and head[20] == 0x2F:
                    b = head[21:25]
                    bits = b[0] | (b[1] << 8) | (b[2] << 16) | (b[3] << 24)
                    w = (bits & 0x3FFF) + 1
                    h = ((bits >> 14) & 0x3FFF) + 1
                elif chunk == b"VP8X":
                    w = (head[24] | (head[25] << 8) | (head[26] << 16)) + 1
                    h = (head[27] | (head[28] << 8) | (head[29] << 16)) + 1
                else:
                    return None
                return (w, h) if w > 0 and h > 0 else None
    except Exception:
        pass
    return None
