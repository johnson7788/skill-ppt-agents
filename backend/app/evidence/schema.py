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
