# Discord 동기화 시스템 (wiki -> Discord 포럼) - 구현 스펙 v1.2

- 상태: **설계 확정, 구현 완료(M1~M5, §13.7 태그, §13.8 제목 포함). v1.2의 §8.1/§8.2는 미구현 과제다.** 이 문서는 코딩 에이전트 핸드오프용이다.
- 작성: 2026-10-14 (v0.1 -> v1.0 재작성)
- v1.1 (2026-10-15): **§13.8 제목(스레드 이름) 동기화 추가.** 현재 구현은 게시 시점에만 제목을 넣고 이후에는 갱신하지 않는다(스펙 누락). 이 변경으로 §11.4, §6.2, §15.3, §17.2, §19(O-4)가 함께 갱신된다.
- v1.2 (2026-10-15): **§8 변환 배선(§8.1) + 위키링크 해석(§8.2) 추가.** 두 가지를 함께 고친다. (1) §7의 2단계(convert)가 구현에서 누락되어 위키링크·콜아웃·형광·수식·각주가 Discord에 원문 그대로 노출된다(프로덕션 포럼 실측: `[[` 45곳/10개 노트). (2) `[[노트]]`를 게시된 포럼 스레드 링크 또는 가든 URL로 치환한다. 함께 갱신: §3, §5.1, §5.2, §6.2, §7, §8, §11.4, §13.1, §15.3, §17.1(U9 확장 + U16·U17), §17.2(12), §18, §19(O-8).
- 대상 독자: 이 스펙만 읽고 구현할 수 있는 코딩 에이전트. 프로젝트 사전 지식 없음을 가정한다.
- 요약: LLM 위키 볼트의 마크다운 노트 1개를 Discord 포럼 글 1개로 게시하고, 이후 노트 수정을 **메시지 편집만으로** 반영한다.

## 핸드오프 노트 (먼저 읽을 것)

1. **§19 "구현 전 확인 목록"의 O-1 ~ O-3을 먼저 처리한다.** 셋 다 5분 내 실측 가능하고, 결과에 따라 세부 구현이 달라진다. 나머지는 이 스펙대로 구현하면 된다.
2. **M1(§18)은 Discord API 없이 전부 구현·테스트할 수 있다.** 네트워크 접근 없이 순수 함수로 만들어라. M1 리뷰 통과 후 M2로 간다.
3. 토큰/길드/포럼 ID는 아직 미확정이다. M2 착수 전에 사용자가 제공한다. 하드코딩하지 말고 §5 설정 파일에서 읽어라.
4. 이 시스템은 사용자 머신(Artix Linux, Python 3.14.7)에서 단독 스크립트로 돈다. 서버/데몬 없음.

---

## 0. 배경: 왜 이런 구조인가

Discord 포럼 글의 메시지는 **중간 삽입이 불가능**하다. 새 메시지는 항상 스레드 맨 끝에 붙는다. 그런데 사람들이 글에 댓글을 단다. 따라서 "노트를 수정했으니 본문을 다시 올린다"를 새 메시지로 하면, 수정본이 댓글 아래로 밀려 문서가 읽히지 않는다.

해결: **게시 시점에 본문 슬롯을 미리 전부 만들고, 이후 수정은 그 슬롯들의 편집(edit)만으로 처리한다.** 편집은 순서를 바꾸지 않으므로 다른 사람 댓글이 있어도 문서 상단이 그대로 유지된다.

문서가 나중에 길어지면 슬롯이 더 필요한데, 그때 새 메시지를 만들면 댓글 뒤로 밀린다. 그래서 **꼬리에 예비 슬롯(버퍼)을 미리 확보**해 둔다.

---

## 1. 용어

| 용어 | 정의 |
|---|---|
| 슬롯(slot) | Discord 메시지 1개와 1:1 대응하는 단위. 인덱스 i는 스레드 내 순서 |
| 스타터(starter) | 포럼 글을 만들 때 함께 생성되는 첫 메시지. 목차/메타 전용 |
| 본문 슬롯 | `role=content`. 문서 텍스트 청크를 담는다. 첨부 없음 |
| 이미지 슬롯 | `role=image`. 이미지 첨부 1개만 담는다. 본문 텍스트 없음 |
| 버퍼 슬롯 | `role=buffer`. 꼬리 예비 슬롯. 내용 `-# (reserved)` |
| 스냅샷 슬롯 | `role=snapshot`. 전체 원본 .md 첨부를 담는 꼬리 슬롯(버퍼 뒤) |
| 포럼 글 / 스레드 | `POST /channels/{forum_id}/threads`가 만드는 스레드. `post_id` = `thread_id` |
| 계획(plan) | 노트 1개를 슬롯 시퀀스로 변환한 결과. 아직 Discord에 반영되지 않은 상태 |
| 상태(state) | 이미 Discord에 존재하는 슬롯 목록(사이드카 `index.json` + frontmatter) |

---

## 2. 확정 사실과 제약 (전부 사용자 확인 완료, 2026-10-14)

설계를 바꾸려면 이 항목을 먼저 반박해야 한다.

| # | 사실 | 근거 | 설계 영향 |
|---|---|---|---|
| C-1 | Discord 첨부는 메시지 **본문 하단**에 렌더된다(인라인 아님) | 사용자 확인 | 이미지 = 전용 슬롯 (§9.3) |
| C-2 | 메시지 content 제한은 2000이고, 한글 1자 = 1 유닛으로 센다 | 한글 최대 입력 후 복사해 `wc` -> `0 1 6000` (=2000자 x 3바이트) | `LIMIT=1900` (§9.4) |
| C-3 | 전용 봇은 해당 서버의 **관리자 역할**을 갖는다 | 사용자 확인 | 채널 권한 체크 불필요. unarchive/unlock/태그/이름 변경 모두 가능 |
| C-4 | **메시지 편집 시 `attachments`를 생략하면 기존 첨부가 삭제된다** | 사용자 확인 | 모든 편집에 `attachments`를 명시한다 (§13.4) |
| C-5 | 댓글이 달린다 | 사용자 진술 | 삭제 후 재게시 금지. 편집 기반 동기화 필수 |
| C-6 | Discord 메시지는 중간 삽입 불가 | API 특성 | 버퍼 전략 (§12) |

---

## 3. 비목표 (구현하지 말 것)

- 양방향 동기화. Discord 쪽 수정은 다음 동기화에서 덮인다. 단방향(위키 -> Discord)이다.
- 스레드 삭제/재생성에 의한 재정렬. 순서 변경은 편집만으로 흡수한다.
- 기존 댓글 보존을 위한 마이그레이션. `rebalance`(§12.4)는 최후 수단이며 댓글·딥링크 소실을 감수한다.
- 위키 노트 본문 자체의 편집(이 시스템은 frontmatter의 `discord_sync` 블록만 쓴다).
- 모든 마크다운 문법의 완전한 Discord 렌더링. §8의 변환 표가 범위다.
- 인라인 코드(백틱) 내부의 위키링크·형광 마커 치환. 변환 제외 대상은 `codefence` 블록과 이미지 문법뿐이다(§8.1).
- 위키링크의 역방향 추적(이 노트를 링크한 노트 목록·백링크 생성). §8.2는 한 방향 해석만 한다.

---

## 4. 산출물과 파일 레이아웃

작업 디렉토리는 `<repo-root>/`로 고정한다.

```
slop-discord-wiki-sync/
  SPEC.md              # 이 문서
  config.json          # 설정 (§5). 토큰 제외. 사용자가 편집
  secrets.json         # 봇 토큰. 0600. git/백업 제외 (§5.3)
  index.json           # 사이드카 상태 (§6.2). 스크립트가 관리
  sync.py              # 엔트리포인트 (CLI, §15)
  sync/
    __init__.py
    config.py          # 설정 로드/검증
    vault.py           # 볼트 노트 읽기, frontmatter 읽기/쓰기
    markdown.py        # 블록 파서 + Obsidian->Discord 변환 (§7, §8)
    chunker.py         # 슬롯 계획 생성 (§9)
    state.py           # index.json + frontmatter 로드/저장 (§6)
    discord_api.py     # REST 클라이언트 + 레이트리밋 큐 (§13)
    images.py          # 이미지 해석/정규화/해시 (§14)
    apply.py           # 계획 -> Discord 반영 (§11)
    cli.py             # 인자 파싱, 출력
  tests/
    fixtures/          # 입력 노트 + 기대 계획 (§17)
    test_chunker.py ...
  logs/                # 실행 로그 (JSONL, §16)
  .lock                # 동시 실행 방지 (§16)
```

- 의존성: **표준 라이브러리를 우선**한다(사용자 머신에 pip/venv 마찰이 있고 passwordless sudo가 없다). 외부 패키지는 정말 필요할 때만 쓰고 요구사항에 명시한다. 구현은 frontmatter 파싱에 **PyYAML**, HTTP에 **discord.py**(저수준 `HTTPClient`/`Route` - 레이트리밋·재시도·multipart를 위임)를 쓴다. 둘 다 `pyproject.toml`에 선언하고 `uv sync`로 설치한다.
- YAML은 `PyYAML`을 쓰거나, 필요한 키만 다루는 최소 파서를 직접 작성한다(frontmatter는 단순 중첩 맵 + 문자열 리스트 수준). 선택은 구현자 재량. 단 §6.1 스키마는 정확히 지켜야 한다.
- 시간은 항상 ISO 8601 + 오프셋(`Asia/Seoul`)으로 기록한다.

---

## 5. 설정

### 5.1 config.json

```json
{
  "vault_root": "/path/to/vault",
  "index_path": "index.json",
  "logs_dir": "logs",
  "defaults": {
    "forum_id": null,
    "forum_tags": [],
    "guild_id": null,
    "limit": 1900,
    "buffer_policy": { "mode": "adaptive", "min": 2, "max": 10, "ratio": 0.5 },
    "snapshot_attachment": true,
    "callout_mode": "quote",
    "wiki_link_mode": "discord",
    "wiki_link_style": "url",
    "wiki_link_fallback": "garden",
    "garden_base_url": "https://garden.example.com",
    "image": {
      "max_bytes": 9500000,
      "max_width": 1600,
      "format": "preserve",
      "one_image_per_slot": true
    },
    "rate_limit": { "min_interval_ms": 350, "max_retries": 5 }
  },
  "notes": {
    "pages/projects/UBL.md": { "forum_id": "1234567890", "forum_tags": ["1111111111111111111"] }
  }
}
```

- `defaults.forum_id`가 null이고 노트 frontmatter에도 `forum_id`가 없으면 그 노트는 **건너뛰고 경고**한다(임의 포럼 추측 금지).
- 게시 위치 우선순위: frontmatter `discord_sync.forum_id` > `config.notes[경로].forum_id` > `defaults.forum_id`. 문서가 자기 게시 위치를 자기 안에 갖는 게 휴대성에 낫다.
- 태그 우선순위: frontmatter `discord_sync.forum_tags` > `config.notes[경로].forum_tags` > `defaults.forum_tags`. 값은 **Discord 태그 ID 문자열 배열**이다(Obsidian 태그 아님, §13.7).
- `guild_id` 우선순위(§8.2 점프 링크용): index 노트 항목 > `config.notes[경로]` > `defaults.guild_id`. 같은 길드의 포럼만 쓰면 `defaults.guild_id` 한 줄이면 된다.
- `limit`은 1900 고정 권장. 올리려면 O-2 실측 후에.
- `image.max_bytes`는 Discord 기본 한도 10MB보다 낮게 잡아 여유를 둔다.

### 5.2 필드 의미

| 키 | 의미 |
|---|---|
| `buffer_policy.ratio` | `N = clamp(ceil(K * ratio), min, max)` (§12.1) |
| `snapshot_attachment` | 꼬리에 전체 .md 첨부 슬롯을 유지할지 |
| `callout_mode` | `quote`(콜아웃을 인용으로 변환) / `strip`(제거) |
| `wiki_link_mode` | `text`(표시 텍스트만) / `garden`(가든 URL) / `discord`(게시된 포럼 스레드 링크, 기본). §8.2 |
| `wiki_link_style` | `discord` 모드의 링크 형식: `url`(`[표시](https://discord.com/channels/{guild_id}/{thread_id})`, 기본) / `mention`(`<#thread_id>`) |
| `wiki_link_fallback` | `discord` 모드에서 Discord 대상이 없을 때: `garden`(`dg-publish: true`인 노트는 가든 URL, 기본) / `text` |
| `garden_base_url` | 가든 루트 URL(기본 `https://garden.example.com`). 슬러그 규칙은 §8.2 |
| `url`(구값) | `garden`의 별칭(하위 호환). 새 설정에는 쓰지 않는다 |
| `image.one_image_per_slot` | true = 이미지 1장당 슬롯 1개(권장). false = 연속 이미지를 한 슬롯에 최대 10장 |
| `image.max_width` | 초과 시 축소 재인코딩 |
| `rate_limit.min_interval_ms` | 요청 사이 최소 간격(전역 직렬 큐) |
| `forum_tags` | 포럼 글 생성 시 적용할 Discord 태그 ID 배열(§13.7). 최대 5개 |
| `guild_id` | §8.2 점프 링크 생성용 길드 ID. index에 없을 때의 폴백(§5.1) |

### 5.3 secrets.json

```json
{ "bot_token": "..." }
```

- 파일 모드 0600으로 생성하고, 로그에 값을 절대 출력하지 않는다.
- 환경변수 `DISCORD_BOT_TOKEN`이 있으면 그것을 우선한다.

---

## 6. 데이터 모델

### 6.1 노트 frontmatter (노트 파일 안, 최소 정보)

```yaml
---
title: UBL
tags: [project]
discord_sync:
  enabled: true
  forum_id: "1234567890"
  forum_tags:
    - "1111111111111111111"
  post_id: "9876543210"
  buffers:
    - "1111111111111111111"
    - "2222222222222222222"
  content_hash: "sha256:abcd..."
  synced_at: "2026-10-14T21:00:00+09:00"
---
```

- `enabled: true`가 없으면 동기화 대상이 아니다(명시적 옵트인). `enabled: false`는 명시적 제외.
- `post_id` = 스레드 ID. `buffers`는 사람이 눈으로 확인할 수 있게 남긴다(사용자 요구사항).
- `content_hash`는 **변환 후 최종 슬롯 시퀀스 전체의 해시**다(§9.5). 노트 원문 해시가 아니다.
- frontmatter는 반드시 **노트 파일 자체**를 편집해 갱신한다(YAML 블록 유지, 다른 키 보존, 들여쓰기 2칸).
- 존재하지 않는 키를 임의로 추가하지 않는다. 위 스키마가 전체다.

### 6.2 사이드카 index.json (상세 상태)

```json
{
  "schema_version": 1,
  "notes": {
    "pages/projects/UBL.md": {
      "forum_id": "1234567890",
      "guild_id": "111",
      "thread_id": "9876543210",
      "thread_name": "UBL",
      "starter_id": "9999999999",
      "bot_id": "222",
      "content_hash": "sha256:abcd...",
      "synced_at": "2026-10-14T21:00:00+09:00",
      "slots": [
        { "i": 0, "role": "content",  "id": "1001", "hash": "sha256:c1" },
        { "i": 1, "role": "content",  "id": "1002", "hash": "sha256:c2" },
        { "i": 2, "role": "image",    "id": "1003", "hash": "sha256:i1",
          "src": "raw/images/foo.png", "src_hash": "sha256:f1",
          "attachment_id": "1300", "filename": "foo.png", "description": "alt text" },
        { "i": 3, "role": "content",  "id": "1004", "hash": "sha256:c3" },
        { "i": 4, "role": "buffer",   "id": "1005", "hash": "sha256:b" },
        { "i": 5, "role": "buffer",   "id": "1006", "hash": "sha256:b" },
        { "i": 6, "role": "snapshot", "id": "1007", "hash": "sha256:s1",
          "attachment_id": "1301" }
      ]
    }
  }
}
```

- `thread_name`은 **Discord에 실제로 적용된 스레드 이름**이다(§13.8). 원하는 값은 frontmatter `title`(없으면 파일명 stem)이므로 **[스레드 이름]은 frontmatter 스키마에 추가하지 않는다**(§6.1 스키마 불변). 이 키가 없거나 `null`이면 "적용값 미상"으로 보고 다음 동기화에서 1회 갱신을 시도한다.
- `guild_id`는 점프 링크(`https://discord.com/channels/{guild_id}/{thread_id}`) 생성에 필요하다(§8.2). 스레드 생성 응답(§13.1)과 상태 보정 `PATCH`/`GET` 응답의 `guild_id`를 저장하며, 요청값이 아니라 응답값을 신뢰한다(§13.8과 같은 규칙). `wiki_link_style=mention`만 쓰면 없어도 동작한다.
- `slots`는 **스레드 내 실제 순서와 1:1로 일치**해야 한다. `i`는 0부터 연속.
- `hash`는 §9.5 규칙으로 계산한다. 같으면 그 슬롯은 이번 동기화에서 편집하지 않는다.
- `attachment_id`는 §13.4의 유지 목록에 필요하다. 단 편집 직전에 API에서 재조회해 복구할 수 있다.
- index.json은 **스레드 단위 진실**이다. frontmatter와 불일치하면 index.json을 신뢰하고 frontmatter를 재생성한다.
- 쓰기는 원자적으로 한다(임시 파일 + `os.replace`).

---

## 7. 파이프라인

```
1. read        노트 파일 읽기, frontmatter 분리, NFC 정규화
2. parse       블록 분해 (§9.1)  -> blocks
3. convert     블록별 Obsidian -> Discord 변환 (§8) + 위키링크 해석 (§8.2)
               (codefence·image 블록은 원문 유지)
4. chunk       슬롯 계획 생성 (§9.2)  -> plan = [SlotPlan, ...]
5. diff        plan vs state -> 변경 목록 (§11.2)
6. apply       Discord API 호출 (생성/편집) (§11.3)
7. verify      첨부 관련 편집은 GET으로 검증 (§13.4)
8. persist     index.json + frontmatter 갱신 (§6)
```

각 단계는 순수 함수로 분리해 M1에서 5까지를 네트워크 없이 테스트 가능하게 만든다.

- **v1.2에서 순서를 바꿨다:** 변환(3)은 블록 분해(2) **뒤**에 온다. 코드펜스·이미지 블록을 변환에서 제외하려면 블록 경계가 먼저 필요하기 때문이다. 길이 계산은 여전히 변환 후 문자열로 한다(§9.4).
- **3단계는 실제로 호출되어야 한다.** `markdown.convert_obsidian_to_discord()`가 정의만 되고 파이프라인에서 호출되지 않으면 §8 전체가 무효다(§8.1).

---

## 8. 마크다운 변환 규칙 (Obsidian -> Discord)

Discord는 위키링크·콜아웃·각주·수식을 지원하지 않는다. 아래 표대로 변환한다.

| 입력 | 출력 | 비고 |
|---|---|---|
| YAML frontmatter | (제거) | 본문에 포함하지 않는다 |
| `[[노트이름]]` | 모드별 (§8.2) | 기본 `discord`: 게시된 스레드 링크. 표시 텍스트 = 대상 이름 |
| `[[노트이름\|표시]]` | 모드별 (§8.2) | 표시 텍스트는 파이프 뒤를 쓴다 |
| `[[노트이름#헤딩]]` | `[[노트이름]]`과 동일 | 앵커는 버린다(v1) |
| `![[파일.png]]` | **이미지 슬롯** | §9.3. 위키링크 치환 대상이 **아니다**: `(?<!!)` 필수(§8.1) |
| `![[파일.png\|300]]` | 이미지 슬롯(크기 무시) | Discord는 크기 지정 미지원 |
| `![alt](경로.png)` | 이미지 슬롯, alt를 `description`으로 | §9.3 |
| `> [!note] 제목` + 본문 | `> **제목**` + `> 본문` | `callout_mode=strip`이면 제거 |
| `> [!warning] ...` 등 | 동일 규칙 | 종류별 분기 없음(v1) |
| `**bold**` `*italic*` `` `code` `` | 그대로 | Discord 지원 |
| 코드펜스 | 그대로 | 원자적 블록(§9.1) |
| `#` ~ `######` 헤딩 | 그대로 | Discord는 `#`/`##`/`###`만 렌더. 그 이하는 굵은 텍스트로 강등 |
| `- [ ]` / `- [x]` | 그대로 | Discord 지원 |
| `$수식$`, `==하이라이트==` | 텍스트로 강등 | 마커 제거 |
| `^[각주]`, `[^1]` | 텍스트로 강등 | 마커 제거 |
| 이미지가 아닌 첨부 링크 | 텍스트로 유지 | 다운로드 시도 금지 |

- 변환은 **블록 단위로** 한다(코드펜스·이미지 블록은 제외, §8.1). 길이 계산은 변환 **후** 문자열로 한다(변환으로 길이가 변한다).
- 변환 후 문자열을 NFC 정규화한다(§9.4).

### 8.1 변환 배선 (필수, 현재 미구현)

`markdown.convert_obsidian_to_discord()`는 존재하고 U9도 통과하지만, 실제 파이프라인(`cli._build_slots`)은 `parse_blocks(body)`만 호출한다. 테스트가 함수를 직접 호출해 검증했기 때문에 **배선 누락을 잡지 못했다.** 그 결과 프로덕션 포럼의 게시물에 `[[...]]`(실측 45곳 / 10개 노트), `> [!note] ...`, `==형광==`, `$수식$`, `[^각주]`가 원문 그대로 노출되어 있다(2026-10-15 실측).

- 순서: read -> parse(§9.1) -> convert(§8 + §8.2) -> chunk(§9.2). 변환이 길이를 바꾸므로 청킹 전에 끝나야 한다.
- 변환은 **텍스트 블록에만** 적용한다. `codefence` 블록과 `image` 블록은 원문을 유지한다(코드 안의 `==x==`·`[[x]]`를 건드리면 U4 불변식이 깨진다).
- `![[파일.png]]`는 위키링크가 아니다. 치환 정규식에 **negative lookbehind `(?<!!)`를 반드시 붙인다.** 없으면 `![[a.png]]`가 `!a.png`로 깨져 이미지 슬롯(§9.3)이 사라진다.
- 변환 결과가 빈 문자열이 된 블록(수식만 있던 문단 등)은 계획에서 제외한다. 이때 슬롯 수 K가 줄 수 있으므로 축소 규칙(§10)을 따른다.
- 회귀 방지: U9는 `convert_obsidian_to_discord()`를 직접 부르지 말고 **`_build_slots()`를 경유**해 검증한다.
- 첫 배포 효과: 게시된 12개 노트의 본문이 바뀌므로 다음 `sync --all`에서 전부 1회 편집된다(정상). 편집은 슬롯 단위 PATCH이며 아카이브/재게시가 아니다(댓글 보존).

### 8.2 위키링크 해석 (가든 / Discord 링크 치환)

`[[노트]]`는 볼트 노트를 가리키는데 Discord는 위키링크를 모른다. 볼트 노트는 이미 가든(garden.example.com)과 Discord 포럼 양쪽에 게시되어 있으므로, 링크 대상을 **게시된 스레드 링크**로 바꾼다(2026-10-15 사용자 요구).

**문법**

| 입력 | 처리 |
|---|---|
| `[[대상]]` | 대상 해석 후 치환. 표시 텍스트 = 대상 이름 |
| `[[대상\|표시]]` | 표시 텍스트 = 파이프 뒤 |
| `[[대상#헤딩]]`, `[[대상#헤딩\|표시]]` | `#` 뒤 앵커는 버린다(v1) |
| `![[...]]` | 대상 아님(임베드, §9.3) |

**모드별 출력** (`wiki_link_mode`)

| 모드 | 출력 |
|---|---|
| `text` | 표시 텍스트만 |
| `garden` | `[표시]({garden_base_url}/{slug})` |
| `discord` (기본) | 아래 사다리 |

**`discord` 모드의 해석 사다리** (위에서 먼저 맞는 것 하나)

1. **index.json에 대상 노트 항목이 있고 `thread_id`가 있으면 -> Discord 링크**
   - `wiki_link_style=url`(기본): `[표시](https://discord.com/channels/{guild_id}/{thread_id})`
   - `wiki_link_style=mention`: `<#thread_id>`
   - `guild_id`는 **index 노트 항목 > `config.notes[경로].guild_id` > `defaults.guild_id`** 순으로 찾는다. `url` 스타일인데 셋 다 없으면 링크를 만들 수 없으므로 2번으로 내려간다(경고 1줄). `mention` 스타일은 `guild_id`가 필요 없다.
2. **`wiki_link_fallback=garden`(기본)이고 대상 노트 frontmatter에 `dg-publish: true`가 있으면 -> 가든 URL**
3. **그 외 -> 표시 텍스트만** (경고에 대상 이름을 모은다)

**slug 규칙** (`garden` 모드 · 사다리 2번 공통)

- frontmatter `dg-permalink` > 파일명 stem(소문자, 공백 -> `-`).
- 한글 파일명 노트는 가든에서 `dg-permalink`가 필수다(슬러그가 비면 11ty `DuplicatePermalinkOutputError`). permalink가 없고 stem이 ASCII가 아니면 slug를 만들지 말고 3번으로 내려간다(경고).
- 실측 확인(2026-10-15): `/ficciones`, `/read-books`, `/kyu-of-babel`, `/jorge-luis-borges` 전부 200.

**대상 -> 노트 찾기** (순서 고정, 결정적)

1. index.json 키와 정확히 일치(`대상`, `대상.md`)
2. 볼트에서 상대경로 stem 일치
3. 여러 개면 모호 -> 경고 후 3번(표시 텍스트만)

**결정성 · 무변경**

- 해석은 **index.json + 볼트 frontmatter만** 본다. HTTP 호출 0회 -> `--dry-run` 불변식(§15.1) 유지.
- 볼트 노트의 **본문은 읽지 않는다**(frontmatter만). 링크 순환·재귀 없음.
- 해석 결과가 달라지면 슬롯 본문이 달라져 해시도 달라진다(§9.5) -> 다음 동기화에서 그 슬롯이 1회 편집된다. **루프가 아니다**(해석이 state·볼트에만 의존하므로 수렴한다).
- 새 노트를 게시한 뒤 그 노트를 참조하던 기존 노트를 링크화하려면 `sync --all`을 한 번 돌린다. `rebalance`로 `thread_id`가 바뀐 경우도 같다.
- 미해석 링크는 **오류가 아니다.** 실패 집계(exit 4)에 넣지 않고 경고만 출력한다.

**길이 · 렌더링**

- 점프 URL 1개 ≈ 65자(UTF-16). 링크가 많으면 슬롯이 늘 수 있다(늘리는 건 안전, 줄이는 건 §10).
- `[표시](...)`와 `<#thread_id>` 둘 다 Discord에서 클릭 가능한 링크로 렌더된다. 멘션은 권한 없는 사용자에게 `#deleted-channel`로 보일 수 있고 표시 텍스트가 스레드 이름으로 고정된다 -> 기본값은 `url` 스타일(O-8로 실측 확정).
- 점프 링크는 임베드를 만들지 않는다. 콘텐츠 슬롯에는 이미 `SUPPRESS_EMBEDS`가 있으므로(§13.6) 미리보기도 없다. 편집 경로는 `flags`를 보내지 않으므로 기존 플래그가 유지된다(§13.4).

---

## 9. 청킹

### 9.1 블록 분해

| 블록 | 원자성 | 쪼개기 규칙 |
|---|---|---|
| 문단 | 아니오 | 문장 경계(`.`, `!`, `?`, 줄바꿈)에서 쪼갠다. 그래도 안 되면 하드 분할 |
| 헤딩 | 아니오 | 뒤따르는 본문과 같은 슬롯에 묶는 것을 우선한다(헤딩만 남는 슬롯 금지) |
| 코드펜스 | **예** | 쪼개지 않는다. 한도 초과 시 `\n```\n`로 닫고 다음 슬롯에서 펜스를 재개 |
| 표 | **예** | 쪼개지 않는다. 한 슬롯에 담을 수 없으면 행 단위로 자르고 각 조각에 헤더를 반복한다. v1은 이 경로 미구현 -> 에러로 보고 |
| 인용 | 아니오 | 문단과 동일 |
| 목록 | 아니오 | 항목(`- `) 경계에서 쪼갠다 |
| 이미지 | **예** | 그 자체로 슬롯 경계를 강제한다(§9.3) |

- 블록 사이 구분은 빈 줄 1개로 정규화한다.

### 9.2 슬롯 계획 생성 (의사코드)

```python
def build_plan(blocks, cfg):
    plan, buf = [], []
    for b in blocks:
        if b.type == "image":
            flush_text(buf, plan)          # 텍스트 슬롯 확정
            plan.append(ImageSlotPlan(b))  # 이미지 슬롯(텍스트 없음)
            continue
        if cur_len(buf) + block_len(b) > cfg.limit:
            pieces = split_block(b, cfg.limit - cur_len(buf))
            flush_text(buf + pieces.head, plan)
            buf = pieces.tail
            while cur_len(buf) > cfg.limit:      # 원자 블록이 넘칠 때
                head, buf = hard_split(buf, cfg.limit)
                flush_text(head, plan)
        else:
            buf.append(b)
    flush_text(buf, plan)
    return plan
```

### 9.3 이미지 처리 규칙

- **이미지 1장 = 슬롯 1개**(`one_image_per_slot=true` 기본). 근거는 C-1: 첨부는 본문 하단에 붙으므로, 이미지를 담은 슬롯이 이미지가 나와야 할 위치에 정확히 있어야 한다.
- 이미지 슬롯의 content를 **빈 문자열로 두지 않는다.** Discord는 content와 첨부가 모두 비면 거부한다. `-# (image)` 같은 최소 placeholder를 넣는다. 거슬리면 O-2에서 제로폭 문자 가능 여부를 확인해 교체한다.
- 이미지가 문단 중간에 있으면 그 문단이 쪼개진다(문단 경계보다 이미지 경계가 우선). 트레이드오프로 받아들인다.
- 이미지 슬롯은 `attachments`를 직접 소유한다. content 슬롯은 절대 첨부를 갖지 않는다.
- 문서 맨 앞 이미지도 자연스럽게 첫 슬롯이 된다(빈 텍스트 슬롯을 만들지 않는다).

### 9.4 길이 계산

```python
def length(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2     # UTF-16 코드 유닛 기준
```

- 변환 + NFC 정규화 후 측정한다.
- `LIMIT = cfg.limit`(기본 1900), 하드 한도 2000.
- **NFC 정규화는 필수다.** NFD 한글은 음절 1자가 자모 3코드포인트로 분해되어 카운트가 3배로 부푼다(2000 한도가 약 667음절에서 걸림). 파일 읽기 직후와 변환 후 두 번 정규화한다.

### 9.5 슬롯 해시 (변경 감지)

```
hash(slot) = sha256(prefix + payload) 의 앞 16바이트, "sha256:<hex>" 로 기록
  content -> "text\n"  + nfc(content)
  image   -> "image\n" + src_hash + "\n" + description
  buffer  -> "buffer"                     # 내용/위치 항상 동일
  snapshot-> "snapshot\n" + snapshot_payload_hash
```

- 최상위 `content_hash` = 계획 전체의 슬롯 hash를 `"\n".join()`한 값의 sha256. no-op 조기 종료에 쓴다.

---

## 10. 슬롯 역할 전환 규칙

한 슬롯의 역할은 동기화 중에 바뀔 수 있다. 편집 시 **첨부를 명시적으로 계산**(C-4)해야 하므로 전환별로 보낼 값을 못박는다.

| 이전 role | 이후 role | content | attachments |
|---|---|---|---|
| content | content | 새 텍스트 | `[]` |
| content | image | `-# (image)` | 새 파일 파트 |
| content | buffer | `-# (reserved)` | `[]` |
| image | content | 새 텍스트 | `[]` (기존 이미지 제거) |
| image | image (동일 이미지) | 그대로 | **편집 안 함**(해시 동일) |
| image | image (교체) | `-# (image)` | 새 파일 파트 (기존 id 제외) |
| image | buffer | `-# (reserved)` | `[]` |
| buffer | content/image | 새 내용 | `[]` 또는 새 파일 |
| buffer | buffer | 편집 안 함 | 편집 안 함 |
| snapshot | snapshot | 새 메타 | 새 파일 파트 |

- **content 슬롯은 첨부를 가질 수 없다.** 이 불변식이 지켜지면 첨부 사고는 이미지 슬롯에서만 발생하고, 그 경로는 좁다.

---

## 11. 동기화 알고리즘

### 11.1 진입 조건

| 상황 | 동작 |
|---|---|
| frontmatter에 `discord_sync.enabled` 없음 | 건너뜀(무출력) |
| `enabled: true`, `post_id` 없음 | **신규 게시**(§11.3) |
| `enabled: true`, `post_id` 있음 | **동기화**(§11.4) |
| 로컬 index.json에는 스레드가 있는데 frontmatter에 `post_id`가 없음 | index.json을 신뢰하고 frontmatter를 재생성(경고 출력) |

### 11.2 diff 계산

```python
def diff(plan, state):
    ops = []
    for i, s in enumerate(plan):
        old = state.slots[i] if i < len(state.slots) else None
        if old and old.role == s.role and old.hash == s.hash:
            continue                                  # 무변경
        ops.append(Edit(slot_id=old.id, target=s) if old else Create(target=s))
    # plan보다 남는 슬롯 -> 버퍼로 되돌림 (삭제 금지)
    for j in range(len(plan), len(state.slots)):
        old = state.slots[j]
        if old.role == "buffer":
            continue
        ops.append(RevertToBuffer(slot_id=old.id))
    return ops
```

- `--dry-run`은 이 `ops` 목록을 사람이 읽는 형태 + JSON으로 출력하고 끝낸다.
- 상단 `content_hash`가 같고 버퍼 수가 정책을 만족하면 `ops`가 비고 API를 한 번도 호출하지 않는다.

### 11.3 신규 게시

```
1. 스레드 생성 (POST threads) — starter 메시지의 content + `applied_tags` 포함 (§13.1, §13.7)
2. plan의 슬롯을 순서대로 생성 (POST messages)          -> state.slots
3. 버퍼 N개 생성 (§12.1)                                 -> state.slots (append)
4. snapshot_attachment=true이면 스냅샷 슬롯 1개 생성
5. frontmatter + index.json 기록
```

- 생성 순서는 반드시 plan 순서대로. 직렬 실행(§13.5).
- 중간에 실패하면 **생성된 슬롯까지를 state에 저장**하고 종료한다(다음 실행이 이어서 채운다). 롤백(삭제)하지 않는다.

### 11.4 동기화 (기존 글 반영)

```
1. 스레드 상태 보정 + 제목 동기화 — §13.2, §13.8
   (archived/locked 해제. 제목이 state.thread_name과 다를 때만 같은 PATCH에 "name"을 포함)
2. 태그 갱신: 노트의 태그 집합이 state와 다르면 `applied_tags` PATCH (§13.7)
3. ops 순서대로 적용
   - Edit            -> PATCH message (§13.4)
   - RevertToBuffer  -> PATCH message (content="-# (reserved)", attachments=[])
   - Create          -> 남은 버퍼 슬롯이 있으면 그것을 Edit으로 재활용
                        없으면 §12.3 폴백
4. 스냅샷 슬롯 갱신 (내용 또는 원본 첨부가 바뀐 경우만)
5. 버퍼 수가 정책 N보다 부족하면 스냅샷 앞에 새 버퍼를 생성해 보충
6. 검증(§13.4) 후 frontmatter + index.json 기록(state.thread_name 포함)
```

- 무변경 판정은 세 축을 모두 본다: 슬롯 ops가 비어 있고, 태그 집합이 같고, 제목이 state.thread_name과 같을 때만 `no-op`(HTTP 0회).
- 위키링크 해석(§8.2)은 **계획 생성 시점에 이미 끝나 있다**(index.json + 볼트 frontmatter만 참조). 동기화 경로에서 링크 해석을 위해 추가 HTTP를 호출하지 않는다.
- 새 노트를 게시한 뒤에는 `sync --all`을 한 번 돌려 그 노트를 참조하는 기존 노트를 링크화한다(참조 노트당 1회 편집).

### 11.5 불변식 (테스트로 강제할 것)

1. `state.slots`의 순서 = 스레드 실제 순서. 삭제·분리 없음.
2. content 슬롯의 `attachments`는 항상 `[]`.
3. 이미지 슬롯의 content는 빈 문자열이 아니다.
4. 버퍼·스냅샷 슬롯은 항상 꼬리에 연속으로 몰려 있다.
5. 동일 입력 -> 동일 계획(결정적). 해시도 동일.

---

## 12. 버퍼 / 폴백 / 스냅샷 / rebalance

### 12.1 버퍼 정책 (적응형)

- `K` = 이번 계획의 슬롯 수(content + image).
- `N = clamp(ceil(K * ratio), min, max)` = 기본 `clamp(ceil(K*0.5), 2, 10)`.
- 예: K=4 -> N=2 / K=12 -> N=6 / K=20 -> N=10 / K=30 -> N=10(max).
- 버퍼 content는 **항상 `-# (reserved)` 한 종류**로 고정한다. 번호를 넣으면 소비 시마다 나머지 버퍼의 내용이 바뀌여 불필요한 편집이 발생한다.
- 버퍼는 **앞에서부터 순서대로** 소비한다(순서 유지). 소비된 슬롯은 role이 content/image로 바뀌고 더 이상 버퍼가 아니다.
- 버퍼 부족 시(문서 축소로 버퍼가 반환된 경우 포함) 스냅샷 슬롯 **앞에** 새 버퍼를 생성해 `N`을 맞춘다. 새 메시지는 꼬리에 붙으므로 댓글 뒤로 밀리지만, 버퍼·스냅샷은 원래 꼬리 영역이라 문제되지 않는다.

### 12.2 왜 버퍼가 성립하는가

댓글이 이미 달려 있어도, 버퍼 슬롯은 게시 시점에 이미 스레드에 존재한다. 따라서 이후 문서가 커져도 **존재하는 슬롯을 채우기만** 하면 되고, 새 메시지를 만들 필요가 없다. 이것이 "문서 상단이 댓글에 밀리지 않는다"의 핵심이다.

### 12.3 폴백 (버퍼 소진 + 성장 초과)

우선순위:

1. 남은 버퍼 슬롯 소비.
2. 그래도 부족 -> **스냅샷 슬롯을 "초과분 통합본"으로 강등**: 스냅샷 슬롯 1개에 남은 모든 청크를 담되 2000자를 넘을 수 없으므로, 초과분은 **전체 .md 첨부 + 안내문**으로 대체한다.
3. 그래도 부족하거나 `--rebalance` 요청 -> **rebalance**(§12.4, 수동 승인 필요).

### 12.4 rebalance (최후 수단, 명시적 요청 시에만)

```
1. 새 스레드를 만들어 전체를 재게시 (댓글·딥링크 소실)
2. 기존 스레드 꼬리에 안내 1줄 후 아카이브
3. index.json/frontmatter를 새 post_id로 갱신, 이전 post_id를 `previous_threads`에 보관
```

- `--rebalance` 플래그가 없으면 절대 실행하지 않는다. 실패 시 exit 4로 중단하고 수동 판단을 요청한다.

### 12.5 스냅샷 슬롯

- `snapshot_attachment=true`(기본)이면 꼬리(버퍼 뒤)에 1개 유지한다.
- 내용: 안내 1줄 + `synced_at` + 원문 링크(garden.example.com). 첨부: 노트 원본 .md.
- 버퍼가 남아도 유지한다 -> 버퍼 소진 시에도 완전한 사본이 있다.
- 갱신은 **같은 메시지 편집**으로 한다(새 파일 파트 + `attachments`에 새 파일 포함). 메시지를 삭제·재생성하지 않는다(순서 보존 + id churn 방지). 편집이 실패하면 그때만 삭제·재생성으로 폴백한다.
- 원본 .md 해시가 같으면 편집하지 않는다.

---

## 13. Discord REST API 계약

Base: `https://discord.com/api/v10`. 헤더: `Authorization: Bot <token>`, `User-Agent: DiscordBot (...)`. 구현은 이 계약을 `discord.py`의 `HTTPClient`/`Route`로 수행한다(레이트리밋·재시도는 discord.py가 처리, 요청 사이 최소 간격 350ms는 자체 적용).

### 13.1 포럼 글 생성

```
POST /channels/{forum_id}/threads
{
  "name": "<제목, <= 100자>",
  "message": { "content": "<스타터 본문>", "allowed_mentions": { "parse": [] } },
  "applied_tags": ["<tag_id>", ...],         // 선택, §13.7 (최대 5개, available_tags 검증)
  "flags": 4096                               // SUPPRESS_NOTIFICATIONS
}
-> 201 { "id": <thread_id>, "guild_id": <guild_id>, "name": <적용된 제목>, "parent_id": <forum_id>, ... }
```

스타터 메시지 내용(권장 포맷):

```markdown
# <노트 제목>
<원문 링크(garden.example.com)>

## 목차
- [<섹션 제목>](https://discord.com/channels/{guild}/{thread}/{slot_message_id})
- ...

-# 마지막 동기화: 2026-10-14T21:00:00+09:00
```

- 목차 딥링크는 슬롯 메시지 ID가 필요하므로 **최초 게시 시에는"(동기화 후 갱신)"으로 두고, 다음 동기화에서 채운다.**
- 스타터는 `state.starter_id`로 별도 관리한다(`slots`에 넣지 않는다).
- **제목(스레드 이름) 동기화는 §13.8이 단일 소스다.** 생성 시에는 위 `name`이 그대로 스레드 이름이 되고, 이후 변경은 §13.8로 반영한다.
- **`guild_id`는 생성 응답에서 받아 index.json에 저장한다**(§6.2). §8.2의 점프 링크에 필요하다.
- **범위 밖(미구현, 별도 과제):** 목차 딥링크(`## 목차` 항목) 채우기는 미구현이다. 스타터 본문의 `# <제목>` 줄은 §13.8 제목 동기화에 포함되어 구현되어 있다(v1.2 시점 코드 실측).

### 13.2 스레드 상태 보정 (매 동기화 시작 시)

```
PATCH /channels/{thread_id}  { "archived": false, "locked": false }
```

- C-3에 의해 권한 문제는 없다. 이미 정상이면 비용 없는 안전 호출이다(생략 가능하지만 유지 권장).
- 제목이 바뀌었을 때는 **이 호출에 `name`을 합쳐** 한 번에 보낸다(§13.8).

### 13.3 메시지 생성

```
POST /channels/{thread_id}/messages
{
  "content": "<본문 또는 placeholder>",
  "flags": 4096 | (4096+4),                   // +SUPPRESS_EMBEDS(텍스트 슬롯)
  "allowed_mentions": { "parse": [] }
}
```

- 이미지 슬롯은 multipart로 보낸다: `payload_json` + `files[0]`.

### 13.4 메시지 편집 (가장 중요)

```
PATCH /channels/{thread_id}/messages/{message_id}
```

**C-4: `attachments`를 생략하면 기존 첨부가 삭제된다.** 따라서 편집 요청은 항상 목표 최종 상태를 명시한다.

| 목표 | 본문 | `payload_json`의 `attachments` |
|---|---|---|
| 텍스트/버퍼가 됨 | 새 텍스트 | `[]` |
| 이미지 유지 | 기존 placeholder | `[{"id": "<old_attachment_id>", "description": "<alt>"}]` |
| 이미지 교체 | 기존 placeholder | `[]` + `files[0]`(새 파일, `attachments`에 `{"id": 0, "filename": ..., "description": ...}`) |
| 이미지 -> 텍스트 | 새 텍스트 | `[]` |

- multipart 구성(수동 조립 예): `payload_json` 파트 1개 + `files[i]` 파트 N개. `attachments` 배열에서 파일 파트 참조는 `{"id": <파트인덱스>, "filename": ..., "description": ...}` 형태다.
- **편집 직전 자가 복구**: `attachment_id`를 사이드카에서 읽되, 불확실하면 먼저 `GET /channels/{ch}/messages/{id}`로 현재 `attachments[].id`를 읽어 유지 목록을 구성한다. 이러면 사이드카가 깨져도 이미지를 잃지 않는다.
- **편집 후 검증**: 첨부를 건드린 편집은 `GET`으로 `attachments`를 확인한다. 기대와 다르면 로컬 `src`에서 재업로드하고 사이드카 `attachment_id`를 갱신한다.
- no-op 보장: `hash`가 같으면 편집 호출을 하지 않는다.

### 13.5 레이트리밋 / 직렬 큐

- 모든 요청을 **단일 직렬 큐**에 넣는다(병렬 금지). 요청 사이 최소 `rate_limit.min_interval_ms`(기본 350ms) 대기.
- 429 응답: `retry_after`(초, 소수) 또는 `X-RateLimit-Reset-After` 만큼 대기 후 같은 요청을 재시도. `X-RateLimit-Global: true`이면 전체 큐를 멈춘다. 최대 `max_retries`(기본 5) 후 실패로 처리하고 해당 슬롯만 건너뛰며 계속 진행(전체 abort 금지).
- 5xx는 지수 백오프(1s, 2s, 4s...)로 재시도. 4xx는 재시도하지 않고 로그 + 실패 집계.
- 성공/실패 카운트를 모아 종료 코드에 반영(§15.2).

### 13.6 플래그 값

| 플래그 | 값 | 용도 |
|---|---|---|
| `SUPPRESS_EMBEDS` | 1 << 2 = 4 | 텍스트 슬롯의 링크 미리보기 차단 |
| `SUPPRESS_NOTIFICATIONS` | 1 << 12 = 4096 | 생성 시 알림 차단(모든 슬롯) |

- 생성 시 둘 다 쓴다(텍스트 슬롯: 4100 / 이미지·버퍼·스냅샷: 4096).
- **O-1에서 편집 시에도 `flags`를 보낼 수 있는지 확인한다.** 불가하면 생성 시에만 적용한다.

### 13.7 포럼 태그 자동 적용

포럼 글을 **처음 생성할 때** 노트에 지정된 태그 ID를 `applied_tags`로 실어 보낸다.

- 태그 ID 출처(우선순위, §5.1과 동일 규칙):
  1. frontmatter `discord_sync.forum_tags` (문자열 배열)
  2. `config.notes[경로].forum_tags`
  3. `defaults.forum_tags`
  - 셋 다 없거나 빈 배열이면 태그를 달지 않는다(오류 아님).
- 값은 **Discord 태그 ID 문자열**(snowflake)이다. 이름이 아니다. ID를 얻는 방법은 `doctor --tags <forum_id>`(§15.1).
- 생성 시: `POST /channels/{forum_id}/threads` 본문에 `"applied_tags": ["<id>", ...]` 포함(§13.1).
- 동기화 시: 노트의 태그 집합이 state와 다르면 `PATCH /channels/{thread_id}` `{"applied_tags": [...]}`로 갱신한다(§13.2 호출에 합쳐도 된다). 같으면 호출하지 않는다.
- **전송 전 검증(필수)**:
  - `GET /channels/{forum_id}`의 `available_tags`를 읽어 **존재하는 ID만** 전송한다. 미등록 ID는 경고 후 제외한다(그대로 보내면 Discord가 400으로 스레드 생성 전체를 거부한다).
  - 최대 **5개**. 초과하면 exit 2로 중단하고 메시지를 출력한다(조용히 자르지 않는다).
  - `moderated: true` 태그는 MANAGE_THREADS 권한이 필요하다. 봇은 관리자 역할이라 통과하지만, 실패 시 경고하고 그 태그만 제외한다.
- state: index.json 노트 항목에 `forum_tags`(실제 적용된 ID 배열)를 저장한다. frontmatter의 `discord_sync.forum_tags`는 **원하는 값**(입력), index의 값은 **적용된 값**이다. 둘을 비교해 갱신 여부를 판단한다.
- `--dry-run` 출력에 `forum_tags`(해석된 ID 배열)를 포함한다. **dry-run은 HTTP 0회 불변식(§15.1)을 지켜야 하므로 이름 해석을 위해 네트워크를 호출하지 않는다** — ID만 출력하고, 이름은 로컬 캐시가 있을 때만 덧붙인다.

### 13.8 제목(스레드 이름) 동기화

위키에서 노트의 `title`을 바꾸면 포럼 글 제목도 따라 바뀌어야 한다(2026-10-15 추가). **구현 누락이었던 항목**이며, §13.1의 한 줄(제목 동기화)을 동기화 경로까지 확장한 것이다.

- 원하는 값(desired): frontmatter `title` > 파일명 stem (§13.1과 동일 규칙).
- 적용된 값(applied): `index.json` 노트 항목의 `thread_name`(§6.2).
- 길이: 100자를 넘으면 100자로 자른다(코드포인트 기준, 기존 게시 동작과 동일). 제목이 비면 stem으로 폴백한다.
- 호출: `PATCH /channels/{thread_id}` `{"archived": false, "locked": false, "name": "<desired>"}` — §13.2 상태 보정과 **같은 호출에 합쳐도 된다.** 제목이 같으면 `name` 키를 넣지 않는다.
- `thread_name`이 없거나 `null`이면(구버전 state, adopt로 채워지지 않은 경우) 1회 갱신을 시도하고, 응답의 `name`을 저장한다. **항상 PATCH하지는 않는다**(매 동기화 churn 금지).
- 저장: PATCH/GET 응답의 실제 `name` 값을 신뢰해 `index.json`의 `thread_name`에 기록한다(요청값을 그대로 쓰지 않는다). frontmatter에는 추가하지 않는다.
- 무변경: `thread_name == desired`이면 호출도 저장도 하지 않는다.
- 실패: 4xx는 경고 1줄 출력 후 슬롯 작업은 계속 진행하고, 실패 집계에 넣어 exit 4로 끝난다(§15.2). 5xx/429는 §13.5 재시도 규칙.
- `--dry-run`: **HTTP 0회를 유지한다**(비교는 저장된 `thread_name`과만 한다). §15.3 출력 참조.
- `adopt`: `GET /channels/{thread_id}`로 실제 `name`을 읽어 `thread_name`에 저장한다. 읽기 실패 시 키를 생략하고 다음 동기화에서 1회 갱신한다.
- `rebalance`: 새 스레드를 만들므로 방금 만든 이름을 `thread_name`에 저장한다.

---

## 14. 이미지 파이프라인

### 14.1 소스 해석

| 참조 형식 | 해석 |
|---|---|
| `![[foo.png]]` | 볼트 전체에서 파일명 검색(Obsidian 최단경로 규칙). 유일하지 않으면 경고 후 첫 매치 |
| `![[폴더/foo.png]]` | 볼트 루트 기준 상대 경로 |
| `![alt](경로)` | 노트 파일 기준 상대 경로 |
| `![alt](https://...)` | 원격. v1은 다운로드하지 않고 링크 텍스트로 대체(경고) |

- 경로는 `vault_root` 밖으로 나갈 수 없다(`..` 거부, symlink 해석 후 재검증).
- 공백·한글 파일명은 그대로 처리한다(URL 인코딩은 업로드 시에만).

### 14.2 정규화

```
1. 파일 읽기 -> sha256 (src_hash)
2. 크기/포맷 검사
   - bytes <= max_bytes 이고 width <= max_width 이면원본 그대로 업로드
   - 초과 시: 리사이즈/재인코딩 (Pillow 있으면 사용, 없으면 경고 후 원본 시도)
3. filename: 원본 파일명 유지(확장자 포함)
4. description: alt 텍스트. 없으면 빈 문자열
```

- `src_hash`가 같으면 재업로드하지 않는다(해시 기반 no-op).
- 같은 이미지가 문서에 2번 나오면 재업로드된다(Discord CDN URL은 서명 파라미터가 있어 만료되므로 URL 재사용 금지).
- 이미지 슬롯은 `attachments` 최대 10개 제한 안에 항상 1개이므로 이 제한에 걸리지 않는다(`one_image_per_slot=false`면 최대 10장).
- 업로드 실패 폴백: (1) 리사이즈 후 재시도 -> (2) 실패하면 그 슬롯을 링크 텍스트 슬롯으로 생성하고 로그에 남긴다(조용히 건너뛰지 않는다).

---

## 15. CLI 명세

### 15.1 커맨드

```
sync.py publish <note> [--force]              # 신규 게시 (post_id 없을 때)
sync.py sync <note> [--dry-run] [--diff]      # 변경 반영
sync.py sync --all [--dry-run]                # enabled 노트 전체
sync.py status <note>                          # 계획과 상태의 차이만 사람이 읽는 형태로
sync.py verify <note>                          # GET으로 실제 스레드 상태를 state와 대조
sync.py adopt <note> --post-id <id> [--starter-id <id>]   # 기존 글 재입양(상태 복구)
sync.py rebalance <note> --yes                # 새 스레드로 재게시(명시 승인 필요)
sync.py doctor                                 # 토큰/포럼 접근/사이드카 정합성/볼트 경로 점검
sync.py doctor --tags <forum_id>               # 포럼의 available_tags(id/name/moderated) 출력
```

공통: `--config <path>`(기본 `./config.json`), `--json`(기계 판독 출력), `--verbose`.

- `<note>`는 볼트 루트 기준 상대 경로(`pages/projects/UBL.md`). 절대 경로도 허용하되 볼트 안이어야 한다.
- `--dry-run`은 네트워크 호출이 **0회**여야 한다. 테스트로 강제할 것(HTTP 레이어에 호출 카운터).

### 15.2 종료 코드

| 코드 | 의미 |
|---|---|
| 0 | 성공(변경 없음 포함) |
| 1 | 사용법/인자 오류 |
| 2 | 설정·토큰·포럼 미지정 |
| 3 | 볼트/노트 읽기 오류 |
| 4 | 부분 실패(일부 슬롯 실패). 재실행으로 이어서 처리 가능해야 함 |
| 5 | 상태 불일치(수동 조치 필요). `doctor`/`adopt` 안내 출력 |
| 6 | 버퍼 소진 + 성장 초과 -> rebalance 권고 |

### 15.3 `--dry-run` 출력 형식 (M1의 수용 기준)

사람이 읽는 표 + `--json` 시 다음 구조:

```json
{
  "note": "pages/projects/UBL.md",
  "action": "sync",
  "mode": "dry-run",
  "content_hash": "sha256:...",
  "limit": 1900,
  "thread_name": { "desired": "UBL", "applied": "UBL", "action": "noop" },
  "wiki_links": { "discord": 2, "garden": 0, "text": 0, "unresolved": [] },
  "plan": [
    { "i": 0, "role": "content", "chars": 812, "preview": "# UBL\nUBL은 ...", "hash": "sha256:c1" },
    { "i": 1, "role": "image", "src": "raw/images/foo.png", "filename": "foo.png",
      "description": "alt", "src_hash": "sha256:f1", "hash": "sha256:i1" },
    { "i": 2, "role": "buffer", "content": "-# (reserved)", "hash": "sha256:b" },
    { "i": 3, "role": "snapshot", "filename": "UBL.md", "hash": "sha256:s1" }
  ],
  "buffer_policy": { "K": 2, "N": 2, "min": 2, "max": 10, "ratio": 0.5 },
  "ops": [
    { "kind": "create", "i": 0, "target_role": "content" },
    { "kind": "edit", "i": 1, "message_id": "1003", "from": "content", "to": "image" },
    { "kind": "noop", "i": 2 }
  ],
  "warnings": ["이미지 파일 not found: raw/images/old.png"]
}
```

- `thread_name.action`은 `noop` 또는 `patch`다. `applied`는 `index.json`의 저장값이며, 없으면 `null`로 출력한다(이 경우 `action=patch`).
- 사람이 읽는 출력에는 제목 한 줄을 추가한다: `제목: "UBL" (noop)` / 제목이 바뀌면 `제목: "UBL" -> "UBL 개요" (PATCH)`. **dry-run은 이 판단 때문에도 네트워크를 호출하지 않는다**(저장값과만 비교).
- `wiki_links`는 §8.2의 해석 결과 요약이다. `discord`/`garden`/`text`는 치환 개수, `unresolved`는 표시 텍스트로 떨어진 대상 이름 목록이다. 사람이 읽는 출력 한 줄: `링크: 2개 (discord 2, garden 0, text 0)`. **해석도 HTTP 0회다**(§8.2).

---

## 16. 로깅 / 락 / 오류 처리

### 16.1 로그

- `logs/{YYYY-MM-DD}.jsonl`, 1실행 = 1 run_id. 필드: `ts`, `run_id`, `note`, `action`, `slot_i`, `message_id`, `http_status`, `wait_ms`, `result`, `detail`.
- 토큰·본문 전문은 로그에 넣지 않는다(프리뷰 80자).
- 요약은 stdout에, 상세는 파일에.

### 16.2 락

- `.lock`을 `O_CREAT|O_EXCL`로 생성하고 `{pid, started_at}`을 쓴다. 종료 시 삭제.
- 이미 존재하면 pid 생존을 확인해 좀비 락은 회수한다. 살아 있으면 exit 4로 중단.

### 16.3 오류 분류

| 분류 | 처리 |
|---|---|
| 설정/토큰 오류 | 즉시 중단(exit 2) |
| 노트 읽기/파싱 오류 | 즉시 중단(exit 3), 해당 노트만 |
| 이미지 없음/읽기 실패 | 경고, 해당 슬롯을 링크 텍스트로 대체하고 계속 |
| 429 | 대기 후 재시도(§13.5) |
| 5xx | 백오프 재시도, 초과 시 해당 슬롯 실패 집계 |
| 4xx(권한 제외) | 재시도 없음, 실패 집계 |
| 편집 후 첨부 불일치 | 로컬 `src`에서 재업로드 후 재검증. 2회 실패 시 exit 4 |

- **실패를 조용히 넘기지 않는다.** 모든 경고·오류는 실행 요약에 건수와 함께 출력한다.

---

## 17. 테스트 계획

### 17.1 단위 테스트 (M1, 네트워크 없음)

픽스처는 `tests/fixtures/`에 노트 + 기대 계획 JSON 쌍으로 둔다.

| ID | 케이스 | 기대 |
|---|---|---|
| U1 | 1문단 짧은 노트 | 슬롯 1개, chars 정확 |
| U2 | 정확히 LIMIT 직전/직후 | 경계에서 분할되지 않음/분할됨 |
| U3 | LIMIT 초과 긴 문단 | 문장 경계에서 분할, 하드 분할 없음 |
| U4 | LIMIT 초과 코드펜스 | 펜스 닫힘 + 다음 슬롯에서 재개 |
| U5 | 표 1개 | 쪼개지지 않음(또는 명시적 에러) |
| U6 | 이미지 단독 / 문단 중간 / 문서 맨 앞 / 연속 2장 | 각각 기대 슬롯 배치(§9.3) |
| U7 | NFD 한글 노트 | NFC 정규화 후 길이 정확 |
| U8 | 아스트랄 이모지 | UTF-16 2유닛으로 계산 |
| U9 | `[[링크]]` / 콜아웃 / `==형광==` / `$수식$` / `[^각주]` | §8 표대로 변환. **`_build_slots()`를 경유해 검증한다**(함수 직접 호출 금지 — 배선 누락을 잡기 위함, §8.1) |
| U10 | diff: 축소 + 역할 전환 | RevertToBuffer 포함, 삭제없음 |
| U11 | 버퍼 정책 K=2,12,20,30 | N=2,6,10,10 |
| U12 | 결정성 | 같은 입력 2회 -> 동일 계획·동일 해시 |
| U13 | 태그 해석 우선순위 | frontmatter > notes > defaults, 없으면 빈 배열 |
| U14 | 태그 검증 | 5개 초과 -> exit 2, 미등록 ID -> 경고 후 제외(네트워크 없이 mock) |
| U15 | 제목 해석/비교 (§13.8) | `title` 없으면 stem 폴백, 100자 초과 시 100자 절단, `thread_name`과 같으면 `action=noop`, 다르거나 `null`이면 `action=patch` (네트워크 없음) |
| U16 | 위키링크 해석 사다리 (§8.2) | index에 대상 있고 `thread_id`+`guild_id` 있음 -> 점프 URL / `mention` 스타일 -> `<#id>` / index에 있는데 `guild_id` 없음(url) -> 폴백 / index에 없고 `dg-publish: true` -> 가든 URL(`dg-permalink` 우선, 없으면 stem slug) / 둘 다 없음 -> 표시 텍스트 / 한글 stem + permalink 없음 -> 표시 텍스트 + 경고 / 모호한 대상 -> 경고 / `\|표시`·`#헤딩` 처리 / 2회 실행 결과 동일(결정성). 네트워크 없음 |
| U17 | 문법 엣지 (블록 변환) | `![[파일.png]]`가 `!파일.png`로 깨지지 않음(lookbehind) / 코드펜스 안의 `[[x]]`·`==y==`·`$z$` 미변환 / 변환 후 빈 블록은 계획에서 제외 / 변환으로 길이가 변한 문단의 chars 정확 |

### 17.2 통합 테스트 (M2 이후, 테스트 포럼 필요)

순서대로 실행하고 각 단계에서 기대 상태를 확인한다.

1. 1슬롯 + 버퍼 2개 게시 -> 스레드 순서: starter, content, buffer, buffer, snapshot
2. 같은 노트 재동기화 -> HTTP 호출 0회(no-op)
3. 텍스트만 수정 -> content 슬롯만 편집됨
4. 이미지 추가 -> content 슬롯 뒤에 이미지 슬롯 생성, **렌더 위치가 문서 순서와 일치**
5. 텍스트 수정 -> 이미지 슬롯 첨부 유지 확인(가장 중요. C-4 회귀 방지)
6. 이미지 교체 -> 같은 슬롯 id, 새 첨부
7. 문서 대폭 확장 -> 버퍼 소비, 새 메시지 생성 0
8. 문서 축소 -> 남는 슬롯이 `-# (reserved)`로 복귀
9. 댓글을 1개 단 후 7번 반복 -> 문서 상단 순서 불변
10. 태그 지정 후 게시 -> 스레드에 태그가 붙음. 태그 변경 후 재동기화 -> PATCH로 갱신됨
11. 제목 변경 후 재동기화 -> PATCH 1회로 스레드 이름이 바뀌고, index의 `thread_name`이 갱신되며 exit 0 (즉시 재실행 시 HTTP 0회). 제목을 되돌리면 다시 PATCH 1회
12. 위키링크가 있는 노트를 게시 -> 실제 메시지에 점프 링크가 들어가고 클라이언트에서 클릭 가능, 임베드 없음. **대상 노트가 미게시 상태면 표시 텍스트로 나가고, 대상 게시 후 `sync --all` 한 번으로 1회 편집돼 링크로 바뀌며, 그 다음 실행은 HTTP 0회(no-op)**

---

## 18. 마일스톤과 수용 기준

| 단계 | 범위 | 수용 기준 |
|---|---|---|
| M0 | §19 확인 3건 | O-1~O-3 결과를 SPEC에 기록 |
| M1 | 설정/볼트 읽기/변환/청킹/diff + `--dry-run` | U1~U17 전부 통과, HTTP 호출 0회, `--dry-run --json` 스키마 준수 |
| M2 | 게시 (스레드 + 슬롯 + 버퍼 + 스냅샷 + 태그) | 통합 1, 2, 10 통과. 실패 시에도 state가 일관 |
| M3 | 동기화 (diff + 편집 + 버퍼 소비/반환 + 제목 동기화 §13.8 + 위키링크 §8.2) | 통합 3~5, 7, 8, 11, 12 통과 |
| M4 | 이미지 파이프라인 (해석/정규화/alt/폴백) | 통합 4, 6, 9 통과 + 업로드 실패 폴백 동작 |
| M5 | 복구/운영 (`adopt`, `doctor`, `verify`, 락, 로그, rebalance) | `doctor`가 의도적 손상 state를 탐지, `adopt`로 복구 |

- 각 마일스톤은 독립적으로 리뷰 가능해야 한다. 남은 것(미구현)을 README 대신 SPEC에 체크리스트로 남긴다.

---

## 19. 구현 전 확인 목록 (M0)

각 항목은 테스트 포럼에서 5분 내 확인 가능하다. 결과를 이 절에 덮어쓴다.

| ID | 확인 사항 | 확인 방법 | 결과에 따른 조치 |
|---|---|---|---|
| O-1 | 편집 요청에 `flags`를 보낼 수 있는가(SUPPRESS_NOTIFICATIONS가 편집 후에도 유지되는가) | 테스트 메시지 생성 후 편집, GET으로 `flags` 확인 | 불가하면 생성 시에만 적용(§13.6) |
| O-2 | 내용이 제로폭 문자만인 메시지가 전송되는가 / `-# (reserved)`가 정상 전송되는가 | 테스트 채널에 두 종류 전송 | 거절되면 `-# (reserved)` 고정 유지 |
| O-3 | 이미지 슬롯 렌더 위치가 문서 순서와 일치하는가 (C-1 최종 확인) | 텍스트-A, 이미지, 텍스트-B 3슬롯 게시 | 어긋나면 §9.3 재설계(이 경우 이미지를 앞 텍스트 슬롯에 붙이는 변형 검토) |
| O-4 | 포럼 글 작성 후 봇이 `PATCH`로 `name`/`applied_tags`를 바꿀 수 있는가 | 실제 포럼에서 시도 | 안 되면 제목 동기화 제거 |

- **O-4 확정(2026-10-15):** `applied_tags` PATCH는 실측 완료(태그 추가/제거 왕복 확인, 프로덕션 포럼). `name` PATCH는 미실측이지만 봇이 관리자 역할(C-3)이라 가능으로 판단하고 §13.8을 확정한다. 실패가 확인되면 §13.8의 PATCH를 제거하고 "제목 동기화 안 함"으로 되돌린다 — 그 경우 통합 테스트 11도 삭제한다.
| O-5 | 실제 볼트 이미지의 크기 분포(10MB 초과 비율, 평균 폭) | 볼트 이미지 대상으로 `du`/`identify` | 10MB 초과가 많으면 Pillow 도입 확정 |
| O-6 | `hidden` 상태 스레드에 대한 GET/PATCH 동작 | 아카이브/숨김 스레드에 시도 | 실패 시 가드 강화 |
| O-7 | `POST threads`에 `applied_tags`를 실어 태그가 실제로 붙는가 / 5개 초과·미등록 ID 시 응답 | 테스트 포럼에서 태그 1~2개로 생성, 초과·미등록도 시도 | 초과·미등록이 400이면 §13.7 검증을 필수로 유지 |
| O-8 | §8.2의 링크 표현이 실제 클라이언트에서 동작하는가 | 실제 포럼 메시지에 `[표시](https://discord.com/channels/{guild}/{thread})`와 `<#thread_id>`를 각각 넣고 확인 | 점프 링크가 안 열리거나 임베드가 뜨면 `wiki_link_style` 기본값·`SUPPRESS_EMBEDS` 처리를 수정한다. `mention`이 `#deleted-channel`로 보이면 `url` 스타일만 쓴다 |

- 해결된 항목은 지우지 말고 "확정: ..."으로 남긴다.
- 이미 확정된 사실: C-1~C-6(§2).

---

## 20. 부록: 샘플

### 20.1 입력 노트 (예)

```markdown
---
title: UBL
tags: [project]
discord_sync:
  enabled: true
---

# UBL

Uxntal Bridge Language 소개.

![[raw/images/ubl.png]]

## 설계

...
```

### 20.2 생성되는 슬롯 (개념)

```
[starter]  # UBL + 링크 + 목차 + 마지막 동기화
[0] content  "# UBL\n\nUxntal Bridge Language 소개."
[1] image    (첨부: ubl.png)
[2] content  "## 설계\n\n..."
[3] buffer   "-# (reserved)"
[4] buffer   "-# (reserved)"
[5] snapshot (첨부: UBL.md)
```

### 20.3 용어 색인

C-1~C-6(§2 제약), K(계획 슬롯 수), N(버퍼 수), plan/state(§1), slot/role(§1, §10), O-x(§19 확인 항목)
