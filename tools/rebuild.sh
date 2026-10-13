#!/usr/bin/env bash
# Full regeneration: recompile, derive function bounds (switch errors + gotos to labels outside their function),
# recompile again with them, regenerate the import stubs, build. GOW3_GAME = folder with default.xex/default.bin.
set -u
P=$(cd "$(dirname "$0")/.." && pwd)
GAME=${GOW3_GAME:-$P/../gow3-decomp}
win() { cygpath -w "$1" 2>/dev/null || echo "$1"; }
bash "$P/tools/recomp.sh" || exit 1
for round in 1 2 3; do
  out=$(python -P "$P/tools/fix_boundaries.py" "$GAME/default.bin" "$P/recomp.log" "$P/config/GOW3_switch_tables.toml" \
        "$P/ppc/ppc_func_mapping.cpp" "$P/config/GOW3_functions.toml" "$P/ppc")
  echo "$out"
  echo "$out" | grep -q "^switches 0;" && echo "$out" | grep -q "missing labels: 0" && break
  bash "$P/tools/recomp.sh" || exit 1
done
grep -ohE "__imp__[A-Za-z_][A-Za-z0-9_]*" "$P"/ppc/ppc_recomp.*.cpp | grep -v "__imp__sub_" | sed 's/^__imp__//' | sort -u > "$P/runtime/import_list.txt"
python -P "$P/runtime/gen_stubs.py"
cmd //c "$(win "$P/tools/build.cmd")" > "$P/build.log" 2>&1
rc=$?
grep -iE " error|error:|undefined symbol" "$P/build.log" | head -20
echo "build exit $rc"
exit $rc
