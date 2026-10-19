"""End-to-End-Test: Seiten im Hauptfenster – Ordner-Assistent mit Recherche und
„Verschieben nach“, Aufräumen (Durchwischen) und Speicher-Analyse. Keine eigenen Fenster.

    QT_QPA_PLATFORM=offscreen python dev/tests/test_app_pages.py [ARBEITSORDNER] [APP.pyw]
"""
import argparse
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
N = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="ds_pages_test_"))
N.mkdir(parents=True, exist_ok=True)
if os.name != "nt":                      # eigener Papierkorb, nicht der des Benutzers
    (N / "xdg").mkdir(exist_ok=True)
    os.environ["XDG_DATA_HOME"] = str(N / "xdg")
APP = (Path(sys.argv[2]) if len(sys.argv) > 2
       else Path(__file__).resolve().parents[2] / "DesktopSorter.pyw")
loader = importlib.machinery.SourceFileLoader("ds", str(APP))
spec = importlib.util.spec_from_loader("ds", loader)
g = importlib.util.module_from_spec(spec)
sys.modules["ds"] = g
loader.exec_module(g)

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

app = QApplication([])
f = QFont()
f.setPixelSize(13)
app.setFont(f)
app.setStyleSheet(g.STYLE)
RESULTS = []
GB = 1024 ** 3


def ok(cond, text):
    RESULTS.append(bool(cond))
    print(("  OK     " if cond else "  FEHLER ") + text)


def wait(cond, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def touch(p: Path, data=b"x", size=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    if size is None:
        p.write_bytes(data)
    else:
        with open(p, "wb") as fh:
            fh.truncate(int(size))
    return p


# ---------- Umgebung ----------
for d in ("Desktop", "Boxen", "journal", "DL", "Ziel", "drive"):
    shutil.rmtree(N / d, ignore_errors=True)
(N / "Desktop").mkdir()
g.JOURNAL_DIR = N / "journal"
g.RESEARCH_CACHE = N / "journal" / "recherche.json"
g.RESEARCH_SOURCES = {k: (v[0], v[1], 0.0, v[3]) for k, v in g.RESEARCH_SOURCES.items()}
g.org_installed_programs = lambda *a, **k: []
g.desktop_dirs = lambda: (N / "Desktop", Path("/nonexistent"))
for k in ("documents", "pictures", "videos", "music", "downloads", "desktop"):
    (N / "home" / k).mkdir(parents=True, exist_ok=True)
_known = g.org_known_folder
g.org_known_folder = lambda key, *a: N / "home" / g.org_known_folder_key(key)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: print("   ⚠", (a[2] if len(a) > 2 else "")[:200]))

DL = N / "DL"
touch(DL / "SteamSetup.exe", b"MZ" + b"\0" * 4000)
touch(DL / "arduino-ide_2.3.6_Windows_64bit.msi", b"\xd0\xcf" + b"\0" * 4000)
touch(DL / "Interstellar.2014.1080p.BluRay.x264.mkv", b"\x1a\x45" + b"\0" * 4000)
touch(DL / "Factorio_x64_2.0.15.zip", b"PK" + b"\0" * 4000)
touch(DL / "Lebenslauf Felix 2025.pdf", b"%PDF-1.4 Lebenslauf")
touch(DL / "IMG_0001.jpg", b"\xff\xd8" + b"\0" * 900)
touch(DL / "notizen.txt", b"Einkaufsliste")

# ---------- Simulierte offene Quellen ----------
WD = {"interstellar": ("Q1", "Interstellar", "Film von Christopher Nolan aus dem Jahr 2014"),
      "factorio": ("Q2", "Factorio", "Computerspiel von Wube Software"),
      "world of warcraft": ("Q3", "World of Warcraft", "MMORPG von Blizzard Entertainment"),
      "arduino ide": ("Q4", "Arduino IDE", "integrierte Entwicklungsumgebung")}
FLATHUB = {"steam": ("Steam", "Launcher for the Steam software distribution service", "game",
                     ["PackageManager"])}
CHOCO = {"steam": ("steam", "Steam", "Valve's game launcher", "steam games launcher")}
QUERIES = []


def fake_fetch(url, data=None, raw=False):
    host = urlparse(url).netloc
    q = parse_qs(urlparse(url).query)
    term = (data or {}).get("query") or (q.get("search") or q.get("srsearch") or [""])[0] \
        or (q.get("searchTerm") or [""])[0].strip("'")
    if term:
        QUERIES.append(term.lower())
    if "wikidata" in host:
        if "wbsearchentities" in url:
            hit = WD.get(term.lower())
            return {"search": [{"id": hit[0], "label": hit[1], "description": hit[2]}] if hit else []}
        return {"claims": {}} if "wbgetclaims" in url else {"entities": {}}
    if "wikipedia" in host:
        return {"query": {"search": []}}
    if "flathub" in host:
        hit = FLATHUB.get(term.lower())
        return {"hits": [{"name": hit[0], "summary": hit[1], "main_categories": hit[2],
                          "sub_categories": hit[3], "app_id": "x"}] if hit else []}
    hit = CHOCO.get(term.lower())
    entry = "" if not hit else (f'<entry><title type="text">{hit[0]}</title><summary type="text">'
                                f'{hit[2]}</summary><m:properties><d:Title>{hit[1]}</d:Title>'
                                f'<d:Tags>{hit[3]}</d:Tags></m:properties></entry>')
    return ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices" '
            'xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">'
            f'{entry}</feed>')


g._online_fetch = fake_fetch

# ---------- Synthetisches Laufwerk für die Speicher-Seite ----------
R = N / "drive"
touch(R / "Windows/System32/big.dll", size=20 * GB)
touch(R / "pagefile.sys", size=16 * GB)
touch(R / "Program Files (x86)/World of Warcraft/Wow.exe", size=50e6)
touch(R / "Program Files (x86)/World of Warcraft/Data/data.001", size=200 * GB)
touch(R / "Program Files (x86)/Diablo IV/Diablo IV Launcher.exe", size=5e6)
touch(R / "Program Files (x86)/Diablo IV/Data/base/d.idx", size=90 * GB)
touch(R / "SteamLibrary/steamapps/common/Cyberpunk 2077/bin/x64/Cyberpunk2077.exe", size=1e8)
touch(R / "SteamLibrary/steamapps/common/Cyberpunk 2077/archive/a.archive", size=70 * GB)
touch(R / "Users/Felix/Downloads/ubuntu-24.04-desktop-amd64.iso", size=6 * GB)
for i in range(4):
    touch(R / f"Users/Felix/Videos/Urlaub 2023/clip{i}.mp4", size=10 * GB)
touch(R / "Users/Felix/AppData/Local/Temp/x.tmp", size=3 * GB)
os.symlink(R / "Program Files (x86)", R / "Users/Felix/Link")         # darf nicht doppelt zählen
DRIVE = {"root": R, "label": "Test-SSD", "total": 1000 * GB, "used": 460 * GB, "free": 540 * GB,
         "removable": False}
g.storage_drives = lambda: [DRIVE]

# ---------- Hauptfenster ----------
fm = g.FenceManager(N / "Boxen")
fm.sync()
args = argparse.Namespace(desktop=N / "Desktop", rules=None, memory=N / "mem.json", min_group=2,
                          storage=N / "Boxen")
win = g.MainWindow(args, fm)
fm.pageRequested.connect(lambda page, folder: win.show_page(page, folder))   # wie run_app
win.resize(1320, 860)
win.show()
wait(lambda: not win.research_running() and not (win._worker and win._worker.isRunning()))


def own_windows():
    return [w for w in QApplication.topLevelWidgets() if w.isVisible()
            and isinstance(w, (g.OrganizerView, g.CleanerView, g.StorageView))]


print("Seiten:")
ok([l for l in win.switch.labels] == [l for _k, l in win.PAGES] and len(win.PAGES) == 4,
   f"Umschalter: {win.switch.labels}")
ok(win.current_page() == "desktop", "Start auf „Desktop“")

# ---------- Ordner-Assistent mit Recherche ----------
print("Ordner + Recherche:")
fm.open_organizer(DL)                                   # Tray/Explorer → Seite statt Fenster
ok(win.current_page() == "folders" and win.organizer.folder == DL, "Ordner-Assistent als Seite")
org = win.organizer
ok(wait(lambda: org.plan is not None and org._research is None and org.research, 30),
   "Analyse und Recherche fertig")
ok(not own_windows(), "kein eigenes Fenster geöffnet")
res = {Path(p).name: r for p, r in org.research.items()}
ok(res.get("SteamSetup.exe", {}).get("category") == "Spiele-Launcher & Tools",
   f"SteamSetup.exe → Launcher ({res.get('SteamSetup.exe', {}).get('agree')})")
ok(res.get("Interstellar.2014.1080p.BluRay.x264.mkv", {}).get("category") == "Musik & Video",
   "Interstellar.2014…mkv → Film")
ok(res.get("arduino-ide_2.3.6_Windows_64bit.msi", {}).get("category") == "Entwicklung",
   "arduino-ide_2.3.6…msi → Entwicklung")
ok(res.get("Factorio_x64_2.0.15.zip", {}).get("category") == "Spiele", "Factorio_x64_2.0.15.zip → Spiele")
ok(not any("lebenslauf" in q or "img" in q or "notizen" in q for q in QUERIES),
   f"Keine Lebensläufe/Fotos/Notizen gesendet ({sorted(set(QUERIES))})")
ok(org.trees["research"].topLevelItemCount() >= 3, "Recherche-Kachel mit Gruppen nach Thema")
dests = {m.file.name: m.dest_rel for m in org.plan.moves}
ok(dests.get("SteamSetup.exe") == "Programme & Installer/Spiele-Launcher & Tools",
   f"Ziel nach Recherche: {dests.get('SteamSetup.exe')}")
steam = next(f for f in org.plan.files if f.name == "SteamSetup.exe")
ok(any(r.startswith("🔎 Steam") for r in steam.reasons), f"Grund zeigt Recherche: {steam.reasons}")
org.show_section("research")
org._check_all(False)
for it in org._iter_items(org.trees["research"]):
    f_ = it.data(0, Qt.UserRole)
    if f_ is not None and f_.name == "SteamSetup.exe":
        it.setCheckState(0, Qt.Checked)
org._run_action(secondary=False)                        # nach Thema einsortieren
ok((DL / "Programme & Installer" / "Spiele-Launcher & Tools" / "SteamSetup.exe").exists(),
   "Einsortiert nach Thema")
ok(wait(lambda: org.plan is not None and not (org._worker and org._worker.isRunning())
        and org._research is None, 30), "neu analysiert")
org.undo_last()
ok((DL / "SteamSetup.exe").exists(), "Rückgängig: Installer wieder da")
wait(lambda: org.plan is not None and not (org._worker and org._worker.isRunning())
     and org._research is None, 30)

print("Verschieben nach:")
movie = next(f for f in org.plan.files if f.name.startswith("Interstellar"))
org.move_files([movie], N / "home" / "videos")
ok((N / "home" / "videos" / movie.name).exists() and not (DL / movie.name).exists(),
   "Datei in „Videos“ eingeordnet (außerhalb des Ordners)")
wait(lambda: org.plan is not None and not (org._worker and org._worker.isRunning()), 30)
org.undo_last()
ok((DL / movie.name).exists(), "Rückgängig holt sie zurück")
wait(lambda: org.plan is not None and not (org._worker and org._worker.isRunning())
     and org._research is None, 30)
menu = org._move_menu(None, [movie])
titles = [a.text() for a in menu.actions()]
ok(any("Dokumente" in t for t in titles) and any("Anderer Ordner" in t for t in titles),
   f"Menü „Verschieben nach“: {titles}")
fm.store.add_box("Spiele")
menu = org._move_menu(None, [movie])
ok(any("Desktop-Box" in (a.menu().title() if a.menu() else "") for a in menu.actions()),
   "Desktop-Boxen als Ziel")
g.MainWindow.confirm = lambda self, title, text: True
save = touch(DL / "Savegame.sav", b"save")
win.files_to_box([type("F", (), {"path": save})()], "Spiele")
on_desk = N / "Desktop" / "Savegame.sav"
ok(on_desk.exists() and not save.exists() and fm.store.box_of(on_desk) == "Spiele",
   "In Desktop-Box legen: Datei kommt auf den Desktop und in die Box")
wait(lambda: org.plan is not None and not (org._worker and org._worker.isRunning())
     and org._research is None, 30)

# ---------- Durchwischen: Seite statt Fenster ----------
print("Aufräumen:")
org.show_section("research")
tree = org.trees["research"]
for it in org._iter_items(tree):
    f_ = it.data(0, Qt.UserRole)
    it.setSelected(f_ is not None and f_.name in ("Factorio_x64_2.0.15.zip", "notizen.txt"))
org.swipe_selected()
cw = win.cleaner
ok(win.current_page() == "clean" and cw.fixed is not None and not own_windows(),
   "Durchwischen öffnet die Seite „Aufräumen“")
ok(wait(lambda: any(e.get("research") for e in cw.queue), 20),
   f"Karte zeigt Recherche: {[e.get('research') for e in cw.queue]}")
cw.decide("trash")
win.show_page("folders")                                 # Verlassen → Entscheidungen ausführen
ok(not (DL / "Factorio_x64_2.0.15.zip").exists(), "Beim Verlassen in den Papierkorb")
wait(lambda: org.plan is not None and not (org._worker and org._worker.isRunning()), 30)
win.show_page("clean", DL)
ok(cw.fixed is None and cw.folder == DL and cw.queue, "Aufräumen für einen Ordner")
cw.decide("keep")
cw.finish()
ok(win.current_page() == "clean" and cw.loaded, "„Fertig“ lädt neu, bleibt auf der Seite")

# ---------- Speicher ----------
print("Speicher:")
win.show_page("storage")
sv = win.storage
ok(len(sv.cards) == 1 and sv.cards[0].isChecked(), "Laufwerk als Karte")
ok(wait(lambda: sv._job is None and sv.hogs.rows, 60), "Analyse fertig")
rows = sv.hogs.rows
top = [(r["label"], round(r["size"] / GB), r["kind"]) for r in rows[:6]]
print("    ", top)
ok(rows[0]["label"] == "World of Warcraft" and round(rows[0]["size"] / GB) == 200,
   "Platz 1: World of Warcraft 200 GB")
ok(rows[0]["kind"] == "Spiel" and "MMORPG" in rows[0]["note"], "Recherche: WoW als Spiel (MMORPG)")
ok(rows[1]["label"] == "Diablo IV" and rows[2]["label"] == "Cyberpunk 2077", "Diablo IV, Cyberpunk folgen")
ok(sum(r["size"] for r in rows) <= 460 * GB, "Symlink nicht doppelt gezählt")
labels = [r["label"] for r in rows]
ok("Urlaub 2023" in labels and "Temp" in labels and "Auslagerungsdatei (Windows)" in labels,
   "Ordner, Temporäres und Systemdateien erkannt")
ok(len(rows) <= 20, "höchstens 20 Einträge")
ok("World of Warcraft" in sv.res_title.text() or "größten" in sv.res_title.text(),
   f"Titel: {sv.res_title.text()}")
iso = next(i for i, r in enumerate(rows) if r["label"].endswith(".iso"))
sv.trash_row(iso)
ok(not (R / "Users/Felix/Downloads/ubuntu-24.04-desktop-amd64.iso").exists(), "ISO in den Papierkorb")
ok(not own_windows(), "weiterhin kein eigenes Fenster")
if os.environ.get("DS_SHOT"):                           # Bilder aller Seiten für die Doku
    shot = Path(os.environ["DS_SHOT"])
    win.grab().save(str(shot.with_name(shot.stem + "_speicher.png")))
    win.show_page("folders")
    org.show_section("research")
    app.processEvents()
    win.grab().save(str(shot.with_name(shot.stem + "_ordner.png")))
    win.show_page("clean")
    wait(lambda: cw.queue and cw.current() is not None, 5)
    app.processEvents()
    win.grab().save(str(shot.with_name(shot.stem + "_aufraeumen.png")))

win.show_page("desktop")
ok(win.current_page() == "desktop", "zurück zu „Desktop“")
win.close()
fails = RESULTS.count(False)
print(f"\n{len(RESULTS) - fails}/{len(RESULTS)} Prüfungen bestanden")
sys.exit(1 if fails else 0)
