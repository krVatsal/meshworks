"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Search, Clock, Cpu, CreditCard, LogOut, User as UserIcon, Zap } from "lucide-react";
import { useAuth } from "../context/AuthContext";

export default function NavBar() {
  const pathname = usePathname();
  const { user, setLoginModalOpen, logout } = useAuth();
  const [userMenuOpen, setUserMenuOpen] = useState(false);

  const statusLabel =
    pathname === "/"
      ? "DISCOVERY:MODE"
      : pathname === "/history"
        ? "ARCHIVE:VIEW"
        : pathname === "/pricing"
          ? "SUBSCRIPTION:PLANS"
          : "VIEWER:3D";

  const planId = user?.subscription?.plan_id || "free";
  const gensCount = user?.usage?.generations_count ?? 0;
  const gensLimit = user?.usage?.generations_limit ?? 10;
  const isFree = planId === "free";

  return (
    <>
      {/* Top Nav */}
      <nav className="sticky top-0 z-50 border-b border-white/5 bg-black/60 backdrop-blur-xl">
        <div className="max-w-screen-2xl mx-auto px-6 lg:px-12 h-14 flex items-center justify-between">
          {/* Logo */}
          <Link
            href="/"
            className="flex items-center gap-3 group"
            data-testid="nav-logo"
          >
            <div className="relative w-7 h-7">
              <div className="absolute inset-0 border border-cyber/50 rotate-45 group-hover:border-cyber transition-colors duration-300" />
              <div className="absolute inset-1 border border-cyber/30 rotate-45 group-hover:border-cyber/60 transition-colors duration-300" />
              <Cpu
                size={11}
                className="absolute inset-0 m-auto text-cyber"
              />
            </div>
            <span className="font-rajdhani font-bold text-lg tracking-widest text-white">
              MESHWORK
            </span>
            <span className="hidden sm:block text-[10px] font-mono text-cyber/60 tracking-widest pt-0.5">
              v1.0
            </span>
          </Link>

          {/* Nav links & User Actions */}
          <div className="flex items-center gap-2 sm:gap-4">
            <Link
              href="/"
              data-testid="nav-search"
              className={`flex items-center gap-2 px-3 py-1.5 text-xs font-mono tracking-widest transition-colors duration-200 ${
                pathname === "/"
                  ? "text-cyber border-b border-cyber"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <Search size={12} />
              <span className="hidden sm:inline">SEARCH</span>
            </Link>

            <Link
              href="/history"
              data-testid="nav-history"
              className={`flex items-center gap-2 px-3 py-1.5 text-xs font-mono tracking-widest transition-colors duration-200 ${
                pathname === "/history"
                  ? "text-cyber border-b border-cyber"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <Clock size={12} />
              <span className="hidden sm:inline">HISTORY</span>
            </Link>

            <Link
              href="/pricing"
              data-testid="nav-pricing"
              className={`flex items-center gap-2 px-3 py-1.5 text-xs font-mono tracking-widest transition-colors duration-200 ${
                pathname === "/pricing"
                  ? "text-cyber border-b border-cyber"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <CreditCard size={12} />
              <span className="hidden sm:inline">PLANS</span>
            </Link>

            {/* Quota & User Profile or Login */}
            {user ? (
              <div className="relative flex items-center gap-3 pl-2 border-l border-white/10">
                {/* Quota Badge */}
                <Link
                  href="/pricing"
                  className={`hidden md:flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-mono tracking-wider border transition-all ${
                    isFree && gensCount >= gensLimit
                      ? "bg-red-500/10 border-red-500/30 text-red-400"
                      : isFree
                        ? "bg-cyber/10 border-cyber/30 text-cyber"
                        : "bg-purple-500/10 border-purple-500/30 text-purple-300 font-bold"
                  }`}
                  title={isFree ? `${gensCount}/${gensLimit} free generations used this month` : `${planId.toUpperCase()} Active`}
                >
                  <Zap size={10} className={isFree ? "text-cyber" : "text-purple-400"} />
                  {isFree ? `${gensCount}/${gensLimit} GENS` : planId.toUpperCase()}
                </Link>

                {/* User Dropdown Button */}
                <div className="relative">
                  <button
                    onClick={() => setUserMenuOpen(!userMenuOpen)}
                    className="flex items-center gap-2 p-1 rounded-full hover:ring-2 hover:ring-cyber/40 transition-all"
                  >
                    {user.picture ? (
                      <img
                        src={user.picture}
                        alt={user.name}
                        className="w-7 h-7 rounded-full border border-cyber/30 object-cover"
                      />
                    ) : (
                      <div className="w-7 h-7 rounded-full bg-slate-800 border border-cyber/30 flex items-center justify-center text-cyber text-xs font-bold font-mono">
                        {user.name.charAt(0).toUpperCase()}
                      </div>
                    )}
                  </button>

                  {/* Dropdown Menu */}
                  {userMenuOpen && (
                    <div
                      className="absolute right-0 mt-2 w-56 p-2 bg-slate-950 border border-white/10 rounded-xl shadow-2xl z-50 animate-fade-in font-mono text-xs"
                      onMouseLeave={() => setUserMenuOpen(false)}
                    >
                      <div className="px-3 py-2 border-b border-white/10 space-y-0.5">
                        <div className="font-bold text-white truncate">{user.name}</div>
                        <div className="text-[10px] text-slate-400 truncate">{user.email}</div>
                        <div className="text-[9px] text-cyber uppercase tracking-wider pt-1">
                          Plan: {planId} ({gensCount}/{gensLimit} used)
                        </div>
                      </div>

                      <div className="py-1">
                        <Link
                          href="/pricing"
                          onClick={() => setUserMenuOpen(false)}
                          className="flex items-center gap-2 w-full px-3 py-2 rounded-lg hover:bg-white/5 text-slate-300 hover:text-white transition-colors"
                        >
                          <CreditCard size={13} />
                          Manage Subscription
                        </Link>

                        <button
                          onClick={() => {
                            setUserMenuOpen(false);
                            logout();
                          }}
                          className="flex items-center gap-2 w-full px-3 py-2 rounded-lg hover:bg-red-500/10 text-red-400 transition-colors"
                        >
                          <LogOut size={13} />
                          Sign Out
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ) : (
              /* Sign In Button */
              <button
                onClick={() => setLoginModalOpen(true)}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-cyber/50 bg-cyber/10 text-cyber hover:bg-cyber hover:text-black font-rajdhani font-bold text-xs tracking-wider transition-all shadow-[0_0_15px_rgba(0,255,200,0.15)]"
              >
                <UserIcon size={12} />
                SIGN IN
              </button>
            )}
          </div>
        </div>
      </nav>

      {/* Status bar */}
      <div className="border-b border-white/5 bg-black/40 backdrop-blur-sm px-6 lg:px-12 py-1.5 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="pulse-dot w-1.5 h-1.5 rounded-full bg-neon-green block" />
            <span className="font-mono text-[10px] text-slate-500 tracking-widest">
              SYS:ONLINE
            </span>
          </div>
          <span className="text-slate-700 text-[10px]">{"//"}</span>
          <span className="font-mono text-[10px] text-slate-500 tracking-widest">
            {statusLabel}
          </span>
        </div>

        {/* Free Quota Notice on Mobile */}
        {user && isFree && (
          <div className="md:hidden font-mono text-[10px] text-cyber">
            {gensCount}/{gensLimit} GENS
          </div>
        )}
      </div>
    </>
  );
}
