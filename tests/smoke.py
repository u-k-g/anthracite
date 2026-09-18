# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD-embedded assertions, launched by tests/runtests.nu."""
import os
import traceback
import unittest

try:
    if os.environ.get("ANTHRACITE_SMOKE") != "1":
        raise RuntimeError("Run this test through just test with an isolated profile")
    import FreeCAD as App
    import FreeCADGui as Gui

    # Native notification widgets can re-enter Qt logging/accessibility on
    # macOS. Keep diagnostics in the report/log, not transient test widgets.
    from PySide6 import QtCore, QtTest, QtWidgets
    QtTest.QTest.qWait(100)
    notifications = Gui.getMainWindow().findChild(QtWidgets.QWidget, 'notificationArea')
    assert notifications is not None and notifications.isHidden(), 'Hide only the notification indicator'
    App.ParamGet("User parameter:BaseApp/Preferences/NotificationArea").SetBool(
        "NotificationAreaEnabled", False)
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    for workbench in ("PartDesignWorkbench", "PartWorkbench", "SketcherWorkbench"):
        if workbench not in Gui.listWorkbenches():
            raise RuntimeError(f"Missing editing workbench: {workbench}")
        Gui.activateWorkbench(workbench)
    Gui.activateWorkbench("PartDesignWorkbench")
    import TestAnthracite
    print("Testing installed executor:", TestAnthracite.__file__, flush=True)

    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(TestAnthracite)
    )
    if not result.wasSuccessful():
        raise RuntimeError("Executor tests failed")
    # This module checks docking, reload, invalid-QML fallback, selection and
    # real viewport rendering, then schedules a normal QApplication shutdown.
    import TestAnthraciteGui
except BaseException:
    traceback.print_exc()
    # FreeCAD otherwise catches script errors and leaves the application open.
    os._exit(1)
