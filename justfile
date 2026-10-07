# SPDX-License-Identifier: LGPL-2.1-or-later
set shell := ["nu", "--no-config-file", "--commands"]
root := justfile_directory()

default:
    @just --list

# Copy the addon and install the CLI and agent skill.
install addon="":
    @nu --no-config-file '{{root}}/install.nu' {{quote(addon)}}

# Run native tests in stock FreeCAD with an isolated profile.
test:
    @nu --no-config-file '{{root}}/tests/runtests.nu'
