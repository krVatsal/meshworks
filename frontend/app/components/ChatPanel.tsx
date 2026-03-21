"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import axios from "axios";
import {
  Send,
  Bot,
  User,
  Loader2,
  Lightbulb,
  ChevronDown,
  Play,
  Pause,
  RotateCcw,
  StopCircle,
  Crosshair,
  Mic,
} from "lucide-react";
import DOMPurify from "dompurify";

interface Message {
  role: "user" | "assistant";
  content: string;
  explanation?: string;
  matchedLabel?: string | null;
}

interface ChatPanelProps {
  searchId: string;
  modelTitle: string;
  onLabelSelect?: (label: string | null) => void;
}

type NarrationState = "idle" | "playing" | "paused";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

/* ─────────────────────────────────────────────────────────────────────
   WAVEFORM  – animated bars shown while speaking
───────────────────────────────────────────────────────────────────── */
function Waveform({ active }: { active: boolean }) {
  const heights = [3, 6, 10, 7, 4, 9, 5, 8, 6, 3];
  return (
    <div className="flex items-center gap-[2px]">
      {heights.map((h, i) => (
        <div
          key={i}
          className={`w-[2px] rounded-full transition-all duration-300 ${
            active ? "bg-teal-400" : "bg-slate-600"
          }`}
          style={{
            height: active ? `${h}px` : "3px",
            animation: active ? "wave 1s ease-in-out infinite" : "none",
            animationDelay: `${i * 75}ms`,
          }}
        />
      ))}
      <style>{`
        @keyframes wave {
          0%, 100% { transform: scaleY(0.4); }
          50%       { transform: scaleY(1.3); }
        }
      `}</style>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────
   ICON CONTROL BUTTON
───────────────────────────────────────────────────────────────────── */
function CtrlBtn({
  icon,
  label,
  onClick,
  accent,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  accent: "teal" | "amber" | "blue" | "red";
}) {
  const map = {
    teal:  "hover:bg-teal-500/20  hover:text-teal-300",
    amber: "hover:bg-amber-500/20 hover:text-amber-300",
    blue:  "hover:bg-sky-500/20   hover:text-sky-300",
    red:   "hover:bg-red-500/20   hover:text-red-400",
  };
  return (
    <button
      title={label}
      onClick={onClick}
      className={`w-6 h-6 rounded-lg flex items-center justify-center text-slate-500 transition-all duration-150 ${map[accent]}`}
    >
      {icon}
    </button>
  );
}

/* ─────────────────────────────────────────────────────────────────────
   NARRATION BAR
───────────────────────────────────────────────────────────────────── */
function NarrationBar({
  text,
  idx,
  active,
  state,
  onPlay,
  onPause,
  onResume,
  onReplay,
  onStop,
}: {
  text: string;
  idx: number;
  active: boolean;
  state: NarrationState;
  onPlay: (t: string, i: number) => void;
  onPause: () => void;
  onResume: () => void;
  onReplay: (t: string, i: number) => void;
  onStop: () => void;
}) {
  const isPlaying = active && state === "playing";
  const isPaused  = active && state === "paused";

  return (
    <div
      className={`mt-2 flex items-center gap-2.5 px-3 py-2 rounded-xl border transition-all duration-300 ${
        active
          ? "bg-teal-950/50 border-teal-500/35 shadow-[0_0_16px_rgba(20,184,166,0.1)]"
          : "bg-slate-800/40 border-slate-700/40 hover:border-slate-600/60"
      }`}
    >
      {/* Mic icon */}
      <div
        className={`flex-shrink-0 w-6 h-6 rounded-lg flex items-center justify-center transition-colors ${
          active ? "bg-teal-500/20 text-teal-400" : "bg-slate-700/60 text-slate-500"
        }`}
      >
        <Mic size={11} />
      </div>

      {/* Status + waveform */}
      <div className="flex items-center gap-2 flex-1 min-w-0">
        <span
          className={`text-[9px] font-mono uppercase tracking-[0.15em] flex-shrink-0 transition-colors ${
            isPlaying ? "text-teal-400" : isPaused ? "text-amber-400" : "text-slate-600"
          }`}
        >
          {isPlaying ? "Playing" : isPaused ? "Paused" : "Narrate"}
        </span>
        <Waveform active={isPlaying} />
      </div>

      {/* Control buttons */}
      <div className="flex items-center gap-0.5 flex-shrink-0">
        {!active && (
          <CtrlBtn icon={<Play size={10} />} label="Play" onClick={() => onPlay(text, idx)} accent="teal" />
        )}
        {isPlaying && (
          <CtrlBtn icon={<Pause size={10} />} label="Pause" onClick={onPause} accent="amber" />
        )}
        {isPaused && (
          <CtrlBtn icon={<Play size={10} />} label="Resume" onClick={onResume} accent="teal" />
        )}
        {active && (
          <>
            <CtrlBtn icon={<RotateCcw size={10} />} label="Replay from start" onClick={() => onReplay(text, idx)} accent="blue" />
            <CtrlBtn icon={<StopCircle size={10} />} label="Stop" onClick={onStop} accent="red" />
          </>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────
   EXPLANATION ACCORDION DROPDOWN
───────────────────────────────────────────────────────────────────── */
function ExplanationDropdown({ text }: { text: string }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-2 w-full">
      <button
        onClick={() => setOpen((v) => !v)}
        className={`w-full flex items-center gap-2 px-3 py-2 rounded-xl border text-[10px] font-medium tracking-wide transition-all duration-200 group ${
          open
            ? "bg-indigo-950/60 border-indigo-500/40 text-indigo-300"
            : "bg-slate-800/50 border-slate-700/40 text-slate-500 hover:border-indigo-500/30 hover:text-indigo-400"
        }`}
      >
        <Lightbulb
          size={11}
          className={`flex-shrink-0 transition-colors ${
            open ? "text-indigo-400" : "text-slate-600 group-hover:text-indigo-400"
          }`}
        />
        <span className="uppercase tracking-[0.15em] text-[9px]">Detailed Explanation</span>
        <ChevronDown
          size={11}
          className={`ml-auto flex-shrink-0 transition-transform duration-300 ${open ? "rotate-180" : ""}`}
        />
      </button>

      <div
        className="overflow-hidden transition-all duration-300 ease-in-out"
        style={{ maxHeight: open ? "600px" : "0", opacity: open ? 1 : 0 }}
      >
        <div className="mt-1.5 rounded-xl border border-indigo-500/20 bg-gradient-to-br from-indigo-950/50 to-slate-900/70 overflow-hidden">
          <div className="h-px w-full bg-gradient-to-r from-indigo-500/50 via-violet-500/30 to-transparent" />
          <div className="px-4 py-3 flex gap-3">
            <div className="w-px flex-shrink-0 bg-gradient-to-b from-indigo-500/50 to-transparent rounded-full" />
            <p className="text-[11px] text-indigo-100/75 leading-[1.7]">{text}</p>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────
   MESSAGE BUBBLE
───────────────────────────────────────────────────────────────────── */
function MessageBubble({
  msg,
  idx,
  narration,
  onLabelSelect,
  onPlay,
  onPause,
  onResume,
  onReplay,
  onStop,
}: {
  msg: Message;
  idx: number;
  narration: { index: number; state: NarrationState } | null;
  onLabelSelect?: (l: string | null) => void;
  onPlay: (t: string, i: number) => void;
  onPause: () => void;
  onResume: () => void;
  onReplay: (t: string, i: number) => void;
  onStop: () => void;
}) {
  const isUser = msg.role === "user";
  const isActiveNarration = narration?.index === idx;
  const hasExplanation = !!msg.explanation && msg.explanation !== msg.content;

  return (
    <div className={`flex gap-3 ${isUser ? "flex-row-reverse" : "flex-row"}`}>
      {/* Avatar */}
      <div
        className={`flex-shrink-0 w-7 h-7 rounded-xl flex items-center justify-center mt-0.5 ring-1 ${
          isUser
            ? "bg-cyan-500/10 text-cyan-400 ring-cyan-500/25"
            : "bg-violet-500/10 text-violet-400 ring-violet-500/25"
        }`}
      >
        {isUser ? <User size={13} /> : <Bot size={13} />}
      </div>

      {/* Content column */}
      <div className={`flex flex-col max-w-[79%] ${isUser ? "items-end" : "items-start"}`}>
        {/* Main speech bubble */}
        <div
          className={`px-4 py-2.5 text-sm leading-relaxed ${
            isUser
              ? "bg-gradient-to-br from-cyan-500/18 to-cyan-700/10 text-cyan-50 rounded-2xl rounded-tr-sm border border-cyan-500/20"
              : "bg-gradient-to-br from-slate-700/60 to-slate-800/40 text-slate-100 rounded-2xl rounded-tl-sm border border-slate-600/25"
          }`}
          dangerouslySetInnerHTML={{
            __html: DOMPurify.sanitize(
              msg.content
                .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
                .replace(/\*(.*?)\*/g, "<em>$1</em>")
            ),
          }}
        />

        {/* Assistant extras block */}
        {!isUser && (
          <div className="w-full mt-1 space-y-0">
            {/* 3D highlight pill */}
            {msg.matchedLabel && (
              <div className="mt-1.5">
                <button
                  onClick={() => onLabelSelect?.(msg.matchedLabel ?? null)}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-cyan-950/50 border border-cyan-500/30 text-cyan-300 text-[9px] font-mono tracking-widest hover:bg-cyan-500/15 hover:border-cyan-400/50 transition-all"
                >
                  <Crosshair size={9} />
                  {msg.matchedLabel}
                </button>
              </div>
            )}

            {/* Explanation dropdown */}
            {hasExplanation && <ExplanationDropdown text={msg.explanation!} />}

            {/* Narration bar */}
            {msg.explanation && (
              <NarrationBar
                text={msg.explanation}
                idx={idx}
                active={isActiveNarration}
                state={isActiveNarration ? narration!.state : "idle"}
                onPlay={onPlay}
                onPause={onPause}
                onResume={onResume}
                onReplay={onReplay}
                onStop={onStop}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────
   MAIN EXPORT
───────────────────────────────────────────────────────────────────── */
export default function ChatPanel({ searchId, modelTitle, onLabelSelect }: ChatPanelProps) {
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "assistant",
      content: `Hi! I can answer questions about **${modelTitle}** — its parts, structure, function, or anything else you'd like to know. Ask me about specific segments or anatomical features!`,
    },
  ]);
  const [input, setInput]               = useState("");
  const [loading, setLoading]           = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [narration, setNarration]       = useState<{ index: number; state: NarrationState } | null>(null);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef   = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  /* ── textarea auto-height ── */
  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const el = e.target;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  };

  /* ── send message ── */
  const sendMessage = async () => {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    setMessages((p) => [...p, { role: "user", content: text }]);
    setLoading(true);
    try {
      const res = await axios.post(`${API_BASE}/api/chat`, {
        search_id: searchId,
        message: text,
        conversation_id: conversationId,
      });
      const matchedLabel: string | null = res.data.matched_label ?? null;
      const explanation: string = res.data.explanation ?? res.data.response;
      setConversationId(res.data.conversation_id);
      setMessages((p) => [
        ...p,
        { role: "assistant", content: res.data.response, explanation, matchedLabel },
      ]);
      if (matchedLabel) onLabelSelect?.(matchedLabel);
    } catch {
      setMessages((p) => [
        ...p,
        { role: "assistant", content: "Sorry, I couldn't get a response. Please try again." },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  };

  /* ── TTS controls ── */
  const stopSpeech = useCallback(() => {
    window.speechSynthesis?.cancel();
    setNarration(null);
  }, []);

  const playSpeech = useCallback((text: string, index: number) => {
    if (!window.speechSynthesis) return alert("TTS is not supported in this browser.");
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 0.93; u.pitch = 1; u.volume = 1;
    u.onstart = () => setNarration({ index, state: "playing" });
    u.onend   = () => setNarration(null);
    u.onerror = () => setNarration(null);
    window.speechSynthesis.speak(u);
    setNarration({ index, state: "playing" });
  }, []);

  const pauseSpeech  = useCallback(() => {
    window.speechSynthesis?.pause();
    setNarration((p) => p ? { ...p, state: "paused" } : null);
  }, []);

  const resumeSpeech = useCallback(() => {
    window.speechSynthesis?.resume();
    setNarration((p) => p ? { ...p, state: "playing" } : null);
  }, []);

  const replaySpeech = useCallback((text: string, index: number) => {
    playSpeech(text, index);
  }, [playSpeech]);

  return (
    <div className="flex flex-col h-full bg-[#0c1118] rounded-2xl border border-slate-700/40 overflow-hidden shadow-2xl shadow-black/50">

      {/* ── Header ── */}
      <div className="flex-shrink-0 flex items-center gap-3 px-4 py-3.5 border-b border-slate-700/40 bg-slate-900/70 backdrop-blur-md">
        <div className="relative flex-shrink-0">
          <div className="w-8 h-8 rounded-xl bg-violet-500/10 ring-1 ring-violet-500/25 flex items-center justify-center">
            <Bot size={15} className="text-violet-400" />
          </div>
          <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-teal-400 shadow-[0_0_8px_rgba(45,212,191,0.9)]" />
        </div>

        <div className="flex flex-col min-w-0">
          <span className="text-sm font-semibold text-slate-100 leading-none tracking-tight">
            Model Assistant
          </span>
          <span className="text-[10px] text-slate-500 mt-0.5 truncate tracking-wide">
            {modelTitle}
          </span>
        </div>

        {narration && (
          <div className="ml-auto flex items-center gap-2 px-2.5 py-1 rounded-full bg-teal-950/60 border border-teal-500/30">
            <span className="w-1.5 h-1.5 rounded-full bg-teal-400 animate-pulse shadow-[0_0_4px_rgba(45,212,191,0.8)]" />
            <span className="text-[9px] font-mono text-teal-300 tracking-[0.15em] uppercase">
              {narration.state === "paused" ? "Paused" : "Narrating"}
            </span>
          </div>
        )}
      </div>

      {/* ── Messages ── */}
      <div className="flex-1 overflow-y-auto px-4 py-5 space-y-5 min-h-0">
        {messages.map((msg, i) => (
          <MessageBubble
            key={i}
            msg={msg}
            idx={i}
            narration={narration}
            onLabelSelect={onLabelSelect}
            onPlay={playSpeech}
            onPause={pauseSpeech}
            onResume={resumeSpeech}
            onReplay={replaySpeech}
            onStop={stopSpeech}
          />
        ))}

        {/* Typing indicator */}
        {loading && (
          <div className="flex gap-3">
            <div className="flex-shrink-0 w-7 h-7 rounded-xl bg-violet-500/10 ring-1 ring-violet-500/25 flex items-center justify-center">
              <Bot size={13} className="text-violet-400" />
            </div>
            <div className="px-4 py-2.5 rounded-2xl rounded-tl-sm bg-slate-800/60 border border-slate-700/40 flex items-center gap-1.5">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="w-1.5 h-1.5 rounded-full bg-slate-500 animate-bounce"
                  style={{ animationDelay: `${i * 160}ms` }}
                />
              ))}
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* ── Input ── */}
      <div className="flex-shrink-0 px-4 pb-4 pt-3 border-t border-slate-700/40 bg-slate-900/50">
        <div
          className="flex items-end gap-2 bg-slate-800/60 rounded-xl border border-slate-700/40
                     focus-within:border-cyan-500/50 focus-within:shadow-[0_0_0_3px_rgba(6,182,212,0.07)]
                     transition-all duration-200 px-3.5 py-2.5"
        >
          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            placeholder="Ask about this model…"
            className="flex-1 bg-transparent text-sm text-slate-200 placeholder-slate-600 resize-none outline-none leading-relaxed"
            style={{ maxHeight: "120px", overflowY: "auto" }}
          />
          <button
            onClick={sendMessage}
            disabled={!input.trim() || loading}
            className={`flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center transition-all duration-150 ${
              input.trim() && !loading
                ? "bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30 hover:scale-105"
                : "text-slate-700 cursor-not-allowed"
            }`}
          >
            {loading ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
          </button>
        </div>
        <p className="text-[10px] text-slate-700 mt-2 text-center tracking-[0.15em] uppercase">
          ↵ Send &nbsp;·&nbsp; ⇧↵ New line
        </p>
      </div>
    </div>
  );
}