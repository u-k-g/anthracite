# SPDX-License-Identifier: LGPL-2.1-or-later
"""Small Qt additions around stock FreeCAD; the bridge does not depend on these."""
from pathlib import Path
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
        self.hovered = -1
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
            self.hovered = -1
            while self.count():
                self.removeTab(0)
            for label, icon in labels:
                index = self.addTab(icon, label)
                self.setTabData(index, label)
                self.setTabToolTip(index, label)
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
            expanded = index in (self.currentIndex(), self.hovered) or self.tabIcon(index).isNull()
            self.setTabText(index, self.tabData(index) if expanded else "")
        self.updateGeometry()

    def mouseMoveEvent(self, event):
        index = self.tabAt(event.position().toPoint())
        if index != self.hovered:
            self.hovered = index
            self.update_labels()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hovered = -1
        self.update_labels()
        super().leaveEvent(event)


class Eyedropper:
    def __init__(self, ui):
        self.ui = ui
        self.view = None
        self.callback = None
        self.document = None

    def GetResources(self):
        return {"MenuText": "Pick geometry reference", "ToolTip": "Copy a checked geometry reference (Esc cancels)",
                "Pixmap": str(Path(__file__).parent / "Resources" / "eyedropper.svg")}

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
        self.application = QtWidgets.QApplication.instance()
        stylesheet = (Path(__file__).parent / "Resources" / "ui.qss").read_text()
        self.window.setStyleSheet(self.window.styleSheet() + "\n" + stylesheet)
        self.picker = Eyedropper(self)
        Gui.addCommand("Anthracite_PickGeometry", self.picker)
        self.toolbar = QtWidgets.QToolBar("Anthracite", self.window)
        self.toolbar.setObjectName("AnthraciteTools")
        self.toolbar.setIconSize(QtCore.QSize(16, 16))
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
        self.preview = QtWidgets.QPushButton("Console", self.window.statusBar())
        self.preview.setObjectName("AnthraciteConsolePreview")
        self.preview.setFlat(True)
        self.preview.setMaximumWidth(540)
        self.preview.setMinimumWidth(160)
        self.preview.clicked.connect(self.toggle_console)
        self.window.statusBar().addPermanentWidget(self.preview, 1)
        self.build_console()
        self.application.installEventFilter(self)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.application.aboutToQuit.connect(self.stop)
        self.refresh()

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
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        self.messages = QtWidgets.QTreeWidget(panel)
        self.messages.setObjectName("AnthraciteNotifications")
        self.messages.setHeaderLabels(["Time", "Severity", "Source", "Message"])
        self.messages.setRootIsDecorated(False)
        self.messages.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.messages.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.messages.setColumnWidth(0, 85)
        self.messages.setColumnWidth(1, 110)
        self.messages.setColumnWidth(2, 130)
        layout.addWidget(self.messages)
        tools = QtWidgets.QHBoxLayout()
        tools.addStretch()
        for label, callback in [("Copy selected", self.copy_messages), ("Clear", self.messages.clear)]:
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            tools.addWidget(button)
        layout.addLayout(tools)
        self.pages.addWidget(panel)
        self.dock.setWidget(self.pages)
        title = QtWidgets.QWidget(self.dock)
        title.setObjectName("AnthraciteConsoleNavigation")
        navigation = QtWidgets.QHBoxLayout(title)
        navigation.setContentsMargins(4, 2, 4, 2)
        navigation.setSpacing(4)
        self.navigation = QtWidgets.QButtonGroup(title)
        for index, label in enumerate(("Console", "Report", "Notifications")):
            button = QtWidgets.QToolButton(title)
            button.setText(label)
            button.setCheckable(True)
            self.navigation.addButton(button, index)
            navigation.addWidget(button)
        self.navigation.idClicked.connect(self.show_page)
        self.navigation.button(0).setChecked(True)
        navigation.addStretch()
        close = QtWidgets.QToolButton(title)
        close.setText("×")
        close.setToolTip("Hide console")
        close.clicked.connect(self.dock.hide)
        navigation.addWidget(close)
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
            self.preview.setText(self.preview.fontMetrics().elidedText(text, QtCore.Qt.ElideRight, 510))

    def copy_messages(self):
        text = "\n".join("\t".join(item.text(column) for column in range(4))
                         for item in self.messages.selectedItems())
        QtWidgets.QApplication.clipboard().setText(text)

    def refresh(self):
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
                severity = "Info"
                image = icon.toImage() if isinstance(icon, QtGui.QPixmap) else None
                for level, name in [("Error", "edit_Cancel"), ("Warning", "Warning"), ("Critical", "critical-info")]:
                    if image == QtGui.QIcon(f":/icons/{name}.svg").pixmap(16, 16).toImage():
                        severity = level
                        break
                row = QtWidgets.QTreeWidgetItem([QtCore.QTime.currentTime().toString("HH:mm:ss"), severity, item.text(1), message])
                row.setToolTip(3, message)
                if isinstance(icon, QtGui.QPixmap):
                    row.setIcon(1, QtGui.QIcon(icon))
                self.messages.addTopLevelItem(row)
                self.preview_message(message)
                while self.messages.topLevelItemCount() > 1000:
                    self.messages.takeTopLevelItem(0)
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
        self.picker.cancel()
        self.application.removeEventFilter(self)


def start():
    global _ui
    if _ui is None:
        _ui = Interface()
    return _ui
