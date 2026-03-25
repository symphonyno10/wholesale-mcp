# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **MCP 프롬프트/리소스**: 레시피 생성 워크플로우, JSON 스키마, 사이트 유형 판별 가이드는
> MCP 서버의 prompts와 resources로 제공됩니다. 어떤 AI 도구에서든 사용 가능합니다.
> - `generate_recipe(site_url, site_name)` — 레시피 자동 생성 워크플로우
> - `recipe_json_schema()` — 레시피 JSON 형식 가이드
> - `site_type_guide()` — 사이트 유형 판별 가이드
> - `recipes://list` — 등록된 레시피 목록
> - `recipes://credentials-template` — credentials.json 템플릿

## 프로젝트 개요

wholesale-mcp는 AI 코딩 어시스턴트(Claude Code, Cursor 등)가 도매 사이트를 직접 탐색, 분석, 주문할 수 있게 해주는 MCP(Model Context Protocol) 서버입니다. 시스템은 세 가지 모드로 작동합니다:

1. **브라우저 탐색 (Playwright)**: 새 사이트 분석 및 네트워크 트래픽 캡처용
2. **레시피 실행 (HTTP)**: JSON "레시피"를 사용한 자동 로그인/검색/장바구니 작업
3. **레시피 자동 생성 (NEW)**: AI가 브라우저 분석 결과로 자동으로 레시피 JSON 생성 ⭐

## 아키텍처

### 핵심 컴포넌트

- **[server.py](server.py)**: FastMCP 프레임워크를 사용하는 MCP 서버 진입점. 24개의 도구를 제공:
  - 브라우저 탐색 도구 (Playwright 기반) - 10개
  - 레시피 기반 주문 도구 (HTTP 기반) - 6개
  - 레시피 자동 생성 도구 (NEW) - 4개 ⭐
  - 세션 관리 도구 - 4개

- **[site_executor.py](site_executor.py)**: 레시피 JSON 파일을 읽고 `requests.Session` + BeautifulSoup으로 실제 HTTP 요청을 실행하는 엔진

- **[recipe_normalizer.py](recipe_normalizer.py)**: AI가 생성한 레시피 JSON을 정규 형식으로 변환 (AI 출력은 키 이름이 매번 다를 수 있음)

- **[recipe_schema.py](recipe_schema.py)**: 레시피, 상품, 주문에 대한 데이터 모델 및 검증

- **[recipes/](recipes/)**: 사이트별 JSON 레시피 파일 디렉토리 (예: `wos_nicepharm_com.json`)

### 상태 관리

[server.py](server.py)에서 전역 상태 관리:
- `_playwright`, `_browser`, `_page`: 싱글톤 Playwright 브라우저 인스턴스 (lazy 초기화)
- `_network_log`: 브라우저 탐색 중 캡처된 HTTP 요청/응답
- `_executors`: `site_id` → `SiteExecutor` 인스턴스 딕셔너리
- `_recipes`: `site_id` → recipe JSON 딕셔너리

## 개발 명령어

### 설치
```bash
pip install -r requirements.txt
playwright install chromium
```

### MCP 서버 실행
```bash
python server.py
```

서버는 MCP 연결을 위해 stdio transport 모드로 실행됩니다.

### 레시피 실행 테스트

AI 어시스턴트 없이 레시피를 테스트하려면 Python 대화형 모드 사용:

```python
from site_executor import SiteExecutor
import json

# 레시피 로드
with open('recipes/wos_nicepharm_com.json', 'r') as f:
    recipe = json.load(f)

# 작업 실행
executor = SiteExecutor(recipe)
executor.login('username', 'password')
results = executor.search('타이레놀')
executor.add_to_cart(results[0].product_code, 1)
```

## 레시피 JSON 구조

레시피는 각 도매 사이트의 HTTP 엔드포인트와 파싱 규칙을 정의합니다. 주요 섹션:

- **메타데이터**: `site_id`, `site_name`, `site_url`, `encoding`, `recipe_version`
- **login**: HTTP 메서드, URL, 페이로드 템플릿, 성공 판단 지표
- **search**: 쿼리 파라미터, HTML/JSON 응답 파싱 셀렉터
- **cart_add**: HTTP 엔드포인트 또는 폼 기반 제출
- **파싱 필드**: HTML 셀렉터 또는 JSON 경로를 상품 속성에 매핑

normalizer([recipe_normalizer.py](recipe_normalizer.py))는 AI 생성 레시피의 변형을 처리합니다 (예: `name` → `product_name`, `maker` → `manufacturer`).

## 크레덴셜 구조

### 개인정보 분리 원칙

- **레시피** (`recipes/*.json`) — 사이트 API 구조만 담음. 개인정보 없음. 공개 가능.
- **크레덴셜** (`credentials.json`) — ID/PW + 사용자별 고유 값. 비공개 (.gitignore).

레시피에서 사용자별로 다른 값은 `{VEN_CD}`, `{CUST_CD}` 등의 변수로 작성한다.
실제 값은 `credentials.json`의 `site_params`에 저장되고, 실행 시 자동 치환된다.

### credentials.json 구조

```json
{
  "site_id": {
    "username": "로그인 ID",
    "password": "비밀번호",
    "site_params": {           // 선택. 로그인 응답에서 못 얻는 사용자별 고유 값
      "VEN_CD": "거래처 코드",
      "VEN_NM": "약국 이름"
    }
  }
}
```

- `site_params`는 사이트에 따라 필요할 수도, 안 할 수도 있음
- JWT 사이트(백제약품 등)는 로그인 응답의 `userData`에서 `CUST_CD` 등을 자동 추출 → `site_params` 불필요
- form POST 사이트(복산나이스팜 등)의 매출원장은 거래처 코드가 필요 → `site_params`에 저장

### 변수 치환 흐름

```
credentials.json의 site_params    →  _login_data에 병합
로그인 응답의 userData (JWT)       →  _login_data에 병합
레시피의 {VEN_CD}, {CUST_CD} 등   →  _resolve_payload에서 _login_data로 치환
```

## 일상 사용 가이드

### 새 사이트 등록 (처음 1회)

사용자가 URL + ID + PW를 알려주면:
```
1. register_site(url, username, password, site_name)
   → credentials.json에 자동 저장 (ID/PW)
   → 레시피 유무 확인

2. 레시피 없으면 → "레시피 자동 생성 워크플로우" 진행 (아래 섹션)
   → 레시피 생성 중 거래처 코드 등 발견 시 → credentials.json에 site_params 추가
3. 레시피 있으면 → recipe_login(site_id)로 바로 사용
```

### 서버 시작 후 (매 세션)

```
auto_login_all()
→ credentials.json에 등록된 모든 사이트에 자동 로그인
→ site_params도 자동 로드되어 매출원장 등에서 변수 치환 가능
→ 이후 recipe_search, recipe_add_to_cart 즉시 사용 가능
```

### 검색/주문/매출원장 (일상 사용)

**중요: list_sites()의 available_features를 확인하고 사용 가능한 기능만 호출할 것.**
예: cart_delete가 false인 사이트에서는 recipe_delete_from_cart를 호출하지 않는다.

사용자가 "타이레놀 검색해줘"라고 하면:
```
1. list_sites() → 등록 사이트 + available_features + 로그인 상태 확인
2. logged_in=false인 사이트 있으면 → auto_login_all()
3. recipe_search(site_id, "타이레놀") → 결과 표시
4. recipe_add_to_cart(site_id, product_code, qty) → 장바구니 추가
5. recipe_sales_ledger(site_id, period="3m") → 매출원장 조회
```

## 파일 접근 규칙 (중요!)

### MCP 데이터 디렉토리

모든 데이터 파일(레시피, 크레덴셜, 매출원장, 검색결과 등)은 **DATA_DIR**에 저장됩니다.
DATA_DIR 위치: `~/.wholesale-mcp` (Linux/Mac) 또는 `%APPDATA%/wholesale-mcp` (Windows)

**AI 클라이언트(Claude Desktop, Cursor 등)의 자체 파일 도구(Read, Write 등)로는
MCP 서버의 DATA_DIR에 접근할 수 없습니다.**
반드시 MCP 서버가 제공하는 파일 도구를 사용해야 합니다:

### 파일 관리 도구

| 도구 | 용도 |
|------|------|
| `list_data_files(subdirectory)` | 파일 목록 조회 ("data", "recipes" 등) |
| `read_data_file(path, offset, limit, keyword)` | 파일 읽기 (JSON 페이징/검색 지원) |
| `write_data_file(path, content, format)` | 파일 쓰기 (CSV, JSON, 텍스트) |
| `export_ledger_csv(site_id, period)` | 매출원장 CSV 내보내기 |

### 대량 데이터 처리 워크플로우

매출원장 통계/그래프 작업 시:
```
1. export_ledger_csv(site_id, period="1y") → CSV 파일 자동 저장
2. read_data_file("data/ledger_site.json", keyword="타이레놀") → 필터링 조회
3. write_data_file("data/analysis.csv", csv_content) → 분석 결과 저장
4. list_data_files("data") → 저장된 파일 확인
```

**주의**: `saved_to` 응답에 포함된 경로는 DATA_DIR 기준 상대경로입니다.
`read_data_file('data/ledger_site.json')`처럼 그대로 사용하면 됩니다.

## 중요한 구현 세부사항

### 네트워크 캡처 ([server.py](server.py):80-112)

`_on_response` 핸들러는 정적 자산(.css, .js, 이미지)을 필터링하고 다음을 캡처:
- HTTP 메서드, URL, 상태, content-type
- POST 요청 본문
- JSON 응답 미리보기 (500자)
- HTML 본문 크기

### 폼 기반 장바구니 추가 ([site_executor.py](site_executor.py):521-582)

`cart_add.type`이 "form"일 때, executor는 마지막 검색 결과 HTML을 파싱하여 주문 폼을 찾고, 모든 hidden 필드를 채우고, 상품 코드를 매칭한 후 제출합니다. 단순 REST API가 없는 사이트를 처리합니다.

### 인증 감지 ([site_executor.py](site_executor.py):90-112)

성공 지표는 다음이 될 수 있습니다: 쿠키 존재, 리다이렉트 URL, JSON 필드 값, 상태 코드, 텍스트 내용. 또한 폴백 메커니즘([site_executor.py](site_executor.py):163-177)이 있어 명시적 지표 실패 시 새 쿠키 또는 에러 키워드 부재를 확인합니다.

### 요청 쓰로틀링 ([site_executor.py](site_executor.py):34-62)

executor는 레시피의 `connection.request_interval_ms`를 준수하여 사이트에 과부하를 주지 않습니다.

### 레시피 자동 생성 도구

1. **analyze_page_for_recipe(page_type)**: 한 번 호출로 3종 동시 분석 수행.
   - `page_type`: "login", "search", "cart", "sales_ledger", "auto"
   - 반환: forms, all_links(hidden 포함), js_handlers(외부 JS의 AJAX URL + 함수명), html_forms_raw, tables, buttons, cookies, recent_post_requests
   - 외부 JS 파일(Order.js 등)을 자동으로 fetch하여 `$.post`, `GetAjax`, `/Home/` 등 패턴의 URL 추출

2. **capture_form_submission(form_selector)**: 폼 제출 + POST 요청 캡처.
   - 실제 전송된 URL, method, post_data, status를 반환
   - AJAX/fetch 요청도 캡처하여 SPA 사이트 대응

3. **save_recipe(site_id, recipe_json, overwrite)**: 레시피 저장 + 캐시 무효화.

### execute_js 추가 분석 패턴

`analyze_page_for_recipe()`가 자동으로 JS 분석을 수행하지만, 결과가 불충분할 때 AI가 `execute_js()`로 추가 확인:

**장바구니 함수 소스코드 확인:**
```javascript
// cart_functions에서 발견한 함수명.toString()으로 소스 확인
functionName.toString().substring(0, 500)
// → 내부에서 호출하는 AJAX URL과 payload 파라미터 추출
```

**검색 결과 테이블 행 구조 확인:**
```javascript
// 검색 결과 테이블의 각 td 구조 파악
JSON.stringify(Array.from(document.querySelector('검색결과행셀렉터').querySelectorAll('td')).map((td,i) => ({i, class:td.className, text:td.textContent.trim().substring(0,30)})))
```

## 설정

프로젝트 루트에 `.mcp.json` 생성:

```json
{
  "mcpServers": {
    "wholesale-tools": {
      "command": "python",
      "args": ["path/to/wholesale-mcp/server.py"],
      "cwd": "path/to/wholesale-mcp"
    }
  }
}
```

## HTML 파싱 필드 문법 ([site_executor.py](site_executor.py):360-431)

필드 스펙 지원 기능:
- 의사 요소가 있는 CSS 셀렉터: `"td.price::text"`
- 속성 추출: `"input[name='code']::attr(value)"`
- 정규식 추출: `"td.name|regex(\\d+T)"`
- 여러 요소 조인: `{"selector": "img", "attribute": "alt", "join": true}`
