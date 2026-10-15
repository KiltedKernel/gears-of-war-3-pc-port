# Contributing

Bug reports, test results and changes are welcome.

## Reporting a problem

Open an issue with the bug report form: what happened, where (setup, a campaign chapter, a mode, multiplayer, the
launcher, a mod), your version and PC, and `build\gow3.log`. Network lines in the log hide public addresses and join
codes, so it is safe to post.

Results from setups that have not been tested yet are especially useful: multiplayer between two PCs or over the
internet, routers with and without UPnP, and campaign chapters played to their end (docs/STATUS.md lists what is
verified).

## Game files

This repository holds no game code or assets, and must not. Never commit, attach or link to:

- the game's files, disc images (.iso), executables (.xex) or anything extracted from them (packages, textures,
  sounds, movies, generated `ppc/` code);
- decryption keys;
- other people's saves.

Everything game-derived is made on each player's PC from their own disc (`.gitignore` already excludes the usual
file types). Facts about the game (function addresses, data layouts, file formats) belong in
docs/REVERSE_ENGINEERING.md, written in your own words.

## Making changes

- Build and run as in README.md (Developing). `cmd /c tools\build.cmd` rebuilds the runtime, the launcher and the
  server.
- Test with the scripted playtests (tests/README.md: `tools\playtest.ps1`, `tools\mp_test.ps1`). Tests use their own
  data folders and never touch a player's saves, mods or settings; keep it that way.
- Write code like the code around it, and note what you learned in docs/ENGINEERING_LOG.md (newest at the end).
- Changes are released under GPL-3.0 (LICENSE).

## Releasing

- Set the new version in VERSION, CHANGELOG.md (the release heading), README.md and docs/STATUS.md, then run
  `python tools/check_version.py`: it fails when they differ (0.2.1 shipped calling itself 0.2.0 this way).
