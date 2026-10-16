import { OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import type { Hemi } from "@/api/brain";
import { DIVERGING, lut, lutIndex } from "@/lib/colormaps";

export type View = "lateral" | "medial";

/** fsaverage (x right, y anterior, z superior) to three.js (x anterior, y up, z out of screen). */
function toThree(src: Float32Array, center: number[]): Float32Array {
  const out = new Float32Array(src.length);
  for (let i = 0; i < src.length; i += 3) {
    out[i] = src[i + 1] - center[1];
    out[i + 1] = src[i + 2] - center[2];
    out[i + 2] = src[i] - center[0];
  }
  return out;
}

function baseShade(sulc: Float32Array): Float32Array {
  // Gyri light, sulci dark: the familiar inflated-brain look.
  const out = new Float32Array(sulc.length);
  for (let i = 0; i < sulc.length; i++) out[i] = sulc[i] > 0 ? 0.42 : 0.68;
  return out;
}

function HemiMesh({
  hemi,
  offset,
  frame,
  vertexOffset,
  view,
  colormap,
  thresholdCodes,
}: {
  hemi: Hemi;
  offset: number;
  frame: Uint8Array | null;
  vertexOffset: number;
  view: View;
  colormap: string;
  thresholdCodes: number;
}) {
  const geometry = useMemo(() => {
    const [min, max] = hemi.bbox;
    const center = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2];
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(toThree(hemi.positions, center), 3));
    g.setIndex(new THREE.BufferAttribute(hemi.faces, 1));
    g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(hemi.sulc.length * 3), 3));
    g.computeVertexNormals();
    return g;
  }, [hemi]);
  const shade = useMemo(() => baseShade(hemi.sulc), [hemi]);

  useEffect(() => {
    const colors = geometry.getAttribute("color") as THREE.BufferAttribute;
    const arr = colors.array as Float32Array;
    const table = lut(colormap);
    const diverging = DIVERGING.has(colormap);
    const n = hemi.sulc.length;
    for (let v = 0; v < n; v++) {
      const code = frame ? frame[vertexOffset + v] : 0;
      const idx = lutIndex(code, diverging, thresholdCodes);
      if (idx < 0) {
        arr[v * 3] = arr[v * 3 + 1] = arr[v * 3 + 2] = shade[v];
      } else {
        arr[v * 3] = table[idx * 3];
        arr[v * 3 + 1] = table[idx * 3 + 1];
        arr[v * 3 + 2] = table[idx * 3 + 2];
      }
    }
    colors.needsUpdate = true;
  }, [geometry, frame, vertexOffset, colormap, thresholdCodes, shade, hemi]);

  useEffect(() => () => geometry.dispose(), [geometry]);

  // Lateral: each hemisphere's outer face toward the camera; medial: the inner face.
  const isLeft = hemi.name === "left";
  const facesCameraByDefault = !isLeft; // right hemi's +x (lateral) maps to +z
  const showLateral = view === "lateral";
  const rotY = facesCameraByDefault === showLateral ? 0 : Math.PI;
  return (
    <mesh geometry={geometry} position={[offset, 0, 0]} rotation={[0, rotY, 0]}>
      <meshLambertMaterial vertexColors side={THREE.DoubleSide} />
    </mesh>
  );
}

export function BrainView({
  hemis,
  frame,
  view,
  colormap,
  thresholdCodes,
  className,
}: {
  hemis: Hemi[];
  frame: Uint8Array | null;
  view: View;
  colormap: string;
  thresholdCodes: number;
  className?: string;
}) {
  const left = hemis.find((h) => h.name === "left")!;
  const right = hemis.find((h) => h.name === "right")!;
  const span = Math.max(left.bbox[1][1] - left.bbox[0][1], right.bbox[1][1] - right.bbox[0][1]);
  const gap = span * 0.55;
  const controls = useRef(null);
  return (
    <div className={className ?? "h-80 w-full rounded-xl bg-black/90"}>
      <Canvas camera={{ position: [0, 0, span * 1.9], fov: 40 }} dpr={[1, 2]}>
        <ambientLight intensity={0.7} />
        <directionalLight position={[0, span, span * 2]} intensity={0.9} />
        <HemiMesh hemi={left} offset={-gap} frame={frame} vertexOffset={0} view={view} colormap={colormap} thresholdCodes={thresholdCodes} />
        <HemiMesh
          hemi={right}
          offset={gap}
          frame={frame}
          vertexOffset={left.sulc.length}
          view={view}
          colormap={colormap}
          thresholdCodes={thresholdCodes}
        />
        <OrbitControls ref={controls} enablePan={false} />
      </Canvas>
    </div>
  );
}
