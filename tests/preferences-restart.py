# SPDX-License-Identifier: LGPL-2.1-or-later
"""Selected theme survives restart without reapplying its defaults."""
import os
import time
import traceback

try:
    if os.environ.get("ANTHRACITE_SMOKE") != "1":
        raise RuntimeError("Run through just test with an isolated profile")
    import FreeCAD as App
    import AnthraciteBridge
    from PySide6 import QtTest, QtWidgets

    application = QtWidgets.QApplication.instance()
    deadline = time.monotonic() + 5
    while "#080808" not in application.styleSheet().lower():
        if time.monotonic() >= deadline:
            raise AssertionError("Selected theme did not load on restart")
        QtTest.QTest.qWait(50)
    assert App.ParamGet("User parameter:BaseApp/Preferences/MainWindow").GetString("Theme") == "Anthracite Dark"
    assert App.ParamGet("User parameter:BaseApp/Preferences/General").GetInt("ToolbarIconSize") == 32
    assert App.ParamGet("User parameter:BaseApp/Preferences/AnthraciteTest").GetString("Sentinel") == "keep"
    assert AnthraciteBridge.status()["running"], "Theme metadata broke bridge autoload"
    os.write(1, b"ANTHRACITE_PREFERENCES_RESTART_OK\n")
    os._exit(0)
except BaseException:
    traceback.print_exc()
    os._exit(1)
