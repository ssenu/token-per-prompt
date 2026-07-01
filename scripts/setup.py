#!/usr/bin/env python3
"""token-per-prompt: one-time setup — writes config.json and wires settings.json.

Arg-driven (non-interactive) so the agent can gather choices via AskUserQuestion
and invoke this with flags. Cross-platform:
  - python path  = sys.executable (the interpreter running this file)
  - script paths = derived from this file's own location
  - settings     = ~/.claude/settings.json

Idempotent: re-running updates in place without duplicating hooks, and preserves
any pre-existing status line (e.g. claude-dashboard) by chaining it underneath.
"""
import sys
import os
import json
import shlex
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report import data_file, DATA_DIR  # noqa: E402

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.expanduser(os.path.join("~", ".claude", "settings.json"))


def q(p):
    """Quote a path with forward slashes (safe for bash and PowerShell hooks)."""
    return '"' + p.replace("\\", "/") + '"'


def cmd(script):
    return q(sys.executable) + " " + q(os.path.join(SCRIPTS, script))


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser(description="token-per-prompt 초기설정")
    ap.add_argument("--location", choices=["hook", "statusline", "both"], default="both",
                    help="표시 위치: 답변 아래(hook) / 상태줄(statusline) / 둘 다")
    ap.add_argument("--display", choices=["simple", "basic", "detailed"], default="detailed")
    ap.add_argument("--plan", choices=["pro", "max5", "max20"], default="pro")
    ap.add_argument("--percent", choices=["on", "off"], default="on")
    ap.add_argument("--autocalibrate", choices=["on", "off"], default="on")
    ap.add_argument("--theme", default="tokyonight", help="tokyonight | default(무색)")
    ap.add_argument("--refresh", type=int, default=2, help="상태줄 새로고침 주기(초)")
    ap.add_argument("--settings", default=SETTINGS, help="(테스트용) settings.json 경로")
    args = ap.parse_args()

    settings_path = args.settings

    # ---- config.json (per-user data dir) ----
    cfg = load_json(data_file("config.json"), {})
    cfg.update({
        "display": args.display,
        "plan": args.plan,
        "percent": args.percent == "on",
        "autocalibrate": args.autocalibrate == "on",
        "theme": None if args.theme == "default" else args.theme,
    })

    settings = load_json(settings_path, {})
    report_cmd = cmd("report.py")
    status_cmd = cmd("statusline.py")

    # ---- Stop hook ----
    hooks = settings.setdefault("hooks", {})
    stop = hooks.setdefault("Stop", [])
    for grp in stop:  # strip any prior token-per-prompt hook (dedupe)
        grp["hooks"] = [h for h in grp.get("hooks", [])
                        if "report.py" not in h.get("command", "")]
    stop[:] = [g for g in stop if g.get("hooks")]
    if args.location in ("hook", "both"):
        stop.append({"hooks": [{"type": "command", "command": report_cmd}]})
    if not stop:
        hooks.pop("Stop", None)

    # ---- status line ----
    existing = settings.get("statusLine", {}).get("command", "")
    if args.location in ("statusline", "both"):
        # keep any pre-existing (non-ours) status line by chaining it below us
        if existing and "statusline.py" not in existing:
            cfg["chain_command"] = shlex.split(existing)
        settings["statusLine"] = {"type": "command", "command": status_cmd,
                                  "refreshInterval": args.refresh}
    elif "statusline.py" in existing:
        # switching away from status line → restore the chained command (or drop)
        chain = cfg.get("chain_command")
        if chain:
            settings["statusLine"] = {"type": "command",
                                      "command": " ".join(q(c) for c in chain)}
        else:
            settings.pop("statusLine", None)

    save_json(data_file("config.json"), cfg)
    save_json(settings_path, settings)

    print("✓ token-per-prompt 설정 완료")
    print(f"  데이터 폴더 : {DATA_DIR}")
    print(f"  settings    : {settings_path}")
    print(f"  위치={args.location}  표시={args.display}  요금제={args.plan}  "
          f"%={args.percent}  자동보정={args.autocalibrate}  테마={args.theme}  "
          f"새로고침={args.refresh}s")
    print("  → Claude Code를 재시작하면 적용됩니다.")


if __name__ == "__main__":
    main()
