#!/usr/bin/env bash
# 결과 한 방에 보기:  ./results.sh
#   --quiet     터미널 요약 없이 HTML 만
#   --no-open   브라우저 자동 실행 안 함
set -euo pipefail
cd "$(dirname "$0")"

# pandas/scipy 가 있는 파이썬을 찾는다
for candidate in \
    "$HOME/miniforge3/bin/python" \
    "$HOME/miniforge3/envs/vr_step2/bin/python" \
    "$(command -v python3 || true)"
do
    if [ -x "$candidate" ] && "$candidate" -c "import requests, scipy" >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done

if [ -z "${PY:-}" ]; then
    echo "requests 와 scipy 가 설치된 파이썬을 찾지 못했습니다." >&2
    echo "설치: python3 -m pip install requests scipy" >&2
    exit 1
fi

exec "$PY" report.py "$@"
