# SPDX-License-Identifier: LGPL-2.1-or-later
{ pkgs }:
let
  probe = pkgs.writeScript "anthracite-launch-probe" ''
    #!${pkgs.nushell}/bin/nu --no-config-file
    ${builtins.readFile ../tests/launcher-probe.nu}
  '';
in pkgs.runCommand "anthracite-launcher-check" { nativeBuildInputs = [ pkgs.nushell ]; } ''
  nu --no-config-file ${../tests/launcher.nu} ${../devutils/launch.nu} ${probe}
  touch "$out"
''
