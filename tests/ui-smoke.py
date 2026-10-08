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
    tabs = ui.tabs
    assert tabs.tabText(tabs.currentIndex()), "Selected workbench label is missing"
    inactive = next(i for i in range(tabs.count()) if i != tabs.currentIndex() and not tabs.tabIcon(i).isNull())
    assert not tabs.tabText(inactive), "Inactive labels were not collapsed"
    point = QtCore.QPointF(tabs.tabRect(inactive).center())
    QtWidgets.QApplication.sendEvent(tabs, QtGui.QMouseEvent(
        QtCore.QEvent.MouseMove, point, tabs.mapToGlobal(point.toPoint()),
        QtCore.Qt.NoButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier))
    assert tabs.tabText(inactive) == tabs.tabData(inactive), "Hover did not reveal the label"
    QtWidgets.QApplication.sendEvent(tabs, QtCore.QEvent(QtCore.QEvent.Leave))
    assert not tabs.tabText(inactive)
    QtTest.QTest.mouseClick(tabs, QtCore.Qt.LeftButton, pos=tabs.tabRect(inactive).center())
    assert tabs.currentIndex() == inactive and tabs.source.currentIndex() == inactive
    Gui.activateWorkbench("PartWorkbench")
    QtTest.QTest.qWait(300)

    App.Console.PrintMessage("Latest console preview test\n")
    wait_until(lambda: "Latest console preview test" in ui.preview.text(), "Preview did not follow native report output")
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
    App.Console.PrintNotification("UiTestSource", "Native user notice")
    App.Console.PrintUserWarning("UiTestSource", "Native user warning")
    App.Console.PrintUserError("UiTestSource", "Native user error")
    wait_until(lambda: {ui.messages.topLevelItem(i).text(3) for i in range(ui.messages.topLevelItemCount())}
               >= {"Native user notice", "Native user warning", "Native user error"},
               "Native user notifications were not captured")
    rows = [ui.messages.topLevelItem(i) for i in range(ui.messages.topLevelItemCount())]
    for message, severity in [("Native user notice", "Info"), ("Native user warning", "Warning"), ("Native user error", "Error")]:
        row = next(row for row in rows if row.text(3) == message)
        assert row.text(1) == severity, (message, row.text(1))
        assert row.text(2) == "UiTestSource"
        assert QtCore.QTime.fromString(row.text(0), "HH:mm:ss").isValid()
        row.setSelected(True)
    ui.copy_messages()
    assert "UiTestSource" in QtWidgets.QApplication.clipboard().text()
    assert "Native user warning" in QtWidgets.QApplication.clipboard().text()
    count = ui.messages.topLevelItemCount()
    QtTest.QTest.qWait(600)
    assert ui.messages.topLevelItemCount() == count, "Polling duplicated messages"
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
