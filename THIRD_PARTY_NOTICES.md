# Third-party notices

This project (GPL-3.0, see LICENSE; `modcore/` is MIT, see modcore/LICENSE) contains or builds on the components below.
None of their authors endorse this project.

| component | license | how it is used |
|---|---|---|
| [libmspack](https://github.com/kyz/libmspack) | LGPL-2.1 | `tools/xpack/lzxd_xmem.c` is a modified copy of its LZX decoder (lzxd.c; the change: an odd-sized uncompressed block's pad byte is optional); `tools/build_xpack.cmd` fetches libmspack v1.11 for its headers to build `tools/xpack/xpack.exe`, the package decompressor |

libmspack is (C) 2003-2023 Stuart Caie and is free software under the GNU Lesser General Public License version 2.1
(the full text: https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html, and COPYING.LIB in libmspack's source). The
modified file keeps its notice; its source is this repository.

The tools use Python packages installed separately (numpy, Pillow; lz4 for packages compressed with it) and
Microsoft's build tools.

The old PC port (tag `legacy-port-final`) built on Unleashed Recompiled (GPL-3.0), Xenia (BSD-3-Clause), XenonRecomp
(MIT), o1heap (MIT) and unordered_dense (MIT); their notices are in that tag's THIRD_PARTY_NOTICES.md.

Gears of War is a trademark of Microsoft. This project contains no game code or assets: everything game-derived is made
on the player's PC from their own discs.
