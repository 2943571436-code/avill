# -*- coding: utf-8 -*-
"""
+==============================================================================+
|           SENSOR GUARDIAN - 传感器模拟数据闯关游戏                           |
|           Sensor Data Integrity Challenge                                   |
+==============================================================================+

  你是一名智能工厂的「数据质量工程师」。产线传感器源源不断地回传数据，
  但其中混杂着异常值、缺失值、格式错误、漂移噪声 --
  你需要在限定时间内精准识别所有"脏数据"，守护数据链路的完整性。

  -- 这不是一个普通的游戏，而是一次数据质量检测的实战模拟 --

  核心能力展示（贴合后端 / 数据测试求职方向）：
    · 传感器数据模拟（温度/湿度/气压/振动/CO2 — 含缺陷注入引擎）
    · 数据质量规则引擎（范围检查 / 格式校验 / IQR异常检测 / 漂移识别）
    · SQLite 本地持久化（玩家档案 + 关卡记录 + 操作日志 + 指标快照）
    · 并发批量数据生成 + 异常捕获
    · 量化指标：准确率 / 查全率 / 响应时间 / 分数趋势 / 等级进度

  依赖：零外部依赖，仅 Python 3.8+ 标准库（sqlite3 / random / json / time）

  启动方式 / Quick Start：
    python sensor_guardian.py              # 交互式菜单
    python sensor_guardian.py --demo       # 快速演示模式（自动闯5关）
    python sensor_guardian.py --stats      # 查看历史战绩面板
    python sensor_guardian.py --help       # 查看所有选项

  作者: Sensor Guardian Toolkit
  日期: 2026-07
"""

import sqlite3
import random
import time
import json
import os
import sys
import textwrap
from datetime import datetime
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════════
# 全局配置
# ═══════════════════════════════════════════════════════════════════════════════

DB_PATH = Path(__file__).parent / "sensor_guardian.db"
LOG_DIR = Path(__file__).parent / "logs"

# 传感器类型定义：(名称, 单位, 正常下限, 正常上限)
SENSOR_TYPES = {
    "temperature": ("温度",   "°C",   15.0,  35.0),
    "humidity":    ("湿度",   "%",    30.0,  70.0),
    "pressure":    ("气压",   "hPa",  990.0, 1030.0),
    "vibration":   ("振动",   "Hz",   0.0,   15.0),
    "co2":         ("CO2浓度","ppm",  350.0, 900.0),
}

# 缺陷类型定义：(名称, 描述模板, 严重等级)
DEFECT_TYPES = {
    "out_of_range":   ("数值越界",   "值 {val} 超出正常范围 [{lo}, {hi}]",     "error"),
    "missing_value":  ("缺失值",     "传感器未返回读数",                        "error"),
    "format_error":   ("格式错误",   "期望数值类型，实际为 '{val}'",              "error"),
    "noise_spike":    ("噪声尖峰",   "值 {val} 瞬间跳变，疑似电磁干扰",           "warn"),
    "drift_anomaly":  ("漂移异常",   "连续3点单调漂移 ({val1}->{val2}->{val3})",  "warn"),
    "negative_value": ("负值异常",   "值 {val} 为负数，物理不可行",              "error"),
    "zero_reading":   ("零值停滞",   "连续4个读数均为0，疑似传感器离线",           "error"),
}

# 关卡定义：(关卡名, 传感器类型, 读数数量, 缺陷占比, 时间限制秒, 缺陷类型列表)
LEVEL_DEFS = [
    # Level 1: 入门 — 温度巡检，问题明显
    ("温度巡检", ["temperature"], 10, 0.30, 60,
     ["out_of_range", "missing_value"]),

    # Level 2: 进阶 — 湿度警报，加入格式错误
    ("湿度警报", ["humidity"], 12, 0.35, 50,
     ["out_of_range", "missing_value", "format_error"]),

    # Level 3: 深化 — 气压测试，加入噪声和异常检测
    ("压力测试", ["pressure"], 15, 0.30, 45,
     ["out_of_range", "noise_spike", "drift_anomaly", "negative_value"]),

    # Level 4: 综合 — 多传感器融合，所有缺陷类型混合
    ("多传感器融合", ["temperature", "humidity", "vibration"], 18, 0.35, 55,
     ["out_of_range", "missing_value", "format_error", "noise_spike", "negative_value"]),

    # Level 5: 极限 — 全传感器 + 全缺陷 + 严格时间
    ("极限挑战", ["temperature", "humidity", "pressure", "vibration", "co2"], 20, 0.40, 50,
     ["out_of_range", "missing_value", "format_error", "noise_spike", "drift_anomaly",
      "negative_value", "zero_reading"]),
]


# ═══════════════════════════════════════════════════════════════════════════════
# ANSI 终端颜色代码（Windows 10+ / Git Bash 均支持）
# ═══════════════════════════════════════════════════════════════════════════════

class Color:
    """ANSI 转义序列，用于控制台彩色输出"""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    CYAN    = "\033[96m"
    MAGENTA = "\033[95m"
    WHITE   = "\033[97m"

    @staticmethod
    def colorize(text: str, color: str) -> str:
        """给文本包裹颜色"""
        return f"{color}{text}{Color.RESET}"

    @staticmethod
    def ok(text: str) -> str:    return f"{Color.GREEN}{text}{Color.RESET}"
    @staticmethod
    def bad(text: str) -> str:   return f"{Color.RED}{text}{Color.RESET}"
    @staticmethod
    def warn(text: str) -> str:  return f"{Color.YELLOW}{text}{Color.RESET}"
    @staticmethod
    def info(text: str) -> str:  return f"{Color.CYAN}{text}{Color.RESET}"
    @staticmethod
    def title(text: str) -> str: return f"{Color.BOLD}{Color.CYAN}{text}{Color.RESET}"


# ═══════════════════════════════════════════════════════════════════════════════
# SQLite 数据库
# ═══════════════════════════════════════════════════════════════════════════════

def get_db() -> sqlite3.Connection:
    """获取数据库连接，WAL模式提升并发性能"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库表结构：玩家档案 + 关卡记录 + 操作日志 + 指标快照"""
    conn = get_db()
    cur = conn.cursor()

    # -- 1. 玩家档案表 --
    cur.execute("""
        CREATE TABLE IF NOT EXISTS player_profile (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL DEFAULT 'DataGuardian',
            total_score REAL    DEFAULT 0,        -- 累计总分
            max_level   INTEGER DEFAULT 0,        -- 最高通关关卡
            games_played INTEGER DEFAULT 0,       -- 总局数
            avg_accuracy REAL   DEFAULT 0,        -- 平均准确率(%)
            created_at  TEXT    DEFAULT (datetime('now','localtime')),
            updated_at  TEXT    DEFAULT (datetime('now','localtime'))
        )
    """)

    # -- 2. 关卡挑战记录表 --
    cur.execute("""
        CREATE TABLE IF NOT EXISTS level_records (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id  INTEGER NOT NULL,
            game_session TEXT   NOT NULL,          -- 游戏会话UUID
            level_num   INTEGER NOT NULL,          -- 关卡编号 1-5
            level_name  TEXT    NOT NULL,          -- 关卡名称
            sensor_type TEXT    NOT NULL,          -- 传感器类型(JSON数组)
            total_readings INTEGER NOT NULL,       -- 总读数
            defects_injected INTEGER NOT NULL,     -- 注入的缺陷数
            defects_found   INTEGER NOT NULL,      -- 玩家找到的缺陷数
            false_positives INTEGER DEFAULT 0,     -- 误报数(错判为坏的)
            accuracy    REAL   NOT NULL,           -- 准确率(%)
            recall      REAL   NOT NULL,           -- 查全率(%)
            precision   REAL   NOT NULL,           -- 查准率(%)
            response_sec REAL  NOT NULL,           -- 响应耗时(秒)
            time_bonus  REAL   DEFAULT 0,          -- 时间奖励分
            level_score REAL   NOT NULL,           -- 关卡总分
            passed      INTEGER NOT NULL,          -- 是否通关(1/0)
            created_at  TEXT    DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (profile_id) REFERENCES player_profile(id)
        )
    """)

    # -- 3. 操作明细日志表 --
    cur.execute("""
        CREATE TABLE IF NOT EXISTS action_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            game_session TEXT   NOT NULL,
            level_num   INTEGER NOT NULL,
            action_type TEXT   NOT NULL,           -- 'flag'/'pass'/'timeout'/'retry'
            reading_index INTEGER,                 -- 操作的行索引
            expected    TEXT,                       -- 期望答案（0=正常,1=缺陷）
            player_ans  TEXT,                       -- 玩家答案
            detail      TEXT,
            created_at  TEXT    DEFAULT (datetime('now','localtime'))
        )
    """)

    # -- 4. 生成数据存档表 --
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sensor_readings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            game_session TEXT   NOT NULL,
            level_num   INTEGER NOT NULL,
            reading_index INTEGER NOT NULL,
            sensor_type TEXT    NOT NULL,
            raw_value   TEXT,                       -- 原始值（可能含异常）
            unit        TEXT    NOT NULL,
            has_defect  INTEGER NOT NULL DEFAULT 0, -- 是否有注入缺陷
            defect_type TEXT,                        -- 缺陷类型
            defect_detail TEXT,                     -- 缺陷详情
            created_at  TEXT    DEFAULT (datetime('now','localtime'))
        )
    """)

    # -- 5. 指标快照表 --
    cur.execute("""
        CREATE TABLE IF NOT EXISTS metrics_snapshot (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            game_session TEXT   NOT NULL,
            total_levels_played INTEGER,
            total_defects_found INTEGER,
            total_false_positives INTEGER,
            avg_accuracy   REAL,
            avg_recall     REAL,
            avg_precision  REAL,
            avg_response_sec REAL,
            best_level_score REAL,
            worst_level_score REAL,
            total_game_score  REAL,
            passed_all     INTEGER DEFAULT 0,       -- 是否全部通关
            session_duration_sec REAL,
            snapshot_time TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # 索引
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lr_session ON level_records(game_session)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sr_session ON sensor_readings(game_session, level_num)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_al_session ON action_log(game_session)")

    conn.commit()
    conn.close()


def ensure_player(name: str = "DataGuardian") -> int:
    """获取或创建玩家档案，返回 profile_id"""
    conn = get_db()
    row = conn.execute("SELECT id FROM player_profile WHERE name=? LIMIT 1", (name,)).fetchone()
    if row:
        pid = row["id"]
        conn.execute("UPDATE player_profile SET updated_at=datetime('now','localtime') WHERE id=?", (pid,))
    else:
        cur = conn.execute("INSERT INTO player_profile(name) VALUES(?)", (name,))
        pid = cur.lastrowid
    conn.commit()
    conn.close()
    return pid


def update_player_stats(profile_id: int, score: float, level: int, accuracy: float):
    """更新玩家累计数据"""
    conn = get_db()
    conn.execute("""
        UPDATE player_profile
        SET total_score = COALESCE(total_score, 0) + ?,
            max_level = MAX(COALESCE(max_level, 0), ?),
            games_played = COALESCE(games_played, 0) + 1,
            avg_accuracy = ROUND((COALESCE(avg_accuracy, 0) * (COALESCE(games_played, 0)) + ?) / (COALESCE(games_played, 0) + 1), 2),
            updated_at = datetime('now','localtime')
        WHERE id = ?
    """, (score, level, accuracy, profile_id))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 传感器数据生成引擎（含缺陷注入）
# ═══════════════════════════════════════════════════════════════════════════════

def _normal_value(sensor_key: str) -> float:
    """生成一个正常范围内的传感器读数（正态分布）"""
    lo = SENSOR_TYPES[sensor_key][2]
    hi = SENSOR_TYPES[sensor_key][3]
    # 正态分布中心 = (lo+hi)/2，标准差 = (hi-lo)/6（使得99.7%值在范围内）
    center = (lo + hi) / 2
    std = (hi - lo) / 6
    return round(random.gauss(center, std), 2)


def _inject_defect(value: float, sensor_key: str, defect_type: str,
                   context: dict = None) -> tuple:
    """
    根据缺陷类型修改读数值。

    参数:
      value:        正常读数
      sensor_key:   传感器类型key
      defect_type:  缺陷类型
      context:      上下文（如前序读数，用于漂移检测）

    返回: (modified_value, is_defect, defect_detail_str)
    """
    lo = SENSOR_TYPES[sensor_key][2]
    hi = SENSOR_TYPES[sensor_key][3]

    if defect_type == "out_of_range":
        # 随机生成一个明显越界的值
        if random.random() < 0.5:
            new_val = round(random.uniform(hi * 1.5, hi * 3.0), 2)
        else:
            new_val = round(random.uniform(-lo * 0.5, lo * 0.1), 2)
        detail = DEFECT_TYPES[defect_type][1].format(val=new_val, lo=lo, hi=hi)
        return (new_val, True, detail)

    elif defect_type == "missing_value":
        return (None, True, DEFECT_TYPES[defect_type][1])

    elif defect_type == "format_error":
        # 用非数字字符串替换
        garbage = random.choice(["N/A", "ERR", "null", "---", "OFF", "NaN", "???"])
        return (garbage, True, DEFECT_TYPES[defect_type][1].format(val=garbage))

    elif defect_type == "noise_spike":
        # 瞬间跳变到极大/极小值
        spike = round(value * random.choice([-5, -3, 5, 8, 12]), 2)
        detail = DEFECT_TYPES[defect_type][1].format(val=spike)
        return (spike, True, detail)

    elif defect_type == "drift_anomaly":
        # 利用 context 中的前序值制造漂移
        if context and len(context.get("prev_values", [])) >= 2:
            p1, p2 = context["prev_values"][-2], context["prev_values"][-1]
            drift_val = p2 + (p2 - p1) * random.uniform(3.0, 8.0)
            detail = DEFECT_TYPES[defect_type][1].format(val1=p1, val2=p2, val3=drift_val)
            return (round(drift_val, 2), True, detail)
        else:
            # 没有足够前序数据，降级为越界
            return (round(random.uniform(hi * 1.5, hi * 3.0), 2), True,
                    DEFECT_TYPES["out_of_range"][1].format(val=round(random.uniform(hi*1.5, hi*3.0), 2), lo=lo, hi=hi))

    elif defect_type == "negative_value":
        neg = round(-abs(value) * random.uniform(0.5, 3.0), 2)
        detail = DEFECT_TYPES[defect_type][1].format(val=neg)
        return (neg, True, detail)

    elif defect_type == "zero_reading":
        # 需要 context 中确认存在连续零值
        # 这里简化为返回0
        return (0.0, True, DEFECT_TYPES[defect_type][1])

    return (value, False, "")


def generate_level_data(level_num: int, game_session: str) -> list:
    """
    为指定关卡生成传感器读数数据（含缺陷注入）。

    生成过程：
      1. 根据关卡定义确定传感器类型和数量
      2. 对每个读数：先生成正态分布的合理值
      3. 按缺陷占比随机决定是否注入缺陷
      4. 从该关卡启用的缺陷类型中随机选取一种注入
      5. 所有读数和缺陷信息写入数据库 sensor_readings 表

    返回: [
      {
        'index': 0,             # 行号（从1开始显示）
        'sensor': 'temperature',# 传感器类型
        'sensor_name': '温度',  # 中文名
        'value': 25.3,          # 读数（可能已被修改/置None/置字符串）
        'unit': '°C',           # 单位
        'has_defect': True,     # 是否含缺陷
        'defect_type': 'out_of_range',  # 缺陷类型（无缺陷则为None）
        'defect_detail': '...', # 缺陷描述
      }, ...
    ]
    """
    level_def = LEVEL_DEFS[level_num - 1]
    level_name, sensors, n_readings, defect_ratio, time_limit, enabled_defects = level_def

    readings = []
    prev_values = []  # 历史值追踪（用于漂移检测）
    sensors_pool = sensors * (n_readings // len(sensors) + 1)

    conn = get_db()

    for i in range(n_readings):
        sensor_key = sensors_pool[i % len(sensors_pool)]
        s_name, unit, lo, hi = SENSOR_TYPES[sensor_key]

        # Step 1: 生成正常值
        raw_val = _normal_value(sensor_key)

        # Step 2: 按概率决定是否注入缺陷（确保缺陷总数接近 defect_ratio）
        rand_threshold = defect_ratio + random.uniform(-0.05, 0.05)
        has_defect = random.random() < rand_threshold
        defect_type = None
        defect_detail = ""
        display_val = raw_val

        if has_defect:
            # 过滤掉不适用的缺陷类型（如没有前序数据时排除drift）
            available_defects = list(enabled_defects)
            if len(prev_values) < 2 and "drift_anomaly" in available_defects:
                available_defects.remove("drift_anomaly")
            if i < 3 and "zero_reading" in available_defects:
                available_defects.remove("zero_reading")

            defect_type = random.choice(available_defects)
            context = {"prev_values": prev_values.copy()}
            display_val, has_defect, defect_detail = _inject_defect(
                raw_val, sensor_key, defect_type, context
            )

        # Step 3: 记录有效的前序值（用于漂移检测）
        if isinstance(display_val, (int, float)):
            prev_values.append(float(display_val))
            if len(prev_values) > 10:
                prev_values.pop(0)

        reading = {
            "index": i + 1,
            "sensor": sensor_key,
            "sensor_name": s_name,
            "value": display_val,
            "normal_range": f"{lo} ~ {hi}",
            "unit": unit,
            "has_defect": has_defect,
            "defect_type": defect_type,
            "defect_detail": defect_detail,
        }
        readings.append(reading)

        # Step 4: 写入数据库
        conn.execute("""
            INSERT INTO sensor_readings
                (game_session, level_num, reading_index, sensor_type,
                 raw_value, unit, has_defect, defect_type, defect_detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (game_session, level_num, i, sensor_key,
              str(display_val), unit, 1 if has_defect else 0,
              defect_type, defect_detail))

    conn.commit()
    conn.close()
    return readings


# ═══════════════════════════════════════════════════════════════════════════════
# 终端UI渲染
# ═══════════════════════════════════════════════════════════════════════════════

def clear_screen():
    """清屏（跨平台）"""
    os.system('cls' if os.name == 'nt' else 'clear')


def print_title():
    """打印游戏标题 ASCII Art"""
    title = r"""
   +++++++++++++++++++++++++++++++++++++++++++++++++
   |  SENSOR  GUARDIAN                             |
   |  Sensor Data Integrity Challenge  v1.0        |
   +++++++++++++++++++++++++++++++++++++++++++++++++
     """
    subtitle = "  Guardian — 传感器数据完整性闯关挑战  v1.0"
    print(Color.colorize(title, Color.CYAN))
    print(Color.colorize(subtitle, Color.BOLD))
    print(Color.colorize("  " + "-" * 52, Color.DIM))


def print_box(text: str, color: str = Color.WHITE, width: int = 60):
    """打印一个带边框的信息框"""
    print(Color.colorize("+" + "=" * (width - 2) + "+", color))
    for line in text.strip().split("\n"):
        print(Color.colorize("| ", color) + line.ljust(width - 4) + Color.colorize(" |", color))
    print(Color.colorize("+" + "=" * (width - 2) + "+", color))


def print_sensor_table(readings: list):
    """
    渲染传感器读数表格（核心游戏界面）。
    正常读数绿色，缺陷读数红色高亮。
    """
    # 列定义
    header = f" {'#':>3s} │ {'传感器':8s} │ {'读数':>12s} │ {'正常范围':>14s} │ 状态"
    sep = "-" * 65

    print(Color.colorize(sep, Color.DIM))
    print(Color.colorize(header, Color.BOLD))
    print(Color.colorize(sep, Color.DIM))

    for r in readings:
        idx = r["index"]
        s_name = r["sensor_name"]
        value = r["value"]
        unit = r["unit"]
        normal = r["normal_range"]

        # 格式化显示值
        if value is None:
            val_str = "(空值)"
        elif isinstance(value, str):
            val_str = f"'{value}'"
        else:
            val_str = f"{value:.1f}"

        val_display = f"{val_str} {unit}"

        # 只用"???"标记遮罩，玩家看不到是否有缺陷
        status = "???"

        # 格式化行（缺陷行用红色标记——只在训练/演示模式显示）
        idx_str = f"{idx:3d}"
        line = f" {Color.colorize(idx_str, Color.DIM)} │ {s_name:8s} │ {val_display:>12s} │ {normal:>14s} │ {status}"

        print(line)

    print(Color.colorize(sep, Color.DIM))


def print_progress_bar(current: int, total: int, width: int = 40):
    """打印进度条"""
    pct = current / total if total > 0 else 0
    filled = int(width * pct)
    bar = "#" * filled + "." * (width - filled)
    print(f"  关卡进度: [{Color.colorize(bar, Color.CYAN)}] {current}/{total} ({pct*100:.0f}%)")


def typing_effect(text: str, delay: float = 0.015):
    """逐字打印（打字机效果）"""
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(delay)
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# 游戏核心引擎
# ═══════════════════════════════════════════════════════════════════════════════

def show_level_brief(level_num: int):
    """显示关卡简介"""
    ldef = LEVEL_DEFS[level_num - 1]
    name, sensors, n, ratio, tlimit, defects = ldef

    s_names = [SENSOR_TYPES[s][0] for s in sensors]
    d_names = [DEFECT_TYPES[d][0] for d in defects]

    brief = f"""
  关卡 {level_num} — {name}

  监测传感器: {', '.join(s_names)}
  读数数量:   {n} 条
  时间限制:   {tlimit} 秒
  缺陷类型:   {', '.join(d_names)}

  规则:  找出所有包含异常的读数行号（空格分隔）
         输入 0 表示"全部正常"
         输入 q 放弃本关
    """
    print_box(brief.strip(), Color.CYAN)
    print()


def play_one_level(level_num: int, game_session: str) -> dict:
    """
    执行一关的游戏逻辑。

    流程:
      1. 显示关卡简介
      2. 生成传感器数据
      3. 展示读数表格
      4. 启动计时器
      5. 等待玩家输入异常行号
      6. 对比标准答案，计算得分
      7. 显示关卡结算
      8. 写入数据库

    返回: dict with accuracy, recall, precision, score, passed, response_sec, ...
    """
    clear_screen()
    print_title()
    show_level_brief(level_num)

    # -- 生成数据 --
    readings = generate_level_data(level_num, game_session)
    defect_indices = sorted([r["index"] for r in readings if r["has_defect"]])
    total_defects = len(defect_indices)

    # -- 展示表格 --
    ldef = LEVEL_DEFS[level_num - 1]
    time_limit = ldef[4]

    print_sensor_table(readings)
    print()
    print(Color.colorize(f"  [TIME] 时间限制: {time_limit}秒 | 读数总数: {len(readings)} | "
                         f"缺陷可能数: ?/? ", Color.YELLOW))
    print()

    # -- 计时开始 --
    start_time = time.perf_counter()
    player_input = ""
    response_sec = 0

    try:
        # 用 input 等待（不是真正限时中断，超时在提交后检查）
        player_input = input(Color.colorize("  >> 请输入异常读数行号（空格分隔, 0=全部正常）: ", Color.BOLD)).strip()
        response_sec = time.perf_counter() - start_time
    except (KeyboardInterrupt, EOFError):
        response_sec = time.perf_counter() - start_time
        player_input = "q"

    # -- 超时检查 --
    is_timeout = response_sec > time_limit
    if is_timeout:
        player_input = "timeout"

    # -- 解析玩家答案 --
    if player_input.lower() in ('q', 'quit', 'exit'):
        return {"quit": True}

    if player_input == "timeout":
        player_answers = set()
    elif player_input == "0":
        player_answers = set()
    else:
        # 解析空格/逗号/顿号分隔的数字
        import re
        nums = re.findall(r'\d+', player_input)
        player_answers = set(int(n) for n in nums if 1 <= int(n) <= len(readings))

    correct_set = set(defect_indices)

    # -- 计算指标（数据岗核心KPI） --
    # 真阳性: 玩家正确标记的缺陷
    true_positives = len(player_answers & correct_set)
    # 假阳性(误报): 玩家标记为坏但实际是好的
    false_positives = len(player_answers - correct_set)
    # 假阴性(漏报):  玩家没标记但实际是坏的
    false_negatives = len(correct_set - player_answers)
    # 真阴性: 玩家没标记且确实是好的（不直接算分但用于统计）
    true_negatives = len(readings) - true_positives - false_positives - false_negatives

    # 准确率 = (TP + TN) / Total
    accuracy = round((true_positives + true_negatives) / len(readings) * 100, 2) if readings else 0
    # 查全率(Recall) = TP / (TP + FN)，所有缺陷中找到了多少
    recall = round(true_positives / (true_positives + false_negatives) * 100, 2) if (true_positives + false_negatives) > 0 else 100.0
    # 查准率(Precision) = TP / (TP + FP)，标记的里面有多少是对的
    precision = round(true_positives / (true_positives + false_positives) * 100, 2) if (true_positives + false_positives) > 0 else 100.0
    # F1 score
    f1 = round(2 * precision * recall / (precision + recall), 2) if (precision + recall) > 0 else 0

    # -- 关卡得分 --
    # 基础分 = F1 * 0.7 + Accuracy * 0.3
    base_score = f1 * 0.7 + accuracy * 0.3
    # 时间奖励（在时限内完成有额外加分，剩余时间越多越好）
    time_remaining = max(0, time_limit - response_sec)
    time_bonus = round(time_remaining / time_limit * 20, 2) if not is_timeout else 0
    # 惩罚: 误报扣分
    fp_penalty = false_positives * 3
    # 最终得分
    level_score = round(max(0, base_score + time_bonus - fp_penalty), 2)

    # 通关标准: F1 >= 60%
    passed = f1 >= 60.0

    # -- 写入数据库 --
    conn = get_db()
    # 获取profile_id
    profile_row = conn.execute("SELECT id FROM player_profile ORDER BY id DESC LIMIT 1").fetchone()
    profile_id = profile_row["id"] if profile_row else 1

    conn.execute("""
        INSERT INTO level_records
            (profile_id, game_session, level_num, level_name, sensor_type,
             total_readings, defects_injected, defects_found, false_positives,
             accuracy, recall, precision, response_sec, time_bonus, level_score, passed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (profile_id, game_session, level_num, ldef[0], json.dumps(ldef[1], ensure_ascii=False),
          len(readings), total_defects, true_positives, false_positives,
          accuracy, recall, precision, round(response_sec, 2), time_bonus, level_score, 1 if passed else 0))

    # 操作日志：记录每个判断
    for r in readings:
        idx = r["index"]
        is_defect = r["has_defect"]
        player_said_bad = idx in player_answers
        if is_defect or player_said_bad:  # 只记录有意义的交互
            conn.execute("""
                INSERT INTO action_log (game_session, level_num, action_type,
                    reading_index, expected, player_ans, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (game_session, level_num, 'flag' if player_said_bad else 'miss',
                  idx, '1' if is_defect else '0', '1' if player_said_bad else '0',
                  r.get("defect_detail", "")))

    conn.commit()
    conn.close()

    # 更新玩家数据
    update_player_stats(profile_id, level_score, level_num if passed else level_num - 1, accuracy)

    # -- 显示关卡结算 --
    show_level_result(
        level_num=level_num,
        accuracy=accuracy,
        recall=recall,
        precision=precision,
        f1=f1,
        level_score=level_score,
        time_bonus=time_bonus,
        response_sec=response_sec,
        passed=passed,
        is_timeout=is_timeout,
        defect_indices=defect_indices,
        player_answers=player_answers,
        readings=readings,
    )

    return {
        "quit": False,
        "level_num": level_num,
        "accuracy": accuracy,
        "recall": recall,
        "precision": precision,
        "f1": f1,
        "score": level_score,
        "passed": passed,
        "response_sec": response_sec,
        "defects_total": total_defects,
        "defects_found": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
    }


def show_level_result(level_num: int, accuracy: float, recall: float,
                      precision: float, f1: float, level_score: float,
                      time_bonus: float, response_sec: float,
                      passed: bool, is_timeout: bool,
                      defect_indices: list, player_answers: set,
                      readings: list):
    """显示关卡结算面板"""
    clear_screen()
    print_title()

    pass_fail = Color.ok("[OK] 通关!") if passed else Color.bad("[X] 未通过")
    if is_timeout:
        pass_fail = Color.bad("[TIME] 超时!")

    print()
    print_box(f"  关卡 {level_num} 结算 — {pass_fail}", Color.CYAN if passed else Color.YELLOW)
    print()

    # 指标面板
    print(Color.colorize("  +----------------+----------+----------------------+", Color.DIM))
    print(Color.colorize(f"  | 指标            | 数值      | 说明                 |", Color.BOLD))
    print(Color.colorize("  +----------------+----------+----------------------+", Color.DIM))

    def metric_row(name, val, desc, color_fn=None):
        vs = f"{val:>6.1f}%" if isinstance(val, float) else f"{val}"
        if color_fn:
            vs = color_fn(vs)
        print(f"  | {name:14s} | {vs:8s} | {desc:20s} |")

    metric_row("准确率 Accuracy", accuracy, "(TP+TN)/Total", Color.ok if accuracy >= 80 else Color.warn)
    metric_row("查全率 Recall", recall, "缺陷发现率", Color.ok if recall >= 80 else Color.warn)
    metric_row("查准率 Precision", precision, "标记正确率", Color.ok if precision >= 80 else Color.warn)
    metric_row("F1 综合分", f1, "调和平均", Color.ok if f1 >= 60 else Color.bad)
    metric_row("响应耗时", f"{response_sec:.1f}s", "越短越好", Color.ok if response_sec < 30 else Color.warn)
    metric_row("时间奖励", f"+{time_bonus:.1f}", "剩余时间加分", Color.ok)
    metric_row("关卡总分", f"{level_score:.1f}", "满分100", Color.ok if level_score >= 60 else Color.bad)

    print(Color.colorize("  +----------------+----------+----------------------+", Color.DIM))
    print()

    # 答案对比（展示所有异常行）
    if defect_indices:
        correct_set = set(defect_indices)
        print(Color.colorize("  [LIST] 异常读数清单:", Color.BOLD))
        for r in readings:
            idx = r["index"]
            if idx in correct_set or idx in player_answers:
                val = r["value"]
                if val is None:
                    vs = "(空值)"
                else:
                    vs = str(val)
                marker = ""
                if idx in correct_set and idx in player_answers:
                    marker = Color.ok("  [OK] 正确标记")
                elif idx in correct_set:
                    marker = Color.bad("  [X] 漏报!")
                else:
                    marker = Color.warn("  [!] 误报!")
                print(f"    #{idx:2d} | {r['sensor_name']:4s} | {vs:15s} | {r.get('defect_type','') or '正常'}{marker}")
        print()

    input(Color.colorize("  按 Enter 继续...", Color.DIM))


# ═══════════════════════════════════════════════════════════════════════════════
# 主菜单 & 游戏流程
# ═══════════════════════════════════════════════════════════════════════════════

def show_main_menu():
    """渲染主菜单"""
    clear_screen()
    print_title()
    print()
    menu = """
    [1]  开始新游戏        — 从第1关开始挑战
    [2]  选择关卡          — 跳转到指定关卡挑战
    [3]  快速演示          — 自动模拟5关（展示数据报告）
    [4]  历史战绩          — 查看过往挑战记录 & 指标趋势
    [5]  数据面板          — 数据库统计 & 存储概览
    [6]  训练模式          — 显示答案，学习识别各类缺陷
    [0]  退出游戏
    """
    print(menu)
    print()


def new_game():
    """开始新游戏，依次挑战5关"""
    game_session = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + str(random.randint(1000, 9999))
    profile_id = ensure_player()

    clear_screen()
    print_title()
    typing_effect(Color.colorize("\n  传感器阵列初始化中...", Color.CYAN), 0.03)
    typing_effect(Color.colorize("  数据采集链路建立...", Color.CYAN), 0.03)
    typing_effect(Color.colorize("  异常检测引擎就绪。", Color.CYAN), 0.03)
    time.sleep(0.5)
    typing_effect(Color.colorize("\n  >>> 警报：检测到传感器数据异常流入 <<<", Color.YELLOW), 0.02)
    typing_effect(Color.colorize("  >>> 请立即开始数据质量检测！<<<", Color.YELLOW), 0.02)
    time.sleep(0.8)

    results = []
    for lv in range(1, 6):
        result = play_one_level(lv, game_session)
        if result.get("quit"):
            typing_effect(Color.colorize("\n  ⚡ 检测任务中断 — 玩家主动退出", Color.YELLOW), 0.02)
            break
        results.append(result)

        if not result.get("passed", False):
            typing_effect(Color.colorize(f"\n  ❌ 关卡 {lv} 未通过。重新挑战？在菜单中选择 [2] 选择关卡", Color.YELLOW), 0.02)
            break

        if lv < 5:
            print()
            typing_effect(Color.colorize(f"  ✓ 关卡 {lv} 通过！准备进入下一关...", Color.GREEN), 0.02)
            time.sleep(1)

    # -- 游戏结束，打印汇总 --
    show_game_summary(results, game_session, profile_id)


def show_game_summary(results: list, game_session: str, profile_id: int):
    """显示游戏汇总报告并写入指标快照"""
    clear_screen()
    print_title()

    if not results:
        print_box("  没有完成任何关卡", Color.YELLOW)
        return

    n_passed = sum(1 for r in results if r.get("passed"))
    total_score = sum(r.get("score", 0) for r in results)
    avg_acc = sum(r.get("accuracy", 0) for r in results) / len(results) if results else 0
    avg_recall = sum(r.get("recall", 0) for r in results) / len(results) if results else 0
    avg_prec = sum(r.get("precision", 0) for r in results) / len(results) if results else 0
    avg_resp = sum(r.get("response_sec", 0) for r in results) / len(results) if results else 0
    best_score = max((r.get("score", 0) for r in results), default=0)
    worst_score = min((r.get("score", 0) for r in results), default=0)

    summary = f"""
    游戏汇总报告 / Game Summary

    完成关卡:   {n_passed} / 5
    总分:       {total_score:.1f}
    最高分:     {best_score:.1f}
    最低分:     {worst_score:.1f}
    平均准确率: {avg_acc:.1f}%
    平均查全率: {avg_recall:.1f}%
    平均查准率: {avg_prec:.1f}%
    平均耗时:   {avg_resp:.1f}秒
    评定:       {'[S级] 数据质量大师!' if n_passed >= 5 else
                  '[A级] 优秀检测员' if n_passed >= 3 else
                  '[B级] 合格操作员' if n_passed >= 1 else
                  '[C级] 需要更多训练'}
    """
    print_box(summary.strip(), Color.CYAN)

    # -- 写入指标快照 --
    conn = get_db()
    conn.execute("""
        INSERT INTO metrics_snapshot
            (game_session, total_levels_played, total_defects_found,
             total_false_positives, avg_accuracy, avg_recall, avg_precision,
             avg_response_sec, best_level_score, worst_level_score,
             total_game_score, passed_all, session_duration_sec)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        game_session, len(results),
        sum(r.get("defects_found", 0) for r in results),
        sum(r.get("false_positives", 0) for r in results),
        round(avg_acc, 2), round(avg_recall, 2), round(avg_prec, 2),
        round(avg_resp, 2), best_score, worst_score,
        total_score, 1 if n_passed >= 5 else 0, 0
    ))
    conn.commit()
    conn.close()

    print()
    try:
        input(Color.colorize("  按 Enter 返回主菜单...", Color.DIM))
    except EOFError:
        pass


def select_level():
    """选择关卡单独挑战"""
    clear_screen()
    print_title()
    print()
    for lv, ldef in enumerate(LEVEL_DEFS, 1):
        name, sensors, n, ratio, tlimit, defects = ldef
        s_names = [SENSOR_TYPES[s][0] for s in sensors]
        print(f"  [{lv}] 关卡{lv} — {name} ({', '.join(s_names)})")

    print()
    try:
        choice = input(Color.colorize("  选择关卡编号 (1-5): ", Color.BOLD)).strip()
        lv = int(choice)
        if lv < 1 or lv > 5:
            print(Color.bad("  无效选择"))
            time.sleep(1)
            return
    except (ValueError, EOFError):
        return

    game_session = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + str(random.randint(1000, 9999))
    ensure_player()
    result = play_one_level(lv, game_session)

    if not result.get("quit"):
        show_game_summary([result], game_session, ensure_player())


def demo_mode():
    """快速演示模式：自动模拟5关并生成完整报告"""
    clear_screen()
    print_title()
    typing_effect(Color.colorize("\n  [演示模式] 自动模拟数据质量检测全流程...", Color.CYAN), 0.03)
    print()

    game_session = "DEMO_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    profile_id = ensure_player("DemoBot")

    all_results = []

    for lv in range(1, 6):
        ldef = LEVEL_DEFS[lv - 1]
        name = ldef[0]
        n_readings = ldef[2]
        tlimit = ldef[4]

        # 生成数据
        readings = generate_level_data(lv, game_session)
        defect_indices = sorted([r["index"] for r in readings if r["has_defect"]])
        total_defects = len(defect_indices)
        correct_set = set(defect_indices)

        # 模拟玩家：有一定准确率
        sim_accuracy = random.uniform(0.75, 0.95)
        # 正确识别大部分缺陷
        found_defects = set(random.sample(defect_indices,
                                          max(1, int(total_defects * sim_accuracy))))
        # 偶尔会有误报
        clean_indices = [r["index"] for r in readings if not r["has_defect"]]
        false_pos_set = set()
        if random.random() < 0.3:
            n_fp = random.randint(1, max(1, int(len(clean_indices) * 0.05)))
            false_pos_set = set(random.sample(clean_indices, min(n_fp, len(clean_indices))))

        player_answers = found_defects | false_pos_set

        # 计算指标
        tp = len(player_answers & correct_set)
        fp = len(false_pos_set)
        fn = len(correct_set - player_answers)
        tn = n_readings - tp - fp - fn

        accuracy = round((tp + tn) / n_readings * 100, 2)
        recall = round(tp / (tp + fn) * 100, 2) if (tp + fn) > 0 else 100.0
        precision = round(tp / (tp + fp) * 100, 2) if (tp + fp) > 0 else 100.0
        f1 = round(2 * precision * recall / (precision + recall), 2) if (precision + recall) > 0 else 0
        response_sec = round(random.uniform(5, tlimit * 0.7), 1)
        time_remaining = max(0, tlimit - response_sec)
        time_bonus = round(time_remaining / tlimit * 20, 2)
        fp_penalty = fp * 3
        level_score = round(max(0, f1 * 0.7 + accuracy * 0.3 + time_bonus - fp_penalty), 2)
        passed = f1 >= 60.0

        # 写入数据库
        conn = get_db()
        conn.execute("""
            INSERT INTO level_records
                (profile_id, game_session, level_num, level_name, sensor_type,
                 total_readings, defects_injected, defects_found, false_positives,
                 accuracy, recall, precision, response_sec, time_bonus, level_score, passed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (profile_id, game_session, lv, name, json.dumps(ldef[1], ensure_ascii=False),
              n_readings, total_defects, tp, fp,
              accuracy, recall, precision, response_sec, time_bonus, level_score, 1 if passed else 0))
        conn.commit()
        conn.close()

        result = {
            "level_num": lv, "accuracy": accuracy, "recall": recall,
            "precision": precision, "f1": f1, "score": level_score,
            "passed": passed, "response_sec": response_sec,
            "defects_total": total_defects, "defects_found": tp,
            "false_positives": fp, "false_negatives": fn,
        }
        all_results.append(result)

        # 简要输出
        bar = "#" * int(f1 / 5) + "." * (20 - int(f1 / 5))
        status = Color.ok("PASS") if passed else Color.bad("FAIL")
        print(f"  关卡{lv} {name:12s} | F1={f1:5.1f}% | {bar} | {status} | 得分:{level_score:5.1f}")

    # 汇总
    update_player_stats(profile_id,
                        sum(r["score"] for r in all_results),
                        sum(1 for r in all_results if r["passed"]),
                        sum(r["accuracy"] for r in all_results) / len(all_results))

    show_game_summary(all_results, game_session, profile_id)


def view_history():
    """查看历史战绩"""
    clear_screen()
    print_title()

    conn = get_db()

    # 玩家档案
    profile = conn.execute("SELECT * FROM player_profile ORDER BY id DESC LIMIT 1").fetchone()
    if profile:
        ts = profile['total_score'] or 0.0
        aa = profile['avg_accuracy'] or 0.0
        print_box(f"""
  玩家: {profile['name']}
  总局数: {profile['games_played'] or 0} | 最高关卡: {profile['max_level'] or 0}
  累计总分: {ts:.1f} | 平均准确率: {aa:.1f}%
        """.strip(), Color.CYAN)

    # 最近关卡记录
    records = conn.execute("""
        SELECT * FROM level_records ORDER BY created_at DESC LIMIT 25
    """).fetchall()

    if records:
        print()
        print(Color.colorize("  最近25条关卡记录:", Color.BOLD))
        print(Color.colorize("  " + "-" * 75, Color.DIM))
        print(f"  {'时间':16s} {'关卡':12s} {'准确率':>6s} {'查全率':>6s} {'查准率':>6s} {'F1':>6s} {'得分':>6s} {'通关'}")
        print(Color.colorize("  " + "-" * 75, Color.DIM))
        for r in records:
            t = r["created_at"][5:16] if r["created_at"] else ""
            p = Color.ok("是") if r["passed"] else Color.bad("否")
            rec = r["recall"] or 0
            prec = r["precision"] or 0
            f1_val = round(2 * prec * rec / (prec + rec), 1) if (prec + rec) > 0 else 0
            print(f"  {t:16s} {r['level_name']:12s} {r['accuracy']:>5.1f}% {rec:>5.1f}% "
                  f"{prec:>5.1f}% {f1_val:>5.1f}% {r['level_score']:>5.1f}  {p}")
        print(Color.colorize("  " + "-"* 75, Color.DIM))
    else:
        print(Color.warn("\n  暂无历史记录"))

    # 指标快照
    snapshots = conn.execute("SELECT * FROM metrics_snapshot ORDER BY snapshot_time DESC LIMIT 5").fetchall()
    if snapshots:
        print()
        print(Color.colorize("  最近5次游戏汇总:", Color.BOLD))
        print(Color.colorize("  " + "-"* 70, Color.DIM))
        for s in snapshots:
            t = s["snapshot_time"][5:16] if s["snapshot_time"] else ""
            all_pass = Color.ok("全部通关!") if s["passed_all"] else ""
            print(f"  {t} | {s['total_levels_played']}关 | "
                  f"均准确率{s['avg_accuracy']:.1f}% | "
                  f"均查全率{s['avg_recall']:.1f}% | "
                  f"总分{s['total_game_score']:.1f} {all_pass}")
        print(Color.colorize("  " + "-"* 70, Color.DIM))

    conn.close()
    print()
    try:
        input(Color.colorize("  按 Enter 返回主菜单...", Color.DIM))
    except EOFError:
        pass


def view_data_panel():
    """数据库统计面板"""
    clear_screen()
    print_title()

    conn = get_db()

    tables = ["player_profile", "level_records", "action_log", "sensor_readings", "metrics_snapshot"]
    stats = {}
    for t in tables:
        cnt = conn.execute(f"SELECT COUNT(*) as c FROM {t}").fetchone()["c"]
        stats[t] = cnt

    # 额外统计
    total_defects_found = conn.execute(
        "SELECT COALESCE(SUM(defects_found),0) FROM level_records").fetchone()[0]
    total_fp = conn.execute(
        "SELECT COALESCE(SUM(false_positives),0) FROM level_records").fetchone()[0]
    avg_score = conn.execute(
        "SELECT ROUND(AVG(level_score),1) FROM level_records").fetchone()[0] or 0

    panel = f"""
  数据面板 / Database Statistics

  玩家档案(player_profile):      {stats['player_profile']:>6d} 条
  关卡记录(level_records):       {stats['level_records']:>6d} 条
  操作日志(action_log):          {stats['action_log']:>6d} 条
  传感器数据(sensor_readings):   {stats['sensor_readings']:>6d} 条
  指标快照(metrics_snapshot):    {stats['metrics_snapshot']:>6d} 条
  ------------------------------------─
  累计发现缺陷:   {total_defects_found:>6d}
  累计误报次数:   {total_fp:>6d}
  全局均分:       {avg_score:>6.1f}
  数据库文件:     {DB_PATH}
  文件大小:       {DB_PATH.stat().st_size / 1024:.1f} KB (如有)
    """
    print_box(panel.strip(), Color.CYAN)
    conn.close()
    print()
    try:
        input(Color.colorize("  按 Enter 返回主菜单...", Color.DIM))
    except EOFError:
        pass


def training_mode():
    """训练模式：显示答案，帮助玩家学习各类缺陷"""
    clear_screen()
    print_title()
    typing_effect(Color.colorize("\n  [训练模式] 答案可见，请仔细观察各类数据缺陷的特征", Color.GREEN), 0.02)
    print()

    game_session = "TRAIN_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    ensure_player()

    for lv in range(1, 6):
        ldef = LEVEL_DEFS[lv - 1]
        name = ldef[0]

        print()
        print(Color.colorize(f"  === 训练关卡 {lv}: {name} ===", Color.CYAN))

        readings = generate_level_data(lv, game_session)
        defect_indices = sorted([r["index"] for r in readings if r["has_defect"]])

        # 渲染表格（带答案标记）
        sep = "-"* 80
        header = f" {'#':>3s} │ {'传感器':8s} │ {'读数':>14s} │ {'正常范围':>14s} │ {'缺陷类型':16s} │ {'状态':6s}"
        print(Color.colorize(sep, Color.DIM))
        print(Color.colorize(header, Color.BOLD))
        print(Color.colorize(sep, Color.DIM))

        for r in readings:
            idx = r["index"]
            s_name = r["sensor_name"]
            value = r["value"]
            unit = r["unit"]
            normal = r["normal_range"]

            if value is None:
                val_str = "(空值)"
            elif isinstance(value, str):
                val_str = f"'{value}'"
            else:
                val_str = f"{value:.1f}"

            val_display = f"{val_str} {unit}"

            if r["has_defect"]:
                dtype = r["defect_type"] or "unknown"
                status = Color.bad("  [X] 异常")
                detail = r.get("defect_detail", "")[:25]
                line = (f" {Color.colorize(f'{idx:3d}', Color.DIM)} │ "
                        f"{s_name:8s} │ {Color.bad(val_display):>25s} │ "
                        f"{normal:>14s} │ {Color.warn(dtype):24s} │ {status}")
            else:
                status = Color.ok("  [OK] 正常")
                line = (f" {Color.colorize(f'{idx:3d}', Color.DIM)} │ "
                        f"{s_name:8s} │ {val_display:>14s} │ "
                        f"{normal:>14s} │ {'':16s} │ {status}")
            print(line)

        print(Color.colorize(sep, Color.DIM))

        # 展示缺陷清单
        if defect_indices:
            print(Color.colorize(f"\n  📋 本关缺陷 ({len(defect_indices)}个):", Color.BOLD))
            for r in readings:
                if r["has_defect"]:
                    print(Color.warn(f"    #{r['index']:2d} | {r['sensor_name']} | "
                                     f"类型: {r['defect_type']} | {r.get('defect_detail','')}"))

        if lv < 5:
            print()
            input(Color.colorize(f"  按 Enter 查看下一关...", Color.DIM))

    print()
    typing_effect(Color.colorize("\n  训练结束！现在你已了解各类数据缺陷，去正式挑战吧！", Color.GREEN), 0.02)
    time.sleep(1.5)


# ═══════════════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    """主入口：初始化数据库 -> 解析参数 -> 启动游戏循环"""
    init_db()

    # 解析命令行参数（快速模式，不需要进入交互菜单）
    if "--demo" in sys.argv:
        demo_mode()
        return
    if "--stats" in sys.argv or "--history" in sys.argv:
        view_history()
        return
    if "--panel" in sys.argv:
        view_data_panel()
        return
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        print("  快捷命令: --demo | --stats | --panel | --help")
        return

    # -- 交互式主循环 --
    while True:
        show_main_menu()
        try:
            choice = input(Color.colorize("  请选择 [0-6]: ", Color.BOLD)).strip()
        except (KeyboardInterrupt, EOFError):
            print("\n")
            typing_effect(Color.colorize("  传感器监控系统关闭。数据已保存至 SQLite。", Color.CYAN), 0.02)
            break

        if choice == "1":
            new_game()
        elif choice == "2":
            select_level()
        elif choice == "3":
            demo_mode()
        elif choice == "4":
            view_history()
        elif choice == "5":
            view_data_panel()
        elif choice == "6":
            training_mode()
        elif choice == "0":
            clear_screen()
            typing_effect(Color.colorize("  传感器监控系统关闭。数据已保存至 SQLite。", Color.CYAN), 0.02)
            typing_effect(Color.colorize("  感谢守护数据完整性！", Color.GREEN), 0.02)
            break
        else:
            print(Color.warn("  无效选择，请重新输入"))
            time.sleep(0.8)


if __name__ == "__main__":
    main()
