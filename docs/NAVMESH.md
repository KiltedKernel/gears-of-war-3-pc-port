# Navigation meshes for converted chapters

Gears 3's AI walks on navigation meshes (pylons and their polygons), which Gears 1 and 2 levels do not have.
`tools/trilogy.py build` makes them by running Gears 3's own navmesh builder on each converted region: the game is
started on the converted level with the builder's passes armed, and the meshes it builds are dumped and injected into
the chapter's packages (`tools/navinject.py`).

That builder is driven by hooks in the old Gears 3 PC port's runtime (`runtime/ue3/navmesh_build.cpp`), which this
repository no longer carries. Until the builder is ported to COGBox's developer mode:

1. Check out the tag `legacy-port-final` in a separate folder and build it as its README describes (Setup.cmd, from
   your own Gears 3 disc image). That gives `build\gow3.exe`.
2. Copy that `build` folder into this repository (or build there), so `build\gow3.exe` exists.
3. Run the chapter build once without `--reuse-navmesh`. The navmeshes are kept in `build\trilogy\*.navm`; later
   builds pass `--reuse-navmesh` and need no runtime at all.

The navmeshes are made from your own game files and stay on your PC, like everything else the converter makes.
