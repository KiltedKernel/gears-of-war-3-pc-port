"""Campaign chapters for mods: new chapters in Gears 3's campaign menu, each loading its own map (offline helper).

How Gears 3 starts a chapter (docs/REVERSE_ENGINEERING.md): the menu builds 'GearGame_P?game=...GearGameSP?chapter=N'
and native code loads Default__GearTypes.ChapterLevelNames[N] (31 entries on the disc). The menu lists chapters from
config: [ChapterN GearCampaignChapterData] and [ActN GearCampaignActData] in Xbox360-GearGame.ini, their text in
GearGame.int. Chapters 32-49 (CHAP_DLC_1..18) are free; the runtime (runtime/ue3/campaign_chapters.cpp) puts the
MapName of every chapter 32-49 found in the active config into ChapterLevelNames.

usage: python -P campaign.py levels <GearGame.xxx>        the disc's chapter -> map table
       python -P campaign.py fragment --chapter 32 --act 6 --map sp_mymap_p --title "First Chapter" \\
              [--act-title "My Campaign"] [--number-desc "Chapter 1"] [--location ...] [--time ...] \\
              [--screenshot <texture path>] [--out chapter.ini]
           an ini fragment for tools/coalesced.py merge (the act's section is included when --act-title is given)
"""
import argparse, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FIRST_FREE_CHAPTER, LAST_CHAPTER = 32, 49
ROMAN = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI', 'XII']
DEFAULT_SHOT = 'UI_Portraits_G3.SP.T_Portraits_SP_A1C0_Act1'


def fragment(a):
    if not FIRST_FREE_CHAPTER <= a.chapter <= LAST_CHAPTER:
        raise SystemExit(f'--chapter: new chapters take the free numbers {FIRST_FREE_CHAPTER}-{LAST_CHAPTER}')
    if a.act < 6: print(f'note: act {a.act} is one of the game\'s own (1-5); the chapter joins it', file=sys.stderr)
    shot = a.screenshot or DEFAULT_SHOT
    act_num = a.act - 1                                # ActNum counts from 0 (Act1 has ActNum=0)
    out = ['##### Xbox360-GearGame.ini']
    if a.act_title:
        out += [f'[Act{a.act} GearCampaignActData]', f'ActNum={act_num}', 'bRequiredActForGameCompletion=false',
                f'ScreenshotPathName={shot}']
    out += [f'[Chapter{a.chapter} GearCampaignChapterData]', f'ChapterNum={a.chapter}', f'ActNum={act_num}',
            f'MapName={a.map}', f'ScreenshotPathName={shot}', 'IsAlwaysUnlocked=true', '##### GearGame.int']
    if a.act_title:
        roman = ROMAN[a.act - 1] if a.act <= len(ROMAN) else str(a.act)
        out += [f'[Act{a.act} GearCampaignActData]', f'ListName={roman}: {a.act_title}', f'DisplayName={a.act_title}']
    out += [f'[Chapter{a.chapter} GearCampaignChapterData]', f'ListName={a.list_name or str(a.chapter - FIRST_FREE_CHAPTER + 1) + ": " + a.title}', f'DisplayName={a.title}',
            f'ChapterNumDesc={a.number_desc or "Chapter " + str(a.chapter - FIRST_FREE_CHAPTER + 1)}']
    if a.location: out.append(f'ChapterLocation={a.location}')
    if a.time: out.append(f'ChapterTime={a.time}')
    text = '\n'.join(out) + '\n'
    if a.out:
        open(a.out, 'w', encoding='utf-8', newline='\n').write(text); print(f'-> {a.out}')
    else:
        sys.stdout.write(text)


def main():
    ap = argparse.ArgumentParser(prog='campaign', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    lv = sub.add_parser('levels'); lv.add_argument('package')
    f = sub.add_parser('fragment')
    f.add_argument('--chapter', type=int, required=True); f.add_argument('--act', type=int, required=True)
    f.add_argument('--map', required=True); f.add_argument('--title', required=True)
    f.add_argument('--list-name'); f.add_argument('--act-title'); f.add_argument('--number-desc')
    f.add_argument('--location'); f.add_argument('--time'); f.add_argument('--screenshot'); f.add_argument('--out')
    a = ap.parse_args()
    if a.cmd == 'levels':
        import upk
        p = upk.Package(a.package)
        i = next(i for i, e in enumerate(p.exports) if e['name'] == 'Default__GearTypes')
        d = p.export_data(i)
        # tagged properties from offset 4: find ChapterLevelNames' array (count + FNames)
        name_idx = p.names.index('ChapterLevelNames')
        o = 4
        while o + 24 <= len(d):
            n, _ = struct.unpack_from('>ii', d, o)
            if p.names[n] == 'None': break
            t, _ = struct.unpack_from('>ii', d, o + 8)
            size = struct.unpack_from('>i', d, o + 16)[0]
            o += 24
            if p.names[t] in ('StructProperty', 'ByteProperty'): o += 8
            if p.names[t] == 'BoolProperty': size = 1
            if n == name_idx:
                count = struct.unpack_from('>i', d, o)[0]
                for k in range(count):
                    ni, nn = struct.unpack_from('>ii', d, o + 4 + 8 * k)
                    print(k, p.names[ni] + (f'_{nn - 1}' if nn else ''))
                return
            o += size
        raise SystemExit('ChapterLevelNames not found')
    fragment(a)


if __name__ == '__main__':
    main()
