#!/bin/bash
# 레시피 생성 → E2E 검증 → 실패 시 반복 (최대 10회)
#
# 사용법:
#   ./generate_and_validate.sh <site_url> <site_id> <username> <password> [site_name] [max_iterations]
#
# 예시:
#   ./generate_and_validate.sh https://bpm.geoweb.kr bpm_geoweb_kr REDACTED_ID REDACTED_PW "지오웹BPM" 5

set -e

SITE_URL="$1"
SITE_ID="$2"
USERNAME="$3"
PASSWORD="$4"
SITE_NAME="${5:-$SITE_ID}"
MAX_ITER="${6:-10}"

if [ -z "$SITE_URL" ] || [ -z "$SITE_ID" ] || [ -z "$USERNAME" ] || [ -z "$PASSWORD" ]; then
  echo "사용법: $0 <site_url> <site_id> <username> <password> [site_name] [max_iterations]"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# venv 활성화
if [ -f "venv/bin/activate" ]; then
  source venv/bin/activate
fi

echo "================================================================"
echo " 레시피 생성 + E2E 검증 루프"
echo " 사이트: $SITE_NAME ($SITE_URL)"
echo " 최대 반복: ${MAX_ITER}회"
echo "================================================================"

for i in $(seq 1 "$MAX_ITER"); do
  echo ""
  echo "████████████████████████████████████████████████████████████████"
  echo "  ITERATION $i / $MAX_ITER"
  echo "████████████████████████████████████████████████████████████████"

  # 1. 레시피 생성 (서브에이전트)
  echo ""
  echo ">>> 레시피 생성 중..."
  bash "$SCRIPT_DIR/recipe_generator.sh" "$SITE_URL" "$SITE_ID" "$USERNAME" "$PASSWORD" "$SITE_NAME"

  # 2. E2E 검증
  echo ""
  echo ">>> E2E 검증 중..."
  if python "$SCRIPT_DIR/e2e_validator.py" "$SITE_ID"; then
    echo ""
    echo "================================================================"
    echo " ✅ 성공! ($i회 만에 완료)"
    echo "================================================================"
    exit 0
  fi

  echo ""
  echo ">>> ❌ 검증 실패. 다음 iteration으로..."
  echo ">>> 프롬프트 수정이 필요하면 Ctrl+C로 중단하세요."
  echo ""

  # 잠시 대기 (사용자가 중단할 시간)
  sleep 3
done

echo ""
echo "================================================================"
echo " ⚠️  최대 반복 횟수 ($MAX_ITER) 도달. 수동 확인 필요."
echo "================================================================"
exit 1
