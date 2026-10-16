"""Baut die Ordner-Assistent-Engine (dev/organizer/org_*.py) und die Assistenten-Oberfläche
(dev/organizer_ui.py) in DesktopSorter.pyw ein.

    python dev/assemble.py              # DesktopSorter.pyw aktualisieren
    python dev/assemble.py --check      # nur prüfen, ob die .pyw zu den Quellen passt

Wiederholbar: vorhandene Abschnitte werden ersetzt. Prüft außerdem, dass kein Name
zwischen App, Engine-Modulen und Oberfläche kollidiert.
"""
import argparse
import ast
import re
import sys
from pathlib import Path

DEV = Path(__file__).resolve().parent
ORG = DEV / "organizer"
UI = DEV / "organizer_ui.py"
APP = DEV.parent / "DesktopSorter.pyw"
ORDER = ["org_core", "org_lexicon", "org_extract", "org_dupes", "org_junk", "org_photos",
         "org_shell", "org_planner"]
ALLOWED_DUPLICATES = {"org_extract_text"}       # bewusst ersetzt (Text-Cache in der UI)

ENGINE_TITLE = "# ORDNER-ASSISTENT – Analyse-Engine (zweisprachig DE/EN)"
RULE = "# " + "=" * 75
GUI_MARKER = RULE + "\n# OBERFLÄCHE\n"
UI_TITLE = "# Ordner-Assistent – Oberfläche"
STYLE_MARKER = '\nSTYLE = """'


def strip_module(name: str, src: str) -> str:
    src = re.sub(r"^from __future__ import annotations\n", "", src, flags=re.M)
    src = src.replace("try:\n    from org_core import ORG_CATEGORIES\nexcept ImportError:"
                      "  # beim Einfügen in die große Datei existiert ORG_CATEGORIES bereits\n"
                      "    pass\n", "")
    src = re.sub(r"^from org_\w+ import \([^)]*\)[^\n]*\n", "", src, flags=re.M | re.S)
    src = re.sub(r"^from org_\w+ import [^\n]*\n", "", src, flags=re.M)
    left = re.findall(r"^\s*(?:from org_\w+ import|import org_\w+)", src, flags=re.M)
    if left:
        raise SystemExit(f"{name}: übrige Engine-Importe: {left}")
    return src.strip("\n") + "\n"


def top_names(src: str):
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield node.name
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    yield t.id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            yield node.target.id


def split_app(app: str) -> tuple[str, str, str]:
    """Liefert (App-Teil vor der Engine, App-Teil zwischen Engine und UI-Abschnitt,
    App-Rest ab STYLE) – ohne bereits eingebaute Engine/UI."""
    assert app.count(GUI_MARKER) == 1, "GUI-Marker fehlt"
    head, tail = app.split(GUI_MARKER, 1)
    if ENGINE_TITLE in head:                      # alte Engine entfernen
        head = head[:head.index(ENGINE_TITLE)]
        head = head[:head.rindex(RULE)]
    head = head.rstrip("\n") + "\n\n\n"
    assert tail.count(STYLE_MARKER) == 1, "STYLE-Marker fehlt"
    mid, rest = tail.split(STYLE_MARKER, 1)
    if UI_TITLE in mid:                           # alte UI entfernen
        start = mid.index(UI_TITLE)
        start = mid.rindex("# ---", 0, start)
        mid = mid[:start]
    mid = mid.rstrip("\n") + "\n\n\n"
    return head, mid, rest


def build(app: str) -> str:
    head, mid, rest = split_app(app)
    engine = [RULE, ENGINE_TITLE, RULE,
              "# Erkennt Wichtiges (Lebenslauf, Zeugnisse, Verträge, Steuer, Fotos …), Duplikate,",
              "# alte Versionen und Müll und schlägt eine Ordnerstruktur vor. Nur Standardbibliothek.",
              "# Quellen: dev/organizer/org_*.py – nicht hier bearbeiten, sondern dort ändern und",
              "# `python dev/assemble.py` ausführen.", ""]
    sections = {"app": head + mid + 'STYLE = """' + rest}
    for m in ORDER:
        src = strip_module(m, (ORG / f"{m}.py").read_text(encoding="utf-8"))
        sections[m] = src
        engine += [f"# ----- {m} " + "-" * (70 - len(m)), src, ""]
    ui = UI.read_text(encoding="utf-8").strip("\n") + "\n"
    sections["ui"] = ui
    owner: dict = {}
    for sec, src in sections.items():
        for n in set(top_names(src)):
            owner.setdefault(n, []).append(sec)
    coll = {n: s for n, s in owner.items() if len(s) > 1 and n not in ALLOWED_DUPLICATES}
    if coll:
        raise SystemExit(f"KOLLISIONEN zwischen Abschnitten: {coll}")
    out = (head + "\n".join(engine) + "\n\n" + GUI_MARKER + mid + ui + "\n\n"
           + 'STYLE = """' + rest)
    ast.parse(out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--app", type=Path, default=APP)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    app = a.app.read_text(encoding="utf-8")
    out = build(app)
    if a.check:
        if out != app:
            print("DesktopSorter.pyw ist NICHT aktuell – bitte `python dev/assemble.py` ausführen.")
            sys.exit(1)
        print("DesktopSorter.pyw passt zu den Quellen.")
        return
    target = a.out or a.app
    target.write_text(out, encoding="utf-8")
    print(f"geschrieben: {target} ({len(out.splitlines())} Zeilen, {len(out) // 1024} KB)")


if __name__ == "__main__":
    main()
