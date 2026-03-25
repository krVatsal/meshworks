"use client";

import { useRef, useEffect, useState, type CSSProperties } from "react";
import * as THREE from "three";

interface ModelData {
  type: string;
  title: string;
  url?: string;
  embed_url?: string;
  originalUrl?: string;
}

export default function ModelViewer({
  model,
  highlightLabel,
  animating,
  onAnimationEnd,
}: {
  model: ModelData | null;
  highlightLabel?: string | null;
  animating?: { segments: {name: string; description: string}[] } | null;
  onAnimationEnd?: () => void;
}) {
  if (!model || model.type === "none") {
    return <NoModelState />;
  }

  // All approved models are now served as GLB from /api/output/ — use Three.js
  if (model.url) {
    return <GltfViewer 
      src={model.url} 
      originalSrc={model.originalUrl ?? null}
      highlightLabel={highlightLabel ?? null}
      animating={animating}
      onAnimationEnd={onAnimationEnd}
    />;
  }

  // Fallback only: embed-only Sketchfab models that couldn't be downloaded
  if (model.type === "sketchfab" && model.embed_url) {
    return (
      <SketchfabViewer
        embedUrl={model.embed_url}
        title={model.title}
        highlightLabel={highlightLabel}
      />
    );
  }

  return <NoModelState />;
}

function SketchfabViewer({
  embedUrl,
  title,
  highlightLabel,
}: {
  embedUrl: string;
  title: string;
  highlightLabel?: string | null;
}) {
  const [loaded, setLoaded] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const apiRef = useRef<any>(null);
  const nodeMapRef = useRef<Record<string, number>>({});

  // Extract UID from embed URL e.g. /models/{uid}/embed
  const uid = embedUrl.match(/models\/([a-f0-9]+)/)?.[1] ?? "";

  useEffect(() => {
    if (!uid || !iframeRef.current) return;

    // Load Sketchfab API script once
    const existing = document.querySelector('script[data-sketchfab-api]');
    const initViewer = () => {
      const SF = (window as any).Sketchfab;
      if (!SF || !iframeRef.current) return;
      const client = new SF(iframeRef.current);
      client.init(uid, {
        success: (api: any) => {
          apiRef.current = api;
          api.start();
          api.addEventListener("viewerready", () => {
            setLoaded(true);
            api.getNodeMap((_err: any, nodes: any) => {
              if (_err || !nodes) return;
              const map: Record<string, number> = {};
              Object.values(nodes).forEach((node: any) => {
                if (node.name) map[node.name.toLowerCase()] = node.instanceID;
              });
              nodeMapRef.current = map;
            });
          });
        },
        error: () => console.warn("[Sketchfab] Viewer init failed"),
      });
    };

    if (existing) {
      initViewer();
    } else {
      const s = document.createElement("script");
      s.src = "https://static.sketchfab.com/api/sketchfab-viewer-1.12.1.js";
      s.setAttribute("data-sketchfab-api", "1");
      s.onload = initViewer;
      document.head.appendChild(s);
    }
  }, [uid]);

  // Highlight / unhighlight when label changes
  useEffect(() => {
    const api = apiRef.current;
    if (!api) return;

    api.getNodeMap((_err: any, nodes: any) => {
      if (_err || !nodes) return;

      // Show all first
      Object.values(nodes).forEach((node: any) => {
        if (node.type === "MatrixTransform") api.show(node.instanceID);
      });

      if (!highlightLabel) return;

      const needle = highlightLabel.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
      let bestId: number | null = null;
      let bestScore = -1;

      Object.entries(nodeMapRef.current).forEach(([name, id]) => {
        let score = 0;
        if (name === needle) score = 100;
        else if (name.includes(needle) || needle.includes(name)) score = 80;
        else {
          const overlap = needle.split(" ").filter(t => t && name.includes(t)).length;
          score = overlap * 20;
        }
        if (score > bestScore) { bestScore = score; bestId = id; }
      });

      if (bestId === null || bestScore < 20) return;

      // Hide everything except match
      Object.values(nodes).forEach((node: any) => {
        if (node.type === "MatrixTransform" && node.instanceID !== bestId) {
          api.hide(node.instanceID);
        }
      });
      api.focusOnNode(bestId);
    });
  }, [highlightLabel]);

  return (
    <div className="relative w-full h-full bg-obsidian overflow-hidden" data-testid="sketchfab-viewer">
      {!loaded && <ViewerLoader label="Initializing Sketchfab Renderer" />}
      <iframe
        ref={iframeRef}
        src=""
        id="api-frame"
        title={title}
        allow="autoplay; fullscreen; xr-spatial-tracking"
        allowFullScreen
        className="w-full h-full"
      />
      <CornerMarkers />
    </div>
  );
}

function normalizeName(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function bestMeshMatch(meshes: THREE.Mesh[], label: string): THREE.Mesh | null {
  const normalizedLabel = normalizeName(label);
  if (!normalizedLabel) return null;

  let best: THREE.Mesh | null = null;
  let bestScore = -1;

  for (const mesh of meshes) {
    const meshName = normalizeName(mesh.name || "");
    if (!meshName) continue;

    let score = 0;
    if (meshName === normalizedLabel) score = 100;
    else if (meshName.includes(normalizedLabel) || normalizedLabel.includes(meshName)) score = 85;
    else {
      const meshTokens = new Set(meshName.split(" ").filter(Boolean));
      const labelTokens = normalizedLabel.split(" ").filter(Boolean);
      let overlap = 0;
      for (const token of labelTokens) {
        if (meshTokens.has(token)) overlap += 1;
      }
      if (labelTokens.length > 0) {
        score = Math.round((overlap / labelTokens.length) * 70);
      }
    }

    if (score > bestScore) {
      bestScore = score;
      best = mesh;
    }
  }

  return bestScore >= 30 ? best : null;
}

function GltfViewer({ src, originalSrc, highlightLabel, animating, onAnimationEnd }: { 
  src: string;
  originalSrc?: string | null;
  highlightLabel: string | null;
  animating?: { segments: {name: string; description: string}[] } | null;
  onAnimationEnd?: () => void;
}) {
  const mountRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<string | null>(
    "Loading Three.js renderer..."
  );
  const [outlineColor] = useState(new THREE.Color(0x00f0ff));
  const [showOriginal, setShowOriginal] = useState(false);
  const activeSrc = showOriginal && originalSrc ? originalSrc : src;
  const outlineLayerRef = useRef<THREE.Scene | null>(null);
  const materialDefaultsRef = useRef<Map<string, any>>(new Map());
  const meshesRef = useRef<THREE.Mesh[]>([]);
  const [meshLoadVersion, setMeshLoadVersion] = useState(0);
  const highlightLabelRef = useRef<string | null>(highlightLabel);
  const animatingRef = useRef(animating);
  // after: const animatingRef = useRef(animating);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const snapTargetRef = useRef<{ pos: THREE.Vector3; up: THREE.Vector3 } | null>(null);
  const modelRadiusRef = useRef<number>(5);
  useEffect(() => { animatingRef.current = animating; }, [animating]);

  const getMeshMaterials = (mesh: THREE.Mesh): THREE.Material[] => {
    const material = mesh.material;
    return Array.isArray(material) ? material : [material];
  };

  const rememberDefaults = (material: THREE.Material) => {
    if (materialDefaultsRef.current.has(material.uuid)) return;
    const base: {
      opacity: number;
      transparent: boolean;
      emissiveIntensity?: number;
      emissiveHex?: number;
    } = {
      opacity: material.opacity,
      transparent: material.transparent,
    };

    if ("emissive" in material) {
      const mat = material as THREE.MeshStandardMaterial;
      base.emissiveHex = mat.emissive.getHex();
      base.emissiveIntensity = mat.emissiveIntensity;
    }

    materialDefaultsRef.current.set(material.uuid, base);
  };

  const restoreMaterial = (material: THREE.Material) => {
    const defaults = materialDefaultsRef.current.get(material.uuid);
    if (!defaults) return;

    material.opacity = defaults.opacity;
    material.transparent = defaults.transparent;

    if ("emissive" in material) {
      const mat = material as THREE.MeshStandardMaterial;
      mat.emissive.setHex(defaults.emissiveHex ?? 0x000000);
      mat.emissiveIntensity = defaults.emissiveIntensity ?? 0;
    }

    material.needsUpdate = true;
  };

  const applyHighlight = (label: string | null) => {
    const meshes = meshesRef.current;
    if (!meshes.length) return;

    // STEP 1: Clear ALL previous highlights first
    if (!label) {
      // Complete reset: restore all meshes to original state
      for (const mesh of meshes) {
        for (const material of getMeshMaterials(mesh)) {
          restoreMaterial(material);
        }
        mesh.userData.isHighlighted = false;
      }
      return;
    }

    // STEP 2: Find the new matching mesh
    const matchedMesh = bestMeshMatch(meshes, label);
    
    // STEP 3: Apply new highlighting to all meshes
    for (const mesh of meshes) {
      const isMatch = mesh.uuid === (matchedMesh?.uuid);
      mesh.userData.isHighlighted = isMatch;

      for (const material of getMeshMaterials(mesh)) {
        rememberDefaults(material);

        if (isMatch && matchedMesh) {
          // ✨ NEW HIGHLIGHTED MESH - Advanced Technique
          material.opacity = 1;
          material.transparent = false;

          if ("emissive" in material) {
            const mat = material as THREE.MeshStandardMaterial;
            mat.emissive.setHex(0x00f0ff);
            mat.emissiveIntensity = 1.2;
          }

          if ("roughness" in material) {
            const mat = material as THREE.MeshStandardMaterial;
            mat.roughness = Math.max(0.2, mat.roughness * 0.6);
          }

          if ("metalness" in material) {
            const mat = material as THREE.MeshStandardMaterial;
            mat.metalness = Math.min(1, mat.metalness + 0.3);
          }
        } else {
          // 🌫️ BACKGROUND MESHES - Fade out previous
          material.opacity = 0.15;
          material.transparent = true;

          if ("emissive" in material) {
            const mat = material as THREE.MeshStandardMaterial;
            mat.emissive.setHex(0x000000);
            mat.emissiveIntensity = 0;
          }

          if ("roughness" in material) {
            const mat = material as THREE.MeshStandardMaterial;
            mat.roughness = Math.min(1, mat.roughness + 0.4);
          }

          if ("metalness" in material) {
            const mat = material as THREE.MeshStandardMaterial;
            mat.metalness = Math.max(0, mat.metalness - 0.2);
          }
        }

        material.needsUpdate = true;
      }
    }
  };

  useEffect(() => {
    if (!mountRef.current) return;

    const container = mountRef.current;
    setMeshLoadVersion(0);
    const width = container.clientWidth;
    const height = container.clientHeight;

    // Scene
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0a0a);

    // Camera
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
    
    camera.position.set(2, 2, 5);
    cameraRef.current = camera;

    // Renderer with better settings for highlighting
    const renderer = new THREE.WebGLRenderer({ 
      antialias: true,
      alpha: true,
      powerPreference: 'high-performance'
    });
    renderer.setSize(width, height);
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.toneMappingExposure = 1.2;
    container.appendChild(renderer.domElement);

    // Setup Post-Processing for Bloom Effect
    let effectComposer: any;
    let bloomPass: any;
    const setupPostProcessing = async () => {
      try {
        const { EffectComposer } = await import("three/examples/jsm/postprocessing/EffectComposer.js");
        const { RenderPass } = await import("three/examples/jsm/postprocessing/RenderPass.js");
        const { UnrealBloomPass } = await import("three/examples/jsm/postprocessing/UnrealBloomPass.js");

        effectComposer = new EffectComposer(renderer);
        const renderPass = new RenderPass(scene, camera);
        effectComposer.addPass(renderPass);

        // Bloom pass for glowing highlights
        bloomPass = new UnrealBloomPass(
          new THREE.Vector2(width, height),
          1.5,      // strength
          0.4,      // radius
          0.85      // threshold (only glow bright colors)
        );
        effectComposer.addPass(bloomPass);
      } catch (e) {
        console.log("Bloom post-processing unavailable, using basic highlighting");
      }
    };
    setupPostProcessing();

    // Lights with dynamic adjustment for highlighting
    const ambient = new THREE.AmbientLight(0xffffff, 0.5);
    scene.add(ambient);
    const highlightLight = new THREE.SpotLight(0x00f0ff, 1, 100, Math.PI / 4, 0.5, 1);
    highlightLight.position.set(3, 8, 3);
    highlightLight.target.position.set(0, 0, 0);
    scene.add(highlightLight);
    scene.add(highlightLight.target);

    // Additional directional light for overall illumination
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
    dirLight.position.set(5, 10, 5);
    scene.add(dirLight);

    // Store lights for dynamic adjustment
    const lightsRef = { highlightLight, dirLight };

    // Orbit controls — mouse drag to rotate, scroll to zoom, right-click to pan
    let controls: any;
    const loadControls = async () => {
      try {
        const { OrbitControls } = await import(
          "three/examples/jsm/controls/OrbitControls.js"
        );
        controls = new OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;
        controls.dampingFactor = 0.05;
        controls.minDistance = 0.1;
        controls.maxDistance = 1000;
        controls.enablePan = true;
      } catch {
        console.warn("OrbitControls unavailable");
      }
    };
    loadControls();

    // Load GLB
    let animId: number;

    const load = async () => {
      try {
        const { GLTFLoader } = await import(
          "three/examples/jsm/loaders/GLTFLoader.js"
        );
        const loader = new GLTFLoader();
        // Resolve relative URLs against the frontend origin
        const resolvedSrc = activeSrc.startsWith("/")
          ? `${window.location.origin}${activeSrc}`
          : activeSrc;

        loader.load(
          resolvedSrc,
          (gltf) => {
            scene.add(gltf.scene);
            setStatus(null);

            // Center + fit camera to model bounds
            const box = new THREE.Box3().setFromObject(gltf.scene);
            const center = box.getCenter(new THREE.Vector3());
            const size = box.getSize(new THREE.Vector3());
            const maxDim = Math.max(size.x, size.y, size.z);
            gltf.scene.position.sub(center);  // center at origin

            const fov = camera.fov * (Math.PI / 180);
            const dist = Math.abs(maxDim / (2 * Math.tan(fov / 2))) * 1.8;
            camera.position.set(0, 0, dist);
            modelRadiusRef.current = dist;
            camera.near = dist / 100;
            camera.far = dist * 100;
            camera.updateProjectionMatrix();

            const meshes: THREE.Mesh[] = [];
            gltf.scene.traverse((child) => {
              if ((child as THREE.Mesh).isMesh) {
                const mesh = child as THREE.Mesh;
                if (Array.isArray(mesh.material)) {
                  mesh.material = mesh.material.map((material) => {
                    const cloned = material.clone();
                    cloned.side = THREE.DoubleSide;
                    rememberDefaults(cloned);
                    return cloned;
                  });
                } else {
                  mesh.material = mesh.material.clone();
                  mesh.material.side = THREE.DoubleSide;
                  rememberDefaults(mesh.material);
                }
                meshes.push(mesh);
              }
            });
            meshesRef.current = meshes;
            setMeshLoadVersion((version) => version + 1);
            applyHighlight(highlightLabelRef.current);
          },
          undefined,
          () => setStatus("Error loading model via Three.js")
        );
      } catch {
        setStatus("Three.js loader unavailable");
      }
    };
    load();

    // Animation loop with post-processing
    const animate = () => {
      animId = requestAnimationFrame(animate);

      // Axis snap animation — disables orbit controls while lerping
      if (snapTargetRef.current) {
        if (controls) controls.enabled = false;
        camera.position.lerp(snapTargetRef.current.pos, 0.12);
        camera.up.lerp(snapTargetRef.current.up, 0.12);
        camera.lookAt(0, 0, 0);
        if (camera.position.distanceTo(snapTargetRef.current.pos) < 0.05) {
          camera.position.copy(snapTargetRef.current.pos);
          camera.up.copy(snapTargetRef.current.up);
          camera.lookAt(0, 0, 0);
          snapTargetRef.current = null;
          if (controls) { controls.enabled = true; controls.update(); }
        }
      } else {
        controls?.update();
      }

      if (effectComposer) {
        effectComposer.render();
      } else {
        renderer.render(scene, camera);
      }
    };
    animate();

    const handleResize = () => {
      const w = container.clientWidth;
      const h = container.clientHeight;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
      
      // Update post-processing composer size
      if (effectComposer) {
        effectComposer.setSize(w, h);
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      cancelAnimationFrame(animId);
      window.removeEventListener("resize", handleResize);
      controls?.dispose();
      renderer.dispose();
      meshesRef.current = [];
      materialDefaultsRef.current.clear();
      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement);
      }
    };
  }, [activeSrc]);

  useEffect(() => {
    // Update ref for use in other contexts
    highlightLabelRef.current = highlightLabel;
    
    // Immediately trigger highlighting update when label changes
    if (meshesRef.current.length > 0) {
      applyHighlight(highlightLabel);
    }
  }, [highlightLabel]);

  useEffect(() => {
    if (!animating || !meshesRef.current.length) return;

    let cancelled = false;
    

    const runSequence = async () => {
      for (const seg of animating.segments) {
        if (cancelled) break;

        const mesh = bestMeshMatch(meshesRef.current, seg.name);
        applyHighlight(mesh ? seg.name : null);

        await new Promise<void>(resolve => {
          if (!window.speechSynthesis) { resolve(); return; }
          window.speechSynthesis.cancel();
          const u = new SpeechSynthesisUtterance(seg.description);
          u.rate = 0.9;
          u.onend = () => resolve();
          u.onerror = () => resolve();
          window.speechSynthesis.speak(u);
        });

        if (cancelled) break;
        await new Promise(r => setTimeout(r, 200));
      }

      if (!cancelled) {
        applyHighlight(null);
        onAnimationEnd?.();
      }
    };

    runSequence();
    return () => {
      cancelled = true;
      window.speechSynthesis?.cancel();
    };
  }, [animating, meshLoadVersion]);

  return (
    <div
      className="relative w-full h-full bg-obsidian"
      data-testid="three-viewer"
    >
      {status && <ViewerLoader label={status} />}
      <div ref={mountRef} className="w-full h-full" />
      {originalSrc && (
        <button
          onClick={() => setShowOriginal(v => !v)}
          className="absolute top-3 right-16 z-10 px-3 py-1.5 bg-black/70 border border-cyber/40 text-cyber font-mono text-[10px] tracking-widest hover:bg-cyber/10 transition-colors"
        >
          {showOriginal ? "SEGMENTED VIEW" : "ORIGINAL TEXTURE"}
        </button>
      )}

      <AxisGizmo
        cameraRef={cameraRef}
        snapTargetRef={snapTargetRef}
        modelRadiusRef={modelRadiusRef}
      />
      <CornerMarkers />
    </div>
  );
}

function ViewerLoader({ label }: { label: string }) {
  const reverseStyle: CSSProperties = {
    animationDirection: "reverse",
    animationDuration: "2s",
  };

  return (
    <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-obsidian">
      <div className="relative w-16 h-16 mb-6">
        <div className="absolute inset-0 border border-cyber/30 spin-slow" />
        <div
          className="absolute inset-2 border border-cyber/50 spin-slow"
          style={reverseStyle}
        />
        <div className="absolute inset-4 bg-cyber/20 animate-pulse" />
      </div>
      <p className="font-mono text-xs text-cyber/70 tracking-widest animate-pulse">
        {label}
      </p>
      <p className="font-mono text-[10px] text-slate-600 tracking-widest mt-2">
        NEURAL_RENDERER::INIT
      </p>
    </div>
  );
}

function CornerMarkers() {
  return (
    <>
      <div className="absolute top-3 left-3 w-4 h-4 border-t border-l border-cyber/40 pointer-events-none" />
      <div className="absolute top-3 right-3 w-4 h-4 border-t border-r border-cyber/40 pointer-events-none" />
      <div className="absolute bottom-3 left-3 w-4 h-4 border-b border-l border-cyber/40 pointer-events-none" />
      <div className="absolute bottom-3 right-3 w-4 h-4 border-b border-r border-cyber/40 pointer-events-none" />
    </>
  );
}

function NoModelState() {
  return (
    <div
      className="w-full h-full flex flex-col items-center justify-center bg-obsidian"
      data-testid="no-model-state"
    >
      <div className="w-16 h-16 border border-white/10 flex items-center justify-center mb-6">
        <div className="w-8 h-8 border border-white/20 rotate-45" />
      </div>
      <p className="font-rajdhani text-white/40 text-lg tracking-widest">
        NO MODEL FOUND
      </p>
      <p className="font-mono text-xs text-slate-600 mt-2 tracking-wide">
        Try a different search query
      </p>
    </div>
  );
}

function AxisGizmo({
  cameraRef,
  snapTargetRef,
  modelRadiusRef,
}: {
  cameraRef: React.RefObject<THREE.PerspectiveCamera | null>;
  snapTargetRef: React.RefObject<{ pos: THREE.Vector3; up: THREE.Vector3 } | null>;
  modelRadiusRef: React.RefObject<number>;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number>(0);

  // Each world axis: direction, color, label, and which side the camera up-vector should use
  const AXES = [
    { label: "X", dir: [1, 0, 0], neg: [-1, 0, 0], up: [0, 1, 0],  negUp: [0, 1, 0],  color: "#ef4444", negColor: "#7f1d1d" },
    { label: "Y", dir: [0, 1, 0], neg: [0, -1, 0], up: [0, 0, -1], negUp: [0, 0, 1],  color: "#22c55e", negColor: "#14532d" },
    { label: "Z", dir: [0, 0, 1], neg: [0, 0, -1], up: [0, 1, 0],  negUp: [0, 1, 0],  color: "#3b82f6", negColor: "#1e3a8a" },
  ] as const;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const SIZE = 90, CX = SIZE / 2, CY = SIZE / 2, R = 32;

    const project = (m: number[], vx: number, vy: number, vz: number) => ({
      x: CX + (m[0] * vx + m[1] * vy + m[2] * vz) * R,
      y: CY - (m[4] * vx + m[5] * vy + m[6] * vz) * R,
      z:       m[8] * vx + m[9] * vy + m[10]* vz,   // depth for sorting
    });

    const draw = () => {
      ctx.clearRect(0, 0, SIZE, SIZE);
      const camera = cameraRef.current;
      if (!camera) { rafRef.current = requestAnimationFrame(draw); return; }

      camera.updateMatrixWorld();
      const m = camera.matrixWorld.elements;

      // Build all 6 axis endpoints (pos + neg for each)
      type Item = { x: number; y: number; z: number; color: string; r: number; label?: string };
      const items: Item[] = [];
      for (const ax of AXES) {
        const p = project(m, ax.dir[0], ax.dir[1], ax.dir[2]);
        const n = project(m, ax.neg[0], ax.neg[1], ax.neg[2]);
        items.push({ ...p, color: ax.color,    r: 9,  label: ax.label });
        items.push({ ...n, color: ax.negColor, r: 5 });
      }
      // Paint back-to-front
      items.sort((a, b) => a.z - b.z);

      for (const item of items) {
        // Line from center
        ctx.beginPath();
        ctx.moveTo(CX, CY);
        ctx.lineTo(item.x, item.y);
        ctx.strokeStyle = item.color;
        ctx.lineWidth = item.r > 6 ? 1.5 : 1;
        ctx.globalAlpha = item.r > 6 ? 0.85 : 0.35;
        ctx.stroke();
        ctx.globalAlpha = 1;

        // Circle
        ctx.beginPath();
        ctx.arc(item.x, item.y, item.r, 0, Math.PI * 2);
        ctx.fillStyle = item.color;
        ctx.globalAlpha = item.r > 6 ? 1 : 0.45;
        ctx.fill();
        ctx.globalAlpha = 1;

        // Label
        if (item.label) {
          ctx.font = "bold 9px monospace";
          ctx.fillStyle = "#fff";
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(item.label, item.x, item.y);
        }
      }

      // Center dot
      ctx.beginPath();
      ctx.arc(CX, CY, 2.5, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255,255,255,0.5)";
      ctx.fill();

      rafRef.current = requestAnimationFrame(draw);
    };

    draw();
    return () => cancelAnimationFrame(rafRef.current);
  }, []);

  const handleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const camera = cameraRef.current;
    if (!camera) return;

    const rect = (e.target as HTMLCanvasElement).getBoundingClientRect();
    // Map click to canvas coords (canvas is 90x90, rendered at CSS size)
    const SIZE = 90, CX = SIZE / 2, CY = SIZE / 2, R = 32;
    const mx = ((e.clientX - rect.left) / rect.width) * SIZE;
    const my = ((e.clientY - rect.top) / rect.height) * SIZE;

    camera.updateMatrixWorld();
    const m = camera.matrixWorld.elements;

    const project = (vx: number, vy: number, vz: number) => ({
      x: CX + (m[0]*vx + m[1]*vy + m[2]*vz) * R,
      y: CY - (m[4]*vx + m[5]*vy + m[6]*vz) * R,
    });

    // All 6 snappable views
    const views = AXES.flatMap((ax) => [
      { dir: ax.dir, up: ax.up },
      { dir: ax.neg, up: ax.negUp },
    ]);

    let best: (typeof views)[0] | null = null;
    let bestDist = 14; // click radius in canvas px

    for (const v of views) {
      const p = project(v.dir[0], v.dir[1], v.dir[2]);
      const d = Math.hypot(mx - p.x, my - p.y);
      if (d < bestDist) { bestDist = d; best = v; }
    }

    if (!best) return;
    const dist = modelRadiusRef.current;
    snapTargetRef.current = {
      pos: new THREE.Vector3(...best.dir).multiplyScalar(dist),
      up:  new THREE.Vector3(...best.up),
    };
  };

  return (
    <div
      className="absolute bottom-12 right-3 pointer-events-auto"
      style={{ width: 90, height: 90, zIndex: 10 }}
      title="Click an axis to snap view"
    >
      {/* subtle border matching the cyber aesthetic */}
      <div className="absolute inset-0 border border-cyber/20" />
      <canvas
        ref={canvasRef}
        width={90}
        height={90}
        onClick={handleClick}
        style={{ cursor: "crosshair", width: "100%", height: "100%" }}
      />
    </div>
  );
}