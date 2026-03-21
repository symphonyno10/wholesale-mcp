# wholesale-mcp

AI 어시스턴트(Claude Code, Cursor 등)가 도매 사이트에 로그인하고, 약품을 검색하고, 장바구니에 담고, 매출원장을 조회하는 MCP 서버.

## 어떻게 작동하나?

1. **사이트 등록** — URL + ID + PW를 주면 AI가 사이트를 분석해서 "레시피" JSON을 자동 생성
2. **레시피 실행** — 생성된 레시피로 HTTP 기반 로그인/검색/장바구니/매출원장 실행
3. **일상 사용** — "타이레놀 검색해줘" 한 마디면 등록된 사이트에서 검색

## 지원 사이트 (5개 검증 완료)

| 사이트 | 유형 | 로그인 | 검색 | 장바구니 | 매출원장 |
|--------|------|:---:|:---:|:---:|:---:|
| 복산나이스팜 | ASP Classic (form) | ✅ | ✅ | ✅ | ✅ |
| 지오웹 BPM | ASP.NET MVC (AJAX) | ✅ | ✅ | ✅ | ✅ |
| 백제약품 | Quasar SPA (JWT) | ✅ | ✅ | ✅ | ✅ |
| 우정약품 | ASP Classic (form) | ✅ | ✅ | ✅ | ✅ |
| 세화약품 | AngularJS (AJAX) | ✅ | ✅ | ✅ | ✅ |

## 빠른 시작

### 1. 설치

**방법 A: pip (가장 간단)**
```bash
pip install wholesale-mcp
playwright install chromium
```

**방법 B: 원커맨드 스크립트**

Windows (PowerShell):
```powershell
irm https://raw.githubusercontent.com/symphonyno10/wholesale-mcp/main/install.ps1 | iex
```

macOS / Linux:
```bash
curl -fsSL https://raw.githubusercontent.com/symphonyno10/wholesale-mcp/main/install.sh | bash
```

<details>
<summary>방법 C: 수동 설치</summary>

Windows (PowerShell):
```powershell
git clone https://github.com/symphonyno10/wholesale-mcp.git
cd wholesale-mcp
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

macOS / Linux:
```bash
git clone https://github.com/symphonyno10/wholesale-mcp.git
cd wholesale-mcp
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```
</details>

### 2. MCP 연결

AI 도구(Claude Desktop, Claude Code, Cursor 등)의 `.mcp.json`에 설정을 추가합니다.

**pip으로 설치한 경우 (방법 A):**
```json
{
  "mcpServers": {
    "wholesale-tools": {
      "command": "wholesale-mcp"
    }
  }
}
```

<details>
<summary>git clone / 스크립트로 설치한 경우 (방법 B, C)</summary>

경로를 직접 지정해야 합니다. `홍길동`을 본인 사용자명으로 바꾸세요.

Windows:
```json
{
  "mcpServers": {
    "wholesale-tools": {
      "command": "C:/Users/홍길동/wholesale-mcp/venv/Scripts/python.exe",
      "args": ["C:/Users/홍길동/wholesale-mcp/server.py"],
      "cwd": "C:/Users/홍길동/wholesale-mcp"
    }
  }
}
```

macOS/Linux:
```json
{
  "mcpServers": {
    "wholesale-tools": {
      "command": "/Users/홍길동/wholesale-mcp/venv/bin/python",
      "args": ["/Users/홍길동/wholesale-mcp/server.py"],
      "cwd": "/Users/홍길동/wholesale-mcp"
    }
  }
}
```

> | 항목 | 설명 |
> |------|------|
> | `command` | 가상환경의 python 실행파일 경로 |
> | `args` | server.py 파일의 절대 경로 |
> | `cwd` | 프로젝트 폴더 (recipes/가 있는 곳) |

</details>

**연결 확인:** AI 도구를 재시작한 후 `list_sites()`를 호출해서 사이트 목록이 나오면 성공입니다.

### 3. 크레덴셜 설정

`credentials.json`을 생성하여 사이트별 ID/PW를 저장합니다. 이 파일은 `.gitignore`에 포함되어 git에 올라가지 않습니다.

```bash
cp credentials.example.json credentials.json
# credentials.json을 편집하여 실제 ID/PW 입력
```

```json
{
  "wos_nicepharm_com": {
    "username": "your_id",
    "password": "your_password",
    "site_params": {
      "VEN_CD": "your_vendor_code",
      "VEN_NM": "your_pharmacy_name"
    }
  },
  "bpm_geoweb_kr": {
    "username": "your_id",
    "password": "your_password"
  }
}
```

- `username`, `password` — 사이트 로그인 정보
- `site_params` — 사이트별 사용자 고유 값 (거래처 코드 등). 레시피에서 `{VEN_CD}` 같은 변수로 참조됨. 로그인 응답에서 자동으로 얻을 수 없는 값만 여기에 넣으면 됨.

또는 AI에게 직접 등록을 요청할 수 있습니다:
```
사용자: "https://wos.nicepharm.com id:my_id pass:my_pass 등록해줘"
AI → register_site(url, id, pw) → credentials.json에 자동 저장
```

### 4. 일상 사용

#### 예시 1: 전체 사이트 검색

```
사용자: "타이레놀 검색해줘"

AI → auto_login_all()
   → recipe_search("wos_nicepharm_com", "타이레놀")
   → recipe_search("bpm_geoweb_kr", "타이레놀")
   → recipe_search("ibjp_co_kr", "타이레놀")
   → recipe_search("wjwp_co_kr", "타이레놀")
   → recipe_search("esehwa_co_kr", "타이레놀")
   → 5개 사이트 결과를 통합 테이블로 표시
```

#### 예시 2: 조건부 장바구니 추가

```
사용자: "650mg 타이레놀 5개씩 재고 있는 곳에만 담아줘"

AI → 5개 사이트 검색 결과에서 "650" 포함 상품 필터
   → 재고 > 0인 사이트만 선별
   → recipe_add_to_cart("bpm_geoweb_kr", "096507", 5)    # 재고 400 → 담기
   → recipe_add_to_cart("ibjp_co_kr", "14002722", 5)     # 재고 19836 → 담기
   → recipe_add_to_cart("esehwa_co_kr", "64326", 5)      # 재고 31 → 담기
   → "복산나이스팜: 재고 0 — 건너뜀"
   → 결과 안내:
     ✅ 지오웹BPM: 5개 담김
     ✅ 백제약품: 5개 담김
     ✅ 세화약품: 5개 담김
     ❌ 복산나이스팜: 재고 없음
     ❌ 우정약품: 650mg 6T 없음 (500T만 있음)
```

#### 예시 3: 매출원장 조회

```
사용자: "최근 3개월 매출원장 보여줘"

AI → recipe_sales_ledger("wos_nicepharm_com", period="3m")
   → 결과:
     2026/03/20 | 케이캡구강붕해정(병) | 50/30T | 4 | 39,000 | 156,000
     2026/03/20 | 아타칸정           | 16/30T | 1 | 24,390 |  24,390
     ...
     총 3,427건 (약품별 상세)
```

#### 예시 4: 새 사이트 등록 + 레시피 자동 생성

```
사용자: "https://new-pharmacy.co.kr id:myid pass:mypw 등록해줘"

AI → register_site("https://new-pharmacy.co.kr", "myid", "mypw", "신규약품")
   → open_site("https://new-pharmacy.co.kr")
   → analyze_page_for_recipe("login")    # 폼 구조 자동 분석
   → fill_input + capture_form_submission # 로그인 POST 캡처
   → analyze_page_for_recipe("search")   # 검색/장바구니/메뉴 한 번에 파악
   → fill_input("타이레놀") + submit_form # 검색 실행 → API 캡처
   → save_recipe("new_pharmacy_co_kr", recipe_json)
   → recipe_login → recipe_search → recipe_add_to_cart  # E2E 검증
   → "레시피 생성 완료. 로그인/검색/장바구니 모두 정상 작동합니다."
```

#### 예시 5: 가격 비교

```
사용자: "타이레놀 500mg 정 가격 비교해줘"

AI → 5개 사이트 검색 결과에서 "타이레놀정500mg" 필터
   → 가격 비교 테이블:
     | 사이트       | 상품명              | 규격   | 단가    | 재고  |
     |-------------|--------------------| ------|--------|------|
     | 복산나이스팜  | 타이레놀정(존슨)      | 10T   | 2,330원 | 0    |
     | 백제약품     | 타이레놀정500mg(존슨)  | 30T   | 6,600원 | 100  |
     | 우정약품     | 타이레놀정500mg       | 10T   | 2,400원 | 0    |
     | 세화약품     | 약국용타이레놀정500mg  | 20T   | 3,600원 | 0    |
     → "백제약품이 30T 6,600원으로 재고 있음. T당 220원으로 가장 저렴합니다."
```

## 보안

| 파일 | 내용 | git |
|------|------|-----|
| `credentials.json` | 사이트별 ID/PW + 거래처 코드 | **제외** (.gitignore) |
| `.mcp.json` | MCP 서버 로컬 경로 | **제외** (.gitignore) |
| `recipes/*.json` | 사이트 API 구조 (개인정보 없음) | 포함 |
| `*.py` | 소스 코드 | 포함 |

- 레시피 JSON에는 개인정보가 포함되지 않습니다. 거래처 코드, 약국 이름 등은 `{VEN_CD}`, `{VEN_NM}` 변수로 처리되며, 실제 값은 `credentials.json`에만 저장됩니다.
- JWT 토큰 기반 사이트(백제약품 등)는 로그인 응답에서 `CUST_CD`, `USER_ID` 등을 자동 추출하므로 `site_params`가 불필요합니다.

## MCP 도구 목록

### 사이트 관리
| 도구 | 설명 |
|------|------|
| `register_site(url, id, pw, name)` | 새 사이트 등록 (credentials.json 저장) |
| `auto_login_all()` | 등록된 전체 사이트 일괄 로그인 |
| `list_sites()` | 사이트 목록 + 로그인 상태 + credentials 등록 여부 |

### 레시피 실행 (HTTP)
| 도구 | 설명 |
|------|------|
| `recipe_login(site_id)` | 로그인 (credentials.json에서 자동 로드) |
| `recipe_search(site_id, keyword)` | 약품 검색 |
| `recipe_add_to_cart(site_id, code, qty)` | 장바구니 추가 |
| `recipe_sales_ledger(site_id, period)` | 매출원장 조회 |
| `get_recipe(site_id)` | 레시피 JSON 조회 |
| `get_session_info(site_id)` | 세션 상태 확인 |

### 브라우저 탐색 (레시피 생성용)
| 도구 | 설명 |
|------|------|
| `open_site(url)` | 브라우저에서 URL 열기 |
| `analyze_page_for_recipe(type)` | 페이지 3종 분석 (DOM + HTML + JS) |
| `snapshot_page()` | 인터랙티브 요소 목록 |
| `click_element(selector)` | 요소 클릭 |
| `fill_input(selector, value)` | 입력 필드 채우기 |
| `submit_form(selector)` | 폼 제출 |
| `capture_form_submission(selector)` | 폼 제출 시 POST 캡처 |
| `get_network_log(filter)` | 네트워크 요청 로그 |
| `get_page_html(selector)` | HTML 소스 |
| `execute_js(code)` | JavaScript 실행 |
| `screenshot()` | 스크린샷 |
| `save_recipe(site_id, json, overwrite)` | 레시피 JSON 저장 |

### 세션
| 도구 | 설명 |
|------|------|
| `get_cookies()` | 브라우저 쿠키 |
| `set_cookies(json)` | 쿠키 주입 |
| `close_browser()` | 브라우저 종료 |

## 레시피 생성 과정

AI가 새 사이트를 분석하여 자동으로 레시피를 만드는 과정:

```
1. register_site(url, id, pw)          # credentials 저장
2. open_site(url)                      # 브라우저 열기
3. analyze_page_for_recipe("login")    # 로그인 폼 분석
4. fill_input + capture_form_submission # 로그인 POST 캡처
5. analyze_page_for_recipe("search")   # 메뉴 + 검색 + 장바구니 API 한 번에 파악
6. fill_input + submit_form            # 검색 실행 → 네트워크 캡처
7. get_network_log()                   # 검색 API URL + 파라미터
8. save_recipe(site_id, json)          # 레시피 저장
9. recipe_login → recipe_search → recipe_add_to_cart  # E2E 검증
```

## 사이트 유형 자동 판별

AI가 `analyze_page_for_recipe()` 결과를 보고 사이트 유형을 즉시 판별:

| 조건 | 유형 | 예시 |
|------|------|------|
| forms 있음 + ajax_urls 비어있음 | form POST | 복산나이스팜, 우정약품 |
| forms 있음 + ajax_urls 있음 | AJAX 하이브리드 | 지오웹 BPM |
| forms 비어있음 + 로그인 후 쿠키 없음 | SPA + JWT | 백제약품 |
| forms 비어있음 + angular + 쿠키 있음 | AngularJS | 세화약품 |

## Python 직접 사용 (AI 없이)

레시피 JSON + `site_executor.py`만 있으면 AI 없이 Python 코드로 바로 사용할 수 있습니다.

```python
from site_executor import SiteExecutor
import json

# 레시피 로드
with open('recipes/wos_nicepharm_com.json') as f:
    recipe = json.load(f)

executor = SiteExecutor(recipe)

# 로그인 (site_params는 거래처 코드 등 사용자별 고유 값)
executor.login('my_id', 'my_pw', site_params={
    'VEN_CD': '12345',
    'VEN_NM': '우리약국'
})

# 검색
products = executor.search('타이레놀')
for p in products:
    print(f'{p.product_name} | {p.unit_price:,.0f}원 | 재고:{p.stock_quantity}')

# 장바구니 추가
executor.add_to_cart(products[0].product_code, 1)

# 매출원장 조회 (최근 3개월)
entries = executor.get_sales_ledger(period='3m')
for e in entries:
    print(f'{e.transaction_date} | {e.product_name} | {e.sales_amount:,.0f}원')
```

MCP 서버(`server.py`)는 이 SiteExecutor를 AI가 호출할 수 있게 감싼 것입니다.

## 파일 구조

```
wholesale-mcp/
├── server.py                # MCP 서버 (모든 도구 정의)
├── site_executor.py         # 레시피 실행 엔진 (HTTP)
├── browser_engine.py        # Playwright 브라우저 래퍼
├── recipe_normalizer.py     # AI 생성 레시피 정규화
├── recipe_schema.py         # 데이터 모델
├── requirements.txt         # 의존성
├── CLAUDE.md                # AI 프롬프트 가이드
├── recipes/                 # 사이트별 레시피 JSON (공개, 개인정보 없음)
│   ├── wos_nicepharm_com.json
│   ├── bpm_geoweb_kr.json
│   └── ...
├── recipes.example/         # 레시피 템플릿 예시
├── credentials.example.json # 크레덴셜 템플릿
├── credentials.json         # 실제 ID/PW (.gitignore, 비공개)
└── .mcp.json                # MCP 연결 설정 (.gitignore, 비공개)
```

## 라이선스

MIT
