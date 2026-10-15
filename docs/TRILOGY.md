# The trilogy: Gears of War 1 and 2 in Gears of War 3's engine

The goal: the campaigns of Gears of War and Gears of War 2 playable **inside this port**, with Gears 3's engine,
gameplay and sandbox (its movement, weapons, AI, co-op and modes), not by starting another game. Their levels,
characters, scripts and sound are converted into Gears 3's formats.

Everything converted comes from **your own discs** and is built on your PC into your mods folder. The repository holds
only the tools and the recipes, never a converted file; the results must not be shared with people who do not own
the games.

## Trying it

You need Gears of War 1's disc files next to Gears 3's (setup prepares Gears 3's). Extract your Gears 1 disc image
once:

```
python -P tools/xdvdfs.py "D:\Games\Gears of War.iso" "D:\Games\gow1\disc"
```

Then build a conversion (about three minutes: the level is converted, then the game runs once, minimised, so Gears
3's own navmesh builder can make the level's navigation):

```
python -P tools/trilogy.py --g1 "D:\Games\gow1\disc" list
python -P tools/trilogy.py --g1 "D:\Games\gow1\disc" build gears1_prison
```

The result is a mod (`build\mods\Gears1_Prison`) that the launcher's Mods tab lists and can switch off.

**gears1_prison**: Gears 1's opening. Campaign > Standard > New starts in Gears 1's prison cell instead of Gears 3's
prologue: Gears 1's opening cutscene (its cameras, characters and sound), Dom at the cell door, his orders, and the
player following him out of the cell block, lit by Gears 1's own lights and colour grading. Gears 3's prologue
returns when the mod is off. Verified (tests/g1_follow_dom.txt, built from the discs by the command above): the
cutscene plays, Marcus follows Dom out of the cell and along the corridor, Dom reaches his first goal, Gears 1's own
"Choose path" prompt appears; 60 fps.

## How a conversion is built

A recipe (`tools/recipes/<name>.ini`) names the Gears 1 packages, the Gears 3 level whose slot they take, and the
steps:

| step | tool | what it does |
|---|---|---|
| convert | `levelinject.py` | Gears 1's static meshes, BSP (as static meshes, with their light maps), materials (on Gears 3 parent materials), cover, lights, post-process, skeletal meshes, sounds and Kismet copied into the Gears 3 level package (version 374 -> 828) |
| navmesh | the game (`runtime/ue3/navmesh_build.cpp`), `navinject.py` | Gears 3's leftover navmesh builder passes run in game; the mesh is dumped and written into the package. The builder finds no ground in a streamed campaign sublevel, so a campaign recipe converts the geometry a second time into a multiplayer map's slot (20000 units up), builds there and moves the mesh back (`[navmesh] slot`) |
| patch | `stripkismet.py` with a `.txt` recipe | the Gears 3 packages around the slot edited: their scripts off, a cutscene skipped, a player start moved |
| check | `modtool.py check` | every package read back |

`trilogy.py convert-map` and `patch-level` run single steps for creators; `levelinject.py --help` lists the conversion
options. The formats and findings behind each tool are in docs/ENGINEERING_LOG.md and docs/REVERSE_ENGINEERING.md.

## What converts today

- Textures, static meshes (with kDOP collision), BSP, light maps, materials mapped onto Gears 3 parents.
- Skeletal meshes and animations (AnimSet / AnimSequence), sounds (XMA), lights (point and spot, dynamic channels).
- Cover (CoverLinks with their slots), navmeshes (generated in game, several pylons, mantle and cover-slip edges).
- Kismet: Gears 1 sequence objects mapped to Gears 3 classes; actions without a Gears 3 class pass their links through;
  Matinee cutscenes with Gears 1's cameras and director; AI factories spawning Gears 3's characters (Dom).
- Multiplayer maps: Gears 1's Gridlock inside Gears 3's Gridlock slot, playable in Horde.

## Roadmap

1. **The prison, end to end**: the rest of the cell block's scripting past Dom's first orders, the first enemies
   (the yard, S03-S04, converted separately already), FaceFX, translucent materials (light shafts).
2. **A level of its own**: a converted persistent level as a new campaign chapter (32-49, docs/MODDING.md) instead of
   taking the prologue's slot, so Gears 3's campaign stays untouched.
3. **Act 1 of Gears 1**, then the rest: one recipe per chapter, run in order by one command.
4. **Gears of War 2**: the same tools on version 575 packages (textures and meshes read already).
5. **In the launcher**: a Trilogy page that finds the Gears 1 / 2 discs and builds the chapters.
