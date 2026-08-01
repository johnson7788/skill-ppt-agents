"""EvidenceAnswer: 循证问答的唯一数据契约。

skill 只产出这个结构；A2UI JSON 由它确定性推导（见 mapper.py），绝不反过来。
所有字段对应 meituan.png 那张"循证决策支持"卡。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ConclusionPoint(BaseModel):
    label: str | None = None  # 如 "适用年龄"，可空
    text: str


class Conclusion(BaseModel):
    subject: str  # 结论主句，如 "氯雷他定（开瑞坦）为第二代抗组胺药"
    citations: list[int] = Field(default_factory=list)  # 关联 references[].id
    points: list[ConclusionPoint] = Field(default_factory=list)


class Caution(BaseModel):
    highlight: str | None = None  # 红色强调短语，如 "建议先明确诊断"
    text: str


class Reference(BaseModel):
    id: int
    title: str
    source: str  # 期刊/机构，如 "WHO" / "Allergy"
    year: int
    isbn: str | None = None
    pmid: str | None = None
    volume: str | None = None  # 如 "79(3): 456-470"
    url: str | None = None


class EvidenceAnswer(BaseModel):
    question: str
    intro: str  # 卡片前的引导语
    evidenceLevel: str  # A / B / C / D
    basis: str  # 证据来源摘要，如 "基于 WHO 过敏指南 · 多项 RCT"
    conclusion: Conclusion
    cautions: list[Caution] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)


# --- 自测问卷/量表模式（如 PHQ-9、GAD-7、中医体质辨识）-------------------------
# 评分在前端做（选项 score 求和 → 落到某个 band），后端只产量表定义。
class ScaleOption(BaseModel):
    label: str  # 如 "完全不会"
    score: int  # 该选项计分，如 0/1/2/3


class ScaleBand(BaseModel):
    min: int  # 区间下界（含）
    max: int  # 区间上界（含）
    label: str  # 如 "中度"
    advice: str  # 该档位的建议


class Questionnaire(BaseModel):
    title: str  # 如 "PHQ-9 抑郁自评量表"
    intro: str  # 填写说明，如 "根据最近两周的情况选择"
    options: list[ScaleOption]  # 所有题共用一组选项（PHQ/GAD 皆如此）
    items: list[str]  # 题干列表
    bands: list[ScaleBand]  # 总分分档
    disclaimer: str  # 免责声明（结果仅供参考、不替代面诊）
