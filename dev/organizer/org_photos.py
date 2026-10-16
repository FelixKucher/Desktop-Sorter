# ===========================================================================
# ORDNER-ASSISTENT – Fotos & Videos: persönlich / Bildschirmfoto / Chat-Bild,
# dazu Gruppierung persönlicher Fotos nach Ereignissen (Urlaub, Feier, Monat).
# ===========================================================================
# Liest selbst keine Dateien: EXIF steht in f.meta["exif"], die Bildgröße in
# f.meta["size"] (vom Planer vorab befüllt). Optional kann ein Leser (reader)
# übergeben werden, der fehlende Metadaten nachliefert.
# Alle Namen beginnen mit org_ / Org / ORG_ / _org_ph_ (Einzeldatei-Programm).
# Nur Standardbibliothek.
from __future__ import annotations

import math
import re
import time
from datetime import datetime as _org_ph_datetime
from datetime import timedelta as _org_ph_timedelta

from org_core import ORG_CATEGORIES, ORG_JUNK, ORG_PROTECT, OrgFile  # noqa: F401

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
ORG_MONTH_NAMES_DE = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
                      "August", "September", "Oktober", "November", "Dezember")

# Typische Bildschirmauflösungen (Monitore, Laptops, Smartphones, Tablets).
# Beide Ausrichtungen werden unten automatisch ergänzt.
_ORG_PH_SCREEN_BASE = (
    (1920, 1080), (2560, 1440), (3840, 2160), (1366, 768), (1536, 864), (1440, 900),
    (2880, 1800), (1170, 2532), (1179, 2556), (1080, 2400), (1080, 1920), (2560, 1600),
    (3440, 1440),
    # Ergänzungen: weitere verbreitete Monitore / Laptops
    (1280, 720), (1280, 800), (1280, 1024), (1600, 900), (1680, 1050), (1920, 1200),
    (2560, 1080), (2736, 1824), (2256, 1504), (3024, 1964), (3456, 2234), (2880, 1620),
    (5120, 1440), (5120, 2880), (1360, 768),
    # Surface / MacBook / iMac (Retina- und HiDPI-Bildschirmfotos)
    (2880, 1920), (2496, 1664), (3000, 2000), (2560, 1664), (2940, 1912), (2304, 1440),
    (3840, 2400), (3840, 1600), (4096, 2304), (4480, 2520),
    # iPhone / Android
    (750, 1334), (1125, 2436), (1242, 2688), (828, 1792), (1242, 2208), (1284, 2778),
    (1290, 2796), (1080, 2340), (1080, 2280), (1080, 2160), (1440, 3200), (1440, 3120),
    (1440, 3088), (1440, 2960), (1440, 2560), (720, 1600), (720, 1280),
    (640, 1136), (1206, 2622), (1320, 2868), (1344, 2992), (1080, 2408), (1080, 2412),
    (1220, 2712),
    # iPad
    (2048, 2732), (1668, 2388), (1620, 2160), (1640, 2360), (1536, 2048), (1488, 2266),
    (2064, 2752),
)
ORG_SCREEN_SIZES = frozenset(
    [s for s in _ORG_PH_SCREEN_BASE] + [(h, w) for (w, h) in _ORG_PH_SCREEN_BASE])

# Medien-Kategorien, die dieses Modul vergibt (andere Kategorien bleiben unangetastet)
ORG_MEDIA_CATEGORIES = ("photo_personal", "photo_chat", "screenshot", "image",
                        "video_personal", "video")

ORG_STICKER_MAX_BYTES = 200 * 1024          # kleine .webp = Sticker / Chat-Bild
_ORG_PH_RAW_EXT = {".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2"}
_ORG_PH_RASTER_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif"}
_ORG_PH_DAY_CUTOFF_H = 4                    # Fotos bis 4 Uhr zählen zum Vortag (Feiern)

# ---------------------------------------------------------------------------
# Muster (vorkompiliert, auf den Dateinamen OHNE Endung angewendet)
# ---------------------------------------------------------------------------
# Eindeutige Bildschirmfoto-/Bildschirmaufnahme-Namen (irgendwo im Namen)
_ORG_PH_RE_SHOT_STRONG = re.compile(
    r"screen[\s_\-]?shot|bildschirmfoto|bildschirmaufnahme|bildschirmkopie|"
    r"bildschirmaufzeichnung|screen[\s_\-]?recording|screenrecord|screen[\s_\-]?capture|"
    r"screencap|snipaste|captura de pantalla|capture d.\s?[ée]cran|schermafbeelding|"
    r"^rpreplay",                                   # iOS-Bildschirmaufnahme
    re.I)
# Windows-Kopie-Präfixe ("Kopie von X", "Kopie (2) von X", "Copy of X") vor dem Abgleich
_ORG_PH_RE_COPY_PREFIX = re.compile(
    r"^(?:(?:kopie|copy)(?:\s*\(\d+\))?\s+(?:von|of)\s+)+", re.I)
# Schwache Namen (Snipping Tool, Paint, Mac-Snip): nur Wort + Zahlen/Satzzeichen
_ORG_PH_RE_SHOT_WEAK = re.compile(
    r"(?:capture|snip|ausschneiden|unbenannt|untitled)[\W\d_]*", re.I)
# OBS-Standardname für Aufnahmen: "2024-01-01 12-00-00.mkv"
_ORG_PH_RE_OBS = re.compile(r"\d{4}-\d{2}-\d{2}[ _]\d{2}-\d{2}-\d{2}", re.I)

# Chat-Apps: (Muster, App-Name)
_ORG_PH_CHAT_NAMES = tuple((re.compile(p, re.I), app) for p, app in (
    (r"^(?:img|vid|aud|ptt|stk)-\d{8}-wa\d+", "WhatsApp"),
    (r"^whatsapp[\s_\-]+(?:image|video|bild|gif|audio|sticker|foto)", "WhatsApp"),
    (r"^(?:photo|video|sticker|animation|round)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}",
     "Telegram"),
    (r"^(?:photo|video)_\d+@\d{2}-\d{2}-\d{4}_\d{2}-\d{2}-\d{2}", "Telegram"),
    (r"^signal-\d{4}-\d{2}-\d{2}-\d", "Signal"),
    (r"^threema[\-_]", "Threema"),
    (r"^viber[_ ]", "Viber"),
    (r"^received_\d{6,}", "Facebook Messenger"),
    (r"^fb_img_\d{10,}", "Facebook"),
    (r"^\d{6,}_\d{6,}_\d{6,}_[nobs]$", "Facebook/Instagram"),
    (r"^snapchat-\d+", "Snapchat"),
))
# Ordnernamen: App-Name als eigenes Wort ("Telegramm"/"Vibernetz" sind KEINE Chat-Ordner)
_ORG_PH_CHAT_DIRS = tuple((re.compile(r"(?<![a-z])%s(?![a-z])" % k), app) for k, app in (
    ("whatsapp", "WhatsApp"), ("telegram", "Telegram"), ("threema", "Threema"),
    ("viber", "Viber")))
_ORG_PH_CHAT_DIRS_EXACT = {"signal": "Signal", "messenger": "Facebook Messenger"}
_ORG_PH_SHOT_DIRS = {"screenshots", "screenshot", "bildschirmfotos", "screen shots",
                     "bildschirmfoto", "captures"}

# Kamera-/Handy-Dateinamen für Fotos
_ORG_PH_CAM_PHOTO = tuple(re.compile(p, re.I) for p in (
    r"^img_e?\d{4}(?!\d)",            # iPhone / Canon IMG_1234, bearbeitet IMG_E1234
    r"^img_\d{8}_\d{6}",              # Android IMG_20240101_120000
    r"^img\d{14}",                    # OnePlus/Oppo IMG20240101120000
    r"^dsc[_fn]?\d{4,5}(?!\d)",       # Sony/Nikon/Fuji DSC01234, DSC_1234, DSCF1234
    r"^_dsc\d{4}",
    r"^_mg_\d{4}",                    # Canon Adobe-RGB
    r"^p[1-9a-c]\d{6}(?!\d)",         # Panasonic/Olympus P1010001, Okt–Dez PA/PB/PC…
    r"^imgp\d{4}",                    # Pentax
    r"^win_\d{8}_\d{2}_\d{2}_\d{2}",  # Windows-Kamera-App (Webcam/Surface)
    r"^pxl_\d{8}_\d{6,9}",            # Google Pixel
    r"^\d{8}_\d{6}(?!\d)",            # Samsung/LG 20240101_120000
    r"^mvimg_\d{8}_\d{6}",            # Pixel Bewegungsfoto
    r"^dji_\d{4}",                    # Drohne
    r"^gopr\d{4}", r"^g[hxlp]\d{6}",  # GoPro
    r"^sam_\d{4}", r"^cimg\d{4}", r"^100_\d{4}", r"^imag\d{4}",
    r"^wp_\d{8}", r"^pano_\d{8}", r"^burst\d{10}",
))
# Kamera-/Handy-Dateinamen für Videos
_ORG_PH_CAM_VIDEO = tuple(re.compile(p, re.I) for p in (
    r"^vid_\d{8}_\d{6}", r"^vid\d{14}", r"^video_\d{8}_\d{6}",
    r"^pxl_\d{8}_\d{6,9}", r"^\d{8}_\d{6}(?!\d)", r"^mov_\d{4}",
    r"^img_e?\d{4}(?!\d)", r"^img_\d{8}_\d{6}",     # iPhone IMG_1234.MOV
    r"^dji_\d{4}", r"^gopr\d{4}", r"^g[hxlp]\d{6}",
    r"^mvi_\d{4}", r"^dsc[_fn]?\d{4,5}(?!\d)", r"^p[1-9a-c]\d{6}(?!\d)",
    r"^c\d{4}$", r"^mah\d{5}", r"^m2u\d{5}", r"^win_\d{8}_\d{2}_\d{2}_\d{2}",
))
_ORG_PH_RE_AVCHD_NAME = re.compile(r"^\d{5}$")       # 00001.MTS
_ORG_PH_CAM_DIRS = {"dcim", "camera roll", "eigene aufnahmen", "avchd", "bdmv",
                    "m4root", "kamera", "camera"}
# "PhotoScan" (Google) = abfotografierte Papierfotos → bleibt persönlich
_ORG_PH_RE_SCANNER = re.compile(
    r"(?<!photo)scan|mfc|mfp|officejet|deskjet|laserjet|pixma|ecosys|workforce|imagerunner|"
    r"imageclass|bizhub|perfection|\blide\b|\bfi-\d{4}|\bds-\d{3}", re.I)
_ORG_PH_RE_SHOT_SOFTWARE = re.compile(
    r"screenshot|snipping|greenshot|sharex|lightshot|snagit|bildschirm", re.I)

# Datumsangaben
_ORG_PH_RE_EXIF_DT = re.compile(
    r"^\s*(\d{4})[\-:.](\d{1,2})[\-:.](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?")
_ORG_PH_RE_NAME_DT = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[\-_]?(\d{2})[\-_]?(\d{2})"
    r"(?:[\-_ T]?(\d{2})[\-_.:]?(\d{2})[\-_.:]?(\d{2}))?")


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------
def org_month_name(m: int) -> str:
    """Deutscher Monatsname (1 = Januar); ungültig → ""."""
    try:
        m = int(m)
    except (TypeError, ValueError, OverflowError):
        return ""
    if 1 <= m <= 12:
        return ORG_MONTH_NAMES_DE[m - 1]
    return ""


def _org_ph_exif(f) -> dict:
    try:
        meta = f.meta if isinstance(f.meta, dict) else {}
        ex = meta.get("exif")
        return ex if isinstance(ex, dict) else {}
    except Exception:
        return {}


def _org_ph_str(v) -> str:
    if isinstance(v, bytes):
        try:
            v = v.decode("utf-8", "replace")
        except Exception:
            return ""
    if not isinstance(v, str):
        return ""
    return v.replace("\x00", "").strip()


def _org_ph_dims(f, exif: dict):
    """(Breite, Höhe) aus f.meta["size"], sonst aus EXIF; sonst None."""
    cands = []
    try:
        if isinstance(f.meta, dict):
            cands.append(f.meta.get("size"))
    except Exception:
        pass
    cands.append((exif.get("width"), exif.get("height")))
    for c in cands:
        try:
            if c is None or len(c) != 2:
                continue
            w, h = int(c[0]), int(c[1])
            if w > 0 and h > 0:
                return w, h
        except (TypeError, ValueError, OverflowError):
            continue
    return None


def _org_ph_gps(exif: dict):
    """(lat, lon) als floats, wenn gültig; sonst None."""
    g = exif.get("gps")
    try:
        if g is None or len(g) != 2:
            return None
        lat, lon = float(g[0]), float(g[1])
    except (TypeError, ValueError, OverflowError):
        return None
    if math.isnan(lat) or math.isnan(lon):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    if lat == 0.0 and lon == 0.0:
        return None
    return lat, lon


def _org_ph_mk_dt(y, mo, d, h=0, mi=0, s=0):
    try:
        y, mo, d = int(y), int(mo), int(d)
        h, mi, s = int(h or 0), int(mi or 0), int(s or 0)
        if not (1900 <= y <= 2100):
            return None
        if s == 60:                      # Schaltsekunde
            s = 59
        return _org_ph_datetime(y, mo, d, h, mi, s)
    except (TypeError, ValueError, OverflowError):
        return None


def _org_ph_exif_dt(exif: dict):
    """EXIF-Aufnahmezeit als naive Ortszeit (datetime) oder None."""
    s = _org_ph_str(exif.get("datetime"))
    if not s:
        return None
    m = _ORG_PH_RE_EXIF_DT.match(s)
    if not m:
        return None
    return _org_ph_mk_dt(*m.groups())


def _org_ph_name_dt(stem: str):
    """Datum aus Kamera-Dateinamen (IMG_20240101_120000, PXL_..., 20240101_120000)."""
    m = _ORG_PH_RE_NAME_DT.search(stem)
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    if h is not None:
        dt = _org_ph_mk_dt(y, mo, d, h, mi, s)
        if dt is not None:
            return dt
    return _org_ph_mk_dt(y, mo, d, 12, 0, 0)


def _org_ph_mtime_dt(mtime):
    try:
        t = time.localtime(float(mtime))
        return _org_ph_datetime(t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min,
                        min(t.tm_sec, 59))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _org_ph_dir_parts(f) -> list:
    """Ordnerteile des relativen Pfads (klein), ohne Dateinamen."""
    try:
        rel = str(f.rel or "").replace("\\", "/")
    except Exception:
        return []
    parts = [p.strip().lower() for p in rel.split("/")[:-1]]
    return [p for p in parts if p]


def _org_ph_stem_ext(f):
    try:
        name = str(f.path.name)
    except Exception:
        name = str(getattr(f, "rel", "") or "")
    # Windows-Pfad auf POSIX geparst ("C:\\…\\IMG_1.JPG") → nur der Dateiname
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    ext = getattr(f, "ext", "") or ""
    ext = ext.lower() if isinstance(ext, str) else ""
    if not ext and "." in name:
        ext = "." + name.rsplit(".", 1)[-1].lower()
    stem = name[:-len(ext)] if ext and name.lower().endswith(ext) else name
    return stem, ext


def _org_ph_camera_label(exif: dict) -> str:
    make = _org_ph_str(exif.get("make"))
    model = _org_ph_str(exif.get("model"))
    if make and model and model.lower().startswith(make.split(" ")[0].lower()):
        return model
    return (make + " " + model).strip()


_ORG_PH_REASON_KEY = "_org_ph_reason"     # eigene Begründung (für erneute Einordnung)
_ORG_PH_PROTECT_KEY = "_org_ph_protect"   # vorher von anderem Modul geschützt


def _org_ph_set(f, cat: str, reason: str, score: float) -> None:
    f.category = cat
    f.tier = ORG_CATEGORIES.get(cat, {}).get("tier", "")
    f.score = score
    f.add_reason(reason)
    try:
        f.meta[_ORG_PH_REASON_KEY] = reason
    except Exception:
        pass


def _org_ph_match_stem(stem: str) -> str:
    """Dateiname für den Musterabgleich: ohne Rand-Leerzeichen und Kopie-Präfix."""
    s = stem.strip()
    return _ORG_PH_RE_COPY_PREFIX.sub("", s) or s


def _org_ph_chat_app_from_dirs(parts: list) -> str:
    for p in parts:
        if p in _ORG_PH_CHAT_DIRS_EXACT:
            return _ORG_PH_CHAT_DIRS_EXACT[p]
        for rx, app in _ORG_PH_CHAT_DIRS:
            if rx.search(p):
                return app
    return ""


# ---------------------------------------------------------------------------
# Einordnung
# ---------------------------------------------------------------------------
def _org_ph_classify_one(f) -> bool:
    """Ordnet eine Bild-/Videodatei ein. True, wenn eingeordnet."""
    kind = f.kind
    raw_stem, ext = _org_ph_stem_ext(f)
    stem = _org_ph_match_stem(raw_stem)
    exif = _org_ph_exif(f)
    parts = _org_ph_dir_parts(f)
    make = _org_ph_str(exif.get("make"))
    model = _org_ph_str(exif.get("model"))
    software = _org_ph_str(exif.get("software"))
    has_cam = bool(make or model)
    is_video = kind == "video"

    # Jahr nur aus EXIF-Aufnahmedatum (sonst bleibt f.year unverändert / None)
    dt = _org_ph_exif_dt(exif)
    if dt is not None:
        f.year = dt.year

    # 1) Eindeutiger Bildschirmfoto-/Bildschirmaufnahme-Name
    if _ORG_PH_RE_SHOT_STRONG.search(stem):
        _org_ph_set(f, "screenshot", "Bildschirmaufnahme (Dateiname)" if is_video
                    else "Bildschirmfoto (Dateiname)", 0.95)
        return True
    if is_video and not has_cam and _ORG_PH_RE_OBS.fullmatch(stem):
        _org_ph_set(f, "screenshot", "Bildschirmaufnahme (OBS-Dateiname)", 0.7)
        return True

    # 2) Chat-Apps nach Dateiname
    for rx, app in _ORG_PH_CHAT_NAMES:
        if rx.search(stem):
            what = "Video" if is_video else "Bild"
            _org_ph_set(f, "photo_chat", "%s-%s (Dateiname)" % (app, what), 0.95)
            return True

    # 3) Kamera-/Handy-Daten im EXIF
    if has_cam:
        label = _org_ph_camera_label(exif)
        if _ORG_PH_RE_SCANNER.search(make + " " + model + " " + software):
            _org_ph_set(f, "video" if is_video else "image",
                        "Gescanntes Bild (Gerät: %s)" % label, 0.6)
            return True
        _org_ph_set(f, "video_personal" if is_video else "photo_personal",
                    "Kamera: %s" % label, 0.9)
        return True

    # 4) RAW-Format = immer eigene Kamera
    if ext in _ORG_PH_RAW_EXT:
        _org_ph_set(f, "photo_personal", "Kamera-Rohformat (%s)" % ext.lstrip(".").upper(),
                    0.9)
        return True

    # 5) Chat- bzw. Bildschirmfoto-Ordner
    app = _org_ph_chat_app_from_dirs(parts)
    if app:
        _org_ph_set(f, "photo_chat", "Liegt im %s-Ordner" % app, 0.75)
        return True
    if not is_video and any(p in _ORG_PH_SHOT_DIRS for p in parts):
        _org_ph_set(f, "screenshot", "Liegt im Ordner für Bildschirmfotos", 0.7)
        return True
    if software and _ORG_PH_RE_SHOT_SOFTWARE.search(software):
        _org_ph_set(f, "screenshot", "Bildschirmfoto (Programm: %s)" % software, 0.8)
        return True

    if is_video:
        # 6v) Kamera-Dateinamen / Kamera-Ordner für Videos
        if any(rx.search(stem) for rx in _ORG_PH_CAM_VIDEO) or \
                (ext in (".mts", ".m2ts") and _ORG_PH_RE_AVCHD_NAME.match(stem)):
            _org_ph_set(f, "video_personal", "Kamera-Dateiname (%s)" % stem, 0.8)
            return True
        if any(p in _ORG_PH_CAM_DIRS for p in parts):
            _org_ph_set(f, "video_personal", "Liegt im Kamera-Ordner (DCIM)", 0.75)
            return True
        _org_ph_set(f, "video", "Video", 0.4)
        return True

    is_png = ext == ".png"
    dims = _org_ph_dims(f, exif)

    # 6) PNG ohne Kamera-Daten in Bildschirmgröße (auch iPhone IMG_1234.PNG)
    if is_png and dims is not None and dims in ORG_SCREEN_SIZES:
        _org_ph_set(f, "screenshot", "Bildschirmfoto (PNG %d×%d, ohne Kamera-Daten)"
                    % dims, 0.7)
        return True

    # 7) Kamera-Dateinamen / Kamera-Ordner (PNG zählt nicht: Kameras speichern kein PNG)
    if not is_png:
        if any(rx.search(stem) for rx in _ORG_PH_CAM_PHOTO):
            _org_ph_set(f, "photo_personal", "Kamera-Dateiname (%s)" % stem, 0.75)
            return True
        if any(p in _ORG_PH_CAM_DIRS for p in parts) and ext not in (".gif", ".svg"):
            _org_ph_set(f, "photo_personal", "Liegt im Kamera-Ordner (DCIM)", 0.7)
            return True

    # 8) Schwache Bildschirmfoto-Namen (Snipping Tool "Capture", Paint "Unbenannt")
    if ext in _ORG_PH_RASTER_EXT and _ORG_PH_RE_SHOT_WEAK.fullmatch(stem):
        _org_ph_set(f, "screenshot", "Bildschirmfoto (typischer Name „%s“)" % stem, 0.6)
        return True

    # 9) Kleine .webp = Sticker / Webbild aus Chats
    if ext == ".webp":
        try:
            small = 0 <= int(f.size) < ORG_STICKER_MAX_BYTES
        except (TypeError, ValueError):
            small = False
        if small:
            _org_ph_set(f, "photo_chat", "Sticker oder Chat-Bild (kleine WebP-Datei)", 0.55)
            return True

    _org_ph_set(f, "image", "Bild", 0.4)
    return True


def org_classify_media(files: list, reader=None) -> int:
    """Ordnet Bilder/Videos ein: photo_personal, photo_chat, screenshot, image,
    video_personal, video. Übersprungen: in_project, Stufe junk und Dateien, die
    bereits eine Nicht-Medien-Kategorie haben (z. B. "id" für einen Ausweis-Scan).
    reader (optional): reader(f) -> dict, wird mit f.meta zusammengeführt, wenn
    f.meta noch keinen "exif"-Eintrag hat. Liefert die Anzahl eingeordneter Dateien."""
    n = 0
    try:
        items = list(files or [])
    except Exception:
        return 0
    for f in items:
        try:
            if getattr(f, "kind", "") not in ("image", "video"):
                continue
            if getattr(f, "in_project", False) or getattr(f, "tier", "") == ORG_JUNK:
                continue
            cat = getattr(f, "category", "") or ""
            if cat and cat not in ORG_MEDIA_CATEGORIES and cat != "other":
                continue
            if not isinstance(f.meta, dict):
                f.meta = {}
            if reader is not None and "exif" not in f.meta:
                try:
                    extra = reader(f)
                    if isinstance(extra, dict):
                        f.meta.update(extra)
                except Exception:
                    pass
            # Erneute Einordnung: eigene alte Begründung entfernen (fremde bleiben)
            old_reason = f.meta.get(_ORG_PH_REASON_KEY)
            if cat in ORG_MEDIA_CATEGORIES and old_reason and isinstance(f.reasons, list):
                f.reasons[:] = [r for r in f.reasons if r != old_reason]
            # Von einem anderen Modul als wichtig markiert → nie herabstufen
            # (Merker in meta, damit auch eine zweite Einordnung nicht herabstuft)
            keep_protect = bool(f.meta.get(_ORG_PH_PROTECT_KEY)) or (
                getattr(f, "tier", "") == ORG_PROTECT and cat not in ORG_MEDIA_CATEGORIES)
            if _org_ph_classify_one(f):
                n += 1
            if keep_protect:
                f.meta[_ORG_PH_PROTECT_KEY] = True
                if f.tier != ORG_PROTECT:
                    f.tier = ORG_PROTECT
                    f.add_reason("Bleibt geschützt (von anderer Prüfung als wichtig erkannt)")
        except Exception:
            continue                      # defekte Einträge überspringen
    return n


# ---------------------------------------------------------------------------
# Ereignisse
# ---------------------------------------------------------------------------
def org_haversine_km(a, b) -> float:
    """Großkreis-Entfernung in km zwischen (lat, lon)-Paaren; ungültig → 0.0."""
    try:
        lat1, lon1 = math.radians(float(a[0])), math.radians(float(a[1]))
        lat2, lon2 = math.radians(float(b[0])), math.radians(float(b[1]))
        dlat, dlon = lat2 - lat1, lon2 - lon1
        h = math.sin(dlat / 2) ** 2 + \
            math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(max(0.0, h))))
    except (TypeError, ValueError, IndexError, KeyError, OverflowError):
        return 0.0


def _org_ph_median(vals: list) -> float:
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _org_ph_median_point(points: list):
    """Median (lat, lon); Längengrade über die Datumsgrenze (±180°) werden verschoben."""
    if not points:
        return None
    lons = [p[1] for p in points]
    if max(lons) - min(lons) > 180.0:
        lons = [x + 360.0 if x < 0 else x for x in lons]
    lon = _org_ph_median(lons)
    if lon > 180.0:
        lon -= 360.0
    return (_org_ph_median([p[0] for p in points]), lon)


def _org_ph_home_from(points: list):
    """Häufigster Aufenthaltsort: 1°×1°-Gitterzelle mit GPS-Fotos in den meisten
    verschiedenen Monaten, dann an den meisten Tagen (Zuhause verteilt sich über das Jahr,
    eine Reise – auch mit 1000 Fotos – ballt sich in wenigen Tagen), zuletzt die meisten
    Fotos; davon der Median der Punkte. points: (lat, lon, Tag-Ordinal) – Tag darf None sein."""
    if not points:
        return None
    cells: dict = {}
    for p in points:
        key = (math.floor(p[0]), math.floor(p[1]))
        c = cells.get(key)
        if c is None:
            c = cells[key] = (set(), [])
        c[0].add(p[2])
        c[1].append(p)
    def months(days):
        out = set()
        for d in days:
            try:
                dt = _org_ph_datetime.fromordinal(int(d))
                out.add((dt.year, dt.month))
            except (TypeError, ValueError, OverflowError):
                pass
        return len(out)

    best = min(cells.items(), key=lambda kv: (-months(kv[1][0]), -len(kv[1][0]),
                                              -len(kv[1][1]), kv[0]))
    return _org_ph_median_point(best[1][1])


def _org_ph_valid_home(home):
    try:
        if home is None or isinstance(home, (str, bytes, dict)) or len(home) != 2:
            return None
        lat, lon = float(home[0]), float(home[1])
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def _org_ph_ymd(d) -> str:
    return "%04d-%02d-%02d" % (d.year, d.month, d.day)


def _org_ph_event_name(first: _org_ph_datetime, last: _org_ph_datetime) -> str:
    start = first.date()
    # Nachtfotos bis 4 Uhr gehören noch zum Vortag (Silvester, Feiern)
    try:
        end = (last - _org_ph_timedelta(hours=_ORG_PH_DAY_CUTOFF_H)).date()
    except OverflowError:
        end = last.date()
    if end < start:
        end = start
    if end == start:
        return _org_ph_ymd(start)
    if end.year == start.year:
        return "%s bis %02d-%02d" % (_org_ph_ymd(start), end.month, end.day)
    return "%s bis %s" % (_org_ph_ymd(start), _org_ph_ymd(end))


def org_photo_dt(f):
    """Bezugszeit eines Fotos (naive Ortszeit): EXIF → Dateiname → Änderungsdatum."""
    try:
        exif = _org_ph_exif(f)
        dt = _org_ph_exif_dt(exif)
        if dt is not None:
            return dt
        stem, _ext = _org_ph_stem_ext(f)
        dt = _org_ph_name_dt(_org_ph_match_stem(stem))
        if dt is not None:
            return dt
        return _org_ph_mtime_dt(getattr(f, "mtime", None))
    except Exception:
        return None


def _org_ph_set_event(items: list, name: str, year: int) -> None:
    for it in items:
        try:
            it[2].event = name
            it[2].year = year
        except Exception:
            continue


def _org_ph_month_buckets(items: list) -> None:
    for it in items:
        d = it[0]
        try:
            it[2].event = "%04d-%02d %s" % (d.year, d.month, org_month_name(d.month))
            it[2].year = d.year
        except Exception:
            continue


def _org_ph_away_runs(cl: list, home_pt) -> list:
    """Teilt einen Cluster in Abschnitte zu Hause / unterwegs (> 100 km).
    Fotos ohne GPS übernehmen den Zustand des vorherigen Fotos. → [(unterwegs, items)]"""
    last = None
    for it in cl:
        if it[3] is not None:
            last = org_haversine_km(it[3], home_pt) > 100.0
            break
    if last is None:
        return [(False, cl)]
    runs = []
    cur: list = []
    cur_flag = last
    for it in cl:
        flag = (org_haversine_km(it[3], home_pt) > 100.0) if it[3] is not None else last
        last = flag
        if cur and flag != cur_flag:
            runs.append((cur_flag, cur))
            cur = []
        cur_flag = flag
        cur.append(it)
    if cur:
        runs.append((cur_flag, cur))
    return runs


def org_photo_events(files: list, min_event: int = 6, gap_hours: float = 36.0,
                     home=None, max_days: float = 31.0) -> int:
    """Gruppiert persönliche Fotos/Videos zu Ereignissen und setzt f.event/f.year.
    Cluster = aufeinanderfolgende Aufnahmen mit Abstand <= gap_hours. Ab min_event
    Dateien: "2024-07-01" / "2024-07-01 bis 07-14" / "2023-12-30 bis 2024-01-02",
    mit " – Reise", wenn der GPS-Median > 100 km von home (bzw. vom häufigsten Ort)
    entfernt ist. Kleinere Cluster und Dauer-Cluster (> max_days, Reisen > 3×max_days)
    kommen in Monatsordner "2024-07 Juli"; aus Dauer-Clustern (z. B. täglich ein Foto)
    werden Reise-Abschnitte (GPS > 100 km) trotzdem als Ereignis herausgelöst.
    Liefert die Anzahl benannter Ereignisse."""
    try:
        min_event = max(1, int(min_event))
    except (TypeError, ValueError, OverflowError):
        min_event = 6
    try:
        gap_s = float(gap_hours) * 3600.0
        if math.isnan(gap_s):
            raise ValueError
        gap_s = max(0.0, gap_s)
    except (TypeError, ValueError, OverflowError):
        gap_s = 36.0 * 3600.0
    try:
        max_span_s = float(max_days) * 86400.0
        if math.isnan(max_span_s):
            raise ValueError
        max_span_s = max(0.0, max_span_s)
    except (TypeError, ValueError, OverflowError):
        max_span_s = 31.0 * 86400.0
    try:
        items = list(files or [])
    except Exception:
        return 0

    dated = []          # (datetime, laufende Nr., OrgFile, gps)
    all_gps = []        # (lat, lon, Tag)
    for i, f in enumerate(items):
        try:
            if getattr(f, "category", "") not in ("photo_personal", "video_personal"):
                continue
            if getattr(f, "in_project", False):
                continue
            dt = org_photo_dt(f)
            if dt is None:
                continue
            g = _org_ph_gps(_org_ph_exif(f))
            if g is not None:
                all_gps.append((g[0], g[1], dt.toordinal()))
            dated.append((dt, i, f, g))
        except Exception:
            continue
    if not dated:
        return 0
    dated.sort(key=lambda t: (t[0], t[1]))

    home_pt = _org_ph_valid_home(home)
    if home_pt is None:
        home_pt = _org_ph_home_from(all_gps)

    # Cluster bilden (eine Schleife, O(n))
    clusters = []
    cur = [dated[0]]
    for prev, item in zip(dated, dated[1:]):
        if (item[0] - prev[0]).total_seconds() > gap_s:
            clusters.append(cur)
            cur = []
        cur.append(item)
    clusters.append(cur)

    n_events = 0
    for cl in clusters:
        first, last = cl[0][0], cl[-1][0]
        travel = False
        if home_pt is not None:
            med = _org_ph_median_point([it[3] for it in cl if it[3] is not None])
            if med is not None and org_haversine_km(med, home_pt) > 100.0:
                travel = True
        span = (last - first).total_seconds()
        limit = max_span_s * 3 if travel else max_span_s
        if len(cl) >= min_event and span <= limit:
            name = _org_ph_event_name(first, last)
            if travel:
                name += " – Reise"
            n_events += 1
            _org_ph_set_event(cl, name, first.year)
        elif span > limit and home_pt is not None:
            # Dauer-Cluster: Reise-Abschnitte herauslösen, Rest in Monatsordner
            for away, run in _org_ph_away_runs(cl, home_pt):
                r_first, r_last = run[0][0], run[-1][0]
                if away and len(run) >= min_event and \
                        (r_last - r_first).total_seconds() <= max_span_s * 3:
                    n_events += 1
                    _org_ph_set_event(run, _org_ph_event_name(r_first, r_last) + " – Reise",
                                      r_first.year)
                else:
                    _org_ph_month_buckets(run)
        else:
            _org_ph_month_buckets(cl)
    return n_events
