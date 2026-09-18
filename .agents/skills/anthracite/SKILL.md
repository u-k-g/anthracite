---
name: anthracite
description: Use when doing CAD work in FreeCAD through the Anthracite bridge: inspecting a model, creating or editing parametric features, measuring geometry, or verifying a design change with the freecad tool. Also use when the user pastes an Anthracite geometry reference (a dict with document/object/subelement/revision) or asks to operate their running FreeCAD.
---

# Anthracite: FreeCAD through one checked tool

Anthracite exposes a running FreeCAD GUI to any agent as a single MCP tool, `freecad`. The
source you send is ordinary FreeCAD Python. It runs as one native transaction, recomputes and
validates, then either commits or rolls back and returns a structured observation. There is no
per-operation tool catalog and no CAD DSL: use FreeCAD's real document and workbench APIs.

The user must have FreeCAD open with Anthracite running. If a call fails with "bridge is not
running", ask them to open FreeCAD; do not try to start it yourself.

## Connecting

Add the server to your MCP client. `anthracite-mcp` ships beside the Anthracite module
(typically `<FreeCAD user data>/Mod/Anthracite/anthracite-mcp`); use `python3` so the script
never depends on an executable bit or a particular shell. In this repository, `just connect
codex` or `just connect opencode` registers it, or prints the exact command to run.

```sh
claude mcp add anthracite -- python3 /path/to/anthracite-mcp
codex mcp add anthracite -- python3 /path/to/anthracite-mcp
opencode mcp add anthracite -- python3 /path/to/anthracite-mcp
```

```json
{ "mcpServers": { "anthracite": { "command": "python3", "args": ["/path/to/anthracite-mcp"] } } }
```

The server auto-discovers the running bridge. Set `ANTHRACITE_BRIDGE` to override the
discovery file, or `ANTHRACITE_HOST` / `ANTHRACITE_PORT` / `ANTHRACITE_TOKEN` to connect
directly.

## The one tool: `freecad(code)`

`code` is a complete, ordinary FreeCAD Python program. Prebound names: `App`, `FreeCAD`, `Gui`,
`doc` (the active document), and `cad` (an inspection helper). The whole program is one action:
open a transaction, `exec` your code, `doc.recompute()`, validate every object, then commit or
abort. A trailing bare expression becomes the call's `result`.

Because the whole program is one transaction, put a coherent change in one call, and let a
failure roll it back rather than trying to half-apply edits across calls.

## Discover before you act

- `cad.guide()` returns short native recipes (partdesign, edit, expressions, attachments,
  patterns, repair); `cad.guide('edit')` returns one. This is the authoritative starting point.
- `cad.api(query='Pad', kind='types')` lists installed object types; `cad.api('BasePad',
  kind='methods')` documents an object's native methods; `kind` may also be `commands`,
  `workbenches`, or `modules`. Never guess a type, property, or argument that you can look up.
- `cad.tree()` pages the document; `cad.inspect('BasePad')` gives one object's parameters,
  dependencies, expressions, and frame; `cad.sketch('BaseSketch')` lists geometry and
  constraints; `cad.explain('BasePad')` gives the native dependency chain;
  `cad.diagnostics()` reports under-constraint, conflicts, opaque shapes, and recompute errors.

Paged results carry `total` and `nextOffset`. Follow `nextOffset` until it is null rather than
assuming the returned page is everything.

## Editing discipline

- Put `cad.action('Meaningful label')` as the **first** statement of a mutating call. It names
  the native undo step; without it the edit is labeled "Agent edit".
- Prefer native parametric features over duplicated solids: sketches + Pad/Pocket, patterns,
  expressions, attachments. Preserve existing feature names and downstream dependencies.
- Express intent with relationships (expressions, constraints, patterns) rather than repeated
  literal dimensions; inspect the parameters you are about to change before changing them.
- History actions (`cad.undo`, `cad.redo`, `cad.checkpoint`, `cad.restore_checkpoint`) must be
  the only statement in a call, with literal arguments. They cannot be nested in a mutation.

## Read the observation

Every result is a structured observation. The fields that decide your next step:

- `ok` — the transaction committed. `ok: false` returns `error`, and `rolledBack` /
  `requiresInspection` describe whether the document was restored.
- `revision` and `revisionBefore` — the document revision after and before the call.
- `observation.created` / `changed` / `deleted` and `editReceipt` — what actually changed,
  including native side effects during execution.
- `verification` — results of `cad.verify([...])` claims you scheduled (see below).
- `diagnostics` and `warnings` — native status, not proof of design correctness.
- `images` — viewport renders; visual feedback, not proof of exact geometry or topology.
- `nextActions` — suggested recovery steps after a failure. Follow them instead of retrying blind.
- `interveningChanges` — edits made outside Anthracite (user, native undo/redo, other tools).
- `conflict: true` — your plan was formed against a stale revision; nothing was executed.

A successful recompute is **not** proof the design meets the requirement. Before claiming a
result, schedule checks with `cad.verify([...])` (they run after the final recompute and appear
in `verification`), and use `cad.measure(...)` / `cad.topology(...)` for explicit numbers. Report
only what you checked, and say so when a requirement cannot be checked mechanically.

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

## Visual feedback

`cad.review('BasePad')` explains a target and attaches a focused view. `cad.render_views(...)`
attaches up to six ordered views; `cad.render_sketch('BaseSketch')` diagrams a sketch. Renders
never modify geometry and the user's camera is restored. Announce images as feedback, not proof.

## Uncertainty

If a call times out or the connection drops, the outcome is unknown — the edit may have
committed. Do **not** retry. Reconnect, then inspect the document (`cad.tree()`, `cad.inspect`,
`cad.history()`) to establish actual state before deciding anything.

## Troubleshooting

- "bridge is not running" — FreeCAD/Anthracite is not open, or the bridge is disabled.
- "RevisionConflict" — the document changed since your last observation; re-inspect and rebuild
  references before retrying.
- "Topology changed within this action" — finish the current action and inspect topology in a new
  call; do not chain dependent edits that assume pre-edit face/edge indices.
- "Open or create a document before using this helper" / "no active document" — create one with
  `App.newDocument('Part')` inside a `freecad` call; Anthracite will not create a document on its
  own. Prefer a separate call that only creates or opens the document, then build it in the next
  call: with a document already active the build runs in a transaction and rolls back on failure.
  A single call may create the document and build it, but there is nothing to roll back to, so
  that call runs without a transaction and leaves partial work in place if it fails.
