#!/usr/bin/env nu
# SPDX-License-Identifier: LGPL-2.1-or-later
# Install the Anthracite CLI on PATH so any agent on this machine can run it.
const root = path self | path dirname | path dirname

def main [--dir: string = ""] {
    let candidates = [
        ($root | path join build src build debug Mod Anthracite anthracite)
        ($root | path join build native Mod Anthracite anthracite)
        ($root | path join build src src Mod Anthracite anthracite)
    ]
    let existing = $candidates | where { |candidate| $candidate | path exists }
    if ($existing | is-empty) {
        error make {msg: $"No anthracite CLI found. Looked in: ($candidates | str join ', '). Run `just build` first, or pass --dir <directory>."}
    }
    let cli = $existing | first
    let bin = if $dir == "" { $env.HOME | path join .local bin } else { $dir | path expand }
    mkdir $bin
    let target = $bin | path join anthracite
    # A wrapper, not a symlink: the source file's executable bit is not tracked.
    $"#!/bin/sh\nexec python3 ($cli) \"$@\"\n" | save --force $target
    ^chmod +x $target
    print $"Installed ($target) -> ($cli)"
    if not ($env.PATH | any { |entry| $entry == $bin }) {
        print $"Note: ($bin) is not on PATH; add it so agents can run `anthracite`."
    }
}
