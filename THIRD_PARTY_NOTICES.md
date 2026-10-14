# Third-party notices

This project (GPL-3.0, see LICENSE) contains, modifies or builds on the components below. Their copyright notices and
licenses are reproduced here as those licenses require. None of their authors endorse this project.

| component | license | how it is used |
|---|---|---|
| [Unleashed Recompiled](https://github.com/hedge-dev/UnleashedRecomp) | GPL-3.0 | the runtime's kernel, threading and heap code started from it (runtime/; full text: LICENSE and runtime/COPYING.unleashed) |
| [Xenia](https://github.com/xenia-canary/xenia-canary) | BSD-3-Clause | fetched by the setup at a pinned commit, patched (tools/xenia-gow3.patch) and built: its Direct3D 12 GPU emulation and audio; xe_glue/ connects it to the port |
| [XenonRecomp](https://github.com/hedge-dev/XenonRecomp) | MIT | fetched by the setup at a pinned commit, patched (tools/xenonrecomp-gow3.patch) and built: recompiles the game code; its XenonUtils is linked into the game |
| [o1heap](https://github.com/pavel-kirienko/o1heap) | MIT | runtime/thirdparty/o1heap |
| [unordered_dense](https://github.com/martinus/unordered_dense) | MIT | runtime/thirdparty/unordered_dense |

Components the setup downloads with Xenia and XenonRecomp keep their own licenses (in their source trees); among those
linked into the built game: FFmpeg's libavcodec / libavformat / libavutil (LGPL-2.1-or-later; Xenia uses them to decode
XMA audio), fmt (MIT), snappy (BSD-3-Clause), xxHash (BSD-2-Clause), SIMDe (MIT), and Xenia's own third-party code.
This repository distributes none of them as binaries; anyone who distributes a built game must follow those licenses
too (for FFmpeg: the LGPL's terms for static linking).

The setup also uses Python packages (pycryptodome, numpy, Pillow) and Microsoft's build tools, installed separately.

Gears of War is a trademark of Microsoft. This project contains no game code or assets: everything game-derived is made
on the player's PC from their own disc.

## Xenia (BSD-3-Clause)

```
Copyright (c) 2015, Ben Vanik.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
    * Redistributions of source code must retain the above copyright
      notice, this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright
      notice, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.
    * Neither the name of the project nor the
      names of its contributors may be used to endorse or promote products
      derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL BEN VANIK BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## XenonRecomp (MIT)

```
# MIT License

Copyright (c) 2025 hedge-dev and contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

- The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

- THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## o1heap (MIT)

```
Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions
of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS
OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

Copyright (c) 2020 Pavel Kirienko
Authors: Pavel Kirienko <pavel.kirienko@zubax.com>

READ THE DOCUMENTATION IN README.md.
```

## unordered_dense (MIT)

```
/////////////////////// ankerl::unordered_dense::{map, set} /////////////////////////
A fast & densely stored hashmap and hashset based on robin-hood backward shift deletion.
Version 4.4.0
https://github.com/martinus/unordered_dense

Licensed under the MIT License <http://opensource.org/licenses/MIT>.
SPDX-License-Identifier: MIT
Copyright (c) 2022-2023 Martin Leitner-Ankerl <martin.ankerl@gmail.com>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
