"""evidence_to_a2ui: 把 EvidenceAnswer 确定性映射成 A2UI v0.9 消息流。

设计取舍（见 plan.md §5）：不让 LLM 手搓 A2UI JSON，而是这里用纯函数拼。
好处：产出永远是合法 A2UI、永远带证据等级+文献+就医提示、可单测。
变的是证据内容，不变的是版式。

A2UI v0.9 消息 = [{version, createSurface}, {version, updateComponents}]。
组件用 basic catalog：Card/Column/Row/Text/Divider/Button。文字全部内联字面量
（不用 {path} 数据绑定），因内容由服务端生成、无需前端二次填充。

自检：`python -m app.evidence.mapper`（喂 fixtures/loratadine.json，断言无悬空
child 引用、根为 Card、文献数=2）。
"""
from __future__ import annotations

from typing import Any

from .schema import EvidenceAnswer, Questionnaire

VERSION = "v0.9"
# 合并了 basic + 3 个 Smart Wrapper(EvidenceHeader/EvidenceBadge/CautionBox) 的自定义
# catalog，前端 web/src/catalog.tsx 的 EVIDENCE_CATALOG_ID 必须与此一致。
CATALOG_ID = "https://evidence-a2ui.local/catalog/v1"
SURFACE_ID = "evidence-card"


def create_surface_msg(surface_id: str = SURFACE_ID) -> dict[str, Any]:
    return {"version": VERSION,
            "createSurface": {"surfaceId": surface_id, "catalogId": CATALOG_ID}}


def update_components_msg(
    components: list[dict[str, Any]], surface_id: str = SURFACE_ID
) -> dict[str, Any]:
    return {"version": VERSION,
            "updateComponents": {"surfaceId": surface_id, "components": components}}


def evidence_components(answer: EvidenceAnswer) -> list[dict[str, Any]]:
    """把 EvidenceAnswer 拼成扁平组件列表（不含 surface 消息壳）。

    M3 增量更新用它：追问时对比前后组件列表，只 updateComponents 差异部分，
    surface 不重建、卡片不整块重画。组件 id 确定性 → 同 id 原地更新。
    """
    comps: list[dict[str, Any]] = []

    def add(comp: dict[str, Any]) -> str:
        comps.append(comp)
        return comp["id"]

    def text(cid: str, s: str, variant: str | None = None) -> str:
        c: dict[str, Any] = {"id": cid, "component": "Text", "text": s}
        if variant:
            c["variant"] = variant
        return add(c)

    def divider(cid: str) -> str:
        return add({"id": cid, "component": "Divider"})

    body: list[str] = []

    # 标题条（Smart Wrapper：绿色渐变头）
    add({"id": "hdr", "component": "EvidenceHeader",
         "title": "循证决策支持", "subtitle": "Powered by Evidence Engine"})
    body.append("hdr")

    # 证据等级徽章（Smart Wrapper：彩色药丸） + 依据
    add({"id": "badge", "component": "EvidenceBadge", "level": answer.evidenceLevel})
    text("basis", answer.basis, "caption")
    add({"id": "badge_row", "component": "Row", "children": ["badge", "basis"],
         "justify": "start", "align": "center"})
    body.append("badge_row")

    # 循证结论
    text("concl_h", "📋 循证结论", "h5")
    body.append("concl_h")
    cite = "".join(f" [{c}]" for c in answer.conclusion.citations)
    text("concl_subject", f"**{answer.conclusion.subject}**{cite}")
    body.append("concl_subject")
    point_ids: list[str] = []
    for i, p in enumerate(answer.conclusion.points):
        prefix = f"**{p.label}**：" if p.label else ""
        pid = text(f"point_{i}", f"· {prefix}{p.text}")
        point_ids.append(pid)
    add({"id": "concl_points", "component": "Column", "children": point_ids,
         "align": "start"})
    body.append("concl_points")

    # 注意事项
    if answer.cautions:
        body.append(divider("d_caution"))
        text("caution_h", "⚠️ 注意事项", "h5")
        body.append("caution_h")
        caution_ids: list[str] = []
        for i, c in enumerate(answer.cautions):
            cid = f"caution_{i}"
            add({"id": cid, "component": "CautionBox",
                 "highlight": c.highlight or "", "text": c.text})
            caution_ids.append(cid)
        add({"id": "caution_col", "component": "Column",
             "children": caution_ids, "align": "start"})
        body.append("caution_col")

    # 参考文献
    if answer.references:
        body.append(divider("d_ref"))
        text("ref_h", f"📚 参考文献（共 {len(answer.references)} 篇）", "h5")
        body.append("ref_h")
        for r in answer.references:
            title = text(f"ref_{r.id}_title", f"{r.id}. {r.title}")
            ident = r.isbn and f"ISBN: {r.isbn}" or (r.pmid and f"PMID: {r.pmid}") or ""
            meta_bits = [r.source, str(r.year)]
            if r.volume:
                meta_bits.append(r.volume)
            if ident:
                meta_bits.append(ident)
            meta = text(f"ref_{r.id}_meta", " · ".join(meta_bits), "caption")
            ref_children = [title, meta]
            if r.url:
                text(f"ref_{r.id}_link_t", "查看原文 →")
                add({"id": f"ref_{r.id}_link", "component": "Button",
                     "child": f"ref_{r.id}_link_t",
                     "action": {"event": {"name": "open_reference",
                                          "context": {"url": r.url, "id": r.id}}}})
                ref_children.append(f"ref_{r.id}_link")
            add({"id": f"ref_{r.id}", "component": "Column",
                 "children": ref_children, "align": "start"})
            body.append(f"ref_{r.id}")

    # 根
    add({"id": "root_col", "component": "Column", "children": body, "align": "stretch"})
    add({"id": "root", "component": "Card", "child": "root_col"})

    return comps


def questionnaire_components(q: Questionnaire) -> list[dict[str, Any]]:
    """把 Questionnaire 拼成组件列表：单个自定义 Questionnaire 组件（自带打分逻辑），
    评分在前端做（选项 score 求和 → 落 band），后端只传量表定义。"""
    # A2UI 约定：组件树必须有一个 id="root" 的根组件（web_core server_to_client schema）。
    return [{
        "id": "root",
        "component": "Questionnaire",
        "title": q.title,
        "intro": q.intro,
        "options": [o.model_dump() for o in q.options],
        "items": list(q.items),
        "bands": [b.model_dump() for b in q.bands],
        "disclaimer": q.disclaimer,
    }]


def evidence_to_a2ui(
    answer: EvidenceAnswer, surface_id: str = SURFACE_ID
) -> list[dict[str, Any]]:
    comps = evidence_components(answer)
    return [create_surface_msg(surface_id), update_components_msg(comps, surface_id)]


def _check(answer: EvidenceAnswer) -> None:
    msgs = evidence_to_a2ui(answer)
    assert msgs[0]["createSurface"]["surfaceId"] == SURFACE_ID
    comps = msgs[1]["updateComponents"]["components"]
    by_id = {c["id"]: c for c in comps}
    assert len(by_id) == len(comps), "组件 id 有重复"
    root = by_id["root"]
    assert root["component"] == "Card"
    # 无悬空引用：所有 child/children 指向的 id 都存在
    for c in comps:
        refs = [c["child"]] if "child" in c else c.get("children", [])
        for rid in refs:
            assert rid in by_id, f"悬空引用: {c['id']} -> {rid}"
    # 文献都渲染出来了
    for r in answer.references:
        assert f"ref_{r.id}" in by_id, f"缺文献 {r.id}"
    print(f"OK: {len(comps)} 组件, {len(answer.references)} 篇文献, 无悬空引用")


if __name__ == "__main__":
    import json
    import pathlib

    fixture = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "loratadine.json"
    ans = EvidenceAnswer.model_validate(json.loads(fixture.read_text("utf-8")))
    _check(ans)
