set shell := ["bash", "-euo", "pipefail", "-c"]

root := justfile_directory()
source_dir := root / "build/src"
patch_dir := root / "patches"
series_file := patch_dir / "series"
pin_file := root / "freecad_commit.txt"
repo_file := root / "freecad_repo.txt"
profile_dir := root / "build/profile"

# List the available workflow commands.
default:
    @just --list

# Configure the pinned FreeCAD source for a debug development build.
configure: _require-source
    cd "{{ source_dir }}" && pixi run configure-debug

# Build the complete FreeCAD application and every configured workbench.
build: _require-source
    #!/usr/bin/env bash
    set -euo pipefail
    build_directory="{{ source_dir }}/build/debug"
    cmake="{{ source_dir }}/.pixi/envs/default/bin/cmake"
    if [[ ! -f "$build_directory/CMakeCache.txt" || ! -x "$cmake" ]]; then
        printf '%s\n' "Anthracite is not configured; run \`just configure\` first." >&2
        exit 1
    fi
    "$cmake" --build "$build_directory" --parallel "${JOBS:-10}"

# Build and launch full FreeCAD with a writable, persistent Anthracite profile.
run: build
    #!/usr/bin/env bash
    set -euo pipefail
    executable="{{ source_dir }}/build/debug/bin/FreeCAD"
    if [[ ! -x "$executable" ]]; then
        printf '%s\n' "The complete Anthracite build did not produce FreeCAD." >&2
        exit 1
    fi
    mkdir -p "{{ profile_dir }}"
    export FREECAD_USER_HOME="{{ profile_dir }}"
    export QSG_RHI_BACKEND="opengl"
    exec "$executable"

# Check that the local machine has the workflow dependencies.
doctor:
    #!/usr/bin/env bash
    set -euo pipefail
    failed=0
    for command_name in git just pixi quilt; do
        if command -v "$command_name" >/dev/null 2>&1; then
            printf '%-8s %s\n' "$command_name" "$(command -v "$command_name")"
        else
            printf '%-8s %s\n' "$command_name" "missing"
            failed=1
        fi
    done
    printf '%-8s %s\n' "pin" "$(tr -d '[:space:]' < "{{ pin_file }}")"
    if (( failed != 0 )); then
        printf '\nEnter `nix-shell` or install the missing commands.\n' >&2
        exit 1
    fi

# Materialize pinned FreeCAD in build/src; optionally use a local Git object reference.
setup reference="":
    #!/usr/bin/env bash
    set -euo pipefail
    upstream_url="$(tr -d '[:space:]' < "{{ repo_file }}")"
    upstream_commit="$(tr -d '[:space:]' < "{{ pin_file }}")"
    checkout_reference={{ quote(reference) }}

    if [[ ! "$upstream_commit" =~ ^[0-9a-f]{40}$ ]]; then
        printf 'Invalid full commit hash in %s\n' "{{ pin_file }}" >&2
        exit 1
    fi

    if [[ -d "{{ source_dir }}/.git" ]]; then
        current_commit="$(git -C "{{ source_dir }}" rev-parse HEAD)"
        if [[ "$current_commit" != "$upstream_commit" ]]; then
            printf '%s\n' "build/src already exists at $current_commit."
            printf '%s\n' "Pinned FreeCAD is $upstream_commit."
            printf '%s\n' "Move the existing source tree aside after preserving any patch work, then rerun setup." >&2
            exit 1
        fi
        printf '%s\n' "FreeCAD is already materialized at the pinned commit."
        exit 0
    fi

    if [[ -e "{{ source_dir }}" ]]; then
        printf '%s\n' "{{ source_dir }} exists but is not a Git checkout; refusing to overwrite it." >&2
        exit 1
    fi

    if [[ -z "$checkout_reference" && -d "{{ root }}/../FreeCAD/.git" ]]; then
        checkout_reference="{{ root }}/../FreeCAD"
    fi

    mkdir -p "{{ root }}/build"
    clone_arguments=(--no-checkout)
    if [[ -n "$checkout_reference" ]]; then
        if [[ ! -d "$checkout_reference/.git" ]]; then
            printf 'Reference checkout is not a Git worktree: %s\n' "$checkout_reference" >&2
            exit 1
        fi
        clone_arguments+=(--reference-if-able "$checkout_reference")
        printf 'Using local object reference: %s\n' "$checkout_reference"
    fi

    git clone "${clone_arguments[@]}" "$upstream_url" "{{ source_dir }}"
    git -C "{{ source_dir }}" checkout --detach "$upstream_commit"
    printf 'Materialized FreeCAD %s in %s\n' "$upstream_commit" "{{ source_dir }}"

# Show the pinned upstream, source-tree state, and Quilt stack.
status:
    #!/usr/bin/env bash
    set -euo pipefail
    printf 'Upstream: %s\n' "$(tr -d '[:space:]' < "{{ repo_file }}")"
    printf 'Pinned:   %s\n' "$(tr -d '[:space:]' < "{{ pin_file }}")"
    if [[ ! -d "{{ source_dir }}/.git" ]]; then
        printf 'Source:   not materialized (run `just setup`)\n'
        exit 0
    fi
    printf 'Source:   %s\n' "$(git -C "{{ source_dir }}" rev-parse HEAD)"
    git -C "{{ source_dir }}" status --short
    if command -v quilt >/dev/null 2>&1; then
        source "{{ root }}/devutils/quilt-env.sh"
        cd "{{ source_dir }}"
        printf '\nApplied patches:\n'
        quilt applied 2>/dev/null || printf '%s\n' "(none)"
        printf '\nUnapplied patches:\n'
        quilt unapplied 2>/dev/null || printf '%s\n' "(none)"
    else
        printf '\nQuilt is missing; enter `nix-shell` for patch-stack status.\n'
    fi

# Apply every currently unapplied patch.
push: _require-source _require-quilt
    #!/usr/bin/env bash
    set -euo pipefail
    source "{{ root }}/devutils/quilt-env.sh"
    cd "{{ source_dir }}"
    last_patch="$(awk '!/^[[:space:]]*(#|$)/ { print $1 }' "{{ series_file }}" | tail -n 1)"
    if [[ -z "$last_patch" ]]; then
        printf '%s\n' "The patch series is empty."
    elif [[ "$(quilt top 2>/dev/null || true)" == "$last_patch" ]]; then
        printf '%s\n' "All patches are already applied."
    else
        quilt push -a
    fi

# Remove every currently applied patch.
pop: _require-source _require-quilt
    #!/usr/bin/env bash
    set -euo pipefail
    source "{{ root }}/devutils/quilt-env.sh"
    cd "{{ source_dir }}"
    if quilt top >/dev/null 2>&1; then
        quilt pop -a
    else
        printf '%s\n' "No patches are applied."
    fi

# Apply the next patch in the series.
next: _require-source _require-quilt
    #!/usr/bin/env bash
    set -euo pipefail
    source "{{ root }}/devutils/quilt-env.sh"
    cd "{{ source_dir }}"
    if quilt next >/dev/null 2>&1; then
        quilt push
    else
        printf '%s\n' "No patch remains to apply."
    fi

# Remove the current topmost patch.
previous: _require-source _require-quilt
    #!/usr/bin/env bash
    set -euo pipefail
    source "{{ root }}/devutils/quilt-env.sh"
    cd "{{ source_dir }}"
    if quilt top >/dev/null 2>&1; then
        quilt pop
    else
        printf '%s\n' "No patches are applied."
    fi

# Create and append a new topmost patch (the .patch suffix is optional).
patch-new name: _require-source _require-quilt
    #!/usr/bin/env bash
    set -euo pipefail
    source "{{ root }}/devutils/quilt-env.sh"
    cd "{{ source_dir }}"
    patch_name={{ quote(name) }}
    if [[ "$patch_name" != *.patch ]]; then
        patch_name="${patch_name}.patch"
    fi
    if [[ "$patch_name" == /* || "$patch_name" == *..* || ! "$patch_name" =~ ^[A-Za-z][A-Za-z0-9._/-]*\.patch$ ]]; then
        printf 'Invalid patch name: %s\n' "$patch_name" >&2
        exit 1
    fi
    last_patch="$(awk '!/^[[:space:]]*(#|$)/ { print $1 }' "{{ series_file }}" | tail -n 1)"
    if [[ -n "$last_patch" && "$(quilt top 2>/dev/null || true)" != "$last_patch" ]]; then
        printf '%s\n' "Apply the existing series with \`just push\` before creating a patch." >&2
        exit 1
    fi
    mkdir -p "$(dirname "{{ patch_dir }}/$patch_name")"
    quilt new "$patch_name"

# Pop the stack and apply through one existing semantic patch so it can be amended.
patch-edit name: _require-source _require-quilt
    #!/usr/bin/env bash
    set -euo pipefail
    source "{{ root }}/devutils/quilt-env.sh"
    cd "{{ source_dir }}"
    patch_name={{ quote(name) }}
    if [[ "$patch_name" != *.patch ]]; then
        patch_name="${patch_name}.patch"
    fi
    if ! awk -v target="$patch_name" \
        '!/^[[:space:]]*(#|$)/ && $1 == target { found = 1 } END { exit !found }' \
        "{{ series_file }}"; then
        printf 'Patch is not in the series: %s\n' "$patch_name" >&2
        exit 1
    fi
    if [[ "$(quilt top 2>/dev/null || true)" == "$patch_name" ]]; then
        printf 'Patch is already current: %s\n' "$patch_name"
        exit 0
    fi
    if quilt top >/dev/null 2>&1; then
        quilt pop -a
    fi
    quilt push "$patch_name"

# Add one source path to the current topmost patch before editing it.
patch-add path: _require-source _require-quilt
    #!/usr/bin/env bash
    set -euo pipefail
    source "{{ root }}/devutils/quilt-env.sh"
    cd "{{ source_dir }}"
    if ! quilt top >/dev/null 2>&1; then
        printf '%s\n' "There is no current patch; run \`just patch-new <name>\` first." >&2
        exit 1
    fi
    source_path={{ quote(path) }}
    if [[ "$source_path" == /* || "$source_path" == .. || "$source_path" == ../* || "$source_path" == */../* ]]; then
        printf 'Path must be relative to build/src: %s\n' "$source_path" >&2
        exit 1
    fi
    if quilt files |
        awk -v target="$source_path" '$0 == target { found = 1 } END { exit !found }'; then
        printf 'Path is already owned by %s: %s\n' "$(quilt top)" "$source_path"
        exit 0
    fi
    quilt add "$source_path"

# Show the unrefreshed changes in the current patch.
patch-diff: _require-source _require-quilt
    #!/usr/bin/env bash
    set -euo pipefail
    source "{{ root }}/devutils/quilt-env.sh"
    cd "{{ source_dir }}"
    quilt diff

# Refresh the current patch from changes made in build/src.
patch-refresh: _require-source _require-quilt
    #!/usr/bin/env bash
    set -euo pipefail
    source "{{ root }}/devutils/quilt-env.sh"
    cd "{{ source_dir }}"
    quilt refresh

# Check series integrity and apply every patch in an isolated temporary clone.
validate: validate-series _require-source
    #!/usr/bin/env bash
    set -euo pipefail
    validation_dir="$(mktemp -d "${TMPDIR:-/tmp}/anthracite-patches.XXXXXX")"
    trap 'rm -rf "$validation_dir"' EXIT
    upstream_commit="$(tr -d '[:space:]' < "{{ pin_file }}")"
    git clone --quiet --shared --no-checkout "{{ source_dir }}" "$validation_dir/source"
    git -C "$validation_dir/source" checkout --quiet --detach "$upstream_commit"

    patch_count=0
    while IFS= read -r patch_name; do
        git -C "$validation_dir/source" apply --check "{{ patch_dir }}/$patch_name"
        git -C "$validation_dir/source" apply "{{ patch_dir }}/$patch_name"
        patch_count=$((patch_count + 1))
    done < <(awk '!/^[[:space:]]*(#|$)/ { print $1 }' "{{ series_file }}")

    git -C "$validation_dir/source" diff --check
    printf 'Validated %d patch(es) against FreeCAD %s.\n' "$patch_count" "$upstream_commit"

# Check that series entries are safe, unique, present, and exhaustive.
validate-series:
    #!/usr/bin/env bash
    set -euo pipefail
    entries_file="$(mktemp "${TMPDIR:-/tmp}/anthracite-series.XXXXXX")"
    actual_file="$(mktemp "${TMPDIR:-/tmp}/anthracite-patches.XXXXXX")"
    trap 'rm -f "$entries_file" "$actual_file"' EXIT
    awk '!/^[[:space:]]*(#|$)/ { print $1 }' "{{ series_file }}" > "$entries_file"

    duplicate="$(sort "$entries_file" | uniq -d | head -n 1)"
    if [[ -n "$duplicate" ]]; then
        printf 'Duplicate series entry: %s\n' "$duplicate" >&2
        exit 1
    fi

    while IFS= read -r patch_name; do
        [[ -z "$patch_name" ]] && continue
        if [[ "$patch_name" == /* || "$patch_name" == .. || "$patch_name" == ../* || "$patch_name" == */../* ]]; then
            printf 'Unsafe series entry: %s\n' "$patch_name" >&2
            exit 1
        fi
        if [[ "$patch_name" != *.patch ]]; then
            printf 'Series entry is not a .patch file: %s\n' "$patch_name" >&2
            exit 1
        fi
        if [[ ! -f "{{ patch_dir }}/$patch_name" ]]; then
            printf 'Series entry does not exist: %s\n' "$patch_name" >&2
            exit 1
        fi
    done < "$entries_file"

    find "{{ patch_dir }}" -type f -name '*.patch' -print |
        sed 's#^{{ patch_dir }}/##' |
        sort > "$actual_file"
    orphan="$(comm -23 "$actual_file" <(sort "$entries_file") | head -n 1)"
    if [[ -n "$orphan" ]]; then
        printf 'Patch is missing from series: %s\n' "$orphan" >&2
        exit 1
    fi
    printf 'Series is valid (%s patch(es)).\n' "$(wc -l < "$entries_file" | tr -d '[:space:]')"

_require-source:
    #!/usr/bin/env bash
    if [[ ! -d "{{ source_dir }}/.git" ]]; then
        printf '%s\n' "FreeCAD is not materialized; run \`just setup\` first." >&2
        exit 1
    fi
    pinned_commit="$(tr -d '[:space:]' < "{{ pin_file }}")"
    source_commit="$(git -C "{{ source_dir }}" rev-parse HEAD)"
    if [[ "$source_commit" != "$pinned_commit" ]]; then
        printf 'build/src is at %s, but FreeCAD is pinned to %s.\n' "$source_commit" "$pinned_commit" >&2
        exit 1
    fi

_require-quilt:
    #!/usr/bin/env bash
    if ! command -v quilt >/dev/null 2>&1; then
        printf '%s\n' "Quilt is missing; enter \`nix-shell\` first." >&2
        exit 1
    fi
