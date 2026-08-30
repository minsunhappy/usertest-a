# User Test A — Intent-based Video Highlight Evaluation

Web-based user study comparing 4 highlight-generation conditions
(`intentcut_s2`, `funclip`, `timechat`, `random`) over 5 videos.
Each participant watches all 5 sets × 4 videos (set order and within-set
condition order are randomized per participant) and answers 3 questions
(7-point Likert) after every video.

## Structure

```
index.html            intro + participant info
test.html             main flow: [intent screen → 4 × (video + Q1–Q3)] × 5 sets
final.html            completion + upload flush / JSON backup
config.js             Supabase URL/key + question definitions
manifest.json         sets, intents, blinded file→condition mapping (generated)
data/<set>/v[1-4].mp4 re-encoded highlight videos (generated)
prepare_data.py       encodes source renders into data/ and writes manifest.json
supabase_schema.sql   run in Supabase SQL editor (tables + insert-only RLS)
analyze.py            pulls responses and produces stats + plots into analysis/
```

## Setup

1. **Data** (already done if `data/` is populated):
   `python3 prepare_data.py` — re-encodes the renders_FINAL_0828 sources to
   720p H.264 and writes `manifest.json`. Files are named `v1..v4` per set so
   participants can't infer the condition from the URL.

2. **Supabase**: create a project, run `supabase_schema.sql` in the SQL editor,
   then fill `SUPABASE_URL` / `SUPABASE_ANON_KEY` in `config.js`.
   Without keys the site still works; responses stay local and the final page
   offers a JSON download instead.

3. **Deploy**: push to GitHub and enable Pages (Settings → Pages → deploy from
   `main`). Or test locally: `python3 -m http.server 8000`.

## Analysis

```bash
export SUPABASE_URL=https://xxxx.supabase.co
export SUPABASE_SERVICE_KEY=...        # service_role key (Settings → API)
python3 analyze.py
# or from local backup files: python3 analyze.py backup1.json backup2.json
```

Outputs in `analysis/`: raw CSV, per-condition and per-set summaries,
Wilcoxon/paired-t tests (ours vs each baseline), bar + box plots.
Participants with incomplete data (≠20 responses) are excluded automatically.

## Anti-skip measures

Currently **off**: `REQUIRE_FULL_WATCH = false` in `config.js`. Questions are
answerable right away and seeking is unrestricted; participants are only asked
in the instructions to watch each video fully. Flip the flag to `true` to
re-enable gating (questions unlock on `ended`, forward seeks snap back).

Playback rate is always locked to 1×. Progress is stored in `localStorage`, so a
refresh resumes where the participant left off, and the "← 이전 세트" button
(or "← 참가자 정보 수정" on set 1) goes back with answers editable; each set's
responses upload on advance with a retry queue flushed on the final page.
