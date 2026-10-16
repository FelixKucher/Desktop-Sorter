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
from __future__ import annotations

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
