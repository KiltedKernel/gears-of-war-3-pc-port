#!/usr/bin/env bash
# Fetch and prepare XenonRecomp (MIT) in tools/XenonRecomp: pinned commit, submodules, the port's patch (GoW2 ops,
# fused multiply-add, timebase). Then build with tools/build_xenon.cmd.
set -eu
T=$(cd "$(dirname "$0")" && pwd)
X=$T/XenonRecomp
COMMIT=$(cat "$T/xenonrecomp-commit.txt")
if [ ! -d "$X/.git" ]; then
  git clone https://github.com/hedge-dev/XenonRecomp.git "$X"
fi
cd "$X"
if [ "$(git rev-parse HEAD)" != "$COMMIT" ]; then
  git fetch origin "$COMMIT" 2>/dev/null || true
  git checkout -q "$COMMIT"
fi
git submodule update --init --recursive --jobs 8
if git apply --check "$T/xenonrecomp-gow3.patch" 2>/dev/null; then
  git apply "$T/xenonrecomp-gow3.patch"
elif git apply --reverse --check "$T/xenonrecomp-gow3.patch" 2>/dev/null; then
  echo "XenonRecomp patch already applied"
else
  echo "XenonRecomp patch does not apply: check 'git -C $X status'"; exit 1
fi
echo "XenonRecomp ready at $COMMIT"
