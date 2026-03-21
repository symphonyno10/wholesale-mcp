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

사용자가 "타이레놀 검색해줘"라고 하면:
```
1. list_sites() → 등록 사이트 + 로그인 상태 확인
2. logged_in=false인 사이트 있으면 → auto_login_all()
3. recipe_search(site_id, "타이레놀") → 결과 표시
4. recipe_add_to_cart(site_id, product_code, qty) → 장바구니 추가
5. recipe_sales_ledger(site_id, period="3m") → 매출원장 조회
```

---

## 레시피 자동 생성 워크플로우

### 핵심 원칙: AI가 직접 판단

**규칙 기반 스크립트가 아니라, AI가 MCP 도구를 호출하고 결과를 보고 직접 판단한다.**

`analyze_page_for_recipe()`는 한 번 호출로 3종 분석을 동시에 수행한다:

| 반환 필드 | 내용 | AI가 판단하는 것 |
|-----------|------|-----------------|
| `forms` | 폼 구조, 필드 목록 (is_password, is_username, is_search 자동 태깅) | 로그인/검색 필드명, POST URL |
| `all_links` | visible + hidden 전체 링크 | 메뉴 구조, 매출원장 위치 |
| `js_handlers.ajax_urls` | 외부 JS에서 추출한 모든 AJAX URL | 장바구니 API, 검색 API |
| `js_handlers.cart_functions` | 장바구니/주문 관련 함수명 | cart_add 방식 (form vs API) |
| `html_forms_raw` | form 태그 HTML 원문 | hidden 필드, action URL |
| `tables` | 테이블 구조 (헤더, td 클래스, 행 수) | 검색결과 파싱 셀렉터 |
| `recent_post_requests` | 네트워크 캡처된 POST | 실제 API URL + payload |
| `cookies` | 현재 쿠키 목록 | 인증 쿠키 이름 |

### 공통 원칙: 버튼 발견 → 코드 추적 → API 확정

```
모든 STEP에서 동일한 방법을 사용한다:
1. snapshot_page() 또는 snapshot_iframe()으로 UI 요소(버튼/링크/폼) 발견
2. 요소의 텍스트로 기능 인식 ("로그인", "검색", "담기", "삭제" 등)
3. 해당 요소의 코드를 추적하여 실제 API 확정:
   A) href/action에 URL 직접 있으면 → 즉시 확정
   B) onclick/ng-click 함수명 → execute_js로 함수 소스 추적 → URL 추출
   C) jQuery 이벤트 → 외부 JS fetch → 핸들러 소스 분석
   D) 이벤트가 안 보이면 → click_element() + get_network_log() 캡처
4. capture_form_submission()으로 실제 전송되는 POST 데이터 검증
```

### STEP 1: 로그인

```
1. open_site(url) → snapshot_page()
2. "로그인" 버튼 발견 → 근처의 입력 필드 확인:
   - password 타입 필드 → 비밀번호
   - text 타입 필드 (위에 있는 것) → 아이디
   - 또는 analyze_page_for_recipe("login")의 is_username/is_password 자동 태깅
3. fill_input으로 ID/PW 입력
4. "로그인" 버튼의 코드 추적:
   - form action → 로그인 POST URL
   - SPA는 버튼 클릭 → get_network_log()로 실제 POST 캡처
   - AngularJS는 ng-click 함수 추적 → AJAX URL 확인
5. capture_form_submission() → 실제 전송된 URL + payload 최종 확정
6. get_cookies() → 로그인 후 새로 생긴 쿠키 = success_indicator
   - JWT 사이트: 쿠키 없음 → get_network_log()에서 토큰 응답 확인
```

### STEP 2: 메인 페이지 전체 분석

```
1. analyze_page_for_recipe("search") → 3종 동시 수집:
   - forms: 모든 폼 + 필드 (검색, 주문 등)
   - all_links: 모든 메뉴 (visible + hidden) — "매출원장", "주문내역" 등
   - js_handlers: 외부 JS의 AJAX URL + 함수명
   - html_forms_raw: form 태그 원문
   - iframes: 장바구니 iframe 등

2. snapshot_page()로 버튼/링크 목록 추가 확인:
   - 각 버튼의 텍스트로 기능 파악 ("검색", "담기", "삭제" 등)
   - iframe이 있으면 snapshot_iframe()으로 내부 요소도 확인

3. hidden 메뉴 적극 탐색:
   - all_links에서 visible=false인 링크 → 숨겨진 메뉴 (매출원장, 주문내역, 세금계산서 등)
   - 햄버거 메뉴, 드롭다운, 사이드바 토글 버튼 클릭 → 하위 메뉴 노출
     예: 지오웹의 "조회" 메뉴 클릭 → 매출원장, 세금계산서 등 하위 메뉴 노출
   - execute_js로 CSS display:none 요소의 HTML도 직접 읽을 수 있음
   - get_page_html("nav") 또는 get_page_html("[class*=menu]")로 전체 메뉴 HTML 확인
```

### STEP 3: 검색

```
1. "검색" 또는 "조회" 버튼 발견 → 코드 추적:
   - 버튼이 form 안에 있으면 → form action이 검색 URL
   - onclick/submit 함수가 있으면 → 함수 소스에서 AJAX URL 추출
2. 검색 필드 확인:
   - 버튼 근처의 text input → 키워드 필드
   - select → 필터 옵션 (전체/전문/일반 등)
3. fill_input으로 "타이레놀" 입력 → 버튼 클릭 또는 submit_form()
4. get_network_log() → 실제 검색 API URL + 파라미터 캡처
5. screenshot()으로 검색 결과 화면 직접 확인:
   - 테이블 컬럼에 가격(단가)이 있는지 눈으로 확인
   - 없으면 → 상품 1개 클릭 → screenshot() → 상세 패널에 가격 위치 확인
   - get_network_log()에서 상세 API 캡처 (예: /Home/PartialProductInfo/상품코드)
6. 결과 파싱 구조 확인:
   - HTML 응답: execute_js로 테이블 구조 확인 (행 셀렉터, td 클래스, hidden div)
   - JSON 응답: get_network_log()의 body_preview에서 구조 확인:
     - 배열 `[{...}]` → json_mapping.items_path: ""
     - 객체 `{"list": [{...}]}` → json_mapping.items_path: "list"
     - json_mapping 구조: {"items_path": "경로", "fields": {"product_code": "실제키", ...}}
```

### STEP 3.5: 페이지네이션

```
검색 결과 페이지에서 페이지 이동 요소를 찾는다:

1. "다음", "2", "3" 같은 페이지 링크/버튼 발견 → href 확인:
   - href에 Page=2, page=2 등 파라미터 → type: "html_links"
   - paging_selector: 해당 링크들의 CSS 셀렉터
   - page_url_param: URL의 페이지 파라미터명
   - method_override: 1페이지 POST → 2페이지 GET인 경우

2. 페이지 링크 없으면 → get_network_log()에서 JSON 응답 확인:
   - totalPage/totalRec 필드 있으면 → type: "param"
   - page_param: 요청의 page 파라미터명

3. 둘 다 없으면 → pagination 없음 (한 번에 전체 반환)
```

### STEP 4: 장바구니 추가

```
1. "담기", "장바구니", "주문" 버튼 발견 → 코드 추적:
   A) 버튼이 frmOrder 등 form 안에 있으면 → form 방식
      - form action URL = cart_add URL
      - input[name^=pc_] → product_code_prefix
      - input[name^=qty_] → quantity_prefix
   B) 버튼의 onclick에 함수명(AddCart, ProcessCart 등) → 함수 소스 추적
      - execute_js로 함수.toString() → AJAX URL + payload 파라미터 추출
      - jsf_com_GetAjax 같은 래퍼 안에 실제 URL이 있을 수 있음
   C) SPA: 버튼 클릭 → get_network_log()로 POST 캡처

2. 실제 담기 테스트:
   - 검색 결과에서 재고 있는 상품 선택
   - 담기 실행 → get_network_log()로 실제 요청 확인
```

### STEP 4.5: 장바구니 관리 (조회/삭제/비우기)

```
1. 장바구니 UI 찾기:
   - snapshot_page()에서 iframe 있으면 → snapshot_iframe()으로 내부 확인
   - iframe 없으면 메인 페이지에서 직접 확인

2. 버튼 텍스트로 기능 인식:
   - "장바구니 비우기", "전체삭제" → cart_clear
   - "삭제", 휴지통 아이콘 → cart_delete

3. 발견된 요소의 코드 추적 (공통 원칙 A~D 적용)

4. cart_view: 상품 담기 후 get_network_log()에서 조회 API 자동 캡처
   또는 iframe src URL이 곧 cart_view URL

5. 없는 기능: cart_clear 없으면 생략 (코드가 폴백 자동 수행)
```

### STEP 5: 매출원장

```
1. STEP 2의 all_links에서 "매출원장" 링크 발견 → href 확인
2. click_element()로 해당 링크 클릭 → 페이지 이동
3. snapshot_page()로 매출원장 페이지 UI 확인:
   - 날짜 입력 필드 (sDate, eDate 등)
   - "조회" 버튼 → 코드 추적으로 API URL 확인
   - 옵션 (일별/약품별, transgu 등)
4. 날짜 입력 → "조회" 버튼 클릭 → get_network_log()로 실제 API 캡처
5. 결과 확인:
   - execute_js로 테이블 헤더 확인 (일자, 제품명, 수량, 단가, 매출, 잔액)
   - JSON 응답이면 필드 매핑 확인
```

### STEP 6: 레시피 JSON 작성 + E2E 검증

```
1. 위 분석 결과를 종합하여 레시피 JSON 직접 작성
2. save_recipe(site_id, recipe_json, overwrite=True)
3. E2E 검증 (7가지):
   - recipe_login(site_id, user, pass) → 성공?
   - recipe_search(site_id, "타이레놀") → 결과 있음?
   - recipe_add_to_cart(site_id, product_code, 1) → 성공?
   - recipe_view_cart(site_id) → 방금 담은 상품 보임?
   - recipe_delete_from_cart 또는 recipe_clear_cart → 삭제 성공?
   - recipe_sales_ledger(site_id, period="1m") → 데이터 있음?
   - 전체 통과 시 → 사용자에게 공유 여부 물어보기 → share_recipe()
4. 실패 시: 해당 단계로 돌아가서 분석 재실행 → 레시피 수정
5. 최대 10회 반복
```

### 레시피 JSON 형식 참고

검증된 레시피 예시는 `recipes/wos_nicepharm_com.json`과 `recipes/bpm_geoweb_kr.json` 참조.

**필수 섹션**: login, search, cart_add
**선택 섹션**: sales_ledger, order_submit, search.pagination

**parsing.fields 문법**:
- CSS 셀렉터: `"td:nth-child(3) a"`
- 속성 추출: `"input[name^=pc_]"` + `"attribute": "value"`
- 텍스트 추출: `"attribute": "text"`
- join: `{"selector": "img", "attribute": "alt", "join": true}`

**pagination (검색 페이지네이션, 선택)**:

JSON API 방식 (세화약품 등):
```json
"pagination": {
  "type": "param",
  "page_param": "page",
  "start_page": 1,
  "total_pages_path": "totalPage",
  "max_pages": 10
}
```

HTML 링크 방식 (복산나이스팜, 우정약품 등):
```json
"pagination": {
  "type": "html_links",
  "paging_selector": "div.paging a",
  "page_url_param": "Page",
  "method_override": "GET",
  "max_pages": 5
}
```

pagination이 없으면 1페이지만 가져옴 (하위 호환).

**success_indicator 타입**:
- `cookie`: 특정 쿠키 존재 확인
- `status_code`: HTTP 상태 코드
- `json_field`: JSON 응답의 특정 필드 값
- `contains`: 응답 텍스트에 특정 문자열 포함
- `redirect`: 리다이렉트 URL 포함

### 사이트 유형 즉시 판별법

**`analyze_page_for_recipe()` 결과를 보고 AI가 사이트 유형을 즉시 판별하는 기준:**

```
1. forms 비어있음 + external_js에 angular 없음 + 로그인 후 쿠키 없음
   → 유형 C: SPA (Vue/React/Quasar) + JWT 인증
   → 네트워크 캡처로 API 찾아야 함

2. forms 비어있음 + external_js에 angular 있음 + 로그인 후 쿠키 있음
   → 유형 D: AngularJS + 쿠키 인증
   → execute_js로 scope 접근 + 네트워크 캡처

3. forms 있음 + ajax_urls 비어있음
   → 유형 A: ASP Classic / 전통 form POST
   → form action이 곧 API URL

4. forms 있음 + ajax_urls에 Cart/Search/Data 등 URL 있음
   → 유형 B: ASP.NET MVC / 하이브리드
   → ajax_urls의 URL을 레시피에 사용
```

#### 유형 A: 전통 form POST (복산나이스팜)

| 판별 근거 | 값 |
|-----------|-----|
| `forms` | 있음 (frmSearch, frmOrder) |
| `ajax_urls` | **비어있음** |
| `cookies` | 로그인 후 쿠키 생성 (`wos`) |
| **인증** | 쿠키 기반 |
| **검색** | form POST → HTML 응답 → BeautifulSoup 파싱 |
| **장바구니** | `cart_add.type = "form"` (frmOrder 제출) |
| **상품코드** | `input[name^=pc_]` (form 안에 있음) |
| **가격** | 검색 결과 테이블에 있음 |

#### 유형 B: AJAX 하이브리드 (지오웹 BPM)

| 판별 근거 | 값 |
|-----------|-----|
| `forms` | 있음 (frmSearch) |
| `ajax_urls` | **`/Home/DataCart/`, `/Home/PartialSearchProduct`** 등 |
| `cart_functions` | `AddCart`, `ProcessCart` |
| `all_links` | hidden 메뉴에 매출원장 (visible=false) |
| **인증** | 쿠키 기반 (`GEORELAUTH`) |
| **검색** | AJAX POST → HTML partial 응답 |
| **장바구니** | REST API (`/Home/DataCart/add`) — `execute_js`로 `ProcessCart.toString()` 확인하여 payload 파악 |
| **상품코드** | hidden div (`div.div-product-detail ul li:first-child`) |
| **가격** | 검색 목록에 없음, 상품 클릭 후 상세 패널에만 있음 |

#### 유형 C: SPA + JWT (백제약품)

| 판별 근거 | 값 |
|-----------|-----|
| `forms` | **비어있음** (form 태그 없음) |
| `ajax_urls` | JS 번들이 minified라 **못 뽑을 수 있음** |
| `cookies` | 로그인 후에도 인증 쿠키 없음 |
| **인증** | JWT Bearer 토큰 (`Authorization: Bearer ...`) |
| **검색** | GET JSON API (`/ord/itemSearch?keyword=...`) |
| **장바구니** | POST JSON API (`/ord/addBasket`) |
| **상품코드** | JSON 응답의 `ITEM_CD` 필드 |
| **가격** | JSON 응답의 `ORD_WP2_AMT` 필드 |
| **핵심** | 로그인 응답의 `userData`에서 `custCd`, `userId`, `dlvBrchCd` 추출 → 이후 모든 API에 변수로 사용 |

**SPA + JWT 사이트 레시피 작성 시 필수사항:**
- `login.token` 섹션 필수 (path, header, prefix, user_data_path)
- `search.params`와 `cart_add.payload`에 `{CUST_CD}`, `{USER_ID}` 등 로그인 데이터 변수 사용
- `response_type: "json"` + `json_mapping` 사용 (HTML 파싱 아님)
- `sales_ledger.date_format: "%Y%m%d"` (사이트별 날짜 포맷 다름)

#### 유형 D: AngularJS + 쿠키 (세화약품)

| 판별 근거 | 값 |
|-----------|-----|
| `forms` | **비어있음** (form 태그 없음) |
| `ajax_urls` | **비어있음** (AngularJS $http 호출은 외부 JS 패턴에 안 걸림) |
| `cookies` | 로그인 후 사이트별 쿠키 생성 (`ESEHWAui`) |
| `external_js_files` | `angular.*.js`, `base.js`, `common.js` |
| **인증** | 쿠키 기반 (JWT 아님) |
| **검색** | GET JSON API (`/common/ajax/physic.asp?mode=list&physicNm=...`) |
| **장바구니** | POST (`/common/ajax/bag.asp` + `mode=add&physicCds=...&qtys=...`) |
| **상품코드** | JSON 응답의 `physicCd` 필드 |
| **가격** | JSON 응답의 `unitCost` 필드 |
| **매출원장** | 서버 사이드 HTML 렌더링 (AngularJS 데이터 바인딩 아님) |

**유형 D 판별**: forms 비어있음 + external_js에 `angular` 있음 + cookies에 인증 쿠키 있음 (JWT와 구분)

**유형 D 사이트에서 API 찾는 방법:**
1. `analyze_page_for_recipe()`로는 API를 못 찾음 (AngularJS $http는 패턴에 안 걸림)
2. **forms가 비어있으므로 HTML에서 파라미터를 추출할 수 없음**
3. `execute_js`로 Angular scope 함수 목록 확인:
   ```javascript
   angular.element(document.querySelector('[data-ng-controller]')).scope()
   ```
4. **Playwright fill이 Angular 모델을 업데이트 안 할 수 있음**
   → `execute_js`로 scope에 직접 값 설정:
   ```javascript
   scope.$apply(() => { scope.frm.id = 'user'; scope.frm.pw = 'pass'; });
   scope.Login();
   ```
5. 검색도 DOM input 값을 직접 설정해야 할 수 있음:
   ```javascript
   document.querySelector('#tx_pnm').value = '타이레놀';
   scope.GetList();
   ```
6. `get_network_log()`로 실제 API URL 캡처
7. **캡처된 POST body의 파라미터를 그대로 레시피 params에 넣을 것**
   - HTML forms에서 추출한 파라미터는 불완전 (AngularJS가 동적으로 추가하는 파라미터가 있음)
   - 반드시 네트워크 캡처 결과를 기준으로 레시피 작성

#### SPA (유형 C/D) 사이트에서 API 찾는 방법

SPA 사이트는 `analyze_page_for_recipe()`의 `ajax_urls`로 못 찾을 수 있다.
이 경우 **실제로 클릭 → 네트워크 캡처**로 찾아야 한다:

```
1. open_site(login_url) → fill_input → click(로그인 버튼) → get_network_log("login")
   → POST /jwt/login 또는 POST /common/ajax/user.asp 캡처

2. fill_input(검색어) → click(검색 버튼) → get_network_log("item" 또는 "physic")
   → GET /ord/itemSearch?keyword=... 또는 GET /common/ajax/physic.asp?mode=list 캡처

3. click(담기 버튼) → get_network_log("basket" 또는 "bag")
   → POST /ord/addBasket 또는 POST /common/ajax/bag.asp 캡처

4. click(매출원장 메뉴) → get_network_log("ledger")
   → GET /ordLedger/listSearch?... 캡처
```

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
ProcessCart.toString().substring(0, 500)
// → jsf_com_GetAjax("/Home/DataCart/" + n, u, ...)
// → u = {productCode: t, moveCode: i, orderQty: r}
```

**검색 결과 hidden div 구조 확인:**
```javascript
JSON.stringify(Array.from(document.querySelector('tr.tr-product-list div[style*=display]').querySelectorAll('li')).map((li,i) => ({i, text:li.textContent.trim()})))
// → [{i:0, text:"094941"}, ...] → 0번째가 상품코드
```

**검색 결과 테이블 행 구조 확인:**
```javascript
JSON.stringify(Array.from(document.querySelector('tr.tr-product-list').querySelectorAll('td')).map((td,i) => ({i, class:td.className, text:td.textContent.trim().substring(0,30)})))
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

## 새 사이트 추가 프로세스

사용자가 새 도매 사이트 URL + 크레덴셜을 주면, 아래 프로세스를 순서대로 실행한다.

### PHASE 1: 탐색 + 레시피 생성

```
1. open_site(url) → analyze_page_for_recipe("login")
   → 사이트 유형 즉시 판별 (forms 유무, ajax_urls 유무)
   → 로그인 필드명, POST URL 확인

2. fill_input → capture_form_submission() 또는 click(로그인 버튼)
   → 로그인 POST 캡처 (URL, payload, 응답)
   → get_cookies() → 인증 쿠키 or JWT 토큰 확인

3. analyze_page_for_recipe("search")
   → 검색 필드, 메뉴 구조, ajax_urls, cart_functions 한 번에 파악
   → all_links에서 매출원장 위치 확인

4. fill_input(검색어) → submit/click → get_network_log()
   → 검색 API URL + 파라미터 캡처
   → 결과 구조 확인 (HTML 테이블 or JSON)

5. 장바구니 방식 결정:
   - ajax_urls에 cart/basket URL 있으면 → execute_js로 함수 소스 확인 → API payload 파악
   - 없으면 → form 방식 확정
   - SPA면 → 담기 버튼 클릭 → get_network_log() 캡처

6. 매출원장: all_links에서 "매출원장" 클릭 → get_network_log() 캡처

7. 레시피 JSON 작성 → save_recipe()
```

### PHASE 2: E2E 테스트 (4가지 기능)

```python
# 반드시 4가지 모두 테스트
recipe_login(site_id, user, pass)           # → 성공?
recipe_search(site_id, "타이레놀")          # → 결과 있음?
recipe_add_to_cart(site_id, product_code, 1) # → 성공?
recipe_sales_ledger(site_id, period="1m")    # → 데이터 있음?
```

실패한 기능이 있으면 PHASE 3으로 진행.
4/4 성공이면 PHASE 4로 건너뛴다.

### PHASE 3: 실패 분석 + 수정 루프 (최대 10회)

```
for iteration in range(1, 11):
    1. E2E 테스트 실행
    2. 실패한 기능 확인
    3. 실패 원인 분석:
       - 로그인 실패 → success_indicator 잘못됨? payload 필드 누락?
       - 검색 실패 → URL 잘못됨? 파라미터 누락? 파싱 셀렉터 잘못됨?
       - 장바구니 실패 → API URL? payload 필드명? 인증 헤더?
       - 매출원장 실패 → URL? 날짜 포맷? JSON 파싱 경로?
    4. 브라우저로 돌아가서 해당 기능 재분석
       - get_network_log()로 실제 요청 재확인
       - execute_js()로 JS 함수 소스 확인
    5. 레시피 수정 → save_recipe(overwrite=True)
    6. 4/4 성공하면 루프 종료
```

### PHASE 4: 코드/프롬프트 강화 판단

E2E 성공 후 반드시 확인:

```
Q1: 기존 SiteExecutor 코드로 처리 가능했는가?
    - YES → 코드 수정 불필요
    - NO → 어떤 기능이 부족했는가?
      → site_executor.py 수정 (새 인증 방식, 새 파싱 로직 등)

Q2: 이 사이트의 유형이 기존 3가지 (form/AJAX/SPA+JWT)에 해당하는가?
    - YES → 프롬프트 수정 불필요
    - NO → 새 유형 발견
      → CLAUDE.md "사이트 유형 즉시 판별법"에 유형 D 추가

Q3: analyze_page_for_recipe()가 필요한 정보를 다 반환했는가?
    - YES → 도구 수정 불필요
    - NO → 어떤 정보가 부족했는가?
      → server.py의 analyze 도구 보강

Q4: 전체 회귀 테스트 통과하는가?
    - 기존 모든 사이트 E2E 재실행 (수정한 코드가 기존 사이트를 깨뜨리지 않는지)
```

### PHASE 5: 회귀 테스트

코드/프롬프트를 수정했으면 기존 전체 사이트 E2E 재실행:

```python
# 모든 레시피 파일에 대해 자동 실행
for recipe_file in recipes/*.json:
    test(recipe_file)  # 4/4 통과해야 함
```

하나라도 실패하면 수정이 기존 사이트를 깨뜨린 것이므로 즉시 수정.

### 체크리스트 요약

새 사이트 추가 시 아래 체크리스트를 따른다:

- [ ] PHASE 1: MCP 도구로 사이트 탐색 + 레시피 생성
- [ ] PHASE 2: E2E 4/4 테스트
- [ ] PHASE 3: 실패 시 수정 루프 (최대 10회)
- [ ] PHASE 4: 코드/프롬프트 강화 필요 여부 판단
- [ ] PHASE 5: 전체 회귀 테스트 (기존 사이트 포함)
