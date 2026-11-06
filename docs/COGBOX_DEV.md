# Working in COGBox (for agents moving this project's work there)

COGBox (github.com/Shagg88/COGBox, local clone `../cogbox`) is where the trilogy and mod support ship. This is the map
needed to add code there; COGBox's own notes are in `Gears3-Native/notes/` (28-mod-support.md: our merged work).

## Layout
- `Gears2-Native/` is the shared base: `src/layer0_xenos` (renderer, presenter, crash handler, in-game menu
  `ingame_menu.cpp`, dev tools `include/xenos/dev_tools.h`), `src/layer2_ue3` (UE3 helpers shared by Gears 2/3/J:
  config install, mod extras, package reads, shader prebuild, `main_menu_exit.cpp`), `patches/` (the SDK patches,
  `sdk-patchNN-*.diff` + README, applied in the SDK root with `git apply`), `cogbox/` (the launcher: `src/page.cpp`,
  `gears_settings.cpp` builds every game's command line; `test/settings_test.cpp`).
- `Gears3-Native/` is thin: `src/layer3_gow3/` (`gow3_app.h` installs everything in `OnPostSetup`; `game_patches.cpp`;
  `mod_chapters.cpp`; `main_menu_exit.cpp`; `mouse_look.cpp` + `ue3_mouse_look.h`; `extras.cpp`), `src/layer2_ue3/`
  (`objects.cpp`: UE3 reflection - FindClass / FindInstancesOf / properties; `modcore/`), two manifests
  (`gow3_manifest.toml` base disc, `gow3_manifest_tu6.toml` TU6: the release ships both `port-disc` and `port-tu6`).
- The SDK: ReXGlue, source in `../cogbox-tp/rexglue-src`, installed to `../cogbox-tp/rexglue-install`.

## Hooking the game
- **Mid-function hook:** a `[[midasm_hook]]` in the manifest (name, address, registers) calls a C++ function with the
  guest registers (`void Gow3MaxTickRate(PPCRegister& f1)  // after lfs f1,0x4C8(r3) at 0x8253D9B0`). Changing a
  manifest means regenerating the code (the recompile), so prefer the next two where they fit.
- **Function override:** define `extern "C" void sub_XXXXXXXX(PPCContext&, uint8_t*)` and call the original
  `__imp__sub_XXXXXXXX` (main_menu_exit.cpp's Localize hook). Per build: `#if defined(GOW3_TU6)` picks the address.
- **Game thread each frame:** `TickTimeline()` / `Gow3ModChaptersTick()` in game_patches.cpp run on the game thread;
  `ue3_mouse_look.h` hooks `UGameViewportClient::InputAxis` (first event of each frame for player 1).
- **File opens:** `Ue3ShaderPrebuildChainFileOpened(listener)` (several listeners chain).
- Addresses move between the base disc and TU6: `Gears3-Native/tools/analysis/xex_addr_map.py` maps them,
  `match.py`/`side.py` find a Gears 2 function's Gears 3 twin. This project's addresses (docs/REVERSE_ENGINEERING.md)
  are mostly base-disc: map them before use.

## Input
- The SDK's keyboard/mouse driver (`src/input/mnk/mnk_input_driver.cpp`): binds (`keybind_*`, exact modifier match,
  `g_bare_mod_mask`), mouse capture (`QueueMouseCaptureUpdate`), `SetMouseLookActive(bool)` (false frees the cursor:
  the menu mode a menu-mouse port needs), walk key (patch 60/63). Native mouse look is layer 3 (`ue3_mouse_look.h`).

## Building and testing
- Build recipes (from this project's job scratch, recreate from docs/ECOSYSTEM.md): SDK `cmake --build out\build\rel`
  + install; Gears 3 TU6 `cmake --build build\RelWithDebInfo-tu6 -j 6 --target gow3` (6 jobs: more ran out of memory).
- A runnable install: `../cogbox-release/app` (CogBox.exe; Gears 3 under `app/Gears3/`: `port-tu6/gow3.exe`, `logs/`
  one folder per session (args.txt, gow3.log, menu.txt), `user/` saves, `mods/<Name>` enabled in the launcher's Mods tab).
- Shagg's own agent runs jobs from `Gears3-Native/jobs/queue` (`Run-Jobs.bat`, tools/queue_driver.ps1).
- Not in COGBox yet (this repo's runtime only): the scripted input driver (GOW3_INPUT_SCRIPT), GOW3_LOG_PROPS /
  GOW3_SET_PROPS / GOW3_WATCH_PROP, GOW3_KISMET_TRACE, the F8 marker, Scaleform menu mouse (runtime/ui/gfx_menu.cpp).
  Porting them is the next step for testing the trilogy on COGBox.
