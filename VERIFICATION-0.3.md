# ANDA 0.3 검증 기록 / Verification

기록 기준: 2026-11-18 KST · Asia/Seoul (UTC+9). 내부 저장은 UTC epoch. Linux / Python 3.12.14.

- 전체 66개 unittest 통과. 메모리 신규 20개 포함: 출처/범위, 중복·충돌, 정정 이력, 동시 수정, 제안·확정·철회, 만료, local-only, 별도 Notion 목적지 고정, 전체 스냅샷, 일반 MCP 에이전트, 이벤트 규격, 외부 subprocess 프로토콜, KST.
- 기존 46개 테스트의 기록·outbox·재시도·복구·설치·API 경계도 유지했다.
- 실제 MCP 프로세스 및 새 설치본 사용 경로를 확인한다. 클라이언트 라벨과 native AI 모델 실행을 구분한다.
- 실제 외부 어댑터 프로토콜 테스트는 결정적 로컬 helper다. 모든 AI 제공자의 실응답을 검증한 것이 아니다.
- Codex 실제 응답 시험은 v0.2 때 scoped SQLite/log 경로로도 읽기 전용 초기화 오류가 발생했다. v0.3에서 무작정 모델 호출을 반복하지 않았다. Claude native 프로그램도 없으므로 실제 다중 AI 추론은 미완료다.
- 공개 파일 검사는 manifest·파일 종류·선택한 비밀/계정 패턴을 점검한다. 완전한 보안감사나 비밀 부재 보증은 아니다.

미완료: 실제 Notion·Discord 런타임 수신 ID, native AI 실응답·클라이언트별 설치, 원격 SSH/여러 기기·macOS/Windows 실검증, 48시간 관찰, Notion DB/뷰와 임의 편집 import, OAuth/OS supervisor, 보관·삭제 정책 및 provisioning unknown 전용 복구 도구.

~~~sh
python3 -m unittest discover -s tests -v
python3 scripts/smoke.py
python3 scripts/check_release.py
~~~
