"""
Chart image generation for the watchlist scanner. PushNotification can only
send text, so this saves a PNG locally (charts/ subfolder) and the
notification just references the file path -- see wyckoff_watchlist_scanner.py.
"""

import matplotlib
matplotlib.use("Agg")  # no display available when run headless from a scheduled task
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from pathlib import Path

CHART_DIR = Path(__file__).with_name("charts")

MARKER_STYLE = {
    "spring": dict(color="green", marker="^", label="Spring"),
    "upthrust": dict(color="red", marker="v", label="Upthrust"),
    "SC": dict(color="darkgreen", marker="P", label="Selling Climax"),
    "BC": dict(color="darkred", marker="P", label="Buying Climax"),
    "AR": dict(color="gray", marker="D", label="Automatic Rally/Reaction"),
    "SOS": dict(color="blue", marker="^", label="Sign of Strength"),
    "SOW": dict(color="orange", marker="v", label="Sign of Weakness"),
    "LPS": dict(color="teal", marker="o", label="Last Point of Support"),
    "LPSY": dict(color="brown", marker="o", label="Last Point of Supply"),
    "abc_start": dict(color="purple", marker="o", label="ABC start (0)"),
    "abc_a": dict(color="purple", marker="s", label="ABC point A"),
    "abc_b": dict(color="purple", marker="s", label="ABC point B"),
    "abc_c": dict(color="purple", marker="s", label="ABC point C"),
}


def plot_signal_chart(sym, bars, out_dir=None, res=None, sup=None,
                       range_high=None, range_low=None, markers=None,
                       title_suffix="", window=90):
    """markers: list of {"idx": int, "kind": one of MARKER_STYLE, "text": optional label}
    idx values are absolute indices into `bars`. Returns the saved PNG path."""
    out_dir = Path(out_dir) if out_dir else CHART_DIR
    out_dir.mkdir(exist_ok=True)

    n = len(bars)
    start = max(0, n - window)
    view = bars[start:]
    x = list(range(len(view)))

    fig, ax = plt.subplots(figsize=(11, 6))
    for i, b in enumerate(view):
        color = "#2e7d32" if b["close"] >= b["open"] else "#c62828"
        ax.plot([i, i], [b["low"], b["high"]], color=color, linewidth=0.8, zorder=2)
        body_low, body_high = sorted([b["open"], b["close"]])
        ax.add_patch(Rectangle((i - 0.3, body_low), 0.6, max(body_high - body_low, 0.01),
                                facecolor=color, edgecolor=color, zorder=3))

    if res is not None:
        ax.axhline(res, color="crimson", linestyle="--", linewidth=1, label=f"Resistance {res:.2f}")
    if sup is not None:
        ax.axhline(sup, color="seagreen", linestyle="--", linewidth=1, label=f"Support {sup:.2f}")
    if range_high is not None:
        ax.axhline(range_high, color="slategray", linestyle=":", linewidth=1, label=f"Range high {range_high:.2f}")
    if range_low is not None:
        ax.axhline(range_low, color="slategray", linestyle=":", linewidth=1, label=f"Range low {range_low:.2f}")

    seen_labels = set()
    for m in markers or []:
        idx = m["idx"] - start
        if idx < 0 or idx >= len(view):
            continue
        style = MARKER_STYLE.get(m["kind"], dict(color="black", marker="*", label=m["kind"]))
        label = style["label"] if style["label"] not in seen_labels else None
        seen_labels.add(style["label"])
        if "price" in m:
            price = m["price"]
        else:
            # SOW/SC/spring/LPS mark the breakdown/dip extreme -> low; the rest mark the up-side extreme -> high
            price = view[idx]["high"] if m["kind"] in ("upthrust", "BC", "LPSY", "SOS") else view[idx]["low"]
        ax.scatter([idx], [price], color=style["color"], marker=style["marker"], s=90,
                   zorder=4, label=label)
        if m.get("text"):
            ax.annotate(m["text"], (idx, price), textcoords="offset points", xytext=(0, 10),
                        ha="center", fontsize=8, color=style["color"])

    tick_step = max(1, len(view) // 10)
    ax.set_xticks(x[::tick_step])
    ax.set_xticklabels([view[i]["date"] for i in x[::tick_step]], rotation=45, ha="right", fontsize=8)
    ax.set_title(f"{sym} -- {title_suffix}" if title_suffix else sym)
    ax.set_ylabel("Price")
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()

    last_date = bars[-1]["date"]
    safe_suffix = "".join(c if c.isalnum() else "_" for c in title_suffix)[:40]
    out_path = out_dir / f"{sym}_{last_date}_{safe_suffix}.png"
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path
