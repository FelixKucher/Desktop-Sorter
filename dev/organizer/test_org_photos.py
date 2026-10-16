# Tests für org_photos (python test_org_photos.py → Exit-Code 0 = bestanden)
from __future__ import annotations

import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from org_core import ORG_JUNK, ORG_KEEP, ORG_PROTECT, ORG_REVIEW, OrgFile, org_kind
from org_photos import (ORG_SCREEN_SIZES, org_classify_media, org_haversine_km,
                        org_month_name, org_photo_dt, org_photo_events)

ROOT = Path("/root")
HAMBURG = (53.55, 9.99)
ROME = (41.90, 12.49)
LUEBECK = (53.87, 10.69)   # ~55 km von Hamburg


def mk(rel, size=500_000, exif=None, dims=None, mtime=None, meta=True, **kw):
    p = ROOT / rel
    m = {}
    if meta:
        m["exif"] = exif or {}
        m["size"] = dims
    f = OrgFile(path=p, rel=rel, size=size,
                mtime=mtime if mtime is not None else 1700000000.0,
                kind=org_kind(p), ext=p.suffix.lower(), meta=m)
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def cls(rel, **kw):
    f = mk(rel, **kw)
    org_classify_media([f])
    return f


CAM = {"make": "Apple", "model": "iPhone 13", "datetime": "2023-07-14 18:22:01"}

# (relativer Pfad, kwargs, erwartete Kategorie)
NAME_CASES = [
    # --- Chat ---
    ("IMG-20240101-WA0001.jpg", {}, "photo_chat"),
    ("VID-20240101-WA0003.mp4", {}, "photo_chat"),
    ("STK-20240101-WA0002.webp", {}, "photo_chat"),
    ("WhatsApp Image 2024-01-01 at 12.00.00.jpeg", {}, "photo_chat"),
    ("WhatsApp Video 2024-01-01 at 12.00.00.mp4", {}, "photo_chat"),
    ("WhatsApp Bild 2024-01-01 um 12.00.00_abcd1234.jpg", {}, "photo_chat"),
    ("photo_2024-01-01_12-00-00.jpg", {}, "photo_chat"),
    ("video_2024-01-01_12-00-00.mp4", {}, "photo_chat"),
    ("photo_123@01-01-2024_12-00-00.jpg", {}, "photo_chat"),
    ("signal-2024-01-01-120000.jpeg", {}, "photo_chat"),
    ("signal-2024-01-01-12-00-00-123.jpg", {}, "photo_chat"),
    ("threema-20240101-120000-1234.jpg", {}, "photo_chat"),
    ("viber_image_2024-01-01_12-00-00-123.jpg", {}, "photo_chat"),
    ("received_123456789012345.jpeg", {}, "photo_chat"),
    ("FB_IMG_1700000000000.jpg", {}, "photo_chat"),
    ("Snapchat-123456789.jpg", {}, "photo_chat"),
    ("123456789_1234567890123_9876543210987_n.jpg", {}, "photo_chat"),
    ("sticker.webp", {"size": 30_000}, "photo_chat"),
    ("WhatsApp/Media/WhatsApp Images/random.jpg", {}, "photo_chat"),
    ("Telegram Desktop/funny.png", {}, "photo_chat"),
    # --- Bildschirmfotos ---
    ("Screenshot 2024-01-01 120000.png", {}, "screenshot"),
    ("Screenshot_20240101-120000_Chrome.jpg", {}, "screenshot"),
    ("Screenshot_20240101-120000_WhatsApp.jpg", {}, "screenshot"),
    ("Screen Shot 2020-01-01 at 12.00.00.png", {}, "screenshot"),
    ("Bildschirmfoto 2024-01-01 um 12.00.00.png", {}, "screenshot"),
    ("Bildschirmaufnahme 2024-01-01 um 12.00.00.mov", {}, "screenshot"),
    ("Screenshot (12).png", {}, "screenshot"),
    ("Snip20240101_1.png", {}, "screenshot"),
    ("Snipaste_2024-01-01_12-00-00.png", {}, "screenshot"),
    ("Capture.PNG", {}, "screenshot"),
    ("Capture (3).jpg", {}, "screenshot"),
    ("Ausschneiden.png", {}, "screenshot"),
    ("Unbenannt.png", {}, "screenshot"),
    ("Unbenannt-1.jpg", {}, "screenshot"),
    ("Pictures/Screenshots/foo.jpg", {}, "screenshot"),
    ("2024-01-01 12-00-00.mkv", {}, "screenshot"),          # OBS
    ("IMG_1234.PNG", {"dims": (1170, 2532)}, "screenshot"),   # iPhone-Screenshot
    ("desktop.png", {"dims": (1920, 1080)}, "screenshot"),
    ("hochkant.png", {"dims": (1080, 1920)}, "screenshot"),
    ("ultrawide.png", {"dims": (1440, 3440)}, "screenshot"),  # gedreht
    ("exifdims.png", {"exif": {"width": 2560, "height": 1440}}, "screenshot"),
    ("tool.png", {"exif": {"software": "Greenshot"}}, "screenshot"),
    # --- Persönliche Fotos ---
    ("IMG_1234.JPG", {}, "photo_personal"),
    ("IMG_E1234.HEIC", {}, "photo_personal"),
    ("IMG_1234 2.jpg", {}, "photo_personal"),
    ("IMG_20240101_120000.jpg", {}, "photo_personal"),
    ("IMG20240101120000.jpg", {}, "photo_personal"),
    ("DSC01234.JPG", {}, "photo_personal"),
    ("DSC_1234.JPG", {}, "photo_personal"),
    ("DSCF1234.JPG", {}, "photo_personal"),
    ("DSCN1234.JPG", {}, "photo_personal"),
    ("P1010001.JPG", {}, "photo_personal"),
    ("PXL_20240101_120000123.jpg", {}, "photo_personal"),
    ("PXL_20240101_120000123.MP.jpg", {}, "photo_personal"),
    ("20240101_120000.jpg", {}, "photo_personal"),
    ("MVIMG_20240101_120000.jpg", {}, "photo_personal"),
    ("DJI_0001.JPG", {}, "photo_personal"),
    ("GOPR0001.JPG", {}, "photo_personal"),
    ("_MG_1234.JPG", {}, "photo_personal"),
    ("_DSC1234.ARW", {}, "photo_personal"),
    ("irgendwas.nef", {}, "photo_personal"),                  # RAW
    ("Handy/DCIM/Camera/abc.jpg", {}, "photo_personal"),
    ("Oma Geburtstag.jpg", {"exif": CAM}, "photo_personal"),
    ("bearbeitet.png", {"exif": CAM, "dims": (1920, 1080)}, "photo_personal"),
    # --- Persönliche Videos ---
    ("VID_20240101_120000.mp4", {}, "video_personal"),
    ("VID20240101120000.mp4", {}, "video_personal"),
    ("PXL_20240101_120000123.mp4", {}, "video_personal"),
    ("20240101_120000.mp4", {}, "video_personal"),
    ("MOV_1234.mp4", {}, "video_personal"),
    ("IMG_1234.MOV", {}, "video_personal"),
    ("DJI_0002.MP4", {}, "video_personal"),
    ("GX010001.MP4", {}, "video_personal"),
    ("MVI_1234.MOV", {}, "video_personal"),
    ("C0001.MP4", {}, "video_personal"),
    ("PRIVATE/AVCHD/BDMV/STREAM/00001.MTS", {}, "video_personal"),
    ("DCIM/100APPLE/clip.mov", {}, "video_personal"),
    ("Hochzeit.mp4", {"exif": {"make": "Sony"}}, "video_personal"),
    # --- Sonstige ---
    ("Urlaub Italien 2023.jpg", {}, "image"),
    ("logo.png", {"dims": (512, 512)}, "image"),
    ("logo2.png", {}, "image"),                               # ohne Größe
    ("IMG_1234.PNG", {"dims": (800, 600)}, "image"),          # gespeichertes Bild
    ("IMG_20240101_120000.png", {}, "image"),
    ("Capture the flag.png", {}, "image"),
    ("Unbenannt-1.psd", {}, "image"),
    ("hintergrund.webp", {"size": 900_000}, "image"),
    ("vector.svg", {}, "image"),
    ("Film.mkv", {}, "video"),
    ("Tutorial.mp4", {}, "video"),
    ("scan.jpg", {"exif": {"make": "Canon", "model": "CanoScan LiDE 300"}}, "image"),
    ("DCIM/anim.gif", {}, "image"),
    ("Bewerbungsfoto.jpg", {}, "image"),
]

RESULTS = {"ok": 0, "fail": 0}


def check(name, cond, detail=""):
    if cond:
        RESULTS["ok"] += 1
    else:
        RESULTS["fail"] += 1
        print("FEHLER:", name, detail)


# ---------------------------------------------------------------------------
def test_name_cases():
    for rel, kw, want in NAME_CASES:
        f = cls(rel, **kw)
        check("name " + rel, f.category == want,
              "→ %s (%s) erwartet %s" % (f.category, f.reasons, want))
    check("≥60 Namensfälle", len(NAME_CASES) >= 60)


def test_tiers_reasons_year():
    f = cls("IMG_1234.JPG", exif=CAM)
    check("tier personal", f.tier == ORG_PROTECT)
    check("year exif", f.year == 2023)
    check("reason Kamera", any("Kamera: Apple iPhone 13" in r for r in f.reasons), f.reasons)
    f = cls("IMG-20240101-WA0001.jpg")
    check("tier chat", f.tier == ORG_REVIEW)
    check("year none", f.year is None)
    check("reason WhatsApp", any("WhatsApp" in r for r in f.reasons), f.reasons)
    check("tier shot", cls("Screenshot.png").tier == ORG_REVIEW)
    check("tier image", cls("x.jpg").tier == ORG_KEEP)
    check("tier video_personal", cls("VID_20240101_120000.mp4").tier == ORG_PROTECT)
    f = cls("x.png", dims=(1920, 1080))
    check("reason dims", any("1920×1080" in r for r in f.reasons), f.reasons)
    # Kamera-Label ohne doppelten Hersteller
    f = cls("a.jpg", exif={"make": "NIKON CORPORATION", "model": "NIKON D750"})
    check("label nikon", "Kamera: NIKON D750" in f.reasons, f.reasons)
    # ungültiges EXIF-Datum
    f = cls("a.jpg", exif={"make": "Canon", "datetime": "0000:00:00 00:00:00"})
    check("year invalid", f.year is None)
    f = cls("a.jpg", exif={"make": "Canon", "datetime": "2019:05:06 10:11:12"})
    check("year colon format", f.year == 2019)


def test_skips_and_robustness():
    a = mk("IMG_1234.JPG", in_project=True)
    b = mk("IMG_1235.JPG", tier=ORG_JUNK, category="duplicate")
    c = mk("Personalausweis.jpg", category="id", tier=ORG_PROTECT)
    d = mk("Brief.pdf")
    e = mk("IMG_1236.JPG")
    n = org_classify_media([a, b, c, d, e])
    check("skip count", n == 1, n)
    check("skip project", a.category == "")
    check("skip junk", b.category == "duplicate")
    check("skip id", c.category == "id")
    check("skip doc", d.category == "")
    # kaputte Metadaten
    bad = [mk("a.png", exif={"make": 5, "gps": "x", "datetime": 7}, dims=("a", None)),
           mk("b.jpg", exif="kaputt", dims=(0, 0)),
           mk("c.png", dims=(1920,)),
           mk("d.webp", size=None),
           mk("ä ö ü 漢字.jpg", exif={"make": b"Can\x00on", "datetime": b"2020-01-01"})]
    bad[1].meta["exif"] = "kaputt"
    n = org_classify_media(bad)
    check("bad meta classified", n == 5, n)
    check("bytes make", bad[4].category == "photo_personal", bad[4].category)
    check("None list", org_classify_media(None) == 0)
    check("junk list", org_classify_media([None, 3, "x"]) == 0)
    # meta None
    g = mk("IMG_9999.JPG")
    g.meta = None
    check("meta None", org_classify_media([g]) == 1 and g.category == "photo_personal")


def test_reader_injection():
    calls = []

    def reader(f):
        calls.append(f.rel)
        return {"exif": {"make": "Google", "model": "Pixel 7"}, "size": (4000, 3000)}

    f = mk("foo.jpg", meta=False)
    g = mk("bar.jpg")                    # hat schon exif → Leser nicht aufrufen
    org_classify_media([f, g], reader=reader)
    check("reader called once", calls == ["foo.jpg"], calls)
    check("reader result", f.category == "photo_personal")

    def bad_reader(f):
        raise OSError("gesperrt")
    h = mk("baz.jpg", meta=False)
    check("reader error", org_classify_media([h], reader=bad_reader) == 1 and h.category == "image")


def test_screen_sizes():
    for w, h in ((1920, 1080), (1366, 768), (1179, 2556), (2556, 1179), (3440, 1440)):
        check("screen %dx%d" % (w, h), (w, h) in ORG_SCREEN_SIZES)
    check("no 512", (512, 512) not in ORG_SCREEN_SIZES)
    # JPEG in Bildschirmgröße ohne Kamera ist KEIN Screenshot (Regel nur für PNG)
    check("jpg screen no", cls("wallpaper.jpg", dims=(1920, 1080)).category == "image")


def test_month_names():
    check("month 1", org_month_name(1) == "Januar")
    check("month 3", org_month_name(3) == "März")
    check("month 12", org_month_name(12) == "Dezember")
    check("month 0", org_month_name(0) == "")
    check("month 13", org_month_name(13) == "")
    check("month str", org_month_name("x") == "")


def test_haversine():
    d = org_haversine_km(HAMBURG, ROME)
    check("hh-rom", 1290 < d < 1330, d)
    check("zero", org_haversine_km(HAMBURG, HAMBURG) == 0)
    d = org_haversine_km((-33.87, 151.21), (-37.81, 144.96))   # Sydney–Melbourne
    check("syd-mel", 700 < d < 730, d)


# ---------------------------------------------------------------------------
def photo(name, dt, gps=None, cat="photo_personal", mtime=None):
    ex = {"make": "Apple", "model": "iPhone"}
    if dt is not None:
        ex["datetime"] = dt.strftime("%Y-%m-%d %H:%M:%S")
    if gps is not None:
        ex["gps"] = gps
    f = mk(name, exif=ex, mtime=mtime)
    f.category = cat
    f.tier = ORG_PROTECT
    return f


def home_base(n=30, start=datetime(2023, 1, 3, 12)):
    """Alltagsfotos zu Hause, verstreut (alle 9 Tage eins) → Monatsordner."""
    return [photo("home%d.jpg" % i, start + timedelta(days=9 * i),
                  (HAMBURG[0] + 0.01 * (i % 5), HAMBURG[1] + 0.01 * (i % 3)))
            for i in range(n)]


def test_events_basic():
    base = home_base()
    trip = [photo("rom%d.jpg" % i, datetime(2023, 11, 5, 9) + timedelta(hours=7 * i),
                  (ROME[0] + 0.001 * i, ROME[1])) for i in range(20)]
    weekend = [photo("we%d.jpg" % i, datetime(2023, 5, 13, 10) + timedelta(hours=3 * i),
                     (LUEBECK[0], LUEBECK[1])) for i in range(8)]
    files = base + trip + weekend
    n = org_photo_events(files)
    check("events count", n == 2, n)
    check("trip name", trip[0].event == "2023-11-05 bis 11-10 – Reise", trip[0].event)
    check("trip all same", len({f.event for f in trip}) == 1)
    check("trip year", all(f.year == 2023 for f in trip))
    check("weekend name", weekend[0].event == "2023-05-13 bis 05-14", weekend[0].event)
    check("base month", base[0].event == "2023-01 Januar", base[0].event)
    check("base march", base[7].event == "2023-03 März", (base[7].event, base[7].reasons))
    check("home not reise", all("Reise" not in f.event for f in base + weekend))


def test_events_explicit_home():
    # Mit home = Rom ist Rom keine Reise, aber Hamburg-Wochenende schon
    trip = [photo("rom%d.jpg" % i, datetime(2023, 8, 5, 9) + timedelta(hours=3 * i), ROME)
            for i in range(10)]
    hh = [photo("hh%d.jpg" % i, datetime(2023, 9, 1, 9) + timedelta(hours=1 * i), HAMBURG)
          for i in range(10)]
    n = org_photo_events(trip + hh, home=ROME)
    check("home n", n == 2, n)
    check("home rome", trip[0].event == "2023-08-05 bis 08-06", trip[0].event)
    check("home hh reise", hh[0].event == "2023-09-01 – Reise", hh[0].event)
    # ungültiges home → automatisch
    n = org_photo_events(trip + hh, home=("a", "b"))
    check("bad home n", n == 2, n)


def test_events_single_day_and_year_span():
    day = [photo("d%d.jpg" % i, datetime(2022, 6, 18, 14) + timedelta(minutes=20 * i))
           for i in range(10)]
    ny = [photo("ny%d.jpg" % i, datetime(2023, 12, 30, 10) + timedelta(hours=8 * i))
          for i in range(10)]
    n = org_photo_events(day + ny)
    check("day n", n == 2, n)
    check("day name", day[0].event == "2022-06-18", day[0].event)
    check("year span", ny[0].event == "2023-12-30 bis 2024-01-02", ny[0].event)
    check("year span year", all(f.year == 2023 for f in ny))   # ein Ordner, Startjahr


def test_midnight_and_dst():
    # Feier 20:00 bis 02:30 → zählt als ein Tag (Nachtfotos bis 4 Uhr = Vortag)
    party = [photo("p%d.jpg" % i, datetime(2023, 12, 31, 20) + timedelta(minutes=45 * i))
             for i in range(9)]
    org_photo_events(party)
    check("party one day", party[0].event == "2023-12-31", party[0].event)
    # nach 4 Uhr → zwei Tage
    late = [photo("l%d.jpg" % i, datetime(2023, 6, 10, 20) + timedelta(hours=1.5 * i))
            for i in range(7)]   # bis 05:00
    org_photo_events(late)
    check("late two days", late[0].event == "2023-06-10 bis 06-11", late[0].event)
    # Nur nach Mitternacht → eigener Tag
    am = [photo("a%d.jpg" % i, datetime(2023, 6, 20, 0, 30) + timedelta(minutes=10 * i))
          for i in range(7)]
    org_photo_events(am)
    check("after midnight", am[0].event == "2023-06-20", am[0].event)
    # Sommerzeit-Umstellung (26.03.2023 02:00→03:00): naive Ortszeit, Lücke 35 h + DST
    # bleibt unter 36 h → ein Ereignis
    dst = [photo("s%d.jpg" % i, datetime(2023, 3, 25, 1, 30) + timedelta(minutes=5 * i))
           for i in range(4)]
    dst += [photo("t%d.jpg" % i, datetime(2023, 3, 26, 12, 50) + timedelta(minutes=5 * i))
            for i in range(4)]
    n = org_photo_events(dst)
    check("dst one event", n == 1 and len({f.event for f in dst}) == 1,
          (n, {f.event for f in dst}))
    # Lücke genau 36 h → nicht getrennt; 36 h + 1 s → getrennt
    t0 = datetime(2023, 4, 1, 12)
    g = [photo("g%d.jpg" % i, t0 + timedelta(minutes=i)) for i in range(6)]
    g += [photo("h%d.jpg" % i, t0 + timedelta(minutes=5, hours=36) + timedelta(minutes=i))
          for i in range(6)]
    check("gap exact", org_photo_events(g) == 1)
    g2 = [photo("g%d.jpg" % i, t0 + timedelta(minutes=i)) for i in range(6)]
    g2 += [photo("h%d.jpg" % i, t0 + timedelta(minutes=5, hours=36, seconds=1)
                 + timedelta(minutes=i)) for i in range(6)]
    check("gap exceeded", org_photo_events(g2) == 2)


def test_mtime_and_name_fallback():
    # ohne EXIF-Datum: Dateiname, sonst Änderungsdatum
    t = time.mktime((2021, 9, 4, 15, 0, 0, 0, 0, -1))
    fs = []
    for i in range(7):
        f = photo("x%d.jpg" % i, None, mtime=t + 3600 * i)
        fs.append(f)
    n = org_photo_events(fs)
    check("mtime event", n == 1 and fs[0].event == "2021-09-04", (n, fs[0].event))
    check("mtime year", fs[0].year == 2021)
    f = photo("IMG_20190203_101500.jpg", None, mtime=t)
    check("name dt", org_photo_dt(f) == datetime(2019, 2, 3, 10, 15), org_photo_dt(f))
    f = photo("PXL_20200101_235959123.jpg", None, mtime=t)
    check("pxl dt", org_photo_dt(f) == datetime(2020, 1, 1, 23, 59, 59), org_photo_dt(f))
    org_photo_events([f])
    check("small → month", f.event == "2020-01 Januar", f.event)
    f = photo("x.jpg", None, mtime=float("nan"))
    check("bad mtime", org_photo_events([f]) == 0 and f.event == "")
    f = photo("y.jpg", None, mtime="kaputt")
    check("str mtime", org_photo_events([f]) == 0)


def test_hemispheres():
    # Wohnort Buenos Aires (Süd/West), Reise nach Santiago de Chile, Sydney
    ba = (-34.60, -58.38)
    base = [photo("ba%d.jpg" % i, datetime(2022, 1, 2) + timedelta(days=10 * i),
                  (ba[0] - 0.02 * (i % 4), ba[1] - 0.02 * (i % 3))) for i in range(25)]
    scl = [photo("scl%d.jpg" % i, datetime(2022, 3, 3, 9) + timedelta(hours=5 * i),
                 (-33.45, -70.67)) for i in range(8)]
    near = [photo("near%d.jpg" % i, datetime(2022, 5, 7, 9) + timedelta(hours=2 * i),
                  (-34.90, -58.30)) for i in range(8)]   # ~34 km → keine Reise
    n = org_photo_events(base + scl + near)
    check("hemi n", n == 2, n)
    check("hemi reise", scl[0].event.endswith(" – Reise"), scl[0].event)
    check("hemi near", near[0].event == "2022-05-07", near[0].event)
    # Zelle mit negativen Koordinaten: floor statt int (−34.6 → −35)
    syd = [photo("syd%d.jpg" % i, datetime(2021, 1, 1) + timedelta(days=11 * i),
                 (-33.87, 151.21)) for i in range(10)]
    trip = [photo("akl%d.jpg" % i, datetime(2021, 7, 1) + timedelta(hours=3 * i),
                  (-36.85, 174.76)) for i in range(6)]
    n = org_photo_events(syd + trip)
    check("syd n", n == 1 and trip[0].event == "2021-07-01 – Reise", (n, trip[0].event))


def test_no_gps_no_reise():
    fs = [photo("n%d.jpg" % i, datetime(2020, 7, 1) + timedelta(hours=2 * i))
          for i in range(10)]
    n = org_photo_events(fs)
    check("no gps", n == 1 and "Reise" not in fs[0].event, fs[0].event)


def test_long_cluster_and_filters():
    # Jemand fotografiert täglich ein Jahr lang zu Hause → kein Riesen-Ereignis
    daily = [photo("dl%d.jpg" % i, datetime(2020, 1, 1, 18) + timedelta(days=i), HAMBURG)
             for i in range(120)]
    n = org_photo_events(daily)
    check("daily no event", n == 0, n)
    check("daily months", daily[0].event == "2020-01 Januar" and
          daily[-1].event == "2020-04 April", (daily[0].event, daily[-1].event))
    # Nur persönliche Kategorien; Videos zählen mit
    chat = photo("c.jpg", datetime(2020, 1, 1), cat="photo_chat")
    vid = [photo("v%d.mp4" % i, datetime(2020, 2, 2, 10) + timedelta(minutes=i),
                 cat="video_personal") for i in range(6)]
    proj = photo("p.jpg", datetime(2020, 1, 1))
    proj.in_project = True
    n = org_photo_events([chat, proj] + vid)
    check("filters", n == 1 and chat.event == "" and proj.event == "" and
          vid[0].event == "2020-02-02", (n, chat.event, vid[0].event))
    check("empty", org_photo_events([]) == 0 and org_photo_events(None) == 0)
    check("junk input", org_photo_events([None, 1, "x"]) == 0)
    check("bad params", org_photo_events(vid, min_event="x", gap_hours=None) >= 0)
    # min_event anpassbar
    small = [photo("s%d.jpg" % i, datetime(2020, 3, 3, 10) + timedelta(minutes=i))
             for i in range(3)]
    check("min_event 3", org_photo_events(small, min_event=3) == 1)
    check("min_event 6", org_photo_events(small) == 0 and small[0].event == "2020-03 März")


def test_performance():
    import random
    rnd = random.Random(42)
    names = ["IMG_%04d.JPG", "WhatsApp Image %d.jpeg", "Screenshot_%d.png",
             "DSC%05d.JPG", "Urlaub %d.jpg", "PXL_20230101_%06d.jpg", "VID_20230101_%06d.mp4",
             "logo%d.png"]
    files = []
    t0 = datetime(2015, 1, 1)
    for i in range(10_000):
        name = names[i % len(names)] % i
        exif = {}
        if i % 3 == 0:
            dt = t0 + timedelta(hours=rnd.randint(0, 24 * 365 * 8))
            exif = {"make": "Apple", "model": "iPhone", "datetime": dt.strftime("%Y-%m-%d %H:%M:%S")}
            if i % 2:
                exif["gps"] = (rnd.uniform(-60, 70), rnd.uniform(-170, 170))
        files.append(mk("Bilder/%d/%s" % (i % 50, name), exif=exif,
                        dims=(1920, 1080) if i % 7 == 0 else (4000, 3000),
                        mtime=1.4e9 + rnd.randint(0, 250_000_000)))
    s = time.perf_counter()
    n = org_classify_media(files)
    e = org_photo_events(files)
    dur = time.perf_counter() - s
    check("perf n", n == 10_000, n)
    check("perf events", e >= 0)
    check("perf time", dur < 5.0, dur)
    pers = [f for f in files if f.category in ("photo_personal", "video_personal")]
    check("perf all events set", all(f.event for f in pers))
    print("  Leistung: 10.000 Dateien in %.2f s (%d Ereignisse)" % (dur, e))


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
        except Exception:
            RESULTS["fail"] += 1
            print("AUSNAHME in", t.__name__)
            traceback.print_exc()
    print("org_photos: %d Prüfungen bestanden, %d fehlgeschlagen (%d Testfunktionen, "
          "Python %s)" % (RESULTS["ok"], RESULTS["fail"], len(tests), sys.version.split()[0]))
    return 1 if RESULTS["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
