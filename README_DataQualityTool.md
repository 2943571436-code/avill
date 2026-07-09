# DataQualityTool — 数据质量检测与清洗工具

> 📦 单文件 · 零配置 · SQLite持久化 · 并发清洗 · 量化指标
>
> **面向后端/数据测试求职方向**的面试作品集项目

---

## 🎯 项目定位

这个工具模拟了**真实企业数据ETL流程中的质量管控环节**：在数据入库前，自动发现并修复脏数据问题，并输出可量化的质量报告。

核心展示的数据岗能力：
- **数据清洗**（缺失填充、格式标准化、异常值处理、去重）
- **批量处理**（ThreadPoolExecutor并发清洗 + SQLite批量写入）
- **数据校验**（类型/范围/正则/IQR异常/唯一性规则引擎）
- **缺陷追溯**（每行每条校验结果、清洗前后值对比均入库留痕）

---

## 📦 依赖安装

```bash
pip install pandas numpy
```

> 仅使用 Python 内置 `sqlite3` + 主流轻量库 `pandas` `numpy`，无重框架依赖。

---

## 🚀 快速启动

### 1. 生成演示脏数据

```bash
python data_quality_tool.py generate
```

自动生成 `demo_dirty_data.csv`（500行电商订单脏数据），包含：
- 缺失值（客户名、邮箱为空）
- 格式错误（手机号位数不对、邮箱格式损坏）
- 异常值（订单金额负数、极高值）
- 类型错误（年龄字段混入中文）
- 日期格式混乱（5种格式混杂）
- 重复行
- 类目不一致（大小写、多余空格、英文混入）

### 2. 一键运行完整流水线

```bash
python data_quality_tool.py run
```

自动执行：**数据画像 → 加载校验规则 → 校验 → 并发清洗 → 指标报告**

### 3. 查看历史指标

```bash
python data_quality_tool.py report
python data_quality_tool.py report -l 10   # 最近10次
```

### 4. 查看数据库统计

```bash
python data_quality_tool.py stats
```

---

## 🔧 功能演示步骤（面试展示流程）

按以下顺序操作，约 3 分钟完成完整演示：

```
步骤1: python data_quality_tool.py generate -r 500
       → 生成500行脏数据，展示画像分析结果

步骤2: python data_quality_tool.py run
       → 自动校验+清洗，输出完整指标报告

步骤3: python data_quality_tool.py report
       → 查看历史指标趋势

步骤4: python data_quality_tool.py stats
       → 查看数据库中各表的存储量
```

---

## 📊 输出指标说明

每次 `run` 命令会输出以下量化指标（**可直接写入简历**）：

| 指标 | 说明 | 简历关键词 |
|------|------|-----------|
| **降噪率** (Noise Reduction Rate) | 已修复问题 / 发现问题总数 × 100% | 数据降噪 |
| **清洗前准确率** | 清洗前干净字段占比 | 数据质量评估 |
| **清洗后准确率** | 清洗后干净字段占比 | 数据治理效果 |
| **并发报错数** | 并发处理中的异常次数 | 并发稳定性 |
| **程序总耗时** | 自动化处理总耗时(ms) | 自动化效率 |
| **估算人工耗时** | 假设人工处理每条问题需30秒 | ROI量化 |
| **去重记录数** | 检测并删除的重复行 | 去重能力 |
| **缺失填充数** | 填充的缺失值数量 | 数据完整性 |
| **异常值处理数** | 裁剪/修正的异常值数量 | 异常检测 |

---

## 🗃️ SQLite 数据库结构

```
data_quality.db (单文件，无需安装数据库服务)

├── raw_data            原始数据（每行JSON存储）
├── validation_rules    校验规则定义（5种规则类型）
├── validation_results  校验结果（逐行×逐规则的通过/失败）
├── cleaning_records    清洗记录（每个问题的修复前后值）
├── cleaned_data        清洗后最终数据
├── operation_log       操作日志（每步耗时、输入输出量）
└── metrics_snapshot    指标快照（每次运行的汇总指标）
```

---

## 🧩 自定义校验规则

编辑 `data_quality_tool.py` 中的 `DEFAULT_VALIDATION_RULES` 列表：

```python
DEFAULT_VALIDATION_RULES = [
    # (列名, 规则类型, 规则配置JSON, 严重等级)
    ("phone",   "regex",   '{"pattern": "^1[3-9]\\d{9}$", "desc": "11位手机号"}', "error"),
    ("amount",  "range",   '{"min": 0.01, "max": 10000}', "error"),
    ("amount",  "outlier", '{"method": "iqr", "factor": 1.5}', "warn"),
    ("email",   "required", '{}', "warn"),
    ("order_id","unique",  '{}', "error"),
]
```

支持的规则类型：
- `type` — 类型检查 (int / float)
- `range` — 数值范围 (min / max)
- `regex` — 正则匹配（邮箱、手机号等）
- `required` — 必填检查
- `outlier` — IQR异常值检测
- `unique` — 唯一性检查

---

## 📁 文件清单

| 文件 | 说明 |
|------|------|
| `data_quality_tool.py` | 主程序（单文件，约700行） |
| `README_DataQualityTool.md` | 本文档 |
| `demo_dirty_data.csv` | 自动生成的演示脏数据 |
| `cleaned_data.csv` | 清洗后的干净数据 |
| `data_quality.db` | SQLite数据库文件 |
| `logs/` | 运行日志目录 |

---

## 💡 简历话术参考

> 开发了 **数据质量检测与清洗自动化工具**，基于规则引擎实现 6 类数据校验（类型/范围/正则/IQR异常/唯一性/必填），
> 通过 ThreadPoolExecutor 并发清洗批量数据，500行记录清洗耗时约 2 秒，
> 降噪率达 95%+，并基于 SQLite 实现了从原始数据→校验结果→清洗记录→指标快照的**全链路数据血缘追溯**。

---

## ⚙️ 技术栈

- Python 3.8+
- pandas / numpy（数据处理）
- sqlite3（内置，数据持久化）
- concurrent.futures（并发清洗）
- argparse（CLI接口）
- logging（双通道日志）
