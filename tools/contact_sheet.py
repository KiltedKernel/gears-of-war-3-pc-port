# Contact sheet: screenshots (shot_<N>s.png) of one or more folders side by side, a row per time, scaled down, with
# labels; for reviewing scripted runs (tools/mp_test.ps1, playtest.ps1) at a glance.
# usage: python tools/contact_sheet.py out.png build/mp/host/shots build/mp/join/shots [--width 480]
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw


def main():
    args = sys.argv[1:]
    width = 480
    if '--width' in args:
        i = args.index('--width')
        width = int(args[i + 1])
        del args[i:i + 2]
    out, folders = Path(args[0]), [Path(a) for a in args[1:]]
    # shot_050s.png / shot_235s.png: time -> file, per folder
    shots = [{int(m.group(1)): p for p in f.glob('shot_*s.png') if (m := re.match(r'shot_(\d+)s\.png', p.name))} for f in folders]
    times = sorted({t for s in shots for t in s})
    if not times:
        print('no screenshots')
        return
    height = width * 9 // 16
    sheet = Image.new('RGB', (width * len(folders), (height + 18) * len(times)), 'black')
    draw = ImageDraw.Draw(sheet)
    for row, t in enumerate(times):
        for col, f in enumerate(folders):
            p = shots[col].get(t)
            y = row * (height + 18)
            draw.text((col * width + 4, y + 2), f'{f.parent.name} {t}s', fill='white')
            if p:
                sheet.paste(Image.open(p).convert('RGB').resize((width, height)), (col * width, y + 18))
    sheet.save(out)
    print(f'{out}: {len(times)} rows x {len(folders)} columns')


if __name__ == '__main__':
    main()
