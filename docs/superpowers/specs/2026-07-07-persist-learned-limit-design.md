# 학습된 5시간 리밋을 새 세션에 이어쓰기 (persist-learned-limit)

작성일: 2026-07-07

## 배경 / 문제

token-per-prompt는 질문별 토큰 사용량을 "5시간 리밋의 약 N%"로 보여준다. 이
리밋은 `calibrate.py`가 서버 실측(`oauth/usage`)으로 역산·보정한다.

현재 동작의 문제:

- 보정된 리밋(`calib.json`의 `limit`)은 세션·윈도우를 넘어 영속되도록 설계돼
  있으나, `samples >= MIN_SAMPLES(2)`로 "신뢰됨(ok)"이 되기 **전까지는** 무조건
  하드코딩 기본값(`PLAN_5H_LIMIT`, 예: pro=600,000)으로 되돌아간다.
- 그래서 **새 세션의 첫 질문들**은 이전에 어느 정도 보정된 값을 무시하고 거친
  기본 추정치를 쓴다 → 실제와 어긋난 %를 보여준다.

## 목표

새 세션을 열어 첫 질문을 해도, **이전에 학습된 보정값을 기준**으로 %가 계산되어
대략 실제와 일치하게 한다. 단, 이번 세션에서 서버 실측으로 **1회 재확인되기
전까지는 `(보정중)` 마커를 유지**해 "이월된 추정치"임을 표시한다.

## 비목표 (YAGNI)

- 학습값을 config로 수동 편집하는 UI. (기존 `default_limit` override로 충분)
- 여러 계정/프로필별 학습값 분리.
- 보정 알고리즘(지수평활, 역산 공식) 자체의 변경.

## 설계

### 앵커 vs 학습값 분리

두 개념을 명확히 분리한다:

- **앵커(anchor)** = 하드코딩 플랜 기본값(`PLAN_5H_LIMIT`) 또는 사용자가 명시한
  `default_limit` override. **런타임에 바뀌지 않는다.** 오직 sane-band 판정
  (`SANE_LO*anchor ≤ x ≤ SANE_HI*anchor`)에만 쓰인다. `_static_limit()`가 이
  값을 반환한다(기존 그대로).
- **학습값(learned)** = `calib.json`의 `limit`. 서버 실측으로 역산·평활된 최선의
  추정치. 세션·윈도우를 넘어 영속된다.

학습값이 자기 자신을 sane-band 앵커로 삼으면 폭주(unbounded drift)하므로, 앵커는
반드시 하드코딩/override 쪽으로 고정한다.

### 데이터 모델 (`calib.json`)

기존 필드 유지(`window_reset`, `cum_tokens`, `baseline_util`, `baseline_tokens`,
`limit`, `samples`). 신규 필드 추가:

- `confirmed_session` (문자열|null): 이번 윈도우에서 마지막으로 서버 실측 기반
  샘플을 **확정한 세션 id**(`CLAUDE_CODE_SESSION_ID`). `update_calibration`이
  샘플을 전진(`samples += 1`)시킬 때 현재 세션 id로 기록한다. 윈도우 리셋 시
  다른 per-window 누적값과 함께 초기화한다(null).

### 상태 확장: `calibration_status(cfg, script_dir, static_limits, session_id=None)`

기존 3상태(`ok`/`calibrating`/`static`)를 4상태로 확장한다.

| 상태 | 조건 | 반환 limit | 마커 |
|------|------|-----------|------|
| `static` | autocalibrate off | 앵커 | 없음 |
| `cold` | on, 학습값 없음 또는 비-sane | 앵커 | `(보정중)` |
| `learned` | on, sane한 학습값 존재, **이번 세션 미재확인** | **학습값** | `(보정중)` |
| `ok` | on, sane한 학습값, **이번 세션 재확인됨** | 학습값 | 없음 |

판정 로직:

```
static = _static_limit(cfg, static_limits)              # 앵커
if not autocalibrate: return (static, "static")
lim = calib.limit; samples = calib.samples
sane = lim and lim > 0 and static and SANE_LO*static <= lim <= SANE_HI*static
if not sane: return (static, "cold")
confirmed = (calib.confirmed_session == session_id) and samples >= MIN_SAMPLES
return (lim, "ok") if confirmed else (lim, "learned")
```

- `session_id`는 호출부에서 `os.environ.get("CLAUDE_CODE_SESSION_ID")`로 전달.
  전달 안 되면(None) `confirmed`는 항상 거짓 → 최대 `learned`까지만(안전 측).
- 요금제 변경(pro→max5 등) 시 옛 학습값이 새 앵커의 sane-band 밖으로 나가면
  자동으로 `cold`로 떨어져 새 앵커를 쓴다 → 자동 폴백.

### 호출부 수정

- `report.py` (Stop 훅): `session_id` 전달, `calibrating = state in ("cold","learned")`.
  기존에 `update_calibration` 후 `calibration_status`를 호출하는 흐름 유지.
- `statusline.py`: 동일하게 `session_id` 전달, `calibrating = state in ("cold","learned")`.
- `summary.py`: `session_id` 전달. 라벨 매핑 확장:
  - `ok` → `실측보정`
  - `learned` → `이전보정값(재확인중)`
  - `cold` → `기본추정(보정중)`
  - `static` → `기본추정`
- `update_calibration`: 샘플 전진 블록에서 `calib["confirmed_session"] =
  os.environ.get("CLAUDE_CODE_SESSION_ID")` 기록. 윈도우 리셋 블록에서
  `calib["confirmed_session"] = None` 초기화.

### 하위 호환

- 기존 `calib.json`에 `confirmed_session`이 없으면 `.get()`로 None 처리 → 첫
  실행 시 `learned`(마커 유지)로 시작, 이번 세션 재확인 후 `ok`.
- `calibration_status`의 `session_id`는 기본값 None이라 기존 호출도 깨지지 않음.
- `effective_limit()`는 `calibration_status(...)[0]` 그대로 → limit만 필요할 때
  session_id 없이 호출해도 학습값을 반환(정확한 값). 상태만 보수적으로 나옴.

## 테스트

`scripts/test_calibrate.py` (표준 라이브러리 `unittest`, 임시 디렉터리에
calib.json 조립) 신규 추가. `DATA_DIR`를 monkeypatch하거나 `_calib_path`를
임시 경로로 유도해 격리한다.

케이스:

1. 학습값 없음 → `(앵커, "cold")`.
2. sane 학습값, `confirmed_session != 현재sid` → `(학습값, "learned")`.
3. sane 학습값, `confirmed_session == 현재sid`, `samples>=2` → `(학습값, "ok")`.
4. 비-sane 학습값(요금제 변경 상정) → `(앵커, "cold")`.
5. autocalibrate off → `(앵커, "static")`.
6. `update_calibration`가 샘플 전진 시 `confirmed_session`을 현재 sid로 기록
   (서버 util을 가짜로 주입).

## 영향 파일

- `scripts/calibrate.py` — 상태 확장, `confirmed_session` 기록.
- `scripts/report.py` — session_id 전달, calibrating 판정.
- `scripts/statusline.py` — 동일.
- `scripts/summary.py` — session_id 전달, 라벨 매핑.
- `scripts/test_calibrate.py` — 신규 테스트.
- `README.md` / `SKILL.md` — "이전 보정값을 새 세션에 이어쓴다" 한 줄 반영(선택).
