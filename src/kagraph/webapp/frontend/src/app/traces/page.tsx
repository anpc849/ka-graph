"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { format } from "date-fns";
import { Search, Filter, Clock, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { fetchJson } from "@/lib/api";

interface Trace {
  id: string;
  name: string;
  status: string;
  start_time: string;
  end_time: string | null;
  session_id: string | null;
  duration_ms: number | null;
  error?: string | null;
}

export default function TracesListPage() {
  const router = useRouter();
  const [traces, setTraces] = useState<Trace[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams();
    if (search.trim()) params.set("search", search.trim());
    if (status) params.set("status", status);
    setLoading(true);
    setError(null);
    fetchJson<Trace[]>(`/api/traces?${params.toString()}`)
      .then((data) => {
        setTraces(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message || String(err));
        setLoading(false);
      });
  }, [search, status]);

  async function deleteTrace(trace: Trace) {
    const ok = window.confirm(`Delete trace "${trace.name}" (${trace.id}) from the database?`);
    if (!ok) return;
    setDeletingId(trace.id);
    try {
      const response = await fetch(`/api/traces/${trace.id}`, { method: "DELETE" });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || "Failed to delete trace.");
      }
      setTraces((items) => items.filter((item) => item.id !== trace.id));
    } catch (error: any) {
      window.alert(error.message || String(error));
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="flex-1 flex flex-col h-full bg-background overflow-hidden">
      {/* Header */}
      <div className="flex flex-col border-b border-[#1E293B] bg-[#0F172A] px-8 py-6">
        <h1 className="text-3xl font-bold tracking-tight text-white mb-6">Traces</h1>

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4 w-full max-w-xl">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-500" />
              <input
                type="text"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search traces..."
                className="w-full bg-[#020617] text-white border border-[#1E293B] rounded-lg py-2.5 pl-9 pr-4 text-sm focus:outline-none focus:ring-2 focus:ring-[#22C55E]/50 transition-all placeholder:text-slate-600"
              />
            </div>
            <div className="relative flex items-center gap-2 px-3 py-2.5 border border-[#1E293B] rounded-lg text-sm font-medium text-slate-400 bg-[#020617] focus-within:ring-2 focus-within:ring-[#22C55E]/50 transition-all">
              <Filter className="h-4 w-4 text-[#22C55E]" />
              <select value={status} onChange={(event) => setStatus(event.target.value)} className="bg-transparent outline-none text-white cursor-pointer">
                <option value="">All status</option>
                <option value="RUNNING">Running</option>
                <option value="SUCCESS">Success</option>
                <option value="ERROR">Error</option>
              </select>
            </div>
          </div>
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto p-8 bg-[#020617]">
        <div className="rounded-xl border border-[#1E293B] bg-[#0F172A] overflow-hidden shadow-sm">
          <table className="w-full text-sm text-left">
            <thead className="bg-[#0F172A] border-b border-[#1E293B] text-xs uppercase font-semibold text-slate-500 tracking-wider">
              <tr>
                <th className="px-6 py-4 font-medium">Timestamp</th>
                <th className="px-6 py-4 font-medium">Name</th>
                <th className="px-6 py-4 font-medium">ID</th>
                <th className="px-6 py-4 font-medium">Status</th>
                <th className="px-6 py-4 font-medium">Duration</th>
                <th className="px-6 py-4 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1E293B]">
              {error ? (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center text-red-300 font-medium">
                    {error}
                    <div className="mt-2 text-xs text-slate-500">Open Studio Logs from the sidebar to inspect the backend log in real time.</div>
                  </td>
                </tr>
              ) : loading ? (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center text-slate-500 font-medium">Loading traces...</td>
                </tr>
              ) : traces.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center text-slate-500 font-medium">No traces found.</td>
                </tr>
              ) : (
                traces.map(trace => (
                  <tr
                    key={trace.id}
                    className="hover:bg-[#1E293B]/50 transition-colors group cursor-pointer"
                    onClick={() => router.push(`/traces/${trace.id}`)}
                  >
                    <td className="px-6 py-4 whitespace-nowrap text-slate-400 font-mono text-xs">
                      {format(new Date(trace.start_time), "MMM d, yyyy HH:mm:ss")}
                    </td>
                    <td className="px-6 py-4 font-semibold text-white">
                      {trace.name}
                    </td>
                    <td className="px-6 py-4 font-mono text-xs text-slate-500">
                      {trace.id.substring(0, 8)}...
                    </td>
                    <td className="px-6 py-4">
                      <span className={cn(
                        "px-2.5 py-1 rounded-md text-xs font-bold uppercase tracking-wider",
                        trace.status === "SUCCESS" ? "bg-[#22C55E]/10 text-[#22C55E] border border-[#22C55E]/20" : trace.status === "RUNNING" ? "bg-yellow-500/10 text-yellow-500 border border-yellow-500/20" : "bg-red-500/10 text-red-500 border border-red-500/20"
                      )}>
                        {trace.status}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-slate-400 font-mono text-xs">
                      <span className="inline-flex items-center gap-1.5">
                        <Clock className="h-3.5 w-3.5 text-slate-500" />
                        {trace.duration_ms == null ? "-" : `${trace.duration_ms}ms`}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-right">
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          deleteTrace(trace);
                        }}
                        disabled={deletingId === trace.id}
                        className="inline-flex items-center justify-center p-2 rounded-lg text-slate-500 hover:bg-red-500/20 hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100 disabled:opacity-40"
                        title="Delete trace"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
