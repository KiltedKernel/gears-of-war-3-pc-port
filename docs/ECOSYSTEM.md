# The Gears recompilation projects (2026-10-31)

Who else is porting the Xbox 360 Gears games to PC, what each has, and where this project fits. Flippage (Gears 3)
proposed merging the projects into one package (gow3-recomp issue #25, then email with Santiago and Shagg); this file
is the background for that. All of them are bring-your-own-game.

| Project | Games | License | Base | Created | Stars |
|---|---|---|---|---|---|
| [Shagg88/COGBox](https://github.com/Shagg88/COGBox) | Gears 2, 3, Judgment (+ launcher) | MIT (own code) | ReXGlue SDK + own D3D12/Vulkan renderer | 2026-10-27 | ~240 |
| [santiquiroz/gears-of-war-2-pc-port](https://github.com/santiquiroz/gears-of-war-2-pc-port) | Gears 2 (PC + Android) | BSD-3 | ReXGlue SDK | 2026-10-14 | ~125 |
| [Flippage/gears-of-war-3-recomp](https://github.com/Flippage/gears-of-war-3-recomp) | Gears 3 | BSD-3 | ReXGlue SDK (Gears 2 port's patch series) | 2026-10-22 | ~46 |
| [OverkillLabs3/gears-judgement-recomp](https://github.com/OverkillLabs3/gears-judgement-recomp) | Judgment | BSD-3 | ReXGlue SDK | earlier | ~127 |
| this project (gears3-pc) | Gears 3 (+ a Gears 2 port) | GPL-3 (UnleashedRecomp lineage) | XenonRecomp + own kernel + Xenia D3D12 | 2026-10 | |

## What each has that matters to us

**COGBox** (Shagg): the furthest along as a product. One launcher (carousel, per-game settings, 11 languages,
self-update, problem reports, DLC, mods). Own renderer (any resolution, SMAA/FSR/DLSS/XeSS, HDR, Vulkan
experimental, Proton). Gears 3 made for **Title Update 6** by default. Code is layered: `layer2_ue3` (engine-level,
shared idea across games: config bundle edits, package patches, mods, console), `layer3_gow3` (game-specific).
- **Mods (0.2.0)**: `<Game>\mods\<mod>\` laid out like the game's data folder, optional `mod.ini` `[Mod] Name,
  Version, Author, Description`; the launcher keeps order and on/off (`CogBox.ini [Mods]`) and passes the enabled
  mods to the game as `--ue3_mods` (load order, later wins). The game (`Gears3-Native/src/layer2_ue3/
  config_install.cpp`) shows each top-level game folder through a read-only **overlay device** in which mod files
  replace or add at any depth, rewrites the **TOC** lines (size, uncompressed size, MD5; copies in `<user
  data>\mod-overlay`), and updates the **startup SHA-1 table** for replaced files. Programs are never taken from a mod.
- Also: `--ue3_ini_edits` (settings through Coalesced edits), `--ue3_package_patches` (patched script packages).
- Missing compared with ours: config fragments merged across mods, mod.ini rules (priority, requires, side, game,
  content), the multiplayer fingerprint, new campaign chapters, content packages, Xbox 360 bare-file mods, and the
  trilogy.

**Gears 2 port** (Santiago): the origin of the shared SDK patch series (native renderer, texture packs, FSR, KBM,
options menu); Android; System Link co-op for up to 4+ players; texture packs with a Real-ESRGAN pack builder. No
mod loader. Our own Gears 2 port (gears2-pc) overlaps it; in a merger his would be the Gears 2 base.

**gow3-recomp** (Flippage): Gears 3 on the same series: native renderer, DLSS/FSR, KBM, co-op, TU6 + DLC, launcher,
texture packs, `GOW2_VFS_OVERRIDES` / `GOW3_VFS_OVERLAY` file serving, Coalesced edits with HashSec. No mod loader.

## What this project brings (nobody else has it)

1. **Mod features beyond replacing files**: config fragments that stack across mods (`config\*.ini`, `+ - *`
   operators), mod.ini rules (priority, requires, side, game, content), a multiplayer fingerprint so every player of
   a session has the same mods, Xbox 360 bare-file mods, new campaign chapters (32-49) in Select Chapter, content
   packages (multiplayer characters in the campaign).
2. **The trilogy**: Gears 1 (and later 2) campaigns converted into Gears 3's engine as mods, from the player's own
   discs (tools/trilogy.py and its converters: levels, BSP, materials, Kismet, AI, cover, navigation meshes).
3. **Research**: Gears 3 navigation (pylons, cross-pylon edges, the pylon-graph planner), cover, Kismet behaviour vs
   Gears 1, UE3 package formats for versions 374/575/828 (docs/ENGINEERING_LOG.md, REVERSE_ENGINEERING.md).
4. **Test tooling**: scripted playtests (walk, route, shoot, fight), property / Kismet / navigation tracing.

## The merger (2026-10-31)

Flippage proposed, by email to Shagg, Santiago and us ("Gears Definitive Collection"), merging the projects into
**Shagg's COGBox** and working on that together (DLSS/FSR with the HUD kept out of the upscaler from gow3-recomp,
Android and 3+ player co-op from the Gears 2 port, pre-game shader compilation from COGBox), coordinating on Discord,
and having Claude plan the combination from all repos. We agreed. So the runtime our mod features target is
**COGBox's Gears 3** (Title Update 6 by default), through its `--ue3_mods` overlay (`Gears3-Native/src/layer2_ue3/
config_install.cpp`) and its launcher's mod list (`Gears2-Native/cogbox/src/mods.cpp`).

## Direction that follows

Whatever runtime the merged package keeps, our mod features should arrive as **a portable core plus thin adapters**
(docs/REXGLUE_PORT.md):
- core, standard C++ only, BSD-3 (MIT-compatible): manifests (ours and COGBox's `[Mod]` keys), load order, file
  mapping, config fragment merge, merged TOC lines, multiplayer fingerprint;
- an adapter per runtime: COGBox's overlay device and `--ue3_mods`, or gow3-recomp's overrides/overlay;
- game hooks (campaign chapters, content packages) with addresses for the base disc **and Title Update 6**, the
  version COGBox and gow3-recomp ship by default;
- the trilogy converter stays a separate tool that outputs ordinary mod folders.

Licenses: MIT and BSD-3 mix freely; our GPL-3 runtime code that descends from UnleashedRecomp
(`kernel/io/file_system.cpp`) never goes into the merged package.
