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
- streaming chat and CAD activity, with durable threads, plans, approvals and drafts to follow
- transactional, undoable changes with recompute, validation and structured diagnostics

FreeCAD's normal selection, commands, properties, task panels and viewport remain first class.
The agent is another powerful way to operate the application, not a replacement for its existing
interface.

</details>

<details open>
<summary><strong>status</strong></summary>

The first working vertical slice is implemented in three ordered patches:

- a persistent native FreeCAD dock with a QML chat timeline and composer
- a transactional `freecad` Python executor with rollback, recompute, validation, structured
  change observations and external-edit revision checks
- a Rust process bridge for the existing Codex `app-server`, including streaming, interruption
  and dynamic tool calls

Codex is the first complete provider adapter. Claude Code and OpenCode adapters, approvals, durable
Turso-backed threads and richer CAD observations remain follow-on work behind the same narrow
runtime protocol.

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
- Embedded [Turso](https://github.com/tursodatabase/turso) will store conversations, sessions, tool
  activity, drafts, CAD action history and document/session associations outside `.FCStd` by
  default. Any future in-document metadata must use upstream-supported FreeCAD mechanisms and
  round-trip safely through unmodified FreeCAD.

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

With FreeCAD materialized and the patches applied, configure and build the current slice with
FreeCAD's Pixi environment:

```sh
pixi run configure-debug
pixi run cmake --build build/debug --target AnthraciteGui FreeCAD -j 10
```

Launch the resulting `build/debug/bin/FreeCAD`, open the Anthracite dock, and use **Configure…** to
select an existing Codex executable if it is not already on FreeCAD's `PATH`.

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
  → existing Codex session
  → one freeform FreeCAD Python tool call
  → GUI-thread transaction, recompute and validation
  → structured CAD change observation
  → streamed result in the sidebar
```

This path is implemented. Additional providers can adapt to the same runtime event protocol without
changing the FreeCAD executor or QML surface.

</details>

<details>
<summary><strong>license</strong></summary>

Anthracite uses the same license as FreeCAD:
[GNU Lesser General Public License v2.1 or later](LICENSE).

</details>
