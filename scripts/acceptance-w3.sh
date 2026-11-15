#!/usr/bin/env bash
# W3 验收：生成闭环（Fake 回落）+ 失败归因 + 渠道解析语义
# 前置: .env 中 ENABLE_FAKE_PROVIDER=true
# 用法: BASE=http://localhost:18080 INVITE=letmein ./scripts/acceptance-w3.sh
set -euo pipefail

BASE="${BASE:-http://localhost:8080}"
INVITE="${INVITE:-letmein}"
TAG="w3$(date +%s)"
PASS=0; FAIL=0
check() { if [ "$2" -eq 0 ]; then PASS=$((PASS+1)); echo "✅ $1"; else FAIL=$((FAIL+1)); echo "❌ $1"; fi; }

TOKEN=$(curl -sf -X POST "$BASE/api/auth/register" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$TAG\",\"email\":\"$TAG@t.io\",\"password\":\"password123\",\"invite_code\":\"$INVITE\"}" \
  | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')
AUTH="Authorization: Bearer $TOKEN"
[ -n "$TOKEN" ]; check "注册登录" $?

echo "== 1. Fake 回落提交→worker 完成=="
TASK=$(curl -s --max-time 15 -X POST "$BASE/api/tasks" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"type":"llm","prompt":"写一句开场白","model":"fake-llm"}')
TASK_ID=$(echo "$TASK" | sed -n 's/.*"task":{"id":\([0-9]*\).*/\1/p')
[ -n "$TASK_ID" ]; check "任务提交入队" $?

FOUND=1
for i in $(seq 1 20); do
  SNAPSHOT=$(curl -sf "$BASE/api/tasks/$TASK_ID" -H "$AUTH" || true)
  echo "$SNAPSHOT" | grep -q '"completed"' && { FOUND=0; break; }
  sleep 1
done
check "worker 完成（Fake 回落）" $FOUND

echo "== 2. 失败归因字段存在 =="
echo "$SNAPSHOT" | grep -q '"error_msg"'; check "error_msg 字段" $?

echo "== 3. SSE 终态补发（订阅晚于完成）=="
SSE=$(curl -sf --max-time 5 "$BASE/api/tasks/stream?task_id=$TASK_ID" -H "$AUTH" || true)
echo "$SSE" | grep -q "completed"; check "SSE 补发 completed 帧" $?

echo "== 4. 无渠道域提交（Fake 关闭语义不适用 llm 时仍回落）=="
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/tasks" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"type":"audio","prompt":"hello","model":"fake-audio"}')
{ [ "$CODE" = "200" ] || [ "$CODE" = "422" ]; }; check "audio 域提交语义正确($CODE)" $?

echo "== 5. 画布挂生成结果（节点+连线同步）=="
PID=$(curl -sf -X POST "$BASE/api/canvas/projects" -H "$AUTH" -H 'Content-Type: application/json' -d '{"title":"W3验收"}' | sed -n 's/.*"id":\([0-9]*\).*/\1/p' | head -1)
NODES='{"nodes":[{"type":"text","title":"源","position_x":0,"position_y":0,"metadata_json":"{\"prompt\":\"p\",\"client_id\":\"src\"}"}]}'
R=$(curl -sf -X PUT "$BASE/api/canvas/projects/$PID/nodes" -H "$AUTH" -H 'Content-Type: application/json' -d "$NODES")
SRC_ID=$(echo "$R" | sed -n 's/.*"id":\([0-9]*\).*/\1/p' | head -1)
GEN_NODE='{"nodes":[{"type":"text","title":"源","position_x":0,"position_y":0,"metadata_json":"{\"client_id\":\"src\"}"},{"type":"text","title":"回流","position_x":320,"position_y":0,"metadata_json":"{\"client_id\":\"gen\",\"prompt\":\"生成内容\"}"}]}'
R=$(curl -sf -X PUT "$BASE/api/canvas/projects/$PID/nodes" -H "$AUTH" -H 'Content-Type: application/json' -d "$GEN_NODE")
GEN_ID=$(echo "$R" | sed -n 's/.*"id":\([0-9]*\).*"gen".*/\1/p' | head -1)
curl -sf -X PUT "$BASE/api/canvas/projects/$PID/connections" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"connections\":[{\"from_node_id\":$SRC_ID,\"to_node_id\":$GEN_ID}]}" > /dev/null
READBACK=$(curl -sf "$BASE/api/canvas/projects/$PID" -H "$AUTH")
echo "$READBACK" | grep -q "生成内容"; check "回流节点 prompt 回读" $?
echo "$READBACK" | grep -q "from_node_id"; check "回流连线存在" $?

echo
echo "W3 验收结果: $PASS 通过 / $FAIL 失败"
[ "$FAIL" -eq 0 ]
