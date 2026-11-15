#!/usr/bin/env bash
# W1 验收脚本：三步起跑后逐条 curl 验证主链路
# 用法: BASE=http://localhost:8080 INVITE=letmein ./scripts/acceptance-w1.sh
set -euo pipefail

BASE="${BASE:-http://localhost:8080}"
INVITE="${INVITE:-letmein}"
TAG="$(date +%s)"
PASS=0; FAIL=0

check() { # check <名称> <条件结果0/1>
  if [ "$2" -eq 0 ]; then PASS=$((PASS+1)); echo "✅ $1"; else FAIL=$((FAIL+1)); echo "❌ $1"; fi
}

echo "== 1. 健康检查 =="
HEALTH=$(curl -sf "$BASE/api/health") || true
[ "$(echo "$HEALTH" | grep -c '"ok"')" -ge 1 ]; check "health 200 + app 标识" $?

echo "== 2. invite 模式无码注册应 403 =="
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"ac$TAG\",\"email\":\"ac$TAG@t.io\",\"password\":\"password123\"}")
[ "$CODE" = "403" ]; check "无邀请码注册 403" $?

echo "== 3. 带码注册 + 登录 =="
TOKEN=$(curl -sf -X POST "$BASE/api/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"ac$TAG\",\"email\":\"ac$TAG@t.io\",\"password\":\"password123\",\"invite_code\":\"$INVITE\"}" \
  | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')
[ -n "$TOKEN" ]; check "注册返回 token" $?
AUTH="Authorization: Bearer $TOKEN"

echo "== 4. 创建渠道，key 掩码回显 =="
CH=$(curl -sf -X POST "$BASE/api/providers/channels" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"domain":"llm","name":"acct","base_url":"https://api.example.com/v1","key":"sk-secret-xyz9876"}')
echo "$CH" | grep -q "key_masked"; check "渠道创建返回 key_masked" $?
echo "$CH" | grep -q "sk-secret-xyz9876" && RC=1 || RC=0; check "响应无明文 key" $RC
CH_ID=$(echo "$CH" | sed -n 's/.*"id":\([0-9]*\).*/\1/p' | head -1)

echo "== 4.5 清理已有渠道（保证 Fake 回落路径稳定）=="
for CID in $(curl -sf "$BASE/api/providers/channels" -H "$AUTH" | sed -n 's/.*"id":\([0-9]*\).*/\1/p'); do
  curl -sf -X DELETE "$BASE/api/providers/channels/$CID" -H "$AUTH" > /dev/null || true
done

echo "== 5. 提交任务（FakeProvider，需 ENABLE_FAKE_PROVIDER=true）=="
TASK=$(curl -s -X POST "$BASE/api/tasks" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"type":"llm","prompt":"hello canvas","model":"fake-llm"}')
TASK_ID=$(echo "$TASK" | sed -n 's/.*"task":{"id":\([0-9]*\).*/\1/p')
if [ -n "$TASK_ID" ]; then
  check "任务提交入队" 0
else
  echo "$TASK" | grep -q "QUEUE_UNAVAILABLE" && echo "⏭️  SKIP 任务闭环（无 worker/Redis，QUEUE_UNAVAILABLE 行为正确）" || check "任务提交" 1
fi

if [ -n "$TASK_ID" ]; then
  echo "== 6. 等待 worker 消费（最长 20s）=="
  FOUND=1  # 1=未找到（失败语义），0=找到
  for i in $(seq 1 20); do
    SNAPSHOT=$(curl -sf "$BASE/api/tasks/$TASK_ID" -H "$AUTH" || true)
    echo "$SNAPSHOT" | grep -q '"completed"' && { FOUND=0; break; }
    sleep 1
  done
  check "任务被 worker 完成" $FOUND

  echo "== 7. cost_amount 已记账 =="
  SNAPSHOT=$(curl -sf "$BASE/api/tasks/$TASK_ID" -H "$AUTH")
  echo "$SNAPSHOT" | grep -q '"cost_amount"'; check "cost_amount 字段存在" $?
else
  echo "== 6/7. ⏭️  SKIP（无任务闭环环境）=="
fi

echo "== 8. 画布 CRUD =="
PID=$(curl -sf -X POST "$BASE/api/canvas/projects" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"title":"验收画布"}' | sed -n 's/.*"id":\([0-9]*\).*/\1/p' | head -1)
[ -n "$PID" ]; check "画布项目创建" $?

echo
echo "验收结果: $PASS 通过 / $FAIL 失败"
[ "$FAIL" -eq 0 ]
