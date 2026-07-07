#!/usr/bin/env python3
"""token-per-prompt: auto-calibration of the 5-hour token limit.

Anthropic's oauth/usage endpoint reports the *real* 5-hour utilization, but only
at ~1% resolution — too coarse to attribute a single prompt directly. So instead
of showing the server delta, we use it to calibrate the effective 5-hour token
limit: across the window, when utilization rises by Δ%, the billed tokens we
counted in that span imply  limit ≈ tokens / (Δ/100).  The per-prompt % then
stays token-based (fine-grained) but anchored to the real limit.

All server reads are cached (default 5 min) so we never hammer the endpoint —
the actual fetch happens from the Stop hook at most once per TTL window.
"""
import os
import json
import time
import urllib.request

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CREDS = os.path.expanduser("~/.claude/.credentials.json")
DATA_DIR = os.path.expanduser(os.path.join("~", ".claude", "token-per-prompt"))


def _data_file(name):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception:
        pass
    return os.path.join(DATA_DIR, name)


def _calib_path(script_dir=None):
    return _data_file("calib.json")


def _util_cache_path(script_dir=None):
    return _data_file("util_cache.json")


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return dict(default)


def _save(path, data):
    # Atomic write (temp + replace) so a concurrent reader / another session
    # never sees a half-written file. Reduces corruption when multiple sessions'
    # Stop hooks touch the shared calib.json at once.
    try:
        tmp = path + f".{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


# A calibrated limit is only trusted within this band of the plan's static
# default. Outside it (e.g. multi-session race undercounting cum_tokens →
# absurdly small limit → one prompt showing 90%) we reject it as noise.
SANE_LO, SANE_HI = 0.25, 4.0
MIN_SAMPLES = 2


def get_utilization(script_dir, ttl=300):
    """Return (utilization_pct, window_reset) for the 5-hour window.

    Cached to util_cache.json for `ttl` seconds. On any failure we return the
    last cached value if present, else (None, None).
    """
    cache_path = _util_cache_path(script_dir)
    cache = _load(cache_path, {})
    now = time.time()
    if cache and now - cache.get("ts", 0) < ttl:
        return cache.get("util"), cache.get("reset")

    try:
        with open(CREDS, encoding="utf-8") as fh:
            token = json.load(fh)["claudeAiOauth"]["accessToken"]
    except Exception:
        return (cache.get("util"), cache.get("reset")) if cache else (None, None)

    try:
        req = urllib.request.Request(USAGE_URL, headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        five = data.get("five_hour") or {}
        util = five.get("utilization")
        reset = five.get("resets_at")
        _save(cache_path, {"ts": now, "util": util, "reset": reset})
        return util, reset
    except Exception:
        return (cache.get("util"), cache.get("reset")) if cache else (None, None)


def update_calibration(script_dir, cfg, turn_billed, static_limits=None, ttl=300):
    """Accumulate this turn's tokens and refine the limit when utilization moved.

    Runs from the Stop hook (once per turn). Cheap: the server read is TTL-cached.
    A derived limit outside the sane band (see SANE_LO/HI) is rejected without
    advancing the baseline, so the span keeps growing and can self-correct — this
    stops multi-session token undercounting from producing a bogus tiny limit.
    """
    calib = _load(_calib_path(script_dir), {})
    util, reset = get_utilization(script_dir, ttl=ttl)

    if util is None:
        # No server data — just keep accumulating so a later read can use it.
        calib["cum_tokens"] = calib.get("cum_tokens", 0) + turn_billed
        _save(_calib_path(script_dir), calib)
        return

    # New 5-hour window → reset only the per-window accumulators. The derived
    # `limit` and its confidence (`samples`) are an ACCOUNT property (the 5-hour
    # budget doesn't change between windows), so they persist — once calibrated,
    # every later window/session keeps using that value instead of reverting to
    # the raw static estimate.
    if calib.get("window_reset") != reset:
        calib["window_reset"] = reset
        calib["cum_tokens"] = 0
        calib["baseline_util"] = util
        calib["baseline_tokens"] = 0

    calib["cum_tokens"] = calib.get("cum_tokens", 0) + turn_billed

    du = util - calib.get("baseline_util", util)          # % moved
    dt = calib["cum_tokens"] - calib.get("baseline_tokens", 0)  # tokens in span
    if du >= 1 and dt > 0:
        new_limit = dt / (du / 100.0)
        static = _static_limit(cfg, static_limits or {})
        plausible = not static or (SANE_LO * static <= new_limit <= SANE_HI * static)
        if plausible:
            old = calib.get("limit")
            # Exponential smoothing so a single noisy span can't swing it wildly.
            calib["limit"] = new_limit if not old else 0.6 * old + 0.4 * new_limit
            calib["samples"] = calib.get("samples", 0) + 1
            calib["baseline_util"] = util
            calib["baseline_tokens"] = calib["cum_tokens"]
        # else: implausible (likely concurrent-session undercount) → keep the
        # span open (don't advance baseline) so more tokens can correct it.

    _save(_calib_path(script_dir), calib)


def _static_limit(cfg, static_limits):
    """Fallback limit: an explicit config override if set, else the plan estimate.
    Set "default_limit" in config.json to change the cold-start estimate."""
    override = cfg.get("default_limit")
    if isinstance(override, (int, float)) and override > 0:
        return override
    return static_limits.get(cfg.get("plan", "pro"))


def calibration_status(cfg, script_dir, static_limits):
    """Return (limit, state) where state is one of:
      "ok"          — calibrated and trusted (samples + sane band met)
      "calibrating" — auto-calibrate on but not yet trustworthy → show (보정중)
      "static"      — auto-calibrate off; plain static estimate
    """
    static = _static_limit(cfg, static_limits)
    if not cfg.get("autocalibrate"):
        return static, "static"
    calib = _load(_calib_path(script_dir), {})
    lim = calib.get("limit")
    samples = calib.get("samples", 0)
    trusted = (lim and lim > 0 and samples >= MIN_SAMPLES and static
               and SANE_LO * static <= lim <= SANE_HI * static)
    return (lim, "ok") if trusted else (static, "calibrating")


def effective_limit(cfg, script_dir, static_limits):
    """Back-compat: just the limit from calibration_status."""
    return calibration_status(cfg, script_dir, static_limits)[0]
