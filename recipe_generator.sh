#!/bin/bash
# 레시피 자동 생성 — claude -p 서브에이전트 실행
#
# 사용법:
#   ./recipe_generator.sh <site_url> <site_id> <username> <password> [site_name]
#
# 예시:
#   ./recipe_generator.sh https://example.com site_id username password "사이트명"

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

SITE_URL="$1"
SITE_ID="$2"
USERNAME="$3"
PASSWORD="$4"
SITE_NAME="${5:-$SITE_ID}"

if [ -z "$SITE_URL" ] || [ -z "$SITE_ID" ] || [ -z "$USERNAME" ] || [ -z "$PASSWORD" ]; then
  echo "사용법: $0 <site_url> <site_id> <username> <password> [site_name]"
  exit 1
fi

echo "================================================"
echo " 레시피 자동 생성: $SITE_NAME"
echo " URL: $SITE_URL"
echo " Site ID: $SITE_ID"
echo "================================================"

PROMPT=$(cat <<PROMPT_END
너는 도매 약품 사이트의 레시피를 생성하는 전문가다.

MCP 서버의 generate_recipe 프롬프트를 참조하여 아래 사이트의 레시피를 생성하라.
generate_recipe 프롬프트에 STEP 1~6 워크플로우가 상세히 안내되어 있다.

핵심 원칙:

■ 성능: screenshot은 최소한으로 (팝업 확인, 가격 없을 때, 최종 검증만)
  일반 탐색은 analyze_page_for_recipe()의 button_actions와 snapshot_page() 텍스트로 충분하다.

■ 분기 (중요!):
  analyze_page_for_recipe() 호출 후 button_actions를 확인하라.
  - button_actions에 api/ajax_urls가 있으면 → Fast Path: 바로 레시피 작성. 검색 1회만 실행.
  - button_actions에 api가 없으면 (SPA) → Deep Path: 버튼 클릭 + get_network_log()로 API 캡처.

■ 검색 파라미터: get_network_log()의 post_data/query를 그대로 레시피 params에 복사하라.
  빈 문자열로 넣지 마라. 실제 전송된 값을 그대로 넣어라.

■ 변수 규칙: {USERNAME}, {PASSWORD}, {KEYWORD}, {QUANTITY}, {PRODUCT_CODE}, {VEN_CD}, {VEN_NM}
  credentials.json의 site_params에 있는 키는 반드시 변수로 작성하라.

■ 가격: 검색 결과에 단가 컬럼이 없으면 → 약품 클릭 → 상세 패널에서 가격 API 캡처 → product_detail 섹션에 기록.

■ 장바구니: 담기→조회→삭제→비우기를 연속 실행하며 매 단계 get_network_log()로 API 캡처.
  주의: 장바구니가 비어있으면 삭제/비우기를 시도하지 마라. confirm/alert 팝업이 반복되면 즉시 중단하라.

■ 매출원장: 상세/요약 구분. 요약이면 상세 API도 찾아라 (행 클릭 또는 품목수불 메뉴).

■ JSON 응답은 items_path 확인 필수 (배열이면 "", 객체 안이면 해당 키)
■ AngularJS는 forms가 비어있으므로 반드시 네트워크 캡처에서 파라미터를 추출하라
■ 완성된 레시피를 save_recipe()로 저장하고 available_features를 기록하라

사이트: $SITE_URL
사이트 이름: $SITE_NAME
아이디: $USERNAME
비밀번호: $PASSWORD
저장할 site_id: $SITE_ID
PROMPT_END
)

# 로그 디렉토리 생성
mkdir -p "$SCRIPT_DIR/logs"
LOG_FILE="$SCRIPT_DIR/logs/${SITE_ID}_$(date +%Y%m%d_%H%M%S).log"

echo "로그 파일: $LOG_FILE"
echo ""

echo "$PROMPT" | claude -p \
  --verbose --output-format stream-json \
  --allowedTools "mcp__wholesale-tools__open_site,mcp__wholesale-tools__snapshot_page,mcp__wholesale-tools__snapshot_iframe,mcp__wholesale-tools__analyze_page_for_recipe,mcp__wholesale-tools__fill_input,mcp__wholesale-tools__click_element,mcp__wholesale-tools__submit_form,mcp__wholesale-tools__capture_form_submission,mcp__wholesale-tools__get_network_log,mcp__wholesale-tools__get_page_html,mcp__wholesale-tools__execute_js,mcp__wholesale-tools__screenshot,mcp__wholesale-tools__get_cookies,mcp__wholesale-tools__set_cookies,mcp__wholesale-tools__save_recipe,mcp__wholesale-tools__close_browser,mcp__wholesale-tools__generate_recipe_spec,mcp__wholesale-tools__get_recipe,mcp__wholesale-tools__list_sites,mcp__wholesale-tools__recipe_login,mcp__wholesale-tools__recipe_search,mcp__wholesale-tools__recipe_add_to_cart,mcp__wholesale-tools__recipe_view_cart,mcp__wholesale-tools__recipe_delete_from_cart,mcp__wholesale-tools__recipe_clear_cart,mcp__wholesale-tools__recipe_sales_ledger,mcp__wholesale-tools__share_recipe" \
  2>&1 | tee "$LOG_FILE"

echo ""
echo "================================================"
echo " 레시피 생성 완료: recipes/${SITE_ID}.json"
echo "================================================"
