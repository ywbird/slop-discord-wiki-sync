# slop-discord-wiki-sync

[English](README.md) | [한국어](README.ko.md)

Obsidian 위키 볼트의 마크다운 노트 1개를 **Discord 포럼 글 1개**로 게시하고, 이후 노트 수정을 **메시지 편집만으로** 반영하는 단일 CLI 도구.

- 서버·데몬 없음. 단독 스크립트(`sync.py`) + `sync/` 모듈. 서드파티 의존성은 PyYAML 하나뿐.
- 단방향 동기화: 위키 -> Discord. Discord 쪽 수정은 다음 동기화에서 덮인다.
- `--dry-run`은 **HTTP 호출 0회**를 보장한다(테스트로 강제).

---

## 왜 이런 구조인가

Discord 포럼 글의 메시지는 **중간 삽입이 불가능**하다. 새 메시지는 항상 스레드 맨 끝에 붙는다. 그런데 글에는 사람들이 댓글을 단다. 그래서 "노트를 수정했으니 본문을 다시 올린다"를 새 메시지로 하면 수정본이 댓글 아래로 밀려 문서가 읽히지 않는다.

해결:

1. **게시 시점에 본문 슬롯을 미리 전부 만든다.** 이후 수정은 그 슬롯들의 편집(edit)만으로 처리한다. 편집은 순서를 바꾸지 않으므로 댓글이 있어도 문서 상단이 그대로 유지된다.
2. **꼬리에 예비 슬롯(버퍼)을 미리 확보**한다. 문서가 나중에 길어져 슬롯이 더 필요할 때 새 메시지를 만들면 댓글 뒤로 밀리기 때문이다.

---

## 주요 특징

- **슬롯 모델**: Discord 메시지 1개 = 슬롯 1개. 역할은 `content`(본문 텍스트) / `image`(이미지 첨부 1개) / `buffer`(예비, `-# (reserved)`) / `snapshot`(원본 `.md` 첨부).
- **적응형 버퍼**: 슬롯 수 K에 비례해 예비 슬롯 N을 확보(`ratio` 0.5, `min` 2, `max` 10). 축소는 삭제가 아니라 후행 슬롯을 `buffer`로 되돌려 처리한다(순서 불변).
- **해시 기반 no-op**: 역할 + 슬롯 해시가 같으면 편집하지 않는다. 최상위 `content_hash`가 같으면 동기화 자체를 건너뛴다(HTTP 0회).
- **정확한 길이 계산**: UTF-16 코드 유닛(`len(s.encode("utf-16-le"))//2`)으로 센다. NFC 정규화 필수(한글 NFD는 글자 수가 부풀어 오른다).
- **첨부 보존**: 메시지 편집 시 `attachments`를 생략하면 기존 첨부가 삭제되므로, 항상 전체 `attachments` 배열을 보낸다.
- **Obsidian -> Discord 변환**: 위키링크·콜아웃·형광·수식·각주를 Discord 문법으로 변환한다(코드펜스·이미지 블록은 원문 유지).
- **위키링크 해석**: `[[노트]]`를 게시된 Discord 스레드 점프 링크로 치환한다(없으면 가든 URL, 그다음 표시 텍스트). 해석은 `index.json` + 볼트 frontmatter만 읽으므로 HTTP 0회·재귀 없음.
- **포럼 태그 / 스레드 제목 동기화**: frontmatter 우선순위에 따라 태그를 적용하고, 제목 변경도 같은 PATCH에 병합한다.
- **레이트리밋 준수**: 단일 직렬 큐 + 최소 간격(기본 350ms), 429는 `retry_after`를 존중해 재시도.
- **원자적 상태 쓰기**: `index.json`은 임시 파일 + `os.replace`.

---

## 요구사항

- Python 3.11+ (개발·검증 환경: Artix Linux, Python 3.14.7)
- [uv](https://docs.astral.sh/uv/) (권장) 또는 일반 `python3`
- PyYAML - 유일한 서드파티 의존성(`uv sync`가 설치한다)
- Discord 봇 토큰. 봇은 대상 서버에서 **관리자 역할**을 갖는 것을 전제로 한다(채널 권한 체크 생략, unarchive/unlock/태그/이름 변경 가능).

---

## 설치

```bash
git clone <repo-url> slop-discord-wiki-sync
cd slop-discord-wiki-sync
uv sync                              # .venv 생성 + PyYAML 설치
cp config.example.json config.json   # 값 채우기
```

uv를 안 쓴다면 의존성을 직접 설치하고, `uv run sync.py` 대신 `python3 sync.py`로 실행한다.

```bash
python3 -m pip install pyyaml
```

토큰은 둘 중 하나로 준다.

```bash
# 1) secrets.json (권장, mode 0600)
printf '{"bot_token": "YOUR_BOT_TOKEN"}' > secrets.json && chmod 600 secrets.json

# 2) 환경변수
export DISCORD_BOT_TOKEN=YOUR_BOT_TOKEN
```

---

## 설정

### `config.json`

| 키 | 의미 |
|---|---|
| `vault_root` | Obsidian 볼트 절대 경로 |
| `index_path` | 사이드카 상태 파일(기본 `index.json`) |
| `defaults.forum_id` | 게시 대상 포럼 채널 ID. `null`이면 노트별 지정 필요 |
| `defaults.forum_tags` | 적용할 Discord 태그 ID 목록(최대 5) |
| `defaults.guild_id` | 점프 링크 생성용 길드 ID |
| `defaults.limit` | 메시지 1개 최대 길이(기본 1900) |
| `defaults.buffer_policy` | `{mode, min, max, ratio}` |
| `defaults.snapshot_attachment` | 원본 `.md` 첨부 슬롯 생성 여부 |
| `defaults.callout_mode` | `quote`(콜아웃을 인용으로) / `strip`(제거) |
| `defaults.wiki_link_mode` | `discord`(기본) / `garden` / `text` |
| `defaults.wiki_link_style` | `url`(기본) / `mention` |
| `defaults.wiki_link_fallback` | `garden`(기본) / `text` |
| `defaults.garden_base_url` | 가든 URL 접두사 |
| `defaults.image` | `{max_bytes, max_width, format, one_image_per_slot}` |
| `defaults.rate_limit` | `{min_interval_ms, max_retries}` |
| `notes` | 노트별 오버라이드(경로 -> 설정). 비어 있으면 전부 `defaults` 상속 |

우선순위: frontmatter `discord_sync.*` > `config.notes[경로]` > `defaults`.

### 노트 frontmatter (옵트인)

```yaml
---
title: UBL
discord_sync:
  enabled: true          # true가 없으면 동기화 대상이 아니다
  forum_id: "1234567890" # 생략 시 defaults 상속
  forum_tags: ["1111111111111111111"]
  post_id: "9876543210"  # 게시 후 스크립트가 기록
  content_hash: "sha256:..."
  synced_at: "2026-10-14T21:00:00+09:00"
---
```

`sync --all`은 볼트를 재귀 탐색해 `discord_sync.enabled: true`인 노트만 처리한다.

---

## 사용법

```bash
uv run sync.py publish <note> [--force]              # 신규 게시
uv run sync.py sync <note> [--dry-run] [--diff]      # 변경 반영
uv run sync.py sync --all [--dry-run]                # enabled 노트 전체
uv run sync.py status <note>                         # 계획과 상태의 차이만 출력
uv run sync.py verify <note>                         # GET으로 실제 스레드와 state 대조
uv run sync.py adopt <note> --post-id <id> [--starter-id <id>]   # 기존 글 재입양
uv run sync.py rebalance <note> --yes                # 새 스레드로 재게시(댓글·딥링크 소실)
uv run sync.py doctor                                # 토큰/볼트/모듈 점검
uv run sync.py doctor --tags <forum_id>              # 포럼의 available_tags 출력
```

공통 옵션: `--config <path>`(기본 `./config.json`), `--json`(기계 판독 출력), `--verbose`.

`<note>`는 볼트 루트 기준 상대 경로(절대 경로도 허용하되 볼트 안이어야 한다).

예시:

```bash
# 변경 사항만 확인 (네트워크 호출 없음)
uv run sync.py sync "UBL.md" --dry-run --diff

# 실제 반영
uv run sync.py sync "UBL.md"

# 볼트 전체 동기화
uv run sync.py sync --all
```

### 종료 코드

| 코드 | 의미 |
|---|---|
| 0 | 성공(변경 없음 포함) |
| 1 | 사용법/인자 오류 |
| 2 | 설정·토큰·포럼 미지정 |
| 3 | 볼트/노트 읽기 오류 |
| 4 | 부분 실패(일부 슬롯 실패). 재실행으로 이어서 처리 가능 |
| 5 | 상태 불일치(수동 조치 필요). `doctor`/`adopt` 안내 |
| 6 | 버퍼 소진 + 성장 초과 -> `rebalance` 권고 |

---

## 동작 원리 요약

```
read -> parse(블록 분해) -> convert(Obsidian->Discord) -> chunk(슬롯 계획)
     -> diff(계획 vs index.json) -> apply(생성/편집) -> persist(index.json + frontmatter)
```

- **불변식**: 슬롯 순서는 절대 바꾸지 않는다. 줄이는 것도 삭제가 아니라 후행 슬롯을 `buffer`로 되돌리는 방식이다. 버퍼는 왼쪽부터 소비된다.
- **콘텐츠 슬롯에는 첨부가 없어야 하고, 이미지 슬롯은 첨부 1개만 갖는다.** 빈 콘텐츠는 허용되지 않는다(플레이스홀더 사용).
- **`synced_at`** = "Discord 상태를 실제로 쓴 시각". no-op이면 갱신되지 않는다(HTTP 0회 불변식 유지).
- **`index.json`이 스레드 단위 진실**이다. frontmatter와 어긋나면 index.json을 신뢰한다.

---

## 마크다운 변환 (Obsidian -> Discord)

| 입력 | 출력 |
|---|---|
| `[[노트]]`, `[[노트\|표시]]` | 모드별(기본: 게시된 스레드 점프 링크) |
| `![[파일.png]]`, `![alt](경로.png)` | 이미지 슬롯(alt는 `description`) |
| `> [!note] 제목` + 본문 | `> **제목**` + `> 본문` |
| `$수식$`, `==형광==`, `^[각주]`, `[^1]` | 마커 제거 후 텍스트로 강등 |
| 코드펜스 | 원문 유지(원자적 블록) |
| `#`~`######` 헤딩 | 그대로(Discord는 `#`/`##`/`###`만 렌더) |

변환은 **텍스트 블록에만** 적용한다. `![[...]]`는 이미지 임베드이므로 위키링크 치환에서 제외한다(정규식 negative lookbehind `(?<!!)` 필수).

---

## 테스트

```bash
uv run python -m tests.test_m1     # 24/24 passed (pytest 불필요)
```

M1 단위 테스트는 네트워크 없이 돈다. 경계 분할, 코드펜스 원자성, NFD->NFC 길이, 이모지(서로게이트 페어), 위키링크/콜아웃 변환, 버퍼 정책, 결정성, 태그 우선순위·검증, `--dry-run` HTTP 0회를 검증한다.

---

## 파일 구조

```
slop-discord-wiki-sync/
  sync.py              # 엔트리포인트
  sync/
    config.py          # 설정 로드/검증
    vault.py           # 볼트 노트 읽기, frontmatter, NFC
    markdown.py        # 블록 파서 + Obsidian->Discord 변환
    chunker.py         # 슬롯 계획 생성
    state.py           # index.json + frontmatter 상태
    discord_api.py     # REST 클라이언트 + 직렬 큐/레이트리밋
    images.py          # 이미지 해석/정규화/해시
    apply.py           # 계획 -> Discord 반영
    tags.py            # 포럼 태그 해석/검증
    cli.py             # 인자 파싱, 출력
  tests/
    test_m1.py         # M1 단위 테스트
    fixtures/          # 입력 노트 + 기대 계획
  docs/
    HANDOFF-2026-10-15-wiki-links.md
  SPEC.md              # 설계 단일 소스(전체 스펙)
  AGENTS.md            # 에이전트 작업 규칙
  config.example.json  # 설정 예시
  pyproject.toml       # 프로젝트 메타데이터 + PyYAML 의존성 (uv)
  uv.lock              # 의존성 버전 고정
  LICENSE              # VibeCoded AI-Slop License v1.0
```

---

## 문서

- **`SPEC.md`** - 설계 단일 소스. 데이터 모델, 변환 규칙, 청킹, 동기화 알고리즘, REST 계약, CLI 명세, 테스트 계획, 마일스톤.
- **`AGENTS.md`** - 코딩 에이전트용 작업 규칙(놓치기 쉬운 제약 모음).

---

## 라이선스

[VibeCoded AI-Slop License v1.0](LICENSE) - Copyright (c) 2026 ywbird

무엇이든 마음대로 해도 된다. 허가도, 출처 표기도, 수정본 공개도 필요 없다.
