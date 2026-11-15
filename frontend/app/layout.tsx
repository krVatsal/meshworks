import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import NavBar from "./components/NavBar";
import { AuthProvider } from "./context/AuthContext";
import AuthModal from "./components/AuthModal";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "MeshWork | 3D Model Discovery",
  description: "Discover 3D models with natural language",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&family=Rajdhani:wght@500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <AuthProvider>
          <div className="grain cyber-grid min-h-screen flex flex-col">
            <NavBar />
            <main className="flex-1">{children}</main>
            <footer className="border-t border-white/5 py-4 px-6 text-center text-xs font-mono text-slate-500">
              <div className="max-w-7xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3">
                <div>© 2026 MeshWorks • AI 3D Platform</div>
                <div className="flex items-center gap-4">
                  <Link href="/privacy" className="hover:text-cyber transition-colors">
                    Privacy Policy
                  </Link>
                  <span className="text-white/10">•</span>
                  <Link href="/terms" className="hover:text-cyber transition-colors">
                    Terms of Service
                  </Link>
                  <span className="text-white/10">•</span>
                  <Link href="/pricing" className="hover:text-cyber transition-colors">
                    Plans
                  </Link>
                </div>
              </div>
            </footer>
            <AuthModal />
          </div>
        </AuthProvider>
      </body>
    </html>
  );
}
