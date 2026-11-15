"""中立模型预设目录（BYOK）：常用开放服务商的接入参考配置。

定位：CreaGen Canvas 是中立的平台——模型与能力由用户自行接入（自带 Key）。
本目录只提供「填表便利」：点选预设自动填 Base URL/默认模型/能力提示/获取 Key 入口，
不绑定任何单一服务商；自定义渠道始终完全支持。
"""

from __future__ import annotations

from typing import TypedDict


class ProviderPreset(TypedDict):
    id: str
    displayName: str
    domain: str
    baseUrl: str
    defaultModels: list[str]
    capabilities: list[str]
    keyUrl: str
    requiresKey: bool
    note: str


PROVIDER_PRESETS: list[ProviderPreset] = [
    # ---- LLM ----
    {
        "id": "deepseek",
        "displayName": "DeepSeek",
        "domain": "llm",
        "baseUrl": "https://api.deepseek.com",
        "defaultModels": ["deepseek-chat", "deepseek-reasoner"],
        "capabilities": [],
        "keyUrl": "https://platform.deepseek.com/api_keys",
        "requiresKey": True,
        "note": "reasoner 输出含思维链，将自动剥离只保留正文。",
    },
    {
        "id": "siliconflow",
        "displayName": "硅基流动 SiliconFlow",
        "domain": "llm",
        "baseUrl": "https://api.siliconflow.cn/v1",
        "defaultModels": [
            "deepseek-ai/DeepSeek-V3.2",
            "Qwen/Qwen3-32B",
            "zai-org/GLM-4.6",
        ],
        "capabilities": [],
        "keyUrl": "https://cloud.siliconflow.cn/account/ak",
        "requiresKey": True,
        "note": "聚合平台，模型目录连通后自动同步（90+ 模型）。",
    },
    {
        "id": "moonshot",
        "displayName": "月之暗面 Kimi",
        "domain": "llm",
        "baseUrl": "https://api.moonshot.cn/v1",
        "defaultModels": ["kimi-k2-0905-preview", "moonshot-v1-8k"],
        "capabilities": [],
        "keyUrl": "https://platform.moonshot.cn/console/api-keys",
        "requiresKey": True,
        "note": "",
    },
    {
        "id": "zhipu",
        "displayName": "智谱 GLM",
        "domain": "llm",
        "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
        "defaultModels": ["glm-4.6", "glm-4-flash"],
        "capabilities": [],
        "keyUrl": "https://open.bigmodel.cn/usercenter/apikeys",
        "requiresKey": True,
        "note": "glm-4-flash 免费额度较大，适合提示词优化等轻量任务。",
    },
    {
        "id": "qwen",
        "displayName": "通义千问（兼容模式）",
        "domain": "llm",
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "defaultModels": ["qwen-max", "qwen-plus", "qwen-turbo"],
        "capabilities": [],
        "keyUrl": "https://bailian.console.aliyun.com/?apiKey=1",
        "requiresKey": True,
        "note": "百炼控制台获取 API Key。",
    },
    {
        "id": "minimax",
        "displayName": "MiniMax",
        "domain": "llm",
        "baseUrl": "https://api.minimaxi.com/v1",
        "defaultModels": ["MiniMax-M2.7"],
        "capabilities": [],
        "keyUrl": "https://platform.minimaxi.com/",
        "requiresKey": True,
        "note": "M2.7 输出含 <think> 段，将自动剥离。",
    },
    {
        "id": "openai",
        "displayName": "OpenAI",
        "domain": "llm",
        "baseUrl": "https://api.openai.com/v1",
        "defaultModels": ["gpt-4o", "gpt-4o-mini"],
        "capabilities": [],
        "keyUrl": "https://platform.openai.com/api-keys",
        "requiresKey": True,
        "note": "",
    },
    {
        "id": "openrouter",
        "displayName": "OpenRouter（聚合）",
        "domain": "llm",
        "baseUrl": "https://openrouter.ai/api/v1",
        "defaultModels": ["openai/gpt-4o-mini", "deepseek/deepseek-chat"],
        "capabilities": [],
        "keyUrl": "https://openrouter.ai/keys",
        "requiresKey": True,
        "note": "一个 Key 聚合数十家服务商的模型。",
    },
    {
        "id": "ollama",
        "displayName": "Ollama（本地）",
        "domain": "llm",
        "baseUrl": "http://localhost:11434/v1",
        "defaultModels": ["qwen3:8b", "deepseek-r1:8b"],
        "capabilities": [],
        "keyUrl": "https://ollama.com/download",
        "requiresKey": False,
        "note": "本机部署 Ollama 后无需密钥；先 ollama pull 模型。",
    },
    # ---- 图片 ----
    {
        "id": "siliconflow",
        "displayName": "硅基流动 SiliconFlow",
        "domain": "image",
        "baseUrl": "https://api.siliconflow.cn/v1",
        "defaultModels": [
            "Kwai-Kolors/Kolors",
            "Qwen/Qwen-Image",
            "Qwen/Qwen-Image-Edit-2509",
        ],
        "capabilities": ["edit_ref"],
        "keyUrl": "https://cloud.siliconflow.cn/account/ak",
        "requiresKey": True,
        "note": "Kolors 免费档；Qwen-Image-Edit 为参考图编辑（整图语义，无蒙版）。",
    },
    {
        "id": "openai",
        "displayName": "OpenAI",
        "domain": "image",
        "baseUrl": "https://api.openai.com/v1",
        "defaultModels": ["gpt-image-1"],
        "capabilities": ["edit"],
        "keyUrl": "https://platform.openai.com/api-keys",
        "requiresKey": True,
        "note": "支持 /images/edits 蒙版局部重绘。",
    },
    {
        "id": "minimax",
        "displayName": "MiniMax 图片",
        "domain": "image",
        "baseUrl": "https://api.minimaxi.com/v1",
        "defaultModels": ["image-01"],
        "capabilities": [],
        "keyUrl": "https://platform.minimaxi.com/",
        "requiresKey": True,
        "note": "",
    },
    # ---- 视频 ----
    {
        "id": "minimax",
        "displayName": "MiniMax 海螺视频",
        "domain": "video",
        "baseUrl": "https://api.minimaxi.com/v1",
        "defaultModels": ["MiniMax-Hailuo-2.3-Fast", "MiniMax-Hailuo-2.3"],
        "capabilities": [],
        "keyUrl": "https://platform.minimaxi.com/",
        "requiresKey": True,
        "note": "时长 6/10 秒，分辨率 768P/1080P。",
    },
    # ---- 音频 ----
    {
        "id": "openai",
        "displayName": "OpenAI 语音",
        "domain": "audio",
        "baseUrl": "https://api.openai.com/v1",
        "defaultModels": ["tts-1", "tts-1-hd"],
        "capabilities": [],
        "keyUrl": "https://platform.openai.com/api-keys",
        "requiresKey": True,
        "note": "文字转语音（/audio/speech）。",
    },
]
