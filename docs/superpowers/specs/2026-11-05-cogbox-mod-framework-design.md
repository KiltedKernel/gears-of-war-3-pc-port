# COGBox mod framework: runtime layer and base mods (design)

Status: draft for Shagg and Flippage (COGBox maintainers). The mod-facing parts are written so outside mod creators
can follow them later. Author: KiltedKernel (with Claude). Date: 2026-11-05.

## 1. Why

Mods for the UE3 Gears games are data: packages, maps, config fragments. They cannot add UnrealScript (the game's
script is cooked), so anything that needs logic has to come from the runtime. Today each mod reaches into Gears 3's
internals by hand, and the first real campaign mod (the trilogy: Gears 1 chapters converted into Gears 3) shows what
that costs:

- **Menu slots collide with the game itself.** The trilogy declares `[Act6 GearCampaignActData] ActNum=5` and chapters
  32-34 in Gears 3's config. On the base disc that shows "VI: Gears of War" in Select Chapter. Title Update 6 (what
  COGBox runs) rewrote the act and chapter lists for RAAM's Shadow (`ConstructActList`, `ConstructChapterSelectData`,
  `DLC2_ACT_NUM = 5`, `DLC2_START_CHAPTER = 31`, `IsCampaignDLC1Installed`): the act is hidden, and moving it to
  ActNum 6 hides it too. A second campaign mod would also collide with the first: both would pick chapter 32.
- **Startup cost lands on the player.** The mods' character packages are loaded on the game thread at the title
  screen: a 35 s freeze before the title answers (COGBox 0.4.5 log, `gow3_tick_log max 33916 ms`).
- **Engine semantics differ between games.** Gears 1 put players back at the checkpoint actor on a load; Gears 3
  restores them where they stood. Each converted checkpoint now needs a converter-made teleport. Every such difference
  is solved privately, per mod, today.
- **Nothing tells a creator what went wrong.** A hidden act, a slot clash or a missing dependency fails silently.

The goal is a stable base: mods declare what they add, COGBox decides how it is wired into the game, and a mod built
against it keeps working across title updates, DLC and other mods.

## 2. Goals, non-goals, anti-goals

Goals
- A **mod API** in COGBox's runtime: services a mod declares in its `mod.ini`, with a version number.
- **Campaigns as a first-class mod type:** a mod declares a campaign and its chapters; COGBox gives out the act and
  chapter slots, lists them in the menus (TU6 and disc), starts them, and keeps their progress apart.
- **Base mods:** data-only mods others depend on (`requires=`), e.g. a Gears 1 base with shared characters and
  textures, so content is not duplicated per mod.
- **Diagnostics:** every framework decision is logged on one prefix, and problems reach the launcher.
- **Developer mode:** the debugging tools mod work needs, behind one launcher switch.

Non-goals (for this version)
- New gameplay code in mods (no scripting language).
- Multiplayer content mods beyond what the existing fingerprint already handles (maps and modes come later).
- Gears 1 and Gears 2 runtimes. The design should allow them, and Judgment is in scope where it shares Gears 3's code.

Anti-goals (it is a failure, even if it works, if)
- an unmodded game behaves differently in any way;
- a mod has to hard-code engine slot numbers, addresses or file offsets;
- a working mod breaks when a newer COGBox ships without the mod changing;
- installing a mod needs anything beyond dropping its folder in `mods\` and ticking it.

## 3. Constraints

- Both Gears 3 builds COGBox ships (base disc and TU6), and the player's own files (no game data in COGBox or mods).
- Must not disturb DLC: RAAM's Shadow's act and chapters stay where TU6 expects them.
- Co-op and System Link: players must agree on the mods (the existing fingerprint) and on the slots given out
  (allocation must be deterministic from the enabled mods and their order, not first-come).
- Saves: campaign progress and checkpoints of a mod campaign must not overwrite or corrupt the stock campaign's.
- Licensing: COGBox is MIT; our modcore is MIT; nothing GPL enters.

## 4. Shape

Two layers.

**Layer A: the mod runtime (native, in COGBox).** Services in `Gears2-Native/src/layer2_ue3` (shared UE3 code) with
per-game adapters in `layer3_*`, the same split the mod support (PR #122) already uses. Each service:
- reads its declarations from the enabled mods' `mod.ini` (after the existing `requires` / `game` / `enabled` rules);
- logs what it decided on the prefix `mod_api:`;
- is gated by the mod's declared API level, so older mods keep the behaviour they were made for.

**Layer B: base mods (data).** Ordinary mod folders others depend on. COGBox treats them like any mod; the convention
is the value.

### 4.1 Manifest (mod.ini, API level 1)

```ini
[Mod]
Name = Gears of War (trilogy): 14 Years After
Version = 0.2
Api = 1                       ; the mod API level it was made for
Requires = Gears1Base         ; already supported (mod extras)

[Campaign]
Id = gears1                   ; stable id: progress and saves are keyed by it, never by slot numbers
Title = Gears of War
Order = 10                    ; where it sorts among campaigns

[Chapter.1]
Map = GOW1_SP_Prison_P
Title = 14 Years After
Location = Jacinto Maximum Security Prison
Time = Emergence Day + 14 years
Screenshot = UI_Portraits_G3.SP.T_Portraits_SP_A1C0_Act1
ContentPackages = MPCH_COGDomG2MP_SF, MPCH_COGMarcusG2MP_SF
```

Chapters of one campaign may come from several mods (the trilogy ships one chapter per mod); they join by `Id`.

### 4.2 Campaign registry

1. **Allocation.** At start-up, the enabled campaigns are sorted by `Order`, then mod load order. Each gets the next
   free act number and each chapter the next free chapter slot in Gears 3's DLC range (32-49), skipping slots the
   game or installed DLC use. Results are logged (`mod_api: campaign gears1: act 6, chapters 32-34`).
2. **Config.** The registry writes the act and chapter sections itself, into the merged config the mod support
   already builds. Mods stop shipping raw `GearCampaignActData` fragments; a fragment that still does is honoured for
   API level 0 and logged as such.
3. **Map loading.** As today: `Default__GearTypes.ChapterLevelNames[N]` is filled at run time (mod_chapters.cpp).
4. **Menu integration (open: section 6.1).** The registry's campaigns must appear in Select Chapter on TU6.
5. **Progress and saves.** Version 1 relies on allocation being deterministic: the same enabled mods in the same
   order get the same slots, so the game's own slot-keyed progress and checkpoints stay valid. Adding or removing a
   campaign that sorts earlier moves later campaigns' slots; the launcher warns before that happens. Keying progress
   by campaign `Id` (surviving any change) is a later version: it needs the profile's progress data understood first.
   (Author's call: keeps v1 inside what the game already stores.)

### 4.3 Content packages

The mod-declared packages (the squad, shared characters) load **asynchronously, before the chapter that needs them
starts**, not on the game thread at the title screen. A chapter waits for its packages at its loading screen, where
waiting is expected. Removes the 35 s title freeze.

### 4.4 Engine-difference adapters

Behaviour a converted game needs that Gears 3 does not do, declared per campaign:

```ini
[Campaign]
CheckpointPlacement = actor   ; on a load, players go to the checkpoint actor (Gears 1), not where they saved
```

The trilogy's converter does this in data today (a teleport added to each checkpoint's load event). Moving it into
the runtime is optional: the rule is that each such difference has one documented home, and the converter's
data-side fixes are listed alongside the runtime ones so creators can find them.

### 4.5 Developer mode

One launcher switch (and `--mod_dev`) turns on the tools mod work needs, ported from KiltedKernel's runtime:
property logging and holding (`LOG_PROPS` / `SET_PROPS` / `WATCH_PROP`), the Kismet trace, a scripted input driver
for repeatable tests, an F8 log marker, and fatal engine error text in the log. Off by default; no cost when off.

### 4.6 Diagnostics and validation

- Every service logs on `mod_api:`; problems (a missing dependency, an unknown key, more chapters than free slots, a
  declared map that is not in the mod) are listed in the launcher's mod section with the reason.
- A `modcheck` tool (offline) runs the same checks on a mod folder before it is shared, plus the existing
  publish rules (no game files, no executables).

## 5. Base mods

- **Gears1Base** (the trilogy's first): shared textures (today's TrilogySharedTextures), converted characters,
  stock converted Kismet pieces, the Gears 1 objectives text. Chapter mods `Requires = Gears1Base`.
- Base mods follow the same manifest; their `Api` level gates them the same way.
- Later bases (Gears2Base, shared UI pieces) only when a second consumer exists.

## 6. Open questions

### 6.1 Menu integration on TU6 (research first)

**Found (2026-11-05):** TU6's `UIWidget_MainMenu.ConstructActList` (decompiled from ScriptPatch_GearGame.bin with
tools/uscript.py against the disc's GearGame.xxx tables) hard-codes five acts: a new act row is only added
`if (I < 5)` (bytecode `2C 05`, script +0x272 on disk) and after the loop `CurrentList.SetFloat('length', 5.0)`
(`1E 40A00000`, +0x4D9). The base disc has neither cap. Acts must also be contiguous (the loop looks for ActNum = 0,
1, 2 ... up to the number of act providers), so mod acts take ActNum 5, 6 ... with no gaps (ActNum 6 alone was never
found, on either build). **So approach 1 becomes concrete:** a runtime *script constant* service that, once the patched
function is loaded, finds those two constants in its Script array by pattern and raises them to 5 + the registry's
act count; acts get contiguous numbers from 5. Still to read: `ConstructChapterSelectData` (its decompile stops on a
name the patch adds; the decoder needs the patch's own name table) for a similar chapter or DLC filter, and how the
RAAM's Shadow act (`DLC2_ACT_NUM` 5) is listed when the DLC is installed (then mod acts start after it).

Original research list:
- Read the patched `ConstructActList` / `ConstructChapterSelectData` (TU6 `ScriptPatch_GearGame.bin`: a small package
  of replacement script; LZX, decodes with the trilogy's tools) to see exactly what they filter on.
- Candidate approaches, in order of preference:
  1. **Extend the stock list:** a runtime hook on those script functions (or on the native the list ends in) adds the
     registry's acts after the stock ones, on both builds.
  2. **Use the DLC path:** present mod campaigns through the campaign-DLC menu TU6 already has (the "DLC campaign"
     selection), if it can list more than RAAM's Shadow.
  3. **Fallback that always works:** start a chapter from the launcher (a campaign picker that travels straight to the
     chapter; the mod support already does this with `open GearGame_P?chapter=N`). Good for development even if 1
     or 2 lands.

### 6.2 For the maintainers
- Where the services live: in `Gears2-Native/src/layer2_ue3` (shared) as proposed, or a new `mod_api` folder?
- The `Api` level: one number for the whole API, or per service?
- Launcher UI: a "Campaigns" list (installed mod campaigns, their slots, problems) inside the existing MODS section,
  or its own tab?
- Developer mode: acceptable to ship in releases (off by default), or debug builds only?

## 7. Phasing

1. **Research 6.1** (menu integration on TU6) and agree this document with the maintainers.
2. **Manifest + campaign registry** (allocation, config writing, map loading, menu integration), with the trilogy as
   the first user; the launcher-start fallback alongside for development.
3. **Content packages** (asynchronous, per chapter): removes the title freeze.
4. **Developer mode** (the debug tools port), so further trilogy work runs on COGBox.
5. **Base mods + modcheck**, when the second campaign or creator appears.

## 8. Left to the builder

File names and code layout within the agreed folders; the exact log wording; how allocation is persisted between
runs (if at all, given it is deterministic); test coverage (unit tests for allocation and manifest parsing in
`gn_tests`, in-game verification for menus and loading).
