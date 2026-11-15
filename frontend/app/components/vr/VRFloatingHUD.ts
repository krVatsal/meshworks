import * as THREE from "three";

export interface VRHUDButton {
  id: string;
  label: string;
  x: number;
  y: number;
  w: number;
  h: number;
  isHovered?: boolean;
}

export class VRFloatingHUD {
  public mesh: THREE.Mesh;
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private texture: THREE.CanvasTexture;

  private title = "MESHWORKS VR HOLODECK";
  private timerText = "TIME: UNLIMITED";
  private timerColor = "#00f0ff";
  private activePartText = "Inspecting: Entire Model";
  private aiResponseText = "Welcome to the VR Holodeck. Point at any segment and squeeze your grip to pull it out. Click Voice to ask questions!";
  private micStatusText = "Mic: Idle (Click to Ask)";

  public buttons: VRHUDButton[] = [
    { id: "voice", label: "🎙️ VOICE CHAT", x: 40, y: 410, w: 210, h: 65 },
    { id: "explode", label: "💥 EXPLODE", x: 270, y: 410, w: 210, h: 65 },
    { id: "reset", label: "🔄 RESET", x: 500, y: 410, w: 210, h: 65 },
    { id: "story", label: "🎬 STORY TOUR", x: 730, y: 410, w: 240, h: 65 },
  ];

  constructor() {
    this.canvas = document.createElement("canvas");
    this.canvas.width = 1024;
    this.canvas.height = 512;
    this.ctx = this.canvas.getContext("2d")!;

    this.texture = new THREE.CanvasTexture(this.canvas);
    this.texture.minFilter = THREE.LinearFilter;
    this.texture.magFilter = THREE.LinearFilter;

    // Curved plane geometry for high-tech holographic appearance
    const geometry = new THREE.PlaneGeometry(1.6, 0.8, 16, 16);
    
    // Slight curve
    const pos = geometry.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i);
      pos.setZ(i, -Math.pow(x / 1.6, 2) * 0.15);
    }
    geometry.computeVertexNormals();

    const material = new THREE.MeshBasicMaterial({
      map: this.texture,
      transparent: true,
      opacity: 0.94,
      side: THREE.DoubleSide,
    });

    this.mesh = new THREE.Mesh(geometry, material);
    this.mesh.name = "VR_FLOATING_HUD";
    this.update();
  }

  public setTimer(secondsRemaining: number, unlimited: boolean) {
    if (unlimited) {
      this.timerText = "TIME: ∞ (MASTER)";
      this.timerColor = "#fbbf24";
    } else {
      const mins = Math.floor(secondsRemaining / 60);
      const secs = secondsRemaining % 60;
      const formatted = `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
      this.timerText = `TIME: ${formatted}`;
      if (secondsRemaining <= 60) {
        this.timerColor = "#ef4444";
      } else if (secondsRemaining <= 180) {
        this.timerColor = "#f59e0b";
      } else {
        this.timerColor = "#00f0ff";
      }
    }
    this.update();
  }

  public setActivePart(partName: string | null) {
    this.activePartText = partName ? `Inspecting: ${partName}` : "Inspecting: Entire Model";
    this.update();
  }

  public setAIResponse(text: string) {
    this.aiResponseText = text;
    this.update();
  }

  public setMicStatus(status: string) {
    this.micStatusText = status;
    this.update();
  }

  public setHoveredButton(buttonId: string | null) {
    let changed = false;
    for (const btn of this.buttons) {
      const isHover = btn.id === buttonId;
      if (btn.isHovered !== isHover) {
        btn.isHovered = isHover;
        changed = true;
      }
    }
    if (changed) {
      this.update();
    }
  }

  public checkIntersection(uv: THREE.Vector2): string | null {
    if (!uv) return null;
    const canvasX = uv.x * this.canvas.width;
    const canvasY = (1 - uv.y) * this.canvas.height;

    for (const btn of this.buttons) {
      if (
        canvasX >= btn.x &&
        canvasX <= btn.x + btn.w &&
        canvasY >= btn.y &&
        canvasY <= btn.y + btn.h
      ) {
        return btn.id;
      }
    }
    return null;
  }

  public update() {
    const ctx = this.ctx;
    const w = this.canvas.width;
    const h = this.canvas.height;

    // Background Cyber Glass Panel
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "rgba(10, 15, 25, 0.92)";
    ctx.beginPath();
    ctx.roundRect(10, 10, w - 20, h - 20, 24);
    ctx.fill();

    // Outer Neon Border
    ctx.lineWidth = 4;
    ctx.strokeStyle = "rgba(0, 240, 255, 0.5)";
    ctx.stroke();

    // Corner decorative accents
    ctx.fillStyle = "#00f0ff";
    ctx.fillRect(10, 10, 30, 6);
    ctx.fillRect(10, 10, 6, 30);
    ctx.fillRect(w - 40, 10, 30, 6);
    ctx.fillRect(w - 16, 10, 6, 30);

    // ── Header Bar ─────────────────────────────────────────────────────────────
    ctx.font = "bold 26px 'Rajdhani', monospace, sans-serif";
    ctx.fillStyle = "#ffffff";
    ctx.fillText(this.title, 40, 56);

    // Timer Badge
    ctx.font = "bold 24px 'Courier New', monospace";
    ctx.fillStyle = this.timerColor;
    ctx.fillText(this.timerText, w - 310, 56);

    // Separator line
    ctx.strokeStyle = "rgba(255, 255, 255, 0.12)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(40, 75);
    ctx.lineTo(w - 40, 75);
    ctx.stroke();

    // ── Active Part / Status Subheader ─────────────────────────────────────────
    ctx.font = "18px 'Courier New', monospace";
    ctx.fillStyle = "#00f0ff";
    ctx.fillText(this.activePartText.toUpperCase(), 40, 110);

    ctx.font = "16px monospace";
    ctx.fillStyle = "#94a3b8";
    ctx.fillText(this.micStatusText, w - 340, 110);

    // ── AI Curator Message Box ─────────────────────────────────────────────────
    ctx.fillStyle = "rgba(0, 0, 0, 0.45)";
    ctx.beginPath();
    ctx.roundRect(40, 130, w - 80, 255, 14);
    ctx.fill();
    ctx.strokeStyle = "rgba(0, 240, 255, 0.2)";
    ctx.lineWidth = 2;
    ctx.stroke();

    // Label on message box
    ctx.font = "bold 14px monospace";
    ctx.fillStyle = "#00f0ff";
    ctx.fillText("AI CURATOR INSIGHT:", 60, 160);

    // Wrapped message text
    ctx.font = "20px 'Inter', sans-serif";
    ctx.fillStyle = "#e2e8f0";
    this.wrapText(ctx, this.aiResponseText, 60, 195, w - 120, 30);

    // ── Interactive Action Buttons ─────────────────────────────────────────────
    for (const btn of this.buttons) {
      if (btn.isHovered) {
        ctx.fillStyle = "rgba(0, 240, 255, 0.35)";
        ctx.strokeStyle = "#00f0ff";
        ctx.lineWidth = 3;
      } else {
        ctx.fillStyle = "rgba(255, 255, 255, 0.05)";
        ctx.strokeStyle = "rgba(255, 255, 255, 0.25)";
        ctx.lineWidth = 2;
      }

      ctx.beginPath();
      ctx.roundRect(btn.x, btn.y, btn.w, btn.h, 12);
      ctx.fill();
      ctx.stroke();

      ctx.font = "bold 18px 'Rajdhani', monospace, sans-serif";
      ctx.fillStyle = btn.isHovered ? "#ffffff" : "#cbd5e1";
      ctx.textAlign = "center";
      ctx.fillText(btn.label, btn.x + btn.w / 2, btn.y + btn.h / 2 + 6);
      ctx.textAlign = "left";
    }

    this.texture.needsUpdate = true;
  }

  private wrapText(
    ctx: CanvasRenderingContext2D,
    text: string,
    x: number,
    y: number,
    maxWidth: number,
    lineHeight: number
  ) {
    const words = text.split(" ");
    let line = "";

    for (let n = 0; n < words.length; n++) {
      const testLine = line + words[n] + " ";
      const metrics = ctx.measureText(testLine);
      const testWidth = metrics.width;
      if (testWidth > maxWidth && n > 0) {
        ctx.fillText(line, x, y);
        line = words[n] + " ";
        y += lineHeight;
        if (y > 360) {
          ctx.fillText("...", x, y);
          return;
        }
      } else {
        line = testLine;
      }
    }
    ctx.fillText(line, x, y);
  }
}
