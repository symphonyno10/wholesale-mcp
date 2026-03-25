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
import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP
from .browser_engine import BrowserEngine, SNAPSHOT_JS

PACKAGE_DIR = Path(__file__).resolve().parent          # wholesale_mcp/ (번들 레시피)
# 데이터 디렉토리: 환경변수 > APPDATA > USERPROFILE > PyInstaller > cwd
# mcpb의 ${user_config.*}와 ${HOME}은 Windows에서 동작 불안정 (이슈 #52, #217)
# 서버가 OS API로 직접 경로 결정
def _resolve_data_dir() -> Path:
    env_dir = os.environ.get("WHOLESALE_MCP_DATA_DIR", "")
    if env_dir and "${" not in env_dir and "%" not in env_dir:
        p = Path(env_dir)
        if p.is_absolute():
            p.mkdir(parents=True, exist_ok=True)
            return p.resolve()
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "wholesale-mcp"
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        return Path(userprofile) / "wholesale-mcp-data"
    # Mac/Linux: ~/.wholesale-mcp
    home = Path.home()
    if home != Path("/"):
        return home / ".wholesale-mcp"
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent / "data"
    return Path.cwd()

DATA_DIR = _resolve_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 최초 실행 시 번들 레시피를 사용자 폴더에 복사
_bundled_candidates = [
    PACKAGE_DIR / "recipes",           # src/wholesale_mcp/recipes/
    PACKAGE_DIR.parent / "recipes",    # src/recipes/
    PACKAGE_DIR.parent.parent / "recipes",  # project_root/recipes/
]
if getattr(sys, 'frozen', False):
    import sys as _sys
    _bundled_candidates.insert(0, Path(_sys._MEIPASS) / "recipes")
_user_recipes = DATA_DIR / "recipes"
for _bundled_recipes in _bundled_candidates:
    if _bundled_recipes.exists() and _bundled_recipes.is_dir() and \
       _bundled_recipes.resolve() != _user_recipes.resolve():
        _user_recipes.mkdir(exist_ok=True)
        import shutil
        for src in _bundled_recipes.glob("*.json"):
            dst = _user_recipes / src.name
            if not dst.exists():
                shutil.copy2(src, dst)
        break

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("wholesale-mcp")
logger.info(f"DATA_DIR: {DATA_DIR}")
logger.info(f"APPDATA: {os.environ.get('APPDATA', '(없음)')}")
logger.info(f"frozen: {getattr(sys, 'frozen', False)}")

mcp = FastMCP("wholesale-tools")

# SQLite DB 초기화
from .db import WholesaleDB
_db = WholesaleDB(DATA_DIR / "wholesale.db")

# ── 상태 관리 ──
_engine = BrowserEngine()
_executors: dict = {}  # site_id → SiteExecutor
_credentials: dict = {}  # site_id → {username, password}
_recipes: dict = {}    # site_id → recipe dict
_last_recipe_draft: dict = {}  # 최근 analyze_page_for_recipe()의 recipe_draft (save_recipe 병합용)


# ── 헬퍼 ──

def _load_credentials() -> dict:
    """credentials.json에서 사이트별 로그인 정보 로드"""
    global _credentials
    if _credentials:
        return _credentials
    cred_path = DATA_DIR / "credentials.json"
    if cred_path.exists():
        try:
            _credentials = json.loads(cred_path.read_text(encoding="utf-8"))
            logger.info(f"크레덴셜 로드: {list(_credentials.keys())}")
        except Exception as e:
            logger.error(f"크레덴셜 로드 실패: {e}")
    return _credentials


def _get_credential(site_id: str) -> dict | None:
    """site_id로 credential 조회. _auto/_test suffix fallback 지원."""
    creds = _load_credentials()
    cred = creds.get(site_id)
    if not cred:
        # _auto, _test suffix 제거 후 재시도
        base_id = site_id.replace('_auto', '').replace('_test', '').rstrip('_')
        cred = creds.get(base_id)
    return cred


def _load_recipes() -> dict:
    """번들 + 사용자 recipes/ 디렉토리에서 레시피 JSON 로드"""
    global _recipes
    if _recipes:
        return _recipes

    # 1. 번들 레시피 (pip 패키지에 포함, 낮은 우선순위)
    bundled_dir = PACKAGE_DIR / "recipes"
    if bundled_dir.is_dir():
        for f in bundled_dir.glob("*.json"):
            try:
                recipe = json.loads(f.read_text(encoding="utf-8"))
                site_id = recipe.get("site_id", f.stem)
                _recipes[site_id] = recipe
                logger.info(f"레시피 로드 (번들): {site_id}")
            except Exception as e:
                logger.error(f"레시피 로드 실패 {f.name}: {e}")

    # 2. 사용자 레시피 (CWD/recipes/, 높은 우선순위 — 같은 site_id면 덮어씀)
    user_dir = DATA_DIR / "recipes"
    if user_dir.is_dir() and user_dir.resolve() != bundled_dir.resolve():
        for f in user_dir.glob("*.json"):
            try:
                recipe = json.loads(f.read_text(encoding="utf-8"))
                site_id = recipe.get("site_id", f.stem)
                _recipes[site_id] = recipe
                logger.info(f"레시피 로드 (사용자): {site_id}")
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
    output_dir = DATA_DIR / "screenshots"
    output_dir.mkdir(exist_ok=True)
    fname = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    fpath = output_dir / fname
    await _engine.screenshot(str(fpath))
    rel_path = str(fpath.relative_to(DATA_DIR))
    return json.dumps({
        "path": rel_path,
        "absolute_path": str(fpath),
        "how_to_read": f"read_data_file('{rel_path}')로 읽을 수 있습니다."
    }, ensure_ascii=False, indent=2)


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

    cred_path = DATA_DIR / "credentials.json"
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
    from .site_executor import SiteExecutor

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
    try:
        recipes = _load_recipes()
    except Exception as e:
        logger.error(f"레시피 로드 실패: {e}")
        return json.dumps([], ensure_ascii=False)
    try:
        creds = _load_credentials()
    except Exception as e:
        logger.error(f"크레덴셜 로드 실패: {e}")
        creds = {}
    sites = []
    for sid, r in recipes.items():
        # available_features가 레시피에 있으면 사용, 없으면 섹션 유무로 판단
        af = r.get("available_features", {})
        sites.append({
            "site_id": sid,
            "name": r.get("site_name", ""),
            "url": r.get("site_url", ""),
            "available_features": {
                "login": af.get("login", "login" in r),
                "search": af.get("search", "search" in r),
                "cart_add": af.get("cart_add", "cart_add" in r),
                "cart_view": af.get("cart_view", "cart_view" in r),
                "cart_delete": af.get("cart_delete", "cart_delete" in r),
                "cart_clear": af.get("cart_clear", "cart_clear" in r),
                "sales_ledger": af.get("sales_ledger", "sales_ledger" in r),
                "pagination": af.get("pagination", "pagination" in r.get("search", {})),
            },
            "has_credentials": sid in creds,
            "logged_in": sid in _executors and _executors[sid].is_authenticated(),
        })
    return json.dumps({
        "data_dir": str(DATA_DIR),
        "sites": sites,
        "file_tools": "list_data_files(), read_data_file(), write_data_file(), export_ledger_csv() 사용 가능"
    }, ensure_ascii=False, indent=2)


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

    # credentials.json에서 자동 로드 (_auto fallback 포함)
    cred = _get_credential(site_id) or {}
    if not username or not password:
        username = username or cred.get("username", "")
        password = password or cred.get("password", "")
    if not username or not password:
        raise ValueError(f"로그인 정보 없음: credentials.json에 {site_id} 추가 필요")

    # site_params: 사용자별 고유 값 (거래처 코드 등)
    site_params = cred.get("site_params", {})

    from .site_executor import SiteExecutor
    executor = SiteExecutor(recipe)
    ok = executor.login(username, password, site_params=site_params)
    if ok:
        _executors[site_id] = executor

    cookie_names = [c.name for c in executor.session.cookies]
    return json.dumps({
        "success": ok,
        "site_id": site_id,
        "authenticated": executor.is_authenticated(),
        "cookies": len(cookie_names),
        "cookie_names": cookie_names,
        "login_data_keys": list(executor._login_data.keys()) if hasattr(executor, '_login_data') else []
    }, ensure_ascii=False)


@mcp.tool()
def recipe_search(site_id: str, keyword: str, edi_code: str = "", max_pages: int = 0) -> str:
    """레시피 기반 HTTP 검색. max_pages=0이면 레시피 기본값 사용."""
    executor = _executors.get(site_id)
    if not executor or not executor.is_authenticated():
        raise ValueError(f"로그인 먼저 필요: {site_id}")

    products = executor.search(keyword, edi_code or None, max_pages=max_pages)
    items = [{
        "product_code": p.product_code,
        "product_name": p.product_name,
        "edi_code": p.edi_code,
        "manufacturer": p.manufacturer,
        "unit_price": p.unit_price,
        "stock_quantity": p.stock_quantity,
        "pack_unit": p.pack_unit,
        "pack_units": p.pack_units,
    } for p in products]

    # 약품 마스터 DB에 자동 저장
    try:
        _db.upsert_products(items, site_id)
    except Exception as e:
        logger.warning(f"약품 DB 저장 실패: {e}")

    # 결과가 많으면 파일로 저장하고 요약만 반환
    MAX_INLINE = 20
    if len(items) > MAX_INLINE:
        data_dir = DATA_DIR / "data"
        data_dir.mkdir(exist_ok=True)
        fpath = data_dir / f"search_{site_id}_{keyword}.json"
        fpath.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        rel_path = str(fpath.relative_to(DATA_DIR))
        return json.dumps({
            "total": len(items),
            "saved_to": rel_path,
            "sample": items[:5],
            "message": f"결과 {len(items)}건. 상위 5건만 표시.",
            "how_to_read": f"read_data_file('{rel_path}')로 전체 데이터를 읽으세요."
        }, ensure_ascii=False, indent=2)

    return json.dumps(items, ensure_ascii=False, indent=2)


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
    """장바구니에서 특정 상품 삭제. 장바구니가 비어있으면 실행하지 않는다."""
    executor = _executors.get(site_id)
    if not executor or not executor.is_authenticated():
        raise ValueError(f"로그인 먼저 필요: {site_id}")

    # 장바구니 비어있으면 실행 거부 (빈 장바구니 삭제 → 무한 팝업 방지)
    try:
        items = executor.view_cart()
        if not items:
            return json.dumps({
                "success": False,
                "site_id": site_id,
                "product_code": product_code,
                "error": "장바구니가 비어있습니다. 삭제할 상품이 없습니다."
            }, ensure_ascii=False, indent=2)
    except Exception:
        pass  # view_cart 실패해도 삭제 시도는 허용

    ok = executor.delete_from_cart(product_code)
    return json.dumps({
        "success": ok,
        "site_id": site_id,
        "product_code": product_code,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def recipe_clear_cart(site_id: str) -> str:
    """장바구니 전체 비우기. 장바구니가 비어있으면 실행하지 않는다."""
    executor = _executors.get(site_id)
    if not executor or not executor.is_authenticated():
        raise ValueError(f"로그인 먼저 필요: {site_id}")

    # 장바구니 비어있으면 실행 거부 (빈 장바구니 비우기 → 무한 팝업 방지)
    try:
        items = executor.view_cart()
        if not items:
            return json.dumps({
                "success": True,
                "site_id": site_id,
                "message": "장바구니가 이미 비어있습니다."
            }, ensure_ascii=False, indent=2)
    except Exception:
        pass  # view_cart 실패해도 비우기 시도는 허용

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
        "login_data": executor._login_data,
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

    items = [{
        "date": e.transaction_date,
        "product_name": e.product_name,
        "pack_unit": e.pack_unit,
        "quantity": e.quantity,
        "unit_price": e.unit_price,
        "sales_amount": e.sales_amount,
        "balance": e.balance,
    } for e in entries]

    # SQLite에 자동 저장 (필터 전 전체 데이터)
    try:
        _db.upsert_ledger(items, site_id)
    except Exception as e:
        logger.warning(f"DB 저장 실패: {e}")

    # 서버 측 후처리 필터 — 사이트가 product_filter를 무시하는 경우 대비
    if product_filter:
        items = [i for i in items if product_filter.lower() in (i.get('product_name', '') or '').lower()]

    total_amount = sum(i.get('sales_amount', 0) or 0 for i in items)

    # 필터 적용 후 결과가 적으면 인라인, 대량이면 파일 저장
    MAX_INLINE = 200 if product_filter else 20
    if len(items) > MAX_INLINE:
        data_dir = DATA_DIR / "data"
        data_dir.mkdir(exist_ok=True)
        fpath = data_dir / f"ledger_{site_id}.json"
        fpath.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        rel_path = str(fpath.relative_to(DATA_DIR))
        return json.dumps({
            "site_id": site_id,
            "period": f"{start_date or '(auto)'} ~ {end_date or '(today)'}",
            "detail_mode": "약품별 상세" if detail else "일자별 요약",
            "total_entries": len(items),
            "total_amount": total_amount,
            "saved_to": rel_path,
            "sample": items[:5],
            "message": f"매출원장 {len(items)}건 (합계 {total_amount:,.0f}원). 상위 5건만 표시.",
            "how_to_read": f"read_data_file('{rel_path}')로 전체 데이터를 읽으세요. CSV 내보내기: export_ledger_csv('{site_id}')"
        }, ensure_ascii=False, indent=2)

    return json.dumps({
        "site_id": site_id,
        "period": f"{start_date or '(auto)'} ~ {end_date or '(today)'}",
        "detail_mode": "약품별 상세" if detail else "일자별 요약",
        "total_entries": len(items),
        "total_amount": total_amount,
        "entries": items
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
    - button_actions: 버튼별 이벤트 핸들러 + API URL (3계층 추출)
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

    # 외부 JS fetch 제거됨 — button_actions (3계층 추출)가 대체

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

    # 3계층 API 추출 (button_actions)
    try:
        button_result = await _engine.extract_button_actions()
    except Exception:
        button_result = {"framework": {}, "button_actions": [], "extraction_layers_used": []}

    # ── recipe_draft: 검증된 셀렉터를 도구가 직접 생성 ──
    recipe_draft = {}
    try:
        recipe_draft = await page.evaluate("""() => {
            const draft = {};

            // 1. 검색 결과 테이블 → row_selector + fields 자동 추출
            const tables = document.querySelectorAll('table');
            for (const table of tables) {
                const rows = table.querySelectorAll('tbody tr');
                if (rows.length < 2) continue;

                // 클래스가 있는 행 찾기
                const classedRows = Array.from(rows).filter(r => r.className.trim());
                if (classedRows.length === 0) continue;

                const firstRow = classedRows[0];
                const rowClass = firstRow.className.trim().split(/\s+/)[0];

                // 행 셀렉터 검증: 이 셀렉터로 실제 몇 개 잡히는지
                const testSelector = `tr.${rowClass}`;
                const matched = document.querySelectorAll(testSelector).length;
                if (matched < 2) continue;

                // 필드 자동 추출
                const fields = {};
                const tds = firstRow.querySelectorAll('td');

                tds.forEach((td, i) => {
                    const cls = td.className.trim();
                    const text = td.textContent.trim().substring(0, 30);

                    // input 필드 (product_code, stock, price 등)
                    td.querySelectorAll('input[name]').forEach(inp => {
                        const name = inp.name;
                        const prefix = name.replace(/[_]?\d+$/, '');
                        if (prefix.includes('pc')) {
                            fields.product_code = {selector: `input[name^=${prefix}]`, attribute: 'value'};
                        } else if (prefix.includes('stock')) {
                            fields.stock_quantity = {selector: `input[name^=${prefix}]`, attribute: 'value'};
                        } else if (prefix.includes('price')) {
                            fields.price = {selector: `input[name^=${prefix}]`, attribute: 'value'};
                        } else if (prefix.includes('qty') || prefix.includes('bagQty')) {
                            fields.quantity = {selector: `input[name^=${prefix}]`, attribute: 'value'};
                        } else if (prefix.includes('soldout')) {
                            fields.remark = {selector: `input[name^=${prefix}]`, attribute: 'value'};
                        } else if (prefix.includes('bag_num')) {
                            fields.bag_num = {selector: `input[name^=${prefix}]`, attribute: 'value'};
                        }
                    });

                    // td 클래스 기반 필드
                    if (cls && !fields[cls]) {
                        const tdSelector = cls.includes(' ') ? `td.${cls.split(/\s+/)[0]}` : `td.${cls}`;
                        // 클래스명에서 필드명 추론
                        if (cls.includes('proName') || cls.includes('physic')) {
                            fields.product_name = tdSelector;
                        } else if (cls.includes('phaCompany') || cls.includes('maker')) {
                            fields.manufacturer = `${tdSelector} span` in document.querySelector(`${testSelector} ${tdSelector} span`) ? `${tdSelector} span` : tdSelector;
                        } else if (cls.includes('standard') || cls.includes('std')) {
                            fields.pack_unit = tdSelector;
                        } else if (cls.includes('stock')) {
                            if (!fields.stock_quantity) fields.stock_quantity = `${tdSelector} span`;
                        } else if (cls.includes('unitPrice') || cls.includes('price')) {
                            fields.unit_price = tdSelector;
                        } else if (cls.includes('amountMoney')) {
                            fields.total_price = tdSelector;
                        }
                    }

                    // a 태그 안의 텍스트 (상품명)
                    const link = td.querySelector('a');
                    if (link && text.length > 5 && !fields.product_name) {
                        fields.product_name = cls ? `td.${cls.split(/\\s+/)[0]} a` : `td:nth-child(${i+1}) a`;
                    }

                    // hidden div (div-product-detail 등)
                    const hiddenDiv = td.querySelector('div[style*="display:none"] ul li');
                    if (hiddenDiv && !fields.product_code) {
                        const divClass = td.querySelector('div[style*="display:none"]').className;
                        fields.product_code = {
                            selector: `.${divClass} ul li:first-child`,
                            attribute: 'text'
                        };
                    }
                });

                // td 클래스 없는 경우 nth-child로 폴백
                const headers = table.querySelectorAll('thead th, thead td');
                if (headers.length > 0 && Object.keys(fields).length < 3) {
                    headers.forEach((th, i) => {
                        const headerText = th.textContent.trim();
                        if (headerText.includes('제품명') || headerText.includes('품목')) {
                            if (!fields.product_name) fields.product_name = `td:nth-child(${i+1})`;
                        } else if (headerText.includes('제조사') || headerText.includes('제약')) {
                            if (!fields.manufacturer) fields.manufacturer = `td:nth-child(${i+1})`;
                        } else if (headerText.includes('규격')) {
                            if (!fields.pack_unit) fields.pack_unit = `td:nth-child(${i+1})`;
                        } else if (headerText.includes('단가') || headerText.includes('가격')) {
                            if (!fields.unit_price) fields.unit_price = `td:nth-child(${i+1})`;
                        } else if (headerText.includes('재고')) {
                            if (!fields.stock_quantity) fields.stock_quantity = `td:nth-child(${i+1})`;
                        } else if (headerText.includes('수량')) {
                            if (!fields.quantity) fields.quantity = `td:nth-child(${i+1})`;
                        }
                    });
                }

                if (Object.keys(fields).length >= 2) {
                    draft.search_table = {
                        row_selector: testSelector,
                        row_count: matched,
                        fields: fields,
                        verified: true
                    };
                    break;
                }
            }

            // 2. 폼 → search/cart_add 매핑
            document.querySelectorAll('form').forEach(form => {
                const action = form.action || '';
                const name = form.name || form.id || '';
                const method = (form.method || 'GET').toUpperCase();

                // 검색 폼
                const searchInput = form.querySelector('input[name*=physic], input[name*=product], input[name*=keyword], input[name*=srchText], input[name*=pnm]');
                if (searchInput && !draft.search_form) {
                    const params = {};
                    form.querySelectorAll('input, select').forEach(inp => {
                        if (inp.name && inp.type !== 'submit' && inp.type !== 'image') {
                            params[inp.name] = inp.value || '';
                        }
                    });
                    // 키워드 필드를 변수로
                    if (searchInput.name in params) {
                        params[searchInput.name] = '{KEYWORD}';
                    }
                    draft.search_form = {
                        url: action,
                        method: method,
                        keyword_field: searchInput.name,
                        params: params
                    };
                }

                // 장바구니 폼 (pc_, qty_ 패턴)
                const pcInput = form.querySelector('input[name^=pc_]');
                const qtyInput = form.querySelector('input[name^=qty_]');
                if (pcInput && qtyInput && !draft.cart_form) {
                    draft.cart_form = {
                        url: action,
                        method: method,
                        form_name: name,
                        product_code_prefix: 'pc_',
                        quantity_prefix: 'qty_',
                        type: 'form'
                    };
                }
            });

            return draft;
        }""")
    except Exception:
        recipe_draft = {}

    result = {
        "detected_type": detected_type,
        "url": analysis["url"],
        "title": analysis["title"],
        "forms": analysis["forms"],
        "tables": analysis["tables"],
        "all_links": analysis["all_links"],
        "button_actions": button_result.get("button_actions", []),
        "framework": button_result.get("framework", {}),
        "recipe_draft": recipe_draft,
        "accessibility_tree": await _engine.get_accessibility_tree(),
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
            ),
            "has_api_actions": len([a for a in button_result.get("button_actions", []) if a.get("api") or a.get("ajax_urls")]) > 0,
            "has_recipe_draft": bool(recipe_draft)
        }
    }

    # recipe_draft를 전역에 저장 (save_recipe 자동 병합용)
    global _last_recipe_draft
    if recipe_draft:
        _last_recipe_draft = recipe_draft

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

        # ── recipe_draft 자동 병합: AI가 parsing을 빠뜨려도 도구가 보완 ──
        global _last_recipe_draft
        if _last_recipe_draft:
            draft = _last_recipe_draft
            # search.parsing이 없으면 recipe_draft에서 자동 채움
            search = recipe.get("search", {})
            if search and not search.get("parsing", {}).get("selector"):
                st = draft.get("search_table", {})
                if st.get("row_selector"):
                    search.setdefault("parsing", {})["selector"] = st["row_selector"]
                    if st.get("fields") and not search["parsing"].get("fields"):
                        search["parsing"]["fields"] = st["fields"]
                    recipe["search"] = search
                    logger.info(f"[{site_id}] recipe_draft에서 search.parsing 자동 병합: {st['row_selector']}")

            # search_form params가 비어있으면 draft에서 채움
            sf = draft.get("search_form", {})
            if sf.get("params") and not search.get("params"):
                search["params"] = sf["params"]
                recipe["search"] = search
                logger.info(f"[{site_id}] recipe_draft에서 search.params 자동 병합")

        recipes_dir = DATA_DIR / "recipes"
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


# ═══════════════════════════════════════════
# 그룹 5: 파일 관리 도구 (MCP Filesystem 패턴 준수)
#
# 공식 레퍼런스: @modelcontextprotocol/server-filesystem
# - 파일 접근은 Tools로 제공 (Resources 아님)
# - allowed_directories 기반 샌드박스
# - 심링크/null byte 방어, 원자적 쓰기
# - Tool Annotations로 읽기/쓰기/위험도 명시
# ═══════════════════════════════════════════

# ── 경로 검증 (path-validation 모듈 상당) ──

def _validate_path(file_path: str, must_exist: bool = True) -> Path:
    """MCP filesystem 패턴 준수 경로 검증.

    보안 체크:
    1. null byte 차단
    2. 경로 정규화 (normalize + resolve)
    3. allowed directory (DATA_DIR) 내부 확인
    4. 심링크 실제 경로 확인
    """
    # null byte 차단
    if '\x00' in file_path:
        raise ValueError("경로에 null byte를 포함할 수 없습니다.")

    data_root = DATA_DIR.resolve()
    p = Path(file_path)
    if p.is_absolute():
        target = p.resolve()
    else:
        target = (DATA_DIR / file_path).resolve()

    # 샌드박스 경계 확인
    try:
        target.relative_to(data_root)
    except ValueError:
        raise ValueError(
            f"허용된 디렉토리 외부 접근 거부: {file_path}\n"
            f"허용 디렉토리: {data_root}"
        )

    if must_exist:
        if not target.exists():
            raise ValueError(
                f"파일이 없습니다: {file_path}\n"
                f"list_data_files()로 사용 가능한 파일을 확인하세요."
            )
        # 심링크 실제 경로 확인
        real_path = target.resolve(strict=True)
        try:
            real_path.relative_to(data_root)
        except ValueError:
            raise ValueError(f"접근 거부 — 심링크 대상이 허용 디렉토리 외부: {file_path}")
    else:
        # 새 파일: 부모 디렉토리 검증
        parent = target.parent
        if parent.exists():
            real_parent = parent.resolve(strict=True)
            try:
                real_parent.relative_to(data_root)
            except ValueError:
                raise ValueError(f"접근 거부 — 부모 디렉토리가 허용 범위 외부: {file_path}")

    return target


def _atomic_write(target: Path, content: str, encoding: str = 'utf-8') -> None:
    """원자적 파일 쓰기 (TOCTOU 방지).

    공식 filesystem 서버 패턴: 임시파일 → atomic rename.
    """
    import tempfile
    target.parent.mkdir(parents=True, exist_ok=True)

    # 같은 디렉토리에 임시파일 → rename (크로스 파일시스템 방지)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".{target.stem}_",
        suffix=".tmp"
    )
    try:
        with os.fdopen(fd, 'w', encoding=encoding) as f:
            f.write(content)
        os.replace(tmp_path, str(target))  # atomic on POSIX and Windows
    except Exception:
        # 실패 시 임시파일 정리
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── 민감 파일 보호 목록 ──
_PROTECTED_FILES = {"credentials.json"}


@mcp.tool(annotations={"readOnlyHint": True, "title": "허용된 디렉토리 목록"})
def list_allowed_directories() -> str:
    """이 MCP 서버가 접근할 수 있는 디렉토리를 표시합니다.

    모든 파일 도구(read/write/list)는 이 디렉토리 내부에서만 작동합니다.
    AI 클라이언트의 자체 파일 도구(Read, Write 등)로는 이 디렉토리에 접근할 수 없으므로,
    반드시 MCP 서버의 파일 도구를 사용해야 합니다.
    """
    data_root = DATA_DIR.resolve()
    subdirs = []
    for d in sorted(data_root.iterdir()):
        if d.is_dir():
            subdirs.append(str(d.relative_to(data_root)))

    return json.dumps({
        "allowed_directories": [str(data_root)],
        "data_dir": str(data_root),
        "subdirectories": subdirs,
        "available_tools": {
            "read": ["read_data_file", "get_file_info"],
            "write": ["write_data_file", "export_ledger_csv"],
            "list": ["list_data_files", "search_data_files"],
            "meta": ["list_allowed_directories"],
        },
        "note": "AI 클라이언트의 자체 파일 도구(Read/Write)로는 이 디렉토리에 접근 불가. 반드시 위 MCP 도구를 사용하세요."
    }, ensure_ascii=False, indent=2)


@mcp.tool(annotations={"readOnlyHint": True, "title": "데이터 파일 읽기"})
def read_data_file(file_path: str, offset: int = 0, limit: int = 200,
                   keyword: str = "", head: int = 0, tail: int = 0) -> str:
    """데이터 파일 읽기 (매출원장 JSON, 레시피, CSV 등).

    이 도구는 MCP 서버의 데이터 디렉토리 내 파일을 읽습니다.
    AI 클라이언트의 자체 파일 도구로는 이 파일들에 접근할 수 없으므로,
    반드시 이 도구를 사용하세요.

    Parameters:
    - file_path: 파일 경로 (상대경로 권장: "data/ledger_site.json")
    - offset: 건너뛸 항목 수 (JSON 배열인 경우)
    - limit: 반환할 최대 항목 수 (기본 200)
    - keyword: 검색 필터 (JSON 배열의 모든 필드에서 검색)
    - head: 첫 N줄만 반환 (텍스트/CSV, 0=전체)
    - tail: 마지막 N줄만 반환 (텍스트/CSV, 0=전체)

    사용 가능한 파일 목록은 list_data_files()로 확인하세요.
    """
    target = _validate_path(file_path)

    # JSON 파일이면 항목별 검색/페이징 지원
    if target.suffix == '.json':
        data = json.loads(target.read_text(encoding='utf-8'))
        if isinstance(data, list):
            if keyword:
                data = [item for item in data
                        if keyword.lower() in json.dumps(item, ensure_ascii=False).lower()]
            total = len(data)
            sliced = data[offset:offset + limit]
            return json.dumps({
                "total": total,
                "offset": offset,
                "limit": limit,
                "keyword": keyword,
                "count": len(sliced),
                "items": sliced,
                "data_dir": str(DATA_DIR),
            }, ensure_ascii=False, indent=2)
        # dict 등 다른 JSON은 그대로 반환
        return json.dumps(data, ensure_ascii=False, indent=2)

    # 텍스트/CSV 파일
    text = target.read_text(encoding='utf-8')
    if head > 0:
        lines = text.splitlines(keepends=True)
        text = ''.join(lines[:head])
    elif tail > 0:
        lines = text.splitlines(keepends=True)
        text = ''.join(lines[-tail:])

    if len(text) > 500_000:
        return text[:500_000] + f"\n\n... (파일 크기 {len(text):,} bytes, 500KB까지 표시)"
    return text


# 하위 호환: read_project_file → read_data_file 별칭
@mcp.tool(annotations={"readOnlyHint": True, "title": "[별칭] 데이터 파일 읽기"})
def read_project_file(file_path: str, offset: int = 0, limit: int = 200,
                      keyword: str = "") -> str:
    """[별칭] read_data_file과 동일. 데이터 파일 읽기."""
    return read_data_file(file_path, offset, limit, keyword)


@mcp.tool(annotations={"readOnlyHint": True, "title": "파일 정보 조회"})
def get_file_info(file_path: str) -> str:
    """파일 메타데이터 조회 (크기, 수정일, 타입).

    Parameters:
    - file_path: 파일 경로 (상대 또는 절대)
    """
    target = _validate_path(file_path)
    stat = target.stat()
    return json.dumps({
        "path": str(target.relative_to(DATA_DIR)),
        "absolute_path": str(target),
        "size": stat.st_size,
        "size_human": f"{stat.st_size:,} bytes" if stat.st_size < 1024*1024
                      else f"{stat.st_size/1024/1024:.1f} MB",
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "created": datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
        "is_symlink": target.is_symlink(),
        "suffix": target.suffix,
    }, ensure_ascii=False, indent=2)


@mcp.tool(annotations={"readOnlyHint": True, "title": "파일 목록 조회"})
def list_data_files(subdirectory: str = "") -> str:
    """데이터 디렉토리의 파일 목록 조회.

    AI 클라이언트가 접근 가능한 파일 목록을 확인합니다.
    saved_to로 반환된 파일 경로를 찾을 때 유용합니다.

    Parameters:
    - subdirectory: 하위 디렉토리 (예: "data", "recipes", "screenshots"). 비어있으면 전체.
    """
    if subdirectory:
        target_dir = _validate_path(subdirectory)
        if not target_dir.is_dir():
            raise ValueError(f"디렉토리가 아닙니다: {subdirectory}")
    else:
        target_dir = DATA_DIR

    if not target_dir.exists():
        return json.dumps({
            "data_dir": str(DATA_DIR),
            "subdirectory": subdirectory,
            "files": [],
            "message": "디렉토리가 비어있거나 존재하지 않습니다."
        }, ensure_ascii=False, indent=2)

    files = []
    for f in sorted(target_dir.rglob("*")):
        if f.is_file():
            rel = f.relative_to(DATA_DIR)
            stat = f.stat()
            files.append({
                "path": str(rel),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })

    return json.dumps({
        "data_dir": str(DATA_DIR),
        "subdirectory": subdirectory or "(전체)",
        "total_files": len(files),
        "files": files,
        "usage": "read_data_file('경로')로 파일 내용을 읽으세요."
    }, ensure_ascii=False, indent=2)


@mcp.tool(annotations={"readOnlyHint": True, "title": "파일 검색"})
def search_data_files(pattern: str = "*.json", keyword: str = "") -> str:
    """데이터 디렉토리 내 파일 검색 (glob 패턴 + 내용 검색).

    Parameters:
    - pattern: glob 패턴 (예: "*.json", "data/ledger_*.csv", "**/*.json")
    - keyword: 파일 내용에서 검색할 키워드 (선택)
    """
    data_root = DATA_DIR.resolve()
    matches = []

    for f in sorted(data_root.rglob(pattern)):
        if not f.is_file():
            continue
        # 심링크 체크
        try:
            f.resolve(strict=True).relative_to(data_root)
        except (ValueError, OSError):
            continue

        rel = str(f.relative_to(data_root))
        stat = f.stat()
        entry = {
            "path": rel,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        }

        if keyword:
            try:
                content = f.read_text(encoding='utf-8', errors='ignore')
                if keyword.lower() not in content.lower():
                    continue
                # 매칭 라인 미리보기
                for line in content.splitlines():
                    if keyword.lower() in line.lower():
                        entry["match_preview"] = line.strip()[:200]
                        break
            except Exception:
                continue

        matches.append(entry)
        if len(matches) >= 100:
            break

    return json.dumps({
        "pattern": pattern,
        "keyword": keyword,
        "total_matches": len(matches),
        "files": matches,
    }, ensure_ascii=False, indent=2)


@mcp.tool(annotations={
    "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True,
    "title": "데이터 파일 쓰기"
})
def write_data_file(file_path: str, content: str, format: str = "auto") -> str:
    """데이터 파일 쓰기/내보내기 (CSV, JSON, 텍스트).

    매출원장 데이터를 CSV로 변환하거나, 분석 결과를 저장할 때 사용합니다.
    AI 클라이언트의 자체 파일 도구로는 데이터 디렉토리에 쓸 수 없으므로,
    반드시 이 도구를 사용하세요.

    Parameters:
    - file_path: 상대 경로 (예: "data/analysis.csv", "data/report.json")
    - content: 파일 내용 (텍스트/CSV/JSON 문자열)
    - format: "auto"(확장자 추론), "csv", "json", "text"

    보안: 데이터 디렉토리 내부에만 쓸 수 있습니다. credentials.json 수정 불가.
    """
    target = _validate_path(file_path, must_exist=False)

    # 민감 파일 보호
    if target.name in _PROTECTED_FILES:
        raise ValueError(f"{target.name}은(는) 이 도구로 수정할 수 없습니다. register_site()를 사용하세요.")

    # JSON 포맷 정규화
    if format == "json" or (format == "auto" and target.suffix == '.json'):
        try:
            parsed = json.loads(content)
            content = json.dumps(parsed, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass

    # 원자적 쓰기
    _atomic_write(target, content)
    rel_path = target.relative_to(DATA_DIR.resolve())

    return json.dumps({
        "success": True,
        "file_path": str(rel_path),
        "absolute_path": str(target),
        "size": target.stat().st_size,
        "message": f"파일 저장 완료. read_data_file('{rel_path}')로 읽을 수 있습니다."
    }, ensure_ascii=False, indent=2)


@mcp.tool(annotations={
    "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
    "title": "매출원장 CSV 내보내기"
})
def export_ledger_csv(site_id: str, start_date: str = "", end_date: str = "",
                      period: str = "3m", product_filter: str = "") -> str:
    """매출원장 데이터를 CSV 파일로 내보내기.

    대량 데이터 분석/통계에 적합합니다. 결과를 CSV+JSON으로 저장하고 경로를 반환합니다.

    Parameters:
    - site_id: 사이트 ID
    - start_date, end_date: 날짜 범위 (YYYY-MM-DD)
    - period: 상대 기간 ("1w", "1m", "3m", "6m", "1y")
    - product_filter: 약품명 필터
    """
    import csv
    import io

    executor = _executors.get(site_id)
    if not executor or not executor.is_authenticated():
        raise ValueError(f"로그인 먼저 필요: {site_id}")

    entries = executor.get_sales_ledger(
        start_date=start_date, end_date=end_date,
        period=period, detail_mode="0",
        product_filter=product_filter
    )

    items = [{
        "date": e.transaction_date,
        "product_name": e.product_name,
        "pack_unit": e.pack_unit,
        "quantity": e.quantity,
        "unit_price": e.unit_price,
        "sales_amount": e.sales_amount,
        "balance": e.balance,
    } for e in entries]

    if product_filter:
        items = [i for i in items if product_filter.lower() in (i.get('product_name', '') or '').lower()]

    if not items:
        return json.dumps({"error": "데이터 없음", "site_id": site_id}, ensure_ascii=False)

    # CSV 생성
    output = io.StringIO()
    fieldnames = ["date", "product_name", "pack_unit",
                  "quantity", "unit_price", "sales_amount", "balance"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(items)
    csv_content = output.getvalue()

    # 원자적 쓰기
    data_dir = DATA_DIR / "data"
    data_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d")
    csv_path = data_dir / f"ledger_{site_id}_{timestamp}.csv"
    _atomic_write(csv_path, csv_content, encoding='utf-8-sig')  # BOM for Excel

    json_path = data_dir / f"ledger_{site_id}.json"
    _atomic_write(json_path, json.dumps(items, ensure_ascii=False, indent=2))

    total_amount = sum(i.get('sales_amount', 0) or 0 for i in items)

    return json.dumps({
        "success": True,
        "site_id": site_id,
        "total_entries": len(items),
        "total_amount": total_amount,
        "csv_file": str(csv_path.relative_to(DATA_DIR)),
        "json_file": str(json_path.relative_to(DATA_DIR)),
        "message": f"매출원장 {len(items)}건 CSV/JSON 저장 완료. "
                   f"read_data_file('{csv_path.relative_to(DATA_DIR)}')로 읽을 수 있습니다.",
        "sample": items[:5],
    }, ensure_ascii=False, indent=2)


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
4. 모든 페이지에서 폼 요소를 빠짐없이 확인:
   - 셀렉트박스: execute_js로 모든 option 목록과 현재 선택값 읽기
   - 라디오버튼: 각 값과 라벨 확인, 현재 체크된 것 확인
   - 체크박스: 체크 상태와 값 확인
   - 드롭다운/콤보박스: 클릭해서 펼쳐진 목록 확인
   - 옵션을 전환하면 결과가 달라질 수 있으므로:
     기본값으로 조회 → screenshot → 다른 값으로 전환 → 재조회 → screenshot → 비교

## STEP 1: 로그인

1. open_site("{site_url}") → snapshot_page()
2. "로그인" 버튼 발견 → 근처의 입력 필드 확인 (password, text)
3. fill_input으로 ID/PW 입력
4. "로그인" 버튼의 코드 추적 → form action 또는 AJAX URL 확인
5. capture_form_submission() → 실제 전송된 URL + payload 최종 확정
6. get_cookies() → 새로 생긴 쿠키 = success_indicator
   JWT: get_network_log()에서 토큰 응답 확인
7. 로그인 후 screenshot() → 팝업이 UI를 가리면 "닫기"/"확인" 버튼 클릭 → 반복

## STEP 2: 메인 페이지 전체 분석

1. analyze_page_for_recipe("search") → 반환값에 recipe_draft 포함
   - recipe_draft.search_table: 검증된 row_selector + fields (도구가 직접 검증)
   - recipe_draft.search_form: 검색 폼의 URL, method, params (키워드 필드 자동 감지)
   - recipe_draft.cart_form: 장바구니 폼의 URL, form_name, prefix
   - button_actions: 삭제/비우기 등 이벤트 핸들러의 API URL
   - all_links: 매출원장 등 메뉴 링크 (hidden 포함)

2. **recipe_draft가 있으면 셀렉터를 직접 작성하지 말고 그대로 사용하라.**
   - recipe_draft.search_table.row_selector → search.parsing.selector에 복사
   - recipe_draft.search_table.fields → search.parsing.fields에 복사
   - recipe_draft.search_form.params → search.params에 복사
   - recipe_draft.cart_form → cart_add에 복사

3. recipe_draft가 없거나 불충분한 경우에만 직접 탐색 (SPA 사이트)

4. hidden 메뉴 탐색: all_links에서 visible=false인 링크 확인

## STEP 3: 검색

1. recipe_draft.search_form이 있으면 → 그대로 사용. 검색 1회만 실행하여 확인.
2. 없으면 → "검색"/"조회" 버튼 찾아서 코드 추적
3. fill_input으로 "타이레놀" 입력 → 버튼 클릭
4. get_network_log() → 실제 검색 API URL + 파라미터 캡처
4. screenshot()으로 검색 결과 화면을 직접 확인 — 가격(단가) 컬럼이 테이블에 있는지 눈으로 확인
5. 가격 컬럼이 없으면 (중요!):
   - 검색 결과에서 약품명을 click_element()로 클릭
   - screenshot()으로 오른쪽 또는 하단에 나타나는 제품정보/상세 패널 확인
   - 패널에 "주문단가", "단가", "가격" 등이 보이면 → get_network_log()로 해당 API 캡처
   - 캡처한 상세 API(URL, 메서드, 파라미터, 가격 위치)를 레시피 search.product_detail에 반영
   - 이 단계를 건너뛰면 안 됨 — 가격 정보 없는 레시피는 불완전
5. 결과 파싱 — 컬럼 매핑을 정확히 결정:
   A) HTML:
      - execute_js로 테이블 헤더(th) 목록 읽기 → 각 컬럼의 의미 파악
      - 첫 데이터 행의 각 td 텍스트와 class를 순서대로 읽기
      - product_name, product_code, manufacturer, unit_price, stock_quantity 등을 정확히 매핑
      - hidden div(display:none) 안에 상품코드 등이 있을 수 있으므로 확인
      - screenshot과 대조하여 파싱 결과가 화면과 일치하는지 검증
   B) JSON:
      - get_network_log body_preview에서 실제 키와 값 확인
      - items_path 결정 (배열이면 "", 객체면 해당 키)
      - json_mapping: {{"items_path": "경로", "fields": {{"product_code": "실제키", ...}}}}

## STEP 3.5: 페이지네이션

1. "다음", "2", "3" 페이지 링크 발견 → href에서 페이지 파라미터 확인
   → type: "html_links", paging_selector, page_url_param
2. 링크 없으면 → get_network_log()에서 JSON 응답의 totalPage 확인
   → type: "param", page_param
3. 둘 다 없으면 → pagination 없음

## STEP 4: 장바구니 전체 흐름 (한 세션에서 연속 실행, 네트워크 끊지 않음)

1. "담기" 버튼 코드 추적 → 담기 실행 → screenshot + get_network_log
2. 장바구니 영역 screenshot → 상품 보이는지 확인 → get_network_log에서 조회 API 캡처
3. "삭제" 버튼/아이콘 screenshot → 코드 추적 → 클릭 → get_network_log에서 삭제 API 캡처
4. 다시 1개 담기 → "전체삭제"/"비우기" 버튼 screenshot → 클릭 → get_network_log에서 비우기 API 캡처
   - 전체삭제 버튼이 없으면: 테이블 헤더의 전체선택 체크박스 확인 → 체크 → 선택삭제 클릭
   - 어떤 방식이든 안 되면 cart_clear는 false (코드가 view+delete 폴백 자동 수행)
5. 전체 네트워크 로그에서 cart 관련 URL 전부 추출 → 레시피에 반영

## STEP 5: 매출원장

1. STEP 2의 all_links에서 매출원장 관련 링크 탐색:
   - "매출원장" 링크 → 기본 매출원장 (일자별/거래명세서별)
   - "품목수불" 링크가 있으면 → 품목별 상세 (약품명+수량+단가) ← 이쪽이 더 정확
   - **품목수불이 있으면 품목수불을 우선 사용** (수량/단가가 포함됨)
   - 매출원장만 있으면 매출원장 사용

2. 매출원장/품목수불 페이지 이동 후 UI 확인:
   - 날짜 입력 필드, "조회" 버튼
   - **라디오 버튼을 반드시 확인** → execute_js로 모든 라디오의 name, value, 주변 텍스트 캡처:
     ```
     document.querySelectorAll('input[type=radio]').forEach(r => {{
       {{name: r.name, value: r.value, checked: r.checked,
        label: r.parentElement.textContent.trim()}}
     }})
     ```
   - 라디오에 "제품별 상세", "상세 조회" 같은 옵션이 있으면 → 그 value가 detail
   - "거래명세서별", "합계", "요약" 같은 옵션 → 그 value가 summary
   - 셀렉트박스도 동일하게 option value 확인

3. 상세 모드로 조회:
   - 상세 라디오/셀렉트 선택 → 최근 3일로 날짜 설정 → 조회
   - get_network_log()로 실제 API + 파라미터 캡처
   - **반드시 detail_values 기록**:
     ```
     "detail_values": {{"detail": "상세 라디오 value", "summary": "요약 라디오 value"}}
     ```
   - 해당 파라미터에 {{DETAIL_MODE}} 변수 사용

4. 결과 파싱 — 반드시 수량/단가가 있는지 확인:
   A) HTML 응답:
      - execute_js로 테이블 헤더(th) 읽기 → 컬럼 의미 파악
      - 첫 데이터 행의 td 텍스트와 class를 읽기
      - **수량/단가 컬럼이 있는지 반드시 확인**
      - 없으면 → 품목수불 페이지로 전환하거나, 행 클릭으로 상세 API 탐색
      - 제조사+약품명이 별도 컬럼이면 product_name에 약품명 매핑 (제조사 아님)
      - 합계/월계/누계 행 제외하는 셀렉터 사용
   B) JSON 응답:
      - get_network_log body_preview에서 키 확인 → json_mapping 작성
   C) 2단계 조회 (요약만 나오는 사이트):
      - "외 N종" 텍스트, 수량=0 → 요약 목록
      - 요약 행 클릭 → get_network_log → 상세 API 캡처
      - 레시피에 sales_ledger_detail 섹션으로 반드시 기록

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

JSON API의 경우 response_type: "json" + json_mapping 사용:
```json
"response_type": "json",
"json_mapping": {{
  "items_path": "list",
  "fields": {{
    "product_code": "physicCd",
    "product_name": "physicNm",
    "unit_price": "unitCost",
    "stock_quantity": "stock"
  }}
}}
```
- items_path: 응답이 배열이면 "" (빈 문자열), 객체 안에 있으면 해당 키 ("list", "data.items" 등)
- fields: 레시피 필드명 → JSON 응답의 실제 키 매핑

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
    recipes = _load_recipes()
    recipe_dir = DATA_DIR / "recipes"
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


# ═══════════════════════════════════════════
# 그룹 6: SQLite 매출원장 분석 도구
# ═══════════════════════════════════════════

@mcp.tool()
def sync_ledger(site_id: str, period: str = "3m") -> str:
    """매출원장을 사이트에서 가져와 SQLite DB에 누적 저장.

    중복 데이터는 자동 스킵. 호출할 때마다 새 데이터만 추가됩니다.

    Parameters:
    - site_id: 사이트 ID 또는 "all" (전체 사이트)
    - period: 동기화 기간 ("1w", "1m", "3m", "6m", "1y")
    """
    if site_id == "all":
        results = {}
        for sid in _executors:
            try:
                entries = _executors[sid].get_sales_ledger(period=period, detail_mode="0")
                items = [{"date": e.transaction_date, "product_name": e.product_name,
                         "pack_unit": e.pack_unit, "quantity": e.quantity,
                         "unit_price": e.unit_price, "sales_amount": e.sales_amount,
                         "balance": e.balance, "manufacturer": e.manufacturer,
                         "edi_code": getattr(e, 'edi_code', '')} for e in entries]
                results[sid] = _db.upsert_ledger(items, sid)
            except Exception as e:
                results[sid] = {"error": str(e)[:80]}
        return json.dumps(results, ensure_ascii=False, indent=2)

    executor = _executors.get(site_id)
    if not executor or not executor.is_authenticated():
        raise ValueError(f"로그인 먼저 필요: {site_id}")

    entries = executor.get_sales_ledger(period=period, detail_mode="0")
    items = [{"date": e.transaction_date, "product_name": e.product_name,
             "pack_unit": e.pack_unit, "quantity": e.quantity,
             "unit_price": e.unit_price, "sales_amount": e.sales_amount,
             "balance": e.balance, "manufacturer": e.manufacturer,
             "edi_code": getattr(e, 'edi_code', '')} for e in entries]
    result = _db.upsert_ledger(items, site_id)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def search_ledger(keyword: str, site_id: str = "all", period: str = "3m",
                  limit: int = 100) -> str:
    """SQLite DB에서 약품명으로 매출원장 검색.

    사이트에 접속하지 않고 로컬 DB에서 즉시 검색합니다.
    DB에 데이터가 없으면 sync_ledger를 먼저 실행하세요.

    Parameters:
    - keyword: 약품명 검색어
    - site_id: 사이트 ID 또는 "all"
    - period: 검색 기간
    - limit: 최대 반환 건수
    """
    rows = _db.search(keyword, site_id, period, limit)
    return json.dumps({
        "keyword": keyword,
        "period": period,
        "total": len(rows),
        "entries": rows
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def ledger_summary(site_id: str = "all", period: str = "1m",
                   top_n: int = 20) -> str:
    """약품별 주문 합계 (TOP N).

    가장 많이 주문한 약품, 매출액 순위를 확인합니다.

    Parameters:
    - site_id: 사이트 ID 또는 "all" (전체 도매)
    - period: 기간
    - top_n: 상위 몇 개
    """
    rows = _db.summary(site_id, period, top_n)
    return json.dumps({
        "site_id": site_id,
        "period": period,
        "top_n": top_n,
        "items": rows
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def ledger_compare(keyword: str, period: str = "3m") -> str:
    """도매별 같은 약품 가격 비교.

    여러 도매에서 같은 약품의 단가, 주문량을 비교합니다.

    Parameters:
    - keyword: 약품명 검색어
    - period: 비교 기간
    """
    rows = _db.compare(keyword, period)
    return json.dumps({
        "keyword": keyword,
        "period": period,
        "sites": rows
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def ledger_trend(keyword: str = "", site_id: str = "all",
                 period: str = "6m") -> str:
    """월별 주문 추이.

    Parameters:
    - keyword: 약품명 (빈 값이면 전체)
    - site_id: 사이트 ID 또는 "all"
    - period: 기간
    """
    rows = _db.trend(keyword, site_id, period)
    return json.dumps({
        "keyword": keyword or "(전체)",
        "site_id": site_id,
        "period": period,
        "months": rows
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def db_stats() -> str:
    """SQLite DB 현황 — 저장된 매출원장/약품 수, 사이트별 통계."""
    stats = _db.stats()
    return json.dumps(stats, ensure_ascii=False, indent=2)


# ── 엔트리포인트 ──

def main():
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
