# Adversariale Tests für org_dupes – Datenverlust, Windows-Eigenheiten, echte
# deutsche/englische Dateinamen, Grenzwerte, Leistung. Alles wird hier erzeugt.
from __future__ import annotations

import ast
import os
import random
import shutil
import sys
import tempfile
import time
import types
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import org_dupes as od  # noqa: E402
from org_core import (ORG_JUNK, ORG_KEEP, ORG_PROTECT, ORG_REVIEW, OrgFile,  # noqa: E402
                      org_kind, org_walk)
from org_dupes import (org_find_duplicates, org_mark_duplicates, org_mark_versions,  # noqa: E402
                       org_pick_keeper, org_version_base, org_version_groups, org_version_key)

KB = 1024


def mk(rel, size=10, mtime=1000.0, depth=None, in_project=False, root="/nonexistent-root",
       category="", tier=""):
    p = Path(root) / rel.replace("\\", "/")
    return OrgFile(path=p, rel=rel, size=size, mtime=mtime, kind=org_kind(p),
                   ext=p.suffix.lower(),
                   depth=rel.replace("\\", "/").count("/") if depth is None else depth,
                   in_project=in_project, category=category, tier=tier)


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orgdupes_adv_"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, data: bytes, mtime=None):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    def files(self):
        return org_walk(self.root)[0]

    def rel(self, files):
        return {f.rel: f for f in files}


def survivors(group):
    """Mitglieder, die nach dem Markieren NICHT zum Löschen vorgeschlagen sind."""
    return [f for f in group if f.tier != ORG_JUNK]


# ===========================================================================
# 1) Datenverlust: das Behalten-Exemplar darf nie ein Rest/Müll sein
# ===========================================================================
class TestNoDataLoss(_Tmp):
    def test_keeper_never_word_backup_tmp(self):
        # Word legt ~WRL0001.tmp als Kopie an; ist sie älter, gewann sie früher Regel 4
        data = os.urandom(3000)
        self.write("Bewerbung/~WRL0001.tmp", data, mtime=1_000_000)
        self.write("Bewerbung/Lebenslauf.docx", data, mtime=2_000_000)
        files = self.files()
        groups = org_find_duplicates(files)
        self.assertEqual(len(groups), 1)
        keeper = org_pick_keeper(groups[0])
        self.assertEqual(keeper.rel, "Bewerbung/Lebenslauf.docx")

    def test_keeper_never_partial_or_lock(self):
        for junkname in ("Zeugnis.pdf.part", "Zeugnis.pdf.crdownload", "~$Zeugnis.pdf",
                         "Zeugnis.tmp", "Zeugnis.pdf.bak", ".~lock.Zeugnis.pdf#",
                         "Zeugnis.pdf.download", "Zeugnis.pdf.opdownload"):
            a = mk("Dok/" + junkname, mtime=1)              # älter, flacher Pfad
            b = mk("Dok/Archiv/Zeugnis (1).pdf", mtime=99)  # Kopie-Marker, tiefer
            self.assertEqual(org_pick_keeper([a, b]).rel, b.rel, junkname)

    def test_keeper_never_already_junk_tier(self):
        a = mk("a.pdf", mtime=1, category="temp", tier=ORG_JUNK)
        b = mk("Downloads/b - Kopie.pdf", mtime=50, tier=ORG_PROTECT)
        self.assertIs(org_pick_keeper([a, b]), b)

    def test_pipeline_with_org_junk_both_orders(self):
        try:
            import org_junk
        except Exception:
            self.skipTest("org_junk nicht vorhanden")
        old = time.time() - 30 * 86400
        data = os.urandom(5000)
        self.write("Docs/~WRL1234.tmp", data, mtime=old - 100)
        self.write("Docs/Lebenslauf.docx", data, mtime=old)
        self.write("Downloads/Arbeitszeugnis.pdf.part", b"Z" * 7777, mtime=old - 10)
        self.write("Docs/Arbeitszeugnis.pdf", b"Z" * 7777, mtime=old)
        for order in ("junk_first", "dupes_first"):
            files = self.files()
            if order == "junk_first":
                org_junk.org_detect_junk(files)
            groups = org_find_duplicates(files)
            org_mark_duplicates(files, groups)
            if order == "dupes_first":
                org_junk.org_detect_junk(files)
            r = self.rel(files)
            self.assertNotEqual(r["Docs/Lebenslauf.docx"].tier, ORG_JUNK, order)
            self.assertNotEqual(r["Docs/Arbeitszeugnis.pdf"].tier, ORG_JUNK, order)
            for g in groups:
                self.assertTrue(survivors(g), order)

    def test_mismatched_group_never_marked(self):
        # Aufrufer mischt Gruppen falsch zusammen: andere Größe / anderer Hash -> nie Müll
        k = mk("Dok/Lebenslauf.pdf", size=100, mtime=1)
        other_size = mk("Dok/Zeugnis.pdf", size=200, mtime=2, tier=ORG_PROTECT)
        k.meta["hash"] = "aa"
        other_hash = mk("Dok/Vertrag.pdf", size=100, mtime=3, tier=ORG_PROTECT)
        other_hash.meta["hash"] = "bb"
        n = org_mark_duplicates([], [[k, other_size, other_hash]])
        self.assertEqual(n, 0)
        self.assertEqual(other_size.tier, ORG_PROTECT)
        self.assertEqual(other_hash.tier, ORG_PROTECT)

    def test_overlapping_groups_keep_one(self):
        a, b, c = mk("a.txt", mtime=1), mk("b.txt", mtime=2), mk("c.txt", mtime=3)
        org_mark_duplicates([], [[a, b], [b, c], [c, a]])
        self.assertTrue(survivors([a, b, c]))
        self.assertEqual(a.tier, "")

    def test_keeper_deleted_between_find_and_mark(self):
        data = os.urandom(2000)
        self.write("Fotos/Oma.pdf", data, mtime=1_000_000)          # Original (Keeper)
        self.write("Downloads/Oma (1).pdf", data, mtime=2_000_000)
        files = self.files()
        groups = org_find_duplicates(files)
        self.assertEqual(org_pick_keeper(groups[0]).rel, "Fotos/Oma.pdf")
        os.remove(self.root / "Fotos" / "Oma.pdf")                  # Nutzer räumt parallel
        org_mark_duplicates(files, groups)
        r = self.rel(files)
        self.assertNotEqual(r["Downloads/Oma (1).pdf"].tier, ORG_JUNK)

    def test_hardlink_plus_real_copy(self):
        if not hasattr(os, "link"):
            self.skipTest("keine Hardlinks")
        data = os.urandom(4000)
        p = self.write("A/Zeugnis.pdf", data, mtime=1_000_000)
        (self.root / "B").mkdir()
        try:
            os.link(p, self.root / "B" / "Zeugnis.pdf")
        except OSError:
            self.skipTest("Hardlink nicht möglich")
        self.write("C/Zeugnis.pdf", data, mtime=2_000_000)
        files = self.files()
        groups = org_find_duplicates(files)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)       # Hardlink zählt nur einmal
        org_mark_duplicates(files, groups)
        # physische Datei A/B (dieselbe!) darf nicht komplett Müll sein, solange C Müll ist
        r = self.rel(files)
        ab_junk = r["A/Zeugnis.pdf"].tier == ORG_JUNK or r["B/Zeugnis.pdf"].tier == ORG_JUNK
        self.assertFalse(ab_junk and r["C/Zeugnis.pdf"].tier == ORG_JUNK)

    def test_same_file_different_spelling(self):
        self.write("x/a.txt", b"hello world!")
        f = self.files()[0]
        alias = OrgFile(path=self.root / "x" / ".." / "x" / "a.txt", rel="x/../x/a.txt",
                        size=f.size, mtime=f.mtime, kind="document", ext=".txt")
        self.assertEqual(org_find_duplicates([f, alias]), [])

    def test_invariant_fuzz_keeper_survives(self):
        rnd = random.Random(7)
        names = ["Lebenslauf.docx", "Lebenslauf - Kopie.docx", "~WRL0001.tmp", "x.part",
                 "Zeugnis (1).pdf", "Copy of CV.pdf", "Foto.jpg", "a.bak"]
        dirs = ["", "Downloads/", "Dokumente/", "Desktop/Neuer Ordner (3)/", "Projekt/src/"]
        tiers = ["", ORG_PROTECT, ORG_KEEP, ORG_REVIEW, ORG_JUNK]
        for _ in range(2000):
            group = []
            for _j in range(rnd.randint(2, 6)):
                f = mk(rnd.choice(dirs) + rnd.choice(names), mtime=rnd.randint(0, 5),
                       in_project=rnd.random() < 0.1, tier=rnd.choice(tiers))
                group.append(f)
            had_nonjunk = any(f.tier != ORG_JUNK for f in group)
            org_mark_duplicates([], [group])
            keeper = org_pick_keeper(group)
            self.assertEqual(keeper.dup_of, "")
            if had_nonjunk:
                self.assertTrue(survivors(group), [f.rel for f in group])
            for f in group:
                if f.in_project:
                    self.assertEqual(f.dup_of, "")

    def test_protected_photo_copy_only_if_identical(self):
        # Familienfoto in zwei Ordnern, aber eines ist bearbeitet (gleiche Größe!)
        a = os.urandom(200 * KB)
        b = bytearray(a)
        b[100 * KB] ^= 1
        self.write("Bilder/Familie/Oma.jpg", a)
        self.write("Downloads/Oma.jpg", bytes(b))
        self.assertEqual(org_find_duplicates(self.files()), [])


# ===========================================================================
# 2) Dateisystem-Grenzfälle
# ===========================================================================
class TestFilesystemEdgeCases(_Tmp):
    def test_unicode_emoji_umlaut_names(self):
        data = os.urandom(999)
        names = ["Zeugnisse/Abitur Müller.pdf", "Downloads/Abitur Müller (1).pdf",
                 "Fotos 🎄/Weihnachten 😀.pdf", "简历/简历.pdf"]
        for n in names:
            self.write(n, data)
        groups = org_find_duplicates(self.files())
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 4)
        n = org_mark_duplicates([], groups)
        self.assertEqual(n, 3)
        self.assertEqual(sum(1 for f in groups[0] if f.dup_of == ""), 1)

    def test_very_long_name(self):
        data = b"L" * 1500
        long_name = ("Bewerbung_" + "x" * 230 + ".pdf")
        self.write("a/" + long_name, data)
        self.write("b/" + long_name, data)
        self.assertEqual(len(org_find_duplicates(self.files())), 1)

    def test_file_grew_since_walk(self):
        self.write("a.bin", b"A" * 100)
        self.write("b.bin", b"A" * 100)
        files = self.files()
        # nach dem Einlesen wächst eine Datei stark (z. B. Download läuft weiter)
        self.write("b.bin", b"A" * (3 * 1024 * KB))
        self.assertEqual(org_find_duplicates(files), [])

    def test_large_file_shrunk_since_walk(self):
        data = os.urandom(400 * KB)
        self.write("a.bin", data)
        self.write("b.bin", data)
        files = self.files()
        self.write("b.bin", data[:300 * KB])
        self.assertEqual(org_find_duplicates(files), [])

    def test_file_deleted_since_walk(self):
        data = os.urandom(1000)
        for n in ("a.txt", "b.txt", "c.txt"):
            self.write(n, data)
        files = self.files()
        os.remove(self.root / "b.txt")
        groups = org_find_duplicates(files)
        self.assertEqual(sorted(f.rel for f in groups[0]), ["a.txt", "c.txt"])

    def test_boundary_sizes(self):
        edge = od.ORG_DUPE_EDGE_BYTES
        for size in (1, edge - 1, edge, edge + 1, 2 * edge - 1, 2 * edge, 2 * edge + 1,
                     od.ORG_DUPE_CHUNK_BYTES, od.ORG_DUPE_CHUNK_BYTES + 1):
            d = Path(tempfile.mkdtemp(prefix="orgsz_", dir=str(self.root)))
            data = os.urandom(size)
            (d / "a.bin").write_bytes(data)
            (d / "b.bin").write_bytes(data)
            diff = bytearray(data)
            diff[size // 2] ^= 0x55
            (d / "c.bin").write_bytes(bytes(diff))
            files = org_walk(d)[0]
            groups = org_find_duplicates(files)
            self.assertEqual(len(groups), 1, size)
            self.assertEqual(sorted(f.rel for f in groups[0]), ["a.bin", "b.bin"], size)
            import hashlib
            self.assertEqual(groups[0][0].meta["hash"],
                             hashlib.blake2b(data, digest_size=32).hexdigest(), size)

    def test_cloud_placeholder_never_read(self):
        data = os.urandom(3000)
        self.write("OneDrive/a.pdf", data)
        self.write("OneDrive/b.pdf", data)
        self.write("OneDrive/c.pdf", data)
        files = self.files()
        real_stat = os.stat

        def fake_stat(p, *a, **k):
            st = real_stat(p, *a, **k)
            attrs = 0x00400000 if str(p).endswith("b.pdf") else 0x20
            return types.SimpleNamespace(st_file_attributes=attrs, st_size=st.st_size)

        opened = []
        real_open = open

        def spy(path, *a, **k):
            opened.append(str(path))
            return real_open(path, *a, **k)

        with mock.patch.object(od, "ORG_DUPE_CHECK_CLOUD", True), \
                mock.patch.object(od, "_org_dupe_stat", fake_stat), \
                mock.patch("builtins.open", spy):
            groups = org_find_duplicates(files)
        self.assertFalse(any(p.endswith("b.pdf") for p in opened))
        self.assertEqual(sorted(f.rel for f in groups[0]), ["OneDrive/a.pdf", "OneDrive/c.pdf"])

    def test_long_path_prefix(self):
        lp = od._org_dupe_long_path
        short = "C:\\Users\\a\\x.pdf"
        self.assertEqual(lp(short), short)
        longp = "C:\\Users\\" + "a" * 260 + "\\x.pdf"
        self.assertEqual(lp(longp), "\\\\?\\" + longp)
        unc = "\\\\server\\share\\" + "b" * 260
        self.assertEqual(lp(unc), "\\\\?\\UNC\\server\\share\\" + "b" * 260)
        pre = "\\\\?\\C:\\" + "c" * 300
        self.assertEqual(lp(pre), pre)

    def test_cancel_during_big_file(self):
        data = os.urandom(70 * 1024 * KB)        # > 64 Blöcke -> Prüfung mitten im Lesen
        self.write("a.bin", data)
        self.write("b.bin", data)
        files = self.files()
        calls = {"n": 0}

        def cancelled():
            calls["n"] += 1
            return calls["n"] > 3                # erst beim vollen Hash abbrechen
        self.assertEqual(org_find_duplicates(files, cancelled=cancelled), [])

    def test_callbacks_raising(self):
        data = os.urandom(500)
        self.write("a.txt", data)
        self.write("b.txt", data)

        def boom(*_a):
            raise RuntimeError("kaputt")
        groups = org_find_duplicates(self.files(), cancelled=boom, progress=boom)
        self.assertEqual(len(groups), 1)

    def test_backslash_rel_and_weak_dirs(self):
        a = mk("Downloads\\Zeugnis.pdf", mtime=1)
        b = mk("Dokumente\\Zeugnisse\\Zeugnis.pdf", mtime=9)
        self.assertIs(org_pick_keeper([a, b]), b)
        c = mk("C:\\Users\\Felix\\Desktop\\Neuer Ordner (7)\\x.pdf", mtime=1)
        d = mk("Archiv\\x.pdf", mtime=9)
        self.assertIs(org_pick_keeper([c, d]), d)

    def test_nan_and_garbage_mtime(self):
        a = mk("a.txt", mtime=float("nan"))
        b = mk("b.txt", mtime=5)
        c = mk("c.txt", mtime=None)
        self.assertIs(org_pick_keeper([a, b, c]), b)
        self.assertIs(org_pick_keeper([c, a, b]), b)

    def test_copy_marker_variants(self):
        h = od._org_dupe_has_copy_marker
        for n in ("Lebenslauf-Kopie.pdf", "Lebenslauf (Kopie).pdf", "Kopie - Lebenslauf.pdf",
                  "Lebenslauf – Kopie.pdf", "Lebenslauf - Kopie - Kopie.pdf",
                  "Lebenslauf (1) (1).pdf", "Lebenslauf - Copy (2).pdf", "IMG_1234(1).jpg",
                  "CV.copy.pdf", "cv_copy2.pdf"):
            self.assertTrue(h(n), n)
        for n in ("Copyright.pdf", "Kopierer.pdf", "Rechnung (2024).pdf", "Hardcopy.pdf",
                  "Kopieranleitung.pdf", "Lebenslauf.pdf", "Anlage (100).pdf"):
            self.assertFalse(h(n), n)

    def test_bad_inputs_never_raise(self):
        weird = [None, 1, "x", object(), types.SimpleNamespace(size=5, path=None),
                 types.SimpleNamespace(size=5, path="/nope/a", rel=None, meta=None),
                 types.SimpleNamespace(size=5, path="/nope/b", rel=b"x", meta=[])]
        self.assertEqual(org_find_duplicates(weird), [])
        self.assertEqual(org_find_duplicates(iter(weird)), [])
        self.assertEqual(org_mark_duplicates(None, [weird, "ab", 5]), 0)
        self.assertEqual(org_mark_duplicates([], 5), 0)
        self.assertIsNotNone(org_pick_keeper(weird))
        self.assertEqual(org_version_groups(weird), {})
        self.assertEqual(org_version_groups(5), {})
        self.assertEqual(org_mark_versions({"k": weird}), 0)
        self.assertEqual(org_mark_versions(5), 0)
        self.assertEqual(org_mark_versions([1, 2]), 0)


# ===========================================================================
# 3) Versionsschlüssel – echte Namen
# ===========================================================================
ADV_KEYS = [
    # nicht-lateinische Namen dürfen nicht zu "2023.docx" / "1.docx" zusammenfallen
    ("简历_v2_2023.docx", "简历 2023.docx"),
    ("Отчет 1 final.docx", "отчет 1.docx"),
    ("Søren_final.pdf", "søren.pdf"),
    ("Lebenslauf_FINAL_v3_korr.docx", "lebenslauf.docx"),
    ("Lebenslauf-Kopie.docx", "lebenslauf.docx"),
    ("Lebenslauf (Kopie).docx", "lebenslauf.docx"),
    ("Kopie - Lebenslauf.docx", "lebenslauf.docx"),
    ("LEBENSLAUF-NEU.DOCX", "lebenslauf.docx"),
    ("Lebenslauf_neu_neu.docx", "lebenslauf.docx"),
    ("Lebenslauf Endfassung.docx", "lebenslauf.docx"),
    ("Lebenslauf_finalfinal.docx", "lebenslauf.docx"),
    ("Kopie von Kopie von Lebenslauf.docx", "lebenslauf.docx"),
    (unicodedata.normalize("NFD", "Bericht_überarbeitet.docx"), "bericht.docx"),
    (unicodedata.normalize("NFD", "Prüfung_final.pdf"), "pruefung.pdf"),
    ("Protokoll 2024-03-01 final.docx", "protokoll 2024 03 01.docx"),
    ("Rechnung_2024.01.15_final.pdf", "rechnung 2024.01.15.pdf"),
    ("Bewerbung_V1_Siemens.pdf", "bewerbung siemens.pdf"),
    ("Bewerbung_V1_Bosch.pdf", "bewerbung bosch.pdf"),
    ("Teil 1 final.pdf", "teil 1.pdf"),
    ("Teil 2 final.pdf", "teil 2.pdf"),
    ("Zeugnis 2. Klasse.pdf", None),
    ("Zeugnis Klasse 3.pdf", None),
    ("Brief an Oma v. 2020.pdf", None),
    ("Lebenslauf (2024).docx", None),
    ("Steuer 2023.pdf", None),
    ("Kontoauszug_2024_01_15.pdf", None),
    ("IMG_20240712_123456.jpg", None),
    ("WhatsApp Image 2024-07-12 at 10.11.12.jpeg", None),
    ("Lebenslauf.docx - Verknüpfung.lnk", None),
    ("Reise New York Planung.pdf", None),
    ("Bericht\udcfc final.docx", "bericht.docx"),     # nicht dekodierbarer Name (Linux)
]


class TestVersionKeyAdversarial(unittest.TestCase):
    def test_cases(self):
        bad = [(n, e, org_version_key(n)) for n, e in ADV_KEYS if org_version_key(n) != e]
        self.assertEqual(bad, [])

    def test_non_latin_distinct(self):
        self.assertNotEqual(org_version_base("简历_v2_2023.docx"), org_version_base("合同_2023.docx"))
        self.assertNotEqual(org_version_base("Отчет 1 final.docx"), org_version_base("Счет 1.docx"))

    def test_dates_and_numbers_kept_distinct(self):
        pairs = [("Kontoauszug 2024-01 final.pdf", "Kontoauszug 2024-02 final.pdf"),
                 ("Rechnung_1234_final.pdf", "Rechnung_1235_final.pdf"),
                 ("Protokoll 01.03.2024 v2.docx", "Protokoll 02.03.2024 v2.docx"),
                 ("Klausur 1 korrigiert.pdf", "Klausur 2 korrigiert.pdf")]
        for a, b in pairs:
            self.assertNotEqual(org_version_key(a), org_version_key(b), (a, b))

    def test_nfc_nfd_same_key(self):
        a = unicodedata.normalize("NFC", "Zeugnis_Müller_final.pdf")
        b = unicodedata.normalize("NFD", "Zeugnis_Müller_alt.pdf")
        self.assertEqual(org_version_key(a), org_version_key(b))

    def test_huge_and_weird_strings(self):
        t0 = time.perf_counter()
        for s in ("a" * 100000 + ".docx", "(" * 5000, "v" * 3000 + "1.2" * 2000,
                  "final " * 5000, "\x00\x01final.pdf", "...final...", "  final  .pdf "):
            org_version_key(s)
            self.assertIsInstance(org_version_base(s), str)
        self.assertLess(time.perf_counter() - t0, 3.0)


# ===========================================================================
# 4) Versionsgruppen – keine falschen "Ältere Version" für wichtige Dateien
# ===========================================================================
class TestVersionGroupsAdversarial(unittest.TestCase):
    def test_photos_never_versions(self):
        files = [mk("Bilder/IMG_0001.JPG", mtime=1, category="photo_personal", tier=ORG_PROTECT),
                 mk("Bilder/IMG_0001 (1).JPG", mtime=2, category="photo_personal",
                    tier=ORG_PROTECT),
                 mk("Bilder/Screenshot (1).png", mtime=3), mk("Bilder/Screenshot (2).png", mtime=4),
                 mk("Bilder/Bewerbungsfoto.jpg", mtime=5),
                 mk("Bilder/Bewerbungsfoto_bearbeitet.jpg", mtime=6),
                 mk("Videos/Hochzeit.mp4", mtime=1), mk("Videos/Hochzeit_final.mp4", mtime=2),
                 mk("Musik/Song.mp3"), mk("Musik/Song (1).mp3")]
        self.assertEqual(org_version_groups(files), {})
        self.assertEqual(files[0].tier, ORG_PROTECT)

    def test_generic_counter_names_not_versions(self):
        for stem in ("Scan", "Dokument", "document", "Unbenannt", "download", "Rechnung",
                     "invoice", "Kontoauszug", "Entgeltabrechnung", "Telekomrechnung",
                     "Arbeitsunfähigkeitsbescheinigung", "Ticket", "Befund"):
            files = [mk("Downloads/%s.pdf" % stem, mtime=1, tier=ORG_PROTECT),
                     mk("Downloads/%s (1).pdf" % stem, mtime=2, tier=ORG_PROTECT),
                     mk("Downloads/%s (2).pdf" % stem, mtime=3, tier=ORG_PROTECT)]
            self.assertEqual(org_version_groups(files), {}, stem)

    def test_recurring_category_counter_not_versions(self):
        files = [mk("Gehalt/Abrechnung Firma.pdf", mtime=1, category="payslip", tier=ORG_PROTECT),
                 mk("Gehalt/Abrechnung Firma (1).pdf", mtime=2, category="payslip",
                    tier=ORG_PROTECT)]
        self.assertEqual(org_version_groups(files), {})
        files = [mk("Gehalt/Lohnzettel Firma.pdf", mtime=1, category="payslip"),
                 mk("Gehalt/Lohnzettel Firma (1).pdf", mtime=2, category="payslip")]
        self.assertEqual(org_version_groups(files), {})

    def test_cv_counter_still_versions(self):
        files = [mk("Bewerbung/Lebenslauf.docx", mtime=1),
                 mk("Bewerbung/Lebenslauf (1).docx", mtime=2)]
        self.assertEqual(list(org_version_groups(files)), ["Bewerbung/lebenslauf.docx"])
        files = [mk("Scan.pdf", mtime=1), mk("Scan_final.pdf", mtime=2)]
        self.assertEqual(list(org_version_groups(files)), ["scan.pdf"])

    def test_lock_file_never_newest(self):
        cv = mk("Bewerbung/CV.docx", size=30000, mtime=1, category="cv", tier=ORG_PROTECT)
        lock = mk("Bewerbung/~$CV_final.docx", size=162, mtime=999)
        g = org_version_groups([cv, lock])
        org_mark_versions(g)
        self.assertEqual(cv.tier, ORG_PROTECT)
        self.assertTrue(cv.version_latest)

    def test_empty_newer_file_never_newest(self):
        cv = mk("Bewerbung/Lebenslauf.docx", size=30000, mtime=1, category="cv", tier=ORG_PROTECT)
        empty = mk("Bewerbung/Lebenslauf_neu.docx", size=0, mtime=999)
        org_mark_versions(org_version_groups([cv, empty]))
        self.assertEqual(cv.tier, ORG_PROTECT)
        self.assertEqual(cv.reasons, [])

    def test_backslash_rel(self):
        a = mk("Bewerbung\\CV.docx", mtime=1)
        b = mk("Bewerbung\\CV_final.docx", mtime=2)
        g = org_version_groups([a, b])
        self.assertEqual(list(g), ["Bewerbung/cv.docx"])

    def test_versions_never_junk_never_project(self):
        a = mk("Dok/Bericht.docx", mtime=1, tier=ORG_PROTECT)
        b = mk("Dok/Bericht_v2.docx", mtime=2)
        p = mk("Dok/Bericht_v3.docx", mtime=3, in_project=True, tier=ORG_KEEP)
        n = org_mark_versions({"Dok/bericht.docx": [a, b, p]})
        self.assertEqual(n, 1)
        self.assertEqual(a.tier, ORG_REVIEW)
        self.assertTrue(b.version_latest)
        self.assertEqual(p.tier, ORG_KEEP)
        self.assertEqual(p.version_group, "")

    def test_mark_versions_no_partial_state(self):
        good = mk("Dok/A_final.docx", mtime=1)
        newest = types.SimpleNamespace(rel="Dok/A.docx", mtime=5, path="Dok/A.docx",
                                       version_group="", version_latest=True, tier="",
                                       in_project=False, dup_of="")
        n = org_mark_versions({"Dok/a.docx": [good, newest]})
        self.assertEqual(n, 1)
        self.assertEqual(good.tier, ORG_REVIEW)
        self.assertIn("Ältere Version von A.docx", good.reasons)

    def test_mietvertrag_alt_neu_review_only(self):
        a = mk("Wohnen/Mietvertrag alt.pdf", mtime=1, category="property", tier=ORG_PROTECT)
        b = mk("Wohnen/Mietvertrag neu.pdf", mtime=2, category="property", tier=ORG_PROTECT)
        org_mark_versions(org_version_groups([a, b]))
        self.assertEqual(a.tier, ORG_REVIEW)          # Prüfen, nie Aufräumen
        self.assertNotEqual(a.tier, ORG_JUNK)
        self.assertEqual(b.tier, ORG_PROTECT)


# ===========================================================================
# 5) Leistung bei 60.000 Einträgen
# ===========================================================================
class TestPerformance60k(_Tmp):
    def test_find_60k_shared_sizes_missing_files(self):
        # schlimmster Fall: alle in wenigen Größen-Buckets, Dateien fehlen -> jede wird
        # geöffnet und scheitert; darf trotzdem nicht ewig dauern
        files = [mk("d%d/f%05d.pdf" % (i % 50, i), size=1000 + (i % 100), root=str(self.root))
                 for i in range(60000)]
        t0 = time.perf_counter()
        self.assertEqual(org_find_duplicates(files), [])
        self.assertLess(time.perf_counter() - t0, 5.0)

    def test_mark_huge_group(self):
        group = [mk("d%d/icon.png" % i, mtime=i) for i in range(60000)]
        for f in group:
            f.meta["hash"] = "h"
        t0 = time.perf_counter()
        n = org_mark_duplicates([], [group])
        self.assertEqual(n, 59999)
        self.assertLess(time.perf_counter() - t0, 5.0)

    def test_versions_60k_realistic(self):
        rnd = random.Random(3)
        words = ["Lebenslauf", "Zeugnis", "Bewerbung", "Rechnung", "Kontoauszug",
                 "Mietvertrag", "Steuererklärung", "简历", "Отчет", "Präsentation"]
        marks = ["", "_final", " (1)", "_v2", " - Kopie", "_neu", "_2024_01", " Entwurf",
                 "_überarbeitet", " FINAL (2)"]
        exts = [".docx", ".pdf", ".jpg", ".xlsx"]
        files = [mk("d%d/%s %d%s%s" % (i % 400, rnd.choice(words), rnd.randint(1, 300),
                                       rnd.choice(marks), rnd.choice(exts)), mtime=i)
                 for i in range(60000)]
        od._ORG_DUPE_BASE_CACHE.clear()
        t0 = time.perf_counter()
        g = org_version_groups(files)
        org_mark_versions(g)
        dt = time.perf_counter() - t0
        self.assertGreater(len(g), 0)
        self.assertLess(dt, 3.0, "zu langsam: %.2fs" % dt)
        self.assertFalse(any(f.tier == ORG_JUNK for f in files))
        self.assertFalse(any(f.ext == ".jpg" and f.version_group for f in files))


# ===========================================================================
# 6) Regeln für das Einfügen in die große Datei
# ===========================================================================
class TestHardRules(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "org_dupes.py"), encoding="utf-8") as fh:
            self.src = fh.read()

    def test_top_level_prefixes(self):
        tree = ast.parse(self.src)
        bad = []
        ok = ("org_", "Org", "ORG_", "_org_", "_ORG_")
        for node in tree.body:
            names = []
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                names = [node.name]
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    names += [n.id for n in ast.walk(t) if isinstance(n, ast.Name)]
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                names = [node.target.id] if isinstance(node.target, ast.Name) else []
            elif isinstance(node, (ast.For, ast.With)):
                names = [n.id for n in ast.walk(node) if isinstance(n, ast.Name)
                         and isinstance(n.ctx, ast.Store)]
            bad += [n for n in names if not n.startswith(ok)]
        self.assertEqual(bad, [])

    def test_py39_syntax(self):
        if sys.version_info >= (3, 8):
            ast.parse(self.src, feature_version=(3, 9))
        self.assertNotIn("match ", "".join(l for l in self.src.splitlines()
                                           if l.lstrip().startswith("match ")))
        self.assertNotIn("strict=", self.src)
        self.assertIn("from __future__ import annotations", self.src)

    def test_no_network_or_delete(self):
        for bad in ("import socket", "urllib", "http.client", "os.remove", "os.unlink",
                    "shutil.rmtree", "send2trash", ".unlink(", "os.rename", "shutil.move"):
            self.assertNotIn(bad, self.src, bad)


if __name__ == "__main__":
    prog = unittest.main(exit=False, verbosity=1)
    res = prog.result
    failed = len(res.failures) + len(res.errors)
    print("org_dupes adversarial: %d Tests, %d fehlgeschlagen, %d übersprungen (Python %s)"
          % (res.testsRun, failed, len(res.skipped), sys.version.split()[0]))
    sys.exit(0 if res.wasSuccessful() else 1)
