#!/usr/bin/env python3
"""token-per-prompt: optional status-line wrapper.

Claude Code allows exactly one `statusLine` command, so to show our per-prompt
token line *and* an existing status line (e.g. claude-dashboard) we compose them
instead of competing: run the wrapped command first, then print our line below.

Status-line input arrives as JSON on stdin (contains `transcript_path`). We read
it once, forward it to the wrapped command, and reuse it for our own tokens.

Configured via config.json:
  - "chain_command": argv list of the existing status line to run first (optional)
  - display / plan: same keys report.py uses
"""
import sys
import os
import json
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report import last_turn_usage, format_line, load_config, state_path, PLAN_5H_LIMIT
import calibrate


CLIMBING = "🔄"  # turn still generating / climbing
DONE = "✅"      # value finalized by the Stop hook


def turn_indicator(usage, script_dir):
    """🔄 while a turn is still climbing, ✅ once the Stop hook pinned it done.

    Keyed on the turn's prompt position (last_user_idx) so it is immune to the
    final assistant message not being flushed yet when Stop fires.
    """
    last_user_idx = usage.get("last_user_idx", -1)
    has_response = usage.get("last_asst_idx", -1) > last_user_idx
    done_user_idx = None
    try:
        with open(state_path(script_dir), encoding="utf-8") as fh:
            done_user_idx = json.load(fh).get("done_user_idx")
    except Exception:
        pass
    if has_response and last_user_idx >= 0 and last_user_idx == done_user_idx:
        return DONE
    return CLIMBING


def main():
    raw = sys.stdin.read()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cfg = load_config(script_dir)

    lines = []

    # 1) run the chained status line (dashboard) first, on top.
    chain = cfg.get("chain_command")
    if chain:
        try:
            r = subprocess.run(
                chain, input=raw, capture_output=True, text=True,
                encoding="utf-8", timeout=15,
            )
            top = (r.stdout or "").rstrip("\n")
            if top:
                lines.append(top)
        except Exception:
            pass

    # 2) our per-prompt token line, below.
    try:
        hook = json.loads(raw) if raw.strip() else {}
    except Exception:
        hook = {}
    transcript = hook.get("transcript_path")
    if transcript and os.path.exists(transcript):
        try:
            usage = last_turn_usage(transcript)
            # Always render — even at 0 steps — so a new question visibly
            # resets to 0 and then climbs as the answer's steps land, instead
            # of the line vanishing until the turn finishes. The leading
            # indicator replaces the 📊 emoji: 🔄 (climbing) vs ✅ (finalized).
            # effective_limit only READS calib.json here (no API call).
            limit = calibrate.effective_limit(cfg, script_dir, PLAN_5H_LIMIT)
            indicator = turn_indicator(usage, script_dir)
            lines.append(indicator + " "
                         + format_line(usage, cfg, emoji=False, limit=limit,
                                       theme=cfg.get("theme")))
        except Exception:
            pass

    sys.stdout.write("\n".join(lines))


if __name__ == "__main__":
    main()
