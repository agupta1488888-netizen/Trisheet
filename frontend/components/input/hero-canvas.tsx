"use client";

/**
 * The hero's cinematic backdrop.
 *
 * Isolated into its own client-only module so `input-screen.tsx` can load it
 * through `next/dynamic` with `ssr: false` — three.js, the fiber renderer and
 * the post-processing chain never enter the server-rendered payload or block
 * first paint.
 *
 * The scene is the product's own mechanism, not a stock market mood. Filing
 * pages hang at different depths; on three of them a single line item is
 * marked with a bright node; fine threads run from those nodes forward to
 * anchors on a larger sheet at the front — the assembled profile. On a slow
 * stagger a bead of light travels each thread, and the anchor it reaches
 * flashes. That is "every figure traces back to the filing it came from",
 * rendered as motion, which is what the headline over this canvas claims.
 *
 * The pages carry no legible text, deliberately. Every "word" is a rounded
 * blob: correct line rhythm, correct gap texture, correct ragged last line of
 * a paragraph, and right-aligned numeric columns where a financial statement
 * would have them — but nothing that resolves to a character at any zoom.
 * CLAUDE.md forbids presenting an unlabelled figure as if it were live, and
 * "illegible at a glance" is not a promise this file can keep across viewport
 * sizes, so it renders nothing to be legible in the first place. That also
 * sidesteps `next/font`: its families are hashed CSS variables that a canvas
 * 2D context cannot resolve by name, so any `fillText` here would silently
 * fall back to a system font and vary by machine.
 *
 * Glow is real post-processing, not the stacked halo/core fake this file used
 * previously. Emissive colours are built exactly one way — `new THREE.Color(hex)`
 * (which converts sRGB to the linear working space) then `multiplyScalar(gain)`
 * to push past the bloom threshold. Never pass an array literal as a colour
 * here: R3F routes those to `setRGB`, which treats them as *already* linear,
 * and mixing the two conventions is the usual reason bloom appears not to work.
 * `BLOOM_LUMINANCE_THRESHOLD` sits in the gap between the brightest element
 * that must stay matte (the thread core) and the dimmest that must glow (the
 * trailing bead); the gains below were chosen to hold that gap.
 *
 * `animate` (gated by `prefers-reduced-motion`, read by the caller) stops
 * every motion. It does *not* turn off bloom or the vignette — those are
 * static image-space effects, and a reader who asked for less motion is still
 * entitled to the same picture, held still. The pulses stay visible, parked
 * part-way along their threads rather than bunched at their sources.
 */

import { useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Line, PerformanceMonitor, Sparkles } from "@react-three/drei";
import { Bloom, EffectComposer, Vignette } from "@react-three/postprocessing";
import * as THREE from "three";

// --- Palette -------------------------------------------------------------
// Two colours only — slate and emerald — matching the assistant showcase
// below the fold, so the page reads as one design rather than two stacked.
// Anything not on this list does not belong in this scene.

const BACKGROUND_COLOR = "#020617";

const SUBSTRATE_COLOR = "#0b1220";
const PAGE_BORDER_COLOR = "#94a3b8";
const INK_COLOR = "#cbd5e1";
const TINT_NEAR_COLOR = "#e2e8f0";
const TINT_FAR_COLOR = "#475569";

const THREAD_HALO_COLOR = "#34d399";
const THREAD_CORE_COLOR = "#6ee7b7";
const NODE_COLOR = "#34d399";
const HIGHLIGHT_COLOR = "#34d399";
const ANCHOR_COLOR = "#34d399";
const PULSE_COLOR = "#a7f3d0";
const SPARKLE_COLOR = "#cbd5e1";

// --- Emissive gains ------------------------------------------------------
// Multiplied onto the linear colour. Anything whose resulting luminance clears
// BLOOM_LUMINANCE_THRESHOLD glows; anything below it stays matte.

const NODE_GAIN = 4.0;
const ANCHOR_REST_GAIN = 1.6;
const ANCHOR_FLASH_GAIN = 3.0;
const HIGHLIGHT_GAIN = 0.95;

const BLOOM_LUMINANCE_THRESHOLD = 0.85;
const BLOOM_LUMINANCE_SMOOTHING = 0.12;
const BLOOM_INTENSITY = 1.15;
const BLOOM_RADIUS = 0.72;
const BLOOM_LEVELS = 7;
const VIGNETTE_OFFSET = 0.3;
const VIGNETTE_DARKNESS = 0.6;

// --- Seeded randomness ---------------------------------------------------
// mulberry32. The previous version of this file called Math.random() inside
// useMemo, so the composition was different on every mount and could not be
// art-directed or re-checked after a tweak. Determinism across reloads and
// hot module replacement is what makes "tune by eye" possible at all.

function createRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// --- Page textures -------------------------------------------------------
// One canvas bake per recipe, shared by reference across every sheet that
// uses it — four GPU uploads for twelve pages. Mipmaps are doing double duty:
// a far sheet's line pitch falls below one screen pixel and trilinear
// filtering dissolves it into a soft wash, which is the aerial-perspective
// cue that depth of field would otherwise have cost a full blur pass to buy.

const PAGE_TEXTURE_W = 768;
const PAGE_TEXTURE_H = 994;
const PAGE_ASPECT = PAGE_TEXTURE_W / PAGE_TEXTURE_H;

const PAGE_MARGIN_X = 68;
const PAGE_MARGIN_TOP = 78;
const PAGE_MARGIN_BOTTOM = 62;

const GLYPH_RADIUS = 1.6;
const WORD_GAP = 6;
const WORD_MIN_W = 13;
const WORD_MAX_W = 68;

const SUBSTRATE_ALPHA = 0.14;
const PAGE_BORDER_ALPHA = 0.32;
const PAGE_BORDER_WIDTH = 1.5;
const PAGE_BORDER_INSET = 3;

const INK_ALPHA_MIN = 0.4;
const INK_ALPHA_MAX = 0.78;
const HEADER_INK_ALPHA = 0.88;
const RULE_ALPHA = 0.45;
const FOOTNOTE_GLYPH_H = 4.5;
const FOOTNOTE_ALPHA = 0.26;

const PARAGRAPH_MIN_LINES = 4;
const PARAGRAPH_MAX_LINES = 11;
const LAST_LINE_MIN = 0.3;
const LAST_LINE_MAX = 0.8;

const TABLE_MIN_ROWS = 4;
const TABLE_MAX_ROWS = 8;
const TABLE_LABEL_MIN = 0.18;
const TABLE_LABEL_MAX = 0.34;
const TABLE_FIRST_COLUMN = 0.62;
const TABLE_COLUMN_PITCH = 0.19;
const TABLE_NUMBER_MIN = 34;
const TABLE_NUMBER_MAX = 58;

const MAX_ANISOTROPY = 8;

interface PageRecipe {
  readonly seed: number;
  readonly linePitch: number;
  readonly glyphHeight: number;
  readonly headerLines: number;
  /** Every Nth body block is a table; the rest are prose. Lower = more tables. */
  readonly tableEvery: number;
  readonly tableColumns: number;
  readonly footnoteLines: number;
}

/**
 * Four characters, so twelve sheets do not read as tiled wallpaper. Index 0
 * is the most table-heavy and is what the front "profile" sheet uses — that
 * is the sheet the anchors land on, so it has to look like a page of figures.
 */
const PAGE_RECIPES: readonly PageRecipe[] = [
  // 0 — statement: mostly tables, the page a set of figures lives on.
  {
    seed: 0x5eed01,
    linePitch: 19,
    glyphHeight: 6.5,
    headerLines: 2,
    tableEvery: 2,
    tableColumns: 3,
    footnoteLines: 3,
  },
  // 1 — MD&A: prose, the occasional table.
  {
    seed: 0x5eed02,
    linePitch: 19,
    glyphHeight: 6.5,
    headerLines: 2,
    tableEvery: 5,
    tableColumns: 2,
    footnoteLines: 2,
  },
  // 2 — notes: tighter leading, short paragraphs, heavy footnotes.
  {
    seed: 0x5eed03,
    linePitch: 15,
    glyphHeight: 5.2,
    headerLines: 1,
    tableEvery: 4,
    tableColumns: 2,
    footnoteLines: 5,
  },
  // 3 — cover/exhibit: a heavy header and looser leading.
  {
    seed: 0x5eed04,
    linePitch: 22,
    glyphHeight: 7.2,
    headerLines: 3,
    tableEvery: 3,
    tableColumns: 3,
    footnoteLines: 1,
  },
];

/** One line of "words": correct rhythm and gaps, no characters. */
function drawWordRun(
  ctx: CanvasRenderingContext2D,
  random: () => number,
  x: number,
  y: number,
  width: number,
  glyphHeight: number,
  alpha: number,
): void {
  ctx.globalAlpha = alpha;
  const limit = x + width;
  let cursor = x;
  while (cursor < limit) {
    const remaining = limit - cursor;
    const wordWidth = Math.min(
      WORD_MIN_W + random() * (WORD_MAX_W - WORD_MIN_W),
      remaining,
    );
    if (wordWidth < WORD_MIN_W * 0.5) {
      break;
    }
    ctx.beginPath();
    ctx.roundRect(cursor, y, wordWidth, glyphHeight, GLYPH_RADIUS);
    ctx.fill();
    cursor += wordWidth + WORD_GAP;
  }
}

/**
 * A row of a financial table: a label on the left, then numbers right-aligned
 * into fixed columns. This is the single detail that turns "a page" into "a
 * filing" — the eye recognises the column structure long before it would have
 * read any of the digits.
 */
function drawTableRow(
  ctx: CanvasRenderingContext2D,
  random: () => number,
  x: number,
  y: number,
  measure: number,
  glyphHeight: number,
  alpha: number,
  columns: number,
): void {
  const labelWidth =
    measure * (TABLE_LABEL_MIN + random() * (TABLE_LABEL_MAX - TABLE_LABEL_MIN));
  drawWordRun(ctx, random, x, y, labelWidth, glyphHeight, alpha);

  ctx.globalAlpha = alpha;
  for (let column = 0; column < columns; column += 1) {
    const right = x + measure * (TABLE_FIRST_COLUMN + column * TABLE_COLUMN_PITCH);
    const width =
      TABLE_NUMBER_MIN + random() * (TABLE_NUMBER_MAX - TABLE_NUMBER_MIN);
    ctx.beginPath();
    ctx.roundRect(right - width, y, width, glyphHeight, GLYPH_RADIUS);
    ctx.fill();
  }
}

function drawPage(ctx: CanvasRenderingContext2D, recipe: PageRecipe): void {
  const random = createRandom(recipe.seed);
  const measure = PAGE_TEXTURE_W - PAGE_MARGIN_X * 2;
  const bottom = PAGE_TEXTURE_H - PAGE_MARGIN_BOTTOM;

  ctx.clearRect(0, 0, PAGE_TEXTURE_W, PAGE_TEXTURE_H);

  // The substrate and the border together are what make this unambiguously a
  // page rather than text floating in space. Neither is optional.
  ctx.globalAlpha = SUBSTRATE_ALPHA;
  ctx.fillStyle = SUBSTRATE_COLOR;
  ctx.fillRect(0, 0, PAGE_TEXTURE_W, PAGE_TEXTURE_H);

  ctx.globalAlpha = PAGE_BORDER_ALPHA;
  ctx.strokeStyle = PAGE_BORDER_COLOR;
  ctx.lineWidth = PAGE_BORDER_WIDTH;
  ctx.strokeRect(
    PAGE_BORDER_INSET,
    PAGE_BORDER_INSET,
    PAGE_TEXTURE_W - PAGE_BORDER_INSET * 2,
    PAGE_TEXTURE_H - PAGE_BORDER_INSET * 2,
  );

  ctx.fillStyle = INK_COLOR;
  let y = PAGE_MARGIN_TOP;

  for (let line = 0; line < recipe.headerLines; line += 1) {
    const width = measure * (0.34 + random() * 0.3);
    drawWordRun(
      ctx,
      random,
      PAGE_MARGIN_X,
      y,
      width,
      recipe.glyphHeight * 1.45,
      HEADER_INK_ALPHA,
    );
    y += recipe.linePitch * 1.7;
  }

  ctx.globalAlpha = RULE_ALPHA;
  ctx.fillRect(PAGE_MARGIN_X, y, measure, 1.2);
  y += recipe.linePitch * 1.4;

  // Fill the body all the way to the footnote block rather than laying down a
  // fixed number of blocks. A page that stops half way leaves blank substrate
  // underneath, and anything placed there — an anchor, a node — reads as
  // floating in the dark instead of sitting on a line of a filing.
  const bodyBottom = bottom - recipe.footnoteLines * FOOTNOTE_GLYPH_H * 2.6;
  let block = 0;
  while (y < bodyBottom - recipe.linePitch) {
    const isTable = block % recipe.tableEvery === recipe.tableEvery - 1;

    if (isTable) {
      const rows =
        TABLE_MIN_ROWS +
        Math.floor(random() * (TABLE_MAX_ROWS - TABLE_MIN_ROWS));
      ctx.globalAlpha = RULE_ALPHA;
      ctx.fillRect(PAGE_MARGIN_X, y, measure, 1);
      y += recipe.linePitch * 0.8;
      for (let row = 0; row < rows && y < bodyBottom; row += 1) {
        const alpha =
          INK_ALPHA_MIN + random() * (INK_ALPHA_MAX - INK_ALPHA_MIN);
        drawTableRow(
          ctx,
          random,
          PAGE_MARGIN_X,
          y,
          measure,
          recipe.glyphHeight,
          alpha,
          recipe.tableColumns,
        );
        y += recipe.linePitch;
      }
      ctx.globalAlpha = RULE_ALPHA;
      ctx.fillRect(PAGE_MARGIN_X, y, measure, 1);
      y += recipe.linePitch * 1.4;
    } else {
      const lines =
        PARAGRAPH_MIN_LINES +
        Math.floor(random() * (PARAGRAPH_MAX_LINES - PARAGRAPH_MIN_LINES));
      for (let line = 0; line < lines && y < bodyBottom; line += 1) {
        // The last line of a paragraph is short and ragged. That single
        // feature is what makes a block of blobs read as prose rather than
        // as a texture.
        const isLast = line === lines - 1;
        const width = isLast
          ? measure *
            (LAST_LINE_MIN + random() * (LAST_LINE_MAX - LAST_LINE_MIN))
          : measure;
        const alpha =
          INK_ALPHA_MIN + random() * (INK_ALPHA_MAX - INK_ALPHA_MIN);
        drawWordRun(
          ctx,
          random,
          PAGE_MARGIN_X,
          y,
          width,
          recipe.glyphHeight,
          alpha,
        );
        y += recipe.linePitch;
      }
      y += recipe.linePitch * 0.9;
    }

    block += 1;
  }

  for (let line = 0; line < recipe.footnoteLines && y < bottom; line += 1) {
    drawWordRun(
      ctx,
      random,
      PAGE_MARGIN_X + 14,
      y,
      measure * (0.6 + random() * 0.34),
      FOOTNOTE_GLYPH_H,
      FOOTNOTE_ALPHA,
    );
    y += FOOTNOTE_GLYPH_H * 2.6;
  }

  ctx.globalAlpha = 1;
}

function createPageTexture(
  recipe: PageRecipe,
  maxAnisotropy: number,
): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = PAGE_TEXTURE_W;
  canvas.height = PAGE_TEXTURE_H;
  const ctx = canvas.getContext("2d");
  if (ctx !== null) {
    drawPage(ctx, recipe);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.wrapS = THREE.ClampToEdgeWrapping;
  texture.wrapT = THREE.ClampToEdgeWrapping;
  texture.generateMipmaps = true;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.anisotropy = Math.min(MAX_ANISOTROPY, maxAnisotropy);
  texture.needsUpdate = true;
  return texture;
}

// --- Shared GPU resources ------------------------------------------------
// Two geometries and four textures for the whole scene, shared by reference
// and scaled per instance. R3F disposes what it creates through the
// reconciler; it does not dispose anything built by hand in a hook, so this
// owns its own cleanup.

interface SharedResources {
  readonly pages: readonly THREE.CanvasTexture[];
  readonly plane: THREE.PlaneGeometry;
  readonly sphere: THREE.SphereGeometry;
}

function useSharedResources(): SharedResources {
  const maxAnisotropy = useThree((state) =>
    state.gl.capabilities.getMaxAnisotropy(),
  );

  const resources = useMemo<SharedResources>(
    () => ({
      pages: PAGE_RECIPES.map((recipe) =>
        createPageTexture(recipe, maxAnisotropy),
      ),
      plane: new THREE.PlaneGeometry(1, 1),
      sphere: new THREE.SphereGeometry(1, 12, 12),
    }),
    [maxAnisotropy],
  );

  useEffect(() => {
    return () => {
      for (const page of resources.pages) {
        page.dispose();
      }
      resources.plane.dispose();
      resources.sphere.dispose();
    };
  }, [resources]);

  return resources;
}

// --- Composition ---------------------------------------------------------
// Positions are tuned against the layout above this canvas: an opaque white
// form card covers the left half, and the headline column sits right of
// centre. Everything bright is placed where it is actually visible — above
// the brand mark, in the gutter between the two columns, or out in the right
// margin. Nothing bright sits behind display type.

const DEPTH_NEAR_Z = 1.7;
const DEPTH_SPAN = 15.5;
const OPACITY_NEAR = 0.52;
const OPACITY_FAR = 0.14;
const PARALLAX_NEAR = 1.0;
const PARALLAX_FAR = 0.22;

/**
 * Where the composition's centre of interest should land horizontally, in
 * normalised device coordinates (−1 is the left edge, +1 the right).
 *
 * This is derived from the layout rather than chosen by taste. The form card
 * and headline are anchored left inside a capped column, so their right edge
 * lands near ndc 0.17 on a wide window and near 0.46 on a narrow one; 0.70
 * clears the text at both. Solving for a target NDC is also what makes this
 * survive a change of aspect ratio — an earlier version applied a fixed
 * world-space offset, which looked correct at one window size and drove the
 * threads straight through the headline at another.
 */
const COMPOSITION_TARGET_NDC_X = 0.7;

/** Local x of the composition's centre of mass, between the profile sheet and s3. */
const COMPOSITION_CENTER_X = 5.0;

const COMPOSITION_REFERENCE_WIDTH = 12.28;
const COMPOSITION_MIN_SCALE = 0.62;

interface SheetSpec {
  readonly id: string;
  readonly x: number;
  readonly y: number;
  readonly z: number;
  readonly width: number;
  readonly rotY: number;
  readonly rotZ: number;
  readonly page: number;
  readonly flipped: boolean;
  readonly drift: number;
}

/** The front sheet: the profile being assembled, and every thread's target. */
const PROFILE_SHEET: SheetSpec = {
  id: "profile",
  x: 3.55,
  y: -1.05,
  z: 1.7,
  width: 3.3,
  rotY: -0.28,
  rotZ: 0.018,
  page: 0,
  flipped: false,
  drift: 0,
};

/**
 * The three sheets that carry a marked line item.
 *
 * Clustered deliberately into the upper right. The headline column above this
 * canvas reaches to roughly x 0.55 in normalised device coordinates, and every
 * node here resolves to x 0.60 or beyond — so the threads running down to the
 * anchors stay in the right margin instead of sweeping across the type, which
 * is what the first render of this scene did.
 */
const SOURCE_SHEETS: readonly SheetSpec[] = [
  {
    id: "s1",
    x: 4.6,
    y: 3.0,
    z: -1.6,
    width: 2.2,
    rotY: 0.24,
    rotZ: -0.045,
    page: 1,
    flipped: false,
    drift: 0,
  },
  {
    id: "s2",
    x: 6.6,
    y: 0.9,
    z: -3.1,
    width: 2.05,
    rotY: 0.3,
    rotZ: 0.055,
    page: 2,
    flipped: false,
    drift: 0,
  },
  {
    id: "s3",
    x: 7.4,
    y: 2.2,
    z: -4.2,
    width: 1.95,
    rotY: -0.2,
    rotZ: 0.032,
    page: 3,
    flipped: false,
    drift: 0,
  },
];

/**
 * Depth filler. These are the only sheets that drift — see the rigidity note
 * on `ProvenanceSystem`. They get *larger* in world units the further back
 * they sit, so perspective shrink does not reduce them to specks; a page has
 * to stay recognisable as a page at every depth or the field reads as noise.
 */
const DECOR_SHEETS: readonly SheetSpec[] = [
  {
    id: "s4",
    x: -3.9,
    y: 1.7,
    z: -5.3,
    width: 2.4,
    rotY: 0.34,
    rotZ: -0.03,
    page: 1,
    flipped: true,
    drift: 0.09,
  },
  {
    id: "s5",
    x: 1.1,
    y: -3.6,
    z: -6.2,
    width: 2.3,
    rotY: 0.16,
    rotZ: 0.05,
    page: 2,
    flipped: false,
    drift: 0.13,
  },
  {
    id: "s6",
    x: 7.3,
    y: -2.2,
    z: -7.5,
    width: 2.6,
    rotY: -0.26,
    rotZ: -0.055,
    page: 3,
    flipped: false,
    drift: 0.11,
  },
  {
    id: "s7",
    x: -6.1,
    y: -1.1,
    z: -8.7,
    width: 2.5,
    rotY: 0.28,
    rotZ: 0.025,
    page: 0,
    flipped: true,
    drift: 0.16,
  },
  {
    id: "s8",
    x: 3.2,
    y: 4.3,
    z: -9.9,
    width: 2.7,
    rotY: 0.1,
    rotZ: -0.04,
    page: 1,
    flipped: false,
    drift: 0.1,
  },
  {
    id: "s9",
    x: -1.6,
    y: -5.0,
    z: -11.2,
    width: 2.8,
    rotY: 0.22,
    rotZ: 0.045,
    page: 2,
    flipped: true,
    drift: 0.18,
  },
  {
    id: "s10",
    x: 8.4,
    y: 2.9,
    z: -12.6,
    width: 3.0,
    rotY: -0.18,
    rotZ: -0.025,
    page: 3,
    flipped: false,
    drift: 0.12,
  },
  {
    id: "s11",
    x: -8.2,
    y: 3.8,
    z: -13.8,
    width: 3.1,
    rotY: 0.24,
    rotZ: 0.03,
    page: 0,
    flipped: true,
    drift: 0.15,
  },
];

function sheetHeight(sheet: SheetSpec): number {
  return sheet.width / PAGE_ASPECT;
}

function depthOf(z: number): number {
  return THREE.MathUtils.clamp((DEPTH_NEAR_Z - z) / DEPTH_SPAN, 0, 1);
}

// --- Nodes, anchors and threads ------------------------------------------

const NODE_Z_OFFSET = 0.012;
const NODE_RADIUS = 0.045;
const HIGHLIGHT_WIDTH = 0.5;
const HIGHLIGHT_HEIGHT = 0.022;
const ANCHOR_WIDTH = 0.2;
const ANCHOR_HEIGHT = 0.028;
const ANCHOR_FLASH_SECONDS = 0.5;

const PULSE_TRAVEL_SECONDS = 1.6;
const PULSE_CYCLE_SECONDS = 7.0;
const THREAD_SEGMENTS = 48;

/**
 * Three beads per thread, staggered along the same curve. Bloom fuses them
 * into one tapering comet, which buys a trail for the cost of two extra
 * spheres and no orientation maths at all.
 */
const TRAIL = [
  { lag: 0.0, radius: 0.055, gain: 5.0 },
  { lag: 0.018, radius: 0.042, gain: 2.2 },
  { lag: 0.04, radius: 0.03, gain: 0.9 },
] as const;

interface ThreadSpec {
  readonly id: string;
  readonly sheet: SheetSpec;
  /** Source position in sheet-local units, as a fraction of width/height. */
  readonly u: number;
  readonly v: number;
  /** Anchor position on the profile sheet, same units. */
  readonly anchorU: number;
  readonly anchorV: number;
  /** Signed, so the bundle fans out instead of stacking. */
  readonly bow: number;
  /** Offset into the shared pulse cycle. */
  readonly phase: number;
  /** Where the bead parks when motion is off. */
  readonly stillU: number;
}

/**
 * Anchors sit right of centre on the profile sheet and below the subheading,
 * and the bows are small: a thread here is a filament, not an arc. Large bows
 * turned the first render into four sweeping curves that read as a chart line
 * — the exact iconography this scene exists to get away from.
 */
const THREADS: readonly ThreadSpec[] = [
  {
    id: "t1",
    sheet: SOURCE_SHEETS[0] ?? PROFILE_SHEET,
    u: -0.1,
    v: 0.2,
    anchorU: 0.04,
    anchorV: -0.02,
    bow: 0.35,
    phase: 0.0,
    stillU: 0.68,
  },
  {
    id: "t2",
    sheet: SOURCE_SHEETS[0] ?? PROFILE_SHEET,
    u: 0.14,
    v: 0.06,
    anchorU: 0.065,
    anchorV: -0.06,
    bow: -0.28,
    phase: 1.4,
    stillU: 0.33,
  },
  {
    id: "t3",
    sheet: SOURCE_SHEETS[1] ?? PROFILE_SHEET,
    u: 0.18,
    v: -0.3,
    anchorU: 0.09,
    anchorV: -0.1,
    bow: 0.42,
    phase: 2.8,
    stillU: 0.86,
  },
  {
    id: "t4",
    sheet: SOURCE_SHEETS[2] ?? PROFILE_SHEET,
    u: 0.2,
    v: 0.1,
    anchorU: 0.115,
    anchorV: -0.14,
    bow: -0.22,
    phase: 4.2,
    stillU: 0.47,
  },
];

/** Sheet-local (u, v) to the group's world space, honouring the sheet's tilt. */
function sheetPointToWorld(
  sheet: SheetSpec,
  u: number,
  v: number,
): THREE.Vector3 {
  const holder = new THREE.Object3D();
  holder.position.set(sheet.x, sheet.y, sheet.z);
  holder.rotation.set(0, sheet.rotY, sheet.rotZ);
  holder.updateMatrixWorld(true);
  return new THREE.Vector3(
    u * sheet.width,
    v * sheetHeight(sheet),
    NODE_Z_OFFSET,
  ).applyMatrix4(holder.matrixWorld);
}

function buildThreadCurve(
  from: THREE.Vector3,
  to: THREE.Vector3,
  bow: number,
): THREE.CatmullRomCurve3 {
  const delta = new THREE.Vector3().subVectors(to, from);
  const first = from.clone().addScaledVector(delta, 0.3);
  const second = from.clone().addScaledVector(delta, 0.68);
  first.y += bow;
  first.z -= delta.z * 0.1;
  second.y -= bow * 0.45;
  second.x += bow * 0.35;
  return new THREE.CatmullRomCurve3(
    [from, first, second, to],
    false,
    "catmullrom",
    0.5,
  );
}

// --- Scene pieces --------------------------------------------------------

function Sheet({
  spec,
  texture,
  plane,
  animate,
  children,
}: {
  spec: SheetSpec;
  texture: THREE.CanvasTexture | undefined;
  plane: THREE.PlaneGeometry;
  animate: boolean;
  children?: React.ReactNode;
}) {
  const groupRef = useRef<THREE.Group>(null);
  const depth = depthOf(spec.z);
  const height = sheetHeight(spec);

  const tint = useMemo(
    () => new THREE.Color(TINT_NEAR_COLOR).lerp(new THREE.Color(TINT_FAR_COLOR), depth),
    [depth],
  );
  const opacity = THREE.MathUtils.lerp(OPACITY_NEAR, OPACITY_FAR, depth);
  const parallax = THREE.MathUtils.lerp(PARALLAX_NEAR, PARALLAX_FAR, depth);
  const baseRotZ = spec.rotZ + (spec.flipped ? Math.PI : 0);

  useFrame((state) => {
    const group = groupRef.current;
    if (!animate || group === null || spec.drift === 0) {
      return;
    }
    const t = state.clock.elapsedTime;
    const phase = spec.z;
    group.position.y =
      spec.y + Math.sin(t * spec.drift + phase) * 0.11 * parallax;
    group.position.x =
      spec.x + Math.cos(t * spec.drift * 0.71 + phase) * 0.066 * parallax;
    group.rotation.z =
      baseRotZ + Math.sin(t * spec.drift * 0.53 + phase) * 0.012 * parallax;
  });

  return (
    <group
      ref={groupRef}
      position={[spec.x, spec.y, spec.z]}
      rotation={[0, spec.rotY, baseRotZ]}
    >
      <mesh
        geometry={plane}
        scale={[spec.width, height, 1]}
        renderOrder={Math.round(spec.z * 100)}
        dispose={null}
      >
        <meshBasicMaterial
          map={texture}
          color={tint}
          transparent
          opacity={opacity}
          depthWrite={false}
          side={THREE.DoubleSide}
          toneMapped={false}
        />
      </mesh>
      {children}
    </group>
  );
}

/** A marked line item: a dim rule running left, with a bright node on its end. */
function SourceNode({
  spec,
  plane,
  sphere,
}: {
  spec: ThreadSpec;
  plane: THREE.PlaneGeometry;
  sphere: THREE.SphereGeometry;
}) {
  const nodeColor = useMemo(
    () => new THREE.Color(NODE_COLOR).multiplyScalar(NODE_GAIN),
    [],
  );
  const ruleColor = useMemo(
    () => new THREE.Color(HIGHLIGHT_COLOR).multiplyScalar(HIGHLIGHT_GAIN),
    [],
  );
  const x = spec.u * spec.sheet.width;
  const y = spec.v * sheetHeight(spec.sheet);

  return (
    <group position={[x, y, NODE_Z_OFFSET]}>
      <mesh
        geometry={plane}
        position={[-HIGHLIGHT_WIDTH / 2, 0, 0]}
        scale={[HIGHLIGHT_WIDTH, HIGHLIGHT_HEIGHT, 1]}
        dispose={null}
      >
        <meshBasicMaterial
          color={ruleColor}
          transparent
          opacity={0.5}
          depthWrite={false}
          toneMapped={false}
        />
      </mesh>
      <mesh geometry={sphere} scale={NODE_RADIUS} dispose={null}>
        <meshBasicMaterial color={nodeColor} toneMapped={false} />
      </mesh>
    </group>
  );
}

/**
 * The figure on the profile sheet that a thread lands on. It sits just under
 * the bloom threshold at rest and crosses it when its pulse arrives — "the
 * figure lights up when its source reaches it", which is the whole product in
 * one gesture.
 */
function Anchor({
  spec,
  plane,
  animate,
}: {
  spec: ThreadSpec;
  plane: THREE.PlaneGeometry;
  animate: boolean;
}) {
  const materialRef = useRef<THREE.MeshBasicMaterial>(null);
  const baseColor = useMemo(() => new THREE.Color(ANCHOR_COLOR), []);
  const restColor = useMemo(
    () => new THREE.Color(ANCHOR_COLOR).multiplyScalar(ANCHOR_REST_GAIN),
    [],
  );
  const x = spec.anchorU * PROFILE_SHEET.width;
  const y = spec.anchorV * sheetHeight(PROFILE_SHEET);

  useFrame((state) => {
    const material = materialRef.current;
    if (!animate || material === null) {
      return;
    }
    const local = (state.clock.elapsedTime + spec.phase) % PULSE_CYCLE_SECONDS;
    const since = local - PULSE_TRAVEL_SECONDS;
    const flash =
      since < 0 ? 0 : Math.max(0, 1 - since / ANCHOR_FLASH_SECONDS);
    material.color
      .copy(baseColor)
      .multiplyScalar(ANCHOR_REST_GAIN + ANCHOR_FLASH_GAIN * flash);
  });

  return (
    <mesh
      geometry={plane}
      position={[x, y, NODE_Z_OFFSET]}
      scale={[ANCHOR_WIDTH, ANCHOR_HEIGHT, 1]}
      dispose={null}
    >
      <meshBasicMaterial
        ref={materialRef}
        color={restColor}
        depthWrite={false}
        toneMapped={false}
      />
    </mesh>
  );
}

function Thread({ curve }: { curve: THREE.CatmullRomCurve3 }) {
  const points = useMemo(() => curve.getPoints(THREAD_SEGMENTS), [curve]);
  return (
    <>
      <Line
        points={points}
        color={THREAD_HALO_COLOR}
        transparent
        opacity={0.15}
        lineWidth={4.5}
        depthWrite={false}
        toneMapped={false}
      />
      <Line
        points={points}
        color={THREAD_CORE_COLOR}
        transparent
        opacity={0.55}
        lineWidth={1.6}
        depthWrite={false}
        toneMapped={false}
      />
    </>
  );
}

function TrailBead({
  curve,
  lag,
  radius,
  gain,
  phase,
  stillU,
  animate,
  sphere,
}: {
  curve: THREE.CatmullRomCurve3;
  lag: number;
  radius: number;
  gain: number;
  phase: number;
  stillU: number;
  animate: boolean;
  sphere: THREE.SphereGeometry;
}) {
  const meshRef = useRef<THREE.Mesh>(null);
  const color = useMemo(
    () => new THREE.Color(PULSE_COLOR).multiplyScalar(gain),
    [gain],
  );
  const stillPosition = useMemo(
    () => curve.getPointAt(THREE.MathUtils.clamp(stillU - lag, 0, 1)),
    [curve, stillU, lag],
  );

  useFrame((state) => {
    const mesh = meshRef.current;
    if (!animate || mesh === null) {
      return;
    }
    const local = (state.clock.elapsedTime + phase) % PULSE_CYCLE_SECONDS;
    const travelling = local < PULSE_TRAVEL_SECONDS;
    // Without this the bead parks on the anchor between cycles and the scene
    // reads as broken rather than idle.
    mesh.visible = travelling;
    if (!travelling) {
      return;
    }
    const raw = local / PULSE_TRAVEL_SECONDS;
    const eased = raw * raw * (3 - 2 * raw);
    const u = THREE.MathUtils.clamp(eased - lag, 0, 1);
    curve.getPointAt(u, mesh.position);
    mesh.scale.setScalar(radius * (0.4 + 0.6 * Math.sin(raw * Math.PI)));
  });

  return (
    <mesh
      ref={meshRef}
      geometry={sphere}
      position={stillPosition}
      scale={radius}
      dispose={null}
    >
      <meshBasicMaterial color={color} toneMapped={false} />
    </mesh>
  );
}

/**
 * The sheets that carry nodes, the sheet that carries the anchors, and the
 * threads between them, in one group.
 *
 * Rigidity invariant: nothing in here may drift relative to anything else in
 * here. Thread geometry is built once from the endpoints computed below, so
 * giving a node-carrying sheet its own motion would leave the threads
 * visibly detached from their nodes. Parallax is the decorative field's job.
 */
function ProvenanceSystem({
  animate,
  resources,
}: {
  animate: boolean;
  resources: SharedResources;
}) {
  const curves = useMemo(
    () =>
      THREADS.map((thread) => ({
        spec: thread,
        curve: buildThreadCurve(
          sheetPointToWorld(thread.sheet, thread.u, thread.v),
          sheetPointToWorld(PROFILE_SHEET, thread.anchorU, thread.anchorV),
          thread.bow,
        ),
      })),
    [],
  );

  return (
    <group>
      <Sheet
        spec={PROFILE_SHEET}
        texture={resources.pages[PROFILE_SHEET.page]}
        plane={resources.plane}
        animate={animate}
      >
        {THREADS.map((thread) => (
          <Anchor
            key={thread.id}
            spec={thread}
            plane={resources.plane}
            animate={animate}
          />
        ))}
      </Sheet>

      {SOURCE_SHEETS.map((sheet) => (
        <Sheet
          key={sheet.id}
          spec={sheet}
          texture={resources.pages[sheet.page]}
          plane={resources.plane}
          animate={animate}
        >
          {THREADS.filter((thread) => thread.sheet.id === sheet.id).map(
            (thread) => (
              <SourceNode
                key={thread.id}
                spec={thread}
                plane={resources.plane}
                sphere={resources.sphere}
              />
            ),
          )}
        </Sheet>
      ))}

      {curves.map(({ spec, curve }) => (
        <group key={spec.id}>
          <Thread curve={curve} />
          {TRAIL.map((bead) => (
            <TrailBead
              key={bead.lag}
              curve={curve}
              lag={bead.lag}
              radius={bead.radius}
              gain={bead.gain}
              phase={spec.phase}
              stillU={spec.stillU}
              animate={animate}
              sphere={resources.sphere}
            />
          ))}
        </group>
      ))}
    </group>
  );
}

function SheetField({
  animate,
  resources,
}: {
  animate: boolean;
  resources: SharedResources;
}) {
  return (
    <>
      {DECOR_SHEETS.map((sheet) => (
        <Sheet
          key={sheet.id}
          spec={sheet}
          texture={resources.pages[sheet.page]}
          plane={resources.plane}
          animate={animate}
        />
      ))}
    </>
  );
}

/**
 * The one breathing group. Periods are deliberately incommensurate (roughly
 * 74s, 101s, 57s and 86s) so the motion never visibly returns to where it
 * started — a hero that loops on a short cycle reads as a screensaver.
 */
function Composition({
  animate,
  resources,
}: {
  animate: boolean;
  resources: SharedResources;
}) {
  const groupRef = useRef<THREE.Group>(null);

  useFrame((state) => {
    const group = groupRef.current;
    if (!animate || group === null) {
      return;
    }
    const t = state.clock.elapsedTime;
    group.rotation.y = Math.sin(t * 0.085) * 0.03;
    group.rotation.x = Math.sin(t * 0.062 + 1.2) * 0.018;
    group.position.y = Math.sin(t * 0.11) * 0.055;
    group.position.x = Math.sin(t * 0.073 + 0.6) * 0.07;
  });

  return (
    <group ref={groupRef}>
      <SheetField animate={animate} resources={resources} />
      <ProvenanceSystem animate={animate} resources={resources} />
      <Sparkles
        count={animate ? 90 : 40}
        scale={[20, 11, 16]}
        size={1.3}
        speed={animate ? 0.16 : 0}
        color={SPARKLE_COLOR}
        opacity={0.3}
      />
    </group>
  );
}

function Scene({ animate }: { animate: boolean }) {
  const resources = useSharedResources();
  const viewportWidth = useThree((state) => state.viewport.width);

  // Solve for the offset that puts the composition's centre of mass at the
  // target NDC, rather than deriving it from viewport width directly: the
  // content column is anchored left, so the scene has to hold its position
  // relative to the *frame edge* at every aspect ratio, not drift with it.
  const scale = THREE.MathUtils.clamp(
    viewportWidth / COMPOSITION_REFERENCE_WIDTH,
    COMPOSITION_MIN_SCALE,
    1,
  );
  const shiftX =
    COMPOSITION_TARGET_NDC_X * (viewportWidth / 2) -
    COMPOSITION_CENTER_X * scale;

  return (
    <group position={[shiftX, 0, 0]} scale={scale}>
      <Composition animate={animate} resources={resources} />
    </group>
  );
}

function Effects() {
  return (
    <EffectComposer multisampling={4} enableNormalPass={false} stencilBuffer={false}>
      <Bloom
        mipmapBlur
        intensity={BLOOM_INTENSITY}
        luminanceThreshold={BLOOM_LUMINANCE_THRESHOLD}
        luminanceSmoothing={BLOOM_LUMINANCE_SMOOTHING}
        radius={BLOOM_RADIUS}
        levels={BLOOM_LEVELS}
      />
      <Vignette offset={VIGNETTE_OFFSET} darkness={VIGNETTE_DARKNESS} eskil={false} />
    </EffectComposer>
  );
}

/**
 * Under `frameloop="demand"` the composer drives rendering from a prioritised
 * frame callback, and the first composed frame can be missed while it sizes
 * its render targets — which shows up as a black canvas for exactly the
 * readers who asked for less motion, and so is easy to ship unnoticed. Nudge
 * it on mount, once more shortly after, and again on resize.
 */
function StillFrameNudge({ active }: { active: boolean }) {
  const invalidate = useThree((state) => state.invalidate);
  const width = useThree((state) => state.size.width);
  const height = useThree((state) => state.size.height);

  useEffect(() => {
    if (!active) {
      return;
    }
    invalidate();
    const timer = window.setTimeout(() => {
      invalidate();
    }, 150);
    return () => {
      window.clearTimeout(timer);
    };
  }, [active, invalidate, width, height]);

  return null;
}

/**
 * This canvas is the first thing every visitor's GPU meets and cannot be
 * scrolled past, so it gives up resolution rather than frame rate on weak
 * hardware. `AdaptiveDpr` would be the wrong tool — it keys off pointer-driven
 * regression, and this canvas has pointer events disabled entirely.
 */
function DprGovernor() {
  const setDpr = useThree((state) => state.setDpr);
  return (
    <PerformanceMonitor
      bounds={() => [50, 60]}
      flipflops={2}
      onDecline={() => {
        setDpr(1);
      }}
      onFallback={() => {
        setDpr(1);
      }}
    />
  );
}

export function HeroCanvas({ reducedMotion }: { reducedMotion: boolean }) {
  const animate = !reducedMotion;

  return (
    <Canvas
      dpr={[1, 1.5]}
      // `flat` is NoToneMapping: every hex below is the colour that reaches
      // the bloom pass, which is what makes the threshold maths above hold.
      flat
      // Context MSAA would be allocated and never used — the scene renders
      // into the composer's own target. `multisampling` on EffectComposer is
      // the setting that actually antialiases this scene.
      gl={{ antialias: false, alpha: false }}
      camera={{ position: [0, 0, 9], fov: 42, near: 0.1, far: 60 }}
      frameloop={reducedMotion ? "demand" : "always"}
      style={{ pointerEvents: "none" }}
    >
      <color attach="background" args={[BACKGROUND_COLOR]} />
      <StillFrameNudge active={reducedMotion} />
      {animate ? <DprGovernor /> : null}
      <Scene animate={animate} />
      <Effects />
    </Canvas>
  );
}
