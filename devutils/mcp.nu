#!/usr/bin/env nu
# SPDX-License-Identifier: LGPL-2.1-or-later
# Register the Anthracite MCP server with an agent installed on this machine.
#
# Prefers each agent's own `mcp add` so the client-specific config format stays
# correct. When an agent is not on PATH, the exact command is printed instead of
# guessing at that client's config file.
const root = path self | path dirname | path dirname
const name = "anthracite"
const agents = ["codex" "opencode"]

def main [agent: string = "", --server: string = ""] {
    let targets = if $agent == "" { $agents } else { [$agent] }
    for target in $targets {
        if not ($agents | any { |known| $known == $target }) {
            error make {msg: $"Unknown agent '($target)'. Use one of: ($agents | str join ', ')."}
        }
    }

    let candidates = if $server != "" {
        [$server | path expand]
    } else {
        [
            ($root | path join build src build debug Mod Anthracite anthracite-mcp)
            ($root | path join build native Mod Anthracite anthracite-mcp)
            ($root | path join build src src Mod Anthracite anthracite-mcp)
        ]
    }
    let existing = $candidates | where { |candidate| $candidate | path exists }
    if ($existing | is-empty) {
        error make {msg: $"No anthracite-mcp found. Looked in: ($candidates | str join ', '). Run `just build` first, or pass --server <path>."}
    }
    let server = $existing | first
    print $"MCP server: python3 ($server)\n"

    let results = $targets | each { |target| register $target $server }
    if ($results | any { |ok| not $ok }) {
        error make {msg: "One or more agents could not be registered."}
    }
    print "FreeCAD must be running for the agent to connect."
}

def register [agent: string, server: string] {
    let command = $"python3 ($server)"
    if (which $agent | is-empty) {
        print $"($agent) is not on PATH. In a shell where it is, run:"
        print $"    ($agent) mcp add ($name) -- ($command)\n"
        return true
    }
    run-external $agent "mcp" "remove" $name | complete | ignore
    let added = run-external $agent "mcp" "add" $name "--" "python3" $server | complete
    if $added.exit_code == 0 {
        print $"($agent): registered '($name)'. Verify with `($agent) mcp list`."
        return true
    }
    let output = $added.stdout + $added.stderr
    print $"($agent): registration failed\n($output)"
    if ($output | str contains "EACCES") or ($output | str contains "permission denied") {
        print "  The config file is not writable. If it is a Nix store symlink or a read-only"
        print "  copy, make it writable (`chmod u+w <config>`) or add the mcp block declaratively."
    }
    return false
}
