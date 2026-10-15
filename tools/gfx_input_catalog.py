"""Catalog of Gears 3's Scaleform menu input: for every handleInput (and the functions it calls in the same class) in
the decompiled movies, each branch on a button (navEquivalent / code) with its phase (keyDown, keyHold, keyUp, any)
and the engine callbacks (ExternalInterface.call) and local actions reached in it. Heuristic: the branch body is the
brace block after the if. usage: python -P tools/gfx_input_catalog.py <folder of FFDec script exports, one folder per movie> > catalog.tsv
(made by tools/gfx_extract.py and FFDec: docs/REVERSE_ENGINEERING.md, "Menu input")"""
import os, re, sys

root = sys.argv[1]
NAV = {'GAMEPAD_A': 'A', 'GAMEPAD_B': 'B', 'GAMEPAD_X': 'X', 'GAMEPAD_Y': 'Y', 'GAMEPAD_START': 'Start', 'START': 'Start',
       'GAMEPAD_BACK': 'Back', 'BACK': 'Back', 'GAMEPAD_L1': 'LB', 'GAMEPAD_R1': 'RB', 'GAMEPAD_L2': 'LT', 'GAMEPAD_R2': 'RT',
       'GAMEPAD_L3': 'LS', 'GAMEPAD_R3': 'RS', 'UP': 'Up', 'DOWN': 'Down', 'LEFT': 'Left', 'RIGHT': 'Right', 'ENTER': 'A',
       'ESCAPE': 'B', 'GAMEPAD_RS_UP': 'RS up', 'GAMEPAD_RS_DOWN': 'RS down', 'GAMEPAD_RS_LEFT': 'RS left',
       'GAMEPAD_RS_RIGHT': 'RS right'}
STR = {'"enter-gamepad_A"': 'A', '"escape-gamepad_B"': 'B', '"gamepad_X"': 'X', '"gamepad_Y"': 'Y', '"start"': 'Start',
       '"back"': 'Back', '"up"': 'Up', '"down"': 'Down', '"left"': 'Left', '"right"': 'Right', '"gamepad_L1"': 'LB',
       '"gamepad_R1"': 'RB', '"gamepad_L2"': 'LT', '"gamepad_R2"': 'RT'}
nav_re = re.compile(r'navEquivalent\s*==\s*(?:gfx\.ui\.NavigationCode\.([A-Z_0-9]+)|("[^"]+"))')
phase_re = re.compile(r'\.value\s*==\s*"(key\w+)"')
call_re = re.compile(r'ExternalInterface\.call\("(\w+)"')
fn_re = re.compile(r'^   function (\w+)\(', re.M)


def block(text, start):
    """the brace block starting at the first { at or after start"""
    i = text.find('{', start)
    if i < 0: return ''
    depth = 0
    for j in range(i, len(text)):
        if text[j] == '{': depth += 1
        elif text[j] == '}':
            depth -= 1
            if depth == 0: return text[i:j + 1]
    return text[i:]


rows = set()
for dirpath, _, files in os.walk(root):
    for fn in files:
        if not fn.endswith('.as'): continue
        path = os.path.join(dirpath, fn)
        text = open(path, encoding='utf-8', errors='replace').read()
        if 'navEquivalent' not in text: continue
        movie = os.path.relpath(path, root).split(os.sep)[0]
        cls = fn[:-3]
        # functions of the class, for one level of calls (this.Foo(...) inside a branch)
        funcs = {m.group(1): block(text, m.end()) for m in fn_re.finditer(text)}
        for m in re.finditer(r'\bif\s*\(', text):
            # the condition: up to the matching parenthesis
            k, depth = m.end() - 1, 0
            for e in range(k, min(len(text), k + 2000)):
                if text[e] == '(': depth += 1
                elif text[e] == ')':
                    depth -= 1
                    if depth == 0: break
            cond = text[k:e + 1]
            navs = []
            for a, b in nav_re.findall(cond):
                navs.append(NAV.get(a, a) if a else STR.get(b, b))
            if not navs: continue
            phases = sorted(set(phase_re.findall(cond))) or ['any']
            body = block(text, e)
            calls = set(call_re.findall(body))
            for name in re.findall(r'this\.(\w+)\(', body):
                if name in funcs: calls |= {f'{c} (via {name})' for c in call_re.findall(funcs[name])}
            local = sorted(set(re.findall(r'this\.(\w+)\(', body)) - {'playSound'})[:6]
            negated = '!=' in cond and '==' not in cond
            for n in sorted(set(navs)):
                rows.add((movie, cls, n + (' (not)' if negated else ''), '/'.join(phases), ', '.join(sorted(calls)) or '-',
                          ', '.join(local) or '-'))
print('movie\tclass\tbutton\tphase\tengine callbacks\tlocal calls')
for r in sorted(rows):
    print('\t'.join(r))
