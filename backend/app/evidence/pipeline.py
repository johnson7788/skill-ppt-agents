"""M2 真实检索管线：用户问题 -> PICO -> medical-pico-search -> EvidenceAnswer。

三步（见 plan §7/§2）：
  1. extract_pico  : LLM 从自然语言问题抽 PICO 四要素。
  2. run_search    : 调 medical-pico-search skill（Milvus 语义检索）拿四类文献。
  3. build_answer  : LLM 只写循证结论/注意/等级；references 由 Python 从检索结果
                     确定性构建（id/标题/期刊/年份/链接全来自真实数据），LLM 只能
                     引用这些 id——杜绝编造文献（医疗场景硬约束）。

产出 EvidenceAnswer 后仍走 mapper.evidence_to_a2ui 渲染，版式不变。

离线自检（不触网）：`python -m app.evidence.pipeline`，喂假文献+假 LLM 输出，
断言 references 全来自检索、citations 无悬空。
"""
from __future__ import annotations

import importlib.util
import json
import os
from typing import Any

from .schema import Caution, Conclusion, ConclusionPoint, EvidenceAnswer, Reference

# medical-pico-search skill 脚本路径（M4 vendor 进本 repo 前先用绝对路径，可 env 覆盖）
PICO_SEARCH_SCRIPT = os.environ.get(
    "PICO_SEARCH_SCRIPT",
    "/Users/admin/yifu/agno_medical_science/backend/medical_skills"
    "/medical-pico-search/scripts/infoxmed_search.py",
)
# 检索四类，按证据强度排序；候选文献总数上限（卡片放不下太多）
_CATEGORIES = ["chinese_guideline", "english_guideline", "systematic_meta", "rct"]
_MAX_CANDIDATES = 6
_ABSTRACT_CLIP = 400  # 喂给 LLM 的摘要截断，省 token


# --------------------------------------------------------------------------- LLM
def _model_and_key() -> tuple[str, str]:
    provider = os.environ.get("MODEL_PROVIDER", "deepseek")
    name = os.environ.get("MODEL_NAME", "deepseek-v4-pro")
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    model = name if name.startswith(f"{provider}/") else f"{provider}/{name}"
    return model, key


async def _chat_json(system: str, user: str) -> dict[str, Any]:
    import litellm

    model, key = _model_and_key()
    resp = await litellm.acompletion(
        model=model,
        api_key=key,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return json.loads(resp.choices[0].message.content)


# --------------------------------------------------------------------------- 1
_PICO_SYS = (
    "你是循证医学检索助手。从用户的临床问题中抽取 PICO 四要素，只输出 JSON："
    '{"P":"人群/疾病","I":"干预/药物","C":"对照(可空)","O":"结局(可空)"}。'
    "关键词用于向量检索，禁止包含年份；C/O 无法判断时留空字符串。"
)


async def extract_pico(question: str) -> dict[str, str]:
    d = await _chat_json(_PICO_SYS, question)
    return {k: str(d.get(k, "")).strip() for k in ("P", "I", "C", "O")}


# --------------------------------------------------------------------------- 2
def _load_skill():
    spec = importlib.util.spec_from_file_location("infoxmed_search", PICO_SEARCH_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"找不到检索脚本: {PICO_SEARCH_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def run_search(pico: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    mod = _load_skill()
    return await mod.search_embedding_by_PICO(
        P=pico["P"], I=pico["I"], C=pico["C"], O=pico["O"],
        tool_context=mod.MockToolContext(),
    )


# --------------------------------------------------------------------------- 3
def _year_of(item: dict[str, Any]) -> int:
    d = str(item.get("publish_date", ""))[:4]
    return int(d) if d.isdigit() else 0


def build_candidates(results: dict[str, list[dict[str, Any]]]) -> list[Reference]:
    """从检索结果确定性拉出候选文献，assign id 1..N。references 只出这里。"""
    flat: list[dict[str, Any]] = []
    for cat in _CATEGORIES:
        for it in results.get(cat, []) or []:
            it = {**it, "_cat": cat}
            flat.append(it)
    # 按 weight × reranker_score 降序，取前 N
    flat.sort(key=lambda x: (x.get("weight", 0) or 0) * (x.get("reranker_score", 0) or 0),
              reverse=True)
    refs: list[Reference] = []
    for i, it in enumerate(flat[:_MAX_CANDIDATES], start=1):
        refs.append(Reference(
            id=i,
            title=(it.get("title") or "未命名文献").strip(),
            source=(it.get("journal") or "unknown").strip(),
            year=_year_of(it),
            url=it.get("link") or None,
        ))
    return refs


def _candidates_prompt(refs: list[Reference], results: dict[str, Any]) -> str:
    # 把摘要贴回候选（refs 不带摘要），供 LLM 判断
    absmap: dict[str, str] = {}
    for cat in _CATEGORIES:
        for it in results.get(cat, []) or []:
            absmap[str(it.get("title", "")).strip().lower()] = it.get("abstract", "") or ""
    lines = []
    for r in refs:
        ab = absmap.get(r.title.lower(), "")[:_ABSTRACT_CLIP]
        lines.append(f"[{r.id}] {r.title}（{r.source}, {r.year}）\n    摘要: {ab}")
    return "\n".join(lines) if lines else "（未检索到文献）"


_ANSWER_SYS = (
    "你是循证决策支持引擎。基于用户问题和给定的候选文献，输出循证结论 JSON。"
    "只引用给定候选文献的编号（citations/points 内的引用必须是候选 id，禁止编造）。"
    "证据等级 A/B/C/D：有高质量指南或 Meta 分析支持=A，多项 RCT=B，少量证据=C，"
    "无充分证据=D。输出严格 JSON：\n"
    '{"intro":"卡片前引导语(一句)","evidenceLevel":"A|B|C|D",'
    '"basis":"证据来源摘要，如 基于X指南·N篇文献",'
    '"conclusion":{"subject":"结论主句","citations":[引用的候选id],'
    '"points":[{"label":"要点标签(可空)","text":"要点正文"}]},'
    '"cautions":[{"highlight":"红色强调短语(可空)","text":"注意事项正文"}]}'
)


async def build_answer(
    question: str,
    pico: dict[str, str],
    results: dict[str, list[dict[str, Any]]],
    context: str = "",
) -> EvidenceAnswer:
    refs = build_candidates(results)
    user = (
        f"{context}"
        f"当前问题：{question}\n"
        f"PICO：P={pico['P']} I={pico['I']} C={pico['C']} O={pico['O']}\n\n"
        f"候选文献：\n{_candidates_prompt(refs, results)}"
    )
    d = await _chat_json(_ANSWER_SYS, user)
    return assemble(question, refs, d)


def assemble(
    question: str, refs: list[Reference], llm: dict[str, Any]
) -> EvidenceAnswer:
    """纯函数：LLM 文本 + 确定性 references -> EvidenceAnswer。过滤悬空引用。"""
    valid_ids = {r.id for r in refs}
    concl = llm.get("conclusion", {}) or {}
    citations = [c for c in concl.get("citations", []) if c in valid_ids]
    points = [ConclusionPoint(label=(p.get("label") or None), text=p.get("text", ""))
              for p in concl.get("points", []) or []]
    cautions = [Caution(highlight=(c.get("highlight") or None), text=c.get("text", ""))
                for c in llm.get("cautions", []) or []]
    # references 只保留被引用的（无引用则全留，避免空文献区）
    cited = {i for i in citations}
    kept = [r for r in refs if r.id in cited] or refs
    return EvidenceAnswer(
        question=question,
        intro=llm.get("intro", ""),
        evidenceLevel=str(llm.get("evidenceLevel", "D")).strip()[:1].upper() or "D",
        basis=llm.get("basis", ""),
        conclusion=Conclusion(
            subject=concl.get("subject", ""), citations=citations, points=points),
        cautions=cautions,
        references=kept,
    )


async def answer_question(
    question: str, history: list[str] | None = None
) -> EvidenceAnswer:
    """M3：history=此前的追问列表。有则综合上下文检索+生成，卡片随对话演进。"""
    context = ""
    if history:
        prior = "\n".join(f"- {h}" for h in history)
        context = f"对话背景（此前的追问，需综合考虑并在已有结论上补充/修订）：\n{prior}\n\n"
    pico = await extract_pico(context + question)
    results = await run_search(pico)
    return await build_answer(question, pico, results, context)


def _check() -> None:
    fake_results = {
        "chinese_guideline": [
            {"title": "过敏性鼻炎诊疗指南", "journal": "中华耳鼻咽喉杂志",
             "publish_date": "2022-05-01 00:00:00", "link": "http://x/1",
             "weight": 5, "reranker_score": 0.9, "abstract": "二代抗组胺药一线..."},
        ],
        "rct": [
            {"title": "Loratadine RCT", "journal": "Allergy",
             "publish_date": "2019-01-01 00:00:00", "link": "http://x/2",
             "weight": 4, "reranker_score": 0.8, "abstract": "safe and effective"},
        ],
    }
    refs = build_candidates(fake_results)
    assert len(refs) == 2 and refs[0].id == 1
    assert refs[0].title == "过敏性鼻炎诊疗指南" and refs[0].year == 2022
    fake_llm = {
        "intro": "为你循证分析",
        "evidenceLevel": "A级",  # 会被截成 A
        "basis": "基于指南·2篇文献",
        "conclusion": {"subject": "可用", "citations": [1, 99],  # 99 悬空应被滤
                       "points": [{"label": "剂量", "text": "10mg qd"}]},
        "cautions": [{"highlight": "先明确诊断", "text": "反复症状建议就医"}],
    }
    ans = assemble("能吃氯雷他定吗", refs, fake_llm)
    assert ans.evidenceLevel == "A", ans.evidenceLevel
    assert ans.conclusion.citations == [1], "悬空引用未过滤"
    assert [r.id for r in ans.references] == [1], "只应保留被引用文献"
    # 无引用时全留
    ans2 = assemble("q", refs, {"conclusion": {"citations": []}})
    assert len(ans2.references) == 2
    print("OK: 候选构建 + 悬空引用过滤 + references 保留逻辑全通过")


if __name__ == "__main__":
    _check()
