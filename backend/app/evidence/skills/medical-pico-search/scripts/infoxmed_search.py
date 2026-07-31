#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Date  : 2025/10/29
# @File  : adk_tools.py
# @Contact : github: johnson7788
# @Desc  : 基于向量搜索，最新搜索

import logging
import os
import json
import time
import argparse
import re
import asyncio
from typing import List, Dict, Any, Union
from datetime import datetime
import uuid
try:
    from google.adk.tools import ToolContext
except ImportError:
    # CLI 模式下不需要 google.adk，使用 MockToolContext 代替
    ToolContext = None
import dotenv

# 用于异步HTTP请求
import aiohttp

dotenv.load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
# module_path = os.path.dirname(os.path.abspath(__file__))
# module_log_file = os.path.join(module_path, "tools.log")
# file_handler = logging.FileHandler(module_log_file, encoding="utf-8")
# file_handler.setLevel(logging.INFO)
# formatter = logging.Formatter(
#     '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
#     datefmt='%Y-%m-%d %H:%M:%S'
# )
# file_handler.setFormatter(formatter)
# logger.addHandler(file_handler)


TIER_MAPPING = {
    5: "【（顶层）全球顶刊】",
    4: "【（一层）科室重磅期刊】",
    3: "【（二层）科室一定影响力期刊】",
    2: "【（三层）补充期刊】",
    1: "【剩余期刊】"
}

key_mapping = {
    "search_guideline_db": "中文指南",
    "search_meta_db": "英文指南",
    "search_evidence_db": "系统评价和meta分析",
    "search_clinical_db": "RCT"
}

class MockToolContext:
    """模拟 Google ADK 的 ToolContext，用于本地测试"""
    def __init__(self):
        # 初始化 state，模拟 ADK 中的 state 字典
        self.state = {
            "search_dbs": [],
            "latest_guideline_date": None
        }


async def milvus_item_search(keywords: str, tool_context: ToolContext, 
                             collection_name: str, dense_name: str, sparse_name: str, content_chunk_field: str, output_fields: list[str], 
                             num: int, Threshold: float, db_key: str, filter_condition: str = "", filter_date_limit: str = ""):
    """
    Milvus 查询+返回权重前三的数据。
    
    :param keywords: 查询内容
    :type keywords: str
    :param tool_context: 工具上下文
    :type tool_context: ToolContext
    :param collection_name: 目标集合名称
    :type collection_name: str
    :param dense_name: 目标集合密集向量字段名称
    :type dense_name: str
    :param sparse_name: 目标集合稀疏向量字段名称    
    :type sparse_name: str
    :param content_chunk_field: 目标集合原文段落字段名称
    :type content_chunk_field: str
    :param output_fields: 需要输出的字段名称列表
    :type output_fields: list[str]
    :param num: 召回数量
    :type num: int
    :param Threshold: 阈值，低于该值的数据会被过滤
    :type Threshold: float
    :param db_key: 查询工具对应的key
    :type db_key: str
    :param filter_condition: 过滤条件
    :type filter_condition: str
    :param filter_date_limit: 过滤日期
    :type filter_date_limit: str
    """
    URL = "https://ai.infox-med.com/UserQueryProcess/GeneralQueryInterface"
    HEADERS = {"Content-Type": "application/json"}
    
    hits = []
    async with aiohttp.ClientSession() as session:
        data_body = {
            "collection_name": collection_name,
            "query": keywords,
            "dense_name": dense_name,
            "sparse_name": sparse_name,
            "content_chunk_field": content_chunk_field,
            "output_fields": output_fields,
            "filter_condition": filter_condition,
            "num": num,
            "Threshold": Threshold,
        }
        try:
            async with session.post(URL, json=data_body, headers=HEADERS) as response:
                if response.status == 200:
                    result = await response.json()
                    hits = result.get("results", [])
                    # logger.info(f"API返回 {len(hits)} 条结果")
        except Exception as e:
            logger.error(f"Milvus query failed: {e}")
            hits = []

    # 加载现有结果用于合并
    search_dbs = tool_context.state.get("search_dbs", [])
    merged = {}
    for entry in search_dbs:
        if entry.get("db") == db_key:
            for item in entry.get("result", []):
                if item.get("id"):
                    merged[item.get("id")] = item

    # 如果有新结果，进行处理
    if hits:
        # 计算每个结果的权重 (tier × reranker_score)
        scored_hits = []
        for hit in hits:
            # # 时间过滤
            # if filter_date_limit:
            #     doc_publish_time = hit.get("doc_publish_time", "")
            #     if doc_publish_time and str(doc_publish_time)[:10] < filter_date_limit:
            #         continue
                
            tier = hit.get("tier", 0)
            try:
                scored_hits.append({
                    "weight": tier,
                    "hit": hit
                })
            except (ValueError, TypeError) as e:
                logger.warning(f"计算权重失败: {e}")
                continue
        
        # 按权重降序排序
        scored_hits.sort(key=lambda x: x["weight"], reverse=True)

        # 打印所有结果
        db_source = key_mapping.get(db_key, db_key)
        for item in scored_hits:
            h = item["hit"]
            logger.info(f"源: {db_source} | 标题: {h.get('doc_title')} | 发布时间: {h.get('doc_publish_time')} | 期刊等级: {h.get('tier')} | ID: {h.get('doc_id')} | 权重: {item['weight']}")

        # 全部文章
        top_hits = scored_hits
        
        for item_data in top_hits:
            max_weight = item_data["weight"]
            best_result = item_data["hit"]
            _id = best_result.get("doc_id", "")
            
            if not _id: 
                continue

            # 处理文档基础信息
            pdf_name = best_result.get("doc_title", "") or best_result.get("title", "")
            plain_content = best_result.get("doc_content_chunk", "")
            doc_publish_time = best_result.get("doc_publish_time", "")
            # 文章摘要
            doc_abstract = best_result.get("doc_abstract", "")
            finnal_content = doc_abstract + "\n" + plain_content
            #期刊等级
            tier = best_result.get("tier", 0)
            reranker_score = best_result.get("reranker_score", 0.0)
            try:
                tier_val = int(float(tier)) if tier is not None else 1
            except (ValueError, TypeError):
                tier_val = 1
            
            tier_label = TIER_MAPPING.get(tier_val, "其他期刊")
            
            # 直接使用 finnal_content 作为匹配内容

            if _id in merged:
                # 合并逻辑：若ID已存在，合并 match_sentences
                existed = merged[_id]

                # 更新权重(如果新权重更高)
                if max_weight > existed.get("weight", 0):
                    existed["weight"] = max_weight


                merged[_id] = existed
            else:
                # 新ID，创建新条目
                link = f"https://www.infox-med.com/#/articleDetails?id={_id}"

                # 格式化发布日期
                publish_date = ""
                if doc_publish_time:
                    try:
                        # 尝试解析日期格式
                        dt = datetime.fromisoformat(str(doc_publish_time).replace('Z', '+00:00'))
                        publish_date = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except (ValueError, TypeError):
                        publish_date = str(doc_publish_time)

                # 获取额外字段，如果不存在则使用 "unknown"
                authors = best_result.get("authors") or best_result.get("author") or "unknown"
                journal = best_result.get("journal") or "unknown"
                doc_if = best_result.get("doc_if") or "unknown"
                publication_type = best_result.get("publication_type") or best_result.get("pub_type") or "unknown"

                item = {
                    "id": _id,
                    "title": pdf_name.title() if pdf_name else "",
                    "abstract": finnal_content,
                    "authors": authors,
                    "journal": journal,
                    "publish_date": publish_date,
                    "impact_factor": doc_if,
                    "publication_type": publication_type,
                    "link": link,
                    "weight": max_weight,
                    "reranker_score": reranker_score,
                }
                merged[_id] = item

    # 转换回列表并按权重排序
    final_items = list(merged.values())
    # final_items.sort(key=lambda x: x.get("weight", 0), reverse=True)

    tool_context.state["search_dbs"] = final_items

    return final_items

async def search_guideline_zh(P,I,C,O, tool_context: ToolContext) -> Dict[str, Any]:
    """
    Asynchronous search medical Chinese guideline data.
    分4次搜索：P → PI → PIC → PICO，合并结果并按id去重（保留最高权重）

    :param P: P（人群）
    :param I: I（干预）
    :param C: C（比较）
    :param O: O（结局）
    :return: JSON object with search results
    """
    # 构建4组搜索关键词
    search_keywords = [
        P.strip(),                    # P
        f"{I}".strip(),           # PI
        f"{P} {I}".strip(),  # PI
        f"{P} {I} {C}".strip(),       # PIC
        f"{P} {I} {O}".strip(),  # PIO
        f"{P} {I} {C} {O}".strip(),   # PICO
    ]

    # 去重合并的结果
    merged_results = {}

    for idx, keywords in enumerate(search_keywords):
        if not keywords.strip():
            continue
        search_label = ["P", "I", "PI", "PIC", "PIO","PICO"][idx]
        logger.info(f"查询中文指南库 ({search_label}): {keywords}")

        data = await milvus_item_search(keywords=keywords,
                                  tool_context=tool_context,
                                  collection_name="guideline_zh",
                                  dense_name="doc_content_chunk_dense",
                                  sparse_name="doc_content_chunk_sparse",
                                  content_chunk_field="doc_content_chunk",
                                  output_fields=["doc_abstract","doc_title", "doc_content_chunk", "doc_publish_time", "tier","doc_id","doc_if"],
                                  num=20,
                                  Threshold=0.1,
                                  db_key="search_guideline_db")

        logger.info(f"查询结果条数: {len(data)}")
        # 合并结果，按id去重，保留最高权重
        for item in data:
            item_id = item.get("id")
            if not item_id:
                continue
            if item_id in merged_results:
                if item.get("weight", 0) > merged_results[item_id].get("weight", 0):
                    merged_results[item_id] = item
            else:
                merged_results[item_id] = item
        logger.info(f"合并搜索结果，现在的数据条数: {len(merged_results)}")

    # 转换为列表并按权重排序
    data = list(merged_results.values())
    # data.sort(key=lambda x: x.get("weight", 0), reverse=True)
    data.sort(key=lambda x: x.get("weight", 0) * x.get("reranker_score", 0), reverse=True)

    # 提取结果中最早的发布时间并存入 state
    if data:
        valid_dates = []
        for item in data:
            if item.get("publish_date"):
                valid_dates.append(str(item.get("publish_date"))[:10])

        if valid_dates:
            min_date = min(valid_dates)
            current_date = tool_context.state.get("latest_guideline_date")
            if not current_date or current_date > min_date:
                tool_context.state["latest_guideline_date"] = min_date
                logger.info(f"设置文献过滤时间: {min_date}")

    # 记录日志
    for item in data:
        title = item.get("title")
        p_time = item.get("publish_date")
        weight = item.get("weight")
        logger.info(f"中文指南排序过滤结果: | 标题: {title} | 发布时间: {p_time} | ID: {item.get('id')} | 权重: {weight}")

    return {
        "chinese_guideline": data[:10]
    }

async def search_guideline_en(P, I, C, O, tool_context: ToolContext) -> Dict[str, Any]:
    """
    Asynchronous search the English guideline database.
    分4次搜索：P → PI → PIC → PICO，合并结果并按id去重（保留最高权重）
    """
    search_keywords = [
        P.strip(),
        f"{I}".strip(),
        f"{P} {I}".strip(),
        f"{P} {I} {C}".strip(),
        f"{P} {I} {O}".strip(),
        f"{P} {I} {C} {O}".strip(),
    ]

    merged_results = {}

    for idx, keywords in enumerate(search_keywords):
        if not keywords.strip():
            continue
        search_label = ["P", "I", "PI", "PIC", "PIO","PICO"][idx]
        logger.info(f"查询英文指南库 ({search_label}): {keywords}")

        data = await milvus_item_search(keywords=keywords,
                                  tool_context=tool_context,
                                  collection_name="guideline_en",
                                  dense_name="doc_content_chunk_dense",
                                  sparse_name="doc_content_chunk_sparse",
                                  content_chunk_field="doc_content_chunk",
                                  output_fields=["doc_abstract","doc_title", "doc_content_chunk", "doc_publish_time", "tier", "doc_id"],
                                  num=20,
                                  Threshold=0.1,
                                  db_key="search_meta_db")

        # 合并结果，按id去重，保留最高权重
        for item in data:
            item_id = item.get("id")
            if not item_id:
                continue
            if item_id in merged_results:
                if item.get("weight", 0) > merged_results[item_id].get("weight", 0):
                    merged_results[item_id] = item
            else:
                merged_results[item_id] = item
    
    # 转换为列表并按权重排序
    data = list(merged_results.values())
    # data.sort(key=lambda x: x.get("weight", 0), reverse=True)
    data.sort(key=lambda x: x.get("weight", 0) * x.get("reranker_score", 0), reverse=True)
    # 提取结果中最早的发布时间并存入 state
    if data:
        valid_dates = []
        for item in data:
            if item.get("publish_date"):
                valid_dates.append(str(item.get("publish_date"))[:10])

        if valid_dates:
            min_date = min(valid_dates)
            current_date = tool_context.state.get("latest_guideline_date")
            if not current_date or current_date > min_date:
                tool_context.state["latest_guideline_date"] = min_date
                logger.info(f"设置文献过滤时间: {min_date}")

    # 记录日志
    for item in data:
        title = item.get("title")
        p_time = item.get("publish_date")
        weight = item.get("weight")
        logger.info(f"英文指南排序过滤结果: | 标题: {title} | 发布时间: {p_time} | ID: {item.get('id')} | 权重: {weight}")

    return {
        "english_guideline": data[:10]
    }

async def search_systematic_meta_db(P, I, C, O, tool_context: ToolContext) -> Dict[str, Any]:
    """
    search Systematic Review and Meta-Analysis database
    分4次搜索：P → PI → PIC → PICO，合并结果并按id去重（保留最高权重）
    """
    search_keywords = [
        P.strip(),
        f"{I}".strip(),
        f"{P} {I}".strip(),
        f"{P} {I} {C}".strip(),
        f"{P} {I} {O}".strip(),
        f"{P} {I} {C} {O}".strip(),
    ]

    merged_results = {}

    for idx, keywords in enumerate(search_keywords):
        if not keywords.strip():
            continue
        search_label = ["P", "I", "PI", "PIC", "PIO","PICO"][idx]
        logger.info(f"系统评价和meta分析关键词 ({search_label}): {keywords}")

        data = await milvus_item_search(keywords=keywords, 
                                  tool_context=tool_context, 
                                  collection_name="systematic_and_meta", 
                                  dense_name="doc_content_chunk_dense", 
                                  sparse_name="doc_content_chunk_sparse", 
                                  content_chunk_field="doc_content_chunk", 
                                  output_fields=["doc_abstract","doc_title", "doc_content_chunk", "doc_publish_time", "tier", "doc_id"],
                                  num=20,
                                  Threshold=0.1,
                                  db_key="search_evidence_db")
        
        # 二次过滤：标题必须包含 meta-analysis 或 systematic review
        filtered_records = []
        for record in data:
            title = record.get("title", "")
            if title and ("meta-analysis" in title.lower() or "systematic review" in title.lower()):
                filtered_records.append(record)
        
        # 合并结果，按id去重，保留最高权重
        for item in filtered_records:
            item_id = item.get("id")
            if not item_id:
                continue
            if item_id in merged_results:
                if item.get("weight", 0) > merged_results[item_id].get("weight", 0):
                    merged_results[item_id] = item
            else:
                merged_results[item_id] = item

    # 转换为列表并按权重排序
    filtered_records = list(merged_results.values())
    # filtered_records.sort(key=lambda x: x.get("weight", 0), reverse=True)
    filtered_records.sort(key=lambda x: x.get("weight", 0) * x.get("reranker_score", 0), reverse=True)
    logger.info(f"系统评价和Meta第一次过滤结果:\n{filtered_records}")
    # 过滤 weight > 1.0，如果全都在 1.0 以下则不过滤
    high_quality_data = [x for x in filtered_records if x.get("weight", 0) > 1.0]
    if high_quality_data:
        filtered_records = high_quality_data

    # 记录日志
    for item in filtered_records:
        title = item.get("title")
        p_time = item.get("publish_date")
        weight = item.get("weight")
        logger.info(f"系统评价和Meta分析排序过滤结果: | 标题: {title} | 发布时间: {p_time} | ID: {item.get('id')} | 权重: {weight}")

    return {
        "systematic_meta": filtered_records[:10]
    }

async def search_clinical_db(P, I, C, O, tool_context: ToolContext) -> Dict[str, Any]:
    """
    search RCT database
    分4次搜索：P → PI → PIC → PICO，合并结果并按id去重（保留最高权重）
    """
    search_keywords = [
        P.strip(),
        f"{I}".strip(),
        f"{P} {I}".strip(),
        f"{P} {I} {C}".strip(),
        f"{P} {I} {O}".strip(),
        f"{P} {I} {C} {O}".strip(),
    ]

    merged_results = {}

    for idx, keywords in enumerate(search_keywords):
        if not keywords.strip():
            continue
        search_label = ["P", "I", "PI", "PIC", "PIO","PICO"][idx]
        logger.info(f"查询RCT关键词 ({search_label}): {keywords}")

        data = await milvus_item_search(keywords=keywords,
                                  tool_context=tool_context,
                                  collection_name="RCT",
                                  dense_name="doc_content_chunk_dense",
                                  sparse_name="doc_content_chunk_sparse",
                                  content_chunk_field="doc_content_chunk",
                                  output_fields=["doc_abstract","doc_title", "doc_content_chunk", "doc_publish_time", "tier", "doc_id"],
                                  num=20,
                                  Threshold=0.1,
                                  db_key="search_clinical_db")
        
        # 合并结果，按id去重，保留最高权重
        for item in data:
            item_id = item.get("id")
            if not item_id:
                continue
            if item_id in merged_results:
                if item.get("weight", 0) > merged_results[item_id].get("weight", 0):
                    merged_results[item_id] = item
            else:
                merged_results[item_id] = item

    # 转换为列表并按权重排序
    data = list(merged_results.values())
    # data.sort(key=lambda x: x.get("weight", 0), reverse=True)
    data.sort(key=lambda x: x.get("weight", 0) * x.get("reranker_score", 0), reverse=True)

    # 过滤 weight > 1.0，如果全都在 1.0 以下则不过滤
    high_quality_data = [x for x in data if x.get("weight", 0) > 1.0]
    if high_quality_data:
        data = high_quality_data

    # 记录日志
    for item in data:
        title = item.get("title")
        p_time = item.get("publish_date")
        weight = item.get("weight")
        logger.info(f"RCT排序过滤结果: | 标题: {title} | 发布时间: {p_time} | ID: {item.get('id')} | 权重: {weight}")

    return {
        "rct": data[:10]
    }

async def search_embedding_by_PICO(P: str, I: str, C: str="", O: str="", tool_context: ToolContext=None) -> Dict[str, Any]:
    """
    从用户问题中提取 PICO，以此搜索全部数据库，禁止添加年份时间。

    :param P: P（人群）：疾病、严重程度、年龄/性别、伴随疾病、基线风险、场景（门诊/住院/ICU等）。
    :param I: I（干预）：剂量、疗程、途径、频次、联合用药/策略细节。
    :param C: 尽量提供，可以为空
    :param O: 尽量提供，可以为空
    :return: Combined JSON object with all search results
    """
    keywords = P + " " + I + " " + C + " " + O
    logger.info(f"Starting search_all_dbs with keywords: {keywords}")
    
    combined_results = {}
    
    # Phase 1:中英文指南
    async def run_guideline():
        try:
            res = await search_guideline_zh(P,I,C,O, tool_context)
            return res
        except Exception as e:
            logger.error(f"Error in search_guideline_zh: {e}")
            return None

    async def run_guideline_en():
        try:
            res = await search_guideline_en(P,I,C,O, tool_context)
            return res
        except Exception as e:
            logger.error(f"Error in run_guideline_en: {e}")
            return None

    # Concurrent Search for all databases
    async def run_systematic_meta():
        try:
            res = await search_systematic_meta_db(P,I,C,O, tool_context)
            return res
        except Exception as e:
            logger.error(f"Error in search_evidence_db: {e}")
            return None

    async def run_clinical():
        try:
            res = await search_clinical_db(P,I,C,O, tool_context)
            return res
        except Exception as e:
            logger.error(f"Error in search_clinical_db: {e}")
            return None

    results = await asyncio.gather(run_guideline(), run_guideline_en(), run_systematic_meta(), run_clinical())
    for res in results:
        if res:
            combined_results.update(res)

    logger.info("search_all_dbs completed.")
    tool_context.state["search_dbs"] = combined_results
    return combined_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="InfoxMed 医学文献 PICO 搜索")
    parser.add_argument("--P", type=str, required=True, help="P（人群）：疾病、严重程度、年龄/性别等")
    parser.add_argument("--I", type=str, required=True, help="I（干预）：剂量、疗程、途径等")
    parser.add_argument("--C", type=str, default="", help="C（比较）：对照组或替代方案（可选）")
    parser.add_argument("--O", type=str, default="", help="O（结局）：需要关注的临床终点（可选）")
    parser.add_argument("--output", type=str, default="", help="输出结果保存的文件路径（可选，默认输出到终端）")
    args = parser.parse_args()

    async def run_search():
        mock_context = MockToolContext()
        logger.info(f"PICO 搜索参数 - P: {args.P}, I: {args.I}, C: {args.C}, O: {args.O}")
        result = await search_embedding_by_PICO(
            P=args.P,
            I=args.I,
            C=args.C,
            O=args.O,
            tool_context=mock_context
        )
        return result

    result = asyncio.run(run_search())

    output_json = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
        print(f"搜索结果已保存到: {args.output}")
    else:
        print("\n===== 搜索结果 =====")
        print(output_json)