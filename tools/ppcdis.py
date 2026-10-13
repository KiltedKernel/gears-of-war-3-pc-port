"""Print the PPC disassembly around a guest address, from the recompiled sources' instruction comments
(each '// insn' line is one instruction, in address order from the function start).
usage: dis.py 0x82457E80 [before=16] [after=24]"""
import bisect, glob, re, sys
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PPC = ROOT + '/ppc'
target = int(sys.argv[1], 16)
before = int(sys.argv[2]) if len(sys.argv) > 2 else 16
after = int(sys.argv[3]) if len(sys.argv) > 3 else 24

starts = sorted(int(m, 16) for m in re.findall(r'\{ 0x([0-9A-F]+), sub_', open(f'{PPC}/ppc_func_mapping.cpp').read()))
func = starts[bisect.bisect_right(starts, target) - 1]
name = f'__imp__sub_{func:08X}'
for path in glob.glob(f'{PPC}/ppc_recomp.*.cpp'):
    text = open(path).read()
    i = text.find(f'PPC_FUNC_IMPL({name})')
    if i < 0:
        continue
    body = text[i:text.find('\n}\n', i)]
    insns = [l.strip()[3:] for l in body.split('\n') if l.strip().startswith('// ')]
    k = (target - func) // 4
    print(f'{name} ({path.split("/")[-1].split(chr(92))[-1]}), {len(insns)} instructions')
    for j in range(max(0, k - before), min(len(insns), k + after)):
        print(f'{"=>" if j == k else "  "} {func + 4 * j:08X}  {insns[j]}')
    break
