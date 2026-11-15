import React from "react";
import Link from "next/link";
import { Shield, ArrowLeft, Mail, Lock, Eye, FileText, CheckCircle2 } from "lucide-react";

export const metadata = {
  title: "Privacy Policy | MeshWorks",
  description: "Privacy Policy and Google OAuth Data Practices for MeshWorks 3D Platform",
};

export default function PrivacyPolicyPage() {
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
            <Shield size={12} />
            LEGAL COMPLIANCE & PRIVACY
          </div>
          <h1 className="text-3xl md:text-5xl font-rajdhani font-bold tracking-tight text-white mb-3">
            PRIVACY POLICY
          </h1>
          <p className="text-sm font-mono text-slate-400">
            Last Updated: November 15, 2026 • Effective Immediately
          </p>
        </div>

        {/* Content Sections */}
        <div className="space-y-10 text-sm font-sans leading-relaxed text-slate-300">
          {/* Section 1: Overview */}
          <section className="space-y-3">
            <h2 className="text-xl font-rajdhani font-bold text-white flex items-center gap-2">
              <span className="text-cyber">01.</span> Overview & Scope
            </h2>
            <p>
              Welcome to <strong>MeshWorks</strong> (accessible at{" "}
              <a href="https://meshworks.systems" className="text-cyber hover:underline">
                https://meshworks.systems
              </a>
              ). MeshWorks provides an AI-driven 3D model discovery, segmentation, viewing, and generation platform.
              We are committed to respecting your privacy and protecting the personal information you share with us.
            </p>
            <p>
              This Privacy Policy explains what information we collect, how it is used, how Google OAuth data is handled,
              and your rights regarding your personal information.
            </p>
          </section>

          {/* Section 2: Information We Collect */}
          <section className="space-y-4">
            <h2 className="text-xl font-rajdhani font-bold text-white flex items-center gap-2">
              <span className="text-cyber">02.</span> Information We Collect
            </h2>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="p-4 rounded-xl bg-white/[0.02] border border-white/10 space-y-2">
                <div className="flex items-center gap-2 text-cyber font-mono text-xs font-bold">
                  <Lock size={14} />
                  GOOGLE ACCOUNT DATA
                </div>
                <p className="text-xs text-slate-400">
                  When you sign in with Google OAuth, we receive non-sensitive identity claims: your verified Google Email, Full Name, Profile Picture, and unique Google Subject ID.
                </p>
              </div>

              <div className="p-4 rounded-xl bg-white/[0.02] border border-white/10 space-y-2">
                <div className="flex items-center gap-2 text-cyber font-mono text-xs font-bold">
                  <FileText size={14} />
                  USAGE & GENERATION DATA
                </div>
                <p className="text-xs text-slate-400">
                  Search prompts, generated 3D assets (.glb files), mesh segmentation metadata, and monthly generation quota counters associated with your account.
                </p>
              </div>
            </div>
            <p>
              We do <strong>not</strong> collect passwords, financial bank details, or sensitive permissions (such as Google Drive or Gmail content).
            </p>
          </section>

          {/* Section 3: Google API Services User Data Policy */}
          <section className="space-y-3 p-5 rounded-xl bg-cyber/[0.03] border border-cyber/20">
            <h2 className="text-xl font-rajdhani font-bold text-cyber flex items-center gap-2">
              <CheckCircle2 size={18} />
              Google OAuth Limited Use Disclosure
            </h2>
            <p className="text-xs md:text-sm text-slate-300">
              MeshWorks&apos; use and transfer to any other app of information received from Google APIs adheres to the{" "}
              <a
                href="https://developers.google.com/terms/api-services-user-data-policy"
                target="_blank"
                rel="noopener noreferrer"
                className="text-cyber underline font-mono"
              >
                Google API Services User Data Policy
              </a>
              , including the Limited Use requirements.
            </p>
            <ul className="list-disc list-inside text-xs text-slate-400 space-y-1">
              <li>We only use Google OAuth data to authenticate you and manage your user account session.</li>
              <li>We never sell your Google user data to data brokers or third parties.</li>
              <li>We do not use your Google data to serve advertisements or train unrelated external generalized AI models.</li>
            </ul>
          </section>

          {/* Section 4: How We Use Your Data */}
          <section className="space-y-3">
            <h2 className="text-xl font-rajdhani font-bold text-white flex items-center gap-2">
              <span className="text-cyber">03.</span> How We Use Your Information
            </h2>
            <ul className="list-disc list-inside space-y-2 text-slate-300">
              <li><strong>Session Authentication:</strong> Securely identifying your account and granting access to your 3D assets.</li>
              <li><strong>Quota Tracking:</strong> Monitoring monthly generation limits (10 free searches/month or subscription limits).</li>
              <li><strong>Payment Processing:</strong> Managing plan activations via Razorpay using order IDs (we never store raw card or UPI PIN data).</li>
              <li><strong>Platform Optimization:</strong> Improving mesh rendering, caching popular 3D models, and maintaining server stability.</li>
            </ul>
          </section>

          {/* Section 5: Data Storage and Security */}
          <section className="space-y-3">
            <h2 className="text-xl font-rajdhani font-bold text-white flex items-center gap-2">
              <span className="text-cyber">04.</span> Data Security & Retention
            </h2>
            <p>
              All account information is transmitted over encrypted HTTPS channels (TLS 1.3) and stored in secured MongoDB Atlas clusters protected by IP whitelisting and role-based access control. Generated 3D assets are cached under an automated LRU storage lifecycle.
            </p>
          </section>

          {/* Section 6: Data Deletion & Your Rights */}
          <section className="space-y-3">
            <h2 className="text-xl font-rajdhani font-bold text-white flex items-center gap-2">
              <span className="text-cyber">05.</span> Your Rights & Account Deletion
            </h2>
            <p>
              You have the right to request access to your personal information or request permanent deletion of your account and associated search history at any time.
            </p>
            <p>
              To request account or data deletion, or to revoke authorization, email us at{" "}
              <a href="mailto:prompt2mesh@gmail.com" className="text-cyber font-mono hover:underline">
                prompt2mesh@gmail.com
              </a>
              . You may also revoke MeshWorks access via your{" "}
              <a
                href="https://myaccount.google.com/permissions"
                target="_blank"
                rel="noopener noreferrer"
                className="text-cyber hover:underline font-mono"
              >
                Google Security Permissions
              </a>{" "}
              page at any time.
            </p>
          </section>

          {/* Section 7: Contact */}
          <section className="pt-6 border-t border-white/10 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
            <div>
              <h3 className="text-base font-rajdhani font-bold text-white">Questions about our privacy practices?</h3>
              <p className="text-xs font-mono text-slate-400">Our engineering and legal team is available to help.</p>
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
