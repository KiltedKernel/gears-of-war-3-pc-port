# Contributing

Bug reports, test results and changes are welcome.

## Reporting a problem

Open an issue with the bug report form: what happened, where (which converted chapter, and where in it), your COGBox
version, and the game's log (COGBox keeps one per session in `Gears3\logs\`). Screenshots of the same spot in the
original game help a lot with visual problems.

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

- Build chapters as in README.md, into a scratch folder; test them on COGBox with a test copy of the mod.
- Changes to the game's runtime or the launcher (mod support included) belong in COGBox: open them there, on `dev`.
- Write down what you learn in docs/ENGINEERING_LOG.md, and game facts in docs/REVERSE_ENGINEERING.md.
