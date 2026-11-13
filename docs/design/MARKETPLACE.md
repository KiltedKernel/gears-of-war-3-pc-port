# Design: a mod marketplace for COGBox

Status: proposal (2026-11-12), to discuss with Shagg (COGBox) before building. Goal: players browse, search, sort
and install mods from inside COGBox's launcher, like Steam Workshop but for COGBox; creators publish without running
a server.

## Recommendation: a registry repository, not a mod repository

Mods are hosted by their authors; a public **registry** repository holds only small manifest files that describe each
mod and say where to download it. This is the model winget-pkgs, Scoop buckets and Homebrew taps use.

```
cogbox-mods/registry            (a GitHub organisation's repository)
  mods/gears3/hordebank/mod.yaml
  mods/gears3/cogbox-trilogy/mod.yaml
  ...
  schema/mod.schema.json
  .github/workflows/validate.yml   (runs on every pull request)
  .github/workflows/publish.yml    (on merge: builds index.json, published with GitHub Pages)
```

A manifest:

```yaml
id: hordebank
name: Horde Bank
game: gears3
version: 1.2.0
author: someone
description: Interest on unspent Horde money.
tags: [horde, gameplay]
license: MIT
api: 1                      # COGBox mod API level it needs
side: host                  # who needs it in multiplayer (mod.ini's side)
requires: []
download:
  url: https://github.com/someone/hordebank/releases/download/v1.2.0/hordebank-1.2.0.zip
  sha256: 3f1c...
  size: 48213
screenshots: [https://.../1.png]
kind: files                 # files | build (see "Mods made from the player's discs")
```

**Publishing:** a creator opens a pull request adding or updating their manifest. The validation workflow checks the
schema, downloads the zip, checks the hash and size, runs the mod checker (structure, mod.ini, API level, no
executables or disc images, no files over a size limit), and comments the result. A maintainer merges. On merge the
index is rebuilt (`index.json`: every mod's latest manifest plus download counts gathered from the hosts' APIs once
a day) and published.

**The launcher (COGBox repository):** a Browse page next to Mods. It fetches `index.json` (cached; works offline with
the last copy), lists mods for the current game with search (name, author, description, tags), filters (game, tag,
side) and sorting (updated, name, downloads). A mod page shows the description, screenshots, version, requirements
and what it changes. Install downloads the zip, checks the hash, unpacks it into `<game>\mods\`, and turns it on;
installed mods show Update when the index has a newer version. Uninstall moves the folder to a backup.

## Why this shape

- No server to run or pay for: GitHub hosts the registry, the index and usually the downloads.
- Review is transparent (pull requests) and creators keep their own repositories and release pages.
- COGBox's repository stays code only; the registry can have its own maintainers.
- The launcher depends on one JSON format, so another source (mod.io, a mirror) can be added later.

What it does not give out of the box: ratings and comments. A first version can link each mod to its repository's
discussions; real ratings would need a small service later.

Alternatives considered: **mod.io** (hosting, moderation, an API, ratings; but a third party's terms and accounts, and
its rules on content derived from games); **Nexus Mods** (no free API for in-launcher downloads); **Thunderstore**
(self-hostable, but a Django service to run). Mods inside the COGBox repository itself: simplest, but every mod update
becomes a COGBox commit and the repository grows with binaries.

## Mods made from the player's discs

Some mods cannot be shared as files: the trilogy is converted from the player's own Gears 1 and 2 discs, and mods
that modify the games' packages contain the games' data. The registry supports them without carrying game data:

- `kind: build`: the manifest names a builder (a tool and a recipe, e.g. COGBox-trilogy's `trilogy.py build
  gears1_ch1`) and which disc it needs. The launcher shows "Build from your disc", asks for the disc image, and runs
  the build locally.
- Binary patches: a mod ships the difference against the player's own package (a delta format such as xdelta/VCDIFF),
  and the launcher applies it to the player's copy at install time. The mod then contains only the change.

This is also where the policy decisions belong (what may be published, takedown handling); those are Shagg's and the
COGBox maintainers' to make.

## Where things live

- **COGBox** (Shagg88/COGBox): the Browse page and installer in the launcher; the mod format (already there).
- **A COGBox mods organisation** (e.g. `cogbox-mods`): the registry repository, and optionally COGBox-trilogy and the
  sample mods, so creators have one place to look.
- **COGBox-trilogy**: lists itself in the registry as a `kind: build` mod.

## First steps

1. Agree the shape with Shagg; create the organisation and the registry repository with the schema and validation.
2. Add the sample mods and COGBox-trilogy as the first entries.
3. Launcher: Browse (read-only list and search), then install and update, then `kind: build`.
