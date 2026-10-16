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

Then build a conversion:

```
python -P tools/trilogy.py --g1 "D:\Games\gow1\disc" list
python -P tools/trilogy.py --g1 "D:\Games\gow1\disc" build gears1_ch1
```

| recipe | what you get | build |
|---|---|---|
| **gears1_ch1** | Gears 1's first chapter, the prison ("14 Years After"), as a chapter of its own: Campaign > Standard > Select Chapter > Act VI "Gears of War" > 1. Gears 3's campaign is untouched. | about 15 minutes (the level converted, then the game runs a few times, minimised, so Gears 3's own navmesh builder makes each region's navigation) |
| gears1_prison | the earlier preview: Gears 1's opening in place of Gears 3's prologue (Campaign > New) | about 3 minutes |

The result is a mod (`build\mods\Gears1_Chapter1`) that the launcher's Mods tab lists and can switch off. Every player
of a co-op session needs it (it is a set of packages).

## A chapter's packages

A Gears 1 level is a persistent level and its streamed sublevels (the prison: `SP_Prison_P` and 23 more). Each
becomes a Gears 3 package of its own, named after it with the game's prefix (`GOW1_SP_Prison_S01`), so Gears 1's
own scripts stream them in and out by name, as they did on the Xbox 360.

- **The persistent level** (the chapter's map) is a clone of a rich Gears 3 sublevel, the **kit**
  (`SP_Nightmare_02_S`): its material instances, compiled into its shader cache, are the parents of every converted
  material. Into it go Gears 1's persistent content and Kismet, its player start, its streaming list
  (WorldInfo.StreamingLevels), its post-process, and the **carriers** (below), always loaded.
- **A section** per Gears 1 sublevel is a clone of a small Gears 3 sublevel (`SP_Char_Tower_1a_S`, 736 KB) with that
  sublevel converted into it: meshes, BSP, light maps, materials (child instances of the kit's, imported), lights,
  cover, movers, sounds, Kismet.
- Clones are **blanked**: the template's actors are left out of its level (they never spawn, tick, draw or collide)
  but the ones the conversion reuses (mesh collections, cover links, pylons), its Kismet is emptied, and it gets its
  own package GUID (`tools/levelpkg.py`).
- **Carriers.** A character can spawn only if a loaded package carries its cooked class. Gears 3's campaign has Dom
  in `GEARGAMESP_SF` (always loaded) and Marcus in `GearGame.xxx`; other characters are cooked into the levels that
  use them. A carrier is such a level, blanked of everything, streamed in with the chapter for its characters:
  `SP_Airfield_02_S` (2.4 MB) for the Drone.

How Gears 3 runs a campaign chapter (found while building this): `GearGame_P` stays the persistent level and the
chapter's map is streamed into it, then the map's own levels. The player spawns at `GearGame_P`'s start (the
origin) before the map is in, so the map's script must move the player: Gears 1's does (a checkpoint, then a
teleport to its player start).

## The conversion model

What each kind of Gears 1 content becomes. Gears 2 (package version 575) follows the same model; its packages read
already (textures, meshes).

| Gears 1 | Gears 3 | notes |
|---|---|---|
| static meshes (StaticMeshActor) | components of the section's StaticMeshCollectionActor, new StaticMesh exports with a kDOP tree (per-polygon collision) | `levelinject`, `meshconv`, `kdop` |
| BSP | static mesh chunks with the BSP's light maps | `bspconv` |
| light maps, lights | vertex light maps baked from Gears 1's light maps and point lights; its dynamic lights copied (for characters) | `lighting`; the cutscene lights are too bright on characters (to calibrate) |
| materials | child MaterialInstanceConstants of the kit's template (texture parameters only) | translucent materials (light shafts, fog sheets) are left out for now |
| textures | Texture2D exports, NeverStream | |
| skeletal meshes, animations | SkeletalMesh (a skinned material template), AnimSet / AnimSequence | FaceFX not yet |
| sounds | XMA1 to XMA2 (lossless), SoundCues and their nodes | 3-stream XMA (surround) not yet |
| music | SeqAct_SetMusicTrack to SeqAct_PlayMusicTrack | music track banks and cross-fades not yet |
| cover (CoverLink) | the section's CoverLinks (and clones) with Gears 3's slot format | |
| paths (PathNode, ReachSpec) | Gears 3's navigation mesh, generated by Gears 3's own builder in the game | each region's geometry converted again into a multiplayer map's slot (20000 units up), built there, moved back into a section's pylons (`[navmesh ...]` in a recipe) |
| Kismet | the same sequences with Gears 3's classes; 30 classes renamed to their Gears 3 counterparts; actions with no counterpart are pass-throughs (their chains keep running) | `objcopy`; see below |
| streaming (LevelStreaming, LevelVisibility, MultiLevelStreaming, WaitForLevelsVisible) | the same actions, level names prefixed | names of levels not built are dropped (Gears 1's next chapter) |
| SeqVar_Dom | GearSeqVar_Player, squad slot 2 | Gears 3's squad is four player slots, each a human or an AI; a co-op partner takes Dom's slot |
| SeqVar_Named across levels ("Hard", "MarcusLeft", "dom") | "dom": GearSeqVar_Player slot 2; a variable of the chapter's persistent level: an import of that variable in the converted persistent package | Gears 1 resolves a name in any loaded level, Gears 3 in the variable's own level only. Unresolved, a CompareBool without variables is True (the prison's ceiling collapse dialog ended early and the hard route's door never opened) and AI orders on "dom" had no pawn |
| SeqAct_TutorialManual, SeqAct_ToggleTutorialMode | SeqAct_ManageTutorials (Add / Complete Tutorial, System On / Off), the Gears 3 tutorial teaching the same thing | |
| AI factory spawning Dom | the same, with "Player Slot" 2 | Gears 3's own levels spawn squad members so |
| Gears 1 / 2 characters | Gears 3's versions where it has them: a recipe's `pawn_classes` (Pawn_COGDom=GearPawn_COGDomG2MP: Gears 3's classic Dom) and `load_packages` (its multiplayer character package, MPCH_COGDomG2MP_SF, written as the mod's `content =`: the runtime loads it and keeps it loaded) | classic Marcus, Dom, Cole, Baird... are multiplayer characters (MPCH_*G2MP_SF, their own meshes on Gears 3's skeleton); Marcus, the player, needs a slot 1 factory (as Gears 3's SP_Hanover_P makes Cole the player) |
| objectives' texts | Gears 1's [OBJECTIVES] strings (Coalesced.int, warfaregame.int) as the mod's config fragment for Gears 3's level_objectives.int [Objectives] | other languages: the English text for now |
| AI factory spawning Locust | the same, with Gears 3's class: Drone (classic in Horde, Savage in the campaign), Boomer, Theron, Wretch... | the class must be carried (above) |
| SeqAct_WarCheckPoint + SeqEvt_CheckpointActivated | SeqAct_Checkpoint ("Save"), its "Out" linked to the event's targets; the event as SeqEvent_CheckpointLoaded | Gears 1's event fires on save and load, Gears 3's on load |
| player start | PlayerStart in the persistent level, in its navigation list | |
| triggers, trigger volumes | Trigger (a volume as a cylinder over its bounds) | |
| movers, doors (InterpActor), Matinee | InterpActor, SeqAct_Interp with Gears 1's tracks | the view returns to the player after a cutscene |

### Gears of War 2 (version 575)

What is known for Gears 2's levels (its first chapter, the hospital: `SP_Hospital_P` and 26 more):
- Every package decodes (tools/xpack: Xbox LZX streams leave an odd-sized uncompressed block unpadded; with the
  CAB form's pad byte, some blocks failed).
- A chapter starts from an intro map (`SP_HospitalIntro_P`) whose PrepareMapChange loads the chapter's persistent
  level with its first sublevels (the tutorial's among them); the chapter's streaming is in its scripting sublevels
  (`_S`), not the persistent level. A recipe's `persistent` is then the chapter's `_P`, and the intro's Kismet joins
  it (or the intro becomes the persistent level).
- Properties are between Gears 1's and Gears 3's: booleans 4 bytes (Gears 3: 1 byte, from 673), enums as a byte
  (Gears 3: a name, from 633), no import/export GUID table in the summary. levelinject converts Gears 1 (374) today;
  its material, mesh and Kismet steps check for 374 and need their 575 reading (textures and static meshes read
  already).
- The squad: Gears 2's Dom, Baird, Gus, Tai are Gears 3 slots 2-4 (Baird and Gus are cooked into Gears 3 levels:
  carriers; Tai is not in Gears 3).

Not converted yet, by kind: objectives and tutorials (pass-throughs), SeqAct_AIShootAtTarget, SeqAct_CauseDamage,
SeqAct_AICombatSpectrum, Kryll, FaceFX, translucent materials, level streaming volumes (their levels stream by
Kismet).

## Recipes

A recipe (`tools/recipes/<name>.ini`) describes a conversion. A chapter of its own:

```ini
[mod]
folder = Gears1_Chapter1
name = Gears of War: 14 Years After (trilogy)
side = everyone

[chapter]
number = 32                       ; Gears 3 reserves 32-49 for downloadable campaigns
act = 6
title = 14 Years After
prefix = GOW1_
persistent = SP_Prison_P          ; the Gears 1 persistent level
sections = SP_Prison_S01, ...     ; every other package of the level
kit = SP_Nightmare_02_S
materials = MI_GR_Cliffwall_Tilable_01
section_template = SP_Char_Tower_1a_S
look = SP_Nightmare_P             ; the post-process Gears 1's values go on top of
content = GEARGAMESP_SF
carriers = SP_Airfield_02_S
options = --bsp --replace --cover --kismet

[section SP_Prison_S02_Meshes]     ; optional, per section: options, extra_sources
options = ...

[navmesh cellblock]                ; a navmesh region
section = SP_Prison_S02_Scripting  ; the section whose pylons receive it
pylon_at = 150,940,150             ; standing room on the converted floor (Gears 1's path nodes are)
pylons_at = -1441,1975,178;...     ; more pylons for a large or broken-up region
slot = MP_GridlockG3
url = MP_GridlockG3?game=GearGameContent.GearGameHorde?listen
offset = 0,0,20000
sources = SP_Prison_P, SP_Prison_S01, SP_Prison_S02, SP_Prison_S02_Meshes
options = ... --offset 0,0,20000 --pylon-at=150,940,20150 ...   ; the same pylons, 20000 up
```

To start a new chapter's recipe:
1. List the level's packages (`python -P tools/upk.py <persistent> exports LevelStreaming`).
2. `python -P tools/levelroute.py <scripting packages>` lists where the player must go (touch triggers), where the
   squad goes (move orders), teleports and checkpoints: the seeds for navmesh pylons and the route for a playtest.
3. Build with `--no-navmesh` first, look at the report (characters not available, actions without a counterpart,
   data not converted), then add the navmesh regions.

## Debugging a conversion

- `GOW3_KISMET_TRACE=GOW1_` logs every Kismet op of the converted packages that runs, with its sequence path:
  where a converted script stops.
- `python -P tools/kismetgraph.py <package> <sequence>` prints a sequence's ops, their output links and variables
  (Gears 1, 2 or 3 packages): compare the Gears 1 original with the conversion.
- `GOW3_LOG_PROPS=Class:Prop,Prop@;...` logs live objects' properties (`@`: a reference by name), with each object's
  package; `GOW3_LOG_POS=1` the pawns' positions; `GOW3_NO_PLAY_AREA_KILL=1` while a region has no navmesh.
- Playtests: `tests/g1_chapter1.txt` (the chapter from Select Chapter on a fresh profile); the input script's
  `follow <class part> [s] [distance]` walks the player after a pawn (Dom leads the way in Gears 1).
- A script that stops where its sequence "should" continue: check which output of a condition fired, then what it
  read. The prison's ceiling collapse ended early because its CompareBool read a named variable of another level
  (Gears 3 does not resolve those: converted to imports now). A player who stops moving may be in cover: the route
  walker's A press at a wall takes cover (screenshots show it).

## Roadmap

The deliverables, in order (each comes only after the one before it is complete):

**A. The trilogy mod**: Gears of War 1 and 2's campaigns playable start to finish in Gears 3's engine and sandbox, in
single player and co-op. It keeps those games' encounters and sandbox: no weapons or enemies Gears 1 or 2 did not
have there. Where Gears 3 has a higher-quality version of a Gears 1 / 2 asset, that version replaces the original:
above all the characters (Gears 1 and 2's Marcus become Gears 3's classic Marcus, likewise Dom, Cole, Baird and the
rest), in play and in cutscenes.
1. **The prison, end to end**: every section converts (done), navmesh for every region (four regions), the first
   enemies (the Drone carrier: done), the scripted fights, Gears 1's chapter end (done: chapter complete, then the
   main menu until a next chapter exists).
2. **Characters from Gears 3**: the squad plays as Gears 3 pawns (Marcus slot 1, Dom slot 2); Dom is Gears 3's
   classic Dom (`pawn_classes`, `load_packages`). Next: classic Marcus (a slot 1 factory at the chapter's start),
   then Gears 3 meshes in place of Gears 1's in Matinees (the skeletons differ: animations retargeted or the
   cutscene's characters played by Gears 3 pawns).
3. **Act 1, then the rest of Gears 1**: one recipe per chapter, chapters 32 and up in order, each leading to the next.
4. **Gears of War 2**: the converter on version 575 packages; Baird and Gus as squad slots 3 and 4.
5. **In the launcher**: a Trilogy page that finds the Gears 1 / 2 discs and builds the chapters.
6. **Closer to the originals**: the classic Drone (Horde's cooked class, loaded by the runtime), FaceFX, translucent
   materials, music banks.

**B. The remix trilogy mod** (after A is complete): the same levels with encounters reworked for Gears 3's sandbox:
its weapons, enemies and set pieces (the Silverback...) where Gears 1 and 2 had none. A second mod beside A, built
from the same recipes plus encounter edits (new factories, carriers for Gears 3's enemies).

**C. A modding framework** (after the trilogy): everything learned here turned into libraries and tools that make
modding the port far more accessible: campaign levels (the chapter-of-its-own pipeline, kits, carriers, navmesh
regions, Kismet mapping and tracing), characters, encounters, weapons, menus, and the other mod types they make
possible.
