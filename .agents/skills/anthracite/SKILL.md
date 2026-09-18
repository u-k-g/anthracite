---
name: anthracite
description: Use when doing CAD work in FreeCAD through the Anthracite CLI: inspecting a model, creating or editing parametric features, measuring geometry, or verifying a design change. Also use when the user pastes an Anthracite geometry reference (a dict with document/object/subelement/revision) or asks to operate their running FreeCAD.
---

# Anthracite: FreeCAD through one checked command

Anthracite exposes a running FreeCAD GUI as a single command-line program, `anthracite`. The
Python you pass is ordinary FreeCAD Python. It runs as one native transaction, recomputes and
validates, then either commits or rolls back and prints a structured observation as JSON.

There is no tool catalog and no CAD DSL: use FreeCAD's real document and workbench APIs. The
user must have FreeCAD open with Anthracite running; if a command reports the bridge is not
running, ask them to open FreeCAD rather than starting it yourself.

## Running it

```sh
anthracite exec 'doc.addObject("Part::Box", "Box")'
anthracite exec build.py            # a file
anthracite exec - <<'PY'            # or from stdin for anything long
cad.action('Pocket the top face')
...
PY
```

`exec` prints a **compact JSON** by default — `ok`, `revision`, `error`, `result`, `stdout`/`stderr`,
`verification`, `nextActions`, `shots`, and a small `observation` (created/changed/deleted names).
Add `--full` for the entire observation, `--select ok,result,verification` for chosen keys,
`--print-stdout` to see your program's `print()` output as text, and `--out obs.json` to write the
JSON to a file. **`print()` goes to the `stdout` field** — read it instead of guessing.

- `anthracite status` — bridge and active-document status.
- `anthracite shots` — list saved viewport images (`--clear` to remove them).
- `anthracite log --limit 20` — recent operations, one line each, without parsing `operations.ndjson`.

Exit code is `0` when the edit committed, `1` when it was rejected or rolled back, `2` for a
transport problem. Always read the JSON even on `1`; the summary line on stderr says why.

If `anthracite` is not on PATH, run `just connect` in the Anthracite repository, or use the
absolute path FreeCAD shows in its status bar.

`App`, `FreeCAD`, `Gui`, `Part`, `Sketcher`, `doc` (the active document), and `cad` (the
inspection helper) are prebound; import anything else you need.

## Prefer a file or a heredoc for anything long

`exec` takes a program, so put multi-line work in a `.py` file or a heredoc rather than fighting
shell quoting. One call is one transaction: put a coherent change in one program and let a
failure roll it back instead of half-applying edits across calls.

## Discover before you act

- `cad.guide()` returns short native recipes (partdesign, edit, expressions, attachments,
  patterns, repair); `cad.guide('edit')` returns one. Start here.
- `cad.api(query='Pad', kind='types')` lists installed object types; `cad.api('BasePad',
  kind='methods')` documents an object's native methods; `kind` may also be `commands`,
  `workbenches`, or `modules`. Never guess a type, property, or argument you can look up.
- `cad.tree()` pages the document; `cad.inspect('BasePad')` gives one object's parameters,
  dependencies, expressions, and frame; `cad.sketch('BaseSketch')` lists geometry and
  constraints; `cad.explain('BasePad')` gives the native dependency chain;
  `cad.diagnostics()` reports under-constraint, conflicts, opaque shapes, and recompute errors.
- `cad.parameters('Pad')` lists the editable design parameters (native properties and sketch
  dimensions), which is also how you find what the user can turn without another agent call.

Paged results carry `total` and `nextOffset`. Follow `nextOffset` until it is null rather than
assuming the returned page is everything.

## Part Design first; Part only as a last resort

Default to **PartDesign**. It is the only workbench that keeps a design editable: one Body builds
one contiguous solid as a linear history of sketches and features, so changing a sketch dimension
or a feature parameter rebuilds everything downstream. That is the parametric behaviour the user
expects, and it is what `cad.editability()` scores.

Use native features for intent: `Hole` instead of a cylinder cut, `Pattern`/`Mirrored` instead of
repeated booleans, `Fillet`/`Chamfer`/`Draft` on the feature rather than on raw faces. Attach
sketches to datum planes instead of a fragile top face so names and face indices survive.

Reach for **Part** (CSG: primitives, `Cut`/`Fuse`, one-shot `Fillet`) only when PartDesign cannot
express the job:

- Imported geometry — a STEP or mesh arrives as an opaque `Part::Feature` with no history to
  preserve, so edit it with Part.
- `ShapeString`/lettering and other Draft objects, which exist only in Part/Draft.
- Scripted shape building — Part is FreeCAD's native shape API (`Part.makeBox`, `Part.Wire`,
  lofts from raw geometry).
- Multi-solid results or tooling shapes used to cut several bodies: a Body is one contiguous
  solid, so several solids need several bodies (or Part).
- Quick CSG you will never revisit.

Weak spots to avoid: booleans standing in for design intent, fillets/chamfers bound to face or
edge indices that break when anything upstream changes, and literals with no expressions. Those
are exactly what drags `cad.editability()` down.

Hybrid is normal and expected: keep imported bases and lettering in Part, remodel the parts you
will iterate on as PartDesign Bodies, and use Part booleans only as tools between them.

## Editing discipline

- Put `cad.action('Meaningful label')` as the **first** statement of a mutating program. It
  names the native undo step; without it the edit is labeled "Agent edit".
- Prefer native parametric features over duplicated solids: sketches + Pad/Pocket, patterns,
  expressions, attachments. Preserve existing feature names and downstream dependencies.
- Express intent with relationships (expressions, constraints, patterns) rather than repeated
  literal dimensions; inspect the parameters you are about to change first.
- History actions (`cad.undo`, `cad.redo`, `cad.checkpoint`, `cad.restore_checkpoint`) must be
  the only statement in a call, with literal arguments. They cannot be nested in a mutation.
- When a native feature fails (Pad, Pocket, Hole, Pattern), fix that feature — its profile,
  constraints, support, or parameters. Do not delete it and boolean around the failure.
- Creating a document is allowed when none is open (`App.newDocument('Part')`), but that call
  has no transaction to roll back to. Prefer one call that creates the document, then a second
  call that builds it, so the build is transactional. Do not create or switch the active document
  in the middle of an edit: a transaction cannot span documents, and the call is rejected.

## Working from a drawing

1. Read the drawing with your own image/PDF tools and list its dimensions and features.
2. Inspect what already exists: `cad.tree()`, `cad.inspect()`, `cad.diagnostics()`.
3. Build native parametric features: sketches + Pad/Pocket/Hole/Pattern, expressions for related
   dimensions, datums for mating faces.
4. Render (`cad.render_views`) and verify with explicit `cad.verify` claims and `cad.measure`
   callouts, then check `cad.editability()`.

## Read the observation

The JSON decides your next step:

- `ok` — the transaction committed. `ok: false` returns `error`, and `rolledBack` /
  `requiresInspection` say whether the document was restored.
- `revision` and `revisionBefore` — the document revision after and before the call.
- `observation.created` / `changed` / `deleted` and `editReceipt` — what actually changed,
  including native side effects during execution.
- `verification` — results of `cad.verify([...])` claims you scheduled.
- `diagnostics` and `warnings` — native status, not proof of design correctness.
- `shots` — viewport images written to disk (see below).
- `nextActions` — suggested recovery after a failure; follow them instead of retrying blind.
- `interveningChanges` — edits made outside Anthracite (user, native undo/redo, other tools).
- `conflict: true` — your plan was formed against a stale revision; nothing was executed.

A successful recompute is **not** proof the design meets the requirement. Before claiming a
result, schedule explicit claims. Each claim is a dict with `object`, `metric`, `expected`, and
`tolerance` for numeric metrics; they run after the final recompute and land in `verification`:

```python
cad.verify([
    {'object': 'Pad', 'metric': 'volume_mm3', 'expected': 19200.0, 'tolerance': 1e-6},
    {'object': 'Pad', 'metric': 'valid', 'expected': True},
    {'object': 'Sketch', 'metric': 'sketchDegreesOfFreedom', 'expected': 0, 'tolerance': 0},
    {'object': 'Body', 'metric': 'bodyTip', 'expected': 'Pad'},
])
```

Numeric metrics need a finite `tolerance`; booleans and strings do not. **`expected` must come
from the requirement** — the drawing or the user's numbers — never from a value you computed in
the same program. Verifying a model against itself proves nothing.

Judge the result on two axes, not one:

- **G (geometry)** — does the solid match the intent? `cad.verify` claims, `cad.measure` numbers,
  and `cad.compare('reference.step')` when a reference file exists.
- **E (editability)** — is the tree still parametric? `cad.editability()` returns a score and
  findings: opaque solids, booleans standing in for native operations, repeated features with no
  pattern, literal dimensions with no expressions, pockets that should be holes, blocked sketches.

A high G with a low E is a **failure** — fix the tree before calling the task done.

Interface checks count as geometry too. `cad.measure(<face reference>)` returns `faceType`,
`normal` for a plane, or `axis` / `axisPosition` / `radius_mm` for a cylinder; `cad.measure(a,
other=b)` adds `distance_mm`, `angleDeg`, `parallel`, and `overlapVolume_mm3`. Feed them to
`cad.verify` like any metric, for example
`{'object': <face reference>, 'metric': 'radius_mm', 'expected': 5.0, 'tolerance': 1e-6}` — use it
for datum planes, hole axes, and mating faces rather than only bounding boxes and volumes.

When the user has a reference (a drawing or a STEP file), export and compare: `cad.export('part.step')`
then `cad.compare('reference.step')`, which reports `volumeRatio`, `linearScale`, `scaleMatches`, and
`gScore`. It allows translation and rotation only; a scaled part is caught by the ratios, and mirror
detection is not implemented yet — say so rather than assuming.

Metrics: `volume_mm3`, `area_mm2`, `size_mm`, `centerOfMass_mm`, `valid`, `solids`, `type`,
`fullyConstrained`, `bodyTip`, `sketchDegreesOfFreedom`, `nativeFeatureHistory`; or `distance_mm`
and `overlapVolume_mm3` when the claim adds an `other` object. Use `cad.measure(...)` and
`cad.topology(...)` for numbers you are still exploring. A claim that cannot be evaluated comes
back `unverifiable` — never treat that as a pass. Report only what you checked, and say so when a
requirement cannot be checked mechanically.

## Viewing the shots

Every call that changes geometry writes viewport PNGs and reports them as `shots` with absolute
paths. Base64 is never in the JSON. **Look at them** — read the file with whatever your harness
provides:

- Codex: the `view_image` tool, e.g. `view_image` with the shot's `path`.
- OpenCode: the `read` tool on the shot's `path` (needs a vision-capable model).
- Anything else: open the PNG with your file/image tool.

Images are feedback, not proof of exact geometry or topology. Use them to catch gross errors and
to confirm the shape looks like what was asked, then verify dimensions numerically.

## References expire

A topology reference identifies exact native geometry and **expires whenever the revision
changes**: `{'document': 'Part', 'documentToken': '…', 'object': 'Pad', 'subelement': 'Face3',
'revision': 7}`.

- Never guess a new face or edge index after an edit, undo, or document change. Inspect
  `cad.topology('Pad')` again and use the returned references.
- If a reference is stale (the revision moved on), do not hand-match faces by bounds. Call
  `cad.remap(stale_reference, facts)` with the facts you recorded for it (`area_mm2`/`length_mm`,
  `centerOfMass_mm`, `geometryType`, `normal`/`axis`); it returns the best current match plus
  alternatives, which you should confirm.
- `cad.topology` output is large and pages: pass `kind`, `limit`, and `offset` (follow
  `nextOffset`), or use `cad.review(<reference>)` to look at one face instead of dumping all of them.
- If the user pastes a reference (from Anthracite's geometry picker) into the conversation, pass
  it straight to `cad.review(<reference>)`; `cad.inspect`, `cad.topology`, and `cad.measure`
  accept it too.
- `cad.selection()` describes the user's current FreeCAD selection, including momentary face and
  edge references — use it instead of asking the user to describe what they picked.

## Uncertainty

If a call times out or the connection drops, the outcome is unknown — the edit may have
committed. Do **not** retry. Reconnect, then inspect the document (`anthracite status`,
`cad.tree()`, `cad.inspect`, `cad.history()`) to establish actual state before deciding anything.

## Troubleshooting

- "the Anthracite bridge is not running" — FreeCAD/Anthracite is not open, or the bridge is
  disabled.
- "RevisionConflict" — the document changed since your last observation; re-inspect and rebuild
  references before retrying.
- "Topology changed within this action" — finish the current action and inspect topology in a new
  call; do not chain dependent edits that assume pre-edit face/edge indices.
- "No active FreeCAD document" — create one explicitly with `App.newDocument('Part')` inside a
  call; Anthracite will not create a document on its own.
