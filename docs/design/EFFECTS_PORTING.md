# Design: bringing Gears 1 and 2's look into Gears 3

Status: proposal (2026-11-12). Goal: recreate Gears 1 and 2 in Gears 3's engine as closely as the originals look and
play, porting their visual logic where Gears 3 has no equivalent, and using Gears 3's newer features only where they
improve on the old games' technical limits (shadows, resolution, texture detail) without changing the art.

## Where the converter stands

Today every converted surface is an instance (MaterialInstanceConstant) of a handful of Gears 3 master materials: an
opaque level master, a masked one, Gears 3's fog sheet, the weapon shader for glowing and character surfaces.
Gears 1's material graph is *read* (textures, tints, UV tiling, blend mode, emissive, opacity) and squeezed into those
masters' parameters. That is why some things cannot come out right:

- effects whose look is in the graph rather than the textures: rim and sheen terms, panning and bump-offset webs,
  view-dependent glints, the cobwebs' unlit veil, modulate decals (approximated as cutouts), light cones;
- particles: Gears 1's sprite materials are not converted; each emitter gets one of two stock Gears 3 materials
  (steam smoke, a flare), so smoke is the wrong smoke and the Wretch drool had nothing close;
- assets Gears 3 never had (Gears 2's Riftworm, Gears 1's Seeder and Kryll swarms, Gears 1/2 weapons' muzzle and
  tracer effects as they were).

The hard limit: a Gears 3 material is a set of shaders compiled for the Xbox 360 GPU when the game was cooked. We
cannot compile new ones for the console's shader format, so a new material graph cannot be cooked into a package.

## The proposal: three layers

### 1. More and better masters (converter only, now)

Map Gears 1/2 materials onto a wider set of Gears 3's own masters, chosen per feature, not one per blend mode:

- particle masters that take a texture parameter (translucent, additive, lit/unlit, soft and camera-facing variants)
  instead of two fixed materials, so a converted sprite uses its own Gears 1 texture and colour;
- Gears 3's own decal and modulate-capable masters for blood, prints and grime (no more cutouts);
- unlit translucent and light-shaft masters for cones and beams, with Gears 1's colours and falloff textures;
- a catalogue tool that lists every master in Gears 3's always-loaded packages with its parameters, blend mode and
  lighting model, so a recipe can pick by feature (tools/g1g3map.py grows into this).

This layer alone fixes the drool and smoke class of problems.

### 2. Shader overrides in COGBox (the real port of the visual logic)

COGBox already translates every Xbox 360 shader to HLSL at run time and compiles it with dxc, and already replaces
textures by a hash of their bytes (`gow2_texture_replace_dir`). The same hook for shaders:

- **COGBox:** a mod can ship `shaders/<hash>.hlsl` (or a table of them). When a draw's pixel shader microcode has that
  hash, COGBox compiles and uses the mod's HLSL instead, with the same constants and texture bindings available.
- **Carrier masters:** the converter instances a small set of Gears 3 "carrier" masters (ones whose compiled shaders
  sample several textures and read several scalar and vector parameters). A material's parameters carry an ID.
- **Material transpiler:** the converter turns each Gears 1/2 material graph into HLSL (the expressions map almost one
  to one: TextureSample, Multiply, Lerp, Panner, BumpOffset, Fresnel, CameraVector...), wrapped as the carrier's pixel
  shader with a switch on the ID. Gears 1's rim lights, panning webs, unlit veils and modulate blends then render as
  they did, lit by Gears 3's lighting where the material was lit.

This is the general mechanism the user asked for: Gears 1 and 2's visual presentation logic ported directly, and it
also covers post-processing (Gears 1's bloom and tone curve as an override of Gears 3's post shaders, per level).

Needs: Shagg's agreement on the COGBox hook (small: a hash lookup where shaders are created, plus dxc, which COGBox
ships). Risks: per-draw constant layouts differ between carrier permutations (pick carriers with stable layouts and
read their constant map from COGBox's shader dump); vertex factory variants (static mesh, skeletal, particle) each
need their own carrier.

### 3. Assets Gears 3 does not have

- Meshes, skeletons and animations convert as they do now (characters, the Corpser): static and skeletal meshes,
  AnimSets, Matinee and Kismet.
- Set pieces that were mostly scripted (the Riftworm's interior is level geometry; its exterior shots are Matinee)
  become levels plus Matinee plus Kismet, with materials through layer 1 or 2.
- Creatures with their own AI and gameplay code (Seeders, Kryll, Gears 2's Riftworm heart fight) need behaviour that
  Gears 3's classes approximate: a recipe maps each to the nearest Gears 3 class and its Kismet; where nothing is
  close, the gameplay is rebuilt in Kismet, and as a last resort as a COGBox gameplay patch.

## Light

The biggest visible gap today. Gears 1 baked its static lighting (light maps for BSP, vertex light for meshes) and lit
characters with a few dynamic lights. The converter keeps Gears 1's vertex light for meshes, bakes the lights Gears 1
drew dynamically into vertices (shadowed by the level's geometry), and lights characters with Gears 3's light
environments. Next steps, in order:

1. Gears 1's own texture light maps for its BSP surfaces (its LightMapTexture2D data and UVs) instead of per-vertex
   light on the converted BSP: the original lighting, texel for texel, where flat vertex light washes out.
2. Characters: Gears 1's per-light direct lighting is closer than Gears 3's composited light environments (the
   cutscene comparison of 2026-11-12); via layer 2, or light-environment settings per level.
3. Gears 3's additions only where they help: higher shadow resolution, ambient occlusion, a sharper resolve.

## Order of work

1. Layer 1 catalogue and particle masters (fixes chapter 1's effects).
2. Gears 1 light maps (fixes most of the flat or washed-out lighting).
3. The COGBox shader hook, then the transpiler for the materials layers 1 and 2 cannot reach.
4. Gears 2 assets as Gears 2 chapters need them.
