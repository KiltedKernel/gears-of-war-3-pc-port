# Engineering log

The development journal, copied from the working tree it was written in. Entries about Gears of War 1 and 2 content (the "trilogy" work: converting their levels into Gears 3) belong to separate tooling that is not part of this repository yet; they are kept here for the reverse-engineering facts they record about Gears 3.



# Gears of War 3 PC port: journal

A static recompilation of the player's own Gears of War 3 disc (Xbox 360, NTSC-U), built from the Gears of War 2
port (D:\gow2-port): same runtime, same embedded Xenia GPU, same tools. Long-term goal: put the assets of Gears 1, 2
and 3 into this port, so the trilogy can be played with Gears 3's gameplay and sandbox.
No game files are in this repository; everything game-derived is made from the disc (dev copy: D:\gow3-decomp).

## 2026-10-11: Recon
- ISO `Gears of War 3 [NTSCU][MEX].iso` (8.7 GB) extracted with tools/xdvdfs.py to D:\gow3-decomp\disc: 2,568 files,
  7.4 GB. `GearGame/CookedXbox360/*.xxx` UE3 packages, `Coalesced_INT.bin`, `Movies/*.bik`, `Xbox360TOC*.txt`,
  `TRIOVIZ*.XBX` (TriOviz 3D), `$SystemUpdate`. One game disc, single default.xex
  (SHA-256 08b60f29...6411917, 21,938,176 B).
- XEX2: retail AES and basic compression (the same as Gears 2). Base 0x82000000, image 0x15B0000, entry 82CE8EB0.
  Sections: .rdata 82000600, .pdata 8224B000 (0x50CC8), .text 822A0000 (0xD291CC), BINK 82FC9200, RADCODE
  82FE9E00, .data 82FF5C00, .idata 83480000.
- Imports: 293 (Gears 2: 290). New in Gears 3: semaphores (NtCreate/ReleaseSemaphore), KeInitializeDpc,
  MmMapIoSpace, NtWriteFileGather, NtYieldExecution, XexLoadImage/XexUnloadImage/XexLoadImageHeaders,
  XamGetOverlappedResult, XamUserGetDeviceContext, XamReadTileToTexture, XAudioGetSpeakerConfig/DuckerLevel,
  NetDll_XNetConnect / XNetSetSystemLinkPort / XNetQosGetListenStats, VdSetDisplayModeOverride and a few XAM UIs.
  tools/list_imports.py lists them by name.

## 2026-10-11: Recompilation
- Save/restore helpers: the same bytes as Gears 2's, one match each: restgprlr_14 82CC9910, savegprlr_14 82CC98C0,
  restfpr_14 82CCA1CC, savefpr_14 82CCA180, restvmx_14 82CCA518, savevmx_14 82CCA280, restvmx_64 82CCA5AC,
  savevmx_64 82CCA314. memset (Gears 2's 0xA0 bytes): 82CC9EA0, bounded by hand in fix_boundaries.py MANUAL.
- **XenonAnalyse finds no jump tables in Gears 3.** Its fixed patterns expect `lis / addi / rlwinm / lwzx`; Gears 3's
  compiler schedules `lis / slwi / addi / lwzx` (and puts nops before the bctr). New tools/find_switches.py: for
  each `mtctr; bctr` it finds the bounds check (bgt/ble ... cmplwi), then evaluates the code from there to the bctr
  symbolically (constants, the index, its shifts, indexed loads, adds, register copies, spill/reload of the index
  through r1/r31). It handles absolute, byte-offset and halfword-offset tables, and keys a table at its bctr.
  Check: on Gears 2 it gives 504 tables, 503 identical to XenonAnalyse's (the six caller-bounded ones come from
  find_inline_switches.py). Gears 3: 103 + 5 caller-bounded (find_inline_switches.py, which now also skips tables
  keyed at the bctr).
  The remaining `mtctr; bctr` sites are virtual calls and UnrealScript's native dispatch (GNatives: a byte opcode
  indexes a table of function pointers in .data), which are correct as indirect calls.
- 4,641 unrecognized instructions. tools/patch_xenonrecomp_gow3.py adds:
  - update-form loads/stores: lfsu lfsux lfdu stfsu stfdu lhzu lhzux lhau sthu sthux lbzux stbux lwzux
  - addc, addme, subfze, mulhdu
  - bdzf (the compiler's small-switch idiom `mtctr idx; cmpwi idx,0; bdzf eq,case1; bdzf eq,case2...`; the CR bit
    is taken from the instruction; XenonRecomp's own bdnzf assumes eq)
  - halfword VMX: vsrah vslh vsrh vsubshs vcmpgtsh(.) vcmpequh(.) (CR6 from the byte mask), vspltish, vaddsws, lvehx
  - **disassembler dialect**: "mulchw." was really vsldoi128. The default dialect included the PowerPC 403/405,
    whose multiply-accumulate ops share opcode 4 with VMX128 and matched first. PPC_OPCODE_403 was removed from the
    default (thirdparty/disasm/ppc-dis.c).
  XenonRecomp is shared with the Gears 2 port (tools/XenonRecomp is a junction). Checked: Gears 2 regenerated with
  the patched recompiler is byte-identical in all 199 files.
- fix_boundaries.py and fix_stubs.py read .pdata and the code span from the PE header (tools/ximage.py) instead of
  Gears 2 constants. New: with the ppc/ folder as a 6th argument, fix_boundaries also bounds functions that `goto`
  a label they do not contain (2 in Gears 3: a chain of bdzf past the analyzer's chosen end).
- tools/rebuild.sh: recompile, bounds rounds, import stubs (every `__imp__` name in ppc/, including the CRT ones
  like _snprintf), build. Result: **56,951 functions, 0 switch errors, 0 unknown instructions**.

## 2026-10-11: Runtime (from the Gears 2 port)
- Taken as is: kernel, XAM, threads, files, audio (XMA through Xenia), GPU (Xenia D3D12, shared glue and build in
  D:\xenia), input, settings, launcher. The executable is gow3.exe, settings gow3.ini, log gow3.log, saves
  %LOCALAPPDATA%\GoW3Recomp, environment variables GOW3_*.
- Removed (each bound to Gears 2 addresses, so in Gears 3 it would override unrelated functions): the Gears 2
  patches (frame-rate unlock, 16x anisotropy, texture pool, black shading fix, shadow resolution), the animation
  debugger (about 290 hooks), debug_hooks, native mouse look (the mouse drives the right stick until UPlayerInput's
  InputAxis is found in Gears 3). Off: effects settings (GSystemSettings address) and mouse menus (Gears 2 UI layout).
- Fixes for Gears 3:
  - **"Connection Lost" over the title screen**: the runtime reported no network cable (XNetGetEthernetLinkStatus 0,
    XN_LIVE_LINK_STATE_CHANGED 0). Now the link is up (0x0B: active, 100 Mbps, full duplex). There is still no Live
    logon.
  - **Crash after "press start"** (a UE3 small-block pool handing out a block twice): **XamGetExecutionId** was a stub
    that returned nothing. It now returns the XEX's execution-info header (0x40006).
    Found with the new hardware watchpoint (below): a 32-byte block was freed while sub_829927B0 still held a
    pointer into its TArray (it was grown by 828EF568), so the call path differed from the console's.
- New debugging tool, runtime/watch.cpp: `GOW3_WATCH=<guest address>[,len]` sets DR0 in every guest thread. Each
  write logs the value, the guest registers, the guest call chain (r1 back chain) and the host stack. Guest memory
  layout is deterministic, so an address from one crash can be watched in the next run.

## 2026-10-11: Status
- Boots, plays the Epic logo movie, title screen with audio at 30 fps. Main menu, campaign menu, difficulty select,
  loading screen, and **Act 1 Prologue "Troubled Past"**: the opening cutscene, then gameplay with the WALK tutorial.
  No crash in 155 s. Saves created (PlayerStorage.dat, GearsCheckpoint). Scripted test: tests/new_campaign.txt.
- **Rendering bug**: surfaces in shadow or ambient light have a flat green tint (walls, Marcus's armour); directly
  lit areas look right.
- Still stubbed or missing: XexLoadImage (an extra module is requested at 82000ACC), KeInitializeDpc, MmMapIoSpace
  (XMA contexts), XamUserGetDeviceContext, XamVoiceIsActiveProcess, XamContentGetLicenseMask, XAudioGetSpeakerConfig,
  NtQueryInformationFile class 26.

## 2026-10-11: Rendering fix, patches, mouse look, imports
- **Green tint in shadowed areas: Xenia's resolution scaling.** At resolution_scale 1 the image is correct.
  Tests at 2x:
  | option | result |
  |---|---|
  | draw_resolution_scaled_texture_offsets off | still green |
  | resolve_resolution_scale_fill_half_pixel_offset off | still green (and blue) |
  | draw_resolution_scale_threshold 640 / 320 / 160 | correct |
  | draw_resolution_scale_threshold 64 | green again |

  So a tiny render target (64 < pitch <= 160; Gears 3's ambient-light buffer) breaks when upscaled. New setting
  `[graphics] scale_threshold = 160`: render targets that narrow stay at native size. At 160 the frame's mean colour
  matches resolution scale 1 (19.8/16.9/7.3 against 19.8/16.9/7.4). The glue also takes GOW_SCALED_TEXTURE_OFFSETS,
  GOW_RESOLVE_HALF_PIXEL and GOW_SCALE_THRESHOLD from the environment for experiments (Host.scaleThreshold is new in
  gow2_gpu.h; both ports rebuilt).
- **Patches** from Xenia's game-patches for this XEX ("4D5308AB - Gears of War 3.patch.toml", hash EF4AD5E9DD1982ED).
  Each address was checked against the instruction it replaces, then made a mid-asm hook controlled by gow3.ini
  (kernel/gow3_patches.cpp, config/GOW3_hooks.toml):
  - frame-rate unlock: tick cap load 8253D9B0 skipped; presentation interval `li r11,2` at 82AF66BC -> 1
  - 16x anisotropy: 82969614
  - motion blur 82430640/827A2BCC, depth of field 824BAA50/82593DC4, bloom 82593DC0, lens flares 82861FD0,
    ambient occlusion 82762C54/827C9628 (new setting ambient_occlusion)

  Result: 60 fps in menus and cutscenes, 49-60 in the prologue at 2560x1440.
- **Native mouse look**: UPlayerInput::InputAxis is 8273B518, found from the KEY_MouseX global. Key FNames come from
  the static initializer at 824F10F8: MouseX 834440C8, MouseY 834440D0, XboxTypeS_RightX 83444438, RightY 83444440.
  hid/mouse_look.cpp is Gears 2's with these addresses. tests/campaign_look.txt: the camera turns right, then up.
  tools/match_function.py (find a Gears 2 function in Gears 3 by instruction shape) did not find this one: the
  Gears 2 function is 0x7C bytes and the best match scored only 0.61.
- Imports (kernel/gow3_imports.cpp): MmMapIoSpace (physical view address), KeInitializeDpc, XamUserGetDeviceContext,
  XamVoiceIsActiveProcess, XamContentGetLicenseMask, XAudioGetSpeakerConfig. XexLoadImage("xam.xex") stays a
  failure: the title then skips the party ordinals it wanted (XamPartyGetUserList/SendGameInvites/SetCustomData/
  GetBandwidth, XamShowPartyUI/CommunitySessionsUI).
- Title ID 4D5308AB for content folders, XNetLogonGetTitleID and profile settings (was Gears 2's 4D53082D; the
  test saves were moved).
- Intro skip: the startup movie is Open_Logos.bik (Bink 2 "KB2a", 643 frames; frame counts at the same offsets 8 and
  16). With the skip on, the title is up at 12 s.
- Launcher: Gears 2-only options removed (shadow quality, self-shadowing, texture pool, black shading fix,
  distortion, fog); ambient occlusion added.
- Playtest tests/walk_prologue.txt: 7.5 minutes into the prologue, no crash, 57-60 fps.

## 2026-10-11: Combat fix, saves, keyboard/mouse prompts
- **Saves**: "Continue" loads the last checkpoint (the prologue's Jacinto flashback); the save slot screen lists
  "Act I Anchored" (tests/continue.txt).
- **Combat crash** (breakpoint in recompiled code): XenonRecomp emits `__builtin_debugtrap()` for instructions it does
  not implement. Gears 3 had 271 `vcmpbfp128.`, 4 `vupkd3d128` type 3 and 1 `blrl`.
  tools/patch_xenonrecomp_gow3.py, second round:
  - vcmpbfp(128)(.): per element bit 31 = !(a <= b), bit 30 = !(a >= -b); the record form sets CR6.eq when every
    element is in bounds
  - vupkd3d128 type 3 (FLOAT16_2): x and y from the high and low halves of PPC element 3, then 0 and 1 (new
    ppc_half_to_float in ppc_context.h, IEEE half like Xenia's F16C path)
  - blrl: a call to LR with LR = the return address

  No traps are left in Gears 3's output. Gears 2 regenerated with these changes differs only where it had the same
  traps (one vupkd3d128 type 3, one blrl). Its two vupkd3d128 type 5 (FLOAT16_4) traps remain, and the Gears 2 build
  was left as it is. tests/combat.txt: five minutes of fighting through the flashback, waking on the Raven's Nest
  and a checkpoint save, 60 fps, no crash.
- **Keyboard/mouse prompts** (ui/prompts.cpp, tools/make_prompts.py):
  - The button font: page 0 at 256x256 DXT5 (FNV 350c61d5241b80a8); its FFontCharacter table has the same 'A', 'B',
    'Y' records as Gears 2. Page 1 (FNV f324ae1953e67914) is empty from row 151. Found by dumping textures
    (GOW2_TEXTURE_DUMP) and guest memory at the WALK prompt.
  - Character map, which differs from Gears 2 in places:
    - A B X Y
    - ] ^ _ ` and w x y z: LT RT LB RB
    - C: right stick; L and M: left stick; j and h: stick clicks
    - d-pad: 7/m up, 8/q down, 9/n left, 6/o right, t up/down, u left/right
    - < > and : ;: Back / Start
  - In-game text (UE3 canvas) reads the table on every draw. **Menus are Scaleform**: when the font loaded, GFx made a
    texture-glyph object per character, {vtable (82022E30 in menus, 821C8D54 in game), refcount, ?, image, UvBounds,
    UvOrigin}, and it caches the geometry of laid-out text.
  - Gears 2's approach (repack page 0, rewrite the table) showed garbage in menus: page-0 pixels moved under the
    cached UVs.
  - Gears 3 design: page 0 is never touched. The 21 unique icons go into page 1's free rows (152-232); the icon block
    rows are encoded once and spliced in whenever page 1 loads, before its upload. Keyboard mode points the button
    characters at page 1:
    - the table records: TextureIndex 1
    - the GFx glyph objects: the page-1 rectangle, and the page-1 image of the same font instance (an image counts
      as a page-0/1 image when most of its objects match that page's rectangles; the nearest one by address). One
      reference is moved with the pointer (count at +4 of the image object; without it a destroyed glyph released
      the wrong image and the game crashed at 2 minutes).
    - wider icons: the UV origin is moved left by the extra width, because GFx advances by the button's width.
  - Text GFx has already laid out keeps whichever icons it was laid out with until the menu lays it out again;
    there is never a wrong picture.
  - Checked: menus show [Space] ACCEPT / [F] BACK / [Enter] OPTIONS, and the WALK tutorial shows WASD and the mouse.
    Switching device while a menu is open updates on its next layout (tests/prompt_switch.txt). 4 minutes, no crash.

## 2026-10-11: Mouse in menus (ui/gfx_menu.cpp), UE3 reflection for Gears 3
- **UE3 reflection, Gears 3 layout** (ue3/reflection.cpp), found from a menu-time memory dump:
  | | Gears 3 | Gears 2 |
  |---|---|---|
  | GNames | 02130000 | 03AC0000 |
  | GObjObjects | 00030000 | 01300000 |
  | "GameViewportClient" / Default__ | names 10043 / 10044 | 1968 / 1969 |
  | UObject slot (ObjectInternalInteger) | +32 | +4 |
  | UObject Outer / Name / Class | +40 / +44 / +52 | same |
  | UStruct super / children / properties size | +64 / +68 / +72 | +60 / +76 / +80 |
  | UField next | +60 | +64 |
  | UProperty ArrayDim / ElementSize / Offset | +64 / +68 / +84 | +68 / +72 / +100 |
  | UBoolProperty mask | +104 | +132 |

  Gears 3's UObject header starts {vtable, HashNext, ObjectFlags (64-bit), HashOuterNext, StateFrame, Linker,
  LinkerIndex (-1)}, then the slot. Name entries are packed without alignment. Slot 2 is "System", not "Object", so the
  object table is validated by slot consistency only. New: ue3::FindInstancesOf(class), which also extends the object
  count, since the table grows.
- **Menus are Scaleform movie players**: GFxMoviePlayer subclasses named UIScene_*, flags at +0xC8 (bMovieIsOpen
  80000000, bAllowInput 04000000, bCaptureInput 00040000, bIgnoreMouseInput 00020000). Open scenes seen:
  | state | open scenes |
  |---|---|
  | title | Alert |
  | menus | Background+in, FrontEndContent+in (+ Busy+in while loading) |
  | gameplay | CalloutsContent+in (HUD prompts) |
  | pause | PauseContent+in+capture, CalloutsContent+in |

  Menu mode is on while an open movie takes input and is not Background, HUD or CalloutsContent. Alert counts only
  while it takes input, i.e. a dialog is up.
- In menu mode the mouse is released. Left click = A, right click = B, wheel = d-pad up/down (one press per notch,
  120 ms apart), Esc = B; in play Esc = Start (pause). tests/mouse_menu.txt: click into Campaign, wheel Standard ->
  Arcade, right click back. tests/esc_pause.txt: Esc pauses and resumes.
- Native Scaleform mouse (hover): the Xbox build still has the full GFx key map, including mouse buttons, wheel,
  FullKeyboard and GamepadMouse (built in 8291E0B8, the GFx engine constructor; vtable 821C8DEC). Where the pointer
  position comes from is not found yet; a search for bIgnoreMouseInput tests found only the priority pass
  (82923A70). Hover-to-select is not done.
- Also: InputAxis is called about 720 times per 2 s in menus and in play alike (6 axes x 60), so it is no
  menu signal.

## 2026-10-11: Trilogy groundwork: reading Gears 2 and Gears 3 packages offline
- **tools/xpack** (build: tools/build_xpack.cmd; C++ with XenonRecomp's libmspack lzxd): Xbox UE3 compression is LZX
  with a 128 KB window in XMemCompress framing. A stream is {tag, block size 0x20000, total compressed/uncompressed,
  (compressed, uncompressed) per block, block data}; each block is frames of [u16 BE compressed size] (or
  [FF][u16 uncompressed][u16 compressed]) for 32 KB of output, and the LZX bitstream is the payloads back to back.
  `xpack in out` unpacks a whole-file compressed package; `xpack in out <offset hex>` unpacks one compressed chunk.
- Gears 3: only 13 of 1,838 packages are compressed as a whole (Core, Engine, GearGame ...). The rest are plain
  packages with LZX chunks (compression flag 4; e.g. GEARGAMESP_SF has 11 chunks). Gears 2 packages are also chunked.
  Known issue: GearGame.xxx (whole-file) fails at block 340 of 660 (lzxd decrunch error). The block mixes frames that
  continue an uncompressed block (32,768 bytes), uncompressed-block headers (32,784 = 16 + 32,768) and an odd-sized
  compressed frame; padding odd frames does not fix it.
- **tools/upk.py**: big-endian Xbox packages (byte order from the tag), Xbox chunks through xpack, and the version
  828 header (ImportExportGuidsOffset, two GUID counts and ThumbnailTableOffset after DependsOffset; absent in 575).
  Gears 3 GEARGAMESP_SF: 3,545 exports from 10 MB. Gears 2 GUDMP_COGMarcusG1_SF: 176 exports.
- **tools/census.py**, 40 packages per game (export classes by data size):
  | class | Gears 2 (575) | Gears 3 (828) |
  |---|---|---|
  | Texture2D | 88 MB | 61 MB |
  | SoundNodeWave | 43 MB | 43 MB |
  | StaticMesh | 34 MB | 38 MB |
  | SkeletalMesh | 22 MB | 13 MB |
  | ShaderCache | 22 MB | 39 MB |
  | AnimSequence | 19 MB | 16 MB |
  | LightMapTexture2D | 14 MB | 9 MB |
  | Level | 8 MB | 9 MB |

  Material graphs are small, but their compiled shaders live in ShaderCache, which is per engine version. 7 of the
  40 Gears 2 packages fail to parse; the export-entry walk needs work for them.

## 2026-10-11: Prompts and mouse, after the first real playtest
- Reported: the menus showed controller prompts, and the mouse did not work in menus. The log of that session
  (build/gow3_user_1236.log) shows the keyboard prompts switched on at the title and never back. So the swap ran,
  but the menus had laid out their text before it: Scaleform caches the layout, and moving glyphs (the page-1
  design) only reached text laid out afterwards. My scripted test had won that race.
- **Prompts, new design**: glyph positions never change for menus. Keyboard mode draws *compact* icons over the
  buttons in page 0 itself, each resized (bilinear) to its button's rectangle; only the touched 4x4 blocks are
  re-encoded. Every menu, laid out before or after, changes picture at once, in both directions. The canvas path
  (in-game text) still points the table at the full-size icons in page 1. The Scaleform glyph-object rewriting
  (and its reference counting) is gone.
  make_prompts.py writes the compact set under character code + 256: Spc / Ent / Tab / Bk keycaps (labels shrink to
  fit), single keys and mouse icons as they are, key clusters squeezed to 26 px. tests/kbm_menus.txt: keyboard only
  -> [Spc] ACCEPT [F] BACK [Ent] OPTIONS; a pad press switches the same menu back to A / B / Start.
- **Mouse**: a click presses A on the *highlighted* option and the wheel moves the highlight. There is no hover, so
  clicking an option under the cursor selects whatever is highlighted, which reads as broken. gfx_menu now always
  logs menu-mode changes (with the open scenes) and the first 30 clicks/wheel turns (position, inside window).
  Hover needs Scaleform's own mouse handling. Leads so far: the GFx key map (8291E0B8) includes the mouse buttons,
  and the ActionScript builtin name table (_xmouse at 82023688, pointed to from 833716A0) leads to the movie root's
  mouse state.

## 2026-10-11: Mouse hover in menus (in progress)
- **Reflection fix**: GNames and GObjObjects are growing arrays that get reallocated, so the cached data address went
  stale and names came back as "_0". Their globals are at 8343D148 / 8343D188 ({data, count, max}); ue3::Refresh
  reads them again on lookups.
- **Scaleform path, found and working at the engine level**:
  - UIScene +60 pMovie -> FGFxMovie: +0 file name, +20/+24 size 1280x720, +48 GFxMovieDef, +52 GFxMovieView (root)
  - root vtable slot 45 = HandleEvent (82BCD738); event {+0 type (1 move, 2 down, 3 up, 4 wheel, 5/6 key, 8 focus),
    +4 x, +8 y (viewport px, x20 = twips), +0x10 button, +0x14 mouse index < root+0x9B4 (mouse count)}
  - Advance (vtable 37) drains the input queue (82BDB900, queue at root+0x124); queued mouse 82BD8FE0; hit test
    82BCD5E8
  - Fed MouseMove/Down/Up from the game thread (gfx_menu::OnGameThread, from hid::GetState): HandleEvent returns 3,
    and the hit test finds a different item under HORDE and BEAST.
- **The menu does not react**: the main menu is a CLIK MenuList of MenuItem renderers, and the CLIK Button code with
  onRollOver/onPress/onRelease is present, but rollover shows nothing and Down/Up (on separate frames) do not
  select. Most likely the visible highlight is keyboard focus, and the mouse states are not drawn or not wired.
- Next idea: read the focused item and step the d-pad toward the hovered one. A root field that holds the focus
  is not found yet: root+0x2448 changes every frame, so it is not that.
- Debug switches: GOW3_GFX_CLICK=1 (Scaleform clicks instead of A), GOW3_GFX_TRACE=1 (runtime/gfx_debug.cpp: queued
  mouse and hit-test results), GOW3_GFX_FOCUS=1 (watch root+0x2448). The shipped behaviour is unchanged: click = A on
  the highlighted option, wheel = d-pad, right click / Esc = back; MouseMove events are sent and are harmless.

## 2026-10-11: Mouse hover in menus (working on CLIK lists)
- The menus *do* get Scaleform mouse input (previous entry), but they highlight by keyboard focus, and CLIK's mouse
  states draw nothing. So hover drives the focus:
  - **Lists**: CLIK renderers are sprites (vtable 82024090) named MenuItemN (+0x138); once Scaleform has needed it,
    +0x13C holds the renderer's full path (e.g. _level0.MainMenuSP.MainMenu.Nav.MenuList1.MenuItem1), whose parent
    is the list. A heap scan once a second collects the lists; MenuItem1..20 are probed under each.
  - **Reading AS from C++**: the scene's own UGFxMoviePlayer::GetVariableNumber (vtable +0x148; native
    execGetVariableNumber 82911D90, UFunction native pointer at +136) and SetVariableNumber (+0x15C, 829121C8),
    with an FString path. For each renderer: _xmouse/_ymouse vs _width/_height = under the cursor; _displayFocus =
    highlighted. Plain fields only. Setting the list's selectedIndex changes nothing visible (it stays 0); the
    `selected` getter also marks the highlight but runs ActionScript.
  - **Input interference**: any GetVariable from the input poll makes the menu drop that frame's pad input (shown by
    switching queries off). So a query pass runs only when the cursor has rested 80 ms and nothing was pressed for
    400 ms. It plans |hovered - highlighted| d-pad presses, sent 250 ms apart with no queries (30 ms each: a longer
    press read as a press + repeat in the campaign submenu); a later pass verifies. A click (A) waits until a pass
    confirms the highlight is on the hovered item (at most 1.5 s).
  - **Top menu only**: if any open menu scene has bCaptureInput, only those are hovered (Options over the main menu
    otherwise answered with the hidden main-menu list).
- Checked: main menu, cursor on BEAST -> highlight walks there (3 steps), click opens Beast; Campaign submenu, cursor
  on ARCADE -> 1 step, click opens Arcade (tests/mouse_hover.txt, tests/mouse_hover_sub.txt).
- Known gap: lists whose renderers never had their path cached (seen: Options) are not found, so no hover there;
  wheel/click/Esc still work. Next: a parent link from renderer to list (not found at +0x4/+0xC/+0x34), or asking
  Scaleform for a path.
- Removed the investigation scaffolding (runtime/gfx_debug.cpp hooks, root diff/focus watchers, Scaleform-click
  experiment). Test-harness note: a bash loop that passes "tests\$t.txt" yields "tests$t.txt" (0 events), which
  looks like the game ignoring Start.

## 2026-10-11: Hover selects directly; clicks on sliders and steppers (all CLIK lists)
- Read the menus' ActionScript: the SwfMovie exports in GearStart.xxx (UI_Frontend.UI_FrontEndAssets, UI_Options.*,
  ...) hold uncompressed GFX files (tag "GFX", after the export's properties); JPEXS FFDec (offline, D:	oolsfdec)
  decompiles them. Findings:
  - The menus are CLIK ScrollingLists (gears.controls.MainMenuList, OptionsMenuScrollingList, OptionsScrollingList,
    GearDialog.List). The selection is the list's selectedIndex property; ScrollingList.handleInput sets it for a
    d-pad press, and its setter moves the highlight, scrolls, updates the tooltip and calls Owner.SelectedIndexChanged.
  - The main menu's MenuList1 is a holder clip: the list is Nav.MenuList1.MenuList, with the MenuItemN renderers
    placed beside it (externalRenderers). That is why "selectedIndex stays 0" earlier: it was read on the holder.
- **UGFxMoviePlayer::Invoke** is vtable +0x190 (execInvoke 82912FB0): r3 FASValue result, r4 scene, r5 FString
  method path, r6 TArray<FASValue>. FASValue is 0x18 bytes: +0 Type byte (0 undefined, 1 null, 2 number, 3 string,
  4 boolean), +4 B, +8 N float, +0xC S FString. AS2 class setters compile to methods, so
  Invoke("<list>.__set__selectedIndex", n) runs the setter.
- **Finding the list, any menu**: Invoke("Selection.getFocus") returns the focused list's path; its renderers are
  "<list>.renderers.N" (GetVariable paths go through arrays; "renderers.length" too). Each renderer: _xmouse/_ymouse vs
  _width/_height = under the cursor, `index` = the option (plain field). The Scaleform heap scan (FindLists) is gone.
- Hover: when the cursor rests 30 ms, one query pass; if the hovered option is not the selection, the setter is
  invoked, and a pass 30 ms later confirms it. The d-pad walk stays as a fallback if the setter ever does not take.
  The Options lists also select on mouse rollover by themselves (OptionsScrollingList's itemRollOver handler), now
  that they get MouseMove.
- Clicks (once the selection is on the hovered option): option rows (OptionListItemRenderer) with a visible
  MySlider: the slider goes to the clicked point (Slider.trackPress's formula from _minimum/_maximum/_snapInterval,
  offsetLeft/offsetRight, __width); setting `value` does not dispatch "change", so it is set one step short and the
  last step is a d-pad press (Slider.handleInput dispatches it, and the game saves it). Rows with a visible
  MyOptionStepper: left on its prevBtn, right anywhere else on the row. Other options: A. A click beside the options
  of a focused list does nothing; with no list (or hover off), a click is A.
- Also: the menu worker could start before GFxMoviePlayer's properties were linked (offsets -1, no mouse in menus
  for that run); it now waits for them.
- Checked (scripted, separate test copy with its own LOCALAPPDATA): main menu -> BEAST (select 3, opens Beast);
  Campaign -> ARCADE; Options -> CONTROLLER; Controller page: Look Sensitivity slider click 0.8 -> 0.45 (+10 -> +3),
  Control Scheme prevBtn -> ALTERNATE, Invert Y row -> ON, click on empty space ignored; pause menu ->
  RESTART CHAPTER, OPTIONS (tooltips follow). tests/options_hover.txt, options_click.txt, pause_hover.txt.
- Not yet: clicking the button-prompt bar (B BACK etc.), dragging a slider (a click sets it).

## 2026-10-11: Hover latency (follows the moving cursor, about one frame behind)
- Report: hover felt slow (~300 ms). Measured with a scripted glide (new script event `glide x y ms`; tests/hover_speed.txt,
  GOW3_HOVER_TIMING=1 logs each selection with its time and the cursor y): the old design (a pass only once the cursor
  had rested 30 ms) did not move the selection at all during a 1.5 s glide down the main menu, and only 110 ms after
  it stopped; a jump took ~80 ms.
- Found on the way (a temporary in-process sampling profiler: suspend the thread, RIP + StackWalk64, dbghelp names
  from gow3.pdb; recompiled functions show as __imp__sub_XXXXXXXX):
  - **Two threads call XamInputGetState** (hid::GetState): the game's main loop (_xstart -> 8253D9B8 -> 8296CC20 ->
    8296DCB8 -> 8296B618) and a second thread (82CF6468 -> 823255F8 -> 8296BD28). Passes that ran on the second one
    raced the game thread: Scaleform did not apply the mouse moves while they kept coming (renderers' _ymouse stayed on
    the first row), and lookups were slow. Hover now runs on the main thread only (g_mainThreadId, set in wmain:
    _xstart runs on the process's main thread). This is probably also what "lookups make the menu drop pad input" was.
  - ue3::Read32 asks VirtualQuery every time: about a quarter of a pass went to it (the scene's vtable read per
    lookup). Vtable reads on scenes are now plain.
  - A lookup (GetVariableNumber) then costs ~0.2 ms, Invoke("Selection.getFocus") ~2 ms.
- Design now: once per frame on the game thread, first the pass for the previous frame's MouseMove (Scaleform has
  applied it in between), then this frame's MouseMove. Passes run only while the cursor moves. The focused list's
  path and its renderers' _y are cached for 250 ms (and refreshed after a click); the search starts at the last row hit.
  The setter result is read back at once (it runs synchronously), so there is no check pass.
- Rows are hit-tested by their spacing (a row runs from its renderer's _y to the next one's): renderers with a "new"
  icon (STATS & AWARDS, EVENT CALENDAR) are taller than their row, and their _height swallowed the row below
  (XBOX LIVE MARKETPLACE could not be hovered).
- Result (glide down the main menu): each row is selected 1-3 px after the cursor crosses into it (about one frame,
  10-25 ms at the glide's speed); all 7 rows. A jump: 15 ms (was ~80). Pass cost 0.5 ms average, 4 ms worst (the
  250 ms focus refresh). Rechecked tests/mouse_hover, mouse_hover_sub, options_click, pause_hover.
- tools/xpack: XPACK_SKIP_BAD=1 zero-fills blocks that fail to decode and goes on (GearGame.xxx: blocks 340-347), so
  the rest of the package parses (UI_Common.* SwfMovies). The front end's button-prompt bar (UIScene_CalloutsContent)
  is not in GearStart's or GearGame's movies; still to find.

## 2026-10-11: Project moved to D:\Gears of War PC ports
- Everything (gow2-port, gow3-port, both -decomp folders, xenia, tools) now sits in one folder. The junctions
  (game_link, gow3-port\xe_glue, tools\XenonRecomp, tools\UnleashedRecomp) pointed at the old D:\ locations and were
  recreated; the CMake build trees of xenia\build-gow2, gow3-port\build and gow2-port\build were configured again
  (CMake caches cannot move; a full rebuild of all three takes 3 minutes). Desktop shortcuts updated.
- Helper scripts no longer name D:\gow3-port: Python tools use their own location, playtest.ps1/shot.ps1
  $PSScriptRoot, the .cmd helpers env.cmd. The path has spaces: playtest.ps1/shot.ps1 now quote the disc argument
  (PowerShell 5.1's Start-Process passed it unquoted and the game looked for D:\Gears\default.xex). run_once.cmd was
  already broken (backslashes eaten) and is rewritten.

## 2026-10-11: Trilogy, first milestone: a Gears 2 texture in Gears 3
- **tools/upk.py**: the export table is now read exactly: after the 40 fixed bytes come export flags, the
  NetObjectCount array, the package GUID and package flags, with no ComponentMap in either 575 or 828; the table
  ends exactly at DependsOffset. The 7 Gears 2 packages that failed now parse (40 of 40 in the census sample; Gears 2
  Texture2D totals 127 MB there, not 88). UTF-16 names are little-endian even in Xbox packages (CJK names in
  GearGame.xxx). New: Package.properties() (tagged properties; BoolProperty is a u32 in 575 and a byte in 828,
  ByteProperty has an enum name from 633), the summary's flag/chunk offsets and each export's table offset.
- **Texture2D is serialized the same way in Gears 2 and 3** (tools/texture2d.py): properties, an empty SourceArt bulk
  header, then the mips, each {bulk flags, element count, size on disk, offset in file} (+ data when inline),
  SizeX, SizeY; then the texture cache GUID (Gears 3: plus 4 bytes, an empty array).
  | bulk flags | meaning |
  |---|---|
  | 0x81 | in the .tfc named by TextureFileCacheName, LZX (XMemCompress chunk) |
  | 0x00 | inline in the package (mips from FirstResourceMemMip / the mip tail) |
  | 0x21 | empty (dropped top mip, or a mip inside the tail of a larger one) |

  Pixel data is the same in both games: tiled Xenos surfaces, 16-bit big-endian words, DXT1/DXT5/A8R8G8B8/G8
  (no BC5 seen). texture2d.py lists, describes and decodes any mip to PNG (vectorized untiling); checked on Gears 3
  Dom (CharTextures.tfc), Gears 2 Marcus (GearGame.xxx, Textures.tfc), a Gears 2 vehicle.
  Gears 2's characters are in GearGame.xxx (compressed as a whole), not in the level packages.
- **mods\ folder** (runtime/kernel/gow2_files.cpp): build\mods\<path on disc> overrides the disc file; each override
  is logged once ("files: mod override ...").
- **tools/modpack.py** builds a mod folder from a list of replacements (mods_src/g2_marcus_test.txt):
  - the Gears 3 package is written uncompressed with every offset kept: CompressionFlags 0, chunk table emptied
    (padded), and **PKG_StoreCompressed (0x02000000) cleared**: with it set, the loader builds its chunk map
    (sub_822E9E78 -> 822B92D8) and read chunks[count - 1] of an empty table (crash, guest address FFFFFFF4);
  - the Texture2D gets new serial data appended at the end, its export entry pointing there: Gears 3's properties
    with TextureFileCacheName = "Textures" number 2, i.e. **Textures_1**, then Gears 2's mips. Mips from Gears 2's
    cache are copied as they are (LZX chunks) into mods\...\Textures_1.tfc; the engine opens caches by name on first
    use (it tries Textures_1.xxx, then Textures_1.tfc), so no name-table change is needed;
  - Xbox360TOC.txt (size, uncompressed size, path, MD5; the engine reads it at boot) is copied with the new sizes
    and MD5s and the new cache.
- Result (tests/continue.txt, Jacinto flashback): Marcus's bandana and Anya's body texture (SP_Nightmare_01_S and
  _Cine both hold a copy) replaced by Gears 2 Marcus's armour sheet: it streams in at full resolution, no crash,
  60 fps. The UVs belong to another mesh, so it is a pipeline test, not a fit (shots_g2_texture.png). Without
  build\mods the game is unchanged. The test mod is not left in build\: `python -P tools/modpack.py build/mods
  ../gow3-decomp/disc/GearGame/CookedXbox360 ../gow2-decomp/disc/GearGame/CookedXenon mods_src/g2_marcus_test.txt`.
- Limits: same format and size only (no resizing yet); one package rewrite per target package; Gears 3 keeps copies
  of a texture in several packages, and every copy that may load first must be replaced.

## 2026-10-11: Trilogy goal clarified; Gears 2 campaign vs Gears 3 engine, measured
- Goal (the user's words): play the Gears 1 and 2 campaigns **in Gears 3's engine, with all of its gameplay and
  sandbox features**: content converted into Gears 3, not the other ports launched from its menu. (A launcher entry
  in the campaign menu was started and backed out; what was learned about Scaleform stays below.)
- **tools/class_compat.py** (with upk.Package(tables_only=True): only the chunks holding the tables are unpacked;
  whole-file compressed packages now load directly): every class a campaign's levels instantiate, and whether the
  other game defines or uses it. Output kept outside the repository (..
esearch\g2_sp_classes.tsv).
  Gears 2 campaign: 452 SP_ packages (1 unreadable: SP_Fort_Wave_Stranded01 is not Gears 2's, an LZX error), 939
  classes, 2.33 M objects. Present in Gears 3 by name:
  | category | classes | in Gears 3 | objects | objects covered |
  |---|---|---|---|---|
  | geometry, lighting, materials, sounds, actors ("other") | 490 | 433 | 1,483,369 | 99.9% |
  | components | 30 | 29 | 277,683 | 100% |
  | navigation / cover | 20 | 15 | 238,654 | 86.6% |
  | old UIScene UI (cooked into every level) | 33 | 1 | 208,622 | 1.1% |
  | Kismet / Matinee | 250 | 197 | 122,202 | 97.3% |
  | pawns / AI / weapons / vehicles | 116 | 66 | 1,692 | 80.3% |
  - The old UI (UIState_*, UIComp_*, GearUIScene_* pause/game-over/path-choice scenes) is Gears 2's HUD and menus;
    Gears 3 replaced the system with Scaleform, so it is dropped, not ported.
  - **Navigation is the structural difference.** Gears 2 levels use the waypoint graph: 11,263 PathNode, 136,857
    ReachSpec, 33,755 SlotToSlotReachSpec, 30,966 CoverSlotMarker, mantle/swat-turn/cover-slip specs. Gears 3's
    campaign uses navigation meshes: 719 Pylon, 1,516 NavigationMeshBase, 13 PathNodes in all. CoverLink (cover)
    exists in both (G2 9,212, G3 12,997). Gears 3's AI cannot walk Gears 2's graph: converted levels need a navmesh.
  - **Gears 3's executable still contains the navmesh generator**: its stages' status strings ("Exploring seeds",
    "Simplifying mesh", "Splitting mesh for imported meshes", "Splitting mesh around path objects", "Fixing up
    mesh", at 8215C954..8215CA40) are loaded by sub_825BDB60, sub_825BDEC8 and sub_825BE2C8, which call real
    algorithms (825C9478 is 2.7 KB of code) and have no direct callers (a vtable, or dead code). Not yet confirmed
    by running it. If it works, Gears 3 can build navmeshes for converted levels itself (from Pylons placed where
    Gears 2 had PathNodes).
  - Missing gameplay classes are Gears 2's set pieces and enemy variants: Derrick (InterpActor_COGDerrick,
    Turret_Derrick, SeqAct_DerrickControl), Centaur (CinematicCentaur, SeqAct_CentaurSettings), Reaver ride,
    barges, Hydra, Riftworm, Sire tubes, security bots, grappling drones, flocks (Kryll-like FlockSpawner/
    FlockAttractor), Tai, knockable trees, popup cover, ladder markers; ~25 Kismet actions. Each needs Gears 3 code:
    UnrealScript bytecode for engine version 828, or native code in this runtime (any guest function can be
    called or replaced from C++).
  - "Present by name" is not "loads unchanged": tagged properties are tolerant (unknown ones are skipped), but
    classes with native serialization (Level, Model/BSP, StaticMesh, SkeletalMesh, AnimSequence, lightmaps,
    physics) need a converter each. Next measurement: property-level differences of the shared classes.
- Scaleform API found on the way (for any future menu work): UGFxMoviePlayer natives are registered as
  {"UGFxMoviePlayerexec<Name>", thunk} pairs; vtable: CreateObject +0x188 (FString* class, UClass*),
  GetVariableObject +0x150, SetVariableObject +0x164 (FString* path, UGFxObject*), GetVariableNumber +0x148, Invoke
  +0x190. UGFxObject: SetString sub_8292D488 (obj, FString* member, FString* value, context), SetFloat sub_8292D388
  (f1), SetElementObject sub_8292DC60 (obj, index, child). The main menu's MenuData is an AS array of screens
  {Title, Options[{label, ToolTip, Next...}]}; a choice goes to UnrealScript via Callback_MenuOptionWasSelected.

## 2026-10-11: Gears of War 1 disc
- The player's Gears of War disc image (7.8 GB) extracted with tools/xdvdfs.py to
  ..\gow1-decomp\disc (1,810 files, 6.3 GB, 14 s). default.xex SHA-256 df1041da...efe2d1. Game folder **WarGame**
  (Gears 1's game module is WarGame; renamed GearGame from Gears 2 on), cooked folder WarGame\CookedXenon: 1,745
  files, 947 packages without the _LOC_ ones. **No .tfc texture caches**, and levels are not seekfree: content
  packages (COG_Dom.xxx, COG_Anya.xxx, COG_City_*...) are loaded by reference.
- **Package version 374** (engine build 2451, cooker 32). tools/upk.py now reads it:
  - summary: no DependsOffset (the header size at +8 bounds the tables), generations of 3 ints;
  - export entries: the 40 fixed bytes, then a **ComponentMap** (count + {FName, index}; dropped by 543), export
    flags, NetObjectCount array, GUID, and no package flags (added by 475): 68 bytes each when empty;
  - **compression flag 2 = LZO** (Gears 2/3: 4 = LZX). Chunk headers have no block size (the tag again; 128 KB
    blocks). Whole-file compressed packages (Core, Engine, WarGame ...) have a **little-endian** wrapper
    (c1 83 2a 9e) around a big-endian package.
  - tools/xpack: an LZO1X decoder (lzo1x_decompress_safe's format, bounds-checked), `xpack in out <offset> lzo`,
    and the little-endian wrapper. upk picks the codec from the compression flag / the wrapper's byte order.
  All 947 packages' tables parse.
- Textures (tools/texture2d.py): every mip is inline, LZO-compressed (bulk flag 0x10), no cache GUID; Format is a
  raw EPixelFormat byte (version 374 writes ByteProperty without its enum name): 2 A8R8G8B8, 3 G8, 5 DXT1, 6 DXT3,
  7 DXT5. Same tiled Xenos data as Gears 2/3: COG_Dom_Body1_CP_Tex decodes (1024 top mip; 2048 dropped at cook).
- Gears 1 campaign vs Gears 3 (tools/class_compat.py, which now also tries Gears 1's names as Gears 3's: War* ->
  Gear*, WDO_ -> GDO_): 421 SP_ packages, 664 classes, 2.23 M objects. 423 classes exist in Gears 3, 39 more under
  the GearGame name (3,708 objects: WarAnim_* animation nodes, WarPhysicalMaterialProperty, pickups, destructible
  objects, Kismet player-anim/vehicle actions), 202 missing (35,476 objects; 23,751 of them CoverSlotMarker).
  Missing gameplay: Kryll (SeqAct_WarSetKryllFlockTarget, KryllRepulsor, SeqEvt_KryllFlockIsDead), Hammer of Dawn
  toggles, objectives (SeqAct_AddObjective/CompleteObjective), WarCheckpoint, music tracks/moods, the Boomer and
  Torque Bow projectile classes of Gears 1, emergence-hole spawners. Navigation: waypoints like Gears 2 (5,173
  PathNode, 183,148 ReachSpec, 23,751 CoverSlotMarker, 8,584 CoverLink): the same navmesh problem and solution.
- Also fixed: Gears 3's GearGame.xxx (whole-file, one bad LZX block) loads again for censuses.

## 2026-10-11: StaticMesh in all three games; Gears 1/2 meshes rendered by Gears 3
- **tools/staticmesh.py** reads StaticMesh in 374, 575 and 828 byte-exactly (the reader must end on the export's
  last byte). Sweep (tools/mesh_sweep.py, 120 random packages per game): Gears 1 2,355 of 2,355, Gears 2 3,742 of
  3,742, Gears 3 3,256 of 3,256 after the fixes below (two of the 360 packages hit the LZX bug further down).
  Fields are **not aligned** (one-byte flags inside), so the reader is a byte cursor.
  | | Gears 3 (828) | Gears 2 (575) | Gears 1 (374) |
  |---|---|---|---|
  | after Bounds (7 f), BodySetup ref | kDOP bounds (6 f), compact 6-byte nodes, bulk | 32-byte nodes, bulk | 36-byte nodes, plain array |
  | kDOP triangles | bulk {u16 x3, material} | same | plain array of 4 u32 |
  | InternalVersion | 18, then TArray<FName>, TArray<float> (one value on the convoy meshes), int | 18, then TArray<FName> | 15 |
  | element | 9 ints, fragments, a 1-byte flag | 9 ints, fragments | 7 ints |
  | vertices | positions (bulk float3) + {TangentX, TangentZ, half UVs}: 8 + 4 x UVs bytes | positions + {TangentX, TangentZ, FFFFFFFF, half UVs}: 12 + 4 x UVs | one interleaved array {pos, 4 bytes, TangentX/Y/Z, float UVs}: 28 + 8 x UVs |
  | colours | stride, count, bulk only if count > 0 | bulk always | none |
  | after the index/wireframe lists | - | edges bulk (16-byte), per-triangle byte array | same, and one int |
  | tail | 1, thumbnail angle, distance, high-res name/CRC, lighting GUID, int, streaming factors, 1 | 1, angle, distance, name, CRC | 1, angle |
  Packed normals are stored byte-reversed (w z y x); TangentZ.w is the binormal sign (255 / 0). Gears 3 kDOP trees
  are complete binary trees (node counts are powers of two) and may be empty (one shipped mesh has 0/0).
- Render check (tools/meshview.py: OBJ -> flat-shaded PNG from four sides): Gears 3 COG_Pillar041305, the same
  asset from Gears 1's SP_Prison_S02_Meshes (same shape, 1,913 against 1,230 vertices), a Gears 2 Landown window.
- **tools/meshconv.py**: a Gears 1/2 (or 3) mesh as Gears 3 serial data on a Gears 3 template mesh (its properties,
  materials by element, body setup, tail fields): tangents repacked (Gears 1: the sign from TangentY; float UVs to
  halves), UV sets padded to the template's LightMapCoordinateIndex, LOD 0 only, no vertex colours, an empty kDOP
  tree. It reads its output back with staticmesh.py and compares vertices and triangles.
- **tools/modpack.py** takes `mesh <gears3 package> <mesh|*> g1|g2|g3:<package> <mesh|=>` lines (texture lines
  stay as they were); a Gears 1 cooked folder is the optional 5th argument.
- In game (tests/continue.txt, the Jacinto flashback):
  - every static mesh of SP_Nightmare_01 and _01_S (76) replaced by Gears 2's Landown window frame: the cell's walls
    and doorway become window lattices, no crash (shots_g2_meshes.png, mods_src/g2_mesh_test.txt);
  - every one of them rewritten by meshconv from itself: **indistinguishable** from the shipped game (mean pixel
    difference 1.8-4.4 of 255, the run-to-run noise; lightmaps included, shots_mesh_roundtrip.png,
    mods_src/g3_mesh_roundtrip.txt). The writer reproduces Gears 3's format.
- Not done: other LODs, vertex colours, a real kDOP tree (per-polygon collision), full-precision UVs. A converted
  mesh with another vertex count than the component's lightmap data is only correct once the level's components
  are converted too (milestone 2).
- Known: some LZX blocks fail in xpack (Gears 2 SP_Sinkhole_W, Gears 3 MP_Checkout, SP_Wreckage_01_S_General,
  GearGame.xxx block 340). They hold XMemCompress frames of odd length (e.g. 32784, 32768, 31437, 32812 in one
  block): the frames have to be decoded with the bit reader restarted at each frame, which libmspack's lzxd (one
  continuous stream) does not do.

## 2026-10-11: Milestone 2 started: Gears 1 level geometry inside a Gears 3 level
- **tools/upkwrite.py**: rewrites a Gears 3 package with names, imports and exports added and export data replaced.
  No export data moves (inline bulk data records absolute file offsets): the new name/import/export/depends/GUID
  tables are written at the end of the file and the summary (and the newest generation's counts) points there.
  Checked: SP_Nightmare_01 with relocated tables and no other change loads and plays normally.
  (Gears 3 header: summary, names, imports, exports, depends (one empty array per export when cooked), the empty
  import/export GUID table at TotalHeaderSize, then export data.)
- **How Gears 3 levels hold meshes**: StaticMeshCollectionActor. After its properties (StaticMeshComponents array,
  Tag) come one 4x4 float matrix per component: rotation and translation only (unit rows, translation in row 3);
  the component keeps Scale3D. A StaticMeshComponent's data: int 0, an int (an object ref, TemplateOwnerClass?),
  the tagged properties (StaticMesh, IrrelevantLights, Materials, VisibilityId, LightingChannels, Scale3D...), then
  native LODData with the lightmaps (an empty array, 4 zero bytes, = no static lighting; 5 shipped components).
- **Gears 1 levels**: StaticMeshActor = a 32-byte state frame, then properties (StaticMeshComponent, Location,
  Rotation (65536 = 360 degrees), DrawScale[, DrawScale3D]); its StaticMeshComponent has properties from offset 8
  (StaticMesh...) and per-vertex lighting after them (2,396 bytes on a floor tile). MP_Gridlock holds its 148
  meshes itself (2,034 actors; extents -9,570..39,854 x -22,716..13,430 x -12,112..4,284).
- **tools/levelinject.py**: a Gears 1/2 level's StaticMeshActors into a Gears 3 level: every distinct mesh converted
  once (meshconv, on a template mesh: --template picks its materials), every actor a new component cloned from a
  Gears 3 template component with its mesh, Scale3D, no lightmap and VisibilityId -1 (never culled by precomputed
  visibility), appended to a collection actor with its matrix (UE3 FRotationMatrix of the rotator, plus
  --offset). --center/--radius select the actors.
- **runtime/ue3/pos_log.cpp**: GOW3_LOG_POS=1 logs every Pawn's location and yaw every 2 s (Actor.Location +84,
  Rotation +96 in Gears 3). tests/continue_walk.txt: the flashback's cutscene, then walking: Marcus goes from
  (168, 592, 187) to (-714, 1177, 203).
- In game: 191 Gridlock actors (radius 1,500 around its centre, 60 meshes) moved onto the flashback's cell area
  (--offset -636,480,-192): the Gears 1 buildings stand in the Gears 3 corridors, as black walls and floors
  (shots_g1_gridlock_inject.png). No crash, 60 fps.
- What is missing for a playable level: **lighting** (the components have no lightmaps and this level's lights are
  baked, so unlit = black; Gears 1's components carry per-vertex lighting that could become Gears 3 LightMap1D
  data, or a light environment could light them), **collision** (empty kDOP trees: Marcus walks through), the
  original materials (all take the template's), BSP, and meshes referenced from other packages (imports).

## 2026-10-11: Gears 1 geometry lit and solid in Gears 3
- **Static lighting, read** (tools/lighting.py). StaticMeshComponent LODData after the tagged properties: count,
  then per LOD ShadowMaps (refs), ShadowVertexBuffers (refs) and the light map:
  | | Gears 1 (374) | Gears 3 (828) |
  |---|---|---|
  | type 1, per vertex | LightGuids, owner ref, bulk {flags, count, size, offset} of **3 FColors** per vertex (three directional coefficients), 3 scale vectors | LightGuids, owner ref, bulk of **2 FColors** per vertex, 3 scale vectors, an empty bulk header, a byte, int 0 |
  | type 2, textures | LightGuids (no owner), 3 x {LightMapTexture2D, scale}, coordinate scale and bias | 2 textures, NormalizedAverageColor and DirectionalMaxComponent, with scales |
  FColor bytes are A R G B. Gears 1's MP_Gridlock: 1,690 components with per-vertex lighting, 300 with texture
  light maps, 44 none (all 2,044 level components parse to the end; the 62 failures are class defaults).
- **Converted**: per vertex, Gears 1's three coefficients (texture light maps sampled at the vertices' lightmap UVs)
  become Gears 3's pair: the average colour normalized to its largest channel, and the three directional maxima
  normalized to the largest (its scale in the second scale vector). levelinject writes it as the new component's
  light map (owner = the component itself); 176 of 191 injected components lit (the others: no source lighting or a
  vertex count that does not match). Result: the injected Gears 1 buildings are lit as Gears 1 lit them, in Gears 3's
  renderer. The scale semantics are inferred (one shipped Gears 3 example); brightness may need calibration.
- **Collision: Gears 3's compact kDOP tree, found and rebuilt** (tools/kdop.py):
  - new debugging tools: GOW3_FIND=<hex>[;...] (runtime/ue3/mem_find.cpp: guest memory search, 64 KB-page heap,
    fault-guarded) and GOW3_FIND_WATCH=1, which arms a **read** watchpoint on each match; runtime/watch.cpp now has
    four slots (DR0-DR3), run-time arming of every guest thread, `,rw` read mode, GOW3_WATCH_AFTER (from process
    start) and no stack walk (it fast-failed: reading the watched value inside the handler re-triggered it).
  - the node test is **sub_826CBBB0** (called from 826CAB20 < 826C2CC4 < 825F5098 < 825F5284, recursive): VMX code
    with constants 127.5, +/-0.5 clamps, 1/127 and -1/254 (820CD200-820CD250).
  - format: N 6-byte nodes (N = next power of two >= ceil(triangles / 5)); node k encodes BOTH children's boxes
    relative to its own: a byte b above 128 insets child A's bound by (b - 128) / 127 of the extent, below 127 child
    B's by (127 - b) / 127 (min bytes 0-2 from the minimum, max bytes 3-5 from the maximum); children 2k+1 / 2k+2
    clamped to N - 1, a zero sentinel: any index >= N - 1 is a leaf; triangle ranges halve, the first child taking
    count - count / 2, so triangles are in depth-first leaf order.
  - checked: 94,772 of 94,777 shipped leaves contain their triangles (the 5: rounding). The builder (split on the
    centroids' widest axis, floor-rounded insets, the larger inset per byte) rebuilds all 274 trees of
    SP_Nightmare_01 and MP_GridlockG3 with the shipped node count and every leaf containing its triangles exactly.
  - meshconv builds the tree for every converted mesh (material index = element).
  - levelinject drops the template component's BlockNonZeroExtent=False (back to its default, true), so pawns
    collide (--no-collision keeps it).
- In game (tests/continue_walk.txt, GOW3_LOG_POS=1): without collision Marcus walked to (-714, 1177); with it he is
  deflected at (209, 1070) and stops at (61, 1318) against a Gears 1 Ephyra column (shots_g1_collision.png).

## 2026-10-11: Gears 1 materials on Gears 3 shaders
- **Gears 3 side**: a MaterialInstanceConstant whose parent takes texture parameters: in SP_Nightmare_01,
  M_COG_Jacinto_Trims_Wood_02_V01 (parent M_ES_Phong_Opaque_Master_01, imported from Engine-class material
  package): TextureParameterValues Diffuse_01 / Normal_01 / Specular_01, scalars (SpecPower1/2, Tex_Scale_02...),
  vectors (Diffuse_01_Color...), bHasStaticPermutationResource with 1,756 bytes of compiled shaders after the
  properties. Arrays of structs are counts followed by tagged properties per element ({ParameterName,
  ParameterValue, ExpressionGUID}). A clone with only texture parameters changed keeps valid shaders.
- **Gears 1 side**: plain Materials; DiffuseColor / Normal / SpecularColor are tagged structs whose Expression leads
  through the expression graph (inputs are ExpressionInput structs); the shallowest TextureSample with a texture is
  the slot's (fallback: ReferencedTextures by name). E.g. COG_A_destroyed_Mat: diffuse COG_A_destroyed_Tex, normal
  COG_A_destroyed_Normal (under Adds of detail normals), specular = the diffuse.
- **Gears 1 texture data in maps**: mips flagged 0x11 ("separate", LZO) are in the texture's own content package,
  the outermost package of its path (COG_AssaultRifle.Textures.X -> COG_AssaultRifle.xxx), at that file's raw
  offset, as an LZO chunk (tools/texture2d.py reads them).
- **tools/materialconv.py**: raw tagged-property records (read, edit, re-encode with new names), Gears 1 material ->
  textures, Gears 1 texture -> Gears 3 Texture2D (a slot-matched Gears 3 texture's properties: size, format name,
  MipTailBaseIdx; TextureFileCacheName and FirstResourceMemMip removed, NeverStream = True; every mip inline,
  uncompressed, with its BulkDataOffsetInFile fixed up by tools/upkwrite.py (new: fixups)), and the material
  instance clone. **The empty top mips are dropped** (size from the first stored mip, MipTailBaseIdx shifted): with
  them, a texture loaded whole had every mip at the wrong place (striped garbage in game).
- levelinject --materials <instance>: textures converted once, one instance per Gears 1 material, meshes' elements
  pointing at them; cloned components lose Materials (it overrode the mesh's materials with the template's) and
  IrrelevantLights. Gridlock's core (radius 1,500): 43 materials, 97 textures, a 39 MB package.
- In game: the injected Gears 1 geometry shows Gears 1's textures (cracked concrete columns, stone ledges and
  floors) under Gears 3's shading, lit by its own Gears 1 lighting, solid (shots_g1_materials.png).
- Not yet: Gears 1 materials that are not plain Materials, special materials (glass, emissive, masked, decals), and
  the template instance's colour/scale parameters (one instance for every material).

## 2026-10-11: Gears 1 BSP as Gears 3 static meshes, with its own light maps
- **UModel (374)**, after its empty tagged properties (tools/bspconv.py): Bounds (7f), Vectors (count + float3),
  Points (count + float3), Nodes (count + 68-byte FBspNode: plane, zone mask (u64), iVertPool, iSurf, iVertexIndex,
  component index, component node index, component element index, iBack, iFront, {iZone[2], NumVertices,
  NodeFlags} bytes, iLeaf[2]), the owner ref of the next array (TTransArray: the Model's own export ref), Surfs
  (count + 52-byte FBspSurf: Material, PolyFlags, pBase, vNormal, vTextureU, vTextureV, iBrushPoly, Actor, plane,
  ShadowMapScale), Verts (count + 24-byte FVert: pVertex, iSide, ShadowTexCoord (2f), BackfaceShadowTexCoord
  (2f)), then NumSharedSides, NumZones... MP_Gridlock's world model (Model_2, the largest Model of the level): 139
  vectors, 1,731 points, 1,119 nodes, 650 surfs, 17,932 verts -> 1,119 polygons, 3,166 triangles, 18 materials.
  Rendered top-down it is Gridlock's layout (research/shots/g1_gridlock_bsp.png).
- A node with vertices is a convex polygon (fan); UVs as UE3 builds BSP vertex buffers: ((P - base) . TextureU) / 128
  (shifted by whole tiles toward 0 for half precision). Winding: cross(v1 - v0, v2 - v0) points against the normal
  (99.9% of Gears 1's mesh triangles), so the fans are wound that way. Polygons are grouped in 4,096-unit cells
  (< 60,000 vertices each) into meshes with one element per material and go through meshconv like any mesh
  (collision tree included).
- **ModelComponent (374)**: after its properties, the Model ref, ZoneIndex, Elements (count; each: a light map exactly
  like a static mesh LOD's, Component ref, Material ref, Nodes (count + u16), ShadowMaps, IrrelevantLights (GUIDs)),
  then its own fields. A node's light map is that of the element listing it; its vertices' ShadowTexCoord (x
  CoordinateScale + CoordinateBias) addresses the light map textures. MP_Gridlock: all 1,119 nodes listed, 491 with
  texture light maps, 628 with none (type 0: the actors' average lighting is used there).
  lighting.py: read_lightmap_374 (shared by meshes and BSP) and sample_lightmap_374 (decoded textures cached).
- levelinject --bsp: the world model's polygons (within --center/--radius) as mesh chunks + components (world space +
  --offset), materials through the same conversion, light from the BSP's own light maps.
- In game: Gridlock's core BSP (5 chunks) stands among the injected actors, textured with Gears 1 materials and shaded
  by its Gears 1 light maps (a flat ambient version looked pale and striped).

## 2026-10-12: All of Gears 1's Gridlock inside Gears 3's Gridlock, in Horde
- **Getting there by script** (tests/horde_gridlock.txt, tests/horde_gridlock_look.txt): main menu -> HORDE (2 down)
  -> PRIVATE (1 down) -> A (start) -> A (confirm) -> map selection: the **right trigger** cycles maps (Checkout,
  Drydock, Gridlock, Hotel, Mercy, Old Town, Overpass, Sandbar, Thrashball, Trenches), two presses = GRIDLOCK
  (MP_GridlockG3) -> Start (select) -> Start (start match). New input-script command `t trig L|R ms` (hid.cpp: a
  virtual controller's triggers). Marcus spawns at (3851, 6370, 443).
- **The two Gridlocks share coordinates**: Gears 1 actor median (736, 620, 592), Gears 3 (754, 1029, 594), similar
  spread: Gears 3's remake was built on the original layout, so Gears 1's level goes in at offset 0.
- levelinject into MP_GridlockG3 (--template GothPillar_Short --materials MI_Locust_Shield --bsp --fix-starts): 148
  meshes on 2,034 actors (1,948 lit from Gears 1's light maps), the BSP, 108 materials, 249 textures; a 139 MB package.
  Template search now skips class-default components; MI_Locust_Shield is the package's one instance of the Phong
  master with Diffuse_01 / Normal_01 / Specular_01.
- **2 fps, found with a profiler and fixed: 42 fps.** New runtime/ue3/profiler.cpp, GOW3_PROFILE=1 (guest threads) or
  =all (every thread): samples instruction pointers every ms, logs each busy thread's top functions every 10 s
  (gow3.pdb names; the target thread is resumed before anything allocates). The game thread was 100% in guest code
  (sub_827C50B0, a recursive walk, 82341040, 827D05D0...), the GPU thread half idle. Cause: **the dominant
  directional light** (the sun). UE3 lights a primitive dynamically with every light whose GUID its light map does
  not list; the new light maps listed none, so all ~2,100 new components were dynamic shadow casters for the sun.
  The new light maps now list every light's LightGuid and LightmapGuid (lighting.gears3_vertex_lightmap
  light_guids). The DominantDirectionalLightComponent's data starts with its precomputed shadow depth map (u16
  count + data) and 8 more bytes before the properties (25,092 bytes in).
- **The pawn never spawned** (wave 1 failed at once, no Pawn in GOW3_LOG_POS) - two causes:
  - converted meshes kept the **template's BodySetup** (the pillar's simple collision box), which UE3 uses instead of
    the triangles: every converted mesh collided as a pillar box at its origin (it also explains odd deflections in
    the flashback walks). meshconv now writes no body setup: collision is the kDOP tree. With --no-collision the pawn
    spawned, which pointed there.
  - Gears 1's floors are a little higher than Gears 3's (368 against about 355 at the first spawn), so a spawn
    capsule started inside the new floor. --fix-starts lifts every PlayerStart/EnemyStart (Gears 3 actors: properties
    at byte 26, after a state frame) to 130 above the highest Gears 1 BSP floor under it, when it is lower (50 of 51).
- Result (shots_g1_gridlock_in_g3.png): Horde wave 1 on Gridlock with Gears 1's buildings standing where Gears 3's
  remade ones are, Gears 1 facades lit by Gears 1's light maps, its trims and BSP streets; about 30 fps.
- Next: remove Gears 3's own geometry (its collection actors' components and BSP) so the level is Gears 1's alone;
  floors from meshes too in --fix-starts; navigation (Horde's enemies walk Gears 3's navmesh, which no longer matches
  everywhere).
- Seen once: a crash at boot after Startup.xxx loads (sub_822BB110 < 822B8228 < 8295B360 < 822EF0E0, guest address
  4B847104); the next run with the same build was fine.

## 2026-10-12: Gears 1's Gridlock alone in Gears 3's slot; its point lights baked
- **--replace** (levelinject): every StaticMeshCollectionActor of the target keeps only the new components (MP_GridlockG3:
  18 collection actors, 1,704 components detached; its 3 loose StaticMeshActors, BSP, volumes, cover, pickups,
  destructibles, InterpActors and decals stay). Horde runs on Gears 1's Gridlock with Gears 3's gameplay: spawn,
  34-60 fps (shots_g1_gridlock_replace.png). (Correction: the countdown did not run then; it waits for the
  command centre flag to be built, see 'A whole Horde wave' below.)
- **Gears 1 lights its levels mostly at run time**: on MP_Gridlock the 3 DirectionalLights and 2 SkyLights are baked into
  light maps (UseDirectLightMap), but all 368 PointLights are dynamic, and there are no shadow maps at all. BSP nodes
  of element light map type 0 (628) were lit by those lamps only; every light map holds only the sun/sky part. So the
  converted lighting was missing every lamp.
  - lighting.g1_point_lights: position (the light component's CachedParentToWorld row 3), Radius (default 1,024),
    LightColor (FColor bytes B G R A in properties) x Brightness. bake_point_lights: colour x (1 - d^2/r^2)^2 x N.L per
    vertex (world-space positions and normals: mesh vertices through scale and FRotationMatrix; BSP from the chunk's
    TangentZ), unshadowed (as Gears 1 drew them). Added to every coefficient of the converted samples; components and
    nodes with no light map get it over a 0.05 ambient. All 2,034 components lit (1,948 before).
  - range: samples clipped at 2.0 and the scale vector set to the 99th percentile (8 bits per channel under one scale
    per component: a vertex beside a lamp left the rest near black).
- ModelComponent details confirmed: an element's node list holds Model node indices (also listed, sorted, in the
  component's own node array after the elements, preceded by its ComponentIndex).
- Look: Gears 1's buildings, truck, walls and BSP streets lit by Gears 1's sun/sky light maps and lamps, under Gears 3's
  night sky, fog and post-processing (those come from MP_GridlockG3's own sky, fog and post-process settings).
- Open: sky/fog/post-process (Gears 1's daylight Gridlock against Gears 3's night), the light maps' directional
  semantics (calibrated by eye), navigation for Horde's enemies, cover (Gears 3's CoverLinks match Gears 3's walls).

## 2026-10-12: Gears 1's cover in Gears 3 (--cover)
- **Gears 3 CoverSlot is native** (tools/cover.py, verified by parsing all 225 links of MP_GridlockG3 to the last byte):
  SlotValidAfterTime f; ForceCoverType, CoverType, LocationDescription FNames; LocationOffset 3f; RotationOffset 3i;
  Actions (CA_* FNames); FireLinks {Interactions bytes, packed int, 2 flag bytes}; ExposedCoverPackedProperties;
  TurnTargetPackedProperties; SlipRefs {24-byte poly, BasedPosition as tagged properties, Direction}; OverlapClaimsList
  {link, slot}; then 21 flag bytes (cover.FLAGS order). Gears 1 slots are tagged structs (cover.read_g1_links;
  defaults: bEnabled and every bAllow* on). The remake's MP_GridlockG3 holds the same cover layout as Gears 1 (equal link
  rotations and slot patterns), moved around: 54% of the links pair within 100 units.
- **Player cover goes through the navigation mesh.** Experiments (tests/horde_cover.txt, GOW3_LOG_POS now logs
  GearPawn.CurrentLink): stock cover works in the port; stock slots re-encoded by cover.write_slot_828 with all AI data
  dropped (fire links, exposure, slips, overlaps) still work; the same stock data shuffled onto other link actors
  does not. The binding: Level native data after NavListStart/End, CoverListStart/End and PylonListStart/End:
  CoverLinkRefs (count + every CoverLink), then CoverIndexPairs (count + 5-byte {int link ref index, byte slot}),
  which the navmesh's polygons index (MP_GridlockG3: 225 links, 1,010 pairs).
- levelinject --cover: Gears 1 links assigned to the target's links by optimal matching of slot positions (Hungarian,
  scipy), Location/Rotation/Slots written (tags inserted when the target link had them at default), spare links
  emptied; every CoverIndexPair re-aimed at the Gears 1 slot nearest its old slot. MP_Gridlock: 198 links (635 slots)
  in, 1,010 references re-aimed (median 37 units, 74 beyond 200), 587 of 635 slots referenced.
- Result: Marcus takes Gears 3 cover on Gears 1's walls and slides along them (shots_g1_cover.png;
  --starts-at-cover, a test option, puts every player start 80 units behind a different slot facing it;
  tests/horde_cover_spawn.txt presses A at spawn, then A/D).
- Open: slots no reference reaches (48 here; a level with no Gears 3 counterpart has no navmesh at all: the navmesh
  must be generated for converted campaign levels, which then also holds cover); AI fire links/exposure (left empty).

## 2026-10-12: Gears 1's sky and look (--environment, unlit materials)
- The sky was missing both ways: --replace detached Gears 3's sky sphere (GEN_Procedural_Sky SM_SkySphere in a
  collection actor; its M_Gridlock_Night_Sky is unlit and procedural: only colour parameters survive cooking), and
  Gears 1's skydome (SkyDomes.NewSkyBox_DR_SMesh, x300, inside the seek-free map) was converted with the lit template
  and lit by nothing: black. Gears 1's sky material is EmissiveColor <- one TextureSample (CP_Sky3_Tex, DXT1 2048).
- **Unlit materials** (materialconv.g1_unlit: Gears 1 LightingModel is an int, MLM_Unlit = 2 (Phong 0, NonDirectional
  1, SHPRT 3, Custom 4); or only an emissive input): converted with the emissive texture as the template's diffuse and
  no specular; their components get a constant light map (UNLIT_LIGHT 1.0: diffuse x 1). No Gears 3 MIC on an unlit
  parent with a texture parameter exists in MP_GridlockG3, and a cloned Material would need its own shaders, so this
  keeps the one lit template. On MP_Gridlock only the skydome qualifies.
- **--environment**: the target's PostProcessVolume Settings take Gears 1's WorldInfo.DefaultPostProcessSettings
  (Bloom_Scale 0.2, DOF, Scene_Desaturation 0.4, HighLights 0.8, MidTones 1.4, Shadows) with Gears 3's night colour
  grading table (ColorGrading_LookupTable, bOverride_Scene_ColorGradingLUT) and Bloom_Tint dropped; the
  ExponentialHeightFog keeps Gears 3's density and height but takes Gears 1's HeightFog colour (BGRA cdd6d8) and
  brightness 0.6. (Gears 1's HeightFog is a uniform layer below z 3,701, 2.5e-5 per unit: not reproduced exactly.)
- Result: shots_g1_environment.png: Gears 1's cloudy blue sky over the street, no night grading. Open: the dominant
  directional light (Gears 3's moon: characters' light and shadow direction; its baked shadow depth map), fog density.
- meshconv: source UVs clamped to the half-float range (one Gears 1 mesh has 1e13 light map UVs).

## 2026-10-12: A whole Horde wave on Gears 1's Gridlock; UnrealScript disassembler
- **tools/uscript.py**: UnrealScript bytecode of cooked Gears 3 packages as pseudo-code (every UE3 token; natives
  named from Core/Engine/the package). Function export: +12 Next, +16 SuperStruct, +20 Children, +24 script size in
  memory, +28 on disk, script, then iNative u16, OperPrecedence, FunctionFlags. Object references are 8 bytes in
  memory and 4 on disk (jump targets are memory offsets). Read GearGameHorde_Base's StartNextWave, MatchInProgress
  (BeginState, Timer, ShouldSpawnEnemies) and GearHordeObjective_BuildFlag without a decoding error.
- **runtime/ue3/prop_log.cpp**: GOW3_LOG_PROPS="Class:Prop,Prop;Class2:Prop" logs those properties and the
  UnrealScript state (a State object in the StateFrame, UObject +20) of every live instance every 2 s;
  GOW3_WATCH_PROP=Class:Prop arms a write watchpoint on that property of the first instance. reflection BoolMask
  returned UBoolProperty's +104 for any property type; now 0 unless the property is a BoolProperty.
- **Horde's countdown "froze" at 0:30 on the stock map too: not a bug.** GearGameHorde (MatchInProgress) spawns
  GearHordeObjective_BuildFlag on round 0 when the map has flag markers; its Timer adds 1 s to WaveSpawningStartTime
  every second until the team builds the command centre flag (the watchpoint: writes from the script VM,
  sub_822D1868/sub_822D2130, +1.0 each second). MP_GridlockG3 has 5 flag markers (GearFortificationMarker,
  FortificationType 21, FlagSpawnProtectionRadius 2400), e.g. (-1036, -861, 596).
- **levelinject --starts-at=x,y,z,yaw** (test): every player start there (properties inserted when absent; shared
  set_property helper). tests/horde_build_flag.txt: spawn at (-1150, -861, 672) facing the flag marker, hold Y and X:
  flag built, countdown runs, wave 1 spawns (4 Drones, 3 Tickers) and the idle Marcus is killed: wave failed,
  restart menu (shots_g1_horde_wave.png). (A start at (1442, -2234, 470) failed to spawn the pawn: wave failed at
  once.)
- **Enemies on Gears 1's geometry walk Gears 3's navmesh** (the remake keeps the layout): Drones travelled
  3,600-5,200 units, Tickers reached the player; one Drone stayed within 400 units (firing position or blocked).
  Campaign levels have no such navmesh: generating one stays the next structural step.
- Recurring early boot crash (sub_822BB110 reading guest 4B847084, from sub_822B8228 < 8295B360 < 822EF0E0) seen
  again once this session; a rerun passes.

## 2026-10-12: Gears 3's own navmesh generator, run in game (runtime/ue3/navmesh_build.cpp)
- The editor's path builder is compiled out, but its APylon passes survive as orphaned functions (no caller, no
  vtable or data reference; found by their status strings at 8215C954..8215CA40 and their shape: one argument, the
  pylon, fields +648 NavMeshPtr / +652 ObstacleMesh / +660 open list / +664 path objects / +668 seed list):
    825BDB60 "Exploring": new path and obstacle meshes, path objects collected, then sub_825ABC70 = the flood fill
             (sub_825ABB30 seed polygon at the pylon's Location; per open polygon sub_825AB488 tries 8 directions
             through sub_825AA7C8, which ground-traces and fits the scout); poly limit 65,535
    825BDEC8 "Exploring seeds" (the seed list), 825BE2C8 "Simplifying mesh", 825BE398 "Splitting mesh for imported
    meshes", 825BE400 "Splitting mesh around path objects", 825BE468 "Fixing up mesh" (mesh vfunc +288),
    825BE4D8 obstacle mesh from the path mesh (sub_825B9D50) and its fix-up.
  UNavigationMeshBase fields seen changing: +0x3C/+0x40 Verts (stock MP_GridlockG3: 3,682), +0x68/+0x6C edges
  (2,283), +0x78 polys (894), +0x84/+0x8C the build's polygon list, +0x9C/+0xA0 final polys, +500 poly octree.
- Making them run in game (GOW3_NAVMESH_BUILD=1 or a hex list of passes, on the game thread from hid's per-frame
  hook, every Pylon, once its world is GWorld + 10 s):
  - GIsGame (83420554, set by sub_8299F7B0, read in 176 places) is cleared during each pass: in game the
    border-polygon test (sub_825D98F8) answers no, and the flood fill stops at the seed polygon.
  - the Scout class's default object (sub_825F8E30, cached at 83451BA8) and the scout (sub_825F8AB8) are fetched
    first with GIsGame set: their lookup by class name fails with it cleared (a null read at +0x480).
  - wait for the pylon's world to be GWorld: a converted level (142 MB) streams in while GWorld is still
    GearStartTransition's world, and traces into a world that is not active hit nothing.
  - the trace in question is UWorld::SingleLineCheck (sub_8256F2A0: world, hit, source actor, end, start, flags,
    extent; returns 1 for no hit); the seed's ground trace reaches 119 units below the pylon: a Pylon must stand
    within ~100 units above the floor (levelinject --pylon-at=x,y,z).
- Stock MP_GridlockG3 (pylon as shipped): exploring 18 s -> 20,148 vertices / 10,984 polygons; simplifying 135 s
  -> 625 polygons; fix-up -> 1,306 vertices, 625 polygons (shipped: 3,682 / 894); obstacle mesh 847 entries
  (shipped 807). No crash. Edges (+0x6C) stay 0: the edge-building step is not among these passes (no status
  string; not found yet).
- Converted Gears 1 Gridlock (--pylon-at=-1104,-861,642), all seven passes, no crash: exploring 59 s -> 21,067
  vertices / 11,496 polygons; seeds 1.3 s; simplifying 508 s -> 1,717 polygons; splits ~0; fix-up -> 3,550
  vertices, 1,717 polygons; obstacle mesh 2,123 entries. The first navmesh made from Gears 1 geometry, by Gears 3's
  own generator. One earlier identical run hung after exploration (game thread stuck in the post-flood-fill step;
  audio alive): intermittent, not yet explained.
- Not yet: edges (the step is still unidentified: 190 true orphans in 825A0000..8261FFFF, from the recompiled
  source's call comments plus .rdata/.data pointers), AI on the generated mesh, and keeping the result (10 minutes
  per load: the mesh must be saved into the package, e.g. through the engine's own Serialize with a memory writer).
- Address mapping trap: default.bin's .text bytes are not at file offset VA - 0x822a0000 + 0x29ee00 (a prologue is
  not there): call/caller scans over the raw image gave wrong answers; the recompiled source's "// bl 0x..."
  comments are the reliable call graph. (.rdata is at VA - 0x82000000; .data at VA - 0x82ff5c00 + 0xfe9800.)
- runtime/ue3/reflection.cpp: the one-time table search read committed regions without a guard; a region released
  during the scan crashed the process at start-up (seen with the profiler on). Each region scan now runs under SEH.
- Also: levelinject sets BlockZeroExtent/BlockRigidBody/CollideActors/BlockActors on converted components and
  UseSimpleLineCollision/UseSimpleBoxCollision off on converted meshes (they have no BodySetup), and kdop.py pads
  node bounds by 0.5 (flat floors had zero-thickness slabs). These were tried while the real cause (the inactive
  world) was unknown; kept as safer settings, their own effect not measured. GOW3_NAVMESH_PROBE=x,y,z[,range]
  traces through a point in every World object (validated on the stock map: a floor at z 369, a BlockingVolume).
- Instrumentation note: ue3::Read32 is a guarded read (a VirtualQuery each); a per-call wrapper doing six of them made
  exploration 130x slower (profiler: 99% ZwQueryVirtualMemory). Wrappers read only when logging.

## 2026-10-12: The cooked navmesh format; edges generated offline; generated navmeshes into packages
- **Edges are not made by the surviving passes** (none of them reaches an edge constructor). With the generated mesh
  and no edges, Horde's Drones jumped between spawn points (the game relocating enemies that cannot path), where on
  the shipped mesh they walked to the player. So edges, border segments and polygon cover are made offline.
- **UNavigationMeshBase serial format** (tools/navmesh.py; UNavigationMeshBase::Serialize sub_825B81B0, vtable
  8215E7F8 slot 10; read + write round-trips both shipped MP_GridlockG3 meshes byte for byte):
    NavMeshVersionNum, VersionAtGenerationTime (43, 43)
    Verts: count, each FVector + PolyIndices (count + u16)                      mesh +0x3C, 40 bytes in memory
    EdgeStorageInfo: count, each {DataOffset u32, DataSize u16 (in-memory size, 136 for the base class), edge
      class FName (index, number)}                                              mesh +0x4C
    Polys: count, each PolyVerts (u16 list), PolyEdges (u16 list of edge indices), PolyCenter, PolyNormal, BoxBounds
      (6 floats + valid byte), PolyCover (count + 24 bytes: CoverLink object ref, zero GUID, slot index; every
      slot of the map on exactly one polygon), PolyHeight float              mesh +0x9C, 136 bytes in memory
    LocalToWorld, WorldToLocal (identity), border segments (count + {v0, v1, poly} u16: polygon sides not covered
      by a shared border), Bounds (FBox)
    edges in EdgeStorageInfo order, each by its class's Serialize (vtable slot 23; FNavMeshEdgeBase, vtable
      8215E940, sub_825BFAA8 + sub_825BFCD0): Vert0, Vert1, Poly0, Poly1 (u16), EdgeLength float (the entity size:
      34 / 60 / 90), EdgeCenter, byte 0, byte 1, EdgePerpDir (into Poly1). Other classes (BasicOneWay, CoverSlip,
      Mantle, OneWayBackRef, PathObject) add fields: not decoded (kept as bytes in a round trip).
  The obstacle mesh has the same layout (no edges, no segments).
- **Edge rule** (tools/navedges.py, measured on the shipped mesh): an edge joins two polygons (lower index first) on
  a stretch of their shared border where the XY distance to the obstacle mesh's walls is >= the size; sizes 90, 60,
  34; a stretch equal to a larger size's is not repeated; each edge gets two new vertices. Every shipped edge's
  endpoints have clearance >= its size. Regenerated from the shipped polygons: all 581 shipped base-edge polygon
  pairs found, the same size set for 520; 1,456 base edges (shipped 1,377 base + 906 special).
- **Runtime dump**: GOW3_NAVMESH_DUMP=<file> after the passes (GOW3_NAVMESH_BUILD=none dumps a level's meshes as
  loaded). Verified: the stock meshes dumped from memory equal the cooked file's vertices and polygons exactly.
- **tools/navinject.py**: package + dump -> both NavigationMeshBase exports rewritten (edges, segments, polygon cover
  from the package's own CoverLinks), optional --starts-at for tests.
- **Proof on the stock map**: MP_GridlockG3 with its navmesh rebuilt from the dumped polygons + generated edges /
  segments / cover: Horde wave 1 enemies walk to the player (Drone 7,263 units, every Ticker reached him), no
  relocation jumps; the wave spawned out (WavePointsRemaining 0).
- **Gears 1's Gridlock with its own navmesh**: generated in game from Gears 1 geometry (3,550 vertices, 1,717
  polygons; obstacle mesh 4,544 / 2,145), dumped (research/navmesh/g1_gridlock.navm), completed offline (2,717
  edges, 2,166 border segments, 586 of 635 Gears 1 cover slots on polygons) and written into the converted package:
  no runtime build at load. Horde wave 1: Tickers reach the player, Drones cross 5,500-6,300 units to firing
  positions and shoot him from across Gears 1's street (shots_g1_navmesh_wave.png); the wave spawns out.
  Pipeline (MP_GridlockG3 from Gears 1's MP_Gridlock):
    1. levelinject.py ... --bsp --fix-starts --replace --cover --environment --pylon-at=-1104,-861,642
    2. game with GOW3_NAVMESH_BUILD=825BDB60,825BDEC8,825BE2C8,825BE398,825BE400,825BE468,825BE4D8 and
       GOW3_NAVMESH_DUMP=<file> (about 11 minutes: exploring 60 s, simplifying 490 s)
    3. navinject.py <package from 1> <dump> <package out>
  Open: special edges (mantle, cover slip, drop-down, one-way: Gears 1's vaults and slips), the generator's
  intermittent hang after exploration, one pylon per level (campaign levels need several: cross-pylon edges).

## 2026-10-12: Mod support for creators (milestone 6, first part)
- **Several mods at once** (runtime/kernel/mods.cpp, mods.h; used by gow2_files.cpp ResolveGuestPath): every folder
  in mods\ with a mod.ini ([mod] name, version, author, description, priority, enabled) is a mod; files directly in
  mods\ form "(loose files)" (priority 0, the converters' old output). Load order: priority, then name; the last
  wins a conflict. The log lists each mod, every conflict and every override.
- **Merged TOC**: the game finds packages through GearGame\Xbox360TOC*.txt; the runtime serves, for each, the
  disc's TOC with each winning mod file's size and MD5 (BCrypt) replaced, and new files added to the main TOC only
  (the language TOCs list localized files). Written to %LOCALAPPDATA%\GoW3Recomp\mods_toc. A TOC inside a mod is
  ignored; modpack.update_toc no longer writes one into a folder with a mod.ini.
- **tools/trilogy.py** (one command for creators): `mods` (the runtime's view: order, files, conflicts), `new-mod`,
  `check` (manifest; every package read back, version 828, replaces/adds), `convert-map` (a Gears 1 map into a
  Gears 3 map's slot as a mod: levelinject with the standard options, then navinject with --navmesh).
- **docs/MODDING.md**: installing, mod.ini, load order, the tool, the navmesh procedure (GOW3_NAVMESH_LEVEL=<map>
  picks the map to build on), formats, debugging. README points at it.
- Verified in game: two mods (Gears 1 Gridlock at priority 0, the stock MP_GridlockG3 at -5): both listed, the
  conflict logged, Gears 1's package loaded, merged TOC, a Horde wave on it. `convert-map` end to end (2 minutes:
  conversion + navmesh) gives build\mods\Gears1_Gridlock, which loads (left installed with enabled = false).

## 2026-10-12: Every navmesh edge class decoded; mantle and cover-slip edges generated
- **Edge classes** (vtables: base 8215E940, BasicOneWay 8215EA48, DropDown 8215ECD8, OneWayBackRef 8215EB48,
  Mantle 8215E9C0, CoverSlip 8215EC50, PathObject 8215ED58; slot 3 = class name, 23 = Serialize, 24 = vertices).
  Classes other than the base and BasicOneWay serialize four vertices (+12, +14, +112, +114, sub_825C0010), then:
  OneWayBackRef (sub_825BFF78) two polygon references (FPolyReference: object ref, GUID, u32 id = 0xFFFF0000 |
  polygon: sub_825BE658) and a u16; DropDown (sub_825BEA18) + DropHeight float; Mantle and CoverSlip
  (sub_825BFDB8) + actor reference (object ref + GUID, sub_82430E38) + RelItem + object ref + MoveDir FVector + u32;
  PathObject (sub_825C3230) + actor reference + u32. tools/navmesh.py now reads all 2,283 edges of MP_GridlockG3
  (1,377 base, 447 back references, 337 cover slips, 73 mantles, 37 path objects, 12 one-way) to the last byte and
  writes them back identically. EdgeStorageInfo DataSize per class: 136 / 216 / 304 / 244.
- **Meaning** (measured on the shipped mesh): a mantle or cover slip is attached to a cover slot (CoverLink + slot
  index); its one vertex is the slot (both pairs), EdgeCenter the slot, EdgeLength -1, type byte 1 (mantle) / 2
  (slip), byte 255, references (pylon, source polygon) -> (pylon, destination polygon), MoveDir = the destination
  point: slot + forward 212 (mantle), + forward 76 / side -131 (slip left, u252 -1), + forward 75 / side +117 (slip
  right, u252 +1), z +23. It is listed in the source polygon; right after it comes its OneWayBackRef (same vertex,
  type 6, references reversed) listed in the destination polygon. All 447 back references pair with a mantle, slip
  or path-object edge this way.
- **Generation** (navedges.special_edges, from the package's CoverLinks: bCanMantle on mid-level cover,
  bCanCoverSlip_Left/Right): on MP_GridlockG3's own polygons it finds all 404 shipped mantle/slip edges (393 between
  the same polygons) plus 83 the editor had rejected by traces (a destination wall-clearance minimum of 40 drops the
  worst). Gears 1 Gridlock: 57 mantles and 317 cover slips (+ back references), 3,465 edges in all.
- In game: the wave runs normally on it; neither it nor the stock map showed an enemy mantle or slip in wave 1
  (GearPawn.SpecialMove sampled every 100 ms: GOW3_LOG_PROPS_MS=100 logs changes only; ESpecialMove 1 MidLvlJumpOver,
  5 CoverSlip, 6 MantleUpLowCover, 7 MantleDown), so their use is not shown yet.

## 2026-10-12: Navmeshes with several pylons (cross-pylon edges)
- Campaign levels split navigation into pylons (SP_Bridge_01_S: 18; SP_Mercy_Paths 72; 136 SP packages have
  pylons). tools/navmesh.py reads and rewrites all 36 of Bridge's meshes byte for byte once FNavMeshCrossPylonEdge
  (vtable 8215EAC8) is read with the OneWayBackRef layout (sub_825BFF78: four vertices, two polygon references,
  u16); EdgeStorageInfo size 216 (DropDown: 220).
- **Cross-pylon edges** (152 on Bridge): stored in their own pylon's mesh and listed in their own polygon; Vert0/1
  in their own mesh, the second vertex pair the same two points in the other mesh; references (own pylon, own
  polygon) and (other pylon, other polygon); type byte 0, byte 1, EdgeLength the size; the other mesh holds the
  mirror (146 of 152).
- **Generation** (navedges.cross_edges, navmesh.add_cross_edges): shared borders between polygons of two pylons
  (the single-mesh rule on the two vertex sets), per size where both pylons' walls leave room. Each pylon's
  obstacle mesh lines its own boundary with walls, also where it meets the next pylon: walls lying on any
  cross-pylon border are ignored. On Bridge's shipped polygons: 60 of the 61 shipped polygon pairs, 132 of 152
  edges identical (pylons, polygons, size); 140 generated.
- **navinject.py <in> <dump> <out> all**: every pylon in the dump (edges, special edges, cover), then cross-pylon
  edges, cover slots claimed by several pylons kept on one polygon (a containing one first). Checked offline on
  Bridge (18 pylons from its own polygons: 140 cross-pylon edges, 622 slots, all meshes read back) and Gridlock
  (single pylon: unchanged). trilogy convert-map uses it.
- **Campaign level in game**: the runtime's activation now works for streamed sublevels (a sublevel's pylons live in
  its own world, never GWorld): passes/dump start once GWorld is not GearStartTransition's, GOW3_NAVMESH_LEVEL picks
  a sublevel package (sp_nightmare_02_s), GOW3_NAVMESH_DELAY the wait, and the log lists the packages whose pylons
  are loaded. The Jacinto flashback (tests/continue_walk.txt) streams sp_nightmare_01_s (1 pylon) and
  sp_nightmare_02_s (2 pylons, 51 cover links). SP_Nightmare_02_S rebuilt from its dumped polygons with generated
  edges (Pylon_4: 68 mantles, 64 slips; shipped 78, 63), cover (247 slots; shipped 258) and border segments, as a mod:
  the flashback plays, Marcus' squad takes cover (68 of 70 samples with a squadmate in cover, the same CoverLinks as
  unmodified), no crash. Its two pylons do not touch (no cross-pylon edges shipped or generated): cross-pylon edges
  are still checked offline only.

## 2026-10-12: First Gears 1 campaign level: the Prison (SP_Prison), several sublevels at once
- Gears 1 campaign levels are a persistent package (SP_<Name>_P) and streamed sublevels in shared world coordinates
  (geometry SNN, _Meshes, _World, BSP; _Scripting with Kismet and cover; _Audio; cinematics). SP_Prison
  ("14 Years After"): ~5,000 mesh actors, ~4,000 BSP polygons, 385 cover links, ~960 lights, Kismet in every package.
  The player start: SP_Prison_P PlayerStart_0 (150.5, 686.6, 192.1), yaw 16404.
- **levelinject takes several sources** (comma-separated): actors, meshes and materials keyed by (source, export);
  meshes, materials and textures reused by name across sublevels; BSP of each source; every source's lamps light all
  the geometry; cover from every source; the environment from the first source with a HeightFog. Single-source
  output unchanged (Gridlock: the same 142,484,080 bytes).
- **--replace disarms the target's kill volumes** (MP_GridlockG3: 34 PhysicsVolumes, bPainCausing, 100,000 damage a
  second): they belong to the removed layout.
- Prison sections P, S01, S02 (+ _Meshes, _Scripting) into the Gridlock slot, lifted 20,000 (clear of Gridlock's
  BSP and blocking volumes; at its own height the start overlaps them and the pawn never spawns): 1,536 actors, 200
  meshes, 189 materials, 258 textures, 1,870 BSP polygons, 120 cover links, 125 MB. The pawn spawns in the cell and
  stands on Gears 1's floor (collision works), then dies after 6 s:
- **Gears 3 kills players outside the play area**: GearPawn.CheckInPlayArea (script, every 1.5-2.5 s) calls the
  native IsInPlayArea; out longer than MaxOutOfPlayAreaTime -> GearGame.HandlePlayerOutOfPlayArea -> FellOutOfWorld
  (exempt: SpecialMove 1, 6, 7, 81 - mantles). The play area is the navmesh's: a converted level needs its own
  navmesh before anyone can stand in it. --pylon-at now also moves the pylon's ExpansionSphereCenter (MP_GridlockG3
  has bUseExpansionSphereOverride, centred on its own map) and sets ExpansionRadius (--pylon-radius).
- **The prison's navmesh** (the pylon in the cell, radius 9,000): generated in game in 20 s (593 polygons, 1,231
  vertices; obstacle mesh 713 polygons), completed by navinject (889 edges: 12 mantles, 43 cover slips; 727 border
  segments; 83 of 284 cover slots on it) and written into the prison mod. Marcus then lives past the 6 s: he walks
  out of the cell up a corridor of Gears 1's prison (shots_g1_prison.png), until he steps onto props the navmesh does
  not cover (the play-area check kills him after 23 s; those spots are off every polygon). Gears 3 maps keep players
  off such props with blocking volumes; a converted level needs them (or the props' collision tuned).
- **GOW3_NO_PLAY_AREA_KILL=1** (runtime/ue3/play_area.cpp): MaxOutOfPlayAreaTime (GearPawn +5380) set to 1e9 on every
  GearPawn each second: free roam in a converted level (Marcus explored the prison for the whole 250 s run).
- (Superseded:) the navmesh step was first stopped by the host's memory. The package with the pylon placed in the cell (--pylon-at=150,687,20180
  --pylon-radius 9000) is research/navmesh/MP_GridlockG3_prison.xxx; the in-game generation run (GOW3_NAVMESH_LEVEL=
  MP_GridlockG3, dump to research/navmesh/g1_prison_s01s02.navm) was stopped by the host running low on memory before
  the level loaded. Then: navinject.py with that dump, and the walk test.

## 2026-10-12: Adding actors to a Gears 3 level (new CoverLinks)
- Campaign levels have more cover than the host map has links (the Prison: 385; MP_GridlockG3: 225). cover.add_links
  clones a CoverLink and its CylinderComponent per new link and wires it in:
  - exports: the template's class, outer (the level), flags, archetype; NetIndex -1 (an actor's prelude is 26 bytes
    before its properties, NetIndex its last int: 0x2C2, 0x2C3... per link; a component's at +4); the clone's
    CylinderComponent/CollisionComponent point at its own component.
  - Level native data: Actors (count + refs after the properties) grows; the cover list (NextCoverLink) and the
    navigation point list (nextNavigationPoint) end at the new links (their ends: Level refs just before
    CoverLinkRefs, in the order NavListStart, NavListEnd, CoverListStart, CoverListEnd, PylonListStart,
    PylonListEnd, an empty array); CoverLinkRefs grows (CoverIndexPairs unchanged: polygons reference cover directly).
  - upkwrite.Rewriter.current(index): an export's data as it will be written (the last replacement).
- levelinject --cover: source links beyond the target's become new links (location, rotation, slots). Offline check
  (the Prison's five scripting sublevels into MP_GridlockG3): 160 links added, 385 links parse, all in Level.Actors
  and CoverLinkRefs, the cover list walks 385 links and the navigation list 459 points to the last new link.
  Not yet loaded in game.

## 2026-10-13: Gears 1's opening cutscene plays in Gears 3 (camera actors; director in campaign mode)
- The opening (SP_Prison_Cine_Start, InterpData_0, 57.5 s): groups marcus / dom (AnimControl, FaceFX, Move,
  Sound; Baked_AnimSet_0), Wretch02 / 04, ArmorBag, shadowMarcus, Jack, weld, the cell door and its slat, three
  spotlights (FloatProp), two door material parameters, Camera01-03, and a director (Director, Fade, Events, four
  Sound tracks). SeqAct_Interp_0 links the groups to SkeletalMeshActors, InterpActors, CameraActors,
  SpotLightToggleables and MaterialInstanceActors.
- **CameraActor** is copied now (camera 0 hard-attached to cinematic Marcus's b_MF_Head: it follows his animation).
  Editor-only components are not copied (EDITOR_COMPONENTS: frustum / light-cone / light-radius drawers, arrows,
  sprites; a camera's StaticMeshComponent, which Gears 3's class default has no template for).
- World-frame Matinee move keys (MoveFrame IMF_World) are moved by the conversion offset (Copier.shift_points); the
  opening's tracks are all relative-to-initial, so this one was not affected.
- In the Horde slot the Matinee runs to its end but no InterpGroupInstDirector is created for the player (the view
  stays on the player); **in the campaign** (Gears1_Prison_Campaign) one is, and **the director cuts between Gears
  1's cameras**: a tilted shot through the cell bars, the cell from inside with Marcus at the door, other fixed
  angles (build\shots_g1_cutscene_160/170/180/200.png), timed by Gears 1's cut track while the Matinee plays
  (positions 0-57.46 s at 150-215 s of the run).
- Seen: Gears 3's player Marcus and the prologue's "Walk" tutorial stay on screen during the cutscene; the
  cutscene's lights (SpotLightToggleable) and material parameters are not converted; FaceFX not converted.
- **Clean cutscene** (build\shots_g1_cutscene_clean_170/190/240.png): no HUD, tutorial or gameplay Marcus.
  - Cinematic mode needs the player's controller: Gears 3's cutscenes link GearSeqVar_Player ("All Players");
    Gears 3's SeqVar_Marcus is a SeqVar_Character (PawnClass: Marcus's pawn). The toggle fired (ActivateCount 1) and
    hid nothing. objcopy.Copier.player_vars: a SeqVar_Marcus linked only by player-control ops
    (PLAYER_CONTROL_OPS: ToggleCinematicMode, ToggleHUD, ToggleInput; or ToggleHidden beside a cinematic mode)
    becomes GearSeqVar_Player.
  - Gears 3's own prologue scripting in the slot: tools/stripkismet.py. `SP_Nightmare_02_S --keep=G1_` empties
    its top-level Kismet except the converted G1_ subsequences (18 objects off); `SP_Nightmare_01_S
    --drop-class=SeqAct_ManageTutorials` cuts the 2 links into the tutorial ops ("Walk" prompt) and keeps the
    rest. **Do not strip SP_Nightmare_01_Cine / _01_S whole**: SP_Nightmare_P streams _02_S in on their remote
    events (CinematicFinishedIntro...), and without them Gears 1's opening never started. Run stripkismet
    again after every convert-map (the conversion rewrites _02_S from the disc's).
  - Light shafts / fog sheets: Gears 1's volumetrics are BlendMode 2 (translucent) unlit two-sided materials
    (3 additive, 4 modulate); converted through the one opaque MIC template they drew as solid white cones.
    materialconv.g1_blend / g1_see_through: static mesh actors whose every element is see-through are left out
    (prison campaign: 83 actors, 11 meshes) until translucent materials get a Gears 3 template.

## 2026-10-13: Gears 1's opening playable end to end in Gears 3's campaign (cutscene -> Dom -> player control)
New campaign (Gears1_Prison_Campaign enabled; tests/g1_after_cine.txt): Gears 1's opening cutscene starts at once
(~125 s into the run), Dom spawns, says "Let's go" and leads out (to (-562,1149)), the view returns to the player and
Marcus walks out of the cell into the corridor (y 1321, x 17..483; build\shots_g1_playable_corridor_311.png).
What it took:
- **Dom's variable**: Gears 3's AI factory never writes Gears 1's per-set output ("Set 1 [1]"). The link rebuild
  also puts those variables on Gears 3's **"Last Spawned"** (a SeqVar_Object link; "Spawned" expects a
  SeqVar_ObjectList and left it empty). Dom's SeqAct_Speak ("Let's go") waited for its speaker before; now it
  finishes and SeqAct_AIMove runs.
- **View after the cutscene**: the director's InterpTrackInstDirector.OldViewTarget stayed null, so when the
  Matinee ended (bIsPlaying 0, GroupInst freed) the view stayed on its last camera.
  objcopy.restore_view_after_cutscenes adds a SeqAct_SetCameraTarget on GearSeqVar_Player (no camera target: the
  player's own view) to the "Completed" output of every copied Matinee with a director group (3 in the prison).
- **SeqAct_ChangeCollision**: Gears 1 stores bCollideActors / bBlockActors, Gears 3 applies CollisionType only
  (default COLLIDE_CustomDefault): objcopy.change_collision sets COLLIDE_BlockAll / TouchAll / NoCollision.
  Actors a script switches collision on for are copied with collision off until then
  (objcopy.collision_enabled_by_script / collision_off; the cell door).
- **Gears 3's prologue under it** (tools/stripkismet.py edits, after every convert-map):
  - SP_Nightmare_01 (Gears 3's dream cell: walls, BSP, 24 blocking volumes) still loaded under the converted
    prison and stopped Marcus at the cell door (StaticMeshActor_82 Prison_Cell_Walls at (129,764)): the mod ships
    the empty sublevel SP_Nightmare_02_W as SP_Nightmare_01.xxx (the streaming still finds a level of that name).
  - Gears 3's intro cutscene (SP_Nightmare_01_Cine "00_Nightmare Opening Cine", its remake of this opening) plays
    on the player's pawn and left him at (85,561), outside the cell: `--bypass=Name.SeqAct_Interp_1:Completed+EndCIne`
    reroutes the links into it to its Completed and EndCIne (event track) targets; EndCIne's chain (cinematic mode
    off -> DONE -> unhideDoor -> CinematicFinishedIntro) still streams the prison in.
  - The "start black" cinematic mode (SP_Nightmare_P SeqAct_ToggleCinematicMode_5) and SP_Nightmare_01_S's
    ToggleCinematicMode_0/1/3 lock movement and only the stripped _02_S script released them: `--set=...:
    bDisableMovement=0` (and turning / input for _5).
  - The campaign PlayerStart (SP_Nightmare_P PlayerStart_0, (160,592)) -> Gears 1's start: `--move=PlayerStart_0:
    150.6,686.6,192.1,16404`.
  - Full recipe:
    ```
    python -P tools/stripkismet.py <mod> <g3 cooked> SP_Nightmare_02_S --keep=G1_
    python -P tools/stripkismet.py <mod> <g3 cooked> SP_Nightmare_01_S --drop-class=SeqAct_ManageTutorials
    python -P tools/stripkismet.py <mod> <g3 cooked> SP_Nightmare_01_S --set=SeqAct_ToggleCinematicMode_0:bDisableMovement=0,SeqAct_ToggleCinematicMode_1:bDisableMovement=0,SeqAct_ToggleCinematicMode_3:bDisableMovement=0
    python -P tools/stripkismet.py <mod> <g3 cooked> SP_Nightmare_P --set=SeqAct_ToggleCinematicMode_5:bDisableMovement=0,SeqAct_ToggleCinematicMode_5:bDisableTurning=0,SeqAct_ToggleCinematicMode_5:bDisableInput=0 --move=PlayerStart_0:150.6,686.6,192.1,16404
    python -P tools/stripkismet.py <mod> <g3 cooked> SP_Nightmare_01_Cine --bypass=Name.SeqAct_Interp_1:Completed+EndCIne
    cp <g3 cooked>/SP_Nightmare_02_W.xxx <mod>/GearGame/CookedXbox360/SP_Nightmare_01.xxx
    ```
    (stripkismet edits the mod's copy when there is one: run each once on a fresh package, or delete the mod's copy
    first; only _02_S is rewritten by the conversion.)
- Testing pitfall: tests/new_campaign2.txt's last menu "A" presses (82-94 s) can land in the level once it loads
  quickly (A = roadie run / take cover); g1_after_cine.txt presses B before walking.
- Seen / open: without SP_Nightmare_01's lights the converted prison is very dark, and the prologue's sepia
  post-processing still applies; cutscene lights (SpotLightToggleable) and FaceFX not converted; a black triangle
  near Marcus's hands after the cutscene (an unconverted mesh/material); the hidden player is still pushed by
  moving actors during cutscenes in general.


## 2026-10-13: Gears 1's opening inside Gears 3's campaign (build\mods\Gears1_Prison_Campaign)
- Gears 3's prologue (the Jacinto flashback) is a remake of Gears 1's prison opening at the same coordinates:
  SP_Nightmare_P's PlayerStart (168, 592, 192) yaw 16384, Gears 1's (150, 687, 192) yaw 16404. The cell block, its
  audio and the opening cinematic go into **SP_Nightmare_02_S** (the streaming sublevel with mesh collections, 2
  pylons, 51 CoverLinks, unlit template components, a texture MIC: MI_GR_Cliffwall_Tilable_01; skeletal template
  MI_LOC_Boomer_Shader) with **offset 0**: 120 MB.
- Campaign content: for a target SP_<Map>_*, the AI pawn classes available are those of all the map's packages
  (SP_Nightmare_P: GearPawn_COGDomG2, COGBenjaminCarmine, COGRedShirt; _02_S: Boomer, FlameDrone, Hunters...);
  PAWN_MAP / PAWN_STANDIN entries are now candidate lists (Pawn_COGDom -> COGDom, else COGDomG2), so Gears 1's Dom
  is Gears 3's Dom here (no stand-in).
- Sublevel lists: SP_Nightmare_02_S stores no CoverLinkRefs (51 links, the table empty), so the list ends are found
  by their contents (cover.level_lists: CoverListStart/End two CoverLink refs, PylonListStart/End two Pylon refs);
  add_links, pylons and the cover pass use it; CoverIndexPairs are re-aimed only where they exist. levelinject skips
  skeletal meshes when the target has none to take as a template.
- **New campaign** (tests/new_campaign2.txt): Gears 3's prologue starts with Marcus (GearPawn_COGMarcus, campaign) in
  the cell at (168, 592); Gears 1's Kismet runs in the campaign game mode (the cinematic's LevelLoaded, "spawndom"
  -> time2Go, its SeqAct_AIFactory) and **spawns Gears 3's Dom at Gears 1's PathNode outside the cell (144, 941)**,
  standing at the open cell door while Gears 3's "Walk" tutorial shows (build\shots_g1_campaign_dom.png). 49-60
  fps. (Continue from a later checkpoint resumes in Gears 3's street battle, unaffected.)
- Note: the test overwrote the profile's campaign checkpoint (it is the scripted new campaign).
- **Pass-throughs**: a Gears 1 action with no Gears 3 class (objectives, tutorials, AISetTetherDist, CauseDamage,
  VinceTag...) is no longer dropped: it becomes a SeqAct_Log (screen output off) and the link rebuild routes all its
  inputs to In and all its outputs (Links merged) to Out, so chains through it keep running (dropped, the opening's
  AISetTetherDist stopped time2Go: Dom's speech, his walk to the cell and the cinematic-mode-off after it never
  ran). OBJCOPY_NO_PASSTHROUGH=1 drops them as before. Cell block in the campaign: 47 actions as pass-throughs,
  3 objects without a class left (music variables). In game the pass-throughs fire (AISetTetherDist, tutorials).
- SeqAct_AIMoveToActor -> GearGame's **SeqAct_AIMove** (Gears 3's AI moves through it; links: Finished -> Reached
  Goal, Destination -> Move To).
- **navmesh.write_dump / shift_dump** (exact round trip: a polygon's box-valid field keeps its 4 raw bytes): a
  generated navmesh reused for the same level converted at another offset, its pylons renamed. The cell block's
  Horde-slot navmesh (z +20000) shifted by -20000 into the campaign package's new cell pylon (levelinject
  --pylons-at=150,940,150): 593 polygons, 889 edges, 83 cover slots. (Generating in the campaign directly picked
  the wrong sublevel's pylons first and hung: to look at.)
- **Open**: in the campaign the opening still stalls at Dom's walk: SeqAct_AIMove activates but Dom (GearAI_Dom as
  spawned by the factory) stays at his spawn point, so the cinematic mode Gears 1's opening turned on is never
  released and the player cannot move. Next: whether the cloned pylon's mesh is registered at run time (octree,
  pylon list), Dom's controller state (the prologue's squad scripting), the SeqVar_Marcus target.
  Ruled out since: the cloned pylon (the same navmesh in the sublevel's own Pylon_1, moved to the cell: Dom still
  stays) and the move action (Engine's AIMoveToActor walks the Horde slot's stand-in; GearGame's SeqAct_AIMove did
  not help here; the mapping was removed again). The player stays frozen in the cinematic mode Gears 1's opening
  turns on (time2Go's ToggleCinematicMode_2/_3), released only at the end of Dom's chain.
- Found since (prop_log on GearAI / GearPC): Dom's GearAI_Dom sits in Action_Idle with no command and no MoveTarget,
  and GearPC_SP goes to bCinematicMode 1. The move order's Target is **SeqVar_Dom_47**, while the factory wrote Dom
  into **SeqVar_Dom_48**: Gears 1's SeqVar_Dom is a lookup (every instance = the Dom pawn), as SeqVar_Object only
  the factory's own output holds him. Changes kept: SeqAct_AIMoveToActor -> GearGame's SeqAct_AIMove (back in; GearAI
  takes move orders from it), AI factories keep Gears 1's link layout (DYNAMIC_LINKS: per spawn set, like a
  sequence's). Tried and reverted (OBJCOPY_CHARACTER_VARS=1): SeqVar_Dom -> Engine's SeqVar_Character {PawnClass}
  (GearGameContent.GearPawn_COGDomG2, then GearGame.GearPawn_COGDomBase): the campaign hung (the main thread in
  sub_822F7F00's sleep loop, before Dom spawns) with either class. Next: a lookup that works (GearGame's
  SeqVar_Squad / a named variable written by the factory, or every SeqVar_Dom merged into the factory's output
  variable when the sequence is converted).
- Then: **every SeqVar_Dom of a package is merged into the one an AI factory writes** (Copier.factory_output_var;
  the others are not created and references to them resolve to it): time2Go's move order now targets the factory's
  SeqVar_Dom_48. In game Dom still idles (Action_Idle, no command): next, the runtime value of that variable
  (SeqVar_Object.ObjValue: does Gears 3's factory fill a Gears 1-laid "Set 1 [1]" link?) and the factory's
  VarLinkDescs / OutLinkDescs bookkeeping.
- Checked (prop_log SeqVar_Object:ObjValue): Dom spawns, yet the factory's **SeqVar_Dom_48 stays empty**: Gears 3's
  factory does not write the spawned pawn into the "Set 1 [1]" variable link as Gears 1 laid it out. Most likely it
  finds the per-set links by index after its own fixed variable links (Gears 3's class default has a different
  fixed set than Gears 1's "Spawn Points", "Last Spawned"). Next: lay the factory's VariableLinks out as Gears 3 does
  (its default fixed links, then one per spawn set), compared with a Gears 3 map's factory (SP_Airfield_01B_S).
- Gears 3's factory layout confirmed (SP_Airfield_01B_S): VariableLinks = the class default's Spawn Points, Last
  Spawned, Spawned, Num Dead, Player Slot, Spawn Destinations, then per set "Set 1 [1]".."Set 1 [4]", "Set 1 [Spawn
  Points]", ...; OutputLinks = All Spawned, All Dead, Spawns Disabled, Spawned, # Dead, Entered Combat, Died, then
  "Spawn 1 [1]".. . That is what the link rebuild gives, so the factory is no longer exempt (DYNAMIC_LINKS empty).
  Still, SeqVar_Dom_48 (on "Set 1 [1]") stays empty in game. Next session: connect the merged Dom variable to the
  factory's "Last Spawned" / "Spawned" links as well, and check what Gears 3's factory writes (SeqVar_Object ObjValue
  of a Gears 3 map's factory outputs) - or give Dom's move order the factory's "Last Spawned" variable.

## Conversion recipes (2026-10-13; G1 = ..\gow1-decomp\disc\WarGame\CookedXenon, G3 = ..\gow3-decomp\disc\GearGame\CookedXbox360)
- Cell block in the Gridlock slot (build\mods\Gears1_Prison_Test): levelinject G3\MP_GridlockG3.xxx with sources
  SP_Prison_P, S01, S01_Scripting, S02, S02_Meshes, S02_Scripting (+ SP_Prison_Audio, S01_Audio, S02_Audio,
  SP_Prison_Cine_Start for sound and the opening): --template GothPillar_Short --materials MI_Locust_Shield --bsp
  --replace --cover --environment --kismet --offset 0,0,20000 --starts-at=150,687,20272,16404
  --pylon-at=150,687,20180 --pylon-radius 9000; then navinject with research
avmesh\g1_prison_s01s02.navm (all).
- Yard in the Gridlock slot (build\mods\Gears1_Prison_Yard): sources SP_Prison_S03, S04, S04_Scripting, S04_Audio,
  S02_Meshes; the same options with --starts-at=-3240,2460,20860,16384 --pylon-at=-3170,4328,20800
  --pylons-at="-3208,2600,20800;-1500,2600,20160;-1600,6000,20160;-3300,6300,20800"; navinject
  research
avmesh\g1_prison_yard5.navm (all).
- Cell block in the campaign (build\mods\Gears1_Prison_Campaign): target G3\SP_Nightmare_02_S.xxx, the cell block
  sources with audio and the opening cinematic, --materials MI_GR_Cliffwall_Tilable_01 --bsp --replace --cover
  --environment --kismet --offset 0,0,0 --pylon-at=150,940,150 --pylon-radius 6000; navinject
  research
avmesh\g1_prison_campaign_p1.navm (the cell block's dump shifted by -20000 in z, navmesh.shift_dump).
  Test: tests/new_campaign2.txt / walk_prologue.txt (a new campaign: overwrites the profile's checkpoint).

## 2026-10-13: mod support: enable / disable / pack / install
- tools/trilogy.py: **enable / disable <mod>...** (the mod's mod.ini `enabled` line rewritten), **pack <mod>
  [--out x.zip]** (the mod's check first, then its folder as one zip: <folder>-<version>.zip next to mods\),
  **install <x.zip> [--replace]** (one folder with a mod.ini; refuses absolute paths, "..", drive letters,
  backslashes, and an installed mod of the same folder without --replace). Mods are found by folder or mod.ini name.
- Tested: the Gridlock sample packed (71 MB), installed into an empty mods folder and listed; a second install
  refused; a zip with "Evil/../../escape.txt" refused. docs/MODDING.md "Installing a mod" shows the commands.

## 2026-10-13: Gears 1 Kismet classes mapped to Gears 3 ones
- Survey of the Kismet classes the copier still dropped, over Gears 1's whole campaign (counts = uses), and Gears 3
  classes doing the same: CLASS_RENAME now maps SeqAct_WarCameraShake -> SeqAct_CameraShake (333),
  SeqAct_AIAcquireEnemy -> SeqAct_AISetTarget (177), SeqAct_WarUseCommLink -> SeqAct_GearUseCommLink (157),
  SeqAct_TempHUDFade -> SeqAct_CameraFade (132), SeqEvt_CheckpointActivated -> SeqEvent_CheckpointLoaded (130),
  SeqAct_WarCheckPoint -> SeqAct_Checkpoint (128), SeqAct_AISetFocus -> SeqAct_AILookAt (85),
  SeqAct_ToggleHammerOfDawn -> SeqAct_ToggleHOD (68), SeqAct_WarPlayerAnim -> SeqAct_GearPlayerAnim (47),
  SeqAct_SetMood -> SeqAct_AISetMood (45). Links come from the Gears 3 classes (rebuilt as before); properties Gears
  3 does not declare are ignored by its loader.
- Both test mods load with them and they run: the yard's AIAcquireEnemy (as AISetTarget) and checkpoint; the cell
  block's initialPlayerStuff now passes its checkpoint (the sequence used to stop there). Dropped in the yard: 29 ->
  13 objects; cell block: 77 -> 51.
- **Music**: Gears 1's SeqAct_SetMusicTrack assigns SeqVar_MusicTrack tracks ({TrackType, TheSoundCue}) to its music
  manager, which plays them by combat mood; it becomes Gears 3's SeqAct_PlayMusicTrack with a MusicTrackStruct
  (TheSoundCue: the linked track's cue, its "Ambient" one if it sets several; fades 2 s), the cue copied with its
  waves. In the cell block the opening cinematic's action fires and **the prison music plays**: the recorded output
  (GOW3_AUDIO_DUMP) matches the first 10 s of Gears 1's Music_Menu at 211.7 s (normalised correlation 0.321,
  median 0.019). Not mapped yet: SeqAct_CrossFadeMusicTracks with SeqVar_MusicTrackBank (the persistent level's).
- Still without a counterpart: SeqAct_VinceTag (335, Gears 1 debugging), SeqAct_WarSetKryllFlockTarget and the
  other Kryll actions (Gears 3 has no Kryll), SeqAct_CauseDamage (147), objectives (Add/Complete/Clear: 214),
  SeqAct_AICombatSpectrum, AIShootAtTarget, SetWalking, TutorialManual.

## 2026-10-13: neighbour-section meshes; several pylons (tools/pylons.py)
- The yard's missing floor under Trigger_3 is rocky_floor002 in **SP_Prison_S02_Meshes**: Gears 1 sections share
  geometry at their borders (found by testing every prison package's static mesh bounds against the point). With
  S02_Meshes among the yard's sources Marcus stands at Trigger_3's real location, touching it unlocks easyPath and
  the Drone spawns (no test placement); 150 MB.
- The navmesh from the one pylon stayed small (66 polygons, one corridor; 59 with the movers left out, so doors
  were not the limit). **tools/pylons.py**: more pylons, clones of the level's (actor, CylinderComponent, both
  NavigationMeshBase subobjects re-pointed in the native tail, new NavGuid, Location / ExpansionSphereCenter /
  ExpansionRadius), linked into Level.Actors, the navigation point list (nextNavigationPoint, NavListEnd) and the
  pylon list (Pylon.NextPylon, PylonListEnd). **levelinject --pylons-at "x,y,z;x,y,z"**. The runtime generator takes
  them all ("5 pylons, the level active"): Pylon_2 171 polygons, Pylon_3 62, Pylon_1 59 (290, five times as much);
  two seeds found no ground. navinject: pylons with an empty dump are disabled (bDisabled: a clone holds the copied
  mesh of its template), the others injected (489 edges, 76 cover slots on polygons; no cross-pylon edges: the
  regions do not touch).
- The yard's encounter waits as in Gears 1: the Drone stays behind easyPath's button-operated door.
- **Kismet references to Gears 1 CoverLinks** now resolve to the target link that received that link's data in the
  cover pass (or the new link made for it): levelinject keeps a (source, CoverLink) -> target map and seeds the
  copier with it. The Dom factory's spawn point (Gears 1 CoverLink_0 -> the target's CoverLink_93) and the hallway
  factories' MoveTargets resolve; **the Dom stand-in (Onyx Guard) spawns in Dom's cover at (-3222, 2566)** (with
  Marcus 35 units away the spawn was refused: encroachment).
- Seen in game: Marcus on the prison catwalk (build\shots_g1_yard_catwalk.png).
- Generation tip: OBJCOPY_SKIP=InterpActor builds the level without movers.

## 2026-10-13: first Gears 1 fight in Gears 3: the prison yard (S03-S04), a Locust Drone from Gears 1's Kismet
- New test mod **build\mods\Gears1_Prison_Yard**: SP_Prison_S03, S04, S04_Scripting, S04_Audio in the Gridlock
  slot (offset 0,0,20000; start at Gears 1's PlayerStart_0 (-3330, 4215, 848); pylon at PathNode_15): 957 mesh
  actors, 29 BSP chunks, 24 movers, 13 triggers, 604 Kismet objects, 119 MB. (A separate mod from the cell block:
  one package per Gears 1 section keeps the sizes down.)
- **Enum arrays**: an array of enum bytes (AISpawnInfo.WeaponLoadOut: EInventoryTypes) is serialized by Gears 3 as
  one FName per element; copied as raw bytes it hung the load (bisected: package S04_Scripting, class
  SeqAct_AIFactory; the cell block's Dom factory had an empty loadout). Now converted by name (WEAP_HammerOfDawn ->
  WEAP_HOD).
- **Stand-ins**: a Gears 1 character whose Gears 3 class is not loaded in this game mode spawns as an available one
  (COG: GearPawn_COGOnyxGuard; Locust: GearPawn_LocustDrone; PAWN_STANDIN) instead of the factory being dropped
  (S04's factories mix Dom and Locust); the conversion reports them.
- Navmesh generated in game for the yard (pylon at PathNode_15: 66 polygons around the upper corridor; dump
  research
avmesh\g1_prison_yard.navm) and injected.
- **The fight**: easyPath is unlocked by touching Trigger_3 (Main_Sequence Gate_0 -> easyPath's
  SequenceActivated -> SeqAct_AIFactory_3). Placed at the player start for the test (OBJCOPY_PLACE now also takes
  an actor name: Trigger_3=x,y,z), the factory spawns a **GearPawn_LocustDrone at Gears 1's PathNode_15**, which
  engages and kills Marcus (health 900 -> 720 -> 576 -> ... -> 0 in about 8 s; kill feed "Drone / Player"):
  build\shots_g1_first_fight.png, _killfeed.png. Gears 3's AI, weapons, damage and HUD, driven by Gears 1's script.
- Seen: below Trigger_3's real location there is no converted floor (Marcus fell 550 units into darkness): part of
  the yard's geometry is missing (to check: the sections' BSP / meshes there).

## 2026-10-13: Gears 1 AI factories spawn Gears 3 characters; the opening cinematic's Kismet runs
- **Enums by name, properly**: the earlier conversion took Gears 1's value index into Gears 3's names; the orders
  differ (AIType 10: Minh in Gears 1, Redshirt in Gears 3). Gears 1's script enums are now read too (Script3 over
  Core/Engine/GameFramework/WarfareGame) and a value goes by its Gears 1 name (aliases: DarkWretchSuicider ->
  DarkWretch, Gus_Resonator -> Gus; a value Gears 3 lacks leaves the property out). A property name tied to
  different enums in the two games (Type: EInputTypes / ESettingsDataType) stays a raw byte.
- **Class references** in properties go through the class mapping (War* -> Gear*, imported from the script package):
  ControllerClass WarAI_Dom -> GearGame.GearAI_Dom (it was None: import matching by the same name).
- **SeqAct_AIFactory** (no longer skipped: its PostLoad crash was the raw-index bug): Gears 1's AISpawnInfo is close
  to Gears 3's; WarPawnClass -> GearPawnClass, mapped to a Gears 3 content class (PAWN_MAP: Pawn_GeistLocust ->
  GearGameContent.GearPawn_LocustDrone, Pawn_COGDom -> GearPawn_COGDom...; CONTROLLER_MAP for the renamed
  controllers). Gears 3 needs the class (its own sets name it: GearGameContent.GearPawn_LocustDroneSavage; with None
  the spawn crashed: null read in the "Last Spawned" routine sub_82A61AA8 -> sub_822C3C20). The class must be loaded:
  levelinject lists the GearGameContent classes of the target and GEARGAMEHORDE_SF (Horde/MP slot: Drone, Sniper,
  Boomer, Theron, Hunters, Berserker, Kantus, Ticker, COGOnyxGuard...; no Dom); a factory spawning another class is
  dropped (reported). OBJCOPY_PAWN_FALLBACK=<class> (testing) puts an available class instead.
- SeqVar_Dom -> SeqVar_Object (receives the factory's spawned pawn); PathNodes are copied (spawn points).
- **The opening**: Dom's spawn waits for the remote event "spawndom", sent by **SP_Prison_Cine_Start** (the opening
  cinematic); with it among the sources (+ the _Audio sublevels) the cinematic's Kismet runs (its Matinee, the
  chapter title, Bag_Dust / Cage_Dust / Weld_ON / Wretch_Drool effect sequences, ToggleHidden...), "spawndom" reaches
  time2Go, and its SeqAct_AIFactory spawns the pawn (fallback GearPawn_COGOnyxGuard for Dom) at the PathNode outside
  Marcus's cell (144, 941); time2Go's SeqAct_AIMoveToActor then walks him to Marcus at the cell door
  (build\shots_g1_ai_spawn.png): Gears 1's Dom scene played by a Gears 3 AI.
- Skeletal meshes' materials: a material whose parent is not compiled for skinning draws as the engine default on a
  skeletal mesh, so skeletal meshes get their own MICs: the converted textures in a clone of a skeletal-capable MIC
  of the target (the first skeletal mesh MIC with Diffuse / Normal / Specular parameters: Gridlock's
  M_COG_Silverback_Part02), OverallEmissive 0 (materialconv.mic_828 now also sets scalar parameters;
  materialconv.mic_texture_params). Gears 1's cinematic Marcus (Marcus_NoArmor, tank top) renders textured in
  Gears 3 (build\shots_g1_marcus_model.png, placed in the button room for the test).
- With no Dom in a Horde slot's content, the factory is dropped and reported ("SeqAct_AIFactory (pawn Pawn_COGDom
  not available)"); the earlier check missed it (the convertibility pass had recorded the class already).
- Open: the cinematic package's static meshes (pipes in front of the cell) draw flat white (probably no baked light
  maps: the full-bright fallback); one 3-stream XMA1 wave (surround) is not converted (multi-stream).

## 2026-10-13: Gears 1 sounds play in Gears 3 (tools/xma.py; audio sublevels)
- **Sound data**: Gears 1 SoundNodeWave = an FName + one bulk block of XMA1 (format 0x0165, a little-endian
  XMAWAVEFORMAT, seek table, 2048-byte packets); Gears 3 = four bulk blocks (raw, PC, Xbox 360, PS3), the Xbox one
  with a 12-byte size header, an XMA2WAVEFORMATEX (0x0166, big-endian, 52 bytes) and the packets. The compressed
  frames are the same; packet headers have the same layout (Gears 1's 15-bit first-frame offsets equal the frame
  walk on all 298 packets of SP_Prison_S01_Audio) except the top 6 bits: XMA1 a sequence number, XMA2 the frames
  starting in the packet. tools/xma.py walks the frame chain (15-bit lengths, across packets; 0x7FFF = padding to
  the packet's end) and writes those counts; on Gears 3's own waves the walk reproduces the header counts on 213 of
  229 (the others: multi-stream stereo). **Lossless**: ffmpeg decodes the converted XMA2 to exactly the XMA1
  original's PCM on all 13 of S01_Audio's waves (max sample difference 0). Frames x 512 / rate = Duration.
- **Sound objects** (objcopy): distributions are UComponents (8-byte prelude: they were dropped as unconvertible);
  SoundNodeAmbient(NonLoop): Gears 1's MinRadius/MaxRadius/VolumeModulation/PitchModulation/DelayTime
  distributions -> Gears 3's RadiusMin/Max, VolumeMin/Max, PitchMin/Max, DelayMin/Max floats, Wave -> SoundSlots;
  SoundCue SoundGroup -> SoundClass; SoundNodeWave -> XMA2 + NumChannels/SampleRate/RawPCMDataSize, bulk offsets
  through upkwrite fixups. SoundCue / SoundNodeWave are copied when referenced (PlaySound, AudioComponents, nodes);
  ambient sound actors (AmbientSoundSimple / NonLoop) are copied; a source package without Kismet still gets its
  actors copied. The link rebuild only touches plain objects (not components).
- Prison + SP_Prison_Audio, S01_Audio, S02_Audio: 28 ambient sound actors; the copies play (AudioComponent
  PlaybackTime advances). Spawning in Trigger_0 (the cell door): SeqEvent_Touch_5 (S01_Scripting) -> remote event
  "triggeredChopperFlybySound" -> SeqEvent_RemoteEvent_3 in **G1_SP_Prison_S01_Audio** -> SeqAct_PlaySound_0.
- **runtime GOW3_AUDIO_DUMP=<file>**: the game's output (6 channels summed, raw little-endian float32, 48 kHz).
  Cross-correlating that recording with the decoded helicopter_flyby04: normalised peak 0.300 at 164.7 s (the
  spawn), median 0.011; a control (the wind loop, not near) 0.037. The Gears 1 flyby plays in Gears 3.

## 2026-10-12: Gears 1 animations play in Gears 3 (AnimSet / AnimSequence)
- objcopy copies an AnimSet when a copied object refers to it (DEEP_COPY: Matinee groups' GroupAnimSets,
  components' AnimSets), as a top-level Trilogy_<name> with its sequences.
- **Enums by name**: Gears 3 writes an enum ByteProperty as the enum's FName in the tag and the value's FName as the
  data (RotationCompressionFormat = ACF_Float96NoW); the copier wrote Gears 1's raw byte with enum None. Script3 now
  reads Gears 3's enums (a ByteProperty export ends with its Enum reference, an Enum export with its value names) and
  every Gears 1 enum byte is written by name (Physics -> PHYS_Interpolating...). This applies to all copied objects
  (Kismet, actors), not only animations.
- **Key encoding**: Gears 1's sequences are AKF_ConstantKeyLerp (per track: TransOffset, NumTransKeys, RotOffset,
  NumRotKeys; translations 12-byte vectors; rotations Float96NoW...). Gears 3 still has that enum value, but none of
  its sequences use it, and played as is the crow's animation rendered as huge spikes. The keys are now re-laid as
  AKF_VariableKeyLerp, the encoding Gears 3's own Float96NoW sequences use: per track, keys then (more than one key)
  a frame index per key (u8 below 256 frames), padded to 4 bytes with 0x55; Gears 1's evenly spaced keys at frame
  round(k * (frames - 1) / (keys - 1)); offsets rewritten. Native data: Gears 3 has a raw-animation count (0) before
  the byte stream.
- Ruled out on the way (kept as switches): conjugating non-root rotation keys (OBJCOPY_CONJUGATE_KEYS; offline the
  crow's keys as stored fit its skeleton best: frame 0 lies 14.5 units from the reference pose, conjugated 17.7;
  reference bones 7.6 units from their vertices as stored, 23.3 conjugated), translations from the skeleton
  (OBJCOPY_ANIM_ROTATION_ONLY). Remember: a reference-pose render proves nothing about bone conventions (the skinning
  matrices are identity).
- In game (test: OBJCOPY_TEST_ANIM=SP_Bird1_Anims:FlyQuick loops it on the copied skeletal mesh components): the
  crows flap, wings up then down a second later (build\shots_g1_crows_flap_a.png, _b.png); AnimNodeSequence
  CurrentTime cycles through FlyQuick's 2.03 s.

## 2026-10-12: Gears 1 skeletal meshes in Gears 3 (tools/skelmesh.py)
- **Formats** (documented in tools/skelmesh.py): both games serialize Bounds, Materials, Origin, RotOrigin,
  RefSkeleton, SkeletalDepth, LODModels, a name -> bone map. Differences, 374 -> 828:
  - FMeshBone 48 -> 52 bytes (+ BoneColor);
  - section 10 -> 13 bytes (NumTriangles u16 -> u32, + TriangleSorting byte);
  - index buffer: count + u16 -> a container (u32 NeedsCPUAccess, u8 DataTypeSize, bulk array: element size,
    count, data);
  - Gears 1 only: ShadowIndices, ShadowTriangleDoubleSided, Edges;
  - vertices: Gears 1's 40-byte (position 3f, TangentX/Y/Z, UV 2f, 4 bone indices, 4 weights) -> Gears 3's GPU skin
    buffer (NumTexCoords, bUseFullPrecisionUVs, bUsePackedPosition, MeshExtension, MeshOrigin, bulk array:
    TangentX, TangentZ (w: binormal sign), 4 bone indices, 4 weights, position packed u32 or 3f, UVs half or float);
  - Gears 3's tail after the LOD: an int, the name map, a constant-shaped block (zeros, 1, 0, 4, a float, zeros).
  The 374 reader parses the prison's crow (SP_Bird1_SKMesh, 32 bones, 552 vertices) to its last byte; the 828
  reader parses 61 Gears 3 meshes (vertex formats: 24 bytes packed + half UVs (51), 28/32 with 2/3 UV sets, one
  unpacked 32-byte: COG_Jace_Summer).
- **skelmesh.to_828**: first LOD; bones (+ BoneColor -1), sections, 16/32-bit indices, chunks, required bones,
  empty raw point indices (bulk offset fixed up to its position), a 32-byte unpacked vertex (half UVs) with
  **MeshExtension 1 / MeshOrigin 0** (the shader applies pos * extension + origin even unpacked: the mesh's
  half size there blew the crow up 45 times), TangentZ.w = sign of the basis determinant, LODInfo built (per-section
  bEnableShadowCasting / TriangleSortSettings), the tail's constant block from one of the target's meshes.
- **levelinject --kismet**: SkeletalMeshActors are copied (their DynamicLightEnvironment, SkeletalMeshComponent,
  AudioComponent and AnimNodeSequence: template names as Gears 3's Default__SkeletalMeshActor), their meshes
  converted (materials through the level's material conversion). Test switches: OBJCOPY_UNHIDE=1 (actors that
  scripts would reveal stay visible), OBJCOPY_PLACE=Class=x,y,z (actors of that class put there, 80 units apart).
- In game: the prison's four crows (hidden until the door sequence; placed in the button room for the test)
  render in their reference pose, wings, legs and claws right (build\shots_g1_birds.png). Animations (AnimSets)
  are not converted yet.

## 2026-10-12: a Gears 1 button opens a Gears 1 door in Gears 3 (interaction triggers, Matinee)
- **Interaction triggers**: Trigger_DoorInteraction and Trigger_ButtonInteraction exist in Gears 3 (Trigger
  subclasses, with SeqEvt_DoorOpen / SeqEvt_ButtonPush < SeqEvt_Interaction and the push/kick special moves), so
  they are copied like triggers; their AlignToObj points at Note actors, copied too. The actors to copy are now a
  closure (Kismet's references, the movers, and whatever those refer to) copied in one pass, so references between
  them resolve.
- **Matinee move tracks**: Gears 3 evaluates an InterpTrackMove key through LookupTrack.Points[key] ({GroupName,
  Time}, one per PosTrack key; a later engine addition). Gears 1 tracks have none, and the first Matinee crashed
  reading the empty array (sub_82551778, InterpTrackMove at +136). objcopy now has per-class fix-ups (FIXUPS):
  InterpTrackMove gets a LookupTrack with GroupName None at each PosTrack key's time.
- **In game (Prison, easyDoorRoom)**: standing at the converted button, Gears 3 shows its own "X push button"
  prompt (build\shots_g1_button_prompt.png). Pressing X fires SeqEvt_ButtonPush, and the room's whole Gears 1
  script runs: SeqAct_Interp_0 (Matinee), PlaySound, CameraLookAt, Gates, Toggles, a WaitForLevelsVisible (emptied),
  FinishSequence, and five remote events, which easyCoverRoom's SeqEvent_RemoteEvents receive (its SeqCond_IsCoop
  and Toggles run). The Matinee swings the door InterpActor (Gears 1 InterpActor_1): yaw -16384 -> -16116 -> -1024
  (about 85 degrees).
- The cell-block door (hardRouteDoor) is locked by design: its SeqEvt_DoorOpen ships disabled and a level-load
  Toggle switches its trigger's collision off until story events. Its leaves are SkeletalMeshActors animated by
  Matinee (InterpTrackAnimControl): skeletal meshes are not converted yet.
- prop_log: "Prop+N" logs the dword N bytes into a property (Rotation+4 = yaw, Location+8 = z).
- Test scripts: tests/prison_button.txt (start at -2210,-40,20830 facing +y; looks at the door, presses X),
  tests/prison_door.txt.

## 2026-10-12: actors for the converted Kismet: triggers fire it, doors and movers are in the level
levelinject --kismet now also copies actors (tools/objcopy.py), and runs last (the cover/environment/replace steps
rewrite the Level from its original data, which had dropped the new actors from Level.Actors).
- **Actor copy**: Gears 1 actor prelude (32 bytes: Node, StateNode, ProbeMask, IgnoreMask, two shorts, StateStack,
  Offset, NetIndex) -> Gears 3's 26 (Node = StateNode = the class, ProbeMask -1, a short (varies arbitrarily in
  Gears 3's own actors: 0), StateStack, Offset -1, NetIndex -1); flags 0x0207000100000000, archetype None, as
  Gears 3's level actors. Components (8-byte prelude in both) get their class default's template as archetype
  (import <package>.Default__<actor class>.<template name>, e.g. Engine.Default__Trigger.CollisionCylinder): that is
  how the engine matches an instance to the class's component. Location + the conversion offset; actor names made
  unique across the level (sublevels repeat Trigger_2...). Kind (actor/component/object) from Gears 3's class tree.
- **Which actors**: the Trigger / TriggerVolume / InterpActor exports the copied Kismet refers to (Originator,
  ObjValue, Targets...), and every InterpActor of the sources. A **TriggerVolume becomes a Trigger** whose collision
  cylinder covers the brush's bounds (Model bounds after its properties; scale, pre-pivot; rotation ignored): its
  brush Model and cached physics data are not converted. InterpActor meshes go through the level's mesh and
  material conversion; their StaticMeshComponent loses its Materials override (Gears 1 materials) and gets an
  empty LODData tail; their DynamicLightEnvironment components already carry Gears 3's template name.
- Prison (P, S01, S02 + _Meshes, _Scripting): 13 Triggers, 10 TriggerVolumes (as Triggers), 8 InterpActors.
- **In game**: the actors are in the level (Trigger.GeneratedEvents is filled at run time: 0 -> 2), spawning inside
  Trigger_0 (the cell door) touches it (Touching 1), **SeqEvent_Touch_5 fires** and runs its
  SeqAct_ActivateRemoteEvent ("triggeredChopperFlybySound": the listener is in SP_Prison_S01_Audio, not converted).
  The door InterpActor_36 (SizedDoor1_DR) renders and blocks the player (stops 40 units from it:
  build\shots_g1_door.png; dark: movers are lit by their light environment only).
- Before the fix, the triggers' components stayed unattached (Owner 0) while Gears 3's own went 0 -> owner: the
  sign that an actor is not in Level.Actors.
- **tools/upkwrite.py**: a replaced export is written once (its last version). Every replacement used to be
  appended, superseded copies left as dead data (the Level replaced once per source package: 7 MB); every package
  written by the tools gets smaller.
Next: doors that open (Gears 1's Trigger_DoorInteraction / ButtonInteraction and the Matinee that moves them),
SkeletalMeshActors, Emitters, sounds (the _Audio sublevels), PlayerStart / checkpoints.

## 2026-10-12: converted Gears 1 Kismet runs in Gears 3
Every build since the object-flags fix had hung or crashed at match load ("Preparing match...", frames stop at ~7,700;
earlier "loaded" runs were the build whose copies the loader skipped). Bisected by source package
(LEVELINJECT_KISMET_ONLY=<packages>) and class (OBJCOPY_SKIP=<classes>), with two new diagnostics:
- **runtime/crash.cpp**: a crash also logs every guest register that holds a UE3 object (name, class, outer).
- **tools/fnstrings.py** <default.bin> <ppc dir> <addresses>: the strings a recompiled function refers to (lis/addi
  pairs into .rdata, UTF-16 or ASCII), to name functions in a crash or hang stack.
Fixed, in tools/objcopy.py:
1. **Array element types**: in cooked script packages an array's inner property carries the array's name, and
   property_types kept whichever came first, so SequenceObjects / LinkedVariables / Targets were "object properties"
   and their Gears 1 indices were copied raw (a sequence then held Marcus's AnimSequences: crash in the sequence
   init under LoadMap). Inner properties are skipped now. Gears 1's game script is WarfareGame.xxx (672 classes),
   not WarGame.xxx.
2. **Objects whose data does not convert are dropped** (references to them None) instead of written with empty
   properties and their raw native data: Gears 1's DistributionVectorUniform (a component: its prelude is not a
   NetIndex) under the ActorFactoryRigidBody of the physics-prop prefabs hung the load.
3. **SeqAct_AIFactory not copied** (SKIP_CLASSES): its Gears 3 PostLoad crashed on the Gears 1 spawn data
   ("Actor is static" logged through a null output device: sub_8243CDE8 from sub_82A5FBE0, the "Spawn Points"
   handler). Enemies need their own mapping.
4. **Links rebuilt from Gears 3's classes** (objcopy.Upgrader, what the editor's UpdateObject does and cooked data
   never gets): each op gets its Gears 3 class's InputLinks/OutputLinks/VariableLinks/EventLinks (class defaults,
   nearest in the super chain, from Core/Engine/GameFramework/GearGame) carrying the connections of the Gears 1 link
   with the same description (else the same index when the counts agree); Gears 1 links without a counterpart are
   appended (a switch's "Link 3"); every InputLinkIdx follows its target's new input order. Sequences keep their own
   links (they come from their SequenceActivated events and FinishSequence actions: replaced by the empty class
   default, 20 links pointed past their target's inputs). Gears 1's SeqEvent_LevelBeginning becomes
   SeqEvent_LevelLoaded on "Beginning of Level"; LevelLoaded's "Out" goes on "Loaded and Visible".
5. **Level streaming emptied** (neutralize_streaming): the chosen sublevels are merged into the persistent level and
   Gears 1's others do not exist, so SeqAct_LevelStreaming / LevelVisibility / MultiLevelStreaming /
   WaitForLevelsVisible lose their level names and stop blocking: they complete and fire their outputs.
Result (Prison P, S01, S02 + _Meshes, _Scripting: 1,048 objects, 562 ops relinked, 56 streaming actions emptied):
the match loads (12,459 frames in 230 s, Marcus in his cell), and **Gears 1's Kismet runs**: SeqEvent_LevelBeginning
and LevelLoaded fire, the emptied MultiLevelStreaming / WaitForLevelsVisible actions complete, subsequence
initialPlayerStuff activates (its SequenceActivated event fires), SeqAct_Delay runs, the hardRouteDoor prefab's
LevelLoaded fires its SeqAct_Toggle. Chains stop where they reach actors: actor references are still None
(triggers, doors, InterpActors, spawners are the next conversion). The G1 LevelLoaded with MaxTriggerCount 0 fired
twice (Gears 3's own use 1).

## 2026-10-12: Gears 1 Kismet copied into Gears 3 (tools/objcopy.py, levelinject --kismet)
- Class survey (tools/class_compat.py, the Prison's 25 packages): of 128 Kismet/game classes, 91 exist in Gears 3
  under the same name, 7 renamed (War* -> Gear*), 30 missing (objectives, tutorials, SeqVar_Dom, camera shake,
  UIStateSequence...). A Kismet object's layout is the same in both games: a 4-byte NetIndex prelude, tagged
  properties (InputLinks/OutputLinks/VariableLinks/EventLinks: arrays of tagged structs; Gears 3's carry more fields).
- **tools/objcopy.py**: copies Gears 1 exports into a Gears 3 package. Properties re-encoded 374 -> 828 (bool: 4-byte
  value -> size 0 + one byte; byte: + enum FName 'None'; structs recursively when their value parses as tagged
  properties; arrays by element type, read from the Gears 1 script packages' property declarations: an ArrayProperty
  export's Inner child); object and name references remapped; a source export not copied becomes None. Classes: a
  Gears 3 import of the same name (War* -> Gear*), imported from the defining script package (class index over Gears
  3's Core/Engine/GameFramework/GearGame) when the target lacks it. Exports are created first and their outers set
  after (a child can precede its parent). Object flags 0x0007000100000000 as Gears 3's Kismet: with 0x000F0004 (no
  RF_LoadForClient/Server) the loader skipped every copied object.
- **levelinject --kismet**: each source's Main_Sequence (and everything under it) as G1_<package>, listed in the
  target's Main_Sequence.SequenceObjects, ParentSequence set. The Prison (P, S01, S02 + _Meshes, _Scripting): 6
  sequences, 1,051 objects, 103 without a Gears 3 class; the 219 output links of P+S01 resolve to copied objects (171)
  or None (48, the dropped objective/tutorial actions).
- In game: the copies load (582 objects under G1_SP_Prison_P, subsequences time2Go, Music, streamOutside...), join
  the sequence tree (their ParentSequence becomes Main_Sequence at run time), no crash, 60 fps. **They do not run
  yet**: SeqEvent_LevelLoaded / LevelBeginning / Touch in them keep TriggerCount 0 and no copied action activates,
  while Gears 3's own events in the same Main_Sequence fire. (Logged with GOW3_LOG_PROPS="SequenceEvent:TriggerCount;
  SequenceOp:ActivateCount"; prop_log now prints each object's outer.) Not the version: Gears 3's cooked class
  defaults store no ObjClassVersion and its own instances also say 1. Next: the native side that starts sequences
  and activates level events (USequence BeginPlay / InitializeSequence), and the actors Kismet refers to (triggers,
  InterpActors, doors), which are not converted yet (their references are None).

## Trilogy: what it takes
- Package versions: Gears 2 cooked packages are UE3 version 575 (1,744 of 1,765), Gears 3's are 828 (1,824 of
  1,838). Gears 3's engine will not load 575 packages, so the plan is an offline converter from Gears 1/2 packages
  to Gears 3 ones.
- Order of difficulty: textures (same Xbox formats, recompressed into Gears 3's texture file caches), then skeletal
  meshes and animations (skeletons differ: retargeting as in the RE4 work), then static meshes, then materials
  (cooked shaders are per engine version: map onto Gears 3 parent materials with texture parameters), and last whole
  levels (BSP, lightmaps, Kismet, AI and script).
  A realistic first milestone: one Gears 2 character model playable in Gears 3.
- Gears of War 1: on hand since 2026-10-11 (gow1-decomp next to this repository; package version 374, see that entry).

## 2026-10-13: recipes (trilogy.py patch-level); light actors attempted (opt-in, crashes)
- The Gears 3 package edits around a campaign conversion are a recipe: tools/recipes/gears1_prison_campaign.txt,
  applied with `python -P tools/trilogy.py patch-level Gears1_Prison_Campaign tools/recipes/gears1_prison_campaign.txt`
  or `convert-map ... --patch <recipe>`. One line per Gears 3 package with tools/stripkismet.py options (`--keep`,
  `--drop-class`, `--bypass=<op path end>:<out>[+<out>]`, `--set=<obj>:<bool>=0|1`, `--move=<actor>:x,y,z[,yaw]`;
  no options: its Kismet off) or `copy <package> <as package>`; packages are rebuilt from the disc each time
  (`--in-place`: the mod's own converted package). The recipe also switches off the scripts of SP_Nightmare_02_Cine
  and SP_Nightmare_Adam_Cine (Gears 3's later prologue cutscenes, streamed in after the intro). Documented in
  docs/MODDING.md ("Converting into a campaign map"). Rebuild + recipe + playtest: the opening plays through and
  Marcus walks the corridor (x 58..254 at y 1320).
- Light actors (objcopy.LIGHT_CLASSES: Point/SpotLight, *Toggleable, *Movable; components with the same 8-byte
  prelude / props / 8-byte tail layout in both games; FIXUPS light_component: LightingChannels Dynamic +
  CompositeDynamic only, no shadows) **crash or hang the campaign load** of SP_Nightmare_02_S: all 433 of the prison
  (render stops at ~107 s, main thread waiting in sub_82387A38 -> sub_82387D68 -> sub_82324BB0 on the loader
  thread in sub_823F4238, the file I/O routine); only the cutscene's 7 Toggleables: access violation reading guest
  0x10 (lr 822B9628); raw components (OBJCOPY_LIGHTS_RAW=1, no fixup) the same. Suspects: references to the skipped
  editor components (PreviewLightRadius / Components -> DrawLightRadiusComponent), the Gears 3 class default's
  component template names, light-specific native data. Left opt-in (`OBJCOPY_LIGHTS=1`); not in the mod.
- An intermittent stall was seen once before the lights (render stopped at ~157 s during the opening, not
  reproduced in the next runs).

## 2026-10-13: Gears 1's colour grading in the campaign slot; following Dom out of the cell
- The sepia over everything was Gears 3's dream grading: an unbound PostProcessVolume_3 in SP_Nightmare_02_W
  (always loaded; colour-grading lookup table + film grain), now also loaded a second time as the empty
  SP_Nightmare_01. The recipe switches it off in _02_W (`--set` now also takes level actors) and puts Gears 1's prison
  post-process (SP_Prison_P WorldInfo: Scene_HighLights 0.8/0.8/0.7, MidTones 1.3, Desaturation 0.4, DOF) into the
  copy's volume: stripkismet `--g1-environment=<Gears 1 package>` (levelinject.environment on any package with a
  volume; convert-map --environment did nothing here: SP_Nightmare_02_S has no PostProcessVolume). Recipe lines
  can write Gears 1's cooked folder as `{g1}` (GOW1_DISC, default ../gow1-decomp/disc).
  build\shots_g1_opening_g1_postprocess_200.png: Gears 1's blue-grey look.
- tests/g1_follow_dom.txt: after the cutscene, north out of the cell, then west along the corridor: Marcus reaches
  (-292,937); touch events fire in time2Go (the move tutorial's gate), G1_SP_Prison_S01_Scripting and the level.
  Dom reached his first goal (-563,1149) in most runs; once he stayed at the cell door (116,961) with his move order
  active.
- Characters were still lit only by the baked level (near silhouettes): fixed below (light actors).

## 2026-10-13: Gears 1's lights for characters (the light-component crash fixed)
- The crash / hang with converted light actors: the copied light components had **no tail**. A Gears 3 (and Gears 1)
  light component ends with 8 bytes (InclusionConvexVolumes, ExclusionConvexVolumes: empty arrays); the copier writes
  a component's tail only from COMPONENT_TAIL, so Gears 3 read past the component. COMPONENT_TAIL now has
  PointLightComponent / SpotLightComponent: bytes(8). (Bisected: minimal properties still crashed; the export dump
  showed `tail 0` against 8 in Gears 3's own levels.)
- Every light of the converted packages is copied (with --kismet; LEVELINJECT_NO_LIGHTS=1 leaves them out): the
  prison campaign gets PointLight 333, SpotLight 100, PointLightToggleable 5, SpotLightToggleable 4 (the cutscene's,
  which its Matinee drives). Their LightingChannels are Dynamic + CompositeDynamic only (the level geometry has their
  light baked) and they cast no shadows. Playtest (tests/g1_follow_dom.txt): loads, full frame rate, Marcus, Dom
  and the cutscene actors lit (no longer silhouettes).

## 2026-10-13: menus stopped answering pad and keyboard after a mouse click (fixed)
Reported: after clicking to navigate a menu once, neither keyboard/mouse nor the controller drove the menus.
Reproduced with tests/menu_click_then_pad.txt (click Campaign, keep moving the cursor, then d-pad / B / arrows):
- Gears 3 reuses the main menu's list for its submenus (MainMenuSP.MainMenu.Nav.MenuList1.MenuList: Campaign's 3
  options shown in the 7 rows of the main menu). The spare row renderers stay visible with their old `index`, so
  the cursor over the empty rows below "Coming soon!" hovered options 3-6 and hover set `selectedIndex` past the
  end of the data: the highlight sat on an empty row and the d-pad could not move it any more (screenshot: the bar
  at an empty row, nothing selected in the submenu).
- ui/gfx_menu.cpp: a row whose index is at or past the list's dataProvider.length is not an option (hover and click
  treat it as beside the options); the focused list must be shown (it and every parent clip _visible, _alpha > 0);
  the list must still have focus (Selection.getFocus) when hover selects; no lookups for 300 ms (was 150) after our
  own press while the menu it opens comes up. Verified: hover stops at "Coming soon!", the pad then steps Arcade /
  Standard, B and the arrow keys work.

## 2026-10-13: Dom visible in the opening cutscene (skeletal meshes imported from Gears 1's content packages)
- Reported: Dom invisible during the cutscene. Its Dom (SkeletalMeshActor_0) shows COG_Dom.COG_Dom_CamSkel, an
  **import**: the mesh is cooked into WarfareGame.xxx (Gears 1's always-loaded content; COG_Dom.xxx holds only its
  textures), not into the level. Only the levels' own skeletal mesh exports were converted, and the import resolved
  to nothing (no mesh).
- levelinject: the skeletal meshes the levels import (objcopy.skeletal_mesh_imports; full name by
  objcopy.import_path, whose outer can be an export: the cooked level's package placeholder) are read from the
  Gears 1 package named after the import's outer, else WarfareGame.xxx; that package joins the sources for meshes and
  materials only (no actors or Kismet), and the copier maps the import to the converted mesh (imported_skel).
  Prison: 7 skeletal meshes (Dom's cutscene mesh from WarfareGame.xxx). build\shots_g1_cutscene_dom_155.png.
- Seen: the cutscene's lights are harsh on characters (blown highlights; Gears 1's brightness on Gears 3's
  light environments); calibration to do.

## 2026-10-13: new campaign chapters in Gears 3's menu, loading their own maps
- How a chapter starts: GearMenuGame.BuildCampaignURL -> `GearGame_P?game=geargamecontent.GearGameSP?chapter=N?listen`
  (+ `?loadcheckpoint`); GearGame_P is an empty persistent level (a PlayerStart, an empty Main_Sequence);
  GearGameSP_Base.InitGame stores N in CurrentChapter; native code then loads **Default__GearTypes.ChapterLevelNames[N]**
  (GearGame.xxx defaults: 31 FNames, chapter 0 sp_nightmare_p ... 30 SP_Rooftop_P). Found by tracing: a chapter-0
  MapName changed in config still loaded SP_Nightmare_P; the FName sp_nightmare_p is referenced only by
  Default__GearTypes (and the shader cache).
- The menu's chapters come from config (Coalesced_INT.bin): [ChapterN GearCampaignChapterData] {ChapterNum, ActNum,
  MapName (display only), ScreenshotPathName, IsAlwaysUnlocked} and [ActN GearCampaignActData] {ActNum,
  bRequiredActForGameCompletion, ScreenshotPathName} in Xbox360-GearGame.ini, ListName / DisplayName /
  ChapterNumDesc / ChapterLocation / ChapterTime in GearGame.int. Providers are found by the ChapterNum field
  (GearUIDataStore_GameResource.GetChapterDataProvider), acts by ActNum; the act list grows with the config (an
  Act6 section shows "VI: ..." after Act V). EChapterPoint: 0-30 chapters, 31 CHAP_Gameover, **32-49 CHAP_DLC_1..18**
  (reserved for downloadable campaigns), 50 CHAP_MAX.
- tools/coalesced.py: `merge` (ini fragments: `##### <ini>` headers; Key= replace, +Key= add, -Key= remove,
  *Key=(First,...) replace by first field); the writer now stores strings as UTF-16 like the game (round trip
  byte-identical).
- GearGame.xxx cannot be rewritten offline: it is compressed as a whole, the loader wants that form for startup
  packages, and tools/xpack zero-fills blocks 340-347 (a rewritten uncompressed copy hung the boot after the file
  opened). So **runtime/ue3/campaign_chapters.cpp** does it in memory: reads every chapter 32-49's MapName from the
  active Coalesced_INT.bin (mods::Resolve), finds Default__GearTypes (new ue3::FindObject), and on the game thread
  makes the FNames with the game's constructor **sub_82302F28(FName* out, const TCHAR* UTF-16BE, 1 = FNAME_Add)**
  (as the static initializer at 824F10A0 makes KEY_MouseX...) and points ChapterLevelNames at a larger copy.
  Verified: Act "VI: Gears of War" / chapter "1: 14 Days After E-Day" (chapter 32, MapName sp_nightmare_p) in
  Select Chapter; choosing it loads GearGame_P then SP_Nightmare_P ("campaign chapters: ChapterLevelNames[32] =
  sp_nightmare_p (name 70322 0)"). tests/select_chapter.txt.
- Debugging additions: GOW3_FILE_STACK=<name part> (the call stack of whoever opens a matching file; package loads
  come from the async loader thread 8295AD80), GOW3_WATCH_PROP=Class:Prop@Object,rw (an instance by name, reads too).

## Plan
Trilogy (the goal: Gears 1/2 campaigns inside Gears 3's engine and gameplay), in milestones that each run in game:
1. Assets: done for textures and static meshes (all three games read; Gears 1/2 meshes render in Gears 3). Next:
   skeletal meshes and animations, materials mapped onto Gears 3 parent materials (kDOP collision: done).
2. One Gears 1/2 level's geometry in Gears 3 (in progress): actors injected, lit from their own Gears 1 lighting,
   solid (kDOP), textured (Gears 1 materials on a Gears 3 master), BSP included (with its light maps); all of
   MP_Gridlock runs inside MP_GridlockG3 in Horde (2026-10-12), alone (--replace), its lamps baked, Gears 1's
   cover usable (--cover), its sky and post-process (--environment), its own navmesh (navinject.py). Next: meshes imported
   from other packages, special materials, lighting calibration.
3. Navigation: done for one pylon (Gears 3's own generator in game, edges/segments/cover offline, written into the
   package: navinject.py); mantle and cover-slip edges from cover; several pylons (cross-pylon edges, checked
   offline). Next: a campaign level in game, drop-down edges, the generator's intermittent hang.
4. Kismet/Matinee conversion, enemy mapping, set pieces. Done (2026-10-13): Gears 1's prison opening runs in Gears
   3's campaign slot (SP_Nightmare_02_S) from its cutscene (Gears 1's cameras, director, characters, sound) through
   Dom's first orders to player control, lit by Gears 1's own lights and colour grading; the Gears 3 prologue around
   it is patched by a recipe (tools/recipes/gears1_prison_campaign.txt, trilogy.py patch-level). Next: the rest of
   the prison (S01-S02 scripting past "time2Go", the first enemies), FaceFX, translucent materials (light shafts),
   a persistent level of our own instead of Gears 3's prologue.
5. Campaign flow: chapters in the campaign menu, saves, cinematics.
6. Mod support for other creators (long-term deliverable, added 2026-10-12): accessible enough that others can add
   onto the trilogy port easily. Pieces: a documented mod folder layout and manifest (the runtime already loads
   overrides from build/mods: gow2_files.cpp), load order between mods, one command-line entry point over the
   tools/*.py converters, stable documented formats (packages, navmesh, cover), clear errors, a sample mod.
   Done (2026-10-12): mod folders + mod.ini, priorities, conflicts, merged TOC (runtime); tools/trilogy.py (mods,
   new-mod, check, convert-map); docs/MODDING.md; Gears1_Gridlock as the sample. Next: script/gameplay hooks for
   mods, a mod-enable switch in the launcher, packaging (zip) and versioned format docs.
Tools: a frame-aware LZX decoder in xpack (the failing blocks above).
Other: menus (clickable prompt bar, slider dragging); longer playtests.

## 2026-10-13: Esc "breaking" the menus; keyboard prompts missing in a fresh build

**Esc.** A player log (build\gow3.log, with the new menu input trace) and scripted repros showed no stuck input: Esc
is B in menus, and B on the main menu's first page is `Callback_TransitionToTitle` (MainMenuView.handleInput /
HostBackwards with an empty MenuOptionsHistory). That reloads the front end: about 10 s with the game thread busy and
no XInput polling (a GOW3_HANG_DUMP=<s> thread dump showed it working, not waiting), then PRESS START, where
handleInput reads only GAMEPAD_START, so clicks (A), arrows and Esc did nothing. Clicks and Escs during the back
animation are dropped too (SetDenyInput). Fix (runtime/ui/gfx_menu.cpp, hid.cpp): in menus Esc is not mapped to a
button; hid::TakeMenuEscape hands each press to the game thread, which presses B unless the main menu
(_level0.MainMenuSP.MainMenu) is on its title screen (bOnTitleScreen) or first page (MenuOptionsHistory.length 0, its
list focused, no capturing overlay). A right click goes the same way; a left click on the title screen is Start. The
controller's B is unchanged. The menu input trace now updates its last state outside menus as well (a release during a
transition had been logged as a held key). Tests: menu_esc_root, menu_esc_click_still, menu_esc_then_click,
menu_title_back.

**Prompts.** A build made without setup.ps1 had no build\prompts, so only controller prompts showed. tools/build.cmd
now runs make_prompts.py when kbm_glyphs.rgba is missing. make_prompts failed on Gears of War: Reloaded's updated
packages: version 835 adds an int between an export's flags and its NetObjectCount (1 for textures and packages, 0 for
an ObjectReferencer); upk.py reads it (class and default objects with components still fail, e.g. COG_*_SF.upk).
make_prompts draws the keycaps when Reloaded's cannot be read.

## 2026-10-13: campaign coverage, every chapter

The chapter URL works as the game's command line (ExLoadedCommandLine, GOW3_COMMAND_LINE), so a chapter boots with no
menus. tools/chapter_test.py runs chapters 0-30 for 150 s each on a fresh, isolated test profile (playtest.ps1 now
uses build\testdata and build\testmods; the player's saves and mods are never touched), pressing Start then B every
12 s (skips a cutscene; in play it opens and closes the pause menu), walking, turning, firing and taking cover. All 31
chapters load (SP_Nightmare_P .. SP_Rooftop_P, with their intro/cine persistent levels: SP_Stadium_ColeCine_P,
SP_Imulsion_P, SP_OldShipyard_Intro_P, SP_Azura_Intro_P), render, and run without a crash, black or frozen screen;
when the aimless player dies the checkpoint reloads. Also seen: a brand-new profile shows the brightness calibration screen
("Adjust the slider so the left-most cog is barely visible") after Start, before the main menu: scripts for a fresh
profile press A once more (this looked like a dropped press at first), and GearCampaignChapterData's
IsAlwaysUnlocked (config) unlocks a chapter in Select Chapter (examples/mods/UnlockAllChapters).

## 2026-10-13: Versus with bots; drawn prompts by default

tests/versus_bots.txt (fresh profile): Start, brightness screen (A), VERSUS > PRIVATE > TEAM DEATHMATCH > START GAME
> Yes > pregame lobby (map selection: Start selects the map, Start again starts the match; the right trigger cycles
maps, as in Horde). Character setup, then Team Deathmatch on Checkout: the player with Baird, Griffin, Cole and Clayton
Carmine against five Locust bots; the bots fight (team respawns 15 -> 12, kill feed), 60 fps with default settings.
Casual and Standard need Xbox LIVE (the game says so). Keyboard prompts are now drawn by default
(tools/make_prompts.py drawn; Gears of War: Reloaded's keycaps are an opt-in), so only the Gears 3 disc is needed.

Beast (tests/beast_private.txt): BEAST (3 down) > PRIVATE > difficulty Normal > Start Match > lobby (the Locust side,
map CHECKOUT): one Start selects the map and starts (a second one opens the pause menu) > "Welcome to Beast Mode",
$3750 > A (choose your creature) > A (Wild Ticker, $75) > wave 1 with the 1:00 clock; when it runs out, WAVE 1 FAILED
and the restart menu. Standard (online) asks for Xbox LIVE Gold, like Versus Casual/Standard.
