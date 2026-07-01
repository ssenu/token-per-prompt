---
description: token-per-prompt 초기설정 (표시 위치·수준·요금제·테마 등)
---

token-per-prompt를 설정할 거야. **AskUserQuestion**으로 아래 항목을 물어본 뒤, 선택값을 인자로
`${CLAUDE_PLUGIN_ROOT}/scripts/setup.py`를 사용자의 파이썬으로 실행해
(Windows는 `python`, macOS/Linux는 `python3`).

물어볼 항목:
1. **표시 위치** `--location`: `hook`(답변 아래) / `statusline`(상태줄) / `both`
2. **표시 수준** `--display`: `simple`(총토큰) / `basic`(종류별) / `detailed`(+5시간%)
3. **요금제** `--plan`: `pro` / `max5` / `max20`
4. **테마** `--theme`: `tokyonight` / `default`(무색)
5. **5시간 % 표시** `--percent`: `on` / `off`
6. **자동보정** `--autocalibrate`: `on`(서버 실측으로 리밋 보정) / `off`
7. 상태줄을 쓰면 **새로고침 주기** `--refresh`: `1` / `2` / `5` (초)

예) `python "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py" --location both --display detailed --plan pro --theme tokyonight --percent on --autocalibrate on --refresh 2`

실행 후 결과를 요약하고, **Claude Code를 재시작해야 적용**된다고 안내해.
상태줄 위치를 선택했고 기존 상태줄(예: claude-dashboard)이 있으면, 그것은 자동으로 우리 줄 위에 유지된다고 알려줘.
