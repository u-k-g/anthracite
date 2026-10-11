# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real stock FreeCAD widgets, native notices, console, and geometry picking."""
import json
import os
from pathlib import Path
import time
import traceback

try:
    if os.environ.get("ANTHRACITE_SMOKE") != "1":
        raise RuntimeError("Run through just test with an isolated profile")
    import FreeCAD as App
    import FreeCADGui as Gui
    import AnthraciteUi
    import AnthraciteExecutor as Executor
    from PySide6 import QtCore, QtGui, QtTest, QtWidgets

    def wait_until(predicate, message):
        deadline = time.monotonic() + 10
        while not predicate():
            if time.monotonic() >= deadline:
                raise AssertionError(message)
            QtTest.QTest.qWait(50)

    wait_until(lambda: AnthraciteUi._ui is not None, "Interface did not autoload")
    ui = AnthraciteUi._ui
    wait_until(lambda: ui.tabs is not None, "Compact workbench tabs were not installed")
    # FreeCAD's Start page is opened by a later startup timer. Finish startup
    # before creating the document, as a user opening a model would do.
    QtTest.QTest.qWait(1500)
    palette = QtWidgets.QApplication.instance().palette()
    assert palette.color(QtGui.QPalette.Window).name() == "#080808"
    assert palette.color(QtGui.QPalette.AlternateBase).name() == "#111111"
    assert palette.color(QtGui.QPalette.Highlight).name() == "#a06666"
    document = App.newDocument("AnthraciteUiTest")
    box = document.addObject("Part::Box", "Box")
    document.recompute()
    for workbench in ("PartDesignWorkbench", "PartWorkbench", "SketcherWorkbench"):
        Gui.activateWorkbench(workbench)
        QtTest.QTest.qWait(300)
        App.setActiveDocument(document.Name)
        ui.refresh()
        assert ui.toolbar.isVisible(), "Global eyedropper disappeared with a workbench change"
        assert ui.pick_action.isEnabled()
        assert ui.tabs.isVisible(), "Workbench selector was hidden in toolbar overflow"
        assert ui.tabs.mapTo(ui.window, QtCore.QPoint()).x() > ui.window.width() * 0.4, "Workbench selector is not on the right"
        assert not ui.toolbar.isMovable(), "Global navigation has a drag handle"
        top_bars = [bar for bar in ui.window.findChildren(QtWidgets.QToolBar)
                    if bar is not ui.toolbar and bar.isVisible()
                    and ui.window.toolBarArea(bar) == QtCore.Qt.TopToolBarArea]
        standard = {"File", "Edit", "Clipboard", "Workbench", "Macro", "View",
                    "Individual Views", "Structure", "Help"}
        common = [bar for bar in top_bars if bar.objectName() in standard]
        specific = [bar for bar in top_bars if bar.objectName() not in standard]
        assert common and specific, "Missing standard or workbench toolbars"
        assert all(bar.y() == ui.toolbar.y() and bar.x() < ui.toolbar.x() for bar in common), "Standard tools are not on the top left beside the selector"
        assert all(bar.y() >= ui.toolbar.y() + ui.toolbar.height() for bar in specific), "Workbench tools are not below the standard tools"
        assert sum(ui.window.toolBarBreak(bar) for bar in top_bars) == 1, "Command rows have extra breaks"
        assert all(bar.palette().color(QtGui.QPalette.Window).name() == "#080808" for bar in top_bars), "Native toolbars retained their gray Fusion palettes"
        # QSS can keep an old gray brush even after QPalette reports the right
        # color. Check the rendered surface, not just the preference/palette.
        image = top_bars[0].grab().toImage()
        assert image.pixelColor(image.width() // 2, 0).name() == "#080808", "Toolbar QSS cached a gray background"
    tabs = ui.tabs
    assert tabs.tabText(tabs.currentIndex()), "Selected workbench label is missing"
    inactive = next(i for i in range(tabs.count()) if i != tabs.currentIndex() and not tabs.tabIcon(i).isNull())
    assert not tabs.tabText(inactive), "Inactive labels were not collapsed"
    QtTest.QTest.qWait(100)
    width = tabs.tabRect(inactive).width()
    point = QtCore.QPointF(tabs.tabRect(inactive).center())
    QtWidgets.QApplication.sendEvent(tabs, QtGui.QMouseEvent(
        QtCore.QEvent.MouseMove, point, tabs.mapToGlobal(point.toPoint()),
        QtCore.Qt.NoButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier))
    assert not tabs.tabText(inactive), "Hover revealed an inactive workbench name"
    assert not tabs.tabToolTip(inactive), "Hover shows an inactive workbench tooltip"
    assert tabs.tabRect(inactive).width() == width, "Hover resized an inactive tab"
    QtWidgets.QApplication.sendEvent(tabs, QtCore.QEvent(QtCore.QEvent.Leave))
    assert not tabs.tabText(inactive)
    QtTest.QTest.mouseClick(tabs, QtCore.Qt.LeftButton, pos=tabs.tabRect(inactive).center())
    assert tabs.currentIndex() == inactive and tabs.source.currentIndex() == inactive
    assert tabs.tabText(inactive) == tabs.tabData(inactive), "Active workbench name is missing"
    Gui.activateWorkbench("PartWorkbench")
    QtTest.QTest.qWait(300)

    App.Console.PrintMessage("Latest console preview test\n")
    wait_until(lambda: "Latest console preview test" in ui.preview.toolTip(), "Preview did not follow native report output")
    assert ui.preview.width() == 140, "Console preview expanded across the status bar"
    ui.dock.hide()
    QtTest.QTest.mouseClick(ui.preview, QtCore.Qt.LeftButton)
    assert ui.dock.isVisible()
    assert ui.window.dockWidgetArea(ui.dock) == QtCore.Qt.BottomDockWidgetArea
    ui.show_page(0)
    console = ui.pages.widget(0)
    QtTest.QTest.keyClicks(console, "App.AnthraciteConsoleTest = 731")
    QtTest.QTest.keyClick(console, QtCore.Qt.Key_Return)
    assert App.AnthraciteConsoleTest == 731, "The dock does not contain the real Python console"
    del App.AnthraciteConsoleTest
    ui.show_page(2)
    assert ui.pages.currentIndex() == 2
    assert ui.dock.titleBarWidget().height() == 0
    sidebar = ui.dock.findChild(QtWidgets.QWidget, "AnthraciteConsoleNavigation")
    assert sidebar.width() == 150 and sidebar.x() < ui.pages.x(), "Console navigation is not a compact side column"
    assert ui.dock.palette().color(QtGui.QPalette.Window).name() == "#080808"
    App.Console.PrintNotification("UiTestSource", "Native user notice")
    App.Console.PrintUserWarning("UiTestSource", "Native user warning")
    App.Console.PrintUserError("UiTestSource", "Native user error")
    wait_until(lambda: all(message in ui.messages.toPlainText() for message in
                          ("Native user notice", "Native user warning", "Native user error")),
               "Native user notifications were not captured")
    lines = ui.messages.toPlainText().splitlines()
    for message, severity in [("Native user notice", "Notification"), ("Native user warning", "Warning"), ("Native user error", "Error")]:
        caption = lines[lines.index(message) - 1].split(" · ")
        assert caption[1:] == [severity, "UiTestSource"]
        assert QtCore.QTime.fromString(caption[0], "HH:mm:ss").isValid()
    ui.messages.selectAll()
    QtTest.QTest.keyClick(ui.messages, QtCore.Qt.Key_C, QtCore.Qt.ControlModifier)
    assert "UiTestSource" in QtWidgets.QApplication.clipboard().text()
    assert "Native user warning" in QtWidgets.QApplication.clipboard().text()
    count = len(ui.notifications)
    QtTest.QTest.qWait(600)
    assert len(ui.notifications) == count, "Polling duplicated messages"
    QtTest.QTest.mouseClick(ui.preview, QtCore.Qt.LeftButton)
    assert not ui.dock.isVisible()

    view = Gui.activeDocument().activeView()
    view.viewTop()
    view.fitAll()
    QtTest.QTest.qWait(400)
    # The real Coin event path, without relying on screen coordinates of the OS window.
    from pivy import coin
    Gui.runCommand("Anthracite_PickGeometry")
    assert ui.picker.callback is not None
    position = view.getPointOnScreen(App.Vector(5, 5, 10))
    event = coin.SoMouseButtonEvent()
    event.setButton(coin.SoMouseButtonEvent.BUTTON1)
    event.setState(coin.SoButtonEvent.DOWN)
    event.setPosition(coin.SbVec2s(int(position[0]), int(position[1])))
    action = coin.SoHandleEventAction(coin.SbViewportRegion(view.getSize()[0], view.getSize()[1]))
    action.setEvent(event)
    action.apply(view.getSceneGraph())
    assert ui.picker.callback is None, "Viewer event did not complete the geometry pick"
    picked = json.loads(QtWidgets.QApplication.clipboard().text())
    assert picked["reference"]["object"] == "Box"
    assert picked["reference"]["subelement"].startswith("Face")
    assert Executor.validate_picks_json(json.dumps([picked])) == "ok"
    assert not Gui.Selection.getSelection(), "Picking altered native selection"
    box.Length = 20
    document.recompute()
    try:
        Executor.validate_picks_json(json.dumps([picked]))
    except ValueError:
        pass
    else:
        raise AssertionError("An intervening edit did not invalidate the copied reference")
    Gui.runCommand("Anthracite_PickGeometry")
    QtTest.QTest.keyClick(ui.window, QtCore.Qt.Key_Escape)
    assert ui.picker.callback is None and QtWidgets.QApplication.overrideCursor() is None
    Gui.runCommand("Anthracite_PickGeometry")
    App.closeDocument(document.Name)
    assert ui.picker.callback is None, "Closing the document left picking active"
    assert QtWidgets.QApplication.overrideCursor() is None
    ui.show_page(2)
    cursor = ui.messages.textCursor()
    cursor.clearSelection()
    ui.messages.setTextCursor(cursor)
    screenshot = Path(__file__).parents[1] / "test-results" / "interface.png"
    assert ui.window.grab().save(str(screenshot))
    os.write(1, b"ANTHRACITE_UI_SMOKE_OK\n")
    os._exit(0)
except BaseException:
    if "ui" in globals():
        ui.window.grab().save(str(Path(__file__).parents[1] / "test-results" / "interface-failure.png"))
        os.write(2, f"Preview: {ui.preview.toolTip()}\n".encode())
    os.write(2, traceback.format_exc().encode())
    os._exit(1)
