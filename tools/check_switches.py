"""Independently re-derive every jump table in GOW3_switch_tables.toml from the game image and report mismatches
(label lists, counts, defaults). usage: check_switches.py D:/gow3-decomp/default.bin config/GOW3_switch_tables.toml"""
import re, struct, sys

BASE = 0x82000000
img = open(sys.argv[1], 'rb').read()
toml = open(sys.argv[2]).read()

def word(a): return struct.unpack_from('>I', img, a - BASE)[0]
def u8(a): return img[a - BASE]
def u16(a): return struct.unpack_from('>H', img, a - BASE)[0]
def simm(w): return (w & 0xFFFF) - (0x10000 if w & 0x8000 else 0)
def rt(w): return (w >> 21) & 31
def ra(w): return (w >> 16) & 31
def rb(w): return (w >> 11) & 31

def lis_addi(a):
    """lis rX,hi ; addi rX,rX,lo at a -> (reg, value) or None"""
    w0, w1 = word(a), word(a + 4)
    if w0 >> 26 != 15 or ra(w0) != 0 or w1 >> 26 != 14 or ra(w1) != rt(w0):
        return None
    return rt(w1), ((simm(w0) << 16) + simm(w1)) & 0xFFFFFFFF

def bound(base, index_reg):
    """walk back from base for 'cmplwi crN,rI,n' + 'bgt crN,default' -> (count, default) or (None, None)"""
    default = None
    for a in range(base - 4, base - 4 * 24, -4):
        w = word(a)
        if w >> 26 == 16 and default is None:          # bc: bgt crN,target (BO=12, BI=4N+1)
            bo, bi = rt(w), ra(w)
            if bo == 12 and bi % 4 == 1:
                default = (a + simm(w & 0xFFFC)) & 0xFFFFFFFF
                crf = bi // 4
        if w >> 26 == 19 and (w >> 1) & 0x3FF == 16 and default is None:   # bclr: bgtlr -> default is a return
            if rt(w) == 12 and ra(w) % 4 == 1:
                default, crf = 'ret', ra(w) // 4
        if w >> 26 == 10 and default is not None:       # cmplwi crN,rA,UIMM
            if (w >> 23) & 7 == crf and ra(w) == index_reg:
                return (w & 0xFFFF) + 1, default
            if (w >> 23) & 7 == crf:
                return None, default                    # compare on another register (index moved)
    return None, default

problems = 0
switches = re.findall(r'\[\[switch\]\]\s*base = (0x[0-9A-Fa-f]+)\s*r = (\d+)\s*default = (0x[0-9A-Fa-f]+)\s*labels = \[([^\]]*)\]', toml)
kinds = {}
for base_s, r_s, default_s, labels_s in switches:
    base, reg, cfg_default = int(base_s, 16), int(r_s), int(default_s, 16)
    labels = [int(x, 16) for x in re.findall(r'0x[0-9A-Fa-f]+', labels_s)]
    t = lis_addi(base)
    derived, kind = None, '?'
    if t:
        treg, table = t
        w2, w3 = word(base + 8), word(base + 12)
        # absolute: rlwinm r0,rI,2,0,29 ; lwzx r0,r12,r0
        if w2 >> 26 == 21 and (w2 >> 11) & 31 == 2 and w3 >> 26 == 31 and (w3 >> 1) & 0x3FF == 23:
            kind = 'absolute'
            derived = lambda i: word(table + 4 * i)
        # computed / offset: lbzx (or lhzx) r0,r12,rI ; [rlwinm r0,r0,2] ; lis/addi r12,target_base ; add
        elif w2 >> 26 == 31 and (w2 >> 1) & 0x3FF in (87, 279):
            load = u8 if (w2 >> 1) & 0x3FF == 87 else u16
            step = 1 if load is u8 else 2
            a = base + 12
            shift = 0
            if word(a) >> 26 == 21:
                shift = (word(a) >> 11) & 31
                a += 4
            t2 = lis_addi(a)
            if t2:
                tb = t2[1]
                kind = 'computed' if shift else 'offset'
                derived = lambda i, load=load, step=step, shift=shift, tb=tb: (tb + (load(table + step * i) << shift)) & 0xFFFFFFFF
        # halfword offsets: rlwinm r0,rI,1,0,30 ; lhzx r0,r12,r0 ; [rlwinm r0,r0,2] ; lis/addi r12,target_base ; add
        elif w2 >> 26 == 21 and (w2 >> 11) & 31 == 1 and w3 >> 26 == 31 and (w3 >> 1) & 0x3FF == 279:
            a = base + 16
            shift = 0
            if word(a) >> 26 == 21:
                shift = (word(a) >> 11) & 31
                a += 4
            t2 = lis_addi(a)
            if t2:
                tb = t2[1]
                kind = 'half-offset'
                derived = lambda i, shift=shift, tb=tb: (tb + (u16(table + 2 * i) << shift)) & 0xFFFFFFFF
    if derived is None:
        print(f'{base:08X}: unknown pattern'); problems += 1; continue
    kinds[kind] = kinds.get(kind, 0) + 1
    want = [derived(i) for i in range(len(labels))]
    count, default = bound(base, reg)
    msgs = []
    if want != labels:
        bad = [i for i in range(len(labels)) if want[i] != labels[i]]
        msgs.append(f'labels differ at {bad[:6]} (config {labels[bad[0]]:08X}, image {want[bad[0]]:08X})')
    if count is not None and count != len(labels):
        msgs.append(f'count {len(labels)} in config, bound check allows {count}')
    if default not in (None, 'ret') and default != cfg_default:
        msgs.append(f'default {cfg_default:08X} in config, image branches to {default:08X}')
    if msgs:
        problems += 1
        print(f'{base:08X} ({kind}, r{reg}): ' + '; '.join(msgs))
print(f'{len(switches)} switches checked {kinds}; {problems} with problems')
