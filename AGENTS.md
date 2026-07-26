# AGENTS.md

## Sources of truth

- `README.md` is authoritative for the product, architecture, and patch
  workflow.
- `freecad_commit.txt` is the exact upstream base.
- `patches/series` is the exact order of Anthracite changes.

## Repository model

Anthracite is a patch-stack soft fork of FreeCAD.

- FreeCAD is materialized in ignored `build/src`; it is not vendored and is not
  a submodule.
- The committed product is the pin plus the ordered `.patch` files, not the
  contents or Git history of `build/src`.
- Keep new Anthracite functionality concentrated in
  `src/Mod/Anthracite` inside the FreeCAD tree.
- Patch FreeCAD core only when a narrow integration point is materially cleaner
  or more capable than keeping the change in the Anthracite module.
- `.gitignore` is a strict whitelist. Any new intentional repository file or
  directory must also be explicitly admitted there.

Do not commit directly inside `build/src`, add it to this repository, or leave
an implementation only in that ignored tree.

## Patch workflow

Use GNU Quilt through the `justfile`:

```sh
nix-shell                 # when Quilt is not already available
just doctor
just setup                # materialize the pinned FreeCAD source
just push                 # apply the existing series
just patch-new 0001-name
just patch-add path/in/freecad

# Edit and test build/src/path/in/freecad.

just patch-diff
just patch-refresh
just validate
```

- Run `patch-add` before editing every file. For a new file, add its nonexistent
  path first and then create it.
- Make changes in `build/src`, then let `patch-refresh` produce the patch.
  Do not normally hand-edit generated hunks.
- Apply all earlier patches before creating a new topmost patch.
- Keep patches small, ordered, and independently understandable.
- `just validate` applies the full series to an isolated checkout and checks
  patch integrity; it does not replace focused compilation or tests.
- Do not reset, replace, or delete a dirty `build/src`. Preserve or refresh the
  current patch work first.
- A FreeCAD pin bump is deliberate work: pop the series, change the pin,
  materialize the new base, repair each patch in order, then build and test.

## Product constraints

- The interface and agent experience live inside FreeCAD.
- The UI is QML-first, with Qt Quick where useful. It must be a normal FreeCAD
  dock/sidebar: movable, resizable, closable, floatable, and restorable.
- The native 3D viewport and ordinary FreeCAD interactions remain first class.
- Integrate the user's existing Codex, Claude Code, or OpenCode installation.
  Preserve its authentication, configuration, models, skills, and normal
  tools. Do not build a new general-purpose agent harness yet.
- Expose one CAD-specific model tool:
  `freecad(<ordinary FreeCAD Python source>)`.
- Python is the model's compositional action language. Prefer FreeCAD document
  and workbench APIs, then registered GUI commands, then thin helpers.
- Do not replace FreeCAD with hundreds of per-operation JSON tools, a custom CAD
  language, direct `.FCStd` XML mutation, or screen-coordinate automation.
- Each mutating Python call must run on the GUI thread inside a named FreeCAD
  transaction, recompute and validate, then commit or roll back and return a
  structured CAD-aware observation.
- Documents persist between calls; invisible Python locals do not.
- Treat document revisions, internal object names, and topology ambiguity as
  correctness concerns. Never silently guess a changed face or edge.

## Technology boundaries

- Use Rust whenever the work is naturally self-contained and doing so does not
  make the FreeCAD patch stack harder to maintain.
- Rust should own provider processes and protocol normalization, session/event
  state, Turso persistence, durable activity records, and non-GUI background
  work.
- C++/Qt should own FreeCAD registration, QObject/QML integration, docking,
  GUI-thread scheduling, and narrow bridges to FreeCAD `App` and `Gui`.
- Python remains the model-facing FreeCAD API. Do not create a large Rust
  binding layer where a direct FreeCAD Python call is clearer.
- Keep the Rust/native boundary narrow and stable.
- Use embedded [Turso](https://github.com/tursodatabase/turso) for conversation
  and session state. Store only a small Anthracite document identity in
  `.FCStd`.

## Reference repositories

- [FreeCAD](https://github.com/FreeCAD/FreeCAD): upstream application, source
  conventions, module system, Python APIs, transactions, commands, Qt/QML
  integration, and build/test patterns. Follow native FreeCAD patterns before
  inventing parallel abstractions.
- [Helium](https://github.com/imputnet/helium): copy the pinned-upstream,
  disposable-source, ordered-Quilt-series workflow. Do not copy browser-specific
  packaging or its separate platform-repository layering.
- [T3 Code](https://github.com/pingdotgg/t3code): copy the provider-driver model
  for existing agent installations and the interaction design: threads,
  streaming timeline, compact expandable activity, bottom composer, model and
  effort controls, approvals, plans, requested input, and persistent drafts.
  Translate those ideas into native QML; do not copy its React/WebSocket stack,
  project/worktree concepts, browser preview, or monolithic chat components.
- [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent): copy the narrow
  agent loop and small, powerful tool surface. Its broad shell action maps to
  Anthracite's broad FreeCAD Python action; its command observation maps to a
  transaction/diff/recompute/diagnostic/render observation. Preserve bounded
  execution and serializable trajectories.
- [Codex](https://github.com/openai/codex): reference provider protocols,
  session handling, approvals, tool adaptation, and operational safeguards.
  Keep provider adaptation outside the FreeCAD executor.
- [VibeCAD](https://github.com/10-X-eng/vibecad): low-confidence comparative
  material only. Independently justify anything taken from it. Do not copy its
  large per-workbench tool surface or broad custom CAD runtime.

## Initial implementation target

The first vertical slice is:

```text
prompt in a dockable QML sidebar
  → existing Codex, Claude Code, or OpenCode session
  → one freeform FreeCAD Python tool call
  → GUI-thread transaction and recompute
  → structured CAD change observation
  → streamed result in the sidebar
```

Keep work focused on proving this path before expanding the tool surface,
workbench coverage, or secondary UI.
