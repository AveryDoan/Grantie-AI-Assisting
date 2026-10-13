"""Charts: our redaction model next to other systems on DataSIR. Makes no model run; it only draws stored numbers.

    python docs/redaction_compare_charts.py          # writes docs/compare_*.png

OTHER SYSTEMS (published, not re-run by us)
  Source: Mo et al., "DataSIR: A Benchmark Dataset for Sensitive Information Recognition", NeurIPS 2025,
  slide 8 (Tables 3 and 4), https://neurips.cc/media/neurips-2025/Slides/121867.pdf
  Metric: LRAcc = the tool must recognise the value AND give its category label, as a % of samples.

OUR MODEL (measured by us, docs/redaction_datasir.ipynb, seed 7, 3,030-row sample of the same dataset, 16 methods)
  Metric: % of values removed from the text the AI would receive (100 - "survive %").

THE TWO METRICS DIFFER. Removing a value is easier than naming its category, so the bars are NOT a like-for-like
score. They show the order of magnitude, and every chart says so. Different samples and settings also apply.
"""

from pathlib import Path

import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parent

# ---- published (LRAcc %, DataSIR Tables 3 and 4) ----
NLP = {  # tool: (original, transformed)
    "HanLP": (13.71, 4.15), "spaCy": (13.29, 2.40), "NLTK": (2.59, 0.39), "Presidio": (23.71, 3.31),
}
LLM = {  # model: (no label/format hint, label hint, label + format hint)
    "DeepSeek-V3": (4.18, 47.90, 54.37), "Qwen3-235B": (5.68, 47.55, 55.97),
    "Gemini 2.5 Flash": (4.46, 53.91, 65.04), "GPT-4.1": (6.65, 55.79, 64.30),
}

# ---- ours: method -> (rows, survive % before the obfuscation layer, survive % after) ----
OURS = {
    "Original data": (240, 12, 12), "Text inversion": (60, 92, 92), "Acrostic poetry": (30, 57, 57),
    "Martian text": (240, 72, 25), "Inserting special characters": (240, 39, 16),
    "Inserting Chinese characters": (60, 10, 10), "Base64": (240, 92, 1), "ASCII encoding": (240, 94, 0),
    "Braille": (240, 85, 0), "HTML entity": (240, 100, 0), "Morse": (240, 88, 0), "Nested encoding": (240, 91, 0),
    "Numerical capitalization": (150, 85, 0), "URL encoding": (90, 69, 0), "UTF-8 escapes": (240, 95, 0),
    "Unicode escapes": (240, 93, 0),
}
TYPES = {  # data type: (survive % before, after)
    "Name": (75, 21), "Driver's license": (86, 11), "Personal ID": (87, 9), "Address": (65, 8),
    "Passport": (84, 3), "Email": (69, 1), "Landline": (77, 1), "Mobile": (75, 0),
}

C_NLP, C_LLM, C_OFF, C_ON, GREY = "#8a8f98", "#d98e04", "#e39a8f", "#1b5e8a", "#555"
CAVEAT = "Metrics differ: ours = value removed; others = LRAcc (value recognised and labelled). Order of magnitude only."


def removed(rows):
    """(removed % before, after) over the transformed rows, from the rounded per-method survive %."""
    t = [(n, b, a) for m, (n, b, a) in rows.items() if m != "Original data"]
    N = sum(n for n, _, _ in t)
    return 100 - sum(n * b for n, b, _ in t) / N, 100 - sum(n * a for n, _, a in t) / N


def style(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(colors=GREY)


def labels(ax, bars, fmt="{:.0f}%"):
    for b in bars:
        ax.text(b.get_width() + 1, b.get_y() + b.get_height() / 2, fmt.format(b.get_width()), va="center", fontsize=9, color="#222")


def chart_headline():
    off, on = removed(OURS)
    names = [*NLP, *LLM, "Our model\n(before obfuscation layer)", "Our model\n(now)"]
    vals = [v[1] for v in NLP.values()] + [v[2] for v in LLM.values()] + [off, on]
    cols = [C_NLP] * len(NLP) + [C_LLM] * len(LLM) + [C_OFF, C_ON]
    fig, ax = plt.subplots(figsize=(11, 6), facecolor="white")
    bars = ax.barh(names[::-1], vals[::-1], color=cols[::-1])
    labels(ax, bars)
    ax.set_xlim(0, 108); ax.set_xlabel("% of format-transformed values (higher is better)")
    ax.set_title("Encoded and obfuscated personal data (DataSIR)", loc="left", fontsize=14, fontweight="bold")
    fig.text(0.01, 0.005, CAVEAT + "\nOthers: DataSIR paper (NeurIPS 2025), LLMs with label+format hint. Ours: 2,790 transformed rows, own run.",
             fontsize=8, color=GREY, va="bottom")
    ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=c) for c in (C_NLP, C_LLM, C_OFF, C_ON)],
              labels=["Classic NLP tools", "LLMs (published)", "Ours, before", "Ours, now"], loc="center right", bbox_to_anchor=(1, 0.62), frameon=False)
    style(ax); fig.tight_layout(rect=(0, 0.07, 1, 1)); fig.savefig(OUT / "compare_headline.png", dpi=150); plt.close(fig)


def chart_plain_vs_encoded():
    off, on = removed(OURS)
    orig = 100 - OURS["Original data"][1]
    groups = ["Plain values\n(untransformed)", "Encoded / obfuscated\nvalues"]
    series = [("HanLP", C_NLP, [13.71, 4.15]), ("spaCy", "#b0b4ba", [13.29, 2.40]), ("Presidio", "#5d6470", [23.71, 3.31]),
              ("Our model, now", C_ON, [orig, on])]
    fig, ax = plt.subplots(figsize=(9, 5.6), facecolor="white")
    w = 0.2
    for i, (n, c, v) in enumerate(series):
        bars = ax.bar([x + (i - 1.5) * w for x in range(2)], v, w, color=c, label=n)
        for b in bars: ax.text(b.get_x() + w / 2, b.get_height() + 1.2, f"{b.get_height():.0f}%", ha="center", fontsize=9)
    ax.set_xticks(range(2)); ax.set_xticklabels(groups); ax.set_ylim(0, 108)
    ax.set_ylabel("% of values (higher is better)")
    ax.set_title("Plain vs encoded values: classic tools drop sharply, ours drops little", loc="left", fontsize=13, fontweight="bold")
    fig.text(0.01, 0.005, CAVEAT + "\nPublished tools: DataSIR Table 3 (LRAcc). Ours: own run on a 3,030-row sample.", fontsize=8, color=GREY, va="bottom")
    ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(0.0, 0.8)); style(ax); fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(OUT / "compare_plain_vs_encoded.png", dpi=150); plt.close(fig)


def chart_methods():
    items = sorted(OURS.items(), key=lambda kv: kv[1][2])
    fig, ax = plt.subplots(figsize=(10, 7), facecolor="white")
    ys = range(len(items))
    ax.barh([y + .2 for y in ys], [100 - v[1] for _, v in items], .38, color=C_OFF, label="Before (no obfuscation layer; Presidio-based)")
    b = ax.barh([y - .2 for y in ys], [100 - v[2] for _, v in items], .38, color=C_ON, label="Now")
    ax.set_yticks(list(ys)); ax.set_yticklabels([k for k, _ in items]); ax.set_xlim(0, 105)
    ax.set_xlabel("% of values removed before the AI sees them (higher is better)")
    ax.set_title("Our model by encoding method", loc="left", fontsize=14, fontweight="bold")
    ax.legend(frameon=False, loc="upper right"); style(ax)
    fig.text(0.01, 0.005, "Own run, DataSIR sample (seed 7, 3,030 rows). Weakest remaining: text inversion, acrostic poetry.", fontsize=8, color=GREY)
    fig.tight_layout(rect=(0, 0.03, 1, 1)); fig.savefig(OUT / "compare_by_method.png", dpi=150); plt.close(fig)


def chart_llm_prompts():
    """Published only: how much the LLMs depend on being told what to look for, with Presidio for reference."""
    labels_ = ["No hints", "Told the label", "Label + format"]
    cols = ["#f0c987", "#e0a52b", "#b37400"]
    fig, ax = plt.subplots(figsize=(9, 5.2), facecolor="white")
    w = 0.25
    for i, lab in enumerate(labels_):
        ax.bar([x + (i - 1) * w for x in range(len(LLM))], [v[i] for v in LLM.values()], w, color=cols[i], label=lab)
    ax.axhline(NLP["Presidio"][1], color=C_NLP, ls="--", label="Presidio on transformed data (3.3%)")
    ax.set_xticks(range(len(LLM))); ax.set_xticklabels(LLM.keys()); ax.set_ylabel("LRAcc (%)")
    ax.set_title("Published: LLM accuracy jumps from about 5% to 55-65% once told what to look for", loc="left", fontsize=13, fontweight="bold")
    ax.legend(frameon=False, loc="upper left"); style(ax)
    fig.text(0.01, 0.005, "Source: DataSIR paper, Table 4 (NeurIPS 2025). Not our model.", fontsize=8, color=GREY)
    fig.tight_layout(rect=(0, 0.03, 1, 1)); fig.savefig(OUT / "compare_llm_prompts.png", dpi=150); plt.close(fig)


if __name__ == "__main__":
    off, on = removed(OURS)
    print(f"Ours, transformed rows: removed {off:.1f}% before, {on:.1f}% now")
    for f in (chart_headline, chart_plain_vs_encoded, chart_methods, chart_llm_prompts):
        f()
    print("Wrote", *sorted(p.name for p in OUT.glob("compare_*.png")))
