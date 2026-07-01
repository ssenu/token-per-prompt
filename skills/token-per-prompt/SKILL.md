---
name: token-per-prompt
description: >
  Shows how many tokens each question/turn used — under each answer (Stop hook)
  and live in the status line — plus a full per-question report table for the
  session. Use when the user asks to see per-prompt/per-question token usage,
  "이 질문 토큰", "질문별 사용량 표", "토큰 리포트", set up/configure the token
  display, or wants a token dashboard that breaks usage down by question rather
  than by session total. 100% local; reads the session transcript; costs 0 tokens.
---

# token-per-prompt

세션 총량이 아니라 **질문(턴) 하나하나가 얼마나 썼는지**를 보여주는 편의 도구입니다.
모두 로컬 transcript(`.jsonl`)를 읽어 계산하므로 **네트워크·토큰 소모가 0**입니다.

## 세 가지 표시 방식

1. **답변 아래 (Stop 훅)** — 매 답변이 끝나면 그 질문의 토큰을 한 줄로 기록(고정).
   `출력 1,234 · 새 컨텍스트 80 · 캐시읽기 320,000 | 5시간 리밋의 약 0.25%`
2. **상태줄 (실시간)** — 화면 하단에 현재 턴 값을 실시간 표시. 질문마다 0으로 리셋 → 오름.
   맨 앞 아이콘: 🔄 진행중 / ✅ 완료. 기존 상태줄(claude-dashboard 등)은 위에 유지됨.
3. **리포트 표** — 세션의 모든 질문을 한 표로: 시각 · 출력 · 새컨텍스트 · 5시간% + 합계.

## 사용법 (에이전트 동작)

- **리포트 요청** ("토큰 사용량 표 보여줘", "질문별 토큰", "/token-report")
  → `${CLAUDE_PLUGIN_ROOT}/scripts/summary.py`를 사용자 파이썬으로 실행하고 표를 그대로 보여줌.
  (Windows `python`, macOS/Linux `python3`. 특정 세션은 `--session <경로>`.)
- **설정 요청** ("토큰 스킬 설정", "/token-setup")
  → AskUserQuestion으로 위치/수준/요금제/테마/% /자동보정/새로고침을 물은 뒤
  `${CLAUDE_PLUGIN_ROOT}/scripts/setup.py`를 해당 인자로 실행. 재시작 안내.

## 값의 의미 (사용자에게 설명할 때)

- **출력** = 생성한 답변 토큰(제일 비쌈). **새 컨텍스트** = 처음 처리해 캐시에 쓴 토큰.
  이 둘이 **실질 소비**. **캐시읽기** = 기존 맥락 재읽기로 **정가의 ~10%**라 부담이 미미(0은 아님).
- **5시간 %** = `(출력+새컨텍스트+입력) ÷ 5시간 리밋 × 100`. 캐시읽기는 제외.
  리밋은 요금제별 추정 기본값이며, `autocalibrate`가 켜지면 서버 실제 utilization으로 보정됨(추정).
- **상태줄과 답변 아래 값의 차이**: 같은 턴을 가리킬 때(상태줄 ✅)만 일치. 상태줄은 "실시간 최신 턴",
  답변 아래는 "그 질문의 고정 기록". 진행중(🔄)이면 값이 다른 게 정상.

## 주의

- 여러 세션을 동시에 쓰면 리포트 자동탐지나 자동보정이 흔들릴 수 있음(단일 세션이면 정확).
- 상태줄/훅 배선 경로는 설치 위치 기준 절대경로로 기록됨. 플러그인 업데이트 후 경로가 바뀌면
  `/token-setup`을 다시 실행하면 됨.
- 파이썬 3.8+ 필요. 표준 라이브러리만 사용(외부 의존성 없음).
