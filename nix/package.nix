# SPDX-License-Identifier: LGPL-2.1-or-later
{ pkgs, lib }:
let
  revision = lib.trim (builtins.readFile ../freecad_commit.txt);
  # Include submodules. Update this hash when changing freecad_commit.txt.
  upstream = assert lib.trim (builtins.readFile ../freecad_repo.txt)
    == "https://github.com/FreeCAD/FreeCAD.git";
    pkgs.fetchFromGitHub {
    owner = "FreeCAD";
    repo = "FreeCAD";
    rev = revision;
    fetchSubmodules = true;
    hash = "sha256-RP68rd19wX4gDD5PuRQ1J4Z9Qmp5HpEg6sC94RRMEdI=";
  };
  entries = builtins.filter (line: line != "") (
    map (line: lib.trim (builtins.head (lib.splitString "#" line)))
      (lib.splitString "\n" (builtins.readFile ../patches/series))
  );
  series = map (entry:
    if builtins.match "[A-Za-z][A-Za-z0-9_-]*\\.patch" entry == null then
      throw "Invalid Anthracite series entry: ${entry}"
    else ../patches + "/${entry}"
  ) entries;
  source = pkgs.applyPatches {
    name = "anthracite-source-${builtins.substring 0 12 revision}";
    src = upstream;
    patches = series;
    postPatch = ''
      test -f src/3rdParty/OndselSolver/CMakeLists.txt
    '';
  };
in {
  inherit source;
  package = pkgs.freecad.overrideAttrs (old: {
    pname = "anthracite";
    src = source;
    # Retain nixpkgs' platform integration patches after Anthracite's series.
    buildInputs = old.buildInputs ++ [ pkgs.qt6.qtdeclarative ];
    cmakeFlags = old.cmakeFlags ++ [ "-DBUILD_ANTHRACITE=ON" ];
    # A local builder can compile this too; parallelism follows Nix's cores setting.
    requiredSystemFeatures = [];
    postInstall = (old.postInstall or "") + ''
      # The branding template is relative to FreeCAD's application home.
      test -f "$out/Mod/Anthracite/AnthraciteDefaults.cfg"
      test -f "$out/Mod/Anthracite/anthracite-mcp"
      test -f "$out/bin/branding.xml"
    '';
    postFixup = (old.postFixup or "") + ''
      # Wrap the existing Qt/Python wrapper, preserving its complete native closure.
      mv "$out/bin/FreeCAD" "$out/bin/.anthracite-FreeCAD"
      makeWrapper ${pkgs.nushell}/bin/nu "$out/bin/FreeCAD" \
        --add-flags --no-config-file \
        --add-flags ${../devutils/launch.nu} \
        --add-flags "$out/bin/.anthracite-FreeCAD"
    '';
    meta = old.meta // {
      description = "FreeCAD with a checked Python executor exposed to agents over a local bridge";
      mainProgram = "FreeCAD";
    };
  });
}
