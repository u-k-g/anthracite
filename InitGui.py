# SPDX-License-Identifier: LGPL-2.1-or-later


def _start_bridge():
    # FreeCAD executes InitGui.py with separate globals and locals. Keep imports
    # inside the deferred callback so they remain available when it runs.
    import FreeCAD

    try:
        import AnthraciteBridge

        AnthraciteBridge.start()
        status = AnthraciteBridge.status()
        FreeCAD.Console.PrintMessage(
            f"Anthracite bridge on {status['host']}:{status['port']}\n"
        )
    except Exception as error:  # Never block FreeCAD startup.
        FreeCAD.Console.PrintWarning(f"Anthracite bridge did not start: {error}\n")

    try:
        import AnthraciteUi

        AnthraciteUi.apply_defaults()
        AnthraciteUi.start()
    except Exception as error:  # Appearance must not prevent the bridge from running.
        FreeCAD.Console.PrintWarning(f"Anthracite interface did not start: {error}\n")


from PySide6 import QtCore

QtCore.QTimer.singleShot(0, _start_bridge)
