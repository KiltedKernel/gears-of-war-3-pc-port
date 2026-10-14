"""Build the keyboard/mouse button-prompt glyphs (build/prompts/kbm_glyphs.png + .json) for the runtime's prompt swap.
By default every icon is drawn here (black keycaps with a white rim, labels in Segoe UI Bold, which every Windows
has): the port needs nothing but the player's Gears of War 3 disc, and every player sees the same icons.
Opt-in: "reloaded" takes the key icons from the player's own Gears of War: Reloaded install (its WarfareFonts_Keycaps20
font, Steam version; mouse icons are still drawn), falling back to drawn ones when it is missing or unreadable. The
output is generated on the player's PC and never committed.
usage: python -P make_prompts.py [drawn | reloaded | <Reloaded install folder>] [out folder]
  ("auto", the old default, now means drawn)
Each glyph replaces one character of the game's Warfare_HUD.Xbox360_18pt font (see runtime/ui/prompts.cpp)."""
import importlib.util, json, os, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
H = 26                       # glyph height (Xbox360_18pt cells are 26 texels tall)
SS = 4                       # supersampling for the drawn icons

def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + '.py'))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

# ---- Reloaded keycaps -------------------------------------------------------------------------------------------
KEYCAP_PACKAGE = 'Startup_LOC_INT.upk'
FONT_PAGES = {'WarfareFonts.Fonts.WarfareFonts_Keycaps20.Texture2D_3': (2048, 2048),   # characters
              'WarfareFonts.Fonts.WarfareFonts_Keycaps20.Texture2D_4': (2048, 2048)}   # named keys
CELL, PITCH_X, PITCH_Y, X0, Y0 = 136, 148, 160, 4, 16

def char_cell(c):            # page 1: character c sits in slot c - 31, 13 per row
    i = ord(c) - 31
    return (X0 + PITCH_X * (i % 13), Y0 + PITCH_Y * (i // 13), CELL, CELL)

NAMED = {                    # page 2 (x, y, w, h)
    'Del': (540, 816, 136, 136), 'BkSp': (688, 816, 244, 136), 'Up': (944, 816, 136, 136), 'Down': (1092, 816, 136, 136),
    'Left': (1240, 816, 136, 136), 'Right': (1388, 816, 136, 136), 'Ctrl': (1536, 816, 244, 136),
    'Alt': (261, 976, 136, 136), 'Esc': (1364, 976, 136, 136), 'Tab': (1512, 976, 244, 136),
    'Shift': (4, 1136, 244, 136), 'LShift': (261, 1136, 244, 136), 'Enter': (1780, 1296, 244, 136),
    'Space': (261, 1456, 355, 136),
}

def reloaded_keycaps(install):
    pkg = None
    for root, dirs, files in os.walk(os.path.join(install, 'WarGame', 'CookedPC')):
        if KEYCAP_PACKAGE in files: pkg = os.path.join(root, KEYCAP_PACKAGE); break
    if not pkg: return None
    upk, dxt = load_tool('upk'), load_tool('dxt')
    p = upk.Package(pkg)
    pages = []
    for path, (w, h) in FONT_PAGES.items():
        i = next(i for i in range(len(p.exports)) if p.path(i) == path)
        data = p.export_data(i)
        # bulk data record: flags, element count, size on disk, offset, then the top mip (DXT5, linear)
        for o in range(0, 2048, 4):
            cnt, sz = np.frombuffer(data[o + 4:o + 12], dtype='<i4')
            if cnt == w * h and sz == cnt: break
        pages.append(Image.fromarray(dxt.decode_dxt5(data[o + 16:o + 16 + w * h], w, h), 'RGBA'))
    return pages

# ---- drawn keycaps (fallback, and the mouse icons) ----------------------------------------------------------------
def keycap(width_cells, label=None, mouse=None):
    """black keycap with a white rim like Reloaded's, at 136-texel scale; label text or a mouse drawing"""
    w, h = int(136 * width_cells) if width_cells > 1 else 136, 136
    im = Image.new('RGBA', (w * SS // 4, h * SS // 4), (0, 0, 0, 0))
    s = SS / 4
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((2 * s, 2 * s, (w - 2) * s, (h - 2) * s), radius=18 * s, fill=(255, 255, 255, 255))
    d.rounded_rectangle((12 * s, 12 * s, (w - 12) * s, (h - 12) * s), radius=10 * s, fill=(0, 0, 0, 255))
    if label:
        size = 70
        while True:   # shrink a long label until it fits inside the rim
            try: font = ImageFont.truetype('segoeuib.ttf', int(size * s))
            except OSError: font = ImageFont.load_default(); break
            if d.textlength(label, font=font) <= (w - 34) * s or size <= 30: break
            size -= 4
        tw = d.textlength(label, font=font)
        d.text(((w * s - tw) / 2, h * s / 2), label, font=font, fill=(255, 255, 255, 255), anchor='lm')
    if mouse:
        cx, cy = w * s / 2, h * s / 2
        mw, mh = 50 * s, 76 * s
        body = (cx - mw / 2, cy - mh / 2, cx + mw / 2, cy + mh / 2)
        d.rounded_rectangle(body, radius=24 * s, outline=(255, 255, 255, 255), width=int(6 * s))
        d.line((cx, cy - mh / 2, cx, cy - mh / 2 + 30 * s), fill=(255, 255, 255, 255), width=int(5 * s))
        d.line((cx - mw / 2, cy - mh / 2 + 30 * s, cx + mw / 2, cy - mh / 2 + 30 * s), fill=(255, 255, 255, 255), width=int(5 * s))
        top = cy - mh / 2
        if mouse in ('left', 'right'):         # filled button
            x0, x1 = (cx - mw / 2 + 4 * s, cx - 2 * s) if mouse == 'left' else (cx + 2 * s, cx + mw / 2 - 4 * s)
            d.rounded_rectangle((x0, top + 4 * s, x1, top + 28 * s), radius=10 * s, fill=(255, 255, 255, 255))
        elif mouse == 'middle':                # wheel
            d.rounded_rectangle((cx - 6 * s, top + 8 * s, cx + 6 * s, top + 26 * s), radius=6 * s, fill=(255, 255, 255, 255))
        elif mouse == 'move':                  # arrows around the mouse
            for ax, ay, ux, uy in ((cx - 48 * s, cy, -1, 0), (cx + 48 * s, cy, 1, 0)):
                d.polygon([(ax + ux * 14 * s, ay), (ax, ay - 12 * s), (ax, ay + 12 * s)], fill=(255, 255, 255, 255))
    return im

def scaled(im, height=H, max_width=None):
    w = max(1, round(im.width * height / im.height))
    if max_width: w = min(w, max_width)
    return im.resize((w, height), Image.LANCZOS)

def cluster(top, bottom):
    """two rows of half-height keys: top centred, bottom side by side (WASD, arrow keys)"""
    half = H // 2
    tops = [scaled(k, half) for k in top]; bots = [scaled(k, half) for k in bottom]
    width = max(sum(k.width for k in tops), sum(k.width for k in bots))
    out = Image.new('RGBA', (width, H), (0, 0, 0, 0))
    x = (width - sum(k.width for k in tops)) // 2
    for k in tops: out.alpha_composite(k, (x, 0)); x += k.width
    x = (width - sum(k.width for k in bots)) // 2
    for k in bots: out.alpha_composite(k, (x, H - half)); x += k.width
    return out

def find_reloaded():
    """Gears of War Reloaded in any Steam library (steamapps/libraryfolders.vdf), else None"""
    import re
    steam = os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'), 'Steam')
    libraries = [steam]
    vdf = os.path.join(steam, 'steamapps', 'libraryfolders.vdf')
    if os.path.exists(vdf):
        libraries += [p.replace('\\\\', '\\') for p in re.findall(r'"path"\s+"([^"]+)"', open(vdf, encoding='utf-8', errors='ignore').read())]
    for lib in libraries:
        d = os.path.join(lib, 'steamapps', 'common', 'Gears of War Reloaded')
        if os.path.isdir(os.path.join(d, 'WarGame')): return d
    return None

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'drawn'
    if mode in ('drawn', 'auto'): install = ''
    elif mode == 'reloaded':
        install = find_reloaded() or ''
        if not install: print('Gears of War: Reloaded not found in a Steam library: drawing the keycaps')
    else: install = mode
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, '..', 'build', 'prompts')
    pages = None
    if os.path.isdir(install):
        try: pages = reloaded_keycaps(install)
        except Exception as e:   # an update of Reloaded changed its packages: the drawn keycaps still work
            print(f'Gears of War: Reloaded keycaps not readable ({e}); drawing them instead')
    source = 'Gears of War: Reloaded keycaps' if pages else 'drawn keycaps'

    def key(name):
        if pages:
            if len(name) == 1:
                x, y, w, h = char_cell(name.upper())
                return pages[0].crop((x, y, x + w, y + h))
            x, y, w, h = NAMED[name]
            return pages[1].crop((x, y, x + w, y + h))
        labels = {'BkSp': 'Bk Sp', 'Up': '\u2191', 'Down': '\u2193', 'Left': '\u2190', 'Right': '\u2192', 'LShift': 'Shift'}
        wide = {'BkSp': 1.8, 'Tab': 1.8, 'Space': 2.6, 'Shift': 1.8, 'LShift': 1.8, 'Enter': 1.8, 'Ctrl': 1.8}
        return keycap(wide.get(name, 1), labels.get(name, name))

    mouse = lambda which: keycap(1, mouse=which)
    # Gears of War 3 button font character -> keyboard/mouse glyph (bindings: runtime/hid/hid.cpp). The font has
    # two styles of shoulder buttons (] ^ _ ` and w x y z), two left sticks (L, M) and the d-pad as single directions
    # (7/m up, 8/q down, 9/n left, 6/o right) or pairs (t up/down, u left/right); Back/Start are < > and : ;
    lmb, rmb = scaled(mouse('left')), scaled(mouse('right'))
    wasd = lambda: cluster([key('W')], [key('A'), key('S'), key('D')])
    glyphs = {
        'A': scaled(key('Space'), max_width=48), 'B': scaled(key('F')), 'X': scaled(key('E')), 'Y': scaled(key('Q')),
        '_': scaled(key('Tab'), max_width=36), 'y': scaled(key('Tab'), max_width=36),
        '`': scaled(key('R')), 'z': scaled(key('R')),
        ']': rmb, 'w': scaled(mouse('right')), '^': lmb, 'x': scaled(mouse('left')),
        'L': wasd(), 'M': wasd(), 'C': scaled(mouse('move')), 'j': scaled(key('C')), 'h': scaled(mouse('middle')),
        'l': cluster([key('Up')], [key('Left'), key('Down'), key('Right')]),
        '7': scaled(key('Up')), 'm': scaled(key('Up')), '8': scaled(key('Down')), 'q': scaled(key('Down')),
        '9': scaled(key('Left')), 'n': scaled(key('Left')), '6': scaled(key('Right')), 'o': scaled(key('Right')),
        't': cluster([key('Up')], [key('Down')]), 'u': cluster([], [key('Left'), key('Right')]),
        '>': scaled(key('Enter'), max_width=40), ';': scaled(key('Enter'), max_width=40),
        '<': scaled(key('BkSp'), max_width=36), ':': scaled(key('BkSp'), max_width=36),
    }
    # compact versions at the button glyphs' own size (about 24x26): the runtime draws these over the controller buttons in
    # the font page itself, so text that Scaleform laid out earlier (menus cache their layout) changes picture at once.
    # Character code + 256 in the strip.
    def compact(ch, im):
        short = {'A': 'Spc', '>': 'Ent', ';': 'Ent', '_': 'Tab', 'y': 'Tab', '<': 'Bk', ':': 'Bk'}
        if ch in short: return scaled(keycap(1, short[ch]))
        if im.width <= 28: return im
        return im.resize((26, H), Image.LANCZOS)   # key clusters, squeezed to a button's width
    for ch, im in list(glyphs.items()):
        glyphs[chr(ord(ch) + 256)] = compact(ch, im)
    # pack into one strip image (the runtime repacks into the game's font page)
    width = sum(g.width + 2 for g in glyphs.values())
    atlas = Image.new('RGBA', (width, H), (0, 0, 0, 0))
    rects, x = {}, 0
    for ch, g in glyphs.items():
        atlas.alpha_composite(g, (x, 0)); rects[ord(ch)] = [x, 0, g.width, H]; x += g.width + 2
    os.makedirs(out, exist_ok=True)
    atlas.save(os.path.join(out, 'kbm_glyphs.png'))
    # raw RGBA too, so the runtime needs no PNG decoder
    open(os.path.join(out, 'kbm_glyphs.rgba'), 'wb').write(np.array(atlas).tobytes())
    json.dump({'source': source, 'width': atlas.width, 'height': atlas.height, 'glyphs': rects},
              open(os.path.join(out, 'kbm_glyphs.json'), 'w'), indent=1)
    # plain index for the runtime: "<width> <height>", then "<character code> <x> <y> <w> <h>" per glyph
    with open(os.path.join(out, 'kbm_glyphs.txt'), 'w') as f:
        f.write(f'{atlas.width} {atlas.height}\n')
        for code, (x, y, w, h) in rects.items(): f.write(f'{code} {x} {y} {w} {h}\n')
    print(f'{len(glyphs)} glyphs from {source} -> {out}')

if __name__ == '__main__':
    main()
