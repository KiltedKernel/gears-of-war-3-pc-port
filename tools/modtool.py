"""modtool: make, check, share and manage mods for the Gears of War 3 PC port (docs/MODDING.md).

  python -P tools/modtool.py mods                       installed mods, their files, conflicts (as the game sees them)
  python -P tools/modtool.py new-mod <folder> [--name "Title"] [--author "You"] [--priority N]
  python -P tools/modtool.py check <folder>             manifest and every package in the mod read back
  python -P tools/modtool.py enable|disable <mod>...    switch installed mods on or off
  python -P tools/modtool.py pack <mod> [--out x.zip]   a mod as one .zip to share (checked first)
  python -P tools/modtool.py install <x.zip> [--replace] a shared mod into the mods folder

Locations (override with options or environment variables):
  --mods     the game's mods folder       (GOW3_MODS,    default build/mods next to tools/)
  --g3       Gears 3's disc files         (GOW3_DISC,    default ../gow3-decomp/disc)
"""
import argparse, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPO = os.path.dirname(HERE)


def paths(args):
    mods = args.mods or os.environ.get('GOW3_MODS') or os.path.join(REPO, 'build', 'mods')
    g3 = args.g3 or os.environ.get('GOW3_DISC') or os.path.join(REPO, '..', 'gow3-decomp', 'disc')
    return os.path.abspath(mods), os.path.abspath(g3)


def read_manifest(folder):
    m = {'name': os.path.basename(folder), 'version': '', 'author': '', 'description': '', 'priority': 0, 'enabled': True}
    ini = os.path.join(folder, 'mod.ini')
    if not os.path.isfile(ini): return None
    for line in open(ini, encoding='utf-8', errors='replace'):
        line = line.strip()
        if not line or line[0] in ';#[' or '=' not in line: continue
        k, v = (x.strip() for x in line.split('=', 1)); k = k.lower()
        if k == 'priority':
            try: m[k] = int(v)
            except ValueError: raise SystemExit(f'{ini}: priority must be a whole number, not "{v}"')
        elif k == 'enabled': m[k] = v.lower() not in ('false', '0', 'no')
        elif k in m: m[k] = v
    return m


def installed(mods_dir):
    """mods in load order (as kernel/mods.cpp: priority, then name; files directly in mods\\ as '(loose files)')"""
    out, loose = [], False
    if not os.path.isdir(mods_dir): return out
    for entry in sorted(os.listdir(mods_dir)):
        path = os.path.join(mods_dir, entry)
        m = read_manifest(path) if os.path.isdir(path) else None
        if m: m['dir'] = path; out.append(m)
        else: loose = True
    if loose:
        out.append({'name': '(loose files)', 'version': '', 'author': '', 'description': '', 'priority': 0, 'enabled': True,
                    'dir': mods_dir, 'loose': True})
    out.sort(key=lambda m: (m['priority'], m['name']))
    return out


def mod_files(m, mods_dir):
    files = {}
    for root, dirs, names in os.walk(m['dir']):
        if m.get('loose') and os.path.abspath(root) == os.path.abspath(mods_dir):
            dirs[:] = [d for d in dirs if not os.path.isfile(os.path.join(root, d, 'mod.ini'))]
        for n in names:
            rel = os.path.relpath(os.path.join(root, n), m['dir'])
            low = rel.lower().replace('/', '\\')
            if low == 'mod.ini' or (low.startswith('geargame\\xbox360toc') and low.endswith('.txt')): continue
            files[low] = os.path.join(root, n)
    return files


def cmd_mods(args):
    mods_dir, _ = paths(args)
    ms = installed(mods_dir)
    if not ms: print(f'no mods in {mods_dir}'); return
    owners = {}
    for m in ms:
        state = '' if m['enabled'] else '  [disabled]'
        print(f"{m['name']} {m['version']}{' by ' + m['author'] if m['author'] else ''}  (priority {m['priority']}){state}")
        if m['description']: print(f"    {m['description']}")
        if not m['enabled']: continue
        files = mod_files(m, mods_dir)
        for low, path in sorted(files.items()):
            print(f'    {low}  ({os.path.getsize(path):,} bytes)')
            owners.setdefault(low, []).append(m['name'])
    for low, who in owners.items():
        if len(who) > 1: print(f"conflict: {low}: {', '.join(who)} -> {who[-1]} wins")


def cmd_new(args):
    mods_dir, _ = paths(args)
    folder = os.path.join(mods_dir, args.folder)
    if os.path.exists(os.path.join(folder, 'mod.ini')): raise SystemExit(f'{folder} already has a mod.ini')
    os.makedirs(os.path.join(folder, 'GearGame', 'CookedXbox360'), exist_ok=True)
    with open(os.path.join(folder, 'mod.ini'), 'w', encoding='utf-8') as f:
        f.write('; A mod for the Gears of War 3 PC port (docs/MODDING.md). Files under this folder replace the\n'
                '; game\'s files at the same path (GearGame\\CookedXbox360\\<package>.xxx ...); new packages are added.\n'
                '[mod]\n'
                f'name = {args.name or args.folder}\nversion = 0.1\nauthor = {args.author or ""}\n'
                'description = \n'
                f'priority = {args.priority}\nenabled = true\n')
    print(f'created {folder} (put files under {os.path.join(folder, "GearGame", "CookedXbox360")})')


def cmd_check(args):
    import upk
    mods_dir, g3 = paths(args)
    folder = args.folder if os.path.isdir(args.folder) else os.path.join(mods_dir, args.folder)
    m = read_manifest(folder)
    if not m: raise SystemExit(f'{folder}: no mod.ini (create one with: modtool.py new-mod {os.path.basename(folder)})')
    print(f"{m['name']} {m['version']} (priority {m['priority']}){'' if m['enabled'] else ' [disabled]'}")
    m['dir'] = folder
    problems = 0
    for low, path in sorted(mod_files(m, mods_dir).items()):
        disc = os.path.join(g3, low)
        kind = 'replaces' if os.path.isfile(disc) else 'adds'
        if low.endswith(('.xxx', '.upk')):
            try:
                p = upk.Package(path)
                note = f'package version {p.version}, {len(p.exports)} exports'
                if p.version != 828: note += '  <- not a Gears 3 package (version 828)'; problems += 1
            except Exception as ex:
                note = f'cannot be read: {ex}'; problems += 1
        else:
            note = f'{os.path.getsize(path):,} bytes'
        print(f'  {kind} {low}: {note}')
    print('ok' if not problems else f'{problems} problem(s)')
    return 1 if problems else 0


def find_mod(mods_dir, key):
    """an installed mod by folder name (case-insensitive) or its mod.ini name"""
    for m in installed(mods_dir):
        if m.get('loose'): continue
        if os.path.basename(m['dir']).lower() == key.lower() or m['name'].lower() == key.lower(): return m
    raise SystemExit(f'no installed mod "{key}" (python -P tools/modtool.py mods lists them)')


def set_enabled(m, on):
    ini = os.path.join(m['dir'], 'mod.ini')
    lines = [l for l in open(ini, encoding='utf-8', errors='replace').read().splitlines()
             if not (l.strip().lower().startswith('enabled') and '=' in l)]
    lines.append(f'enabled = {"true" if on else "false"}')
    open(ini, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines) + '\n')


def cmd_enable(args, on=True):
    mods_dir, _ = paths(args)
    for key in args.mod:
        m = find_mod(mods_dir, key); set_enabled(m, on)
        print(f'{m["name"]}: {"enabled" if on else "disabled"}')


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
    """a mod .zip (as made by pack: one folder with a mod.ini) into the mods folder"""
    import zipfile
    mods_dir, _ = paths(args)
    with zipfile.ZipFile(args.zip) as z:
        names = [n for n in z.namelist() if not n.endswith('/')]
        tops = {n.split('/', 1)[0] for n in names}
        if len(tops) != 1: raise SystemExit(f'{args.zip}: expected one mod folder in the zip, found {sorted(tops)}')
        top = tops.pop()
        for n in names:                                  # nothing outside the mod folder
            if n.startswith('/') or '..' in n.split('/') or ':' in n or '\\' in n:
                raise SystemExit(f'{args.zip}: unsafe path "{n}": not installed')
        if f'{top}/mod.ini' not in names: raise SystemExit(f'{args.zip}: no {top}/mod.ini: not a mod')
        dest = os.path.join(mods_dir, top)
        if os.path.exists(dest) and not args.replace:
            raise SystemExit(f'{dest} exists (install with --replace to overwrite it)')
        os.makedirs(mods_dir, exist_ok=True)
        z.extractall(mods_dir, members=names)
    m = read_manifest(dest)
    print(f'installed {m["name"]} {m["version"]} -> {dest}')


def main():
    ap = argparse.ArgumentParser(prog='modtool', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--mods'); ap.add_argument('--g3')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('mods', help='list installed mods, their files and conflicts')
    n = sub.add_parser('new-mod', help='create a mod folder with a mod.ini')
    n.add_argument('folder'); n.add_argument('--name'); n.add_argument('--author'); n.add_argument('--priority', type=int, default=0)
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
