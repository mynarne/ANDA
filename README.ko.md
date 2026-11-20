# ANDA

[English](README.md)

![ANDA 아이콘](assets/anda-icon-v01.png)

ANDA는 프로젝트별 SQLite 원장으로 에이전트, Notion, Discord를 연결하는 프리뷰 스킬입니다.

## 시작하기

1. 에이전트에게 **비공개 Git 저장소 링크**를 전달하세요. [SKILL.md](SKILL.md)를 읽고 복사 전에 사용할 리비전을 확인하도록 요청하세요.
2. 승인된 **비공개 Notion 페이지**와 **비공개 Discord 텍스트 채널**을 하나씩 고르세요. Notion 연결, Discord 봇, 허용할 사용자를 확인하세요. 필요한 권한을 검토하고 자격증명은 보호된 로컬 환경에 두세요. [온보딩 안내](references/onboarding.md)
3. Python 3.11 이상을 사용하세요. 저장소 루트에서 설치와 상태에 서로 다른 새 절대 경로를 지정하세요. 상태는 패키지와 버전 관리 밖에 둡니다.

~~~sh
python3 -B scripts/check_release.py
python3 -B scripts/install.py --target "$ANDA_PACKAGE" --apply
python3 -B "$ANDA_PACKAGE/scripts/anda.py" --home "$ANDA_STATE" init --project demo
python3 -B "$ANDA_PACKAGE/scripts/anda.py" --home "$ANDA_STATE" doctor
~~~

### 안내형 설정 (setup)

마법사는 에이전트 터미널이 아니라 **사용자 본인의 터미널**에서 실행합니다. 비밀이 아닌 ID는 에이전트가 플래그로 미리 채워 줄 수 있습니다. 빠진 값은 실행 중에 묻고, 새 상태 경로면 초기화도 함께 합니다.

~~~sh
python3 -B "$ANDA_PACKAGE/scripts/anda.py" --home "$ANDA_STATE" setup --project demo \
  --discord-app 앱_ID --discord-guild 서버_ID --owner-id 내_Discord_사용자_ID --notion-parent 페이지_링크
~~~

다음 세 가지는 공개 API가 없고 서비스마다 본인 로그인이 필요하므로 직접 합니다. Discord 앱·봇 만들기(Message Content Intent 켜기), Notion 내부 통합 만들기(콘텐츠 읽기·삽입) 및 상위 페이지에 연결하기, 출력된 봇 초대 링크에서 **승인(Authorize)** 누르기입니다. 초대 링크는 채널 보기, 메시지 보내기, 메시지 기록 보기 권한을 요청합니다. 비공개 채널을 만들 때만 필요한 채널 관리 권한도 함께 요청하며, 설정이 끝나면 봇 역할에서 빼도 됩니다.

마법사가 하는 일은 다음과 같습니다.

1. 토큰은 숨김 입력으로만 받습니다. 인자·파일·출력에는 남기지 않습니다.
2. 봇, 서버 참여, 소유자, 상위 페이지를 읽기 전용 GET으로 확인합니다. 이미 설정된 목적지가 있으면 `doctor --live`도 실행합니다.
3. 토큰을 Windows 자격 증명 관리자나 macOS 키체인에 저장합니다. 그 밖의 OS에서는 보호된 환경 변수 `ANDA_*_TOKEN`을 사용하세요. 설정 파일에는 변수 이름만 남습니다. 변수가 없으면 `doctor`·`worker`·`reconcile`이 저장소의 값을 읽습니다.
4. `provision` 계획을 보여 주고, yes라고 답한 경우에만 `--apply`를 실행합니다.
5. Claude Code 훅과 MCP 설정을 exec 형식으로 출력합니다. 한 번 더 승인하면 `<project>/.claude/settings.local.json`과 `<project>/.mcp.json`에 합쳐 씁니다. 전역 클라이언트 설정은 건드리지 않으며, Claude Code가 다음 실행 때 승인을 다시 묻습니다.

검증은 따로 합니다. `doctor --live`와 `scripts/verify_claude_hooks.py --home 새_빈_폴더`를 실행하세요.

승인한 경로를 설정한 뒤 `doctor --live`로 읽기 권한을 확인합니다. 실제 전송과 모델 실행은 각각 승인합니다. 첫 `worker --live`에서 기준점을 잡고, 새 합성 입력 한 건으로 제한된 실행을 한 뒤 예상 수신 기록을 다시 읽어 확인하세요. [첫 실행 안내](references/relay-first-run.md) · [실검증 절차](references/live-acceptance.ko.md)

## 각 경로에서 할 수 있는 일

- **에이전트** — 프로젝트에 연결된 읽기 전용 릴레이 MCP로 문맥과 변경 커서를 읽습니다. 승인된 네이티브 훅은 선택한 턴의 화면 표시 요청·응답을 전달할 수 있습니다. 훅은 클라이언트별 설정과 신뢰 승인이 필요합니다.
- **Notion** — 승인된 페이지에 릴레이 기록을 보관할 수 있습니다. 등록한 작성자가 선택 페이지 바로 아래에 새로 쓰거나 수정한 적격 텍스트 문단은 Discord로 보냅니다. 이 경로는 모델을 호출하지 않습니다. 조회 사이의 중간 편집, 삭제, 중첩 블록은 수집 범위 밖입니다.
- **Discord** — 기본 요청은 `!anda `로 시작합니다. 모델 실행을 켜면 응답을 양쪽 목적지로 보낼 수 있습니다. 접두사 없는 일반 문장 수집은 별도 동의와 지정 범위의 `all_text` 설정이 필요합니다.

이벤트 ID와 전송 대기열로 로컬 기록의 중복을 방지합니다. 전송 결과가 불명확하면 재시도하기 전에 원래 목적지를 확인하세요. [운영·복구 안내](references/operations.md)

## 검증 범위

- **Windows** — 비공개 통합 시험에서 허용된 Discord 일반 요청 한 건을 Notion에 기록하고, 로그인된 Codex의 응답을 Notion과 Discord 양쪽에서 확인했습니다. Notion의 직접 문단 한 건을 Discord로 전달했고, 승인된 Codex 훅 한 턴의 요청·응답도 각각 양쪽에서 다시 읽었습니다. 실제 수신은 총 8건이었고 원장을 다시 연 뒤 추가 중복 전송은 0건이었습니다. 별도 시험에서 읽기 전용 MCP 변경 커서도 확인했습니다. [Windows 검증 기록](VERIFICATION-RELAY.md)
- **Claude·Gemini** — 범위를 지정한 MCP에서 이벤트 한 건을 읽는 경로를 확인했습니다. Windows의 Claude Code 2.1.322에서는 실제 턴 한 번으로 네이티브 훅(SessionStart·UserPromptSubmit·Stop)을 확인했습니다. 한글·이모지 메모리의 문맥 주입, 화면 표시 요청·응답 기록, `prompt_id` 공유 ID가 확인 대상이며 `scripts/verify_claude_hooks.py`로 재현합니다. Claude 모델 실행 어댑터와 Gemini 훅은 미검증입니다.
- **macOS** — 격리된 복사 설치와 MCP stdio가 통과했습니다. 별도 Mac 시험 후보에서 합성 데이터의 수동 복구도 통과했습니다. Mac 임시 경로 패치가 통합됐고, CI(macos-latest)에서 전체 단위 테스트와 smoke가 통과합니다. macOS에서의 Notion·Discord 실제 전송과 승인된 Codex 네이티브 훅 한 턴은 미검증입니다.

Linux, 무인 운영, 더 넓은 클라이언트 범위는 미검증입니다. [공개 조건](PUBLICATION.md)을 검토하는 동안 저장소를 비공개로 유지하세요.
