"use client";

import React, { useState } from "react";
import { useAuth } from "../context/AuthContext";
import { Check, Zap, Shield, Building, Sparkles, ArrowRight, Loader2 } from "lucide-react";
import axios from "axios";

declare global {
  interface Window {
    Razorpay?: any;
  }
}

interface PlanItem {
  id: string;
  name: string;
  price: string;
  period: string;
  description: string;
  badge?: string;
  popular?: boolean;
  features: string[];
  type: "individual" | "institutional";
}

const PLANS: PlanItem[] = [
  {
    id: "free",
    name: "FREE TIER",
    price: "₹0",
    period: "forever",
    description: "Ideal for hobbyists exploring AI-powered 3D asset generation.",
    features: [
      "10 model searches & generations / month",
      "1 Free VR Holodeck session / month (10 min session)",
      "Standard 3D WebGL viewer & inspection",
      "Direct GLB export download",
      "Community model archive access",
    ],
    type: "individual",
  },
  {
    id: "pro_monthly",
    name: "PRO MONTHLY",
    price: "₹999",
    period: "per month",
    description: "For individual 3D designers, game devs, and digital artists.",
    features: [
      "250 model generations / month",
      "25 VR Holodeck sessions / month (45 min session)",
      "Multi-part mesh segmentation & semantic renaming",
      "Interactive 3D narrative & part inspection",
      "Priority web discovery & fallback mesh generator",
      "High-speed dedicated queue",
    ],
    type: "individual",
  },
  {
    id: "pro_yearly",
    name: "PRO YEARLY",
    price: "₹9,999",
    period: "per year",
    popular: true,
    badge: "SAVE 17%",
    description: "Maximum efficiency and cost savings for serious creators.",
    features: [
      "3,000 model generations / year",
      "300 VR Holodeck sessions / year (Unlimited session duration)",
      "Everything in Pro Monthly",
      "Full 4GB persistent LRU model cache",
      "Early access to new AI & segmentation models",
      "Commercial usage license included",
    ],
    type: "individual",
  },
  {
    id: "institutional_1yr",
    name: "INSTITUTIONAL (1 YEAR)",
    price: "₹49,999",
    period: "1-year term",
    badge: "FOR LABS & ACADEMIA",
    description: "Deploy campus-wide or across research laboratories.",
    features: [
      "10,000 pooled generations / year",
      "1,000 Pooled VR Holodeck classroom sessions / year",
      "Up to 50 active student/staff seats",
      "Email domain auto-onboarding (e.g. @university.edu)",
      "Centralized usage monitoring & analytics",
      "Standard academic support & SLA",
    ],
    type: "institutional",
  },
  {
    id: "institutional_2yr",
    name: "INSTITUTIONAL (2 YEARS)",
    price: "₹89,999",
    period: "2-year term",
    popular: true,
    badge: "BEST VALUE FOR UNIVERSITIES",
    description: "Multi-year institution contract with dedicated compute & support.",
    features: [
      "25,000 pooled generations across 2 years",
      "3,000 Pooled VR Holodeck classroom sessions / 2 years",
      "Up to 100 active student/staff seats",
      "Custom domain auto-join & role access control",
      "Dedicated API rate limits & batch processing",
      "Priority engineering support & feature requests",
    ],
    type: "institutional",
  },
];

export default function PricingPage() {
  const { user, setLoginModalOpen, refreshUser } = useAuth();
  const [activeTab, setActiveTab] = useState<"individual" | "institutional">("individual");
  const [loadingPlan, setLoadingPlan] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const currentPlanId = user?.subscription?.plan_id || "free";

  // Load Razorpay Checkout script dynamically
  const loadRazorpayScript = (): Promise<boolean> => {
    return new Promise((resolve) => {
      if (window.Razorpay) {
        resolve(true);
        return;
      }
      const script = document.createElement("script");
      script.src = "https://checkout.razorpay.com/v1/checkout.js";
      script.onload = () => resolve(true);
      script.onerror = () => resolve(false);
      document.body.appendChild(script);
    });
  };

  const handleSubscribe = async (planId: string) => {
    if (planId === "free" || planId === currentPlanId) return;

    if (!user) {
      setLoginModalOpen(true);
      return;
    }

    setLoadingPlan(planId);
    try {
      const isLoaded = await loadRazorpayScript();
      if (!isLoaded) {
        alert("Failed to load Razorpay payment gateway. Please check your internet connection.");
        setLoadingPlan(null);
        return;
      }

      // 1. Create order on backend
      const orderRes = await axios.post<{
        order_id: string;
        amount: number;
        currency: string;
        key_id: string;
        plan_id: string;
        plan_name: string;
      }>("/api/payments/create-order", { plan_id: planId });

      const { order_id, amount, currency, key_id, plan_name } = orderRes.data;

      // 2. Open Razorpay Checkout modal
      const options = {
        key: key_id,
        amount: amount,
        currency: currency,
        name: "Meshworks 3D",
        description: `Subscription: ${plan_name}`,
        order_id: order_id,
        prefill: {
          name: user.name,
          email: user.email,
        },
        theme: {
          color: "#00FFC8",
        },
        handler: async function (response: {
          razorpay_payment_id: string;
          razorpay_order_id: string;
          razorpay_signature: string;
        }) {
          try {
            // 3. Verify payment signature on backend
            await axios.post("/api/payments/verify", {
              razorpay_order_id: response.razorpay_order_id,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_signature: response.razorpay_signature,
              plan_id: planId,
            });

            await refreshUser();
            setSuccessMessage(`Congratulations! Your ${plan_name} has been activated.`);
          } catch (verifyErr) {
            console.error("[Payment] Verification failed:", verifyErr);
            alert("Payment completed but signature verification failed. Please contact support.");
          } finally {
            setLoadingPlan(null);
          }
        },
        modal: {
          ondismiss: function () {
            setLoadingPlan(null);
          },
        },
      };

      const rzp = new window.Razorpay(options);
      rzp.open();
    } catch (err: any) {
      console.error("[Payment] Order error:", err);
      const detail = err.response?.data?.detail;
      alert(typeof detail === "string" ? detail : "Failed to initiate payment. Please verify Razorpay credentials.");
      setLoadingPlan(null);
    }
  };

  const filteredPlans = PLANS.filter((p) => p.type === activeTab);

  return (
    <div className="max-w-7xl mx-auto px-6 lg:px-12 py-12 lg:py-16">
      {/* Header */}
      <div className="text-center max-w-3xl mx-auto space-y-4 mb-12">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-cyber/10 border border-cyber/30 text-cyber text-xs font-mono tracking-widest">
          <Sparkles size={12} />
          SUBSCRIPTION & LICENSING PLANS
        </div>

        <h1 className="font-rajdhani text-4xl sm:text-5xl lg:text-6xl text-white font-bold leading-tight">
          ACCELERATE YOUR <span className="text-cyber text-glow-cyan">3D WORKFLOW</span>
        </h1>

        <p className="text-slate-400 text-sm sm:text-base font-mono">
          Start free with 10 generations every month. Scale with Pro creator quotas or multi-year institutional licenses for your university or lab.
        </p>

        {/* Tab Switcher */}
        <div className="inline-flex p-1 bg-white/5 border border-white/10 rounded-xl mt-6">
          <button
            onClick={() => setActiveTab("individual")}
            className={`px-6 py-2 rounded-lg text-xs font-mono tracking-wider transition-all ${
              activeTab === "individual"
                ? "bg-cyber text-black font-bold shadow-[0_0_20px_rgba(0,255,200,0.3)]"
                : "text-slate-400 hover:text-white"
            }`}
          >
            CREATOR PLANS
          </button>
          <button
            onClick={() => setActiveTab("institutional")}
            className={`px-6 py-2 rounded-lg text-xs font-mono tracking-wider transition-all ${
              activeTab === "institutional"
                ? "bg-cyber text-black font-bold shadow-[0_0_20px_rgba(0,255,200,0.3)]"
                : "text-slate-400 hover:text-white"
            }`}
          >
            INSTITUTIONAL LICENSES
          </button>
        </div>
      </div>

      {/* Success Banner */}
      {successMessage && (
        <div className="max-w-2xl mx-auto mb-10 p-4 bg-emerald-500/10 border border-emerald-500/30 rounded-xl text-center space-y-2">
          <div className="text-emerald-400 font-rajdhani text-lg font-bold">
            {successMessage}
          </div>
          <button
            onClick={() => setSuccessMessage(null)}
            className="text-xs font-mono text-emerald-300 underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Plans Grid */}
      <div className={`grid gap-8 ${filteredPlans.length === 3 ? "lg:grid-cols-3" : "lg:grid-cols-2 max-w-4xl mx-auto"}`}>
        {filteredPlans.map((plan) => {
          const isCurrent = currentPlanId === plan.id;
          const isProYearly = plan.id === "pro_yearly";
          const isInst2Yr = plan.id === "institutional_2yr";
          const isHighlight = plan.popular;

          return (
            <div
              key={plan.id}
              className={`relative flex flex-col justify-between p-8 rounded-2xl border transition-all duration-300 ${
                isHighlight
                  ? "bg-slate-900/80 border-cyber shadow-[0_0_40px_rgba(0,255,200,0.15)] ring-1 ring-cyber"
                  : "bg-slate-950/60 border-white/10 hover:border-white/20"
              }`}
            >
              {/* Badge */}
              {plan.badge && (
                <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-0.5 rounded-full bg-cyber text-black font-mono text-[10px] font-bold tracking-widest uppercase">
                  {plan.badge}
                </div>
              )}

              <div>
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h3 className="font-rajdhani text-xl font-bold text-white tracking-wider">
                      {plan.name}
                    </h3>
                    <p className="text-xs font-mono text-slate-400 mt-1 min-h-[32px]">
                      {plan.description}
                    </p>
                  </div>
                  {plan.type === "institutional" ? (
                    <Building className="text-cyber shrink-0" size={24} />
                  ) : (
                    <Zap className={isHighlight ? "text-cyber" : "text-slate-500"} size={22} />
                  )}
                </div>

                {/* Price */}
                <div className="my-6 pb-6 border-b border-white/5">
                  <span className="font-rajdhani text-4xl sm:text-5xl font-bold text-white">
                    {plan.price}
                  </span>
                  <span className="text-xs font-mono text-slate-500 ml-2">
                    / {plan.period}
                  </span>
                </div>

                {/* Feature List */}
                <ul className="space-y-3 mb-8">
                  {plan.features.map((feature, i) => (
                    <li key={i} className="flex items-start gap-3 text-xs font-mono text-slate-300">
                      <Check size={14} className="text-cyber shrink-0 mt-0.5" />
                      <span>{feature}</span>
                    </li>
                  ))}
                </ul>
              </div>

              {/* Action Button */}
              <div>
                <button
                  disabled={isCurrent || loadingPlan === plan.id}
                  onClick={() => handleSubscribe(plan.id)}
                  className={`w-full py-3.5 px-4 rounded-xl font-rajdhani font-bold text-sm tracking-wider flex items-center justify-center gap-2 transition-all duration-200 ${
                    isCurrent
                      ? "bg-white/5 text-slate-500 cursor-default border border-white/10"
                      : isHighlight
                        ? "bg-cyber text-black hover:bg-cyber/90 shadow-[0_0_25px_rgba(0,255,200,0.3)]"
                        : "bg-white/10 text-white hover:bg-white/20 border border-white/10"
                  }`}
                >
                  {loadingPlan === plan.id ? (
                    <>
                      <Loader2 size={16} className="animate-spin" />
                      INITIATING PAYMENT...
                    </>
                  ) : isCurrent ? (
                    "CURRENT PLAN"
                  ) : (
                    <>
                      {plan.id === "free" ? "ACTIVE BY DEFAULT" : "UPGRADE NOW"}
                      <ArrowRight size={14} />
                    </>
                  )}
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Institutional Inquiry Footer */}
      <div className="mt-16 p-8 rounded-2xl bg-gradient-to-r from-slate-950 via-slate-900 to-slate-950 border border-white/10 text-center max-w-3xl mx-auto space-y-4">
        <Shield size={32} className="mx-auto text-cyber" />
        <h3 className="font-rajdhani text-2xl font-bold text-white tracking-wider">
          NEED CUSTOM INSTITUTIONAL SEATS OR PURCHASE ORDERS?
        </h3>
        <p className="text-xs font-mono text-slate-400 max-w-lg mx-auto">
          We offer custom invoicing, GST compliance, and on-premise model segmentation setups for accredited academic institutions and research teams.
        </p>
        <div className="pt-2">
          <a
            href="mailto:contact@meshworks.io?subject=Institutional%20Inquiry%20Prompt2Mesh"
            className="inline-flex items-center gap-2 px-6 py-2.5 rounded-lg border border-white/20 hover:border-cyber text-xs font-mono text-white transition-colors"
          >
            CONTACT ENTERPRISE DESK
            <ArrowRight size={12} />
          </a>
        </div>
      </div>
    </div>
  );
}
