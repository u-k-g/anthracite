# SPDX-License-Identifier: LGPL-2.1-or-later
def main [launcher: path, probe: path] {
    use std/assert
    let directory = mktemp -d -t anthracite-launch.XXXXXX
    $env.XDG_CONFIG_HOME = $directory | path join 'config with spaces'
    $env.XDG_DATA_HOME = $directory | path join data
    $env.XDG_STATE_HOME = $directory | path join state
    $env.XDG_CACHE_HOME = $directory | path join cache
    let first = ^nu --no-config-file $launcher $probe 'model with spaces.FCStd' | complete
    assert equal $first.exit_code 0 $first.stderr
    let preferences = $env.XDG_CONFIG_HOME | path join anthracite user.cfg
    'user layout' | save $preferences
    let second = ^nu --no-config-file $launcher $probe 'model with spaces.FCStd' | complete
    assert equal $second.exit_code 0 $second.stderr
    assert equal (open --raw $preferences) 'user layout'
    let invalid = with-env {XDG_CONFIG_HOME: relative} { ^nu --no-config-file $launcher $probe | complete }
    assert ($invalid.exit_code != 0)
    rm -r $directory
    print 'Launcher tests passed.'
}
