"""Campaign coverage: boot each chapter directly and check it loads and plays.

usage: python tools/chapter_test.py [chapters] [--seconds N] [--out DIR]
  chapters: "0-30" (default), "7", "7,8,14-18"
For each chapter the game starts on GearGame_P?game=geargamecontent.GearGameSP?chapter=N (GOW3_COMMAND_LINE: the URL
the menu's Select Chapter builds, without the menus) with a fresh test profile and no mods (tools/playtest.ps1), plays
generic input (walk, look around, fire, cover) and takes screenshots. Per chapter it records: crash or not, the
persistent map that loaded, frame rates, black or frozen screenshots. Output: <out>/chNN.png (contact sheet),
<out>/report.md and report.json. A chapter takes about --seconds (default 150): all 31 take about 80 minutes.
"""
import argparse, json, os, re, shutil, subprocess, sys, time
from PIL import Image, ImageDraw, ImageStat

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
BUILD = os.path.join(ROOT, 'build')
URL = 'GearGame_P?game=geargamecontent.GearGameSP?chapter={}?listen'


def parse_chapters(text):
    out = []
    for part in text.split(','):
        if '-' in part:
            a, b = part.split('-'); out += range(int(a), int(b) + 1)
        elif part.strip():
            out.append(int(part))
    return out


def input_script(seconds):
    """generic play: from 30 s, every 12 s skip a cutscene (Start skips one; in play it pauses, and B closes the pause
    menu again), walk forward, turn, fire, take cover"""
    lines = ['# chapter_test.py: generic play (skip cutscenes, walk, look, fire, cover)', '28 capture']
    t = 30.0
    while t < seconds - 8:
        lines += [f'{t:.1f} pad 0010 200', f'{t + 1.5:.1f} pad 2000 150',
                  f'{t + 3:.1f} key 57 3000', f'{t + 6.5:.1f} mouse 400 0 600', f'{t + 8:.1f} trig R 900',
                  f'{t + 9.5:.1f} key 20 150', f'{t + 10.5:.1f} mouse -250 40 500']
        t += 12
    return '\n'.join(lines) + '\n'


def shot_times(seconds):
    return [s for s in (30, 50, 70, 90, 110, 130, 150, 170, 190) if s < seconds - 2]


def analyse(shots):
    """per screenshot: mean brightness; frozen = identical to the previous one"""
    info, prev = [], None
    for path in shots:
        im = Image.open(path).convert('RGB').resize((160, 90))
        mean = sum(ImageStat.Stat(im).mean) / 3
        same = prev is not None and list(im.getdata()) == list(prev.getdata())
        info.append({'shot': os.path.basename(path), 'mean': round(mean, 1), 'black': mean < 6, 'same_as_previous': same})
        prev = im
    return info


def contact_sheet(shots, title, path):
    if not shots: return
    w, h = 320, 180
    sheet = Image.new('RGB', (w * min(len(shots), 4), 22 + h * ((len(shots) + 3) // 4)), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    d.text((6, 5), title, fill=(255, 255, 255))
    for i, s in enumerate(shots):
        sheet.paste(Image.open(s).convert('RGB').resize((w, h)), ((i % 4) * w, 22 + (i // 4) * h))
        d.text(((i % 4) * w + 4, 24 + (i // 4) * h), os.path.basename(s)[5:9], fill=(255, 255, 0))
    sheet.save(path)


def run_chapter(n, seconds, out):
    script = os.path.join(out, 'input.txt')
    open(script, 'w').write(input_script(seconds))
    times = shot_times(seconds)
    env = dict(os.environ, GOW3_COMMAND_LINE=URL.format(n))
    start = time.time()
    r = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', os.path.join(ROOT, 'tools', 'playtest.ps1'),
                        '-Script', script, '-Seconds', str(seconds), '-Fresh', '-Shots', ','.join(map(str, times))],
                       capture_output=True, text=True, env=env)
    status = (r.stdout.strip().splitlines() or ['?'])[-1]
    log = open(os.path.join(BUILD, 'gow3.log'), encoding='utf8', errors='replace').read()
    shutil.copy(os.path.join(BUILD, 'gow3.log'), os.path.join(out, f'ch{n:02d}.log'))
    maps = []
    for m in re.findall(r'NtCreateFile: \S+\\CookedXbox360\\(\w+_P)\.xxx', log, re.I):
        if m.lower() not in [x.lower() for x in maps] and m.lower() != 'geargame_p': maps.append(m)
    fps = [float(x) for x in re.findall(r'video: ([\d.]+) fps', log)]
    crash = [l for l in log.splitlines() if 'CRASH' in l][:3]
    shots = sorted(os.path.join(BUILD, 'shots', f) for f in os.listdir(os.path.join(BUILD, 'shots'))) if os.path.isdir(os.path.join(BUILD, 'shots')) else []
    info = analyse(shots)
    sheet = os.path.join(out, f'ch{n:02d}.png')
    contact_sheet(shots, f'chapter {n}: {", ".join(maps) or "no map"}  ({status})', sheet)
    late = info[-3:]
    result = {
        'chapter': n, 'status': status, 'seconds': round(time.time() - start), 'maps': maps, 'crash': crash,
        'fps_min': min(fps) if fps else None, 'fps_last': fps[-1] if fps else None, 'shots': info,
        'exited_early': status.startswith('exited'),
        'black_at_end': bool(late) and all(s['black'] for s in late),
        'frozen_at_end': len(late) >= 2 and all(s['same_as_previous'] for s in late[1:]),
    }
    result['ok'] = bool(maps) and not crash and not result['exited_early'] and not result['black_at_end'] and not result['frozen_at_end']
    return result


def write_report(results, out):
    json.dump(results, open(os.path.join(out, 'report.json'), 'w'), indent=1)
    rows = ['| chapter | result | map | fps (min / last) | notes |', '|---|---|---|---|---|']
    for r in results:
        notes = []
        if r['crash']: notes.append('crash: ' + r['crash'][0].split('CRASH:')[-1].strip()[:60])
        if r['exited_early']: notes.append(r['status'])
        if r['black_at_end']: notes.append('black screen')
        if r['frozen_at_end']: notes.append('frozen picture')
        if not r['maps']: notes.append('no chapter map loaded')
        fps = f"{r['fps_min']:.0f} / {r['fps_last']:.0f}" if r['fps_min'] is not None else '-'
        rows.append(f"| {r['chapter']} | {'ok' if r['ok'] else 'FAIL'} | {', '.join(r['maps'])} | {fps} | {'; '.join(notes)} |")
    open(os.path.join(out, 'report.md'), 'w').write('# Chapter coverage\n\n' + '\n'.join(rows) + '\n\nSheets: chNN.png\n')
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('chapters', nargs='?', default='0-30')
    ap.add_argument('--seconds', type=int, default=150)
    ap.add_argument('--out', default=os.path.join(BUILD, 'coverage'))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    results = []
    for n in parse_chapters(a.chapters):
        r = run_chapter(n, a.seconds, a.out)
        results.append(r)
        print(f"chapter {n:2}: {'ok  ' if r['ok'] else 'FAIL'} {', '.join(r['maps']) or '-'} {r['status']}"
              f"{' CRASH' if r['crash'] else ''}{' black' if r['black_at_end'] else ''}{' frozen' if r['frozen_at_end'] else ''}", flush=True)
        write_report(results, a.out)
    print('\n'.join(write_report(results, a.out)))
    return 0 if all(r['ok'] for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
