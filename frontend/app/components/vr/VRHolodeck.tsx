"use client";

import React, { useEffect, useRef, useState, useCallback } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { VRButton } from "three/examples/jsm/webxr/VRButton.js";
import { VRFloatingHUD } from "./VRFloatingHUD";
import { VRSpeechManager } from "./VRSpeechManager";
import { VRSessionInfo, VRSegmentObject } from "./types";
import axios from "axios";

interface VRHolodeckProps {
  modelId: string;
  modelUrl: string;
  modelTitle: string;
  sessionInfo: VRSessionInfo;
  onExit: () => void;
}

export default function VRHolodeck({
  modelId,
  modelUrl,
  modelTitle,
  sessionInfo,
  onExit,
}: VRHolodeckProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [secondsRemaining, setSecondsRemaining] = useState<number>(
    sessionInfo.durationSeconds
  );
  const [isSessionExpired, setIsSessionExpired] = useState<boolean>(false);
  const [loadingStatus, setLoadingStatus] = useState<string>(
    "Entering VR Holodeck..."
  );

  const sceneRef = useRef<THREE.Scene | null>(null);
  const hudRef = useRef<VRFloatingHUD | null>(null);
  const speechManagerRef = useRef<VRSpeechManager | null>(null);
  const segmentsRef = useRef<VRSegmentObject[]>([]);
  const grabbedSegmentRef = useRef<VRSegmentObject | null>(null);
  const activeSegmentRef = useRef<VRSegmentObject | null>(null);
  const isExplodedRef = useRef<boolean>(false);
  const isStoryModeRef = useRef<boolean>(false);

  // ─── Session Timer ─────────────────────────────────────────────────────────
  useEffect(() => {
    if (sessionInfo.unlimitedTime) return;

    const timer = setInterval(() => {
      setSecondsRemaining((prev) => {
        if (prev <= 1) {
          clearInterval(timer);
          setIsSessionExpired(true);
          hudRef.current?.setTimer(0, false);
          hudRef.current?.setAIResponse(
            "Your VR Holodeck session has concluded. Upgrade to Pro for extended VR immersion!"
          );
          return 0;
        }
        const updated = prev - 1;
        hudRef.current?.setTimer(updated, false);
        return updated;
      });
    }, 1000);

    return () => clearInterval(timer);
  }, [sessionInfo.unlimitedTime]);

  // ─── AI Chat & Voice Assistant ─────────────────────────────────────────────
  const askAICurator = useCallback(
    async (userMessage: string, partName?: string | null) => {
      hudRef.current?.setAIResponse(`Thinking: "${userMessage}"...`);
      try {
        const res = await axios.post("/api/vr/chat", {
          model_id: modelId,
          message: userMessage,
          highlighted_part: partName || null,
        });

        const reply = res.data.response;
        hudRef.current?.setAIResponse(reply);

        // Speak aloud through headset
        speechManagerRef.current?.speak(reply);
      } catch (err) {
        console.warn("[VR] Chat error:", err);
        const fallback = partName
          ? `This is the ${partName}. It serves a key visual and architectural role in this model.`
          : `You are examining the ${modelTitle}. Use your controllers to grab or explode parts.`;
        hudRef.current?.setAIResponse(fallback);
        speechManagerRef.current?.speak(fallback);
      }
    },
    [modelId, modelTitle]
  );

  // ─── Story Tour Automation ─────────────────────────────────────────────────
  const startStoryTour = useCallback(async () => {
    if (isStoryModeRef.current || segmentsRef.current.length === 0) return;
    isStoryModeRef.current = true;

    hudRef.current?.setAIResponse("Starting Guided VR Story Tour...");
    speechManagerRef.current?.speak(
      `Welcome to the guided spatial breakdown of the ${modelTitle}. Let us explore each component.`,
      async () => {
        for (let i = 0; i < segmentsRef.current.length; i++) {
          const seg = segmentsRef.current[i];
          // Highlight this segment
          highlightSegment(seg);
          hudRef.current?.setActivePart(seg.name);

          // Request AI narration for this segment
          await askAICurator(
            `Explain the role and significance of the ${seg.name} in this model.`,
            seg.name
          );

          // Wait 6 seconds between segments
          await new Promise((resolve) => setTimeout(resolve, 6000));
        }

        unhighlightAll();
        hudRef.current?.setActivePart(null);
        hudRef.current?.setAIResponse(
          "Story tour complete. You may now freely interact with all components."
        );
        isStoryModeRef.current = false;
      }
    );
  }, [modelTitle, askAICurator]);

  // ─── Segment Highlight & Explode ───────────────────────────────────────────
  const highlightSegment = (segment: VRSegmentObject) => {
    unhighlightAll();
    activeSegmentRef.current = segment;
    segment.isHighlighted = true;
    (segment.mesh.material as THREE.MeshStandardMaterial).emissive =
      new THREE.Color(0x00f0ff);
    (segment.mesh.material as THREE.MeshStandardMaterial).emissiveIntensity = 0.5;
    hudRef.current?.setActivePart(segment.name);
  };

  const unhighlightAll = () => {
    activeSegmentRef.current = null;
    for (const seg of segmentsRef.current) {
      seg.isHighlighted = false;
      (seg.mesh.material as THREE.MeshStandardMaterial).emissive =
        new THREE.Color(0x000000);
      (seg.mesh.material as THREE.MeshStandardMaterial).emissiveIntensity = 0.0;
    }
  };

  const toggleExplodedView = () => {
    isExplodedRef.current = !isExplodedRef.current;
    const explode = isExplodedRef.current;

    for (const seg of segmentsRef.current) {
      if (explode) {
        // Move outward relative to center
        const offset = seg.originalPosition.clone().normalize().multiplyScalar(0.4);
        seg.mesh.position.copy(seg.originalPosition.clone().add(offset));
      } else {
        seg.mesh.position.copy(seg.originalPosition);
        seg.mesh.quaternion.copy(seg.originalQuaternion);
      }
    }

    hudRef.current?.setAIResponse(
      explode
        ? "Model exploded! Each component is isolated for inspection."
        : "Assembly reset to compact origin."
    );
  };

  const resetAssembly = () => {
    isExplodedRef.current = false;
    for (const seg of segmentsRef.current) {
      seg.mesh.position.copy(seg.originalPosition);
      seg.mesh.quaternion.copy(seg.originalQuaternion);
      seg.mesh.scale.copy(seg.originalScale);
    }
    unhighlightAll();
    hudRef.current?.setAIResponse("All parts returned to baseline assembly.");
  };

  // ─── Three.js Scene Setup ──────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;
    const container = containerRef.current;

    // 1. Scene & Camera
    const scene = new THREE.Scene();
    sceneRef.current = scene;
    scene.background = new THREE.Color(0x020617);

    const camera = new THREE.PerspectiveCamera(
      70,
      window.innerWidth / window.innerHeight,
      0.1,
      1000
    );
    camera.position.set(0, 1.6, 0); // User eye height standing in room

    // 2. Renderer with WebXR
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.xr.enabled = true;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    container.appendChild(renderer.domElement);

    // Mount Native VR Button
    const vrBtn = VRButton.createButton(renderer);
    vrBtn.style.position = "absolute";
    vrBtn.style.bottom = "32px";
    vrBtn.style.left = "calc(50% - 110px)";
    vrBtn.style.width = "220px";
    vrBtn.style.height = "52px";
    vrBtn.style.background = "#00f0ff";
    vrBtn.style.color = "#000000";
    vrBtn.style.fontFamily = "monospace";
    vrBtn.style.fontWeight = "bold";
    vrBtn.style.borderRadius = "12px";
    vrBtn.style.boxShadow = "0 0 25px rgba(0,240,255,0.4)";
    container.appendChild(vrBtn);

    // 3. Lighting
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.9);
    scene.add(ambientLight);

    const dirLight = new THREE.DirectionalLight(0xffffff, 1.5);
    dirLight.position.set(3, 5, 2);
    dirLight.castShadow = true;
    scene.add(dirLight);

    const spotLight = new THREE.SpotLight(0x00f0ff, 2.5);
    spotLight.position.set(0, 3.5, -1.2);
    spotLight.target.position.set(0, 1.0, -1.2);
    scene.add(spotLight);
    scene.add(spotLight.target);

    // 4. Inspection Pedestal (Glowing Cyber Table)
    const pedestalGroup = new THREE.Group();
    const tableTopGeo = new THREE.CylinderGeometry(0.7, 0.75, 0.08, 32);
    const tableTopMat = new THREE.MeshStandardMaterial({
      color: 0x0f172a,
      roughness: 0.2,
      metalness: 0.8,
    });
    const tableTop = new THREE.Mesh(tableTopGeo, tableTopMat);
    tableTop.position.set(0, 0.85, -1.2);
    tableTop.receiveShadow = true;
    pedestalGroup.add(tableTop);

    const pedestalRingGeo = new THREE.TorusGeometry(0.72, 0.015, 16, 64);
    const pedestalRingMat = new THREE.MeshBasicMaterial({ color: 0x00f0ff });
    const pedestalRing = new THREE.Mesh(pedestalRingGeo, pedestalRingMat);
    pedestalRing.rotation.x = Math.PI / 2;
    pedestalRing.position.set(0, 0.89, -1.2);
    pedestalGroup.add(pedestalRing);
    scene.add(pedestalGroup);

    // 5. Floating Curved HUD
    const hud = new VRFloatingHUD();
    hud.mesh.position.set(0, 1.6, -2.1);
    hud.setTimer(sessionInfo.durationSeconds, sessionInfo.unlimitedTime);
    hudRef.current = hud;
    scene.add(hud.mesh);

    // 6. Speech Manager Setup
    const speechMgr = new VRSpeechManager();
    speechManagerRef.current = speechMgr;
    speechMgr.onTranscript((text) => {
      hud.setMicStatus(`Heard: "${text}"`);
      askAICurator(text, activeSegmentRef.current?.name);
    });
    speechMgr.onStatusChange((statusText) => {
      hud.setMicStatus(statusText);
    });

    // 7. Load Showroom Classroom Environment GLB
    const gltfLoader = new GLTFLoader();
    setLoadingStatus("Loading Virtual Classroom Environment...");
    gltfLoader.load(
      "/vr_environment.glb",
      (gltf) => {
        const env = gltf.scene;
        env.position.set(0, 0, 0);
        env.scale.set(1, 1, 1);
        env.traverse((child) => {
          if ((child as THREE.Mesh).isMesh) {
            child.receiveShadow = true;
          }
        });
        scene.add(env);
        setLoadingStatus("Loading Segmented 3D Model...");
      },
      undefined,
      (err) => {
        console.warn("[VR] Environment load fallback:", err);
      }
    );

    // 8. Load Segmented Model onto Pedestal
    const modelGroup = new THREE.Group();
    modelGroup.position.set(0, 0.9, -1.2);
    scene.add(modelGroup);

    gltfLoader.load(
      modelUrl,
      (gltf) => {
        const root = gltf.scene;
        // Compute bounding box and normalize scale
        const bbox = new THREE.Box3().setFromObject(root);
        const size = new THREE.Vector3();
        bbox.getSize(size);
        const maxDim = Math.max(size.x, size.y, size.z);
        const targetScale = 0.85 / (maxDim || 1);
        root.scale.setScalar(targetScale);

        // Center on table top
        const center = new THREE.Vector3();
        bbox.getCenter(center);
        root.position.x = -center.x * targetScale;
        root.position.y = -bbox.min.y * targetScale;
        root.position.z = -center.z * targetScale;

        // Collect segmented sub-meshes
        const segments: VRSegmentObject[] = [];
        root.traverse((child) => {
          if ((child as THREE.Mesh).isMesh) {
            const mesh = child as THREE.Mesh;
            mesh.castShadow = true;
            mesh.receiveShadow = true;

            // Clone material so individual highlights don't bleed
            if (mesh.material) {
              mesh.material = (mesh.material as THREE.Material).clone();
            }

            segments.push({
              mesh: mesh,
              name: mesh.name || `part_${segments.length + 1}`,
              originalPosition: mesh.position.clone(),
              originalQuaternion: mesh.quaternion.clone(),
              originalScale: mesh.scale.clone(),
              isGrabbed: false,
              isHighlighted: false,
            });
          }
        });

        segmentsRef.current = segments;
        modelGroup.add(root);
        setLoadingStatus("Ready. Put on your VR headset and click ENTER VR!");
      },
      undefined,
      (err) => {
        console.error("[VR] Model load error:", err);
        setLoadingStatus("Failed to load 3D model in VR.");
      }
    );

    // 9. VR Controllers Setup
    const controllers: THREE.XRTargetRaySpace[] = [];
    const raycaster = new THREE.Raycaster();
    const tempMatrix = new THREE.Matrix4();

    for (let i = 0; i < 2; i++) {
      const controller = renderer.xr.getController(i);
      scene.add(controller);
      controllers.push(controller);

      // Ray visual line
      const rayGeo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(0, 0, 0),
        new THREE.Vector3(0, 0, -3),
      ]);
      const rayMat = new THREE.LineBasicMaterial({ color: 0x00f0ff });
      const rayLine = new THREE.Line(rayGeo, rayMat);
      rayLine.name = "rayLine";
      controller.add(rayLine);

      // Squeeze (Grip button) -> Grab segment
      controller.addEventListener("squeezestart", () => {
        tempMatrix.identity().extractRotation(controller.matrixWorld);
        raycaster.ray.origin.setFromMatrixPosition(controller.matrixWorld);
        raycaster.ray.direction.set(0, 0, -1).applyMatrix4(tempMatrix);

        const meshes = segmentsRef.current.map((s) => s.mesh);
        const hits = raycaster.intersectObjects(meshes, false);

        if (hits.length > 0) {
          const hitMesh = hits[0].object as THREE.Mesh;
          const seg = segmentsRef.current.find((s) => s.mesh === hitMesh);
          if (seg) {
            grabbedSegmentRef.current = seg;
            seg.isGrabbed = true;
            highlightSegment(seg);
            controller.attach(seg.mesh); // 6-DOF attach to controller!
          }
        }
      });

      controller.addEventListener("squeezeend", () => {
        if (grabbedSegmentRef.current) {
          modelGroup.attach(grabbedSegmentRef.current.mesh); // Re-attach to model space
          grabbedSegmentRef.current.isGrabbed = false;
          grabbedSegmentRef.current = null;
        }
      });

      // Select (Trigger button) -> Click HUD buttons or select part
      controller.addEventListener("selectstart", () => {
        tempMatrix.identity().extractRotation(controller.matrixWorld);
        raycaster.ray.origin.setFromMatrixPosition(controller.matrixWorld);
        raycaster.ray.direction.set(0, 0, -1).applyMatrix4(tempMatrix);

        // 1. Check HUD button click
        if (hudRef.current) {
          const hudHits = raycaster.intersectObject(hudRef.current.mesh, false);
          if (hudHits.length > 0 && hudHits[0].uv) {
            const btnId = hudRef.current.checkIntersection(hudHits[0].uv);
            if (btnId === "voice") {
              speechManagerRef.current?.toggleListening();
            } else if (btnId === "explode") {
              toggleExplodedView();
            } else if (btnId === "reset") {
              resetAssembly();
            } else if (btnId === "story") {
              startStoryTour();
            }
            return;
          }
        }

        // 2. Check Segment selection
        const meshes = segmentsRef.current.map((s) => s.mesh);
        const hits = raycaster.intersectObjects(meshes, false);
        if (hits.length > 0) {
          const hitMesh = hits[0].object as THREE.Mesh;
          const seg = segmentsRef.current.find((s) => s.mesh === hitMesh);
          if (seg) {
            highlightSegment(seg);
            askAICurator(
              `Describe the structure and purpose of the ${seg.name}.`,
              seg.name
            );
          }
        }
      });
    }

    // 10. WebXR Render Loop
    renderer.setAnimationLoop(() => {
      // Raycast hover check for HUD buttons
      if (controllers[0] && hudRef.current) {
        tempMatrix.identity().extractRotation(controllers[0].matrixWorld);
        raycaster.ray.origin.setFromMatrixPosition(controllers[0].matrixWorld);
        raycaster.ray.direction.set(0, 0, -1).applyMatrix4(tempMatrix);

        const hudHits = raycaster.intersectObject(hudRef.current.mesh, false);
        if (hudHits.length > 0 && hudHits[0].uv) {
          const btnId = hudRef.current.checkIntersection(hudHits[0].uv);
          hudRef.current.setHoveredButton(btnId);
        } else {
          hudRef.current.setHoveredButton(null);
        }
      }

      renderer.render(scene, camera);
    });

    // Handle Window Resize
    const handleResize = () => {
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight);
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      renderer.setAnimationLoop(null);
      renderer.dispose();
      speechManagerRef.current?.stopSpeaking();
    };
  }, [
    modelUrl,
    sessionInfo.durationSeconds,
    sessionInfo.unlimitedTime,
    askAICurator,
    startStoryTour,
  ]);

  return (
    <div className="relative w-screen h-screen bg-black overflow-hidden select-none">
      {/* 3D WebXR Viewport */}
      <div ref={containerRef} className="w-full h-full" />

      {/* Top Floating Overlay (2D desktop / headset mirror) */}
      <div className="absolute top-6 left-6 right-6 flex items-center justify-between pointer-events-none z-30">
        <div className="flex items-center gap-3">
          <div className="px-3 py-1.5 rounded-lg bg-black/80 border border-cyber/40 text-cyber font-mono text-xs backdrop-blur-md">
            🥽 VR HOLODECK ACTIVE
          </div>
          <span className="text-white text-xs font-mono font-bold">
            {modelTitle}
          </span>
        </div>

        <div className="flex items-center gap-3 pointer-events-auto">
          {/* Countdown Clock */}
          {!sessionInfo.unlimitedTime && (
            <div
              className={`px-3 py-1.5 rounded-lg font-mono text-xs border backdrop-blur-md ${
                secondsRemaining <= 60
                  ? "bg-red-500/20 border-red-500/50 text-red-400 animate-pulse font-bold"
                  : "bg-black/80 border-cyber/30 text-cyber"
              }`}
            >
              ⏱️ {Math.floor(secondsRemaining / 60)}:
              {String(secondsRemaining % 60).padStart(2, "0")} REMAINING
            </div>
          )}

          {/* Exit Button */}
          <button
            onClick={onExit}
            className="px-4 py-1.5 bg-red-500/20 hover:bg-red-500/30 border border-red-500/40 text-red-300 rounded-lg text-xs font-mono transition-colors"
          >
            EXIT VR
          </button>
        </div>
      </div>

      {/* Loading Banner */}
      {loadingStatus && (
        <div className="absolute bottom-24 left-1/2 -translate-x-1/2 px-6 py-2.5 rounded-full bg-black/90 border border-cyber/30 text-slate-300 font-mono text-xs backdrop-blur-md z-30 flex items-center gap-3">
          <div className="w-2 h-2 rounded-full bg-cyber animate-ping" />
          <span>{loadingStatus}</span>
        </div>
      )}

      {/* Expired Overlay */}
      {isSessionExpired && (
        <div className="absolute inset-0 bg-black/90 backdrop-blur-md flex items-center justify-center p-6 z-50">
          <div className="max-w-md w-full p-8 rounded-2xl bg-slate-950 border border-amber-500/40 text-center space-y-4">
            <h2 className="text-2xl font-rajdhani font-bold text-white tracking-wider">
              VR SESSION CONCLUDED
            </h2>
            <p className="text-xs font-mono text-slate-400">
              Your allocated free VR Holodeck time has ended. Upgrade to Pro for 25 monthly sessions and extended immersion!
            </p>
            <div className="pt-2 flex justify-center gap-3">
              <button
                onClick={onExit}
                className="px-6 py-2.5 rounded-lg bg-cyber text-black font-rajdhani font-bold text-sm tracking-wider hover:bg-cyber/90 transition-all"
              >
                RETURN TO VIEWER
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
