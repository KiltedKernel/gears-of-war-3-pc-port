# Modding the Gears of War 3 PC port

This port runs Gears of War 3 natively on PC. Mods replace or add game packages and configuration, add campaign
chapters, and stack with each other.

Mods contain material derived from the games. Share them only with people who own the games, and never
redistribute the games' own files unmodified.

## Installing a mod

Copy the mod's folder into `mods\` next to the game's executable (`build\mods\` in a source build):

```
build\
  gow3.exe
  mods\
    BrighterGridlock\
      mod.ini
      GearGame\CookedXbox360\MP_GridlockG3.xxx
```

That's all: start the game. The launcher's **Mods** tab lists the installed mods: tick or untick a mod to turn it on or
off, use **Up** / **Down** to change the load order (later mods win when two change the same file), **Install .zip...**
to add a shared mod, and select a mod to see what it changes and which other mods it overlaps. (Without the launcher:
set `enabled = false` in a mod's `mod.ini` to turn it off.)

The mod tool does the same from the command line:

```
python -P tools/modtool.py install BrighterGridlock-0.1.zip     # a shared mod into mods\ (--replace to update it)
python -P tools/modtool.py disable BrighterGridlock             # off, still installed
python -P tools/modtool.py enable BrighterGridlock              # on again
python -P tools/modtool.py pack BrighterGridlock                # your mod as one .zip to share (checked first)
```

`install` accepts a zip with one mod folder holding a `mod.ini`. It refuses paths that would land outside that
folder, and won't overwrite an installed mod unless you pass `--replace`.

## What a mod is

A folder with a `mod.ini` and game files at the same relative paths as on the disc.

- A file at `GearGame\CookedXbox360\X.xxx` in your mod **replaces** the game's `X.xxx`.
- A file that the game doesn't have is **added**. The game finds packages through its table of contents
  (`GearGame\Xbox360TOC.txt`), and the runtime builds that list for you from the disc and every enabled mod, so a mod
  never ships or edits a TOC. If a mod contains one, it is ignored.

### mod.ini

```ini
[mod]
name = Brighter Gridlock
version = 0.1
author = Your name
description = One line about what the mod does.
priority = 0
enabled = true
side = everyone
```

| key | meaning |
|---|---|
| `name` | shown in the log and by `modtool.py mods` (default: the folder's name) |
| `version`, `author`, `description` | free text |
| `priority` | whole number, default 0; when two mods have the same file, the higher priority wins (equal: the later name alphabetically) |
| `enabled` | `false` / `0` / `no` turns the mod off |
| `side` | who needs the mod in multiplayer: `everyone` (default), `host` or `client` (see [Mods in multiplayer](#mods-in-multiplayer)) |
| `requires` | other mods it needs, by name or folder, separated by commas (`requires = Horde: big bank`). If one is missing or off, the game leaves this mod off and says why |
| `content` | the game's own content packages the mod uses where the game would not load them, separated by commas (`content = MPCH_COGMarcusG2MP_SF`: a multiplayer character in a campaign level). Loaded once the game is up and kept loaded, so a level's imports of their objects resolve |
| `game` | the game it is made for: `gears3` (default). A mod for another game (`gears2`) is left off |

Files placed directly in `mods\` (not inside a folder with a `mod.ini`) still work, as one mod named
"(loose files)" at priority 0.

### Load order and conflicts

When the game starts it writes to `build\gow3.log`:

```
mods: Night Gridlock 1 (priority -5)
mods: Brighter Gridlock 0.1 by someone (priority 0)
mods: conflict on geargame\cookedxbox360\mp_gridlockg3.xxx: Night Gridlock, Brighter Gridlock (the last wins)
```

`python -P tools/modtool.py mods` shows the same from the command line, without starting the game.

## The mod tool

`tools/modtool.py` is the command line for creators (Python 3.10+):

```
python -P tools/modtool.py mods                      # installed mods, files, conflicts
python -P tools/modtool.py new-mod MyMod --name "My mod" --author "Me"
python -P tools/modtool.py check MyMod               # every package read back (Gears 3 packages are version 828)
python -P tools/modtool.py pack MyMod                # one .zip to share
python -P tools/modtool.py install MyMod-0.1.zip     # into mods\ (--replace to update)
python -P tools/modtool.py enable|disable MyMod
```

## Game configuration

Gears 3 keeps all its .ini files packed in `GearGame\CookedXbox360\Coalesced_INT.bin` (menus, chapters, weapons,
game modes...). `tools/coalesced.py` reads and writes it:

```
python -P tools/coalesced.py dump  <Coalesced_INT.bin> config.txt          # readable text, one ##### header per ini
python -P tools/coalesced.py merge <Coalesced_INT.bin> <out.bin> my.ini   # apply an ini fragment
```

A fragment names its ini with `##### <file>` (for example `##### Xbox360-GearGame.ini` or `##### GearGame.int`),
then sections and keys, UE3 style: `Key=v` replaces the key, `+Key=v` adds a value, `-Key=v` removes one, and
`*Key=(Identifier=X,...)` replaces the entry with the same first field (a menu entry).

**Ship fragments, not the whole file.** Put them in the mod's `config\` folder (any number of `.ini` files):

```
MyMod\
  mod.ini
  config\
    chapters.ini
```

When the game starts, the runtime merges every enabled mod's fragments, in load order, into each language's
`Coalesced_<LANG>.bin` (the disc's, or a whole file a mod ships) and serves the merged file. So several mods can
change the configuration at once. The log lists each fragment (`mods: config chapters.ini from My mod: 13 lines`);
mistakes are logged with the file and line. `python -P tools/modtool.py check MyMod` checks fragments too.

Working examples are in `examples/mods/` (see [Sample mods](#sample-mods)). Copy one into `mods\` (or install its
folder as a .zip from the launcher).

## Mods in multiplayer

Every mod says who needs it with `side` in its `mod.ini`:

| side | changes | in a multiplayer session |
|---|---|---|
| `client` | only what you see and hear: menus, text, the HUD, the camera | yours alone; nobody else needs it |
| `host` | the rules of the game mode (Horde's money, Versus round settings): the host's game runs the mode | acts in matches you host; others need not have it |
| `everyone` (default) | anything every player's game simulates or loads: packages, maps, weapon and character stats | every player must have the same ones |

When you join someone (a friend's game or a server), your `everyone` mods are compared with the session's: the
names, versions and files' sizes, as one fingerprint. If they differ you are not let in, and the game says which
mods the session uses ("this session needs the mods Lancer ammo 1.0 (you have none)"). `client` and `host` mods are
never compared. Games of version 0.2 (no mod check) count as having no mods. `python -P tools/modtool.py mods`
prints your multiplayer mods and their fingerprint.

Pick the side carefully: a mod marked `client` or `host` that changes something both games simulate (weapon
damage, ammunition, movement) lets players with different settings play together, and their games disagree.
When unsure, leave the default, `everyone`.

### Servers

`gow3_server.exe` can require a set of mods: give it a mods folder (`--mods <folder>`, or `mods =` in its
`server.ini`, `examples/server/server.ini`). Copy the mod folders players install there (the server reads each
`mod.ini` and the names and sizes of the mod's files and fragments; it never loads them). The server prints the
mods players need and their fingerprint; players with other mods are turned away with the list. Without `--mods`,
each room plays with the mods of the first player in it.

## Mods made for the Xbox 360

Mods for the console version are usually shared as the changed game files: a `Coalesced_INT.bin`, some `.xxx`
packages, sometimes inside the game's own folders, often with an edited `Xbox360TOC.txt`. The port takes them as
they are:

- **Install** the .zip with the launcher (**Install .zip...**) or `modtool.py install`: an archive without a
  `mod.ini` goes into a new mod folder named after the archive, and a `mod.ini` is written for it (side
  `everyone`).
- **Files go where the game expects them**: a path containing a `GearGame` folder is used from there (so
  `Gears of War 3\GearGame\CookedXbox360\X.xxx` works); bare packages (`.xxx`, `.upk`), texture caches (`.tfc`)
  and `Coalesced_*.bin` go to `GearGame\CookedXbox360`; bare movies (`.bik`) to `GearGame\Movies`.
- **The TOC is ignored**: the runtime lists every mod file in the game's table of contents itself.
- **A whole Coalesced file** is used as the base, and other mods' `config\` fragments still apply on top of it.
- **Code patches** (a modified `default.xex`, Xenia `.patch.toml` files) cannot apply: the port's game code is C++
  recompiled on your PC. `modtool.py check` points them out.

Packages must be Gears 3 packages (version 828); `modtool.py check` reads each one
back.

## Sample mods

`examples/mods/` holds one sample for each kind of mod. All of them are configuration fragments: they contain no game
data, so they can be shared freely.

| mod | side | what it shows |
|---|---|---|
| `WiderView` | client | a wider camera field of view (`GearCamera.ini`): one player's own view |
| `ExampleMenuText` | client | a menu string (`GearGameUI.int`) |
| `UnlockAllChapters` | client | every chapter in Select Chapter (`IsAlwaysUnlocked`) |
| `HordeBank` | host | Horde starts with $5,000: a game mode's setting, which the host's game applies; players who join see it |
| `HordeInterest` | host | builds on `HordeBank` with `requires`: off (and saying why) without it |
| `LancerAmmo` | everyone | more Lancer magazines (`GearWeapon.ini`, `GearWeaponMP.ini`): weapons are simulated by every game |
| `ExampleChapter` | everyone | a new act and chapter (co-op partners load the same chapter) |

`examples/server/` has a `server.ini` whose server requires `LancerAmmo`.

## New campaign chapters

Chapters are listed from config, and each one loads the map the runtime puts in the game's chapter table. Gears 3
reserves chapter numbers 32-49 for downloadable campaigns. A new chapter takes one of those numbers:

```ini
##### Xbox360-GearGame.ini
[Act6 GearCampaignActData]
ActNum=5
bRequiredActForGameCompletion=false
ScreenshotPathName=UI_Portraits_G3.SP.T_Portraits_SP_A1C0_Act1
[Chapter32 GearCampaignChapterData]
ChapterNum=32
ActNum=5
MapName=sp_mymap_p
ScreenshotPathName=UI_Portraits_G3.SP.T_Portraits_SP_A1C0_Act1
IsAlwaysUnlocked=true
##### GearGame.int
[Act6 GearCampaignActData]
ListName=VI: My Campaign
DisplayName=My Campaign
[Chapter32 GearCampaignChapterData]
ListName=1: First Chapter
DisplayName=First Chapter
ChapterNumDesc=Chapter 1
```

`python -P tools/campaign.py fragment --chapter 32 --act 6 --map sp_mymap_p --title "First Chapter" --act-title
"My Campaign" --out MyMod\config\chapters.ini` writes such a fragment into a mod. The new act appears in Campaign > Standard > Select Chapter after Act V. When the game starts, the runtime reads every
chapter 32-49 from the active config and loads its `MapName` for it (`runtime/ue3/campaign_chapters.cpp`). The map is
a persistent level package (`sp_mymap_p.xxx`) in the mod.

## Formats and further reading

- `docs/REVERSE_ENGINEERING.md`: how the port works and what is known about the game (addresses, formats).
- `docs/ENGINEERING_LOG.md`: the full development journal.
- `tools/upk.py`, `tools/upkwrite.py`: read and rewrite Gears 3 packages (big-endian, version 828).
- `tools/coalesced.py`: the packed configuration. `tools/uscript.py`: UnrealScript bytecode as readable code.
- `tools/texture2d.py`: textures. `tools/campaign.py`: the chapter table.

## Debugging

- `build\gow3.log` lists the mods loaded, conflicts, and every file a mod overrides (`files: mod override ...`).
- `GOW3_LOG_POS=1` logs the player's and enemies' positions every 2 seconds, which helps when placing things.
- Gears 3 kills a player who stays off the level's navmesh for a few seconds (an anti-exploit check). While a
  new level's navmesh is incomplete, `GOW3_NO_PLAY_AREA_KILL=1` turns that off so you can walk everywhere.
