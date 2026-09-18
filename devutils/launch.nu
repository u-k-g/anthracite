#!/usr/bin/env nu
# SPDX-License-Identifier: LGPL-2.1-or-later
def --wrapped main [executable: string, ...arguments: string] {
    let roots = {
        config: ($env.XDG_CONFIG_HOME? | default ($env.HOME | path join .config))
        data: ($env.XDG_DATA_HOME? | default ($env.HOME | path join .local share))
        state: ($env.XDG_STATE_HOME? | default ($env.HOME | path join .local state))
        cache: ($env.XDG_CACHE_HOME? | default ($env.HOME | path join .cache))
    } | transpose key value | update value { |row| $row.value | path join anthracite } | transpose -r -d
    for directory in ($roots | values) {
        if not ($directory | str starts-with /) {
            error make {msg: $"Anthracite requires absolute XDG paths: ($directory)"}
        }
    }
    for directory in ($roots | values) { mkdir $directory }
    $env.FREECAD_USER_HOME = $roots.config
    $env.FREECAD_USER_DATA = $roots.data
    $env.FREECAD_USER_TEMP = $roots.cache
    $env.QSG_RHI_BACKEND = "opengl"
    exec $executable --user-cfg ($roots.config | path join user.cfg) --system-cfg ($roots.config | path join system.cfg) ...$arguments
}
