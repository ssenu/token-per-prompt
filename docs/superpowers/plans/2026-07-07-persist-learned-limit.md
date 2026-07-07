# 학습된 5시간 리밋 이어쓰기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 새 세션의 첫 질문부터 이전에 학습된 5시간 리밋 보정값을 %의 기준으로 쓰되, 이번 세션에서 재확인되기 전까지 `(보정중)` 마커를 유지한다.

**Architecture:** `calibrate.py`에서 앵커(하드코딩 기본값, sane-band 판정 전용)와 학습값(영속되는 보정 리밋)을 분리한다. `calibration_status`를 3상태에서 4상태(`static`/`cold`/`learned`/`ok`)로 확장하고, `update_calibration`이 샘플 전진 시 현재 세션 id를 `confirmed_session`에 기록한다. 호출부(report/statusline/summary)는 세션 id를 전달하고 마커/라벨 판정을 갱신한다.

**Tech Stack:** Python 3.8+ 표준 라이브러리만. 테스트는 `unittest`. Windows는 `python`, macOS/Linux는 `python3`.

## Global Constraints

- 외부 의존성 금지 — 표준 라이브러리만 사용.
- 토큰/네트워크 소모 0 원칙 유지(테스트는 `get_utilization`을 monkeypatch해 네트워크 우회).
- 하위 호환: 기존 `calib.json`에 `confirmed_session`이 없어도 `.get()`으로 안전하게 동작.
- `calibration_status(cfg, script_dir, static_limits, session_id=None)` — `session_id`는 기본값 None(기존 호출 비파괴).
- 세션 id 출처: `os.environ.get("CLAUDE_CODE_SESSION_ID")`.
- 상수: `SANE_LO=0.25`, `SANE_HI=4.0`, `MIN_SAMPLES=2` (기존값 유지).
- 마커 판정: `calibrating = state in ("cold", "learned")`.

---

### Task 1: `calibration_status` 4상태 확장 + 테스트

**Files:**
- Modify: `scripts/calibrate.py` (`calibration_status` 함수)
- Test: `scripts/test_calibrate.py` (신규)

**Interfaces:**
- Consumes: 기존 `_static_limit(cfg, static_limits)`, `_load`, `_calib_path`, 모듈 상수 `SANE_LO/SANE_HI/MIN_SAMPLES/DATA_DIR`.
- Produces: `calibration_status(cfg, script_dir, static_limits, session_id=None) -> (limit: float, state: str)` where `state in {"static","cold","learned","ok"}`.

- [ ] **Step 1: 실패하는 테스트 작성** — `scripts/test_calibrate.py` 생성

```python
import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calibrate  # noqa: E402

STATIC = {"pro": 600_000, "max5": 3_000_000, "max20": 12_000_000}


class CalibrationStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_dir = calibrate.DATA_DIR
        calibrate.DATA_DIR = self.tmp

    def tearDown(self):
        calibrate.DATA_DIR = self._orig_dir

    def _write_calib(self, data):
        with open(os.path.join(self.tmp, "calib.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def test_autocalibrate_off_is_static(self):
        cfg = {"plan": "pro", "autocalibrate": False}
        limit, state = calibrate.calibration_status(cfg, None, STATIC, "s1")
        self.assertEqual(state, "static")
        self.assertEqual(limit, 600_000)

    def test_no_learned_value_is_cold(self):
        cfg = {"plan": "pro", "autocalibrate": True}
        # no calib.json written → no learned limit
        limit, state = calibrate.calibration_status(cfg, None, STATIC, "s1")
        self.assertEqual(state, "cold")
        self.assertEqual(limit, 600_000)

    def test_sane_learned_unconfirmed_is_learned(self):
        cfg = {"plan": "pro", "autocalibrate": True}
        self._write_calib({"limit": 900_000, "samples": 3,
                           "confirmed_session": "other-session"})
        limit, state = calibrate.calibration_status(cfg, None, STATIC, "s1")
        self.assertEqual(state, "learned")
        self.assertEqual(limit, 900_000)

    def test_sane_learned_confirmed_this_session_is_ok(self):
        cfg = {"plan": "pro", "autocalibrate": True}
        self._write_calib({"limit": 900_000, "samples": 2,
                           "confirmed_session": "s1"})
        limit, state = calibrate.calibration_status(cfg, None, STATIC, "s1")
        self.assertEqual(state, "ok")
        self.assertEqual(limit, 900_000)

    def test_insane_learned_falls_back_to_cold(self):
        cfg = {"plan": "pro", "autocalibrate": True}
        # 100k is below the sane band for pro (0.25*600k = 150k) → rejected.
        self._write_calib({"limit": 100_000, "samples": 5,
                           "confirmed_session": "s1"})
        limit, state = calibrate.calibration_status(cfg, None, STATIC, "s1")
        self.assertEqual(state, "cold")
        self.assertEqual(limit, 600_000)

    def test_confirmed_but_no_session_id_is_learned(self):
        cfg = {"plan": "pro", "autocalibrate": True}
        self._write_calib({"limit": 900_000, "samples": 2,
                           "confirmed_session": "s1"})
        limit, state = calibrate.calibration_status(cfg, None, STATIC, None)
        self.assertEqual(state, "learned")
        self.assertEqual(limit, 900_000)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python scripts/test_calibrate.py -v`
Expected: FAIL — 기존 `calibration_status`는 `session_id` 인자를 받지 못해 `TypeError`, 또는 상태값이 `"calibrating"`이라 어서션 실패.

- [ ] **Step 3: `calibration_status` 최소 구현** — `scripts/calibrate.py`의 기존 함수를 교체

```python
def calibration_status(cfg, script_dir, static_limits, session_id=None):
    """Return (limit, state) where state is one of:
      "static"  — auto-calibrate off; plain static/override anchor.
      "cold"    — auto-calibrate on, no usable learned value yet → anchor.
      "learned" — on, a sane learned limit exists but this session hasn't
                  re-confirmed it against the server yet → show (보정중).
      "ok"      — on, sane learned limit re-confirmed this session.

    The anchor (_static_limit) is the hardcoded/override plan estimate and is
    used ONLY for the sane-band gate — never the learned value itself, so a
    learned limit can never anchor its own plausibility (prevents drift).
    """
    static = _static_limit(cfg, static_limits)
    if not cfg.get("autocalibrate"):
        return static, "static"
    calib = _load(_calib_path(script_dir), {})
    lim = calib.get("limit")
    samples = calib.get("samples", 0)
    sane = (lim and lim > 0 and static
            and SANE_LO * static <= lim <= SANE_HI * static)
    if not sane:
        return static, "cold"
    confirmed = (session_id is not None
                 and calib.get("confirmed_session") == session_id
                 and samples >= MIN_SAMPLES)
    return (lim, "ok") if confirmed else (lim, "learned")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python scripts/test_calibrate.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: 커밋**

```bash
git add scripts/calibrate.py scripts/test_calibrate.py
git commit -m "feat(calibrate): 4-state calibration_status (static/cold/learned/ok) with session-scoped re-confirm"
```

---

### Task 2: `update_calibration`이 `confirmed_session` 기록

**Files:**
- Modify: `scripts/calibrate.py` (`update_calibration` 함수)
- Test: `scripts/test_calibrate.py` (테스트 케이스 추가)

**Interfaces:**
- Consumes: 기존 `update_calibration(script_dir, cfg, turn_billed, static_limits=None, ttl=300)`, `get_utilization`.
- Produces: 샘플 전진 시 `calib["confirmed_session"] = os.environ.get("CLAUDE_CODE_SESSION_ID")` 기록; 윈도우 리셋 시 `calib["confirmed_session"] = None` 초기화.

- [ ] **Step 1: 실패하는 테스트 추가** — `scripts/test_calibrate.py`에 클래스 추가

```python
class UpdateCalibrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_dir = calibrate.DATA_DIR
        calibrate.DATA_DIR = self.tmp
        self._orig_util = calibrate.get_utilization
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-xyz"

    def tearDown(self):
        calibrate.DATA_DIR = self._orig_dir
        calibrate.get_utilization = self._orig_util
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)

    def _calib(self):
        with open(os.path.join(self.tmp, "calib.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_advancing_sample_records_session(self):
        cfg = {"plan": "pro", "autocalibrate": True}
        # First call establishes the window baseline at util=50.
        calibrate.get_utilization = lambda script_dir, ttl=300: (50.0, "reset-A")
        calibrate.update_calibration(None, cfg, 300_000, static_limits=STATIC)
        # Second call: util rose to 52 (+2%), tokens accumulated → a sample lands.
        calibrate.get_utilization = lambda script_dir, ttl=300: (52.0, "reset-A")
        calibrate.update_calibration(None, cfg, 12_000, static_limits=STATIC)
        c = self._calib()
        self.assertGreaterEqual(c.get("samples", 0), 1)
        self.assertEqual(c.get("confirmed_session"), "sess-xyz")

    def test_window_reset_clears_confirmed_session(self):
        cfg = {"plan": "pro", "autocalibrate": True}
        calibrate.get_utilization = lambda script_dir, ttl=300: (50.0, "reset-A")
        calibrate.update_calibration(None, cfg, 300_000, static_limits=STATIC)
        calibrate.get_utilization = lambda script_dir, ttl=300: (52.0, "reset-A")
        calibrate.update_calibration(None, cfg, 12_000, static_limits=STATIC)
        # New window (different reset) → confirmed_session cleared.
        calibrate.get_utilization = lambda script_dir, ttl=300: (3.0, "reset-B")
        calibrate.update_calibration(None, cfg, 1_000, static_limits=STATIC)
        self.assertIsNone(self._calib().get("confirmed_session"))
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python scripts/test_calibrate.py -v`
Expected: FAIL — `confirmed_session` 키가 기록되지 않아 `test_advancing_sample_records_session`에서 `None != "sess-xyz"`.

- [ ] **Step 3: `update_calibration` 수정** — 두 지점에 라인 추가

윈도우 리셋 블록(`if calib.get("window_reset") != reset:`)에 초기화 추가:

```python
    if calib.get("window_reset") != reset:
        calib["window_reset"] = reset
        calib["cum_tokens"] = 0
        calib["baseline_util"] = util
        calib["baseline_tokens"] = 0
        calib["confirmed_session"] = None
```

샘플 전진 블록(`if plausible:` 안, `calib["samples"] = ...` 옆)에 기록 추가:

```python
        if plausible:
            old = calib.get("limit")
            # Exponential smoothing so a single noisy span can't swing it wildly.
            calib["limit"] = new_limit if not old else 0.6 * old + 0.4 * new_limit
            calib["samples"] = calib.get("samples", 0) + 1
            calib["confirmed_session"] = os.environ.get("CLAUDE_CODE_SESSION_ID")
            calib["baseline_util"] = util
            calib["baseline_tokens"] = calib["cum_tokens"]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python scripts/test_calibrate.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: 커밋**

```bash
git add scripts/calibrate.py scripts/test_calibrate.py
git commit -m "feat(calibrate): record confirmed_session on sample advance, clear on window reset"
```

---

### Task 3: 호출부(report/statusline/summary)에 session_id 전달 + 마커/라벨 갱신

**Files:**
- Modify: `scripts/report.py` (`main`의 calibration 블록)
- Modify: `scripts/statusline.py` (`main`의 status 블록)
- Modify: `scripts/summary.py` (`main`의 라벨 매핑)

**Interfaces:**
- Consumes: Task 1의 `calibration_status(..., session_id)`, Task 2의 `confirmed_session` 기록.
- Produces: 세 스크립트 모두 `os.environ.get("CLAUDE_CODE_SESSION_ID")`를 `calibration_status`에 전달하고, `calibrating = state in ("cold","learned")`로 마커 판정.

- [ ] **Step 1: `report.py` 수정** — `scripts/report.py`의 calibration try 블록

기존:
```python
        limit, state = calibrate.calibration_status(cfg, script_dir, PLAN_5H_LIMIT)
        calibrating = state == "calibrating"
```
교체:
```python
        sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
        limit, state = calibrate.calibration_status(cfg, script_dir, PLAN_5H_LIMIT, sid)
        calibrating = state in ("cold", "learned")
```

- [ ] **Step 2: `statusline.py` 수정** — status 블록

기존:
```python
            limit, state = calibrate.calibration_status(cfg, script_dir, PLAN_5H_LIMIT)
            indicator = turn_indicator(usage, script_dir)
            lines.append(indicator + " "
                         + format_line(usage, cfg, emoji=False, limit=limit,
                                       theme=cfg.get("theme"),
                                       calibrating=(state == "calibrating")))
```
교체:
```python
            sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
            limit, state = calibrate.calibration_status(cfg, script_dir, PLAN_5H_LIMIT, sid)
            indicator = turn_indicator(usage, script_dir)
            lines.append(indicator + " "
                         + format_line(usage, cfg, emoji=False, limit=limit,
                                       theme=cfg.get("theme"),
                                       calibrating=(state in ("cold", "learned"))))
```

- [ ] **Step 3: `summary.py` 수정** — 세션 id 전달 + 라벨 매핑

기존:
```python
    cfg = load_config()
    limit, cal_state = calibrate.calibration_status(cfg, None, PLAN_5H_LIMIT)
```
교체:
```python
    cfg = load_config()
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    limit, cal_state = calibrate.calibration_status(cfg, None, PLAN_5H_LIMIT, sid)
```

기존:
```python
    src = {"ok": "실측보정", "calibrating": "기본추정(보정중)",
           "static": "기본추정"}.get(cal_state, "기본추정")
```
교체:
```python
    src = {"ok": "실측보정", "learned": "이전보정값(재확인중)",
           "cold": "기본추정(보정중)", "static": "기본추정"}.get(cal_state, "기본추정")
```

- [ ] **Step 4: 스모크 테스트 — 세 스크립트가 임포트/실행 가능한지 확인**

Run: `python -c "import sys; sys.path.insert(0,'scripts'); import report, statusline, summary; print('import OK')"`
Expected: `import OK` (구문/임포트 오류 없음).

Run: `python scripts/test_calibrate.py -v`
Expected: PASS (8 tests) — 회귀 없음.

- [ ] **Step 5: 커밋**

```bash
git add scripts/report.py scripts/statusline.py scripts/summary.py
git commit -m "feat: pass session id to calibration_status; use learned limit with (보정중) until re-confirmed"
```

---

### Task 4: 문서 반영 (README / SKILL)

**Files:**
- Modify: `README.md` (8번 자동보정 섹션)
- Modify: `skills/token-per-prompt/SKILL.md` (값의 의미 섹션)

**Interfaces:**
- Consumes: 없음(문서만).
- Produces: 없음.

- [ ] **Step 1: `README.md`의 8번 섹션에 한 문단 추가** — "한계:" 줄 바로 위에 삽입

```markdown
- **이전 보정값 이어쓰기**: 한 번 보정된 리밋은 계정 속성이라 세션·5시간 윈도우를
  넘어 유지됩니다. 그래서 **새 세션을 열어 첫 질문을 해도** 하드코딩 기본값이 아니라
  직전에 학습된 값을 기준으로 %가 계산됩니다. 다만 그 세션에서 서버 실측으로 한 번
  재확인되기 전까지는 `(보정중)` 마커가 붙어 "이월된 추정치"임을 알려줍니다.
```

- [ ] **Step 2: `SKILL.md`의 "값의 의미" 섹션 5시간 % 항목 끝에 한 줄 추가**

기존 줄:
```markdown
  리밋은 요금제별 추정 기본값이며, `autocalibrate`가 켜지면 서버 실제 utilization으로 보정됨(추정).
```
바로 아래에 추가:
```markdown
  한 번 학습된 리밋은 세션·윈도우를 넘어 유지되어, 새 세션 첫 질문부터 이전 보정값을
  기준으로 % 를 계산함(그 세션에서 재확인되기 전까지 `(보정중)` 표시).
```

- [ ] **Step 3: 커밋**

```bash
git add README.md skills/token-per-prompt/SKILL.md
git commit -m "docs: 이전 보정값을 새 세션에 이어쓰는 동작 설명 추가"
```

---

## Self-Review

- **Spec coverage:** 앵커/학습값 분리(Task 1) · 4상태(Task 1) · `confirmed_session` 기록/리셋(Task 2) · 호출부 3개 전달+마커+라벨(Task 3) · 요금제 변경 자동 폴백(Task 1 sane-band, `test_insane_learned_falls_back_to_cold`) · 하위 호환(`.get`/`session_id=None`, Task 1 `test_confirmed_but_no_session_id_is_learned`) · 문서(Task 4) — 스펙 전 항목이 태스크에 매핑됨.
- **Placeholder scan:** TBD/TODO/"적절히 처리" 없음. 모든 코드 스텝에 실제 코드 포함.
- **Type consistency:** `calibration_status(cfg, script_dir, static_limits, session_id=None)` 시그니처가 Task 1 정의와 Task 3 세 호출부에서 일치. 상태 문자열 `{"static","cold","learned","ok"}`가 Task 1 반환·Task 3 마커/라벨과 일치. `confirmed_session` 키명이 Task 1 판정·Task 2 기록에서 일치.
