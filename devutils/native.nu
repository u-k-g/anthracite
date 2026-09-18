#!/usr/bin/env nu
# SPDX-License-Identifier: LGPL-2.1-or-later
const root = path self | path dirname | path dirname
const script = path self

def --wrapped main [mode: string, ...arguments: string] {
    let source = $root | path join build src
    let jobs = $env.JOBS? | default "6" | into string
    if (sys host | get name) == "Darwin" {
        let caller = $env.PWD
        let nu_executable = $nu.current-exe
        hide-env -i PYTHONHOME PYTHONPATH QT_PLUGIN_PATH QML_IMPORT_PATH QML2_IMPORT_PATH CMAKE_PREFIX_PATH NIXPKGS_QT6_QML_IMPORT_PATH
        let prefix = $source | path join .pixi envs default
        $env.ANTHRACITE_PYTHON = $prefix | path join bin python
        cd $source
        match $mode {
            build => {
                # Opt-in override for hosts whose default (Xcode) SDK is too new for
                # the pinned clang/libc++; e.g. ANTHRACITE_OSX_SYSROOT=/path/MacOSX26.5.sdk
                let sysroot = $env.ANTHRACITE_OSX_SYSROOT? | default ""
                let sysroot_flag = if $sysroot == "" { [] } else { [$"-DCMAKE_OSX_SYSROOT=($sysroot)"] }
                ^pixi run --locked env CFLAGS= CXXFLAGS= DEBUG_CFLAGS= DEBUG_CXXFLAGS= cmake --preset conda-macos-debug -DBUILD_ANTHRACITE=ON ...$sysroot_flag
                if $env.LAST_EXIT_CODE != 0 { error make {msg: 'Build preparation failed; compilation was not started.'} }
                exec pixi run --locked cmake --build build/debug --parallel $jobs
            }
            exec => { exec pixi run --locked $nu_executable --no-config-file $script exec-at $caller ...$arguments }
            _ => { error make {msg: $"Unknown native command: ($mode)"} }
        }
    } else {
        match $mode {
            build => {
                let flags = $env.cmakeFlags? | default "" | split row ' ' | where { $in != "" }
                ^cmake -S $source -B $arguments.0 -G Ninja ...$flags -DCMAKE_BUILD_TYPE=Debug -DBUILD_ANTHRACITE=ON
                if $env.LAST_EXIT_CODE != 0 { error make {msg: 'Build preparation failed; compilation was not started.'} }
                exec cmake --build $arguments.0 --parallel $jobs
            }
            exec => { exec $arguments.0 ...($arguments | skip 1) }
            _ => { error make {msg: $"Unknown native command: ($mode)"} }
        }
    }
}

# Pixi changes directory; preserve the caller's document context.
def --wrapped "main exec-at" [directory: path, executable: string, ...arguments: string] {
    cd $directory
    exec $executable ...$arguments
}
