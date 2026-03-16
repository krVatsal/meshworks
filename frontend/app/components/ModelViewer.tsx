"use client";

import { useRef, useEffect, useState, type CSSProperties } from "react";
import * as THREE from "three";

interface ModelData {
  type: string;
  title: string;
  url?: string;
  embed_url?: string;
}

export default function ModelViewer({
  model,
  highlightLabel,
}: {
  model: ModelData | null;
  highlightLabel?: string | null;
}) {
  if (!model || model.type === "none") {
    return <NoModelState />;
  }

  if (model.type === "sketchfab") {
    return (
      <SketchfabViewer
        embedUrl={model.embed_url ?? ""}
        title={model.title}
        highlightLabel={highlightLabel}
      />
    );
  }

  if (model.type === "glb" || model.type === "gltf") {
    return <GltfViewer src={model.url ?? ""} highlightLabel={highlightLabel ?? null} />;
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

function GltfViewer({ src, highlightLabel }: { src: string; highlightLabel: string | null }) {
  const mountRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<string | null>(
    "Loading Three.js renderer..."
  );
  const [outlineColor] = useState(new THREE.Color(0x00f0ff));
  const outlineLayerRef = useRef<THREE.Scene | null>(null);
  const materialDefaultsRef = useRef<Map<string, any>>(new Map());
  const meshesRef = useRef<THREE.Mesh[]>([]);
  const highlightLabelRef = useRef<string | null>(highlightLabel);

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
    const width = container.clientWidth;
    const height = container.clientHeight;

    // Scene
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0a0a);

    // Camera
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
    camera.position.set(2, 2, 5);

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

    // Load GLB
    let animId: number;
    const load = async () => {
      try {
        const { GLTFLoader } = await import(
          "three/examples/jsm/loaders/GLTFLoader.js"
        );
        const loader = new GLTFLoader();
        // Resolve relative URLs against the frontend origin
        const resolvedSrc = src.startsWith("/")
          ? `${window.location.origin}${src}`
          : src;

        loader.load(
          resolvedSrc,
          (gltf) => {
            scene.add(gltf.scene);
            setStatus(null);
            const meshes: THREE.Mesh[] = [];
            gltf.scene.traverse((child) => {
              if ((child as THREE.Mesh).isMesh) {
                const mesh = child as THREE.Mesh;
                if (Array.isArray(mesh.material)) {
                  mesh.material = mesh.material.map((material) => {
                    const cloned = material.clone();
                    (cloned as THREE.MeshStandardMaterial).vertexColors = true;
                    rememberDefaults(cloned);
                    return cloned;
                  });
                } else {
                  mesh.material = mesh.material.clone();
                  (mesh.material as THREE.MeshStandardMaterial).vertexColors = true;
                  rememberDefaults(mesh.material);
                }
                meshes.push(mesh);
              }
            });
            meshesRef.current = meshes;
            applyHighlight(highlightLabelRef.current);
            // Center model
            const box = new THREE.Box3().setFromObject(gltf.scene);
            const center = box.getCenter(new THREE.Vector3());
            gltf.scene.position.sub(center);
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
      scene.children.forEach((c) => {
        if ((c as THREE.Group).isGroup) c.rotation.y += 0.003;
      });
      
      // Use post-processing composer if available, otherwise fallback to basic render
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
      renderer.dispose();
      meshesRef.current = [];
      materialDefaultsRef.current.clear();
      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement);
      }
    };
  }, [src]);

  useEffect(() => {
    // Update ref for use in other contexts
    highlightLabelRef.current = highlightLabel;
    
    // Immediately trigger highlighting update when label changes
    if (meshesRef.current.length > 0) {
      applyHighlight(highlightLabel);
    }
  }, [highlightLabel]);

  return (
    <div
      className="relative w-full h-full bg-obsidian"
      data-testid="three-viewer"
    >
      {status && <ViewerLoader label={status} />}
      <div ref={mountRef} className="w-full h-full" />
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
