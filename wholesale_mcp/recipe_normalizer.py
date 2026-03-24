"""
AI 레시피 정규화 모듈

AI(Gemini 등)가 생성한 레시피 JSON은 매번 키 이름이 달라질 수 있다.
이 모듈은 다양한 형태의 AI 출력을 하나의 정규(canonical) 형식으로 변환하여
SiteExecutor가 안정적으로 실행할 수 있게 한다.
"""
import logging
from copy import deepcopy

logger = logging.getLogger(__name__)

# 단계(step) 이름 목록
_STEP_NAMES = ('login', 'search', 'search_result', 'cart_add', 'cart', 'order_submit', 'order')

# 성공 판단 키 변형 → 정규: success_indicator
_INDICATOR_KEY_ALIASES = ('success_indicator', 'verification', 'validation')

# 성공 타입 정규화 매핑
_INDICATOR_TYPE_MAP = {
    'html_contains': 'contains',
    'text_contains': 'contains',
    'url_contains': 'redirect',
}

# 파싱 selector 키 변형 → 정규: selector
_SELECTOR_KEY_ALIASES = ('selector', 'item_selector', 'container', 'row_selector')

# 필드명 정규화 (AI 변형 → 정규)
_FIELD_NAME_MAP = {
    'name': 'product_name',
    'product_id': 'product_code',
    'goods_code': 'product_code',
    'it_id': 'product_code',
    'maker': 'manufacturer',
    'standard': 'pack_unit',
    'specification': 'pack_unit',
    'spec': 'pack_unit',
    'std': 'pack_unit',
}


def normalize_recipe(raw: dict) -> dict:
    """AI 생성 레시피 → 정규 형식 변환"""
    recipe = deepcopy(raw)
    _flatten_steps(recipe)
    _normalize_site_info(recipe)

    for step_name in _STEP_NAMES:
        if step_name in recipe and isinstance(recipe[step_name], dict):
            _normalize_step(recipe[step_name], step_name)

    if 'cart' in recipe and 'cart_add' not in recipe:
        recipe['cart_add'] = recipe.pop('cart')
    if 'order' in recipe and 'order_submit' not in recipe:
        recipe['order_submit'] = recipe.pop('order')

    return recipe


def _flatten_steps(recipe: dict):
    """steps 중첩 구조를 최상위로 풀기"""
    steps = recipe.pop('steps', None)
    if not steps or not isinstance(steps, dict):
        return
    for key, value in steps.items():
        if key not in recipe:
            recipe[key] = value


def _normalize_site_info(recipe: dict):
    """site_info에서 최상위 메타데이터 보완"""
    site_info = recipe.get('site_info', {})
    if not site_info:
        return
    if not recipe.get('site_url') and site_info.get('base_url'):
        recipe['site_url'] = site_info['base_url']
    if not recipe.get('encoding') and site_info.get('encoding'):
        recipe['encoding'] = site_info['encoding']
    if not recipe.get('site_name') and site_info.get('name'):
        recipe['site_name'] = site_info['name']


def _normalize_step(step: dict, step_name: str):
    """개별 단계 정규화"""
    _normalize_indicator(step)
    if step_name in ('search', 'search_result'):
        _normalize_parsing(step)
    if not step.get('content_type') and step.get('headers', {}).get('Content-Type'):
        step['content_type'] = step['headers']['Content-Type']


def _normalize_indicator(step: dict):
    """성공 판단 키 변형 → success_indicator로 통일"""
    indicator = None
    for alias in _INDICATOR_KEY_ALIASES:
        if alias in step:
            indicator = step.pop(alias)
            break
    if not indicator or not isinstance(indicator, dict):
        return

    ind_type = indicator.get('type', '')
    if ind_type in _INDICATOR_TYPE_MAP:
        indicator['type'] = _INDICATOR_TYPE_MAP[ind_type]
    if ind_type == 'url_contains' or indicator.get('url_contains'):
        indicator['type'] = 'redirect'
        if not indicator.get('value'):
            indicator['value'] = indicator.pop('url_contains', '')
    if indicator.get('text') and not indicator.get('value'):
        indicator['value'] = indicator.pop('text')

    step['success_indicator'] = indicator


def _normalize_parsing(step: dict):
    """검색 결과 파싱 정규화"""
    parsing = step.get('parsing', {})
    if not parsing:
        return
    for alias in _SELECTOR_KEY_ALIASES:
        if alias in parsing:
            if alias != 'selector':
                parsing['selector'] = parsing.pop(alias)
            break
    fields = parsing.get('fields', {})
    if fields:
        normalized_fields = {}
        for field_name, field_spec in fields.items():
            canonical_name = _FIELD_NAME_MAP.get(field_name, field_name)
            normalized_fields[canonical_name] = field_spec
        parsing['fields'] = normalized_fields
