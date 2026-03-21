"""
레시피 기반 HTTP 실행 엔진

레시피 JSON을 읽어서 requests.Session + BeautifulSoup으로
도매 사이트의 로그인/검색/장바구니/주문을 실행하는 범용 엔진.
"""
import re
import time
import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup

from datetime import date, timedelta
from recipe_schema import WholesaleProduct, OrderResult, SalesLedgerEntry, CartItem
from recipe_normalizer import normalize_recipe

logger = logging.getLogger(__name__)


class SiteExecutor:
    """레시피 JSON을 읽어서 실제 HTTP 요청을 실행하는 엔진"""

    def __init__(self, recipe: dict):
        self.recipe = normalize_recipe(recipe)
        self.site_id = self.recipe.get('site_id', 'unknown')
        self.site_url = self.recipe.get('site_url', '').rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self._encoding = self.recipe.get('encoding', 'utf-8')
        self.session.verify = self.recipe.get('connection', {}).get('ssl_verify', True)
        self._request_interval = self.recipe.get('connection', {}).get('request_interval_ms', 500) / 1000
        self._last_request_time = 0
        self._authenticated = False
        self._last_search_html = ''
        self._last_search_url = ''
        self._search_html_pages: list[str] = []
        self._cached_username = ''
        self._cached_password = ''
        self._cached_site_params = {}
        self._login_data = {}  # 로그인 응답에서 추출한 사용자 정보 (custCd, userId 등)

    def _get_step(self, step_name: str) -> Optional[dict]:
        step = self.recipe.get(step_name)
        return step if isinstance(step, dict) else None

    def _get_url(self, spec: dict) -> str:
        return spec.get('url') or spec.get('action') or spec.get('endpoint') or ''

    def _resolve_payload(self, payload: dict, variables: dict) -> dict:
        # 로그인 데이터도 변수로 사용 가능
        all_vars = dict(self._login_data)
        all_vars.update(variables)
        resolved = {}
        for key, val in payload.items():
            if isinstance(val, str):
                for var_name, var_val in all_vars.items():
                    val = val.replace(f'{{{var_name}}}', str(var_val))
                    val = val.replace(f'{{{var_name.lower()}}}', str(var_val))
                    val = val.replace(f'{{{var_name.upper()}}}', str(var_val))
            resolved[key] = val
        return resolved

    def _throttle(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self._request_interval:
            time.sleep(self._request_interval - elapsed)
        self._last_request_time = time.time()

    def _build_url(self, path: str, base_url: str = None) -> str:
        if path.startswith('http'):
            return path
        from urllib.parse import urljoin
        base = base_url or self.site_url + '/'
        return urljoin(base, path)

    def _make_request(self, method: str, url: str, content_type: str = None,
                      data: dict = None, headers: dict = None,
                      timeout: int = 30) -> requests.Response:
        self._throttle()
        kwargs = {'timeout': timeout}
        if content_type == 'application/json':
            kwargs['json'] = data
        else:
            kwargs['data'] = data
        if headers:
            kwargs['headers'] = {**self.session.headers, **headers}
        full_url = self._build_url(url)
        logger.debug(f"[{self.site_id}] {method} {full_url}")
        resp = self.session.request(method, full_url, **kwargs)

        # 401 → JWT 만료 시 자동 재로그인 후 재시도
        if resp.status_code == 401 and self._cached_username and 'Authorization' in self.session.headers:
            logger.info(f"[{self.site_id}] 401 감지 → 자동 재로그인 시도")
            if self.login(self._cached_username, self._cached_password, self._cached_site_params):
                resp = self.session.request(method, full_url, **kwargs)

        # 429 → Rate limit 시 대기 후 재시도
        if resp.status_code == 429:
            import time as _time
            retry_after = int(resp.headers.get('Retry-After', '3'))
            logger.info(f"[{self.site_id}] 429 → {retry_after}초 대기 후 재시도")
            _time.sleep(retry_after)
            resp = self.session.request(method, full_url, **kwargs)

        if self._encoding and self._encoding.lower() != 'utf-8':
            resp.encoding = self._encoding
        resp.raise_for_status()
        return resp

    def _check_success(self, resp: requests.Response, indicator: dict) -> bool:
        if not indicator:
            return resp.ok
        ind_type = indicator.get('type', '')
        if ind_type == 'redirect':
            return indicator.get('value', '') in resp.url
        elif ind_type == 'json_field':
            try:
                data = resp.json()
                path = indicator.get('path', '')
                value = indicator.get('value', '')
                actual = data.get(path, '')
                # value가 빈 문자열이면 "키가 존재하고 값이 있으면 성공"
                if value == '':
                    return bool(actual)
                return str(actual) == str(value)
            except Exception:
                return False
        elif ind_type == 'cookie':
            cookie_key = indicator.get('key', '')
            return cookie_key in self.session.cookies
        elif ind_type == 'status_code':
            return resp.status_code == indicator.get('value', 200)
        elif ind_type == 'contains':
            return indicator.get('value', '') in resp.text
        return resp.ok

    # ─── 로그인 ──────────────────────────────────────────

    def login(self, username: str, password: str, site_params: dict = None) -> bool:
        # 재로그인용 캐시
        self._cached_username = username
        self._cached_password = password
        self._cached_site_params = site_params or {}

        # site_params: credentials.json의 사용자별 고유 값 (거래처 코드 등)
        if site_params:
            self._login_data.update(site_params)

        login_spec = self._get_step('login')
        if not login_spec:
            logger.error(f"[{self.site_id}] 로그인 레시피 없음")
            return False

        login_url = self._get_url(login_spec)
        if not login_url:
            logger.error(f"[{self.site_id}] 로그인 URL 누락")
            return False

        payload = login_spec.get('payload', {})
        fields = login_spec.get('fields', {})

        if payload:
            variables = {
                'USERNAME': username, 'username': username,
                'PASSWORD': password, 'password': password,
                'USER_ID': username, 'user_id': username,
                'USER_PW': password, 'user_pw': password,
            }
            data = self._resolve_payload(payload, variables)
        elif fields:
            data = {
                fields.get('username', 'username'): username,
                fields.get('password', 'password'): password,
            }
            for key, val in login_spec.get('extra_fields', {}).items():
                data[key] = val
        else:
            data = {'user_id': username, 'user_pw': password}

        try:
            before_cookies = set(self.session.cookies.keys())
            resp = self._make_request(
                method=login_spec.get('method', 'POST'),
                url=login_url,
                content_type=login_spec.get('content_type',
                              login_spec.get('headers', {}).get('Content-Type',
                              'application/x-www-form-urlencoded')),
                data=data,
                headers=login_spec.get('headers'),
            )

            indicator = login_spec.get('success_indicator', {})
            success = self._check_success(resp, indicator)

            if not success:
                after_cookies = set(self.session.cookies.keys())
                new_cookies = after_cookies - before_cookies
                fail_keywords = ['실패', '올바르지', '일치하지', 'fail', 'error',
                                 '잘못된', '비밀번호를 확인', '아이디를 확인']
                resp_text = resp.text[:2000].lower()
                has_fail = any(kw in resp_text for kw in fail_keywords)

                if new_cookies and not has_fail:
                    logger.info(f"[{self.site_id}] 로그인 성공 (폴백: 새 쿠키 {new_cookies})")
                    success = True
                elif not has_fail and resp.ok:
                    logger.info(f"[{self.site_id}] 로그인 성공 추정 (폴백: 에러 없음)")
                    success = True

            # JWT 토큰 처리
            token_spec = login_spec.get('token')
            if token_spec and success:
                try:
                    resp_json = resp.json()
                    token_path = token_spec.get('path', 'accessToken')
                    token_value = resp_json
                    for key in token_path.split('.'):
                        if key and isinstance(token_value, dict):
                            token_value = token_value.get(key, '')
                    if token_value and isinstance(token_value, str):
                        header_name = token_spec.get('header', 'Authorization')
                        prefix = token_spec.get('prefix', 'Bearer ')
                        self.session.headers[header_name] = f"{prefix}{token_value}"
                        logger.info(f"[{self.site_id}] JWT 토큰 설정 완료")

                    # userData를 _login_data에 저장 (custCd, userId 등)
                    user_data_path = token_spec.get('user_data_path', 'userData')
                    user_data = resp_json
                    for key in user_data_path.split('.'):
                        if key and isinstance(user_data, dict):
                            user_data = user_data.get(key, {})
                    if isinstance(user_data, dict):
                        self._login_data = {k: str(v) for k, v in user_data.items()}
                        logger.info(f"[{self.site_id}] 로그인 데이터 저장: {list(self._login_data.keys())[:5]}...")
                except Exception as e:
                    logger.warning(f"[{self.site_id}] JWT 토큰 추출 실패: {e}")

            self._authenticated = success
            if success:
                logger.info(f"[{self.site_id}] 로그인 성공")
            return success

        except Exception as e:
            logger.error(f"[{self.site_id}] 로그인 에러: {e}")
            self._authenticated = False
            return False

    def is_authenticated(self) -> bool:
        return self._authenticated

    # ─── 검색 ──────────────────────────────────────────

    def search(self, query: str, edi_code: str = None, max_pages: int = 0) -> list[WholesaleProduct]:
        search_spec = self._get_step('search')
        if not search_spec:
            logger.error(f"[{self.site_id}] 검색 레시피 없음")
            return []

        pagination = search_spec.get('pagination')
        if not pagination:
            products, _ = self._search_single_page(query, edi_code, search_spec)
            return products

        page_limit = max_pages if max_pages > 0 else pagination.get('max_pages', 10)
        pag_type = pagination.get('type', '')
        all_products = []
        self._search_html_pages = []

        if pag_type == 'param':
            page_param = pagination.get('page_param', 'page')
            start_page = pagination.get('start_page', 1)
            total_pages = None

            for page_num in range(start_page, start_page + page_limit):
                products, resp_data = self._search_single_page(
                    query, edi_code, search_spec,
                    page_override={page_param: str(page_num)}
                )
                all_products.extend(products)
                if total_pages is None and isinstance(resp_data, dict):
                    total_pages = self._get_total_pages_json(resp_data, pagination)
                if total_pages and page_num >= total_pages:
                    break
                if not products:
                    break

        elif pag_type == 'html_links':
            products, resp_data = self._search_single_page(query, edi_code, search_spec)
            all_products.extend(products)
            if isinstance(resp_data, str):
                self._search_html_pages.append(resp_data)
                total_pages = min(self._get_total_pages_html(resp_data, pagination), page_limit)
                for page_num in range(2, total_pages + 1):
                    products, page_html = self._search_page_by_url(
                        query, edi_code, search_spec, pagination, page_num
                    )
                    all_products.extend(products)
                    if isinstance(page_html, str):
                        self._search_html_pages.append(page_html)
                    if not products:
                        break
        else:
            products, _ = self._search_single_page(query, edi_code, search_spec)
            all_products.extend(products)

        logger.info(f"[{self.site_id}] 검색 결과: {len(all_products)}건 (페이지네이션)")
        return all_products

    def _search_single_page(self, query: str, edi_code: str = None,
                            search_spec: dict = None,
                            page_override: dict = None) -> tuple:
        """1페이지 검색 요청+파싱. (products, raw_response) 반환."""
        search_url = self._get_url(search_spec)
        if not search_url:
            return [], None

        edi_params = search_spec.get('edi_params')
        if edi_code and edi_params:
            params_spec = dict(edi_params)
            variables = {'EDI_CODE': edi_code, 'edi_code': edi_code}
        else:
            search_query = edi_code if edi_code else query
            params_spec = dict(search_spec.get('params', {}))
            variables = {
                'KEYWORD': search_query, 'keyword': search_query,
                'QUERY': search_query, 'query': search_query,
                'SEARCH_WORD': search_query, 'search_word': search_query,
            }
            if edi_code:
                variables.update({'EDI_CODE': edi_code, 'edi_code': edi_code})

        data = self._resolve_payload(params_spec, variables) if params_spec else {}
        if page_override:
            data.update(page_override)
        method = search_spec.get('method', 'GET')

        try:
            if method.upper() == 'GET':
                full_url = self._build_url(search_url)
                self._throttle()
                resp = self.session.get(
                    full_url, params=data,
                    headers=search_spec.get('headers'),
                    timeout=30,
                )
                if self._encoding and self._encoding.lower() != 'utf-8':
                    resp.encoding = self._encoding
                resp.raise_for_status()
            else:
                resp = self._make_request(
                    method=method, url=search_url,
                    content_type=search_spec.get('content_type'),
                    data=data if data else None,
                    headers=search_spec.get('headers'),
                )

            response_type = search_spec.get('response_type', 'html')
            if response_type == 'json':
                json_data = resp.json()
                products = self._parse_json_response(json_data, search_spec.get('json_mapping', {}))
                return products, json_data
            else:
                self._last_search_html = resp.text
                self._last_search_url = resp.url
                products = self._parse_html_response(resp.text, search_spec.get('parsing', {}))
                return products, resp.text

        except Exception as e:
            logger.error(f"[{self.site_id}] 검색 에러: {e}")
            return [], None

    def _get_total_pages_json(self, data: dict, pagination: dict) -> int:
        """JSON 응답에서 총 페이지 수 추출"""
        total_pages_path = pagination.get('total_pages_path', '')
        if total_pages_path:
            value = data
            for key in total_pages_path.split('.'):
                if key and isinstance(value, dict):
                    value = value.get(key, 0)
            try:
                return int(value)
            except (ValueError, TypeError):
                return 1
        return 1

    def _get_total_pages_html(self, html: str, pagination: dict) -> int:
        """HTML 페이지네이션에서 총 페이지 수 추출"""
        from urllib.parse import urlparse, parse_qs
        soup = BeautifulSoup(html, 'html.parser')
        selector = pagination.get('paging_selector', 'div.paging a')
        links = soup.select(selector)
        if not links:
            return 1
        max_page = 1
        page_param = pagination.get('page_url_param', 'Page')
        for link in links:
            href = link.get('href', '')
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            for pv in qs.get(page_param, qs.get(page_param.lower(), [])):
                try:
                    max_page = max(max_page, int(pv))
                except ValueError:
                    pass
        return max_page

    def _search_page_by_url(self, query: str, edi_code: str,
                            search_spec: dict, pagination: dict,
                            page_num: int) -> tuple:
        """HTML 페이지네이션: 특정 페이지 GET 요청"""
        search_url = self._get_url(search_spec)
        page_param = pagination.get('page_url_param', 'Page')
        method = pagination.get('method_override', search_spec.get('method', 'GET'))

        search_query = edi_code if edi_code else query
        params_spec = dict(search_spec.get('params', {}))
        variables = {
            'KEYWORD': search_query, 'keyword': search_query,
            'QUERY': search_query, 'query': search_query,
            'SEARCH_WORD': search_query, 'search_word': search_query,
        }
        data = self._resolve_payload(params_spec, variables) if params_spec else {}
        data[page_param] = str(page_num)

        try:
            full_url = self._build_url(search_url)
            self._throttle()
            if method.upper() == 'GET':
                resp = self.session.get(
                    full_url, params=data,
                    headers=search_spec.get('headers'),
                    timeout=30,
                )
            else:
                resp = self.session.post(
                    full_url, data=data,
                    headers=search_spec.get('headers'),
                    timeout=30,
                )
            if self._encoding and self._encoding.lower() != 'utf-8':
                resp.encoding = self._encoding
            resp.raise_for_status()

            self._last_search_html = resp.text
            self._last_search_url = resp.url
            products = self._parse_html_response(resp.text, search_spec.get('parsing', {}))
            return products, resp.text
        except Exception as e:
            logger.error(f"[{self.site_id}] 페이지 {page_num} 검색 에러: {e}")
            return [], None

    def _parse_html_response(self, html: str, parsing_spec: dict) -> list[WholesaleProduct]:
        soup = BeautifulSoup(html, 'html.parser')
        item_selector = parsing_spec.get('selector', 'tr')
        fields = parsing_spec.get('fields', {})
        items = soup.select(item_selector)
        products = []

        for item in items:
            product_data = {}
            for field_name, field_spec in fields.items():
                product_data[field_name] = self._extract_field(item, field_spec)

            name = product_data.get('product_name') or product_data.get('name', '')
            if name:
                stock_raw = product_data.get('stock_quantity') or product_data.get('stock', '')
                stock_qty = self._parse_stock_quantity(stock_raw)
                remark = product_data.get('remark', '').strip()
                if remark == 'Y':
                    remark = '품절'
                elif remark in ('N', '-', ''):
                    remark = ''
                soldout = remark == '품절' or stock_raw in ('품절', '재고없음')
                stock_avail = stock_qty != 0 and not soldout

                products.append(WholesaleProduct(
                    site_id=self.site_id,
                    product_code=product_data.get('product_code') or product_data.get('product_id', ''),
                    product_name=name,
                    edi_code=product_data.get('edi_code', ''),
                    manufacturer=product_data.get('manufacturer') or product_data.get('maker', ''),
                    unit_price=self._parse_price(product_data.get('price') or product_data.get('unit_price', '0')),
                    stock_available=stock_avail,
                    stock_quantity=stock_qty,
                    pack_unit=product_data.get('pack_unit') or product_data.get('standard', ''),
                    pack_units=self._parse_pack_units(product_data.get('pack_unit') or product_data.get('standard', '')),
                    box_quantity=self._parse_int(product_data.get('box_quantity', '0')),
                    insurance_type=product_data.get('insurance_type', ''),
                    product_type=product_data.get('product_type', ''),
                    discount=self._parse_price(product_data.get('discount', '0')),
                    remark=remark,
                    raw_data=product_data,
                ))

        logger.info(f"[{self.site_id}] 검색 결과: {len(products)}건")
        return products

    def _parse_json_response(self, data: dict, mapping: dict) -> list[WholesaleProduct]:
        items_path = mapping.get('items_path', '')
        fields = mapping.get('fields', {})
        items = data
        for key in items_path.split('.'):
            if key and isinstance(items, dict):
                items = items.get(key, [])
        if not isinstance(items, list):
            return []

        products = []
        for item in items:
            stock_field = fields.get('stock_quantity', fields.get('stock_status', ''))
            stock_raw = item.get(stock_field, -1) if stock_field else -1
            stock_qty = self._parse_stock_quantity(str(stock_raw))
            remark_field = fields.get('remark', '')
            remark = str(item.get(remark_field, '')) if remark_field else ''
            soldout = stock_qty == 0 or remark in ('품절', '재고없음')
            stock_avail = stock_qty != 0 and not soldout
            pack_unit_str = str(item.get(fields.get('pack_unit', ''), ''))

            products.append(WholesaleProduct(
                site_id=self.site_id,
                product_code=str(item.get(fields.get('product_code', ''), '')),
                product_name=str(item.get(fields.get('product_name', ''), '')),
                edi_code=str(item.get(fields.get('edi_code', ''), '')),
                manufacturer=str(item.get(fields.get('manufacturer', ''), '')),
                unit_price=float(item.get(fields.get('unit_price', ''), 0) or 0),
                stock_available=stock_avail,
                stock_quantity=stock_qty,
                pack_unit=pack_unit_str,
                pack_units=self._parse_pack_units(pack_unit_str),
                box_quantity=self._parse_int(str(item.get(fields.get('box_quantity', ''), 0))),
                insurance_type=str(item.get(fields.get('insurance_type', ''), '')),
                product_type=str(item.get(fields.get('product_type', ''), '')),
                remark=remark,
                raw_data=item,
            ))

        logger.info(f"[{self.site_id}] JSON 검색 결과: {len(products)}건")
        return products

    def _extract_field(self, element, field_spec) -> str:
        if isinstance(field_spec, dict):
            selector = field_spec.get('selector', '')
            attr = field_spec.get('attribute', 'text')

            if field_spec.get('join') and selector:
                els = element.select(selector)
                if not els:
                    return ''
                values = []
                for el in els:
                    if attr == 'text':
                        values.append(el.get_text(strip=True))
                    elif attr == 'value':
                        values.append(el.get('value', ''))
                    else:
                        values.append(el.get(attr, ''))
                value = ' '.join(v for v in values if v)
                regex = field_spec.get('regex')
                if regex and value:
                    match = re.search(regex, value)
                    value = match.group(0) if match else value
                return value

            el = element.select_one(selector) if selector else element
            if el is None:
                return ''

            if attr == 'text' or field_spec.get('text'):
                value = el.get_text(strip=True)
            elif attr == 'value':
                value = el.get('value', '')
            else:
                value = el.get(attr, '') or el.get_text(strip=True)

            regex = field_spec.get('regex')
            if regex and value:
                match = re.search(regex, value)
                value = match.group(0) if match else value
            return value

        if isinstance(field_spec, str):
            return self._extract_field_str(element, field_spec)
        return ''

    def _extract_field_str(self, element, selector: str) -> str:
        regex_pattern = None
        if '|regex(' in selector:
            selector, regex_part = selector.rsplit('|regex(', 1)
            regex_pattern = regex_part.rstrip(')')

        pseudo = None
        if '::' in selector:
            selector, pseudo = selector.rsplit('::', 1)

        selector = selector.strip()
        el = element.select_one(selector) if selector else element
        if el is None:
            return ''

        if pseudo and pseudo == 'text':
            value = el.get_text(strip=True)
        elif pseudo and pseudo.startswith('attr('):
            attr_name = pseudo[5:-1]
            value = el.get(attr_name, '')
        else:
            value = el.get_text(strip=True)

        if regex_pattern and value:
            match = re.search(regex_pattern, value)
            value = match.group(0) if match else value
        return value

    def _parse_price(self, price_str: str) -> float:
        try:
            cleaned = re.sub(r'[^\d.]', '', str(price_str))
            return float(cleaned) if cleaned else 0.0
        except ValueError:
            return 0.0

    def _parse_stock_quantity(self, stock_raw: str) -> int:
        if not stock_raw or stock_raw == '-1':
            return -1
        s = str(stock_raw).strip()
        if s in ('품절', '재고없음', 'N', ''):
            return 0
        try:
            cleaned = re.sub(r'[^\d.]', '', s)
            return int(float(cleaned)) if cleaned else -1
        except (ValueError, TypeError):
            return -1

    def _parse_int(self, value: str) -> int:
        try:
            cleaned = re.sub(r'[^\d]', '', str(value))
            return int(cleaned) if cleaned else 0
        except (ValueError, TypeError):
            return 0

    def _parse_pack_units(self, pack_str: str) -> list[int]:
        utils = self.recipe.get('utils', {})
        regex = (self.recipe.get('pack_unit_parsing', {}).get('regex')
                 or utils.get('pack_unit_regex')
                 or r'(\d+)\s*(T|C|EA|정|캡슐|포|ml|g|vial|병|tab|cap)')
        matches = re.findall(regex, str(pack_str), re.IGNORECASE)
        units = sorted([int(m[0]) for m in matches], reverse=True)
        return units if units else []

    # ─── 장바구니 조회/삭제 ──────────────────────────────────

    def view_cart(self) -> list[CartItem]:
        """장바구니 내역 조회"""
        cart_view = self._get_step('cart_view')
        if not cart_view:
            logger.warning(f"[{self.site_id}] cart_view 레시피 없음")
            return []

        url = self._get_url(cart_view)
        method = cart_view.get('method', 'GET')
        params = cart_view.get('params', {})
        data = self._resolve_payload(params, {}) if params else None

        try:
            if method.upper() == 'GET':
                full_url = self._build_url(url)
                self._throttle()
                resp = self.session.get(full_url, params=data, timeout=30)
                if self._encoding and self._encoding.lower() != 'utf-8':
                    resp.encoding = self._encoding
                resp.raise_for_status()
            else:
                resp = self._make_request(method=method, url=url,
                                         content_type=cart_view.get('content_type'), data=data)

            response_type = cart_view.get('response_type', 'html')
            if response_type == 'json':
                return self._parse_cart_json(resp.json(), cart_view.get('json_mapping', {}))
            else:
                return self._parse_cart_html(resp.text, cart_view.get('parsing', {}))
        except Exception as e:
            logger.error(f"[{self.site_id}] 장바구니 조회 에러: {e}")
            return []

    def _parse_cart_json(self, data, mapping: dict) -> list[CartItem]:
        """JSON 장바구니 응답 파싱"""
        items_path = mapping.get('items_path', '')
        fields = mapping.get('fields', {})
        items = data
        for key in items_path.split('.'):
            if key and isinstance(items, dict):
                items = items.get(key, [])
        if not isinstance(items, list):
            return []

        cart_items = []
        for item in items:
            cart_items.append(CartItem(
                site_id=self.site_id,
                product_code=str(item.get(fields.get('product_code', ''), '')),
                product_name=str(item.get(fields.get('product_name', ''), '')),
                quantity=int(item.get(fields.get('quantity', ''), 0) or 0),
                unit_price=float(item.get(fields.get('unit_price', ''), 0) or 0),
                total_price=float(item.get(fields.get('total_price', ''), 0) or 0),
                raw_data=item,
            ))
        return cart_items

    def _parse_cart_html(self, html: str, parsing_spec: dict) -> list[CartItem]:
        """HTML 장바구니 응답 파싱"""
        soup = BeautifulSoup(html, 'html.parser')
        selector = parsing_spec.get('selector', 'tr')
        fields = parsing_spec.get('fields', {})
        rows = soup.select(selector)
        cart_items = []

        for row in rows:
            name = self._extract_field(row, fields.get('product_name', ''))
            if not name:
                continue
            # 추가 필드를 raw_data에 저장 (bag_num, stock_cd 등)
            raw = {}
            for field_name, field_spec in fields.items():
                if field_name not in ('product_name', 'product_code', 'quantity', 'unit_price', 'total_price'):
                    raw[field_name] = self._extract_field(row, field_spec)
            cart_items.append(CartItem(
                site_id=self.site_id,
                product_code=self._extract_field(row, fields.get('product_code', '')),
                product_name=name,
                quantity=self._parse_int(self._extract_field(row, fields.get('quantity', ''))),
                unit_price=self._parse_price(self._extract_field(row, fields.get('unit_price', ''))),
                total_price=self._parse_price(self._extract_field(row, fields.get('total_price', ''))),
                raw_data=raw,
            ))
        return cart_items

    def delete_from_cart(self, product_code: str) -> bool:
        """장바구니에서 특정 상품 삭제"""
        cart_delete = self._get_step('cart_delete')
        if not cart_delete:
            logger.warning(f"[{self.site_id}] cart_delete 레시피 없음")
            return False

        url = self._get_url(cart_delete)
        method = cart_delete.get('method', 'POST')
        variables = {'PRODUCT_CODE': product_code, 'product_code': product_code}

        # requires_cart_view: cart_view에서 추가 정보를 가져와서 orderNum 등을 구성
        if cart_delete.get('requires_cart_view'):
            items = self.view_cart()
            target = next((item for item in items if item.product_code == product_code), None)
            if not target:
                logger.warning(f"[{self.site_id}] 장바구니에 {product_code} 없음")
                return False
            raw = target.raw_data
            variables.update({
                'BAG_NUM': str(raw.get('bag_num', '')),
                'STOCK_CD': str(raw.get('stock_cd', '')),
                'QUANTITY': str(target.quantity),
            })
            # orderNum 포맷 구성
            fmt = cart_delete.get('order_num_format', '')
            if fmt:
                order_num = fmt
                for k, v in variables.items():
                    order_num = order_num.replace(f'{{{k}}}', v)
                variables['ORDER_NUM'] = order_num

        try:
            if method.upper() in ('GET', 'DELETE'):
                params = cart_delete.get('params', {})
                data = self._resolve_payload(params, variables) if params else None
                full_url = self._build_url(url)
                self._throttle()
                resp = self.session.request(method.upper(), full_url, params=data, timeout=30)
                if self._encoding and self._encoding.lower() != 'utf-8':
                    resp.encoding = self._encoding
                resp.raise_for_status()
            else:
                payload = cart_delete.get('payload', {})
                data = self._resolve_payload(payload, variables)
                resp = self._make_request(method=method, url=url,
                                         content_type=cart_delete.get('content_type'), data=data)
            return self._check_success(resp, cart_delete.get('success_indicator', {}))
        except Exception as e:
            logger.error(f"[{self.site_id}] 장바구니 삭제 에러: {e}")
            return False

    def clear_cart(self) -> bool:
        """장바구니 전체 비우기"""
        cart_clear = self._get_step('cart_clear')
        if not cart_clear:
            # 폴백: cart_view → delete_from_cart 반복
            if self._get_step('cart_view') and self._get_step('cart_delete'):
                items = self.view_cart()
                if not items:
                    return True
                ok = True
                for item in items:
                    if item.product_code:
                        ok = self.delete_from_cart(item.product_code) and ok
                return ok
            logger.warning(f"[{self.site_id}] cart_clear 레시피 없음")
            return False

        url = self._get_url(cart_clear)
        method = cart_clear.get('method', 'GET')
        raw_params = cart_clear.get('params', cart_clear.get('payload', {}))
        data = self._resolve_payload(raw_params, {}) if raw_params else None

        try:
            if method.upper() == 'GET':
                full_url = self._build_url(url)
                self._throttle()
                resp = self.session.get(full_url, params=data, timeout=30)
                resp.raise_for_status()
            else:
                resp = self._make_request(method=method, url=url,
                                         content_type=cart_clear.get('content_type'), data=data)
            return self._check_success(resp, cart_clear.get('success_indicator', {}))
        except Exception as e:
            logger.error(f"[{self.site_id}] 장바구니 비우기 에러: {e}")
            return False

    # ─── 매출원장 ──────────────────────────────────────────

    @staticmethod
    def _resolve_period(start_date: str, end_date: str, period: str) -> tuple[str, str]:
        """날짜 기간 계산. period: '1w','1m','3m','6m','1y' 등"""
        today = date.today()

        if not end_date:
            ed = today
        else:
            ed = date.fromisoformat(end_date.replace("/", "-"))

        if start_date:
            return start_date, end_date or ed.strftime("%Y-%m-%d")

        # period 파싱
        amount = int(period[:-1]) if period[:-1].isdigit() else 3
        unit = period[-1].lower()
        if unit == 'w':
            sd = ed - timedelta(weeks=amount)
        elif unit == 'm':
            m = ed.month - amount
            y = ed.year
            while m < 1:
                m += 12
                y -= 1
            sd = ed.replace(year=y, month=m, day=min(ed.day, 28))
        elif unit == 'y':
            sd = ed.replace(year=ed.year - amount)
        else:
            sd = ed - timedelta(days=90)

        return sd.strftime("%Y-%m-%d"), ed.strftime("%Y-%m-%d")

    def get_sales_ledger(self, start_date: str = "", end_date: str = "",
                         period: str = "3m", detail_mode: str = "0",
                         product_filter: str = "") -> list[SalesLedgerEntry]:
        """매출원장 조회.

        Args:
            start_date: 시작일 (YYYY-MM-DD 또는 YYYYMMDD). 비어있으면 period로 계산.
            end_date: 종료일. 비어있으면 오늘.
            period: 상대 기간. '1w','1m','3m','6m','1y'. start_date 없을 때 사용.
            detail_mode: '0'=약품별 상세, '1'=일자별 요약.
            product_filter: 약품명 검색 필터.
        """
        ledger_spec = self._get_step('sales_ledger')
        if not ledger_spec:
            logger.error(f"[{self.site_id}] 매출원장 레시피 없음")
            return []

        ledger_url = self._get_url(ledger_spec)
        if not ledger_url:
            logger.error(f"[{self.site_id}] 매출원장 URL 누락")
            return []

        # 날짜 계산
        s_date, e_date = self._resolve_period(start_date, end_date, period)

        # 날짜 포맷 변환 (레시피에 date_format이 있으면 적용)
        date_format = ledger_spec.get('date_format', '')
        if date_format:
            from datetime import datetime as dt
            try:
                sd = dt.strptime(s_date, "%Y-%m-%d")
                ed = dt.strptime(e_date, "%Y-%m-%d")
                s_date = sd.strftime(date_format)
                e_date = ed.strftime(date_format)
            except ValueError:
                pass

        variables = {
            'START_DATE': s_date, 'start_date': s_date,
            'END_DATE': e_date, 'end_date': e_date,
            'DETAIL_MODE': detail_mode, 'detail_mode': detail_mode,
            'PRODUCT_FILTER': product_filter, 'product_filter': product_filter,
            'PRODUCT_NAME_FILTER': product_filter,
        }

        params_spec = ledger_spec.get('params', {})
        data = self._resolve_payload(params_spec, variables) if params_spec else {}
        method = ledger_spec.get('method', 'GET')

        try:
            if method.upper() == 'GET':
                full_url = self._build_url(ledger_url)
                self._throttle()
                resp = self.session.get(
                    full_url, params=data,
                    headers=ledger_spec.get('headers'),
                    timeout=60,
                )
                if self._encoding and self._encoding.lower() != 'utf-8':
                    resp.encoding = self._encoding
                resp.raise_for_status()
            else:
                resp = self._make_request(
                    method=method, url=ledger_url,
                    content_type=ledger_spec.get('content_type'),
                    data=data if data else None,
                    headers=ledger_spec.get('headers'),
                    timeout=60,
                )

            response_type = ledger_spec.get('response_type', 'html')
            if response_type == 'json':
                return self._parse_ledger_json(resp.json(), ledger_spec.get('json_mapping', {}))
            else:
                return self._parse_ledger_html(resp.text, ledger_spec.get('parsing', {}))

        except Exception as e:
            logger.error(f"[{self.site_id}] 매출원장 에러: {e}")
            return []

    def _parse_ledger_json(self, data, mapping: dict) -> list[SalesLedgerEntry]:
        """매출원장 JSON 응답 파싱"""
        items_path = mapping.get('items_path', 'list')
        fields = mapping.get('fields', {})
        items = data
        for key in items_path.split('.'):
            if key and isinstance(items, dict):
                items = items.get(key, [])
        if not isinstance(items, list):
            return []

        entries = []
        for item in items:
            name = str(item.get(fields.get('product_name', 'ITEM_NM_UNIT'), ''))
            if not name:
                continue
            entries.append(SalesLedgerEntry(
                site_id=self.site_id,
                transaction_date=str(item.get(fields.get('transaction_date', 'APRV_DT'), '')),
                product_name=name,
                pack_unit=str(item.get(fields.get('pack_unit', ''), '')),
                quantity=self._parse_int(str(item.get(fields.get('quantity', 'ITEM_CNT_TXT'), '0'))),
                unit_price=0.0,
                sales_amount=self._parse_price(str(item.get(fields.get('sales_amount', 'TOTAL_AMT'), '0'))),
                payment=self._parse_price(str(item.get(fields.get('payment', 'PAY_AMT'), '0'))),
                balance=self._parse_price(str(item.get(fields.get('balance', 'BALANCE_A_AMT'), '0'))),
                raw_data=item,
            ))

        logger.info(f"[{self.site_id}] 매출원장(JSON): {len(entries)}건")
        return entries

    def _parse_ledger_html(self, html: str, parsing_spec: dict) -> list[SalesLedgerEntry]:
        """매출원장 HTML 파싱.

        많은 도매 사이트의 매출원장 HTML은 <tr> 태그가 깨져 있어
        BeautifulSoup이 정상 파싱하지 못합니다. 정규식으로 행 단위 추출 후
        각 행을 개별 파싱합니다.
        """
        # 깨진 HTML 대응: <tr 단위로 분리
        row_pattern = re.compile(
            r'<tr\s+align="right"[^>]*>(.*?)(?=<tr[\s>]|</tbody>|</table>)',
            re.DOTALL | re.IGNORECASE
        )
        rows = row_pattern.findall(html)

        if not rows:
            # fallback: BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')
            item_selector = parsing_spec.get('selector', 'tr')
            fields = parsing_spec.get('fields', {})
            items = soup.select(item_selector)
            entries = []
            for item in items:
                raw = {}
                for field_name, field_spec in fields.items():
                    raw[field_name] = self._extract_field(item, field_spec)
                name = (raw.get('product_name') or '').strip()
                if not name or name.startswith('['):
                    continue
                entries.append(SalesLedgerEntry(
                    site_id=self.site_id,
                    transaction_date=(raw.get('transaction_date') or '').strip(),
                    product_name=name,
                    pack_unit=(raw.get('pack_unit') or '').strip(),
                    quantity=self._parse_int(raw.get('quantity', '0')),
                    unit_price=self._parse_price(raw.get('unit_price', '0')),
                    sales_amount=self._parse_price(raw.get('sales_amount', '0')),
                    financial_discount=self._parse_price(raw.get('financial_discount', '0')),
                    payment=self._parse_price(raw.get('payment', '0')),
                    balance=self._parse_price(raw.get('balance', '0')),
                    raw_data=raw,
                ))
            logger.info(f"[{self.site_id}] 매출원장(fallback): {len(entries)}건")
            return entries

        # 정규식 기반 파싱
        td_pattern = re.compile(r'<td[^>]*>(.*?)</td>', re.DOTALL | re.IGNORECASE)
        td_nm_pattern = re.compile(r'<td[^>]*class="td_nm"[^>]*>(.*?)</td>', re.DOTALL | re.IGNORECASE)

        entries = []
        last_date = ''

        for row_html in rows:
            # 제품명 추출 (td.td_nm)
            nm_match = td_nm_pattern.search(row_html)
            if not nm_match:
                continue
            product_name = re.sub(r'<[^>]+>', '', nm_match.group(1)).strip()
            product_name = product_name.replace('&nbsp;', '').strip()
            if not product_name or '거래명세서' in product_name or product_name.startswith('['):
                continue

            # 모든 td 추출
            tds = td_pattern.findall(row_html)
            tds_clean = [re.sub(r'<[^>]+>', '', td).strip() for td in tds]

            # 날짜 (첫 번째 또는 두 번째 td)
            row_date = ''
            for td in tds_clean[:2]:
                if re.match(r'\d{4}/\d{2}/\d{2}', td):
                    row_date = td
                    break
            if row_date:
                last_date = row_date
            else:
                row_date = last_date

            # td.td_num 값들 추출 (수량, 단가, 매출 순)
            # 닫는 태그가 없는 경우도 대응: <td class="td_num">5 <td ... 또는 <td ...>5</td>
            td_num_pattern = re.compile(
                r'<td\s+class="td_num"[^>]*>(.*?)(?=<td|</tr>|$)',
                re.DOTALL | re.IGNORECASE
            )
            nums = td_num_pattern.findall(row_html)
            nums_clean = [re.sub(r'<[^>]+>', '', n).strip().replace(',', '') for n in nums]
            # 빈 문자열 제거하지 않음 (위치 중요)

            # 규격 (td_nm 다음 td)
            pack_unit = ''
            nm_end = nm_match.end()
            after_nm = row_html[nm_end:]
            next_td = td_pattern.search(after_nm)
            if next_td:
                pack_unit = re.sub(r'<[^>]+>', '', next_td.group(1)).replace('&nbsp;', '').strip()

            quantity = self._parse_int(nums_clean[0]) if len(nums_clean) > 0 else 0
            unit_price = self._parse_price(nums_clean[1]) if len(nums_clean) > 1 else 0.0
            sales_amount = self._parse_price(nums_clean[2]) if len(nums_clean) > 2 else 0.0

            entries.append(SalesLedgerEntry(
                site_id=self.site_id,
                transaction_date=row_date,
                product_name=product_name,
                pack_unit=pack_unit,
                quantity=quantity,
                unit_price=unit_price,
                sales_amount=sales_amount,
            ))

        logger.info(f"[{self.site_id}] 매출원장: {len(entries)}건")
        return entries

    # ─── 장바구니 / 주문 ──────────────────────────────────

    def add_to_cart(self, product_code: str, quantity: int) -> bool:
        cart_spec = self._get_step('cart_add')
        if not cart_spec:
            return self._add_to_cart_form(product_code, quantity)

        if cart_spec.get('type') == 'form':
            return self._add_to_cart_form(
                product_code, quantity,
                form_name=cart_spec.get('form_name'),
                pc_prefix=cart_spec.get('product_code_prefix', 'pc_'),
                qty_prefix=cart_spec.get('quantity_prefix', 'qty_'),
            )

        cart_url = self._get_url(cart_spec)
        if not cart_url:
            logger.warning(f"[{self.site_id}] 장바구니 URL 누락")
            return False

        payload = cart_spec.get('payload', {})
        params = cart_spec.get('params', {})

        if payload:
            variables = {
                'PRODUCT_CODE': product_code, 'product_code': product_code,
                'QUANTITY': str(quantity), 'quantity': str(quantity),
            }
            data = self._resolve_payload(payload, variables)
        else:
            data = {
                params.get('product_code', 'product_code'): product_code,
                params.get('quantity', 'quantity'): quantity,
            }

        try:
            resp = self._make_request(
                method=cart_spec.get('method', 'POST'),
                url=cart_url,
                content_type=cart_spec.get('content_type',
                              cart_spec.get('headers', {}).get('Content-Type',
                              'application/x-www-form-urlencoded')),
                data=data,
                headers=cart_spec.get('headers'),
            )
            success = self._check_success(resp, cart_spec.get('success_indicator', {}))
            if success:
                logger.info(f"[{self.site_id}] 장바구니 추가: {product_code} x{quantity}")
            return success
        except Exception as e:
            logger.error(f"[{self.site_id}] 장바구니 추가 에러: {e}")
            return False

    def _add_to_cart_form(self, product_code: str, quantity: int,
                          form_name: str | None = None,
                          pc_prefix: str = 'pc_',
                          qty_prefix: str = 'qty_') -> bool:
        # 페이지네이션된 검색 결과가 있으면 모든 페이지에서 상품 검색
        pages = getattr(self, '_search_html_pages', [])
        if not pages:
            html = getattr(self, '_last_search_html', '')
            if html:
                pages = [html]
        if not pages:
            logger.warning(f"[{self.site_id}] 장바구니: 검색 HTML 없음 (먼저 검색 필요)")
            return False

        # 모든 페이지에서 상품코드를 찾을 때까지 순회
        for page_html in pages:
            soup = BeautifulSoup(page_html, 'html.parser')
            if form_name:
                form = soup.find('form', attrs={'name': form_name})
            else:
                form = (soup.find('form', attrs={'name': 'frmOrder'})
                        or soup.find('form', attrs={'action': re.compile(r'(?i)(bag|cart)')})
                        )
            if not form:
                continue
            # 이 페이지에 해당 상품코드가 있는지 확인
            found = False
            for inp in form.find_all('input'):
                if inp.get('name', '').startswith(pc_prefix) and inp.get('value') == product_code:
                    found = True
                    break
            if found:
                # 이 페이지의 HTML로 장바구니 처리
                self._last_search_html = page_html
                break
        else:
            logger.warning(f"[{self.site_id}] 장바구니: product_code={product_code} 모든 페이지에서 없음")
            return False

        html = self._last_search_html
        soup = BeautifulSoup(html, 'html.parser')
        if form_name:
            form = soup.find('form', attrs={'name': form_name})
        else:
            form = (soup.find('form', attrs={'name': 'frmOrder'})
                    or soup.find('form', attrs={'action': re.compile(r'(?i)(bag|cart)')})
                    )
        if not form:
            logger.warning(f"[{self.site_id}] 장바구니: 주문 폼을 찾을 수 없음")
            return False

        action = form.get('action', '')
        if not action.startswith('http'):
            action = self._build_url(action, base_url=self._last_search_url)

        data = {}
        target_index = None
        for inp in form.find_all('input'):
            name = inp.get('name', '')
            if not name:
                continue
            inp_type = inp.get('type', 'text').lower()
            if inp_type == 'image':
                continue
            value = inp.get('value', '')
            if name.startswith(pc_prefix) and value == product_code:
                target_index = name[len(pc_prefix):]
                logger.debug(f"[{self.site_id}] 장바구니: 상품 찾음 - {name} = {value}")
            data[name] = value

        if target_index is None:
            logger.warning(f"[{self.site_id}] 장바구니: product_code={product_code} 폼에 없음")
            # 디버깅: 폼에 있는 모든 product_code 출력
            pc_fields = [f"{inp.get('name')}={inp.get('value')}" for inp in form.find_all('input')
                        if inp.get('name', '').startswith(pc_prefix)]
            logger.warning(f"[{self.site_id}] 폼의 {pc_prefix} 필드: {pc_fields[:5]}")
            return False

        for key in list(data.keys()):
            if key.startswith(qty_prefix):
                idx = key[len(qty_prefix):]
                data[key] = str(quantity) if idx == target_index else ''

        try:
            self._throttle()
            resp = self.session.post(
                action, data=data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=30,
            )
            resp_text = resp.text[:1000]
            ok = resp.status_code == 200 and '오류' not in resp_text
            if ok:
                logger.info(f"[{self.site_id}] 폼 장바구니 추가: {product_code} x{quantity}")
            return ok
        except Exception as e:
            logger.error(f"[{self.site_id}] 폼 장바구니 에러: {e}")
            return False

    def submit_order(self, delivery_date: str = None) -> OrderResult:
        order_spec = self._get_step('order_submit')
        if not order_spec:
            return OrderResult(success=False, message="주문 레시피 없음")

        order_url = self._get_url(order_spec)
        if not order_url:
            return OrderResult(success=False, message="주문 URL 누락")

        payload = order_spec.get('payload', {})
        params = order_spec.get('params', {})

        if payload:
            variables = {}
            if delivery_date:
                variables['DELIVERY_DATE'] = delivery_date
            data = self._resolve_payload(payload, variables) if variables else dict(payload)
        else:
            data = {}
            if delivery_date and params.get('delivery_date'):
                data[params['delivery_date']] = delivery_date

        try:
            resp = self._make_request(
                method=order_spec.get('method', 'POST'),
                url=order_url,
                content_type=order_spec.get('content_type',
                              order_spec.get('headers', {}).get('Content-Type',
                              'application/x-www-form-urlencoded')),
                data=data if data else None,
                headers=order_spec.get('headers'),
            )
            success = self._check_success(resp, order_spec.get('success_indicator', {}))

            order_id = ''
            resp_mapping = order_spec.get('order_response_mapping', {})
            if success and resp_mapping:
                try:
                    resp_data = resp.json()
                    id_path = resp_mapping.get('order_id_path', '')
                    for key in id_path.split('.'):
                        if key and isinstance(resp_data, dict):
                            resp_data = resp_data.get(key, '')
                    order_id = str(resp_data)
                except Exception:
                    pass

            return OrderResult(
                success=success,
                order_id=order_id,
                message="주문 완료" if success else "주문 실패",
            )
        except Exception as e:
            logger.error(f"[{self.site_id}] 주문 에러: {e}")
            return OrderResult(success=False, message=str(e))

    def logout(self):
        self.session.close()
        self._authenticated = False
        logger.info(f"[{self.site_id}] 로그아웃")
