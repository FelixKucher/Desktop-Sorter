# Adversarial-Tests (Korrektheit) für org_planner.py: Vorrang der Einordnung,
# Treffsicherheit DE/EN Ende-zu-Ende, Versionen ↔ Duplikate, Ereignisnamen, Zielordner,
# Statistik, reorganize_subfolders, Fortschritt/Abbruch, Python-3.9-Syntax, Namensregel.
# Aufruf: python test_org_planner_correctness.py   (Exit-Code 0 = alles bestanden)
from __future__ import annotations

import ast
import glob
import os
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
from org_core import (ORG_CATEGORIES, ORG_JUNK, ORG_KEEP, ORG_PROTECT,  # noqa: E402
                      ORG_REVIEW, org_year_of)
from org_planner import org_analyze, org_apply_moves, org_trash, org_undo  # noqa: E402
from test_org_planner import (BERLIN, MALLORCA, NOW, PROGRAMS, ROME, build_tree,  # noqa: E402
                              make_docx, make_jpeg, make_pdf, make_png, snapshot, write)

DAY = 86400.0


def _exif_dt(y, mo, d, h=12, mi=0):
    return "%04d:%02d:%02d %02d:%02d:00" % (y, mo, d, h, mi)


def _tmp():
    return Path(tempfile.mkdtemp(prefix="orgcorr_"))


def _by_rel(plan):
    return {f.rel: f for f in plan.files}


def _moves(plan):
    return {m.file.rel: m for m in plan.moves}


def _check_invariants(tc, plan):
    """Allgemeine Konsistenz eines Plans (gilt für jede Analyse)."""
    junk_ids = {id(f) for f in plan.junk}
    for f in plan.files:
        if f.tier == ORG_JUNK and not f.in_project:
            tc.assertIn(id(f), junk_ids, f.rel)
        if f.in_project:
            tc.assertNotIn(id(f), junk_ids, f.rel)
            tc.assertFalse(f.dup_of, f.rel)
            tc.assertFalse(f.version_group, f.rel)
    for f in plan.protect:
        tc.assertNotIn(id(f), junk_ids, f.rel)
        tc.assertEqual(f.tier, ORG_PROTECT, f.rel)
    for f in plan.junk:
        tc.assertIn(f.category, ("temp", "duplicate", "installer"), f.rel)
        if f.dup_of:
            keeper = _by_rel(plan).get(f.dup_of)
            tc.assertIsNotNone(keeper, f.dup_of)
            tc.assertNotEqual(keeper.tier, ORG_JUNK, f.dup_of)
            tc.assertFalse(keeper.dup_of, f.dup_of)
    seen = set()
    for m in plan.moves:
        f = m.file
        tc.assertNotIn(id(f), seen, "Datei doppelt im Plan: %s" % f.rel)
        seen.add(id(f))
        tc.assertFalse(f.in_project, f.rel)
        tc.assertNotEqual(f.tier, ORG_JUNK, f.rel)
        tc.assertFalse(f.dup_of, f.rel)
        tc.assertEqual(f.target, m.dest_rel, f.rel)
        tc.assertTrue(m.dest_rel, f.rel)
        tc.assertFalse(m.dest_rel.startswith("/") or ".." in m.dest_rel.split("/"), m.dest_rel)
        parent = f.rel.rpartition("/")[0].casefold()
        d = m.dest_rel.casefold()
        tc.assertFalse(parent == d or parent.startswith(d + "/"),
                       "liegt schon richtig, trotzdem verschoben: %s" % f.rel)
        for p in plan.projects:
            if p and p != ".":
                tc.assertFalse(d == p.casefold() or d.startswith(p.casefold() + "/"),
                               "Ziel in Projektordner: %s" % m.dest_rel)
    # Statistik
    st = plan.stats

    def sz(f):
        return max(0, int(f.size))
    tc.assertEqual(st["files"], len(plan.files))
    tc.assertEqual(st["bytes"], sum(sz(f) for f in plan.files))
    tc.assertEqual(st["junk_bytes"], sum(sz(f) for f in plan.junk))
    tc.assertEqual(st["dup_bytes"], sum(sz(f) for f in plan.junk if f.dup_of))
    tc.assertEqual(st["protect_count"], len(plan.protect))
    tc.assertEqual(st["review_count"], len(plan.review))
    tc.assertEqual(st["move_count"], len(plan.moves))
    tc.assertEqual(st["version_count"],
                   sum(1 for g in plan.versions.values() for f in g if not f.version_latest))
    tc.assertLessEqual(st["dup_bytes"], st["junk_bytes"])
    tc.assertLessEqual(st["junk_bytes"], st["bytes"])
    tc.assertGreaterEqual(st["seconds"], 0)


# ---------------------------------------------------------------------------
class DuplicateKeeper(unittest.TestCase):
    """Welches Exemplar bleibt? Der aussagekräftige Name/die wichtigere Kategorie."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = _tmp()
        root = cls.root = cls.tmp / "Ablage"
        root.mkdir()
        inv = make_pdf("Rechnung Nr 5\nBetrag 10 EUR")
        write(root, "download.pdf", inv, 500)                    # älter, nichtssagend
        write(root, "Rechnung_Amazon_2021.pdf", inv, 100)
        img = make_jpeg(_exif_dt(2022, 5, 5, 10), BERLIN, "Apple", "iPhone 13", salt="perso")
        write(root, "IMG_4567.JPG", img, 500)                    # älter: Handyfoto
        write(root, "Personalausweis.jpg", img, 100)             # gleicher Inhalt, Ausweis
        cv = make_pdf("Lebenslauf\nBerufserfahrung")
        write(root, "Downloads/Lebenslauf.pdf", cv, 600)
        write(root, "Bewerbung/Lebenslauf.pdf", cv, 100)
        # Versionen + Duplikate: v2 und „v2 (1)“ identisch, v1 anders
        write(root, "Lebenslauf_v1.docx", make_docx("Lebenslauf eins"), 300)
        v2 = make_docx("Lebenslauf zwei, aktuell")
        write(root, "Lebenslauf_v2.docx", v2, 200)
        write(root, "Lebenslauf_v2 (1).docx", v2, 100)
        cls.plan = org_analyze(root, programs=[], now=NOW)
        cls.f = _by_rel(cls.plan)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def test_invariants(self):
        _check_invariants(self, self.plan)

    def test_id_scan_beats_generic_photo(self):
        f = self.f
        self.assertEqual(f["Personalausweis.jpg"].category, "id")
        self.assertEqual(f["Personalausweis.jpg"].tier, ORG_PROTECT)
        self.assertFalse(f["Personalausweis.jpg"].dup_of)
        self.assertEqual(f["IMG_4567.JPG"].dup_of, "Personalausweis.jpg")
        self.assertEqual(_moves(self.plan)["Personalausweis.jpg"].dest_rel,
                         "Dokumente/Ausweise")

    def test_named_invoice_beats_generic_document(self):
        f = self.f
        self.assertEqual(f["Rechnung_Amazon_2021.pdf"].category, "invoice")
        self.assertFalse(f["Rechnung_Amazon_2021.pdf"].dup_of)
        self.assertEqual(f["download.pdf"].dup_of, "Rechnung_Amazon_2021.pdf")
        self.assertEqual(f["download.pdf"].meta.get("orig_category"), "document")

    def test_protected_copy_outside_downloads_kept(self):
        f = self.f
        self.assertEqual(f["Downloads/Lebenslauf.pdf"].dup_of, "Bewerbung/Lebenslauf.pdf")
        self.assertFalse(f["Bewerbung/Lebenslauf.pdf"].dup_of)

    def test_versions_and_duplicates(self):
        f = self.f
        self.assertEqual(f["Lebenslauf_v2 (1).docx"].dup_of, "Lebenslauf_v2.docx")
        self.assertFalse(f["Lebenslauf_v2 (1).docx"].version_group)
        groups = list(self.plan.versions.values())
        self.assertEqual(len(groups), 1, self.plan.versions)
        self.assertEqual(sorted(m.rel for m in groups[0]),
                         ["Lebenslauf_v1.docx", "Lebenslauf_v2.docx"])
        self.assertTrue(f["Lebenslauf_v2.docx"].version_latest)
        self.assertFalse(f["Lebenslauf_v1.docx"].version_latest)
        self.assertNotEqual(f["Lebenslauf_v1.docx"].tier, ORG_JUNK)
        mv = _moves(self.plan)
        self.assertEqual(mv["Lebenslauf_v1.docx"].dest_rel, mv["Lebenslauf_v2.docx"].dest_rel)
        self.assertNotIn("Lebenslauf_v2 (1).docx", mv)

    def test_trash_copy_then_keeper_keeps_one(self):
        # Reihenfolge Kopie → Original: am Ende muss genau ein Exemplar übrig bleiben
        tmp = _tmp()
        try:
            root = tmp / "T"
            root.mkdir()
            data = make_pdf("Rechnung 7\nBetrag")
            write(root, "download (2).pdf", data, 50)
            write(root, "Rechnung_7.pdf", data, 400)
            plan = org_analyze(root, programs=[], now=NOW)
            f = _by_rel(plan)
            copy = f["download (2).pdf"]
            self.assertEqual(copy.dup_of, "Rechnung_7.pdf")
            for order in ([copy, f["Rechnung_7.pdf"]],):
                n, fails = org_trash(order, lambda p: (os.unlink(p), True)[1])
                left = os.listdir(str(root))
                self.assertEqual(len(left), 1, (left, fails))
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)


# ---------------------------------------------------------------------------
class RealWorldClassification(unittest.TestCase):
    """DE/EN-Dateinamen Ende-zu-Ende: Kategorie → Stufe → Zielordner → Vorauswahl."""

    DOCS = {
        # Name: (Kategorie, Textinhalt)
        "Abschlusszeugnis_Realschule.pdf": ("certificate", "Abschlusszeugnis"),
        "Kuendigung_Fitnessstudio.pdf": ("contract", "Kündigung"),
        "Krankmeldung_AU.pdf": ("health", "Arbeitsunfähigkeitsbescheinigung"),
        "Arztbrief_Kardiologie.pdf": ("health", "Arztbrief"),
        "Lohnsteuerbescheinigung_2022.pdf": ("tax", "Ausdruck der elektronischen "
                                                    "Lohnsteuerbescheinigung für 2022"),
        "Employment_Contract.pdf": ("contract", "Employment Agreement"),
        "Tax_Return_2022.pdf": ("tax", "Tax return 2022"),
        "Insurance_Policy_Haftpflicht.pdf": ("insurance", "Policy"),
        "Bank_Statement_2023_05.pdf": ("bank", "Statement"),
        "Boarding_Pass_LH123.pdf": ("travel", "Boarding pass"),
        "Bedienungsanleitung_Waschmaschine.pdf": ("manual", "Bedienungsanleitung"),
        "Heiratsurkunde.pdf": ("certificate", "Eheurkunde Standesamt"),
        "Grundbuchauszug.pdf": ("property", "Grundbuch"),
        "Resume_John_Doe.docx": ("cv", "Work experience"),
        "Cover_Letter_Google.docx": ("cv", "Dear hiring manager"),
        "Einkommensteuerbescheid 2021.pdf": ("tax", "Finanzamt"),
        "Hausratversicherung.pdf": ("insurance", "Versicherungsschein"),
        "Transcript_of_Records.pdf": ("certificate", "Transcript of Records"),
    }
    IMAGES = {
        # fotografierte Dokumente (Handy-EXIF!) → Dokumentkategorie
        "Fahrzeugschein.jpg": "vehicle",
        "Kfz-Schein.jpg": "vehicle",
        "Mutterpass.jpg": "health",
        "Impfpass.jpg": "health",
        "Kassenbon Mediamarkt.jpg": "invoice",
        "Garantieschein Waschmaschine.jpg": "invoice",
        "Grundbuchauszug.jpg": "property",
        "Flugticket.png": "travel",
        "Krankenkassenkarte.jpg": "health",
        "Kinderreisepass.jpg": "id",
        "Fuehrerschein Rueckseite.jpg": "id",
        # Familienfotos mit „Dokument“-Wörtern im Namen → bleiben Fotos
        "Geburtstag Oma.jpg": "photo_personal",
        "Hochzeit Standesamt.jpg": "photo_personal",
        "Taufe Urkunde.jpg": "photo_personal",
        "Passfoto.jpg": "photo_personal",
        "Postkarte Oma.jpg": "photo_personal",
        "Urlaub Kreta Karte.jpg": "photo_personal",
    }
    ARCHIVES = {
        "Bewerbungsunterlagen.zip": "cv",
        "Zeugnisse.zip": "certificate",
        "Steuererklaerung_2023.zip": "tax",
        "Fotos Sommer.zip": "archive",
        "Sicherung Handy.zip": "archive",
    }

    @classmethod
    def setUpClass(cls):
        cls.tmp = _tmp()
        root = cls.root = cls.tmp / "Dokumente"
        root.mkdir()
        for name, (_cat, text) in cls.DOCS.items():
            if name.endswith(".docx"):
                write(root, name, make_docx(text), 100)
            else:
                write(root, name, make_pdf(text), 100)
        for i, name in enumerate(cls.IMAGES):
            if name.endswith(".png"):
                write(root, name, make_png(1240, 1754, name), 100)
            else:
                write(root, name, make_jpeg(_exif_dt(2023, 6, 1 + i, 12), BERLIN, "Apple",
                                            "iPhone 13", salt=name), 100)
        for name in cls.ARCHIVES:
            write(root, name, b"PK\x05\x06" + b"\x00" * 18 + name.encode(), 100)
        cls.plan = org_analyze(root, programs=[], now=NOW)
        cls.f = _by_rel(cls.plan)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def test_invariants(self):
        _check_invariants(self, self.plan)

    def _assert_wired(self, f, cat):
        self.assertEqual(f.category, cat, f.rel)
        self.assertEqual(f.tier, ORG_CATEGORIES[cat]["tier"], f.rel)
        mv = _moves(self.plan).get(f.rel)
        folder = ORG_CATEGORIES[cat]["folder"]
        if not folder:
            self.assertIsNone(mv, f.rel)
            return
        self.assertIsNotNone(mv, f.rel)
        exp = folder.replace("{year}", str(org_year_of(f))).split("/")
        if exp[0] == "Dokumente":
            exp = exp[1:]                              # Wurzel heißt „Dokumente“
        if "{event}" in folder:
            self.assertTrue(mv.dest_rel.startswith("Bilder/Fotos/"), mv.dest_rel)
        else:
            self.assertEqual(mv.dest_rel, "/".join(exp), f.rel)
        if f.tier in (ORG_PROTECT, ORG_KEEP) and f.score >= org_planner.ORG_PLAN_CHECK_SCORE:
            self.assertTrue(mv.checked, f.rel)

    def test_documents(self):
        for name, (cat, _t) in self.DOCS.items():
            with self.subTest(name=name):
                self._assert_wired(self.f[name], cat)
        self.assertEqual(self.f["Lohnsteuerbescheinigung_2022.pdf"].year, 2022)
        self.assertEqual(_moves(self.plan)["Einkommensteuerbescheid 2021.pdf"].dest_rel,
                         "Steuer/2021")

    def test_photographed_documents_and_family_photos(self):
        for name, cat in self.IMAGES.items():
            with self.subTest(name=name):
                self._assert_wired(self.f[name], cat)

    def test_archives_by_name(self):
        for name, cat in self.ARCHIVES.items():
            with self.subTest(name=name):
                self._assert_wired(self.f[name], cat)
        self.assertEqual(_moves(self.plan)["Steuererklaerung_2023.zip"].dest_rel, "Steuer/2023")


# ---------------------------------------------------------------------------
class PrecedenceRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = _tmp()
        root = cls.root = cls.tmp / "Desktop"
        root.mkdir()
        write(root, "Lebenslauf.pdf.crdownload", make_pdf("Lebenslauf"), 3)   # alt → Rest
        write(root, "Zeugnis.pdf.crdownload", make_pdf("Zeugnis"), 0.1)        # läuft noch
        write(root, "Zeugnis_leer.pdf", b"", 10)                               # 0 Byte
        write(root, "~$Lebenslauf.docx", b"\x00" * 162, 10)
        write(root, "Rechnung.pdf", make_pdf("Rechnung A\nBetrag 1"), 100)
        write(root, "Rechnung (1).pdf", make_pdf("Rechnung B\nBetrag 2"), 90)
        write(root, "Screenshot Zeugnis.png", make_png(1920, 1080, "sz"), 10)
        write(root, "Screenshot 2024-01-01.png", make_png(1920, 1080, "s"), 10)
        write(root, "Lebenslauf.docx.bak", make_docx("Lebenslauf alt"), 10)
        write(root, "~$Lebenslauf_gross.docx", b"\x00" * 20000, 10)     # zu groß für Sperrdatei
        cls.plan = org_analyze(root, programs=[], now=NOW,
                               protected_paths=[str(root / "Wichtig_leer.txt")])
        cls.f = _by_rel(cls.plan)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def test_invariants(self):
        _check_invariants(self, self.plan)

    def test_junk_before_classification(self):
        f = self.f
        self.assertEqual(f["Lebenslauf.pdf.crdownload"].tier, ORG_JUNK)
        self.assertEqual(f["Zeugnis_leer.pdf"].tier, ORG_JUNK)
        self.assertEqual(f["~$Lebenslauf.docx"].tier, ORG_JUNK)
        # laufender Download: weder Müll noch geschützt noch verschoben
        z = f["Zeugnis.pdf.crdownload"]
        self.assertNotIn(z.tier, (ORG_JUNK, ORG_PROTECT))
        self.assertNotIn(z.rel, _moves(self.plan))

    def test_recurring_counter_is_not_version(self):
        f = self.f
        self.assertFalse(f["Rechnung (1).pdf"].version_group)
        self.assertTrue(f["Rechnung (1).pdf"].version_latest)

    def test_screenshot_of_certificate_stays_certificate(self):
        self.assertEqual(self.f["Screenshot Zeugnis.png"].category, "certificate")
        self.assertEqual(self.f["Screenshot 2024-01-01.png"].category, "screenshot")

    def test_oversized_lock_name_not_protected(self):
        x = self.f["~$Lebenslauf_gross.docx"]
        self.assertEqual(x.tier, ORG_REVIEW, x.reasons)
        mv = _moves(self.plan).get(x.rel)
        self.assertTrue(mv is None or not mv.checked)

    def test_backup_file_review_not_junk(self):
        b = self.f["Lebenslauf.docx.bak"]
        self.assertEqual(b.tier, ORG_REVIEW)

    def test_user_protected_empty_file_not_junk(self):
        tmp = _tmp()
        try:
            root = tmp / "R"
            root.mkdir()
            write(root, "Wichtig_leer.txt", b"", 10)
            write(root, "a.txt", "Inhalt eins", 10)
            write(root, "Kopie a.txt", "Inhalt eins", 5)
            plan = org_analyze(root, programs=[], now=NOW,
                               protected_paths=[str(root / "Wichtig_leer.txt"),
                                                str(root / "Kopie a.txt")])
            f = _by_rel(plan)
            self.assertEqual(f["Wichtig_leer.txt"].tier, ORG_PROTECT)
            self.assertNotIn(f["Wichtig_leer.txt"], plan.junk)
            self.assertEqual(f["Kopie a.txt"].tier, ORG_PROTECT)
            self.assertFalse(f["Kopie a.txt"].dup_of)
            _check_invariants(self, plan)
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)


# ---------------------------------------------------------------------------
class EventsAndTargets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = _tmp()
        root = cls.root = cls.tmp / "Pictures"
        root.mkdir()
        # Silvester-Reise über den Jahreswechsel (Rom), 8 Fotos
        t0 = time.mktime((2023, 12, 30, 10, 0, 0, 0, 0, -1))
        for i in range(8):
            t = t0 + i * 8 * 3600
            dt = time.strftime("%Y:%m:%d %H:%M:%S", time.localtime(t))
            write(root, "IMG_%04d.JPG" % (5001 + i), make_jpeg(dt, ROME, salt="sil%d" % i), 30)
        # Zuhause: über Monate verteilt (Berlin)
        for i, (y, mo, d) in enumerate([(2023, 3, 1), (2023, 5, 2), (2023, 8, 9), (2024, 2, 3),
                                         (2024, 4, 4), (2024, 6, 5)]):
            write(root, "IMG_%04d.JPG" % (6001 + i),
                  make_jpeg(_exif_dt(y, mo, d, 15), BERLIN, salt="h%d" % i), 30)
        # Handyfoto ohne EXIF, Datum nur im Namen, Datei viel später kopiert
        write(root, "IMG_20190704_101500.jpg", make_jpeg(None, None, make="", salt="n"), 5)
        # Handyvideo mit Datum im Namen
        write(root, "VID_20230705_120000.mp4", b"\x00\x00\x00\x18ftypmp42" + b"v" * 50, 5)
        # Unterordner (bleibt ohne reorganize_subfolders)
        write(root, "Urlaub Mallorca/IMG_7001.JPG",
              make_jpeg(_exif_dt(2024, 8, 3, 10), MALLORCA, salt="m"), 30)
        write(root, "Scan Rechnung.jpg", make_jpeg(_exif_dt(2022, 2, 2), BERLIN, "Apple",
                                                   "iPhone 13", salt="r"), 30)
        cls.plan = org_analyze(root, programs=[], now=NOW)
        cls.f = _by_rel(cls.plan)
        cls.mv = _moves(cls.plan)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def test_invariants(self):
        _check_invariants(self, self.plan)

    def test_new_year_event(self):
        exp = "2023-12-30 bis 2024-01-01 – Reise"
        for i in range(8):
            rel = "IMG_%04d.JPG" % (5001 + i)
            self.assertEqual(self.f[rel].event, exp, rel)
            self.assertEqual(self.mv[rel].dest_rel, "Fotos/2023/" + exp, rel)  # Alias Bilder

    def test_month_buckets_and_name_date(self):
        self.assertEqual(self.mv["IMG_6004.JPG"].dest_rel, "Fotos/2024/2024-02 Februar")
        n = self.f["IMG_20190704_101500.jpg"]
        self.assertEqual(n.category, "photo_personal")
        self.assertEqual(self.mv[n.rel].dest_rel, "Fotos/2019/2019-07 Juli")

    def test_video_year_from_name(self):
        v = self.f["VID_20230705_120000.mp4"]
        self.assertEqual(v.category, "video_personal")
        self.assertEqual(self.mv[v.rel].dest_rel, "Videos/2023")

    def test_alias_only_strips_matching_root(self):
        # Wurzel „Pictures“: Fotos ohne „Bilder/“, Dokumente aber mit „Dokumente/“
        self.assertEqual(self.mv["Scan Rechnung.jpg"].dest_rel, "Dokumente/Rechnungen/2022")

    def test_subfolder_not_moved_by_default(self):
        self.assertNotIn("Urlaub Mallorca/IMG_7001.JPG", self.mv)
        for m in self.plan.moves:
            self.assertEqual(m.file.depth, 0, m.file.rel)


# ---------------------------------------------------------------------------
class ReorganizeSubfolders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = _tmp()
        root = cls.root = cls.tmp / "Dokumente"
        root.mkdir()
        write(root, "bewerbung/Lebenslauf.pdf", make_pdf("Lebenslauf"), 100)       # klein!
        write(root, "Bewerbung/2019/Anschreiben.docx", make_docx("Anschreiben"), 100)
        write(root, "Alt/Lebenslauf_v1.docx", make_docx("Lebenslauf 1"), 300)
        write(root, "Alt/Lebenslauf_v2.docx", make_docx("Lebenslauf 2"), 100)
        write(root, "Alt/Lebenslauf_v3.docx", make_docx("Lebenslauf 3"), 50)
        write(root, "Kram/Rechnung_2020.pdf", make_pdf("Rechnung 2020"), 100)
        write(root, "Rechnungen/2020/Rechnung_Strom_2020.pdf", make_pdf("Rechnung"), 100)
        write(root, "Projekt/.git/HEAD", "x", 10)
        write(root, "Projekt/Vertrag.pdf", make_pdf("Vertrag"), 10)
        write(root, "Tool/tool.exe", b"MZ" + b"t" * 50, 10)
        write(root, "Tool/lib/core.dll", b"MZ" + b"d" * 50, 10)
        write(root, "Tool/Lizenzvertrag.pdf", make_pdf("Lizenzvertrag"), 10)
        cls.plan = org_analyze(root, programs=[], now=NOW, reorganize_subfolders=True)
        cls.f = _by_rel(cls.plan)
        cls.mv = _moves(cls.plan)
        cls.plan0 = org_analyze(root, programs=[], now=NOW)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def test_invariants(self):
        _check_invariants(self, self.plan)
        _check_invariants(self, self.plan0)

    def test_already_in_place_case_insensitive(self):
        self.assertNotIn("bewerbung/Lebenslauf.pdf", self.mv)
        self.assertNotIn("Bewerbung/2019/Anschreiben.docx", self.mv)
        self.assertNotIn("Rechnungen/2020/Rechnung_Strom_2020.pdf", self.mv)

    def test_versions_move_together(self):
        g = [self.mv.get("Alt/Lebenslauf_v%d.docx" % i) for i in (1, 2, 3)]
        self.assertTrue(all(g), self.mv.keys())
        self.assertEqual({m.dest_rel for m in g}, {"Bewerbung"})
        self.assertEqual(len({m.checked for m in g}), 1)

    def test_subfolder_moves(self):
        self.assertEqual(self.mv["Kram/Rechnung_2020.pdf"].dest_rel, "Rechnungen/2020")

    def test_project_and_program_untouched(self):
        self.assertNotIn("Projekt/Vertrag.pdf", self.mv)
        self.assertNotIn("Tool/Lizenzvertrag.pdf", self.mv)
        self.assertNotIn("Tool/tool.exe", self.mv)

    def test_default_mode_moves_nothing_in_subfolders(self):
        self.assertEqual([m.file.rel for m in self.plan0.moves], [])
        # Versionen werden trotzdem erkannt
        self.assertTrue(self.plan0.versions)

    def test_apply_undo_roundtrip(self):
        tmp = _tmp()
        try:
            root = tmp / "Dokumente"
            shutil.copytree(str(self.root), str(root))
            before = snapshot(root)
            plan = org_analyze(root, programs=[], now=NOW, reorganize_subfolders=True)
            n, fails = org_apply_moves(plan, plan.moves, tmp / "j.json")
            self.assertEqual(n, len(plan.moves), fails)
            self.assertEqual(fails, [])
            r, msgs = org_undo(tmp / "j.json")
            self.assertEqual(r, n, msgs)
            self.assertEqual(snapshot(root), before)
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)



class CuratedFoldersAndIdempotency(unittest.TestCase):
    def test_user_subfolders_inside_category_are_respected(self):
        tmp = _tmp()
        try:
            root = tmp / "Bilder"
            for i in range(8):                       # ergibt ein Ereignis „… – Reise“
                write(root, "Fotos/2023/Italien mit Oma/IMG_%04d.JPG" % (100 + i),
                      make_jpeg(_exif_dt(2023, 7, 10, 9 + i), ROME, salt="it%d" % i), 30)
            for i in range(6):
                write(root, "Fotos/2023/Zuhause/IMG_%04d.JPG" % (200 + i),
                      make_jpeg(_exif_dt(2023, 1 + i, 3, 15), BERLIN, salt="z%d" % i), 30)
            write(root, "Fotos/IMG_0300.JPG",                  # direkt in „Fotos“
                  make_jpeg(_exif_dt(2023, 9, 9, 9), BERLIN, salt="f"), 30)
            write(root, "Dokumente/Steuer/Alt/Steuerbescheid_2019.pdf",
                  make_pdf("Steuerbescheid 2019"), 30)
            plan = org_analyze(root, programs=[], now=NOW, reorganize_subfolders=True)
            _check_invariants(self, plan)
            mv = _moves(plan)
            for rel in mv:
                self.assertFalse(rel.startswith("Fotos/2023/"), rel)
            self.assertNotIn("Dokumente/Steuer/Alt/Steuerbescheid_2019.pdf", mv)
            self.assertEqual(mv["Fotos/IMG_0300.JPG"].dest_rel, "Fotos/2023/2023-09 September")
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)

    def test_second_run_proposes_nothing(self):
        for reorg in (False, True):
            tmp = _tmp()
            try:
                root = build_tree(tmp)
                p1 = org_analyze(root, programs=PROGRAMS, now=NOW, reorganize_subfolders=reorg)
                n, fails = org_apply_moves(p1, p1.moves, tmp / "j.json")
                self.assertEqual((n, fails), (len(p1.moves), []))
                p2 = org_analyze(root, programs=PROGRAMS, now=NOW, reorganize_subfolders=reorg)
                self.assertEqual([(m.file.rel, m.dest_rel) for m in p2.moves], [], reorg)
                _check_invariants(self, p2)
                # gleiche Einordnung vorher/nachher (nur der Ort hat sich geändert)
                cats1 = sorted((f.name, f.category, f.tier) for f in p1.files)
                cats2 = sorted((f.name, f.category, f.tier) for f in p2.files)
                self.assertEqual(cats1, cats2)
            finally:
                shutil.rmtree(str(tmp), ignore_errors=True)



class UndoThroughLink(unittest.TestCase):
    @unittest.skipIf(not hasattr(os, "symlink") or os.name == "nt", "Symlinks nötig")
    def test_undo_when_plan_root_is_a_link(self):
        tmp = _tmp()
        try:
            real = tmp / "echt"
            write(real, "Lebenslauf.pdf", make_pdf("Lebenslauf\nBerufserfahrung"), 10)
            write(real, "Rechnung_2021.pdf", make_pdf("Rechnung 2021"), 10)
            link = tmp / "verknuepft"
            os.symlink(str(real), str(link))
            before = snapshot(real)
            plan = org_analyze(real, programs=[], now=NOW)
            # Oberfläche baut den Plan mit dem (unaufgelösten) Pfad, den der Nutzer sieht
            plan.root = link
            for f in plan.files:
                f.path = link.joinpath(*f.rel.split("/"))
            n, fails = org_apply_moves(plan, plan.moves, tmp / "j.json")
            self.assertEqual((n, fails), (2, []))
            r, msgs = org_undo(tmp / "j.json")
            self.assertEqual(r, 2, msgs)
            self.assertEqual(snapshot(real), before)
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)


# ---------------------------------------------------------------------------
class ProgressAndCancel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = _tmp()
        cls.root = build_tree(cls.tmp)
        cls.calls = []
        cls.plan = org_analyze(cls.root, programs=PROGRAMS, now=NOW,
                               progress=lambda s, d, t: cls.calls.append((s, d, t)))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def test_invariants_on_messy_tree(self):
        _check_invariants(self, self.plan)

    def test_progress_monotonic_and_ordered(self):
        stages = []
        for s, d, t in self.calls:
            self.assertIsInstance(d, int)
            self.assertIsInstance(t, int)
            self.assertGreaterEqual(d, 0)
            if t > 0:
                self.assertLessEqual(d, t, (s, d, t))
            if not stages or stages[-1] != s:
                self.assertNotIn(s, stages, "Stufe kehrt zurück: %s" % s)
                stages.append(s)
        self.assertEqual(stages, list(org_planner.ORG_PLAN_STAGES))
        last = {}
        for s, d, t in self.calls:
            if s in last and t == last[s][1]:
                self.assertGreaterEqual(d, last[s][0], s)
            last[s] = (d, t)

    def test_cancel_at_every_point(self):
        n_checks = [0]

        def count():
            n_checks[0] += 1
            return False
        org_analyze(self.root, programs=PROGRAMS, now=NOW, cancelled=count)
        total = n_checks[0]
        self.assertGreater(total, 10)
        before = snapshot(self.root)
        step = max(1, total // 40)
        for k in list(range(1, total, step)) + [total - 1]:
            calls = [0]

            def canc(k=k, calls=calls):
                calls[0] += 1
                return calls[0] >= k
            plan = org_analyze(self.root, programs=PROGRAMS, now=NOW, cancelled=canc)
            if plan.stats.get("cancelled"):
                self.assertEqual(plan.moves, [], k)
                self.assertEqual(plan.junk, [], k)
                self.assertEqual(plan.dup_groups, [], k)
                self.assertEqual(plan.empty_dirs, [], k)
                self.assertTrue(all(not f.target for f in plan.files), k)
            self.assertIn("seconds", plan.stats)
        self.assertEqual(snapshot(self.root), before)



class ProgressTiming(unittest.TestCase):
    """Langsame Schritte melden trotzdem regelmäßig Fortschritt."""

    def setUp(self):
        self.tmp = _tmp()
        self.root = self.tmp / "Langsam"
        for i in range(12):
            write(self.root, "Ordner%02d/Brief_%02d.pdf" % (i, i), make_pdf("Brief %d" % i), 10)
            write(self.root, "Brief_top_%02d.pdf" % i, make_pdf("Brief top %d" % i), 10)
        self._saved = (org_planner.org_extract_text, org_planner.ORG_PLAN_PROGRESS_SECONDS)

    def tearDown(self):
        org_planner.org_extract_text, org_planner.ORG_PLAN_PROGRESS_SECONDS = self._saved
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_slow_extraction_reports_progress(self):
        real = org_planner.org_extract_text

        def slow(path, max_chars=4000):
            time.sleep(0.03)
            return real(path, max_chars)
        org_planner.org_extract_text = slow
        org_planner.ORG_PLAN_PROGRESS_SECONDS = 0.05
        calls = []
        plan = org_analyze(self.root, programs=[], now=NOW,
                           progress=lambda s, d, t: calls.append((s, d, t)))
        mid = [c for c in calls if c[0] == "Inhalte lesen" and 0 < c[1] < c[2]]
        self.assertGreaterEqual(len(mid), 3, calls)
        _check_invariants(self, plan)

    def test_walk_reports_activity(self):
        org_planner.ORG_PLAN_PROGRESS_SECONDS = 0.0
        calls = []
        org_analyze(self.root, programs=[], now=NOW, extract_text=False,
                    progress=lambda s, d, t: calls.append((s, d, t)))
        walk = [c for c in calls if c[0] == "Dateien erfassen"]
        self.assertGreaterEqual(len(walk), 5, walk)
        self.assertEqual(walk[-1][1], walk[-1][2])          # Abschluss: n von n
        self.assertEqual(walk[-1][2], 24)



class Performance60k(unittest.TestCase):
    def test_60000_limit_truncates_and_stays_consistent(self):
        tmp = _tmp()
        try:
            root = tmp / "Downloads"
            names = ["Rechnung_%d_2023.pdf", "IMG_%04d.JPG", "Fahrzeugschein %d.jpg",
                     "Lebenslauf_v%d.docx", "Screenshot %d.png", "Zeugnisse %d.zip",
                     "WhatsApp Image %d.jpeg", "setup%d.exe", "Kassenbon %d.jpg", "x%d.txt"]
            n = 0
            for d in range(401):
                sub = root / ("Ordner %d" % d)
                sub.mkdir(parents=True)
                for i in range(150):
                    nm = names[(d + i) % len(names)] % (d * 150 + i)
                    (sub / nm).write_bytes(b"x" * (n % 97 + 1) + str(n % 5000).encode())
                    n += 1
            self.assertGreater(n, 60000)
            t0 = time.monotonic()
            plan = org_analyze(root, programs=PROGRAMS, extract_text=False, now=NOW,
                               reorganize_subfolders=True)
            dt = time.monotonic() - t0
            print("\n  %d Dateien (Grenze 60.000) analysiert in %.1f s, %d Verschiebungen, "
                  "%d Duplikate" % (n, dt, len(plan.moves), plan.stats.get("dup_count", 0)))
            self.assertTrue(plan.truncated)
            self.assertEqual(len(plan.files), 60000)
            self.assertNotIn("error", plan.stats)
            self.assertLess(dt, 60.0)
            _check_invariants(self, plan)
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)


# ---------------------------------------------------------------------------
class CodeRules(unittest.TestCase):
    MODULES = sorted(glob.glob(str(HERE / "org_*.py")))

    def test_all_modules_found(self):
        names = {Path(m).name for m in self.MODULES}
        for n in ("org_core.py", "org_lexicon.py", "org_extract.py", "org_dupes.py",
                  "org_junk.py", "org_photos.py", "org_shell.py", "org_planner.py"):
            self.assertIn(n, names)

    def test_py39_syntax(self):
        for m in self.MODULES:
            src = Path(m).read_text(encoding="utf-8")
            try:
                ast.parse(src, filename=m, feature_version=(3, 9))
            except SyntaxError as e:
                self.fail("%s: nicht Python-3.9-kompatibel: %s" % (m, e))
            self.assertIn("from __future__ import annotations", src, m)

    def test_no_new_runtime_apis(self):
        bad = ("removeprefix(", "removesuffix(", "strict=True", "hardlink_to(",
               "kw_only=", "slots=True", "bit_count(", "except*", "tomllib", "zoneinfo")
        for m in self.MODULES:
            src = Path(m).read_text(encoding="utf-8")
            for b in bad:
                self.assertNotIn(b, src, "%s: %s" % (Path(m).name, b))
            tree = ast.parse(src)
            for node in ast.walk(tree):
                # isinstance(x, A | B) ist erst ab 3.10 erlaubt
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") in (
                        "isinstance", "issubclass") and len(node.args) == 2:
                    self.assertNotIsInstance(node.args[1], ast.BinOp,
                                             "%s:%d" % (Path(m).name, node.lineno))

    @staticmethod
    def _top_names(node):
        out = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            tg = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in tg:
                out += [n.id for n in ast.walk(t) if isinstance(n, ast.Name)]
        elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
            for ch in ast.iter_child_nodes(node):
                out += CodeRules._top_names(ch)
            for h in getattr(node, "handlers", []) or []:
                for ch in h.body:
                    out += CodeRules._top_names(ch)
        return out

    def test_naming_prefix_all_modules(self):
        ok = ("org_", "Org", "ORG_", "_org_", "_ORG_")
        seen = {}
        for m in self.MODULES:
            tree = ast.parse(Path(m).read_text(encoding="utf-8"))
            for node in tree.body:
                for n in self._top_names(node):
                    self.assertTrue(n.startswith(ok), "%s:%d %s" % (Path(m).name,
                                                                  node.lineno, n))
                    other = seen.get(n)
                    self.assertTrue(other is None or other == m,
                                    "%s doppelt in %s und %s" % (n, other, m))
                    seen[n] = m
                # „from x import y“ aus der Standardbibliothek: nur bekannte Namen, die das
                # Hauptprogramm ohnehin so verwendet, sonst Alias mit Präfix
                if isinstance(node, ast.ImportFrom) and node.module and \
                        not node.module.startswith("org_") and node.module != "__future__":
                    for a in node.names:
                        nm = a.asname or a.name
                        if not nm.startswith(ok):
                            self.assertIn(nm, ("dataclass", "field", "Path"),
                                          "%s: from %s import %s ohne org-Präfix"
                                          % (Path(m).name, node.module, nm))

    def test_no_module_attribute_access_between_org_modules(self):
        for m in self.MODULES:
            tree = ast.parse(Path(m).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        self.assertFalse(a.name.startswith("org_"),
                                         "%s: import %s" % (Path(m).name, a.name))
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    self.assertFalse(node.value.id.startswith("org_") and
                                     node.value.id in {Path(x).stem for x in self.MODULES},
                                     "%s:%d %s.%s" % (Path(m).name, node.lineno,
                                                      node.value.id, node.attr))


if __name__ == "__main__":
    t0 = time.time()
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    res = unittest.TextTestRunner(verbosity=1).run(suite)
    print("SUMMARY: %d Tests, %d Fehler, %d Ausnahmen, %d übersprungen (Python %s, %.1f s)"
          % (res.testsRun, len(res.failures), len(res.errors), len(res.skipped),
             sys.version.split()[0], time.time() - t0))
    sys.exit(0 if res.wasSuccessful() else 1)
