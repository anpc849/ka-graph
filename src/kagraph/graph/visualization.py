from __future__ import annotations

import io
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kagraph.constants import END, START


@dataclass(frozen=True)
class GraphNode:
    id: str
    name: str | None = None
    metadata: dict | None = None


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    label: str | None = None
    conditional: bool = False


class _VertexViewer:
    height = 3

    def __init__(self, name: str) -> None:
        self.name = name
        self.h = self.height
        self.w = len(name) + 2
        self.xy: tuple[float, float] = (0.0, 0.0)


class _AsciiCanvas:
    def __init__(self, cols: int, lines: int) -> None:
        self.cols = max(cols, 2)
        self.lines = max(lines, 2)
        self.canvas = [[" "] * self.cols for _ in range(self.lines)]

    def draw(self) -> str:
        return "\n".join("".join(row).rstrip() for row in self.canvas).rstrip()

    def point(self, x: int, y: int, char: str) -> None:
        if 0 <= x < self.cols and 0 <= y < self.lines:
            self.canvas[y][x] = char

    def line(self, x0: int, y0: int, x1: int, y1: int, char: str) -> None:
        dx = x1 - x0
        dy = y1 - y0
        steps = max(abs(dx), abs(dy), 1)
        for step in range(steps + 1):
            x = round(x0 + dx * step / steps)
            y = round(y0 + dy * step / steps)
            self.point(x, y, char)

    def text(self, x: int, y: int, text: str) -> None:
        for index, char in enumerate(text):
            self.point(x + index, y, char)

    def box(self, x0: int, y0: int, width: int, height: int) -> None:
        width -= 1
        height -= 1
        for x in range(x0, x0 + width):
            self.point(x, y0, "-")
            self.point(x, y0 + height, "-")
        for y in range(y0, y0 + height):
            self.point(x0, y, "|")
            self.point(x0 + width, y, "|")
        self.point(x0, y0, "+")
        self.point(x0 + width, y0, "+")
        self.point(x0, y0 + height, "+")
        self.point(x0 + width, y0 + height, "+")


class KaGraphView:
    """Drawable view of a compiled KaGraph."""

    def __init__(self, nodes: Iterable[Any], edges: Iterable[GraphEdge]) -> None:
        self.nodes = tuple(
            n if isinstance(n, GraphNode) else GraphNode(
                id=n,
                name=n.split(":")[-1] if ":" in n else n,
                metadata={"cluster": n.rsplit(":", 1)[0]} if ":" in n else None
            )
            for n in nodes
        )
        self.edges = tuple(edges)

    def print_ascii(self) -> None:
        """Prints a terminal-friendly graph representation."""

        text = self.draw_ascii()
        print(text)
        return text

    def draw_ascii(self) -> str:
        """Returns a LangGraph-style ASCII rendering."""

        vertices = {node.id: node for node in self.nodes}
        layout = _build_sugiyama_layout(vertices, self.edges)
        min_x, min_y, max_x, max_y = _layout_bounds(layout)
        canvas = _AsciiCanvas(
            cols=int(math.ceil(max_x) - math.floor(min_x)) + 1,
            lines=max(2, int(math.ceil(max_y) - math.floor(min_y)) + 1),
        )

        # Match LangGraph: edges first, boxes afterward so nodes mask line
        # segments that pass through their area.
        for edge in layout.g.sE:
            points = edge.view._pts
            for index in range(1, len(points)):
                start = points[index - 1]
                end = points[index]
                canvas.line(
                    int(round(start[0] - min_x)),
                    int(round(start[1] - min_y)),
                    int(round(end[0] - min_x)),
                    int(round(end[1] - min_y)),
                    "." if edge.data.conditional else "*",
                )

        for vertex in layout.g.sV:
            x = vertex.view.xy[0] - vertex.view.w / 2.0
            y = vertex.view.xy[1] - vertex.view.h / 2.0
            canvas.box(
                int(round(x - min_x)),
                int(round(y - min_y)),
                vertex.view.w,
                vertex.view.h,
            )
            canvas.text(
                int(round(x - min_x)) + 1,
                int(round(y - min_y)) + 1,
                vertex.data.name or vertex.data.id,
            )

        return canvas.draw()

    def draw_png(
        self,
        path: str | Path | None = None,
        *,
        return_bytes: bool = False,
    ):
        """Renders the graph as PNG.

        In notebooks this returns an ``IPython.display.Image`` when IPython is
        available. Pass ``return_bytes=True`` to get raw PNG bytes instead.
        """

        png = self._render_png_bytes()
        if path is not None:
            Path(path).write_bytes(png)
        if return_bytes:
            return png

        try:
            from IPython.display import Image
        except ImportError:
            return png
        return Image(data=png)

    def to_json(self) -> dict:
        return {
            "nodes": [{"id": node.id} for node in self.nodes],
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    **({"label": edge.label} if edge.label else {}),
                    **({"conditional": True} if edge.conditional else {}),
                }
                for edge in self.edges
            ],
        }

    def _render_png_bytes(self) -> bytes:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as error:
            raise ImportError(
                "draw_png() requires Pillow. Install it with `pip install pillow` "
                "or `pip install kagraph[viz]`."
            ) from error

        vertices = {node.id: node for node in self.nodes}
        layout = _build_sugiyama_layout(vertices, self.edges)
        min_x, min_y, max_x, max_y = _layout_bounds(layout)

        ss = 3
        scale_x = 14 * ss
        scale_y = 20 * ss
        padding = 40 * ss

        width = max(260 * ss, int(math.ceil(max_x - min_x) * scale_x) + padding * 2)
        height = max(180 * ss, int(math.ceil(max_y - min_y) * scale_y) + padding * 2)

        image = Image.new("RGBA", (width, height), (255, 255, 255, 255))
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("arial.ttf", 16 * ss)
        except Exception:
            try:
                font = ImageFont.truetype("DejaVuSans.ttf", 16 * ss)
            except Exception:
                font = ImageFont.load_default()

        def project(point: tuple[float, float]) -> tuple[int, int]:
            return (
                int(round((point[0] - min_x) * scale_x)) + padding,
                int(round((point[1] - min_y) * scale_y)) + padding,
            )

        edge_pairs = set()
        for edge in layout.g.sE:
            edge_pairs.add((edge.v[0].data.id, edge.v[1].data.id))

        # Endpoint Spacing (similar to Frontend)
        top_attachments = {}
        bottom_attachments = {}
        for edge in layout.g.sE:
            u, v = edge.v[0].data.id, edge.v[1].data.id
            sy, ty = edge.v[0].view.xy[1], edge.v[1].view.xy[1]
            if sy >= ty: # Backward
                top_attachments.setdefault(u, []).append((edge, True))
                bottom_attachments.setdefault(v, []).append((edge, False))
            else: # Forward
                bottom_attachments.setdefault(u, []).append((edge, True))
                top_attachments.setdefault(v, []).append((edge, False))
                
        edge_offsets = {}
        POINT_SPREAD = 18 * ss
        def apply_spacing(attachments):
            for node, group in attachments.items():
                if len(group) < 2:
                    if group:
                        edge_offsets[(group[0][0], group[0][1])] = 0.0
                    continue
                def sort_key(item):
                    e, is_source = item
                    other_v = e.v[1] if is_source else e.v[0]
                    # Sort by X, then by names to consistently untangle bidirectional edges
                    return (other_v.view.xy[0], e.v[0].data.id, e.v[1].data.id)
                group.sort(key=sort_key)
                for i, (e, is_source) in enumerate(group):
                    edge_offsets[(e, is_source)] = (i - (len(group) - 1) / 2.0) * POINT_SPREAD
        
        apply_spacing(top_attachments)
        apply_spacing(bottom_attachments)

        edge_paths = []
        for edge in layout.g.sE:
            u, v = edge.v[0].data.id, edge.v[1].data.id
            if u == v:
                points = [project(point) for point in _self_loop_points(edge.v[0].view)]
                points = _smooth_polyline(points)
            else:
                s_view, t_view = edge.v[0].view, edge.v[1].view
                is_backward = s_view.xy[1] >= t_view.xy[1]
                
                s_rect = _vertex_rect(s_view)
                t_rect = _vertex_rect(t_view)
                
                s_port_proj = project(((s_rect[0] + s_rect[2]) / 2.0, s_rect[1] if is_backward else s_rect[3]))
                t_port_proj = project(((t_rect[0] + t_rect[2]) / 2.0, t_rect[3] if is_backward else t_rect[1]))
                
                s_port = (s_port_proj[0] + edge_offsets.get((edge, True), 0.0), s_port_proj[1])
                t_port = (t_port_proj[0] + edge_offsets.get((edge, False), 0.0), t_port_proj[1])
                
                points = [project(p) for p in edge.view._pts]
                if len(points) == 2:
                    points = [s_port, t_port]
                else:
                    points[0] = s_port
                    points[-1] = t_port
                
                if u != v and (v, u) in edge_pairs and len(points) == 2:
                    points = _get_curved_polyline(points[0], points[-1], offset=25 * ss)
                elif len(points) > 2:
                    points = _smooth_polyline(points)
            edge_paths.append((edge, points))

        edge_color = (100, 116, 139, 255) # slate-500
        cond_edge_color = (148, 163, 184, 255) # slate-400


        clusters = {}
        for vertex in layout.g.sV:
            cluster_name = vertex.data.metadata.get("cluster") if vertex.data.metadata else None
            if cluster_name:
                clusters.setdefault(cluster_name, []).append(vertex)
                
        for cluster_name, v_list in clusters.items():
            c_min_x = min(v.view.xy[0] - v.view.w / 2.0 for v in v_list)
            c_min_y = min(v.view.xy[1] - v.view.h / 2.0 for v in v_list)
            c_max_x = max(v.view.xy[0] + v.view.w / 2.0 for v in v_list)
            c_max_y = max(v.view.xy[1] + v.view.h / 2.0 for v in v_list)
            
            c_pad_x = 10 * ss
            c_pad_y = 10 * ss
            
            c_left, c_top = project((c_min_x, c_min_y))
            c_right, c_bottom = project((c_max_x, c_max_y))
            
            # Use fixed padding in projected coords
            c_left -= c_pad_x
            c_top -= c_pad_y + 24 * ss
            c_right += c_pad_x
            c_bottom += c_pad_y
            
            draw.rectangle(
                (c_left, c_top, c_right, c_bottom),
                fill=(253, 251, 221, 255),  # light yellow
                outline=(212, 212, 170, 255),
                width=2 * ss
            )
            # Draw cluster label
            _draw_centered_text(
                draw, 
                (c_left, c_top, c_right, c_top + 24 * ss), 
                cluster_name, 
                font, 
                (71, 85, 105, 255)
            )

        for edge, points in edge_paths:
            color = cond_edge_color if edge.data.conditional else edge_color
            if edge.data.conditional:
                _draw_dashed_polyline(draw, points, fill=color, width=2 * ss, dash=6 * ss, gap=4 * ss)
            else:
                draw.line(points, fill=color, width=2 * ss, joint="curve")

        for vertex in layout.g.sV:
            _draw_png_node(draw, vertex, project, font, ss)

        for edge, points in edge_paths:
            color = cond_edge_color if edge.data.conditional else edge_color
            if len(points) >= 2:
                _draw_arrowhead(draw, points[-2], points[-1], color, ss)

            if edge.data.label:
                total_len = sum(math.hypot(points[i+1][0]-points[i][0], points[i+1][1]-points[i][1]) for i in range(len(points)-1))
                mid_len = total_len / 2
                cur_len = 0
                mx, my = points[0]
                for i in range(len(points)-1):
                    d = math.hypot(points[i+1][0]-points[i][0], points[i+1][1]-points[i][1])
                    if cur_len + d >= mid_len:
                        if d > 0:
                            t = (mid_len - cur_len) / d
                            mx = points[i][0] + t*(points[i+1][0]-points[i][0])
                            my = points[i][1] + t*(points[i+1][1]-points[i][1])
                        break
                    cur_len += d
                text = edge.data.label
                bbox = draw.textbbox((0, 0), text, font=font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                pad_x = 4 * ss
                pad_y = 2 * ss
                draw.rounded_rectangle(
                    (mx - tw/2 - pad_x, my - th/2 - pad_y, mx + tw/2 + pad_x, my + th/2 + pad_y),
                    radius=2 * ss,
                    fill=(241, 245, 249, 255),
                )
                draw.text((mx - tw/2, my - th/2), text, fill=(71, 85, 105, 255), font=font)

        final_image = image.resize((width // ss, height // ss), Image.Resampling.LANCZOS)
        rgb_image = Image.new("RGB", final_image.size, (255, 255, 255))
        rgb_image.paste(final_image, mask=final_image.split()[3])

        buffer = io.BytesIO()
        rgb_image.save(buffer, format="PNG")
        return buffer.getvalue()


def _build_sugiyama_layout(
    vertices: Mapping[str, str],
    edges: Sequence[GraphEdge],
) -> Any:
    try:
        from grandalf.graphs import Edge, Graph, Vertex
        from grandalf.layouts import SugiyamaLayout
        from grandalf.routing import EdgeViewer, route_with_lines
    except ImportError as error:
        raise ImportError(
            "Graph visualization requires grandalf. Install it with "
            "`pip install grandalf` or `pip install kagraph[viz]`."
        ) from error

    vertices_ = {node_id: Vertex(node) for node_id, node in vertices.items()}
    edges_ = [
        Edge(vertices_[edge.source], vertices_[edge.target], data=edge)
        for edge in edges
    ]
    graph = Graph(vertices_.values(), edges_).C[0]

    for vertex in graph.sV:
        vertex.view = _VertexViewer(vertex.data.name or vertex.data.id)
    for edge in graph.sE:
        edge.view = EdgeViewer()

    roots = [vertex for vertex in graph.sV if not vertex.e_in()]
    layout = SugiyamaLayout(graph)
    layout.init_all(roots=roots, optimize=True)
    layout.yspace = _VertexViewer.height * 0.7
    layout.xspace = min(vertex.view.w for vertex in graph.sV)
    def safe_route_with_lines(edge, points):
        try:
            route_with_lines(edge, points)
        except ValueError:
            # Grandalf can fail routing self-loops or tightly packed branch
            # edges when the next routing point is inside the node box. Keep
            # the center-to-center path; KaGraph trims visible endpoints later.
            return

    layout.route_edge = safe_route_with_lines
    layout.draw()
    return layout


def _layout_bounds(layout: Any) -> tuple[float, float, float, float]:
    x_values = []
    y_values = []
    for vertex in layout.g.sV:
        x_values.append(vertex.view.xy[0] - vertex.view.w / 2.0)
        x_values.append(vertex.view.xy[0] + vertex.view.w / 2.0)
        y_values.append(vertex.view.xy[1] - vertex.view.h / 2.0)
        y_values.append(vertex.view.xy[1] + vertex.view.h / 2.0)
    for edge in layout.g.sE:
        for x, y in edge.view._pts:
            x_values.append(x)
            y_values.append(y)
    return min(x_values), min(y_values), max(x_values), max(y_values)


def _visible_edge_points(edge: Any) -> list[tuple[float, float]]:
    points = list(edge.view._pts)
    if len(points) < 2:
        return points

    source = edge.v[0].view
    target = edge.v[1].view
    source_rect = _vertex_rect(source)
    target_rect = _vertex_rect(target)

    if _point_in_rect(points[0], source_rect):
        points[0] = _segment_rect_intersection(points[0], points[1], source_rect)
    if _point_in_rect(points[-1], target_rect):
        points[-1] = _segment_rect_intersection(points[-2], points[-1], target_rect)
    return points


def _self_loop_points(view: Any) -> list[tuple[float, float]]:
    x, y = view.xy
    half_w = view.w / 2.0
    half_h = view.h / 2.0
    loop_w = max(4.0, view.w * 0.75)
    loop_h = max(2.5, view.h * 0.8)
    return [
        (x + half_w, y - half_h * 0.45),
        (x + half_w + loop_w, y - half_h * 0.45),
        (x + half_w + loop_w, y + half_h * 0.45),
        (x + half_w, y + half_h * 0.45),
    ]


def _vertex_rect(view: Any) -> tuple[float, float, float, float]:
    return (
        view.xy[0] - view.w / 2.0,
        view.xy[1] - view.h / 2.0,
        view.xy[0] + view.w / 2.0,
        view.xy[1] + view.h / 2.0,
    )


def _point_in_rect(
    point: tuple[float, float],
    rect: tuple[float, float, float, float],
) -> bool:
    return rect[0] <= point[0] <= rect[2] and rect[1] <= point[1] <= rect[3]


def _segment_rect_intersection(
    start: tuple[float, float],
    end: tuple[float, float],
    rect: tuple[float, float, float, float],
) -> tuple[float, float]:
    x1, y1 = start
    x2, y2 = end
    
    cx = (rect[0] + rect[2]) / 2.0
    max_dev = (rect[2] - rect[0]) * 0.35 # Allow spreading along middle 70% of the edge
    
    # Determine which point is the center (inside the rect)
    if rect[0] <= x1 <= rect[2] and rect[1] <= y1 <= rect[3]:
        # start is center (source node)
        intersect_y = rect[3] if y2 > y1 else rect[1]
        dy = y2 - y1
        dx = x2 - x1
        if dy == 0:
            clamped_x = max(cx - max_dev, min(cx + max_dev, x2))
            return (clamped_x, intersect_y)
            
        t = (intersect_y - y1) / dy
        x = x1 + t * dx
        clamped_x = max(cx - max_dev, min(cx + max_dev, x))
        return (clamped_x, intersect_y)
    else:
        # end is center (target node)
        intersect_y = rect[1] if y1 < y2 else rect[3]
        dy = y2 - y1
        dx = x2 - x1
        if dy == 0:
            clamped_x = max(cx - max_dev, min(cx + max_dev, x1))
            return (clamped_x, intersect_y)
            
        t = (intersect_y - y1) / dy
        x = x1 + t * dx
        clamped_x = max(cx - max_dev, min(cx + max_dev, x))
        return (clamped_x, intersect_y)


def _draw_png_node(draw, vertex: Any, project, font, ss: int) -> None:
    left, top = project(
        (
            vertex.view.xy[0] - vertex.view.w / 2.0,
            vertex.view.xy[1] - vertex.view.h / 2.0,
        )
    )
    right, bottom = project(
        (
            vertex.view.xy[0] + vertex.view.w / 2.0,
            vertex.view.xy[1] + vertex.view.h / 2.0,
        )
    )
    name = str(vertex.data.name or vertex.data.id).strip()
    
    # Premium Light Mode Styling
    fill = (255, 255, 255, 255) # white
    outline = (59, 130, 246, 255) # blue-500
    text_color = (30, 41, 59, 255) # slate-800
    radius = 6 * ss

    if name == START:
        fill = (16, 185, 129, 255) # emerald-500
        outline = (16, 185, 129, 255)
        text_color = (255, 255, 255, 255)
        radius = (bottom - top) // 2
    elif name == END:
        fill = (249, 115, 22, 255) # orange-500
        outline = (249, 115, 22, 255)
        text_color = (255, 255, 255, 255)
        radius = (bottom - top) // 2

    # Draw subtle drop shadow
    shadow_offset = 3 * ss
    draw.rounded_rectangle(
        (left, top + shadow_offset, right, bottom + shadow_offset),
        radius=radius,
        fill=(0, 0, 0, 15),
    )

    # Draw main node
    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=radius,
        fill=fill,
        outline=outline,
        width=2 * ss,
    )
    _draw_centered_text(draw, (left, top, right, bottom), name, font, text_color)


def _draw_centered_text(draw, box, text: str, font, fill) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = box[0] + (box[2] - box[0] - text_width) // 2
    y = box[1] + (box[3] - box[1] - text_height) // 2
    draw.text((x, y), text, fill=fill, font=font)


def _draw_dashed_polyline(draw, points, *, fill, width: int, dash: int, gap: int) -> None:
    for start, end in zip(points, points[1:]):
        _draw_dashed_line(draw, start, end, fill=fill, width=width, dash=dash, gap=gap)


def _draw_dashed_line(draw, start, end, *, fill, width: int, dash: int, gap: int) -> None:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return
    steps = int(length // (dash + gap)) + 1
    for index in range(steps):
        segment_start = index * (dash + gap)
        segment_end = min(segment_start + dash, length)
        if segment_start >= length:
            break
        p1 = (
            start[0] + dx * segment_start / length,
            start[1] + dy * segment_start / length,
        )
        p2 = (
            start[0] + dx * segment_end / length,
            start[1] + dy * segment_end / length,
        )
        draw.line((p1, p2), fill=fill, width=width)


def _draw_arrowhead(draw, start, end, fill, ss: int) -> None:
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    size = 10 * ss
    points = [
        end,
        (
            end[0] - size * math.cos(angle - math.pi / 6),
            end[1] - size * math.sin(angle - math.pi / 6),
        ),
        (
            end[0] - size * math.cos(angle + math.pi / 6),
            end[1] - size * math.sin(angle + math.pi / 6),
        ),
    ]
    draw.polygon(points, fill=fill)


def _get_curved_polyline(p0: tuple[float, float], p1: tuple[float, float], offset: float) -> list[tuple[float, float]]:
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return [p0, p1]
    nx = -dy / length
    ny = dx / length
    cx = (p0[0] + p1[0]) / 2 + nx * offset
    cy = (p0[1] + p1[1]) / 2 + ny * offset

    curve_points = []
    for i in range(21):
        t = i / 20
        x = (1 - t)**2 * p0[0] + 2 * (1 - t) * t * cx + t**2 * p1[0]
        y = (1 - t)**2 * p0[1] + 2 * (1 - t) * t * cy + t**2 * p1[1]
        curve_points.append((x, y))
    return curve_points


def _smooth_polyline(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Uses Chaikin's corner cutting algorithm to smooth a polyline."""
    if len(points) <= 2:
        return points
    
    smoothed = points
    for _ in range(4): # 4 iterations of smoothing
        new_pts = [smoothed[0]]
        for i in range(len(smoothed) - 1):
            p0 = smoothed[i]
            p1 = smoothed[i+1]
            new_pts.append((0.75 * p0[0] + 0.25 * p1[0], 0.75 * p0[1] + 0.25 * p1[1]))
            new_pts.append((0.25 * p0[0] + 0.75 * p1[0], 0.25 * p0[1] + 0.75 * p1[1]))
        new_pts.append(smoothed[-1])
        smoothed = new_pts
    return smoothed
