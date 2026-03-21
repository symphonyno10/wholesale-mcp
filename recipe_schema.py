"""
도매 주문 레시피 스키마 및 데이터 모델

레시피: AI가 도매 사이트를 분석하여 추출한 HTTP 요청 경로 정보.
"이 사이트에서 검색하려면 POST /search에 {searchWord: '...'} 보내면 된다" 같은 구조 정보.
"""
from dataclasses import dataclass, field
from typing import Any, Optional
import json
import logging

logger = logging.getLogger(__name__)

# 레시피 JSON 최상위 필수 키 (메타데이터만 필수, login/search는 단계별 분석 시 없을 수 있음)
REQUIRED_RECIPE_KEYS = {'recipe_version', 'site_id', 'site_name', 'site_url'}


@dataclass
class WholesaleProduct:
    """도매 상품 통합 데이터 모델"""
    site_id: str
    product_code: str
    product_name: str
    edi_code: str = ''
    manufacturer: str = ''
    unit_price: float = 0.0
    stock_available: bool = True
    stock_quantity: int = -1       # 실제 재고 수량 (-1: 미확인, 0: 품절)
    pack_unit: str = ''            # 규격 원문 (예: '500T', '650/500T')
    pack_units: list = field(default_factory=list)  # [500, 100, 30] 포장 단위 목록
    box_quantity: int = 0          # 박스입수
    insurance_type: str = ''       # '보험' | '비보험' | ''
    product_type: str = ''         # '전문' | '일반' | ''
    discount: float = 0.0          # 할인율
    remark: str = ''               # 비고 (품절, 공급지연, 생산중지 등)
    min_order_qty: int = 1
    raw_data: dict = field(default_factory=dict)


@dataclass
class SalesLedgerEntry:
    """매출원장 항목"""
    site_id: str = ''
    transaction_date: str = ''
    product_name: str = ''
    pack_unit: str = ''
    quantity: int = 0
    unit_price: float = 0.0
    sales_amount: float = 0.0
    financial_discount: float = 0.0
    payment: float = 0.0
    balance: float = 0.0
    product_code: str = ''
    manufacturer: str = ''
    raw_data: dict = field(default_factory=dict)


@dataclass
class OrderItem:
    """주문 항목"""
    product_code: str
    product_name: str
    edi_code: str
    unit: int         # 포장 단위 (100T, 30T 등)
    quantity: int     # 주문 수량 (통 수)
    unit_price: float = 0.0

    @property
    def total_amount(self) -> int:
        """총 수량 (정 수)"""
        return self.unit * self.quantity

    @property
    def subtotal(self) -> float:
        """소계"""
        return self.unit_price * self.quantity


@dataclass
class OrderResult:
    """주문 결과"""
    success: bool
    order_id: str = ''
    message: str = ''
    items_ordered: list = field(default_factory=list)
    timestamp: str = ''


@dataclass
class CartItem:
    """장바구니 항목"""
    site_id: str
    product_code: str
    product_name: str
    edi_code: str = ''
    unit: int = 0
    quantity: int = 1
    unit_price: float = 0.0


def validate_recipe(recipe: dict) -> tuple[bool, str]:
    """레시피 JSON 유효성 검증"""
    missing = REQUIRED_RECIPE_KEYS - set(recipe.keys())
    if missing:
        return False, f"필수 키 누락: {missing}"

    if recipe.get('recipe_version', 0) < 1:
        return False, "recipe_version은 1 이상이어야 합니다"

    login = recipe.get('login', {})
    if login:
        if not login.get('url'):
            return False, "login.url이 필요합니다"
        if not login.get('fields') and not login.get('payload'):
            return False, "login.fields 또는 login.payload가 필요합니다"

    search = recipe.get('search', {})
    if search:
        if not search.get('url'):
            return False, "search.url이 필요합니다"
        if search.get('response_type') and search['response_type'] not in ('html', 'json'):
            return False, "search.response_type은 'html' 또는 'json'이어야 합니다"

    return True, "유효"


def load_recipe_from_file(file_path: str) -> Optional[dict]:
    """JSON 파일에서 레시피 로드"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            recipe = json.load(f)
        valid, msg = validate_recipe(recipe)
        if not valid:
            logger.warning(f"레시피 검증 실패 ({file_path}): {msg}")
            return None
        return recipe
    except Exception as e:
        logger.error(f"레시피 로드 실패 ({file_path}): {e}")
        return None
