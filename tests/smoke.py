# SPDX-License-Identifier: LGPL-2.1-or-later
"""Addon autoload and native executor assertions in stock FreeCAD's GUI."""
import os
import traceback
import unittest

try:
    if os.environ.get("ANTHRACITE_SMOKE") != "1":
        raise RuntimeError("Run this test through just test with an isolated profile")
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide6 import QtWidgets
    import AnthraciteBridge
    import TestAnthracite

    if tuple(int(part) for part in App.Version()[:2]) < (1, 0):
        raise RuntimeError("Anthracite requires FreeCAD 1.0 or newer")
    installed = os.path.realpath(os.environ["ANTHRACITE_TEST_ADDON"])
    assert os.path.dirname(os.path.realpath(AnthraciteBridge.__file__)) == installed
    # Let InitGui's deferred callback run; do not start the bridge ourselves.
    application = QtWidgets.QApplication.instance()
    application.processEvents()
    assert AnthraciteBridge.status()["running"], "InitGui did not start the bridge"
    assert AnthraciteBridge.status()["port"] > 0

    probe = App.newDocument("AnthracitePortabilityProbe")
    print("FreeCAD:", App.Version(), "HasPendingTransaction:",
          getattr(probe, "HasPendingTransaction", "absent"), flush=True)
    App.closeDocument(probe.Name)
    for workbench in ("PartDesignWorkbench", "PartWorkbench", "SketcherWorkbench"):
        assert workbench in Gui.listWorkbenches(), workbench
        Gui.activateWorkbench(workbench)
    Gui.activateWorkbench("PartDesignWorkbench")

    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(TestAnthracite)
    )
    if not result.wasSuccessful():
        raise RuntimeError("Executor tests failed")
    AnthraciteBridge.stop()
    os.write(1, b"ANTHRACITE_GUI_SMOKE_OK\n")
    os._exit(0)
except BaseException:
    traceback.print_exc()
    os._exit(1)
