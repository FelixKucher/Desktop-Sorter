#!/usr/bin/env python3
"""
DesktopSorter.pyw — sortiert Windows-Desktop-Symbole thematisch in Gruppen
(wie die iOS-App-Mediathek), mit Liquid-Glass-Oberfläche. Eine einzige Datei.

Einmalig installieren (PowerShell):
    py -m pip install PySide6 PySideSix-Frameless-Window

Starten: Doppelklick auf DesktopSorter.pyw   (oder: py DesktopSorter.pyw)

Neben der Datei entstehen automatisch:
    regeln.toml           eigene Themen (z. B. „Hausplanung“) — frei editierbar
    entscheidungen.json   deine Zuordnungen und gelernten Stichwörter
Journal für „Rückgängig“: %USERPROFILE%\\.desktop_sorter

Bedienung
  • Gruppen erscheinen als Glaskarten. Rechts „Auf dem Desktop“: was liegen
    bleibt; orange „?“ = unklar.
  • Objekte per Drag & Drop auf eine Karte, auf „+ Neue Gruppe“, auf „Papierkorb“
    oder zurück nach rechts ziehen. Rechtsklick für alle Optionen, Doppelklick =
    Vorschlag. Bestehende Boxen (▣) sind immer als Karte da.
  • Unterordner werden mit einsortiert; Programm-, Spiel- und Projektordner (⚙)
    bleiben liegen, damit sie weiter funktionieren.
  • Recherche: Jede Zuordnung wird in offenen Quellen gegengeprüft (Wikidata,
    Wikipedia, Flathub, Chocolatey) – bei jedem Scan und vor dem Sortieren.
    Widersprüche bleiben als „?“ zur Entscheidung liegen.
  • „In Boxen sortieren“ verschiebt erst nach Bestätigung; „Rückgängig“ macht es
    komplett rückgängig.
  • Die Leiste darunter erreicht alles Weitere: Ordner-Assistent, Downloads
    aufräumen, Papierkorb, neue Box, Boxen anordnen/ausblenden, Einstellungen.
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
import threading
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
     r"roomsketcher|sweet home|sketchup|solidworks|kicad|^printer(\.cfg)?$|klipper"),
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

# Kategorien des Ordner-Assistenten → Desktop-Gruppe (Rest: „Büro & Dokumente“)
DOC_GROUPS = {"invoice": "Finanzen", "bank": "Finanzen", "tax": "Finanzen",
              "payslip": "Finanzen", "insurance": "Finanzen"}

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
    role: str = ""                # Ordner: program | project | content | empty
    check: str = ""               # Ergebnis der Recherche (confirmed, corrected, conflict …)

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
        self.aliases: dict[str, str] = d.get("umbenannt", {})   # umbenannte Boxen: alt → neu

    def rules(self) -> list[tuple[str, re.Pattern]]:
        return [(c, re.compile("|".join(map(re.escape, kws)), re.I))
                for c, kws in self.keywords.items() if kws]

    def alias(self, cat: str) -> str:
        seen = {"Sonstiges"}                        # „Sonstiges“ = bleibt liegen, nie umleiten
        while cat in self.aliases and cat not in seen:
            seen.add(cat)
            cat = self.aliases[cat]
        return cat

    def renamed(self, old: str, new: str) -> None:
        """Box umbenannt: Entscheidungen, Regeln und künftige Zuordnungen folgen."""
        self.aliases = {k: (new if v == old else v) for k, v in self.aliases.items()}
        self.aliases[old] = new
        self.aliases.pop(new, None)
        self.files = {k: (new if v == old else v) for k, v in self.files.items()}
        if old in self.keywords:
            self.keywords.setdefault(new, []).extend(self.keywords.pop(old))
        self.groups = [new if x == old else x for x in self.groups]

    def save(self) -> None:
        data = {"gruppen": self.groups, "dateien": self.files, "regeln": self.keywords}
        if self.aliases:
            data["umbenannt"] = self.aliases
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def classify(it: Item, rules, memory: Memory | None = None) -> None:
    def hit(cat, why, conf="high"):
        it.category, it.reason, it.confidence = (memory.alias(cat) if memory else cat), why, conf

    exe = Path(it.target.split(" -")[0]).name.lower() if it.kind == "lnk" else ""
    # Dateien: ohne Endung vergleichen – sonst wird jede „….pdf“ zur „bekannten App“
    hay = f"{it.path.stem if it.kind == 'file' else it.label} {exe}".strip().lower()

    if memory and it.path.name in memory.files:
        cat = memory.files[it.path.name]
        return hit(cat or "Sonstiges", "deine frühere Entscheidung",
                   "user" if cat else "keep")
    for cat, rx in rules:
        if m := rx.search(it.label):
            return hit(cat, f"eigene Regel '{m.group(0)}'")
    if it.kind == "file":                      # Dokumente: Thema vor App-Namen
        for cat, pat in KEYWORDS:
            if m := re.search(pat, it.label, re.I):
                return hit(cat, f"Stichwort '{m.group(0)}'")
    for cat, pat in KNOWN_APPS:
        if m := re.search(pat, hay, re.I):
            return hit(cat, f"bekannte App '{m.group(0).strip()}'")
    if it.target and GAME_TARGET.search(it.target):
        return hit("Spiele", "Ziel liegt in Spielebibliothek", "medium")
    for cat, pat in KEYWORDS:
        if m := re.search(pat, it.label, re.I):
            return hit(cat, f"Stichwort '{m.group(0)}'")
    if it.kind == "file":                      # zweisprachiges Dokument-Lexikon
        doc = org_classify_document(it.path.name, "", org_kind(it.path))[0]
        if doc and doc != "document":
            return hit(DOC_GROUPS.get(doc, "Büro & Dokumente"),
                       f"Dokument: {ORG_CATEGORIES[doc]['label']}", "medium")
    ext = it.path.suffix.lower().lstrip(".")
    if it.kind == "file" and ext in EXTENSIONS:
        return hit(EXTENSIONS[ext], f"Dateityp .{ext}", "low")
    hit("Sonstiges", "keine Regel", "low")


# --------------------------------------------------------------------------- #
# Unterordner: Programm/Projekt (bleibt liegen) oder Inhalt (wird einsortiert)
# --------------------------------------------------------------------------- #
PROGRAM_EXTS = {".exe", ".dll", ".pak", ".jar", ".msi", ".sys", ".ocx", ".asar"}
PROGRAM_MARKERS = {".build.info", "steam_appid.txt", "unityplayer.dll", "unins000.exe",
                   "uninstall.exe", "uninst.exe", "launcher.exe", "gameoverlayrenderer64.dll",
                   "eosoverlayrenderer-win64-shipping.exe", "engine", "_commonredist"}
FOLDER_CONTENT_GROUPS = {"image": "Bilder & Grafik", "video": "Musik & Video",
                         "audio": "Musik & Video", "document": "Büro & Dokumente",
                         "cad": "3D-Druck & CAD", "code": "Entwicklung", "archive": "Archive",
                         "installer": "System & Datenträger", "science": "Büro & Dokumente"}
FOLDER_SCAN_LIMIT = 1500         # so viele Einträge je Ordner höchstens ansehen


def installed_locations(reg=None) -> set[str]:
    """InstallLocation aller in Windows eingetragenen Programme (normalisiert)."""
    if reg is None:
        if os.name != "nt":
            return set()
        import winreg as reg
    out = set()
    roots = [(reg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
             (reg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
             (reg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hive, key in roots:
        try:
            with reg.OpenKey(hive, key) as k:
                i = 0
                while True:
                    try:
                        sub = reg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with reg.OpenKey(k, sub) as sk:
                            loc = str(reg.QueryValueEx(sk, "InstallLocation")[0]).strip().strip('"')
                    except OSError:
                        continue
                    if len(loc) > 3:
                        out.add(os.path.normcase(os.path.normpath(loc)))
        except OSError:
            continue
    return out


def folder_role(path: Path, install_dirs: set[str] = frozenset()) -> tuple[str, str]:
    """(Rolle, Begründung). „program“/„project“ bleiben liegen, damit sie funktionieren."""
    norm = os.path.normcase(os.path.normpath(str(path)))
    if norm in install_dirs or any(d.startswith(norm + os.sep) for d in install_dirs):
        return "program", "Programmordner – in Windows als installiertes Programm eingetragen"
    try:
        if org_is_project_dir(path):
            return "project", "Projektordner (Code/Versionsverwaltung) – bleibt liegen"
    except Exception:                                   # noqa: BLE001
        pass
    seen, exe = 0, None
    stack = [(path, 0)]
    while stack and seen < FOLDER_SCAN_LIMIT:
        d, depth = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            seen += 1
            n = e.name.lower()
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if n in PROGRAM_MARKERS and depth <= 1:
                return "program", f"Programm- oder Spielordner (enthält {e.name}) – bleibt liegen"
            if depth >= 1 and (n in ORG_PROJECT_STRONG or n.endswith((".sln", ".csproj", ".vcxproj"))):
                return "project", f"enthält Code-Projekte ({Path(d).name}) – bleibt liegen"
            if not is_dir:
                ext = os.path.splitext(n)[1]
                if ext == ".ino":
                    return "project", f"Arduino-Sketch ({e.name}) – bleibt liegen"
                if ext in PROGRAM_EXTS and depth <= 2:
                    exe = exe or e.name
            elif depth < 2 and not n.startswith("."):
                stack.append((Path(e.path), depth + 1))
    if exe:
        return "program", f"Programm- oder Spielordner (enthält {exe}) – bleibt liegen"
    return ("content" if seen else "empty"), ""


def folder_content_group(path: Path) -> tuple[str, float, int]:
    """Häufigste Dateiart im Ordner → (Gruppe, Anteil, Anzahl Dateien)."""
    counts: dict[str, int] = {}
    total = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            if fn.lower() in ("desktop.ini", "thumbs.db"):
                continue
            k = org_kind(Path(fn))
            counts[k] = counts.get(k, 0) + 1
            total += 1
            if total >= FOLDER_SCAN_LIMIT:
                break
        if total >= FOLDER_SCAN_LIMIT:
            break
    if not total:
        return "", 0.0, 0
    kind, n = max(counts.items(), key=lambda kv: kv[1])
    return FOLDER_CONTENT_GROUPS.get(kind, ""), n / total, total


def classify_folder(it: Item, install_dirs: set[str] = frozenset()) -> None:
    """Unterordner einordnen. Programm-/Projektordner bleiben liegen; eigene
    Entscheidungen (aus dem Gedächtnis) haben Vorrang."""
    if it.kind != "folder":
        return
    role, why = folder_role(it.path, install_dirs)
    it.role = role
    if it.confidence in ("user", "keep"):
        return
    if role in ("program", "project"):
        it.category, it.reason, it.confidence = "Sonstiges", why, "keep"
        return
    if it.confidence == "high":                         # Name eindeutig (Regel/Stichwort)
        return
    cat, _score, _terms, _year = org_classify_document(it.label, "", "document")
    if cat:
        it.category, it.confidence = DOC_GROUPS.get(cat, "Büro & Dokumente"), "medium"
        it.reason = f"Ordnername: {ORG_CATEGORIES[cat]['label']}"
        return
    group, share, n = folder_content_group(it.path)
    if group and share >= 0.6:
        it.category, it.confidence = group, "medium"
        it.reason = f"Inhalt: {round(share * 100)} % {group} ({n} Dateien)"
    elif role == "empty":
        it.reason = "leerer Ordner"


def folder_has_binaries(path: Path, limit: int = 5000) -> str:
    """Tiefe Suche nach Programmdateien (für Ordner, die online als Software gelten)."""
    seen = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            seen += 1
            if os.path.splitext(fn)[1].lower() in PROGRAM_EXTS:
                return fn
        if seen >= limit:
            break
    return ""


# --------------------------------------------------------------------------- #
# Recherche in offenen Quellen – prüft jede Zuordnung gegen (ohne API-Schlüssel)
# --------------------------------------------------------------------------- #
RESEARCH_UA = "DesktopSorter/1.0 (https://github.com/FelixKucher/Desktop-Sorter)"
RESEARCH_CACHE = JOURNAL_DIR / "recherche_cache.json"
RESEARCH_CACHE_DAYS = 90         # Treffer so lange merken …
RESEARCH_MISS_DAYS = 30          # … „nichts gefunden“ kürzer
RESEARCH_MAX = 250               # höchstens so viele Namen pro Durchgang
RESEARCH_LEAVE = ("program", "project")
RESEARCH_EXE = {".exe", ".msi"}
# Schlüssel → (Anzeigename, Server, Pause zwischen Anfragen in s, Bedeutung)
RESEARCH_SOURCES: dict[str, tuple[str, str, float, str]] = {
    "wikidata": ("Wikidata", "wikidata.org", 0.5,
                 "freie Wissensdatenbank (CC0): Beschreibung und „ist ein(e)“, z. B. Computerspiel"),
    "wikipedia": ("Wikipedia", "wikipedia.org", 0.5,
                  "Enzyklopädie DE/EN – springt ein, wenn Wikidata nichts findet"),
    "flathub": ("Flathub", "flathub.org", 0.2,
                "offener App-Katalog mit Standard-Kategorien (Spiel, Entwicklung, Grafik …)"),
    "chocolatey": ("Chocolatey", "community.chocolatey.org", 0.3,
                   "Windows-Paketverzeichnis der Community: Programme und Schlagwörter"),
}
GAMING = {"Spiele", "Spiele-Launcher & Tools"}       # gelten untereinander als Übereinstimmung
ONLINE_RULES: list[tuple[str, str]] = [
    ("Spiele-Launcher & Tools", r"spieleplattform|vertriebsplattform|distribution platform|"
                                r"digital distribution|game launcher|launcher|mod manager|"
                                r"game client|spiele-?client|game store|modding"),
    ("Entwicklung", r"entwicklungsumgebung|integrated development|\bide\b|quelltext-?editor|"
                    r"source[- ]code editor|code editor|programmiersprache|programming|"
                    r"programmierung|compiler|debugger|versionsverwaltung|version control|"
                    r"software development|softwareentwicklung|hard- und softwareplattform|"
                    r"mikrocontroller|microcontroller|open hardware|open-source hardware|"
                    r"electronics platform|virtualisierung|virtual machine|hypervisor|\bsdk\b|"
                    r"framework|spiel-?engine|game engine"),
    ("Spiele", r"computerspiel|videospiel|\bspiel\b|video ?game|\bgames?\b|mmorpg|rollenspiel|"
               r"shooter|strategiespiel|battle royale|simulationsspiel|downloadable content|"
               r"game expansion|erweiterung .*spiel"),
    ("3D-Druck & CAD", r"\bcad\b|slicer|3d-druck|3d printing|3d-modell|3d model|3d-grafik|"
                       r"3d graphics|3d computer graphics|computer-aided design|rechnergestützt"),
    ("Kommunikation", r"messenger|instant[- ]messaging|\bvoip\b|\bchat|videokonferenz|"
                      r"video ?conferenc|e-mail-programm|e-?mail[- ]client|telefonie|"
                      r"kommunikationsdienst|collaboration platform"),
    ("Browser & Internet", r"webbrowser|web browser|\bbrowser\b|\bftp\b|\bvpn\b|"
                           r"download-?manager|passwort-?manager|password manager"),
    ("Büro & Dokumente", r"\boffice\b|bürosoftware|textverarbeitung|word processor|"
                         r"tabellenkalkulation|spreadsheet|\bpdf\b|präsentationsprogramm|"
                         r"presentation program|notiz|note-taking"),
    ("Bilder & Grafik", r"bildbearbeitung|image editor|raster graphics|grafiksoftware|"
                        r"vektorgrafik|vector graphics|fotobearbeitung|photo editing|"
                        r"image manipulation"),
    ("Musik & Video", r"mediaplayer|media player|musik-?streaming|music streaming|"
                      r"videobearbeitung|video editing|audio editor|streaming"),
    ("Hardware & Peripherie", r"treiber|\bdriver|peripher|\brgb\b|mainboard|motherboard|"
                              r"\bmaus\b|\bmouse\b|\bmice\b|tastatur|keyboard|drucker|printer|"
                              r"grafikkarte|graphics card"),
    ("System & Datenträger", r"dienstprogramm|\butilit|antivir|backup|partition|"
                             r"disk imag|datenträger|betriebssystem|operating system|"
                             r"systemprogramm|system software"),
]
FLATHUB_MAIN = {"game": "Spiele", "development": "Entwicklung", "graphics": "Bilder & Grafik",
                "audiovideo": "Musik & Video", "office": "Büro & Dokumente",
                "network": "Browser & Internet", "utility": "System & Datenträger",
                "system": "System & Datenträger"}
FLATHUB_SUB = {"InstantMessaging": "Kommunikation", "Chat": "Kommunikation",
               "VideoConference": "Kommunikation", "Email": "Kommunikation",
               "IRCClient": "Kommunikation", "Telephony": "Kommunikation",
               "WebBrowser": "Browser & Internet", "FileTransfer": "Browser & Internet",
               "3DGraphics": "3D-Druck & CAD", "Engineering": "3D-Druck & CAD",
               "IDE": "Entwicklung", "Debugger": "Entwicklung", "RevisionControl": "Entwicklung",
               "HardwareSettings": "Hardware & Peripherie", "Printing": "Hardware & Peripherie",
               "Emulator": "Spiele-Launcher & Tools", "PackageManager": "Spiele-Launcher & Tools"}
_RESEARCH_LOCK = threading.Lock()


class OnlineRateLimited(Exception):
    """HTTP 429 – die Quelle bremst; für diesen Durchgang aufhören."""


def online_clean_name(name: str) -> str:
    """„arduino-1.8.19-windows“ → „arduino“; „Diablo III (x86)“ → „Diablo III“.
    Leer, wenn nichts Nachschlagbares übrig bleibt (z. B. „ZS_7805 052 430“)."""
    s = re.sub(r"\.(lnk|url|exe|msi|zip)$", "", name, flags=re.I)
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    s = re.sub(r"[_\-.]+", " ", s)
    s = re.sub(r"\b(v?\d+(\s\d+)*|x64|x86|win(32|64)?|windows|setup|installer|portable|"
               r"launcher|client|full|final|neu|new|kopie|copy)\b", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    letters = sum(ch.isalpha() for ch in s)
    return s if letters >= 3 and letters >= len(s.replace(" ", "")) * 0.6 else ""


def online_category(text: str) -> str:
    t = text.lower()
    for cat, pat in ONLINE_RULES:
        if re.search(pat, t):
            return cat
    return ""


def research_family(cat: str) -> str:
    return "gaming" if cat in GAMING else cat


def _research_norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", (s or "").casefold())


RESEARCH_MIN_COVER = 0.6         # Verknüpfungen: „Arduino IDE v2“ ≈ „Arduino IDE“ …
RESEARCH_MIN_COVER_FOLDER = 0.8  # … Ordner fast nur bei gleichem Namen („Scans“ ≠ „scanservjs“)


def research_cover(label: str, query: str) -> float:
    """1.0 = gleicher Name; sonst Anteil, wenn einer mit dem anderen beginnt; sonst 0."""
    a, b = _research_norm(label), _research_norm(query)
    if not a or not b:
        return 0.0
    short, long_ = sorted((a, b), key=len)
    return len(short) / len(long_) if long_.startswith(short) else 0.0


def research_similar(label: str, query: str) -> bool:
    """„Arduino IDE v2“ ≈ „Arduino IDE“, aber „Urlaub“ ≉ „Urlaub Kroatien“."""
    return research_cover(label, query) >= RESEARCH_MIN_COVER


def _research_best(hits: list, query: str, names) -> list:
    """[(Abdeckung, Treffer)] mit ausreichend ähnlichem Namen, genaueste zuerst (stabil).
    names(Treffer) liefert die Namen, unter denen der Treffer passen darf."""
    scored = []
    for h in hits:
        cover = max((research_cover(n, query) for n in names(h)), default=0.0)
        if cover >= RESEARCH_MIN_COVER:
            scored.append((cover, h))
    return sorted(scored, key=lambda x: -x[0])


def _online_fetch(url: str, data: dict | None = None, raw: bool = False):
    """GET (oder POST mit JSON) → JSON bzw. Text; höchstens 2 MB."""
    import urllib.request
    headers = {"User-Agent": RESEARCH_UA,
               "Accept": "application/atom+xml" if raw else "application/json"}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=8) as r:   # noqa: S310 — feste https-URLs
        text = r.read(2_000_000).decode("utf-8", "replace")
    return text if raw else json.loads(text)


def _research_disambiguation(text: str) -> bool:
    return bool(re.search(r"begriffskl|disambiguation|wikimedia-liste|wikimedia list", text, re.I))


def research_wikidata(q: str, fetch, labels: dict) -> dict | None:
    """Suche (de, dann en). Ohne verwertbare Beschreibung zusätzlich „ist ein(e)“ (P31)."""
    from urllib.parse import quote
    api = "https://www.wikidata.org/w/api.php"
    for lang in ("de", "en"):
        data = fetch(f"{api}?action=wbsearchentities&search={quote(q)}&language={lang}"
                     f"&uselang={lang}&type=item&format=json&limit=7")
        raw = [h for h in (data.get("search", []) if isinstance(data, dict) else [])
               if not _research_disambiguation(h.get("description", ""))]
        hits = _research_best(raw, q, lambda h: (h.get("label", ""),
                                                 (h.get("match") or {}).get("text", "")))
        if not hits:
            continue
        for cover, h in hits:                            # genauester Treffer mit Kategorie
            cat = online_category(h.get("description", ""))
            if cat:
                return {"source": "Wikidata", "label": h.get("label", ""),
                        "text": h.get("description", ""), "category": cat, "cover": cover,
                        "url": f"https://www.wikidata.org/wiki/{h.get('id', '')}"}
        cover, h = hits[0]
        qid, label, desc = h.get("id", ""), h.get("label", ""), h.get("description", "")
        kinds = _wikidata_kinds(qid, fetch, labels) if qid else []
        text = desc or ", ".join(kinds)
        if text:
            if kinds and desc:
                text = f"{desc} ({', '.join(kinds[:3])})"
            return {"source": "Wikidata", "label": label, "text": text, "cover": cover,
                    "category": online_category(" ".join([desc] + kinds)),
                    "url": f"https://www.wikidata.org/wiki/{qid}"}
    return None


def _wikidata_kinds(qid: str, fetch, labels: dict) -> list[str]:
    """Bezeichnungen der „ist ein(e)“-Aussagen (P31), Bezeichnungen zwischengespeichert."""
    api = "https://www.wikidata.org/w/api.php"
    data = fetch(f"{api}?action=wbgetclaims&entity={qid}&property=P31&format=json")
    ids = []
    for c in (data.get("claims", {}).get("P31", []) if isinstance(data, dict) else []):
        try:
            ids.append(c["mainsnak"]["datavalue"]["value"]["id"])
        except (KeyError, TypeError):
            continue
    missing = [i for i in ids if i not in labels][:20]
    if missing:
        d = fetch(f"{api}?action=wbgetentities&ids={'|'.join(missing)}&props=labels"
                  f"&languages=de|en&format=json")
        for i, ent in (d.get("entities", {}) if isinstance(d, dict) else {}).items():
            names = ent.get("labels", {}) if isinstance(ent, dict) else {}
            labels[i] = (names.get("de") or names.get("en") or {}).get("value", "")
    return [labels[i] for i in ids if labels.get(i)]


def research_wikipedia(q: str, fetch) -> dict | None:
    import html as _html
    from urllib.parse import quote
    for lang in ("de", "en"):
        data = fetch(f"https://{lang}.wikipedia.org/w/api.php?action=query&list=search"
                     f"&srsearch={quote(q)}&srlimit=3&format=json&srprop=snippet")
        raw = (data.get("query", {}).get("search", []) if isinstance(data, dict) else [])
        for cover, h in _research_best(raw, q, lambda h: (h.get("title", ""),)):
            title = h.get("title", "")
            snippet = _html.unescape(re.sub(r"<[^>]+>", "", h.get("snippet", "")))
            if snippet and not _research_disambiguation(snippet):
                return {"source": "Wikipedia", "label": title, "text": snippet[:160],
                        "category": online_category(snippet), "cover": cover,
                        "url": f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"}
    return None


def research_flathub(q: str, fetch) -> dict | None:
    data = fetch("https://flathub.org/api/v2/search", data={"query": q})
    raw = (data.get("hits", []) if isinstance(data, dict) else [])[:10]
    for cover, h in _research_best(raw, q, lambda h: (h.get("name", ""),))[:1]:
        name = h.get("name", "")
        main = h.get("main_categories") or []
        main = [main] if isinstance(main, str) else list(main)
        subs = list(h.get("sub_categories") or [])
        cat = next((FLATHUB_SUB[s] for s in subs if s in FLATHUB_SUB
                    and not (s == "PackageManager" and "game" not in main)), "")
        cat = cat or next((FLATHUB_MAIN[m] for m in main if m in FLATHUB_MAIN), "")
        cat = cat or online_category(h.get("summary", ""))
        kinds = "/".join(main + subs)
        return {"source": "Flathub", "label": name, "cover": cover,
                "text": f"{h.get('summary', '')} ({kinds})" if kinds else h.get("summary", ""),
                "category": cat, "url": f"https://flathub.org/apps/{h.get('app_id', '')}"}
    return None


def research_chocolatey(q: str, fetch) -> dict | None:
    import xml.etree.ElementTree as ET
    from urllib.parse import quote
    term = quote(q.replace("'", "''"))
    text = fetch("https://community.chocolatey.org/api/v2/Search()?searchTerm="
                 f"%27{term}%27&targetFramework=%27%27&includePrerelease=false&$top=8", raw=True)
    ns = {"a": "http://www.w3.org/2005/Atom",
          "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
          "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"}
    seen, pkgs = set(), []
    for e in ET.fromstring(text).findall("a:entry", ns):
        pid = (e.findtext("a:title", "", ns) or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        props = e.find("m:properties", ns)
        title = (props.findtext("d:Title", "", ns) if props is not None else "") or pid
        title = re.sub(r"\s*\((install|portable)\)\s*$|^\[deprecated\]\s*", "", title.strip(),
                       flags=re.I)
        summary = (e.findtext("a:summary", "", ns) or "").strip()
        tags = (props.findtext("d:Tags", "", ns) if props is not None else "") or ""
        # „lghub“ heißt im Text „Logitech G HUB gives you …“ → Textanfang zählt auch
        lead = summary[:len(q) + 2]
        pkgs.append({"pid": pid, "title": title, "summary": summary, "tags": tags, "lead": lead})
    for cover, k in _research_best(pkgs, q, lambda k: (k["title"], k["pid"], k["lead"]))[:1]:
        return {"source": "Chocolatey", "label": k["title"], "cover": cover,
                "text": (k["summary"] or k["tags"])[:160],
                "category": online_category(f"{k['summary']} {k['tags']}"),
                "url": f"https://community.chocolatey.org/packages/{k['pid']}"}
    return None


def research_targets(items: list[Item]) -> list[Item]:
    """Was geprüft wird: Verknüpfungen, Programme (.exe/.msi) und Ordner – Unklares zuerst.
    Dokumente nie (deren Namen sind oft persönlich), eigene Entscheidungen nicht."""
    out = []
    for it in items:
        if it.confidence == "user" or (it.confidence == "keep" and it.role not in RESEARCH_LEAVE):
            continue
        if (it.kind in ("lnk", "url", "folder")
                or (it.kind == "file" and it.path.suffix.lower() in RESEARCH_EXE)) \
                and online_clean_name(it.label):
            out.append(it)
    out.sort(key=lambda i: i.confidence != "low")
    return out[:RESEARCH_MAX]


def research_combine(evidence: list[dict]) -> dict:
    """Quellen abstimmen lassen. confirmed = Zahl der Quellen, die (bis auf Spiel/Launcher)
    dasselbe sagen; dissent = Quellen mit anderer Meinung."""
    votes = [e for e in evidence if e.get("category")]
    res = {"category": "", "confirmed": 0, "agree": [], "dissent": [], "evidence": evidence}
    if not votes:
        return res
    count: dict[str, int] = {}
    for e in votes:
        count[e["category"]] = count.get(e["category"], 0) + 1
    order = [e["category"] for e in votes]
    best = max(count, key=lambda c: (count[c], -order.index(c)))
    fam = research_family(best)
    res.update(category=best,
               agree=[e["source"] for e in votes if research_family(e["category"]) == fam],
               dissent=[f"{e['source']}: {e['category']}" for e in votes
                        if research_family(e["category"]) != fam])
    res["confirmed"] = len(res["agree"])
    return res


def research_lines(res: dict) -> list[str]:
    out = []
    for e in res.get("evidence") or []:
        cat = f" → {e['category']}" if e.get("category") else ""
        out.append(f"{e['source']}: {e['label']} – {e['text']}{cat}")
    return out


def research_apply(it: Item, res: dict | None, alias=None) -> str:
    """Ergebnis gegen die lokale Zuordnung prüfen. Rückgabe: confirmed | corrected |
    conflict | hint | info | found | none. Eigene Entscheidungen und eigene Regeln gewinnen
    immer; Programm-/Projektordner bleiben liegen."""
    if not res or not res.get("evidence"):
        return "none"
    first = res["evidence"][0]
    info = f"{first['source']}: {first['label']} – {first['text']}"
    cat, n = res.get("category", ""), res.get("confirmed", 0)
    src = ", ".join(res.get("agree", []))

    def note(text):
        if "Recherche" not in it.reason and "✓ geprüft" not in it.reason:
            it.reason = f"{it.reason} · {text}"

    if it.kind == "folder" and cat and res.get("binaries") and it.role not in RESEARCH_LEAVE \
            and it.confidence not in ("user", "keep"):
        it.role, it.category, it.confidence = "program", "Sonstiges", "keep"
        it.reason = f"Programmordner (enthält {res['binaries']}) – bleibt liegen · Recherche: {info}"
        return "corrected"
    if it.role in RESEARCH_LEAVE or it.confidence in ("user", "keep"):
        note(f"Recherche: {info}")
        return "info"
    if not cat:
        if it.confidence == "low":
            it.reason = f"Recherche: {info} (keine passende Gruppe)"
        else:
            note(f"Recherche: {info}")
        return "found"
    target = alias(cat) if alias else cat
    local = it.category if it.confidence in ("high", "medium") else ""
    if local and (research_family(local) == research_family(cat) or local == target):
        note(f"✓ geprüft ({src})")
        if n >= 2 and it.confidence == "medium":
            it.confidence = "high"
        return "confirmed"
    if local:
        authoritative = it.reason.startswith("eigene Regel") or it.reason.startswith("Inhalt:")
        if n >= 2 and not authoritative:
            it.reason = (f"⚠ Bitte prüfen: hier „{local}“ ({it.reason}), "
                         f"Recherche sagt „{cat}“ ({src})")
            it.category, it.confidence = target, "low"
            return "conflict"
        note(f"Recherche-Hinweis: {src} meint „{cat}“")
        return "hint"
    it.category = target
    it.confidence = "high" if n >= 2 else "medium"
    it.reason = f"Recherche: {info} ({src})"
    return "corrected"


RESEARCH_FUNCS = {"wikidata": research_wikidata, "wikipedia": research_wikipedia,
                  "flathub": research_flathub, "chocolatey": research_chocolatey}


def research_run(items: list[Item], cache: dict, fetch=None, sources=None, progress=None,
                 stop=lambda: False, force: bool = False, pauses: dict | None = None):
    """Alle Namen in allen Quellen nachschlagen – je Server ein eigener Strang mit
    Pause zwischen den Anfragen, Wikipedia nur, wenn Wikidata nichts findet.
    → ({Pfad: Ergebnis}, {Quelle: "ok" | "limit" | "offline"})."""
    from urllib.error import HTTPError
    fetch = fetch or _online_fetch
    sources = [s for s in (sources or list(RESEARCH_SOURCES)) if s in RESEARCH_FUNCS]
    queries: dict[str, str] = {}
    for it in items:
        q = online_clean_name(it.label)
        if q:
            queries.setdefault(q.casefold(), q)
    lanes = [[s for s in ("wikidata", "wikipedia") if s in sources]]
    lanes += [[s] for s in ("flathub", "chocolatey") if s in sources]
    lanes = [lane for lane in lanes if lane]
    evidence: dict[str, dict] = {k: {} for k in queries}
    status = {s: "ok" for s in sources}
    labels = cache.setdefault("_labels", {})
    total, done = max(1, len(queries) * len(lanes)), [0]
    lock = threading.Lock()
    now = time.time()

    def fresh(entry) -> bool:
        days = RESEARCH_CACHE_DAYS if entry.get("ev") else RESEARCH_MISS_DAYS
        return not force and now - entry.get("t", 0) < days * 86400

    def lane_worker(lane):
        pause = min((pauses or {}).get(s, RESEARCH_SOURCES[s][2]) for s in lane)
        last = [0.0]

        def throttled(url, data=None, raw=False):
            wait = last[0] + pause - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                return fetch(url, data=data, raw=raw)
            finally:
                last[0] = time.time()

        fails = 0
        for key, q in queries.items():
            if stop():
                return
            for src in lane:
                if status[src] != "ok":
                    continue
                if src == "wikipedia" and evidence[key].get("wikidata"):
                    continue
                entry = cache.setdefault(src, {}).get(key)
                if entry and fresh(entry):
                    evidence[key][src] = entry.get("ev")
                    continue
                try:
                    if src == "wikidata":
                        ev = research_wikidata(q, throttled, labels)
                    else:
                        ev = RESEARCH_FUNCS[src](q, throttled)
                    fails = 0
                except HTTPError as e:
                    if e.code == 429:
                        status[src] = "limit"
                        continue
                    if e.code != 404:                   # 404 = einfach nichts gefunden
                        fails += 1
                        if fails >= 2:
                            status[src] = "offline"
                        continue
                    ev = None
                except Exception:                       # noqa: BLE001 — Netz, Format, XML
                    fails += 1
                    if fails >= 2:
                        status[src] = "offline"
                    continue
                cache[src][key] = {"t": time.time(), "ev": ev}
                evidence[key][src] = ev
            with lock:
                done[0] += 1
                if progress:
                    progress(done[0], total, q)

    threads = [threading.Thread(target=lane_worker, args=(lane,), daemon=True) for lane in lanes]
    for t in threads:
        t.start()
    for t in threads:
        while t.is_alive():
            t.join(0.2)
            if stop():
                break
    results = {}
    for it in items:
        q = online_clean_name(it.label)
        if not q:
            continue
        need = RESEARCH_MIN_COVER_FOLDER if it.kind == "folder" else RESEARCH_MIN_COVER
        ev = [e for s in sources if (e := evidence.get(q.casefold(), {}).get(s))
              and e.get("cover", 1.0) >= need]
        res = research_combine(ev)
        if it.kind == "folder" and res["category"]:
            res["binaries"] = folder_has_binaries(it.path)
        results[str(it.path)] = res
    return results, status


def research_status_text(status: dict) -> str:
    bad = {"limit": "bremst gerade", "offline": "nicht erreichbar"}
    return " · ".join(f"{RESEARCH_SOURCES[s][0]} {bad[v]}" for s, v in status.items() if v in bad)


def research_cache_load() -> dict:
    try:
        data = json.loads(RESEARCH_CACHE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def research_cache_save(cache: dict) -> None:
    """Mit dem Stand auf der Platte zusammenführen (Hauptfenster und Boxen schreiben beide)."""
    with _RESEARCH_LOCK:
        disk = research_cache_load()
        for k, v in cache.items():
            if isinstance(v, dict):
                disk.setdefault(k, {}).update(v)
        limit = time.time() - RESEARCH_CACHE_DAYS * 86400
        for k, v in disk.items():
            if k != "_labels" and isinstance(v, dict):
                disk[k] = {q: e for q, e in v.items()
                           if isinstance(e, dict) and e.get("t", 0) > limit}
        try:
            RESEARCH_CACHE.parent.mkdir(exist_ok=True)
            RESEARCH_CACHE.write_text(json.dumps(disk, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def research_settings(cfg: dict | None) -> tuple[bool, list[str]]:
    r = (cfg or {}).get("research") or {}
    srcs = [s for s in r.get("sources", list(RESEARCH_SOURCES)) if s in RESEARCH_SOURCES]
    return bool(r.get("on", True)), srcs


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
# ORDNER-ASSISTENT – Analyse-Engine (zweisprachig DE/EN)
# ===========================================================================
# Erkennt Wichtiges (Lebenslauf, Zeugnisse, Verträge, Steuer, Fotos …), Duplikate,
# alte Versionen und Müll und schlägt eine Ordnerstruktur vor. Nur Standardbibliothek.
# Quellen: dev/organizer/org_*.py – nicht hier bearbeiten, sondern dort ändern und
# `python dev/assemble.py` ausführen.

# ----- org_core --------------------------------------------------------------
# ===========================================================================
# ORDNER-ASSISTENT – gemeinsame Grundlage (Datenmodell, Kategorien, Regeln)
# ===========================================================================
# Alle Namen dieses Teils beginnen mit org_ / Org / ORG_, damit sie im
# Einzeldatei-Programm DesktopSorter.pyw nicht mit bestehenden Namen kollidieren.
# Nur Standardbibliothek.

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------- Stufen (wie wertvoll ist eine Datei?) ----------
ORG_PROTECT = "protect"   # wichtig, dauerhaft behalten – nie zum Löschen vorschlagen
ORG_KEEP = "keep"         # behalten, nur einsortieren
ORG_REVIEW = "review"     # Nutzer soll entscheiden
ORG_JUNK = "junk"         # Löschvorschlag (Papierkorb) – nur nach Bestätigung
ORG_TIERS = (ORG_PROTECT, ORG_KEEP, ORG_REVIEW, ORG_JUNK)
ORG_TIER_LABELS = {ORG_PROTECT: "Wichtig", ORG_KEEP: "Behalten",
                   ORG_REVIEW: "Prüfen", ORG_JUNK: "Aufräumen"}

# ---------- Dateiarten nach Endung ----------
ORG_KIND_EXT: dict[str, set[str]] = {
    "document": {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".pages",
                 ".xls", ".xlsx", ".ods", ".csv", ".numbers", ".ppt", ".pptx", ".odp",
                 ".key", ".epub", ".xps", ".oxps", ".eml", ".msg", ".tex"},
    "image": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff",
              ".heic", ".heif", ".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf",
              ".rw2", ".svg", ".psd", ".xcf"},
    "video": {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".wmv", ".webm", ".mts", ".m2ts",
              ".3gp", ".mpg", ".mpeg"},
    "audio": {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma", ".aiff",
              ".aif", ".amr"},
    "archive": {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst"},
    "installer": {".exe", ".msi", ".msix", ".msixbundle", ".appx", ".appxbundle", ".iso",
                  ".img", ".dmg", ".pkg", ".apk"},
    "code": {".py", ".ipynb", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".cs", ".go",
             ".rs", ".r", ".jl", ".m", ".sh", ".ps1", ".bat", ".sql", ".html", ".css",
             ".json", ".yaml", ".yml", ".toml", ".xml"},
    "cad": {".dwg", ".dxf", ".stl", ".3mf", ".step", ".stp", ".f3d", ".obj", ".fbx",
            ".blend", ".skp", ".gcode", ".iges", ".igs"},
    "science": {".fasta", ".fa", ".fastq", ".fq", ".gb", ".gbk", ".dna", ".ab1", ".sbol",
                ".pdb", ".cif", ".bam", ".sam", ".vcf", ".bed", ".gff", ".gtf", ".h5ad",
                ".fcs", ".czi", ".nd2", ".lif", ".prism", ".pzfx", ".h5", ".hdf5"},
}


def org_kind(path: Path) -> str:
    """Grobe Dateiart: document|image|video|audio|archive|installer|code|cad|science|other."""
    ext = path.suffix.lower()
    for kind, exts in ORG_KIND_EXT.items():
        if ext in exts:
            return kind
    return "other"


# ---------- Kategorien: Zielordner + Stufe ----------
# folder darf {year} (Jahr aus Dokument/EXIF/Änderungsdatum) und {event}
# (Foto-Ereignis, z. B. "2024-07 Juli") enthalten. "" = bleibt, wo es ist.
# Ordnernamen bewusst deutsch; die Erkennung ist zweisprachig (DE + EN).
ORG_CATEGORIES: dict[str, dict] = {
    # --- wichtige Dokumente (dauerhaft behalten) ---
    "cv":          {"label": "Lebenslauf & Bewerbung", "folder": "Dokumente/Bewerbung", "tier": ORG_PROTECT},
    "certificate": {"label": "Zeugnisse & Urkunden", "folder": "Dokumente/Zeugnisse & Urkunden", "tier": ORG_PROTECT},
    "id":          {"label": "Ausweise & Identität", "folder": "Dokumente/Ausweise", "tier": ORG_PROTECT},
    "contract":    {"label": "Verträge", "folder": "Dokumente/Verträge", "tier": ORG_PROTECT},
    "insurance":   {"label": "Versicherungen", "folder": "Dokumente/Versicherungen", "tier": ORG_PROTECT},
    "tax":         {"label": "Steuer", "folder": "Dokumente/Steuer/{year}", "tier": ORG_PROTECT},
    "payslip":     {"label": "Gehalt & Rente", "folder": "Dokumente/Gehalt & Rente/{year}", "tier": ORG_PROTECT},
    "health":      {"label": "Gesundheit", "folder": "Dokumente/Gesundheit", "tier": ORG_PROTECT},
    "property":    {"label": "Wohnen & Eigentum", "folder": "Dokumente/Wohnen", "tier": ORG_PROTECT},
    # --- Alltagsdokumente (behalten, einsortieren) ---
    "invoice":     {"label": "Rechnungen & Belege", "folder": "Dokumente/Rechnungen/{year}", "tier": ORG_KEEP},
    "bank":        {"label": "Bank & Finanzen", "folder": "Dokumente/Finanzen/{year}", "tier": ORG_KEEP},
    "vehicle":     {"label": "Fahrzeug", "folder": "Dokumente/Fahrzeug", "tier": ORG_KEEP},
    "education":   {"label": "Studium & Schule", "folder": "Dokumente/Studium & Schule", "tier": ORG_KEEP},
    "work":        {"label": "Arbeit", "folder": "Dokumente/Arbeit", "tier": ORG_KEEP},
    "science":     {"label": "Wissenschaft", "folder": "Dokumente/Wissenschaft", "tier": ORG_KEEP},
    "travel":      {"label": "Reisen & Tickets", "folder": "Dokumente/Reisen", "tier": ORG_REVIEW},
    "manual":      {"label": "Anleitungen", "folder": "Dokumente/Anleitungen", "tier": ORG_REVIEW},
    "document":    {"label": "Sonstige Dokumente", "folder": "Dokumente/Sonstiges", "tier": ORG_REVIEW},
    # --- Medien ---
    "photo_personal": {"label": "Persönliche Fotos", "folder": "Bilder/Fotos/{year}/{event}", "tier": ORG_PROTECT},
    "photo_chat":  {"label": "Chat-Bilder", "folder": "Bilder/Chats", "tier": ORG_REVIEW},
    "screenshot":  {"label": "Bildschirmfotos", "folder": "Bilder/Bildschirmfotos", "tier": ORG_REVIEW},
    "image":       {"label": "Bilder", "folder": "Bilder/Sonstige", "tier": ORG_KEEP},
    "video_personal": {"label": "Persönliche Videos", "folder": "Videos/{year}", "tier": ORG_PROTECT},
    "video":       {"label": "Videos", "folder": "Videos", "tier": ORG_KEEP},
    "audio":       {"label": "Musik & Audio", "folder": "Musik", "tier": ORG_KEEP},
    # --- Technik ---
    "archive":     {"label": "Archive", "folder": "Archive", "tier": ORG_REVIEW},
    "installer":   {"label": "Installationsdateien", "folder": "Programme & Installer", "tier": ORG_REVIEW},
    "code":        {"label": "Code & Projekte", "folder": "Projekte", "tier": ORG_KEEP},
    "cad":         {"label": "CAD & 3D", "folder": "Projekte/CAD & 3D", "tier": ORG_KEEP},
    "science_data": {"label": "Labordaten", "folder": "Dokumente/Wissenschaft/Daten", "tier": ORG_KEEP},
    # --- Aufräumen ---
    "temp":        {"label": "Temporär & Reste", "folder": "", "tier": ORG_JUNK},
    "duplicate":   {"label": "Duplikate", "folder": "", "tier": ORG_JUNK},
    "other":       {"label": "Sonstiges", "folder": "", "tier": ORG_REVIEW},
}

# ---------- Sicherheit: was nie angefasst / nie betreten wird ----------
# Ordner mit diesen Namen werden nicht durchsucht (System, Programme, Caches).
ORG_SKIP_DIRS = {
    "$recycle.bin", "system volume information", "windows", "program files",
    "program files (x86)", "programdata", "appdata", "node_modules", ".git", ".svn",
    ".hg", "venv", ".venv", "env", "__pycache__", ".idea", ".vscode", ".cache",
    "site-packages", "dist-packages", ".tox", ".mypy_cache", ".pytest_cache",
    "onedrive temp", "desktopsorter boxen",
}
# Enthält ein Ordner eine dieser Dateien/Unterordner, ist er ein Projekt und wird als
# Einheit behandelt: nichts darin wird verschoben, umbenannt oder gelöscht.
# Starke Marker machen einen Ordner allein zum Projekt. Schwache Marker (eine einzelne
# requirements.txt oder package.json landet schnell mal im Downloads-Ordner) zählen nur,
# wenn daneben echter Quellcode liegt und der Ordner kein typischer Benutzerordner ist.
ORG_PROJECT_STRONG = {
    ".git", ".hg", ".svn", "cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "cmakelists.txt", ".sln", ".csproj", ".vcxproj", "composer.json", "gemfile", ".project",
    ".idea", "renv.lock", "snakefile", "nextflow.config", ".obsidian",
}
ORG_PROJECT_WEAK = {"package.json", "pyproject.toml", "setup.py", "requirements.txt",
                    "makefile", "environment.yml"}
ORG_PROJECT_MARKERS = ORG_PROJECT_STRONG | ORG_PROJECT_WEAK
ORG_SOURCE_EXT = {".py", ".ipynb", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".cs", ".go",
                  ".rs", ".r", ".jl", ".m", ".sh", ".ps1"}
ORG_USER_DIR_NAMES = {"downloads", "download", "desktop", "documents", "dokumente",
                      "eigene dateien", "pictures", "bilder", "music", "musik", "videos",
                      "onedrive", "schreibtisch"}
ORG_MAX_FILES = 60_000        # Obergrenze je Analyse (wird im UI gemeldet)


def org_is_project_dir(d: Path) -> bool:
    try:
        names = {p.name.lower() for p in d.iterdir()}
    except OSError:
        return False
    if names & ORG_PROJECT_STRONG:
        return True
    if any(n.endswith((".sln", ".csproj", ".vcxproj", ".xcodeproj")) for n in names):
        return True
    if names & ORG_PROJECT_WEAK and d.name.lower() not in ORG_USER_DIR_NAMES:
        return any(os.path.splitext(n)[1] in ORG_SOURCE_EXT for n in names)
    return False


# ---------- Datensatz je Datei ----------
@dataclass
class OrgFile:
    path: Path                     # absoluter Pfad
    rel: str                       # Pfad relativ zum analysierten Ordner (mit "/")
    size: int
    mtime: float
    kind: str                      # siehe org_kind()
    ext: str                       # Endung klein, mit Punkt
    depth: int = 0                 # 0 = liegt direkt im analysierten Ordner
    text: str = ""                 # extrahierter Textanfang (max. ~4000 Zeichen)
    meta: dict = field(default_factory=dict)   # z. B. exif: datetime/make/model/gps, pages
    category: str = ""             # Schlüssel aus ORG_CATEGORIES
    tier: str = ""                 # ORG_PROTECT | ORG_KEEP | ORG_REVIEW | ORG_JUNK
    score: float = 0.0             # Sicherheit der Einordnung 0..1
    reasons: list = field(default_factory=list)  # kurze deutsche Begründungen
    year: int | None = None        # Bezugsjahr (Dokumentdatum, EXIF, sonst mtime)
    event: str = ""                # Foto-Ereignis, z. B. "2024-07 Juli – Reise"
    dup_of: str = ""               # rel des Originals, wenn exaktes Duplikat
    version_group: str = ""        # Schlüssel einer Versionsgruppe
    version_latest: bool = True    # neueste Version innerhalb der Gruppe?
    target: str = ""               # vorgeschlagener Zielordner relativ zum Wurzelordner
    in_project: bool = False       # liegt in einem Projektordner → nie anfassen
    cloud: bool = False            # OneDrive & Co. „nur online“: nie lesen (würde Download auslösen)

    @property
    def name(self) -> str:
        return self.path.name

    def add_reason(self, text: str) -> None:
        if text and text not in self.reasons:
            self.reasons.append(text)


def org_year_of(f: OrgFile) -> int:
    """Bezugsjahr: f.year, sonst EXIF-Datum, sonst Änderungsdatum."""
    if f.year:
        return f.year
    dt = (f.meta.get("exif") or {}).get("datetime", "")
    if dt[:4].isdigit():
        return int(dt[:4])
    return time.localtime(f.mtime).tm_year


def org_rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def org_walk(root: Path, recursive: bool = True, limit: int = ORG_MAX_FILES,
             cancelled=lambda: False) -> tuple[list[OrgFile], list[str], bool]:
    """Durchläuft root und liefert (Dateien, Projektordner-rel, abgeschnitten?).
    Überspringt System-/Cache-Ordner, versteckte Dateien und Links; Projektordner
    werden erfasst (für Duplikat-Schutz), ihre Dateien aber als in_project markiert."""
    files: list[OrgFile] = []
    projects: list[str] = []
    truncated = False
    root = root.resolve()
    stack: list[tuple[Path, int, bool]] = [(root, 0, False)]
    while stack:
        if cancelled():
            break
        d, depth, in_proj = stack.pop()
        if d != root and not in_proj and org_is_project_dir(d):
            in_proj = True
            projects.append(org_rel(d, root))
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            name_l = e.name.lower()
            try:
                if e.is_symlink():
                    continue
                if _org_hidden(e):
                    continue
                if e.is_dir(follow_symlinks=False):
                    if name_l in ORG_SKIP_DIRS or name_l.startswith("."):
                        continue
                    if _org_is_link_dir(e):          # Junction/Link: zeigt evtl. woandershin
                        continue
                    if recursive:
                        stack.append((Path(e.path), depth + 1, in_proj))
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if name_l == "desktop.ini":          # Ordner-Einstellungen: nie anfassen
                continue
            p = Path(e.path)
            attrs = getattr(st, "st_file_attributes", 0)
            files.append(OrgFile(path=p, rel=org_rel(p, root), size=st.st_size,
                                 mtime=st.st_mtime, kind=org_kind(p), ext=p.suffix.lower(),
                                 depth=depth, in_project=in_proj,
                                 cloud=bool(attrs & ORG_CLOUD_ATTRS)))
            if len(files) >= limit:
                truncated = True
                return files, projects, truncated
    return files, projects, truncated


def _org_hidden(entry: os.DirEntry) -> bool:
    """Versteckt/System (Windows-Attribute). Thumbs.db & .DS_Store bleiben sichtbar,
    damit sie als Reste erkannt werden können."""
    if entry.name.lower() in ("thumbs.db", ".ds_store"):
        return False
    try:
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attrs & 0x2 or attrs & 0x4)       # HIDDEN | SYSTEM


# FILE_ATTRIBUTE_OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS: Datei liegt nur in der Cloud
ORG_CLOUD_ATTRS = 0x1000 | 0x40000 | 0x400000
_ORG_LINK_TAGS = (0xA0000003, 0xA000000C)      # IO_REPARSE_TAG_MOUNT_POINT, _SYMLINK


def _org_is_link_dir(entry: os.DirEntry) -> bool:
    """Junction oder Verzeichnis-Link (Windows). OneDrive-Platzhalter (andere Tags) zählen nicht."""
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return True
    return getattr(st, "st_reparse_tag", 0) in _ORG_LINK_TAGS


# ----- org_lexicon -----------------------------------------------------------
# ===========================================================================
# ORDNER-ASSISTENT – zweisprachiger Dokument-Klassifikator (Deutsch + Englisch)
# ===========================================================================
# Erkennt anhand von Dateiname UND extrahiertem Text, was ein Dokument ist
# (Lebenslauf, Zeugnis, Ausweis, Vertrag, Steuer, Gehalt, ...), und das
# Bezugsjahr. Nur Standardbibliothek; alle Namen beginnen mit org_/Org/ORG_.
#
# Grundidee:
#   * Alle Begriffe stehen in EINEM vorkompilierten Trie-Regex. Die Suche ist
#     "links-längste": an jeder Stelle gewinnt der längste Begriff und
#     verbraucht die Zeichen. Dadurch zählt z. B. in "Gehaltsabrechnung" nur
#     der Gesamtbegriff (Gehalt), nicht das enthaltene "rechnung" (Rechnung).
#     Sperrbegriffe (Kategorie "") nutzen das gezielt: "abrechnung",
#     "berechnung", "erzeugnis", "steuerung" ... verbrauchen Text ohne Wertung.
#   * Modi: "s" = Teilwort (deutsche Komposita: "zeugnis" trifft
#     Arbeitszeugnis), "p" = Wortanfang, "w" = ganzes Wort (kurze Kürzel wie
#     cv, id, nda, w2 – kein "cv" in "cvs", kein "id" in "video").
#   * Dateiname zählt 3-fach, Text 1-fach. Text braucht mind. 2 verschiedene
#     Treffer je Kategorie oder einen sehr starken Begriff (Gewicht >= 3),
#     außer der Dateiname stützt dieselbe Kategorie schon.
#
# Bewusste Entscheidungen:
#   * Garantie/Gewährleistung/Garantieschein -> "invoice" (gehört zum Kaufbeleg,
#     der für Garantiefälle aufbewahrt wird).
#   * Kaufvertrag Auto -> "contract" (Vertrag ist geschützt und wiegt schwerer).
#   * Geburts-/Heirats-/Sterbeurkunde -> "certificate" (Ordner "Zeugnisse & Urkunden").
#   * Führerschein -> "id"; Zulassungsbescheinigung/Fahrzeugschein -> "vehicle".
#   * Lohnsteuerbescheinigung, Elster, Spendenbescheinigung -> "tax";
#     Renteninformation/Rentenbescheid -> "payslip" (Ordner "Gehalt & Rente").
#   * Transcript of Records / ToR -> "certificate"; Notenspiegel -> "education".
#   * Versicherungsvertrag/-police -> "insurance" (nicht "contract").
#   * Nebenkosten-/Strom-/Gasabrechnung -> "property" (Wohnen).
#   * "Rezept" zählt nur mit medizinischem Kontext (Arzt, Praxis, Medikament,
#     Apotheke, mg, PZN ...) als "health" – sonst ist es ein Kochrezept.
#   * Bilder/Videos/Audio: nur starke Begriffe im Namen (Gewicht >= 2), damit
#     "Urlaub.jpg" oder "Auto.jpg" kein Dokument wird, "Personalausweis.jpg" aber schon.
#     Situationswörter (Krankenhaus, Klinik, Ultraschall, Arzt, Police ...) und
#     Ereigniswörter (Feier, Hochzeit, Urlaub, Baby ...) machen aus einem persönlichen
#     Foto kein Dokument (sonst landet das Neugeborenen-Foto im Ordner "Gesundheit").
#   * Chat-Export: Name "WhatsApp Chat mit ..." -> None. Sieht nur der TEXT wie ein Chat
#     aus (oder steht bloß "WhatsApp" im Namen), zählt der Text nicht, der Dateiname
#     aber weiterhin ("Lebenslauf per WhatsApp.pdf" bleibt Lebenslauf).
#   * Getrennt geschriebene Komposita im Namen ("Gehalts-Abrechnung", "Lebens Lauf")
#     werden zusätzlich zusammengezogen geprüft.

import math
import os
import re
import time
import unicodedata


# Kategorien, die dieses Modul vergeben kann (alle sind Schlüssel von ORG_CATEGORIES)
ORG_LEX_CATEGORIES = ("cv", "certificate", "id", "contract", "insurance", "tax", "payslip",
                      "health", "property", "invoice", "bank", "vehicle", "education",
                      "work", "science", "travel", "manual")

ORG_LEX_NAME_FACTOR = 3.0      # Dateiname zählt dreifach
ORG_LEX_MIN_SCORE = 2.0        # Mindest-Evidenz für eine Einordnung
ORG_LEX_MIN_TEXT = 3.0         # Mindest-Evidenz, wenn nur der Text (nicht der Name) belegt
ORG_LEX_STRONG = 3.0           # ein Begriff ab diesem Gewicht reicht im Text allein
ORG_LEX_TEXT_CAP = 8.5         # Obergrenze Text-Evidenz je Kategorie (< starker Namensbegriff 9)
ORG_LEX_HEAD = 160             # erste 3 Zeilen, max. so viele Zeichen = Überschrift
ORG_LEX_MAX_TEXT = 6000        # so viele Zeichen Text werden ausgewertet
ORG_LEX_MEDIA_MIN = 2.0        # Bilder/Videos: nur starke Namensbegriffe
_ORG_MEDIA_KINDS = ("image", "video", "audio")
_ORG_SKIP_KINDS = ("installer", "code", "cad", "science", "science_data")
# Bilder/Videos: diese Begriffe beschreiben eher die Situation eines persönlichen Fotos
# ("Lea Krankenhaus erster Tag.jpg", "Ultraschall Baby.jpg", "Police car.jpg") als ein
# abfotografiertes Dokument -> bei Medien nicht werten.
_ORG_MEDIA_DENY = frozenset((
    "police", "policen", "arzt", "aerztin", "arztin", "krankenhaus", "klinik", "hospital",
    "therapie", "ultraschall", "schwangerschaft", "krankheit", "erkrankung", "reha",
    "rehabilitation", "krankengymnastik", "impfung", "impf", "vaccination", "vaccine", "sehtest",
    "hoertest", "hortest", "blutdruck", "zahnbehandlung", "tuev", "tuv", "dekra"))
# Bilder/Videos mit Ereignis-Wörtern sind persönliche Fotos, keine Dokument-Scans
# ("Lea Krankenhaus erster Tag.jpg", "Zeugnisübergabe Abschlussfeier.jpg")
_ORG_MEDIA_EVENT_RE = re.compile(
    r"(?:feier|party|hochzeit|wedding|urlaub|ferien|geburtstag|birthday|baby|taufe|einschulung|"
    r"abiball|ausflug|familie(?!n ?stammbuch|n ?kasse)|family|vacation|holiday|weihnacht|christmas|silvester|selfie|"
    r"strand|beach|erster tag|first day|geburt (?!urkunde)|newborn|neugeboren)")

# ---------------------------------------------------------------------------
# Normalisierung
# ---------------------------------------------------------------------------
_ORG_PLAIN_TABLE = {ord("ä"): "a", ord("ö"): "o", ord("ü"): "u", ord("ß"): "ss"}
# unsichtbare Zeichen (weiches Trennzeichen aus PDFs, Nullbreite-Zeichen, BOM) -> entfernen
_ORG_INVISIBLE_RE = re.compile("[\u00ad\u200b\u200c\u200d\u2060\ufeff]+")
_ORG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_ORG_CAMEL_RE = re.compile(r"(?<=[a-zäöüß])(?=[A-ZÄÖÜ][a-zäöüß])")
_ORG_COMBINING_RE = re.compile("[̀-ͯ᪰-᫿᷀-᷿⃐-⃿︠-︯]+")
# Silbentrennung am Zeilenende im Text: "Arbeits-\nzeugnis" -> "Arbeitszeugnis"
_ORG_HYPHEN_BREAK_RE = re.compile(r"(?<=[a-zäöüß])-[ \t]*\r?\n[ \t]*(?=[a-zäöüß])")


def _org_fold(s: str) -> str:
    """Kleinschreibung + Umlaute -> ae/oe/ue/ss + übrige Akzente entfernen (Satzzeichen bleiben)."""
    s = s.lower()
    if s.isascii():
        return s
    s = unicodedata.normalize("NFC", _ORG_INVISIBLE_RE.sub("", s))
    # str.replace ist deutlich schneller als translate() mit Mehrzeichen-Ersetzungen
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    if "ẞ" in s:
        s = s.replace("ẞ", "ss")
    if not s.isascii():
        s = _ORG_COMBINING_RE.sub("", unicodedata.normalize("NFKD", s)).lower()
    return s


def _org_to_str(v) -> str:
    """Eingabe tolerant in Text wandeln (Path, bytes, None ...)."""
    if isinstance(v, str):
        return v
    if v is None:
        return ""
    if isinstance(v, (bytes, bytearray)):
        try:
            return os.fsdecode(bytes(v))
        except Exception:
            return ""
    try:
        return os.fspath(v) if hasattr(v, "__fspath__") else str(v)
    except Exception:
        return ""


def org_normalize(s: str) -> str:
    """Kleinschreibung, ä->ae ö->oe ü->ue ß->ss, Trennzeichen (_ - . + / usw.) -> Leerzeichen,
    Leerraum zusammengefasst. Alle Lexikon-Muster sind gegen diese Form geschrieben."""
    try:
        if not isinstance(s, str):
            s = _org_to_str(s)
        return _ORG_NON_ALNUM_RE.sub(" ", _org_fold(s)).strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Lexikon: (Kategorie, Modus, Namensgewicht, "begriff, begriff, ...", [Textgewicht])
# Kategorie "" = Sperrbegriff (verbraucht Text, zählt nichts).
# Begriffe mit echten Umlauten schreiben: daraus entstehen automatisch die
# Formen "ae" (Fuehrerschein) und – ab 5 Zeichen – "a" (Fuhrerschein).
# ---------------------------------------------------------------------------
_ORG_LEX_SRC = (
    # ----- Lebenslauf & Bewerbung -----
    ("cv", "s", 3, "lebenslauf, lebensläufe, curriculum vitae, bewerbungsunterlagen, bewerbungsmappe, "
                   "bewerbungsschreiben, motivationsschreiben, initiativbewerbung, bewerbungsfoto"),
    ("cv", "s", 2.5, "bewerbung, anschreiben, cover letter, covering letter, motivation letter, "
                     "letter of motivation, application letter, job application, letter of application"),
    ("cv", "w", 2.5, "cv, resume, resumes, résumé"),
    # typische erste Sätze eines Anschreibens (reichen allein im Text)
    ("cv", "s", 3, "hiermit bewerbe ich mich, bewerbe mich hiermit, bewerbe ich mich um, "
                   "i am writing to apply, i would like to apply for, i am applying for"),
    ("cv", "s", 2, "bewerbe, vorstellungsgespräch, stellenanzeige, stellenausschreibung, stellengesuch, "
                   "personal statement, bewerber"),
    ("cv", "s", 1, "berufserfahrung, berufliche erfahrung, persönliche daten, sprachkenntnisse, "
                   "edv kenntnisse, it kenntnisse, computerkenntnisse, work experience, "
                   "professional experience, beruflicher werdegang, werdegang, schulbildung, "
                   "key skills, technical skills, hobbys, hobbies, referenzen auf anfrage, "
                   "references available, kernkompetenzen, qualifikationsprofil"),
    ("cv", "w", 1, "linkedin, xing, skills"),

    # ----- Zeugnisse & Urkunden -----
    ("certificate", "s", 3, "zeugnis, zeugnisse, urkunde, urkunden, arbeitszeugnis, zwischenzeugnis, "
                            "abschlusszeugnis, abiturzeugnis, reifezeugnis, meisterbrief, gesellenbrief, "
                            "vollsten zufriedenheit, letter of recommendation, recommendation letter, "
                            "reference letter, letter of reference, transcript of records, "
                            "employment certificate, certificate of employment, approbationsurkunde"),
    ("certificate", "s", 3, "masterzeugnis, taufschein, familienstammbuch, stammbuch, namensänderung, "
                            "vaterschaftsanerkennung, kirchenaustritt, austrittsbescheinigung, "
                            "birth certificate, marriage certificate, death certificate, "
                            "degree certificate"),
    ("certificate", "s", 2, "zertifikat, certificate, diploma, approbation, teilnahmebescheinigung, "
                            "teilnahmebestätigung, fortbildungsnachweis, qualifikationsnachweis, "
                            "empfehlungsschreiben, referenzschreiben, erbschein"),
    ("certificate", "w", 2, "diplom, toefl, ielts, telc, testdaf, ecdl, cambridge certificate"),
    ("certificate", "s", 1.5, "abitur, to whom it may concern, without reservation, highly recommend, "
                              "wärmstens empfehlen, uneingeschränkt empfehlen, berufliche zukunft, "
                              "weiteren berufsweg, weiteren lebensweg, bedauern sein ausscheiden, "
                              "bedauern ihr ausscheiden, bedauern wir sein, bedauern wir ihr, "
                              "verhalten gegenüber vorgesetzten, zu unserer vollen zufriedenheit, "
                              "stets zu unserer, auf eigenen wunsch, zu unserer vollsten"),
    ("certificate", "s", 1, "ausscheiden, tätigkeitsbereich, aufgabengebiet, fachkenntnisse, "
                            "arbeitsweise, i recommend, recommend him, recommend her"),

    # ----- Ausweise & Identität -----
    ("id", "s", 3, "personalausweis, reisepass, kinderreisepass, passport, führerschein, "
                   "aufenthaltstitel, aufenthaltserlaubnis, niederlassungserlaubnis, "
                   "sozialversicherungsausweis, identity card, id card, driver s license, "
                   "drivers license, driving licence, driving license, driver license, "
                   "staatsangehörigkeitsausweis, residence permit"),
    ("id", "s", 2, "ausweis, ausweiskopie, ausweisdokument, ausweisnummer, visum, meldebescheinigung, "
                   "anmeldebestätigung, aufenthaltsbescheinigung, passkopie, passnummer, einbürgerung"),
    ("id", "w", 2, "perso, id scan, id kopie, id copy, id front, id back, id vorderseite, "
                   "id rückseite, green card, pass kopie"),
    ("id", "w", 0.7, "id"),
    ("id", "w", 0.5, "visa"),
    ("id", "s", 3, "waffenbesitzkarte, jagdschein, angelschein, fischereischein, pilotenlizenz, "
                   "segelschein, social security card"),
    ("id", "w", 2.5, "esta"),
    ("id", "s", 2.5, "visa antrag, visaantrag, visa application, ummeldung"),

    # ----- Verträge & Rechtliches -----
    ("contract", "s", 3, "vertrag, verträge, arbeitsvertrag, mietvertrag, kaufvertrag, aufhebungsvertrag, "
                         "ehevertrag, darlehensvertrag, leasingvertrag, ausbildungsvertrag, "
                         "untermietvertrag, kreditvertrag, dienstvertrag, werkvertrag, erbvertrag, "
                         "contract, contracts, tenancy agreement, rental agreement, lease agreement, "
                         "employment agreement, non disclosure"),
    ("contract", "s", 2.5, "betreuungsverfügung, power of attorney, last will, offer letter, "
                           "letter of offer, abfindung"),
    ("contract", "s", 2.5, "kündigung, kündigungsbestätigung, geheimhaltungsvereinbarung, "
                           "vertraulichkeitsvereinbarung, vollmacht, vorsorgevollmacht, "
                           "generalvollmacht, testament, scheidungsurteil, scheidungsantrag, "
                           "scheidungsvereinbarung, sorgerecht"),
    ("contract", "s", 2, "vereinbarung, agreement, amtsgericht, landgericht, arbeitsgericht, "
                         "familiengericht, gerichtsurteil, gerichtsbeschluss, gerichtsvollzieher, "
                         "mahnbescheid"),
    ("contract", "w", 2.5, "nda, lease"),
    ("contract", "w", 2, "scheidung, urteil"),
    ("contract", "s", 1.5, "rechtsanwalt, anwalt, anwältin, vertragspartner, vertragsparteien, "
                           "governing law, contracting parties, hereinafter"),
    ("contract", "s", 1, "vertragsbeginn, vertragslaufzeit, vertragsende, kündigungsfrist, probezeit, "
                         "widerruf, beurkundung, notar, vertragsbedingungen, terms and conditions"),
    ("contract", "s", 0.7, "unterschrieben, unterzeichnet, gegengezeichnet, unterschrift, laufzeit"),
    ("contract", "w", 0.7, "signed, leasing"),
    ("contract", "s", 1, "vertragsnummer, vertragskonto, vertragskontonummer", 0),

    # ----- Versicherungen -----
    ("insurance", "s", 3, "versicherungsschein, versicherungspolice, versicherungsvertrag, "
                          "versicherungsbestätigung, versicherungsnachweis, haftpflichtversicherung, "
                          "hausratversicherung, berufsunfähigkeitsversicherung, insurance policy, "
                          "certificate of insurance, schadensmeldung, schadenmeldung, "
                          "schadensanzeige, schadenanzeige"),
    ("insurance", "s", 2, "versicherung, versicherungen, haftpflicht, hausrat, rechtsschutz, "
                          "berufsunfähigkeit, lebensversicherung, unfallversicherung, "
                          "krankenzusatzversicherung, zahnzusatzversicherung, kfz versicherung, "
                          "autoversicherung, reiseversicherung, reiserücktritt, "
                          "auslandskrankenversicherung, wohngebäudeversicherung, insurance, "
                          "versicherungsbedingungen, beitragsrechnung, beitragsanpassung, "
                          "schadensfall, schadenfall, schadennummer, schadensnummer, "
                          "versicherungsschutz, versicherungsnehmer, health insurance, "
                          "unfallbericht, unfallmeldung"),
    ("insurance", "s", 2, "krankenversicherung", 0.7),
    ("insurance", "w", 2.5, "police, policen"),
    ("insurance", "w", 2, "pkv, gkv, evb"),
    ("insurance", "s", 3, "insurance certificate, mitgliedsbescheinigung"),
    ("insurance", "s", 1.5, "krankenkasse, beitragsbescheinigung, versicherte person"),
    ("insurance", "w", 0.7, "allianz, huk, huk coburg, ergo, axa, debeka, generali, signal iduna, devk, "
                            "hdi, lvm, provinzial, barmenia, gothaer, aok, barmer, dak, tk, ikk, bkk, "
                            "techniker krankenkasse"),
    ("insurance", "s", 1, "versicherungsnummer, rentenversicherungsnummer, sozialversicherungsnummer, "
                          "versichertennummer, versicherungsnr, krankenversichertennummer", 0),

    # ----- Steuer -----
    ("tax", "s", 3, "steuerbescheid, einkommensteuerbescheid, einkommensteuererklärung, "
                    "steuererklärung, lohnsteuerbescheinigung, übermittlungsprotokoll, tax return, "
                    "tax assessment, steuerbescheinigung, jahressteuerbescheinigung, "
                    "umsatzsteuervoranmeldung, umsatzsteuererklärung, gewerbesteuererklärung, "
                    "spendenbescheinigung, zuwendungsbestätigung, steuerrückerstattung, "
                    "lohnsteuerjahresausgleich, form 1099, form 1040"),
    ("tax", "p", 3, "elster"),
    ("tax", "s", 2, "einkommensteuer, finanzamt, steuerberater, steuerberatung, werbungskosten, "
                    "sonderausgaben, außergewöhnliche belastungen, haushaltsnahe, kapitalerträge, "
                    "freistellungsauftrag, steuererstattung, steuernachzahlung, veranlagung, "
                    "zu versteuernde, zu versteuerndes, bruttoarbeitslohn, einbehaltene lohnsteuer, "
                    "self assessment, erbschaftsteuer, schenkungsteuer"),
    ("tax", "w", 3, "est bescheid, estbescheid, est erklärung, esterklärung, p45"),
    ("tax", "w", 2, "anlage n, anlage kap, anlage vorsorgeaufwand, anlage so, anlage kind, anlage r, "
                    "anlage v, w 2, w2, p60, lstb, 1099 misc, 1099 int, 1099 div, 1099 nec"),
    ("tax", "w", 1.5, "tax, taxes, irs, hmrc"),
    ("tax", "s", 1.5, "steuer, steuern", 1),
    ("tax", "s", 1, "festsetzung, steuerpflichtig"),
    ("tax", "s", 1, "lohnsteuer", 0.5),
    ("tax", "w", 0.5, "1099, 1040"),
    ("tax", "s", 1.5, "steuernummer, steuer nr, steuer id, steuerid, steueridentifikationsnummer, "
                      "steuerliche identifikationsnummer, steuer identifikationsnummer, "
                      "identifikationsnummer", 0),

    # ----- Gehalt & Rente -----
    ("payslip", "s", 3, "gehaltsabrechnung, lohnabrechnung, entgeltabrechnung, verdienstabrechnung, "
                        "bezügeabrechnung, bezügemitteilung, gehaltsnachweis, lohnnachweis, "
                        "verdienstnachweis, verdienstbescheinigung, gehaltsbescheinigung, lohnzettel, "
                        "gehaltszettel, payslip, pay slip, payslips, pay stub, paystub, salary slip, "
                        "salary statement, earnings statement, brutto netto bezüge, "
                        "brutto netto abrechnung, renteninformation, rentenauskunft, rentenbescheid, "
                        "versicherungsverlauf, meldebescheinigung zur sozialversicherung, "
                        "sozialversicherungsmeldung, entgeltbescheinigung, lohn und gehaltsabrechnung"),
    ("payslip", "s", 2.5, "bürgergeld, jobcenter, grundsicherung"),
    ("payslip", "s", 2, "gehaltserhöhung, jahresgehalt, bruttogehalt, nettogehalt, grundgehalt, "
                        "gehaltsmitteilung, lohnerhöhung, provisionsabrechnung, payroll, salary, "
                        "arbeitslosengeld, elterngeld, krankengeld, kindergeld, familienkasse, "
                        "deutsche rentenversicherung, pension statement, betriebsrente"),
    ("payslip", "w", 2, "gehalt, riester, rürup"),
    ("payslip", "p", 1.5, "rente"),
    ("payslip", "s", 1.5, "rentenversicherung", 0.7),
    ("payslip", "s", 1.5, "auszahlungsbetrag, nettoverdienst, nettobezug, nettobezüge, bruttobezüge, "
                          "gesamtbrutto, steuerbrutto, sv brutto, gesetzliche abzüge, gesetzliches netto, "
                          "net pay, gross pay"),
    ("payslip", "s", 1, "personalnummer, abrechnungsmonat, arbeitnehmeranteil, arbeitgeberanteil, "
                        "sozialversicherung, kv beitrag, rv beitrag, av beitrag, pv beitrag, "
                        "lohnart, year to date, national insurance, sozialversicherungsbeiträge"),
    ("payslip", "s", 0.5, "steuerklasse, urlaubsanspruch, resturlaub, überstunden"),
    ("payslip", "w", 1, "lohn, pension, ytd"),

    # ----- Gesundheit -----
    ("health", "s", 3, "arztbrief, krankmeldung, krankschreibung, arbeitsunfähigkeitsbescheinigung, "
                       "arbeitsunfähigkeit, impfpass, impfausweis, impfnachweis, impfzertifikat, "
                       "impfbescheinigung, impfbuch, mutterpass, untersuchungsheft, "
                       "kinderuntersuchungsheft, allergiepass, medikationsplan, medikamentenplan, "
                       "entlassungsbrief, entlassbrief, entlassungsbericht, befundbericht, laborbefund, "
                       "heil und kostenplan, heilkostenplan, privatrezept, kassenrezept, "
                       "patientenverfügung, sick note, medical certificate, medical report, "
                       "discharge summary, vaccination certificate, vaccination record, doctor s note, "
                       "doctors note, gesundheitszeugnis, schwerbehindertenausweis, notarzt, "
                       "operationsbericht, op bericht, arztbericht, krankenhausbericht"),
    ("health", "s", 3, "bonusheft, zahnbonusheft, gelbes heft, krankenkassenkarte, versichertenkarte"),
    ("health", "w", 2.5, "egk"),
    ("health", "s", 3, "vaccination card, vaccine card, impfkarte, impfausweis kopie"),
    ("health", "w", 3, "erezept, u heft"),
    ("health", "p", 3, "e rezept"),
    ("health", "s", 2, "brillenpass, arzt, ärztin, ärztlich, befund, diagnose, diagnosis, attest, impfung, röntgen, "
                       "ultraschall, blutbild, blutwerte, laborwerte, blutdruck, krankenhaus, klinik, "
                       "therapie, krankengymnastik, überweisungsschein, medikament, arznei, "
                       "prescription, vaccination, vaccine, hospital, sehtest, hörtest, "
                       "zahnbehandlung, zahnersatz, schwerbehinderung, grad der behinderung, "
                       "pflegegrad, schwangerschaft, vorsorgeuntersuchung, krankheit, erkrankung, "
                       "gesundheitskarte, krankenversichertenkarte, packungsbeilage, beipackzettel, "
                       "rehabilitation, anamnese, lab results"),
    ("health", "p", 2, "impf"),
    ("health", "w", 2, "mrt, mri, ekg, ecg, x ray, xray, reha"),
    ("health", "w", 1, "ct, doctor, dr med"),
    ("health", "p", 1, "patient, medical, symptom"),
    ("health", "s", 1, "apotheke, pharmacy, dosierung, tabletten, gesundheit, behandlung, untersuchung"),
    ("health", "w", 0.7, "health"),
    ("health", "w", 0.5, "praxis"),
    ("health", "s", 0.5, "beschwerden"),
    # "Rezept" zählt nur mit medizinischem Kontext (siehe _ORG_REZEPT_CTX_RE)
    ("health", "s", 2, "rezept, rezepte"),

    # ----- Wohnen & Eigentum -----
    ("property", "s", 3, "nebenkostenabrechnung, betriebskostenabrechnung, heizkostenabrechnung, "
                         "grundbuch, grundbuchauszug, flurkarte, katasterauszug, liegenschaftskarte, "
                         "wohnungsübergabeprotokoll, übergabeprotokoll, wohnungsgeberbestätigung, "
                         "eigentümerversammlung, hausgeldabrechnung, teilungserklärung, "
                         "mietbescheinigung, mieterhöhung, mietschuldenfreiheitsbescheinigung, "
                         "mietkaution, grundsteuerbescheid, grundsteuererklärung"),
    ("property", "s", 2.5, "rundfunkbeitrag, beitragsservice"),
    ("property", "s", 2.5, "nebenkosten, betriebskosten, energieausweis, baugenehmigung, bauantrag, "
                           "grundsteuer, wohnungsbewerbung, mieterselbstauskunft, wohngeld"),
    ("property", "s", 2, "heizkosten, stromabrechnung, gasabrechnung, wasserabrechnung, "
                         "hausverwaltung, hausgeld, wirtschaftsplan, baufinanzierung, hypothek, "
                         "grundschuld, utility bill"),
    ("property", "s", 1.5, "miete, mieter, vermieter, vermietung, kaution", 1),
    ("property", "s", 1.5, "mietwohnung, eigentumswohnung, eigentümer, immobilie, grundriss, lageplan, "
                           "tenancy, landlord, jahresabrechnung, mortgage"),
    ("property", "s", 1, "wohnung, eigentum, abschlagszahlung, tenant"),
    ("property", "w", 1.5, "deed, rent, expose, exposé"),
    ("property", "w", 1, "property"),

    # ----- Rechnungen & Belege -----
    ("invoice", "s", 2.5, "rechnung, rechnungen, invoice, receipt, quittung, kassenbon, kassenzettel, "
                          "kassenbeleg, kaufbeleg, zahlungsbeleg, rechnungsnummer, rechnungsnr, "
                          "rechnungsdatum, invoice number, invoice date, proforma"),
    ("invoice", "s", 1.5, "gutschrift, mahnung, zahlungserinnerung, zahlungsbestätigung, kaufbestätigung, "
                          "bestellbestätigung, auftragsbestätigung, lieferschein, bestellung, "
                          "bestellnummer, bestellnr, garantie, "
                          "gewährleistung, warranty, kostenvoranschlag, kaufnachweis, "
                          "order confirmation, purchase order, payment confirmation, zwischensumme, "
                          "gesamtbetrag, rechnungsbetrag, nettobetrag, bruttobetrag, zahlbar bis, "
                          "zahlungsziel, fällig am, lieferdatum, leistungsdatum, artikelnummer, "
                          "einzelpreis, subtotal, amount due, total due, balance due, belegnummer"),
    ("invoice", "s", 2, "garantieschein, garantiekarte, garantiebeleg, garantiezertifikat"),
    ("invoice", "s", 1, "angebot", 0.5),
    ("invoice", "s", 0.5, "umsatzsteuer, mehrwertsteuer, ust id, ust idnr, ustidnr, steuersatz", 1),
    ("invoice", "w", 1.2, "bill, beleg, belege"),
    ("invoice", "w", 1, "inv, ust, mwst, vat, order"),
    ("invoice", "w", 0.7, "amazon, ebay, zalando, mediamarkt, ikea, otto"),

    # ----- Bank & Finanzen -----
    ("bank", "s", 3, "kontoauszug, kontoauszüge, depotauszug, bank statement, account statement, "
                     "credit card statement, kreditkartenabrechnung, kreditkartenauszug, finanzstatus, "
                     "jahreskontoauszug, kontoabschluss, rechnungsabschluss, depotübersicht, "
                     "vermögensübersicht, wertpapierabrechnung"),
    ("bank", "s", 2, "girokonto, tagesgeld, festgeld, sparkonto, sparbuch, kontoeröffnung, kontostand, "
                     "kontoumsätze, umsatzübersicht, kontoübersicht, kreditkarte, dauerauftrag, "
                     "lastschriftmandat, sepa mandat, darlehen, tilgungsplan, wertpapier, dividende, "
                     "brokerage, sparplan, zinsbescheinigung, alter kontostand, neuer kontostand, "
                     "überweisungsträger, deutsche bank, ing diba, trade republic, scalable capital"),
    ("bank", "s", 1.5, "buchungstag, wertstellung, überweisung, kryptowährung"),
    ("bank", "p", 1.5, "depot"),
    ("bank", "p", 1, "kredit, aktie"),
    ("bank", "w", 1.5, "etf, etfs"),
    ("bank", "s", 3, "bonitätsauskunft, schufa auskunft, schufaauskunft"),
    ("bank", "w", 2, "schufa"),
    ("bank", "w", 1, "bank, statement, sparkasse, volksbank, raiffeisenbank, commerzbank, postbank, dkb, "
                     "comdirect, consorsbank, n26, targobank, hypovereinsbank, santander, revolut, "
                     "onvista, flatex, smartbroker, bitcoin, crypto, paypal, saldo"),
    ("bank", "w", 1, "iban, bic, sepa", 0.3),
    ("bank", "s", 0.5, "kontoinhaber, verwendungszweck"),

    # ----- Fahrzeug -----
    ("vehicle", "s", 3, "konformitätsbescheinigung, coc papiere, coc dokument"),
    ("vehicle", "s", 3, "zulassungsbescheinigung, fahrzeugschein, fahrzeugbrief, fahrzeugpapiere, "
                        "hauptuntersuchung, abgasuntersuchung, kraftfahrzeugsteuer, tüv bericht, "
                        "tüv bescheinigung, bußgeldbescheid, kfz brief, kfz schein, "
                        "vehicle registration, car registration"),
    ("vehicle", "s", 2.5, "werkstattrechnung, tankquittung, tankbeleg"),
    ("vehicle", "s", 1.5, "fahrzeug, kraftfahrzeug, werkstatt, inspektion, reifenwechsel, ölwechsel, "
                          "autohaus, autokauf, gebrauchtwagen, neuwagen, bußgeld, strafzettel, "
                          "verwarnungsgeld, fahrtenbuch, leasingrate, vehicle"),
    ("vehicle", "s", 0.7, "kennzeichen, zulassung"),
    ("vehicle", "w", 2, "tüv, tuv, dekra"),
    ("vehicle", "w", 1.5, "kfz, pkw, adac"),
    ("vehicle", "w", 1, "auto, reifen, motorrad, wohnmobil"),
    ("vehicle", "w", 0.7, "car, bmw, vw, volkswagen, audi, opel, ford, toyota, mercedes, skoda, "
                          "renault, tesla, hyundai, kia, fiat, peugeot"),

    # ----- Studium & Schule -----
    ("education", "s", 3, "immatrikulationsbescheinigung, studienbescheinigung, immatrikulation, "
                          "exmatrikulation, exmatrikulationsbescheinigung, bafög, notenspiegel, "
                          "notenübersicht, leistungsübersicht, bachelorarbeit, masterarbeit, "
                          "diplomarbeit, seminararbeit, abschlussarbeit, studienarbeit, dissertation, "
                          "doktorarbeit, zulassungsbescheid, schulbescheinigung, semesterbeitrag, "
                          "semesterticket, modulhandbuch, studienordnung, prüfungsordnung, "
                          "certificate of enrollment, certificate of enrolment, enrollment certificate, "
                          "enrolment certificate"),
    ("education", "w", 3, "thesis"),
    ("education", "s", 2, "hausarbeit, studienplatz, matrikelnummer, praktikumsbericht, praxissemester, "
                          "praxisbericht, lernzettel, mitschrift, vorlesungsskript, übungsblatt, "
                          "übungsaufgaben, aufgabenblatt, hausaufgabe, exercise sheet, problem set, "
                          "syllabus, berichtsheft, ausbildungsnachweis, projektarbeit, "
                          "integralrechnung, differentialrechnung, wahrscheinlichkeitsrechnung, "
                          "vektorrechnung, bruchrechnung, prozentrechnung, zinsrechnung, "
                          "kostenrechnung, rechnungswesen, abgabetermin, abgabe bis, aufgabe 1, "
                          "aufgabe 2"),
    ("education", "s", 1.5, "studium, studien, hochschule, universität, fachhochschule, university, "
                            "vorlesung, lecture, klausur, tutorium, stundenplan, gymnasium, "
                            "realschule, grundschule, berufsschule, gesamtschule, lehrplan, "
                            "homework, assignment, studiengang, studierende, lehrstuhl, nachhilfe, "
                            "abschlussprüfung, transcript"),
    ("education", "s", 1, "prüfung, schule, ausbildung, praktikum, semester, professor"),
    ("education", "w", 1.5, "uni, ects, exam, exams, seminar"),
    ("education", "p", 1, "skript, student, modul"),
    ("education", "w", 0.7, "bachelor, master, prof, kurs, course"),

    # ----- Arbeit -----
    ("work", "s", 2.5, "reisekostenabrechnung, spesenabrechnung, stundenzettel, stundennachweis, "
                       "arbeitszeitnachweis, zeiterfassung, timesheet, urlaubsantrag, "
                       "arbeitgeberbescheinigung, arbeitsbescheinigung, zielvereinbarung, "
                       "mitarbeitergespräch, jahresgespräch, personalgespräch, dienstplan, "
                       "schichtplan, dienstreise, dienstreiseantrag, geschäftsreise, business trip, "
                       "expense report, besprechungsprotokoll, meeting notes, meeting minutes, "
                       "sitzungsprotokoll, abmahnung"),
    ("work", "s", 2, "reisekosten"),
    ("work", "s", 1.5, "onboarding, elternzeit, quartalsbericht, jahresbericht, monatsbericht, "
                       "wochenbericht, statusbericht, spesen"),
    ("work", "s", 1, "besprechung, agenda, workshop, schulung, weiterbildung, fortbildung, betriebsrat, "
                     "homeoffice, home office, roadmap, kundenpräsentation"),
    ("work", "s", 1, "arbeitgeber", 0.5),
    ("work", "w", 1, "meeting, okr, kpi, kpis"),
    ("work", "s", 0.6, "projekt, präsentation, presentation, protokoll"),
    ("work", "s", 0.5, "bericht, konzept, budget"),
    ("work", "w", 0.5, "report, memo"),

    # ----- Wissenschaft -----
    ("science", "s", 2.5, "biorxiv, medrxiv, arxiv, peer review, rebuttal, lab notebook, laborbuch, "
                          "versuchsprotokoll, laborprotokoll, lab protocol, impact factor"),
    ("science", "p", 2, "manuscript, manuskript, preprint, supplementary"),
    ("science", "s", 1.5, "proceedings, symposium, publication, publikation, western blot, plasmid, "
                          "sequenzierung, sequencing, zellkultur, cell culture, antibody, antikörper, "
                          "gene expression, drittmittel, forschungsantrag, research proposal, "
                          "elsevier, springer"),
    ("science", "w", 2, "et al, doi"),
    ("science", "w", 1.5, "plos, ieee, pcr, qpcr, elisa, reviewer, reviewers"),
    ("science", "s", 1, "forschung, hypothese, hypothesis, assay, dataset, mikroskop, microscop"),
    ("science", "p", 1, "journal, conference, konferenz, research, experiment, protein"),
    ("science", "w", 1, "paper, papers, abstract, nature, poster, grant, dfg, supplement, primer, "
                        "acm, wiley, citation, studie"),
    ("science", "w", 0.5, "science, cell, introduction, methods, discussion, references, figure, fig, "
                          "results, conclusion, review"),

    # ----- Reisen & Tickets -----
    ("travel", "s", 3, "boarding pass, boardingpass, bordkarte, flugticket, flugbuchung, "
                       "flugbestätigung, bahnticket, bahnfahrkarte, zugticket, hotelbuchung, "
                       "hotelreservierung, hotelbestätigung, reisebestätigung, reiseunterlagen, "
                       "reiseplan, reiseroute, reiseverlauf, buchungsbestätigung, onlineticket, "
                       "online ticket, itinerary, flight confirmation, booking confirmation, "
                       "hotel reservation, mietwagen, car rental, rental car, ferienwohnung, airbnb, "
                       "eintrittskarte, konzertkarte, konzertticket, festivalticket, fahrkarte, "
                       "bahncard, deutschlandticket, pauschalreise, rundreise, reisebüro, "
                       "reisedokumente, travel documents"),
    ("travel", "w", 3, "eticket"),
    ("travel", "p", 3, "e ticket"),
    ("travel", "p", 1.5, "ticket, hotel, booking"),
    ("travel", "s", 1.5, "flug, packliste, deutsche bahn, urlaubsreise, reiseführer"),
    ("travel", "w", 1.5, "flight, flights, fewo, lufthansa, eurowings, ryanair, easyjet, condor, tuifly, "
                         "expedia, flixbus"),
    ("travel", "p", 1, "reise"),
    ("travel", "s", 1, "reservierung, gepäck, abflug, sitzplatz, departure"),
    ("travel", "w", 1, "reservation, check in, baggage, tui, sbb, öbb"),
    ("travel", "s", 0.7, "ankunft, arrival"),
    ("travel", "w", 0.7, "gate, travel, db"),
    ("travel", "s", 0.5, "urlaub, ausflug, buchung"),
    ("travel", "w", 0.5, "trip, holiday, holidays, vacation, sightseeing"),

    # ----- Anleitungen -----
    ("manual", "s", 3, "bedienungsanleitung, gebrauchsanweisung, gebrauchsanleitung, betriebsanleitung, "
                       "benutzerhandbuch, bedienungshandbuch, montageanleitung, aufbauanleitung, "
                       "installationsanleitung, kurzanleitung, schnellstartanleitung, user manual, "
                       "user guide, owner s manual, owners manual, instruction manual, "
                       "operating instructions, installation guide, quick start guide, "
                       "quickstart guide, datenblatt, datasheet, data sheet, produktdatenblatt, "
                       "sicherheitsdatenblatt"),
    ("manual", "s", 2, "anleitung, handbuch, quick start, quickstart, spec sheet, sicherheitshinweise, "
                       "safety instructions, getting started, pflegehinweise, specifications"),
    ("manual", "w", 2, "manual, manuals"),
    ("manual", "s", 1, "technische daten"),
    ("manual", "w", 1, "instructions, tutorial"),
    ("manual", "w", 0.7, "howto, how to, faq"),

    # ----- Sperrbegriffe (verbrauchen Text, zählen nichts) -----
    # "erzeugnis" nur am Wortanfang bzw. in bekannten Komposita sperren – sonst wird
    # "Masterzeugnis" (mast+erzeugnis) verschluckt
    ("", "p", 0, "erzeugnis, erzeugnisse"),
    ("", "s", 0, "milcherzeugnis, fleischerzeugnis, agrarerzeugnis, naturerzeugnis, druckerzeugnis, "
                 "tabakerzeugnis, industrieerzeugnis, landwirtschaftserzeugnis, backerzeugnis, "
                 "obsterzeugnis, holzerzeugnis, stahlerzeugnis"),
    # im Text nur Sperrbegriff (Gehaltsabrechnung), im Dateinamen aber echte Belege
    ("tax", "s", 2, "kirchensteuer", 0),
    ("insurance", "s", 2, "pflegeversicherung, arbeitslosenversicherung", 0),
    ("", "s", 0, "abrechnung, abrechnungen, berechnung, verrechnung, hochrechnung, umrechnung, "
                 "terminvereinbarung, schwarz, preise, kreis, wettbewerb, garantiert, "
                 "gesteuert, steuerung, steuerbar, steuergerät, steuereinheit, steuerrad, "
                 "steuerelement, steuerkette, überprüfung, vertragen, geburtstag, aktiengesellschaft, "
                 "eigentumsvorbehalt, unser eigentum, datenschutz, impressum, widerrufsbelehrung, "
                 "widerrufsrecht, widerrufsformular, solidaritätszuschlag, "
                 "support ticket, ticketsystem, police officer, police car, police station, "
                 "flugmodus, kochrezept, backrezept, tischreservierung, dipl ing, entscheidung, bescheinigung, "
                 "bewertung"),
    ("", "w", 0, "video id, user id, kunden id, order id, apple id, session id, client id, "
                 "transaction id, id list, id liste, ids"),
)

# Groß-/Kleinschreibung zählt (nur im Dateinamen): sonst zu mehrdeutig
_ORG_CASE_TOKENS = {"ToR": ("certificate", 2.5), "AU": ("health", 2.5), "eAU": ("health", 3.0),
                    "HU": ("vehicle", 2.0), "BU": ("insurance", 2.0), "KV": ("insurance", 1.5),
                    "CT": ("health", 1.0), "ESt": ("tax", 2.5), "EStE": ("tax", 3.0),
                    "eGK": ("health", 2.5)}
_ORG_CASE_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(sorted(_ORG_CASE_TOKENS, key=len, reverse=True))
                          + r")(?![A-Za-z0-9])")

# medizinischer Kontext für "Rezept"
_ORG_REZEPT_KEY = "rezept"
_ORG_REZEPT_CTX_RE = re.compile(
    r"(?<![a-z0-9])(?:arzt|aerzt|praxis|medikament|apothek|patient|dosierung|tablette|mg|pzn|"
    r"verordnung|krankenkasse|dr med|pharma|kapseln|salbe|einnahme|wirkstoff|hausarzt)"
    r"|(?:arzt|aerztin|apotheke|medikament|tabletten|ibuprofen|paracetamol|antibiotik|amoxicillin|"
    r"novalgin|cortison|kortison|insulin|tropfen)|\d ?mg(?![a-z])")

# Chat-Export (WhatsApp/Telegram ...): Text zählt dann nicht
_ORG_CHAT_LINE_RE = re.compile(
    r"^\[?\d{1,2}[./]\d{1,2}[./]\d{2,4},?\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[ap]\.?m\.?)?\]?\s*(?:-\s*)?"
    r"[^:\n]{1,40}:", re.M | re.I)
# Dateiname eines Chat-Exports ("WhatsApp Chat mit Anna.txt") -> gar nicht einordnen.
# Ein bloßes "WhatsApp" im Namen ("Personalausweis WhatsApp.jpg") verwirft nur den Text.
_ORG_CHAT_NAME_RE = re.compile(r"(?<![a-z0-9])(?:whatsapp chat|telegram chat|signal chat|chat mit|"
                               r"chat with|chatverlauf|chat export|chat history)(?![a-z0-9])")
_ORG_CHAT_APP_RE = re.compile(r"(?<![a-z0-9])(?:whatsapp|telegram)(?![a-z0-9])")

_ORG_EXT_RE = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,5}$")


# ---------------------------------------------------------------------------
# Aufbau des Trie-Regex (einmal beim Import)
# ---------------------------------------------------------------------------
class OrgLexTerm(object):
    __slots__ = ("key", "cat", "nw", "tw", "mode", "display")

    def __init__(self, key, cat, nw, tw, mode, display):
        self.key = key
        self.cat = cat
        self.nw = nw
        self.tw = tw
        self.mode = mode
        self.display = display


def _org_term_variants(term: str) -> list:
    """Normalform + Variante ohne Umlaut-Transkription (Fuhrerschein statt Führerschein)."""
    out = [org_normalize(term)]
    plain = _ORG_NON_ALNUM_RE.sub(" ", term.lower().translate(_ORG_PLAIN_TABLE)).strip()
    if plain != out[0] and len(plain) >= 5:
        out.append(plain)
    return [v for v in out if v]


def _org_build_lexicon():
    terms = {}
    conflicts = []
    for entry in _ORG_LEX_SRC:
        cat, mode, nw, words = entry[0], entry[1], float(entry[2]), entry[3]
        tw = float(entry[4]) if len(entry) > 4 else nw
        for raw in words.split(","):
            raw = raw.strip()
            if not raw:
                continue
            for key in _org_term_variants(raw):
                if key in terms:
                    old = terms[key]
                    if (old.cat, old.mode) != (cat, mode) or old.nw != nw:
                        conflicts.append(key)
                    continue
                terms[key] = OrgLexTerm(key, cat, nw, tw, mode, raw)
    return terms, conflicts


def _org_trie_pattern(terms: dict) -> str:
    """Erzeugt aus allen Begriffen einen Trie-Regex (längster Treffer zuerst)."""
    root = {}
    end = ""          # Schlüssel für "Begriff endet hier"
    for key, t in terms.items():
        node = root
        for ch in key:
            node = node.setdefault(ch, {})
        if t.mode == "s":
            node[end] = ""
        else:
            # Wortanfang per fester Lookbehind-Breite am Blatt prüfen
            suffix = "(?<![a-z0-9]" + re.escape(key) + ")"
            if t.mode == "w":
                suffix += "(?![a-z0-9])"
            node[end] = suffix

    def gen(node):
        suffix = node.get(end)
        chars = sorted(k for k in node if k != end)
        if not chars:
            return suffix or ""
        alts = [re.escape(c) + gen(node[c]) for c in chars]
        body = alts[0] if len(alts) == 1 else "(?:" + "|".join(alts) + ")"
        if suffix is None:
            return body
        if suffix == "":
            return "(?:" + body + ")?"
        return "(?:" + body + "|" + suffix + ")"

    return gen(root)


_ORG_LEX_TERMS, _ORG_LEX_CONFLICTS = _org_build_lexicon()
_ORG_LEX_RE = re.compile(_org_trie_pattern(_ORG_LEX_TERMS))
_ORG_CAT_ORDER = {c: i for i, c in enumerate(ORG_LEX_CATEGORIES)}


# ---------------------------------------------------------------------------
# Jahreserkennung
# ---------------------------------------------------------------------------
_ORG_YEAR_ANCHOR_RE = re.compile(
    r"(?:veranlagungszeitraum|veranlagungsjahr|steuerjahr|kalenderjahr|wirtschaftsjahr|"
    r"geschaeftsjahr|abrechnungsjahr|abrechnungszeitraum|tax\s+year|fiscal\s+year|financial\s+year|"
    r"f(?:ue|u)r\s+das\s+(?:kalender|steuer|wirtschafts)?jahr|"
    r"for\s+the\s+(?:tax\s+|fiscal\s+|calendar\s+)?year|bescheid\s+f(?:ue|u)r|"
    r"einkommensteuer(?:erkl(?:ae|a)rung|bescheid)?|steuererkl(?:ae|a)rung|steuerbescheid|"
    r"lohnsteuerbescheinigung|jahressteuerbescheinigung|steuerbescheinigung)"
    # (?![.,]\d): "Einkommensteuer 2015,00 €" ist ein Betrag, kein Jahr
    r"[^\d\n]{0,25}?(?:\d{1,2}\.\s?\d{1,2}\.\s?)?((?:19|20)\d\d)(?!\d)(?![.,]\d)")
_ORG_MONTHS = (r"januar|jaenner|jan|februar|feb|maerz|marz|mar|march|april|apr|mai|may|juni|june|jun|"
               r"juli|july|jul|august|aug|september|sept|sep|oktober|october|okt|oct|november|nov|"
               r"dezember|december|dez|dec")
# Text: zuerst alle vierstelligen Jahreskandidaten finden (selten -> schnell), dann den
# Kontext links/rechts prüfen (Datum dd.mm.yyyy, yyyy-mm-dd, mm/yyyy, "März 2023", "2023/24").
_ORG_YEAR4_RE = re.compile(r"(?:19|20)\d\d")
_ORG_LEFT_DMY_RE = re.compile(r"(?:^|[^\d])(\d{1,2})([./-])\s?(\d{1,2})\2\s?$")
_ORG_LEFT_MY_RE = re.compile(r"(?:^|[^\d.,])(\d{1,2})[./]$")
_ORG_LEFT_MONTH_RE = re.compile(r"(?:^|[^a-z])(?:" + _ORG_MONTHS + r")\.?\s*"
                                r"(?:\d{1,2}(?:st|nd|rd|th)?\.?,?\s*)?$")
_ORG_RIGHT_YMD_RE = re.compile(r"([./-])(\d{1,2})\1(\d{1,2})(?!\d)")
_ORG_RIGHT_YY_RE = re.compile(r"/(\d\d|\d{4})(?![\d/.])")
_ORG_DMYY_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d\d)(?![\d.])")
_ORG_NAME_YEAR_RE = re.compile(
    r"(?<!\d)(?:(?P<a>(?:19|20)\d\d)(?:(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])?)?"
    r"|(?:0[1-9]|[12]\d|3[01])(?:0[1-9]|1[0-2])(?P<b>(?:19|20)\d\d))(?!\d)")
_ORG_NAME_MMYY_RE = re.compile(r"(?<![\d])(?:0[1-9]|1[0-2])[._ -](\d\d)(?![\d])")
_ORG_ANCHOR_HINTS = ("jahr", "year", "zeitraum", "bescheid", "steuer")


def _org_year_ok(y: int, now=None) -> bool:
    return 1990 <= y <= (now or time.localtime().tm_year) + 1


def _org_text_years(t: str, now: int) -> dict:
    """Zählt Jahre aus Datumsangaben im (gefalteten) Text; ohne Datum: freie Jahreszahlen."""
    dates = {}
    bare = {}
    n = len(t)
    for m in _ORG_YEAR4_RE.finditer(t):
        s, e = m.start(), m.end()
        if (s and t[s - 1].isdigit()) or (e < n and t[e].isdigit()):
            continue
        y = int(m.group())
        if not _org_year_ok(y, now):
            continue
        left = t[max(0, s - 24):s]
        right = t[e:e + 6]
        is_date = False
        lm = _ORG_LEFT_DMY_RE.search(left[-10:])
        if lm and 1 <= int(lm.group(1)) <= 31 and 1 <= int(lm.group(3)) <= 12:
            is_date = True
        elif _ORG_LEFT_MY_RE.search(left[-4:]) and 1 <= int(_ORG_LEFT_MY_RE.search(left[-4:]).group(1)) <= 12:
            is_date = True
        elif _ORG_LEFT_MONTH_RE.search(left):
            is_date = True
        else:
            rm = _ORG_RIGHT_YMD_RE.match(right)
            if rm and 1 <= int(rm.group(2)) <= 12 and 1 <= int(rm.group(3)) <= 31:
                is_date = True
            else:
                rm = _ORG_RIGHT_YY_RE.match(right)
                if rm and int(rm.group(1)) % 100 == (y + 1) % 100:
                    is_date = True
        if is_date:
            dates[y] = dates.get(y, 0) + 1
        elif not ((s and t[s - 1] in ".,/-") or (e + 1 < n and t[e] in ".," and t[e + 1].isdigit())):
            bare[y] = bare.get(y, 0) + 1
    if "." in t:
        for m in _ORG_DMYY_RE.finditer(t):
            s = m.start()
            if s and (t[s - 1].isdigit() or t[s - 1] == "."):
                continue
            if 1 <= int(m.group(1)) <= 31 and 1 <= int(m.group(2)) <= 12:
                y = 2000 + int(m.group(3))
                if _org_year_ok(y, now):
                    dates[y] = dates.get(y, 0) + 1
    return dates or bare


def _org_detect_year_folded(fname: str, ftext: str):
    now = time.localtime().tm_year
    # 1) Anker ("Veranlagungszeitraum 2022", "tax year 2022", "Bescheid für 2022")
    for src in (fname, ftext):
        if src and any(h in src for h in _ORG_ANCHOR_HINTS):
            for m in _ORG_YEAR_ANCHOR_RE.finditer(src):
                y = int(m.group(1))
                if _org_year_ok(y, now):
                    return y
    # 2) Dateiname
    for m in _ORG_NAME_YEAR_RE.finditer(fname):
        y = int(m.group("a") or m.group("b"))
        if _org_year_ok(y, now):
            return y
    for m in _ORG_NAME_MMYY_RE.finditer(fname):
        y = 2000 + int(m.group(1))
        if _org_year_ok(y, now):
            return y
    # 3) häufigstes Jahr aus Datumsangaben im Text (Gleichstand: das spätere)
    if ftext:
        counts = _org_text_years(ftext, now)
        if counts:
            return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
    return None


def org_detect_year(name: str, text: str = ""):
    """Bezugsjahr eines Dokuments (1990..aktuelles Jahr+1) oder None.
    Reihenfolge: Anker wie "Veranlagungszeitraum 2022"/"tax year 2022" > Jahr im
    Dateinamen > häufigstes plausibles Jahr aus Datumsangaben im Text > freie Jahreszahl."""
    try:
        # wie org_classify_document: auch Path-Objekte, nur der Dateiname zählt
        name = _org_to_str(name)
        name = os.path.basename(name.replace("\\", "/")) or name
        text = text if isinstance(text, str) else ""
        if text and "-" in text and "\n" in text:
            text = _ORG_HYPHEN_BREAK_RE.sub("", text[:ORG_LEX_MAX_TEXT + 200])
        fname = _org_fold(_ORG_EXT_RE.sub("", name))
        ftext = _org_fold(text[:ORG_LEX_MAX_TEXT]) if text else ""
        return _org_detect_year_folded(fname, ftext)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Klassifikation
# ---------------------------------------------------------------------------
def _org_scan(norm: str) -> dict:
    hits = {}
    for m in _ORG_LEX_RE.finditer(norm):
        k = m.group()
        hits[k] = hits.get(k, 0) + 1
    return hits


def _org_saturate(s: float) -> float:
    """Text-Evidenz: bis 6 linear, darüber gedämpft, nie über ORG_LEX_TEXT_CAP
    (bleibt damit unter einem starken Namensbegriff = 9)."""
    if s <= 6.0:
        return s
    room = ORG_LEX_TEXT_CAP - 6.0
    return 6.0 + room * (1.0 - math.exp(-(s - 6.0) / room))


def _org_is_chat_text(text: str) -> bool:
    """Text sieht aus wie ein Chat-Export: mind. 3 Zeilen "12.03.23, 14:22 - Name: ..." und
    diese machen mind. zwei Drittel der Zeilen aus (ein Kontoauszug mit Uhrzeiten zählt nicht)."""
    if not text:
        return False
    head = text[:4000]
    n = 0
    for _ in _ORG_CHAT_LINE_RE.finditer(head):
        n += 1
    if n < 3:
        return False
    lines = sum(1 for ln in head.split("\n") if ln.strip())
    return n * 3 >= lines * 2


def org_classify_document(name: str, text: str = "", kind: str = "document") -> tuple:
    """Ordnet ein Dokument einer Kategorie aus ORG_CATEGORIES zu.
    Rückgabe: (kategorie oder None, sicherheit 0..1, getroffene begriffe, jahr oder None)."""
    try:
        name = _org_to_str(name)
        text = text if isinstance(text, str) else ""
        kind = (kind or "document").lower() if isinstance(kind, str) else "document"
        name = os.path.basename(name.replace("\\", "/")) or name
        stem = _ORG_EXT_RE.sub("", name) or name
        if text and "-" in text and "\n" in text:
            text = _ORG_HYPHEN_BREAK_RE.sub("", text[:ORG_LEX_MAX_TEXT + 200])
        fname = _org_fold(stem)
        ftext = _org_fold(text[:ORG_LEX_MAX_TEXT]) if text else ""
        year = _org_detect_year_folded(fname, ftext)
        if kind in _ORG_SKIP_KINDS:
            return (None, 0.0, [], year)
        media = kind in _ORG_MEDIA_KINDS

        name_norm = _ORG_NON_ALNUM_RE.sub(" ", fname).strip()
        # Chat-Export: Name wie "WhatsApp Chat mit Anna" -> nichts; Chat-Text (oder nur
        # "WhatsApp" im Namen) -> nur der Text zählt nicht, der Dateiname weiterhin
        # ("Lebenslauf per WhatsApp.pdf" bleibt ein Lebenslauf).
        if _ORG_CHAT_NAME_RE.search(name_norm):
            return (None, 0.0, [], year)
        if ftext and (_ORG_CHAT_APP_RE.search(name_norm) or _org_is_chat_text(text)):
            ftext = ""
        media_event = bool(media and _ORG_MEDIA_EVENT_RE.search(name_norm))

        # --- Dateiname (auch mit getrennten Binnen-Großbuchstaben: "TranscriptOfRecords") ---
        name_hits = _org_scan(name_norm)
        camel = org_normalize(_ORG_CAMEL_RE.sub(" ", stem))
        if camel != name_norm:
            for k in _org_scan(camel):
                name_hits.setdefault(k, 1)
        # zusammengezogene Form: "Gehalts-Abrechnung", "Lebens Lauf", "Steuer_Bescheid".
        # Nur lange Teilwort-Begriffe, die wirklich über eine Trennstelle reichen.
        if " " in name_norm:
            tokens = name_norm.split()
            for k in _org_scan(name_norm.replace(" ", "")):
                if k in name_hits:
                    continue
                t = _ORG_LEX_TERMS[k]
                kj = k.replace(" ", "")
                if t.mode != "s" or len(kj) < 8 or any(kj in tok for tok in tokens):
                    continue
                name_hits[k] = 1
        # --- Text ---
        text_norm = _ORG_NON_ALNUM_RE.sub(" ", ftext).strip() if ftext else ""
        text_hits = _org_scan(text_norm) if text_norm else {}
        # Begriffe in der Überschrift (Textanfang) zählen doppelt bzw. dreifach (Dokumenttitel)
        head_hits = {}
        if text_norm:
            head = " ".join(ln for ln in ftext[:ORG_LEX_HEAD * 3].split("\n", 3)[:3] if ln.strip())
            head_hits = _org_scan(_ORG_NON_ALNUM_RE.sub(" ", head[:ORG_LEX_HEAD]))

        # Rezept nur mit medizinischem Kontext
        rez_ok = None

        def rezept_ok():
            return bool(_ORG_REZEPT_CTX_RE.search(name_norm) or
                        (text_norm and _ORG_REZEPT_CTX_RE.search(text_norm)))

        name_score = {}
        name_terms = {}
        for k in name_hits:
            t = _ORG_LEX_TERMS[k]
            if not t.cat or t.nw <= 0:
                continue
            if media:
                if t.nw < ORG_LEX_MEDIA_MIN or k in _ORG_MEDIA_DENY:
                    continue
                # Foto eines Ereignisses: nur echte Ausweis-/Pass-Begriffe zählen
                if media_event and not (t.cat in ("id", "health") and t.nw >= 3):
                    continue
            if t.display.startswith(_ORG_REZEPT_KEY) and t.cat == "health":
                if rez_ok is None:
                    rez_ok = rezept_ok()
                if not rez_ok:
                    continue
            w = t.nw * ORG_LEX_NAME_FACTOR
            name_score[t.cat] = name_score.get(t.cat, 0.0) + w
            name_terms.setdefault(t.cat, []).append((w, t.display))
        for m in _ORG_CASE_RE.finditer(stem):
            cat, w = _ORG_CASE_TOKENS[m.group(1)]
            if media and (w < ORG_LEX_MEDIA_MIN or media_event):
                continue
            w *= ORG_LEX_NAME_FACTOR
            name_score[cat] = name_score.get(cat, 0.0) + w
            name_terms.setdefault(cat, []).append((w, m.group(1)))

        text_raw = {}
        text_terms = {}
        for k, cnt in text_hits.items():
            t = _ORG_LEX_TERMS[k]
            if not t.cat or t.tw <= 0:
                continue
            if t.display.startswith(_ORG_REZEPT_KEY) and t.cat == "health":
                if rez_ok is None:
                    rez_ok = rezept_ok()
                if not rez_ok:
                    continue
            w = t.tw * (1.0 + 0.2 * min(cnt - 1, 3))
            if k in head_hits:
                w += t.tw * (2.0 if t.tw >= ORG_LEX_STRONG else 1.0)
            d = text_raw.setdefault(t.cat, [0.0, 0, False])
            d[0] += w
            d[1] += 1
            if t.tw >= ORG_LEX_STRONG:
                d[2] = True
            text_terms.setdefault(t.cat, []).append((w, t.display))

        totals = dict(name_score)
        for cat, (s, distinct, strong) in text_raw.items():
            if distinct >= 2 or strong or cat in name_score:
                totals[cat] = totals.get(cat, 0.0) + _org_saturate(s)
        if not totals:
            return (None, 0.0, [], year)

        ranked = sorted(totals.items(), key=lambda kv: (-kv[1], _ORG_CAT_ORDER.get(kv[0], 99)))
        best_cat, best = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        if best < ORG_LEX_MIN_SCORE or (best_cat not in name_score and best < ORG_LEX_MIN_TEXT):
            return (None, 0.0, [], year)
        evidence = 1.0 - math.exp(-best / 2.5)
        margin = (best - second) / best
        score = evidence * (0.5 + 0.5 * margin)
        if best_cat not in name_score:
            score *= 0.9                      # nur Textbelege: etwas vorsichtiger
        score = round(max(0.0, min(1.0, score)), 3)

        terms = []
        for lst in (name_terms.get(best_cat, []), text_terms.get(best_cat, [])):
            for _w, disp in sorted(lst, key=lambda x: -x[0]):
                if disp not in terms:
                    terms.append(disp)
        return (best_cat, score, terms[:8], year)
    except Exception:
        return (None, 0.0, [], None)


def org_lexicon_reason(category: str, terms: list) -> str:
    """Kurze deutsche Begründung, z. B. 'Steuer erkannt (steuerbescheid, finanzamt)'."""
    try:
        if not category or not isinstance(category, str):
            return ""
        try:
            label = ORG_CATEGORIES.get(category, {}).get("label", category)
        except Exception:
            label = category
        if isinstance(terms, str):
            terms = [terms]
        try:
            shown = ", ".join(str(t) for t in list(terms or [])[:3])
        except Exception:
            shown = ""
        return "%s erkannt (%s)" % (label, shown) if shown else "%s erkannt" % label
    except Exception:
        return ""


# ----- org_extract -----------------------------------------------------------
# ===========================================================================
# ORDNER-ASSISTENT – sichere Text- und Metadaten-Extraktion
# ===========================================================================
# Liest Textanfänge (PDF, Office, RTF, Text/HTML/E-Mail), EXIF-Daten und
# Bildgrößen – ohne externe Bibliotheken, threadsicher (läuft im QThread-Worker),
# robust gegen kaputte/abgeschnittene Dateien, Zip-Bomben und Offset-Schleifen.
# Öffentliche Funktionen werfen nie Ausnahmen, sondern liefern neutrale Werte.
# Alle Namen beginnen mit org_ / Org / ORG_ bzw. _org_ (privat).

import base64
import binascii
import codecs
import html
import os
import re
import stat
import struct
import threading
import zipfile
import zlib
from pathlib import Path

# ---------- Einstellungen ----------
# QtPdf (pdfium) nur als Rückfall, wenn der reine Python-Leser (fast) nichts
# findet (z. B. verschlüsselte PDFs mit leerem Kennwort, exotische Fonts).
# Im Worker-Thread ohne QGuiApplication getestet; alle Aufrufe werden über eine
# Sperre serialisiert. Auf False setzen, um QtPdf komplett zu meiden.
ORG_EXTRACT_USE_QTPDF = True
ORG_EXTRACT_QTPDF_MAX_BYTES = 200 * 1024 * 1024

ORG_EXTRACT_TEXT_EXTS = frozenset({
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".log", ".html", ".htm", ".xhtml",
    ".xml", ".json", ".eml", ".tex"})
ORG_EXTRACT_WORD_EXTS = frozenset({".docx", ".docm", ".dotx", ".dotm"})
ORG_EXTRACT_XL_EXTS = frozenset({".xlsx", ".xlsm", ".xltx", ".xltm"})
ORG_EXTRACT_PPT_EXTS = frozenset({".pptx", ".pptm", ".potx", ".ppsx"})
ORG_EXTRACT_ODF_EXTS = frozenset({".odt", ".ods", ".odp", ".ott", ".ots", ".otp"})

# Cloud-Platzhalter (OneDrive „Nur online verfügbar“ u. ä.) nicht lesen: jedes Öffnen
# würde die Datei aus dem Internet herunterladen (keine Netzwerkzugriffe!).
ORG_EXTRACT_SKIP_CLOUD = True
# FILE_ATTRIBUTE_OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS
ORG_EXTRACT_CLOUD_ATTRS = 0x1000 | 0x40000 | 0x400000

_ORG_TEXT_READ_MAX = 256 * 1024
_ORG_TEXT_READ_FIRST = 32 * 1024
_ORG_EXIF_READ_FIRST = 128 * 1024
_ORG_RTF_READ_MAX = 2 * 1024 * 1024
_ORG_ZIP_MEMBER_MAX = 30 * 1024 * 1024
_ORG_ZIP_RATIO_MAX = 200
_ORG_ZIP_XML_READ = 3 * 1024 * 1024
_ORG_PDF_HEAD = 2 * 1024 * 1024
_ORG_PDF_TAIL = 1024 * 1024
_ORG_PDF_STREAM_MAX = 8 * 1024 * 1024
_ORG_PDF_TOTAL_MAX = 48 * 1024 * 1024
_ORG_IMG_HEAD = 512 * 1024

_org_qt_lock = threading.Lock()


# ===========================================================================
# Allgemeine Helfer
# ===========================================================================
_ORG_RE_CTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffd\ue000-\uf8ff\u200b\ufeff]")


def _org_ws(text: str) -> str:
    """Leerraum zusammenfassen (str.split ist ~10x schneller als re.sub);
    Rand-Leerzeichen bleiben als ein Leerzeichen erhalten (Stückgrenzen)."""
    j = " ".join(text.split())
    if not j:
        return " " if text else ""
    if text[0].isspace():
        j = " " + j
    if text[-1].isspace():
        j += " "
    return j


def _org_clean_part(text: str) -> str:
    text = text.replace("\xad", "")
    if _ORG_RE_CTRL.search(text):
        text = _ORG_RE_CTRL.sub(" ", text)
    return _org_ws(text)


def _org_clean(text: str, max_chars: int) -> str:
    """Steuerzeichen entfernen, Leerraum zusammenfassen, kürzen.
    Stückweise, damit lange Texte (256 KB) nicht komplett bereinigt werden."""
    if not text or max_chars <= 0:
        return ""
    step = max(16384, max_chars * 2)
    if len(text) <= step:
        return _org_clean_part(text).strip()[:max_chars]
    parts = []
    got = 0
    i = 0
    n = len(text)
    while i < n and got <= max_chars + 1:
        part = _org_clean_part(text[i:i + step])
        parts.append(part)
        got += len(part)
        i += step
    return _org_ws("".join(parts)).strip()[:max_chars]


def _org_fs_path(path, nt=None) -> str:
    """Windows: lange Pfade (>= 248 Zeichen) mit \\\\?\\ versehen, sonst unverändert."""
    s = os.fspath(path)
    if not isinstance(s, str):
        return s
    if nt is None:
        nt = os.name == "nt"
    if not nt or len(s) < 248 or s.startswith("\\\\?\\"):
        return s
    import ntpath
    if os.name == "nt" and not ntpath.isabs(s):
        s = ntpath.abspath(s)
    s = ntpath.normpath(s)
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    if len(s) >= 3 and s[1] == ":" and s[2] == "\\":
        return "\\\\?\\" + s
    return s


def _org_file_ok(path, st=None) -> bool:
    """Nur normale, lokal vorhandene Dateien lesen (keine Ordner, Pipes, Geräte,
    keine Cloud-Platzhalter, die beim Öffnen heruntergeladen würden)."""
    try:
        if st is None:
            st = os.stat(path)
        if not stat.S_ISREG(st.st_mode):
            return False
        if ORG_EXTRACT_SKIP_CLOUD:
            attrs = getattr(st, "st_file_attributes", 0) or 0
            if attrs & ORG_EXTRACT_CLOUD_ATTRS:
                return False
        return True
    except (OSError, ValueError, TypeError, OverflowError):
        return False


def _org_prepare(path):
    """Pfad prüfen/umwandeln → str für open() oder None, wenn nicht lesbar."""
    try:
        p = _org_fs_path(path)
    except (TypeError, ValueError):
        return None
    if not _org_file_ok(p):
        return None
    return p


def _org_read(path, limit: int, offset: int = 0) -> bytes:
    try:
        with open(path, "rb") as fh:
            if offset:
                fh.seek(offset)
            return fh.read(limit)
    except (OSError, ValueError, TypeError, OverflowError):
        return b""


def _org_unhex(raw: bytes) -> bytes:
    h = re.sub(rb"[^0-9A-Fa-f]", b"", raw)
    if len(h) % 2:
        h += b"0"
    try:
        return binascii.unhexlify(h)
    except (binascii.Error, ValueError):
        return b""


# ===========================================================================
# Klartext, HTML, E-Mail
# ===========================================================================
def _org_decode_bytes(data: bytes, truncated: bool = True) -> str:
    """utf-8 (BOM) → utf-16 (BOM/Heuristik) → cp1252.
    truncated=False: Datei vollständig gelesen → kein abgeschnittenes utf-8-Zeichen
    am Ende möglich (sonst ginge z. B. das „ü“ in cp1252 „Menü“ verloren)."""
    if not data:
        return ""
    if data.startswith(codecs.BOM_UTF8):
        return data[3:].decode("utf-8", "replace")
    if data.startswith(codecs.BOM_UTF32_LE) or data.startswith(codecs.BOM_UTF32_BE):
        return data.decode("utf-32", "replace")
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        return data[:len(data) - (len(data) % 2)].decode("utf-16", "replace")
    sample = data[:4096]
    zeros = sample.count(0)
    if len(sample) >= 4 and zeros > len(sample) // 4:
        odd = sample[1::2].count(0)
        even = sample[0::2].count(0)
        enc = "utf-16-le" if odd > even else "utf-16-be"
        return data[:len(data) - (len(data) % 2)].decode(enc, "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        # abgeschnittenes Mehrbyte-Zeichen am Ende (Leselimit) → trotzdem utf-8
        if truncated and e.end >= len(data) and e.start >= len(data) - 3:
            try:
                return data[:e.start].decode("utf-8")
            except UnicodeDecodeError:
                pass
    return data.decode("cp1252", "replace")


_ORG_RE_HTML_OPEN = re.compile(r"<(script|style|noscript|svg)\b|<!--", re.I)
_ORG_RE_HTML_TAG = re.compile(r"<[^<>]{0,4000}>")


def _org_html_drop(text: str) -> str:
    """Skripte, Styles, Kommentare entfernen – linear (kein Rückverfolgen bei
    fehlenden End-Tags; Rest ab einem offenen Block wird verworfen)."""
    out = []
    pos = 0
    low = None
    n = len(text)
    while pos < n:
        m = _ORG_RE_HTML_OPEN.search(text, pos)
        if m is None:
            out.append(text[pos:])
            break
        out.append(text[pos:m.start()])
        out.append(" ")
        if m.group(1) is None:
            end = text.find("-->", m.end())
            pos = n if end < 0 else end + 3
        else:
            if low is None:
                low = text.lower()
            end = low.find("</" + m.group(1).lower(), m.end())
            if end < 0:
                pos = n
            else:
                gt = text.find(">", end)
                pos = n if gt < 0 else gt + 1
    return "".join(out)


def _org_strip_markup(text: str) -> str:
    text = _org_html_drop(text)
    text = _ORG_RE_HTML_TAG.sub(" ", text)
    return html.unescape(text)


def _org_eml_text(data: bytes) -> str:
    """E-Mail: Betreff + Text-Teil (MIME/Base64/QP dekodiert)."""
    try:
        import email
        import email.policy
        msg = email.message_from_bytes(data, policy=email.policy.default)
        parts = []
        for h in ("subject", "from", "date"):
            v = msg.get(h)
            if v:
                parts.append(str(v))
        body = msg.get_body(preferencelist=("plain", "html"))
        if body is not None:
            try:
                content = body.get_content()
            except Exception:                      # unbekannter Zeichensatz u. ä.
                content = body.get_payload(decode=True) or b""
            if isinstance(content, bytes):
                content = _org_decode_bytes(content, False)
            if body.get_content_subtype() == "html":
                content = _org_strip_markup(content)
            parts.append(content)
        if parts:
            return " ".join(parts)
    except Exception:
        pass
    return _org_decode_bytes(data)


def _org_plain_text(path, ext: str, max_chars: int = 4000) -> str:
    markup = ext in (".html", ".htm", ".xhtml", ".xml")
    # Klartext: zuerst nur einen Anfang lesen (spart E/A bei großen Logs/CSVs);
    # reicht er nach dem Bereinigen nicht, bis 256 KB nachlesen.
    first = _ORG_TEXT_READ_MAX if (markup or ext == ".eml") else \
        min(_ORG_TEXT_READ_MAX, max(_ORG_TEXT_READ_FIRST, max_chars * 8))
    limit = first
    while True:
        data = _org_read(path, limit)
        if not data:
            return ""
        if ext == ".eml":
            return _org_eml_text(data)
        text = _org_decode_bytes(data, len(data) >= limit)
        if text:
            bad = len(_ORG_RE_CTRL.findall(text[:8192]))
            if bad > len(text[:8192]) * 0.05:
                return ""                          # Binärdaten mit Text-Endung
        if markup:
            text = _org_strip_markup(text)
        text = _org_clean(text, max_chars)
        if len(data) < limit or limit >= _ORG_TEXT_READ_MAX or len(text) >= max_chars:
            return text
        limit = _ORG_TEXT_READ_MAX


# ===========================================================================
# Office (OOXML / ODF) über zipfile
# ===========================================================================
_ORG_RE_XML_DROP = re.compile(
    rb"<(w:instrText|w:delText|mc:Fallback|w:fldData|w:rPr|w:pPr|a:rPr|a:pPr)\b[^>]*?(?:/>|>.*?</\1>)",
    re.S)
_ORG_RE_XML_BREAK = re.compile(
    rb"</(?:w:p|a:p|text:p|text:h|si|c|table:table-cell|w:tc|a:tc|text:list-item)>"
    rb"|<(?:w:tab|w:br|w:cr|a:br|text:tab|text:s|text:line-break)\b[^>]*>")
_ORG_RE_XML_TAG = re.compile(rb"<[^>]*>")
_ORG_RE_PPT_SLIDE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_ORG_RE_DOCX_HEADER = re.compile(r"^word/header(\d+)\.xml$")


def _org_zip_member_ok(info: zipfile.ZipInfo) -> bool:
    """Schutz vor Zip-Bomben und verschlüsselten Einträgen."""
    if info.flag_bits & 0x1:
        return False
    if info.file_size > _ORG_ZIP_MEMBER_MAX:
        return False
    if info.file_size > 64 * 1024:
        if info.compress_size <= 0:
            return False
        if info.file_size / float(info.compress_size) > _ORG_ZIP_RATIO_MAX:
            return False
    return True


def _org_zip_read(zf: zipfile.ZipFile, name: str, limit: int = _ORG_ZIP_XML_READ) -> bytes:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return b""
    if not _org_zip_member_ok(info):
        return b""
    try:
        with zf.open(info) as m:
            return m.read(limit)
    except Exception:
        return b""


def _org_xml_text(raw: bytes) -> str:
    if not raw:
        return ""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):          # XML in UTF-16 (selten)
        raw = raw[:len(raw) - len(raw) % 2].decode("utf-16", "replace").encode("utf-8")
    raw = _ORG_RE_XML_DROP.sub(b"", raw)
    raw = _ORG_RE_XML_BREAK.sub(b" ", raw)
    raw = _ORG_RE_XML_TAG.sub(b"", raw)
    return html.unescape(raw.decode("utf-8", "replace"))


_ORG_RE_OOXML_REL = re.compile(rb"<(?:\w+:)?Relationship\b[^>]*>")
_ORG_RE_OOXML_TARGET = re.compile(rb"Target=[\"']/?([^\"']+)[\"']")


def _org_ooxml_main(zf: zipfile.ZipFile, names, default: str) -> str:
    """Hauptteil laut _rels/.rels (manche Programme nennen ihn document2.xml o. ä.)."""
    if default in names:
        return default
    for m in _ORG_RE_OOXML_REL.finditer(_org_zip_read(zf, "_rels/.rels", 64 * 1024)):
        tag = m.group(0)
        if b"/officeDocument\"" not in tag and b"/officeDocument'" not in tag:
            continue
        t = _ORG_RE_OOXML_TARGET.search(tag)
        if t:
            name = t.group(1).decode("utf-8", "replace")
            if name in names:
                return name
    return default


def _org_office_text(path, ext: str, max_chars: int) -> str:
    # XML-Lesemenge an max_chars koppeln (Formatierungs-XML ist ~20-50x länger als Text)
    xml_limit = min(_ORG_ZIP_XML_READ, max(512 * 1024, max_chars * 100))
    try:
        with open(path, "rb") as fh:
            with zipfile.ZipFile(fh) as zf:
                names = zf.namelist()
                parts = []
                if ext in ORG_EXTRACT_WORD_EXTS:
                    heads = sorted((int(m.group(1)), n) for n in names
                                   for m in [_ORG_RE_DOCX_HEADER.match(n)] if m)
                    for _, n in heads[:2]:
                        parts.append(_org_xml_text(_org_zip_read(zf, n, 256 * 1024))[:300])
                    main = _org_ooxml_main(zf, set(names), "word/document.xml")
                    parts.append(_org_xml_text(_org_zip_read(zf, main, xml_limit)))
                elif ext in ORG_EXTRACT_XL_EXTS:
                    t = _org_xml_text(_org_zip_read(zf, "xl/sharedStrings.xml", xml_limit))
                    if not t.strip():
                        # ohne gemeinsame Zeichenketten (inlineStr): erstes Blatt lesen
                        t = _org_xml_text(_org_zip_read(zf, "xl/worksheets/sheet1.xml",
                                                        xml_limit))
                    parts.append(t)
                elif ext in ORG_EXTRACT_PPT_EXTS:
                    slides = sorted((int(m.group(1)), n) for n in names
                                    for m in [_ORG_RE_PPT_SLIDE.match(n)] if m)
                    total = 0
                    for _, n in slides[:5]:
                        t = _org_xml_text(_org_zip_read(zf, n, 1024 * 1024))
                        parts.append(t)
                        total += len(t)
                        if total > max_chars * 2:
                            break
                elif ext in ORG_EXTRACT_ODF_EXTS:
                    parts.append(_org_xml_text(_org_zip_read(zf, "content.xml", xml_limit)))
                return " ".join(p for p in parts if p)
    except Exception:
        return ""


# ===========================================================================
# RTF
# ===========================================================================
_ORG_RE_RTF = re.compile(
    rb"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z'])|([{}])|[\r\n]+|([^\\{}\r\n]+)")
_ORG_RTF_SKIP = frozenset({
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "objdata", "themedata",
    "colorschememapping", "datastore", "latentstyles", "rsidtbl", "listtable",
    "listoverridetable", "generator", "xmlnstbl", "mmathPr", "fldinst", "filetbl",
    "revtbl", "xe", "bkmkstart", "bkmkend", "pgdsctbl", "wgrffmtfilter", "operator",
    "shppict", "nonshppict", "blipuid", "factoidname", "sp", "sn", "sv", "private",
    "listtext", "pntext", "pntxtb", "pntxta", "userprops", "docvar", "template",
    "footnote", "annotation", "atnid", "atnauthor", "header", "footer", "headerl",
    "headerr", "headerf", "footerl", "footerr", "footerf", "passwordhash"})
_ORG_RTF_BREAK = frozenset({"par", "line", "tab", "cell", "row", "sect", "page",
                            "emspace", "enspace", "qmspace"})
_ORG_RTF_CHARS = {"emdash": "\u2014", "endash": "\u2013", "bullet": "\u2022",
                  "lquote": "\u2018", "rquote": "\u2019", "ldblquote": "\u201c",
                  "rdblquote": "\u201d"}


def _org_rtf_text(path, max_chars: int) -> str:
    data = _org_read(path, _ORG_RTF_READ_MAX)
    if not data.lstrip().startswith(b"{\\rtf"):
        return ""
    out = []
    count = 0
    stack = []
    ignorable = False
    ucskip = 1
    curskip = 0
    cp = "cp1252"
    limit = max_chars * 2 + 200
    for m in _ORG_RE_RTF.finditer(data):
        word, arg, hexc, sym, brace, text = m.groups()
        if brace is not None:
            curskip = 0
            if brace == b"{":
                if len(stack) < 1000:
                    stack.append((ignorable, ucskip))
            elif stack:
                ignorable, ucskip = stack.pop()
        elif sym is not None:
            curskip = 0
            if sym == b"*":
                ignorable = True
            elif ignorable:
                continue
            elif sym == b"~":
                out.append(" ")
            elif sym in (b"{", b"}", b"\\"):
                out.append(sym.decode("latin-1"))
            elif sym in (b"\r", b"\n"):
                out.append(" ")
            elif sym == b"_":
                out.append("-")
        elif word is not None:
            w = word.decode("ascii")
            if w in _ORG_RTF_SKIP:
                ignorable = True
                curskip = 0
                continue
            if w == "ansicpg" and arg:
                try:
                    codecs.lookup("cp" + arg.decode("ascii"))
                    cp = "cp" + arg.decode("ascii")
                except (LookupError, UnicodeDecodeError):
                    pass
            elif w == "mac":
                cp = "mac_roman"
            elif w == "uc" and arg:
                ucskip = max(0, min(int(arg), 8))
            if ignorable:
                continue
            if w == "u" and arg:
                n = int(arg)
                if n < 0:
                    n += 65536
                if 0 <= n < 0x110000:
                    out.append(chr(n))
                    count += 1
                curskip = ucskip
                continue
            curskip = 0
            if w in _ORG_RTF_BREAK:
                out.append(" ")
            elif w in _ORG_RTF_CHARS:
                out.append(_ORG_RTF_CHARS[w])
        elif hexc is not None:
            if curskip > 0:
                curskip -= 1
            elif not ignorable:
                out.append(bytes([int(hexc, 16)]).decode(cp, "replace"))
                count += 1
        elif text is not None:
            if curskip > 0:
                cut = min(curskip, len(text))
                text = text[cut:]
                curskip -= cut
            if text and not ignorable:
                out.append(text.decode(cp, "replace"))
                count += len(text)
        if count > limit:
            break
    return "".join(out)


# ===========================================================================
# PDF (reiner Python-Leser, nur zlib)
# ===========================================================================
class _org_PdfName(str):
    """PDF-Name (/Name) – unterscheidet sich so von Zeichenketten (bytes)."""
    __slots__ = ()


class _org_PdfRef(tuple):
    """Indirekte Referenz (Nummer, Generation)."""
    __slots__ = ()


_ORG_PDF_WSB = b"\x00\t\n\x0c\r "
_ORG_RE_PDF_WS = re.compile(rb"(?:[\x00\t\n\x0c\r ]+|%[^\r\n]*)*")
_ORG_RE_PDF_NUM = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)")
_ORG_RE_PDF_REFTAIL = re.compile(rb"[\x00\t\n\x0c\r ]+(\d{1,5})[\x00\t\n\x0c\r ]+R(?![A-Za-z0-9_])")
_ORG_RE_PDF_NAME = re.compile(rb"/([^\x00\t\n\x0c\r ()<>\[\]{}/%]*)")
_ORG_RE_PDF_KW = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*")
_ORG_RE_PDF_NAMEHEX = re.compile(rb"#([0-9A-Fa-f]{2})")
_ORG_RE_PDF_LITSPECIAL = re.compile(rb"[()\\]")
_ORG_RE_PDF_OBJ = re.compile(rb"(?:(?<=[\x00\t\n\x0c\r >])|\A)(\d{1,10})[\x00\t\n\x0c\r ]+(\d{1,5})[\x00\t\n\x0c\r ]+obj(?![A-Za-z])")
_ORG_RE_PDF_STREAMKW = re.compile(rb"[\x00\t\n\x0c\r ]*stream(?:\r\n|\n|\r)?")
_ORG_RE_PDF_ROOT = re.compile(rb"/Root[\x00\t\n\x0c\r ]+(\d{1,10})[\x00\t\n\x0c\r ]+\d{1,5}[\x00\t\n\x0c\r ]+R")
_ORG_RE_PDF_ENCRYPT = re.compile(rb"/Encrypt[\x00\t\n\x0c\r ]*(?:\d+[\x00\t\n\x0c\r ]+\d+[\x00\t\n\x0c\r ]+R|<<)")
_ORG_RE_PDF_XREFTYPE = re.compile(rb"/Type[\x00\t\n\x0c\r ]*/XRef(?![A-Za-z])")
_ORG_RE_PDF_TYPEPAGE = re.compile(rb"/Type[\x00\t\n\x0c\r ]*/Page(?![A-Za-z])")
_ORG_RE_PDF_TYPEPAGES = re.compile(rb"/Type[\x00\t\n\x0c\r ]*/Pages(?![A-Za-z])")
_ORG_RE_PDF_LINEAR = re.compile(rb"/Linearized\b[^>]{0,400}?/N[\x00\t\n\x0c\r ]+(\d{1,7})")
_ORG_RE_PDF_EI = re.compile(rb"[\x00\t\n\x0c\r ]EI(?=[\x00\t\n\x0c\r ]|$)")
_ORG_RE_PDF_CTOK = re.compile(
    rb"(?P<ws>[\x00\t\n\x0c\r ]+|%[^\r\n]*)"
    rb"|(?P<num>[+-]?(?:\d+\.?\d*|\.\d+))"
    rb"|(?P<name>/[^\x00\t\n\x0c\r ()<>\[\]{}/%]*)"
    rb"|(?P<dopen><<)|(?P<dclose>>>)"
    rb"|(?P<hex><[0-9A-Fa-f\x00\t\n\x0c\r ]*>)"
    rb"|(?P<aopen>\[)|(?P<aclose>\])"
    rb"|(?P<lit>\()"
    rb"|(?P<op>[A-Za-z'\"*][A-Za-z0-9'\"*]*)"
    rb"|(?P<other>.)", re.S)
_ORG_PDF_ESC = {0x6E: b"\n", 0x72: b"\r", 0x74: b"\t", 0x62: b"\b", 0x66: b"\f",
                0x28: b"(", 0x29: b")", 0x5C: b"\\"}

# WinAnsi (cp1252) und MacRoman als Grundtabellen für einfache Fonts
_ORG_PDF_WINANSI = tuple(
    ("" if c == "\ufffd" else c) for c in
    (bytes([i]).decode("cp1252", "replace") for i in range(256)))
_ORG_PDF_MACROMAN = tuple(bytes([i]).decode("mac_roman", "replace") for i in range(256))
# häufige Glyphennamen aus /Differences (inkl. deutscher Umlaute)
_ORG_PDF_GLYPHS = {
    "space": " ", "exclam": "!", "quotedbl": '"', "numbersign": "#", "dollar": "$",
    "percent": "%", "ampersand": "&", "quotesingle": "'", "quoteright": "\u2019",
    "quoteleft": "\u2018", "parenleft": "(", "parenright": ")", "asterisk": "*",
    "plus": "+", "comma": ",", "hyphen": "-", "minus": "\u2212", "period": ".",
    "slash": "/", "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "colon": ":",
    "semicolon": ";", "less": "<", "equal": "=", "greater": ">", "question": "?",
    "at": "@", "bracketleft": "[", "backslash": "\\", "bracketright": "]",
    "underscore": "_", "braceleft": "{", "bar": "|", "braceright": "}",
    "adieresis": "\u00e4", "odieresis": "\u00f6", "udieresis": "\u00fc",
    "Adieresis": "\u00c4", "Odieresis": "\u00d6", "Udieresis": "\u00dc",
    "germandbls": "\u00df", "eacute": "\u00e9", "egrave": "\u00e8", "agrave": "\u00e0",
    "aacute": "\u00e1", "ccedilla": "\u00e7", "Eacute": "\u00c9", "ecircumflex": "\u00ea",
    "endash": "\u2013", "emdash": "\u2014", "quotedblleft": "\u201c",
    "quotedblright": "\u201d", "quotedblbase": "\u201e", "quotesinglbase": "\u201a",
    "bullet": "\u2022", "ellipsis": "\u2026", "Euro": "\u20ac", "section": "\u00a7",
    "degree": "\u00b0", "fi": "fi", "fl": "fl", "ff": "ff", "ffi": "ffi", "ffl": "ffl",
    "multiply": "\u00d7", "copyright": "\u00a9", "registered": "\u00ae",
    "trademark": "\u2122", "sterling": "\u00a3", "paragraph": "\u00b6",
    "nbspace": " ", "uni00A0": " ", "guillemotleft": "\u00ab", "guillemotright": "\u00bb",
}
_ORG_PDF_UCS2_ENCODINGS = frozenset({"UniGB-UCS2-H", "UniJIS-UCS2-H", "UniKS-UCS2-H",
                                     "UniCNS-UCS2-H", "UniJIS-UTF16-H", "UniGB-UTF16-H"})


def _org_pdf_skip(raw: bytes) -> str:
    return ""


def _org_pdf_winansi(raw: bytes) -> str:
    t = _ORG_PDF_WINANSI
    return "".join([t[c] for c in raw])


def _org_pdf_literal(data: bytes, pos: int):
    """Literal-String ab '(' bei pos → (bytes, Endposition). Escapes inkl. Oktal."""
    i = pos + 1
    n = len(data)
    depth = 1
    out = bytearray()
    while i < n:
        m = _ORG_RE_PDF_LITSPECIAL.search(data, i)
        if m is None:
            out += data[i:]
            return bytes(out), n
        j = m.start()
        out += data[i:j]
        c = data[j]
        if c == 0x5C:
            if j + 1 >= n:
                return bytes(out), n
            e = data[j + 1]
            if e in _ORG_PDF_ESC:
                out += _ORG_PDF_ESC[e]
                i = j + 2
            elif 0x30 <= e <= 0x37:
                k = j + 1
                v = 0
                while k < n and k < j + 4 and 0x30 <= data[k] <= 0x37:
                    v = v * 8 + (data[k] - 0x30)
                    k += 1
                out.append(v & 0xFF)
                i = k
            elif e == 0x0D:
                i = j + 3 if data[j + 2:j + 3] == b"\n" else j + 2
            elif e == 0x0A:
                i = j + 2
            else:
                out.append(e)
                i = j + 2
        elif c == 0x28:
            depth += 1
            out.append(c)
            i = j + 1
        else:
            depth -= 1
            if depth == 0:
                return bytes(out), j + 1
            out.append(c)
            i = j + 1
    return bytes(out), n


def _org_pdf_name_decode(raw: bytes) -> str:
    if b"#" in raw:
        raw = _ORG_RE_PDF_NAMEHEX.sub(lambda m: bytes([int(m.group(1), 16)]), raw)
    return raw.decode("latin-1")


def _org_pdf_value(data: bytes, pos: int, depth: int = 0):
    """Ein PDF-Objekt ab pos parsen → (Wert, Endposition). Nie Ausnahme."""
    n = len(data)
    pos = _ORG_RE_PDF_WS.match(data, pos).end()
    if pos >= n or depth > 40:
        return None, pos
    c = data[pos]
    if c == 0x2F:                                             # /Name
        m = _ORG_RE_PDF_NAME.match(data, pos)
        return _org_PdfName(_org_pdf_name_decode(m.group(1))), m.end()
    if c == 0x3C:                                             # << oder <hex>
        if data[pos + 1:pos + 2] == b"<":
            d = {}
            pos += 2
            for _ in range(20000):
                pos = _ORG_RE_PDF_WS.match(data, pos).end()
                if pos >= n:
                    break
                if data[pos:pos + 2] == b">>":
                    pos += 2
                    break
                if data[pos] != 0x2F:
                    _, np = _org_pdf_value(data, pos, depth + 1)
                    pos = np if np > pos else pos + 1
                    continue
                m = _ORG_RE_PDF_NAME.match(data, pos)
                key = _org_pdf_name_decode(m.group(1))
                v, np = _org_pdf_value(data, m.end(), depth + 1)
                d[key] = v
                pos = np if np > m.end() else m.end() + 1
            return d, pos
        end = data.find(b">", pos)
        if end < 0:
            end = n
        return _org_unhex(data[pos + 1:end]), end + 1
    if c == 0x5B:                                             # [ Array ]
        arr = []
        pos += 1
        for _ in range(200000):
            pos = _ORG_RE_PDF_WS.match(data, pos).end()
            if pos >= n:
                break
            if data[pos] == 0x5D:
                pos += 1
                break
            v, np = _org_pdf_value(data, pos, depth + 1)
            if np <= pos:
                pos += 1
                continue
            arr.append(v)
            pos = np
        return arr, pos
    if c == 0x28:
        return _org_pdf_literal(data, pos)
    m = _ORG_RE_PDF_NUM.match(data, pos)
    if m:
        tok = m.group()
        if b"." not in tok:
            iv = int(tok)
            r = _ORG_RE_PDF_REFTAIL.match(data, m.end())
            if r and iv >= 0:
                return _org_PdfRef((iv, int(r.group(1)))), r.end()
            return iv, m.end()
        try:
            return float(tok), m.end()
        except ValueError:
            return 0.0, m.end()
    m = _ORG_RE_PDF_KW.match(data, pos)
    if m:
        kw = m.group()
        if kw == b"true":
            return True, m.end()
        if kw == b"false":
            return False, m.end()
        return None, m.end()
    return None, pos + 1


def _org_pdf_inflate(raw: bytes, limit: int):
    try:
        return zlib.decompressobj().decompress(raw, limit)
    except zlib.error:
        pass
    # beschädigter Strom (oder roher Deflate ohne zlib-Kopf): so viel wie möglich retten
    best = None
    for wbits in (15, -15):
        d = zlib.decompressobj(wbits)
        out = bytearray()
        for i in range(0, len(raw), 512):
            room = limit - len(out)
            if room <= 0:
                break
            try:
                out += d.decompress(raw[i:i + 512], room)
            except zlib.error:
                break
        if out and (best is None or len(out) > len(best)):
            best = bytes(out)
    return best


def _org_pdf_runlength(raw: bytes, limit: int) -> bytes:
    out = bytearray()
    i = 0
    n = len(raw)
    while i < n and len(out) < limit:
        b = raw[i]
        if b == 128:
            break
        if b < 128:
            out += raw[i + 1:i + 2 + b]
            i += 2 + b
        else:
            if i + 1 >= n:
                break
            out += raw[i + 1:i + 2] * (257 - b)
            i += 2
    return bytes(out[:limit])


def _org_cmap_parse(data: bytes):
    """ToUnicode-CMap → (maps {Länge: {Code: Text}}, große Bereiche, Codelängen)."""
    maps = {}
    ranges = {}
    lens = set()

    def dst_text(raw: bytes) -> str:
        if len(raw) == 1:
            return chr(raw[0])
        if len(raw) % 2:
            raw = raw[:-1]
        return raw.decode("utf-16-be", "replace")

    for blk in re.finditer(rb"begincodespacerange(.*?)endcodespacerange", data, re.S):
        for lo in re.finditer(rb"<([0-9A-Fa-f\s]+)>\s*<[0-9A-Fa-f\s]+>", blk.group(1)):
            L = len(_org_unhex(lo.group(1)))
            if 1 <= L <= 4:
                lens.add(L)
    for blk in re.finditer(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for m in re.finditer(rb"<([0-9A-Fa-f\s]+)>\s*(?:<([0-9A-Fa-f\s]*)>|/([^\s<\[/]+))",
                             blk.group(1)):
            src = _org_unhex(m.group(1))
            L = len(src)
            if not 1 <= L <= 4:
                continue
            if m.group(2) is not None:
                txt = dst_text(_org_unhex(m.group(2)))
            else:
                txt = _ORG_PDF_GLYPHS.get(m.group(3).decode("latin-1"), "")
            maps.setdefault(L, {})[int.from_bytes(src, "big")] = txt
            lens.add(L)
    for blk in re.finditer(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for m in re.finditer(
                rb"<([0-9A-Fa-f\s]+)>\s*<([0-9A-Fa-f\s]+)>\s*(?:<([0-9A-Fa-f\s]*)>|\[([^\]]*)\])",
                blk.group(1)):
            lo_b = _org_unhex(m.group(1))
            L = len(lo_b)
            if not 1 <= L <= 4:
                continue
            lo = int.from_bytes(lo_b, "big")
            hi = int.from_bytes(_org_unhex(m.group(2)), "big")
            if hi < lo or hi - lo > 0xFFFF:
                continue
            lens.add(L)
            mp = maps.setdefault(L, {})
            if m.group(4) is not None:
                for k, item in enumerate(re.findall(rb"<([0-9A-Fa-f\s]*)>", m.group(4))):
                    if lo + k > hi:
                        break
                    mp[lo + k] = dst_text(_org_unhex(item))
                continue
            base = dst_text(_org_unhex(m.group(3)))
            if not base:
                continue
            if hi - lo <= 256:
                head, last = base[:-1], ord(base[-1])
                for k in range(hi - lo + 1):
                    if last + k < 0x110000:
                        mp[lo + k] = head + chr(last + k)
            else:
                ranges.setdefault(L, []).append((lo, hi, base))
    return maps, ranges, sorted(lens, reverse=True) or [1]


def _org_cmap_decode(cmap, raw: bytes, base) -> str:
    maps, ranges, lens = cmap
    out = []
    i = 0
    n = len(raw)
    minlen = lens[-1]
    while i < n:
        for L in lens:
            if i + L > n:
                continue
            code = int.from_bytes(raw[i:i + L], "big")
            s = maps.get(L, {}).get(code)
            if s is None:
                for lo, hi, dst in ranges.get(L, ()):
                    if lo <= code <= hi:
                        c = ord(dst[-1]) + code - lo
                        s = dst[:-1] + chr(c) if c < 0x110000 else ""
                        break
            if s is not None:
                out.append(s)
                i += L
                break
        else:
            if base is not None:
                out.append(base[raw[i]])
                i += 1
            else:
                i += minlen
    return "".join(out)


class _org_PdfDoc:
    """Minimaler, fehlertoleranter PDF-Leser über einem Byte-Puffer (je Aufruf
    eine eigene Instanz → threadsicher)."""

    def __init__(self, data: bytes):
        self.data = data
        self.offsets = {}
        for m in _ORG_RE_PDF_OBJ.finditer(data):
            self.offsets[int(m.group(1))] = m.end()   # spätere Fassung gewinnt
        self.compressed = {}
        self._objstm_done = False
        self._cache = {}
        self._endpos = {}
        self._streams = {}
        self._fonts = {}
        self._inflated = 0
        self.encrypted = _org_pdf_is_encrypted(data)
        # Zustand der Textausgabe
        self._sep = ""
        self._last_len = 0
        self._tm_y = None
        self._count = 0
        self._limit = 0

    # ----- Objekte -----
    def obj(self, num):
        if num in self._cache:
            return self._cache[num]
        self._cache[num] = None                       # Schutz vor Zyklen
        val = None
        off = self.offsets.get(num)
        if off is not None:
            val, end = _org_pdf_value(self.data, off)
            self._endpos[num] = end
        else:
            self._load_objstm()
            comp = self.compressed.get(num)
            if comp is not None:
                val, _ = _org_pdf_value(comp[0], comp[1])
        self._cache[num] = val
        return val

    def resolve(self, v):
        for _ in range(8):
            if isinstance(v, _org_PdfRef):
                v = self.obj(v[0])
            else:
                break
        return v if not isinstance(v, _org_PdfRef) else None

    def stream(self, num):
        if num in self._streams:
            return self._streams[num]
        self._streams[num] = None
        d = self.obj(num)
        if not isinstance(d, dict) or num not in self._endpos:
            return None
        data = self.data
        m = _ORG_RE_PDF_STREAMKW.match(data, self._endpos[num])
        if m is None:
            return None
        start = m.end()
        raw = None
        length = self.resolve(d.get("Length"))
        if isinstance(length, int) and 0 <= length <= len(data) - start:
            tail = data[start + length:start + length + 40].lstrip(_ORG_PDF_WSB)
            if tail.startswith(b"endstream"):
                raw = data[start:start + length]
        if raw is None:
            e = data.find(b"endstream", start)
            raw = data[start:e] if e >= 0 else data[start:]
            if raw.endswith(b"\r\n"):
                raw = raw[:-2]
            elif raw.endswith(b"\n") or raw.endswith(b"\r"):
                raw = raw[:-1]
        out = self._decode(raw, d)
        self._streams[num] = out
        return out

    def _decode(self, raw: bytes, d: dict):
        f = self.resolve(d.get("Filter"))
        if f is None:
            filters = []
        elif isinstance(f, list):
            filters = [self.resolve(x) for x in f]
        else:
            filters = [f]
        out = raw
        for fl in filters:
            room = min(_ORG_PDF_STREAM_MAX, _ORG_PDF_TOTAL_MAX - self._inflated)
            if room <= 0:
                return None
            if fl in ("FlateDecode", "Fl"):
                out = _org_pdf_inflate(out, room)
            elif fl in ("ASCIIHexDecode", "AHx"):
                e = out.find(b">")
                out = _org_unhex(out[:e] if e >= 0 else out)
            elif fl in ("ASCII85Decode", "A85"):
                e = out.find(b"~>")
                body = (out[:e] if e >= 0 else out).strip()
                if body.startswith(b"<~"):
                    body = body[2:]
                try:
                    out = base64.a85decode(b"<~" + body + b"~>", adobe=True)
                except (ValueError, binascii.Error):
                    return None
            elif fl in ("RunLengthDecode", "RL"):
                out = _org_pdf_runlength(out, room)
            else:
                return None                           # Bildfilter u. a. – kein Text
            if out is None:
                return None
            self._inflated += len(out)
        return out

    def _load_objstm(self):
        if self._objstm_done:
            return
        self._objstm_done = True
        seen = 0
        for num, off in list(self.offsets.items()):
            if b"/ObjStm" not in self.data[off:off + 600]:
                continue
            seen += 1
            if seen > 300:
                break
            d = self.obj(num)
            if not isinstance(d, dict) or d.get("Type") != "ObjStm":
                continue
            buf = self.stream(num)
            first = self.resolve(d.get("First"))
            count = self.resolve(d.get("N"))
            if not buf or not isinstance(first, int) or not isinstance(count, int):
                continue
            if not 0 < first <= len(buf):
                continue
            ints = [int(x) for x in re.findall(rb"\d+", buf[:first])]
            for k in range(0, min(len(ints) // 2, count, 100000) * 2, 2):
                onum, ooff = ints[k], ints[k + 1]
                if onum not in self.offsets and onum not in self.compressed:
                    if first + ooff < len(buf):
                        self.compressed[onum] = (buf, first + ooff)

    def _typed_objects(self, regex, max_parse: int = 3000):
        """Objekte, deren Text zum Muster passt (direkt und aus Objektströmen)."""
        res = []
        data = self.data
        for num, off in sorted(self.offsets.items(), key=lambda kv: kv[1]):
            if regex.search(data, off, off + 400):
                res.append(num)
                if len(res) >= max_parse:
                    return res
        self._load_objstm()
        for num in sorted(self.compressed):
            buf, off = self.compressed[num]
            if regex.search(buf, off, off + 400):
                res.append(num)
                if len(res) >= max_parse:
                    break
        return res

    def root(self):
        last = None
        for m in _ORG_RE_PDF_ROOT.finditer(self.data):
            last = m
        if last is not None:
            cat = self.obj(int(last.group(1)))
            if isinstance(cat, dict):
                return cat
        for num in self._typed_objects(re.compile(rb"/Type[\x00\t\n\x0c\r ]*/Catalog"), 20):
            cat = self.obj(num)
            if isinstance(cat, dict):
                return cat
        return None

    # ----- Seiten -----
    def pages(self, limit: int):
        result = []
        cat = self.root()
        top = self.resolve(cat.get("Pages")) if isinstance(cat, dict) else None
        if isinstance(top, dict):
            stack = [(top, None)]
            visited = set()
            steps = 0
            while stack and len(result) < limit and steps < 20000:
                steps += 1
                node, inherited = stack.pop()
                if not isinstance(node, dict):
                    continue
                res = node.get("Resources", inherited)
                kids = self.resolve(node.get("Kids"))
                if isinstance(kids, list) and node.get("Type") != "Page":
                    for kid in reversed(kids):
                        if isinstance(kid, _org_PdfRef):
                            if kid[0] in visited:
                                continue
                            visited.add(kid[0])
                        stack.append((self.resolve(kid), res))
                elif "Contents" in node or node.get("Type") == "Page":
                    result.append((node, res))
        if not result:                                 # Seitenbaum fehlt/abgeschnitten
            for num in self._typed_objects(_ORG_RE_PDF_TYPEPAGE, 500):
                node = self.obj(num)
                if isinstance(node, dict) and node.get("Type") == "Page":
                    result.append((node, node.get("Resources")))
                    if len(result) >= limit:
                        break
        return result

    def page_count(self):
        cat = self.root()
        top = self.resolve(cat.get("Pages")) if isinstance(cat, dict) else None
        if isinstance(top, dict):
            c = self.resolve(top.get("Count"))
            if isinstance(c, int) and 0 < c < 1000000:
                return c
        m = _ORG_RE_PDF_LINEAR.search(self.data, 0, 4096)
        if m and 0 < int(m.group(1)) < 1000000:
            return int(m.group(1))
        best = 0
        for num in self._typed_objects(_ORG_RE_PDF_TYPEPAGES, 2000):
            node = self.obj(num)
            if isinstance(node, dict) and node.get("Type") == "Pages":
                c = self.resolve(node.get("Count"))
                if isinstance(c, int) and best < c < 1000000:
                    best = c
        if best:
            return best
        cnt = len(set(self._typed_objects(_ORG_RE_PDF_TYPEPAGE, 100000)))
        return cnt or None

    # ----- Fonts -----
    def font_decoder(self, fonts, name):
        if not isinstance(fonts, dict):
            return None
        ref = fonts.get(name)
        key = ref[0] if isinstance(ref, _org_PdfRef) else None
        if key is not None and key in self._fonts:
            return self._fonts[key]
        dec = self._make_decoder(self.resolve(ref))
        if key is not None:
            self._fonts[key] = dec
        return dec

    def _make_decoder(self, fd):
        if not isinstance(fd, dict):
            return None
        cmap = None
        tu = fd.get("ToUnicode")
        if isinstance(tu, _org_PdfRef):
            raw = self.stream(tu[0])
            if raw:
                parsed = _org_cmap_parse(raw)
                if parsed[0] or parsed[1]:
                    cmap = parsed
        if fd.get("Subtype") == "Type0":
            if cmap is not None:
                return lambda b, c=cmap: _org_cmap_decode(c, b, None)
            enc = self.resolve(fd.get("Encoding"))
            if enc in _ORG_PDF_UCS2_ENCODINGS:
                return lambda b: b[:len(b) - len(b) % 2].decode("utf-16-be", "replace")
            return _org_pdf_skip                       # Glyphen-IDs ohne Zuordnung
        enc = self.resolve(fd.get("Encoding"))
        table = _ORG_PDF_WINANSI
        diffs = None
        if enc == "MacRomanEncoding":
            table = _ORG_PDF_MACROMAN
        elif isinstance(enc, dict):
            if self.resolve(enc.get("BaseEncoding")) == "MacRomanEncoding":
                table = _ORG_PDF_MACROMAN
            diffs = self.resolve(enc.get("Differences"))
        if isinstance(diffs, list):
            table = list(table)
            code = 0
            for item in diffs[:2000]:
                if isinstance(item, int) and not isinstance(item, bool):
                    code = item
                elif isinstance(item, _org_PdfName):
                    if 0 <= code < 256:
                        ch = _org_pdf_glyph(item)
                        if ch is not None:
                            table[code] = ch
                    code += 1
            table = tuple(table)
        if cmap is not None:
            return lambda b, c=cmap, t=table: _org_cmap_decode(c, b, t)
        if table is _ORG_PDF_WINANSI:
            return _org_pdf_winansi
        return lambda b, t=table: "".join([t[c] for c in b])

    # ----- Text -----
    def _emit(self, s: str, out: list):
        if not s:
            return
        sep = self._sep
        if out and (sep in ("line", "word") or
                    (sep == "same" and not (self._last_len <= 1 and len(s) <= 1))):
            out.append(" ")
        self._sep = ""
        out.append(s)
        self._last_len = len(s.strip())
        self._count += len(s)

    def _soft(self, kind: str):
        if self._sep != "line":
            self._sep = kind

    def run_content(self, content: bytes, resources, out: list, depth: int = 0):
        res = self.resolve(resources)
        res = res if isinstance(res, dict) else {}
        fonts = self.resolve(res.get("Font"))
        xobjs = None
        decoder = None
        ops = []
        arrays = []
        pos = 0
        n = len(content)
        tok = _ORG_RE_PDF_CTOK
        forms = 0
        while pos < n:
            if self._count >= self._limit:
                return
            m = tok.match(content, pos)
            if m is None:
                pos += 1
                continue
            kind = m.lastgroup
            pos = m.end()
            if kind == "ws" or kind == "other" or kind == "dopen" or kind == "dclose":
                continue
            if kind == "num":
                try:
                    ops.append(float(m.group()))
                except ValueError:
                    ops.append(0.0)
            elif kind == "lit":
                s, pos = _org_pdf_literal(content, m.start())
                ops.append(s)
            elif kind == "hex":
                ops.append(_org_unhex(m.group()[1:-1]))
            elif kind == "name":
                ops.append(_org_PdfName(_org_pdf_name_decode(m.group()[1:])))
            elif kind == "aopen":
                if len(arrays) < 32:
                    arrays.append(ops)
                    ops = []
            elif kind == "aclose":
                if arrays:
                    arr = ops
                    ops = arrays.pop()
                    ops.append(arr)
            else:                                         # Operator
                op = m.group()
                if arrays:                                # kaputtes Array → verwerfen
                    ops = arrays[0]
                    arrays = []
                if op == b"Tf":
                    if len(ops) >= 2 and isinstance(ops[-2], _org_PdfName):
                        decoder = self.font_decoder(fonts, ops[-2])
                elif op == b"Tj" or op == b"'" or op == b'"':
                    if op != b"Tj":
                        self._sep = "line"
                    if ops and isinstance(ops[-1], bytes):
                        self._emit((decoder or _org_pdf_winansi)(ops[-1]), out)
                elif op == b"TJ":
                    if ops and isinstance(ops[-1], list):
                        dec = decoder or _org_pdf_winansi
                        for el in ops[-1]:
                            if isinstance(el, bytes):
                                self._emit(dec(el), out)
                            elif isinstance(el, float) and el < -200:
                                self._soft("word")
                elif op == b"Td" or op == b"TD":
                    if len(ops) >= 2 and isinstance(ops[-1], float):
                        if abs(ops[-1]) > 0.01:
                            self._sep = "line"
                        else:
                            self._soft("same")
                elif op == b"T*":
                    self._sep = "line"
                elif op == b"Tm":
                    if len(ops) >= 6 and isinstance(ops[-1], float):
                        y = ops[-1]
                        if self._tm_y is not None and abs(y - self._tm_y) < 0.5:
                            self._soft("same")
                        else:
                            self._sep = "line"
                        self._tm_y = y
                elif op == b"ET":
                    self._soft("same")
                elif op == b"BI":
                    e = _ORG_RE_PDF_EI.search(content, pos)
                    pos = e.end() if e else n
                elif op == b"Do":
                    if depth < 3 and forms < 30 and ops and isinstance(ops[-1], _org_PdfName):
                        forms += 1
                        if xobjs is None:
                            xobjs = self.resolve(res.get("XObject"))
                            if not isinstance(xobjs, dict):
                                xobjs = {}
                        ref = xobjs.get(ops[-1])
                        if isinstance(ref, _org_PdfRef):
                            xd = self.obj(ref[0])
                            if isinstance(xd, dict) and xd.get("Subtype") == "Form":
                                sub = self.stream(ref[0])
                                if sub:
                                    self.run_content(sub, xd.get("Resources", res), out,
                                                     depth + 1)
                ops = []
                continue
            if len(ops) > 5000:
                ops = ops[-50:]
        # abgeschnittener Strom: noch offene Zeichenketten trotzdem ausgeben
        pending = (arrays[0] if arrays else []) + ops
        for el in pending:
            items = el if isinstance(el, list) else [el]
            for it in items:
                if isinstance(it, bytes):
                    self._emit((decoder or _org_pdf_winansi)(it), out)

    def text(self, max_chars: int) -> str:
        if self.encrypted:
            return ""
        out = []
        self._count = 0
        self._limit = max_chars * 2 + 200
        pages = self.pages(8)
        if pages:
            for idx, (page, res) in enumerate(pages):
                if idx >= 3 and self._count >= 200:
                    break
                c = page.get("Contents")
                cr = self.resolve(c)
                if isinstance(cr, list):
                    refs = cr
                elif isinstance(c, _org_PdfRef):
                    refs = [c]
                else:
                    refs = []
                chunks = []
                for r in refs[:64]:
                    if isinstance(r, _org_PdfRef):
                        s = self.stream(r[0])
                        if s:
                            chunks.append(s)
                if chunks:
                    self.run_content(b"\n".join(chunks), res, out)
                self._sep = "line"
                if self._count >= self._limit:
                    break
        else:
            # kein Seitenbaum: Inhaltsströme direkt durchsuchen
            tried = 0
            for num, off in sorted(self.offsets.items(), key=lambda kv: kv[1]):
                if tried >= 40 or self._count >= self._limit:
                    break
                head = self.data[off:off + 300]
                if b"stream" not in head or b"/Type" in head.split(b"stream")[0]:
                    continue
                tried += 1
                s = self.stream(num)
                if s and b"BT" in s and (b"Tj" in s or b"TJ" in s):
                    self.run_content(s, None, out)
                    self._sep = "line"
        return "".join(out)


def _org_pdf_is_encrypted(data: bytes) -> bool:
    """/Encrypt nur im Trailer bzw. Querverweis-Strom werten – nicht, wenn das
    Wort zufällig im (unkomprimierten) Seitentext steht."""
    for m in _ORG_RE_PDF_ENCRYPT.finditer(data):
        s = m.start()
        before = data[max(0, s - 1024):s]
        lp = before.rfind(b"(")
        if lp > before.rfind(b")") and b"\n" not in before[lp:]:
            continue                                # steht in einem Text-Literal "( … )"
        if b"trailer" in before:
            return True
        win = data[max(0, s - 4096):s + 4096]
        if _ORG_RE_PDF_XREFTYPE.search(win) or b"/Root" in win:
            return True
    return False


def _org_pdf_glyph(name: str):
    if name in _ORG_PDF_GLYPHS:
        return _ORG_PDF_GLYPHS[name]
    base = name.split(".", 1)[0]
    if len(base) == 1:
        return base
    if base in _ORG_PDF_GLYPHS:
        return _ORG_PDF_GLYPHS[base]
    m = re.match(r"^(?:uni|u)([0-9A-Fa-f]{4,6})$", base)
    if m:
        v = int(m.group(1), 16)
        if v < 0x110000 and not 0xD800 <= v <= 0xDFFF:
            return chr(v)
    return None


def _org_pdf_load(path, head: int, tail: int):
    """Anfang + Ende einer PDF lesen (Seitenbaum liegt oft am Dateiende)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(0)
            if size <= head + tail:
                return fh.read(), size
            h = fh.read(head)
            fh.seek(size - tail)
            return h + b"\n" + fh.read(tail), size
    except (OSError, ValueError, TypeError, OverflowError):
        return b"", 0


def _org_pdf_text_qt(path, max_chars: int) -> str:
    """Rückfall über QtPdf (pdfium). Ohne QGuiApplication im Worker-Thread getestet;
    Aufrufe serialisiert. Liefert "" wenn QtPdf fehlt oder scheitert."""
    try:
        from PySide6.QtPdf import QPdfDocument
    except Exception:
        return ""
    with _org_qt_lock:
        doc = None
        try:
            doc = QPdfDocument(None)
            doc.load(str(path))
            pages = doc.pageCount()
            parts = []
            total = 0
            for i in range(min(pages, 3)):
                t = doc.getAllText(i).text()
                parts.append(t)
                total += len(t)
                if total >= max_chars:
                    break
            return " ".join(parts)
        except Exception:
            return ""
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
                del doc


def _org_pdf_text(path, max_chars: int, qt_path=None) -> str:
    data, size = _org_pdf_load(path, _ORG_PDF_HEAD, _ORG_PDF_TAIL)
    if not data:
        return ""
    text = ""
    try:
        text = _org_clean(_org_PdfDoc(data).text(max_chars), max_chars)
    except Exception:
        text = ""
    if len(text) < 20 and ORG_EXTRACT_USE_QTPDF and size <= ORG_EXTRACT_QTPDF_MAX_BYTES \
            and b"%PDF" in data[:1024]:
        qt = _org_clean(_org_pdf_text_qt(qt_path or path, max_chars), max_chars)
        if len(qt) > len(text):
            text = qt
    return text


# ===========================================================================
# Öffentliche Text-API
# ===========================================================================
def org_extract_text(path: Path, max_chars: int = 4000) -> str:
    """Die ersten max_chars lesbaren Zeichen (Leerraum zusammengefasst) oder ""."""
    try:
        if max_chars <= 0:
            return ""
        p = Path(path)
        ext = p.suffix.lower()
        office = ext in ORG_EXTRACT_WORD_EXTS or ext in ORG_EXTRACT_XL_EXTS \
            or ext in ORG_EXTRACT_PPT_EXTS or ext in ORG_EXTRACT_ODF_EXTS
        if not (office or ext in (".pdf", ".rtf") or ext in ORG_EXTRACT_TEXT_EXTS):
            return ""
        fp = _org_prepare(p)                   # Ordner/Pipe/Cloud-Platzhalter → ""
        if fp is None:
            return ""
        if ext == ".pdf":
            return _org_pdf_text(fp, max_chars, str(p))
        if office:
            return _org_clean(_org_office_text(fp, ext, max_chars), max_chars)
        if ext == ".rtf":
            return _org_clean(_org_rtf_text(fp, max_chars), max_chars)
        return _org_clean(_org_plain_text(fp, ext, max_chars), max_chars)
    except Exception:
        pass
    return ""


def org_pdf_page_count(path) -> int | None:
    """Seitenzahl (best effort) oder None."""
    try:
        path = _org_prepare(path)
        if path is None:
            return None
        for head in (1024 * 1024, 4 * 1024 * 1024):
            data, size = _org_pdf_load(path, head, 1024 * 1024)
            if not data:
                return None
            n = _org_PdfDoc(data).page_count()
            if n:
                return n
            if size <= head + 1024 * 1024:
                break
    except Exception:
        pass
    return None


# ===========================================================================
# EXIF
# ===========================================================================
_ORG_TIFF_TYPES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 13: 4}
_ORG_EXIF_IFD0 = frozenset({0x010F, 0x0110, 0x0131, 0x0132, 0x0112, 0x0100, 0x0101,
                            0x8769, 0x8825})
_ORG_EXIF_SUB = frozenset({0x9003, 0x9004, 0xA002, 0xA003})
_ORG_EXIF_GPS = frozenset({1, 2, 3, 4})
_ORG_RE_EXIF_DT = re.compile(r"^\s*(\d{4})[:\-./](\d{2})[:\-./](\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")


def _org_tiff_ifd(buf: bytes, e: str, off: int, wanted, visited: set) -> dict:
    """Ein IFD lesen (nur gewünschte Tags). off relativ zu buf[0]."""
    res = {}
    n = len(buf)
    if off < 8 or off + 2 > n or off in visited:
        return res
    visited.add(off)
    try:
        count = struct.unpack_from(e + "H", buf, off)[0]
    except struct.error:
        return res
    if count > 1000:
        return res
    for i in range(count):
        ent = off + 2 + 12 * i
        if ent + 12 > n:
            break
        tag, typ, cnt = struct.unpack_from(e + "HHI", buf, ent)
        if tag not in wanted:
            continue
        size = _ORG_TIFF_TYPES.get(typ)
        if not size or cnt == 0 or cnt > 4096:
            continue
        total = size * cnt
        if total <= 4:
            voff = ent + 8
        else:
            voff = struct.unpack_from(e + "I", buf, ent + 8)[0]
        if voff + total > n:
            continue
        raw = buf[voff:voff + total]
        try:
            if typ == 2:
                res[tag] = raw.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
            elif typ == 3:
                res[tag] = list(struct.unpack(e + "%dH" % cnt, raw))
            elif typ in (4, 13):
                res[tag] = list(struct.unpack(e + "%dI" % cnt, raw))
            elif typ == 9:
                res[tag] = list(struct.unpack(e + "%di" % cnt, raw))
            elif typ in (5, 10):
                fmt = e + ("%dI" if typ == 5 else "%di") % (2 * cnt)
                v = struct.unpack(fmt, raw)
                res[tag] = [(v[k], v[k + 1]) for k in range(0, len(v), 2)]
            else:
                res[tag] = raw
        except struct.error:
            continue
    return res


def _org_exif_dt(v) -> str:
    if not isinstance(v, str):
        return ""
    m = _ORG_RE_EXIF_DT.match(v.replace("\x00", ""))
    if not m:
        return ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh, mi, ss = (int(m.group(k) or 0) for k in (4, 5, 6))
    if not (1800 <= y <= 2200 and 1 <= mo <= 12 and 1 <= d <= 31
            and hh < 24 and mi < 60 and ss < 61):
        return ""
    return "%04d-%02d-%02d %02d:%02d:%02d" % (y, mo, d, hh, mi, ss)


def _org_gps_coord(vals, ref):
    if not isinstance(vals, list) or not vals:
        return None
    total = 0.0
    for k, (num, den) in enumerate(vals[:3]):
        if den == 0:
            if num == 0:
                continue
            return None
        total += (num / float(den)) / (60.0 ** k)
    if isinstance(ref, str) and ref[:1].upper() in ("S", "W"):
        total = -abs(total)                        # auch bei negativem SRATIONAL
    elif isinstance(ref, str) and ref[:1].upper() in ("N", "E"):
        total = abs(total)
    return total


def _org_tiff_parse(buf: bytes) -> dict:
    """TIFF-Struktur (II*/MM*) ab buf[0] → EXIF-Ergebnis-Dict."""
    if len(buf) < 8:
        return {}
    if buf[:2] == b"II":
        e = "<"
    elif buf[:2] == b"MM":
        e = ">"
    else:
        return {}
    magic, ifd0_off = struct.unpack_from(e + "HI", buf, 2)
    if magic not in (42, 0x4F52, 0x5352, 0x55):
        return {}
    visited = set()
    ifd0 = _org_tiff_ifd(buf, e, ifd0_off, _ORG_EXIF_IFD0, visited)
    sub = {}
    gps = {}
    ptr = ifd0.get(0x8769)
    if isinstance(ptr, list) and ptr:
        sub = _org_tiff_ifd(buf, e, ptr[0], _ORG_EXIF_SUB, visited)
    ptr = ifd0.get(0x8825)
    if isinstance(ptr, list) and ptr:
        gps = _org_tiff_ifd(buf, e, ptr[0], _ORG_EXIF_GPS, visited)
    out = {}
    for tag in (0x9003, 0x9004):
        dt = _org_exif_dt(sub.get(tag))
        if dt:
            out["datetime"] = dt
            break
    else:
        dt = _org_exif_dt(ifd0.get(0x0132))
        if dt:
            out["datetime"] = dt
    for tag, key in ((0x010F, "make"), (0x0110, "model"), (0x0131, "software")):
        v = ifd0.get(tag)
        if isinstance(v, str):
            v = _ORG_RE_CTRL.sub("", v).strip()
            if v:
                out[key] = v
    v = ifd0.get(0x0112)
    if isinstance(v, list) and v and 1 <= v[0] <= 8:
        out["orientation"] = v[0]
    for keys, src in (((0xA002, 0xA003), sub), ((0x0100, 0x0101), ifd0)):
        w, h = src.get(keys[0]), src.get(keys[1])
        if isinstance(w, list) and isinstance(h, list) and w and h and w[0] > 0 and h[0] > 0:
            out["width"], out["height"] = int(w[0]), int(h[0])
            break
    lat = _org_gps_coord(gps.get(2), gps.get(1))
    lon = _org_gps_coord(gps.get(4), gps.get(3))
    if lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180 \
            and not (lat == 0 and lon == 0):
        out["gps"] = (round(lat, 6), round(lon, 6))
    return out


def _org_exif_from_payload(payload: bytes) -> dict:
    if payload.startswith(b"Exif\x00\x00"):
        payload = payload[6:]
    return _org_tiff_parse(payload)


def _org_exif_jpeg(buf: bytes, short=None) -> dict:
    """short (Liste): erhält True, wenn der Puffer vor dem Bildbeginn endete
    (dann lohnt erneutes Lesen mit größerem Puffer)."""
    pos = 2
    n = len(buf)
    for _ in range(200):
        if pos + 4 > n:
            if short is not None:
                short.append(True)
            break
        if buf[pos] != 0xFF:
            nxt = buf.find(b"\xff", pos)                # Neusynchronisieren
            if nxt < 0:
                break
            pos = nxt
            continue
        marker = buf[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker in (0xD9, 0xDA):
            break
        seglen = struct.unpack_from(">H", buf, pos + 2)[0]
        if seglen < 2:
            break
        if marker == 0xE1 and buf[pos + 4:pos + 10] == b"Exif\x00\x00":
            if pos + 2 + seglen > n and short is not None:
                short.append(True)
                return {}
            res = _org_tiff_parse(buf[pos + 10:pos + 2 + seglen])
            if res:
                return res
        pos += 2 + seglen
    return {}


def _org_exif_png(buf: bytes, short=None) -> dict:
    pos = 8
    n = len(buf)
    for _ in range(10000):
        if pos + 8 > n:
            if short is not None:
                short.append(True)
            break
        length, ctype = struct.unpack_from(">I4s", buf, pos)
        if ctype == b"eXIf":
            if pos + 8 + length > n and short is not None:
                short.append(True)
                return {}
            return _org_exif_from_payload(buf[pos + 8:pos + 8 + length])
        if ctype == b"IEND":
            break
        pos += 12 + length
    return {}


def _org_exif_webp(buf: bytes) -> dict:
    pos = 12
    n = len(buf)
    for _ in range(1000):
        if pos + 8 > n:
            break
        ctype, length = struct.unpack_from("<4sI", buf, pos)
        if ctype == b"EXIF":
            return _org_exif_from_payload(buf[pos + 8:pos + 8 + length])
        pos += 8 + length + (length & 1)
    return {}


def _org_exif_search(buf: bytes) -> dict:
    """HEIC/HEIF/CR3 u. a.: EXIF-Block im Kopf suchen."""
    start = 0
    for _ in range(8):
        i = buf.find(b"Exif\x00\x00", start)
        if i < 0:
            break
        res = _org_tiff_parse(buf[i + 6:i + 6 + 1024 * 1024])
        if res:
            return res
        start = i + 6
    for sig in (b"II*\x00", b"MM\x00*"):
        start = 0
        for _ in range(8):
            i = buf.find(sig, start)
            if i < 0:
                break
            res = _org_tiff_parse(buf[i:i + 1024 * 1024])
            if res:
                return res
            start = i + 4
    return {}


def org_read_exif(path: Path) -> dict:
    """EXIF-Basisdaten: datetime, make, model, software, gps, orientation, width, height."""
    try:
        path = _org_prepare(path)
        if path is None:
            return {}
        # erst 128 KB (EXIF steht bei JPEG/PNG fast immer am Anfang), nur bei Bedarf 512 KB
        buf = _org_read(path, _ORG_EXIF_READ_FIRST)
        if len(buf) < 12:
            return {}
        full = len(buf) < _ORG_EXIF_READ_FIRST
        if buf[:3] == b"\xff\xd8\xff" or buf[:8] == b"\x89PNG\r\n\x1a\n":
            fn = _org_exif_jpeg if buf[:1] == b"\xff" else _org_exif_png
            if full:                               # ganze Datei gelesen
                return fn(buf)
            short = []
            res = fn(buf, short)
            if res or not short:
                return res
            return fn(_org_read(path, _ORG_IMG_HEAD))
        if not full:
            buf = _org_read(path, _ORG_IMG_HEAD)
        if buf[:4] in (b"II*\x00", b"MM\x00*", b"IIRO", b"IIU\x00"):
            return _org_tiff_parse(buf)
        if buf[:4] == b"RIFF" and buf[8:12] == b"WEBP":
            return _org_exif_webp(buf)
        if buf[4:8] == b"ftyp":
            return _org_exif_search(buf)
    except Exception:
        pass
    return {}


# ===========================================================================
# Bildgröße aus Dateikopf
# ===========================================================================
_ORG_JPEG_SOF = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB,
                           0xCD, 0xCE, 0xCF})


def _org_jpeg_size(fh):
    """Segmente überspringen (seek), höchstens wenige KB lesen."""
    pos = 2
    for _ in range(500):
        fh.seek(pos)
        hdr = fh.read(4)
        if len(hdr) < 2:
            return None
        if hdr[0] != 0xFF:
            # Füllbytes/Müll zwischen Segmenten: bis zum nächsten 0xFF vorspulen
            fh.seek(pos)
            chunk = fh.read(4096)
            k = chunk.find(b"\xff")
            if k <= 0:
                return None
            pos += k
            continue
        marker = hdr[1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker in (0xD9, 0xDA) or len(hdr) < 4:
            return None
        seglen = struct.unpack(">H", hdr[2:4])[0]
        if seglen < 2:
            return None
        if marker in _ORG_JPEG_SOF:
            d = fh.read(5)
            if len(d) < 5:
                return None
            h, w = struct.unpack(">xHH", d)
            return (w, h) if w > 0 and h > 0 else None
        pos += 2 + seglen
        if pos > 64 * 1024 * 1024:
            return None
    return None


def org_image_size(path: Path) -> tuple | None:
    """(Breite, Höhe) aus dem Dateikopf von JPEG/PNG/GIF/BMP/WebP, sonst None."""
    try:
        path = _org_prepare(path)
        if path is None:
            return None
        with open(path, "rb") as fh:
            head = fh.read(64)
            if head[:3] == b"\xff\xd8\xff":
                return _org_jpeg_size(fh)
            if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR" and len(head) >= 24:
                w, h = struct.unpack(">II", head[16:24])
                return (w, h) if w > 0 and h > 0 else None
            if head[:6] in (b"GIF87a", b"GIF89a") and len(head) >= 10:
                w, h = struct.unpack("<HH", head[6:10])
                return (w, h) if w > 0 and h > 0 else None
            if head[:2] == b"BM" and len(head) >= 26:
                hsize = struct.unpack("<I", head[14:18])[0]
                if hsize == 12:
                    w, h = struct.unpack("<HH", head[18:22])
                else:
                    w, h = struct.unpack("<ii", head[18:26])
                w, h = abs(w), abs(h)
                return (w, h) if w > 0 and h > 0 else None
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP" and len(head) >= 30:
                chunk = head[12:16]
                if chunk == b"VP8 " and head[23:26] == b"\x9d\x01\x2a":
                    w, h = struct.unpack("<HH", head[26:30])
                    w, h = w & 0x3FFF, h & 0x3FFF
                elif chunk == b"VP8L" and head[20] == 0x2F:
                    b = head[21:25]
                    bits = b[0] | (b[1] << 8) | (b[2] << 16) | (b[3] << 24)
                    w = (bits & 0x3FFF) + 1
                    h = ((bits >> 14) & 0x3FFF) + 1
                elif chunk == b"VP8X":
                    w = (head[24] | (head[25] << 8) | (head[26] << 16)) + 1
                    h = (head[27] | (head[28] << 8) | (head[29] << 16)) + 1
                else:
                    return None
                return (w, h) if w > 0 and h > 0 else None
    except Exception:
        pass
    return None


# ----- org_dupes -------------------------------------------------------------
# ===========================================================================
# ORDNER-ASSISTENT – exakte Duplikate + Versionsgruppen
# ===========================================================================
# Exakte Duplikate: Größe -> Anfang/Ende-Hash -> voller Hash (blake2b).
# Versionsgruppen: "Lebenslauf.docx", "Lebenslauf_final.docx",
# "Lebenslauf_final (1).docx" im selben Ordner -> neueste bleibt, Rest "Prüfen".
# Alle Namen beginnen mit org_ / Org / ORG_ / _org_dupe_ (Einzeldatei-Programm).
# Nur Standardbibliothek. Dieses Modul liest Dateien, verändert aber keine.

import hashlib
import os
import re
import unicodedata


ORG_DUPE_EDGE_BYTES = 64 * 1024        # Anfang + Ende für den Schnell-Hash
ORG_DUPE_CHUNK_BYTES = 1024 * 1024     # Blockgröße beim vollständigen Hash
ORG_DUPE_DIGEST_SIZE = 32              # blake2b-256 als Hex in f.meta["hash"]


# ---------------------------------------------------------------------------
# Hilfen: Hashing
# ---------------------------------------------------------------------------
def _org_dupe_safe_progress(progress, done: int, total: int) -> None:
    if progress is None:
        return
    try:
        progress(done, total)
    except Exception:
        pass                                 # Fehler im Fortschritts-Callback ignorieren


def _org_dupe_is_cancelled(cancelled) -> bool:
    try:
        return bool(cancelled())
    except Exception:
        return False


# Windows: Cloud-Platzhalter (OneDrive „Nur online“, offline) nie lesen – Lesen würde
# den Download auslösen (Netzwerk, Speicherplatz). Attribute laut WinAPI.
ORG_DUPE_CLOUD_ATTRS = 0x00001000 | 0x00040000 | 0x00400000   # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS
ORG_DUPE_CHECK_CLOUD = os.name == "nt"
_org_dupe_stat = os.stat                    # austauschbar für Tests


def _org_dupe_is_cloud_only(path) -> bool:
    """True, wenn die Datei nur als Cloud-Platzhalter vorliegt (nicht lesen!)."""
    if not ORG_DUPE_CHECK_CLOUD:
        return False
    try:
        attrs = getattr(_org_dupe_stat(str(path)), "st_file_attributes", 0) or 0
    except (OSError, ValueError, TypeError):
        return False                         # Lesefehler folgt ohnehin beim Öffnen
    return bool(attrs & ORG_DUPE_CLOUD_ATTRS)


def _org_dupe_long_path(s: str) -> str:
    """Windows-Pfad >= 248 Zeichen mit \\\\?\\-Präfix versehen (MAX_PATH umgehen)."""
    if len(s) < 248 or s.startswith("\\\\?\\"):
        return s
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def _org_dupe_io_path(path):
    """Pfad zum Öffnen: unter Windows lange Pfade erlauben, sonst unverändert."""
    if os.name != "nt":
        return path
    try:
        return _org_dupe_long_path(os.path.abspath(str(path)))
    except Exception:
        return path


def _org_dupe_full_hash(path, buf: bytearray, size: int = -1, cancelled=None) -> str | None:
    """Vollständiger blake2b-Hash in 1-MB-Blöcken; None bei Lesefehler, Abbruch
    oder wenn sich die Größe seit dem Einlesen geändert hat."""
    try:
        h = hashlib.blake2b(digest_size=ORG_DUPE_DIGEST_SIZE)
        mv = memoryview(buf)
        read = 0
        chunks = 0
        with open(_org_dupe_io_path(path), "rb", buffering=0) as fh:
            while True:
                n = fh.readinto(buf)
                if not n:
                    break
                h.update(mv[:n])
                read += n
                chunks += 1
                # Abbruch auch mitten in sehr großen Dateien (alle 64 MB prüfen)
                if cancelled is not None and chunks % 64 == 0 and _org_dupe_is_cancelled(cancelled):
                    return None
        if size >= 0 and read != size:
            return None                      # Datei wurde inzwischen verändert
        return h.hexdigest()
    except (OSError, ValueError, TypeError):
        return None


def _org_dupe_quick_hash(path, size: int):
    """Schnell-Hash. Liefert (digest, ist_vollständig) oder None bei Lesefehler.
    Kleine Dateien (<= 2 x 64 KB) werden ganz gelesen -> sofort voller Hash."""
    try:
        if _org_dupe_is_cloud_only(path):
            return None
        with open(_org_dupe_io_path(path), "rb") as fh:
            if size <= 2 * ORG_DUPE_EDGE_BYTES:
                data = fh.read(size + 1)         # nie mehr lesen als erwartet
                if len(data) != size:
                    return None                  # Größe geändert -> überspringen
                h = hashlib.blake2b(data, digest_size=ORG_DUPE_DIGEST_SIZE)
                return h.hexdigest(), True
            head = fh.read(ORG_DUPE_EDGE_BYTES)
            fh.seek(size - ORG_DUPE_EDGE_BYTES)
            tail = fh.read(ORG_DUPE_EDGE_BYTES)
        h = hashlib.blake2b(head, digest_size=16)
        h.update(b"|")
        h.update(tail)
        return h.hexdigest(), False
    except (OSError, ValueError, TypeError, OverflowError):
        return None


def _org_dupe_path_key(f) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(f.path)))
    except Exception:
        return str(getattr(f, "path", id(f)))


def _org_dupe_collapse_links(group: list) -> list:
    """Gleiche physische Datei (Hardlink/Junction) nur einmal zählen – sonst würde
    ein „Duplikat“ gelöscht, das in Wahrheit dieselbe Datei ist."""
    seen = set()
    out = []
    for f in group:
        try:
            st = os.stat(_org_dupe_io_path(str(f.path)))
            ident = (st.st_dev, st.st_ino) if st.st_ino else None
        except OSError:
            continue                         # inzwischen verschwunden/unlesbar
        if ident is not None:
            if ident in seen:
                continue
            seen.add(ident)
        out.append(f)
    return out


# ---------------------------------------------------------------------------
# Öffentlich: exakte Duplikate
# ---------------------------------------------------------------------------
def org_find_duplicates(files: list, cancelled=lambda: False, progress=None) -> list:
    """Gruppen (>= 2) byte-identischer Dateien. Leere Dateien werden ignoriert,
    Dateien mit einmaliger Größe nie gelesen. Voller Hash landet in f.meta["hash"].
    Bei Abbruch (cancelled()) -> []. progress(done_bytes, total_bytes) optional."""
    try:
        return _org_dupe_find(files, cancelled, progress)
    except Exception:
        return []


def _org_dupe_find(files, cancelled, progress) -> list:
    if not files:
        return []
    # 1) nach Größe bündeln (doppelte Einträge desselben Pfads entfernen)
    by_size: dict = {}
    for f in files:
        try:
            size = int(f.size)
        except (AttributeError, TypeError, ValueError):
            continue
        if size <= 0 or getattr(f, "path", None) is None:
            continue
        if getattr(f, "cloud", False):      # „nur online“: Lesen würde den Download starten
            continue
        by_size.setdefault(size, []).append(f)

    buckets = []
    for size, members in by_size.items():
        if len(members) < 2:
            continue
        seen = set()
        uniq = []
        for f in members:
            k = _org_dupe_path_key(f)
            if k not in seen:
                seen.add(k)
                uniq.append(f)
        if len(uniq) >= 2:
            buckets.append((size, uniq))
    if not buckets:
        _org_dupe_safe_progress(progress, 0, 0)
        return []

    # Gesamtmenge = Schnell-Hash aller Kandidaten + (schlimmstenfalls) voller Hash
    big = 2 * ORG_DUPE_EDGE_BYTES
    total = 0
    for size, members in buckets:
        quick = size if size <= big else big
        total += quick * len(members)
        if size > big:
            total += size * len(members)
    done = 0
    _org_dupe_safe_progress(progress, done, total)

    groups: list = []
    buf = bytearray(ORG_DUPE_CHUNK_BYTES)
    for size, members in buckets:
        quick_cost = size if size <= big else big
        # 2) Schnell-Hash (Anfang + Ende bzw. ganze kleine Datei)
        by_quick: dict = {}
        complete = False
        for f in members:
            if _org_dupe_is_cancelled(cancelled):
                return []
            res = _org_dupe_quick_hash(f.path, size)
            done += quick_cost
            if res is None:
                if size > big:
                    done += size             # wird nie voll gelesen
                continue                     # unlesbar -> überspringen
            digest, complete = res
            by_quick.setdefault(digest, []).append(f)
        _org_dupe_safe_progress(progress, done, total)

        for digest, cand in by_quick.items():
            if complete:
                if len(cand) >= 2:
                    for f in cand:
                        _org_dupe_set_hash(f, digest)
                    groups.append(cand)
                continue
            if len(cand) < 2:
                done += size * len(cand)     # kein voller Hash nötig
                continue
            # 3) voller Hash nur bei Kollision des Schnell-Hashs
            by_full: dict = {}
            for f in cand:
                if _org_dupe_is_cancelled(cancelled):
                    return []
                full = _org_dupe_full_hash(f.path, buf, size, cancelled)
                if full is None and _org_dupe_is_cancelled(cancelled):
                    return []
                done += size
                _org_dupe_safe_progress(progress, done, total)
                if full is None:
                    continue
                by_full.setdefault(full, []).append(f)
            for full, same in by_full.items():
                if len(same) >= 2:
                    for f in same:
                        _org_dupe_set_hash(f, full)
                    groups.append(same)
        _org_dupe_safe_progress(progress, done, total)

    result = []
    for g in groups:
        g = _org_dupe_collapse_links(g)
        if len(g) >= 2:
            g.sort(key=lambda x: str(getattr(x, "rel", "")))
            result.append(g)
    # größte Ersparnis zuerst, dann stabil nach Pfad
    result.sort(key=lambda g: (-int(g[0].size), str(getattr(g[0], "rel", ""))))
    _org_dupe_safe_progress(progress, total, total)
    return result


def _org_dupe_set_hash(f, digest: str) -> None:
    try:
        if not isinstance(f.meta, dict):
            f.meta = {}
        f.meta["hash"] = digest
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Öffentlich: welches Exemplar behalten?
# ---------------------------------------------------------------------------
# Ordner, in denen eher Kopien/Reste liegen (Teile des rel-Pfads, ohne Dateiname)
ORG_DUPE_WEAK_DIRS = {"downloads", "download", "temp", "tmp", "neuer ordner",
                      "new folder", "desktop"}
_ORG_DUPE_DIR_COUNTER_RE = re.compile(r"\s*\(\d{1,3}\)$")
# Kopie-Kennzeichen im Dateinamen (ohne Endung)
_ORG_DUPE_COPY_NAME_RE = re.compile(
    r"(?:^(?:kopie|copy)\s+(?:von|of)\b"   # "Kopie von x", "Copy of x"
    r"|\(\d{1,2}\)\s*$"                    # "x (1)" .. "x (99)"
    # "x - Kopie", "x_copy", "x-Kopie", "x (Kopie)", "Kopie - x", "x copy2"
    r"|(?:^|[\s_\-–—(\[.])(?:kopie|copy)(?=$|[\s_\-–—)\].\d]))",
    re.IGNORECASE)
# Reste/Temporärdateien: nie als Behalten-Exemplar wählen, wenn es eine echte Datei
# gibt (sonst: Original = Duplikat, „Original“ ~WRL0001.tmp = Müll -> beides weg)
_ORG_DUPE_TEMP_EXTS = {".tmp", ".temp", ".crdownload", ".part", ".partial", ".download",
                       ".opdownload", ".bak", ".old", ".swp", ".swo", ".~tmp", ".dmp"}
_ORG_DUPE_TEMP_PREFIXES = ("~$", "~wrl", ".~lock", "~lock")


def _org_dupe_in_weak_dir(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")[:-1]
    for p in parts:
        p = _ORG_DUPE_DIR_COUNTER_RE.sub("", p.strip().lower())
        if p in ORG_DUPE_WEAK_DIRS:
            return True
    return False


def _org_dupe_has_copy_marker(name: str) -> bool:
    stem = os.path.splitext(name)[0]
    return bool(_ORG_DUPE_COPY_NAME_RE.search(stem))


def _org_dupe_junk_rank(f, name: str) -> int:
    """0 = normale Datei, 1 = Temp-/Rest-Name (~$x.docx, x.tmp, x.part …),
    2 = schon als Müll eingestuft (ORG_JUNK)."""
    if getattr(f, "tier", "") == ORG_JUNK:
        return 2
    nl = name.lower()
    if nl.startswith(_ORG_DUPE_TEMP_PREFIXES):
        return 1
    return 1 if os.path.splitext(nl)[1] in _ORG_DUPE_TEMP_EXTS else 0


def _org_dupe_keeper_key(f):
    rel = str(getattr(f, "rel", "") or "").replace("\\", "/")
    try:
        name = f.path.name
    except Exception:
        name = rel.rsplit("/", 1)[-1]
    try:
        mtime = float(f.mtime)
        if mtime != mtime:                   # NaN
            mtime = float("inf")
    except (TypeError, ValueError, AttributeError):
        mtime = float("inf")
    try:
        depth = int(f.depth)
    except (TypeError, ValueError, AttributeError):
        depth = rel.count("/")
    return (_org_dupe_junk_rank(f, name),                 # (0) Sicherheit: kein Rest/Müll
            not bool(getattr(f, "in_project", False)),   # (1) Projekt zuerst
            _org_dupe_in_weak_dir(rel),                   # (2) nicht Downloads/Temp
            _org_dupe_has_copy_marker(name),              # (3) ohne "Kopie"
            mtime,                                        # (4) ältestes = Original
            depth,                                        # (5) flachste Ebene
            len(rel),                                     # (6) kürzester Pfad
            rel)                                          # (7) alphabetisch


def org_pick_keeper(group: list):
    """Das Exemplar, das behalten wird (None bei leerer Gruppe)."""
    try:
        members = [f for f in (group or []) if f is not None]
        if not members:
            return None
        return min(members, key=_org_dupe_keeper_key)
    except Exception:
        try:
            return group[0]
        except Exception:
            return None


def org_mark_duplicates(files: list, groups: list) -> int:
    """Markiert alle Nicht-Behalten-Exemplare als Duplikat (ORG_JUNK).
    Dateien in Projektordnern und das behaltene Exemplar bleiben unberührt.
    `files` wird nicht benötigt (Signatur für einheitliche Pipeline)."""
    marked = 0
    try:
        group_iter = list(groups or [])
    except Exception:
        return 0
    for group in group_iter:
        try:
            if not group or len(group) < 2:
                continue
            keeper = org_pick_keeper(group)
            if keeper is None:
                continue
            # Sicherheit: Behalten-Exemplar inzwischen gelöscht/verschoben? Dann ein
            # noch vorhandenes Exemplar behalten (sonst wären alle Kopien „Müll“).
            if not _org_dupe_exists(keeper):
                existing = [m for m in group if m is not None and _org_dupe_exists(m)]
                if existing:
                    keeper = org_pick_keeper(existing)
                    if keeper is None:
                        continue
            keeper_path = _org_dupe_path_key(keeper)
            keeper_rel = str(keeper.rel)
            keeper_hash = _org_dupe_get_hash(keeper)
            keeper_size = getattr(keeper, "size", None)
        except Exception:
            continue
        for f in group:
            try:
                if f is None or f is keeper or getattr(f, "in_project", False):
                    continue
                if _org_dupe_path_key(f) == keeper_path:
                    continue                 # derselbe Pfad doppelt – nie markieren
                # Sicherheit: Gruppe passt nicht (andere Größe / anderer Hash) -> nie Müll
                if getattr(f, "size", None) != keeper_size:
                    continue
                h = _org_dupe_get_hash(f)
                if h and keeper_hash and h != keeper_hash:
                    continue
                f.add_reason("Exakte Kopie von %s" % keeper_rel)
                f.dup_of = keeper_rel
                f.category = "duplicate"
                f.tier = ORG_JUNK
                f.score = 1.0
                marked += 1
            except Exception:
                continue
    return marked


def _org_dupe_exists(f) -> bool:
    try:
        return os.path.isfile(_org_dupe_io_path(str(f.path)))
    except Exception:
        return False


def _org_dupe_get_hash(f) -> str:
    try:
        meta = f.meta
        return str(meta.get("hash") or "") if isinstance(meta, dict) else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Versionsschlüssel
# ---------------------------------------------------------------------------
_ORG_DUPE_UMLAUTS = {ord("ä"): "ae", ord("ö"): "oe", ord("ü"): "ue", ord("ß"): "ss",
                     ord("Ä"): "ae", ord("Ö"): "oe", ord("Ü"): "ue", ord("ẞ"): "ss"}
# Token: "(2)" | "[2]" | "v1.2" / "2.1" | Wort/Zahl – Unicode-fähig, damit kyrillische/
# chinesische Namen nicht zu "" bzw. nur Ziffern zusammenfallen ("简历_2023" ≠ "合同_2023")
_ORG_DUPE_TOKEN_RE = re.compile(r"\(\d{1,2}\)|\[\d{1,2}\]|[^\W\d_]*\d+(?:\.\d+)+|[^\W_]+")
# Starke Marker: überall im Namen entfernt
ORG_DUPE_STRONG_MARKERS = {
    "final", "finale", "finaler", "finales", "finalversion", "endgueltig", "endgultig",
    "endgueltige", "endgueltiges", "endversion", "neueste", "neuster", "neuste",
    "neuestes", "aktuellste", "newest", "latest", "copy", "kopie", "draft", "entwurf",
    "korrigiert", "korrigierte", "korrektur", "ueberarbeitet", "uberarbeitet",
    "ueberarbeitete", "edited", "updated", "bearbeitet", "bearbeitete", "backup",
    "sicherung", "sicherungskopie", "korr", "endfassung", "finalfinal",
}
# Schwache Marker: nur am Anfang/Ende des Namens (sonst zu viele Fehltreffer,
# z. B. "New York Reise", "Update Anleitung Router")
ORG_DUPE_WEAK_MARKERS = {
    "neu", "new", "aktuell", "current", "alt", "old", "edit", "update", "fertig",
}
_ORG_DUPE_VERSION_TOKEN_RE = re.compile(
    r"^(?:v\d{1,2}(?:\.\d{1,3})*|ver\d{1,3}(?:\.\d{1,3})*|version\d{1,3}(?:\.\d{1,3})*"
    r"|rev\d{1,3}(?:\.\d{1,3})*)$")
_ORG_DUPE_WEAK_VERSION_RE = re.compile(r"^r\d{1,2}$")       # "r2" nur am Rand
_ORG_DUPE_VERSION_WORDS = {"v", "ver", "version", "rev", "revision"}
_ORG_DUPE_NUMBER_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3})*$")
_ORG_DUPE_SMALL_NUM_RE = re.compile(r"^\d{1,2}$")
_ORG_DUPE_PAREN_RE = re.compile(r"^[\(\[]\d{1,2}[\)\]]$")
_ORG_DUPE_GLUED_RE = re.compile(r"^(?P<word>[a-z]+)(?P<num>\d{1,2})$")
# Endungen, die dieselbe Dateifamilie meinen
_ORG_DUPE_EXT_ALIAS = {".jpeg": ".jpg", ".jpe": ".jpg", ".tiff": ".tif", ".htm": ".html",
                       ".yaml": ".yml", ".mpeg": ".mpg", ".aif": ".aiff"}
# echte Endung: 1–8 Zeichen, mind. ein Buchstabe ("v1.2" hat keine Endung ".2")
_ORG_DUPE_EXT_RE = re.compile(r"^\.(?=[a-z0-9]*[a-z])[a-z0-9]{1,8}$")
_ORG_DUPE_BASE_CACHE: dict = {}
_ORG_DUPE_CACHE_MAX = 200_000


def _org_dupe_fold(s: str) -> str:
    s = unicodedata.normalize("NFC", s).lower().translate(_ORG_DUPE_UMLAUTS)
    if not s.isascii():
        s = "".join(c for c in unicodedata.normalize("NFKD", s)
                    if not unicodedata.combining(c))
    return s


def _org_dupe_is_strong(tok: str) -> bool:
    if tok in ORG_DUPE_STRONG_MARKERS or _ORG_DUPE_PAREN_RE.match(tok):
        return True
    if _ORG_DUPE_VERSION_TOKEN_RE.match(tok):
        return True
    m = _ORG_DUPE_GLUED_RE.match(tok)        # "final2", "kopie3"
    return bool(m and m.group("word") in ORG_DUPE_STRONG_MARKERS)


def _org_dupe_is_weak(tok: str) -> bool:
    if tok in ORG_DUPE_WEAK_MARKERS or _ORG_DUPE_WEAK_VERSION_RE.match(tok):
        return True
    m = _ORG_DUPE_GLUED_RE.match(tok)        # "neu2", "alt3"
    return bool(m and m.group("word") in ORG_DUPE_WEAK_MARKERS)


def _org_dupe_split(name: str):
    """(Basisschlüssel, hat_Marker). Basisschlüssel = Name ohne Versions-/Kopie-
    Marker + Endungsfamilie, z. B. "lebenslauf.docx". "" wenn nichts übrig bleibt."""
    return _org_dupe_split3(name)[:2]


def _org_dupe_split3(name: str):
    """(Basisschlüssel, hat_Marker, nur_Zähler). nur_Zähler = einzige Marker sind
    Browser-/Explorer-Zähler wie "(1)" – kein echtes Versionswort."""
    if not isinstance(name, str):
        return "", False, False
    cached = _ORG_DUPE_BASE_CACHE.get(name)
    if cached is not None:
        return cached
    res = _org_dupe_split_uncached(name)
    if len(_ORG_DUPE_BASE_CACHE) >= _ORG_DUPE_CACHE_MAX:
        _ORG_DUPE_BASE_CACHE.clear()
    _ORG_DUPE_BASE_CACHE[name] = res
    return res


def _org_dupe_split_uncached(name: str):
    if not isinstance(name, str) or not name.strip():
        return "", False, False
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    stem, ext = os.path.splitext(name)
    if not stem:                              # ".bashrc" -> keine Endung
        stem, ext = ext, ""
    ext = ext.lower()
    if not _ORG_DUPE_EXT_RE.match(ext):
        stem, ext = name, ""                  # "Bericht v1.2" -> kein echtes Suffix
    ext = _ORG_DUPE_EXT_ALIAS.get(ext, ext)
    toks = _ORG_DUPE_TOKEN_RE.findall(_org_dupe_fold(stem))
    n = len(toks)
    removed = [False] * n

    # Durchgang 1: starke Marker überall
    i = 0
    while i < n:
        t = toks[i]
        if _org_dupe_is_strong(t):
            removed[i] = True
            # "Kopie von x", "Copy of x"
            if t in ("kopie", "copy") and i + 1 < n and toks[i + 1] in ("von", "of"):
                removed[i + 1] = True
                i += 1
            # Zähler direkt danach: "final 2", "Kopie 3"
            elif (i + 1 < n and _ORG_DUPE_SMALL_NUM_RE.match(toks[i + 1])
                  and not _ORG_DUPE_PAREN_RE.match(t)):
                removed[i + 1] = True
                i += 1
        elif t in _ORG_DUPE_VERSION_WORDS and i + 1 < n and _ORG_DUPE_NUMBER_RE.match(toks[i + 1]):
            removed[i] = removed[i + 1] = True   # "Version 3", "rev 2", "V 1.2"
            i += 1
        i += 1

    # Durchgang 2: schwache Marker nur am Rand (von außen nach innen)
    def _prev_marker(j: int) -> bool:
        return j >= 0 and (removed[j] or _org_dupe_is_weak(toks[j]))

    j = n - 1
    while j >= 0:
        if removed[j]:
            j -= 1
            continue
        t = toks[j]
        if _org_dupe_is_weak(t):
            removed[j] = True
        elif _ORG_DUPE_SMALL_NUM_RE.match(t) and _prev_marker(j - 1):
            removed[j] = True                 # "Lebenslauf neu 2"
        else:
            break
        j -= 1
    i = 0
    while i < n:
        if removed[i]:
            i += 1
            continue
        if _org_dupe_is_weak(toks[i]):
            removed[i] = True
            if i + 1 < n and _ORG_DUPE_SMALL_NUM_RE.match(toks[i + 1]):
                removed[i + 1] = True
                i += 1
            i += 1
            continue
        break

    has_marker = any(removed)
    counter_only = has_marker and all(
        _ORG_DUPE_PAREN_RE.match(t) for t, r in zip(toks, removed) if r)
    base = " ".join(t for t, r in zip(toks, removed) if not r)
    if not base:
        return "", has_marker, counter_only
    return base + ext, has_marker, counter_only


def org_version_base(name: str) -> str:
    """Basisschlüssel eines Dateinamens – auch ohne Marker (für Gruppierung).
    "Lebenslauf_final_FINAL (2).docx" und "Lebenslauf.docx" -> "lebenslauf.docx"."""
    try:
        return _org_dupe_split(name)[0]
    except Exception:
        return ""


def org_version_key(name: str):
    """Basisschlüssel, wenn der Name einen Versions-/Kopie-Marker trägt, sonst None.
    Reine Zahlen/Daten werden nie entfernt (Rechnung_1234 ≠ Rechnung_1235)."""
    try:
        base, has_marker = _org_dupe_split(name)
    except Exception:
        return None
    if not has_marker or not base:
        return None
    return base


# ---------------------------------------------------------------------------
# Öffentlich: Versionsgruppen
# ---------------------------------------------------------------------------
# Fotos/Videos/Musik bilden keine Versionsgruppen: "IMG_0001 (1).JPG",
# "Screenshot (3).png", "image (1).jpg" sind fast immer VERSCHIEDENE Aufnahmen, und
# bei "Foto_bearbeitet.jpg" ist das ältere Exemplar das unbearbeitete Original.
# Exakte Kopien findet org_find_duplicates trotzdem.
_ORG_DUPE_MEDIA_KINDS = {"image", "video", "audio"}
_ORG_DUPE_MEDIA_EXTS = {e for k in _ORG_DUPE_MEDIA_KINDS for e in ORG_KIND_EXT.get(k, ())}
_ORG_DUPE_MEDIA_CATEGORIES = {"photo_personal", "photo_chat", "screenshot", "image",
                              "video_personal", "video", "audio"}
# Nur-Zähler-Gruppen ("Rechnung.pdf", "Rechnung (1).pdf") bei Allerweltsnamen oder
# wiederkehrenden Belegen sind meist verschiedene Dokumente, keine Versionen.
ORG_DUPE_GENERIC_WORDS = {
    "scan", "scans", "dokument", "document", "doc", "datei", "file", "download",
    "unbenannt", "untitled", "unnamed", "neues", "new", "neu", "image", "bild", "foto",
    "photo", "img", "screenshot", "bildschirmfoto", "anhang", "attachment", "print",
    "druck", "export", "output", "ausgabe", "brief", "letter", "formular", "form",
    "seite", "page", "blatt", "sheet", "mappe", "tabelle", "praesentation",
    "presentation", "notiz", "notes", "note", "text", "liste", "list", "pdf", "kopie",
    "copy", "test", "temp", "tmp", "info", "data", "daten", "report",
}
_ORG_DUPE_RECURRING_STEMS = (
    "rechnung", "abrechnung", "invoice", "receipt", "beleg", "quittung", "auszug",
    "statement", "payslip", "bescheid", "bescheinigung", "nachweis", "ticket",
    "fahrkarte", "bestellung", "order", "lieferschein", "gutschrift", "mahnung",
    "zahlung", "payment", "kassenbon", "bon", "termin", "befund", "rezept",
)
_ORG_DUPE_TEMP_NAME_PREFIXES = ("~", ".~")


def _org_dupe_is_media(f) -> bool:
    if getattr(f, "kind", "") in _ORG_DUPE_MEDIA_KINDS:
        return True
    if getattr(f, "category", "") in _ORG_DUPE_MEDIA_CATEGORIES:
        return True
    return str(getattr(f, "ext", "") or "").lower() in _ORG_DUPE_MEDIA_EXTS


def _org_dupe_generic_base(base: str) -> bool:
    """Basisname ist Allerweltsname oder wiederkehrender Beleg (Rechnung, Auszug …)."""
    stem = base.rsplit(".", 1)[0] if "." in base else base
    toks = stem.split()
    if not toks:
        return True
    if all(t.isdigit() or t in ORG_DUPE_GENERIC_WORDS for t in toks):
        return True
    return any(t.endswith(_ORG_DUPE_RECURRING_STEMS) for t in toks)


_ORG_DUPE_RECURRING_CATEGORIES = {"invoice", "bank", "payslip", "tax", "travel", "health",
                                  "insurance"}


def org_version_groups(files: list) -> dict:
    """{"<Ordner-rel>/<basis>": [OrgFile, ...]} – Dateien im selben Ordner mit gleichem
    Basisschlüssel, mind. 2 Mitglieder, mind. eines mit Marker. Ausgeschlossen:
    Projektdateien, Duplikate, Aufräum-Kandidaten (ORG_JUNK), leere Dateien,
    Sperr-/Temp-Dateien (~$x.docx), Fotos/Videos/Musik, sowie reine Zähler-Gruppen
    ("Scan (1).pdf") bei Allerweltsnamen und wiederkehrenden Belegen."""
    buckets: dict = {}
    marked_keys = set()
    semantic_keys = set()                    # Gruppe hat echtes Versionswort
    try:
        items = list(files or [])
    except Exception:
        return {}
    for f in items:
        try:
            if getattr(f, "in_project", False):
                continue
            if f.dup_of or f.category == "duplicate" or f.tier == ORG_JUNK:
                continue
            if _org_dupe_is_media(f):
                continue
            try:
                if int(f.size) <= 0:
                    continue                 # leere Datei ist nie "neueste Version"
            except (TypeError, ValueError, AttributeError):
                pass
            rel = str(f.rel).replace("\\", "/")
            parent, _, fname = rel.rpartition("/")
            fname = fname or f.path.name
            if fname.startswith(_ORG_DUPE_TEMP_NAME_PREFIXES):
                continue                     # Office-/LibreOffice-Sperrdateien
            if os.path.splitext(fname)[1].lower() in _ORG_DUPE_TEMP_EXTS:
                continue
            base, has_marker, counter_only = _org_dupe_split3(fname)
            if not base:
                continue
            key = (parent + "/" + base) if parent else base
            buckets.setdefault(key, []).append(f)
            if has_marker:
                marked_keys.add(key)
                if not counter_only:
                    semantic_keys.add(key)
        except Exception:
            continue
    out = {}
    for key in marked_keys:
        try:
            members = buckets.get(key) or []
            if len(members) < 2:
                continue
            if key not in semantic_keys:
                # nur "(1)"-Zähler: bei Allerweltsnamen/Belegen keine Versionen
                base = key.rsplit("/", 1)[-1]
                if _org_dupe_generic_base(base):
                    continue
                if any(getattr(m, "category", "") in _ORG_DUPE_RECURRING_CATEGORIES
                       for m in members):
                    continue
            members.sort(key=lambda x: str(x.rel))
            out[key] = members
        except Exception:
            continue
    return out


def _org_dupe_display_name(f) -> str:
    try:
        return f.path.name
    except Exception:
        return str(getattr(f, "rel", "") or "").replace("\\", "/").rsplit("/", 1)[-1]


def org_mark_versions(groups: dict) -> int:
    """Neueste Datei je Gruppe bleibt (version_latest=True), ältere -> ORG_REVIEW
    ("Ältere Version von …"), nie ORG_JUNK. Liefert Anzahl älterer Versionen."""
    count = 0
    try:
        group_items = list((groups or {}).items())
    except Exception:
        return 0

    def _age(x):
        try:
            m = float(x.mtime)
            if m != m:                           # NaN
                m = float("-inf")
        except (TypeError, ValueError, AttributeError):
            m = float("-inf")
        return (m, str(getattr(x, "rel", "")))

    for key, members in group_items:
        try:
            members = [f for f in members
                       if f is not None and not getattr(f, "in_project", False)
                       and not getattr(f, "dup_of", "")]
            if len(members) < 2:
                continue
            newest = max(members, key=_age)
            reason = "Ältere Version von %s" % _org_dupe_display_name(newest)
        except Exception:
            continue
        try:
            newest.version_group = key
            newest.version_latest = True
        except Exception:
            continue
        for f in members:
            if f is newest:
                continue
            try:
                f.add_reason(reason)
                f.version_group = key
                f.version_latest = False
                f.tier = ORG_REVIEW
                count += 1
            except Exception:
                continue
    return count


# ----- org_junk --------------------------------------------------------------
# ===========================================================================
# ORDNER-ASSISTENT – Reste & Müll, Installationsdateien, leere Ordner
# ===========================================================================
# Erkennt eindeutige Reste (Sperrdateien, Temp-Dateien, abgebrochene Downloads,
# Vorschau-Caches, Absturzabbilder, leere Dateien), ordnet Installationsdateien
# bereits installierten Programmen zu und findet leere Ordner.
# Nichts wird hier gelöscht oder verschoben – es werden nur Vorschläge markiert.
# Nur Standardbibliothek. Alle Namen beginnen mit org_ / Org / ORG_ / _org_.

import math
import os
import re
import stat as _org_junk_stat
import time
import unicodedata
from pathlib import Path


# ---------------------------------------------------------------------------
# 1) Reste / Müll
# ---------------------------------------------------------------------------
ORG_JUNK_DAY = 86400.0
ORG_JUNK_PARTIAL_AGE = 1 * ORG_JUNK_DAY        # abgebrochene Downloads: erst nach 24 h
ORG_JUNK_FRESH_AGE = 3600.0                     # Sperr-/Temp-Dateien: jünger als 1 h → evtl. in Benutzung
ORG_JUNK_LOG_AGE = 90 * ORG_JUNK_DAY            # Protokolldateien: ab 90 Tagen prüfen
ORG_JUNK_INSTALLER_AGE = 60 * ORG_JUNK_DAY      # Installer: ab 60 Tagen "alt"

ORG_JUNK_PARTIAL_EXTS = {".crdownload", ".part", ".partial", ".download", ".opdownload"}
ORG_JUNK_TEMP_EXTS = {".tmp", ".temp"}
ORG_JUNK_BACKUP_EXTS = {".bak", ".old"}
ORG_JUNK_CACHE_NAMES = {
    "thumbs.db": "Vorschaubild-Cache von Windows (Thumbs.db)",
    "ehthumbs.db": "Vorschaubild-Cache von Windows (ehthumbs.db)",
    "ehthumbs_vista.db": "Vorschaubild-Cache von Windows (ehthumbs_vista.db)",
    ".ds_store": "macOS-Ordnerinfo (.DS_Store)",
}
# Leere Dateien, die absichtlich leer sind
ORG_JUNK_EMPTY_OK = {".gitkeep", ".keep", "__init__.py", ".nomedia", "py.typed",
                     "desktop.ini", ".placeholder", ".empty"}

# Office-Besitzerdateien (~$…) sind ~162 Byte, LibreOffice-Locks < 1 KB.
# Größere Dateien mit solchem Namen sind keine Sperrdateien → nur prüfen.
ORG_JUNK_LOCK_MAX_SIZE = 8192
# Kopfkennungen echter Windows-Absturzabbilder (Minidump / Kernel-Dump)
_ORG_JUNK_DMP_MAGIC = (b"MDMP", b"PAGEDUMP", b"PAGEDU64")
# Name/Ordner typischer Absturzabbilder (wenn der Inhalt nicht lesbar ist)
_ORG_JUNK_DMP_NAME = re.compile(r"^(memory|mini\d*[-_]?\d*.*|.*\.exe\.\d+|.*crash.*|.*minidump.*)\.dmp$",
                                re.IGNORECASE)
_ORG_JUNK_DMP_DIRS = {"crashdumps", "minidump", "crashes", "crash reports", "crashreports",
                      "reports", "dumps", "livekerneldump"}

_ORG_JUNK_LIBRE_LOCK = re.compile(r"^\.~lock\..+#$", re.IGNORECASE)
_ORG_JUNK_WRL = re.compile(r"^~wrl\w*\.tmp$", re.IGNORECASE)


def _org_junk_age(f, now: float) -> float:
    try:
        age = float(now) - float(f.mtime)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(age):      # NaN/inf: Alter unbekannt → wie frisch behandeln
        return 0.0
    return age


def _org_junk_set(f, reason: str) -> None:
    f.category = "temp"
    f.tier = ORG_JUNK
    try:
        f.score = max(float(f.score or 0.0), 0.95)
    except (TypeError, ValueError):
        f.score = 0.95
    f.add_reason(reason)


def _org_junk_set_review(f, reason: str) -> None:
    # Wichtiges nie herabstufen, bereits als Müll Erkanntes nicht aufwerten
    if f.tier not in (ORG_PROTECT, ORG_JUNK):
        f.tier = ORG_REVIEW
    f.add_reason(reason)


def _org_junk_size(f) -> int:
    try:
        return int(f.size)
    except (TypeError, ValueError, OverflowError):
        return -1


def _org_junk_name(f) -> str:
    p = f.path
    name = getattr(p, "name", None)
    if not isinstance(name, str):
        name = str(p).replace("\\", "/").rsplit("/", 1)[-1]
    return name


def _org_junk_dmp_is_crash(f) -> bool:
    """Echtes Absturzabbild? (.dmp nutzen auch Datenbank-Exporte, z. B. Oracle)"""
    if _org_junk_size(f) == 0:
        return True
    try:
        with open(str(f.path), "rb") as fh:
            head = fh.read(8)
    except (OSError, ValueError, TypeError):
        head = None
    if head is not None:
        return head.startswith(_ORG_JUNK_DMP_MAGIC)
    # Inhalt nicht lesbar → nur bei typischem Namen oder Ordner
    if _ORG_JUNK_DMP_NAME.match(_org_junk_name(f)):
        return True
    try:
        parent = str(f.path).replace("\\", "/").rsplit("/", 2)
        return len(parent) >= 2 and parent[-2].lower() in _ORG_JUNK_DMP_DIRS
    except Exception:
        return False


def _org_junk_rule(f, now: float):
    """Liefert (Aktion, Begründung): Aktion 'junk' | 'review' | None."""
    name = _org_junk_name(f)
    nl = name.lower()
    ext = (f.ext or os.path.splitext(nl)[1]).lower()
    age = _org_junk_age(f, now)

    # Sperrdateien von Office / LibreOffice (jünger als 1 h: Datei evtl. noch geöffnet)
    if nl.startswith("~$"):
        if age < ORG_JUNK_FRESH_AGE:
            return None, ""
        if _org_junk_size(f) > ORG_JUNK_LOCK_MAX_SIZE:
            return "review", "Name wie Office-Sperrdatei, aber zu groß – bitte prüfen"
        return "junk", "Office-Sperrdatei (Rest einer geöffneten Datei)"
    if _ORG_JUNK_LIBRE_LOCK.match(name):
        if age < ORG_JUNK_FRESH_AGE:
            return None, ""
        if _org_junk_size(f) > ORG_JUNK_LOCK_MAX_SIZE:
            return "review", "Name wie LibreOffice-Sperrdatei, aber zu groß – bitte prüfen"
        return "junk", "LibreOffice-Sperrdatei"
    if _ORG_JUNK_WRL.match(name):
        if age < ORG_JUNK_FRESH_AGE:
            return None, ""
        return "junk", "Temporäre Word-Datei"
    # abgebrochene Downloads – jünger als 24 h: Download läuft evtl. noch
    if ext in ORG_JUNK_PARTIAL_EXTS:
        if age <= ORG_JUNK_PARTIAL_AGE:
            return None, ""
        return "junk", "Abgebrochener Download (älter als 24 Stunden)"
    if ext in ORG_JUNK_TEMP_EXTS:
        if age < ORG_JUNK_FRESH_AGE:
            return None, ""
        return "junk", "Temporäre Datei"
    if nl in ORG_JUNK_CACHE_NAMES:
        return "junk", ORG_JUNK_CACHE_NAMES[nl]
    if ext == ".dmp":
        if _org_junk_dmp_is_crash(f):
            return "junk", "Absturzabbild (Crash-Dump)"
        return "review", "Speicherabbild/Export (.dmp) – kein Absturzabbild erkannt"
    # Sicherungskopien und Protokolle: nur prüfen, nie automatisch Müll
    if ext in ORG_JUNK_BACKUP_EXTS:
        return "review", "Sicherungskopie"
    if ext == ".log":
        if age > ORG_JUNK_LOG_AGE:
            return "review", "Alte Protokolldatei"
        return None, ""
    size = _org_junk_size(f)
    if size == 0 and nl not in ORG_JUNK_EMPTY_OK:
        return "junk", "Leere Datei (0 Byte)"
    return None, ""


def org_detect_junk(files: list, now: float | None = None) -> int:
    """Markiert eindeutige Reste als Kategorie "temp" / Stufe ORG_JUNK.
    Sicherungskopien (.bak/.old) und alte Protokolle → ORG_REVIEW.
    Dateien in Projektordnern werden nie angefasst. Liefert Anzahl Müll."""
    if not files:
        return 0
    if now is None:
        now = time.time()
    count = 0
    for f in files:
        try:
            if getattr(f, "in_project", False):
                continue
            action, reason = _org_junk_rule(f, now)
            if action == "junk":
                _org_junk_set(f, reason)
                count += 1
            elif action == "review":
                _org_junk_set_review(f, reason)
        except Exception:      # defekter Datensatz: überspringen
            continue
    return count


# ---------------------------------------------------------------------------
# 2) Installierte Programme (Registry)
# ---------------------------------------------------------------------------
ORG_JUNK_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
ORG_JUNK_UNINSTALL_KEY_WOW = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
ORG_JUNK_REG_MAX_KEYS = 20000


def _org_junk_reg_value(reg, key, name: str):
    try:
        return reg.QueryValueEx(key, name)[0]
    except Exception:
        return None


def _org_junk_reg_close(reg, key) -> None:
    closer = getattr(reg, "CloseKey", None)
    if closer is None:
        return
    try:
        closer(key)
    except Exception:
        pass


def org_installed_programs(reg=None) -> list:
    """DisplayName aller installierten Programme (HKLM 64/32 Bit + HKCU).
    reg: winreg-ähnliches Modul (für Tests). Ohne Windows → []."""
    if reg is None:
        if os.name != "nt":
            return []
        try:
            import winreg as reg  # type: ignore
        except ImportError:
            return []
    try:
        hklm = reg.HKEY_LOCAL_MACHINE
        hkcu = reg.HKEY_CURRENT_USER
        key_read = getattr(reg, "KEY_READ", 0x20019)
    except Exception:
        return []
    wow64 = getattr(reg, "KEY_WOW64_64KEY", 0)
    sources = [
        (hklm, ORG_JUNK_UNINSTALL_KEY, key_read | wow64),
        (hklm, ORG_JUNK_UNINSTALL_KEY_WOW, key_read),
        (hkcu, ORG_JUNK_UNINSTALL_KEY, key_read),
    ]
    names: list = []
    seen: set = set()
    for hive, path, access in sources:
        try:
            root = reg.OpenKey(hive, path, 0, access)
        except Exception:
            access = key_read          # Ansicht-Flag nicht unterstützt → ohne
            try:
                root = reg.OpenKey(hive, path, 0, access)
            except Exception:
                continue
        try:
            for i in range(ORG_JUNK_REG_MAX_KEYS):
                try:
                    sub_name = reg.EnumKey(root, i)
                except OSError:
                    break           # Ende der Aufzählung (wie im echten winreg)
                except Exception:
                    break
                try:
                    sub = reg.OpenKey(root, sub_name, 0, access)
                except Exception:
                    continue
                try:
                    dn = _org_junk_reg_value(reg, sub, "DisplayName")
                    if not isinstance(dn, str) or not dn.strip():
                        continue
                    sc = _org_junk_reg_value(reg, sub, "SystemComponent")
                    try:
                        if sc is not None and int(sc) == 1:
                            continue
                    except (TypeError, ValueError):
                        pass
                    # Updates/Patches gehören zu einem anderen Eintrag
                    if _org_junk_reg_value(reg, sub, "ParentKeyName"):
                        continue
                    rt = _org_junk_reg_value(reg, sub, "ReleaseType")
                    if isinstance(rt, str) and rt.lower() in ("update", "hotfix", "security update"):
                        continue
                    dn = " ".join(dn.split())
                    k = dn.lower()
                    if k not in seen:
                        seen.add(k)
                        names.append(dn)
                finally:
                    _org_junk_reg_close(reg, sub)
        finally:
            _org_junk_reg_close(reg, root)
    return names


# ---------------------------------------------------------------------------
# 3) Installationsdatei ↔ installiertes Programm
# ---------------------------------------------------------------------------
# Wörter ohne Aussagekraft im Dateinamen (werden entfernt)
ORG_JUNK_NOISE = {
    "setup", "install", "installer", "installation", "instal", "inst", "x64", "x86", "x32",
    "win", "win32", "win64", "windows", "amd64", "arm64", "aarch64", "ia32", "i386", "i686",
    "full", "offline", "online", "web", "latest", "stable", "release", "user", "system",
    "portable", "en", "de", "us", "uk", "multi", "multilingual", "build", "bit", "gui",
    "desktop", "browser", "bin", "standalone", "beta", "lts", "final", "signed", "msi", "exe",
    "bootstrapper", "downloader", "deu", "ger", "eng", "german", "english", "deutsch",
    "intl", "international", "uninstall", "unins", "kopie", "copy", "any", "anycpu", "cpu",
    "per", "machine", "silent", "stub", "redistributable",
}
# Allgemeine Wörter: zählen nicht als eindeutiges Merkmal (z. B. "driver.exe")
ORG_JUNK_GENERIC = {
    "driver", "drivers", "treiber", "tool", "tools", "update", "updater", "app", "apps",
    "client", "launcher", "manager", "helper", "service", "runtime", "studio", "viewer",
    "player", "reader", "editor", "pro", "plus", "free", "invoice", "rechnung", "document",
    "dokument", "file", "files", "datei", "download", "program", "programm", "software",
    "microsoft", "google", "mozilla", "adobe", "apple", "oracle", "intel", "amd", "nvidia",
    "realtek", "dell", "lenovo", "media", "center", "edition", "community", "professional",
    "home", "basic", "suite", "package", "pack", "kit", "sdk", "framework", "components",
    "component", "extension", "plugin", "data", "new", "the", "and", "for", "with",
    "version", "inc", "corporation", "corp", "llc", "ltd", "gmbh", "foundation", "project",
    "team", "office", "online", "cloud", "connect", "assistant", "control", "panel",
    "utility", "utilities", "support", "game", "games", "image", "video", "audio", "sound",
    "music", "photo", "foto", "net", "core", "host", "agent", "bundle", "patch", "fix",
    "hotfix", "test", "demo", "trial", "main", "start", "run", "launch",
}
# Abkürzungen / Kurznamen in Dateinamen → Wörter im Programmnamen
ORG_JUNK_ALIASES = {
    "vscode": ("visual", "studio", "code"),
    "vscodium": ("vscodium",),
    "chromesetup": ("chrome",),
    "googlechrome": ("google", "chrome"),
    "ff": ("firefox",),
    "ffsetup": ("firefox",),
    "npp": ("notepad++",),
    "notepadpp": ("notepad++",),
    "notepadplusplus": ("notepad++",),
    "7z": ("7", "zip"),
    "7zip": ("7", "zip"),
    "obs": ("obs", "studio"),
    "obsstudio": ("obs", "studio"),
    "epic": ("epic", "games", "launcher"),
    "epicgames": ("epic", "games", "launcher"),
    "epicinstaller": ("epic", "games", "launcher"),
    "node": ("node", "js"),
    "nodejs": ("node", "js"),
    "rpi": ("raspberry", "pi"),
    "msteams": ("teams",),
    "teamsbootstrapper": ("teams",),
    "tsetup": ("telegram",),
    "vcredist": ("visual", "c++"),
    "dotnet": ("net",),
    "acrordrdc": ("acrobat",),
    "acrordrdcx": ("acrobat",),
    "acrordr": ("acrobat",),
    "vlc": ("vlc",),
    "gimp": ("gimp",),
    "putty": ("putty",),
    "winscp": ("winscp",),
    "qbittorrent": ("qbittorrent",),
    "anydesk": ("anydesk",),
}
# Programmfamilien, deren Versionen nebeneinander installiert sind:
# hier muss Haupt.Neben-Version übereinstimmen (python-3.12 ≠ Python 3.11)
ORG_JUNK_VERSIONED = {"python", "net"}
ORG_JUNK_NO_MATCH_EXTS = {".iso", ".img", ".dmg", ".pkg", ".apk"}
ORG_JUNK_DISK_IMAGE_EXTS = {".iso", ".img"}

_ORG_JUNK_WORD = re.compile(r"[^\W_]+(?:\+\+)?")
_ORG_JUNK_PROG_WORD = re.compile(r"[^\W_]+(?:\+\+)?")
_ORG_JUNK_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+|\+\+")
_ORG_JUNK_ALPHA_NUM = re.compile(r"^([A-Za-z]{3,})\d+$")
_ORG_JUNK_VERSION = re.compile(r"(?<![0-9A-UW-Za-uw-z])(\d+)\.(\d+)")
# Fallback für versionsgebundene Familien: "python3.12", "python312"
_ORG_JUNK_STRICT_VER = re.compile(r"(?:python|dotnet|net)[ _-]?v?(\d+)\.(\d+)", re.IGNORECASE)
_ORG_JUNK_STRICT_VER2 = re.compile(r"python[ _-]?(\d)(\d{1,2})(?!\d)", re.IGNORECASE)
# Jahreszahlen (Jahresprodukte: Steuer 2023 ≠ Steuer 2024, VC++ 2010 ≠ 2015-2022)
_ORG_JUNK_YEAR = re.compile(r"(?<!\d)(19[89]\d|20\d\d)(?!\d)")
_ORG_JUNK_7Z = re.compile(r"^7z\d{3,4}", re.IGNORECASE)
_ORG_JUNK_COPY_MARK = re.compile(r"\s*(\(\d+\)|-\s*kopie|-\s*copy|\s+kopie|\s+copy)\s*$",
                                 re.IGNORECASE)
_ORG_JUNK_SUFFIXES = ("installer", "install", "setup", "x64", "x86", "win64", "win32",
                      "64bit", "32bit", "full", "web", "offline", "online")


def _org_junk_strip_suffix(tok: str) -> str:
    changed = True
    while changed:
        changed = False
        for suf in _ORG_JUNK_SUFFIXES:
            if tok.endswith(suf) and len(tok) - len(suf) >= 2:
                tok = tok[: -len(suf)]
                changed = True
    return tok


def _org_junk_is_hash(word: str) -> bool:
    """Zufällige Kennungen (z. B. bei Zoom-Installern) erkennen."""
    if len(word) < 10:
        return False
    if any(c.isdigit() for c in word):
        return True
    parts = _ORG_JUNK_CAMEL.findall(word)
    return len(parts) >= 3 and (len(word) / float(len(parts))) < 3.0


def _org_junk_file_tokens(filename: str):
    """Dateiname → (Liste (Token, Pflicht?, eindeutig?), Version|None)."""
    base = str(filename).replace("\\", "/").rsplit("/", 1)[-1]
    base = unicodedata.normalize("NFC", base)
    stem, _ext = os.path.splitext(base)
    stem = _ORG_JUNK_COPY_MARK.sub("", stem).strip()
    # Versionsgebundene Familien zuerst ("python3.11.5" → 3.11, nicht 11.5)
    m = (_ORG_JUNK_STRICT_VER.search(stem) or _ORG_JUNK_VERSION.search(stem)
         or _ORG_JUNK_STRICT_VER2.search(stem))
    version = (int(m.group(1)), int(m.group(2))) if m else None
    raw: list = []          # (Token, Pflicht?) – Pflicht: 2-Zeichen-Teil eines Kamelworts
    m7 = _ORG_JUNK_7Z.match(stem)
    if m7:
        raw.append(("7z", False))
        stem = stem[m7.end():]
    for word in _ORG_JUNK_WORD.findall(stem):
        wl = word.lower()
        if wl in ORG_JUNK_NOISE:
            continue
        if any(c.isdigit() for c in word):
            ma = _ORG_JUNK_ALPHA_NUM.match(word)
            if not ma or ma.group(1).isupper():
                continue                    # Versionsnummer, Kennung, x64, BRV013 …
            word = ma.group(1)
            wl = word.lower()
            if wl in ORG_JUNK_NOISE:
                continue
        if wl in ORG_JUNK_ALIASES:
            raw.append((wl, False))
            continue
        if not word.isascii():
            raw.append((wl, False))          # Umlaute usw.: als ganzes Wort behalten
            continue
        if _org_junk_is_hash(word):
            continue
        if word.endswith("++"):
            raw.append((wl, False))
            continue
        parts = _ORG_JUNK_CAMEL.findall(word)
        multi = len(parts) >= 2
        for part in parts:
            pl = part.lower()
            if pl.isdigit() or pl == "++":
                continue
            if pl not in ORG_JUNK_NOISE and pl not in ORG_JUNK_ALIASES and part.islower():
                pl = _org_junk_strip_suffix(pl)
            if pl in ORG_JUNK_NOISE:
                continue
            # "ZoomIt", "OperaGX": der 2-Zeichen-Teil unterscheidet das Produkt → Pflicht
            raw.append((pl, multi and len(pl) == 2 and pl.isalpha()))
    # Aliase auf Wortpaare und Einzelwörter anwenden
    toks: list = []
    i = 0
    n = len(raw)
    while i < n:
        t = raw[i][0]
        if i + 1 < n and (t + raw[i + 1][0]) in ORG_JUNK_ALIASES:
            for a in ORG_JUNK_ALIASES[t + raw[i + 1][0]]:
                toks.append((a, True, False))
            i += 2
            continue
        if t in ORG_JUNK_ALIASES:
            for a in ORG_JUNK_ALIASES[t]:
                toks.append((a, True, False))
        else:
            toks.append((t, False, raw[i][1]))
        i += 1
    out: list = []
    seen: set = set()
    for t, is_alias, short_req in toks:
        if t in seen:
            continue
        seen.add(t)
        mandatory = is_alias or len(t) >= 3 or short_req
        distinct = is_alias or (len(t) >= 3 and t not in ORG_JUNK_GENERIC)
        out.append((t, mandatory, distinct))
    return out, version


class _org_JunkProgram(object):
    __slots__ = ("name", "words", "wset", "version", "text", "years")

    def __init__(self, name: str):
        self.name = name
        low = unicodedata.normalize("NFC", name).lower()
        self.words = _ORG_JUNK_PROG_WORD.findall(low)
        self.wset = set(self.words)
        self.text = " ".join(self.words)     # für schnelle Vorauswahl
        m = _ORG_JUNK_VERSION.search(low)
        self.version = (int(m.group(1)), int(m.group(2))) if m else None
        self.years = set(_ORG_JUNK_YEAR.findall(low))


class _org_JunkProgList(list):
    """Vorbereitete Programmliste + Gesamttext für schnelles Aussortieren."""
    alltext = ""


_org_junk_prep_cache: list = [None]     # [(Schlüssel, Liste)] – ein Eintrag, atomar ersetzt


def _org_junk_prepare(programs) -> list:
    try:
        key = tuple(programs or ())
    except TypeError:
        key = ()
    cached = _org_junk_prep_cache[0]
    if cached is not None and cached[0] == key:
        return cached[1]
    out = _org_JunkProgList()
    seen = set()
    for p in key:
        if not isinstance(p, str) or not p.strip() or p in seen:
            continue
        seen.add(p)
        out.append(_org_JunkProgram(p))
    out.alltext = "\n".join(pr.text for pr in out)
    _org_junk_prep_cache[0] = (key, out)
    return out


def _org_junk_cover(tokens: list, prog) -> set | None:
    """Alle Pflicht-Token müssen (einzeln oder zusammengesetzt) Wörter des
    Programmnamens sein. Liefert die verbrauchten Programmwörter oder None."""
    used: set = set()
    i = 0
    n = len(tokens)
    wset = prog.wset
    while i < n:
        found = -1
        joined = ""
        for j in range(min(n, i + 4) - 1, i - 1, -1):
            cand = "".join(t[0] for t in tokens[i:j + 1])
            if cand in wset:
                found = j
                joined = cand
                break
        if found < 0:
            if tokens[i][1]:
                return None
            i += 1
            continue
        used.add(joined)
        i = found + 1
    return used


def _org_junk_extras(prog, used: set) -> int:
    n = 0
    for w in prog.words:
        if w in used or len(w) < 3 or any(c.isdigit() for c in w):
            continue
        if w in ORG_JUNK_NOISE or w in ORG_JUNK_GENERIC:
            continue
        n += 1
    return n


def _org_junk_match_prepared(filename: str, progs: list):
    try:
        tokens, version = _org_junk_file_tokens(filename)
    except Exception:
        return None
    if not tokens:
        return None
    file_distinct = any(t[2] for t in tokens)
    try:
        stem = os.path.splitext(str(filename).replace("\\", "/").rsplit("/", 1)[-1])[0]
        file_years = set(_ORG_JUNK_YEAR.findall(stem))
    except Exception:
        file_years = set()
    strict = any(t[0] in ORG_JUNK_VERSIONED for t in tokens)
    must = [t[0] for t in tokens if t[1]]
    if not must:                               # nur Kürzel (≤ 2 Zeichen) → zu unsicher
        return None
    alltext = getattr(progs, "alltext", None)
    if alltext is not None and not all(m in alltext for m in must):
        return None                            # kein Programm enthält alle Merkmale
    best = None
    best_key = None
    for prog in progs:
        text = prog.text
        if not all(m in text for m in must):   # Vorauswahl ohne Zerlegung
            continue
        used = _org_junk_cover(tokens, prog)
        if used is None:
            continue
        # mind. ein eindeutiges Merkmal (z. B. "team"+"viewer" → "teamviewer")
        if not file_distinct and not any(len(u) >= 3 and u not in ORG_JUNK_GENERIC
                                         and u not in ORG_JUNK_NOISE for u in used):
            continue
        if strict and version is not None:
            if prog.version is None or prog.version != version:
                continue
        elif strict and prog.version is None and "launcher" in prog.wset:
            continue
        if file_years and prog.years and not (file_years & prog.years):
            continue                           # anderes Jahresprodukt (Steuer 2023 ≠ 2024)
        extras = _org_junk_extras(prog, used)
        if extras > 2:
            continue
        key = (extras, len(prog.words), len(prog.name))
        if best_key is None or key < best_key:
            best, best_key = prog, key
    return best.name if best is not None else None


def org_match_installer(filename: str, programs: list) -> str | None:
    """Gibt den DisplayName des installierten Programms zurück, zu dem die
    Installationsdatei eindeutig gehört – sonst None."""
    try:
        return _org_junk_match_prepared(filename, _org_junk_prepare(programs))
    except Exception:
        return None


# Dateien, die einen Ordner als Programmordner (portables Programm, Spiel) ausweisen
ORG_JUNK_PROGRAM_LIB_EXTS = {".dll", ".pak", ".asar", ".ocx", ".bpl", ".so", ".dylib"}
_ORG_JUNK_INSTALLER_HINT = re.compile(r"setup|install|inst[_\-. ]|\binst\b|unins|redist|"
                                      r"bootstrap|web[_\-. ]?installer", re.IGNORECASE)


def _org_junk_parent_key(f) -> str:
    try:
        return str(f.path.parent).lower()
    except Exception:
        return ""


def _org_junk_program_dirs(files) -> set:
    out: set = set()
    for f in files:
        try:
            if (getattr(f, "ext", "") or "").lower() in ORG_JUNK_PROGRAM_LIB_EXTS:
                out.add(_org_junk_parent_key(f))
        except Exception:
            continue
    out.discard("")
    return out


def org_mark_installers(files: list, programs: list, now: float | None = None) -> int:
    """Installationsdateien einordnen: bereits installiert → ORG_JUNK,
    sonst ORG_REVIEW (alt / Datenträgerabbild / Installationsdatei).
    Liefert Anzahl der als Müll markierten Dateien."""
    if not files:
        return 0
    if now is None:
        now = time.time()
    try:
        progs = _org_junk_prepare(programs)
    except Exception:
        progs = []
    count = 0
    cache: dict = {}
    prog_dirs = _org_junk_program_dirs(files)
    for f in files:
        try:
            if getattr(f, "kind", "") != "installer" or getattr(f, "in_project", False):
                continue
            if f.tier == ORG_JUNK:          # schon Müll (z. B. Duplikat) – nicht ändern
                continue
            ext = (f.ext or "").lower()
            name = _org_junk_name(f)
            # .exe neben Programmbibliotheken (portable Programme, Spiele): ist das
            # Programm selbst, kein Installer → nie Müll, nicht einsortieren
            if (ext == ".exe" and prog_dirs and _org_junk_parent_key(f) in prog_dirs
                    and not _ORG_JUNK_INSTALLER_HINT.search(name)):
                if f.tier != ORG_PROTECT:
                    f.tier = ORG_REVIEW
                f.add_reason("Programmdatei in einem Programmordner")
                continue
            if f.tier == ORG_PROTECT:       # als wichtig erkannt → nie herabstufen
                f.add_reason("Installationsdatei")
                continue
            f.category = "installer"
            if ext in ORG_JUNK_DISK_IMAGE_EXTS:
                f.tier = ORG_REVIEW
                f.add_reason("Datenträgerabbild")
                continue
            match = None
            if ext not in ORG_JUNK_NO_MATCH_EXTS and progs:
                key = name.lower()
                if key not in cache:
                    cache[key] = _org_junk_match_prepared(name, progs)
                match = cache[key]
            if match:
                f.tier = ORG_JUNK
                try:
                    f.score = max(float(f.score or 0.0), 0.85)
                except (TypeError, ValueError):
                    f.score = 0.85
                f.add_reason("Programm „%s“ ist bereits installiert" % match)
                count += 1
                continue
            f.tier = ORG_REVIEW
            age = _org_junk_age(f, now)
            if age > ORG_JUNK_INSTALLER_AGE and age < 1e11:
                f.add_reason("Alte Installationsdatei (vor %d Tagen geladen)"
                             % int(age // ORG_JUNK_DAY))
            else:
                f.add_reason("Installationsdatei")
        except Exception:
            continue
    return count


# ---------------------------------------------------------------------------
# 4) Leere Ordner
# ---------------------------------------------------------------------------
# Von Programmen angelegte Ordner, die leer bleiben dürfen
ORG_JUNK_EMPTY_KEEP = {
    "custom office templates", "benutzerdefinierte office-vorlagen", "my shapes",
    "meine shapes", "my data sources", "eigene datenquellen", "outlook files",
    "outlook-dateien", "onenote notebooks", "onenote-notizbücher",
}
ORG_JUNK_EMPTY_KEEP = {unicodedata.normalize("NFC", n) for n in ORG_JUNK_EMPTY_KEEP}
_ORG_JUNK_ATTR_HIDDEN = 0x2
_ORG_JUNK_ATTR_SYSTEM = 0x4
_ORG_JUNK_ATTR_REPARSE = 0x400


def _org_junk_names_project(names: set) -> bool:
    if names & ORG_PROJECT_MARKERS:
        return True
    return any(n.endswith((".sln", ".csproj", ".vcxproj", ".xcodeproj")) for n in names)


def _org_junk_dir_excluded(entry, skip: set) -> bool:
    """Ordner, der nie als leer gemeldet und nie betreten wird."""
    nl = unicodedata.normalize("NFC", entry.name).lower()
    if nl in skip or nl.startswith(".") or nl in ORG_JUNK_EMPTY_KEEP:
        return True
    try:
        if entry.is_symlink():
            return True
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return True
    if _org_junk_stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0) or 0
    if attrs & (_ORG_JUNK_ATTR_HIDDEN | _ORG_JUNK_ATTR_SYSTEM | _ORG_JUNK_ATTR_REPARSE):
        return True
    is_junction = getattr(entry, "is_junction", None)
    if is_junction is not None:
        try:
            if is_junction():
                return True
        except OSError:
            return True
    return False


def org_empty_dirs(root: Path, skip=ORG_SKIP_DIRS) -> list:
    """Ordner unter root (ohne root), die in keiner Tiefe eine Datei enthalten.
    Ausgenommen: Projekt-, System-, versteckte Ordner, Links/Junctions, Ordner
    mit desktop.ini. Tiefste zuerst."""
    try:
        return _org_junk_empty_dirs(root, skip)
    except Exception:          # unerwarteter Fehler → lieber nichts vorschlagen
        return []


def _org_junk_empty_dirs(root, skip) -> list:
    if skip is None:
        skip = ORG_SKIP_DIRS
    try:
        root = Path(root)
        if not root.is_dir():
            return []
    except (OSError, TypeError, ValueError):
        return []
    try:
        skip_l = {unicodedata.normalize("NFC", str(s)).lower() for s in (skip or ())}
    except TypeError:
        skip_l = set(ORG_SKIP_DIRS)
    result: list = []
    empty: dict = {}
    # Stapel: (Pfad, Tiefe, erweitert?, Kinder, hat_Inhalt)
    stack: list = [(str(root), 0, False, None, False)]
    while stack:
        d, depth, expanded, children, has_content = stack.pop()
        if expanded:
            child_ok = [empty.pop(c, False) for c in children]
            ok = not has_content and all(child_ok)
            if depth > 0:
                empty[d] = ok
                if ok:
                    result.append((depth, d))
            continue
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            if depth > 0:
                empty[d] = False        # nicht lesbar → nicht anfassen
            continue
        names = {e.name.lower() for e in entries}
        if _org_junk_names_project(names):
            if depth == 0:
                return []               # Wurzel ist selbst ein Projekt → nichts anfassen
            empty[d] = False
            continue
        content = False
        subdirs: list = []
        for e in entries:
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                content = True
                continue
            if not is_dir:
                content = True          # jede Datei (auch desktop.ini, versteckte) zählt
                continue
            if _org_junk_dir_excluded(e, skip_l):
                content = True
                continue
            subdirs.append(e.path)
        stack.append((d, depth, True, subdirs, content))
        for s in subdirs:
            stack.append((s, depth + 1, False, None, False))
    result.sort(key=lambda t: (-t[0], t[1]))
    return [Path(p) for _dpt, p in result]


# ----- org_photos ------------------------------------------------------------
# ===========================================================================
# ORDNER-ASSISTENT – Fotos & Videos: persönlich / Bildschirmfoto / Chat-Bild,
# dazu Gruppierung persönlicher Fotos nach Ereignissen (Urlaub, Feier, Monat).
# ===========================================================================
# Liest selbst keine Dateien: EXIF steht in f.meta["exif"], die Bildgröße in
# f.meta["size"] (vom Planer vorab befüllt). Optional kann ein Leser (reader)
# übergeben werden, der fehlende Metadaten nachliefert.
# Alle Namen beginnen mit org_ / Org / ORG_ / _org_ph_ (Einzeldatei-Programm).
# Nur Standardbibliothek.

import math
import re
import time
from datetime import datetime as _org_ph_datetime
from datetime import timedelta as _org_ph_timedelta


# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
ORG_MONTH_NAMES_DE = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
                      "August", "September", "Oktober", "November", "Dezember")

# Typische Bildschirmauflösungen (Monitore, Laptops, Smartphones, Tablets).
# Beide Ausrichtungen werden unten automatisch ergänzt.
_ORG_PH_SCREEN_BASE = (
    (1920, 1080), (2560, 1440), (3840, 2160), (1366, 768), (1536, 864), (1440, 900),
    (2880, 1800), (1170, 2532), (1179, 2556), (1080, 2400), (1080, 1920), (2560, 1600),
    (3440, 1440),
    # Ergänzungen: weitere verbreitete Monitore / Laptops
    (1280, 720), (1280, 800), (1280, 1024), (1600, 900), (1680, 1050), (1920, 1200),
    (2560, 1080), (2736, 1824), (2256, 1504), (3024, 1964), (3456, 2234), (2880, 1620),
    (5120, 1440), (5120, 2880), (1360, 768),
    # Surface / MacBook / iMac (Retina- und HiDPI-Bildschirmfotos)
    (2880, 1920), (2496, 1664), (3000, 2000), (2560, 1664), (2940, 1912), (2304, 1440),
    (3840, 2400), (3840, 1600), (4096, 2304), (4480, 2520),
    # iPhone / Android
    (750, 1334), (1125, 2436), (1242, 2688), (828, 1792), (1242, 2208), (1284, 2778),
    (1290, 2796), (1080, 2340), (1080, 2280), (1080, 2160), (1440, 3200), (1440, 3120),
    (1440, 3088), (1440, 2960), (1440, 2560), (720, 1600), (720, 1280),
    (640, 1136), (1206, 2622), (1320, 2868), (1344, 2992), (1080, 2408), (1080, 2412),
    (1220, 2712),
    # iPad
    (2048, 2732), (1668, 2388), (1620, 2160), (1640, 2360), (1536, 2048), (1488, 2266),
    (2064, 2752),
)
ORG_SCREEN_SIZES = frozenset(
    [s for s in _ORG_PH_SCREEN_BASE] + [(h, w) for (w, h) in _ORG_PH_SCREEN_BASE])

# Medien-Kategorien, die dieses Modul vergibt (andere Kategorien bleiben unangetastet)
ORG_MEDIA_CATEGORIES = ("photo_personal", "photo_chat", "screenshot", "image",
                        "video_personal", "video")

ORG_STICKER_MAX_BYTES = 200 * 1024          # kleine .webp = Sticker / Chat-Bild
_ORG_PH_RAW_EXT = {".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2"}
_ORG_PH_RASTER_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif"}
_ORG_PH_DAY_CUTOFF_H = 4                    # Fotos bis 4 Uhr zählen zum Vortag (Feiern)

# ---------------------------------------------------------------------------
# Muster (vorkompiliert, auf den Dateinamen OHNE Endung angewendet)
# ---------------------------------------------------------------------------
# Eindeutige Bildschirmfoto-/Bildschirmaufnahme-Namen (irgendwo im Namen)
_ORG_PH_RE_SHOT_STRONG = re.compile(
    r"screen[\s_\-]?shot|bildschirmfoto|bildschirmaufnahme|bildschirmkopie|"
    r"bildschirmaufzeichnung|screen[\s_\-]?recording|screenrecord|screen[\s_\-]?capture|"
    r"screencap|snipaste|captura de pantalla|capture d.\s?[ée]cran|schermafbeelding|"
    r"^rpreplay",                                   # iOS-Bildschirmaufnahme
    re.I)
# Windows-Kopie-Präfixe ("Kopie von X", "Kopie (2) von X", "Copy of X") vor dem Abgleich
_ORG_PH_RE_COPY_PREFIX = re.compile(
    r"^(?:(?:kopie|copy)(?:\s*\(\d+\))?\s+(?:von|of)\s+)+", re.I)
# Schwache Namen (Snipping Tool, Paint, Mac-Snip): nur Wort + Zahlen/Satzzeichen
_ORG_PH_RE_SHOT_WEAK = re.compile(
    r"(?:capture|snip|ausschneiden|unbenannt|untitled)[\W\d_]*", re.I)
# OBS-Standardname für Aufnahmen: "2024-01-01 12-00-00.mkv"
_ORG_PH_RE_OBS = re.compile(r"\d{4}-\d{2}-\d{2}[ _]\d{2}-\d{2}-\d{2}", re.I)

# Chat-Apps: (Muster, App-Name)
_ORG_PH_CHAT_NAMES = tuple((re.compile(p, re.I), app) for p, app in (
    (r"^(?:img|vid|aud|ptt|stk)-\d{8}-wa\d+", "WhatsApp"),
    (r"^whatsapp[\s_\-]+(?:image|video|bild|gif|audio|sticker|foto)", "WhatsApp"),
    (r"^(?:photo|video|sticker|animation|round)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}",
     "Telegram"),
    (r"^(?:photo|video)_\d+@\d{2}-\d{2}-\d{4}_\d{2}-\d{2}-\d{2}", "Telegram"),
    (r"^signal-\d{4}-\d{2}-\d{2}-\d", "Signal"),
    (r"^threema[\-_]", "Threema"),
    (r"^viber[_ ]", "Viber"),
    (r"^received_\d{6,}", "Facebook Messenger"),
    (r"^fb_img_\d{10,}", "Facebook"),
    (r"^\d{6,}_\d{6,}_\d{6,}_[nobs]$", "Facebook/Instagram"),
    (r"^snapchat-\d+", "Snapchat"),
))
# Ordnernamen: App-Name als eigenes Wort ("Telegramm"/"Vibernetz" sind KEINE Chat-Ordner)
_ORG_PH_CHAT_DIRS = tuple((re.compile(r"(?<![a-z])%s(?![a-z])" % k), app) for k, app in (
    ("whatsapp", "WhatsApp"), ("telegram", "Telegram"), ("threema", "Threema"),
    ("viber", "Viber")))
_ORG_PH_CHAT_DIRS_EXACT = {"signal": "Signal", "messenger": "Facebook Messenger"}
_ORG_PH_SHOT_DIRS = {"screenshots", "screenshot", "bildschirmfotos", "screen shots",
                     "bildschirmfoto", "captures"}

# Kamera-/Handy-Dateinamen für Fotos
_ORG_PH_CAM_PHOTO = tuple(re.compile(p, re.I) for p in (
    r"^img_e?\d{4}(?!\d)",            # iPhone / Canon IMG_1234, bearbeitet IMG_E1234
    r"^img_\d{8}_\d{6}",              # Android IMG_20240101_120000
    r"^img\d{14}",                    # OnePlus/Oppo IMG20240101120000
    r"^dsc[_fn]?\d{4,5}(?!\d)",       # Sony/Nikon/Fuji DSC01234, DSC_1234, DSCF1234
    r"^_dsc\d{4}",
    r"^_mg_\d{4}",                    # Canon Adobe-RGB
    r"^p[1-9a-c]\d{6}(?!\d)",         # Panasonic/Olympus P1010001, Okt–Dez PA/PB/PC…
    r"^imgp\d{4}",                    # Pentax
    r"^win_\d{8}_\d{2}_\d{2}_\d{2}",  # Windows-Kamera-App (Webcam/Surface)
    r"^pxl_\d{8}_\d{6,9}",            # Google Pixel
    r"^\d{8}_\d{6}(?!\d)",            # Samsung/LG 20240101_120000
    r"^mvimg_\d{8}_\d{6}",            # Pixel Bewegungsfoto
    r"^dji_\d{4}",                    # Drohne
    r"^gopr\d{4}", r"^g[hxlp]\d{6}",  # GoPro
    r"^sam_\d{4}", r"^cimg\d{4}", r"^100_\d{4}", r"^imag\d{4}",
    r"^wp_\d{8}", r"^pano_\d{8}", r"^burst\d{10}",
))
# Kamera-/Handy-Dateinamen für Videos
_ORG_PH_CAM_VIDEO = tuple(re.compile(p, re.I) for p in (
    r"^vid_\d{8}_\d{6}", r"^vid\d{14}", r"^video_\d{8}_\d{6}",
    r"^pxl_\d{8}_\d{6,9}", r"^\d{8}_\d{6}(?!\d)", r"^mov_\d{4}",
    r"^img_e?\d{4}(?!\d)", r"^img_\d{8}_\d{6}",     # iPhone IMG_1234.MOV
    r"^dji_\d{4}", r"^gopr\d{4}", r"^g[hxlp]\d{6}",
    r"^mvi_\d{4}", r"^dsc[_fn]?\d{4,5}(?!\d)", r"^p[1-9a-c]\d{6}(?!\d)",
    r"^c\d{4}$", r"^mah\d{5}", r"^m2u\d{5}", r"^win_\d{8}_\d{2}_\d{2}_\d{2}",
))
_ORG_PH_RE_AVCHD_NAME = re.compile(r"^\d{5}$")       # 00001.MTS
_ORG_PH_CAM_DIRS = {"dcim", "camera roll", "eigene aufnahmen", "avchd", "bdmv",
                    "m4root", "kamera", "camera"}
# "PhotoScan" (Google) = abfotografierte Papierfotos → bleibt persönlich
_ORG_PH_RE_SCANNER = re.compile(
    r"(?<!photo)scan|mfc|mfp|officejet|deskjet|laserjet|pixma|ecosys|workforce|imagerunner|"
    r"imageclass|bizhub|perfection|\blide\b|\bfi-\d{4}|\bds-\d{3}", re.I)
_ORG_PH_RE_SHOT_SOFTWARE = re.compile(
    r"screenshot|snipping|greenshot|sharex|lightshot|snagit|bildschirm", re.I)

# Datumsangaben
_ORG_PH_RE_EXIF_DT = re.compile(
    r"^\s*(\d{4})[\-:.](\d{1,2})[\-:.](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?")
_ORG_PH_RE_NAME_DT = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[\-_]?(\d{2})[\-_]?(\d{2})"
    r"(?:[\-_ T]?(\d{2})[\-_.:]?(\d{2})[\-_.:]?(\d{2}))?")


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------
def org_month_name(m: int) -> str:
    """Deutscher Monatsname (1 = Januar); ungültig → ""."""
    try:
        m = int(m)
    except (TypeError, ValueError, OverflowError):
        return ""
    if 1 <= m <= 12:
        return ORG_MONTH_NAMES_DE[m - 1]
    return ""


def _org_ph_exif(f) -> dict:
    try:
        meta = f.meta if isinstance(f.meta, dict) else {}
        ex = meta.get("exif")
        return ex if isinstance(ex, dict) else {}
    except Exception:
        return {}


def _org_ph_str(v) -> str:
    if isinstance(v, bytes):
        try:
            v = v.decode("utf-8", "replace")
        except Exception:
            return ""
    if not isinstance(v, str):
        return ""
    return v.replace("\x00", "").strip()


def _org_ph_dims(f, exif: dict):
    """(Breite, Höhe) aus f.meta["size"], sonst aus EXIF; sonst None."""
    cands = []
    try:
        if isinstance(f.meta, dict):
            cands.append(f.meta.get("size"))
    except Exception:
        pass
    cands.append((exif.get("width"), exif.get("height")))
    for c in cands:
        try:
            if c is None or len(c) != 2:
                continue
            w, h = int(c[0]), int(c[1])
            if w > 0 and h > 0:
                return w, h
        except (TypeError, ValueError, OverflowError):
            continue
    return None


def _org_ph_gps(exif: dict):
    """(lat, lon) als floats, wenn gültig; sonst None."""
    g = exif.get("gps")
    try:
        if g is None or len(g) != 2:
            return None
        lat, lon = float(g[0]), float(g[1])
    except (TypeError, ValueError, OverflowError):
        return None
    if math.isnan(lat) or math.isnan(lon):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    if lat == 0.0 and lon == 0.0:
        return None
    return lat, lon


def _org_ph_mk_dt(y, mo, d, h=0, mi=0, s=0):
    try:
        y, mo, d = int(y), int(mo), int(d)
        h, mi, s = int(h or 0), int(mi or 0), int(s or 0)
        if not (1900 <= y <= 2100):
            return None
        if s == 60:                      # Schaltsekunde
            s = 59
        return _org_ph_datetime(y, mo, d, h, mi, s)
    except (TypeError, ValueError, OverflowError):
        return None


def _org_ph_exif_dt(exif: dict):
    """EXIF-Aufnahmezeit als naive Ortszeit (datetime) oder None."""
    s = _org_ph_str(exif.get("datetime"))
    if not s:
        return None
    m = _ORG_PH_RE_EXIF_DT.match(s)
    if not m:
        return None
    return _org_ph_mk_dt(*m.groups())


def _org_ph_name_dt(stem: str):
    """Datum aus Kamera-Dateinamen (IMG_20240101_120000, PXL_..., 20240101_120000)."""
    m = _ORG_PH_RE_NAME_DT.search(stem)
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    if h is not None:
        dt = _org_ph_mk_dt(y, mo, d, h, mi, s)
        if dt is not None:
            return dt
    return _org_ph_mk_dt(y, mo, d, 12, 0, 0)


def _org_ph_mtime_dt(mtime):
    try:
        t = time.localtime(float(mtime))
        return _org_ph_datetime(t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min,
                        min(t.tm_sec, 59))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _org_ph_dir_parts(f) -> list:
    """Ordnerteile des relativen Pfads (klein), ohne Dateinamen."""
    try:
        rel = str(f.rel or "").replace("\\", "/")
    except Exception:
        return []
    parts = [p.strip().lower() for p in rel.split("/")[:-1]]
    return [p for p in parts if p]


def _org_ph_stem_ext(f):
    try:
        name = str(f.path.name)
    except Exception:
        name = str(getattr(f, "rel", "") or "")
    # Windows-Pfad auf POSIX geparst ("C:\\…\\IMG_1.JPG") → nur der Dateiname
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    ext = getattr(f, "ext", "") or ""
    ext = ext.lower() if isinstance(ext, str) else ""
    if not ext and "." in name:
        ext = "." + name.rsplit(".", 1)[-1].lower()
    stem = name[:-len(ext)] if ext and name.lower().endswith(ext) else name
    return stem, ext


def _org_ph_camera_label(exif: dict) -> str:
    make = _org_ph_str(exif.get("make"))
    model = _org_ph_str(exif.get("model"))
    if make and model and model.lower().startswith(make.split(" ")[0].lower()):
        return model
    return (make + " " + model).strip()


_ORG_PH_REASON_KEY = "_org_ph_reason"     # eigene Begründung (für erneute Einordnung)
_ORG_PH_PROTECT_KEY = "_org_ph_protect"   # vorher von anderem Modul geschützt


def _org_ph_set(f, cat: str, reason: str, score: float) -> None:
    f.category = cat
    f.tier = ORG_CATEGORIES.get(cat, {}).get("tier", "")
    f.score = score
    f.add_reason(reason)
    try:
        f.meta[_ORG_PH_REASON_KEY] = reason
    except Exception:
        pass


def _org_ph_match_stem(stem: str) -> str:
    """Dateiname für den Musterabgleich: ohne Rand-Leerzeichen und Kopie-Präfix."""
    s = stem.strip()
    return _ORG_PH_RE_COPY_PREFIX.sub("", s) or s


def _org_ph_chat_app_from_dirs(parts: list) -> str:
    for p in parts:
        if p in _ORG_PH_CHAT_DIRS_EXACT:
            return _ORG_PH_CHAT_DIRS_EXACT[p]
        for rx, app in _ORG_PH_CHAT_DIRS:
            if rx.search(p):
                return app
    return ""


# ---------------------------------------------------------------------------
# Einordnung
# ---------------------------------------------------------------------------
def _org_ph_classify_one(f) -> bool:
    """Ordnet eine Bild-/Videodatei ein. True, wenn eingeordnet."""
    kind = f.kind
    raw_stem, ext = _org_ph_stem_ext(f)
    stem = _org_ph_match_stem(raw_stem)
    exif = _org_ph_exif(f)
    parts = _org_ph_dir_parts(f)
    make = _org_ph_str(exif.get("make"))
    model = _org_ph_str(exif.get("model"))
    software = _org_ph_str(exif.get("software"))
    has_cam = bool(make or model)
    is_video = kind == "video"

    # Jahr nur aus EXIF-Aufnahmedatum (sonst bleibt f.year unverändert / None)
    dt = _org_ph_exif_dt(exif)
    if dt is not None:
        f.year = dt.year

    # 1) Eindeutiger Bildschirmfoto-/Bildschirmaufnahme-Name
    if _ORG_PH_RE_SHOT_STRONG.search(stem):
        _org_ph_set(f, "screenshot", "Bildschirmaufnahme (Dateiname)" if is_video
                    else "Bildschirmfoto (Dateiname)", 0.95)
        return True
    if is_video and not has_cam and _ORG_PH_RE_OBS.fullmatch(stem):
        _org_ph_set(f, "screenshot", "Bildschirmaufnahme (OBS-Dateiname)", 0.7)
        return True

    # 2) Chat-Apps nach Dateiname
    for rx, app in _ORG_PH_CHAT_NAMES:
        if rx.search(stem):
            what = "Video" if is_video else "Bild"
            _org_ph_set(f, "photo_chat", "%s-%s (Dateiname)" % (app, what), 0.95)
            return True

    # 3) Kamera-/Handy-Daten im EXIF
    if has_cam:
        label = _org_ph_camera_label(exif)
        if _ORG_PH_RE_SCANNER.search(make + " " + model + " " + software):
            _org_ph_set(f, "video" if is_video else "image",
                        "Gescanntes Bild (Gerät: %s)" % label, 0.6)
            return True
        _org_ph_set(f, "video_personal" if is_video else "photo_personal",
                    "Kamera: %s" % label, 0.9)
        return True

    # 4) RAW-Format = immer eigene Kamera
    if ext in _ORG_PH_RAW_EXT:
        _org_ph_set(f, "photo_personal", "Kamera-Rohformat (%s)" % ext.lstrip(".").upper(),
                    0.9)
        return True

    # 5) Chat- bzw. Bildschirmfoto-Ordner
    app = _org_ph_chat_app_from_dirs(parts)
    if app:
        _org_ph_set(f, "photo_chat", "Liegt im %s-Ordner" % app, 0.75)
        return True
    if not is_video and any(p in _ORG_PH_SHOT_DIRS for p in parts):
        _org_ph_set(f, "screenshot", "Liegt im Ordner für Bildschirmfotos", 0.7)
        return True
    if software and _ORG_PH_RE_SHOT_SOFTWARE.search(software):
        _org_ph_set(f, "screenshot", "Bildschirmfoto (Programm: %s)" % software, 0.8)
        return True

    if is_video:
        # 6v) Kamera-Dateinamen / Kamera-Ordner für Videos
        if any(rx.search(stem) for rx in _ORG_PH_CAM_VIDEO) or \
                (ext in (".mts", ".m2ts") and _ORG_PH_RE_AVCHD_NAME.match(stem)):
            _org_ph_set(f, "video_personal", "Kamera-Dateiname (%s)" % stem, 0.8)
            return True
        if any(p in _ORG_PH_CAM_DIRS for p in parts):
            _org_ph_set(f, "video_personal", "Liegt im Kamera-Ordner (DCIM)", 0.75)
            return True
        _org_ph_set(f, "video", "Video", 0.4)
        return True

    is_png = ext == ".png"
    dims = _org_ph_dims(f, exif)

    # 6) PNG ohne Kamera-Daten in Bildschirmgröße (auch iPhone IMG_1234.PNG)
    if is_png and dims is not None and dims in ORG_SCREEN_SIZES:
        _org_ph_set(f, "screenshot", "Bildschirmfoto (PNG %d×%d, ohne Kamera-Daten)"
                    % dims, 0.7)
        return True

    # 7) Kamera-Dateinamen / Kamera-Ordner (PNG zählt nicht: Kameras speichern kein PNG)
    if not is_png:
        if any(rx.search(stem) for rx in _ORG_PH_CAM_PHOTO):
            _org_ph_set(f, "photo_personal", "Kamera-Dateiname (%s)" % stem, 0.75)
            return True
        if any(p in _ORG_PH_CAM_DIRS for p in parts) and ext not in (".gif", ".svg"):
            _org_ph_set(f, "photo_personal", "Liegt im Kamera-Ordner (DCIM)", 0.7)
            return True

    # 8) Schwache Bildschirmfoto-Namen (Snipping Tool "Capture", Paint "Unbenannt")
    if ext in _ORG_PH_RASTER_EXT and _ORG_PH_RE_SHOT_WEAK.fullmatch(stem):
        _org_ph_set(f, "screenshot", "Bildschirmfoto (typischer Name „%s“)" % stem, 0.6)
        return True

    # 9) Kleine .webp = Sticker / Webbild aus Chats
    if ext == ".webp":
        try:
            small = 0 <= int(f.size) < ORG_STICKER_MAX_BYTES
        except (TypeError, ValueError):
            small = False
        if small:
            _org_ph_set(f, "photo_chat", "Sticker oder Chat-Bild (kleine WebP-Datei)", 0.55)
            return True

    _org_ph_set(f, "image", "Bild", 0.4)
    return True


def org_classify_media(files: list, reader=None) -> int:
    """Ordnet Bilder/Videos ein: photo_personal, photo_chat, screenshot, image,
    video_personal, video. Übersprungen: in_project, Stufe junk und Dateien, die
    bereits eine Nicht-Medien-Kategorie haben (z. B. "id" für einen Ausweis-Scan).
    reader (optional): reader(f) -> dict, wird mit f.meta zusammengeführt, wenn
    f.meta noch keinen "exif"-Eintrag hat. Liefert die Anzahl eingeordneter Dateien."""
    n = 0
    try:
        items = list(files or [])
    except Exception:
        return 0
    for f in items:
        try:
            if getattr(f, "kind", "") not in ("image", "video"):
                continue
            if getattr(f, "in_project", False) or getattr(f, "tier", "") == ORG_JUNK:
                continue
            cat = getattr(f, "category", "") or ""
            if cat and cat not in ORG_MEDIA_CATEGORIES and cat != "other":
                continue
            if not isinstance(f.meta, dict):
                f.meta = {}
            if reader is not None and "exif" not in f.meta:
                try:
                    extra = reader(f)
                    if isinstance(extra, dict):
                        f.meta.update(extra)
                except Exception:
                    pass
            # Erneute Einordnung: eigene alte Begründung entfernen (fremde bleiben)
            old_reason = f.meta.get(_ORG_PH_REASON_KEY)
            if cat in ORG_MEDIA_CATEGORIES and old_reason and isinstance(f.reasons, list):
                f.reasons[:] = [r for r in f.reasons if r != old_reason]
            # Von einem anderen Modul als wichtig markiert → nie herabstufen
            # (Merker in meta, damit auch eine zweite Einordnung nicht herabstuft)
            keep_protect = bool(f.meta.get(_ORG_PH_PROTECT_KEY)) or (
                getattr(f, "tier", "") == ORG_PROTECT and cat not in ORG_MEDIA_CATEGORIES)
            if _org_ph_classify_one(f):
                n += 1
            if keep_protect:
                f.meta[_ORG_PH_PROTECT_KEY] = True
                if f.tier != ORG_PROTECT:
                    f.tier = ORG_PROTECT
                    f.add_reason("Bleibt geschützt (von anderer Prüfung als wichtig erkannt)")
        except Exception:
            continue                      # defekte Einträge überspringen
    return n


# ---------------------------------------------------------------------------
# Ereignisse
# ---------------------------------------------------------------------------
def org_haversine_km(a, b) -> float:
    """Großkreis-Entfernung in km zwischen (lat, lon)-Paaren; ungültig → 0.0."""
    try:
        lat1, lon1 = math.radians(float(a[0])), math.radians(float(a[1]))
        lat2, lon2 = math.radians(float(b[0])), math.radians(float(b[1]))
        dlat, dlon = lat2 - lat1, lon2 - lon1
        h = math.sin(dlat / 2) ** 2 + \
            math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(max(0.0, h))))
    except (TypeError, ValueError, IndexError, KeyError, OverflowError):
        return 0.0


def _org_ph_median(vals: list) -> float:
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _org_ph_median_point(points: list):
    """Median (lat, lon); Längengrade über die Datumsgrenze (±180°) werden verschoben."""
    if not points:
        return None
    lons = [p[1] for p in points]
    if max(lons) - min(lons) > 180.0:
        lons = [x + 360.0 if x < 0 else x for x in lons]
    lon = _org_ph_median(lons)
    if lon > 180.0:
        lon -= 360.0
    return (_org_ph_median([p[0] for p in points]), lon)


def _org_ph_home_from(points: list):
    """Häufigster Aufenthaltsort: 1°×1°-Gitterzelle mit GPS-Fotos in den meisten
    verschiedenen Monaten, dann an den meisten Tagen (Zuhause verteilt sich über das Jahr,
    eine Reise – auch mit 1000 Fotos – ballt sich in wenigen Tagen), zuletzt die meisten
    Fotos; davon der Median der Punkte. points: (lat, lon, Tag-Ordinal) – Tag darf None sein."""
    if not points:
        return None
    cells: dict = {}
    for p in points:
        key = (math.floor(p[0]), math.floor(p[1]))
        c = cells.get(key)
        if c is None:
            c = cells[key] = (set(), [])
        c[0].add(p[2])
        c[1].append(p)
    def months(days):
        out = set()
        for d in days:
            try:
                dt = _org_ph_datetime.fromordinal(int(d))
                out.add((dt.year, dt.month))
            except (TypeError, ValueError, OverflowError):
                pass
        return len(out)

    best = min(cells.items(), key=lambda kv: (-months(kv[1][0]), -len(kv[1][0]),
                                              -len(kv[1][1]), kv[0]))
    return _org_ph_median_point(best[1][1])


def _org_ph_valid_home(home):
    try:
        if home is None or isinstance(home, (str, bytes, dict)) or len(home) != 2:
            return None
        lat, lon = float(home[0]), float(home[1])
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def _org_ph_ymd(d) -> str:
    return "%04d-%02d-%02d" % (d.year, d.month, d.day)


def _org_ph_event_name(first: _org_ph_datetime, last: _org_ph_datetime) -> str:
    start = first.date()
    # Nachtfotos bis 4 Uhr gehören noch zum Vortag (Silvester, Feiern)
    try:
        end = (last - _org_ph_timedelta(hours=_ORG_PH_DAY_CUTOFF_H)).date()
    except OverflowError:
        end = last.date()
    if end < start:
        end = start
    if end == start:
        return _org_ph_ymd(start)
    if end.year == start.year:
        return "%s bis %02d-%02d" % (_org_ph_ymd(start), end.month, end.day)
    return "%s bis %s" % (_org_ph_ymd(start), _org_ph_ymd(end))


def org_photo_dt(f):
    """Bezugszeit eines Fotos (naive Ortszeit): EXIF → Dateiname → Änderungsdatum."""
    try:
        exif = _org_ph_exif(f)
        dt = _org_ph_exif_dt(exif)
        if dt is not None:
            return dt
        stem, _ext = _org_ph_stem_ext(f)
        dt = _org_ph_name_dt(_org_ph_match_stem(stem))
        if dt is not None:
            return dt
        return _org_ph_mtime_dt(getattr(f, "mtime", None))
    except Exception:
        return None


def _org_ph_set_event(items: list, name: str, year: int) -> None:
    for it in items:
        try:
            it[2].event = name
            it[2].year = year
        except Exception:
            continue


def _org_ph_month_buckets(items: list) -> None:
    for it in items:
        d = it[0]
        try:
            it[2].event = "%04d-%02d %s" % (d.year, d.month, org_month_name(d.month))
            it[2].year = d.year
        except Exception:
            continue


def _org_ph_away_runs(cl: list, home_pt) -> list:
    """Teilt einen Cluster in Abschnitte zu Hause / unterwegs (> 100 km).
    Fotos ohne GPS übernehmen den Zustand des vorherigen Fotos. → [(unterwegs, items)]"""
    last = None
    for it in cl:
        if it[3] is not None:
            last = org_haversine_km(it[3], home_pt) > 100.0
            break
    if last is None:
        return [(False, cl)]
    runs = []
    cur: list = []
    cur_flag = last
    for it in cl:
        flag = (org_haversine_km(it[3], home_pt) > 100.0) if it[3] is not None else last
        last = flag
        if cur and flag != cur_flag:
            runs.append((cur_flag, cur))
            cur = []
        cur_flag = flag
        cur.append(it)
    if cur:
        runs.append((cur_flag, cur))
    return runs


def org_photo_events(files: list, min_event: int = 6, gap_hours: float = 36.0,
                     home=None, max_days: float = 31.0) -> int:
    """Gruppiert persönliche Fotos/Videos zu Ereignissen und setzt f.event/f.year.
    Cluster = aufeinanderfolgende Aufnahmen mit Abstand <= gap_hours. Ab min_event
    Dateien: "2024-07-01" / "2024-07-01 bis 07-14" / "2023-12-30 bis 2024-01-02",
    mit " – Reise", wenn der GPS-Median > 100 km von home (bzw. vom häufigsten Ort)
    entfernt ist. Kleinere Cluster und Dauer-Cluster (> max_days, Reisen > 3×max_days)
    kommen in Monatsordner "2024-07 Juli"; aus Dauer-Clustern (z. B. täglich ein Foto)
    werden Reise-Abschnitte (GPS > 100 km) trotzdem als Ereignis herausgelöst.
    Liefert die Anzahl benannter Ereignisse."""
    try:
        min_event = max(1, int(min_event))
    except (TypeError, ValueError, OverflowError):
        min_event = 6
    try:
        gap_s = float(gap_hours) * 3600.0
        if math.isnan(gap_s):
            raise ValueError
        gap_s = max(0.0, gap_s)
    except (TypeError, ValueError, OverflowError):
        gap_s = 36.0 * 3600.0
    try:
        max_span_s = float(max_days) * 86400.0
        if math.isnan(max_span_s):
            raise ValueError
        max_span_s = max(0.0, max_span_s)
    except (TypeError, ValueError, OverflowError):
        max_span_s = 31.0 * 86400.0
    try:
        items = list(files or [])
    except Exception:
        return 0

    dated = []          # (datetime, laufende Nr., OrgFile, gps)
    all_gps = []        # (lat, lon, Tag)
    for i, f in enumerate(items):
        try:
            if getattr(f, "category", "") not in ("photo_personal", "video_personal"):
                continue
            if getattr(f, "in_project", False):
                continue
            dt = org_photo_dt(f)
            if dt is None:
                continue
            g = _org_ph_gps(_org_ph_exif(f))
            if g is not None:
                all_gps.append((g[0], g[1], dt.toordinal()))
            dated.append((dt, i, f, g))
        except Exception:
            continue
    if not dated:
        return 0
    dated.sort(key=lambda t: (t[0], t[1]))

    home_pt = _org_ph_valid_home(home)
    if home_pt is None:
        home_pt = _org_ph_home_from(all_gps)

    # Cluster bilden (eine Schleife, O(n))
    clusters = []
    cur = [dated[0]]
    for prev, item in zip(dated, dated[1:]):
        if (item[0] - prev[0]).total_seconds() > gap_s:
            clusters.append(cur)
            cur = []
        cur.append(item)
    clusters.append(cur)

    n_events = 0
    for cl in clusters:
        first, last = cl[0][0], cl[-1][0]
        travel = False
        if home_pt is not None:
            med = _org_ph_median_point([it[3] for it in cl if it[3] is not None])
            if med is not None and org_haversine_km(med, home_pt) > 100.0:
                travel = True
        span = (last - first).total_seconds()
        limit = max_span_s * 3 if travel else max_span_s
        if len(cl) >= min_event and span <= limit:
            name = _org_ph_event_name(first, last)
            if travel:
                name += " – Reise"
            n_events += 1
            _org_ph_set_event(cl, name, first.year)
        elif span > limit and home_pt is not None:
            # Dauer-Cluster: Reise-Abschnitte herauslösen, Rest in Monatsordner
            for away, run in _org_ph_away_runs(cl, home_pt):
                r_first, r_last = run[0][0], run[-1][0]
                if away and len(run) >= min_event and \
                        (r_last - r_first).total_seconds() <= max_span_s * 3:
                    n_events += 1
                    _org_ph_set_event(run, _org_ph_event_name(r_first, r_last) + " – Reise",
                                      r_first.year)
                else:
                    _org_ph_month_buckets(run)
        else:
            _org_ph_month_buckets(cl)
    return n_events


# ----- org_shell -------------------------------------------------------------
# ===========================================================================
# ORDNER-ASSISTENT – Explorer-Integration (Rechtsklick auf Ordner) + Known Folders
# ===========================================================================
"""Windows-Explorer-Kontextmenü „Mit Desktop Sorter aufräumen“ und Windows-Standardordner.

Kontextmenü:
  Die Einträge werden pro Benutzer unter HKEY_CURRENT_USER\\Software\\Classes angelegt
  (keine Administratorrechte nötig):
    * Directory\\shell\\DesktopSorter             – Rechtsklick auf einen Ordner   (%1)
    * Directory\\Background\\shell\\DesktopSorter  – Rechtsklick in einen Ordner   (%V)
    * Drive\\shell\\DesktopSorter                 – Rechtsklick auf ein Laufwerk   (%1)

  Windows 11: Solche klassischen Einträge erscheinen erst unter
  „Weitere Optionen anzeigen“ (Umschalt+F10 / klassisches Menü). Für das neue,
  kompakte Win11-Menü bräuchte es eine paketierte App (IExplorerCommand +
  Sparse-Package/MSIX) – das ist bewusst nicht Teil dieses Moduls.

Explorer-Eigenheit beim Aufruf:
  Für Laufwerke (und %V im Laufwerkshintergrund) wird "C:\\" übergeben. Durch die
  Windows-Kommandozeilenregeln maskiert der Backslash das schließende
  Anführungszeichen, im Programm kommt dann  C:"  an. org_parse_cli_folder()
  bereinigt das (und weitere Fälle).

Alle Registry-Funktionen nehmen ein winreg-artiges Modul `reg` entgegen (für Tests);
ohne `reg` wird auf Windows das echte winreg benutzt, sonst geben sie False/None zurück.
Nur Standardbibliothek.
"""

import os
import re
import sys
from pathlib import Path

# ---------- Registry-Layout ----------
ORG_SHELL_KEY_NAME = "DesktopSorter"
ORG_SHELL_DEFAULT_LABEL = "Mit Desktop Sorter aufräumen"
ORG_SHELL_CLI_FLAG = "--organize"
# (Registry-Pfad unter HKCU, Platzhalter, MultiSelectModel setzen?)
ORG_SHELL_REG_ENTRIES: tuple = (
    ("Software\\Classes\\Directory\\shell\\" + ORG_SHELL_KEY_NAME, "%1", True),
    ("Software\\Classes\\Directory\\Background\\shell\\" + ORG_SHELL_KEY_NAME, "%V", False),
    ("Software\\Classes\\Drive\\shell\\" + ORG_SHELL_KEY_NAME, "%1", True),
)
ORG_SHELL_MAIN_KEY = ORG_SHELL_REG_ENTRIES[0][0]
ORG_SHCNE_ASSOCCHANGED = 0x08000000
ORG_SHCNF_IDLIST = 0x0000

# ---------- Known Folders ----------
ORG_KNOWN_FOLDER_GUIDS: dict[str, str] = {
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}
# Ersatzname unter Path.home(), falls die Windows-API nicht verfügbar ist
ORG_KNOWN_FOLDER_FALLBACK: dict[str, str] = {
    "documents": "Documents", "downloads": "Downloads", "pictures": "Pictures",
    "desktop": "Desktop", "music": "Music", "videos": "Videos",
}
# Deutsche/englische Varianten → kanonischer Name
ORG_KNOWN_FOLDER_ALIASES: dict[str, str] = {
    "documents": "documents", "document": "documents", "docs": "documents",
    "dokumente": "documents", "eigene dateien": "documents", "my documents": "documents",
    "downloads": "downloads", "download": "downloads",
    "pictures": "pictures", "picture": "pictures", "photos": "pictures", "bilder": "pictures",
    "fotos": "pictures", "my pictures": "pictures",
    "desktop": "desktop", "schreibtisch": "desktop",
    "music": "music", "musik": "music", "my music": "music",
    "videos": "videos", "video": "videos", "my videos": "videos",
    # ältere deutsche Windows-Namen
    "eigene dokumente": "documents", "eigene bilder": "pictures",
    "eigene musik": "music", "eigene videos": "videos", "desktop-ordner": "desktop",
}
# XDG-Schlüssel (nur Nicht-Windows, z. B. „~/Dokumente“ auf deutschem Linux)
_ORG_XDG_KEYS: dict[str, str] = {
    "documents": "XDG_DOCUMENTS_DIR", "downloads": "XDG_DOWNLOAD_DIR",
    "pictures": "XDG_PICTURES_DIR", "desktop": "XDG_DESKTOP_DIR",
    "music": "XDG_MUSIC_DIR", "videos": "XDG_VIDEOS_DIR",
}

_ORG_RE_DRIVE_ONLY = re.compile(r"^[A-Za-z]:$")
_ORG_RE_DRIVE_ROOT = re.compile(r"^[A-Za-z]:[\\/]+$")
_ORG_RE_WINLIKE = re.compile(r"^(?:[A-Za-z]:|\\\\|//)")
_ORG_RE_XDG_LINE = re.compile(r'^\s*(XDG_[A-Z]+_DIR)\s*=\s*"?(.*?)"?\s*$')
_ORG_RE_ICON_INDEX = re.compile(r",\s*-?\d+\s*$")
_ORG_RE_LONG_DRIVE = re.compile(r"^\\\\[?.]\\[A-Za-z]:$")   # \\?\C:  → \\?\C:\


# ===========================================================================
# Hilfen
# ===========================================================================
def _org_shell_reg(reg):
    """Injiziertes winreg-Modul oder das echte (nur Windows); sonst None."""
    if reg is not None:
        return reg
    if os.name != "nt":
        return None
    try:
        import winreg  # type: ignore
        return winreg
    except ImportError:
        return None


def _org_shell_notify() -> None:
    """Explorer über geänderte Zuordnungen informieren (Menü sofort aktualisieren)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(ORG_SHCNE_ASSOCCHANGED, ORG_SHCNF_IDLIST,
                                             None, None)
    except Exception:
        pass


def _org_shell_to_str(value) -> str:
    """Beliebigen Wert (str, bytes, Path …) robust in str wandeln; nie Ausnahme."""
    if value is None:
        return ""
    try:
        if isinstance(value, (bytes, bytearray)):
            return os.fsdecode(bytes(value))
        if not isinstance(value, str) and hasattr(value, "__fspath__"):
            v = os.fspath(value)
            return os.fsdecode(v) if isinstance(v, bytes) else v
        return str(value)
    except Exception:
        return ""


def _org_shell_clean_exe(value) -> str:
    """Pfad für die Kommandozeile säubern (Anführungszeichen entfernen, Leerraum)."""
    s = _org_shell_to_str(value).replace("\x00", "").strip()
    # Windows-Pfade dürfen kein " enthalten – mitgegebene Quotes entfernen
    s = s.replace('"', "").strip()
    return s


def org_shell_quote(path: str) -> str:
    """Pfad für eine Windows-Kommandozeile in Anführungszeichen setzen.

    Abschließende Backslashes werden verdoppelt, damit sie das schließende
    Anführungszeichen nicht maskieren (CommandLineToArgvW-Regeln)."""
    s = _org_shell_clean_exe(path)
    trail = len(s) - len(s.rstrip("\\"))
    return '"' + s + "\\" * trail + '"'


def _org_shell_icon_value(icon_path: str) -> str:
    """Icon-Wert: enthält der Pfad ein Komma, ',0' anhängen – sonst liest Explorer
    alles nach dem letzten Komma als Symbolindex ("C:\\Müller, Jürgen\\x.exe")."""
    if "," in icon_path and not _ORG_RE_ICON_INDEX.search(icon_path):
        return icon_path + ",0"
    return icon_path


def org_context_menu_build_command(command_exe: str, script: str = "",
                                   placeholder: str = "%1") -> str:
    """Befehlszeile für den Registry-Eintrag, z. B.
    "C:\\...\\pythonw.exe" "C:\\...\\DesktopSorter.pyw" --organize "%1"
    Ohne `script` (z. B. eingefrorene .exe) entfällt das Skriptargument."""
    try:
        parts = [org_shell_quote(command_exe)]
        sc = _org_shell_clean_exe(script)
        if sc:
            parts.append(org_shell_quote(sc))
        parts.append(ORG_SHELL_CLI_FLAG)
        parts.append('"' + str(placeholder or "%1") + '"')
        return " ".join(parts)
    except Exception:
        return ""


def org_shell_default_launcher() -> tuple:
    """(command_exe, script) für das laufende Programm.

    Eingefroren (PyInstaller o. ä.): (sys.executable, ""). Sonst bevorzugt
    pythonw.exe (kein Konsolenfenster) neben sys.executable + Pfad des Hauptskripts."""
    try:
        exe = sys.executable or ""
        if getattr(sys, "frozen", False):
            return (exe, "")
        if os.name == "nt" and exe:
            p = Path(exe)
            if p.name.lower() == "python.exe":
                pw = p.with_name("pythonw.exe")
                if pw.exists():
                    exe = str(pw)
        main = sys.modules.get("__main__")
        script = getattr(main, "__file__", "") or (sys.argv[0] if sys.argv else "")
        if script:
            script = os.path.abspath(script)
        return (exe, script)
    except Exception:
        return ("", "")


# ===========================================================================
# Kontextmenü installieren / entfernen / prüfen
# ===========================================================================
def org_context_menu_install(command_exe: str, script: str,
                             label: str = ORG_SHELL_DEFAULT_LABEL,
                             icon=None, reg=None) -> bool:
    """Rechtsklick-Einträge für Ordner, Ordnerhintergrund und Laufwerke anlegen
    (pro Benutzer, ohne Adminrechte). True bei Erfolg.

    Hinweis Windows 11: Die Einträge stehen im klassischen Menü unter
    „Weitere Optionen anzeigen“; das moderne Menü erfordert eine paketierte App
    (IExplorerCommand + Sparse-Package) und ist hier nicht abgedeckt."""
    w = _org_shell_reg(reg)
    if w is None:
        return False
    try:
        exe = _org_shell_clean_exe(command_exe)
        if not exe:
            return False
        # Steuerzeichen (Zeilenumbrüche, NUL) gehören nicht ins Menü
        label_s = "".join(c for c in _org_shell_to_str(label) if c >= " ").strip() \
            or ORG_SHELL_DEFAULT_LABEL
        icon_s = _org_shell_icon_value(_org_shell_clean_exe(icon) or exe)
        sz = getattr(w, "REG_SZ", 1)
        cmds = [org_context_menu_build_command(exe, script, ph)
                for _kp, ph, _s in ORG_SHELL_REG_ENTRIES]
        if not all(cmds):
            return False
    except Exception:
        return False
    try:
        for (key_path, _placeholder, single), cmd in zip(ORG_SHELL_REG_ENTRIES, cmds):
            with w.CreateKeyEx(w.HKEY_CURRENT_USER, key_path, 0, w.KEY_WRITE) as k:
                w.SetValueEx(k, "", 0, sz, label_s)
                w.SetValueEx(k, "Icon", 0, sz, icon_s)
                if single:
                    w.SetValueEx(k, "MultiSelectModel", 0, sz, "Single")
            with w.CreateKeyEx(w.HKEY_CURRENT_USER, key_path + "\\command", 0,
                               w.KEY_WRITE) as k:
                w.SetValueEx(k, "", 0, sz, cmd)
    except Exception:
        # halb geschriebene Einträge wieder entfernen → Zustand bleibt eindeutig
        try:
            for key_path, _ph, _single in ORG_SHELL_REG_ENTRIES:
                _org_reg_delete_tree(w, key_path)
        except Exception:
            pass
        _org_shell_notify()
        return False
    _org_shell_notify()
    return True


def _org_reg_delete_tree(w, path: str, _depth: int = 0) -> bool:
    """Schlüssel samt Unterschlüsseln löschen (Kinder zuerst). Fehlt er: ok."""
    if _depth > 64:
        return False   # Schutz vor Endlosrekursion (defektes reg-Modul)
    # Unterschlüssel einsammeln
    children = []
    try:
        with w.OpenKey(w.HKEY_CURRENT_USER, path, 0, w.KEY_READ) as k:
            i = 0
            while i < 10000:
                try:
                    children.append(w.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except FileNotFoundError:
        return True
    except OSError:
        return False
    ok = True
    for c in children:
        c = _org_shell_to_str(c).strip("\\")
        if not c:
            ok = False   # leerer Name würde wieder auf denselben Schlüssel zeigen
            continue
        if not _org_reg_delete_tree(w, path + "\\" + c, _depth + 1):
            ok = False
    try:
        w.DeleteKey(w.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        pass
    except OSError:
        ok = False
    return ok


def org_context_menu_uninstall(reg=None) -> bool:
    """Alle Rechtsklick-Einträge entfernen (command zuerst, dann Elternschlüssel).
    Fehlende Schlüssel sind kein Fehler; mehrfacher Aufruf ist unbedenklich."""
    w = _org_shell_reg(reg)
    if w is None:
        return False
    ok = True
    try:
        for key_path, _ph, _single in ORG_SHELL_REG_ENTRIES:
            if not _org_reg_delete_tree(w, key_path):
                ok = False
    except Exception:
        return False
    _org_shell_notify()
    return ok


def org_context_menu_installed(reg=None) -> bool:
    """True, wenn Directory\\shell\\DesktopSorter\\command existiert."""
    w = _org_shell_reg(reg)
    if w is None:
        return False
    try:
        with w.OpenKey(w.HKEY_CURRENT_USER, ORG_SHELL_MAIN_KEY + "\\command", 0,
                       w.KEY_READ):
            return True
    except Exception:
        return False


def org_context_menu_command(reg=None):
    """Aktuell eingetragene Befehlszeile (Ordner-Eintrag) oder None.
    Damit lässt sich erkennen, ob das Skript inzwischen verschoben wurde."""
    return _org_reg_read_command(_org_shell_reg(reg), ORG_SHELL_MAIN_KEY)


def _org_reg_read_command(w, key_path: str):
    """(Default)-Wert von <key_path>\\command oder None."""
    if w is None:
        return None
    try:
        with w.OpenKey(w.HKEY_CURRENT_USER, key_path + "\\command", 0,
                       w.KEY_READ) as k:
            value, _typ = w.QueryValueEx(k, "")
    except Exception:
        return None
    if value is None:
        return None
    s = _org_shell_to_str(value)
    return s if s else None


def org_context_menu_is_current(command_exe: str, script: str, reg=None) -> bool:
    """True, wenn alle drei Einträge existieren und auf genau dieses Programm/Skript
    zeigen (Groß-/Kleinschreibung egal). False = fehlt oder veraltet → neu installieren."""
    try:
        w = _org_shell_reg(reg)
        if w is None:
            return False
        for key_path, placeholder, _single in ORG_SHELL_REG_ENTRIES:
            cur = _org_reg_read_command(w, key_path)
            want = org_context_menu_build_command(command_exe, script, placeholder)
            if not cur or not want or cur.strip().casefold() != want.casefold():
                return False
        return True
    except Exception:
        return False


# ===========================================================================
# Kommandozeile: Ordner nach --organize
# ===========================================================================
def _org_cli_clean(raw) -> str:
    """Einen von Explorer übergebenen Pfad säubern (Quotes, Backslash-Eigenheit)."""
    s = _org_shell_to_str(raw).replace("\x00", "").strip()
    # führende Quotes weg; ab dem ersten weiteren " ist alles Müll
    # (Windows-Pfade können kein " enthalten: 'C:" --x' → 'C:')
    s = s.lstrip('"').strip()
    if '"' in s:
        s = s[:s.index('"')].rstrip()
    if not s:
        return ""
    if s.lower().startswith("file:"):
        try:
            from urllib.parse import unquote, urlparse
            from urllib.request import url2pathname
            u = urlparse(s)
            if u.netloc and u.netloc.lower() != "localhost":
                s = "\\\\" + u.netloc + url2pathname(u.path).replace("/", "\\")
            else:
                s = url2pathname(u.path) if os.name == "nt" else unquote(u.path)
        except Exception:
            return ""
        if not s:
            return ""
    if _ORG_RE_DRIVE_ONLY.match(s):
        return s + "\\"
    if _ORG_RE_DRIVE_ROOT.match(s):
        return s[:2] + "\\"
    if _ORG_RE_WINLIKE.match(s) or "\\" in s:
        t = s.rstrip("\\/")
        if _ORG_RE_LONG_DRIVE.match(t):
            return t + "\\"          # \\?\C:\ bleibt Laufwerkswurzel
        return t if t else s
    if len(s) > 1:
        t = s.rstrip("/")
        return t if t else "/"
    return s


def _org_cli_candidates(value: str, rest: list) -> list:
    """Kandidaten: – falls Explorer nicht gequotet hat – der Wert mit den folgenden
    Argumenten per Leerzeichen verbunden (LÄNGSTER zuerst, sonst gewinnt bei
    „C:\\My Docs“ der falsche Ordner „C:\\My“), dann der Wert selbst, ggf. in '…'."""
    v = _org_shell_to_str(value)
    joined_list = []
    joined = v
    for extra in rest[:32]:
        e = _org_shell_to_str(extra)
        if e.startswith("--"):
            break
        joined = joined + " " + e
        joined_list.append(joined)
    out = list(reversed(joined_list))
    out.append(v)
    vs = v.strip()
    if len(vs) >= 2 and vs[0] == "'" and vs[-1] == "'":
        out.append(vs[1:-1])
    return out


def org_parse_cli_folder(argv, is_dir=None):
    """Ordner hinter „--organize“ aus argv holen (auch „--organize=PFAD“).

    Behandelt: abschließenden Backslash, Laufwerkswurzel ("C:\\" kommt als C:" an),
    verirrte Anführungszeichen, ungequotete Pfade mit Leerzeichen, file:-URLs.
    Gibt Path zurück oder None, wenn kein Wert da ist oder es kein Ordner ist.
    `is_dir` (Testhilfe): Funktion str → bool, Standard os.path.isdir."""
    try:
        if isinstance(argv, (str, bytes)):
            return None   # einzelner String statt Liste → nicht raten
        args = [_org_shell_to_str(a) for a in (argv or [])]
        check = is_dir if is_dir is not None else os.path.isdir
        idx = -1
        value = None
        for i, a in enumerate(args):
            al = a.strip().lower()
            if al == ORG_SHELL_CLI_FLAG:
                idx = i
                if i + 1 < len(args):
                    value = args[i + 1]
                break
            if al.startswith(ORG_SHELL_CLI_FLAG + "="):
                idx = i
                value = a.strip()[len(ORG_SHELL_CLI_FLAG) + 1:]
                break
        if idx < 0 or value is None:
            return None
        rest = args[idx + 2:] if args[idx].strip().lower() == ORG_SHELL_CLI_FLAG \
            else args[idx + 1:]
        if value.strip().startswith("--"):
            return None   # nächstes Argument ist eine Option, kein Ordner
        seen = set()
        for cand in _org_cli_candidates(value, rest):
            tries = []
            # Nicht-Windows: " und Leerzeichen am Ende sind in Ordnernamen erlaubt –
            # erst den unveränderten Wert prüfen, sonst träfe es den Elternordner
            if os.name != "nt" and cand and cand.strip():
                tries.append(cand)
            tries.append(_org_cli_clean(cand))
            for p in tries:
                if not p or p in seen:
                    continue
                seen.add(p)
                try:
                    ok = bool(check(p))
                except Exception:
                    ok = False
                if ok:
                    if is_dir is None and not os.path.isabs(p):
                        p = os.path.abspath(p)
                    return Path(p)
        return None
    except Exception:
        return None


# ===========================================================================
# Windows-Standardordner (Known Folders)
# ===========================================================================
def _org_shget_known_folder(guid: str):
    """SHGetKnownFolderPath (berücksichtigt OneDrive-/GPO-Umleitung). Nur Windows."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class _OrgGUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        u = uuid.UUID(guid)
        g = _OrgGUID(u.fields[0], u.fields[1], u.fields[2],
                     (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
        # eigene DLL-Instanz: argtypes nicht global für windll.shell32 ändern
        shell32 = ctypes.WinDLL("shell32")
        ole32 = ctypes.WinDLL("ole32")
        fn = shell32.SHGetKnownFolderPath
        fn.argtypes = [ctypes.POINTER(_OrgGUID), wintypes.DWORD, wintypes.HANDLE,
                       ctypes.POINTER(ctypes.c_void_p)]
        fn.restype = ctypes.c_long
        buf = ctypes.c_void_p()
        hr = fn(ctypes.byref(g), 0, None, ctypes.byref(buf))
        try:
            if hr != 0 or not buf.value:
                return None
            return ctypes.wstring_at(buf.value)
        finally:
            if buf.value:
                ole32.CoTaskMemFree(buf)
    except Exception:
        return None


def _org_xdg_user_dir(name: str):
    """Ordner aus ~/.config/user-dirs.dirs (nur Nicht-Windows), sonst None."""
    key = _ORG_XDG_KEYS.get(name)
    if not key or os.name == "nt":
        return None
    try:
        home = Path.home()
        cfg = Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config")) / "user-dirs.dirs"
        with open(cfg, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = _ORG_RE_XDG_LINE.match(line)
                if not m or m.group(1) != key:
                    continue
                val = m.group(2).replace("${HOME}", str(home)).replace("$HOME", str(home))
                p = Path(os.path.expanduser(val))
                if not p.is_absolute():
                    p = home / p          # relativ = relativ zu HOME, nie zum cwd
                if p.is_dir() and p != home:
                    return p
    except Exception:
        return None
    return None


def org_known_folder_key(name) -> str:
    """Kanonischer Schlüssel ('documents' …) für einen DE/EN-Namen, sonst ''."""
    try:
        return ORG_KNOWN_FOLDER_ALIASES.get(str(name).strip().lower(), "")
    except Exception:
        return ""


def org_known_folder(name: str, _resolver=None) -> Path:
    """Pfad eines Windows-Standardordners: 'documents' | 'downloads' | 'pictures' |
    'desktop' | 'music' | 'videos' (auch deutsch: 'dokumente', 'bilder', 'musik' …).

    Windows: SHGetKnownFolderPath (folgt OneDrive-Umleitung). Sonst bzw. bei Fehler:
    XDG-Ordner (Linux) oder Path.home()/<englischer Name>. Unbekannter Name →
    Path.home(). `_resolver` (Testhilfe): Funktion GUID-String → Pfad-String|None."""
    try:
        home = Path.home()
    except Exception:
        home = Path(os.path.expanduser("~"))
    key = org_known_folder_key(name)
    if not key:
        return home
    guid = ORG_KNOWN_FOLDER_GUIDS[key]
    try:
        got = _resolver(guid) if _resolver is not None else _org_shget_known_folder(guid)
    except Exception:
        got = None
    got_s = _org_shell_to_str(got).replace("\x00", "").strip() if got else ""
    if got_s:
        try:
            return Path(got_s)
        except Exception:
            pass
    if _resolver is None:
        xdg = _org_xdg_user_dir(key)
        if xdg is not None:
            return xdg
    return home / ORG_KNOWN_FOLDER_FALLBACK[key]


def org_known_folders(_resolver=None) -> dict:
    """Alle Standardordner {schlüssel: Path} – z. B. für Schnellauswahl im UI."""
    return {k: org_known_folder(k, _resolver) for k in ORG_KNOWN_FOLDER_GUIDS}


# ----- org_planner -----------------------------------------------------------
# ===========================================================================
# ORDNER-ASSISTENT – Planer: verbindet alle Prüfungen zu einem Aufräum-Plan
# ===========================================================================
# org_analyze()      Ordner analysieren → OrgPlan (Vorschläge, nichts wird verändert)
# org_apply_moves()  ausgewählte Verschiebungen ausführen (mit Protokoll, nie überschreiben)
# org_undo()         Verschiebungen anhand des Protokolls rückgängig machen
# org_trash()        Aufräum-Kandidaten in den Papierkorb (Funktion wird übergeben)
# org_backup()       „Tresor“: wichtige Dateien inkrementell und geprüft sichern
#
# Alle Namen beginnen mit org_ / Org / ORG_ / _org_plan_ (Einzeldatei-Programm).
# Nur Standardbibliothek.

import hashlib
import json
import os
import re
import shutil
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------
ORG_PLAN_TEXT_BUDGET = 25.0        # s: Inhalte lesen je Analyse, danach nur Dateinamen
ORG_PLAN_META_BUDGET = 60.0        # s: EXIF/Bildgröße lesen je Analyse
ORG_PLAN_TEXT_MAX_CHARS = 4000
ORG_PLAN_CHECK_SCORE = 0.6         # ab dieser Sicherheit ist eine Verschiebung vorausgewählt
ORG_PLAN_MEDIA_CHECK_SCORE = 0.7   # Medien mit eindeutigem Muster (auch Stufe „Prüfen“)
ORG_PLAN_PROGRESS_EVERY = 250
ORG_PLAN_PROGRESS_SECONDS = 0.3    # spätestens so oft Fortschritt melden (langsame Schritte)
ORG_PLAN_HASH_CHUNK = 1024 * 1024

# Dateiarten, die NUR nach dem Dateinamen als Dokument erkannt werden (kein Textinhalt):
# fotografierte Dokumente („Fahrzeugschein.jpg“, „Kassenbon.jpg“, „Mutterpass.jpg“) und
# Archive („Bewerbungsunterlagen.zip“, „Zeugnisse.zip“). Das Lexikon ist bei Bildern
# bewusst streng (nur starke Begriffe, Feier-/Ereignisnamen ausgenommen) – daher kein
# zusätzlicher Vorfilter (ein solcher hatte z. B. Fahrzeugschein/Kassenbon übersehen).
ORG_PLAN_NAME_ONLY_KINDS = ("image", "archive")
# Diese Kategorien vergibt die Dokument-Erkennung für Bilder/Archive nie
_ORG_PLAN_NAME_ONLY_DENY = {"photo_personal", "photo_chat", "screenshot", "image",
                            "video_personal", "video", "audio", "archive", "installer",
                            "code", "cad", "science_data", "temp", "duplicate", "other",
                            "document"}
ORG_PLAN_EXIF_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp",
                      ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2"}
ORG_PLAN_SIZE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

# Rückfall-Kategorien je Dateiart: (Kategorie, Sicherheit)
ORG_PLAN_KIND_FALLBACK = {
    "archive": ("archive", 0.5), "code": ("code", 0.4), "cad": ("cad", 0.6),
    "science": ("science_data", 0.6), "audio": ("audio", 0.6), "other": ("other", 0.3),
    "installer": ("other", 0.3), "image": ("image", 0.4), "video": ("video", 0.4),
    "document": ("document", 0.3),
}

# Wird z. B. „Dokumente“ selbst aufgeräumt, entfällt der Zielordner-Anfang „Dokumente/“
ORG_PLAN_ROOT_ALIASES = {
    "dokumente": {"documents", "dokumente", "eigene dokumente", "my documents",
                  "eigene dateien"},
    "bilder": {"pictures", "bilder", "eigene bilder", "my pictures"},
    "videos": {"videos", "eigene videos", "my videos"},
    "musik": {"music", "musik", "eigene musik", "my music"},
}

# Gespeicherte Webseiten: „Seite.html“ + Ordner „Seite_files“ gehören zusammen
ORG_PLAN_WEB_EXTS = {".html", ".htm", ".xhtml", ".shtml"}
ORG_PLAN_WEB_DIR_SUFFIXES = ("_files", "-dateien", "_dateien", " dateien", "-files",
                             ".files", "_archivos", "-fichiers")
# Programmordner (portable Programme, Spiele): nichts daraus herauslösen
ORG_PLAN_PROGRAM_LIB_EXTS = {".dll", ".pak", ".asar", ".ocx", ".bpl", ".so", ".dylib"}

ORG_PLAN_STAGES = ("Dateien erfassen", "Inhalte lesen", "Einordnen", "Duplikate suchen",
                   "Fotos gruppieren", "Plan erstellen")

_ORG_PLAN_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_ORG_PLAN_RESERVED = {"con", "prn", "aux", "nul"} | {"com%d" % i for i in range(1, 10)} | \
    {"lpt%d" % i for i in range(1, 10)}
_ORG_PLAN_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_ORG_PLAN_USER_PROTECT = "_org_user_protect"
_ORG_PLAN_DUP_KEEPER = "_org_dup_keeper"
_ORG_PLAN_LINKED = "_org_plan_linked"
_ORG_PLAN_JOURNAL_TAIL = b"]}\n"
# Änderungszeit-Toleranz (s) beim Prüfen „seit der Analyse verändert?“ (FAT/Netzlaufwerke)
ORG_PLAN_MTIME_TOLERANCE = 1.0
# Windows-Reparse-Punkte, die auf einen anderen Ort zeigen (Junction, Symlink).
# Cloud-Platzhalter (OneDrive) haben andere Tags und bleiben normal bearbeitbar.
_ORG_PLAN_ATTR_REPARSE = 0x400
_ORG_PLAN_LINK_TAGS = (0xA0000003, 0xA000000C)   # IO_REPARSE_TAG_MOUNT_POINT / _SYMLINK


# ---------------------------------------------------------------------------
# Datenmodell
# ---------------------------------------------------------------------------
@dataclass
class OrgMove:
    file: OrgFile
    dest_rel: str          # Zielordner relativ zum Wurzelordner, mit "/"
    checked: bool = False  # in der Oberfläche vorausgewählt


@dataclass
class OrgPlan:
    root: Path
    files: list = field(default_factory=list)
    projects: list = field(default_factory=list)
    truncated: bool = False
    empty_dirs: list = field(default_factory=list)
    moves: list = field(default_factory=list)
    junk: list = field(default_factory=list)
    protect: list = field(default_factory=list)
    review: list = field(default_factory=list)
    versions: dict = field(default_factory=dict)
    dup_groups: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)


class _org_PlanCancelled(Exception):
    pass


# ---------------------------------------------------------------------------
# Kleine Hilfen
# ---------------------------------------------------------------------------
def _org_plan_call_progress(progress, stage: str, done: int, total: int) -> None:
    if progress is None:
        return
    try:
        progress(stage, int(done), int(total))
    except Exception:
        pass


def _org_plan_due(tick: list) -> bool:
    """True, wenn seit der letzten Meldung ORG_PLAN_PROGRESS_SECONDS vergangen sind
    (tick = [letzter Zeitpunkt], wird fortgeschrieben)."""
    now = time.monotonic()
    if now - tick[0] >= ORG_PLAN_PROGRESS_SECONDS:
        tick[0] = now
        return True
    return False


def _org_plan_walk_cancelled(progress, cancelled):
    """Abbruch-Prüfung für org_walk (wird je Ordner aufgerufen) – meldet nebenbei
    „Dateien erfassen“ mit der Zahl durchsuchter Ordner (gesamt 0 = unbestimmt),
    damit die Oberfläche bei großen/langsamen Laufwerken nicht stehen bleibt."""
    state = {"dirs": 0, "tick": [time.monotonic()]}

    def _check():
        state["dirs"] += 1
        if _org_plan_due(state["tick"]):
            _org_plan_call_progress(progress, "Dateien erfassen", state["dirs"], 0)
        return _org_plan_is_cancelled(cancelled)
    return _check


def _org_plan_is_cancelled(cancelled) -> bool:
    if cancelled is None:
        return False
    try:
        return bool(cancelled())
    except Exception:
        return False


def _org_plan_check(cancelled) -> None:
    if _org_plan_is_cancelled(cancelled):
        raise _org_PlanCancelled()


def _org_plan_key(p) -> str:
    """Vergleichsschlüssel für Pfade (Groß-/Kleinschreibung wie das Betriebssystem)."""
    s = unicodedata.normalize("NFC", os.fspath(p))
    return os.path.normcase(os.path.normpath(s))


def _org_plan_inside(root_key: str, path_key: str) -> bool:
    if path_key == root_key:
        return True
    base = root_key if root_key.endswith(os.sep) else root_key + os.sep
    return path_key.startswith(base)


def _org_plan_fold(s: str) -> str:
    return unicodedata.normalize("NFC", s).casefold()


def _org_plan_tier_of(cat: str) -> str:
    return ORG_CATEGORIES.get(cat, {}).get("tier", ORG_REVIEW)


def _org_plan_user_protected(f) -> bool:
    try:
        return bool(f.meta.get(_ORG_PLAN_USER_PROTECT))
    except Exception:
        return False


def _org_plan_sanitize_part(part: str) -> str:
    s = _ORG_PLAN_BAD_CHARS.sub("_", part).strip().rstrip(". ")
    if not s:
        return ""
    if s.split(".")[0].lower() in _ORG_PLAN_RESERVED:
        s = "_" + s
    return s[:120].rstrip(". ")


def _org_plan_split_name(name: str):
    stem, ext = os.path.splitext(name)
    if not stem:                       # ".bashrc"
        return name, ""
    return stem, ext


def _org_plan_free_name(directory: Path, name: str, tag: str = "") -> Path:
    """Kollisionsfreier Name: "x.pdf", "x (2).pdf" … bzw. mit Zusatz
    "x (wiederhergestellt).pdf", "x (wiederhergestellt 2).pdf" …"""
    stem, ext = _org_plan_split_name(name)
    for k in range(1, 100000):
        if tag:
            cand = "%s (%s)%s" % (stem, tag, ext) if k == 1 else \
                "%s (%s %d)%s" % (stem, tag, k, ext)
        else:
            cand = name if k == 1 else "%s (%d)%s" % (stem, k, ext)
        p = directory / cand
        if not os.path.lexists(str(p)):
            return p
    raise OSError("Kein freier Dateiname für %s" % name)


def _org_plan_rename_noreplace(src, dst) -> None:
    """Verschiebt src → dst, überschreibt NIE (FileExistsError, wenn dst existiert)."""
    src, dst = os.fspath(src), os.fspath(dst)
    if os.name == "nt":
        os.rename(src, dst)            # Windows: wirft FileExistsError, wenn dst existiert
        return
    try:
        os.link(src, dst)              # atomar: scheitert, wenn dst existiert
    except FileExistsError:
        raise
    except OSError:
        # Dateisystem ohne harte Links (FAT, manche Netzlaufwerke)
        if os.path.lexists(dst):
            raise FileExistsError(dst)
        os.rename(src, dst)
        return
    try:
        os.unlink(src)
    except OSError:
        try:
            os.unlink(dst)             # nur den eben angelegten zweiten Namen entfernen
        except OSError:
            pass
        raise


def _org_plan_hash_file(path, cancelled=None):
    """(blake2b-Hex, Größe) oder (None, -1)."""
    h = hashlib.blake2b(digest_size=32)
    size = 0
    try:
        with open(os.fspath(path), "rb") as fh:
            while True:
                chunk = fh.read(ORG_PLAN_HASH_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
                size += len(chunk)
                if cancelled is not None and size % (64 * ORG_PLAN_HASH_CHUNK) == 0 \
                        and _org_plan_is_cancelled(cancelled):
                    return None, -1
    except (OSError, ValueError, TypeError):
        return None, -1
    return h.hexdigest(), size


def _org_plan_unchanged(f, st) -> bool:
    """Größe und Änderungszeit wie bei der Analyse? (unbekannt → False = nicht anfassen)"""
    try:
        if int(st.st_size) != int(f.size):
            return False
        m = float(f.mtime)
        if m != m or m in (float("inf"), float("-inf")):
            return False
        return abs(float(st.st_mtime) - m) <= ORG_PLAN_MTIME_TOLERANCE
    except Exception:
        return False


def _org_plan_real_key(p) -> str:
    """Schlüssel des echten Orts: Ordner aufgelöst (Links/Junctions), Name unverändert."""
    s = os.fspath(p)
    parent, name = os.path.split(os.path.abspath(s))
    return _org_plan_key(os.path.join(os.path.realpath(parent), name))


def _org_plan_is_link_dir(path, lstat=None) -> bool:
    """Ordner ist ein Symlink oder eine Windows-Junction (zeigt evtl. aus dem Ordner
    hinaus). OneDrive-Platzhalter (Cloud-Reparse-Tags) zählen nicht."""
    try:
        if lstat is None and os.path.islink(str(path)):
            return True
        st = (lstat or os.lstat)(str(path))
    except (OSError, ValueError, TypeError):
        return False
    try:
        import stat as _st
        if _st.S_ISLNK(int(getattr(st, "st_mode", 0) or 0)):
            return True
    except Exception:
        pass
    attrs = int(getattr(st, "st_file_attributes", 0) or 0)
    if attrs & _ORG_PLAN_ATTR_REPARSE:
        tag = int(getattr(st, "st_reparse_tag", 0) or 0)
        return tag in _ORG_PLAN_LINK_TAGS
    return False


def _org_plan_link_dirs(root: Path, files) -> list:
    """Unterordner (rel), die Verknüpfungen sind; tiefere Ebenen darunter nicht extra."""
    dirs = set()
    for f in files:
        try:
            parts = f.rel.split("/")[:-1]
        except Exception:
            continue
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    found = []
    for d in sorted(dirs, key=lambda x: (x.count("/"), x)):
        if any(d.startswith(x + "/") for x in found):
            continue
        if _org_plan_is_link_dir(root.joinpath(*d.split("/"))):
            found.append(d)
    return found


def _org_plan_root_of(f):
    """Wurzelordner einer Datei aus path und rel ableiten (oder None)."""
    try:
        n = len([p for p in str(f.rel).replace("\\", "/").split("/") if p])
        if n <= 0:
            return None
        return Path(f.path).parents[n - 1]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Analyse
# ---------------------------------------------------------------------------
def _org_plan_root_is_project(root: Path) -> bool:
    """Wurzel selbst oder ein Elternordner (bis ausschließlich Benutzerordner/Laufwerk)
    ist ein Projekt → nichts verändern."""
    if org_is_project_dir(root):
        return True
    try:
        home_key = _org_plan_key(Path.home().resolve())
    except Exception:
        home_key = ""
    try:
        parents = list(root.parents)
    except Exception:
        return False
    for p in parents[:-1][:8]:         # ohne Laufwerkswurzel
        if _org_plan_key(p) == home_key:
            break
        try:
            names = {n.lower() for n in os.listdir(str(p))}
        except OSError:
            continue
        if names & {".git", ".hg", ".svn"}:
            return True
    return False


def _org_plan_project_of(rel: str, proj_set: set) -> str:
    parts = rel.split("/")[:-1]
    for i in range(len(parts), 0, -1):
        cand = "/".join(parts[:i])
        if cand in proj_set:
            return cand
    return ""


def _org_plan_protect_keys(protected_paths) -> set:
    keys = set()
    try:
        items = list(protected_paths or ())
    except Exception:
        return keys
    for p in items:
        try:
            keys.add(_org_plan_key(os.path.realpath(os.fspath(p))))
        except Exception:
            continue
    return keys


def _org_plan_key_protected(k: str, keys: set, root_key: str) -> bool:
    """Pfad-Schlüssel k oder ein Elternordner ist geschützt? Ab root wird ohne Abbruch
    bis zur Laufwerkswurzel weitergeprüft (geschützter Ordner oberhalb von root)."""
    while True:
        if k in keys:
            return True
        parent = os.path.dirname(k)
        if parent == k:
            return False
        k = parent


def _org_plan_apply_user_protect(files, protected_paths, root_key: str) -> int:
    """Vom Nutzer geschützte Pfade (Dateien oder Ordner) markieren."""
    keys = _org_plan_protect_keys(protected_paths)
    if not keys:
        return 0
    # root selbst oder ein Elternordner geschützt → alles geschützt (einmal prüfen)
    all_hit = _org_plan_key_protected(root_key, keys, root_key)
    inner = set(k for k in keys if k != root_key and _org_plan_inside(root_key, k))
    n = 0
    for f in files:
        try:
            hit = all_hit
            if not hit and inner:
                k = _org_plan_key(f.path)
                while k != root_key:
                    if k in inner:
                        hit = True
                        break
                    parent = os.path.dirname(k)
                    if parent == k:
                        break
                    k = parent
            if hit:
                f.meta[_ORG_PLAN_USER_PROTECT] = True
                n += 1
        except Exception:
            continue
    return n


def _org_plan_enforce_protect(files) -> None:
    """Sicherheitsregel: Geschütztes landet nie im Aufräumen-Vorschlag.
    Ausnahme: exaktes Duplikat (dup_of gesetzt, Original bleibt erhalten)."""
    for f in files:
        try:
            if f.in_project:
                continue
            if _org_plan_user_protected(f):
                if f.tier == ORG_JUNK or f.dup_of:
                    if f.dup_of:
                        f.add_reason("Inhaltsgleich mit %s" % f.dup_of)
                        orig = f.meta.get("orig_category") or ""
                        f.category = orig if orig and orig != "duplicate" else f.category
                        if f.category == "duplicate":
                            f.category = ""
                        f.dup_of = ""
                        f.meta.pop(_ORG_PLAN_DUP_KEEPER, None)
                f.tier = ORG_PROTECT
                f.add_reason("Vom Nutzer geschützt")
                continue
            if f.tier == ORG_JUNK and not f.dup_of:
                was_protected = (_org_plan_tier_of(f.category) == ORG_PROTECT
                                 and f.category not in ("temp", "duplicate")) or \
                    bool(f.meta.get("_org_ph_protect")) or \
                    f.meta.get("orig_tier") == ORG_PROTECT
                if was_protected:
                    f.tier = ORG_REVIEW
                    f.add_reason("Sicherheitsprüfung: wichtige Datei – nicht zum Löschen "
                                 "vorgeschlagen")
            if f.tier == ORG_JUNK and f.dup_of and f.category != "duplicate":
                # Duplikat-Kennzeichen ohne Duplikat-Kategorie: lieber prüfen lassen
                f.tier = ORG_REVIEW
        except Exception:
            continue


def _org_plan_read_contents(plan, work, extract_text, progress, cancelled) -> None:
    stage = "Inhalte lesen"
    texts = []
    metas = []
    for f in work:
        if f.tier == ORG_JUNK:
            continue
        if f.kind == "document" and extract_text:
            texts.append(f)
        elif f.kind == "image" and (f.ext in ORG_PLAN_EXIF_EXTS or f.ext in ORG_PLAN_SIZE_EXTS):
            metas.append(f)
    # oberste Ebene zuerst (wird verschoben), kleine Dateien vor großen
    texts.sort(key=lambda x: (x.depth, x.size))
    total = len(texts) + len(metas)
    done = 0
    tick = [time.monotonic()]       # langsame PDFs: Fortschritt auch zeitgesteuert melden
    _org_plan_call_progress(progress, stage, 0, total)
    read = skipped = 0
    deadline = time.monotonic() + ORG_PLAN_TEXT_BUDGET
    for f in texts:
        _org_plan_check(cancelled)
        if time.monotonic() > deadline or getattr(f, "cloud", False):
            skipped += 1                    # Zeitbudget erschöpft / nur online → nur Dateiname
            f.meta["text_skipped"] = True
        else:
            try:
                f.text = org_extract_text(f.path, ORG_PLAN_TEXT_MAX_CHARS) or ""
            except Exception:
                f.text = ""
            read += 1
        done += 1
        if done % ORG_PLAN_PROGRESS_EVERY == 0 or _org_plan_due(tick):
            _org_plan_call_progress(progress, stage, done, total)
    meta_read = meta_skipped = 0
    deadline = time.monotonic() + ORG_PLAN_META_BUDGET
    for f in metas:
        _org_plan_check(cancelled)
        if time.monotonic() > deadline or getattr(f, "cloud", False):
            meta_skipped += 1
        else:
            meta_read += 1
            exif = {}
            if f.ext in ORG_PLAN_EXIF_EXTS:
                try:
                    exif = org_read_exif(f.path) or {}
                except Exception:
                    exif = {}
                if exif:
                    f.meta["exif"] = exif
            if f.ext in ORG_PLAN_SIZE_EXTS and not (exif.get("width") and exif.get("height")):
                try:
                    sz = org_image_size(f.path)
                except Exception:
                    sz = None
                if sz:
                    f.meta["size"] = sz
        done += 1
        if done % ORG_PLAN_PROGRESS_EVERY == 0 or _org_plan_due(tick):
            _org_plan_call_progress(progress, stage, done, total)
    _org_plan_call_progress(progress, stage, total, total)
    plan.stats.update({"text_read": read, "text_skipped": skipped,
                       "meta_read": meta_read, "meta_skipped": meta_skipped})


def _org_plan_reason_text(label: str, name: str, terms: list, has_text: bool) -> str:
    """„Erkannt: Lebenslauf & Bewerbung (Dateiname: lebenslauf; Inhalt: berufserfahrung)“."""
    in_name, in_text = [], []
    try:
        compact = org_normalize(name).replace(" ", "")
        for t in terms or ():
            tn = org_normalize(str(t)).replace(" ", "")
            if tn and tn in compact:
                in_name.append(str(t))
            else:
                in_text.append(str(t))
    except Exception:
        in_name = [str(t) for t in (terms or [])]
    if not has_text:
        in_name, in_text = in_name + in_text, []
    parts = []
    if in_name or not in_text:
        parts.append("Dateiname" + (": " + ", ".join(in_name[:2]) if in_name else ""))
    if in_text:
        parts.append("Inhalt: " + ", ".join(in_text[:2]))
    return "Erkannt: %s (%s)" % (label, "; ".join(parts))


def _org_plan_classify(work, progress, cancelled) -> None:
    stage = "Einordnen"
    total = len(work)
    _org_plan_call_progress(progress, stage, 0, total)
    # 1) Dokumente, dazu Bilder und Archive mit Dokument-Namen („Personalausweis.jpg“,
    #    „Zeugnisse.zip“) – vor den Fotos, damit „Screenshot Zeugnis.png“ ein Zeugnis bleibt.
    for i, f in enumerate(work):
        if i % ORG_PLAN_PROGRESS_EVERY == 0:
            _org_plan_check(cancelled)
            _org_plan_call_progress(progress, stage, i, total * 2)
        try:
            if f.tier == ORG_JUNK:
                continue
            if f.kind == "document":
                pass
            elif f.kind in ORG_PLAN_NAME_ONLY_KINDS:
                if f.category:
                    continue
            else:
                continue
            pre_tier = f.tier
            cat, score, terms, year = org_classify_document(
                f.name, f.text if f.kind == "document" else "", f.kind)
            if f.kind != "document" and cat in _ORG_PLAN_NAME_ONLY_DENY:
                cat = None
            if cat is None or cat not in ORG_CATEGORIES:
                if f.kind != "document":
                    continue            # Bild → Fotoprüfung, Archiv → Rückfall „Archive“
                f.category = "document"
                f.score = 0.3
                f.add_reason("Dokument ohne eindeutige Zuordnung")
                if year:
                    f.year = year
            else:
                f.category = cat
                f.score = float(score or 0.0)
                if year:
                    f.year = year
                label = ORG_CATEGORIES[cat].get("label", cat)
                f.add_reason(_org_plan_reason_text(label, f.name, terms, bool(f.text)))
            f.tier = _org_plan_tier_of(f.category)
            if pre_tier == ORG_REVIEW:
                # Hinweis einer Vorprüfung bleibt bestehen – auch gegen „Wichtig“: bei
                # Dokumenten kommt er nur von Sperrdatei-Namen („~$Lebenslauf.docx“, zu groß
                # für eine echte Sperrdatei); so etwas ist kein geschützter Lebenslauf.
                f.tier = ORG_REVIEW
        except Exception:
            continue
    # 2) Fotos & Videos
    _org_plan_check(cancelled)
    try:
        org_classify_media(work)
    except Exception:
        pass
    # 3) Rückfall nach Dateiart
    for i, f in enumerate(work):
        if i % ORG_PLAN_PROGRESS_EVERY == 0:
            _org_plan_check(cancelled)
            _org_plan_call_progress(progress, stage, total + i, total * 2)
        try:
            if f.tier == ORG_JUNK or f.category:
                if not f.tier and f.category:
                    f.tier = _org_plan_tier_of(f.category)
                if f.category == "installer" and not f.score:
                    f.score = 0.5
                continue
            cat, score = ORG_PLAN_KIND_FALLBACK.get(f.kind, ("other", 0.3))
            f.category = cat
            f.score = max(float(f.score or 0.0), score)
            if not f.tier:
                f.tier = _org_plan_tier_of(cat)
            if f.kind == "installer" and not f.reasons:
                f.add_reason("Programmdatei")
        except Exception:
            continue
    _org_plan_call_progress(progress, stage, total * 2, total * 2)


def _org_plan_exists(f) -> bool:
    try:
        return os.path.isfile(str(f.path))
    except Exception:
        return False


def _org_plan_is_protected(f) -> bool:
    return f.tier == ORG_PROTECT or _org_plan_user_protected(f)


def _org_plan_keeper_rank(f) -> int:
    """Vorrang beim Behalten exakter Kopien (kleiner = besser):
    0 geschütztes Dokument (Ausweis, Zeugnis …) / vom Nutzer geschützt,
    1 geschütztes Foto/Video, 2 „Behalten“, 3 „Prüfen“, 4 Rest.
    So bleibt „Personalausweis.jpg“ statt „IMG_4567.JPG“ und „Rechnung_Amazon.pdf“
    statt „download.pdf“ – der aussagekräftige Name samt passendem Zielordner."""
    try:
        if _org_plan_user_protected(f):
            return 0
        if f.tier == ORG_PROTECT:
            return 1 if f.category in ORG_MEDIA_CATEGORIES else 0
        if f.tier == ORG_KEEP:
            return 2
        if f.tier == ORG_REVIEW:
            return 3
    except Exception:
        pass
    return 4


def _org_plan_mark_duplicates(groups) -> list:
    """Wie org_mark_duplicates, aber: enthält eine Gruppe geschützte Dateien (Lebenslauf,
    Fotos …), wird eine davon behalten (Dokument vor Foto, dann „Behalten“ vor „Prüfen“,
    siehe _org_plan_keeper_rank). Liefert markierte Gruppen [Original, Kopien…]."""
    out = []
    for g in groups or ():
        try:
            members = [m for m in g if m is not None]
            if len(members) < 2:
                continue
            best = min(_org_plan_keeper_rank(m) for m in members)
            pool = [m for m in members if _org_plan_keeper_rank(m) == best] or members
            keeper = org_pick_keeper(pool)
            if keeper is None:
                continue
            if not _org_plan_exists(keeper):
                existing = [m for m in pool if _org_plan_exists(m)] or \
                    [m for m in members if _org_plan_exists(m)]
                if not existing:
                    continue
                keeper = org_pick_keeper(existing)
                if keeper is None:
                    continue
            k_key = _org_plan_key(keeper.path)
            k_hash = str((keeper.meta or {}).get("hash") or "")
            marked = []
            for f in members:
                try:
                    if f is keeper or f.in_project or _org_plan_key(f.path) == k_key:
                        continue
                    if f.size != keeper.size:
                        continue
                    h = str((f.meta or {}).get("hash") or "")
                    if h and k_hash and h != k_hash:
                        continue
                    if _org_plan_user_protected(f):
                        f.add_reason("Inhaltsgleich mit %s" % keeper.rel)
                        continue
                    f.meta["orig_category"] = f.category
                    f.meta["orig_tier"] = f.tier
                    f.meta[_ORG_PLAN_DUP_KEEPER] = keeper
                    f.add_reason("Exakte Kopie von %s" % keeper.rel)
                    f.dup_of = keeper.rel
                    f.category = "duplicate"
                    f.tier = ORG_JUNK
                    f.score = 1.0
                    f.version_group = ""
                    if keeper.in_project:
                        # Original liegt nur in einem Projekt-/verknüpften Ordner: solche
                        # Dateien entstehen/verschwinden mit dem Projekt (Build, „git clean“)
                        # → lose Kopie nicht zum Löschen vorschlagen, nur prüfen lassen.
                        f.tier = ORG_REVIEW
                        f.score = 0.5
                        f.add_reason("Original liegt in einem Projektordner – Kopie nicht "
                                     "automatisch löschen")
                    marked.append(f)
                except Exception:
                    continue
            if marked:
                keeper.add_reason("Original (%d Kopie%s gefunden)"
                                  % (len(marked), "" if len(marked) == 1 else "n"))
                out.append([keeper] + marked)
        except Exception:
            continue
    return out


def _org_plan_event_part(f) -> str:
    ev = (f.event or "").strip()
    if ev:
        return ev
    try:
        dt = org_photo_dt(f)
        if dt is not None:
            return "%04d-%02d %s" % (dt.year, dt.month, org_month_name(dt.month))
    except Exception:
        pass
    return "Ohne Datum"


def _org_plan_root_alias(root: Path) -> str:
    try:
        n = _org_plan_fold(root.name)
    except Exception:
        return ""
    for key, names in ORG_PLAN_ROOT_ALIASES.items():
        if n in names:
            return key
    return ""


def _org_plan_folder_for(f, alias: str = "") -> str:
    """Zielordner (relativ, "/"), Platzhalter aufgelöst; "" = bleibt."""
    cat = f.category
    if cat == "duplicate":
        cat = f.meta.get("orig_category") or ""
    folder = ORG_CATEGORIES.get(cat, {}).get("folder", "") if cat else ""
    if not folder:
        return ""
    if "{year}" in folder:
        try:
            year = str(int(org_year_of(f)))
        except Exception:
            year = "Ohne Jahr"
        folder = folder.replace("{year}", year)
    if "{event}" in folder:
        folder = folder.replace("{event}", _org_plan_event_part(f).replace("/", "-"))
    parts = [_org_plan_sanitize_part(p) for p in folder.split("/")]
    parts = [p for p in parts if p]
    if alias and parts and parts[0].casefold() == alias:
        parts = parts[1:]
    return "/".join(parts)


def _org_plan_static_prefix(f, alias: str = "") -> str:
    """Fester Anfang des Zielordners vor dem ersten Platzhalter, z. B. „Bilder/Fotos“
    für „Bilder/Fotos/{year}/{event}“ oder „Dokumente/Steuer“; "" ohne Platzhalter."""
    cat = f.category
    if cat == "duplicate":
        cat = f.meta.get("orig_category") or ""
    folder = ORG_CATEGORIES.get(cat, {}).get("folder", "") if cat else ""
    if "{" not in folder:
        return ""
    head = folder.split("{", 1)[0]
    parts = [_org_plan_sanitize_part(p) for p in head.split("/")]
    parts = [p for p in parts if p]
    if alias and parts and parts[0].casefold() == alias:
        parts = parts[1:]
    return "/".join(parts)


def _org_plan_dir_set(files) -> set:
    dirs = set()
    for f in files:
        parts = f.rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            d = "/".join(parts[:i])
            if d in dirs:
                # Eltern schon erfasst – trotzdem tiefere Ebenen ergänzen
                continue
            dirs.add(d)
    return {_org_plan_fold(d) for d in dirs}


def _org_plan_web_dirs(f, dirs: set) -> list:
    """Zu einer gespeicherten Webseite gehörende Ordner ("Seite_files"), gefaltet."""
    if f.ext not in ORG_PLAN_WEB_EXTS:
        return []
    parent, _, name = f.rel.rpartition("/")
    stem = _org_plan_split_name(name)[0]
    prefix = (parent + "/") if parent else ""
    out = []
    for suf in ORG_PLAN_WEB_DIR_SUFFIXES:
        d = _org_plan_fold(prefix + stem + suf)
        if d in dirs:
            out.append(d)
    return out


def _org_plan_program_dirs(files) -> list:
    """Ordner (rel), die wie ein Programm aussehen (.exe + Bibliotheken) → nicht zerlegen."""
    lib_dirs = set()
    exe_dirs = set()
    for f in files:
        parent = f.rel.rpartition("/")[0]
        if not parent:
            continue
        if f.ext in ORG_PLAN_PROGRAM_LIB_EXTS:
            lib_dirs.add(parent)
        elif f.ext == ".exe":
            exe_dirs.add(parent)
    pinned = set()
    for d in lib_dirs:
        top = d
        parts = d.split("/")
        # Starter eine oder zwei Ebenen höher (z. B. Spiel/bin/x64/*.dll, Spiel/Spiel.exe)
        for up in range(0, min(3, len(parts))):
            cand = "/".join(parts[:len(parts) - up])
            if cand in exe_dirs:
                top = cand
        if d in exe_dirs or top != d:
            pinned.add(_org_plan_fold(top))
    return sorted(pinned)


def _org_plan_under(rel_fold: str, prefixes) -> bool:
    for p in prefixes:
        if rel_fold.startswith(p + "/"):
            return True
    return False


def _org_plan_checked(f) -> bool:
    try:
        score = float(f.score or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if f.tier in (ORG_PROTECT, ORG_KEEP) and score >= ORG_PLAN_CHECK_SCORE:
        return True
    if f.category in ORG_MEDIA_CATEGORIES and f.tier != ORG_JUNK and \
            score >= ORG_PLAN_MEDIA_CHECK_SCORE:
        return True
    return False


def _org_plan_build_moves(plan, reorganize_subfolders, progress, cancelled) -> list:
    stage = "Plan erstellen"
    files = plan.files
    total = len(files)
    _org_plan_call_progress(progress, stage, 0, total)
    alias = _org_plan_root_alias(plan.root)
    dirs = _org_plan_dir_set(files)
    prog_dirs = _org_plan_program_dirs(files) if reorganize_subfolders else []
    web_dirs = {}
    for f in files:
        for d in _org_plan_web_dirs(f, dirs):
            web_dirs[d] = f.rel
    if reorganize_subfolders and web_dirs:
        prog_dirs = list(prog_dirs) + sorted(web_dirs)
    # Projekt- und verknüpfte Ordner sind tabu – auch als Ziel (z. B. „Projekte“ = Git-Repo)
    taboo = {_org_plan_fold(p) for p in (plan.projects or []) if p and p != "."}
    taboo |= {_org_plan_fold(d) for d in plan.stats.get("link_dirs", []) or []}
    moves = []
    by_file = {}
    for i, f in enumerate(files):
        if i % ORG_PLAN_PROGRESS_EVERY == 0:
            _org_plan_check(cancelled)
            _org_plan_call_progress(progress, stage, i, total)
        try:
            f.target = ""
            if f.in_project or f.tier == ORG_JUNK or f.dup_of:
                continue
            if f.depth != 0 and not reorganize_subfolders:
                continue
            if f.version_group and not f.version_latest:
                continue                    # folgt unten der neuesten Version
            parent = f.rel.rpartition("/")[0]
            if prog_dirs and _org_plan_under(_org_plan_fold(f.rel), prog_dirs):
                f.add_reason("Gehört zu einem Programm- oder Webseitenordner – bleibt, "
                             "wo es ist")
                continue
            if _org_plan_web_dirs(f, dirs):
                f.add_reason("Gespeicherte Webseite – bleibt bei ihrem Ordner")
                continue
            dest = _org_plan_folder_for(f, alias)
            if not dest:
                continue
            pf, df = _org_plan_fold(parent), _org_plan_fold(dest)
            if pf == df or pf.startswith(df + "/"):
                continue                    # liegt schon richtig
            # Liegt schon im richtigen Bereich mit eigener Unterteilung („Fotos/2023/
            # Italien“, „Steuer/Alt“): eigene Ordnernamen des Nutzers nicht zerlegen und
            # bei erneuter Analyse nicht hin- und herschieben (Ereignisse/Jahre ändern sich).
            sp = _org_plan_fold(_org_plan_static_prefix(f, alias))
            if sp and pf.startswith(sp + "/"):
                continue
            if taboo and (df in taboo or _org_plan_under(df, taboo)):
                f.add_reason("Zielordner „%s“ ist ein Projektordner – bleibt, wo es ist" % dest)
                continue
            mv = OrgMove(file=f, dest_rel=dest, checked=_org_plan_checked(f))
            f.target = dest
            moves.append(mv)
            by_file[id(f)] = mv
        except Exception:
            continue
    # ältere Versionen ziehen mit der neuesten Version um (bleiben nebeneinander)
    for key, members in (plan.versions or {}).items():
        try:
            latest = [m for m in members if m.version_latest]
            lead = by_file.get(id(latest[0])) if latest else None
            if lead is None:
                continue
            for m in members:
                if m is latest[0] or m.version_latest or m.in_project or \
                        m.tier == ORG_JUNK or m.dup_of or id(m) in by_file:
                    continue
                mv = OrgMove(file=m, dest_rel=lead.dest_rel, checked=lead.checked)
                m.target = lead.dest_rel
                moves.append(mv)
                by_file[id(m)] = mv
        except Exception:
            continue
    moves.sort(key=lambda mv: (mv.dest_rel.casefold(), mv.file.rel.casefold()))
    _org_plan_call_progress(progress, stage, total, total)
    return moves


def _org_plan_finish_lists(plan) -> None:
    files = plan.files
    plan.junk = [f for f in files if f.tier == ORG_JUNK and not f.in_project]
    plan.protect = [f for f in files if f.tier == ORG_PROTECT and not f.in_project]
    plan.review = [f for f in files if f.tier == ORG_REVIEW and not f.in_project]


def _org_plan_stats(plan, t0: float) -> None:
    files = plan.files
    st = plan.stats

    def _sz(f):
        try:
            return max(0, int(f.size))
        except Exception:
            return 0

    dups = [f for f in plan.junk if f.dup_of]
    st.update({
        "files": len(files),
        "bytes": sum(_sz(f) for f in files),
        "junk_bytes": sum(_sz(f) for f in plan.junk),
        "dup_bytes": sum(_sz(f) for f in dups),
        "junk_count": len(plan.junk),
        "dup_count": len(dups),
        "protect_count": len(plan.protect),
        "move_count": len(plan.moves),
        "review_count": len(plan.review),
        "version_count": sum(1 for g in plan.versions.values() for f in g
                             if not f.version_latest),
        "project_count": len(plan.projects),
        "empty_dir_count": len(plan.empty_dirs),
        "truncated": bool(plan.truncated),
    })
    st["seconds"] = round(time.monotonic() - t0, 3)


def org_analyze(root: Path, recursive: bool = True, reorganize_subfolders: bool = False,
                programs=None, progress=None, cancelled=lambda: False, now=None,
                extract_text: bool = True, protected_paths=None) -> OrgPlan:
    """Analysiert root und liefert einen Plan. Verändert keine Dateien.

    Reihenfolge / Vorrang:
     1. Erfassen (org_walk). Dateien in Projektordnern: nur gemeldet, Kategorie "code",
        Stufe „Behalten“, nie verschoben/gelöscht/versioniert. Ist die Wurzel selbst
        (oder ein Elternordner) ein Projekt, gilt das für alle Dateien.
     2. Reste (org_detect_junk) und Installer (org_mark_installers) – VOR der Einordnung,
        damit „Lebenslauf.pdf.crdownload“ oder eine leere IMG_1.JPG nicht geschützt werden.
     3. Inhalte lesen: Text der Dokumente (Zeitbudget ORG_PLAN_TEXT_BUDGET, danach nur
        Dateinamen), EXIF und Bildgröße der Bilder (Budget ORG_PLAN_META_BUDGET).
     4. Einordnen: Dokumente und Bilder mit Dokument-Namen (org_classify_document),
        danach Fotos/Videos (org_classify_media), sonst Rückfall nach Dateiart.
     5. Exakte Duplikate – geschützte Exemplare werden bevorzugt behalten.
     6. Versionen (nur Nicht-Müll), ältere → „Prüfen“.
     7. Fotos zu Ereignissen gruppieren.
     8. Schutzregel: Geschütztes nie im Aufräumen-Vorschlag (außer exakte Kopie).
     9. Zielordner/Verschiebungen (nur oberste Ebene, außer reorganize_subfolders).
    10. Leere Ordner, Statistik.
    progress(stufe, erledigt, gesamt); cancelled() wird regelmäßig geprüft."""
    t0 = time.monotonic()
    try:
        root_p = Path(root)
    except Exception:
        root_p = Path(".")
    plan = OrgPlan(root=root_p)
    try:
        _org_plan_run(plan, recursive, reorganize_subfolders, programs, progress,
                      cancelled, now, extract_text, protected_paths)
    except _org_PlanCancelled:
        plan.stats["cancelled"] = True
        plan.moves, plan.junk, plan.dup_groups, plan.versions = [], [], [], {}
        plan.empty_dirs = []
        for f in plan.files:
            f.target = ""
    except Exception as e:                      # nie abstürzen: nur Bericht, keine Aktionen
        plan.stats["error"] = "Analyse unvollständig: %s" % (e,)
        plan.moves, plan.junk, plan.dup_groups, plan.empty_dirs = [], [], [], []
        for f in plan.files:
            f.target = ""
    try:
        if "cancelled" in plan.stats or "error" in plan.stats:
            plan.protect = [f for f in plan.files if f.tier == ORG_PROTECT]
            plan.review = [f for f in plan.files if f.tier == ORG_REVIEW]
        _org_plan_stats(plan, t0)
    except Exception:
        plan.stats["seconds"] = round(time.monotonic() - t0, 3)
    return plan


def _org_plan_run(plan, recursive, reorganize_subfolders, programs, progress, cancelled,
                  now, extract_text, protected_paths) -> None:
    if now is None:
        now = time.time()
    # ---- 1. Erfassen ----
    _org_plan_call_progress(progress, "Dateien erfassen", 0, 0)
    root = plan.root
    if not root.is_dir():
        plan.stats["error"] = "Ordner nicht gefunden: %s" % root
        return
    root = root.resolve()
    plan.root = root
    files, projects, truncated = org_walk(root, bool(recursive),
                                          cancelled=_org_plan_walk_cancelled(progress,
                                                                             cancelled))
    _org_plan_check(cancelled)
    plan.files, plan.projects, plan.truncated = files, list(projects), bool(truncated)
    _org_plan_call_progress(progress, "Dateien erfassen", len(files), len(files))
    if _org_plan_root_is_project(root):
        plan.stats["root_is_project"] = True
        plan.projects.insert(0, ".")
        for f in files:
            f.in_project = True
    # Verknüpfte Unterordner (Windows-Junctions/Symlinks, die org_walk betritt): deren
    # Inhalt liegt evtl. ganz woanders → wie Projektdateien nie verschieben/löschen.
    link_dirs = []
    if not plan.stats.get("root_is_project"):
        link_dirs = _org_plan_link_dirs(root, files)
    plan.stats["link_dirs"] = list(link_dirs)
    if link_dirs:
        link_pre = [_org_plan_fold(d) for d in link_dirs]
        for f in files:
            if not f.in_project and _org_plan_under(_org_plan_fold(f.rel), link_pre):
                f.in_project = True
                f.meta[_ORG_PLAN_LINKED] = True
    proj_set = set(plan.projects)
    for f in files:
        if f.in_project:
            f.category = "code"
            f.tier = ORG_KEEP
            f.score = 1.0
            f.target = ""
            if f.meta.get(_ORG_PLAN_LINKED):
                f.add_reason("Liegt in einem verknüpften Ordner (Junction/Symlink) – "
                             "wird nicht verändert")
                continue
            proj = _org_plan_project_of(f.rel, proj_set) or "."
            f.add_reason("Teil des Projekts „%s“ – wird nicht verändert"
                         % (root.name if proj == "." else proj))
    work = [f for f in files if not f.in_project]
    n_user = _org_plan_apply_user_protect(work, protected_paths, _org_plan_key(root))
    plan.stats["user_protected"] = n_user

    # ---- 2. Reste & Installer ----
    org_detect_junk(work, now)
    if programs is None:
        try:
            programs = org_installed_programs()
        except Exception:
            programs = []
    org_mark_installers(work, list(programs or []), now)
    _org_plan_enforce_protect(work)
    _org_plan_check(cancelled)

    # ---- 3. Inhalte lesen ----
    _org_plan_read_contents(plan, work, extract_text, progress, cancelled)

    # ---- 4. Einordnen ----
    _org_plan_classify(work, progress, cancelled)
    for f in work:
        if _org_plan_user_protected(f):
            f.tier = ORG_PROTECT

    # ---- 5. Exakte Duplikate (inkl. Projektdateien als mögliche Originale) ----
    stage = "Duplikate suchen"
    _org_plan_call_progress(progress, stage, 0, 0)
    cands = [f for f in files if f.tier != ORG_JUNK]
    groups = org_find_duplicates(
        cands, cancelled=lambda: _org_plan_is_cancelled(cancelled),
        progress=lambda d, t: _org_plan_call_progress(progress, stage, d, t))
    _org_plan_check(cancelled)
    plan.dup_groups = _org_plan_mark_duplicates(groups)

    # ---- 6. Versionen ----
    vgroups = org_version_groups([f for f in work if f.tier != ORG_JUNK])
    org_mark_versions(vgroups)
    plan.versions = {k: v for k, v in vgroups.items()
                     if any(not m.version_latest for m in v)}
    _org_plan_check(cancelled)

    # ---- 7. Fotos gruppieren ----
    _org_plan_call_progress(progress, "Fotos gruppieren", 0, 1)
    plan.stats["event_count"] = org_photo_events(work)
    _org_plan_call_progress(progress, "Fotos gruppieren", 1, 1)
    _org_plan_check(cancelled)

    # ---- 8. Schutzregel ----
    _org_plan_enforce_protect(work)

    # ---- 9. Plan ----
    plan.moves = _org_plan_build_moves(plan, reorganize_subfolders, progress, cancelled)

    # ---- 10. Leere Ordner, Listen ----
    empty = org_empty_dirs(root) if not plan.stats.get("root_is_project") else []
    if not recursive:
        rk = _org_plan_key(root)
        empty = [d for d in empty if _org_plan_key(Path(d).parent) == rk]
    # vom Nutzer geschützte und verknüpfte Ordner nie als „leer, löschen“ vorschlagen
    pkeys = _org_plan_protect_keys(protected_paths)
    rk = _org_plan_key(root)
    lkeys = {_org_plan_key(root.joinpath(*d.split("/"))) for d in link_dirs}
    if pkeys or lkeys:
        keep = []
        for d in empty:
            try:
                k = _org_plan_key(d)
                if _org_plan_key_protected(k, pkeys | lkeys, rk):
                    continue
            except Exception:
                continue
            keep.append(d)
        empty = keep
    plan.empty_dirs = list(empty)
    _org_plan_finish_lists(plan)


# ---------------------------------------------------------------------------
# Protokoll (Journal) – nach JEDER Verschiebung gültiges JSON auf der Platte
# ---------------------------------------------------------------------------
def _org_plan_load_journal(path: Path):
    """Liest ein Protokoll; repariert ein beim Absturz abgeschnittenes Ende."""
    try:
        raw = Path(path).read_bytes()
    except (OSError, TypeError, ValueError):
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    # Reparatur: Kopf lesen, dann Einträge einzeln bis zum ersten defekten
    try:
        idx = text.find('"moves"')
        if idx < 0:
            return None
        head = text[:idx].rstrip().rstrip(",") + "}"
        data = json.loads(head)
        if not isinstance(data, dict):
            return None
        pos = text.find("[", idx) + 1
        dec = json.JSONDecoder()
        moves = []
        n = len(text)
        while pos < n:
            while pos < n and text[pos] in " \r\n\t,":
                pos += 1
            if pos >= n or text[pos] == "]":
                break
            try:
                val, pos = dec.raw_decode(text, pos)
            except ValueError:
                break
            moves.append(val)
        data["moves"] = moves
        return data
    except Exception:
        return None


class _org_PlanJournal(object):
    """Hält die Protokolldatei: Kopf (root, created) + "moves"-Liste am Ende.
    Neue Verschiebungen werden angehängt (ohne die Datei neu zu schreiben);
    neue Ordner schreiben die Datei atomar neu (selten)."""

    def __init__(self, path: Path, root: Path, created=None, moves=None):
        self.path = Path(path)
        self.root = str(root)
        self.created = list(created or [])
        self.moves = list(moves or [])
        self.fh = None
        self.tail = 0
        self._rewrite()

    def _rewrite(self) -> None:
        if self.fh is not None:
            try:
                self.fh.close()
            except OSError:
                pass
            self.fh = None
        head = json.dumps({"root": self.root, "created": self.created}, ensure_ascii=False)
        body = head[:-1] + ', "moves": ['
        entries = ",\n".join(json.dumps(m, ensure_ascii=False) for m in self.moves)
        data = (body + ("\n" + entries if entries else "")).encode("utf-8")
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(str(tmp), "wb") as fh:
            fh.write(data + _ORG_PLAN_JOURNAL_TAIL)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(str(tmp), str(self.path))
        self.fh = open(str(self.path), "r+b")
        self.tail = len(data)

    def add_created(self, d: str) -> None:
        self.created.append(d)
        self._rewrite()

    def add_move(self, src: str, dst: str) -> int:
        """Hängt [src, dst] an; liefert die alte Endposition (für rollback)."""
        prev = self.tail
        entry = json.dumps([src, dst], ensure_ascii=False).encode("utf-8")
        data = (b",\n" if self.moves else b"\n") + entry
        self.fh.seek(prev)
        self.fh.write(data + _ORG_PLAN_JOURNAL_TAIL)
        self.fh.truncate()
        self.fh.flush()
        self.tail = prev + len(data)
        self.moves.append([src, dst])
        return prev

    def rollback(self, prev: int) -> None:
        """Letzten Eintrag zurücknehmen (Verschiebung ist gescheitert)."""
        self.moves.pop()
        self.fh.seek(prev)
        self.fh.write(_ORG_PLAN_JOURNAL_TAIL)
        self.fh.truncate()
        self.fh.flush()
        self.tail = prev

    def close(self) -> None:
        if self.fh is not None:
            try:
                self.fh.flush()
                os.fsync(self.fh.fileno())
            except (OSError, ValueError):
                pass
            try:
                self.fh.close()
            except OSError:
                pass
            self.fh = None


def _org_plan_safe_dest(root: Path, dest_rel):
    """Zielordner unter root oder None (Pfad-Ausbruch, ungültige Namen)."""
    if not isinstance(dest_rel, str):
        return None
    s = dest_rel.replace("\\", "/").strip()
    if s.startswith("/") or _ORG_PLAN_DRIVE_RE.match(s) or "\x00" in s:
        return None
    parts = [p for p in s.split("/") if p not in ("", ".")]
    for p in parts:
        if p == ".." or _ORG_PLAN_BAD_CHARS.search(p) or p != p.rstrip(". ") or \
                p.split(".")[0].lower() in _ORG_PLAN_RESERVED or p != p.strip():
            return None
    d = root.joinpath(*parts) if parts else root
    try:
        real = Path(os.path.realpath(str(d)))
    except (OSError, ValueError):
        return None
    if not _org_plan_inside(_org_plan_key(os.path.realpath(str(root))), _org_plan_key(real)):
        return None
    return real


def _org_plan_open_journal(journal_path: Path, root: Path):
    """Neues Protokoll oder Fortsetzung eines vorhandenen (gleicher Wurzelordner).
    Ein fremdes/defektes Protokoll wird nie überschrieben → (None, Meldung)."""
    jp = Path(journal_path)
    if os.path.lexists(str(jp)):
        data = _org_plan_load_journal(jp)
        if not data or _org_plan_key(str(data.get("root", ""))) != _org_plan_key(root):
            return None, "Protokolldatei existiert bereits und gehört nicht zu diesem Ordner: %s" % jp
        return _org_PlanJournal(jp, root, data.get("created") or [],
                                data.get("moves") or []), ""
    try:
        jp.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return _org_PlanJournal(jp, root), ""


def org_apply_moves(plan: OrgPlan, moves: list, journal_path: Path) -> tuple:
    """Verschiebt die gewählten Dateien. Nie überschreiben, nie aus plan.root hinaus.
    Das Protokoll ist nach jeder Verschiebung gültiges JSON:
    {"root": str, "created": [Ordner], "moves": [[alt, neu], …]}.
    Liefert (Anzahl verschoben, Fehlermeldungen)."""
    failures = []
    moved = 0
    try:
        root = Path(os.path.realpath(str(plan.root)))
        if not root.is_dir():
            return 0, ["Ordner nicht gefunden: %s" % plan.root]
        journal, msg = _org_plan_open_journal(Path(journal_path), root)
        if journal is None:
            return 0, [msg]
    except Exception as e:
        return 0, ["Protokoll kann nicht angelegt werden: %s" % (e,)]
    root_key = _org_plan_key(root)
    try:
        journal_keys = {_org_plan_real_key(journal.path),
                        _org_plan_real_key(journal.path.with_name(journal.path.name + ".tmp"))}
    except Exception:
        journal_keys = set()
    proj_cache = {}

    def _dest_in_project(dest_dir: Path) -> bool:
        # jeder vorhandene Ordner unterhalb von root bis zum Ziel: Projektordner?
        try:
            parts = dest_dir.relative_to(root).parts
        except ValueError:
            return True
        cur = root
        for part in parts:
            cur = cur / part
            k = _org_plan_key(cur)
            if k not in proj_cache:
                proj_cache[k] = os.path.isdir(str(cur)) and org_is_project_dir(cur)
            if proj_cache[k]:
                return True
        return False

    try:
        for m in list(moves or []):
            f = getattr(m, "file", None)
            rel = str(getattr(f, "rel", "") or "?")
            try:
                if f is None:
                    continue
                if getattr(f, "in_project", False):
                    failures.append("Projektdatei wird nicht verschoben: %s" % rel)
                    continue
                src = Path(f.path)
                if not os.path.isfile(str(src)) or os.path.islink(str(src)):
                    failures.append("Nicht mehr vorhanden: %s" % rel)
                    continue
                src_real = Path(os.path.realpath(str(src)))
                if not _org_plan_inside(root_key, _org_plan_key(src_real)):
                    failures.append("Liegt außerhalb des Ordners: %s" % rel)
                    continue
                if _org_plan_key(src_real) in journal_keys:
                    failures.append("Protokolldatei wird nicht verschoben: %s" % rel)
                    continue
                try:
                    st = os.stat(str(src))
                except OSError:
                    failures.append("Nicht lesbar: %s" % rel)
                    continue
                if not _org_plan_unchanged(f, st):
                    failures.append("Seit der Analyse verändert – nicht verschoben: %s" % rel)
                    continue
                dest_dir = _org_plan_safe_dest(root, getattr(m, "dest_rel", None))
                if dest_dir is None:
                    failures.append("Ungültiges Ziel abgelehnt: %s → %s"
                                    % (rel, getattr(m, "dest_rel", "")))
                    continue
                if _org_plan_key(src_real.parent) == _org_plan_key(dest_dir):
                    continue                        # liegt schon dort
                if _dest_in_project(dest_dir):
                    failures.append("Zielordner liegt in einem Projekt – nicht verschoben: "
                                    "%s → %s" % (rel, m.dest_rel))
                    continue
                # Ordner anlegen (von oben nach unten), jeden neuen protokollieren
                rel_parts = dest_dir.relative_to(root).parts if dest_dir != root else ()
                cur = root
                ok = True
                for part in rel_parts:
                    cur = cur / part
                    if os.path.isdir(str(cur)):
                        continue
                    if os.path.lexists(str(cur)):
                        ok = False
                        break
                    journal.add_created(str(cur))
                    os.mkdir(str(cur))
                if not ok:
                    failures.append("Zielordner ist eine Datei: %s" % m.dest_rel)
                    continue
                # nach dem Anlegen erneut prüfen (Verknüpfungen)
                if not _org_plan_inside(root_key, _org_plan_key(os.path.realpath(str(dest_dir)))):
                    failures.append("Ungültiges Ziel abgelehnt: %s" % m.dest_rel)
                    continue
                target = _org_plan_free_name(dest_dir, src.name)
                # echter Ort ins Protokoll (wie root/target), sonst lehnt org_undo den
                # Eintrag bei einem Wurzelordner hinter einer Verknüpfung als „außerhalb“ ab
                prev = journal.add_move(str(src_real), str(target))
                try:
                    _org_plan_rename_noreplace(src, target)
                except FileExistsError:
                    journal.rollback(prev)
                    target = _org_plan_free_name(dest_dir, src.name)   # zweiter Versuch
                    prev = journal.add_move(str(src_real), str(target))
                    try:
                        _org_plan_rename_noreplace(src, target)
                    except OSError as e:
                        journal.rollback(prev)
                        failures.append("Verschieben fehlgeschlagen: %s (%s)" % (rel, e))
                        continue
                except OSError as e:
                    journal.rollback(prev)
                    failures.append("Verschieben fehlgeschlagen: %s (%s)" % (rel, e))
                    continue
                moved += 1
                try:
                    new_rel = target.relative_to(root).as_posix()
                    f.path = target
                    f.rel = new_rel
                    f.depth = new_rel.count("/")
                    f.target = ""
                except Exception:
                    pass
            except Exception as e:
                failures.append("Verschieben fehlgeschlagen: %s (%s)" % (rel, e))
    finally:
        journal.close()
    return moved, failures


def _org_plan_undone_name(jp: Path) -> Path:
    name = jp.name
    base = name[:-5] if name.lower().endswith(".json") else name
    cand = jp.with_name(base + ".undone.json")
    k = 2
    while os.path.lexists(str(cand)):
        cand = jp.with_name("%s.undone (%d).json" % (base, k))
        k += 1
    return cand


def org_undo(journal_path: Path) -> tuple:
    """Macht die Verschiebungen eines Protokolls rückgängig (in umgekehrter Reihenfolge).
    Liefert (Anzahl wiederhergestellt, Meldungen)."""
    msgs = []
    restored = 0
    try:
        jp = Path(journal_path)
        data = _org_plan_load_journal(jp)
        if not data or not isinstance(data.get("moves", []), list):
            return 0, ["Protokoll nicht lesbar: %s" % journal_path]
        root = str(data.get("root") or "")
        if not root:
            return 0, ["Protokoll ohne Ordnerangabe: %s" % journal_path]
        root_key = _org_plan_key(os.path.realpath(root))
    except Exception as e:
        return 0, ["Protokoll nicht lesbar: %s (%s)" % (journal_path, e)]
    remaining = []
    for entry in reversed(list(data.get("moves") or [])):
        try:
            if not (isinstance(entry, (list, tuple)) and len(entry) >= 2 and
                    isinstance(entry[0], str) and isinstance(entry[1], str)):
                msgs.append("Ungültiger Protokolleintrag übersprungen")
                continue
            src, dst = entry[0], entry[1]
            name = os.path.basename(dst)
            # echter Ort zählt: Ordner, die inzwischen Verknüpfungen nach außen sind,
            # werden abgelehnt (weder von außen holen noch nach außen schieben)
            if not (_org_plan_inside(root_key, _org_plan_key(os.path.abspath(src))) and
                    _org_plan_inside(root_key, _org_plan_key(os.path.abspath(dst))) and
                    _org_plan_inside(root_key, _org_plan_real_key(src)) and
                    _org_plan_inside(root_key, _org_plan_real_key(dst))):
                msgs.append("Eintrag außerhalb des Ordners übersprungen: %s" % name)
                continue
            if not os.path.lexists(dst):
                if os.path.lexists(src):
                    continue            # Verschiebung fand nie statt (Absturz davor)
                msgs.append("„%s“ wurde inzwischen verschoben oder gelöscht – übersprungen"
                            % name)
                continue
            if not os.path.isfile(dst) or os.path.islink(dst):
                msgs.append("„%s“ ist keine Datei mehr – übersprungen" % name)
                continue
            src_p = Path(src)
            parent = src_p.parent
            if os.name != "nt" and os.path.isfile(src) and not os.path.islink(src):
                # Absturz zwischen „link“ und „unlink“: beide Namen zeigen auf dieselben
                # Daten → nur den zusätzlichen Namen entfernen (Inode geprüft, nie 0)
                try:
                    s1, s2 = os.stat(src), os.stat(dst)
                    same = bool(s1.st_ino) and (s1.st_dev, s1.st_ino) == (s2.st_dev, s2.st_ino)
                except OSError:
                    same = False
                if same:
                    os.unlink(dst)
                    restored += 1
                    continue
            if not os.path.isdir(str(parent)):
                os.makedirs(str(parent), exist_ok=True)
            target = src_p
            if os.path.lexists(src):
                target = _org_plan_free_name(parent, src_p.name, "wiederhergestellt")
                msgs.append("„%s“ war belegt – wiederhergestellt als „%s“"
                            % (src_p.name, target.name))
            try:
                _org_plan_rename_noreplace(dst, target)
            except FileExistsError:
                target = _org_plan_free_name(parent, src_p.name, "wiederhergestellt")
                _org_plan_rename_noreplace(dst, target)
            restored += 1
        except Exception as e:
            remaining.append(list(entry[:2]) if isinstance(entry, (list, tuple)) else entry)
            msgs.append("Wiederherstellen fehlgeschlagen: %s (%s)"
                        % (os.path.basename(str(entry[1])) if isinstance(entry, (list, tuple))
                           and len(entry) > 1 else "?", e))
    # angelegte Ordner entfernen, wenn leer (tiefste zuerst)
    created = [d for d in (data.get("created") or []) if isinstance(d, str)]
    for d in sorted(created, key=len, reverse=True):
        try:
            if not _org_plan_inside(root_key, _org_plan_key(os.path.abspath(d))) or \
                    _org_plan_key(os.path.abspath(d)) == root_key:
                continue
            if os.path.isdir(d) and not os.path.islink(d) and not os.listdir(d):
                os.rmdir(d)
        except OSError:
            continue
    try:
        if remaining:
            remaining.reverse()
            j = _org_PlanJournal(jp, Path(root), data.get("created") or [], remaining)
            j.close()
            msgs.append("Protokoll bleibt für einen weiteren Versuch erhalten")
        else:
            os.replace(str(jp), str(_org_plan_undone_name(jp)))
    except Exception as e:
        msgs.append("Protokoll konnte nicht abgeschlossen werden: %s" % (e,))
    return restored, msgs


# ---------------------------------------------------------------------------
# Papierkorb
# ---------------------------------------------------------------------------
def _org_plan_keeper_path(f):
    k = None
    try:
        k = f.meta.get(_ORG_PLAN_DUP_KEEPER)
    except Exception:
        k = None
    if k is not None:
        try:
            return str(k.path)
        except Exception:
            pass
    root = _org_plan_root_of(f)
    if root is None or not f.dup_of:
        return None
    return str(root.joinpath(*[p for p in str(f.dup_of).replace("\\", "/").split("/") if p]))


def _org_plan_important(f) -> bool:
    """Wichtige Datei (auch wenn die Stufe herabgesetzt wurde, z. B. „Ältere Version“
    eines Zeugnisses oder Sicherheitsprüfung)?"""
    try:
        if getattr(f, "tier", "") == ORG_PROTECT:
            return True
        cat = getattr(f, "category", "") or ""
        if cat not in ("temp", "duplicate") and _org_plan_tier_of(cat) == ORG_PROTECT:
            return True
        meta = getattr(f, "meta", None) or {}
        return bool(meta.get("_org_ph_protect")) or meta.get("orig_tier") == ORG_PROTECT
    except Exception:
        return True


def _org_plan_dup_ok(f, p: str, cur_size: int, trashed: set):
    """Darf die Kopie f (Pfad p) weg? → (ok, Schlüssel des Originals)."""
    kp = _org_plan_keeper_path(f)
    if not kp:
        return False, ""
    kk = _org_plan_key(kp)
    if kk == _org_plan_key(p) or kk in trashed:
        return False, kk
    if not os.path.isfile(kp) or os.path.islink(kp):
        return False, kk
    try:
        kst = os.stat(kp)
        if kst.st_size != cur_size:
            return False, kk
        if os.path.samefile(kp, p):          # derselbe Datenbestand (Link) → nie löschen
            return False, kk
    except OSError:
        return False, kk
    kobj = None
    try:
        kobj = f.meta.get(_ORG_PLAN_DUP_KEEPER)
    except Exception:
        kobj = None
    if kobj is not None and _org_plan_unchanged(kobj, kst):
        return True, kk
    # Original unbekannt oder seit der Analyse verändert → Inhalt Byte für Byte vergleichen
    h1 = _org_plan_hash_file(p)[0]
    h2 = _org_plan_hash_file(kp)[0]
    return bool(h1) and h1 == h2, kk


def org_trash(files: list, trash_fn) -> tuple:
    """Verschiebt Dateien mit trash_fn(pfad) -> bool in den Papierkorb.
    Verweigert: Geschütztes (außer exakte Kopie, deren Original noch da und unverändert
    ist), Projektdateien, verschwundene oder seit der Analyse veränderte Dateien,
    Dateien außerhalb des analysierten Ordners (Verknüpfungen) und das Original von
    Kopien, die im selben Aufruf gelöscht wurden. Liefert (Anzahl, Fehlermeldungen)."""
    count = 0
    fails = []
    trashed = set()
    needed = set()          # Originale bereits gelöschter Kopien → müssen bleiben
    try:
        items = list(files or [])
    except Exception:
        return 0, ["Ungültige Dateiliste"]
    for f in items:
        rel = str(getattr(f, "rel", "") or getattr(f, "path", "?"))
        try:
            if getattr(f, "in_project", False):
                fails.append("Projektdatei – nicht gelöscht: %s" % rel)
                continue
            if _org_plan_user_protected(f):
                fails.append("Vom Nutzer geschützt – nicht gelöscht: %s" % rel)
                continue
            p = str(f.path)
            if not os.path.isfile(p) or os.path.islink(p):
                fails.append("Nicht mehr vorhanden: %s" % rel)
                continue
            root = _org_plan_root_of(f)
            if root is not None and not _org_plan_inside(
                    _org_plan_key(os.path.realpath(str(root))),
                    _org_plan_key(os.path.realpath(p))):
                fails.append("Liegt außerhalb des Ordners (Verknüpfung) – nicht gelöscht: %s"
                             % rel)
                continue
            pk = _org_plan_key(p)
            if pk in needed:
                fails.append("Original gelöschter Kopien – bleibt erhalten: %s" % rel)
                continue
            try:
                st = os.stat(p)
            except OSError:
                fails.append("Nicht lesbar: %s" % rel)
                continue
            if not _org_plan_unchanged(f, st):
                fails.append("Seit der Analyse verändert – nicht gelöscht: %s" % rel)
                continue
            keeper_key = ""
            if f.dup_of:
                ok, keeper_key = _org_plan_dup_ok(f, p, st.st_size, trashed)
                if not ok:
                    fails.append("Original fehlt oder wurde verändert – Kopie bleibt: %s" % rel)
                    continue
            elif _org_plan_important(f):
                fails.append("Wichtige Datei – wird nicht gelöscht: %s" % rel)
                continue
            err = None
            try:
                ok = bool(trash_fn(p))
            except Exception as e:
                ok = False
                err = e
            if ok or not os.path.lexists(p):
                # auch bei gemeldetem Fehler, wenn die Datei trotzdem weg ist
                trashed.add(pk)
                if keeper_key:
                    needed.add(keeper_key)
            if ok:
                count += 1
            elif err is not None:
                fails.append("Papierkorb fehlgeschlagen: %s (%s)" % (rel, err))
            else:
                fails.append("Papierkorb fehlgeschlagen: %s" % rel)
        except Exception as e:
            fails.append("Papierkorb fehlgeschlagen: %s (%s)" % (rel, e))
    return count, fails


# ---------------------------------------------------------------------------
# Tresor (Sicherung wichtiger Dateien)
# ---------------------------------------------------------------------------
def _org_plan_backup_folder(f) -> str:
    folder = _org_plan_folder_for(f, "")
    if not folder:
        cat = f.category
        if cat == "duplicate":
            cat = f.meta.get("orig_category") or ""
        label = ORG_CATEGORIES.get(cat, {}).get("label", "") if cat else ""
        folder = _org_plan_sanitize_part(label) or "Sonstiges"
    return folder


def org_backup(files: list, dest_root: Path, progress=None, cancelled=lambda: False) -> dict:
    """Kopiert wichtige Dateien nach dest_root/<Kategorie-Ordner>/<Name>.
    Inkrementell (gleiche Größe + blake2b → übersprungen), Namenskonflikt → "x (2).ext",
    Kopie über Zwischendatei + Prüfsummenvergleich. Löscht/überschreibt in dest_root nie
    etwas außer der eigenen Zwischendatei. → {"copied", "skipped", "failed", "bytes"}"""
    res = {"copied": 0, "skipped": 0, "failed": [], "bytes": 0}
    try:
        dest = Path(dest_root)
        os.makedirs(str(dest), exist_ok=True)
        dest_real = Path(os.path.realpath(str(dest)))
        dest_key = _org_plan_key(dest_real)
        items = [f for f in list(files or []) if f is not None]
    except Exception as e:
        res["failed"].append("Sicherungsziel nicht verfügbar: %s (%s)" % (dest_root, e))
        return res
    total = len(items)
    for i, f in enumerate(items):
        if _org_plan_is_cancelled(cancelled):
            res["cancelled"] = True
            break
        _org_plan_call_progress(progress, "Sichern", i, total)
        rel = str(getattr(f, "rel", "") or "?")
        tmp = None
        try:
            src = str(f.path)
            if not os.path.isfile(src):
                res["failed"].append("Nicht gefunden: %s" % rel)
                continue
            if _org_plan_inside(dest_key, _org_plan_key(os.path.realpath(src))):
                res["skipped"] += 1                     # liegt schon im Tresor
                continue
            sub = _org_plan_backup_folder(f)
            tdir = dest_real.joinpath(*[p for p in sub.split("/") if p])
            if not _org_plan_inside(dest_key, _org_plan_key(tdir)):
                res["failed"].append("Ungültiger Zielordner: %s" % sub)
                continue
            os.makedirs(str(tdir), exist_ok=True)
            src_hash, src_size = _org_plan_hash_file(src, cancelled)
            if src_hash is None:
                res["failed"].append("Nicht lesbar: %s" % rel)
                continue
            name = Path(src).name
            stem, ext = _org_plan_split_name(name)
            target = None
            skip = False
            for k in range(1, 100000):
                cand = tdir / (name if k == 1 else "%s (%d)%s" % (stem, k, ext))
                if not os.path.lexists(str(cand)):
                    target = cand
                    break
                try:
                    if os.path.isfile(str(cand)) and os.path.getsize(str(cand)) == src_size \
                            and _org_plan_hash_file(cand)[0] == src_hash:
                        skip = True
                        break
                except OSError:
                    continue
            if skip:
                res["skipped"] += 1
                continue
            if target is None:
                res["failed"].append("Kein freier Name im Tresor: %s" % name)
                continue
            tmp = tdir / ("~org_tmp_%s.part" % uuid.uuid4().hex)
            shutil.copy2(src, str(tmp))
            got, _sz = _org_plan_hash_file(tmp)
            if got != src_hash:
                try:
                    os.unlink(str(tmp))
                except OSError:
                    pass
                tmp = None
                res["failed"].append("Prüfsumme nach dem Kopieren falsch: %s" % rel)
                continue
            try:
                _org_plan_rename_noreplace(tmp, target)
            except FileExistsError:
                target = _org_plan_free_name(tdir, name)
                _org_plan_rename_noreplace(tmp, target)
            tmp = None
            res["copied"] += 1
            res["bytes"] += src_size
        except Exception as e:
            res["failed"].append("Sichern fehlgeschlagen: %s (%s)" % (rel, e))
        finally:
            if tmp is not None:
                try:
                    if os.path.isfile(str(tmp)):
                        os.unlink(str(tmp))     # nur die eigene Zwischendatei
                except OSError:
                    pass
    _org_plan_call_progress(progress, "Sichern", total, total)
    return res



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
           "py -m pip install PySide6 PySideSix-Frameless-Window")


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

from PySide6.QtCore import (QFile, QFileInfo, QFileSystemWatcher, QMimeData, QObject, QPoint,
                            QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt, QThread,
                            QTimer, QUrl, QVariantAnimation, Signal)
from PySide6.QtGui import (QBrush, QColor, QCursor, QDesktopServices, QDrag, QFont, QIcon,
                           QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
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


def badge_icon(icon: QIcon, size=96, text="?", color: QColor | None = None) -> QIcon:
    """Punkt oben rechts: orange „?“ = unklar, „⚙“ = Programm-/Projektordner."""
    pm = icon.pixmap(size, size)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.drawPixmap(0, 0, pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    d = size * 0.38
    r = QRectF(size - d - 1, 1, d, d)
    p.setBrush(color or WARN)
    p.setPen(QPen(QColor(255, 255, 255, 220), 2))
    p.drawEllipse(r)
    f = QFont()
    f.setPixelSize(int(d * 0.7))
    f.setBold(True)
    p.setFont(f)
    p.setPen(Qt.white)
    p.drawText(r, Qt.AlignCenter, text)
    p.end()
    return QIcon(out)


def faded_icon(icon: QIcon, size=96, opacity=0.45) -> QIcon:
    """Blasses Symbol – „liegt schon in der Box“."""
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setOpacity(opacity)
    p.drawPixmap(0, 0, icon.pixmap(size, size).scaled(size, size, Qt.KeepAspectRatio,
                                                      Qt.SmoothTransformation))
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
class ElideLabel(QLabel):
    """Einzeilig, kürzt bei Platzmangel mit „…“ statt das Fenster zu verbreitern."""
    def setText(self, text: str):
        super().setText(text)
        self.setToolTip(text)

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, _):
        p = QPainter(self)
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.width())
        self.style().drawItemText(p, self.rect(), Qt.AlignLeft | Qt.AlignVCenter, self.palette(),
                                  self.isEnabled(), text, self.foregroundRole())


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
    pathMenuRequested = Signal(str, QPoint)      # Eintrag einer bestehenden Box
    deleteRequested = Signal(list)
    activated_ = Signal(int)
    pathActivated = Signal(str)

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
        self.itemDoubleClicked.connect(
            lambda it: self.pathActivated.emit(it.data(Qt.UserRole + 1))
            if it.data(Qt.UserRole) == -1 else self.activated_.emit(it.data(Qt.UserRole)))

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

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete and self.selectedItems():
            self.deleteRequested.emit(self.ids(self.selectedItems()))
        else:
            super().keyPressEvent(e)

    def _menu(self, pos):
        items = self.selectedItems()
        under = self.itemAt(pos)
        if under and under.data(Qt.UserRole) == -1:    # Box-Inhalt: eigenes Menü
            self.pathMenuRequested.emit(under.data(Qt.UserRole + 1),
                                        self.viewport().mapToGlobal(pos))
            return
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
                 new=False, parent=None, box_count: int | None = None, box_icons=(),
                 trash=False):
        super().__init__(parent)
        self.name, self.icons, self.count = name, icons, count
        self.selected, self.new, self.trash = selected, new, trash
        self.box_count = box_count               # None = (noch) keine Box auf dem Desktop
        self.box_icons = list(box_icons)
        self._hover = self._drag = False
        self.setFixedSize(self.W, self.H)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        if trash:
            tip = "Objekte hierher ziehen, um sie in den Papierkorb zu verschieben"
        elif new:
            tip = "Objekte hierher ziehen, um eine neue Gruppe anzulegen"
        elif box_count is not None:
            tip = (f"Box „{name}“ auf dem Desktop: {box_count} Objekt(e)"
                   + (f" · {count} neu" if count else " · Objekte hierher ziehen"))
        else:
            tip = f"{name}: {count} Objekt(e)"
        self.setToolTip(tip)
        if not (new or trash):
            shadow(self, 30, 10, 80)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and not (self.new or self.trash):
            self.clicked.emit(self.name)

    def contextMenuEvent(self, e):
        if not (self.new or self.trash):
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
        self.dropped.emit("" if (self.new or self.trash) else self.name,
                          decode_ids(e.mimeData()))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        red = QColor("#FF453A")
        paint_glass(p, self.rect(), 26, strength=0.6 if (self.new or self.trash) else 1.0,
                    tint=(red if self.trash else ACCENT) if self._drag else None,
                    hover=self._hover or self._drag, active=self.selected or self._drag,
                    dashed=(self.new or self.trash) and not self._drag)
        if self.new or self.trash:
            glyph, title, sub = (("🗑", "Papierkorb", "hierher ziehen = löschen") if self.trash
                                 else ("+", "Neue Gruppe", "hierher ziehen"))
            f = self.font()
            f.setPixelSize(36 if self.trash else 44)
            f.setWeight(QFont.Light)
            p.setFont(f)
            p.setPen(red.lighter(130) if self.trash else QColor(255, 255, 255, 200))
            p.drawText(QRectF(0, 40, self.W, 80), Qt.AlignCenter, glyph)
            f.setPixelSize(13)
            f.setWeight(QFont.DemiBold)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255, 220))
            p.drawText(QRectF(0, 128, self.W, 24), Qt.AlignCenter, title)
            f.setWeight(QFont.Normal)
            f.setPixelSize(11)
            p.setFont(f)
            p.setPen(TEXT_2)
            p.drawText(QRectF(0, 150, self.W, 20), Qt.AlignCenter, sub)
            return

        cell, gap, x0, y0 = 70, 12, 18, 18
        cells = [QRectF(x0 + (i % 2) * (cell + gap), y0 + (i // 2) * (cell + gap), cell, cell)
                 for i in range(4)]
        icons = self.icons or self.box_icons      # leer → Inhalt der bestehenden Box zeigen
        if not self.icons:
            p.setOpacity(0.45)
        big = icons[:3] if len(icons) > 4 else icons[:4]
        for ic, c in zip(big, cells):
            p.drawPixmap(c.adjusted(5, 5, -5, -5).toRect(), ic.pixmap(120, 120))
        if len(icons) > 4:                            # Mini-Raster in Zelle 4
            c = cells[3]
            mini = (cell - 8) / 2
            for j, ic in enumerate(icons[3:7]):
                r = QRectF(c.left() + (j % 2) * (mini + 8), c.top() + (j // 2) * (mini + 8),
                           mini, mini)
                p.drawPixmap(r.adjusted(2, 2, -2, -2).toRect(), ic.pixmap(64, 64))
        p.setOpacity(1.0)
        if not icons:
            f = self.font()
            f.setPixelSize(12)
            p.setFont(f)
            p.setPen(TEXT_2)
            p.drawText(QRectF(0, 60, self.W, 60), Qt.AlignCenter, "leer\nObjekte hierher ziehen")

        f = self.font()
        f.setPixelSize(13)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(Qt.white)
        title = ("▣ " if self.box_count is not None else "") + self.name
        title_r = QRectF(18, self.H - 36, self.W - 36 - 44, 22)
        p.drawText(title_r, Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(title, Qt.ElideRight, int(title_r.width())))
        f.setWeight(QFont.Normal)
        p.setFont(f)
        if self.box_count is not None:            # „+3“ neu bzw. Anzahl in der Box
            p.setPen(QColor("#64D2FF") if self.count else TEXT_2)
            text = f"+{self.count}" if self.count else str(self.box_count)
        else:
            p.setPen(TEXT_2)
            text = str(self.count)
        p.drawText(QRectF(self.W - 62, self.H - 36, 44, 22), Qt.AlignRight | Qt.AlignVCenter,
                   text)


class CardGrid(QWidget):
    """Fließendes Raster aus Karten; Spaltenzahl folgt der Fensterbreite."""
    PAD, GAP = 14, 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cards: list[GroupCard] = []

    def set_cards(self, cards):
        for c in self.cards:
            c.hide()                     # sofort weg, gelöscht wird erst in der Event-Schleife
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


class ResearchJob(QObject):
    """Recherche im Hintergrund, ohne die Oberfläche zu sperren. Daemon-Thread:
    eine hängende Anfrage hält das Beenden des Programms nicht auf."""
    progress = Signal(int, int, str)
    finished = Signal(dict, dict)

    def __init__(self, items: list[Item], sources: list[str], parent=None, force=False):
        super().__init__(parent)
        self.items, self.sources, self.force = items, sources, force
        self.cancel = False
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self):
        cache = research_cache_load()
        try:
            res, status = research_run(self.items, cache, sources=self.sources,
                                       progress=self.progress.emit, stop=lambda: self.cancel,
                                       force=self.force)
        except Exception as e:               # noqa: BLE001 — in der GUI anzeigen
            res, status = {}, {"_error": f"{type(e).__name__}: {e}"}
        research_cache_save(cache)
        self.finished.emit(res, status)


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
        self.icons = icon_cache()       # gemeinsam mit den Boxen, scharfe Shell-Symbole
        self._icon_refresh = QTimer(self, singleShot=True, interval=350)
        self._icon_refresh.timeout.connect(self._refresh_icons)
        self.icons.updated.connect(lambda _: self._icon_refresh.start())
        self.selected: str | None = None
        self.user_dir: Path | None = None
        self._worker: Worker | None = None
        self._research: ResearchJob | None = None
        self._research_cfg: dict = {}   # ohne Boxen-Verwaltung: Einstellungen nur für die Sitzung
        self.research_results: dict[str, dict] = {}  # Pfad → Ergebnis, übersteht Neu-Scans
        self._apply_pending = False     # „In Boxen sortieren“ wartet auf die Recherche
        self._research_next = None      # nach Abbruch als Nächstes starten

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
        self.research_label = ElideLabel("")
        self.research_label.setObjectName("sub")
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        titles.addWidget(self.research_label)
        head.addLayout(titles)
        head.addStretch(1)

        self.t_public = Toggle("Öffentlicher Desktop", checked=True)
        self.t_folders = Toggle("Ordner einbeziehen", checked=True)
        self.t_folders.setToolTip("Unterordner mit einsortieren. Programm-, Spiel- und "
                                  "Projektordner (⚙) bleiben trotzdem liegen.")
        self.t_public.toggled.connect(self.rescan)
        self.t_folders.toggled.connect(self.refresh)
        self.b_scan = GlassButton("↻  Neu scannen")
        self.b_apply = GlassButton("In Boxen sortieren", primary=True)
        self.b_scan.clicked.connect(self.rescan)
        self.b_apply.clicked.connect(self.apply)
        for w in (self.t_public, self.t_folders):
            head.addWidget(w)
            head.addSpacing(6)
        head.addSpacing(10)
        for w in (self.b_scan, self.b_apply):
            head.addWidget(w)
        root.addLayout(head)

        # Alle Funktionen an einer Stelle: Sortieren · Aufräumen · Boxen
        tools = QHBoxLayout()
        tools.setSpacing(8)
        self._fence_only: list[QWidget] = []

        def tool(text, slot, tip, fences=False):
            b = GlassButton(text, small=True)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            tools.addWidget(b)
            if fences:
                self._fence_only.append(b)
            return b

        def divider():
            line = QFrame()
            line.setFixedSize(1, 22)
            line.setStyleSheet("background: rgba(255,255,255,45);")
            tools.addSpacing(6)
            tools.addWidget(line)
            tools.addSpacing(6)

        self.b_research = tool("🔎  Recherche ▾", self.show_research_menu,
                               "Jede Zuordnung wird in offenen Quellen gegengeprüft "
                               "(Wikidata, Wikipedia, Flathub, Chocolatey) – bei jedem Scan "
                               "und vor dem Sortieren.\nGesendet werden nur Namen von "
                               "Verknüpfungen, Programmen und Ordnern.")
        self.b_undo = tool("↶  Rückgängig", self.undo, "Letzte Sortierung vollständig umkehren")
        divider()
        self.b_org = tool("📁  Ordner-Assistent ▾", self.show_organizer_menu,
                          "Beliebigen Ordner analysieren, sortieren und sicher aufräumen", True)
        self.b_clean = tool("🧹  Downloads", lambda: self.fences.open_cleaner(downloads_dir()),
                            "Downloads im Tinder-Stil durchwischen: links = Papierkorb, "
                            "rechts = behalten", True)
        self.b_trash = tool("🗑  Papierkorb", self.trash_selected,
                            "Markierte Objekte in den Papierkorb verschieben (Taste Entf)")
        divider()
        self.b_newbox = tool("+  Neue Box", self.new_box, "Leere Glas-Box anlegen", True)
        self.b_arrange = tool("▦  Anordnen", lambda: self.fences.arrange_all(),
                              "Alle Boxen ordentlich nebeneinander anordnen", True)
        self.b_vis = tool("◐  Ausblenden", self.toggle_boxes,
                          "Boxen auf dem Desktop aus- bzw. einblenden", True)
        self.b_more = tool("☰  Mehr ▾", self.show_more_menu,
                           "Ordner-Boxen, Layouts, Einstellungen, Beenden", True)
        tools.addStretch(1)
        root.addLayout(tools)
        if not self.fences:
            for b in self._fence_only:
                b.setToolTip(b.toolTip() + "\n(nur im Normalbetrieb mit Boxen)")
        else:
            self.fences.visibilityChanged.connect(self._update_vis_button)
            self._update_vis_button(self.fences.visible)
            self.fences.renamed.connect(self._box_renamed)

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
        self.detail_list.dropped.connect(self._drop_on_detail)
        self.detail_list.menuRequested.connect(self.item_menu)
        self.detail_list.pathMenuRequested.connect(self.path_menu)
        self.detail_list.pathActivated.connect(self.open_item)
        self.detail_list.deleteRequested.connect(self.trash_items)
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
        self.desk_list.deleteRequested.connect(self.trash_items)
        rl.addWidget(self.desk_list, 1)
        body.addWidget(self.desk)
        root.addLayout(body, 1)

        self.busy_widgets = [self.b_scan, self.b_research, self.b_undo, self.b_apply,
                             self.b_trash, self.b_accept, self.t_public, self.t_folders,
                             self.grid, self.detail, self.desk] + self._fence_only
        self.set_busy(None)

    def paintEvent(self, _):
        p = QPainter(self)
        paint_backdrop(p, self.rect(), overlay_only=ACRYLIC)

    # ---------- Daten ----------
    def icon_for(self, it: Item) -> QIcon:
        return self.icons.get(it.path)

    def _refresh_icons(self):
        """Nachgeladene Shell-Symbole übernehmen (nicht während eines Scans)."""
        busy = self._worker is not None and self._worker.isRunning()
        if self.isVisible() and self.items and not busy:
            self.refresh()

    def box_dirs(self) -> dict[str, Path]:
        """Bestehende Glas-Boxen (Ordner im Box-Speicher) – immer als Ziel verfügbar."""
        if self.fences:
            return self.fences.group_dirs()
        try:
            return {d.name: d for d in sorted(self.args.storage.iterdir())
                    if d.is_dir() and not d.name.startswith(".")}
        except OSError:
            return {}

    @staticmethod
    def box_entries(folder: Path) -> list[Path]:
        try:
            return sorted((p for p in folder.iterdir() if p.name.lower() != "desktop.ini"
                           and not p.name.startswith(".")), key=lambda p: p.name.lower())
        except OSError:
            return []

    def known_groups(self, groups=None) -> list[str]:
        names = list(self.box_dirs())                # bestehende Boxen zuerst
        names += list(groups or []) + [c for c in CATEGORIES if c != "Sonstiges"]
        names += [c for c, _ in self.toml_rules] + self.memory.groups
        return list(dict.fromkeys(names))

    def plan(self):
        """Unklare Objekte bleiben bis zur Entscheidung auf dem Desktop. Was zu einer
        bestehenden Box passt, kommt auch einzeln hinein."""
        inc = self.t_folders.isChecked()
        pool = [it for it in self.items if it.kind != "folder" or inc]
        sure = [it for it in pool if it.confidence != "low"]
        groups, left = plan(sure, self.args.min_group, inc)
        boxes = self.box_dirs()
        for it in [i for i in left if i.category in boxes and i.confidence != "keep"]:
            groups.setdefault(it.category, []).append(it)
            left.remove(it)
        unclear = [it for it in pool if it.confidence == "low"]
        return groups, unclear + sorted(left, key=lambda i: i.label.lower())

    def set_busy(self, text: str | None):
        for w in self.busy_widgets:
            w.setEnabled(text is None and (self.fences is not None or w not in self._fence_only))
        if text:
            self.subtitle.setText(text)
        if bool(text) != getattr(self, "_busy", False):
            self._busy = bool(text)
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
            install_dirs = installed_locations() if any(i.kind == "folder" for i in items) else set()
            for it in items:
                classify(it, rules, self.memory)
                classify_folder(it, install_dirs)    # Programmordner bleiben liegen
            return user, items

        def done(res):
            self.user_dir, self.items = res
            self.apply_research()                    # bekannte Ergebnisse sofort
            self.refresh()
            on, srcs = self.research_settings()
            pending = [it for it in research_targets(self.items)
                       if str(it.path) not in self.research_results]
            if on and srcs and pending:
                self.start_research(pending)
            else:
                self.update_research_label()
                if self._apply_pending:
                    self._apply_pending = False
                    QTimer.singleShot(0, self.apply)

        self.run_bg("Scanne Desktop …", job, done)

    def refresh(self):
        groups, left = self.plan()
        boxes = {n: self.box_entries(d) for n, d in self.box_dirs().items()}
        names = list(groups) + [b for b in boxes if b not in groups]
        if self.selected not in names:
            self.selected = None
        index = {id(it): i for i, it in enumerate(self.items)}

        cards = []
        for name in names:
            its = groups.get(name, [])
            box = boxes.get(name)
            c = GroupCard(name, [self.icon_for(i) for i in its[:7]], len(its),
                          selected=name == self.selected,
                          box_count=None if box is None else len(box),
                          box_icons=[self.icons.get(p) for p in (box or [])[:7]])
            c.clicked.connect(self.select_group)
            c.dropped.connect(self.on_card_drop)
            c.menuRequested.connect(self.card_menu)
            cards.append(c)
        new = GroupCard("", [], 0, new=True)
        new.dropped.connect(self.on_card_drop)
        trash = GroupCard("", [], 0, trash=True)
        trash.dropped.connect(lambda _n, ids: QTimer.singleShot(0, lambda: self.trash_items(ids)))
        cards += [new, trash]
        self.grid.set_cards(cards)

        self.desk_list.clear()
        unclear = progs = 0
        for it in left:
            low = it.confidence == "low"
            prog = it.role in RESEARCH_LEAVE and not low
            unclear += low
            progs += prog
            icon = self.icon_for(it)
            if low:
                icon = badge_icon(icon)
            elif prog:
                icon = badge_icon(icon, text="⚙", color=QColor("#5E5CE6") if it.role == "program"
                                  else QColor("#30B0C7"))
            li = QListWidgetItem(icon, it.label)
            li.setData(Qt.UserRole, index[id(it)])
            if low:
                tip = (f"Unklar — Vorschlag: {it.category} ({it.reason})\nDoppelklick = übernehmen"
                       if it.category != "Sonstiges" else
                       "Unklar — keine Zuordnung gefunden" +
                       (f"\n{it.reason}" if "Recherche" in it.reason else ""))
            elif prog:
                tip = (f"Bleibt liegen, damit es weiter funktioniert:\n{it.reason}\n"
                       f"Auf eine Gruppe ziehen verschiebt trotzdem (mit Rückfrage).")
            else:
                tip = f"Bleibt auf dem Desktop ({it.reason})"
            li.setToolTip(tip + self.research_tip(it))
            self.desk_list.addItem(li)
        parts = [f"{unclear} unklar"] if unclear else []
        parts += [f"{progs} Programmordner"] if progs else []
        parts += [f"{len(left) - unclear - progs} bleiben"]
        self.desk_sub.setText(" · ".join(parts) + ("" if unclear else " · alles zugeordnet"))
        self.b_accept.setVisible(any(i.confidence == "low" and i.category != "Sonstiges"
                                     for i in left))

        self.detail_list.clear()
        self.detail_list.setAcceptDrops(self.selected is not None)
        if self.selected:
            its = groups.get(self.selected, [])
            box = boxes.get(self.selected)
            if box is None:
                info = f"{len(its)} Objekte"
            elif its:
                info = f"{len(its)} neu  ·  {len(box)} bereits in der Box"
            else:
                info = f"{len(box)} in der Box  ·  Objekte hierher ziehen, um sie hinzuzufügen"
            self.detail_title.setText(("▣ " if box is not None else "") +
                                      f"{self.selected}  ·  {info}")
            for it in its:
                li = QListWidgetItem(self.icon_for(it), it.label)
                li.setData(Qt.UserRole, index[id(it)])
                li.setToolTip(it.reason + self.research_tip(it))
                self.detail_list.addItem(li)
            for p in box or []:                     # schon in der Box: nur ansehen/öffnen
                li = QListWidgetItem(faded_icon(self.icons.get(p)), display_name(p))
                li.setData(Qt.UserRole, -1)
                li.setData(Qt.UserRole + 1, str(p))
                li.setFlags(Qt.ItemIsEnabled)
                li.setToolTip("Bereits in der Box · Doppelklick öffnet · Rechtsklick für mehr")
                self.detail_list.addItem(li)
        else:
            self.detail_title.setText("Gruppe oder Box anklicken, um ihren Inhalt zu sehen")

        n_items = sum(len(g) for g in groups.values())
        self.subtitle.setText(f"{len(self.items)} Objekte gefunden  ·  {n_items} werden in "
                              f"{len(groups)} Gruppen sortiert  ·  {len(boxes)} Boxen  ·  "
                              f"{unclear} unklar")

    def later_refresh(self):
        QTimer.singleShot(0, self.refresh)   # nach Ende des Drag-Vorgangs

    # ---------- Aktionen auf Objekten ----------
    def assign(self, ids, group: str):
        ids = [i for i in ids if 0 <= i < len(self.items)]
        progs = [self.items[i] for i in ids
                 if self.items[i].role in RESEARCH_LEAVE and self.items[i].confidence != "user"]
        if progs and not self.confirm(
                "Programmordner verschieben?",
                "Diese Ordner sehen nach installierten Programmen, Spielen oder Projekten aus:\n\n"
                + "\n".join(f"• {p.label} – {p.reason}" for p in progs[:8])
                + "\n\nNach dem Verschieben funktionieren Verknüpfungen, Startmenü-Einträge, "
                  "Updates oder die Entwicklungsumgebung oft nicht mehr.\n\n"
                  f"Trotzdem in „{group}“ einsortieren?"):
            return
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
        QTimer.singleShot(0, lambda: self._card_drop(group, ids))   # Dialoge erst nach dem Drag

    def _card_drop(self, group, ids):
        if not group:
            group = self.ask_group_name()
            if not group:
                return
        self.assign(ids, group)

    def _drop_on_detail(self, ids):
        if self.selected:
            group = self.selected
            QTimer.singleShot(0, lambda: self.assign(ids, group))

    def select_group(self, name):
        self.selected = None if self.selected == name else name
        self.refresh()

    def open_item(self, path):
        try:
            open_path(str(path))
        except OSError as e:
            self.message("Öffnen", f"Konnte nicht öffnen:\n{path}\n\n{e}", warn=True)

    def item_menu(self, ids, pos):
        ids = [i for i in ids if 0 <= i < len(self.items)]
        if not ids:
            return
        its = [self.items[i] for i in ids]
        groups, _ = self.plan()
        m = glass_menu(self)
        one = its[0] if len(its) == 1 else None
        if any(i.confidence == "low" and i.category != "Sonstiges" for i in its):
            hint = f": {one.category}" if one else ""
            m.addAction(f"✓  Vorschlag übernehmen{hint}", lambda: self.accept_suggestions(ids))
        sub = glass_menu(m, "Verschieben nach")
        boxes = self.box_dirs()
        for g in self.known_groups(groups):
            act = sub.addAction(("▣  " if g in boxes else "") + g, lambda g=g: self.assign(ids, g))
            act.setEnabled(not (one and one.category == g and one.confidence != "low"))
        m.addMenu(sub)
        m.addAction("+  Neue Gruppe …", lambda: (n := self.ask_group_name()) and self.assign(ids, n))
        m.addAction("⌂  Auf dem Desktop lassen", lambda: self.keep(ids))
        m.addSeparator()
        if one:
            m.addAction("▶  Öffnen", lambda: self.open_item(one.path))
            m.addAction("↗  Dateispeicherort öffnen", lambda: reveal_path(str(one.path)))
            if one.kind == "folder" and self.fences:
                m.addAction("📁  Mit Ordner-Assistent aufräumen …",
                            lambda: self.fences.open_organizer(one.path))
            if one.category != "Sonstiges":
                m.addAction(f"★  Als Regel für „{one.category}“ merken …",
                            lambda: self.remember_rule(one))
        web = [i for i in its if i in research_targets(its)]
        if web:
            act = m.addAction("🔎  Erneut recherchieren", lambda: self.start_research(web, force=True))
            act.setEnabled(not self.research_running())
        m.addSeparator()
        m.addAction("🗑  In den Papierkorb …", lambda: self.trash_items(ids))
        m.exec(pos)

    def path_menu(self, path: str, pos):
        """Rechtsklick auf etwas, das schon in einer Box liegt."""
        m = glass_menu(self)
        m.addAction("▶  Öffnen", lambda: self.open_item(path))
        m.addAction("↗  Dateispeicherort öffnen", lambda: reveal_path(path))
        if self.fences:
            m.addAction("⌂  Zurück auf den Desktop", lambda: self.back_to_desktop([path]))
        m.addSeparator()
        m.addAction("🗑  In den Papierkorb …", lambda: self.trash_paths([Path(path)]))
        m.exec(pos)

    def back_to_desktop(self, paths):
        self.fences.to_desktop(paths)
        self.rescan()

    # ---------- Papierkorb ----------
    def trash_selected(self):
        ids = self.desk_list.ids(self.desk_list.selectedItems())
        ids += self.detail_list.ids(self.detail_list.selectedItems())
        ids = [i for i in dict.fromkeys(ids) if i >= 0]
        if not ids:
            return self.message("Papierkorb", "Erst Objekte markieren – oder sie auf die Karte "
                                              "„Papierkorb“ ziehen.")
        self.trash_items(ids)

    def trash_items(self, ids):
        its = [self.items[i] for i in dict.fromkeys(ids) if 0 <= i < len(self.items)]
        if its:
            self.trash_paths([it.path for it in its])

    def trash_paths(self, paths: list[Path]):
        """Nach Rückfrage in den Papierkorb – nie endgültig löschen. Warnt bei
        Programmordnern, wichtigen Dokumenten und geschützten Dateien."""
        paths = [Path(p) for p in paths if Path(p).exists() or Path(p).is_symlink()]
        if not paths:
            return
        by_path = {str(it.path): it for it in self.items}
        protected = set(self.fences.cfg.get("protected", [])) if self.fences else set()
        warn_prog, warn_important = [], []
        for p in paths:
            it = by_path.get(str(p))
            role = it.role if it else (folder_role(p)[0] if p.is_dir() else "")
            if role in RESEARCH_LEAVE:
                warn_prog.append(p.name)
            if str(p) in protected:
                warn_important.append(f"{display_name(p)} (von dir als wichtig geschützt)")
                continue
            if p.is_file() and p.suffix.lower() not in (".lnk", ".url"):
                cat = org_classify_document(p.name, "", org_kind(p))[0]
                if cat and ORG_CATEGORIES[cat]["tier"] == ORG_PROTECT:
                    warn_important.append(f"{p.name} ({ORG_CATEGORIES[cat]['label']})")
        names = "\n".join(f"• {display_name(p)}" for p in paths[:10])
        if len(paths) > 10:
            names += f"\n… und {len(paths) - 10} weitere"
        text = (f"{len(paths)} Objekt(e) in den Papierkorb verschieben?\n\n{names}\n\n"
                f"Aus dem Papierkorb jederzeit wiederherstellbar.")
        if warn_prog:
            text += ("\n\n⚠ Programm-/Projektordner: " + ", ".join(warn_prog) +
                     "\nProgramme besser über Einstellungen → Apps deinstallieren – sonst "
                     "bleiben Registry-Einträge und Verknüpfungen ins Leere zurück.")
        if warn_important:
            text += "\n\n⚠ Wirkt wichtig:\n" + "\n".join(f"• {w}" for w in warn_important[:8])
        if not self.confirm("In den Papierkorb", text):
            return
        failed = [display_name(p) for p in paths if not safe_trash(p)]
        if failed:
            self.message("Papierkorb", f"{len(failed)} Objekt(e) nicht verschoben – "
                         f"{TRASH_FAIL_HINT}. Auf dem öffentlichen Desktop sind "
                         f"Administratorrechte nötig.\n\n" + "\n".join(failed[:15]), warn=True)
        if len(failed) < len(paths):
            self.subtitle.setText(f"{len(paths) - len(failed)} Objekt(e) im Papierkorb")
            self.rescan()

    # ---------- Boxen ----------
    def new_box(self):
        self.fences.new_box()
        self.refresh()

    def toggle_boxes(self):
        self.fences.set_visible(not self.fences.visible)

    def _update_vis_button(self, visible: bool):
        self.b_vis.setText("◐  Ausblenden" if visible else "◑  Einblenden")
        self.b_vis.updateGeometry()

    def _menu_below(self, menu: QMenu, button: QWidget):
        menu.exec(button.mapToGlobal(QPoint(0, button.height() + 4)))

    def show_organizer_menu(self):
        self._menu_below(self.fences.organizer_menu(self), self.b_org)

    def show_more_menu(self):
        f = self.fences
        m = glass_menu(self)
        m.addAction("+  Neue Ordner-Box …", lambda: (f.new_portal(), self.refresh()))
        m.addMenu(f.layouts_menu(m))
        m.addMenu(f.settings_menu(m))
        if self.args.rules:
            m.addAction("✎  Eigene Regeln bearbeiten (regeln.toml)",
                        lambda: self.open_item(self.args.rules))
        m.addSeparator()
        m.addAction("⏻  Desktop Sorter beenden", QApplication.quit)
        self._menu_below(m, self.b_more)

    # ---------- Recherche in offenen Quellen ----------
    def research_settings(self) -> tuple[bool, list[str]]:
        return research_settings(self.fences.cfg if self.fences else {"research": self._research_cfg})

    def _set_research(self, on: bool, sources: list[str]):
        cfg = {"on": on, "sources": [s for s in RESEARCH_SOURCES if s in sources]}
        if self.fences:
            self.fences.cfg["research"] = cfg
            self.fences.write_cfg()
        else:
            self._research_cfg = cfg

    def research_running(self) -> bool:
        """Läuft – oder das Ergebnis ist noch nicht zugestellt."""
        return self._research is not None

    def show_research_menu(self):
        on, srcs = self.research_settings()
        m = glass_menu(self)
        m.setToolTipsVisible(True)
        auto = m.addAction("✓  Immer prüfen – bei jedem Scan und vor dem Sortieren")
        auto.setCheckable(True)
        auto.setChecked(on)
        auto.toggled.connect(lambda v: self.reset_research(v, srcs))
        again = m.addAction("↻  Alles jetzt neu prüfen (ohne Zwischenspeicher)",
                            lambda: self.start_research(force=True))
        again.setEnabled(on and bool(srcs) and not self.research_running())
        m.addSeparator()
        head = m.addAction("Quellen")
        head.setEnabled(False)
        for key, (name, host, _pause, what) in RESEARCH_SOURCES.items():
            act = m.addAction(f"{name}  ·  {host}")
            act.setCheckable(True)
            act.setChecked(key in srcs)
            act.setToolTip(what)
            act.toggled.connect(lambda v, k=key: self.reset_research(
                on, [s for s in RESEARCH_SOURCES if (s in srcs and s != k) or (s == k and v)]))
        m.addSeparator()
        m.addAction("🗑  Zwischenspeicher leeren", self.clear_research_cache)
        note = m.addAction("Gesendet werden nur Namen von Verknüpfungen, Programmen und "
                           "Ordnern – nie Dokumente, Pfade oder Inhalte.")
        note.setEnabled(False)
        self._menu_below(m, self.b_research)

    def reset_research(self, on: bool, sources: list[str]):
        """Einstellung geändert: frisch scannen, dann mit den neuen Quellen prüfen."""
        self._set_research(on, sources)
        if self._research:
            self._research.cancel = True
        self.research_results.clear()
        self.rescan()

    def clear_research_cache(self):
        with _RESEARCH_LOCK:
            try:
                RESEARCH_CACHE.unlink()
            except OSError:
                pass
        on, srcs = self.research_settings()
        self.reset_research(on, srcs)

    def start_research(self, items=None, force=False):
        if self.research_running():
            if self._research.cancel:           # abgebrochener Lauf endet gleich
                self._research_next = (items, force)
            return
        on, srcs = self.research_settings()
        todo = list(items) if items is not None else research_targets(self.items)
        if not (on and srcs and todo):
            return self.update_research_label()
        self._research = ResearchJob(todo, srcs, self, force)
        self._research.progress.connect(self._research_progress)
        self._research.finished.connect(self._research_done)
        names = ", ".join(RESEARCH_SOURCES[s][0] for s in srcs)
        self.research_label.setText(f"🔎  Recherche läuft: {len(todo)} Namen in {names} …")
        self._research.start()

    def _research_progress(self, n: int, total: int, name: str):
        if self.research_running():
            self.research_label.setText(f"🔎  Recherche läuft: {n}/{total} Abfragen – „{name}“"
                                        + (" · sortiert wird danach" if self._apply_pending else ""))

    def _research_done(self, results: dict, status: dict):
        job, self._research = self._research, None
        if job and job.cancel:
            nxt, self._research_next = self._research_next, None
            if nxt:
                self.start_research(*nxt)
            return
        self.research_status = status
        self.research_results.update(results)
        if job and job.force:                   # frisch klassifizieren, dann neu anwenden
            return self.rescan()
        self.apply_research()
        if not (self._worker and self._worker.isRunning()):
            self.refresh()
        self.update_research_label()
        if self._apply_pending:
            self._apply_pending = False
            QTimer.singleShot(0, self.apply)

    def apply_research(self):
        """Ergebnisse auf frisch eingeordnete Objekte anwenden (jedes nur einmal)."""
        for it in self.items:
            res = self.research_results.get(str(it.path))
            if res is not None and not it.check:
                it.check = research_apply(it, res, self.memory.alias)

    def research_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for it in self.items:
            if it.check:
                counts[it.check] = counts.get(it.check, 0) + 1
        return counts

    def update_research_label(self):
        on, srcs = self.research_settings()
        if not (on and srcs):
            self.research_label.setText("🔎  Recherche aus – Zuordnung nur lokal "
                                        "(einschalten über „Recherche ▾“)")
            return
        if self.research_running():
            return
        c = self.research_counts()
        parts = [f"{c.get('confirmed', 0)} bestätigt", f"{c.get('corrected', 0)} ergänzt"]
        if c.get("conflict"):
            n = c["conflict"]
            parts.append(f"{n} {'Widerspruch' if n == 1 else 'Widersprüche'} (orange ?)")
        if c.get("hint"):
            parts.append(f"{c['hint']} {'Hinweis' if c['hint'] == 1 else 'Hinweise'}")
        none = c.get("none", 0) + c.get("found", 0)
        if none:
            parts.append(f"{none} ohne Zuordnung")
        text = f"🔎  Recherche ({len(srcs)} Quellen): " + " · ".join(parts)
        problems = research_status_text(getattr(self, "research_status", {}))
        if problems:
            text += f"  ·  ⚠ {problems}"
        self.research_label.setText(text)
        self.research_label.setToolTip(
            text + "\n\nGeprüft mit: " + ", ".join(RESEARCH_SOURCES[s][0] for s in srcs) +
            "\nDetails je Objekt im Tooltip; Einstellungen unter „Recherche ▾“.")

    def research_tip(self, it: Item) -> str:
        res = self.research_results.get(str(it.path))
        if res is None:
            return ""
        lines = research_lines(res)
        if not lines:
            return "\n\nRecherche: in keiner Quelle gefunden"
        return "\n\nRecherche:\n" + "\n".join(f"• {line}" for line in lines[:4])

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
        box = self.box_dirs().get(name)
        planned = [i for i, it in enumerate(self.items)
                   if it.category == name and it.confidence not in ("low", "keep")]
        fence = self.fences.fences.get(name) if self.fences else None
        if box is not None:
            m.addAction("↗  Box-Ordner öffnen", lambda: self.open_item(box))
            if fence:
                m.addAction("✎  Box umbenennen …", lambda: self.rename_box(name))
        else:
            m.addAction("✎  Umbenennen …", lambda: self.rename_group(name))
        if planned:
            m.addAction("⌂  Neue Objekte nicht einsortieren" if box is not None
                        else "⌂  Auflösen (alles auf dem Desktop lassen)",
                        lambda: self.keep(planned))
        if fence:
            m.addSeparator()
            m.addAction("⌂  Box auflösen (Inhalt zurück auf den Desktop) …",
                        lambda: (self.fences.dissolve(fence), self.rescan()))
        m.exec(pos)

    def rename_box(self, old):
        fence = self.fences.fences.get(old)
        if fence:
            self.fences.rename(fence)          # meldet sich über fences.renamed zurück

    def _box_renamed(self, old: str, new: str):
        """Box wurde umbenannt (hier oder auf dem Desktop): alles folgt dem neuen Namen."""
        for it in self.items:
            if it.category == old:
                it.category = new
        self.memory.renamed(old, new)
        self.memory.save()
        if self.selected == old:
            self.selected = new
        self.refresh()

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

    # ---------- Ausführen / Undo ----------
    def apply(self):
        if self.research_running():             # erst alles prüfen, dann sortieren
            self._apply_pending = True
            self.research_label.setText("🔎  Recherche läuft noch – sortiert wird, sobald "
                                        "alles geprüft ist …")
            return
        groups, _ = self.plan()
        n = sum(len(g) for g in groups.values())
        if not n:
            return self.message("Nichts zu tun", "Keine Objekte zum Einsortieren.")
        on, srcs = self.research_settings()
        c = self.research_counts()
        if on and srcs:
            check = (f"Geprüft mit {', '.join(RESEARCH_SOURCES[s][0] for s in srcs)}: "
                     f"{c.get('confirmed', 0)} bestätigt, {c.get('corrected', 0)} ergänzt"
                     + (f", {c['conflict']} Widersprüche bleiben zur Prüfung liegen"
                        if c.get("conflict") else ""))
            problems = research_status_text(getattr(self, "research_status", {}))
            if problems:
                check += f"\n⚠ Nicht vollständig: {problems}"
        else:
            check = "Recherche ist ausgeschaltet – nur lokal geprüft."
        if not self.confirm("In Boxen sortieren",
                            f"{n} Objekte in {len(groups)} Glas-Boxen auf dem Desktop "
                            f"einsortieren?\n\n{check}\n\nMit „Rückgängig“ jederzeit umkehrbar."):
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
            self.fences.mark_desktop_known()    # Zurückgelegtes nicht wieder einsortieren
            self.fences.sync()
        self.message("Rückgängig", out.getvalue().strip())
        self.rescan()

    def closeEvent(self, e):
        if self.keep_running:           # App läuft mit den Boxen im Infobereich weiter
            e.ignore()
            self.hide()
        else:
            if self._research:
                self._research.cancel = True
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
# Glas-Boxen auf dem Desktop — ein Stardock-Fences-Klon
# --------------------------------------------------------------------------- #
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
FENCE_RADIUS = 8                # = Windows-11-Fensterecken
TINTS = {"Glas": None, "Blau": "#0A84FF", "Lila": "#BF5AF2", "Grün": "#30D158",
         "Orange": "#FF9F0A", "Rot": "#FF453A", "Grau": "#8E8E93"}
OPACITY = {"Hell": (55, 0x28), "Normal": (110, 0x50), "Dunkel": (175, 0x90)}  # Füllung, Acrylic
ICON_SIZES = {"Klein": 32, "Mittel": 40, "Groß": 56}
SORTS = ("Name", "Typ", "Neueste zuerst")
BOX_DEFAULTS = {"tint": "Glas", "opacity": "Normal", "icons": "Mittel", "sort": "Name",
                "collapsed": False}
SETTINGS_DEFAULTS = {"autosort": True, "quickhide": True, "attach": True,
                     "hover_expand": True, "snap": True, "autostart": True,
                     "context_menu": True, "remind_downloads": True}
SETTING_LABELS = {
    "autosort": "Neue Verknüpfungen automatisch einsortieren",
    "quickhide": "Doppelklick auf den Desktop blendet Boxen aus",
    "hover_expand": "Eingeklappte Boxen beim Überfahren öffnen",
    "snap": "Boxen magnetisch ausrichten",
    "attach": "An den Desktop heften (bleibt bei Win + D)",
    "context_menu": "Im Explorer-Rechtsklick „Mit Desktop Sorter aufräumen“",
    "remind_downloads": "Wöchentlich an volle Downloads erinnern",
}
WINDOWS_ONLY_SETTINGS = {"attach", "context_menu"}
REMIND_EVERY_DAYS = 7
REMIND_MIN_FILES = 20           # so viele Dateien älter als 30 Tage lösen die Erinnerung aus
PORTAL_LIMIT = 400              # mehr Einträge zeigt eine Ordner-Box nicht an


# ---------- Windows-Anbindung (ctypes, ohne Zusatzpakete) ----------
class Win32:
    ok = os.name == "nt"

    @staticmethod
    def _u():
        import ctypes
        return ctypes.windll.user32

    @classmethod
    def acrylic(cls, hwnd: int, color: QColor | None, alpha: int) -> bool:
        """Acrylic-Blur hinter dem Fenster (Farbe + Alpha) und runde Ecken (Win11)."""
        if not cls.ok:
            return False
        import ctypes
        from ctypes import wintypes

        class ACCENT(ctypes.Structure):
            _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                        ("GradientColor", wintypes.DWORD), ("AnimationId", ctypes.c_int)]

        class WCA(ctypes.Structure):
            _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.POINTER(ACCENT)),
                        ("SizeOfData", ctypes.c_size_t)]

        c = color or QColor(20, 24, 32)
        abgr = (alpha << 24) | (c.blue() << 16) | (c.green() << 8) | c.red()
        try:
            accent = ACCENT(4, 0, abgr, 0)                       # ACRYLICBLURBEHIND
            data = WCA(19, ctypes.pointer(accent), ctypes.sizeof(accent))
            ok = bool(cls._u().SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))
            pref = ctypes.c_int(2)                               # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
            return ok
        except (AttributeError, OSError):
            return False

    @classmethod
    def attach_to_desktop(cls, hwnd: int, on: bool) -> None:
        """Progman als Besitzer setzen: „Desktop anzeigen“ (Win + D) blendet dann
        die Box nicht aus, sie bleibt aber ein eigenständiges Fenster (Acrylic)."""
        if not cls.ok:
            return
        import ctypes
        u = cls._u()
        u.FindWindowW.restype = ctypes.c_void_p
        u.SetWindowLongPtrW.restype = ctypes.c_void_p
        u.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        owner = u.FindWindowW("Progman", None) if on else None
        u.SetWindowLongPtrW(hwnd, -8, owner)                     # GWLP_HWNDPARENT

    @classmethod
    def left_button_down(cls) -> bool:
        if not cls.ok:
            return bool(QApplication.mouseButtons() & Qt.LeftButton)
        return bool(cls._u().GetAsyncKeyState(0x01) & 0x8000)

    @classmethod
    def desktop_listview_at(cls, x: int, y: int) -> int:
        """Handle der Desktop-Symbolliste unter dem Punkt, sonst 0."""
        if not cls.ok:
            return 0
        import ctypes
        from ctypes import wintypes
        u = cls._u()
        u.WindowFromPoint.restype = ctypes.c_void_p
        u.WindowFromPoint.argtypes = [wintypes.POINT]
        u.GetParent.restype = ctypes.c_void_p
        u.GetParent.argtypes = [ctypes.c_void_p]
        u.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        hwnd = u.WindowFromPoint(wintypes.POINT(x, y))

        def cls_name(h):
            buf = ctypes.create_unicode_buffer(64)
            u.GetClassNameW(h, buf, 64)
            return buf.value

        if hwnd and cls_name(hwnd) == "SysListView32":
            parent = u.GetParent(hwnd)
            if parent and cls_name(parent) == "SHELLDLL_DefView":
                return hwnd
        return 0

    @classmethod
    def selected_count(cls, listview: int) -> int:
        import ctypes
        u = cls._u()
        u.SendMessageW.restype = ctypes.c_ssize_t
        u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                   ctypes.c_ssize_t]
        return int(u.SendMessageW(listview, 0x1032, 0, 0))     # LVM_GETSELECTEDCOUNT


class DesktopDoubleClick(QObject):
    """Globaler Maus-Hook (WH_MOUSE_LL) in eigenem Thread; meldet Doppelklicks.
    Ob der Doppelklick auf eine freie Desktopfläche ging, prüft der Empfänger."""
    doubleClicked = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread_id = 0

    def start(self):
        if not Win32.ok or self._thread_id:
            return
        import threading
        threading.Thread(target=self._run, daemon=True, name="desktop-hook").start()

    def stop(self):
        if self._thread_id:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
            self._thread_id = 0

    def _run(self):
        import ctypes
        from ctypes import wintypes
        u, k = ctypes.windll.user32, ctypes.windll.kernel32

        class MSLL(ctypes.Structure):
            _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("extra", ctypes.c_size_t)]

        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        u.CallNextHookEx.restype = LRESULT
        u.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM,
                                     wintypes.LPARAM]
        u.SetWindowsHookExW.restype = ctypes.c_void_p
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
        k.GetModuleHandleW.restype = ctypes.c_void_p
        dbl_ms = u.GetDoubleClickTime()
        cx, cy = u.GetSystemMetrics(36), u.GetSystemMetrics(37)   # SM_CXDOUBLECLK/CY
        last = [0, 0, 0]

        def proc(code, wparam, lparam):
            if code == 0 and wparam == 0x0201:                      # WM_LBUTTONDOWN
                m = ctypes.cast(lparam, ctypes.POINTER(MSLL)).contents
                t, x, y = m.time, m.pt.x, m.pt.y
                if (t - last[0] <= dbl_ms and abs(x - last[1]) <= cx
                        and abs(y - last[2]) <= cy):
                    last[0] = 0
                    self.doubleClicked.emit(x, y)                   # → Qt-Hauptthread
                else:
                    last[:] = [t, x, y]
            return u.CallNextHookEx(None, code, wparam, lparam)

        self._proc = HOOKPROC(proc)                                 # Referenz halten!
        hook = u.SetWindowsHookExW(14, self._proc, k.GetModuleHandleW(None), 0)
        if not hook:
            return
        self._thread_id = k.GetCurrentThreadId()
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        u.UnhookWindowsHookEx(ctypes.c_void_p(hook))


def open_path(path: str) -> None:
    if os.name == "nt":
        os.startfile(path)              # startet .lnk/.url/.exe wie ein Doppelklick
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def trash_supported(path: str) -> bool:
    """Gibt es auf diesem Laufwerk einen Papierkorb? Unter Windows nur auf lokalen
    Festplatten – auf USB-Sticks und Netzlaufwerken würde „Papierkorb“ endgültig löschen."""
    if os.name != "nt":
        return True
    drive, _ = os.path.splitdrive(os.path.abspath(str(path)))
    if not drive or drive.startswith(("\\\\", "//")):          # UNC-Pfad = Netzwerk
        return False
    import ctypes
    return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 3   # DRIVE_FIXED


def safe_trash(path) -> bool:
    """In den Papierkorb – aber nie, wenn das Laufwerk keinen hat."""
    return trash_supported(str(path)) and bool(QFile.moveToTrash(str(path)))


TRASH_FAIL_HINT = ("in Benutzung, oder Laufwerk ohne Papierkorb (USB-Stick, Netzlaufwerk) – "
                   "dort bitte selbst löschen")


def reveal_path(path: str) -> None:
    if os.name == "nt":
        subprocess.Popen(["explorer", f"/select,{path}"], creationflags=NO_WINDOW)
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))


def display_name(p: Path) -> str:
    return p.stem if p.suffix.lower() in (".lnk", ".url") else p.name


def inspect_paths(paths: list[Path]) -> list[Item]:
    """Wie scan(), aber für einzelne neue Desktop-Verknüpfungen."""
    items = [Item(p, "user", "url" if p.suffix.lower() == ".url" else "lnk") for p in paths]
    lnks = resolve_lnks([i.path for i in items if i.kind == "lnk"])
    for it in items:
        if it.kind == "url":
            it.target = read_url_file(it.path)
        else:
            t, a = lnks.get(str(it.path), ("", ""))
            it.target = f"{t} {a}".strip()
    return items


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


def sync_context_menu(on: bool) -> None:
    """Explorer-Rechtsklick „Mit Desktop Sorter aufräumen“ (pro Benutzer, ohne Admin)
    eintragen bzw. entfernen – im Hintergrund, zeigt immer auf die aktuelle .pyw."""
    if not Win32.ok:
        return

    def work():
        try:
            if on:
                exe = Path(sys.executable)
                if exe.with_name("pythonw.exe").exists():
                    exe = exe.with_name("pythonw.exe")
                org_context_menu_install(str(exe), str(Path(__file__).resolve()))
            else:
                org_context_menu_uninstall()
        except Exception:                               # noqa: BLE001 — optionales Extra
            pass

    import threading
    threading.Thread(target=work, daemon=True).start()


# ---------- Programmsymbole ----------
def shell_image(path: str, size: int = 128) -> QImage | None:
    """Symbol in hoher Auflösung direkt von der Windows-Shell (wie im Explorer).
    Anders als QFileIconProvider liefert das für jede .url/.lnk ihr eigenes
    Symbol (Qt cacht pro Dateiendung → alle Steam-.url sähen gleich aus) und
    funktioniert auch für Store-Apps."""
    if not Win32.ok:
        return None
    import ctypes
    from ctypes import wintypes
    shell32, gdi32, user32 = ctypes.windll.shell32, ctypes.windll.gdi32, ctypes.windll.user32

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    class SIZE(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class BITMAP(ctypes.Structure):
        _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long),
                    ("bmHeight", ctypes.c_long), ("bmWidthBytes", ctypes.c_long),
                    ("bmPlanes", ctypes.c_ushort), ("bmBitsPixel", ctypes.c_ushort),
                    ("bmBits", ctypes.c_void_p)]

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_ushort),
                    ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]

    import uuid
    u = uuid.UUID("bcc18b79-ba16-442f-80c4-8a59c30c463b")    # IShellItemImageFactory
    iid = GUID(u.fields[0], u.fields[1], u.fields[2],
               (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
    shell32.SHCreateItemFromParsingName.argtypes = [
        ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    factory = ctypes.c_void_p()
    if shell32.SHCreateItemFromParsingName(path, None, ctypes.byref(iid),
                                           ctypes.byref(factory)) != 0 or not factory:
        return None
    vtbl = ctypes.cast(factory, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    get_image = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, SIZE, ctypes.c_int,
                                   ctypes.POINTER(ctypes.c_void_p))(vtbl[3])
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])
    hbmp = ctypes.c_void_p()
    try:
        hr = get_image(factory, SIZE(size, size), 0x4, ctypes.byref(hbmp))   # SIIGBF_ICONONLY
    finally:
        release(factory)
    if hr != 0 or not hbmp:
        return None

    gdi32.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    try:
        bm = BITMAP()
        gdi32.GetObjectW(hbmp, ctypes.sizeof(bm), ctypes.byref(bm))
        w, h = bm.bmWidth, abs(bm.bmHeight)
        if w <= 0 or h <= 0:
            return None
        bih = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)  # 32 bpp, top-down
        buf = ctypes.create_string_buffer(w * h * 4)
        hdc = user32.GetDC(None)
        rows = gdi32.GetDIBits(hdc, hbmp, 0, h, buf, ctypes.byref(bih), 0)
        user32.ReleaseDC(None, hdc)
    finally:
        gdi32.DeleteObject(hbmp)
    if rows != h:
        return None
    return image_from_bgra(buf.raw, w, h)


def image_from_bgra(raw: bytes, w: int, h: int) -> QImage | None:
    """32-bpp-Pixel der Shell → QImage. Ältere Symbole haben keinen Alphakanal
    (alle Alpha-Bytes 0) – sie werden deckend übernommen statt unsichtbar."""
    if not raw.strip(b"\0"):
        return None                                         # komplett leer
    if not raw[3::4].strip(b"\0"):                          # kein Alphakanal
        return QImage(raw, w, h, w * 4, QImage.Format_RGB32).copy()
    img = QImage(raw, w, h, w * 4, QImage.Format_ARGB32_Premultiplied).copy()
    return _trim_icon(img)


def _trim_icon(img: QImage) -> QImage:
    """Kleines Symbol mitten in großer, transparenter Leinwand → zuschneiden."""
    probe = img.scaled(32, 32, Qt.IgnoreAspectRatio, Qt.FastTransformation)
    alpha = [[probe.pixelColor(x, y).alpha() for x in range(32)] for y in range(32)]
    if not any(a > 8 for row in alpha for a in row):
        return img
    xs = [x for x in range(32) if any(alpha[y][x] > 8 for y in range(32))]
    ys = [y for y in range(32) if any(alpha[y][x] > 8 for x in range(32))]
    span = max(xs[-1] - xs[0], ys[-1] - ys[0]) + 1
    if span >= 24:                                          # füllt die Fläche bereits
        return img
    sx, sy = img.width() / 32, img.height() / 32
    side = int(span * max(sx, sy)) + 4
    cx = int((xs[0] + xs[-1] + 1) / 2 * sx)
    cy = int((ys[0] + ys[-1] + 1) / 2 * sy)
    return img.copy(max(0, cx - side // 2), max(0, cy - side // 2), side, side)


class IconLoader(QThread):
    """Lädt Shell-Symbole im Hintergrund, damit Boxen sofort erscheinen."""
    loaded = Signal(str, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        import queue
        self.queue: "queue.Queue[str | None]" = queue.Queue()
        self.start()

    def request(self, path: str):
        self.queue.put(path)

    def stop(self):
        self.queue.put(None)
        self.wait(2000)

    def run(self):
        if Win32.ok:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)   # Shell-Aufrufe brauchen STA
        while True:
            path = self.queue.get()
            if path is None:
                break
            try:
                img = shell_image(path)
            except Exception:                               # noqa: BLE001 — Symbol optional
                img = None
            if img is not None and not img.isNull():
                self.loaded.emit(path, img)


class IconCache(QObject):
    """Gemeinsamer Symbol-Speicher: sofort ein Platzhalter (Qt bzw. Buchstabe),
    das scharfe Shell-Symbol folgt asynchron → Signal updated(path)."""
    updated = Signal(str)

    def __init__(self):
        super().__init__()
        self.provider = QFileIconProvider()
        self.cache: dict[str, QIcon] = {}
        self.loader = IconLoader(self) if Win32.ok else None
        if self.loader:
            self.loader.loaded.connect(self._on_loaded)
            QApplication.instance().aboutToQuit.connect(self.loader.stop)

    def get(self, path) -> QIcon:
        key = str(path)
        if key not in self.cache:
            ic = self.provider.icon(QFileInfo(key))
            if ic.isNull() or not ic.availableSizes():
                ic = letter_icon(display_name(Path(key)))
            self.cache[key] = ic
            if self.loader:
                self.loader.request(key)
        return self.cache[key]

    def _on_loaded(self, path: str, img: QImage):
        self.cache[path] = QIcon(QPixmap.fromImage(img))
        self.updated.emit(path)


_ICON_CACHE: IconCache | None = None


def icon_cache() -> IconCache:
    global _ICON_CACHE
    if _ICON_CACHE is None:
        _ICON_CACHE = IconCache()
    return _ICON_CACHE


def checked_menu(parent: QMenu, title: str, options, current, on_pick) -> QMenu:
    sub = glass_menu(parent, title)
    for opt in options:
        act = sub.addAction(opt, lambda o=opt: on_pick(o))
        act.setCheckable(True)
        act.setChecked(opt == current)
    parent.addMenu(sub)
    return sub


# ---------- Box ----------
class FenceList(QListWidget):
    """Symbolraster einer Box. Klick startet, Ziehen verschiebt die Datei
    (in eine andere Box oder – via Explorer – auf den Desktop)."""
    launched = Signal(str)
    filesDropped = Signal(list)
    menuRequested = Signal(str, QPoint)
    draggedOut = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("fence")
        self.setViewMode(QListView.IconMode)
        self.setMovement(QListView.Static)
        self.setResizeMode(QListView.Adjust)
        self.setWrapping(True)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.ElideRight)
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
        self.set_icon_size(40)

    def set_icon_size(self, px: int):
        self.icon_px = px
        self.setIconSize(QSize(px, px))
        self._regrid()

    def _regrid(self):
        # Spalten gleichmäßig über die Boxbreite verteilen; Platz für eine
        # evtl. eingeblendete Scrollleiste (8 px) ist reserviert.
        w = max(1, self.width() - 2 * self.frameWidth() - 12)
        min_cell = self.icon_px + 40
        cols = max(1, w // min_cell)
        self.setGridSize(QSize(w // cols, self.icon_px + 50))

    def resizeEvent(self, e):
        self._regrid()
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
        path = it.data(Qt.UserRole)
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(path)])
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.setPixmap(it.icon().pixmap(48, 48))
        drag.setHotSpot(QPoint(24, 24))
        drag.exec(Qt.MoveAction | Qt.CopyAction, Qt.MoveAction)
        if not Path(path).exists():          # vom Explorer/Desktop übernommen
            self.draggedOut.emit(path)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and e.source() is not self:
            e.setDropAction(Qt.CopyAction)   # wir verschieben selbst; Quelle löscht nichts
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


class ResizeGrip(QWidget):
    """Sichtbarer Anfasser unten rechts: ziehen ändert Breite und Höhe der Box."""
    SIZE = 20

    def __init__(self, fence: "Fence"):
        super().__init__(fence)
        self.fence = fence
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.SizeFDiagCursor)
        self.setToolTip("Ziehen, um die Größe zu ändern")
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.fence.begin_resize(Qt.RightEdge | Qt.BottomEdge, e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        self.fence.drag_resize(e.globalPosition().toPoint())

    def mouseReleaseEvent(self, e):
        self.fence.end_resize()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 230 if self._hover else 130), 1.6,
                      Qt.SolidLine, Qt.RoundCap))
        s = self.SIZE - 5                                   # drei diagonale Linien
        for k in (4, 8, 12):
            p.drawLine(QPointF(s - k, s), QPointF(s, s - k))


class Fence(QWidget):
    """Eine Glas-Box auf dem Desktop."""
    TITLE = 34
    EDGE = 7                    # Breite der greifbaren Ränder rechts/unten

    def __init__(self, mgr: "FenceManager", name: str, folder: Path, portal: bool):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnBottomHint)
        self.mgr, self.name, self.folder, self.portal = mgr, name, folder, portal
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.setWindowTitle(name)
        self.count, self.full_h, self.peek = 0, 250, False
        self._acrylic = False
        self._visual_collapsed = False
        self._resize: tuple | None = None    # (Kanten, Start-Mausposition, Start-Geometrie)
        self.setMinimumSize(170, 80)         # eingeklappt: Höhe = Titelleiste
        self.setMouseTracking(True)          # Mauszeiger an den Rändern anpassen

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, self.TITLE + 2, self.EDGE, self.EDGE + 4)
        self.list = FenceList(self)
        self.list.launched.connect(mgr.launch)
        self.list.filesDropped.connect(lambda ps: mgr.move_to(ps, self.name))
        self.list.menuRequested.connect(lambda p, pos: mgr.item_menu(self, p, pos))
        self.list.draggedOut.connect(mgr.note_returned)
        lay.addWidget(self.list)
        self.grip = ResizeGrip(self)
        self.grip.raise_()
        mgr.icons.updated.connect(self.update_icon)

        self._save = QTimer(self, singleShot=True, interval=400)
        self._save.timeout.connect(lambda: mgr.save_geometry(self))
        self._snap = QTimer(self, singleShot=True, interval=220)
        self._snap.timeout.connect(self._snap_when_released)
        self._unpeek = QTimer(self, singleShot=True, interval=450)
        self._unpeek.timeout.connect(self._end_peek)
        self.apply_style()

    # ----- Einstellungen dieser Box -----
    @property
    def st(self) -> dict:
        return self.mgr.box_cfg(self.name)

    def apply_style(self):
        self.list.set_icon_size(ICON_SIZES.get(self.st["icons"], 40))
        if self._acrylic or self.isVisible():
            self._apply_acrylic()
        self.update()

    def _apply_acrylic(self):
        tint = TINTS.get(self.st["tint"])
        alpha = OPACITY.get(self.st["opacity"], OPACITY["Normal"])[1]
        self._acrylic = Win32.acrylic(int(self.winId()), QColor(tint) if tint else None, alpha)

    # ----- Inhalt -----
    def set_items(self, paths: list[Path]):
        mode = self.st["sort"]
        if mode == "Typ":
            key = lambda q: (q.suffix.lower(), display_name(q).lower())
        elif mode == "Neueste zuerst":
            key = lambda q: -q.stat().st_mtime if q.exists() else 0
        else:
            key = lambda q: display_name(q).lower()
        self.list.clear()
        for p in sorted(paths, key=key):
            it = QListWidgetItem(self.mgr.icons.get(p), display_name(p))
            it.setData(Qt.UserRole, str(p))
            it.setToolTip(f"{display_name(p)}\nKlicken zum Öffnen")
            self.list.addItem(it)
        self.count = len(paths)
        self.update()

    def update_icon(self, path: str):
        """Scharfes Shell-Symbol ist nachgeladen → Platzhalter ersetzen."""
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole) == path:
                it.setIcon(self.mgr.icons.get(path))

    # ----- Darstellung -----
    def showEvent(self, e):
        super().showEvent(e)
        if not self._acrylic:
            self._apply_acrylic()
        Win32.attach_to_desktop(int(self.winId()), self.mgr.settings["attach"])

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        tint = TINTS.get(self.st["tint"])
        fill_a = OPACITY.get(self.st["opacity"], OPACITY["Normal"])[0]
        base = QColor(tint) if tint else QColor(16, 18, 28)
        base.setAlpha(int(fill_a * (0.35 if self._acrylic else 1.0)))
        path = QPainterPath()
        path.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), FENCE_RADIUS, FENCE_RADIUS)
        p.fillPath(path, base)
        paint_glass(p, r, FENCE_RADIUS, strength=0.9)

        f = self.font()
        f.setPixelSize(13)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(Qt.white)
        title = ("↗ " if self.portal else "") + self.name
        tr = QRectF(14, 0, self.width() - 64, self.TITLE)
        p.drawText(tr, Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(title, Qt.ElideRight, int(tr.width())))
        f.setWeight(QFont.Normal)
        f.setPixelSize(11)
        p.setFont(f)
        p.setPen(TEXT_2)
        p.drawText(QRectF(self.width() - 50, 0, 36, self.TITLE), Qt.AlignVCenter | Qt.AlignRight,
                   f"{self.count}" + (" ▸" if self._visual_collapsed else ""))
        if not self._visual_collapsed:
            p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            p.drawLine(QPointF(12, self.TITLE), QPointF(self.width() - 12, self.TITLE))

    def resizeEvent(self, e):
        self.grip.move(self.width() - self.grip.width() - 1, self.height() - self.grip.height() - 1)
        self.grip.raise_()
        if not self._visual_collapsed:
            self.full_h = self.height()
        self._save.start()

    def moveEvent(self, e):
        self._save.start()
        if self.mgr.settings["snap"]:
            self._snap.start()

    def _snap_when_released(self):
        if Win32.left_button_down():            # Nutzer zieht noch
            self._snap.start()
        else:
            self.mgr.snap(self)

    # ----- Größe ändern (Anfasser unten rechts, rechter und unterer Rand) -----
    def _edges_at(self, pos):
        x, y = pos.x(), pos.y()
        edges = Qt.Edge(0)
        if x >= self.width() - self.EDGE:
            edges |= Qt.RightEdge
        if y >= self.height() - self.EDGE and not self._visual_collapsed:
            edges |= Qt.BottomEdge
        return edges

    def begin_resize(self, edges, global_pos: QPoint):
        if self._visual_collapsed:
            edges &= ~Qt.BottomEdge
        if not edges:
            return
        if self.peek:                      # beim Ziehen nicht wieder einklappen
            self.set_collapsed(False)
        if self.windowHandle().startSystemResize(edges):
            return                         # Windows übernimmt (flüssig, mit Snap)
        self._resize = (edges, global_pos, QRect(self.geometry()))

    def drag_resize(self, global_pos: QPoint):
        if not self._resize:
            return
        edges, start, geo = self._resize
        d = global_pos - start
        w = max(self.minimumWidth(), geo.width() + (d.x() if edges & Qt.RightEdge else 0))
        h = max(self.minimumHeight(), geo.height() + (d.y() if edges & Qt.BottomEdge else 0))
        self.resize(w, h)

    def end_resize(self):
        if self._resize:
            self._resize = None
            self.mgr.save_geometry(self)

    # ----- Maus: Titelleiste + Ränder -----
    def _on_title(self, e) -> bool:
        return e.position().y() <= self.TITLE

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        edges = self._edges_at(e.position().toPoint())
        if edges:
            self.begin_resize(edges, e.globalPosition().toPoint())
        elif self._on_title(e):
            if not self.windowHandle().startSystemMove():      # Fallback: manuell ziehen
                self._drag_from = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._resize:
            self.drag_resize(e.globalPosition().toPoint())
            return
        if getattr(self, "_drag_from", None) is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_from)
            return
        edges = self._edges_at(e.position().toPoint())      # nur Zeiger anpassen
        if edges == (Qt.RightEdge | Qt.BottomEdge):
            self.setCursor(Qt.SizeFDiagCursor)
        elif edges & Qt.RightEdge:
            self.setCursor(Qt.SizeHorCursor)
        elif edges & Qt.BottomEdge:
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, e):
        self.end_resize()
        if getattr(self, "_drag_from", None) is not None:
            self._drag_from = None
            if self.mgr.settings["snap"]:
                self.mgr.snap(self)

    def mouseDoubleClickEvent(self, e):
        if self._on_title(e):
            self.set_collapsed(not self.st["collapsed"])

    def contextMenuEvent(self, e):
        if e.pos().y() <= self.TITLE:
            self.mgr.fence_menu(self, e.globalPos())

    # ----- Einklappen & Aufklappen beim Überfahren -----
    def _show_collapsed(self, on: bool):
        if on == self._visual_collapsed:
            return
        if on:
            self.full_h = self.height()
        target = self.TITLE if on else self.full_h   # vor setMinimumHeight merken: das
        self._visual_collapsed = on                  # löst ein resizeEvent aus, das
        self.list.setVisible(not on)                 # full_h sonst auf 80 setzen würde
        self.grip.setVisible(not on)
        self.setMinimumHeight(self.TITLE if on else 80)
        self.resize(self.width(), target)
        self.update()

    def set_collapsed(self, on: bool, save=True):
        self.peek = False
        self.st["collapsed"] = on
        self._show_collapsed(on)
        if save:
            self._save.start()

    def enterEvent(self, e):
        self._unpeek.stop()
        if self.st["collapsed"] and self.mgr.settings["hover_expand"] and not self.peek:
            self.peek = True
            self._show_collapsed(False)

    def leaveEvent(self, e):
        if self.peek:
            self._unpeek.start()

    def _end_peek(self):
        if self.peek and not self.underMouse():
            self.peek = False
            self._show_collapsed(True)

    # ----- Drop auf Titelleiste/Rand -----
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.setDropAction(Qt.CopyAction)
            e.accept()
            self.enterEvent(e)                  # eingeklappte Box beim Ziehen öffnen

    def dragLeaveEvent(self, e):
        self.leaveEvent(e)

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        e.setDropAction(Qt.CopyAction)
        e.accept()
        if paths:
            self.mgr.move_to(paths, self.name)


# ---------- Verwaltung aller Boxen ----------
class FenceManager(QObject):
    """Eine Box pro Unterordner von STORAGE_DIR plus Ordner-Portale; folgt Änderungen
    live, sortiert neue Desktop-Verknüpfungen ein und reagiert auf Quick-Hide."""
    openManager = Signal()
    visibilityChanged = Signal(bool)
    notify = Signal(str, str)
    renamed = Signal(str, str)          # Box umbenannt (alt, neu)

    def __init__(self, storage: Path, rules_path: Path | None = None,
                 memory_path: Path | None = None):
        super().__init__()
        self.storage = storage
        self.storage.mkdir(parents=True, exist_ok=True)
        self.rules_path, self.memory_path = rules_path, memory_path
        self.cfg_path = storage / ".boxen.json"
        self.cfg = self._load_cfg()
        self.fences: dict[str, Fence] = {}
        self.icons = icon_cache()
        self.visible = True
        self._anims = []
        self._worker: Worker | None = None

        self.watcher = QFileSystemWatcher(self)
        self._sync = QTimer(self, singleShot=True, interval=300)
        self._sync.timeout.connect(self.sync)
        self.watcher.directoryChanged.connect(lambda _: self._sync.start())

        # Neue Desktop-Verknüpfungen erkennen
        self.returned: set[str] = set()
        self.desktops: list[Path] = []
        self.desk_known: set[str] = set()
        self.desk_watch = QFileSystemWatcher(self)
        self._auto = QTimer(self, singleShot=True, interval=2000)
        self._auto.timeout.connect(self._autosort)
        self.desk_watch.directoryChanged.connect(lambda _: self._auto.start())
        self.watch_desktop()

        # Quick-Hide: Doppelklick auf freie Desktopfläche
        self.hook = DesktopDoubleClick(self)
        self.hook.doubleClicked.connect(self._desktop_double_click)
        if self.settings["quickhide"]:
            self.hook.start()

        # Bildschirmänderungen (Auflösung, Monitor ab-/angesteckt)
        app = QApplication.instance()
        app.screenAdded.connect(lambda _: QTimer.singleShot(500, self.ensure_on_screen))
        app.screenRemoved.connect(lambda _: QTimer.singleShot(500, self.ensure_on_screen))
        app.primaryScreen().availableGeometryChanged.connect(
            lambda _: QTimer.singleShot(500, self.ensure_on_screen))

    # ----- Konfiguration -----
    def _load_cfg(self) -> dict:
        try:
            raw = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if raw and "boxes" not in raw:            # altes Format: nur Geometrien
            raw = {"boxes": raw}
        raw.setdefault("boxes", {})
        raw.setdefault("snapshots", {})
        raw["settings"] = {**SETTINGS_DEFAULTS, **raw.get("settings", {})}
        return raw

    def write_cfg(self):
        try:
            self.cfg_path.write_text(json.dumps(self.cfg, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
        except OSError:
            pass

    @property
    def settings(self) -> dict:
        return self.cfg["settings"]

    def box_cfg(self, name: str) -> dict:
        box = self.cfg["boxes"].setdefault(name, {})
        for k, v in BOX_DEFAULTS.items():
            box.setdefault(k, v)
        return box

    def set_setting(self, key: str, value: bool):
        self.settings[key] = value
        self.write_cfg()
        if key == "autostart":
            set_autostart(value)
        elif key == "context_menu":
            sync_context_menu(value)
        elif key == "quickhide":
            self.hook.start() if value else self.hook.stop()
        elif key == "attach":
            for f in self.fences.values():
                Win32.attach_to_desktop(int(f.winId()), value)
        elif key == "hover_expand" and not value:
            for f in self.fences.values():
                f._end_peek()

    # ----- Abgleich mit dem Dateisystem -----
    def group_dirs(self) -> dict[str, Path]:
        """Box-Ordner (ohne Portale) — Ziele fürs Einsortieren."""
        if not self.storage.is_dir():
            return {}
        return {d.name: d for d in sorted(self.storage.iterdir())
                if d.is_dir() and not d.name.startswith(".")}

    def sources(self) -> dict[str, tuple[Path, bool]]:
        src = {n: (d, False) for n, d in self.group_dirs().items()}
        for name, box in self.cfg["boxes"].items():
            portal = box.get("portal")
            if portal and Path(portal).is_dir() and name not in src:
                src[name] = (Path(portal), True)
        return src

    def names(self) -> list[str]:
        return list(self.group_dirs())

    def sync(self):
        src = self.sources()
        for name in [n for n in self.fences if n not in src]:
            self.fences.pop(name).deleteLater()
        for i, (name, (folder, portal)) in enumerate(src.items()):
            fence = self.fences.get(name)
            if fence is None or fence.folder != folder:
                if fence:
                    fence.deleteLater()
                fence = self.fences[name] = Fence(self, name, folder, portal)
                self._place(fence)
                if self.visible:
                    fence.show()
            entries = [p for p in folder.iterdir()
                       if p.name.lower() != "desktop.ini" and not p.name.startswith(".")
                       and not (portal and is_hidden(p))]
            fence.set_items(entries[:PORTAL_LIMIT] if portal else entries)
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        self.watcher.addPaths([str(self.storage)] + [str(f) for f, _ in src.values()])

    def _place(self, fence: Fence):
        box = fence.st
        if "x" in box:
            rect = QRect(box["x"], box["y"], box["w"], box["h"])
            if any(s.availableGeometry().intersects(rect) for s in QApplication.screens()):
                fence.setGeometry(rect)
                fence.full_h = box["h"]
                if box["collapsed"]:
                    fence.set_collapsed(True, save=False)
                return
        fence.setGeometry(self._free_slot(fence))

    def _free_slot(self, fence: Fence) -> QRect:
        """Neue Box: von rechts oben spaltenweise nach links (Desktop-Symbole sitzen
        links), erste Stelle ohne Überlappung mit anderen Boxen."""
        screen = QApplication.primaryScreen().availableGeometry()
        w, h, gap = 360, 250, 16
        rows = max(1, (screen.height() - gap) // (h + gap))
        taken = [f.geometry() for f in self.fences.values() if f is not fence and f.isVisible()]
        for i in range(200):
            col, row = divmod(i, rows)
            r = QRect(screen.right() - gap - w - col * (w + gap),
                      screen.top() + gap + row * (h + gap), w, h)
            if r.left() < screen.left():
                break
            if not any(r.intersects(t) for t in taken):
                return r
        return QRect(screen.center().x() - w // 2, screen.center().y() - h // 2, w, h)

    def save_geometry(self, fence: Fence):
        if self.fences.get(fence.name) is not fence:
            return
        g = fence.geometry()
        fence.st.update({"x": g.x(), "y": g.y(), "w": g.width(),
                         "h": fence.full_h if fence._visual_collapsed else g.height()})
        self.write_cfg()

    def ensure_on_screen(self):
        for f in self.fences.values():
            if not any(s.availableGeometry().intersects(f.geometry())
                       for s in QApplication.screens()):
                f.setGeometry(self._free_slot(f))

    def arrange_all(self):
        for f in self.fences.values():
            f.move(-10000, -10000)
        for f in self.fences.values():
            r = self._free_slot(f)
            f.setGeometry(r.x(), r.y(), r.width(), f.height() if f.st["collapsed"] else r.height())
            f.full_h = r.height()

    # ----- Magnetisches Ausrichten -----
    def snap(self, fence: Fence, threshold=14, gap=10):
        g = fence.geometry()
        screen = (fence.screen() or QApplication.primaryScreen()).availableGeometry()
        others = [f.geometry() for f in self.fences.values() if f is not fence and f.isVisible()]
        xs = [(screen.left() + gap, "l"), (screen.right() - gap + 1, "r")]
        ys = [(screen.top() + gap, "t"), (screen.bottom() - gap + 1, "b")]
        for o in others:
            xs += [(o.right() + gap + 1, "l"), (o.left(), "l"), (o.left() - gap, "r"),
                   (o.right() + 1, "r")]
            ys += [(o.bottom() + gap + 1, "t"), (o.top(), "t"), (o.top() - gap, "b"),
                   (o.bottom() + 1, "b")]

        def best(edge_lo, edge_hi, cands):
            moves = [(t - edge_lo) if side in "lt" else (t - edge_hi) for t, side in cands]
            moves = [m for m in moves if abs(m) <= threshold]
            return min(moves, key=abs) if moves else 0

        dx = best(g.left(), g.right() + 1, xs)
        dy = best(g.top(), g.bottom() + 1, ys)
        if dx or dy:
            fence.move(g.x() + dx, g.y() + dy)

    # ----- Quick-Hide -----
    def _desktop_double_click(self, x: int, y: int):
        lv = Win32.desktop_listview_at(x, y)
        if lv:   # kurz warten: Explorer hebt die Auswahl beim Klick ins Leere auf
            QTimer.singleShot(90, lambda: Win32.selected_count(lv) == 0 and self.toggle_visible())

    def toggle_visible(self):
        self.set_visible(not self.visible)

    def set_visible(self, on: bool, animate=True):
        if on == self.visible:
            return
        self.visible = on
        self._anims.clear()
        for f in self.fences.values():
            if not animate:
                f.setVisible(on)
                continue
            if on:
                f.setWindowOpacity(0.0)
                f.show()
            anim = QPropertyAnimation(f, b"windowOpacity", self)
            anim.setDuration(160)
            anim.setStartValue(f.windowOpacity() if not on else 0.0)
            anim.setEndValue(1.0 if on else 0.0)
            if not on:
                anim.finished.connect(lambda f=f: (f.hide(), f.setWindowOpacity(1.0)))
            anim.start()
            self._anims.append(anim)
        self.visibilityChanged.emit(on)

    # ----- Automatisch einsortieren -----
    def watch_desktop(self):
        self.desktops = [d for d in desktop_dirs() if d.is_dir()]
        if self.desk_watch.directories():
            self.desk_watch.removePaths(self.desk_watch.directories())
        if self.desktops:
            self.desk_watch.addPaths([str(d) for d in self.desktops])
        self.mark_desktop_known()

    def _desktop_names(self) -> dict[str, Path]:
        out = {}
        for d in self.desktops:
            try:
                out.update({p.name: p for p in d.iterdir()})
            except OSError:
                pass
        return out

    def mark_desktop_known(self):
        """Aktuellen Desktop als bekannt markieren (nach Rückgängig, Auflösen …)."""
        self._auto.stop()
        self.desk_known = set(self._desktop_names())

    def note_returned(self, path: str):
        self.returned.add(Path(path).name)

    def _autosort(self):
        current = self._desktop_names()
        new = [p for n, p in current.items()
               if n not in self.desk_known and n not in self.returned]
        self.desk_known = set(current)
        if not self.settings["autosort"] or (self._worker and self._worker.isRunning()):
            return
        cands = [p for p in new if p.suffix.lower() in (".lnk", ".url") and not is_hidden(p)]
        targets = set(self.group_dirs())
        if not cands or not targets:
            return
        rules = load_rules(self.rules_path)
        memory = Memory(self.memory_path) if self.memory_path else None
        if memory:
            rules = rules + memory.rules()

        research_on, sources = research_settings(self.cfg)

        def job():
            items = inspect_paths(cands)
            for it in items:
                classify(it, rules, memory)
            if research_on and sources:          # auch hier: erst prüfen, dann einsortieren
                cache = research_cache_load()
                results, _ = research_run(research_targets(items), cache, sources=sources)
                research_cache_save(cache)
                for it in items:
                    research_apply(it, results.get(str(it.path)), memory.alias if memory else None)
            return [(str(it.path), it.category) for it in items
                    if it.confidence in ("high", "medium", "user") and it.category in targets]

        def done(pairs):
            for path, cat in pairs:
                if Path(path).exists():
                    self.move_to([path], cat, quiet=True)
                    self._message_action = None
                    self.notify.emit("Automatisch einsortiert",
                                     f"{display_name(Path(path))} → {cat}")

        self._worker = Worker(job, self)
        self._worker.done.connect(done)
        self._worker.start()

    # ----- Datei-Aktionen -----
    def launch(self, path: str):
        try:
            open_path(path)
        except OSError as e:
            QMessageBox.warning(None, "Desktop Sorter", f"Konnte nicht öffnen:\n{path}\n\n{e}")

    def _move(self, paths, folder: Path, quiet=False) -> list[str]:
        failed = []
        for src in map(Path, paths):
            if not src.exists() or src.parent == folder:
                continue
            try:
                shutil.move(str(src), str(unique_dest(folder, src.name)))
            except OSError as e:
                failed.append(f"{src.name}: {e.strerror or e}")
        if failed and not quiet:
            QMessageBox.warning(None, "Desktop Sorter",
                                "Nicht verschoben (öffentlicher Desktop braucht Adminrechte):\n\n"
                                + "\n".join(failed))
        self._sync.start()
        return failed

    def folder_of(self, name: str) -> Path:
        src = self.sources().get(name)
        return src[0] if src else self.storage / name

    def move_to(self, paths, name: str, quiet=False):
        folder = self.folder_of(name)
        folder.mkdir(parents=True, exist_ok=True)
        self._move(paths, folder, quiet)

    def to_desktop(self, paths):
        for p in paths:
            self.note_returned(p)
        self._move(paths, desktop_dirs()[0])

    # ----- Boxen anlegen / ändern -----
    def new_box(self, paths=()):
        name = ask_box_name("Neue Box", "")
        if name and name not in self.sources():
            (self.storage / name).mkdir(exist_ok=True)
            if paths:
                self.move_to(paths, name)
            self._sync.start()

    def new_portal(self):
        from PySide6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(None, "Ordner für die Ordner-Box wählen",
                                                  str(Path.home()))
        if not folder:
            return
        base = Path(folder).name or folder
        name, n = base, 2
        while name in self.sources():
            name, n = f"{base} ({n})", n + 1
        self.box_cfg(name)["portal"] = folder
        self.write_cfg()
        self.sync()

    def rename(self, fence: Fence):
        old = fence.name
        new = ask_box_name("Box umbenennen", old)
        if not new or new == old or new in self.sources():
            return
        if not fence.portal:
            try:
                (self.storage / old).rename(self.storage / new)
            except OSError as e:
                QMessageBox.warning(None, "Desktop Sorter", f"Umbenennen nicht möglich:\n{e}")
                return
            fence.folder = self.storage / new
        self.cfg["boxes"][new] = self.cfg["boxes"].pop(old, {})
        self.fences[new] = self.fences.pop(old)
        fence.name = new
        fence.setWindowTitle(new)
        fence.update()
        self.write_cfg()
        if not fence.portal and self.memory_path:   # künftige Zuordnungen folgen dem neuen Namen
            try:
                mem = Memory(self.memory_path)
                mem.renamed(old, new)
                mem.save()
            except (OSError, ValueError):
                pass
        self._sync.start()
        if not fence.portal:
            self.renamed.emit(old, new)

    def dissolve(self, fence: Fence):
        if fence.portal:
            if QMessageBox.question(None, "Ordner-Box entfernen",
                                    f"Ordner-Box „{fence.name}“ entfernen?\n"
                                    f"Der Ordner und seine Dateien bleiben unverändert.") \
                    == QMessageBox.Yes:
                self.cfg["boxes"].pop(fence.name, None)
                self.write_cfg()
                self.sync()
            return
        if QMessageBox.question(None, "Box auflösen",
                                f"Alle {fence.count} Objekte aus „{fence.name}“ zurück auf den "
                                f"Desktop legen und die Box entfernen?") != QMessageBox.Yes:
            return
        folder = self.storage / fence.name
        if not self._move([str(p) for p in folder.iterdir()], desktop_dirs()[0]):
            shutil.rmtree(folder, ignore_errors=True)
            self.cfg["boxes"].pop(fence.name, None)
            self.write_cfg()
        self.mark_desktop_known()
        self._sync.start()

    def set_box_option(self, fence: Fence, key: str, value):
        fence.st[key] = value
        self.write_cfg()
        fence.apply_style()
        if key == "sort":
            self._sync.start()

    # ----- Layouts -----
    def save_snapshot(self):
        dlg = QInputDialog()
        dlg.setWindowTitle("Layout speichern")
        dlg.setLabelText("Name des Layouts:")
        dlg.setTextValue(time.strftime("Layout %d.%m. %H:%M"))
        dlg.setWindowFlag(Qt.WindowStaysOnTopHint)
        if not dlg.exec() or not dlg.textValue().strip():
            return
        self.cfg["snapshots"][dlg.textValue().strip()] = {
            n: {k: f.st[k] for k in ("x", "y", "w", "h", "collapsed") if k in f.st}
            for n, f in self.fences.items()}
        self.write_cfg()

    def restore_snapshot(self, name: str):
        for box, geo in self.cfg["snapshots"].get(name, {}).items():
            f = self.fences.get(box)
            if not f or "x" not in geo:
                continue
            f.set_collapsed(False, save=False)
            f.setGeometry(geo["x"], geo["y"], geo["w"], geo["h"])
            f.full_h = geo["h"]
            f.set_collapsed(geo.get("collapsed", False))
        self.ensure_on_screen()

    def delete_snapshot(self, name: str):
        self.cfg["snapshots"].pop(name, None)
        self.write_cfg()

    # ----- Menüs -----
    def item_menu(self, fence: Fence, path: str, pos):
        m = glass_menu()
        m.addAction("▶  Öffnen", lambda: self.launch(path))
        m.addAction("↗  Dateispeicherort öffnen", lambda: reveal_path(path))
        sub = glass_menu(m, "Verschieben in")
        for n in self.sources():
            if n != fence.name:
                sub.addAction(n, lambda n=n: self.move_to([path], n))
        sub.addSeparator()
        sub.addAction("+  Neue Box …", lambda: self.new_box([path]))
        m.addMenu(sub)
        m.addAction("⌂  Auf den Desktop legen", lambda: self.to_desktop([path]))
        m.exec(pos)

    def fence_menu(self, fence: Fence, pos):
        m = glass_menu()
        m.addAction("▴  Ausklappen" if fence.st["collapsed"] else "▾  Einklappen",
                    lambda: fence.set_collapsed(not fence.st["collapsed"]))
        look = glass_menu(m, "Darstellung")
        checked_menu(look, "Farbe", TINTS, fence.st["tint"],
                     lambda v: self.set_box_option(fence, "tint", v))
        checked_menu(look, "Deckkraft", OPACITY, fence.st["opacity"],
                     lambda v: self.set_box_option(fence, "opacity", v))
        checked_menu(look, "Symbolgröße", ICON_SIZES, fence.st["icons"],
                     lambda v: self.set_box_option(fence, "icons", v))
        checked_menu(look, "Sortieren nach", SORTS, fence.st["sort"],
                     lambda v: self.set_box_option(fence, "sort", v))
        m.addMenu(look)
        m.addAction("✎  Umbenennen …", lambda: self.rename(fence))
        m.addAction("↗  Ordner im Explorer öffnen",
                    lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(fence.folder))))
        if fence.portal:
            m.addAction("🧹  Ordner aufräumen …", lambda: self.open_cleaner(fence.folder))
        m.addSeparator()
        m.addAction("+  Neue Box …", lambda: self.new_box())
        m.addAction("+  Neue Ordner-Box …", self.new_portal)
        m.addSeparator()
        m.addAction("⌂  Ordner-Box entfernen" if fence.portal
                    else "⌂  Box auflösen (alles auf den Desktop)", lambda: self.dissolve(fence))
        m.addAction("✦  Desktop Sorter öffnen", self.openManager.emit)
        m.exec(pos)

    def build_tray_menu(self, open_manager) -> QMenu:
        menu = glass_menu()
        menu.addAction("✦  Desktop Sorter öffnen", open_manager)
        act_vis = menu.addAction("Boxen anzeigen")
        act_vis.setCheckable(True)
        act_vis.setChecked(self.visible)
        act_vis.toggled.connect(lambda on: self.set_visible(on))
        self.visibilityChanged.connect(act_vis.setChecked)
        menu.addSeparator()
        menu.addAction("+  Neue Box …", lambda: self.new_box())
        menu.addAction("+  Neue Ordner-Box …", self.new_portal)
        menu.addAction("▦  Boxen neu anordnen", self.arrange_all)
        menu.addAction("🧹  Downloads aufräumen …", lambda: self.open_cleaner(downloads_dir()))
        menu.addMenu(self.organizer_menu(menu))
        menu.addMenu(self.layouts_menu(menu))
        menu.addMenu(self.settings_menu(menu))
        menu.addSeparator()
        menu.addAction("⏻  Beenden", QApplication.quit)
        return menu

    def organizer_menu(self, parent: QMenu | None = None) -> QMenu:
        org = glass_menu(parent, "📁  Ordner-Assistent")
        for label, key in (("Dokumente", "documents"), ("Downloads", "downloads"),
                           ("Bilder", "pictures"), ("Desktop", "desktop"),
                           ("Videos", "videos")):
            org.addAction(label, lambda k=key: self.open_organizer(org_known_folder(k)))
        org.addSeparator()
        org.addAction("Anderer Ordner …", self.pick_and_organize)
        return org

    def layouts_menu(self, parent: QMenu | None = None) -> QMenu:
        layouts = glass_menu(parent, "Layouts")
        layouts.aboutToShow.connect(lambda: self._fill_layouts(layouts))
        return layouts

    def settings_menu(self, parent: QMenu | None = None) -> QMenu:
        """Häkchen werden bei jedem Öffnen aus der Konfiguration gelesen."""
        prefs = glass_menu(parent, "Einstellungen")
        keys = [k for k in SETTING_LABELS if k not in WINDOWS_ONLY_SETTINGS or Win32.ok]
        keys += ["autostart"] if Win32.ok else []

        def fill():
            prefs.clear()
            for key in keys:
                if key == "autostart":
                    prefs.addSeparator()
                act = prefs.addAction(SETTING_LABELS.get(key, "Mit Windows starten"))
                act.setCheckable(True)
                act.setChecked(bool(self.settings[key]))
                act.toggled.connect(lambda on, k=key: self.set_setting(k, on))
        prefs.aboutToShow.connect(fill)
        fill()
        return prefs

    def open_cleaner(self, folder: Path):
        win = getattr(self, "_cleaner", None)
        if win is None or not win.isVisible():
            win = self._cleaner = CleanerWindow(self, folder)
        win.show()
        win.raise_()
        win.activateWindow()

    def open_organizer(self, folder: Path):
        """Ordner-Assistent für einen Ordner (ein Fenster je Ordner)."""
        folder = Path(folder)
        wins = [w for w in getattr(self, "_organizers", []) if w.isVisible()]
        for w in wins:
            if os.path.normcase(str(w.folder)) == os.path.normcase(str(folder)):
                break
        else:
            w = OrganizerWindow(self, folder)
            wins.append(w)
        self._organizers = wins
        w.show()
        w.showNormal()
        w.raise_()
        w.activateWindow()

    def pick_and_organize(self):
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(None, "Ordner aufräumen & sortieren",
                                             str(org_known_folder("documents")))
        if d:
            self.open_organizer(Path(d))

    # ----- Erinnerung: Downloads läuft voll -----
    def check_downloads_reminder(self):
        if not self.settings.get("remind_downloads"):
            return
        last = self.cfg.get("last_reminder", 0)
        if time.time() - last < REMIND_EVERY_DAYS * 86400:
            return
        folder = downloads_dir()
        cutoff = time.time() - 30 * 86400
        old, size = 0, 0
        try:
            for p in folder.iterdir():
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_mtime < cutoff and not p.name.lower() == "desktop.ini":
                    old += 1
                    size += st.st_size if p.is_file() else 0
        except OSError:
            return
        if old >= REMIND_MIN_FILES:
            self.cfg["last_reminder"] = time.time()
            self.write_cfg()
            self._message_action = lambda: self.open_cleaner(folder)
            self.notify.emit("Downloads aufräumen?",
                             f"{old} Dateien sind älter als 30 Tage ({human_size(size)}). "
                             f"Klicken zum Durchwischen.")

    def message_clicked(self):
        action = getattr(self, "_message_action", None)
        self._message_action = None
        if action:
            action()

    def _fill_layouts(self, menu: QMenu):
        menu.clear()
        menu.addAction("💾  Aktuelles Layout speichern …", self.save_snapshot)
        names = list(self.cfg["snapshots"])
        if names:
            menu.addSeparator()
            for n in names:
                menu.addAction(f"↺  {n}", lambda n=n: self.restore_snapshot(n))
            dele = glass_menu(menu, "Layout löschen")
            for n in names:
                dele.addAction(n, lambda n=n: self.delete_snapshot(n))
            menu.addMenu(dele)


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


# --------------------------------------------------------------------------- #
# Downloads aufräumen – Wischen wie bei Tinder
# --------------------------------------------------------------------------- #
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico", ".svg"}
TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".log", ".py", ".ini", ".cfg", ".xml",
            ".yaml", ".yml", ".toml", ".bat", ".ps1", ".sh", ".html", ".css", ".js"}
SWIPE_THRESHOLD = 130


def downloads_dir() -> Path:
    return known_folder("374DE290-123F-4565-9164-39C4925E467B", Path.home() / "Downloads")


def download_origin(path: Path) -> str:
    """Herkunft eines Downloads aus dem NTFS-Stream Zone.Identifier (HostUrl)."""
    if os.name != "nt" or path.is_dir():
        return ""
    try:
        with open(str(path) + ":Zone.Identifier", encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return ""
    m = re.search(r"^(?:HostUrl|ReferrerUrl)=(\S+)", text, re.M)
    if not m:
        return ""
    from urllib.parse import urlparse
    host = urlparse(m.group(1)).hostname or ""
    return host[4:] if host.startswith("www.") else host


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return ""


def path_size(p: Path, limit=20000) -> int:
    if not p.is_dir():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    total = 0
    for i, f in enumerate(p.rglob("*")):
        if i > limit:
            break
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def make_preview(path: Path, w: int, h: int) -> tuple[QPixmap | None, str]:
    """Vorschau: Bild, erste PDF-Seite oder Textanfang; sonst (None, "")."""
    ext = path.suffix.lower()
    try:
        if ext in IMAGE_EXT:
            from PySide6.QtGui import QImageReader
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid():
                reader.setScaledSize(size.scaled(w * 2, h * 2, Qt.KeepAspectRatio))
            img = reader.read()
            if not img.isNull():
                return QPixmap.fromImage(img), ""
        elif ext == ".pdf":
            from PySide6.QtPdf import QPdfDocument
            doc = QPdfDocument()
            doc.load(str(path))
            if doc.pageCount() > 0:
                ps = doc.pagePointSize(0)
                scale = min(w * 2 / max(ps.width(), 1), h * 2 / max(ps.height(), 1))
                img = doc.render(0, QSize(int(ps.width() * scale), int(ps.height() * scale)))
                if not img.isNull():                    # Seite ist transparent → auf Papier
                    page = QImage(img.size(), QImage.Format_RGB32)
                    page.fill(Qt.white)
                    pp = QPainter(page)
                    pp.drawImage(0, 0, img)
                    pp.end()
                    return QPixmap.fromImage(page), ""
        elif ext in TEXT_EXT and path.stat().st_size < 50_000_000:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return None, fh.read(1500)
    except Exception:                                   # noqa: BLE001 — Vorschau optional
        pass
    return None, ""


class SwipeCard(QWidget):
    """Karte mit Vorschau. Ziehen nach links = Papierkorb, rechts = behalten."""
    decided = Signal(str)                                # "trash" | "keep"
    CW, CH = 400, 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entry: dict | None = None
        self.next_entry: dict | None = None
        self.offset = 0.0
        self._press: QPoint | None = None
        self._anim = None
        self.done_text = ""
        self.confirm = None             # optional: fn(action, entry) -> bool
        self.setCursor(Qt.OpenHandCursor)
        icon_cache().updated.connect(lambda _: self.update())

    def set_entry(self, entry, next_entry):
        self.entry, self.next_entry, self.offset = entry, next_entry, 0.0
        self.update()

    def card_rect(self) -> QRectF:
        return QRectF((self.width() - self.CW) / 2, (self.height() - self.CH) / 2 - 6,
                      self.CW, self.CH)

    # ----- Wischen -----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.entry and not self._busy():
            self._press = e.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._press is not None:
            self.offset = e.position().x() - self._press.x()
            self.update()

    def mouseReleaseEvent(self, e):
        if self._press is None:
            return
        self._press = None
        self.setCursor(Qt.OpenHandCursor)
        if self.offset <= -SWIPE_THRESHOLD:
            self.fly("trash")
        elif self.offset >= SWIPE_THRESHOLD:
            self.fly("keep")
        else:
            self._animate(0.0, 180)

    def _busy(self) -> bool:
        return self._anim is not None and self._anim.state() == QVariantAnimation.Running

    def _animate(self, target: float, ms: int, then=None):
        from PySide6.QtCore import QEasingCurve
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(float(self.offset))
        self._anim.setEndValue(float(target))
        self._anim.setDuration(ms)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "offset", v), self.update()))
        if then:
            self._anim.finished.connect(then)
        self._anim.start()

    def fly(self, action: str):
        if not self.entry or self._busy():
            return
        if self.confirm and not self.confirm(action, self.entry):   # z. B. „wirklich löschen?“
            self._animate(0.0, 180)
            return
        target = (-1.0 if action == "trash" else 1.0) * (self.width() + self.CW)
        self._animate(target, 230, lambda: self.decided.emit(action))

    # ----- Zeichnen -----
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self.card_rect()
        if not self.entry:
            f = self.font()
            f.setPixelSize(22)
            f.setWeight(QFont.DemiBold)
            p.setFont(f)
            p.setPen(Qt.white)
            p.drawText(self.rect(), Qt.AlignCenter, self.done_text or "Keine Dateien")
            return
        progress = min(1.0, abs(self.offset) / SWIPE_THRESHOLD)
        if self.next_entry:                              # nächste Karte dahinter
            p.save()
            s = 0.94 + 0.06 * progress
            p.translate(r.center())
            p.scale(s, s)
            p.translate(-r.center() + QPointF(0, 18 * (1 - progress)))
            self._paint_card(p, r, self.next_entry, ghost=True)
            p.restore()
        p.save()
        p.translate(r.center() + QPointF(self.offset, 0))
        p.rotate(self.offset * 0.05)
        p.translate(-r.center())
        self._paint_card(p, r, self.entry)
        if self.offset:                                  # Stempel
            trash = self.offset < 0
            col = QColor("#FF453A" if trash else "#30D158")
            col.setAlpha(int(255 * progress))
            p.save()
            p.translate(r.left() + (r.width() - 40 if trash else 40), r.top() + 52)
            p.rotate(14 if trash else -14)
            f = self.font()
            f.setPixelSize(26)
            f.setWeight(QFont.Black)
            p.setFont(f)
            text = "PAPIERKORB" if trash else "BEHALTEN"
            tw = p.fontMetrics().horizontalAdvance(text) + 24
            box = QRectF(-tw if trash else 0, -22, tw, 44)
            p.setPen(QPen(col, 3.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(box, 8, 8)
            p.drawText(box, Qt.AlignCenter, text)
            p.restore()
        p.restore()

    def _paint_card(self, p: QPainter, r: QRectF, e: dict, ghost=False):
        path = QPainterPath()
        path.addRoundedRect(r, 26, 26)
        p.fillPath(path, QColor(18, 20, 30, 150 if ghost else 205))
        paint_glass(p, r, 26, strength=1.3)
        if ghost:
            return
        pv = QRectF(r.left() + 14, r.top() + 14, r.width() - 28, 300)
        clip = QPainterPath()
        clip.addRoundedRect(pv, 18, 18)
        p.fillPath(clip, QColor(0, 0, 0, 70))
        p.save()
        p.setClipPath(clip)
        pix, text = e.get("preview"), e.get("text")
        if pix:
            sz = pix.size().scaled(pv.size().toSize(), Qt.KeepAspectRatio)
            target = QRectF(pv.center().x() - sz.width() / 2, pv.center().y() - sz.height() / 2,
                            sz.width(), sz.height())
            p.drawPixmap(target.toRect(), pix)
        elif text:
            f = QFont("Consolas")
            f.setStyleHint(QFont.Monospace)
            f.setPixelSize(11)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255, 200))
            p.drawText(pv.adjusted(14, 12, -14, -12), Qt.AlignLeft | Qt.AlignTop | Qt.TextWrapAnywhere,
                       text)
        else:
            ic = icon_cache().get(e["path"])
            p.drawPixmap(QRectF(pv.center().x() - 64, pv.center().y() - 64, 128, 128).toRect(),
                         ic.pixmap(256, 256))
        p.restore()

        y = pv.bottom() + 16
        f = self.font()
        f.setPixelSize(17)
        f.setWeight(QFont.Bold)
        p.setFont(f)
        p.setPen(Qt.white)
        name_r = QRectF(r.left() + 22, y, r.width() - 44, 48)
        name = e["path"].name
        fm = p.fontMetrics()
        p.drawText(name_r, Qt.AlignLeft | Qt.AlignTop | Qt.TextWrapAnywhere,
                   fm.elidedText(name, Qt.ElideMiddle, int(name_r.width() * 1.9)))
        f.setPixelSize(13)
        f.setWeight(QFont.Normal)
        p.setFont(f)
        p.setPen(TEXT_2)
        kind = "Ordner" if e["path"].is_dir() else (e["path"].suffix.upper().lstrip(".") or "Datei")
        lines = [f"{kind}  ·  {human_size(e['size'])}",
                 "geladen am " + time.strftime("%d.%m.%Y, %H:%M", time.localtime(e["mtime"]))]
        if e.get("origin"):
            lines.append(f"von {e['origin']}")
        p.drawText(QRectF(r.left() + 22, y + 52, r.width() - 44, 80),
                   Qt.AlignLeft | Qt.AlignTop, "\n".join(lines))

        chip = org_chip(e.get("org"))           # Vorsortierung: Vorschlag + Grund
        if chip:
            text, color = chip
            f.setPixelSize(12)
            f.setWeight(QFont.DemiBold)
            p.setFont(f)
            fm = p.fontMetrics()
            text = fm.elidedText(text, Qt.ElideRight, int(r.width() - 72))
            cr = QRectF(r.left() + 22, r.bottom() - 46, fm.horizontalAdvance(text) + 26, 28)
            bg = QColor(color)
            bg.setAlpha(60)
            path = QPainterPath()
            path.addRoundedRect(cr, 14, 14)
            p.fillPath(path, bg)
            p.setPen(QPen(QColor(color), 1.2))
            p.drawPath(path)
            p.setPen(QColor(color).lighter(150))
            p.drawText(cr, Qt.AlignCenter, text)


def org_main_reason(f) -> str:
    """Wichtigster Grund zuerst: bei Löschvorschlägen das „Warum löschen“ (Kopie,
    installiert, Sperrdatei …), nicht die Kategorie-Erkennung."""
    reasons = list(getattr(f, "reasons", []) or [])
    if getattr(f, "dup_of", ""):
        hit = next((r for r in reasons if "Kopie" in r), None)
        return hit or f"Exakte Kopie von {Path(f.dup_of).name}"
    if getattr(f, "tier", "") in (ORG_JUNK, ORG_REVIEW):
        hit = next((r for r in reasons if not r.startswith(("Erkannt", "Kamera"))), None)
        if hit:
            return hit
    return reasons[0] if reasons else ORG_CATEGORIES.get(f.category, {}).get("label", "")


def org_chip(f) -> tuple[str, str] | None:
    """Text + Farbe des Vorsortier-Chips einer Karte (None = keine Analyse)."""
    if f is None:
        return None
    label = ORG_CATEGORIES.get(f.category, {}).get("label", "")
    reason = org_main_reason(f) or label
    if f.tier == ORG_JUNK:
        return f"Vorschlag: Papierkorb – {reason}", "#FF453A"
    if f.tier == ORG_PROTECT:
        return f"🔒 Wichtig – {label or reason}", "#30D158"
    if f.tier == ORG_KEEP:
        return f"Behalten – {label or reason}", "#64D2FF"
    return (f"{label}: {reason}" if label and reason != label else (reason or "Prüfen")), "#FFD60A"


class RoundButton(QAbstractButton):
    def __init__(self, glyph: str, color: str, size=64, tip="", parent=None):
        super().__init__(parent)
        self.glyph, self.color, self._hover = glyph, QColor(color), False
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setOpacity(1.0 if self.isEnabled() else 0.35)
        paint_glass(p, self.rect(), self.width() / 2, strength=2.0, tint=self.color,
                    hover=self._hover or self.isDown())
        f = self.font()
        f.setPixelSize(int(self.width() * 0.42))
        f.setWeight(QFont.Bold)
        p.setFont(f)
        p.setPen(self.color.lighter(150) if self._hover else self.color.lighter(125))
        p.drawText(self.rect(), Qt.AlignCenter, self.glyph)


CLEANER_SORTS = ("Vorsortiert", "Älteste zuerst", "Größte zuerst")
ORG_TIER_ORDER = {ORG_JUNK: 0, ORG_REVIEW: 1, ORG_KEEP: 2, ORG_PROTECT: 3}


class CleanerWindow(_Base):
    """Downloads (oder jeden anderen Ordner bzw. eine übergebene Dateiliste) Datei für
    Datei aufräumen. „Vorsortiert“: Müll-Vorschläge zuerst, Wichtiges zuletzt."""

    def __init__(self, mgr: "FenceManager", folder: Path, entries: list | None = None,
                 title: str | None = None, hints=None):
        super().__init__()
        self.mgr, self.folder = mgr, Path(folder)
        self.fixed = [Path(p) for p in entries] if entries is not None else None
        self.hints = hints                  # vorhandener OrgPlan (aus dem Ordner-Assistenten)
        self.title_text = title or "Aufräumen"
        self.decisions: list[tuple[dict, str]] = []     # (Eintrag, "trash"|"keep")
        self.queue: list[dict] = []
        self.idx = 0
        self.committed = False
        self._presort: OrgWorker | None = None
        self.setWindowTitle(self.title_text)
        self.setWindowIcon(app_icon())
        if FRAMELESS:
            self.setTitleBar(TitleBar(self))
            for b in (self.titleBar.minBtn, self.titleBar.maxBtn, self.titleBar.closeBtn):
                b.setNormalColor(QColor(255, 255, 255, 200))
                b.setHoverColor(Qt.white)
        if ACRYLIC:
            self.windowEffect.setAcrylicEffect(self.winId(), ACRYLIC_TINT)
        self._build()
        self.resize(620, 820)
        self.load()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 40 if FRAMELESS else 22, 22, 22)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel(self.title_text)
        title.setObjectName("h1")
        self.sub = QLabel("")
        self.sub.setObjectName("sub")
        titles.addWidget(title)
        titles.addWidget(self.sub)
        head.addLayout(titles)
        head.addStretch(1)
        self.b_folder = GlassButton("Ordner …", small=True)
        self.b_folder.clicked.connect(self.choose_folder)
        self.b_folder.setVisible(self.fixed is None)
        self.b_sort = GlassButton(CLEANER_SORTS[0], small=True)
        self.b_sort.setToolTip("Sortierung wechseln: Vorsortiert (Müll-Vorschläge zuerst) · "
                               "Älteste zuerst · Größte zuerst")
        self.b_sort.clicked.connect(self.toggle_sort)
        head.addWidget(self.b_folder)
        head.addWidget(self.b_sort)
        root.addLayout(head)

        opts = QHBoxLayout()
        self.t_again = Toggle("Behaltene erneut zeigen")
        self.t_again.toggled.connect(lambda _: self.load())
        self.t_again.setVisible(self.fixed is None)
        opts.addWidget(self.t_again)
        opts.addStretch(1)
        self.b_bulk = GlassButton("", small=True)
        self.b_bulk.setToolTip("Alle Karten mit rotem „Vorschlag: Papierkorb“ auf einmal "
                               "markieren (einzeln rückgängig machbar)")
        self.b_bulk.clicked.connect(self.trash_all_suggestions)
        self.b_bulk.hide()
        opts.addWidget(self.b_bulk)
        root.addLayout(opts)

        self.card = SwipeCard()
        self.card.decided.connect(self.decide)
        self.card.confirm = self._confirm_action
        root.addWidget(self.card, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.b_trash = RoundButton("✕", "#FF453A", 72, "In den Papierkorb  (← / Entf)")
        self.b_undo = RoundButton("↶", "#FFD60A", 50, "Rückgängig  (Strg+Z)")
        self.b_open = RoundButton("↗", "#64D2FF", 50, "Öffnen  (Enter)")
        self.b_keep = RoundButton("✓", "#30D158", 72, "Behalten  (→ / Leertaste)")
        self.b_trash.clicked.connect(lambda: self.card.fly("trash"))
        self.b_keep.clicked.connect(lambda: self.card.fly("keep"))
        self.b_undo.clicked.connect(self.undo)
        self.b_open.clicked.connect(self.open_current)
        for b in (self.b_trash, self.b_undo, self.b_open, self.b_keep):
            row.addWidget(b, 0, Qt.AlignVCenter)
            row.addSpacing(14)
        row.addStretch(1)
        root.addLayout(row)
        self.b_done = GlassButton("Fertig", primary=True)
        self.b_done.clicked.connect(self.close)
        root.addWidget(self.b_done, 0, Qt.AlignHCenter)
        self.setFocusPolicy(Qt.StrongFocus)

    def paintEvent(self, _):
        p = QPainter(self)
        paint_backdrop(p, self.rect(), overlay_only=ACRYLIC)

    # ----- Daten -----
    def _kept(self) -> dict:
        return self.mgr.cfg.setdefault("cleaner_keep", {}).setdefault(str(self.folder), {})

    def _kept_key(self, p: Path) -> str:
        try:
            return p.relative_to(self.folder).as_posix()
        except ValueError:
            return str(p)

    def load(self):
        kept = {} if (self.t_again.isChecked() or self.fixed is not None) else self._kept()
        protected = set(self.mgr.cfg.get("protected", []))
        if self.fixed is not None:
            children = [p for p in self.fixed if p.exists()]
        else:
            try:
                children = list(self.folder.iterdir())
            except OSError:
                children = []
        entries = []
        for p in children:
            if p.name.lower() == "desktop.ini" or is_hidden(p) or p.name.endswith(".crdownload"):
                continue
            if str(p) in protected:                  # vom Nutzer geschützt → nie anbieten
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if kept.get(self._kept_key(p)) == int(mtime):
                continue
            entries.append({"path": p, "mtime": mtime, "size": None, "origin": None})
        self.queue = self._sorted(entries)
        self.idx = 0
        self.show_current()
        self._start_presort()

    # ----- Vorsortierung durch die Analyse des Ordner-Assistenten -----
    def _start_presort(self):
        if self.hints is not None:                   # Plan liegt schon vor
            return self._apply_hints(self.hints)
        if self._presort and self._presort.isRunning():
            return
        folder, protected = self.folder, set(self.mgr.cfg.get("protected", []))

        def job(progress, cancelled):
            return org_analyze(folder, recursive=False, programs=org_installed_programs(),
                               progress=progress, cancelled=cancelled,
                               protected_paths=protected)

        self.sub.setText(self.sub.text() + "  ·  analysiere …")
        self._presort = OrgWorker(job, self)
        self._presort.done.connect(self._apply_hints)
        self._presort.failed.connect(lambda _msg: self.show_current())
        self._presort.start()

    def _apply_hints(self, plan):
        by_path = {os.path.normcase(str(f.path)): f for f in plan.files}
        for e in self.queue:
            e["org"] = by_path.get(os.path.normcase(str(e["path"])))
        if self.b_sort.text() == CLEANER_SORTS[0]:
            self.queue = self.queue[:self.idx] + self._sorted(self.queue[self.idx:])
        self.show_current()

    def _sorted(self, entries: list[dict]) -> list[dict]:
        mode = self.b_sort.text()
        if mode == "Größte zuerst":
            for e in entries:
                if e["size"] is None:
                    e["size"] = path_size(e["path"])
            return sorted(entries, key=lambda e: -e["size"])
        if mode == CLEANER_SORTS[0] and any(e.get("org") for e in entries):
            def key(e):
                f = e.get("org")
                tier = ORG_TIER_ORDER.get(f.tier, 1) if f else 1
                return (tier, 0 if (f and f.dup_of) else 1, -(f.size if f else 0), e["mtime"])
            return sorted(entries, key=key)
        return sorted(entries, key=lambda e: e["mtime"])

    def toggle_sort(self):
        i = CLEANER_SORTS.index(self.b_sort.text()) if self.b_sort.text() in CLEANER_SORTS else 0
        self.b_sort.setText(CLEANER_SORTS[(i + 1) % len(CLEANER_SORTS)])
        self.b_sort.updateGeometry()
        # Bereits Entschiedenes bleibt, nur der Rest wird neu sortiert
        self.queue = self.queue[:self.idx] + self._sorted(self.queue[self.idx:])
        self.show_current()

    def _suggested_junk(self) -> list[dict]:
        return [e for e in self.queue[self.idx:] if e.get("org") and e["org"].tier == ORG_JUNK]

    def trash_all_suggestions(self):
        junk = self._suggested_junk()
        if not junk or self.card._busy():
            return
        size = sum((e["org"].size if e.get("org") else 0) for e in junk)
        if QMessageBox.question(self, "Vorschläge übernehmen",
                                f"Alle {len(junk)} Löschvorschläge ({human_size(size)}) für den "
                                f"Papierkorb markieren?\n\nGelöscht wird erst bei „Fertig“; "
                                f"jede Markierung lässt sich mit ↶ zurücknehmen.") != QMessageBox.Yes:
            return
        ids = {id(e) for e in junk}
        rest = [e for e in self.queue[self.idx:] if id(e) not in ids]
        self.queue = self.queue[:self.idx] + junk + rest     # Vorschläge nach vorn …
        for _ in junk:                                       # … und der Reihe nach markieren
            cur = self.current()
            if cur["size"] is None:
                cur["size"] = cur["org"].size
            self.decisions.append((cur, "trash"))
            self.idx += 1
        self.show_current()

    def _confirm_action(self, action: str, entry: dict) -> bool:
        """Sicherheitsabfragen, bevor etwas Wichtiges oder die letzte Kopie in den
        Papierkorb soll."""
        f = entry.get("org")
        if action != "trash" or f is None:
            return True
        copies = [d["path"].name for d, a in self.decisions if a == "trash" and d.get("org")
                  and d["org"].dup_of and d["org"].dup_of == f.rel]
        if copies and QMessageBox.question(
                self, "Letzte Kopie?",
                f"Die Kopie(n) {', '.join(copies)} sind schon für den Papierkorb markiert.\n"
                f"Mit „{entry['path'].name}“ wäre der Inhalt ganz weg.\n\nTrotzdem?") \
                != QMessageBox.Yes:
            return False
        if f.tier != ORG_PROTECT or f.dup_of:
            return True
        label = ORG_CATEGORIES.get(f.category, {}).get("label", "wichtig")
        return QMessageBox.question(
            self, "Wirklich löschen?",
            f"„{entry['path'].name}“ sieht wichtig aus ({label}).\n\n"
            f"Trotzdem in den Papierkorb?") == QMessageBox.Yes

    def _prepare(self, e: dict | None):
        if e and "preview" not in e:
            if e["size"] is None:
                e["size"] = path_size(e["path"])
            e["origin"] = download_origin(e["path"])
            e["preview"], e["text"] = make_preview(e["path"], SwipeCard.CW - 28, 300)
        return e

    def current(self) -> dict | None:
        return self.queue[self.idx] if self.idx < len(self.queue) else None

    def show_current(self):
        cur = self._prepare(self.current())
        nxt = self._prepare(self.queue[self.idx + 1]) if self.idx + 1 < len(self.queue) else None
        trash = [d for d, a in self.decisions if a == "trash"]
        freed = sum(d["size"] or 0 for d in trash)
        if not cur:
            kept = sum(1 for _, a in self.decisions if a == "keep")
            self.card.done_text = (f"Alles erledigt ✨\n\n{len(trash)} in den Papierkorb · "
                                   f"{kept} behalten\n{human_size(freed)} frei") \
                if self.decisions else "Hier gibt es nichts aufzuräumen ✨"
        self.card.set_entry(cur, nxt)
        left = len(self.queue) - self.idx
        self.sub.setText(f"{self.folder.name or self.folder}  ·  noch {left}  ·  "
                         f"{human_size(freed)} frei")
        self.sub.setToolTip(str(self.folder))
        for b in (self.b_trash, self.b_keep, self.b_open):
            b.setEnabled(cur is not None)
        self.b_undo.setEnabled(bool(self.decisions))
        junk = self._suggested_junk()
        self.b_bulk.setVisible(bool(junk))
        if junk:
            self.b_bulk.setText(f"🗑  Alle {len(junk)} Vorschläge → Papierkorb")
            self.b_bulk.updateGeometry()
        self.b_done.setText(f"Fertig – {len(trash)} in den Papierkorb" if trash else "Fertig")
        self.b_done.updateGeometry()

    # ----- Entscheidungen -----
    def decide(self, action: str):
        cur = self.current()
        if not cur:
            return
        self.decisions.append((cur, action))
        if action == "keep" and self.fixed is None:
            self._kept()[self._kept_key(cur["path"])] = int(cur["mtime"])
            self.mgr.write_cfg()
        self.idx += 1
        self.show_current()

    def undo(self):
        if not self.decisions or self.card._busy():
            return
        entry, action = self.decisions.pop()
        if action == "keep" and self.fixed is None:
            self._kept().pop(self._kept_key(entry["path"]), None)
            self.mgr.write_cfg()
        self.idx = max(0, self.idx - 1)
        self.queue[self.idx] = entry
        self.show_current()

    def open_current(self):
        cur = self.current()
        if cur:
            self.mgr.launch(str(cur["path"]))

    def choose_folder(self):
        from PySide6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, "Ordner zum Aufräumen wählen",
                                                  str(self.folder))
        if folder:
            self.commit()
            self.folder, self.decisions, self.committed = Path(folder), [], False
            self.load()

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_Left, Qt.Key_Delete):
            self.card.fly("trash")
        elif k in (Qt.Key_Right, Qt.Key_Space):
            self.card.fly("keep")
        elif k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Up):
            self.open_current()
        elif k == Qt.Key_Backspace or (k == Qt.Key_Z and e.modifiers() & Qt.ControlModifier):
            self.undo()
        elif k == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)

    # ----- Papierkorb -----
    def commit(self) -> list[str]:
        """Alle als „Papierkorb“ markierten Dateien jetzt in den Papierkorb legen
        (wiederherstellbar). Bis dahin ist jede Entscheidung rückgängig zu machen."""
        if self.committed:
            return []
        self.committed = True
        failed = []
        for entry, action in self.decisions:
            if action == "trash" and entry["path"].exists():
                if not safe_trash(entry["path"]):
                    failed.append(entry["path"].name)
        return failed

    def closeEvent(self, e):
        if self._presort and self._presort.isRunning():
            self._presort.cancel()
            self._presort.wait(5000)
        n = sum(1 for _, a in self.decisions if a == "trash")
        failed = self.commit()
        if failed:
            QMessageBox.warning(self, "Papierkorb",
                                f"Nicht in den Papierkorb verschoben ({TRASH_FAIL_HINT}):\n\n"
                                + "\n".join(failed))
        elif n:
            self.mgr.notify.emit("Aufgeräumt", f"{n} Datei(en) im Papierkorb")
        super().closeEvent(e)


# --------------------------------------------------------------------------- #
# Ordner-Assistent – Oberfläche
# --------------------------------------------------------------------------- #
from PySide6.QtWidgets import (QFileDialog, QHeaderView, QStackedWidget, QTreeWidget,
                               QTreeWidgetItem)

ORG_TEXT_CACHE: dict[tuple, str] = {}      # (Pfad, Größe, mtime) → Text; macht Neu-Analysen schnell
_org_extract_text_raw = org_extract_text


def org_extract_text(path, max_chars: int = 4000) -> str:       # noqa: F811 — bewusst ersetzt
    """Zwischengespeicherte Textextraktion (gleiche Datei = gleicher Text)."""
    try:
        st = os.stat(path)
        key = (str(path), st.st_size, int(st.st_mtime), max_chars)
    except OSError:
        return ""
    if key not in ORG_TEXT_CACHE:
        if len(ORG_TEXT_CACHE) > 20000:              # Speicher begrenzen (~80 MB Text)
            ORG_TEXT_CACHE.clear()
        ORG_TEXT_CACHE[key] = _org_extract_text_raw(path, max_chars)
    return ORG_TEXT_CACHE[key]


def org_dest_for(f: OrgFile) -> str:
    """Zielordner für eine (ggf. manuell umkategorisierte) Datei."""
    folder = ORG_CATEGORIES.get(f.category, {}).get("folder", "")
    if not folder:
        return ""
    year = org_year_of(f)
    event = f.event or f"{year}-{time.localtime(f.mtime).tm_mon:02d} " \
                       f"{org_month_name(time.localtime(f.mtime).tm_mon)}"
    return folder.replace("{year}", str(year)).replace("{event}", event)


class OrgWorker(QThread):
    """Führt eine Analyse/Aktion im Hintergrund aus; Fortschritt gedrosselt."""
    progress = Signal(str, int, int)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self._cancel = False
        self._last = 0.0

    def cancel(self):
        self._cancel = True

    def _progress(self, stage, done, total):
        now = time.monotonic()
        if now - self._last > 0.08 or done >= total:
            self._last = now
            self.progress.emit(str(stage), int(done or 0), int(total or 0))

    def run(self):
        try:
            self.done.emit(self.fn(self._progress, lambda: self._cancel))
        except Exception as e:                          # noqa: BLE001 — im UI anzeigen
            import traceback
            self.failed.emit(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()[-1500:]}")


class OrgTile(QAbstractButton):
    """Kachel der Übersicht: Symbol, große Zahl, Beschriftung."""

    def __init__(self, glyph: str, title: str, color: str, parent=None):
        super().__init__(parent)
        self.glyph, self.title, self.color = glyph, title, QColor(color)
        self.value, self.sub = "–", ""
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(170, 92)
        self._hover = False

    def set_value(self, value: str, sub: str = ""):
        self.value, self.sub = value, sub
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        paint_glass(p, r, 18, strength=1.2, tint=self.color if self.isChecked() else None,
                    hover=self._hover, active=self.isChecked())
        f = self.font()
        f.setPixelSize(13)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(self.color.lighter(140))
        p.drawText(QRectF(16, 12, r.width() - 24, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{self.glyph}  {self.title}")
        f.setPixelSize(26)
        f.setWeight(QFont.Bold)
        p.setFont(f)
        p.setPen(Qt.white)
        p.drawText(QRectF(16, 34, r.width() - 24, 34), Qt.AlignLeft | Qt.AlignVCenter, self.value)
        f.setPixelSize(11)
        f.setWeight(QFont.Normal)
        p.setFont(f)
        p.setPen(TEXT_2)
        p.drawText(QRectF(16, 66, r.width() - 24, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.sub, Qt.ElideRight, int(r.width() - 24)))


ORG_SECTIONS = [
    # Schlüssel, Titel, Symbol, Farbe
    ("moves", "Neue Ordnung", "🗂", "#0A84FF"),
    ("protect", "Wichtig & dauerhaft", "🔒", "#30D158"),
    ("dups", "Duplikate", "⧉", "#FF9F0A"),
    ("versions", "Alte Versionen", "🕘", "#BF5AF2"),
    ("junk", "Reste & Müll", "🗑", "#FF453A"),
    ("review", "Prüfen", "❔", "#64D2FF"),
    ("largest", "Größte Dateien", "⬛", "#8E8E93"),
]


class OrganizerWindow(_Base):
    """Analysiert einen beliebigen Ordner, schlägt Ordnung vor und räumt sicher auf."""

    def __init__(self, mgr: "FenceManager", folder: Path):
        super().__init__()
        self.mgr, self.folder = mgr, Path(folder)
        self.plan = None
        self._worker: OrgWorker | None = None
        self.setWindowTitle("Ordner-Assistent")
        self.setWindowIcon(app_icon())
        if FRAMELESS:
            self.setTitleBar(TitleBar(self))
            for b in (self.titleBar.minBtn, self.titleBar.maxBtn, self.titleBar.closeBtn):
                b.setNormalColor(QColor(255, 255, 255, 200))
                b.setHoverColor(Qt.white)
        if ACRYLIC:
            self.windowEffect.setAcrylicEffect(self.winId(), ACRYLIC_TINT)
        self._build()
        self.resize(1240, 860)
        QTimer.singleShot(0, self.analyze)

    # ---------- Aufbau ----------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 40 if FRAMELESS else 22, 22, 18)
        root.setSpacing(12)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        t = QLabel("Ordner-Assistent")
        t.setObjectName("h1")
        self.sub = QLabel("")
        self.sub.setObjectName("sub")
        titles.addWidget(t)
        titles.addWidget(self.sub)
        head.addLayout(titles)
        head.addStretch(1)
        self.t_recursive = Toggle("Unterordner einbeziehen", checked=True)
        self.t_reorg = Toggle("Unterordner neu ordnen")
        self.t_recursive.toggled.connect(lambda _: self.analyze())
        self.t_reorg.toggled.connect(lambda _: self.analyze())
        self.b_folder = GlassButton("Ordner …", small=True)
        self.b_folder.clicked.connect(self.choose_folder)
        self.b_again = GlassButton("↻  Neu analysieren", small=True)
        self.b_again.clicked.connect(self.analyze)
        for w in (self.t_recursive, self.t_reorg):
            head.addWidget(w)
            head.addSpacing(8)
        head.addWidget(self.b_folder)
        head.addWidget(self.b_again)
        root.addLayout(head)

        # Fortschritt
        self.progress_row = QWidget()
        pr = QHBoxLayout(self.progress_row)
        pr.setContentsMargins(0, 0, 0, 0)
        self.progress_lbl = QLabel("")
        self.progress_lbl.setObjectName("sub")
        self.progress_bar = OrgProgressBar()
        self.b_cancel = GlassButton("Abbrechen", small=True)
        self.b_cancel.clicked.connect(lambda: self._worker and self._worker.cancel())
        pr.addWidget(self.progress_lbl)
        pr.addWidget(self.progress_bar, 1)
        pr.addWidget(self.b_cancel)
        root.addWidget(self.progress_row)

        # Kacheln
        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.tiles: dict[str, OrgTile] = {}
        for key, title, glyph, color in ORG_SECTIONS:
            tile = OrgTile(glyph, title, color)
            tile.clicked.connect(lambda _=False, k=key: self.show_section(k))
            self.tiles[key] = tile
            tiles.addWidget(tile)
        root.addLayout(tiles)

        # Inhalt
        self.panel = GlassPanel()
        pl = QVBoxLayout(self.panel)
        pl.setContentsMargins(16, 14, 16, 12)
        top = QHBoxLayout()
        self.sec_title = QLabel("")
        self.sec_title.setObjectName("h2")
        self.sec_hint = QLabel("")
        self.sec_hint.setObjectName("sub")
        self.sec_hint.setWordWrap(True)
        tv = QVBoxLayout()
        tv.setSpacing(2)
        tv.addWidget(self.sec_title)
        tv.addWidget(self.sec_hint)
        top.addLayout(tv, 1)
        self.b_all = GlassButton("Alle", small=True)
        self.b_none = GlassButton("Keine", small=True)
        self.b_all.clicked.connect(lambda: self._check_all(True))
        self.b_none.clicked.connect(lambda: self._check_all(False))
        top.addWidget(self.b_all)
        top.addWidget(self.b_none)
        pl.addLayout(top)
        self.stack = QStackedWidget()
        self.trees: dict[str, QTreeWidget] = {}
        for key, *_ in ORG_SECTIONS:
            tree = self._make_tree()
            self.trees[key] = tree
            self.stack.addWidget(tree)
        pl.addWidget(self.stack, 1)

        actions = QHBoxLayout()
        self.b_undo = GlassButton("↶  Letzte Sortierung rückgängig", small=True)
        self.b_undo.clicked.connect(self.undo_last)
        actions.addWidget(self.b_undo)
        actions.addStretch(1)
        self.b_swipe = GlassButton("Einzeln durchwischen", small=True)
        self.b_swipe.clicked.connect(self.swipe_selected)
        self.b_secondary = GlassButton("", small=True)
        self.b_secondary.clicked.connect(lambda: self._run_action(secondary=True))
        self.b_primary = GlassButton("", primary=True)
        self.b_primary.clicked.connect(lambda: self._run_action(secondary=False))
        actions.addWidget(self.b_swipe)
        actions.addWidget(self.b_secondary)
        actions.addWidget(self.b_primary)
        pl.addLayout(actions)
        root.addWidget(self.panel, 1)
        self.current = "moves"
        self.busy_widgets = [self.b_folder, self.b_again, self.t_recursive, self.t_reorg,
                             self.panel] + list(self.tiles.values())

    def _make_tree(self) -> QTreeWidget:
        t = QTreeWidget()
        t.setObjectName("org")
        t.setColumnCount(4)
        t.setHeaderLabels(["Name", "Grund", "Größe", "Datum"])
        t.setIconSize(QSize(20, 20))
        t.setUniformRowHeights(True)
        t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        t.setContextMenuPolicy(Qt.CustomContextMenu)
        t.customContextMenuRequested.connect(lambda pos, tree=t: self._item_menu(tree, pos))
        t.itemDoubleClicked.connect(self._open_item)
        h = t.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.Interactive)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        t.setColumnWidth(1, 360)
        return t

    def paintEvent(self, _):
        p = QPainter(self)
        paint_backdrop(p, self.rect(), overlay_only=ACRYLIC)

    # ---------- Analyse ----------
    def _protected(self) -> set:
        return set(self.mgr.cfg.setdefault("protected", []))

    def _ignored(self) -> set:
        return set(self.mgr.cfg.setdefault("org_ignore", []))

    def set_busy(self, text: str | None):
        for w in self.busy_widgets:
            w.setEnabled(text is None)
        self.progress_row.setVisible(text is not None)
        if text:
            self.progress_lbl.setText(text)
            self.progress_bar.set_value(0, 0)

    def analyze(self):
        if self._worker and self._worker.isRunning():
            return
        folder, recursive, reorg = self.folder, self.t_recursive.isChecked(), self.t_reorg.isChecked()
        protected = self._protected()

        def job(progress, cancelled):
            programs = org_installed_programs()
            return org_analyze(folder, recursive=recursive, reorganize_subfolders=reorg,
                               programs=programs, progress=progress, cancelled=cancelled,
                               protected_paths=protected)

        self.set_busy("Analysiere …")
        self.sub.setText(f"{self.folder.name or self.folder}")
        self.sub.setToolTip(str(self.folder))
        self._worker = OrgWorker(job, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_plan)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, stage, done, total):
        self.progress_lbl.setText(f"{stage} …  {done:,} / {total:,}".replace(",", ".")
                                  if total else f"{stage} …")
        self.progress_bar.set_value(done, total)

    def _on_failed(self, msg):
        self.set_busy(None)
        QMessageBox.warning(self, "Ordner-Assistent", f"Analyse fehlgeschlagen:\n\n{msg}")

    def _on_plan(self, plan):
        self.set_busy(None)
        ignored = self._ignored()
        if ignored:                                     # „Nicht anfassen“ respektieren
            keep = lambda f: str(f.path) not in ignored
            plan.moves = [m for m in plan.moves if keep(m.file)]
            plan.junk = [f for f in plan.junk if keep(f)]
            plan.review = [f for f in plan.review if keep(f)]
        self.plan = plan
        st = plan.stats
        note = "  ·  ⚠ nur die ersten Dateien analysiert" if plan.truncated else ""
        self.sub.setToolTip(str(self.folder))
        self.sub.setText(f"{self.folder.name or self.folder}  ·  {st.get('files', len(plan.files)):,} Dateien  ·  "
                         f"{human_size(st.get('bytes', 0))}  ·  {st.get('seconds', 0):.1f} s"
                         .replace(",", ".") + note)
        self.populate()

    # ---------- Darstellung ----------
    def _file_item(self, f: OrgFile, checked: bool | None, extra_reason: str = "") -> QTreeWidgetItem:
        main = org_main_reason(f)
        rest = [r for r in f.reasons if r != main]
        reasons = " · ".join(([extra_reason] if extra_reason else []) + ([main] if main else []) + rest)
        it = QTreeWidgetItem([f.rel, reasons, human_size(f.size),
                              time.strftime("%d.%m.%Y", time.localtime(f.mtime))])
        it.setIcon(0, icon_cache().get(f.path))
        it.setToolTip(0, str(f.path))
        it.setToolTip(1, reasons)
        it.setData(0, Qt.UserRole, f)
        it.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
        if checked is not None:
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
        return it

    def _group_item(self, tree: QTreeWidget, title: str, sub: str = "", checkable=True) -> QTreeWidgetItem:
        g = QTreeWidgetItem([title, sub, "", ""])
        f = g.font(0)
        f.setBold(True)
        g.setFont(0, f)
        if checkable:
            g.setFlags(g.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
        tree.addTopLevelItem(g)
        return g

    def populate(self):
        plan = self.plan
        for tree in self.trees.values():
            tree.clear()
        if plan is None:
            return
        # Neue Ordnung – nach Zielordner
        t = self.trees["moves"]
        groups: dict[str, list] = {}
        for i, m in enumerate(plan.moves):
            groups.setdefault(m.dest_rel, []).append((i, m))
        for dest in sorted(groups):
            g = self._group_item(t, f"📁  {dest}", f"{len(groups[dest])} Datei(en)")
            for i, m in sorted(groups[dest], key=lambda im: im[1].file.rel.lower()):
                it = self._file_item(m.file, m.checked)
                it.setData(1, Qt.UserRole, i)
                g.addChild(it)
            g.setExpanded(len(groups) <= 12)
        # Wichtig – nach Kategorie
        t = self.trees["protect"]
        cats: dict[str, list] = {}
        for f in plan.protect:
            cats.setdefault(ORG_CATEGORIES.get(f.category, {}).get("label", f.category), []).append(f)
        for label in sorted(cats):
            g = self._group_item(t, f"🔒  {label}", f"{len(cats[label])} Datei(en)")
            for f in sorted(cats[label], key=lambda x: x.rel.lower()):
                g.addChild(self._file_item(f, True))
            g.setExpanded(True)
        # Duplikate – nach Original
        t = self.trees["dups"]
        for grp in plan.dup_groups:
            keeper = next((f for f in grp if not f.dup_of), grp[0])
            copies = [f for f in grp if f.dup_of]
            if not copies:
                continue
            g = self._group_item(t, f"Original: {keeper.rel}",
                                 f"{len(copies)} Kopie(n) · {human_size(keeper.size * len(copies))}")
            for f in copies:
                g.addChild(self._file_item(f, True))
            g.setExpanded(len(plan.dup_groups) <= 30)
        # Alte Versionen
        t = self.trees["versions"]
        for key, grp in plan.versions.items():
            latest = next((f for f in grp if f.version_latest), None)
            olds = [f for f in grp if not f.version_latest]
            if not olds:
                continue
            g = self._group_item(t, f"Neueste: {latest.rel if latest else key}",
                                 f"{len(olds)} ältere Version(en)")
            for f in sorted(olds, key=lambda x: -x.mtime):
                g.addChild(self._file_item(f, False))
            g.setExpanded(True)
        # Reste & Müll (ohne Duplikate – die haben eine eigene Ansicht)
        t = self.trees["junk"]
        rest = [f for f in plan.junk if not f.dup_of]
        cats = {}
        for f in rest:
            cats.setdefault(ORG_CATEGORIES.get(f.category, {}).get("label", f.category), []).append(f)
        for label in sorted(cats):
            g = self._group_item(t, f"🗑  {label}", f"{len(cats[label])} · "
                                 f"{human_size(sum(x.size for x in cats[label]))}")
            for f in cats[label]:
                g.addChild(self._file_item(f, True))
            g.setExpanded(True)
        if plan.empty_dirs:
            g = self._group_item(t, "📂  Leere Ordner", f"{len(plan.empty_dirs)}")
            for d in plan.empty_dirs:
                it = QTreeWidgetItem([os.path.relpath(str(d), str(plan.root)), "Leerer Ordner", "", ""])
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Checked)
                it.setData(0, Qt.UserRole + 2, str(d))
                it.setIcon(0, icon_cache().get(d))
                g.addChild(it)
            g.setExpanded(True)
        # Prüfen
        t = self.trees["review"]
        cats = {}
        for f in plan.review:
            cats.setdefault(ORG_CATEGORIES.get(f.category, {}).get("label", f.category), []).append(f)
        for label in sorted(cats, key=lambda k: -len(cats[k])):
            g = self._group_item(t, f"❔  {label}", f"{len(cats[label])} Datei(en)")
            for f in sorted(cats[label], key=lambda x: x.rel.lower()):
                g.addChild(self._file_item(f, False))
            g.setExpanded(len(cats) <= 6)
        # Größte Dateien
        t = self.trees["largest"]
        big = sorted((f for f in plan.files if not f.in_project), key=lambda f: -f.size)[:60]
        for f in big:
            tag = {ORG_PROTECT: "🔒 wichtig", ORG_JUNK: "🗑 Vorschlag: löschen"}.get(f.tier, "")
            t.addTopLevelItem(self._file_item(f, False, tag))

        self._update_tiles()
        self.show_section(self.current)

    def _update_tiles(self):
        p = self.plan
        dup_copies = [f for f in p.junk if f.dup_of]
        rest = [f for f in p.junk if not f.dup_of]
        olds = [f for g in p.versions.values() for f in g if not f.version_latest]
        self.tiles["moves"].set_value(str(len(p.moves)), f"in {len({m.dest_rel for m in p.moves})} Ordner")
        self.tiles["protect"].set_value(str(len(p.protect)), "nie zum Löschen vorgeschlagen")
        self.tiles["dups"].set_value(human_size(sum(f.size for f in dup_copies)),
                                     f"{len(dup_copies)} Kopien")
        self.tiles["versions"].set_value(str(len(olds)), f"in {len(p.versions)} Gruppen")
        self.tiles["junk"].set_value(human_size(sum(f.size for f in rest)),
                                     f"{len(rest)} Dateien · {len(p.empty_dirs)} leere Ordner")
        self.tiles["review"].set_value(str(len(p.review)), "selbst entscheiden")
        self.tiles["largest"].set_value(human_size(sum(f.size for f in sorted(
            p.files, key=lambda f: -f.size)[:60])), "die 60 größten")

    SECTION_INFO = {
        "moves": ("Vorgeschlagene Ordnung",
                  "Dateien werden innerhalb dieses Ordners in Unterordner verschoben – nichts wird gelöscht. "
                  "Häkchen = übernehmen. Rückgängig jederzeit möglich.",
                  "Ausgewählte einsortieren", None),
        "protect": ("Wichtig & dauerhaft behalten",
                    "Lebenslauf, Zeugnisse, Urkunden, Verträge, Steuer, Gehalt, Versicherungen, Ausweise, "
                    "Gesundheit und persönliche Fotos. Diese Dateien werden nie zum Löschen vorgeschlagen.",
                    "🗄  Im Tresor sichern …", "🔒  Als geschützt merken"),
        "dups": ("Exakte Duplikate",
                 "Byte-identische Kopien. Das Original bleibt immer erhalten; nur die Kopien gehen in den Papierkorb.",
                 "Kopien in den Papierkorb", None),
        "versions": ("Alte Versionen",
                     "„final“, „v2“, „Kopie“, „(1)“ … – die neueste Version bleibt, ältere kannst du löschen.",
                     "Ausgewählte in den Papierkorb", None),
        "junk": ("Reste & Müll",
                 "Office-Sperrdateien, Temporäres, abgebrochene Downloads, leere Dateien und Ordner, "
                 "Installer bereits installierter Programme. Alles geht in den Papierkorb (wiederherstellbar).",
                 "Ausgewählte in den Papierkorb", None),
        "review": ("Selbst entscheiden",
                   "Unklare Dateien. Am schnellsten: markieren und „Einzeln durchwischen“ (links = Papierkorb, "
                   "rechts = behalten).",
                   "Ausgewählte in den Papierkorb", None),
        "largest": ("Größte Dateien",
                    "Die größten Platzfresser in diesem Ordner.",
                    "Ausgewählte in den Papierkorb", None),
    }

    def show_section(self, key: str):
        self.current = key
        for k, tile in self.tiles.items():
            tile.setChecked(k == key)
        self.stack.setCurrentWidget(self.trees[key])
        title, hint, primary, secondary = self.SECTION_INFO[key]
        self.sec_title.setText(title)
        self.sec_hint.setText(hint)
        self.b_primary.setText(primary)
        self.b_primary.updateGeometry()
        self.b_secondary.setVisible(secondary is not None)
        if secondary:
            self.b_secondary.setText(secondary)
            self.b_secondary.updateGeometry()
        self.b_swipe.setVisible(key in ("review", "versions", "largest", "junk", "dups"))
        self.b_undo.setEnabled(self._last_journal() is not None)

    # ---------- Auswahl ----------
    def _iter_items(self, tree: QTreeWidget):
        stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            yield it
            stack.extend(it.child(i) for i in range(it.childCount()))

    def _checked_files(self, key: str) -> list:
        return [it.data(0, Qt.UserRole) for it in self._iter_items(self.trees[key])
                if it.data(0, Qt.UserRole) is not None and it.checkState(0) == Qt.Checked]

    def _check_all(self, on: bool):
        for it in self._iter_items(self.trees[self.current]):
            if it.flags() & Qt.ItemIsUserCheckable and it.childCount() == 0:
                it.setCheckState(0, Qt.Checked if on else Qt.Unchecked)

    # ---------- Aktionen ----------
    def _run_action(self, secondary: bool):
        if self.plan is None:
            return
        key = self.current
        if key == "moves":
            return self.apply_moves()
        if key == "protect":
            return self.remember_protected() if secondary else self.backup_vault()
        files = self._checked_files(key)
        dirs = []
        if key == "junk":
            dirs = [it.data(0, Qt.UserRole + 2) for it in self._iter_items(self.trees["junk"])
                    if it.data(0, Qt.UserRole + 2) and it.checkState(0) == Qt.Checked]
        self.trash(files, dirs)

    def apply_moves(self):
        idx = [it.data(1, Qt.UserRole) for it in self._iter_items(self.trees["moves"])
               if it.data(1, Qt.UserRole) is not None and it.checkState(0) == Qt.Checked]
        moves = [self.plan.moves[i] for i in idx]
        if not moves:
            return QMessageBox.information(self, "Ordner-Assistent", "Nichts ausgewählt.")
        n_dirs = len({m.dest_rel for m in moves})
        if QMessageBox.question(self, "Einsortieren",
                                f"{len(moves)} Dateien in {n_dirs} Ordner einsortieren?\n\n"
                                f"Es wird nichts gelöscht; „Rückgängig“ stellt alles wieder her.") \
                != QMessageBox.Yes:
            return
        JOURNAL_DIR.mkdir(exist_ok=True)
        journal = JOURNAL_DIR / f"organize-{time.strftime('%Y%m%d-%H%M%S')}.json"
        moved, failed = org_apply_moves(self.plan, moves, journal)
        msg = f"{moved} Dateien einsortiert."
        if failed:
            msg += "\n\nNicht verschoben:\n" + "\n".join(failed[:15])
        QMessageBox.information(self, "Ordner-Assistent", msg)
        self.analyze()

    def trash(self, files: list, dirs: list = ()):
        if not files and not dirs:
            return QMessageBox.information(self, "Ordner-Assistent", "Nichts ausgewählt.")
        size = sum(f.size for f in files)
        text = f"{len(files)} Datei(en) ({human_size(size)})"
        if dirs:
            text += f" und {len(dirs)} leere(n) Ordner"
        if QMessageBox.question(self, "Papierkorb", f"{text} in den Papierkorb verschieben?\n\n"
                                "Du kannst alles über den Papierkorb wiederherstellen.") \
                != QMessageBox.Yes:
            return
        done, failed = org_trash(files, safe_trash)
        for d in dirs:                                   # leere Ordner: vorher erneut prüfen
            dp = Path(d)
            try:
                still_empty = dp.is_dir() and not any(x.is_file() for x in dp.rglob("*"))
            except OSError:
                still_empty = False
            if still_empty and safe_trash(dp):
                done += 1
            elif dp.exists():
                failed.append(f"{dp.name}: nicht mehr leer oder gesperrt")
        msg = f"{done} Objekt(e) im Papierkorb."
        if failed:
            msg += f"\n\nÜbersprungen ({TRASH_FAIL_HINT}):\n" + "\n".join(failed[:15])
        QMessageBox.information(self, "Papierkorb", msg)
        self.analyze()

    def remember_protected(self):
        files = self._checked_files("protect")
        lst = self.mgr.cfg.setdefault("protected", [])
        new = [str(f.path) for f in files if str(f.path) not in lst]
        lst.extend(new)
        self.mgr.write_cfg()
        QMessageBox.information(self, "Geschützt",
                                f"{len(new)} Datei(en) zusätzlich als geschützt gemerkt.\n"
                                "Sie erscheinen nie mehr als Löschvorschlag – auch nicht im "
                                "Downloads-Aufräumen.")

    def backup_vault(self):
        files = self._checked_files("protect")
        if not files:
            return QMessageBox.information(self, "Tresor", "Nichts ausgewählt.")
        start = self.mgr.cfg.get("vault_dir") or str(Path.home())
        dest = QFileDialog.getExistingDirectory(
            self, "Tresor-Ordner wählen (z. B. OneDrive oder externe Festplatte)", start)
        if not dest:
            return
        self.mgr.cfg["vault_dir"] = dest
        self.mgr.write_cfg()

        def job(progress, cancelled):
            return org_backup(files, Path(dest), progress=progress, cancelled=cancelled)

        def done(res):
            self.set_busy(None)
            fails = res.get("failed", [])
            msg = (f"{res.get('copied', 0)} kopiert, {res.get('skipped', 0)} waren schon "
                   f"gesichert ({human_size(res.get('bytes', 0))}).\nZiel: {dest}")
            if fails:
                msg += "\n\nFehler:\n" + "\n".join(fails[:10])
            QMessageBox.information(self, "Tresor", msg)

        self.set_busy("Sichere in den Tresor …")
        self._worker = OrgWorker(job, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def swipe_selected(self):
        tree = self.trees[self.current]
        files = [it.data(0, Qt.UserRole) for it in tree.selectedItems()
                 if it.data(0, Qt.UserRole) is not None]
        if not files:
            files = self._checked_files(self.current) or [
                it.data(0, Qt.UserRole) for it in self._iter_items(tree)
                if it.data(0, Qt.UserRole) is not None]
        files = [f for f in files if f.tier != ORG_PROTECT or f.dup_of]
        if not files:
            return
        win = CleanerWindow(self.mgr, self.folder, entries=[f.path for f in files],
                            title=self.SECTION_INFO[self.current][0], hints=self.plan)
        win.destroyed.connect(self._after_swipe)
        win.setAttribute(Qt.WA_DeleteOnClose)
        win.show()
        self._swipe = win

    def _after_swipe(self, *_):
        """Nach dem Durchwischen neu analysieren – falls der Assistent noch offen ist."""
        try:
            if self.isVisible():
                self.analyze()
        except RuntimeError:                         # Fenster wurde inzwischen gelöscht
            pass

    def _last_journal(self) -> Path | None:
        js = sorted(JOURNAL_DIR.glob("organize-*[0-9].json")) if JOURNAL_DIR.is_dir() else []
        return js[-1] if js else None

    def undo_last(self):
        j = self._last_journal()
        if not j:
            return
        if QMessageBox.question(self, "Rückgängig", "Letzte Sortierung durch den "
                                "Ordner-Assistenten vollständig rückgängig machen?") != QMessageBox.Yes:
            return
        restored, failed = org_undo(j)
        msg = f"{restored} Datei(en) zurückgelegt."
        if failed:
            msg += "\n\n" + "\n".join(failed[:15])
        QMessageBox.information(self, "Rückgängig", msg)
        self.analyze()

    def choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Ordner analysieren", str(self.folder))
        if d:
            self.folder = Path(d)
            self.analyze()

    # ---------- Kontextmenü ----------
    def _open_item(self, it, _col=0):
        f = it.data(0, Qt.UserRole)
        if f is not None:
            self.mgr.launch(str(f.path))

    def _item_menu(self, tree: QTreeWidget, pos):
        it = tree.itemAt(pos)
        f = it.data(0, Qt.UserRole) if it else None
        if f is None:
            return
        m = glass_menu(self)
        m.addAction("▶  Öffnen", lambda: self.mgr.launch(str(f.path)))
        m.addAction("↗  Im Explorer zeigen", lambda: reveal_path(str(f.path)))
        m.addSeparator()
        cat = glass_menu(m, "Kategorie ändern")
        for key, c in ORG_CATEGORIES.items():
            if key in ("temp", "duplicate", "other") or not c["folder"]:
                continue
            a = cat.addAction(c["label"], lambda k=key: self.recategorize(f, k))
            a.setCheckable(True)
            a.setChecked(key == f.category)
        m.addMenu(cat)
        prot = str(f.path) in self._protected()
        m.addAction("🔓  Nicht mehr schützen" if prot else "🔒  Schützen (nie löschen)",
                    lambda: self.toggle_protect(f, not prot))
        m.addAction("🚫  Nicht anfassen (ausblenden)", lambda: self.ignore(f))
        m.exec(tree.viewport().mapToGlobal(pos))

    def recategorize(self, f: OrgFile, key: str):
        f.category = key
        f.tier = ORG_CATEGORIES[key]["tier"]
        f.add_reason(f"Manuell: {ORG_CATEGORIES[key]['label']}")
        dest = org_dest_for(f)
        plan = self.plan
        plan.moves = [m for m in plan.moves if m.file is not f]
        plan.junk = [x for x in plan.junk if x is not f]
        plan.review = [x for x in plan.review if x is not f]
        if f.tier == ORG_PROTECT and f not in plan.protect:
            plan.protect.append(f)
        parent = Path(f.rel).parent.as_posix()
        if dest and not (parent == dest or parent.startswith(dest + "/")):
            plan.moves.append(OrgMove(f, dest, True))
        self.populate()

    def toggle_protect(self, f: OrgFile, on: bool):
        lst = self.mgr.cfg.setdefault("protected", [])
        if on and str(f.path) not in lst:
            lst.append(str(f.path))
        elif not on and str(f.path) in lst:
            lst.remove(str(f.path))
        self.mgr.write_cfg()
        if on:
            f.tier = ORG_PROTECT
            self.plan.junk = [x for x in self.plan.junk if x is not f or x.dup_of]
            self.plan.review = [x for x in self.plan.review if x is not f]
            if f not in self.plan.protect:
                self.plan.protect.append(f)
        self.populate()

    def ignore(self, f: OrgFile):
        lst = self.mgr.cfg.setdefault("org_ignore", [])
        if str(f.path) not in lst:
            lst.append(str(f.path))
            self.mgr.write_cfg()
        p = self.plan
        p.moves = [m for m in p.moves if m.file is not f]
        p.junk = [x for x in p.junk if x is not f]
        p.review = [x for x in p.review if x is not f]
        self.populate()

    def closeEvent(self, e):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        super().closeEvent(e)


class OrgProgressBar(QWidget):
    """Schmaler Glas-Fortschrittsbalken (unbestimmt, wenn total == 0)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(10)
        self.done, self.total, self._phase = 0, 0, 0.0
        self._timer = QTimer(self, interval=30)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_value(self, done: int, total: int):
        self.done, self.total = done, total
        self.update()

    def _tick(self):
        if self.isVisible() and not self.total:
            self._phase = (self._phase + 0.012) % 1.0
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint_glass(p, r, r.height() / 2, strength=1.0)
        if self.total:
            w = r.width() * min(1.0, self.done / max(1, self.total))
            seg = QRectF(r.left(), r.top(), w, r.height())
        else:
            w = r.width() * 0.25
            seg = QRectF(r.left() + (r.width() + w) * self._phase - w, r.top(), w, r.height())
        path = QPainterPath()
        path.addRoundedRect(seg.intersected(r), r.height() / 2, r.height() / 2)
        p.fillPath(path, ACCENT)


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
QTreeWidget#org { background: transparent; border: none; outline: none; }
QTreeWidget#org::item { padding: 4px 2px; border-radius: 6px; }
QTreeWidget#org::item:hover { background: rgba(255,255,255,20); }
QTreeWidget#org::item:selected { background: rgba(10,132,255,110); }
QTreeWidget#org::indicator { width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid rgba(255,255,255,130); background: rgba(255,255,255,18); }
QTreeWidget#org::indicator:checked { background: #0A84FF; border-color: #64D2FF; }
QTreeWidget#org::indicator:indeterminate { background: rgba(10,132,255,110); border-color: #64D2FF; }
QHeaderView, QTreeWidget#org QHeaderView { background: transparent; border: none; }
QHeaderView::section, QTreeWidget#org QHeaderView::section { background: transparent;
    color: rgba(255,255,255,150); border: none; border-bottom: 1px solid rgba(255,255,255,40);
    padding: 4px 6px; font-weight: 600; }
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
    ap.add_argument("--min-group", type=int, default=2)
    ap.add_argument("--storage", type=Path, default=STORAGE_DIR,
                    help="Ordner mit dem Inhalt der Glas-Boxen")
    ap.add_argument("--background", action="store_true",
                    help="nur Boxen + Infobereich-Symbol (für den Autostart)")
    ap.add_argument("--organize", nargs="?", default=None, metavar="ORDNER",
                    help="Ordner-Assistent für diesen Ordner öffnen (Explorer-Rechtsklick)")
    ap.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)
    # parse_known_args: Explorer übergibt Pfade mit Eigenheiten (z. B. „C:\"“)
    args, _unknown = ap.parse_known_args()
    args.organize_folder = org_parse_cli_folder(sys.argv) if "--organize" in sys.argv else None
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
        if args.organize_folder:            # Explorer-Rechtsklick → laufende Instanz
            msg = b"organize:" + str(args.organize_folder).encode("utf-8")
        else:
            msg = b"background" if args.background else b"show"
        sock.write(msg)
        sock.waitForBytesWritten(1000)
        return
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    server.listen(server_name)

    app.setQuitOnLastWindowClosed(False)
    fences = FenceManager(args.storage, args.rules, args.memory)
    fences.sync()
    app.aboutToQuit.connect(fences.hook.stop)
    # Offene Aufräum-Entscheidungen beim Beenden noch ausführen
    app.aboutToQuit.connect(lambda: getattr(fences, "_cleaner", None)
                            and fences._cleaner.isVisible() and fences._cleaner.close())
    # Autostart: Verknüpfung bei jedem Start (neu) anlegen – zeigt so auch nach
    # dem Verschieben der .pyw oder einer neuen Python-Umgebung auf die richtige Stelle.
    if Win32.ok and fences.settings["autostart"]:
        import threading
        threading.Thread(target=set_autostart, args=(True,), daemon=True).start()
    if fences.settings["context_menu"]:
        sync_context_menu(True)             # Rechtsklick-Eintrag aktuell halten
    # Erinnerung an volle Downloads: 10 min nach dem Start, danach alle 6 h prüfen
    QTimer.singleShot(10 * 60 * 1000, fences.check_downloads_reminder)
    reminder = QTimer(app, interval=6 * 3600 * 1000)
    reminder.timeout.connect(fences.check_downloads_reminder)
    reminder.start()
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
        data = b""
        while conn.waitForReadyRead(300):
            data += bytes(conn.readAll())
        if data == b"show":
            open_manager()
        elif data.startswith(b"organize:"):
            folder = Path(data[len(b"organize:"):].decode("utf-8", "replace"))
            if folder.is_dir():
                fences.open_organizer(folder)
        conn.close()

    server.newConnection.connect(on_connection)

    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(app_icon(), app)
        tray.setToolTip("Desktop Sorter")
        tray.setContextMenu(fences.build_tray_menu(open_manager))
        tray.activated.connect(lambda reason: open_manager()
                               if reason == QSystemTrayIcon.Trigger else None)
        fences.notify.connect(lambda title, text: tray.showMessage(
            title, text, QSystemTrayIcon.Information, 5000))
        tray.messageClicked.connect(fences.message_clicked)
        tray.show()
        app._tray = tray                # Referenz halten

    if args.organize_folder:
        fences.open_organizer(args.organize_folder)
    elif not args.background or not fences.fences:
        open_manager()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
