# SPDX-License-Identifier: LGPL-2.1-or-later

# Keep the native message observer alive even when upgrading from the addon
# version which disabled NotificationArea entirely. This runs before MainWindow.
import FreeCAD

if not FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Anthracite").GetBool("DefaultsApplied", False):
    from pathlib import Path

    backup = Path(FreeCAD.getUserAppDataDir()) / "Anthracite" / "preferences-before.cfg"
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        FreeCAD.ParamGet("User parameter:BaseApp").Export(str(backup))
        backup.chmod(0o600)
    notifications = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/NotificationArea")
    notifications.SetBool("NotificationAreaEnabled", True)
    notifications.SetBool("NonIntrusiveNotificationsEnabled", False)
