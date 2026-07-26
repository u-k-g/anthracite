# Upstream and patch workflow

## Repository model

Anthracite follows Helium's relationship with Chromium:

- upstream source is not committed here and is not a Git submodule;
- one file pins the exact upstream revision;
- a setup command materializes a disposable upstream tree in `build/src`;
- project changes live in GNU Quilt patches under `patches/`;
- `patches/series` is the authoritative application order.

Helium's separate macOS/Linux/Windows wrappers do track its shared Helium core
repository as a submodule. That extra layer is not applicable here: Anthracite
is currently one repository, so FreeCAD corresponds to Chromium, not to
Helium's shared core repository.

The tracked inputs are:

```text
freecad_repo.txt       upstream Git URL
freecad_commit.txt     exact upstream commit
patches/series         ordered patch manifest
patches/**/*.patch     all Anthracite changes to FreeCAD
```

Everything under `build/` is ignored and reproducible.

## Enter the development environment

Quilt is the only extra patch-management dependency. With Nix:

```sh
nix-shell
```

Alternatively, install `quilt`, `git`, and `just` using the system package
manager.

## First setup

```sh
just doctor
just setup
```

`just setup` clones FreeCAD into `build/src` and checks out the commit in
`freecad_commit.txt` with a detached HEAD. If the sibling checkout
`../FreeCAD` exists, Git uses it as an object reference to make the clone much
faster while retaining the canonical upstream URL as `origin`.

An explicit reference checkout can also be supplied:

```sh
just setup /path/to/FreeCAD
```

The setup command never resets a dirty or patched source tree.

## Apply and inspect the patch stack

```sh
just push             # apply every patch
just pop              # remove every applied patch
just next              # apply one patch
just previous          # remove one patch
just status            # show pin, source, and Quilt state
just validate          # apply the full series in an isolated temporary clone
```

`just validate` does not disturb `build/src`.

## Create a patch

Quilt records changes in the current topmost patch. The normal loop is:

```sh
just push
just patch-new 0001-short-description
just patch-add src/path/to/existing-file.cpp

# Edit and test build/src/src/path/to/existing-file.cpp normally.

just patch-diff
just patch-refresh
just validate
```

`patch-new` adds `.patch` when it is omitted and appends the patch to
`patches/series`. Run `patch-add` once for each file before editing it. For a
new upstream file, call `patch-add` while the path does not yet exist, then
create and edit the file.

To work directly with Quilt, load the same environment used by the recipes:

```sh
source devutils/quilt-env.sh
cd build/src
quilt series
```

Do not edit generated patch hunks by hand during normal development. Modify
the materialized FreeCAD source, then run `just patch-refresh`.

## Move to a newer FreeCAD revision

Version bumps remain deliberate:

1. Pop the current series and ensure the source tree is clean.
2. Change `freecad_commit.txt` to the chosen full commit hash.
3. Materialize a fresh `build/src` at that commit.
4. Run `just push`; when a patch fails, repair it in order with Quilt and
   refresh it.
5. Build and test FreeCAD.
6. Run `just validate`.
7. Commit the new pin and refreshed patch files together.

There is intentionally no automatic "make conflicts go away" command. A base
bump is the point where each patch is reviewed against upstream changes.
