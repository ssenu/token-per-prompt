#!/usr/bin/env python3
"""token-per-prompt: Stop-hook script.

Reads the Claude Code hook JSON on stdin, opens the session transcript, sums the
token usage for the turn that just finished (all assistant messages since the
last user message), and emits a one-line summary via the hook `systemMessage`.

100% local: it only reads the local .jsonl transcript. No network, no tokens.
"""
import sys
import json
import os
import time

# ---- runtime data dir (config/state/calibration live here, not with the code
# so the skill can be installed read-only and shared across machines) ----
DATA_DIR = os.path.expanduser(os.path.join("~", ".claude", "token-per-prompt"))


def data_file(name):
    """Absolute path inside the per-user data dir (created on demand)."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception:
        pass
    return os.path.join(DATA_DIR, name)


# ---- plan limits (per-window token budgets used for the "B" percentage) ----
# Rough starting denominators for the 5-hour window, empirically anchored (Pro's
# effective budget observed near ~600K billed tokens; Max tiers scale 5x/20x).
# These are only defaults — with autocalibrate on, the real limit is derived
# from live server utilization (see calibrate.py) and overrides these.
PLAN_5H_LIMIT = {
    "pro": 600_000,
    "max5": 3_000_000,
    "max20": 12_000_000,
}

DEFAULT_CONFIG = {"display": "basic", "plan": "pro"}

# ANSI truecolor themes for the status-line rendering. `None`/"default" = plain
# (used by the under-answer hook, whose systemMessage shouldn't carry ANSI).
THEMES = {
    "tokyonight": {
        "label": "\x1b[38;2;122;162;247m",   # blue  #7aa2f7
        "accent": "\x1b[38;2;158;206;106m",  # green #9ece6a
        "num": "\x1b[38;2;192;202;245m",     # fg    #c0caf5
        "pct": "\x1b[38;2;224;175;104m",     # yellow #e0af68
        "muted": "\x1b[38;2;86;95;137m",     # comment #565f89
        "dim": "\x1b[2m",
        "reset": "\x1b[0m",
    },
}


def _painter(theme_name):
    """Return a colorize(role, text) function for the given theme (or identity)."""
    t = THEMES.get(theme_name)
    if not t:
        return lambda role, s: s
    return lambda role, s: f"{t.get(role, '')}{s}{t['reset']}"


def load_config(script_dir=None):
    """Load config.json from the per-user data dir (script_dir kept for API compat)."""
    try:
        with open(data_file("config.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        return {**DEFAULT_CONFIG, **data}
    except Exception:
        return dict(DEFAULT_CONFIG)


def last_turn_usage(transcript_path):
    """Sum usage for assistant messages emitted since the last user message.

    Also returns line positions used to tell whether the turn is still open
    (`last_user_idx` newer than `last_asst_idx`) and the id of the final
    assistant message (`last_asst_id`) so the Stop marker can pin "done".
    """
    out = cc = cr = inp = 0
    steps = 0
    seen = set()
    idx = -1
    last_user_idx = -1
    last_asst_idx = -1
    last_asst_id = None
    # Walk the file; reset the accumulator whenever a real user turn appears so
    # we end up holding only the most recent assistant turn.
    with open(transcript_path, encoding="utf-8") as fh:
        for raw in fh:
            idx += 1
            line = raw.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("type")
            msg = o.get("message", {}) or {}
            if t == "user":
                c = msg.get("content")
                if isinstance(c, str):
                    txt = c
                elif isinstance(c, list):
                    txt = " ".join(
                        p.get("text", "")
                        for p in c
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                else:
                    txt = ""
                # Only a genuine typed prompt starts a new turn (skip tool
                # results, which are role=user but carry no text).
                if txt.strip():
                    out = cc = cr = inp = steps = 0
                    seen.clear()
                    last_user_idx = idx
            elif t == "assistant":
                mid = msg.get("id")
                if mid and mid in seen:
                    continue
                if mid:
                    seen.add(mid)
                u = msg.get("usage") or {}
                if u:
                    inp += u.get("input_tokens", 0)
                    out += u.get("output_tokens", 0)
                    cc += u.get("cache_creation_input_tokens", 0)
                    cr += u.get("cache_read_input_tokens", 0)
                    steps += 1
                    last_asst_idx = idx
                    last_asst_id = mid
    return {"input": inp, "output": out, "cache_creation": cc,
            "cache_read": cr, "steps": steps,
            "last_user_idx": last_user_idx, "last_asst_idx": last_asst_idx,
            "last_asst_id": last_asst_id}


def state_path(script_dir=None):
    return data_file("state.json")


def iter_turns(transcript_path):
    """Yield one aggregated dict per *question* turn, oldest first.

    Each turn: {q, ts, input, output, cache_creation, cache_read, steps}. Only
    turns with at least one billed assistant step are returned. Command/interrupt
    pseudo-prompts are skipped so the report matches what appeared under answers.
    """
    turns = []
    cur = None
    seen = set()
    with open(transcript_path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("type")
            msg = o.get("message", {}) or {}
            if t == "user":
                c = msg.get("content")
                if isinstance(c, str):
                    txt = c
                elif isinstance(c, list):
                    txt = " ".join(
                        p.get("text", "")
                        for p in c
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                else:
                    txt = ""
                txt = txt.strip()
                if txt and not txt.startswith("[Request interrupted") \
                        and "local-command" not in txt:
                    # isMeta marks slash-command expansions and other injected
                    # turns (Claude Code hides these from the chat too). Start a
                    # turn so its assistant steps attribute correctly, but tag it
                    # so the report can drop it instead of listing /token-report.
                    cur = {"q": txt.replace("\n", " "), "ts": o.get("timestamp", ""),
                           "input": 0, "output": 0, "cache_creation": 0,
                           "cache_read": 0, "steps": 0,
                           "meta": bool(o.get("isMeta"))}
                    turns.append(cur)
                    seen.clear()
            elif t == "assistant" and cur is not None:
                mid = msg.get("id")
                if mid and mid in seen:
                    continue
                if mid:
                    seen.add(mid)
                u = msg.get("usage") or {}
                if u:
                    cur["input"] += u.get("input_tokens", 0)
                    cur["output"] += u.get("output_tokens", 0)
                    cur["cache_creation"] += u.get("cache_creation_input_tokens", 0)
                    cur["cache_read"] += u.get("cache_read_input_tokens", 0)
                    cur["steps"] += 1
    return [t for t in turns if t["steps"] > 0 and not t.get("meta")]


def billed_tokens(u):
    """Tokens that count toward the rate limit for this prompt (cache reads are
    cheap and excluded — kept consistent between the % numerator and calibration)."""
    return u["input"] + u["output"] + u["cache_creation"]


def format_line(u, cfg, emoji=True, limit=None, theme=None, calibrating=False):
    c = _painter(theme)
    display = cfg.get("display", "basic")
    prefix = "📊 " if emoji else ""
    total = u["input"] + u["output"] + u["cache_creation"] + u["cache_read"]
    if display == "simple":
        return f"{prefix}{c('num', format(total, ','))} tokens"

    sep = c("dim", " · ")
    base = (prefix
            + c("label", "출력") + " " + c("num", format(u["output"], ",")) + sep
            + c("label", "새 컨텍스트") + " " + c("num", format(u["cache_creation"], ",")) + sep
            + c("muted", "캐시읽기") + " " + c("muted", format(u["cache_read"], ",")))
    if display == "basic" or not cfg.get("percent", True):
        return base

    # detailed: add option-B percentage of the 5-hour limit
    if limit is None:
        limit = PLAN_5H_LIMIT.get(cfg.get("plan", "pro"))
    if limit:
        pct = billed_tokens(u) / limit * 100
        tail = f"5시간 리밋의 약 {pct:.2f}%"
        pct_str = c("pct", tail) + (" " + c("muted", "(보정중)") if calibrating else "")
        return base + " " + c("dim", "|") + " " + pct_str
    return base


def main():
    raw = sys.stdin.read()
    try:
        hook = json.loads(raw) if raw.strip() else {}
    except Exception:
        hook = {}

    transcript = hook.get("transcript_path")
    if not transcript or not os.path.exists(transcript):
        # Nothing to report; stay silent.
        sys.exit(0)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    cfg = load_config(script_dir)

    # The Stop hook can fire a hair before Claude Code flushes the final
    # assistant message(s) to the transcript. Stopping at the first non-zero
    # read would freeze an *undercounted* value (missing the final message),
    # which then wouldn't match the settled value the live status line shows.
    # So wait until the usage stabilizes: a response exists AND two consecutive
    # reads agree — meaning the turn is fully written.
    usage = None
    prev_sig = None
    for attempt in range(15):  # up to ~3s
        try:
            u = last_turn_usage(transcript)
        except Exception as e:
            print(json.dumps({"systemMessage": f"token-per-prompt error: {e}"}))
            sys.exit(0)
        usage = u
        has_response = u["last_asst_idx"] > u["last_user_idx"]
        sig = (u["steps"], u["output"], u["cache_creation"], u["cache_read"])
        if has_response and sig == prev_sig:
            break
        prev_sig = sig
        time.sleep(0.2)

    if not usage or usage["steps"] == 0:
        sys.exit(0)

    # Pin this turn as "done" so the live status line can show ✅ (vs the
    # climbing icon while a later turn is still in flight). We key on the turn's
    # *prompt* position (fixed at turn start) rather than the final assistant
    # message id, which may not be flushed yet when the Stop hook runs.
    try:
        with open(state_path(script_dir), "w", encoding="utf-8") as fh:
            json.dump({"done_user_idx": usage.get("last_user_idx"),
                       "transcript": transcript}, fh)
    except Exception:
        pass

    # Auto-calibrate the 5-hour limit from real server utilization (cached),
    # then resolve (limit, state) — state drives the "(보정중)" marker.
    limit = None
    calibrating = False
    try:
        import calibrate
        if cfg.get("autocalibrate"):
            calibrate.update_calibration(script_dir, cfg, billed_tokens(usage),
                                         static_limits=PLAN_5H_LIMIT)
        limit, state = calibrate.calibration_status(cfg, script_dir, PLAN_5H_LIMIT)
        calibrating = state == "calibrating"
    except Exception:
        limit, calibrating = None, False

    line = format_line(usage, cfg, emoji=False, limit=limit, calibrating=calibrating)
    print(json.dumps({"systemMessage": line}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
