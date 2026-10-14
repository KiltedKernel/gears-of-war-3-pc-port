# Status and roadmap

## Verified (scripted playtests in tests/, see ENGINEERING_LOG.md)

- Boots from the player's disc, Epic logo movie (or `-nointro`), title screen with audio.
- Menus: main menu, campaign, difficulty, save slots, options; controller, keyboard and mouse (hover selects, click,
  right click back, wheel, sliders and steppers).
- Campaign: Act 1 Prologue "Troubled Past" through the flashback and waking on the Raven's Nest, combat, checkpoint
  saves, Continue (tests/new_campaign.txt, combat.txt, continue.txt; minutes-long runs without a crash).
- Horde private matches start and play waves.
- 60 fps (frame-rate unlock), 16x anisotropy, effect toggles, resolution scaling, native mouse look, keyboard/mouse
  button prompts, launcher with options.
- Mods: folders with `mod.ini`, priorities and conflicts, merged table of contents, configuration fragments merged
  at startup (several mods changing the config stack), new campaign chapters (32-49) in Select Chapter loading their
  own maps.

## Not done / known issues

- No Xbox Live: online modes, party UI (the game asks for xam.xex through XexLoadImage, which fails; it then skips the
  party features).
- Acts 2-5: every chapter loads and plays its first minutes (tools/chapter_test.py); none has been played through
  to its end in tests.
- Versus with bots: not tested.
- `tools/xpack` does not decode every LZX block (GearGame.xxx blocks 340-347): packages compressed as a whole cannot
  be rewritten offline yet.

## Roadmap to the deliverable: your ISO in, a PC port out

1. **Converter for players.** `tools/setup.ps1` does the whole conversion today, but it needs a C++ toolchain
   (Visual Studio, LLVM, CMake, Ninja) because the recompiled game code is generated from the player's disc and must
   be compiled on their machine. Goal: a single installer/converter executable that brings or fetches what it needs
   (a pinned portable clang + ninja, the prebuilt runtime as a library), shows progress, and resumes after failures.
   Done so far (2026-10-13): the setup detects missing tools and installs them with winget (Visual Studio Build
   Tools with clang-cl, CMake and Ninja; Python; Git), `-CheckOnly` reports. The converter window
   (`Setup.cmd`, tools/setup_gui.ps1) runs it with progress and a log. Next: prebuilt runtime and tools, so the
   player's PC only recompiles the game code.
2. ~~Launcher with a mod manager~~ (done 2026-10-13: the Mods tab, with Remove to the Recycle Bin). Later: update mods, mod pages with
   screenshots.
3. ~~Runtime config merge for mods~~ (done 2026-10-13).
4. **Campaign coverage:** play through every act with scripted tests; fix what breaks. `tools/chapter_test.py` boots
   every chapter (0-30) directly and plays generic input for 150 s. First run (2026-10-13): all 31 chapters load
   their maps, play cutscenes and gameplay (combat, turret and vehicle sections, the Maelstrom submarine) with no
   crash, black or frozen screen; deaths reload checkpoints. Next: longer play-throughs per chapter (checkpoint to
   checkpoint, set pieces, chapter transitions), and a game frame-rate measure.
5. **Reverse-engineering documentation:** keep `docs/REVERSE_ENGINEERING.md` current as systems are understood.
6. **Room for expansion:** content conversion from Gears of War 1 and 2 (developed separately) plugs in as mods
   (new chapters, levels, characters).
