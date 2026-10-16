# Adversarial-Tests (Datensicherheit) für org_planner.py
# Aufruf: python test_org_planner_data_safety.py   (Exit-Code 0 = alles bestanden)
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import org_planner  # noqa: E402
from org_core import ORG_CATEGORIES, ORG_JUNK, ORG_PROTECT, ORG_REVIEW, OrgFile  # noqa: E402
from org_planner import (OrgMove, org_analyze, org_apply_moves, org_backup,  # noqa: E402
                         org_trash, org_undo)
from test_org_planner import (NOW, DAY, PROGRAMS, build_tree, by_rel, make_docx,  # noqa: E402
                              make_jpeg, make_pdf, snapshot, write)

HAVE_SYMLINK = True
try:
    _t = Path(tempfile.mkdtemp(prefix="orgsafe_probe_"))
    os.symlink(str(_t), str(_t / "l"))
except (OSError, NotImplementedError, AttributeError):
    HAVE_SYMLINK = False
finally:
    shutil.rmtree(str(_t), ignore_errors=True)


def sha(p) -> str:
    with open(str(p), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def contents(root: Path) -> set:
    return {v for k, v in snapshot(root).items() if k.startswith("F:")}


def unlink_fn(p) -> bool:
    os.unlink(p)
    return True


def set_mtime(p, t) -> None:
    os.utime(str(p), (t, t))


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="orgsafe_"))
        self.root = self.tmp / "Dokumente"
        self.root.mkdir()
        self.journal = self.tmp / "journal" / "j.json"

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def analyze(self, **kw):
        kw.setdefault("programs", PROGRAMS)
        kw.setdefault("now", NOW)
        return org_analyze(self.root, **kw)


# ---------------------------------------------------------------------------
# Papierkorb
# ---------------------------------------------------------------------------
class TrashSafety(_Tmp):
    def _pair(self, data=None, name="Notiz.txt"):
        data = data or "Inhalt der Notiz – nur einmal wirklich wichtig"
        write(self.root, name, data, 50)
        write(self.root, "Alt/" + name, data, 40)
        plan = self.analyze()
        f = by_rel(plan)
        a, b = f[name], f["Alt/" + name]
        copy = a if a.dup_of else b
        keeper = b if copy is a else a
        self.assertTrue(copy.dup_of, "Testvoraussetzung: Duplikat erkannt")
        return plan, keeper, copy

    def test_keeper_modified_same_size_copy_survives(self):
        # Original nach der Analyse geändert (gleiche Größe) → Kopie ist die letzte
        # Fassung des alten Inhalts und darf NICHT gelöscht werden
        plan, keeper, copy = self._pair()
        data = bytearray(keeper.path.read_bytes())
        data[0] = ord("X")
        keeper.path.write_bytes(bytes(data))
        n, fails = org_trash([copy], unlink_fn)
        self.assertEqual(n, 0, fails)
        self.assertTrue(copy.path.is_file())

    def test_keeper_touched_but_identical_copy_may_go(self):
        # nur Zeitstempel geändert, Inhalt gleich → Byte-Vergleich erlaubt das Löschen
        plan, keeper, copy = self._pair()
        set_mtime(keeper.path, NOW - 5 * DAY)
        n, fails = org_trash([copy], unlink_fn)
        self.assertEqual((n, fails), (1, []))
        self.assertTrue(keeper.path.is_file())

    def test_copy_modified_same_size_refused(self):
        plan, keeper, copy = self._pair()
        data = bytearray(copy.path.read_bytes())
        data[0] = ord("Y")
        copy.path.write_bytes(bytes(data))              # neue mtime, gleiche Größe
        n, fails = org_trash([copy], unlink_fn)
        self.assertEqual(n, 0)
        self.assertTrue(any("verändert" in x for x in fails), fails)
        self.assertTrue(copy.path.is_file())

    def test_copy_then_keeper_same_call_one_survives(self):
        for order in ("copy_first", "keeper_first"):
            shutil.rmtree(str(self.root))
            self.root.mkdir()
            plan, keeper, copy = self._pair()
            self.assertNotEqual(keeper.tier, ORG_PROTECT)   # ungeschützt: Notiz
            data = keeper.path.read_bytes()
            seq = [copy, keeper] if order == "copy_first" else [keeper, copy]
            n, fails = org_trash(seq, unlink_fn)
            self.assertEqual(n, 1, (order, fails))
            left = [p for p in (keeper.path, copy.path) if p.is_file()]
            self.assertEqual(len(left), 1, order)
            self.assertEqual(left[0].read_bytes(), data)

    def test_trash_fn_fails_after_deleting_keeper_still_kept(self):
        plan, keeper, copy = self._pair()

        def liar(p):                    # löscht, meldet aber Fehler
            os.unlink(p)
            return False
        n, fails = org_trash([copy, keeper], liar)
        self.assertEqual(n, 0)
        self.assertFalse(copy.path.exists())
        self.assertTrue(keeper.path.is_file(),
                        "Original darf nach verlorener Kopie nicht auch weg")

    def test_keeper_path_aliases_copy_via_symlink(self):
        if not HAVE_SYMLINK:
            self.skipTest("keine Symlinks")
        data = "gleich"
        write(self.root, "A/x.txt", data, 50)
        write(self.root, "B/x.txt", data, 50)
        set_mtime(self.root / "A/x.txt", NOW - 50 * DAY)
        set_mtime(self.root / "B/x.txt", NOW - 50 * DAY)
        plan = self.analyze()
        f = by_rel(plan)
        copy = f["A/x.txt"] if f["A/x.txt"].dup_of else f["B/x.txt"]
        keeper = f["B/x.txt"] if copy is f["A/x.txt"] else f["A/x.txt"]
        # Ordner des Originals wird durch einen Link auf den Ordner der Kopie ersetzt
        kdir = keeper.path.parent
        shutil.rmtree(str(kdir))
        os.symlink(str(copy.path.parent), str(kdir))
        n, fails = org_trash([copy], unlink_fn)
        self.assertEqual(n, 0, fails)
        self.assertTrue(copy.path.is_file())

    def test_file_through_link_outside_root_refused(self):
        if not HAVE_SYMLINK:
            self.skipTest("keine Symlinks")
        outside = self.tmp / "Extern"
        outside.mkdir()
        victim = write(outside, "wichtig.tmp", "fremde Datei", 300)
        os.symlink(str(outside), str(self.root / "link"))
        st = victim.stat()
        fake = OrgFile(path=self.root / "link" / "wichtig.tmp", rel="link/wichtig.tmp",
                       size=st.st_size, mtime=st.st_mtime, kind="other", ext=".tmp", depth=1,
                       category="temp", tier=ORG_JUNK)
        n, fails = org_trash([fake], unlink_fn)
        self.assertEqual(n, 0)
        self.assertTrue(victim.is_file())

    def test_old_version_and_demoted_important_never_trashed(self):
        # zwei VERSCHIEDENE Zeugnisse, eines davon mit „(1)“ → Versionsgruppe;
        # das ältere (Stufe „Prüfen“) darf trotzdem nie gelöscht werden
        write(self.root, "Zeugnis.pdf", make_pdf("Arbeitszeugnis Firma A"), 300)
        write(self.root, "Zeugnis (1).pdf", make_pdf("Arbeitszeugnis Firma B GmbH"), 100)
        write(self.root, "Lebenslauf_v1.docx", make_docx("Lebenslauf alt"), 300)
        write(self.root, "Lebenslauf_v2.docx", make_docx("Lebenslauf neu und länger"), 100)
        plan = self.analyze()
        everything = list(plan.files)
        for f in everything:
            self.assertIn(ORG_CATEGORIES[f.category]["tier"], (ORG_PROTECT,), f.rel)
        n, fails = org_trash(everything, unlink_fn)
        self.assertEqual(n, 0, fails)
        self.assertEqual(len(list(self.root.iterdir())), 4)

    def test_modified_mtime_only_same_size_refused(self):
        p = write(self.root, "~WRL0001.tmp", b"\x01" * 300, 50)
        plan = self.analyze()
        f = by_rel(plan)["~WRL0001.tmp"]
        self.assertEqual(f.tier, ORG_JUNK)
        p.write_bytes(b"\x02" * 300)                    # gleiche Größe, neuer Inhalt
        n, fails = org_trash([f], unlink_fn)
        self.assertEqual(n, 0)
        self.assertTrue(p.is_file())


class TrashWorstCase(unittest.TestCase):
    """Nutzer wählt im Papierkorb-Dialog ALLES aus: kein wichtiger Inhalt geht verloren,
    und jeder Inhalt eines Duplikats existiert danach noch mindestens einmal."""

    def test_trash_everything(self):
        tmp = Path(tempfile.mkdtemp(prefix="orgsafe_all_"))
        try:
            root = build_tree(tmp)
            plan = org_analyze(root, programs=PROGRAMS, now=NOW)
            important = {}
            dup_contents = set()
            for f in plan.files:
                cat = f.meta.get("orig_category") if f.category == "duplicate" else f.category
                if ORG_CATEGORIES.get(cat, {}).get("tier") == ORG_PROTECT or f.in_project:
                    important[f.rel] = sha(f.path)
                if f.dup_of:
                    dup_contents.add(sha(f.path))
            self.assertGreater(len(important), 20)
            files = list(plan.files)
            random.Random(7).shuffle(files)
            n, fails = org_trash(files, unlink_fn)
            self.assertGreater(n, 5)
            left = contents(root)
            for rel, h in important.items():
                self.assertIn(h, left, "wichtiger Inhalt verloren: %s" % rel)
            for h in dup_contents:
                self.assertIn(h, left, "letzte Kopie eines Duplikats gelöscht")
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)


# ---------------------------------------------------------------------------
# Analyse: falsche Löschvorschläge
# ---------------------------------------------------------------------------
class AnalysisSafety(_Tmp):
    def test_copy_of_project_file_is_review_not_junk(self):
        thesis = make_pdf("Masterarbeit\nKapitel 1")
        write(self.root, "Projekte/thesis/.git/HEAD", "ref", 10)
        write(self.root, "Projekte/thesis/build/thesis.pdf", thesis, 10)
        write(self.root, "Masterarbeit_final.pdf", thesis, 30)
        plan = self.analyze()
        f = by_rel(plan)["Masterarbeit_final.pdf"]
        self.assertTrue(f.dup_of)
        self.assertNotEqual(f.tier, ORG_JUNK)
        self.assertNotIn(f, plan.junk)

    def test_no_moves_into_project_folder(self):
        write(self.root, "Projekte/.git/HEAD", "ref", 10)
        write(self.root, "Projekte/main.py", "print(1)", 10)
        write(self.root, "skript.py", "print(2)", 10)
        write(self.root, "Modell.stl", "solid x\nendsolid x\n", 10)
        plan = self.analyze()
        for m in plan.moves:
            self.assertFalse(m.dest_rel.casefold().startswith("projekte"), m.dest_rel)
        # auch eine untergeschobene Verschiebung wird beim Ausführen abgelehnt
        f = by_rel(plan)["skript.py"]
        before = snapshot(self.root)
        n, fails = org_apply_moves(plan, [OrgMove(f, "Projekte/neu", True),
                                          OrgMove(f, "Projekte", True)], self.journal)
        self.assertEqual(n, 0)
        self.assertEqual(len(fails), 2)
        self.assertEqual(snapshot(self.root), before)

    def test_protected_parent_above_root(self):
        write(self.root, "Unter/Lebenslauf.pdf.tmp", "x" * 50, 30)
        write(self.root, "Unter/a.txt", "gleich", 30)
        write(self.root, "Unter/b/a.txt", "gleich", 20)
        plan = org_analyze(self.root / "Unter", programs=PROGRAMS, now=NOW,
                           protected_paths={str(self.root)})
        self.assertEqual(plan.junk, [])
        for f in plan.files:
            self.assertEqual(f.tier, ORG_PROTECT, f.rel)
        n, _ = org_trash(plan.files, unlink_fn)
        self.assertEqual(n, 0)

    def test_protected_dir_not_listed_as_empty(self):
        (self.root / "Wichtig leer").mkdir()
        (self.root / "Sonst leer").mkdir()
        write(self.root, "a.txt", "x", 10)
        plan = self.analyze(protected_paths={str(self.root / "Wichtig leer")})
        names = {Path(d).name for d in plan.empty_dirs}
        self.assertNotIn("Wichtig leer", names)
        self.assertIn("Sonst leer", names)

    def test_link_dirs_untouched(self):
        # Windows-Junctions werden von org_walk betreten → simuliert per Injektion
        cv = make_pdf("Lebenslauf\nBerufserfahrung")
        write(self.root, "Lebenslauf.pdf", cv, 300)
        write(self.root, "Verknüpfung/Lebenslauf.pdf", cv, 100)
        write(self.root, "Verknüpfung/notiz.tmp", "t", 300)
        write(self.root, "Verknüpfung/Rechnung_2020.pdf", make_pdf("Rechnung 2020"), 300)
        write(self.root, "Notiz.txt", "gleich", 300)
        write(self.root, "Verknüpfung/Notiz.txt", "gleich", 10)
        (self.root / "Verknüpfung" / "leer").mkdir()
        real = org_planner._org_plan_is_link_dir
        link = org_planner._org_plan_key(self.root / "Verknüpfung")
        org_planner._org_plan_is_link_dir = \
            lambda p, lstat=None: org_planner._org_plan_key(p) == link
        try:
            plan = self.analyze(reorganize_subfolders=True)
        finally:
            org_planner._org_plan_is_link_dir = real
        self.assertEqual(plan.stats.get("link_dirs"), ["Verknüpfung"])
        moved = {id(m.file) for m in plan.moves}
        for f in plan.files:
            if f.rel.startswith("Verknüpfung/"):
                self.assertTrue(f.in_project, f.rel)
                self.assertNotIn(f, plan.junk)
                self.assertNotIn(id(f), moved)
                self.assertFalse(f.dup_of)
        self.assertNotIn(by_rel(plan)["Notiz.txt"], plan.junk)   # Original nur im Link
        self.assertFalse(any("Verknüpfung" in str(d) for d in plan.empty_dirs))
        n, _ = org_trash([f for f in plan.files if f.rel.startswith("Verknüpfung/")],
                         unlink_fn)
        self.assertEqual(n, 0)

    def test_is_link_dir_reparse_tags(self):
        class St(object):
            def __init__(self, attrs, tag=0):
                self.st_mode = 0o040755
                self.st_file_attributes = attrs
                self.st_reparse_tag = tag
        fn = org_planner._org_plan_is_link_dir
        self.assertTrue(fn("x", lstat=lambda p: St(0x410, 0xA0000003)))   # Junction
        self.assertTrue(fn("x", lstat=lambda p: St(0x410, 0xA000000C)))   # Symlink
        self.assertFalse(fn("x", lstat=lambda p: St(0x410, 0x9000601A)))  # OneDrive
        self.assertFalse(fn("x", lstat=lambda p: St(0x10)))
        self.assertFalse(fn("x", lstat=lambda p: (_ for _ in ()).throw(OSError())))
        d = self.root / "echt"
        d.mkdir()
        self.assertFalse(fn(d))
        if HAVE_SYMLINK:
            os.symlink(str(d), str(self.root / "lnk"))
            self.assertTrue(fn(self.root / "lnk"))

    def test_fuzz_invariants(self):
        """Zufällige Bäume mit wichtigen Namen in Download-/Temp-Ordnern, Kopien,
        Projekten: Invarianten der Analyse."""
        names = ["Lebenslauf.pdf", "Zeugnis.pdf", "Arbeitsvertrag.pdf", "Steuerbescheid_2022.pdf",
                 "Personalausweis.jpg", "IMG_0001.JPG", "Rechnung_2021.pdf", "Notiz.txt",
                 "Kopie von Lebenslauf.pdf", "Lebenslauf (1).pdf", "setup.exe", "x.tmp",
                 "Lebenslauf.pdf.crdownload", "Gehaltsabrechnung_2023_01.pdf", "Mietvertrag.docx"]
        dirs = ["", "Downloads", "Temp", "Neuer Ordner", "Alt/2019", "Desktop", "Backup",
                "Projekt/.git", "Projekt/docs"]
        payloads = [make_pdf("Lebenslauf Berufserfahrung"), make_pdf("Zeugnis"), b"abc" * 30,
                    make_jpeg("2023:07:10 10:00:00", (41.9, 12.5), salt="f"), b""]
        for seed in range(6):
            shutil.rmtree(str(self.root))
            self.root.mkdir()
            rnd = random.Random(seed)
            for i in range(60):
                d = rnd.choice(dirs)
                nm = rnd.choice(names)
                rel = (d + "/" if d else "") + nm
                if (self.root / rel).exists():
                    continue
                write(self.root, rel, rnd.choice(payloads), rnd.uniform(0.0, 900.0))
            before = snapshot(self.root)
            plan = self.analyze(reorganize_subfolders=bool(seed % 2))
            self.assertEqual(snapshot(self.root), before)
            f = by_rel(plan)
            self.assertNotIn("error", plan.stats)
            for x in plan.files:
                if x.in_project:
                    self.assertNotIn(x, plan.junk)
                    self.assertFalse(x.dup_of)
                if x.tier == ORG_JUNK:
                    self.assertIn(x.category, ("temp", "duplicate", "installer"), x.rel)
                    if ORG_CATEGORIES.get(x.meta.get("orig_category", ""), {}).get("tier") \
                            == ORG_PROTECT:
                        self.assertTrue(x.dup_of, x.rel)
                if x.dup_of:
                    k = x.meta.get(org_planner._ORG_PLAN_DUP_KEEPER) or f[x.dup_of]
                    self.assertNotEqual(k.tier, ORG_JUNK, x.rel)
                    self.assertFalse(k.dup_of, x.rel)
                    self.assertEqual(sha(k.path), sha(x.path), x.rel)
            for g in plan.dup_groups:
                self.assertTrue(any(m.tier != ORG_JUNK for m in g))
            for m in plan.moves:
                self.assertFalse(m.file.in_project or m.file.dup_of or m.file.tier == ORG_JUNK)
            # alles in den Papierkorb → kein wichtiger Inhalt geht verloren
            imp = {sha(x.path) for x in plan.files if x.size > 0 and (
                x.in_project or ORG_CATEGORIES.get(
                    x.meta.get("orig_category") if x.dup_of else x.category, {}
                ).get("tier") == ORG_PROTECT)}
            org_trash(list(plan.files), unlink_fn)
            left = contents(self.root)
            self.assertEqual(imp - left, set(), "seed %d" % seed)


# ---------------------------------------------------------------------------
# Verschieben / Rückgängig
# ---------------------------------------------------------------------------
class ApplyUndoSafety(_Tmp):
    def _simple(self):
        write(self.root, "Notizen.txt", "Milch Brot", 30)
        write(self.root, "Rechnung_IKEA_2024.pdf", make_pdf("Rechnung IKEA"), 200)
        write(self.root, "Alt/Einkauf.txt", "Tomaten", 30)
        return self.analyze(reorganize_subfolders=True)

    def test_apply_skips_files_changed_since_analysis(self):
        plan = self._simple()
        mv = {m.file.rel: m for m in plan.moves}
        p = self.root / "Notizen.txt"
        p.write_text("Milch Brot Käse")                       # Größe geändert
        q = self.root / "Alt/Einkauf.txt"
        q.write_text("Gurken!")                               # gleiche Größe, neue Zeit
        set_mtime(q, time.time() + 5)
        n, fails = org_apply_moves(plan, [mv["Notizen.txt"], mv["Alt/Einkauf.txt"],
                                          mv["Rechnung_IKEA_2024.pdf"]], self.journal)
        self.assertEqual(n, 1)
        self.assertEqual(len(fails), 2)
        self.assertTrue(all("verändert" in x for x in fails), fails)
        self.assertTrue(p.is_file() and q.is_file())

    def test_apply_never_moves_its_own_journal(self):
        plan = self._simple()
        journal = self.root / "organizer-journal.json"
        journal.write_text(json.dumps({"root": str(plan.root), "created": [], "moves": []}))
        st = journal.stat()
        fake = OrgFile(path=journal, rel=journal.name, size=st.st_size, mtime=st.st_mtime,
                       kind="code", ext=".json")
        before = snapshot(self.root)
        moves = [OrgMove(fake, "Projekte", True)] + list(plan.moves)
        n, fails = org_apply_moves(plan, moves, journal)
        self.assertEqual(n, len(plan.moves))
        self.assertTrue(any("Protokolldatei" in x for x in fails), fails)
        self.assertTrue(journal.is_file())
        data = json.loads(journal.read_text("utf-8"))
        self.assertEqual(len(data["moves"]), n)
        r, msgs = org_undo(journal)
        self.assertEqual(r, n)
        after = snapshot(self.root)
        before.pop("F:organizer-journal.json")
        self.assertEqual({k: v for k, v in after.items() if "organizer-journal" not in k},
                         before)

    def test_crash_between_link_and_unlink_undo_exact(self):
        if os.name == "nt":
            self.skipTest("POSIX-Pfad (link+unlink)")
        plan = self._simple()
        before = snapshot(self.root)

        class Crash(BaseException):
            pass
        real = org_planner._org_plan_rename_noreplace
        calls = {"n": 0}

        def half(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:
                os.link(os.fspath(src), os.fspath(dst))       # Absturz nach „link“
                raise Crash()
            return real(src, dst)
        org_planner._org_plan_rename_noreplace = half
        try:
            with self.assertRaises(Crash):
                org_apply_moves(plan, plan.moves, self.journal)
        finally:
            org_planner._org_plan_rename_noreplace = real
        r, msgs = org_undo(self.journal)
        self.assertEqual(snapshot(self.root), before, msgs)

    def test_undo_does_not_restore_through_link_outside(self):
        if not HAVE_SYMLINK:
            self.skipTest("keine Symlinks")
        plan = self._simple()
        mv = {m.file.rel: m for m in plan.moves}
        n, _ = org_apply_moves(plan, [mv["Alt/Einkauf.txt"]], self.journal)
        self.assertEqual(n, 1)
        moved_to = mv["Alt/Einkauf.txt"].file.path
        outside = self.tmp / "Draussen"
        outside.mkdir()
        shutil.rmtree(str(self.root / "Alt"))
        os.symlink(str(outside), str(self.root / "Alt"))      # Ursprungsordner → außen
        r, msgs = org_undo(self.journal)
        self.assertEqual(r, 0)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue(moved_to.is_file())

    def test_undo_does_not_pull_files_in_from_outside(self):
        if not HAVE_SYMLINK:
            self.skipTest("keine Symlinks")
        plan = self._simple()
        mv = {m.file.rel: m for m in plan.moves}
        n, _ = org_apply_moves(plan, [mv["Notizen.txt"]], self.journal)
        self.assertEqual(n, 1)
        dest_dir = mv["Notizen.txt"].file.path.parent
        outside = self.tmp / "Fremd"
        outside.mkdir()
        (outside / "Notizen.txt").write_text("fremd")
        shutil.move(str(dest_dir), str(self.tmp / "weg"))
        os.symlink(str(outside), str(dest_dir))               # Zielordner → außen
        r, msgs = org_undo(self.journal)
        self.assertEqual(r, 0)
        self.assertEqual((outside / "Notizen.txt").read_text(), "fremd")
        self.assertFalse((self.root / "Notizen.txt").exists())

    def test_apply_refuses_nan_mtime_and_foreign_root(self):
        plan = self._simple()
        f = by_rel(plan)["Notizen.txt"]
        f.mtime = float("nan")
        other = self.tmp / "Anders"
        other.mkdir()
        g = OrgFile(path=write(other, "x.txt", "x", 1), rel="x.txt", size=1,
                    mtime=(other / "x.txt").stat().st_mtime, kind="document", ext=".txt")
        n, fails = org_apply_moves(plan, [OrgMove(f, "Sonstiges", True),
                                          OrgMove(g, "Sonstiges", True)], self.journal)
        self.assertEqual(n, 0)
        self.assertEqual(len(fails), 2)
        self.assertTrue((other / "x.txt").is_file())

    def test_sibling_prefix_not_inside(self):
        ins = org_planner._org_plan_inside
        k = org_planner._org_plan_key
        self.assertFalse(ins(k(self.root), k(str(self.root) + "2" + os.sep + "x")))
        self.assertTrue(ins(k(self.root), k(self.root / "x")))
        self.assertFalse(ins(k(self.root), k(self.root.parent)))

    @unittest.skipUnless(os.name == "nt", "nur Windows: Groß-/Kleinschreibung")
    def test_windows_case_insensitive_keys(self):
        k = org_planner._org_plan_key
        self.assertEqual(k("C:\\Docs\\A.pdf"), k("c:\\docs\\a.PDF"))
        self.assertTrue(org_planner._org_plan_inside(k("C:\\Docs"), k("c:\\DOCS\\x")))


# ---------------------------------------------------------------------------
# Tresor
# ---------------------------------------------------------------------------
class BackupSafety(_Tmp):
    def test_backup_never_writes_through_symlink_in_vault(self):
        if not HAVE_SYMLINK:
            self.skipTest("keine Symlinks")
        write(self.root, "Lebenslauf.pdf", make_pdf("Lebenslauf Berufserfahrung"), 100)
        plan = self.analyze()
        cv = by_rel(plan)["Lebenslauf.pdf"]
        self.assertEqual(cv.tier, ORG_PROTECT)
        vault = self.tmp / "Tresor"
        tdir = vault / "Dokumente" / "Bewerbung"
        tdir.mkdir(parents=True)
        victim = self.tmp / "fremd.pdf"
        victim.write_text("fremder Inhalt")
        os.symlink(str(victim), str(tdir / "Lebenslauf.pdf"))
        res = org_backup([cv], vault)
        self.assertEqual(res["copied"], 1, res)
        self.assertEqual(victim.read_text(), "fremder Inhalt")
        self.assertTrue(os.path.islink(str(tdir / "Lebenslauf.pdf")))
        self.assertEqual(sha(tdir / "Lebenslauf (2).pdf"), sha(cv.path))

    def test_backup_source_changes_during_copy(self):
        write(self.root, "Steuerbescheid_2022.pdf", make_pdf("Bescheid Einkommensteuer 2022"), 100)
        plan = self.analyze()
        f = by_rel(plan)["Steuerbescheid_2022.pdf"]
        vault = self.tmp / "Tresor"
        real = shutil.copy2

        def changing(src, dst, *a, **k):
            with open(src, "ab") as fh:                 # Quelle ändert sich mitten drin
                fh.write(b"%neu")
            return real(src, dst, *a, **k)
        org_planner.shutil.copy2 = changing
        try:
            res = org_backup([f], vault)
        finally:
            org_planner.shutil.copy2 = real
        self.assertEqual(res["copied"], 0)
        self.assertEqual(len(res["failed"]), 1)
        self.assertEqual([p for p in vault.rglob("*") if p.is_file()], [])


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    res = unittest.TextTestRunner(verbosity=1).run(suite)
    print("SUMMARY: %d Tests, %d Fehler, %d Ausnahmen, %d übersprungen (Python %s)"
          % (res.testsRun, len(res.failures), len(res.errors), len(res.skipped),
             sys.version.split()[0]))
    sys.exit(0 if res.wasSuccessful() else 1)
