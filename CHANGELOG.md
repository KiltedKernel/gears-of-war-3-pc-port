# Changelog

## 0.1.0 (2026-10-13)

First release: Gears of War 3 as a native Windows program, made from the player's own disc image.

**Install**
- `Setup.cmd`: an installation wizard (disc image, install location, shortcuts on the desktop and in the Start menu,
  build tools, progress with a log, finish). Missing build tools (Visual Studio 2022 Build Tools with clang-cl, CMake
  and Ninja; Python 3; Git) are installed with winget when the player agrees. Rerunning continues after a failure.
- `tools\setup.ps1`: the same from a console. It checks the disc's `default.xex` is the version the port was made for,
  recompiles the game code (XenonRecomp, pinned and patched), builds Xenia's GPU and audio (pinned and patched) and the
  port, and draws the keyboard and mouse prompt icons.

**Game**
- Boots from the disc's files; logo movies (or `-nointro`); title screen, menus and options.
- The whole campaign loads and plays: every chapter of Acts I-V was booted and played for minutes in tests, with
  cutscenes, combat, turret and vehicle sections; checkpoints save and Continue loads them.
- Offline modes: Campaign (Standard, Arcade, Select Chapter), Horde, Versus with bots and Beast (Private).
- Frame rate unlock (30 to 240 fps; game speed unaffected), resolution scaling, 16x anisotropic filtering, effect
  toggles, vsync, fullscreen (F11 / Alt+Enter).
- Keyboard and mouse: native mouse look, rebound actions, menus with the mouse (hover, click, wheel, sliders), button
  prompts that follow the device used last (drawn keyboard and mouse icons; Gears of War: Reloaded's as an opt-in).
  Controllers through XInput.

**Launcher**
- Display, graphics, effects, controls, audio and game settings (`build\gow3.ini`).
- Mods tab: installed mods in load order, on/off, load order, conflicts, install from a .zip, remove (to the Recycle
  Bin).

**Mods**
- Folders in `build\mods` with a `mod.ini` replace or add game files; priorities; the table of contents is merged at
  startup.
- Configuration fragments (`config\*.ini`) merged into the game's Coalesced files at startup; several mods stack.
- New campaign chapters (numbers 32-49) in Select Chapter, loading their own maps.
- `tools\modtool.py` (create, check, pack, install), `tools\campaign.py` (chapter fragments), examples in
  `examples\mods` (a new act and chapter, a menu string, unlock every chapter).

**For developers**
- Reverse-engineering documentation (`docs/REVERSE_ENGINEERING.md`) and the development journal
  (`docs/ENGINEERING_LOG.md`); tools for UE3 packages, Coalesced files, UnrealScript bytecode, textures and the XEX.
- Scripted playtests (`tools\playtest.ps1`, `tests\`) isolated from the player's saves, mods and settings; campaign
  coverage (`tools\chapter_test.py`).

**Known limits**
- No Xbox LIVE: online modes and the party features are unavailable.
- The build needs a C++ toolchain on the player's PC (the recompiled game code is made from their disc).
- Chapters have been played for their first minutes in tests, not each to its end.
