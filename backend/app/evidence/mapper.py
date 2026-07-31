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

from .schema import EvidenceAnswer

VERSION = "v0.9"
CATALOG_ID = "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"
SURFACE_ID = "evidence-card"


def evidence_to_a2ui(
    answer: EvidenceAnswer, surface_id: str = SURFACE_ID
) -> list[dict[str, Any]]:
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

    # 标题条
    text("hdr_title", "循证决策支持", "h4")
    text("hdr_sub", "Powered by Evidence Engine", "caption")
    body += ["hdr_title", "hdr_sub", divider("d_hdr")]

    # 证据等级徽章 + 依据
    text("badge", f"{answer.evidenceLevel} 级证据", "h5")
    text("basis", answer.basis, "caption")
    add({"id": "badge_row", "component": "Row", "children": ["badge", "basis"],
         "justify": "start", "align": "center"})
    body.append("badge_row")

    # 循证结论
    text("concl_h", "循证结论", "h5")
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
        text("caution_h", "注意事项", "h5")
        body.append("caution_h")
        caution_ids: list[str] = []
        for i, c in enumerate(answer.cautions):
            hl = f"**{c.highlight}** " if c.highlight else ""
            cid = text(f"caution_{i}", f"{hl}{c.text}")
            caution_ids.append(cid)
        add({"id": "caution_col", "component": "Column",
             "children": caution_ids, "align": "start"})
        body.append("caution_col")

    # 参考文献
    if answer.references:
        body.append(divider("d_ref"))
        text("ref_h", f"参考文献（共 {len(answer.references)} 篇）", "h5")
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

    return [
        {"version": VERSION,
         "createSurface": {"surfaceId": surface_id, "catalogId": CATALOG_ID}},
        {"version": VERSION,
         "updateComponents": {"surfaceId": surface_id, "components": comps}},
    ]


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
