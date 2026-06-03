"use client";

import { useEffect, useRef, useState } from "react";
import { RefreshCw, Terminal } from "lucide-react";

interface LogPayload {
  path: string;
  lines: string[];
}

export default function StudioLogsPage() {
  const [backendLog, setBackendLog] = useState("");
  const [logPath, setLogPath] = useState<string | null>(null);
  const [status, setStatus] = useState("Connecting to backend log stream...");
  const logRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    let source: EventSource | null = null;
    let cancelled = false;

    async function loadInitialLog() {
      try {
        const res = await fetch("/api/studio/logs/backend?lines=200");
        if (!res.ok) throw new Error(`Log API returned ${res.status}`);
        const payload = (await res.json()) as LogPayload;
        if (cancelled) return;
        setLogPath(payload.path);
        setBackendLog(payload.lines.join(""));
      } catch (error: any) {
        if (!cancelled) setStatus(error.message || String(error));
      }
    }

    loadInitialLog();
    source = new EventSource("/api/studio/logs/backend/stream?lines=80");
    source.onopen = () => setStatus("Streaming backend.log");
    source.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as { text?: string };
        if (payload.text) {
          setBackendLog((current) => `${current}${payload.text}`.slice(-100000));
        }
      } catch {
        setBackendLog((current) => `${current}${event.data}\n`.slice(-100000));
      }
    };
    source.onerror = () => setStatus("Backend log stream disconnected. Refresh to reconnect.");

    return () => {
      cancelled = true;
      source?.close();
    };
  }, []);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [backendLog]);

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-[#020617]">
      <div className="border-b border-[#1E293B] bg-[#0F172A] px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight text-white mb-1">Studio Logs</h1>
            <p className="text-sm text-slate-400">
              Real-time FastAPI backend output for diagnosing dashboard, trace, and replay requests.
            </p>
          </div>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="inline-flex items-center gap-2 rounded-lg border border-[#1E293B] bg-[#020617] px-3 py-2 text-sm font-semibold text-slate-300 hover:text-white hover:border-[#334155]"
          >
            <RefreshCw className="h-4 w-4" />
            Reconnect
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 p-6">
        <div className="h-full rounded-xl border border-[#1E293B] bg-[#0F172A] overflow-hidden flex flex-col">
          <div className="flex items-center justify-between border-b border-[#1E293B] px-4 py-3 text-xs text-slate-400">
            <div className="inline-flex items-center gap-2">
              <Terminal className="h-4 w-4 text-[#22C55E]" />
              <span className="font-semibold text-slate-200">backend.log</span>
              <span>{status}</span>
            </div>
            {logPath ? <span className="font-mono truncate max-w-[50vw]">{logPath}</span> : null}
          </div>
          <pre
            ref={logRef}
            className="flex-1 min-h-0 overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-xs leading-5 text-slate-200"
          >
            {backendLog || "Waiting for backend log output..."}
          </pre>
        </div>
      </div>
    </div>
  );
}
