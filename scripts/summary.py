#!/usr/bin/env python3
"""token-per-prompt: per-question usage report for the current session.

Collects the token totals that appeared under each answer and prints them as one
table (시각 · 출력 · 새컨텍스트 · 5시간% · 질문) with a summary row.

Runs fully local off the session transcript. Cross-platform: the current session
is auto-detected as the most recently written transcript under ~/.claude/projects.
"""
import sys
import os
import glob
import argparse
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report import (iter_turns, billed_tokens, load_config,  # noqa: E402
                    PLAN_5H_LIMIT)
import calibrate  # noqa: E402


def find_current_transcript():
    """Resolve the *current* session's transcript.

    1) CLAUDE_CODE_SESSION_ID → the transcript is named "<session-id>.jsonl", so
       this is exact and unambiguous even with several sessions open at once.
    2) Fallback: the most recently modified transcript (the session that just
       wrote this command). Used only if the env var is unavailable."""
    base = os.path.expanduser(os.path.join("~", ".claude", "projects"))
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if sid:
        matches = glob.glob(os.path.join(base, "*", sid + ".jsonl"))
        if matches:
            return matches[0]
    files = glob.glob(os.path.join(base, "*", "*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def fmt_ts(ts):
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%H:%M")
    except Exception:
        return ts[11:16] if len(ts) >= 16 else ""


def main():
    ap = argparse.ArgumentParser(description="질문별 토큰 사용량 표")
    ap.add_argument("--session", help="특정 transcript(.jsonl) 경로 (기본: 현재 세션 자동탐지)")
    args = ap.parse_args()

    transcript = args.session or find_current_transcript()
    if not transcript or not os.path.exists(transcript):
        print("세션 transcript를 찾지 못했습니다.")
        return

    cfg = load_config()
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    limit, cal_state = calibrate.calibration_status(cfg, None, PLAN_5H_LIMIT, sid)
    turns = iter_turns(transcript)
    if not turns:
        print("집계할 질문이 없습니다.")
        return

    print(f"{'시각':<6}{'출력':>9}{'새컨텍스트':>12}{'5시간%':>9}   질문")
    print("─" * 68)
    tot_out = tot_cc = tot_billed = 0
    for t in turns:
        b = billed_tokens(t)
        pct = (b / limit * 100) if limit else 0
        tot_out += t["output"]
        tot_cc += t["cache_creation"]
        tot_billed += b
        q = t["q"][:30]
        print(f"{fmt_ts(t['ts']):<6}{t['output']:>9,}{t['cache_creation']:>12,}"
              f"{pct:>8.2f}%   {q}")
    print("─" * 68)
    tot_pct = (tot_billed / limit * 100) if limit else 0
    print(f"{'합계':<6}{tot_out:>9,}{tot_cc:>12,}{tot_pct:>8.2f}%   "
          f"({len(turns)}개 질문)")

    src = {"ok": "실측보정", "learned": "이전보정값(재확인중)",
           "cold": "기본추정(보정중)", "static": "기본추정"}.get(cal_state, "기본추정")
    print(f"\n※ % = (출력+새컨텍스트+입력) ÷ 5시간리밋({int(limit):,}, {src}) · 캐시읽기 제외")


if __name__ == "__main__":
    main()
