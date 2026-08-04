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
 * On the glass, since it is the whole object: a transmissive material is
 * almost entirely a picture of its surroundings. An earlier version of this
 * file rendered as a black slab, and the cause was not the shader — it was
 * that `background` had been set to near-black and the environment held four
 * dim lights. Transmission of 1 against a black backdrop is, correctly, black.
 * If this ever looks wrong again, check what the material can *see* before
 * touching its coefficients.
 *
 * Performance notes, in the order they matter:
 *   - Each slab renders its own transmission buffer every frame. That is the
 *     price of letting the layers refract each other, which is the point of
 *     the object; `transmissionSampler` would collapse them onto the
 *     renderer's single shared pass, but that pass excludes transmissive
 *     meshes, so the stack would read as six flat cards.
 *   - Six passes is the ceiling this scene can afford. `backside` was tried
 *     and removed: it adds a second pass per slab, and twelve passes plus a
 *     512 environment froze the renderer outright. `thickness` carries the
 *     sense of volume on its own. Do not re-enable it without measuring.
 *   - `ContactShadows` uses `frames={1}`, baking once instead of re-rendering
 *     the scene from below every frame. The object turns slowly enough that a
 *     static shadow is indistinguishable, and it was a full extra pass.
 *   - Resolution (128) and samples (2) are held deliberately low. If frames
 *     drop further, lower those before removing refraction entirely.
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
  ContactShadows,
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

const FLOAT_AMPLITUDE = 0.055;
const FLOAT_SPEED = 0.34;
const SPIN_SPEED = 0.035;
const TILT_X = 0.16;
const PARALLAX_X = 0.16;
const PARALLAX_Y = 0.1;
const DAMP_SLOW = 2.4;
const DAMP_FAST = 6;

// --- Palette -------------------------------------------------------------


const GLASS_COLOR = "#dadde1";
/**
 * What the transmission pass samples where the scene is empty.
 *
 * This was previously near-black (#0a0a0d), which is why the object rendered
 * as a black slab: transmission is 1, so the material is almost entirely
 * "what is behind it", and what was behind it was black. Nothing behind the
 * monolith is lit, so this stands in for a lit studio backdrop. It is the
 * single most load-bearing value in the file.
 */
const GLASS_BACKGROUND = new THREE.Color("#3f434a");
/**
 * Beer-Lambert absorption. At 1.1 the diagonal path through six stacked
 * slabs was several attenuation lengths, so the glass absorbed almost
 * everything before it reached the eye. Four units is roughly the depth of
 * the whole stack, which leaves it smoked rather than opaque.
 */
const GLASS_ATTENUATION = "#70757d";
const GLASS_ATTENUATION_DISTANCE = 2.4;
const BASE_COLOR = "#16161a";
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
        {/* The `transmissionSampler` flag is deliberately absent. With it,
            drei skips its own framebuffer pass and falls back to the built-in
            transmission target, which excludes transmissive meshes by design —
            so the slabs could not refract each other, and the stack read as
            six flat cards. Without it each slab renders its own buffer and you
            can see layers through layers, which is the whole point of the
            object. The cost is one extra scene pass per slab, paid for by
            dropping resolution and samples. */}
        <MeshTransmissionMaterial
          samples={2}
          resolution={128}
          transmission={1}
          thickness={2}
          roughness={0.1}
          ior={1.5}
          chromaticAberration={0.03}
          anisotropicBlur={0.25}
          distortion={0.04}
          distortionScale={0.14}
          temporalDistortion={0}
          color={GLASS_COLOR}
          background={GLASS_BACKGROUND}
          attenuationDistance={GLASS_ATTENUATION_DISTANCE}
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
          opacity={0.03}
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
        {/* Matte, not metal. At metalness 0.85 this was ~100% environment
            reflection, and the environment is nearly black, so it rendered as
            a black void. A machined anodised pedestal is a dielectric. */}
        <meshStandardMaterial
          color={BASE_COLOR}
          roughness={0.62}
          metalness={0}
          envMapIntensity={0.3}
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
 * The studio, built from geometry rather than an HDR file so nothing has to
 * download and nothing fails offline or behind a strict CSP.
 *
 * A glass object is almost entirely a picture of its surroundings, so this
 * rig *is* the material — no amount of tuning the shader compensates for an
 * environment that is mostly black, which is what the previous four dim
 * lightformers amounted to.
 *
 * Five sources, in the order a product photographer would place them: a broad
 * overhead softbox for the top faces, two narrow high-intensity strips raking
 * the left and right edges (these are what draw the bright white chamfer
 * lines), a back light for silhouette separation, and a dim floor bounce so
 * the underside is not dead. Resolution stays at 256, and is not a knob to
 * turn: 512 was measured, and together with the transmission passes it froze
 * the renderer outright — see the performance notes at the top of this file.
 * The rim strips hold at 256 at the widths above, which is what that budget
 * buys.
 */
function Studio() {
  return (
    <Environment resolution={256}>
      {/* Key: broad, soft, overhead. */}
      <Lightformer
        form="rect"
        intensity={3.2}
        color="#ffffff"
        position={[0, 6, 1]}
        scale={[12, 7, 1]}
        rotation={[Math.PI / 2, 0, 0]}
      />
      {/* Rim left and right: narrow and bright. Width is deliberately small —
          a wide source washes the face, a narrow one draws an edge. */}
      <Lightformer
        form="rect"
        intensity={10}
        color="#ffffff"
        position={[-4.6, 1.2, 1.4]}
        scale={[0.7, 9, 1]}
        rotation={[0, Math.PI / 2, 0]}
      />
      <Lightformer
        form="rect"
        intensity={7.5}
        color="#ffffff"
        position={[4.6, 0.4, 1.8]}
        scale={[0.7, 9, 1]}
        rotation={[0, -Math.PI / 2, 0]}
      />
      {/* Back light, for separation from a near-black page. */}
      <Lightformer
        form="rect"
        intensity={2.6}
        color="#ffffff"
        position={[0, 1.5, -6]}
        scale={[7, 5, 1]}
        rotation={[0, 0, 0]}
      />
      {/* Floor bounce. Dim on purpose: enough to keep the underside of the
          stack and the pedestal's chamfer alive, not enough to flatten the
          contact shadow underneath. */}
      <Lightformer
        form="circle"
        intensity={0.7}
        color="#ffffff"
        position={[0, -4.5, 1]}
        scale={[8, 8, 1]}
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
      // AgX rather than R3F's ACESFilmic default. ACES has an aggressive toe
      // that crushes shadows, which on an object this dark removed most of
      // the tonal separation in the glass before it ever reached the screen.
      // AgX holds the low end and rolls off highlights without desaturating,
      // which is why it has become the default for product renders.
      gl={{
        antialias: true,
        alpha: true,
        toneMapping: THREE.AgXToneMapping,
        toneMappingExposure: 1.0,
      }}
      camera={{ position: [0, 0.6, 8.4], fov: 30, near: 0.1, far: 40 }}
      frameloop={reducedMotion ? "demand" : "always"}
    >
      <StillFrameNudge active={reducedMotion} />
      {animate ? <DprGovernor /> : null}
      <ambientLight intensity={0.18} />
      <Studio />
      <Monolith hovered={hovered} readToken={readToken} animate={animate} />

      {/* A real contact shadow, replacing the additive white disc that used to
          sit here. That disc was a *glow* — it lightened exactly where contact
          darkening belongs, which is why the pedestal never looked like it was
          standing on anything. Positioned in world space, outside the rotating
          group, so the shadow stays on the floor while the object turns. */}
      <ContactShadows
        position={[0, -1.32, 0]}
        scale={11}
        blur={2.8}
        opacity={0.75}
        far={4}
        resolution={512}
        color="#000000"
        frames={1}
      />
    </Canvas>
  );
}
