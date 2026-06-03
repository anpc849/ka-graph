"use client";

import { useEffect, useState } from "react";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar } from 'recharts';
import { cn } from "@/lib/utils";
import { Activity } from "lucide-react";
import { fetchJson } from "@/lib/api";

interface DashboardData {
  traces: {
    total: number;
    replayable: number;
    by_name: { name: string; count: number }[];
  };
  events: {
    total: number;
  };
  costs: {
    total_usd: number;
    models: { model: string; tokens: number; usd: number }[];
  };
  traces_by_time: { date: string; count: number }[];
}

export default function Home() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchJson<{ status: string }>("/api/health", 5000)
      .then(() => fetchJson<DashboardData>("/api/dashboard"))
      .then((payload) => {
        setData(payload);
        setError(null);
      })
      .catch((err) => {
        setError(err.message || String(err));
      });
  }, []);

  if (error) {
    return (
      <div className="p-8 text-slate-300 flex flex-col gap-3 justify-center h-full max-w-2xl">
        <h1 className="text-2xl font-bold text-white">Dashboard API is not responding</h1>
        <p className="text-sm text-slate-400">{error}</p>
        <p className="text-sm text-slate-500">Open Studio Logs from the sidebar to inspect the backend log in real time.</p>
      </div>
    );
  }

  if (!data) return <div className="p-8 text-muted-foreground flex items-center justify-center h-full">Loading dashboard...</div>;

  const validModels = data.costs.models.filter(m => m.model !== 'assistant' && (m.tokens > 0 || m.usd > 0));

  return (
    <div className="flex-1 overflow-y-auto p-6 md:p-8 bg-background">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-white mb-1">Dashboard</h1>
          <p className="text-sm text-slate-400">Overview of your graph orchestrations and traces.</p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
        {/* Traces Card */}
        <div className="group relative rounded-2xl border border-[#1E293B] bg-[#0F172A] p-6 shadow-sm transition-all duration-300 hover:shadow-lg hover:-translate-y-1 hover:border-[#334155]">
          <div className="absolute top-0 right-0 p-4 opacity-10 transition-opacity group-hover:opacity-20">
            <Activity className="h-24 w-24 text-white" />
          </div>
          <h3 className="text-sm font-semibold tracking-wide text-slate-400 mb-6 uppercase">Traces</h3>
          <div className="flex items-baseline gap-2 mb-6">
            <span className="text-5xl font-bold tracking-tighter text-white">{data.traces.total >= 1000 ? (data.traces.total / 1000).toFixed(2) + 'K' : data.traces.total}</span>
            <span className="text-xs text-[#22C55E] font-medium">+Total traces</span>
          </div>
          <div className="flex flex-col gap-2 w-full relative z-10">
            {data.traces.by_name.map((item, idx) => (
              <div key={idx} className={cn("flex justify-between items-center text-sm py-1.5 px-3 rounded-lg transition-colors", idx === 0 ? "bg-[#22C55E]/10 text-[#22C55E] border border-[#22C55E]/20" : "text-slate-300 hover:bg-[#1E293B]")}>
                <span className="font-medium">{item.name}</span>
                <span className="font-mono text-xs">{item.count}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Costs Card */}
        <div className="group relative rounded-2xl border border-[#1E293B] bg-[#0F172A] p-6 shadow-sm transition-all duration-300 hover:shadow-lg hover:-translate-y-1 hover:border-[#334155]">
          <h3 className="text-sm font-semibold tracking-wide text-slate-400 mb-6 uppercase">Model costs</h3>
          <div className="flex items-baseline gap-2 mb-6">
            <span className="text-5xl font-bold tracking-tighter text-white">${data.costs.total_usd.toFixed(4)}</span>
            <span className="text-xs text-slate-500 font-medium">USD</span>
          </div>
          <div className="w-full text-sm relative z-10">
            <div className="flex justify-between font-semibold text-slate-500 pb-2 border-b border-[#1E293B] mb-3 uppercase text-xs tracking-wider">
              <span className="flex-[2]">Model</span>
              <span className="flex-1 text-right">Tokens</span>
              <span className="flex-1 text-right">Cost</span>
            </div>
            <div className="flex flex-col gap-2">
              {validModels.map((m, idx) => (
                <div key={idx} className="flex justify-between items-center py-1.5 text-slate-300 hover:text-white transition-colors">
                  <span className="flex-[2] truncate pr-2 font-medium">{m.model}</span>
                  <span className="flex-1 text-right font-mono text-xs text-slate-400">{m.tokens >= 1000000 ? (m.tokens/1000000).toFixed(2)+'M' : m.tokens >= 1000 ? (m.tokens/1000).toFixed(2)+'K' : m.tokens}</span>
                  <span className="flex-1 text-right font-mono text-xs text-[#22C55E]">${m.usd.toFixed(4)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Studio Coverage Card */}
        <div className="group relative rounded-2xl border border-[#1E293B] bg-[#0F172A] p-6 shadow-sm transition-all duration-300 hover:shadow-lg hover:-translate-y-1 hover:border-[#334155]">
          <h3 className="text-sm font-semibold tracking-wide text-slate-400 mb-6 uppercase">Storage Events</h3>
          <div className="flex items-baseline gap-2 mb-6">
            <span className="text-5xl font-bold tracking-tighter text-white">{data.events.total >= 1000 ? (data.events.total / 1000).toFixed(2) + 'K' : data.events.total}</span>
            <span className="text-xs text-slate-500 font-medium">Events</span>
          </div>
          <div className="w-full text-sm relative z-10">
            <div className="flex justify-between font-semibold text-slate-500 pb-2 border-b border-[#1E293B] mb-3 uppercase text-xs tracking-wider">
              <span className="flex-[2]">Signal</span>
              <span className="flex-1 text-right">Count</span>
            </div>
            <div className="flex flex-col gap-2">
              <div className="flex justify-between items-center py-1.5 text-slate-300">
                <span className="flex-[2] font-medium">Replayable traces</span>
                <span className="flex-1 text-right font-mono text-xs bg-[#1E293B] px-2 py-1 rounded-md">{data.traces.replayable}</span>
              </div>
              <div className="flex justify-between items-center py-1.5 text-slate-300">
                <span className="flex-[2] font-medium">Stored events</span>
                <span className="flex-1 text-right font-mono text-xs bg-[#1E293B] px-2 py-1 rounded-md">{data.events.total}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Traces by time */}
        <div className="rounded-2xl border border-[#1E293B] bg-[#0F172A] p-6 shadow-sm flex flex-col min-h-[400px]">
          <h3 className="text-sm font-semibold tracking-wide text-slate-400 mb-6 uppercase">Traces over time</h3>
          <div className="w-full h-[280px]">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data.traces_by_time} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="colorTraces" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#22C55E" stopOpacity={0.4}/>
                    <stop offset="95%" stopColor="#22C55E" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#1E293B" />
                <XAxis dataKey="date" stroke="#64748b" fontSize={11} tickLine={false} axisLine={false} fontFamily="var(--font-fira-code)" />
                <YAxis stroke="#64748b" fontSize={11} tickLine={false} axisLine={false} fontFamily="var(--font-fira-code)" />
                <Tooltip
                  contentStyle={{ background: "#0F172A", border: "1px solid #1E293B", borderRadius: "12px", color: "#F8FAFC", boxShadow: "0 10px 15px -3px rgb(0 0 0 / 0.5)" }}
                  itemStyle={{ color: "#22C55E" }}
                />
                <Area type="monotone" dataKey="count" name="Traces" stroke="#22C55E" strokeWidth={3} fillOpacity={1} fill="url(#colorTraces)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Model Usage */}
        <div className="rounded-2xl border border-[#1E293B] bg-[#0F172A] p-6 shadow-sm flex flex-col min-h-[400px]">
          <h3 className="text-sm font-semibold tracking-wide text-slate-400 mb-6 uppercase">Usage breakdown</h3>
          <div className="w-full h-[280px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={validModels} margin={{ top: 10, right: 10, left: 10, bottom: 60 }} barGap={8} barCategoryGap="20%">
                <defs>
                  <linearGradient id="colorCost" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3B82F6" stopOpacity={0.9}/>
                    <stop offset="95%" stopColor="#3B82F6" stopOpacity={0.4}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#1E293B" />
                <XAxis dataKey="model" stroke="#64748b" fontSize={11} tickLine={false} axisLine={false} angle={-25} textAnchor="end" />
                <YAxis stroke="#3B82F6" fontSize={11} tickLine={false} axisLine={false} tickFormatter={(val) => `$${val}`} />
                <Tooltip
                  contentStyle={{ background: "rgba(15, 23, 42, 0.9)", border: "1px solid #334155", borderRadius: "12px", color: "#F8FAFC", backdropFilter: "blur(4px)" }}
                  cursor={{ fill: '#1E293B', opacity: 0.4 }}
                  formatter={(value, name) => {
                    const numericValue = Number(value ?? 0);
                    const label = String(name);
                    return [label === 'usd' ? `$${numericValue.toFixed(4)}` : numericValue, label === 'usd' ? 'Cost' : label];
                  }}
                />
                <Bar dataKey="usd" name="Cost" fill="url(#colorCost)" radius={[6, 6, 0, 0]} maxBarSize={40} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </div>
  );
}
