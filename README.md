<h1 align="center">anthracite</h1>

<p align="center">
  an llm-native freecad soft fork where existing coding agents can work through freecad's real
  python api.
</p>

<details open>
<summary><strong>overview</strong></summary>

Anthracite puts a complete agent experience inside FreeCAD.

- a native, dockable QML sidebar alongside the 3D viewport
- existing coding-agent installations instead of a new agent harness
- streaming chat and CAD activity with approvals, requested input, plans and durable replay
- transactional, undoable changes with recompute, validation and structured diagnostics

FreeCAD's normal selection, commands, properties, task panels and viewport remain first class.
The agent is another powerful way to operate the application, not a replacement for its existing
interface.

</details>

<details open>
<summary><strong>status</strong></summary>

The working vertical slice is implemented end to end:

- a persistent native FreeCAD dock with a QML chat timeline and composer
- a transactional `freecad` Python executor with rollback, recompute, validation, structured
  change observations and external-edit revision checks
- a Rust process bridge for the existing Codex `app-server`, including streaming, interruption
  and dynamic tool calls
- a provider-neutral, line-delimited interaction protocol for approvals, requested input, plans,
  usage, activity and robust interleaved streaming
- embedded Turso persistence for external document identities, normalized event history and
  resuming the same Codex thread across FreeCAD sessions
- document-scoped conversation creation and navigation with per-thread persistent drafts
- safe session switching when the active FreeCAD document changes, including Save and Save As
- live Codex model/effort controls, per-thread selection persistence and compact expandable
  activity rows
- bounded viewport renders attached to the same transactional `freecad` tool observation for
  visual model verification
- a normalized context-window usage meter in the composer
- structured active-workbench and selected object/face/edge context, including geometry summaries
  for momentary topology references
- live Codex and OpenCode adapters, a native provider switcher and a local MCP bridge that gives
  OpenCode the same transactional `freecad` tool

Codex and OpenCode use the user's existing installations, authentication, configuration, models,
skills and normal tools. Anthracite starts their native server modes, normalizes their events and
adds only its FreeCAD tool. It does not add T3 Code's project registry, worktrees or simultaneous
project sessions: one provider operates on the one active FreeCAD document context. Claude Code is
deliberately not near-term work.

</details>

<details open>
<summary><strong>agent interface</strong></summary>

Anthracite adds one CAD-specific tool to the selected agent:

```text
freecad(<ordinary Python source>)
```

The model writes normal FreeCAD Python using `App`, `Gui`, workbench modules and a thin `cad`
helper. Each call runs on the GUI thread inside a named transaction, recomputes and validates the
document, then commits or rolls back and returns the result, CAD changes and diagnostics.

The document persists between calls while Python locals do not. Document revisions prevent stale
writes. Stable internal object names are reported alongside labels and shape summaries.

Use document/workbench APIs first, registered GUI commands second and thin helpers only for missing
ergonomics. Anthracite does not re-express FreeCAD as hundreds of JSON tools, invent a CAD language,
edit `.FCStd` XML directly or rely on screen coordinates.

</details>

<details open>
<summary><strong>upstream</strong></summary>

Anthracite is an independent soft fork of [FreeCAD](https://github.com/FreeCAD/FreeCAD), pinned to
commit `145529fe741292ff0b3977a01195bf0247425794`.

FreeCAD is materialized in ignored `build/src`; it is not vendored or tracked as a submodule.
Anthracite changes are explicit GNU Quilt patches under `patches/`, applied in the order recorded by
`patches/series`.

</details>

<details>
<summary><strong>architecture</strong></summary>

- QML and Qt Quick own the native sidebar experience.
- Rust owns self-contained provider, protocol, session, event, persistence and background work when
  that keeps the FreeCAD patch stack simple.
- C++/Qt owns FreeCAD registration, docking, GUI-thread scheduling and narrow native bridges.
- Python remains the model-facing FreeCAD action language.
- Embedded [Turso](https://github.com/tursodatabase/turso) stores conversation events, provider
  thread state and document/session associations outside `.FCStd`. Any future in-document metadata
  must use upstream-supported FreeCAD mechanisms and round-trip safely through unmodified FreeCAD.

Keep new product code concentrated in `src/Mod/Anthracite`. Patch FreeCAD core only for narrow,
proven integration gaps.

</details>

<details>
<summary><strong>development</strong></summary>

- `nix-shell` enters the Quilt development environment
- `just setup` materializes the pinned FreeCAD source
- `just push` applies the current patch series
- `just patch-edit sidebar` makes an existing semantic patch current
- `just patch-new feature-name` creates a new semantic patch when no existing patch owns the change
- `just patch-add src/path/to/file` adds a path not already owned by the current patch
- `just patch-refresh` turns source changes into the current patch
- `just validate` applies the full series in an isolated checkout

Patches describe current features and divergences, not their development history. Amend the
existing owning patch whenever possible; patch order belongs only in `patches/series`, so filenames
are never numbered. Keep each source file owned by one patch where practical. Make changes in
`build/src`, then refresh the patch. Do not leave implementation only in the ignored source tree.
FreeCAD version bumps are deliberate: change the pin, repair every patch in order, then build and
test.

With FreeCAD materialized and the patches applied, configure and build the complete application:

```sh
just configure
just build
```

Launch with `just run`; it first performs an incremental full build so a partial developer target
cannot open a workbenchless FreeCAD. The launcher uses a writable, persistent FreeCAD profile under
`build/profile`; this avoids read-only or incompatible system FreeCAD profiles while leaving Codex
and OpenCode authentication and configuration untouched. Open the Anthracite dock, choose Codex or
OpenCode in the composer, and use **Configure…** only if Anthracite cannot discover the existing
executable.

</details>

<details>
<summary><strong>influences</strong></summary>

- [T3 Code](https://github.com/pingdotgg/t3code) — existing-provider integration and primary UI/UX
  inspiration, translated into native QML
- [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) — one powerful compositional action
  followed by a useful observation
- [Helium](https://github.com/imputnet/helium) — pinned upstream, disposable source and an ordered
  patch series

</details>

<details>
<summary><strong>vertical slice</strong></summary>

```text
prompt in the dockable QML sidebar
  → existing Codex or OpenCode installation
  → one freeform FreeCAD Python tool call
  → GUI-thread transaction, recompute and validation
  → structured CAD change observation
  → streamed result in the sidebar
```

Both providers adapt to the same runtime event protocol and share one FreeCAD executor and QML
surface.

</details>

<details>
<summary><strong>license</strong></summary>

Anthracite uses the same license as FreeCAD:
[GNU Lesser General Public License v2.1 or later](LICENSE).

</details>
