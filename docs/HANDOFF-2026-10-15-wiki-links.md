# 핸드오프: §8 변환 배선 + §8.2 위키링크 해석 (SPEC v1.2)

- 대상: 코딩 에이전트
- 작성: 2026-10-15 (사용자 요청)
- 단일 소스: `SPEC.md` **v1.2** — 이 문서는 요약이 아니라 **작업 지시서**다. 충돌하면 SPEC이 이긴다.
- 작업 디렉토리: `<repo-root>/` (repo root = 실행 cwd). **git 저장소다** (HEAD `98eefec`, 직전 커밋 4개).
- 런타임: Artix Linux, Python 3.14.7, 서버/데몬 없음. pytest 미설치 → 테스트는 `python3 -m tests.test_m1`

## 0. 현재 상태 (베이스라인)

- M1~M5 구현 완료. §13.7(포럼 태그), §13.8(제목 동기화)까지 동작.
- M1 단위 테스트 **22/22 통과**(`python3 -m tests.test_m1`). `--dry-run`은 HTTP 호출 0회.
- **작업 시작 전 `git status`를 확인한다.** 스타터 메시지 `# 제목` 동기화(`_starter_content` 공통화)가 아직 커밋되지 않은 상태로 남아 있을 수 있다(2026-10-15 시점 `sync/cli.py`, `tests/test_m1.py` modified). 그 변경은 유지한다(§13.1 근거).
- 프로덕션: 포럼 `<FORUM_ID>` / 길드 `<GUILD_ID>` / 태그 `<TAG_ID>`. 게시된 노트 12개(모두 게시 완료, `sync --all`은 현재 전부 no-op).
- `index.json`의 12개 항목에 `thread_id` / `thread_name` 있음. **`guild_id`는 아직 없음**.

## 1. 작업 1 — 변환 배선 (§8.1, 버그 수정)

`markdown.convert_obsidian_to_discord()`는 정의되어 있고 U9도 통과하는데, `sync/cli.py`의 `_build_slots()`가 `markdown.parse_blocks(body)`만 호출한다. 그래서 콜아웃·형광·수식·각주·위키링크 변환이 **실제 게시물에 적용되지 않는다.** 프로덕션 포럼 실측: `[[...]]` 원문 45곳 / 10개 노트 노출. (테스트가 함수를 직접 호출했기 때문에 못 잡았다.)

**요구사항**

1. 파이프라인 순서를 `read → parse → convert → chunk`로 바꾼다. 즉 `parse_blocks()` 먼저, 그다음 블록별 변환, 그다음 `build_plan()`.
2. 변환은 **`text` 블록에만** 적용한다. `codefence` / `image` 블록은 원문 유지(코드 안의 `==x==`, `[[x]]`를 건드리면 U4 불변식이 깨진다).
3. `![[파일.png]]`는 이미지 임베드다. `_convert_wiki_links`의 정규식에 **negative lookbehind `(?<!!)`를 반드시 붙인다.** 없으면 `![[a.png]]` → `!a.png`로 깨져 이미지 슬롯이 사라진다(현재 함수에 이 버그가 있다).
4. 변환 결과가 빈 문자열이 된 블록(수식만 있던 문단 등)은 계획에서 제외한다. 슬롯 수 K가 줄 수 있으므로 축소는 §10 규칙(후행 슬롯 → `buffer` / `-# (reserved)`)을 따르고 **삭제하지 않는다**.
5. 길이 계산은 변환 후 문자열로 한다(§9.4, UTF-16 코드 유닛 + NFC).

## 2. 작업 2 — 위키링크 해석 (§8.2, 신규 기능)

`[[노트]]`를 볼트/디스코드 게시 상태에 따라 링크로 치환한다. 사용자 요구: "디스코드에 올라간 파일은 링크를 디스코드 링크로 치환".

**문법**: `[[대상]]`, `[[대상|표시]]`, `[[대상#헤딩]]`, `[[대상#헤딩|표시]]`(`#` 뒤 앵커는 버린다), `![[...]]`는 대상 아님.

**모드** (`wiki_link_mode`): `text` | `garden` | `discord`(**기본**). 구값 `url`은 `garden`의 별칭으로만 받는다(새로 쓰지 않음).

**`discord` 모드 사다리** (위에서 먼저 맞는 것 하나)

1. index.json에 대상 노트 있고 `thread_id` 있음 → Discord 링크
   - `wiki_link_style=url`(기본): `[표시](https://discord.com/channels/{guild_id}/{thread_id})`
   - `wiki_link_style=mention`: `<#thread_id>` (guild_id 불필요)
   - `guild_id` 조회 순서: index 노트 항목 > `config.notes[경로].guild_id` > `defaults.guild_id`. `url`인데 셋 다 없으면 2번으로 내려간다(경고 1줄).
2. `wiki_link_fallback=garden`(기본)이고 대상 노트 frontmatter에 `dg-publish: true` → 가든 URL
3. 그 외 → **표시 텍스트만** + 경고(미해석 목록)

**slug 규칙** (가든): frontmatter `dg-permalink` > 파일명 stem(소문자, 공백→`-`). permalink 없고 stem이 ASCII가 아니면 slug를 만들지 말고 3번으로(경고).

**대상 → 노트 찾기**: (1) index.json 키 정확 일치(`대상`, `대상.md`) (2) 볼트 상대경로 stem 일치 (3) 여러 개면 모호 → 경고 후 표시 텍스트만.

**절대 지킬 것**

- 해석은 **index.json + 볼트 frontmatter만** 참조한다. HTTP 0회 → `--dry-run` 불변식(§15.1) 유지. 볼트 노트의 **본문은 읽지 않는다**(frontmatter만) — 재귀/순환 없음.
- 미해석 링크는 오류가 아니다. 실패 집계(exit 4)에 넣지 않고 경고만.
- 해석은 결정적이어야 한다. 같은 state·볼트면 같은 결과 → 해시도 같다. 해석 대상이 늘면(새 노트 게시) 다음 sync에서 1회 편집되고 수렴한다. 루프 금지.
- `![[...]]` 보존(lookbehind).

## 3. 작업 3 — `guild_id` 저장/설정 (§6.2, §13.1)

1. 스레드 생성 응답의 `guild_id`를 index에 저장한다(`_persist` 경로).
2. `PATCH`/`GET /channels/{thread_id}` 응답에 `guild_id`가 있으면 자가 치유로 저장한다(요청값이 아니라 응답값).
3. `config.json` `defaults.guild_id`에 길드 ID를 넣는다(게시된 12개 노트에 즉시 적용되는 가장 싼 경로 — 백필 HTTP 불필요).
4. 조회 우선순위는 §8.2와 동일.

## 4. 손댈 코드 (예상 지점)

| 파일 | 변경 |
|---|---|
| `sync/markdown.py` | `_convert_wiki_links` lookbehind + 해석 사다리 분리(순수 함수화), `garden`/`discord` 모드 |
| `sync/cli.py` | `_build_slots()` 순서 변경 + 링크 해석 컨텍스트( index·볼트·config·note_rel ) 주입, `_persist`의 `guild_id`, `--dry-run` 출력에 `wiki_links` |
| `sync/state.py` | index 항목에 `guild_id` (읽기/쓰기) |
| `config.json` | `wiki_link_mode`/`wiki_link_style`/`wiki_link_fallback`/`garden_base_url`/`defaults.guild_id` |
| `tests/test_m1.py` | U9를 `_build_slots()` 경유로 바꾸고, U16/U17 추가(픽스처는 `tests/fixtures/`) |

## 5. 검증 (순서대로)

1. `python3 -m tests.test_m1` — U1~U17 통과 (pytest 없음, 이 커맨드 유지).
2. `python3 sync.py sync 픽션들.md --dry-run --json` → HTTP 0회, 본문에 `[[` 없음, `wiki_links`에 discord/garden/text/unresolved 집계. 사람용 출력에 `링크: N개 (discord …, garden …, text …)`.
3. **예상값(실측 기반)**: 게시된 12개 노트의 링크 **46개 중 discord 45 / text 1**. text는 미게시(비공개) 노트 1회. 12개 노트 전부 본문이 바뀌므로 첫 `sync --all`에서 **각 노트 1회 편집**되고, 즉시 재실행은 HTTP 0회(no-op)여야 한다. (초안의 58/53/5는 frontmatter `sources:`의 `[[...]]`까지 센 값이라 틀렸다 — 변환기 실제 출력은 46/45/1.)
4. 실측 확인: `sync.py sync 픽션들.md` 후 Discord에서 점프 링크가 클릭 가능한지, 임베드가 안 뜨는지, `[[`가 남아 있지 않은지(O-8).
5. `python3 sync.py sync --all` → 12/12 성공, 재실행 no-op.
6. 마무리: 변경을 커밋한다(기존 커밋 메시지 스타일 `feat(sync): ...` / `fix(markdown): ...`).

## 6. 금지 / 주의

- **C-4**: 메시지 편집 시 `attachments`를 생략하면 기존 첨부가 삭제된다. 항상 목표 상태 기준 배열을 보낸다(텍스트/버퍼 슬롯은 `[]`). 이번 작업에서 이 경로는 건드리지 않는다.
- 슬롯 삭제·재정렬 금지(§10). 줄이는 건 `buffer` 반환만.
- frontmatter 스키마에 키를 추가하지 않는다(§6.1). `guild_id`/`thread_name`은 **index.json에만**.
- `--dry-run`에서 네트워크 호출 0회(HTTP 계층 카운터로 강제). 태그 이름 해석도 하지 않는다(§13.7).
- 슬롯/메시지 순서가 바뀌면 안 된다(댓글 보존이 시스템 전체의 존재 이유).
- 링크 때문에 슬롯 수가 늘면 늘리는 건 안전(꼬리에 생성)하지만, 버퍼 소진 시 §12.3 폴백 규칙을 따른다.

## 7. 롤백

- 기능만 끄기: `config.json` `wiki_link_mode: "text"` → 링크는 표시 텍스트. (배선 수정 §8.1은 유지해야 한다 — 그건 버그 수정이다.)
- 완전 되돌리기: SPEC v1.2 이전 상태로 코드 복원 후 `sync --all`(1회 편집으로 원문 `[[...]]` 복귀).
- `guild_id`/`wiki_links` 키는 남아도 무해하다.

## 8. 미구현으로 남겨도 되는 것 (범위 밖)

- 스타터 메시지 `## 목차` 딥링크 채우기(§13.1).
- 인라인 코드(백틱) 내부 마커 치환.
- 백링크/역방향 추적.

## 9. 구현 중 스펙이 틀렸다고 판단되면

코드를 스펙에 맞추는 게 기본이다. 그래도 스펙이 틀렸다면 **SPEC.md를 고치고 변경 이력(v1.3) 한 줄을 추가**한 뒤, 무엇을 왜 바꿨는지 보고한다. 스펙과 코드가 조용히 어긋난 상태(= U9가 배선 누락을 못 잡은 상태)를 만들지 않는다.
