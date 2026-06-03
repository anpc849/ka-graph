"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Activity, ArrowLeft, Bot, Braces, ChevronDown, ChevronRight, Clock, GitBranch, MessageSquare, Play, RefreshCcw, Search, Terminal, Wrench, XCircle } from "lucide-react";
import { format } from "date-fns";
import { ReactFlow, Background, Controls, type Edge, type Node as FlowNode, Position, MarkerType, Handle, type EdgeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "dagre";
import { cn } from "@/lib/utils";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const API_BASE = process.env.NEXT_PUBLIC_KATRACE_API_URL || "";

interface TraceEvent {
  id: string;
  sequence: number;
  timestamp: string;
  event: string;
  name?: string | null;
  node?: string | null;
  checkpoint_id?: string | null;
  parent_ids?: string[] | null;
  data?: Record<string, any> | null;
  metadata?: Record<string, any> | null;
}

interface TimelineNode {
  id: string;
  label: string;
  sublabel?: string;
  kind: string;
  depth: number;
  event?: TraceEvent;
  primary?: boolean;
  badges?: string[];
  relatedEvents?: TraceEvent[];
  children: TimelineNode[];
  startTimeMs?: number;
  endTimeMs?: number;
}

interface MessageItem {
  sequence: number;
  role: string;
  sender_name: string;
  content: any;
  tool_calls?: any[];
  metadata?: Record<string, any>;
  streamed?: boolean;
  event: TraceEvent;
}

interface MessageGroup {
  id: string;
  label: string;
  breadcrumb: string;
  depth: number;
  messages: MessageItem[];
  children: MessageGroup[];
}

interface TraceData {
  trace: any;
  spans: any[];
  generations: any[];
  event_count: number;
  checkpoint_count: number;
}

interface CheckpointSummary {
  checkpoint_id: string;
  sequence: number;
  timestamp: string;
  event: string;
  name?: string | null;
  node?: string | null;
  data?: Record<string, any> | null;
  metadata?: Record<string, any> | null;
}

interface PlaygroundDefaults {
  input: any;
  config: Record<string, any>;
  context: Record<string, any>;
  recursion_limit: number;
  chat_name: string;
  system_instructions?: string | null;
  model_id?: string | null;
}

interface FieldSpec {
  name: string;
  type?: string;
  required?: boolean;
}

type FieldDrafts = Record<string, string>;

const QUICK_INPUT_FIELD_PRIORITY = ["input", "question", "task", "query", "prompt", "message", "value"];

const EVENT_FILTERS = [
  "primary",
  "node",
  "tool",
  "model",
  "message",
  "checkpoint",
  "error",
  "all",
] as const;

type TimelineMode = "behavior" | "raw";

const nodeTypes = {
  cluster: ({ data, style }: any) => (
    <div style={{ ...style, position: "relative", width: "100%", height: "100%" }}>
      <div style={{ position: "absolute", top: 8, left: 12, fontSize: 12, fontWeight: "bold", color: "#94a3b8" }}>
        {data.label}
      </div>
    </div>
  ),
  custom: ({ data, style }: any) => (
    <div style={style}>
      {/* Top handles — shared anchor for all edges entering/leaving from top */}
      <Handle type="target" position={Position.Top} id="top-target" style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Top} id="top-source" style={{ opacity: 0 }} />
      {/* Bottom handles — shared anchor for all edges entering/leaving from bottom */}
      <Handle type="source" position={Position.Bottom} id="bottom-source" style={{ opacity: 0 }} />
      <Handle type="target" position={Position.Bottom} id="bottom-target" style={{ opacity: 0 }} />
      {/* Right handles for self-loops */}
      <Handle type="source" position={Position.Right} id="right-source" style={{ opacity: 0 }} />
      <Handle type="target" position={Position.Right} id="right-target" style={{ opacity: 0 }} />
      {data.label}
    </div>
  ),
};

// Custom edge that renders a quadratic bezier with a configurable horizontal control-point
// offset. When offset=0 it behaves like a straight top-to-bottom line (for normal edges).
// Custom edge using a CUBIC bezier for smooth, organic curves.
//
// data.curveOffset  — horizontal shift of the control points (px).
//                     +70 arcs right, -70 arcs left, 0 flows straight.
//                     Bidirectional pairs use opposite signs so they never overlap.
//
// data.labelOffsetY — vertical nudge applied to the label position (px).
//                     Used when multiple labeled edges share the same source/target
//                     to fan the labels apart and avoid overlap.
function CurvedEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  data,
  style,
  markerEnd,
  label,
  labelStyle,
  animated,
}: EdgeProps) {
  const offset       = typeof data?.curveOffset  === "number" ? (data.curveOffset  as number) : 0;
  const labelOffsetY = typeof data?.labelOffsetY === "number" ? (data.labelOffsetY as number) : 0;
  const sOffsetX     = typeof data?.sourceOffsetX === "number" ? (data.sourceOffsetX as number) : 0;
  const tOffsetX     = typeof data?.targetOffsetX === "number" ? (data.targetOffsetX as number) : 0;

  const sx = sourceX + sOffsetX;
  const sy = sourceY;
  const tx = targetX + tOffsetX;
  const ty = targetY;

  // Determine the "direction" of the edge in screen space (SVG y grows downward).
  // sign = +1  → going down  (forward edge)
  // sign = -1  → going up    (backward / loop edge)
  const dy      = ty - sy;
  const absDy   = Math.abs(dy);
  let strength = Math.min(absDy * 0.5, 120);
  const sign     = dy >= 0 ? 1 : -1;

  let d = "";
  let lx = 0;
  let ly = 0;

  const pointOnCubic = (
    t: number,
    p0x: number,
    p0y: number,
    p1x: number,
    p1y: number,
    p2x: number,
    p2y: number,
    p3x: number,
    p3y: number,
  ) => {
    const mt = 1 - t;
    return {
      x: mt*mt*mt*p0x + 3*mt*mt*t*p1x + 3*mt*t*t*p2x + t*t*t*p3x,
      y: mt*mt*mt*p0y + 3*mt*mt*t*p1y + 3*mt*t*t*p2y + t*t*t*p3y,
    };
  };

  if (data?.isSelfLoop) {
    // Draw a loop extending to the right
    // sx, sy are at the right center of the node (because we use right handles).
    const loopSizeX = 80;
    const loopSizeY = 50;
    const cp1x = sx + loopSizeX;
    const cp1y = sy - loopSizeY;
    const cp2x = tx + loopSizeX;
    const cp2y = ty + loopSizeY;
    d = `M ${sx},${sy} C ${cp1x},${cp1y} ${cp2x},${cp2y} ${tx},${ty}`;
    
    const labelPoint = pointOnCubic(0.5, sx, sy, cp1x, cp1y, cp2x, cp2y, tx, ty);
    lx = labelPoint.x;
    ly = labelPoint.y + labelOffsetY;
  } else {
    // Control points are offset horizontally by `offset * 0.6` so the curve
    // bows smoothly left or right rather than kinking at the endpoints.
    const cp1x = sx + offset * 0.6;
    const cp1y = sy + sign * strength;
    const cp2x = tx + offset * 0.6;
    const cp2y = ty - sign * strength;

    d = `M ${sx},${sy} C ${cp1x},${cp1y} ${cp2x},${cp2y} ${tx},${ty}`;

    const labelT = typeof data?.labelT === "number" ? (data.labelT as number) : 0.68;
    const labelPoint = pointOnCubic(labelT, sx, sy, cp1x, cp1y, cp2x, cp2y, tx, ty);
    lx = labelPoint.x;
    ly = labelPoint.y + labelOffsetY;
  }

  const labelText = typeof label === "string" ? label : label ? String(label) : "";
  const labelWidth = Math.min(110, Math.max(34, labelText.length * 7 + 16));
  const labelFill = (labelStyle as React.CSSProperties | undefined)?.fill || "#E2E8F0";


  return (
    <>
      <path
        id={id}
        className={`react-flow__edge-path${animated ? " animated" : ""}`}
        d={d}
        style={style as React.CSSProperties}
        markerEnd={markerEnd}
        fill="none"
      />
      {labelText && (
        <g transform={`translate(${lx}, ${ly})`} pointerEvents="none">
          <rect
            x={-labelWidth / 2}
            y={-10}
            width={labelWidth}
            height={20}
            rx={5}
            fill="rgba(2,6,23,0.9)"
            stroke="rgba(51,65,85,0.85)"
            strokeWidth={1}
          />
          <text
            textAnchor="middle"
            dominantBaseline="central"
            fill={String(labelFill)}
            fontSize={11}
            fontWeight={700}
            paintOrder="stroke"
            stroke="rgba(2,6,23,0.75)"
            strokeWidth={3}
          >
            {labelText}
          </text>
        </g>
      )}
    </>
  );
}

const edgeTypes = { curved: CurvedEdge };

function layoutGraph(nodes: FlowNode[], edges: Edge[]) {
  const graph = new dagre.graphlib.Graph({ compound: true });
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir: "TB", nodesep: 64, ranksep: 84 });
  
  nodes.forEach((node) => {
    if (node.type === "cluster") {
      graph.setNode(node.id, { label: node.data.label });
    } else {
      graph.setNode(node.id, { width: 160, height: 48 });
      if (node.parentId) {
        if (!graph.hasNode(node.parentId)) graph.setNode(node.parentId, {});
        graph.setParent(node.id, node.parentId);
      }
    }
  });

  // Identify back-edges via DFS so we can omit them from dagre.
  // Dagre handles acyclic compound graphs perfectly but scrambles on cycles.
  const visited = new Set();
  const recStack = new Set();
  const backEdges = new Set();

  function dfs(nodeId: string) {
    visited.add(nodeId);
    recStack.add(nodeId);
    edges.filter(e => e.source === nodeId).forEach(edge => {
      if (!visited.has(edge.target)) {
        dfs(edge.target);
      } else if (recStack.has(edge.target)) {
        backEdges.add(edge.id);
      }
    });
    recStack.delete(nodeId);
  }
  
  // Prioritize natural flow starting points
  if (nodes.find(n => n.id === "__start__")) dfs("__start__");
  nodes.forEach(n => {
    if (n.id.endsWith(":__start__") && !visited.has(n.id)) dfs(n.id);
  });
  nodes.forEach(n => {
    if (!visited.has(n.id)) dfs(n.id);
  });
  
  edges.forEach((edge) => {
    if (backEdges.has(edge.id)) return; // Omit back edge to prevent cycle scramble
    
    // We do NOT route crossing edges to the cluster boundary.
    // Instead, we just use the exact internal nodes.
    // Since we already omitted back-edges, dagre will perfectly rank the compound nodes
    // strictly top-to-bottom without scrambling the internals or throwing rank errors!
    if (edge.source !== edge.target) {
      graph.setEdge(edge.source, edge.target);
    }
  });
  
  dagre.layout(graph);

  nodes.forEach((node) => {
    const pos = graph.node(node.id);
    if (!pos) return;
    
    if (node.type === "cluster") {
      const padX = 24;
      const padY = 32;
      node.position = { x: pos.x - pos.width / 2 - padX, y: pos.y - pos.height / 2 - padY };
      node.style = { ...node.style, width: pos.width + padX * 2, height: pos.height + padY * 2 };
    } else {
      if (node.parentId) {
        const parentPos = graph.node(node.parentId);
        if (parentPos) {
          const padX = 24;
          const padY = 32;
          const parentLeft = parentPos.x - parentPos.width / 2 - padX;
          const parentTop = parentPos.y - parentPos.height / 2 - padY;
          node.position = { x: (pos.x - 80) - parentLeft, y: (pos.y - 24) - parentTop };
        } else {
          node.position = { x: pos.x - 80, y: pos.y - 24 };
        }
      } else {
        node.position = { x: pos.x - 80, y: pos.y - 24 };
      }
    }
  });

  // Build the full set of edge keys to detect bidirectional pairs.
  // A pair (A, B) is bidirectional when both A→B and B→A exist in the edges list.
  const edgeKeySet = new Set(edges.map((e) => `${e.source}→${e.target}`));

  edges.forEach((edge) => {
    const src = graph.node(edge.source);
    const tgt = graph.node(edge.target);
    if (!src || !tgt) return;

    const isBackward    = src.y >= tgt.y;
    const isBidirectional = edgeKeySet.has(`${edge.target}→${edge.source}`);

    if (edge.source === edge.target) {
      // ── SELF LOOP edge ──────────────────────────────────────────────────
      edge.sourceHandle = "right-source";
      edge.targetHandle = "right-target";
      edge.data = { ...edge.data, isSelfLoop: true, labelT: 0.5 };
    } else if (!isBackward) {
      // ── FORWARD edge ──────────────────────────────────────────────────────
      // Exits bottom of source, enters top of target.
      // Bidirectional forward edge bends RIGHT (+70) so its return edge
      // can bend LEFT (-70) and the two curves never cross.
      edge.sourceHandle = "bottom-source";
      edge.targetHandle = "top-target";
      edge.data = { ...edge.data, curveOffset: isBidirectional ? 70 : 0, labelT: 0.72 };
    } else {
      // ── BACKWARD / LOOP edge ───────────────────────────────────────────────
      if (isBidirectional) {
        // Same physical anchor points as the forward counterpart but opposite curve.
        //
        // Forward  A→B : bottom-of-A  →  top-of-B  (curveOffset = +70, bows right)
        // Backward B→A : top-of-B     →  bottom-of-A (curveOffset = -70, bows left)
        //
        // The two curves share the exact same two pixel anchors but arc in opposite
        // directions → no overlap, minimum start/end points, no intersection.
        edge.sourceHandle = "top-source";    // top of the lower node
        edge.targetHandle = "bottom-target"; // bottom of the upper node
        edge.data = { ...edge.data, curveOffset: -70, labelT: 0.72 };
      } else {
        // Unidirectional backward edge.
        // If it spans a long distance (e.g. replan back to __start__), route it out of the RIGHT side
        // and bow it outwards so it perfectly clears the incoming top edges and all intermediate nodes.
        const isLongBackward = src.y - tgt.y > 100;
        edge.sourceHandle = isLongBackward ? "right-source" : "top-source";
        edge.targetHandle = isLongBackward ? "right-target" : "bottom-target";
        edge.data = { ...edge.data, curveOffset: isLongBackward ? 300 : 0, labelT: 0.72 };
      }
    }
  });

  // ── Endpoint Horizontal Spacing ──────────────────────────────────────────
  // Instead of all edges sharing the exact center point, we fan out edges 
  // entering/leaving the same node face so they are distinct.
  const POINT_SPREAD = 20;

  const topAttachments = new Map<string, Array<{ edge: Edge, type: 'source' | 'target' }>>();
  const bottomAttachments = new Map<string, Array<{ edge: Edge, type: 'source' | 'target' }>>();

  edges.forEach((edge) => {
    // Top face
    if (edge.sourceHandle === 'top-source') {
      if (!topAttachments.has(edge.source)) topAttachments.set(edge.source, []);
      topAttachments.get(edge.source)!.push({ edge, type: 'source' });
    } else if (edge.targetHandle === 'top-target') {
      if (!topAttachments.has(edge.target)) topAttachments.set(edge.target, []);
      topAttachments.get(edge.target)!.push({ edge, type: 'target' });
    }
    
    // Bottom face
    if (edge.sourceHandle === 'bottom-source') {
      if (!bottomAttachments.has(edge.source)) bottomAttachments.set(edge.source, []);
      bottomAttachments.get(edge.source)!.push({ edge, type: 'source' });
    } else if (edge.targetHandle === 'bottom-target') {
      if (!bottomAttachments.has(edge.target)) bottomAttachments.set(edge.target, []);
      bottomAttachments.get(edge.target)!.push({ edge, type: 'target' });
    }
  });

  const applySpacing = (attachments: Map<string, Array<{ edge: Edge, type: 'source' | 'target' }>>) => {
    attachments.forEach((group) => {
      if (group.length < 2) return;
      
      // Sort so edges connecting to nodes further left get negative offsets, etc.
      group.sort((a, b) => {
        const otherNodeA = a.type === 'source' ? graph.node(a.edge.target) : graph.node(a.edge.source);
        const otherNodeB = b.type === 'source' ? graph.node(b.edge.target) : graph.node(b.edge.source);
        const diffX = (otherNodeA?.x || 0) - (otherNodeB?.x || 0);
        if (diffX !== 0) return diffX;
        
        // If connecting to the same node (bidirectional), sort by curveOffset 
        // to prevent the curves from crossing. 
        // A curve offset of -70 (arcs left) should attach to the left side (negative index)
        // A curve offset of +70 (arcs right) should attach to the right side (positive index)
        const offsetA = (a.edge.data?.curveOffset as number) || 0;
        const offsetB = (b.edge.data?.curveOffset as number) || 0;
        return offsetA - offsetB;
      });

      group.forEach((item, i) => {
        const offset = (i - (group.length - 1) / 2) * POINT_SPREAD;
        if (!item.edge.data) item.edge.data = {};
        if (item.type === 'source') {
          item.edge.data.sourceOffsetX = offset;
        } else {
          item.edge.data.targetOffsetX = offset;
        }
      });
    });
  };

  applySpacing(topAttachments);
  applySpacing(bottomAttachments);

  // ── Label-overlap prevention ─────────────────────────────────────────────
  // When multiple labeled edges share the same source node, their labels end
  // up at a similar Y position (all at the midpoint between two rank rows).
  // We fan them vertically (±13 px per step) so they never overlap.
  const LABEL_SPREAD = 16; // px between adjacent labels

  const bySource = new Map<string, Edge[]>();
  const byTarget = new Map<string, Edge[]>();
  edges.forEach((edge) => {
    if (!edge.label) return;
    if (!bySource.has(edge.source)) bySource.set(edge.source, []);
    bySource.get(edge.source)!.push(edge);
    if (!byTarget.has(edge.target)) byTarget.set(edge.target, []);
    byTarget.get(edge.target)!.push(edge);
  });

  // Fan labels that share the same SOURCE node.
  bySource.forEach((group) => {
    if (group.length < 2) return;
    group.forEach((edge, i) => {
      const nudge = (i - (group.length - 1) / 2) * LABEL_SPREAD;
      edge.data = { ...edge.data, labelOffsetY: Number(edge.data?.labelOffsetY ?? 0) + nudge };
    });
  });

  // Fan labels that share the same TARGET node (they haven't been touched by the
  // source pass unless there is a genuine collision, so this is safe to add on top).
  byTarget.forEach((group) => {
    if (group.length < 2) return;
    // Only apply if source-based spreading hasn't already resolved the overlap.
    // Check by comparing unique sources: if all edges came from different sources
    // the source pass already spread them; skip to avoid double-nudging.
    const uniqueSources = new Set(group.map((e) => e.source));
    if (uniqueSources.size < group.length) {
      group.forEach((edge, i) => {
        const nudge = (i - (group.length - 1) / 2) * LABEL_SPREAD;
        edge.data = { ...edge.data, labelOffsetY: Number(edge.data?.labelOffsetY ?? 0) + nudge };
      });
    }
  });

  return { nodes, edges };
}


function eventKind(event: TraceEvent): string {
  if (event.event.includes("subgraph")) return "subgraph";
  if (event.event.includes("checkpoint")) return "checkpoint";
  if (event.event.includes("tool")) return "tool";
  if (event.event.includes("chat_model")) return "model";
  if (event.event.includes("message")) return "message";
  if (event.event.includes("error")) return "error";
  if (event.event.includes("node")) return "node";
  return "other";
}

function eventIcon(kind: string) {
  if (kind === "tool") return <Wrench className="h-3.5 w-3.5" />;
  if (kind === "model") return <Bot className="h-3.5 w-3.5" />;
  if (kind === "message") return <MessageSquare className="h-3.5 w-3.5" />;
  if (kind === "checkpoint") return <GitBranch className="h-3.5 w-3.5" />;
  if (kind === "error") return <XCircle className="h-3.5 w-3.5" />;
  return <Activity className="h-3.5 w-3.5" />;
}

function compactJson(value: any) {
  if (value === undefined || value === null) return "";
  return JSON.stringify(value, null, 2);
}

function formatUsdFromNanodollars(value: number) {
  if (!Number.isFinite(value) || value <= 0) return "$0.000000";
  return `$${(value / 1_000_000_000).toFixed(6)}`;
}

function formatCount(value: number) {
  return Number.isFinite(value) ? new Intl.NumberFormat().format(value) : "0";
}

function eventTimestampMs(event?: TraceEvent) {
  if (!event?.timestamp) return null;
  const value = new Date(event.timestamp).getTime();
  return Number.isFinite(value) ? value : null;
}

function formatDuration(start?: TraceEvent, end?: TraceEvent) {
  const startMs = eventTimestampMs(start);
  const endMs = eventTimestampMs(end);
  if (startMs === null || endMs === null || endMs < startMs) return "";
  const ms = endMs - startMs;
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms}ms`;
}

function traceDurationMinutes(trace: any) {
  let ms = Number(trace?.duration_ms || 0);
  if (!ms && trace?.start_time && trace?.end_time) {
    const start = new Date(trace.start_time).getTime();
    const end = new Date(trace.end_time).getTime();
    if (Number.isFinite(start) && Number.isFinite(end) && end >= start) {
      ms = end - start;
    }
  }
  if (!ms) return "-";
  return `${(ms / 60_000).toFixed(2)} min`;
}

function usageSublabel(event: TraceEvent) {
  const usage = event.data?.usage || {};
  const inputTokens = usage.input_tokens;
  const outputTokens = usage.output_tokens;
  const tokenPart =
    inputTokens !== undefined || outputTokens !== undefined
      ? `${inputTokens ?? 0} in / ${outputTokens ?? 0} out`
      : "";
  const totalCost = Number(usage.total_cost_nanodollars || 0);
  const costPart = totalCost ? formatUsdFromNanodollars(totalCost) : "";
  return [event.name || "model", tokenPart, costPart].filter(Boolean).join(" Â· ");
}

function updateKeys(event?: TraceEvent) {
  const update = event?.data?.update;
  if (!update || typeof update !== "object" || Array.isArray(update)) return [];
  return Object.keys(update);
}

function fieldDraftsFromObject(value: any, schema: FieldSpec[] = []): FieldDrafts {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return schema.length === 0
      ? { value: value === undefined ? "" : typeof value === "string" ? value : compactJson(value) }
      : Object.fromEntries(schema.map((field) => [field.name, ""]));
  }
  if (schema.length > 0) {
    return Object.fromEntries(
      schema.map((field) => {
        const item = value[field.name];
        return [field.name, item === undefined ? "" : typeof item === "string" ? item : compactJson(item)];
      }),
    );
  }
  const fields = Object.fromEntries(
    Object.entries(value).map(([key, item]) => [
      key,
      typeof item === "string" ? item : compactJson(item),
    ]),
  );
  schema.forEach((field) => {
    if (fields[field.name] === undefined) fields[field.name] = "";
  });
  return fields;
}

function parseDraftValue(raw: string, original: any) {
  if (typeof original === "number") return Number(raw);
  if (typeof original === "boolean") return raw.trim().toLowerCase() === "true";
  if (original && typeof original === "object") return raw.trim() ? JSON.parse(raw) : Array.isArray(original) ? [] : {};
  if (original === null || original === undefined) {
    if (!raw.trim()) return "";
    try {
      return JSON.parse(raw);
    } catch {
      return raw;
    }
  }
  return raw;
}

function parseBySchemaValue(raw: string, spec?: FieldSpec) {
  const type = spec?.type || "";
  if (type === "int" || type === "float" || type === "number") return Number(raw);
  if (type === "bool" || type === "boolean") return raw.trim().toLowerCase() === "true";
  if (type.startsWith("list") || type.startsWith("dict") || type.startsWith("tuple")) {
    return raw.trim() ? JSON.parse(raw) : type.startsWith("list") || type.startsWith("tuple") ? [] : {};
  }
  return parseDraftValue(raw, undefined);
}

function parseFieldDrafts(label: string, drafts: FieldDrafts, original: any) {
  try {
    if (!original || typeof original !== "object" || Array.isArray(original)) {
      return parseDraftValue(drafts.value || "", original);
    }
    return Object.fromEntries(
      Object.entries(drafts).map(([key, raw]) => [key, parseDraftValue(raw, original?.[key])]),
    );
  } catch (error: any) {
    throw new Error(`${label} field is not valid: ${error.message}`);
  }
}

function parseFieldDraftsWithSchema(label: string, drafts: FieldDrafts, original: any, schema: FieldSpec[]) {
  try {
    if (schema.length > 0) {
      return Object.fromEntries(
        schema.map((field) => {
          const raw = drafts[field.name] ?? "";
          return [
            field.name,
            original && typeof original === "object" && !Array.isArray(original) && original[field.name] !== undefined
              ? parseDraftValue(raw, original[field.name])
              : parseBySchemaValue(raw, field),
          ];
        }),
      );
    }
    if (!original || typeof original !== "object" || Array.isArray(original)) {
      return parseDraftValue(drafts.value || "", original);
    }
    return Object.fromEntries(
      Object.entries(drafts).map(([key, raw]) => {
        const spec = schema.find((field) => field.name === key);
        return [key, original?.[key] !== undefined ? parseDraftValue(raw, original[key]) : parseBySchemaValue(raw, spec)];
      }),
    );
  } catch (error: any) {
    throw new Error(`${label} field is not valid: ${error.message}`);
  }
}

function primaryInputField(fields: FieldDrafts, schema: FieldSpec[]) {
  const schemaNames = schema.map((field) => field.name);
  const fieldNames = Object.keys(fields);
  const names = schemaNames.length > 0 ? schemaNames : fieldNames;
  for (const preferred of QUICK_INPUT_FIELD_PRIORITY) {
    if (names.includes(preferred)) return preferred;
  }
  return names[0] || "input";
}

function EditableFields({
  title,
  fields,
  onChange,
  shape,
  schema,
  disabled,
}: {
  title: string;
  fields: FieldDrafts;
  onChange: (value: FieldDrafts) => void;
  shape: any;
  schema: FieldSpec[];
  disabled: boolean;
}) {
  const orderedKeys = [
    ...schema.map((field) => field.name),
    ...Object.keys(fields).filter((key) => !schema.some((field) => field.name === key)),
  ];
  return (
    <div className="grid gap-2">
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{title}</span>
        {schema.length > 0 ? <span>{schema.length} detected fields</span> : null}
      </div>
      <div className="grid gap-2">
        {orderedKeys.length === 0 ? <div className="rounded border border-border bg-background px-3 py-2 text-xs text-muted-foreground">No detected fields.</div> : orderedKeys.map((key) => {
          const value = fields[key] ?? "";
          const spec = schema.find((field) => field.name === key);
          const original = shape && typeof shape === "object" && !Array.isArray(shape) ? shape[key] : key === "value" ? shape : undefined;
          const typeHint = spec?.type || (original === undefined ? "any" : Array.isArray(original) ? "list" : typeof original);
          const isComplex = original && typeof original === "object";
          return (
            <label key={key} className="grid gap-1 text-xs text-muted-foreground">
              <span className="flex items-center justify-between gap-2">
                <span className="truncate">{key}</span>
                <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{typeHint}{spec?.required ? " required" : ""}</span>
              </span>
              {isComplex ? (
                <textarea value={value} onChange={(e) => onChange({ ...fields, [key]: e.target.value })} disabled={disabled} className="min-h-16 rounded-md border border-border bg-background p-2 font-mono text-xs text-foreground outline-none focus:ring-1 focus:ring-primary-accent disabled:opacity-50" />
              ) : (
                <input value={value} onChange={(e) => onChange({ ...fields, [key]: e.target.value })} disabled={disabled} className="rounded-md border border-border bg-background px-3 py-2 text-xs text-foreground outline-none focus:ring-1 focus:ring-primary-accent disabled:opacity-50" />
              )}
            </label>
          );
        })}
      </div>
    </div>
  );
}

function eventRunId(event: TraceEvent): string | null {
  return typeof event.metadata?.run_id === "string" ? event.metadata.run_id : null;
}

function eventParentRunId(event: TraceEvent): string | null {
  if (typeof event.metadata?.parent_run_id === "string") return event.metadata.parent_run_id;
  return event.parent_ids?.[0] || null;
}

function eventStep(event: TraceEvent): number | null {
  const raw = event.metadata?.step ?? event.data?.step;
  return typeof raw === "number" ? raw : null;
}

function graphDepth(event: TraceEvent): number {
  const raw = event.metadata?.graph_depth;
  return typeof raw === "number" ? raw : 0;
}

function stepScopeKey(parentRunId: string | null, step: number | null, fallback: number) {
  return `${parentRunId || "root"}:${step ?? fallback}`;
}

function eventLabel(event: TraceEvent) {
  if (event.event === "on_step_start") return `Step ${eventStep(event) ?? event.sequence}`;
  if (event.event === "on_node_start") return `Node ${event.node || event.name || event.sequence}`;
  if (event.event === "on_tool_start") return `Tool ${event.name || event.sequence}`;
  if (event.event === "on_subgraph_start") return `Subgraph ${event.name || event.sequence}`;
  return event.event;
}

function eventSublabel(event: TraceEvent) {
  if (event.event === "on_step_start") {
    const next = event.data?.next;
    return Array.isArray(next) ? `${next.length} scheduled node${next.length === 1 ? "" : "s"}` : event.name || "step";
  }
  if (event.event === "on_node_start" || event.event === "on_node_update" || event.event === "on_node_end") {
    return event.node || event.name || "node";
  }
  if (event.event === "on_chat_model_end") {
    const usage = event.data?.usage || {};
    const tokens = [usage.input_tokens, usage.output_tokens].filter((item) => item !== undefined && item !== null).join(" / ");
    return tokens ? `${tokens} tokens in/out` : event.name || "model";
  }
  return event.node || event.name || "graph";
}

function makeEventTimelineNode(event: TraceEvent, depth: number): TimelineNode {
  return {
    id: `event-${event.sequence}`,
    label: eventLabel(event),
    sublabel: eventSublabel(event),
    kind: eventKind(event),
    depth,
    event,
    primary: true,
    children: [],
  };
}

function buildRawTimelineTree(events: TraceEvent[]): TimelineNode[] {
  const graphRoot: TimelineNode = {
    id: "graph-root",
    label: "Graph",
    sublabel: "runtime",
    kind: "other",
    depth: 0,
    primary: true,
    children: [],
  };
  const runNodes = new Map<string, TimelineNode>();
  const stepNodes = new Map<string, TimelineNode>();
  const lastNodeByName = new Map<string, TimelineNode>();
  let activeStep: TimelineNode | null = null;
  let activeNode: TimelineNode | null = null;

  const appendTo = (parent: TimelineNode | null | undefined, child: TimelineNode) => {
    (parent || graphRoot).children.push(child);
  };

  [...events].sort((a, b) => a.sequence - b.sequence).forEach((event) => {
    const runId = eventRunId(event);
    const parentRunId = eventParentRunId(event);
    const step = eventStep(event);

    if (event.event === "on_graph_start") {
      graphRoot.event = event;
      graphRoot.label = event.name || "Graph";
      graphRoot.sublabel = `started at #${event.sequence}`;
      if (runId) runNodes.set(runId, graphRoot);
      return;
    }

    if (event.event === "on_step_start") {
      const parent = (parentRunId && runNodes.get(parentRunId)) || graphRoot;
      const node = makeEventTimelineNode(event, parent.depth + 1);
      if (graphDepth(event) > 0) node.label = `Subgraph step ${step ?? event.sequence}`;
      appendTo(parent, node);
      activeStep = node;
      stepNodes.set(stepScopeKey(parentRunId, step, event.sequence), node);
      if (runId) runNodes.set(runId, node);
      return;
    }

    if (event.event === "on_node_start" || event.event === "on_tool_start") {
      const parent = (parentRunId && runNodes.get(parentRunId)) || stepNodes.get(stepScopeKey(parentRunId, step, event.sequence)) || activeStep || graphRoot;
      const node = makeEventTimelineNode(event, parent.depth + 1);
      appendTo(parent, node);
      if (runId) runNodes.set(runId, node);
      if (event.event === "on_node_start") {
        activeNode = node;
        if (event.node || event.name) lastNodeByName.set(event.node || event.name || "", node);
      }
      return;
    }

    if (event.event === "on_subgraph_start") {
      const parent = (parentRunId && runNodes.get(parentRunId)) || stepNodes.get(stepScopeKey(parentRunId, step, event.sequence)) || activeNode || activeStep || graphRoot;
      const node = makeEventTimelineNode(event, parent.depth + 1);
      appendTo(parent, node);
      if (runId) runNodes.set(runId, node);
      return;
    }

    const parent =
      (parentRunId && runNodes.get(parentRunId)) ||
      (event.node && lastNodeByName.get(event.node)) ||
      stepNodes.get(stepScopeKey(parentRunId, step, event.sequence)) ||
      activeNode ||
      activeStep ||
      graphRoot;
    const node = makeEventTimelineNode(event, parent.depth + 1);
    appendTo(parent, node);
    if (event.event === "on_node_end" && activeNode && (activeNode.event?.node || activeNode.event?.name) === (event.node || event.name)) {
      activeNode = null;
    }
    if (event.event === "on_step_end" && activeStep === stepNodes.get(stepScopeKey(parentRunId, step, event.sequence))) {
      activeStep = null;
    }
  });

  return [graphRoot];
}

function buildBehaviorTimelineTree(events: TraceEvent[]): TimelineNode[] {
  const graphRoot: TimelineNode = {
    id: "behavior-graph-root",
    label: "Graph run",
    sublabel: "behavior timeline",
    kind: "other",
    depth: 0,
    primary: true,
    children: [],
    relatedEvents: [],
  };
  const runNodes = new Map<string, TimelineNode>();
  const stepNodes = new Map<string, TimelineNode>();
  const nodeRuns = new Map<string, TimelineNode>();
  const sorted = [...events].sort((a, b) => a.sequence - b.sequence);

  const appendTo = (parent: TimelineNode | undefined | null, child: TimelineNode) => {
    (parent || graphRoot).children.push(child);
  };

  const refreshNodeSublabel = (node: TimelineNode) => {
    const nodeStart = node.relatedEvents?.find((event) => event.event === "on_node_start" || event.event === "on_tool_start");
    const nodeEnd = node.relatedEvents?.find((event) => event.event === "on_node_end" || event.event === "on_tool_end");
    const duration = formatDuration(nodeStart, nodeEnd);
    const writes = updateKeys(node.event);
    const badges = node.badges || [];
    const modelCount = node.children.filter((child) => child.kind === "model").length;
    const parts = [
      writes.length ? `writes ${writes.join(", ")}` : "",
      modelCount ? `${modelCount} LLM call${modelCount === 1 ? "" : "s"}` : "",
      duration,
      ...badges,
    ].filter(Boolean);
    node.sublabel = parts.length ? parts.join(" - ") : nodeStart?.node || nodeStart?.name || "node";
  };

  sorted.forEach((event) => {
    const runId = eventRunId(event);
    const parentRunId = eventParentRunId(event);
    const step = eventStep(event);
    const stepKey = stepScopeKey(parentRunId, step, event.sequence);

    if (event.event === "on_graph_start") {
      graphRoot.event = event;
      graphRoot.label = event.name || "Graph run";
      graphRoot.sublabel = `started at #${event.sequence}`;
      graphRoot.relatedEvents?.push(event);
      if (runId) runNodes.set(runId, graphRoot);
      return;
    }

    if (event.event === "on_graph_end") {
      graphRoot.relatedEvents?.push(event);
      const duration = formatDuration(graphRoot.event, event);
      graphRoot.sublabel = ["completed", duration].filter(Boolean).join(" - ");
      return;
    }

    if (event.event === "on_subgraph_start") {
      const parent = (parentRunId && runNodes.get(parentRunId)) || stepNodes.get(stepKey) || graphRoot;
      const subgraphNode: TimelineNode = {
        id: `behavior-subgraph-${runId || event.sequence}`,
        label: `Subgraph ${event.name || "graph"}`,
        sublabel: "started",
        kind: "subgraph",
        depth: parent.depth + 1,
        event,
        primary: true,
        relatedEvents: [event],
        children: [],
      };
      appendTo(parent, subgraphNode);
      if (runId) runNodes.set(runId, subgraphNode);
      return;
    }

    if (event.event === "on_subgraph_end") {
      const subgraphNode = runId ? runNodes.get(runId) : undefined;
      if (subgraphNode) {
        subgraphNode.relatedEvents?.push(event);
        const duration = formatDuration(subgraphNode.event, event);
        subgraphNode.sublabel = ["completed", duration].filter(Boolean).join(" - ");
      }
      return;
    }

    if (event.event === "on_step_start") {
      const parent = (parentRunId && runNodes.get(parentRunId)) || graphRoot;
      const depth = graphDepth(event);
      const next = event.data?.next;
      const stepNode: TimelineNode = {
        id: `behavior-step-${parentRunId || "root"}-${step ?? event.sequence}`,
        label: depth > 0 ? `Subgraph step ${step ?? event.sequence}` : `Step ${step ?? event.sequence}`,
        sublabel: Array.isArray(next) ? `${next.join(", ") || "no nodes"} scheduled` : event.name || "step",
        kind: "step",
        depth: parent.depth + 1,
        event,
        primary: true,
        relatedEvents: [event],
        children: [],
      };
      appendTo(parent, stepNode);
      stepNodes.set(stepKey, stepNode);
      if (runId) runNodes.set(runId, stepNode);
      return;
    }

    if (event.event === "on_step_end") {
      const stepNode = stepNodes.get(stepKey);
      if (stepNode) {
        stepNode.relatedEvents?.push(event);
        const duration = formatDuration(stepNode.event, event);
        const next = event.data?.next;
        stepNode.sublabel = [
          duration,
          Array.isArray(next) && next.length > 0 ? `next ${next.join(", ")}` : "complete",
        ].filter(Boolean).join(" - ");
      }
      return;
    }

    if (event.event === "on_node_start" || event.event === "on_tool_start") {
      const parent = (parentRunId && runNodes.get(parentRunId)) || stepNodes.get(stepKey) || graphRoot;
      const node: TimelineNode = {
        id: `behavior-node-${runId || event.sequence}`,
        label: event.node || event.name || "node",
        sublabel: event.event === "on_tool_start" ? "tool started" : "node started",
        kind: event.event === "on_tool_start" ? "tool" : "node",
        depth: parent.depth + 1,
        event,
        primary: true,
        relatedEvents: [event],
        badges: [],
        children: [],
      };
      appendTo(parent, node);
      if (runId) {
        runNodes.set(runId, node);
        nodeRuns.set(runId, node);
      }
      return;
    }

    if (event.event === "on_chat_model_end") {
      const parent = (parentRunId && runNodes.get(parentRunId)) || stepNodes.get(stepKey) || graphRoot;
      const child: TimelineNode = {
        id: `behavior-model-${event.sequence}`,
        label: "LLM call",
        sublabel: usageSublabel(event),
        kind: "model",
        depth: parent.depth + 1,
        event,
        primary: true,
        relatedEvents: [event],
        children: [],
      };
      appendTo(parent, child);
      if (parent.kind === "node" || parent.kind === "tool") refreshNodeSublabel(parent);
      return;
    }

    if (event.event === "on_message" || event.event === "on_chat_model_stream") {
      const parent = (parentRunId && runNodes.get(parentRunId)) || stepNodes.get(stepKey) || graphRoot;
      appendTo(parent, {
        id: `behavior-message-${event.sequence}`,
        label: event.name || "Message",
        sublabel: event.event === "on_chat_model_stream" ? "stream chunk" : event.data?.message?.role || "message",
        kind: "message",
        depth: parent.depth + 1,
        event,
        primary: false,
        relatedEvents: [event],
        children: [],
      });
      return;
    }

    if (event.event === "on_node_update") {
      const parent = (parentRunId && runNodes.get(parentRunId)) || (runId && nodeRuns.get(runId));
      if (parent) {
        parent.event = event;
        parent.relatedEvents?.push(event);
        refreshNodeSublabel(parent);
        return;
      }
    }

    if (event.event === "on_node_end" || event.event === "on_tool_end") {
      const parent = (runId && runNodes.get(runId)) || (parentRunId && runNodes.get(parentRunId));
      if (parent) {
        parent.relatedEvents?.push(event);
        refreshNodeSublabel(parent);
        return;
      }
    }

    if (event.event === "on_checkpoint") {
      const parent = (parentRunId && runNodes.get(parentRunId)) || stepNodes.get(stepKey) || graphRoot;
      if (parent.kind === "node" || parent.kind === "tool") {
        parent.relatedEvents?.push(event);
        parent.badges = [...(parent.badges || []), `checkpoint #${event.sequence}`];
        refreshNodeSublabel(parent);
        return;
      }
      appendTo(parent, {
        id: `behavior-checkpoint-${event.sequence}`,
        label: "Checkpoint",
        sublabel: event.name || event.checkpoint_id || `#${event.sequence}`,
        kind: "checkpoint",
        depth: parent.depth + 1,
        event,
        primary: false,
        relatedEvents: [event],
        children: [],
      });
      return;
    }

    if (event.event.includes("error")) {
      const parent = (parentRunId && runNodes.get(parentRunId)) || graphRoot;
      appendTo(parent, {
        id: `behavior-error-${event.sequence}`,
        label: "Error",
        sublabel: event.name || event.node || `#${event.sequence}`,
        kind: "error",
        depth: parent.depth + 1,
        event,
        primary: true,
        relatedEvents: [event],
        children: [],
      });
    }
  });

  return pruneEmptyStepNodes([graphRoot]);
}

function pruneEmptyStepNodes(nodes: TimelineNode[]): TimelineNode[] {
  return nodes.flatMap((node) => {
    const children = pruneEmptyStepNodes(node.children);
    if (node.kind === "step" && children.length === 0) return [];
    return [{ ...node, children }];
  });
}
function timelineNodeMatches(node: TimelineNode, filter: string, needle: string): boolean {
  const event = node.event;
  const kind = event ? eventKind(event) : node.kind;
  if (filter === "primary" && node.primary === false) return false;
  if (filter === "primary" && node.primary !== false) {
    if (!needle) return true;
    if (!event) return `${node.label} ${node.sublabel || ""}`.toLowerCase().includes(needle);
    return `${node.label} ${node.sublabel || ""} ${event.event} ${event.name || ""} ${event.node || ""} ${compactJson(event.data)}`.toLowerCase().includes(needle);
  }
  if (filter !== "all" && kind !== filter) return false;
  if (!needle) return true;
  if (!event) return `${node.label} ${node.sublabel || ""}`.toLowerCase().includes(needle);
  return `${event.event} ${event.name || ""} ${event.node || ""} ${compactJson(event.data)}`.toLowerCase().includes(needle);
}

function filterTimelineTree(nodes: TimelineNode[], filter: string, needle: string): TimelineNode[] {
  return nodes.flatMap((node) => {
    const children = filterTimelineTree(node.children, filter, needle);
    if (timelineNodeMatches(node, filter, needle) || children.length > 0) {
      return [{ ...node, children }];
    }
    return [];
  });
}

function flattenTimeline(nodes: TimelineNode[], expanded: Record<string, boolean>): TimelineNode[] {
  const rows: TimelineNode[] = [];
  const visit = (node: TimelineNode) => {
    rows.push(node);
    const isExpanded = expanded[node.id] ?? true;
    if (isExpanded) node.children.forEach(visit);
  };
  nodes.forEach(visit);
  return rows;
}

function computeTimestamps(nodes: TimelineNode[]): { min: number, max: number } | null {
  let min = Infinity;
  let max = -Infinity;
  nodes.forEach(node => {
    let nodeMin = Infinity;
    let nodeMax = -Infinity;

    if (node.event && node.event.timestamp) {
      const ms = new Date(node.event.timestamp).getTime();
      if (!isNaN(ms)) {
        nodeMin = Math.min(nodeMin, ms);
        nodeMax = Math.max(nodeMax, ms);
      }
    }
    node.relatedEvents?.forEach(ev => {
      if (ev.timestamp) {
        const ms = new Date(ev.timestamp).getTime();
        if (!isNaN(ms)) {
          nodeMin = Math.min(nodeMin, ms);
          nodeMax = Math.max(nodeMax, ms);
        }
      }
    });

    const childRange = computeTimestamps(node.children);
    if (childRange) {
      nodeMin = Math.min(nodeMin, childRange.min);
      nodeMax = Math.max(nodeMax, childRange.max);
    }

    if (nodeMin !== Infinity) node.startTimeMs = nodeMin;
    if (nodeMax !== -Infinity) node.endTimeMs = nodeMax;

    if (nodeMin !== Infinity) min = Math.min(min, nodeMin);
    if (nodeMax !== -Infinity) max = Math.max(max, nodeMax);
  });

  return min !== Infinity ? { min, max } : null;
}

function getGanttColor(kind: string) {
  switch (kind) {
    case "error": return "bg-red-500/80 border-red-400 text-red-100";
    case "model": return "bg-purple-500/80 border-purple-400 text-purple-100";
    case "tool": return "bg-orange-500/80 border-orange-400 text-orange-100";
    case "step": return "bg-blue-500/80 border-blue-400 text-blue-100";
    case "subgraph": return "bg-slate-500/80 border-slate-400 text-slate-100";
    default: return "bg-emerald-500/80 border-emerald-400 text-emerald-100";
  }
}

function messageItemFromEvent(event: TraceEvent): MessageItem | null {
  if (event.event === "on_message" && event.data?.message) {
    const raw = event.data.message;
    return {
      sequence: event.sequence,
      role: raw.role || raw.sender?.role || event.metadata?.role || "assistant",
      sender_name: raw.sender_name || raw.sender?.name || raw.sender?.id || event.name || "message",
      content: raw.content || raw.text || "",
      tool_calls: raw.tool_calls || raw.metadata?.tool_calls || [],
      metadata: raw.metadata || {},
      event,
    };
  }
  if (event.event === "on_chat_model_stream" && event.data?.content) {
    return {
      sequence: event.sequence,
      role: "assistant",
      sender_name: event.name || "assistant",
      content: event.data.content,
      streamed: true,
      event,
    };
  }
  return null;
}

function buildMessageGroups(nodes: TimelineNode[], trail: string[] = []): MessageGroup[] {
  return nodes.flatMap((node) => {
    if (node.kind === "message") return [];
    const label = node.label || node.event?.name || "Context";
    const nextTrail = label === "Graph run" ? trail : [...trail, label];
    const directMessages = node.children
      .filter((child) => child.kind === "message" && child.event)
      .map((child) => messageItemFromEvent(child.event as TraceEvent))
      .filter((msg): msg is MessageItem => {
        if (!msg) return false;
        if (msg.role === "system") {
          const contentStr = typeof msg.content === "string" ? msg.content.trim() : JSON.stringify(msg.content);
          if (!contentStr || contentStr === "{}" || contentStr === "[]") return false;
        }
        return true;
      });
    const messages = directMessages;
    const childGroups = node.children.flatMap((child) => buildMessageGroups([child], nextTrail));
    if (messages.length === 0 && childGroups.length === 0) return [];
    return [
      {
        id: `message-group-${node.id}`,
        label,
        breadcrumb: nextTrail.join(" > ") || label,
        depth: Math.max(0, nextTrail.length - 1),
        messages,
        children: childGroups,
      },
    ];
  });
}

// ─── JSON Syntax Token renderers ───────────────────────────────────────────

/** Returns true when every element of an array is a primitive (no nesting). */
function isShallowPrimitiveArray(arr: any[]): boolean {
  return arr.every((v) => v === null || typeof v !== "object");
}

/** Detect math/logic operator symbols so they get distinct styling. */
function isOperator(v: string): boolean {
  return /^[+\-*/^%&|!<>=~]+$/.test(v.trim());
}

function JsonPrimitive({ value }: { value: string | number | boolean | null }) {
  if (value === null) return <span className="font-mono text-[11px] text-slate-500 italic">null</span>;
  if (typeof value === "boolean")
    return <span className="font-mono text-[11px] text-amber-400">{String(value)}</span>;
  if (typeof value === "number")
    return <span className="font-mono text-[11px] text-emerald-400">{value}</span>;
  // string — check for operator
  if (isOperator(value))
    return (
      <span className="inline-flex items-center justify-center rounded px-2 py-0.5 font-mono text-[12px] font-bold text-violet-300 bg-violet-900/40 border border-violet-500/40 leading-none">
        {value}
      </span>
    );
  return <span className="font-mono text-[11px] text-sky-300 break-words">&quot;{value}&quot;</span>;
}

/** Compact inline pill row for arrays of primitives (e.g. token lists). */
function InlineTokenRow({ items }: { items: any[] }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 py-1">
      <span className="font-mono text-[11px] text-slate-500">[</span>
      {items.map((v, i) => (
        <span key={i} className="flex items-center gap-1">
          <JsonPrimitive value={v} />
          {i < items.length - 1 && <span className="font-mono text-[11px] text-slate-600">,</span>}
        </span>
      ))}
      <span className="font-mono text-[11px] text-slate-500">]</span>
    </div>
  );
}

/** Collapsible JSON tree node. */
function JsonTreeNode({ label, data, defaultOpen = true }: { label?: string; data: any; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const isObj = data !== null && typeof data === "object";
  const isArr = Array.isArray(data);
  const count = isObj ? (isArr ? (data as any[]).length : Object.keys(data).length) : 0;

  // Primitive leaf
  if (!isObj) {
    return (
      <div className="flex items-baseline gap-2 py-0.5">
        {label !== undefined && (
          <span className="shrink-0 font-mono text-[11px] font-semibold text-teal-400">{label}:</span>
        )}
        <JsonPrimitive value={data} />
      </div>
    );
  }

  // Shallow primitive array → inline chip row
  if (isArr && isShallowPrimitiveArray(data as any[])) {
    return (
      <div className="flex items-start gap-2 py-0.5 flex-wrap">
        {label !== undefined && (
          <span className="shrink-0 font-mono text-[11px] font-semibold text-teal-400 mt-0.5">{label}:</span>
        )}
        <InlineTokenRow items={data as any[]} />
      </div>
    );
  }

  // Collapsible object / complex array
  const bracket = isArr ? ["[", "]"] : ["{", "}"];
  const entries = isArr
    ? (data as any[]).map((v, i) => [String(i), v] as [string, any])
    : Object.entries(data as Record<string, any>);

  return (
    <div className="py-0.5">
      <button
        onClick={() => setOpen((p) => !p)}
        className="flex items-center gap-1 group focus:outline-none"
      >
        <span className={cn(
          "flex h-4 w-4 items-center justify-center rounded text-[9px] transition-colors",
          "bg-white/5 border border-white/10 text-slate-400 group-hover:bg-white/10 group-hover:text-slate-200"
        )}>
          {open ? "▾" : "▸"}
        </span>
        {label !== undefined && (
          <span className="font-mono text-[11px] font-semibold text-teal-400">{label}:</span>
        )}
        <span className="font-mono text-[11px] text-slate-500">
          {bracket[0]}
          {!open && (
            <span className="mx-1 text-slate-500 italic">
              {isArr ? `${count} item${count !== 1 ? "s" : ""}` : `${count} key${count !== 1 ? "s" : ""}`}
            </span>
          )}
          {!open && bracket[1]}
        </span>
      </button>
      {open && (
        <div className="mt-1 ml-4 border-l border-white/10 pl-3 flex flex-col gap-0.5">
          {entries.map(([k, v]) => (
            <JsonTreeNode
              key={k}
              label={isArr ? undefined : k}
              data={v}
              defaultOpen={!Array.isArray(v) || !(v as any[]).every((x) => x !== null && typeof x === "object" && !Array.isArray(x))}
            />
          ))}
          <span className="font-mono text-[11px] text-slate-500">{bracket[1]}</span>
        </div>
      )}
    </div>
  );
}

function JsonViewer({ data }: { data: any }) {
  if (typeof data !== "object" || data === null) {
    return (
      <div className="font-mono text-[11px] text-slate-300">
        <JsonPrimitive value={data} />
      </div>
    );
  }

  if (Array.isArray(data)) {
    // Top-level array of objects — render each as a collapsible card
    if (!isShallowPrimitiveArray(data)) {
      return (
        <div className="flex flex-col gap-2">
          {(data as any[]).map((item, index) => (
            <div key={index} className="rounded-md border border-white/5 bg-white/5 px-3 py-2">
              <JsonTreeNode data={item} defaultOpen={index === 0} />
            </div>
          ))}
        </div>
      );
    }
    return <InlineTokenRow items={data} />;
  }

  // Top-level object — render each key as a styled card
  return (
    <div className="flex flex-col gap-2 mt-1">
      {Object.entries(data as Record<string, any>).map(([key, value]) => {
        const isSimplePrimitive = value === null || (typeof value !== "object");
        return (
          <div key={key} className="rounded-lg border border-white/10 bg-[#0F172A]/40 shadow-sm overflow-hidden">
            <div className="bg-[#1E293B]/60 px-4 py-2 border-b border-white/5 flex items-center">
              <span className="text-xs font-bold text-[#38bdf8] uppercase tracking-wider">{key}</span>
            </div>
            <div className="p-3">
              {typeof value === "string" && !isOperator(value) ? (
                <div className="prose prose-invert prose-sm max-w-none text-slate-200 leading-relaxed marker:text-slate-500 prose-p:leading-relaxed prose-a:text-[#3b82f6] hover:prose-a:underline w-full max-w-full">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      pre: ({node, ...props}: any) => <pre {...props} className="bg-[#020617]/80 border border-white/10 p-3 rounded-md overflow-x-hidden whitespace-pre-wrap break-words break-all w-full max-w-full" />,
                      code: ({node, ...props}: any) => <code {...props} className="whitespace-pre-wrap break-words break-all" />
                    }}
                  >
                    {value}
                  </ReactMarkdown>
                </div>
              ) : isSimplePrimitive ? (
                <JsonPrimitive value={value} />
              ) : (
                <JsonTreeNode data={value} defaultOpen />
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function isMediaBlock(value: any): boolean {
  if (!value || typeof value !== "object") return false;
  const type = String(value.type || "").toLowerCase();
  return Boolean(
    type === "image_url" ||
    type === "video_url" ||
    type === "audio_url" ||
    value.image_url ||
    value.video_url ||
    value.audio_url ||
    (typeof value.mime_type === "string" && /^(image|video|audio)\//.test(value.mime_type))
  );
}

function mediaUrl(value: any): string {
  if (!value || typeof value !== "object") return "";
  const nested = value.image_url || value.video_url || value.audio_url;
  if (typeof nested === "string") return nested;
  if (nested && typeof nested.url === "string") return nested.url;
  if (typeof value.url === "string") return value.url;
  if (typeof value.location === "string") return value.location;
  if (typeof value.content === "string" && typeof value.mime_type === "string") {
    return `data:${value.mime_type};base64,${value.content}`;
  }
  return "";
}

function mediaKind(value: any): "image" | "video" | "audio" | null {
  if (!value || typeof value !== "object") return null;
  const type = String(value.type || "").toLowerCase();
  const mime = String(value.mime_type || "").toLowerCase();
  if (type.includes("audio") || mime.startsWith("audio/")) return "audio";
  if (type.includes("video") || mime.startsWith("video/")) return "video";
  if (type.includes("image") || mime.startsWith("image/") || value.image_url) return "image";
  return null;
}

function contentBlocks(content: any): any[] {
  if (Array.isArray(content)) return content;
  if (content && typeof content === "object" && Array.isArray(content.content)) return content.content;
  return [content];
}

function hasMultimodalContent(content: any): boolean {
  return contentBlocks(content).some(isMediaBlock);
}

function messageContentText(content: any): string {
  if (content === undefined || content === null) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter((block) => !isMediaBlock(block))
      .map((block) => {
        if (typeof block === "string") return block;
        if (block?.type === "text") return block.text || "";
        if (typeof block?.text === "string") return block.text;
        return "";
      })
      .filter(Boolean)
      .join("\n\n");
  }
  if (content?.type === "text") return content.text || "";
  if (typeof content?.text === "string") return content.text;
  return "";
}

function detectMessageFormat(content: any): "multimodal" | "json" | "markdown" | "text" | "empty" {
  if (hasMultimodalContent(content)) return "multimodal";
  const displayContent = typeof content === "string" ? content : messageContentText(content);
  if (!displayContent) {
    if (content && typeof content === "object") return "json";
    return "empty";
  }
  
  if (typeof displayContent === "string" && (displayContent.trim().startsWith("{") || displayContent.trim().startsWith("["))) {
    try {
      JSON.parse(displayContent);
      return "json";
    } catch {
      // ignore
    }
  }
  
  if (typeof displayContent === "string" && (displayContent.includes("```") || displayContent.includes("**") || displayContent.includes("###") || displayContent.includes("- "))) {
    return "markdown";
  }
  return "text";
}

function MediaBlock({ block }: { block: any }) {
  const url = mediaUrl(block);
  const kind = mediaKind(block);
  if (!url || !kind) return <JsonViewer data={block} />;

  if (kind === "image") {
    return (
      <div className="overflow-hidden rounded-lg border border-[#334155]/70 bg-[#020617]/80">
        <img src={url} alt="message image" className="max-h-[420px] w-full object-contain bg-black/30" />
      </div>
    );
  }

  if (kind === "video") {
    return (
      <div className="overflow-hidden rounded-lg border border-[#334155]/70 bg-[#020617]/80">
        <video controls src={url} className="max-h-[420px] w-full bg-black" />
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-[#334155]/70 bg-[#020617]/80 p-3">
      <audio controls src={url} className="w-full" />
    </div>
  );
}

function FormattedMessageContent({ content }: { content: any }) {
  const blocks = contentBlocks(content);
  const mediaBlocks = blocks.filter(isMediaBlock);
  const textBlocks = blocks.filter((block) => !isMediaBlock(block));
  const displayContent = typeof content === "string"
    ? content
    : textBlocks
        .map((block) => {
          if (typeof block === "string") return block;
          if (block?.type === "text") return block.text || "";
          if (typeof block?.text === "string") return block.text;
          return "";
        })
        .filter(Boolean)
        .join("\n\n");

  if (!displayContent.trim()) {
    if (mediaBlocks.length > 0) {
      return (
        <div className="flex flex-col gap-3">
          {mediaBlocks.map((block, index) => <MediaBlock key={index} block={block} />)}
        </div>
      );
    }
    if (content && typeof content === "object") {
      return <JsonViewer data={content} />;
    }
    return (
      <div className="rounded-md border border-dashed border-slate-700/70 bg-[#020617]/50 px-3 py-2 text-[11px] font-medium text-slate-500">
        No text content.
      </div>
    );
  }

  let isJson = false;
  let parsedJson: any = null;
  try {
    if (typeof displayContent === "string" && (displayContent.trim().startsWith("{") || displayContent.trim().startsWith("["))) {
      parsedJson = JSON.parse(displayContent);
      isJson = true;
    }
  } catch (e) {
    // Not valid JSON, treat as markdown
  }

  if (isJson) {
    return <JsonViewer data={parsedJson} />;
  }

  return (
    <div className="flex flex-col gap-3">
      {mediaBlocks.map((block, index) => <MediaBlock key={index} block={block} />)}
      <div className="prose prose-invert prose-sm max-w-none text-slate-200 leading-relaxed marker:text-slate-400 prose-p:leading-relaxed prose-a:text-[#3b82f6] prose-a:no-underline hover:prose-a:underline w-full max-w-full">
        <ReactMarkdown 
          remarkPlugins={[remarkGfm]}
          components={{
            pre: ({node, ...props}: any) => <pre {...props} className="bg-[#0F172A]/80 border border-white/10 p-3 rounded-md overflow-x-hidden whitespace-pre-wrap break-words break-all w-full max-w-full" />,
            code: ({node, ...props}: any) => <code {...props} className="whitespace-pre-wrap break-words break-all" />
          }}
        >
          {displayContent}
        </ReactMarkdown>
      </div>
    </div>
  );
}

function MessageGroupsView({
  groups,
  onSelect,
  selectedSequence,
}: {
  groups: MessageGroup[];
  onSelect: (event: TraceEvent) => void;
  selectedSequence?: number;
}) {
  return (
    <div className="grid gap-5">
      {groups.map((group) => (
        <div key={group.id} className="relative pl-5">
          {/* Stylized hierarchy line */}
          <div className="absolute left-0 top-2 bottom-0 w-[2px] bg-gradient-to-b from-[#3B82F6] via-[#8B5CF6] to-transparent rounded-full opacity-60" />
          
          <div className="mb-3 flex flex-col gap-1">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <div className="absolute left-[-4px] top-[10px] h-2.5 w-2.5 rounded-full bg-[#3B82F6] shadow-[0_0_10px_rgba(59,130,246,0.8)] ring-2 ring-[#0F172A]" />
                <div className="truncate text-sm font-bold text-slate-200 tracking-wide">{group.label}</div>
              </div>
              {group.messages.length > 0 ? (
                <span className="rounded-full bg-[#1E293B] px-2.5 py-0.5 text-[10px] font-semibold text-slate-300 border border-[#334155] shadow-sm">
                  {group.messages.length} msg
                </span>
              ) : null}
            </div>
            {group.breadcrumb && (
              <div className="truncate text-xs text-slate-500 font-medium pl-4">{group.breadcrumb}</div>
            )}
          </div>
          
          <div className="pl-4 flex flex-col gap-4">
            {group.messages.map((message) => {
              const isUser = message.role === "user";
              const isAssistant = message.role === "assistant";
              const isSystem = message.role === "system";
              const hasToolCalls = Boolean(message.tool_calls?.length);
              const messageText = messageContentText(message.content);
              const formatLabel = hasToolCalls && !messageText.trim()
                ? "tool call"
                : detectMessageFormat(message.content);
              
              return (
                <button
                  key={`${group.id}-${message.sequence}`}
                  onClick={() => onSelect(message.event)}
                  className={cn(
                    "relative block w-full rounded-xl border p-4 text-left transition-all duration-300 ease-out hover:-translate-y-1 hover:shadow-2xl group",
                    isUser
                      ? "bg-gradient-to-br from-[#1e3a8a]/40 to-[#172554]/40 border-[#3b82f6]/30 hover:border-[#3b82f6]/60"
                      : isAssistant
                      ? "bg-gradient-to-br from-[#064e3b]/40 to-[#022c22]/40 border-[#10b981]/30 hover:border-[#10b981]/60"
                      : isSystem
                      ? "bg-gradient-to-br from-[#1e293b]/60 to-[#0f172a]/60 border-[#475569]/40 hover:border-[#64748b]/60"
                      : "bg-gradient-to-br from-[#4c1d95]/40 to-[#2e1065]/40 border-[#8b5cf6]/30 hover:border-[#8b5cf6]/60",
                    selectedSequence === message.sequence && "ring-2 ring-[#22C55E] ring-offset-2 ring-offset-[#020617] shadow-[0_0_20px_rgba(34,197,94,0.2)]",
                  )}
                >
                  <div className="mb-3 flex items-center justify-between gap-2 border-b border-white/10 pb-3">
                    <div className="flex items-center gap-3">
                      <div className={cn(
                        "flex h-7 w-7 items-center justify-center rounded-lg text-sm shadow-lg",
                        isUser ? "bg-[#3b82f6]/20 shadow-[#3b82f6]/10 border border-[#3b82f6]/30" 
                        : isAssistant ? "bg-[#10b981]/20 shadow-[#10b981]/10 border border-[#10b981]/30" 
                        : isSystem ? "bg-[#64748b]/20 shadow-[#64748b]/10 border border-[#64748b]/30" 
                        : "bg-[#8b5cf6]/20 shadow-[#8b5cf6]/10 border border-[#8b5cf6]/30"
                      )}>
                        {isUser ? "👤" : isAssistant ? "🤖" : isSystem ? "⚙️" : "🛠️"}
                      </div>
                      <span className="truncate text-sm font-bold tracking-wide text-white group-hover:text-white/90 transition-colors">
                        {message.sender_name || message.role}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="rounded-md px-2 py-1 text-[9px] font-bold uppercase tracking-widest bg-slate-800/80 text-slate-400 border border-slate-700/50">
                        {formatLabel}
                      </span>
                      <span className={cn(
                        "shrink-0 rounded-md px-2 py-1 text-[10px] font-bold uppercase tracking-wider border",
                        message.streamed 
                          ? "bg-[#10b981]/10 text-[#10b981] border-[#10b981]/20" 
                          : "bg-[#0f172a] text-slate-400 border-slate-700/50"
                      )}>
                        #{message.sequence} {message.streamed ? "stream" : ""}
                      </span>
                    </div>
                  </div>
                  
                  <FormattedMessageContent content={message.content} />
                  
                  {message.tool_calls?.length ? (
                    <div className="mt-4 overflow-hidden rounded-md border border-[#334155]/60 bg-[#020617]/80 shadow-inner">
                      <div className="bg-[#0f172a] px-3 py-2 text-[10px] font-bold uppercase tracking-widest text-slate-400 border-b border-[#334155]/60">
                        Tool Calls
                      </div>
                      <pre className="overflow-auto p-3 text-[11px] text-[#34d399] whitespace-pre-wrap break-words font-mono">
                        {compactJson(message.tool_calls)}
                      </pre>
                    </div>
                  ) : null}
                </button>
              );
            })}
            
            {group.children.length > 0 ? (
              <div className="mt-1">
                <MessageGroupsView groups={group.children} onSelect={onSelect} selectedSequence={selectedSequence} />
              </div>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function TraceDetailPage() {
  const params = useParams();
  const traceId = params.traceId as string;
  const [data, setData] = useState<TraceData | null>(null);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [databaseEvents, setDatabaseEvents] = useState<TraceEvent[]>([]);
  const [checkpoints, setCheckpoints] = useState<CheckpointSummary[]>([]);
  const [databaseCheckpoints, setDatabaseCheckpoints] = useState<CheckpointSummary[]>([]);
  const [selectedEvent, setSelectedEvent] = useState<TraceEvent | null>(null);
  const [filter, setFilter] = useState<(typeof EVENT_FILTERS)[number]>("primary");
  const [search, setSearch] = useState("");
  const [replayOutput, setReplayOutput] = useState<any>(null);
  const [isReplaying, setIsReplaying] = useState(false);
  const [expandedTimeline, setExpandedTimeline] = useState<Record<string, boolean>>({});
  const [timelineMode, setTimelineMode] = useState<TimelineMode>("behavior");
  const [playgroundDefaults, setPlaygroundDefaults] = useState<PlaygroundDefaults | null>(null);
  const [playgroundReplayable, setPlaygroundReplayable] = useState(false);
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [modelId, setModelId] = useState("");
  const [playgroundSchema, setPlaygroundSchema] = useState<{ input: FieldSpec[]; context: FieldSpec[]; config: FieldSpec[] }>({ input: [], context: [], config: [] });
  const [inputFields, setInputFields] = useState<FieldDrafts>({});
  const [contextFields, setContextFields] = useState<FieldDrafts>({});
  const [recursionLimit, setRecursionLimit] = useState("25");
  const [chatName, setChatName] = useState("kagraph_studio_playground");
  const [systemInstructions, setSystemInstructions] = useState("");
  const [replayError, setReplayError] = useState("");
  const [playgroundTraceActive, setPlaygroundTraceActive] = useState(false);
  const [playgroundOpen, setPlaygroundOpen] = useState(false);
  const timelineRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/traces/${traceId}`).then((res) => res.json()).then(setData);
    fetch(`${API_BASE}/api/traces/${traceId}/events`).then((res) => res.json()).then((items) => {
      setEvents(items);
      setDatabaseEvents(items);
      setSelectedEvent(items.at(-1) || null);
    });
    fetch(`${API_BASE}/api/traces/${traceId}/checkpoints`).then((res) => res.json()).then((items) => {
      setCheckpoints(items);
      setDatabaseCheckpoints(items);
    });
    fetch(`${API_BASE}/api/traces/${traceId}/playground`).then((res) => res.json()).then((payload) => {
      const defaults = payload.defaults as PlaygroundDefaults;
      const schema = payload.schema || {};
      setPlaygroundDefaults(defaults);
      setPlaygroundReplayable(Boolean(payload.replayable));
      setPlaygroundSchema({
        input: schema.input || [],
        context: schema.context || [],
        config: schema.config || [],
      });
      setModelOptions(payload.models || []);
      setModelId(defaults.model_id || payload.models?.[0] || "");
      setInputFields(fieldDraftsFromObject(defaults.input || {}, schema.input || []));
      setContextFields(fieldDraftsFromObject(defaults.context || {}, schema.context || []));
      setRecursionLimit(String(defaults.recursion_limit || 25));
      setChatName(defaults.chat_name || "kagraph_studio_playground");
      setSystemInstructions(defaults.system_instructions || "");
    });
  }, [traceId]);

  useEffect(() => {
    const source = new EventSource(`${API_BASE}/api/traces/${traceId}/stream?after=-1`);
    source.addEventListener("trace_event", (message) => {
      const event = JSON.parse((message as MessageEvent).data) as TraceEvent;
      if (playgroundTraceActive) return;
      setEvents((prev) => {
        if (prev.some((item) => item.sequence === event.sequence)) return prev;
        return [...prev, event].sort((a, b) => a.sequence - b.sequence);
      });
      setDatabaseEvents((prev) => {
        if (prev.some((item) => item.sequence === event.sequence)) return prev;
        return [...prev, event].sort((a, b) => a.sequence - b.sequence);
      });
      setSelectedEvent(event);
    });
    return () => source.close();
  }, [traceId, playgroundTraceActive]);

  useEffect(() => {
    timelineRef.current?.scrollTo({ top: timelineRef.current.scrollHeight });
  }, [events.length]);

  const { timelineRows, traceStartMs, totalDurationMs } = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const baseTree = timelineMode === "behavior" ? buildBehaviorTimelineTree(events) : buildRawTimelineTree(events);
    const range = computeTimestamps(baseTree);
    const tree = filterTimelineTree(baseTree, filter, needle);
    const rows = flattenTimeline(tree, expandedTimeline);
    const min = range?.min ?? 0;
    const max = range?.max ?? 0;
    const duration = Math.max(max - min, 1);
    return { timelineRows: rows, traceStartMs: min, totalDurationMs: duration };
  }, [events, filter, search, expandedTimeline, timelineMode]);

  const messageGroups = useMemo(() => buildMessageGroups(buildBehaviorTimelineTree(events)), [events]);

  function toggleTimelineNode(id: string) {
    setExpandedTimeline((prev) => ({ ...prev, [id]: !(prev[id] ?? true) }));
  }

  const graphData = useMemo(() => {
    const staticGraph = data?.trace?.metadata_json?.graph;
    if (!staticGraph) return { nodes: [], edges: [] };

    function getNodeTheme(id: string) {
      const lower = id.toLowerCase();
      if (lower.includes("start") || lower.includes("agent")) {
        return { border: "#A855F7", bg: "rgba(168,85,247,0.1)" }; // Purple
      }
      if (lower.includes("end")) {
        return { border: "#F59E0B", bg: "rgba(245,158,11,0.1)" }; // Orange
      }
      return { border: "#3B82F6", bg: "rgba(59,130,246,0.1)" }; // Blue
    }

    const clusters = new Set<string>();
    const executedNodes = new Set(events.map((event) => event.node || event.name).filter(Boolean));
    const errorNodes = new Set(events.filter((event) => eventKind(event) === "error").map((event) => event.node || event.name));

    const nodes: FlowNode[] = [];
    staticGraph.nodes.forEach((nodeObj: any) => {
      const id = typeof nodeObj === "string" ? nodeObj : nodeObj.id;
      let label = id;
      let parentId: string | undefined = undefined;

      if (id.includes(":")) {
        const parts = id.split(":");
        label = parts.pop()!;
        parentId = parts.join(":");
        if (parentId) clusters.add(parentId);
      }

      const executed = executedNodes.has(id) || executedNodes.has(label) || label === "__start__" || label === "__end__";
      const errored = errorNodes.has(id);
      const theme = getNodeTheme(id);

      nodes.push({
        id,
        type: "custom",
        parentId,
        extent: parentId ? "parent" : undefined,
        zIndex: 1,
        position: { x: 0, y: 0 },
        data: { label },
        style: {
          width: 160,
          minHeight: 48,
          borderRadius: 8,
          border: `1px solid ${errored ? "#EF4444" : theme.border}`,
          background: errored ? "rgba(239,68,68,0.15)" : theme.bg,
          color: "#F8FAFC",
          opacity: executed ? 1 : 0.4,
          fontSize: 14,
          fontWeight: 400,
          padding: 12,
          textAlign: "center" as const,
          boxShadow: errored ? "0 0 15px rgba(239,68,68,0.3)" : executed ? `0 0 15px ${theme.border}30` : "none",
        },
      });
    });

    clusters.forEach((c) => {
      nodes.push({
        id: c,
        type: "cluster",
        zIndex: -1,
        position: { x: 0, y: 0 },
        data: { label: c },
        style: {
          backgroundColor: "rgba(30, 41, 59, 0.5)",
          border: "1px dashed #475569",
          borderRadius: 12,
          zIndex: -1,
        },
      });
    });

    const edges = staticGraph.edges.map((edge: any) => {
      const executed = executedNodes.has(edge.source) && executedNodes.has(edge.target);
      const sourceTheme = getNodeTheme(edge.source);
      const color = executed ? sourceTheme.border : "#334155";
      const isConditional = !!edge.label;

      return {
        id: `${edge.source}-${edge.target}-${edge.label || ""}`,
        source: edge.source,
        target: edge.target,
        label: edge.label || undefined,
        type: "curved",
        animated: executed && isConditional, // only animate conditional edges or maybe not at all? Let's just use dashed.
        style: {
          stroke: color,
          strokeWidth: 1.5,
          strokeDasharray: isConditional ? "4 4" : undefined,
          opacity: executed ? 1 : 0.4,
        },
        labelStyle: { fill: "#E2E8F0", fontWeight: 500, fontSize: 11 },
        labelBgStyle: { fill: "transparent" },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 20,
          height: 20,
          color: color,
        },
      };
    });
    return layoutGraph(nodes, edges);
  }, [data, events]);

  const agentStepCount = useMemo(
    () => events.filter((event) => event.event === "on_step_start").length,
    [events],
  );
  const llmCallCount = useMemo(
    () => events.filter((event) => event.event === "on_chat_model_end").length,
    [events],
  );
  const tokenStats = useMemo(() => {
    const stats = { input: 0, output: 0 };
    let sawEventTokens = false;
    events.forEach((event) => {
      if (event.event !== "on_chat_model_end") return;
      const usage = event.data?.usage || {};
      const inputTokens = Number(usage.input_tokens || 0);
      const outputTokens = Number(usage.output_tokens || 0);
      if (inputTokens || outputTokens) sawEventTokens = true;
      stats.input += inputTokens;
      stats.output += outputTokens;
    });
    if (!sawEventTokens && data?.generations?.length) {
      data.generations.forEach((generation: any) => {
        const usage = generation.metadata_json?.usage || {};
        stats.input += Number(usage.input_tokens ?? generation.usage_input_tokens ?? 0);
        stats.output += Number(usage.output_tokens ?? generation.usage_output_tokens ?? 0);
      });
    }
    return stats;
  }, [events, data]);
  const costStats = useMemo(() => {
    const stats = { input: 0, output: 0, total: 0 };
    let sawSplitCost = false;
    events.forEach((event) => {
      if (event.event !== "on_chat_model_end") return;
      const usage = event.data?.usage || {};
      const inputCost = Number(usage.input_tokens_cost_nanodollars || 0);
      const outputCost = Number(usage.output_tokens_cost_nanodollars || 0);
      const totalCost = Number(usage.total_cost_nanodollars || 0);
      if (inputCost || outputCost) {
        sawSplitCost = true;
        stats.input += inputCost;
        stats.output += outputCost;
      }
      stats.total += totalCost;
    });
    if (!sawSplitCost && data?.generations?.length) {
      data.generations.forEach((generation: any) => {
        const usage = generation.metadata_json?.usage || {};
        stats.input += Number(usage.input_tokens_cost_nanodollars || 0);
        stats.output += Number(usage.output_tokens_cost_nanodollars || 0);
        stats.total += Number(usage.total_cost_nanodollars || 0) || Number(generation.cost_total || 0) * 1_000_000_000;
      });
    }
    if (!stats.total) stats.total = stats.input + stats.output;
    return stats;
  }, [events, data]);
  const nodeRunCount = useMemo(
    () => events.filter((event) => event.event === "on_node_start").length,
    [events],
  );
  const selectedState = selectedEvent?.data?.state || selectedEvent?.data?.checkpoint?.state || null;
  const selectedUpdate = selectedEvent?.data?.update || null;
  const selectedUsage = selectedEvent?.data?.usage || null;
  const selectedMessage = selectedEvent?.data?.message || null;
  const selectedStep = selectedEvent ? eventStep(selectedEvent) : null;
  const selectedEventPayload = selectedEvent
    ? {
        sequence: selectedEvent.sequence,
        event: selectedEvent.event,
        name: selectedEvent.name,
        node: selectedEvent.node,
        checkpoint_id: selectedEvent.checkpoint_id,
        metadata: selectedEvent.metadata || {},
        data: selectedEvent.data || {},
      }
    : null;
  const traceStatus = data?.trace?.status || "LOADING";
  const canReplay = Boolean(data?.trace?.agent_binary || playgroundReplayable);
  const quickInputField = primaryInputField(inputFields, playgroundSchema.input);
  const quickInputValue = inputFields[quickInputField] ?? "";

  async function runReplay() {
    if (!canReplay || isReplaying) return;
    setIsReplaying(true);
    setReplayOutput(null);
    setReplayError("");
    try {
      const input = parseFieldDraftsWithSchema("Input", inputFields, playgroundDefaults?.input || {}, playgroundSchema.input);
      const context = parseFieldDraftsWithSchema("Context", contextFields, playgroundDefaults?.context || {}, playgroundSchema.context);
      const res = await fetch(`${API_BASE}/api/traces/${traceId}/replay`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          input,
          config: {},
          context,
          recursion_limit: Number(recursionLimit) || undefined,
          chat_name: chatName || undefined,
          system_instructions: systemInstructions || undefined,
          model_id: modelId || undefined,
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        setReplayError(body.detail || body.error || "Replay failed.");
      } else {
        setReplayOutput(body);
        if (Array.isArray(body.events) && body.events.length > 0) {
          setPlaygroundTraceActive(true);
          setEvents(body.events);
          setCheckpoints(Array.isArray(body.checkpoints) ? body.checkpoints : []);
          setSelectedEvent(body.events.at(-1) || null);
          setExpandedTimeline({});
        }
      }
    } catch (error: any) {
      setReplayError(error.message || String(error));
    } finally {
      setIsReplaying(false);
    }
  }

  function restoreDatabaseTrace() {
    setPlaygroundTraceActive(false);
    setEvents(databaseEvents);
    setCheckpoints(databaseCheckpoints);
    setSelectedEvent(databaseEvents.at(-1) || null);
    setReplayOutput(null);
    setReplayError("");
    setExpandedTimeline({});
  }

  if (!data) {
    return <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Loading trace...</div>;
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
      <header className="flex items-center justify-between border-b border-border bg-card px-5 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <Link href="/traces" className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground">
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-lg font-semibold">{data.trace.name || "KaGraph trace"}</h1>
              <span className={cn("rounded px-2 py-0.5 text-xs font-medium", traceStatus === "ERROR" ? "bg-destructive/15 text-destructive" : traceStatus === "RUNNING" ? "bg-yellow-500/15 text-yellow-500" : "bg-green-500/15 text-green-500")}>
                {traceStatus}
              </span>
              {playgroundTraceActive ? (
                <span className="rounded bg-primary-accent/15 px-2 py-0.5 text-xs font-medium text-primary-accent">
                  Playground view
                </span>
              ) : null}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-[11px] text-slate-400">
              <div className="flex items-center gap-2">
                <span className="font-mono bg-[#1E293B] text-slate-300 px-1.5 py-0.5 rounded text-[10px] border border-slate-700/50">{data.trace.id}</span>
                <span className="flex items-center gap-1 text-slate-300"><Clock className="h-3.5 w-3.5" /> {format(new Date(data.trace.start_time), "MMM d, HH:mm:ss")}</span>
                <span className="text-slate-500">{traceDurationMinutes(data.trace)}</span>
              </div>

              <div className="h-4 w-px bg-slate-700/50 hidden sm:block"></div>

              <div className="flex items-center gap-3 bg-[#0F172A] px-2.5 py-1 rounded-md border border-[#1E293B]">
                <Bot className="h-3.5 w-3.5 text-blue-400" />
                <span title="LLM Calls" className="text-slate-300"><strong className="text-white font-semibold">{llmCallCount}</strong> calls</span>
                <span className="text-slate-600 text-[9px]">&bull;</span>
                <span title={`Input: ${tokenStats.input} | Output: ${tokenStats.output}`} className="text-slate-300 cursor-help"><strong className="text-white font-semibold">{formatCount(tokenStats.input + tokenStats.output)}</strong> tokens</span>
                <span className="text-slate-600 text-[9px]">&bull;</span>
                <span title={`In: ${formatUsdFromNanodollars(costStats.input)} | Out: ${formatUsdFromNanodollars(costStats.output)}`} className="text-slate-300 cursor-help"><strong className="text-white font-semibold">{formatUsdFromNanodollars(costStats.total)}</strong></span>
              </div>

              <div className="flex items-center gap-3 bg-[#0F172A] px-2.5 py-1 rounded-md border border-[#1E293B]">
                <Activity className="h-3.5 w-3.5 text-emerald-400" />
                <span title="Agent Steps" className="text-slate-300"><strong className="text-white font-semibold">{agentStepCount}</strong> steps</span>
                <span className="text-slate-600 text-[9px]">&bull;</span>
                <span title="Node Runs" className="text-slate-300"><strong className="text-white font-semibold">{nodeRunCount}</strong> nodes</span>
                <span className="text-slate-600 text-[9px]">&bull;</span>
                <span title="Events" className="text-slate-300"><strong className="text-white font-semibold">{events.length}</strong> events{playgroundTraceActive ? " (mem)" : ""}</span>
                <span className="text-slate-600 text-[9px]">&bull;</span>
                <span title="Checkpoints" className="text-slate-300"><strong className="text-white font-semibold">{checkpoints.length}</strong> chkpts</span>
              </div>
            </div>
          </div>
        </div>
        {playgroundTraceActive ? (
          <button onClick={restoreDatabaseTrace} className="shrink-0 rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground">
            Restore DB trace
          </button>
        ) : null}
      </header>
      <main className="flex flex-col min-h-0 flex-1 overflow-hidden bg-[#020617] relative">
        {/* Top Area: Gantt Timeline */}
        <section className="flex-shrink-0 flex flex-col bg-[#0F172A] border-b border-[#1E293B] h-[35vh]">
          <div className="flex items-center justify-between px-4 py-2 border-b border-[#1E293B]">
            <div className="flex items-center gap-2 text-sm font-bold text-white tracking-wide"><Terminal className="h-4 w-4 text-[#22C55E]" /> Gantt Timeline</div>
            <div className="flex items-center gap-4">
              <div className="relative w-[200px]">
                <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
                <input value={search} onChange={(e) => setSearch(e.target.value)} className="w-full rounded border border-[#1E293B] bg-[#020617] py-1 pl-7 pr-2 text-[10px] text-white outline-none focus:ring-1 focus:ring-[#22C55E] placeholder:text-slate-600" placeholder="Search events..." />
              </div>
              <div className="flex rounded bg-[#020617] p-0.5 border border-[#1E293B]">
                {(["behavior", "raw"] as TimelineMode[]).map((mode) => (
                  <button key={mode} onClick={() => setTimelineMode(mode)} className={cn("rounded px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider text-slate-400 hover:text-white transition-colors", timelineMode === mode && "bg-[#1E293B] text-white shadow-sm")}>
                    {mode === "behavior" ? "Behavior" : "Raw"}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="flex-1 overflow-auto bg-[#0F172A] relative scrollbar-thin scrollbar-thumb-[#1E293B] overflow-x-hidden">
            <div className="w-full flex flex-col">
              {timelineRows.map(row => {
                const event = row.event;
                const isExpanded = expandedTimeline[row.id] ?? true;
                const isSelected = event && selectedEvent?.sequence === event.sequence;
                const startPercent = ((row.startTimeMs ?? traceStartMs) - traceStartMs) / totalDurationMs * 100;
                const widthPercent = Math.max(0.2, ((row.endTimeMs ?? row.startTimeMs ?? traceStartMs) - (row.startTimeMs ?? traceStartMs)) / totalDurationMs * 100);

                return (
                  <div key={row.id} className={cn("flex h-[32px] border-b border-[#1E293B]/50 transition-colors", isSelected ? "bg-[#1E293B]/60" : "hover:bg-[#1E293B]/30")}>
                    {/* Y-Axis: Hierarchy */}
                    <div className="sticky left-0 z-10 w-[300px] shrink-0 bg-[#020617] border-r border-[#1E293B] px-2 flex items-center gap-1.5" style={{ paddingLeft: `${Math.max(0.5, row.depth * 1)}rem` }}>
                      <button
                        onClick={(click) => {
                          if (row.children.length > 0) {
                            click.stopPropagation();
                            toggleTimelineNode(row.id);
                          } else if (event) {
                            setSelectedEvent(event);
                          }
                        }}
                        className="flex items-center gap-1.5 min-w-0 w-full text-left"
                      >
                        <span className="flex h-3.5 w-3.5 shrink-0 items-center justify-center text-slate-500 hover:text-white">
                          {row.children.length > 0 ? (isExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />) : null}
                        </span>
                        <span className={cn("truncate font-bold text-[11px]", isSelected ? "text-[#22C55E]" : "text-slate-300")} title={row.label}>{row.label}</span>
                        {event ? <span className="ml-auto font-mono text-slate-500 text-[9px] shrink-0">#{event.sequence}</span> : null}
                      </button>
                    </div>

                    {/* X-Axis: Gantt Bar */}
                    <div className="flex-1 min-w-0 relative border-l border-[#1E293B] mr-4">
                      <div
                        className={cn("absolute top-1/2 -translate-y-1/2 h-[18px] rounded cursor-pointer border flex items-center overflow-hidden px-1.5", getGanttColor(row.kind), isSelected && "ring-1 ring-white/50")}
                        style={{ left: `${startPercent}%`, width: `${widthPercent}%`, minWidth: '12px' }}
                        onClick={() => event && setSelectedEvent(event)}
                        title={`${row.label} - ${row.sublabel || ''}`}
                      >
                        <span className="truncate text-[10px] font-semibold text-white drop-shadow-md pointer-events-none">{widthPercent > 5 ? row.sublabel || row.label : ""}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </section>

        {/* Middle Area: 3 Equal Columns */}
        <div className="flex-1 flex min-h-0 overflow-hidden divide-x divide-[#1E293B]">
          {/* Column 1: Graph & Floating Playground */}
          <section className="flex-[1] flex flex-col shrink-0 relative bg-[#020617] min-w-0">
            <div className="flex items-center gap-2 px-4 py-2 border-b border-[#1E293B] text-xs font-bold text-white tracking-wide bg-[#0F172A]"><RefreshCcw className="h-3.5 w-3.5 text-[#22C55E]" /> Graph Viewer</div>
            <div className="flex-1 relative min-h-0">
              <ReactFlow nodes={graphData.nodes} edges={graphData.edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} fitView attributionPosition="bottom-right" colorMode="dark">
                <Background color="#1E293B" gap={18} />
              </ReactFlow>

              {/* Floating Chat Playground */}
              <div className="absolute bottom-4 left-4 right-4 z-50 transition-all duration-300">
                <div className="rounded-xl border border-[#1E293B] bg-[#0F172A]/95 backdrop-blur-xl shadow-2xl shadow-black/80 overflow-hidden flex flex-col">
                  {/* Expanded Config */}
                  {playgroundOpen && (
                    <div className="border-b border-[#1E293B] p-4 flex flex-col gap-3 bg-[#020617]/90 max-h-[40vh] overflow-y-auto scrollbar-thin scrollbar-thumb-[#1E293B]">
                      <div className="flex items-center justify-between">
                        <div className="text-xs font-bold text-white tracking-wide flex items-center gap-2"><Wrench className="w-3.5 h-3.5 text-slate-400" /> Advanced Settings</div>
                        <span className={cn("rounded px-2.5 py-1 text-[9px] font-bold tracking-wider uppercase", canReplay ? "bg-[#22C55E]/10 text-[#22C55E] border border-[#22C55E]/20" : "bg-[#1E293B] text-slate-400 border border-[#334155]")}>{canReplay ? "enabled" : "disabled"}</span>
                      </div>

                      <div className="grid grid-cols-[1fr_90px] gap-2">
                        <label className="grid gap-1.5 text-[10px] text-slate-400 font-bold uppercase tracking-wide">
                          Chat name
                          <input value={chatName} onChange={(e) => setChatName(e.target.value)} disabled={!canReplay} className="rounded-md border border-[#1E293B] bg-[#0F172A] px-3 py-2 text-xs text-white outline-none focus:ring-1 focus:ring-[#22C55E] disabled:opacity-50" />
                        </label>
                        <label className="grid gap-1.5 text-[10px] text-slate-400 font-bold uppercase tracking-wide">
                          Limit
                          <input value={recursionLimit} onChange={(e) => setRecursionLimit(e.target.value)} disabled={!canReplay} className="rounded-md border border-[#1E293B] bg-[#0F172A] px-3 py-2 text-xs text-white outline-none focus:ring-1 focus:ring-[#22C55E] disabled:opacity-50 font-mono" />
                        </label>
                      </div>

                      <div className="grid grid-cols-2 gap-3">
                        <EditableFields title="Graph input" fields={inputFields} onChange={setInputFields} shape={playgroundDefaults?.input || {}} schema={playgroundSchema.input} disabled={!canReplay} />
                        <EditableFields title="Context" fields={contextFields} onChange={setContextFields} shape={playgroundDefaults?.context || {}} schema={playgroundSchema.context} disabled={!canReplay} />
                      </div>

                      <label className="grid gap-1.5 text-[10px] text-slate-400 font-bold uppercase tracking-wide">
                        System instructions
                        <textarea value={systemInstructions} onChange={(e) => setSystemInstructions(e.target.value)} disabled={!canReplay} className="min-h-16 rounded-md border border-[#1E293B] bg-[#0F172A] p-3 text-xs text-white outline-none focus:ring-1 focus:ring-[#22C55E] disabled:opacity-50" />
                      </label>

                      {(replayOutput || replayError) && (
                        <pre className="max-h-40 overflow-auto break-words whitespace-pre-wrap rounded-md border border-[#1E293B] bg-[#0F172A] p-3 text-[10px] text-slate-300 font-mono scrollbar-thin scrollbar-thumb-[#1E293B]">
                          {replayError || compactJson(replayOutput)}
                        </pre>
                      )}
                    </div>
                  )}

                  {/* Chat Bar */}
                  <div className="flex items-center gap-2 p-2 bg-[#0F172A]/80">
                    <button onClick={() => setPlaygroundOpen(!playgroundOpen)} className={cn("p-1.5 rounded-md transition-colors", playgroundOpen ? "bg-[#1E293B] text-white" : "text-slate-400 hover:text-white hover:bg-[#1E293B]/50")}>
                      <Wrench className="w-4 h-4" />
                    </button>
                    <select value={modelId} onChange={(e) => setModelId(e.target.value)} disabled={!canReplay} className="shrink-0 w-[120px] rounded border border-[#1E293B] bg-[#020617] px-2 py-1.5 text-[11px] text-white font-medium outline-none focus:ring-1 focus:ring-[#22C55E] disabled:opacity-50">
                      <option value="">Captured model</option>
                      {modelOptions.map((model) => <option key={model} value={model}>{model.replace("google/", "").replace("gemini-1.5-", "").replace("-preview", "")}</option>)}
                    </select>
                    <span title="Graph input field" className="shrink-0 max-w-[96px] truncate rounded border border-[#1E293B] bg-[#020617] px-2 py-1.5 font-mono text-[10px] font-semibold text-slate-300">
                      {quickInputField}
                    </span>
                    <input
                      placeholder={`Replay ${quickInputField}...`}
                      className="flex-1 rounded border border-[#1E293B] bg-[#020617] px-3 py-1.5 text-[11px] text-white outline-none focus:ring-1 focus:ring-[#22C55E] placeholder:text-slate-500 min-w-0"
                      value={quickInputValue}
                      onChange={(e) => {
                        const val = e.target.value;
                        setInputFields((prev) => ({ ...prev, [quickInputField]: val }));
                      }}
                      disabled={!canReplay}
                      onKeyDown={(e) => { if (e.key === 'Enter' && canReplay && !isReplaying) runReplay(); }}
                    />
                    <button
                      onClick={runReplay}
                      disabled={!canReplay || isReplaying}
                      aria-busy={isReplaying}
                      className={cn(
                        "shrink-0 rounded px-3 py-1.5 text-[11px] font-bold transition-colors flex items-center gap-1.5",
                        canReplay && !isReplaying
                          ? "bg-[#22C55E] text-[#020617] hover:bg-[#16a34a] shadow-lg shadow-[#22C55E]/20"
                          : "bg-[#1E293B] text-slate-500 shadow-none cursor-not-allowed",
                        isReplaying && "text-slate-300",
                      )}
                    >
                      {isReplaying ? <RefreshCcw className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" fill="currentColor" />}
                      {isReplaying ? "Running" : "Run"}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </section>

          {/* Column 2: Messages */}
          <section className="flex-[1] flex flex-col min-w-0 bg-[#0F172A]">
            <div className="flex items-center gap-2 px-4 py-2 border-b border-[#1E293B] text-xs font-bold text-white tracking-wide bg-[#020617]"><MessageSquare className="h-3.5 w-3.5 text-[#22C55E]" /> Messages</div>
            <div className="min-h-0 flex-1 overflow-auto p-4 scrollbar-thin scrollbar-thumb-[#1E293B]">
              {messageGroups.length === 0 ? (
                <div className="text-xs text-slate-500 font-medium">No messages captured yet.</div>
              ) : (
                <MessageGroupsView groups={messageGroups} onSelect={setSelectedEvent} selectedSequence={selectedEvent?.sequence} />
              )}
            </div>
          </section>

          {/* Column 3: Event Inspector */}
          <section className="flex-[1] flex flex-col min-w-0 bg-[#0F172A]">
            <div className="flex items-center gap-2 px-4 py-2 border-b border-[#1E293B] text-xs font-bold text-white tracking-wide bg-[#020617]"><Braces className="h-3.5 w-3.5 text-[#22C55E]" /> Event Inspector</div>
            <div className="min-h-0 flex-1 overflow-auto p-4 scrollbar-thin scrollbar-thumb-[#1E293B]">
              {!selectedEvent ? (
                <div className="rounded-xl border border-[#1E293B] bg-[#020617] p-4 text-xs text-slate-400 font-medium text-center">Select a timeline row to inspect the event.</div>
              ) : (
                <div className="grid gap-2 min-w-0">
                  <div className="rounded-xl border border-[#1E293B] bg-[#020617] p-3">
                    <div className="mb-3 flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate text-xs font-bold text-white">{selectedEvent.event}</div>
                        <div className="mt-1 text-[10px] text-slate-400 font-medium">
                          {selectedEvent.node || selectedEvent.name || "graph"} {selectedStep !== null ? `Â· step ${selectedStep}` : ""}
                        </div>
                      </div>
                      <span className="shrink-0 rounded-md bg-[#1E293B] px-1.5 py-0.5 font-mono text-[9px] font-bold text-slate-300">#{selectedEvent.sequence}</span>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-[10px]">
                      <div className="rounded border border-[#1E293B] bg-[#0F172A] p-2">
                        <div className="text-slate-500 font-semibold mb-1">Kind</div>
                        <div className="font-bold text-white capitalize">{eventKind(selectedEvent)}</div>
                      </div>
                      <div className="rounded border border-[#1E293B] bg-[#0F172A] p-2">
                        <div className="text-slate-500 font-semibold mb-1">Checkpoint</div>
                        <div className="truncate font-mono text-white">{selectedEvent.checkpoint_id || "none"}</div>
                      </div>
                    </div>
                  </div>

                  {selectedUsage ? (
                    <div className="rounded-xl border border-[#1E293B] bg-[#020617] p-3">
                      <div className="mb-2 text-[10px] font-bold text-white uppercase tracking-wide">Model usage</div>
                      <div className="grid grid-cols-2 gap-2 text-[10px]">
                        <div className="rounded border border-[#1E293B] bg-[#0F172A] p-2">
                          <div className="text-slate-500 font-semibold mb-1">Input tokens</div>
                          <div className="font-mono text-white">{selectedUsage.input_tokens ?? 0}</div>
                        </div>
                        <div className="rounded border border-[#1E293B] bg-[#0F172A] p-2">
                          <div className="text-slate-500 font-semibold mb-1">Output tokens</div>
                          <div className="font-mono text-white">{selectedUsage.output_tokens ?? 0}</div>
                        </div>
                        <div className="rounded border border-[#1E293B] bg-[#0F172A] p-2">
                          <div className="text-slate-500 font-semibold mb-1">Input cost</div>
                          <div className="font-mono text-white">{formatUsdFromNanodollars(Number(selectedUsage.input_tokens_cost_nanodollars || 0))}</div>
                        </div>
                        <div className="rounded border border-[#1E293B] bg-[#0F172A] p-2">
                          <div className="text-slate-500 font-semibold mb-1">Output cost</div>
                          <div className="font-mono text-white">{formatUsdFromNanodollars(Number(selectedUsage.output_tokens_cost_nanodollars || 0))}</div>
                        </div>
                      </div>
                    </div>
                  ) : null}

                  {selectedMessage ? (
                    <div className="rounded-xl border border-[#1E293B] bg-[#020617] p-3">
                      <div className="mb-2 text-[10px] font-bold text-white uppercase tracking-wide">Message</div>
                      <FormattedMessageContent content={selectedMessage.content ?? selectedMessage.text ?? ""} />
                      {Array.isArray(selectedMessage.tool_calls) && selectedMessage.tool_calls.length > 0 ? (
                        <div className="mt-3 overflow-hidden rounded-md border border-[#334155]/60 bg-[#020617]/80">
                          <div className="bg-[#0f172a] px-3 py-2 text-[10px] font-bold uppercase tracking-widest text-slate-400 border-b border-[#334155]/60">
                            Tool calls
                          </div>
                          <pre className="max-h-40 overflow-auto p-3 text-[10px] text-[#34d399] whitespace-pre-wrap break-words font-mono">
                            {compactJson(selectedMessage.tool_calls)}
                          </pre>
                        </div>
                      ) : null}
                      {selectedMessage.metadata && Object.keys(selectedMessage.metadata).length > 0 ? (
                        <details className="mt-3 overflow-hidden rounded-md border border-[#334155]/60 bg-[#020617]/80">
                          <summary className="cursor-pointer bg-[#0f172a] px-3 py-2 text-[10px] font-bold uppercase tracking-widest text-slate-400 border-b border-[#334155]/60">
                            Metadata
                          </summary>
                          <pre className="max-h-40 overflow-auto p-3 text-[10px] text-slate-400 whitespace-pre-wrap break-words font-mono">
                            {compactJson(selectedMessage.metadata)}
                          </pre>
                        </details>
                      ) : null}
                    </div>
                  ) : null}

                  <div className="rounded-xl border border-[#1E293B] bg-[#020617] p-3 min-w-0">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <div className="text-[10px] font-bold text-white uppercase tracking-wide">Node writes</div>
                      {selectedUpdate && typeof selectedUpdate === "object" ? <span className="rounded bg-[#22C55E]/10 px-1.5 py-0.5 text-[9px] font-bold text-[#22C55E] border border-[#22C55E]/20">{Object.keys(selectedUpdate).length} fields</span> : null}
                    </div>
                    <pre className="max-h-40 overflow-auto break-words whitespace-pre-wrap rounded border border-[#1E293B] bg-[#0F172A] p-2 text-[10px] text-slate-300 font-mono scrollbar-thin scrollbar-thumb-[#1E293B] leading-relaxed">{selectedUpdate ? compactJson(selectedUpdate) : "This event did not write state."}</pre>
                  </div>

                  <div className="rounded-xl border border-[#1E293B] bg-[#020617] p-3 min-w-0">
                    <div className="mb-2 text-[10px] font-bold text-white uppercase tracking-wide">State snapshot</div>
                    <pre className="max-h-48 overflow-auto break-words whitespace-pre-wrap rounded border border-[#1E293B] bg-[#0F172A] p-2 text-[10px] text-slate-300 font-mono scrollbar-thin scrollbar-thumb-[#1E293B] leading-relaxed">{selectedState ? compactJson(selectedState) : "No state snapshot was attached to this event."}</pre>
                  </div>

                  <details className="rounded-xl border border-[#1E293B] bg-[#020617] overflow-hidden group min-w-0">
                    <summary className="cursor-pointer px-3 py-2.5 text-[10px] font-bold text-slate-400 uppercase tracking-wide hover:bg-[#1E293B]/50 transition-colors">Raw event payload</summary>
                    <pre className="max-h-60 overflow-auto break-words whitespace-pre-wrap border-t border-[#1E293B] bg-[#0F172A] p-3 text-[10px] leading-relaxed font-mono text-slate-400 scrollbar-thin scrollbar-thumb-[#1E293B]">{selectedEventPayload ? compactJson(selectedEventPayload) : ""}</pre>
                  </details>
                </div>
              )}
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
