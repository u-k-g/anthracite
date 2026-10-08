#!/usr/bin/env nu
# SPDX-License-Identifier: LGPL-2.1-or-later
const root = path self | path dirname | path dirname

def main [] {
    ^nu --no-config-file ($root | path join tests install.nu)
    let executable = $env.FREECAD_BIN? | default (if (sys host | get name) == Darwin {
        '/Applications/FreeCAD.app/Contents/Resources/bin/freecad'
    } else { 'FreeCAD' })
    let results = $root | path join test-results
    mkdir $results
    let directory = mktemp -d -p $results test.XXXXXX
    $env.XDG_CONFIG_HOME = $directory | path join config
    $env.XDG_DATA_HOME = $directory | path join data
    $env.XDG_STATE_HOME = $directory | path join state
    $env.XDG_CACHE_HOME = $directory | path join cache
    $env.FREECAD_USER_HOME = $env.XDG_CONFIG_HOME | path join FreeCAD
    $env.FREECAD_USER_DATA = $env.XDG_DATA_HOME | path join FreeCAD
    $env.FREECAD_USER_TEMP = $env.XDG_CACHE_HOME | path join FreeCAD
    $env.ANTHRACITE_SMOKE = '1'
    $env.ANTHRACITE_TEST_ADDON = $env.FREECAD_USER_DATA | path join Mod Anthracite
    mkdir $env.FREECAD_USER_HOME $env.FREECAD_USER_TEMP
    '<FCParameters><FCParamGroup Name="Root"><FCParamGroup Name="BaseApp"><FCParamGroup Name="Preferences"><FCParamGroup Name="AnthraciteTest"><FCText Name="Sentinel">keep</FCText></FCParamGroup><FCParamGroup Name="RecentFiles"><FCText Name="MRU0">keep.FCStd</FCText></FCParamGroup><FCParamGroup Name="NotificationArea"><FCBool Name="NotificationAreaEnabled" Value="0" /></FCParamGroup></FCParamGroup></FCParamGroup></FCParamGroup></FCParameters>' | save ($env.FREECAD_USER_HOME | path join user.cfg)
    ^nu --no-config-file ($root | path join install.nu) $env.ANTHRACITE_TEST_ADDON --addon-only
    for test in [[script marker]; [preferences-smoke.py ANTHRACITE_PREFERENCES_SMOKE_OK] [preferences-restart.py ANTHRACITE_PREFERENCES_RESTART_OK] [ui-smoke.py ANTHRACITE_UI_SMOKE_OK] [smoke.py ANTHRACITE_GUI_SMOKE_OK] [bridge-smoke.py ANTHRACITE_BRIDGE_SMOKE_OK]] {
        let result = run-external $executable '--user-cfg' ($env.FREECAD_USER_HOME | path join user.cfg) '--system-cfg' ($env.FREECAD_USER_HOME | path join system.cfg) '--log-file' ($directory | path join $"($test.script).FreeCAD.log") ($root | path join tests $test.script) | complete
        let output = $result.stdout + $result.stderr
        $output | save ($directory | path join $"($test.script).log")
        print $output
        if $result.exit_code != 0 or not ($output | str contains $test.marker) {
            error make {msg: $"($test.script) failed (exit ($result.exit_code)). Logs and isolated profile retained at ($directory)"}
        }
    }
    rm -r $directory
    print 'All stock FreeCAD integration tests passed.'
}
