# Tests für org_dupes – alle Testdateien werden hier programmatisch erzeugt.
from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import org_dupes as od  # noqa: E402
from org_core import ORG_JUNK, ORG_KEEP, ORG_PROTECT, ORG_REVIEW, OrgFile, org_kind, org_walk  # noqa: E402
from org_dupes import (org_find_duplicates, org_mark_duplicates, org_mark_versions,  # noqa: E402
                       org_pick_keeper, org_version_base, org_version_groups, org_version_key)

KB = 1024


def mk(rel, size=10, mtime=1000.0, depth=None, in_project=False, root="/nonexistent-root",
       category="", tier=""):
    p = Path(root) / rel
    return OrgFile(path=p, rel=rel, size=size, mtime=mtime, kind=org_kind(p),
                   ext=p.suffix.lower(), depth=rel.count("/") if depth is None else depth,
                   in_project=in_project, category=category, tier=tier)


class _TmpCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orgdupes_"))

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

    def by_rel(self, files):
        return {f.rel: f for f in files}


# ---------------------------------------------------------------------------
class TestFindDuplicates(_TmpCase):
    def test_across_subfolders_different_names(self):
        data = os.urandom(5000)
        self.write("Dokumente/Lebenslauf.pdf", data)
        self.write("Downloads/cv_final.pdf", data)
        self.write("Desktop/x/y/kopie.pdf", data)
        self.write("other.pdf", os.urandom(5000))
        groups = org_find_duplicates(self.files())
        self.assertEqual(len(groups), 1)
        rels = sorted(f.rel for f in groups[0])
        self.assertEqual(rels, ["Desktop/x/y/kopie.pdf", "Dokumente/Lebenslauf.pdf",
                                "Downloads/cv_final.pdf"])
        hashes = {f.meta["hash"] for f in groups[0]}
        self.assertEqual(len(hashes), 1)
        self.assertEqual(len(hashes.pop()), 64)

    def test_same_size_different_content(self):
        self.write("a.bin", b"A" * 3000)
        self.write("b.bin", b"B" * 3000)
        self.assertEqual(org_find_duplicates(self.files()), [])

    def test_large_same_size_different_content(self):
        base = os.urandom(300 * KB)
        self.write("a.bin", base)
        self.write("b.bin", base[:-1] + bytes([base[-1] ^ 1]))
        self.assertEqual(org_find_duplicates(self.files()), [])

    def test_edges_equal_middle_differs(self):
        size = 3 * 64 * KB + 777
        head = os.urandom(64 * KB)
        tail = os.urandom(64 * KB)
        mid_len = size - 2 * 64 * KB
        m1 = os.urandom(mid_len)
        m2 = bytearray(m1)
        m2[mid_len // 2] ^= 0xFF
        self.write("a.bin", head + m1 + tail)
        self.write("b.bin", head + bytes(m2) + tail)
        self.write("c.bin", head + m1 + tail)          # echtes Duplikat von a
        groups = org_find_duplicates(self.files())
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(f.rel for f in groups[0]), ["a.bin", "c.bin"])

    def test_large_identical_files_full_hash(self):
        data = os.urandom(2 * 1024 * KB + 5)              # > 1 MB-Block, ungerade
        self.write("x/one.iso", data)
        self.write("y/two.iso", data)
        groups = org_find_duplicates(self.files())
        self.assertEqual(len(groups), 1)
        import hashlib
        expect = hashlib.blake2b(data, digest_size=32).hexdigest()
        self.assertTrue(all(f.meta["hash"] == expect for f in groups[0]))

    def test_small_and_large_hash_consistent(self):
        import hashlib
        small = os.urandom(100)
        self.write("s1.txt", small)
        self.write("s2.txt", small)
        g = org_find_duplicates(self.files())
        self.assertEqual(g[0][0].meta["hash"], hashlib.blake2b(small, digest_size=32).hexdigest())

    def test_zero_byte_ignored(self):
        self.write("leer1.txt", b"")
        self.write("leer2.txt", b"")
        self.write("sub/leer3.txt", b"")
        self.assertEqual(org_find_duplicates(self.files()), [])

    def test_unreadable_skipped(self):
        data = b"z" * 4000
        self.write("a.txt", data)
        self.write("b.txt", data)
        files = self.files()
        ghost = mk("weg.txt", size=4000, root=str(self.root))       # existiert nicht
        dirlike = OrgFile(path=self.root, rel="ordner", size=4000, mtime=1, kind="other",
                          ext="")                                     # Ordner statt Datei
        groups = org_find_duplicates(files + [ghost, dirlike])
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(f.rel for f in groups[0]), ["a.txt", "b.txt"])

    def test_unreadable_via_mock(self):
        data = b"q" * 200 * KB
        self.write("a.bin", data)
        self.write("b.bin", data)
        self.write("c.bin", data)
        files = self.files()
        real_open = open

        def fake_open(path, *a, **k):
            if str(path).endswith("b.bin"):
                raise PermissionError("verweigert")
            return real_open(path, *a, **k)

        with mock.patch("builtins.open", fake_open):
            groups = org_find_duplicates(files)
        self.assertEqual(sorted(f.rel for f in groups[0]), ["a.bin", "c.bin"])

    def test_unique_size_never_read(self):
        self.write("a.bin", b"1" * 10)
        self.write("b.bin", b"2" * 11)
        opened = []
        real_open = open

        def spy(path, *a, **k):
            opened.append(str(path))
            return real_open(path, *a, **k)

        with mock.patch("builtins.open", spy):
            self.assertEqual(org_find_duplicates(self.files()), [])
        self.assertEqual(opened, [])

    def test_same_path_twice_not_a_duplicate(self):
        self.write("a.txt", b"hello world")
        f = self.files()[0]
        twin = mk("a.txt", size=f.size, root=str(self.root))
        self.assertEqual(org_find_duplicates([f, twin]), [])

    @unittest.skipUnless(hasattr(os, "link"), "keine Hardlinks")
    def test_hardlink_not_a_duplicate(self):
        p = self.write("a.txt", b"hardlink content")
        try:
            os.link(p, self.root / "b.txt")
        except OSError:
            self.skipTest("Hardlink nicht möglich")
        self.assertEqual(org_find_duplicates(self.files()), [])

    def test_cancel_returns_empty(self):
        data = b"c" * 1000
        for i in range(5):
            self.write("f%d.txt" % i, data)
        self.assertEqual(org_find_duplicates(self.files(), cancelled=lambda: True), [])

    def test_progress_monotonic_and_complete(self):
        big = os.urandom(300 * KB)
        self.write("a.bin", big)
        self.write("b.bin", big)
        self.write("c.bin", os.urandom(300 * KB))
        self.write("d.txt", b"x" * 50)
        self.write("e.txt", b"x" * 50)
        calls = []
        groups = org_find_duplicates(self.files(), progress=lambda d, t: calls.append((d, t)))
        self.assertEqual(len(groups), 2)
        self.assertTrue(calls)
        totals = {t for _, t in calls}
        self.assertEqual(len(totals), 1)
        dones = [d for d, _ in calls]
        self.assertEqual(dones, sorted(dones))
        self.assertEqual(calls[-1][0], calls[-1][1])
        # Gruppen: größte Ersparnis zuerst
        self.assertGreater(groups[0][0].size, groups[1][0].size)

    def test_bad_input_never_raises(self):
        self.assertEqual(org_find_duplicates(None), [])
        self.assertEqual(org_find_duplicates([]), [])
        weird = mk("x", size=None)
        self.assertEqual(org_find_duplicates([weird, object(), mk("y", size="abc")]), [])
        self.assertEqual(org_find_duplicates([mk("a", 5), mk("b", 5)],
                                             progress=lambda d, t: 1 / 0), [])

    def test_perf_20000_files(self):
        root = str(self.root)
        files = [mk("u/%05d.dat" % i, size=1000 + i, root=root) for i in range(20000)]
        # einige echte Kollisionen (Rest existiert gar nicht -> darf nie gelesen werden)
        for i in range(40):
            data = os.urandom(2000 + (i % 5))
            sz = len(data)
            self.write("c/%d_a.bin" % i, data)
            self.write("c/%d_b.bin" % i, data)
        files += [f for f in self.files() if f.rel.startswith("c/")]
        t0 = time.perf_counter()
        groups = org_find_duplicates(files)
        dt = time.perf_counter() - t0
        self.assertEqual(len(groups), 40)
        self.assertLess(dt, 2.0, "zu langsam: %.2fs" % dt)


# ---------------------------------------------------------------------------
class TestPickKeeper(unittest.TestCase):
    def pick(self, *files):
        return org_pick_keeper(list(files)).rel

    def test_rule1_project(self):
        a = mk("Dokumente/a.txt", mtime=1)
        b = mk("Projekt/src/a.txt", mtime=99, in_project=True)
        self.assertEqual(self.pick(a, b), "Projekt/src/a.txt")

    def test_rule2_download_temp_dirs(self):
        for weak in ("Downloads", "download", "Temp", "tmp", "Neuer Ordner",
                     "Neuer Ordner (2)", "New folder", "Desktop"):
            a = mk("%s/a.pdf" % weak, mtime=1)
            b = mk("Dokumente/Archiv/b.pdf", mtime=50)
            self.assertEqual(self.pick(a, b), "Dokumente/Archiv/b.pdf", weak)

    def test_rule2_only_dirs_not_filename(self):
        a = mk("Dokumente/Downloads.pdf", mtime=1)
        b = mk("Dokumente/x.pdf", mtime=2)
        self.assertEqual(self.pick(a, b), "Dokumente/Downloads.pdf")

    def test_rule3_copy_markers(self):
        for copy in ("Lebenslauf - Kopie.pdf", "Kopie von Lebenslauf.pdf",
                     "Copy of Lebenslauf.pdf", "Lebenslauf - Copy.pdf", "Lebenslauf (1).pdf",
                     "Lebenslauf(12).pdf", "Lebenslauf_copy.pdf", "Lebenslauf_kopie.pdf",
                     "Lebenslauf copy.pdf", "Lebenslauf - Kopie (2).pdf"):
            a = mk("Dok/" + copy, mtime=1)
            b = mk("Dok/Lebenslauf.pdf", mtime=5)
            self.assertEqual(self.pick(a, b), "Dok/Lebenslauf.pdf", copy)

    def test_rule3_no_false_copy(self):
        self.assertFalse(od._org_dupe_has_copy_marker("Copyright.pdf"))
        self.assertFalse(od._org_dupe_has_copy_marker("Rechnung (2024).pdf"))
        self.assertFalse(od._org_dupe_has_copy_marker("Kopierer Anleitung.pdf"))

    def test_rule4_oldest(self):
        self.assertEqual(self.pick(mk("a/x.txt", mtime=200), mk("b/y.txt", mtime=100)), "b/y.txt")

    def test_rule5_shallowest(self):
        self.assertEqual(self.pick(mk("a/b/c/x.txt"), mk("z/y.txt")), "z/y.txt")

    def test_rule6_shortest_rel(self):
        self.assertEqual(self.pick(mk("aaaa/x.txt"), mk("b/x.txt")), "b/x.txt")

    def test_rule7_alphabetical(self):
        self.assertEqual(self.pick(mk("b/x.txt"), mk("a/x.txt")), "a/x.txt")

    def test_order_project_beats_everything(self):
        a = mk("Downloads/Kopie von x.txt", mtime=999, in_project=True)
        b = mk("x.txt", mtime=1)
        self.assertEqual(self.pick(a, b), a.rel)

    def test_empty(self):
        self.assertIsNone(org_pick_keeper([]))
        self.assertIsNone(org_pick_keeper(None))


# ---------------------------------------------------------------------------
class TestMarkDuplicates(unittest.TestCase):
    def test_marks_non_keepers(self):
        k = mk("Dokumente/Zeugnis.pdf", mtime=1, category="certificate", tier=ORG_PROTECT)
        c1 = mk("Downloads/Zeugnis (1).pdf", mtime=5, category="certificate", tier=ORG_PROTECT)
        c2 = mk("Desktop/Zeugnis - Kopie.pdf", mtime=6)
        n = org_mark_duplicates([k, c1, c2], [[c1, k, c2]])
        self.assertEqual(n, 2)
        self.assertEqual(k.tier, ORG_PROTECT)
        self.assertEqual(k.dup_of, "")
        self.assertEqual(k.category, "certificate")
        for c in (c1, c2):
            self.assertEqual(c.dup_of, "Dokumente/Zeugnis.pdf")
            self.assertEqual(c.category, "duplicate")
            self.assertEqual(c.tier, ORG_JUNK)
            self.assertIn("Exakte Kopie von Dokumente/Zeugnis.pdf", c.reasons)

    def test_in_project_copies_never_marked(self):
        a = mk("Dok/a.txt", mtime=1)
        p = mk("Proj/a.txt", mtime=9, in_project=True, tier=ORG_KEEP)
        q = mk("Proj2/a.txt", mtime=9, in_project=True, tier=ORG_KEEP)
        n = org_mark_duplicates([], [[a, p, q]])
        # Keeper ist eine Projektdatei, die anderen Projektdateien bleiben unberührt,
        # nur die Kopie außerhalb wird markiert.
        self.assertEqual(n, 1)
        self.assertEqual(a.tier, ORG_JUNK)
        self.assertIn(a.dup_of, ("Proj/a.txt", "Proj2/a.txt"))
        self.assertEqual(p.tier, ORG_KEEP)
        self.assertEqual(q.tier, ORG_KEEP)
        self.assertEqual((p.dup_of, q.dup_of), ("", ""))

    def test_never_marks_keeper_or_same_path(self):
        a = mk("x.txt")
        a2 = mk("x.txt")
        self.assertEqual(org_mark_duplicates([], [[a, a2]]), 0)
        self.assertEqual(org_mark_duplicates([], [[a]]), 0)
        self.assertEqual(org_mark_duplicates([], None), 0)
        self.assertEqual(org_mark_duplicates([], [None, [], [object(), object()]]), 0)

    def test_end_to_end_with_walk(self):
        root = Path(tempfile.mkdtemp(prefix="orgdupes_e2e_"))
        try:
            data = os.urandom(1234)
            (root / "Dokumente").mkdir()
            (root / "Downloads").mkdir()
            (root / "proj").mkdir()
            (root / "proj" / "package.json").write_text("{}")
            (root / "proj" / "index.js").write_text("// Projektcode")  # schwacher Marker + Code
            (root / "Dokumente" / "Vertrag.pdf").write_bytes(data)
            (root / "Downloads" / "Vertrag (1).pdf").write_bytes(data)
            (root / "proj" / "Vertrag.pdf").write_bytes(data)
            files = org_walk(root)[0]
            groups = org_find_duplicates(files)
            self.assertEqual(len(groups), 1)
            self.assertEqual(len(groups[0]), 3)
            n = org_mark_duplicates(files, groups)
            self.assertEqual(n, 2)
            rel = {f.rel: f for f in files}
            self.assertEqual(rel["Downloads/Vertrag (1).pdf"].dup_of, "proj/Vertrag.pdf")
            self.assertEqual(rel["Dokumente/Vertrag.pdf"].dup_of, "proj/Vertrag.pdf")
            self.assertEqual(rel["proj/Vertrag.pdf"].dup_of, "")
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (Name, erwarteter Schlüssel oder None)
VERSION_CASES = [
    # --- Negativfälle: keine Marker ---
    ("Rechnung_1234.pdf", None),
    ("Rechnung_1235.pdf", None),
    ("Kontoauszug 2024-01.pdf", None),
    ("Kontoauszug_2024_02.pdf", None),
    ("Foto 2.jpg", None),
    ("IMG_20240712_1234.jpg", None),
    ("Lebenslauf.docx", None),
    ("Bewerbung Firma 2023.pdf", None),
    ("Altersvorsorge.pdf", None),
    ("Neuigkeiten.txt", None),
    ("Copyright.txt", None),
    ("New York Reise Planung.pdf", "york reise planung.pdf"),   # "new" am Rand -> Marker
    ("Reise New York Planung.pdf", None),                       # schwacher Marker mitten drin
    ("Update Router Anleitung.pdf", "router anleitung.pdf"),
    ("Router Update Anleitung.pdf", None),
    ("Finanzen.xlsx", None),
    ("Kapitel 3.docx", None),
    ("", None),
    ("final.docx", None),                                       # nichts übrig
    ("Kopie (2).pdf", None),
    # --- Positivfälle ---
    ("Lebenslauf_final.docx", "lebenslauf.docx"),
    ("Lebenslauf_final (1).docx", "lebenslauf.docx"),
    ("Lebenslauf_final_FINAL (2).docx", "lebenslauf.docx"),
    ("Lebenslauf final_final.docx", "lebenslauf.docx"),
    ("Lebenslauf_endgültig.docx", "lebenslauf.docx"),
    ("Lebenslauf_endgueltig.docx", "lebenslauf.docx"),
    ("LEBENSLAUF ENDGÜLTIG.docx", "lebenslauf.docx"),
    ("Lebenslauf_Endversion.docx", "lebenslauf.docx"),
    ("Lebenslauf fertig.docx", "lebenslauf.docx"),
    ("Lebenslauf_neu.docx", "lebenslauf.docx"),
    ("Lebenslauf neu 2.docx", "lebenslauf.docx"),
    ("Lebenslauf_neueste.docx", "lebenslauf.docx"),
    ("Lebenslauf_aktuell.docx", "lebenslauf.docx"),
    ("Lebenslauf_alt.docx", "lebenslauf.docx"),
    ("Lebenslauf_old.docx", "lebenslauf.docx"),
    ("Lebenslauf - Kopie.docx", "lebenslauf.docx"),
    ("Lebenslauf - Kopie (3).docx", "lebenslauf.docx"),
    ("Kopie von Lebenslauf.docx", "lebenslauf.docx"),
    ("Copy of CV.docx", "cv.docx"),
    ("CV - Copy.docx", "cv.docx"),
    ("CV (12).docx", "cv.docx"),
    ("CV(1).docx", "cv.docx"),
    ("CV_new.docx", "cv.docx"),
    ("CV newest.docx", "cv.docx"),
    ("CV latest.docx", "cv.docx"),
    ("CV current.docx", "cv.docx"),
    ("CV_final_2.docx", "cv.docx"),
    ("CV final2.docx", "cv.docx"),
    ("Bericht v2.docx", "bericht.docx"),
    ("Bericht_V12.docx", "bericht.docx"),
    ("Bericht v1.2.docx", "bericht.docx"),
    ("Bericht ver2.docx", "bericht.docx"),
    ("Bericht Version 3.docx", "bericht.docx"),
    ("Bericht_version_2.1.docx", "bericht.docx"),
    ("Bericht rev3.docx", "bericht.docx"),
    ("Bericht_r2.docx", "bericht.docx"),
    ("Bericht draft.docx", "bericht.docx"),
    ("Bericht_Entwurf.docx", "bericht.docx"),
    ("Bericht korrigiert.docx", "bericht.docx"),
    ("Bericht_Korrektur.docx", "bericht.docx"),
    ("Bericht überarbeitet.docx", "bericht.docx"),
    ("Bericht_ueberarbeitet.docx", "bericht.docx"),
    ("Bericht edit.docx", "bericht.docx"),
    ("Bericht_edited.docx", "bericht.docx"),
    ("Bericht updated.docx", "bericht.docx"),
    ("Bericht_update.docx", "bericht.docx"),
    ("Bericht bearbeitet.docx", "bericht.docx"),
    ("Bericht_backup.docx", "bericht.docx"),
    ("Bericht Sicherung.docx", "bericht.docx"),
    ("Bericht_v2_Mueller.docx", "bericht mueller.docx"),
    ("Bericht_v2_Müller.docx", "bericht mueller.docx"),
    ("Foto 2 (1).jpg", "foto 2.jpg"),
    ("Rechnung_1234 (1).pdf", "rechnung 1234.pdf"),
    ("Kontoauszug 2024-01 - Kopie.pdf", "kontoauszug 2024 01.pdf"),
    ("Urlaub.JPEG (1).jpeg", "urlaub jpeg.jpg"),
    ("Bild_final.JPEG", "bild.jpg"),
    ("Café final.txt", "cafe.txt"),
]


class TestVersionKey(unittest.TestCase):
    def test_cases(self):
        self.assertGreaterEqual(len(VERSION_CASES), 40)
        bad = []
        for name, expect in VERSION_CASES:
            got = org_version_key(name)
            if got != expect:
                bad.append((name, expect, got))
        self.assertEqual(bad, [])

    def test_same_key_as_base(self):
        base = org_version_base("Lebenslauf.docx")
        self.assertEqual(base, "lebenslauf.docx")
        self.assertEqual(org_version_key("Lebenslauf_final_FINAL (2).docx"), base)
        self.assertEqual(org_version_base("Foto 2.jpg"), "foto 2.jpg")

    def test_extension_families_differ(self):
        keys = {org_version_key(n) for n in ("CV_final.docx", "CV_final.doc", "CV_final.pdf")}
        self.assertEqual(len(keys), 3)
        self.assertEqual(org_version_key("Bild_final.jpeg"), org_version_key("Bild_alt.jpg"))

    def test_numbers_not_versions(self):
        self.assertNotEqual(org_version_base("Rechnung_1234.pdf"),
                            org_version_base("Rechnung_1235.pdf"))
        self.assertNotEqual(org_version_base("Kontoauszug_2024_01.pdf"),
                            org_version_base("Kontoauszug_2024_02.pdf"))

    def test_bad_input(self):
        for bad in (None, 123, b"x", "   ", ".", "..", "()", "/"):
            self.assertIsNone(org_version_key(bad))
            self.assertIsInstance(org_version_base(bad), str)


# ---------------------------------------------------------------------------
class TestVersionGroups(unittest.TestCase):
    def test_group_with_unmarked_base(self):
        a = mk("Bewerbung/Lebenslauf.docx", mtime=100)
        b = mk("Bewerbung/Lebenslauf_final.docx", mtime=200)
        c = mk("Bewerbung/Lebenslauf_final (1).docx", mtime=300)
        d = mk("Bewerbung/Lebenslauf.pdf", mtime=400)          # andere Familie
        e = mk("Andere/Lebenslauf_final.docx", mtime=500)     # anderer Ordner, allein
        groups = org_version_groups([a, b, c, d, e])
        self.assertEqual(list(groups), ["Bewerbung/lebenslauf.docx"])
        self.assertEqual({f.rel for f in groups["Bewerbung/lebenslauf.docx"]},
                         {a.rel, b.rel, c.rel})
        n = org_mark_versions(groups)
        self.assertEqual(n, 2)
        self.assertTrue(c.version_latest)
        self.assertEqual(c.version_group, "Bewerbung/lebenslauf.docx")
        self.assertEqual(c.reasons, [])
        for f in (a, b):
            self.assertFalse(f.version_latest)
            self.assertEqual(f.tier, ORG_REVIEW)
            self.assertEqual(f.version_group, "Bewerbung/lebenslauf.docx")
            self.assertIn("Ältere Version von Lebenslauf_final (1).docx", f.reasons)
        self.assertTrue(d.version_latest)
        self.assertEqual(d.version_group, "")

    def test_no_marker_no_group(self):
        a = mk("Rechnung_1234.pdf")
        b = mk("Rechnung_1235.pdf")
        c = mk("Foto 1.jpg")
        d = mk("Foto 2.jpg")
        self.assertEqual(org_version_groups([a, b, c, d]), {})

    def test_single_marked_alone_no_group(self):
        self.assertEqual(org_version_groups([mk("CV_final.docx")]), {})

    def test_excludes_project_and_duplicates(self):
        a = mk("Doc/Bericht.docx")
        b = mk("Doc/Bericht_v2.docx", in_project=True)
        c = mk("Doc/Bericht - Kopie.docx", category="duplicate", tier=ORG_JUNK)
        c.dup_of = "Doc/Bericht.docx"
        self.assertEqual(org_version_groups([a, b, c]), {})

    def test_protect_old_version_becomes_review_not_junk(self):
        a = mk("Zeugnis_alt.pdf", mtime=1, category="certificate", tier=ORG_PROTECT)
        b = mk("Zeugnis.pdf", mtime=2, category="certificate", tier=ORG_PROTECT)
        n = org_mark_versions(org_version_groups([a, b]))
        self.assertEqual(n, 1)
        self.assertEqual(a.tier, ORG_REVIEW)
        self.assertEqual(b.tier, ORG_PROTECT)
        self.assertNotEqual(a.tier, ORG_JUNK)

    def test_mark_versions_bad_input(self):
        self.assertEqual(org_mark_versions(None), 0)
        self.assertEqual(org_mark_versions({"k": [mk("a")]}), 0)
        self.assertEqual(org_mark_versions({"k": [None, object(), object()]}), 0)
        self.assertEqual(org_version_groups(None), {})
        self.assertEqual(org_version_groups([None, object()]), {})

    def test_dupes_then_versions_pipeline(self):
        k = mk("Dok/Lebenslauf.docx", mtime=1)
        cp = mk("Dok/Lebenslauf - Kopie.docx", mtime=2)
        v = mk("Dok/Lebenslauf_final.docx", mtime=3)
        org_mark_duplicates([], [[k, cp]])
        g = org_version_groups([k, cp, v])
        self.assertEqual({f.rel for f in g["Dok/lebenslauf.docx"]}, {k.rel, v.rel})

    def test_perf_60000_names(self):
        rnd = random.Random(1)
        words = ["Rechnung", "Lebenslauf", "Bericht", "Foto", "Scan", "Vertrag"]
        marks = ["", "_final", " (1)", "_v2", " - Kopie", "_neu", "_2024_01"]
        files = [mk("d%d/%s_%d%s.pdf" % (i % 300, rnd.choice(words), rnd.randint(1, 500),
                                         rnd.choice(marks)), mtime=i) for i in range(60000)]
        t0 = time.perf_counter()
        g = org_version_groups(files)
        org_mark_versions(g)
        dt = time.perf_counter() - t0
        self.assertGreater(len(g), 0)
        self.assertLess(dt, 3.0, "zu langsam: %.2fs" % dt)


if __name__ == "__main__":
    prog = unittest.main(exit=False, verbosity=1)
    res = prog.result
    total = res.testsRun
    failed = len(res.failures) + len(res.errors)
    print("org_dupes: %d Tests, %d fehlgeschlagen, %d übersprungen (Python %s)"
          % (total, failed, len(res.skipped), sys.version.split()[0]))
    sys.exit(0 if res.wasSuccessful() else 1)
