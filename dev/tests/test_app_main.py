"""End-to-End-Test: Hauptfenster – Unterordner (Programme bleiben liegen), bestehende Boxen
als Gruppen, Papierkorb, Online-Nachschlagen (gemockt) und Funktionsleiste.

    QT_QPA_PLATFORM=offscreen python dev/tests/test_app_main.py [ARBEITSORDNER] [APP.pyw]
"""
import argparse
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
N = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="ds_main_test_"))
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

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

app = QApplication([])
f = QFont()
f.setPixelSize(13)
app.setFont(f)
app.setStyleSheet(g.STYLE)
RESULTS = []


def ok(cond, text):
    RESULTS.append(bool(cond))
    print(("  OK     " if cond else "  FEHLER ") + text)


def touch(p: Path, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def wait(cond, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def idle():
    """Scan fertig UND Ergebnis zugestellt (das done-Signal kommt erst nach Thread-Ende)."""
    return not getattr(win, "_busy", False) and not (win._worker and win._worker.isRunning())


# ---------- Testdaten ----------
D, S = N / "Desktop", N / "Boxen"
for d in (D, S, N / "journal"):
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
g.JOURNAL_DIR = N / "journal"
g.ONLINE_CACHE = N / "journal" / "online_cache.json"
g.ONLINE_PAUSE = 0
(N / "mem.json").unlink(missing_ok=True)

touch(D / "Diablo III" / ".build.info")                      # Blizzard-Spiel
touch(D / "Diablo III" / "Diablo III Launcher.exe")
touch(D / "Arduino IDE" / "arduino.exe")                     # installiertes Programm
touch(D / "Arduino IDE" / "lib" / "core.jar")
touch(D / "Arduino Sketche" / "Blink" / "Blink.ino")         # Projektordner
touch(D / "Visual Studio" / "Projects" / "Rechner" / "Rechner.sln")
touch(D / "Visual Studio" / "Projects" / "Rechner" / "main.cpp")
touch(D / "Python Tool" / ".git" / "HEAD")                   # Git-Repo
touch(D / "Python Tool" / "tool.py")
for i in range(5):
    touch(D / "Urlaub Kroatien" / f"IMG_{i:04}.jpg")
    touch(D / "Hochzeit Fotos" / f"DSC_{i:04}.jpg")
touch(D / "Bewerbung 2024" / "Anschreiben.docx")
touch(D / "Krimskrams" / "a.txt")
touch(D / "Krimskrams" / "b.jpg")
touch(D / "Krimskrams" / "c.mp3")
touch(D / "Stardew Valley" / "Saves" / "Farm_123" / "Farm_123")   # Spielstände, kein Programm
touch(D / "Factorio" / "bin" / "x64" / "deep" / "factorio.exe")  # exe tiefer als 2 Ebenen
touch(D / "Factorio" / "data" / "base" / "info.json")
(D / "Hollow Knight.url").write_text("[InternetShortcut]\nURL=steam://rungameid/367520\n")
touch(D / "Discord.lnk")
touch(D / "Rechnung_2024_03.pdf")
touch(D / "Notizen.txt")
touch(D / "Müll.txt")
touch(D / "Lebenslauf Felix.pdf")
# Bestehende Boxen: „Spiele“ mit Inhalt, „Kommunikation“ mit Inhalt, „Werkzeuge“ leer
touch(S / "Spiele" / "Elden Ring.lnk")
touch(S / "Kommunikation" / "Teams.lnk")
(S / "Werkzeuge").mkdir()

# ---------- Engine: Unterordner ----------
print("Unterordner erkennen:")
roles = {n: g.folder_role(D / n)[0] for n in
         ("Diablo III", "Arduino IDE", "Arduino Sketche", "Visual Studio", "Python Tool",
          "Urlaub Kroatien", "Krimskrams", "Stardew Valley", "Factorio")}
ok(roles["Diablo III"] == "program", f"Diablo III = Programm ({roles['Diablo III']})")
ok(roles["Arduino IDE"] == "program", f"Arduino IDE = Programm ({roles['Arduino IDE']})")
ok(roles["Arduino Sketche"] == "project", f"Arduino-Sketche = Projekt ({roles['Arduino Sketche']})")
ok(roles["Visual Studio"] == "project", f"Visual-Studio-Projekte = Projekt ({roles['Visual Studio']})")
ok(roles["Python Tool"] == "project", f"Git-Repo = Projekt ({roles['Python Tool']})")
ok(roles["Urlaub Kroatien"] == "content", "Fotoordner = Inhalt")
ok(roles["Factorio"] == "content", "Factorio (exe sehr tief) zunächst Inhalt – Online-Prüfung klärt")
inst = {os.path.normcase(os.path.normpath(str(D / "Krimskrams")))}
ok(g.folder_role(D / "Krimskrams", inst)[0] == "program", "Eintrag in der Registry (InstallLocation) = Programm")
ok(g.online_clean_name("arduino-1.8.19-windows") == "arduino", "Name bereinigen: arduino-1.8.19-windows")
ok(g.online_clean_name("Diablo III (x86)") == "Diablo III", "Name bereinigen: Diablo III (x86)")
ok(g.online_clean_name("ZS_7805 052 430") == "", "Kryptische Namen werden nicht gesendet")

# ---------- Online-Mock ----------
ONLINE = {
    "stardew valley": ("Stardew Valley", "Videospiel von ConcernedApe (2016)"),
    "factorio": ("Factorio", "Computerspiel von Wube Software"),
    "diablo iii": ("Diablo III", "Computerspiel von Blizzard Entertainment"),
    "arduino": ("Arduino", "Hard- und Softwareplattform für Mikrocontroller"),
}
QUERIES = []
MODE = {"m": "ok"}


def fake_fetch(url):
    q = parse_qs(urlparse(url).query)
    term = (q.get("search") or q.get("srsearch"))[0]
    QUERIES.append(term)
    if MODE["m"] == "limit":
        raise HTTPError(url, 429, "Too Many Requests", {}, None)
    if MODE["m"] == "offline":
        raise URLError("offline")
    hit = ONLINE.get(term.lower())
    if "wikidata" in url:
        return {"search": [{"label": hit[0], "description": hit[1]}] if hit else []}
    return {"query": {"search": []}}


g._online_fetch = fake_fetch

# ---------- Hauptfenster ----------
print("Hauptfenster:")
fm = g.FenceManager(S)
fm.sync()
args = argparse.Namespace(desktop=D, rules=None, memory=N / "mem.json", model="x", min_group=2,
                          storage=S)
CONFIRMS, MESSAGES = [], []
ANSWERS = []
g.MainWindow.confirm = lambda self, title, text: (CONFIRMS.append((title, text)),
                                                  ANSWERS.pop(0) if ANSWERS else False)[1]
g.MainWindow.message = lambda self, title, text, warn=False: MESSAGES.append((title, text))
win = g.MainWindow(args, fm)
win.resize(1320, 860)
win.show()
ok(wait(lambda: win.items and idle()), "Scan fertig")
ok(win.t_folders.isChecked(), "„Ordner einbeziehen“ ist standardmäßig an")


def item(name):
    return next(it for it in win.items if it.label == name)


def idx(name):
    return win.items.index(item(name))


for n in ("Diablo III", "Arduino IDE"):
    it = item(n)
    ok(it.role == "program" and it.confidence == "keep", f"{n}: bleibt liegen ({it.reason})")
for n in ("Arduino Sketche", "Visual Studio", "Python Tool"):
    it = item(n)
    ok(it.role == "project" and it.confidence == "keep", f"{n}: Projekt bleibt liegen ({it.reason})")
ok(item("Urlaub Kroatien").category == "Bilder & Grafik", f"Urlaub Kroatien → Bilder & Grafik ({item('Urlaub Kroatien').reason})")
ok(item("Bewerbung 2024").category == "Büro & Dokumente", f"Bewerbung 2024 → Büro & Dokumente ({item('Bewerbung 2024').reason})")
ok(item("Krimskrams").confidence == "low", "Krimskrams bleibt unklar")
ok(item("Rechnung_2024_03.pdf").category == "Finanzen", "PDF-Rechnung → Finanzen (Thema vor Dateityp)")
ok(item("Lebenslauf Felix.pdf").category == "Büro & Dokumente"
   and "Lebenslauf" in item("Lebenslauf Felix.pdf").reason, "Lebenslauf über das Dokument-Lexikon erkannt")

groups, left = win.plan()
ok("Spiele" in groups and [i.label for i in groups["Spiele"]] == ["Hollow Knight"],
   "Einzelnes Spiel landet in der bestehenden Box „Spiele“")
ok("Kommunikation" in groups, "Discord landet in der bestehenden Box „Kommunikation“")
ok("Bilder & Grafik" in groups and len(groups["Bilder & Grafik"]) == 2, "Zwei Fotoordner bilden eine Gruppe")
ok(all(i.label not in ("Diablo III", "Arduino IDE") for gr in groups.values() for i in gr),
   "Programmordner sind in keiner Gruppe")

cards = win.grid.cards
names = [c.name for c in cards]
ok("Werkzeuge" in names, "Leere bestehende Box „Werkzeuge“ wird immer als Karte gezeigt")
spiele = next(c for c in cards if c.name == "Spiele")
ok(spiele.box_count == 1 and spiele.count == 1, "Karte „Spiele“: 1 in der Box, +1 neu")
ok(next(c for c in cards if c.name == "Werkzeuge").box_count == 0, "Karte „Werkzeuge“: leer")
ok(cards[-2].new and cards[-1].trash, "Am Ende: „+ Neue Gruppe“ und „Papierkorb“")
ok("Programmordner" in win.desk_sub.text(), f"Desktop-Liste zählt Programmordner ({win.desk_sub.text()})")
tips = {win.desk_list.item(r).text(): win.desk_list.item(r).toolTip() for r in range(win.desk_list.count())}
ok("Bleibt liegen" in tips.get("Diablo III", ""), "Tooltip erklärt, warum Diablo III liegen bleibt")

win.select_group("Spiele")
rows = [(win.detail_list.item(r).text(), win.detail_list.item(r).data(Qt.UserRole),
         bool(win.detail_list.item(r).flags() & Qt.ItemIsSelectable)) for r in range(win.detail_list.count())]
ok(("Hollow Knight", idx("Hollow Knight"), True) in rows, "Detail: neues Objekt verschiebbar")
ok(("Elden Ring", -1, False) in rows, "Detail: Box-Inhalt sichtbar, nicht verschiebbar")
ok("bereits in der Box" in win.detail_title.text(), f"Detail-Titel ({win.detail_title.text()})")
win.select_group("Werkzeuge")
ok(win.selected == "Werkzeuge" and "in der Box" in win.detail_title.text(),
   "Leere Box auswählbar – Ziel für Drag & Drop")
win.select_group("Werkzeuge")

# Programmordner in eine Gruppe ziehen → Rückfrage
ANSWERS[:] = [False]
win.assign([idx("Diablo III")], "Spiele")
win.refresh()
ok(CONFIRMS and "Programmordner" in CONFIRMS[-1][0], "Rückfrage beim Verschieben eines Programmordners")
ok(item("Diablo III").confidence == "keep", "„Nein“ → Diablo III bleibt liegen")
ANSWERS[:] = [True]
win.assign([idx("Notizen.txt")], "Werkzeuge")
QApplication.processEvents()
win.refresh()
ok(item("Notizen.txt").category == "Werkzeuge", "Zuordnung zu leerer Box „Werkzeuge“")
ok(len(CONFIRMS) == 1, "Keine Rückfrage für normale Dateien")
ok("Werkzeuge" in win.plan()[0], "„Werkzeuge“ hat jetzt ein neues Objekt")

# ---------- Papierkorb ----------
print("Papierkorb:")
CONFIRMS.clear()
ANSWERS[:] = [False]
win.trash_items([idx("Diablo III")])
ok(CONFIRMS and "Programm-/Projektordner" in CONFIRMS[-1][1], "Warnung bei Programmordner")
ok((D / "Diablo III").exists(), "„Abbrechen“ → nichts gelöscht")
ANSWERS[:] = [False]
win.trash_items([idx("Lebenslauf Felix.pdf")])
ok("Wirkt wichtig" in CONFIRMS[-1][1] and "Lebenslauf" in CONFIRMS[-1][1], "Warnung bei Lebenslauf")
ok((D / "Lebenslauf Felix.pdf").exists(), "Lebenslauf bleibt")
CONFIRMS.clear()
ANSWERS[:] = [True]
trash_card = win.grid.cards[-1]
trash_card.dropped.emit("", [idx("Müll.txt")])                 # Drop auf die Papierkorb-Karte
ok(wait(lambda: not (D / "Müll.txt").exists(), 5), "Drop auf „Papierkorb“ → Datei im Papierkorb")
ok(CONFIRMS and "Wirkt wichtig" not in CONFIRMS[-1][1], "Normale Datei ohne Warnung")
ok(wait(lambda: win.items and idle()
        and all(it.label != "Müll.txt" for it in win.items)), "Nach dem Löschen neu gescannt")
# Entf-Taste in der Desktop-Liste
touch(D / "Alt.tmp")
win.rescan()
wait(lambda: idle() and any(i.label == "Alt.tmp" for i in win.items))
for r in range(win.desk_list.count()):
    li = win.desk_list.item(r)
    li.setSelected(li.text() == "Alt.tmp")
ANSWERS[:] = [True]
from PySide6.QtGui import QKeyEvent
from PySide6.QtCore import QEvent
QApplication.sendEvent(win.desk_list, QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier))
ok(wait(lambda: not (D / "Alt.tmp").exists(), 5), "Entf-Taste → Papierkorb")
wait(idle)

# ---------- Online nachschlagen ----------
print("Online nachschlagen:")
cands = [i.label for i in g.online_candidates(win.items)]
ok("Stardew Valley" in cands and "Factorio" in cands, f"Kandidaten: unklare Ordner ({cands})")
ok(not any(c.endswith((".pdf", ".txt")) for c in cands), "Dateien werden nie gesendet")
ok(cands.index("Diablo III") > cands.index("Stardew Valley"), "Programmordner nur danach (Info)")
win._set_online_mode("manual")
win.online_lookup()
ok(wait(lambda: win._online and not win._online.is_running() and "Online" in win.subtitle.text(), 15),
   f"Nachschlagen fertig ({win.subtitle.text()})")
sv, fa, di = item("Stardew Valley"), item("Factorio"), item("Diablo III")
ok(sv.category == "Spiele" and sv.confidence == "medium", f"Stardew Valley → Spiele ({sv.reason})")
ok(fa.role == "program" and fa.confidence == "keep", f"Factorio: online Spiel + exe gefunden → bleibt liegen ({fa.reason})")
ok("Online:" in di.reason and di.confidence == "keep", "Diablo III: nur Info, bleibt liegen")
ok(item("Krimskrams").confidence == "low", "Krimskrams: nichts gefunden → bleibt unklar")
ok(all(q.lower() not in ("notizen", "lebenslauf felix") for q in QUERIES), "Keine Dokumentnamen gesendet")
cache = json.loads(g.ONLINE_CACHE.read_text(encoding="utf-8"))
ok("stardew valley" in cache, "Ergebnisse im Cache")
n_q = len(QUERIES)
win.rescan()
wait(idle)
ok(item("Stardew Valley").category == "Spiele", "Ergebnis übersteht Neu-Scan")
res, why = g.online_resolve([item("Stardew Valley")], cache, fake_fetch)
ok(len(QUERIES) == n_q and res, "Zweiter Durchgang aus dem Cache – keine neue Anfrage")
MODE["m"] = "limit"
res, why = g.online_resolve([g.Item(D / "Neu Eins", "user", "folder"),
                             g.Item(D / "Neu Zwei", "user", "folder")], {}, fake_fetch)
ok(why == "limit" and len(QUERIES) == n_q + 1, "HTTP 429 → sofort aufhören")
MODE["m"] = "offline"
t0 = time.time()
res, why = g.online_resolve([g.Item(D / "Neu Eins", "user", "folder"),
                             g.Item(D / "Neu Zwei", "user", "folder")], {}, fake_fetch)
ok(why == "offline" and time.time() - t0 < 2, "Offline → sofort aufhören")
MODE["m"] = "ok"

# ---------- Funktionsleiste ----------
print("Funktionsleiste:")
for b in (win.b_org, win.b_clean, win.b_newbox, win.b_arrange, win.b_vis, win.b_more, win.b_trash,
          win.b_online, win.b_ai, win.b_undo):
    ok(b.isEnabled(), f"„{b.text()}“ aktiv")
win.toggle_boxes()
ok(not fm.visible and "Einblenden" in win.b_vis.text(), "Boxen ausblenden")
win.toggle_boxes()
ok(fm.visible and "Ausblenden" in win.b_vis.text(), "Boxen einblenden")
for show in (win.show_more_menu, win.show_organizer_menu):
    QTimer.singleShot(150, lambda: QApplication.activePopupWidget()
                      and QApplication.activePopupWidget().close())
    show()
ok(True, "Menüs „Mehr“ und „Ordner-Assistent“ öffnen ohne Fehler")
g.ask_box_name = lambda title, text: "Games"
win.select_group("Spiele")
win.rename_box("Spiele")
ok((S / "Games" / "Elden Ring.lnk").exists() and item("Hollow Knight").category == "Games",
   "Box umbenennen: Ordner und Zuordnungen folgen")
ok(win.selected == "Games", "Auswahl folgt der Umbenennung")
g.ask_box_name = lambda title, text: "Neu Leer"
win.new_box()
ok((S / "Neu Leer").is_dir() and any(c.name == "Neu Leer" for c in win.grid.cards),
   "Neue Box erscheint sofort als Karte")

if os.environ.get("DS_SHOT"):
    if win.selected != "Games":
        win.select_group("Games")
    QApplication.processEvents()
    win.grab().save(os.environ["DS_SHOT"])

# ---------- Sortieren & Rückgängig ----------
print("Sortieren:")
ANSWERS[:] = [True]
win.apply()
ok((S / "Games" / "Hollow Knight.url").exists(), "Hollow Knight in Box „Games“")
ok((S / "Kommunikation" / "Discord.lnk").exists(), "Discord in Box „Kommunikation“")
ok((S / "Bilder & Grafik" / "Urlaub Kroatien").is_dir(), "Fotoordner in „Bilder & Grafik“")
ok((S / "Werkzeuge" / "Notizen.txt").exists(), "Notizen in leerer Box „Werkzeuge“")
ok((S / "Games" / "Stardew Valley").is_dir(), "Stardew Valley (online erkannt) in „Games“")
for n in ("Diablo III", "Arduino IDE", "Arduino Sketche", "Visual Studio", "Python Tool", "Factorio"):
    ok((D / n).is_dir(), f"{n} liegt noch auf dem Desktop")
wait(idle)
ANSWERS[:] = [True]
win.undo()
ok((D / "Hollow Knight.url").exists() and (D / "Urlaub Kroatien").is_dir(), "Rückgängig")

win.close()
fails = RESULTS.count(False)
print(f"\n{len(RESULTS) - fails}/{len(RESULTS)} Prüfungen bestanden")
sys.exit(1 if fails else 0)
