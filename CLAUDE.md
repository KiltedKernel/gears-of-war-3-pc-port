# Agent briefing: gears3-pc

Read this first. It is written for an AI coding agent (Claude Code or similar) picking up this project, including
agents working on [Flippage/gears-of-war-3-recomp](https://github.com/Flippage/gears-of-war-3-recomp).

## What this project is, and where it is going

A Gears of War 3 PC port (XenonRecomp static recompilation + a small kernel reimplementation + Xenia's D3D12 GPU
code), whose real deliverables are:

1. **Mod support for Gears 3**: `mods\<Name>\mod.ini` folders, merged table of contents, merged config, new campaign
   chapters, mods in multiplayer (docs/MODDING.md).
2. **The trilogy**: Gears of War 1 and 2 campaigns converted into Gears 3's engine as mods, built on the player's PC
   from their own discs (docs/TRILOGY.md). Then a remix mod, then a modding framework for other creators.

**Direction (2026-10-30):** gow3-recomp becomes the Gears 3 base; this repository narrows to mod support and the
trilogy. Our mod runtime files are offered to gow3-recomp under BSD-3 (its issue #25). The plan, their extension
points and the file-by-file mapping: **docs/REXGLUE_PORT.md**. Our runtime stays the trilogy's test bed until the
trilogy plays the same on gow3-recomp. Flippage has since proposed one merged package with the Gears 2 port and
COGBox (docs/ECOSYSTEM.md); our mod features go in as a portable core plus per-runtime adapters.

## Where the knowledge is

| File | What |
|---|---|
| docs/ECOSYSTEM.md | the other Gears projects (COGBox, Gears 2 port, gow3-recomp), the proposed merger, what we bring |
| docs/REXGLUE_PORT.md | the move onto a ReXGlue runtime: portable mod core + adapters, mapping, steps |
| docs/TRILOGY.md | the converter, recipes, what each chapter needs |
| docs/ENGINEERING_LOG.md | every finding, newest at the end (long: search it, e.g. for "Pylon", "CoverLink", "Kismet") |
| docs/REVERSE_ENGINEERING.md | game facts: addresses, layouts, formats |
| docs/MODDING.md | the mod format players and creators use |
| docs/STATUS.md | what is verified, what is not |

## Layout

- `runtime/`: the port's runtime. Mod pieces: `kernel/mods.cpp`, `mods.h`, `mod_manifest.h`, `kernel/mod_config.cpp`,
  `ue3/campaign_chapters.cpp`, `ue3/content_packages.cpp` (all original to this project; their portable core is `modcore/`, MIT).
  `kernel/io/file_system.cpp` descends from UnleashedRecomp: GPL only, never copy it into a BSD project.
  Debug tooling: `hid/hid.cpp` (scripted input), `ue3/prop_log.cpp`, `ue3/kismet_trace.cpp`, `ue3/cover_trace.cpp`.
- `tools/`: Python. `trilogy.py` (chapter builds from recipes in `tools/recipes/`), `levelinject.py` (Gears 1 levels
  into a Gears 3 package), `objcopy.py` (actors, Kismet, class/property mapping), `navcross.py` / `pylons.py` /
  `navinject.py` (navigation meshes), `materialconv.py`, `staticmesh.py`, `upk.py` / `upkwrite.py` (package read /
  write), `kismetgraph.py` (a Kismet sequence as text), `g1route.py` (test routes over Gears 1's path network).
- `ppc/`: generated code from the player's own executable. Git-ignored; never commit it.

## Building and testing

- Runtime: `cmd /c tools\build.cmd` (or `ninja gow3.exe` in `build\`). The link fails while gow3.exe is running.
- A chapter: `python -P tools/trilogy.py --mods <folder> build gears1_ch1 --reuse-navmesh --replace` (about 7
  minutes reusing the navmesh). Build test variants into a scratch folder, not the player's `build\mods`.
- Scripted playtest: `powershell -File tools\playtest.ps1 -Script <file> -Seconds N -Mods <mod folder> -Fresh
  [-Shots "t1,t2"]`. Script verbs (one per line, `<seconds> <verb> ...`): pad, trig, walkto, route, face, look,
  teleport, follow, choose, fight, shoot, capture (runtime/hid/hid.cpp header). Logs: `build\gow3.log`; shots:
  `build\shots\`.
- Diagnostics (environment variables): `GOW3_LOG_POS=1`, `GOW3_LOG_PROPS="Class:Prop,Prop@,+offset;..."` with
  `GOW3_LOG_PROPS_MS`, `GOW3_SET_PROPS="Class:Prop=value"`, `GOW3_KISMET_TRACE=<package substring>` (needs
  GOW3_LOG_PROPS set too), `GOW3_NAV_TRACE=1`, `GOW3_COVER_TRACE=1`.

## Rules

- Bring your own game: no game files, executables, extracted assets, generated code or keys in any commit. Facts
  about the game go in docs, in your own words.
- The repository is public: scan every commit for personal data (names, emails, home paths, IPs) before pushing.
- Tests use `-Fresh` and their own data folders; never the player's saves, mods or settings.
- Never kill a game or launcher process you did not start yourself (the player may be playing); kill by exact PID.
- Write code like the code around it; log what you learn in docs/ENGINEERING_LOG.md.
- Commits end with a `Co-Authored-By:` line for the AI that helped.

## Open work (keep this current)

- COGBox (the merged project's home, local clone `../cogbox`): our mod support (PR #122) is merged in COGBox 0.4.3
  (SDK patch 61, renumbered from our 60); fixes for its issues #146 (PR #148, merged) and #145 (PR #156, Gears 2
  voice-language twins). Build recipe and status in docs/ECOSYSTEM.md and COGBox's Gears3-Native/notes/28-mod-support.md.
  **The trilogy's chapter 1 loads and plays on COGBox.** Open: launcher entries for chapters 32+; test tooling.

- Port the six mod files and the debug tooling onto gow3-recomp (docs/REXGLUE_PORT.md).
- Trilogy, Gears 1 chapter 1 (prison), easy route: scripted runs pass the cell, the cover room, the S02 door, the
  Drone fight, the S04 catwalk (fixed 2026-11-02: two converter bugs, ENGINEERING_LOG "the catwalk") and the
  roadie-run door (button at y 4675, hold A); S05's scripting starts. Next: S05 to the yard (script:
  build/scratch/buttons8.txt, extend it).
  Continue (quit and resume at a checkpoint) works since 2026-11-03: converted Kismet needs unique net indices
  (tools/upkwrite.py; ENGINEERING_LOG 2026-11-03). Checkpoint-start tests: build/scratch/snap_s04f (S04 save) +
  cp_s04_yard.txt; pass -Mods the mod folder itself (.../Gears1_Chapter1), not its parent. Seen once: Dom below the world.
- Chapter 1 route stops at the S05 rubble (-4367,6828): converted collision blocks a plain Gears 1 path (ENGINEERING_LOG
  2026-11-04). Not yet verified: the hard route end to end, the yard fight, the Corpser ending, the hand-off to chapter 2.
  Chapters 2 and 3 rebuilt 2026-11-04 (build/scratch/trilogy3); both load and play from Select Chapter (boot_ch2/3.txt).
  The S05 stop is a frozen player, not a wall (ENGINEERING_LOG 2026-11-04 later); Dom's fall was a test-script artifact.
