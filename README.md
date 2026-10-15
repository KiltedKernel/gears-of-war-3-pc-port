# Gears of War 3 — PC port

A native Windows version of Gears of War 3, made from **your own copy of the game**. The Xbox 360 code is translated
to C++ ahead of time (static recompilation with XenonRecomp), runs on a small reimplementation of the console's
kernel, and draws with Xenia's Direct3D 12 GPU emulation. A launcher sets it up, and a mod system lets creators
replace or add packages, configuration and campaign chapters.

**No game files are included.** The installation wizard reads your disc image and makes everything game-derived on
your PC.

Version 0.2.1: the whole campaign (all five acts) loads and plays, as do Horde, Versus with bots and Beast offline;
multiplayer over System Link, on a LAN, over the internet with a friend's code, or on a server; keyboard, mouse and controller;
mods. See [docs/STATUS.md](docs/STATUS.md) for what is
verified and what is next, and [CHANGELOG.md](CHANGELOG.md).

Sister project: the [Gears of War 2 PC port](https://github.com/KiltedKernel/gears-of-war-2-pc-port), which shares
this port's runtime, setup, launcher and multiplayer.

## Install

1. Get the source: download the release's source archive and extract it to a folder of your choice, or clone the
   repository.
2. Double-click **`Setup.cmd`**. The installation wizard asks for your disc image, where to keep the game files, and
   which shortcuts to make; it checks the build tools and can install the missing ones; then it builds the port,
   showing each step's progress and a log.
3. When it is done, start **Gears of War 3 Launcher** (desktop or Start menu): settings, mods, and Play.

**You need:** a disc image (.iso) of your own Gears of War 3 game disc (the wizard checks it is the version this port
was made for), Windows 10 or 11 (64-bit), a Direct3D 12 graphics card, a Haswell / Zen or newer processor, about
25 GB free, an internet connection, and about an hour the first time (most of it compiling). The build tools (Visual
Studio 2022 Build Tools with clang-cl, CMake and Ninja; Python 3; Git for Windows) are installed with winget if you
agree.

If a step fails, the wizard shows why; fix the cause and run Setup again: finished steps are skipped. The same setup
from a console:

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup.ps1 -Iso "D:\Games\Gears of War 3.iso" [-StartMenu] [-InstallPrerequisites]
```

`tools\setup.ps1 -CheckOnly` lists missing build tools. `-Game` and `-Xenia` choose where the game files and Xenia go
(remembered in `tools\local.cmd`); by default they go inside this folder (`game\`, `xenia\`).

## Playing

Run **Gears of War 3** (`build\gow3.exe`) or **Gears of War 3 Launcher** (`build\gow3_launcher.exe`). The launcher
sets display, resolution, frame rate (up to 240), effects, mouse, prompts and audio (`build\gow3.ini`), your gamertag and
multiplayer, manages mods, and starts the game. `-nointro` skips the logo movies. Saves live in `%LOCALAPPDATA%\GoW3Recomp`.

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
wheel scrolls. Button prompts follow the device you used last. The keyboard and mouse prompt icons are drawn by the
setup (`build\prompts`); if you own Gears of War: Reloaded on Steam, `python tools\make_prompts.py reloaded` uses its
keycap art instead (`python tools\make_prompts.py drawn` goes back).

Offline modes: Campaign (Standard, Arcade, Select Chapter), Horde, Versus and Beast (each under **Private**, with bots).
The Casual / Standard entries are Xbox LIVE matchmaking and ask for Xbox LIVE.

## Multiplayer

Play over the game's **System Link**. In the launcher's **Multiplayer** tab, set a gamertag and choose **With
friends** (games on your network find each other; over the internet, type a friend's code) or **On a server** (pick
one from the list, or **Run my own**). Then, in the game, open Party Options (Back on the main menu), pick **System
Link**, and create or join a party. The game tells you on screen who joins and what goes wrong. See
[docs/MULTIPLAYER.md](docs/MULTIPLAYER.md).

## Mods

Each folder in `build\mods\` with a `mod.ini` adds or replaces game files; several mods stack by priority and the
game's table of contents is merged automatically. Mods can change the game's configuration and add campaign chapters
to Select Chapter. The launcher's **Mods** tab turns mods on or off, changes the load order, installs a shared .zip and
removes mods; `tools\modtool.py` creates, checks and packs them. Examples are in `examples\mods` (one unlocks every
chapter in Select Chapter). See [docs/MODDING.md](docs/MODDING.md).

## How it works / reverse engineering

[docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md) describes the port's structure and what is known about the
game (functions, data layouts, file formats), with the tools that read them.
[docs/ENGINEERING_LOG.md](docs/ENGINEERING_LOG.md) is the full development journal.

## Developing

- `bash tools/rebuild.sh`: recompile (when `config/` changed) and rebuild; `cmd /c tools\build.cmd`: rebuild the
  runtime only.
- `tools\playtest.ps1 -Script tests\<script>.txt -Seconds N -Shots "a,b"`: scripted input, screenshots in
  `build\shots`, log in `build\gow3.log` ([tests/README.md](tests/README.md)). Tests use their own saves, profile and
  settings (`build\testdata`; `-Fresh` starts empty) and their own mods (`-Mods "examples\mods\X"`), never the
  player's.
- `python tools\chapter_test.py [0-30]`: campaign coverage. Boots each chapter directly (`GOW3_COMMAND_LINE`), plays
  generic input, and reports crashes, the map loaded, frame rates and black or frozen screens
  (`build\coverage\report.md`, a contact sheet per chapter).
- Debug switches are environment variables `GOW3_*`, documented in the files that read them.
- The package tools (`tools\upk.py` and those built on it) decompress with `tools\xpack\xpack.exe`: build it once with
  `cmd /c tools\build_xpack.cmd` (after setup, which fetches the LZX decoder with XenonRecomp).

## Reporting problems and contributing

Open an issue with the bug report form and attach `build\gow3.log` (network lines hide public addresses and join
codes, so it is safe to post). Never post game files, disc images or keys. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

GPL-3.0 (see LICENSE): the runtime's kernel, threading and heap code comes from Unleashed Recompiled (GPL-3.0). Third
party components keep their licenses (runtime/thirdparty). Gears of War is a trademark of Microsoft; this project
contains no game assets or code, is not affiliated with or endorsed by Microsoft, Epic Games or The Coalition, and
needs your own legally obtained copy of the game.
