# ===========================================================================
# ORDNER-ASSISTENT – exakte Duplikate + Versionsgruppen
# ===========================================================================
# Exakte Duplikate: Größe -> Anfang/Ende-Hash -> voller Hash (blake2b).
# Versionsgruppen: "Lebenslauf.docx", "Lebenslauf_final.docx",
# "Lebenslauf_final (1).docx" im selben Ordner -> neueste bleibt, Rest "Prüfen".
# Alle Namen beginnen mit org_ / Org / ORG_ / _org_dupe_ (Einzeldatei-Programm).
# Nur Standardbibliothek. Dieses Modul liest Dateien, verändert aber keine.
from __future__ import annotations

import hashlib
import os
import re
import unicodedata

from org_core import ORG_JUNK, ORG_KIND_EXT, ORG_REVIEW, OrgFile  # noqa: F401  (OrgFile für Typen)

ORG_DUPE_EDGE_BYTES = 64 * 1024        # Anfang + Ende für den Schnell-Hash
ORG_DUPE_CHUNK_BYTES = 1024 * 1024     # Blockgröße beim vollständigen Hash
ORG_DUPE_DIGEST_SIZE = 32              # blake2b-256 als Hex in f.meta["hash"]


# ---------------------------------------------------------------------------
# Hilfen: Hashing
# ---------------------------------------------------------------------------
def _org_dupe_safe_progress(progress, done: int, total: int) -> None:
    if progress is None:
        return
    try:
        progress(done, total)
    except Exception:
        pass                                 # Fehler im Fortschritts-Callback ignorieren


def _org_dupe_is_cancelled(cancelled) -> bool:
    try:
        return bool(cancelled())
    except Exception:
        return False


# Windows: Cloud-Platzhalter (OneDrive „Nur online“, offline) nie lesen – Lesen würde
# den Download auslösen (Netzwerk, Speicherplatz). Attribute laut WinAPI.
ORG_DUPE_CLOUD_ATTRS = 0x00001000 | 0x00040000 | 0x00400000   # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS
ORG_DUPE_CHECK_CLOUD = os.name == "nt"
_org_dupe_stat = os.stat                    # austauschbar für Tests


def _org_dupe_is_cloud_only(path) -> bool:
    """True, wenn die Datei nur als Cloud-Platzhalter vorliegt (nicht lesen!)."""
    if not ORG_DUPE_CHECK_CLOUD:
        return False
    try:
        attrs = getattr(_org_dupe_stat(str(path)), "st_file_attributes", 0) or 0
    except (OSError, ValueError, TypeError):
        return False                         # Lesefehler folgt ohnehin beim Öffnen
    return bool(attrs & ORG_DUPE_CLOUD_ATTRS)


def _org_dupe_long_path(s: str) -> str:
    """Windows-Pfad >= 248 Zeichen mit \\\\?\\-Präfix versehen (MAX_PATH umgehen)."""
    if len(s) < 248 or s.startswith("\\\\?\\"):
        return s
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def _org_dupe_io_path(path):
    """Pfad zum Öffnen: unter Windows lange Pfade erlauben, sonst unverändert."""
    if os.name != "nt":
        return path
    try:
        return _org_dupe_long_path(os.path.abspath(str(path)))
    except Exception:
        return path


def _org_dupe_full_hash(path, buf: bytearray, size: int = -1, cancelled=None) -> str | None:
    """Vollständiger blake2b-Hash in 1-MB-Blöcken; None bei Lesefehler, Abbruch
    oder wenn sich die Größe seit dem Einlesen geändert hat."""
    try:
        h = hashlib.blake2b(digest_size=ORG_DUPE_DIGEST_SIZE)
        mv = memoryview(buf)
        read = 0
        chunks = 0
        with open(_org_dupe_io_path(path), "rb", buffering=0) as fh:
            while True:
                n = fh.readinto(buf)
                if not n:
                    break
                h.update(mv[:n])
                read += n
                chunks += 1
                # Abbruch auch mitten in sehr großen Dateien (alle 64 MB prüfen)
                if cancelled is not None and chunks % 64 == 0 and _org_dupe_is_cancelled(cancelled):
                    return None
        if size >= 0 and read != size:
            return None                      # Datei wurde inzwischen verändert
        return h.hexdigest()
    except (OSError, ValueError, TypeError):
        return None


def _org_dupe_quick_hash(path, size: int):
    """Schnell-Hash. Liefert (digest, ist_vollständig) oder None bei Lesefehler.
    Kleine Dateien (<= 2 x 64 KB) werden ganz gelesen -> sofort voller Hash."""
    try:
        if _org_dupe_is_cloud_only(path):
            return None
        with open(_org_dupe_io_path(path), "rb") as fh:
            if size <= 2 * ORG_DUPE_EDGE_BYTES:
                data = fh.read(size + 1)         # nie mehr lesen als erwartet
                if len(data) != size:
                    return None                  # Größe geändert -> überspringen
                h = hashlib.blake2b(data, digest_size=ORG_DUPE_DIGEST_SIZE)
                return h.hexdigest(), True
            head = fh.read(ORG_DUPE_EDGE_BYTES)
            fh.seek(size - ORG_DUPE_EDGE_BYTES)
            tail = fh.read(ORG_DUPE_EDGE_BYTES)
        h = hashlib.blake2b(head, digest_size=16)
        h.update(b"|")
        h.update(tail)
        return h.hexdigest(), False
    except (OSError, ValueError, TypeError, OverflowError):
        return None


def _org_dupe_path_key(f) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(f.path)))
    except Exception:
        return str(getattr(f, "path", id(f)))


def _org_dupe_collapse_links(group: list) -> list:
    """Gleiche physische Datei (Hardlink/Junction) nur einmal zählen – sonst würde
    ein „Duplikat“ gelöscht, das in Wahrheit dieselbe Datei ist."""
    seen = set()
    out = []
    for f in group:
        try:
            st = os.stat(_org_dupe_io_path(str(f.path)))
            ident = (st.st_dev, st.st_ino) if st.st_ino else None
        except OSError:
            continue                         # inzwischen verschwunden/unlesbar
        if ident is not None:
            if ident in seen:
                continue
            seen.add(ident)
        out.append(f)
    return out


# ---------------------------------------------------------------------------
# Öffentlich: exakte Duplikate
# ---------------------------------------------------------------------------
def org_find_duplicates(files: list, cancelled=lambda: False, progress=None) -> list:
    """Gruppen (>= 2) byte-identischer Dateien. Leere Dateien werden ignoriert,
    Dateien mit einmaliger Größe nie gelesen. Voller Hash landet in f.meta["hash"].
    Bei Abbruch (cancelled()) -> []. progress(done_bytes, total_bytes) optional."""
    try:
        return _org_dupe_find(files, cancelled, progress)
    except Exception:
        return []


def _org_dupe_find(files, cancelled, progress) -> list:
    if not files:
        return []
    # 1) nach Größe bündeln (doppelte Einträge desselben Pfads entfernen)
    by_size: dict = {}
    for f in files:
        try:
            size = int(f.size)
        except (AttributeError, TypeError, ValueError):
            continue
        if size <= 0 or getattr(f, "path", None) is None:
            continue
        if getattr(f, "cloud", False):      # „nur online“: Lesen würde den Download starten
            continue
        by_size.setdefault(size, []).append(f)

    buckets = []
    for size, members in by_size.items():
        if len(members) < 2:
            continue
        seen = set()
        uniq = []
        for f in members:
            k = _org_dupe_path_key(f)
            if k not in seen:
                seen.add(k)
                uniq.append(f)
        if len(uniq) >= 2:
            buckets.append((size, uniq))
    if not buckets:
        _org_dupe_safe_progress(progress, 0, 0)
        return []

    # Gesamtmenge = Schnell-Hash aller Kandidaten + (schlimmstenfalls) voller Hash
    big = 2 * ORG_DUPE_EDGE_BYTES
    total = 0
    for size, members in buckets:
        quick = size if size <= big else big
        total += quick * len(members)
        if size > big:
            total += size * len(members)
    done = 0
    _org_dupe_safe_progress(progress, done, total)

    groups: list = []
    buf = bytearray(ORG_DUPE_CHUNK_BYTES)
    for size, members in buckets:
        quick_cost = size if size <= big else big
        # 2) Schnell-Hash (Anfang + Ende bzw. ganze kleine Datei)
        by_quick: dict = {}
        complete = False
        for f in members:
            if _org_dupe_is_cancelled(cancelled):
                return []
            res = _org_dupe_quick_hash(f.path, size)
            done += quick_cost
            if res is None:
                if size > big:
                    done += size             # wird nie voll gelesen
                continue                     # unlesbar -> überspringen
            digest, complete = res
            by_quick.setdefault(digest, []).append(f)
        _org_dupe_safe_progress(progress, done, total)

        for digest, cand in by_quick.items():
            if complete:
                if len(cand) >= 2:
                    for f in cand:
                        _org_dupe_set_hash(f, digest)
                    groups.append(cand)
                continue
            if len(cand) < 2:
                done += size * len(cand)     # kein voller Hash nötig
                continue
            # 3) voller Hash nur bei Kollision des Schnell-Hashs
            by_full: dict = {}
            for f in cand:
                if _org_dupe_is_cancelled(cancelled):
                    return []
                full = _org_dupe_full_hash(f.path, buf, size, cancelled)
                if full is None and _org_dupe_is_cancelled(cancelled):
                    return []
                done += size
                _org_dupe_safe_progress(progress, done, total)
                if full is None:
                    continue
                by_full.setdefault(full, []).append(f)
            for full, same in by_full.items():
                if len(same) >= 2:
                    for f in same:
                        _org_dupe_set_hash(f, full)
                    groups.append(same)
        _org_dupe_safe_progress(progress, done, total)

    result = []
    for g in groups:
        g = _org_dupe_collapse_links(g)
        if len(g) >= 2:
            g.sort(key=lambda x: str(getattr(x, "rel", "")))
            result.append(g)
    # größte Ersparnis zuerst, dann stabil nach Pfad
    result.sort(key=lambda g: (-int(g[0].size), str(getattr(g[0], "rel", ""))))
    _org_dupe_safe_progress(progress, total, total)
    return result


def _org_dupe_set_hash(f, digest: str) -> None:
    try:
        if not isinstance(f.meta, dict):
            f.meta = {}
        f.meta["hash"] = digest
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Öffentlich: welches Exemplar behalten?
# ---------------------------------------------------------------------------
# Ordner, in denen eher Kopien/Reste liegen (Teile des rel-Pfads, ohne Dateiname)
ORG_DUPE_WEAK_DIRS = {"downloads", "download", "temp", "tmp", "neuer ordner",
                      "new folder", "desktop"}
_ORG_DUPE_DIR_COUNTER_RE = re.compile(r"\s*\(\d{1,3}\)$")
# Kopie-Kennzeichen im Dateinamen (ohne Endung)
_ORG_DUPE_COPY_NAME_RE = re.compile(
    r"(?:^(?:kopie|copy)\s+(?:von|of)\b"   # "Kopie von x", "Copy of x"
    r"|\(\d{1,2}\)\s*$"                    # "x (1)" .. "x (99)"
    # "x - Kopie", "x_copy", "x-Kopie", "x (Kopie)", "Kopie - x", "x copy2"
    r"|(?:^|[\s_\-–—(\[.])(?:kopie|copy)(?=$|[\s_\-–—)\].\d]))",
    re.IGNORECASE)
# Reste/Temporärdateien: nie als Behalten-Exemplar wählen, wenn es eine echte Datei
# gibt (sonst: Original = Duplikat, „Original“ ~WRL0001.tmp = Müll -> beides weg)
_ORG_DUPE_TEMP_EXTS = {".tmp", ".temp", ".crdownload", ".part", ".partial", ".download",
                       ".opdownload", ".bak", ".old", ".swp", ".swo", ".~tmp", ".dmp"}
_ORG_DUPE_TEMP_PREFIXES = ("~$", "~wrl", ".~lock", "~lock")


def _org_dupe_in_weak_dir(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")[:-1]
    for p in parts:
        p = _ORG_DUPE_DIR_COUNTER_RE.sub("", p.strip().lower())
        if p in ORG_DUPE_WEAK_DIRS:
            return True
    return False


def _org_dupe_has_copy_marker(name: str) -> bool:
    stem = os.path.splitext(name)[0]
    return bool(_ORG_DUPE_COPY_NAME_RE.search(stem))


def _org_dupe_junk_rank(f, name: str) -> int:
    """0 = normale Datei, 1 = Temp-/Rest-Name (~$x.docx, x.tmp, x.part …),
    2 = schon als Müll eingestuft (ORG_JUNK)."""
    if getattr(f, "tier", "") == ORG_JUNK:
        return 2
    nl = name.lower()
    if nl.startswith(_ORG_DUPE_TEMP_PREFIXES):
        return 1
    return 1 if os.path.splitext(nl)[1] in _ORG_DUPE_TEMP_EXTS else 0


def _org_dupe_keeper_key(f):
    rel = str(getattr(f, "rel", "") or "").replace("\\", "/")
    try:
        name = f.path.name
    except Exception:
        name = rel.rsplit("/", 1)[-1]
    try:
        mtime = float(f.mtime)
        if mtime != mtime:                   # NaN
            mtime = float("inf")
    except (TypeError, ValueError, AttributeError):
        mtime = float("inf")
    try:
        depth = int(f.depth)
    except (TypeError, ValueError, AttributeError):
        depth = rel.count("/")
    return (_org_dupe_junk_rank(f, name),                 # (0) Sicherheit: kein Rest/Müll
            not bool(getattr(f, "in_project", False)),   # (1) Projekt zuerst
            _org_dupe_in_weak_dir(rel),                   # (2) nicht Downloads/Temp
            _org_dupe_has_copy_marker(name),              # (3) ohne "Kopie"
            mtime,                                        # (4) ältestes = Original
            depth,                                        # (5) flachste Ebene
            len(rel),                                     # (6) kürzester Pfad
            rel)                                          # (7) alphabetisch


def org_pick_keeper(group: list):
    """Das Exemplar, das behalten wird (None bei leerer Gruppe)."""
    try:
        members = [f for f in (group or []) if f is not None]
        if not members:
            return None
        return min(members, key=_org_dupe_keeper_key)
    except Exception:
        try:
            return group[0]
        except Exception:
            return None


def org_mark_duplicates(files: list, groups: list) -> int:
    """Markiert alle Nicht-Behalten-Exemplare als Duplikat (ORG_JUNK).
    Dateien in Projektordnern und das behaltene Exemplar bleiben unberührt.
    `files` wird nicht benötigt (Signatur für einheitliche Pipeline)."""
    marked = 0
    try:
        group_iter = list(groups or [])
    except Exception:
        return 0
    for group in group_iter:
        try:
            if not group or len(group) < 2:
                continue
            keeper = org_pick_keeper(group)
            if keeper is None:
                continue
            # Sicherheit: Behalten-Exemplar inzwischen gelöscht/verschoben? Dann ein
            # noch vorhandenes Exemplar behalten (sonst wären alle Kopien „Müll“).
            if not _org_dupe_exists(keeper):
                existing = [m for m in group if m is not None and _org_dupe_exists(m)]
                if existing:
                    keeper = org_pick_keeper(existing)
                    if keeper is None:
                        continue
            keeper_path = _org_dupe_path_key(keeper)
            keeper_rel = str(keeper.rel)
            keeper_hash = _org_dupe_get_hash(keeper)
            keeper_size = getattr(keeper, "size", None)
        except Exception:
            continue
        for f in group:
            try:
                if f is None or f is keeper or getattr(f, "in_project", False):
                    continue
                if _org_dupe_path_key(f) == keeper_path:
                    continue                 # derselbe Pfad doppelt – nie markieren
                # Sicherheit: Gruppe passt nicht (andere Größe / anderer Hash) -> nie Müll
                if getattr(f, "size", None) != keeper_size:
                    continue
                h = _org_dupe_get_hash(f)
                if h and keeper_hash and h != keeper_hash:
                    continue
                f.add_reason("Exakte Kopie von %s" % keeper_rel)
                f.dup_of = keeper_rel
                f.category = "duplicate"
                f.tier = ORG_JUNK
                f.score = 1.0
                marked += 1
            except Exception:
                continue
    return marked


def _org_dupe_exists(f) -> bool:
    try:
        return os.path.isfile(_org_dupe_io_path(str(f.path)))
    except Exception:
        return False


def _org_dupe_get_hash(f) -> str:
    try:
        meta = f.meta
        return str(meta.get("hash") or "") if isinstance(meta, dict) else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Versionsschlüssel
# ---------------------------------------------------------------------------
_ORG_DUPE_UMLAUTS = {ord("ä"): "ae", ord("ö"): "oe", ord("ü"): "ue", ord("ß"): "ss",
                     ord("Ä"): "ae", ord("Ö"): "oe", ord("Ü"): "ue", ord("ẞ"): "ss"}
# Token: "(2)" | "[2]" | "v1.2" / "2.1" | Wort/Zahl – Unicode-fähig, damit kyrillische/
# chinesische Namen nicht zu "" bzw. nur Ziffern zusammenfallen ("简历_2023" ≠ "合同_2023")
_ORG_DUPE_TOKEN_RE = re.compile(r"\(\d{1,2}\)|\[\d{1,2}\]|[^\W\d_]*\d+(?:\.\d+)+|[^\W_]+")
# Starke Marker: überall im Namen entfernt
ORG_DUPE_STRONG_MARKERS = {
    "final", "finale", "finaler", "finales", "finalversion", "endgueltig", "endgultig",
    "endgueltige", "endgueltiges", "endversion", "neueste", "neuster", "neuste",
    "neuestes", "aktuellste", "newest", "latest", "copy", "kopie", "draft", "entwurf",
    "korrigiert", "korrigierte", "korrektur", "ueberarbeitet", "uberarbeitet",
    "ueberarbeitete", "edited", "updated", "bearbeitet", "bearbeitete", "backup",
    "sicherung", "sicherungskopie", "korr", "endfassung", "finalfinal",
}
# Schwache Marker: nur am Anfang/Ende des Namens (sonst zu viele Fehltreffer,
# z. B. "New York Reise", "Update Anleitung Router")
ORG_DUPE_WEAK_MARKERS = {
    "neu", "new", "aktuell", "current", "alt", "old", "edit", "update", "fertig",
}
_ORG_DUPE_VERSION_TOKEN_RE = re.compile(
    r"^(?:v\d{1,2}(?:\.\d{1,3})*|ver\d{1,3}(?:\.\d{1,3})*|version\d{1,3}(?:\.\d{1,3})*"
    r"|rev\d{1,3}(?:\.\d{1,3})*)$")
_ORG_DUPE_WEAK_VERSION_RE = re.compile(r"^r\d{1,2}$")       # "r2" nur am Rand
_ORG_DUPE_VERSION_WORDS = {"v", "ver", "version", "rev", "revision"}
_ORG_DUPE_NUMBER_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3})*$")
_ORG_DUPE_SMALL_NUM_RE = re.compile(r"^\d{1,2}$")
_ORG_DUPE_PAREN_RE = re.compile(r"^[\(\[]\d{1,2}[\)\]]$")
_ORG_DUPE_GLUED_RE = re.compile(r"^(?P<word>[a-z]+)(?P<num>\d{1,2})$")
# Endungen, die dieselbe Dateifamilie meinen
_ORG_DUPE_EXT_ALIAS = {".jpeg": ".jpg", ".jpe": ".jpg", ".tiff": ".tif", ".htm": ".html",
                       ".yaml": ".yml", ".mpeg": ".mpg", ".aif": ".aiff"}
# echte Endung: 1–8 Zeichen, mind. ein Buchstabe ("v1.2" hat keine Endung ".2")
_ORG_DUPE_EXT_RE = re.compile(r"^\.(?=[a-z0-9]*[a-z])[a-z0-9]{1,8}$")
_ORG_DUPE_BASE_CACHE: dict = {}
_ORG_DUPE_CACHE_MAX = 200_000


def _org_dupe_fold(s: str) -> str:
    s = unicodedata.normalize("NFC", s).lower().translate(_ORG_DUPE_UMLAUTS)
    if not s.isascii():
        s = "".join(c for c in unicodedata.normalize("NFKD", s)
                    if not unicodedata.combining(c))
    return s


def _org_dupe_is_strong(tok: str) -> bool:
    if tok in ORG_DUPE_STRONG_MARKERS or _ORG_DUPE_PAREN_RE.match(tok):
        return True
    if _ORG_DUPE_VERSION_TOKEN_RE.match(tok):
        return True
    m = _ORG_DUPE_GLUED_RE.match(tok)        # "final2", "kopie3"
    return bool(m and m.group("word") in ORG_DUPE_STRONG_MARKERS)


def _org_dupe_is_weak(tok: str) -> bool:
    if tok in ORG_DUPE_WEAK_MARKERS or _ORG_DUPE_WEAK_VERSION_RE.match(tok):
        return True
    m = _ORG_DUPE_GLUED_RE.match(tok)        # "neu2", "alt3"
    return bool(m and m.group("word") in ORG_DUPE_WEAK_MARKERS)


def _org_dupe_split(name: str):
    """(Basisschlüssel, hat_Marker). Basisschlüssel = Name ohne Versions-/Kopie-
    Marker + Endungsfamilie, z. B. "lebenslauf.docx". "" wenn nichts übrig bleibt."""
    return _org_dupe_split3(name)[:2]


def _org_dupe_split3(name: str):
    """(Basisschlüssel, hat_Marker, nur_Zähler). nur_Zähler = einzige Marker sind
    Browser-/Explorer-Zähler wie "(1)" – kein echtes Versionswort."""
    if not isinstance(name, str):
        return "", False, False
    cached = _ORG_DUPE_BASE_CACHE.get(name)
    if cached is not None:
        return cached
    res = _org_dupe_split_uncached(name)
    if len(_ORG_DUPE_BASE_CACHE) >= _ORG_DUPE_CACHE_MAX:
        _ORG_DUPE_BASE_CACHE.clear()
    _ORG_DUPE_BASE_CACHE[name] = res
    return res


def _org_dupe_split_uncached(name: str):
    if not isinstance(name, str) or not name.strip():
        return "", False, False
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    stem, ext = os.path.splitext(name)
    if not stem:                              # ".bashrc" -> keine Endung
        stem, ext = ext, ""
    ext = ext.lower()
    if not _ORG_DUPE_EXT_RE.match(ext):
        stem, ext = name, ""                  # "Bericht v1.2" -> kein echtes Suffix
    ext = _ORG_DUPE_EXT_ALIAS.get(ext, ext)
    toks = _ORG_DUPE_TOKEN_RE.findall(_org_dupe_fold(stem))
    n = len(toks)
    removed = [False] * n

    # Durchgang 1: starke Marker überall
    i = 0
    while i < n:
        t = toks[i]
        if _org_dupe_is_strong(t):
            removed[i] = True
            # "Kopie von x", "Copy of x"
            if t in ("kopie", "copy") and i + 1 < n and toks[i + 1] in ("von", "of"):
                removed[i + 1] = True
                i += 1
            # Zähler direkt danach: "final 2", "Kopie 3"
            elif (i + 1 < n and _ORG_DUPE_SMALL_NUM_RE.match(toks[i + 1])
                  and not _ORG_DUPE_PAREN_RE.match(t)):
                removed[i + 1] = True
                i += 1
        elif t in _ORG_DUPE_VERSION_WORDS and i + 1 < n and _ORG_DUPE_NUMBER_RE.match(toks[i + 1]):
            removed[i] = removed[i + 1] = True   # "Version 3", "rev 2", "V 1.2"
            i += 1
        i += 1

    # Durchgang 2: schwache Marker nur am Rand (von außen nach innen)
    def _prev_marker(j: int) -> bool:
        return j >= 0 and (removed[j] or _org_dupe_is_weak(toks[j]))

    j = n - 1
    while j >= 0:
        if removed[j]:
            j -= 1
            continue
        t = toks[j]
        if _org_dupe_is_weak(t):
            removed[j] = True
        elif _ORG_DUPE_SMALL_NUM_RE.match(t) and _prev_marker(j - 1):
            removed[j] = True                 # "Lebenslauf neu 2"
        else:
            break
        j -= 1
    i = 0
    while i < n:
        if removed[i]:
            i += 1
            continue
        if _org_dupe_is_weak(toks[i]):
            removed[i] = True
            if i + 1 < n and _ORG_DUPE_SMALL_NUM_RE.match(toks[i + 1]):
                removed[i + 1] = True
                i += 1
            i += 1
            continue
        break

    has_marker = any(removed)
    counter_only = has_marker and all(
        _ORG_DUPE_PAREN_RE.match(t) for t, r in zip(toks, removed) if r)
    base = " ".join(t for t, r in zip(toks, removed) if not r)
    if not base:
        return "", has_marker, counter_only
    return base + ext, has_marker, counter_only


def org_version_base(name: str) -> str:
    """Basisschlüssel eines Dateinamens – auch ohne Marker (für Gruppierung).
    "Lebenslauf_final_FINAL (2).docx" und "Lebenslauf.docx" -> "lebenslauf.docx"."""
    try:
        return _org_dupe_split(name)[0]
    except Exception:
        return ""


def org_version_key(name: str):
    """Basisschlüssel, wenn der Name einen Versions-/Kopie-Marker trägt, sonst None.
    Reine Zahlen/Daten werden nie entfernt (Rechnung_1234 ≠ Rechnung_1235)."""
    try:
        base, has_marker = _org_dupe_split(name)
    except Exception:
        return None
    if not has_marker or not base:
        return None
    return base


# ---------------------------------------------------------------------------
# Öffentlich: Versionsgruppen
# ---------------------------------------------------------------------------
# Fotos/Videos/Musik bilden keine Versionsgruppen: "IMG_0001 (1).JPG",
# "Screenshot (3).png", "image (1).jpg" sind fast immer VERSCHIEDENE Aufnahmen, und
# bei "Foto_bearbeitet.jpg" ist das ältere Exemplar das unbearbeitete Original.
# Exakte Kopien findet org_find_duplicates trotzdem.
_ORG_DUPE_MEDIA_KINDS = {"image", "video", "audio"}
_ORG_DUPE_MEDIA_EXTS = {e for k in _ORG_DUPE_MEDIA_KINDS for e in ORG_KIND_EXT.get(k, ())}
_ORG_DUPE_MEDIA_CATEGORIES = {"photo_personal", "photo_chat", "screenshot", "image",
                              "video_personal", "video", "audio"}
# Nur-Zähler-Gruppen ("Rechnung.pdf", "Rechnung (1).pdf") bei Allerweltsnamen oder
# wiederkehrenden Belegen sind meist verschiedene Dokumente, keine Versionen.
ORG_DUPE_GENERIC_WORDS = {
    "scan", "scans", "dokument", "document", "doc", "datei", "file", "download",
    "unbenannt", "untitled", "unnamed", "neues", "new", "neu", "image", "bild", "foto",
    "photo", "img", "screenshot", "bildschirmfoto", "anhang", "attachment", "print",
    "druck", "export", "output", "ausgabe", "brief", "letter", "formular", "form",
    "seite", "page", "blatt", "sheet", "mappe", "tabelle", "praesentation",
    "presentation", "notiz", "notes", "note", "text", "liste", "list", "pdf", "kopie",
    "copy", "test", "temp", "tmp", "info", "data", "daten", "report",
}
_ORG_DUPE_RECURRING_STEMS = (
    "rechnung", "abrechnung", "invoice", "receipt", "beleg", "quittung", "auszug",
    "statement", "payslip", "bescheid", "bescheinigung", "nachweis", "ticket",
    "fahrkarte", "bestellung", "order", "lieferschein", "gutschrift", "mahnung",
    "zahlung", "payment", "kassenbon", "bon", "termin", "befund", "rezept",
)
_ORG_DUPE_TEMP_NAME_PREFIXES = ("~", ".~")


def _org_dupe_is_media(f) -> bool:
    if getattr(f, "kind", "") in _ORG_DUPE_MEDIA_KINDS:
        return True
    if getattr(f, "category", "") in _ORG_DUPE_MEDIA_CATEGORIES:
        return True
    return str(getattr(f, "ext", "") or "").lower() in _ORG_DUPE_MEDIA_EXTS


def _org_dupe_generic_base(base: str) -> bool:
    """Basisname ist Allerweltsname oder wiederkehrender Beleg (Rechnung, Auszug …)."""
    stem = base.rsplit(".", 1)[0] if "." in base else base
    toks = stem.split()
    if not toks:
        return True
    if all(t.isdigit() or t in ORG_DUPE_GENERIC_WORDS for t in toks):
        return True
    return any(t.endswith(_ORG_DUPE_RECURRING_STEMS) for t in toks)


_ORG_DUPE_RECURRING_CATEGORIES = {"invoice", "bank", "payslip", "tax", "travel", "health",
                                  "insurance"}


def org_version_groups(files: list) -> dict:
    """{"<Ordner-rel>/<basis>": [OrgFile, ...]} – Dateien im selben Ordner mit gleichem
    Basisschlüssel, mind. 2 Mitglieder, mind. eines mit Marker. Ausgeschlossen:
    Projektdateien, Duplikate, Aufräum-Kandidaten (ORG_JUNK), leere Dateien,
    Sperr-/Temp-Dateien (~$x.docx), Fotos/Videos/Musik, sowie reine Zähler-Gruppen
    ("Scan (1).pdf") bei Allerweltsnamen und wiederkehrenden Belegen."""
    buckets: dict = {}
    marked_keys = set()
    semantic_keys = set()                    # Gruppe hat echtes Versionswort
    try:
        items = list(files or [])
    except Exception:
        return {}
    for f in items:
        try:
            if getattr(f, "in_project", False):
                continue
            if f.dup_of or f.category == "duplicate" or f.tier == ORG_JUNK:
                continue
            if _org_dupe_is_media(f):
                continue
            try:
                if int(f.size) <= 0:
                    continue                 # leere Datei ist nie "neueste Version"
            except (TypeError, ValueError, AttributeError):
                pass
            rel = str(f.rel).replace("\\", "/")
            parent, _, fname = rel.rpartition("/")
            fname = fname or f.path.name
            if fname.startswith(_ORG_DUPE_TEMP_NAME_PREFIXES):
                continue                     # Office-/LibreOffice-Sperrdateien
            if os.path.splitext(fname)[1].lower() in _ORG_DUPE_TEMP_EXTS:
                continue
            base, has_marker, counter_only = _org_dupe_split3(fname)
            if not base:
                continue
            key = (parent + "/" + base) if parent else base
            buckets.setdefault(key, []).append(f)
            if has_marker:
                marked_keys.add(key)
                if not counter_only:
                    semantic_keys.add(key)
        except Exception:
            continue
    out = {}
    for key in marked_keys:
        try:
            members = buckets.get(key) or []
            if len(members) < 2:
                continue
            if key not in semantic_keys:
                # nur "(1)"-Zähler: bei Allerweltsnamen/Belegen keine Versionen
                base = key.rsplit("/", 1)[-1]
                if _org_dupe_generic_base(base):
                    continue
                if any(getattr(m, "category", "") in _ORG_DUPE_RECURRING_CATEGORIES
                       for m in members):
                    continue
            members.sort(key=lambda x: str(x.rel))
            out[key] = members
        except Exception:
            continue
    return out


def _org_dupe_display_name(f) -> str:
    try:
        return f.path.name
    except Exception:
        return str(getattr(f, "rel", "") or "").replace("\\", "/").rsplit("/", 1)[-1]


def org_mark_versions(groups: dict) -> int:
    """Neueste Datei je Gruppe bleibt (version_latest=True), ältere -> ORG_REVIEW
    ("Ältere Version von …"), nie ORG_JUNK. Liefert Anzahl älterer Versionen."""
    count = 0
    try:
        group_items = list((groups or {}).items())
    except Exception:
        return 0

    def _age(x):
        try:
            m = float(x.mtime)
            if m != m:                           # NaN
                m = float("-inf")
        except (TypeError, ValueError, AttributeError):
            m = float("-inf")
        return (m, str(getattr(x, "rel", "")))

    for key, members in group_items:
        try:
            members = [f for f in members
                       if f is not None and not getattr(f, "in_project", False)
                       and not getattr(f, "dup_of", "")]
            if len(members) < 2:
                continue
            newest = max(members, key=_age)
            reason = "Ältere Version von %s" % _org_dupe_display_name(newest)
        except Exception:
            continue
        try:
            newest.version_group = key
            newest.version_latest = True
        except Exception:
            continue
        for f in members:
            if f is newest:
                continue
            try:
                f.add_reason(reason)
                f.version_group = key
                f.version_latest = False
                f.tier = ORG_REVIEW
                count += 1
            except Exception:
                continue
    return count
