# Gears of War 3 — PC port

A native Windows build of Gears of War 3, made from **your own copy of the game**. The Xbox 360 code is translated to
C++ ahead of time (static recompilation with XenonRecomp), runs on a small reimplementation of the console's kernel,
and draws with Xenia's Direct3D 12 GPU emulation. A launcher configures it, and a mod system lets creators replace or
add packages, configuration and campaign chapters.

**No game files are included.** The setup reads your disc image and produces everything game-derived on your PC.

Status: boots, menus, the Act 1 campaign and Horde play; mods and new campaign chapters work. See [docs/STATUS.md](docs/STATUS.md) for what is verified and the roadmap.

## What you need

- A disc image (ISO) of your own Gears of War 3 disc. The setup checks that `default.xex` matches the version this
  port was made for (`tools/default_xex.sha256`).
- Windows 10/11, a Direct3D 12 GPU, an x86-64-v3 CPU (Haswell / Zen or newer).
- To build: Visual Studio 2022 with **Desktop development with C++**, LLVM (`clang-cl`), CMake, Ninja, Git for
  Windows and Python 3 (the setup installs `pycryptodome`; `numpy` and `Pillow` are used for the key prompts).
- About 25 GB free (the extracted disc is about 8 GB; Xenia and the build take the rest).

## Setup: your ISO to a PC port

Double-click **`Setup.cmd`**: choose your disc image and press **Convert**. The window checks the build tools, offers
to install the missing ones, shows the progress of each step and keeps a log (`build\setup.log`). When it is done,
**Open launcher** starts the game's launcher. The same setup from a console:

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup.ps1 -Iso "D:\Games\Gears of War 3.iso"
```

Missing build tools are listed first, and the setup offers to install them with winget: one Visual Studio 2022 Build
Tools install (C++ compiler, Windows SDK, clang-cl, CMake, Ninja), plus Python and Git if needed. Add
`-InstallPrerequisites` to install without asking, or run `tools\setup.ps1 -CheckOnly` to only see what is missing.

The setup:
1. extracts the disc next to this folder (`..\gow3-decomp\disc`) and checks the game version;
2. decrypts `default.xex` for the analysis tools;
3. fetches and patches XenonRecomp and Xenia at pinned commits and builds them;
4. recompiles the game code into `ppc\` and runs the instruction tests;
5. builds `build\gow3.exe` and `build\gow3_launcher.exe`, makes the keyboard prompts, and puts shortcuts on the desktop.

The first run takes a while. If a step fails, fix the cause and rerun the same command: finished steps are skipped.
`-Game` and `-Xenia` choose other folders; `-NoShortcut` skips the shortcuts.

## Playing

Run **Gears of War 3** (`build\gow3.exe`) or **Gears of War 3 Launcher** (`build\gow3_launcher.exe`): the launcher's
options window (display, resolution, frame rate, effects, mouse, prompts, audio) saves `build\gow3.ini` and starts
the game. `-nointro` skips the logo movies. Saves and settings caches live in `%LOCALAPPDATA%\GoW3Recomp`.

| Keyboard / mouse | Controller |
|---|---|
| WASD | left stick |
| mouse | camera (native mouse look) |
| left mouse | fire (RT) |
| right mouse | aim (LT) |
| Space or Left Shift | A: cover, roadie run, use |
| R | RB: reload / active reload |
| Tab | LB: tactical map |
| E | X: pick up / interact |
| F | B: melee, chainsaw |
| Q | Y: point of interest |
| 1-4 or arrow keys | d-pad: weapons |
| C / Z (middle mouse) | left / right stick click |
| Enter, or Esc in play | Start (pause) |
| Esc in menus | B: back (not on the main menu's first page) |
| Backspace | Back |
| F11 or Alt+Enter | fullscreen |

In menus the mouse works too: hover selects, left click accepts (Start on the title screen), right click goes back, the
wheel scrolls. On the main menu's first page Esc and right click do nothing; the controller's B still returns to the
title screen as on the console.

## Mods

Each folder in `build\mods\` with a `mod.ini` adds or replaces game files; several mods stack by priority, and the
game's table of contents is merged automatically. Mods can change the game's configuration and add campaign chapters
to the Select Chapter menu. Players manage mods in the launcher's **Mods** tab (turn them on or off, change the load
order, install a shared .zip); `tools\modtool.py` does the same from the command line and creates, checks and packs
mods. See
[docs/MODDING.md](docs/MODDING.md).

## How it works / reverse engineering

[docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md) describes the port's structure and what is known about the
game (functions, data layouts, file formats), with the tools that read them. [docs/ENGINEERING_LOG.md](docs/ENGINEERING_LOG.md)
is the full development journal.

## Developing

- `bash tools/rebuild.sh`: recompile (when `config/` changed) and rebuild.
- `cmd /c tools\build.cmd`: rebuild the runtime only.
- `tools\playtest.ps1 -Script tests\<script>.txt -Seconds N -Shots "a,b"`: scripted input (tests/), screenshots in
  `build\shots`, log in `build\gow3.log`. Tests use their own saves and profile (`build	estdata`, `-Fresh` starts
  empty) and their own mods (`-Mods "examples\mods\X"`), never the player's (`GOW3_DATA_DIR`, `GOW3_MODS_DIR`).
- `GOW3_COMMAND_LINE` adds to the game's command line, e.g. a map URL to start on without the menus.
- `python tools\chapter_test.py [0-30]`: campaign coverage. Boots each chapter directly, plays generic input, and
  reports crashes, the map loaded, frame rates and black or frozen screens (`build\coverageeport.md`, one contact
  sheet per chapter).
- Debug switches are environment variables `GOW3_*`, documented in the files that read them.
- The package tools (`tools/upk.py` and those built on it) decompress with `tools\xpack\xpack.exe`: build it once
  with `cmd /c tools\build_xpack.cmd` (after setup, which fetches the LZX decoder with XenonRecomp).

## License

GPL-3.0 (see LICENSE): the runtime's kernel, threading and heap code comes from Unleashed Recompiled (GPL-3.0). Third
party components keep their licenses (runtime/thirdparty). Gears of War is a trademark of Microsoft; this project
contains no game assets or code.
