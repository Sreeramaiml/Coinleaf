"""Chart geometry for the dashboard, rendered as inline SVG by templates/partials/charts.html.

Mark specs follow the data-viz method: bars <= 24px thick with a 4px rounded data end and
a square base; 2px lines with round joins; end markers r=4 with a 2px surface ring;
hairline solid gridlines; compact Indian axis labels; colours by role via CSS classes
(series-1 = money in, series-2 = money out; a single-series chart uses its entity's hue).
Values are never computed here: every number arrives from the analytics layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from finassist.core.money import format_compact_inr, format_inr, percent

__all__ = ["columns", "hbars", "line_chart", "nice_ticks", "segments", "sparkline"]


def nice_ticks(max_value: int, count: int = 4) -> list[int]:
    """Clean tick values (minor units) from 0 to at least max_value."""
    if max_value <= 0:
        return [0, 100_000]  # ₹0 .. ₹1K for an empty chart
    raw = max_value / count
    mag = 1
    while mag * 10 <= raw:
        mag *= 10
    for mult in (1, 2, 2.5, 5, 10):
        step = int(mag * mult)
        if step * count >= max_value:
            break
    return [step * i for i in range(count + 1)]


def _col_path(x: float, y_top: float, w: float, base: float) -> str:
    """A column with a 4px rounded top and a square base."""
    h = base - y_top
    if h <= 0:
        return ""
    r = min(4.0, w / 2, h)
    return (
        f"M{x:.1f},{base:.1f} L{x:.1f},{y_top + r:.1f} Q{x:.1f},{y_top:.1f} {x + r:.1f},{y_top:.1f} "
        f"L{x + w - r:.1f},{y_top:.1f} Q{x + w:.1f},{y_top:.1f} {x + w:.1f},{y_top + r:.1f} "
        f"L{x + w:.1f},{base:.1f} Z"
    )


def _bar_path(x0: float, y: float, length: float, h: float) -> str:
    """A horizontal bar with a 4px rounded end and a square start."""
    if length <= 0:
        return ""
    r = min(4.0, h / 2, length)
    x1 = x0 + length
    return (
        f"M{x0:.1f},{y:.1f} L{x1 - r:.1f},{y:.1f} Q{x1:.1f},{y:.1f} {x1:.1f},{y + r:.1f} "
        f"L{x1:.1f},{y + h - r:.1f} Q{x1:.1f},{y + h:.1f} {x1 - r:.1f},{y + h:.1f} L{x0:.1f},{y + h:.1f} Z"
    )


def _month_label(key: str, first: bool) -> str:
    d = date.fromisoformat(key + "-01")
    return f"{d:%b} '{d:%y}" if first or d.month == 1 else f"{d:%b}"


@dataclass
class Chart:
    width: int
    height: int
    title: str
    desc: str
    data: dict[str, Any] = field(default_factory=dict)


def line_chart(monthly: list[dict[str, Any]], series: list[tuple[str, str, str]]) -> Chart:
    """Trend over months. series: (key, name, css class)."""
    W, H = 720, 260
    left, right, top, bottom = 58, 16, 14, 30
    pw, ph = W - left - right, H - top - bottom
    n = len(monthly)
    peak = max([m[k] for m in monthly for k, _, _ in series] + [0])
    ticks = nice_ticks(peak)
    ymax = ticks[-1]

    def X(i: int) -> float:
        return left + (pw * i / (n - 1) if n > 1 else pw / 2)

    def Y(v: int) -> float:
        return top + ph - (ph * v / ymax if ymax else 0)

    lines = []
    for key, name, cls in series:
        pts = [(X(i), Y(m[key])) for i, m in enumerate(monthly)]
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        lines.append(
            {
                "name": name,
                "cls": cls,
                "d": d,
                "last": pts[-1] if pts else None,
                "area": d + f" L{pts[-1][0]:.1f},{top + ph:.1f} L{pts[0][0]:.1f},{top + ph:.1f} Z"
                if pts
                else "",
            }
        )
    band = pw / max(n - 1, 1)
    hits = []
    for i, m in enumerate(monthly):
        label = date.fromisoformat(m["month"] + "-01").strftime("%B %Y")
        rows = "\n".join(f"{name}\t{format_inr(m[key])}" for key, name, _ in series)
        hits.append(
            {
                "x": X(i) - band / 2,
                "w": band,
                "cx": X(i),
                "title": label,
                "rows": rows,
                "aria": f"{label}: " + ", ".join(f"{name} {format_inr(m[key])}" for key, name, _ in series),
            }
        )
    table = [
        [date.fromisoformat(m["month"] + "-01").strftime("%b %Y")] + [format_inr(m[k]) for k, _, _ in series]
        for m in monthly
    ]
    return Chart(
        W,
        H,
        "Money in and money out by month",
        "Line chart of monthly totals",
        {
            "left": left,
            "right": W - right,
            "top": top,
            "base": top + ph,
            "yticks": [{"y": Y(t), "label": format_compact_inr(t)} for t in ticks],
            "xlabels": [
                {"x": X(i), "label": _month_label(m["month"], i == 0)} for i, m in enumerate(monthly)
            ],
            "lines": lines,
            "hits": hits,
            "table_head": ["Month"] + [s[1] for s in series],
            "table": table,
        },
    )


def columns(points: list[tuple[date, int]], cls: str, title: str) -> Chart:
    """One series of daily columns (money out per day)."""
    W, H = 720, 200
    left, right, top, bottom = 58, 10, 12, 26
    pw, ph = W - left - right, H - top - bottom
    n = max(len(points), 1)
    ticks = nice_ticks(max([v for _, v in points] + [0]))
    ymax = ticks[-1]
    band = pw / n
    w = min(24.0, max(band - 4.0, 2.0))  # <=24px, with air between columns
    base = top + ph
    cols = []
    for i, (d, v) in enumerate(points):
        x = left + band * i + (band - w) / 2
        y = base - (ph * v / ymax if ymax else 0)
        cols.append(
            {
                "d": _col_path(x, y, w, base),
                "hx": left + band * i,
                "hw": band,
                "title": f"{d:%a %d %b %Y}",
                "rows": f"Money out\t{format_inr(v)}",
                "aria": f"{d:%d %b}: {format_inr(v)}",
            }
        )
    xl = [
        {"x": left + band * i + band / 2, "label": f"{d.day}"}
        for i, (d, _) in enumerate(points)
        if d.day == 1 or d.day % 5 == 0
    ]
    table = [[f"{d:%d %b %Y}", format_inr(v)] for d, v in points if v]
    return Chart(
        W,
        H,
        title,
        "Column chart of spending per day",
        {
            "left": left,
            "right": W - right,
            "top": top,
            "base": base,
            "cls": cls,
            "cols": cols,
            "xlabels": xl,
            "yticks": [{"y": base - ph * t / ymax, "label": format_compact_inr(t)} for t in ticks],
            "table_head": ["Day", "Money out"],
            "table": table,
        },
    )


def hbars(items: list[tuple[str, int]], cls: str, title: str, extra: list[str] | None = None) -> Chart:
    """Ranked horizontal bars, one hue (part-to-whole, compared with each other)."""
    W = 460
    label_w, value_w = 150, 92
    row, thick = 34, 14
    H = max(row * len(items), row)
    top = max((v for _, v in items), default=0)
    span = W - label_w - value_w
    bars = []
    for i, (name, v) in enumerate(items):
        y = i * row + (row - thick) / 2
        length = span * v / top if top else 0
        bars.append(
            {
                "name": name,
                "y": y,
                "ty": i * row + row / 2,
                "d": _bar_path(label_w, y, length, thick),
                "vx": label_w + length + 8,
                "value": format_compact_inr(v),
                "full": format_inr(v),
                "note": extra[i] if extra else "",
            }
        )
    table = [[name, format_inr(v)] + ([extra[i]] if extra else []) for i, (name, v) in enumerate(items)]
    return Chart(W, H, title, "Ranked bars", {"cls": cls, "bars": bars, "label_w": label_w, "table": table})


def sparkline(values: list[int], width: int = 132, height: int = 36) -> dict[str, Any]:
    """12-point trend in the de-emphasis hue; the current (last) point in the accent."""
    if not values:
        return {}
    hi, lo = max(values), min(values)
    rng = (hi - lo) or 1
    pad = 4
    pts = [
        (
            pad + (width - 2 * pad) * i / max(len(values) - 1, 1),
            pad + (height - 2 * pad) * (1 - (v - lo) / rng),
        )
        for i, v in enumerate(values)
    ]
    return {
        "w": width,
        "h": height,
        "d": "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts),
        "last": pts[-1],
    }


def segments(
    parts: list[tuple[str, int]], slots: list[str], width: int = 320, height: int = 28
) -> dict[str, Any]:
    """Tall segmented bar (one whole split into <= 6 parts) as SVG geometry. Colour follows the
    part (its fixed slot), never its rank; a part too narrow for its label prints none."""
    total = sum(v for _, v in parts if v > 0)
    blocks = []
    x = 0.0
    gap = 2.0
    for (name, v), slot in zip(parts, slots, strict=False):
        if v <= 0 or not total:
            continue
        share = percent(v, total) or Decimal(0)
        w = width * v / total
        blocks.append(
            {
                "name": name,
                "slot": slot,
                "x": x,
                "w": max(w - gap, 1.0),
                "label": f"{share:.0f}%",
                "value": format_inr(v),
                "fits": w >= 44,
                "ty": height / 2,
            }
        )
        x += w
    return {"blocks": blocks, "total": format_inr(total), "width": width, "height": height}
