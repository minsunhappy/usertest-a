#!/usr/bin/env python3
"""Pull the study data from Supabase and write a self-contained results page.

    /Users/sunny/miniforge3/bin/python report.py           -> report.html

Reads credentials from supabase_secrets.env. QA/test accounts are excluded and
append-only duplicates collapse to the newest row per (participant, set, condition).
The page carries one section per scope — everyone pooled, plus each participant —
and a filter row switches between them.
"""
import datetime as dt
import html
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
OUT = HERE / "report.html"

# TimeChat is excluded from the study: its saliency answers were all ties, so the
# assembler filled the budget from the earliest candidates and only ever covered the
# first ~30% of each video. Its rows stay in the database; put it back here to analyse them.
CONDITIONS = ["intentcut_s2", "funclip", "random"]
ALL_CONDITIONS = ["intentcut_s2", "funclip", "timechat", "random"]
# fixed colour slot per condition, so a condition keeps its hue when the set changes
COND_SLOT = {"intentcut_s2": 1, "funclip": 2, "timechat": 3, "random": 4}
COND_LABEL = {
    "intentcut_s2": "IntentCut",
    "funclip": "FunClip",
    "timechat": "TimeChat",
    "random": "Random",
}
QS = [
    ("q1", "Q1 · Intent relevance", "How well did the scenes match the given intent?"),
    ("q2", "Q2 · Editing naturalness", "Did it feel like one naturally edited piece?"),
    ("q3", "Q3 · Overall satisfaction", "How satisfying was the viewing experience?"),
]
SET_LABEL = {
    "baseball": "Baseball (home runs)",
    "harp_seal": "Harp seal (close-ups)",
    "worldcup": "World Cup (Messi)",
    "spiderman": "Spider-Man (webs)",
    "interstellar": "Interstellar (wave)",
}
GENDER_EN = {"남": "male", "여": "female", "미기재": "unspecified"}
PER_PERSON = 5 * len(CONDITIONS)   # 5 videos x analysed conditions
EXCLUDE_NAME_RE = re.compile(r"^(QA[-_]|SETUP_TEST$|POLICY_TEST$|test\d*$)", re.IGNORECASE)


# ── data ────────────────────────────────────────────────────────────────
def load_env():
    env = HERE / "supabase_secrets.env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def fetch(table, params):
    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"]
    rows, offset = [], 0
    while True:
        r = requests.get(f"{url}/rest/v1/{table}",
                         headers={"apikey": key, "Authorization": f"Bearer {key}",
                                  "Range": f"{offset}-{offset + 999}"},
                         params=params, timeout=30)
        r.raise_for_status()
        chunk = r.json()
        rows.extend(chunk)
        if len(chunk) < 1000:
            return rows
        offset += 1000


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def stdev(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def collect():
    load_env()
    if not os.environ.get("SUPABASE_SERVICE_KEY"):
        sys.exit("supabase_secrets.env 에 SUPABASE_SERVICE_KEY 를 넣어주세요.")
    people = fetch("participants", {"select": "*", "order": "created_at.asc"})
    resp = fetch("responses", {"select": "*", "order": "id.asc"})

    real = {p["id"]: p for p in people if not EXCLUDE_NAME_RE.match(p.get("name") or "")}
    latest = {}   # newest row wins (edits are appended, never updated)
    for r in resp:
        if r["participant_id"] in real and r["condition"] in CONDITIONS:
            latest[(r["participant_id"], r["set_id"], r["condition"])] = r

    by_person = defaultdict(list)
    for r in latest.values():
        by_person[r["participant_id"]].append(r)

    done, partial = [], []
    for pid, rows in by_person.items():
        (done if len(rows) == PER_PERSON else partial).append((real[pid], rows))
    done.sort(key=lambda t: t[0]["created_at"])
    partial.sort(key=lambda t: t[0]["created_at"])
    return done, partial


# ── stats ───────────────────────────────────────────────────────────────
def wilcoxon_and_t(a, b):
    diffs = [x - y for x, y in zip(a, b)]
    if len(diffs) < 2 or all(d == 0 for d in diffs):
        return None, None
    from scipy import stats
    return stats.wilcoxon(a, b).pvalue, stats.ttest_rel(a, b).pvalue


def cohens_dz(a, b):
    diffs = [x - y for x, y in zip(a, b)]
    sd = stdev(diffs)
    return (mean(diffs) / sd) if sd else None


def units_pooled(done):
    """One unit per participant: their 5 sets averaged. {cond: {q: value}}"""
    out = []
    for _, rows in done:
        u = {}
        for c in CONDITIONS:
            vals = [r for r in rows if r["condition"] == c]
            u[c] = {q: mean([v[q] for v in vals]) for q, _, _ in QS}
        out.append(u)
    return out


def units_one_person(rows):
    """One unit per set, for a single participant. {cond: {q: value}}"""
    out = []
    for s in SET_LABEL:
        set_rows = [r for r in rows if r["set_id"] == s]
        if not set_rows:
            continue
        u = {}
        for c in CONDITIONS:
            hit = next((r for r in set_rows if r["condition"] == c), None)
            u[c] = {q: (hit[q] if hit else None) for q, _, _ in QS}
        out.append(u)
    return out


# ── rendering ───────────────────────────────────────────────────────────
def esc(s):
    return html.escape(str(s))


def mask(name):
    name = (name or "").strip()
    return name[0] + "○" * max(1, len(name) - 1) if name else "anon"


def stars(p):
    """*** p<.001, ** p<.01, * p<.05, † p<.10 — the usual convention."""
    if p is None:
        return "—"
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    if p < 0.10:
        return "†"
    return "n.s."


def bar_chart(title, subtitle, values, compact=False, brackets=()):
    """One bar per condition, with a direct value label on each (relief rule).

    brackets: list of (other_index, label, provisional) comparing bar 0 to that bar.
    """
    W, H = (300, 200) if compact else (360, 240)
    pad_l, pad_r, pad_t, pad_b = 34, 10, 26, 46
    if brackets:
        pad_t = 26 + 22 * len(brackets)
        H += 22 * len(brackets)
    plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b
    lo, hi = 1, 7
    slot = plot_w / len(CONDITIONS)
    bar_w = slot - 16

    def y(v):
        return pad_t + plot_h * (1 - (v - lo) / (hi - lo))

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(title)} by condition">']
    for g in range(1, 8):
        gy = round(y(g), 1)
        parts.append(f'<line class="grid" x1="{pad_l}" x2="{W - pad_r}" y1="{gy}" y2="{gy}"/>')
        parts.append(f'<text class="axis" x="{pad_l - 8}" y="{gy + 3.5}" text-anchor="end">{g}</text>')

    for i, c in enumerate(CONDITIONS):
        m, sem = values[c]
        if m is None:
            continue
        x = pad_l + slot * i + (slot - bar_w) / 2
        top = y(m)
        parts.append(
            f'<g class="bar" style="--hue:var(--c{COND_SLOT[c]});">'
            f'<title>{esc(COND_LABEL[c])} · mean {m:.2f}{f" ± {sem:.2f}" if sem else ""}</title>'
            f'<rect x="{x:.1f}" y="{top:.1f}" width="{bar_w:.1f}" '
            f'height="{pad_t + plot_h - top:.1f}" rx="4" ry="4"/>')
        if sem:
            parts.append(
                f'<line class="err" x1="{x + bar_w / 2:.1f}" x2="{x + bar_w / 2:.1f}" '
                f'y1="{y(min(7, m + sem)):.1f}" y2="{y(max(1, m - sem)):.1f}"/>')
        parts.append(
            f'<text class="val" x="{x + bar_w / 2:.1f}" y="{top - 7:.1f}" '
            f'text-anchor="middle">{m:.1f}</text></g>')
        parts.append(
            f'<text class="cond" x="{x + bar_w / 2:.1f}" y="{H - pad_b + 18}" '
            f'text-anchor="middle">{esc(COND_LABEL[c])}</text>')

    def bar_center(i):
        return pad_l + slot * i + slot / 2

    for level, (other, label, provisional) in enumerate(brackets):
        by = pad_t - 14 - level * 22
        x0, x1 = bar_center(0), bar_center(other)
        cls = "brk provisional" if provisional else "brk"
        parts.append(
            f'<g class="{cls}">'
            f'<path d="M{x0:.1f} {by + 6:.1f} V{by:.1f} H{x1:.1f} V{by + 6:.1f}"/>'
            f'<text x="{(x0 + x1) / 2:.1f}" y="{by - 4:.1f}" text-anchor="middle">{esc(label)}</text>'
            f'</g>')
    parts.append("</svg>")
    return (f'<figure class="chart"><figcaption><h3>{esc(title)}</h3>'
            f'<p>{esc(subtitle)}</p></figcaption>{"".join(parts)}</figure>')


def condition_table(stats, caption):
    head = "".join(f'<th scope="col">{esc(t.split(" · ")[0])}</th>' for _, t, _ in QS)
    rows = []
    for i, c in enumerate(CONDITIONS):
        cells = "".join(
            (f"<td>{stats[c][q][0]:.2f}<span class='sd'> ± {stats[c][q][1]:.2f}</span></td>"
             if stats[c][q][0] is not None else "<td>—</td>") for q, _, _ in QS)
        rows.append(f'<tr><th scope="row"><span class="chip" style="background:var(--c{COND_SLOT[c]})"></span>'
                    f'{esc(COND_LABEL[c])}</th>{cells}</tr>')
    return (f'<div class="scroll"><table class="data"><caption>{esc(caption)}</caption>'
            f'<thead><tr><th scope="col">Condition</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def significance_table(units, caption, enough):
    rows = []
    for q, qt, _ in QS:
        a = [u["intentcut_s2"][q] for u in units]
        for base in [c for c in CONDITIONS if c != "intentcut_s2"]:
            b = [u[base][q] for u in units]
            pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
            xa, xb = [p[0] for p in pairs], [p[1] for p in pairs]
            wp, tp = wilcoxon_and_t(xa, xb) if pairs else (None, None)
            d = cohens_dz(xa, xb) if pairs else None
            diff = (mean(xa) - mean(xb)) if pairs else None
            verdict = "too few" if (wp is None or not enough) else (
                "significant" if (tp is not None and tp < 0.05) else "not significant")
            cls = "sig" if verdict == "significant" else ("na" if verdict == "too few" else "ns")
            mark = stars(tp)
            star_cls = "star" + ("" if (mark not in ("n.s.", "—") and enough) else " muted")
            rows.append(
                f'<tr><td>{esc(qt.split(" · ")[0])}</td><td>vs {esc(COND_LABEL[base])}</td>'
                f'<td class="num">{f"{diff:+.2f}" if diff is not None else "—"}</td>'
                f'<td class="num">{f"{d:.2f}" if d is not None else "—"}</td>'
                f'<td class="num">{f"{wp:.3f}" if wp is not None else "—"}</td>'
                f'<td class="num">{f"{tp:.3f}" if tp is not None else "—"}</td>'
                f'<td><span class="{star_cls}">{esc(mark)}</span></td>'
                f'<td><span class="tag {cls}">{verdict}</span></td></tr>')
    return (f'<div class="scroll"><table class="data"><caption>{esc(caption)}</caption>'
            f'<thead><tr><th scope="col">Question</th><th scope="col">Comparison</th>'
            f'<th scope="col">Mean diff</th><th scope="col">Cohen d<sub>z</sub></th>'
            f'<th scope="col">Wilcoxon p</th><th scope="col">paired-t p</th>'
            f'<th scope="col">Sig.</th><th scope="col">Verdict</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>'
            f'<p class="legend">*** p&lt;.001 · ** p&lt;.01 · * p&lt;.05 · † p&lt;.10 · n.s. not significant'
            f'{" · provisional while the sample is small" if not enough else ""}</p></div>')


def section(scope_id, rowsets, tiles, warn, unit_word, enough, hidden):
    """rowsets: list of (participant, rows) making up this scope."""
    units = units_pooled(rowsets) if scope_id == "all" else units_one_person(rowsets[0][1])

    stats = {}
    for c in CONDITIONS:
        stats[c] = {}
        for q, _, _ in QS:
            vals = [u[c][q] for u in units]
            vals = [v for v in vals if v is not None]
            stats[c][q] = (mean(vals), (stdev(vals) / len(vals) ** 0.5) if len(vals) > 1 else 0.0)

    err = f" · error bars = SEM across {unit_word}" if len(units) > 1 else ""
    prov = "" if enough else f" · significance is provisional (n={len(units)})"

    def brackets_for(q):
        """IntentCut vs each baseline, drawn above the bars."""
        out = []
        a = [u["intentcut_s2"][q] for u in units]
        for i, base in enumerate([c for c in CONDITIONS if c != "intentcut_s2"], start=1):
            b = [u[base][q] for u in units]
            pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
            if not pairs:
                continue
            _, tp = wilcoxon_and_t([p[0] for p in pairs], [p[1] for p in pairs])
            out.append((i, stars(tp), not enough))
        return out

    charts = "".join(
        bar_chart(t, s + err + prov, {c: stats[c][q] for c in CONDITIONS},
                  brackets=brackets_for(q))
        for q, t, s in QS)

    all_rows = [r for _, rows in rowsets for r in rows]
    set_ids = [s for s in SET_LABEL if any(r["set_id"] == s for r in all_rows)]

    def set_mean(set_id, cond, q):
        return mean([r[q] for r in all_rows if r["set_id"] == set_id and r["condition"] == cond])

    set_charts = "".join(
        bar_chart(SET_LABEL[s], "Q1 intent relevance",
                  {c: (set_mean(s, c, "q1"), 0.0) for c in CONDITIONS}, compact=True)
        for s in set_ids)

    set_tables = []
    for q, qt, _ in QS:
        head = "".join(f'<th scope="col">{esc(SET_LABEL[s])}</th>' for s in set_ids)
        body = []
        for i, c in enumerate(CONDITIONS):
            cells = "".join(
                (f"<td>{set_mean(s, c, q):.1f}</td>" if set_mean(s, c, q) is not None else "<td>—</td>")
                for s in set_ids)
            body.append(f'<tr><th scope="row"><span class="chip" style="background:var(--c{COND_SLOT[c]})"></span>'
                        f'{esc(COND_LABEL[c])}</th>{cells}</tr>')
        set_tables.append(
            f'<div class="scroll"><table class="data"><caption>{esc(qt)} · mean by video</caption>'
            f'<thead><tr><th scope="col">Condition</th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')

    cap = "Mean by condition" + (f" (n={len(units)} {unit_word} · ± SEM)" if len(units) > 1 else "")
    sig_cap = f"IntentCut vs baselines · paired tests over {unit_word} (n={len(units)})"

    return f"""
    <section class="scope" data-scope="{esc(scope_id)}"{' hidden' if hidden else ''}>
      {tiles}
      {warn}
      <h2>Mean scores by condition</h2>
      <p class="lede">{esc("Each participant's five videos are averaged first, then averaged across participants." if scope_id == "all" else "Averaged over the five videos this participant rated.")}</p>
      <div class="charts">{charts}</div>

      <h2>Numbers and significance</h2>
      <p class="lede">The same values as the charts above, followed by IntentCut against each baseline.</p>
      <div class="tables">
        {condition_table(stats, cap)}
        {significance_table(units, sig_cap, enough)}
      </div>

      <h2>Results by video</h2>
      <p class="lede">How the conditions differ within each source video. Bars show Q1 intent relevance.</p>
      <div class="charts">{set_charts}</div>
      <div class="tables" style="margin-top:22px">{"".join(set_tables)}</div>
    </section>"""


def build(done, partial):
    n = len(done)

    genders = defaultdict(int)
    ages = []
    for p, _ in done:
        genders[p.get("gender") or "미기재"] += 1
        if p.get("age"):
            ages.append(p["age"])
    gender_bits = " · ".join(f"{GENDER_EN.get(g, g)} {c}" for g, c in sorted(genders.items(), key=lambda kv: -kv[1]))
    age_val = f"{mean(ages):.0f}" if ages else "—"
    age_sub = f"range {min(ages)}–{max(ages)}" if ages else "completed participants"

    tiles_all = f"""
    <div class="tiles">
      <div class="tile"><span class="k">Completed</span><strong>{n}</strong>
        <span class="sub">{esc(gender_bits) or "—"}</span></div>
      <div class="tile"><span class="k">In progress</span><strong>{len(partial)}</strong>
        <span class="sub">fewer than {PER_PERSON} ratings</span></div>
      <div class="tile"><span class="k">Mean age</span><strong>{age_val}</strong>
        <span class="sub">{esc(age_sub)}</span></div>
      <div class="tile"><span class="k">Ratings</span><strong>{n * PER_PERSON}</strong>
        <span class="sub">5 videos × {len(CONDITIONS)} conditions × {n}</span></div>
    </div>"""

    warn_all = ""
    if n < 2:
        warn_all = f'<p class="warn">With only {n} participant(s) the tests below carry no weight yet — read the means as a trend, nothing more.</p>'
    elif n < 5:
        warn_all = f'<p class="warn">Based on {n} participants. The sample is small, so verdicts are withheld and p-values are shown for reference only.</p>'
    elif n < 10:
        warn_all = f'<p class="warn">Based on {n} participants. The sample is still small, so treat p-values as provisional.</p>'

    sections = [section("all", done, tiles_all, warn_all, "participants", enough=(n >= 5), hidden=False)]
    tabs = ['<button class="tab" type="button" data-target="all" aria-pressed="true">Everyone</button>']

    for p, rows in done:
        pid = p["id"]
        favourite = max(CONDITIONS, key=lambda c: mean([r["q3"] for r in rows if r["condition"] == c]) or 0)
        tiles_p = f"""
        <div class="tiles">
          <div class="tile"><span class="k">Participant</span><strong class="sm">{esc(mask(p.get("name")))}</strong>
            <span class="sub">{esc(p.get("age") or "—")} · {esc(GENDER_EN.get(p.get("gender"), p.get("gender") or "—"))}</span></div>
          <div class="tile"><span class="k">Ratings</span><strong>{len(rows)}</strong>
            <span class="sub">5 videos × {len(CONDITIONS)} conditions</span></div>
          <div class="tile"><span class="k">Top-rated condition</span><strong class="sm">{esc(COND_LABEL[favourite])}</strong>
            <span class="sub">by Q3</span></div>
          <div class="tile"><span class="k">Started</span><strong class="sm">{esc(p["created_at"][:16].replace("T", " "))}</strong>
            <span class="sub">UTC</span></div>
        </div>"""
        warn_p = ('<p class="warn">One person\'s ratings. The tests below pair this participant\'s five videos, '
                  'so they describe a within-person tendency and do not generalise.</p>')
        sections.append(section(pid, [(p, rows)], tiles_p, warn_p, "videos", enough=False, hidden=True))
        tabs.append(f'<button class="tab" type="button" data-target="{esc(pid)}" '
                    f'aria-pressed="false">{esc(mask(p.get("name")))}</button>')

    roster = []
    for p, rows in done + partial:
        n_rows = len(rows)
        state = "complete" if n_rows == PER_PERSON else f"in progress {n_rows}/{PER_PERSON}"
        cls = "done" if n_rows == PER_PERSON else "wip"
        roster.append(f'<tr><td>{esc(mask(p.get("name")))}</td><td class="num">{esc(p.get("age") or "—")}</td>'
                      f'<td>{esc(GENDER_EN.get(p.get("gender"), p.get("gender") or "—"))}</td>'
                      f'<td>{esc(p["created_at"][:16].replace("T", " "))}</td>'
                      f'<td><span class="tag {cls}">{state}</span></td></tr>')
    roster_tbl = (f'<div class="scroll"><table class="data"><caption>Participants (names masked)</caption>'
                  f'<thead><tr><th scope="col">Participant</th><th scope="col">Age</th>'
                  f'<th scope="col">Gender</th><th scope="col">Started (UTC)</th>'
                  f'<th scope="col">Status</th></tr></thead><tbody>{"".join(roster)}</tbody></table></div>')

    stamp = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    return TEMPLATE.format(stamp=esc(stamp), tabs="".join(tabs),
                           sections="".join(sections), roster=roster_tbl)


TEMPLATE = """<title>Intent-Based Highlight Study</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Gowun+Batang:wght@400;700&family=IBM+Plex+Sans+KR:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {{
  color-scheme: light;
  --bg: #f6f7f9; --surface: #ffffff; --line: #e3e6ec;
  --ink: #14181f; --ink-2: #4a5261; --ink-3: #6f7787;
  --accent: #1f4f96;
  --c1: #2a78d6; --c2: #eb6834; --c3: #1baf7a; --c4: #eda100;
  --good-bg: #e7f4ec; --good-ink: #16653a;
  --ns-bg: #eef0f4;  --ns-ink: #4a5261;
  --wip-bg: #fdf1e3; --wip-ink: #8a4d10;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --bg: #14161a; --surface: #1c1f25; --line: #2c313a;
    --ink: #f2f4f8; --ink-2: #b9c0cd; --ink-3: #8d95a4; --accent: #86b3f0;
    --c1: #3987e5; --c2: #d95926; --c3: #199e70; --c4: #c98500;
    --good-bg: #14361f; --good-ink: #8fd6a8;
    --ns-bg: #262b33;  --ns-ink: #b9c0cd;
    --wip-bg: #3a2a16; --wip-ink: #e8bb7e;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --bg: #14161a; --surface: #1c1f25; --line: #2c313a;
  --ink: #f2f4f8; --ink-2: #b9c0cd; --ink-3: #8d95a4; --accent: #86b3f0;
  --c1: #3987e5; --c2: #d95926; --c3: #199e70; --c4: #c98500;
  --good-bg: #14361f; --good-ink: #8fd6a8;
  --ns-bg: #262b33;  --ns-ink: #b9c0cd;
  --wip-bg: #3a2a16; --wip-ink: #e8bb7e;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: "IBM Plex Sans KR", system-ui, sans-serif;
  font-size: 15px; line-height: 1.65;
}}
.page {{ max-width: 1120px; margin: 0 auto; padding: 44px 24px 80px; }}
header.top {{ border-bottom: 1px solid var(--line); padding-bottom: 22px; margin-bottom: 22px; }}
h1 {{
  font-family: "Gowun Batang", Georgia, serif; font-weight: 700;
  font-size: clamp(1.7rem, 3.4vw, 2.3rem); line-height: 1.25; margin: 0 0 6px;
  text-wrap: balance; letter-spacing: -0.01em;
}}
.eyebrow {{
  font-size: .78rem; font-weight: 600; letter-spacing: .13em; text-transform: uppercase;
  color: var(--accent); margin: 0 0 10px;
}}
.stamp {{ color: var(--ink-3); font-size: .9rem; margin: 0; font-variant-numeric: tabular-nums; }}
h2 {{
  font-family: "Gowun Batang", Georgia, serif; font-size: 1.25rem; font-weight: 700;
  margin: 44px 0 4px; letter-spacing: -0.01em;
}}
h2 + .lede {{ color: var(--ink-2); margin: 0 0 16px; font-size: .95rem; }}
.filters {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 22px; }}
.filters .k {{
  font-size: .78rem; font-weight: 600; letter-spacing: .1em; text-transform: uppercase;
  color: var(--ink-3); margin-right: 4px;
}}
.tab {{
  font: inherit; font-size: .92rem; font-weight: 500; cursor: pointer;
  background: var(--surface); color: var(--ink-2);
  border: 1px solid var(--line); border-radius: 999px; padding: 6px 16px;
  transition: background .12s, color .12s, border-color .12s;
}}
.tab:hover {{ border-color: var(--accent); color: var(--ink); }}
.tab[aria-pressed="true"] {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
.tab:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }}
.tile {{
  background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
  padding: 16px 18px; display: flex; flex-direction: column; gap: 2px;
}}
.tile .k {{ font-size: .78rem; font-weight: 600; letter-spacing: .1em; text-transform: uppercase; color: var(--ink-3); }}
.tile strong {{
  font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 1.75rem; font-weight: 500;
  line-height: 1.2; font-variant-numeric: tabular-nums;
}}
.tile strong.sm {{ font-family: "IBM Plex Sans KR", sans-serif; font-size: 1.15rem; font-weight: 600; }}
.tile strong em {{ font-style: normal; font-size: .95rem; color: var(--ink-2); margin-left: 3px; }}
.tile .sub {{ font-size: .85rem; color: var(--ink-2); }}
.warn {{
  background: var(--wip-bg); color: var(--wip-ink); border-radius: 8px;
  padding: 11px 15px; font-size: .92rem; margin: 18px 0 0;
}}
.charts {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 14px; }}
.chart {{
  margin: 0; background: var(--surface); border: 1px solid var(--line);
  border-radius: 10px; padding: 16px 14px 8px;
}}
.chart figcaption h3 {{ font-size: 1rem; margin: 0 0 2px; font-weight: 600; }}
.chart figcaption p {{ margin: 0 0 6px; font-size: .84rem; color: var(--ink-3); }}
.chart svg {{ width: 100%; height: auto; display: block; overflow: visible; }}
.grid {{ stroke: var(--line); stroke-width: 1; }}
.axis {{ fill: var(--ink-3); font-size: 10px; font-family: "IBM Plex Mono", monospace; }}
.cond {{ fill: var(--ink-2); font-size: 11px; font-weight: 500; }}
.val {{ fill: var(--ink); font-size: 12px; font-weight: 500; font-family: "IBM Plex Mono", monospace; }}
.bar rect {{ fill: var(--hue); transition: opacity .12s; }}
.bar:hover rect {{ opacity: .78; }}
.err {{ stroke: var(--ink-2); stroke-width: 2; }}
.brk path {{ fill: none; stroke: var(--ink-2); stroke-width: 1.5; }}
.brk text {{ fill: var(--ink); font-size: 12px; font-weight: 600; font-family: "IBM Plex Mono", monospace; }}
.brk.provisional path {{ stroke: var(--ink-3); stroke-dasharray: 3 3; }}
.brk.provisional text {{ fill: var(--ink-3); font-weight: 500; }}
.legend {{ margin: 8px 0 0; font-size: .82rem; color: var(--ink-3); }}
.star {{ font-family: "IBM Plex Mono", monospace; font-weight: 600; }}
.star.muted {{ color: var(--ink-3); font-weight: 400; }}
.tables {{ display: flex; flex-direction: column; gap: 26px; }}
.scroll {{ overflow-x: auto; }}
table.data {{
  width: 100%; border-collapse: collapse; background: var(--surface);
  border: 1px solid var(--line); border-radius: 10px; overflow: hidden;
  font-variant-numeric: tabular-nums;
}}
table.data caption {{
  caption-side: top; text-align: left; font-size: .82rem; font-weight: 600;
  letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3); padding: 0 0 8px;
}}
table.data th, table.data td {{
  padding: 9px 13px; text-align: left; border-bottom: 1px solid var(--line);
  font-size: .93rem; white-space: nowrap;
}}
table.data thead th {{ font-size: .82rem; color: var(--ink-2); font-weight: 600; }}
table.data tbody tr:last-child th, table.data tbody tr:last-child td {{ border-bottom: none; }}
table.data td, table.data .num {{ font-family: "IBM Plex Mono", ui-monospace, monospace; }}
table.data tbody th {{ font-weight: 500; }}
.sd {{ color: var(--ink-3); font-size: .84em; }}
.chip {{ display: inline-block; width: 10px; height: 10px; border-radius: 3px;
  margin-right: 8px; vertical-align: baseline; }}
.tag {{ display: inline-block; padding: 2px 9px; border-radius: 999px;
  font-size: .8rem; font-weight: 600; font-family: "IBM Plex Sans KR", sans-serif; }}
.tag.sig, .tag.done {{ background: var(--good-bg); color: var(--good-ink); }}
.tag.ns, .tag.na {{ background: var(--ns-bg); color: var(--ns-ink); }}
.tag.wip {{ background: var(--wip-bg); color: var(--wip-ink); }}
footer {{ margin-top: 46px; padding-top: 18px; border-top: 1px solid var(--line);
  color: var(--ink-3); font-size: .86rem; }}
@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; }} }}
</style>

<div class="page">
  <header class="top">
    <p class="eyebrow">User study A</p>
    <h1>Intent-based highlight generation — study results</h1>
    <p class="stamp">Updated {stamp} · 7-point scale, higher is better</p>
  </header>

  <div class="filters" role="group" aria-label="Result scope">
    <span class="k">View</span>{tabs}
  </div>

  {sections}

  <h2>Participants</h2>
  <p class="lede">Names are reduced to the first character. Use the buttons above to see one person's results.</p>
  <div class="tables">{roster}</div>

  <footer>
    IntentCut is the proposed method; FunClip and Random are baselines.
    Where an answer was revised only the latest submission counts, and development/QA accounts are excluded.
  </footer>
</div>

<script>
const tabs = [...document.querySelectorAll(".tab")];
const scopes = [...document.querySelectorAll(".scope")];
tabs.forEach(tab => tab.addEventListener("click", () => {{
  tabs.forEach(t => t.setAttribute("aria-pressed", String(t === tab)));
  const target = tab.dataset.target;
  scopes.forEach(s => {{ s.hidden = s.dataset.scope !== target; }});
}}));
</script>
"""


def summary(done, partial, registered_no_data):
    """What you'd otherwise have to open the page to read."""
    n = len(done)
    out = []
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    out.append(f"\n의도 기반 하이라이트 평가 · {stamp}")
    out.append("=" * 62)

    genders = defaultdict(int)
    ages = []
    for p, _ in done:
        genders[p.get("gender") or "미기재"] += 1
        if p.get("age"):
            ages.append(p["age"])
    who = ", ".join(f"{p['name']}({p.get('age')}{p.get('gender')})" for p, _ in done)
    gbits = " · ".join(f"{g} {c}명" for g, c in sorted(genders.items(), key=lambda kv: -kv[1]))
    age_bit = f"평균 {mean(ages):.0f}세, 범위 {min(ages)}–{max(ages)}" if ages else "-"
    out.append(f"완료 {n}명 ({gbits}, {age_bit})")
    out.append(f"  {who}" if who else "  (없음)")
    if partial:
        out.append("진행 중 " + ", ".join(f"{p['name']} {len(r)}/20" for p, r in partial))
    if registered_no_data:
        out.append(f"등록만 하고 응답 없음: {registered_no_data}명")

    if not done:
        out.append("\n아직 완료한 참가자가 없어 집계할 결과가 없습니다.")
        return "\n".join(out)

    units = units_pooled(done)
    out.append("\n조건별 평균 (7점 척도, 높을수록 좋음)")
    out.append(f"  {'조건':<12} {'Q1 관련도':>9} {'Q2 자연도':>9} {'Q3 만족도':>9}")
    for c in CONDITIONS:
        vals = [f"{mean([u[c][q] for u in units]):>9.2f}" for q, _, _ in QS]
        tail = "   ← 제안 방법" if c == "intentcut_s2" else ""
        out.append(f"  {COND_LABEL[c]:<12}" + "".join(vals) + tail)

    all_rows = [r for _, rows in done for r in rows]

    def set_mean(s, c, q):
        return mean([r[q] for r in all_rows if r["set_id"] == s and r["condition"] == c])

    out.append("\n영상별 Q3 만족도 — IntentCut vs 최고 베이스라인")
    for s, label in SET_LABEL.items():
        ic = set_mean(s, "intentcut_s2", "q3")
        if ic is None:
            continue
        best_c = max((c for c in CONDITIONS if c != "intentcut_s2"),
                     key=lambda c: set_mean(s, c, "q3") or 0)
        bv = set_mean(s, best_c, "q3")
        gap = ic - bv
        flag = "  ← 열세" if gap < 0 else ("  = 동률" if gap == 0 else "")
        out.append(f"  {label:<16} {ic:.1f} vs {bv:.1f} ({COND_LABEL[best_c]})  {gap:+.1f}{flag}")

    out.append(f"\nIntentCut vs 베이스라인 (참가자 단위 대응 검정, n={n})")
    enough = n >= 5
    if not enough:
        out.append(f"  * 참가자 {n}명이라 유의성 판정은 보류합니다 (5명부터 표시).")
    for q, qt, _ in QS:
        a = [u["intentcut_s2"][q] for u in units]
        for base in [c for c in CONDITIONS if c != "intentcut_s2"]:
            b = [u[base][q] for u in units]
            wp, tp = wilcoxon_and_t(a, b)
            d = cohens_dz(a, b)
            verdict = "표본 부족" if (wp is None or not enough) else (
                "유의" if (tp is not None and tp < 0.05) else "유의차 없음")
            out.append(
                f"  {qt.split(' · ')[0]} vs {COND_LABEL[base]:<10} "
                f"{mean(a) - mean(b):+.2f}  "
                f"d={f'{d:.2f}' if d is not None else '  - '}  "
                f"p={f'{tp:.3f}' if tp is not None else '  -  '}  [{verdict}]")
    return "\n".join(out)


if __name__ == "__main__":
    args = set(sys.argv[1:])
    done, partial = collect()
    OUT.write_text(build(done, partial))

    # participants who registered but never submitted a set
    load_env()
    people = fetch("participants", {"select": "id,name"})
    known = {p["id"] for p, _ in done} | {p["id"] for p, _ in partial}
    ghosts = sum(1 for p in people
                 if not EXCLUDE_NAME_RE.match(p.get("name") or "") and p["id"] not in known)

    if "--quiet" not in args:
        print(summary(done, partial, ghosts))
    print(f"\nHTML: {OUT}")

    if "--no-open" not in args:
        import subprocess
        subprocess.run(["open", str(OUT)], check=False)
        print("브라우저로 열었습니다. Claude 에게 공유하려면 report.html 을 다시 게시하세요.")
