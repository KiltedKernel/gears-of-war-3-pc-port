# Gears of War 1 -> 3: points of articulation

Where Gears 1's engine and data differ from Gears 3's in ways the trilogy's converter must handle. Each point is
either measured on the games' own files (tools/g1g3map.py), read from the running games (COGBox's Gears 1 and
Gears 3 builds with the scripted controller and object reflection), or taken from the engineering log, which has the
details and the in-game evidence. Started 2026-11-11, with Gears 1 running on COGBox next to Gears 3.

## How this is measured

| Tool | What it answers |
|---|---|
| `tools/g1g3map.py <recipe>` | Every class in a chapter's Gears 1 packages, its object count and the Gears 3 class the converter turns it into. Lists Gears 1 classes with no Gears 3 class, and properties Gears 1 objects set that the Gears 3 class does not declare (editor-only data left out; `*`: the converter's source names the property). |
| COGBox Gears 1 + `--cogbox_input_script` | The real Gears 1, driven by script: `pos`, `teleport`, `face`, `walkto`... (Gears1-Native ue3/script_input.h). |
| The same script on the trilogy | Gears 1's map coordinates are the conversion's, so one script of `teleport` / `face` lines gives the same view in both games: side-by-side screenshots (`um win shot`, window only). |
| `--mod_kismet_trace`, `--mod_log_props` (COGBox Gears 3) | Which converted Kismet ops run, what a live actor holds. |

The maps of chapters 1-3 (2026-11-11) are in `build/scratch/g1g3/ch*_map.md` (local; they name game data, so they
are not committed). Regenerate them after converter changes.

## The two engines

| | Gears of War (2006) | Gears of War 3 (2011, TU6) |
|---|---|---|
| Package version | 374 | 828 |
| Compression | LZO (whole packages and texture mips) | LZX (XMemCompress chunks) |
| Names in memory | 16-bit (big-endian) text at entry +16 | 8-bit text at entry +8 |
| UObject in memory | slot +4, Outer +40, Name +44 (index, number), Class +52 | slot +32, Outer +40, Name +44, Class +52 |
| UStruct / UField | SuperStruct +60, Children +76, Next +64 | SuperStruct +64, Children +68, Next +60 |
| UProperty | ElementSize +72, Offset +96, BoolProperty mask +124 | ElementSize +68, Offset +84, mask +104 |
| Object references in script bytecode | 4 bytes | 8 bytes in memory (4 on disk) |
| Textures | every mip inline in the package, LZO; no texture caches | top mips in texture caches (`Textures.tfc`, `CharTextures.tfc`), LZX, streamed; small mips inline from `FirstResourceMemMip` |
| Level streaming | LevelStreamingVolumes and Kismet | Kismet (the converter streams by Kismet only) |
| AI navigation | PathNodes and ReachSpecs | navigation meshes (Pylons); path nodes are not walked |
| Cover | `CoverLink` actors plus `CoverSlotMarker` path nodes | `CoverLink` slots, found by the player through navmesh polygons' cover references |
| Game classes | `War*` (WarPC, WarPawn, WarAI_*), pawns `Pawn_Geist*` / `Pawn_COG*` | `Gear*` (GearPC, GearPawn, GearAI_*), pawns `GearPawn_Locust*` / `GearPawn_COG*` in content packages |
| Characters | Gears 1's own meshes and FaceFX | Gears 3's classic Marcus and Dom (content classes on campaign classes: tools/campaignclass.py) |

The memory layouts were found at run time on both games (Gears1-Native ue3/objects.cpp `Discover`, logged on TU5;
Gears 3's from the Gears 3 port).

## Points of articulation and what the converter does

Status: **done** (converted and checked in game), **partial**, **gap** (not converted yet). Counts are objects in
chapters 1 / 2 / 3. A gap's status comes from the data and the converter's tables; the in-game effect written beside
it is the expected one, to be confirmed with a side-by-side view when that point is worked on.

### World and rendering

| Gears 1 | Gears 3 | Status | Notes / next step |
|---|---|---|---|
| BSP, static meshes, light maps | the same classes | done | geometry, collision and Gears 1's baked lighting carried over; extra lights baked (lighting.py) |
| Textures, every mip inline (LZO) | streamed from texture caches | partial | converted textures are resident (`NeverStream`) and capped at 512 (`max_texture`), so walls look smoother than Gears 1's (side-by-side view 4). Streaming them from the chapter's own `.tfc` is in progress (below) |
| Materials (expression graphs) | material instances of Gears 3 parents | done | slot textures carried; `MaterialExpressionTextureCoordinate.Tiling` (825 in ch1) handled |
| `ParticleSpriteEmitter` settings: SpawnRate, Material, BurstList, ScreenAlignment, EmitterLoops, EmitterDuration, UseLocalSpace, SubImages, InterpolationMethod | moved to `ParticleModuleRequired` / spawn modules | partial | spawn rate, material and bursts are read; looping, duration, local space, sub-UV frames and screen alignment are not: effects can loop or animate wrongly. Next: map them into ParticleModuleRequired |
| `ParticleLODLevel.LevelSetting` (207) | not declared | gap | low priority (LOD distances) |
| `DecalComponent.Thickness` (60), `DecalLifetimeAge` (24-44) | renamed / different lifetime system | gap | decals may clip or never fade |
| `KActor` physics props (16 in ch1: broken lights, rubble, the ceiling collapse's cover pieces, floor stones Kismet shows on the easy path) | `KActor` | partial | were not converted at all; now copied as InterpActors (objcopy, `OBJCOPY_NO_KACTORS` turns it off): they stand where Gears 1 put them and Kismet can show / hide / move them, but do not fall or tumble (converted meshes carry no rigid-body collision: physics is the next step). Their `DrawScaleX/Y/Z` are Gears 1's copies of `DrawScale3D`, which they also carry |
| `WDO_*` destructible furniture (couch, desk, armchair...) | none | gap | ch3's office (13 couches...): a static mesh stand-in, or a Gears 3 destructible |
| Physical materials (`WarPhysicalMaterial*`: footsteps, impact sounds, impact effects, decals; 200+ in ch3) | `PhysicalMaterialProperty` with Gears 3's own tables | gap | surfaces sound and splash with Gears 3's defaults: map Gears 1 surface types onto Gears 3's |

### Gameplay actors

| Gears 1 | Gears 3 | Status | Notes / next step |
|---|---|---|---|
| PathNodes / ReachSpecs | navigation meshes | done | built by Gears 3's own builder from seeds (navmesh recipes); missing geometry leaves holes (the hard hallway: fixed 2026-11-11) |
| `CoverSlotMarker` (1006 / - / 3651) | none: slots live in CoverLink | done | cover links converted, slots from the markers |
| `WarWallPathNode` (ch3: 66) | none | gap | Gears 1's wall-climb nav points (Corpser / wretches on walls): check what uses them |
| `WarWeaponPickupFactory` | GearWeaponPickupFactory | done | |
| `WarAmmoPickup_AmmoCase` / `_AmmoPouch` (15 / 8 in ch3) | Gears 3 ammo pickups | gap | ammo boxes missing |
| `WarCrimsonOmenPickupFactory` (COG tags; 3) | collectibles (Gears 3's own table) | gap | Gears 1's collectibles: need their own tracking or a Gears 3 collectible |
| `WarPointOfInterest` (12-15) | POI (Y to look) | partial | used as Kismet targets; the look-at prompt itself is not converted |
| `WarCheckpoint` -> PathNode (`Overrides`, `TeleportDistance`) | checkpoints by Kismet | partial | per-checkpoint overrides lost |
| `WarSpawner_EmergenceHole`, `WarSpawner_Seeder` | Gears 3 e-holes / seeder | partial | e-holes converted (EHOLE_CLASSES); seeders not |
| `Turret_TroikaCabal`, `Weap_Troika` | Gears 3 Troika | partial | owned components dropped (OBJCOPY troika handling) |
| `HammerOfDawnBeam*`, `WEAP_HammerOfDawn` | Gears 3 Hammer of Dawn | gap | ch3's Hammer of Dawn |
| Pawns `Pawn_Geist*`, `Pawn_COG*` | `GearPawn_*` content classes | done | PAWN_MAP; classic Marcus on the campaign class (GearPawn_COGMarcusG1: the multiplayer class twitched) |
| AI controllers `WarAI_*` | `GearAI_*` | done | CONTROLLER_MAP |
| `GUDData_*`, `SGroup_*` (character chatter and sound groups) | Gears 3's GUDS banks | gap | Gears 1 battle chatter is not played; Gears 3's for the stand-in classes plays instead |
| `WarAnim_*` anim-tree nodes (ch3) | Gears 3 anim nodes | gap | Gears 1 anim trees (cutscene or AI meshes) do not load; Gears 3's are used |

### Scripting (Kismet)

| Gears 1 | Gears 3 | Status | Notes / next step |
|---|---|---|---|
| Sequences, events, variables, most actions | the same classes or CLASS_RENAME | done | |
| `SeqAct_WarCameraShake` -> `SeqAct_CameraShake` (26) | `CameraShake`, `GlobalScale`, `GlobalAmplitudeScale`, `GlobalFrequencyScale` not carried | gap | scripted shakes run without their shake settings: convert the shake object and scales |
| `SeqAct_SetMood` -> `SeqAct_AISetMood` (14) | `Mood` not carried (12 set it) | gap | AI combat moods never set: map Gears 1's mood enum onto Gears 3's |
| `SeqAct_AIShootAtTarget` (6 / - / 2) | none (made a log op) | gap | Dom's scripted shots (the bridge guards in ch1): Gears 3's SeqAct_AIFireAtTarget-style op |
| `SeqAct_SetWalking` (ch3: 21), `SeqAct_DoorPush` (10) | none | gap | |
| `SeqAct_AICombatSpectrum`, `SeqAct_AISetTetherDist` | none | gap | AI tuning, low priority |
| `SeqAct_ClearObjectives`, `SeqAct_FailObjective` | Gears 3 objectives | gap | objectives are added (config fragment) but not cleared/failed |
| `SeqAct_TutorialManual` -> `SeqAct_ManageTutorials` (`ObjectiveName`, `ObjectiveDesc`) | | partial | tutorial type kept, its objective text not |
| `SeqAct_DisplayPathChoice` (`TitleMarkup`, `LeftDescMarkup`, `RightDescMarkup`) | Gears 3's path choice | partial | the choice works; Gears 1's texts are not shown |
| Music (`SeqVar_MusicTrack`, `SeqVar_MusicTrackBank`, `SeqAct_CrossFadeMusicTracks`, `SeqAct_SetMusicTrack`) | `SeqAct_PlayMusicTrack` | partial | SetMusicTrack is renamed; track variables and cross-fades are not converted |
| `SeqAct_VinceTag` (10-31) | none | n/a | Gears 1's development tags (no effect) |
| UI scenes in levels (`UIScene`, `UIState*`, `UIEvent*`...) | Scaleform menus | n/a | Gears 1's in-level UI (tutorial screens): Gears 3's own HUD is used |

## Texture streaming (done in chapter 1, 2026-11-11)

Gears 3 streams a texture when its top mips are in a texture cache: bulk data flagged as stored in a separate file
(0x01; Gears 3's own add 0x80, LZX), the file being `<TextureFileCacheName>.tfc` beside the packages (no header: the
mips back to back at their offsets), with `FirstResourceMemMip` the first mip kept inline. A mod's own cache was
proven earlier (tools/modpack.py: Gears 2 mips in `Textures_1.tfc` streamed in). COGBox lists a mod's added files in
the game's table of contents, so a converted chapter can ship its cache.

The converter (materialconv.TextureCache, recipe `stream_textures = true`): a chapter's converted textures keep every
mip; the ones over 128 texels go into `<persistent package>_Textures.tfc`, uncompressed (flags 0x01); the smaller ones
stay inline. `max_texture` no longer applies.

Checked in game (chapter 1): the game opens `GOW1_SP_Prison_P_Textures.tfc` beside its own caches
(`--gow3_mod_file_log`), and the converted textures draw correctly at the detail they had before. Every mip over 128
texels is only in that file, so Gears 3 reads uncompressed cache mips (flags 0x01). The persistent package went from
93 MB (512 cap) or 137 MB (uncapped, resident) to 47 MB plus a 95 MB cache. At the cell block's distances the engine
picks the same mips as the 512 cap (no visible difference there); the gain is the resident memory freed for the
streamed characters, and full size up close.

Still to compare fairly: Gears 1 rendered at 2560x1440 and the trilogy at 1920x1080 in these shots, so sharpness
between the games needs equal resolutions. Brightness does differ: the trilogy measured 25-35% darker than Gears 1 in
the same views (cell door, east corridor): the next lighting point (tone mapping and post-process: the recipe's
`look`, Gears 1's values over SP_Nightmare_P's).

## Regenerating

    python -P tools/g1g3map.py tools/recipes/gears1_ch1.ini --out build/scratch/g1g3/ch1_map.md

For a new chapter's recipe, run it before the first build: the classes Gears 3 lacks and the unmarked properties are
the conversion work that chapter needs beyond what the converter already does.
