#!/usr/bin/env nu
# SPDX-License-Identifier: LGPL-2.1-or-later
# Mirror this repository's Anthracite skill onto the machine's global skills directory.
#
# The destination is replaced entirely so removals and renames propagate. Override the
# base directory with ANTHRACITE_AGENTS_SKILLS or an explicit target argument.
const root = path self | path dirname | path dirname
const source = $root | path join .agents skills anthracite

def main [target: string = ""] {
    if not ($source | path exists) {
        error make {msg: $"Missing skill source: ($source)"}
    }
    let base = if $target != "" {
        $target
    } else {
        let from_env = $env.ANTHRACITE_AGENTS_SKILLS?
        if ($from_env | is-empty) { $env.HOME | path join .agents skills } else { $from_env }
    }
    let expanded = $base | path expand
    let destination = if ($expanded | path basename) == "anthracite" {
        $expanded
    } else {
        $expanded | path join anthracite
    }
    mkdir ($destination | path dirname)
    if ($destination | path exists) { rm --recursive --force $destination }
    cp --recursive $source $destination
    print $"Synced ($source) -> ($destination)"
}
