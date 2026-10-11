# anthracite

A Python addon that lets coding agents operate stock FreeCAD through one checked CLI.
The repository root is the addon: `Init.py` and `InitGui.py` sit beside the engine modules
and executable `anthracite` client. No compilation or custom workbench is needed.

Requires **FreeCAD >= 1.0 with PySide6 (Qt 6)** and Python >= 3.11 inside FreeCAD.
The CLI uses only the Python standard library and runs outside FreeCAD with `python3`.
Your agent keeps its own authentication, configuration, models, skills, and tools.

## Axis measurements and saved requirements

Axis measurements distinguish finite extents from closest-point distance:

```python
cad.measure('LeftWall', 'RightWall', axis='y', at='max', other_at='min')
cad.preview_measurement('LeftWall', 'RightWall', axis='y', at='max', other_at='min',
                        expected=87.8, tolerance=0.01, view='top')
cad.declare_checks('DeckFit', [{
    'object': 'LeftWall', 'other': 'RightWall', 'axis': 'y',
    'at': 'max', 'other_at': 'min', 'metric': 'axisDistance_mm',
    'expected': 87.8, 'tolerance': 0.01,
}])
cad.checks('DeckFit')
cad.preview_checks('DeckFit', view='top')
```

`axisDistance_mm` is signed second anchor minus first. `axisGap_mm` is second
minimum minus first maximum; a negative value indicates projected interval overlap,
not solid interference. `minX_mm`, `maxX_mm`, `spanX_mm` and their Y/Z counterparts
report native BRep bounds. `frame='world'` is the default; name a native object for
its rigid coordinate frame. `distance_mm` remains the finite closest-point distance.
Bounds are kernel measurements with the reported tolerance, not exact symbolic extrema.

Previews label the objects, axis, anchors, frame, measured value and optional requirement.
They draw projected axis distances and preserve the user's camera/selection/model.
Bounds-centre anchors need not lie on the surface; these are explanatory image overlays.
Use a current topology reference for a face/edge preview, or named objects for saved checks.
Saved checks use native links, survive save/reopen, and report automatically after checked
edits. A failure is reported in `verification`; it does not reject an otherwise valid edit.
Deleting a target makes the check unverifiable. Checks do not impose geometric constraints:
use native expressions/sketch constraints to maintain relationships. Verification metadata
does not enter the derived model IR. Remove the native check group to remove its requirements.

Comparison alignment diagnostics include coverage of both sampled surfaces and up to twelve
worst sample mismatches in the reference frame. Distances are between sampled points, not
exact BRep surface distances. Pose ranking includes every sample in both directions rather
than only ICP inliers; native volume IoU judges finalist poses.

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
zero becomes zero, and JSON keys are sorted. Numbers use **12 decimal places** in
native base units, declared in the schema, to absorb last-bit solver and rotation
normalization on native copy/restore. This is a quantized input identity, not exact
floating-point identity. Cached external-sketch projection IDs are excluded; resolved
external links, projected geometry and projection flags remain in the definition.
The authoritative
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

### Native authoring and parameter edits

These helpers create real Sketcher and PartDesign objects. Python remains ordinary
FreeCAD Python; you can inspect and edit every result in the GUI.

```python
cad.action('Create a parametric plate')
body = doc.addObject('PartDesign::Body', 'Body')
sketch = cad.rectangle_sketch(body, 'BaseSketch', '20 mm', '10 mm')
pad = cad.pad(sketch, 'Pad', '5 mm')
cad.declare_parameter('width', sketch, constraint='Width', unit='mm', bounds=[10, 40])
cad.declare_parameter('height', sketch, constraint='Height', bounds=[5, 20])
cad.declare_parameter('thickness', pad, property='Length', bounds=[1, 10])
pad.setExpression('Length', 'BaseSketch.Constraints.Width / 10')
```

`rectangle_sketch` anchors one corner at the origin and names its dimensions Width
and Height; `circle_sketch(body, name, diameter)` centers a circle at the origin and
names its Diameter. Both produce fully constrained sketches in the local XY plane.
An optional native `placement=App.Placement(...)` positions the sketch explicitly.
`pad(sketch, name, length)` and `pocket(sketch, name, length)` create native length-based
features in the sketch's Body. Use native attachment APIs for datum-plane attachment;
the helpers do not guess support faces. They return native objects for further edits.

`cad.parameters('Pad')` discovers native inputs. `cad.parameters()` lists declared
names, bindings, current values, units, declared bounds, native bounds, expressions,
and independence. `App::PropertyLength` cannot be negative; use native
`App::PropertyDistance` for signed offsets. Bounds outside a known native range are
rejected, and a whole batch is preflighted before assigning any input. A `None` native
limit means unknown: FreeCAD's constrained scalar properties do not expose their
runtime min/max through public Python APIs. Native clamping is still detected and
the action rolled back.
Declarations persist as an `AnthraciteParameters` string property on the bound
object. Values and expressions live exclusively in native properties and
ExpressionEngine. Object label changes and constraint index changes preserve named
bindings; deleting or renaming a bound dimension reports a missing binding.
Declaration metadata participates in the definition hash, including bounds.

```python
cad.action('Resize the plate')
cad.set_parameters({'width': '30 mm', 'height': 12})
cad.verify([{'object': 'Pad', 'metric': 'volume_mm3',
             'expected': 1080, 'tolerance': 0.001}])
```

Numbers use the declared unit; quantity strings must have compatible dimensions.
The batch setter preflights every assignment, refuses derived inputs, and preserves
native expressions. The existing executor recomputes and validates the entire batch
in one undo step. Declared bounds are also checked after recompute, including derived
values; a violation rolls back the action. Direct GUI edits remain native FreeCAD
edits, and inspection reads their current values. Geometry changes receive the same
automatic viewport PNG feedback as other checked edits.

For larger parts, use a separate native parameter group and keep dependent dimensions
in expressions; a child sketch referencing its containing Body can create a dependency
cycle. `cad.guide('design-intent')`, `cad.guide('profiles')`, `cad.guide('arcs')`, and
`cad.guide('engineering')` provide native recipes and checks. Prefer a coherent base
outline and shared manufacturing features over accumulating copied solids. Test
individual knobs and a coupled edit, checked undo/redo, then save/reopen/edit.
`cad.editability('BodyName')` audits only that Body and its dependencies; the score is
a heuristic and does not replace these tests.

`cad.bodies()` gives a concise orientation view. `cad.api(kind='helpers', query='render')`
lists actual helper signatures and documentation. `cad.emit('label', data)` writes
to `events`; a standalone read helper returns its value in `result`. Compact CLI output
includes events. Use `--select ok,result,events,verification,shots --print-stdout` when
printing text to avoid also dumping its JSON-contained stdout.

### Geometry selection and isolated candidates

`cad.select_geometry('Pad', surface='Plane', where={'centerOfMass_mm': [15, 6, 3]},
unique=True)` filters measured native geometry and returns revision-bound references.
Use `kind='edges'` for edges. Supported facts include centerOfMass_mm, area_mm2,
length_mm, radius_mm, axisPosition, normal, axis, direction, majorRadius_mm, and
minorRadius_mm. Coordinates are in the world frame; directions must be unit vectors
and are signed analytic directions, not oriented outward face normals. `tolerance`
is an absolute scalar/component-distance tolerance in each queried fact's units.
Without `unique=True`, all matching candidates are paged. A unique query errors on
zero or multiple matches rather than choosing an arbitrary face.

For an edge, `cad.measure(reference)` includes `length_mm`. Measuring two linear
edges also reports angle, parallelism and `lineSeparation_mm`, the perpendicular
distance between their infinite supporting lines. `distance_mm` remains the closest
distance between the finite edges. Distinguish perpendicular strip width, angled end
span and material thickness when checking a design. `isometric` is accepted as an
alias for the `axonometric` render view.

```python
cad.evaluate({'width': '30 mm', 'height': '12 mm'}, target='Pad', checks=[
    {'object': 'Pad', 'metric': 'volume_mm3', 'expected': 1080, 'tolerance': 0.001}
])
```

Evaluation uses native object copying into a temporary hidden transfer document,
then a separate headless FreeCAD process with its own user profile. The worker uses
the same checked executor. It returns candidate validity (`result.ok`), explicit
verification, `accepted` when checks were supplied, parameter values, definition
hash, editability, and optional target measurements. The outer CLI `ok` means the
evaluation request completed; inspect the candidate result before applying it.
Headless candidates have no viewport images. Apply an accepted candidate with
`cad.set_parameters(...)` to obtain GUI feedback.

The source document's native inputs, full BRep geometry, revision and undo history
are checked after copying, including failures. FreeCAD may rewrite external-sketch
projection cache IDs; those are compared through resolved links and normalized
geometry rather than document-local IDs. Checked undo uses the same narrow sketch
normalization, with a pairwise absolute 1e-12 bound on sketch solver floats when a
rounding boundary differs. Sketch structure, flags, constraints and resolved links
remain checked. Full BRep records are compared at 14 decimal places to handle OCCT's
last-bit direction-vector normalization on restore; derived bounds/area/volume summaries
do not override that full-geometry check. Those summaries alone never establish
restoration. Only self-contained native documents
supported by the IR can be
evaluated: Python features, external files and cross-document links fail explicitly.
Finish any open native transaction first. The worker is synchronous; FreeCAD's GUI
waits while it runs. `timeout` defaults to 60 seconds (range 1–300); the separate
worker is terminated on expiry. Failure files and logs remain at the returned path.
Set `FREECAD_CMD` if the command-line executable cannot be found beside FreeCAD.

Canonical JSON remains the derived representation and FreeCAD remains the evaluator.
Gradients, an evaluation cache, and an optimizer are not implemented here.

`cad.export('/path/model.step')` writes detached world-space geometry without
changing the document's cached BRep data. Supported formats are STEP, IGES, BREP,
STL and OBJ; `format='step'` can select the format independently of the filename.
Writes replace the destination after successful export. STL/OBJ are triangulated
with 0.1 mm linear and 0.5 rad angular deflection, reported in `meshOptions`.
Revision-bound geometry references are validated before exporting subelements.

An edit executes on FreeCAD's GUI thread as one named native transaction, recomputes,
validates, and commits or rolls back. The CLI returns JSON, including `ok`, revision tokens,
diagnostics, and viewport PNG paths. After a timeout or lost connection, inspect the
document before deciding what to do next; execution may still have committed.

Preserve editable feature trees with sketches and native PartDesign features.
`cad.verify([...])` checks explicit requirements after recompute; expected values must
come from the requirement. Use `cad.compare('reference.step')` for geometry and
`cad.editability()` for the tree. A high geometry score with low editability is a failure.
The [agent skill](.agents/skills/anthracite/SKILL.md) covers inspection and editing discipline.

### Reference comparison

`cad.compare('reference.step')` compares every visible terminal solid, including
assembly links, against the reference. Supply `target='Body'` or a list of object
names to choose a different scope. Reference geometry is read from STEP or BREP;
the document, placements, and feature history are preserved.

```python
cad.compare('reference.step', tolerance=0.1, angle_tolerance=6,
            samples=1024, starts=64, threshold=0.97)
```

Comparison requires **NumPy and SciPy in FreeCAD's Python environment** (both are
present in the tested stock FreeCAD 1.1.3 distribution). No Open3D dependency is
required. Multi-start alignment uses proper axis rotations, exact BRep inertia
axes, sampled principal axes, analytic surface axes, FPFH descriptors with
graduated robust registration, ICP, and deterministic Sobol rotations. It never
fits scale or accepts a reflection. `alignment` reports the selected transform,
methods, and search count. This is a finite search, not a global-optimum proof.

For a part, `gScore` and `score` are **0.4 shape + 0.4 interface + 0.2 topology**.
`passes` requires the score to reach `threshold` (default **0.97**), with valid
closed solids and no detected mirror mismatch. Shape is the mean of exact
volume IoU and area-weighted surface F1 at `tolerance` in mm; surface samples use
exact distances to BRep boundaries. Topology compares solid components, handles
(through-holes), and enclosed cavities using welded boundary Euler counts and
native shells. Periodic seams are not counted as holes. `volumeRatio`,
`areaRatio`, `linearScale`, and `scaleMatches` remain diagnostic fields;
unaligned bounding sizes do not contribute to the score.

Automatic interface jigs surround reference planar and cylindrical faces with
material and clearance regions; other shapes get an envelope jig. These are
**geometric proxies, not inferred mating requirements**. For specified interfaces,
pass native shapes or STEP/BREP filenames in reference coordinates:

```python
cad.compare('reference.step', jigs=[
    {'kind': 'keep-out', 'region': 'bolt-clearance.brep',
     'shell': 'surrounding-material.brep', 'group': 'bolt-pattern'},
    {'kind': 'keep-in', 'region': 'locating-pin.brep',
     'shell': 'surrounding-clearance.brep', 'group': 'locator'},
])
```

Each jig needs an opposite-material shell and must agree with the reference.
Material and clearance IoUs catch undersized and oversized features. IoU ≤0.80
scores zero and ≥0.95 scores one, with a linear ramp between. Features in a mating
group share one bounded pose search (±1° per axis, ±1% of reference size per axis);
the group's worst feature determines its score, then groups are averaged.
`jig_depth` controls automatic collar thickness in mm; `jig_search_starts`
controls Sobol trials (default 16). Full diagnostics include each jig's missing
material, blocked clearance, and chosen group pose.

For multiple solids, `assembly` reports Hungarian pairing after removing one
shared rigid pose. Per-part pose error is
`d = hypot(translation_mm / 0.1, rotation_deg / 6)` by default. A congruent part
scores `1 / (1+d)`; local volume IoU additionally penalizes wrong part geometry.
Scores are averaged over the larger part count; missing and extra parts score
zero. `score` becomes the assembly score, `gScore` retains the composite geometry
score, and **both must reach the threshold** to pass. Symmetry-equivalent part
rotations use the smallest tested angle. Parts are the individual solids in the
reference file, so export assemblies with their component solids preserved.

Mirror detection runs a separate reflected alignment for diagnosis only. An
exact reflected match that materially improves on the proper match fails; an
achiral part cannot establish handedness. `mirror=False` skips this diagnostic.
Every result records `scorerVersion`. This implements the scoring structure
described by [CADGenBench](https://github.com/huggingface/cadgenbench/blob/main/docs/metrics.md)
and [Normal](https://normal.ai/research/cadarena/methodology); it is not a claim of
benchmark parity with their verifiers. Surface sampling, finite registration,
automatic jigs, and solid-based assembly decomposition have explicit limits.
Large comparisons can take seconds or longer and occupy FreeCAD's GUI thread.
The CLI waits for completion; inspect an uncertain outcome before retrying after
a lost connection.

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
