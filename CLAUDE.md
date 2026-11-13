# Agent briefing: COGBox-trilogy

Read this first. It is written for an AI coding agent (Claude Code or similar) picking up this project.

## What this project is

The Gears of War trilogy inside Gears of War 3, as mods for **COGBox** (github.com/Shagg88/COGBox; local clone
`../cogbox`), which runs Gears 1, 2, 3 and Judgment natively on PC:

1. **The trilogy**: Gears 1 and 2 campaigns converted into Gears 3's engine and gameplay, built on the player's PC
   from their own discs (docs/TRILOGY.md). Gears 1 chapter 1 (the prison) is the tech demo being polished first.
2. Then a remix mod, then tools for other creators. Mod support itself lives in COGBox (this project contributed it:
   COGBox 0.4.3, extended to mod API 1 in 0.6.0); changes to it go to COGBox as PRs on its `dev` branch.

This repository was a Gears of War 3 PC port (static recompilation + runtime) until 2026-11-12; that is kept at the
tag `legacy-port-final`. Its runtime is still what generates the trilogy's navmeshes (docs/NAVMESH.md) until the
builder is ported to COGBox's developer mode.

## Where the knowledge is

| File | What |
|---|---|
| docs/TRILOGY.md | the converter, recipes, what each chapter needs |
| docs/G1_G3_ARTICULATION.md | where Gears 1 and 3 differ and what the converter does about each (tools/g1g3map.py) |
| docs/ENGINEERING_LOG.md | every finding, newest at the end (long: search it, e.g. "Pylon", "CoverLink", "Kismet") |
| docs/REVERSE_ENGINEERING.md | game facts: addresses, layouts, formats |
| docs/COGBOX_DEV.md | developing and testing on COGBox (developer mode, scripted controller, Kismet trace) |
| docs/MODDING.md | the mod format |
| docs/NAVMESH.md | how navmeshes are made (the legacy runtime, for now) |
| docs/ECOSYSTEM.md | the other Gears projects (COGBox, the Gears 2 port, gow3-recomp) |
| docs/legacy/ | the old port's status, multiplayer and ReXGlue documents |

## Layout

- `tools/`: Python. `trilogy.py` (chapter builds from recipes in `tools/recipes/`), `levelinject.py` (Gears 1 levels
  into a Gears 3 package), `objcopy.py` (actors, Kismet, class/property mapping), `materialconv.py`, `meshconv.py`,
  `bspconv.py`, `lighting.py`, `navinject.py` / `pylons.py` / `navcross.py` (navigation), `upk.py` / `upkwrite.py`
  (package read / write), `kismetgraph.py` (a Kismet sequence as text), `g1route.py` (test routes over Gears 1's path
  network), `campaignclass.py` (classic characters as campaign classes), `modtool.py`, `xpack/` (the decompressor).
- `examples/mods/`: sample mods. `tests/`: scripted test inputs. `modcore/`: the portable mod core (MIT).

## Building and testing

- A chapter: `python -P tools/trilogy.py --mods <folder> build gears1_ch1 --reuse-navmesh --replace` (about 30
  minutes). Build test variants into a scratch folder (`build/scratch/...`), never the player's mods.
- Test on COGBox with its developer-mode switches: `--cogbox_input_script=<file>` (scripted controller: pad, trig,
  walkto, route, face, teleport, follow, choose, fight, shoot, capture, quit...), `--mod_kismet_trace=<package part>`,
  `--ue3_mods=<folders>`, `--ue3_exec_at=<ms>:<command>`. Use a test copy of a chapter (e.g. `Gears3\mods-test`) and
  a test user data folder, never the player's.
- Gears 1 reference shots: COGBox's Gears 1 build takes the same scripted controller; Gears 1's coordinates equal the
  conversion's.

## Rules

- Bring your own game: no game files, executables, extracted assets, generated code or keys in any commit. Facts
  about the game go in docs, in your own words.
- The repository is public: scan every commit for personal data (names, emails, home paths, IPs) before pushing.
- Never kill a game or launcher process you did not start yourself (the player may be playing); kill by exact PID.
- Back up the player's installed mods before replacing them; never install while the game runs.
- Write code like the code around it; log what you learn in docs/ENGINEERING_LOG.md.
- Commits end with a `Co-Authored-By:` line for the AI that helped.

## Open work (keep this current)

- Chapter 1 polish (2026-11-12): cutscene character shading (flatter and brighter than Gears 1's key light); ragdolls
  fall through converted floors (no PhysX collision for converted meshes); the big room after the path choice
  (stretched floor textures, a door's texture, a black slab, flat light cones); Marcus's arm twitch in dialogue;
  enemy pathfinding in the hard route's yard fight.
- Port the navmesh builder to COGBox's developer mode, so building needs no legacy runtime.
- Effects that Gears 3 lacks (Gears 1/2 particles and materials, the Riftworm...) and a COGBox mod marketplace:
  design notes in docs/ (2026-11-12).
