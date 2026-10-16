# Adversariale Tests für org_photos (python test_org_photos_adversarial.py → Exit 0 = ok)
from __future__ import annotations

import math
import random
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

from org_core import ORG_CATEGORIES, ORG_JUNK, ORG_KEEP, ORG_PROTECT, ORG_REVIEW, OrgFile, org_kind
import org_photos
from org_photos import (ORG_SCREEN_SIZES, org_classify_media, org_haversine_km,
                        org_month_name, org_photo_dt, org_photo_events)

ROOT = Path("/root")
HAMBURG = (53.55, 9.99)
ROME = (41.90, 12.49)
RESULTS = {"ok": 0, "fail": 0}


def check(name, cond, detail=""):
    if cond:
        RESULTS["ok"] += 1
    else:
        RESULTS["fail"] += 1
        print("FEHLER:", name, detail)


def mk(rel, size=500_000, exif=None, dims=None, mtime=1700000000.0, path=None, **kw):
    p = path if path is not None else ROOT / rel
    f = OrgFile(path=p, rel=rel, size=size, mtime=mtime, kind=org_kind(Path(str(p))),
                ext=Path(str(p)).suffix.lower(), meta={"exif": exif or {}, "size": dims})
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def cls(rel, **kw):
    f = mk(rel, **kw)
    org_classify_media([f])
    return f


def photo(name, dt, gps=None, cat="photo_personal"):
    ex = {"make": "Apple", "model": "iPhone"}
    if dt is not None:
        ex["datetime"] = dt.strftime("%Y-%m-%d %H:%M:%S")
    if gps is not None:
        ex["gps"] = gps
    f = mk(name, exif=ex)
    f.category = cat
    f.tier = ORG_PROTECT
    return f


# ---------------------------------------------------------------------------
# Echte Dateinamen, die der Erbauer nicht bedacht hat
REAL_NAMES = [
    # Olympus/OM: Monat 10–12 als A/B/C → PA010001 usw.
    ("PA010001.JPG", {}, "photo_personal"),
    ("PB120034.JPG", {}, "photo_personal"),
    ("PC250001.ORF", {}, "photo_personal"),
    ("PC250002.JPG", {}, "photo_personal"),
    # Pentax
    ("IMGP1234.JPG", {}, "photo_personal"),
    # Windows-Kamera-App (Webcam/Surface) → Ordner "Eigene Aufnahmen"/"Camera Roll"
    ("WIN_20240101_12_00_00_Pro.jpg", {}, "photo_personal"),
    ("WIN_20240101_12_00_00_Pro.mp4", {}, "video_personal"),
    # iOS-Bildschirmaufnahme
    ("RPReplay_Final1700000000.MP4", {}, "screenshot"),
    ("ScreenRecording_01-01-2024 12-00-00_1.MP4", {}, "screenshot"),
    # Windows-Kopien (deutsch/englisch, alt und neu)
    ("Kopie von IMG_1234.JPG", {}, "photo_personal"),
    ("Copy of DSC01234.JPG", {}, "photo_personal"),
    ("IMG_1234 - Kopie.JPG", {}, "photo_personal"),
    ("IMG_1234 - Kopie (2).JPG", {}, "photo_personal"),
    ("Kopie (2) von IMG_1234.JPG", {}, "photo_personal"),
    ("Kopie von WhatsApp Image 2024-01-01 at 12.00.00.jpeg", {}, "photo_chat"),
    ("Kopie von VID_20240101_120000.mp4", {}, "video_personal"),
    # führendes Leerzeichen (Windows erlaubt es)
    (" IMG_1234.JPG", {}, "photo_personal"),
    # bearbeitete Varianten
    ("IMG_1234_bearbeitet.jpg", {}, "photo_personal"),
    ("PXL_20240101_120000123-edited.jpg", {}, "photo_personal"),
    ("20240101_120000(0).jpg", {}, "photo_personal"),
    # neuere Telefone im PNG-Screenshot-Format
    ("IMG_0001.PNG", {"dims": (1206, 2622)}, "screenshot"),    # iPhone 16 Pro
    ("IMG_0002.PNG", {"dims": (1320, 2868)}, "screenshot"),    # iPhone 16 Pro Max
    ("IMG_0003.PNG", {"dims": (640, 1136)}, "screenshot"),     # iPhone SE (1. Gen.)
    ("Screenshot_x.png", {"dims": (1344, 2992)}, "screenshot"),
    ("bild.png", {"dims": (1080, 2408)}, "screenshot"),
    ("bild2.png", {"dims": (2880, 1920)}, "screenshot"),       # Surface Pro
    ("bild3.png", {"dims": (2560, 1664)}, "screenshot"),       # MacBook Air M2
    ("bild4.png", {"dims": (2940, 1912)}, "screenshot"),       # MacBook Air 15"
    # Ordner mit ähnlichen Namen, die KEINE Chat-Ordner sind
    ("Telegramme von Opa 1945/scan1.jpg", {}, "image"),
    ("Telegramm/brief.jpg", {}, "image"),
    ("Familie/Vibernetz/foto.jpg", {}, "image"),
    # echte Chat-Ordner mit Unterstrich/Bindestrich
    ("whatsapp_backup/foto.jpg", {}, "photo_chat"),
    ("Telegram-Export/foto.jpg", {}, "photo_chat"),
    ("WhatsApp Images/Sent/foto.jpg", {}, "photo_chat"),
    # Google PhotoScan: abfotografierte Familienfotos → persönlich
    ("PhotoScan_20240101.jpg", {"exif": {"make": "Google", "model": "Pixel 7",
                                        "software": "PhotoScan"}}, "photo_personal"),
    # Echte Scanner bleiben "image"
    ("Scan_0001.jpg", {"exif": {"make": "EPSON", "model": "Perfection V600"}}, "image"),
    ("Scan_0002.jpg", {"exif": {"make": "Brother", "model": "MFC-L2710DW"}}, "image"),
    # Kamera, deren Name nicht wie ein Scanner aussehen darf
    ("a.jpg", {"exif": {"make": "SONY", "model": "DSC-RX100M7"}}, "photo_personal"),
    ("b.jpg", {"exif": {"make": "FUJIFILM", "model": "X-T4"}}, "photo_personal"),
    # Umlaute, Emoji, sehr lange Namen
    ("Oma & Opa 🎉 Geburtstag ÄÖÜß.jpg", {"exif": {"make": "samsung", "model": "SM-G991B"}},
     "photo_personal"),
    ("Ü" * 200 + ".jpg", {}, "image"),
    ("IMG_1234" + "x" * 230 + ".JPG", {}, "photo_personal"),
    # Wortähnliche Namen, die nicht als Screenshot gelten dürfen
    ("Capture the flag.png", {}, "image"),
    ("Untitled design (1).png", {}, "image"),
    ("Snippet Rezept.jpg", {}, "image"),
    ("Bewerbungsfoto.png", {"dims": (600, 800)}, "image"),
    # Leerzeichen-only EXIF
    ("leer.jpg", {"exif": {"make": "   ", "model": "\x00\x00"}}, "image"),
]


def test_real_world_names():
    for rel, kw, want in REAL_NAMES:
        f = cls(rel, **kw)
        check("name " + rel[:60], f.category == want,
              "→ %s (%s) erwartet %s" % (f.category, f.reasons, want))


def test_windows_paths():
    # Backslash-Pfade (z. B. aus einem Windows-Export, auf POSIX geparst)
    p = PurePosixPath("C:\\Users\\Felix\\Bilder\\Urlaub\\IMG_1234.JPG")
    f = mk("Urlaub\\IMG_1234.JPG", path=p)
    org_classify_media([f])
    check("backslash posix path", f.category == "photo_personal", (f.category, f.reasons))
    # rel mit Backslash und DCIM
    f = mk("Handy\\DCIM\\Camera\\abc.jpg")
    org_classify_media([f])
    check("backslash dcim", f.category == "photo_personal", f.category)
    # rel mit Backslash und Screenshot-Ordner
    f = mk("Bilder\\Screenshots\\abc.jpg")
    org_classify_media([f])
    check("backslash screenshots", f.category == "screenshot", f.category)
    # Laufwerksbuchstabe im rel (sollte nicht vorkommen, darf aber nicht stören)
    f = mk("D:/DCIM/100CANON/x.jpg")
    org_classify_media([f])
    check("drive letter rel", f.category == "photo_personal", f.category)
    # path=None → kein Absturz, Name aus rel
    f = mk("IMG_1234.JPG")
    f.path = None
    check("path None", org_classify_media([f]) == 1 and f.category == "photo_personal",
          f.category)


def test_never_junk_never_downgrade():
    """Schlimmster Fehler: Wichtiges als Müll. Dieses Modul darf nie junk setzen
    und nie eine von anderen Modulen gesetzte Schutzstufe herabsetzen."""
    rnd = random.Random(7)
    names = ["IMG_%d.JPG", "Screenshot %d.png", "WhatsApp Image %d.jpeg", "x%d.webp",
             "Unbenannt%d.png", "Lebenslauf %d.jpg", "%d.png", "VID_%d.mp4", "a%d.raw"]
    fs = []
    for i in range(3000):
        ex = rnd.choice([{}, {"make": "Canon"}, {"software": "Greenshot"}, "x", None])
        f = mk(names[i % len(names)] % i, exif=ex if isinstance(ex, dict) else {},
               dims=rnd.choice([None, (1920, 1080), (5, 5), ("a", "b")]),
               size=rnd.choice([0, 10, 10 ** 6, None]))
        if not isinstance(ex, dict):
            f.meta["exif"] = ex
        fs.append(f)
    org_classify_media(fs)
    check("never junk", all(f.tier != ORG_JUNK for f in fs),
          [f.rel for f in fs if f.tier == ORG_JUNK][:3])
    check("tier matches category",
          all(f.tier == ORG_CATEGORIES[f.category]["tier"] for f in fs if f.category))
    # Schutzstufe von anderem Modul (ohne Kategorie, z. B. Ausweis per Text erkannt)
    f = mk("Screenshot Personalausweis.png", tier=ORG_PROTECT)
    org_classify_media([f])
    check("protect kept", f.tier == ORG_PROTECT, (f.category, f.tier))
    org_classify_media([f])                      # zweiter Lauf darf nicht herabstufen
    check("protect kept rerun", f.tier == ORG_PROTECT, (f.category, f.tier))
    # Kategorie "cv" für Bewerbungsfoto bleibt unangetastet
    f = mk("Bewerbungsfoto.jpg", category="cv", tier=ORG_PROTECT)
    org_classify_media([f])
    check("cv kept", f.category == "cv" and f.tier == ORG_PROTECT)
    # bereits junk (Duplikat) bleibt junk, auch mit Kamera-EXIF
    f = mk("IMG_1.JPG", exif={"make": "Apple"}, tier=ORG_JUNK, category="duplicate")
    org_classify_media([f])
    check("junk untouched", f.category == "duplicate" and f.tier == ORG_JUNK)


def test_reclassify_no_stale_reasons():
    f = mk("foo.jpg")
    org_classify_media([f])
    first = list(f.reasons)
    f.meta["exif"] = {"make": "Apple", "model": "iPhone 12"}
    org_classify_media([f])
    check("reclass category", f.category == "photo_personal")
    check("no stale reason", not any(r in f.reasons for r in first), f.reasons)
    # fremde Begründungen anderer Module bleiben erhalten
    g = mk("foo2.jpg")
    g.add_reason("Fremdmodul: Hinweis")
    org_classify_media([g])
    g.meta["exif"] = {"make": "Apple"}
    org_classify_media([g])
    check("foreign reason kept", "Fremdmodul: Hinweis" in g.reasons, g.reasons)
    # Idempotent: zweimal gleich → gleiche Begründungen
    h = mk("IMG_1.JPG", exif={"make": "Apple", "model": "iPhone"})
    org_classify_media([h])
    r1 = list(h.reasons)
    org_classify_media([h])
    check("idempotent", h.reasons == r1, (r1, h.reasons))


def test_public_functions_never_raise():
    for v in (float("inf"), float("-inf"), float("nan"), None, "3", b"3", [], {}, 10 ** 100):
        try:
            r = org_month_name(v)
            ok = isinstance(r, str)
        except Exception as e:      # noqa: BLE001
            ok, r = False, repr(e)
        check("month_name %r" % (v,), ok, r)
    check("month_name '3'", org_month_name("3") == "März")
    for a, b in ((None, (1, 2)), ((1,), (1, 2)), (("x", "y"), (1, 2)), ((1, 2), None),
                 ((float("nan"), 1), (1, 2)), ((1e308, 1e308), (0, 0))):
        try:
            d = org_haversine_km(a, b)
            ok = isinstance(d, float)
        except Exception as e:      # noqa: BLE001
            ok, d = False, repr(e)
        check("haversine %r %r" % (a, b), ok, d)
    for obj in (None, 3, "x", object()):
        try:
            org_photo_dt(obj)
            ok = True
        except Exception:           # noqa: BLE001
            ok = False
        check("photo_dt %r" % (type(obj),), ok)
    for kw in ({"min_event": float("nan")}, {"gap_hours": float("nan")},
               {"gap_hours": float("inf")}, {"max_days": float("nan")},
               {"max_days": -5}, {"min_event": -3}, {"home": "12"}, {"home": b"ab"},
               {"home": {"lat": 1}}, {"home": (float("inf"), 0)}):
        fs = [photo("k%d.jpg" % i, datetime(2020, 1, 1, 10) + timedelta(minutes=i), HAMBURG)
              for i in range(8)]
        try:
            n = org_photo_events(fs, **kw)
            ok = isinstance(n, int) and all(f.event for f in fs)
        except Exception as e:      # noqa: BLE001
            ok, n = False, repr(e)
        check("events kw %r" % (kw,), ok, n)
    # home als String "12" darf nicht als (1.0, 2.0) gelten → automatisch (Hamburg)
    fs = [photo("s%d.jpg" % i, datetime(2020, 1, 1, 10) + timedelta(minutes=i), HAMBURG)
          for i in range(8)]
    org_photo_events(fs, home="12")
    check("home str ignored", "Reise" not in fs[0].event, fs[0].event)

    class Frozen(object):
        __slots__ = ("category", "in_project", "meta", "path", "rel", "ext", "mtime")

        def __init__(self, i):
            self.category = "photo_personal"
            self.in_project = False
            self.meta = {"exif": {"datetime": "2020-01-01 10:%02d:00" % i}}
            self.path = Path("x%d.jpg" % i)
            self.rel = "x%d.jpg" % i
            self.ext = ".jpg"
            self.mtime = 0.0
    try:
        n = org_photo_events([Frozen(i) for i in range(8)])
        ok = True
    except Exception as e:          # noqa: BLE001
        ok, n = False, repr(e)
    check("events on objects without event attr", ok, n)


def _folder_safe(name):
    bad = set('<>:"/\\|?*')
    return name and not (set(name) & bad) and not name.endswith((" ", ".")) and \
        all(ord(c) >= 32 for c in name)


def test_event_names_are_valid_folder_names():
    rnd = random.Random(3)
    fs = []
    for i in range(2000):
        dt = datetime(1995, 1, 1) + timedelta(minutes=rnd.randint(0, 60 * 24 * 365 * 30))
        gps = rnd.choice([None, HAMBURG, ROME, (-33.9, 151.2), (40.7, -74.0)])
        fs.append(photo("r%d.jpg" % i, dt, gps))
    # dazu mtime-Fallback mit sehr altem Datum
    f = photo("old.jpg", None)
    f.mtime = -1e9               # 1938 (auf POSIX gültig)
    fs.append(f)
    org_photo_events(fs)
    names = {f.event for f in fs if f.event}
    check("folder-safe names", all(_folder_safe(n) for n in names),
          [n for n in names if not _folder_safe(n)][:5])
    check("all have event", all(f.event for f in fs))
    check("all have year", all(isinstance(f.year, int) for f in fs))


def test_home_by_days_not_photo_count():
    # 200 Alltagsfotos zu Hause über 200 Tage, dazu EINE Reise mit 1000 Fotos in 10 Tagen.
    # Häufigster Ort ist Hamburg (an viel mehr Tagen), nicht Rom.
    days = [datetime(2023, 1, 1, 12) + timedelta(days=i * 1.7) for i in range(220)]
    days = [d for d in days if not datetime(2023, 7, 29) <= d <= datetime(2023, 8, 13)][:200]
    base = [photo("h%d.jpg" % i, d, HAMBURG) for i, d in enumerate(days)]
    trip = [photo("t%d.jpg" % i, datetime(2023, 8, 1, 8) + timedelta(minutes=14 * i),
                  (ROME[0] + 0.001 * (i % 7), ROME[1])) for i in range(1000)]
    org_photo_events(base + trip)
    check("trip is Reise", trip[0].event.endswith(" – Reise"), trip[0].event)
    check("home not Reise", not any("Reise" in f.event for f in base),
          [f.event for f in base if "Reise" in f.event][:2])


def test_daily_shooter_trip_extracted():
    # Täglich ein Foto zu Hause (ein Jahr), dazwischen 12 Tage Rom mit vielen Fotos.
    # Ohne Sonderbehandlung wäre alles ein Riesen-Cluster → nur Monatsordner.
    fs = []
    d0 = datetime(2022, 1, 1, 18)
    for i in range(365):
        d = d0 + timedelta(days=i)
        if datetime(2022, 7, 10) <= d < datetime(2022, 7, 22):
            for k in range(10):
                fs.append(photo("r%d_%d.jpg" % (i, k), d.replace(hour=9) + timedelta(hours=k),
                                ROME))
        else:
            fs.append(photo("h%d.jpg" % i, d, HAMBURG))
    n = org_photo_events(fs)
    trip = [f for f in fs if f.rel.startswith("r")]
    home = [f for f in fs if f.rel.startswith("h")]
    check("daily trip n", n == 1, n)
    check("daily trip name", trip[0].event == "2022-07-10 bis 07-21 – Reise", trip[0].event)
    check("daily trip uniform", len({f.event for f in trip}) == 1)
    check("daily home months", home[0].event == "2022-01 Januar" and
          all("Reise" not in f.event for f in home))


def test_antimeridian():
    # Wohnort Taveuni (Fidschi) direkt an der Datumsgrenze: Fotos auf beiden Seiten.
    pts = [(-16.85, 179.98), (-16.85, -179.98)]
    base = [photo("f%d.jpg" % i, datetime(2021, 1, 1, 12) + timedelta(days=5 * i), pts[i % 2])
            for i in range(40)]
    party = [photo("p%d.jpg" % i, datetime(2021, 9, 4, 18) + timedelta(minutes=20 * i),
                   pts[i % 2]) for i in range(8)]
    org_photo_events(base + party)
    check("antimeridian no false Reise", party[0].event == "2021-09-04", party[0].event)
    # Reise von dort nach Neuseeland bleibt eine Reise
    trip = [photo("n%d.jpg" % i, datetime(2021, 11, 1, 9) + timedelta(hours=3 * i),
                  (-36.85, 174.76)) for i in range(8)]
    org_photo_events(base + trip)
    check("antimeridian trip", trip[0].event.endswith(" – Reise"), trip[0].event)


def test_boundaries():
    # Genau 100 km entfernt → keine Reise (Regel: > 100 km)
    home = (50.0, 8.0)
    dlat = 100.0 / 111.195            # ~1° Breite = 111,195 km
    near = (50.0 + dlat - 1e-6, 8.0)
    far = (50.0 + dlat + 0.01, 8.0)
    a = [photo("a%d.jpg" % i, datetime(2020, 5, 1, 10) + timedelta(hours=i), near)
         for i in range(6)]
    b = [photo("b%d.jpg" % i, datetime(2020, 6, 1, 10) + timedelta(hours=i), far)
         for i in range(6)]
    org_photo_events(a + b, home=home)
    check("≤100 km no Reise", "Reise" not in a[0].event, (a[0].event,
          org_haversine_km(near, home)))
    check(">100 km Reise", b[0].event.endswith(" – Reise"), b[0].event)
    # min_event genau erreicht / knapp verfehlt
    c = [photo("c%d.jpg" % i, datetime(2020, 7, 1, 10) + timedelta(minutes=i)) for i in range(6)]
    check("min_event exact", org_photo_events(c) == 1 and c[0].event == "2020-07-01")
    c = c[:5]
    check("min_event-1", org_photo_events(c) == 0 and c[0].event == "2020-07 Juli")
    # 31 Tage genau → Ereignis; 31 Tage + 1 min → Monate (ohne GPS, keine Reise)
    t0 = datetime(2020, 1, 1, 10)
    d = [photo("d%d.jpg" % i, t0 + timedelta(days=i)) for i in range(31)]
    d.append(photo("dz.jpg", t0 + timedelta(days=31)))
    check("31 days event", org_photo_events(d) == 1, d[0].event)
    d[-1] = photo("dz.jpg", t0 + timedelta(days=31, minutes=1))
    check("31 days+1 min months", org_photo_events(d) == 0, d[0].event)
    # Herbst-Zeitumstellung: doppelte Stunde 02:00–03:00 → keine negativen Lücken
    e = [photo("e%d.jpg" % i, datetime(2023, 10, 29, 1, 30) + timedelta(minutes=10 * i))
         for i in range(8)]
    e.append(photo("e_dup.jpg", datetime(2023, 10, 29, 2, 5)))
    check("dst autumn", org_photo_events(e) == 1 and len({f.event for f in e}) == 1)
    # Schaltjahr 29.02.
    g = [photo("g%d.jpg" % i, datetime(2024, 2, 29, 10) + timedelta(minutes=i)) for i in range(6)]
    check("leap day", org_photo_events(g) == 1 and g[0].event == "2024-02-29", g[0].event)
    # Event über Monatsgrenze im selben Jahr
    h = [photo("h%d.jpg" % i, datetime(2024, 4, 29, 10) + timedelta(hours=10 * i))
         for i in range(8)]
    org_photo_events(h)
    check("month span", h[0].event == "2024-04-29 bis 05-02", h[0].event)


def test_mixed_sources_and_bad_dates():
    # EXIF-Datum mit Zeitzone, ISO-T, Punkten; Dateinamen-Datum; ungültige Monate
    f = photo("a.jpg", None)
    f.meta["exif"]["datetime"] = "2021-05-06T07:08:09+02:00"
    check("iso tz", org_photo_dt(f) == datetime(2021, 5, 6, 7, 8, 9), org_photo_dt(f))
    f.meta["exif"]["datetime"] = "2021.05.06 07:08:09"
    check("dots", org_photo_dt(f) == datetime(2021, 5, 6, 7, 8, 9))
    f.meta["exif"]["datetime"] = "2021:13:40 07:08:09"
    f.mtime = time.mktime((2019, 1, 2, 3, 4, 5, 0, 0, -1))
    check("bad exif → mtime", org_photo_dt(f) == datetime(2019, 1, 2, 3, 4, 5), org_photo_dt(f))
    # Dateiname mit Unsinn-Datum darf nicht gewinnen
    g = photo("IMG_20241399_999999.jpg", None)
    g.mtime = time.mktime((2018, 6, 7, 8, 9, 10, 0, 0, -1))
    check("bad name date → mtime", org_photo_dt(g) == datetime(2018, 6, 7, 8, 9, 10),
          org_photo_dt(g))
    # Klassifizierung mit kaputtem EXIF-Jahr setzt kein Jahr
    h = cls("IMG_1.JPG", exif={"make": "Apple", "datetime": "9999:01:01 00:00:00"})
    check("year 9999 ignored", h.year is None, h.year)


def test_performance_60k():
    rnd = random.Random(1)
    names = ["IMG_%04d.JPG", "WhatsApp Image %d.jpeg", "Screenshot_%d.png", "DSC%05d.JPG",
             "Urlaub %d.jpg", "PXL_20230101_%06d.jpg", "VID_20230101_%06d.mp4", "logo%d.png",
             "Kopie von IMG_%04d.JPG", "PA%06d.JPG"]
    fs = []
    t0 = datetime(2010, 1, 1)
    for i in range(60_000):
        exif = {}
        if i % 2 == 0:
            dt = t0 + timedelta(minutes=rnd.randint(0, 60 * 24 * 365 * 14))
            exif = {"make": "Apple", "model": "iPhone", "datetime": dt.strftime("%Y:%m:%d %H:%M:%S")}
            if i % 4 == 0:
                exif["gps"] = (rnd.uniform(-60, 70), rnd.uniform(-179.9, 179.9))
        fs.append(mk("A/%d/%s" % (i % 300, names[i % len(names)] % i), exif=exif,
                     dims=(1920, 1080) if i % 9 == 0 else (4032, 3024),
                     mtime=1.3e9 + rnd.randint(0, 400_000_000)))
    s = time.perf_counter()
    n = org_classify_media(fs)
    m = time.perf_counter()
    e = org_photo_events(fs)
    end = time.perf_counter()
    check("60k classified", n == 60_000, n)
    check("60k time", end - s < 10.0, end - s)
    pers = [f for f in fs if f.category in ("photo_personal", "video_personal")]
    check("60k events set", all(f.event for f in pers))
    # Worst Case: alle Fotos in einem einzigen Dauer-Cluster (alle 10 min ein Foto)
    big = [photo("w%d.jpg" % i, datetime(2020, 1, 1) + timedelta(minutes=10 * i),
                 HAMBURG if i % 50 else ROME) for i in range(60_000)]
    s2 = time.perf_counter()
    org_photo_events(big)
    d2 = time.perf_counter() - s2
    check("60k one cluster time", d2 < 10.0, d2)
    check("60k one cluster events", all(f.event for f in big))
    print("  Leistung: 60.000 Dateien klassifizieren %.2f s, Ereignisse %.2f s, "
          "Dauer-Cluster %.2f s (%d Ereignisse)" % (m - s, end - m, d2, e))


def test_top_level_prefixes():
    import ast
    src = Path(org_photos.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__" \
                and node.module != "org_core":
            names += [a.asname or a.name for a in node.names]
    ok = ("org_", "Org", "ORG_", "_org_", "_ORG_")
    bad = [n for n in names if not n.startswith(ok)]
    check("prefixes", not bad, bad)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
        except Exception:
            RESULTS["fail"] += 1
            print("AUSNAHME in", t.__name__)
            traceback.print_exc()
    print("org_photos adversarial: %d Prüfungen bestanden, %d fehlgeschlagen "
          "(%d Testfunktionen, Python %s)" % (RESULTS["ok"], RESULTS["fail"], len(tests),
                                               sys.version.split()[0]))
    return 1 if RESULTS["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
