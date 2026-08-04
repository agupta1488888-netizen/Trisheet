"use client";

/**
 * The hero's centrepiece: a filing rendered as a physical object.
 *
 * Six translucent slabs stacked into a single monolith on a machined base —
 * financial statements, notes, MD&A, risk factors, exhibits, XBRL. At rest it
 * reads as one solid block. Hovered, the layers part slightly. When a report
 * is requested they lift in sequence, bottom of the document to top, with
 * fine data lines drawn between them: the filing being read, one section at a
 * time.
 *
 * Deliberately not a chart, not a hologram, not a glowing anything. The
 * reference points are product photography and industrial design — Braun,
 * Rams, a Vision Pro press shot — which is why the camera runs a long lens
 * (fov 30) rather than a wide one. Long lenses flatten perspective and read
 * as "photographed"; wide lenses read as "video game".
 *
 * Monochrome by construction. There is no colour anywhere in this file except
 * neutral greys and white; the material's `chromaticAberration` supplies the
 * only tint, and it is barely perceptible. Any hue introduced here would
 * break the register the rest of the page is holding.
 *
 * Performance notes, in the order they matter:
 *   - `transmissionSampler` puts every slab on the renderer's single shared
 *     transmission pass. Without it each of the six materials allocates and
 *     renders its own buffer every frame, which is roughly a 6x cost and the
 *     difference between 60fps and 20fps on integrated graphics.
 *   - The environment is built from `Lightformer` geometry rather than an HDR
 *     preset, so there is no texture to download and nothing to fail offline
 *     or behind a strict CSP.
 *   - `PerformanceMonitor` drops DPR before it drops frames.
 *   - Labels are baked canvas textures, not `troika` text: no font fetch, no
 *     async layout, deterministic across machines.
 *
 * Loaded through `next/dynamic` with `ssr: false`, so three.js never enters
 * the server payload or blocks first paint.
 */

import { useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import {
  Environment,
  Lightformer,
  PerformanceMonitor,
  MeshTransmissionMaterial,
  RoundedBox,
} from "@react-three/drei";
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

const SLAB_WIDTH = 3.1;
const SLAB_DEPTH = 2.15;
const SLAB_HEIGHT = 0.26;
const SLAB_RADIUS = 0.035;
const SLAB_SMOOTHNESS = 3;

/** Near-zero at rest so the stack reads as one machined block, not a pile. */
const GAP_REST = 0.012;
const GAP_HOVER = 0.13;
/** How far a slab rises out of the stack while it is being read. */
const GAP_READING = 0.4;

const BASE_WIDTH = 3.42;
const BASE_DEPTH = 2.44;
const BASE_HEIGHT = 0.44;

// --- Reading sequence ----------------------------------------------------

const READ_STAGGER = 0.16;
const READ_RISE = 0.55;
const READ_HOLD = 0.5;
const READ_RETURN = 0.75;
const READ_TOTAL =
  LAYERS.length * READ_STAGGER + READ_RISE + READ_HOLD + READ_RETURN;

// --- Motion --------------------------------------------------------------

const FLOAT_AMPLITUDE = 0.045;
const FLOAT_SPEED = 0.5;
const SPIN_SPEED = 0.075;
const TILT_X = 0.16;
const PARALLAX_X = 0.16;
const PARALLAX_Y = 0.1;
const DAMP_SLOW = 2.4;
const DAMP_FAST = 6;

// --- Palette -------------------------------------------------------------


const GLASS_COLOR = "#c9ccd2";
/**
 * What the transmission pass samples where the scene is empty. Without an
 * explicit dark value the glass samples the studio lights and renders as a
 * solid pale block — the single thing most likely to make this look wrong.
 */
const GLASS_BACKGROUND = new THREE.Color("#0a0a0d");
const GLASS_ATTENUATION = "#4a4f57";
const BASE_COLOR = "#101013";
const LABEL_COLOR = "rgba(255,255,255,0.72)";
const PARTICLE_COLOR = "#ffffff";
const DATALINE_COLOR = "#ffffff";

/** Deterministic, so the particle field is art-directed rather than random. */
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
// Baked once into a canvas texture each. Small caps, wide tracking, the
// typographic register of a spec sheet rather than a UI.

const LABEL_TEXTURE_W = 1024;
const LABEL_TEXTURE_H = 128;
const LABEL_PLANE_W = 1.5;
const LABEL_PLANE_H = LABEL_PLANE_W * (LABEL_TEXTURE_H / LABEL_TEXTURE_W);

function createLabelTexture(text: string): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = LABEL_TEXTURE_W;
  canvas.height = LABEL_TEXTURE_H;
  const ctx = canvas.getContext("2d");
  if (ctx !== null) {
    ctx.clearRect(0, 0, LABEL_TEXTURE_W, LABEL_TEXTURE_H);
    ctx.font =
      "500 54px ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillStyle = LABEL_COLOR;
    // Manual tracking: canvas 2D has no letter-spacing in every engine we
    // ship to, and the wide tracking is most of what makes this read as a
    // machined label rather than body text.
    let x = 8;
    for (const character of text) {
      ctx.fillText(character, x, LABEL_TEXTURE_H / 2);
      x += ctx.measureText(character).width + 3.5;
    }
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  texture.needsUpdate = true;
  return texture;
}

interface Resources {
  readonly labels: readonly THREE.CanvasTexture[];
  readonly particles: THREE.BufferGeometry;
}

const PARTICLE_COUNT = 420;

function useResources(): Resources {
  const resources = useMemo<Resources>(() => {
    const random = createRandom(0x7a1e);
    const stackHeight = LAYERS.length * (SLAB_HEIGHT + GAP_REST);
    const positions = new Float32Array(PARTICLE_COUNT * 3);
    for (let i = 0; i < PARTICLE_COUNT; i += 1) {
      positions[i * 3] = (random() - 0.5) * SLAB_WIDTH * 0.92;
      positions[i * 3 + 1] = (random() - 0.5) * stackHeight;
      positions[i * 3 + 2] = (random() - 0.5) * SLAB_DEPTH * 0.92;
    }
    const particles = new THREE.BufferGeometry();
    particles.setAttribute(
      "position",
      new THREE.BufferAttribute(positions, 3),
    );
    return {
      labels: LAYERS.map((layer) => createLabelTexture(layer.label)),
      particles,
    };
  }, []);

  useEffect(() => {
    return () => {
      for (const label of resources.labels) {
        label.dispose();
      }
      resources.particles.dispose();
    };
  }, [resources]);

  return resources;
}

// --- Shared animation state ---------------------------------------------
// Held in refs and mutated in `useFrame`. Driving six slabs through React
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

/** How far layer `index` has lifted, 0..1, given seconds into the sequence. */
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

function Slab({
  index,
  label,
  drive,
}: {
  index: number;
  label: THREE.CanvasTexture | undefined;
  drive: React.RefObject<Drive>;
}) {
  const groupRef = useRef<THREE.Group>(null);
  const labelRef = useRef<THREE.Mesh>(null);

  // Measured from the middle of the stack so the monolith stays centred as
  // the gap opens, rather than growing downward off its own base.
  const offsetFromCentre = index - (LAYERS.length - 1) / 2;

  useFrame(() => {
    const group = groupRef.current;
    const state = drive.current;
    if (group === null || state === null) {
      return;
    }
    const gap = THREE.MathUtils.lerp(GAP_REST, GAP_HOVER, state.separation);
    const lift = readProgress(index, state.readTime) * GAP_READING;
    group.position.y =
      -offsetFromCentre * (SLAB_HEIGHT + gap) + lift * offsetFromCentre * 0.18 +
      lift;

    const labelMesh = labelRef.current;
    if (labelMesh !== null) {
      const material = labelMesh.material;
      if (material instanceof THREE.MeshBasicMaterial) {
        // Labels are for inspection, not decoration: they fade up only once
        // the stack has opened enough for them to sit in clear space.
        material.opacity =
          0.28 + 0.62 * Math.max(state.separation, Math.min(lift / GAP_READING, 1));
      }
    }
  });

  return (
    <group ref={groupRef}>
      <RoundedBox
        args={[SLAB_WIDTH, SLAB_HEIGHT, SLAB_DEPTH]}
        radius={SLAB_RADIUS}
        smoothness={SLAB_SMOOTHNESS}
        creaseAngle={0.4}
      >
        <MeshTransmissionMaterial
          transmissionSampler
          samples={4}
          resolution={256}
          transmission={1}
          thickness={1.1}
          roughness={0.09}
          ior={1.46}
          chromaticAberration={0.045}
          anisotropicBlur={0.3}
          distortion={0.06}
          distortionScale={0.18}
          temporalDistortion={0}
          color={GLASS_COLOR}
          background={GLASS_BACKGROUND}
          attenuationDistance={1.1}
          attenuationColor={GLASS_ATTENUATION}
        />
      </RoundedBox>

      {/* A hairline along the top face. Real glass catches light on its
          edges, and this is what stops the slabs reading as flat quads. */}
      <mesh position={[0, SLAB_HEIGHT / 2 + 0.001, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[SLAB_WIDTH * 0.995, SLAB_DEPTH * 0.995]} />
        <meshBasicMaterial
          color="#ffffff"
          transparent
          opacity={0.075}
          depthWrite={false}
        />
      </mesh>

      {label === undefined ? null : (
        <mesh
          ref={labelRef}
          position={[
            -SLAB_WIDTH / 2 + LABEL_PLANE_W / 2 + 0.14,
            0,
            SLAB_DEPTH / 2 + 0.012,
          ]}
        >
          <planeGeometry args={[LABEL_PLANE_W, LABEL_PLANE_H]} />
          <meshBasicMaterial
            map={label}
            transparent
            opacity={0.28}
            depthWrite={false}
            toneMapped={false}
          />
        </mesh>
      )}
    </group>
  );
}

/**
 * The fine vertical rules drawn in the space a lifted slab leaves behind.
 * They only exist while the sequence is running, which is the whole point:
 * they are the visual claim that something is being read.
 */
function DataLines({ drive }: { drive: React.RefObject<Drive> }) {
  const groupRef = useRef<THREE.Group>(null);
  const columns = useMemo(() => {
    const random = createRandom(0x2f19);
    return LAYERS.slice(0, -1).flatMap((layer, index) =>
      Array.from({ length: 5 }, (_, line) => ({
        key: `${layer.id}-${line}`,
        index,
        x: (random() - 0.5) * SLAB_WIDTH * 0.86,
        z: (random() - 0.5) * SLAB_DEPTH * 0.72,
      })),
    );
  }, []);

  useFrame(() => {
    const group = groupRef.current;
    const state = drive.current;
    if (group === null || state === null) {
      return;
    }
    const active = state.readTime >= 0;
    group.visible = active;
    if (!active) {
      return;
    }
    for (const child of group.children) {
      const index = child.userData.layerIndex;
      if (typeof index !== "number") {
        continue;
      }
      const progress = readProgress(index, state.readTime);
      child.scale.y = Math.max(progress, 0.001);
      if (child instanceof THREE.Mesh) {
        const material = child.material;
        if (material instanceof THREE.MeshBasicMaterial) {
          material.opacity = 0.34 * Math.sin(progress * Math.PI);
        }
      }
    }
  });

  const stackHeight = LAYERS.length * (SLAB_HEIGHT + GAP_REST);

  return (
    <group ref={groupRef} visible={false}>
      {columns.map((column) => (
        <mesh
          key={column.key}
          position={[
            column.x,
            stackHeight / 2 -
              (column.index + 0.5) * (SLAB_HEIGHT + GAP_REST) -
              SLAB_HEIGHT / 2,
            column.z,
          ]}
          userData={{ layerIndex: column.index }}
        >
          <boxGeometry args={[0.004, GAP_READING, 0.004]} />
          <meshBasicMaterial
            color={DATALINE_COLOR}
            transparent
            opacity={0}
            depthWrite={false}
            toneMapped={false}
          />
        </mesh>
      ))}
    </group>
  );
}

function Particles({ geometry }: { geometry: THREE.BufferGeometry }) {
  return (
    <points geometry={geometry} dispose={null}>
      <pointsMaterial
        color={PARTICLE_COLOR}
        size={0.012}
        sizeAttenuation
        transparent
        opacity={0.28}
        depthWrite={false}
        toneMapped={false}
      />
    </points>
  );
}

function Base() {
  return (
    <group position={[0, -(LAYERS.length * (SLAB_HEIGHT + GAP_REST)) / 2 - BASE_HEIGHT / 2 - 0.02, 0]}>
      <RoundedBox
        args={[BASE_WIDTH, BASE_HEIGHT, BASE_DEPTH]}
        radius={0.03}
        smoothness={3}
        creaseAngle={0.4}
      >
        <meshStandardMaterial
          color={BASE_COLOR}
          roughness={0.34}
          metalness={0.85}
        />
      </RoundedBox>
      {/* Top chamfer catch-light. */}
      <mesh position={[0, BASE_HEIGHT / 2 + 0.001, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[BASE_WIDTH * 0.99, BASE_DEPTH * 0.99]} />
        <meshBasicMaterial
          color="#ffffff"
          transparent
          opacity={0.045}
          depthWrite={false}
        />
      </mesh>

      {/* A soft pool of light on the floor. Cheaper than a shadow pass and it
          does the same job: without something under it the object reads as
          floating in a void rather than standing on a surface. */}
      <mesh
        position={[0, -BASE_HEIGHT / 2 - 0.004, 0]}
        rotation={[-Math.PI / 2, 0, 0]}
      >
        <circleGeometry args={[3.4, 64]} />
        <meshBasicMaterial
          color="#ffffff"
          transparent
          opacity={0.05}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
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
    // delta is clamped because a backgrounded tab returns one enormous frame,
    // which would otherwise snap the whole rig to its target in one step.
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

    // Parallax leads the spin rather than replacing it, so the object never
    // stops moving but always leans toward the reader.
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
    <group ref={groupRef} rotation={[TILT_X, 0.42, 0]}>
      {LAYERS.map((layer, index) => (
        <Slab
          key={layer.id}
          index={index}
          label={resources.labels[index]}
          drive={driveRef}
        />
      ))}
      <Particles geometry={resources.particles} />
      <DataLines drive={driveRef} />
      <Base />
    </group>
  );
}

/**
 * Studio lighting built from geometry rather than an HDR file: three soft
 * boxes standing in for a key light, a fill and a rim. This is what puts the
 * long specular streaks along the slab edges, and it downloads nothing.
 */
function Studio() {
  return (
    <Environment resolution={256}>
      <Lightformer
        form="rect"
        intensity={2.1}
        color="#ffffff"
        position={[0, 5, -2]}
        scale={[10, 6, 1]}
        rotation={[Math.PI / 2, 0, 0]}
      />
      <Lightformer
        form="rect"
        intensity={0.85}
        color="#ffffff"
        position={[-5, 1, 1]}
        scale={[3, 8, 1]}
        rotation={[0, Math.PI / 2, 0]}
      />
      <Lightformer
        form="rect"
        intensity={0.85}
        color="#ffffff"
        position={[5, 0, 2]}
        scale={[3, 8, 1]}
        rotation={[0, -Math.PI / 2, 0]}
      />
      <Lightformer
        form="circle"
        intensity={0.4}
        color="#ffffff"
        position={[0, -4, 1]}
        scale={[6, 6, 1]}
        rotation={[-Math.PI / 2, 0, 0]}
      />
    </Environment>
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
 * Under `frameloop="demand"` nothing schedules a frame after the environment
 * and transmission buffers finish sizing, so the first composed image can be
 * missed entirely — a black canvas for exactly the readers who asked for less
 * motion. Nudge it on mount and on resize.
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
    // Transparent, with no `<color attach="background">`: the hero owns the
    // page colour and the object floats on it, so there is no seam where the
    // canvas ends. Pointer events stay enabled — R3F derives `state.pointer`
    // from events on the canvas element, and disabling them would silently
    // kill the parallax while leaving everything else working.
    <Canvas
      dpr={[1, 1.75]}
      gl={{ antialias: true, alpha: true }}
      camera={{ position: [0, 0.5, 11.2], fov: 30, near: 0.1, far: 40 }}
      frameloop={reducedMotion ? "demand" : "always"}
    >
      <StillFrameNudge active={reducedMotion} />
      {animate ? <DprGovernor /> : null}
      <ambientLight intensity={0.12} />
      <Studio />
      <Monolith hovered={hovered} readToken={readToken} animate={animate} />
    </Canvas>
  );
}
