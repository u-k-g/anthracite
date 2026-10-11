# SPDX-License-Identifier: LGPL-2.1-or-later
const root = path self | path dirname | path dirname

def main [] {
    use std/assert
    let directory = mktemp -d -t anthracite-install.XXXXXX
    let data = $directory | path join FreeCAD v1-1
    mkdir $data
    let command = $directory | path join freecadcmd
    '#!/usr/bin/env python3
import os, sys
assert sys.argv[1] == "--get-config"
if sys.argv[2] == "BuildVersionMajor":
    print(os.environ.get("ANTHRACITE_TEST_MAJOR", "1"))
elif sys.argv[2] == "UserAppData":
    print(os.environ["ANTHRACITE_TEST_DATA"])
else:
    sys.exit(1)
' | save $command
    ^chmod +x $command
    # Supply only a subprocess's HOME; never alter the current shell's home.
    let installer = $root | path join install.nu
    let literal = with-env {HOME: $directory} {
        ^nu --no-config-file $installer '$HOME/Library/FreeCAD/Mod/Anthracite' | complete
    }
    assert ($literal.exit_code != 0)
    assert ($literal.stderr | str contains 'unexpanded home variable')
    for attempt in [1 2] {
        let result = with-env {HOME: $directory, FREECAD_CMD: $command, ANTHRACITE_TEST_DATA: $data} {
            ^nu --no-config-file $installer | complete
        }
        assert equal $result.exit_code 0 $result.stderr
    }
    let addon = $data | path join Mod Anthracite
    assert ($addon | path join package.xml | path exists)
    assert ($addon | path join AnthraciteUi.py | path exists)
    assert ($addon | path join AnthraciteIR.py | path exists)
    assert ($addon | path join AnthraciteCompare.py | path exists)
    assert ($addon | path join AnthraciteAuthor.py | path exists)
    assert ($addon | path join AnthraciteEvaluate.py | path exists)
    assert ($addon | path join AnthraciteEvaluateWorker.py | path exists)
    assert ($addon | path join Resources icons color-picker.svg | path exists)
    assert ($addon | path join Resources icons LICENSE-Iconoir | path exists)
    assert ($addon | path join Resources ui.qss | path exists)
    assert ($addon | path join 'Anthracite Dark' 'Anthracite Dark.cfg' | path exists)
    assert ($addon | path join 'Anthracite Dark' parameters 'Anthracite Dark.yaml' | path exists)
    assert ($addon | path join 'Anthracite Dark' overlay 'Anthracite Dark.qss' | path exists)
    let cli = $directory | path join .local bin anthracite
    assert equal ($cli | path type) symlink
    assert equal ($cli | path expand) ($addon | path join anthracite | path expand)
    let help = run-external $cli '--help' | complete
    assert equal $help.exit_code 0 $help.stderr
    assert ($help.stdout | str contains '{exec,status,shots,log}')
    assert equal (open --raw ($directory | path join .agents skills anthracite SKILL.md)) (open --raw ($root | path join .agents skills anthracite SKILL.md))
    rm $cli
    'unrelated executable' | save $cli
    let collision = with-env {HOME: $directory, FREECAD_CMD: $command, ANTHRACITE_TEST_DATA: $data} {
        ^nu --no-config-file $installer | complete
    }
    assert ($collision.exit_code != 0)
    assert equal (open --raw $cli) 'unrelated executable'
    let old = with-env {HOME: $directory, FREECAD_CMD: $command, ANTHRACITE_TEST_MAJOR: '0', ANTHRACITE_TEST_DATA: $data} {
        ^nu --no-config-file $installer | complete
    }
    assert ($old.exit_code != 0)
    assert ($old.stderr | str contains 'too old')
    let invalid = with-env {HOME: $directory, FREECAD_CMD: $command, ANTHRACITE_TEST_DATA: ''} {
        ^nu --no-config-file $installer | complete
    }
    assert ($invalid.exit_code != 0)
    assert ($invalid.stderr | str contains 'Could not determine the FreeCAD user-data directory')
    let explicit = $directory | path join explicit Mod Anthracite
    let override = with-env {HOME: $directory, FREECAD_CMD: '/does/not/exist'} {
        ^nu --no-config-file $installer $explicit --addon-only | complete
    }
    assert equal $override.exit_code 0 $override.stderr
    assert ($explicit | path join InitGui.py | path exists)
    rm -r $directory
    print 'Addon installation tests passed.'
}
