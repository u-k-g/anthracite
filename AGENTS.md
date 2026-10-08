# AGENTS.md

Default to `jj`; use `git` only when there is no other option. Run mutating VCS commands
only when explicitly requested.

README is the installation and usage guide. This repository root is a standard FreeCAD
Python addon. Keep implementation here, using public FreeCAD APIs; no core modifications,
native GUI module, build system, or patch workflow belongs in the addon.

FreeCAD is the CAD system. Expose it to external agents through one checked `anthracite`
CLI with four verbs: exec, status, log, shots. Prefer document/workbench APIs, registered
GUI commands, then thin helpers. Do not add a CAD DSL, MCP server, provider adapters,
in-app agent host, or a second executor. One agent operates on the active document at a time.

Preserve editable native feature trees and PartDesign-first modeling. Mutations execute
on the GUI thread inside one named transaction, recompute, validate, then commit or roll
back. Transactions cannot undo arbitrary Python side effects. Report incomplete rollback
honestly. Design correctness requires explicit checks against requirements, not just a
valid solid or successful recompute. Images are feedback, not exact geometry proof.

Documents persist; Python locals do not. Reject stale revision-bound topology references;
report ambiguity and remapping instead of guessing. On timeouts or lost connections,
inspect uncertain outcomes and never replay automatically. Keep session identity outside
`.FCStd`. Operation logs support inspection, not recovery.

Use Python and Qt for the global geometry picker, console/notification dock, compact
workbench tabs, and widget polish; retain native selection and the real Python console.
Keep bridge startup independent of presentation. Do not add an empty workbench just to
expose the bridge. Require FreeCAD >= 1.0 with PySide6 and Python >= 3.11.
Bundle portable appearance and behavior settings as a native preference pack. Apply it
automatically once at first startup, with a backup; preserve later user changes. Keep serialized
window layouts, recent-item history, machine-specific paths/fonts, and fork flags out.
The package metadata's workbench entry only locates root Init.py/InitGui.py; it does not
register a selectable workbench.

Run commands from the repository root. Use Nushell for orchestration. `just install`
copies the addon and installs the bundled CLI and skill; `just test` uses stock FreeCAD
with an isolated user profile and real transports. Do not rebuild FreeCAD. Verify native
parameter edits, undo/redo, intervening user edits, stale references, remapping, explicit
claims, and uncertain execution. Logs from failures stay in `test-results/`.

Preserve authorship and LGPL-2.1-or-later notices. New source uses the SPDX identifier.
Follow existing formatting and names; avoid speculative abstractions and blanket churn.
Handle expected runtime failures as explicit errors. Reserve assertions for structural
invariants and test expectations, never fallible production I/O or user input.
