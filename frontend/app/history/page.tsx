"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import axios from "axios";
import { Trash2, Box, Layers, Search, Clock } from "lucide-react";

const API = `${process.env.NEXT_PUBLIC_BACKEND_URL}/api`;

interface HistoryModel {
  type: string;
}

interface HistoryItem {
  id: string;
  original_prompt: string;
  created_at: string;
  status: string;
  primary_model?: HistoryModel;
  attributes?: { object_type: string; style: string };
}

export default function HistoryPage() {
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;
    axios
      .get(`${API}/history?limit=50`)
      .then((res) => {
        if (!cancelled) setHistory(res.data);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    try {
      await axios.delete(`${API}/history/${id}`);
      setHistory((prev) => prev.filter((item) => item.id !== id));
    } catch {
      // ignore
    }
  };

  if (loading) {
    return (
      <div className="max-w-screen-2xl mx-auto px-6 lg:px-12 py-12">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <div
              key={i}
              className="h-32 bg-obsidian border border-white/5 animate-pulse"
            />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-screen-2xl mx-auto px-6 lg:px-12 py-12">
      {/* Header */}
      <div className="mb-10 fade-in-up">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-1 h-8 bg-neon-purple" />
          <span className="font-mono text-[10px] text-neon-purple tracking-widest">
            DISCOVERY_ARCHIVE // {history.length} RECORDS
          </span>
        </div>
        <h1 className="font-rajdhani text-4xl sm:text-5xl text-white leading-none">
          SEARCH <span className="text-neon-purple">HISTORY</span>
        </h1>
      </div>

      {history.length === 0 ? (
        <EmptyState />
      ) : (
        <div
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3"
          data-testid="history-grid"
        >
          {history.map((item, i) => (
            <HistoryCard
              key={item.id}
              item={item}
              delay={Math.min(i, 5)}
              onDelete={(e: React.MouseEvent) => handleDelete(e, item.id)}
              onClick={() => router.push(`/view/${item.id}`)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function HistoryCard({
  item,
  delay,
  onDelete,
  onClick,
}: {
  item: HistoryItem;
  delay: number;
  onDelete: (e: React.MouseEvent) => void;
  onClick: () => void;
}) {
  const [hovering, setHovering] = useState(false);
  const modelType = item.primary_model?.type;

  const typeColors: Record<
    string,
    { border: string; text: string; bg: string }
  > = {
    sketchfab: {
      border: "border-neon-purple/30",
      text: "text-neon-purple",
      bg: "bg-neon-purple/5",
    },
    glb: { border: "border-cyber/30", text: "text-cyber", bg: "bg-cyber/5" },
    gltf: {
      border: "border-neon-green/30",
      text: "text-neon-green",
      bg: "bg-neon-green/5",
    },
  };
  const tc = (modelType && typeColors[modelType]) || {
    border: "border-white/10",
    text: "text-slate-500",
    bg: "bg-white/5",
  };

  return (
    <div
      className={`fade-in-up stagger-${delay + 1} group relative cursor-pointer bg-obsidian border border-white/5 hover:border-cyber/20 transition-colors duration-300 overflow-hidden`}
      onClick={onClick}
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      data-testid={`history-item-${item.id}`}
    >
      {/* Top gradient line */}
      <div
        className={`h-px w-full ${hovering ? "bg-cyber/40" : "bg-transparent"} transition-colors duration-300`}
      />

      <div className="p-4">
        {/* Header row */}
        <div className="flex items-start justify-between gap-2 mb-3">
          <div className="flex items-center gap-1.5">
            <Clock size={9} className="text-slate-600" />
            <span className="font-mono text-[9px] text-slate-600">
              {new Date(item.created_at).toLocaleDateString("en-US", {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
          </div>
          <button
            onClick={onDelete}
            data-testid={`delete-history-${item.id}`}
            className="opacity-0 group-hover:opacity-100 p-1 text-slate-700 hover:text-neon-red transition-all duration-200"
          >
            <Trash2 size={11} />
          </button>
        </div>

        {/* Prompt */}
        <p className="text-white text-sm leading-snug mb-3 line-clamp-2 group-hover:text-cyber/90 transition-colors duration-200">
          {item.original_prompt}
        </p>

        {/* Tags */}
        <div className="flex items-center justify-between">
          <div className="flex gap-1 flex-wrap">
            {item.attributes?.object_type && (
              <span className="px-1.5 py-0.5 bg-white/5 text-slate-500 font-mono text-[9px]">
                {item.attributes.object_type}
              </span>
            )}
            {item.attributes?.style && (
              <span className="px-1.5 py-0.5 bg-white/5 text-slate-500 font-mono text-[9px]">
                {item.attributes.style}
              </span>
            )}
          </div>

          {/* Model type */}
          {modelType && (
            <span
              className={`flex items-center gap-1 px-1.5 py-0.5 border font-mono text-[9px] tracking-widest ${tc.border} ${tc.text} ${tc.bg}`}
            >
              {modelType === "sketchfab" ? (
                <Layers size={8} />
              ) : (
                <Box size={8} />
              )}
              {modelType.toUpperCase()}
            </span>
          )}
        </div>

        {/* Status */}
        {item.status === "no_model" && (
          <p className="mt-2 font-mono text-[9px] tracking-widest text-neon-red/60">
            NO MODEL FOUND
          </p>
        )}
      </div>

      {/* Corner accent */}
      <div className="absolute bottom-0 right-0 w-6 h-6 border-b border-r border-cyber/10 group-hover:border-cyber/30 transition-colors duration-300" />
    </div>
  );
}

function EmptyState() {
  const router = useRouter();
  return (
    <div
      className="flex flex-col items-center justify-center py-32 gap-6"
      data-testid="empty-history"
    >
      <div className="w-16 h-16 border border-white/10 flex items-center justify-center">
        <Search size={20} className="text-white/20" />
      </div>
      <div className="text-center">
        <p className="font-rajdhani text-white/40 text-2xl tracking-widest mb-2">
          NO SEARCHES YET
        </p>
        <p className="font-mono text-xs text-slate-600">
          Start discovering 3D models with natural language
        </p>
      </div>
      <button
        onClick={() => router.push("/")}
        className="px-6 py-2 border border-cyber/30 text-cyber font-mono text-xs tracking-widest hover:bg-cyber/10 transition-colors duration-200"
      >
        START SEARCHING
      </button>
    </div>
  );
}
