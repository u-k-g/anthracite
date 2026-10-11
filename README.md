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

The installer finds FreeCAD, checks that it is version 1.0 or newer, and asks it for
its user-data directory without opening the GUI. It copies the addon into that
directory's `Mod/Anthracite/`, symlinks its CLI into `~/.local/bin/anthracite`, and copies
the agent skill into `~/.agents/skills/anthracite/`. This also selects the correct
version-specific directory on macOS automatically.

Put `~/.local/bin` on PATH, then restart FreeCAD. On macOS the installer finds
`/Applications/FreeCAD.app` or `~/Applications/FreeCAD.app`; elsewhere it finds FreeCAD
on PATH. For a different installation or multiple installed versions, select the
command-line executable with `FREECAD_CMD`. In zsh:

```sh
FREECAD_CMD='/path/to/freecadcmd' just install
```

An explicit destination remains available: `just install '/absolute/path/to/Mod/Anthracite'`.
This bypasses detection. In Nushell, `$HOME` inside a quoted string is literal text;
use a `~/` path or construct the path with `($env.HOME | path join ...)`.

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

### Derived feature IR

After a modeling call has recomputed and committed, inspect its canonical feature
definition through the same CLI:

```sh
anthracite exec 'cad.ir()'
anthracite exec 'cad.ir("Cut")'
anthracite exec 'cad.model_hash()'
```

The result contains a versioned `definition`, its UTF-8 JSON `canonical` serialization,
and a SHA-256 `definitionHash`. Compilation reads the live native document, including
unsaved edits; it does not edit, save, or recompute it. FreeCAD remains the source of
truth and evaluator. Delete this projection and regenerate it whenever needed.

IR v1 projects native App-level properties, including Part Design bodies, sketches,
constraints, expressions, attachments, and feature links. It never reads ViewObject.
Each node contains its native type and typed inputs; links address the SHA-256 content
hash of the child node. Expression references use those hashes too. The document
projection sorts roots and tracks instance counts, so duplicate native features are
not silently lost when equal node content is shared. Source names appear in a separate
lookup map outside the hash. Document/object names, creation order, labels, visibility,
and cached computed shapes do not determine identity.

Quantities use mm/kg/s/A/K/mol/cd/rad bases, quaternion signs are normalized, negative
zero becomes zero, and JSON keys are sorted. Scalar inputs retain their finite
round-trip precision. Quaternion components use **14 decimal places**, declared in
the schema, to absorb FreeCAD's one-ulp rotation normalization on file restore. This
is a quantized rotation identity, not exact floating-point identity. The authoritative
attachment engine type is retained; its derived UI selector is excluded. Expression whitespace
is normalized with token boundaries and string literals preserved; algebraic
equivalence is not inferred. Complex native inputs use normalized persistence XML and
binary-content hashes. Imported/baked Part features retain a BRep-content hash, even
when Shape is marked as an output. Sketch constraint label positions and visibility
are excluded. Editor ReadOnly/runtime Output flags cannot hide a genuine input.

This is **conservative native-input content identity**, not a geometric-equivalence
proof. Two different trees producing the same solid may have different hashes;
equivalent BRep encodings can also differ. A reusable
evaluation cache would also need the evaluator and kernel versions/options in its key.
No OCP evaluator, search loop, or evaluation cache is included yet. Binary-content
hashes identify resources; this projection is not a self-contained OCP program.

Arbitrary Python features/properties, external file inputs and cross-document links
or expressions, ambiguous label references, cycles, and unrecomputed inputs fail
explicitly without a partial hash. Other native property serialization failures also
fail explicitly. An explicit target covers only its dependency closure and resolved
world placement; the default covers the document. Part Design modeling remains the
preferred native workflow.

### Parameter loop direction

Keep canonical JSON and FreeCAD's document as the authority. Follow the parameter
actions described in [vcad's Loon guide](https://docs.vcad.io/guides/ai/loon): named
parameters and their expressions survive evaluation, edits change those parameters,
and gradients describe how an objective responds to them. No CAD DSL is needed.

Today `cad.parameters()` discovers editable native properties and sketch dimensions;
the IR preserves native inputs and ExpressionEngine dependencies. Discovery does not
yet declare optimization knobs or provide a batch parameter setter or gradient API.

The future loop should identify explicit independent native parameters with units
and bounds, retaining derived expressions. Parameter actions must use the existing
executor's single checked transaction and preserve the feature tree. Gradient
evaluation should measure explicit objectives on isolated candidates, report the
method, step sizes and units, and reject invalid or discontinuous samples rather
than invent a derivative. Initially FreeCAD can evaluate finite differences;
analytic differentiation is a separate capability. Cache keys must include the
canonical definition and evaluator/kernel configuration. No loop is implemented yet.

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
and **Notifications**, using the original compact side navigation. Notifications show
the time received by the panel, severity, source, and selectable message text. Select
text and copy it with the usual keyboard shortcut. The panel
retains up to 1,000 messages for the current session.

Standard tools and custom global toolbars sit on the top left, immediately followed
by workbench tabs and the eyedropper. Workbench-specific tools occupy the row below.
Workbench tabs keep the
active label visible; inactive tabs stay icon-only, including on hover. Qt styling
and the Anthracite palette supply compact toolbar spacing, status-bar styling, dimension
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
Bundled Iconoir icons retain their [MIT notice](Resources/icons/LICENSE-Iconoir).
