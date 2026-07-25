# Anthracite

Anthracite is a soft fork of FreeCAD designed so existing coding agents can
operate the entire application through one strong CAD tool and a native
FreeCAD interface.

This document is the concise source of truth for the design.

## Distribution

- Base FreeCAD commit:
  `145529fe741292ff0b3977a01195bf0247425794`.
- Every Anthracite change is stored as an explicit `.patch` file.
- Patches are applied in order from `patches/series`.
- Moving to a newer FreeCAD revision is deliberate: reapply the series, resolve
  conflicts, build, and test.
- New functionality stays concentrated in `src/Mod/Anthracite`, with small
  FreeCAD core patches only where they make the integration cleaner.

## Application shape

```text
Anthracite FreeCAD
  ├─ native dockable QML chat interface
  ├─ Anthracite session and provider controller
  ├─ Turso conversation/state database
  ├─ selected existing agent harness
  │    ├─ Codex
  │    ├─ Claude Code
  │    └─ OpenCode
  └─ one Anthracite CAD tool
       └─ transactional FreeCAD Python execution
```

The interface and Anthracite controller live inside FreeCAD. Codex, Claude Code,
and OpenCode remain their normal provider processes, launched and managed by
Anthracite in the same way T3 Code integrates existing agent installations.

## User interface

- The UI is QML-first, using Qt Quick only where it is useful.
- It is hosted as a normal FreeCAD sidebar/dock, using the same docking system
  as FreeCAD's other on-screen panels.
- It is movable, resizable, closable, floatable, and restorable.
- The native FreeCAD 3D viewport remains the main artifact view.
- The UI takes its interaction design from T3 Code:
  - document/task threads;
  - streaming conversation timeline;
  - compact, expandable agent activity;
  - bottom-docked composer;
  - model, effort, mode, permissions, send, and stop controls;
  - plans, approvals, and requested user input;
  - persistent drafts;
  - selection and viewport context chips;
  - change, object, Python, plan, and diagnostic views.

FreeCAD selections are first-class prompt context. A user can select an object,
feature, face, edge, sketch, drawing, or viewport and refer to it naturally in
the composer.

## Agent integration

Anthracite does not create a new general-purpose agent harness yet.

It follows T3 Code's provider-driver model:

- discover or configure the user's existing agent binary;
- preserve its existing authentication, home directory, configuration, skills,
  models, and ordinary tools;
- launch and supervise provider sessions;
- normalize provider messages, tool calls, approvals, plans, and errors into
  one Anthracite event model;
- support multiple configured instances where useful, such as personal and
  work Codex homes.

Initial providers are Codex, Claude Code, and OpenCode. Each provider adapter
uses its native session/tool interface where possible, with a small compatibility
bridge only where required.

## Model tool surface

Anthracite adds exactly one CAD-specific tool to the selected harness:

```text
freecad(<ordinary Python source>)
```

The model writes normal FreeCAD Python using `App`, `Gui`, workbench modules,
and a small `cad` helper library. Its existing harness tools, such as shell and
file tools, remain available.

Each `freecad` call:

1. receives a fresh local Python namespace;
2. runs on FreeCAD's GUI thread;
3. opens a named document transaction;
4. executes the Python;
5. recomputes and validates the affected document;
6. commits or rolls back;
7. records created, changed, and deleted objects and properties;
8. returns structured diagnostics, the final expression, stdout/stderr, and
   optional viewport images.

The document persists between calls even though Python locals do not.

Useful helpers include:

```python
cad.inspect(...)
cad.find(...)
cad.help(...)
cad.selection()
cad.resolve(...)
cad.select(...)
cad.render(...)
cad.emit(...)
cad.assert_valid(...)
```

Anthracite does not replace FreeCAD with hundreds of JSON operations, a custom
CAD language, XML editing, or screen-coordinate automation. It uses FreeCAD's
real Python and command APIs.

## Rust boundary

Anything that is naturally self-contained and does not make the FreeCAD patch
stack harder to maintain should be implemented in Rust.

Rust should own:

- provider discovery, process management, and protocol normalization;
- the provider-driver registry and session lifecycle;
- the common event and tool-call model;
- Turso access, migrations, and persistence;
- conversation, thread, turn, plan, and activity state;
- durable trajectory and CAD-action records;
- background work that does not touch FreeCAD GUI objects;
- patch/build/version-bump command-line tooling where useful.

C++/Qt should own:

- FreeCAD module registration;
- QObject and QML integration;
- dock/window integration;
- GUI-thread scheduling;
- direct FreeCAD `App` and `Gui` hooks;
- narrow bridges between Rust, QML, and FreeCAD.

Python is the model's action language and is used where FreeCAD already exposes
the best API. Rust should not replace a direct, maintainable FreeCAD Python call
with a large custom binding layer.

The Rust boundary should be narrow and stable, using a small C ABI or similarly
contained bridge so upstream FreeCAD changes do not propagate through the Rust
core.

## Storage

Anthracite uses [Turso Database](https://github.com/tursodatabase/turso) as an
embedded, in-process Rust database.

It stores:

- document identities;
- threads and messages;
- provider sessions;
- plans and approvals;
- tool calls and Python actions;
- CAD transaction IDs and change summaries;
- diagnostics and render references;
- composer drafts;
- panel and layout state.

Only a small Anthracite document UUID belongs in `.FCStd`. Conversation history
stays in Turso rather than bloating or coupling itself to FreeCAD's document
format.

Turso is pinned like any other important dependency, with explicit schema
migrations and backups before destructive migrations.

## CAD state and changes

- Every successful CAD mutation is transactional and undoable.
- Document revisions prevent an agent from writing against stale state after a
  user edit.
- Results are presented as CAD changes: objects, properties, dependencies,
  recompute state, validity, and before/after views where useful.
- Stable object references use document identity and internal object names.
- Face and edge references also retain geometric and adjacency information so
  Anthracite can detect ambiguity after recomputation.
- Ambiguous references produce an error or clarification instead of silently
  selecting different geometry.

## First vertical slice

The first patch series should prove:

```text
prompt in the dockable QML sidebar
  → existing Codex, Claude Code, or OpenCode session
  → one freeform FreeCAD Python tool call
  → GUI-thread transaction and recompute
  → structured CAD change observation
  → streamed result in the sidebar
```

Everything else should grow outward from this narrow path.
