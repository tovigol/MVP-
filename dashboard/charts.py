"""Small hand-built SVG charts (pure functions returning HTML strings).

Why not a chart library: the dashboard refreshes every 2 seconds, and plain SVG
redraws instantly without the flicker/animation of a chart widget.
Each mark has a <title>, so hovering shows its exact numbers.
"""
from __future__ import annotations

import html
import math

from dashboard.data import CRITICAL, GOOD, WARNING

GRID = "#eef0f4"
AXIS_TEXT = "#98a2b3"
SURFACE = "#ffffff"
EMPTY = "#e9ecf2"

GROUPS = [("automatic", "Done automatically", GOOD),
          ("owner", "Sent for approval", WARNING),
          ("blocked", "Stopped", CRITICAL)]  # stacking order, bottom to top


def _nice_step(value: float) -> int:
    """Smallest 1/2/5 x 10^n that is >= value (at least 1)."""
    if value <= 1:
        return 1
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 5, 10):
        if value <= step * magnitude:
            return int(step * magnitude)
    return int(10 * magnitude)


def _nice_ceiling(value: float, ticks: int = 4) -> int:
    """Axis maximum split into `ticks` whole-number steps (so labels are never 3.75)."""
    return _nice_step(math.ceil(value / ticks)) * ticks


def sparkline(values: list[int], color: str, width: int = 96, height: int = 36) -> str:
    """A tiny area + 2px line; flat baseline when there is no data."""
    if not values:
        values = [0]
    top = max(values) or 1
    step = width / max(1, len(values) - 1)
    points = [(i * step, height - 3 - (v / top) * (height - 6)) for i, v in enumerate(values)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area = f"0,{height} {line} {width},{height}"
    gid = f"g{color.strip('#')}"
    return (f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" aria-hidden="true">'
            f'<defs><linearGradient id="{gid}" x1="0" x2="0" y1="0" y2="1">'
            f'<stop offset="0" stop-color="{color}" stop-opacity=".25"/>'
            f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>'
            f'<polygon points="{area}" fill="url(#{gid})"/>'
            f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')


def hourly_bars(buckets: list[dict], current_hour: int | None = None) -> str:
    """Stacked columns per hour (done / sent for approval / stopped), recessive grid, hover titles."""
    width, height = 760, 400
    left, right, top, bottom = 38, 8, 12, 30
    plot_w, plot_h = width - left - right, height - top - bottom
    totals = [sum(b[k] for k, _, _ in GROUPS) for b in buckets]
    y_max = _nice_ceiling(max(totals) if totals else 0)
    slot = plot_w / len(buckets)
    bar_w = min(18.0, slot * 0.56)

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
             f'aria-label="Actions per hour today">']
    for i in range(5):  # gridlines + y labels
        value = y_max * i / 4
        y = top + plot_h - plot_h * i / 4
        parts.append(f'<line x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}" '
                     f'stroke="{GRID}" stroke-width="1"/>')
        parts.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="14" '
                     f'fill="{AXIS_TEXT}">{value:g}</text>')

    for i, bucket in enumerate(buckets):
        x = left + i * slot + (slot - bar_w) / 2
        hour = bucket["hour"]
        if hour % 3 == 0:
            parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{height - 9}" text-anchor="middle" '
                         f'font-size="14" fill="{AXIS_TEXT}">{hour:02d}:00</text>')
        tip = (f"{hour:02d}:00-{hour:02d}:59 · " +
               ", ".join(f"{bucket[k]} {label.lower()}" for k, label, _ in GROUPS))
        parts.append(f'<g><title>{html.escape(tip)}</title>'
                     f'<rect x="{left + i * slot:.1f}" y="{top}" width="{slot:.1f}" height="{plot_h}" '
                     f'fill="transparent"/>')  # big hit target for the hover
        if current_hour is not None and hour == current_hour and not totals[i]:
            parts.append(f'<rect x="{x:.1f}" y="{top + plot_h - 3}" width="{bar_w:.1f}" height="3" '
                         f'rx="1.5" fill="{EMPTY}"/>')
        y_cursor = top + plot_h
        drawn = [(k, c) for k, _, c in GROUPS if bucket[k]]
        for n, (key, color) in enumerate(drawn):
            seg_h = plot_h * bucket[key] / y_max
            is_top = n == len(drawn) - 1
            gap = 0 if is_top else 2  # 2px surface gap between stacked segments
            y_cursor -= seg_h
            rx = 4 if is_top else 0
            parts.append(f'<rect x="{x:.1f}" y="{y_cursor:.1f}" width="{bar_w:.1f}" '
                         f'height="{max(1.0, seg_h - gap):.1f}" rx="{rx}" fill="{color}"/>')
            if is_top and seg_h > 8:  # square off the bottom of a rounded top segment
                parts.append(f'<rect x="{x:.1f}" y="{y_cursor + seg_h / 2:.1f}" width="{bar_w:.1f}" '
                             f'height="{max(1.0, seg_h / 2 - gap):.1f}" fill="{color}"/>')
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)


def donut(segments: list[tuple[str, int, str]], center_value: str, center_label: str) -> str:
    """Ring chart with 2px gaps between parts and the total in the middle."""
    size, radius, stroke = 180, 66, 20
    circumference = 2 * math.pi * radius
    total = sum(n for _, n, _ in segments)
    c = size / 2
    parts = [f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img" '
             f'aria-label="{html.escape(center_value + " " + center_label)}">',
             f'<circle cx="{c}" cy="{c}" r="{radius}" fill="none" stroke="{EMPTY}" stroke-width="{stroke}"/>']
    if total:
        offset = 0.0
        visible = [s for s in segments if s[1]]
        gap = 2 if len(visible) > 1 else 0
        for label, n, color in visible:
            length = circumference * n / total
            parts.append(
                f'<circle cx="{c}" cy="{c}" r="{radius}" fill="none" stroke="{color}" '
                f'stroke-width="{stroke}" stroke-dasharray="{max(0.5, length - gap):.2f} {circumference:.2f}" '
                f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 {c} {c})">'
                f'<title>{html.escape(label)}: {n} ({round(100 * n / total)}%)</title></circle>')
            offset += length
    parts.append(f'<text x="{c}" y="{c + 4}" text-anchor="middle" font-size="30" font-weight="700" '
                 f'fill="#101828">{html.escape(center_value)}</text>')
    parts.append(f'<text x="{c}" y="{c + 24}" text-anchor="middle" font-size="12" '
                 f'fill="#667085">{html.escape(center_label)}</text></svg>')
    return "".join(parts)
