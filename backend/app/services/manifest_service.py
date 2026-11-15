"""制片清单服务：模板、填槽、校验、闸门语义。

闸门规则（机器可判定，全部来自《周三》实战教训）：
- 8 类槽位各有 kind 对应的填充校验
- 剧本槽（type=script）触发 G1 校验：
  语速带（台词字数 vs 时长，中文 2-4.6 字/s）
  黑名单空间词（"对面/递给/推给"召唤第二人 → 必须双人镜才允许）
  匀速检测（时长方差 < 1.5 打回）
  道具覆盖（剧本提到的叙事道具必须在 props 槽登记）
- 探针裁决：dialogue_route 必须是 native|tts 之一（不开"未裁决"进工作流）
- strict=True 时 generate(video)/compose 提交前强制 ready，否则 422 MANIFEST_NOT_READY
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

# ---------- 内置模板（一集/一部剧标准开工清单） ----------

MANIFEST_TEMPLATE = [
    {"key": "style_bible", "label": "风格圣经", "kind": "text",
     "required": True,
     "desc": "色调/光线/渲染风格/画幅，所有 prompt 共同引用"},
    {"key": "characters", "label": "人物资产簇", "kind": "asset_list",
     "required": True, "min": 1,
     "desc": "每人: 正/侧/背/手 四卡齐 + 锚描述; 值=画布节点id数组"},
    {"key": "scenes", "label": "场景锚", "kind": "asset_list",
     "required": True, "min": 1,
     "desc": "一场景一锚; 值=画布节点id数组"},
    {"key": "props", "label": "叙事道具锚", "kind": "asset_list",
     "required": False, "min": 0,
     "desc": "剧本关键道具(婚戒/信/刀...)单独锚; 值=画布节点id数组"},
    {"key": "script", "label": "拍摄剧本", "kind": "script",
     "required": True,
     "desc": "shots JSON: [{cid,dur,weight,dialogue,desc,prompt}] — 触发 G1 校验"},
    {"key": "audio_plan", "label": "音频预案", "kind": "text",
     "required": True,
     "desc": "BGM 来源(站内路径/合成方案) + 台词路线(与探针联动)"},
    {"key": "delivery", "label": "交付规格", "kind": "text",
     "required": True,
     "desc": "画幅/单集时长/平台限制"},
    {"key": "probes", "label": "开工探针", "kind": "probe",
     "required": True,
     "desc": "台词路线裁决(native/tts) + 风格一致性 + 身份绑定"},
]

# 空间召唤词黑名单（《周三》F1 三次踩坑: "推到对面"召唤出第二双手）
SPACIAL_SUMMON_WORDS = ["对面", "递给", "推给", "递到", "推到", "交给对面", "隔桌递", "进门"]

# 语速带（中文口播, 字/秒; 低于 2 念不完, 高于 4.6 赶台词）
SPEED_MIN, SPEED_MAX = 2.0, 4.6
# 匀速判定: 全片时长方差低于此值打回（叙事密度失衡）
DUR_VAR_MIN = 1.5
# 双人镜关键词（黑名单词仅对单人镜报错）
DUAL_PERSON_HINTS = ["双人", "两人", "过肩", "正反打", "同框"]
WAIVE_LIMIT = 3  # 同一 soft 规则被豁免次数上限, 达到即自动升级 hard


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_schema() -> list[dict]:
    return [dict(s) for s in MANIFEST_TEMPLATE]


def parse_shots(script_text: str) -> list[dict]:
    """剧本槽值 → shots 列表。兼容: JSON 字符串 / python 字面量降级不了就 []。"""
    if not script_text or not script_text.strip():
        return []
    try:
        data = json.loads(script_text)
    except (ValueError, TypeError):
        return []
    if isinstance(data, dict):
        data = data.get("shots") or []
    return data if isinstance(data, list) else []


def g1_validate(shots: list[dict], props_declared: set[str] | None = None) -> dict:
    """剧本闸。返回 {ok, problems: [{cid, rule, detail}]}。

    全部机器可判定；problems 为空才放行。
    """
    problems: list[dict] = []
    if not shots:
        return {"ok": False, "problems": [{"cid": "-", "rule": "empty",
                                           "detail": "剧本为空或不可解析"}]}
    durs = []
    for s in shots:
        cid = s.get("cid") or s.get("id") or "?"
        try:
            dur = float(s.get("dur") or 0)
        except (TypeError, ValueError):
            dur = 0.0
        durs.append(dur)
        if dur <= 0:
            problems.append({"cid": cid, "rule": "dur_missing", "detail": "时长缺失"})
            continue
        if dur > 15.0:
            problems.append({"cid": cid, "rule": "dur_over_15s",
                             "detail": f"H3 单段上限 15s, 实际 {dur}s"})
        dlg = str(s.get("dialogue") or "")
        desc = str(s.get("desc") or "")
        if dlg:
            n = len(re.sub(r"[，。！？、\s]", "", dlg))
            sps = n / dur
            if sps < SPEED_MIN or sps > SPEED_MAX:
                problems.append({
                    "cid": cid, "rule": "speed_band",
                    "detail": f"台词 {n} 字 / {dur}s = {sps:.1f}字/s, 允许 {SPEED_MIN}-{SPEED_MAX}",
                })
        # 空间召唤词: 单人镜报错
        joined = desc + " " + str(s.get("prompt") or "")
        is_dual = any(h in joined for h in DUAL_PERSON_HINTS)
        if not is_dual:
            for w in SPACIAL_SUMMON_WORDS:
                if w in joined:
                    problems.append({
                        "cid": cid, "rule": "spacial_summon",
                        "detail": f"单人镜含空间召唤词「{w}」— 会召唤第二主体, "
                                  f"改纯单人操作动词或改双人镜",
                    })
                    break
    # 匀速检测
    if len(durs) >= 3:
        mean = sum(durs) / len(durs)
        var = sum((d - mean) ** 2 for d in durs) / len(durs)
        if var < DUR_VAR_MIN:
            problems.append({
                "cid": "-", "rule": "monotone_duration",
                "detail": f"全片时长方差 {var:.2f} < {DUR_VAR_MIN} — 匀速叙事, "
                          f"峰值镜拆多镜/过渡镜压缩",
            })
    # 道具覆盖: 剧本 desc 提到的道具词必须在 props 登记过
    if props_declared is not None:
        for s in shots:
            cid = s.get("cid") or "?"
            for p in (s.get("props") or []):
                if str(p) not in props_declared:
                    problems.append({
                        "cid": cid, "rule": "prop_not_declared",
                        "detail": f"道具「{p}」未在 props 槽登记锚",
                    })
    return {"ok": not problems, "problems": problems}


def probes_ready(probes: dict) -> tuple[bool, list[str]]:
    """探针裁决是否完成。"""
    missing = []
    if probes.get("dialogue_route") not in ("native", "tts"):
        missing.append("台词路线未裁决 (dialogue_route: native|tts)")
    if not probes.get("style_ok"):
        missing.append("风格一致性探针未过 (style_ok)")
    if not probes.get("identity_ok"):
        missing.append("身份绑定探针未过 (identity_ok)")
    return (not missing), missing


def readiness(manifest_row) -> dict:
    """完整就绪判定。返回 {ready, completeness, missing:[...], g1:{...}}"""
    data = manifest_row.to_dict()
    schema = {s["key"]: s for s in data["schema"]} or {s["key"]: s for s in load_schema()}
    slots = data["slots"]
    missing: list[str] = []
    filled = 0
    required_keys = [k for k, s in schema.items() if s.get("required")]
    for key in required_keys:
        st = slots.get(key) or {}
        if st.get("status") not in ("filled", "passed"):
            missing.append(f"{schema[key].get('label') or key} 未填")
        else:
            filled += 1
            # script 槽: G1 校验必须 ok
            if schema[key].get("kind") == "script":
                g1 = g1_validate(parse_shots(str(st.get("value") or "")),
                                 props_declared=None)
                if not g1["ok"]:
                    missing.append(
                        "剧本 G1 闸未过: "
                        + "; ".join(f"{p['cid']}:{p['rule']}" for p in g1["problems"][:5]))
            # probe 槽: 探针裁决
            if schema[key].get("kind") == "probe":
                ok, miss = probes_ready(data["probes"])
                if not ok:
                    missing.extend(miss)
    total = len(required_keys)
    return {
        "ready": not missing,
        "completeness": round(filled / total, 2) if total else 0.0,
        "missing": missing,
    }

# ==================== v2: 证据闸 + 豁免日志 + 分层信任 ====================

def _chk_anchor(value, task_lookup):
    """锚图证据: 值里至少一个 /outputs/images/img_*.png 路径。"""
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    paths = re.findall(r"/outputs/images/img_\d+_[0-9a-f]+\.png", text)
    if not paths:
        return False, "无锚图路径 (需 /outputs/images/img_*.png), 文字描述不算证据"
    return True, f"{len(paths)} 张锚图"


def _chk_tasks(value, task_lookup):
    """任务证据: 值含 task_id, 且 (若提供 task_lookup) 状态为 completed。"""
    ids = value if isinstance(value, list) else re.findall(r"\d{3,}", json.dumps(value))
    if not ids:
        return False, "无 task_id 证据"
    if task_lookup is None:
        return True, f"{len(ids)} 个 task_id (无 lookup, 只验存在)"
    bad = []
    for tid in ids:
        try:
            t = task_lookup(int(tid))
        except (TypeError, ValueError):
            bad.append(str(tid))
            continue
        if not t or t.get("status") != "completed":
            bad.append(f"{tid}:{(t or {}).get('status', 'missing')}")
    if bad:
        return False, "任务未完成/不存在: " + ",".join(bad[:5])
    return True, f"{len(ids)} 个任务全部 completed"


def _chk_asr(value, task_lookup):
    """ASR 证据: {cid: {script, asr}} 且台词镜全覆盖 + 逐字对齐。"""
    if not isinstance(value, dict) or not value:
        return False, "ASR 记录为空"
    def norm(s):
        return re.sub(r"[，。！？、\s,.!?]", "", str(s))
    bad = []
    for cid, rec in value.items():
        if not isinstance(rec, dict):
            bad.append(f"{cid}:格式")
            continue
        script, asr = norm(rec.get("script") or ""), norm(rec.get("asr") or "")
        if not script:
            continue  # 无台词镜允许缺
        if script != asr:
            bad.append(f"{cid}:不匹配")
    if bad:
        return False, "ASR 未逐字对齐: " + ",".join(bad[:5])
    return True, f"{len(value)} 镜 ASR 全对齐"


def _chk_qa(value, task_lookup):
    """QA 网格证据: frames >= 4 且 path 非空。"""
    if not isinstance(value, dict):
        return False, "QA 记录格式错误"
    frames = value.get("frames") or 0
    path = value.get("path") or ""
    if not isinstance(frames, int) or frames < 4:
        return False, f"QA 帧数 {frames} < 4 (抽样盲区下限)"
    if not path:
        return False, "无网格存档路径"
    return True, f"{frames} 帧 @ {path}"


# ---------- 证据闸规则表 ----------
EVIDENCE_RULES = {
    "image_anchor": {"tier": "hard", "desc": "锚图: /outputs/images/img_*.png 路径",
                     "check": _chk_anchor},
    "task_proof": {"tier": "hard", "desc": "task_id 列表且任务真实 completed", "check": _chk_tasks},
    "asr_proof": {"tier": "hard", "desc": "台词镜逐字 ASR 对齐记录", "check": _chk_asr},
    "qa_grid": {"tier": "soft", "desc": "QA 网格 >=4 帧 + 存档路径", "check": _chk_qa},
}


def evidence_check(kind: str, value, task_lookup=None) -> tuple[bool, str]:
    rule: dict[str, Any] | None = EVIDENCE_RULES.get(kind)
    if not rule:
        return True, "unknown kind, skip"
    check: Any = rule["check"]
    return check(value, task_lookup)


def evidence_tier(kind: str) -> str:
    return EVIDENCE_RULES.get(kind, {}).get("tier", "soft")


# ---------- 豁免日志 ----------

SLOT_RULE_TIERS = {
    "slot:characters": "hard",
    "slot:props": "hard",
    "slot:scenes": "soft",
}


def _rule_tier(rule_key: str) -> str:
    if rule_key.startswith("slot:"):
        return SLOT_RULE_TIERS.get(rule_key, "soft")
    return evidence_tier(rule_key)


def waiver_apply(manifest_data: dict, rule_key: str, reason: str, by: str = "agent") -> dict:
    """豁免一条 evidence 闸。hard 规则拒绝; soft 记录并计数, 同规则 >=WAIVE_LIMIT 自动升 hard。"""
    tier = _rule_tier(rule_key)
    if tier == "hard":
        return {"ok": False, "detail": f"{rule_key} 是 hard 规则, 不允许豁免"}
    if not reason or len(reason.strip()) < 10:
        return {"ok": False, "detail": "豁免必须给出 >=10 字理由"}
    waivers = manifest_data.setdefault("waivers", [])
    waivers.append({"rule": rule_key, "reason": reason.strip(), "by": by, "at": _now()})
    # 升级判定
    n = sum(1 for w in waivers if w["rule"] == rule_key)
    escalated = False
    if n >= WAIVE_LIMIT:
        if rule_key in EVIDENCE_RULES:
            EVIDENCE_RULES[rule_key]["tier"] = "hard"
        elif rule_key in SLOT_RULE_TIERS:
            SLOT_RULE_TIERS[rule_key] = "hard"
        escalated = True
    return {"ok": True, "count": n, "escalated_to_hard": escalated}


def waiver_report(manifest_data: dict) -> list[dict]:
    """交付声明层: 全部豁免记录 + 当前升级状态。"""
    out = []
    for w in manifest_data.get("waivers", []):
        out.append({**w, "now_tier": evidence_tier(w["rule"])})
    return out


# ---------- 槽位校验 (v2 主入口, 向后兼容 v1) ----------

def validate_slot(key: str, slot_schema: dict, slot_data: dict,
                  task_lookup=None, shots: list[dict] | None = None) -> tuple[bool, str]:
    """单槽校验。返回 (ok, detail)。

    v1 语义保留: status filled/passed 即计数。
    v2: 槽 schema 带 evidence=<kind> 时, 额外跑证据校验; 不过则降级为 failed
        (除非该规则已有有效豁免 — 豁免判定在 readiness 层统一做, 这里如实报 fail)。
    """
    status = (slot_data or {}).get("status")
    if status not in ("filled", "passed"):
        return False, "未填"
    ev = slot_schema.get("evidence")
    if not ev:
        return True, "v1 filled"
    value = (slot_data or {}).get("value")
    ok, detail = evidence_check(ev, value, task_lookup)
    return ok, detail




# ---------- v2.1: G4 QA 证据闸 + 交付声明闸 ----------

def _waivers_from_row(manifest_row) -> list[dict]:
    """从 slots_json.__waivers__ 读豁免日志 (v2 落地后的持久化位置)。"""
    try:
        import json as _json
        slots_raw = _json.loads(manifest_row.slots_json or "{}")
        return list(slots_raw.get("__waivers__") or [])
    except (ValueError, TypeError):
        return []


def evidence_summary(manifest_row, task_lookup=None) -> dict:
    """全部证据槽的当前判定快照 (G2+G4+交付三层)。供 check_gate_v2 / 交付闸调用。"""
    data = manifest_row.to_dict()
    slots = data["slots"]
    wlog = _waivers_from_row(manifest_row)
    out = {"ok": True, "failed": [], "waived": []}

    def _judge(name, ev_kind, value):
        ok, detail = evidence_check(ev_kind, value, task_lookup)
        rule_key = f"slot:{name}"
        ws = [w for w in wlog if w.get("rule") == rule_key]
        entry = {"ok": ok, "detail": detail, "tier": _rule_tier(rule_key)}
        if not ok:
            if ws:
                entry.update(waived=True, waive_reason=ws[-1].get("reason"))
                out["waived"].append({**entry, "slot": name})  # type: ignore[attr-defined]  # 异构字典值 mypy 收窄
            else:
                out["ok"] = False
                out["failed"].append({**entry, "slot": name})  # type: ignore[attr-defined]  # 同上
        return entry

    # G2: 资产三层
    for key in ("characters", "props", "scenes"):
        st = slots.get(key) or {}
        if st.get("status") in ("filled", "passed"):
            out[key] = _judge(key, "image_anchor", st.get("value"))
    # G4: qa_grid 槽 (老 manifest 无此槽时跳过, 有则强制)
    st = slots.get("qa_grid") or {}
    if st.get("status") in ("filled", "passed"):
        out["qa_grid"] = _judge("qa_grid", "qa_grid", st.get("value"))
    # 交付: delivery_statement 槽 (五项文件存在性由调用方 verify, 这里验格式)
    st = slots.get("delivery_statement") or {}
    if st.get("status") in ("filled", "passed"):
        out["delivery_statement"] = _judge(
            "delivery_statement", "delivery_statement", st.get("value"))
    return out


def check_delivery_statement(value) -> tuple[bool, str]:
    """交付声明五项: narrative_integrity/conditional_passes/voided_promises/
    unverified_items/asset_volatility 每项非空 str。"""
    if not isinstance(value, dict):
        return False, "交付声明必须是 dict"
    five = ["narrative_integrity", "conditional_passes", "voided_promises",
            "unverified_items", "asset_volatility"]
    missing = [k for k in five if not str(value.get(k) or "").strip()]
    if missing:
        return False, "五项声明缺失: " + ",".join(missing)
    short = [k for k in five if len(str(value.get(k)).strip()) < 4]
    if short:
        return False, "声明过于敷衍(>=4字): " + ",".join(short)
    return True, "五项声明齐"


# 注册进 EVIDENCE_RULES (delivery 槽是 hard: 交付声明不许豁免)
EVIDENCE_RULES["delivery_statement"] = {
    "tier": "hard", "desc": "交付五项声明", "check": check_delivery_statement}


def readiness_v2(manifest_row, task_lookup=None) -> dict:
    """v2 就绪判定: v1 规则 + 证据闸 + 豁免折算。"""
    data = manifest_row.to_dict()
    schema = {s["key"]: s for s in data["schema"]} or {s["key"]: s for s in load_schema()}
    slots = data["slots"]
    waivers = {w["rule"] for w in data.get("waivers", [])}
    missing: list[str] = []
    waived_notes: list[str] = []
    filled = 0
    required_keys = [k for k, s in schema.items() if s.get("required")]
    for key in required_keys:
        st = slots.get(key) or {}
        if st.get("status") not in ("filled", "passed"):
            missing.append(f"{schema[key].get('label') or key} 未填")
            continue
        filled += 1
        if schema[key].get("kind") == "script":
            g1 = g1_validate(parse_shots(str(st.get("value") or "")), props_declared=None)
            if not g1["ok"]:
                missing.append("剧本 G1 闸未过: " + "; ".join(
                    f"{p['cid']}:{p['rule']}" for p in g1["problems"][:5]))
        if schema[key].get("kind") == "probe":
            ok, miss = probes_ready(data["probes"])
            if not ok:
                missing.extend(miss)
        # v2 证据闸
        ev = schema[key].get("evidence")
        if ev:
            ok, detail = evidence_check(ev, st.get("value"), task_lookup)
            if not ok:
                if ev in waivers:
                    waived_notes.append(f"{key}: waived — {detail}")
                else:
                    missing.append(f"{schema[key].get('label') or key} 证据不足: {detail}")
    total = len(required_keys)
    return {
        "ready": not missing,
        "completeness": round(filled / total, 2) if total else 0.0,
        "missing": missing,
        "waived": waived_notes,
        "waiver_log": waiver_report(data),
    }
