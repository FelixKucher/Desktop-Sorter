# Adversariale Tests für org_shell.py – Fälle, an die der Erstautor nicht gedacht hat.
from __future__ import annotations

import ast
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import org_shell  # noqa: E402
from org_shell import (  # noqa: E402
    ORG_SHELL_REG_ENTRIES, org_context_menu_build_command, org_context_menu_command,
    org_context_menu_install, org_context_menu_installed, org_context_menu_is_current,
    org_context_menu_uninstall, org_known_folder, org_known_folders, org_parse_cli_folder,
    org_shell_quote, org_known_folder_key,
)
from test_org_shell import FakeWinreg, win_argv_split  # noqa: E402

EXE = r"C:\Users\Jürgen Müller\AppData\Local\Programs\Python\Python313\pythonw.exe"
SCRIPT = r"C:\Users\Jürgen Müller\Tools\DesktopSorter.pyw"
DIR_KEY = r"Software\Classes\Directory\shell\DesktopSorter"
BG_KEY = r"Software\Classes\Directory\Background\shell\DesktopSorter"
DRV_KEY = r"Software\Classes\Drive\shell\DesktopSorter"


class _BadStr:
    def __str__(self):
        raise ValueError("kaputtes __str__")

    __repr__ = __str__


class _FailNth(FakeWinreg):
    """Fake-winreg, das beim n-ten CreateKeyEx bzw. bei einem bestimmten Wert scheitert."""

    def __init__(self, fail_at=None, fail_value=None):
        FakeWinreg.__init__(self)
        self.fail_at = fail_at
        self.fail_value = fail_value
        self.creates = 0

    def CreateKeyEx(self, key, sub_key, reserved=0, access=None):
        self.creates += 1
        if self.fail_at is not None and self.creates == self.fail_at:
            raise PermissionError(5, "Zugriff verweigert (Richtlinie)")
        return FakeWinreg.CreateKeyEx(self, key, sub_key, reserved, access)

    def SetValueEx(self, key, value_name, reserved, typ, value):
        if self.fail_value is not None and value_name == self.fail_value:
            raise OSError(1010, "Registry kaputt")
        return FakeWinreg.SetValueEx(self, key, value_name, reserved, typ, value)


class _EmptyEnum(FakeWinreg):
    """Defektes reg-Modul: EnumKey liefert immer '' (würde Endlosrekursion auslösen)."""

    def EnumKey(self, key, index):
        if index < 3:
            return ""
        raise OSError(259, "Keine Daten")


def explorer_invoke(cmd_template, folder, placeholder="%1"):
    """Simuliert Explorer: Platzhalter ersetzen, dann Windows-argv-Zerlegung."""
    return win_argv_split(cmd_template.replace('"' + placeholder + '"', '"' + folder + '"'))


# ===========================================================================
class TestRegistryAdversarial(unittest.TestCase):
    def setUp(self):
        self.reg = FakeWinreg()
        self._orig_notify = org_shell._org_shell_notify
        org_shell._org_shell_notify = lambda: None

    def tearDown(self):
        org_shell._org_shell_notify = self._orig_notify

    # --- nie Ausnahme ---
    def test_bad_str_never_raises(self):
        b = _BadStr()
        self.assertFalse(org_context_menu_install(b, SCRIPT, reg=self.reg))
        self.assertTrue(org_context_menu_install(EXE, SCRIPT, label=b, reg=self.reg))
        self.assertEqual(self.reg.get(DIR_KEY), org_shell.ORG_SHELL_DEFAULT_LABEL)
        self.assertEqual(org_shell_quote(b), '""')
        self.assertIsInstance(org_context_menu_build_command(b, b, b), str)
        self.assertFalse(org_context_menu_is_current(b, b, reg=self.reg))
        self.assertTrue(org_context_menu_install(EXE, SCRIPT, icon=b, reg=self.reg))
        self.assertEqual(self.reg.get(DIR_KEY, "Icon"), EXE)

    def test_bytes_and_pathlike_inputs(self):
        self.assertEqual(org_shell_quote(b"C:\\a b\\x.exe"), '"C:\\a b\\x.exe"')
        self.assertTrue(org_context_menu_install(Path("/opt/py/bin/python3"),
                                                 Path("/home/jürgen/ds.pyw"), reg=self.reg))
        cmd = org_context_menu_command(reg=self.reg)
        self.assertNotIn("b'", cmd)
        self.assertNotIn("PosixPath", cmd)
        self.assertIn("/home/jürgen/ds.pyw", cmd)

    # --- Icon mit Komma (Explorer liest „pfad,index“) ---
    def test_icon_path_with_comma_gets_index(self):
        exe = r"C:\Users\Müller, Jürgen\Python\pythonw.exe"
        self.assertTrue(org_context_menu_install(exe, SCRIPT, reg=self.reg))
        self.assertEqual(self.reg.get(DIR_KEY, "Icon"), exe + ",0")
        # Befehl selbst bleibt korrekt zerlegbar
        argv = win_argv_split(org_context_menu_command(reg=self.reg))
        self.assertEqual(argv[0], exe)

    def test_icon_explicit_index_kept(self):
        ico = r"C:\Windows\System32\shell32.dll,-16769"
        org_context_menu_install(EXE, SCRIPT, icon=ico, reg=self.reg)
        self.assertEqual(self.reg.get(BG_KEY, "Icon"), ico)

    # --- halbe Installation darf keinen Zwischenzustand hinterlassen ---
    def test_partial_install_rolls_back(self):
        for n in range(1, 7):
            reg = _FailNth(fail_at=n)
            self.assertFalse(org_context_menu_install(EXE, SCRIPT, reg=reg), n)
            self.assertFalse(org_context_menu_installed(reg=reg), n)
            for k in (DIR_KEY, BG_KEY, DRV_KEY):
                self.assertFalse(reg.has(k), (n, k))
            self.assertEqual(reg.open_handles, set(), n)

    def test_failing_icon_value_rolls_back(self):
        reg = _FailNth(fail_value="Icon")
        self.assertFalse(org_context_menu_install(EXE, SCRIPT, reg=reg))
        self.assertFalse(org_context_menu_installed(reg=reg))
        self.assertIsNone(org_context_menu_command(reg=reg))

    # --- Deinstallation darf nichts Fremdes löschen ---
    def test_uninstall_leaves_foreign_keys(self):
        foreign = [r"Software\Classes\Directory\shell\DesktopSorterAlt\command",
                   r"Software\Classes\Directory\shell\cmd\command",
                   r"Software\Classes\Directory\shell\DesktopSorte\command",
                   r"Software\Classes\Drive\shell\find\command",
                   r"Software\Classes\Directory\Background\shell\Powershell7\command"]
        for f in foreign:
            with self.reg.CreateKeyEx(self.reg.HKEY_CURRENT_USER, f, 0, self.reg.KEY_WRITE) as k:
                self.reg.SetValueEx(k, "", 0, self.reg.REG_SZ, "fremd")
        org_context_menu_install(EXE, SCRIPT, reg=self.reg)
        self.assertTrue(org_context_menu_uninstall(reg=self.reg))
        for f in foreign:
            self.assertTrue(self.reg.has(f), f)
            self.assertEqual(self.reg.get(f), "fremd")
        self.assertFalse(self.reg.has(DIR_KEY))

    def test_uninstall_case_insensitive_existing_key(self):
        with self.reg.CreateKeyEx(self.reg.HKEY_CURRENT_USER,
                                  r"SOFTWARE\classes\directory\SHELL\desktopsorter\COMMAND",
                                  0, self.reg.KEY_WRITE):
            pass
        self.assertTrue(org_context_menu_installed(reg=self.reg))
        self.assertTrue(org_context_menu_uninstall(reg=self.reg))
        self.assertFalse(org_context_menu_installed(reg=self.reg))

    def test_uninstall_broken_enum_no_recursion(self):
        reg = _EmptyEnum()
        org_context_menu_install(EXE, SCRIPT, reg=reg)
        t = time.time()
        r = org_context_menu_uninstall(reg=reg)
        self.assertIsInstance(r, bool)
        self.assertLess(time.time() - t, 2.0)

    def test_is_current_checks_all_entries(self):
        org_context_menu_install(EXE, SCRIPT, reg=self.reg)
        self.assertTrue(org_context_menu_is_current(EXE, SCRIPT, reg=self.reg))
        # Hintergrund-Eintrag veraltet (z. B. alte Version / manuell geändert)
        with self.reg.CreateKeyEx(self.reg.HKEY_CURRENT_USER, BG_KEY + r"\command", 0,
                                  self.reg.KEY_WRITE) as k:
            self.reg.SetValueEx(k, "", 0, self.reg.REG_SZ,
                                org_context_menu_build_command(EXE, r"D:\alt\x.pyw", "%V"))
        self.assertFalse(org_context_menu_is_current(EXE, SCRIPT, reg=self.reg))
        # Laufwerks-Eintrag fehlt
        org_context_menu_install(EXE, SCRIPT, reg=self.reg)
        self.reg.DeleteKey(self.reg.HKEY_CURRENT_USER, DRV_KEY + r"\command")
        self.assertFalse(org_context_menu_is_current(EXE, SCRIPT, reg=self.reg))

    def test_label_control_chars(self):
        org_context_menu_install(EXE, SCRIPT, label="Auf\nräumen\x00\t!", reg=self.reg)
        self.assertEqual(self.reg.get(DIR_KEY), "Aufräumen!")
        org_context_menu_install(EXE, SCRIPT, label="   ", reg=self.reg)
        self.assertEqual(self.reg.get(DIR_KEY), org_shell.ORG_SHELL_DEFAULT_LABEL)
        org_context_menu_install(EXE, SCRIPT, label=None, reg=self.reg)
        self.assertEqual(self.reg.get(DIR_KEY), org_shell.ORG_SHELL_DEFAULT_LABEL)

    def test_reinstall_is_idempotent(self):
        org_context_menu_install(EXE, SCRIPT, reg=self.reg)
        snap = {k: dict(v["values"]) for k, v in self.reg.keys.items()}
        org_context_menu_install(EXE, SCRIPT, reg=self.reg)
        self.assertEqual({k: dict(v["values"]) for k, v in self.reg.keys.items()}, snap)

    def test_all_values_are_reg_sz_strings(self):
        org_context_menu_install(EXE, SCRIPT, reg=self.reg)
        for _path, data in self.reg.keys.items():
            for _ln, (_n, v, t) in data["values"].items():
                self.assertEqual(t, self.reg.REG_SZ)
                self.assertIsInstance(v, str)

    # --- Ende-zu-Ende: Registry → Explorer → argv → Parser ---
    def test_end_to_end_explorer_roundtrip(self):
        exe = "\\\\nas\\Tools Ü\\Python 3.13\\pythonw.exe"
        script = "C:\\Users\\Jürgen Müller\\😀 Projekte\\" + ("x" * 200) + "\\DesktopSorter.pyw"
        org_context_menu_install(exe, script, reg=self.reg)
        folders = [
            "C:\\", "D:\\",
            r"C:\Users\Jürgen Müller\Documents",
            r"C:\Users\Jürgen Müller\Bilder\Urlaub 2019 – Mallorca 🌴",
            r"C:\Daten\Rock & Roll\100% Zeugnisse",
            "\\\\fritz.box\\FRITZ.NAS\\Fotos Familie",
            "C:\\" + "\\".join(["Sehr langer Ordnername Äöü"] * 12),
        ]
        cmds = {
            "%1": self.reg.get(DIR_KEY + r"\command"),
            "%V": self.reg.get(BG_KEY + r"\command"),
        }
        drv = self.reg.get(DRV_KEY + r"\command")
        for ph, tmpl in cmds.items():
            for folder in folders:
                argv = explorer_invoke(tmpl, folder, ph)
                self.assertEqual(argv[0], exe)
                self.assertEqual(argv[1], script)
                want = folder.lower()
                got = org_parse_cli_folder(argv, is_dir=lambda p, w=want: p.lower() == w)
                self.assertIsNotNone(got, (ph, folder, argv))
                self.assertEqual(str(got).lower(), want)
        for root in ("C:\\", "E:\\"):
            argv = explorer_invoke(drv, root, "%1")
            got = org_parse_cli_folder(argv, is_dir=lambda p, r=root: p == r)
            self.assertEqual(str(got), root)


# ===========================================================================
class TestParseAdversarial(unittest.TestCase):
    def test_unquoted_split_prefers_longest_real_dir(self):
        # Falscher Ordner wäre fatal: „Eigene“ statt „Eigene Dateien“ würde aufgeräumt
        with tempfile.TemporaryDirectory() as td:
            short = Path(td) / "Eigene"
            full = Path(td) / "Eigene Dateien Jürgen"
            short.mkdir()
            full.mkdir()
            r = org_parse_cli_folder(["s", "--organize", str(short), "Dateien", "Jürgen"])
            self.assertEqual(r, full)
            # mit nachfolgender Option
            r = org_parse_cli_folder(["s", "--organize", str(short), "Dateien", "Jürgen",
                                      "--background"])
            self.assertEqual(r, full)

    def test_unquoted_split_windows_model(self):
        dirs = {r"c:\users\felix\my", r"c:\users\felix\my docs"}
        r = org_parse_cli_folder(["s", "--organize", r"C:\Users\Felix\My", "Docs"],
                                 is_dir=lambda p: p.lower() in dirs)
        self.assertEqual(str(r), r"C:\Users\Felix\My Docs")

    @unittest.skipIf(os.name == "nt", "nur Nicht-Windows: \" und Leerzeichen am Namensende")
    def test_posix_quote_and_trailing_space_in_name(self):
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td) / "a"
            parent.mkdir()
            q = Path(td) / 'a"b'
            q.mkdir()
            self.assertEqual(org_parse_cli_folder(["--organize", str(q)]), q)
            sp = Path(td) / "foo "
            (Path(td) / "foo").mkdir()
            sp.mkdir()
            self.assertEqual(org_parse_cli_folder(["--organize", str(sp)]), sp)
            # Stray-Quote-Reparatur funktioniert weiterhin
            self.assertEqual(org_parse_cli_folder(["--organize", str(parent) + '"']), parent)

    def test_long_path_prefix_drive_root(self):
        r = org_parse_cli_folder(["--organize", "\\\\?\\C:\\"],
                                 is_dir=lambda p: p == "\\\\?\\C:\\")
        self.assertEqual(str(r), "\\\\?\\C:\\")
        r = org_parse_cli_folder(["--organize", "\\\\?\\C:\\Users\\Felix\\"],
                                 is_dir=lambda p: p == "\\\\?\\C:\\Users\\Felix")
        self.assertIsNotNone(r)

    def test_forward_slashes_and_mixed(self):
        dirs = {"c:/users/felix/documents", "c:\\"}
        chk = lambda p: p.lower() in dirs  # noqa: E731
        self.assertEqual(str(org_parse_cli_folder(["--organize", "C:/Users/Felix/Documents/"],
                                                  is_dir=chk)), "C:/Users/Felix/Documents")
        self.assertEqual(str(org_parse_cli_folder(["--organize", "C:/"], is_dir=chk)), "C:\\")
        self.assertEqual(str(org_parse_cli_folder(["--organize", "C:\\\\\\"], is_dir=chk)),
                         "C:\\")

    def test_never_raises_on_weird_argv(self):
        cases = [
            "--organize C:\\x",               # String statt Liste
            b"--organize",
            [b"--organize", b"\xff\xfe"],
            [_BadStr(), "--organize", _BadStr()],
            ["--organize", "\udcff\udc80"],    # einzelne Surrogates
            ["--organize", "x" * 40000],
            ["--organize", "C:\\" + "a\\" * 5000],
            ["--organize", "\t \n"],
            ["--organize", "--"],
            ["--organize=", "C:\\"],
            ["--organize", "'"],
            ["--organize", "file://"],
            ["--organize", "file://server"],
            ["--organize", "file:///%ZZ%00"],
            42, object(), {"--organize": 1},
        ]
        for c in cases:
            try:
                r = org_parse_cli_folder(c)
            except Exception as e:  # pragma: no cover
                self.fail("Ausnahme für Fall %d: %s" % (cases.index(c), type(e).__name__))
            self.assertTrue(r is None or isinstance(r, Path), "Fall %d" % cases.index(c))
        self.assertIsNone(org_parse_cli_folder("--organize /tmp"))

    def test_bytes_argv_decoded(self):
        with tempfile.TemporaryDirectory() as td:
            r = org_parse_cli_folder([b"s", b"--organize", os.fsencode(td)])
            self.assertEqual(r, Path(td))

    def test_pathlike_argv(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(org_parse_cli_folder(["--organize", Path(td)]), Path(td))

    def test_emoji_and_umlaut_real_dir(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "Urlaub 2019 🌴 Größe – Fotos von Oma & Opa"
            d.mkdir()
            self.assertEqual(org_parse_cli_folder(["--organize", str(d)]), d)
            self.assertEqual(org_parse_cli_folder(["--organize", str(d) + os.sep + '"']), d)

    def test_dot_is_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            old = os.getcwd()
            try:
                os.chdir(td)
                r = org_parse_cli_folder(["--organize", "."])
                self.assertTrue(r.is_absolute())
                self.assertEqual(os.path.realpath(str(r)), os.path.realpath(td))
            finally:
                os.chdir(old)

    def test_file_not_dir_is_none(self):
        # Rechtsklick auf eine Datei (z. B. Lebenslauf.pdf) darf nicht deren Ordner liefern
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "Lebenslauf_final_v2 (1).pdf"
            f.write_bytes(b"%PDF-1.4")
            self.assertIsNone(org_parse_cli_folder(["--organize", str(f)]))
            self.assertIsNone(org_parse_cli_folder(["--organize", str(f) + '"']))

    def test_first_flag_wins_and_value_never_from_other_option(self):
        dirs = {"c:\\a", "c:\\b"}
        chk = lambda p: p.lower() in dirs  # noqa: E731
        self.assertEqual(str(org_parse_cli_folder(["--organize", "C:\\a", "--organize", "C:\\b"],
                                                  is_dir=chk)), "C:\\a")
        self.assertIsNone(org_parse_cli_folder(["--rules", "C:\\a", "--organize"], is_dir=chk))
        self.assertIsNone(org_parse_cli_folder(["--organizer", "C:\\a"], is_dir=chk))
        self.assertIsNone(org_parse_cli_folder(["-organize", "C:\\a"], is_dir=chk))

    def test_performance_60k_args(self):
        calls = []

        def chk(p):
            calls.append(p)
            return p == "C:\\Ziel"
        big = ["--x%d" % i for i in range(60000)] + ["--organize", "C:\\Ziel\\"]
        t = time.time()
        self.assertEqual(str(org_parse_cli_folder(big, is_dir=chk)), "C:\\Ziel")
        # 60k Positionsargumente nach dem Wert: Kandidaten bleiben begrenzt
        big2 = ["--organize", "C:\\nix"] + ["teil%d" % i for i in range(60000)]
        calls.clear()
        self.assertIsNone(org_parse_cli_folder(big2, is_dir=chk))
        self.assertLessEqual(len(calls), 80)
        self.assertLess(time.time() - t, 2.0)


# ===========================================================================
class TestKnownFolderAdversarial(unittest.TestCase):
    def test_resolver_returns_bytes_or_pathlike(self):
        self.assertEqual(str(org_known_folder("documents", _resolver=lambda g: b"/data/Dokumente")),
                         "/data/Dokumente")
        self.assertEqual(org_known_folder("pictures", _resolver=lambda g: Path("/x/Bilder")),
                         Path("/x/Bilder"))

    def test_resolver_returns_junk(self):
        home = Path.home()
        for junk in ("", "   ", "\x00", 0, False, b""):
            self.assertEqual(org_known_folder("music", _resolver=lambda g, j=junk: j),
                             home / "Music", repr(junk))

    def test_bad_name_types(self):
        for n in (_BadStr(), 123, b"documents", ["documents"], "", "   "):
            r = org_known_folder(n, _resolver=lambda g: None)
            self.assertIsInstance(r, Path)

    def test_old_german_aliases(self):
        none = lambda g: None  # noqa: E731
        self.assertEqual(org_known_folder("Eigene Bilder", _resolver=none),
                         Path.home() / "Pictures")
        self.assertEqual(org_known_folder("EIGENE DOKUMENTE", _resolver=none),
                         Path.home() / "Documents")
        self.assertEqual(org_known_folder_key("Fotos"), "pictures")
        self.assertEqual(org_known_folder_key("Downloads "), "downloads")
        self.assertEqual(org_known_folder_key("Lebenslauf"), "")

    def test_known_folders_all_paths(self):
        d = org_known_folders(_resolver=lambda g: None)
        self.assertEqual(len(d), 6)
        for v in d.values():
            self.assertIsInstance(v, Path)

    @unittest.skipIf(os.name == "nt", "XDG nur Nicht-Windows")
    def test_xdg_relative_and_braces_and_garbage(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as cwd:
            old_home = os.environ.get("HOME")
            old_xdg = os.environ.get("XDG_CONFIG_HOME")
            old_cwd = os.getcwd()
            try:
                os.environ["HOME"] = td
                os.environ.pop("XDG_CONFIG_HOME", None)
                (Path(td) / ".config").mkdir()
                (Path(td) / "Bilder").mkdir()
                (Path(td) / "Musik").mkdir()
                # gleichnamiger Ordner im cwd darf NICHT gewinnen
                (Path(cwd) / "Bilder").mkdir()
                os.chdir(cwd)
                (Path(td) / ".config" / "user-dirs.dirs").write_bytes(
                    b"\xff\xfe\x00garbage\n"
                    b'XDG_PICTURES_DIR="Bilder"\n'
                    b'XDG_MUSIC_DIR="${HOME}/Musik"\n'
                    b'XDG_VIDEOS_DIR="$HOME"\n')
                self.assertEqual(org_known_folder("pictures"), Path(td) / "Bilder")
                self.assertEqual(org_known_folder("music"), Path(td) / "Musik")
                # $HOME selbst ist kein Videos-Ordner → Fallback
                self.assertEqual(org_known_folder("videos"), Path(td) / "Videos")
            finally:
                os.chdir(old_cwd)
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home
                if old_xdg is not None:
                    os.environ["XDG_CONFIG_HOME"] = old_xdg

    def test_shget_guarded_off_windows(self):
        if os.name != "nt":
            self.assertIsNone(org_shell._org_shget_known_folder("nicht-mal-eine-guid"))
            self.assertIsNone(org_shell._org_shget_known_folder(None))


# ===========================================================================
class TestPasteSafety(unittest.TestCase):
    """Das Modul wird in eine große Datei kopiert: keine Namenskollisionen, py3.9-tauglich."""

    @staticmethod
    def _top_names(src):
        names = set()
        for node in ast.parse(src).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
        return names

    def test_no_collisions_with_other_modules_and_main_file(self):
        mine = self._top_names(Path(org_shell.__file__).read_text(encoding="utf-8"))
        bad = [n for n in mine
               if not n.startswith(("org_", "Org", "ORG_", "_org_", "_ORG_"))]
        self.assertEqual(bad, [])
        others = {}
        for p in Path(HERE).glob("org_*.py"):
            if p.name == "org_shell.py":
                continue
            for n in self._top_names(p.read_text(encoding="utf-8")):
                others.setdefault(n, p.name)
        main = Path(HERE).resolve().parents[1] / "DesktopSorter.pyw"
        if main.exists():
            # Nur den App-Teil vergleichen: die eingebaute Engine (also auch org_shell
            # selbst) und die Assistenten-UI werden von dev/assemble.py herausgeschnitten.
            sys.path.insert(0, str(Path(HERE).resolve().parent))
            import assemble
            head, mid, rest = assemble.split_app(main.read_text(encoding="utf-8"))
            app_only = head + mid + 'STYLE = """' + rest
            for n in self._top_names(app_only):
                others.setdefault(n, main.name)
        clash = sorted(n for n in mine if n in others)
        self.assertEqual(clash, [], clash)

    def test_no_new_syntax(self):
        src = Path(org_shell.__file__).read_text(encoding="utf-8")
        self.assertIn("from __future__ import annotations", src)
        tree = ast.parse(src)
        match_cls = getattr(ast, "Match", None)
        for node in ast.walk(tree):
            if match_cls is not None:
                self.assertNotIsInstance(node, match_cls)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "isinstance":
                self.assertNotIsInstance(node.args[1], ast.BinOp)
            if isinstance(node, ast.keyword):
                self.assertNotEqual(node.arg, "strict")
        for bad in (".removeprefix(", ".removesuffix("):
            self.assertNotIn(bad, src)


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=1).result
    total = res.testsRun
    fails = len(res.failures) + len(res.errors)
    print("test_org_shell_adversarial: %d Tests, %d Fehler, %d übersprungen (Python %s)"
          % (total, fails, len(res.skipped), sys.version.split()[0]))
    sys.exit(0 if fails == 0 else 1)
