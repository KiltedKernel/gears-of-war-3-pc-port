#!/usr/bin/env bash
# Fetch and prepare Xenia (BSD-3) for the port's GPU: pinned commit, submodules, the port's patch, version.h.
# Then build with tools/build_xenia.cmd (Xenia's CMake + MSVC, libraries + xe_glue), then tools/build.cmd.
# GOW3_XENIA = where (default: xenia next to this repository).
set -eu
T=$(cd "$(dirname "$0")" && pwd)
. "$T/local.sh"   # this install's folders (tools/local.cmd)
X=${GOW3_XENIA:-$T/../../xenia}
COMMIT=$(cat "$T/xenia-commit.txt")
if [ ! -d "$X/.git" ]; then
  git clone https://github.com/xenia-canary/xenia-canary.git "$X"
fi
cd "$X"
if [ "$(git rev-parse HEAD)" != "$COMMIT" ]; then
  git fetch --depth 1 origin "$COMMIT" 2>/dev/null || true
  git checkout -q "$COMMIT"
fi
git submodule update --init --depth 1 --jobs 8
if git apply --check "$T/xenia-gow3.patch" 2>/dev/null; then
  git apply "$T/xenia-gow3.patch"
elif git apply --reverse --check "$T/xenia-gow3.patch" 2>/dev/null; then
  echo "xenia patch already applied"
else
  echo "xenia patch does not apply: check 'git -C $X status'"; exit 1
fi
for d in build-gow3 build; do
  mkdir -p $d
  # written only when it changes (a rewrite would rebuild part of Xenia on every setup run)
  printf '#ifndef GENERATED_VERSION_H_\n#define GENERATED_VERSION_H_\n#define XE_BUILD_BRANCH "canary"\n#define XE_BUILD_COMMIT "%s"\n#define XE_BUILD_COMMIT_SHORT "%s"\n#define XE_BUILD_DATE __DATE__\n#endif\n' "$COMMIT" "${COMMIT:0:7}" > $d/version.h.new
  if cmp -s $d/version.h.new $d/version.h; then rm $d/version.h.new; else mv $d/version.h.new $d/version.h; fi
done
echo "xenia ready at $COMMIT ($X)"
