#!/usr/bin/env python3
"""
독립 E2E 레시피 테스트 하네스

검증된 레시피 없이도 동작하는 테스트 + 진단 도구.
실패 시 원인 분석 및 수정 제안을 제공.

사용법:
    python recipe_test_harness.py \\
        --recipe recipes/bpm_geoweb_kr.json \\
        --username YOUR_ID \\
        --password YOUR_PW
"""

import json
import sys
import argparse
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from site_executor import SiteExecutor

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")


@dataclass
class StepResult:
    name: str
    success: bool
    details: dict = field(default_factory=dict)
    error: Optional[str] = None
    suggestions: list = field(default_factory=list)


class RecipeTestHarness:
    """독립 E2E 레시피 테스트"""

    def __init__(self, recipe_path: str, username: str, password: str,
                 test_keyword: str = "타이레놀"):
        self.recipe_path = Path(recipe_path)
        self.username = username
        self.password = password
        self.test_keyword = test_keyword

        with open(self.recipe_path, "r", encoding="utf-8") as f:
            self.recipe = json.load(f)

        self.executor = SiteExecutor(self.recipe)
        self.results: list[StepResult] = []

    def _header(self, title: str):
        print(f"\n{'=' * 60}")
        print(f" {title}")
        print(f"{'=' * 60}")

    def test_login(self) -> StepResult:
        """로그인 테스트 + 진단"""
        self._header("🔐 로그인 테스트")

        result = StepResult(name="login", success=False)

        # 레시피 검증
        login_spec = self.recipe.get("login")
        if not login_spec:
            result.error = "레시피에 login 섹션 없음"
            result.suggestions.append("analyze_page_for_recipe('login')으로 로그인 분석 필요")
            print(f"  ❌ {result.error}")
            return result

        print(f"  URL: {login_spec.get('url')}")
        print(f"  Method: {login_spec.get('method')}")
        print(f"  Payload 필드: {list(login_spec.get('payload', {}).keys())}")

        try:
            success = self.executor.login(self.username, self.password)

            result.details["authenticated"] = self.executor.is_authenticated()
            result.details["cookie_count"] = len(self.executor.session.cookies)
            result.details["cookies"] = {c.name: c.value[:30] + "..." for c in self.executor.session.cookies}

            if success:
                result.success = True
                print(f"  ✅ 로그인 성공")
                print(f"     인증: {self.executor.is_authenticated()}")
                print(f"     쿠키: {len(self.executor.session.cookies)}개")
                for c in self.executor.session.cookies:
                    print(f"       - {c.name} = {c.value[:30]}...")
            else:
                result.error = "로그인 실패 (인증 실패)"
                print(f"  ❌ 로그인 실패")

                # 진단
                if self.executor.session.cookies:
                    result.suggestions.append(
                        f"쿠키가 {len(self.executor.session.cookies)}개 생성됨 — "
                        f"success_indicator.key가 잘못되었을 수 있음. "
                        f"현재: '{login_spec.get('success_indicator', {}).get('key')}', "
                        f"실제 쿠키: {[c.name for c in self.executor.session.cookies]}"
                    )
                else:
                    result.suggestions.append("쿠키가 생성되지 않음 — URL 또는 payload 확인 필요")
                    result.suggestions.append("브라우저에서 capture_form_submission()으로 실제 요청 확인")

        except Exception as e:
            result.error = str(e)
            result.suggestions.append(f"예외 발생: {e}")
            result.suggestions.append("login.url이 올바른지 확인")
            print(f"  ❌ 예외: {e}")

        self.results.append(result)
        return result

    def test_search(self) -> StepResult:
        """검색 테스트 + 진단"""
        self._header(f"🔍 검색 테스트 ('{self.test_keyword}')")

        result = StepResult(name="search", success=False)

        search_spec = self.recipe.get("search")
        if not search_spec:
            result.error = "레시피에 search 섹션 없음"
            result.suggestions.append("브라우저로 검색 페이지 분석 필요")
            print(f"  ❌ {result.error}")
            self.results.append(result)
            return result

        print(f"  URL: {search_spec.get('url')}")
        print(f"  Method: {search_spec.get('method')}")
        print(f"  파라미터: {list(search_spec.get('params', {}).keys())}")
        print(f"  행 셀렉터: {search_spec.get('parsing', {}).get('selector')}")

        try:
            products = self.executor.search(self.test_keyword)

            result.details["product_count"] = len(products)

            if products:
                result.success = True
                result.details["first_3"] = [
                    {"name": p.product_name, "code": p.product_code, "price": p.unit_price}
                    for p in products[:3]
                ]
                print(f"  ✅ 검색 성공: {len(products)}건")
                for i, p in enumerate(products[:3], 1):
                    print(f"     {i}. {p.product_name} (코드: {p.product_code}, 가격: {p.unit_price})")

                # 데이터 품질 체크
                empty_codes = sum(1 for p in products if not p.product_code)
                empty_names = sum(1 for p in products if not p.product_name)
                if empty_codes > len(products) * 0.5:
                    result.suggestions.append(
                        f"상품코드 누락 {empty_codes}/{len(products)} — "
                        f"parsing.fields.product_code 셀렉터 확인 필요"
                    )
                if empty_names > len(products) * 0.5:
                    result.suggestions.append(
                        f"상품명 누락 {empty_names}/{len(products)} — "
                        f"parsing.fields.product_name 셀렉터 확인 필요"
                    )
            else:
                result.error = "검색 결과 없음 (0건)"
                print(f"  ❌ 검색 결과 없음")

                # 진단: HTTP 응답 확인
                result.suggestions.append("search.url이 올바른지 확인 — 브라우저에서 get_network_log('POST') 실행")
                result.suggestions.append("search.params에 필수 파라미터가 빠졌을 수 있음")
                result.suggestions.append("parsing.selector가 실제 HTML 구조와 맞는지 확인")

        except Exception as e:
            result.error = str(e)
            print(f"  ❌ 예외: {e}")

            if "404" in str(e):
                result.suggestions.append("search.url이 잘못됨 (404) — 올바른 검색 엔드포인트 확인 필요")
            elif "500" in str(e):
                result.suggestions.append("서버 에러 (500) — 파라미터 형식 확인")
            else:
                result.suggestions.append(f"예외: {e}")

        self.results.append(result)
        return result

    def test_cart_add(self) -> StepResult:
        """장바구니 추가 테스트 + 진단"""
        self._header("🛒 장바구니 추가 테스트")

        result = StepResult(name="cart_add", success=False)

        cart_spec = self.recipe.get("cart_add")
        if not cart_spec:
            result.error = "레시피에 cart_add 섹션 없음"
            result.suggestions.append("브라우저로 장바구니 폼 분석 필요")
            print(f"  ❌ {result.error}")
            self.results.append(result)
            return result

        print(f"  URL: {cart_spec.get('url')}")
        print(f"  Type: {cart_spec.get('type')}")
        print(f"  폼 이름: {cart_spec.get('form_name')}")

        # 재고 있는 상품 찾기
        search_result = next((r for r in self.results if r.name == "search"), None)
        if not search_result or not search_result.success:
            result.error = "검색 실패로 테스트 불가"
            result.suggestions.append("검색을 먼저 성공시켜야 장바구니 테스트 가능")
            print(f"  ⚠️ {result.error}")
            self.results.append(result)
            return result

        # 재검색 (장바구니용)
        products = self.executor.search(self.test_keyword)
        test_product = next((p for p in products if p.stock_available and p.product_code), None)

        if not test_product:
            test_product = next((p for p in products if p.product_code), None)

        if not test_product:
            result.error = "테스트할 상품 없음"
            result.suggestions.append("상품코드가 없음 — parsing.fields.product_code 확인")
            print(f"  ⚠️ {result.error}")
            self.results.append(result)
            return result

        print(f"  테스트 상품: {test_product.product_name}")
        print(f"  상품코드: {test_product.product_code}")

        try:
            success = self.executor.add_to_cart(test_product.product_code, 1)

            if success:
                result.success = True
                print(f"  ✅ 장바구니 추가 성공")
            else:
                result.error = "장바구니 추가 실패"
                print(f"  ❌ 장바구니 추가 실패")
                result.suggestions.append("cart_add.url 확인 — 브라우저에서 실제 장바구니 요청 확인")
                result.suggestions.append("cart_add.form_name 확인 — 올바른 폼 이름인지 확인")
                result.suggestions.append("cart_add.product_code_prefix 확인 — 실제 input name 패턴 확인")

        except Exception as e:
            result.error = str(e)
            result.suggestions.append(f"예외: {e}")
            print(f"  ❌ 예외: {e}")

        self.results.append(result)
        return result

    def test_sales_ledger(self) -> StepResult:
        """매출원장 테스트 + 진단"""
        self._header("📊 매출원장 테스트")

        result = StepResult(name="sales_ledger", success=False)

        ledger_spec = self.recipe.get("sales_ledger")
        if not ledger_spec:
            result.error = "레시피에 sales_ledger 섹션 없음 (선택 기능)"
            print(f"  ⚠️ {result.error}")
            self.results.append(result)
            return result

        print(f"  URL: {ledger_spec.get('url')}")
        print(f"  Method: {ledger_spec.get('method')}")

        try:
            entries = self.executor.get_sales_ledger(period="1m")
            result.details["entry_count"] = len(entries)

            if entries:
                result.success = True
                filled = sum(1 for e in entries if e.product_name and e.transaction_date)
                result.details["filled_ratio"] = f"{filled}/{len(entries)}"
                print(f"  ✅ 매출원장 성공: {len(entries)}건 (필드 채움: {filled}/{len(entries)})")
                for e in entries[:3]:
                    print(f"     {e.transaction_date} | {e.product_name[:25]} | 매출:{e.sales_amount:,.0f}")
            else:
                result.error = "매출원장 결과 없음 (0건)"
                print(f"  ❌ {result.error}")
                result.suggestions.append("sales_ledger.url 확인")
                result.suggestions.append("sales_ledger.params 날짜 형식 확인")

        except Exception as e:
            result.error = str(e)
            result.suggestions.append(f"예외: {e}")
            print(f"  ❌ 예외: {e}")

        self.results.append(result)
        return result

    def run(self) -> dict:
        """전체 테스트 실행"""
        print("=" * 60)
        print(f" 레시피 테스트 하네스")
        print(f" 파일: {self.recipe_path.name}")
        print(f" 사이트: {self.recipe.get('site_name', 'N/A')}")
        print("=" * 60)

        # 테스트 실행
        login_result = self.test_login()

        if login_result.success:
            search_result = self.test_search()

            if search_result.success:
                self.test_cart_add()

            # 매출원장은 검색 성공 여부와 무관하게 테스트
            self.test_sales_ledger()

        # 결과 요약
        self._header("📊 결과 요약")

        success_count = sum(1 for r in self.results if r.success)
        total = len(self.results)

        for r in self.results:
            status = "✅" if r.success else "❌"
            print(f"  {status} {r.name}: {'성공' if r.success else r.error}")

        print(f"\n  성공률: {success_count}/{total} ({success_count / total * 100:.1f}%)")

        # 수정 제안
        all_suggestions = []
        for r in self.results:
            if r.suggestions:
                all_suggestions.extend(r.suggestions)

        if all_suggestions:
            self._header("💡 수정 제안")
            for i, sug in enumerate(all_suggestions, 1):
                print(f"  {i}. {sug}")

        # JSON 결과
        report = {
            "recipe_file": str(self.recipe_path),
            "site_name": self.recipe.get("site_name"),
            "success_rate": f"{success_count}/{total}",
            "results": [
                {
                    "name": r.name,
                    "success": r.success,
                    "error": r.error,
                    "details": r.details,
                    "suggestions": r.suggestions
                }
                for r in self.results
            ],
            "all_suggestions": all_suggestions
        }

        return report


def main():
    parser = argparse.ArgumentParser(description='독립 E2E 레시피 테스트')
    parser.add_argument('--recipe', required=True, help='레시피 파일 경로')
    parser.add_argument('--username', required=True, help='로그인 아이디')
    parser.add_argument('--password', required=True, help='로그인 비밀번호')
    parser.add_argument('--keyword', default='타이레놀', help='테스트 검색어')

    args = parser.parse_args()

    harness = RecipeTestHarness(
        recipe_path=args.recipe,
        username=args.username,
        password=args.password,
        test_keyword=args.keyword
    )

    report = harness.run()

    # 종료 코드
    success_count = sum(1 for r in harness.results if r.success)
    sys.exit(0 if success_count == len(harness.results) else 1)


if __name__ == "__main__":
    main()
