<h1 align="center">anthracite</h1>

<p align="center">
  an llm-native freecad soft fork where existing coding agents can work through freecad's real
  python api.
</p>

<details open>
<summary><strong>overview</strong></summary>

Anthracite puts a complete agent experience inside FreeCAD.

- a native, dockable QML sidebar alongside the 3D viewport
- existing Codex, Claude Code and OpenCode installations instead of a new agent harness
- document threads, streaming activity, plans, approvals, drafts and CAD-aware context
- transactional, undoable changes with recompute, validation and structured diagnostics

FreeCAD's normal selection, commands, properties, task panels and viewport remain first class.
The agent is another powerful way to operate the application, not a replacement for its existing
interface.

</details>

<details open>
<summary><strong>agent interface</strong></summary>

Anthracite adds one CAD-specific tool to the selected agent:

```text
freecad(<ordinary Python source>)
```

The model writes normal FreeCAD Python using `App`, `Gui`, workbench modules and a thin `cad`
helper. Each call runs on the GUI thread inside a named transaction, recomputes and validates the
document, then commits or rolls back and returns the result, CAD changes, diagnostics and optional
viewport images.

The document persists between calls while Python locals do not. Document revisions prevent stale
writes, and ambiguous face or edge references fail instead of silently resolving to different
topology.

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
- Embedded [Turso](https://github.com/tursodatabase/turso) stores conversations, sessions, tool
  activity, drafts and CAD action history. Only a small Anthracite identity belongs in `.FCStd`.

Keep new product code concentrated in `src/Mod/Anthracite`. Patch FreeCAD core only for narrow,
proven integration gaps.

</details>

<details>
<summary><strong>development</strong></summary>

- `nix-shell` enters the Quilt development environment
- `just setup` materializes the pinned FreeCAD source
- `just push` applies the current patch series
- `just patch-new 0001-short-description` creates the next patch
- `just patch-add src/path/to/file` records a file before it is edited
- `just patch-refresh` turns source changes into the current patch
- `just validate` applies the full series in an isolated checkout

Make changes in `build/src`, then refresh the patch. Do not leave implementation only in the ignored
source tree. FreeCAD version bumps are deliberate: change the pin, repair every patch in order,
then build and test.

</details>

<details>
<summary><strong>influences</strong></summary>

- [T3 Code](https://github.com/pingdotgg/t3code) — existing-provider integration and primary UI/UX
  inspiration, translated into native QML
- [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) — one powerful compositional action
  followed by a useful observation
- [Helium](https://github.com/imputnet/helium) — pinned upstream, disposable source and an ordered
  patch series
- [VibeCAD](https://github.com/10-X-eng/vibecad) — low-confidence comparative material, not an
  architectural source of truth

</details>

<details>
<summary><strong>first milestone</strong></summary>

```text
prompt in the dockable QML sidebar
  → existing Codex, Claude Code or OpenCode session
  → one freeform FreeCAD Python tool call
  → GUI-thread transaction, recompute and validation
  → structured CAD change observation
  → streamed result in the sidebar
```

Build this path before expanding the tool surface or workbench coverage.

</details>

<details>
<summary><strong>license</strong></summary>

Anthracite uses the same license as FreeCAD:
[GNU Lesser General Public License v2.1 or later](LICENSE).

</details>
