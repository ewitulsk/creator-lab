import { useQuery } from "@tanstack/react-query";
import { ApiError, api, rawFetch, unwrap } from "./client";

export type BrainMeta = NonNullable<Awaited<ReturnType<typeof fetchMeta>>>;

type TribeAnalyzer = "tribe_video" | "tribe_title";

async function fetchMeta(videoId: number, analyzer: TribeAnalyzer) {
  try {
    return await unwrap(
      api.GET("/api/videos/{video_id}/brain", { params: { path: { video_id: videoId }, query: { analyzer } } }),
    );
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

export function useBrainMeta(videoId: number, analyzer: TribeAnalyzer = "tribe_video") {
  return useQuery({ queryKey: ["brain", videoId, analyzer], queryFn: () => fetchMeta(videoId, analyzer), staleTime: 60_000 });
}

export type Hemi = { name: string; positions: Float32Array; faces: Uint32Array; sulc: Float32Array; bbox: number[][] };

type MeshJson = {
  hemis: {
    name: string;
    n_vertices: number;
    n_faces: number;
    coords: { offset: number; length: number };
    faces: { offset: number; length: number };
    sulc: { offset: number; length: number };
    bbox: number[][];
  }[];
};

export async function loadMesh(): Promise<Hemi[]> {
  const meta: MeshJson = await (await rawFetch("/api/brain/assets/mesh.json")).json();
  const buf = await (await rawFetch("/api/brain/assets/mesh.bin")).arrayBuffer();
  return meta.hemis.map((h) => ({
    name: h.name,
    positions: new Float32Array(buf, h.coords.offset, h.coords.length),
    faces: new Uint32Array(buf, h.faces.offset, h.faces.length),
    sulc: new Float32Array(buf, h.sulc.offset, h.sulc.length),
    bbox: h.bbox,
  }));
}

export function useMesh() {
  return useQuery({ queryKey: ["brain-mesh"], queryFn: loadMesh, staleTime: Infinity, retry: false });
}

/**
 * Lazily loads quantized frames in chunks and keeps them. frame(i) returns the uint8
 * row for timestep i, or null while its chunk is still loading (it starts the load).
 */
export class FrameStore {
  private chunks = new Map<number, Uint8Array>();
  private pending = new Map<number, Promise<void>>();
  readonly chunk: number;
  onLoad: () => void = () => {};

  constructor(
    private url: string,
    readonly nFrames: number,
    readonly nVertices: number,
    chunk = 120,
  ) {
    this.chunk = chunk;
  }

  private load(c: number): Promise<void> {
    const existing = this.pending.get(c);
    if (existing) return existing;
    const p = rawFetch(`${this.url}&start=${c * this.chunk}&count=${this.chunk}`)
      .then((r) => r.arrayBuffer())
      .then((b) => {
        this.chunks.set(c, new Uint8Array(b));
        this.onLoad();
      })
      .finally(() => this.pending.delete(c));
    this.pending.set(c, p);
    return p;
  }

  frame(i: number): Uint8Array | null {
    if (i < 0 || i >= this.nFrames) return null;
    const c = Math.floor(i / this.chunk);
    const data = this.chunks.get(c);
    // Prefetch the next chunk as playback approaches it.
    if ((i % this.chunk) > this.chunk * 0.6 && (c + 1) * this.chunk < this.nFrames && !this.chunks.has(c + 1)) {
      void this.load(c + 1);
    }
    if (!data) {
      void this.load(c);
      return null;
    }
    const row = i - c * this.chunk;
    return data.subarray(row * this.nVertices, (row + 1) * this.nVertices);
  }
}
