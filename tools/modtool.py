"""modtool: make, check, share and manage mods for the Gears of War 3 PC port (docs/MODDING.md).

  python -P tools/modtool.py mods                       installed mods, their files, conflicts (as the game sees them)
  python -P tools/modtool.py new-mod <folder> [--name "Title"] [--author "You"] [--priority N] [--side everyone|host|client]
  python -P tools/modtool.py check <folder>             manifest, files and every package in the mod read back
  python -P tools/modtool.py enable|disable <mod>...    switch installed mods on or off
  python -P tools/modtool.py pack <mod> [--out x.zip]   a mod as one .zip to share (checked first)
  python -P tools/modtool.py install <x.zip> [--replace] a shared mod into the mods folder (also Xbox 360 mods: their
                                                        files without a mod.ini; one is made)

Locations (override with options or environment variables):
  --mods     the game's mods folder       (GOW3_MODS,    default build/mods next to tools/)
  --g3       Gears 3's disc files         (GOW3_DISC,    default ../gow3-decomp/disc)

The rules are those of runtime/kernel/mod_manifest.h (the game, the launcher and the server use that file): keep
the two in step.
"""
import re, argparse, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPO = os.path.dirname(HERE)

SIDES = ('everyone', 'host', 'client')
GAMES = {'gears3': 'Gears of War 3', 'gears2': 'Gears of War 2', 'gears1': 'Gears of War'}


def local_game_disc():
    """the disc folder this install uses (tools/local.cmd, written by tools/setup.ps1), or None"""
    p = os.path.join(REPO, 'tools', 'local.cmd')
    if os.path.exists(p):
        for line in open(p, encoding='ascii', errors='replace'):
            m = re.match(r'set "GOW3_GAME=(.*)"', line.strip())
            if m: return os.path.join(m.group(1), 'disc')
    return None

def paths(args):
    mods = args.mods or os.environ.get('GOW3_MODS') or os.path.join(REPO, 'build', 'mods')
    g3 = args.g3 or os.environ.get('GOW3_DISC') or local_game_disc() or os.path.join(REPO, '..', 'gow3-decomp', 'disc')
    return os.path.abspath(mods), os.path.abspath(g3)


def lower(s):
    """as modinfo::Lower: ASCII letters only, / -> \\"""
    return ''.join(chr(ord(c) + 32) if 'A' <= c <= 'Z' else c for c in s).replace('/', '\\')


def read_manifest(folder):
    m = {'name': os.path.basename(folder), 'version': '', 'author': '', 'description': '', 'priority': 0, 'enabled': True,
         'side': 'everyone', 'requires': [], 'game': 'gears3', 'problem': '', 'dir': folder, 'loose': False, 'warnings': []}
    ini = os.path.join(folder, 'mod.ini')
    if not os.path.isfile(ini): return None
    for line in open(ini, encoding='utf-8-sig', errors='replace'):
        line = line.strip(' \t\r\n')
        if not line or line[0] in ';#[' or '=' not in line: continue
        k, v = (x.strip(' \t\r\n') for x in line.split('=', 1)); k = lower(k)
        if k == 'priority':
            try: m[k] = int(v)
            except ValueError: raise SystemExit(f'{ini}: priority must be a whole number, not "{v}"')
        elif k == 'enabled': m[k] = lower(v) not in ('false', '0', 'no')
        elif k == 'side':
            s = lower(v)
            if s == 'server': s = 'host'
            if s not in SIDES: m['warnings'].append(f'side = {v}: not one of {", ".join(SIDES)} (taken as everyone)'); s = 'everyone'
            m['side'] = s
        elif k == 'game': m['game'] = lower(v) or 'gears3'
        elif k == 'requires': m['requires'] = [r.strip(' \t\r\n') for r in v.split(',') if r.strip(' \t\r\n')]
        elif k in ('name', 'version', 'author', 'description'): m[k] = v
    return m


def installed(mods_dir):
    """mods in load order (priority, then name; files directly in mods\\ as '(loose files)'), with each one's problem
    (made for another game, a required mod missing or off) as runtime/kernel/mod_manifest.h Scan"""
    out, loose = [], False
    if not os.path.isdir(mods_dir): return out
    for entry in sorted(os.listdir(mods_dir)):
        path = os.path.join(mods_dir, entry)
        m = read_manifest(path) if os.path.isdir(path) else None
        if m: out.append(m)
        else: loose = True
    if loose:
        out.append({'name': '(loose files)', 'version': '', 'author': '', 'description': '', 'priority': 0, 'enabled': True,
                    'side': 'everyone', 'requires': [], 'game': 'gears3', 'problem': '', 'dir': mods_dir, 'loose': True, 'warnings': []})
    out.sort(key=lambda m: (m['priority'], m['name'].encode('utf-8')))
    for m in out:
        if m['game'] != 'gears3': m['problem'] = 'made for ' + GAMES.get(m['game'], m['game'])
    active = lambda m: m['enabled'] and not m['problem']
    changed = True
    while changed:
        changed = False
        for m in out:
            if not active(m): continue
            for r in m['requires']:
                o = next((o for o in out if lower(o['name']) == lower(r) or lower(os.path.basename(o['dir'])) == lower(r)), None)
                if o is None: m['problem'] = f'needs the mod {r}, which is not installed'
                elif not active(o): m['problem'] = f'needs the mod {r}, which is off'
                if m['problem']: changed = True; break
    return out


def game_path(rel):
    """where a mod's file goes in the game (modinfo::GamePath), or None: mod.ini, config fragments, TOCs, other files"""
    rel = lower(rel)
    if rel == 'mod.ini' or rel.startswith('config\\'): return None
    g = 0 if rel.startswith('geargame\\') else rel.find('\\geargame\\')
    if g >= 0: rel = rel[g + (1 if g else 0):]
    else:
        name = rel.rsplit('\\', 1)[-1]
        ext = name.rsplit('.', 1)[1] if '.' in name else ''
        if ext in ('xxx', 'upk', 'tfc') or (ext == 'bin' and name.startswith('coalesced_')): rel = 'geargame\\cookedxbox360\\' + name
        elif ext == 'bik': rel = 'geargame\\movies\\' + name
        else: return None
    if rel.startswith('geargame\\xbox360toc') and rel.endswith('.txt'): return None
    return rel


def mod_files(m, mods_dir=None, ignored=None):
    """{game path: file} of a mod (the last of two files for one game path wins, as in the game). ignored: a list that
    receives the files the game does not use"""
    files = []
    for root, dirs, names in os.walk(m['dir']):
        if m.get('loose') and os.path.abspath(root) == os.path.abspath(m['dir']):
            dirs[:] = [d for d in dirs if not os.path.isfile(os.path.join(root, d, 'mod.ini'))]
        for n in names:
            full = os.path.join(root, n)
            rel = os.path.relpath(full, m['dir'])
            g = game_path(rel)
            if g: files.append((g, full))
            elif ignored is not None and lower(rel) != 'mod.ini' and not lower(rel).startswith('config\\'): ignored.append(rel)
    files.sort()
    return files


def fragments(m):
    if m.get('loose'): return []
    d = os.path.join(m['dir'], 'config')
    if not os.path.isdir(d): return []
    return sorted(os.path.join(d, f) for f in os.listdir(d) if lower(os.path.splitext(f)[1]) == '.ini' and os.path.isfile(os.path.join(d, f)))


def multiplayer(ms):
    """(fingerprint, summary) of the 'everyone' mods that are on, as modinfo::ForMultiplayer"""
    text, summary = b'', []
    for m in ms:
        if not m['enabled'] or m['problem'] or m['side'] != 'everyone': continue
        text += (m['name'] + '|' + m['version'] + '|').encode('utf-8')
        for g, f in mod_files(m): text += f'{g}:{os.path.getsize(f)};'.encode('mbcs' if os.name == 'nt' else 'utf-8', 'replace')
        for f in fragments(m): text += f'config\\{lower(os.path.basename(f))}:{os.path.getsize(f)};'.encode('utf-8')
        text += b'\n'
        summary.append(m['name'] + (' ' + m['version'] if m['version'] else ''))
    if not text: return 0, ''
    h = 0xCBF29CE484222325
    for c in text: h = ((h ^ c) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h or 1, ', '.join(summary)


def cmd_mods(args):
    mods_dir, _ = paths(args)
    ms = installed(mods_dir)
    if not ms: print(f'no mods in {mods_dir}'); return
    owners = {}
    for m in ms:
        state = '  [disabled]' if not m['enabled'] else f"  [not loaded: {m['problem']}]" if m['problem'] else ''
        print(f"{m['name']} {m['version']}{' by ' + m['author'] if m['author'] else ''}  (priority {m['priority']}, {m['side']}){state}")
        if m['description']: print(f"    {m['description']}")
        if not m['enabled'] or m['problem']: continue
        for low, path in mod_files(m):
            print(f'    {low}  ({os.path.getsize(path):,} bytes)')
            owners.setdefault(low, []).append(m['name'])
        for f in fragments(m): print(f'    config\\{os.path.basename(f)}  (configuration fragment)')
    for low, who in owners.items():
        if len(who) > 1: print(f"conflict: {low}: {', '.join(who)} -> {who[-1]} wins")
    fp, summary = multiplayer(ms)
    print(f'multiplayer: sessions need {summary} (fingerprint {fp:016x})' if fp else
          'multiplayer: no mod that everyone needs (sessions without mods)')


def cmd_new(args):
    mods_dir, _ = paths(args)
    folder = os.path.join(mods_dir, args.folder)
    if os.path.exists(os.path.join(folder, 'mod.ini')): raise SystemExit(f'{folder} already has a mod.ini')
    os.makedirs(os.path.join(folder, 'GearGame', 'CookedXbox360'), exist_ok=True)
    os.makedirs(os.path.join(folder, 'config'), exist_ok=True)
    with open(os.path.join(folder, 'mod.ini'), 'w', encoding='utf-8') as f:
        f.write('; A mod for the Gears of War 3 PC port (docs/MODDING.md). Files under this folder replace the\n'
                '; game\'s files at the same path (GearGame\\CookedXbox360\\<package>.xxx ...); new packages are added;\n'
                '; config\\*.ini fragments change the game\'s configuration.\n'
                '; side: everyone (every player of a multiplayer session needs the mod), host (rules of matches you\n'
                '; host), client (only what you see and hear).\n'
                '[mod]\n'
                f'name = {args.name or args.folder}\nversion = 0.1\nauthor = {args.author or ""}\n'
                'description = \n'
                f'priority = {args.priority}\nenabled = true\nside = {args.side}\n')
    print(f'created {folder} (put files under {os.path.join(folder, "GearGame", "CookedXbox360")}, '
          f'configuration fragments in {os.path.join(folder, "config")})')


def check_fragment(path):
    """problems in a config fragment (the rules of runtime/kernel/mod_config.cpp)"""
    errs, have_file, have_section = [], False, False
    for n, raw in enumerate(open(path, encoding='utf-8-sig', errors='replace'), 1):
        line = raw.strip()
        if not line or line.startswith(';') or (line.startswith('#') and not line.startswith('#####')): continue
        if line.startswith('#####'):
            have_file, have_section = bool(line[5:].strip()), False
            if not have_file: errs.append(f'line {n}: ##### needs an ini file name')
            continue
        if line.startswith('[') and line.endswith(']'):
            if not have_file: errs.append(f'line {n}: section before any ##### <ini file> line')
            have_section = True; continue
        if '=' not in line or not have_section: errs.append(f'line {n}: expected [section] or key=value')
    return errs


def cmd_check(args):
    import upk
    mods_dir, g3 = paths(args)
    folder = args.folder if os.path.isdir(args.folder) else os.path.join(mods_dir, args.folder)
    m = read_manifest(folder)
    if not m: raise SystemExit(f'{folder}: no mod.ini (create one with: modtool.py new-mod {os.path.basename(folder)})')
    print(f"{m['name']} {m['version']} (priority {m['priority']}, {m['side']}){'' if m['enabled'] else ' [disabled]'}")
    problems = 0
    for w in m['warnings']: print(f'  {w}'); problems += 1
    if m['game'] != 'gears3': print(f"  made for {GAMES.get(m['game'], m['game'])}: the Gears of War 3 port does not load it"); problems += 1
    if m['requires']:
        names = {lower(o['name']) for o in installed(mods_dir)} | {lower(os.path.basename(o['dir'])) for o in installed(mods_dir)}
        for r in m['requires']:
            print(f"  requires {r}: {'installed' if lower(r) in names else 'NOT installed (the game turns this mod off without it)'}")
    ignored = []
    files = mod_files(m, mods_dir, ignored)
    for low, path in files:
        disc = os.path.join(g3, low)
        kind = 'replaces' if os.path.isfile(disc) else 'adds'
        rel = lower(os.path.relpath(path, folder))
        moved = '' if rel == low else f' (from {os.path.relpath(path, folder)})'
        if low.endswith(('.xxx', '.upk')):
            try:
                p = upk.Package(path)
                note = f'package version {p.version}, {len(p.exports)} exports'
                if p.version != 828: note += '  <- not a Gears 3 package (version 828)'; problems += 1
            except Exception as ex:
                note = f'cannot be read: {ex}'; problems += 1
        else:
            note = f'{os.path.getsize(path):,} bytes'
        print(f'  {kind} {low}{moved}: {note}')
    for f in fragments(m):
        errs = check_fragment(f)
        print(f"  config {os.path.basename(f)}: {'fragment ok' if not errs else '; '.join(errs[:3])}")
        problems += len(errs)
    for rel in ignored:
        low = lower(rel)
        if low.endswith(('.xex', '.patch.toml', '.patch')) or low.endswith('default.xex'):
            print(f'  {rel}: code patches cannot apply to the recompiled game (its code is C++ built on your PC); ignored')
        elif not low.endswith(('.txt', '.md', '.png', '.jpg', '.url', '.pdf')):
            print(f'  {rel}: not a game file the port knows where to put; ignored')
    if not files and not fragments(m): print('  (no game files and no configuration fragments)')
    print('ok' if not problems else f'{problems} problem(s)')
    return 1 if problems else 0


def find_mod(mods_dir, key):
    """an installed mod by folder name (case-insensitive) or its mod.ini name"""
    for m in installed(mods_dir):
        if m.get('loose'): continue
        if lower(os.path.basename(m['dir'])) == lower(key) or lower(m['name']) == lower(key): return m
    raise SystemExit(f'no installed mod "{key}" (python -P tools/modtool.py mods lists them)')


def set_enabled(m, on):
    ini = os.path.join(m['dir'], 'mod.ini')
    lines = [l for l in open(ini, encoding='utf-8-sig', errors='replace').read().splitlines()
             if not (l.strip().lower().startswith('enabled') and '=' in l)]
    lines.append(f'enabled = {"true" if on else "false"}')
    open(ini, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines) + '\n')


def cmd_enable(args, on=True):
    mods_dir, _ = paths(args)
    for key in args.mod:
        m = find_mod(mods_dir, key); set_enabled(m, on)
        print(f'{m["name"]}: {"enabled" if on else "disabled"}')
    for m in installed(mods_dir):
        if m['enabled'] and m['problem']: print(f"note: {m['name']} is not loaded: {m['problem']}")


def cmd_pack(args):
    """a mod as one .zip for sharing: its folder (mod.ini and files), checked first"""
    import zipfile
    mods_dir, _ = paths(args)
    m = find_mod(mods_dir, args.mod)
    args.folder = m['dir']
    if cmd_check(args): raise SystemExit('not packed: the check failed')
    folder = os.path.basename(m['dir'])
    out = os.path.abspath(args.out or os.path.join(mods_dir, '..', f'{folder}-{m["version"] or "0"}.zip'))
    n = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(m['dir']):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                if f.startswith('.'): continue
                full = os.path.join(root, f)
                z.write(full, os.path.join(folder, os.path.relpath(full, m['dir'])).replace(os.sep, '/')); n += 1
    print(f'{m["name"]}: {n} files -> {out} ({os.path.getsize(out) >> 20} MB)')


def cmd_install(args):
    """a mod .zip into the mods folder: one folder with a mod.ini (as pack makes it); or, as mods for the Xbox 360 are
    shared, game files (bare, or in the game's folders) without a mod.ini, which go into a new folder named after the
    zip with a mod.ini made for them"""
    import zipfile
    mods_dir, _ = paths(args)
    with zipfile.ZipFile(args.zip) as z:
        names = [n for n in z.namelist() if not n.endswith('/')]
        if not names: raise SystemExit(f'{args.zip}: empty')
        for n in names:                                  # nothing outside the mod folder
            if n.startswith('/') or '..' in n.split('/') or ':' in n or '\\' in n:
                raise SystemExit(f'{args.zip}: unsafe path "{n}": not installed')
        tops = {n.split('/', 1)[0] for n in names}
        one_folder = len(tops) == 1 and all('/' in n for n in names)
        top = next(iter(tops))
        if one_folder and f'{top}/mod.ini' in names:
            dest, base, wrap, has_ini = os.path.join(mods_dir, top), mods_dir, False, True
        else:
            has_ini = 'mod.ini' in names
            if not has_ini and not any(game_path(n) for n in names):
                raise SystemExit(f'{args.zip}: no mod.ini and no game files (packages, Coalesced files, movies): not a mod')
            folder = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', os.path.splitext(os.path.basename(args.zip))[0]).rstrip('. ') or 'Mod'
            dest, base, wrap = os.path.join(mods_dir, folder), os.path.join(mods_dir, folder), True
        if os.path.exists(dest) and not args.replace:
            raise SystemExit(f'{dest} exists (install with --replace to overwrite it)')
        os.makedirs(base, exist_ok=True)
        z.extractall(base, members=names)
    if not has_ini:
        with open(os.path.join(dest, 'mod.ini'), 'w', encoding='utf-8') as f:
            f.write(f'[mod]\nname = {os.path.basename(dest)}\nversion = \nauthor = \n'
                    f'description = Installed from {os.path.basename(args.zip)} (it had no mod.ini)\n'
                    'priority = 0\nenabled = true\nside = everyone\n')
    m = read_manifest(dest)
    print(f'installed {m["name"]} {m["version"]} -> {dest}{"" if has_ini else " (made a mod.ini for it)"}')
    ignored = []
    files = mod_files(m, None, ignored)
    for g, f in files:
        rel = lower(os.path.relpath(f, dest))
        if rel != g: print(f'  {os.path.relpath(f, dest)} -> {g}')
    if not files and not fragments(m): print('  note: no game files and no configuration fragments in it')


def main():
    ap = argparse.ArgumentParser(prog='modtool', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--mods'); ap.add_argument('--g3')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('mods', help='list installed mods, their files and conflicts')
    n = sub.add_parser('new-mod', help='create a mod folder with a mod.ini')
    n.add_argument('folder'); n.add_argument('--name'); n.add_argument('--author'); n.add_argument('--priority', type=int, default=0)
    n.add_argument('--side', choices=SIDES, default='everyone')
    c = sub.add_parser('check', help="read back a mod's manifest and packages")
    c.add_argument('folder')
    e = sub.add_parser('enable', help='switch mods on'); e.add_argument('mod', nargs='+')
    d = sub.add_parser('disable', help='switch mods off (they stay installed)'); d.add_argument('mod', nargs='+')
    k = sub.add_parser('pack', help='a mod as one .zip to share (checked first)'); k.add_argument('mod'); k.add_argument('--out')
    i = sub.add_parser('install', help='a mod .zip into the mods folder'); i.add_argument('zip'); i.add_argument('--replace', action='store_true')
    args = ap.parse_args()
    return {'mods': cmd_mods, 'new-mod': cmd_new, 'check': cmd_check,
            'enable': cmd_enable, 'disable': lambda a: cmd_enable(a, False), 'pack': cmd_pack,
            'install': cmd_install}[args.cmd](args) or 0


if __name__ == '__main__':
    sys.exit(main())
