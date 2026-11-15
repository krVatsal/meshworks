"use client";

import React, { useEffect, useRef, useState } from "react";
import axios from "axios";
import { useAuth } from "../context/AuthContext";
import { X, Sparkles, Shield, AlertTriangle, ArrowRight } from "lucide-react";
import Link from "next/link";

declare global {
  interface Window {
    google?: any;
  }
}

export default function AuthModal() {
  const {
    user,
    loginModalOpen,
    setLoginModalOpen,
    quotaModalOpen,
    setQuotaModalOpen,
    handleGoogleCredential,
    isMaster,
  } = useAuth();

  const googleBtnRef = useRef<HTMLDivElement>(null);
  const [clientId, setClientId] = useState<string>(
    process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || ""
  );

  // Fetch client ID dynamically from backend if not baked into env
  useEffect(() => {
    if (!clientId) {
      axios
        .get<{ google_client_id: string }>("/api/auth/config")
        .then((res) => {
          if (res.data?.google_client_id) {
            setClientId(res.data.google_client_id);
          }
        })
        .catch((err) => {
          console.warn("[Auth] Could not fetch auth config:", err);
        });
    }
  }, [clientId]);

  // Load and initialize Google Identity Services
  useEffect(() => {
    if (!loginModalOpen && !quotaModalOpen) return;

    const initializeGoogle = () => {
      if (!window.google || !clientId) return;

      try {
        window.google.accounts.id.initialize({
          client_id: clientId,
          callback: (response: any) => {
            if (response.credential) {
              handleGoogleCredential(response.credential);
            }
          },
        });

        if (googleBtnRef.current) {
          googleBtnRef.current.innerHTML = "";
          window.google.accounts.id.renderButton(googleBtnRef.current, {
            theme: "filled_black",
            size: "large",
            shape: "rectangular",
            text: "continue_with",
            width: 320,
          });
        }
      } catch (err) {
        console.error("[Google Auth] Error initializing button:", err);
      }
    };

    if (window.google) {
      initializeGoogle();
    } else {
      const script = document.createElement("script");
      script.src = "https://accounts.google.com/gsi/client";
      script.async = true;
      script.defer = true;
      script.onload = initializeGoogle;
      document.head.appendChild(script);
    }
  }, [loginModalOpen, quotaModalOpen, clientId, handleGoogleCredential]);

  if (!loginModalOpen && (!quotaModalOpen || isMaster)) return null;

  const isQuotaView = quotaModalOpen && !isMaster;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md animate-fade-in">
      <div className="relative w-full max-w-md p-6 bg-slate-950 border border-cyber/30 rounded-xl shadow-[0_0_50px_rgba(0,255,200,0.15)] overflow-hidden">
        {/* Glow corner accent */}
        <div className="absolute top-0 right-0 w-32 h-32 bg-cyber/10 rounded-full blur-2xl pointer-events-none" />

        {/* Close Button */}
        <button
          onClick={() => {
            setLoginModalOpen(false);
            setQuotaModalOpen(false);
          }}
          className="absolute top-4 right-4 text-slate-500 hover:text-white transition-colors p-1"
        >
          <X size={18} />
        </button>

        {isQuotaView ? (
          /* Quota Exceeded View */
          <div className="text-center space-y-5 pt-2">
            <div className="w-12 h-12 mx-auto rounded-full bg-amber-500/10 border border-amber-500/30 flex items-center justify-center text-amber-400">
              <AlertTriangle size={24} />
            </div>

            <div className="space-y-1">
              <h2 className="text-xl font-rajdhani font-bold tracking-wider text-white">
                GENERATION LIMIT REACHED
              </h2>
              <p className="text-xs font-mono text-slate-400">
                You have reached your 10 free generations for this month.
              </p>
            </div>

            <div className="p-4 bg-white/[0.02] border border-white/5 rounded-lg space-y-2 text-left">
              <div className="flex justify-between text-xs font-mono">
                <span className="text-slate-400">Usage Quota:</span>
                <span className="text-amber-400 font-bold">10 / 10 Used</span>
              </div>
              <div className="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
                <div className="bg-amber-400 h-full w-full" />
              </div>
              <p className="text-[10px] font-mono text-slate-500">
                Resets on: {user?.usage?.current_period_end ? new Date(user.usage.current_period_end).toLocaleDateString() : "Next month"}
              </p>
            </div>

            <div className="pt-2">
              <Link
                href="/pricing"
                onClick={() => setQuotaModalOpen(false)}
                className="w-full inline-flex items-center justify-center gap-2 px-5 py-3 rounded-lg bg-cyber text-black font-rajdhani font-bold text-sm tracking-wider hover:bg-cyber/90 transition-all shadow-[0_0_20px_rgba(0,255,200,0.3)]"
              >
                UPGRADE TO PRO OR INSTITUTIONAL
                <ArrowRight size={14} />
              </Link>
            </div>
          </div>
        ) : (
          /* Sign In View */
          <div className="text-center space-y-5 pt-2">
            <div className="w-12 h-12 mx-auto rounded-full bg-cyber/10 border border-cyber/30 flex items-center justify-center text-cyber">
              <Shield size={24} />
            </div>

            <div className="space-y-1">
              <h2 className="text-xl font-rajdhani font-bold tracking-wider text-white">
                SIGN IN WITH GOOGLE
              </h2>
              <p className="text-xs font-mono text-slate-400">
                Get 10 free 3D model generations every month.
              </p>
            </div>

            {/* Feature Perks */}
            <div className="py-2 space-y-2 text-left text-xs font-mono text-slate-300">
              <div className="flex items-center gap-2">
                <Sparkles size={12} className="text-cyber" />
                <span>10 free AI model searches & generations/month</span>
              </div>
              <div className="flex items-center gap-2">
                <Sparkles size={12} className="text-cyber" />
                <span>Interactive 3D viewing & mesh segmentation</span>
              </div>
              <div className="flex items-center gap-2">
                <Sparkles size={12} className="text-cyber" />
                <span>Persistent model history & exports</span>
              </div>
            </div>

            {/* Google Sign In Button Container */}
            <div className="flex justify-center pt-2">
              <div ref={googleBtnRef} />
            </div>

            {!clientId && (
              <p className="text-[11px] font-mono text-amber-400/90 bg-amber-500/10 p-2 rounded border border-amber-500/20">
                ⚠️ Set <code className="text-white">NEXT_PUBLIC_GOOGLE_CLIENT_ID</code> in your .env to enable Google OAuth popup.
              </p>
            )}

            <p className="text-[10px] font-mono text-slate-500">
              By signing in, you agree to Meshworks terms of service and usage quotas.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
