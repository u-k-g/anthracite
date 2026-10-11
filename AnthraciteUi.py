# SPDX-License-Identifier: LGPL-2.1-or-later
"""Small Qt additions around stock FreeCAD; the bridge does not depend on these."""
from pathlib import Path
from collections import deque
import xml.etree.ElementTree as ET

import FreeCAD as App
import FreeCADGui as Gui
from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import getCppPointer, isValid

_ui = None


def apply_defaults():
    """Merge portable defaults once, keeping a backup and subsequent user edits."""
    preferences = App.ParamGet("User parameter:BaseApp/Preferences/Anthracite")
    if preferences.GetBool("DefaultsApplied", False):
        return
    backup = Path(App.getUserAppDataDir()) / "Anthracite" / "preferences-before.cfg"
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        App.ParamGet("User parameter:BaseApp").Export(str(backup))
    backup.chmod(0o600)
    config = Path(__file__).parent / "Anthracite Dark" / "Anthracite Dark.cfg"
    setters = {"FCText": "SetString", "FCBool": "SetBool", "FCInt": "SetInt",
               "FCUInt": "SetUnsigned", "FCFloat": "SetFloat"}

    def merge(group, path):
        parameters = App.ParamGet("User parameter:" + path)
        for entry in group:
            if entry.tag == "FCParamGroup":
                merge(entry, path + "/" + entry.attrib["Name"])
            else:
                value = (entry.text or "") if entry.tag == "FCText" else entry.attrib["Value"]
                if entry.tag == "FCBool":
                    value = bool(int(value))
                elif entry.tag in ("FCInt", "FCUInt"):
                    value = int(value)
                elif entry.tag == "FCFloat":
                    value = float(value)
                getattr(parameters, setters[entry.tag])(entry.attrib["Name"], value)

    root = ET.parse(config).getroot().find("FCParamGroup/FCParamGroup")
    if root is None or root.attrib["Name"] != "BaseApp":
        raise ValueError("The bundled preference pack has no BaseApp group")
    merge(root, "BaseApp")
    preferences.SetBool("DefaultsApplied", True)
    App.saveParameter()


class CompactTabs(QtWidgets.QTabBar):
    """Mirror the native selector, keeping its workbench activation behavior."""
    def __init__(self, source, parent):
        super().__init__(parent)
        self.source = source
        self.snapshot = None
        self.setObjectName("AnthraciteWorkbenchTabs")
        self.setMouseTracking(True)
        self.setExpanding(False)
        self.setUsesScrollButtons(True)
        self.setIconSize(QtCore.QSize(16, 16))
        self.currentChanged.connect(self.activate)
        self.sync()

    def sync(self):
        combo = isinstance(self.source, QtWidgets.QComboBox)
        labels = [(self.source.itemText(i) if combo else
                   self.source.tabData(i) or self.source.tabText(i),
                   (self.source.itemIcon(i) if combo else self.source.tabIcon(i)))
                  for i in range(self.source.count())]
        snapshot = [(label, icon.cacheKey()) for label, icon in labels]
        blocked = self.blockSignals(True)
        if snapshot != self.snapshot:
            while self.count():
                self.removeTab(0)
            for label, icon in labels:
                index = self.addTab(icon, label)
                self.setTabData(index, label)
                self.setAccessibleTabName(index, label)
            self.snapshot = snapshot
        self.setCurrentIndex(self.source.currentIndex())
        self.blockSignals(blocked)
        self.update_labels()

    def activate(self, index):
        self.source.setCurrentIndex(index)
        if isinstance(self.source, QtWidgets.QComboBox):
            self.source.activated.emit(index)
        self.update_labels()

    def update_labels(self):
        for index in range(self.count()):
            expanded = index == self.currentIndex()
            self.setTabText(index, self.tabData(index) if expanded else "")
        self.updateGeometry()

    def paintEvent(self, event):
        # Native tab painting reserves label space beside an icon even when the
        # label is collapsed. Center icon-only tabs as the original selector did.
        painter = QtWidgets.QStylePainter(self)
        order = [i for i in range(self.count()) if i != self.currentIndex()]
        if self.currentIndex() >= 0:
            order.append(self.currentIndex())
        for index in order:
            option = QtWidgets.QStyleOptionTab()
            self.initStyleOption(option, index)
            if option.text or option.icon.isNull():
                painter.drawControl(QtWidgets.QStyle.CE_TabBarTab, option)
            else:
                painter.drawControl(QtWidgets.QStyle.CE_TabBarTabShape, option)
                rectangle = QtCore.QRect(QtCore.QPoint(), option.iconSize)
                rectangle.moveCenter(option.rect.center())
                option.icon.paint(painter, rectangle)


class Eyedropper:
    def __init__(self, ui):
        self.ui = ui
        self.view = None
        self.callback = None
        self.document = None

    def GetResources(self):
        return {"MenuText": "Pick geometry reference", "ToolTip": "Copy a checked geometry reference (Esc cancels)",
                "Pixmap": str(Path(__file__).parent / "Resources" / "icons" / "color-picker.svg")}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        if self.view is not None:
            self.cancel("Geometry pick cancelled")
            return
        if App.ActiveDocument is None or Gui.activeDocument() is None:
            self.ui.preview_message("Open a document before picking geometry")
            return
        from pivy import coin
        self.document = App.ActiveDocument.Name
        self.view = Gui.activeDocument().activeView()
        try:
            self.callback = self.view.addEventCallbackPivy(coin.SoMouseButtonEvent.getClassTypeId(), self.pick)
            App.addDocumentObserver(self)
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CrossCursor)
        except Exception:
            self.cancel()
            raise
        self.ui.pick_action.setChecked(True)
        self.ui.preview_message("Click a face, edge, or vertex to copy its reference · Esc cancels")

    def pick(self, callback):
        from pivy import coin
        event = callback.getEvent()
        if event.getState() != coin.SoButtonEvent.DOWN or event.getButton() != coin.SoMouseButtonEvent.BUTTON1:
            return
        callback.setHandled()
        try:
            if App.ActiveDocument is None or App.ActiveDocument.Name != self.document:
                raise ValueError("The active document changed. Pick again in the current document.")
            position = tuple(event.getPosition().getValue())
            info = self.view.getObjectInfo(position)
            if not info:
                self.ui.preview_message("No geometry here · click a face, edge, or vertex · Esc cancels")
                return
            self.copy_reference(info)
        except Exception as error:
            self.ui.preview_message(f"Could not pick geometry: {error}")
        self.cancel()

    def copy_reference(self, info):
        import AnthraciteExecutor
        encoded = AnthraciteExecutor.picked_reference_json(
            info["Document"], info["Object"], info.get("Component", ""),
            info["x"], info["y"], info["z"])
        QtWidgets.QApplication.clipboard().setText(encoded)
        self.ui.preview_message("Geometry reference copied")

    def cancel(self, message=None):
        if self.callback is not None:
            from pivy import coin
            try:
                self.view.removeEventCallbackPivy(coin.SoMouseButtonEvent.getClassTypeId(), self.callback)
            except RuntimeError:  # A closed document can already have destroyed its viewer.
                pass
            QtWidgets.QApplication.restoreOverrideCursor()
            App.removeDocumentObserver(self)
        self.view = self.callback = self.document = None
        self.ui.pick_action.setChecked(False)
        if message:
            self.ui.preview_message(message)

    def slotDeletedDocument(self, document):
        if document.Name == self.document:
            self.cancel("Geometry pick cancelled: document closed")

    def slotActivateDocument(self, document):
        if document is None or document.Name != self.document:
            self.cancel("Geometry pick cancelled: active document changed")


class Interface(QtCore.QObject):
    def __init__(self):
        self.window = Gui.getMainWindow()
        super().__init__(self.window)
        self.tabs = None
        self.notification_button = None
        self.seen_notifications = {}
        self.notifications = deque(maxlen=1000)
        self.application = QtWidgets.QApplication.instance()
        self.original_palette = QtGui.QPalette(self.application.palette())
        self.palette_applied = False
        # QSS resolves palette() colors when it is polished. Set the palette
        # first, as the old Chrome did, rather than caching Fusion gray brushes.
        self.update_palette()
        stylesheet = (Path(__file__).parent / "Resources" / "ui.qss").read_text()
        self.window.setStyleSheet(self.window.styleSheet() + "\n" + stylesheet)
        self.picker = Eyedropper(self)
        Gui.addCommand("Anthracite_PickGeometry", self.picker)
        self.toolbar = QtWidgets.QToolBar("Anthracite", self.window)
        self.toolbar.setObjectName("AnthraciteTools")
        self.toolbar.setIconSize(QtCore.QSize(16, 16))
        self.toolbar.setMovable(False)
        spacer = QtWidgets.QWidget(self.toolbar)
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.toolbar.addWidget(spacer)
        self.window.addToolBar(QtCore.Qt.TopToolBarArea, self.toolbar)
        self.pick_action = QtGui.QAction(QtGui.QIcon(self.picker.GetResources()["Pixmap"]), "Pick geometry reference", self.toolbar)
        self.pick_action.setObjectName("AnthracitePickGeometry")
        self.pick_action.setCheckable(True)
        # Both the global button and shortcut invoke the registered FreeCAD command.
        self.pick_action.setToolTip("Pick geometry reference (Ctrl+Shift+E)")
        self.pick_action.triggered.connect(lambda: Gui.runCommand("Anthracite_PickGeometry"))
        self.toolbar.addAction(self.pick_action)
        self.shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Shift+E"), self.window)
        self.shortcut.activated.connect(lambda: Gui.runCommand("Anthracite_PickGeometry"))
        self.preview = QtWidgets.QToolButton(self.window.statusBar())
        self.preview.setObjectName("AnthraciteConsolePreview")
        self.preview.setText("Console · No messages yet")
        self.preview.setIcon(QtGui.QIcon(str(Path(__file__).parent / "Resources" / "icons" / "terminal.svg")))
        self.preview.setIconSize(QtCore.QSize(16, 16))
        self.preview.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.preview.setFixedWidth(140)
        font = self.preview.font()
        font.setPixelSize(12)
        self.preview.setFont(font)
        self.preview.clicked.connect(self.toggle_console)
        self.window.statusBar().addPermanentWidget(self.preview, 0)
        self.build_console()
        self.application.installEventFilter(self)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.application.aboutToQuit.connect(self.stop)
        self.window.workbenchActivated.connect(self.workbench_activated)
        self.refresh()
        # FreeCAD restores saved toolbar positions after InitGui has run. Give
        # the standard commands and selector the first row, and workbench
        # commands the second, once that startup work has finished.
        QtCore.QTimer.singleShot(1000, self.place_navigation)

    def workbench_activated(self, name):
        # Native workbenches restore their own toolbar breaks when activated.
        QtCore.QTimer.singleShot(0, self.place_navigation)

    def place_navigation(self):
        bars = [bar for bar in self.window.findChildren(QtWidgets.QToolBar)
                if bar is not self.toolbar and bar.isVisible()
                and self.window.toolBarArea(bar) == QtCore.Qt.TopToolBarArea]
        bars.sort(key=lambda bar: (bar.y(), bar.x()))
        # These are FreeCAD's standard toolbars, independent of the workbench.
        standard = {"File", "Edit", "Clipboard", "Workbench", "Macro", "View",
                    "Individual Views", "Structure", "Help"}
        common = [bar for bar in bars if bar.objectName() in standard]
        specific = [bar for bar in bars if bar.objectName() not in standard]
        for bar in bars + [self.toolbar]:
            self.window.removeToolBarBreak(bar)
        self.window.addToolBar(QtCore.Qt.TopToolBarArea, self.toolbar)
        for bar in common:
            self.window.insertToolBar(self.toolbar, bar)
        for bar in specific:
            self.window.addToolBar(QtCore.Qt.TopToolBarArea, bar)
        if specific:
            self.window.insertToolBarBreak(specific[0])

    def update_palette(self):
        # A native theme's YAML controls QSS substitution, not QPalette. The
        # original Chrome set both. Respect other themes and user accent edits.
        surfaces = self.window.findChildren(QtWidgets.QToolBar) + self.window.findChildren(QtWidgets.QDockWidget)
        if App.ParamGet("User parameter:BaseApp/Preferences/MainWindow").GetString("Theme") != "Anthracite Dark":
            if self.palette_applied:
                self.application.setPalette(self.original_palette)
                self.window.setPalette(QtGui.QPalette())
                for widget in surfaces:
                    original = widget.property("AnthraciteOriginalPalette")
                    if isinstance(original, QtGui.QPalette):
                        widget.setPalette(original)
                        widget.setProperty("AnthraciteOriginalPalette", None)
                self.palette_applied = False
            return
        accent = App.ParamGet("User parameter:BaseApp/Preferences/Themes").GetUnsigned("ThemeAccentColor1", 0xA06666FF)
        accent_color = QtGui.QColor.fromRgb(accent >> 24, (accent >> 16) & 255, (accent >> 8) & 255)
        palette = self.application.palette()
        colors = {"Window": "#080808", "Base": "#080808", "AlternateBase": "#111111",
                  "Button": "#111111", "WindowText": "#E5E1DE", "Text": "#E5E1DE",
                  "ButtonText": "#E5E1DE", "HighlightedText": "#FFFFFF", "Link": "#C99696",
                  "Mid": "#292929", "ToolTipBase": "#111111", "ToolTipText": "#E5E1DE",
                  "PlaceholderText": "#96918E"}
        for role, color in colors.items():
            palette.setColor(getattr(QtGui.QPalette, role), QtGui.QColor(color))
        palette.setColor(QtGui.QPalette.Highlight, accent_color)
        for role in (QtGui.QPalette.WindowText, QtGui.QPalette.Text, QtGui.QPalette.ButtonText):
            palette.setColor(QtGui.QPalette.Disabled, role, QtGui.QColor("#827D7A"))
        current = self.application.palette()
        if (current.color(QtGui.QPalette.Window).name() != "#080808"
                or current.color(QtGui.QPalette.AlternateBase).name() != "#111111"
                or current.color(QtGui.QPalette.Highlight) != accent_color):
            self.application.setPalette(palette)
            self.window.setPalette(palette)
        # Existing native bars retain their class-specific Fusion palettes after
        # QApplication changes. Apply the same palette to those surfaces too.
        for widget in surfaces:
            if (widget.palette().color(QtGui.QPalette.Window) != palette.color(QtGui.QPalette.Window)
                    or widget.palette().color(QtGui.QPalette.Highlight) != accent_color):
                if widget.property("AnthraciteOriginalPalette") is None:
                    widget.setProperty("AnthraciteOriginalPalette", widget.palette())
                widget.setPalette(palette)
        self.palette_applied = True

    def build_console(self):
        self.dock = self.window.findChild(QtWidgets.QDockWidget, "Python console")
        report_dock = self.window.findChild(QtWidgets.QDockWidget, "Report view")
        if self.dock is None or report_dock is None:
            raise RuntimeError("FreeCAD's Python console and Report view must be enabled")
        console = self.dock.widget()
        report = report_dock.widget()
        console.setParent(None)
        report.setParent(None)
        report_dock.setWidget(QtWidgets.QWidget(report_dock))
        report_dock.hide()
        self.pages = QtWidgets.QStackedWidget(self.dock)
        self.pages.setObjectName("AnthraciteConsolePages")
        self.pages.addWidget(console)
        self.pages.addWidget(report)
        self.messages = QtWidgets.QPlainTextEdit(self.pages)
        self.messages.setObjectName("AnthraciteNotifications")
        self.messages.setReadOnly(True)
        self.messages.setPlaceholderText("No user notifications this session")
        self.messages.setFont(self.application.font())
        self.pages.addWidget(self.messages)
        panel = QtWidgets.QWidget(self.dock)
        layout = QtWidgets.QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QtWidgets.QWidget(panel)
        sidebar.setObjectName("AnthraciteConsoleNavigation")
        sidebar.setFixedWidth(150)
        navigation = QtWidgets.QVBoxLayout(sidebar)
        navigation.setContentsMargins(6, 6, 6, 6)
        navigation.setSpacing(4)
        self.navigation = QtWidgets.QButtonGroup(sidebar)
        for index, label in enumerate(("Console", "Report", "User notifications")):
            button = QtWidgets.QPushButton(sidebar)
            button.setText(label)
            button.setCheckable(True)
            button.setMinimumHeight(32)
            button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            self.navigation.addButton(button, index)
            navigation.addWidget(button)
        self.navigation.idClicked.connect(self.show_page)
        self.navigation.button(1).setChecked(True)
        self.pages.setCurrentIndex(1)
        navigation.addStretch()
        layout.addWidget(sidebar)
        layout.addWidget(self.pages, 1)
        self.dock.setWidget(panel)
        title = QtWidgets.QWidget(self.dock)
        title.setFixedHeight(0)
        self.dock.setTitleBarWidget(title)
        self.window.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.dock)
        self.dock.hide()
        self.dock.visibilityChanged.connect(self.console_visibility)
        # Native warnings still request the real Report dock. Redirect that request to its page.
        report_dock.visibilityChanged.connect(lambda visible: self.show_page(1) if visible else None)
        report_dock.visibilityChanged.connect(lambda visible: report_dock.hide() if visible else None)
        report.document().contentsChanged.connect(lambda: self.preview_message(report.document().lastBlock().text() or report.document().lastBlock().previous().text()))

    def console_visibility(self, visible):
        self.preview.setProperty("consoleOpen", visible)
        self.preview.style().unpolish(self.preview)
        self.preview.style().polish(self.preview)

    def show_page(self, index):
        self.pages.setCurrentIndex(index)
        self.navigation.button(index).setChecked(True)
        self.dock.show()
        self.dock.raise_()
        self.pages.currentWidget().setFocus()

    def toggle_console(self):
        if self.dock.isVisible():
            self.dock.hide()
        else:
            self.show_page(self.pages.currentIndex())

    def preview_message(self, message):
        text = " ".join(message.split())
        if text:
            self.preview.setToolTip(text + "\nClick to show/hide console")
            self.preview.setText(self.preview.fontMetrics().elidedText(text, QtCore.Qt.ElideRight, 100))

    def refresh(self):
        self.update_palette()
        self.pick_action.setEnabled(App.ActiveDocument is not None)
        # FreeCAD rebuilds its native selector when theme/workbench settings change.
        if self.tabs is not None and (not isValid(self.tabs) or not isValid(self.tabs.source)):
            if isValid(self.tabs):
                self.tabs.setParent(None)
                self.tabs.deleteLater()
            self.tabs = None
        toolbar = self.window.findChild(QtWidgets.QToolBar, "Workbench")
        if toolbar is not None and self.tabs is None:
            source = toolbar.findChild(QtWidgets.QTabBar) or toolbar.findChild(QtWidgets.QComboBox)
            if source is not None:
                self.tabs = CompactTabs(source, self.toolbar)
                for action in toolbar.actions():
                    if isinstance(action, QtWidgets.QWidgetAction):
                        widget = action.defaultWidget()
                        if widget is source or widget.isAncestorOf(source):
                            action.setVisible(False)
                self.toolbar.insertWidget(self.pick_action, self.tabs)
        if self.tabs is not None:
            self.tabs.sync()
            # Keep the selector and picker visible even when large native icons
            # make the standard command bars use their overflow menus.
            self.toolbar.setMinimumWidth(self.tabs.sizeHint().width() + 40)
        if self.notification_button is not None and not isValid(self.notification_button):
            self.notification_button = None
            self.seen_notifications.clear()
        if self.notification_button is None:
            for button in self.window.findChildren(QtWidgets.QPushButton, "notificationArea"):
                if button.menu() is not None and button.menu().findChild(QtWidgets.QTreeWidget) is not None:
                    self.notification_button = button
                    button.hide()
                    break
        if self.notification_button is not None:
            menu = self.notification_button.menu()
            # The native QWidgetAction caches messages until this signal. Flush its
            # model without showing the popup or replacing FreeCAD's log observer.
            if not menu.isVisible():
                menu.aboutToShow.emit()
            tree = menu.findChild(QtWidgets.QTreeWidget)
            live_items = set()
            for index in reversed(range(tree.topLevelItemCount())):
                item = tree.topLevelItem(index)
                identity = getCppPointer(item)[0]
                live_items.add(identity)
                message = item.text(2)
                # Native NotificationItem overrides data(), including UserRole;
                # keep our receipt bookkeeping outside FreeCAD's item/model.
                if self.seen_notifications.get(identity) == message:
                    continue
                self.seen_notifications[identity] = message
                icon = item.data(0, QtCore.Qt.DecorationRole)
                severity = "Notification"
                image = icon.toImage() if isinstance(icon, QtGui.QPixmap) else None
                for level, name in [("Error", "edit_Cancel"), ("Warning", "Warning"), ("Critical", "critical-info")]:
                    if image == QtGui.QIcon(f":/icons/{name}.svg").pixmap(16, 16).toImage():
                        severity = level
                        break
                entry = f"{QtCore.QTime.currentTime().toString('HH:mm:ss')} · {severity} · {item.text(1)}\n{message}\n"
                full = len(self.notifications) == self.notifications.maxlen
                self.notifications.append(entry)
                if full:
                    self.messages.setPlainText("\n".join(self.notifications))
                else:
                    self.messages.appendPlainText(entry)
                self.preview_message(message)
            self.seen_notifications = {key: value for key, value in self.seen_notifications.items() if key in live_items}
        # Touch only known status-bar fields, not editing widgets in task panels.
        for widget in self.window.statusBar().findChildren(QtWidgets.QWidget):
            if widget.inherits("Gui::DimensionWidget"):
                widget.setMinimumWidth(widget.fontMetrics().horizontalAdvance("0000.00 × 0000.00 mm") + 16)

    def eventFilter(self, watched, event):
        if self.picker.view is not None and event.type() == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_Escape:
            self.picker.cancel("Geometry pick cancelled")
            return True
        return False

    def stop(self):
        self.timer.stop()
        self.window.workbenchActivated.disconnect(self.workbench_activated)
        self.picker.cancel()
        self.application.removeEventFilter(self)


def start():
    global _ui
    if _ui is None:
        _ui = Interface()
    return _ui
