# Private repository snapshot / 비공개 저장소 스냅샷

This is a clean source snapshot of ANDA, distributed privately. It is not a production release and does not authorize public publication, model execution, external delivery, or infrastructure setup.

- The package consists of MANIFEST.json and its listed files. Python runtime dependencies are from the standard library; see [LICENSE](LICENSE) for licensing.
- Do not include local state, private chat exports, SQLite databases, real configuration, backups, credentials, or personal or internal identifiers.
- After changing any listed file, update its SHA-256 in the manifest. Run the offline checks below and verify installation into a new path. Hashes check integrity, not publisher identity.
- See the [relay verification notes](VERIFICATION-RELAY.md) for the scope and limitations of previous checks. Do not turn on hosting, scheduled workflows, or paid services as part of preparing the snapshot.

이 패키지는 ANDA의 비공개 소스 스냅샷입니다. 개인정보, 내부 식별자, 실제 설정과 실행 상태는 포함하지 않습니다. 파일을 바꾸면 매니페스트와 오프라인 검증을 갱신하세요. 공개 전환이나 서비스 설정은 별도 결정입니다.

~~~sh
python3 -B scripts/check_release.py
python3 -B -m unittest discover -s tests -q
python3 -B scripts/smoke.py
~~~
