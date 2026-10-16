# Adversariale Tests für org_lexicon – wichtige Dokumente dürfen nie "verloren gehen"
# (None statt geschützter Kategorie), persönliche Fotos dürfen nicht als Dokument
# einsortiert werden, kaputte Eingaben dürfen nie eine Ausnahme werfen.
# Aufruf: python test_org_lexicon_adversarial.py  (Exit-Code 0 = bestanden)
from __future__ import annotations

import ast
import os
import random
import sys
import time
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import org_lexicon as L  # noqa: E402
from org_core import ORG_CATEGORIES, ORG_PROTECT, org_kind  # noqa: E402
from org_lexicon import (org_classify_document, org_detect_year, org_lexicon_reason,  # noqa: E402
                         org_normalize)

NOW = time.localtime().tm_year
PROTECTED = {k for k, v in ORG_CATEGORIES.items() if v["tier"] == ORG_PROTECT}


def kind_of(name):
    return org_kind(Path(name.replace("\\", "/")))


def cat(name, text="", kind=None):
    return org_classify_document(name, text, kind or kind_of(name))[0]


# ---------------------------------------------------------------------------
# 1) Wichtige Dokumente: müssen eine GESCHÜTZTE Kategorie bekommen (nie None)
# ---------------------------------------------------------------------------
IMPORTANT = [
    # Windows-Pfade, Laufwerke, UNC, Langpfad-Präfix
    (r"C:\Users\Felix\Documents\Lebenslauf_Felix_Kucher_2024.pdf", "cv"),
    (r"\\NAS\home\Bewerbung\CV.pdf", "cv"),
    (r"\\?\C:\Users\Felix\OneDrive\Dokumente\Arbeitszeugnis.pdf", "certificate"),
    ("C:Lebenslauf.pdf", "cv"),
    ("D:/Scans/Personalausweis_Scan.jpg", "id"),
    # Kopien / Versionen / Download-Reste
    ("Lebenslauf (1).docx", "cv"),
    ("Lebenslauf - Kopie (2).docx", "cv"),
    ("Kopie von Lebenslauf.docx", "cv"),
    ("Felix_Kucher_CV_2024_final_v2 (1).docx", "cv"),
    ("Lebenslauf_final_FINAL_neu.docx", "cv"),
    ("Lebenslauf_Felix.pdf.pdf", "cv"),
    ("Lebenslauf.pdf ", "cv"),
    # Groß-/Kleinschreibung, Umlaute, Unicode-Formen
    ("LEBENSLAUF.PDF", "cv"),
    ("LebenslaufFelixKucher.pdf", "cv"),
    ("Fu\u0308hrerschein.pdf", "id"),                 # NFD (macOS/iCloud)
    ("FÜHRERSCHEIN.PDF", "id"),
    ("Fuehrerschein.jpg", "id"),
    ("Fuhrerschein.jpg", "id"),
    ("ＬＥＢＥＮＳＬＡＵＦ.pdf", "cv"),                 # Vollbreite
    ("Le\u00adbens\u00adlauf.pdf", "cv"),             # weiches Trennzeichen
    ("Lebens\u200blauf.pdf", "cv"),                   # Nullbreite-Leerzeichen
    ("\ufeffLebenslauf.pdf", "cv"),                   # BOM
    ("Lebenslauf 😀 neu.pdf", "cv"),
    ("Lebenslauf_\udcff.pdf", "cv"),                  # kaputte Kodierung (surrogateescape)
    ("Zeugnis_Bachelor_" + "x" * 300 + ".pdf", "certificate"),
    # getrennt geschriebene Komposita
    ("Gehalts-Abrechnung 03-2024.pdf", "payslip"),
    ("Lohn_Abrechnung.pdf", "payslip"),
    ("Lebens Lauf.pdf", "cv"),
    ("Steuer-Bescheid 2022.pdf", "tax"),
    ("Miet Vertrag.pdf", "contract"),
    ("Nebenkosten-Abrechnung 2023.pdf", "property"),
    # Zeugnisse – "Masterzeugnis" wurde früher von "erzeugnis" verschluckt
    ("Masterzeugnis.pdf", "certificate"),
    ("Semesterzeugnis.pdf", "certificate"),
    ("Halbjahreszeugnis Klasse 7.pdf", "certificate"),
    ("Führungszeugnis.pdf", "certificate"),
    ("IHK Prüfungszeugnis.pdf", "certificate"),
    ("Praktikumszeugnis.pdf", "certificate"),
    ("Geburtsurkunde Lea.pdf", "certificate"),
    ("Taufschein.pdf", "certificate"),
    ("Familienstammbuch.pdf", "certificate"),
    ("Kirchenaustritt.pdf", "certificate"),
    ("Birth Certificate.pdf", "certificate"),
    ("Marriage Certificate.pdf", "certificate"),
    # Ausweise
    ("Perso vorne.jpg", "id"),
    ("Reisepass Kinder.jpg", "id"),
    ("Aufenthaltstitel.jpg", "id"),
    ("Waffenbesitzkarte.pdf", "id"),
    ("Jagdschein.pdf", "id"),
    ("Angelschein.pdf", "id"),
    ("Social Security Card.jpg", "id"),
    ("ESTA.pdf", "id"),
    # Steuer – Abkürzungen und Sperrbegriffe, die früher alles blockiert haben
    ("ESt 2022.pdf", "tax"),
    ("ESt-Bescheid 2021.pdf", "tax"),
    ("Est-Erklärung 2022.pdf", "tax"),
    ("Kirchensteuer Bescheid.pdf", "tax"),
    ("P45.pdf", "tax"),
    # Versicherung
    ("Pflegeversicherung Bescheid.pdf", "insurance"),
    ("Arbeitslosenversicherung.pdf", "insurance"),
    ("Insurance Certificate.pdf", "insurance"),
    ("Mitgliedsbescheinigung Krankenkasse.pdf", "insurance"),
    # Gesundheit
    ("Rezept Ibuprofen 600mg.pdf", "health"),
    ("eGK.jpg", "health"),
    ("Krankenkassenkarte.jpg", "health"),
    ("Zahnbonusheft.jpg", "health"),
    ("Vaccination card.jpg", "health"),
    ("Impfpass Baby.jpg", "health"),                  # Ereigniswort darf echten Pass nicht blocken
    ("Mutterpass.pdf", "health"),
    # Gehalt & Sozialleistungen
    ("Bürgergeld Bescheid.pdf", "payslip"),
    ("Jobcenter Bescheid.pdf", "payslip"),
    # Verträge
    ("Betreuungsverfügung.pdf", "contract"),
    ("Power of Attorney.pdf", "contract"),
    ("Offer Letter ACME.pdf", "contract"),
    ("Abfindung Vereinbarung.pdf", "contract"),
    # WhatsApp im Namen: Name zählt trotzdem
    ("Lebenslauf per WhatsApp.pdf", "cv"),
    ("Personalausweis WhatsApp.jpg", "id"),
]

# ---------------------------------------------------------------------------
# 2) Persönliche Fotos/Videos: dürfen KEIN Dokument werden (bleiben Fotos)
# ---------------------------------------------------------------------------
PERSONAL_MEDIA = [
    "Lea Krankenhaus erster Tag.jpg", "Geburt Klinik Lea.jpg", "Ultraschall Baby 12 SSW.jpg",
    "Schwangerschaft 20. Woche.jpg", "Babybauch Schwangerschaft.jpg", "Oma im Krankenhaus.jpg",
    "Zeugnisübergabe Abschlussfeier.jpg", "Hochzeit Standesamt Urkunde.jpg",
    "Hund beim Tierarzt.jpg", "Zahnarzt Lea.jpg", "Therapie Pferd.jpg", "Reha Klinik Ausblick.jpg",
    "Police car.jpg", "Police officer Berlin.jpg", "TÜV Plakette.jpg", "Urlaub Hotel Pool.jpg",
    "Flughafen München.jpg", "Reise Italien 2019.jpg", "Familienfeier Garten.jpg",
    "Neues Auto Papa.jpg", "Uni Party.jpg", "Schule Einschulung Lea.jpg", "Firma Weihnachtsfeier.mp4",
    "Baby Lea erste Schritte.mp4", "Weihnachten 2022.jpg", "Oma 80. Geburtstag.jpg",
    "IMG_1234.jpg", "PXL_20230101_120000.jpg", "DSC00001.JPG", "20230715_143210.jpg",
    "WhatsApp Image 2024-01-01 at 12.00.00.jpeg", "Screenshot 2024-03-01 123456.png",
    "Hochzeit Lisa und Tom.jpg", "Bewerbung Party.jpg",
]

# ---------------------------------------------------------------------------
# 3) Negativfälle (Dokumente ohne Kategorie)
# ---------------------------------------------------------------------------
NEGATIVE = [
    "Milcherzeugnisse.pdf", "Erzeugnisse der Region.pdf", "Steuerung Heizung.pdf",
    "Vertragen sich Hund und Katze.docx", "Garantiert gute Laune.docx", "Compass.pdf",
    "Bypass.pdf", "Passwort.txt", "passwords.txt", "Idee.docx", "Ideen.txt", "Kid.jpg",
    "video_id_list.txt", "Video_ID_123.mp4", "au.pdf", "Australia trip.docx", "Auto.jpg",
    "Tor.jpg", "Tor des Monats.mp4", "Rezept Lasagne.pdf", "Rezepte Sammlung.pdf",
    "Einkaufsliste.txt", "Gedichte.docx", "Brief an Oma.docx", "Lieferadresse.txt",
    "Polizei Kontrolle.jpg", "Nils Teuer.jpg", "Lisa Mietz Party.jpg", "Lea Bens Lauf.pdf",
    "Kochbuch.pdf", "Policy.pdf", "Privacy Policy.pdf", "Abrechnung 03-2024.pdf",
    "Bescheinigung.pdf", "notes.txt", "Untitled.docx", "Dokument1.docx", "Neues Dokument.txt",
    "Scan_0001.pdf", "Mappe1.xlsx",
]

T_CHATLIKE = ("01.03.2024, 10:00 - Teilnehmer: Max\n01.03.2024, 10:05 - Thema: Budget\n"
              "01.03.2024, 10:30 - Ergebnis: ok\n")
T_WHATSAPP = ("12.03.23, 14:22 - Max: Hast du den Vertrag schon unterschrieben?\n"
              "12.03.23, 14:23 - Felix: Ja, und die Rechnung vom Arzt ist auch gekommen\n"
              "12.03.23, 14:25 - Max: Super, dann schick mir den Kontoauszug\n"
              "12.03.23, 14:30 - Felix: Mach ich, Steuererklärung auch gleich\n")
T_KONTO_TIMES = ("Sparkasse\nKontoauszug\n02.03.2024, 12:30 Uhr - Lastschrift: Amazon\n"
                 "03.03.2024, 08:15 - Gutschrift: Gehalt\n05.03.2024, 09:00 - Kartenzahlung: Rewe\n"
                 "Alter Kontostand 1.000 Neuer Kontostand 900\nWertstellung Buchungstag\n")

# Texte (nur Inhalt, generischer Scan-Name)
TEXT_CASES = [
    ("Sehr geehrte Damen und Herren,\nhiermit bewerbe ich mich auf die ausgeschriebene Stelle "
     "als Softwareentwickler.", "cv"),
    ("Standesamt Berlin-Mitte\nGeburtsurkunde\n(Geburtenregister Nr. G 1234/1990)", "certificate"),
    ("Arbeits-\nzeugnis\nHerr Max war vom 01.01.2018 bis 31.12.2022 bei uns.", "certificate"),
    ("Lebens\u00adlauf\nFelix Kucher\nBerufserfahrung\nSprachkenntnisse", "cv"),
    ("Techniker Krankenkasse\nMitgliedsbescheinigung\nHerr Kucher ist seit 2015 versichert.",
     "insurance"),
    ("SCHUFA-Bonitätsauskunft\nDatenkopie nach Art. 15 DSGVO", "bank"),
    (T_KONTO_TIMES, "bank"),
    ("REISEPASS PASSPORT PASSEPORT\nBUNDESREPUBLIK DEUTSCHLAND\nP<D<<KUCHER<<FELIX<<<<", "id"),
    ("NHS Trust\nDischarge Summary\nPatient: John Doe\nDiagnosis: appendicitis", "health"),
    ("Form W-2 Wage and Tax Statement 2023\nEmployer identification number", "tax"),
    ("Abrechnung der Brutto/Netto-Bezüge für März 2024\nPersonalnummer 4711\nSteuer-ID 123\n"
     "Versicherungsnummer 12 345678 A 123\nKirchensteuer 12,00\nPflegeversicherung 76,50\n"
     "Auszahlungsbetrag 2.900,00", "payslip"),
]


class TestImportantNeverLost(unittest.TestCase):
    def test_important_documents(self):
        bad = []
        for name, want in IMPORTANT:
            got = cat(name)
            if got != want:
                bad.append((name, want, got))
        self.assertEqual(bad, [], "wichtige Dokumente falsch/nicht erkannt")

    def test_important_are_protected_tier(self):
        for name, want in IMPORTANT:
            if want in PROTECTED:
                self.assertIn(cat(name), PROTECTED, name)

    def test_path_objects(self):
        self.assertEqual(org_classify_document(Path("/x/Lebenslauf.pdf"))[0], "cv")
        self.assertEqual(org_classify_document(PureWindowsPath(r"C:\x\Steuerbescheid 2022.pdf"))[0], "tax")
        self.assertEqual(org_classify_document(PurePosixPath("Arbeitsvertrag.pdf"))[0], "contract")
        self.assertEqual(org_classify_document(os.fsencode("Lebenslauf.pdf"))[0], "cv")

    def test_strong_name_survives_chatlike_text(self):
        # früher: 3 Zeilen mit Uhrzeit -> alles None, auch ein Arbeitszeugnis
        for name, want in [("Arbeitszeugnis.pdf", "certificate"), ("Lebenslauf.pdf", "cv"),
                           ("Kontoauszug_2024_03.pdf", "bank"),
                           ("Kündigung Mietvertrag.pdf", "contract")]:
            self.assertEqual(cat(name, T_CHATLIKE), want, name)
            self.assertEqual(cat(name, T_WHATSAPP), want, name)

    def test_chat_export_name_still_none(self):
        self.assertIsNone(cat("WhatsApp Chat mit Steuerberater.txt", T_WHATSAPP))
        self.assertIsNone(cat("Chat mit Vermieter.txt"))
        self.assertIsNone(cat("chat.txt", T_WHATSAPP))
        self.assertIsNone(cat("Notizen.txt", T_WHATSAPP))

    def test_text_only_cases(self):
        bad = []
        for text, want in TEXT_CASES:
            got = cat("Scan_20240301.pdf", text)
            if got != want:
                bad.append((text[:40], want, got))
        self.assertEqual(bad, [])

    def test_random_suffix_noise_keeps_cv(self):
        # Zufällige Zeichen hinter "Lebenslauf_" dürfen die Erkennung nie kippen
        rnd = random.Random(42)
        alphabet = "abcXYZ0123456789 _-.()[]{}äöüßÄÖÜ€@#~!'&+,;=😀\u0301\u00ad\u200b\udcff"
        for _ in range(500):
            junk = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 40)))
            name = "Lebenslauf_" + junk + ".pdf"
            got = org_classify_document(name)
            self.assertEqual(got[0], "cv", repr(name))


class TestPersonalMedia(unittest.TestCase):
    def test_personal_photos_not_documents(self):
        bad = [(n, cat(n)) for n in PERSONAL_MEDIA if cat(n) is not None]
        self.assertEqual(bad, [], "persönliche Fotos als Dokument eingeordnet")

    def test_document_scans_as_images_still_work(self):
        for n, want in [("Personalausweis_Scan.jpg", "id"), ("Quittung.jpg", "invoice"),
                        ("Zeugnis.jpg", "certificate"), ("Fahrzeugschein.jpg", "vehicle"),
                        ("Garantiekarte.jpg", "invoice"), ("Ausweis.png", "id"),
                        ("Röntgen Knie.jpg", "health"), ("Gesundheitskarte.jpg", "health")]:
            self.assertEqual(cat(n), want, n)


class TestNegatives(unittest.TestCase):
    def test_negatives(self):
        bad = [(n, cat(n)) for n in NEGATIVE if cat(n) is not None]
        self.assertEqual(bad, [])

    def test_skip_kinds(self):
        for n in ["Lebenslauf.exe", "Rechnung.pdf.exe", "Steuer.py", "Vertrag.stl", "Rechnung.fasta"]:
            self.assertIsNone(cat(n), n)


class TestRobustness(unittest.TestCase):
    WEIRD = [None, 0, 1.5, b"", b"\xff\xfe", bytearray(b"Lebenslauf.pdf"), [], {}, object(),
             "", " ", ".", "..", ".pdf", "\\", "/", "C:\\", "\x00", "\udcff" * 10, "\u0301" * 50,
             "ä" * 10000, "a" * 100000, "1" * 5000, "2019" * 2000, "\n" * 5000]

    def _shape(self, r):
        self.assertIsInstance(r, tuple)
        self.assertEqual(len(r), 4)
        c, s, t, y = r
        self.assertTrue(c is None or c in ORG_CATEGORIES, c)
        self.assertTrue(0.0 <= s <= 1.0)
        self.assertIsInstance(t, list)
        for x in t:
            self.assertIsInstance(x, str)
        self.assertTrue(y is None or (isinstance(y, int) and 1990 <= y <= NOW + 1), y)

    def test_never_raises(self):
        for a in self.WEIRD:
            for b in self.WEIRD:
                for k in (None, "image", "IMAGE", "document", 5, "", "other"):
                    self._shape(org_classify_document(a, b, k))
            self.assertTrue(org_detect_year(a, a) is None or isinstance(org_detect_year(a, a), int))
            self.assertIsInstance(org_normalize(a), str)
            self.assertIsInstance(org_lexicon_reason(a, a), str)

    def test_reason_robust(self):
        self.assertEqual(org_lexicon_reason("cv", 5), "Lebenslauf & Bewerbung erkannt")
        self.assertEqual(org_lexicon_reason("cv", "lebenslauf"),
                         "Lebenslauf & Bewerbung erkannt (lebenslauf)")
        self.assertEqual(org_lexicon_reason("", ["x"]), "")
        self.assertEqual(org_lexicon_reason(None, None), "")
        self.assertIn("Steuer", org_lexicon_reason("tax", ["a", "b", "c", "d"]))
        self.assertNotIn("d", org_lexicon_reason("tax", ["a", "b", "c", "d"]).split("(")[1])

    def test_fuzz_unicode(self):
        rnd = random.Random(7)
        for _ in range(1500):
            n = "".join(chr(rnd.randint(1, 0x2FFFF)) for _ in range(rnd.randint(0, 60)))
            t = "".join(chr(rnd.randint(1, 0x2FFFF)) for _ in range(rnd.randint(0, 300)))
            self._shape(org_classify_document(n, t, rnd.choice(["document", "image", "video"])))
            org_detect_year(n, t)
            org_normalize(n)

    def test_pathological_timing(self):
        inputs = [
            ("x.pdf", "a" * 1000000), ("x.pdf", "2019 " * 4000), ("x.pdf", "01.01.2019 " * 2000),
            ("x.pdf", "1.1.20, 1:00 - x: " * 2000), ("x.pdf", "Steuerbescheid für " * 1000),
            ("x.pdf", "Einkommensteuer" + " x" * 12 + "\n" * 2000), ("x.pdf", "januar " * 3000),
            ("x.pdf", "-\n" * 10000), ("x.pdf", "a-\nb" * 5000), ("Lebenslauf" + "a" * 200000 + ".pdf", ""),
            ("x" * 50000 + ".pdf", "\u0301" * 20000), ("_" * 100000, "."), ("a-b " * 20000, "ä-ö " * 5000),
        ]
        for n, t in inputs:
            t0 = time.perf_counter()
            org_classify_document(n, t)
            org_detect_year(n, t)
            self.assertLess(time.perf_counter() - t0, 1.0, (n[:20], t[:20]))


class TestYears(unittest.TestCase):
    def test_amount_after_anchor_is_not_year(self):
        self.assertEqual(org_detect_year("x.pdf", "Einkommensteuer 2015,00 EUR festgesetzt. "
                                                  "Datum 03.04.2024"), 2024)
        self.assertEqual(org_detect_year("x.pdf", "Einkommensteuer 2.019,00 EUR\nDatum 03.04.2024"), 2024)
        self.assertEqual(org_detect_year("x.pdf", "Bescheid für 2022 über Einkommensteuer"), 2022)
        self.assertEqual(org_detect_year("x.pdf", "Veranlagungszeitraum 2021."), 2021)

    def test_path_and_basename(self):
        self.assertEqual(org_detect_year(Path("/a/Steuerbescheid 2022.pdf")), 2022)
        self.assertEqual(org_detect_year(PureWindowsPath(r"C:\Steuer 2019\Rechnung.pdf")), None)
        self.assertEqual(org_detect_year(r"C:\Steuer 2019\Rechnung 2021.pdf"), 2021)
        # Konsistent mit org_classify_document
        self.assertEqual(org_classify_document(r"C:\Steuer 2019\Rechnung.pdf")[3], None)

    def test_boundaries(self):
        self.assertIsNone(org_detect_year("Foto_1989.pdf"))
        self.assertEqual(org_detect_year("Foto_1990.pdf"), 1990)
        self.assertEqual(org_detect_year("Plan_%d.pdf" % (NOW + 1)), NOW + 1)
        self.assertIsNone(org_detect_year("Plan_%d.pdf" % (NOW + 2)))
        self.assertIsNone(org_detect_year("12345678901234567890.pdf"))
        self.assertIn(org_detect_year("IMG_20231301.jpg"), (None, 2023))   # Monat 13 ungültig

    def test_date_formats(self):
        self.assertEqual(org_detect_year("x.pdf", "Datum: 31.12.2019"), 2019)
        self.assertEqual(org_detect_year("x.pdf", "Stand 2020-05-01"), 2020)
        self.assertEqual(org_detect_year("x.pdf", "Wintersemester 2023/24"), 2023)
        self.assertEqual(org_detect_year("x.pdf", "München, im März 2018"), 2018)
        self.assertEqual(org_detect_year("x.pdf", "London, 5 March 2017"), 2017)
        self.assertEqual(org_detect_year("Vertrag 31.12.19.pdf"), 2019)
        self.assertEqual(org_detect_year("Gehaltsabrechnung_03_2024.pdf"), 2024)
        self.assertEqual(org_detect_year("Scan_20240301.pdf"), 2024)
        # Chat-Text wird für die Kategorie verworfen, das Jahr bleibt nutzbar
        self.assertEqual(org_detect_year("x.txt", T_WHATSAPP), 2023)

    def test_year_from_classify_matches(self):
        for n, t in [("Steuerbescheid 2022.pdf", ""), ("x.pdf", "Bescheid für 2021"),
                     ("Lohnsteuerbescheinigung.pdf", "Lohnsteuerbescheinigung für 2023")]:
            self.assertEqual(org_classify_document(n, t)[3], org_detect_year(n, t))


class TestScoresAndRules(unittest.TestCase):
    def test_clear_names_score(self):
        for n in ["Masterzeugnis.pdf", "Gehalts-Abrechnung 03.pdf", "ESt-Bescheid 2021.pdf",
                  "Lebenslauf per WhatsApp.pdf", "Kirchensteuer Bescheid.pdf",
                  r"C:\Users\Felix\Documents\Lebenslauf.pdf"]:
            r = org_classify_document(n)
            self.assertGreaterEqual(r[1], 0.6, (n, r))

    def test_disambiguation_kept(self):
        self.assertEqual(cat("Gehaltsabrechnung.pdf"), "payslip")
        self.assertEqual(cat("Nebenkostenabrechnung.pdf"), "property")
        self.assertEqual(cat("Rechnung.pdf"), "invoice")
        self.assertIsNone(cat("Abrechnung.pdf"))
        # Kirchensteuer/Pflegeversicherung im Gehaltszettel-Text zählen nicht
        r = org_classify_document("scan.pdf", TEXT_CASES[-1][0])
        self.assertEqual(r[0], "payslip")

    def test_terms_are_explanatory(self):
        r = org_classify_document("Masterzeugnis.pdf")
        self.assertTrue(r[2])
        self.assertTrue(org_lexicon_reason(r[0], r[2]).startswith("Zeugnisse & Urkunden erkannt"))


class TestCodeRules(unittest.TestCase):
    def test_prefixes_and_compat(self):
        with open(os.path.join(HERE, "org_lexicon.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("from __future__ import annotations", src)
        tree = ast.parse(src)
        allowed = ("org_", "Org", "ORG_", "_org_", "_ORG_")
        for node in tree.body:
            names = []
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                names = [node.name]
            elif isinstance(node, ast.Assign):
                names = [n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
            for n in names:
                self.assertTrue(n.startswith(allowed), n)
        for bad in ("removeprefix", "removesuffix", "strict=", "match ", "case "):
            for line in src.splitlines():
                if line.lstrip().startswith(bad):
                    self.fail("nicht 3.9-kompatibel: %r" % line)

    def test_lexicon_consistent(self):
        self.assertEqual(L._ORG_LEX_CONFLICTS, [])
        for t in L._ORG_LEX_TERMS.values():
            self.assertTrue(t.cat == "" or t.cat in ORG_CATEGORIES, t.cat)
        for k in L._ORG_MEDIA_DENY:
            self.assertIn(k, L._ORG_LEX_TERMS, k)


class TestPerformance(unittest.TestCase):
    def test_60k_files(self):
        rnd = random.Random(1)
        words = ["IMG", "Scan", "Rechnung", "Lebenslauf", "Vertrag", "notes", "final", "v2", "2023",
                 "Kopie", "Foto", "Urlaub", "DSC", "Bericht", "Gehalts", "Abrechnung", "Zeugnis", "(1)"]
        exts = [".pdf", ".jpg", ".docx", ".txt", ".mp4", ".png"]
        texts = ["", "", "", "Rechnung Nr. 1\nRechnungsdatum 01.02.2024\nGesamtbetrag 10 €" * 3,
                 "Sehr geehrte Damen und Herren, " * 40]
        items = []
        for i in range(60000):
            n = " ".join(rnd.choice(words) for _ in range(rnd.randint(1, 4))) + rnd.choice(exts)
            items.append((n, texts[i % len(texts)] if i % 4 == 0 else "", kind_of(n)))
        t0 = time.perf_counter()
        for n, t, k in items:
            org_classify_document(n, t, k)
        dt = time.perf_counter() - t0
        print("\n  60.000 Dateien in %.2f s" % dt)
        self.assertLess(dt, 15.0)


if __name__ == "__main__":
    prog = unittest.main(exit=False, verbosity=1)
    res = prog.result
    n = res.testsRun
    bad = len(res.failures) + len(res.errors)
    print("Python %s: org_lexicon adversarial – %d Tests, %d Fehler, %d wichtige Dokumente, "
          "%d Fotos, %d Negativfälle" % (sys.version.split()[0], n, bad, len(IMPORTANT),
                                         len(PERSONAL_MEDIA), len(NEGATIVE)))
    sys.exit(1 if bad else 0)
