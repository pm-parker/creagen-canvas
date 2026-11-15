---
name: creagen-canvas
description: 使用 CreaGen Canvas（自托管 AI 创作画布）完成复杂的多图/多视频串联创作：文生图、图生图、图生视频（首帧参考）、尾帧接力续拍、画布节点图组织（定点/连线）。当用户要求 生图/生成视频/文生视频/图生视频/首帧参考/尾帧接力/续拍/多图组/角色设定图/分镜/串联创作/AI 画画/创作到画布，或提到 CreaGen、creagen-canvas 时使用。需要实例地址（BASE_URL）与 X-API-Key（用户在 Web 端「设置 → Agent API」生成）。
version: "2.1"
updated: "2026-10-08"
---

# CreaGen Canvas 串联创作技能（零基础入门）

自托管 AI 创作画布。你的产出（图/视频/音频）会成为**画布上的节点**，用户在 Web 端实时看到你的创作图。

## 心智模型（4 个概念，先建立再动手）

- **project（画布）**：一张无限画布，承载一次创作主题
- **node（节点）**：画布上的一个创作单元——图/视频/音频/文字卡，有位置 (x,y)
- **edge（连线）**：节点间的创作脉络——**图→视频 = 首帧参考，图→图 = 参考图**（用户看连线即懂你的创作逻辑）
- **task（任务）**：一次生成。generate 返回 task_id，轮询到 completed 取 output_url

## 快速开始（每次会话的前两步）

```bash
# ① 自检（失败→让用户去 Web 端「设置→Agent API」拿 key）
curl -s -H "X-API-Key: $KEY" $BASE_URL/api/agent/v1/me
# ② 找/建画布（一次创作一个 project）
curl -s -H "X-API-Key: $KEY" $BASE_URL/api/agent/v1/projects
curl -X POST .../api/agent/v1/projects -d '{"title":"主题名"}'
```

**核心动作**——生成并定点投放（结果自动落画布节点）：

```bash
curl -X POST $BASE_URL/api/agent/v1/generate -H "X-API-Key: $KEY" -d '{
  "project_id": $PID, "type": "image", "message": "提示词",
  "params": {"image_count": 4, "x": 0, "y": 0}
}'
# → task_id → 每 8 秒 GET /api/agent/v1/tasks/{id} → completed 后 output_url 即站内路径
```

## 教科书工作流（复杂串联创作的三种范式）

### 范式 A：多图组 → 多视频（最常用的并联结构）

适合"一套角色/场景设定 → 各自出视频"。

```
布局约定：图片行 y=0（每张间隔 x+=400）；对应视频行 y=450；连线 竖向对齐
```

1. **出图组**（一次多角度）：`generate type=image, params:{image_count:4, x:0, y:0}`——4 张备选
2. 轮询完成后，从 `output_url` 里挑选满意的（问用户或按描述选）
3. **逐个出视频（串行！等前一个 completed 再发下一个）**（每个用选定图为首帧）：

```bash
curl -X POST .../generate -d '{
  "project_id": $PID, "type": "video", "message": "这一镜的运动描述",
  "params": {"duration": 5, "resolution": "480x832",          # 竖屏；横屏省略
             "reference_urls": ["/outputs/images/选定图.png"], # 首帧
             "x": 400, "y": 450}
}'
```

4. **连线**（把创作脉络画出来）：

```bash
curl -X POST $BASE_URL/api/agent/v1/projects/$PID/edges -d '{"from_node_id": 图id, "to_node_id": 视频id}'
```
视频耗时 1-15 分钟。**纪律：一次只跑一个视频任务**——同渠道并发会挤爆 GPU 导致双双超时失败（实证），闸门也只放行有限并发；等 `completed` 再发下一个。跨渠道（如本地+算力仓）可各跑一个。

### 范式 B：尾帧接力续拍（串联结构——连续剧情）

适合"一个镜头接一个镜头讲下去"。核心工具：**取帧**。

1. 出第一镜视频 V1（文生或图生）
2. **取 V1 末帧**作为 V2 的首帧（无缝续接）：

```bash
curl -X POST $BASE_URL/api/agent/v1/frame -d '{"url": "/outputs/videos/V1.mp4", "time": -1}'
# → {"url": "/outputs/media/frame_xxx.png"} ← 这就是 V2 的 reference_urls
```

3. V2 用该帧生成（prompt 写"承接上一镜…"）→ 再取 V2 末帧 → V3…循环
4. 每镜 `x` 递增 400、y 相同（横向故事带）；V1→V2→V3 依次连线

### 范式 C：迭代加工链（图生图精修）

图不满意不重画，**拿它当参考改**：

```bash
generate type=image, params: {"reference_urls": [上一步图], "x": 下一个位置}
```
prompt 写修改指令（"改为夜景，保持构图"）。链式重复直到用户满意；全程连线，画布上呈现演进史。

## 画布组织纪律（让用户一眼看懂）

- **布局**：同范式节点对齐（横排=并列镜头，纵排=图→视频关系）；新主题换一行（y += 900）
- **连线必须建**：edge 是你给用户看的"创作说明书"
- **文字卡**：复杂创作先建 text 节点写分镜/计划（`POST /projects/{id}/nodes`），用户能预览你的思路
- 节点可 PATCH（改题/挪位）/ DELETE（清理废案，连线自动清）

## 模型选择（优先级，严格按序）

1. 用户本轮明确指定
2. **系统默认**（`GET /models` 里 `is_default:true`——用户在 Web 端配的，尊重它）
3. `is_free:true` 免费模型（本地 GPU：`z-image-turbo` 秒级、`minimax-h3-video` 视频，零成本）

同名模型多渠道（如双 H3）：`channel_id` 一等字段精确指定（从 `/models` 取）。LLM 任务结果看 `content` 字段。

## 纪律（硬规则）

1. **reference_urls 必须是站内 `/outputs/` 路径**（上一步 output_url 原样回填；视频末帧先 `/frame` 取）
2. 失败必须如实转告 error 原文（平台禁止静默降级）；429 等 60 秒；401 请用户轮换 key
3. 产物链接 = `BASE_URL + output_url`，直接给用户
4. **视频任务必须串行**：同一渠道同时只发 1 个（等 completed 再发下一个）——连发会挤爆 GPU 全部超时失败（2026-10-08 两批任务实证），这不是建议是硬规则
5. 模型名只从 `/models` 列表选，不编造
6. 用户没给 key 就停（绝不猜测/复用）

## API 速查

| 端点 | 用途 |
|---|---|
| GET /me | key 自检 |
| GET/POST /projects | 画布 列/建 |
| GET /models?domain= | 可用模型（is_default/is_free/channel_id/defaults） |
| POST /generate | 生成（type: image/video/llm/audio；params: x,y,duration,resolution,reference_urls,image_count,voice） |
| GET /tasks?limit=&status= | 任务列表（跨会话记忆） |
| GET /tasks/{id} | 轮询（output_url；llm 看 content） |
| GET /projects/{id}/nodes | 节点+连线图回读（接力素材恢复） |
| POST /projects/{id}/nodes · PATCH/DELETE …/nodes/{id} | 文字卡/占位/改题/挪位/清理 |
| POST /projects/{id}/edges | 连线（参考/首帧语义） |
| POST /frame | 取帧（time=-1 末帧——接力核心） |
| GET/POST /channels | 列/接入渠道+模型（key 服务端加密；纪律见上） |

完整错误码/协议细节：仓库 docs/agent-api.md。Web 端「设置 → Agent API Key → 接入指南」可复制系统指令。
