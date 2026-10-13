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

That's all: start the game. To turn a mod off without deleting it, set `enabled = false` in its `mod.ini`.

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
```

| key | meaning |
|---|---|
| `name` | shown in the log and by `modtool.py mods` (default: the folder's name) |
| `version`, `author`, `description` | free text |
| `priority` | whole number, default 0; when two mods have the same file, the higher priority wins (equal: the later name alphabetically) |
| `enabled` | `false` / `0` / `no` turns the mod off |

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
`*Key=(Identifier=X,...)` replaces the entry with the same first field (a menu entry). The merged file goes in the mod
at `GearGame\CookedXbox360\Coalesced_INT.bin`.

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
"My Campaign"` writes such a fragment. The new act appears in Campaign > Standard > Select Chapter after Act V. When the game starts, the runtime reads every
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
