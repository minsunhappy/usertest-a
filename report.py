#!/usr/bin/env python3
"""Pull the study data from Supabase and write a self-contained results page.

    /Users/sunny/miniforge3/bin/python report.py           -> report.html

Reads credentials from supabase_secrets.env. QA/test accounts are excluded and
append-only duplicates collapse to the newest row per (participant, set, condition).
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

CONDITIONS = ["intentcut_s2", "funclip", "timechat", "random"]
COND_LABEL = {
    "intentcut_s2": "IntentCut",
    "funclip": "FunClip",
    "timechat": "TimeChat",
    "random": "Random",
}
# categorical slots 1-4 of the validated reference palette (light / dark steps)
COND_HUE = {
    "intentcut_s2": ("#2a78d6", "#3987e5"),
    "funclip": ("#eb6834", "#d95926"),
    "timechat": ("#1baf7a", "#199e70"),
    "random": ("#eda100", "#c98500"),
}
QS = [
    ("q1", "Q1 · 의도 관련도", "장면들이 주어진 의도와 얼마나 관련 있었나"),
    ("q2", "Q2 · 편집 자연도", "하나의 자연스러운 편집처럼 느껴졌나"),
    ("q3", "Q3 · 전반적 만족도", "전반적인 시청 경험에 얼마나 만족했나"),
]
SET_LABEL = {
    "baseball": "야구 (홈런)",
    "harp_seal": "물범 (클로즈업)",
    "worldcup": "월드컵 (메시)",
    "spiderman": "스파이더맨 (거미줄)",
    "interstellar": "인터스텔라 (파도)",
}
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
    return sum(xs) / len(xs) if xs else None


def stdev(xs):
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
    # newest row wins (edits are appended, never updated)
    latest = {}
    for r in resp:
        if r["participant_id"] not in real:
            continue
        latest[(r["participant_id"], r["set_id"], r["condition"])] = r

    by_person = defaultdict(list)
    for r in latest.values():
        by_person[r["participant_id"]].append(r)

    done, partial = [], []
    for pid, rows in by_person.items():
        (done if len(rows) == 20 else partial).append((real[pid], rows))
    done.sort(key=lambda t: t[0]["created_at"])
    partial.sort(key=lambda t: t[0]["created_at"])
    return real, done, partial


# ── stats ───────────────────────────────────────────────────────────────
def per_participant_means(done):
    """{condition: {q: [one mean per participant]}} — each participant's 5 sets averaged."""
    out = {c: {q: [] for q, _, _ in QS} for c in CONDITIONS}
    for _, rows in done:
        for c in CONDITIONS:
            vals = [r for r in rows if r["condition"] == c]
            for q, _, _ in QS:
                out[c][q].append(mean([v[q] for v in vals]))
    return out


def wilcoxon_and_t(a, b):
    """Paired tests without scipy at import time; returns (w_p, t_p) or (None, None)."""
    diffs = [x - y for x, y in zip(a, b)]
    if len(diffs) < 2 or all(d == 0 for d in diffs):
        return None, None
    from scipy import stats
    return stats.wilcoxon(a, b).pvalue, stats.ttest_rel(a, b).pvalue


def cohens_dz(a, b):
    diffs = [x - y for x, y in zip(a, b)]
    sd = stdev(diffs)
    return (mean(diffs) / sd) if sd else None


# ── rendering ───────────────────────────────────────────────────────────
def esc(s):
    return html.escape(str(s))


def bar_chart(title, subtitle, values, compact=False):
    """One bar per condition with direct value labels (relief rule).

    values: {condition: (mean, sem_or_0)}
    """
    W, H = (360, 240) if not compact else (300, 200)
    pad_l, pad_r, pad_t, pad_b = 34, 10, 26, 46
    plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b
    lo, hi = 1, 7
    slot = plot_w / len(CONDITIONS)
    bar_w = slot - 16

    def y(v):
        return pad_t + plot_h * (1 - (v - lo) / (hi - lo))

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(title)} 조건별 평균">']
    parts.append(f'<title>{esc(title)}</title>')
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
            f'<g class="bar" style="--hue:var(--c{i + 1});">'
            f'<title>{esc(COND_LABEL[c])} · 평균 {m:.2f}{f" ± {sem:.2f}" if sem else ""}</title>'
            f'<rect x="{x:.1f}" y="{top:.1f}" width="{bar_w:.1f}" '
            f'height="{pad_t + plot_h - top:.1f}" rx="4" ry="4"/>'
        )
        if sem:
            parts.append(
                f'<line class="err" x1="{x + bar_w / 2:.1f}" x2="{x + bar_w / 2:.1f}" '
                f'y1="{y(min(7, m + sem)):.1f}" y2="{y(max(1, m - sem)):.1f}"/>'
            )
        parts.append(
            f'<text class="val" x="{x + bar_w / 2:.1f}" y="{top - 7:.1f}" '
            f'text-anchor="middle">{m:.1f}</text></g>'
        )
        parts.append(
            f'<text class="cond" x="{x + bar_w / 2:.1f}" y="{H - pad_b + 18}" '
            f'text-anchor="middle">{esc(COND_LABEL[c])}</text>'
        )
    parts.append("</svg>")
    return (f'<figure class="chart"><figcaption><h3>{esc(title)}</h3>'
            f'<p>{esc(subtitle)}</p></figcaption>{"".join(parts)}</figure>')


def build(real, done, partial):
    n = len(done)
    ppm = per_participant_means(done)

    stats = {}
    for c in CONDITIONS:
        stats[c] = {}
        for q, _, _ in QS:
            vals = [v for v in ppm[c][q] if v is not None]
            m = mean(vals)
            sem = (stdev(vals) / len(vals) ** 0.5) if len(vals) > 1 else 0.0
            stats[c][q] = (m, sem)

    # demographics over completed participants
    genders = defaultdict(int)
    ages = []
    for p, _ in done:
        genders[p.get("gender") or "미기재"] += 1
        if p.get("age"):
            ages.append(p["age"])

    err_note = " · 오차막대 = 표준오차" if n > 1 else ""
    charts = "".join(
        bar_chart(t, s + err_note, {c: stats[c][q] for c in CONDITIONS})
        for q, t, s in QS)

    # per-set small multiples + tables
    set_ids = [s for s in SET_LABEL if any(r["set_id"] == s for _, rows in done for r in rows)]

    def set_mean(set_id, cond, q):
        vals = [r[q] for _, rows in done for r in rows
                if r["set_id"] == set_id and r["condition"] == cond]
        return mean(vals)

    set_charts = "".join(
        bar_chart(SET_LABEL[s], "Q1 의도 관련도",
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
            body.append(f'<tr><th scope="row"><span class="chip" style="background:var(--c{i + 1})"></span>'
                        f'{esc(COND_LABEL[c])}</th>{cells}</tr>')
        set_tables.append(
            f'<div class="scroll"><table class="data"><caption>{esc(qt)} · 세트별 평균</caption>'
            f'<thead><tr><th scope="col">조건</th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')
    set_tables = "".join(set_tables)

    # per-condition table (also the relief/table view for the light-mode contrast WARN)
    head = "".join(f"<th>{esc(t.split(' · ')[0])}</th>" for _, t, _ in QS)
    rows = []
    for i, c in enumerate(CONDITIONS):
        cells = "".join(
            f"<td>{stats[c][q][0]:.2f}<span class='sd'> ± {stats[c][q][1]:.2f}</span></td>"
            if stats[c][q][0] is not None else "<td>—</td>" for q, _, _ in QS)
        rows.append(f'<tr><th scope="row"><span class="chip" style="background:var(--c{i + 1})"></span>'
                    f'{esc(COND_LABEL[c])}</th>{cells}</tr>')
    table = (f'<table class="data"><caption>조건별 평균 (참가자 {n}명 · ± 표준오차)</caption>'
             f'<thead><tr><th scope="col">조건</th>{head}</tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table>')

    # paired comparisons vs each baseline
    sig_rows = []
    for q, qt, _ in QS:
        a = [v for v in ppm["intentcut_s2"][q] if v is not None]
        for base in ["funclip", "timechat", "random"]:
            b = [v for v in ppm[base][q] if v is not None]
            wp, tp = wilcoxon_and_t(a, b)
            d = cohens_dz(a, b)
            diff = mean(a) - mean(b) if a and b else None
            # p값은 참가자가 어느 정도 모여야 의미가 있다
            verdict = "표본 부족" if (wp is None or n < 5) else (
                "유의" if (tp is not None and tp < 0.05) else "유의차 없음")
            cls = "sig" if verdict == "유의" else ("na" if verdict == "표본 부족" else "ns")
            sig_rows.append(
                f'<tr><td>{esc(qt.split(" · ")[0])}</td><td>vs {esc(COND_LABEL[base])}</td>'
                f'<td class="num">{f"{diff:+.2f}" if diff is not None else "—"}</td>'
                f'<td class="num">{f"{d:.2f}" if d is not None else "—"}</td>'
                f'<td class="num">{f"{wp:.3f}" if wp is not None else "—"}</td>'
                f'<td class="num">{f"{tp:.3f}" if tp is not None else "—"}</td>'
                f'<td><span class="tag {cls}">{verdict}</span></td></tr>')
    sig = (f'<table class="data"><caption>IntentCut vs 베이스라인 (참가자 단위 대응 검정)</caption>'
           f'<thead><tr><th scope="col">문항</th><th scope="col">비교</th>'
           f'<th scope="col">평균차</th><th scope="col">Cohen d<sub>z</sub></th>'
           f'<th scope="col">Wilcoxon p</th><th scope="col">paired-t p</th>'
           f'<th scope="col">판정</th></tr></thead><tbody>{"".join(sig_rows)}</tbody></table>')

    # roster (names masked — the page is shareable)
    def mask(name):
        name = (name or "").strip()
        return name[0] + "○" * max(1, len(name) - 1) if name else "익명"

    roster = []
    for p, rows in done + partial:
        n_rows = len(rows)
        state = ("완료" if n_rows == 20 else f"진행 중 {n_rows}/20")
        cls = "done" if n_rows == 20 else "wip"
        roster.append(f'<tr><td>{esc(mask(p.get("name")))}</td><td class="num">{esc(p.get("age") or "—")}</td>'
                      f'<td>{esc(p.get("gender") or "—")}</td>'
                      f'<td>{esc(p["created_at"][:16].replace("T", " "))}</td>'
                      f'<td><span class="tag {cls}">{state}</span></td></tr>')
    roster_tbl = (f'<table class="data"><caption>참가자 명단 (이름은 가림 처리)</caption>'
                  f'<thead><tr><th scope="col">참가자</th><th scope="col">나이</th>'
                  f'<th scope="col">성별</th><th scope="col">시작 시각 (UTC)</th>'
                  f'<th scope="col">상태</th></tr></thead><tbody>{"".join(roster)}</tbody></table>')

    gender_bits = " · ".join(f"{g} {c}명" for g, c in sorted(genders.items(), key=lambda kv: -kv[1]))
    age_bit = f"{mean(ages):.0f}<em>세</em>" if ages else "—"
    age_sub = f"범위 {min(ages)}–{max(ages)}세" if ages else "완료 참가자 기준"
    warn = ""
    if n < 2:
        warn = ('<p class="warn">참가자가 ' + str(n) + '명뿐이라 아래 검정은 아직 의미가 없습니다. '
                '평균은 경향 확인용으로만 보세요.</p>')
    elif n < 10:
        warn = ('<p class="warn">참가자 ' + str(n) + '명 기준입니다. 표본이 작아 p값은 잠정치로 보세요.</p>')

    stamp = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    tiles = f"""
    <div class="tiles">
      <div class="tile"><span class="k">완료 참가자</span><strong>{n}<em>명</em></strong>
        <span class="sub">{esc(gender_bits) or "—"}</span></div>
      <div class="tile"><span class="k">진행 중</span><strong>{len(partial)}<em>명</em></strong>
        <span class="sub">20문항 미완료</span></div>
      <div class="tile"><span class="k">평균 나이</span><strong>{age_bit}</strong>
        <span class="sub">{esc(age_sub)}</span></div>
      <div class="tile"><span class="k">수집 응답</span><strong>{n * 20}<em>개</em></strong>
        <span class="sub">5세트 × 4조건 × {n}명</span></div>
    </div>"""

    return TEMPLATE.format(stamp=esc(stamp), tiles=tiles, warn=warn, charts=charts,
                           table=table, sig=sig, set_charts=set_charts,
                           set_tables=set_tables, roster=roster_tbl)


TEMPLATE = """<title>의도 기반 하이라이트 평가 결과</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Gowun+Batang:wght@400;700&family=IBM+Plex+Sans+KR:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {{
  color-scheme: light;
  --bg: #f6f7f9;
  --surface: #ffffff;
  --line: #e3e6ec;
  --ink: #14181f;
  --ink-2: #4a5261;
  --ink-3: #6f7787;
  --accent: #1f4f96;
  --c1: #2a78d6; --c2: #eb6834; --c3: #1baf7a; --c4: #eda100;
  --good-bg: #e7f4ec; --good-ink: #16653a;
  --ns-bg: #eef0f4;  --ns-ink: #4a5261;
  --wip-bg: #fdf1e3; --wip-ink: #8a4d10;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --bg: #14161a;
    --surface: #1c1f25;
    --line: #2c313a;
    --ink: #f2f4f8;
    --ink-2: #b9c0cd;
    --ink-3: #8d95a4;
    --accent: #86b3f0;
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
header.top {{ border-bottom: 1px solid var(--line); padding-bottom: 22px; margin-bottom: 26px; }}
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
.val {{
  fill: var(--ink); font-size: 12px; font-weight: 500;
  font-family: "IBM Plex Mono", monospace;
}}
.bar rect {{ fill: var(--hue); transition: opacity .12s; }}
.bar:hover rect {{ opacity: .78; }}
.err {{ stroke: var(--ink-2); stroke-width: 2; }}
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
.chip {{
  display: inline-block; width: 10px; height: 10px; border-radius: 3px;
  margin-right: 8px; vertical-align: baseline;
}}
.tag {{
  display: inline-block; padding: 2px 9px; border-radius: 999px;
  font-size: .8rem; font-weight: 600; font-family: "IBM Plex Sans KR", sans-serif;
}}
.tag.sig, .tag.done {{ background: var(--good-bg); color: var(--good-ink); }}
.tag.ns, .tag.na {{ background: var(--ns-bg); color: var(--ns-ink); }}
.tag.wip {{ background: var(--wip-bg); color: var(--wip-ink); }}
footer {{ margin-top: 46px; padding-top: 18px; border-top: 1px solid var(--line);
  color: var(--ink-3); font-size: .86rem; }}
@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; }} }}
</style>

<div class="page">
  <header class="top">
    <p class="eyebrow">사용자 테스트 A</p>
    <h1>의도 기반 하이라이트 생성 — 평가 결과</h1>
    <p class="stamp">마지막 갱신 {stamp} · 7점 척도, 점수가 높을수록 좋음</p>
  </header>

  {tiles}
  {warn}

  <h2>조건별 평균 점수</h2>
  <p class="lede">참가자마다 5개 세트를 평균한 뒤, 참가자 간 평균을 냈습니다.</p>
  <div class="charts">{charts}</div>

  <h2>수치와 유의성</h2>
  <p class="lede">위 그래프와 같은 값이며, 아래는 IntentCut과 각 베이스라인의 대응 비교입니다.</p>
  <div class="tables">
    <div class="scroll">{table}</div>
    <div class="scroll">{sig}</div>
  </div>

  <h2>영상별 결과</h2>
  <p class="lede">원본 영상 5개 각각에서 조건이 어떻게 갈렸는지 봅니다. 막대는 Q1 의도 관련도입니다.</p>
  <div class="charts">{set_charts}</div>
  <div class="tables" style="margin-top:22px">{set_tables}</div>

  <h2>참가자</h2>
  <p class="lede">개인정보 보호를 위해 이름은 첫 글자만 표시합니다.</p>
  <div class="tables"><div class="scroll">{roster}</div></div>

  <footer>
    IntentCut = 제안 방법 · FunClip / TimeChat / Random = 베이스라인.
    수정된 응답은 최신 제출본만 집계하며, 개발·QA 계정은 제외했습니다.
  </footer>
</div>
"""


if __name__ == "__main__":
    real, done, partial = collect()
    OUT.write_text(build(real, done, partial))
    print(f"완료 참가자 {len(done)}명 / 진행 중 {len(partial)}명 → {OUT}")
