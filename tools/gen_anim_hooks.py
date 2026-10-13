"""Generate runtime/anim_debug_list.inc: a GOW3_SKIPPABLE(addr) line for every virtual that a set of animation classes
overrides relative to their engine base class. usage: gen_anim_hooks.py D:/gow3-decomp/default.bin"""
import subprocess, sys, re

BIN = sys.argv[1]
SKEL = ['SkelControlLimb', 'SkelControlLookAt', 'SkelControlSingleBone', 'GearSkelCtrl_IKRecoil', 'GearSkelCtrl_CCD_IK',
        'GearSkelCtrl_Recoil', 'GearSkelCtrl_Copy', 'GearSkelCtrl_FootPlanting', 'GearSkelCtrl_Spring', 'GearSkelCtrl_Prune',
        'GearSkelCtrl_TurretConstrained', 'SkelControlFootPlacement']
NODES = ['AnimNodeAimOffset', 'GearAnim_AimOffset', 'GearAnim_UpperBodyIKHack', 'GearAnim_BlendPerBone',
         'GearAnim_Mirror_Master', 'GearAnim_Mirror_TransitionBlend', 'GearAnim_AdditiveBlending', 'GearAnim_BlendAnimsByAim',
         'GearAnim_TurnInPlace', 'GearAnim_BlendByTargetingMode', 'GearAnim_BlendByWeaponType', 'GearAnim_BlendByWeaponClass',
         'GearAnim_Slot', 'GearAnim_MovementNode', 'GearAnim_BaseBlendNode']
# functions already overridden elsewhere in the runtime (a second strong definition would not link)
TAKEN = {0x8257C1F0, 0x825982F8, 0x8298C2A0, 0x8280DDC8, 0x826404E0, 0x822C1720, 0x824A7FA0, 0x8268A068}

found = {}
for cls, base in [(c, 'SkelControlBase') for c in SKEL] + [(c, 'AnimNodeBlendBase') for c in NODES]:
    out = subprocess.run([sys.executable, 'tools/class_vtable.py', BIN, cls, base, '200'], capture_output=True, text=True).stdout
    for m in re.finditer(r'\[\s*(\d+)\] \+([0-9A-F]+) ([0-9A-F]{8})   base ([0-9A-F]{8})  <-- override', out):
        slot, addr = m.group(2), int(m.group(3), 16)
        if addr in TAKEN or not 0x82200000 <= addr < 0x82E00000: continue
        found.setdefault(addr, set()).add(f'{cls}+{slot}')
with open('runtime/anim_debug_list.inc', 'w') as f:
    for addr in sorted(found):
        f.write(f'GOW3_SKIPPABLE({addr:08X})   // {", ".join(sorted(found[addr]))}\n')
print(len(found), 'functions')
