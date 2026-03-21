"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import axios from "axios";
import ModelViewer from "@/app/components/ModelViewer";
import ChatPanel from "@/app/components/ChatPanel";
import {
  ArrowLeft,
  ExternalLink,
  Box,
  Layers,
  Globe,
  Tag,
  Cpu,
  ChevronRight,
  Zap,
  Copy,
  Check,
  MessageSquare,
  Download,
} from "lucide-react";

const API = `${process.env.NEXT_PUBLIC_BACKEND_URL}/api`;


interface ModelData {
  type: string;
  title: string;
  url?: string;
  embed_url?: string;
  source_url: string;
  source_domain: string;
  is_downloadable?: boolean;
}

interface SearchAttributes {
  object_type: string;
  style: string;
  keywords: string[];
  refined_query: string;
}

interface SearchData {
  id: string;
  original_prompt: string;
  status: string;
  primary_model?: ModelData;
  all_models?: ModelData[];
  attributes?: SearchAttributes;
}

export default function ViewerPage() {
  const params = useParams();
  const id = params.id as string;
  const router = useRouter();
  const [data, setData] = useState<SearchData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeModel, setActiveModel] = useState<ModelData | null>(null);
  const [copied, setCopied] = useState(false);
  const [showChat, setShowChat] = useState(false);
  const [highlightedLabel, setHighlightedLabel] = useState<string | null>(null);
  const [animating, setAnimating] = useState<{segments: {name: string; description: string}[]} | null>(null);
  const [loadingStory, setLoadingStory] = useState(false);

  useEffect(() => {
    let cancelled = false;
    axios
      .get(`${API}/history/${id}`)
      .then((res) => {
        if (!cancelled) {
          setData(res.data);
          setActiveModel(res.data.primary_model ?? null);
        }
      })
      .catch(() => {
        if (!cancelled) setError("Search record not found.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const handleCopy = () => {
    if (data?.original_prompt) {
      navigator.clipboard.writeText(data.original_prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  const startStoryMode = async () => {
    if (!data?.id) return;
    setLoadingStory(true);
    try {
      const res = await axios.get(`${API}/narration/${data.id}`);
      setAnimating({ segments: res.data.segments });
    } catch {
      console.error("Failed to load narration");
    } finally {
      setLoadingStory(false);
    }
  };

  useEffect(() => {
    setHighlightedLabel(null);
  }, [activeModel?.url, activeModel?.embed_url]);

  if (loading) return <ViewerSkeleton />;
  if (error)
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
        <p className="font-mono text-neon-red text-sm">{error}</p>
        <button
          onClick={() => router.push("/")}
          className="font-mono text-[11px] text-cyber/60 hover:text-cyber tracking-widest transition-colors duration-200"
        >
          ← BACK TO SEARCH
        </button>
      </div>
    );

  const modelType = activeModel?.type;
  const typeColors: Record<string, string> = {
    sketchfab: "#BD00FF",
    glb: "#00F0FF",
    gltf: "#00FF94",
  };
  const typeColor = (modelType && typeColors[modelType]) || "#ffffff";

  return (
    <div
      className="flex flex-col lg:flex-row h-[calc(100vh-88px)]"
      data-testid="viewer-page"
    >
      {/* 3D Viewer */}
      <div className="flex-1 relative bg-obsidian border-r border-white/5 min-h-[50vh] lg:min-h-0">
        <ModelViewer 
          model={activeModel} 
          highlightLabel={highlightedLabel}
          animating={animating}
          onAnimationEnd={() => setAnimating(null)}
        />

        {/* Back button overlay */}
        <button
          onClick={() => router.push("/")}
          data-testid="back-to-search"
          className="absolute top-4 left-4 z-20 flex items-center gap-2 px-3 py-1.5 bg-black/80 border border-white/10 text-slate-400 hover:text-white hover:border-white/30 transition-colors duration-200 font-mono text-[11px] tracking-widest backdrop-blur-sm"
        >
          <ArrowLeft size={11} /> BACK
        </button>

        {/* Model type badge */}
        {modelType && modelType !== "none" && (
          <div className="absolute top-4 right-4 z-20">
            <span
              className="px-2 py-1 border font-mono text-[10px] tracking-widest backdrop-blur-sm"
              style={{
                borderColor: `${typeColor}40`,
                color: typeColor,
                background: `${typeColor}10`,
              }}
            >
              {modelType.toUpperCase()}
            </span>
          </div>
        )}
      </div>

      {/* Info Panel */}
      <div className="w-full lg:w-80 xl:w-96 flex flex-col border-t lg:border-t-0 border-white/5 bg-black/40 backdrop-blur-sm overflow-y-auto">
        {/* Chat toggle button */}
        <div className="px-6 pt-4 pb-0">
          <button
            onClick={() => setShowChat((v) => !v)}
            className={`w-full flex items-center justify-center gap-2 py-2 border font-mono text-[10px] tracking-widest transition-colors duration-200 ${
              showChat
                ? "border-cyber/60 bg-cyber/10 text-cyber"
                : "border-white/10 text-slate-500 hover:border-cyber/30 hover:text-cyber/70"
            }`}
          >
            <MessageSquare size={11} />
            {showChat ? "CLOSE CHAT" : "CHAT ABOUT MODEL"}
          </button>

          <button
            onClick={animating 
            ? () => { setAnimating(null); window.speechSynthesis?.cancel(); setHighlightedLabel(null); } 
            : startStoryMode
            }
            disabled={loadingStory}
            className={`mt-2 w-full flex items-center justify-center gap-2 py-2 border font-mono text-[10px] tracking-widest transition-colors duration-200 disabled:opacity-40 disabled:cursor-not-allowed ${
              animating
                ? "border-neon-red/60 bg-neon-red/10 text-neon-red"
                : "border-white/10 text-slate-500 hover:border-cyber/30 hover:text-cyber/70"
            }`}
          >
            {loadingStory ? (
              <span className="animate-pulse">GENERATING STORY...</span>
            ) : animating ? (
              "⏹ STOP STORY"
            ) : (
              "▶ STORY MODE"
            )}
          </button>

        </div>

        {/* Prompt */}
        <div className="p-6 border-b border-white/5">
          <div className="flex items-center justify-between mb-3">
            <span className="font-mono text-[10px] text-slate-600 tracking-widest">
              ORIGINAL PROMPT
            </span>
            <button
              onClick={handleCopy}
              className="text-slate-600 hover:text-cyber transition-colors duration-200"
              data-testid="copy-prompt"
            >
              {copied ? (
                <Check size={12} className="text-neon-green" />
              ) : (
                <Copy size={12} />
              )}
            </button>
          </div>
          <p
            className="text-white text-sm leading-relaxed"
            data-testid="original-prompt"
          >
            {data?.original_prompt}
          </p>
        </div>

        {/* Attributes */}
        {data?.attributes && (
          <div className="p-6 border-b border-white/5">
            <div className="flex items-center gap-2 mb-4">
              <Cpu size={12} className="text-cyber" />
              <span className="font-mono text-[10px] text-slate-600 tracking-widest">
                AI ANALYSIS
              </span>
            </div>
            <div className="space-y-3">
              <AttributeRow
                icon={<Box size={11} />}
                label="Object"
                value={data.attributes.object_type}
              />
              <AttributeRow
                icon={<Zap size={11} />}
                label="Style"
                value={data.attributes.style}
              />
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <Tag size={11} className="text-slate-500" />
                  <span className="font-mono text-[10px] text-slate-600 tracking-widest">
                    KEYWORDS
                  </span>
                </div>
                <div className="flex flex-wrap gap-1">
                  {data.attributes.keywords.map((kw) => (
                    <span
                      key={kw}
                      className="px-2 py-0.5 bg-cyber/5 border border-cyber/20 text-cyber font-mono text-[10px] tracking-wide"
                    >
                      {kw}
                    </span>
                  ))}
                </div>
              </div>
              <AttributeRow
                icon={<ChevronRight size={11} />}
                label="Refined Query"
                value={`"${data.attributes.refined_query}"`}
                mono
              />
            </div>
          </div>
        )}

        {/* Active model info */}
        {activeModel && (
          <div className="p-6 border-b border-white/5">
            <div className="flex items-center gap-2 mb-4">
              <Globe size={12} className="text-cyber" />
              <span className="font-mono text-[10px] text-slate-600 tracking-widest">
                ACTIVE MODEL
              </span>
            </div>
            <p className="text-white text-sm mb-2 line-clamp-2">
              {activeModel.title}
            </p>
            <p className="font-mono text-[10px] text-slate-600 mb-3">
              {activeModel.source_domain}
            </p>

            <div className="flex flex-col gap-2 mt-4">
              <a
                href={activeModel.source_url}
                target="_blank"
                rel="noopener noreferrer"
                data-testid="model-source-link"
                className="flex items-center justify-center gap-2 px-4 py-2 border border-cyber/30 text-cyber hover:bg-cyber/10 font-mono text-[11px] tracking-widest transition-colors duration-200"
              >
                VIEW SOURCE <ExternalLink size={12} />
              </a>

              {activeModel.url && (
                <a
                  href={`${API}/download?url=${encodeURIComponent(activeModel.url)}`}
                  download
                  data-testid="model-download-link"
                  className="flex items-center justify-center gap-2 px-4 py-2 bg-cyber text-black hover:bg-cyber/80 font-mono text-[11px] tracking-widest transition-colors duration-200 font-semibold"
                >
                  DOWNLOAD 3D FILE <Download size={12} />
                </a>
              )}
            </div>
          </div>
        )}

        {/* All models */}
        {data?.all_models && data.all_models.length > 1 && (
          <div className="p-6">
            <div className="flex items-center gap-2 mb-4">
              <Layers size={12} className="text-slate-500" />
              <span className="font-mono text-[10px] text-slate-600 tracking-widest">
                ALL CANDIDATES ({data.all_models.length})
              </span>
            </div>
            <div className="space-y-2">
              {data.all_models.map((m, i) => {
                const isActive =
                  (m.embed_url || m.url) ===
                  (activeModel?.embed_url || activeModel?.url);
                return (
                  <button
                    key={i}
                    onClick={() => setActiveModel(m)}
                    data-testid={`model-candidate-${i}`}
                    className={`w-full text-left p-3 border transition-colors duration-200 ${isActive
                      ? "border-cyber/50 bg-cyber/5"
                      : "border-white/5 bg-white/2 hover:border-white/15 hover:bg-white/5"
                      }`}
                  >
                    <div className="flex items-center justify-between gap-2 mb-1">
                      <span
                        className="font-mono text-[9px] tracking-widest"
                        style={{
                          color: typeColors[m.type] || "#ffffff",
                        }}
                      >
                        {m.type?.toUpperCase()}
                      </span>
                      <span className="font-mono text-[9px] text-slate-600">
                        {m.source_domain}
                      </span>
                    </div>
                    <p className="text-xs text-slate-300 line-clamp-1">
                      {m.title}
                    </p>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* No model state */}
        {data?.status === "no_model" && (
          <div className="p-6">
            <div className="p-4 border border-neon-red/20 bg-neon-red/5">
              <p className="font-mono text-xs text-neon-red/80 tracking-wide">
                No 3D models found for this prompt.
              </p>
              <p className="font-mono text-[10px] text-slate-600 mt-1">
                Try a more specific description.
              </p>
            </div>
            <button
              onClick={() => router.push("/")}
              data-testid="try-again-btn"
              className="mt-4 w-full py-2 border border-cyber/30 text-cyber font-mono text-xs tracking-widest hover:bg-cyber/10 transition-colors duration-200"
            >
              TRY AGAIN
            </button>
          </div>
        )}
      </div>

      {/* Chat Panel */}
      {showChat && (
        <div className="w-full lg:w-80 xl:w-96 flex flex-col border-t lg:border-t-0 lg:border-l border-white/5 bg-black/40 backdrop-blur-sm p-4">
          <ChatPanel
            searchId={id}
            modelTitle={activeModel?.title ?? data?.original_prompt ?? "3D Model"}
            modelUrl={activeModel?.url ?? activeModel?.embed_url}
            onLabelSelect={setHighlightedLabel}
          />
        </div>
      )}
    </div>
  );
}

function AttributeRow({
  icon,
  label,
  value,
  mono,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-start gap-2">
      <span className="text-slate-500 mt-0.5 shrink-0">{icon}</span>
      <div>
        <p className="font-mono text-[9px] text-slate-600 tracking-widest mb-0.5">
          {label.toUpperCase()}
        </p>
        <p
          className={`text-sm text-slate-300 ${mono ? "font-mono text-[11px] text-cyber/80" : ""}`}
        >
          {value}
        </p>
      </div>
    </div>
  );
}

function ViewerSkeleton() {
  return (
    <div className="flex flex-col lg:flex-row h-[calc(100vh-88px)]">
      <div className="flex-1 bg-obsidian flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="relative w-16 h-16">
            <div className="absolute inset-0 border border-cyber/30 spin-slow" />
            <div
              className="absolute inset-2 border border-cyber/50 spin-slow"
              style={{
                animationDirection: "reverse",
                animationDuration: "2s",
              }}
            />
          </div>
          <p className="font-mono text-xs text-cyber/60 tracking-widest animate-pulse">
            LOADING_MODEL_DATA
          </p>
        </div>
      </div>
      <div className="w-full lg:w-80 xl:w-96 bg-black/40 p-6 space-y-4">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-16 bg-white/5 animate-pulse" />
        ))}
      </div>
    </div>
  );
}
