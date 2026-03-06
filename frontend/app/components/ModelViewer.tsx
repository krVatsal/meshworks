"use client";

import { useRef, useEffect, useState, type CSSProperties } from "react";
import * as THREE from "three";

interface ModelData {
  type: string;
  title: string;
  url?: string;
  embed_url?: string;
}

export default function ModelViewer({ model }: { model: ModelData | null }) {
  if (!model || model.type === "none") {
    return <NoModelState />;
  }

  if (model.type === "sketchfab") {
    return (
      <SketchfabViewer
        embedUrl={model.embed_url ?? ""}
        title={model.title}
      />
    );
  }

  if (model.type === "glb" || model.type === "gltf") {
    return <GltfViewer src={model.url ?? ""} />;
  }

  return <NoModelState />;
}

function SketchfabViewer({
  embedUrl,
  title,
}: {
  embedUrl: string;
  title: string;
}) {
  const [loaded, setLoaded] = useState(false);

  return (
    <div
      className="relative w-full h-full bg-obsidian overflow-hidden"
      data-testid="sketchfab-viewer"
    >
      {!loaded && <ViewerLoader label="Initializing Sketchfab Renderer" />}
      <iframe
        src={embedUrl}
        title={title}
        frameBorder="0"
        allow="autoplay; fullscreen; xr-spatial-tracking"
        allowFullScreen
        className="w-full h-full"
        onLoad={() => setLoaded(true)}
      />
      <CornerMarkers />
    </div>
  );
}

function GltfViewer({ src }: { src: string }) {
  const mountRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<string | null>(
    "Loading Three.js renderer..."
  );

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

    // Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(window.devicePixelRatio);
    container.appendChild(renderer.domElement);

    // Lights
    const ambient = new THREE.AmbientLight(0xffffff, 0.5);
    scene.add(ambient);
    const dirLight = new THREE.DirectionalLight(0x00f0ff, 1);
    dirLight.position.set(5, 10, 5);
    scene.add(dirLight);

    // Load GLB
    let animId: number;
    const load = async () => {
      try {
        const { GLTFLoader } = await import(
          "three/examples/jsm/loaders/GLTFLoader.js"
        );
        const loader = new GLTFLoader();
        loader.load(
          src,
          (gltf) => {
            scene.add(gltf.scene);
            setStatus(null);
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

    // Animation loop
    const animate = () => {
      animId = requestAnimationFrame(animate);
      scene.children.forEach((c) => {
        if ((c as THREE.Group).isGroup) c.rotation.y += 0.003;
      });
      renderer.render(scene, camera);
    };
    animate();

    const handleResize = () => {
      const w = container.clientWidth;
      const h = container.clientHeight;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    window.addEventListener("resize", handleResize);

    return () => {
      cancelAnimationFrame(animId);
      window.removeEventListener("resize", handleResize);
      renderer.dispose();
      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement);
      }
    };
  }, [src]);

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
