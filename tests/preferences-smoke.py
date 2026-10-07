# SPDX-License-Identifier: LGPL-2.1-or-later
"""Apply the bundled pack through stock FreeCAD's native preferences dialog."""
import os
import time
import traceback

try:
    if os.environ.get("ANTHRACITE_SMOKE") != "1":
        raise RuntimeError("Run through just test with an isolated profile")
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide6 import QtCore, QtTest, QtWidgets

    window_preferences = App.ParamGet("User parameter:BaseApp/Preferences/MainWindow")
    assert window_preferences.GetString("Theme") != "Anthracite Dark", "Startup applied the pack without selection"
    # Applying the pack must preserve unrelated preferences and recent history.
    App.ParamGet("User parameter:BaseApp/Preferences/AnthraciteTest").SetString("Sentinel", "keep")
    App.ParamGet("User parameter:BaseApp/Preferences/RecentFiles").SetString("MRU0", "keep.FCStd")

    def apply_in_dialog():
        try:
            main_window = Gui.getMainWindow()
            combo = main_window.findChild(QtWidgets.QComboBox, "themesCombobox")
            assert combo is not None, "Native theme selector was not found"
            index = combo.findText("Anthracite Dark")
            assert index >= 0, "FreeCAD did not discover the bundled preference pack"
            combo.setCurrentIndex(index)
            combo.activated.emit(index)
            dialog = combo.window()
            buttons = dialog.findChild(QtWidgets.QDialogButtonBox, "buttonBox")
            assert buttons is not None
            buttons.button(QtWidgets.QDialogButtonBox.Apply).click()
            application = QtWidgets.QApplication.instance()
            # Native style-parameter handlers reload the stylesheet asynchronously.
            QtTest.QTest.qWait(300)
            assert window_preferences.GetString("Theme") == "Anthracite Dark"
            assert window_preferences.GetString("StyleSheet") == "FreeCAD.qss"
            assert window_preferences.GetString("OverlayActiveStyleSheet") == "Anthracite Dark.qss"
            assert App.ParamGet("User parameter:BaseApp/Preferences/View").GetUnsigned("BackgroundColor") == 0x080808FF
            assert App.ParamGet("User parameter:BaseApp/Preferences/NaviCube").GetUnsigned("HiliteColor") == 0xA06666FF
            assert App.ParamGet("User parameter:BaseApp/Preferences/General").GetInt("ToolbarIconSize") == 16
            assert App.ParamGet("User parameter:BaseApp/Preferences/View").GetString("NavigationStyle") == "Gui::TouchpadNavigationStyle"
            assert not App.ParamGet("User parameter:BaseApp/Preferences/NotificationArea").GetBool("NotificationAreaEnabled")
            assert App.ParamGet("User parameter:BaseApp/Preferences/AnthraciteTest").GetString("Sentinel") == "keep"
            assert App.ParamGet("User parameter:BaseApp/Preferences/RecentFiles").GetString("MRU0") == "keep.FCStd"
            # Verify the theme resource participates in native stylesheet expansion.
            theme = QtCore.QFile("qss:parameters/Anthracite Dark.yaml")
            assert theme.exists(), "Theme parameters are not on FreeCAD's search path"
            overlay = QtCore.QFile("overlay:Anthracite Dark.qss")
            assert overlay.exists(), "Overlay stylesheet is not on FreeCAD's search path"
            deadline = time.monotonic() + 5
            while True:
                sheet = (application.styleSheet() + main_window.styleSheet()).lower()
                normalized = "".join(sheet.split())
                if "#080808" in normalized or "rgb(8,8,8)" in normalized:
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError("Native stylesheet did not load our colors")
                QtTest.QTest.qWait(50)
            assert ("#080808" in normalized or "rgb(8,8,8)" in normalized)
            assert ("#a06666" in normalized or "rgb(160,102,102)" in normalized)
            dialog.hide()
            document = App.newDocument("AnthraciteThemePreview")
            document.addObject("Part::Box", "Box")
            document.recompute()
            Gui.activeDocument().activeView().viewAxonometric()
            Gui.activeDocument().activeView().fitAll()
            Gui.updateGui()
            QtTest.QTest.qWait(300)
            shot = os.path.join(os.path.dirname(os.path.dirname(__file__)), "test-results", "theme.png")
            assert main_window.grab().save(shot), "Could not save the theme preview"
            App.closeDocument(document.Name)
            # A subsequent launch must keep this user change, not reapply defaults.
            App.ParamGet("User parameter:BaseApp/Preferences/General").SetInt("ToolbarIconSize", 32)
            App.saveParameter()
            os.write(1, b"ANTHRACITE_PREFERENCES_SMOKE_OK\n")
            os._exit(0)
        except BaseException:
            traceback.print_exc()
            os._exit(1)

    QtCore.QTimer.singleShot(0, apply_in_dialog)
    Gui.runCommand("Std_DlgPreferences")
except BaseException:
    traceback.print_exc()
    os._exit(1)
