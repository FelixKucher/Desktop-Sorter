# --------------------------------------------------------------------------- #
# Ordner-Assistent – Oberfläche
# --------------------------------------------------------------------------- #
from PySide6.QtWidgets import (QFileDialog, QHeaderView, QStackedWidget, QTreeWidget,
                               QTreeWidgetItem)

ORG_TEXT_CACHE: dict[tuple, str] = {}      # (Pfad, Größe, mtime) → Text; macht Neu-Analysen schnell
_org_extract_text_raw = org_extract_text


def org_extract_text(path, max_chars: int = 4000) -> str:       # noqa: F811 — bewusst ersetzt
    """Zwischengespeicherte Textextraktion (gleiche Datei = gleicher Text)."""
    try:
        st = os.stat(path)
        key = (str(path), st.st_size, int(st.st_mtime), max_chars)
    except OSError:
        return ""
    if key not in ORG_TEXT_CACHE:
        if len(ORG_TEXT_CACHE) > 20000:              # Speicher begrenzen (~80 MB Text)
            ORG_TEXT_CACHE.clear()
        ORG_TEXT_CACHE[key] = _org_extract_text_raw(path, max_chars)
    return ORG_TEXT_CACHE[key]


def org_dest_for(f: OrgFile) -> str:
    """Zielordner für eine (ggf. manuell umkategorisierte) Datei."""
    folder = ORG_CATEGORIES.get(f.category, {}).get("folder", "")
    if not folder:
        return ""
    year = org_year_of(f)
    event = f.event or f"{year}-{time.localtime(f.mtime).tm_mon:02d} " \
                       f"{org_month_name(time.localtime(f.mtime).tm_mon)}"
    return folder.replace("{year}", str(year)).replace("{event}", event)


class OrgWorker(QThread):
    """Führt eine Analyse/Aktion im Hintergrund aus; Fortschritt gedrosselt."""
    progress = Signal(str, int, int)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self._cancel = False
        self._last = 0.0

    def cancel(self):
        self._cancel = True

    def _progress(self, stage, done, total):
        now = time.monotonic()
        if now - self._last > 0.08 or done >= total:
            self._last = now
            self.progress.emit(str(stage), int(done or 0), int(total or 0))

    def run(self):
        try:
            self.done.emit(self.fn(self._progress, lambda: self._cancel))
        except Exception as e:                          # noqa: BLE001 — im UI anzeigen
            import traceback
            self.failed.emit(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()[-1500:]}")


class OrgTile(QAbstractButton):
    """Kachel der Übersicht: Symbol, große Zahl, Beschriftung."""

    def __init__(self, glyph: str, title: str, color: str, parent=None):
        super().__init__(parent)
        self.glyph, self.title, self.color = glyph, title, QColor(color)
        self.value, self.sub = "–", ""
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(138, 92)
        self._hover = False

    def set_value(self, value: str, sub: str = ""):
        self.value, self.sub = value, sub
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        paint_glass(p, r, 18, strength=1.2, tint=self.color if self.isChecked() else None,
                    hover=self._hover, active=self.isChecked())
        f = self.font()
        f.setPixelSize(13)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(self.color.lighter(140))
        p.drawText(QRectF(16, 12, r.width() - 24, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(f"{self.glyph}  {self.title}", Qt.ElideRight,
                                              int(r.width() - 26)))
        f.setPixelSize(26)
        f.setWeight(QFont.Bold)
        p.setFont(f)
        p.setPen(Qt.white)
        p.drawText(QRectF(16, 34, r.width() - 24, 34), Qt.AlignLeft | Qt.AlignVCenter, self.value)
        f.setPixelSize(11)
        f.setWeight(QFont.Normal)
        p.setFont(f)
        p.setPen(TEXT_2)
        p.drawText(QRectF(16, 66, r.width() - 24, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.sub, Qt.ElideRight, int(r.width() - 24)))


ORG_SECTIONS = [
    # Schlüssel, Titel, Symbol, Farbe
    ("moves", "Neue Ordnung", "🗂", "#0A84FF"),
    ("research", "Recherche", "🔎", "#5E5CE6"),
    ("protect", "Wichtig & dauerhaft", "🔒", "#30D158"),
    ("dups", "Duplikate", "⧉", "#FF9F0A"),
    ("versions", "Alte Versionen", "🕘", "#BF5AF2"),
    ("junk", "Reste & Müll", "🗑", "#FF453A"),
    ("review", "Prüfen", "❔", "#64D2FF"),
    ("largest", "Größte Dateien", "⬛", "#8E8E93"),
]


ORG_QUICK_FOLDERS = [("🖥", "Desktop", "desktop"), ("📄", "Dokumente", "documents"),
                     ("⬇", "Downloads", "downloads"), ("🖼", "Bilder", "pictures"),
                     ("🎬", "Videos", "videos"), ("🎵", "Musik", "music")]


def org_move_into(files: list, dest: Path) -> tuple:
    """Dateien in einen anderen Ordner legen (nie überschreiben). Protokoll im Format von
    org_undo, damit „Letzte Sortierung rückgängig“ auch das zurücknimmt.
    → ([[alt, neu], …], Fehlermeldungen, Protokoll oder None bei verschiedenen Laufwerken)."""
    dest = Path(dest)
    moves, failed = [], []
    try:
        dest.mkdir(parents=True, exist_ok=True)
        root = Path(os.path.commonpath([str(dest)] + [str(Path(f.path).parent) for f in files]))
    except ValueError:                                   # anderes Laufwerk
        root = None
    except OSError as e:
        return [], [f"{dest}: {e.strerror or e}"], None
    journal = None
    if root is not None:
        JOURNAL_DIR.mkdir(exist_ok=True)
        stamp, n = time.strftime("%Y%m%d-%H%M%S"), 1
        journal = JOURNAL_DIR / f"organize-{stamp}.json"
        while journal.exists():
            n += 1
            journal = JOURNAL_DIR / f"organize-{stamp}-{n}.json"
    for f in files:
        src = Path(f.path)
        if not src.is_file():
            failed.append(f"{src.name}: nicht mehr vorhanden")
            continue
        if src.parent == dest:
            continue
        target = unique_dest(dest, src.name)
        try:
            shutil.move(str(src), str(target))
        except OSError as e:
            failed.append(f"{src.name}: {e.strerror or e}")
            continue
        moves.append([str(src), str(target)])
        if journal:
            journal.write_text(json.dumps({"root": str(root), "created": [], "moves": moves},
                                          ensure_ascii=False, indent=1), encoding="utf-8")
    return moves, failed, journal


def org_research_dest(f, category: str) -> str:
    """Zielordner nach der Recherche: Installer und Archive nach Thema
    („Programme & Installer/Spiele“), Unbekanntes unter „Sonstiges/<Thema>“."""
    base = org_dest_for(f)
    if not category:
        return base
    if f.kind in ("installer", "archive") or not base:
        return f"{base or 'Sonstiges'}/{category}"
    return base


class OrganizerView(QWidget):
    """Analysiert einen beliebigen Ordner, schlägt Ordnung vor und räumt sicher auf –
    mit Recherche zu Installern, Archiven, Videos & Co. Seite im Hauptfenster."""

    def __init__(self, mgr: "FenceManager", folder: Path, autostart=True, boxes_fn=None,
                 parent=None):
        super().__init__(parent)
        self.mgr, self.folder = mgr, Path(folder)
        self.plan = None
        self.boxes_fn = boxes_fn                       # → Namen der Desktop-Boxen
        self.box_move = None                           # fn(Dateien, Box) im Hauptfenster
        self.on_swipe = None                           # fn(Dateien, Titel, Plan) im Hauptfenster
        self.research: dict[str, dict] = {}           # Pfad → Ergebnis der Recherche
        self._research: ResearchJob | None = None
        self._worker: OrgWorker | None = None
        self.setWindowTitle("Ordner-Assistent")
        self._build()
        self.resize(1240, 860)
        if autostart:
            QTimer.singleShot(0, self.analyze)

    # ---------- Aufbau ----------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title_lbl = ElideLabel("📁  Ordner-Assistent")
        self.title_lbl.setObjectName("h2")
        self.sub = ElideLabel("")
        self.sub.setObjectName("sub")
        titles.addWidget(self.title_lbl)
        titles.addWidget(self.sub)
        head.addLayout(titles, 1)
        self.quick: dict[str, GlassButton] = {}
        for glyph, label, key in ORG_QUICK_FOLDERS:
            b = GlassButton(f"{glyph}  {label}", small=True)
            b.clicked.connect(lambda _=False, k=key: self.set_folder(org_known_folder(k)))
            self.quick[key] = b
            head.addWidget(b)
        head.addSpacing(10)
        root.addLayout(head)
        head = QHBoxLayout()
        self.t_recursive = Toggle("Unterordner einbeziehen", checked=True)
        self.t_reorg = Toggle("Unterordner neu ordnen")
        self.t_recursive.toggled.connect(lambda _: self.analyze())
        self.t_reorg.toggled.connect(lambda _: self.analyze())
        self.b_folder = GlassButton("Ordner …", small=True)
        self.b_folder.clicked.connect(self.choose_folder)
        self.b_again = GlassButton("↻  Neu analysieren", small=True)
        self.b_again.clicked.connect(self.analyze)
        for w in (self.t_recursive, self.t_reorg):
            head.addWidget(w)
            head.addSpacing(8)
        head.addStretch(1)
        head.addWidget(self.b_folder)
        head.addWidget(self.b_again)
        root.addLayout(head)

        # Fortschritt
        self.progress_row = QWidget()
        pr = QHBoxLayout(self.progress_row)
        pr.setContentsMargins(0, 0, 0, 0)
        self.progress_lbl = QLabel("")
        self.progress_lbl.setObjectName("sub")
        self.progress_bar = OrgProgressBar()
        self.b_cancel = GlassButton("Abbrechen", small=True)
        self.b_cancel.clicked.connect(lambda: self._worker and self._worker.cancel())
        pr.addWidget(self.progress_lbl)
        pr.addWidget(self.progress_bar, 1)
        pr.addWidget(self.b_cancel)
        root.addWidget(self.progress_row)

        # Kacheln
        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.tiles: dict[str, OrgTile] = {}
        for key, title, glyph, color in ORG_SECTIONS:
            tile = OrgTile(glyph, title, color)
            tile.clicked.connect(lambda _=False, k=key: self.show_section(k))
            self.tiles[key] = tile
            tiles.addWidget(tile)
        root.addLayout(tiles)

        # Inhalt
        self.panel = GlassPanel()
        pl = QVBoxLayout(self.panel)
        pl.setContentsMargins(16, 14, 16, 12)
        top = QHBoxLayout()
        self.sec_title = QLabel("")
        self.sec_title.setObjectName("h2")
        self.sec_hint = QLabel("")
        self.sec_hint.setObjectName("sub")
        self.sec_hint.setWordWrap(True)
        tv = QVBoxLayout()
        tv.setSpacing(2)
        tv.addWidget(self.sec_title)
        tv.addWidget(self.sec_hint)
        top.addLayout(tv, 1)
        self.b_all = GlassButton("Alle", small=True)
        self.b_none = GlassButton("Keine", small=True)
        self.b_all.clicked.connect(lambda: self._check_all(True))
        self.b_none.clicked.connect(lambda: self._check_all(False))
        top.addWidget(self.b_all)
        top.addWidget(self.b_none)
        pl.addLayout(top)
        self.stack = QStackedWidget()
        self.trees: dict[str, QTreeWidget] = {}
        for key, *_ in ORG_SECTIONS:
            tree = self._make_tree()
            self.trees[key] = tree
            self.stack.addWidget(tree)
        pl.addWidget(self.stack, 1)

        actions = QHBoxLayout()
        self.b_undo = GlassButton("↶  Letzte Sortierung rückgängig", small=True)
        self.b_undo.clicked.connect(self.undo_last)
        actions.addWidget(self.b_undo)
        actions.addStretch(1)
        self.b_swipe = GlassButton("Einzeln durchwischen", small=True)
        self.b_swipe.clicked.connect(self.swipe_selected)
        self.b_secondary = GlassButton("", small=True)
        self.b_secondary.clicked.connect(lambda: self._run_action(secondary=True))
        self.b_primary = GlassButton("", primary=True)
        self.b_primary.clicked.connect(lambda: self._run_action(secondary=False))
        actions.addWidget(self.b_swipe)
        actions.addWidget(self.b_secondary)
        actions.addWidget(self.b_primary)
        pl.addLayout(actions)
        root.addWidget(self.panel, 1)
        self.current = "moves"
        self.busy_widgets = [self.b_folder, self.b_again, self.t_recursive, self.t_reorg,
                             self.panel] + list(self.tiles.values()) + list(self.quick.values())

    def _make_tree(self) -> QTreeWidget:
        t = QTreeWidget()
        t.setObjectName("org")
        t.setColumnCount(4)
        t.setHeaderLabels(["Name", "Grund", "Größe", "Datum"])
        t.setIconSize(QSize(20, 20))
        t.setUniformRowHeights(True)
        t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        t.setContextMenuPolicy(Qt.CustomContextMenu)
        t.customContextMenuRequested.connect(lambda pos, tree=t: self._item_menu(tree, pos))
        t.itemDoubleClicked.connect(self._open_item)
        h = t.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.Interactive)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        t.setColumnWidth(1, 360)
        return t

    def paintEvent(self, _):
        if self.isWindow():                  # nur als eigenes Fenster (Tests)
            p = QPainter(self)
            paint_backdrop(p, self.rect(), overlay_only=False)

    def set_folder(self, folder: Path):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        self.folder = Path(folder)
        self.plan = None
        self.populate()
        self.analyze()

    # ---------- Analyse ----------
    def _protected(self) -> set:
        return set(self.mgr.cfg.setdefault("protected", []))

    def _ignored(self) -> set:
        return set(self.mgr.cfg.setdefault("org_ignore", []))

    def set_busy(self, text: str | None):
        for w in self.busy_widgets:
            w.setEnabled(text is None)
        self.progress_row.setVisible(text is not None)
        if text:
            self.progress_lbl.setText(text)
            self.progress_bar.set_value(0, 0)

    def analyze(self):
        if self._worker and self._worker.isRunning():
            return
        folder, recursive, reorg = self.folder, self.t_recursive.isChecked(), self.t_reorg.isChecked()
        protected = self._protected()

        def job(progress, cancelled):
            programs = org_installed_programs()
            return org_analyze(folder, recursive=recursive, reorganize_subfolders=reorg,
                               programs=programs, progress=progress, cancelled=cancelled,
                               protected_paths=protected)

        self.set_busy("Analysiere …")
        if self._research:
            self._research.cancel = True
        self.research = {}
        self.title_lbl.setText(f"📁  {self.folder.name or self.folder}")
        self.sub.setText(str(self.folder))
        self.sub.setToolTip(str(self.folder))
        self._worker = OrgWorker(job, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_plan)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, stage, done, total):
        self.progress_lbl.setText(f"{stage} …  {done:,} / {total:,}".replace(",", ".")
                                  if total else f"{stage} …")
        self.progress_bar.set_value(done, total)

    def _on_failed(self, msg):
        self.set_busy(None)
        QMessageBox.warning(self, "Ordner-Assistent", f"Analyse fehlgeschlagen:\n\n{msg}")

    def _on_plan(self, plan):
        self.set_busy(None)
        ignored = self._ignored()
        if ignored:                                     # „Nicht anfassen“ respektieren
            keep = lambda f: str(f.path) not in ignored
            plan.moves = [m for m in plan.moves if keep(m.file)]
            plan.junk = [f for f in plan.junk if keep(f)]
            plan.review = [f for f in plan.review if keep(f)]
        self.plan = plan
        st = plan.stats
        note = "  ·  ⚠ nur die ersten Dateien analysiert" if plan.truncated else ""
        self.sub.setToolTip(str(self.folder))
        self.sub.setText(f"{self.folder}  ·  {st.get('files', len(plan.files)):,} Dateien  ·  "
                         f"{human_size(st.get('bytes', 0)).replace(',', '#')}  ·  "
                         f"{st.get('seconds', 0):.1f} s".replace(",", ".").replace("#", ",")
                         + note)
        self.populate()
        self._start_research()

    # ---------- Recherche ----------
    def _research_files(self) -> list:
        return [f for f in self.plan.files if research_file_ok(f) and research_file_query(f.path)]

    def _start_research(self):
        on, srcs = research_settings(self.mgr.cfg)
        files = self._research_files() if self.plan else []
        if not (on and srcs and files):
            self.tiles["research"].set_value("–", "Recherche aus" if not (on and srcs)
                                             else "nichts nachzuschlagen")
            return
        if self._research:
            self._research.cancel = True
        items = [Item(f.path, "user", "file") for f in files]
        self._research = job = ResearchJob(items, srcs, self,
                                           query_fn=lambda it: research_file_query(it.path))
        job.progress.connect(lambda n, total, name: job is self._research and
                             self.tiles["research"].set_value(f"{n}/{total}", f"prüfe „{name}“ …"))
        job.finished.connect(lambda res, st, j=job: self._research_done(j, res, st))
        self.tiles["research"].set_value("…", f"{len(files)} Dateien werden geprüft")
        job.start()

    def _research_done(self, job, results: dict, status: dict):
        if job is not self._research or job.cancel or self.plan is None:
            return
        self._research = None
        self.research = results
        self.research_status = status
        by_path = {str(f.path): f for f in self.plan.files}
        for path, res in results.items():
            f = by_path.get(path)
            if f is None or not res.get("evidence"):
                continue
            first = res["evidence"][0]
            f.add_reason(f"🔎 {first['label']} – {first['text']}"
                         + (f" ({', '.join(res['agree'])})" if res.get("agree") else ""))
            dest = org_research_dest(f, res.get("category", ""))
            parent = Path(f.rel).parent.as_posix()
            if dest and not (parent == dest or parent.startswith(dest + "/")) \
                    and f.tier != ORG_JUNK:
                old = next((m for m in self.plan.moves if m.file is f), None)
                if old:
                    old.dest_rel = dest
                else:
                    self.plan.moves.append(OrgMove(f, dest, False))
        self.populate()

    # ---------- Darstellung ----------
    def _file_item(self, f: OrgFile, checked: bool | None, extra_reason: str = "") -> QTreeWidgetItem:
        main = org_main_reason(f)
        rest = [r for r in f.reasons if r != main]
        reasons = " · ".join(([extra_reason] if extra_reason else []) + ([main] if main else []) + rest)
        it = QTreeWidgetItem([f.rel, reasons, human_size(f.size),
                              time.strftime("%d.%m.%Y", time.localtime(f.mtime))])
        it.setIcon(0, icon_cache().get(f.path))
        it.setToolTip(0, str(f.path))
        it.setToolTip(1, reasons)
        it.setData(0, Qt.UserRole, f)
        it.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
        if checked is not None:
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
        return it

    def _group_item(self, tree: QTreeWidget, title: str, sub: str = "", checkable=True) -> QTreeWidgetItem:
        g = QTreeWidgetItem([title, sub, "", ""])
        f = g.font(0)
        f.setBold(True)
        g.setFont(0, f)
        if checkable:
            g.setFlags(g.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
        tree.addTopLevelItem(g)
        return g

    def populate(self):
        plan = self.plan
        for tree in self.trees.values():
            tree.clear()
        if plan is None:
            return
        # Neue Ordnung – nach Zielordner
        t = self.trees["moves"]
        groups: dict[str, list] = {}
        for i, m in enumerate(plan.moves):
            groups.setdefault(m.dest_rel, []).append((i, m))
        for dest in sorted(groups):
            g = self._group_item(t, f"📁  {dest}", f"{len(groups[dest])} Datei(en)")
            for i, m in sorted(groups[dest], key=lambda im: im[1].file.rel.lower()):
                it = self._file_item(m.file, m.checked)
                it.setData(1, Qt.UserRole, i)
                g.addChild(it)
            g.setExpanded(len(groups) <= 12)
        # Recherche – nach Thema laut offenen Quellen
        t = self.trees["research"]
        by_path = {str(f.path): f for f in plan.files}
        topics: dict[str, list] = {}
        for path, res in self.research.items():
            f = by_path.get(path)
            if f is not None:
                topics.setdefault(res.get("category") or ("Gefunden, ohne Thema"
                                  if res.get("evidence") else "Kein Treffer"), []).append((f, res))
        order = sorted(topics, key=lambda k: (k in ("Gefunden, ohne Thema", "Kein Treffer"), k))
        for topic in order:
            g = self._group_item(t, f"🔎  {topic}", f"{len(topics[topic])} Datei(en)")
            for f, res in sorted(topics[topic], key=lambda fr: fr[0].rel.lower()):
                lines = research_lines(res)
                it = self._file_item(f, bool(res.get("category")) and f.tier != ORG_JUNK,
                                     lines[0] if lines else "in keiner Quelle gefunden")
                it.setToolTip(1, "\n".join(lines) or "in keiner Quelle gefunden")
                dest = org_research_dest(f, res.get("category", ""))
                it.setData(1, Qt.UserRole + 3, dest)
                g.addChild(it)
            g.setExpanded(len(order) <= 8)
        # Wichtig – nach Kategorie
        t = self.trees["protect"]
        cats: dict[str, list] = {}
        for f in plan.protect:
            cats.setdefault(ORG_CATEGORIES.get(f.category, {}).get("label", f.category), []).append(f)
        for label in sorted(cats):
            g = self._group_item(t, f"🔒  {label}", f"{len(cats[label])} Datei(en)")
            for f in sorted(cats[label], key=lambda x: x.rel.lower()):
                g.addChild(self._file_item(f, True))
            g.setExpanded(True)
        # Duplikate – nach Original
        t = self.trees["dups"]
        for grp in plan.dup_groups:
            keeper = next((f for f in grp if not f.dup_of), grp[0])
            copies = [f for f in grp if f.dup_of]
            if not copies:
                continue
            g = self._group_item(t, f"Original: {keeper.rel}",
                                 f"{len(copies)} Kopie(n) · {human_size(keeper.size * len(copies))}")
            for f in copies:
                g.addChild(self._file_item(f, True))
            g.setExpanded(len(plan.dup_groups) <= 30)
        # Alte Versionen
        t = self.trees["versions"]
        for key, grp in plan.versions.items():
            latest = next((f for f in grp if f.version_latest), None)
            olds = [f for f in grp if not f.version_latest]
            if not olds:
                continue
            g = self._group_item(t, f"Neueste: {latest.rel if latest else key}",
                                 f"{len(olds)} ältere Version(en)")
            for f in sorted(olds, key=lambda x: -x.mtime):
                g.addChild(self._file_item(f, False))
            g.setExpanded(True)
        # Reste & Müll (ohne Duplikate – die haben eine eigene Ansicht)
        t = self.trees["junk"]
        rest = [f for f in plan.junk if not f.dup_of]
        cats = {}
        for f in rest:
            cats.setdefault(ORG_CATEGORIES.get(f.category, {}).get("label", f.category), []).append(f)
        for label in sorted(cats):
            g = self._group_item(t, f"🗑  {label}", f"{len(cats[label])} · "
                                 f"{human_size(sum(x.size for x in cats[label]))}")
            for f in cats[label]:
                g.addChild(self._file_item(f, True))
            g.setExpanded(True)
        if plan.empty_dirs:
            g = self._group_item(t, "📂  Leere Ordner", f"{len(plan.empty_dirs)}")
            for d in plan.empty_dirs:
                it = QTreeWidgetItem([os.path.relpath(str(d), str(plan.root)), "Leerer Ordner", "", ""])
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Checked)
                it.setData(0, Qt.UserRole + 2, str(d))
                it.setIcon(0, icon_cache().get(d))
                g.addChild(it)
            g.setExpanded(True)
        # Prüfen
        t = self.trees["review"]
        cats = {}
        for f in plan.review:
            cats.setdefault(ORG_CATEGORIES.get(f.category, {}).get("label", f.category), []).append(f)
        for label in sorted(cats, key=lambda k: -len(cats[k])):
            g = self._group_item(t, f"❔  {label}", f"{len(cats[label])} Datei(en)")
            for f in sorted(cats[label], key=lambda x: x.rel.lower()):
                g.addChild(self._file_item(f, False))
            g.setExpanded(len(cats) <= 6)
        # Größte Dateien
        t = self.trees["largest"]
        big = sorted((f for f in plan.files if not f.in_project), key=lambda f: -f.size)[:60]
        for f in big:
            tag = {ORG_PROTECT: "🔒 wichtig", ORG_JUNK: "🗑 Vorschlag: löschen"}.get(f.tier, "")
            t.addTopLevelItem(self._file_item(f, False, tag))

        self._update_tiles()
        self.show_section(self.current)

    def _update_tiles(self):
        p = self.plan
        dup_copies = [f for f in p.junk if f.dup_of]
        rest = [f for f in p.junk if not f.dup_of]
        olds = [f for g in p.versions.values() for f in g if not f.version_latest]
        self.tiles["moves"].set_value(str(len(p.moves)), f"in {len({m.dest_rel for m in p.moves})} Ordner")
        if self.research:
            found = sum(1 for r in self.research.values() if r.get("category"))
            self.tiles["research"].set_value(str(found), f"von {len(self.research)} zugeordnet")
        self.tiles["protect"].set_value(str(len(p.protect)), "nie zum Löschen vorgeschlagen")
        self.tiles["dups"].set_value(human_size(sum(f.size for f in dup_copies)),
                                     f"{len(dup_copies)} Kopien")
        self.tiles["versions"].set_value(str(len(olds)), f"in {len(p.versions)} Gruppen")
        self.tiles["junk"].set_value(human_size(sum(f.size for f in rest)),
                                     f"{len(rest)} Dateien · {len(p.empty_dirs)} leere Ordner")
        self.tiles["review"].set_value(str(len(p.review)), "selbst entscheiden")
        self.tiles["largest"].set_value(human_size(sum(f.size for f in sorted(
            p.files, key=lambda f: -f.size)[:60])), "die 60 größten")

    SECTION_INFO = {
        "research": ("Recherche in offenen Quellen",
                     "Installer, Archive, Videos, Musik und Unbekanntes werden in Wikidata, "
                     "Wikipedia, Flathub und Chocolatey nachgeschlagen (nur der bereinigte Name; "
                     "nie Dokumente oder Fotos). Häkchen = nach Thema einsortieren.",
                     "Ausgewählte nach Thema einsortieren", "Ausgewählte in den Papierkorb"),
        "moves": ("Vorgeschlagene Ordnung",
                  "Dateien werden innerhalb dieses Ordners in Unterordner verschoben – nichts wird gelöscht. "
                  "Häkchen = übernehmen. Rückgängig jederzeit möglich.",
                  "Ausgewählte einsortieren", None),
        "protect": ("Wichtig & dauerhaft behalten",
                    "Lebenslauf, Zeugnisse, Urkunden, Verträge, Steuer, Gehalt, Versicherungen, Ausweise, "
                    "Gesundheit und persönliche Fotos. Diese Dateien werden nie zum Löschen vorgeschlagen.",
                    "🗄  Im Tresor sichern …", "🔒  Als geschützt merken"),
        "dups": ("Exakte Duplikate",
                 "Byte-identische Kopien. Das Original bleibt immer erhalten; nur die Kopien gehen in den Papierkorb.",
                 "Kopien in den Papierkorb", None),
        "versions": ("Alte Versionen",
                     "„final“, „v2“, „Kopie“, „(1)“ … – die neueste Version bleibt, ältere kannst du löschen.",
                     "Ausgewählte in den Papierkorb", None),
        "junk": ("Reste & Müll",
                 "Office-Sperrdateien, Temporäres, abgebrochene Downloads, leere Dateien und Ordner, "
                 "Installer bereits installierter Programme. Alles geht in den Papierkorb (wiederherstellbar).",
                 "Ausgewählte in den Papierkorb", None),
        "review": ("Selbst entscheiden",
                   "Unklare Dateien. Am schnellsten: markieren und „Einzeln durchwischen“ (links = Papierkorb, "
                   "rechts = behalten).",
                   "Ausgewählte in den Papierkorb", None),
        "largest": ("Größte Dateien",
                    "Die größten Platzfresser in diesem Ordner.",
                    "Ausgewählte in den Papierkorb", None),
    }

    def show_section(self, key: str):
        self.current = key
        for k, tile in self.tiles.items():
            tile.setChecked(k == key)
        self.stack.setCurrentWidget(self.trees[key])
        title, hint, primary, secondary = self.SECTION_INFO[key]
        self.sec_title.setText(title)
        self.sec_hint.setText(hint)
        self.b_primary.setText(primary)
        self.b_primary.updateGeometry()
        self.b_secondary.setVisible(secondary is not None)
        if secondary:
            self.b_secondary.setText(secondary)
            self.b_secondary.updateGeometry()
        self.b_swipe.setVisible(key in ("review", "versions", "largest", "junk", "dups"))
        self.b_undo.setEnabled(self._last_journal() is not None)

    # ---------- Auswahl ----------
    def _iter_items(self, tree: QTreeWidget):
        stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            yield it
            stack.extend(it.child(i) for i in range(it.childCount()))

    def _checked_files(self, key: str) -> list:
        return [it.data(0, Qt.UserRole) for it in self._iter_items(self.trees[key])
                if it.data(0, Qt.UserRole) is not None and it.checkState(0) == Qt.Checked]

    def _check_all(self, on: bool):
        for it in self._iter_items(self.trees[self.current]):
            if it.flags() & Qt.ItemIsUserCheckable and it.childCount() == 0:
                it.setCheckState(0, Qt.Checked if on else Qt.Unchecked)

    # ---------- Aktionen ----------
    def _run_action(self, secondary: bool):
        if self.plan is None:
            return
        key = self.current
        if key == "moves":
            return self.apply_moves()
        if key == "research":
            files = self._checked_files("research")
            if secondary:
                return self.trash(files)
            return self.apply_research_moves()
        if key == "protect":
            return self.remember_protected() if secondary else self.backup_vault()
        files = self._checked_files(key)
        dirs = []
        if key == "junk":
            dirs = [it.data(0, Qt.UserRole + 2) for it in self._iter_items(self.trees["junk"])
                    if it.data(0, Qt.UserRole + 2) and it.checkState(0) == Qt.Checked]
        self.trash(files, dirs)

    def apply_moves(self):
        idx = [it.data(1, Qt.UserRole) for it in self._iter_items(self.trees["moves"])
               if it.data(1, Qt.UserRole) is not None and it.checkState(0) == Qt.Checked]
        moves = [self.plan.moves[i] for i in idx]
        if not moves:
            return QMessageBox.information(self, "Ordner-Assistent", "Nichts ausgewählt.")
        n_dirs = len({m.dest_rel for m in moves})
        if QMessageBox.question(self, "Einsortieren",
                                f"{len(moves)} Dateien in {n_dirs} Ordner einsortieren?\n\n"
                                f"Es wird nichts gelöscht; „Rückgängig“ stellt alles wieder her.") \
                != QMessageBox.Yes:
            return
        JOURNAL_DIR.mkdir(exist_ok=True)
        journal = JOURNAL_DIR / f"organize-{time.strftime('%Y%m%d-%H%M%S')}.json"
        moved, failed = org_apply_moves(self.plan, moves, journal)
        msg = f"{moved} Dateien einsortiert."
        if failed:
            msg += "\n\nNicht verschoben:\n" + "\n".join(failed[:15])
        QMessageBox.information(self, "Ordner-Assistent", msg)
        self.analyze()

    def apply_research_moves(self):
        moves = [OrgMove(it.data(0, Qt.UserRole), it.data(1, Qt.UserRole + 3), True)
                 for it in self._iter_items(self.trees["research"])
                 if it.data(0, Qt.UserRole) is not None and it.checkState(0) == Qt.Checked
                 and it.data(1, Qt.UserRole + 3)]
        moves = [m for m in moves if Path(m.file.rel).parent.as_posix() != m.dest_rel]
        if not moves:
            return QMessageBox.information(self, "Ordner-Assistent", "Nichts ausgewählt.")
        if QMessageBox.question(self, "Nach Thema einsortieren",
                                f"{len(moves)} Datei(en) in {len({m.dest_rel for m in moves})} "
                                f"Ordner einsortieren?\n\nRückgängig jederzeit möglich.") \
                != QMessageBox.Yes:
            return
        JOURNAL_DIR.mkdir(exist_ok=True)
        journal = JOURNAL_DIR / f"organize-{time.strftime('%Y%m%d-%H%M%S')}.json"
        moved, failed = org_apply_moves(self.plan, moves, journal)
        msg = f"{moved} Dateien einsortiert."
        if failed:
            msg += "\n\nNicht verschoben:\n" + "\n".join(failed[:15])
        QMessageBox.information(self, "Ordner-Assistent", msg)
        self.analyze()

    def move_files(self, files: list, dest: Path):
        """Einordnen in einen anderen Ordner (Dokumente, Bilder, Desktop-Box …)."""
        files = [f for f in files if not f.in_project]
        if not files:
            return
        same_drive = os.path.splitdrive(str(dest))[0].lower() == \
            os.path.splitdrive(str(self.folder))[0].lower()
        if QMessageBox.question(self, "Verschieben",
                                f"{len(files)} Datei(en) nach\n{dest}\nverschieben?"
                                + ("" if same_drive else "\n\nAnderes Laufwerk – wird kopiert "
                                   "und kann nicht automatisch rückgängig gemacht werden.")) \
                != QMessageBox.Yes:
            return
        moves, failed, _journal = org_move_into(files, dest)
        msg = f"{len(moves)} Datei(en) verschoben nach {dest}."
        if failed:
            msg += "\n\nNicht verschoben:\n" + "\n".join(failed[:15])
        QMessageBox.information(self, "Verschieben", msg)
        self.analyze()

    def trash(self, files: list, dirs: list = ()):
        if not files and not dirs:
            return QMessageBox.information(self, "Ordner-Assistent", "Nichts ausgewählt.")
        size = sum(f.size for f in files)
        text = f"{len(files)} Datei(en) ({human_size(size)})"
        if dirs:
            text += f" und {len(dirs)} leere(n) Ordner"
        if QMessageBox.question(self, "Papierkorb", f"{text} in den Papierkorb verschieben?\n\n"
                                "Du kannst alles über den Papierkorb wiederherstellen.") \
                != QMessageBox.Yes:
            return
        done, failed = org_trash(files, safe_trash)
        for d in dirs:                                   # leere Ordner: vorher erneut prüfen
            dp = Path(d)
            try:
                still_empty = dp.is_dir() and not any(x.is_file() for x in dp.rglob("*"))
            except OSError:
                still_empty = False
            if still_empty and safe_trash(dp):
                done += 1
            elif dp.exists():
                failed.append(f"{dp.name}: nicht mehr leer oder gesperrt")
        msg = f"{done} Objekt(e) im Papierkorb."
        if failed:
            msg += f"\n\nÜbersprungen ({TRASH_FAIL_HINT}):\n" + "\n".join(failed[:15])
        QMessageBox.information(self, "Papierkorb", msg)
        self.analyze()

    def remember_protected(self):
        files = self._checked_files("protect")
        lst = self.mgr.cfg.setdefault("protected", [])
        new = [str(f.path) for f in files if str(f.path) not in lst]
        lst.extend(new)
        self.mgr.write_cfg()
        QMessageBox.information(self, "Geschützt",
                                f"{len(new)} Datei(en) zusätzlich als geschützt gemerkt.\n"
                                "Sie erscheinen nie mehr als Löschvorschlag – auch nicht im "
                                "Downloads-Aufräumen.")

    def backup_vault(self):
        files = self._checked_files("protect")
        if not files:
            return QMessageBox.information(self, "Tresor", "Nichts ausgewählt.")
        start = self.mgr.cfg.get("vault_dir") or str(Path.home())
        dest = QFileDialog.getExistingDirectory(
            self, "Tresor-Ordner wählen (z. B. OneDrive oder externe Festplatte)", start)
        if not dest:
            return
        self.mgr.cfg["vault_dir"] = dest
        self.mgr.write_cfg()

        def job(progress, cancelled):
            return org_backup(files, Path(dest), progress=progress, cancelled=cancelled)

        def done(res):
            self.set_busy(None)
            fails = res.get("failed", [])
            msg = (f"{res.get('copied', 0)} kopiert, {res.get('skipped', 0)} waren schon "
                   f"gesichert ({human_size(res.get('bytes', 0))}).\nZiel: {dest}")
            if fails:
                msg += "\n\nFehler:\n" + "\n".join(fails[:10])
            QMessageBox.information(self, "Tresor", msg)

        self.set_busy("Sichere in den Tresor …")
        self._worker = OrgWorker(job, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def swipe_selected(self):
        tree = self.trees[self.current]
        files = [it.data(0, Qt.UserRole) for it in tree.selectedItems()
                 if it.data(0, Qt.UserRole) is not None]
        if not files:
            files = self._checked_files(self.current) or [
                it.data(0, Qt.UserRole) for it in self._iter_items(tree)
                if it.data(0, Qt.UserRole) is not None]
        files = [f for f in files if f.tier != ORG_PROTECT or f.dup_of]
        if not files:
            return
        title = self.SECTION_INFO[self.current][0]
        if self.on_swipe:                              # im Hauptfenster: Seite „Aufräumen“
            return self.on_swipe([f.path for f in files], title, self.plan)
        win = CleanerView(self.mgr, self.folder, entries=[f.path for f in files],
                          title=title, hints=self.plan)
        win.destroyed.connect(self._after_swipe)
        win.setAttribute(Qt.WA_DeleteOnClose)
        win.show()
        self._swipe = win

    def _after_swipe(self, *_):
        """Nach dem Durchwischen neu analysieren – falls der Assistent noch offen ist."""
        try:
            if self.isVisible():
                self.analyze()
        except RuntimeError:                         # Fenster wurde inzwischen gelöscht
            pass

    def _last_journal(self) -> Path | None:
        js = sorted(JOURNAL_DIR.glob("organize-*[0-9].json")) if JOURNAL_DIR.is_dir() else []
        return js[-1] if js else None

    def undo_last(self):
        j = self._last_journal()
        if not j:
            return
        if QMessageBox.question(self, "Rückgängig", "Letzte Sortierung durch den "
                                "Ordner-Assistenten vollständig rückgängig machen?") != QMessageBox.Yes:
            return
        restored, failed = org_undo(j)
        msg = f"{restored} Datei(en) zurückgelegt."
        if failed:
            msg += "\n\n" + "\n".join(failed[:15])
        QMessageBox.information(self, "Rückgängig", msg)
        self.analyze()

    def choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Ordner analysieren", str(self.folder))
        if d:
            self.set_folder(Path(d))

    # ---------- Kontextmenü ----------
    def _open_item(self, it, _col=0):
        f = it.data(0, Qt.UserRole)
        if f is not None:
            self.mgr.launch(str(f.path))

    def _item_menu(self, tree: QTreeWidget, pos):
        it = tree.itemAt(pos)
        f = it.data(0, Qt.UserRole) if it else None
        if f is None:
            return
        m = glass_menu(self)
        sel = [x.data(0, Qt.UserRole) for x in tree.selectedItems()
               if x.data(0, Qt.UserRole) is not None]
        files = sel if f in sel else [f]
        m.addAction("▶  Öffnen", lambda: self.mgr.launch(str(f.path)))
        m.addAction("↗  Im Explorer zeigen", lambda: reveal_path(str(f.path)))
        res = self.research.get(str(f.path))
        if res and res.get("evidence"):
            info = m.addAction("🔎  " + research_lines(res)[0][:90])
            info.setEnabled(False)
        m.addSeparator()
        m.addMenu(self._move_menu(m, files))
        m.addAction(f"🗑  In den Papierkorb …" + (f" ({len(files)})" if len(files) > 1 else ""),
                    lambda: self.trash(files))
        m.addSeparator()
        cat = glass_menu(m, "Kategorie ändern")
        for key, c in ORG_CATEGORIES.items():
            if key in ("temp", "duplicate", "other") or not c["folder"]:
                continue
            a = cat.addAction(c["label"], lambda k=key: self.recategorize(f, k))
            a.setCheckable(True)
            a.setChecked(key == f.category)
        m.addMenu(cat)
        prot = str(f.path) in self._protected()
        m.addAction("🔓  Nicht mehr schützen" if prot else "🔒  Schützen (nie löschen)",
                    lambda: self.toggle_protect(f, not prot))
        m.addAction("🚫  Nicht anfassen (ausblenden)", lambda: self.ignore(f))
        m.exec(tree.viewport().mapToGlobal(pos))

    def _move_menu(self, parent: QMenu, files: list) -> QMenu:
        """„Verschieben nach“: Zielordner aus dem Plan, Standardordner, Desktop-Boxen."""
        mm = glass_menu(parent, "📦  Verschieben nach")
        if self.plan is not None:
            dests = sorted({org_dest_for(f) for f in files if org_dest_for(f)})
            for d in dests:
                mm.addAction(f"🗂  {d}  (hier)", lambda d=d: self.move_files(files, self.folder / d))
            if dests:
                mm.addSeparator()
        for glyph, label, key in ORG_QUICK_FOLDERS:
            target = org_known_folder(key)
            if os.path.normcase(str(target)) != os.path.normcase(str(self.folder)):
                mm.addAction(f"{glyph}  {label}", lambda t=target: self.move_files(files, t))
        boxes = self.boxes_fn() if self.boxes_fn else []
        if boxes and self.box_move:
            bm = glass_menu(mm, "▣  Desktop-Box")
            for name in boxes:
                bm.addAction(name, lambda n=name: self.box_move(files, n))
            mm.addMenu(bm)
        mm.addSeparator()
        mm.addAction("Anderer Ordner …", lambda: self._move_pick(files))
        return mm

    def _move_pick(self, files: list):
        d = QFileDialog.getExistingDirectory(self, "Zielordner wählen", str(self.folder))
        if d:
            self.move_files(files, Path(d))

    def recategorize(self, f: OrgFile, key: str):
        f.category = key
        f.tier = ORG_CATEGORIES[key]["tier"]
        f.add_reason(f"Manuell: {ORG_CATEGORIES[key]['label']}")
        dest = org_dest_for(f)
        plan = self.plan
        plan.moves = [m for m in plan.moves if m.file is not f]
        plan.junk = [x for x in plan.junk if x is not f]
        plan.review = [x for x in plan.review if x is not f]
        if f.tier == ORG_PROTECT and f not in plan.protect:
            plan.protect.append(f)
        parent = Path(f.rel).parent.as_posix()
        if dest and not (parent == dest or parent.startswith(dest + "/")):
            plan.moves.append(OrgMove(f, dest, True))
        self.populate()

    def toggle_protect(self, f: OrgFile, on: bool):
        lst = self.mgr.cfg.setdefault("protected", [])
        if on and str(f.path) not in lst:
            lst.append(str(f.path))
        elif not on and str(f.path) in lst:
            lst.remove(str(f.path))
        self.mgr.write_cfg()
        if on:
            f.tier = ORG_PROTECT
            self.plan.junk = [x for x in self.plan.junk if x is not f or x.dup_of]
            self.plan.review = [x for x in self.plan.review if x is not f]
            if f not in self.plan.protect:
                self.plan.protect.append(f)
        self.populate()

    def ignore(self, f: OrgFile):
        lst = self.mgr.cfg.setdefault("org_ignore", [])
        if str(f.path) not in lst:
            lst.append(str(f.path))
            self.mgr.write_cfg()
        p = self.plan
        p.moves = [m for m in p.moves if m.file is not f]
        p.junk = [x for x in p.junk if x is not f]
        p.review = [x for x in p.review if x is not f]
        self.populate()

    def closeEvent(self, e):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        if self._research:
            self._research.cancel = True
        super().closeEvent(e)


class OrgProgressBar(QWidget):
    """Schmaler Glas-Fortschrittsbalken (unbestimmt, wenn total == 0)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(10)
        self.done, self.total, self._phase = 0, 0, 0.0
        self._timer = QTimer(self, interval=30)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_value(self, done: int, total: int):
        self.done, self.total = done, total
        self.update()

    def _tick(self):
        if self.isVisible() and not self.total:
            self._phase = (self._phase + 0.012) % 1.0
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint_glass(p, r, r.height() / 2, strength=1.0)
        if self.total:
            w = r.width() * min(1.0, self.done / max(1, self.total))
            seg = QRectF(r.left(), r.top(), w, r.height())
        else:
            w = r.width() * 0.25
            seg = QRectF(r.left() + (r.width() + w) * self._phase - w, r.top(), w, r.height())
        path = QPainterPath()
        path.addRoundedRect(seg.intersected(r), r.height() / 2, r.height() / 2)
        p.fillPath(path, ACCENT)
