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
- 매 단계마다 반드시 screenshot()으로 화면을 직접 확인하라
- 버튼/링크를 발견하면 해당 요소의 코드를 추적하여 실제 API를 확정하라
- 로그인 후 팝업이 있으면 반드시 닫아라
- 검색 결과 screenshot에서 가격(단가) 컬럼이 없으면 반드시 약품명을 클릭하라.
  클릭 후 나타나는 제품정보 패널을 screenshot으로 확인하고, 가격 API를 get_network_log로 캡처하여 레시피에 반영하라. 이 단계를 건너뛰면 안 된다.
- 장바구니는 담기→조회→삭제→비우기를 한 세션에서 연속 실행하며 모든 네트워크를 캡처하라
- JSON 응답은 items_path 확인 필수 (배열이면 "", 객체 안이면 해당 키)
- AngularJS 사이트는 forms가 비어있으므로 반드시 네트워크 캡처에서 파라미터를 추출하라
- 완성된 레시피를 save_recipe()로 저장하고 available_features를 기록하라

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
  --verbose \
  --allowedTools "mcp__wholesale-tools__open_site,mcp__wholesale-tools__snapshot_page,mcp__wholesale-tools__snapshot_iframe,mcp__wholesale-tools__analyze_page_for_recipe,mcp__wholesale-tools__fill_input,mcp__wholesale-tools__click_element,mcp__wholesale-tools__submit_form,mcp__wholesale-tools__capture_form_submission,mcp__wholesale-tools__get_network_log,mcp__wholesale-tools__get_page_html,mcp__wholesale-tools__execute_js,mcp__wholesale-tools__screenshot,mcp__wholesale-tools__get_cookies,mcp__wholesale-tools__set_cookies,mcp__wholesale-tools__save_recipe,mcp__wholesale-tools__close_browser,mcp__wholesale-tools__generate_recipe_spec,mcp__wholesale-tools__get_recipe,mcp__wholesale-tools__list_sites,Read" \
  2>&1 | tee "$LOG_FILE"

echo ""
echo "================================================"
echo " 레시피 생성 완료: recipes/${SITE_ID}.json"
echo "================================================"
