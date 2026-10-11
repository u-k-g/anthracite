#!/usr/bin/env nu
# SPDX-License-Identifier: LGPL-2.1-or-later
const root = path self | path dirname

def freecad-addon-directory [] {
    let configured = $env.FREECAD_CMD? | default ''
    let executable = if $configured != '' {
        $configured
    } else {
        let candidates = if (sys host | get name) == Darwin {
            ['/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd'
             ($env.HOME | path join Applications FreeCAD.app Contents Resources bin freecadcmd)]
        } else { [] }
        let commands = [FreeCADCmd freecadcmd FreeCAD freecad] | each {|name|
            which $name | get path
        } | flatten
        let available = $candidates | append $commands | where {|file| $file | path exists }
        if ($available | is-empty) {
            error make {msg: 'FreeCAD was not found. Install FreeCAD >= 1.0 with PySide6, or set FREECAD_CMD to its command-line executable.'}
        }
        $available | first
    }
    let major = run-external $executable '--get-config' 'BuildVersionMajor' | complete
    if $major.exit_code != 0 or not (($major.stdout | str trim) =~ '^\d+$') {
        error make {msg: $"Could not determine the FreeCAD version using ($executable). ($major.stderr | str trim)"}
    }
    if ($major.stdout | str trim | into int) < 1 {
        error make {msg: 'Anthracite requires FreeCAD >= 1.0 with PySide6. The detected FreeCAD is too old.'}
    }
    let data = run-external $executable '--get-config' 'UserAppData' | complete
    let directory = $data.stdout | str trim
    if $data.exit_code != 0 or $directory == '' or ($directory | path type) != dir {
        error make {msg: $"Could not determine the FreeCAD user-data directory using ($executable). Pass an explicit addon directory instead."}
    }
    $directory | path join Mod Anthracite | path expand
}

def main [addon: string = "", --addon-only] {
    if ($addon | str contains '$HOME') or ($addon | str contains '$env.HOME') {
        error make {msg: 'The installation path contains an unexpanded home variable. Use a ~/ path, or evaluate ($env.HOME | path join ...) in Nushell.'}
    }
    let destination = if $addon == "" {
        freecad-addon-directory
    } else {
        $addon | path expand
    }
    if $destination == $root {
        error make {msg: "Choose an installation directory outside this checkout."}
    }
    mkdir $destination
    for file in [Init.py InitGui.py AnthraciteExecutor.py AnthraciteInspect.py AnthraciteCompare.py AnthraciteHistory.py AnthraciteBridge.py AnthraciteUi.py AnthraciteIR.py AnthraciteAuthor.py AnthraciteEvaluate.py AnthraciteEvaluateWorker.py TestAnthracite.py anthracite LICENSE README.md justfile install.nu package.xml] {
        cp -f ($root | path join $file) ($destination | path join $file)
    }
    cp -r -f ($root | path join 'Anthracite Dark') $destination
    cp -r -f ($root | path join Resources) $destination
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
