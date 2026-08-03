"use client";

/**
 * The cinematic visual behind the assistant showcase section.
 *
 * Isolated into its own client-only module so `ai-financial-agent-section.tsx`
 * can load it through `next/dynamic` with `ssr: false` — three.js and the
 * fiber renderer never enter the server-rendered payload or block first
 * paint on the input screen.
 *
 * The scene is deliberately simple: a slowly rotating wireframe form with a
 * drifting field of points around it, evoking "financial data made
 * legible" without trying to depict anything literal. `animate` gated by
 * the caller's `prefers-reduced-motion` read (see `hooks/use-reduced-motion`)
 * turns the rotation and drift off and renders one still frame instead of
 * looping.
 */

import { useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Float, Sparkles } from "@react-three/drei";
import type { Mesh } from "three";

/** Input screen's own emerald/slate accent — not the report view's palette. */
const MESH_COLOR = "#34d399";
const POINT_COLOR = "#cbd5e1";

function DataMesh({ animate }: { animate: boolean }) {
  const meshRef = useRef<Mesh>(null);

  useFrame((_state, delta) => {
    const mesh = meshRef.current;
    if (!animate || mesh === null) {
      return;
    }
    mesh.rotation.y += delta * 0.08;
    mesh.rotation.x += delta * 0.035;
  });

  return (
    <Float
      speed={animate ? 1.1 : 0}
      rotationIntensity={animate ? 0.25 : 0}
      floatIntensity={animate ? 0.5 : 0}
    >
      <mesh ref={meshRef}>
        <icosahedronGeometry args={[2.2, 1]} />
        <meshBasicMaterial color={MESH_COLOR} wireframe transparent opacity={0.35} />
      </mesh>
    </Float>
  );
}

export function AgentCanvas({ reducedMotion }: { reducedMotion: boolean }) {
  return (
    <Canvas
      dpr={[1, 1.5]}
      gl={{ antialias: true, alpha: true }}
      camera={{ position: [0, 0, 6.5], fov: 42 }}
      frameloop={reducedMotion ? "demand" : "always"}
      style={{ pointerEvents: "none" }}
    >
      <ambientLight intensity={0.8} />
      <DataMesh animate={!reducedMotion} />
      <Sparkles
        count={reducedMotion ? 30 : 140}
        scale={[9, 5.5, 4]}
        size={1.6}
        speed={reducedMotion ? 0 : 0.25}
        color={POINT_COLOR}
        opacity={0.55}
      />
    </Canvas>
  );
}
