# Moving mod support and the trilogy onto gow3-recomp

Plan for running this project's mods on [Flippage/gears-of-war-3-recomp](https://github.com/Flippage/gears-of-war-3-recomp)
(ReXGlue SDK, BSD-3), proposed in its [issue #25](https://github.com/Flippage/gears-of-war-3-recomp/issues/25).
That project becomes the Gears 3 base; this repository keeps the mod tools, the trilogy converter and the runtime
pieces mods need, offered to it under BSD-3 (the six files below were written for this project from scratch;
`runtime/kernel/io/file_system.cpp` descends from UnleashedRecomp and stays GPL, so nothing of it is ported).

Our runtime stays the trilogy's test bed until the trilogy plays the same on gow3-recomp.

## Their extension points (master 8107670)

| Need | gow3-recomp |
|---|---|
| Replace a game function | `dispatcher->SetFunction(<guest address>, &Hook)`, `void Hook(PPCContext& ctx, uint8_t* base)` (`src/gow3_mouse_aim.cpp`) |
| Game addresses | `src/gow3_addresses.h`, one block per executable (base 0.0.0.11, TU6) |
| Serve a host file at a guest path | `GOW2_VFS_OVERRIDES="guest/path=C:\host\file;..."`, read once at start (`sdk/patches/0005`) |
| Files the game folder lacks | `GOW3_VFS_OVERLAY="<game>\|<overlay>"`; files the game has win (`sdk/patches/0092`) |
| Coalesced bundles | `src/gow3_coalesced.cpp`: edited copy padded to its TOC size, HashSec SHA-1s patched (base: 0x835A0000-0x835A07AF) |
| DLC packages | `src/gow3_dlc.cpp` (ContentManager::InstallContent) |
| Settings | cvars, `config/gow3.toml` |

## The six files, mapped

| Ours | On gow3-recomp |
|---|---|
| `kernel/mods.cpp`, `mods.h`, `mod_manifest.h` (discovery, load order, merged TOC) | at start, before the VFS reads its variables: discover `mods\`, write the merged `Xbox360TOC.txt` (and per-language TOCs) to a cache folder, and hand every mod file plus the TOC to `GOW2_VFS_OVERRIDES` (replaced files) and an overlay folder (new files). No kernel hooks needed. |
| `kernel/mod_config.cpp` (config fragments into `Coalesced_<LANG>.bin`) | build on `gow3_coalesced.cpp`, which already rewrites bundles and patches HashSec; the size limit (a copy may not grow past its TOC size) goes away once the TOC entry is ours (merged TOC) |
| `ue3/campaign_chapters.cpp` (chapters 32+ into `Default__GearTypes.ChapterLevelNames`) | same logic on the game thread through a `SetFunction` hook; FName construction `sub_82302F28` (base). TU6 addresses still to find |
| `ue3/content_packages.cpp` (load and root `MPCH_*_SF` packages) | same, calling `UObject::LoadPackageAsync` (`sub_822B7328`, base) from a game-thread hook |

Each file gets a BSD-3 header (copyright KiltedKernel) when it goes over.

## Test tooling (our debug patch set, not for their release)

The trilogy is tested with: scripted input (`runtime/hid/hid.cpp`: walkto, route, face, look, shoot, fight,
teleport, capture), `ue3/prop_log.cpp` (GOW3_LOG_PROPS / GOW3_SET_PROPS), `ue3/kismet_trace.cpp`, the navigation
traces in `ue3/cover_trace.cpp`, `GOW3_LOG_POS`, and timed screenshots (`tools/playtest.ps1`). These port as a
separate patch set kept in this repository.

## Steps

1. Build gow3-recomp locally from the player's own `default.xex` (`scripts\build-sdk.ps1`, `scripts\build.ps1`).
2. Port the six files (BSD-3 headers) on a local branch; run the trilogy's chapter 1 with the converted mods folder.
3. Port the test tooling as a patch set; run the scripted prison routes there.
4. Offer the mod pieces upstream as a PR (or keep them as a patch set if they prefer).
5. Trim this repository to mod tools, converter, trilogy and the patch sets; point players at gow3-recomp.
