#!/usr/bin/env python3
"""Pull user-test responses from Supabase and produce summary stats + plots.

Usage:
    export SUPABASE_URL=https://xxxx.supabase.co
    export SUPABASE_SERVICE_KEY=...   # service_role key (anon key has no read access)
    python3 analyze.py                # or: python3 analyze.py responses.json (local backup files)

Outputs into analysis/: summary CSVs, significance tests, and bar/box plots.
"""
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent / "analysis"
CONDITION_ORDER = ["intentcut_s2", "funclip", "random"]   # TimeChat dropped from the study
CONDITION_LABEL = {
    "intentcut_s2": "IntentCut (ours)",
    "funclip": "FunClip",
    "timechat": "TimeChat",
    "random": "Random",
}
QS = ["q1", "q2", "q3"]
EXPECTED = 5 * 3   # 5 videos x 3 conditions
# 개발/QA 중 쌓인 계정은 분석에서 제외 (실제 참가자만 집계)
EXCLUDE_NAME_RE = re.compile(r"^(QA[-_]|SETUP_TEST$|POLICY_TEST$|test\d*$)", re.IGNORECASE)
Q_LABEL = {"q1": "Q1 Intent relevance", "q2": "Q2 Editing naturalness", "q3": "Q3 Overall satisfaction"}


def load_secrets_file():
    env_path = Path(__file__).resolve().parent / "supabase_secrets.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def fetch_supabase() -> pd.DataFrame:
    import requests
    load_secrets_file()
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if key and "붙여넣기" in key:
        sys.exit("supabase_secrets.env의 SUPABASE_SERVICE_KEY를 실제 service_role 키로 바꿔주세요.")
    if not url or not key:
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY, or pass local backup JSON files.")
    rows, offset, page = [], 0, 1000
    while True:
        r = requests.get(
            f"{url}/rest/v1/responses",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Range": f"{offset}-{offset + page - 1}"},
            params={"select": "*", "order": "id.asc"},
            timeout=30,
        )
        r.raise_for_status()
        chunk = r.json()
        rows.extend(chunk)
        if len(chunk) < page:
            break
        offset += page
    return pd.DataFrame(rows)


def load_local(paths) -> pd.DataFrame:
    rows = []
    for p in paths:
        data = json.loads(Path(p).read_text())
        if isinstance(data, dict):
            rows.extend(data.get("pendingUploads") or data.get("responses") or [])
        else:
            rows.extend(data)
    return pd.DataFrame(rows)


def main():
    df = load_local(sys.argv[1:]) if len(sys.argv) > 1 else fetch_supabase()
    if df.empty:
        sys.exit("No responses found.")
    OUT.mkdir(exist_ok=True)

    if "participant_name" in df.columns:
        is_test = df.participant_name.fillna("").str.match(EXCLUDE_NAME_RE)
        if is_test.any():
            dropped = sorted(df.loc[is_test, "participant_name"].fillna("(이름없음)").unique())
            print(f"테스트 계정 제외: {', '.join(dropped)}")
            df = df[~is_test]
        # 참가자 행이 없어 이름이 비어 있는 응답도 제외
        noname = df.participant_name.isna()
        if noname.any():
            print(f"이름 없는 응답 {int(noname.sum())}행 제외")
            df = df[~noname]

    # Writes are append-only, so a participant who went back and edited a set has
    # several rows for it. Keep the newest row per (participant, set, condition).
    df = df[df.condition.isin(CONDITION_ORDER)]   # TimeChat rows stay in the DB, out of the analysis
    df = df.sort_values("id" if "id" in df.columns else "set_index")
    before = len(df)
    df = df.drop_duplicates(subset=["participant_id", "set_id", "condition"], keep="last")
    if before != len(df):
        print(f"수정본 {before - len(df)}행을 최신 응답으로 대체했습니다.")

    # keep only participants with complete data (5 sets x 4 conditions = 20 rows)
    counts = df.groupby("participant_id").size()
    complete_ids = counts[counts == EXPECTED].index
    incomplete = counts[counts != EXPECTED]
    if len(incomplete):
        names = df.groupby("participant_id").participant_name.first()
        print(f"⚠️  미완료 참가자 {len(incomplete)}명 제외: "
              + ", ".join(f"{names.get(pid, pid[:8])}({c}행)" for pid, c in incomplete.items()))
    df = df[df.participant_id.isin(complete_ids)].copy()
    n = df.participant_id.nunique()
    if n == 0:
        sys.exit("아직 완료한 참가자가 없습니다 (1명당 15개 응답 필요).")
    print(f"participants analyzed: {n} ({len(df)} responses)")
    df.to_csv(OUT / "responses_raw.csv", index=False)

    # ── condition-level summary ──────────────────────────────
    summary = df.groupby("condition")[QS].agg(["mean", "std"]).round(2)
    summary = summary.reindex(CONDITION_ORDER)
    summary.to_csv(OUT / "summary_by_condition.csv")
    print("\n=== mean ± std by condition ===")
    print(summary)

    per_set = df.groupby(["set_id", "condition"])[QS].mean().round(2).reset_index()
    per_set.to_csv(OUT / "summary_by_set_condition.csv", index=False)

    # ── paired significance tests: ours vs each baseline ────
    from scipy import stats
    lines = []
    piv = df.pivot_table(index=["participant_id", "set_id"], columns="condition", values=QS)
    for q in QS:
        for base in [c for c in CONDITION_ORDER if c != "intentcut_s2"]:
            a = piv[(q, "intentcut_s2")]
            b = piv[(q, base)]
            mask = a.notna() & b.notna()
            head = (f"{q} intentcut_s2 vs {base}: "
                    f"mean {a[mask].mean():.2f} vs {b[mask].mean():.2f}")
            if mask.sum() < 2 or (a[mask] - b[mask]).abs().sum() == 0:
                lines.append(f"{head} | 검정 불가 (차이가 없거나 표본 부족)")
                continue
            w = stats.wilcoxon(a[mask], b[mask])
            t = stats.ttest_rel(a[mask], b[mask])
            lines.append(f"{head} | wilcoxon p={w.pvalue:.4f} | paired-t p={t.pvalue:.4f}")
    report = "\n".join(lines)
    (OUT / "significance.txt").write_text(report + "\n")
    print("\n=== paired tests (per participant x set) ===")
    print(report)

    # ── plots ────────────────────────────────────────────────
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5), sharey=True)
    x = np.arange(len(CONDITION_ORDER))
    for ax, q in zip(axes, QS):
        means = [df[df.condition == c][q].mean() for c in CONDITION_ORDER]
        sems = [df[df.condition == c][q].sem() for c in CONDITION_ORDER]
        ax.bar(x, means, yerr=sems, capsize=4,
               color=["#2f6bff", "#9aa7bd", "#9aa7bd", "#9aa7bd"])
        ax.set_xticks(x)
        ax.set_xticklabels([CONDITION_LABEL[c] for c in CONDITION_ORDER], rotation=20)
        ax.set_title(Q_LABEL[q])
        ax.set_ylim(1, 7)
        ax.grid(axis="y", alpha=.3)
    fig.suptitle(f"User test A — mean ratings (N={n}, error bars: SEM)")
    fig.tight_layout()
    fig.savefig(OUT / "means_by_condition.png", dpi=150)

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5), sharey=True)
    for ax, q in zip(axes, QS):
        data = [df[df.condition == c][q].dropna() for c in CONDITION_ORDER]
        ax.boxplot(data)
        ax.set_xticklabels([CONDITION_LABEL[c] for c in CONDITION_ORDER])
        ax.set_title(Q_LABEL[q])
        ax.tick_params(axis="x", rotation=20)
        ax.grid(axis="y", alpha=.3)
    fig.suptitle(f"User test A — rating distributions (N={n})")
    fig.tight_layout()
    fig.savefig(OUT / "boxplots_by_condition.png", dpi=150)

    print(f"\nwrote outputs to {OUT}/")


if __name__ == "__main__":
    main()
