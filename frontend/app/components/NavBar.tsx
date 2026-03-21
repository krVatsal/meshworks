"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Search, Clock, Cpu } from "lucide-react";

export default function NavBar() {
  const pathname = usePathname();

  const statusLabel =
    pathname === "/"
      ? "DISCOVERY:MODE"
      : pathname === "/history"
        ? "ARCHIVE:VIEW"
        : "VIEWER:3D";

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
              MESHVAULT
            </span>
            <span className="hidden sm:block text-[10px] font-mono text-cyber/60 tracking-widest pt-0.5">
              v1.0
            </span>
          </Link>

          {/* Nav links */}
          <div className="flex items-center gap-1">
            <Link
              href="/"
              data-testid="nav-search"
              className={`flex items-center gap-2 px-4 py-1.5 text-xs font-mono tracking-widest transition-colors duration-200 ${
                pathname === "/"
                  ? "text-cyber border-b border-cyber"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <Search size={12} />
              SEARCH
            </Link>
            <Link
              href="/history"
              data-testid="nav-history"
              className={`flex items-center gap-2 px-4 py-1.5 text-xs font-mono tracking-widest transition-colors duration-200 ${
                pathname === "/history"
                  ? "text-cyber border-b border-cyber"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <Clock size={12} />
              HISTORY
            </Link>
          </div>
        </div>
      </nav>

      {/* Status bar */}
      <div className="border-b border-white/5 bg-black/40 backdrop-blur-sm px-6 lg:px-12 py-1.5 flex items-center gap-4">
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
    </>
  );
}
