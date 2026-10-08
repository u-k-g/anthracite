# SPDX-License-Identifier: LGPL-2.1-or-later
"""Automatic first-run preferences, with unrelated profile data preserved."""
import os
from pathlib import Path
import time
import traceback

try:
    if os.environ.get("ANTHRACITE_SMOKE") != "1":
        raise RuntimeError("Run through just test with an isolated profile")
    import FreeCAD as App
    import FreeCADGui as Gui
    import AnthraciteBridge
    import AnthraciteUi
    from PySide6 import QtTest, QtWidgets

    application = QtWidgets.QApplication.instance()
    deadline = time.monotonic() + 10
    while AnthraciteUi._ui is None or "#080808" not in application.styleSheet().lower():
        if time.monotonic() >= deadline:
            raise AssertionError("Automatic preferences/interface did not load")
        QtTest.QTest.qWait(50)
    assert AnthraciteBridge.status()["running"]
    preferences = App.ParamGet("User parameter:BaseApp/Preferences/MainWindow")
    assert preferences.GetString("Theme") == "Anthracite Dark"
    assert preferences.GetString("StyleSheet") == "FreeCAD.qss"
    assert preferences.GetString("OverlayActiveStyleSheet") == "Anthracite Dark.qss"
    assert App.ParamGet("User parameter:BaseApp/Preferences/View").GetUnsigned("BackgroundColor") == 0x080808FF
    assert App.ParamGet("User parameter:BaseApp/Preferences/NaviCube").GetUnsigned("HiliteColor") == 0xA06666FF
    assert App.ParamGet("User parameter:BaseApp/Preferences/General").GetInt("ToolbarIconSize") == 16
    assert App.ParamGet("User parameter:BaseApp/Preferences/View").GetString("NavigationStyle") == "Gui::TouchpadNavigationStyle"
    notifications = App.ParamGet("User parameter:BaseApp/Preferences/NotificationArea")
    assert notifications.GetBool("NotificationAreaEnabled")
    assert not notifications.GetBool("NonIntrusiveNotificationsEnabled")
    assert App.ParamGet("User parameter:BaseApp/Preferences/AnthraciteTest").GetString("Sentinel") == "keep"
    assert App.ParamGet("User parameter:BaseApp/Preferences/RecentFiles").GetString("MRU0") == "keep.FCStd"
    backup = Path(App.getUserAppDataDir()) / "Anthracite" / "preferences-before.cfg"
    assert backup.exists() and "keep.FCStd" in backup.read_text()
    assert backup.stat().st_mode & 0o777 == 0o600
    App.ParamGet("User parameter:BaseApp/Preferences/General").SetInt("ToolbarIconSize", 32)
    App.saveParameter()
    os.write(1, b"ANTHRACITE_PREFERENCES_SMOKE_OK\n")
    os._exit(0)
except BaseException:
    os.write(2, traceback.format_exc().encode())
    os._exit(1)
