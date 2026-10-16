# Tests für org_junk – Reste, Installer, Registry (Fake-winreg), leere Ordner.
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import org_junk as oj  # noqa: E402
from org_core import ORG_JUNK, ORG_KEEP, ORG_PROTECT, ORG_REVIEW, OrgFile, org_kind  # noqa: E402
from org_junk import (org_detect_junk, org_empty_dirs, org_installed_programs,  # noqa: E402
                      org_mark_installers, org_match_installer)

NOW = 1_800_000_000.0
H = 3600.0
D = 86400.0


def mk(rel, size=100, age=10 * D, in_project=False, category="", tier=""):
    p = Path("/nonexistent-root") / rel
    return OrgFile(path=p, rel=rel, size=size, mtime=NOW - age, kind=org_kind(p),
                   ext=p.suffix.lower(), depth=rel.count("/"), in_project=in_project,
                   category=category, tier=tier)


# ---------------------------------------------------------------------------
# Fake-winreg
# ---------------------------------------------------------------------------
class FakeKey(object):
    def __init__(self, values=None, subkeys=None):
        self.values = values or {}
        self.subkeys = subkeys or {}       # Name → FakeKey (Reihenfolge bleibt)
        self.closed = False


class FakeWinreg(object):
    HKEY_LOCAL_MACHINE = "HKLM"
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 0x20019
    KEY_WOW64_64KEY = 0x0100
    KEY_WOW64_32KEY = 0x0200

    def __init__(self, hives, fail_flags=False):
        self.hives = hives               # {"HKLM": FakeKey, "HKCU": FakeKey}
        self.fail_flags = fail_flags
        self.opened = []
        self.closed = 0

    def OpenKey(self, key, sub_key, reserved=0, access=0x20019):
        if self.fail_flags and access & self.KEY_WOW64_64KEY:
            raise OSError("Zugriff verweigert")
        node = self.hives[key] if isinstance(key, str) else key
        if node is None:
            raise FileNotFoundError(sub_key)
        for part in [p for p in sub_key.split("\\") if p]:
            nxt = None
            for name, child in node.subkeys.items():
                if name.lower() == part.lower():
                    nxt = child
                    break
            if nxt is None:
                raise FileNotFoundError(2, "Das System kann die Datei nicht finden", sub_key)
            node = nxt
        self.opened.append((key, sub_key, access))
        return node

    def EnumKey(self, key, index):
        names = list(key.subkeys)
        if index >= len(names):
            raise OSError(259, "Keine weiteren Daten verfügbar")
        return names[index]

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(2, "Wert nicht gefunden", name)
        v = key.values[name]
        return (v, 4 if isinstance(v, int) else 1)

    def CloseKey(self, key):
        self.closed += 1


def uninstall_tree(entries):
    """entries: {Unterschlüssel: {Wert: Daten}} → Baum unter Software\\...\\Uninstall"""
    uninstall = FakeKey(subkeys={k: FakeKey(values=v) for k, v in entries.items()})
    return uninstall


def hive_with(path, uninstall):
    node = uninstall
    for part in reversed(path.split("\\")):
        node = FakeKey(subkeys={part: node})
    return node


def merge(a, b):
    for k, v in b.subkeys.items():
        if k in a.subkeys:
            merge(a.subkeys[k], v)
        else:
            a.subkeys[k] = v
    return a


UNINST = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
UNINST_WOW = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"


def make_fake_registry():
    hklm64 = hive_with(UNINST, uninstall_tree({
        "{GIT}": {"DisplayName": "Git", "DisplayVersion": "2.43.0"},
        "{PY312}": {"DisplayName": "Python 3.12.1 (64-bit)"},
        "{VCREDIST}": {"DisplayName": "Microsoft Visual C++ 2015-2022 Redistributable (x64)",
                       "SystemComponent": 0},
        "{HIDDEN}": {"DisplayName": "Interne Komponente", "SystemComponent": 1},
        "{NONAME}": {"UninstallString": "x.exe"},
        "{EMPTY}": {"DisplayName": "   "},
        "{KB123}": {"DisplayName": "Sicherheitsupdate KB123", "ParentKeyName": "Office"},
        "{HOTFIX}": {"DisplayName": "Hotfix für Irgendwas", "ReleaseType": "Hotfix"},
        "7-Zip": {"DisplayName": "7-Zip 23.01 (x64)"},
    }))
    hklm32 = hive_with(UNINST_WOW, uninstall_tree({
        "Steam": {"DisplayName": "Steam"},
        "{GIT2}": {"DisplayName": "git"},          # Duplikat (Groß/Klein)
        "NoDisplay": {"DisplayName": 12345},       # falscher Typ
    }))
    hkcu = hive_with(UNINST, uninstall_tree({
        "Discord": {"DisplayName": "Discord"},
        "Spotify": {"DisplayName": "Spotify"},
        "Steam": {"DisplayName": "Steam"},         # Duplikat über Hives hinweg
    }))
    return FakeWinreg({"HKLM": merge(hklm64, hklm32), "HKCU": hkcu})


# ---------------------------------------------------------------------------
class TestDetectJunk(unittest.TestCase):
    def check_junk(self, rel, age=10 * D, size=100, expect=True):
        f = mk(rel, size=size, age=age)
        n = org_detect_junk([f], now=NOW)
        if expect:
            self.assertEqual(n, 1, rel)
            self.assertEqual(f.tier, ORG_JUNK, rel)
            self.assertEqual(f.category, "temp", rel)
            self.assertTrue(f.reasons, rel)
        else:
            self.assertEqual(n, 0, rel)
            self.assertNotEqual(f.tier, ORG_JUNK, rel)
        return f

    def test_office_owner_files(self):
        f = self.check_junk("~$benslauf.docx")
        self.assertIn("Office-Sperrdatei", f.reasons[0])
        self.check_junk("Ordner/~$Tabelle.xlsx")
        self.check_junk("~$benslauf.docx", age=10 * 60, expect=False)   # evtl. noch geöffnet

    def test_libreoffice_lock(self):
        self.check_junk(".~lock.Bewerbung.odt#")
        self.check_junk(".~lock.Bewerbung.odt", expect=False)            # ohne # kein Lock

    def test_wrl_tmp(self):
        f = self.check_junk("~WRL0005.tmp")
        self.assertIn("Word", f.reasons[0])

    def test_tmp_temp(self):
        self.check_junk("abc.tmp")
        self.check_junk("abc.TEMP")
        self.check_junk("abc.tmp", age=60, expect=False)
        self.check_junk("template.docx", expect=False)

    def test_partial_downloads_age(self):
        for ext in (".crdownload", ".part", ".partial", ".download", ".opdownload"):
            self.check_junk("film" + ext, age=25 * H)
            self.check_junk("film" + ext, age=23 * H, expect=False)
            self.check_junk("film" + ext, age=25 * H, size=0)
            # junger leerer Teil-Download: läuft vielleicht noch → nicht als leere Datei
            self.check_junk("film" + ext, age=1 * H, size=0, expect=False)

    def test_partial_future_mtime(self):
        self.check_junk("x.crdownload", age=-5 * D, expect=False)

    def test_cache_files(self):
        for n in ("Thumbs.db", "ehthumbs.db", ".DS_Store", "Fotos/thumbs.db"):
            self.check_junk(n, age=1)
        self.check_junk("thumbsup.db", expect=False)

    def test_dump(self):
        f = self.check_junk("MEMORY.DMP")
        self.assertIn("Absturz", f.reasons[0])

    def test_zero_byte(self):
        f = self.check_junk("leer.txt", size=0)
        self.assertEqual(f.reasons, ["Leere Datei (0 Byte)"])
        for n in (".gitkeep", ".keep", "pkg/__init__.py"):
            self.check_junk(n, size=0, expect=False)
        self.check_junk("klein.txt", size=1, expect=False)

    def test_in_project_never(self):
        files = [mk(n, size=0, in_project=True) for n in ("~$x.docx", "a.tmp", "Thumbs.db",
                                                          "leer.txt", "x.bak", "y.log")]
        files[-1].mtime = NOW - 200 * D
        self.assertEqual(org_detect_junk(files, now=NOW), 0)
        for f in files:
            self.assertEqual(f.tier, "")
            self.assertEqual(f.reasons, [])

    def test_backup_review(self):
        for n in ("Lebenslauf.docx.bak", "config.old", "X.BAK"):
            f = mk(n, category="cv" if n.startswith("Leb") else "document", tier=ORG_KEEP)
            cat = f.category
            self.assertEqual(org_detect_junk([f], now=NOW), 0)
            self.assertEqual(f.tier, ORG_REVIEW, n)
            self.assertEqual(f.category, cat, n)
            self.assertIn("Sicherungskopie", f.reasons)

    def test_backup_keeps_protect(self):
        f = mk("Zeugnis.pdf.bak", category="certificate", tier=ORG_PROTECT)
        org_detect_junk([f], now=NOW)
        self.assertEqual(f.tier, ORG_PROTECT)
        self.assertIn("Sicherungskopie", f.reasons)

    def test_empty_backup_not_junk(self):
        f = mk("leer.bak", size=0)
        self.assertEqual(org_detect_junk([f], now=NOW), 0)
        self.assertEqual(f.tier, ORG_REVIEW)

    def test_log_age(self):
        old = mk("app.log", age=91 * D)
        new = mk("app2.log", age=89 * D)
        self.assertEqual(org_detect_junk([old, new], now=NOW), 0)
        self.assertEqual(old.tier, ORG_REVIEW)
        self.assertIn("Alte Protokolldatei", old.reasons)
        self.assertEqual(new.tier, "")
        self.assertEqual(new.reasons, [])

    def test_normal_files_untouched(self):
        files = [mk(n) for n in ("Lebenslauf.pdf", "Urlaub.jpg", "setup.exe", "Notizen.txt",
                                 "tmp.docx", "part.pdf", "download.zip")]
        self.assertEqual(org_detect_junk(files, now=NOW), 0)
        self.assertTrue(all(f.tier == "" and not f.reasons for f in files))

    def test_count_and_default_now(self):
        import time as _t
        files = [mk("a.tmp"), mk("b.tmp"), mk("c.txt"), mk("Thumbs.db")]
        for f in files:
            f.mtime = _t.time() - 2 * D
        self.assertEqual(org_detect_junk(files), 3)    # now=None → time.time()
        self.assertEqual(org_detect_junk([]), 0)
        self.assertEqual(org_detect_junk(None), 0)

    def test_bad_input_no_raise(self):
        class Broken(object):
            in_project = False
        f = mk("Thumbs.db")
        f.mtime = "kaputt"
        self.assertEqual(org_detect_junk([Broken(), f, None], now=NOW), 1)


# ---------------------------------------------------------------------------
class TestInstalledPrograms(unittest.TestCase):
    def test_fake_registry(self):
        reg = make_fake_registry()
        progs = org_installed_programs(reg)
        self.assertIn("Git", progs)
        self.assertIn("Python 3.12.1 (64-bit)", progs)
        self.assertIn("Steam", progs)
        self.assertIn("Discord", progs)
        self.assertIn("Spotify", progs)
        self.assertIn("7-Zip 23.01 (x64)", progs)
        self.assertNotIn("Interne Komponente", progs)          # SystemComponent=1
        self.assertNotIn("Sicherheitsupdate KB123", progs)     # ParentKeyName
        self.assertNotIn("Hotfix für Irgendwas", progs)
        self.assertEqual(len([p for p in progs if p.lower() == "git"]), 1)
        self.assertEqual(progs.count("Steam"), 1)
        self.assertEqual(len(progs), len(set(p.lower() for p in progs)))
        # 64-Bit-Ansicht für HKLM angefordert
        self.assertTrue(any(k == "HKLM" and s == UNINST and a & reg.KEY_WOW64_64KEY
                            for k, s, a in reg.opened))
        self.assertGreater(reg.closed, 0)

    def test_flag_failure_fallback(self):
        reg = make_fake_registry()
        reg.fail_flags = True
        progs = org_installed_programs(reg)
        self.assertIn("Git", progs)

    def test_missing_hives(self):
        reg = FakeWinreg({"HKLM": FakeKey(), "HKCU": FakeKey()})
        self.assertEqual(org_installed_programs(reg), [])

    def test_broken_module(self):
        self.assertEqual(org_installed_programs(object()), [])

    def test_non_windows(self):
        if os.name != "nt":
            self.assertEqual(org_installed_programs(), [])


# ---------------------------------------------------------------------------
PROGRAMS = [
    "Steam", "Google Chrome", "Mozilla Firefox (x64 de)", "Microsoft Visual Studio Code (User)",
    "Git", "Python 3.12.1 (64-bit)", "Python 3.12.1 Core Interpreter (64-bit)",
    "Python Launcher", "Node.js", "VLC media player", "7-Zip 23.01 (x64)",
    "Notepad++ (64-bit x64)", "Discord", "Spotify", "OBS Studio", "Epic Games Launcher",
    "Google Drive", "Zoom Workplace (64-bit)", "WhatsApp", "PyCharm 2025.3.6.1",
    "OrcaSlicer", "balenaEtcher 1.18.11", "Raspberry Pi Imager", "Microsoft Teams",
    "Telegram Desktop", "Realtek Audio Driver", "Microsoft Visual C++ 2015-2022 Redistributable (x64)",
    "ShareX", "LibreOffice 7.6.4.1", "Microsoft OneDrive", "Brave", "GitHub Desktop",
    "WinRAR 6.24 (64-bit)", "TeamViewer", "KeePass Password Safe 2.55", "Audacity 3.4.2",
    "Mozilla Thunderbird (x64 de)", "Adobe Acrobat (64-bit)", "Opera Stable 105.0.4970.21",
    "Microsoft .NET SDK 8.0.100 (x64)",
]

MATCH_CASES = [
    ("SteamSetup.exe", "Steam"),
    ("SteamSetup (1).exe", "Steam"),
    ("ChromeSetup.exe", "Google Chrome"),
    ("chromesetup.exe", "Google Chrome"),
    ("Firefox Installer.exe", "Mozilla Firefox (x64 de)"),
    ("Firefox Setup 120.0.exe", "Mozilla Firefox (x64 de)"),
    ("VSCodeUserSetup-x64-1.85.1.exe", "Microsoft Visual Studio Code (User)"),
    ("Git-2.43.0-64-bit.exe", "Git"),
    ("python-3.12.1-amd64.exe", "Python 3.12.1 (64-bit)"),
    ("node-v20.10.0-x64.msi", "Node.js"),
    ("vlc-3.0.20-win64.exe", "VLC media player"),
    ("7z2301-x64.exe", "7-Zip 23.01 (x64)"),
    ("npp.8.6.Installer.x64.exe", "Notepad++ (64-bit x64)"),
    ("DiscordSetup.exe", "Discord"),
    ("SpotifySetup.exe", "Spotify"),
    ("SpotifyFullSetup.exe", "Spotify"),
    ("OBS-Studio-30.0-Full-Installer-x64.exe", "OBS Studio"),
    ("EpicInstaller-15.17.1.msi", "Epic Games Launcher"),
    ("GoogleDriveSetup.exe", "Google Drive"),
    ("Zoom_cm_fo42mnktZ9W8lhUNtsUo8aSmjM4rMvcNC_mHO5DHvMSuGZOH6Q@AP6UWBgbS05CGnUf-"
     "a0bde2fa6c2e8b0e_ZoomInstallerFull.exe", "Zoom Workplace (64-bit)"),
    ("ZoomInstallerFull.exe", "Zoom Workplace (64-bit)"),
    ("WhatsAppSetup.exe", "WhatsApp"),
    ("whatsappsetup.exe", "WhatsApp"),
    ("PyCharm-2025.3.exe", "PyCharm 2025.3.6.1"),
    ("OrcaSlicer_Windows_Installer_V2.2.0.exe", "OrcaSlicer"),
    ("balenaEtcher-Setup-1.18.11.exe", "balenaEtcher 1.18.11"),
    ("rpi-imager-1.8.5.exe", "Raspberry Pi Imager"),
    ("MSTeamsSetup_c_l_.exe", "Microsoft Teams"),
    ("tsetup-x64.4.12.2.exe", "Telegram Desktop"),
    ("vc_redist.x64.exe", "Microsoft Visual C++ 2015-2022 Redistributable (x64)"),
    ("ShareX-16.0.1-setup.exe", "ShareX"),
    ("LibreOffice_7.6.4_Win_x86-64.msi", "LibreOffice 7.6.4.1"),
    ("OneDriveSetup.exe", "Microsoft OneDrive"),
    ("BraveBrowserSetup-BRV013.exe", "Brave"),
    ("GitHubDesktopSetup-x64.exe", "GitHub Desktop"),
    ("winrar-x64-624d.exe", "WinRAR 6.24 (64-bit)"),
    ("TeamViewer_Setup_x64.exe", "TeamViewer"),
    ("KeePass-2.55-Setup.exe", "KeePass Password Safe 2.55"),
    ("audacity-win-3.4.2-64bit.exe", "Audacity 3.4.2"),
    ("Thunderbird Setup 115.5.0.exe", "Mozilla Thunderbird (x64 de)"),
    ("AcroRdrDC2300120064_de_DE.exe", "Adobe Acrobat (64-bit)"),
    ("OperaSetup.exe", "Opera Stable 105.0.4970.21"),
    ("dotnet-sdk-8.0.100-win-x64.exe", "Microsoft .NET SDK 8.0.100 (x64)"),
]

NO_MATCH_CASES = [
    "setup.exe", "Setup.exe", "install.exe", "installer.msi", "driver_x64.exe", "driver.exe",
    "invoice.exe", "Rechnung.exe", "random_tool_portable.exe", "update.exe", "Tool.exe",
    "python-3.11.5-amd64.exe",                 # andere Python-Version installiert
    "dotnet-sdk-6.0.400-win-x64.exe",          # andere .NET-Version
    "blender-4.0.2-windows-x64.msi",           # nicht installiert
    "GIMP-2.10.36-setup.exe",
    "3f9a7c1e-55b2-4c1d-9e0f-aa12bb34cc56.exe",
    "Signal-Setup.exe",
    "ChromeRemoteDesktopHost.msi",
    "Unbekannt_v2.exe",
    "",
]


class TestMatchInstaller(unittest.TestCase):
    def test_positive(self):
        for fn, expected in MATCH_CASES:
            self.assertEqual(org_match_installer(fn, PROGRAMS), expected, fn)

    def test_negative(self):
        for fn in NO_MATCH_CASES:
            self.assertIsNone(org_match_installer(fn, PROGRAMS), fn)

    def test_python_launcher_only(self):
        self.assertIsNone(org_match_installer("python-3.12.1-amd64.exe", ["Python Launcher"]))
        self.assertIsNone(org_match_installer("python-3.12.1-amd64.exe",
                                              ["Python 3.11.5 (64-bit)", "Python Launcher"]))

    def test_git_vs_github(self):
        self.assertIsNone(org_match_installer("Git-2.43.0-64-bit.exe", ["GitHub Desktop"]))
        self.assertIsNone(org_match_installer("GitHubDesktopSetup-x64.exe", ["Git"]))

    def test_generic_only_programs(self):
        self.assertIsNone(org_match_installer("driver_x64.exe", ["Realtek Audio Driver"]))
        self.assertIsNone(org_match_installer("Setup.exe", ["Setup Factory"]))
        self.assertIsNone(org_match_installer("TeamTool.exe", ["Team Tool Manager"]))

    def test_bad_inputs(self):
        self.assertIsNone(org_match_installer("SteamSetup.exe", []))
        self.assertIsNone(org_match_installer("SteamSetup.exe", None))
        self.assertIsNone(org_match_installer(None, PROGRAMS))
        self.assertEqual(org_match_installer("SteamSetup.exe", [None, 5, "Steam"]), "Steam")

    def test_counts(self):
        self.assertGreaterEqual(len(MATCH_CASES) + len(NO_MATCH_CASES), 40)


# ---------------------------------------------------------------------------
class TestMarkInstallers(unittest.TestCase):
    def test_marking(self):
        a = mk("Downloads/SteamSetup.exe", age=5 * D)
        b = mk("Downloads/blender-4.0.2-windows-x64.msi", age=100 * D)
        c = mk("Downloads/blender-4.1-windows-x64.msi", age=10 * D)
        d = mk("Downloads/Win11_23H2_German_x64.iso", age=400 * D)
        e = mk("Projekt/SteamSetup.exe", in_project=True)
        g = mk("Downloads/Lebenslauf.pdf")
        h = mk("Downloads/raspios.img", age=1 * D)
        i = mk("Downloads/app.apk", age=100 * D)
        dup = mk("Downloads/DiscordSetup (1).exe", category="duplicate", tier=ORG_JUNK)
        n = org_mark_installers([a, b, c, d, e, g, h, i, dup], PROGRAMS, now=NOW)
        self.assertEqual(n, 1)
        self.assertEqual((a.category, a.tier), ("installer", ORG_JUNK))
        self.assertIn("Programm „Steam“ ist bereits installiert", a.reasons)
        self.assertEqual((b.category, b.tier), ("installer", ORG_REVIEW))
        self.assertIn("Alte Installationsdatei (vor 100 Tagen geladen)", b.reasons)
        self.assertEqual((c.category, c.tier), ("installer", ORG_REVIEW))
        self.assertIn("Installationsdatei", c.reasons)
        self.assertEqual((d.category, d.tier), ("installer", ORG_REVIEW))
        self.assertIn("Datenträgerabbild", d.reasons)
        self.assertEqual((h.tier, h.reasons), (ORG_REVIEW, ["Datenträgerabbild"]))
        self.assertEqual(i.tier, ORG_REVIEW)
        self.assertEqual((e.category, e.tier, e.reasons), ("", "", []))
        self.assertEqual((g.category, g.tier), ("", ""))
        self.assertEqual((dup.category, dup.tier), ("duplicate", ORG_JUNK))

    def test_iso_never_junk_even_if_name_matches(self):
        f = mk("Steam.iso")
        self.assertEqual(org_mark_installers([f], ["Steam"], now=NOW), 0)
        self.assertEqual(f.tier, ORG_REVIEW)

    def test_age_threshold(self):
        f59 = mk("x_unknown_setup.exe", age=59 * D)
        f61 = mk("y_unknown_setup.exe", age=61 * D)
        org_mark_installers([f59, f61], [], now=NOW)
        self.assertEqual(f59.reasons, ["Installationsdatei"])
        self.assertEqual(f61.reasons, ["Alte Installationsdatei (vor 61 Tagen geladen)"])

    def test_empty(self):
        self.assertEqual(org_mark_installers([], PROGRAMS), 0)
        self.assertEqual(org_mark_installers(None, None), 0)

    def test_many_files_fast(self):
        import time as _t
        files = [mk("d/SteamSetup_%d.exe" % i) for i in range(2000)]
        t0 = _t.time()
        n = org_mark_installers(files, PROGRAMS * 5, now=NOW)
        self.assertEqual(n, 2000)
        self.assertLess(_t.time() - t0, 10.0)


# ---------------------------------------------------------------------------
class TestEmptyDirs(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orgjunk_"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def mkd(self, rel):
        p = self.root / rel
        p.mkdir(parents=True, exist_ok=True)
        return p

    def touch(self, rel, data=b"x"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def rels(self, paths):
        return [p.relative_to(self.root).as_posix() for p in paths]

    def test_nested_empty(self):
        self.mkd("A/B/C")
        self.mkd("A/D")
        self.mkd("E")
        self.touch("F/datei.txt")
        self.mkd("F/leer")
        res = self.rels(org_empty_dirs(self.root))
        self.assertEqual(set(res), {"A/B/C", "A/B", "A/D", "A", "E", "F/leer"})
        # tiefste zuerst
        self.assertEqual(res[0], "A/B/C")
        depth = [r.count("/") for r in res]
        self.assertEqual(depth, sorted(depth, reverse=True))
        self.assertLess(res.index("A/B"), res.index("A"))

    def test_root_not_included(self):
        self.assertEqual(org_empty_dirs(self.root), [])
        self.mkd("X")
        self.assertEqual(self.rels(org_empty_dirs(self.root)), ["X"])

    def test_file_deep_makes_parents_non_empty(self):
        self.touch("A/B/C/D/tief.txt")
        self.mkd("A/B/leer")
        self.assertEqual(self.rels(org_empty_dirs(self.root)), ["A/B/leer"])

    def test_zero_byte_file_counts(self):
        self.touch("A/leer.txt", b"")
        self.assertEqual(org_empty_dirs(self.root), [])

    def test_desktop_ini(self):
        self.touch("Angepasst/desktop.ini", b"[.ShellClassInfo]")
        self.mkd("Angepasst/leer")
        self.assertEqual(self.rels(org_empty_dirs(self.root)), ["Angepasst/leer"])

    def test_project_dirs(self):
        self.touch("Proj/package.json", b"{}")
        self.mkd("Proj/src/leer")
        self.mkd("Proj2/.git")
        self.mkd("Proj2/build")
        self.touch("Sln/App.sln")
        self.mkd("Sln/bin")
        self.assertEqual(org_empty_dirs(self.root), [])

    def test_skip_hidden_dirs(self):
        self.mkd("node_modules/x")
        self.mkd(".versteckt")
        self.mkd("Outer/AppData")
        self.mkd("Outer2/.cache")
        self.mkd("Custom Office Templates")
        res = self.rels(org_empty_dirs(self.root))
        self.assertEqual(res, [])
        # eigener skip-Satz: node_modules wird dann berücksichtigt
        res2 = set(self.rels(org_empty_dirs(self.root, skip={"appdata"})))
        self.assertEqual(res2, {"node_modules/x", "node_modules"})

    @unittest.skipUnless(hasattr(os, "symlink"), "keine Symlinks")
    def test_symlink_not_followed(self):
        target = Path(tempfile.mkdtemp(prefix="orgjunk_target_"))
        try:
            (target / "leer").mkdir()
            self.mkd("Holder")
            try:
                os.symlink(str(target), str(self.root / "Holder" / "link"))
            except (OSError, NotImplementedError):
                self.skipTest("Symlink nicht erlaubt")
            self.mkd("Leer")
            res = self.rels(org_empty_dirs(self.root))
            self.assertEqual(res, ["Leer"])        # Holder enthält Link → nicht leer
            self.assertTrue((target / "leer").exists())
        finally:
            shutil.rmtree(target, ignore_errors=True)

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "Rechte-Test nur ohne root")
    def test_unreadable_dir(self):
        p = self.mkd("Gesperrt")
        os.chmod(str(p), 0)
        try:
            self.assertEqual(org_empty_dirs(self.root), [])
        finally:
            os.chmod(str(p), 0o755)

    def test_bad_root(self):
        self.assertEqual(org_empty_dirs(self.root / "gibtsnicht"), [])
        self.assertEqual(org_empty_dirs(None), [])
        f = self.touch("datei.txt")
        self.assertEqual(org_empty_dirs(f), [])

    def test_str_root_and_many_dirs(self):
        for i in range(300):
            self.mkd("viele/%03d/a/b" % i)
        res = org_empty_dirs(str(self.root))
        self.assertEqual(len(res), 300 * 3 + 1)
        self.assertEqual(self.rels(res)[-1], "viele")


class TestNames(unittest.TestCase):
    def test_prefixes(self):
        import inspect
        src_names = [n for n, v in vars(oj).items()
                     if not n.startswith("__") and getattr(v, "__module__", "org_junk") == "org_junk"
                     and not inspect.ismodule(v)]
        imported = {"ORG_JUNK", "ORG_KEEP", "ORG_PROTECT", "ORG_REVIEW", "ORG_PROJECT_MARKERS",
                    "ORG_SKIP_DIRS", "Path", "annotations"}
        for n in src_names:
            if n in imported:
                continue
            self.assertTrue(n.startswith(("org_", "Org", "ORG_", "_org_", "_ORG_")), n)


if __name__ == "__main__":
    prog = unittest.main(exit=False, verbosity=1)
    r = prog.result
    print("org_junk: %d Tests, %d Fehler, %d Ausfälle, %d übersprungen"
          % (r.testsRun, len(r.errors), len(r.failures), len(r.skipped)))
    sys.exit(0 if r.wasSuccessful() else 1)
