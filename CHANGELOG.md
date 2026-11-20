# 변경 이력 / Changelog

## 0.3.2 relay draft — 2026-11-20 KST

- `setup` 마법사: 사용자 터미널에서 봇 초대 링크 출력 → 숨김 입력 토큰 → 읽기 전용 GET 확인(`doctor --live` 포함) → OS 자격 증명 저장 → `provision` 계획 및 승인 후 `--apply` → 프로젝트 로컬 Claude Code 훅·MCP 설정(exec 형식, 승인 후 `.claude/settings.local.json`·`.mcp.json` 병합)까지 연결. Discord 앱·Notion 통합 생성과 봇 승인은 계속 사용자 몫. 초대 권한은 채널 보기·보내기·기록 보기(68608)에, 비공개 채널 생성용 채널 관리(16)만 추가.
- 토큰 저장소: Windows 자격 증명 관리자(`CredWriteW`/`CredReadW`), macOS 키체인(`security -i` 표준 입력, 인자에 비밀 없음). 저장 후 다시 읽어 일치할 때만 성공. 설정에는 `ANDA_*_TOKEN` 이름만 남고, 환경 변수가 없을 때 `doctor`·`relay-check`·`provision`·`worker`·`reconcile`이 저장소 값을 읽음. 환경 변수가 항상 우선.
- Windows: CLI·훅·MCP 출력을 항상 UTF-8로 고정. 한국어 Windows(cp949)에서 한글이 깨지고, 이모지가 섞이면 훅 문맥 주입과 MCP가 실패하던 문제 수정.
- Claude Code 훅: 프롬프트마다 오는 `prompt_id`를 안정 이벤트 ID로 사용. 릴레이 모드에서 Claude 훅이 기록되지 않던 문제와 일반 모드 중복 방지 누락 수정. 같은 프롬프트에 Stop이 여러 번 와도 ID 충돌 없이 기록.
- 훅 입력을 1 MiB까지 허용. 32,000바이트를 넘는 본문은 표시된 발췌로 저장하고 문맥 주입은 계속 동작(이전에는 긴 붙여넣기 한 번에 훅 전체 실패).
- MCP: 유효한 호출 안의 실패(리비전 충돌, 출처 이벤트 없음 등)를 사유와 함께 `isError` 결과로 반환. serverInfo 버전을 패키지 버전과 일치.
- `worker --watch`: 한 주기의 예외(DB 잠금 등)로 종료되지 않고 다음 주기로 진행. 점유 중이던 항목은 기존대로 unknown → reconcile, 자동 재전송 없음.
- 경로 안전성: 사용자가 만든 심볼릭 링크와 Windows 리디렉트형 재분석 지점(심볼릭 링크·정션·WSL 링크)은 거부하고, OS 별칭(macOS `/var`→`/private/var`, Windows 8.3 짧은 이름)은 허용. root로 실행하면 예외 없이 모든 링크를 거부. 백업·설치의 위치 검사는 실제 경로끼리 비교. 이전에는 macOS 임시 경로와 Windows 짧은 경로에서 상태 폴더와 온보딩이 거부됐고, Windows 정션은 검사에서 빠져 있었음.
- 긴 훅 본문은 비밀값을 먼저 가린 뒤 자름(자르는 경계에 걸린 토큰 일부가 남지 않도록).
- Stop 기록 ID의 본문 디지스트는 Claude `prompt_id`에만 붙임. Codex `turn_id` 기록은 0.3.1과 같은 ID라 업그레이드 후에도 중복 방지 유지.
- Windows·macOS·Linux GitHub Actions CI 추가. 총 109개 테스트.
- `scripts/verify_claude_hooks.py`: 실제 Claude Code 한 턴으로 네이티브 훅을 검증하는 스크립트. Windows·Claude Code 2.1.322·haiku에서 통과. 같은 시험을 수정 전(0.3.1) 코드로 돌리면 문맥 주입과 `prompt_id` 공유 ID 항목에서 실패.

## 0.3.1 relay draft — 2026-11-18 KST

- 승인된 Discord 일반 입력, Notion 문단 미러, Codex 네이티브 훅의 프로젝트별 동의 및 라우팅.
- Notion 최소권한 작성자 증명, 읽기 전용 MCP 변경 커서, 범위 변경 시 대기 배달 차단.
- Windows 비공개 합성 통합 시험에서 실제 수신 8건과 재열기 후 중복 0건 확인.

## 0.3.0 — 2026-11-18 KST

- 별도 지속 메모리, 출처·범위·시점·상태·정정 이력과 낙관적 동시 수정 제어.
- 사용자 선호/결정 제안·확정 분리, 만료·철회 처리, local-only 제외.
- Notion 메모리 전용 누적 페이지, 목적지 고정 라우팅.
- 모든 호환 AI를 위한 일반 MCP 라벨·이벤트·외부 어댑터 v1 규격.
- 사용자용 KST 표시, 총 66개 테스트.
- 기존 MD·ZIP·Git 초안/후속 버전 보존.

## 0.2.0 — 2026-11-18

- 새 노션 작업 페이지와 디스코드 비공개 작업 공간의 opt-in 자동 구성 및 생성 원장.
- Codex·Claude·Gemini·Grok용 MCP 설정 생성, 단일 호스트를 공유하는 SSH stdio 설정.
- 설정된 MCP 클라이언트 출처 라벨 보존.
- 실험적인 Claude Code 텍스트 실행 어댑터, Codex 런타임 상태 경로 한정·도구 비활성화 보강.
- 원장 전체의 일일 모델 시작 상한 및 제공자 변경 시 기존 작업 자동 전환 차단.
- 전체 SQLite 스냅샷·무결성 검사·개인 파일 권한.
- 한글/영문 README와 MIT 라이선스.
- 실제 플랫폼 연결과 native 제공자 인수 검증은 미완료.

## 0.1.0 — 2026-11-18

- 공통 기억 원장, stdio MCP, 공개 텍스트 훅 정규화.
- 노션·디스코드 출력/명령 수신, 중복·재시도·불명확한 전달 대조.
- opt-in Codex 텍스트 실행 및 manifest 기반 새 경로 설치.
