#!/usr/bin/env python3
"""
레시피 E2E 검증 스크립트

생성된 레시피를 SiteExecutor로 전체 기능 테스트하고
available_features와 실제 결과를 비교합니다.

사용법:
    python e2e_validator.py <site_id>
    python e2e_validator.py --all
    python e2e_validator.py --compare <site_id>  # 백업과 비교
"""

import json
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from site_executor import SiteExecutor


def load_recipe(site_id, recipe_dir="recipes"):
    path = PROJECT_ROOT / recipe_dir / f"{site_id}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_credentials():
    path = PROJECT_ROOT / "credentials.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_site(site_id, recipe, cred):
    """한 사이트의 전체 기능 E2E 테스트"""
    results = {
        "site_id": site_id,
        "site_name": recipe.get("site_name", site_id),
        "login": False,
        "search": False,
        "search_count": 0,
        "search_has_price": False,
        "search_has_code": False,
        "cart_add": False,
        "cart_view": False,
        "cart_view_count": 0,
        "cart_delete": False,
        "cart_clear": False,
        "sales_ledger": False,
        "sales_ledger_count": 0,
        "errors": [],
    }

    executor = SiteExecutor(recipe)
    sp = cred.get("site_params", {})

    # 1. login
    try:
        results["login"] = executor.login(cred["username"], cred["password"], site_params=sp)
    except Exception as e:
        results["errors"].append(f"login: {e}")

    if not results["login"]:
        return results

    # 2. search
    products = []
    try:
        products = executor.search("타이레놀")
        results["search"] = len(products) > 0
        results["search_count"] = len(products)
        if products:
            results["search_has_price"] = any(p.unit_price > 0 for p in products)
            results["search_has_code"] = any(bool(p.product_code) for p in products)
    except Exception as e:
        results["errors"].append(f"search: {e}")

    # 3. cart_add
    test_pc = ""
    if products:
        p = next((x for x in products if x.stock_available and x.product_code), None)
        if p:
            test_pc = p.product_code
            try:
                results["cart_add"] = executor.add_to_cart(test_pc, 1)
            except Exception as e:
                results["errors"].append(f"cart_add: {e}")

    # 4. cart_view
    if "cart_view" in recipe:
        try:
            items = executor.view_cart()
            results["cart_view"] = len(items) > 0
            results["cart_view_count"] = len(items)
        except Exception as e:
            results["errors"].append(f"cart_view: {e}")

    # 5. cart_delete
    if "cart_delete" in recipe and test_pc:
        try:
            results["cart_delete"] = executor.delete_from_cart(test_pc)
        except Exception as e:
            results["errors"].append(f"cart_delete: {e}")

    # 6. cart_clear
    if "cart_clear" in recipe:
        # 다시 담고 비우기
        if test_pc and not results["cart_delete"]:
            pass  # 이미 장바구니에 있음
        elif test_pc:
            try:
                executor.add_to_cart(test_pc, 1)
            except:
                pass
        try:
            results["cart_clear"] = executor.clear_cart()
        except Exception as e:
            results["errors"].append(f"cart_clear: {e}")

    # 7. sales_ledger
    if "sales_ledger" in recipe:
        try:
            entries = executor.get_sales_ledger(period="1m")
            results["sales_ledger"] = True  # API 호출 성공
            results["sales_ledger_count"] = len(entries)
        except Exception as e:
            results["errors"].append(f"sales_ledger: {e}")

    return results


def check_features_match(recipe, results):
    """available_features와 실제 E2E 결과 비교"""
    af = recipe.get("available_features", {})
    mismatches = []

    checks = [
        ("login", results["login"]),
        ("search", results["search"]),
        ("cart_add", results["cart_add"]),
        ("cart_view", results["cart_view"]),
        ("cart_delete", results["cart_delete"]),
        ("cart_clear", results["cart_clear"]),
        ("sales_ledger", results["sales_ledger"]),
    ]

    for feature, actual in checks:
        declared = af.get(feature, feature in recipe)
        if declared != actual:
            mismatches.append({
                "feature": feature,
                "declared": declared,
                "actual": actual,
            })

    return mismatches


def print_results(results, mismatches=None):
    """결과 출력"""
    name = results["site_name"]
    print(f"\n{'=' * 60}")
    print(f" {name} ({results['site_id']})")
    print(f"{'=' * 60}")

    features = [
        ("login", results["login"], ""),
        ("search", results["search"], f"{results['search_count']}건 price={results['search_has_price']} code={results['search_has_code']}"),
        ("cart_add", results["cart_add"], ""),
        ("cart_view", results["cart_view"], f"{results['cart_view_count']}건"),
        ("cart_delete", results["cart_delete"], ""),
        ("cart_clear", results["cart_clear"], ""),
        ("sales_ledger", results["sales_ledger"], f"{results['sales_ledger_count']}건"),
    ]

    for name, ok, detail in features:
        status = "✅" if ok else "❌"
        d = f" ({detail})" if detail else ""
        print(f"  {name:15s} {status}{d}")

    if results["errors"]:
        print(f"\n  에러:")
        for err in results["errors"]:
            print(f"    - {err[:80]}")

    if mismatches:
        print(f"\n  available_features 불일치:")
        for m in mismatches:
            print(f"    - {m['feature']}: declared={m['declared']} actual={m['actual']}")

    # 성공률
    total = 7
    passed = sum(1 for _, ok, _ in features if ok)
    print(f"\n  성공률: {passed}/{total} ({passed/total*100:.0f}%)")

    return passed == total


def main():
    creds = load_credentials()

    if len(sys.argv) < 2:
        print("사용법: python e2e_validator.py <site_id|--all|--compare site_id>")
        sys.exit(1)

    if sys.argv[1] == "--all":
        # 모든 사이트 테스트
        recipes_dir = PROJECT_ROOT / "recipes"
        all_pass = True
        for f in sorted(recipes_dir.glob("*.json")):
            site_id = f.stem
            if site_id.endswith("_auto"):
                continue
            recipe = load_recipe(site_id)
            if not recipe or site_id not in creds:
                continue
            results = test_site(site_id, recipe, creds[site_id])
            mismatches = check_features_match(recipe, results)
            ok = print_results(results, mismatches)
            if not ok:
                all_pass = False

        sys.exit(0 if all_pass else 1)

    elif sys.argv[1] == "--compare" and len(sys.argv) >= 3:
        # 현재 레시피 vs 백업 비교
        site_id = sys.argv[2]
        recipe_new = load_recipe(site_id, "recipes")
        recipe_old = load_recipe(site_id, "recipes_backup_manual")

        if not recipe_new:
            print(f"레시피 없음: recipes/{site_id}.json")
            sys.exit(1)

        cred = creds.get(site_id)
        if not cred:
            print(f"크레덴셜 없음: {site_id}")
            sys.exit(1)

        print("=== 새 레시피 ===")
        results_new = test_site(site_id, recipe_new, cred)
        print_results(results_new)

        if recipe_old:
            print("\n=== 백업 레시피 ===")
            results_old = test_site(site_id, recipe_old, cred)
            print_results(results_old)

        sys.exit(0)

    else:
        # 단일 사이트 테스트
        # --cred 옵션으로 다른 크레덴셜 사용 가능
        site_id = sys.argv[1]
        cred_id = site_id
        for i, arg in enumerate(sys.argv):
            if arg == "--cred" and i + 1 < len(sys.argv):
                cred_id = sys.argv[i + 1]

        recipe = load_recipe(site_id)
        if not recipe:
            print(f"레시피 없음: recipes/{site_id}.json")
            sys.exit(1)

        cred = creds.get(cred_id)
        if not cred:
            print(f"크레덴셜 없음: {cred_id}")
            sys.exit(1)

        results = test_site(site_id, recipe, cred)
        mismatches = check_features_match(recipe, results)
        ok = print_results(results, mismatches)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
