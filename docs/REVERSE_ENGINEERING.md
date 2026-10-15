# How the port works, and what is known about the game

This is the map of the port and of Gears of War 3's internals as far as they have been reverse engineered. Every
address is in the retail XEX this port targets (`tools/default_xex.sha256`, title ID 4D5308AB). The development
journal ([ENGINEERING_LOG.md](ENGINEERING_LOG.md)) has the full history, measurements and dead ends.

## 1. Pipeline: from the disc to gow3.exe

| step | tool | output |
|---|---|---|
| extract the ISO (XDVDFS) | `tools/xdvdfs.py` | `..\gow3-decomp\disc` (2,568 files, 7.4 GB) |
| check the game version | `tools/default_xex.sha256` | — |
| decrypt and decompress default.xex (XEX2, retail AES, basic compression) | `tools/xex_unpack.py` | `default.bin`, a flat image based at 0x82000000 |
| fetch and patch the recompiler | `tools/setup_xenonrecomp.sh`, `xenonrecomp-gow3.patch` | `tools/XenonRecomp` |
| recompile PowerPC to C++ | `tools/recomp.sh` with `config/GOW3*.toml` | `ppc/` (56,951 functions) |
| fetch and patch Xenia's GPU | `tools/setup_xenia.sh`, `xenia-gow3.patch` | `..\xenia` |
| build | `tools/build.cmd` (CMake + Ninja + clang-cl) | `build\gow3.exe`, `build\gow3_launcher.exe` |

`tools/setup.ps1` runs them all; each step is skipped when its output exists.

### Image layout

Base 0x82000000, image size 0x15B0000, entry 82CE8EB0. Sections: .rdata 82000600, .pdata 8224B000 (0x50CC8),
.text 822A0000 (0xD291CC), BINK 82FC9200, RADCODE 82FE9E00, .data 82FF5C00, .idata 83480000. `tools/ximage.py`
reads them from the PE header so no tool hard-codes them. 293 imports (`tools/list_imports.py`).

### Recompiler configuration (config/)

- `GOW3.toml`: the recompiler's main config (register save/restore helpers: restgprlr_14 82CC9910, savegprlr_14
  82CC98C0, restfpr_14 82CCA1CC, savefpr_14 82CCA180, restvmx_14 82CCA518, savevmx_14 82CCA280, restvmx_64
  82CCA5AC, savevmx_64 82CCA314; memset 82CC9EA0).
- `GOW3_switch_tables.toml`: jump tables. XenonAnalyse finds none in Gears 3 (its compiler schedules
  `lis / slwi / addi / lwzx`); `tools/find_switches.py` evaluates each `mtctr; bctr` symbolically (103 tables) and
  `tools/find_inline_switches.py` adds the caller-bounded ones (5). `tools/check_switches.py` re-derives and checks
  them.
- `GOW3_functions.toml`: explicit function bounds (`tools/fix_boundaries.py`, `tools/fix_stubs.py`).
- `GOW3_hooks.toml`: mid-function hooks into the runtime (patches, input, debugging).

The recompiler patches add instructions Gears 3 uses that XenonRecomp lacked (update-form loads/stores, addc,
addme, subfze, mulhdu, bdzf with its CR bit, halfword VMX, vcmpbfp(128), vupkd3d128 FLOAT16_2, blrl) and fix the
disassembler dialect (PPC 403 multiply-accumulate ops shadowed VMX128).

## 2. Runtime (runtime/)

| folder | what |
|---|---|
| `kernel/` | the Xbox 360 kernel and XAM the game imports: threads, events, files (`x360_files.cpp`, guest paths to host paths), memory, XContent saves, network stubs (link up, no Live), `gow3_patches.cpp` (game patches), `gow3_imports.cpp`, `mods.cpp` |
| `cpu/` | the guest context, guest/host calls |
| `gpu/` | the bridge to Xenia's D3D12 GPU emulation (`xe_glue/`); startup of the runtime's services |
| `apu/` | audio (XMA through Xenia) |
| `hid/` | input: XInput pads, keyboard and mouse as player 1's pad, scripted input for tests, native mouse look |
| `ui/` | mouse in Scaleform menus (`gfx_menu.cpp`), keyboard/mouse button prompts (`prompts.cpp`) |
| `ue3/` | UE3 reflection over the running game, and features built on it (below) |
| `user/` | settings (`gow3.ini`), paths |
| `crash.cpp`, `watch.cpp`, `trace.cpp` | crash reports naming UE3 objects, hardware watchpoints, tracing |

The kernel, threading and heap come from Unleashed Recompiled (GPL-3.0); the rest was written for the Gears ports.

### Game patches (kernel/gow3_patches.cpp, gow3.ini)

From Xenia's game-patches for this XEX, each turned into a mid-asm hook: frame-rate unlock (tick cap load 8253D9B0
skipped; presentation interval `li r11,2` at 82AF66BC -> 1), 16x anisotropy 82969614, motion blur
82430640/827A2BCC, depth of field 824BAA50/82593DC4, lens flares 82861FD0, ambient occlusion
82762C54/827C9628. No bloom switch: the only read of GSystemSettings.Bloom (82593DC0) is dead code (its `cmplwi
cr6` is overwritten by the next compare before the branch). Rendering: render targets 64 < pitch <= 160 stay at native size under resolution scaling
(`[graphics] scale_threshold = 160`; the ambient-light buffer turned green when upscaled).

### Fixes found during bring-up

- "Connection Lost" over the title: the network link must report up (XNetGetEthernetLinkStatus 0x0B).
- Crash after "press start": XamGetExecutionId must return the XEX's execution-info header (0x40006).
- Title ID 4D5308AB for content folders, XNetLogonGetTitleID and profile settings.

## 3. UE3 in Gears 3 (engine version 828)

### Object layout (ue3/reflection.cpp)

| | offset |
|---|---|
| UObject: vtable, HashNext, ObjectFlags (64-bit), HashOuterNext, StateFrame (+20), Linker, LinkerIndex | 0..31 |
| UObject slot (ObjectInternalInteger) | +32 |
| Outer / Name / Class | +40 / +44 / +52 |
| UStruct super / children / properties size | +64 / +68 / +72 |
| UField next | +60 |
| UProperty ArrayDim / ElementSize / Offset | +64 / +68 / +84 |
| UBoolProperty mask | +104 |

GNames (pointer at 8343D148) is an array of FNameEntry pointers ({u32 Index = n*2, u32 HashNext, char Name[]},
packed); GObjObjects pointer at 8343D188. "GameViewportClient" is name 10043. FNames are made by
**sub_82302F28(FName* out, const TCHAR* name (UTF-16BE), EFindName = 1 add)**.

### Functions and data used by the port

| address | what |
|---|---|
| 8273B518 | UPlayerInput::InputAxis (native mouse look injects MouseX/MouseY after RightX/RightY) |
| 834440C8 / 834440D0 / 83444438 / 83444440 | KEY_MouseX / MouseY / XboxTypeS_RightX / RightY FNames |
| 82302F28 | FName constructor |
| 8291E0B8 | GFx engine constructor (full key map) |
| GFxMovieView vtable slot 45 (82BCD738) | HandleEvent: the port sends mouse moves |
| UGFxMoviePlayer vtable +0x148 / +0x190 | GetVariableNumber / Invoke (ActionScript from the runtime) |
| Default__GearTypes.ChapterLevelNames | chapter number -> map (31 entries on the disc) |

### Menus (Scaleform)

Menus are UGFxMoviePlayer subclasses named UIScene_*; flags at +0xC8 (bMovieIsOpen 80000000, bAllowInput 04000000,
bCaptureInput 00040000, bIgnoreMouseInput 00020000). The lists are CLIK ScrollingLists; `Selection.getFocus()`
names the focused one and `__set__selectedIndex` selects like a d-pad press. Gears 3 reuses the main menu's list for
its submenus (Campaign's three options in seven rows): rows past the data length must be ignored, or the selection
leaves the list and input appears dead. Details: `runtime/ui/gfx_menu.cpp`.

The front end's main menu is `gears.views.MainMenuView` at `_level0.MainMenuSP.MainMenu` (ActionScript in the
movie; decompiled with JPEXS). Its `handleInput`: on the title screen (`bOnTitleScreen`) only GAMEPAD_START is read;
B with an empty `MenuOptionsHistory` (the first page) calls `Callback_TransitionToTitle`, which reloads the front end
(about 10 s without polling input); `SetDenyInput(true)` drops every press during a transition. The port therefore
answers Esc and right click itself (no B on the first page) and turns a click on the title screen into Start.

### Button prompts

The button font's page 0 (256x256 DXT5, FNV 350c61d5241b80a8) is never touched; keyboard icons go into page 1's free
rows (152-232) and the button characters are pointed there, in the FFontCharacter table and in GFx's per-glyph
objects. Character map and details: `runtime/ui/prompts.cpp`, ENGINEERING_LOG.

### Campaign and chapters

- The menu starts a chapter with `GearGame_P?game=geargamecontent.GearGameSP?chapter=N?listen`
  (GearMenuGame.BuildCampaignURL); GearGame_P is an empty persistent level; GearGameSP_Base.InitGame stores N in
  CurrentChapter; native code loads `Default__GearTypes.ChapterLevelNames[N]`.
- Chapter list: `[ChapterN GearCampaignChapterData]` / `[ActN GearCampaignActData]` in Xbox360-GearGame.ini (and
  GearGame.int for text), found by `ChapterNum` (GearUIDataStore_GameResource.GetChapterDataProvider); their
  `MapName` is not what loads. EChapterPoint: chapters 0-30, CHAP_Gameover 31, CHAP_DLC_1..18 (32-49), CHAP_MAX 50.
- New chapters: `runtime/ue3/campaign_chapters.cpp` (see MODDING.md).
- Unlocking: `GearProfileSettings.HasChapterBeenUnlockedForAccess` returns true for a chapter whose config has
  `IsAlwaysUnlocked=true` (only chapter 0 on the disc), else tests bit N of profile setting 95 (chapters 0-31) or 96
  (32+). The Select Chapter screen checks the same flag (examples/mods/UnlockAllChapters).
- The chapter URL also works as the game's command line (UE3 appCmdLine, read from the kernel variable
  `ExLoadedCommandLine`; the port fills it in `runtime/loader.cpp`, `GOW3_COMMAND_LINE` adds to it): the game then boots
  straight into the chapter (tools/chapter_test.py).
- Cutscenes: `GearPC.CancelCine` (`CANCELMATINEE`) is bound to Space on PC; the controller's Start goes through
  `ButtonPress Start`.

## 4. Files and formats

| format | tool | notes |
|---|---|---|
| UE3 packages (.xxx), big-endian, version 828 | `tools/upk.py`, `tools/upkwrite.py` | chunks LZX-compressed (flag 4); some packages are compressed as a whole (GearGame.xxx, startup packages) |
| Gears of War: Reloaded packages (.upk), little-endian, version 835 | `tools/upk.py` | one more int per export before NetObjectCount; class/default objects with components not parsed yet |
| LZX (XMemCompress framing, 128 KB window) | `tools/xpack` | some blocks with odd frame lengths are not decoded yet (GearGame.xxx 340-347) |
| Coalesced_INT.bin (all .ini) | `tools/coalesced.py` | strings UTF-16; merge for mods |
| UnrealScript bytecode | `tools/uscript.py` | readable pseudo-code of any function |
| Texture2D, texture caches (.tfc) | `tools/texture2d.py`, `tools/texdecode.py`, `tools/dxt.py` | |
| Xbox360TOC.txt | runtime (`kernel/mods.cpp`) | merged with the mods' files at startup |

## 5. Debugging the game

Environment variables (each documented where it is read):

| variable | what |
|---|---|
| `GOW3_INPUT_SCRIPT=<file>` | play scripted input (`tests/`, `tools/playtest.ps1`) |
| `GOW3_WATCH=<addr>[,len][,rw]` | hardware watchpoint: who writes (or reads) guest memory |
| `GOW3_WATCH_PROP=Class:Prop[@Object][,rw]` | the same on a UE3 property |
| `GOW3_LOG_PROPS=Class:Prop,...` | log properties and script state of live objects |
| `GOW3_FILE_STACK=<name part>` | the call stack of whoever opens matching files |
| `GOW3_HANG_DUMP=<s>` | dump every thread's stack after s seconds |
| `GOW3_LOG_POS=1` | pawn positions every 2 s |
| `GOW3_MENU_DEBUG=1`, `GOW3_INPUT_DEBUG=1` | menu scenes, input packets |
| `GOW3_PROFILE=1` | sample where the game thread spends time |

A crash report names the UE3 objects held in the guest registers. Recompiled functions are named
`sub_<guest address>`; `tools/fnstrings.py` lists the strings a function refers to (to name it),
`tools/find_refs.py` finds code forming an address, `tools/ppcdis.py` / `tools/xdis.py` disassemble,
`tools/class_vtable.py` finds a native class's vtable, `tools/match_function.py` finds a function from another game
by shape.
