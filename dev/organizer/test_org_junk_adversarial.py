# Adversariale Tests für org_junk – Fälle, an die der Erstentwickler nicht dachte:
# falsche "Müll"-Urteile über wichtige Dateien, Windows-Pfade, Umlaute, Grenzwerte,
# kaputte Eingaben, Leistung bei 60 000 Dateien.
from __future__ import annotations

import ast
import os
import shutil
import sys
import tempfile
import time
import unicodedata
import unittest
from pathlib import Path, PureWindowsPath

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import org_junk as oj  # noqa: E402
from org_core import ORG_JUNK, ORG_KEEP, ORG_PROTECT, ORG_REVIEW, OrgFile, org_kind  # noqa: E402
from org_junk import (org_detect_junk, org_empty_dirs, org_installed_programs,  # noqa: E402
                      org_mark_installers, org_match_installer)

NOW = 1_800_000_000.0
H = 3600.0
D = 86400.0


def mk(rel, size=100, age=10 * D, in_project=False, category="", tier="", root="/nonexistent-root"):
    p = Path(root) / rel
    return OrgFile(path=p, rel=rel, size=size, mtime=NOW - age, kind=org_kind(p),
                   ext=p.suffix.lower(), depth=rel.count("/"), in_project=in_project,
                   category=category, tier=tier)


# ---------------------------------------------------------------------------
class TestImportantNeverJunk(unittest.TestCase):
    """Schlimmster Fehler: wichtige Datei als Müll markiert."""

    IMPORTANT = [
        "Lebenslauf_final_v2 (1).docx", "Lebenslauf - Kopie.pdf", "CV_Felix_Kucher_EN.pdf",
        "Zeugnis Abitur 2009.pdf", "Arbeitszeugnis_Firma_GmbH.pdf", "Geburtsurkunde.pdf",
        "Heiratsurkunde (Scan).jpg", "IMG_20230812_Urlaub_Italien.jpg", "Oma & Opa 💕.jpg",
        "Steuererklärung 2023.pdf", "Mietvertrag Wohnung.pdf", "Gehaltsabrechnung 01-2024.pdf",
        "Personalausweis_Rückseite.png", "Arztbrief_Dr_Müller.pdf",
        "Temporäre Arbeiten.docx", "tmp_Lebenslauf.docx", "temp_notes.txt", "Vorlage.dotx",
        "Kapitel.part1.rar", "Logbuch.log.pdf", "Thumbs.db.pdf", "Bericht.tmp.pdf",
        "Backup Fotos.zip", "partitur.pdf", "download.pdf", "Lock Screen.png", "~Notizen.txt",
        "$Rechnung.pdf", "Dump Truck Foto.jpg", "memory.dmp.jpg", "WRL Bericht.tmp.docx",
        "Urlaub " + "x" * 220 + ".jpg", unicodedata.normalize("NFD", "Zeugnisse Übersicht.pdf"),
        "家族の写真.jpg", "Hochzeit 👰🤵 2019.mp4",
    ]

    def test_important_names_untouched(self):
        files = [mk(n, size=250_000, age=400 * D, category="cv", tier=ORG_PROTECT)
                 for n in self.IMPORTANT]
        self.assertEqual(org_detect_junk(files, now=NOW), 0)
        for f in files:
            self.assertEqual(f.tier, ORG_PROTECT, f.name)
            self.assertEqual(f.category, "cv", f.name)
            self.assertEqual(f.reasons, [], f.name)

    def test_backup_of_protected_stays_protected(self):
        for n in ("Lebenslauf.docx.bak", "Zeugnis.pdf.old", "Familienfotos.BAK"):
            f = mk(n, size=10, category="certificate", tier=ORG_PROTECT)
            org_detect_junk([f], now=NOW)
            self.assertEqual((f.tier, f.category), (ORG_PROTECT, "certificate"), n)

    def test_large_owner_file_name_is_not_junk(self):
        # Eine 2-MB-Datei "~$Projekt.docx" ist keine Sperrdatei (die sind ~162 Byte)
        f = mk("~$Projekt.docx", size=2_000_000)
        self.assertEqual(org_detect_junk([f], now=NOW), 0)
        self.assertEqual(f.tier, ORG_REVIEW)
        small = mk("~$Projekt.docx", size=162)
        self.assertEqual(org_detect_junk([small], now=NOW), 1)
        big_lock = mk(".~lock.Bewerbung.odt#", size=500_000)
        self.assertEqual(org_detect_junk([big_lock], now=NOW), 0)

    def test_short_name_owner_file_with_keyword(self):
        # Word benennt kurze Dateien "~$Zeugnis.docx" – das ist trotzdem eine Sperrdatei
        f = mk("~$Zeugnis.docx", size=162, category="certificate", tier=ORG_PROTECT)
        self.assertEqual(org_detect_junk([f], now=NOW), 1)


class TestDumpFiles(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orgjunk_dmp_"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, data):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return OrgFile(path=p, rel=rel, size=len(data), mtime=NOW - 30 * D, kind=org_kind(p),
                       ext=p.suffix.lower())

    def test_real_minidump_is_junk(self):
        f = self.write("chrome.exe.1234.dmp", b"MDMP\x93\xa7\x00\x00" + b"\0" * 100)
        self.assertEqual(org_detect_junk([f], now=NOW), 1)
        k = self.write("MEMORY.DMP", b"PAGEDU64" + b"\0" * 100)
        self.assertEqual(org_detect_junk([k], now=NOW), 1)

    def test_database_export_dmp_not_junk(self):
        # Oracle-Export (exp/expdp) hat Endung .dmp, ist aber Nutzdaten
        f = self.write("Kunden_Export_2023.dmp", b"\x03\x03iEXPORT:V19.00.00\nDSYSTEM" + b"x" * 100)
        self.assertEqual(org_detect_junk([f], now=NOW), 0)
        self.assertEqual(f.tier, ORG_REVIEW)
        self.assertNotEqual(f.category, "temp")

    def test_unreadable_dmp_uses_name(self):
        self.assertEqual(org_detect_junk([mk("MEMORY.DMP")], now=NOW), 1)
        self.assertEqual(org_detect_junk([mk("Mini101523-01.dmp")], now=NOW), 1)
        self.assertEqual(org_detect_junk([mk("CrashDumps/app.dmp")], now=NOW), 1)
        f = mk("Datenbank/kunden.dmp")
        self.assertEqual(org_detect_junk([f], now=NOW), 0)
        self.assertEqual(f.tier, ORG_REVIEW)

    def test_empty_dmp_junk(self):
        f = self.write("leer.dmp", b"")
        self.assertEqual(org_detect_junk([f], now=NOW), 1)


class TestJunkEdgeCases(unittest.TestCase):
    def test_nan_inf_mtime_lock_and_partial_left_alone(self):
        for mt in (float("nan"), float("inf"), float("-inf"), 1e308):
            for n in ("~$x.docx", "film.crdownload", "a.tmp"):
                f = mk(n)
                f.mtime = mt
                org_detect_junk([f], now=NOW)       # darf nicht werfen
                if mt != mt or mt == float("inf"):  # NaN / Zukunft: evtl. in Benutzung
                    self.assertNotEqual(f.tier, ORG_JUNK, (n, mt))
        f = mk("film.part")
        f.mtime = float("nan")
        self.assertEqual(org_detect_junk([f], now=NOW), 0)

    def test_partial_boundary_exact_24h(self):
        self.assertEqual(org_detect_junk([mk("a.crdownload", age=24 * H)], now=NOW), 0)
        self.assertEqual(org_detect_junk([mk("a.crdownload", age=24 * H + 1)], now=NOW), 1)

    def test_log_boundary_exact_90d(self):
        f = mk("a.log", age=90 * D)
        org_detect_junk([f], now=NOW)
        self.assertEqual(f.tier, "")

    def test_lock_boundary_1h(self):
        self.assertEqual(org_detect_junk([mk("~$a.docx", age=H - 1)], now=NOW), 0)
        self.assertEqual(org_detect_junk([mk("~$a.docx", age=H)], now=NOW), 1)

    def test_windows_path_objects(self):
        f = mk("x")
        f.path = PureWindowsPath(r"C:\Users\Felix\Documents\~$benslauf.docx")
        f.ext = ".docx"
        self.assertEqual(org_detect_junk([f], now=NOW), 1)
        g = mk("x")
        g.path = r"C:\Users\Felix\Bilder\Thumbs.db"        # str statt Path
        g.ext = ".db"
        self.assertEqual(org_detect_junk([g], now=NOW), 1)
        h = mk("x")
        h.path = "D:/Fotos/Urlaub.jpg"
        h.ext = ".jpg"
        self.assertEqual(org_detect_junk([h], now=NOW), 0)

    def test_uppercase_and_mixed_case_extensions(self):
        for n in ("FILM.CRDOWNLOAD", "Film.Part", "X.TmP", "THUMBS.DB", ".ds_store", "MINI0101-01.DMP"):
            self.assertEqual(org_detect_junk([mk(n)], now=NOW), 1, n)

    def test_ext_field_missing(self):
        f = mk("abc.tmp")
        f.ext = ""
        self.assertEqual(org_detect_junk([f], now=NOW), 1)
        g = mk("abc.tmp")
        g.ext = None
        self.assertEqual(org_detect_junk([g], now=NOW), 1)

    def test_weird_sizes(self):
        for s in (None, "abc", -1, float("nan"), 10 ** 30):
            f = mk("Datei.txt")
            f.size = s
            org_detect_junk([f], now=NOW)
            self.assertNotEqual(f.tier, ORG_JUNK, repr(s))

    def test_idempotent(self):
        files = [mk("a.tmp"), mk("Thumbs.db"), mk("x.bak")]
        self.assertEqual(org_detect_junk(files, now=NOW), 2)
        self.assertEqual(org_detect_junk(files, now=NOW), 2)
        for f in files:
            self.assertEqual(len(f.reasons), len(set(f.reasons)))
            self.assertEqual(len(f.reasons), 1)

    def test_iterables_and_garbage(self):
        self.assertEqual(org_detect_junk(iter([mk("a.tmp")]), now=NOW), 1)
        self.assertEqual(org_detect_junk([1, "x", object(), None], now=NOW), 0)
        self.assertEqual(org_detect_junk([mk("a.tmp")], now="kaputt"), 0)

    def test_duplicate_marked_then_junk_keeps_dup_of(self):
        f = mk("Kopie/a.tmp", category="duplicate", tier=ORG_JUNK)
        f.dup_of = "a.tmp"
        org_detect_junk([f], now=NOW)
        self.assertEqual(f.dup_of, "a.tmp")
        self.assertEqual(f.tier, ORG_JUNK)

    def test_perf_60k(self):
        names = ["Dokument_%d.pdf", "~$Datei%d.docx", "IMG_%d.jpg", "x%d.tmp", "Thumbs.db",
                 "a%d.log", "b%d.bak", "leer%d.txt"]
        files = []
        for i in range(60_000):
            n = names[i % len(names)]
            files.append(mk("Ordner%d/%s" % (i % 97, n % i if "%d" in n else n),
                            size=0 if "leer" in n else 500, age=(i % 200) * D))
        t0 = time.perf_counter()
        n = org_detect_junk(files, now=NOW)
        dt = time.perf_counter() - t0
        self.assertGreater(n, 20_000)
        self.assertLess(dt, 3.0)
        self.assertFalse(any(f.tier == ORG_JUNK for f in files if f.name.endswith(".pdf")))


# ---------------------------------------------------------------------------
REAL_PROGRAMS = [
    "Zoom Workplace (64-bit)", "Opera Stable 105.0.4970.21", "Steam", "Git",
    "Python 3.11.5 (64-bit)", "Python Launcher", "WISO Steuer 2023",
    "Microsoft Visual C++ 2015-2022 Redistributable (x64) - 14.38.33130",
    "Microsoft Edge", "Microsoft Edge WebView2 Runtime", "Minecraft Launcher",
    "The Witcher 3: Wild Hunt", "VLC media player", "Discord", "Übersetzer Pro 3",
    "NVIDIA Grafiktreiber 546.33", "Signal 6.40.0", "Spotify", "HP Smart",
    "Mozilla Firefox (x64 de)", "Mozilla Maintenance Service", "Google Chrome",
    "Microsoft 365 - de-de", "Adobe Acrobat Reader (64-bit)", "Logitech G HUB",
]


class TestInstallerFalsePositives(unittest.TestCase):
    def none(self, fn, progs=REAL_PROGRAMS):
        self.assertIsNone(org_match_installer(fn, progs), fn)

    def test_two_letter_product_suffix(self):
        self.none("ZoomIt.exe")                  # Sysinternals ZoomIt ≠ Zoom
        self.none("ZoomIt64.exe")
        self.none("OperaGXSetup.exe")            # Opera GX ≠ Opera
        self.none("SteamVR_Setup.exe")

    def test_umlaut_words_not_dropped(self):
        self.none("Steam für Jörg.exe")
        self.none("Git Übung.exe")
        self.none("Spotify Überweisung.exe")
        self.assertEqual(org_match_installer("Übersetzer-Pro-Setup.exe", REAL_PROGRAMS),
                         "Übersetzer Pro 3")
        nfd = unicodedata.normalize("NFD", "Übersetzer-Pro-Setup.exe")
        self.assertEqual(org_match_installer(nfd, REAL_PROGRAMS), "Übersetzer Pro 3")

    def test_year_products(self):
        self.none("WISO_Steuer2024_Setup.exe")
        self.none("WISO Steuer 2024.exe")
        self.assertEqual(org_match_installer("WISO_Steuer_2023_Setup.exe", REAL_PROGRAMS),
                         "WISO Steuer 2023")
        self.none("vcredist_x64_2010.exe")       # VC++ 2010 läuft neben 2015-2022
        self.assertIsNotNone(org_match_installer("vc_redist.x64.exe", REAL_PROGRAMS))

    def test_python_versions_without_dash(self):
        self.none("python3.12-setup.exe")
        self.none("python312.exe")
        self.assertEqual(org_match_installer("python3.11.5.exe", REAL_PROGRAMS),
                         "Python 3.11.5 (64-bit)")
        self.none("python-3.12.1-amd64.exe", ["Python Launcher"])

    def test_other_products_same_vendor(self):
        self.none("BetterDiscord-Windows.exe")
        self.none("SteamCMD.exe")
        self.none("steamcmd.exe")
        self.none("FirefoxPortable_120.0_German.paf.exe", ["Mozilla Maintenance Service"])
        self.none("ChromeRemoteDesktopHost.msi")
        self.none("GitKraken-Setup.exe")
        self.none("Signalverarbeitung_Uebung.exe")
        self.none("Lebenslauf Steam.exe")

    def test_generic_and_hashy(self):
        for fn in ("setup (1).exe", "Setup - Kopie.exe", "install_x64_de.exe", "Unbenannt.exe",
                   "a.exe", "ab.exe", "x64.exe", "1.0.0.exe", "~$setup.exe", ".exe", "...exe",
                   "Treiber_Setup.exe", "NVIDIA-Treiber.exe", "Microsoft.exe", "Google.exe",
                   "3f9a7c1e55b24c1d9e0faa12bb34cc56.exe", "AbCdEfGhIj.exe"):
            self.none(fn)

    def test_windows_paths_and_copies(self):
        self.assertEqual(org_match_installer(r"C:\Users\Felix\Downloads\SteamSetup.exe",
                                             REAL_PROGRAMS), "Steam")
        self.assertEqual(org_match_installer(r"\\NAS\share\DiscordSetup (3).exe", REAL_PROGRAMS),
                         "Discord")
        self.assertEqual(org_match_installer("SteamSetup – Kopie.exe", REAL_PROGRAMS), "Steam")
        self.assertEqual(org_match_installer("Steam\u00a0Setup.exe", REAL_PROGRAMS), "Steam")

    def test_bad_inputs_no_raise(self):
        for fn in (None, 5, b"SteamSetup.exe", "\x00\x00.exe", "x" * 10000 + ".exe",
                   "💾💾Setup.exe", "Steam" * 2000 + ".exe"):
            org_match_installer(fn, REAL_PROGRAMS)
        for progs in (None, [], [None], "Steam", 42, [b"Steam"], iter(["Steam"]), {"Steam": 1}):
            org_match_installer("SteamSetup.exe", progs)

    def test_huge_program_list_and_many_names_fast(self):
        progs = ["Programm %d Werkzeug%d" % (i, i) for i in range(2000)] + REAL_PROGRAMS
        t0 = time.perf_counter()
        hits = 0
        for i in range(5000):
            if org_match_installer("Tool%dSetup-x64-%d.%d.exe" % (i, i, i), progs):
                hits += 1
        self.assertEqual(org_match_installer("SteamSetup.exe", progs), "Steam")
        self.assertLess(time.perf_counter() - t0, 10.0)
        self.assertEqual(hits, 0)


class TestMarkInstallersSafety(unittest.TestCase):
    def test_portable_program_folder_not_junk(self):
        # Spiel/portables Programm: .exe neben DLLs ist das Programm selbst
        game = [mk("Spiele/Witcher3/bin/witcher3.exe"), mk("Spiele/Witcher3/bin/bink2w64.dll"),
                mk("Tools/vlc-3.0.20/vlc.exe"), mk("Tools/vlc-3.0.20/libvlc.dll"),
                mk("Tools/vlc-3.0.20/libvlccore.dll"), mk("Minecraft/Minecraft.exe"),
                mk("Minecraft/lwjgl.dll")]
        n = org_mark_installers(game, REAL_PROGRAMS, now=NOW)
        self.assertEqual(n, 0)
        for f in game:
            self.assertNotEqual(f.tier, ORG_JUNK, f.rel)
            if f.ext == ".exe":
                self.assertNotEqual(f.category, "installer", f.rel)   # nicht wegsortieren

    def test_installer_next_to_stray_dll_still_detected(self):
        files = [mk("Downloads/SteamSetup.exe"), mk("Downloads/msvcp140.dll"),
                 mk("Downloads/DiscordSetup.exe")]
        self.assertEqual(org_mark_installers(files, REAL_PROGRAMS, now=NOW), 2)

    def test_installer_without_dll_still_junk(self):
        f = mk("Downloads/vlc-3.0.20-win64.exe")
        self.assertEqual(org_mark_installers([f], REAL_PROGRAMS, now=NOW), 1)

    def test_protect_never_downgraded(self):
        f = mk("Steuer/WISO_Steuer_2023_Setup.exe", category="tax", tier=ORG_PROTECT)
        self.assertEqual(org_mark_installers([f], REAL_PROGRAMS, now=NOW), 0)
        self.assertEqual((f.tier, f.category), (ORG_PROTECT, "tax"))

    def test_bad_mtime_installer_gets_reason(self):
        for mt in (float("-inf"), float("nan"), "x", None):
            f = mk("Downloads/unknown_tool_setup.exe")
            f.mtime = mt
            org_mark_installers([f], [], now=NOW)
            self.assertEqual(f.tier, ORG_REVIEW)
            self.assertTrue(f.reasons, repr(mt))

    def test_msi_in_dll_folder_still_matched(self):
        files = [mk("Downloads/Pack/node-v20.10.0-x64.msi"), mk("Downloads/Pack/x.dll")]
        self.assertEqual(org_mark_installers(files, ["Node.js"], now=NOW), 1)

    def test_iso_named_like_program_never_junk(self):
        for n in ("SteamSetup.iso", "Win11_23H2_German_x64v2.iso", "Fotos_Hochzeit.img"):
            f = mk(n)
            self.assertEqual(org_mark_installers([f], REAL_PROGRAMS, now=NOW), 0)
            self.assertEqual(f.tier, ORG_REVIEW)

    def test_perf_60k_installers(self):
        files = [mk("D%d/Tool%d_Setup_v%d.%d.exe" % (i % 50, i, i % 9, i % 7))
                 for i in range(60_000)]
        files += [mk("Downloads/SteamSetup (%d).exe" % i) for i in range(100)]
        t0 = time.perf_counter()
        n = org_mark_installers(files, REAL_PROGRAMS, now=NOW)
        self.assertLess(time.perf_counter() - t0, 15.0)
        self.assertEqual(n, 100)


# ---------------------------------------------------------------------------
class _K(object):
    def __init__(self, values=None, subkeys=None):
        self.values = values or {}
        self.subkeys = subkeys or {}


class _Reg(object):
    HKEY_LOCAL_MACHINE = "HKLM"
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 0x20019
    KEY_WOW64_64KEY = 0x100
    KEY_WOW64_32KEY = 0x200

    def __init__(self, hives, enum_fail_at=None, query_exc=None):
        self.hives = hives
        self.enum_fail_at = enum_fail_at
        self.query_exc = query_exc

    def OpenKey(self, key, sub, reserved=0, access=0):
        node = self.hives.get(key) if isinstance(key, str) else key
        for part in [p for p in sub.split("\\") if p]:
            if node is None or part not in node.subkeys:
                raise FileNotFoundError(2, "nicht gefunden")
            node = node.subkeys[part]
        if node is None:
            raise PermissionError(5, "Zugriff verweigert")
        return node

    def EnumKey(self, key, i):
        if self.enum_fail_at is not None and i == self.enum_fail_at:
            raise PermissionError(5, "Zugriff verweigert")
        names = list(key.subkeys)
        if i >= len(names):
            raise OSError(259, "Keine weiteren Daten verfügbar")
        return names[i]

    def QueryValueEx(self, key, name):
        if self.query_exc is not None:
            raise self.query_exc
        if name not in key.values:
            raise FileNotFoundError(2, "Wert fehlt")
        return (key.values[name], 1)


def _tree(entries):
    u = _K(subkeys={k: _K(values=v) for k, v in entries.items()})
    node = u
    for part in reversed(r"Software\Microsoft\Windows\CurrentVersion\Uninstall".split("\\")):
        node = _K(subkeys={part: node})
    return node


class TestRegistryAdversarial(unittest.TestCase):
    def test_weird_values(self):
        reg = _Reg({"HKLM": _tree({
            "a": {"DisplayName": "Steam\x00"},
            "b": {"DisplayName": b"Bytes"},
            "c": {"DisplayName": "  Spotify   Music  "},
            "d": {"DisplayName": "Discord", "SystemComponent": "1"},
            "e": {"DisplayName": "Git", "SystemComponent": None},
            "f": {"DisplayName": "Zoom", "ReleaseType": "Security Update"},
            "g": {"DisplayName": "Ünicode Prögramm"},
            "h": {"DisplayName": "x" * 5000},
        }), "HKCU": None})
        progs = org_installed_programs(reg)
        self.assertNotIn("Bytes", progs)
        self.assertIn("Spotify Music", progs)
        self.assertNotIn("Discord", progs)       # SystemComponent "1" als Text
        self.assertIn("Git", progs)
        self.assertNotIn("Zoom", progs)
        self.assertIn("Ünicode Prögramm", progs)
        for p in progs:
            self.assertIsInstance(p, str)

    def test_enum_permission_error_midway(self):
        reg = _Reg({"HKLM": _tree({"a": {"DisplayName": "A"}, "b": {"DisplayName": "B"}}),
                    "HKCU": None}, enum_fail_at=1)
        self.assertEqual(org_installed_programs(reg), ["A"])

    def test_query_raises_runtime_error(self):
        reg = _Reg({"HKLM": _tree({"a": {"DisplayName": "A"}}), "HKCU": None},
                   query_exc=RuntimeError("kaputt"))
        self.assertEqual(org_installed_programs(reg), [])

    def test_subkey_open_denied(self):
        t = _tree({"a": {"DisplayName": "A"}})
        t.subkeys["Software"].subkeys["Microsoft"].subkeys["Windows"].subkeys[
            "CurrentVersion"].subkeys["Uninstall"].subkeys["locked"] = None
        reg = _Reg({"HKLM": t, "HKCU": None})
        self.assertEqual(org_installed_programs(reg), ["A"])

    def test_partial_module(self):
        class M(object):
            HKEY_LOCAL_MACHINE = 1
            HKEY_CURRENT_USER = 2
        self.assertEqual(org_installed_programs(M()), [])


# ---------------------------------------------------------------------------
class TestEmptyDirsAdversarial(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orgjunk_adv_"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def mkd(self, rel):
        p = self.root / rel
        p.mkdir(parents=True, exist_ok=True)
        return p

    def rels(self, paths):
        return [p.relative_to(self.root).as_posix() for p in paths]

    def snapshot(self):
        out = []
        for dp, dn, fn in os.walk(str(self.root)):
            out.append((dp, sorted(dn), sorted(fn)))
        return sorted(out)

    def test_root_is_project(self):
        (self.root / ".git").mkdir()
        self.mkd("build/leer")
        self.assertEqual(org_empty_dirs(self.root), [])

    def test_unicode_names(self):
        for n in ("Übungen", "Fotos 💕", "家族", "x" * 200, "Ordner mit  Leerzeichen ",
                  "Kopie (2)", "a#b%c&d"):
            self.mkd(n)
        res = set(self.rels(org_empty_dirs(self.root)))
        self.assertEqual(len(res), 7)

    def test_nfd_office_folder_kept(self):
        self.mkd(unicodedata.normalize("NFD", "OneNote-Notizbücher"))
        self.mkd("Custom Office Templates")
        self.assertEqual(org_empty_dirs(self.root), [])

    def test_deep_nesting_no_recursion_error(self):
        rel = "/".join(["d"] * 900)
        os.makedirs(str(self.root / rel))
        res = org_empty_dirs(self.root)
        self.assertEqual(len(res), 900)
        self.assertEqual(self.rels(res)[-1], "d")

    def test_skip_none_uses_defaults(self):
        self.mkd("node_modules/x")
        self.assertEqual(org_empty_dirs(self.root, skip=None), [])

    @unittest.skipUnless(hasattr(os, "symlink"), "keine Symlinks")
    def test_broken_symlink_counts_as_content(self):
        self.mkd("A")
        try:
            os.symlink(str(self.root / "gibtsnicht"), str(self.root / "A" / "kaputt"))
        except (OSError, NotImplementedError):
            self.skipTest("Symlink nicht erlaubt")
        self.assertEqual(org_empty_dirs(self.root), [])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "kein mkfifo")
    def test_fifo_counts_as_content(self):
        self.mkd("A")
        os.mkfifo(str(self.root / "A" / "pipe"))
        self.assertEqual(org_empty_dirs(self.root), [])

    def test_hidden_file_only_counts(self):
        self.mkd("A")
        (self.root / "A" / ".versteckt").write_bytes(b"")
        self.assertEqual(org_empty_dirs(self.root), [])

    def test_nothing_deleted_or_created(self):
        self.mkd("A/B")
        self.mkd("C")
        (self.root / "C" / "x.txt").write_bytes(b"x")
        before = self.snapshot()
        org_empty_dirs(self.root)
        org_empty_dirs(str(self.root))
        self.assertEqual(before, self.snapshot())

    def test_windows_attributes_excluded(self):
        class St(object):
            st_mode = 0o040755

            def __init__(self, attrs):
                self.st_file_attributes = attrs

        class E(object):
            def __init__(self, name, attrs):
                self.name = name
                self._a = attrs

            def is_symlink(self):
                return False

            def stat(self, follow_symlinks=True):
                return St(self._a)

        self.assertTrue(oj._org_junk_dir_excluded(E("Junction", 0x400), set()))
        self.assertTrue(oj._org_junk_dir_excluded(E("Versteckt", 0x2), set()))
        self.assertTrue(oj._org_junk_dir_excluded(E("System", 0x4), set()))
        self.assertFalse(oj._org_junk_dir_excluded(E("Normal", 0x10), set()))

    def test_bad_roots(self):
        for r in ("", "\x00", 123, b"/tmp", object()):
            self.assertEqual(org_empty_dirs(r), [])

    def test_many_dirs_fast(self):
        for i in range(4000):
            self.mkd("v/%d/a" % i)
        t0 = time.perf_counter()
        res = org_empty_dirs(self.root)
        self.assertLess(time.perf_counter() - t0, 10.0)
        self.assertEqual(len(res), 8001)


# ---------------------------------------------------------------------------
class TestModuleHygiene(unittest.TestCase):
    def test_top_level_names_prefixed(self):
        src = Path(oj.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        allowed_imports = {"annotations", "math", "os", "re", "time", "unicodedata", "Path",
                           "ORG_JUNK", "ORG_KEEP", "ORG_PROJECT_MARKERS", "ORG_PROTECT",
                           "ORG_REVIEW", "ORG_SKIP_DIRS"}
        for node in tree.body:
            names = []
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = [t.id for t in targets if isinstance(t, ast.Name)]
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    n = a.asname or a.name
                    if n not in allowed_imports:
                        names.append(n)
            for n in names:
                self.assertTrue(n.startswith(("org_", "Org", "ORG_", "_org_", "_ORG_")), n)

    def test_no_py310_syntax(self):
        src = Path(oj.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            self.assertNotEqual(type(node).__name__, "Match")
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "zip":
                self.assertFalse(any(k.arg == "strict" for k in node.keywords))
        self.assertNotIn("removeprefix", src)
        self.assertNotIn("removesuffix", src)

    def test_no_file_mutation_calls(self):
        src = Path(oj.__file__).read_text(encoding="utf-8")
        for bad in ("os.remove", "os.unlink", "shutil.", "os.rmdir", ".unlink(", ".rename(",
                    "os.replace", "send2trash", "urllib", "socket", "http.client"):
            self.assertNotIn(bad, src, bad)


if __name__ == "__main__":
    prog = unittest.main(exit=False, verbosity=1)
    r = prog.result
    print("org_junk adversarial: %d Tests, %d Fehler, %d Ausfälle, %d übersprungen"
          % (r.testsRun, len(r.errors), len(r.failures), len(r.skipped)))
    sys.exit(0 if r.wasSuccessful() else 1)
