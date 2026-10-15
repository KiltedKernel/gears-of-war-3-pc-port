"""Release version check: VERSION, CHANGELOG.md, README.md and docs/STATUS.md name the same release.

usage: python tools/check_version.py
VERSION is what the build uses (the launcher shows it, the game logs it, Setup.cmd's wizard shows it); the documents
repeat it by hand. Run this before tagging a release: it prints each file's version and exits 1 when they differ.
"""
import os, re, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# file, pattern whose first group is the version that file names
SOURCES = [
    ('CHANGELOG.md', r'^## (\d+\.\d+\.\d+) \('),          # the newest release heading (Unreleased has no number)
    ('README.md', r'^Version (\d+\.\d+\.\d+):'),
    ('docs/STATUS.md', r'^Current release: \*\*(\d+\.\d+\.\d+)\*\*'),
]


def main():
    with open(os.path.join(ROOT, 'VERSION'), encoding='utf-8') as f:
        want = f.read().strip()
    print(f'VERSION          {want}')
    bad = False
    for name, pattern in SOURCES:
        with open(os.path.join(ROOT, name), encoding='utf-8') as f:
            m = re.search(pattern, f.read(), re.M)
        got = m.group(1) if m else None
        print(f'{name:<16} {got or "(not found)"}{"" if got == want else "   <- differs"}')
        bad |= got != want
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
