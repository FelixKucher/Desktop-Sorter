# ===========================================================================
# ORDNER-ASSISTENT – zweisprachiger Dokument-Klassifikator (Deutsch + Englisch)
# ===========================================================================
# Erkennt anhand von Dateiname UND extrahiertem Text, was ein Dokument ist
# (Lebenslauf, Zeugnis, Ausweis, Vertrag, Steuer, Gehalt, ...), und das
# Bezugsjahr. Nur Standardbibliothek; alle Namen beginnen mit org_/Org/ORG_.
#
# Grundidee:
#   * Alle Begriffe stehen in EINEM vorkompilierten Trie-Regex. Die Suche ist
#     "links-längste": an jeder Stelle gewinnt der längste Begriff und
#     verbraucht die Zeichen. Dadurch zählt z. B. in "Gehaltsabrechnung" nur
#     der Gesamtbegriff (Gehalt), nicht das enthaltene "rechnung" (Rechnung).
#     Sperrbegriffe (Kategorie "") nutzen das gezielt: "abrechnung",
#     "berechnung", "erzeugnis", "steuerung" ... verbrauchen Text ohne Wertung.
#   * Modi: "s" = Teilwort (deutsche Komposita: "zeugnis" trifft
#     Arbeitszeugnis), "p" = Wortanfang, "w" = ganzes Wort (kurze Kürzel wie
#     cv, id, nda, w2 – kein "cv" in "cvs", kein "id" in "video").
#   * Dateiname zählt 3-fach, Text 1-fach. Text braucht mind. 2 verschiedene
#     Treffer je Kategorie oder einen sehr starken Begriff (Gewicht >= 3),
#     außer der Dateiname stützt dieselbe Kategorie schon.
#
# Bewusste Entscheidungen:
#   * Garantie/Gewährleistung/Garantieschein -> "invoice" (gehört zum Kaufbeleg,
#     der für Garantiefälle aufbewahrt wird).
#   * Kaufvertrag Auto -> "contract" (Vertrag ist geschützt und wiegt schwerer).
#   * Geburts-/Heirats-/Sterbeurkunde -> "certificate" (Ordner "Zeugnisse & Urkunden").
#   * Führerschein -> "id"; Zulassungsbescheinigung/Fahrzeugschein -> "vehicle".
#   * Lohnsteuerbescheinigung, Elster, Spendenbescheinigung -> "tax";
#     Renteninformation/Rentenbescheid -> "payslip" (Ordner "Gehalt & Rente").
#   * Transcript of Records / ToR -> "certificate"; Notenspiegel -> "education".
#   * Versicherungsvertrag/-police -> "insurance" (nicht "contract").
#   * Nebenkosten-/Strom-/Gasabrechnung -> "property" (Wohnen).
#   * "Rezept" zählt nur mit medizinischem Kontext (Arzt, Praxis, Medikament,
#     Apotheke, mg, PZN ...) als "health" – sonst ist es ein Kochrezept.
#   * Bilder/Videos/Audio: nur starke Begriffe im Namen (Gewicht >= 2), damit
#     "Urlaub.jpg" oder "Auto.jpg" kein Dokument wird, "Personalausweis.jpg" aber schon.
#     Situationswörter (Krankenhaus, Klinik, Ultraschall, Arzt, Police ...) und
#     Ereigniswörter (Feier, Hochzeit, Urlaub, Baby ...) machen aus einem persönlichen
#     Foto kein Dokument (sonst landet das Neugeborenen-Foto im Ordner "Gesundheit").
#   * Chat-Export: Name "WhatsApp Chat mit ..." -> None. Sieht nur der TEXT wie ein Chat
#     aus (oder steht bloß "WhatsApp" im Namen), zählt der Text nicht, der Dateiname
#     aber weiterhin ("Lebenslauf per WhatsApp.pdf" bleibt Lebenslauf).
#   * Getrennt geschriebene Komposita im Namen ("Gehalts-Abrechnung", "Lebens Lauf")
#     werden zusätzlich zusammengezogen geprüft.
from __future__ import annotations

import math
import os
import re
import time
import unicodedata

try:
    from org_core import ORG_CATEGORIES
except ImportError:  # beim Einfügen in die große Datei existiert ORG_CATEGORIES bereits
    pass

# Kategorien, die dieses Modul vergeben kann (alle sind Schlüssel von ORG_CATEGORIES)
ORG_LEX_CATEGORIES = ("cv", "certificate", "id", "contract", "insurance", "tax", "payslip",
                      "health", "property", "invoice", "bank", "vehicle", "education",
                      "work", "science", "travel", "manual")

ORG_LEX_NAME_FACTOR = 3.0      # Dateiname zählt dreifach
ORG_LEX_MIN_SCORE = 2.0        # Mindest-Evidenz für eine Einordnung
ORG_LEX_MIN_TEXT = 3.0         # Mindest-Evidenz, wenn nur der Text (nicht der Name) belegt
ORG_LEX_STRONG = 3.0           # ein Begriff ab diesem Gewicht reicht im Text allein
ORG_LEX_TEXT_CAP = 8.5         # Obergrenze Text-Evidenz je Kategorie (< starker Namensbegriff 9)
ORG_LEX_HEAD = 160             # erste 3 Zeilen, max. so viele Zeichen = Überschrift
ORG_LEX_MAX_TEXT = 6000        # so viele Zeichen Text werden ausgewertet
ORG_LEX_MEDIA_MIN = 2.0        # Bilder/Videos: nur starke Namensbegriffe
_ORG_MEDIA_KINDS = ("image", "video", "audio")
_ORG_SKIP_KINDS = ("installer", "code", "cad", "science", "science_data")
# Bilder/Videos: diese Begriffe beschreiben eher die Situation eines persönlichen Fotos
# ("Lea Krankenhaus erster Tag.jpg", "Ultraschall Baby.jpg", "Police car.jpg") als ein
# abfotografiertes Dokument -> bei Medien nicht werten.
_ORG_MEDIA_DENY = frozenset((
    "police", "policen", "arzt", "aerztin", "arztin", "krankenhaus", "klinik", "hospital",
    "therapie", "ultraschall", "schwangerschaft", "krankheit", "erkrankung", "reha",
    "rehabilitation", "krankengymnastik", "impfung", "impf", "vaccination", "vaccine", "sehtest",
    "hoertest", "hortest", "blutdruck", "zahnbehandlung", "tuev", "tuv", "dekra"))
# Bilder/Videos mit Ereignis-Wörtern sind persönliche Fotos, keine Dokument-Scans
# ("Lea Krankenhaus erster Tag.jpg", "Zeugnisübergabe Abschlussfeier.jpg")
_ORG_MEDIA_EVENT_RE = re.compile(
    r"(?:feier|party|hochzeit|wedding|urlaub|ferien|geburtstag|birthday|baby|taufe|einschulung|"
    r"abiball|ausflug|familie(?!n ?stammbuch|n ?kasse)|family|vacation|holiday|weihnacht|christmas|silvester|selfie|"
    r"strand|beach|erster tag|first day|geburt (?!urkunde)|newborn|neugeboren)")

# ---------------------------------------------------------------------------
# Normalisierung
# ---------------------------------------------------------------------------
_ORG_PLAIN_TABLE = {ord("ä"): "a", ord("ö"): "o", ord("ü"): "u", ord("ß"): "ss"}
# unsichtbare Zeichen (weiches Trennzeichen aus PDFs, Nullbreite-Zeichen, BOM) -> entfernen
_ORG_INVISIBLE_RE = re.compile("[\u00ad\u200b\u200c\u200d\u2060\ufeff]+")
_ORG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_ORG_CAMEL_RE = re.compile(r"(?<=[a-zäöüß])(?=[A-ZÄÖÜ][a-zäöüß])")
_ORG_COMBINING_RE = re.compile("[̀-ͯ᪰-᫿᷀-᷿⃐-⃿︠-︯]+")
# Silbentrennung am Zeilenende im Text: "Arbeits-\nzeugnis" -> "Arbeitszeugnis"
_ORG_HYPHEN_BREAK_RE = re.compile(r"(?<=[a-zäöüß])-[ \t]*\r?\n[ \t]*(?=[a-zäöüß])")


def _org_fold(s: str) -> str:
    """Kleinschreibung + Umlaute -> ae/oe/ue/ss + übrige Akzente entfernen (Satzzeichen bleiben)."""
    s = s.lower()
    if s.isascii():
        return s
    s = unicodedata.normalize("NFC", _ORG_INVISIBLE_RE.sub("", s))
    # str.replace ist deutlich schneller als translate() mit Mehrzeichen-Ersetzungen
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    if "ẞ" in s:
        s = s.replace("ẞ", "ss")
    if not s.isascii():
        s = _ORG_COMBINING_RE.sub("", unicodedata.normalize("NFKD", s)).lower()
    return s


def _org_to_str(v) -> str:
    """Eingabe tolerant in Text wandeln (Path, bytes, None ...)."""
    if isinstance(v, str):
        return v
    if v is None:
        return ""
    if isinstance(v, (bytes, bytearray)):
        try:
            return os.fsdecode(bytes(v))
        except Exception:
            return ""
    try:
        return os.fspath(v) if hasattr(v, "__fspath__") else str(v)
    except Exception:
        return ""


def org_normalize(s: str) -> str:
    """Kleinschreibung, ä->ae ö->oe ü->ue ß->ss, Trennzeichen (_ - . + / usw.) -> Leerzeichen,
    Leerraum zusammengefasst. Alle Lexikon-Muster sind gegen diese Form geschrieben."""
    try:
        if not isinstance(s, str):
            s = _org_to_str(s)
        return _ORG_NON_ALNUM_RE.sub(" ", _org_fold(s)).strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Lexikon: (Kategorie, Modus, Namensgewicht, "begriff, begriff, ...", [Textgewicht])
# Kategorie "" = Sperrbegriff (verbraucht Text, zählt nichts).
# Begriffe mit echten Umlauten schreiben: daraus entstehen automatisch die
# Formen "ae" (Fuehrerschein) und – ab 5 Zeichen – "a" (Fuhrerschein).
# ---------------------------------------------------------------------------
_ORG_LEX_SRC = (
    # ----- Lebenslauf & Bewerbung -----
    ("cv", "s", 3, "lebenslauf, lebensläufe, curriculum vitae, bewerbungsunterlagen, bewerbungsmappe, "
                   "bewerbungsschreiben, motivationsschreiben, initiativbewerbung, bewerbungsfoto"),
    ("cv", "s", 2.5, "bewerbung, anschreiben, cover letter, covering letter, motivation letter, "
                     "letter of motivation, application letter, job application, letter of application"),
    ("cv", "w", 2.5, "cv, resume, resumes, résumé"),
    # typische erste Sätze eines Anschreibens (reichen allein im Text)
    ("cv", "s", 3, "hiermit bewerbe ich mich, bewerbe mich hiermit, bewerbe ich mich um, "
                   "i am writing to apply, i would like to apply for, i am applying for"),
    ("cv", "s", 2, "bewerbe, vorstellungsgespräch, stellenanzeige, stellenausschreibung, stellengesuch, "
                   "personal statement, bewerber"),
    ("cv", "s", 1, "berufserfahrung, berufliche erfahrung, persönliche daten, sprachkenntnisse, "
                   "edv kenntnisse, it kenntnisse, computerkenntnisse, work experience, "
                   "professional experience, beruflicher werdegang, werdegang, schulbildung, "
                   "key skills, technical skills, hobbys, hobbies, referenzen auf anfrage, "
                   "references available, kernkompetenzen, qualifikationsprofil"),
    ("cv", "w", 1, "linkedin, xing, skills"),

    # ----- Zeugnisse & Urkunden -----
    ("certificate", "s", 3, "zeugnis, zeugnisse, urkunde, urkunden, arbeitszeugnis, zwischenzeugnis, "
                            "abschlusszeugnis, abiturzeugnis, reifezeugnis, meisterbrief, gesellenbrief, "
                            "vollsten zufriedenheit, letter of recommendation, recommendation letter, "
                            "reference letter, letter of reference, transcript of records, "
                            "employment certificate, certificate of employment, approbationsurkunde"),
    ("certificate", "s", 3, "masterzeugnis, taufschein, familienstammbuch, stammbuch, namensänderung, "
                            "vaterschaftsanerkennung, kirchenaustritt, austrittsbescheinigung, "
                            "birth certificate, marriage certificate, death certificate, "
                            "degree certificate"),
    ("certificate", "s", 2, "zertifikat, certificate, diploma, approbation, teilnahmebescheinigung, "
                            "teilnahmebestätigung, fortbildungsnachweis, qualifikationsnachweis, "
                            "empfehlungsschreiben, referenzschreiben, erbschein"),
    ("certificate", "w", 2, "diplom, toefl, ielts, telc, testdaf, ecdl, cambridge certificate"),
    ("certificate", "s", 1.5, "abitur, to whom it may concern, without reservation, highly recommend, "
                              "wärmstens empfehlen, uneingeschränkt empfehlen, berufliche zukunft, "
                              "weiteren berufsweg, weiteren lebensweg, bedauern sein ausscheiden, "
                              "bedauern ihr ausscheiden, bedauern wir sein, bedauern wir ihr, "
                              "verhalten gegenüber vorgesetzten, zu unserer vollen zufriedenheit, "
                              "stets zu unserer, auf eigenen wunsch, zu unserer vollsten"),
    ("certificate", "s", 1, "ausscheiden, tätigkeitsbereich, aufgabengebiet, fachkenntnisse, "
                            "arbeitsweise, i recommend, recommend him, recommend her"),

    # ----- Ausweise & Identität -----
    ("id", "s", 3, "personalausweis, reisepass, kinderreisepass, passport, führerschein, "
                   "aufenthaltstitel, aufenthaltserlaubnis, niederlassungserlaubnis, "
                   "sozialversicherungsausweis, identity card, id card, driver s license, "
                   "drivers license, driving licence, driving license, driver license, "
                   "staatsangehörigkeitsausweis, residence permit"),
    ("id", "s", 2, "ausweis, ausweiskopie, ausweisdokument, ausweisnummer, visum, meldebescheinigung, "
                   "anmeldebestätigung, aufenthaltsbescheinigung, passkopie, passnummer, einbürgerung"),
    ("id", "w", 2, "perso, id scan, id kopie, id copy, id front, id back, id vorderseite, "
                   "id rückseite, green card, pass kopie"),
    ("id", "w", 0.7, "id"),
    ("id", "w", 0.5, "visa"),
    ("id", "s", 3, "waffenbesitzkarte, jagdschein, angelschein, fischereischein, pilotenlizenz, "
                   "segelschein, social security card"),
    ("id", "w", 2.5, "esta"),
    ("id", "s", 2.5, "visa antrag, visaantrag, visa application, ummeldung"),

    # ----- Verträge & Rechtliches -----
    ("contract", "s", 3, "vertrag, verträge, arbeitsvertrag, mietvertrag, kaufvertrag, aufhebungsvertrag, "
                         "ehevertrag, darlehensvertrag, leasingvertrag, ausbildungsvertrag, "
                         "untermietvertrag, kreditvertrag, dienstvertrag, werkvertrag, erbvertrag, "
                         "contract, contracts, tenancy agreement, rental agreement, lease agreement, "
                         "employment agreement, non disclosure"),
    ("contract", "s", 2.5, "betreuungsverfügung, power of attorney, last will, offer letter, "
                           "letter of offer, abfindung"),
    ("contract", "s", 2.5, "kündigung, kündigungsbestätigung, geheimhaltungsvereinbarung, "
                           "vertraulichkeitsvereinbarung, vollmacht, vorsorgevollmacht, "
                           "generalvollmacht, testament, scheidungsurteil, scheidungsantrag, "
                           "scheidungsvereinbarung, sorgerecht"),
    ("contract", "s", 2, "vereinbarung, agreement, amtsgericht, landgericht, arbeitsgericht, "
                         "familiengericht, gerichtsurteil, gerichtsbeschluss, gerichtsvollzieher, "
                         "mahnbescheid"),
    ("contract", "w", 2.5, "nda, lease"),
    ("contract", "w", 2, "scheidung, urteil"),
    ("contract", "s", 1.5, "rechtsanwalt, anwalt, anwältin, vertragspartner, vertragsparteien, "
                           "governing law, contracting parties, hereinafter"),
    ("contract", "s", 1, "vertragsbeginn, vertragslaufzeit, vertragsende, kündigungsfrist, probezeit, "
                         "widerruf, beurkundung, notar, vertragsbedingungen, terms and conditions"),
    ("contract", "s", 0.7, "unterschrieben, unterzeichnet, gegengezeichnet, unterschrift, laufzeit"),
    ("contract", "w", 0.7, "signed, leasing"),
    ("contract", "s", 1, "vertragsnummer, vertragskonto, vertragskontonummer", 0),

    # ----- Versicherungen -----
    ("insurance", "s", 3, "versicherungsschein, versicherungspolice, versicherungsvertrag, "
                          "versicherungsbestätigung, versicherungsnachweis, haftpflichtversicherung, "
                          "hausratversicherung, berufsunfähigkeitsversicherung, insurance policy, "
                          "certificate of insurance, schadensmeldung, schadenmeldung, "
                          "schadensanzeige, schadenanzeige"),
    ("insurance", "s", 2, "versicherung, versicherungen, haftpflicht, hausrat, rechtsschutz, "
                          "berufsunfähigkeit, lebensversicherung, unfallversicherung, "
                          "krankenzusatzversicherung, zahnzusatzversicherung, kfz versicherung, "
                          "autoversicherung, reiseversicherung, reiserücktritt, "
                          "auslandskrankenversicherung, wohngebäudeversicherung, insurance, "
                          "versicherungsbedingungen, beitragsrechnung, beitragsanpassung, "
                          "schadensfall, schadenfall, schadennummer, schadensnummer, "
                          "versicherungsschutz, versicherungsnehmer, health insurance, "
                          "unfallbericht, unfallmeldung"),
    ("insurance", "s", 2, "krankenversicherung", 0.7),
    ("insurance", "w", 2.5, "police, policen"),
    ("insurance", "w", 2, "pkv, gkv, evb"),
    ("insurance", "s", 3, "insurance certificate, mitgliedsbescheinigung"),
    ("insurance", "s", 1.5, "krankenkasse, beitragsbescheinigung, versicherte person"),
    ("insurance", "w", 0.7, "allianz, huk, huk coburg, ergo, axa, debeka, generali, signal iduna, devk, "
                            "hdi, lvm, provinzial, barmenia, gothaer, aok, barmer, dak, tk, ikk, bkk, "
                            "techniker krankenkasse"),
    ("insurance", "s", 1, "versicherungsnummer, rentenversicherungsnummer, sozialversicherungsnummer, "
                          "versichertennummer, versicherungsnr, krankenversichertennummer", 0),

    # ----- Steuer -----
    ("tax", "s", 3, "steuerbescheid, einkommensteuerbescheid, einkommensteuererklärung, "
                    "steuererklärung, lohnsteuerbescheinigung, übermittlungsprotokoll, tax return, "
                    "tax assessment, steuerbescheinigung, jahressteuerbescheinigung, "
                    "umsatzsteuervoranmeldung, umsatzsteuererklärung, gewerbesteuererklärung, "
                    "spendenbescheinigung, zuwendungsbestätigung, steuerrückerstattung, "
                    "lohnsteuerjahresausgleich, form 1099, form 1040"),
    ("tax", "p", 3, "elster"),
    ("tax", "s", 2, "einkommensteuer, finanzamt, steuerberater, steuerberatung, werbungskosten, "
                    "sonderausgaben, außergewöhnliche belastungen, haushaltsnahe, kapitalerträge, "
                    "freistellungsauftrag, steuererstattung, steuernachzahlung, veranlagung, "
                    "zu versteuernde, zu versteuerndes, bruttoarbeitslohn, einbehaltene lohnsteuer, "
                    "self assessment, erbschaftsteuer, schenkungsteuer"),
    ("tax", "w", 3, "est bescheid, estbescheid, est erklärung, esterklärung, p45"),
    ("tax", "w", 2, "anlage n, anlage kap, anlage vorsorgeaufwand, anlage so, anlage kind, anlage r, "
                    "anlage v, w 2, w2, p60, lstb, 1099 misc, 1099 int, 1099 div, 1099 nec"),
    ("tax", "w", 1.5, "tax, taxes, irs, hmrc"),
    ("tax", "s", 1.5, "steuer, steuern", 1),
    ("tax", "s", 1, "festsetzung, steuerpflichtig"),
    ("tax", "s", 1, "lohnsteuer", 0.5),
    ("tax", "w", 0.5, "1099, 1040"),
    ("tax", "s", 1.5, "steuernummer, steuer nr, steuer id, steuerid, steueridentifikationsnummer, "
                      "steuerliche identifikationsnummer, steuer identifikationsnummer, "
                      "identifikationsnummer", 0),

    # ----- Gehalt & Rente -----
    ("payslip", "s", 3, "gehaltsabrechnung, lohnabrechnung, entgeltabrechnung, verdienstabrechnung, "
                        "bezügeabrechnung, bezügemitteilung, gehaltsnachweis, lohnnachweis, "
                        "verdienstnachweis, verdienstbescheinigung, gehaltsbescheinigung, lohnzettel, "
                        "gehaltszettel, payslip, pay slip, payslips, pay stub, paystub, salary slip, "
                        "salary statement, earnings statement, brutto netto bezüge, "
                        "brutto netto abrechnung, renteninformation, rentenauskunft, rentenbescheid, "
                        "versicherungsverlauf, meldebescheinigung zur sozialversicherung, "
                        "sozialversicherungsmeldung, entgeltbescheinigung, lohn und gehaltsabrechnung"),
    ("payslip", "s", 2.5, "bürgergeld, jobcenter, grundsicherung"),
    ("payslip", "s", 2, "gehaltserhöhung, jahresgehalt, bruttogehalt, nettogehalt, grundgehalt, "
                        "gehaltsmitteilung, lohnerhöhung, provisionsabrechnung, payroll, salary, "
                        "arbeitslosengeld, elterngeld, krankengeld, kindergeld, familienkasse, "
                        "deutsche rentenversicherung, pension statement, betriebsrente"),
    ("payslip", "w", 2, "gehalt, riester, rürup"),
    ("payslip", "p", 1.5, "rente"),
    ("payslip", "s", 1.5, "rentenversicherung", 0.7),
    ("payslip", "s", 1.5, "auszahlungsbetrag, nettoverdienst, nettobezug, nettobezüge, bruttobezüge, "
                          "gesamtbrutto, steuerbrutto, sv brutto, gesetzliche abzüge, gesetzliches netto, "
                          "net pay, gross pay"),
    ("payslip", "s", 1, "personalnummer, abrechnungsmonat, arbeitnehmeranteil, arbeitgeberanteil, "
                        "sozialversicherung, kv beitrag, rv beitrag, av beitrag, pv beitrag, "
                        "lohnart, year to date, national insurance, sozialversicherungsbeiträge"),
    ("payslip", "s", 0.5, "steuerklasse, urlaubsanspruch, resturlaub, überstunden"),
    ("payslip", "w", 1, "lohn, pension, ytd"),

    # ----- Gesundheit -----
    ("health", "s", 3, "arztbrief, krankmeldung, krankschreibung, arbeitsunfähigkeitsbescheinigung, "
                       "arbeitsunfähigkeit, impfpass, impfausweis, impfnachweis, impfzertifikat, "
                       "impfbescheinigung, impfbuch, mutterpass, untersuchungsheft, "
                       "kinderuntersuchungsheft, allergiepass, medikationsplan, medikamentenplan, "
                       "entlassungsbrief, entlassbrief, entlassungsbericht, befundbericht, laborbefund, "
                       "heil und kostenplan, heilkostenplan, privatrezept, kassenrezept, "
                       "patientenverfügung, sick note, medical certificate, medical report, "
                       "discharge summary, vaccination certificate, vaccination record, doctor s note, "
                       "doctors note, gesundheitszeugnis, schwerbehindertenausweis, notarzt, "
                       "operationsbericht, op bericht, arztbericht, krankenhausbericht"),
    ("health", "s", 3, "bonusheft, zahnbonusheft, gelbes heft, krankenkassenkarte, versichertenkarte"),
    ("health", "w", 2.5, "egk"),
    ("health", "s", 3, "vaccination card, vaccine card, impfkarte, impfausweis kopie"),
    ("health", "w", 3, "erezept, u heft"),
    ("health", "p", 3, "e rezept"),
    ("health", "s", 2, "brillenpass, arzt, ärztin, ärztlich, befund, diagnose, diagnosis, attest, impfung, röntgen, "
                       "ultraschall, blutbild, blutwerte, laborwerte, blutdruck, krankenhaus, klinik, "
                       "therapie, krankengymnastik, überweisungsschein, medikament, arznei, "
                       "prescription, vaccination, vaccine, hospital, sehtest, hörtest, "
                       "zahnbehandlung, zahnersatz, schwerbehinderung, grad der behinderung, "
                       "pflegegrad, schwangerschaft, vorsorgeuntersuchung, krankheit, erkrankung, "
                       "gesundheitskarte, krankenversichertenkarte, packungsbeilage, beipackzettel, "
                       "rehabilitation, anamnese, lab results"),
    ("health", "p", 2, "impf"),
    ("health", "w", 2, "mrt, mri, ekg, ecg, x ray, xray, reha"),
    ("health", "w", 1, "ct, doctor, dr med"),
    ("health", "p", 1, "patient, medical, symptom"),
    ("health", "s", 1, "apotheke, pharmacy, dosierung, tabletten, gesundheit, behandlung, untersuchung"),
    ("health", "w", 0.7, "health"),
    ("health", "w", 0.5, "praxis"),
    ("health", "s", 0.5, "beschwerden"),
    # "Rezept" zählt nur mit medizinischem Kontext (siehe _ORG_REZEPT_CTX_RE)
    ("health", "s", 2, "rezept, rezepte"),

    # ----- Wohnen & Eigentum -----
    ("property", "s", 3, "nebenkostenabrechnung, betriebskostenabrechnung, heizkostenabrechnung, "
                         "grundbuch, grundbuchauszug, flurkarte, katasterauszug, liegenschaftskarte, "
                         "wohnungsübergabeprotokoll, übergabeprotokoll, wohnungsgeberbestätigung, "
                         "eigentümerversammlung, hausgeldabrechnung, teilungserklärung, "
                         "mietbescheinigung, mieterhöhung, mietschuldenfreiheitsbescheinigung, "
                         "mietkaution, grundsteuerbescheid, grundsteuererklärung"),
    ("property", "s", 2.5, "rundfunkbeitrag, beitragsservice"),
    ("property", "s", 2.5, "nebenkosten, betriebskosten, energieausweis, baugenehmigung, bauantrag, "
                           "grundsteuer, wohnungsbewerbung, mieterselbstauskunft, wohngeld"),
    ("property", "s", 2, "heizkosten, stromabrechnung, gasabrechnung, wasserabrechnung, "
                         "hausverwaltung, hausgeld, wirtschaftsplan, baufinanzierung, hypothek, "
                         "grundschuld, utility bill"),
    ("property", "s", 1.5, "miete, mieter, vermieter, vermietung, kaution", 1),
    ("property", "s", 1.5, "mietwohnung, eigentumswohnung, eigentümer, immobilie, grundriss, lageplan, "
                           "tenancy, landlord, jahresabrechnung, mortgage"),
    ("property", "s", 1, "wohnung, eigentum, abschlagszahlung, tenant"),
    ("property", "w", 1.5, "deed, rent, expose, exposé"),
    ("property", "w", 1, "property"),

    # ----- Rechnungen & Belege -----
    ("invoice", "s", 2.5, "rechnung, rechnungen, invoice, receipt, quittung, kassenbon, kassenzettel, "
                          "kassenbeleg, kaufbeleg, zahlungsbeleg, rechnungsnummer, rechnungsnr, "
                          "rechnungsdatum, invoice number, invoice date, proforma"),
    ("invoice", "s", 1.5, "gutschrift, mahnung, zahlungserinnerung, zahlungsbestätigung, kaufbestätigung, "
                          "bestellbestätigung, auftragsbestätigung, lieferschein, bestellung, "
                          "bestellnummer, bestellnr, garantie, "
                          "gewährleistung, warranty, kostenvoranschlag, kaufnachweis, "
                          "order confirmation, purchase order, payment confirmation, zwischensumme, "
                          "gesamtbetrag, rechnungsbetrag, nettobetrag, bruttobetrag, zahlbar bis, "
                          "zahlungsziel, fällig am, lieferdatum, leistungsdatum, artikelnummer, "
                          "einzelpreis, subtotal, amount due, total due, balance due, belegnummer"),
    ("invoice", "s", 2, "garantieschein, garantiekarte, garantiebeleg, garantiezertifikat"),
    ("invoice", "s", 1, "angebot", 0.5),
    ("invoice", "s", 0.5, "umsatzsteuer, mehrwertsteuer, ust id, ust idnr, ustidnr, steuersatz", 1),
    ("invoice", "w", 1.2, "bill, beleg, belege"),
    ("invoice", "w", 1, "inv, ust, mwst, vat, order"),
    ("invoice", "w", 0.7, "amazon, ebay, zalando, mediamarkt, ikea, otto"),

    # ----- Bank & Finanzen -----
    ("bank", "s", 3, "kontoauszug, kontoauszüge, depotauszug, bank statement, account statement, "
                     "credit card statement, kreditkartenabrechnung, kreditkartenauszug, finanzstatus, "
                     "jahreskontoauszug, kontoabschluss, rechnungsabschluss, depotübersicht, "
                     "vermögensübersicht, wertpapierabrechnung"),
    ("bank", "s", 2, "girokonto, tagesgeld, festgeld, sparkonto, sparbuch, kontoeröffnung, kontostand, "
                     "kontoumsätze, umsatzübersicht, kontoübersicht, kreditkarte, dauerauftrag, "
                     "lastschriftmandat, sepa mandat, darlehen, tilgungsplan, wertpapier, dividende, "
                     "brokerage, sparplan, zinsbescheinigung, alter kontostand, neuer kontostand, "
                     "überweisungsträger, deutsche bank, ing diba, trade republic, scalable capital"),
    ("bank", "s", 1.5, "buchungstag, wertstellung, überweisung, kryptowährung"),
    ("bank", "p", 1.5, "depot"),
    ("bank", "p", 1, "kredit, aktie"),
    ("bank", "w", 1.5, "etf, etfs"),
    ("bank", "s", 3, "bonitätsauskunft, schufa auskunft, schufaauskunft"),
    ("bank", "w", 2, "schufa"),
    ("bank", "w", 1, "bank, statement, sparkasse, volksbank, raiffeisenbank, commerzbank, postbank, dkb, "
                     "comdirect, consorsbank, n26, targobank, hypovereinsbank, santander, revolut, "
                     "onvista, flatex, smartbroker, bitcoin, crypto, paypal, saldo"),
    ("bank", "w", 1, "iban, bic, sepa", 0.3),
    ("bank", "s", 0.5, "kontoinhaber, verwendungszweck"),

    # ----- Fahrzeug -----
    ("vehicle", "s", 3, "konformitätsbescheinigung, coc papiere, coc dokument"),
    ("vehicle", "s", 3, "zulassungsbescheinigung, fahrzeugschein, fahrzeugbrief, fahrzeugpapiere, "
                        "hauptuntersuchung, abgasuntersuchung, kraftfahrzeugsteuer, tüv bericht, "
                        "tüv bescheinigung, bußgeldbescheid, kfz brief, kfz schein, "
                        "vehicle registration, car registration"),
    ("vehicle", "s", 2.5, "werkstattrechnung, tankquittung, tankbeleg"),
    ("vehicle", "s", 1.5, "fahrzeug, kraftfahrzeug, werkstatt, inspektion, reifenwechsel, ölwechsel, "
                          "autohaus, autokauf, gebrauchtwagen, neuwagen, bußgeld, strafzettel, "
                          "verwarnungsgeld, fahrtenbuch, leasingrate, vehicle"),
    ("vehicle", "s", 0.7, "kennzeichen, zulassung"),
    ("vehicle", "w", 2, "tüv, tuv, dekra"),
    ("vehicle", "w", 1.5, "kfz, pkw, adac"),
    ("vehicle", "w", 1, "auto, reifen, motorrad, wohnmobil"),
    ("vehicle", "w", 0.7, "car, bmw, vw, volkswagen, audi, opel, ford, toyota, mercedes, skoda, "
                          "renault, tesla, hyundai, kia, fiat, peugeot"),

    # ----- Studium & Schule -----
    ("education", "s", 3, "immatrikulationsbescheinigung, studienbescheinigung, immatrikulation, "
                          "exmatrikulation, exmatrikulationsbescheinigung, bafög, notenspiegel, "
                          "notenübersicht, leistungsübersicht, bachelorarbeit, masterarbeit, "
                          "diplomarbeit, seminararbeit, abschlussarbeit, studienarbeit, dissertation, "
                          "doktorarbeit, zulassungsbescheid, schulbescheinigung, semesterbeitrag, "
                          "semesterticket, modulhandbuch, studienordnung, prüfungsordnung, "
                          "certificate of enrollment, certificate of enrolment, enrollment certificate, "
                          "enrolment certificate"),
    ("education", "w", 3, "thesis"),
    ("education", "s", 2, "hausarbeit, studienplatz, matrikelnummer, praktikumsbericht, praxissemester, "
                          "praxisbericht, lernzettel, mitschrift, vorlesungsskript, übungsblatt, "
                          "übungsaufgaben, aufgabenblatt, hausaufgabe, exercise sheet, problem set, "
                          "syllabus, berichtsheft, ausbildungsnachweis, projektarbeit, "
                          "integralrechnung, differentialrechnung, wahrscheinlichkeitsrechnung, "
                          "vektorrechnung, bruchrechnung, prozentrechnung, zinsrechnung, "
                          "kostenrechnung, rechnungswesen, abgabetermin, abgabe bis, aufgabe 1, "
                          "aufgabe 2"),
    ("education", "s", 1.5, "studium, studien, hochschule, universität, fachhochschule, university, "
                            "vorlesung, lecture, klausur, tutorium, stundenplan, gymnasium, "
                            "realschule, grundschule, berufsschule, gesamtschule, lehrplan, "
                            "homework, assignment, studiengang, studierende, lehrstuhl, nachhilfe, "
                            "abschlussprüfung, transcript"),
    ("education", "s", 1, "prüfung, schule, ausbildung, praktikum, semester, professor"),
    ("education", "w", 1.5, "uni, ects, exam, exams, seminar"),
    ("education", "p", 1, "skript, student, modul"),
    ("education", "w", 0.7, "bachelor, master, prof, kurs, course"),

    # ----- Arbeit -----
    ("work", "s", 2.5, "reisekostenabrechnung, spesenabrechnung, stundenzettel, stundennachweis, "
                       "arbeitszeitnachweis, zeiterfassung, timesheet, urlaubsantrag, "
                       "arbeitgeberbescheinigung, arbeitsbescheinigung, zielvereinbarung, "
                       "mitarbeitergespräch, jahresgespräch, personalgespräch, dienstplan, "
                       "schichtplan, dienstreise, dienstreiseantrag, geschäftsreise, business trip, "
                       "expense report, besprechungsprotokoll, meeting notes, meeting minutes, "
                       "sitzungsprotokoll, abmahnung"),
    ("work", "s", 2, "reisekosten"),
    ("work", "s", 1.5, "onboarding, elternzeit, quartalsbericht, jahresbericht, monatsbericht, "
                       "wochenbericht, statusbericht, spesen"),
    ("work", "s", 1, "besprechung, agenda, workshop, schulung, weiterbildung, fortbildung, betriebsrat, "
                     "homeoffice, home office, roadmap, kundenpräsentation"),
    ("work", "s", 1, "arbeitgeber", 0.5),
    ("work", "w", 1, "meeting, okr, kpi, kpis"),
    ("work", "s", 0.6, "projekt, präsentation, presentation, protokoll"),
    ("work", "s", 0.5, "bericht, konzept, budget"),
    ("work", "w", 0.5, "report, memo"),

    # ----- Wissenschaft -----
    ("science", "s", 2.5, "biorxiv, medrxiv, arxiv, peer review, rebuttal, lab notebook, laborbuch, "
                          "versuchsprotokoll, laborprotokoll, lab protocol, impact factor"),
    ("science", "p", 2, "manuscript, manuskript, preprint, supplementary"),
    ("science", "s", 1.5, "proceedings, symposium, publication, publikation, western blot, plasmid, "
                          "sequenzierung, sequencing, zellkultur, cell culture, antibody, antikörper, "
                          "gene expression, drittmittel, forschungsantrag, research proposal, "
                          "elsevier, springer"),
    ("science", "w", 2, "et al, doi"),
    ("science", "w", 1.5, "plos, ieee, pcr, qpcr, elisa, reviewer, reviewers"),
    ("science", "s", 1, "forschung, hypothese, hypothesis, assay, dataset, mikroskop, microscop"),
    ("science", "p", 1, "journal, conference, konferenz, research, experiment, protein"),
    ("science", "w", 1, "paper, papers, abstract, nature, poster, grant, dfg, supplement, primer, "
                        "acm, wiley, citation, studie"),
    ("science", "w", 0.5, "science, cell, introduction, methods, discussion, references, figure, fig, "
                          "results, conclusion, review"),

    # ----- Reisen & Tickets -----
    ("travel", "s", 3, "boarding pass, boardingpass, bordkarte, flugticket, flugbuchung, "
                       "flugbestätigung, bahnticket, bahnfahrkarte, zugticket, hotelbuchung, "
                       "hotelreservierung, hotelbestätigung, reisebestätigung, reiseunterlagen, "
                       "reiseplan, reiseroute, reiseverlauf, buchungsbestätigung, onlineticket, "
                       "online ticket, itinerary, flight confirmation, booking confirmation, "
                       "hotel reservation, mietwagen, car rental, rental car, ferienwohnung, airbnb, "
                       "eintrittskarte, konzertkarte, konzertticket, festivalticket, fahrkarte, "
                       "bahncard, deutschlandticket, pauschalreise, rundreise, reisebüro, "
                       "reisedokumente, travel documents"),
    ("travel", "w", 3, "eticket"),
    ("travel", "p", 3, "e ticket"),
    ("travel", "p", 1.5, "ticket, hotel, booking"),
    ("travel", "s", 1.5, "flug, packliste, deutsche bahn, urlaubsreise, reiseführer"),
    ("travel", "w", 1.5, "flight, flights, fewo, lufthansa, eurowings, ryanair, easyjet, condor, tuifly, "
                         "expedia, flixbus"),
    ("travel", "p", 1, "reise"),
    ("travel", "s", 1, "reservierung, gepäck, abflug, sitzplatz, departure"),
    ("travel", "w", 1, "reservation, check in, baggage, tui, sbb, öbb"),
    ("travel", "s", 0.7, "ankunft, arrival"),
    ("travel", "w", 0.7, "gate, travel, db"),
    ("travel", "s", 0.5, "urlaub, ausflug, buchung"),
    ("travel", "w", 0.5, "trip, holiday, holidays, vacation, sightseeing"),

    # ----- Anleitungen -----
    ("manual", "s", 3, "bedienungsanleitung, gebrauchsanweisung, gebrauchsanleitung, betriebsanleitung, "
                       "benutzerhandbuch, bedienungshandbuch, montageanleitung, aufbauanleitung, "
                       "installationsanleitung, kurzanleitung, schnellstartanleitung, user manual, "
                       "user guide, owner s manual, owners manual, instruction manual, "
                       "operating instructions, installation guide, quick start guide, "
                       "quickstart guide, datenblatt, datasheet, data sheet, produktdatenblatt, "
                       "sicherheitsdatenblatt"),
    ("manual", "s", 2, "anleitung, handbuch, quick start, quickstart, spec sheet, sicherheitshinweise, "
                       "safety instructions, getting started, pflegehinweise, specifications"),
    ("manual", "w", 2, "manual, manuals"),
    ("manual", "s", 1, "technische daten"),
    ("manual", "w", 1, "instructions, tutorial"),
    ("manual", "w", 0.7, "howto, how to, faq"),

    # ----- Sperrbegriffe (verbrauchen Text, zählen nichts) -----
    # "erzeugnis" nur am Wortanfang bzw. in bekannten Komposita sperren – sonst wird
    # "Masterzeugnis" (mast+erzeugnis) verschluckt
    ("", "p", 0, "erzeugnis, erzeugnisse"),
    ("", "s", 0, "milcherzeugnis, fleischerzeugnis, agrarerzeugnis, naturerzeugnis, druckerzeugnis, "
                 "tabakerzeugnis, industrieerzeugnis, landwirtschaftserzeugnis, backerzeugnis, "
                 "obsterzeugnis, holzerzeugnis, stahlerzeugnis"),
    # im Text nur Sperrbegriff (Gehaltsabrechnung), im Dateinamen aber echte Belege
    ("tax", "s", 2, "kirchensteuer", 0),
    ("insurance", "s", 2, "pflegeversicherung, arbeitslosenversicherung", 0),
    ("", "s", 0, "abrechnung, abrechnungen, berechnung, verrechnung, hochrechnung, umrechnung, "
                 "terminvereinbarung, schwarz, preise, kreis, wettbewerb, garantiert, "
                 "gesteuert, steuerung, steuerbar, steuergerät, steuereinheit, steuerrad, "
                 "steuerelement, steuerkette, überprüfung, vertragen, geburtstag, aktiengesellschaft, "
                 "eigentumsvorbehalt, unser eigentum, datenschutz, impressum, widerrufsbelehrung, "
                 "widerrufsrecht, widerrufsformular, solidaritätszuschlag, "
                 "support ticket, ticketsystem, police officer, police car, police station, "
                 "flugmodus, kochrezept, backrezept, tischreservierung, dipl ing, entscheidung, bescheinigung, "
                 "bewertung"),
    ("", "w", 0, "video id, user id, kunden id, order id, apple id, session id, client id, "
                 "transaction id, id list, id liste, ids"),
)

# Groß-/Kleinschreibung zählt (nur im Dateinamen): sonst zu mehrdeutig
_ORG_CASE_TOKENS = {"ToR": ("certificate", 2.5), "AU": ("health", 2.5), "eAU": ("health", 3.0),
                    "HU": ("vehicle", 2.0), "BU": ("insurance", 2.0), "KV": ("insurance", 1.5),
                    "CT": ("health", 1.0), "ESt": ("tax", 2.5), "EStE": ("tax", 3.0),
                    "eGK": ("health", 2.5)}
_ORG_CASE_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(sorted(_ORG_CASE_TOKENS, key=len, reverse=True))
                          + r")(?![A-Za-z0-9])")

# medizinischer Kontext für "Rezept"
_ORG_REZEPT_KEY = "rezept"
_ORG_REZEPT_CTX_RE = re.compile(
    r"(?<![a-z0-9])(?:arzt|aerzt|praxis|medikament|apothek|patient|dosierung|tablette|mg|pzn|"
    r"verordnung|krankenkasse|dr med|pharma|kapseln|salbe|einnahme|wirkstoff|hausarzt)"
    r"|(?:arzt|aerztin|apotheke|medikament|tabletten|ibuprofen|paracetamol|antibiotik|amoxicillin|"
    r"novalgin|cortison|kortison|insulin|tropfen)|\d ?mg(?![a-z])")

# Chat-Export (WhatsApp/Telegram ...): Text zählt dann nicht
_ORG_CHAT_LINE_RE = re.compile(
    r"^\[?\d{1,2}[./]\d{1,2}[./]\d{2,4},?\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[ap]\.?m\.?)?\]?\s*(?:-\s*)?"
    r"[^:\n]{1,40}:", re.M | re.I)
# Dateiname eines Chat-Exports ("WhatsApp Chat mit Anna.txt") -> gar nicht einordnen.
# Ein bloßes "WhatsApp" im Namen ("Personalausweis WhatsApp.jpg") verwirft nur den Text.
_ORG_CHAT_NAME_RE = re.compile(r"(?<![a-z0-9])(?:whatsapp chat|telegram chat|signal chat|chat mit|"
                               r"chat with|chatverlauf|chat export|chat history)(?![a-z0-9])")
_ORG_CHAT_APP_RE = re.compile(r"(?<![a-z0-9])(?:whatsapp|telegram)(?![a-z0-9])")

_ORG_EXT_RE = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,5}$")


# ---------------------------------------------------------------------------
# Aufbau des Trie-Regex (einmal beim Import)
# ---------------------------------------------------------------------------
class OrgLexTerm(object):
    __slots__ = ("key", "cat", "nw", "tw", "mode", "display")

    def __init__(self, key, cat, nw, tw, mode, display):
        self.key = key
        self.cat = cat
        self.nw = nw
        self.tw = tw
        self.mode = mode
        self.display = display


def _org_term_variants(term: str) -> list:
    """Normalform + Variante ohne Umlaut-Transkription (Fuhrerschein statt Führerschein)."""
    out = [org_normalize(term)]
    plain = _ORG_NON_ALNUM_RE.sub(" ", term.lower().translate(_ORG_PLAIN_TABLE)).strip()
    if plain != out[0] and len(plain) >= 5:
        out.append(plain)
    return [v for v in out if v]


def _org_build_lexicon():
    terms = {}
    conflicts = []
    for entry in _ORG_LEX_SRC:
        cat, mode, nw, words = entry[0], entry[1], float(entry[2]), entry[3]
        tw = float(entry[4]) if len(entry) > 4 else nw
        for raw in words.split(","):
            raw = raw.strip()
            if not raw:
                continue
            for key in _org_term_variants(raw):
                if key in terms:
                    old = terms[key]
                    if (old.cat, old.mode) != (cat, mode) or old.nw != nw:
                        conflicts.append(key)
                    continue
                terms[key] = OrgLexTerm(key, cat, nw, tw, mode, raw)
    return terms, conflicts


def _org_trie_pattern(terms: dict) -> str:
    """Erzeugt aus allen Begriffen einen Trie-Regex (längster Treffer zuerst)."""
    root = {}
    end = ""          # Schlüssel für "Begriff endet hier"
    for key, t in terms.items():
        node = root
        for ch in key:
            node = node.setdefault(ch, {})
        if t.mode == "s":
            node[end] = ""
        else:
            # Wortanfang per fester Lookbehind-Breite am Blatt prüfen
            suffix = "(?<![a-z0-9]" + re.escape(key) + ")"
            if t.mode == "w":
                suffix += "(?![a-z0-9])"
            node[end] = suffix

    def gen(node):
        suffix = node.get(end)
        chars = sorted(k for k in node if k != end)
        if not chars:
            return suffix or ""
        alts = [re.escape(c) + gen(node[c]) for c in chars]
        body = alts[0] if len(alts) == 1 else "(?:" + "|".join(alts) + ")"
        if suffix is None:
            return body
        if suffix == "":
            return "(?:" + body + ")?"
        return "(?:" + body + "|" + suffix + ")"

    return gen(root)


_ORG_LEX_TERMS, _ORG_LEX_CONFLICTS = _org_build_lexicon()
_ORG_LEX_RE = re.compile(_org_trie_pattern(_ORG_LEX_TERMS))
_ORG_CAT_ORDER = {c: i for i, c in enumerate(ORG_LEX_CATEGORIES)}


# ---------------------------------------------------------------------------
# Jahreserkennung
# ---------------------------------------------------------------------------
_ORG_YEAR_ANCHOR_RE = re.compile(
    r"(?:veranlagungszeitraum|veranlagungsjahr|steuerjahr|kalenderjahr|wirtschaftsjahr|"
    r"geschaeftsjahr|abrechnungsjahr|abrechnungszeitraum|tax\s+year|fiscal\s+year|financial\s+year|"
    r"f(?:ue|u)r\s+das\s+(?:kalender|steuer|wirtschafts)?jahr|"
    r"for\s+the\s+(?:tax\s+|fiscal\s+|calendar\s+)?year|bescheid\s+f(?:ue|u)r|"
    r"einkommensteuer(?:erkl(?:ae|a)rung|bescheid)?|steuererkl(?:ae|a)rung|steuerbescheid|"
    r"lohnsteuerbescheinigung|jahressteuerbescheinigung|steuerbescheinigung)"
    # (?![.,]\d): "Einkommensteuer 2015,00 €" ist ein Betrag, kein Jahr
    r"[^\d\n]{0,25}?(?:\d{1,2}\.\s?\d{1,2}\.\s?)?((?:19|20)\d\d)(?!\d)(?![.,]\d)")
_ORG_MONTHS = (r"januar|jaenner|jan|februar|feb|maerz|marz|mar|march|april|apr|mai|may|juni|june|jun|"
               r"juli|july|jul|august|aug|september|sept|sep|oktober|october|okt|oct|november|nov|"
               r"dezember|december|dez|dec")
# Text: zuerst alle vierstelligen Jahreskandidaten finden (selten -> schnell), dann den
# Kontext links/rechts prüfen (Datum dd.mm.yyyy, yyyy-mm-dd, mm/yyyy, "März 2023", "2023/24").
_ORG_YEAR4_RE = re.compile(r"(?:19|20)\d\d")
_ORG_LEFT_DMY_RE = re.compile(r"(?:^|[^\d])(\d{1,2})([./-])\s?(\d{1,2})\2\s?$")
_ORG_LEFT_MY_RE = re.compile(r"(?:^|[^\d.,])(\d{1,2})[./]$")
_ORG_LEFT_MONTH_RE = re.compile(r"(?:^|[^a-z])(?:" + _ORG_MONTHS + r")\.?\s*"
                                r"(?:\d{1,2}(?:st|nd|rd|th)?\.?,?\s*)?$")
_ORG_RIGHT_YMD_RE = re.compile(r"([./-])(\d{1,2})\1(\d{1,2})(?!\d)")
_ORG_RIGHT_YY_RE = re.compile(r"/(\d\d|\d{4})(?![\d/.])")
_ORG_DMYY_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d\d)(?![\d.])")
_ORG_NAME_YEAR_RE = re.compile(
    r"(?<!\d)(?:(?P<a>(?:19|20)\d\d)(?:(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])?)?"
    r"|(?:0[1-9]|[12]\d|3[01])(?:0[1-9]|1[0-2])(?P<b>(?:19|20)\d\d))(?!\d)")
_ORG_NAME_MMYY_RE = re.compile(r"(?<![\d])(?:0[1-9]|1[0-2])[._ -](\d\d)(?![\d])")
_ORG_ANCHOR_HINTS = ("jahr", "year", "zeitraum", "bescheid", "steuer")


def _org_year_ok(y: int, now=None) -> bool:
    return 1990 <= y <= (now or time.localtime().tm_year) + 1


def _org_text_years(t: str, now: int) -> dict:
    """Zählt Jahre aus Datumsangaben im (gefalteten) Text; ohne Datum: freie Jahreszahlen."""
    dates = {}
    bare = {}
    n = len(t)
    for m in _ORG_YEAR4_RE.finditer(t):
        s, e = m.start(), m.end()
        if (s and t[s - 1].isdigit()) or (e < n and t[e].isdigit()):
            continue
        y = int(m.group())
        if not _org_year_ok(y, now):
            continue
        left = t[max(0, s - 24):s]
        right = t[e:e + 6]
        is_date = False
        lm = _ORG_LEFT_DMY_RE.search(left[-10:])
        if lm and 1 <= int(lm.group(1)) <= 31 and 1 <= int(lm.group(3)) <= 12:
            is_date = True
        elif _ORG_LEFT_MY_RE.search(left[-4:]) and 1 <= int(_ORG_LEFT_MY_RE.search(left[-4:]).group(1)) <= 12:
            is_date = True
        elif _ORG_LEFT_MONTH_RE.search(left):
            is_date = True
        else:
            rm = _ORG_RIGHT_YMD_RE.match(right)
            if rm and 1 <= int(rm.group(2)) <= 12 and 1 <= int(rm.group(3)) <= 31:
                is_date = True
            else:
                rm = _ORG_RIGHT_YY_RE.match(right)
                if rm and int(rm.group(1)) % 100 == (y + 1) % 100:
                    is_date = True
        if is_date:
            dates[y] = dates.get(y, 0) + 1
        elif not ((s and t[s - 1] in ".,/-") or (e + 1 < n and t[e] in ".," and t[e + 1].isdigit())):
            bare[y] = bare.get(y, 0) + 1
    if "." in t:
        for m in _ORG_DMYY_RE.finditer(t):
            s = m.start()
            if s and (t[s - 1].isdigit() or t[s - 1] == "."):
                continue
            if 1 <= int(m.group(1)) <= 31 and 1 <= int(m.group(2)) <= 12:
                y = 2000 + int(m.group(3))
                if _org_year_ok(y, now):
                    dates[y] = dates.get(y, 0) + 1
    return dates or bare


def _org_detect_year_folded(fname: str, ftext: str):
    now = time.localtime().tm_year
    # 1) Anker ("Veranlagungszeitraum 2022", "tax year 2022", "Bescheid für 2022")
    for src in (fname, ftext):
        if src and any(h in src for h in _ORG_ANCHOR_HINTS):
            for m in _ORG_YEAR_ANCHOR_RE.finditer(src):
                y = int(m.group(1))
                if _org_year_ok(y, now):
                    return y
    # 2) Dateiname
    for m in _ORG_NAME_YEAR_RE.finditer(fname):
        y = int(m.group("a") or m.group("b"))
        if _org_year_ok(y, now):
            return y
    for m in _ORG_NAME_MMYY_RE.finditer(fname):
        y = 2000 + int(m.group(1))
        if _org_year_ok(y, now):
            return y
    # 3) häufigstes Jahr aus Datumsangaben im Text (Gleichstand: das spätere)
    if ftext:
        counts = _org_text_years(ftext, now)
        if counts:
            return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
    return None


def org_detect_year(name: str, text: str = ""):
    """Bezugsjahr eines Dokuments (1990..aktuelles Jahr+1) oder None.
    Reihenfolge: Anker wie "Veranlagungszeitraum 2022"/"tax year 2022" > Jahr im
    Dateinamen > häufigstes plausibles Jahr aus Datumsangaben im Text > freie Jahreszahl."""
    try:
        # wie org_classify_document: auch Path-Objekte, nur der Dateiname zählt
        name = _org_to_str(name)
        name = os.path.basename(name.replace("\\", "/")) or name
        text = text if isinstance(text, str) else ""
        if text and "-" in text and "\n" in text:
            text = _ORG_HYPHEN_BREAK_RE.sub("", text[:ORG_LEX_MAX_TEXT + 200])
        fname = _org_fold(_ORG_EXT_RE.sub("", name))
        ftext = _org_fold(text[:ORG_LEX_MAX_TEXT]) if text else ""
        return _org_detect_year_folded(fname, ftext)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Klassifikation
# ---------------------------------------------------------------------------
def _org_scan(norm: str) -> dict:
    hits = {}
    for m in _ORG_LEX_RE.finditer(norm):
        k = m.group()
        hits[k] = hits.get(k, 0) + 1
    return hits


def _org_saturate(s: float) -> float:
    """Text-Evidenz: bis 6 linear, darüber gedämpft, nie über ORG_LEX_TEXT_CAP
    (bleibt damit unter einem starken Namensbegriff = 9)."""
    if s <= 6.0:
        return s
    room = ORG_LEX_TEXT_CAP - 6.0
    return 6.0 + room * (1.0 - math.exp(-(s - 6.0) / room))


def _org_is_chat_text(text: str) -> bool:
    """Text sieht aus wie ein Chat-Export: mind. 3 Zeilen "12.03.23, 14:22 - Name: ..." und
    diese machen mind. zwei Drittel der Zeilen aus (ein Kontoauszug mit Uhrzeiten zählt nicht)."""
    if not text:
        return False
    head = text[:4000]
    n = 0
    for _ in _ORG_CHAT_LINE_RE.finditer(head):
        n += 1
    if n < 3:
        return False
    lines = sum(1 for ln in head.split("\n") if ln.strip())
    return n * 3 >= lines * 2


def org_classify_document(name: str, text: str = "", kind: str = "document") -> tuple:
    """Ordnet ein Dokument einer Kategorie aus ORG_CATEGORIES zu.
    Rückgabe: (kategorie oder None, sicherheit 0..1, getroffene begriffe, jahr oder None)."""
    try:
        name = _org_to_str(name)
        text = text if isinstance(text, str) else ""
        kind = (kind or "document").lower() if isinstance(kind, str) else "document"
        name = os.path.basename(name.replace("\\", "/")) or name
        stem = _ORG_EXT_RE.sub("", name) or name
        if text and "-" in text and "\n" in text:
            text = _ORG_HYPHEN_BREAK_RE.sub("", text[:ORG_LEX_MAX_TEXT + 200])
        fname = _org_fold(stem)
        ftext = _org_fold(text[:ORG_LEX_MAX_TEXT]) if text else ""
        year = _org_detect_year_folded(fname, ftext)
        if kind in _ORG_SKIP_KINDS:
            return (None, 0.0, [], year)
        media = kind in _ORG_MEDIA_KINDS

        name_norm = _ORG_NON_ALNUM_RE.sub(" ", fname).strip()
        # Chat-Export: Name wie "WhatsApp Chat mit Anna" -> nichts; Chat-Text (oder nur
        # "WhatsApp" im Namen) -> nur der Text zählt nicht, der Dateiname weiterhin
        # ("Lebenslauf per WhatsApp.pdf" bleibt ein Lebenslauf).
        if _ORG_CHAT_NAME_RE.search(name_norm):
            return (None, 0.0, [], year)
        if ftext and (_ORG_CHAT_APP_RE.search(name_norm) or _org_is_chat_text(text)):
            ftext = ""
        media_event = bool(media and _ORG_MEDIA_EVENT_RE.search(name_norm))

        # --- Dateiname (auch mit getrennten Binnen-Großbuchstaben: "TranscriptOfRecords") ---
        name_hits = _org_scan(name_norm)
        camel = org_normalize(_ORG_CAMEL_RE.sub(" ", stem))
        if camel != name_norm:
            for k in _org_scan(camel):
                name_hits.setdefault(k, 1)
        # zusammengezogene Form: "Gehalts-Abrechnung", "Lebens Lauf", "Steuer_Bescheid".
        # Nur lange Teilwort-Begriffe, die wirklich über eine Trennstelle reichen.
        if " " in name_norm:
            tokens = name_norm.split()
            for k in _org_scan(name_norm.replace(" ", "")):
                if k in name_hits:
                    continue
                t = _ORG_LEX_TERMS[k]
                kj = k.replace(" ", "")
                if t.mode != "s" or len(kj) < 8 or any(kj in tok for tok in tokens):
                    continue
                name_hits[k] = 1
        # --- Text ---
        text_norm = _ORG_NON_ALNUM_RE.sub(" ", ftext).strip() if ftext else ""
        text_hits = _org_scan(text_norm) if text_norm else {}
        # Begriffe in der Überschrift (Textanfang) zählen doppelt bzw. dreifach (Dokumenttitel)
        head_hits = {}
        if text_norm:
            head = " ".join(ln for ln in ftext[:ORG_LEX_HEAD * 3].split("\n", 3)[:3] if ln.strip())
            head_hits = _org_scan(_ORG_NON_ALNUM_RE.sub(" ", head[:ORG_LEX_HEAD]))

        # Rezept nur mit medizinischem Kontext
        rez_ok = None

        def rezept_ok():
            return bool(_ORG_REZEPT_CTX_RE.search(name_norm) or
                        (text_norm and _ORG_REZEPT_CTX_RE.search(text_norm)))

        name_score = {}
        name_terms = {}
        for k in name_hits:
            t = _ORG_LEX_TERMS[k]
            if not t.cat or t.nw <= 0:
                continue
            if media:
                if t.nw < ORG_LEX_MEDIA_MIN or k in _ORG_MEDIA_DENY:
                    continue
                # Foto eines Ereignisses: nur echte Ausweis-/Pass-Begriffe zählen
                if media_event and not (t.cat in ("id", "health") and t.nw >= 3):
                    continue
            if t.display.startswith(_ORG_REZEPT_KEY) and t.cat == "health":
                if rez_ok is None:
                    rez_ok = rezept_ok()
                if not rez_ok:
                    continue
            w = t.nw * ORG_LEX_NAME_FACTOR
            name_score[t.cat] = name_score.get(t.cat, 0.0) + w
            name_terms.setdefault(t.cat, []).append((w, t.display))
        for m in _ORG_CASE_RE.finditer(stem):
            cat, w = _ORG_CASE_TOKENS[m.group(1)]
            if media and (w < ORG_LEX_MEDIA_MIN or media_event):
                continue
            w *= ORG_LEX_NAME_FACTOR
            name_score[cat] = name_score.get(cat, 0.0) + w
            name_terms.setdefault(cat, []).append((w, m.group(1)))

        text_raw = {}
        text_terms = {}
        for k, cnt in text_hits.items():
            t = _ORG_LEX_TERMS[k]
            if not t.cat or t.tw <= 0:
                continue
            if t.display.startswith(_ORG_REZEPT_KEY) and t.cat == "health":
                if rez_ok is None:
                    rez_ok = rezept_ok()
                if not rez_ok:
                    continue
            w = t.tw * (1.0 + 0.2 * min(cnt - 1, 3))
            if k in head_hits:
                w += t.tw * (2.0 if t.tw >= ORG_LEX_STRONG else 1.0)
            d = text_raw.setdefault(t.cat, [0.0, 0, False])
            d[0] += w
            d[1] += 1
            if t.tw >= ORG_LEX_STRONG:
                d[2] = True
            text_terms.setdefault(t.cat, []).append((w, t.display))

        totals = dict(name_score)
        for cat, (s, distinct, strong) in text_raw.items():
            if distinct >= 2 or strong or cat in name_score:
                totals[cat] = totals.get(cat, 0.0) + _org_saturate(s)
        if not totals:
            return (None, 0.0, [], year)

        ranked = sorted(totals.items(), key=lambda kv: (-kv[1], _ORG_CAT_ORDER.get(kv[0], 99)))
        best_cat, best = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        if best < ORG_LEX_MIN_SCORE or (best_cat not in name_score and best < ORG_LEX_MIN_TEXT):
            return (None, 0.0, [], year)
        evidence = 1.0 - math.exp(-best / 2.5)
        margin = (best - second) / best
        score = evidence * (0.5 + 0.5 * margin)
        if best_cat not in name_score:
            score *= 0.9                      # nur Textbelege: etwas vorsichtiger
        score = round(max(0.0, min(1.0, score)), 3)

        terms = []
        for lst in (name_terms.get(best_cat, []), text_terms.get(best_cat, [])):
            for _w, disp in sorted(lst, key=lambda x: -x[0]):
                if disp not in terms:
                    terms.append(disp)
        return (best_cat, score, terms[:8], year)
    except Exception:
        return (None, 0.0, [], None)


def org_lexicon_reason(category: str, terms: list) -> str:
    """Kurze deutsche Begründung, z. B. 'Steuer erkannt (steuerbescheid, finanzamt)'."""
    try:
        if not category or not isinstance(category, str):
            return ""
        try:
            label = ORG_CATEGORIES.get(category, {}).get("label", category)
        except Exception:
            label = category
        if isinstance(terms, str):
            terms = [terms]
        try:
            shown = ", ".join(str(t) for t in list(terms or [])[:3])
        except Exception:
            shown = ""
        return "%s erkannt (%s)" % (label, shown) if shown else "%s erkannt" % label
    except Exception:
        return ""
