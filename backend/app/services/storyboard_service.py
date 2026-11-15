"""分镜服务（0.8.0-M1）：剧本 → 分镜 JSON 的 LLM 拆分与解析。"""

import json
import re

SEGMENT_KEYS = ("prompt", "camera_motion", "mood", "audio_cue")


def split_prompts(
    short_description: str,
    story_summary: str,
    segments_count: int,
    duration: int,
) -> tuple[str, str]:
    """构造分镜拆分的 (system, user) 提示词。"""
    system = (
        "你是顶级短视频分镜师。把用户的剧本/创意简报拆分为连续的"
        f" {segments_count} 个分镜段，每段约 {duration} 秒，段与段之间情节连续。"
        "先提炼角色档案：每个主要角色的英文外观锚定描述（含年龄/发型/服装/体型），"
        "供视频生成保持人物一致。"
        '输出 JSON 对象：{"characters": [{"name": "角色名", "description": "英文外观锚定描述"}],'
        ' "segments": [{"prompt": "该段画面的英文视频提示词，40-80 词，含主体/环境/光线）",'
        ' "camera_motion": "镜头运动（中文短语，如 缓慢推近）", "mood": "情绪氛围（中文）",'
        ' "audio_cue": "音频提示（中文）"}]}。'
        "分段提示词不得包含对白/台词/说话内容（视频模型无语音能力），人物只描述动作与表情；"
        "同一角色的外观描述在各段保持一致。"
        "在分段提示词中引用已定义角色时，一律使用 @角色名 语法（如 @阿珍），"
        "系统会在生成时自动展开角色的外观描述。"
        "只输出 JSON，不要解释、不要 markdown 围栏。"
    )
    user = f"剧本/创意简报：{short_description}"
    if story_summary:
        user += f"\n\n故事摘要：{story_summary}"
    user += f"\n\n请拆分为 {segments_count} 段。"
    return system, user


def parse_segments(raw: str, segments_count: int) -> tuple[list[dict], list[dict]]:
    """解析 LLM 输出为 (分镜列表, 角色档案)：剥围栏 → JSON → 校验字段；不足补位、超出截断。

    兼容两种输出形态：裸数组（旧契约，characters=[]）与
    {"characters": [...], "segments": [...]}（v2）。
    抛 ApiError（STORYBOARD_PARSE_FAILED）当完全无法解析。
    """
    from backend.app.core.errors import ApiError

    text = (raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    # 定位 JSON 主体：优先对象（v2），回退数组（旧契约）
    obj_start = text.find("{")
    arr_start = text.find("[")
    if obj_start != -1 and (arr_start == -1 or obj_start < arr_start):
        start, end = obj_start, text.rfind("}")
    else:
        start, end = arr_start, text.rfind("]")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ApiError(
            "分镜拆分结果无法解析，请重试",
            code="STORYBOARD_PARSE_FAILED",
            status_code=502,
        ) from e

    characters: list[dict] = []
    if isinstance(data, dict):
        raw_chars = data.get("characters")
        if isinstance(raw_chars, list):
            for c in raw_chars:
                if isinstance(c, dict) and c.get("name"):
                    characters.append(
                        {
                            "name": str(c.get("name") or "").strip(),
                            "description": str(c.get("description") or "").strip(),
                        }
                    )
        data = data.get("segments")
    if not isinstance(data, list):
        raise ApiError("分镜拆分结果不是数组", code="STORYBOARD_PARSE_FAILED", status_code=502)

    out: list[dict] = []
    for _i, item in enumerate(data[:segments_count]):
        if not isinstance(item, dict):
            continue
        seg: dict = {"segment_index": len(out) + 1}
        for key in SEGMENT_KEYS:
            val = item.get(key)
            seg[key] = str(val).strip() if val else ""
        if not seg["prompt"]:
            seg["prompt"] = f"第 {len(out) + 1} 段（待补充）"
        out.append(seg)
    while len(out) < segments_count:
        out.append(
            {
                "segment_index": len(out) + 1,
                "prompt": f"第 {len(out) + 1} 段（待补充）",
                "camera_motion": "",
                "mood": "",
                "audio_cue": "",
            }
        )
    return out, characters
