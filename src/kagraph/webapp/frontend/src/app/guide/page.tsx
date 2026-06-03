"use client";

import Link from "next/link";
import { ArrowRight, Braces, GitBranch, MessageSquare, Terminal } from "lucide-react";

const sections = [
  {
    title: "Timeline",
    icon: Terminal,
    body:
      "The timeline is the main behavior view. In Behavior mode, Studio groups low-level runtime events into steps, node executions, model calls, tool calls, and checkpoint badges. Use Raw events only when debugging the tracer itself.",
  },
  {
    title: "Messages",
    icon: MessageSquare,
    body:
      "Messages show the actual chat traffic captured from kbench: system prompts, user prompts, assistant responses, streamed chunks, and tool messages. This is the easiest place to inspect what the model saw and returned.",
  },
  {
    title: "Checkpoints",
    icon: GitBranch,
    body:
      "Checkpoints are persisted snapshots of graph progress. In the timeline they appear near the node that produced them, so you can inspect the state at important execution boundaries without a separate checkpoint list.",
  },
];

export default function GuidePage() {
  return (
    <div className="h-full overflow-auto bg-[#020617] p-8 md:p-12 scrollbar-thin scrollbar-thumb-[#1E293B]">
      <div className="max-w-5xl mx-auto">
        <div className="mb-10">
          <h1 className="text-3xl font-bold tracking-tight text-white">Guide</h1>
          <p className="mt-3 max-w-3xl text-[15px] leading-relaxed text-slate-400">
            KaTrace Studio is optimized for inspecting how a KaGraph agent behaves during a benchmark run. The trace keeps raw runtime events, but the default UI turns them into a smaller behavior timeline.
          </p>
        </div>

        <section className="mb-8 rounded-xl border border-[#1E293B] bg-[#0F172A] p-6 shadow-sm">
          <div className="mb-4 flex items-center gap-3 text-base font-bold tracking-wide text-white">
            <Braces className="h-5 w-5 text-[#22C55E]" />
            Event Inspector
          </div>
          <p className="mb-6 text-[15px] leading-relaxed text-slate-400">
            The Event Inspector explains the timeline row you clicked. For agent debugging, the two most important parts are the node write delta and the resulting state snapshot.
          </p>
          <div className="grid gap-5 md:grid-cols-2">
            <div className="rounded-lg border border-[#1E293B] bg-[#020617] p-5 hover:border-[#334155] transition-colors">
              <div className="mb-3 text-sm font-bold text-white tracking-wide">Node writes</div>
              <p className="text-sm leading-relaxed text-slate-400">
                Node writes are the fields produced by the selected node during that event. Think of this as the node's output delta. For example, in the ToT tutorial, `expand` writes `candidates`, `score` writes `scored_candidates`, and `prune` writes the next beam plus `depth`.
              </p>
              <p className="mt-4 text-sm leading-relaxed text-slate-400">
                This is usually the first place to look when you want to answer: what did this node decide, generate, score, route, or change?
              </p>
            </div>
            <div className="rounded-lg border border-[#1E293B] bg-[#020617] p-5 hover:border-[#334155] transition-colors">
              <div className="mb-3 text-sm font-bold text-white tracking-wide">State snapshot</div>
              <p className="text-sm leading-relaxed text-slate-400">
                State snapshot is the accumulated graph state after the selected event or checkpoint. It includes earlier writes from previous nodes plus the latest update when a snapshot is available.
              </p>
              <p className="mt-4 text-sm leading-relaxed text-slate-400">
                Use it to understand what the next node will receive. If Node writes are the delta, State snapshot is the full current state.
              </p>
            </div>
          </div>
        </section>

        <section className="mb-10 grid gap-5 md:grid-cols-3">
          {sections.map((section) => {
            const Icon = section.icon;
            return (
              <div key={section.title} className="rounded-xl border border-[#1E293B] bg-[#0F172A] p-6 hover:bg-[#1E293B]/30 transition-colors shadow-sm">
                <div className="mb-4 flex items-center gap-3 text-base font-bold tracking-wide text-white">
                  <Icon className="h-5 w-5 text-[#22C55E]" />
                  {section.title}
                </div>
                <p className="text-sm leading-relaxed text-slate-400">{section.body}</p>
              </div>
            );
          })}
        </section>

        <Link href="/traces" className="inline-flex items-center gap-2 rounded-lg bg-[#22C55E] px-5 py-3 text-sm font-bold text-[#020617] hover:bg-[#16a34a] transition-all shadow-lg shadow-[#22C55E]/20">
          Open traces
          <ArrowRight className="h-4 w-4" />
        </Link>
      </div>
    </div>
  );
}
