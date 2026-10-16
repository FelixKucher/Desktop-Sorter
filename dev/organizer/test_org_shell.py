# Tests für org_shell.py – laufen ohne Windows dank Fake-winreg.
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import org_shell  # noqa: E402
from org_shell import (  # noqa: E402
    ORG_SHELL_REG_ENTRIES, org_context_menu_build_command, org_context_menu_command,
    org_context_menu_install, org_context_menu_installed, org_context_menu_is_current,
    org_context_menu_uninstall, org_known_folder, org_known_folders, org_parse_cli_folder,
    org_shell_quote,
)


# ---------------------------------------------------------------------------
# Fake winreg – verhält sich in den relevanten Punkten wie das echte Modul
# ---------------------------------------------------------------------------
class FakeHKey:
    def __init__(self, reg, path, access):
        self._reg = reg
        self.path = path          # Tupel kleingeschriebener Namen
        self.access = access
        self.closed = False
        reg.open_handles.add(id(self))
        reg._handles[id(self)] = self

    def Close(self):
        if not self.closed:
            self.closed = True
            self._reg.open_handles.discard(id(self))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.Close()
        return False


class FakeWinreg:
    HKEY_CURRENT_USER = 0x80000001
    HKEY_LOCAL_MACHINE = 0x80000002
    REG_SZ = 1
    REG_EXPAND_SZ = 2
    REG_DWORD = 4
    KEY_READ = 0x20019
    KEY_WRITE = 0x20006
    KEY_ALL_ACCESS = 0xF003F
    _KEY_SET_VALUE = 0x0002

    def __init__(self, fail_create=False, fail_delete=False):
        # Pfad (Tupel, lowercase) → {"name": Originalname, "values": {lname: (name, val, typ)}}
        self.keys = {}
        self.open_handles = set()
        self._handles = {}
        self.fail_create = fail_create
        self.fail_delete = fail_delete
        self.delete_order = []

    # --- intern ---
    def _base(self, key):
        if isinstance(key, FakeHKey):
            if key.closed:
                raise OSError(6, "Das Handle ist ungültig")
            return key.path
        if key in (self.HKEY_CURRENT_USER, self.HKEY_LOCAL_MACHINE):
            return (str(key),)
        raise TypeError("ungültiges Handle")

    def _split(self, sub_key):
        if sub_key is None or sub_key == "":
            return ()
        if not isinstance(sub_key, str):
            raise TypeError("sub_key muss str sein")
        return tuple(p for p in sub_key.split("\\") if p)

    def _full(self, key, sub_key):
        base = self._base(key)
        parts = self._split(sub_key)
        return base + tuple(p.lower() for p in parts), parts

    # --- API ---
    def CreateKeyEx(self, key, sub_key, reserved=0, access=None):
        if access is None:
            access = self.KEY_WRITE
        if self.fail_create:
            raise PermissionError(5, "Zugriff verweigert")
        full, parts = self._full(key, sub_key)
        base = self._base(key)
        cur = base
        for p in parts:
            cur = cur + (p.lower(),)
            if cur not in self.keys:
                self.keys[cur] = {"name": p, "values": {}}
        return FakeHKey(self, full, access)

    def CreateKey(self, key, sub_key):
        return self.CreateKeyEx(key, sub_key, 0, self.KEY_ALL_ACCESS)

    def OpenKey(self, key, sub_key, reserved=0, access=None):
        if access is None:
            access = self.KEY_READ
        full, _parts = self._full(key, sub_key)
        if full not in self.keys and len(full) > 1:
            raise FileNotFoundError(2, "Das System kann die angegebene Datei nicht finden")
        return FakeHKey(self, full, access)

    OpenKeyEx = OpenKey

    def SetValueEx(self, key, value_name, reserved, typ, value):
        if not isinstance(key, FakeHKey):
            raise TypeError("Handle erwartet")
        if key.closed:
            raise OSError(6, "Das Handle ist ungültig")
        if not (key.access & self._KEY_SET_VALUE):
            raise PermissionError(5, "Zugriff verweigert")
        if typ == self.REG_SZ and not isinstance(value, str):
            raise ValueError("REG_SZ braucht str")
        name = value_name or ""
        self.keys[key.path]["values"][name.lower()] = (name, value, typ)

    def QueryValueEx(self, key, value_name):
        path = self._base(key)
        vals = self.keys.get(path, {"values": {}})["values"]
        name = (value_name or "").lower()
        if name not in vals:
            raise FileNotFoundError(2, "Das System kann die angegebene Datei nicht finden")
        _n, v, t = vals[name]
        return (v, t)

    def _children(self, path):
        n = len(path)
        return [k for k in self.keys if len(k) == n + 1 and k[:n] == path]

    def EnumKey(self, key, index):
        path = self._base(key)
        kids = sorted(self._children(path))
        if index >= len(kids):
            raise OSError(259, "Es sind keine Daten mehr verfügbar")
        return self.keys[kids[index]]["name"]

    def DeleteKey(self, key, sub_key):
        if self.fail_delete:
            raise PermissionError(5, "Zugriff verweigert")
        full, _parts = self._full(key, sub_key)
        if full not in self.keys:
            raise FileNotFoundError(2, "Das System kann die angegebene Datei nicht finden")
        if self._children(full):
            raise PermissionError(5, "Zugriff verweigert")   # wie echtes winreg
        del self.keys[full]
        self.delete_order.append("\\".join(full[1:]))

    def CloseKey(self, hkey):
        if isinstance(hkey, FakeHKey):
            hkey.Close()

    # --- Testhilfe ---
    def get(self, path, name=""):
        full = (str(self.HKEY_CURRENT_USER),) + tuple(p.lower() for p in path.split("\\"))
        return self.keys[full]["values"][name.lower()][1]

    def has(self, path):
        full = (str(self.HKEY_CURRENT_USER),) + tuple(p.lower() for p in path.split("\\"))
        return full in self.keys


EXE = r"C:\Users\Jürgen Müller\AppData\Local\Programs\Python\Python313\pythonw.exe"
SCRIPT = r"C:\Users\Jürgen Müller\Tools\DesktopSorter.pyw"
DIR_KEY = r"Software\Classes\Directory\shell\DesktopSorter"
BG_KEY = r"Software\Classes\Directory\Background\shell\DesktopSorter"
DRV_KEY = r"Software\Classes\Drive\shell\DesktopSorter"


def win_argv_split(cmdline):
    """CommandLineToArgvW-Regeln (vereinfacht, ohne "" im Quote) – zum Prüfen der Quotes."""
    args, cur, inq, i, have = [], [], False, 0, False
    while i < len(cmdline):
        c = cmdline[i]
        if c == "\\":
            n = 0
            while i < len(cmdline) and cmdline[i] == "\\":
                n += 1
                i += 1
            if i < len(cmdline) and cmdline[i] == '"':
                cur.append("\\" * (n // 2))
                if n % 2:
                    cur.append('"')
                    i += 1
                have = True
            else:
                cur.append("\\" * n)
                have = True
            continue
        if c == '"':
            inq = not inq
            have = True
            i += 1
            continue
        if c in " \t" and not inq:
            if have:
                args.append("".join(cur))
                cur, have = [], False
            i += 1
            continue
        cur.append(c)
        have = True
        i += 1
    if have:
        args.append("".join(cur))
    return args


class _NotifySpy:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1


# ---------------------------------------------------------------------------
class TestContextMenu(unittest.TestCase):
    def setUp(self):
        self.reg = FakeWinreg()
        self._orig_notify = org_shell._org_shell_notify
        self.spy = _NotifySpy()
        org_shell._org_shell_notify = self.spy

    def tearDown(self):
        org_shell._org_shell_notify = self._orig_notify

    def test_install_creates_all_keys(self):
        self.assertTrue(org_context_menu_install(EXE, SCRIPT, reg=self.reg))
        for k in (DIR_KEY, BG_KEY, DRV_KEY):
            self.assertTrue(self.reg.has(k), k)
            self.assertTrue(self.reg.has(k + r"\command"), k)
            self.assertEqual(self.reg.get(k), "Mit Desktop Sorter aufräumen")
            self.assertEqual(self.reg.get(k, "Icon"), EXE)
        self.assertEqual(self.reg.open_handles, set(), "Handles nicht geschlossen")
        self.assertEqual(self.spy.calls, 1)

    def test_installed_and_command(self):
        self.assertFalse(org_context_menu_installed(reg=self.reg))
        self.assertIsNone(org_context_menu_command(reg=self.reg))
        org_context_menu_install(EXE, SCRIPT, reg=self.reg)
        self.assertTrue(org_context_menu_installed(reg=self.reg))
        cmd = org_context_menu_command(reg=self.reg)
        self.assertEqual(cmd, '"%s" "%s" --organize "%%1"' % (EXE, SCRIPT))
        self.assertEqual(self.reg.open_handles, set())

    def test_command_quoting_roundtrip(self):
        org_context_menu_install(EXE, SCRIPT, reg=self.reg)
        cmd = org_context_menu_command(reg=self.reg)
        argv = win_argv_split(cmd.replace("%1", r"C:\Users\Jürgen Müller\Eigene Dateien"))
        self.assertEqual(argv, [EXE, SCRIPT, "--organize",
                                r"C:\Users\Jürgen Müller\Eigene Dateien"])
        self.assertIn("Jürgen Müller", cmd)

    def test_background_uses_V_and_drive_uses_1(self):
        org_context_menu_install(EXE, SCRIPT, reg=self.reg)
        self.assertTrue(self.reg.get(BG_KEY + r"\command").endswith('--organize "%V"'))
        self.assertTrue(self.reg.get(DRV_KEY + r"\command").endswith('--organize "%1"'))
        self.assertTrue(self.reg.get(DIR_KEY + r"\command").endswith('--organize "%1"'))

    def test_multiselect_single(self):
        org_context_menu_install(EXE, SCRIPT, reg=self.reg)
        self.assertEqual(self.reg.get(DIR_KEY, "MultiSelectModel"), "Single")
        self.assertEqual(self.reg.get(DRV_KEY, "MultiSelectModel"), "Single")

    def test_custom_label_and_icon(self):
        ico = r"C:\Users\Jürgen Müller\Tools\sorter.ico"
        self.assertTrue(org_context_menu_install(EXE, SCRIPT, label="Aufräumen!", icon=ico,
                                                 reg=self.reg))
        self.assertEqual(self.reg.get(DIR_KEY), "Aufräumen!")
        self.assertEqual(self.reg.get(BG_KEY, "Icon"), ico)

    def test_prequoted_paths_not_double_quoted(self):
        org_context_menu_install('"%s"' % EXE, '"%s"' % SCRIPT, reg=self.reg)
        cmd = org_context_menu_command(reg=self.reg)
        self.assertNotIn('""', cmd)
        self.assertEqual(win_argv_split(cmd)[:2], [EXE, SCRIPT])

    def test_frozen_exe_without_script(self):
        org_context_menu_install(r"C:\Program Files\Desktop Sorter\DesktopSorter.exe", "",
                                 reg=self.reg)
        cmd = org_context_menu_command(reg=self.reg)
        self.assertEqual(cmd, r'"C:\Program Files\Desktop Sorter\DesktopSorter.exe" '
                              r'--organize "%1"')

    def test_reinstall_overwrites_stale_path(self):
        org_context_menu_install(EXE, r"D:\alt\DesktopSorter.pyw", reg=self.reg)
        self.assertFalse(org_context_menu_is_current(EXE, SCRIPT, reg=self.reg))
        org_context_menu_install(EXE, SCRIPT, reg=self.reg)
        self.assertTrue(org_context_menu_is_current(EXE, SCRIPT, reg=self.reg))
        self.assertTrue(org_context_menu_is_current(EXE.upper(), SCRIPT, reg=self.reg))
        self.assertIn(SCRIPT, org_context_menu_command(reg=self.reg))

    def test_uninstall_removes_everything_command_first(self):
        org_context_menu_install(EXE, SCRIPT, reg=self.reg)
        self.assertTrue(org_context_menu_uninstall(reg=self.reg))
        for k in (DIR_KEY, BG_KEY, DRV_KEY):
            self.assertFalse(self.reg.has(k))
            self.assertFalse(self.reg.has(k + r"\command"))
        self.assertFalse(org_context_menu_installed(reg=self.reg))
        self.assertIsNone(org_context_menu_command(reg=self.reg))
        # command vor Elternschlüssel gelöscht
        order = [o.lower() for o in self.reg.delete_order]
        for k in (DIR_KEY, BG_KEY, DRV_KEY):
            kl = k.lower()
            self.assertLess(order.index(kl + "\\command"), order.index(kl))
        # Elternschlüssel (Software\Classes\Directory\shell) bleibt erhalten
        self.assertTrue(self.reg.has(r"Software\Classes\Directory\shell"))
        self.assertEqual(self.reg.open_handles, set())

    def test_uninstall_idempotent(self):
        self.assertTrue(org_context_menu_uninstall(reg=self.reg))   # nie installiert
        org_context_menu_install(EXE, SCRIPT, reg=self.reg)
        self.assertTrue(org_context_menu_uninstall(reg=self.reg))
        self.assertTrue(org_context_menu_uninstall(reg=self.reg))
        self.assertFalse(org_context_menu_installed(reg=self.reg))

    def test_uninstall_with_extra_subkeys(self):
        org_context_menu_install(EXE, SCRIPT, reg=self.reg)
        with self.reg.CreateKeyEx(self.reg.HKEY_CURRENT_USER, DIR_KEY + r"\DropTarget\x",
                                  0, self.reg.KEY_WRITE):
            pass
        self.assertTrue(org_context_menu_uninstall(reg=self.reg))
        self.assertFalse(self.reg.has(DIR_KEY))

    def test_install_fails_gracefully(self):
        bad = FakeWinreg(fail_create=True)
        self.assertFalse(org_context_menu_install(EXE, SCRIPT, reg=bad))
        self.assertFalse(org_context_menu_installed(reg=bad))

    def test_uninstall_fails_gracefully(self):
        org_context_menu_install(EXE, SCRIPT, reg=self.reg)
        self.reg.fail_delete = True
        self.assertFalse(org_context_menu_uninstall(reg=self.reg))

    def test_install_rejects_empty_exe(self):
        self.assertFalse(org_context_menu_install("", SCRIPT, reg=self.reg))
        self.assertFalse(org_context_menu_install(None, SCRIPT, reg=self.reg))
        self.assertEqual(self.reg.keys, {})

    def test_broken_reg_module(self):
        class Broken:
            pass
        self.assertFalse(org_context_menu_install(EXE, SCRIPT, reg=Broken()))
        self.assertFalse(org_context_menu_uninstall(reg=Broken()))
        self.assertFalse(org_context_menu_installed(reg=Broken()))
        self.assertIsNone(org_context_menu_command(reg=Broken()))

    @unittest.skipIf(os.name == "nt", "nur Nicht-Windows")
    def test_no_winreg_on_linux(self):
        self.assertFalse(org_context_menu_install(EXE, SCRIPT))
        self.assertFalse(org_context_menu_uninstall())
        self.assertFalse(org_context_menu_installed())
        self.assertIsNone(org_context_menu_command())
        self.assertFalse(org_context_menu_is_current(EXE, SCRIPT))

    def test_quote_helpers(self):
        self.assertEqual(org_shell_quote(r"C:\a b\x.exe"), r'"C:\a b\x.exe"')
        self.assertEqual(org_shell_quote("C:\\dir\\"), '"C:\\dir\\\\"')
        self.assertEqual(win_argv_split(org_shell_quote("C:\\dir\\") + " x"), ["C:\\dir\\", "x"])
        self.assertEqual(org_context_menu_build_command("a.exe", "", "%V"),
                         '"a.exe" --organize "%V"')

    def test_entries_layout(self):
        self.assertEqual(len(ORG_SHELL_REG_ENTRIES), 3)
        self.assertEqual([e[1] for e in ORG_SHELL_REG_ENTRIES], ["%1", "%V", "%1"])


# ---------------------------------------------------------------------------
class TestParseCli(unittest.TestCase):
    WIN_DIRS = {
        "c:\\", "d:\\", "c:\\users\\felix\\documents", "c:\\users\\jürgen müller\\eigene dateien",
        "c:\\users\\felix\\downloads", "\\\\nas\\share\\fotos", "c:\\users\\felix\\my docs",
    }

    def wdir(self, p):
        return p.lower() in self.WIN_DIRS

    def P(self, argv):
        r = org_parse_cli_folder(argv, is_dir=self.wdir)
        return None if r is None else str(r)

    def test_plain(self):
        self.assertEqual(self.P(["s.pyw", "--organize", r"C:\Users\Felix\Documents"]),
                         r"C:\Users\Felix\Documents")

    def test_stray_trailing_quote(self):
        self.assertEqual(self.P(["s.pyw", "--organize", 'C:\\Users\\Felix\\Documents"']),
                         r"C:\Users\Felix\Documents")

    def test_trailing_backslash(self):
        self.assertEqual(self.P(["s", "--organize", "C:\\Users\\Felix\\Documents\\"]),
                         r"C:\Users\Felix\Documents")

    def test_drive_root_quirk(self):
        self.assertEqual(self.P(["s", "--organize", 'C:"']), "C:\\")

    def test_drive_root_backslash(self):
        self.assertEqual(self.P(["s", "--organize", "D:\\"]), "D:\\")

    def test_drive_bare(self):
        self.assertEqual(self.P(["s", "--organize", "D:"]), "D:\\")

    def test_drive_root_quirk_with_following_text(self):
        self.assertEqual(self.P(["s", "--organize", 'C:" --background']), "C:\\")

    def test_fully_quoted_value(self):
        self.assertEqual(self.P(["s", "--organize", '"C:\\Users\\Felix\\Downloads"']),
                         r"C:\Users\Felix\Downloads")

    def test_umlauts_and_spaces(self):
        self.assertEqual(self.P(["s", "--organize", r"C:\Users\Jürgen Müller\Eigene Dateien"]),
                         r"C:\Users\Jürgen Müller\Eigene Dateien")

    def test_unquoted_split_with_spaces(self):
        self.assertEqual(self.P(["s", "--organize", r"C:\Users\Felix\My", "Docs"]),
                         r"C:\Users\Felix\My Docs")

    def test_equals_form(self):
        self.assertEqual(self.P(["s", r"--organize=C:\Users\Felix\Documents"]),
                         r"C:\Users\Felix\Documents")

    def test_unc_trailing_backslash(self):
        self.assertEqual(self.P(["s", "--organize", "\\\\nas\\share\\Fotos\\"]),
                         r"\\nas\share\Fotos")

    def test_missing_flag(self):
        self.assertIsNone(self.P(["s", r"C:\Users\Felix\Documents"]))

    def test_missing_value(self):
        self.assertIsNone(self.P(["s", "--organize"]))

    def test_next_is_option(self):
        self.assertIsNone(self.P(["s", "--organize", "--background"]))

    def test_nonexistent(self):
        self.assertIsNone(self.P(["s", "--organize", r"C:\gibt\es\nicht"]))

    def test_unsubstituted_placeholder(self):
        self.assertIsNone(self.P(["s", "--organize", "%1"]))
        self.assertIsNone(self.P(["s", "--organize", "%V"]))

    def test_empty_and_quotes_only(self):
        self.assertIsNone(self.P(["s", "--organize", ""]))
        self.assertIsNone(self.P(["s", "--organize", '"']))
        self.assertIsNone(self.P(["s", "--organize", '""']))

    def test_garbage_input(self):
        self.assertIsNone(org_parse_cli_folder(None))
        self.assertIsNone(org_parse_cli_folder([]))
        self.assertIsNone(org_parse_cli_folder(["--organize", "x\x00y"]))
        self.assertIsNone(org_parse_cli_folder([123, None, "--organize"]))

    def test_is_dir_raises(self):
        def boom(p):
            raise PermissionError("nein")
        self.assertIsNone(org_parse_cli_folder(["--organize", "C:\\x"], is_dir=boom))

    def test_case_insensitive_flag_and_other_args(self):
        self.assertEqual(self.P(["s", "--background", "--ORGANIZE", "C:\\Users\\Felix\\Downloads\\",
                                 "--rules", "r.txt"]), r"C:\Users\Felix\Downloads")

    def test_real_dirs_posix_and_relative(self):
        with tempfile.TemporaryDirectory() as td:
            sub = Path(td) / "Eigene Dateien Jürgen"
            sub.mkdir()
            r = org_parse_cli_folder(["s", "--organize", str(sub) + os.sep])
            self.assertEqual(r, sub)
            r = org_parse_cli_folder(["s", "--organize", str(sub) + '"'])
            self.assertEqual(r, sub)
            f = sub / "datei.txt"
            f.write_text("x", encoding="utf-8")
            self.assertIsNone(org_parse_cli_folder(["s", "--organize", str(f)]))
            old = os.getcwd()
            try:
                os.chdir(td)
                r = org_parse_cli_folder(["s", "--organize", sub.name])
                self.assertIsNotNone(r)
                self.assertTrue(r.is_absolute())
                self.assertEqual(os.path.realpath(str(r)), os.path.realpath(str(sub)))
            finally:
                os.chdir(old)

    @unittest.skipIf(os.name == "nt", "POSIX-file-URL")
    def test_file_url(self):
        with tempfile.TemporaryDirectory() as td:
            sub = Path(td) / "Mein Ordner"
            sub.mkdir()
            url = "file://" + str(sub).replace(" ", "%20")
            self.assertEqual(org_parse_cli_folder(["--organize", url]), sub)


# ---------------------------------------------------------------------------
class TestKnownFolders(unittest.TestCase):
    def test_resolver_used_with_correct_guid(self):
        seen = []

        def res(guid):
            seen.append(guid)
            return r"C:\Users\Felix\OneDrive\Dokumente"
        r = org_known_folder("documents", _resolver=res)
        self.assertEqual(str(r), r"C:\Users\Felix\OneDrive\Dokumente")
        self.assertEqual(seen, ["FDD39AD0-238F-46AF-ADB4-6C85480369C7"])

    def test_all_guids(self):
        expect = {
            "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
            "downloads": "374DE290-123F-4565-9164-39C4925E467B",
            "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
            "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
            "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
            "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
        }
        got = {}
        for name in expect:
            org_known_folder(name, _resolver=lambda g, n=name: got.__setitem__(n, g))
        self.assertEqual(got, expect)

    def test_fallback_english_names(self):
        home = Path.home()
        none = lambda g: None  # noqa: E731
        for name, eng in (("documents", "Documents"), ("downloads", "Downloads"),
                          ("pictures", "Pictures"), ("desktop", "Desktop"),
                          ("music", "Music"), ("videos", "Videos")):
            self.assertEqual(org_known_folder(name, _resolver=none), home / eng)

    def test_resolver_raises(self):
        def boom(g):
            raise OSError("kaputt")
        self.assertEqual(org_known_folder("pictures", _resolver=boom), Path.home() / "Pictures")

    def test_german_aliases(self):
        none = lambda g: None  # noqa: E731
        self.assertEqual(org_known_folder("Dokumente", _resolver=none), Path.home() / "Documents")
        self.assertEqual(org_known_folder("Bilder", _resolver=none), Path.home() / "Pictures")
        self.assertEqual(org_known_folder("  MUSIK ", _resolver=none), Path.home() / "Music")
        self.assertEqual(org_known_folder("Schreibtisch", _resolver=none), Path.home() / "Desktop")

    def test_unknown_name(self):
        self.assertEqual(org_known_folder("blabla"), Path.home())
        self.assertEqual(org_known_folder(None), Path.home())

    @unittest.skipIf(os.name == "nt", "XDG nur Nicht-Windows")
    def test_default_without_windows(self):
        with tempfile.TemporaryDirectory() as td:
            old_home = os.environ.get("HOME")
            old_xdg = os.environ.get("XDG_CONFIG_HOME")
            try:
                os.environ["HOME"] = td
                os.environ.pop("XDG_CONFIG_HOME", None)
                self.assertEqual(org_known_folder("documents"), Path(td) / "Documents")
                (Path(td) / ".config").mkdir()
                (Path(td) / "Dokumente").mkdir()
                (Path(td) / ".config" / "user-dirs.dirs").write_text(
                    '# Kommentar\nXDG_DOCUMENTS_DIR="$HOME/Dokumente"\n'
                    'XDG_MUSIC_DIR="$HOME/gibtsnicht"\n', encoding="utf-8")
                self.assertEqual(org_known_folder("documents"), Path(td) / "Dokumente")
                self.assertEqual(org_known_folder("music"), Path(td) / "Music")
                allf = org_known_folders()
                self.assertEqual(set(allf), {"documents", "downloads", "pictures", "desktop",
                                             "music", "videos"})
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home
                if old_xdg is not None:
                    os.environ["XDG_CONFIG_HOME"] = old_xdg

    def test_shget_not_called_off_windows(self):
        if os.name != "nt":
            self.assertIsNone(org_shell._org_shget_known_folder(
                "FDD39AD0-238F-46AF-ADB4-6C85480369C7"))


# ---------------------------------------------------------------------------
class TestNamingRules(unittest.TestCase):
    def test_prefixes(self):
        import ast
        src = Path(org_shell.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        bad = []
        for node in tree.body:
            names = []
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = [t.id for t in targets if isinstance(t, ast.Name)]
            for n in names:
                if not n.startswith(("org_", "Org", "ORG_", "_org_", "_ORG_")):
                    bad.append(n)
        self.assertEqual(bad, [])


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=1).result
    total = res.testsRun
    fails = len(res.failures) + len(res.errors)
    print("test_org_shell: %d Tests, %d Fehler, %d übersprungen (Python %s)"
          % (total, fails, len(res.skipped), sys.version.split()[0]))
    sys.exit(0 if fails == 0 else 1)
