"""End-to-End-Test: Ordner-Assistent + vorsortierter Downloads-Cleaner im echten App-Code.

    QT_QPA_PLATFORM=offscreen python dev/tests/test_app_organizer.py [ARBEITSORDNER] [APP.pyw]
"""
import importlib.machinery
import importlib.util
import os
import shutil
import struct
import sys
import tempfile
import time
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
N = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="ds_app_test_"))
N.mkdir(parents=True, exist_ok=True)
APP = (Path(sys.argv[2]) if len(sys.argv) > 2
       else Path(__file__).resolve().parents[2] / "DesktopSorter.pyw")
loader = importlib.machinery.SourceFileLoader("ds", str(APP))
spec = importlib.util.spec_from_loader("ds", loader)
g = importlib.util.module_from_spec(spec)
sys.modules["ds"] = g
loader.exec_module(g)

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QKeyEvent, QPageSize, QPainter, QPdfWriter
from PySide6.QtWidgets import QApplication, QMessageBox

app = QApplication([])
f = QFont()
f.setPixelSize(13)
app.setFont(f)
app.setStyleSheet(g.STYLE)
RESULTS = []


def ok(cond, text):
    RESULTS.append(bool(cond))
    print(("  OK     " if cond else "  FEHLER ") + text)


# ---------- Testdaten: ein typischer chaotischer „Dokumente“-Ordner ----------
ROOT = N / "Dokumente_Chaos"
shutil.rmtree(ROOT, ignore_errors=True)
ROOT.mkdir(parents=True)
NOW = time.time()


def pdf(path: Path, lines, days_ago=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    w = QPdfWriter(str(path))
    w.setPageSize(QPageSize(QPageSize.A4))
    w.setResolution(72)
    p = QPainter(w)
    ff = QFont()
    ff.setPixelSize(14)
    p.setFont(ff)
    for i, line in enumerate(lines):
        p.drawText(QRectF(50, 60 + i * 22, 500, 20), Qt.AlignLeft, line)
    p.end()
    t = NOW - days_ago * 86400
    os.utime(path, (t, t))


def docx(path: Path, text: str, days_ago=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml",
                   '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    t = NOW - days_ago * 86400
    os.utime(path, (t, t))


def jpeg_exif(path: Path, dt: str, make=b"Apple", model=b"iPhone 15", gps=None, days_ago=100):
    """Minimales JPEG mit EXIF (DateTimeOriginal, Make, Model, optional GPS)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = QImage(64, 48, QImage.Format_RGB32)
    img.fill(QColor("#3a7bd5"))
    tmp = path.with_suffix(".tmp.jpg")
    img.save(str(tmp), "JPG")
    raw = tmp.read_bytes()
    tmp.unlink()
    # TIFF (little endian): IFD0 Make, Model, ExifIFD ptr, GPS ptr ; ExifIFD DateTimeOriginal
    def ent(tag, typ, cnt, val):
        return struct.pack("<HHI", tag, typ, cnt) + val
    make_b, model_b, dt_b = make + b"\0", model + b"\0", dt.encode() + b"\0"
    n0 = 4 if gps else 3
    ifd0_off = 8
    ifd0_size = 2 + n0 * 12 + 4
    data_off = ifd0_off + ifd0_size
    make_off = data_off
    model_off = make_off + len(make_b)
    exif_off = model_off + len(model_b)
    exif_size = 2 + 12 + 4
    dt_off = exif_off + exif_size
    gps_off = dt_off + len(dt_b)
    e0 = [ent(0x010F, 2, len(make_b), struct.pack("<I", make_off)),
          ent(0x0110, 2, len(model_b), struct.pack("<I", model_off)),
          ent(0x8769, 4, 1, struct.pack("<I", exif_off))]
    if gps:
        e0.append(ent(0x8825, 4, 1, struct.pack("<I", gps_off)))
    tiff = b"II*\0" + struct.pack("<I", ifd0_off) + struct.pack("<H", n0) + b"".join(e0) + b"\0\0\0\0"
    tiff += make_b + model_b
    tiff += struct.pack("<H", 1) + ent(0x9003, 2, len(dt_b), struct.pack("<I", dt_off)) + b"\0\0\0\0"
    tiff += dt_b
    if gps:
        lat, lon = gps
        rat_off = gps_off + 2 + 4 * 12 + 4
        def rat3(v):
            d = int(abs(v)); m = int((abs(v) - d) * 60); s = int(((abs(v) - d) * 60 - m) * 60 * 100)
            return struct.pack("<IIIIII", d, 1, m, 1, s, 100)
        g_entries = [ent(0x0001, 2, 2, (b"N" if lat >= 0 else b"S") + b"\0\0\0"),
                     ent(0x0002, 5, 3, struct.pack("<I", rat_off)),
                     ent(0x0003, 2, 2, (b"E" if lon >= 0 else b"W") + b"\0\0\0"),
                     ent(0x0004, 5, 3, struct.pack("<I", rat_off + 24))]
        tiff += struct.pack("<H", 4) + b"".join(g_entries) + b"\0\0\0\0" + rat3(lat) + rat3(lon)
    app1 = b"Exif\0\0" + tiff
    seg = b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
    path.write_bytes(raw[:2] + seg + raw[2:])
    t = NOW - days_ago * 86400
    os.utime(path, (t, t))


def touch(path: Path, data: bytes = b"x", days_ago=5):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    t = NOW - days_ago * 86400
    os.utime(path, (t, t))


# Wichtiges
pdf(ROOT / "Lebenslauf_Felix_Kucher_2024.pdf", ["Lebenslauf", "Felix Kucher", "Berufserfahrung", "Ausbildung"], 40)
docx(ROOT / "Lebenslauf_final.docx", "Lebenslauf Felix Kucher Berufserfahrung", 120)
docx(ROOT / "Lebenslauf_final_v2 (1).docx", "Lebenslauf Felix Kucher Berufserfahrung neu", 60)
pdf(ROOT / "Arbeitszeugnis_Firma.pdf", ["Arbeitszeugnis", "Herr Kucher war vom 01.03.2019 bis 31.12.2023",
                                         "stets zu unserer vollsten Zufriedenheit"], 300)
pdf(ROOT / "scan_0012.pdf", ["Zeugnis der allgemeinen Hochschulreife", "Abiturzeugnis", "Gesamtnote 1,7"], 900)
pdf(ROOT / "Steuerbescheid 2022.pdf", ["Finanzamt", "Bescheid für 2022 über Einkommensteuer"], 400)
pdf(ROOT / "Gehaltsabrechnung_03_2024.pdf", ["Entgeltabrechnung März 2024", "Steuer-ID", "Versicherungsnummer"], 200)
pdf(ROOT / "Arbeitsvertrag_2021.pdf", ["Arbeitsvertrag", "zwischen", "und"], 1000)
touch(ROOT / "Personalausweis_Scan.jpg", b"\xff\xd8\xff\xe0" + b"\0" * 2000, 700)
# Alltag
for y, m in ((2022, 3), (2023, 7), (2024, 1)):
    pdf(ROOT / f"Rechnung_Amazon_{y}{m:02d}.pdf", ["Rechnung", "Rechnungsnummer 12345", f"{m:02d}.{y}"],
        (2025 - y) * 300)
pdf(ROOT / "Bedienungsanleitung Waschmaschine.pdf", ["Bedienungsanleitung", "Sicherheitshinweise"], 500)
touch(ROOT / "Rezept Lasagne.txt", "Zutaten: Nudeln, Tomaten, Käse".encode("utf-8"), 50)
# Fotos: Urlaub (weit weg, 7 Fotos an 5 Tagen) + zu Hause verstreut
for i in range(8):
    jpeg_exif(ROOT / f"IMG_{2000 + i}.JPG", f"2023:07:{12 + i // 2:02d} 1{i % 10}:00:00",
              gps=(41.9 + i * 0.01, 12.5), days_ago=450)
for i in range(4):
    jpeg_exif(ROOT / f"IMG_{3000 + i}.JPG", f"2024:0{1 + i}:05 10:00:00", gps=(48.1, 11.6), days_ago=300)
touch(ROOT / "IMG-20240101-WA0001.jpg", b"\xff\xd8" + b"\0" * 500, 280)
shot = QImage(1920, 1080, QImage.Format_RGB32)
shot.fill(QColor("#202020"))
shot.save(str(ROOT / "Screenshot 2024-02-01 101010.png"))
# Müll
touch(ROOT / "~$benslauf_final.docx", b"lock", 3)
touch(ROOT / "leer.txt", b"", 30)
touch(ROOT / "SteamSetup.exe", b"MZ" + b"\0" * 5000, 200)
touch(ROOT / "alte_datei.tmp", b"tmp", 20)
(ROOT / "Neuer Ordner").mkdir()
(ROOT / "Neuer Ordner" / "auch leer").mkdir()
# Duplikate
pdf(ROOT / "Unterlagen" / "Rechnung_Amazon_202307 - Kopie.pdf", ["Rechnung", "Rechnungsnummer 12345", "07.2023"], 10)
shutil.copy2(ROOT / "Rechnung_Amazon_202307.pdf", ROOT / "Unterlagen" / "Rechnung_Amazon_202307 - Kopie.pdf")
shutil.copy2(ROOT / "Arbeitszeugnis_Firma.pdf", ROOT / "Arbeitszeugnis_Firma (1).pdf")
# Projekt (darf nie angefasst werden)
(ROOT / "mein_projekt" / ".git").mkdir(parents=True)
touch(ROOT / "mein_projekt" / "main.py", b"print('hi')", 30)
shutil.copy2(ROOT / "Arbeitsvertrag_2021.pdf", ROOT / "mein_projekt" / "Arbeitsvertrag_2021.pdf")
touch(ROOT / "mein_projekt" / "leer.txt", b"", 30)

before = {p.relative_to(ROOT).as_posix(): p.stat().st_size for p in ROOT.rglob("*") if p.is_file()}

# ---------- App-Umgebung ----------
g.JOURNAL_DIR = N / "org_journal"
shutil.rmtree(g.JOURNAL_DIR, ignore_errors=True)
g.desktop_dirs = lambda: (N / "nodesk", Path("/nonexistent"))
g.org_installed_programs = lambda *a, **k: ["Steam", "Google Chrome", "Python 3.12.1 (64-bit)"]
st = N / "org_boxes"
shutil.rmtree(st, ignore_errors=True)
fm = g.FenceManager(st)
answers = {"q": QMessageBox.Yes}
QMessageBox.question = staticmethod(lambda *a, **k: answers["q"])
QMessageBox.information = staticmethod(lambda *a, **k: print("   ℹ", (a[2] if len(a) > 2 else "").splitlines()[0][:110]))
QMessageBox.warning = staticmethod(lambda *a, **k: print("   ⚠", (a[2] if len(a) > 2 else "")[:300]))

fm.cfg["research"] = {"on": False}       # Recherche testet test_app_pages.py (simuliert)
win = g.OrganizerView(fm, ROOT)
win.resize(1240, 860)
win.show()


def wait(fn, w=None):
    w = w or win

    def chk():
        if w._worker and w._worker.isRunning():
            return QTimer.singleShot(80, chk)
        app.processEvents()
        fn()
    QTimer.singleShot(150, chk)


def by_name(lst, name):
    return [x for x in lst if x.name == name]


def s1():
    p = win.plan
    ok(p is not None, f"Analyse fertig: {p and p.stats}")
    names = lambda lst: {x.name for x in lst}
    prot = names(p.protect)
    for n in ("Lebenslauf_Felix_Kucher_2024.pdf", "Arbeitszeugnis_Firma.pdf", "scan_0012.pdf",
              "Steuerbescheid 2022.pdf", "Gehaltsabrechnung_03_2024.pdf", "Arbeitsvertrag_2021.pdf"):
        ok(n in prot, f"wichtig erkannt: {n}")
    ok(any(x.category == "photo_personal" for x in p.files if x.name.startswith("IMG_2")),
       "Urlaubsfotos = persönliche Fotos")
    events = {x.event for x in p.files if x.name.startswith("IMG_2")}
    ok(any("Reise" in (e or "") for e in events), f"Urlaubs-Ereignis: {events}")
    junk = names(p.junk)
    for n in ("~$benslauf_final.docx", "leer.txt", "SteamSetup.exe", "alte_datei.tmp",
              "Rechnung_Amazon_202307 - Kopie.pdf", "Arbeitszeugnis_Firma (1).pdf"):
        ok(n in junk, f"Müll/Duplikat erkannt: {n}")
    ok(not any(x.in_project for x in p.junk), "nichts aus dem Projektordner im Müll")
    ok(not any(x.tier == g.ORG_PROTECT and not x.dup_of for x in p.junk), "kein Wichtiges im Müll")
    ok("Arbeitszeugnis_Firma.pdf" not in junk, "Original-Zeugnis bleibt (nur Kopie ist Müll)")
    olds = {x.name for grp in p.versions.values() for x in grp if not x.version_latest}
    ok("Lebenslauf_final.docx" in olds, f"alte Lebenslauf-Version gruppiert: {olds}")
    ok(any("Neuer Ordner" in str(d) for d in p.empty_dirs), f"leere Ordner: {[str(d) for d in p.empty_dirs]}")
    dests = {m.file.name: m.dest_rel for m in p.moves}
    ok(dests.get("Lebenslauf_Felix_Kucher_2024.pdf", "").startswith("Dokumente/Bewerbung"),
       f"Ziel Lebenslauf: {dests.get('Lebenslauf_Felix_Kucher_2024.pdf')}")
    ok("Steuer/2022" in dests.get("Steuerbescheid 2022.pdf", ""), f"Ziel Steuer: {dests.get('Steuerbescheid 2022.pdf')}")
    ok(not any(m.file.in_project for m in p.moves), "keine Verschiebung im Projektordner")
    # UI-Bäume gefüllt
    ok(win.trees["moves"].topLevelItemCount() > 3, f"Neue Ordnung: {win.trees['moves'].topLevelItemCount()} Zielordner")
    ok(win.trees["dups"].topLevelItemCount() >= 2, "Duplikat-Gruppen angezeigt")
    for key in ("moves", "protect", "dups", "versions", "junk", "review", "largest"):
        win.show_section(key)
        app.processEvents()
        win.grab().save(str(N / f"org_{key}.png"))
    win.show_section("moves")
    print("2) Einsortieren")
    win.apply_moves()
    wait(s2)


def s2():
    moved_cv = ROOT / "Dokumente" / "Bewerbung" / "Lebenslauf_Felix_Kucher_2024.pdf"
    ok(moved_cv.exists(), "Lebenslauf liegt jetzt in Dokumente/Bewerbung")
    ok((ROOT / "mein_projekt" / "Arbeitsvertrag_2021.pdf").exists(), "Projektordner unverändert")
    print("3) Duplikate + Müll in den Papierkorb")
    win.show_section("dups")
    win._run_action(secondary=False)
    wait(s3)


def s3():
    ok(not (ROOT / "Arbeitszeugnis_Firma (1).pdf").exists(), "Zeugnis-Kopie im Papierkorb")
    ok(any(ROOT.rglob("Arbeitszeugnis_Firma.pdf")), "Original-Zeugnis noch da")
    win.show_section("junk")
    win._run_action(secondary=False)
    wait(s4)


def s4():
    ok(not (ROOT / "~$benslauf_final.docx").exists(), "Office-Sperrdatei entfernt")
    ok(not (ROOT / "Neuer Ordner").exists(), "leerer Ordner entfernt")
    ok((ROOT / "mein_projekt" / "leer.txt").exists(), "leere Datei im Projekt bleibt")
    print("4) Rückgängig (Einsortieren)")
    win.undo_last()
    wait(s5)


def s5():
    ok((ROOT / "Lebenslauf_Felix_Kucher_2024.pdf").exists(), "Lebenslauf wieder am alten Platz")
    after = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*") if p.is_file()}
    trashed = {"~$benslauf_final.docx", "leer.txt", "SteamSetup.exe", "alte_datei.tmp",
               "Unterlagen/Rechnung_Amazon_202307 - Kopie.pdf", "Arbeitszeugnis_Firma (1).pdf"}
    missing = set(before) - after - trashed
    ok(not missing, f"nach Rückgängig fehlt nichts außer dem Papierkorb-Inhalt: {sorted(missing)}")
    print("5) Tresor-Sicherung")
    vault = N / "Tresor"
    shutil.rmtree(vault, ignore_errors=True)
    files = [x for x in win.plan.protect]
    res = g.org_backup(files, vault)
    ok(res.get("copied", 0) >= 6 and not res.get("failed"), f"Tresor: {res}")
    res2 = g.org_backup(files, vault)
    ok(res2.get("copied", 1) == 0, f"Tresor inkrementell: {res2}")
    print("6) Vorsortierter Cleaner (Downloads)")
    dl = N / "DL"
    shutil.rmtree(dl, ignore_errors=True)
    dl.mkdir()
    touch(dl / "SteamSetup.exe", b"MZ" + b"\0" * 3000, 100)
    pdf(dl / "Lebenslauf_Bewerbung_2025.pdf", ["Lebenslauf", "Berufserfahrung"], 5)
    touch(dl / "foto.jpg", b"\xff\xd8" + b"\0" * 900, 3)
    shutil.copy2(dl / "foto.jpg", dl / "foto (1).jpg")
    touch(dl / "notizen.txt", b"Einkaufsliste", 8)
    cw = g.CleanerView(fm, dl)
    cw.resize(620, 820)
    cw.show()

    def chk():
        if cw._presort and cw._presort.isRunning():
            return QTimer.singleShot(80, chk)
        app.processEvents()
        order = [e["path"].name for e in cw.queue]
        ok(order[0] in ("SteamSetup.exe", "foto (1).jpg", "foto.jpg") and order[-1] == "Lebenslauf_Bewerbung_2025.pdf",
           f"Reihenfolge vorsortiert: {order}")
        ok(cw.b_bulk.isVisible(), f"Bulk-Button: {cw.b_bulk.text()}")
        cw.card.offset = -80
        cw.grab().save(str(N / "cleaner_presort.png"))
        cw.card.offset = 0
        cw.trash_all_suggestions()
        marked = [d["path"].name for d, a in cw.decisions if a == "trash"]
        ok("SteamSetup.exe" in marked and "Lebenslauf_Bewerbung_2025.pdf" not in marked, f"Bulk markiert: {marked}")
        # Schutzabfrage: wichtiges Dokument nach links → Nein → Karte bleibt
        while cw.current() and cw.current()["path"].name != "Lebenslauf_Bewerbung_2025.pdf":
            cw.decide("keep")
        answers["q"] = QMessageBox.No
        cw.card.fly("trash")
        app.processEvents()
        ok(cw.current() and cw.current()["path"].name == "Lebenslauf_Bewerbung_2025.pdf",
           "Schutzabfrage verhindert Löschen des Lebenslaufs")
        answers["q"] = QMessageBox.Yes
        cw.close()
        ok(not (dl / "SteamSetup.exe").exists() and (dl / "Lebenslauf_Bewerbung_2025.pdf").exists(),
           "Papierkorb bei Fertig: Installer weg, Lebenslauf da")
        print("7) Explorer-Rechtsklick: CLI-Parsing")
        ok(g.org_parse_cli_folder(["x.pyw", "--organize", str(ROOT)]) == ROOT, "--organize <Ordner>")
        print(f"\n{sum(RESULTS)}/{len(RESULTS)} Prüfungen bestanden")
        win.close()
        app.quit()
    QTimer.singleShot(200, chk)


wait(s1)
app.exec()
sys.exit(0 if all(RESULTS) else 1)
