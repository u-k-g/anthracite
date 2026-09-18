#!/usr/bin/env nu
# SPDX-License-Identifier: LGPL-2.1-or-later
const root = path self | path dirname | path dirname

def main [] {
    let setup = ^nu --no-config-file ($root | path join tests setup.nu) | complete
    print $setup.stdout
    if $setup.exit_code != 0 { error make {msg: $setup.stderr} }
    let results = $root | path join build test-results
    mkdir $results
    let directory = mktemp -d -p $results test.XXXXXX
    $env.XDG_CONFIG_HOME = $directory | path join config
    $env.XDG_DATA_HOME = $directory | path join data
    $env.XDG_STATE_HOME = $directory | path join state
    $env.XDG_CACHE_HOME = $directory | path join cache
    $env.ANTHRACITE_SMOKE = "1"
    let build = if (sys host | get name) == "Darwin" { $root | path join build src build debug } else { $root | path join build native }
    $env.ANTHRACITE_TEST_LAUNCHER = $root | path join devutils launch.nu
    $env.ANTHRACITE_TEST_NU = $nu.current-exe
    for test in [[script executable marker]; [executor.py FreeCADCmd ANTHRACITE_EXECUTOR_TESTS_OK] [smoke.py FreeCAD ANTHRACITE_GUI_SMOKE_OK] [bridge-smoke.py FreeCAD ANTHRACITE_BRIDGE_SMOKE_OK]] {
        let result = ^nu --no-config-file ($root | path join devutils native.nu) exec $nu.current-exe --no-config-file ($root | path join devutils launch.nu) ($build | path join bin $test.executable) ($root | path join tests $test.script) | complete
        let output = $result.stdout + $result.stderr
        $output | save ($directory | path join $"($test.script).log")
        print $output
        if $result.exit_code != 0 or not ($output | str contains $test.marker) or ($output =~ 'TypeError:|QProcess: Destroyed while process|The current style does not support customization') {
            error make {msg: $"($test.script) failed \(exit ($result.exit_code)\). Logs and isolated profile retained at ($directory)"}
        }
    }
    rm -r $directory
    print "All FreeCAD integration tests passed."
}
