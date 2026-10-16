# ===========================================================================
# ORDNER-ASSISTENT – Planer: verbindet alle Prüfungen zu einem Aufräum-Plan
# ===========================================================================
# org_analyze()      Ordner analysieren → OrgPlan (Vorschläge, nichts wird verändert)
# org_apply_moves()  ausgewählte Verschiebungen ausführen (mit Protokoll, nie überschreiben)
# org_undo()         Verschiebungen anhand des Protokolls rückgängig machen
# org_trash()        Aufräum-Kandidaten in den Papierkorb (Funktion wird übergeben)
# org_backup()       „Tresor“: wichtige Dateien inkrementell und geprüft sichern
#
# Alle Namen beginnen mit org_ / Org / ORG_ / _org_plan_ (Einzeldatei-Programm).
# Nur Standardbibliothek.
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from org_core import (ORG_CATEGORIES, ORG_JUNK, ORG_KEEP, ORG_PROJECT_MARKERS,  # noqa: F401
                      ORG_PROTECT, ORG_REVIEW, OrgFile, org_is_project_dir, org_walk,
                      org_year_of)
from org_lexicon import org_classify_document, org_normalize
from org_extract import org_extract_text, org_image_size, org_read_exif
from org_dupes import (org_find_duplicates, org_mark_versions, org_pick_keeper,
                       org_version_groups)
from org_junk import (org_detect_junk, org_empty_dirs, org_installed_programs,
                      org_mark_installers)
from org_photos import (ORG_MEDIA_CATEGORIES, org_classify_media, org_month_name,
                        org_photo_dt, org_photo_events)

# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------
ORG_PLAN_TEXT_BUDGET = 25.0        # s: Inhalte lesen je Analyse, danach nur Dateinamen
ORG_PLAN_META_BUDGET = 60.0        # s: EXIF/Bildgröße lesen je Analyse
ORG_PLAN_TEXT_MAX_CHARS = 4000
ORG_PLAN_CHECK_SCORE = 0.6         # ab dieser Sicherheit ist eine Verschiebung vorausgewählt
ORG_PLAN_MEDIA_CHECK_SCORE = 0.7   # Medien mit eindeutigem Muster (auch Stufe „Prüfen“)
ORG_PLAN_PROGRESS_EVERY = 250
ORG_PLAN_PROGRESS_SECONDS = 0.3    # spätestens so oft Fortschritt melden (langsame Schritte)
ORG_PLAN_HASH_CHUNK = 1024 * 1024

# Dateiarten, die NUR nach dem Dateinamen als Dokument erkannt werden (kein Textinhalt):
# fotografierte Dokumente („Fahrzeugschein.jpg“, „Kassenbon.jpg“, „Mutterpass.jpg“) und
# Archive („Bewerbungsunterlagen.zip“, „Zeugnisse.zip“). Das Lexikon ist bei Bildern
# bewusst streng (nur starke Begriffe, Feier-/Ereignisnamen ausgenommen) – daher kein
# zusätzlicher Vorfilter (ein solcher hatte z. B. Fahrzeugschein/Kassenbon übersehen).
ORG_PLAN_NAME_ONLY_KINDS = ("image", "archive")
# Diese Kategorien vergibt die Dokument-Erkennung für Bilder/Archive nie
_ORG_PLAN_NAME_ONLY_DENY = {"photo_personal", "photo_chat", "screenshot", "image",
                            "video_personal", "video", "audio", "archive", "installer",
                            "code", "cad", "science_data", "temp", "duplicate", "other",
                            "document"}
ORG_PLAN_EXIF_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp",
                      ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2"}
ORG_PLAN_SIZE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

# Rückfall-Kategorien je Dateiart: (Kategorie, Sicherheit)
ORG_PLAN_KIND_FALLBACK = {
    "archive": ("archive", 0.5), "code": ("code", 0.4), "cad": ("cad", 0.6),
    "science": ("science_data", 0.6), "audio": ("audio", 0.6), "other": ("other", 0.3),
    "installer": ("other", 0.3), "image": ("image", 0.4), "video": ("video", 0.4),
    "document": ("document", 0.3),
}

# Wird z. B. „Dokumente“ selbst aufgeräumt, entfällt der Zielordner-Anfang „Dokumente/“
ORG_PLAN_ROOT_ALIASES = {
    "dokumente": {"documents", "dokumente", "eigene dokumente", "my documents",
                  "eigene dateien"},
    "bilder": {"pictures", "bilder", "eigene bilder", "my pictures"},
    "videos": {"videos", "eigene videos", "my videos"},
    "musik": {"music", "musik", "eigene musik", "my music"},
}

# Gespeicherte Webseiten: „Seite.html“ + Ordner „Seite_files“ gehören zusammen
ORG_PLAN_WEB_EXTS = {".html", ".htm", ".xhtml", ".shtml"}
ORG_PLAN_WEB_DIR_SUFFIXES = ("_files", "-dateien", "_dateien", " dateien", "-files",
                             ".files", "_archivos", "-fichiers")
# Programmordner (portable Programme, Spiele): nichts daraus herauslösen
ORG_PLAN_PROGRAM_LIB_EXTS = {".dll", ".pak", ".asar", ".ocx", ".bpl", ".so", ".dylib"}

ORG_PLAN_STAGES = ("Dateien erfassen", "Inhalte lesen", "Einordnen", "Duplikate suchen",
                   "Fotos gruppieren", "Plan erstellen")

_ORG_PLAN_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_ORG_PLAN_RESERVED = {"con", "prn", "aux", "nul"} | {"com%d" % i for i in range(1, 10)} | \
    {"lpt%d" % i for i in range(1, 10)}
_ORG_PLAN_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_ORG_PLAN_USER_PROTECT = "_org_user_protect"
_ORG_PLAN_DUP_KEEPER = "_org_dup_keeper"
_ORG_PLAN_LINKED = "_org_plan_linked"
_ORG_PLAN_JOURNAL_TAIL = b"]}\n"
# Änderungszeit-Toleranz (s) beim Prüfen „seit der Analyse verändert?“ (FAT/Netzlaufwerke)
ORG_PLAN_MTIME_TOLERANCE = 1.0
# Windows-Reparse-Punkte, die auf einen anderen Ort zeigen (Junction, Symlink).
# Cloud-Platzhalter (OneDrive) haben andere Tags und bleiben normal bearbeitbar.
_ORG_PLAN_ATTR_REPARSE = 0x400
_ORG_PLAN_LINK_TAGS = (0xA0000003, 0xA000000C)   # IO_REPARSE_TAG_MOUNT_POINT / _SYMLINK


# ---------------------------------------------------------------------------
# Datenmodell
# ---------------------------------------------------------------------------
@dataclass
class OrgMove:
    file: OrgFile
    dest_rel: str          # Zielordner relativ zum Wurzelordner, mit "/"
    checked: bool = False  # in der Oberfläche vorausgewählt


@dataclass
class OrgPlan:
    root: Path
    files: list = field(default_factory=list)
    projects: list = field(default_factory=list)
    truncated: bool = False
    empty_dirs: list = field(default_factory=list)
    moves: list = field(default_factory=list)
    junk: list = field(default_factory=list)
    protect: list = field(default_factory=list)
    review: list = field(default_factory=list)
    versions: dict = field(default_factory=dict)
    dup_groups: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)


class _org_PlanCancelled(Exception):
    pass


# ---------------------------------------------------------------------------
# Kleine Hilfen
# ---------------------------------------------------------------------------
def _org_plan_call_progress(progress, stage: str, done: int, total: int) -> None:
    if progress is None:
        return
    try:
        progress(stage, int(done), int(total))
    except Exception:
        pass


def _org_plan_due(tick: list) -> bool:
    """True, wenn seit der letzten Meldung ORG_PLAN_PROGRESS_SECONDS vergangen sind
    (tick = [letzter Zeitpunkt], wird fortgeschrieben)."""
    now = time.monotonic()
    if now - tick[0] >= ORG_PLAN_PROGRESS_SECONDS:
        tick[0] = now
        return True
    return False


def _org_plan_walk_cancelled(progress, cancelled):
    """Abbruch-Prüfung für org_walk (wird je Ordner aufgerufen) – meldet nebenbei
    „Dateien erfassen“ mit der Zahl durchsuchter Ordner (gesamt 0 = unbestimmt),
    damit die Oberfläche bei großen/langsamen Laufwerken nicht stehen bleibt."""
    state = {"dirs": 0, "tick": [time.monotonic()]}

    def _check():
        state["dirs"] += 1
        if _org_plan_due(state["tick"]):
            _org_plan_call_progress(progress, "Dateien erfassen", state["dirs"], 0)
        return _org_plan_is_cancelled(cancelled)
    return _check


def _org_plan_is_cancelled(cancelled) -> bool:
    if cancelled is None:
        return False
    try:
        return bool(cancelled())
    except Exception:
        return False


def _org_plan_check(cancelled) -> None:
    if _org_plan_is_cancelled(cancelled):
        raise _org_PlanCancelled()


def _org_plan_key(p) -> str:
    """Vergleichsschlüssel für Pfade (Groß-/Kleinschreibung wie das Betriebssystem)."""
    s = unicodedata.normalize("NFC", os.fspath(p))
    return os.path.normcase(os.path.normpath(s))


def _org_plan_inside(root_key: str, path_key: str) -> bool:
    if path_key == root_key:
        return True
    base = root_key if root_key.endswith(os.sep) else root_key + os.sep
    return path_key.startswith(base)


def _org_plan_fold(s: str) -> str:
    return unicodedata.normalize("NFC", s).casefold()


def _org_plan_tier_of(cat: str) -> str:
    return ORG_CATEGORIES.get(cat, {}).get("tier", ORG_REVIEW)


def _org_plan_user_protected(f) -> bool:
    try:
        return bool(f.meta.get(_ORG_PLAN_USER_PROTECT))
    except Exception:
        return False


def _org_plan_sanitize_part(part: str) -> str:
    s = _ORG_PLAN_BAD_CHARS.sub("_", part).strip().rstrip(". ")
    if not s:
        return ""
    if s.split(".")[0].lower() in _ORG_PLAN_RESERVED:
        s = "_" + s
    return s[:120].rstrip(". ")


def _org_plan_split_name(name: str):
    stem, ext = os.path.splitext(name)
    if not stem:                       # ".bashrc"
        return name, ""
    return stem, ext


def _org_plan_free_name(directory: Path, name: str, tag: str = "") -> Path:
    """Kollisionsfreier Name: "x.pdf", "x (2).pdf" … bzw. mit Zusatz
    "x (wiederhergestellt).pdf", "x (wiederhergestellt 2).pdf" …"""
    stem, ext = _org_plan_split_name(name)
    for k in range(1, 100000):
        if tag:
            cand = "%s (%s)%s" % (stem, tag, ext) if k == 1 else \
                "%s (%s %d)%s" % (stem, tag, k, ext)
        else:
            cand = name if k == 1 else "%s (%d)%s" % (stem, k, ext)
        p = directory / cand
        if not os.path.lexists(str(p)):
            return p
    raise OSError("Kein freier Dateiname für %s" % name)


def _org_plan_rename_noreplace(src, dst) -> None:
    """Verschiebt src → dst, überschreibt NIE (FileExistsError, wenn dst existiert)."""
    src, dst = os.fspath(src), os.fspath(dst)
    if os.name == "nt":
        os.rename(src, dst)            # Windows: wirft FileExistsError, wenn dst existiert
        return
    try:
        os.link(src, dst)              # atomar: scheitert, wenn dst existiert
    except FileExistsError:
        raise
    except OSError:
        # Dateisystem ohne harte Links (FAT, manche Netzlaufwerke)
        if os.path.lexists(dst):
            raise FileExistsError(dst)
        os.rename(src, dst)
        return
    try:
        os.unlink(src)
    except OSError:
        try:
            os.unlink(dst)             # nur den eben angelegten zweiten Namen entfernen
        except OSError:
            pass
        raise


def _org_plan_hash_file(path, cancelled=None):
    """(blake2b-Hex, Größe) oder (None, -1)."""
    h = hashlib.blake2b(digest_size=32)
    size = 0
    try:
        with open(os.fspath(path), "rb") as fh:
            while True:
                chunk = fh.read(ORG_PLAN_HASH_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
                size += len(chunk)
                if cancelled is not None and size % (64 * ORG_PLAN_HASH_CHUNK) == 0 \
                        and _org_plan_is_cancelled(cancelled):
                    return None, -1
    except (OSError, ValueError, TypeError):
        return None, -1
    return h.hexdigest(), size


def _org_plan_unchanged(f, st) -> bool:
    """Größe und Änderungszeit wie bei der Analyse? (unbekannt → False = nicht anfassen)"""
    try:
        if int(st.st_size) != int(f.size):
            return False
        m = float(f.mtime)
        if m != m or m in (float("inf"), float("-inf")):
            return False
        return abs(float(st.st_mtime) - m) <= ORG_PLAN_MTIME_TOLERANCE
    except Exception:
        return False


def _org_plan_real_key(p) -> str:
    """Schlüssel des echten Orts: Ordner aufgelöst (Links/Junctions), Name unverändert."""
    s = os.fspath(p)
    parent, name = os.path.split(os.path.abspath(s))
    return _org_plan_key(os.path.join(os.path.realpath(parent), name))


def _org_plan_is_link_dir(path, lstat=None) -> bool:
    """Ordner ist ein Symlink oder eine Windows-Junction (zeigt evtl. aus dem Ordner
    hinaus). OneDrive-Platzhalter (Cloud-Reparse-Tags) zählen nicht."""
    try:
        if lstat is None and os.path.islink(str(path)):
            return True
        st = (lstat or os.lstat)(str(path))
    except (OSError, ValueError, TypeError):
        return False
    try:
        import stat as _st
        if _st.S_ISLNK(int(getattr(st, "st_mode", 0) or 0)):
            return True
    except Exception:
        pass
    attrs = int(getattr(st, "st_file_attributes", 0) or 0)
    if attrs & _ORG_PLAN_ATTR_REPARSE:
        tag = int(getattr(st, "st_reparse_tag", 0) or 0)
        return tag in _ORG_PLAN_LINK_TAGS
    return False


def _org_plan_link_dirs(root: Path, files) -> list:
    """Unterordner (rel), die Verknüpfungen sind; tiefere Ebenen darunter nicht extra."""
    dirs = set()
    for f in files:
        try:
            parts = f.rel.split("/")[:-1]
        except Exception:
            continue
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    found = []
    for d in sorted(dirs, key=lambda x: (x.count("/"), x)):
        if any(d.startswith(x + "/") for x in found):
            continue
        if _org_plan_is_link_dir(root.joinpath(*d.split("/"))):
            found.append(d)
    return found


def _org_plan_root_of(f):
    """Wurzelordner einer Datei aus path und rel ableiten (oder None)."""
    try:
        n = len([p for p in str(f.rel).replace("\\", "/").split("/") if p])
        if n <= 0:
            return None
        return Path(f.path).parents[n - 1]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Analyse
# ---------------------------------------------------------------------------
def _org_plan_root_is_project(root: Path) -> bool:
    """Wurzel selbst oder ein Elternordner (bis ausschließlich Benutzerordner/Laufwerk)
    ist ein Projekt → nichts verändern."""
    if org_is_project_dir(root):
        return True
    try:
        home_key = _org_plan_key(Path.home().resolve())
    except Exception:
        home_key = ""
    try:
        parents = list(root.parents)
    except Exception:
        return False
    for p in parents[:-1][:8]:         # ohne Laufwerkswurzel
        if _org_plan_key(p) == home_key:
            break
        try:
            names = {n.lower() for n in os.listdir(str(p))}
        except OSError:
            continue
        if names & {".git", ".hg", ".svn"}:
            return True
    return False


def _org_plan_project_of(rel: str, proj_set: set) -> str:
    parts = rel.split("/")[:-1]
    for i in range(len(parts), 0, -1):
        cand = "/".join(parts[:i])
        if cand in proj_set:
            return cand
    return ""


def _org_plan_protect_keys(protected_paths) -> set:
    keys = set()
    try:
        items = list(protected_paths or ())
    except Exception:
        return keys
    for p in items:
        try:
            keys.add(_org_plan_key(os.path.realpath(os.fspath(p))))
        except Exception:
            continue
    return keys


def _org_plan_key_protected(k: str, keys: set, root_key: str) -> bool:
    """Pfad-Schlüssel k oder ein Elternordner ist geschützt? Ab root wird ohne Abbruch
    bis zur Laufwerkswurzel weitergeprüft (geschützter Ordner oberhalb von root)."""
    while True:
        if k in keys:
            return True
        parent = os.path.dirname(k)
        if parent == k:
            return False
        k = parent


def _org_plan_apply_user_protect(files, protected_paths, root_key: str) -> int:
    """Vom Nutzer geschützte Pfade (Dateien oder Ordner) markieren."""
    keys = _org_plan_protect_keys(protected_paths)
    if not keys:
        return 0
    # root selbst oder ein Elternordner geschützt → alles geschützt (einmal prüfen)
    all_hit = _org_plan_key_protected(root_key, keys, root_key)
    inner = set(k for k in keys if k != root_key and _org_plan_inside(root_key, k))
    n = 0
    for f in files:
        try:
            hit = all_hit
            if not hit and inner:
                k = _org_plan_key(f.path)
                while k != root_key:
                    if k in inner:
                        hit = True
                        break
                    parent = os.path.dirname(k)
                    if parent == k:
                        break
                    k = parent
            if hit:
                f.meta[_ORG_PLAN_USER_PROTECT] = True
                n += 1
        except Exception:
            continue
    return n


def _org_plan_enforce_protect(files) -> None:
    """Sicherheitsregel: Geschütztes landet nie im Aufräumen-Vorschlag.
    Ausnahme: exaktes Duplikat (dup_of gesetzt, Original bleibt erhalten)."""
    for f in files:
        try:
            if f.in_project:
                continue
            if _org_plan_user_protected(f):
                if f.tier == ORG_JUNK or f.dup_of:
                    if f.dup_of:
                        f.add_reason("Inhaltsgleich mit %s" % f.dup_of)
                        orig = f.meta.get("orig_category") or ""
                        f.category = orig if orig and orig != "duplicate" else f.category
                        if f.category == "duplicate":
                            f.category = ""
                        f.dup_of = ""
                        f.meta.pop(_ORG_PLAN_DUP_KEEPER, None)
                f.tier = ORG_PROTECT
                f.add_reason("Vom Nutzer geschützt")
                continue
            if f.tier == ORG_JUNK and not f.dup_of:
                was_protected = (_org_plan_tier_of(f.category) == ORG_PROTECT
                                 and f.category not in ("temp", "duplicate")) or \
                    bool(f.meta.get("_org_ph_protect")) or \
                    f.meta.get("orig_tier") == ORG_PROTECT
                if was_protected:
                    f.tier = ORG_REVIEW
                    f.add_reason("Sicherheitsprüfung: wichtige Datei – nicht zum Löschen "
                                 "vorgeschlagen")
            if f.tier == ORG_JUNK and f.dup_of and f.category != "duplicate":
                # Duplikat-Kennzeichen ohne Duplikat-Kategorie: lieber prüfen lassen
                f.tier = ORG_REVIEW
        except Exception:
            continue


def _org_plan_read_contents(plan, work, extract_text, progress, cancelled) -> None:
    stage = "Inhalte lesen"
    texts = []
    metas = []
    for f in work:
        if f.tier == ORG_JUNK:
            continue
        if f.kind == "document" and extract_text:
            texts.append(f)
        elif f.kind == "image" and (f.ext in ORG_PLAN_EXIF_EXTS or f.ext in ORG_PLAN_SIZE_EXTS):
            metas.append(f)
    # oberste Ebene zuerst (wird verschoben), kleine Dateien vor großen
    texts.sort(key=lambda x: (x.depth, x.size))
    total = len(texts) + len(metas)
    done = 0
    tick = [time.monotonic()]       # langsame PDFs: Fortschritt auch zeitgesteuert melden
    _org_plan_call_progress(progress, stage, 0, total)
    read = skipped = 0
    deadline = time.monotonic() + ORG_PLAN_TEXT_BUDGET
    for f in texts:
        _org_plan_check(cancelled)
        if time.monotonic() > deadline or getattr(f, "cloud", False):
            skipped += 1                    # Zeitbudget erschöpft / nur online → nur Dateiname
            f.meta["text_skipped"] = True
        else:
            try:
                f.text = org_extract_text(f.path, ORG_PLAN_TEXT_MAX_CHARS) or ""
            except Exception:
                f.text = ""
            read += 1
        done += 1
        if done % ORG_PLAN_PROGRESS_EVERY == 0 or _org_plan_due(tick):
            _org_plan_call_progress(progress, stage, done, total)
    meta_read = meta_skipped = 0
    deadline = time.monotonic() + ORG_PLAN_META_BUDGET
    for f in metas:
        _org_plan_check(cancelled)
        if time.monotonic() > deadline or getattr(f, "cloud", False):
            meta_skipped += 1
        else:
            meta_read += 1
            exif = {}
            if f.ext in ORG_PLAN_EXIF_EXTS:
                try:
                    exif = org_read_exif(f.path) or {}
                except Exception:
                    exif = {}
                if exif:
                    f.meta["exif"] = exif
            if f.ext in ORG_PLAN_SIZE_EXTS and not (exif.get("width") and exif.get("height")):
                try:
                    sz = org_image_size(f.path)
                except Exception:
                    sz = None
                if sz:
                    f.meta["size"] = sz
        done += 1
        if done % ORG_PLAN_PROGRESS_EVERY == 0 or _org_plan_due(tick):
            _org_plan_call_progress(progress, stage, done, total)
    _org_plan_call_progress(progress, stage, total, total)
    plan.stats.update({"text_read": read, "text_skipped": skipped,
                       "meta_read": meta_read, "meta_skipped": meta_skipped})


def _org_plan_reason_text(label: str, name: str, terms: list, has_text: bool) -> str:
    """„Erkannt: Lebenslauf & Bewerbung (Dateiname: lebenslauf; Inhalt: berufserfahrung)“."""
    in_name, in_text = [], []
    try:
        compact = org_normalize(name).replace(" ", "")
        for t in terms or ():
            tn = org_normalize(str(t)).replace(" ", "")
            if tn and tn in compact:
                in_name.append(str(t))
            else:
                in_text.append(str(t))
    except Exception:
        in_name = [str(t) for t in (terms or [])]
    if not has_text:
        in_name, in_text = in_name + in_text, []
    parts = []
    if in_name or not in_text:
        parts.append("Dateiname" + (": " + ", ".join(in_name[:2]) if in_name else ""))
    if in_text:
        parts.append("Inhalt: " + ", ".join(in_text[:2]))
    return "Erkannt: %s (%s)" % (label, "; ".join(parts))


def _org_plan_classify(work, progress, cancelled) -> None:
    stage = "Einordnen"
    total = len(work)
    _org_plan_call_progress(progress, stage, 0, total)
    # 1) Dokumente, dazu Bilder und Archive mit Dokument-Namen („Personalausweis.jpg“,
    #    „Zeugnisse.zip“) – vor den Fotos, damit „Screenshot Zeugnis.png“ ein Zeugnis bleibt.
    for i, f in enumerate(work):
        if i % ORG_PLAN_PROGRESS_EVERY == 0:
            _org_plan_check(cancelled)
            _org_plan_call_progress(progress, stage, i, total * 2)
        try:
            if f.tier == ORG_JUNK:
                continue
            if f.kind == "document":
                pass
            elif f.kind in ORG_PLAN_NAME_ONLY_KINDS:
                if f.category:
                    continue
            else:
                continue
            pre_tier = f.tier
            cat, score, terms, year = org_classify_document(
                f.name, f.text if f.kind == "document" else "", f.kind)
            if f.kind != "document" and cat in _ORG_PLAN_NAME_ONLY_DENY:
                cat = None
            if cat is None or cat not in ORG_CATEGORIES:
                if f.kind != "document":
                    continue            # Bild → Fotoprüfung, Archiv → Rückfall „Archive“
                f.category = "document"
                f.score = 0.3
                f.add_reason("Dokument ohne eindeutige Zuordnung")
                if year:
                    f.year = year
            else:
                f.category = cat
                f.score = float(score or 0.0)
                if year:
                    f.year = year
                label = ORG_CATEGORIES[cat].get("label", cat)
                f.add_reason(_org_plan_reason_text(label, f.name, terms, bool(f.text)))
            f.tier = _org_plan_tier_of(f.category)
            if pre_tier == ORG_REVIEW:
                # Hinweis einer Vorprüfung bleibt bestehen – auch gegen „Wichtig“: bei
                # Dokumenten kommt er nur von Sperrdatei-Namen („~$Lebenslauf.docx“, zu groß
                # für eine echte Sperrdatei); so etwas ist kein geschützter Lebenslauf.
                f.tier = ORG_REVIEW
        except Exception:
            continue
    # 2) Fotos & Videos
    _org_plan_check(cancelled)
    try:
        org_classify_media(work)
    except Exception:
        pass
    # 3) Rückfall nach Dateiart
    for i, f in enumerate(work):
        if i % ORG_PLAN_PROGRESS_EVERY == 0:
            _org_plan_check(cancelled)
            _org_plan_call_progress(progress, stage, total + i, total * 2)
        try:
            if f.tier == ORG_JUNK or f.category:
                if not f.tier and f.category:
                    f.tier = _org_plan_tier_of(f.category)
                if f.category == "installer" and not f.score:
                    f.score = 0.5
                continue
            cat, score = ORG_PLAN_KIND_FALLBACK.get(f.kind, ("other", 0.3))
            f.category = cat
            f.score = max(float(f.score or 0.0), score)
            if not f.tier:
                f.tier = _org_plan_tier_of(cat)
            if f.kind == "installer" and not f.reasons:
                f.add_reason("Programmdatei")
        except Exception:
            continue
    _org_plan_call_progress(progress, stage, total * 2, total * 2)


def _org_plan_exists(f) -> bool:
    try:
        return os.path.isfile(str(f.path))
    except Exception:
        return False


def _org_plan_is_protected(f) -> bool:
    return f.tier == ORG_PROTECT or _org_plan_user_protected(f)


def _org_plan_keeper_rank(f) -> int:
    """Vorrang beim Behalten exakter Kopien (kleiner = besser):
    0 geschütztes Dokument (Ausweis, Zeugnis …) / vom Nutzer geschützt,
    1 geschütztes Foto/Video, 2 „Behalten“, 3 „Prüfen“, 4 Rest.
    So bleibt „Personalausweis.jpg“ statt „IMG_4567.JPG“ und „Rechnung_Amazon.pdf“
    statt „download.pdf“ – der aussagekräftige Name samt passendem Zielordner."""
    try:
        if _org_plan_user_protected(f):
            return 0
        if f.tier == ORG_PROTECT:
            return 1 if f.category in ORG_MEDIA_CATEGORIES else 0
        if f.tier == ORG_KEEP:
            return 2
        if f.tier == ORG_REVIEW:
            return 3
    except Exception:
        pass
    return 4


def _org_plan_mark_duplicates(groups) -> list:
    """Wie org_mark_duplicates, aber: enthält eine Gruppe geschützte Dateien (Lebenslauf,
    Fotos …), wird eine davon behalten (Dokument vor Foto, dann „Behalten“ vor „Prüfen“,
    siehe _org_plan_keeper_rank). Liefert markierte Gruppen [Original, Kopien…]."""
    out = []
    for g in groups or ():
        try:
            members = [m for m in g if m is not None]
            if len(members) < 2:
                continue
            best = min(_org_plan_keeper_rank(m) for m in members)
            pool = [m for m in members if _org_plan_keeper_rank(m) == best] or members
            keeper = org_pick_keeper(pool)
            if keeper is None:
                continue
            if not _org_plan_exists(keeper):
                existing = [m for m in pool if _org_plan_exists(m)] or \
                    [m for m in members if _org_plan_exists(m)]
                if not existing:
                    continue
                keeper = org_pick_keeper(existing)
                if keeper is None:
                    continue
            k_key = _org_plan_key(keeper.path)
            k_hash = str((keeper.meta or {}).get("hash") or "")
            marked = []
            for f in members:
                try:
                    if f is keeper or f.in_project or _org_plan_key(f.path) == k_key:
                        continue
                    if f.size != keeper.size:
                        continue
                    h = str((f.meta or {}).get("hash") or "")
                    if h and k_hash and h != k_hash:
                        continue
                    if _org_plan_user_protected(f):
                        f.add_reason("Inhaltsgleich mit %s" % keeper.rel)
                        continue
                    f.meta["orig_category"] = f.category
                    f.meta["orig_tier"] = f.tier
                    f.meta[_ORG_PLAN_DUP_KEEPER] = keeper
                    f.add_reason("Exakte Kopie von %s" % keeper.rel)
                    f.dup_of = keeper.rel
                    f.category = "duplicate"
                    f.tier = ORG_JUNK
                    f.score = 1.0
                    f.version_group = ""
                    if keeper.in_project:
                        # Original liegt nur in einem Projekt-/verknüpften Ordner: solche
                        # Dateien entstehen/verschwinden mit dem Projekt (Build, „git clean“)
                        # → lose Kopie nicht zum Löschen vorschlagen, nur prüfen lassen.
                        f.tier = ORG_REVIEW
                        f.score = 0.5
                        f.add_reason("Original liegt in einem Projektordner – Kopie nicht "
                                     "automatisch löschen")
                    marked.append(f)
                except Exception:
                    continue
            if marked:
                keeper.add_reason("Original (%d Kopie%s gefunden)"
                                  % (len(marked), "" if len(marked) == 1 else "n"))
                out.append([keeper] + marked)
        except Exception:
            continue
    return out


def _org_plan_event_part(f) -> str:
    ev = (f.event or "").strip()
    if ev:
        return ev
    try:
        dt = org_photo_dt(f)
        if dt is not None:
            return "%04d-%02d %s" % (dt.year, dt.month, org_month_name(dt.month))
    except Exception:
        pass
    return "Ohne Datum"


def _org_plan_root_alias(root: Path) -> str:
    try:
        n = _org_plan_fold(root.name)
    except Exception:
        return ""
    for key, names in ORG_PLAN_ROOT_ALIASES.items():
        if n in names:
            return key
    return ""


def _org_plan_folder_for(f, alias: str = "") -> str:
    """Zielordner (relativ, "/"), Platzhalter aufgelöst; "" = bleibt."""
    cat = f.category
    if cat == "duplicate":
        cat = f.meta.get("orig_category") or ""
    folder = ORG_CATEGORIES.get(cat, {}).get("folder", "") if cat else ""
    if not folder:
        return ""
    if "{year}" in folder:
        try:
            year = str(int(org_year_of(f)))
        except Exception:
            year = "Ohne Jahr"
        folder = folder.replace("{year}", year)
    if "{event}" in folder:
        folder = folder.replace("{event}", _org_plan_event_part(f).replace("/", "-"))
    parts = [_org_plan_sanitize_part(p) for p in folder.split("/")]
    parts = [p for p in parts if p]
    if alias and parts and parts[0].casefold() == alias:
        parts = parts[1:]
    return "/".join(parts)


def _org_plan_static_prefix(f, alias: str = "") -> str:
    """Fester Anfang des Zielordners vor dem ersten Platzhalter, z. B. „Bilder/Fotos“
    für „Bilder/Fotos/{year}/{event}“ oder „Dokumente/Steuer“; "" ohne Platzhalter."""
    cat = f.category
    if cat == "duplicate":
        cat = f.meta.get("orig_category") or ""
    folder = ORG_CATEGORIES.get(cat, {}).get("folder", "") if cat else ""
    if "{" not in folder:
        return ""
    head = folder.split("{", 1)[0]
    parts = [_org_plan_sanitize_part(p) for p in head.split("/")]
    parts = [p for p in parts if p]
    if alias and parts and parts[0].casefold() == alias:
        parts = parts[1:]
    return "/".join(parts)


def _org_plan_dir_set(files) -> set:
    dirs = set()
    for f in files:
        parts = f.rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            d = "/".join(parts[:i])
            if d in dirs:
                # Eltern schon erfasst – trotzdem tiefere Ebenen ergänzen
                continue
            dirs.add(d)
    return {_org_plan_fold(d) for d in dirs}


def _org_plan_web_dirs(f, dirs: set) -> list:
    """Zu einer gespeicherten Webseite gehörende Ordner ("Seite_files"), gefaltet."""
    if f.ext not in ORG_PLAN_WEB_EXTS:
        return []
    parent, _, name = f.rel.rpartition("/")
    stem = _org_plan_split_name(name)[0]
    prefix = (parent + "/") if parent else ""
    out = []
    for suf in ORG_PLAN_WEB_DIR_SUFFIXES:
        d = _org_plan_fold(prefix + stem + suf)
        if d in dirs:
            out.append(d)
    return out


def _org_plan_program_dirs(files) -> list:
    """Ordner (rel), die wie ein Programm aussehen (.exe + Bibliotheken) → nicht zerlegen."""
    lib_dirs = set()
    exe_dirs = set()
    for f in files:
        parent = f.rel.rpartition("/")[0]
        if not parent:
            continue
        if f.ext in ORG_PLAN_PROGRAM_LIB_EXTS:
            lib_dirs.add(parent)
        elif f.ext == ".exe":
            exe_dirs.add(parent)
    pinned = set()
    for d in lib_dirs:
        top = d
        parts = d.split("/")
        # Starter eine oder zwei Ebenen höher (z. B. Spiel/bin/x64/*.dll, Spiel/Spiel.exe)
        for up in range(0, min(3, len(parts))):
            cand = "/".join(parts[:len(parts) - up])
            if cand in exe_dirs:
                top = cand
        if d in exe_dirs or top != d:
            pinned.add(_org_plan_fold(top))
    return sorted(pinned)


def _org_plan_under(rel_fold: str, prefixes) -> bool:
    for p in prefixes:
        if rel_fold.startswith(p + "/"):
            return True
    return False


def _org_plan_checked(f) -> bool:
    try:
        score = float(f.score or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if f.tier in (ORG_PROTECT, ORG_KEEP) and score >= ORG_PLAN_CHECK_SCORE:
        return True
    if f.category in ORG_MEDIA_CATEGORIES and f.tier != ORG_JUNK and \
            score >= ORG_PLAN_MEDIA_CHECK_SCORE:
        return True
    return False


def _org_plan_build_moves(plan, reorganize_subfolders, progress, cancelled) -> list:
    stage = "Plan erstellen"
    files = plan.files
    total = len(files)
    _org_plan_call_progress(progress, stage, 0, total)
    alias = _org_plan_root_alias(plan.root)
    dirs = _org_plan_dir_set(files)
    prog_dirs = _org_plan_program_dirs(files) if reorganize_subfolders else []
    web_dirs = {}
    for f in files:
        for d in _org_plan_web_dirs(f, dirs):
            web_dirs[d] = f.rel
    if reorganize_subfolders and web_dirs:
        prog_dirs = list(prog_dirs) + sorted(web_dirs)
    # Projekt- und verknüpfte Ordner sind tabu – auch als Ziel (z. B. „Projekte“ = Git-Repo)
    taboo = {_org_plan_fold(p) for p in (plan.projects or []) if p and p != "."}
    taboo |= {_org_plan_fold(d) for d in plan.stats.get("link_dirs", []) or []}
    moves = []
    by_file = {}
    for i, f in enumerate(files):
        if i % ORG_PLAN_PROGRESS_EVERY == 0:
            _org_plan_check(cancelled)
            _org_plan_call_progress(progress, stage, i, total)
        try:
            f.target = ""
            if f.in_project or f.tier == ORG_JUNK or f.dup_of:
                continue
            if f.depth != 0 and not reorganize_subfolders:
                continue
            if f.version_group and not f.version_latest:
                continue                    # folgt unten der neuesten Version
            parent = f.rel.rpartition("/")[0]
            if prog_dirs and _org_plan_under(_org_plan_fold(f.rel), prog_dirs):
                f.add_reason("Gehört zu einem Programm- oder Webseitenordner – bleibt, "
                             "wo es ist")
                continue
            if _org_plan_web_dirs(f, dirs):
                f.add_reason("Gespeicherte Webseite – bleibt bei ihrem Ordner")
                continue
            dest = _org_plan_folder_for(f, alias)
            if not dest:
                continue
            pf, df = _org_plan_fold(parent), _org_plan_fold(dest)
            if pf == df or pf.startswith(df + "/"):
                continue                    # liegt schon richtig
            # Liegt schon im richtigen Bereich mit eigener Unterteilung („Fotos/2023/
            # Italien“, „Steuer/Alt“): eigene Ordnernamen des Nutzers nicht zerlegen und
            # bei erneuter Analyse nicht hin- und herschieben (Ereignisse/Jahre ändern sich).
            sp = _org_plan_fold(_org_plan_static_prefix(f, alias))
            if sp and pf.startswith(sp + "/"):
                continue
            if taboo and (df in taboo or _org_plan_under(df, taboo)):
                f.add_reason("Zielordner „%s“ ist ein Projektordner – bleibt, wo es ist" % dest)
                continue
            mv = OrgMove(file=f, dest_rel=dest, checked=_org_plan_checked(f))
            f.target = dest
            moves.append(mv)
            by_file[id(f)] = mv
        except Exception:
            continue
    # ältere Versionen ziehen mit der neuesten Version um (bleiben nebeneinander)
    for key, members in (plan.versions or {}).items():
        try:
            latest = [m for m in members if m.version_latest]
            lead = by_file.get(id(latest[0])) if latest else None
            if lead is None:
                continue
            for m in members:
                if m is latest[0] or m.version_latest or m.in_project or \
                        m.tier == ORG_JUNK or m.dup_of or id(m) in by_file:
                    continue
                mv = OrgMove(file=m, dest_rel=lead.dest_rel, checked=lead.checked)
                m.target = lead.dest_rel
                moves.append(mv)
                by_file[id(m)] = mv
        except Exception:
            continue
    moves.sort(key=lambda mv: (mv.dest_rel.casefold(), mv.file.rel.casefold()))
    _org_plan_call_progress(progress, stage, total, total)
    return moves


def _org_plan_finish_lists(plan) -> None:
    files = plan.files
    plan.junk = [f for f in files if f.tier == ORG_JUNK and not f.in_project]
    plan.protect = [f for f in files if f.tier == ORG_PROTECT and not f.in_project]
    plan.review = [f for f in files if f.tier == ORG_REVIEW and not f.in_project]


def _org_plan_stats(plan, t0: float) -> None:
    files = plan.files
    st = plan.stats

    def _sz(f):
        try:
            return max(0, int(f.size))
        except Exception:
            return 0

    dups = [f for f in plan.junk if f.dup_of]
    st.update({
        "files": len(files),
        "bytes": sum(_sz(f) for f in files),
        "junk_bytes": sum(_sz(f) for f in plan.junk),
        "dup_bytes": sum(_sz(f) for f in dups),
        "junk_count": len(plan.junk),
        "dup_count": len(dups),
        "protect_count": len(plan.protect),
        "move_count": len(plan.moves),
        "review_count": len(plan.review),
        "version_count": sum(1 for g in plan.versions.values() for f in g
                             if not f.version_latest),
        "project_count": len(plan.projects),
        "empty_dir_count": len(plan.empty_dirs),
        "truncated": bool(plan.truncated),
    })
    st["seconds"] = round(time.monotonic() - t0, 3)


def org_analyze(root: Path, recursive: bool = True, reorganize_subfolders: bool = False,
                programs=None, progress=None, cancelled=lambda: False, now=None,
                extract_text: bool = True, protected_paths=None) -> OrgPlan:
    """Analysiert root und liefert einen Plan. Verändert keine Dateien.

    Reihenfolge / Vorrang:
     1. Erfassen (org_walk). Dateien in Projektordnern: nur gemeldet, Kategorie "code",
        Stufe „Behalten“, nie verschoben/gelöscht/versioniert. Ist die Wurzel selbst
        (oder ein Elternordner) ein Projekt, gilt das für alle Dateien.
     2. Reste (org_detect_junk) und Installer (org_mark_installers) – VOR der Einordnung,
        damit „Lebenslauf.pdf.crdownload“ oder eine leere IMG_1.JPG nicht geschützt werden.
     3. Inhalte lesen: Text der Dokumente (Zeitbudget ORG_PLAN_TEXT_BUDGET, danach nur
        Dateinamen), EXIF und Bildgröße der Bilder (Budget ORG_PLAN_META_BUDGET).
     4. Einordnen: Dokumente und Bilder mit Dokument-Namen (org_classify_document),
        danach Fotos/Videos (org_classify_media), sonst Rückfall nach Dateiart.
     5. Exakte Duplikate – geschützte Exemplare werden bevorzugt behalten.
     6. Versionen (nur Nicht-Müll), ältere → „Prüfen“.
     7. Fotos zu Ereignissen gruppieren.
     8. Schutzregel: Geschütztes nie im Aufräumen-Vorschlag (außer exakte Kopie).
     9. Zielordner/Verschiebungen (nur oberste Ebene, außer reorganize_subfolders).
    10. Leere Ordner, Statistik.
    progress(stufe, erledigt, gesamt); cancelled() wird regelmäßig geprüft."""
    t0 = time.monotonic()
    try:
        root_p = Path(root)
    except Exception:
        root_p = Path(".")
    plan = OrgPlan(root=root_p)
    try:
        _org_plan_run(plan, recursive, reorganize_subfolders, programs, progress,
                      cancelled, now, extract_text, protected_paths)
    except _org_PlanCancelled:
        plan.stats["cancelled"] = True
        plan.moves, plan.junk, plan.dup_groups, plan.versions = [], [], [], {}
        plan.empty_dirs = []
        for f in plan.files:
            f.target = ""
    except Exception as e:                      # nie abstürzen: nur Bericht, keine Aktionen
        plan.stats["error"] = "Analyse unvollständig: %s" % (e,)
        plan.moves, plan.junk, plan.dup_groups, plan.empty_dirs = [], [], [], []
        for f in plan.files:
            f.target = ""
    try:
        if "cancelled" in plan.stats or "error" in plan.stats:
            plan.protect = [f for f in plan.files if f.tier == ORG_PROTECT]
            plan.review = [f for f in plan.files if f.tier == ORG_REVIEW]
        _org_plan_stats(plan, t0)
    except Exception:
        plan.stats["seconds"] = round(time.monotonic() - t0, 3)
    return plan


def _org_plan_run(plan, recursive, reorganize_subfolders, programs, progress, cancelled,
                  now, extract_text, protected_paths) -> None:
    if now is None:
        now = time.time()
    # ---- 1. Erfassen ----
    _org_plan_call_progress(progress, "Dateien erfassen", 0, 0)
    root = plan.root
    if not root.is_dir():
        plan.stats["error"] = "Ordner nicht gefunden: %s" % root
        return
    root = root.resolve()
    plan.root = root
    files, projects, truncated = org_walk(root, bool(recursive),
                                          cancelled=_org_plan_walk_cancelled(progress,
                                                                             cancelled))
    _org_plan_check(cancelled)
    plan.files, plan.projects, plan.truncated = files, list(projects), bool(truncated)
    _org_plan_call_progress(progress, "Dateien erfassen", len(files), len(files))
    if _org_plan_root_is_project(root):
        plan.stats["root_is_project"] = True
        plan.projects.insert(0, ".")
        for f in files:
            f.in_project = True
    # Verknüpfte Unterordner (Windows-Junctions/Symlinks, die org_walk betritt): deren
    # Inhalt liegt evtl. ganz woanders → wie Projektdateien nie verschieben/löschen.
    link_dirs = []
    if not plan.stats.get("root_is_project"):
        link_dirs = _org_plan_link_dirs(root, files)
    plan.stats["link_dirs"] = list(link_dirs)
    if link_dirs:
        link_pre = [_org_plan_fold(d) for d in link_dirs]
        for f in files:
            if not f.in_project and _org_plan_under(_org_plan_fold(f.rel), link_pre):
                f.in_project = True
                f.meta[_ORG_PLAN_LINKED] = True
    proj_set = set(plan.projects)
    for f in files:
        if f.in_project:
            f.category = "code"
            f.tier = ORG_KEEP
            f.score = 1.0
            f.target = ""
            if f.meta.get(_ORG_PLAN_LINKED):
                f.add_reason("Liegt in einem verknüpften Ordner (Junction/Symlink) – "
                             "wird nicht verändert")
                continue
            proj = _org_plan_project_of(f.rel, proj_set) or "."
            f.add_reason("Teil des Projekts „%s“ – wird nicht verändert"
                         % (root.name if proj == "." else proj))
    work = [f for f in files if not f.in_project]
    n_user = _org_plan_apply_user_protect(work, protected_paths, _org_plan_key(root))
    plan.stats["user_protected"] = n_user

    # ---- 2. Reste & Installer ----
    org_detect_junk(work, now)
    if programs is None:
        try:
            programs = org_installed_programs()
        except Exception:
            programs = []
    org_mark_installers(work, list(programs or []), now)
    _org_plan_enforce_protect(work)
    _org_plan_check(cancelled)

    # ---- 3. Inhalte lesen ----
    _org_plan_read_contents(plan, work, extract_text, progress, cancelled)

    # ---- 4. Einordnen ----
    _org_plan_classify(work, progress, cancelled)
    for f in work:
        if _org_plan_user_protected(f):
            f.tier = ORG_PROTECT

    # ---- 5. Exakte Duplikate (inkl. Projektdateien als mögliche Originale) ----
    stage = "Duplikate suchen"
    _org_plan_call_progress(progress, stage, 0, 0)
    cands = [f for f in files if f.tier != ORG_JUNK]
    groups = org_find_duplicates(
        cands, cancelled=lambda: _org_plan_is_cancelled(cancelled),
        progress=lambda d, t: _org_plan_call_progress(progress, stage, d, t))
    _org_plan_check(cancelled)
    plan.dup_groups = _org_plan_mark_duplicates(groups)

    # ---- 6. Versionen ----
    vgroups = org_version_groups([f for f in work if f.tier != ORG_JUNK])
    org_mark_versions(vgroups)
    plan.versions = {k: v for k, v in vgroups.items()
                     if any(not m.version_latest for m in v)}
    _org_plan_check(cancelled)

    # ---- 7. Fotos gruppieren ----
    _org_plan_call_progress(progress, "Fotos gruppieren", 0, 1)
    plan.stats["event_count"] = org_photo_events(work)
    _org_plan_call_progress(progress, "Fotos gruppieren", 1, 1)
    _org_plan_check(cancelled)

    # ---- 8. Schutzregel ----
    _org_plan_enforce_protect(work)

    # ---- 9. Plan ----
    plan.moves = _org_plan_build_moves(plan, reorganize_subfolders, progress, cancelled)

    # ---- 10. Leere Ordner, Listen ----
    empty = org_empty_dirs(root) if not plan.stats.get("root_is_project") else []
    if not recursive:
        rk = _org_plan_key(root)
        empty = [d for d in empty if _org_plan_key(Path(d).parent) == rk]
    # vom Nutzer geschützte und verknüpfte Ordner nie als „leer, löschen“ vorschlagen
    pkeys = _org_plan_protect_keys(protected_paths)
    rk = _org_plan_key(root)
    lkeys = {_org_plan_key(root.joinpath(*d.split("/"))) for d in link_dirs}
    if pkeys or lkeys:
        keep = []
        for d in empty:
            try:
                k = _org_plan_key(d)
                if _org_plan_key_protected(k, pkeys | lkeys, rk):
                    continue
            except Exception:
                continue
            keep.append(d)
        empty = keep
    plan.empty_dirs = list(empty)
    _org_plan_finish_lists(plan)


# ---------------------------------------------------------------------------
# Protokoll (Journal) – nach JEDER Verschiebung gültiges JSON auf der Platte
# ---------------------------------------------------------------------------
def _org_plan_load_journal(path: Path):
    """Liest ein Protokoll; repariert ein beim Absturz abgeschnittenes Ende."""
    try:
        raw = Path(path).read_bytes()
    except (OSError, TypeError, ValueError):
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    # Reparatur: Kopf lesen, dann Einträge einzeln bis zum ersten defekten
    try:
        idx = text.find('"moves"')
        if idx < 0:
            return None
        head = text[:idx].rstrip().rstrip(",") + "}"
        data = json.loads(head)
        if not isinstance(data, dict):
            return None
        pos = text.find("[", idx) + 1
        dec = json.JSONDecoder()
        moves = []
        n = len(text)
        while pos < n:
            while pos < n and text[pos] in " \r\n\t,":
                pos += 1
            if pos >= n or text[pos] == "]":
                break
            try:
                val, pos = dec.raw_decode(text, pos)
            except ValueError:
                break
            moves.append(val)
        data["moves"] = moves
        return data
    except Exception:
        return None


class _org_PlanJournal(object):
    """Hält die Protokolldatei: Kopf (root, created) + "moves"-Liste am Ende.
    Neue Verschiebungen werden angehängt (ohne die Datei neu zu schreiben);
    neue Ordner schreiben die Datei atomar neu (selten)."""

    def __init__(self, path: Path, root: Path, created=None, moves=None):
        self.path = Path(path)
        self.root = str(root)
        self.created = list(created or [])
        self.moves = list(moves or [])
        self.fh = None
        self.tail = 0
        self._rewrite()

    def _rewrite(self) -> None:
        if self.fh is not None:
            try:
                self.fh.close()
            except OSError:
                pass
            self.fh = None
        head = json.dumps({"root": self.root, "created": self.created}, ensure_ascii=False)
        body = head[:-1] + ', "moves": ['
        entries = ",\n".join(json.dumps(m, ensure_ascii=False) for m in self.moves)
        data = (body + ("\n" + entries if entries else "")).encode("utf-8")
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(str(tmp), "wb") as fh:
            fh.write(data + _ORG_PLAN_JOURNAL_TAIL)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(str(tmp), str(self.path))
        self.fh = open(str(self.path), "r+b")
        self.tail = len(data)

    def add_created(self, d: str) -> None:
        self.created.append(d)
        self._rewrite()

    def add_move(self, src: str, dst: str) -> int:
        """Hängt [src, dst] an; liefert die alte Endposition (für rollback)."""
        prev = self.tail
        entry = json.dumps([src, dst], ensure_ascii=False).encode("utf-8")
        data = (b",\n" if self.moves else b"\n") + entry
        self.fh.seek(prev)
        self.fh.write(data + _ORG_PLAN_JOURNAL_TAIL)
        self.fh.truncate()
        self.fh.flush()
        self.tail = prev + len(data)
        self.moves.append([src, dst])
        return prev

    def rollback(self, prev: int) -> None:
        """Letzten Eintrag zurücknehmen (Verschiebung ist gescheitert)."""
        self.moves.pop()
        self.fh.seek(prev)
        self.fh.write(_ORG_PLAN_JOURNAL_TAIL)
        self.fh.truncate()
        self.fh.flush()
        self.tail = prev

    def close(self) -> None:
        if self.fh is not None:
            try:
                self.fh.flush()
                os.fsync(self.fh.fileno())
            except (OSError, ValueError):
                pass
            try:
                self.fh.close()
            except OSError:
                pass
            self.fh = None


def _org_plan_safe_dest(root: Path, dest_rel):
    """Zielordner unter root oder None (Pfad-Ausbruch, ungültige Namen)."""
    if not isinstance(dest_rel, str):
        return None
    s = dest_rel.replace("\\", "/").strip()
    if s.startswith("/") or _ORG_PLAN_DRIVE_RE.match(s) or "\x00" in s:
        return None
    parts = [p for p in s.split("/") if p not in ("", ".")]
    for p in parts:
        if p == ".." or _ORG_PLAN_BAD_CHARS.search(p) or p != p.rstrip(". ") or \
                p.split(".")[0].lower() in _ORG_PLAN_RESERVED or p != p.strip():
            return None
    d = root.joinpath(*parts) if parts else root
    try:
        real = Path(os.path.realpath(str(d)))
    except (OSError, ValueError):
        return None
    if not _org_plan_inside(_org_plan_key(os.path.realpath(str(root))), _org_plan_key(real)):
        return None
    return real


def _org_plan_open_journal(journal_path: Path, root: Path):
    """Neues Protokoll oder Fortsetzung eines vorhandenen (gleicher Wurzelordner).
    Ein fremdes/defektes Protokoll wird nie überschrieben → (None, Meldung)."""
    jp = Path(journal_path)
    if os.path.lexists(str(jp)):
        data = _org_plan_load_journal(jp)
        if not data or _org_plan_key(str(data.get("root", ""))) != _org_plan_key(root):
            return None, "Protokolldatei existiert bereits und gehört nicht zu diesem Ordner: %s" % jp
        return _org_PlanJournal(jp, root, data.get("created") or [],
                                data.get("moves") or []), ""
    try:
        jp.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return _org_PlanJournal(jp, root), ""


def org_apply_moves(plan: OrgPlan, moves: list, journal_path: Path) -> tuple:
    """Verschiebt die gewählten Dateien. Nie überschreiben, nie aus plan.root hinaus.
    Das Protokoll ist nach jeder Verschiebung gültiges JSON:
    {"root": str, "created": [Ordner], "moves": [[alt, neu], …]}.
    Liefert (Anzahl verschoben, Fehlermeldungen)."""
    failures = []
    moved = 0
    try:
        root = Path(os.path.realpath(str(plan.root)))
        if not root.is_dir():
            return 0, ["Ordner nicht gefunden: %s" % plan.root]
        journal, msg = _org_plan_open_journal(Path(journal_path), root)
        if journal is None:
            return 0, [msg]
    except Exception as e:
        return 0, ["Protokoll kann nicht angelegt werden: %s" % (e,)]
    root_key = _org_plan_key(root)
    try:
        journal_keys = {_org_plan_real_key(journal.path),
                        _org_plan_real_key(journal.path.with_name(journal.path.name + ".tmp"))}
    except Exception:
        journal_keys = set()
    proj_cache = {}

    def _dest_in_project(dest_dir: Path) -> bool:
        # jeder vorhandene Ordner unterhalb von root bis zum Ziel: Projektordner?
        try:
            parts = dest_dir.relative_to(root).parts
        except ValueError:
            return True
        cur = root
        for part in parts:
            cur = cur / part
            k = _org_plan_key(cur)
            if k not in proj_cache:
                proj_cache[k] = os.path.isdir(str(cur)) and org_is_project_dir(cur)
            if proj_cache[k]:
                return True
        return False

    try:
        for m in list(moves or []):
            f = getattr(m, "file", None)
            rel = str(getattr(f, "rel", "") or "?")
            try:
                if f is None:
                    continue
                if getattr(f, "in_project", False):
                    failures.append("Projektdatei wird nicht verschoben: %s" % rel)
                    continue
                src = Path(f.path)
                if not os.path.isfile(str(src)) or os.path.islink(str(src)):
                    failures.append("Nicht mehr vorhanden: %s" % rel)
                    continue
                src_real = Path(os.path.realpath(str(src)))
                if not _org_plan_inside(root_key, _org_plan_key(src_real)):
                    failures.append("Liegt außerhalb des Ordners: %s" % rel)
                    continue
                if _org_plan_key(src_real) in journal_keys:
                    failures.append("Protokolldatei wird nicht verschoben: %s" % rel)
                    continue
                try:
                    st = os.stat(str(src))
                except OSError:
                    failures.append("Nicht lesbar: %s" % rel)
                    continue
                if not _org_plan_unchanged(f, st):
                    failures.append("Seit der Analyse verändert – nicht verschoben: %s" % rel)
                    continue
                dest_dir = _org_plan_safe_dest(root, getattr(m, "dest_rel", None))
                if dest_dir is None:
                    failures.append("Ungültiges Ziel abgelehnt: %s → %s"
                                    % (rel, getattr(m, "dest_rel", "")))
                    continue
                if _org_plan_key(src_real.parent) == _org_plan_key(dest_dir):
                    continue                        # liegt schon dort
                if _dest_in_project(dest_dir):
                    failures.append("Zielordner liegt in einem Projekt – nicht verschoben: "
                                    "%s → %s" % (rel, m.dest_rel))
                    continue
                # Ordner anlegen (von oben nach unten), jeden neuen protokollieren
                rel_parts = dest_dir.relative_to(root).parts if dest_dir != root else ()
                cur = root
                ok = True
                for part in rel_parts:
                    cur = cur / part
                    if os.path.isdir(str(cur)):
                        continue
                    if os.path.lexists(str(cur)):
                        ok = False
                        break
                    journal.add_created(str(cur))
                    os.mkdir(str(cur))
                if not ok:
                    failures.append("Zielordner ist eine Datei: %s" % m.dest_rel)
                    continue
                # nach dem Anlegen erneut prüfen (Verknüpfungen)
                if not _org_plan_inside(root_key, _org_plan_key(os.path.realpath(str(dest_dir)))):
                    failures.append("Ungültiges Ziel abgelehnt: %s" % m.dest_rel)
                    continue
                target = _org_plan_free_name(dest_dir, src.name)
                # echter Ort ins Protokoll (wie root/target), sonst lehnt org_undo den
                # Eintrag bei einem Wurzelordner hinter einer Verknüpfung als „außerhalb“ ab
                prev = journal.add_move(str(src_real), str(target))
                try:
                    _org_plan_rename_noreplace(src, target)
                except FileExistsError:
                    journal.rollback(prev)
                    target = _org_plan_free_name(dest_dir, src.name)   # zweiter Versuch
                    prev = journal.add_move(str(src_real), str(target))
                    try:
                        _org_plan_rename_noreplace(src, target)
                    except OSError as e:
                        journal.rollback(prev)
                        failures.append("Verschieben fehlgeschlagen: %s (%s)" % (rel, e))
                        continue
                except OSError as e:
                    journal.rollback(prev)
                    failures.append("Verschieben fehlgeschlagen: %s (%s)" % (rel, e))
                    continue
                moved += 1
                try:
                    new_rel = target.relative_to(root).as_posix()
                    f.path = target
                    f.rel = new_rel
                    f.depth = new_rel.count("/")
                    f.target = ""
                except Exception:
                    pass
            except Exception as e:
                failures.append("Verschieben fehlgeschlagen: %s (%s)" % (rel, e))
    finally:
        journal.close()
    return moved, failures


def _org_plan_undone_name(jp: Path) -> Path:
    name = jp.name
    base = name[:-5] if name.lower().endswith(".json") else name
    cand = jp.with_name(base + ".undone.json")
    k = 2
    while os.path.lexists(str(cand)):
        cand = jp.with_name("%s.undone (%d).json" % (base, k))
        k += 1
    return cand


def org_undo(journal_path: Path) -> tuple:
    """Macht die Verschiebungen eines Protokolls rückgängig (in umgekehrter Reihenfolge).
    Liefert (Anzahl wiederhergestellt, Meldungen)."""
    msgs = []
    restored = 0
    try:
        jp = Path(journal_path)
        data = _org_plan_load_journal(jp)
        if not data or not isinstance(data.get("moves", []), list):
            return 0, ["Protokoll nicht lesbar: %s" % journal_path]
        root = str(data.get("root") or "")
        if not root:
            return 0, ["Protokoll ohne Ordnerangabe: %s" % journal_path]
        root_key = _org_plan_key(os.path.realpath(root))
    except Exception as e:
        return 0, ["Protokoll nicht lesbar: %s (%s)" % (journal_path, e)]
    remaining = []
    for entry in reversed(list(data.get("moves") or [])):
        try:
            if not (isinstance(entry, (list, tuple)) and len(entry) >= 2 and
                    isinstance(entry[0], str) and isinstance(entry[1], str)):
                msgs.append("Ungültiger Protokolleintrag übersprungen")
                continue
            src, dst = entry[0], entry[1]
            name = os.path.basename(dst)
            # echter Ort zählt: Ordner, die inzwischen Verknüpfungen nach außen sind,
            # werden abgelehnt (weder von außen holen noch nach außen schieben)
            if not (_org_plan_inside(root_key, _org_plan_key(os.path.abspath(src))) and
                    _org_plan_inside(root_key, _org_plan_key(os.path.abspath(dst))) and
                    _org_plan_inside(root_key, _org_plan_real_key(src)) and
                    _org_plan_inside(root_key, _org_plan_real_key(dst))):
                msgs.append("Eintrag außerhalb des Ordners übersprungen: %s" % name)
                continue
            if not os.path.lexists(dst):
                if os.path.lexists(src):
                    continue            # Verschiebung fand nie statt (Absturz davor)
                msgs.append("„%s“ wurde inzwischen verschoben oder gelöscht – übersprungen"
                            % name)
                continue
            if not os.path.isfile(dst) or os.path.islink(dst):
                msgs.append("„%s“ ist keine Datei mehr – übersprungen" % name)
                continue
            src_p = Path(src)
            parent = src_p.parent
            if os.name != "nt" and os.path.isfile(src) and not os.path.islink(src):
                # Absturz zwischen „link“ und „unlink“: beide Namen zeigen auf dieselben
                # Daten → nur den zusätzlichen Namen entfernen (Inode geprüft, nie 0)
                try:
                    s1, s2 = os.stat(src), os.stat(dst)
                    same = bool(s1.st_ino) and (s1.st_dev, s1.st_ino) == (s2.st_dev, s2.st_ino)
                except OSError:
                    same = False
                if same:
                    os.unlink(dst)
                    restored += 1
                    continue
            if not os.path.isdir(str(parent)):
                os.makedirs(str(parent), exist_ok=True)
            target = src_p
            if os.path.lexists(src):
                target = _org_plan_free_name(parent, src_p.name, "wiederhergestellt")
                msgs.append("„%s“ war belegt – wiederhergestellt als „%s“"
                            % (src_p.name, target.name))
            try:
                _org_plan_rename_noreplace(dst, target)
            except FileExistsError:
                target = _org_plan_free_name(parent, src_p.name, "wiederhergestellt")
                _org_plan_rename_noreplace(dst, target)
            restored += 1
        except Exception as e:
            remaining.append(list(entry[:2]) if isinstance(entry, (list, tuple)) else entry)
            msgs.append("Wiederherstellen fehlgeschlagen: %s (%s)"
                        % (os.path.basename(str(entry[1])) if isinstance(entry, (list, tuple))
                           and len(entry) > 1 else "?", e))
    # angelegte Ordner entfernen, wenn leer (tiefste zuerst)
    created = [d for d in (data.get("created") or []) if isinstance(d, str)]
    for d in sorted(created, key=len, reverse=True):
        try:
            if not _org_plan_inside(root_key, _org_plan_key(os.path.abspath(d))) or \
                    _org_plan_key(os.path.abspath(d)) == root_key:
                continue
            if os.path.isdir(d) and not os.path.islink(d) and not os.listdir(d):
                os.rmdir(d)
        except OSError:
            continue
    try:
        if remaining:
            remaining.reverse()
            j = _org_PlanJournal(jp, Path(root), data.get("created") or [], remaining)
            j.close()
            msgs.append("Protokoll bleibt für einen weiteren Versuch erhalten")
        else:
            os.replace(str(jp), str(_org_plan_undone_name(jp)))
    except Exception as e:
        msgs.append("Protokoll konnte nicht abgeschlossen werden: %s" % (e,))
    return restored, msgs


# ---------------------------------------------------------------------------
# Papierkorb
# ---------------------------------------------------------------------------
def _org_plan_keeper_path(f):
    k = None
    try:
        k = f.meta.get(_ORG_PLAN_DUP_KEEPER)
    except Exception:
        k = None
    if k is not None:
        try:
            return str(k.path)
        except Exception:
            pass
    root = _org_plan_root_of(f)
    if root is None or not f.dup_of:
        return None
    return str(root.joinpath(*[p for p in str(f.dup_of).replace("\\", "/").split("/") if p]))


def _org_plan_important(f) -> bool:
    """Wichtige Datei (auch wenn die Stufe herabgesetzt wurde, z. B. „Ältere Version“
    eines Zeugnisses oder Sicherheitsprüfung)?"""
    try:
        if getattr(f, "tier", "") == ORG_PROTECT:
            return True
        cat = getattr(f, "category", "") or ""
        if cat not in ("temp", "duplicate") and _org_plan_tier_of(cat) == ORG_PROTECT:
            return True
        meta = getattr(f, "meta", None) or {}
        return bool(meta.get("_org_ph_protect")) or meta.get("orig_tier") == ORG_PROTECT
    except Exception:
        return True


def _org_plan_dup_ok(f, p: str, cur_size: int, trashed: set):
    """Darf die Kopie f (Pfad p) weg? → (ok, Schlüssel des Originals)."""
    kp = _org_plan_keeper_path(f)
    if not kp:
        return False, ""
    kk = _org_plan_key(kp)
    if kk == _org_plan_key(p) or kk in trashed:
        return False, kk
    if not os.path.isfile(kp) or os.path.islink(kp):
        return False, kk
    try:
        kst = os.stat(kp)
        if kst.st_size != cur_size:
            return False, kk
        if os.path.samefile(kp, p):          # derselbe Datenbestand (Link) → nie löschen
            return False, kk
    except OSError:
        return False, kk
    kobj = None
    try:
        kobj = f.meta.get(_ORG_PLAN_DUP_KEEPER)
    except Exception:
        kobj = None
    if kobj is not None and _org_plan_unchanged(kobj, kst):
        return True, kk
    # Original unbekannt oder seit der Analyse verändert → Inhalt Byte für Byte vergleichen
    h1 = _org_plan_hash_file(p)[0]
    h2 = _org_plan_hash_file(kp)[0]
    return bool(h1) and h1 == h2, kk


def org_trash(files: list, trash_fn) -> tuple:
    """Verschiebt Dateien mit trash_fn(pfad) -> bool in den Papierkorb.
    Verweigert: Geschütztes (außer exakte Kopie, deren Original noch da und unverändert
    ist), Projektdateien, verschwundene oder seit der Analyse veränderte Dateien,
    Dateien außerhalb des analysierten Ordners (Verknüpfungen) und das Original von
    Kopien, die im selben Aufruf gelöscht wurden. Liefert (Anzahl, Fehlermeldungen)."""
    count = 0
    fails = []
    trashed = set()
    needed = set()          # Originale bereits gelöschter Kopien → müssen bleiben
    try:
        items = list(files or [])
    except Exception:
        return 0, ["Ungültige Dateiliste"]
    for f in items:
        rel = str(getattr(f, "rel", "") or getattr(f, "path", "?"))
        try:
            if getattr(f, "in_project", False):
                fails.append("Projektdatei – nicht gelöscht: %s" % rel)
                continue
            if _org_plan_user_protected(f):
                fails.append("Vom Nutzer geschützt – nicht gelöscht: %s" % rel)
                continue
            p = str(f.path)
            if not os.path.isfile(p) or os.path.islink(p):
                fails.append("Nicht mehr vorhanden: %s" % rel)
                continue
            root = _org_plan_root_of(f)
            if root is not None and not _org_plan_inside(
                    _org_plan_key(os.path.realpath(str(root))),
                    _org_plan_key(os.path.realpath(p))):
                fails.append("Liegt außerhalb des Ordners (Verknüpfung) – nicht gelöscht: %s"
                             % rel)
                continue
            pk = _org_plan_key(p)
            if pk in needed:
                fails.append("Original gelöschter Kopien – bleibt erhalten: %s" % rel)
                continue
            try:
                st = os.stat(p)
            except OSError:
                fails.append("Nicht lesbar: %s" % rel)
                continue
            if not _org_plan_unchanged(f, st):
                fails.append("Seit der Analyse verändert – nicht gelöscht: %s" % rel)
                continue
            keeper_key = ""
            if f.dup_of:
                ok, keeper_key = _org_plan_dup_ok(f, p, st.st_size, trashed)
                if not ok:
                    fails.append("Original fehlt oder wurde verändert – Kopie bleibt: %s" % rel)
                    continue
            elif _org_plan_important(f):
                fails.append("Wichtige Datei – wird nicht gelöscht: %s" % rel)
                continue
            err = None
            try:
                ok = bool(trash_fn(p))
            except Exception as e:
                ok = False
                err = e
            if ok or not os.path.lexists(p):
                # auch bei gemeldetem Fehler, wenn die Datei trotzdem weg ist
                trashed.add(pk)
                if keeper_key:
                    needed.add(keeper_key)
            if ok:
                count += 1
            elif err is not None:
                fails.append("Papierkorb fehlgeschlagen: %s (%s)" % (rel, err))
            else:
                fails.append("Papierkorb fehlgeschlagen: %s" % rel)
        except Exception as e:
            fails.append("Papierkorb fehlgeschlagen: %s (%s)" % (rel, e))
    return count, fails


# ---------------------------------------------------------------------------
# Tresor (Sicherung wichtiger Dateien)
# ---------------------------------------------------------------------------
def _org_plan_backup_folder(f) -> str:
    folder = _org_plan_folder_for(f, "")
    if not folder:
        cat = f.category
        if cat == "duplicate":
            cat = f.meta.get("orig_category") or ""
        label = ORG_CATEGORIES.get(cat, {}).get("label", "") if cat else ""
        folder = _org_plan_sanitize_part(label) or "Sonstiges"
    return folder


def org_backup(files: list, dest_root: Path, progress=None, cancelled=lambda: False) -> dict:
    """Kopiert wichtige Dateien nach dest_root/<Kategorie-Ordner>/<Name>.
    Inkrementell (gleiche Größe + blake2b → übersprungen), Namenskonflikt → "x (2).ext",
    Kopie über Zwischendatei + Prüfsummenvergleich. Löscht/überschreibt in dest_root nie
    etwas außer der eigenen Zwischendatei. → {"copied", "skipped", "failed", "bytes"}"""
    res = {"copied": 0, "skipped": 0, "failed": [], "bytes": 0}
    try:
        dest = Path(dest_root)
        os.makedirs(str(dest), exist_ok=True)
        dest_real = Path(os.path.realpath(str(dest)))
        dest_key = _org_plan_key(dest_real)
        items = [f for f in list(files or []) if f is not None]
    except Exception as e:
        res["failed"].append("Sicherungsziel nicht verfügbar: %s (%s)" % (dest_root, e))
        return res
    total = len(items)
    for i, f in enumerate(items):
        if _org_plan_is_cancelled(cancelled):
            res["cancelled"] = True
            break
        _org_plan_call_progress(progress, "Sichern", i, total)
        rel = str(getattr(f, "rel", "") or "?")
        tmp = None
        try:
            src = str(f.path)
            if not os.path.isfile(src):
                res["failed"].append("Nicht gefunden: %s" % rel)
                continue
            if _org_plan_inside(dest_key, _org_plan_key(os.path.realpath(src))):
                res["skipped"] += 1                     # liegt schon im Tresor
                continue
            sub = _org_plan_backup_folder(f)
            tdir = dest_real.joinpath(*[p for p in sub.split("/") if p])
            if not _org_plan_inside(dest_key, _org_plan_key(tdir)):
                res["failed"].append("Ungültiger Zielordner: %s" % sub)
                continue
            os.makedirs(str(tdir), exist_ok=True)
            src_hash, src_size = _org_plan_hash_file(src, cancelled)
            if src_hash is None:
                res["failed"].append("Nicht lesbar: %s" % rel)
                continue
            name = Path(src).name
            stem, ext = _org_plan_split_name(name)
            target = None
            skip = False
            for k in range(1, 100000):
                cand = tdir / (name if k == 1 else "%s (%d)%s" % (stem, k, ext))
                if not os.path.lexists(str(cand)):
                    target = cand
                    break
                try:
                    if os.path.isfile(str(cand)) and os.path.getsize(str(cand)) == src_size \
                            and _org_plan_hash_file(cand)[0] == src_hash:
                        skip = True
                        break
                except OSError:
                    continue
            if skip:
                res["skipped"] += 1
                continue
            if target is None:
                res["failed"].append("Kein freier Name im Tresor: %s" % name)
                continue
            tmp = tdir / ("~org_tmp_%s.part" % uuid.uuid4().hex)
            shutil.copy2(src, str(tmp))
            got, _sz = _org_plan_hash_file(tmp)
            if got != src_hash:
                try:
                    os.unlink(str(tmp))
                except OSError:
                    pass
                tmp = None
                res["failed"].append("Prüfsumme nach dem Kopieren falsch: %s" % rel)
                continue
            try:
                _org_plan_rename_noreplace(tmp, target)
            except FileExistsError:
                target = _org_plan_free_name(tdir, name)
                _org_plan_rename_noreplace(tmp, target)
            tmp = None
            res["copied"] += 1
            res["bytes"] += src_size
        except Exception as e:
            res["failed"].append("Sichern fehlgeschlagen: %s (%s)" % (rel, e))
        finally:
            if tmp is not None:
                try:
                    if os.path.isfile(str(tmp)):
                        os.unlink(str(tmp))     # nur die eigene Zwischendatei
                except OSError:
                    pass
    _org_plan_call_progress(progress, "Sichern", total, total)
    return res
