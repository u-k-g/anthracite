<h1 align="center">anthracite</h1>

<p align="center">
  an llm-native freecad fork where coding agents work through freecad's python api.
</p>

<details open>
<summary><strong>overview</strong></summary>

Anthracite puts your existing Codex, OpenCode, or Hermes installation in a native FreeCAD sidebar.
Describe a change, attach images, or pick a face or edge to reference in your message.
Inspect the agent's work through expandable activity, viewport images, and FreeCAD's
normal feature tree, properties, and undo history.

FreeCAD remains the CAD system. The agent submits Python through one checked `freecad`
tool; document edits recompute and validate inside transactions that commit or roll back.
A successful recompute does not prove that the design meets your requirements.

Your existing agent authentication, configuration, models, skills, and tools remain in use.
One provider works with the active document at a time; no separate general-purpose agent
harness or project/worktree manager is added.

</details>

<details open>
<summary><strong>build and run</strong></summary>

Install Nix with flakes enabled, then from this repository:

```sh
nix develop
just setup --fix
just build
just run
```

`just setup` alone checks readiness without changing anything. `--fix` fetches missing
source/submodules and applies patches; it does not overwrite dirty work.
`just build` prepares CMake automatically and compiles incrementally.
`just run` launches without building. Install Codex, OpenCode, or Hermes separately and select it
in the sidebar.

- **Linux:** Nix supplies native dependencies and tooling. Packaged `nix build` and
  `nix run` are also supported. Development output is in `build/native`.
- **macOS:** Nix supplies tooling, Rust, and Pixi. FreeCAD's pinned Pixi environment
  supplies Qt, Python, and CAD libraries—not Homebrew. Dependencies normally arrive as
  binary packages; Anthracite itself is compiled into `build/src/build/debug`.

Run `just test` for Rust tests through cargo-nextest and isolated FreeCAD GUI, executor,
and provider-bridge tests. These use deterministic providers, not real model calls.
Failure logs remain in `build/test-results/`.

</details>

<details>
<summary><strong>local data and customization</strong></summary>

The launcher uses XDG paths, with their standard defaults when unset:

- `$XDG_CONFIG_HOME/anthracite`: preferences, dock layout, optional `qml/Main.qml` override
- `$XDG_DATA_HOME/anthracite`: FreeCAD user data
- `$XDG_STATE_HOME/anthracite`: `anthracite.sqlite3`, attachment snapshots, checkpoints,
  and the readable `anthracite.events.jsonl` projection
- `$XDG_CACHE_HOME/anthracite`: temporary data

SQLite stores sessions and the operation journal; JSONL is for inspection, not recovery.
Session associations stay outside your `.FCStd` files.

To try local sidebar changes, copy the bundled `Main.qml` to the config override above,
then run `Gui.runCommand("Anthracite_ReloadSidebar")` in FreeCAD's Python console.
Reload preserves the provider session; invalid QML leaves the previous view intact.
Remove the override and reload to return to the bundled UI. Native/Rust changes need a rebuild.

</details>

<details>
<summary><strong>contributing</strong></summary>

Anthracite is a patch-stack soft fork of [FreeCAD](https://github.com/FreeCAD/FreeCAD).
[freecad_commit.txt](freecad_commit.txt) pins upstream; [patches/series](patches/series)
orders semantic feature patches. FreeCAD is materialized in ignored `build/src`, not vendored
or tracked as a submodule.

Read [AGENTS.md](AGENTS.md) for design intent, implementation boundaries, reference repos,
and the patch-editing workflow. Amend the owning feature patch; do not leave changes only
in `build/src`. API details belong with the executor's discoverable guidance and tests,
not a second manual here.

</details>

<details>
<summary><strong>license</strong></summary>

Same as FreeCAD: [GNU Lesser General Public License v2.1 or later](LICENSE).

</details>
