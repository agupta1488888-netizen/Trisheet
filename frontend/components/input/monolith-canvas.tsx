"use client";

/**
 * The hero's centrepiece: a filing rendered as an object made of light.
 *
 * Six thin panes held apart in a column — financial statements, notes, MD&A,
 * risk factors, exhibits, XBRL — each outlined in a bright hairline, with the
 * data itself visible inside and between them. Hovered, the stack opens a
 * little further. When a report is requested the panes lift in sequence,
 * bottom of the document to top, with data threads brightening between them.
 *
 * This is deliberately NOT physically-correct glass, and the distinction is
 * the whole reason the file looks the way it does. An earlier version used
 * `MeshTransmissionMaterial` with real refraction, ior 1.5, thickness 2 and a
 * studio HDRI. That is the recipe for thick optical glass, and thick optical
 * glass under studio lighting renders as a polished metal paperweight —
 * correctly, but nothing like the intent. The intent is closer to an
 * architectural drawing lit from within: near-invisible surfaces, emissive
 * edges, and content you can read through every layer.
 *
 * So the construction is inverted. Surfaces are almost transparent and barely
 * lit; the edges are drawn, not shaded; the environment is nearly absent. If
 * this ever starts looking like chrome again, the cause will be someone
 * raising surface opacity or adding a bright environment map.
 *
 * It is also far cheaper than what it replaced. Transmission cost one full
 * scene render per pane per frame — six passes, which froze the renderer
 * outright when a backside pass doubled it. Nothing here renders the scene
 * more than once.
 *
 * Loaded through `next/dynamic` with `ssr: false`, so three.js never enters
 * the server payload or blocks first paint.
 */

import { useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Line, PerformanceMonitor, RoundedBox } from "@react-three/drei";
import * as THREE from "three";

// --- Structure -----------------------------------------------------------
// Authored top-down, the way the document itself is ordered.

interface LayerSpec {
  readonly id: string;
  readonly label: string;
}

const LAYERS: readonly LayerSpec[] = [
  { id: "statements", label: "Financial Statements" },
  { id: "notes", label: "Notes & Footnotes" },
  { id: "mdna", label: "Management Discussion" },
  { id: "risk", label: "Risk Factors" },
  { id: "exhibits", label: "Exhibits" },
  { id: "xbrl", label: "XBRL Data" },
];

const PANE_WIDTH = 3.98;
const PANE_DEPTH = 2.2;
/**
 * Thin. The ratio of thickness to width is what separates "pane of glass"
 * from "block of acrylic" — at the previous 0.26 against 3.1 these read as
 * bricks no matter how they were shaded.
 */
const PANE_THICKNESS = 0.075;
/** Plan-view corner radius. Free of the thickness now that panes are extruded. */
const PANE_CORNER_RADIUS = 0.22;

/**
 * The panes are held apart permanently rather than closed into a monolith.
 * You have to be able to see through the gaps for the layering to read at
 * all; hover only widens what is already open.
 */
const PANE_PITCH_REST = 0.4;
const PANE_PITCH_HOVER = 0.52;
/** Extra lift for the pane currently being read. */
const READ_LIFT = 0.26;

const BASE_WIDTH = 4.34;
const BASE_DEPTH = 2.52;
const BASE_HEIGHT = 0.56;

const STACK_HEIGHT = (LAYERS.length - 1) * PANE_PITCH_REST;

// --- Reading sequence ----------------------------------------------------

const READ_STAGGER = 0.16;
const READ_RISE = 0.55;
const READ_HOLD = 0.5;
const READ_RETURN = 0.75;
const READ_TOTAL =
  LAYERS.length * READ_STAGGER + READ_RISE + READ_HOLD + READ_RETURN;

// --- Motion --------------------------------------------------------------

const FLOAT_AMPLITUDE = 0.05;
const FLOAT_SPEED = 0.32;
const SPIN_SPEED = 0.11;
const TILT_X = 0.14;
const PARALLAX_X = 0.14;
const PARALLAX_Y = 0.09;
const DAMP_SLOW = 2.4;
const DAMP_FAST = 6;

// --- Palette -------------------------------------------------------------
// Monochrome by construction. Everything is white at some opacity; there is
// no hue anywhere in this file.

const SURFACE_COLOR = "#ffffff";
/** Barely there. This is the number that decides glass vs. chrome. */
const SURFACE_OPACITY = 0.15;
const EDGE_COLOR = "#ffffff";
const EDGE_OPACITY = 0.6;
const EDGE_SOFT_OPACITY = 0.12;
const DATA_COLOR = "#ffffff";
const BASE_COLOR = "#141417";
const LABEL_COLOR = "rgba(255,255,255,0.78)";

/** Deterministic, so the data field is art-directed rather than random. */
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

// --- Labels --------------------------------------------------------------

const LABEL_TEXTURE_W = 1024;
const LABEL_TEXTURE_H = 128;
const LABEL_PLANE_W = 1.62;
const LABEL_PLANE_H = LABEL_PLANE_W * (LABEL_TEXTURE_H / LABEL_TEXTURE_W);

function createLabelTexture(text: string): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = LABEL_TEXTURE_W;
  canvas.height = LABEL_TEXTURE_H;
  const ctx = canvas.getContext("2d");
  if (ctx !== null) {
    ctx.clearRect(0, 0, LABEL_TEXTURE_W, LABEL_TEXTURE_H);
    ctx.font =
      "500 50px ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillStyle = LABEL_COLOR;
    // Manual tracking: canvas 2D letter-spacing is not reliable across the
    // engines we ship to, and the wide tracking is most of what makes this
    // read as an engraved label rather than body text.
    let x = 8;
    for (const character of text) {
      ctx.fillText(character, x, LABEL_TEXTURE_H / 2);
      x += ctx.measureText(character).width + 3;
    }
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  texture.needsUpdate = true;
  return texture;
}

/**
 * The pane's own surface: a soft gradient rather than a flat wash, brightest
 * along one edge. A pane filled with a single opacity reads as a wireframe
 * with a tint; the gradient is what makes it read as a sheet catching light.
 * Baked once and shared by all six.
 */
function createSurfaceTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 4;
  canvas.height = 256;
  const ctx = canvas.getContext("2d");
  if (ctx !== null) {
    const gradient = ctx.createLinearGradient(0, 0, 0, 256);
    gradient.addColorStop(0, "rgba(255,255,255,0.16)");
    gradient.addColorStop(0.5, "rgba(255,255,255,0.06)");
    gradient.addColorStop(0.86, "rgba(255,255,255,0.3)");
    gradient.addColorStop(1, "rgba(255,255,255,0.72)");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, 4, 256);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.needsUpdate = true;
  return texture;
}

const ETCH_TEXTURE_W = 1024;
const ETCH_TEXTURE_H = 256;

/** The wordmark machined into the plinth's top face, plus the mark opposite it. */
function createEtchTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = ETCH_TEXTURE_W;
  canvas.height = ETCH_TEXTURE_H;
  const ctx = canvas.getContext("2d");
  if (ctx !== null) {
    ctx.clearRect(0, 0, ETCH_TEXTURE_W, ETCH_TEXTURE_H);
    ctx.textBaseline = "middle";
    // Dimmer than the pane labels. An etch catches a little light; it is not
    // printed on.
    ctx.fillStyle = "rgba(255,255,255,0.3)";
    ctx.font =
      "500 72px ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif";
    ctx.textAlign = "left";
    let x = 60;
    for (const character of "Trisheet") {
      ctx.fillText(character, x, ETCH_TEXTURE_H / 2);
      x += ctx.measureText(character).width + 5;
    }
    ctx.fillStyle = "rgba(255,255,255,0.24)";
    ctx.font =
      "600 64px ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif";
    ctx.textAlign = "right";
    ctx.fillText("T", ETCH_TEXTURE_W - 60, ETCH_TEXTURE_H / 2);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  texture.needsUpdate = true;
  return texture;
}

// --- Data geometry -------------------------------------------------------
// All of it built once, and drawn as three objects total: one point cloud for
// the height field, one line set for the falling threads, one point cloud for
// the scattered motes. Individually meshing these would be hundreds of draw
// calls for something that is meant to read as texture.

const FIELD_COLUMNS = 34;
const FIELD_ROWS = 24;
const FIELD_INSET = 0.66;

/** The dot-matrix wave sitting on the top pane. */
function createFieldGeometry(): THREE.BufferGeometry {
  const positions = new Float32Array(FIELD_COLUMNS * FIELD_ROWS * 3);
  let i = 0;
  for (let column = 0; column < FIELD_COLUMNS; column += 1) {
    for (let row = 0; row < FIELD_ROWS; row += 1) {
      const u = column / (FIELD_COLUMNS - 1) - 0.5;
      const v = row / (FIELD_ROWS - 1) - 0.5;
      positions[i] = u * PANE_WIDTH * FIELD_INSET;
      // Two crossed sines: a readable surface rather than noise, and it holds
      // its shape as the object turns.
      positions[i + 1] =
        Math.sin(u * 5.2) * 0.17 + Math.cos(v * 4.1 + u * 2.2) * 0.115 + 0.14;
      positions[i + 2] = v * PANE_DEPTH * FIELD_INSET;
      i += 3;
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  return geometry;
}

const THREAD_COUNT = 220;

/** Fine vertical strands falling through the whole stack. */
function createThreadGeometry(): THREE.BufferGeometry {
  const random = createRandom(0x51ce);
  const positions = new Float32Array(THREAD_COUNT * 2 * 3);
  const top = STACK_HEIGHT / 2;
  let i = 0;
  for (let thread = 0; thread < THREAD_COUNT; thread += 1) {
    // Two samples averaged pulls the distribution toward the middle, so the
    // threads fall from under the wave rather than across the whole pane.
    const x = ((random() + random()) / 2 - 0.5) * PANE_WIDTH * 0.92;
    const z = ((random() + random()) / 2 - 0.5) * PANE_DEPTH * 0.92;
    const start = top - random() * STACK_HEIGHT * 0.35;
    const length = 0.3 + random() * (STACK_HEIGHT * 0.8);
    positions[i] = x;
    positions[i + 1] = start;
    positions[i + 2] = z;
    positions[i + 3] = x;
    positions[i + 4] = start - length;
    positions[i + 5] = z;
    i += 6;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  return geometry;
}

const MOTE_COUNT = 260;

/** Scattered points suspended between the panes. */
function createMoteGeometry(): THREE.BufferGeometry {
  const random = createRandom(0x0d75);
  const positions = new Float32Array(MOTE_COUNT * 3);
  for (let i = 0; i < MOTE_COUNT; i += 1) {
    positions[i * 3] = (random() - 0.5) * PANE_WIDTH * 0.9;
    positions[i * 3 + 1] = (random() - 0.5) * STACK_HEIGHT * 1.05;
    positions[i * 3 + 2] = (random() - 0.5) * PANE_DEPTH * 0.9;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  return geometry;
}

/**
 * The rounded-rectangle outline of a pane, in the XZ plane.
 *
 * Both the pane solid and its edge hairline are generated from this one
 * shape, so the outline can never drift out of register with the geometry it
 * traces.
 */
function paneShape(inset: number): THREE.Shape {
  const w = (PANE_WIDTH / 2) * inset;
  const d = (PANE_DEPTH / 2) * inset;
  const r = Math.min(PANE_CORNER_RADIUS * inset, w, d);
  const shape = new THREE.Shape();
  shape.moveTo(-w + r, -d);
  shape.lineTo(w - r, -d);
  shape.quadraticCurveTo(w, -d, w, -d + r);
  shape.lineTo(w, d - r);
  shape.quadraticCurveTo(w, d, w - r, d);
  shape.lineTo(-w + r, d);
  shape.quadraticCurveTo(-w, d, -w, d - r);
  shape.lineTo(-w, -d + r);
  shape.quadraticCurveTo(-w, -d, -w + r, -d);
  return shape;
}

function paneOutline(inset: number): [number, number, number][] {
  return paneShape(inset)
    .getPoints(24)
    .map((point): [number, number, number] => [point.x, 0, point.y]);
}

/**
 * The pane solid: a rounded rectangle extruded to its thickness.
 *
 * Deliberately not `RoundedBox`. That primitive rounds every edge by a single
 * radius bounded by the *smallest* dimension — here the 0.075 thickness — so
 * asking for the reference's large plan-view corners produced degenerate
 * geometry and hung the renderer outright. Extruding a shape decouples the
 * two: corners as round as the design wants, thickness as thin as it wants.
 *
 * Built once and shared by all six panes.
 */
function createPaneGeometry(): THREE.ExtrudeGeometry {
  const geometry = new THREE.ExtrudeGeometry(paneShape(1), {
    depth: PANE_THICKNESS,
    bevelEnabled: true,
    bevelThickness: 0.005,
    bevelSize: 0.005,
    bevelSegments: 2,
    curveSegments: 8,
  });
  // Extrude builds in XY along +Z; lay it flat and centre it on its own
  // thickness so `position.y` still addresses the middle of the pane.
  geometry.rotateX(-Math.PI / 2);
  geometry.translate(0, PANE_THICKNESS / 2, 0);
  return geometry;
}

interface Resources {
  readonly labels: readonly THREE.CanvasTexture[];
  readonly surface: THREE.CanvasTexture;
  readonly pane: THREE.ExtrudeGeometry;
  readonly etch: THREE.CanvasTexture;
  readonly field: THREE.BufferGeometry;
  readonly threads: THREE.BufferGeometry;
  readonly motes: THREE.BufferGeometry;
}

function useResources(): Resources {
  const resources = useMemo<Resources>(
    () => ({
      labels: LAYERS.map((layer) => createLabelTexture(layer.label)),
      surface: createSurfaceTexture(),
      pane: createPaneGeometry(),
      etch: createEtchTexture(),
      field: createFieldGeometry(),
      threads: createThreadGeometry(),
      motes: createMoteGeometry(),
    }),
    [],
  );

  useEffect(() => {
    return () => {
      for (const label of resources.labels) {
        label.dispose();
      }
      resources.surface.dispose();
      resources.pane.dispose();
      resources.etch.dispose();
      resources.field.dispose();
      resources.threads.dispose();
      resources.motes.dispose();
    };
  }, [resources]);

  return resources;
}

// --- Shared animation state ---------------------------------------------
// Held in refs and mutated in `useFrame`. Driving six panes through React
// state would re-render the tree every frame; this way the scene graph is
// built once and only matrices change.

interface Drive {
  /** Rest → hover separation, 0..1. */
  separation: number;
  /** Seconds since the reading sequence began; negative when idle. */
  readTime: number;
}

/** Cubic ease-in-out. Smooth at both ends, which is what "premium" means here. */
function ease(t: number): number {
  const clamped = THREE.MathUtils.clamp(t, 0, 1);
  return clamped < 0.5
    ? 4 * clamped * clamped * clamped
    : 1 - Math.pow(-2 * clamped + 2, 3) / 2;
}

/** How far pane `index` has lifted, 0..1, given seconds into the sequence. */
function readProgress(index: number, readTime: number): number {
  if (readTime < 0) {
    return 0;
  }
  const returnStart = LAYERS.length * READ_STAGGER + READ_RISE + READ_HOLD;
  if (readTime >= returnStart) {
    return 1 - ease((readTime - returnStart) / READ_RETURN);
  }
  // Bottom of the document upward: XBRL first, statements last.
  const order = LAYERS.length - 1 - index;
  return ease((readTime - order * READ_STAGGER) / READ_RISE);
}

/**
 * One pane: a nearly-invisible slab, a bright hairline tracing its top edge,
 * a dimmer one under it, and an engraved label.
 *
 * The outline is what you actually see. It is drawn geometry rather than a
 * specular highlight, so it holds at every viewing angle instead of appearing
 * only where a light happens to reflect — which is exactly why the previous
 * shaded version disappeared into the background.
 */
function Pane({
  index,
  label,
  surface,
  geometry,
  drive,
}: {
  index: number;
  label: THREE.CanvasTexture | undefined;
  surface: THREE.CanvasTexture;
  geometry: THREE.ExtrudeGeometry;
  drive: React.RefObject<Drive>;
}) {
  const groupRef = useRef<THREE.Group>(null);
  const offsetFromCentre = index - (LAYERS.length - 1) / 2;
  const outline = useMemo(() => paneOutline(1), []);
  const outlineSoft = useMemo(() => paneOutline(0.985), []);
  // Anchor points for the annotation cards' leader lines. Only some panes
  // carry one, the way the reference only annotates a few.
  const anchor = index === 0 || index === 2 || index === 4;

  useFrame(() => {
    const group = groupRef.current;
    const state = drive.current;
    if (group === null || state === null) {
      return;
    }
    const pitch = THREE.MathUtils.lerp(
      PANE_PITCH_REST,
      PANE_PITCH_HOVER,
      state.separation,
    );
    const lift = readProgress(index, state.readTime) * READ_LIFT;
    group.position.y = -offsetFromCentre * pitch + lift;
  });

  return (
    <group ref={groupRef}>
      <mesh geometry={geometry} dispose={null}>
        <meshBasicMaterial
          color={SURFACE_COLOR}
          map={surface}
          transparent
          opacity={SURFACE_OPACITY}
          depthWrite={false}
          side={THREE.DoubleSide}
          toneMapped={false}
        />
      </mesh>

      {anchor ? (
        <mesh position={[PANE_WIDTH / 2 - 0.02, PANE_THICKNESS / 2, -PANE_DEPTH / 2 + 0.02]}>
          <sphereGeometry args={[0.024, 10, 10]} />
          <meshBasicMaterial color={EDGE_COLOR} toneMapped={false} />
        </mesh>
      ) : null}

      <Line
        points={outline}
        position={[0, PANE_THICKNESS / 2, 0]}
        color={EDGE_COLOR}
        transparent
        opacity={EDGE_OPACITY}
        lineWidth={1.1}
        depthWrite={false}
        toneMapped={false}
      />
      <Line
        points={outlineSoft}
        position={[0, -PANE_THICKNESS / 2, 0]}
        color={EDGE_COLOR}
        transparent
        opacity={EDGE_SOFT_OPACITY}
        lineWidth={1}
        depthWrite={false}
        toneMapped={false}
      />

      {label === undefined ? null : (
        // Lifted clear of the pane's top face. This previously sat at y=0.005
        // while the box's top face is at PANE_THICKNESS/2 (0.0375), so the
        // label plane was *inside* the geometry and never drew. `renderOrder`
        // keeps it above the pane body, which writes no depth.
        <mesh
          position={[
            -PANE_WIDTH / 2 + LABEL_PLANE_W / 2 + 0.16,
            PANE_THICKNESS / 2 + 0.004,
            PANE_DEPTH / 2 - 0.2,
          ]}
          rotation={[-Math.PI / 2, 0, 0]}
          renderOrder={2}
        >
          <planeGeometry args={[LABEL_PLANE_W, LABEL_PLANE_H]} />
          <meshBasicMaterial
            map={label}
            transparent
            opacity={0.92}
            depthWrite={false}
            depthTest={false}
            toneMapped={false}
          />
        </mesh>
      )}
    </group>
  );
}

/**
 * The contents: the height field on the top pane, threads falling through the
 * stack, motes between them. Threads brighten while a report is being read —
 * the one moment the object is doing something rather than being something.
 */
function DataField({ resources, drive }: { resources: Resources; drive: React.RefObject<Drive> }) {
  const threadRef = useRef<THREE.LineSegments>(null);

  useFrame(() => {
    const threads = threadRef.current;
    const state = drive.current;
    if (threads === null || state === null) {
      return;
    }
    const material = threads.material;
    if (material instanceof THREE.LineBasicMaterial) {
      const reading =
        state.readTime < 0
          ? 0
          : Math.sin(
              THREE.MathUtils.clamp(state.readTime / READ_TOTAL, 0, 1) *
                Math.PI,
            );
      material.opacity = 0.1 + 0.34 * reading + 0.06 * state.separation;
    }
  });

  return (
    <group>
      <points
        geometry={resources.field}
        position={[0, STACK_HEIGHT / 2 + PANE_THICKNESS, 0]}
        dispose={null}
      >
        <pointsMaterial
          color={DATA_COLOR}
          size={0.017}
          sizeAttenuation
          transparent
          opacity={0.72}
          depthWrite={false}
          toneMapped={false}
        />
      </points>

      <lineSegments ref={threadRef} geometry={resources.threads} dispose={null}>
        <lineBasicMaterial
          color={DATA_COLOR}
          transparent
          opacity={0.1}
          depthWrite={false}
          toneMapped={false}
        />
      </lineSegments>

      <points geometry={resources.motes} dispose={null}>
        <pointsMaterial
          color={DATA_COLOR}
          size={0.012}
          sizeAttenuation
          transparent
          opacity={0.4}
          depthWrite={false}
          toneMapped={false}
        />
      </points>
    </group>
  );
}

function Base({ etch }: { etch: THREE.CanvasTexture }) {
  const outline = useMemo(
    () =>
      [
        [-BASE_WIDTH / 2, 0, -BASE_DEPTH / 2],
        [BASE_WIDTH / 2, 0, -BASE_DEPTH / 2],
        [BASE_WIDTH / 2, 0, BASE_DEPTH / 2],
        [-BASE_WIDTH / 2, 0, BASE_DEPTH / 2],
        [-BASE_WIDTH / 2, 0, -BASE_DEPTH / 2],
      ] as [number, number, number][],
    [],
  );

  return (
    <group position={[0, -STACK_HEIGHT / 2 - BASE_HEIGHT / 2 - 0.3, 0]}>
      <RoundedBox
        args={[BASE_WIDTH, BASE_HEIGHT, BASE_DEPTH]}
        radius={0.02}
        smoothness={2}
        creaseAngle={0.4}
      >
        {/* Matte, unlit and nearly black. The pedestal is a shadow the object
            stands on, not a feature competing with it. */}
        <meshBasicMaterial color={BASE_COLOR} toneMapped={false} />
      </RoundedBox>
      <Line
        points={outline}
        position={[0, BASE_HEIGHT / 2, 0]}
        color={EDGE_COLOR}
        transparent
        opacity={0.4}
        lineWidth={1}
        depthWrite={false}
        toneMapped={false}
      />
      <Line
        points={outline}
        position={[0, -BASE_HEIGHT / 2, 0]}
        color={EDGE_COLOR}
        transparent
        opacity={0.12}
        lineWidth={1}
        depthWrite={false}
        toneMapped={false}
      />

      {/* "Trisheet" machined into the top face, with the mark at the far end. */}
      <mesh
        position={[0, BASE_HEIGHT / 2 + 0.003, BASE_DEPTH / 2 - 0.34]}
        rotation={[-Math.PI / 2, 0, 0]}
        renderOrder={2}
      >
        <planeGeometry args={[BASE_WIDTH * 0.92, (BASE_WIDTH * 0.92 * ETCH_TEXTURE_H) / ETCH_TEXTURE_W]} />
        <meshBasicMaterial
          map={etch}
          transparent
          opacity={0.95}
          depthWrite={false}
          depthTest={false}
          toneMapped={false}
        />
      </mesh>
    </group>
  );
}

function Monolith({
  hovered,
  readToken,
  animate,
}: {
  hovered: boolean;
  readToken: number;
  animate: boolean;
}) {
  const resources = useResources();
  const groupRef = useRef<THREE.Group>(null);
  const driveRef = useRef<Drive>({ separation: 0, readTime: -1 });
  const tokenRef = useRef(readToken);

  useFrame((state, delta) => {
    const group = groupRef.current;
    const drive = driveRef.current;
    if (group === null) {
      return;
    }
    // Clamped because a backgrounded tab returns one enormous frame, which
    // would otherwise snap the whole rig to its target in a single step.
    const dt = Math.min(delta, 1 / 30);

    if (tokenRef.current !== readToken) {
      tokenRef.current = readToken;
      drive.readTime = readToken > 0 ? 0 : -1;
    }
    if (drive.readTime >= 0) {
      drive.readTime += dt;
      if (drive.readTime > READ_TOTAL) {
        drive.readTime = -1;
      }
    }

    drive.separation = THREE.MathUtils.damp(
      drive.separation,
      hovered ? 1 : 0,
      DAMP_FAST,
      dt,
    );

    if (!animate) {
      return;
    }

    const t = state.clock.elapsedTime;
    group.position.y = Math.sin(t * FLOAT_SPEED) * FLOAT_AMPLITUDE;

    const targetY = t * SPIN_SPEED + state.pointer.x * PARALLAX_X;
    const targetX = TILT_X - state.pointer.y * PARALLAX_Y;
    group.rotation.y = THREE.MathUtils.damp(
      group.rotation.y,
      targetY,
      DAMP_SLOW,
      dt,
    );
    group.rotation.x = THREE.MathUtils.damp(
      group.rotation.x,
      targetX,
      DAMP_SLOW,
      dt,
    );
  });

  return (
    <group ref={groupRef} rotation={[TILT_X, 0.5, 0]}>
      {LAYERS.map((layer, index) => (
        <Pane
          key={layer.id}
          index={index}
          label={resources.labels[index]}
          surface={resources.surface}
          geometry={resources.pane}
          drive={driveRef}
        />
      ))}
      <DataField resources={resources} drive={driveRef} />
      <Base etch={resources.etch} />
    </group>
  );
}

function DprGovernor() {
  const setDpr = useThree((state) => state.setDpr);
  return (
    <PerformanceMonitor
      bounds={() => [55, 60]}
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

/**
 * Under `frameloop="demand"` nothing schedules a frame once the scene settles,
 * so the first composed image can be missed entirely — a blank canvas for
 * exactly the readers who asked for less motion, which is easy to ship
 * unnoticed. Nudge it on mount and on resize.
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
    }, 200);
    return () => {
      window.clearTimeout(timer);
    };
  }, [active, invalidate, width, height]);

  return null;
}

export function MonolithCanvas({
  reducedMotion,
  hovered,
  readToken,
}: {
  reducedMotion: boolean;
  hovered: boolean;
  /** Incremented to run the reading sequence once. */
  readToken: number;
}) {
  const animate = !reducedMotion;

  return (
    // Transparent, with no scene background: the hero owns the page colour and
    // the object floats on it, so there is no seam where the canvas ends.
    // Pointer events stay enabled — R3F derives `state.pointer` from events on
    // the canvas element, and disabling them would silently kill the parallax.
    //
    // No tone mapping. Every material here is unlit and already authored in
    // display values; a filmic curve would only crush the hairlines that carry
    // the whole object.
    <Canvas
      dpr={[1, 1.75]}
      flat
      gl={{ antialias: true, alpha: true }}
      camera={{ position: [0, 0.35, 14.2], fov: 30, near: 0.1, far: 40 }}
      frameloop={reducedMotion ? "demand" : "always"}
    >
      <StillFrameNudge active={reducedMotion} />
      {animate ? <DprGovernor /> : null}
      <Monolith hovered={hovered} readToken={readToken} animate={animate} />
    </Canvas>
  );
}
