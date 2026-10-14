# Scripted tests

Each `.txt` file is an input script for `tools/playtest.ps1`: timed controller, keyboard and mouse events played
inside the runtime (the desktop is never touched), with screenshots taken from the game's own output.

```powershell
powershell -ExecutionPolicy Bypass -File tools\playtest.ps1 -Script tests\versus_bots.txt -Seconds 240 -Fresh -Shots "130,190"
```

Screenshots land in `build\shots`, the log in `build\gow3.log`. Tests run with their own saves, profile and settings
(`build\testdata`) and their own mods (`build\testmods`, from `-Mods "examples\mods\X"`), never the player's.

**Profile state.** A brand-new profile (`-Fresh`) shows the brightness calibration screen after Start; scripts marked
*fresh* expect it. The others expect a profile that has passed it and, for *checkpoint*, a campaign checkpoint: run
`new_campaign.txt` once (without `-Fresh`) to make one.

| script | state | checks |
|---|---|---|
| press_start.txt | any | title screen, Start, into the main menu |
| new_campaign.txt | fresh | Campaign > Standard > New > difficulty: the prologue starts (makes a checkpoint) |
| continue.txt | checkpoint | Campaign > Standard > Continue loads the checkpoint |
| combat.txt | checkpoint | moving, aiming (right mouse), firing (left mouse), reloading (R) |
| esc_pause.txt | checkpoint | Esc pauses in play, Esc resumes |
| select_chapter.txt | checkpoint | Select Chapter (overwrites slot 1) to Act VI (needs examples\mods\ExampleChapter: `-Mods`) |
| horde_private.txt | profile | Horde > Private lobby |
| versus_bots.txt | fresh | Versus > Private > Team Deathmatch with 9 bots, played |
| beast_private.txt | fresh | Beast > Private, a creature chosen, wave 1 played |
| mouse_menu.txt | profile | menus with the mouse: click, wheel, right click |
| options_click.txt | profile | Options > Controller, a slider set with the mouse |
| kbm_menus.txt | profile | menus with the keyboard only |
| prompt_switch.txt | profile | button prompts follow the last device used |
| menu_click_then_pad.txt | profile | after a mouse click into a submenu the controller still works |
| menu_esc_then_click.txt | profile | Esc back, then a click opens the next menu |
| menu_esc_root.txt | profile | Esc / right click on the main menu's first page do nothing; a click on PRESS START starts |
| menu_title_back.txt | profile | back to PRESS START, Start opens the main menu again |

Campaign coverage (every chapter booted directly, no menus) is `python tools\chapter_test.py`; see the README.
