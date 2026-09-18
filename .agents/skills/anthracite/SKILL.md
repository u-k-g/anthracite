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

- `anthracite exec '<python>'` — run a program; prints the observation JSON on stdout and a
  one-line summary on stderr.
- `anthracite status` — bridge and active-document status.
- `anthracite shots` — list saved viewport images (`--clear` to remove them).

Exit code is `0` when the edit committed, `1` when it was rejected or rolled back, `2` for a
transport problem. Always read the JSON even on `1`; it says why.

If `anthracite` is not on PATH, run `just connect` in the Anthracite repository, or use the
absolute path FreeCAD shows in its status bar.

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
  dimensions). The user also has an editable Parameters tab in FreeCAD for the same values.

Paged results carry `total` and `nextOffset`. Follow `nextOffset` until it is null rather than
assuming the returned page is everything.

## Editing discipline

- Put `cad.action('Meaningful label')` as the **first** statement of a mutating program. It
  names the native undo step; without it the edit is labeled "Agent edit".
- Prefer native parametric features over duplicated solids: sketches + Pad/Pocket, patterns,
  expressions, attachments. Preserve existing feature names and downstream dependencies.
- Express intent with relationships (expressions, constraints, patterns) rather than repeated
  literal dimensions; inspect the parameters you are about to change first.
- History actions (`cad.undo`, `cad.redo`, `cad.checkpoint`, `cad.restore_checkpoint`) must be
  the only statement in a call, with literal arguments. They cannot be nested in a mutation.
- Creating a document is allowed when none is open (`App.newDocument('Part')`), but that call
  has no transaction to roll back to. Prefer one call that creates the document, then a second
  call that builds it, so the build is transactional.

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

Numeric metrics need a finite `tolerance`; booleans and strings do not.

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
