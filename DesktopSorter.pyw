#!/usr/bin/env python3
"""
DesktopSorter.pyw — sortiert Windows-Desktop-Symbole thematisch in Gruppen
(wie die iOS-App-Mediathek), mit Liquid-Glass-Oberfläche. Eine einzige Datei.

Einmalig installieren (PowerShell):
    py -m pip install PySide6 PySideSix-Frameless-Window anthropic

Starten: Doppelklick auf DesktopSorter.pyw   (oder: py DesktopSorter.pyw)

Neben der Datei entstehen automatisch:
    regeln.toml           eigene Themen (z. B. „Hausplanung“) — frei editierbar
    entscheidungen.json   deine Zuordnungen und gelernten Stichwörter
Journal für „Rückgängig“: %USERPROFILE%\\.desktop_sorter

Bedienung
  • Gruppen erscheinen als Glaskarten. Rechts „Auf dem Desktop“: was liegen
    bleibt; orange „?“ = unklar.
  • Objekte per Drag & Drop auf eine Karte, auf „+ Neue Gruppe“ oder zurück
    nach rechts ziehen. Rechtsklick für alle Optionen, Doppelklick = Vorschlag.
  • „Sortieren“ verschiebt erst nach Bestätigung; „Rückgängig“ macht es komplett
    rückgängig. „Mit Claude sortieren“ ordnet Unklares per KI zu (API-Schlüssel).
"""
from __future__ import annotations

# ===========================================================================
# SORTIERLOGIK
# ===========================================================================

import argparse
import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

JOURNAL_DIR = Path.home() / ".desktop_sorter"
DEFAULT_RULES = Path(__file__).with_name("regeln.toml")
DEFAULT_RULES_TEXT = """# Eigene Themen — haben Vorrang vor allen eingebauten Regeln.
# Schlüssel = Ordnername, Werte = Regex-Fragmente (Groß/Klein egal) auf den Anzeigenamen.

[regeln]
"Hausplanung" = ["obergeschoss", "erdgeschoss", "grundriss", "^fbh\\\\b", "loopcad",
                 "roomsketcher", "sweet home", "hauszeug"]
"""
DEFAULT_MEMORY = Path(__file__).with_name("entscheidungen.json")

# --------------------------------------------------------------------------- #
# Taxonomie & Wissensbasis
# --------------------------------------------------------------------------- #
CATEGORIES = [
    "Spiele", "Spiele-Launcher & Tools", "Kommunikation", "Browser & Internet",
    "Büro & Dokumente", "Entwicklung", "3D-Druck & CAD", "Hardware & Peripherie",
    "System & Datenträger", "Bilder & Grafik", "Musik & Video", "Finanzen",
    "Einkaufen", "Archive", "Sonstiges",
]

# Regex auf "<Anzeigename> <Ziel-EXE>" (lowercase). Reihenfolge = Priorität.
KNOWN_APPS: list[tuple[str, str]] = [
    ("Spiele-Launcher & Tools",
     r"^steam\b|epic games|^ea$|ea app|origin|ubisoft connect|uplay|rockstar games|"
     r"battle\.net|gog galaxy|xbox|curseforge|overwolf|raider\.?io|path of building|"
     r"exiled exchange|playnite|heroic|launcher"),
    ("Kommunikation",
     r"discord|teamspeak|thunderbird|outlook|\bteams\b|slack|zoom|skype|signal(?!\s*rgb)|"
     r"whatsapp|telegram|mumble"),
    ("Hardware & Peripherie",
     r"icue|rgb ?fusion|signal ?rgb|openrgb|g ?hub|logitech|razer|steelseries|armoury|"
     r"msi center|nvidia|amd software|deskjet|hp smart|zubehör|drucker|scanner"),
    ("3D-Druck & CAD",
     r"fusion|autocad|freecad|blender|orca ?slicer|prusa|cura|bambu|loopcad|"
     r"roomsketcher|sweet home|sketchup|solidworks|kicad|^printer\.cfg$|klipper"),
    ("Entwicklung",
     r"pycharm|visual studio|vs ?code|\bcode\.exe|eclipse|intellij|github|\bgit\b|"
     r"unity|unreal|virtualbox|vmware|docker|wsl|postman|anaconda|jupyter|"
     r"android studio|notepad\+\+"),
    ("Browser & Internet",
     r"chrome|firefox|brave|\bedge\b|msedge|opera|vivaldi|tor ?browser|vpn|filezilla|"
     r"passwort|password|keepass|bitwarden|1password"),
    ("Büro & Dokumente",
     r"google (docs|sheets|slides|drive)|libreoffice|soffice|winword|excel|powerpnt|"
     r"onenote|acrobat|pdf|notion|obsidian"),
    ("System & Datenträger",
     r"iso to usb|rufus|etcher|diskimager|raspberry pi imager|ventoy|recovery|"
     r"dr\.?fone|ccleaner|7-?zip|winrar|crystaldisk|hwinfo|cpu-?z|regedit|hkey|powertoys"),
    ("Bilder & Grafik",
     r"gimp|photoshop|lightroom|krita|inkscape|paint\.net|affinity|irfanview"),
    ("Musik & Video",
     r"spotify|vlc|\bobs\b|audacity|itunes|netflix|davinci|premiere|handbrake"),
    ("Spiele",
     r"guild wars|path of exile|diablo|pubg|playerunknown|red dead|valheim|"
     r"age of empires|minecraft|battlefield|magic the gathering|warcraft|^play |"
     r"league of legends|counter-?strike|fortnite|cyberpunk|baldur|elden ring|"
     r"call of duty|grand theft auto|\bgta\b"),
]

# Zielpfad / URL deutet auf installiertes Spiel
GAME_TARGET = re.compile(
    r"steamapps|\\epic games\\|\\ea games\\|\\riot games\\|\\gog galaxy\\games|"
    r"ubisoft game launcher\\games|\\rockstar games\\|\\xboxgames\\|\\games\\|"
    r"^steam://rungameid|^com\.epicgames\.launcher://|^uplay://|^battlenet://", re.I)

KEYWORDS: list[tuple[str, str]] = [
    ("Finanzen", r"rechnung|invoice|quittung|beleg|kontoauszug|steuer|elster|lohn|"
                 r"gehalt|bank|depot|paypal|überweisung|versicherung|budget|mahnung"),
    ("Einkaufen", r"bestellung|order|amazon|zalando|ebay|lieferschein|retoure|gutschein"),
]

EXTENSIONS: dict[str, str] = {}
for cat, exts in {
    "Bilder & Grafik": "jpg jpeg png gif heic webp tif tiff bmp svg psd xcf",
    "Musik & Video": "mp3 m4a wav flac ogg mp4 mov mkv avi webm",
    "Büro & Dokumente": "pdf doc docx odt rtf txt md xls xlsx ods csv ppt pptx odp",
    "Archive": "zip rar 7z tar gz iso img",
    "3D-Druck & CAD": "dwg dxf stl 3mf step stp f3d obj gcode",
    "Entwicklung": "py ipynb js ts json yml yaml toml ps1 bat sh",
}.items():
    for e in exts.split():
        EXTENSIONS[e] = cat


# --------------------------------------------------------------------------- #
# Windows-Shell
# --------------------------------------------------------------------------- #
def known_folder(guid: str, fallback: Path) -> Path:
    """SHGetKnownFolderPath — respektiert OneDrive-/GPO-Umleitungen."""
    if os.name != "nt":
        return fallback
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    u = uuid.UUID(guid)
    g = GUID(u.fields[0], u.fields[1], u.fields[2],
             (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
    p = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None,
                                                  ctypes.byref(p)) != 0:
        return fallback
    try:
        return Path(p.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(p)


def desktop_dirs() -> tuple[Path, Path]:
    user = known_folder("B4BFCC3A-DB2C-424C-B029-7FE99A87C641", Path.home() / "Desktop")
    public = known_folder("C4AA340D-F20F-4863-AFEF-F87EF2E6BA25",
                          Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop")
    return user, public


_PS_RESOLVE = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$sh = New-Object -ComObject WScript.Shell
$paths = [Console]::In.ReadToEnd() | ConvertFrom-Json
$out = foreach ($p in $paths) {
  try { $l = $sh.CreateShortcut($p); [pscustomobject]@{p=$p; t=$l.TargetPath; a=$l.Arguments} }
  catch { [pscustomobject]@{p=$p; t=''; a=''} }
}
@($out) | ConvertTo-Json -Compress
"""


def resolve_lnks(paths: list[Path]) -> dict[str, tuple[str, str]]:
    """Alle .lnk in EINEM PowerShell-Aufruf auflösen (COM WScript.Shell)."""
    if not paths or os.name != "nt":
        return {}
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            _PS_RESOLVE], input=json.dumps([str(p) for p in paths]),
                           capture_output=True, text=True, encoding="utf-8", timeout=60,
                           creationflags=0x08000000 if os.name == "nt" else 0)  # NO_WINDOW
        data = json.loads(r.stdout or "[]")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}
    data = data if isinstance(data, list) else [data]
    return {d["p"]: (d.get("t") or "", d.get("a") or "") for d in data}


def read_url_file(path: Path) -> str:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read_string(path.read_text(encoding="utf-8", errors="replace"))
        return cp.get("InternetShortcut", "URL", fallback="")
    except configparser.Error:
        return ""


def is_hidden(p: Path) -> bool:
    try:
        return bool(getattr(p.stat(), "st_file_attributes", 0) & 0x2)  # HIDDEN
    except OSError:
        return True


# --------------------------------------------------------------------------- #
# Datenmodell & Klassifikation
# --------------------------------------------------------------------------- #
@dataclass
class Item:
    path: Path
    source: str                   # "user" | "public"
    kind: str = "file"            # lnk | url | file | folder
    target: str = ""              # Ziel-EXE oder URL
    category: str = "Sonstiges"
    reason: str = "keine Regel"
    confidence: str = "low"

    @property
    def label(self) -> str:       # wie auf dem Desktop angezeigt
        return self.path.stem if self.kind in ("lnk", "url") else self.path.name


def scan(user: Path, public: Path, skip_names: set[str]) -> list[Item]:
    items: list[Item] = []
    for base, src in ((user, "user"), (public, "public")):
        if not base.is_dir():
            continue
        for p in sorted(base.iterdir(), key=lambda q: q.name.lower()):
            if p.name.lower() == "desktop.ini" or p.name in skip_names or is_hidden(p):
                continue
            ext = p.suffix.lower()
            kind = ("folder" if p.is_dir() else
                    "lnk" if ext == ".lnk" else "url" if ext == ".url" else "file")
            it = Item(p, src, kind)
            if kind == "url":
                it.target = read_url_file(p)
            items.append(it)
    lnks = resolve_lnks([i.path for i in items if i.kind == "lnk"])
    for it in items:
        if it.kind == "lnk":
            t, a = lnks.get(str(it.path), ("", ""))
            it.target = f"{t} {a}".strip()
    return items


def load_rules(path: Path | None) -> list[tuple[str, re.Pattern]]:
    if not path or not path.exists():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8")).get("regeln", {})
    return [(cat, re.compile("|".join(p), re.I)) for cat, p in data.items()]


# Gedächtnis aus den Rückfragen: neue Gruppen, Entscheidungen pro Datei
# ("" = auf dem Desktop lassen) und gelernte Stichwort-Regeln.
class Memory:
    def __init__(self, path: Path):
        self.path = path
        d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.groups: list[str] = d.get("gruppen", [])
        self.files: dict[str, str] = d.get("dateien", {})
        self.keywords: dict[str, list[str]] = d.get("regeln", {})

    def rules(self) -> list[tuple[str, re.Pattern]]:
        return [(c, re.compile("|".join(map(re.escape, kws)), re.I))
                for c, kws in self.keywords.items() if kws]

    def save(self) -> None:
        self.path.write_text(json.dumps({"gruppen": self.groups, "dateien": self.files,
                                         "regeln": self.keywords},
                                        indent=2, ensure_ascii=False), encoding="utf-8")


def classify(it: Item, rules, memory: Memory | None = None) -> None:
    def hit(cat, why, conf="high"):
        it.category, it.reason, it.confidence = cat, why, conf

    exe = Path(it.target.split(" -")[0]).name.lower() if it.kind == "lnk" else ""
    hay = f"{it.label} {exe}".strip().lower()

    if memory and it.path.name in memory.files:
        cat = memory.files[it.path.name]
        return hit(cat or "Sonstiges", "deine frühere Entscheidung",
                   "user" if cat else "keep")
    for cat, rx in rules:
        if m := rx.search(it.label):
            return hit(cat, f"eigene Regel '{m.group(0)}'")
    for cat, pat in KNOWN_APPS:
        if m := re.search(pat, hay, re.I):
            return hit(cat, f"bekannte App '{m.group(0).strip()}'")
    if it.target and GAME_TARGET.search(it.target):
        return hit("Spiele", "Ziel liegt in Spielebibliothek", "medium")
    for cat, pat in KEYWORDS:
        if m := re.search(pat, it.label, re.I):
            return hit(cat, f"Stichwort '{m.group(0)}'")
    ext = it.path.suffix.lower().lstrip(".")
    if it.kind == "file" and ext in EXTENSIONS:
        return hit(EXTENSIONS[ext], f"Dateityp .{ext}", "low")
    hit("Sonstiges", "keine Regel", "low")


def _anon(s: str) -> str:
    """Benutzerpfad vor dem API-Versand maskieren."""
    home = str(Path.home())
    return re.sub(re.escape(home), "%USERPROFILE%", s, flags=re.I) if s else s


def classify_with_claude(items: list[Item], all_items: list[Item], model: str) -> None:
    """Semantische Gruppierung. Gesendet werden nur Anzeigename, Typ und Ziel
    (Benutzerpfad maskiert) — keine Dateiinhalte."""
    try:
        import anthropic
    except ModuleNotFoundError:
        sys.exit("--ai benötigt:  pip install anthropic")
    if not items:
        return
    payload = [{"id": i, "name": it.label, "typ": it.kind, "ziel": _anon(it.target),
                "vorschlag": it.category} for i, it in enumerate(items)]
    context = sorted({f"{it.label} → {it.category}" for it in all_items
                      if it not in items and it.kind != "folder"})
    schema = {
        "type": "object",
        "properties": {"zuordnungen": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "integer"},
                           "kategorie": {"type": "string"},
                           "begruendung": {"type": "string"}},
            "required": ["id", "kategorie", "begruendung"],
            "additionalProperties": False}}},
        "required": ["zuordnungen"], "additionalProperties": False,
    }
    prompt = f"""Gruppiere Windows-Desktop-Objekte thematisch, wie die iOS-App-Mediathek.

Standardkategorien (bevorzugt verwenden, exakt so schreiben):
{json.dumps(CATEGORIES, ensure_ascii=False)}

Regeln:
- Thema schlägt Dateityp: eine PDF-Rechnung gehört zu "Finanzen", nicht "Büro & Dokumente".
- Spiele (auch unbekannte Indie-Titel) → "Spiele"; Launcher, Mods, Overlays, Build-Planer
  → "Spiele-Launcher & Tools".
- Du darfst höchstens 3 NEUE Themen anlegen, aber nur wenn ≥3 Objekte klar zu einem
  Projekt/Thema gehören (z. B. Grundrisse + CAD-Dateien + Planungs-Apps → "Hausplanung").
  Neue Themen: kurz, deutsch, ohne Sonderzeichen <>:"/\\|?*.
- "vorschlag" stammt aus einer Heuristik — übernehmen oder verwerfen.
- Unbekanntes ehrlich → "Sonstiges". Begründung: max. 8 Wörter.

Bereits sicher zugeordnet (nur Kontext):
{chr(10).join(context) or "—"}

Zu klassifizieren:
{json.dumps(payload, ensure_ascii=False, indent=1)}"""

    client = anthropic.Anthropic()
    try:
        resp = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        sys.exit("Kein gültiger API-Schlüssel (ANTHROPIC_API_KEY).")
    except anthropic.APIStatusError as e:
        print(f"Claude übersprungen (HTTP {e.status_code}): {e.message}", file=sys.stderr)
        return
    except anthropic.APIConnectionError:
        print("Claude übersprungen: keine Verbindung.", file=sys.stderr)
        return
    if resp.stop_reason != "end_turn":
        print(f"Claude übersprungen (stop_reason={resp.stop_reason}).", file=sys.stderr)
        return
    text = next(b.text for b in resp.content if b.type == "text")
    for z in json.loads(text)["zuordnungen"]:
        if 0 <= z["id"] < len(items):
            cat = re.sub(r'[<>:"/\\|?*]', "", z["kategorie"]).strip() or "Sonstiges"
            it = items[z["id"]]
            it.category, it.reason, it.confidence = cat, f"Claude: {z['begruendung']}", "medium"


# --------------------------------------------------------------------------- #
# Rückfragen
# --------------------------------------------------------------------------- #
INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)$", re.I)
KIND_LABEL = {"lnk": "Verknüpfung", "url": "Internet-Verknüpfung",
              "file": "Datei", "folder": "Ordner"}


def _input(prompt: str) -> str | None:
    try:
        return input(prompt).strip()
    except EOFError:
        return None


def _new_group_name() -> str | None:
    while True:
        raw = _input("   Name der neuen Gruppe: ")
        if not raw:
            return None
        name = INVALID.sub("", raw).strip(" .")
        if name and not RESERVED.match(name):
            return name
        print("   Ungültiger Ordnername, bitte erneut.")


def ask_unclear(items: list[Item], memory: Memory, rule_groups: list[str],
                include_folders: bool) -> None:
    """Fragt bei allem nach, was nur per Dateityp oder gar nicht zugeordnet wurde."""
    unclear = [it for it in items if it.confidence == "low"
               and (it.kind != "folder" or include_folders)]
    if not unclear:
        return
    print(f"\n{len(unclear)} Objekt(e) sind nicht eindeutig zuzuordnen.\n")
    for idx, it in enumerate(unclear, 1):
        groups = list(dict.fromkeys(
            [c for c in CATEGORIES if c != "Sonstiges"] + rule_groups + memory.groups
            + [i.category for i in items if i.category != "Sonstiges"]))
        print(f"── ({idx}/{len(unclear)})  {it.path.name}   [{KIND_LABEL[it.kind]}]")
        if it.category != "Sonstiges":
            print(f"   Vorschlag: {it.category}  ({it.reason})")
        width = max(len(g) for g in groups) + 6
        cols = max(1, 96 // width)
        for r in range(0, len(groups), cols):
            print("   " + "".join(f"{n:>2} {g:<{width - 3}}" for n, g in
                                  enumerate(groups[r:r + cols], r + 1)))
        hint = "[Enter] Vorschlag  " if it.category != "Sonstiges" else ""
        print(f"   {hint}[n] neue Gruppe  [l] auf Desktop lassen  "
              f"[o] öffnen  [a] alle übrigen lassen")

        while True:
            ans = _input("   > ")
            if ans is None or ans.lower() == "a":       # Rest unverändert lassen
                for rest in unclear[idx - 1:]:
                    rest.category, rest.reason, rest.confidence = (
                        "Sonstiges", "übersprungen", "keep")
                return
            if ans.lower() == "o":
                if hasattr(os, "startfile"):
                    os.startfile(it.path)               # öffnet mit Standard-App
                else:
                    print(f"   {it.path}")
                continue
            if ans.lower() == "l":
                choice = ""
            elif ans == "" and it.category != "Sonstiges":
                choice = it.category
            elif ans.lower() == "n":
                choice = _new_group_name()
                if not choice:
                    continue
                if choice not in groups and choice not in memory.groups:
                    memory.groups.append(choice)
            elif ans.isdigit() and 1 <= int(ans) <= len(groups):
                choice = groups[int(ans) - 1]
            else:
                print("   Eingabe nicht erkannt.")
                continue
            break

        memory.files[it.path.name] = choice
        if choice:
            it.category, it.reason, it.confidence = choice, "deine Auswahl", "user"
            kw = _input(f"   Als Regel merken? Stichwort für „{choice}“ "
                        f"(Enter = nur diese Datei): ")
            if kw:
                memory.keywords.setdefault(choice, []).append(kw)
        else:
            it.category, it.reason, it.confidence = "Sonstiges", "bleibt", "keep"
        memory.save()
        print()


# --------------------------------------------------------------------------- #
# Plan, Ausführung, Undo
# --------------------------------------------------------------------------- #
def plan(items: list[Item], min_group: int, include_folders: bool):
    groups: dict[str, list[Item]] = {}
    for it in items:
        if it.kind == "folder" and not include_folders:
            continue
        groups.setdefault(it.category, []).append(it)
    # Gruppen, die du selbst gewählt hast, entstehen auch mit nur einem Objekt
    keep = {c: g for c, g in groups.items() if c != "Sonstiges" and
            (len(g) >= min_group or any(i.confidence == "user" for i in g))}
    left = [it for c, g in groups.items() if c not in keep for it in g]
    return dict(sorted(keep.items(), key=lambda kv: (-len(kv[1]), kv[0]))), left


def show(groups, left) -> None:
    for cat, its in groups.items():
        print(f"\n[{cat}]  ({len(its)})")
        for it in its:
            pub = " (öffentl.)" if it.source == "public" else ""
            print(f"   {it.label + pub:<48.48} ← {it.reason} [{it.confidence}]")
    if left:
        print("\nBleibt auf dem Desktop:")
        for it in left:
            print(f"   {it.label:<48.48} ({it.category})")


def unique_dest(folder: Path, name: str) -> Path:
    dest, stem, suf, n = folder / name, Path(name).stem, Path(name).suffix, 2
    while dest.exists():
        dest, n = folder / f"{stem} ({n}){suf}", n + 1
    return dest


def apply(user_desktop: Path, groups) -> tuple[Path, list[str]]:
    JOURNAL_DIR.mkdir(exist_ok=True)
    journal = JOURNAL_DIR / f"journal-{time.strftime('%Y%m%d-%H%M%S')}.json"
    moves, created, denied = [], [], []
    try:
        for cat, its in groups.items():
            folder = user_desktop / cat
            if not folder.exists():
                folder.mkdir()
                created.append(str(folder))
            for it in its:
                dest = unique_dest(folder, it.path.name)
                try:
                    shutil.move(str(it.path), str(dest))
                    moves.append([str(it.path), str(dest)])
                except PermissionError:
                    denied.append(it.label)
    finally:  # Journal immer schreiben, damit --undo auch nach Abbruch greift
        journal.write_text(json.dumps({"moves": moves, "created": created},
                                      indent=2, ensure_ascii=False), encoding="utf-8")
    return journal, denied


def undo(arg: str) -> None:
    js = [Path(arg)] if arg else sorted(JOURNAL_DIR.glob("journal-*[0-9].json"))
    if not js:
        sys.exit("Kein Journal gefunden.")
    data = json.loads(js[-1].read_text(encoding="utf-8"))
    for src, dest in reversed(data["moves"]):
        if Path(dest).exists() and not Path(src).exists():
            shutil.move(dest, src)
        else:
            print(f"übersprungen: {dest}", file=sys.stderr)
    for f in map(Path, reversed(data["created"])):
        if f.is_dir() and not any(p.name != "desktop.ini" for p in f.iterdir()):
            shutil.rmtree(f)
    js[-1].rename(js[-1].with_suffix(".undone.json"))
    print(f"Rückgängig gemacht: {len(data['moves'])} Objekte.")


# ===========================================================================
# OBERFLÄCHE
# ===========================================================================

import argparse
import contextlib
import io
import os
import sys
from pathlib import Path


def _fatal(msg: str) -> None:
    """Fehler sichtbar machen — auch ohne Konsole (.pyw)."""
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "Desktop Sorter", 0x10)
    else:
        print(msg, file=sys.stderr)
    sys.exit(1)


try:
    import PySide6  # noqa: F401
except ImportError:
    _fatal("PySide6 fehlt. Einmalig in PowerShell ausführen:\n\n"
           "py -m pip install PySide6 PySideSix-Frameless-Window anthropic")

from PySide6.QtCore import (QFileInfo, QMimeData, QPoint, QPointF, QRectF, QSize, Qt,
                            QThread, QTimer, QUrl, Signal)
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QDrag, QFont, QIcon,
                           QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QApplication,
                               QFileIconProvider, QFrame, QGraphicsDropShadowEffect,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListView, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QScrollArea, QVBoxLayout, QWidget)


try:
    from qframelesswindow import AcrylicWindow as _Base, TitleBar
    FRAMELESS = True
except ImportError:          # Fallback: normales Fenster
    _Base, TitleBar, FRAMELESS = QWidget, None, False

ACRYLIC = FRAMELESS and sys.platform == "win32"
ACRYLIC_TINT = "12141C55"    # RRGGBBAA — Alpha klein = mehr Durchsicht
MIME = "application/x-desktop-sorter-ids"

ACCENT = QColor("#0A84FF")
WARN = QColor("#FF9F0A")
TEXT_2 = QColor(255, 255, 255, 165)


# --------------------------------------------------------------------------- #
# Glas-Rendering
# --------------------------------------------------------------------------- #
def paint_glass(p: QPainter, rect, radius: float, *, strength=1.0, tint: QColor | None = None,
                hover=False, active=False, dashed=False) -> None:
    """Liquid-Glass-Fläche: Milchglas-Füllung, Glanzlicht oben links und
    Lichtkante (Rim), die oben-links und unten-rechts aufleuchtet."""
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(rect).adjusted(0.75, 0.75, -0.75, -0.75)
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)

    base = QColor(tint or QColor(255, 255, 255))
    top, bottom = QColor(base), QColor(base)
    top.setAlpha(int((52 if hover else 36) * strength + (40 if active else 0)))
    bottom.setAlpha(int((20 if hover else 10) * strength + (20 if active else 0)))
    fill = QLinearGradient(r.topLeft(), r.bottomLeft())
    fill.setColorAt(0, top)
    fill.setColorAt(1, bottom)
    p.fillPath(path, fill)

    sheen = QRadialGradient(QPointF(r.left() + r.width() * 0.22, r.top()), r.width() * 0.75)
    sheen.setColorAt(0, QColor(255, 255, 255, int(34 * strength)))
    sheen.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillPath(path, sheen)

    rim = QLinearGradient(r.topLeft(), r.bottomRight())
    hi = 230 if active else 170
    rim.setColorAt(0.0, QColor(255, 255, 255, hi))
    rim.setColorAt(0.3, QColor(255, 255, 255, 40))
    rim.setColorAt(0.7, QColor(255, 255, 255, 22))
    rim.setColorAt(1.0, QColor(255, 255, 255, 120))
    pen = QPen(QBrush(rim), 1.3)
    if active:
        pen = QPen(ACCENT.lighter(130), 2)
    if dashed:
        pen.setStyle(Qt.DashLine)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)


def paint_backdrop(p: QPainter, rect, overlay_only: bool) -> None:
    """Farbige „Liquid“-Lichtflecken. Mit Acrylic nur als Hauch über dem Blur,
    sonst als vollständiger Hintergrund."""
    w, h = rect.width(), rect.height()
    if not overlay_only:
        g = QLinearGradient(0, 0, w, h)
        g.setColorAt(0, QColor("#0B1026"))
        g.setColorAt(1, QColor("#1A0F3A"))
        p.fillRect(rect, g)
    else:
        p.fillRect(rect, QColor(8, 10, 18, 70))
    k = 0.35 if overlay_only else 1.0
    for cx, cy, rad, col, a in [(0.12, 0.18, 0.55, "#2F6BFF", 125), (0.88, 0.22, 0.45, "#A24BFF", 110),
                                (0.62, 1.0, 0.55, "#00BFA6", 85), (0.3, 0.85, 0.35, "#FF4FA3", 60)]:
        c = QColor(col)
        c.setAlpha(int(a * k))
        rg = QRadialGradient(QPointF(w * cx, h * cy), max(w, h) * rad)
        rg.setColorAt(0, c)
        c2 = QColor(c)
        c2.setAlpha(0)
        rg.setColorAt(1, c2)
        p.fillRect(rect, rg)


def letter_icon(name: str, size=128) -> QIcon:
    """Fallback-Symbol: farbige Kachel mit Anfangsbuchstabe."""
    hue = sum(map(ord, name)) * 37 % 360
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(4, 4, size - 8, size - 8), size * 0.24, size * 0.24)
    g = QLinearGradient(0, 0, 0, size)
    g.setColorAt(0, QColor.fromHsv(hue, 150, 245))
    g.setColorAt(1, QColor.fromHsv((hue + 25) % 360, 210, 170))
    p.fillPath(path, g)
    hl = QLinearGradient(0, 0, 0, size * 0.5)
    hl.setColorAt(0, QColor(255, 255, 255, 90))
    hl.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillPath(path, hl)
    f = QFont()
    f.setPixelSize(int(size * 0.46))
    f.setBold(True)
    p.setFont(f)
    p.setPen(Qt.white)
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, (name.strip() or "?")[0].upper())
    p.end()
    return QIcon(pm)


def badge_icon(icon: QIcon, size=96) -> QIcon:
    """Orangener „?“-Punkt für unklare Objekte."""
    pm = icon.pixmap(size, size)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.drawPixmap(0, 0, pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    d = size * 0.38
    r = QRectF(size - d - 1, 1, d, d)
    p.setBrush(WARN)
    p.setPen(QPen(QColor(255, 255, 255, 220), 2))
    p.drawEllipse(r)
    f = QFont()
    f.setPixelSize(int(d * 0.7))
    f.setBold(True)
    p.setFont(f)
    p.setPen(Qt.white)
    p.drawText(r, Qt.AlignCenter, "?")
    p.end()
    return QIcon(out)


def app_icon() -> QIcon:
    pm = QPixmap(128, 128)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    paint_glass(p, QRectF(4, 4, 120, 120), 30, strength=2.2, tint=QColor("#3A6BFF"))
    for i, col in enumerate(["#FF9F0A", "#30D158", "#0A84FF", "#BF5AF2"]):
        path = QPainterPath()
        path.addRoundedRect(QRectF(26 + (i % 2) * 42, 26 + (i // 2) * 42, 34, 34), 9, 9)
        p.fillPath(path, QColor(col))
    p.end()
    return QIcon(pm)


def shadow(widget: QWidget, blur=32, dy=10, alpha=90) -> None:
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    eff.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(eff)


# --------------------------------------------------------------------------- #
# Glas-Widgets
# --------------------------------------------------------------------------- #
class GlassPanel(QWidget):
    def __init__(self, radius=22, parent=None):
        super().__init__(parent)
        self.radius = radius

    def paintEvent(self, _):
        p = QPainter(self)
        paint_glass(p, self.rect(), self.radius, strength=0.8)


class GlassButton(QAbstractButton):
    def __init__(self, text: str, primary=False, small=False, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.primary, self.small = primary, small
        self.setCursor(Qt.PointingHandCursor)
        self._hover = False

    def sizeHint(self):
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self.text()) + (24 if self.small else 36),
                     30 if self.small else 38)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setOpacity(1.0 if self.isEnabled() else 0.4)
        r = self.rect()
        tint = ACCENT if self.primary else None
        paint_glass(p, r, r.height() / 2, strength=3.2 if self.primary else 1.2,
                    tint=tint, hover=self._hover or self.isDown())
        f = self.font()
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(Qt.white)
        p.drawText(r, Qt.AlignCenter, self.text())


class Toggle(QAbstractButton):
    """iOS-Schalter mit Beschriftung."""
    def __init__(self, text: str, checked=False, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        return QSize(46 + 8 + self.fontMetrics().horizontalAdvance(self.text()), 28)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(1, 4, 44, 24 - 4)
        path = QPainterPath()
        path.addRoundedRect(track, 10, 10)
        if self.isChecked():
            p.fillPath(path, QColor("#30D158"))
        else:
            paint_glass(p, track, 10, strength=1.4)
        knob_x = track.right() - 18 if self.isChecked() else track.left() + 2
        p.setBrush(Qt.white)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(knob_x, track.top() + 2, 16, 16))
        p.setPen(TEXT_2 if not self.isChecked() else Qt.white)
        p.drawText(QRectF(54, 0, self.width() - 54, self.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, self.text())


def glass_menu(parent=None, title="") -> QMenu:
    m = QMenu(title, parent)
    m.setWindowFlags(m.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    m.setAttribute(Qt.WA_TranslucentBackground)
    return m


# --------------------------------------------------------------------------- #
# Listen & Karten mit Drag & Drop
# --------------------------------------------------------------------------- #
def encode_ids(ids) -> QMimeData:
    md = QMimeData()
    md.setData(MIME, ",".join(map(str, ids)).encode())
    return md


def decode_ids(md: QMimeData) -> list[int]:
    raw = bytes(md.data(MIME)).decode()
    return [int(x) for x in raw.split(",") if x]


class ItemList(QListWidget):
    dropped = Signal(list)
    menuRequested = Signal(list, QPoint)
    activated_ = Signal(int)

    def __init__(self, icon=52, cell=110, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.IconMode)
        self.setMovement(QListView.Static)
        self.setResizeMode(QListView.Adjust)
        self.setWrapping(True)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.ElideRight)
        self.setIconSize(QSize(icon, icon))
        self.setGridSize(QSize(cell, icon + 56))
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemDoubleClicked.connect(lambda it: self.activated_.emit(it.data(Qt.UserRole)))

    def ids(self, items) -> list[int]:
        return [i.data(Qt.UserRole) for i in items]

    # Eigener Drag: verhindert, dass Qt nach dem Drop Zeilen selbst entfernt.
    def startDrag(self, _actions):
        items = self.selectedItems()
        if not items:
            return
        drag = QDrag(self)
        drag.setMimeData(encode_ids(self.ids(items)))
        drag.setPixmap(items[0].icon().pixmap(56, 56))
        drag.setHotSpot(QPoint(28, 28))
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, e):
        ok = e.mimeData().hasFormat(MIME) and e.source() is not self and self.acceptDrops()
        e.acceptProposedAction() if ok else e.ignore()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, e):
        ids = decode_ids(e.mimeData())
        e.acceptProposedAction()
        self.dropped.emit(ids)

    def _menu(self, pos):
        items = self.selectedItems()
        under = self.itemAt(pos)
        if under and under not in items:
            self.clearSelection()
            under.setSelected(True)
            items = [under]
        if items:
            self.menuRequested.emit(self.ids(items), self.viewport().mapToGlobal(pos))


class GroupCard(QWidget):
    """Gruppe im Stil der iOS-App-Mediathek: 3 große Symbole + Mini-Raster."""
    W, H = 188, 214
    clicked = Signal(str)
    dropped = Signal(str, list)
    menuRequested = Signal(str, QPoint)

    def __init__(self, name: str, icons: list[QIcon], count: int, selected=False,
                 new=False, parent=None):
        super().__init__(parent)
        self.name, self.icons, self.count = name, icons, count
        self.selected, self.new = selected, new
        self._hover = self._drag = False
        self.setFixedSize(self.W, self.H)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Objekte hierher ziehen, um eine neue Gruppe anzulegen" if new else
                        f"{name}: {count} Objekt(e)")
        if not new:
            shadow(self, 30, 10, 80)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and not self.new:
            self.clicked.emit(self.name)

    def contextMenuEvent(self, e):
        if not self.new:
            self.menuRequested.emit(self.name, e.globalPos())

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            self._drag = True
            self.update()
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._drag = False
        self.update()

    def dropEvent(self, e):
        self._drag = False
        self.update()
        e.acceptProposedAction()
        self.dropped.emit("" if self.new else self.name, decode_ids(e.mimeData()))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        paint_glass(p, self.rect(), 26, strength=0.6 if self.new else 1.0,
                    tint=ACCENT if self._drag else None,
                    hover=self._hover or self._drag, active=self.selected or self._drag,
                    dashed=self.new and not self._drag)
        if self.new:
            f = self.font()
            f.setPixelSize(44)
            f.setWeight(QFont.Light)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255, 200))
            p.drawText(QRectF(0, 40, self.W, 80), Qt.AlignCenter, "+")
            f.setPixelSize(13)
            f.setWeight(QFont.DemiBold)
            p.setFont(f)
            p.drawText(QRectF(0, 128, self.W, 24), Qt.AlignCenter, "Neue Gruppe")
            f.setWeight(QFont.Normal)
            f.setPixelSize(11)
            p.setFont(f)
            p.setPen(TEXT_2)
            p.drawText(QRectF(0, 150, self.W, 20), Qt.AlignCenter, "hierher ziehen")
            return

        cell, gap, x0, y0 = 70, 12, 18, 18
        cells = [QRectF(x0 + (i % 2) * (cell + gap), y0 + (i // 2) * (cell + gap), cell, cell)
                 for i in range(4)]
        big = self.icons[:3] if len(self.icons) > 4 else self.icons[:4]
        for ic, c in zip(big, cells):
            p.drawPixmap(c.adjusted(5, 5, -5, -5).toRect(), ic.pixmap(120, 120))
        if len(self.icons) > 4:                       # Mini-Raster in Zelle 4
            c = cells[3]
            mini = (cell - 8) / 2
            for j, ic in enumerate(self.icons[3:7]):
                r = QRectF(c.left() + (j % 2) * (mini + 8), c.top() + (j // 2) * (mini + 8),
                           mini, mini)
                p.drawPixmap(r.adjusted(2, 2, -2, -2).toRect(), ic.pixmap(64, 64))

        f = self.font()
        f.setPixelSize(13)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(Qt.white)
        title_r = QRectF(18, self.H - 36, self.W - 36 - 30, 22)
        p.drawText(title_r, Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.name, Qt.ElideRight, int(title_r.width())))
        f.setWeight(QFont.Normal)
        p.setFont(f)
        p.setPen(TEXT_2)
        p.drawText(QRectF(self.W - 48, self.H - 36, 30, 22), Qt.AlignRight | Qt.AlignVCenter,
                   str(self.count))


class CardGrid(QWidget):
    """Fließendes Raster aus Karten; Spaltenzahl folgt der Fensterbreite."""
    PAD, GAP = 14, 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cards: list[GroupCard] = []

    def set_cards(self, cards):
        for c in self.cards:
            c.deleteLater()
        self.cards = cards
        for c in cards:
            c.setParent(self)
            c.show()
        self._layout()

    def resizeEvent(self, e):
        self._layout()

    def _layout(self):
        w = max(self.width(), GroupCard.W + 2 * self.PAD)
        cols = max(1, (w - 2 * self.PAD + self.GAP) // (GroupCard.W + self.GAP))
        used = cols * GroupCard.W + (cols - 1) * self.GAP
        x0 = self.PAD + (w - 2 * self.PAD - used) // 2
        for i, c in enumerate(self.cards):
            c.move(x0 + (i % cols) * (GroupCard.W + self.GAP),
                   self.PAD + (i // cols) * (GroupCard.H + self.GAP))
        rows = (len(self.cards) + cols - 1) // cols
        self.setMinimumHeight(2 * self.PAD + rows * GroupCard.H + max(0, rows - 1) * self.GAP)


# --------------------------------------------------------------------------- #
# Hintergrundarbeit
# --------------------------------------------------------------------------- #
class Worker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except SystemExit as e:              # Kernfunktionen beenden mit sys.exit(msg)
            self.failed.emit(str(e))
        except Exception as e:               # noqa: BLE001 — in der GUI anzeigen
            self.failed.emit(f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------- #
# Hauptfenster
# --------------------------------------------------------------------------- #
class MainWindow(_Base):
    def __init__(self, args):
        super().__init__()
        self.args = args
        self.memory = Memory(args.memory)
        self.toml_rules = load_rules(args.rules)
        self.items: list[Item] = []
        self.icons: dict[str, QIcon] = {}
        self.provider = QFileIconProvider()
        self.selected: str | None = None
        self.user_dir: Path | None = None
        self._worker: Worker | None = None

        self.setWindowTitle("Desktop Sorter")
        self.setWindowIcon(app_icon())
        if FRAMELESS:
            self.setTitleBar(TitleBar(self))
            for b in (self.titleBar.minBtn, self.titleBar.maxBtn, self.titleBar.closeBtn):
                b.setNormalColor(QColor(255, 255, 255, 200))
                b.setHoverColor(Qt.white)
            for b in (self.titleBar.minBtn, self.titleBar.maxBtn):
                b.setHoverBackgroundColor(QColor(255, 255, 255, 30))
                b.setPressedBackgroundColor(QColor(255, 255, 255, 50))
        if ACRYLIC:
            self.windowEffect.setAcrylicEffect(self.winId(), ACRYLIC_TINT)

        self._build()
        self.resize(1320, 860)
        QTimer.singleShot(0, self.rescan)

    # ---------- Aufbau ----------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 40 if FRAMELESS else 22, 22, 22)
        root.setSpacing(16)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("Desktop Sorter")
        title.setObjectName("h1")
        self.subtitle = QLabel("…")
        self.subtitle.setObjectName("sub")
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles)
        head.addStretch(1)

        self.t_public = Toggle("Öffentlicher Desktop", checked=True)
        self.t_folders = Toggle("Ordner einbeziehen")
        self.t_public.toggled.connect(self.rescan)
        self.t_folders.toggled.connect(self.refresh)
        self.b_scan = GlassButton("↻  Neu scannen")
        self.b_ai = GlassButton("✦  Mit Claude sortieren")
        self.b_undo = GlassButton("↶  Rückgängig")
        self.b_apply = GlassButton("Sortieren", primary=True)
        self.b_scan.clicked.connect(self.rescan)
        self.b_ai.clicked.connect(self.run_ai)
        self.b_undo.clicked.connect(self.undo)
        self.b_apply.clicked.connect(self.apply)
        for w in (self.t_public, self.t_folders):
            head.addWidget(w)
            head.addSpacing(6)
        head.addSpacing(10)
        for w in (self.b_scan, self.b_ai, self.b_undo, self.b_apply):
            head.addWidget(w)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(18)
        left = QVBoxLayout()
        left.setSpacing(16)

        self.grid = CardGrid()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.grid)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        self.grid.setAutoFillBackground(False)
        left.addWidget(scroll, 1)

        self.detail = GlassPanel()
        self.detail.setFixedHeight(214)
        dl = QVBoxLayout(self.detail)
        dl.setContentsMargins(18, 14, 18, 10)
        self.detail_title = QLabel("Gruppe anklicken, um ihren Inhalt zu sehen")
        self.detail_title.setObjectName("h3")
        self.detail_list = ItemList(icon=44)
        self.detail_list.dropped.connect(lambda ids: self.selected and self.assign(ids, self.selected))
        self.detail_list.menuRequested.connect(self.item_menu)
        dl.addWidget(self.detail_title)
        dl.addWidget(self.detail_list, 1)
        left.addWidget(self.detail)
        body.addLayout(left, 1)

        self.desk = GlassPanel()
        self.desk.setFixedWidth(370)
        rl = QVBoxLayout(self.desk)
        rl.setContentsMargins(18, 16, 18, 12)
        rh = QHBoxLayout()
        rt = QVBoxLayout()
        rt.setSpacing(0)
        lbl = QLabel("Auf dem Desktop")
        lbl.setObjectName("h2")
        self.desk_sub = QLabel("")
        self.desk_sub.setObjectName("sub")
        rt.addWidget(lbl)
        rt.addWidget(self.desk_sub)
        rh.addLayout(rt)
        rh.addStretch(1)
        self.b_accept = GlassButton("Vorschläge übernehmen", small=True)
        self.b_accept.clicked.connect(lambda: self.accept_suggestions(None))
        rh.addWidget(self.b_accept)
        rl.addLayout(rh)
        self.desk_list = ItemList(icon=48, cell=104)
        self.desk_list.dropped.connect(self.keep)
        self.desk_list.menuRequested.connect(self.item_menu)
        self.desk_list.activated_.connect(lambda i: self.accept_suggestions([i]))
        rl.addWidget(self.desk_list, 1)
        body.addWidget(self.desk)
        root.addLayout(body, 1)

        self.busy_widgets = [self.b_scan, self.b_ai, self.b_undo, self.b_apply,
                             self.b_accept, self.t_public, self.t_folders,
                             self.grid, self.detail, self.desk]

    def paintEvent(self, _):
        p = QPainter(self)
        paint_backdrop(p, self.rect(), overlay_only=ACRYLIC)

    # ---------- Daten ----------
    def icon_for(self, it: Item) -> QIcon:
        key = str(it.path)
        if key not in self.icons:
            ic = self.provider.icon(QFileInfo(key))
            if ic.isNull() or not ic.availableSizes():
                ic = letter_icon(it.label)
            self.icons[key] = ic
        return self.icons[key]

    def known_groups(self, groups=None) -> list[str]:
        names = list(groups or []) + [c for c in CATEGORIES if c != "Sonstiges"]
        names += [c for c, _ in self.toml_rules] + self.memory.groups
        return list(dict.fromkeys(names))

    def plan(self):
        """Unklare Objekte bleiben bis zur Entscheidung auf dem Desktop."""
        inc = self.t_folders.isChecked()
        pool = [it for it in self.items if it.kind != "folder" or inc]
        sure = [it for it in pool if it.confidence != "low"]
        groups, left = plan(sure, self.args.min_group, inc)
        unclear = [it for it in pool if it.confidence == "low"]
        return groups, unclear + sorted(left, key=lambda i: i.label.lower())

    def set_busy(self, text: str | None):
        for w in self.busy_widgets:
            w.setEnabled(text is None)
        if text:
            self.subtitle.setText(text)
        QApplication.setOverrideCursor(Qt.WaitCursor) if text else QApplication.restoreOverrideCursor()

    def run_bg(self, text, fn, on_done):
        self.set_busy(text)
        self._worker = Worker(fn, self)

        def ok(result):
            self.set_busy(None)
            on_done(result)

        def fail(msg):
            self.set_busy(None)
            self.refresh()
            self.message("Fehler", msg, warn=True)

        self._worker.done.connect(ok)
        self._worker.failed.connect(fail)
        self._worker.start()

    # ---------- Scan / Anzeige ----------
    def rescan(self):
        a, public_on = self.args, self.t_public.isChecked()

        def job():
            user, public = desktop_dirs()
            if a.desktop:
                user, public = a.desktop, Path("/nonexistent")
            if not public_on:
                public = Path("/nonexistent")
            rules = self.toml_rules + self.memory.rules()
            skip = set(CATEGORIES) | {c for c, _ in rules} | set(self.memory.groups)
            items = scan(user, public, skip)
            for it in items:
                classify(it, rules, self.memory)
            return user, items

        def done(res):
            self.user_dir, self.items = res
            self.icons.clear()
            self.refresh()

        self.run_bg("Scanne Desktop …", job, done)

    def refresh(self):
        groups, left = self.plan()
        if self.selected not in groups:
            self.selected = None

        cards = []
        for name, its in groups.items():
            c = GroupCard(name, [self.icon_for(i) for i in its[:7]], len(its),
                          selected=name == self.selected)
            c.clicked.connect(self.select_group)
            c.dropped.connect(self.on_card_drop)
            c.menuRequested.connect(self.card_menu)
            cards.append(c)
        new = GroupCard("", [], 0, new=True)
        new.dropped.connect(self.on_card_drop)
        cards.append(new)
        self.grid.set_cards(cards)

        self.desk_list.clear()
        unclear = 0
        for it in left:
            low = it.confidence == "low"
            unclear += low
            li = QListWidgetItem(badge_icon(self.icon_for(it)) if low else self.icon_for(it),
                                 it.label)
            li.setData(Qt.UserRole, self.items.index(it))
            li.setToolTip(
                (f"Unklar — Vorschlag: {it.category} ({it.reason})\nDoppelklick = übernehmen"
                 if it.category != "Sonstiges" else "Unklar — keine Zuordnung gefunden")
                if low else f"Bleibt auf dem Desktop ({it.reason})")
            self.desk_list.addItem(li)
        self.desk_sub.setText(f"{unclear} unklar · {len(left) - unclear} bleiben"
                              if unclear else f"{len(left)} bleiben · alles zugeordnet")
        self.b_accept.setVisible(any(i.confidence == "low" and i.category != "Sonstiges"
                                     for i in left))

        self.detail_list.clear()
        self.detail_list.setAcceptDrops(self.selected is not None)
        if self.selected:
            its = groups[self.selected]
            self.detail_title.setText(f"{self.selected}  ·  {len(its)} Objekte")
            for it in its:
                li = QListWidgetItem(self.icon_for(it), it.label)
                li.setData(Qt.UserRole, self.items.index(it))
                li.setToolTip(it.reason)
                self.detail_list.addItem(li)
        else:
            self.detail_title.setText("Gruppe anklicken, um ihren Inhalt zu sehen")

        n_items = sum(len(g) for g in groups.values())
        self.subtitle.setText(f"{len(self.items)} Objekte gefunden  ·  {n_items} werden in "
                              f"{len(groups)} Gruppen sortiert  ·  {unclear} unklar")

    def later_refresh(self):
        QTimer.singleShot(0, self.refresh)   # nach Ende des Drag-Vorgangs

    # ---------- Aktionen auf Objekten ----------
    def assign(self, ids, group: str):
        for i in ids:
            it = self.items[i]
            it.category, it.reason, it.confidence = group, "deine Auswahl", "user"
            self.memory.files[it.path.name] = group
        if group not in self.known_groups():
            self.memory.groups.append(group)
        self.later_refresh()

    def keep(self, ids):
        for i in ids:
            it = self.items[i]
            it.category, it.reason, it.confidence = "Sonstiges", "bleibt (deine Auswahl)", "keep"
            self.memory.files[it.path.name] = ""
        self.later_refresh()

    def accept_suggestions(self, ids):
        pool = [self.items[i] for i in ids] if ids else self.items
        for it in pool:
            if it.confidence == "low" and it.category != "Sonstiges":
                it.reason, it.confidence = f"Vorschlag übernommen ({it.reason})", "user"
                self.memory.files[it.path.name] = it.category
        self.later_refresh()

    def ask_text(self, title, label, text="", ) -> str | None:
        dlg = QInputDialog(self)
        dlg.setWindowTitle(title)
        dlg.setLabelText(label)
        dlg.setTextValue(text)
        dlg.setInputMode(QInputDialog.TextInput)
        return dlg.textValue().strip() if dlg.exec() else None

    def ask_group_name(self, text="") -> str | None:
        raw = self.ask_text("Neue Gruppe", "Name der Gruppe:", text)
        if not raw:
            return None
        name = INVALID.sub("", raw).strip(" .")
        if not name or RESERVED.match(name) or name == "Sonstiges":
            self.message("Ungültiger Name", f"„{raw}“ ist als Ordnername nicht erlaubt.", warn=True)
            return None
        return name

    def on_card_drop(self, group, ids):
        if not group:
            group = self.ask_group_name()
            if not group:
                return
        self.assign(ids, group)

    def select_group(self, name):
        self.selected = None if self.selected == name else name
        self.refresh()

    def item_menu(self, ids, pos):
        its = [self.items[i] for i in ids]
        groups, _ = self.plan()
        m = glass_menu(self)
        one = its[0] if len(its) == 1 else None
        if any(i.confidence == "low" and i.category != "Sonstiges" for i in its):
            hint = f": {one.category}" if one else ""
            m.addAction(f"✓  Vorschlag übernehmen{hint}", lambda: self.accept_suggestions(ids))
        sub = glass_menu(m, "Verschieben nach")
        for g in self.known_groups(groups):
            act = sub.addAction(g, lambda g=g: self.assign(ids, g))
            act.setEnabled(not (one and one.category == g and one.confidence != "low"))
        m.addMenu(sub)
        m.addAction("+  Neue Gruppe …", lambda: (n := self.ask_group_name()) and self.assign(ids, n))
        m.addAction("⌂  Auf dem Desktop lassen", lambda: self.keep(ids))
        m.addSeparator()
        if one:
            m.addAction("↗  Öffnen", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(one.path))))
            if one.category != "Sonstiges":
                m.addAction(f"★  Als Regel für „{one.category}“ merken …",
                            lambda: self.remember_rule(one))
        m.exec(pos)

    def remember_rule(self, it: Item):
        stem = it.label.split("_")[0].split(" ")[0]
        kw = self.ask_text("Regel merken",
                           f"Stichwort im Namen, das künftig zu „{it.category}“ führt:", stem)
        if kw:
            self.memory.keywords.setdefault(it.category, []).append(kw)
            self.memory.save()
            self.subtitle.setText(f"Regel gespeichert: „{kw}“ → {it.category}")

    def card_menu(self, name, pos):
        m = glass_menu(self)
        m.addAction("✎  Umbenennen …", lambda: self.rename_group(name))
        m.addAction("⌂  Auflösen (alles auf dem Desktop lassen)",
                    lambda: self.keep([self.items.index(i) for i in self.items
                                       if i.category == name and i.confidence != "low"]))
        m.exec(pos)

    def rename_group(self, old):
        new = self.ask_group_name(old)
        if not new or new == old:
            return
        if self.selected == old:
            self.selected = new
        self.assign([i for i, it in enumerate(self.items)
                     if it.category == old and it.confidence != "low"], new)
        if old in self.memory.groups:
            self.memory.groups.remove(old)
        if old in self.memory.keywords:
            self.memory.keywords.setdefault(new, []).extend(self.memory.keywords.pop(old))

    # ---------- Claude / Ausführen / Undo ----------
    def run_ai(self):
        todo = [it for it in self.items if it.confidence == "low"
                and (it.kind != "folder" or self.t_folders.isChecked())]
        if not todo:
            return self.message("Nichts zu tun", "Es gibt keine unklaren Objekte.")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            dlg = QInputDialog(self)
            dlg.setWindowTitle("Claude API-Schlüssel")
            dlg.setLabelText("ANTHROPIC_API_KEY (gilt nur für diese Sitzung):")
            dlg.setTextEchoMode(QLineEdit.Password)
            if not dlg.exec() or not dlg.textValue().strip():
                return
            os.environ["ANTHROPIC_API_KEY"] = dlg.textValue().strip()

        def job():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                classify_with_claude(todo, self.items, self.args.model)
            return err.getvalue().strip()

        def done(msg):
            self.refresh()
            if msg:
                self.message("Claude", msg, warn=True)

        self.run_bg(f"Claude ordnet {len(todo)} Objekt(e) zu …", job, done)

    def apply(self):
        groups, _ = self.plan()
        n = sum(len(g) for g in groups.values())
        if not n:
            return self.message("Nichts zu tun", "Keine Objekte zum Einsortieren.")
        if not self.confirm("Sortieren", f"{n} Objekte in {len(groups)} Gruppenordner "
                                         f"verschieben?\n\nMit „Rückgängig“ jederzeit umkehrbar."):
            return
        self.memory.save()
        try:
            _, denied = apply(self.user_dir, groups)
        except OSError as e:
            return self.message("Fehler", str(e), warn=True)
        text = f"{n - len(denied)} Objekte einsortiert."
        if denied:
            text += (f"\n\n{len(denied)} Verknüpfung(en) auf dem öffentlichen Desktop brauchen "
                     f"Administratorrechte:\n" + ", ".join(denied) +
                     "\n\nProgramm als Administrator starten oder „Öffentlicher Desktop“ ausschalten.")
        self.message("Fertig", text, warn=bool(denied))
        self.rescan()

    def undo(self):
        if not self.confirm("Rückgängig", "Letzte Sortierung vollständig rückgängig machen?"):
            return
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                undo("")
        except SystemExit as e:
            return self.message("Rückgängig", str(e), warn=True)
        self.message("Rückgängig", out.getvalue().strip())
        self.rescan()

    # ---------- Dialoge ----------
    def message(self, title, text, warn=False):
        box = QMessageBox(QMessageBox.Warning if warn else QMessageBox.Information,
                          title, text, QMessageBox.Ok, self)
        box.exec()

    def confirm(self, title, text) -> bool:
        box = QMessageBox(QMessageBox.Question, title, text,
                          QMessageBox.Yes | QMessageBox.Cancel, self)
        box.button(QMessageBox.Yes).setText("Ja")
        box.button(QMessageBox.Cancel).setText("Abbrechen")
        return box.exec() == QMessageBox.Yes


STYLE = """
* { color: white; }
QLabel#h1 { font-size: 28px; font-weight: 700; }
QLabel#h2 { font-size: 17px; font-weight: 700; }
QLabel#h3 { font-size: 14px; font-weight: 600; }
QLabel#sub { color: rgba(255,255,255,165); font-size: 12px; }
QToolTip { background: rgba(28,30,42,240); color: white; border: 1px solid rgba(255,255,255,50);
           border-radius: 8px; padding: 6px 8px; }
QMenu { background: rgba(30,32,46,238); border: 1px solid rgba(255,255,255,55);
        border-radius: 14px; padding: 6px; }
QMenu::item { padding: 7px 22px 7px 14px; border-radius: 8px; background: transparent; }
QMenu::item:selected { background: rgba(10,132,255,210); }
QMenu::item:disabled { color: rgba(255,255,255,90); }
QMenu::separator { height: 1px; background: rgba(255,255,255,35); margin: 5px 10px; }
QScrollArea, QScrollArea > QWidget > QWidget, QListWidget { background: transparent; border: none; }
QListWidget { outline: none; }
QListWidget::item { color: white; border-radius: 12px; padding: 4px; }
QListWidget::item:hover { background: rgba(255,255,255,24); }
QListWidget::item:selected { background: rgba(255,255,255,46); }
QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 0; }
QScrollBar::handle:vertical { background: rgba(255,255,255,70); border-radius: 4px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QDialog, QMessageBox, QInputDialog { background: #1C1E2A; }
QLineEdit { background: rgba(255,255,255,22); border: 1px solid rgba(255,255,255,60);
            border-radius: 9px; padding: 7px 10px; selection-background-color: #0A84FF; }
QLineEdit:focus { border-color: #0A84FF; }
QDialog QPushButton, QMessageBox QPushButton {
    background: rgba(255,255,255,26); border: 1px solid rgba(255,255,255,55);
    border-radius: 10px; padding: 6px 18px; min-width: 70px; }
QDialog QPushButton:hover, QMessageBox QPushButton:hover { background: rgba(255,255,255,42); }
QDialog QPushButton:default, QMessageBox QPushButton:default { background: #0A84FF; border-color: #0A84FF; }
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--desktop", type=Path, help="anderen Ordner sortieren (Test)")
    ap.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    ap.add_argument("--memory", type=Path, default=DEFAULT_MEMORY)
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--min-group", type=int, default=2)
    ap.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.rules == DEFAULT_RULES and not DEFAULT_RULES.exists():
        try:
            DEFAULT_RULES.write_text(DEFAULT_RULES_TEXT, encoding="utf-8")
        except OSError:
            pass

    if sys.platform == "win32":     # eigenes Taskleisten-Symbol statt python.exe
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("desktop.sorter")

    app = QApplication(sys.argv)

    def excepthook(exc_type, exc, tb):     # unerwartete Fehler als Dialog statt still
        import traceback
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        QMessageBox.critical(None, "Desktop Sorter — Fehler", text[-3000:])
    sys.excepthook = excepthook
    font = QFont()
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", "Arial"])
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyleSheet(STYLE)

    win = MainWindow(args)
    win.show()
    if args.screenshot:             # Testhilfe: nach dem Scan Bild speichern
        def snap():
            if win._worker and win._worker.isRunning():
                return QTimer.singleShot(200, snap)
            QApplication.processEvents()
            win.grab().save(str(args.screenshot))
            app.quit()
        QTimer.singleShot(800, snap)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
