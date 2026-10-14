"""Which of the game's imports are really implemented by the runtime, which are Unleashed '!!! STUB !!!' bodies, and which are
missing entirely (fall to runtime/import_stubs.cpp). usage: import_coverage.py"""
import glob, re
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = ROOT + r"\runtime"
want = [l.strip() for l in open(fr"{R}\import_list.txt") if l.strip()]
src = "".join(open(f, encoding="utf8", errors="ignore").read() for f in glob.glob(fr"{R}\**\*.cpp", recursive=True) if "import_stubs" not in f)
hooks = dict(re.findall(r"GUEST_FUNCTION_HOOK\(__imp__(\w+),\s*([\w:]+)\)", src))
stubs_fn = set(re.findall(r"GUEST_FUNCTION_STUB\(__imp__(\w+)\)", src))
bodies = {}
for m in re.finditer(r"\n[\w:<>\*\s]+?\b(\w+)\(([^)]*)\)\s*\{(.*?)\n\}", src, re.S):
    bodies.setdefault(m.group(1), m.group(3))
real, stub, missing = [], [], []
for n in want:
    impl = hooks.get(n)
    if impl is None:
        (stub if n in stubs_fn else missing).append(n); continue
    body = bodies.get(impl.split("::")[-1], "")
    (stub if "!!! STUB !!!" in body and len(body.strip().splitlines()) <= 2 else real).append(n)
print(f"imports {len(want)}: implemented {len(real)}, stub bodies {len(stub)}, missing {len(missing)}")
print("STUB:", " ".join(sorted(stub)))
open(fr"{R}\..\import_todo.txt", "w").write("\n".join(sorted(stub + missing)))
