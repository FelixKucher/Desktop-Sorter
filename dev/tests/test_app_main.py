"""End-to-End-Test: Hauptfenster – Unterordner (Programme bleiben liegen), bestehende Boxen
als Gruppen, Papierkorb, Recherche in offenen Quellen (alle vier Quellen simuliert) und
Funktionsleiste.

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


def settled():
    """Scan und Recherche fertig, nichts wartet mehr."""
    return idle() and not win.research_running() and not win._apply_pending


# ---------- Testdaten ----------
D, S = N / "Desktop", N / "Boxen"
for d in (D, S, N / "journal"):
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
g.JOURNAL_DIR = N / "journal"
g.RESEARCH_CACHE = N / "journal" / "recherche_cache.json"
g.RESEARCH_SOURCES = {k: (v[0], v[1], 0.0, v[3]) for k, v in g.RESEARCH_SOURCES.items()}
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
touch(D / "Origin Notes.lnk")            # lokal fälschlich „Origin“ → Launcher; Recherche: Notizen
touch(D / "Rechnung_2024_03.pdf")
touch(D / "Notizen.txt")
touch(D / "Müll.txt")
touch(D / "Lebenslauf Felix.pdf")
# Bestehende Boxen im Format früherer Versionen (Inhalt in Box-Ordnern) – werden beim Start
# zurück auf den Desktop gelegt und zugeordnet: „Spiele“, „Kommunikation“ mit Inhalt, „Werkzeuge“ leer
touch(S / "Spiele" / "Elden Ring.lnk")
touch(S / "Kommunikation" / "Teams.lnk")
(S / "Werkzeuge").mkdir()
g.desktop_dirs = lambda: (D, Path("/nonexistent"))

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
ok(roles["Factorio"] == "content", "Factorio (exe sehr tief) zunächst Inhalt – Recherche klärt")
inst = {os.path.normcase(os.path.normpath(str(D / "Krimskrams")))}
ok(g.folder_role(D / "Krimskrams", inst)[0] == "program", "Eintrag in der Registry (InstallLocation) = Programm")
ok(g.online_clean_name("arduino-1.8.19-windows") == "arduino", "Name bereinigen: arduino-1.8.19-windows")
ok(g.online_clean_name("Diablo III (x86)") == "Diablo III", "Name bereinigen: Diablo III (x86)")
ok(g.online_clean_name("ZS_7805 052 430") == "", "Kryptische Namen werden nicht gesendet")
ok(g.research_similar("Arduino IDE v2", "Arduino IDE") and not g.research_similar("Urlaub", "Urlaub Kroatien"),
   "Namensvergleich: „Arduino IDE v2“ ≈ „Arduino IDE“, „Urlaub“ ≉ „Urlaub Kroatien“")

# ---------- Simulierte offene Quellen ----------
WD = {"diablo iii": ("Q1", "Diablo III", "Computerspiel von Blizzard Entertainment"),
      "stardew valley": ("Q2", "Stardew Valley", "Videospiel von ConcernedApe"),
      "factorio": ("Q3", "Factorio", "Computerspiel von Wube Software"),
      "discord": ("Q4", "Discord", "Instant-Messaging-Dienst"),
      "krimskrams": ("Q7", "Krimskrams", ""),                    # → „ist ein(e)“ (P31)
      "origin notes": ("Q9", "Origin Notes", "Notizprogramm"),
      "arduino ide": ("Q10", "Arduino IDE", "integrierte Entwicklungsumgebung")}
P31 = {"Q7": ["Q500"]}
LABELS = {"Q500": "Begriff"}
WIKIPEDIA = {"hollow knight": ("Hollow Knight",
                               "<span>Hollow Knight</span> ist ein Computerspiel von Team Cherry")}
FLATHUB = {"discord": ("Discord", "Talk, play, hang out", "network", ["InstantMessaging"]),
           "stardew valley": ("Stardew Valley", "Farming RPG", "game", []),
           "origin notes": ("Origin Notes", "Take notes", "office", [])}
CHOCO = {"discord": ("discord", "Discord", "Discord is a voice and text chat for gamers",
                     "voip chat gamer gaming")}
QUERIES = []                             # (Quelle, Suchbegriff)
MODE = {}                                # Quelle → "limit" | "offline"
SLOW = {"s": 0.0}


def choco_xml(term):
    hit = CHOCO.get(term.lower())
    entry = "" if not hit else f"""<entry><title type="text">{hit[0]}</title>
      <summary type="text">{hit[2]}</summary><m:properties><d:Title>{hit[1]}</d:Title>
      <d:Tags>{hit[3]}</d:Tags></m:properties></entry>"""
    return ('<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices" '
            'xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">'
            f'{entry}</feed>')


def fake_fetch(url, data=None, raw=False):
    time.sleep(SLOW["s"])
    host = urlparse(url).netloc
    src = ("wikidata" if "wikidata" in host else "wikipedia" if "wikipedia" in host
           else "flathub" if "flathub" in host else "chocolatey")
    q = parse_qs(urlparse(url).query)
    term = (data or {}).get("query") or (q.get("search") or q.get("srsearch") or [""])[0] \
        or (q.get("searchTerm") or [""])[0].strip("'")
    if term:
        QUERIES.append((src, term))
    if MODE.get(src) == "limit":
        raise HTTPError(url, 429, "Too Many Requests", {}, None)
    if MODE.get(src) == "offline":
        raise URLError("offline")
    if src == "wikidata":
        if "wbsearchentities" in url:
            hit = WD.get(term.lower())
            return {"search": [{"id": hit[0], "label": hit[1], "description": hit[2]}] if hit else []}
        if "wbgetclaims" in url:
            ent = q["entity"][0]
            return {"claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": i}}}}
                                       for i in P31.get(ent, [])]}}
        ids = q["ids"][0].split("|")
        return {"entities": {i: {"labels": {"de": {"value": LABELS.get(i, "")}}} for i in ids}}
    if src == "wikipedia":
        hit = WIKIPEDIA.get(term.lower())
        return {"query": {"search": [{"title": hit[0], "snippet": hit[1]}] if hit else []}}
    if src == "flathub":
        hit = FLATHUB.get(term.lower())
        return {"hits": [{"name": hit[0], "summary": hit[1], "main_categories": hit[2],
                          "sub_categories": hit[3], "app_id": "x"}] if hit else []}
    return choco_xml(term)


g._online_fetch = fake_fetch

# ---------- Hauptfenster ----------
print("Hauptfenster:")
fm = g.FenceManager(S)
fm.sync()
box_of = fm.store.box_of
ok((D / "Elden Ring.lnk").exists() and box_of(D / "Elden Ring.lnk") == "Spiele"
   and not (S / "Spiele").exists() and "Werkzeuge" in fm.store.names(),
   "Alte Box-Ordner: Inhalt zurück auf dem Desktop und der Box zugeordnet")
args = argparse.Namespace(desktop=D, rules=None, memory=N / "mem.json", min_group=2, storage=S)
CONFIRMS, MESSAGES = [], []
ANSWERS = []
g.MainWindow.confirm = lambda self, title, text: (CONFIRMS.append((title, text)),
                                                  ANSWERS.pop(0) if ANSWERS else False)[1]
g.MainWindow.message = lambda self, title, text, warn=False: MESSAGES.append((title, text))
win = g.MainWindow(args, fm)
win.resize(1320, 860)
win.show()
ok(wait(lambda: win.items and settled() and win.research_results, 30), "Scan und Recherche fertig")
ok(win.t_folders.isChecked(), "„Ordner einbeziehen“ ist standardmäßig an")
ok(not hasattr(win, "b_ai") and not hasattr(win, "run_ai"), "Claude-Knopf ist entfernt")
ok(g.research_settings(fm.cfg) == (True, list(g.RESEARCH_SOURCES)), "Recherche standardmäßig an, alle Quellen")


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
ok(item("Rechnung_2024_03.pdf").category == "Finanzen", "PDF-Rechnung → Finanzen (Thema vor Dateityp)")
ok(item("Lebenslauf Felix.pdf").category == "Büro & Dokumente"
   and "Lebenslauf" in item("Lebenslauf Felix.pdf").reason, "Lebenslauf über das Dokument-Lexikon erkannt")

# ---------- Recherche ----------
print("Recherche:")
dc, hk, sv = item("Discord"), item("Hollow Knight"), item("Stardew Valley")
fa, di, kk, on_ = item("Factorio"), item("Diablo III"), item("Krimskrams"), item("Origin Notes")
ok(dc.check == "confirmed" and "✓ geprüft (Wikidata, Flathub, Chocolatey)" in dc.reason,
   f"Discord: von 3 Quellen bestätigt ({dc.reason})")
ok(hk.check == "confirmed" and "Wikipedia" in hk.reason, f"Hollow Knight: Wikipedia springt ein ({hk.reason})")
ok(sv.category == "Spiele" and sv.confidence == "high" and sv.check == "corrected",
   f"Stardew Valley: unklar → Spiele, 2 Quellen ({sv.reason})")
ok(fa.role == "program" and fa.confidence == "keep",
   f"Factorio: Spiel laut Recherche + exe gefunden → bleibt liegen ({fa.reason})")
ok(di.confidence == "keep" and "Computerspiel" in di.reason, "Diablo III: nur Info, bleibt liegen")
ok(kk.confidence == "low" and "Begriff" in kk.reason, f"Krimskrams: „ist ein“ (P31) abgefragt, bleibt unklar ({kk.reason})")
ok(on_.confidence == "low" and on_.category == "Büro & Dokumente" and on_.check == "conflict",
   f"Origin Notes: Widerspruch → zur Prüfung, Vorschlag Büro ({on_.reason})")
sent = {t.lower() for _s, t in QUERIES}
ok(not sent & {"notizen", "lebenslauf felix", "rechnung 2024 03", "müll", "rechnung_2024_03"},
   "Keine Dokumentnamen gesendet")
ok({"discord", "stardew valley", "diablo iii", "factorio"} <= sent, "Verknüpfungen, Programme und Ordner geprüft")
ok(not any(s == "wikipedia" and t.lower() == "discord" for s, t in QUERIES),
   "Wikipedia nur, wenn Wikidata nichts findet")
label = win.research_label.text()
ok("bestätigt" in label and "1 Widerspruch " in label, f"Statuszeile: {label}")
tips = {win.desk_list.item(r).text(): win.desk_list.item(r).toolTip() for r in range(win.desk_list.count())}
ok("Bitte prüfen" in tips.get("Origin Notes", "") and "Flathub" in tips.get("Origin Notes", ""),
   "Tooltip zeigt Widerspruch und Belege")
cache = json.loads(g.RESEARCH_CACHE.read_text(encoding="utf-8"))
ok("discord" in cache.get("flathub", {}) and "Q500" in cache.get("_labels", {}), "Ergebnisse im Zwischenspeicher")
n_q = len(QUERIES)
win.rescan()
ok(wait(settled, 15), "Neu-Scan fertig")
ok(len(QUERIES) == n_q and item("Stardew Valley").category == "Spiele",
   "Neu-Scan nutzt die bekannten Ergebnisse – keine neue Anfrage")
res, status = g.research_run([g.Item(D / "Stardew Valley", "user", "folder")], cache, fake_fetch)
ok(len(QUERIES) == n_q and res[str(D / "Stardew Valley")]["confirmed"] == 2, "Zweiter Lauf aus dem Zwischenspeicher")
MODE.update(wikidata="limit")
res, status = g.research_run([g.Item(D / "Werkzeug Eins", "user", "folder"),
                              g.Item(D / "Werkzeug Zwei", "user", "folder")], {}, fake_fetch)
ok(status["wikidata"] == "limit" and status["flathub"] == "ok"
   and sum(s == "wikidata" for s, _ in QUERIES[n_q:]) == 1, "HTTP 429 → nur diese Quelle hört auf")
MODE.clear()
MODE.update({k: "offline" for k in g.RESEARCH_SOURCES})
t0 = time.time()
res, status = g.research_run([g.Item(D / f"Werkzeug {i}", "user", "folder") for i in "ABCDEF"],
                             {}, fake_fetch)
ok(set(status.values()) == {"offline"} and time.time() - t0 < 2, "Offline → sofort aufhören")
ok("nicht erreichbar" in g.research_status_text(status), "Offline wird gemeldet")
MODE.clear()
groups, left = win.plan()
ok("Spiele" in groups and {i.label for i in groups["Spiele"]} == {"Hollow Knight", "Stardew Valley"},
   "Spiele-Box: Hollow Knight + Stardew Valley (Recherche)")
ok("Kommunikation" in groups, "Discord landet in der bestehenden Box „Kommunikation“")
ok(all(i.label != "Origin Notes" for gr in groups.values() for i in gr), "Widerspruch wird nicht einsortiert")
win.reset_research(False, list(g.RESEARCH_SOURCES))
ok(wait(settled, 15) and "Recherche aus" in win.research_label.text()
   and item("Origin Notes").category == "Spiele-Launcher & Tools", "Recherche abschaltbar → nur lokal")
win.reset_research(True, ["wikidata", "chocolatey"])
ok(wait(lambda: settled() and win.research_results, 15) and "Flathub" not in win.research_label.toolTip()
   and "Chocolatey" in win.research_label.toolTip()
   and item("Discord").check == "confirmed", "Einzelne Quelle abschaltbar")
win.reset_research(True, list(g.RESEARCH_SOURCES))
ok(wait(lambda: settled() and win.research_results, 15), "Wieder alle Quellen")

# ---------- Boxen & Zuordnung ----------
print("Boxen:")
cards = win.grid.cards
names = [c.name for c in cards]
ok("Werkzeuge" in names, "Leere bestehende Box „Werkzeuge“ wird immer als Karte gezeigt")
spiele = next(c for c in cards if c.name == "Spiele")
ok(spiele.box_count == 1 and spiele.count == 2, "Karte „Spiele“: 1 in der Box, +2 neu")
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

ANSWERS[:] = [False]
win.assign([idx("Diablo III")], "Spiele")
win.refresh()
ok(CONFIRMS and "Programmordner" in CONFIRMS[-1][0], "Rückfrage beim Verschieben eines Programmordners")
ok(item("Diablo III").confidence == "keep", "„Nein“ → Diablo III bleibt liegen")
n_conf = len(CONFIRMS)
win.assign([idx("Notizen.txt")], "Werkzeuge")
QApplication.processEvents()
win.refresh()
ok(item("Notizen.txt").category == "Werkzeuge", "Zuordnung zu leerer Box „Werkzeuge“")
ok(len(CONFIRMS) == n_conf, "Keine Rückfrage für normale Dateien")

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
win.grid.cards[-1].dropped.emit("", [idx("Müll.txt")])        # Drop auf die Papierkorb-Karte
ok(wait(lambda: not (D / "Müll.txt").exists(), 5), "Drop auf „Papierkorb“ → Datei im Papierkorb")
ok(CONFIRMS and "Wirkt wichtig" not in CONFIRMS[-1][1], "Normale Datei ohne Warnung")
ok(wait(lambda: win.items and settled() and all(it.label != "Müll.txt" for it in win.items)),
   "Nach dem Löschen neu gescannt")
touch(D / "Alt.tmp")
win.rescan()
wait(lambda: settled() and any(i.label == "Alt.tmp" for i in win.items))
for r in range(win.desk_list.count()):
    li = win.desk_list.item(r)
    li.setSelected(li.text() == "Alt.tmp")
ANSWERS[:] = [True]
from PySide6.QtGui import QKeyEvent
from PySide6.QtCore import QEvent
QApplication.sendEvent(win.desk_list, QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier))
ok(wait(lambda: not (D / "Alt.tmp").exists(), 5), "Entf-Taste → Papierkorb")
wait(settled)

# ---------- Funktionsleiste ----------
print("Funktionsleiste:")
for b in (win.b_research, win.b_undo, win.b_org, win.b_clean, win.b_trash, win.b_newbox,
          win.b_arrange, win.b_vis, win.b_more):
    ok(b.isEnabled(), f"„{b.text()}“ aktiv")
win.toggle_boxes()
ok(not fm.visible and "Einblenden" in win.b_vis.text(), "Boxen ausblenden")
win.toggle_boxes()
ok(fm.visible and "Ausblenden" in win.b_vis.text(), "Boxen einblenden")
for show in (win.show_research_menu, win.show_more_menu, win.show_organizer_menu):
    QTimer.singleShot(150, lambda: QApplication.activePopupWidget()
                      and QApplication.activePopupWidget().close())
    show()
ok(True, "Menüs „Recherche“, „Mehr“ und „Ordner-Assistent“ öffnen ohne Fehler")
g.ask_box_name = lambda title, text: "Games"
win.select_group("Spiele")
win.rename_box("Spiele")
ok(box_of(D / "Elden Ring.lnk") == "Games" and item("Hollow Knight").category == "Games",
   "Box umbenennen: Inhalt und Zuordnungen folgen")
ok(win.selected == "Games", "Auswahl folgt der Umbenennung")
g.ask_box_name = lambda title, text: "Neu Leer"
win.new_box()
ok("Neu Leer" in fm.store.names() and any(c.name == "Neu Leer" for c in win.grid.cards),
   "Neue Box erscheint sofort als Karte")

if os.environ.get("DS_SHOT"):
    if win.selected != "Games":
        win.select_group("Games")
    QApplication.processEvents()
    win.grab().save(os.environ["DS_SHOT"])

# ---------- Sortieren wartet auf die Recherche ----------
print("Sortieren:")
SLOW["s"] = 0.02
CONFIRMS.clear()
ANSWERS[:] = [True]
win.start_research(force=True)                  # alles neu prüfen (ohne Zwischenspeicher)
win.apply()                                     # … währenddessen sortieren
ok(win._apply_pending and not CONFIRMS, "„In Boxen sortieren“ wartet, bis alles geprüft ist")
ok(wait(lambda: CONFIRMS and settled(), 60), "Danach automatisch sortiert")
SLOW["s"] = 0.0
ok(CONFIRMS and "Geprüft mit Wikidata, Wikipedia, Flathub, Chocolatey" in CONFIRMS[0][1],
   "Bestätigung nennt das Prüfergebnis")
ok(box_of(D / "Hollow Knight.url") == "Games", "Hollow Knight in Box „Games“")
ok(box_of(D / "Stardew Valley") == "Games", "Stardew Valley (recherchiert) in „Games“ (umbenannte Box)")
ok(box_of(D / "Discord.lnk") == "Kommunikation", "Discord in Box „Kommunikation“")
ok(box_of(D / "Urlaub Kroatien") == "Bilder & Grafik", "Fotoordner in „Bilder & Grafik“")
ok(box_of(D / "Notizen.txt") == "Werkzeuge", "Notizen in leerer Box „Werkzeuge“")
ok(all((D / n).exists() for n in ("Hollow Knight.url", "Stardew Valley", "Discord.lnk",
                                   "Urlaub Kroatien", "Notizen.txt")),
   "Alle einsortierten Dateien liegen weiterhin im Desktop-Ordner")
ok(not list(S.glob("*/")) or all(d.name.startswith(".") for d in S.iterdir() if d.is_dir()),
   "Keine Box-Ordner mehr – nichts wird vom Desktop weggeschoben")
ok(box_of(D / "Origin Notes.lnk") is None, "Widerspruch bleibt zur Prüfung auf dem Desktop")
for n in ("Diablo III", "Arduino IDE", "Arduino Sketche", "Visual Studio", "Python Tool", "Factorio"):
    ok(box_of(D / n) is None, f"{n} bleibt frei auf dem Desktop")
ok(wait(lambda: settled() and all(i.label != "Hollow Knight" for i in win.items), 10),
   "Neu-Scan: Einsortiertes gilt nicht mehr als „auf dem Desktop“")
ANSWERS[:] = [True]
win.undo()
ok(box_of(D / "Hollow Knight.url") is None and box_of(D / "Urlaub Kroatien") is None
   and box_of(D / "Elden Ring.lnk") == "Games", "Rückgängig: nur die Sortierung wird zurückgenommen")
wait(settled)

# ---------- Automatisch einsortieren prüft mit ----------
print("Automatisch einsortieren:")
fm.desktops = [D]
fm.desk_known = set(fm._desktop_names())
touch(D / "Origin Notes Kopie.lnk")
touch(D / "Discord PTB.lnk")
WD["discord ptb"] = WD["discord"]
FLATHUB["discord ptb"] = FLATHUB["discord"]
fm._autosort()
ok(wait(lambda: not (fm._worker and fm._worker.isRunning())
        and box_of(D / "Discord PTB.lnk") == "Kommunikation", 15),
   "Neue Verknüpfung: geprüft und in „Kommunikation“ einsortiert")
ok(box_of(D / "Origin Notes Kopie.lnk") is None, "Neue Verknüpfung mit Widerspruch bleibt liegen")

win.close()
fails = RESULTS.count(False)
print(f"\n{len(RESULTS) - fails}/{len(RESULTS)} Prüfungen bestanden")
sys.exit(1 if fails else 0)
