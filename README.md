<h1 align="center">anthracite</h1>

<p align="center">
  an llm-native freecad fork where coding agents work through freecad's python api.
</p>

<details open>
<summary><strong>overview</strong></summary>

Anthracite exposes FreeCAD to any coding agent on your machine through one command-line
program, `anthracite`. The agent runs outside FreeCAD—your existing Codex, OpenCode, Claude
Code, or anything that can run a command—and reaches Anthracite over a local bridge. Ask it
to inspect or change the active document; the Python it runs executes inside FreeCAD as one
native transaction that recomputes, validates, and then commits or rolls back, printing a
structured observation as JSON with viewport images written to disk.

Follow the work in FreeCAD's console and the `anthracite log` history: each call records the
code, the observation, diagnostics, and the viewport images the executor returned. FreeCAD
remains the CAD system. A successful recompute does not prove that the design meets your
requirements.

Your existing agent authentication, configuration, models, skills, and tools remain in
use. One agent operates on the active FreeCAD document at a time; no separate
general-purpose harness or project manager is added.

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
`just run` launches without building, and first mirrors this repository's agent skill to
`~/.agents/skills/anthracite` (also available as `just skill-sync`). `just connect` installs the
`anthracite` CLI on your PATH and syncs the skill, so an agent can run `anthracite exec '<python>'`;
the status bar shows the exact command. The eyedropper button in the status bar, or
`Ctrl+Shift+E`, copies a model reference to the clipboard.

- **Linux:** Nix supplies native dependencies and tooling. Packaged `nix build` and
  `nix run` are also supported. Development output is in `build/native`.
- **macOS:** Nix supplies tooling and Pixi. FreeCAD's pinned Pixi environment
  supplies Qt, Python, and CAD libraries—not Homebrew. Dependencies normally arrive as
  binary packages; Anthracite itself is compiled into `build/src/build/debug`.
  If the default Xcode SDK is newer than the pinned clang/libc++ support, point the build at
  an older SDK, e.g. `ANTHRACITE_OSX_SYSROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk just build`.

Run `just test` for isolated FreeCAD GUI, executor, and bridge tests. These use
deterministic inputs, not real model calls. Failure logs remain in `build/test-results/`.

</details>

<details>
<summary><strong>local data and customization</strong></summary>

The launcher uses XDG paths, with their standard defaults when unset:

- `$XDG_CONFIG_HOME/anthracite`: preferences
- `$XDG_DATA_HOME/anthracite`: FreeCAD user data
- `$XDG_STATE_HOME/.anthracite`: `bridge.json` (the running bridge's connection details),
  the readable `operations.ndjson` history of calls, and `shots/` (viewport PNGs the CLI writes)
- `$XDG_CACHE_HOME/anthracite`: temporary data

Session associations stay outside your `.FCStd` files. The running bridge rewrites `bridge.json`
on every launch; `anthracite log` reads the `operations.ndjson` history.

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
