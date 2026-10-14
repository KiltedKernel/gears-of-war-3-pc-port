# Sourced by the bash scripts: GOW3_GAME / GOW3_XENIA from tools/local.cmd (written by tools/setup.ps1) when they are
# not set already. Without local.cmd the scripts keep their own defaults.
_gow3_local="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/local.cmd"
if [ -f "$_gow3_local" ]; then
  while IFS= read -r _l; do
    _l=${_l%$'\r'}
    case "$_l" in
      'set "GOW3_GAME='*)  [ -n "${GOW3_GAME:-}" ]  || { _v=${_l#set \"GOW3_GAME=};  GOW3_GAME=$(cygpath -u "${_v%\"}" 2>/dev/null || echo "${_v%\"}"); export GOW3_GAME; } ;;
      'set "GOW3_XENIA='*) [ -n "${GOW3_XENIA:-}" ] || { _v=${_l#set \"GOW3_XENIA=}; GOW3_XENIA=$(cygpath -u "${_v%\"}" 2>/dev/null || echo "${_v%\"}"); export GOW3_XENIA; } ;;
    esac
  done < "$_gow3_local"
fi
unset _gow3_local _l _v
