"use client";

import { useState, useRef, useEffect } from "react";
import axios from "axios";
import { Send, Bot, User, Loader2, Volume2, Lightbulb } from "lucide-react";
import DOMPurify from "dompurify";

interface Message {
  role: "user" | "assistant";
  content: string;
  explanation?: string;  // Detailed explanation for TTS
  matchedLabel?: string | null;
}

interface ChatPanelProps {
  searchId: string;
  modelTitle: string;
  onLabelSelect?: (label: string | null) => void;
}

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

export default function ChatPanel({
  searchId,
  modelTitle,
  onLabelSelect,
}: ChatPanelProps) {
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "assistant",
      content: `Hi! I can answer questions about **${modelTitle}** — its parts, structure, function, or anything else you'd like to know. Ask me about specific segments or anatomical features!`,
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [speakingId, setSpeakingId] = useState<number | null>(null);  // Track which message is being spoken
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = async () => {
    const text = input.trim();
    if (!text || loading) return;

    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
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
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: res.data.response,
          explanation,
          matchedLabel,
        },
      ]);
      if (matchedLabel) {
        onLabelSelect?.(matchedLabel);
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: "Sorry, I couldn't get a response. Please try again.",
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  // Text-to-Speech function for explanations
  const speakExplanation = (text: string, msgIndex: number) => {
    if (!window.speechSynthesis) {
      alert("Text-to-Speech not supported in your browser");
      return;
    }

    // Cancel any ongoing speech
    window.speechSynthesis.cancel();

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 0.95;
    utterance.pitch = 1;
    utterance.volume = 1;

    utterance.onstart = () => setSpeakingId(msgIndex);
    utterance.onend = () => setSpeakingId(null);
    utterance.onerror = () => setSpeakingId(null);

    window.speechSynthesis.speak(utterance);
  };

  return (
    <div className="flex flex-col h-full bg-gray-900 rounded-xl border border-gray-700 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-700 bg-gray-800/60">
        <Bot size={16} className="text-cyan-400" />
        <span className="text-sm font-semibold text-gray-200">Model Assistant</span>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3 min-h-0">
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex gap-2 ${
              msg.role === "user" ? "flex-row-reverse" : "flex-row"
            }`}
          >
            {/* Avatar */}
            <div
              className={`flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center mt-0.5 ${
                msg.role === "user"
                  ? "bg-cyan-500/20 text-cyan-400"
                  : "bg-purple-500/20 text-purple-400"
              }`}
            >
              {msg.role === "user" ? <User size={12} /> : <Bot size={12} />}
            </div>

            {/* Bubble */}
            <div
              className={`max-w-[80%] px-3 py-2 rounded-xl text-sm leading-relaxed whitespace-pre-wrap ${
                msg.role === "user"
                  ? "bg-cyan-600/20 text-cyan-100 rounded-tr-none"
                  : "bg-gray-700/60 text-gray-200 rounded-tl-none"
              }`}
              dangerouslySetInnerHTML={{
                __html: DOMPurify.sanitize(
                  msg.content
                    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
                    .replace(/\*(.*?)\*/g, "<em>$1</em>")
                ),
              }}
            />

            {msg.role === "assistant" && msg.explanation && msg.explanation !== msg.content && (
              <div className="mt-2 max-w-[80%] w-full p-2.5 rounded-lg bg-blue-900/25 border border-blue-700/30 text-blue-100 text-xs">
                <div className="flex items-start gap-2">
                  <Lightbulb size={13} className="text-blue-400 mt-0.5 flex-shrink-0" />
                  <div className="flex-1 leading-relaxed text-[11px]">{msg.explanation}</div>
                </div>
              </div>
            )}

            {msg.role === "assistant" && (
              <div className="flex flex-col gap-1.5">
                {msg.explanation && (
                  <button
                    onClick={() => speakExplanation(msg.explanation || msg.content, messages.indexOf(msg))}
                    className={`px-2 py-1 rounded text-xs flex items-center gap-1.5 whitespace-nowrap transition-all ${
                      speakingId === messages.indexOf(msg)
                        ? "bg-green-600/40 text-green-300 border border-green-500"
                        : "bg-blue-600/20 text-blue-300 border border-blue-500/30 hover:bg-blue-600/30"
                    }`}
                    title="Narrate the explanation"
                  >
                    <Volume2 size={12} />
                    {speakingId === messages.indexOf(msg) ? "Reading..." : "Narrate"}
                  </button>
                )}
                {msg.matchedLabel && (
                  <button
                    onClick={() => onLabelSelect?.(msg.matchedLabel ?? null)}
                    className="px-2 py-1 rounded border border-cyan-500/50 text-cyan-300 text-[10px] font-mono tracking-wide hover:bg-cyan-500/10 transition-colors whitespace-nowrap"
                    title="Highlight this part in 3D"
                  >
                    HIGHLIGHT: {msg.matchedLabel}
                  </button>
                )}
              </div>
            )}
          </div>
        ))}

        {loading && (
          <div className="flex gap-2">
            <div className="flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center bg-purple-500/20 text-purple-400">
              <Bot size={12} />
            </div>
            <div className="px-3 py-2 rounded-xl rounded-tl-none bg-gray-700/60 text-gray-400 text-sm flex items-center gap-1.5">
              <Loader2 size={12} className="animate-spin" />
              Thinking…
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-3 py-3 border-t border-gray-700 bg-gray-800/40">
        <div className="flex items-end gap-2 bg-gray-800 rounded-lg border border-gray-600 focus-within:border-cyan-500 transition-colors px-3 py-2">
          <textarea
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about this model…"
            className="flex-1 bg-transparent text-sm text-gray-200 placeholder-gray-500 resize-none outline-none leading-relaxed"
            style={{ maxHeight: "120px", overflowY: "auto" }}
          />
          <button
            onClick={sendMessage}
            disabled={!input.trim() || loading}
            className="flex-shrink-0 p-1 rounded-md text-cyan-400 hover:text-cyan-300 disabled:text-gray-600 disabled:cursor-not-allowed transition-colors"
          >
            <Send size={15} />
          </button>
        </div>
        <p className="text-xs text-gray-600 mt-1.5 text-center">
          Enter to send · Shift+Enter for new line
        </p>
      </div>
    </div>
  );
}
