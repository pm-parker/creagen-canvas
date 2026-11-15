"""Agent API: 制片清单（Production Manifest）端点。



POST   /api/agent/v1/projects/{pid}/manifest          创建/替换（UPSERT，返回模板）

GET    /api/agent/v1/projects/{pid}/manifest          读（含 readiness 判定）

PATCH  /api/agent/v1/projects/{pid}/manifest/slots    填槽/改槽

PATCH  /api/agent/v1/projects/{pid}/manifest/probes   探针裁决（联动 audio_plan）

PATCH  /api/agent/v1/projects/{pid}/manifest          strict 开关等

POST   /api/agent/v1/projects/{pid}/manifest/g1       对当前剧本跑 G1 闸（预检）

"""



from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.db import get_db
from backend.app.models.production import ProductionManifest
from backend.app.models.task import Task
from backend.app.routers.agent import _canvas_rate, _own_project, require_agent_key
from backend.app.services import manifest_service
from backend.app.services.manifest_service import (
    evidence_check,
    g1_validate,
    load_schema,
    parse_shots,
    readiness,
    waiver_apply,
)

router = APIRouter(prefix="/api/agent/v1", tags=["agent-api"])





def _get_or_create(db: Session, project_id: int, user_id: int) -> ProductionManifest:

    row = (

        db.query(ProductionManifest)

        .filter(ProductionManifest.project_id == project_id)

        .first()

    )

    if row is None:

        row = ProductionManifest(

            project_id=project_id,

            user_id=user_id,

            schema_json=json.dumps(load_schema(), ensure_ascii=False),

            slots_json="{}",

            probes_json="{}",

        )

        db.add(row)

        db.commit()

        db.refresh(row)

    return row





def _manifest_or_404(db: Session, project_id: int) -> ProductionManifest:

    row = (

        db.query(ProductionManifest)

        .filter(ProductionManifest.project_id == project_id)

        .first()

    )

    if row is None:

        raise HTTPException(

            status_code=404,

            detail={"code": "MANIFEST_NOT_FOUND", "message": "该项目未创建制片清单"},

        )

    return row





def check_gate(db: Session, project_id: int) -> None:

    """generate(video)/compose 提交前的硬闸钩子（供 canvas._agent_generate_core 调用）。



    无 manifest → 放行（老项目兼容）；

    有 manifest 且 strict → 未 ready 返回 422 MANIFEST_NOT_READY。

    """

    row = (

        db.query(ProductionManifest)

        .filter(ProductionManifest.project_id == project_id)

        .first()

    )

    if row is None or not row.strict:

        return

    r = readiness(row)

    if not r["ready"]:

        raise HTTPException(

            status_code=422,

            detail={

                "code": "MANIFEST_NOT_READY",

                "message": "制片清单未就绪，禁止进入视频工作流",

                "completeness": r["completeness"],

                "missing": r["missing"][:10],

            },

        )

    # v2 证据闸: 资产/qa/交付三层, 豁免折算 (scenes 等 soft 可豁免; hard 不过即 422)

    from backend.app.models.task import Task as _Task
    from backend.app.services.manifest_service import evidence_summary



    def _lookup(tid):

        t = db.query(_Task).filter(_Task.id == tid).first()

        return {"status": t.status} if t else None



    ev = evidence_summary(row, _lookup)

    if not ev["ok"]:

        raise HTTPException(

            status_code=422,

            detail={

                "code": "EVIDENCE_NOT_READY",

                "message": "证据闸未过: 资产/QA/交付声明存在无证据且未豁免的槽",

                "failed": ev["failed"][:6],

                "waived": [w["slot"] for w in ev["waived"]],

            },

        )

        raise HTTPException(

            status_code=422,

            detail={

                "code": "MANIFEST_NOT_READY",

                "message": "制片清单未就绪，禁止进入视频工作流",

                "completeness": r["completeness"],

                "missing": r["missing"][:10],

            },

        )





# ---------- schemas ----------





class SlotsPatch(BaseModel):

    slots: dict = Field(..., description="{key: {status, value, node_id?, note?}}")





class ProbesPatch(BaseModel):

    dialogue_route: str | None = Field(None, description="native | tts")

    style_ok: bool | None = None

    identity_ok: bool | None = None

    notes: str | None = None





class ManifestPatch(BaseModel):

    strict: bool | None = None





class WaiveRequest(BaseModel):

    rule: str = Field(..., description="规则 key, 如 slot:scenes")

    reason: str = Field(..., min_length=10, description="豁免理由, >=10 字")





class G1Request(BaseModel):

    script: str | None = Field(None, description="不传=用当前剧本槽值")





# ---------- endpoints ----------





@router.post("/projects/{project_id}/manifest", status_code=201)

def create_manifest(

    project_id: int,

    db: Session = Depends(get_db),

    user=Depends(require_agent_key),

):

    _canvas_rate(user)

    _own_project(db, user, project_id)

    row = _get_or_create(db, project_id, user.id)

    d = row.to_dict()

    d["readiness"] = readiness(row)

    return d





@router.get("/projects/{project_id}/manifest")

def get_manifest(

    project_id: int,

    db: Session = Depends(get_db),

    user=Depends(require_agent_key),

):

    _own_project(db, user, project_id)

    try:

        row = _manifest_or_404(db, project_id)

    except HTTPException:

        # 未创建时返回模板预览（便于 agent 探索）

        return {"project_id": project_id, "template": load_schema(),

                "created": False, "readiness": {"ready": False,

                "completeness": 0.0, "missing": ["清单未创建"]}}

    d = row.to_dict()

    d["readiness"] = readiness(row)

    d["created"] = True

    return d





@router.patch("/projects/{project_id}/manifest/slots")

def patch_slots(

    project_id: int,

    body: SlotsPatch,

    db: Session = Depends(get_db),

    user=Depends(require_agent_key),

):

    _canvas_rate(user)

    _own_project(db, user, project_id)

    row = _manifest_or_404(db, project_id)

    slots = row.to_dict()["slots"]

    schema = {s["key"]: s for s in load_schema()}

    applied, rejected = [], []

    for key, patch in body.slots.items():

        if key not in schema:

            rejected.append({"key": key, "reason": "unknown_slot"})

            continue

        cur = slots.get(key) or {}

        status = patch.get("status") or cur.get("status") or "filled"

        if status not in ("empty", "filled", "passed", "failed"):

            rejected.append({"key": key, "reason": f"bad_status:{status}"})

            continue

        # script 槽: 填值即跑 G1, 结果写回 status

        new_value = patch.get("value", cur.get("value"))

        if schema[key]["kind"] == "script" and new_value:

            g1 = g1_validate(parse_shots(str(new_value)))

            status = "passed" if g1["ok"] else "failed"

            cur["g1"] = g1

        cur.update({

            "status": status,

            "value": new_value,

            "node_id": patch.get("node_id", cur.get("node_id")),

            "note": patch.get("note", cur.get("note")),

            "updated_at": manifest_service._now(),

        })

        slots[key] = cur

        applied.append(key)

    row.slots_json = json.dumps(slots, ensure_ascii=False)

    db.commit()

    d = row.to_dict()

    d["readiness"] = readiness(row)

    return {"applied": applied, "rejected": rejected,

            "readiness": d["readiness"], "slots": d["slots"]}





@router.patch("/projects/{project_id}/manifest/probes")

def patch_probes(

    project_id: int,

    body: ProbesPatch,

    db: Session = Depends(get_db),

    user=Depends(require_agent_key),

):

    _canvas_rate(user)

    _own_project(db, user, project_id)

    row = _manifest_or_404(db, project_id)

    probes = row.to_dict()["probes"]

    if body.dialogue_route is not None:

        if body.dialogue_route not in ("native", "tts"):

            raise HTTPException(

                status_code=422,

                detail={"code": "BAD_ROUTE",

                        "message": "dialogue_route 仅支持 native|tts"},

            )

        probes["dialogue_route"] = body.dialogue_route

    if body.style_ok is not None:

        probes["style_ok"] = body.style_ok

    if body.identity_ok is not None:

        probes["identity_ok"] = body.identity_ok

    if body.notes is not None:

        probes["notes"] = body.notes[:500]

    # 探针联动: tts 路线 → audio_plan 槽自动补注记（数据驱动，不是提示）

    slots = row.to_dict()["slots"]

    if probes.get("dialogue_route") == "tts":

        ap = slots.get("audio_plan") or {}

        ap["route_note"] = "tts: 台词镜 prompt 须为无声口型, 台词走后期轨"

        slots["audio_plan"] = ap

        row.slots_json = json.dumps(slots, ensure_ascii=False)

    row.probes_json = json.dumps(probes, ensure_ascii=False)

    db.commit()

    d = row.to_dict()

    d["readiness"] = readiness(row)

    return {"probes": d["probes"], "readiness": d["readiness"]}





@router.patch("/projects/{project_id}/manifest")

def patch_manifest(

    project_id: int,

    body: ManifestPatch,

    db: Session = Depends(get_db),

    user=Depends(require_agent_key),

):

    _canvas_rate(user)

    _own_project(db, user, project_id)

    row = _manifest_or_404(db, project_id)

    if body.strict is not None:

        row.strict = body.strict

    db.commit()

    d = row.to_dict()

    d["readiness"] = readiness(row)

    return d





@router.post("/projects/{project_id}/manifest/g1")

def run_g1(

    project_id: int,

    body: G1Request,

    db: Session = Depends(get_db),

    user=Depends(require_agent_key),

):

    """G1 预检：不写状态，只验。"""

    _own_project(db, user, project_id)

    script = body.script

    if not script:

        try:

            row = _manifest_or_404(db, project_id)

            slots = row.to_dict()["slots"]

            script = str((slots.get("script") or {}).get("value") or "")

        except HTTPException:

            script = ""

    return g1_validate(parse_shots(script))





@router.post("/projects/{project_id}/manifest/g2")

def run_g2(

    project_id: int,

    db: Session = Depends(get_db),

    user=Depends(require_agent_key),

):

    """G2 资产闸 (v2 证据版): characters/props/scenes 槽必须挂真证据。

    - characters: hard — 每角色正/侧/背/手四卡, 值含 /outputs/images/ 锚图路径

    - props:      hard — 关键道具锚图 (evidence=image_anchor)

    - scenes:     soft — 场景锚图, 可豁免 (waiver, 留痕)

    返回 per-slot 判定; 不过的槽给出可执行修复动作。不写状态。"""

    _own_project(db, user, project_id)

    row = _manifest_or_404(db, project_id)

    slots = row.to_dict()["slots"]



    def task_lookup(tid: int):

        t = db.query(Task).filter(Task.id == tid).first()

        if not t:

            return None

        return {"status": t.status, "output_url": getattr(t, "output_url", None)}



    result = {"ok": True, "slots": {}}

    for key, schema_item in (("characters", {"tier": "hard", "ev": "image_anchor"}),

                             ("props", {"tier": "hard", "ev": "image_anchor"}),

                             ("scenes", {"tier": "soft", "ev": "image_anchor"})):

        st = slots.get(key) or {}

        entry = {"status": st.get("status")}

        if st.get("status") not in ("filled", "passed"):

            entry.update(ok=False, fix=f"{key} 槽未填")

            result["ok"] = False

        else:

            ok, detail = evidence_check(schema_item["ev"], st.get("value"), task_lookup)

            _slots_raw = json.loads(row.slots_json or "{}")

            _wlog = _slots_raw.get("__waivers__") or []

            waivers = [w for w in _wlog if w.get("rule") == f"slot:{key}"]

            entry.update(ok=ok, detail=detail,

                         waived=bool(waivers) and not ok,

                         tier=schema_item["tier"])

            if not ok and not waivers:

                result["ok"] = False

            elif not ok and waivers:

                entry["waive_reason"] = waivers[-1].get("reason")

        result["slots"][key] = entry

    return result





@router.post("/projects/{project_id}/manifest/waive")

def waive_rule(

    project_id: int,

    body: WaiveRequest,

    db: Session = Depends(get_db),

    user=Depends(require_agent_key),

):

    """豁免一条 soft 证据闸: 必须给 >=10 字理由, 记入 manifest.waivers;

    同规则第 3 次豁免自动升 hard (此后拒绝)。"""

    _own_project(db, user, project_id)

    row = _manifest_or_404(db, project_id)

    slots = json.loads(row.slots_json or "{}")

    d = {"waivers": list(slots.get("__waivers__") or [])}

    res = waiver_apply(d, body.rule, body.reason, by="agent")

    if not res.get("ok"):

        raise HTTPException(400, res.get("detail") or "豁免被拒绝")

    slots["__waivers__"] = d.get("waivers", [])

    row.slots_json = json.dumps(slots, ensure_ascii=False)

    db.commit()

    return {"ok": True, "waivers": d.get("waivers"),
         "escalated_to_hard": res.get("escalated_to_hard", False)}
