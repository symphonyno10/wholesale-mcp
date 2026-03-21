"""
브라우저 엔진 모듈

Playwright 브라우저 + 네트워크 캡처를 재사용 가능한 클래스로 제공.
server.py (MCP 도구)와 recipe_explorer.py (탐색기) 양쪽에서 사용.
"""

import asyncio
import json
import logging
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
        self.network_log: list[dict] = []

    @property
    def page(self):
        return self._page

    async def ensure_browser(self):
        """브라우저 싱글톤 초기화 (lazy init)"""
        if self._page and not self._page.is_closed():
            return self._page

        from playwright.async_api import async_playwright

        if not self._playwright:
            self._playwright = await async_playwright().start()
        if not self._browser or not self._browser.is_connected():
            self._browser = await self._playwright.chromium.launch(headless=False)

        ctx = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        self._page = await ctx.new_page()
        self.network_log = []

        # confirm/alert/prompt 다이얼로그 자동 수락
        async def _on_dialog(dialog):
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
                        entry["body_preview"] = body[:2000]
                    except Exception:
                        pass
                elif "html" in ct:
                    try:
                        body = await response.text()
                        entry["body_size"] = len(body)
                        entry["body_preview"] = body[:2000]
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
            self.network_log = []
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

        new_requests = self.network_log[before_count:]
        return {
            "current_url": page.url,
            "new_requests": len(new_requests),
            "requests": new_requests[:10]
        }

    async def get_network_log(self, url_filter: str = "", method_filter: str = "") -> dict:
        """네트워크 로그 반환. URL/메서드 필터 가능."""
        filtered = self.network_log
        if url_filter:
            filtered = [e for e in filtered if url_filter.lower() in e["url"].lower()]
        if method_filter:
            filtered = [e for e in filtered if e["method"].upper() == method_filter.upper()]
        return {
            "total": len(self.network_log),
            "filtered": len(filtered),
            "requests": filtered[-50:]
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
        return self.network_log[before_count:]

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
