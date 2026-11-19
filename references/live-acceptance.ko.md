# 실제 왕복 검증

합성 데이터만 쓰는 별도 테스트 환경에서 다음 순서로 확인한다. 실행 호스트에는 쓰기 가능한 전용 상태 경로와 공식 AI CLI 로그인 세션이 필요하다. 테스트용 연결은 승인한 비공개 대상에만 설정한다.

1. Notion 내부 연결의 권한을 **Read content**와 **Insert content**로 설정하고 테스트 부모 페이지만 공유한다. 아래 명령의 dry run을 확인하고 실제 하위 페이지 생성에 동의하면 동일 명령에 `--apply`를 붙인다.

   ```sh
   python3 scripts/anda.py --home "$ANDA_TEST_STATE" init --project anda-live-test
   python3 scripts/anda.py --home "$ANDA_TEST_STATE" provision --project anda-live-test \
     --notion-parent "$ANDA_TEST_NOTION_PARENT" --notion-user-id "$ANDA_TEST_NOTION_USER"
   ```

2. 테스트 전용 Discord 봇을 비공개 텍스트 채널에 추가하고 **View Channel**, **Send Messages**, **Read Message History**를 부여한다. 사람이 쓴 `!anda` 명령을 읽는 봇에는 Developer Portal에서 **Message Content Intent**를 켠다. 채널을 미리 만들면 봇에 Manage Channels는 필요 없다.
3. 승인된 비밀 저장소에서 worker 환경의 `ANDA_NOTION_TOKEN`, `ANDA_DISCORD_TOKEN`을 주입한다. 설정 파일에는 환경 변수 이름만 둔다. `destinations.discord`를 활성화하고 테스트 채널 ID를 지정한다. `intake.discord`를 활성화하고 테스트 사용자 ID 하나를 `owner_ids`에 넣는다. `provider.enabled`를 `true`로 변경하고 테스트 호출 상한을 2로 지정한다.
4. `doctor --live`에서 두 목적지에 각각 `read_access_confirmed`를 확인한다. `worker --live`를 한 번 실행해 기존 명령의 기준점을 잡는다. 그 후 테스트 사용자로 Discord의 새 메시지에 다음과 같이 입력한다. Notion 요청함에서 반대 방향도 검사하려면 새 문단을 만들고 다른 마커를 쓴다.

   ```text
   !anda Reply with exactly ANDA-LIVE-b93e76f20a41 and nothing else.
   ```

5. `worker --live` 실행 후 두 수신증을 다시 읽어 검증한다. 시험마다 새 마커를 사용한다. 타임아웃 등으로 결과가 불확실하면 status와 해당 목적지의 실제 기록을 점검한 후 재개한다.

   ```sh
   python3 scripts/verify_live.py --home "$ANDA_TEST_STATE" --project anda-live-test \
     --source discord --marker ANDA-LIVE-b93e76f20a41
   ```

6. 다른 공식 AI 클라이언트에 `client-config`가 출력한 MCP 설정을 검토 후 등록한다. 그 클라이언트에서 `memory_context`로 마커를 검색해 **응답 이벤트 ID와 마커**를 가져오도록 요청한다. 실제 도구 호출이 관측되고 반환 ID가 일치해야 두 번째 AI를 통과로 기록한다. 스크립트의 독립 MCP 프로세스 검사만으로 두 번째 AI의 응답을 인증하지 않는다.

검증 기록에는 버전, KST 시각, 요청 방향, 응답 이벤트 ID, Notion 페이지 ID, Discord 메시지 ID를 남긴다. 수신증과 테스트 채널은 비공개 기록에 보관한다.

[Notion 연결 권한](https://developers.notion.com/reference/capabilities) · [Discord 권한](https://docs.discord.com/developers/topics/permissions) · [Discord Message Content Intent](https://docs.discord.com/developers/events/gateway#privileged-intents)
