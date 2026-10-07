# SPDX-License-Identifier: LGPL-2.1-or-later
const root = path self | path dirname | path dirname

def main [] {
    use std/assert
    let directory = mktemp -d -t anthracite-install.XXXXXX
    # Supply only a subprocess's HOME; never alter the current shell's home.
    let installer = $root | path join install.nu
    for attempt in [1 2] {
        let result = with-env {HOME: $directory} {
            ^nu --no-config-file $installer | complete
        }
        assert equal $result.exit_code 0 $result.stderr
    }
    let addon = $directory | path join .local share FreeCAD Mod Anthracite
    assert ($addon | path join package.xml | path exists)
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
    let collision = with-env {HOME: $directory} {
        ^nu --no-config-file $installer | complete
    }
    assert ($collision.exit_code != 0)
    assert equal (open --raw $cli) 'unrelated executable'
    rm -r $directory
    print 'Addon installation tests passed.'
}
