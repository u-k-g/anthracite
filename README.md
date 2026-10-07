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
the optional preference pack and load the bridge; no selectable Anthracite workbench is added.

## Theme and preferences

After installing, choose **Edit → Preferences → General → Theme → Anthracite Dark**,
then click **Apply**. Restart FreeCAD for settings that require it.

This applies the bundled [preference pack](<Anthracite Dark/Anthracite Dark.cfg>):
`#080808` backgrounds, `#A06666` accents, the native overlay stylesheet, viewport and
navigation-cube colors, touchpad navigation, units, toolbar sizing, workbench choices,
and native editing/display defaults from the original Anthracite profile.
Native notification popups are disabled by the pack; diagnostics remain in the report
view, avoiding the macOS popup loop that the old fork addressed in core code.
Selecting the pack applies those preferences to the current FreeCAD user profile.
FreeCAD provides preference-pack backups through its native preferences dialog.

Installation and ordinary startup do not apply the pack. Your later preference changes
remain in place across restarts. Saved window geometry, recent-item history, machine-specific
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
The skill is preserved unchanged; its former `just connect` and status-bar picker notes
are superseded by this addon's installation instructions and native selection workflow.

Stock FreeCAD owns its preferences and UI. The former custom selector, notification fix,
status bar, themed panels, and Ctrl+Shift+E picker are removed. Use native selection with
`cad.selection()`; Python reference helpers remain available. Portable preferences and
theme resources are provided by the optional native preference pack instead of branding.

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

Tests copy the addon into a temporary FreeCAD user profile, apply the pack through the
native theme selector, verify persistence and user overrides across restart, check startup autoload,
run the unchanged native executor tests, then exercise the real bridge and CLI transport,
including committed edits and viewport files. No model calls or FreeCAD rebuilds are used.
Set `FREECAD_BIN` to your stock GUI executable if needed. On macOS the default is
`/Applications/FreeCAD.app/Contents/Resources/bin/freecad`; on Linux it is `FreeCAD` on PATH.
Failed runs retain their profile and logs in `test-results/`.

## License

[LGPL-2.1-or-later](LICENSE), as in the original Anthracite sources. Existing source
attribution and license notices are preserved.
