"""
브라우저 엔진 모듈

Playwright 브라우저 + 네트워크 캡처를 재사용 가능한 클래스로 제공.
server.py (MCP 도구)와 recipe_explorer.py (탐색기) 양쪽에서 사용.
"""

import asyncio
import json
import logging
from collections import deque
from typing import Optional, Callable, Any

logger = logging.getLogger("browser-engine")


# JS: 모든 인터랙티브 요소 추출 (라이브 DOM 기반)
SNAPSHOT_JS = """() => {
    function getSelector(el) {
        if (el.id) return '#' + el.id;
        if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
        let path = [];
        while (el && el.nodeType === 1) {
            let selector = el.tagName.toLowerCase();
            if (el.id) { path.unshift('#' + el.id); break; }
            let sib = el, nth = 1;
            while (sib = sib.previousElementSibling) {
                if (sib.tagName === el.tagName) nth++;
            }
            if (nth > 1) selector += ':nth-of-type(' + nth + ')';
            path.unshift(selector);
            el = el.parentElement;
        }
        return path.join(' > ');
    }

    function isVisible(el) {
        if (!el.offsetParent && el.tagName !== 'BODY' && el.tagName !== 'HTML') return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }

    const result = {
        url: location.href,
        title: document.title,
        buttons: [],
        links: [],
        inputs: [],
        forms: [],
        selects: [],
        iframes: []
    };

    document.querySelectorAll('button, input[type="submit"], input[type="button"], input[type="image"]').forEach(el => {
        result.buttons.push({
            text: (el.textContent || el.value || el.alt || el.title || '').trim().substring(0, 50),
            selector: getSelector(el),
            type: el.type || 'button',
            visible: isVisible(el)
        });
    });

    document.querySelectorAll('a[href]').forEach(el => {
        const href = el.getAttribute('href') || '';
        if (href === '#' || href.startsWith('javascript:void')) return;
        const text = (el.textContent || el.title || '').trim().substring(0, 50);
        if (!text) return;
        result.links.push({
            text: text,
            href: href.substring(0, 200),
            selector: getSelector(el),
            visible: isVisible(el)
        });
    });

    document.querySelectorAll('input:not([type="hidden"]), textarea').forEach(el => {
        result.inputs.push({
            name: el.name || '',
            type: el.type || 'text',
            placeholder: el.placeholder || '',
            value: el.value || '',
            selector: getSelector(el),
            visible: isVisible(el)
        });
    });

    document.querySelectorAll('select').forEach(el => {
        const options = Array.from(el.options).map(o => o.text.trim()).slice(0, 10);
        result.selects.push({
            name: el.name || '',
            options: options,
            selector: getSelector(el),
            visible: isVisible(el)
        });
    });

    document.querySelectorAll('form').forEach(el => {
        const hiddens = el.querySelectorAll('input[type="hidden"]');
        result.forms.push({
            name: el.name || '',
            id: el.id || '',
            action: el.action || '',
            method: (el.method || 'get').toUpperCase(),
            hidden_input_count: hiddens.length,
            hidden_names: Array.from(hiddens).map(h => h.name).filter(n => n).slice(0, 20)
        });
    });

    document.querySelectorAll('iframe').forEach(el => {
        result.iframes.push({
            id: el.id || '',
            name: el.name || '',
            src: (el.src || '').substring(0, 200)
        });
    });

    return result;
}"""


class BrowserEngine:
    """Playwright 브라우저 + 네트워크 캡처 엔진"""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None
        self._MAX_NETWORK_LOG = 500
        self.network_log: deque[dict] = deque(maxlen=self._MAX_NETWORK_LOG)

    @staticmethod
    def _install_chromium():
        """Playwright 내장 드라이버로 Chromium 설치. frozen .exe에서도 작동."""
        import subprocess
        from playwright._impl._driver import compute_driver_executable
        node, cli_js = compute_driver_executable()
        subprocess.run([node, cli_js, "install", "chromium"], check=True, timeout=180)

    @property
    def page(self):
        return self._page

    async def ensure_browser(self):
        """브라우저 싱글톤 초기화 (lazy init). Chromium 없으면 자동 설치."""
        if self._page and not self._page.is_closed():
            return self._page

        from playwright.async_api import async_playwright

        if not self._playwright:
            self._playwright = await async_playwright().start()
        if not self._browser or not self._browser.is_connected():
            try:
                self._browser = await self._playwright.chromium.launch(headless=False)
            except Exception as e:
                if "Executable doesn't exist" in str(e) or "browserType.launch" in str(e):
                    logger.info("Chromium 미설치 → 자동 설치 중 (최초 1회)...")
                    self._install_chromium()
                    self._browser = await self._playwright.chromium.launch(headless=False)
                else:
                    raise

        ctx = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=1,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        self._page = await ctx.new_page()
        self.network_log.clear()

        # confirm/alert/prompt 다이얼로그 자동 수락 (반복 방지)
        self._dialog_count = 0
        self._last_dialog_msg = ""
        async def _on_dialog(dialog):
            msg = dialog.message
            if msg == self._last_dialog_msg:
                self._dialog_count += 1
            else:
                self._dialog_count = 1
                self._last_dialog_msg = msg
            # 같은 메시지가 2번 이상 반복되면 dismiss (무한 루프 방지)
            if self._dialog_count >= 2:
                logger.warning(f"다이얼로그 반복 감지 ({self._dialog_count}회): {msg[:50]} → dismiss")
                await dialog.dismiss()
            else:
                await dialog.accept()
        self._page.on("dialog", _on_dialog)

        # 네트워크 캡처 핸들러
        async def _on_response(response):
            try:
                url = response.url
                if url.startswith("data:") or any(url.endswith(ext) for ext in
                        (".css", ".js", ".png", ".jpg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".svg")):
                    return
                entry = {
                    "method": response.request.method,
                    "url": url,
                    "status": response.status,
                    "content_type": response.headers.get("content-type", ""),
                }
                if response.request.method == "POST":
                    entry["post_data"] = response.request.post_data
                ct = entry["content_type"]
                if "json" in ct:
                    try:
                        body = await response.text()
                        entry["body_preview"] = body[:500]
                    except Exception:
                        pass
                elif "html" in ct:
                    try:
                        body = await response.text()
                        entry["body_size"] = len(body)
                        entry["body_preview"] = body[:500]
                    except Exception:
                        pass
                self.network_log.append(entry)
            except Exception:
                pass

        self._page.on("response", _on_response)
        return self._page

    async def goto(self, url: str, reset_log: bool = True) -> dict:
        """URL 이동. reset_log=True면 네트워크 로그 초기화."""
        page = await self.ensure_browser()
        if reset_log:
            self.network_log.clear()
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
        except Exception:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return {"url": page.url, "title": await page.title()}

    async def snapshot(self) -> dict:
        """현재 페이지의 모든 인터랙티브 요소 추출 (라이브 DOM)"""
        page = await self.ensure_browser()
        result = await page.evaluate(SNAPSHOT_JS)
        return {
            "url": result["url"],
            "title": result["title"],
            "buttons": [b for b in result["buttons"] if b["visible"]],
            "links": [l for l in result["links"] if l["visible"]],
            "inputs": [i for i in result["inputs"] if i["visible"]],
            "selects": [s for s in result["selects"] if s["visible"]],
            "forms": result["forms"],
            "iframes": result["iframes"],
            "hidden_elements": {
                "buttons": len([b for b in result["buttons"] if not b["visible"]]),
                "links": len([l for l in result["links"] if not l["visible"]]),
                "inputs": len([i for i in result["inputs"] if not i["visible"]]),
            }
        }

    async def fill(self, selector: str, value: str, force: bool = False) -> dict:
        """입력 필드에 값 채우기. AngularJS ng-model 자동 동기화 포함."""
        page = await self.ensure_browser()
        await page.fill(selector, value, timeout=5000, force=force)

        # AngularJS ng-model 동기화
        await page.evaluate("""(args) => {
            const el = document.querySelector(args.selector);
            if (!el || !window.angular) return;
            const ngModel = el.getAttribute('data-ng-model') || el.getAttribute('ng-model');
            if (!ngModel) return;
            try {
                const scope = angular.element(el).scope();
                if (scope) {
                    scope.$apply(() => {
                        const parts = ngModel.split('.');
                        let target = scope;
                        for (let i = 0; i < parts.length - 1; i++) {
                            if (!target[parts[i]]) target[parts[i]] = {};
                            target = target[parts[i]];
                        }
                        target[parts[parts.length - 1]] = args.value;
                    });
                }
            } catch(e) {}
        }""", {"selector": selector, "value": value})

        return {"filled": selector, "value": value}

    async def click(self, selector: str) -> dict:
        """요소 클릭"""
        page = await self.ensure_browser()
        try:
            await page.click(selector, timeout=5000)
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        return {"clicked": selector, "current_url": page.url, "title": await page.title()}

    async def submit_form(self, form_selector: str = "form") -> dict:
        """폼 제출 + 새로 발생한 네트워크 요청 반환"""
        page = await self.ensure_browser()
        before_count = len(self.network_log)

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

        new_requests = list(self.network_log)[before_count:]
        return {
            "current_url": page.url,
            "new_requests": len(new_requests),
            "requests": new_requests[:10]
        }

    async def get_network_log(self, url_filter: str = "", method_filter: str = "") -> dict:
        """네트워크 로그 반환. URL/메서드 필터 가능."""
        log_list = list(self.network_log)
        filtered = log_list
        if url_filter:
            filtered = [e for e in filtered if url_filter.lower() in e["url"].lower()]
        if method_filter:
            filtered = [e for e in filtered if e["method"].upper() == method_filter.upper()]
        # 반환 시 body_preview를 200자로 제한 (AI 토큰 절약)
        trimmed = []
        for e in filtered[-30:]:
            entry = {k: v for k, v in e.items()}
            if "body_preview" in entry:
                entry["body_preview"] = entry["body_preview"][:200]
            trimmed.append(entry)
        return {
            "total": len(self.network_log),
            "filtered": len(filtered),
            "requests": trimmed
        }

    async def get_html(self, selector: str = "") -> str:
        """현재 페이지 HTML. selector 지정 시 해당 영역만."""
        page = await self.ensure_browser()
        if selector:
            el = await page.query_selector(selector)
            if not el:
                return ""
            return (await el.inner_html())[:30000]
        return (await page.content())[:30000]

    async def execute_js(self, code: str) -> Any:
        """JavaScript 실행"""
        page = await self.ensure_browser()
        return await page.evaluate(code)

    async def get_cookies(self) -> list[dict]:
        """현재 브라우저 쿠키 목록"""
        page = await self.ensure_browser()
        return await page.context.cookies()

    async def set_cookies(self, cookies: list[dict]):
        """쿠키 주입"""
        page = await self.ensure_browser()
        await page.context.add_cookies(cookies)

    async def screenshot(self, path: str) -> str:
        """스크린샷 저장"""
        page = await self.ensure_browser()
        await page.screenshot(path=path, full_page=False)
        return path

    async def wait_for_content(self, selector: str, timeout: int = 10000) -> bool:
        """특정 CSS 셀렉터가 DOM에 나타날 때까지 대기 (AJAX 콘텐츠용)"""
        page = await self.ensure_browser()
        try:
            await page.wait_for_selector(selector, timeout=timeout, state="attached")
            return True
        except Exception:
            return False

    async def wait_for_stable(self, timeout_ms: int = 3000):
        """페이지가 안정화될 때까지 대기 (AJAX 완료 + DOM 변화 없음)"""
        page = await self.ensure_browser()
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass
        await asyncio.sleep(0.5)

    async def capture_during(self, action_coro) -> list[dict]:
        """
        액션 실행 중 발생한 네트워크 요청만 캡처하여 반환.

        Usage:
            requests = await engine.capture_during(
                engine.submit_form("form")
            )
        """
        before_count = len(self.network_log)
        await action_coro
        await self.wait_for_stable(5000)
        return list(self.network_log)[before_count:]

    async def get_accessibility_tree(self) -> list[dict]:
        """CDP 접근성 트리에서 의미 있는 노드만 추출.

        Stagehand이 사용하는 것과 동일한 Chrome Accessibility Tree를
        CDP로 직접 가져옵니다. DOM HTML 대비 80~90% 크기 축소.

        Returns:
            [{"role": "textbox", "name": "약품명", "value": "타이레놀"}, ...]
        """
        page = await self.ensure_browser()
        ctx = page.context
        client = await ctx.new_cdp_session(page)
        try:
            tree = await client.send('Accessibility.getFullAXTree')
        finally:
            await client.detach()

        skip_roles = {
            'generic', 'none', 'presentation', 'StaticText',
            'InlineTextBox', 'LineBreak', 'paragraph',
        }
        # 입력 필드는 name 대신 description(placeholder)을 사용할 수 있음
        input_roles = {'textbox', 'combobox', 'searchbox', 'spinbutton'}
        nodes = []
        for n in tree.get('nodes', []):
            role = n.get('role', {}).get('value', '')
            name = n.get('name', {}).get('value', '')
            desc = n.get('description', {}).get('value', '') if 'description' in n else ''

            if role in skip_roles:
                continue

            # 입력 필드: name 없어도 description(placeholder)으로 포함
            if role in input_roles:
                label = name or desc
                if not label or len(label.strip()) < 2:
                    continue
                node = {'role': role, 'name': label.strip()[:100]}
            else:
                if not name or len(name.strip()) < 2:
                    continue
                node = {'role': role, 'name': name.strip()[:100]}

            val = n.get('value', {}).get('value', '') if 'value' in n else ''
            if val:
                node['value'] = str(val)[:50]
            if desc and desc != node['name']:
                node['description'] = desc.strip()[:100]
            nodes.append(node)
        return nodes

    async def close(self):
        """브라우저 종료"""
        if self._browser:
            await self._browser.close()
            self._browser = None
            self._page = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def snapshot_iframe(self, iframe_selector: str) -> dict:
        """iframe 내부 요소 추출"""
        page = await self.ensure_browser()
        frame_el = await page.query_selector(iframe_selector)
        if not frame_el:
            return {"error": f"iframe을 찾을 수 없음: {iframe_selector}"}
        frame = await frame_el.content_frame()
        if not frame:
            return {"error": "iframe content_frame 접근 불가"}
        return await frame.evaluate(SNAPSHOT_JS)

    # ═══════════════════════════════════════════
    # 3계층 API 추출 시스템
    # ═══════════════════════════════════════════

    async def detect_framework(self) -> dict:
        """프레임워크 자동 감지"""
        page = await self.ensure_browser()
        return await page.evaluate("""() => {
            const d = {};
            if (window.jQuery) d.jquery = jQuery.fn.jquery;
            if (window.$?.fn?.jquery && !d.jquery) d.jquery = $.fn.jquery;
            if (window.angular) d.angularjs = angular.version?.full || '1.x';
            try { if (document.querySelector('#q-app')?.__vue_app__) d.vue3 = document.querySelector('#q-app').__vue_app__.version || true; } catch(e) {}
            try { if (document.querySelector('[data-v-]')) d.vue2 = true; } catch(e) {}
            try { if (document.querySelector('[data-reactroot]') || document.querySelector('#root')?._reactRootContainer) d.react = true; } catch(e) {}
            return d;
        }""")

    async def extract_button_actions(self) -> dict:
        """3계층 API 추출: 정적 → 프레임워크별 → 동적 캡처"""
        fw = await self.detect_framework()
        actions = []
        layers_used = []

        # Layer 1: 정적 추출 (항상)
        static = await self._extract_static()
        if static:
            actions += static
            layers_used.append("static")

        # Layer 2: 프레임워크별 심층 추출
        if 'jquery' in fw:
            jquery_actions = await self._extract_jquery()
            if jquery_actions:
                actions += jquery_actions
                layers_used.append("jquery")

        if 'angularjs' in fw:
            angular_actions = await self._extract_angularjs()
            if angular_actions:
                actions += angular_actions
                layers_used.append("angularjs")

        # Layer 3: SPA 동적 캡처 (Vue 3/React 또는 Layer 1+2 결과 부족 시)
        api_actions = [a for a in actions if a.get('api') or a.get('ajax_urls')]
        if 'vue3' in fw or 'react' in fw or not api_actions:
            dynamic = await self._extract_dynamic_capture()
            if dynamic:
                actions += dynamic
                layers_used.append("dynamic_capture")

        return {
            "framework": fw,
            "button_actions": actions,
            "extraction_layers_used": layers_used
        }

    async def _extract_static(self) -> list:
        """Layer 1: href, action, onclick 등 HTML 속성에서 정적 추출"""
        page = await self.ensure_browser()
        return await page.evaluate("""() => {
            const results = [];

            // 모든 인터랙티브 요소
            document.querySelectorAll('button, a, input[type=submit], input[type=image], [onclick], [ng-click], [data-ng-click]').forEach(el => {
                const text = (el.textContent || '').trim() || el.alt || el.title || '';
                if (!text || text.length > 40) return;

                const action = {text: text.substring(0, 30), type: 'static'};

                // href
                const href = el.getAttribute('href');
                if (href && href !== '#' && !href.startsWith('javascript:void') && !href.startsWith('javascript:;')) {
                    action.href = href;
                    action.type = 'href';
                }

                // onclick 계열
                const onclick = el.getAttribute('onclick') || el.getAttribute('ng-click') || el.getAttribute('data-ng-click') || el.getAttribute('@click') || el.getAttribute('v-on:click');
                if (onclick) {
                    action.onclick = onclick.substring(0, 100);
                    const funcMatch = onclick.match(/(\\w+)\\s*\\(/);
                    if (funcMatch) action.handler_name = funcMatch[1];
                }

                // form submit
                const form = el.closest('form');
                if (form && (el.type === 'submit' || el.type === 'image')) {
                    action.type = 'form_submit';
                    action.form = form.name || form.id || '';
                    action.action = form.action || '';
                    action.method = (form.method || 'GET').toUpperCase();
                }

                if (action.href || action.onclick || action.type === 'form_submit') {
                    results.push(action);
                }
            });

            // iframe 안 링크도 정적 추출
            document.querySelectorAll('iframe').forEach(iframe => {
                try {
                    const doc = iframe.contentDocument;
                    if (!doc) return;
                    doc.querySelectorAll('a[href*=".asp"], a[href*="/Home/"]').forEach(a => {
                        const text = (a.textContent || '').trim() || a.id || '';
                        if (text) {
                            results.push({
                                text: text.substring(0, 30),
                                type: 'href',
                                href: a.getAttribute('href'),
                                in_iframe: iframe.id || iframe.name || 'unnamed'
                            });
                        }
                    });
                } catch(e) {} // cross-origin
            });

            return results;
        }""")

    async def _extract_jquery(self) -> list:
        """Layer 2A: jQuery $._data()로 이벤트 핸들러 + API URL 추출"""
        page = await self.ensure_browser()
        return await page.evaluate("""() => {
            if (!window.jQuery && !window.$) return [];
            const $ = window.jQuery || window.$;
            const results = [];

            const URL_PATTERN = /["']([^"']*(?:\\.asp|\\.php|\\/Home\\/|\\/api\\/|\\/Service\\/|\\/ord\\/|\\/common\\/ajax\\/|\\/jwt\\/)[^"']*)/g;

            function extractFromElement(el, iframeName) {
                const events = $._data ? $._data(el, 'events') : null;
                if (!events) return;
                const text = (el.textContent || '').trim() || el.alt || el.id || '';
                if (!text) return;

                for (const [type, handlers] of Object.entries(events)) {
                    if (type !== 'click' && type !== 'submit') continue;
                    handlers.forEach(h => {
                        const src = h.handler.toString();
                        const varUrl = src.match(/var\\s+url\\s*=\\s*["']([^"']+)/);
                        const urls = [];
                        let m;
                        const pat = new RegExp(URL_PATTERN.source, 'g');
                        while ((m = pat.exec(src)) !== null) urls.push(m[1]);

                        const action = {
                            text: text.substring(0, 30),
                            type: 'jquery_click',
                            event: type
                        };
                        if (varUrl) action.api = varUrl[1];
                        else if (urls.length) action.ajax_urls = [...new Set(urls)];
                        if (iframeName) action.in_iframe = iframeName;

                        if (action.api || action.ajax_urls) results.push(action);
                    });
                }
            }

            // 메인 페이지
            document.querySelectorAll('button, a[id], input[type=image], input[type=submit], [class*=btn]').forEach(el => {
                extractFromElement(el, null);
            });

            // iframe 내부
            document.querySelectorAll('iframe').forEach(iframe => {
                try {
                    const iframeJQ = iframe.contentWindow?.jQuery || iframe.contentWindow?.$;
                    if (!iframeJQ || !iframeJQ._data) return;
                    const doc = iframe.contentDocument;
                    doc.querySelectorAll('a[id], button, input[type=image]').forEach(el => {
                        const events = iframeJQ._data(el, 'events');
                        if (!events) return;
                        const text = (el.textContent || '').trim() || el.alt || el.id || '';
                        for (const [type, handlers] of Object.entries(events)) {
                            if (type !== 'click' && type !== 'submit') continue;
                            handlers.forEach(h => {
                                const src = h.handler.toString();
                                const varUrl = src.match(/var\\s+url\\s*=\\s*["']([^"']+)/);
                                const urls = [];
                                let m;
                                const pat = new RegExp(URL_PATTERN.source, 'g');
                                while ((m = pat.exec(src)) !== null) urls.push(m[1]);
                                const action = {
                                    text: text.substring(0, 30),
                                    type: 'jquery_click',
                                    event: type,
                                    in_iframe: iframe.id || iframe.name
                                };
                                if (varUrl) action.api = varUrl[1];
                                else if (urls.length) action.ajax_urls = [...new Set(urls)];
                                if (action.api || action.ajax_urls) results.push(action);
                            });
                        }
                    });
                } catch(e) {} // cross-origin
            });

            return results;
        }""")

    async def _extract_angularjs(self) -> list:
        """Layer 2B: AngularJS ng-click → scope 함수 → URL 추출"""
        page = await self.ensure_browser()
        return await page.evaluate("""() => {
            if (!window.angular) return [];
            const results = [];
            const URL_PATTERN = /["']([^"']*(?:\\.asp|\\.php|\\/Home\\/|\\/api\\/|\\/common\\/ajax\\/)[^"']*)/g;

            document.querySelectorAll('[data-ng-click], [ng-click]').forEach(el => {
                const text = (el.textContent || '').trim();
                const ngClick = el.getAttribute('data-ng-click') || el.getAttribute('ng-click');
                if (!text || !ngClick) return;

                const funcMatch = ngClick.match(/(\\w+)\\s*\\(/);
                if (!funcMatch) return;
                const funcName = funcMatch[1];

                try {
                    const scope = angular.element(el).scope();
                    if (scope && typeof scope[funcName] === 'function') {
                        const src = scope[funcName].toString();
                        const urls = [];
                        let m;
                        const pat = new RegExp(URL_PATTERN.source, 'g');
                        while ((m = pat.exec(src)) !== null) urls.push(m[1]);

                        if (urls.length) {
                            results.push({
                                text: text.substring(0, 30),
                                type: 'angularjs_click',
                                handler_name: funcName,
                                ajax_urls: [...new Set(urls)]
                            });
                        }
                    }
                } catch(e) {}
            });

            return results;
        }""")

    async def install_xhr_patch(self):
        """XHR/fetch monkey-patch 설치 — SPA에서 AI가 클릭하기 전에 호출"""
        page = await self.ensure_browser()
        await page.evaluate("""() => {
            if (window.__xhr_patched) return 'already_patched';
            const captured = [];
            const origOpen = XMLHttpRequest.prototype.open;
            const origSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(m, u) {
                this._m = m; this._u = u;
                return origOpen.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function(b) {
                if (this._u && !this._u.endsWith('.js') && !this._u.endsWith('.css'))
                    captured.push({method: this._m, url: this._u, body: b ? String(b).substring(0, 200) : null, ts: Date.now()});
                return origSend.apply(this, arguments);
            };
            const origFetch = window.fetch;
            window.fetch = function(u, o) {
                const url = String(u);
                if (!url.endsWith('.js') && !url.endsWith('.css'))
                    captured.push({method: o?.method || 'GET', url, body: o?.body ? String(o.body).substring(0, 200) : null, ts: Date.now()});
                return origFetch.apply(this, arguments);
            };
            window.__captured = captured;
            window.__xhr_patched = true;
            return 'patched';
        }""")

    async def get_captured_requests(self, since: int = 0) -> list:
        """패치 설치 후 캡처된 API 요청 반환 (CSS/JS/이미지 제외)"""
        page = await self.ensure_browser()
        return await page.evaluate("""(since) => {
            const all = window.__captured || [];
            return all.slice(since).filter(r =>
                !r.url.includes('/Content/') && !r.url.includes('/bundles/') &&
                !r.url.endsWith('.png') && !r.url.endsWith('.gif') && !r.url.endsWith('.jpg')
            );
        }""", since)

    async def _extract_dynamic_capture(self) -> list:
        """Layer 3: XHR/fetch 패치만 설치. 실제 클릭은 AI가 직접 수행."""
        await self.install_xhr_patch()

        # AI가 직접 screenshot + click_element + get_network_log로 탐색
        # 여기서는 패치만 설치하고, 캡처된 요청은 get_captured_requests()로 조회
        return []
