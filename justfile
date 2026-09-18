set shell := ["nu", "--no-config-file", "--commands"]

root := justfile_directory()
source_dir := root / "build/src"
build_dir := if os() == "macos" { source_dir / "build/debug" } else { root / "build/native" }

default:
    @just --list

# Prepare the build settings automatically, then compile incrementally.
build: _require-source
    nix develop --command nu --no-config-file '{{root}}/devutils/native.nu' build '{{build_dir}}'

# Launch with persistent XDG preferences and sessions.
run: skill-sync
    nix develop --command nu --no-config-file '{{root}}/devutils/native.nu' exec nu --no-config-file '{{root}}/devutils/launch.nu' '{{build_dir}}/bin/FreeCAD'

# Mirror this repository's Anthracite skill onto ~/.agents/skills/anthracite.
skill-sync:
    @nu --no-config-file '{{root}}/devutils/skill.nu'

# Install the Anthracite CLI on PATH and mirror the agent skill.
connect: skill-sync
    @nu --no-config-file '{{root}}/devutils/cli.nu'

# Isolated FreeCAD GUI, executor, and bridge tests without real model calls.
test:
    nix develop --command nu --no-config-file '{{root}}/tests/runtests.nu'

# Read-only tool/source/patch readiness check; --fix enters Nix, fetches source, applies patches.
setup option="":
    @nu --no-config-file '{{root}}/devutils/workflow.nu' setup {{quote(option)}}

# Inspect source changes and applied/unapplied patches.
status:
    @nu --no-config-file '{{root}}/devutils/workflow.nu' status

# Apply every remaining patch.
patches-apply:
    @nu --no-config-file '{{root}}/devutils/workflow.nu' patches-apply

# Unapply all patches, leaving the upstream source.
patches-unapply:
    @nu --no-config-file '{{root}}/devutils/workflow.nu' patches-unapply

# Apply one more patch in series order.
patch-apply-next:
    @nu --no-config-file '{{root}}/devutils/workflow.nu' patch-apply-next

# Unapply the current top patch.
patch-unapply-last:
    @nu --no-config-file '{{root}}/devutils/workflow.nu' patch-unapply-last

# Create a feature patch only when no existing patch owns the work.
patch-new name:
    @nu --no-config-file '{{root}}/devutils/workflow.nu' patch-new {{quote(name)}}

# Select an existing patch to amend; temporarily unapply later patches.
patch-edit name:
    @nu --no-config-file '{{root}}/devutils/workflow.nu' patch-edit {{quote(name)}}

# Register a file with the selected patch BEFORE editing it (relative to build/src).
patch-add path:
    @nu --no-config-file '{{root}}/devutils/workflow.nu' patch-add {{quote(path)}}

# Inspect all changes owned by the selected patch.
patch-diff:
    @nu --no-config-file '{{root}}/devutils/workflow.nu' patch-diff

# Save source edits back into the selected .patch file.
patch-refresh:
    @nu --no-config-file '{{root}}/devutils/workflow.nu' patch-refresh

# Apply the full series to an isolated checkout; does not compile or run tests.
validate:
    @nu --no-config-file '{{root}}/devutils/workflow.nu' validate

# Check patch names, duplicates, missing files, and series coverage only.
validate-series:
    @nu --no-config-file '{{root}}/devutils/workflow.nu' validate-series

_require-source:
    @nu --no-config-file '{{root}}/devutils/workflow.nu' require-source
