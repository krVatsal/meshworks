"use client";

import React, { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import axios from "axios";
import Link from "next/link";
import VRHolodeck from "@/app/components/vr/VRHolodeck";
import { VRSessionInfo } from "@/app/components/vr/types";
import { useAuth } from "@/app/context/AuthContext";
import { ArrowLeft, AlertTriangle, ArrowRight, Loader2 } from "lucide-react";

export default function VRPage() {
  const params = useParams();
  const id = params.id as string;
  const router = useRouter();
  const { user, setLoginModalOpen } = useAuth();

  const [loading, setLoading] = useState<boolean>(true);
  const [modelUrl, setModelUrl] = useState<string | null>(null);
  const [modelTitle, setModelTitle] = useState<string>("3D Model");
  const [sessionInfo, setSessionInfo] = useState<VRSessionInfo | null>(null);
  const [quotaExceeded, setQuotaExceeded] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;

    const initVRSession = async () => {
      setLoading(true);
      setErrorMsg(null);

      try {
        // 1. Fetch Model Data
        const historyRes = await axios.get(`/api/history/${id}`);
        const modelRecord = historyRes.data;
        const glbUrl =
          modelRecord?.primary_model?.url ||
          modelRecord?.primary_model?.source_url;
        const title =
          modelRecord?.primary_model?.title ||
          modelRecord?.original_prompt ||
          "3D Asset";

        if (!glbUrl) {
          setErrorMsg("This model does not have a downloadable 3D GLB file.");
          setLoading(false);
          return;
        }

        setModelUrl(glbUrl);
        setModelTitle(title);

        // 2. Start VR Session with Backend Quota Verification
        const sessionRes = await axios.post("/api/vr/session/start", {
          model_id: id,
        });

        const sData = sessionRes.data;
        setSessionInfo({
          sessionId: sData.session_id,
          modelId: sData.model_id,
          durationSeconds: sData.duration_seconds,
          remainingSeconds: sData.duration_seconds,
          unlimitedTime: sData.unlimited_time,
          remainingSessions: sData.remaining_sessions,
          planId: sData.plan_id,
          isMaster: sData.is_master,
        });
      } catch (err: any) {
        console.error("[VR] Session initialization failed:", err);
        if (err.response?.status === 401) {
          setLoginModalOpen(true);
        } else if (
          err.response?.status === 403 &&
          err.response?.data?.detail?.code === "VR_QUOTA_EXCEEDED"
        ) {
          setQuotaExceeded(true);
        } else {
          const detail = err.response?.data?.detail;
          setErrorMsg(
            typeof detail === "string"
              ? detail
              : detail?.message || "Failed to initialize VR session."
          );
        }
      } finally {
        setLoading(false);
      }
    };

    initVRSession();
  }, [id, setLoginModalOpen]);

  if (loading) {
    return (
      <div className="min-h-screen bg-black flex flex-col items-center justify-center space-y-4 text-cyber font-mono text-sm">
        <Loader2 className="animate-spin text-cyber" size={36} />
        <p className="tracking-widest">INITIALIZING VR HOLODECK ENGINE...</p>
      </div>
    );
  }

  // Quota Exceeded Gate
  if (quotaExceeded) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center p-6">
        <div className="max-w-md w-full p-8 rounded-2xl bg-slate-950 border border-amber-500/40 text-center space-y-5">
          <div className="w-12 h-12 mx-auto rounded-full bg-amber-500/10 border border-amber-500/30 flex items-center justify-center text-amber-400">
            <AlertTriangle size={24} />
          </div>
          <h1 className="text-2xl font-rajdhani font-bold text-white tracking-wider">
            VR LIMIT REACHED
          </h1>
          <p className="text-xs font-mono text-slate-400">
            You have used your free VR Holodeck session for this month. Upgrade to Pro for 25 monthly sessions and extended immersion.
          </p>
          <div className="pt-2 flex flex-col gap-2">
            <Link
              href="/pricing"
              className="w-full inline-flex items-center justify-center gap-2 px-5 py-3 rounded-lg bg-cyber text-black font-rajdhani font-bold text-sm tracking-wider hover:bg-cyber/90 transition-all shadow-[0_0_20px_rgba(0,255,200,0.3)]"
            >
              UPGRADE FOR MORE VR SESSIONS
              <ArrowRight size={14} />
            </Link>
            <button
              onClick={() => router.push(`/view/${id}`)}
              className="text-xs font-mono text-slate-400 hover:text-white pt-2"
            >
              Return to 2D Viewer
            </button>
          </div>
        </div>
      </div>
    );
  }

  // General Error Gate
  if (errorMsg || !modelUrl || !sessionInfo) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center p-6">
        <div className="max-w-md w-full p-8 rounded-2xl bg-slate-950 border border-red-500/30 text-center space-y-4">
          <h2 className="text-xl font-rajdhani font-bold text-white">
            CANNOT ENTER VR
          </h2>
          <p className="text-xs font-mono text-red-400">{errorMsg || "Could not load VR model."}</p>
          <button
            onClick={() => router.push(`/view/${id}`)}
            className="inline-flex items-center gap-2 px-4 py-2 bg-white/10 hover:bg-white/20 text-white rounded font-mono text-xs"
          >
            <ArrowLeft size={12} /> Return to Viewer
          </button>
        </div>
      </div>
    );
  }

  // Render VR Holodeck
  return (
    <VRHolodeck
      modelId={id}
      modelUrl={modelUrl}
      modelTitle={modelTitle}
      sessionInfo={sessionInfo}
      onExit={() => router.push(`/view/${id}`)}
    />
  );
}
