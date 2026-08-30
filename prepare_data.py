#!/usr/bin/env python3
"""Re-encode the 5 final sets into web-friendly mp4s under data/ and build manifest.json.

Source: renders_FINAL_0828. Each set has 4 condition videos. Output files get
neutral names (v1..v4, fixed per-set assignment recorded in manifest.json) so
participants can't infer the method from the URL.
"""
import json
import random
import subprocess
import sys
from pathlib import Path

SRC_ROOT = Path("/mnt/localssd/mink/Aframe/99_resource/00_input_video/evaluation/Data/qual_results/renders_FINAL_0828")
OUT_ROOT = Path(__file__).resolve().parent / "data"

# (set_id, source folder, intent folder, Korean intent shown to participants)
SETS = [
    ("baseball",     "baseball_pr_mexico_0827_full",         "0827_1", "양 팀이 홈런을 치는 장면을 모두 보여줘"),
    ("harp_seal",    "harp_seal_race_against_time_full",     "c2e",    "얼음 위 갓 태어난 새끼 물범의 클로즈업 장면을 연달아 보여줘"),
    ("worldcup",     "worldcup_2022_greatest_final_full",    "p2e",    "메시의 골 장면과 세레머니/축하하는 장면을 보여줘"),
    ("spiderman",    "spiderman_0824_full",                  "0826_1", "스파이더맨이 거미줄을 쏘고 사용하는 장면을 보여줘"),
    ("interstellar", "interstellar_wave_0827_full",          "0827_4", "두 번째 파도에서 탈출하는 장면을 보여줘"),
]

CONDITIONS = ["intentcut_s2", "funclip", "timechat", "random"]

FFMPEG = [
    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
]
ENCODE = [
    "-vf", "scale='min(1280,iw)':-2",
    "-c:v", "libx264", "-preset", "medium", "-crf", "23",
    "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "128k",
    "-movflags", "+faststart",
]


def main():
    rng = random.Random(20260829)  # fixed seed -> stable file assignment
    manifest = {"sets": []}
    for set_id, folder, bid, intent_ko in SETS:
        src_dir = SRC_ROOT / folder / bid
        info = json.loads((src_dir / "info.json").read_text())
        out_dir = OUT_ROOT / set_id
        out_dir.mkdir(parents=True, exist_ok=True)

        conds = CONDITIONS[:]
        rng.shuffle(conds)  # fixed per-set condition -> vN assignment
        videos = {}
        for i, cond in enumerate(conds, start=1):
            src = src_dir / f"{cond}.mp4"
            dst = out_dir / f"v{i}.mp4"
            if not src.exists():
                sys.exit(f"missing source: {src}")
            if not dst.exists():
                print(f"[encode] {set_id}/{cond} -> {dst.name}", flush=True)
                subprocess.run(FFMPEG + ["-i", str(src)] + ENCODE + [str(dst)], check=True)
            videos[f"v{i}"] = cond

        manifest["sets"].append({
            "set_id": set_id,
            "source_video": info["vid"],
            "bid": bid,
            "intent_en": info["brief"],
            "intent_ko": intent_ko,
            "videos": videos,  # file stem -> condition (kept server-side only, not shown to participants)
        })

    (OUT_ROOT.parent / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    print("manifest.json written")
    subprocess.run(["du", "-sh", str(OUT_ROOT)])


if __name__ == "__main__":
    main()
