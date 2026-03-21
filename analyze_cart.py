#!/usr/bin/env python3
"""
장바구니 폼 구조 분석 스크립트
실제 검색 결과 HTML을 분석하여 장바구니 폼 구조를 파악합니다.
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from site_executor import SiteExecutor
from bs4 import BeautifulSoup


def analyze_form_structure(html: str):
    """폼 구조 상세 분석"""
    print("\n" + "="*60)
    print("📋 폼 구조 분석")
    print("="*60)

    soup = BeautifulSoup(html, 'html.parser')

    # 모든 form 찾기
    forms = soup.find_all('form')
    print(f"\n찾은 폼 개수: {len(forms)}")

    for idx, form in enumerate(forms, 1):
        print(f"\n폼 #{idx}:")
        print(f"  name: {form.get('name', 'N/A')}")
        print(f"  id: {form.get('id', 'N/A')}")
        print(f"  action: {form.get('action', 'N/A')}")
        print(f"  method: {form.get('method', 'GET').upper()}")

        # hidden input 분석
        hiddens = form.find_all('input', attrs={'type': 'hidden'})
        print(f"  hidden inputs: {len(hiddens)}개")

        if hiddens:
            print(f"    샘플 (최대 10개):")
            for h in hiddens[:10]:
                name = h.get('name', '')
                value = h.get('value', '')[:30]
                print(f"      - {name} = {value}")

        # visible input 분석
        visible_inputs = form.find_all('input', attrs={'type': lambda t: t not in ['hidden', 'image', 'submit']})
        print(f"  visible inputs: {len(visible_inputs)}개")

        # product code 패턴 찾기
        pc_inputs = form.find_all('input', attrs={'name': lambda n: n and (n.startswith('pc_') or 'product' in n.lower() or 'code' in n.lower())})
        print(f"  product code inputs: {len(pc_inputs)}개")

        if pc_inputs:
            print(f"    샘플:")
            for inp in pc_inputs[:5]:
                name = inp.get('name', '')
                value = inp.get('value', '')
                print(f"      - {name} = {value}")

        # quantity 패턴 찾기
        qty_inputs = form.find_all('input', attrs={'name': lambda n: n and (n.startswith('qty_') or 'qty' in n.lower() or 'quantity' in n.lower())})
        print(f"  quantity inputs: {len(qty_inputs)}개")

        if qty_inputs:
            print(f"    샘플:")
            for inp in qty_inputs[:5]:
                name = inp.get('name', '')
                value = inp.get('value', '')
                print(f"      - {name} = {value}")


def analyze_table_structure(html: str):
    """테이블 구조 분석"""
    print("\n" + "="*60)
    print("📊 테이블 구조 분석")
    print("="*60)

    soup = BeautifulSoup(html, 'html.parser')

    # 상품 행 찾기
    product_rows = soup.select("tr.ln_physic")
    print(f"\n상품 행 개수: {len(product_rows)}")

    if product_rows:
        print("\n첫 번째 상품 행 상세 분석:")
        row = product_rows[0]

        # 모든 input 찾기
        inputs = row.find_all('input')
        print(f"\n  Input 필드 개수: {len(inputs)}")

        for inp in inputs:
            inp_type = inp.get('type', 'text')
            name = inp.get('name', 'N/A')
            value = inp.get('value', 'N/A')
            print(f"    - type:{inp_type:10s} name:{name:20s} value:{value}")

        # 모든 td 찾기
        tds = row.find_all('td')
        print(f"\n  TD 개수: {len(tds)}")

        for idx, td in enumerate(tds, 1):
            text = td.get_text(strip=True)[:50]
            print(f"    td[{idx}]: {text}")


def main():
    """메인 함수"""
    print("="*60)
    print("🔬 장바구니 폼 구조 분석")
    print("="*60)

    # 레시피 로드
    recipe_path = PROJECT_ROOT / "recipes" / "wos_nicepharm_com.json"
    with open(recipe_path, 'r', encoding='utf-8') as f:
        recipe = json.load(f)

    # 로그인 및 검색
    executor = SiteExecutor(recipe)

    print("\n로그인 중...")
    if not executor.login("REDACTED_ID", "1234"):
        print("❌ 로그인 실패")
        return

    print("✅ 로그인 성공")

    print("\n검색 중: '타이레놀'")
    products = executor.search("타이레놀")
    print(f"✅ 검색 완료: {len(products)}건")

    if not products:
        print("❌ 검색 결과 없음")
        return

    # 검색 결과 HTML 가져오기
    html = executor._last_search_html

    if not html:
        print("❌ 검색 결과 HTML 없음")
        return

    print(f"\n HTML 길이: {len(html):,} bytes")

    # 분석 시작
    analyze_form_structure(html)
    analyze_table_structure(html)

    # 상품 정보 출력
    print("\n" + "="*60)
    print("🛒 테스트 가능한 상품")
    print("="*60)

    available_products = [p for p in products if p.stock_available and p.product_code]

    if available_products:
        print(f"\n재고 있는 상품: {len(available_products)}개")
        for i, product in enumerate(available_products[:3], 1):
            print(f"\n{i}. {product.product_name}")
            print(f"   상품코드: {product.product_code}")
            print(f"   재고: {product.stock_quantity}")
            print(f"   가격: {product.unit_price:,.0f}원")
    else:
        print("⚠️  재고 있는 상품 없음")

    # HTML 샘플 저장
    output_path = PROJECT_ROOT / "search_result_sample.html"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n✅ 검색 결과 HTML 저장: {output_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
