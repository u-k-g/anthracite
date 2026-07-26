# Anthracite

Anthracite is an LLM-native soft fork of FreeCAD. It puts a complete agent
experience inside FreeCAD and lets existing coding agents operate the
application through FreeCAD's real Python API.

## Product

- The agent interface is a native QML-first FreeCAD dock/sidebar. It behaves
  like FreeCAD's other panels: movable, resizable, closable, floatable, and
  restorable.
- The 3D viewport and normal FreeCAD selection, commands, properties, and task
  panels remain first-class ways to work.
- Users connect their existing Codex, Claude Code, or OpenCode installation.
  Anthracite launches and supervises it while preserving its authentication,
  configuration, models, skills, and ordinary tools.
- Anthracite normalizes provider messages, streaming, tool calls, plans,
  approvals, requested input, and errors into one internal event model. It does
  not implement another general-purpose agent harness.
- The interface includes document threads, a streaming timeline, compact
  expandable activity, a bottom composer, model/effort/mode controls, plans,
  approvals, persistent drafts, selection context, and CAD change and
  diagnostic views.

## Agent interface

Anthracite adds one CAD-specific tool to the selected agent:

```text
freecad(<ordinary Python source>)
```

The model writes normal FreeCAD Python with `App`, `Gui`, workbench modules, and
a thin `cad` helper for inspection, selection, help, rendering, structured
output, validation, progress, and cancellation.

Each call:

1. checks the expected document revision;
2. runs on FreeCAD's GUI thread in a named transaction;
3. executes with a fresh local Python namespace;
4. recomputes and validates the affected document;
5. commits on success or rolls back on failure;
6. returns structured results, diagnostics, object/property changes, and
   optional viewport images.

The FreeCAD document persists between calls; Python locals do not. Mutations
are undoable. Object references use document identity, revision, internal name,
and type. Face and edge references retain geometric context and fail on
ambiguity instead of silently selecting different topology.

Use FreeCAD in this order:

1. document and workbench Python APIs;
2. registered FreeCAD GUI commands;
3. thin Anthracite helpers for generic missing ergonomics;
4. targeted Qt interaction for a remaining dialog gap;
5. a narrow FreeCAD patch when no reliable API exists.

Do not turn FreeCAD into hundreds of JSON operations, create a new CAD
language, edit `.FCStd` XML directly, or make screen-coordinate automation the
normal interface.

## Implementation

- Use Rust for anything naturally self-contained when it does not make the
  FreeCAD patch stack harder to maintain: provider processes and protocols,
  sessions and events, Turso persistence, durable activity records, and
  non-GUI background work.
- Use C++/Qt for FreeCAD registration, QObject/QML integration, docking,
  GUI-thread scheduling, and a narrow native bridge.
- Use Python as the model-facing FreeCAD action language.
- Keep the Rust/native boundary small and stable. Do not replace clear FreeCAD
  Python calls with a large custom binding layer.
- Store conversations, sessions, tool calls, plans, CAD actions, diagnostics,
  drafts, and panel state in embedded
  [Turso](https://github.com/tursodatabase/turso). Store only a small
  Anthracite document identity in `.FCStd`.

## Distribution and patches

Anthracite follows Helium's maintainable soft-fork model:

- FreeCAD is pinned to commit
  `145529fe741292ff0b3977a01195bf0247425794`.
- `just setup` materializes that revision in ignored `build/src`.
- FreeCAD is not vendored and is not a Git submodule.
- Every Anthracite source change is an explicit Quilt patch under `patches/`.
- `patches/series` is the authoritative patch order.
- New product code stays concentrated in `src/Mod/Anthracite`, with small
  FreeCAD core patches only for proven integration gaps.

The normal development loop is:

```sh
nix-shell
just doctor
just setup
just push
just patch-new 0001-short-description
just patch-add src/path/to/file

# Edit and test build/src/src/path/to/file.

just patch-diff
just patch-refresh
just validate
```

Run `patch-add` before editing each file. For a new file, add its nonexistent
path first and then create it. Make changes in `build/src` and refresh the
patch; do not leave implementation only in the ignored source tree.

`just validate` applies the full series to an isolated checkout without
disturbing development work. A FreeCAD version bump is deliberate: change the
pin, materialize the new base, repair patches in order, then build and test.

## Design references

- [FreeCAD](https://github.com/FreeCAD/FreeCAD) supplies the application,
  native APIs, module patterns, and UI conventions.
- [T3 Code](https://github.com/pingdotgg/t3code) supplies the existing-provider
  integration model and primary UI/UX inspiration, translated into native QML.
- [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) supplies the
  narrow agent loop: one powerful compositional action followed by a useful
  observation. FreeCAD Python takes the role of its shell.
- [Helium](https://github.com/imputnet/helium) supplies the pinned-upstream,
  disposable-source, ordered-patch workflow.
- [VibeCAD](https://github.com/10-X-eng/vibecad) is low-confidence comparative
  material, not an architectural source of truth.

## First milestone

```text
prompt in the dockable QML sidebar
  → existing Codex, Claude Code, or OpenCode session
  → one freeform FreeCAD Python tool call
  → GUI-thread transaction, recompute, and validation
  → structured CAD change observation
  → streamed result in the sidebar
```

Build this path before expanding the tool surface or workbench coverage.
