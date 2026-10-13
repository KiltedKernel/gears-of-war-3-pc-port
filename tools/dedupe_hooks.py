"""Comment out Unleashed Recompiled hooks for imports that the GoW2 kernel files (runtime/kernel/gow2_*.cpp)
implement, so each import has exactly one strong definition. Idempotent; run before every build."""
import glob, os, re
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = ROOT + r"\runtime"
mine = set()
for f in glob.glob(os.path.join(R, "kernel", "gow2_*.cpp")):
    mine |= set(re.findall(r"^GUEST_FUNCTION_(?:HOOK|STUB)\(__imp__(\w+)", open(f).read(), re.M))
changed = 0
for f in glob.glob(os.path.join(R, "**", "*.cpp"), recursive=True):
    if os.path.basename(f).startswith("gow2_") or "import_stubs" in f or "thirdparty" in f:
        continue
    s = open(f, encoding="utf8").read()
    def sub(m):
        global changed
        if m.group(2) in mine:
            changed += 1
            return "// replaced by kernel/gow2_*.cpp: " + m.group(1)
        return m.group(1)
    s2 = re.sub(r"^(GUEST_FUNCTION_(?:HOOK|STUB)\(__imp__(\w+).*)$", sub, s, flags=re.M)
    if s2 != s:
        open(f, "w", encoding="utf8").write(s2)
print(f"GoW2 kernel implements {len(mine)} imports; {changed} Unleashed hooks disabled")
