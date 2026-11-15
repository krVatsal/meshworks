import * as THREE from "three";

export interface VRSessionInfo {
  sessionId: string;
  modelId: string;
  durationSeconds: number;
  remainingSeconds: number;
  unlimitedTime: boolean;
  remainingSessions: number;
  planId: string;
  isMaster: boolean;
}

export interface VRSegmentObject {
  mesh: THREE.Mesh;
  name: string;
  originalPosition: THREE.Vector3;
  originalQuaternion: THREE.Quaternion;
  originalScale: THREE.Vector3;
  isGrabbed: boolean;
  isHighlighted: boolean;
  boxHelper?: THREE.BoxHelper;
}

export interface VRChatMessage {
  sender: "user" | "ai" | "system";
  text: string;
  timestamp: number;
}
