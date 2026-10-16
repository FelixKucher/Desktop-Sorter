# ===========================================================================
# ORDNER-ASSISTENT – gemeinsame Grundlage (Datenmodell, Kategorien, Regeln)
# ===========================================================================
# Alle Namen dieses Teils beginnen mit org_ / Org / ORG_, damit sie im
# Einzeldatei-Programm DesktopSorter.pyw nicht mit bestehenden Namen kollidieren.
# Nur Standardbibliothek.
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------- Stufen (wie wertvoll ist eine Datei?) ----------
ORG_PROTECT = "protect"   # wichtig, dauerhaft behalten – nie zum Löschen vorschlagen
ORG_KEEP = "keep"         # behalten, nur einsortieren
ORG_REVIEW = "review"     # Nutzer soll entscheiden
ORG_JUNK = "junk"         # Löschvorschlag (Papierkorb) – nur nach Bestätigung
ORG_TIERS = (ORG_PROTECT, ORG_KEEP, ORG_REVIEW, ORG_JUNK)
ORG_TIER_LABELS = {ORG_PROTECT: "Wichtig", ORG_KEEP: "Behalten",
                   ORG_REVIEW: "Prüfen", ORG_JUNK: "Aufräumen"}

# ---------- Dateiarten nach Endung ----------
ORG_KIND_EXT: dict[str, set[str]] = {
    "document": {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".pages",
                 ".xls", ".xlsx", ".ods", ".csv", ".numbers", ".ppt", ".pptx", ".odp",
                 ".key", ".epub", ".xps", ".oxps", ".eml", ".msg", ".tex"},
    "image": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff",
              ".heic", ".heif", ".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf",
              ".rw2", ".svg", ".psd", ".xcf"},
    "video": {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".wmv", ".webm", ".mts", ".m2ts",
              ".3gp", ".mpg", ".mpeg"},
    "audio": {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma", ".aiff",
              ".aif", ".amr"},
    "archive": {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst"},
    "installer": {".exe", ".msi", ".msix", ".msixbundle", ".appx", ".appxbundle", ".iso",
                  ".img", ".dmg", ".pkg", ".apk"},
    "code": {".py", ".ipynb", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".cs", ".go",
             ".rs", ".r", ".jl", ".m", ".sh", ".ps1", ".bat", ".sql", ".html", ".css",
             ".json", ".yaml", ".yml", ".toml", ".xml"},
    "cad": {".dwg", ".dxf", ".stl", ".3mf", ".step", ".stp", ".f3d", ".obj", ".fbx",
            ".blend", ".skp", ".gcode", ".iges", ".igs"},
    "science": {".fasta", ".fa", ".fastq", ".fq", ".gb", ".gbk", ".dna", ".ab1", ".sbol",
                ".pdb", ".cif", ".bam", ".sam", ".vcf", ".bed", ".gff", ".gtf", ".h5ad",
                ".fcs", ".czi", ".nd2", ".lif", ".prism", ".pzfx", ".h5", ".hdf5"},
}


def org_kind(path: Path) -> str:
    """Grobe Dateiart: document|image|video|audio|archive|installer|code|cad|science|other."""
    ext = path.suffix.lower()
    for kind, exts in ORG_KIND_EXT.items():
        if ext in exts:
            return kind
    return "other"


# ---------- Kategorien: Zielordner + Stufe ----------
# folder darf {year} (Jahr aus Dokument/EXIF/Änderungsdatum) und {event}
# (Foto-Ereignis, z. B. "2024-07 Juli") enthalten. "" = bleibt, wo es ist.
# Ordnernamen bewusst deutsch; die Erkennung ist zweisprachig (DE + EN).
ORG_CATEGORIES: dict[str, dict] = {
    # --- wichtige Dokumente (dauerhaft behalten) ---
    "cv":          {"label": "Lebenslauf & Bewerbung", "folder": "Dokumente/Bewerbung", "tier": ORG_PROTECT},
    "certificate": {"label": "Zeugnisse & Urkunden", "folder": "Dokumente/Zeugnisse & Urkunden", "tier": ORG_PROTECT},
    "id":          {"label": "Ausweise & Identität", "folder": "Dokumente/Ausweise", "tier": ORG_PROTECT},
    "contract":    {"label": "Verträge", "folder": "Dokumente/Verträge", "tier": ORG_PROTECT},
    "insurance":   {"label": "Versicherungen", "folder": "Dokumente/Versicherungen", "tier": ORG_PROTECT},
    "tax":         {"label": "Steuer", "folder": "Dokumente/Steuer/{year}", "tier": ORG_PROTECT},
    "payslip":     {"label": "Gehalt & Rente", "folder": "Dokumente/Gehalt & Rente/{year}", "tier": ORG_PROTECT},
    "health":      {"label": "Gesundheit", "folder": "Dokumente/Gesundheit", "tier": ORG_PROTECT},
    "property":    {"label": "Wohnen & Eigentum", "folder": "Dokumente/Wohnen", "tier": ORG_PROTECT},
    # --- Alltagsdokumente (behalten, einsortieren) ---
    "invoice":     {"label": "Rechnungen & Belege", "folder": "Dokumente/Rechnungen/{year}", "tier": ORG_KEEP},
    "bank":        {"label": "Bank & Finanzen", "folder": "Dokumente/Finanzen/{year}", "tier": ORG_KEEP},
    "vehicle":     {"label": "Fahrzeug", "folder": "Dokumente/Fahrzeug", "tier": ORG_KEEP},
    "education":   {"label": "Studium & Schule", "folder": "Dokumente/Studium & Schule", "tier": ORG_KEEP},
    "work":        {"label": "Arbeit", "folder": "Dokumente/Arbeit", "tier": ORG_KEEP},
    "science":     {"label": "Wissenschaft", "folder": "Dokumente/Wissenschaft", "tier": ORG_KEEP},
    "travel":      {"label": "Reisen & Tickets", "folder": "Dokumente/Reisen", "tier": ORG_REVIEW},
    "manual":      {"label": "Anleitungen", "folder": "Dokumente/Anleitungen", "tier": ORG_REVIEW},
    "document":    {"label": "Sonstige Dokumente", "folder": "Dokumente/Sonstiges", "tier": ORG_REVIEW},
    # --- Medien ---
    "photo_personal": {"label": "Persönliche Fotos", "folder": "Bilder/Fotos/{year}/{event}", "tier": ORG_PROTECT},
    "photo_chat":  {"label": "Chat-Bilder", "folder": "Bilder/Chats", "tier": ORG_REVIEW},
    "screenshot":  {"label": "Bildschirmfotos", "folder": "Bilder/Bildschirmfotos", "tier": ORG_REVIEW},
    "image":       {"label": "Bilder", "folder": "Bilder/Sonstige", "tier": ORG_KEEP},
    "video_personal": {"label": "Persönliche Videos", "folder": "Videos/{year}", "tier": ORG_PROTECT},
    "video":       {"label": "Videos", "folder": "Videos", "tier": ORG_KEEP},
    "audio":       {"label": "Musik & Audio", "folder": "Musik", "tier": ORG_KEEP},
    # --- Technik ---
    "archive":     {"label": "Archive", "folder": "Archive", "tier": ORG_REVIEW},
    "installer":   {"label": "Installationsdateien", "folder": "Programme & Installer", "tier": ORG_REVIEW},
    "code":        {"label": "Code & Projekte", "folder": "Projekte", "tier": ORG_KEEP},
    "cad":         {"label": "CAD & 3D", "folder": "Projekte/CAD & 3D", "tier": ORG_KEEP},
    "science_data": {"label": "Labordaten", "folder": "Dokumente/Wissenschaft/Daten", "tier": ORG_KEEP},
    # --- Aufräumen ---
    "temp":        {"label": "Temporär & Reste", "folder": "", "tier": ORG_JUNK},
    "duplicate":   {"label": "Duplikate", "folder": "", "tier": ORG_JUNK},
    "other":       {"label": "Sonstiges", "folder": "", "tier": ORG_REVIEW},
}

# ---------- Sicherheit: was nie angefasst / nie betreten wird ----------
# Ordner mit diesen Namen werden nicht durchsucht (System, Programme, Caches).
ORG_SKIP_DIRS = {
    "$recycle.bin", "system volume information", "windows", "program files",
    "program files (x86)", "programdata", "appdata", "node_modules", ".git", ".svn",
    ".hg", "venv", ".venv", "env", "__pycache__", ".idea", ".vscode", ".cache",
    "site-packages", "dist-packages", ".tox", ".mypy_cache", ".pytest_cache",
    "onedrive temp", "desktopsorter boxen",
}
# Enthält ein Ordner eine dieser Dateien/Unterordner, ist er ein Projekt und wird als
# Einheit behandelt: nichts darin wird verschoben, umbenannt oder gelöscht.
# Starke Marker machen einen Ordner allein zum Projekt. Schwache Marker (eine einzelne
# requirements.txt oder package.json landet schnell mal im Downloads-Ordner) zählen nur,
# wenn daneben echter Quellcode liegt und der Ordner kein typischer Benutzerordner ist.
ORG_PROJECT_STRONG = {
    ".git", ".hg", ".svn", "cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "cmakelists.txt", ".sln", ".csproj", ".vcxproj", "composer.json", "gemfile", ".project",
    ".idea", "renv.lock", "snakefile", "nextflow.config", ".obsidian",
}
ORG_PROJECT_WEAK = {"package.json", "pyproject.toml", "setup.py", "requirements.txt",
                    "makefile", "environment.yml"}
ORG_PROJECT_MARKERS = ORG_PROJECT_STRONG | ORG_PROJECT_WEAK
ORG_SOURCE_EXT = {".py", ".ipynb", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".cs", ".go",
                  ".rs", ".r", ".jl", ".m", ".sh", ".ps1"}
ORG_USER_DIR_NAMES = {"downloads", "download", "desktop", "documents", "dokumente",
                      "eigene dateien", "pictures", "bilder", "music", "musik", "videos",
                      "onedrive", "schreibtisch"}
ORG_MAX_FILES = 60_000        # Obergrenze je Analyse (wird im UI gemeldet)


def org_is_project_dir(d: Path) -> bool:
    try:
        names = {p.name.lower() for p in d.iterdir()}
    except OSError:
        return False
    if names & ORG_PROJECT_STRONG:
        return True
    if any(n.endswith((".sln", ".csproj", ".vcxproj", ".xcodeproj")) for n in names):
        return True
    if names & ORG_PROJECT_WEAK and d.name.lower() not in ORG_USER_DIR_NAMES:
        return any(os.path.splitext(n)[1] in ORG_SOURCE_EXT for n in names)
    return False


# ---------- Datensatz je Datei ----------
@dataclass
class OrgFile:
    path: Path                     # absoluter Pfad
    rel: str                       # Pfad relativ zum analysierten Ordner (mit "/")
    size: int
    mtime: float
    kind: str                      # siehe org_kind()
    ext: str                       # Endung klein, mit Punkt
    depth: int = 0                 # 0 = liegt direkt im analysierten Ordner
    text: str = ""                 # extrahierter Textanfang (max. ~4000 Zeichen)
    meta: dict = field(default_factory=dict)   # z. B. exif: datetime/make/model/gps, pages
    category: str = ""             # Schlüssel aus ORG_CATEGORIES
    tier: str = ""                 # ORG_PROTECT | ORG_KEEP | ORG_REVIEW | ORG_JUNK
    score: float = 0.0             # Sicherheit der Einordnung 0..1
    reasons: list = field(default_factory=list)  # kurze deutsche Begründungen
    year: int | None = None        # Bezugsjahr (Dokumentdatum, EXIF, sonst mtime)
    event: str = ""                # Foto-Ereignis, z. B. "2024-07 Juli – Reise"
    dup_of: str = ""               # rel des Originals, wenn exaktes Duplikat
    version_group: str = ""        # Schlüssel einer Versionsgruppe
    version_latest: bool = True    # neueste Version innerhalb der Gruppe?
    target: str = ""               # vorgeschlagener Zielordner relativ zum Wurzelordner
    in_project: bool = False       # liegt in einem Projektordner → nie anfassen
    cloud: bool = False            # OneDrive & Co. „nur online“: nie lesen (würde Download auslösen)

    @property
    def name(self) -> str:
        return self.path.name

    def add_reason(self, text: str) -> None:
        if text and text not in self.reasons:
            self.reasons.append(text)


def org_year_of(f: OrgFile) -> int:
    """Bezugsjahr: f.year, sonst EXIF-Datum, sonst Änderungsdatum."""
    if f.year:
        return f.year
    dt = (f.meta.get("exif") or {}).get("datetime", "")
    if dt[:4].isdigit():
        return int(dt[:4])
    return time.localtime(f.mtime).tm_year


def org_rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def org_walk(root: Path, recursive: bool = True, limit: int = ORG_MAX_FILES,
             cancelled=lambda: False) -> tuple[list[OrgFile], list[str], bool]:
    """Durchläuft root und liefert (Dateien, Projektordner-rel, abgeschnitten?).
    Überspringt System-/Cache-Ordner, versteckte Dateien und Links; Projektordner
    werden erfasst (für Duplikat-Schutz), ihre Dateien aber als in_project markiert."""
    files: list[OrgFile] = []
    projects: list[str] = []
    truncated = False
    root = root.resolve()
    stack: list[tuple[Path, int, bool]] = [(root, 0, False)]
    while stack:
        if cancelled():
            break
        d, depth, in_proj = stack.pop()
        if d != root and not in_proj and org_is_project_dir(d):
            in_proj = True
            projects.append(org_rel(d, root))
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            name_l = e.name.lower()
            try:
                if e.is_symlink():
                    continue
                if _org_hidden(e):
                    continue
                if e.is_dir(follow_symlinks=False):
                    if name_l in ORG_SKIP_DIRS or name_l.startswith("."):
                        continue
                    if _org_is_link_dir(e):          # Junction/Link: zeigt evtl. woandershin
                        continue
                    if recursive:
                        stack.append((Path(e.path), depth + 1, in_proj))
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if name_l == "desktop.ini":          # Ordner-Einstellungen: nie anfassen
                continue
            p = Path(e.path)
            attrs = getattr(st, "st_file_attributes", 0)
            files.append(OrgFile(path=p, rel=org_rel(p, root), size=st.st_size,
                                 mtime=st.st_mtime, kind=org_kind(p), ext=p.suffix.lower(),
                                 depth=depth, in_project=in_proj,
                                 cloud=bool(attrs & ORG_CLOUD_ATTRS)))
            if len(files) >= limit:
                truncated = True
                return files, projects, truncated
    return files, projects, truncated


def _org_hidden(entry: os.DirEntry) -> bool:
    """Versteckt/System (Windows-Attribute). Thumbs.db & .DS_Store bleiben sichtbar,
    damit sie als Reste erkannt werden können."""
    if entry.name.lower() in ("thumbs.db", ".ds_store"):
        return False
    try:
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attrs & 0x2 or attrs & 0x4)       # HIDDEN | SYSTEM


# FILE_ATTRIBUTE_OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS: Datei liegt nur in der Cloud
ORG_CLOUD_ATTRS = 0x1000 | 0x40000 | 0x400000
_ORG_LINK_TAGS = (0xA0000003, 0xA000000C)      # IO_REPARSE_TAG_MOUNT_POINT, _SYMLINK


def _org_is_link_dir(entry: os.DirEntry) -> bool:
    """Junction oder Verzeichnis-Link (Windows). OneDrive-Platzhalter (andere Tags) zählen nicht."""
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return True
    return getattr(st, "st_reparse_tag", 0) in _ORG_LINK_TAGS
