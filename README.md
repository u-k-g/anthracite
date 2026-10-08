# anthracite

A Python addon that lets coding agents operate stock FreeCAD through one checked CLI.
The repository root is the addon: `Init.py` and `InitGui.py` sit beside the engine modules
and executable `anthracite` client. No compilation or custom workbench is needed.

Requires **FreeCAD >= 1.0 with PySide6 (Qt 6)** and Python >= 3.11 inside FreeCAD.
The CLI uses only the Python standard library and runs outside FreeCAD with `python3`.
Your agent keeps its own authentication, configuration, models, skills, and tools.

## Install

With Nushell and just installed, run from this checkout:

```sh
just install
```

This copies the addon to `~/.local/share/FreeCAD/Mod/Anthracite/`, symlinks its CLI into
`~/.local/bin/anthracite`, and copies the agent skill into `~/.agents/skills/anthracite/`.
Put `~/.local/bin` on PATH, then restart FreeCAD. For a different FreeCAD user-data
location, use `just install '/absolute/path/to/FreeCAD/Mod/Anthracite'`. In FreeCAD's
Python console, `App.getUserAppDataDir()` identifies the user-data directory.
On macOS this may be `~/Library/Application Support/FreeCAD/v1-1/`; pass that directory's
`Mod/Anthracite` path to `just install`.

For manual installation, copy or symlink this addon folder into your FreeCAD user
`Mod/Anthracite` directory, symlink the bundled `anthracite` into `~/.local/bin/`, and
copy `.agents/skills/anthracite/SKILL.md` to `~/.agents/skills/anthracite/SKILL.md`.
Names remain Anthracite; use one installation and one running bridge at a time.
Installation is by Mod-directory copy. The bundled `package.xml` lets FreeCAD discover
the preference pack and load the bridge; no selectable Anthracite workbench is added.

## Theme and preferences

The first FreeCAD startup after installation automatically applies **Anthracite Dark**
and the bundled settings. Restart FreeCAD for native settings that require it.

This applies the bundled [preference pack](<Anthracite Dark/Anthracite Dark.cfg>):
`#080808` backgrounds, `#A06666` accents, the native overlay stylesheet, viewport and
navigation-cube colors, touchpad navigation, units, toolbar sizing, workbench choices,
and native editing/display defaults from the original Anthracite profile.
Native notification popups are disabled; native user messages appear in the addon's
Notifications panel. Developer diagnostics remain available on the Report page.
This avoids the macOS popup loop that the old fork addressed in core code.

Defaults apply once, rather than at every startup. Your later preference changes remain
in place across restarts. The original preferences are backed up to
`App.getUserAppDataDir()/Anthracite/preferences-before.cfg` before applying defaults.
You can select the pack again through FreeCAD's native Theme preferences to reset them.
Saved window geometry, recent-item history, machine-specific
fonts, external theme references, and fork initialization flags are excluded.
The theme YAML and overlay stylesheet retain their original LGPL notices.

## Use

Startup prints the bridge host and port in FreeCAD's report view. Open or create a document,
then run:

```sh
anthracite status
anthracite exec 'doc.addObject("Part::Box", "Box")'
anthracite log --limit 20
anthracite shots
```

An edit executes on FreeCAD's GUI thread as one named native transaction, recomputes,
validates, and commits or rolls back. The CLI returns JSON, including `ok`, revision tokens,
diagnostics, and viewport PNG paths. After a timeout or lost connection, inspect the
document before deciding what to do next; execution may still have committed.

Preserve editable feature trees with sketches and native PartDesign features.
`cad.verify([...])` checks explicit requirements after recompute; expected values must
come from the requirement. Use `cad.compare('reference.step')` for geometry and
`cad.editability()` for the tree. A high geometry score with low editability is a failure.
The [agent skill](.agents/skills/anthracite/SKILL.md) covers inspection and editing discipline.
The skill is preserved unchanged; its former `just connect` instructions are superseded
by this addon's installation instructions.

The **Anthracite** global toolbar keeps the eyedropper available across workbenches.
Click it or press **Ctrl+Shift+E**, then click geometry to copy a JSON reference and pick
point to the clipboard. **Esc** cancels. The reference carries the document token and
revision, so later geometry edits invalidate it. Native selection and `cad.selection()`
remain available independently.

Click the latest-message preview in the status bar to show or hide the bottom console
dock. Its compact navigation switches between FreeCAD's real **Console**, **Report**,
and **Notifications**. Notification rows show the time received by the panel, severity,
source, and selectable message text; **Copy selected** copies complete rows. The panel
retains up to 1,000 messages for the current session.

Workbench tabs keep the active label visible, collapse inactive labels, and reveal them
on hover. Qt styling supplies compact toolbar spacing, status-bar styling, dimension
display widths, and selected-tab accents. These additions use Python and Qt with stock
FreeCAD, independently of bridge startup.

## Local data

`$XDG_STATE_HOME/.anthracite` (default `~/.local/state/.anthracite`) contains `bridge.json`,
`operations.ndjson`, and viewport shots. Checkpoints live in `$XDG_STATE_HOME/anthracite/checkpoints`.
Session identity stays outside `.FCStd` documents. Operation history is for inspection,
not automatic recovery.

The bridge binds loopback and stores a plaintext authentication token in a mode-0600
discovery file. A local process that can read it can execute arbitrary Python inside
FreeCAD. Consider this access when using a shared machine.

## Test

```sh
just test
```

Tests copy the addon into a temporary FreeCAD user profile, check automatic defaults,
verify persistence and user overrides across restart, exercise real console widgets,
native notifications, workbench tabs and geometry picking, check startup autoload,
run the unchanged native executor tests, then exercise the real bridge and CLI transport,
including committed edits and viewport files. No model calls or FreeCAD rebuilds are used.
Set `FREECAD_BIN` to your stock GUI executable if needed. On macOS the default is
`/Applications/FreeCAD.app/Contents/Resources/bin/freecad`; on Linux it is `FreeCAD` on PATH.
Failed runs retain their profile and logs in `test-results/`.

## License

[LGPL-2.1-or-later](LICENSE), as in the original Anthracite sources. Existing source
attribution and license notices are preserved.
