"""End-to-End-Test: Box-Symbole bleiben im Desktop-Ordner. Solange die App läuft, sind sie
dort ausgeblendet (versteckt + System), beim Beenden/Abmelden/nach einem Absturz wieder
sichtbar. Windows-Dateiattribute und Registry werden simuliert.

    QT_QPA_PLATFORM=offscreen python dev/tests/test_app_boxes.py [ARBEITSORDNER] [APP.pyw]
"""
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
N = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="ds_boxes_test_"))
N.mkdir(parents=True, exist_ok=True)
APP = (Path(sys.argv[2]) if len(sys.argv) > 2
       else Path(__file__).resolve().parents[2] / "DesktopSorter.pyw")
loader = importlib.machinery.SourceFileLoader("ds", str(APP))
spec = importlib.util.spec_from_loader("ds", loader)
g = importlib.util.module_from_spec(spec)
sys.modules["ds"] = g
loader.exec_module(g)

from PySide6.QtWidgets import QApplication, QMessageBox

app = QApplication([])
RESULTS = []


def ok(cond, text):
    RESULTS.append(bool(cond))
    print(("  OK     " if cond else "  FEHLER ") + text)


def touch(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


# ---------- Simulierte Windows-Attribute und Registry ----------
NORMAL, READONLY, HIDDEN_SYS = 0x20, 0x1, g.BOX_ATTRS
ATTRS, DENY, RUNONCE = {}, set(), {"value": None}


def get_attrs(p):
    return ATTRS.setdefault(str(p), NORMAL) if os.path.lexists(p) else None


def set_attrs(p, a):
    if str(p) in DENY:
        return False
    ATTRS[str(p)] = a
    return True


def hidden(p) -> bool:
    return (ATTRS.get(str(p), NORMAL) & HIDDEN_SYS) == HIDDEN_SYS


g.BOX_ATTR_SUPPORT = True
g._get_attrs, g._set_attrs = get_attrs, set_attrs
g.shell_changed = lambda paths: None
g._runonce = lambda value: RUNONCE.__setitem__("value", value)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.warning = staticmethod(lambda *a, **k: None)

D, P, S, DL = N / "Desktop", N / "Public", N / "Boxen", N / "Downloads"
for d in (D, P, S, DL, N / "journal"):
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
g.JOURNAL_DIR = N / "journal"
g.desktop_dirs = lambda: (D, P)
steam, discord, projekte = touch(D / "Steam.lnk"), touch(D / "Discord.lnk"), touch(D / "Projekte" / "a.txt").parent
public = touch(P / "Firmen-VPN.lnk")
DENY.add(str(public))                                 # öffentlicher Desktop ohne Adminrechte
ATTRS[str(discord)] = NORMAL | READONLY               # Ursprungsattribute müssen erhalten bleiben

print("Einsortieren:")
fm = g.FenceManager(S)
fm.sync()
store = fm.store
fm.move_to([steam, discord, projekte], "Spiele & Chat")
ok(all(p.exists() for p in (steam, discord, projekte)), "Dateien bleiben im Desktop-Ordner")
ok(all(hidden(p) for p in (steam, discord, projekte)), "… sind dort aber ausgeblendet (versteckt + System)")
ok(store.box_of(steam) == "Spiele & Chat", "Zuordnung gespeichert")
fm.sync()
ok(fm.fences["Spiele & Chat"].count == 3, "Box zeigt die 3 Objekte")
ok(RUNONCE["value"] and "--restore" in RUNONCE["value"], f"Absturz-Sicherung eingetragen: {RUNONCE['value']}")
script = g.restore_script_path()
ok(script.exists() and f'attrib -h -s "{steam}"' in script.read_text(encoding="utf-8"),
   "Notfall-Datei zum Einblenden aktuell")
failed = fm.move_to([public], "Spiele & Chat", quiet=True)
ok(failed and store.box_of(public) is None and not hidden(public),
   "Öffentlicher Desktop ohne Rechte: wird nicht einsortiert (bleibt sichtbar)")
other = touch(DL / "Notiz.txt")
fm.move_to([other], "Spiele & Chat")
ok(not other.exists() and (D / "Notiz.txt").exists() and hidden(D / "Notiz.txt")
   and store.box_of(D / "Notiz.txt") == "Spiele & Chat",
   "Datei aus einem anderen Ordner: kommt auf den Desktop und in die Box")
store.release([D / "Notiz.txt"])

print("Beenden:")
fm.shutdown()
ok(not any(hidden(p) for p in (steam, discord, projekte)), "Beenden: alle Symbole wieder sichtbar")
ok(ATTRS[str(discord)] == NORMAL | READONLY, "Ursprüngliche Attribute (schreibgeschützt) erhalten")
ok(RUNONCE["value"] is None and not script.exists(), "Absturz-Sicherung wieder entfernt")
ok(store.box_of(steam) == "Spiele & Chat", "Zuordnung bleibt für den nächsten Start gespeichert")

print("Neustart:")
fm2 = g.FenceManager(S)
fm2.sync()
ok(all(hidden(p) for p in (steam, discord, projekte)), "Start: Box-Symbole wieder ausgeblendet")
ok(fm2.fences["Spiele & Chat"].count == 3, "Box wieder gefüllt")

print("Absturz:")
ok(g.restore_desktop(S) == 0, "Wiederherstellen ohne laufende App")
ok(not any(hidden(p) for p in (steam, discord, projekte)), "RunOnce/--restore blendet alles ein")
fm3 = g.FenceManager(S)
ok(all(hidden(p) for p in (steam, discord, projekte)), "nächster Start blendet wieder aus")

print("Abmelden/Herunterfahren:")
fm3.session_ending()
ok(not hidden(steam) and fm3._rehide.isActive(), "Abmelden: eingeblendet, Wiederausblenden geplant")
fm3._rehide.timeout.emit()
ok(hidden(steam), "Abmelden abgebrochen → nach einer Minute wieder ausgeblendet")

print("Zurück auf den Desktop / Auflösen / Löschen:")
fm3.to_desktop([str(steam)])
ok(fm3.store.box_of(steam) is None, "„Auf den Desktop legen“: aus der Box genommen")
ok(not hidden(steam), "… und wieder sichtbar")
discord.unlink()
ok(fm3.store.prune() and fm3.store.box_of(discord) is None, "Gelöschtes Objekt wird vergessen")
fm3.store.remove_box("Spiele & Chat")
ok(not hidden(projekte) and "Spiele & Chat" not in fm3.store.names(), "Box auflösen: alles sichtbar")

print("Autostart:")
cmd = g.autostart_command()
ok("--background" in cmd and str(APP.resolve()) in cmd, f"Befehl: {cmd}")
ok(g.autostart_status() in ("off", "ok", "stale", "disabled"), "Status abfragbar")

fails = RESULTS.count(False)
print(f"\n{len(RESULTS) - fails}/{len(RESULTS)} Prüfungen bestanden")
sys.exit(1 if fails else 0)
