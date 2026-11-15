import React from "react";
import Link from "next/link";
import { Scale, ArrowLeft, Mail, AlertCircle, CheckCircle2, CreditCard, ShieldCheck } from "lucide-react";

export const metadata = {
  title: "Terms of Service | MeshWorks",
  description: "Terms and Conditions of Use for MeshWorks AI 3D Generation Platform",
};

export default function TermsOfServicePage() {
  return (
    <div className="min-h-screen bg-black text-slate-200 selection:bg-cyber selection:text-black">
      {/* Background radial gradient */}
      <div className="fixed inset-0 bg-[radial-gradient(ellipse_80%_80%_at_50%_-20%,rgba(0,255,200,0.06),rgba(0,0,0,0))] pointer-events-none" />

      <main className="relative z-10 max-w-4xl mx-auto px-6 py-12 md:py-16">
        {/* Navigation Breadcrumb */}
        <div className="mb-8">
          <Link
            href="/"
            className="inline-flex items-center gap-2 text-xs font-mono text-slate-400 hover:text-cyber transition-colors"
          >
            <ArrowLeft size={14} />
            BACK TO MESHWORKS
          </Link>
        </div>

        {/* Header */}
        <div className="border-b border-white/10 pb-8 mb-10">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-cyber/10 border border-cyber/30 text-cyber text-xs font-mono mb-4">
            <Scale size={12} />
            LEGAL TERMS & CONDITIONS
          </div>
          <h1 className="text-3xl md:text-5xl font-rajdhani font-bold tracking-tight text-white mb-3">
            TERMS OF SERVICE
          </h1>
          <p className="text-sm font-mono text-slate-400">
            Last Updated: November 15, 2026 • Effective Date: November 15, 2026
          </p>
        </div>

        {/* Content Sections */}
        <div className="space-y-10 text-sm font-sans leading-relaxed text-slate-300">
          {/* Section 1: Agreement to Terms */}
          <section className="space-y-3">
            <h2 className="text-xl font-rajdhani font-bold text-white flex items-center gap-2">
              <span className="text-cyber">01.</span> Agreement to Terms
            </h2>
            <p>
              By accessing or using <strong>MeshWorks</strong> (located at{" "}
              <a href="https://meshworks.systems" className="text-cyber hover:underline">
                https://meshworks.systems
              </a>
              ), whether as a guest or registered user, you agree to be bound by these Terms of Service
              (&ldquo;Terms&rdquo;) and our Privacy Policy. If you do not agree to these Terms, please do not use the service.
            </p>
          </section>

          {/* Section 2: Platform Description & Quotas */}
          <section className="space-y-4">
            <h2 className="text-xl font-rajdhani font-bold text-white flex items-center gap-2">
              <span className="text-cyber">02.</span> Service Description & Quotas
            </h2>
            <p>
              MeshWorks provides an intelligent 3D asset generation, multi-mesh segmentation, scoring, and WebGL viewer engine.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="p-4 rounded-xl bg-white/[0.02] border border-white/10 space-y-1.5">
                <div className="flex items-center gap-2 text-cyber font-mono text-xs font-bold">
                  <CheckCircle2 size={14} />
                  FREE TIER LIMITS
                </div>
                <p className="text-xs text-slate-400">
                  Every registered user receives 10 free AI model searches and generations per calendar month. Quota resets automatically every 30 days.
                </p>
              </div>

              <div className="p-4 rounded-xl bg-white/[0.02] border border-white/10 space-y-1.5">
                <div className="flex items-center gap-2 text-cyber font-mono text-xs font-bold">
                  <CreditCard size={14} />
                  PRO & INSTITUTIONAL PLANS
                </div>
                <p className="text-xs text-slate-400">
                  Users requiring higher generation capacities may upgrade to monthly/yearly Pro subscriptions or multi-seat Institutional licenses.
                </p>
              </div>
            </div>
          </section>

          {/* Section 3: User Accounts & Authentication */}
          <section className="space-y-3">
            <h2 className="text-xl font-rajdhani font-bold text-white flex items-center gap-2">
              <span className="text-cyber">03.</span> User Accounts & Authentication
            </h2>
            <p>
              User authentication is managed via Google OAuth. You are responsible for safeguarding your Google account credentials and for all activities that occur under your MeshWorks account.
            </p>
            <p>
              Automated scraping, bulk unauthorized account creation, or attempts to circumvent quota enforcement are strictly prohibited and may result in immediate suspension.
            </p>
          </section>

          {/* Section 4: Payments & Billing */}
          <section className="space-y-3">
            <h2 className="text-xl font-rajdhani font-bold text-white flex items-center gap-2">
              <span className="text-cyber">04.</span> Payments, Subscriptions & Refunds
            </h2>
            <p>
              Payments for Pro and Institutional plans are securely processed through Razorpay. All prices are listed in Indian Rupees (INR) and are inclusive of applicable taxes unless stated otherwise.
            </p>
            <ul className="list-disc list-inside space-y-1 text-slate-400 text-xs md:text-sm">
              <li><strong>Billing Cycle:</strong> Monthly plans renew on a 30-day basis; annual and institutional licenses remain active for 365 or 730 days from purchase.</li>
              <li><strong>Cancellations:</strong> You can cancel recurring subscriptions at any time through your account portal.</li>
              <li><strong>Refund Policy:</strong> Due to computational GPU costs incurred during 3D generative inference, fees paid for completed generations are non-refundable, except in cases of verified billing errors.</li>
            </ul>
          </section>

          {/* Section 5: Intellectual Property */}
          <section className="space-y-3">
            <h2 className="text-xl font-rajdhani font-bold text-white flex items-center gap-2">
              <span className="text-cyber">05.</span> Intellectual Property & 3D Assets
            </h2>
            <p>
              You retain all ownership rights to the creative prompts you submit. To the extent permitted by applicable law, you own the 3D model outputs generated specifically for your account, subject to third-party open-license terms (such as CC-BY) where community base meshes are sourced or scored.
            </p>
          </section>

          {/* Section 6: Acceptable Use Policy */}
          <section className="space-y-3 p-5 rounded-xl bg-white/[0.02] border border-white/10">
            <h2 className="text-xl font-rajdhani font-bold text-white flex items-center gap-2">
              <AlertCircle size={18} className="text-amber-400" />
              Acceptable Use Policy
            </h2>
            <p className="text-xs text-slate-300">
              You agree not to use MeshWorks to generate, upload, or distribute:
            </p>
            <ul className="list-disc list-inside text-xs text-slate-400 space-y-1">
              <li>Content that infringes upon third-party patents, copyrights, or trademarks.</li>
              <li>Malicious code, exploits, or automated bot traffic designed to disrupt platform servers.</li>
              <li>Content that violates applicable local, national, or international laws.</li>
            </ul>
          </section>

          {/* Section 7: Limitation of Liability */}
          <section className="space-y-3">
            <h2 className="text-xl font-rajdhani font-bold text-white flex items-center gap-2">
              <span className="text-cyber">06.</span> Disclaimers & Limitation of Liability
            </h2>
            <p>
              MeshWorks is provided on an &ldquo;AS IS&rdquo; and &ldquo;AS AVAILABLE&rdquo; basis without warranties of any kind. We do not warrant that 3D model generation will be uninterrupted, error-free, or meet every specific geometric fabrication or 3D printing constraint.
            </p>
          </section>

          {/* Section 8: Contact */}
          <section className="pt-6 border-t border-white/10 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
            <div>
              <h3 className="text-base font-rajdhani font-bold text-white">Questions regarding these terms?</h3>
              <p className="text-xs font-mono text-slate-400">Reach out to our platform administrators.</p>
            </div>
            <a
              href="mailto:prompt2mesh@gmail.com"
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-cyber/10 border border-cyber/30 text-cyber hover:bg-cyber/20 text-xs font-mono transition-colors"
            >
              <Mail size={14} />
              prompt2mesh@gmail.com
            </a>
          </section>
        </div>
      </main>
    </div>
  );
}
