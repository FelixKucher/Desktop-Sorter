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
from dataclasses import dataclass
from pathlib import Path

try:                        # Python ≥ 3.11
    import tomllib
except ModuleNotFoundError:
    try:                    # gleiche API als Paket: pip install tomli
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None      # → eingebauter Leser für regeln.toml

JOURNAL_DIR = Path.home() / ".desktop_sorter"
STORAGE_DIR = Path.home() / "DesktopSorter Boxen"   # Inhalt der Glas-Boxen
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


def _parse_rules_toml(text: str) -> dict[str, list[str]]:
    """Minimaler Leser für das Format von regeln.toml (Abschnitt [regeln] mit
    "Name" = ["…", "…"], auch mehrzeilig) — Fallback ohne tomllib/tomli."""
    def unescape(s: str) -> str:
        return re.sub(r'\\(["\\])', r"\1", s)

    text = re.sub(r"(?m)^\s*#.*$", "", text)
    m = re.search(r"(?ms)^\s*\[regeln\]\s*$(.*?)(?=^\s*\[|\Z)", text)
    if not m:
        return {}
    str_rx = r'"((?:[^"\\]|\\.)*)"'
    return {unescape(k): [unescape(v) for v in re.findall(str_rx, body)]
            for k, body in re.findall(str_rx + r"\s*=\s*\[(.*?)\]", m.group(1), re.S)}


def load_rules(path: Path | None) -> list[tuple[str, re.Pattern]]:
    if not path or not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    data = (tomllib.loads(text) if tomllib else {"regeln": _parse_rules_toml(text)}).get("regeln", {})
    return [(cat, re.compile("|".join(p), re.I)) for cat, p in data.items() if p]


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


def apply(target: Path, groups) -> tuple[Path, list[str]]:
    """Verschiebt jede Gruppe nach target/<Gruppe> (Box-Speicher oder Desktop)."""
    JOURNAL_DIR.mkdir(exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    journal = JOURNAL_DIR / f"journal-{time.strftime('%Y%m%d-%H%M%S')}.json"
    moves, created, denied = [], [], []
    try:
        for cat, its in groups.items():
            folder = target / cat
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


def _find_moved(dest: Path) -> Path | None:
    """Datei, die nach dem Sortieren in eine andere Box gezogen wurde, wiederfinden."""
    root = dest.parent.parent
    if dest.exists():
        return dest
    for d in (root.iterdir() if root.is_dir() else ()):
        cand = d / dest.name
        if d.is_dir() and cand.exists():
            return cand
    return None


def undo(arg: str) -> None:
    js = [Path(arg)] if arg else sorted(JOURNAL_DIR.glob("journal-*[0-9].json"))
    if not js:
        sys.exit("Kein Journal gefunden.")
    data = json.loads(js[-1].read_text(encoding="utf-8"))
    for src, dest in reversed(data["moves"]):
        found = _find_moved(Path(dest))
        if found and not Path(src).exists():
            shutil.move(str(found), src)
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


def _isolate_qt() -> None:
    """Qt auf die Plugins von PySide6 festnageln. Andere Programme mit eigener
    Qt-Version setzen oft QT_PLUGIN_PATH oder legen Qt-DLLs in den PATH; Qt lädt
    dann fremde Plugins und bricht mit „no Qt platform plugin could be
    initialized“ ab."""
    base = Path(PySide6.__file__).resolve().parent
    plugins = next((p for p in (base / "plugins", base / "Qt" / "plugins")
                    if (p / "platforms").is_dir()), None)
    reinstall = ("\n\nIn der Umgebung, mit der du das Programm startest, ausführen:\n\n"
                 "python -m pip install --force-reinstall PySide6 PySide6_Essentials "
                 "PySide6_Addons shiboken6")
    if plugins is None or (os.name == "nt" and
                           not (plugins / "platforms" / "qwindows.dll").exists()):
        _fatal(f"PySide6 ist unvollständig installiert (Qt-Plattform-Plugin fehlt in "
               f"{base})." + reinstall)
    os.environ["QT_PLUGIN_PATH"] = str(plugins)
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(plugins / "platforms")
    if os.name == "nt":                 # fremde Qt-DLL-Ordner aus dem PATH nehmen
        def foreign_qt(d: str) -> bool:
            try:
                p = Path(d)
                return p.resolve() != base and any(
                    (p / dll).exists() for dll in ("Qt6Core.dll", "Qt5Core.dll"))
            except (OSError, ValueError):
                return False
        parts = [d for d in os.environ.get("PATH", "").split(os.pathsep)
                 if d and not foreign_qt(d)]
        os.environ["PATH"] = os.pathsep.join([str(base)] + parts)


_isolate_qt()

from PySide6.QtCore import (QFileInfo, QFileSystemWatcher, QMimeData, QObject, QPoint,
                            QPointF, QRect, QRectF, QSize, Qt, QThread, QTimer, QUrl,
                            Signal)
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QDrag, QFont, QIcon,
                           QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QApplication,
                               QFileIconProvider, QFrame, QGraphicsDropShadowEffect,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListView, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QScrollArea, QSizeGrip, QSystemTrayIcon,
                               QVBoxLayout, QWidget)


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
    def __init__(self, args, fences: "FenceManager | None" = None):
        super().__init__()
        self.args = args
        self.fences = fences
        self.keep_running = False       # True → Schließen versteckt nur (Infobereich)
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
        self.b_apply = GlassButton("In Boxen sortieren", primary=True)
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
        if self.fences:
            names = self.fences.names() + names     # bestehende Boxen zuerst
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
        if not self.confirm("In Boxen sortieren",
                            f"{n} Objekte in {len(groups)} Glas-Boxen auf dem Desktop "
                            f"einsortieren?\n\nMit „Rückgängig“ jederzeit umkehrbar."):
            return
        self.memory.save()
        try:
            _, denied = apply(self.args.storage, groups)
        except OSError as e:
            return self.message("Fehler", str(e), warn=True)
        if self.fences:
            self.fences.sync()
        text = (f"{n - len(denied)} Objekte in Boxen einsortiert.\n\nDie Boxen liegen jetzt auf "
                f"dem Desktop: Klick startet, Titelleiste ziehen verschiebt, Doppelklick "
                f"auf den Titel klappt ein.")
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
        if self.fences:
            self.fences.sync()
        self.message("Rückgängig", out.getvalue().strip())
        self.rescan()

    def closeEvent(self, e):
        if self.keep_running:           # App läuft mit den Boxen im Infobereich weiter
            e.ignore()
            self.hide()
        else:
            super().closeEvent(e)

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


# --------------------------------------------------------------------------- #
# Glas-Boxen auf dem Desktop (Prinzip Stardock Fences)
# --------------------------------------------------------------------------- #
FENCE_TINT = 0x50201814         # AABBGGRR für Windows-Acrylic der Boxen
FENCE_RADIUS = 8                # = Windows-11-Fensterecken
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def win_acrylic(hwnd: int, tint: int = FENCE_TINT) -> bool:
    """Echter Acrylic-Blur hinter einem rahmenlosen Fenster + runde Ecken (Win11)."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    class ACCENT(ctypes.Structure):
        _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                    ("GradientColor", wintypes.DWORD), ("AnimationId", ctypes.c_int)]

    class WCA(ctypes.Structure):
        _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.POINTER(ACCENT)),
                    ("SizeOfData", ctypes.c_size_t)]

    try:
        accent = ACCENT(4, 0, tint, 0)                 # 4 = ACRYLICBLURBEHIND
        data = WCA(19, ctypes.pointer(accent), ctypes.sizeof(accent))  # 19 = ACCENT_POLICY
        ok = bool(ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))
        pref = ctypes.c_int(2)                         # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
        return ok
    except (AttributeError, OSError):
        return False


def open_path(path: str) -> None:
    if os.name == "nt":
        os.startfile(path)              # startet .lnk/.url/.exe wie ein Doppelklick
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def reveal_path(path: str) -> None:
    if os.name == "nt":
        subprocess.Popen(["explorer", f"/select,{path}"], creationflags=NO_WINDOW)
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))


def display_name(p: Path) -> str:
    return p.stem if p.suffix.lower() in (".lnk", ".url") else p.name


# ---------- Autostart (Verknüpfung im Autostart-Ordner) ----------
def startup_lnk() -> Path:
    return (Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows" /
            "Start Menu" / "Programs" / "Startup" / "Desktop Sorter.lnk")


def autostart_enabled() -> bool:
    return os.name == "nt" and startup_lnk().exists()


def set_autostart(on: bool) -> None:
    lnk = startup_lnk()
    if not on:
        if lnk.exists():
            lnk.unlink()
        return
    exe = Path(sys.executable)
    if exe.with_name("pythonw.exe").exists():       # ohne Konsolenfenster starten
        exe = exe.with_name("pythonw.exe")
    script = Path(__file__).resolve()
    q = lambda s: str(s).replace("'", "''")         # PowerShell-Quoting
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(lnk)}');"
          f"$s.TargetPath='{q(exe)}';"
          f"$s.Arguments='\"{q(script)}\" --background';"
          f"$s.WorkingDirectory='{q(script.parent)}';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   capture_output=True, timeout=30, creationflags=NO_WINDOW)


class IconCache:
    def __init__(self):
        self.provider = QFileIconProvider()
        self.cache: dict[str, QIcon] = {}

    def get(self, path: Path) -> QIcon:
        key = str(path)
        if key not in self.cache:
            ic = self.provider.icon(QFileInfo(key))
            if ic.isNull() or not ic.availableSizes():
                ic = letter_icon(display_name(path))
            self.cache[key] = ic
        return self.cache[key]


class FenceList(QListWidget):
    """Symbolraster einer Box. Klick startet, Ziehen verschiebt die Datei
    (in eine andere Box oder – via Explorer – zurück auf den Desktop)."""
    launched = Signal(str)
    filesDropped = Signal(list)
    menuRequested = Signal(str, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.IconMode)
        self.setMovement(QListView.Static)
        self.setResizeMode(QListView.Adjust)
        self.setWrapping(True)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.ElideRight)
        self.setObjectName("fence")
        self.setIconSize(QSize(40, 40))
        self.setGridSize(QSize(88, 90))
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setCursor(Qt.PointingHandCursor)
        self.customContextMenuRequested.connect(self._menu)
        self.itemClicked.connect(self._clicked)
        self._last = ("", 0.0)

    def resizeEvent(self, e):
        # Spalten gleichmäßig über die Boxbreite verteilen (≥ 80 px je Symbol);
        # Platz für eine evtl. eingeblendete Scrollleiste (8 px) ist reserviert.
        w = self.width() - 2 * self.frameWidth() - 12
        cols = max(1, w // 80)
        self.setGridSize(QSize(w // cols, 90))
        super().resizeEvent(e)

    def _clicked(self, it):
        path, now = it.data(Qt.UserRole), time.monotonic()
        if self._last[0] == path and now - self._last[1] < 0.8:   # Doppelklick ≠ 2× starten
            return
        self._last = (path, now)
        self.clearSelection()
        self.launched.emit(path)

    def startDrag(self, _actions):
        it = self.currentItem()
        if not it:
            return
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(it.data(Qt.UserRole))])
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.setPixmap(it.icon().pixmap(48, 48))
        drag.setHotSpot(QPoint(24, 24))
        drag.exec(Qt.MoveAction | Qt.CopyAction, Qt.MoveAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and e.source() is not self:
            e.setDropAction(Qt.CopyAction)      # wir verschieben selbst; Quelle löscht nichts
            e.accept()
        else:
            e.ignore()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        e.setDropAction(Qt.CopyAction)
        e.accept()
        if paths:
            self.filesDropped.emit(paths)

    def _menu(self, pos):
        it = self.itemAt(pos)
        if it:
            self.menuRequested.emit(it.data(Qt.UserRole), self.viewport().mapToGlobal(pos))


class Fence(QWidget):
    """Eine Glas-Box auf dem Desktop."""
    TITLE = 34

    def __init__(self, mgr: "FenceManager", name: str):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnBottomHint)
        self.mgr, self.name = mgr, name
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.setWindowTitle(name)
        self.collapsed, self.full_h, self.count = False, 260, 0
        self._acrylic = False
        self.setMinimumSize(150, self.TITLE)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, self.TITLE + 2, 6, 6)
        self.list = FenceList(self)
        self.list.launched.connect(mgr.launch)
        self.list.filesDropped.connect(lambda ps: mgr.move_to(ps, self.name))
        self.list.menuRequested.connect(lambda p, pos: mgr.item_menu(self, p, pos))
        lay.addWidget(self.list)
        self.grip = QSizeGrip(self)
        self.grip.setFixedSize(14, 14)
        self.grip.setStyleSheet("background: transparent;")

        self._save = QTimer(self, singleShot=True, interval=400)
        self._save.timeout.connect(lambda: mgr.save_geometry(self))

    # ----- Inhalt -----
    def set_items(self, paths: list[Path]):
        self.list.clear()
        for p in sorted(paths, key=lambda q: display_name(q).lower()):
            it = QListWidgetItem(self.mgr.icons.get(p), display_name(p))
            it.setData(Qt.UserRole, str(p))
            it.setToolTip(f"{display_name(p)}\nKlicken zum Starten")
            self.list.addItem(it)
        self.count = len(paths)
        self.update()

    # ----- Darstellung -----
    def showEvent(self, e):
        super().showEvent(e)
        if not self._acrylic:
            self._acrylic = win_acrylic(int(self.winId()))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        if not self._acrylic:                 # ohne Blur: dunkleres Milchglas
            path = QPainterPath()
            path.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), FENCE_RADIUS, FENCE_RADIUS)
            p.fillPath(path, QColor(16, 18, 28, 120))
        paint_glass(p, r, FENCE_RADIUS, strength=0.9)
        f = self.font()
        f.setPixelSize(13)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(Qt.white)
        tr = QRectF(14, 0, self.width() - 60, self.TITLE)
        p.drawText(tr, Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(self.name, Qt.ElideRight, int(tr.width())))
        f.setWeight(QFont.Normal)
        f.setPixelSize(11)
        p.setFont(f)
        p.setPen(TEXT_2)
        p.drawText(QRectF(self.width() - 46, 0, 32, self.TITLE), Qt.AlignVCenter | Qt.AlignRight,
                   f"{self.count}" + (" ▸" if self.collapsed else ""))
        if not self.collapsed:
            p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            p.drawLine(QPointF(12, self.TITLE), QPointF(self.width() - 12, self.TITLE))

    def resizeEvent(self, e):
        self.grip.move(self.width() - self.grip.width() - 2, self.height() - self.grip.height() - 2)
        if not self.collapsed:
            self.full_h = self.height()
        self._save.start()

    def moveEvent(self, e):
        self._save.start()

    # ----- Maus: Titelleiste -----
    def _on_title(self, e) -> bool:
        return e.position().y() <= self.TITLE

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._on_title(e):
            if not self.windowHandle().startSystemMove():      # Fallback: manuell ziehen
                self._drag_from = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if getattr(self, "_drag_from", None) is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, e):
        self._drag_from = None

    def mouseDoubleClickEvent(self, e):
        if self._on_title(e):
            self.set_collapsed(not self.collapsed)

    def contextMenuEvent(self, e):
        if e.pos().y() <= self.TITLE:
            self.mgr.fence_menu(self, e.globalPos())

    def set_collapsed(self, on: bool, save=True):
        if on == self.collapsed:
            return
        if on:
            self.full_h = self.height()
        self.collapsed = on
        self.list.setVisible(not on)
        self.grip.setVisible(not on)
        self.setMinimumHeight(self.TITLE if on else 80)
        self.resize(self.width(), self.TITLE if on else self.full_h)
        self.update()
        if save:
            self._save.start()

    # ----- Drop auf Titelleiste/Rand -----
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.setDropAction(Qt.CopyAction)
            e.accept()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        e.setDropAction(Qt.CopyAction)
        e.accept()
        if paths:
            self.mgr.move_to(paths, self.name)


class FenceManager(QObject):
    """Hält eine Box pro Unterordner von STORAGE_DIR und folgt Änderungen live."""
    openManager = Signal()
    changed = Signal()

    def __init__(self, storage: Path):
        super().__init__()
        self.storage = storage
        self.storage.mkdir(parents=True, exist_ok=True)
        self.cfg_path = storage / ".boxen.json"
        try:
            self.cfg = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cfg = {}
        self.fences: dict[str, Fence] = {}
        self.icons = IconCache()
        self.visible = True
        self.watcher = QFileSystemWatcher(self)
        self._sync = QTimer(self, singleShot=True, interval=300)
        self._sync.timeout.connect(self.sync)
        self.watcher.directoryChanged.connect(lambda _: self._sync.start())

    # ----- Abgleich mit dem Dateisystem -----
    def group_dirs(self) -> dict[str, Path]:
        if not self.storage.is_dir():
            return {}
        return {d.name: d for d in sorted(self.storage.iterdir())
                if d.is_dir() and not d.name.startswith(".")}

    def names(self) -> list[str]:
        return list(self.group_dirs())

    def sync(self):
        dirs = self.group_dirs()
        for name in [n for n in self.fences if n not in dirs]:
            self.fences.pop(name).deleteLater()
        for i, (name, d) in enumerate(dirs.items()):
            fence = self.fences.get(name)
            if fence is None:
                fence = self.fences[name] = Fence(self, name)
                self._place(fence, i)
                if self.visible:
                    fence.show()
            fence.set_items([p for p in d.iterdir()
                             if p.name.lower() != "desktop.ini" and not p.name.startswith(".")])
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        self.watcher.addPaths([str(self.storage)] + [str(d) for d in dirs.values()])
        self.changed.emit()

    def _place(self, fence: Fence, index: int):
        g = self.cfg.get(fence.name)
        screen = QApplication.primaryScreen().availableGeometry()
        if g:
            rect = QRect(g["x"], g["y"], g["w"], g["h"])
            on_screen = any(s.availableGeometry().intersects(rect)
                            for s in QApplication.screens())
            if on_screen:
                fence.setGeometry(rect)
                fence.full_h = g["h"]
                if g.get("collapsed"):
                    fence.set_collapsed(True, save=False)
                return
        # Neue Box: von rechts oben spaltenweise nach links (Desktop-Symbole sitzen links)
        w, h, gap = 360, 250, 16
        rows = max(1, (screen.height() - gap) // (h + gap))
        col, row = divmod(index, rows)
        fence.setGeometry(screen.right() - gap - w - col * (w + gap),
                          screen.top() + gap + row * (h + gap), w, h)

    def save_geometry(self, fence: Fence):
        if fence.name not in self.fences:
            return
        g = fence.geometry()
        self.cfg[fence.name] = {"x": g.x(), "y": g.y(), "w": g.width(),
                                "h": fence.full_h if fence.collapsed else g.height(),
                                "collapsed": fence.collapsed}
        self._write_cfg()

    def _write_cfg(self):
        try:
            self.cfg_path.write_text(json.dumps(self.cfg, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
        except OSError:
            pass

    # ----- Aktionen -----
    def launch(self, path: str):
        try:
            open_path(path)
        except OSError as e:
            QMessageBox.warning(None, "Desktop Sorter", f"Konnte nicht starten:\n{path}\n\n{e}")

    def _move(self, paths, folder: Path) -> list[str]:
        failed = []
        for src in map(Path, paths):
            if not src.exists() or src.parent == folder:
                continue
            try:
                shutil.move(str(src), str(unique_dest(folder, src.name)))
            except OSError as e:
                failed.append(f"{src.name}: {e.strerror or e}")
        if failed:
            QMessageBox.warning(None, "Desktop Sorter",
                                "Nicht verschoben (öffentlicher Desktop braucht Adminrechte):\n\n"
                                + "\n".join(failed))
        self._sync.start()
        return failed

    def move_to(self, paths, name: str):
        folder = self.storage / name
        folder.mkdir(exist_ok=True)
        self._move(paths, folder)

    def to_desktop(self, paths):
        self._move(paths, desktop_dirs()[0])

    def new_box(self, paths=()):
        name = ask_box_name("Neue Box", "")
        if name:
            (self.storage / name).mkdir(exist_ok=True)
            if paths:
                self.move_to(paths, name)
            self._sync.start()

    def rename(self, fence: Fence):
        new = ask_box_name("Box umbenennen", fence.name)
        if not new or new == fence.name or (self.storage / new).exists():
            return
        (self.storage / fence.name).rename(self.storage / new)
        if fence.name in self.cfg:
            self.cfg[new] = self.cfg.pop(fence.name)
        self.fences[new] = self.fences.pop(fence.name)
        fence.name = new
        fence.setWindowTitle(new)
        fence.update()
        self._write_cfg()
        self._sync.start()

    def dissolve(self, fence: Fence):
        if QMessageBox.question(None, "Box auflösen",
                                f"Alle {fence.count} Objekte aus „{fence.name}“ zurück auf den "
                                f"Desktop legen und die Box entfernen?") != QMessageBox.Yes:
            return
        folder = self.storage / fence.name
        if not self._move([str(p) for p in folder.iterdir()], desktop_dirs()[0]):
            shutil.rmtree(folder, ignore_errors=True)
            self.cfg.pop(fence.name, None)
            self._write_cfg()
        self._sync.start()

    def set_visible(self, on: bool):
        self.visible = on
        for f in self.fences.values():
            f.setVisible(on)

    # ----- Menüs -----
    def item_menu(self, fence: Fence, path: str, pos):
        m = glass_menu()
        m.addAction("▶  Starten", lambda: self.launch(path))
        m.addAction("↗  Dateispeicherort öffnen", lambda: reveal_path(path))
        sub = glass_menu(m, "Verschieben in")
        for n in self.names():
            if n != fence.name:
                sub.addAction(n, lambda n=n: self.move_to([path], n))
        sub.addSeparator()
        sub.addAction("+  Neue Box …", lambda: self.new_box([path]))
        m.addMenu(sub)
        m.addAction("⌂  Zurück auf den Desktop", lambda: self.to_desktop([path]))
        m.exec(pos)

    def fence_menu(self, fence: Fence, pos):
        m = glass_menu()
        m.addAction("▴  Ausklappen" if fence.collapsed else "▾  Einklappen",
                    lambda: fence.set_collapsed(not fence.collapsed))
        m.addAction("✎  Umbenennen …", lambda: self.rename(fence))
        m.addAction("+  Neue Box …", lambda: self.new_box())
        m.addAction("⌂  Box auflösen (alles auf den Desktop)", lambda: self.dissolve(fence))
        m.addSeparator()
        m.addAction("✦  Desktop Sorter öffnen", self.openManager.emit)
        m.addAction("⏻  Beenden", QApplication.quit)
        m.exec(pos)


def ask_box_name(title: str, text: str) -> str | None:
    dlg = QInputDialog()
    dlg.setWindowTitle(title)
    dlg.setLabelText("Name der Box:")
    dlg.setTextValue(text)
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint)
    if not dlg.exec():
        return None
    name = INVALID.sub("", dlg.textValue()).strip(" .")
    if not name or RESERVED.match(name) or name.startswith("."):
        QMessageBox.warning(None, "Ungültiger Name", "Dieser Name ist nicht erlaubt.")
        return None
    return name


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
QListWidget#fence::item { padding: 2px 1px; border-radius: 10px; }
QListWidget#fence::item:selected { background: transparent; }
QListWidget#fence::item:hover { background: rgba(255,255,255,30); }
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
    ap.add_argument("--storage", type=Path, default=STORAGE_DIR,
                    help="Ordner mit dem Inhalt der Glas-Boxen")
    ap.add_argument("--background", action="store_true",
                    help="nur Boxen + Infobereich-Symbol (für den Autostart)")
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

    if args.screenshot:             # Testhilfe: nur Hauptfenster rendern
        win = MainWindow(args)
        win.show()
    else:
        return run_app(app, args)
    if args.screenshot:             # Testhilfe: nach dem Scan Bild speichern
        def snap():
            if win._worker and win._worker.isRunning():
                return QTimer.singleShot(200, snap)
            QApplication.processEvents()
            win.grab().save(str(args.screenshot))
            app.quit()
        QTimer.singleShot(800, snap)
    sys.exit(app.exec())


def run_app(app: QApplication, args) -> None:
    """Dauerbetrieb: Boxen auf dem Desktop, Infobereich-Symbol, Hauptfenster bei Bedarf."""
    # Nur eine Instanz: ein zweiter Start öffnet bloß das Hauptfenster der ersten.
    server_name = "DesktopSorter-" + os.environ.get("USERNAME", os.environ.get("USER", "u"))
    sock = QLocalSocket()
    sock.connectToServer(server_name)
    if sock.waitForConnected(300):
        sock.write(b"background" if args.background else b"show")
        sock.waitForBytesWritten(300)
        return
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    server.listen(server_name)

    app.setQuitOnLastWindowClosed(False)
    fences = FenceManager(args.storage)
    fences.sync()
    state = {"win": None}

    def open_manager():
        win = state["win"]
        if win is None:
            win = state["win"] = MainWindow(args, fences)
            win.keep_running = QSystemTrayIcon.isSystemTrayAvailable()
        win.show()
        win.showNormal()
        win.raise_()
        win.activateWindow()

    fences.openManager.connect(open_manager)

    def on_connection():
        conn = server.nextPendingConnection()
        if conn.waitForReadyRead(300) and bytes(conn.readAll()) == b"show":
            open_manager()
        conn.close()

    server.newConnection.connect(on_connection)

    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(app_icon(), app)
        tray.setToolTip("Desktop Sorter")
        menu = glass_menu()
        menu.addAction("✦  Desktop Sorter öffnen", open_manager)
        menu.addAction("+  Neue Box …", lambda: fences.new_box())
        act_vis = menu.addAction("Boxen anzeigen")
        act_vis.setCheckable(True)
        act_vis.setChecked(True)
        act_vis.toggled.connect(fences.set_visible)
        if os.name == "nt":
            act_auto = menu.addAction("Mit Windows starten")
            act_auto.setCheckable(True)
            act_auto.setChecked(autostart_enabled())
            act_auto.toggled.connect(set_autostart)
        menu.addSeparator()
        menu.addAction("⏻  Beenden", app.quit)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda reason: open_manager()
                               if reason == QSystemTrayIcon.Trigger else None)
        tray.show()
        app._tray = tray                # Referenz halten

    if not args.background or not fences.fences:
        open_manager()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
