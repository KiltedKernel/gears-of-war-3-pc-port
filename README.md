# COGBox-trilogy

The Gears of War trilogy inside Gears of War 3, as mods for [COGBox](https://github.com/Shagg88/COGBox): Gears 1's
and Gears 2's campaigns converted into Gears 3's engine and gameplay, built on your PC from **your own discs**. Also
here: the tools that do the conversion, sample mods, and what we have learned about the three games' formats.

**No game files are included, and none are ever made available.** Every package the converter writes is derived from
your discs and stays on your PC.

Status: Gears 1's first three chapters convert and play on COGBox (Act VI in Select Chapter); chapter 1, the prison,
is the one being polished first. See [docs/TRILOGY.md](docs/TRILOGY.md) and the newest entries of
[docs/ENGINEERING_LOG.md](docs/ENGINEERING_LOG.md).

## What you need

- COGBox with Gears of War 3 set up (its launcher does that from your Gears 3 disc image).
- Your own Gears of War 1 disc image (and, later, Gears 2's).
- Windows 10 or 11, Python 3.11 or newer with `numpy` and `Pillow` (and `lz4`), Git, and Visual Studio 2022 (Build Tools are
  enough) with clang-cl, for one small helper (the package decompressor, built automatically the first time).

## Building the trilogy's chapters

1. Extract your Gears 1 disc image:
   `python -P tools/xdvdfs.py "Gears of War.iso" extract-all ..\gow1-decomp\disc`
2. Point the converter at Gears 3's files and your COGBox mods folder, and build a chapter:
   ```
   set GOW3_DISC=<COGBox>\Gears3\game\extracted
   set GOW3_MODS=<COGBox>\Gears3\mods
   python -P tools/trilogy.py build gears1_ch1 --replace
   ```
   `python -P tools/trilogy.py list` shows the chapters (`tools/recipes/*.ini`). A chapter takes about half an hour.
   The first build of a chapter also generates its navigation meshes, which for now needs the legacy runtime (see
   [docs/NAVMESH.md](docs/NAVMESH.md)); later builds pass `--reuse-navmesh`.
3. Turn the chapters on in COGBox's Mods page and pick **Act VI** in Select Chapter.

## What is here

| Path | What |
|---|---|
| `tools/trilogy.py`, `tools/recipes/` | chapter builds: a recipe per chapter, every conversion step in order |
| `tools/levelinject.py`, `objcopy.py`, `materialconv.py`, `meshconv.py`, `bspconv.py`, `lighting.py`, ... | the converter: levels, actors and Kismet, materials, meshes, light |
| `tools/navinject.py`, `pylons.py`, `cover.py`, `g1route.py` | navigation, cover, test routes over Gears 1's path network |
| `tools/upk.py`, `upkwrite.py`, `texture2d.py`, `staticmesh.py`, `skelmesh.py`, `coalesced.py`, `kismetgraph.py` | reading and writing the games' packages and config |
| `tools/modtool.py` | install, check and package mods |
| `examples/mods/` | small sample mods (config, menu text, a chapter) |
| `docs/` | the trilogy, the mod format, Gears 1 against Gears 3, reverse-engineering notes, the engineering log |

## The old PC port

This repository began as a native Gears of War 3 PC port (static recompilation, its own runtime, launcher and
multiplayer). COGBox has since become the place where the games run, and this project's mod support went into it.
The port is kept at the tag [`legacy-port-final`](../../tree/legacy-port-final); its documents are in
[docs/legacy/](docs/legacy/).

## Licence

GPL-3.0 (see [LICENSE](LICENSE)); `modcore/` (the portable mod core COGBox's mod support started from) is MIT.
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) lists what the tools build on. Gears of War is a trademark of Microsoft; this project is not
affiliated with Microsoft, Epic Games or The Coalition.
