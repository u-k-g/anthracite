# Source this file to use Quilt against Anthracite's materialized FreeCAD tree.

if [ -n "${BASH_SOURCE[0]-}" ]; then
    _anthracite_quilt_script="${BASH_SOURCE[0]}"
elif [ -n "${ZSH_VERSION-}" ]; then
    _anthracite_quilt_script="${(%):-%x}"
else
    printf '%s\n' "quilt-env.sh must be sourced from Bash or Zsh" >&2
    return 1
fi

ANTHRACITE_ROOT="$(
    CDPATH= cd -- "$(dirname -- "$_anthracite_quilt_script")/.." &&
        pwd -P
)"
export ANTHRACITE_ROOT

export QUILT_PATCHES="$ANTHRACITE_ROOT/patches"
export QUILT_PUSH_ARGS="--color=auto"
export QUILT_DIFF_OPTS="--show-c-function"
export QUILT_PATCH_OPTS="--unified --reject-format=unified"
export QUILT_DIFF_ARGS="-p ab --no-timestamps --no-index --color=auto"
export QUILT_REFRESH_ARGS="-p ab --no-timestamps --no-index --strip-trailing-whitespace"
export QUILT_COLORS="diff_hdr=1;32:diff_add=1;34:diff_rem=1;31:diff_hunk=1;33:diff_ctx=35:diff_cctx=33"
export QUILT_SERIES_ARGS="--color=auto"
export QUILT_PATCHES_ARGS="--color=auto"
export LC_ALL=C

if [ -n "${LESS-}" ] && [ -z "${QUILT_PAGER+x}" ]; then
    export QUILT_PAGER="less -FRX"
fi

# Absolute QUILT_PATCHES paths behave consistently when Quilt ignores a
# machine-wide quiltrc. This mirrors Helium's development environment.
quilt() {
    command quilt --quiltrc - "$@"
}

unset _anthracite_quilt_script
