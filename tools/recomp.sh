#!/usr/bin/env bash
# Regenerate ppc/ from the player's XEX: merge generated bounds into GOW3.toml, run XenonRecomp, report, then run the
# instruction tests. GOW3_GAME = the folder holding default.xex (default: gow3-decomp next to this repository).
set -u
P=$(cd "$(dirname "$0")/.." && pwd)
GAME=${GOW3_GAME:-$P/../gow3-decomp}
GAME=$(cd "$GAME" 2>/dev/null && pwd) || { echo "game folder not found: ${GOW3_GAME:-$P/../gow3-decomp} (run tools/setup.ps1)"; exit 1; }
[ -f "$GAME/default.xex" ] || { echo "no default.xex in $GAME (run tools/setup.ps1)"; exit 1; }
win() { cygpath -w "$1" 2>/dev/null || echo "$1"; }
cd "$P/config" && python - <<'PY'
s=open('GOW3.toml').read()
mark='# ---- generated: explicit function bounds (tools/fix_boundaries.py) ----'
s=s.split(mark)[0].rstrip()+'\n\n'+mark+'\n'+open('GOW3_functions.toml').read().split('\n',1)[1]
# mid-asm hooks last: a [[table]] before the functions list would capture it (TOML scoping)
s=s.rstrip()+'\n\n'+open('GOW3_hooks.toml').read()
open('GOW3.toml','w').write(s)
PY
# generate into ppc_new, then copy over only the files that changed, so the build recompiles just those
rm -rf "$P/ppc_new" && mkdir -p "$P/ppc_new" "$P/ppc"
# XenonRecomp joins file_path to the config folder as text, so it must be relative: reach the game folder through
# a junction in the repository (works across drives; game_link/ is git-ignored and holds no copies)
if [ "$(cd "$P/game_link" 2>/dev/null && pwd -P)" != "$(cd "$GAME" && pwd -P)" ]; then
  powershell -NoProfile -Command "\$l='$(win "$P/game_link")'; if (Test-Path -LiteralPath \$l) { (Get-Item -LiteralPath \$l).Delete() }; New-Item -ItemType Junction -Path \$l -Target '$(win "$GAME")' | Out-Null" \
    || { echo "could not link $GAME"; exit 1; }
fi
sed -e 's|^out_directory_path = "../ppc"|out_directory_path = "../ppc_new"|' \
    -e 's|^file_path = .*|file_path = "../game_link/default.xex"|' "$P/config/GOW3.toml" > "$P/config/GOW3_new.toml"
"$P/tools/XenonRecomp/build/XenonRecomp/XenonRecomp.exe" "$(win "$P/config/GOW3_new.toml")" "$(win "$P/tools/XenonRecomp/XenonUtils/ppc_context.h")" > "$P/recomp.log" 2>&1
rc=$?
# only a complete run replaces ppc/ (a failed one would otherwise delete everything it did not write)
if [ $rc -ne 0 ] || [ ! -f "$P/ppc_new/ppc_func_mapping.cpp" ]; then
  echo "XenonRecomp failed (exit $rc), ppc/ left as it was; see recomp.log"; rm -rf "$P/ppc_new" "$P/config/GOW3_new.toml"; exit 1
fi
changed=0
for f in "$P"/ppc_new/*; do b=$(basename "$f"); cmp -s "$f" "$P/ppc/$b" || { cp "$f" "$P/ppc/$b"; changed=$((changed+1)); }; done
for f in "$P"/ppc/*; do [ -e "$P/ppc_new/$(basename "$f")" ] || rm "$f"; done
rm -rf "$P/ppc_new" "$P/config/GOW3_new.toml"
echo "exit $rc ; changed files $changed ; outside-function $(grep -c 'outside function' "$P/recomp.log") ; unrecognized $(grep -c Unrecognized "$P/recomp.log")"
# instruction tests on the fresh output (tests/instr_tests.cpp): what the recompiler emits for the ops the port
# added or changed, and the recompiled memset
[ -f "$P/tests/instr_tests.cpp" ] && cmd //c "$(win "$P/tools/instr_tests.cmd")" 2>&1 | grep -E "FAIL|passed"
exit $rc
