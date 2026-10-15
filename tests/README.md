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
| g1_follow_dom.txt | fresh | (trilogy; `-Mods` the Gears1_Prison mod from `tools\trilogy.py build gears1_prison`) a new campaign starts in Gears 1's cell: its cutscene, then Marcus follows Dom out along the corridor |
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
| mp_host.txt + mp_join.txt | fresh | System Link: party created, browsed and joined, Private Team Deathmatch with both players (tools/mp_test.ps1) |
| mp_host_horde.txt + mp_join.txt | fresh | System Link Horde on Checkout: the host walks to the Command Post marker and buys it (walkto, face), the countdown runs, wave 1 attacks both players, both see the wave result |
| mp_host_beast.txt + mp_join_beast.txt | fresh | System Link Beast on Checkout: both players pick a creature and play wave 1 |
| mp_host_coop.txt + mp_join_coop.txt | -HostData build\testdata | System Link campaign co-op: the host continues its checkpoint, the second player joins the campaign and watches until the drop-in point ("Please Wait to Join Game") |

Multiplayer runs two games at once, each with its own settings and log in `build\mp\host` and `build\mp\join`,
connected through loopback only (no firewall prompt). `-Connect` picks the setup: `lan` (no code: the second finds
the first), `samepc` (one data folder, as two copies of one install: the second gets `instance2`), `direct`, `code`,
`server` (a gow3_server.exe started for the test), `hairpin` (a friend's code whose address cannot be reached from
inside the network: found on the network instead) and `unreachable` (a code nobody answers at: a message, then the
game on the network). `-HostMods` / `-JoinMods "examples\mods\A,..."` give each game its own mods and
`-ServerMods` the mods a test server requires (a mismatch: the second game is turned away with the list). The `net:` lines of each log show every step, `net: [title] text` the messages shown in the
game:

```powershell
powershell -ExecutionPolicy Bypass -File tools\mp_test.ps1 -HostScript tests\mp_host.txt -JoinScript tests\mp_join.txt -Seconds 240 -Shots "115,235" -Connect samepc
```

Scripts can steer the player: `<s> walkto <x> <y> [seconds]` walks to a map position, `<s> face <x> <y>` turns
the camera toward one (hid/hid.cpp). Positions come from `GOW3_LOG_POS=1` (pawns) and
`GOW3_LOG_ACTORS=Class[:Property],...` (any actors, e.g. GearFortificationMarker:FlagSpawnProtectionRadius).
`python tools/contact_sheet.py out.png build\mp\host\shots build\mp\join\shots` puts a run's screenshots side by
side.

Campaign coverage (every chapter booted directly, no menus) is `python tools\chapter_test.py`; see the README.
