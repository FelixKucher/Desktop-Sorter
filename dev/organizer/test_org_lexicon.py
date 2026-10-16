# Tests für org_lexicon (python test_org_lexicon.py; Exit-Code 0 = bestanden)
from __future__ import annotations

import sys
import time

from org_core import ORG_CATEGORIES
from org_lexicon import (ORG_LEX_CATEGORIES, _ORG_LEX_CONFLICTS, org_classify_document,
                         org_detect_year, org_lexicon_reason, org_normalize)

FAILS = []
CHECKS = [0]


def check(cond, msg):
    CHECKS[0] += 1
    if not cond:
        FAILS.append(msg)


# ---------------------------------------------------------------------------
# Texte (Ausschnitte, wie sie die Textextraktion liefert)
# ---------------------------------------------------------------------------
T_ARBEITSZEUGNIS = """Muster GmbH · Hauptstraße 1 · 80331 München
Herr Felix Kucher, geboren am 12.05.1990, war vom 01.03.2019 bis 31.12.2023 als
Softwareentwickler in unserem Unternehmen tätig. Zu seinen Aufgaben gehörten ...
Herr Kucher verfügt über umfassende Fachkenntnisse. Er erledigte seine Aufgaben stets
zu unserer vollsten Zufriedenheit. Sein Verhalten gegenüber Vorgesetzten und Kollegen
war stets einwandfrei. Herr Kucher verlässt unser Unternehmen auf eigenen Wunsch.
Wir bedauern sein Ausscheiden und wünschen ihm für seine berufliche Zukunft alles Gute.
München, 31.12.2023"""

T_REFERENCE = """To whom it may concern,
It is my pleasure to write this letter for Felix Kucher, who worked as a software engineer
in my team from March 2020 to June 2023. Felix consistently delivered excellent work and
was a reliable colleague. I highly recommend him for any position and recommend him
without reservation.
Sincerely, Jane Doe, Head of Engineering"""

T_LSTB = """Ausdruck der elektronischen Lohnsteuerbescheinigung für 2023
Nachstehende Daten wurden maschinell an die Finanzverwaltung übermittelt.
Dauer des Dienstverhältnisses 01.01.-31.12.
Steuerklasse/Faktor 1
3. Bruttoarbeitslohn einschl. Sachbezüge 52.340,00
4. Einbehaltene Lohnsteuer von 3. 8.123,00
5. Einbehaltener Solidaritätszuschlag 0,00
22. Arbeitgeberanteil zur Rentenversicherung 4.867,62
23. Arbeitnehmeranteil zur Rentenversicherung 4.867,62
25. Arbeitnehmerbeiträge zur gesetzlichen Krankenversicherung 4.255,24
26. Arbeitnehmerbeiträge zur sozialen Pflegeversicherung 863,68
27. Arbeitnehmerbeiträge zur Arbeitslosenversicherung 680,42
Identifikationsnummer 12 345 678 901  Personalnummer 4711"""

T_AMAZON = """Rechnung
Bestellnummer 302-1234567-1234567   Rechnungsdatum 14.02.2024   Lieferdatum 13.02.2024
Rechnungsnummer DE4ABCDEF123
Verkauft von Amazon EU S.à r.l., USt-IdNr. DE814584193
Steuernummer 123/456/78901
Beschreibung Menge Einzelpreis (ohne USt.) USt. % Gesamtpreis
USB-C Kabel 2 m 1 8,39 € 19% 9,99 €
Zwischensumme (ohne USt.) 8,39 €   Gesamtbetrag 9,99 €"""

T_STEUERBESCHEID = """Finanzamt München Abt. III
Steuernummer 143/234/56789
Bescheid für 2022 über Einkommensteuer, Solidaritätszuschlag und Kirchensteuer
Festsetzung
Die Einkommensteuer wird festgesetzt auf 7.432 €. Das zu versteuernde Einkommen beträgt
48.120 €. Erstattung 1.034,00 €. Datum 15.08.2023"""

T_WHATSAPP = """12.03.23, 14:22 - Max: Hast du den Vertrag schon unterschrieben?
12.03.23, 14:23 - Felix: Ja, und die Rechnung vom Arzt ist auch gekommen
12.03.23, 14:25 - Max: Super, dann schick mir den Kontoauszug
12.03.23, 14:30 - Felix: Mach ich, Steuererklärung auch gleich
13.03.23, 09:01 - Max: 👍"""

T_PAYSLIP = """Abrechnung der Brutto/Netto-Bezüge für März 2024
Personalnummer 4711   Steuerklasse 1   Konfession rk
Gehalt 4.500,00   Gesamtbrutto 4.500,00   Steuerbrutto 4.500,00   SV-Brutto 4.500,00
Lohnsteuer 812,33   Solidaritätszuschlag 0,00   Kirchensteuer 64,98
KV-Beitrag 366,75   RV-Beitrag 418,50   AV-Beitrag 58,50   PV-Beitrag 76,50
Nettoverdienst 2.702,44   Auszahlungsbetrag 2.702,44
Steuer-ID 12345678901   Versicherungsnummer 12 120590 K 123"""

T_KONTOAUSZUG = """Sparkasse Musterstadt  Kontoauszug 3/2024  Girokonto IBAN DE12 3456 7890 1234 5678 90
Buchungstag Wertstellung Verwendungszweck Betrag
01.03.2024 01.03.2024 Miete März -850,00
05.03.2024 05.03.2024 Gehalt Muster GmbH 2.702,44
Alter Kontostand 1.234,56   Neuer Kontostand 3.087,00"""

T_MIETVERTRAG = """Mietvertrag für Wohnräume
zwischen Herrn Max Vermieter (Vermieter) und Herrn Felix Kucher (Mieter)
§1 Mietsache: Die Wohnung im 2. OG ... §3 Miete: Die Kaltmiete beträgt 850 €.
§4 Kaution: drei Monatsmieten. §5 Mietdauer: Das Mietverhältnis beginnt am 01.04.2021.
Kündigungsfrist drei Monate. Ort, Datum, Unterschrift Vermieter, Unterschrift Mieter"""

T_ARZTBRIEF = """Praxis Dr. med. Anna Schmidt, Fachärztin für Innere Medizin
Sehr geehrte Frau Kollegin, wir berichten über unseren gemeinsamen Patienten Felix Kucher.
Diagnose: Gastritis. Befund: Sonographie unauffällig. Therapie: Pantoprazol 40 mg 1-0-0.
Mit freundlichen kollegialen Grüßen"""

T_CV = """Felix Kucher
Persönliche Daten: geboren 12.05.1990 in Berlin
Beruflicher Werdegang
2019 – 2023 Softwareentwickler, Muster GmbH
Ausbildung 2010 – 2015 B.Sc. Informatik, TU München
Sprachkenntnisse: Deutsch (Muttersprache), Englisch (fließend)
EDV-Kenntnisse: Python, C++, SQL   Hobbys: Klettern, Fotografie"""

T_COVERLETTER = """Bewerbung als Softwareentwickler – Ihre Stellenanzeige auf StepStone
Sehr geehrte Damen und Herren,
hiermit bewerbe ich mich auf die ausgeschriebene Stelle ... Über eine Einladung zu einem
Vorstellungsgespräch freue ich mich sehr. Mit freundlichen Grüßen Felix Kucher"""

T_PAPER = """Abstract
We report a novel CRISPR-based assay for single-cell gene expression profiling.
Introduction ... Methods: cells were cultured ... Western blot ... qPCR ...
Results ... Discussion ... References 1. Smith J et al. Nature 2021; doi:10.1038/xyz"""

T_MANUAL = """Bedienungsanleitung Waschmaschine WM 8000
Sicherheitshinweise: Lesen Sie diese Anleitung vor der Inbetriebnahme sorgfältig.
Technische Daten: 230 V, 2200 W. Garantiebedingungen siehe Rückseite."""

T_BOARDING = """BOARDING PASS  Lufthansa  LH 1234  MUNICH - LISBON
Passenger KUCHER/FELIX  Seat 23A  Gate G12  Boarding 06:40  Departure 07:15
Booking reference ABC123"""

T_NEBENKOSTEN = """Hausverwaltung Müller GmbH
Nebenkostenabrechnung für den Abrechnungszeitraum 01.01.2023 – 31.12.2023
Mieter: Felix Kucher, Wohnung 2. OG links
Heizkosten 812,00 €, Wasser 210,00 €, Müllabfuhr 95,00 €, Grundsteuer 120,00 €
Ihre Vorauszahlungen 1.100,00 €  Nachzahlung 137,00 €"""

T_VERSICHERUNG = """Versicherungsschein Nr. 123456789
Privat-Haftpflichtversicherung Tarif Komfort
Versicherungsnehmer: Felix Kucher   Versicherungsbeginn 01.01.2022
Jahresbeitrag 59,90 €. Es gelten die beigefügten Versicherungsbedingungen."""

T_COOKING = """Lasagne Rezept für 4 Personen
Zutaten: 500 g Hackfleisch, 1 Zwiebel, 2 EL Tomatenmark, Lasagneplatten, Béchamel
Den Backofen auf 180 °C vorheizen und 45 Minuten backen. Guten Appetit!"""

T_MEDREZEPT = """Rezept – Praxis Dr. med. Weber
Patient: Felix Kucher   Ibuprofen 600 mg, 20 Tabletten, PZN 01234567
Dosierung 1-0-1 nach dem Essen"""

T_IMMA = """Technische Universität München
Immatrikulationsbescheinigung für das Wintersemester 2023/24
Herr Felix Kucher, Matrikelnummer 03712345, ist im Studiengang Informatik (Master) eingeschrieben."""

T_MEETING = """Besprechungsprotokoll Projekt Phoenix 12.04.2024
Teilnehmer: Team Backend. Agenda: 1. Roadmap Q3 2. Budget 3. Sonstiges
Nächstes Meeting: 19.04.2024"""

T_ARBEITSVERTRAG = """Arbeitsvertrag
zwischen der Muster GmbH (Arbeitgeber) und Herrn Felix Kucher (Arbeitnehmer)
§1 Beginn: Das Arbeitsverhältnis beginnt am 01.03.2019. §2 Probezeit sechs Monate.
§5 Vergütung brutto 4.500 € monatlich. §9 Kündigungsfrist gesetzlich."""

T_GROCERY = """Milch, Eier, Brot, Butter, Käse, Tomaten, Nudeln, Kaffee, Äpfel, Bananen"""

T_STORY = """Es war einmal ein kleiner Drache, der wollte fliegen lernen. Jeden Tag übte er auf
der Wiese hinter dem Haus. Seine Freunde lachten, aber er gab nicht auf."""

T_INVOICE_EN = """INVOICE
Invoice number: INV-2024-001   Invoice date: 2024-01-15
Bill to: Felix Kucher   Description: Consulting services   Subtotal: $1,200.00
VAT 0%   Total due: $1,200.00   Payment terms: 14 days"""

T_STEUER_ELSTER = """Übermittlungsprotokoll Einkommensteuererklärung 2023
Transferticket: et1234567890   Steuernummer: 143/234/56789
Ihre Daten wurden am 03.05.2024 erfolgreich an das Finanzamt übermittelt."""

T_EAU = """Arbeitsunfähigkeitsbescheinigung zur Vorlage beim Arbeitgeber
Erstbescheinigung, arbeitsunfähig seit 04.03.2024, voraussichtlich bis 08.03.2024"""

T_FLYER = """Sommerfest im Garten! Grillen, Musik und Spiele für die ganze Familie.
Samstag ab 15 Uhr, bringt gute Laune mit."""

# ---------------------------------------------------------------------------
# Fälle: (Dateiname, Text, Art, erwartete Kategorie oder None)
# ---------------------------------------------------------------------------
D = "document"
I = "image"
CASES = [
    # --- cv ---
    ("Lebenslauf_Felix_Kucher_2024.pdf", "", D, "cv"),
    ("Resume_2023_final.docx", "", D, "cv"),
    ("CV Felix.pdf", "", D, "cv"),
    ("Anschreiben Firma X.docx", "", D, "cv"),
    ("Cover Letter.pdf", "", D, "cv"),
    ("Lebenslauf_final_v2 (1).docx", "", D, "cv"),
    ("Bewerbung_Siemens_2023.pdf", "", D, "cv"),
    ("Bewerbungsunterlagen komplett.pdf", "", D, "cv"),
    ("Motivationsschreiben Master.docx", "", D, "cv"),
    ("Bewerbungsfoto.jpg", "", I, "cv"),
    ("curriculum_vitae_en.pdf", "", D, "cv"),
    ("felix_kucher_cv.pdf", "", D, "cv"),
    ("Initiativbewerbung BMW.docx", "", D, "cv"),
    ("Lebenslauf.pdf", "", D, "cv"),
    ("Lebenslauf_Fuehrungskraft.docx", "", D, "cv"),
    ("Scan_0001.pdf", T_CV, D, "cv"),
    ("Dokument1.docx", T_COVERLETTER, D, "cv"),
    ("Resume - Software Engineer.pdf", "", D, "cv"),
    # --- certificate ---
    ("Zeugnis_Bachelor.pdf", "", D, "certificate"),
    ("Arbeitszeugnis Muster GmbH.pdf", "", D, "certificate"),
    ("Zwischenzeugnis_2022.pdf", "", D, "certificate"),
    ("Abiturzeugnis.pdf", "", D, "certificate"),
    ("Geburtsurkunde.pdf", "", D, "certificate"),
    ("Heiratsurkunde_Kopie.pdf", "", D, "certificate"),
    ("Transcript of Records.pdf", "", D, "certificate"),
    ("TranscriptOfRecords.pdf", "", D, "certificate"),
    ("ToR_Felix.pdf", "", D, "certificate"),
    ("Masterurkunde.pdf", "", D, "certificate"),
    ("Zertifikat_Scrum_Master.pdf", "", D, "certificate"),
    ("TOEFL Score Report.pdf", "", D, "certificate"),
    ("Certificate of Completion.pdf", "", D, "certificate"),
    ("Teilnahmebescheinigung Erste Hilfe.pdf", "", D, "certificate"),
    ("Diplom.pdf", "", D, "certificate"),
    ("Schulzeugnis Klasse 10.pdf", "", D, "certificate"),
    ("Führungszeugnis.pdf", "", D, "certificate"),
    ("Reference Letter Prof Smith.pdf", "", D, "certificate"),
    ("scan_2024_03_12.pdf", T_ARBEITSZEUGNIS, D, "certificate"),
    ("letter.pdf", T_REFERENCE, D, "certificate"),
    ("Zeugnis.jpg", "", I, "certificate"),
    ("Meisterbrief.pdf", "", D, "certificate"),
    # --- id ---
    ("Personalausweis_Scan.jpg", "", I, "id"),
    ("Personalausweis_Scan.pdf", "", D, "id"),
    ("Reisepass.pdf", "", D, "id"),
    ("Führerschein.pdf", "", D, "id"),
    ("Fuehrerschein_Rueckseite.jpg", "", I, "id"),
    ("Fuhrerschein.pdf", "", D, "id"),
    ("Perso Vorderseite.jpg", "", I, "id"),
    ("Passport_Felix.pdf", "", D, "id"),
    ("ID_Card_front.png", "", I, "id"),
    ("Aufenthaltstitel.pdf", "", D, "id"),
    ("SV-Ausweis.pdf", "", D, "id"),
    ("Meldebescheinigung.pdf", "", D, "id"),
    ("Visum USA.pdf", "", D, "id"),
    ("Studentenausweis.jpg", "", I, "id"),
    ("Drivers License.jpg", "", I, "id"),
    # --- contract ---
    ("Arbeitsvertrag_2021.pdf", "", D, "contract"),
    ("Mietvertrag Wohnung.pdf", "", D, "contract"),
    ("Kündigung Fitnessstudio.pdf", "", D, "contract"),
    ("Kaufvertrag Auto.pdf", "", D, "contract"),
    ("Handyvertrag Telekom.pdf", "", D, "contract"),
    ("NDA_signed.pdf", "", D, "contract"),
    ("Aufhebungsvertrag.pdf", "", D, "contract"),
    ("Kuendigungsbestaetigung_Vodafone.pdf", "", D, "contract"),
    ("Vorsorgevollmacht.pdf", "", D, "contract"),
    ("Testament.pdf", "", D, "contract"),
    ("Rental Agreement.pdf", "", D, "contract"),
    ("Employment Contract.pdf", "", D, "contract"),
    ("Darlehensvertrag.pdf", "", D, "contract"),
    ("Stromvertrag Stadtwerke.pdf", "", D, "contract"),
    ("Scan.pdf", T_ARBEITSVERTRAG, D, "contract"),
    ("Dokument.pdf", T_MIETVERTRAG, D, "contract"),
    ("Vertrag.pdf", "", D, "contract"),
    # --- insurance ---
    ("Versicherungsschein Haftpflicht.pdf", "", D, "insurance"),
    ("Police_KFZ.pdf", "", D, "insurance"),
    ("Hausratversicherung_2023.pdf", "", D, "insurance"),
    ("BU Versicherung Antrag.pdf", "", D, "insurance"),
    ("Krankenversicherung Beitragsanpassung.pdf", "", D, "insurance"),
    ("PKV_Beitragsrechnung.pdf", "", D, "insurance"),
    ("Schadensmeldung Wasserschaden.pdf", "", D, "insurance"),
    ("Kfz-Versicherung Allianz.pdf", "", D, "insurance"),
    ("Rechtsschutzversicherung.pdf", "", D, "insurance"),
    ("Insurance Policy.pdf", "", D, "insurance"),
    ("Scan_Versicherung.pdf", T_VERSICHERUNG, D, "insurance"),
    ("AOK Beitragsbescheinigung.pdf", "", D, "insurance"),
    # --- tax ---
    ("Steuerbescheid 2022.pdf", "", D, "tax"),
    ("Einkommensteuererklärung_2023.pdf", "", D, "tax"),
    ("Einkommensteuererklaerung_2023.pdf", "", D, "tax"),
    ("Lohnsteuerbescheinigung 2023.pdf", "", D, "tax"),
    ("Elster_Übermittlungsprotokoll.pdf", "", D, "tax"),
    ("W-2_2023.pdf", "", D, "tax"),
    ("W2 2022.pdf", "", D, "tax"),
    ("P60_2023.pdf", "", D, "tax"),
    ("Form 1099-MISC.pdf", "", D, "tax"),
    ("Tax Return 2022.pdf", "", D, "tax"),
    ("Spendenbescheinigung Rotes Kreuz.pdf", "", D, "tax"),
    ("Steuer 2021 Belege.pdf", "", D, "tax"),
    ("Finanzamt Schreiben.pdf", "", D, "tax"),
    ("LStB_2022.pdf", "", D, "tax"),
    ("Anlage N.pdf", "", D, "tax"),
    ("scan001.pdf", T_LSTB, D, "tax"),
    ("Bescheid.pdf", T_STEUERBESCHEID, D, "tax"),
    ("protokoll.pdf", T_STEUER_ELSTER, D, "tax"),
    ("Jahressteuerbescheinigung DKB 2023.pdf", "", D, "tax"),
    ("Steuer-ID Mitteilung.pdf", "", D, "tax"),
    # --- payslip ---
    ("Gehaltsabrechnung_03_2024.pdf", "", D, "payslip"),
    ("Payslip March 2024.pdf", "", D, "payslip"),
    ("Lohnabrechnung Januar.pdf", "", D, "payslip"),
    ("Entgeltabrechnung 2023-12.pdf", "", D, "payslip"),
    ("Verdienstabrechnung.pdf", "", D, "payslip"),
    ("Rentenversicherung_Renteninformation.pdf", "", D, "payslip"),
    ("Renteninformation 2023.pdf", "", D, "payslip"),
    ("Gehaltserhöhung 2024.pdf", "", D, "payslip"),
    ("Lohnzettel.pdf", "", D, "payslip"),
    ("salary_slip_feb.pdf", "", D, "payslip"),
    ("Elterngeld Bescheid.pdf", "", D, "payslip"),
    ("Dokument_2024.pdf", T_PAYSLIP, D, "payslip"),
    ("Gehaltsabrechnung März.pdf", T_PAYSLIP, D, "payslip"),
    # --- health ---
    ("Arztbrief.pdf", "", D, "health"),
    ("Befund MRT.pdf", "", D, "health"),
    ("Impfpass.pdf", "", D, "health"),
    ("Krankmeldung AU.pdf", "", D, "health"),
    ("AU_Bescheinigung.pdf", "", D, "health"),
    ("eAU_März.pdf", "", D, "health"),
    ("Laborbefund Blutbild.pdf", "", D, "health"),
    ("Impfzertifikat Corona.pdf", "", D, "health"),
    ("Röntgen Knie.jpg", "", I, "health"),
    ("Zahnarzt Heil- und Kostenplan.pdf", "", D, "health"),
    ("Physiotherapie Verordnung.pdf", "", D, "health"),
    ("Medikationsplan.pdf", "", D, "health"),
    ("Mutterpass.pdf", "", D, "health"),
    ("E-Rezept.pdf", "", D, "health"),
    ("Rezept Hausarzt.pdf", "", D, "health"),
    ("Rezept.pdf", T_MEDREZEPT, D, "health"),
    ("Sick Note.pdf", "", D, "health"),
    ("brief.pdf", T_ARZTBRIEF, D, "health"),
    ("scan_au.pdf", T_EAU, D, "health"),
    ("Patientenverfügung.pdf", "", D, "health"),
    ("Krankenhaus Entlassungsbrief.pdf", "", D, "health"),
    # --- property ---
    ("Nebenkostenabrechnung 2023.pdf", "", D, "property"),
    ("Betriebskostenabrechnung_2022.pdf", "", D, "property"),
    ("Heizkostenabrechnung.pdf", "", D, "property"),
    ("Grundbuchauszug.pdf", "", D, "property"),
    ("Wohnungsübergabeprotokoll.pdf", "", D, "property"),
    ("Wohnungsgeberbestätigung.pdf", "", D, "property"),
    ("Mieterhöhung 2024.pdf", "", D, "property"),
    ("Energieausweis Haus.pdf", "", D, "property"),
    ("Grundsteuerbescheid.pdf", "", D, "property"),
    ("Stromabrechnung 2023.pdf", "", D, "property"),
    ("Hausgeldabrechnung WEG.pdf", "", D, "property"),
    ("Abrechnung.pdf", T_NEBENKOSTEN, D, "property"),
    ("Grundriss Wohnung.pdf", "", D, "property"),
    # --- invoice ---
    ("Rechnung_Amazon_12345.pdf", "", D, "invoice"),
    ("Invoice-INV-2024-001.pdf", "", D, "invoice"),
    ("Quittung.jpg", "", I, "invoice"),
    ("Kassenbon Mediamarkt.jpg", "", I, "invoice"),
    ("Garantie.pdf", "", D, "invoice"),
    ("Garantieschein Waschmaschine.pdf", "", D, "invoice"),
    ("Handyrechnung Mai.pdf", "", D, "invoice"),
    ("Receipt_Uber.pdf", "", D, "invoice"),
    ("cvs_receipt.pdf", "", D, "invoice"),
    ("Mahnung.pdf", "", D, "invoice"),
    ("Bestellbestätigung.pdf", "", D, "invoice"),
    ("Kostenvoranschlag Dach.pdf", "", D, "invoice"),
    ("Lieferschein.pdf", "", D, "invoice"),
    ("document.pdf", T_AMAZON, D, "invoice"),
    ("Rechnung.pdf", T_AMAZON, D, "invoice"),
    ("download.pdf", T_INVOICE_EN, D, "invoice"),
    ("Stromrechnung.pdf", "", D, "invoice"),
    ("Arztrechnung.pdf", "", D, "invoice"),
    # --- bank ---
    ("Kontoauszug_2024_01.pdf", "", D, "bank"),
    ("Bank statement January.pdf", "", D, "bank"),
    ("Depotauszug.pdf", "", D, "bank"),
    ("Kreditkartenabrechnung Feb.pdf", "", D, "bank"),
    ("Sparkasse Kontoeröffnung.pdf", "", D, "bank"),
    ("Tagesgeld Zinsen.pdf", "", D, "bank"),
    ("SEPA-Lastschriftmandat.pdf", "", D, "bank"),
    ("Trade Republic Wertpapierabrechnung.pdf", "", D, "bank"),
    ("Kontoauszüge 2023.pdf", "", D, "bank"),
    ("auszug.pdf", T_KONTOAUSZUG, D, "bank"),
    ("Dauerauftrag Miete.pdf", "", D, "bank"),
    # --- vehicle ---
    ("Zulassungsbescheinigung Teil 1.pdf", "", D, "vehicle"),
    ("TÜV Bericht.pdf", "", D, "vehicle"),
    ("TUEV_2023.pdf", "", D, "vehicle"),
    ("Fahrzeugschein.jpg", "", I, "vehicle"),
    ("HU Bericht 2024.pdf", "", D, "vehicle"),
    ("Werkstattrechnung Inspektion.pdf", "", D, "vehicle"),
    ("Bußgeldbescheid.pdf", "", D, "vehicle"),
    ("Fahrtenbuch.xlsx", "", D, "vehicle"),
    ("KFZ Inspektion.pdf", "", D, "vehicle"),
    ("Reifenwechsel Termin.pdf", "", D, "vehicle"),
    # --- education ---
    ("Immatrikulationsbescheinigung.pdf", "", D, "education"),
    ("Notenspiegel.pdf", "", D, "education"),
    ("Bachelorarbeit_final.pdf", "", D, "education"),
    ("Thesis.pdf", "", D, "education"),
    ("Masterarbeit_v3.docx", "", D, "education"),
    ("BAföG Bescheid.pdf", "", D, "education"),
    ("Bafoeg_Antrag.pdf", "", D, "education"),
    ("Übungsblatt 5 Analysis.pdf", "", D, "education"),
    ("Vorlesung 3 Statistik.pdf", "", D, "education"),
    ("Klausur Mathe 2.pdf", "", D, "education"),
    ("Hausarbeit Soziologie.docx", "", D, "education"),
    ("Stundenplan WS23.pdf", "", D, "education"),
    ("Semesterbeitrag.pdf", "", D, "education"),
    ("Modulhandbuch Informatik.pdf", "", D, "education"),
    ("bescheinigung.pdf", T_IMMA, D, "education"),
    ("Integralrechnung Zusammenfassung.pdf", "", D, "education"),
    # --- work ---
    ("Reisekostenabrechnung Mai.xlsx", "", D, "work"),
    ("Stundenzettel_April.xlsx", "", D, "work"),
    ("Besprechungsprotokoll.docx", "", D, "work"),
    ("Urlaubsantrag.pdf", "", D, "work"),
    ("Meeting Notes 2024-04-12.docx", "", D, "work"),
    ("Zielvereinbarung 2024.pdf", "", D, "work"),
    ("Timesheet March.xlsx", "", D, "work"),
    ("notizen.docx", T_MEETING, D, "work"),
    ("Dienstreise Hamburg.pdf", "", D, "work"),
    # --- science ---
    ("Paper_Nature_2023.pdf", "", D, "science"),
    ("manuscript_v4.docx", "", D, "science"),
    ("preprint_biorxiv.pdf", "", D, "science"),
    ("Supplementary Information.pdf", "", D, "science"),
    ("Smith2021.pdf", T_PAPER, D, "science"),
    ("Laborbuch 2023.docx", "", D, "science"),
    ("Rebuttal letter.docx", "", D, "science"),
    ("Poster Konferenz.pptx", "", D, "science"),
    # --- travel ---
    ("Boarding Pass.pdf", "", D, "travel"),
    ("Bahnticket.pdf", "", D, "travel"),
    ("Hotelbuchung.pdf", "", D, "travel"),
    ("Flugticket Lissabon.pdf", "", D, "travel"),
    ("Buchungsbestätigung Booking.com.pdf", "", D, "travel"),
    ("Mietwagen Mallorca.pdf", "", D, "travel"),
    ("eTicket_DB.pdf", "", D, "travel"),
    ("Konzertkarte.pdf", "", D, "travel"),
    ("Itinerary.pdf", "", D, "travel"),
    ("Boarding Pass.png", "", I, "travel"),
    ("pass.pdf", T_BOARDING, D, "travel"),
    ("Reiseunterlagen Kreta.pdf", "", D, "travel"),
    # --- manual ---
    ("Bedienungsanleitung Waschmaschine.pdf", "", D, "manual"),
    ("User Manual.pdf", "", D, "manual"),
    ("Datenblatt.pdf", "", D, "manual"),
    ("Montageanleitung PAX.pdf", "", D, "manual"),
    ("Handbuch Router.pdf", "", D, "manual"),
    ("datasheet_ESP32.pdf", "", D, "manual"),
    ("Quick Start Guide.pdf", "", D, "manual"),
    ("Gebrauchsanweisung.pdf", "", D, "manual"),
    ("WM8000.pdf", T_MANUAL, D, "manual"),

    # --- weitere gemischte Fälle ---
    ("Wohnungsbewerbung Mieterselbstauskunft.pdf", "", D, "property"),
    ("Visa Antrag Indien.pdf", "", D, "id"),
    ("Reisekosten.xlsx", "", D, "work"),
    ("Schufa Auskunft.pdf", "", D, "bank"),
    ("Brillenpass.pdf", "", D, "health"),
    ("Kindergeld Bescheid Familienkasse.pdf", "", D, "payslip"),
    ("Rentenbescheid Oma.pdf", "", D, "payslip"),
    ("Sterbeurkunde Opa.pdf", "", D, "certificate"),
    ("Kfz-Steuerbescheid.pdf", "", D, "tax"),
    ("Mietkautionskonto.pdf", "", D, "property"),
    ("Ausbildungsvertrag.pdf", "", D, "contract"),
    ("Krankenkasse Mitgliedsbescheinigung.pdf", "", D, "insurance"),
    ("Schwerbehindertenausweis.jpg", "", I, "health"),
    ("Garantiekarte.jpg", "", I, "invoice"),
    ("Statistik Vorlesung Skript.pdf", "", D, "education"),
    ("Lernzettel Biochemie.docx", "", D, "education"),
    ("Deutschlandticket Abo.pdf", "", D, "travel"),
    ("Flixbus Ticket Berlin.pdf", "", D, "travel"),
    ("Grundsteuer Erklärung 2022.pdf", "", D, "property"),
    ("Kreditkarte Visa Abrechnung.pdf", "", D, "bank"),
    ("scan.pdf", "Privatärztliche Rechnung nach GOÄ  Patient: Felix Kucher  Diagnose: Bronchitis  "
                 "Rechnungsbetrag 85,40 € zahlbar bis 30.04.2024", D, "invoice"),
    ("scan.pdf", "Aufgabe 1: Berechnen Sie die Ableitung von f(x)=x^2. Aufgabe 2: Lösen Sie die "
                 "Gleichung. Abgabe bis Freitag im Tutorium.", D, "education"),
    ("ticket.pdf", "Online-Ticket Deutsche Bahn  Hinfahrt 12.05.2024 München Hbf - Berlin Hbf ICE 1000 "
                   "Wagen 7 Sitzplatz 45  Bitte Ausweis bereithalten", D, "travel"),
    ("produkt.pdf", "Technisches Datenblatt  Produkt: LED Panel 60x60  Leistung 40 W", D, "manual"),
    # weitere Negativfälle mit Text
    ("newsletter.html", "Hallo Felix, unser Sommer-Sale startet! Jetzt bis zu 50% sparen. Abmelden vom "
                        "Newsletter hier. Datenschutz | Impressum | AGB", D, None),
    ("lorem.txt", "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor.", D, None),
    ("novel.txt", "It was a bright cold day in April, and the clocks were striking thirteen.", D, None),
    ("werbung.pdf", "Jetzt die beste Kfz-Versicherung finden! Vergleichen Sie Tarife und sparen Sie.", D, None),
    ("Tischreservierung Restaurant.pdf", "", D, None),
    ("Projektplan.xlsx", "", D, None),
    ("Weihnachtsgeschenke Ideen.txt", "", D, None),
    ("Passwörter.txt", "", D, None),
    ("Einladung Hochzeit.pdf", "", D, None),
    ("Umzug Checkliste.pdf", "", D, None),
    ("Poster.jpg", "", I, None),
    # --- Negativfälle (erwartet None) ---
    ("IMG_1234.jpg", "", I, None),
    ("notes.txt", "", D, None),
    ("Einkaufsliste.txt", T_GROCERY, D, None),
    ("video_final.mp4", "", "video", None),
    ("Rezept Lasagne.pdf", "", D, None),
    ("Rezept Lasagne.pdf", T_COOKING, D, None),
    ("Australia trip.docx", "", D, None),
    ("Unbenannt.docx", "", D, None),
    ("Dokument1.docx", "", D, None),
    ("Neues Textdokument.txt", "", D, None),
    ("Screenshot 2024-03-01 123456.png", "", I, None),
    ("DSC_0001.JPG", "", I, None),
    ("Präsentation.pptx", "", D, None),
    ("Geburtstagseinladung.docx", "", D, None),
    ("Wallpaper_4k.jpg", "", I, None),
    ("Berechnung Volumen.xlsx", "", D, None),
    ("Preise Vergleich.xlsx", "", D, None),
    ("Kreisdiagramm.png", "", I, None),
    ("Datenbank Export.csv", "", D, None),
    ("Sitzbank bauen.pdf", "", D, None),
    ("Schwarztee Sorten.docx", "", D, None),
    ("Erzeugnisse Liste.pdf", "", D, None),
    ("WhatsApp Chat mit Anna.txt", T_WHATSAPP, D, None),
    ("chat.txt", T_WHATSAPP, D, None),
    ("Spotify Playlist.txt", "", D, None),
    ("Gedichte.docx", T_STORY, D, None),
    ("Todo.md", "", D, None),
    ("Ideen.txt", "", D, None),
    ("Minecraft Mods.zip", "", "archive", None),
    ("Setup.exe", "", "installer", None),
    ("ElsterFormular_Setup.exe", "", "installer", None),
    ("Lieblingsrezepte.docx", T_COOKING, D, None),
    ("Kochbuch.pdf", "", D, None),
    ("Urlaub 2023.jpg", "", I, None),
    ("Auto.jpg", "", I, None),
    ("Hotel Pool.jpg", "", I, None),
    ("Mama und Papa.jpg", "", I, None),
    ("Tor des Monats.docx", "", D, None),
    ("Abrechnung.pdf", "", D, None),
    ("Steuerung Heizung.docx", "", D, None),
    ("Entscheidung.docx", "", D, None),
    ("video_id_list.txt", "", D, None),
    ("README.txt", "", D, None),
    ("license.txt", "", D, None),
    ("Brainstorming.txt", "", D, None),
    ("Sommerfest.docx", T_FLYER, D, None),
    ("Wettbewerbe 2023.docx", "", D, None),
    ("Terminvereinbarung Friseur.pdf", "", D, None),
    ("Wallpaper Auto.png", "", I, None),
    ("cv2_tutorial.py", "", "code", None),
    ("song.mp3", "", "audio", None),
    ("Lohnt sich das.docx", "", D, None),
    ("festgehalten.txt", "", D, None),
    ("Familienfoto.jpg", "", I, None),
    ("Audio Aufnahme 1.m4a", "", "audio", None),
]


def run_cases():
    ok = 0
    wrong = []
    for name, text, kind, exp in CASES:
        cat, score, terms, _y = org_classify_document(name, text, kind)
        if cat == exp:
            ok += 1
        else:
            wrong.append((name, kind, exp, cat, score, terms))
    total = len(CASES)
    acc = ok / total
    print("Klassifikation: %d/%d korrekt = %.1f %%" % (ok, total, acc * 100))
    for w in wrong:
        print("  FALSCH: %r (%s) erwartet=%s bekommen=%s score=%s terms=%s" % w)
    neg = sum(1 for c in CASES if c[3] is None)
    check(total >= 200, "mind. 200 Fälle (%d)" % total)
    check(neg >= 40, "mind. 40 Negativfälle (%d)" % neg)
    check(acc >= 0.95, "Genauigkeit >= 95 %% (%.1f)" % (acc * 100))
    return total


def run_strict():
    """Regeln, die exakt gelten müssen."""
    # Kategorien gültig
    for c in ORG_LEX_CATEGORIES:
        check(c in ORG_CATEGORIES, "Kategorie fehlt in ORG_CATEGORIES: " + c)
    for name, text, kind, exp in CASES:
        if exp is not None:
            check(exp in ORG_CATEGORIES, "Erwartung ungültig " + exp)
    check(not _ORG_LEX_CONFLICTS, "Lexikon-Konflikte: %r" % _ORG_LEX_CONFLICTS[:10])

    def cat(n, t="", k="document"):
        return org_classify_document(n, t, k)[0]

    # Abrechnung-Regeln
    check(cat("Gehaltsabrechnung.pdf") == "payslip", "Gehaltsabrechnung -> payslip")
    check(cat("Lohnabrechnung.pdf") == "payslip", "Lohnabrechnung -> payslip")
    check(cat("Entgeltabrechnung.pdf") == "payslip", "Entgeltabrechnung -> payslip")
    check(cat("Nebenkostenabrechnung.pdf") == "property", "Nebenkostenabrechnung -> property")
    check(cat("Betriebskostenabrechnung.pdf") == "property", "Betriebskosten -> property")
    check(cat("Heizkostenabrechnung.pdf") == "property", "Heizkosten -> property")
    check(cat("Abrechnung.pdf") is None, "Abrechnung allein -> None")
    check(cat("Rechnung.pdf") == "invoice", "Rechnung allein -> invoice")
    # Steuer-ID/Steuernummer im Text überstimmt nicht
    check(cat("Rechnung_Amazon.pdf", T_AMAZON) == "invoice", "Amazon mit Steuernummer -> invoice")
    check(cat("x.pdf", T_AMAZON) == "invoice", "Amazon-Text allein -> invoice")
    check(cat("x.pdf", T_PAYSLIP) == "payslip", "Payslip-Text mit Steuer-ID/Versicherungsnr -> payslip")
    check(cat("x.pdf", "Steuer-ID 12345678901 Steuernummer 12/345/6789") is None,
          "nur Steuer-ID/Steuernummer im Text -> None")
    check(cat("x.pdf", "Versicherungsnummer 12 120590 K 123") is None, "nur Versicherungsnr -> None")
    # Text braucht 2 Treffer oder einen sehr starken
    check(cat("x.pdf", "Bitte bringen Sie Ihren Ausweis mit.") is None, "1 schwacher Texttreffer -> None")
    check(cat("x.pdf", "Hiermit erhalten Sie Ihr Arbeitszeugnis.") == "certificate", "starker Begriff reicht")
    check(cat("x.pdf", "Mein Lebenslauf liegt bei.") == "cv", "Lebenslauf reicht")
    check(cat("x.pdf", "Anbei der Arbeitsvertrag.") == "contract", "Arbeitsvertrag reicht")
    # Dateiname schlägt Text
    r = org_classify_document("Lebenslauf.pdf", T_AMAZON)
    check(r[0] == "cv", "Name Lebenslauf schlägt Rechnungstext (%r)" % (r,))
    # Kurze Kürzel nur als ganzes Wort
    check(cat("cvs_receipt.pdf") == "invoice", "kein cv in cvs")
    check(cat("video.pdf") is None, "kein id in video")
    check(cat("Auto Australien.docx") != "health", "kein AU in Auto/Australien")
    check(cat("Australia trip.docx") is None, "Australia trip -> None")
    check(cat("AU_Bescheinigung.pdf") == "health", "AU als Token -> health")
    check(cat("nda.pdf") == "contract", "nda als Token")
    check(cat("agenda.pdf") != "contract", "kein nda in agenda")
    check(cat("hypothesis.txt") != "education", "kein thesis in hypothesis")
    check(cat("Wallpaper.png") is None, "kein paper in wallpaper")
    # Rezept
    check(cat("Rezept Lasagne.pdf") is None, "Kochrezept -> None")
    check(cat("Rezept Lasagne.pdf", T_COOKING) is None, "Kochrezept mit Text -> None")
    check(cat("Rezept Dr. med. Weber.pdf") == "health", "Rezept mit Arzt -> health")
    check(cat("Rezept.pdf", T_MEDREZEPT) == "health", "Rezept mit Medikament -> health")
    # Chat-Export
    check(cat("chat.txt", T_WHATSAPP) is None, "WhatsApp-Text -> None")
    check(cat("WhatsApp Chat mit Steuerberater.txt", T_WHATSAPP) is None, "WhatsApp-Name -> None")
    # Bilder: nur starke Begriffe
    check(cat("Urlaub Italien.jpg", "", "image") is None, "Urlaubsfoto kein Reisedokument")
    check(cat("Personalausweis.jpg", "", "image") == "id", "Ausweis-Scan als Bild")
    # Entscheidungen
    check(cat("Kaufvertrag Auto.pdf") == "contract", "Kaufvertrag Auto -> contract")
    check(cat("Garantie.pdf") == "invoice", "Garantie -> invoice")
    # Score
    for n in ["Lebenslauf_Felix_Kucher_2024.pdf", "Resume_2023_final.docx", "CV Felix.pdf",
              "Zeugnis_Bachelor.pdf", "Reisepass.pdf", "Arbeitsvertrag_2021.pdf",
              "Steuerbescheid 2022.pdf", "Gehaltsabrechnung_03_2024.pdf", "Arztbrief.pdf",
              "Rechnung_Amazon_12345.pdf", "Kontoauszug_2024_01.pdf", "Bahnticket.pdf",
              "Bedienungsanleitung Waschmaschine.pdf", "Nebenkostenabrechnung 2023.pdf",
              "Immatrikulationsbescheinigung.pdf", "Police_KFZ.pdf", "Kaufvertrag Auto.pdf",
              "Zulassungsbescheinigung Teil 1.pdf", "Boarding Pass.pdf", "Thesis.pdf",
              "Paper_Nature_2023.pdf", "Reisekostenabrechnung Mai.xlsx", "Impfpass.pdf"]:
        r = org_classify_document(n)
        check(r[1] >= 0.6, "Score >= 0.6 für %s (%r)" % (n, r))
    r = org_classify_document("Lebenslauf.pdf")
    check(isinstance(r, tuple) and len(r) == 4 and isinstance(r[2], list), "Rückgabeform")
    check(r[2] == ["lebenslauf"], "Begriffsliste %r" % (r[2],))
    r = org_classify_document("x.pdf", T_ARBEITSZEUGNIS)
    check(0 < r[1] <= 1, "Score im Bereich")
    # robuste Eingaben
    for bad in [None, 123, b"bytes", "", "....", "\x00\x01", "𝔘𝔫𝔦𝔠𝔬𝔡𝔢.pdf", "a" * 5000]:
        try:
            r = org_classify_document(bad, bad, bad)
            check(isinstance(r, tuple) and len(r) == 4, "Rückgabe bei %r" % (bad,))
            org_detect_year(bad, bad)
            org_normalize(bad)
        except Exception as e:  # pragma: no cover
            check(False, "Ausnahme bei %r: %s" % (bad, e))
    # Begründung
    check(org_lexicon_reason("tax", ["steuerbescheid"]) == "Steuer erkannt (steuerbescheid)", "reason")
    check(org_lexicon_reason("", []) == "", "reason leer")


def run_normalize():
    cases = [
        ("Führerschein", "fuehrerschein"), ("FÜHRERSCHEIN", "fuehrerschein"),
        ("Fuehrerschein", "fuehrerschein"), ("Straße", "strasse"), ("STRASSE", "strasse"),
        ("Lebenslauf_final-v2.(1)+neu.docx", "lebenslauf final v2 1 neu docx"),
        ("  Ärger   über  Öl ", "aerger ueber oel"), ("Zeugnis̈", "zeugnis"),
        ("Bewerbung–Firma/X\\Y", "bewerbung firma x y"), ("Résumé", "resume"),
        ("Kündigung", "kuendigung"), ("Kündigung", "kuendigung"), ("ẞ", "ss"),
        ("", ""), ("W-2", "w 2"), ("SV-Ausweis", "sv ausweis"),
    ]
    for raw, exp in cases:
        got = org_normalize(raw)
        check(got == exp, "normalize(%r)=%r erwartet %r" % (raw, got, exp))
    return len(cases)


def run_years():
    now = time.localtime().tm_year
    cases = [
        ("Steuerbescheid 2022.pdf", "", 2022),
        ("Bescheid_erhalten_2024.pdf", "Bescheid für 2022 über Einkommensteuer", 2022),
        ("scan.pdf", T_STEUERBESCHEID, 2022),
        ("scan.pdf", T_LSTB, 2023),
        ("scan.pdf", "Veranlagungszeitraum: 01.01.2021 - 31.12.2021", 2021),
        ("scan.pdf", "Tax year 2020 ... printed 15 March 2022", 2020),
        ("scan.pdf", "Steuerjahr 2019", 2019),
        ("scan.pdf", "für das Kalenderjahr 2018. Erstellt am 02.02.2020", 2018),
        ("Gehaltsabrechnung_03_2024.pdf", "", 2024),
        ("Kontoauszug_2024_01.pdf", "", 2024),
        ("IMG_20230715_123456.jpg", "", 2023),
        ("Scan 15032021.pdf", "", 2021),
        ("Lebenslauf_Felix.pdf", "", None),
        ("Rechnung_Amazon_12345.pdf", "", None),
        ("x.pdf", T_ARBEITSZEUGNIS, 2023),
        ("x.pdf", "Datum: 2021-06-30, Lieferung 2021-07-02, Zahlung 2022-01-01", 2021),
        ("x.pdf", "München, im März 2023", 2023),
        ("x.pdf", "London, March 5, 2019 and again in March 2019", 2019),
        ("x.pdf", "Wintersemester 2023/24", 2023),
        ("x.pdf", T_IMMA, 2023),
        ("x.pdf", "Geboren am 12.05.1985", None),
        ("x.pdf", "Gültig bis 01.01.%d" % (now + 5), None),
        ("x.pdf", "Im Jahr 2015 wurde das Projekt gestartet.", 2015),
        ("x.pdf", "Betrag 2.019,00 EUR", None),
        ("Gehaltsabrechnung_03_24.pdf", "", 2024),
        ("x.pdf", T_WHATSAPP, 2023),
        ("Einkommensteuererklärung_2023.pdf", "", 2023),
        ("Payslip March 2024.pdf", "", 2024),
        ("x.pdf", T_PAYSLIP, 2024),
        ("x.pdf", "", None),
    ]
    for name, text, exp in cases:
        got = org_detect_year(name, text)
        check(got == exp, "Jahr(%r, %r...) = %r erwartet %r" % (name, text[:30], got, exp))
    # Jahr wird auch von classify geliefert
    check(org_classify_document("Steuerbescheid 2022.pdf")[3] == 2022, "classify liefert Jahr")
    return len(cases)


def run_perf():
    texts = [T_ARBEITSZEUGNIS, T_AMAZON, T_LSTB, T_PAYSLIP, T_KONTOAUSZUG, T_PAPER, "", "", "", "",
             T_MIETVERTRAG * 6, T_STORY * 20]
    names = [c[0] for c in CASES]
    pairs = [(names[i % len(names)], texts[i % len(texts)]) for i in range(10000)]
    t0 = time.perf_counter()
    for n, t in pairs:
        org_classify_document(n, t)
    dt = time.perf_counter() - t0
    print("Leistung: 10.000 Dokumente in %.2f s" % dt)
    check(dt < 4.0, "10.000 Klassifikationen < 4 s (%.2f)" % dt)


if __name__ == "__main__":
    n_cases = run_cases()
    run_strict()
    n_norm = run_normalize()
    n_year = run_years()
    run_perf()
    for f in FAILS:
        print("FEHLER:", f)
    print("Python %s: %d Fälle, %d Normalisierungs-, %d Jahrestests, %d Prüfungen, %d Fehler"
          % (sys.version.split()[0], n_cases, n_norm, n_year, CHECKS[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)
