#!/usr/bin/env nu
# SPDX-License-Identifier: LGPL-2.1-or-later
const root = path self | path dirname

def main [addon: string = "", --addon-only] {
    let destination = if $addon == "" {
        $env.HOME | path join .local share FreeCAD Mod Anthracite
    } else {
        $addon | path expand
    }
    if $destination == $root {
        error make {msg: "Choose an installation directory outside this checkout."}
    }
    mkdir $destination
    for file in [Init.py InitGui.py AnthraciteExecutor.py AnthraciteInspect.py AnthraciteHistory.py AnthraciteBridge.py TestAnthracite.py anthracite LICENSE README.md justfile install.nu package.xml] {
        cp -f ($root | path join $file) ($destination | path join $file)
    }
    cp -r -f ($root | path join 'Anthracite Dark') $destination
    mkdir ($destination | path join tests) ($destination | path join .agents skills anthracite)
    for file in (glob ($root | path join tests '*.py') | append (glob ($root | path join tests '*.nu'))) {
        cp -f $file ($destination | path join tests ($file | path basename))
    }
    cp -f ($root | path join .agents skills anthracite SKILL.md) ($destination | path join .agents skills anthracite SKILL.md)
    ^chmod +x ($destination | path join anthracite)
    print $"Addon installed at ($destination)"
    if $addon_only { return }

    let bin = $env.HOME | path join .local bin
    let cli = $bin | path join anthracite
    let bundled = $destination | path join anthracite
    mkdir $bin
    if ($cli | path exists) or ($cli | path type) == symlink {
        if ($cli | path type) != symlink or ($cli | path expand) != ($bundled | path expand) {
            error make {msg: $"Existing CLI at ($cli) belongs to another installation. Move it aside before installing."}
        }
    } else {
        ^ln -s $bundled $cli
    }
    let skill = $env.HOME | path join .agents skills anthracite
    mkdir $skill
    cp -f ($root | path join .agents skills anthracite SKILL.md) ($skill | path join SKILL.md)
    print $"CLI: ($cli). Skill: ($skill). Add ($bin) to PATH and restart FreeCAD."
}
