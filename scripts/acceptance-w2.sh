#!/usr/bin/env bash
# W2 验收：SPA 托管 + 画布数据闭环（fnos/docker 环境直接跑）
# 用法: BASE=http://localhost:18080 INVITE=letmein ./scripts/acceptance-w2.sh
set -euo pipefail

BASE="${BASE:-http://localhost:8080}"
INVITE="${INVITE:-letmein}"
TAG="w2$(date +%s)"
PASS=0; FAIL=0
check() { if [ "$2" -eq 0 ]; then PASS=$((PASS+1)); echo "✅ $1"; else FAIL=$((FAIL+1)); echo "❌ $1"; fi; }

echo "== 1. SPA 深链回落 =="
DEEP=$(curl -s "$BASE/canvas/42")
echo "$DEEP" | grep -q "<div id=\"root\">" && RC=0 || RC=1
check "深链 /canvas/42 回落 index.html" $?

echo "== 2. 静态资源存在（构建产物非占位）=="
ASSET=$(echo "$DEEP" | grep -o '/assets/[^"]*\.js' | head -1)
[ -n "$ASSET" ]; check "index.html 引用 /assets/*.js" $?
curl -sf -o /dev/null "$BASE$ASSET"; check "资源可加载 $ASSET" $?
MISSING=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/assets/nonexistent-$TAG.js")
[ "$MISSING" = "404" ]; check "缺失资源 404 不回落" $?

echo "== 3. API 命名空间不回落 =="
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/nope")
[ "$CODE" = "404" ]; check "/api/* 404 为 JSON 不回落" $?

echo "== 4. 注册登录 =="
TOKEN=$(curl -sf -X POST "$BASE/api/auth/register" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$TAG\",\"email\":\"$TAG@t.io\",\"password\":\"password123\",\"invite_code\":\"$INVITE\"}" \
  | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')
[ -n "$TOKEN" ]; check "注册返回 token" $?
AUTH="Authorization: Bearer $TOKEN"

echo "== 5. 建项目 + 节点/连线同步回读 =="
PID=$(curl -sf -X POST "$BASE/api/canvas/projects" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"title":"W2验收画布"}' | sed -n 's/.*"id":\([0-9]*\).*/\1/p' | head -1)
[ -n "$PID" ]; check "项目创建" $?

NODES='{"nodes":[{"type":"text","title":"文案","position_x":100,"position_y":100,"metadata_json":"{\"prompt\":\"开场文案\",\"client_id\":\"uuid-a\"}"},{"type":"image","title":"主图","position_x":400,"position_y":100,"metadata_json":"{\"prompt\":\"产品图\",\"client_id\":\"uuid-b\"}"}]}'
R=$(curl -sf -X PUT "$BASE/api/canvas/projects/$PID/nodes" -H "$AUTH" -H 'Content-Type: application/json' -d "$NODES")
ID_A=$(echo "$R" | sed -n 's/.*"id":\([0-9]*\).*"uuid-a".*/\1/p' | head -1)
ID_B=$(echo "$R" | sed -n 's/.*"id":\([0-9]*\).*"uuid-b".*/\1/p' | head -1)
[ -n "$ID_A" ] && [ -n "$ID_B" ]; check "两节点同步成功($ID_A,$ID_B)" $?

CONS="{\"connections\":[{\"from_node_id\":$ID_A,\"to_node_id\":$ID_B}]}"
curl -sf -X PUT "$BASE/api/canvas/projects/$PID/connections" -H "$AUTH" -H 'Content-Type: application/json' -d "$CONS" > /dev/null
READBACK=$(curl -sf "$BASE/api/canvas/projects/$PID" -H "$AUTH")
echo "$READBACK" | grep -q '"from_node_id":'"$ID_A"; check "连线回读一致" $?
echo "$READBACK" | grep -q '开场文案'; check "metadata(prompt) 回读一致" $?

echo
echo "W2 验收结果: $PASS 通过 / $FAIL 失败"
[ "$FAIL" -eq 0 ]
