# Anthracite: an LLM-native control plane for FreeCAD

Status: architecture recommendation, revised 2026-07-26

> **Current source of truth:** [DECISIONS.md](DECISIONS.md). This longer file
> preserves the research, alternatives, and earlier reasoning; where the two
> differ, `DECISIONS.md` is authoritative.

## Executive decision

Build Anthracite as a soft-forked FreeCAD distribution:

1. pin an upstream FreeCAD commit;
2. express every Anthracite change as an explicit, ordered patch in
   `patches/series`;
3. build an isolated `src/Mod/Anthracite` module into FreeCAD;
4. place both the agent runtime and its user interface inside FreeCAD;
5. give the model one broad, freeform Python tool backed by a structured
   transaction and observation layer.

Do **not** make `.FCStd` XML the editing interface. Do **not** translate all of
FreeCAD into hundreds of JSON tools. Do **not** scatter agent-specific behavior
through unrelated FreeCAD subsystems merely because the fork permits it.

The primary model output should be short Python programs using FreeCAD's real
Python APIs. Every program should run as a named transaction and return a
bounded, structured observation containing document changes, recompute
diagnostics, errors, and—when useful—a rendered view.

The patch-stack distribution makes the runtime, UI toolkit, and FreeCAD API
surface deterministic. The module boundary keeps the fork easy to understand
and rebase. Additional core patches are added when a module-level implementation
would be unnecessarily indirect or a missing primitive would materially improve
the agent interface.

## Why this is the right boundary

FreeCAD is already unusually suitable for a coding agent:

- Its Python API controls documents, objects, properties, recomputation,
  transactions, import/export, and most workbench logic.
- The GUI exposes commands and records many human actions as Python.
- Most addon workbenches are Python and are therefore available to the same
  interpreter.
- The in-tree Web module demonstrates the required Qt event handoff onto
  FreeCAD's main thread. Anthracite does not need its TCP protocol because the
  agent is in-process.

The official manual says Python can access almost any FreeCAD feature and that
most addon workbenches are programmed in Python. The local source confirms the
important execution constraints:

- `FreeCADGui.runCommand`, `doCommand`, and related entry points explicitly
  require Python's main thread.
- `App::Document` exposes transactions, undo/redo, recomputation, object
  creation, generic property access, and saving.
- the built-in Web server posts a Qt event before executing Python in
  `__main__`.

References:

- [FreeCAD source](https://github.com/FreeCAD/FreeCAD)
- [FreeCAD manual](https://www.freecad.org/manual/a-freecad-manual.pdf)
- [FreeCAD `App::Document` API](https://freecad.github.io/SourceDoc/d8/d3e/classApp_1_1Document.html)
- [FreeCAD Web module](https://github.com/FreeCAD/FreeCAD/tree/main/src/Mod/Web)

## Product shape: patch-stack soft fork

| Approach | Determinism | Internal access | User experience | Recommendation |
|---|---:|---:|---:|---|
| Stock FreeCAD addon | Medium | High but Python-bound | Installed beside FreeCAD | Useful prototype, not the product |
| Patch-stack soft fork | High | Complete | One integrated application | Chosen |
| Long-lived divergent branch | Medium | Complete | One integrated application | Less explicit than patches |
| `FreeCADCmd` only | High | Headless only | No live CAD workspace | Test harness only |
| Direct `.FCStd` editing | Low correctness | Persistence layer only | No live semantics | Never as normal editing |
| GUI clicking/typing | Low | Visible UI only | Brittle | Last-resort compatibility |

The maintenance unit is an ordered patch, not a permanently merged fork branch:

```text
freecad_commit.txt
patches/
  series
  0001-add-anthracite-module.patch
  0002-bundle-anthracite-ui.patch
  0003-add-document-change-journal.patch
  ...
```

When the pinned FreeCAD revision moves, reapply the series, resolve failures,
build, and run the CAD-agent benchmark. Version bumps are deliberate product
work rather than an ongoing compatibility promise.

### What the built-in module owns

`src/Mod/Anthracite` owns:

- the embedded T3-inspired chat/workspace UI;
- the mini-swe-style agent loop and model/provider adapters;
- conversation, turn, activity, plan, and composer-draft persistence;
- the internal request queue drained on the Qt/main thread;
- transaction and recompute policy;
- Python execution in a controlled namespace;
- generic document/property/dependency inspection;
- selection, camera, screenshot, and view helpers;
- document and selection observers;
- command discovery and `Gui.runCommand` access;
- a FreeCAD dock/workspace for chat, approvals, plans, logs, and CAD changes;
- capability and version negotiation;
- per-document revision numbers and optimistic write guards.

Keep model/network streaming off the GUI thread. Tool execution that touches
FreeCAD is posted to the GUI thread and its structured result is returned to
the agent loop. This is an internal queue, not IPC.

### What may eventually need a FreeCAD patch

Maintain a capability-gap ledger during implementation. The soft fork lets us
patch immediately, but the same discipline still matters: a core patch should
create a clean general primitive instead of embedding one model's behavior in
FreeCAD. Plausible candidates are:

- a structured and cancellable main-thread execution primitive;
- machine-readable metadata for the entire GUI command registry;
- a stable offscreen render/screenshot primitive;
- bindings for specific C++-only workbench operations;
- stronger document/object/subelement identity across recomputes;
- better structured recompute and solver diagnostics;
- a typed document change journal for agent-facing CAD diffs;
- first-class selection/reference serialization;
- built-in Qt WebEngine/WebChannel integration for the Anthracite UI.

Patch rules:

- pin an upstream FreeCAD commit;
- keep an ordered `patches/series`;
- make each patch focused and independently testable;
- separate build/module patches from FreeCAD behavior changes;
- upstream generally useful primitives when desirable;
- validate the whole series only when intentionally bumping FreeCAD.

Helium demonstrates that an ordered patch product can track a much larger
upstream. It also demonstrates the cost: its local series contains hundreds of
patches. Anthracite should use the mechanism without adopting that scale.

The fork should remain *extension-shaped*: concentrate new functionality in the
Anthracite module, with narrow hooks into `App` and `Gui`. A conventional
divergent branch is not more capable than this patch stack and makes individual
changes harder to inspect, reorder, remove, or upstream.

## Proposed architecture

```text
┌──────────────────────── Anthracite FreeCAD ───────────────────────┐
│                                                                   │
│  QWebEngineView: T3-inspired React UI                             │
│  timeline · composer · plans · CAD diffs · object context         │
│                 ↕ typed QWebChannel commands/events               │
│                                                                   │
│  session store ← agent loop → model/provider adapter              │
│                       background thread / async loop               │
│                              │                                    │
│                       freeform Python action                       │
│                              ▼                                    │
│  GUI-thread transaction executor                                  │
│        ↙ inspector      ↓ App/Gui APIs      renderer/selection ↘   │
│                              ▼                                    │
│                       active document                              │
└───────────────────────────────────────────────────────────────────┘
```

### Component 1: embedded UI

Use a bundled React application inside a `QWebEngineView`, hosted in an
Anthracite `QDockWidget` or integrated workspace panel. FreeCAD already has
Qt WebEngine/PySide integration for its Help module, and the soft-forked build
can make WebEngine and WebChannel required dependencies.

Load compiled static assets from the application bundle or Qt resources. Do not
run a localhost frontend server in production. A typed `QWebChannel` bridge
connects UI commands and events directly to the Anthracite module.

The WebEngine render process is an implementation detail of the embedded UI;
there is no separately installed agent service or user-visible second
application.

### Component 2: in-process agent runtime

Mirror mini-swe-agent's good property: the control loop is nearly trivial.

```text
while not finished:
    action = model(messages)
    observation = freecad_environment.execute(action)
    messages += observation
```

The runtime lives inside the FreeCAD process and owns:

- model/provider adaptation;
- native tool-call parsing;
- step, token, cost, and wall-time limits;
- user steering and approvals;
- durable, replayable trajectories;
- context compaction;
- task completion;
- retry policy;
- the bridge between UI state, document context, and model messages.

This follows the useful separation visible in Codex: tool specifications and
provider adapters remain distinct from FreeCAD execution even though both live
in the same application.

### Component 3: GUI-thread tool executor

The agent loop and API streaming must not block Qt event processing. When the
model invokes the FreeCAD tool, the runtime posts a request to the main thread,
executes one named transaction, and resolves the awaiting agent continuation
with the observation.

Only one mutation runs at a time. The concern is coherent FreeCAD state and
responsive streaming—not treating process failure as a reason to move the
product out of FreeCAD.

## T3 Code UI/UX translated to CAD

T3 Code's most useful patterns are visible in its centered virtualized timeline,
bottom-docked context-aware composer, compact grouped tool calls, thread status
sidebar, persistent drafts, plan surface, and thread-scoped tabbed right panel.
Anthracite should reuse those interaction ideas without copying its
code-oriented information architecture literally.

### Workspace mapping

| T3 Code concept | Anthracite equivalent |
|---|---|
| Project | FreeCAD document/project |
| Thread | Agent task or design conversation |
| Working/completed/needs-input status | Live agent state beside each thread |
| File or element mention | Object, feature, face, edge, sketch, drawing, or viewport context |
| Browser element picker | FreeCAD selection/context picker |
| Changed files | Created/changed/deleted FreeCAD objects and properties |
| Code diff | CAD change journal plus before/after or ghosted geometry |
| Browser preview | Native FreeCAD 3D view |
| Terminal/tool log | Python action, stdout, diagnostics, and recompute log |
| Plan sidebar | CAD task steps and active operation |
| Checkpoint/revert | FreeCAD transaction/undo checkpoint |

### Default layout

Keep FreeCAD's 3D viewport as the primary artifact rather than replacing it
with a full-screen chat application:

```text
┌──────────────┬───────────────────────────────┬────────────────────┐
│ FreeCAD tree │                               │ Anthracite         │
│ and tasks    │       native 3D viewport      │ thread timeline    │
│              │                               │                    │
│              │                               │ composer           │
└──────────────┴───────────────────────────────┴────────────────────┘
```

The Anthracite panel is resizable, collapsible, and floatable. A focus mode can
temporarily widen it, but the normal loop lets the user watch and manipulate
the same live model the agent is changing.

Inside the Anthracite panel:

- a compact thread switcher shows working, completed, error, and needs-input
  states;
- the timeline streams brief commentary and folds completed Python/tool
  activity behind concise summaries;
- the composer remains anchored at the bottom with model, effort, interaction
  mode, autonomy/approval policy, context meter, send, and stop;
- secondary tabs show **Changes**, **Objects**, **Plan**, **Python**, and
  **Diagnostics**;
- panel and draft state are stored per document/thread.

### Selection as composer context

T3 Code turns picked browser elements into removable composer chips with a
stable, bounded serialized payload. Anthracite should do the same for CAD
selections.

A chip might display:

```text
[Pad.Face3]  cylindrical face · r=4 mm
[Sketch]     3 DoF · 12 constraints
[TechDraw]   Page001
[Viewport]   current camera + screenshot
```

Its hidden model context includes the document/revision, internal object name,
subelement resolver, geometric signature, relevant properties, and optional
render. The user can select geometry and type “make these holes 6 mm” without
describing which faces they mean.

Deduplicate repeated picks, clamp all serialized context, preserve chips in the
composer draft, and render them separately from the user's natural-language
message.

### CAD-native change presentation

Do not show raw property dumps as the primary result. After each tool action,
show a compact card:

```text
Modified mounting bracket
  + Pocket
  ~ Sketch.HoleDiameter   4 mm → 6 mm
  ~ Body.Shape            valid · 1 solid
  Recompute successful                         [View changes]
```

`View changes` opens a tab containing the object/property tree, Python action,
diagnostics, and when feasible a before/after or ghost overlay in the native
viewport. Turn-level undo belongs beside that summary.

### State model

Copy T3 Code's separation between sparse thread-shell state and active
thread-detail state:

- sidebar shell: title, document, status, latest activity, pending approval or
  input, timestamps;
- active detail: messages, tool activities, plans, checkpoints, CAD changes,
  attachments, and composer draft;
- append-only turn/activity records projected into the UI;
- stable IDs for threads, turns, messages, tool calls, transactions, and
  document revisions.

Persist this in an in-process SQLite database keyed by a FreeCAD document UUID
or project identity. Large transcripts should not be placed directly in
`.FCStd` unless an explicit portable-session feature is added later.

### What not to copy from T3 Code

- Do not reproduce its project/worktree/branch concepts when FreeCAD has no
  equivalent.
- Do not turn the native viewport into an embedded browser preview.
- Do not make chat the only way to interact; ordinary FreeCAD selection,
  property editing, commands, and task panels remain first class.
- Do not allow the main chat container to become monolithic. T3 Code's local
  `ChatView.tsx` is over 6,000 lines and its composer is over 2,700 lines;
  Anthracite should separate session controller, timeline, composer, context
  chips, change surface, and FreeCAD bridge from the outset.

## What the model should output

### Primary representation: Python

The model should emit Python because:

- it is FreeCAD's native automation language;
- it composes across workbenches and addons;
- current LLMs have extensive Python priors;
- it can express loops, constraints, queries, recovery, and validation without
  inventing a CAD-specific planning language;
- users can inspect, replay, test, and edit it;
- the escape hatch is also the complete interface.

Prefer a freeform code tool when the host supports one:

```python
# freecad
doc = App.ActiveDocument
body = doc.addObject("PartDesign::Body", "Bracket")
sketch = doc.addObject("Sketcher::SketchObject", "BracketProfile")
body.addObject(sketch)
# ... normal FreeCAD Python ...
cad.emit({"body": body.Name, "sketch": sketch.Name})
```

The execution namespace should preload:

- `App` and common FreeCAD modules;
- `Gui` only when a GUI is attached;
- a thin `cad` helper for structured output, bounded inspection, identity,
  rendering, progress, and cancellation checks.

`cad` must remain a convenience and safety layer over FreeCAD—not a second CAD
language or shadow document model.

### The model-visible tool surface

The Anthracite agent sees one freeform tool:

```text
freecad(<ordinary Python source>)
```

The model supplies only Python. The environment supplies the current document
revision, transaction name, timeout, output limits, rollback policy, and result
serialization. This removes JSON ceremony and makes the interface directly
analogous to mini-swe-agent's single broad shell action.

Inspection, rendering, help, selection, output, and cooperative progress are
ordinary functions inside that environment:

```python
cad.inspect(target, fields=None, depth=1)
cad.find(type=None, label=None, name=None)
cad.help(query)
cad.selection()
cad.resolve(reference)
cad.select(references)
cad.render(target=None, view="current", annotations=True)
cad.emit(value)
cad.assert_valid(target=None)
cad.progress(message, fraction=None)
cad.check_cancelled()
```

The tool behaves like a notebook cell: it accepts multiple statements, captures
stdout/stderr, and serializes the final expression automatically. The FreeCAD
document persists between calls, but each call receives a fresh local Python
namespace so agent behavior does not depend on invisible interpreter globals.

Do not enumerate every Part, Part Design, Sketcher, Assembly, FEM, CAM, or
TechDraw operation as a separate model tool. High-level shortcuts may be added
as tested Python recipes and dynamically loaded help, but they should not become
a second implementation of FreeCAD.

### Observation contract

Each mutating step should return something like:

```json
{
  "ok": true,
  "transaction_id": "tx_0187",
  "revision": 43,
  "stdout": "",
  "result": {"body": "Bracket", "sketch": "BracketProfile"},
  "changes": {
    "created": ["Bracket", "BracketProfile"],
    "changed": [],
    "deleted": []
  },
  "recompute": {
    "ok": true,
    "errors": [],
    "still_touched": []
  },
  "warnings": [],
  "elapsed_ms": 84,
  "view": null
}
```

On failure it should include exception type, message, bounded traceback,
partial changes, recompute/solver errors, and whether the transaction was
aborted or deliberately retained for diagnosis.

The default should be rollback on an unexpected exception or failed validation.
A caller may request retention only explicitly. After rollback, the response
must still preserve the diagnostic observation.

### Step execution semantics

For each mutation:

1. reject the request if `expected_revision` does not match;
2. capture a lightweight before-snapshot;
3. open a named FreeCAD transaction;
4. execute the Python on the main thread;
5. recompute;
6. inspect invalid, error, and still-touched objects plus workbench-specific
   diagnostics where available;
7. compute a bounded object/property/dependency diff;
8. commit on success or abort on failure;
9. increment the document revision;
10. optionally render;
11. return one deterministic observation.

Cancellation can be cooperative between Python statements or helper calls.
An arbitrary OpenCascade or C++ operation that blocks the GUI thread generally
cannot necessarily be preempted. The in-app stop action interrupts model
streaming immediately and requests cooperative tool cancellation when the
current operation permits it.

### Identity and references

Model-visible references should combine:

- session and document ID;
- document revision;
- object internal name, not user-editable label alone;
- type ID;
- optional persistent object UUID if FreeCAD exposes one reliably;
- a subelement resolver token.

Do not promise that `Face1`, `Edge7`, and similar topological names remain the
same after model changes. For important subelements, store a query or geometric
signature—adjacency, surface type, centroid, normal, area/length, and parent
feature—and resolve it again after recomputation. Return ambiguity rather than
silently selecting the wrong face.

### Source-backed mode

For new parametric parts, Anthracite should optionally retain a `.py` program
beside the `.FCStd` document:

```text
project/
  bracket.FCStd
  bracket.py
  anthracite.json
```

This brings code-first benefits—diffs, review, regeneration, parameters, and
tests—while leaving the actual result as a native FreeCAD document humans can
continue to edit.

It should be a mode, not a universal source of truth. Existing documents,
interactive edits, assemblies, drawings, imported models, and workbench proxy
objects often require direct document mutation. If a source-backed program is
edited, use ordinary source patches rather than a new JSON edit language.

## How Anthracite should drive FreeCAD

Use the following precedence:

1. **Document/workbench Python API** for semantic operations.
2. **GUI command registry** for commands that exist only as registered
   FreeCAD actions.
3. **Thin Anthracite helper** for generic inspection, transactions, views, and
   missing ergonomic bindings.
4. **Qt widget interaction** only for a remaining task-panel/dialog gap.
5. **Upstream or overlay patch** when none of the above is reliable.

This makes behavior semantic and testable while retaining a route to "all of
FreeCAD." The model should not normally emit screen coordinates or raw widget
gestures. A compatibility layer can inspect the main window and operate a
specific dialog, but the gap should be recorded so a better API can replace it.

### Why not edit `.FCStd` XML

An `.FCStd` file is a ZIP persistence container, not a declarative XML API.
FreeCAD writes `Document.xml`, GUI state, type-specific auxiliary files, and
possibly binary BREP data. Object proxy code can participate in
serialization/deserialization. Direct edits would bypass:

- transactions and undo;
- recomputation and dependency propagation;
- property type validation;
- workbench proxy serialization;
- GUI document state;
- version migrations;
- additional and binary files;
- normal save integrity and backup behavior.

XML/ZIP inspection may be useful for offline diagnostics, forensic diffs, or
repair tooling. Normal writes should always go through FreeCAD and be verified
by save, close, reopen, and recompute.

The relevant save path is visible in
[`App::Document`](https://github.com/FreeCAD/FreeCAD/blob/main/src/App/Document.cpp).

## Lessons from the local repositories

### mini-swe-agent: copy the narrow waist

The important idea is not Bash specifically. It is one powerful,
compositional action language behind a very small loop:

```text
model query → execute action(s) → return observation
```

Its local environment gives the model one broad shell tool and returns output
plus a return code. For FreeCAD, Python is the equivalent broad action
language. Preserve step/cost/time limits and trajectory serialization. Replace
shell output with a CAD-aware transaction/diff/recompute/render observation.

### Codex: keep protocol adapters outside the executor

The local Codex tool design separates tool specification/adaptation from
execution and orchestration. Anthracite should likewise have one internal
FreeCAD driver contract, then adapt it to model-native freeform tools, JSON
function tools, MCP, CLI, or a future UI.

### VibeCAD: extract principles, not architecture

The local VibeCAD tree contains 260 Python files and about 193,719 lines under
its FreeCAD module. It maps many workbenches into large native/scripted tool
surfaces and adds a broad custom domain runtime.

Ideas worth retaining:

- sparse turn-start context;
- bounded inspection;
- workbench-aware capabilities;
- revision guards and stable identifiers;
- transactions and recompute diagnostics;
- worker-process isolation;
- rendered feedback;
- preserving accepted candidate programs.

Ideas to avoid:

- manually re-expressing the entire CAD API as per-operation JSON tools;
- duplicating every workbench's semantics;
- creating a large custom CAD scripting language;
- coupling providers, chat UI, execution, and domain logic;
- retaining failed mutations by default.

The maintainability problem is architectural, not merely code style: every new
FreeCAD feature creates another mapping and another opportunity to diverge.

### Helium: use a patch mechanism only for proven gaps

Helium keeps an ordered patch series over a much larger upstream. The relevant
lesson is reproducibility—pinned upstream, explicit patch order, and continuous
rebase validation. The caution is that a patch stack can itself become the main
product. Anthracite should not begin by assuming that burden.

## External prior art

### FreeCAD MCP projects

Several projects independently converge on the same basic process boundary:
an addon runs inside FreeCAD, while an external process speaks MCP and forwards
requests over local RPC.

| Project | Shape | Lesson |
|---|---|---|
| [neka-nat/freecad-mcp](https://github.com/neka-nat/freecad-mcp) | Small object tools, arbitrary Python, screenshot | A compact code escape hatch supplies broad coverage |
| [theosib/FreeCAD-MCP-Server](https://github.com/theosib/FreeCAD-MCP-Server) | External MCP bridge, addon RPC queue, rich inspectors | Main-thread queue plus structured diagnostics is a strong baseline |
| [contextform/freecad-mcp](https://github.com/contextform/freecad-mcp) | Dozens of operations plus Python | Structured shortcuts do not remove the need for code |
| [spkane/freecad-addon-robust-mcp-server](https://github.com/spkane/freecad-addon-robust-mcp-server) | 150+ tools and multiple transports/modes | Broad tool enumeration grows quickly; directly importing FreeCAD has ABI/platform risks |
| [blwfish/freecad-mcp](https://github.com/blwfish/freecad-mcp) | Addon/bridge with a moderate tool set | Confirms practical demand for persistent, day-to-day control |
| [FreeCAD AI](https://github.com/ghbalf/freecad-ai) | Workbench, code generation, structured operations, skills and visual correction | Useful feature survey; also explicitly warns generated code can crash FreeCAD |

These are evidence for the process seam, not a reason to clone any one protocol.
Anthracite should combine their compact executor and strong inspection ideas,
then add rigorous transactions, revisions, replay, capability negotiation,
testing, and recovery.

### CAD-agent research

- [CAD-Assistant](https://cadassistant.github.io/) uses a visual-language
  planner that iteratively emits Python against the FreeCAD API and observes
  evolving rendered state. Its tool augmentation includes rendering, cross
  sections, and sketch parameterization. This is the closest research
  validation for the proposed loop.
- [Agent-Aided Design / AADvark](https://arxiv.org/abs/2604.15184) uses the
  general pattern "write CAD code, compile, visualize, iterate."
- [Text2CAD-Bench](https://arxiv.org/abs/2605.18430) evaluates code-based CAD
  generation and highlights declining reliability as topology and advanced
  operations become more complex. Anthracite needs an execution benchmark, not
  impressive one-shot demos.
- [neuralCAD-Edit](https://autodeskailab.github.io/neuralCAD-Edit/) studies
  multimodal edit communication. Real users combine language with pointing and
  sketches, which argues for selection tokens and annotated view input rather
  than text-only object names.

### Code-first CAD systems

- [KCL](https://zoo.dev/docs/kcl) treats readable source as the parametric
  source of truth and pairs it with engine-level inspect/render/debug tools.
- [build123d](https://build123d.readthedocs.io/en/stable/index.html) offers a
  modern typed Python interface over OpenCascade BREP geometry.
- [CadQuery](https://cadquery.readthedocs.io/en/stable/index.html) shows why a
  fluent, textual Python model is natural for generated parametric geometry.
- [OpenSCAD](https://openscad.org/documentation.html) is the older proof that
  plain text is excellent for reproducibility, although its modeling model is
  much narrower than FreeCAD's.
- [Onshape FeatureScript](https://cad.onshape.com/FsDoc/index.html) demonstrates
  the strongest version of the idea: native UI features and generated code can
  share the same semantic language.

Anthracite should get code-first benefits without replacing FreeCAD's feature
tree with a new kernel or DSL.

### Other application bridges

- [Blender MCP](https://blendermcp.org/) uses arbitrary Python for full
  application control.
- [blender-remote](https://github.com/igamenovoer/blender-remote) explicitly
  argues against mapping an application's whole API into MCP and instead
  provides infrastructure for reusable Python tools.
- [Fusion 360 MCP](https://github.com/faust-machines/fusion360-mcp-server)
  similarly uses an in-application addon plus an external MCP server.
- [SolidWorks MCP](https://github.com/andrewbartels1/SolidworksMCP-python)
  exposes more than a hundred COM-backed operations, illustrating the discovery
  and maintenance cost of a large enumerated tool surface.

The common winning pattern is a small transport and execution substrate plus
application-native code.

## Security and failure model

The `freecad` tool is arbitrary Python execution inside the user's FreeCAD
session. Anthracite is therefore a trusted local agent operating with the
user's authority, not a multi-tenant execution service.

Product controls:

- store provider credentials through the operating-system credential store;
- show the active model, mode, permissions, and mutation state in the composer;
- require approval for commands, subprocesses, network, overwrite/export, and
  destructive document operations according to the selected policy;
- cap output, runtime, render size, and observation breadth;
- keep automatic backups and save only through atomic FreeCAD paths;
- record model messages, Python actions, observations, revisions, and
  transaction IDs in the thread timeline;
- refuse stale writes;
- make every successful mutating turn directly undoable from its change card.

Blocking suspicious Python strings is not a sandbox. Python introspection makes
such filters bypassable. Anthracite should describe its permission choices
honestly instead of claiming that a blacklist makes model-generated Python
safe.

## Benchmark before product expansion

Build a corpus of complete tasks, not isolated API calls. Include:

- Part and Part Design creation/editing;
- constrained Sketcher workflows and solver-error recovery;
- assemblies and external references;
- TechDraw pages and export;
- Spreadsheet-driven parameters and expressions;
- imports, exports, units, materials, colors, and metadata;
- Path/CAM, FEM, and other installed workbenches;
- view, selection, camera, section, and screenshot tasks;
- file open/save-as/close/reopen;
- preference and workbench commands;
- edits to existing messy documents;
- failure, undo, stale revision, cancellation, and user steering.

For every task record:

- semantic correctness;
- visual correctness where relevant;
- recompute and save/reopen success;
- whether undo returns exactly to the prior state;
- API layer used (Python, command, Qt fallback, patch needed);
- action count, latency, context size, and model cost;
- deterministic replay rate;
- UI responsiveness and time to visible feedback.

This corpus measures whether the single Python tool and its observations are
actually sufficient across FreeCAD.

## Implementation sequence

### Phase 0: executable spike

- Pin the first FreeCAD commit and establish `patches/series`.
- Add the built-in `src/Mod/Anthracite` module.
- Implement the background agent loop and one freeform `freecad` Python tool.
- Add GUI-thread execution, named transactions, recompute, rollback, and a
  minimal structured diff.
- Prove ten golden tasks inside the Anthracite FreeCAD build.

Success criterion: a prompt entered inside FreeCAD streams an agent turn,
changes the active document through Python, and returns a validated CAD
observation.

### Phase 1: integrated T3-style workspace

- Bundle a React UI in `QWebEngineView` and bridge it with `QWebChannel`.
- Implement thread switcher, virtualized timeline, streaming messages, grouped
  tool activity, persistent drafts, and the bottom-docked composer.
- Add model, effort, interaction-mode, permission, send, and stop controls.
- Add selection/object/face/edge/viewport context chips.
- Add plans, user-input prompts, and in-composer approvals.

### Phase 2: CAD-native result surfaces

- Revision guards and one-writer semantics.
- Generic property/dependency/shape/sketch inspectors.
- Annotated renders and stable selection tokens.
- Turn-level change cards and undo.
- **Changes**, **Objects**, **Plan**, **Python**, and **Diagnostics** tabs.
- Before/after or ghosted geometry presentation where practical.
- Save/reopen and transaction invariants.

### Phase 3: breadth and refinement

- Dynamic recipes/skills for workbenches.
- Optional source-backed projects.
- Keyboard shortcuts, command palette, focus mode, and polished empty states.
- Thread-shell/detail persistence and context compaction.
- 100+ task compatibility dashboard across supported FreeCAD versions.

### Phase 4: version-bump discipline

- Script patch application, refresh, build, and focused test workflows.
- On an intentional FreeCAD bump, reapply the ordered series, inspect upstream
  changes, resolve failed patches, and run the full CAD-agent benchmark.
- Keep behavioral patches focused even when Anthracite has no requirement to
  support arbitrary stock FreeCAD versions.

## Immediate repository plan

A sensible initial tree is:

```text
anthracite/
  freecad_commit.txt
  patches/
    series
    0001-add-anthracite-module.patch
    0002-add-embedded-ui.patch
    0003-add-agent-runtime.patch
    0004-add-freecad-python-tool.patch
    ...
  scripts/
    apply-patches
    refresh-patch
    build
    bump-freecad
  benchmarks/
    tasks/
    evaluators/
    fixtures/
  tests/
  docs/
```

The first implementation should prove only this narrow waist:

```text
prompt in embedded UI
    → in-process minimal agent loop
    → one freeform Python action
    → GUI-thread named transaction
    → recompute and validate
    → structured diff/diagnostics/render
    → timeline observation and next model step
```

If that narrow waist is correct, FreeCAD's existing and future Python surface
does most of the expansion work for us.
