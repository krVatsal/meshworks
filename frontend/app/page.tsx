"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import axios from "axios";
import {
  Search,
  ChevronRight,
  Clock,
  Zap,
  Box,
  Layers,
} from "lucide-react";

const API = `${process.env.NEXT_PUBLIC_BACKEND_URL}/api`;

const LOADING_STEPS = [
  { label: "Analyzing prompt...", sub: "GROQ::LLM_REFINE" },
  { label: "Scanning the web...", sub: "TAVILY::WEB_SEARCH" },
  { label: "Extracting model data...", sub: "PARSER::GLTF_RESOLVE" },
  { label: "Finalizing results...", sub: "DB::COMMIT" },
];

const EXAMPLE_PROMPTS = [
  "a futuristic cyberpunk motorcycle",
  "medieval castle with towers",
  "low-poly forest deer",
  "sci-fi battle robot",
  "ancient Egyptian temple",
  "steampunk airship",
];

interface HistoryItem {
  id: string;
  original_prompt: string;
  created_at: string;
  status: string;
  primary_model?: { type: string };
  attributes?: { object_type: string; style: string };
}



export default function SearchPage() {
  const [prompt, setPrompt] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingStep, setLoadingStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    axios
      .get(`${API}/history?limit=6`)
      .then((res) => {
        if (!cancelled) setHistory(res.data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let interval: ReturnType<typeof setInterval>;
    if (loading) {
      interval = setInterval(() => {
        setLoadingStep((s) => (s + 1) % LOADING_STEPS.length);
      }, 1400);
    }
    return () => clearInterval(interval);
  }, [loading]);



  const handleSearch = async (e?: React.FormEvent) => {
    e?.preventDefault();
    const q = prompt.trim();
    if (!q || loading) return;

    setLoading(true);
    setLoadingStep(0);
    setError(null);

    try {
      const res = await axios.post(`${API}/search`, { prompt: q });
      router.push(`/view/${res.data.id}`);
    } catch (err: unknown) {
      const msg =
        axios.isAxiosError(err)
          ? err.response?.data?.detail || "Search failed. Please try again."
          : "Search failed. Please try again.";
      setError(msg);
      setLoading(false);
    }
  };

  const handleExample = (ex: string) => {
    setPrompt(ex);
    inputRef.current?.focus();
  };

  return (
    <div className="max-w-screen-2xl mx-auto px-6 lg:px-12 py-12 lg:py-20">
      {/* Hero */}
      <div className="mb-16 fade-in-up">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-1 h-8 bg-cyber" />
          <span className="font-mono text-[10px] text-cyber tracking-widest">
            NEURAL_3D_SEARCH // v1.0.0
          </span>
        </div>
        <h1 className="font-rajdhani text-5xl sm:text-6xl lg:text-7xl text-white leading-none mb-4">
          DISCOVER
          <br />
          <span className="text-cyber text-glow-cyan">3D MODELS</span>
        </h1>
        <p className="text-slate-400 text-base max-w-xl leading-relaxed">
          Describe any 3D object in natural language. Our AI refines your prompt
          and searches the web for the best matching GLTF, GLB, or Sketchfab
          models.
        </p>
      </div>

      {/* Search area */}
      <div className="max-w-3xl mb-10 fade-in-up stagger-2">
        <form onSubmit={handleSearch}>
          <div className="animated-border relative">
            <div className="bg-black/80 border border-white/10 backdrop-blur-xl p-1">
              <div className="flex items-center gap-3 px-4 py-3">
                <Search
                  size={18}
                  className={`flex-shrink-0 ${loading ? "text-cyber animate-pulse" : "text-slate-500"}`}
                />
                <input
                  ref={inputRef}
                  type="text"
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder='Describe a 3D model... e.g. futuristic spaceship'
                  className="flex-1 bg-transparent border-none outline-none text-white placeholder-slate-600 font-mono text-sm tracking-wide"
                  disabled={loading}
                  data-testid="search-input"
                  autoComplete="off"
                />
                {prompt && !loading && (
                  <button
                    type="button"
                    onClick={() => setPrompt("")}
                    className="text-slate-600 hover:text-slate-400 transition-colors duration-200 text-lg leading-none px-1"
                  >
                    ×
                  </button>
                )}
                <button
                  type="submit"
                  disabled={!prompt.trim() || loading}
                  data-testid="search-btn"
                  className="flex items-center gap-2 px-5 py-2 bg-cyber/10 border border-cyber/50 text-cyber font-rajdhani font-bold text-sm tracking-widest hover:bg-cyber/20 hover:border-cyber transition-colors duration-200 disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  {loading ? (
                    <span className="animate-pulse">SCANNING</span>
                  ) : (
                    <>
                      SEARCH <ChevronRight size={14} />
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        </form>

        {/* Loading state */}
        {loading && (
          <div className="mt-4 px-4 py-3 bg-black/60 border border-white/5 backdrop-blur-sm">
            <div className="flex items-center gap-3">
              <div className="flex gap-0.5">
                {LOADING_STEPS.map((_, i) => (
                  <div
                    key={i}
                    className={`h-1 w-8 transition-colors duration-500 ${i <= loadingStep ? "bg-cyber" : "bg-white/10"}`}
                  />
                ))}
              </div>
              <div className="ml-2">
                <p className="font-mono text-xs text-cyber tracking-wide">
                  {LOADING_STEPS[loadingStep].label}
                </p>
                <p className="font-mono text-[10px] text-slate-600 tracking-widest mt-0.5">
                  {LOADING_STEPS[loadingStep].sub}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Error */}
        {error && (
          <div
            className="mt-4 px-4 py-3 bg-neon-red/5 border border-neon-red/30"
            data-testid="search-error"
          >
            <p className="font-mono text-xs text-neon-red">{error}</p>
          </div>
        )}
      </div>

      {/* Example prompts */}
      <div className="mb-16 fade-in-up stagger-3">
        <p className="font-mono text-[10px] text-slate-600 tracking-widest mb-3">
          SUGGESTED QUERIES
        </p>
        <div className="flex flex-wrap gap-2">
          {EXAMPLE_PROMPTS.map((ex) => (
            <button
              key={ex}
              onClick={() => handleExample(ex)}
              data-testid={`example-prompt-${ex.slice(0, 10).replace(/\s/g, "-")}`}
              className="px-3 py-1.5 border border-white/10 bg-white/5 text-slate-400 text-xs font-mono hover:border-cyber/40 hover:text-white hover:bg-cyber/5 transition-colors duration-200"
            >
              {ex}
            </button>
          ))}
        </div>
      </div>

      {/* Recent history */}
      {history.length > 0 && (
        <div className="fade-in-up stagger-4">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <Clock size={14} className="text-slate-500" />
              <span className="font-mono text-[10px] text-slate-500 tracking-widest">
                RECENT DISCOVERIES
              </span>
            </div>
            <Link
              href="/history"
              className="font-mono text-[10px] text-cyber/60 hover:text-cyber tracking-widest transition-colors duration-200"
            >
              VIEW ALL →
            </Link>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {history.map((item, i) => (
              <HistoryCard key={item.id} item={item} delay={i} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function HistoryCard({ item, delay }: { item: HistoryItem; delay: number }) {
  const router = useRouter();
  const modelType = item.primary_model?.type;

  const typeColor: Record<string, string> = {
    sketchfab: "text-neon-purple",
    glb: "text-cyber",
    gltf: "text-neon-green",
  };

  const typeIcon =
    modelType === "sketchfab" ? <Layers size={10} /> : <Box size={10} />;

  return (
    <button
      onClick={() => router.push(`/view/${item.id}`)}
      data-testid={`history-card-${item.id}`}
      className={`fade-in-up stagger-${delay + 1} group text-left w-full p-4 bg-obsidian border border-white/5 hover:border-cyber/30 hover:bg-surface transition-colors duration-300 relative overflow-hidden`}
    >
      <div className="absolute top-0 left-0 w-full h-px bg-gradient-to-r from-transparent via-cyber/20 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
      <div className="flex items-start justify-between gap-2 mb-2">
        <p className="font-mono text-[10px] text-slate-600 tracking-widest">
          {new Date(item.created_at).toLocaleDateString()}
        </p>
        {modelType && modelType !== "none" && (
          <span
            className={`flex items-center gap-1 font-mono text-[9px] tracking-widest ${typeColor[modelType] || "text-slate-500"}`}
          >
            {typeIcon}
            {modelType?.toUpperCase()}
          </span>
        )}
      </div>
      <p className="text-white text-sm leading-snug group-hover:text-cyber transition-colors duration-200 line-clamp-2">
        {item.original_prompt}
      </p>
      {item.attributes && (
        <div className="flex gap-1 mt-2 flex-wrap">
          <span className="px-1.5 py-0.5 bg-white/5 text-slate-500 font-mono text-[9px] tracking-wide">
            {item.attributes.object_type}
          </span>
          <span className="px-1.5 py-0.5 bg-white/5 text-slate-500 font-mono text-[9px] tracking-wide">
            {item.attributes.style}
          </span>
        </div>
      )}
      <div className="absolute bottom-3 right-3">
        <Zap
          size={10}
          className="text-white/5 group-hover:text-cyber/30 transition-colors duration-300"
        />
      </div>
    </button>
  );
}
