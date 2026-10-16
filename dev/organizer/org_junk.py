# ===========================================================================
# ORDNER-ASSISTENT – Reste & Müll, Installationsdateien, leere Ordner
# ===========================================================================
# Erkennt eindeutige Reste (Sperrdateien, Temp-Dateien, abgebrochene Downloads,
# Vorschau-Caches, Absturzabbilder, leere Dateien), ordnet Installationsdateien
# bereits installierten Programmen zu und findet leere Ordner.
# Nichts wird hier gelöscht oder verschoben – es werden nur Vorschläge markiert.
# Nur Standardbibliothek. Alle Namen beginnen mit org_ / Org / ORG_ / _org_.
from __future__ import annotations

import math
import os
import re
import stat as _org_junk_stat
import time
import unicodedata
from pathlib import Path

from org_core import (ORG_JUNK, ORG_KEEP, ORG_PROJECT_MARKERS, ORG_PROTECT,  # noqa: F401
                      ORG_REVIEW, ORG_SKIP_DIRS)

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
