# Desktop Sorter

Sortiert die Symbole auf dem Windows-Desktop thematisch in **Glas-Boxen direkt auf dem Desktop** – nach dem Prinzip von Stardock Fences, gruppiert wie die iOS-App-Mediathek (Spiele, Kommunikation, Entwicklung, …). Jede Box zeigt alle ihre Programme; ein Klick startet sie.

![Glas-Boxen auf dem Desktop](docs/boxen.png)

![Desktop Sorter – Zuordnung](docs/screenshot.png)

*Testansichten mit Platzhalter-Symbolen. Unter Windows erscheinen die echten Programmsymbole, und die Boxen zeigen dein Hintergrundbild unscharf durch (Acrylic).*

## Installation

Voraussetzung: Windows 10/11, Python ≥ 3.9.

```powershell
py -m pip install -r requirements.txt
```

Danach `DesktopSorter.pyw` per Doppelklick starten (oder `py DesktopSorter.pyw`).
Die Datei **nicht auf den Desktop** legen, sonst sortiert sie sich selbst mit ein.

## Ablauf

1. **Zuordnen** im Hauptfenster: Gruppen erscheinen als Glaskarten, Unklares (orange **?**) ordnest du per Drag & Drop oder Rechtsklick zu.
2. **In Boxen sortieren**: Die Verknüpfungen wandern in `%USERPROFILE%\DesktopSorter Boxen\<Gruppe>`, und für jede Gruppe erscheint eine Glas-Box auf dem Desktop.
3. Die App läuft danach im **Infobereich** (Tray) weiter und hält die Boxen auf dem Desktop. Über das Tray-Menü: *Mit Windows starten* aktiviert den Autostart.

## Die Boxen

| Aktion | So geht's |
|---|---|
| Programm starten | **ein Klick** auf das Symbol |
| Box verschieben | Titelleiste ziehen |
| Größe ändern | rechte untere Ecke ziehen |
| Ein-/Ausklappen | Doppelklick auf die Titelleiste |
| Zwischen Boxen verschieben | Symbol in eine andere Box ziehen · Rechtsklick → *Verschieben in* |
| Zurück auf den Desktop | Symbol auf den Desktop ziehen · Rechtsklick → *Zurück auf den Desktop* |
| Neues in eine Box legen | Datei vom Desktop/Explorer auf die Box ziehen |
| Rechtsklick auf Titelleiste | Umbenennen · Neue Box · Box auflösen · Desktop Sorter öffnen · Beenden |

Position, Größe und Einklapp-Zustand jeder Box werden gespeichert. Die Boxen spiegeln den Inhalt ihrer Ordner live – was du im Explorer in `DesktopSorter Boxen\<Gruppe>` legst, erscheint sofort in der Box.

## Zuordnungslogik

Jedes Objekt durchläuft diese Stufen; die erste, die greift, entscheidet:

1. **Deine Entscheidungen** aus früheren Läufen (`entscheidungen.json`)
2. **Eigene Regeln** aus `regeln.toml` – z. B. Projektthemen wie „Hausplanung“
3. **App-Wissensbasis** – Name und Ziel-EXE der Verknüpfung
4. **Spiele-Erkennung** – Ziel liegt in Steam-, Epic-, EA-, Ubisoft-Bibliotheken oder ist ein `steam://`-Link
5. **Stichwörter** im Dateinamen (Rechnung, Bestellung, …)
6. **Dateityp** – nur als Vorschlag; solche Objekte gelten als *unklar*
7. **Optional Claude** – ordnet Unklares semantisch zu und darf eigene Themen bilden

Erfasst werden beide Desktop-Quellen, die Windows gemeinsam anzeigt: der eigene Desktop (auch OneDrive-umgeleitet) und der öffentliche Desktop (`C:\Users\Public\Desktop`).

## Dateien

| Datei | Zweck |
|---|---|
| `DesktopSorter.pyw` | die komplette Anwendung |
| `regeln.toml` | eigene Themen; wird beim ersten Start angelegt und ist frei editierbar |
| `entscheidungen.json` | deine Zuordnungen und gelernten Stichwörter (persönlich, nicht im Repo) |
| `%USERPROFILE%\DesktopSorter Boxen\` | Inhalt der Boxen (ein Unterordner je Box) + `.boxen.json` (Positionen) |
| `%USERPROFILE%\.desktop_sorter\` | Journal für „Rückgängig“ |

Beispiel für `regeln.toml`:

```toml
[regeln]
"Hausplanung" = ["obergeschoss", "grundriss", "loopcad", "sweet home"]
"Labor"       = ["benchling", "snapgene", "\\.ab1$"]
```

## Datenschutz

Ohne Claude läuft alles lokal. Mit **Mit Claude sortieren** werden nur Anzeigename, Typ und Ziel der unklaren Objekte gesendet – der Benutzerpfad wird dabei durch `%USERPROFILE%` ersetzt, Dateiinhalte werden nie übertragen.

## Bekannte Grenzen

- **Win + D** („Desktop anzeigen“) blendet die Boxen wie alle Fenster aus; erneutes Win + D holt sie zurück.
- Unter Windows 10 sind die Ecken der Boxen eckig (runde Fensterecken gibt es erst ab Windows 11).

## Fehlerbehebung

- **„no Qt platform plugin could be initialized“:** `python -m pip install --force-reinstall PySide6 PySide6_Essentials PySide6_Addons shiboken6` in der Umgebung ausführen, mit der du startest.
- **Schwarzer statt durchsichtiger Hintergrund:** In `DesktopSorter.pyw` `ACRYLIC = False` setzen (Hauptfenster) bzw. `win_acrylic` deaktivieren (Boxen).
- **Transparenz anpassen:** `ACRYLIC_TINT` (Hauptfenster) und `FENCE_TINT` (Boxen) – bei `ACRYLIC_TINT` sind die letzten, bei `FENCE_TINT` die ersten zwei Hex-Stellen die Deckkraft.
- **Verknüpfungen vom öffentlichen Desktop lassen sich nicht verschieben:** als Administrator starten oder den Schalter „Öffentlicher Desktop“ ausschalten.
- **Doppelklick öffnet einen Editor:** Rechtsklick → Öffnen mit → `pythonw.exe` → „Immer diese App verwenden“.
