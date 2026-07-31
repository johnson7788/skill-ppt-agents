---
name: medical-pico-search
description: 医学文献 PICO 语义搜索技能，基于 PICO 框架通过 Milvus 向量数据库进行语义检索，覆盖中文指南、英文指南、系统评价/Meta分析和 RCT 四类文献。适用于结构化临床问题的循证文献检索，通过渐进式 PICO 组合查询实现高召回率的语义匹配。当用户提出结构化的临床问题、需要基于 PICO 框架检索文献、或需要语义级别的文献匹配时触发此技能。
---

# Medical PICO Search — 医学文献 PICO 语义搜索

## 概述

此技能提供基于 **PICO 框架** 的医学文献语义搜索能力，通过 Milvus 向量数据库对多个医学文献库进行混合检索（Dense + Sparse），返回经过权重排序和去重的高质量文献结果。

> **与 medical-keyword-search 的区别**：本技能侧重**语义匹配**，输入为结构化的 PICO 要素，适合回答明确的临床问题；而 medical-keyword-search 侧重**精确关键词检索**，支持布尔表达式和高级筛选，适合精确定位特定文献。

## ⚠️ 调用方式（重要）

**必须通过命令行参数传入用户的 PICO 值**，不要直接运行脚本（直接运行会报错缺少必要参数）。

### 分步操作

#### 步骤 1：从用户问题中提取 PICO

分析用户的临床问题，提取出 PICO 各要素：
- **P (Population/Patient)**: 人群 — 疾病、严重程度、年龄/性别、伴随疾病、基线风险、场景
- **I (Intervention)**: 干预 — 剂量、疗程、途径、频次、联合用药/策略细节
- **C (Comparison)**: 比较 — 对照组或替代方案（可选，尽量提供）
- **O (Outcome)**: 结局 — 需要关注的临床终点（可选，尽量提供）

#### 步骤 2：通过命令行调用搜索脚本

使用 `run_command` 工具执行以下命令，**将 PICO 参数替换为从用户问题中提取的实际值**：

```bash
python <skill_directory>/scripts/infoxmed_search.py --P "用户的P值" --I "用户的I值" --C "用户的C值" --O "用户的O值" --output "<输出文件路径>"
```

**完整示例：**

```bash
python <skill_directory>/scripts/infoxmed_search.py --P "轻中度抑郁障碍患者" --I "认知行为治疗 CBT" --C "单纯药物治疗" --O "抑郁症状改善 复发率" --output "/tmp/search_results.json"
```

**参数说明：**

| 参数 | 必选 | 说明 |
|------|------|------|
| `--P` | ✅ 是 | 人群（Population） |
| `--I` | ✅ 是 | 干预（Intervention） |
| `--C` | ❌ 否 | 比较（Comparison），默认为空 |
| `--O` | ❌ 否 | 结局（Outcome），默认为空 |
| `--output` | ❌ 否 | 结果输出文件路径，不指定则输出到终端 |

> **注意**：搜索关键词**禁止添加年份时间**，年份过滤通过 API 的 filter_condition 实现。

#### 步骤 3：读取并展示结果

如果使用了 `--output` 参数，用 `view_file` 读取输出文件；否则直接从命令输出中获取结果。

将结果整理后向用户展示，按数据库分类呈现：
- 中文指南结果
- 英文指南结果
- 系统评价/Meta分析结果
- RCT 临床试验结果

## 搜索架构

### 数据库覆盖

搜索会并行查询 4 个数据库：

| 数据库 | Collection 名称 | 说明 |
|--------|-----------------|------|
| 中文指南库 | `guideline_zh` | 中国医学指南 |
| 英文指南库 | `guideline_en` | 国际英文医学指南 |
| 系统评价/Meta分析库 | `systematic_and_meta` | Systematic Review & Meta-Analysis |
| RCT 临床试验库 | `RCT` | 随机对照试验 |

### 渐进式检索策略

每个数据库使用 6 组关键词逐步扩展搜索：
1. `P` — 仅人群
2. `I` — 仅干预
3. `P I` — 人群+干预
4. `P I C` — 人群+干预+比较
5. `P I O` — 人群+干预+结局
6. `P I C O` — 完整 PICO

多次搜索结果按 ID 去重，保留最高权重。

## 返回结果格式

### 单条文献结构

```json
{
    "id": "文献唯一ID",
    "title": "文献标题",
    "abstract": "摘要 + 匹配内容片段",
    "authors": "作者",
    "journal": "期刊名称",
    "publish_date": "2024-01-15 00:00:00",
    "impact_factor": "影响因子",
    "publication_type": "文献类型",
    "link": "https://www.infox-med.com/#/articleDetails?id=xxx",
    "weight": 5,
    "reranker_score": 0.85
}
```

### 完整返回结构

搜索返回一个字典，包含 4 个子类别：

```json
{
    "chinese_guideline": [...],
    "english_guideline": [...],
    "systematic_meta": [...],
    "rct": [...]
}
```

每个类别最多返回 **10 条**结果，按 `weight × reranker_score` 降序排列。

## 期刊等级映射

结果中的 `weight` 字段对应期刊等级：

| 等级值 | 含义 |
|--------|------|
| 5 | 全球顶刊 |
| 4 | 科室重磅期刊 |
| 3 | 科室一定影响力期刊 |
| 2 | 补充期刊 |
| 1 | 剩余期刊 |

## 特殊过滤规则

- **系统评价/Meta分析库**：标题必须包含 "meta-analysis" 或 "systematic review"（不区分大小写）
- **系统评价/Meta分析库 和 RCT库**：优先返回 weight > 1.0 的高质量文献，如果没有则返回全部
- **指南库**：会自动记录最早发布时间到内部状态，可供后续过滤使用

## 依赖

- `aiohttp` — 异步 HTTP 请求
- `python-dotenv` — 环境变量加载
- 需要网络访问 Milvus 搜索 API

## 示例场景

### 场景1：糖尿病与巨大儿风险

```bash
python scripts/infoxmed_search.py --P "gestational diabetes mellitus, GDM" --I "pre-pregnancy BMI increase, elevated pre-pregnancy BMI" --C "normal pre-pregnancy BMI" --O "macrosomia risk, large for gestational age" --output "/tmp/gdm_results.json"
```

### 场景2：脓毒症休克的液体复苏

```bash
python scripts/infoxmed_search.py --P "脓毒症休克" --I "限制性液体复苏" --output "/tmp/sepsis_results.json"
```

### 场景3：抑郁症 CBT vs 药物治疗

```bash
python scripts/infoxmed_search.py --P "轻中度抑郁障碍患者" --I "认知行为治疗 CBT" --C "单纯药物治疗" --O "抑郁症状改善 复发率" --output "/tmp/depression_results.json"
```

## 注意事项

1. PICO 中的 C 和 O 可以为空，但尽量提供以获得更精确的搜索结果
2. 搜索关键词**禁止添加年份时间**，年份过滤通过 API 的 `filter_condition` 实现
3. 搜索结果会自动按 `weight × reranker_score` 排序，权重越高的文献越靠前
4. 每个数据库每次搜索最多返回 20 条原始结果，经过去重和排序后最终返回前 10 条
5. 建议使用 `--output` 参数将结果保存到文件，避免终端输出过长被截断
