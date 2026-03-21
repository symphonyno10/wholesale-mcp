"""
도매 사이트 MCP 서버 (wholesale-mcp)

AI 코딩 어시스턴트(Claude Code, Cursor 등)에서 MCP 연결하여
도매 사이트를 직접 탐색/분석/주문할 수 있는 도구 모음.

기능:
  1. 브라우저 탐색 (Playwright) - 사이트 구조 분석, 네트워크 캡처
  2. 레시피 실행 (SiteExecutor) - HTTP 기반 로그인/검색/장바구니
  3. 레시피 자동 생성 - 브라우저 탐색 결과로 레시피 JSON 생성
  4. 세션 관리 - 쿠키 조회/주입, 브라우저 종료
"""
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP
from browser_engine import BrowserEngine, SNAPSHOT_JS

PROJECT_ROOT = Path(__file__).resolve().parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("wholesale-mcp")

mcp = FastMCP("wholesale-tools")

# ── 상태 관리 ──
_engine = BrowserEngine()
_executors: dict = {}  # site_id → SiteExecutor
_credentials: dict = {}  # site_id → {username, password}
_recipes: dict = {}    # site_id → recipe dict


# ── 헬퍼 ──

def _load_credentials() -> dict:
    """credentials.json에서 사이트별 로그인 정보 로드"""
    global _credentials
    if _credentials:
        return _credentials
    cred_path = PROJECT_ROOT / "credentials.json"
    if cred_path.exists():
        try:
            _credentials = json.loads(cred_path.read_text(encoding="utf-8"))
            logger.info(f"크레덴셜 로드: {list(_credentials.keys())}")
        except Exception as e:
            logger.error(f"크레덴셜 로드 실패: {e}")
    return _credentials


def _load_recipes() -> dict:
    """recipes/ 디렉토리에서 레시피 JSON 로드"""
    global _recipes
    if _recipes:
        return _recipes
    json_dir = PROJECT_ROOT / "recipes"
    if not json_dir.exists():
        logger.warning(f"레시피 디렉토리 없음: {json_dir}")
        return {}
    for f in json_dir.glob("*.json"):
        try:
            recipe = json.loads(f.read_text(encoding="utf-8"))
            site_id = recipe.get("site_id", f.stem)
            _recipes[site_id] = recipe
            logger.info(f"레시피 로드: {site_id}")
        except Exception as e:
            logger.error(f"레시피 로드 실패 {f.name}: {e}")
    return _recipes


# ═══════════════════════════════════════════
# 그룹 1: 브라우저 탐색 도구
# ═══════════════════════════════════════════

@mcp.tool()
async def open_site(url: str) -> str:
    """브라우저에서 사이트 열기. 네트워크 로그 초기화."""
    result = await _engine.goto(url, reset_log=True)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def snapshot_page() -> str:
    """현재 페이지의 모든 인터랙티브 요소 목록화 (버튼, 링크, 폼, 입력, iframe)"""
    result = await _engine.snapshot()
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def click_element(selector: str) -> str:
    """CSS 셀렉터로 요소 클릭. 클릭 후 페이지 변화 요약 반환."""
    result = await _engine.click(selector)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def fill_input(selector: str, value: str) -> str:
    """CSS 셀렉터로 입력 필드에 값 채우기"""
    result = await _engine.fill(selector, value)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def submit_form(form_selector: str = "form") -> str:
    """폼 제출 (기본: 첫 번째 폼). 제출 후 네트워크 요청 + 결과 반환."""
    result = await _engine.submit_form(form_selector)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_network_log(url_filter: str = "") -> str:
    """캡처된 HTTP 요청 목록. url_filter로 URL 필터링 가능."""
    result = await _engine.get_network_log(url_filter)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_page_html(selector: str = "") -> str:
    """현재 페이지 HTML. selector 지정 시 해당 영역만. 최대 30KB."""
    return await _engine.get_html(selector)


@mcp.tool()
async def screenshot() -> str:
    """현재 화면 스크린샷을 파일로 저장하고 경로 반환."""
    output_dir = PROJECT_ROOT / "screenshots"
    output_dir.mkdir(exist_ok=True)
    fname = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    path = str(output_dir / fname)
    await _engine.screenshot(path)
    return json.dumps({"path": path}, ensure_ascii=False, indent=2)


@mcp.tool()
async def execute_js(code: str) -> str:
    """JavaScript 코드 실행. 반환값은 JSON 직렬화됨."""
    result = await _engine.execute_js(code)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
async def get_cookies() -> str:
    """현재 브라우저 세션의 쿠키 목록"""
    cookies = await _engine.get_cookies()
    return json.dumps(cookies, ensure_ascii=False, indent=2)


@mcp.tool()
async def set_cookies(cookies_json: str) -> str:
    """쿠키 주입. JSON 배열 형식: [{"name":"...", "value":"...", "domain":"...", "path":"/"}]"""
    cookies = json.loads(cookies_json)
    await _engine.set_cookies(cookies)
    return json.dumps({"added": len(cookies)})


@mcp.tool()
async def close_browser() -> str:
    """브라우저 세션 종료"""
    await _engine.close()
    return json.dumps({"closed": True})


@mcp.tool()
async def snapshot_iframe(iframe_selector: str) -> str:
    """iframe 내부의 인터랙티브 요소 목록화"""
    result = await _engine.snapshot_iframe(iframe_selector)
    return json.dumps(result, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════
# 그룹 2: 레시피 기반 주문 도구
# ═══════════════════════════════════════════

@mcp.tool()
def register_site(url: str, username: str, password: str, site_name: str = "") -> str:
    """새 도매 사이트 등록. credentials.json에 ID/PW를 저장한다.

    레시피가 아직 없으면 AI가 MCP 도구로 사이트를 분석하여 레시피를 생성해야 한다.
    레시피가 이미 있으면 바로 recipe_login()으로 사용 가능.

    Args:
        url: 사이트 URL (예: https://wos.nicepharm.com)
        username: 로그인 아이디
        password: 로그인 비밀번호
        site_name: 사이트 이름 (생략 시 URL에서 추출)
    """
    import re
    from urllib.parse import urlparse

    # site_id 생성: URL → wos_nicepharm_com
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    site_id = re.sub(r'[^a-zA-Z0-9]', '_', hostname).strip('_')
    site_id = re.sub(r'_+', '_', site_id)
    if site_id.startswith('www_'):
        site_id = site_id[4:]

    if not site_name:
        site_name = hostname

    # credentials.json에 추가
    global _credentials
    creds = _load_credentials()
    creds[site_id] = {"username": username, "password": password}
    _credentials = creds

    cred_path = PROJECT_ROOT / "credentials.json"
    cred_path.write_text(json.dumps(creds, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"크레덴셜 저장: {site_id}")

    # 레시피 존재 여부 확인
    recipes = _load_recipes()
    has_recipe = site_id in recipes

    result = {
        "site_id": site_id,
        "site_name": site_name,
        "url": url,
        "credentials_saved": True,
        "has_recipe": has_recipe,
    }

    if has_recipe:
        result["next_step"] = f"레시피가 이미 있습니다. recipe_login('{site_id}')로 바로 사용하세요."
    else:
        result["next_step"] = (
            f"레시피가 없습니다. CLAUDE.md의 '레시피 자동 생성 워크플로우'에 따라 "
            f"open_site('{url}') → analyze_page_for_recipe() → 분석 → save_recipe()로 레시피를 생성하세요."
        )

    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def auto_login_all() -> str:
    """credentials.json에 등록된 모든 사이트에 자동 로그인.

    레시피가 있고 credentials이 있는 사이트만 로그인한다.
    서버 재시작 후 한 번 호출하면 모든 사이트를 즉시 사용할 수 있다.
    """
    from site_executor import SiteExecutor

    recipes = _load_recipes()
    creds = _load_credentials()
    results = []

    for site_id, cred in creds.items():
        recipe = recipes.get(site_id)
        if not recipe:
            results.append({"site_id": site_id, "success": False, "reason": "레시피 없음"})
            continue

        username = cred.get("username", "")
        password = cred.get("password", "")
        if not username or not password:
            results.append({"site_id": site_id, "success": False, "reason": "ID/PW 없음"})
            continue

        try:
            site_params = cred.get("site_params", {})
            executor = SiteExecutor(recipe)
            ok = executor.login(username, password, site_params=site_params)
            if ok:
                _executors[site_id] = executor
            results.append({
                "site_id": site_id,
                "site_name": recipe.get("site_name", ""),
                "success": ok,
            })
        except Exception as e:
            results.append({"site_id": site_id, "success": False, "reason": str(e)})

    total = len(results)
    success = sum(1 for r in results if r.get("success"))

    return json.dumps({
        "summary": f"{success}/{total} 사이트 로그인 성공",
        "sites": results,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def list_sites() -> str:
    """등록된 도매사이트 목록 (JSON 레시피 파일 기반)"""
    recipes = _load_recipes()
    creds = _load_credentials()
    sites = []
    for sid, r in recipes.items():
        sites.append({
            "site_id": sid,
            "name": r.get("site_name", ""),
            "url": r.get("site_url", ""),
            "has_login": "login" in r,
            "has_search": "search" in r,
            "has_cart": "cart_add" in r,
            "has_sales_ledger": "sales_ledger" in r,
            "has_credentials": sid in creds,
            "logged_in": sid in _executors and _executors[sid].is_authenticated(),
        })
    return json.dumps(sites, ensure_ascii=False, indent=2)


@mcp.tool()
def get_recipe(site_id: str) -> str:
    """특정 사이트의 레시피 JSON 조회"""
    recipes = _load_recipes()
    recipe = recipes.get(site_id)
    if not recipe:
        raise ValueError(f"레시피 없음: {site_id}")
    return json.dumps(recipe, ensure_ascii=False, indent=2)


@mcp.tool()
def recipe_login(site_id: str, username: str = "", password: str = "") -> str:
    """레시피 기반 HTTP 로그인.

    username/password를 생략하면 credentials.json에서 자동 로드합니다.
    """
    recipes = _load_recipes()
    recipe = recipes.get(site_id)
    if not recipe:
        raise ValueError(f"레시피 없음: {site_id}")

    # credentials.json에서 자동 로드
    creds = _load_credentials().get(site_id, {})
    if not username or not password:
        username = username or creds.get("username", "")
        password = password or creds.get("password", "")
    if not username or not password:
        raise ValueError(f"로그인 정보 없음: credentials.json에 {site_id} 추가 필요")

    # site_params: 사용자별 고유 값 (거래처 코드 등)
    site_params = creds.get("site_params", {})

    from site_executor import SiteExecutor
    executor = SiteExecutor(recipe)
    ok = executor.login(username, password, site_params=site_params)
    if ok:
        _executors[site_id] = executor
    return json.dumps({
        "success": ok,
        "site_id": site_id,
        "authenticated": executor.is_authenticated(),
        "cookies": len(executor.session.cookies)
    }, ensure_ascii=False)


@mcp.tool()
def recipe_search(site_id: str, keyword: str, edi_code: str = "", max_pages: int = 0) -> str:
    """레시피 기반 HTTP 검색. max_pages=0이면 레시피 기본값 사용."""
    executor = _executors.get(site_id)
    if not executor or not executor.is_authenticated():
        raise ValueError(f"로그인 먼저 필요: {site_id}")

    products = executor.search(keyword, edi_code or None, max_pages=max_pages)
    return json.dumps([{
        "product_code": p.product_code,
        "product_name": p.product_name,
        "edi_code": p.edi_code,
        "manufacturer": p.manufacturer,
        "unit_price": p.unit_price,
        "stock_quantity": p.stock_quantity,
        "pack_unit": p.pack_unit,
        "pack_units": p.pack_units,
    } for p in products], ensure_ascii=False, indent=2)


@mcp.tool()
def recipe_add_to_cart(site_id: str, product_code: str, quantity: int) -> str:
    """레시피 기반 HTTP 장바구니 추가"""
    executor = _executors.get(site_id)
    if not executor or not executor.is_authenticated():
        raise ValueError(f"로그인 먼저 필요: {site_id}")

    ok = executor.add_to_cart(product_code, quantity)
    return json.dumps({
        "success": ok,
        "site_id": site_id,
        "product_code": product_code,
        "quantity": quantity
    }, ensure_ascii=False)


@mcp.tool()
def recipe_view_cart(site_id: str) -> str:
    """장바구니 내역 조회. 현재 담겨있는 상품 목록을 반환합니다."""
    executor = _executors.get(site_id)
    if not executor or not executor.is_authenticated():
        raise ValueError(f"로그인 먼저 필요: {site_id}")

    items = executor.view_cart()
    return json.dumps([{
        "product_code": item.product_code,
        "product_name": item.product_name,
        "quantity": item.quantity,
        "unit_price": item.unit_price,
        "total_price": item.total_price,
    } for item in items], ensure_ascii=False, indent=2)


@mcp.tool()
def recipe_delete_from_cart(site_id: str, product_code: str) -> str:
    """장바구니에서 특정 상품 삭제"""
    executor = _executors.get(site_id)
    if not executor or not executor.is_authenticated():
        raise ValueError(f"로그인 먼저 필요: {site_id}")

    ok = executor.delete_from_cart(product_code)
    return json.dumps({
        "success": ok,
        "site_id": site_id,
        "product_code": product_code,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def recipe_clear_cart(site_id: str) -> str:
    """장바구니 전체 비우기"""
    executor = _executors.get(site_id)
    if not executor or not executor.is_authenticated():
        raise ValueError(f"로그인 먼저 필요: {site_id}")

    ok = executor.clear_cart()
    return json.dumps({
        "success": ok,
        "site_id": site_id,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def get_session_info(site_id: str) -> str:
    """SiteExecutor 세션 상태 조회 (쿠키, 인증 여부, 헤더)"""
    executor = _executors.get(site_id)
    if not executor:
        raise ValueError(f"세션 없음: {site_id}")

    cookies = {c.name: c.value for c in executor.session.cookies}
    return json.dumps({
        "site_id": site_id,
        "authenticated": executor.is_authenticated(),
        "cookies": cookies,
        "headers": dict(executor.session.headers),
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def recipe_sales_ledger(site_id: str, start_date: str = "", end_date: str = "",
                        period: str = "3m", detail: bool = True,
                        product_filter: str = "") -> str:
    """매출원장 조회. 최근 주문 약품 상세 리스트 확인.

    날짜 설정:
    - start_date + end_date 직접 지정 (YYYY-MM-DD)
    - 또는 period로 상대 기간: "1w", "1m", "3m"(기본), "6m", "1y"
    - detail=True: 약품별 상세, False: 일자별 요약
    - product_filter: 약품명 검색 필터
    """
    executor = _executors.get(site_id)
    if not executor or not executor.is_authenticated():
        raise ValueError(f"로그인 먼저 필요: {site_id}")

    detail_mode = "0" if detail else "1"
    entries = executor.get_sales_ledger(
        start_date=start_date, end_date=end_date,
        period=period, detail_mode=detail_mode,
        product_filter=product_filter
    )

    return json.dumps({
        "site_id": site_id,
        "period": f"{start_date or '(auto)'} ~ {end_date or '(today)'}",
        "detail_mode": "약품별 상세" if detail else "일자별 요약",
        "total_entries": len(entries),
        "entries": [{
            "date": e.transaction_date,
            "product_name": e.product_name,
            "pack_unit": e.pack_unit,
            "quantity": e.quantity,
            "unit_price": e.unit_price,
            "sales_amount": e.sales_amount,
            "balance": e.balance,
        } for e in entries]
    }, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════
# 그룹 3: 레시피 자동 생성 도구
# ═══════════════════════════════════════════

@mcp.tool()
async def analyze_page_for_recipe(page_type: str = "auto") -> str:
    """
    현재 페이지를 3종 동시 분석하여 레시피 생성에 필요한 정보 추출.

    page_type: "login", "search", "cart", "sales_ledger", "auto" (자동 감지)

    반환 정보:
    - forms: 폼 구조 + 필드 목록 + hidden 필드
    - tables: 테이블 구조 (행 수, 컬럼, 클래스, 샘플 데이터)
    - buttons: 버튼 목록
    - all_links: visible + hidden 전체 링크 (메뉴 구조 파악)
    - js_handlers: onclick 함수명, AJAX URL ($.post, fetch 등)
    - html_forms_raw: form 태그 HTML 원문 (AI가 직접 구조 판단)
    - recent_post_requests: 네트워크 캡처된 POST 요청
    - cookies: 현재 쿠키 목록
    """
    page = await _engine.ensure_browser()

    # 3종 동시 분석: DOM 구조 + JS 핸들러 + 링크 전체
    analysis = await page.evaluate("""() => {
        const result = {
            url: location.href,
            title: document.title,
            forms: [],
            tables: [],
            buttons: [],
            all_links: [],
            js_handlers: {
                onclick_functions: [],
                form_submits: [],
                ajax_urls: []
            },
            html_forms_raw: []
        };

        // ── 1. 폼 분석 (구조화) ──
        document.querySelectorAll('form').forEach((form, idx) => {
            const formData = {
                index: idx,
                name: form.name || '',
                id: form.id || '',
                action: form.action || '',
                method: (form.method || 'GET').toUpperCase(),
                fields: []
            };

            form.querySelectorAll('input, select, textarea').forEach(inp => {
                const field = {
                    type: inp.type || inp.tagName.toLowerCase(),
                    name: inp.name || '',
                    id: inp.id || '',
                    placeholder: inp.placeholder || '',
                    value: inp.type === 'password' ? '' : (inp.value || ''),
                    required: inp.required || false,
                    is_password: inp.type === 'password',
                    is_username: !!(inp.name && /^(.*id|user|login|email)/i.test(inp.name) && inp.type !== 'checkbox' && inp.type !== 'hidden'),
                    is_search: !!(inp.placeholder && /(검색|search|keyword|약품|상품)/i.test(inp.placeholder)) ||
                               !!(inp.name && /(search|keyword|query|physic|product)/i.test(inp.name)),
                    is_date: !!(inp.name && /(date|from|to|dtp|sdate|edate)/i.test(inp.name)) || inp.type === 'date'
                };
                if (inp.type !== 'hidden' || inp.value) {
                    formData.fields.push(field);
                }
            });

            result.forms.push(formData);

            // form HTML 원문 (AI가 직접 판단할 수 있도록)
            result.html_forms_raw.push(form.outerHTML.substring(0, 2000));
        });

        // ── 2. 테이블 분석 ──
        document.querySelectorAll('table').forEach((table, idx) => {
            const rows = table.querySelectorAll('tr');
            const classedRows = table.querySelectorAll('tr[class]');
            if (rows.length < 2) return;

            const ths = rows[0].querySelectorAll('th');
            const firstDataRow = rows.length > 1 ? rows[1] : rows[0];
            const tds = firstDataRow ? firstDataRow.querySelectorAll('td') : [];

            result.tables.push({
                index: idx,
                rows: rows.length,
                classed_rows: classedRows.length,
                first_row_class: classedRows.length > 0 ? classedRows[0].className : '',
                column_count: tds.length,
                headers: Array.from(ths).map(th => th.textContent.trim()).slice(0, 15),
                has_inputs: firstDataRow ? firstDataRow.querySelectorAll('input').length > 0 : false,
                td_classes: Array.from(tds).map(td => td.className).slice(0, 15),
                sample_text: firstDataRow ? firstDataRow.textContent.trim().substring(0, 300) : ''
            });
        });

        // ── 3. 버튼 분석 ──
        document.querySelectorAll('button, input[type="submit"], input[type="button"]').forEach(btn => {
            const text = (btn.textContent || btn.value || '').trim();
            if (text) {
                result.buttons.push({
                    text: text.substring(0, 50),
                    type: btn.type || 'button',
                    onclick: btn.getAttribute('onclick') || ''
                });
            }
        });

        // ── 4. 전체 링크 (visible + hidden) ──
        document.querySelectorAll('a[href]').forEach(a => {
            const href = a.getAttribute('href') || '';
            if (!href || href === '#' || href.startsWith('javascript:void')) return;
            const text = a.textContent.trim();
            if (!text) return;
            result.all_links.push({
                text: text.substring(0, 50),
                href: href.substring(0, 200),
                visible: !!a.offsetParent
            });
        });

        // ── 5. JS 이벤트 핸들러 + AJAX URL 탐색 ──
        // onclick 속성에서 함수명 추출
        const onclickSet = new Set();
        document.querySelectorAll('[onclick]').forEach(el => {
            const oc = el.getAttribute('onclick');
            const m = oc.match(/(\\w+)\\s*\\(/);
            if (m && m[1] !== 'return') onclickSet.add(m[1]);
        });
        result.js_handlers.onclick_functions = Array.from(onclickSet);

        // script 태그에서 AJAX URL 추출
        const ajaxSet = new Set();
        document.querySelectorAll('script:not([src])').forEach(s => {
            const text = s.textContent;
            const patterns = [
                /\\$\\.(?:post|ajax|get)\\s*\\(\\s*['\"]([^'\"]+)['\"]/g,
                /fetch\\s*\\(\\s*['\"]([^'\"]+)['\"]/g,
                /url\\s*[=:]\\s*['\"](\\/[^'\"]+)['\"]/g,
                /GetAjax\\s*\\(\\s*['\"]([^'\"]+)['\"]/g,
                /['\"](\\/(?:Home|Service|Api|Member|MyPage)\\/\\w[^'\"]*)['"]/g
            ];
            for (const pat of patterns) {
                let m;
                while ((m = pat.exec(text)) !== null) {
                    const u = m[1];
                    if (u && !u.endsWith('.js') && !u.endsWith('.css') && !u.endsWith('.png')) {
                        ajaxSet.add(u);
                    }
                }
            }
        });
        result.js_handlers.ajax_urls = Array.from(ajaxSet);

        return result;
    }""")

    # 외부 JS 파일에서 API 엔드포인트 추출 (Order.js 등)
    try:
        external_ajax = await page.evaluate("""async () => {
            const scripts = Array.from(document.querySelectorAll('script[src]'))
                .map(s => s.src)
                .filter(s => !s.includes('jquery') && !s.includes('swiper') && !s.includes('kakao'));

            const results = {external_js_files: [], ajax_urls: [], cart_functions: []};

            for (const src of scripts.slice(0, 5)) {
                try {
                    const resp = await fetch(src);
                    const text = await resp.text();
                    const fileName = src.split('/').pop().split('?')[0];
                    results.external_js_files.push(fileName);

                    // AJAX URL 추출 (다양한 패턴 대응)
                    const patterns = [
                        /\\$\\.(?:post|ajax|get)\\s*\\(\\s*['\"]([^'\"]+)['\"]/g,
                        /fetch\\s*\\(\\s*['\"]([^'\"]+)['\"]/g,
                        /url\\s*[=:]\\s*['\"](\\/[^'\"]+)['\"]/g,
                        /GetAjax\\s*\\(\\s*['\"]([^'\"]+)['\"]/g,
                        /['\"](\\/(?:Home|Service|Api|Member|MyPage)\\/\\w[^'\"]*)['"]/g
                    ];
                    for (const pat of patterns) {
                        let m;
                        while ((m = pat.exec(text)) !== null) {
                            const u = m[1];
                            if (u && !u.endsWith('.js') && !u.endsWith('.css') && !u.endsWith('.png')) {
                                results.ajax_urls.push(u);
                            }
                        }
                    }

                    // 장바구니/주문 함수명
                    const funcPat = /function\\s+(\\w*(?:cart|bag|order|save|add|submit)\\w*)\\s*\\(/gi;
                    let fm;
                    while ((fm = funcPat.exec(text)) !== null) {
                        results.cart_functions.push(fm[1]);
                    }
                } catch(e) {}
            }

            results.ajax_urls = [...new Set(results.ajax_urls)];
            results.cart_functions = [...new Set(results.cart_functions)];
            return results;
        }""")

        # 외부 JS 결과를 js_handlers에 병합
        if external_ajax:
            existing = analysis.get("js_handlers", {})
            existing["external_js_files"] = external_ajax.get("external_js_files", [])
            existing["ajax_urls"] = list(set(
                existing.get("ajax_urls", []) + external_ajax.get("ajax_urls", [])
            ))
            existing["cart_functions"] = external_ajax.get("cart_functions", [])
            analysis["js_handlers"] = existing
    except Exception:
        pass

    # 네트워크 로그에서 POST 요청 추출
    recent_posts = [req for req in _engine.network_log[-20:] if req.get("method") == "POST"]

    # 쿠키 정보
    cookies = await _engine.get_cookies()
    cookie_names = [c["name"] for c in cookies]

    # 페이지 타입 자동 감지
    detected_type = page_type
    if page_type == "auto":
        has_password = any(
            any(f.get("is_password") for f in form.get("fields", []))
            for form in analysis["forms"]
        )
        has_search = any(
            any(f.get("is_search") for f in form.get("fields", []))
            for form in analysis["forms"]
        )
        has_date_range = sum(
            sum(1 for f in form.get("fields", []) if f.get("is_date"))
            for form in analysis["forms"]
        ) >= 2
        url_lower = analysis["url"].lower()

        if has_password:
            detected_type = "login"
        elif has_search:
            detected_type = "search"
        elif "cart" in url_lower or "bag" in url_lower:
            detected_type = "cart"
        elif has_date_range or "ledger" in url_lower or "sales" in url_lower:
            detected_type = "sales_ledger"
        else:
            detected_type = "unknown"

    result = {
        "detected_type": detected_type,
        "url": analysis["url"],
        "title": analysis["title"],
        "forms": analysis["forms"],
        "tables": analysis["tables"],
        "buttons": analysis["buttons"],
        "all_links": analysis["all_links"],
        "js_handlers": analysis["js_handlers"],
        "html_forms_raw": analysis["html_forms_raw"],
        "recent_post_requests": recent_posts,
        "cookies": cookie_names,
        "analysis_hints": {
            "has_login_form": any(
                any(f.get("is_password") for f in form.get("fields", []))
                for form in analysis["forms"]
            ),
            "has_search_form": any(
                any(f.get("is_search") for f in form.get("fields", []))
                for form in analysis["forms"]
            ),
            "has_data_tables": len([t for t in analysis["tables"] if t.get("rows", 0) > 2]) > 0,
            "has_date_inputs": any(
                any(f.get("is_date") for f in form.get("fields", []))
                for form in analysis["forms"]
            )
        }
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def generate_recipe_spec(site_url: str, site_name: str,
                                login_analysis: str = "", search_analysis: str = "",
                                cart_analysis: str = "") -> str:
    """
    분석 결과 + 네트워크 로그를 기반으로 레시피 JSON 생성.

    네트워크 로그에서 실제 캡처된 POST 요청 URL을 사용하므로
    form action 추측보다 정확함.

    Parameters:
    - site_url: 사이트 URL
    - site_name: 사이트 이름
    - login_analysis: analyze_page_for_recipe의 로그인 분석 결과 (JSON)
    - search_analysis: analyze_page_for_recipe의 검색 분석 결과 (JSON)
    - cart_analysis: analyze_page_for_recipe의 장바구니 분석 결과 (JSON)
    """
    from urllib.parse import urlparse

    parsed_url = urlparse(site_url)
    site_id = parsed_url.netloc.replace(".", "_").replace("-", "_")

    recipe = {
        "recipe_version": 4,
        "site_id": site_id,
        "site_name": site_name,
        "site_url": site_url.rstrip("/"),
        "encoding": "utf-8",
        "analyzed_by": "mcp-auto-generator",
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "connection": {
            "pre_login_get": True,
            "request_interval_ms": 500,
            "ssl_verify": True
        }
    }

    # 로그인 스펙 생성
    if login_analysis:
        try:
            login_data = json.loads(login_analysis)
            forms = login_data.get("forms", [])

            # 로그인 폼 찾기 (password 필드 있는 폼)
            login_form = None
            for form in forms:
                if any(f.get("is_password") for f in form.get("fields", [])):
                    login_form = form
                    break

            if login_form:
                username_field = next(
                    (f["name"] for f in login_form["fields"] if f.get("is_username")),
                    "username"
                )
                password_field = next(
                    (f["name"] for f in login_form["fields"] if f.get("is_password")),
                    "password"
                )

                payload = {username_field: "{USERNAME}", password_field: "{PASSWORD}"}
                for field in login_form["fields"]:
                    if field["type"] == "hidden" and field.get("value"):
                        payload[field["name"]] = field["value"]

                # 네트워크 로그에서 실제 로그인 POST URL 찾기
                login_url = login_form["action"]
                login_posts = login_data.get("recent_post_requests", [])
                for req in login_posts:
                    url_lower = req.get("url", "").lower()
                    if any(k in url_lower for k in ["login", "signin", "auth", "certify"]):
                        login_url = req["url"]
                        break

                if not login_url.startswith("http"):
                    login_url = site_url.rstrip("/") + "/" + login_url.lstrip("/")

                # 쿠키 기반 성공 지표 — 실제 쿠키 이름 사용
                auth_cookie_key = "session"
                login_cookies = login_data.get("cookies", [])
                for name in login_cookies:
                    name_lower = name.lower()
                    if any(k in name_lower for k in ["auth", "session", "token", "sid"]):
                        auth_cookie_key = name
                        break
                if not auth_cookie_key or auth_cookie_key == "session":
                    # 쿠키 이름 중 가장 길거나 유의미한 것 선택
                    if login_cookies:
                        auth_cookie_key = login_cookies[0]

                recipe["login"] = {
                    "method": login_form["method"],
                    "url": login_url,
                    "payload": payload,
                    "content_type": "application/x-www-form-urlencoded",
                    "success_indicator": {
                        "type": "cookie",
                        "key": auth_cookie_key
                    }
                }
        except Exception as e:
            logger.error(f"로그인 스펙 생성 실패: {e}")

    # 검색 스펙 생성
    if search_analysis:
        try:
            search_data = json.loads(search_analysis)
            forms = search_data.get("forms", [])

            # 검색 폼 찾기
            search_form = None
            for form in forms:
                if any(f.get("is_search") for f in form.get("fields", [])):
                    search_form = form
                    break
            if not search_form and forms:
                search_form = forms[0]

            # 네트워크 로그에서 실제 검색 POST URL 찾기
            search_url = ""
            search_posts = search_data.get("recent_post_requests", [])
            for req in search_posts:
                url_lower = req.get("url", "").lower()
                if any(k in url_lower for k in ["search", "order", "product", "list", "query"]):
                    search_url = req["url"]
                    break
            if not search_url and search_posts:
                search_url = search_posts[0]["url"]
            if not search_url and search_form:
                search_url = search_form["action"]
            if not search_url.startswith("http"):
                search_url = site_url.rstrip("/") + "/" + search_url.lstrip("/")

            # 검색 파라미터 구성
            params = {}
            if search_form:
                search_field = next(
                    (f["name"] for f in search_form["fields"] if f.get("is_search")),
                    next((f["name"] for f in search_form["fields"]
                          if f["type"] not in ("hidden", "submit", "button")), "keyword")
                )
                params[search_field] = "{KEYWORD}"
                for field in search_form["fields"]:
                    if field["type"] == "hidden" and field.get("value"):
                        params[field["name"]] = field["value"]

            # 검색 결과 파싱 셀렉터 — 테이블 구조에서 자동 추출
            row_selector = "tr"
            tables = search_data.get("tables", [])
            for table in tables:
                if table.get("classed_rows", 0) > 0:
                    row_selector = f"tr.{table['first_row_class'].split()[0]}"
                    break
                elif table.get("rows", 0) > 3:
                    row_selector = "tr"
                    break

            recipe["search"] = {
                "method": search_form["method"] if search_form else "POST",
                "url": search_url,
                "params": params,
                "response_type": "html",
                "content_type": "application/x-www-form-urlencoded",
                "parsing": {
                    "selector": row_selector,
                    "fields": {
                        "product_name": {"selector": "td:nth-child(3) a", "attribute": "text"},
                        "product_code": {"selector": "input[name^=pc_]", "attribute": "value"},
                        "unit_price": {"selector": "td:nth-child(6)", "attribute": "text"}
                    }
                }
            }
        except Exception as e:
            logger.error(f"검색 스펙 생성 실패: {e}")

    # 장바구니 스펙 생성
    if cart_analysis:
        try:
            cart_data = json.loads(cart_analysis)
            forms = cart_data.get("forms", [])

            # 네트워크 로그에서 장바구니 POST URL 찾기
            cart_url = ""
            cart_posts = cart_data.get("recent_post_requests", [])
            for req in cart_posts:
                url_lower = req.get("url", "").lower()
                if any(k in url_lower for k in ["cart", "bag", "basket", "order"]):
                    cart_url = req["url"]
                    break

            if forms:
                cart_form = forms[0]
                if not cart_url:
                    cart_url = cart_form["action"]
                if not cart_url.startswith("http"):
                    cart_url = site_url.rstrip("/") + "/" + cart_url.lstrip("/")

                recipe["cart_add"] = {
                    "type": "form",
                    "method": cart_form["method"],
                    "url": cart_url,
                    "form_name": cart_form["name"] or "cartForm",
                    "product_code_prefix": "pc_",
                    "quantity_prefix": "qty_",
                    "success_indicator": {
                        "type": "status_code",
                        "value": 200
                    }
                }
        except Exception as e:
            logger.error(f"장바구니 스펙 생성 실패: {e}")

    recipe["verified"] = False
    recipe["requires_manual_review"] = True

    return json.dumps(recipe, ensure_ascii=False, indent=2)


@mcp.tool()
def save_recipe(site_id: str, recipe_json: str, overwrite: bool = False) -> str:
    """
    생성된 레시피를 recipes/ 디렉토리에 저장.

    Parameters:
    - site_id: 사이트 ID (파일명으로 사용)
    - recipe_json: 저장할 레시피 JSON 문자열
    - overwrite: True면 기존 파일 덮어쓰기
    """
    try:
        recipe = json.loads(recipe_json)

        recipes_dir = PROJECT_ROOT / "recipes"
        recipes_dir.mkdir(exist_ok=True)

        file_path = recipes_dir / f"{site_id}.json"

        if file_path.exists() and not overwrite:
            return json.dumps({
                "success": False,
                "error": f"레시피 파일이 이미 존재합니다: {file_path}",
                "suggestion": "overwrite=True로 덮어쓰거나 다른 site_id 사용"
            }, ensure_ascii=False)

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(recipe, f, ensure_ascii=False, indent=2)

        global _recipes
        _recipes = {}

        return json.dumps({
            "success": True,
            "file_path": str(file_path),
            "site_id": site_id,
            "message": "레시피가 성공적으로 저장되었습니다"
        }, ensure_ascii=False, indent=2)

    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 파싱 실패: {e}")
    except Exception as e:
        raise ValueError(f"저장 실패: {e}")


@mcp.tool()
async def capture_form_submission(form_selector: str = "form") -> str:
    """
    폼 제출을 캡처하여 실제 요청 정보 수집.
    AJAX/fetch 요청도 캡처하여 SPA 사이트 대응.

    사용법:
    1. fill_input으로 값 채우기
    2. capture_form_submission() 실행
    3. 반환된 실제 URL/payload로 레시피 작성
    """
    page = await _engine.ensure_browser()
    before_count = len(_engine.network_log)

    try:
        submit_btn = await page.query_selector(
            f"{form_selector} input[type='submit'], "
            f"{form_selector} button[type='submit'], "
            f"{form_selector} input[type='image']"
        )
        if submit_btn:
            await submit_btn.click()
        else:
            await page.evaluate(f"document.querySelector('{form_selector}').submit()")

        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass

    # AJAX 안정화 대기
    await _engine.wait_for_stable(3000)

    new_requests = _engine.network_log[before_count:]
    post_requests = [req for req in new_requests if req.get("method") == "POST"]

    if not post_requests:
        return json.dumps({
            "success": False,
            "error": "POST 요청이 캡처되지 않았습니다",
            "all_requests": new_requests[:10]
        }, ensure_ascii=False, indent=2)

    main_request = post_requests[0]

    result = {
        "success": True,
        "request": {
            "method": "POST",
            "url": main_request["url"],
            "post_data": main_request.get("post_data", ""),
            "content_type": main_request.get("content_type", ""),
            "status": main_request.get("status"),
            "body_preview": main_request.get("body_preview", "")
        },
        "all_post_requests": post_requests[:5],
        "current_url": page.url
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════
# MCP Prompts — 어떤 AI든 호출 가능한 워크플로우
# ═══════════════════════════════════════════

@mcp.prompt()
def generate_recipe(site_url: str, site_name: str) -> str:
    """새 도매 사이트의 레시피를 자동 생성하는 워크플로우.

    이 프롬프트를 따라 MCP 도구를 순서대로 호출하면 레시피가 완성됩니다.
    """
    return f"""# 레시피 자동 생성 워크플로우: {site_name} ({site_url})

## 사이트 유형 즉시 판별법

analyze_page_for_recipe() 결과를 보고 아래 기준으로 유형을 판별하세요:

1. forms 비어있음 + external_js에 angular 없음 + 쿠키 없음 → 유형 C: SPA + JWT
2. forms 비어있음 + external_js에 angular 있음 + 쿠키 있음 → 유형 D: AngularJS + 쿠키
3. forms 있음 + ajax_urls 비어있음 → 유형 A: 전통 form POST
4. forms 있음 + ajax_urls에 Cart/Search URL 있음 → 유형 B: AJAX 하이브리드

## 공통 원칙: 버튼 발견 → 코드 추적 → API 확정

모든 STEP에서 동일한 방법을 사용한다:
1. snapshot_page() 또는 snapshot_iframe()으로 UI 요소 발견
2. 요소의 텍스트로 기능 인식 ("로그인", "검색", "담기", "삭제" 등)
3. 해당 요소의 코드 추적:
   A) href/action에 URL 직접 있으면 → 즉시 확정
   B) onclick/ng-click 함수명 → execute_js로 함수 소스 추적 → URL 추출
   C) jQuery 이벤트 → 외부 JS fetch → 핸들러 소스 분석
   D) 이벤트 안 보이면 → click_element() + get_network_log() 캡처

## STEP 1: 로그인

1. open_site("{site_url}") → snapshot_page()
2. "로그인" 버튼 발견 → 근처의 입력 필드 확인 (password, text)
3. fill_input으로 ID/PW 입력
4. "로그인" 버튼의 코드 추적 → form action 또는 AJAX URL 확인
5. capture_form_submission() → 실제 전송된 URL + payload 최종 확정
6. get_cookies() → 새로 생긴 쿠키 = success_indicator
   JWT: get_network_log()에서 토큰 응답 확인

## STEP 2: 메인 페이지 전체 분석

1. analyze_page_for_recipe("search") → 3종 동시 수집
   - forms, all_links(hidden 포함), js_handlers, html_forms_raw, iframes
2. snapshot_page()로 버튼/링크 텍스트 확인 → 각 기능 파악
3. iframe 있으면 snapshot_iframe()으로 내부 요소도 확인
4. hidden 메뉴 적극 탐색:
   - all_links에서 visible=false인 링크 → 숨겨진 하위 메뉴
   - 햄버거/드롭다운/사이드바 버튼 클릭 → 하위 메뉴 노출 후 확인
   - execute_js로 display:none 요소의 HTML도 직접 읽기 가능

## STEP 3: 검색

1. "검색"/"조회" 버튼 발견 → 코드 추적:
   - form 안이면 → form action이 검색 URL
   - onclick 함수면 → 함수 소스에서 AJAX URL 추출
2. fill_input으로 "타이레놀" 입력 → 버튼 클릭
3. get_network_log() → 실제 검색 API URL + 파라미터 캡처
4. execute_js로 결과 구조 확인 (행 셀렉터, td 클래스, hidden div)

## STEP 3.5: 페이지네이션

1. "다음", "2", "3" 페이지 링크 발견 → href에서 페이지 파라미터 확인
   → type: "html_links", paging_selector, page_url_param
2. 링크 없으면 → get_network_log()에서 JSON 응답의 totalPage 확인
   → type: "param", page_param
3. 둘 다 없으면 → pagination 없음

## STEP 4: 장바구니 추가

1. "담기"/"장바구니" 버튼 발견 → 코드 추적:
   A) form 안이면 → form 방식 (action URL, pc_ prefix, qty_ prefix)
   B) onclick 함수(AddCart 등) → 함수 소스 추적 → AJAX URL + payload 추출
   C) SPA: 버튼 클릭 → get_network_log()로 POST 캡처
2. 재고 있는 상품으로 담기 테스트 → 실제 요청 확인

## STEP 4.5: 장바구니 관리 (조회/삭제/비우기)

1. 장바구니 UI 찾기:
   - iframe 있으면 → snapshot_iframe()으로 내부 확인
   - 없으면 메인 페이지에서 직접 확인
2. "장바구니 비우기"/"전체삭제" → cart_clear, "삭제"/휴지통 → cart_delete
3. 발견된 요소의 코드 추적 (공통 원칙 A~D 적용)
4. cart_view: 상품 담기 후 get_network_log()에서 조회 API 캡처
5. 없는 기능은 생략 (코드가 폴백 자동 수행)

## STEP 5: 매출원장

1. STEP 2의 all_links에서 "매출원장" 링크 발견 → click_element()로 이동
2. snapshot_page()로 매출원장 UI 확인:
   - 날짜 입력 필드, "조회" 버튼
3. "조회" 버튼의 코드 추적 → form action 또는 AJAX URL 확인
4. 날짜 입력 → 조회 실행 → get_network_log()로 실제 API 캡처
5. execute_js로 결과 테이블 구조 확인 (헤더: 일자, 제품명, 수량, 단가, 매출)

## STEP 6: 레시피 JSON 작성 + E2E 검증

1. save_recipe(site_id, recipe_json, overwrite=True)
   필수 섹션: login, search, cart_add, cart_view
   권장 섹션: cart_delete, cart_clear, sales_ledger, search.pagination

2. E2E 검증 7가지:
   - recipe_login → 성공?
   - recipe_search → 결과 있음?
   - recipe_add_to_cart → 성공?
   - recipe_view_cart → 방금 담은 상품 보임?
   - recipe_delete_from_cart 또는 recipe_clear_cart → 삭제/비우기 성공?
   - recipe_sales_ledger → 데이터 있음?
   - 전체 통과 시 공유 여부 물어보기

3. 실패 시 해당 단계로 돌아가서 수정 (최대 10회)
4. E2E 성공 시 사용자에게 "커뮤니티에 레시피를 공유할까요?" 물어보기
5. 사용자가 동의하면 share_recipe(site_id) 호출

## 크레덴셜 분리 원칙

레시피에는 개인정보(ID/PW, 거래처코드)를 넣지 않는다.
사용자별 값은 credentials.json의 site_params에 저장하고,
레시피에서는 {{VEN_CD}}, {{CUST_CD}} 등 변수로 참조한다.
"""


@mcp.prompt()
def recipe_json_schema() -> str:
    """레시피 JSON 형식 가이드. 레시피 작성 시 참고하세요."""
    return """# 레시피 JSON 스키마

## 필수 섹션: login, search, cart_add, cart_view
## 권장 섹션: cart_delete, cart_clear, sales_ledger, search.pagination
## 선택 섹션: order_submit

## login
```json
{
  "method": "POST",
  "url": "https://example.com/login",
  "payload": {"id": "{USERNAME}", "pw": "{PASSWORD}"},
  "content_type": "application/x-www-form-urlencoded",
  "success_indicator": {"type": "cookie", "key": "session_id"}
}
```

JWT 사이트는 추가로 token 섹션 필요:
```json
"token": {
  "path": "accessToken",
  "header": "Authorization",
  "prefix": "Bearer ",
  "user_data_path": "userData"
}
```

## search
```json
{
  "method": "POST",
  "url": "https://example.com/search",
  "params": {"keyword": "{KEYWORD}"},
  "response_type": "html",
  "parsing": {
    "selector": "tr.product-row",
    "fields": {
      "product_name": {"selector": "td.name", "attribute": "text"},
      "product_code": {"selector": "input[name^=pc_]", "attribute": "value"},
      "unit_price": {"selector": "td.price", "attribute": "text"}
    }
  }
}
```

JSON API의 경우 response_type: "json" + json_mapping 사용.

## search.pagination (선택)

JSON API 방식:
```json
"pagination": {
  "type": "param",
  "page_param": "page",
  "start_page": 1,
  "total_pages_path": "totalPage",
  "max_pages": 10
}
```

HTML 링크 방식:
```json
"pagination": {
  "type": "html_links",
  "paging_selector": "div.paging a",
  "page_url_param": "Page",
  "method_override": "GET",
  "max_pages": 5
}
```

## cart_add

API 방식:
```json
{
  "method": "POST",
  "url": "/cart/add",
  "payload": {"productCode": "{PRODUCT_CODE}", "orderQty": "{QUANTITY}"},
  "success_indicator": {"type": "json_field", "path": "result", "value": "1"}
}
```

form 방식:
```json
{
  "type": "form",
  "url": "/BagOrder.asp",
  "form_name": "frmOrder",
  "product_code_prefix": "pc_",
  "quantity_prefix": "qty_"
}
```

## cart_view (장바구니 조회)
```json
{
  "method": "GET",
  "url": "/Service/Order/Bag.asp",
  "params": {"currVenCd": "{VEN_CD}"},
  "response_type": "html",
  "parsing": {
    "selector": "tr[id^=bagLine]",
    "fields": {
      "product_name": {"selector": "td.td_nm a", "attribute": "text"},
      "product_code": {"selector": "input[name^=pc_]", "attribute": "value"},
      "quantity": {"selector": "input[name^=bagQty_]", "attribute": "value"},
      "unit_price": {"selector": "input[name^=price_]", "attribute": "value"},
      "bag_num": {"selector": "input[name^=bag_num_]", "attribute": "value"}
    }
  }
}
```
JSON API 사이트: response_type: "json" + json_mapping 사용.

## cart_delete (개별 삭제)
```json
{
  "method": "GET",
  "url": "/ajax/bag.asp",
  "params": {"mode": "del", "code": "{PRODUCT_CODE}"},
  "success_indicator": {"type": "json_field", "path": "retCode", "value": "0000"}
}
```
DELETE 메서드도 지원. SPA는 DELETE /ord/deleteComOrdBasket?saveItemCd={PRODUCT_CODE}
form 사이트에서 requires_cart_view: true면 cart_view에서 bag_num 등을 가져와서 orderNum 구성.

## cart_clear (전체 비우기)
```json
{
  "method": "GET",
  "url": "/Service/Order/BagOrder.asp",
  "params": {"kind": "del", "currVenCd": "{VEN_CD}"}
}
```
cart_clear가 없으면 자동으로 cart_view → cart_delete 반복으로 폴백.

## success_indicator 타입
- cookie: 특정 쿠키 존재
- status_code: HTTP 상태 코드
- json_field: JSON 필드 값
- contains: 응답 텍스트 포함
- redirect: 리다이렉트 URL 포함

## parsing.fields 문법
- CSS 셀렉터: "td:nth-child(3) a"
- 속성 추출: {"selector": "input", "attribute": "value"}
- 정규식: "td.name|regex(\\d+T)"
- join: {"selector": "img", "attribute": "alt", "join": true}
"""


@mcp.prompt()
def site_type_guide() -> str:
    """사이트 유형 판별 가이드. analyze_page_for_recipe() 결과로 판단합니다."""
    return """# 사이트 유형 판별 가이드

## 유형 A: 전통 form POST (복산나이스팜, 우정약품)
- forms 있음, ajax_urls 비어있음
- 쿠키 인증, form action이 곧 API URL
- cart_add: type="form" (frmOrder 폼 제출)
- cart_view: GET Bag.asp (iframe HTML), 셀렉터 tr[id^=bagLine]
- cart_delete: Bag.js에서 btn_delete 핸들러 확인 → BagOrder.asp?kind=multiupdbag&actflag=DEL&orderNum=...
  (orderNum = bag_num|product_code|stock_cd|qty, cart_view에서 bag_num 필요)
- cart_clear: GET BagOrder.asp?kind=del&currVenCd={VEN_CD}

## 유형 B: AJAX 하이브리드 (지오웹 BPM)
- forms 있음, ajax_urls에 /DataCart/, /PartialSearchProduct 등
- 쿠키 인증, AJAX POST로 검색/장바구니
- cart_add: POST /Home/DataCart/add
- cart_view: POST /Home/PartialProductCart (HTML), 셀렉터 tr.tr_cart_list
- cart_delete: POST /Home/DataCart/del (productCode, moveCode, orderQty=0)
- cart_clear: POST /Home/DataCart/alldel

## 유형 C: SPA + JWT (백제약품)
- forms 비어있음, 로그인 후 쿠키 없음
- JWT Bearer 토큰 인증 (Authorization 헤더)
- cart_add: POST /ord/addBasket (JSON)
- cart_view: GET /ord/basketList?custCd={CUST_CD}&basketGbCd=01 (JSON 배열)
- cart_delete: DELETE /ord/deleteComOrdBasket?saveItemCd={code}&custCd=... (DELETE 메서드!)
- cart_clear: cart_delete 반복으로 폴백

## 유형 D: AngularJS + 쿠키 (세화약품)
- forms 비어있음, external_js에 angular 있음, 쿠키 있음
- Playwright fill이 Angular 모델을 업데이트 안 할 수 있음
  → execute_js로 scope에 직접 값 설정
- cart_add: POST /ajax/bag.asp (mode=add)
- cart_view: GET /ajax/bag.asp?mode=list (JSON)
- cart_delete: GET /ajax/bag.asp?mode=del&code={product_code}
- cart_clear: GET /ajax/bag.asp?mode=delall
- 삭제 함수: ng-click="Bag_Del(item)" → order.js에서 실제 URL 확인

## SPA 사이트에서 API 찾는 방법
1. open_site → fill_input → click(버튼) → get_network_log()
2. execute_js로 함수 소스 확인: AddCart.toString(), ProcessCart.toString()
3. jsf_com_GetAjax 같은 래퍼 함수 안에 실제 API URL이 숨어있을 수 있음
4. 삭제 아이콘 클릭 → get_network_log()로 DELETE/POST 캡처
5. AngularJS: ng-click 속성에서 함수명 → JS 파일에서 실제 URL 추출
"""


# ═══════════════════════════════════════════
# MCP Resources — 어떤 AI든 읽을 수 있는 문서
# ═══════════════════════════════════════════

@mcp.resource("recipes://list")
def resource_recipe_list() -> str:
    """등록된 모든 레시피 목록"""
    recipe_dir = PROJECT_ROOT / "recipes"
    recipes = []
    for f in sorted(recipe_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
            recipes.append({
                "site_id": data.get("site_id", f.stem),
                "site_name": data.get("site_name", ""),
                "site_url": data.get("site_url", ""),
                "recipe_version": data.get("recipe_version", 0),
                "has_pagination": "pagination" in data.get("search", {}),
                "has_sales_ledger": "sales_ledger" in data,
            })
        except Exception:
            pass
    return json.dumps(recipes, ensure_ascii=False, indent=2)


@mcp.resource("recipes://credentials-template")
def resource_credentials_template() -> str:
    """credentials.json 템플릿. 사용자가 채워야 할 항목을 보여줍니다."""
    template = {
        "site_id_here": {
            "username": "로그인 아이디",
            "password": "로그인 비밀번호",
            "site_params": {
                "VEN_CD": "거래처코드 (복산나이스팜/우정약품만 필요)",
                "VEN_NM": "약국이름 (복산나이스팜/우정약품만 필요)"
            }
        }
    }
    return json.dumps(template, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════
# 레시피 공유
# ═══════════════════════════════════════════

# Google Form URL (레시피 제출용)
_RECIPE_FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSf9-Ij0DgX20UJDGS2LTp0CxiLz_wQfm90dTf3WvB5G3ovYSw/viewform"

@mcp.tool()
def share_recipe(site_id: str) -> str:
    """레시피를 커뮤니티에 공유합니다. Google Form을 통해 검토 대기열에 제출됩니다."""
    import urllib.request
    import urllib.parse

    if not _RECIPE_FORM_URL:
        raise ValueError("레시피 공유 기능이 아직 설정되지 않았습니다. _RECIPE_FORM_URL을 설정하세요.")

    recipes = _load_recipes()
    recipe = recipes.get(site_id)
    if not recipe:
        raise ValueError(f"레시피 없음: {site_id}")

    site_name = recipe.get('site_name', site_id)
    site_url = recipe.get('site_url', '')
    recipe_json = json.dumps(recipe, ensure_ascii=False, indent=2)

    # Google Form 제출 (formResponse URL 사용)
    form_response_url = _RECIPE_FORM_URL.replace('/viewform', '/formResponse')

    data = urllib.parse.urlencode({
        'entry.975489715': site_name,
        'entry.741459457': site_url,
        'entry.935640016': recipe_json,
    }).encode('utf-8')

    try:
        req = urllib.request.Request(form_response_url, data=data)
        urllib.request.urlopen(req, timeout=10)
        return json.dumps({
            "success": True,
            "site_id": site_id,
            "site_name": site_name,
            "message": "레시피가 검토 대기열에 제출되었습니다. 관리자 검토 후 공개됩니다."
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        raise ValueError(f"레시피 제출 실패: {e}")


# ── 엔트리포인트 ──

def main():
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
