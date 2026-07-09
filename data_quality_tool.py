# -*- coding: utf-8 -*-
"""
===============================================================================
  DataQualityTool — 数据质量检测与清洗工具（单文件版）
  Data Quality Testing & Cleaning Toolkit (Single-File Edition)
===============================================================================
  适用场景 / Use Cases:
    - 数据岗面试作品集展示（突出数据清洗、批量处理、并发、缺陷校验）
    - CSV / JSON 数据接入前的自动化质量把关
    - 日常数据 ETL 流程中的"脏数据"识别与修复

  核心能力 / Core Capabilities:
    1. 脏数据自动生成（用于演示，无需外部数据源）
    2. 数据画像（列类型、空值率、唯一值、分布特征）
    3. 规则引擎：类型校验、范围检查、正则匹配、IQR 异常检测、重复检测
    4. 并发批量清洗（ThreadPoolExecutor，含报错统计）
    5. SQLite 本地持久化（原始数据 → 清洗记录 → 校验结果全程可追溯）
    6. 可视化指标：降噪率、准确率、并发报错数、人工耗时估算
    7. 完善日志系统（操作日志 + 运行日志双通道）

  依赖库（仅主流轻量库）/ Dependencies:
    pip install pandas numpy

  启动方式 / Quick Start:
    python data_quality_tool.py generate   # 生成演示脏数据
    python data_quality_tool.py run        # 一键：画像→校验→清洗→报告
    python data_quality_tool.py report     # 仅查看统计报告

  作者: Data Quality Toolkit
  日期: 2026-07
===============================================================================
"""

import os
import sys
import json
import time
import uuid
import random
import math
import sqlite3
import logging
import argparse
import traceback
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import numpy as np
import pandas as pd

# ──────────────────────────────────────────────────────────────────────────────
# 全局配置 / Global Configuration
# ──────────────────────────────────────────────────────────────────────────────

# 数据库文件路径（SQLite 本地文件，数据持久化）
DB_PATH = Path(__file__).parent / "data_quality.db"

# 演示脏数据输出路径
DEMO_DATA_PATH = Path(__file__).parent / "demo_dirty_data.csv"

# 清洗结果输出路径
CLEAN_OUTPUT_PATH = Path(__file__).parent / "cleaned_data.csv"

# 并发线程数（可根据机器核心数调整）
MAX_WORKERS = 4

# 数据库写锁（保证 SQLite 并发写入安全）
DB_LOCK = Lock()

# ──────────────────────────────────────────────────────────────────────────────
# SQLite 数据库初始化 / Database Schema Setup
# ──────────────────────────────────────────────────────────────────────────────

def get_db_connection() -> sqlite3.Connection:
    """获取数据库连接（每次调用创建新连接，线程安全）"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row          # 让查询结果支持字典式访问
    conn.execute("PRAGMA journal_mode=WAL") # WAL 模式提升并发读性能
    conn.execute("PRAGMA foreign_keys=ON")  # 启用外键约束
    return conn


def init_database():
    """
    初始化数据库表结构。
    表设计遵循规范化原则：原始层 → 规则层 → 校验层 → 清洗层 → 指标层
    每层独立，支持完整的数据血缘追溯。
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # ── 1. 原始数据表 ──
    # 存储导入的原始数据行，每一行以 JSON 格式保存
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS raw_data (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT    NOT NULL,                     -- 来源文件名
            row_index   INTEGER NOT NULL,                     -- 原始行号（0-based）
            raw_values  TEXT    NOT NULL,                     -- JSON 格式的原始字段值
            import_batch TEXT   NOT NULL,                    -- 导入批次 UUID
            import_time TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # ── 2. 校验规则表 ──
    # 存储所有定义的数据校验规则
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS validation_rules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            column_name TEXT    NOT NULL,                     -- 目标列名
            rule_type   TEXT    NOT NULL,                     -- 规则类型（type/range/regex/outlier/required）
            rule_config TEXT    NOT NULL,                     -- JSON 格式的规则参数
            severity    TEXT    NOT NULL DEFAULT 'error',     -- 严重等级: error / warn
            created_time TEXT   NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # ── 3. 校验结果表 ──
    # 每行原始数据 × 每条规则的校验结果
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS validation_results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_id      INTEGER NOT NULL,                    -- 关联 raw_data.id
            column_name TEXT    NOT NULL,                     -- 被校验列名
            rule_type   TEXT    NOT NULL,                     -- 触发的规则类型
            passed      INTEGER NOT NULL,                     -- 1=通过, 0=未通过
            detail      TEXT,                                 -- 失败详情
            check_time  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (raw_id) REFERENCES raw_data(id) ON DELETE CASCADE
        )
    """)

    # ── 4. 清洗记录表 ──
    # 记录每行数据的清洗操作（修复了什么）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cleaning_records (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_id      INTEGER NOT NULL,
            column_name TEXT    NOT NULL,
            issue_type  TEXT    NOT NULL,                     -- 问题类型
            original_value TEXT,                             -- 原始值
            cleaned_value  TEXT,                             -- 清洗后值
            strategy    TEXT    NOT NULL,                     -- 清洗策略
            clean_time  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (raw_id) REFERENCES raw_data(id) ON DELETE CASCADE
        )
    """)

    # ── 5. 清洗后数据表 ──
    # 存储清洗完成后的最终数据（JSON格式）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cleaned_data (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_id      INTEGER NOT NULL UNIQUE,
            source_file TEXT    NOT NULL,
            row_index   INTEGER NOT NULL,
            cleaned_values  TEXT NOT NULL,                    -- JSON 格式的清洗后字段值
            issue_count INTEGER NOT NULL DEFAULT 0,          -- 该行发现的问题数
            clean_batch TEXT    NOT NULL,                    -- 清洗批次 UUID
            clean_time  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (raw_id) REFERENCES raw_data(id) ON DELETE CASCADE
        )
    """)

    # ── 6. 操作日志表 ──
    # 记录每一步操作的执行情况
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS operation_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            operation   TEXT    NOT NULL,                     -- 操作名称
            target      TEXT,                                 -- 操作目标
            records_in  INTEGER DEFAULT 0,                    -- 输入记录数
            records_out INTEGER DEFAULT 0,                    -- 输出记录数
            duration_ms REAL,                                 -- 耗时(毫秒)
            status      TEXT    NOT NULL DEFAULT 'success',   -- 状态: success / error
            detail      TEXT,                                 -- 补充信息
            log_time    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # ── 7. 指标汇总表 ──
    # 每次运行的核心指标快照
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS metrics_snapshot (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_batch       TEXT    NOT NULL,                 -- 运行批次 UUID
            total_records   INTEGER,                          -- 总记录数
            total_fields    INTEGER,                          -- 总字段数
            issues_found    INTEGER,                          -- 发现的问题总数
            issues_fixed    INTEGER,                          -- 已修复的问题数
            noise_reduction_rate REAL,                        -- 降噪率(%): 修复数/总问题数×100
            accuracy_before REAL,                            -- 清洗前数据准确率(%)
            accuracy_after  REAL,                            -- 清洗后数据准确率(%)
            concurrency_errors INTEGER DEFAULT 0,             -- 并发处理中的报错数
            duplicate_count    INTEGER DEFAULT 0,             -- 重复行数
            missing_filled     INTEGER DEFAULT 0,             -- 填充的缺失值数
            outliers_handled   INTEGER DEFAULT 0,             -- 处理的异常值数
            total_duration_ms  REAL,                          -- 总耗时(毫秒)
            estimated_manual_hours REAL,                      -- 估算人工处理耗时(小时)
            snapshot_time   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 创建索引提升查询性能
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_raw_batch ON raw_data(import_batch)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vr_raw ON validation_results(raw_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cr_raw ON cleaning_records(raw_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_clean_batch ON cleaned_data(clean_batch)")

    conn.commit()
    conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# 日志系统 / Logging System
# ──────────────────────────────────────────────────────────────────────────────

def setup_logging():
    """双通道日志：控制台输出 + 文件持久化"""
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)

    # 根 logger 配置
    logger = logging.getLogger("DataQuality")
    logger.setLevel(logging.DEBUG)

    # 控制台 handler（INFO 级别，UTF-8 编码避免中文乱码）
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    # 强制使用 UTF-8 编码（避免 Windows GBK 编码报错）
    try:
        console.setStream(open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1))
    except Exception:
        pass
    console.setFormatter(logging.Formatter(
        '[%(levelname)-5s] %(asctime)s | %(message)s',
        datefmt='%H:%M:%S'
    ))
    logger.addHandler(console)

    # 文件 handler（DEBUG 级别，完整留痕）
    file_handler = logging.FileHandler(
        log_dir / f"data_quality_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s | %(levelname)-7s | %(filename)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(file_handler)

    return logger


log = setup_logging()

# ──────────────────────────────────────────────────────────────────────────────
# 数据库操作辅助函数 / Database Helpers
# ──────────────────────────────────────────────────────────────────────────────

def log_operation(operation: str, target: str = "", records_in: int = 0,
                  records_out: int = 0, duration_ms: float = 0,
                  status: str = "success", detail: str = ""):
    """向 operation_log 表写入一条操作记录"""
    try:
        conn = get_db_connection()
        conn.execute("""
            INSERT INTO operation_log (operation, target, records_in, records_out,
                                       duration_ms, status, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (operation, target, records_in, records_out, duration_ms, status, detail))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"写入操作日志失败: {e}")


def save_metrics(run_batch: str, metrics: dict):
    """保存单次运行的核心指标"""
    try:
        conn = get_db_connection()
        conn.execute("""
            INSERT INTO metrics_snapshot (
                run_batch, total_records, total_fields, issues_found, issues_fixed,
                noise_reduction_rate, accuracy_before, accuracy_after,
                concurrency_errors, duplicate_count, missing_filled,
                outliers_handled, total_duration_ms, estimated_manual_hours
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_batch, metrics.get("total_records"), metrics.get("total_fields"),
            metrics.get("issues_found"), metrics.get("issues_fixed"),
            metrics.get("noise_reduction_rate"), metrics.get("accuracy_before"),
            metrics.get("accuracy_after"), metrics.get("concurrency_errors"),
            metrics.get("duplicate_count"), metrics.get("missing_filled"),
            metrics.get("outliers_handled"), metrics.get("total_duration_ms"),
            metrics.get("estimated_manual_hours")
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"保存指标失败: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# 第一部分：脏数据自动生成 / Synthetic Dirty Data Generator
# ──────────────────────────────────────────────────────────────────────────────
# 目的：无需外部数据源即可演示工具的全部功能
# 生成的数据模拟"电商订单表"中常见的质量问题，包括：
#   - 缺失值（客户名、电话为空）
#   - 格式错误（邮箱无@、日期不规范）
#   - 重复行（完全重复的订单）
#   - 异常值（订单金额极高或为负）
#   - 类型错误（年龄字段出现字符串）
#   - 不一致（产品类目大小写混乱）

# 姓名池（用于随机生成）
FIRST_NAMES = ["张伟", "王芳", "李娜", "赵敏", "刘洋", "陈静", "杨帆", "黄莉",
               "周杰", "吴鑫", "徐辉", "孙悦", "马超", "朱峰", "胡斌", "林洁",
               "何强", "郭涛", "高远", "罗浩", "梁宇", "宋雅", "唐亮", "韩冰",
               "曹瑞", "许鹏", "邓磊", "冯雪", "彭博", "蒋敏"]

# 产品名池
PRODUCTS = ["智能手表S1", "蓝牙耳机Pro", "移动电源20000mAh", "机械键盘K8",
            "USB-C扩展坞", "无线鼠标M3", "4K显示器27寸", "笔记本电脑支架",
            "手机充电器65W", "数据线1.5m", "摄像头1080P", "路由器AX6000"]

# 产品类目（有大小写变体用于制造不一致）
CATEGORIES_CLEAN = ["电子产品", "电脑配件", "手机配件", "办公用品"]
CATEGORIES_DIRTY = CATEGORIES_CLEAN + ["电子产品", "电子 产品", " 电脑配件", "手机配件 ",
                                       "Electronics", "电子产品 ", "办公用品", "手机 配件"]

# 城市池
CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京",
          "西安", "重庆", "苏州", "长沙", "郑州", "天津", "青岛", "厦门"]

# 订单状态
STATUSES = ["已完成", "已发货", "待发货", "已取消", "已退款", "待付款"]


def _maybe_null(value, null_prob=0.06):
    """按概率返回空值（模拟缺失数据）"""
    return None if random.random() < null_prob else value


def _maybe_corrupt(value, corrupt_prob=0.07):
    """按概率返回损坏值（模拟格式错误）"""
    if random.random() < corrupt_prob:
        if isinstance(value, str) and "@" in value:
            # 邮箱损坏：去掉@或域名
            return value.replace("@", "").replace(".com", "")
        return value
    return value


def generate_dirty_data(num_rows: int = 500) -> pd.DataFrame:
    """
    生成模拟脏数据 — 电商订单表。
    每一列都刻意注入了真实场景中常见的数据质量问题。

    列说明：
      order_id      订单ID（字符串，含少量重复）
      customer_name 客户姓名（~6%缺失）
      phone         手机号（~8%格式错误）
      email         邮箱（~7%损坏，~5%缺失）
      city          城市（正常）
      product       产品名（正常）
      category      产品类目（大小写混乱、多余空格、英文混入）
      amount        订单金额（含负值、极大值异常）
      quantity      购买数量（含小数、负数）
      order_date    下单日期（多种日期格式混杂）
      status        订单状态（正常）
      age           年龄（含类型错误：数字中混入字符串）
    """
    random.seed(42)   # 固定种子保证可复现
    np.random.seed(42)

    records = []
    # 基础日期范围
    base_date = datetime(2026, 1, 1)

    for i in range(num_rows):
        # --- order_id: 串行号，10%概率与上一条相同（制造重复） ---
        if i > 0 and random.random() < 0.10:
            order_id = records[-1]["order_id"]
        else:
            order_id = f"ORD-{20260001 + i:06d}"

        # --- customer_name: 6%概率缺失 ---
        name = _maybe_null(random.choice(FIRST_NAMES), null_prob=0.06)

        # --- phone: 生成11位手机号，8%概率格式错误（少位或多字符） ---
        if random.random() < 0.08:
            phone = f"1{random.randint(30, 99)}{random.randint(1000000, 9999999)}"  # 10位
            if random.random() < 0.3:
                phone = f"1{random.randint(30, 99)}x{random.randint(100000, 999999)}"  # 含字母
        else:
            phone = f"1{random.randint(30, 99)}{random.randint(10000000, 99999999)}"

        # --- email: 格式 name@domain.com，7%损坏，5%缺失 ---
        if random.random() < 0.05:
            email = None
        else:
            pinyin = random.choice(["zhangwei", "wangfang", "lina", "zhaomin",
                                     "liuyang", "chenjing", "yangfan", "huangli"])
            email = f"{pinyin}{random.randint(1, 999)}@{random.choice(['qq.com','163.com','gmail.com','outlook.com'])}"
            email = _maybe_corrupt(email, corrupt_prob=0.07)

        # --- city ---
        city = random.choice(CITIES)

        # --- product ---
        product = random.choice(PRODUCTS)

        # --- category: 刻意制造不一致 ---
        if random.random() < 0.30:
            category = random.choice(CATEGORIES_DIRTY)  # 30%用脏类目
        else:
            category = random.choice(CATEGORIES_CLEAN)

        # --- amount: 正态分布（均值200，标准差80），含异常值 ---
        amount = round(np.random.normal(200, 80), 2)
        # 3%概率生成极端异常值
        if random.random() < 0.015:
            amount = round(random.uniform(5000, 20000), 2)  # 极高
        elif random.random() < 0.015:
            amount = round(random.uniform(-500, -0.01), 2)  # 负值

        # --- quantity: 正常1-5，5%包含异常 ---
        if random.random() < 0.05:
            quantity = random.choice([-1, -2, 0, 0.5, 1.5, 99])  # 异常数量
        else:
            quantity = random.randint(1, 5)

        # --- order_date: 多种日期格式混杂 ---
        order_dt = base_date + timedelta(days=random.randint(0, 365))
        fmt_choice = random.random()
        if fmt_choice < 0.50:
            order_date = order_dt.strftime("%Y-%m-%d")               # 标准ISO
        elif fmt_choice < 0.75:
            order_date = order_dt.strftime("%Y/%m/%d")               # 斜线分隔
        elif fmt_choice < 0.90:
            order_date = order_dt.strftime("%d-%m-%Y")               # 日-月-年
        elif fmt_choice < 0.97:
            order_date = order_dt.strftime("%m/%d/%Y")               # 美式
        else:
            order_date = order_dt.strftime("%Y年%m月%d日")            # 中文

        # --- status ---
        status = random.choice(STATUSES)

        # --- age: 18-65，但3%概率混入非数字字符串 ---
        if random.random() < 0.03:
            age = random.choice(["未知", "保密", "N/A", "二十岁", "--"])
        else:
            age = random.randint(18, 65)

        records.append({
            "order_id": order_id,
            "customer_name": name,
            "phone": phone,
            "email": email,
            "city": city,
            "product": product,
            "category": category,
            "amount": amount,
            "quantity": quantity,
            "order_date": order_date,
            "status": status,
            "age": age,
        })

    df = pd.DataFrame(records)
    log.info(f"生成脏数据完成: {len(df)} 行 × {len(df.columns)} 列")
    return df


# ──────────────────────────────────────────────────────────────────────────────
# 第二部分：数据画像 / Data Profiling
# ──────────────────────────────────────────────────────────────────────────────
# 对每一列进行基础统计分析，生成"数据健康报告"
# 输出：每列的类型、空值数、空值率、唯一值数、样本值、异常标志

def profile_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    对 DataFrame 每列进行画像分析。

    返回包含以下字段的 DataFrame：
      - column:         列名
      - dtype:          当前推断类型
      - total_count:    总行数
      - null_count:     空值数
      - null_rate:      空值率(%)
      - unique_count:   唯一值数
      - sample_values:  前5个样本值
      - has_outlier:    是否存在疑似异常
      - issues:         列级别问题描述
    """
    rows = []
    for col in df.columns:
        series = df[col]
        total = len(series)
        null_count = int(series.isna().sum())
        null_rate = round(null_count / total * 100, 2) if total > 0 else 0
        unique_count = series.nunique()
        # 取前5个非空样本值
        sample_vals = series.dropna().head(5).tolist()
        sample_str = ", ".join([str(v)[:30] for v in sample_vals])

        # 判断是否有明显异常
        issues_list = []
        if null_rate > 0:
            issues_list.append(f"缺失率{null_rate}%")
        if series.dtype == object and null_count < total:
            # 检查数值列是否混入非数字（如 age 列）
            numeric_vals = pd.to_numeric(series, errors='coerce')
            non_numeric_count = numeric_vals.isna().sum() - null_count
            if non_numeric_count > 0:
                issues_list.append(f"疑似{non_numeric_count}个非数字值")

        has_outlier = "否" if not issues_list else "是"

        rows.append({
            "column": col,
            "dtype": str(series.dtype),
            "total_count": total,
            "null_count": null_count,
            "null_rate(%)": null_rate,
            "unique_count": unique_count,
            "sample_values": sample_str,
            "has_outlier": has_outlier,
            "issues": "; ".join(issues_list) if issues_list else "无",
        })

    profile_df = pd.DataFrame(rows)
    log.info(f"数据画像完成: {len(profile_df)} 列已分析")
    return profile_df


# ──────────────────────────────────────────────────────────────────────────────
# 第三部分：规则引擎 / Validation Rules Engine
# ──────────────────────────────────────────────────────────────────────────────
# 支持5种规则类型：
#   - type:     类型检查（int, float, str）
#   - range:    数值范围检查（min, max）
#   - regex:    正则表达式匹配（如邮箱格式、手机号格式）
#   - required: 必填检查（非空）
#   - outlier:  IQR 异常值检测（Tukey方法）
#   - unique:   唯一性检查

# 预定义的校验规则集（针对演示数据的列设计）
DEFAULT_VALIDATION_RULES = [
    # (column_name, rule_type, rule_config_json, severity)
    ("customer_name", "required", '{}', "warn"),
    ("phone",        "regex",   '{"pattern": "^1[3-9]\\\\d{9}$", "desc": "11位手机号"}', "error"),
    ("email",        "regex",   '{"pattern": "^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\\\.[a-zA-Z]{2,}$", "desc": "标准邮箱格式"}', "error"),
    ("email",        "required", '{}', "warn"),
    ("amount",       "range",   '{"min": 0.01, "max": 10000}', "error"),
    ("amount",       "outlier", '{"method": "iqr", "factor": 1.5}', "warn"),
    ("quantity",     "type",    '{"expected_type": "int"}', "error"),
    ("quantity",     "range",   '{"min": 1, "max": 100}', "error"),
    ("order_date",   "regex",   '{"pattern": "^\\\\d{4}-\\\\d{2}-\\\\d{2}$", "desc": "YYYY-MM-DD格式"}', "error"),
    ("age",          "type",    '{"expected_type": "int"}', "error"),
    ("age",          "range",   '{"min": 0, "max": 120}', "error"),
    ("category",     "regex",   '{"pattern": "^[\\\\u4e00-\\\\u9fa5a-zA-Z]+$", "desc": "纯中英文无多余符号空格"}', "warn"),
    ("order_id",     "unique",  '{}', "error"),
]


def load_validation_rules(rules_list: list = None):
    """
    将规则列表写入数据库 validation_rules 表。
    rules_list 为空时使用默认规则集。
    """
    if rules_list is None:
        rules_list = DEFAULT_VALIDATION_RULES

    conn = get_db_connection()
    conn.execute("DELETE FROM validation_rules")  # 清空旧规则
    for rule in rules_list:
        col, rtype, config, severity = rule
        conn.execute("""
            INSERT INTO validation_rules (column_name, rule_type, rule_config, severity)
            VALUES (?, ?, ?, ?)
        """, (col, rtype, config, severity))
    conn.commit()
    conn.close()
    log.info(f"加载校验规则: {len(rules_list)} 条")


def _validate_single_field(value, rule_type: str, rule_config: dict) -> tuple:
    """
    对单个字段值执行单条规则校验。

    返回值: (passed: bool, detail: str)
      passed=True  表示通过校验
      passed=False 表示未通过，detail 说明原因
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        # 值为空时的处理：required 规则会失败，其他规则跳过
        if rule_type == "required":
            return False, "值为空"
        return True, "空值自动跳过"

    try:
        if rule_type == "type":
            expected = rule_config.get("expected_type", "str")
            if expected in ("int", "integer"):
                int_val = int(float(str(value)))
                # 检查是否有小数部分
                if float(str(value)) != int_val:
                    return False, f"期望整数，实际为小数 {value}"
            elif expected == "float":
                float(str(value))
            elif expected == "str":
                str(value)
            return True, ""

        elif rule_type == "range":
            num_val = float(value)
            min_v = rule_config.get("min")
            max_v = rule_config.get("max")
            if min_v is not None and num_val < min_v:
                return False, f"值 {num_val} 小于最小值 {min_v}"
            if max_v is not None and num_val > max_v:
                return False, f"值 {num_val} 大于最大值 {max_v}"
            return True, ""

        elif rule_type == "regex":
            import re
            pattern = rule_config.get("pattern", "")
            desc = rule_config.get("desc", pattern)
            if re.match(pattern, str(value)):
                return True, ""
            return False, f"'{value}' 不匹配规则: {desc}"

        elif rule_type == "required":
            return True, ""  # 空值已在前面处理

        elif rule_type == "outlier":
            # 此规则需在批量上下文执行（需要列数据计算IQR）
            # 这里只做标记，实际检测在 validate_dataframe 中统一处理
            return True, ""

        elif rule_type == "unique":
            # 此规则需在批量上下文执行（需要对比全列）
            return True, ""

        else:
            return True, f"未知规则类型: {rule_type}"

    except (ValueError, TypeError) as e:
        return False, f"校验异常: {str(e)}"


def validate_dataframe(df: pd.DataFrame, batch_id: str) -> dict:
    """
    对 DataFrame 执行全部校验规则，结果写入数据库。

    返回值（统计字典）:
      - total_checks:    总校验次数
      - passed_checks:   通过数
      - failed_checks:   未通过数
      - failed_details:  失败明细列表 [{row, column, rule, detail}, ...]
    """
    conn = get_db_connection()
    rules = conn.execute("SELECT * FROM validation_rules").fetchall()
    conn.close()

    if not rules:
        log.warning("未找到校验规则，跳过校验")
        return {"total_checks": 0, "passed_checks": 0, "failed_checks": 0, "failed_details": []}

    # 先获取 raw_data 的 id 映射 (row_index → id)
    conn = get_db_connection()
    raw_map = {}
    for r in conn.execute("SELECT id, row_index FROM raw_data WHERE import_batch=?", (batch_id,)):
        raw_map[r["row_index"]] = r["id"]
    conn.close()

    total_checks = 0
    passed_checks = 0
    failed_checks = 0
    failed_details = []
    results_to_insert = []

    # ── 预处理：为 outlier 规则计算 IQR 边界 ──
    outlier_configs = {}  # {column_name: (q1, q3, iqr, lower, upper)}
    for rule in rules:
        if rule["rule_type"] == "outlier" and rule["column_name"] in df.columns:
            col = rule["column_name"]
            config = json.loads(rule["rule_config"])
            factor = config.get("factor", 1.5)
            series = pd.to_numeric(df[col], errors='coerce').dropna()
            if len(series) > 0:
                q1 = series.quantile(0.25)
                q3 = series.quantile(0.75)
                iqr = q3 - q1
                lower = q1 - factor * iqr
                upper = q3 + factor * iqr
                outlier_configs[col] = (q1, q3, iqr, lower, upper)

    # ── 预处理：为 unique 规则找到重复值 ──
    unique_columns = {}
    for rule in rules:
        if rule["rule_type"] == "unique" and rule["column_name"] in df.columns:
            col = rule["column_name"]
            dup_vals = df[col].dropna().duplicated(keep=False)
            unique_columns[col] = set(df.loc[dup_vals, col].tolist())

    # ── 逐行逐规则校验 ──
    for idx, row in df.iterrows():
        raw_id = raw_map.get(idx)
        if raw_id is None:
            continue

        for rule in rules:
            col = rule["column_name"]
            rtype = rule["rule_type"]

            if col not in df.columns:
                continue

            value = row[col]
            config = json.loads(rule["rule_config"])
            total_checks += 1

            # ── outlier 规则特殊处理 ──
            if rtype == "outlier":
                if col in outlier_configs:
                    try:
                        num_val = float(value)
                        _, _, _, lower, upper = outlier_configs[col]
                        if num_val < lower or num_val > upper:
                            passed = False
                            detail = f"值 {num_val} 超出IQR范围 [{lower:.2f}, {upper:.2f}]"
                        else:
                            passed = True
                            detail = ""
                    except (ValueError, TypeError):
                        passed = True   # 非数字跳过 outlier 检测
                        detail = ""
                else:
                    passed = True
                    detail = ""

            # ── unique 规则特殊处理 ──
            elif rtype == "unique":
                if col in unique_columns and value in unique_columns[col]:
                    passed = False
                    detail = f"'{value}' 存在重复"
                else:
                    passed = True
                    detail = ""

            # ── 通用规则 ──
            else:
                passed, detail = _validate_single_field(value, rtype, config)

            if passed:
                passed_checks += 1
            else:
                failed_checks += 1
                failed_details.append({
                    "row": idx, "column": col, "rule": rtype,
                    "value": str(value)[:50], "detail": detail,
                })

            results_to_insert.append((raw_id, col, rtype, 1 if passed else 0, detail))

    # ── 批量写入校验结果 ──
    conn = get_db_connection()
    conn.executemany("""
        INSERT INTO validation_results (raw_id, column_name, rule_type, passed, detail)
        VALUES (?, ?, ?, ?, ?)
    """, results_to_insert)
    conn.commit()
    conn.close()

    log.info(f"校验完成: 总计{total_checks}次, 通过{passed_checks}, 未通过{failed_checks}")

    return {
        "total_checks": total_checks,
        "passed_checks": passed_checks,
        "failed_checks": failed_checks,
        "failed_details": failed_details,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 第四部分：数据清洗引擎 / Data Cleaning Engine
# ──────────────────────────────────────────────────────────────────────────────
# 包含以下清洗策略：
#   - fill_missing:    缺失值填充（数值用中位数，分类用众数，日期用前向填充）
#   - normalize_date:  日期格式统一为 YYYY-MM-DD
#   - normalize_category: 类目标准化（去空格、统一大小写、中文替换英文）
#   - clip_outlier:    异常值裁剪（用IQR边界替换）
#   - fix_type:        类型修复（字符串数字转数值，非数字标记为NaN后填充）
#   - remove_duplicate: 重复数据去重
#   - trim_string:     字符串前后去空格

CATEGORY_MAPPING = {
    # 英文 → 中文类目映射
    "electronics": "电子产品",
    "computer accessories": "电脑配件",
    "phone accessories": "手机配件",
    "office supplies": "办公用品",
}


def _clean_single_row(row_data: dict, columns: list, strategies: dict) -> tuple:
    """
    对单行数据执行清洗操作（被并发调用的核心函数）。

    参数:
      row_data:  单行原始数据 dict {column: value}
      columns:   列名列表
      strategies: 全局清洗策略配置

    返回: (cleaned_dict, issues_list)
      cleaned_dict: 清洗后的数据
      issues_list:  该行发现并处理的问题列表
    """
    cleaned = {}
    issues = []

    for col in columns:
        value = row_data.get(col)
        original_value = value

        # ── Step 1: 字符串去空格 ──
        if isinstance(value, str):
            value = value.strip()

        # ── Step 2: 日期标准化 ──
        if col == "order_date" and value is not None:
            value = _normalize_date_value(value, issues, col)

        # ── Step 3: 类目标准化 ──
        if col == "category" and isinstance(value, str):
            value = _normalize_category_value(value, issues, col)

        # ── Step 4: 类型修复 ──
        if col in ("quantity", "age"):
            value = _fix_numeric_type(value, col, issues)

        # ── Step 5: 异常值裁剪 ──
        if col == "amount":
            value = _clip_outlier_value(value, issues, col)

        # ── Step 6: 缺失值填充 ──
        if value is None or (isinstance(value, float) and np.isnan(value)):
            value = _fill_missing_value(value, col, strategies, issues)

        cleaned[col] = value

        if value != original_value and original_value is not None:
            issues.append({
                "column": col,
                "issue_type": "value_changed",
                "original": str(original_value)[:50],
                "cleaned": str(value)[:50],
                "strategy": "auto_fix",
            })

    return cleaned, issues


def _normalize_date_value(value, issues, col):
    """日期标准化：统一为 YYYY-MM-DD"""
    if not isinstance(value, str):
        return value

    original = value
    formats_to_try = [
        "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%m/%d/%Y",
        "%Y年%m月%d日", "%Y.%m.%d", "%d/%m/%Y",
    ]
    for fmt in formats_to_try:
        try:
            dt = datetime.strptime(value.replace(" ", ""), fmt)
            if dt.year < 2000 or dt.year > 2100:
                continue
            normalized = dt.strftime("%Y-%m-%d")
            if normalized != original:
                issues.append({
                    "column": col, "issue_type": "date_format",
                    "original": original, "cleaned": normalized, "strategy": "normalize_date",
                })
            return normalized
        except ValueError:
            continue
    # 无法解析的日期标记为None
    issues.append({
        "column": col, "issue_type": "invalid_date",
        "original": original, "cleaned": "None", "strategy": "mark_null",
    })
    return None


def _normalize_category_value(value, issues, col):
    """类目标准化"""
    original = value
    cleaned = value.replace(" ", "").strip().lower()

    # 英文映射
    if cleaned in CATEGORY_MAPPING:
        cleaned = CATEGORY_MAPPING[cleaned]
    else:
        # 统一中文大小写和空格
        for cat in CATEGORIES_CLEAN:
            if cleaned == cat.replace(" ", "").lower():
                cleaned = cat
                break

    if cleaned != original and cleaned != original.replace(" ", ""):
        issues.append({
            "column": col, "issue_type": "category_inconsistent",
            "original": original, "cleaned": cleaned, "strategy": "normalize_category",
        })

    # 如果清洗后还是纯英文，尝试映射为中文
    if cleaned.isascii():
        cleaned = CATEGORY_MAPPING.get(cleaned, cleaned)
    return cleaned


def _fix_numeric_type(value, col, issues):
    """修复数值类型"""
    original = value
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if col == "quantity":
                if float(value) != int(value):
                    # 小数数量 → 向上取整
                    fixed = int(float(value) + 0.5)
                    issues.append({
                        "column": col, "issue_type": "decimal_quantity",
                        "original": str(original), "cleaned": str(fixed), "strategy": "round_to_int",
                    })
                    return max(1, fixed)
                return int(value)
            return int(value) if col == "age" else float(value)

        if isinstance(value, str):
            try:
                num = float(value)
                if col == "quantity" and num != int(num):
                    fixed = int(num + 0.5)
                    issues.append({
                        "column": col, "issue_type": "string_to_numeric",
                        "original": value, "cleaned": str(fixed), "strategy": "convert_and_round",
                    })
                    return max(1, fixed)
                issues.append({
                    "column": col, "issue_type": "string_to_numeric",
                    "original": value, "cleaned": str(int(num) if col in ("quantity", "age") else num),
                    "strategy": "convert_type",
                })
                return int(num) if col in ("quantity", "age") else num
            except ValueError:
                # 非数字字符串 → 标记为None
                issues.append({
                    "column": col, "issue_type": "non_numeric_string",
                    "original": value, "cleaned": "None", "strategy": "mark_null",
                })
                return None
        return value
    except Exception:
        return value


def _clip_outlier_value(value, issues, col):
    """裁剪异常值到合理范围 [0.01, 10000]"""
    try:
        num = float(value)
        if num < 0.01:
            issues.append({
                "column": col, "issue_type": "negative_amount",
                "original": str(num), "cleaned": "0.01", "strategy": "clip_to_min",
            })
            return 0.01
        if num > 10000:
            issues.append({
                "column": col, "issue_type": "extreme_amount",
                "original": str(num), "cleaned": "10000", "strategy": "clip_to_max",
            })
            return 10000.0
        return num
    except (ValueError, TypeError):
        return value


def _fill_missing_value(value, col, strategies, issues):
    """填充缺失值"""
    fill_strategy = strategies.get("missing_fill", {}).get(col, "median")
    default_values = strategies.get("default_values", {})

    fill_val = default_values.get(col)

    if fill_val is not None:
        issues.append({
            "column": col, "issue_type": "missing_filled",
            "original": "None", "cleaned": str(fill_val), "strategy": f"fill_{fill_strategy}",
        })
        return fill_val

    # 如果没有默认值，给一个合理默认值
    default_fallbacks = {
        "customer_name": "未知客户",
        "email": "unknown@placeholder.com",
        "phone": "00000000000",
        "amount": 200.0,
        "quantity": 1,
        "age": 30,
    }
    fallback = default_fallbacks.get(col)
    if fallback is not None:
        issues.append({
            "column": col, "issue_type": "missing_filled",
            "original": "None", "cleaned": str(fallback), "strategy": "fill_default",
        })
        return fallback
    return value


def clean_data_concurrent(df: pd.DataFrame, strategies: dict = None) -> tuple:
    """
    并发批量清洗数据。
    使用 ThreadPoolExecutor 并行处理每一行，大幅提升大数据量下的处理速度。

    参数:
      df:         待清洗的DataFrame
      strategies: 清洗策略配置字典

    返回: (cleaned_df, all_issues, concurrency_errors, total_duration_ms)
    """
    if strategies is None:
        strategies = {
            "missing_fill": {
                "customer_name": "mode", "email": "default",
                "amount": "median", "quantity": "median", "age": "median",
            },
            "default_values": {
                "customer_name": "未知客户",
                "email": "unknown@placeholder.com",
            },
        }

    columns = list(df.columns)
    # 将每行转为 dict 列表
    rows_data = [row.to_dict() for _, row in df.iterrows()]

    all_issues = []
    cleaned_rows = [None] * len(rows_data)  # 预分配保证行序
    concurrency_errors = 0
    start_time = time.perf_counter()

    # ── 使用线程池并发清洗 ──
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # 提交所有任务
        future_to_idx = {
            executor.submit(_clean_single_row, row_data, columns, strategies): idx
            for idx, row_data in enumerate(rows_data)
        }

        # 收集结果
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                cleaned_dict, issues = future.result()
                cleaned_rows[idx] = cleaned_dict
                if issues:
                    for iss in issues:
                        iss["row"] = idx
                    all_issues.extend(issues)
            except Exception as e:
                concurrency_errors += 1
                log.error(f"并发清洗行{idx}异常: {e}")
                # 保留原始数据
                cleaned_rows[idx] = rows_data[idx]

    total_duration_ms = (time.perf_counter() - start_time) * 1000

    # ── 去重处理 ──
    cleaned_df = pd.DataFrame([r for r in cleaned_rows if r is not None])
    dup_before = len(cleaned_df)
    cleaned_df = cleaned_df.drop_duplicates(subset=["order_id"], keep="first")
    dup_removed = dup_before - len(cleaned_df)

    log.info(f"并发清洗完成: {len(cleaned_df)}行, 发现{len(all_issues)}个问题, "
             f"并发报错{concurrency_errors}次, 去重{dup_removed}行, "
             f"耗时{total_duration_ms:.0f}ms")

    return cleaned_df, all_issues, concurrency_errors, total_duration_ms, dup_removed


# ──────────────────────────────────────────────────────────────────────────────
# 第五部分：核心流程编排 / Core Pipeline Orchestration
# ──────────────────────────────────────────────────────────────────────────────

def run_full_pipeline(csv_path: str = None):
    """
    一键执行完整数据质量流水线：
      1. 加载/生成数据
      2. 数据画像
      3. 加载校验规则 → 执行校验
      4. 并发批量清洗
      5. 全部结果写入 SQLite
      6. 计算并输出指标报告
    """
    run_batch = str(uuid.uuid4())[:8]
    total_start = time.perf_counter()

    log.info("=" * 60)
    log.info(f"数据质量流水线启动 [批次: {run_batch}]")
    log.info("=" * 60)

    # ── Step 1: 加载数据 ──
    t0 = time.perf_counter()
    if csv_path and os.path.exists(csv_path):
        # 尝试多种编码读取CSV
        df = None
        for encoding in ['utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'latin-1']:
            try:
                df = pd.read_csv(csv_path, encoding=encoding)
                log.info(f"读取CSV成功 [{encoding}]: {csv_path}")
                break
            except (UnicodeDecodeError, Exception):
                continue
        if df is None:
            log.error(f"无法读取CSV文件: {csv_path}")
            return None
    else:
        log.info("未指定CSV或文件不存在，自动生成演示脏数据...")
        df = generate_dirty_data(num_rows=500)

    duration_load = (time.perf_counter() - t0) * 1000
    log_operation("load_data", str(csv_path or "auto_generated"),
                   records_in=len(df), duration_ms=duration_load)

    # ── Step 2: 数据画像 ──
    t0 = time.perf_counter()
    profile = profile_data(df)
    duration_profile = (time.perf_counter() - t0) * 1000
    log.info("\n" + str(profile.to_string(index=False)))
    log_operation("profile", "", records_in=len(df), duration_ms=duration_profile)

    # ── 存入原始数据 ──
    t0 = time.perf_counter()
    conn = get_db_connection()
    # 先清理旧批次数据
    conn.execute("DELETE FROM validation_results")
    conn.execute("DELETE FROM cleaning_records")
    conn.execute("DELETE FROM cleaned_data")
    conn.execute("DELETE FROM raw_data")
    conn.commit()

    for idx, row in df.iterrows():
        raw_json = json.dumps(row.to_dict(), ensure_ascii=False, default=str)
        conn.execute("""
            INSERT INTO raw_data (source_file, row_index, raw_values, import_batch)
            VALUES (?, ?, ?, ?)
        """, (str(csv_path or "auto_generated"), idx, raw_json, run_batch))
    conn.commit()
    conn.close()
    duration_import = (time.perf_counter() - t0) * 1000
    log_operation("import_raw", "raw_data", records_in=len(df), duration_ms=duration_import)

    # ── Step 3: 加载校验规则 & 执行校验 ──
    t0 = time.perf_counter()
    load_validation_rules()
    validation_result = validate_dataframe(df, run_batch)
    duration_validate = (time.perf_counter() - t0) * 1000
    log_operation("validate", "validation_rules",
                   records_in=validation_result["total_checks"],
                   records_out=validation_result["failed_checks"],
                   duration_ms=duration_validate)

    # ── Step 4: 并发清洗 ──
    t0 = time.perf_counter()
    cleaned_df, all_issues, concurrency_errors, duration_clean, dup_removed = clean_data_concurrent(df)

    # ── 写入清洗记录和清洗后数据 ──
    conn = get_db_connection()
    # 构建 row_index → raw_id 映射
    raw_id_map = {}
    for r in conn.execute("SELECT id, row_index FROM raw_data WHERE import_batch=?", (run_batch,)):
        raw_id_map[r["row_index"]] = r["id"]

    for issue in all_issues:
        row_idx = issue.get("row", 0)
        raw_id = raw_id_map.get(row_idx)
        if raw_id:
            conn.execute("""
                INSERT INTO cleaning_records (raw_id, column_name, issue_type,
                                              original_value, cleaned_value, strategy)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (raw_id, issue.get("column", ""), issue.get("issue_type", ""),
                  issue.get("original", ""), issue.get("cleaned", ""), issue.get("strategy", "")))

    for idx, row in cleaned_df.iterrows():
        raw_id = raw_id_map.get(idx)
        if raw_id is None:
            continue
        cleaned_json = json.dumps(row.to_dict(), ensure_ascii=False, default=str)
        issue_count = sum(1 for iss in all_issues if iss.get("row") == idx)
        conn.execute("""
            INSERT INTO cleaned_data (raw_id, source_file, row_index, cleaned_values,
                                      issue_count, clean_batch)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (raw_id, str(csv_path or "auto_generated"), idx, cleaned_json, issue_count, run_batch))

    conn.commit()
    conn.close()
    log_operation("clean", "concurrent_cleaning",
                   records_in=len(df), records_out=len(cleaned_df),
                   duration_ms=duration_clean,
                   detail=f"issues_found={len(all_issues)}, concurrency_errors={concurrency_errors}, dup_removed={dup_removed}")

    # ── 导出清洗结果到CSV ──
    cleaned_df.to_csv(CLEAN_OUTPUT_PATH, index=False, encoding='utf-8-sig')
    log.info(f"清洗结果已导出: {CLEAN_OUTPUT_PATH}")

    # ── Step 5: 计算指标 ──
    total_duration_ms = (time.perf_counter() - total_start) * 1000
    total_fields = len(df.columns) * len(df)
    total_issues = len(all_issues) + dup_removed
    issues_fixed = len(all_issues)  # 所有issue都已尝试修复

    # 降噪率 = 已修复问题数 / 总发现数 × 100
    noise_reduction_rate = round(issues_fixed / total_issues * 100, 2) if total_issues > 0 else 100.0

    # 准确率（清洗前）= (总字段 - 问题字段) / 总字段 × 100
    accuracy_before = round((total_fields - total_issues) / total_fields * 100, 2) if total_fields > 0 else 100.0
    # 简化的清洗后准确率
    accuracy_after = round(min(100.0, accuracy_before + noise_reduction_rate * 0.01 * (100 - accuracy_before)), 2)

    # 估算人工处理耗时（假设每个问题需30秒处理）
    estimated_manual_hours = round(total_issues * 30 / 3600, 2)

    # 统计各类问题
    missing_filled = sum(1 for iss in all_issues if "missing" in iss.get("issue_type", ""))
    outliers_handled = sum(1 for iss in all_issues if any(
        kw in iss.get("issue_type", "") for kw in ["outlier", "extreme", "negative_amount"]))

    metrics = {
        "run_batch": run_batch,
        "total_records": len(df),
        "total_fields": total_fields,
        "issues_found": total_issues,
        "issues_fixed": issues_fixed,
        "noise_reduction_rate": noise_reduction_rate,
        "accuracy_before": accuracy_before,
        "accuracy_after": accuracy_after,
        "concurrency_errors": concurrency_errors,
        "duplicate_count": dup_removed,
        "missing_filled": missing_filled,
        "outliers_handled": outliers_handled,
        "total_duration_ms": total_duration_ms,
        "estimated_manual_hours": estimated_manual_hours,
    }
    save_metrics(run_batch, metrics)

    # ── Step 6: 打印汇总报告 ──
    _print_summary_report(metrics, all_issues, validation_result, profile)

    return metrics


def _print_summary_report(metrics: dict, all_issues: list, validation_result: dict, profile: pd.DataFrame):
    """打印格式化的汇总报告"""
    log.info("\n" + "=" * 60)
    log.info("   [Report] 数据质量检测报告 / Data Quality Report")
    log.info("=" * 60)

    log.info(f"\n  +-------------------------------------------+")
    log.info(f"  |  批次编号:  {metrics['run_batch']:>26s} |")
    log.info(f"  |  总记录数:  {metrics['total_records']:>26d} |")
    log.info(f"  |  总字段数:  {metrics['total_fields']:>26d} |")
    log.info(f"  +-------------------------------------------+")
    log.info(f"  |  发现问题:  {metrics['issues_found']:>26d} |")
    log.info(f"  |  已修复:    {metrics['issues_fixed']:>26d} |")
    log.info(f"  |  降噪率:    {metrics['noise_reduction_rate']:>24.2f}% |")
    log.info(f"  +-------------------------------------------+")
    log.info(f"  |  清洗前准确率: {metrics['accuracy_before']:>21.2f}% |")
    log.info(f"  |  清洗后准确率: {metrics['accuracy_after']:>21.2f}% |")
    log.info(f"  +-------------------------------------------+")
    log.info(f"  |  并发报错数: {metrics['concurrency_errors']:>22d} |")
    log.info(f"  |  去重记录数: {metrics['duplicate_count']:>22d} |")
    log.info(f"  |  缺失填充:   {metrics['missing_filled']:>22d} |")
    log.info(f"  |  异常值处理: {metrics['outliers_handled']:>22d} |")
    log.info(f"  +-------------------------------------------+")
    log.info(f"  |  程序总耗时: {metrics['total_duration_ms']:>20.0f}ms |")
    log.info(f"  |  估算人工:   {metrics['estimated_manual_hours']:>21.2f}h |")
    log.info(f"  +-------------------------------------------+")

    # 问题类型分布
    if all_issues:
        log.info(f"\n  [List] 问题类型分布 / Issue Type Distribution:")
        issue_types = {}
        for iss in all_issues:
            t = iss.get("issue_type", "unknown")
            issue_types[t] = issue_types.get(t, 0) + 1
        for t, c in sorted(issue_types.items(), key=lambda x: -x[1]):
            log.info(f"     {t:30s}: {c:5d} 条")

    # 数据画像摘要
    log.info(f"\n  [List] 列级别健康度 / Column Health:")
    for _, row in profile.iterrows():
        flag = "[OK]" if row["null_rate(%)"] == 0 else "[WARN]"
        log.info(f"     {flag} {row['column']:20s} | 空值率:{row['null_rate(%)']:5.1f}% | "
                 f"唯一值:{row['unique_count']:5d} | 异常:{row['has_outlier']}")


# ──────────────────────────────────────────────────────────────────────────────
# 第六部分：CLI 命令行接口 / CLI Interface
# ──────────────────────────────────────────────────────────────────────────────

def cmd_generate(args):
    """CLI 子命令: generate — 生成演示脏数据"""
    log.info(f"生成 {args.rows} 行演示脏数据...")
    df = generate_dirty_data(num_rows=args.rows)
    df.to_csv(DEMO_DATA_PATH, index=False, encoding='utf-8-sig')
    log.info(f"已保存到: {DEMO_DATA_PATH}")
    log.info(f"文件大小: {DEMO_DATA_PATH.stat().st_size / 1024:.1f} KB")

    # 快速展示数据画像
    profile = profile_data(df)
    log.info("\n" + str(profile.to_string(index=False)))

    # 展示前5行样本
    log.info("\n--- 前5行样本 ---")
    log.info("\n" + str(df.head().to_string()))


def cmd_run(args):
    """CLI 子命令: run — 一键执行完整流水线"""
    csv_path = args.input if args.input else None
    if csv_path and not os.path.exists(csv_path):
        log.error(f"文件不存在: {csv_path}")
        sys.exit(1)
    run_full_pipeline(csv_path)


def cmd_report(args):
    """CLI 子命令: report — 查看历史指标"""
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT * FROM metrics_snapshot ORDER BY id DESC LIMIT ?
    """, (args.limit,)).fetchall()
    conn.close()

    if not rows:
        log.info("暂无历史指标记录，请先执行: python data_quality_tool.py run")
        return

    log.info("\n" + "=" * 70)
    log.info("   [History] 历史指标记录 / Metrics History")
    log.info("=" * 70)

    for r in rows:
        log.info(f"\n  批次: {r['run_batch']} | 时间: {r['snapshot_time']}")
        log.info(f"    记录数: {r['total_records']:>6d}  "
                 f"发现问题: {r['issues_found']:>5d}  "
                 f"降噪率: {r['noise_reduction_rate']:>6.1f}%")
        log.info(f"    准确率: {r['accuracy_before']:.1f}% → {r['accuracy_after']:.1f}%  "
                 f"并发报错: {r['concurrency_errors']}")
        log.info(f"    程序耗时: {r['total_duration_ms']:.0f}ms  "
                 f"估算人工: {r['estimated_manual_hours']:.1f}h")


def cmd_stats(args):
    """CLI 子命令: stats — 查看数据库统计概览"""
    conn = get_db_connection()
    stats = {}

    for table in ["raw_data", "cleaned_data", "validation_results",
                   "cleaning_records", "operation_log", "metrics_snapshot"]:
        cnt = conn.execute(f"SELECT COUNT(*) as c FROM {table}").fetchone()["c"]
        stats[table] = cnt

    conn.close()

    log.info("\n" + "=" * 50)
    log.info("   [DB] 数据库统计 / Database Statistics")
    log.info("=" * 50)
    log.info(f"  原始数据(raw_data):          {stats['raw_data']:>6d} 行")
    log.info(f"  清洗后数据(cleaned_data):     {stats['cleaned_data']:>6d} 行")
    log.info(f"  校验结果(validation_results): {stats['validation_results']:>6d} 行")
    log.info(f"  清洗记录(cleaning_records):   {stats['cleaning_records']:>6d} 行")
    log.info(f"  操作日志(operation_log):      {stats['operation_log']:>6d} 行")
    log.info(f"  指标快照(metrics_snapshot):   {stats['metrics_snapshot']:>6d} 行")
    log.info(f"  数据库文件: {DB_PATH} ({DB_PATH.stat().st_size / 1024:.1f} KB)")


def main():
    """主入口：解析命令行参数并分发到对应子命令"""
    parser = argparse.ArgumentParser(
        description="DataQualityTool — 数据质量检测与清洗工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例 / Examples:
  python data_quality_tool.py generate          生成500行演示脏数据
  python data_quality_tool.py generate -r 1000  生成1000行演示脏数据
  python data_quality_tool.py run               自动生成数据→画像→校验→清洗→报告
  python data_quality_tool.py run -i data.csv   从CSV文件导入并处理
  python data_quality_tool.py report            查看最近5次运行指标
  python data_quality_tool.py report -l 10      查看最近10次运行指标
  python data_quality_tool.py stats             查看数据库存储统计
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # generate 子命令
    gen_parser = subparsers.add_parser("generate", help="生成演示脏数据")
    gen_parser.add_argument("-r", "--rows", type=int, default=500,
                            help="生成行数（默认500）")
    gen_parser.set_defaults(func=cmd_generate)

    # run 子命令
    run_parser = subparsers.add_parser("run", help="一键执行完整流水线")
    run_parser.add_argument("-i", "--input", type=str, default=None,
                            help="输入CSV文件路径（不指定则自动生成演示数据）")
    run_parser.set_defaults(func=cmd_run)

    # report 子命令
    rep_parser = subparsers.add_parser("report", help="查看历史指标报告")
    rep_parser.add_argument("-l", "--limit", type=int, default=5,
                            help="显示最近N条记录（默认5）")
    rep_parser.set_defaults(func=cmd_report)

    # stats 子命令
    stats_parser = subparsers.add_parser("stats", help="查看数据库统计概览")
    stats_parser.set_defaults(func=cmd_stats)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    # 初始化数据库
    init_database()

    # 分发到对应函数
    args.func(args)


if __name__ == "__main__":
    main()
