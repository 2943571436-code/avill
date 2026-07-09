/**
 * 校内创新创业项目报名系统 — Java 控制台轻量程序
 * ==============================================================
 * 贴合后端 / 数据测试求职方向，突出数据清洗、批量处理、并发、缺陷校验核心能力
 *
 * 功能清单：
 *   1. 新增报名（逐条录入，强校验）
 *   2. 多线程批量导入院系报名名单（线程池并发处理）
 *   3. 重复报名拦截 / 空白表单校验 / 非法字段捕获
 *   4. 按院系统计报名人数，输出统计报表
 *   5. 导出报名数据（CSV / JSON），记录所有操作日志
 *   6. SQLite 本地持久化，支持历史查询
 *   7. 量化对比：自动化校验 vs 人工核对（减少工作量%、缩短交付周期）
 *
 * 编译 & 运行：
 *   ┌─────────────────────────────────────────────────────────┐
 *   │ 1. 下载 SQLite JDBC 驱动:                               │
 *   │    https://github.com/xerial/sqlite-jdbc/releases       │
 *   │    下载 sqlite-jdbc-3.45.1.0.jar 放到当前目录           │
 *   │                                                        │
 *   │ 2. 编译: javac -cp ".;sqlite-jdbc-3.45.1.0.jar" \      │
 *   │              -encoding UTF-8 RegistrationSystem.java    │
 *   │                                                        │
 *   │ 3. 运行: java  -cp ".;sqlite-jdbc-3.45.1.0.jar" \      │
 *   │              -Dfile.encoding=UTF-8 RegistrationSystem   │
 *   └─────────────────────────────────────────────────────────┘
 *
 * Java 版本: 8+
 * 依赖:      sqlite-jdbc (单 JAR, 轻量, 纯 Java 实现)
 */

import java.io.*;
import java.nio.file.*;
import java.sql.*;
import java.text.SimpleDateFormat;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import java.util.regex.Pattern;

// ╔══════════════════════════════════════════════════════════════════════╗
// ║  主类                                                               ║
// ╚══════════════════════════════════════════════════════════════════════╝

public class RegistrationSystem {

    // ── 常量定义 ──
    static final String DB_URL  = "jdbc:sqlite:registration.db";
    static final String DATE_FMT = "yyyy-MM-dd HH:mm:ss";
    static final SimpleDateFormat SDF = new SimpleDateFormat(DATE_FMT);

    // 合法院系列表（用于下拉校验）
    static final String[] VALID_DEPTS = {
        "计算机学院", "软件学院", "电子工程学院", "数学学院",
        "物理学院", "化学学院", "生命科学学院", "经济管理学院",
        "外国语学院", "法学院", "医学院", "机械工程学院"
    };

    // 模拟参数：人工 vs 自动化量化对比
    static final double MANUAL_MINUTES_PER_ENTRY   = 5.0;      // 人工录入每条 5 分钟
    static final double AUTOMATED_SEC_PER_ENTRY    = 0.1;      // 自动化每条 0.1 秒
    static final double MANUAL_ERROR_RATE          = 0.05;     // 人工出错率 5%
    static final double AUTOMATED_ERROR_RATE       = 0.001;    // 自动化出错率 0.1%

    // ── 全局状态 ──
    static Connection conn;
    static final List<String> opLogs = Collections.synchronizedList(new ArrayList<>());
    static final AtomicInteger totalProcessed  = new AtomicInteger(0);
    static final AtomicInteger conflictCaught  = new AtomicInteger(0);
    static final AtomicInteger invalidCaught   = new AtomicInteger(0);
    static final AtomicInteger successImported = new AtomicInteger(0);

    // 线程安全：保护 SQLite 写入（SQLite 本身串行写，此处显式控制）
    static final Object DB_WRITE_LOCK = new Object();

    // ══════════════════════════════════════════════════════════════════
    // 程序入口
    // ══════════════════════════════════════════════════════════════════
    public static void main(String[] args) {
        // ── 解决 Windows CMD GBK 乱码 ──
        try {
            System.setOut(new PrintStream(System.out, true, "UTF-8"));
        } catch (UnsupportedEncodingException ignored) {}

        printHeader();
        initDatabase();
        seedDepartments();

        Scanner sc = new Scanner(System.in, "UTF-8");
        while (true) {
            printMenu();
            String raw = sc.nextLine().trim();
            if (raw.isEmpty()) continue;
            if (!raw.matches("\\d+")) {
                System.out.println("\n  ❌ 请输入数字选项！");
                continue;
            }
            int opt = Integer.parseInt(raw);
            System.out.println();

            switch (opt) {
                case 1:  addRegistration(sc);       break;
                case 2:  batchImport(sc);            break;
                case 3:  searchRegistration(sc);     break;
                case 4:  deptStatistics();           break;
                case 5:  exportData(sc);             break;
                case 6:  showComparisonMetrics();    break;
                case 7:  showOpLogs();               break;
                case 8:  resetAllData(sc);           break;
                case 0:
                    System.out.println("  👋 感谢使用创新创业项目报名系统，再见！");
                    closeDatabase();
                    return;
                default:
                    System.out.println("  ❌ 无效选项，请重新输入！");
            }
            if (opt != 0) {
                System.out.print("\n  按 Enter 返回主菜单...");
                try { sc.nextLine(); } catch (Exception ignored) {}
            }
        }
    }

    // ╔══════════════════════════════════════════════════════════════════╗
    // ║  1. UI 模块                                                      ║
    // ╚══════════════════════════════════════════════════════════════════╝

    static void printHeader() {
        System.out.println("\n  ╔══════════════════════════════════════════════════╗");
        System.out.println("  ║     🏫 校内创新创业项目报名系统 v1.0              ║");
        System.out.println("  ║     后端 / 数据测试 求职作品集                     ║");
        System.out.println("  ╚══════════════════════════════════════════════════╝");
    }

    static void printMenu() {
        System.out.println("\n  📋 主菜单");
        System.out.println("  ┌────────────────────────────────────────────────┐");
        System.out.println("  │  1.  📝 新增报名（逐条录入）                    │");
        System.out.println("  │  2.  📥 多线程批量导入报名名单                  │");
        System.out.println("  │  3.  🔍 查询报名记录                            │");
        System.out.println("  │  4.  📊 院系统计报表                            │");
        System.out.println("  │  5.  💾 导出报名数据                            │");
        System.out.println("  │  6.  🤖 自动化 vs 人工核对量化对比              │");
        System.out.println("  │  7.  📝 查看操作日志                            │");
        System.out.println("  │  8.  🗑  重置所有数据                           │");
        System.out.println("  │  0.  🚪 退出系统                                │");
        System.out.println("  └────────────────────────────────────────────────┘");
        System.out.print("  👉 请选择 (0-8): ");
    }

    // ╔══════════════════════════════════════════════════════════════════╗
    // ║  2. 数据库模块（SQLite 本地持久化）                              ║
    // ╚══════════════════════════════════════════════════════════════════╝

    static void initDatabase() {
        try {
            // 加载 SQLite JDBC 驱动
            Class.forName("org.sqlite.JDBC");
            conn = DriverManager.getConnection(DB_URL);
            conn.setAutoCommit(true);

            try (Statement stmt = conn.createStatement()) {
                // 开启 WAL 模式 —— 提升并发写入性能
                stmt.execute("PRAGMA journal_mode=WAL");
                stmt.execute("PRAGMA busy_timeout=5000");

                // 报名记录表
                stmt.execute(
                    "CREATE TABLE IF NOT EXISTS registrations (" +
                    "  id            INTEGER PRIMARY KEY AUTOINCREMENT," +
                    "  student_id    TEXT    NOT NULL UNIQUE,  " +   // 学号（唯一约束→重复拦截）
                    "  name          TEXT    NOT NULL,         " +   // 姓名
                    "  department    TEXT    NOT NULL,         " +   // 院系
                    "  major         TEXT    NOT NULL,         " +   // 专业
                    "  grade         TEXT    NOT NULL,         " +   // 年级
                    "  phone         TEXT,                    " +   // 手机号
                    "  email         TEXT,                    " +   // 邮箱
                    "  project_name  TEXT    NOT NULL,         " +   // 项目名称
                    "  project_type  TEXT    DEFAULT '创新',   " +   // 项目类型
                    "  import_batch  TEXT,                    " +   // 导入批次号
                    "  created_at    TEXT    DEFAULT (datetime('now','localtime'))," +
                    "  is_valid      INTEGER DEFAULT 1        " +    // 有效性标记
                    ")"
                );

                // 操作日志表
                stmt.execute(
                    "CREATE TABLE IF NOT EXISTS operation_logs (" +
                    "  id            INTEGER PRIMARY KEY AUTOINCREMENT," +
                    "  op_type       TEXT    NOT NULL,         " +   // 操作类型
                    "  detail        TEXT,                    " +   // 详情
                    "  result        TEXT,                    " +   // 结果
                    "  timestamp     TEXT    DEFAULT (datetime('now','localtime'))" +
                    ")"
                );
            }
            log("INFO", "数据库初始化完成 (SQLite WAL 模式)");
        } catch (Exception e) {
            System.err.println("  ❌ 数据库初始化失败: " + e.getMessage());
            System.err.println("  💡 请确认 sqlite-jdbc JAR 已在 classpath 中！");
            System.exit(1);
        }
    }

    /** 初始化院系列表（仅在空表时插入） */
    static void seedDepartments() {
        try {
            PreparedStatement ps = conn.prepareStatement(
                "SELECT COUNT(*) FROM registrations"
            );
            ResultSet rs = ps.executeQuery();
            if (rs.next() && rs.getInt(1) == 0) {
                log("INFO", "系统就绪，等待报名数据录入");
            }
            rs.close(); ps.close();
        } catch (Exception e) {
            log("ERROR", "院系初始化异常: " + e.getMessage());
        }
    }

    static void closeDatabase() {
        try { if (conn != null) conn.close(); } catch (Exception ignored) {}
    }

    // ╔══════════════════════════════════════════════════════════════════╗
    // ║  3. 日志模块                                                     ║
    // ╚══════════════════════════════════════════════════════════════════╝

    static void log(String type, String detail) {
        String ts = SDF.format(new Date());
        String entry = "[" + ts + "] [" + type + "] " + detail;
        opLogs.add(entry);

        // 同步写入数据库
        synchronized (DB_WRITE_LOCK) {
            try {
                PreparedStatement ps = conn.prepareStatement(
                    "INSERT INTO operation_logs (op_type, detail, result) VALUES (?,?,?)"
                );
                ps.setString(1, type);
                ps.setString(2, detail);
                ps.setString(3, type.equals("ERROR") ? "FAIL" : "SUCCESS");
                ps.executeUpdate();
                ps.close();
            } catch (Exception ignored) {}
        }
    }

    // ╔══════════════════════════════════════════════════════════════════╗
    // ║  4. 输入校验模块（强数据校验）                                    ║
    // ╚══════════════════════════════════════════════════════════════════╝

    /**
     * 统一校验一条报名数据。
     * 返回: null 表示校验通过；非 null 为错误信息
     */
    static String validate(String studentId, String name, String dept,
                           String major, String grade, String phone,
                           String email, String projectName) {

        // ── 空值校验 ──
        if (isBlank(studentId))   return "学号不能为空！";
        if (isBlank(name))        return "姓名不能为空！";
        if (isBlank(dept))        return "院系不能为空！";
        if (isBlank(major))       return "专业不能为空！";
        if (isBlank(grade))       return "年级不能为空！";
        if (isBlank(projectName)) return "项目名称不能为空！";

        // ── 格式校验 ──
        if (!studentId.matches("^[A-Za-z0-9]{6,20}$"))
            return "学号格式错误（6-20位字母数字）！";
        if (!name.matches("^[\\u4e00-\\u9fa5a-zA-Z·]{2,20}$"))
            return "姓名格式错误（2-20个中文/英文字符）！";
        if (!grade.matches("^20\\d{2}级$"))
            return "年级格式错误（如：2024级）！";
        if (!isBlank(phone) && !phone.matches("^1[3-9]\\d{9}$"))
            return "手机号格式错误（11位手机号）！";
        if (!isBlank(email) && !email.matches("^[\\w.-]+@[\\w.-]+\\.[a-zA-Z]{2,}$"))
            return "邮箱格式错误！";

        // ── 院系合法性校验 ──
        boolean deptValid = false;
        for (String d : VALID_DEPTS) {
            if (d.equals(dept)) { deptValid = true; break; }
        }
        if (!deptValid) return "院系不在合法列表中: " + dept;

        // ── 重复报名拦截（数据库级别）──
        try {
            PreparedStatement ps = conn.prepareStatement(
                "SELECT COUNT(*) FROM registrations WHERE student_id = ?"
            );
            ps.setString(1, studentId);
            ResultSet rs = ps.executeQuery();
            if (rs.next() && rs.getInt(1) > 0) {
                rs.close(); ps.close();
                return "学号 " + studentId + " 已报名，禁止重复提交！";
            }
            rs.close(); ps.close();
        } catch (Exception e) {
            return "数据库查询异常: " + e.getMessage();
        }

        return null; // 校验通过
    }

    static boolean isBlank(String s) {
        return s == null || s.trim().isEmpty();
    }

    // ╔══════════════════════════════════════════════════════════════════╗
    // ║  5. 新增报名（功能 1）                                           ║
    // ╚══════════════════════════════════════════════════════════════════╝

    static void addRegistration(Scanner sc) {
        System.out.println("  ══════════════════════════════");
        System.out.println("    📝 新增学生报名");
        System.out.println("  ══════════════════════════════");

        String studentId   = prompt(sc, "  学号 (6-20位字母数字): ");
        String name        = prompt(sc, "  姓名: ");
        System.out.println("  可选院系: " + String.join(", ", VALID_DEPTS));
        String dept        = prompt(sc, "  院系: ");
        String major       = prompt(sc, "  专业: ");
        String grade       = prompt(sc, "  年级 (如2024级): ");
        String phone       = prompt(sc, "  手机号 (可选): ");
        String email       = prompt(sc, "  邮箱 (可选): ");
        String projectName = prompt(sc, "  项目名称: ");
        String projectType = prompt(sc, "  项目类型 (创新/创业, 默认创新): ");
        if (isBlank(projectType)) projectType = "创新";

        // 校验
        String err = validate(studentId, name, dept, major, grade, phone, email, projectName);
        if (err != null) {
            System.out.println("\n  ❌ 校验失败: " + err);
            log("WARN", "报名校验失败 | 学号=" + studentId + " | 原因=" + err);
            invalidCaught.incrementAndGet();
            return;
        }

        // 写入数据库
        synchronized (DB_WRITE_LOCK) {
            try {
                PreparedStatement ps = conn.prepareStatement(
                    "INSERT INTO registrations (student_id, name, department, major, " +
                    "grade, phone, email, project_name, project_type) " +
                    "VALUES (?,?,?,?,?,?,?,?,?)"
                );
                ps.setString(1, studentId);
                ps.setString(2, name);
                ps.setString(3, dept);
                ps.setString(4, major);
                ps.setString(5, grade);
                ps.setString(6, phone.isEmpty() ? null : phone);
                ps.setString(7, email.isEmpty() ? null : email);
                ps.setString(8, projectName);
                ps.setString(9, projectType);
                ps.executeUpdate();
                ps.close();
            } catch (SQLException e) {
                System.out.println("\n  ❌ 数据库写入失败: " + e.getMessage());
                log("ERROR", "报名写入失败 | " + e.getMessage());
                return;
            }
        }

        System.out.println("\n  ✅ 报名成功！学号: " + studentId + " | 姓名: " + name);
        log("INFO", "新增报名 | 学号=" + studentId + " | 姓名=" + name + " | 院系=" + dept);
        successImported.incrementAndGet();
    }

    static String prompt(Scanner sc, String hint) {
        System.out.print(hint);
        return sc.nextLine().trim();
    }

    // ╔══════════════════════════════════════════════════════════════════╗
    // ║  6. 多线程批量导入（功能 2）—— 并发处理核心                       ║
    // ╚══════════════════════════════════════════════════════════════════╝

    /**
     * 从 CSV 文件多线程批量导入报名数据。
     * 每条数据经过强校验，重复/非法记录自动拦截并记入日志。
     * 使用固定线程池并发处理，模拟生产环境批量数据清洗场景。
     */
    static void batchImport(Scanner sc) {
        System.out.println("  ══════════════════════════════");
        System.out.println("    📥 多线程批量导入报名名单");
        System.out.println("  ══════════════════════════════");

        System.out.println("\n  CSV 格式要求（每行一条，逗号分隔，不含表头）:");
        System.out.println("    学号,姓名,院系,专业,年级,手机号,邮箱,项目名称,项目类型");
        System.out.println("  示例行:");
        System.out.println("    S2024001,张三,计算机学院,软件工程,2024级,13800138000,zs@qq.com,AI助手,创新");

        System.out.print("\n  请输入 CSV 文件路径: ");
        String filePath = sc.nextLine().trim();

        if (isBlank(filePath)) {
            System.out.println("  ❌ 文件路径不能为空！");
            return;
        }

        File file = new File(filePath);
        if (!file.exists()) {
            System.out.println("  ❌ 文件不存在: " + filePath);
            log("ERROR", "批量导入失败——文件不存在: " + filePath);
            return;
        }

        System.out.print("  请输入线程数 (默认4，建议2-8): ");
        String ts = sc.nextLine().trim();
        int threadCount = (ts.isEmpty() || !ts.matches("\\d+")) ? 4 : Integer.parseInt(ts);
        threadCount = Math.max(1, Math.min(threadCount, 16));

        // ── 重置计数器 ──
        totalProcessed.set(0);
        conflictCaught.set(0);
        invalidCaught.set(0);
        successImported.set(0);

        String batchId = "BATCH_" + System.currentTimeMillis();

        // ── 读取 CSV 所有行 ──
        List<String[]> rows = new ArrayList<>();
        try (BufferedReader br = new BufferedReader(
                new InputStreamReader(new FileInputStream(file), "UTF-8"))) {
            String line;
            int lineNum = 0;
            while ((line = br.readLine()) != null) {
                lineNum++;
                line = line.trim();
                if (line.isEmpty()) continue;

                String[] fields = parseCSVLine(line);
                if (fields.length < 7) {
                    invalidCaught.incrementAndGet();
                    log("WARN", "CSV第" + lineNum + "行格式错误，已跳过");
                    continue;
                }
                rows.add(fields);
            }
        } catch (IOException e) {
            System.out.println("  ❌ 读取文件失败: " + e.getMessage());
            return;
        }

        if (rows.isEmpty()) {
            System.out.println("  ⚠️ 文件中无有效数据行！");
            return;
        }

        System.out.println("\n  ⏳ 共读取 " + rows.size() + " 条数据，启动 " +
                           threadCount + " 个线程并发处理...");

        long t0 = System.currentTimeMillis();

        // ── 线程池并发导入 ──
        ExecutorService executor = Executors.newFixedThreadPool(threadCount);
        CountDownLatch latch = new CountDownLatch(rows.size());

        for (String[] fields : rows) {
            executor.submit(() -> {
                try {
                    processOneRow(fields, batchId);
                } finally {
                    latch.countDown();
                }
            });
        }

        try {
            latch.await(60, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        executor.shutdown();

        long elapsed = System.currentTimeMillis() - t0;

        // ── 输出结果 ──
        System.out.println("\n  ══════════════════════════════");
        System.out.println("  📊 批量导入结果");
        System.out.println("  ══════════════════════════════");
        System.out.println("  总处理数:     " + rows.size());
        System.out.println("  ✅ 成功导入:  " + successImported.get());
        System.out.println("  🚫 重复拦截:  " + conflictCaught.get());
        System.out.println("  ❌ 校验失败:  " + invalidCaught.get());
        System.out.println("  ⏱ 总耗时:     " + elapsed + " ms (" + String.format("%.2f", elapsed/1000.0) + "s)");
        System.out.println("  失败率:       " + String.format("%.1f%%",
            (conflictCaught.get() + invalidCaught.get()) * 100.0 / Math.max(rows.size(), 1)));

        double avgPerRow = (double) elapsed / rows.size();
        System.out.println("  平均每条耗时: " + String.format("%.2f", avgPerRow) + " ms");

        log("INFO", "批量导入完成 | 总数=" + rows.size() +
            " | 成功=" + successImported.get() + " | 重复=" + conflictCaught.get() +
            " | 校验失败=" + invalidCaught.get() + " | 线程=" + threadCount +
            " | 耗时=" + elapsed + "ms");
    }

    /** 处理一行 CSV 数据（由线程池调用） */
    static void processOneRow(String[] fields, String batchId) {
        totalProcessed.incrementAndGet();

        // 安全读取字段（不足的用空串补齐）
        String studentId   = getField(fields, 0);
        String name        = getField(fields, 1);
        String dept        = getField(fields, 2);
        String major       = getField(fields, 3);
        String grade       = getField(fields, 4);
        String phone       = getField(fields, 5);
        String email       = getField(fields, 6);
        String projectName = getField(fields, 7);
        String projectType = getField(fields, 8);
        if (isBlank(projectType)) projectType = "创新";

        // ── 并发场景下的数据校验 ──
        String err = validate(studentId, name, dept, major, grade, phone, email, projectName);
        if (err != null) {
            if (err.contains("已报名")) {
                conflictCaught.incrementAndGet();
                log("WARN", "重复报名拦截 | 学号=" + studentId + " | " + err);
            } else {
                invalidCaught.incrementAndGet();
                log("WARN", "校验失败 | 学号=" + studentId + " | " + err);
            }
            return;
        }

        // ── 线程安全的数据库写入 ──
        synchronized (DB_WRITE_LOCK) {
            try {
                PreparedStatement ps = conn.prepareStatement(
                    "INSERT INTO registrations (student_id, name, department, major, " +
                    "grade, phone, email, project_name, project_type, import_batch) " +
                    "VALUES (?,?,?,?,?,?,?,?,?,?)"
                );
                ps.setString(1, studentId);
                ps.setString(2, name);
                ps.setString(3, dept);
                ps.setString(4, major);
                ps.setString(5, grade);
                ps.setString(6, phone.isEmpty() ? null : phone);
                ps.setString(7, email.isEmpty() ? null : email);
                ps.setString(8, projectName);
                ps.setString(9, projectType);
                ps.setString(10, batchId);
                ps.executeUpdate();
                ps.close();
                successImported.incrementAndGet();
            } catch (SQLException e) {
                // 捕获唯一约束冲突（并发场景下可能多个线程插入同一学号）
                if (e.getMessage() != null && e.getMessage().contains("UNIQUE")) {
                    conflictCaught.incrementAndGet();
                    log("WARN", "并发冲突→唯一约束拦截 | 学号=" + studentId);
                } else {
                    invalidCaught.incrementAndGet();
                    log("ERROR", "数据库写入异常 | 学号=" + studentId + " | " + e.getMessage());
                }
            }
        }
    }

    static String getField(String[] fields, int idx) {
        return (idx < fields.length) ? (fields[idx] != null ? fields[idx].trim() : "") : "";
    }

    /** 简易 CSV 行解析（处理逗号分隔，支持引号包裹） */
    static String[] parseCSVLine(String line) {
        List<String> list = new ArrayList<>();
        boolean inQuotes = false;
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < line.length(); i++) {
            char c = line.charAt(i);
            if (c == '\"') {
                inQuotes = !inQuotes;
            } else if (c == ',' && !inQuotes) {
                list.add(sb.toString().trim());
                sb.setLength(0);
            } else {
                sb.append(c);
            }
        }
        list.add(sb.toString().trim());
        return list.toArray(new String[0]);
    }

    // ╔══════════════════════════════════════════════════════════════════╗
    // ║  7. 查询报名记录（功能 3）                                        ║
    // ╚══════════════════════════════════════════════════════════════════╝

    static void searchRegistration(Scanner sc) {
        System.out.println("  ══════════════════════════════");
        System.out.println("    🔍 查询报名记录");
        System.out.println("  ══════════════════════════════");
        System.out.println("  1. 按学号查询");
        System.out.println("  2. 按院系查询");
        System.out.println("  3. 查看全部（最近 20 条）");
        System.out.print("  👉 请选择: ");
        String raw = sc.nextLine().trim();

        String sql;
        List<String> params = new ArrayList<>();

        switch (raw) {
            case "1":
                System.out.print("  请输入学号: ");
                String sid = sc.nextLine().trim();
                if (isBlank(sid)) { System.out.println("  ❌ 学号不能为空！"); return; }
                sql = "SELECT * FROM registrations WHERE student_id=? ORDER BY created_at DESC";
                params.add(sid);
                break;
            case "2":
                System.out.print("  请输入院系: ");
                String dept2 = sc.nextLine().trim();
                if (isBlank(dept2)) { System.out.println("  ❌ 院系不能为空！"); return; }
                sql = "SELECT * FROM registrations WHERE department=? ORDER BY created_at DESC LIMIT 30";
                params.add(dept2);
                break;
            case "3":
                sql = "SELECT * FROM registrations ORDER BY created_at DESC LIMIT 20";
                break;
            default:
                System.out.println("  ❌ 无效选项！"); return;
        }

        try {
            PreparedStatement ps = conn.prepareStatement(sql);
            for (int i = 0; i < params.size(); i++) {
                ps.setString(i + 1, params.get(i));
            }
            ResultSet rs = ps.executeQuery();

            int count = 0;
            System.out.println("\n  ─────────────────────────────────────────────────────");
            System.out.printf("  %-4s %-12s %-10s %-14s %-10s %-16s\n",
                "序号", "学号", "姓名", "院系", "年级", "项目名称");
            System.out.println("  ─────────────────────────────────────────────────────");

            while (rs.next()) {
                count++;
                System.out.printf("  %-4d %-12s %-10s %-14s %-10s %-16s\n",
                    count,
                    rs.getString("student_id"),
                    rs.getString("name"),
                    rs.getString("department"),
                    rs.getString("grade"),
                    truncate(rs.getString("project_name"), 15)
                );
            }
            rs.close(); ps.close();

            System.out.println("  ─────────────────────────────────────────────────────");
            System.out.println("  共 " + count + " 条记录");
            log("INFO", "查询报名记录 | 条件=" + raw + " | 结果=" + count + "条");

        } catch (SQLException e) {
            System.out.println("  ❌ 查询失败: " + e.getMessage());
        }
    }

    // ╔══════════════════════════════════════════════════════════════════╗
    // ║  8. 院系统计报表（功能 4）                                        ║
    // ╚══════════════════════════════════════════════════════════════════╝

    static void deptStatistics() {
        System.out.println("  ══════════════════════════════");
        System.out.println("    📊 院系报名统计报表");
        System.out.println("  ══════════════════════════════");

        try {
            // 各院系统计
            PreparedStatement ps = conn.prepareStatement(
                "SELECT department, COUNT(*) AS cnt, " +
                "SUM(CASE WHEN is_valid=1 THEN 1 ELSE 0 END) AS valid_cnt " +
                "FROM registrations GROUP BY department ORDER BY cnt DESC"
            );
            ResultSet rs = ps.executeQuery();

            int grandTotal = 0;
            int grandValid = 0;

            System.out.println("\n  ┌──────────────────────────────────────────────────┐");
            System.out.printf ("  │ %-16s %6s %6s %8s │\n", "院系", "报名数", "有效", "占比");
            System.out.println("  ├──────────────────────────────────────────────────┤");

            // 先收集所有行
            List<String[]> rows = new ArrayList<>();
            while (rs.next()) {
                String dept = rs.getString("department");
                int cnt     = rs.getInt("cnt");
                int valid   = rs.getInt("valid_cnt");
                grandTotal += cnt;
                grandValid += valid;
                rows.add(new String[]{dept, String.valueOf(cnt), String.valueOf(valid)});
            }
            rs.close(); ps.close();

            for (String[] r : rows) {
                int cnt = Integer.parseInt(r[1]);
                System.out.printf("  │ %-16s %6s %6s %7.1f%% │\n",
                    r[0], r[1], r[2],
                    (grandTotal > 0 ? cnt * 100.0 / grandTotal : 0));
            }

            System.out.println("  ├──────────────────────────────────────────────────┤");
            System.out.printf("  │ %-16s %6d %6d %7s │\n", "合计", grandTotal, grandValid, "—");
            System.out.println("  └──────────────────────────────────────────────────┘");

            // 无效数据统计
            ps = conn.prepareStatement(
                "SELECT COUNT(*) FROM registrations WHERE is_valid=0"
            );
            rs = ps.executeQuery();
            int invalidCount = rs.next() ? rs.getInt(1) : 0;
            rs.close(); ps.close();

            System.out.println("\n  📈 关键指标:");
            System.out.println("  报名总数:    " + grandTotal);
            System.out.println("  有效报名:    " + grandValid);
            System.out.println("  无效记录:    " + invalidCount);
            System.out.printf ("  数据有效率:  %.1f%%\n",
                (grandTotal > 0 ? grandValid * 100.0 / grandTotal : 0));

            log("INFO", "院系统计 | 总报名=" + grandTotal + " | 有效=" + grandValid +
                " | 无效=" + invalidCount);

        } catch (SQLException e) {
            System.out.println("  ❌ 统计失败: " + e.getMessage());
        }
    }

    // ╔══════════════════════════════════════════════════════════════════╗
    // ║  9. 导出报名数据（功能 5）                                        ║
    // ╚══════════════════════════════════════════════════════════════════╝

    static void exportData(Scanner sc) {
        System.out.println("  ══════════════════════════════");
        System.out.println("    💾 导出报名数据");
        System.out.println("  ══════════════════════════════");
        System.out.println("  1. 导出 CSV");
        System.out.println("  2. 导出 JSON");
        System.out.print("  👉 请选择: ");
        String raw = sc.nextLine().trim();
        String format = raw.equals("2") ? "json" : "csv";

        String ts = new SimpleDateFormat("yyyyMMdd_HHmmss").format(new Date());
        String fileName = "export_" + ts + "." + format;

        try {
            PreparedStatement ps = conn.prepareStatement(
                "SELECT * FROM registrations ORDER BY created_at DESC"
            );
            ResultSet rs = ps.executeQuery();

            if (format.equals("csv")) {
                try (PrintWriter pw = new PrintWriter(
                        new OutputStreamWriter(new FileOutputStream(fileName), "UTF-8"))) {
                    // BOM for Excel
                    pw.print('﻿');
                    pw.println("学号,姓名,院系,专业,年级,手机号,邮箱,项目名称,项目类型,报名时间,有效标记");

                    int exported = 0;
                    while (rs.next()) {
                        exported++;
                        pw.printf("%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%d\n",
                            csvEscape(rs.getString("student_id")),
                            csvEscape(rs.getString("name")),
                            csvEscape(rs.getString("department")),
                            csvEscape(rs.getString("major")),
                            csvEscape(rs.getString("grade")),
                            csvEscape(rs.getString("phone")),
                            csvEscape(rs.getString("email")),
                            csvEscape(rs.getString("project_name")),
                            csvEscape(rs.getString("project_type")),
                            csvEscape(rs.getString("created_at")),
                            rs.getInt("is_valid")
                        );
                    }
                    System.out.println("\n  ✅ 已导出 " + exported + " 条记录到 " + fileName);
                    log("INFO", "导出CSV | 文件=" + fileName + " | 记录=" + exported);
                }
            } else {
                // JSON 导出
                try (PrintWriter pw = new PrintWriter(
                        new OutputStreamWriter(new FileOutputStream(fileName), "UTF-8"))) {
                    pw.println("[");
                    int exported = 0;
                    boolean first = true;
                    while (rs.next()) {
                        if (!first) pw.println(",");
                        first = false;
                        exported++;
                        pw.printf("  {\"学号\":\"%s\",\"姓名\":\"%s\",\"院系\":\"%s\",\"专业\":\"%s\"," +
                                  "\"年级\":\"%s\",\"手机号\":\"%s\",\"邮箱\":\"%s\"," +
                                  "\"项目名称\":\"%s\",\"项目类型\":\"%s\",\"报名时间\":\"%s\"}",
                            jsonEscape(rs.getString("student_id")),
                            jsonEscape(rs.getString("name")),
                            jsonEscape(rs.getString("department")),
                            jsonEscape(rs.getString("major")),
                            jsonEscape(rs.getString("grade")),
                            jsonEscape(rs.getString("phone")),
                            jsonEscape(rs.getString("email")),
                            jsonEscape(rs.getString("project_name")),
                            jsonEscape(rs.getString("project_type")),
                            jsonEscape(rs.getString("created_at"))
                        );
                    }
                    pw.println("\n]");
                    System.out.println("\n  ✅ 已导出 " + exported + " 条记录到 " + fileName);
                    log("INFO", "导出JSON | 文件=" + fileName + " | 记录=" + exported);
                }
            }
            rs.close(); ps.close();

        } catch (Exception e) {
            System.out.println("  ❌ 导出失败: " + e.getMessage());
            log("ERROR", "导出失败: " + e.getMessage());
        }
    }

    // ╔══════════════════════════════════════════════════════════════════╗
    // ║ 10. 自动化 vs 人工核对量化对比（功能 6）                          ║
    // ╚══════════════════════════════════════════════════════════════════╝

    static void showComparisonMetrics() {
        System.out.println("  ══════════════════════════════");
        System.out.println("    🤖 自动化校验 vs 人工核对 — 量化对比分析");
        System.out.println("  ══════════════════════════════");

        int totalRegs = 0;
        int invalidRegs = 0;
        try {
            PreparedStatement ps = conn.prepareStatement(
                "SELECT COUNT(*) AS total, " +
                "SUM(CASE WHEN is_valid=0 THEN 1 ELSE 0 END) AS invalid " +
                "FROM registrations"
            );
            ResultSet rs = ps.executeQuery();
            if (rs.next()) {
                totalRegs = rs.getInt("total");
                invalidRegs = rs.getInt("invalid");
            }
            rs.close(); ps.close();
        } catch (SQLException e) {
            System.out.println("  ❌ 查询失败: " + e.getMessage());
            return;
        }

        if (totalRegs == 0) {
            System.out.println("\n  ⚠️ 暂无报名数据，请先导入数据！");
            return;
        }

        // ── 量化计算 ──
        // 人工核对
        double manualTotalMinutes = totalRegs * MANUAL_MINUTES_PER_ENTRY;
        double manualTotalHours   = manualTotalMinutes / 60;
        double manualErrors       = totalRegs * MANUAL_ERROR_RATE;

        // 自动化校验
        double autoTotalSeconds   = totalRegs * AUTOMATED_SEC_PER_ENTRY;
        double autoTotalMinutes   = autoTotalSeconds / 60;
        double autoErrors         = totalRegs * AUTOMATED_ERROR_RATE;

        // 核心指标
        double timeSavedPct       = (1 - autoTotalMinutes / manualTotalMinutes) * 100;
        double errorReductionPct  = (1 - autoErrors / Math.max(manualErrors, 0.001)) * 100;
        double workloadReduction  = (1 - autoTotalMinutes / manualTotalMinutes) * 100;

        // 数据交付周期
        double manualDays  = manualTotalMinutes / (8 * 60);  // 8小时工作制
        double autoDays    = autoTotalMinutes / (8 * 60);

        // ── 输出 ──
        System.out.println("\n  ┌──────────────────────────────────────────────────────────┐");
        System.out.println("  │              量化对比分析                                 │");
        System.out.println("  ├──────────────────────────────────────────────────────────┤");
        System.out.printf("  │  当前报名数据总量:         %6d 条                       │\n", totalRegs);
        System.out.printf("  │  其中异常数据（无效）:     %6d 条                       │\n", invalidRegs);
        System.out.println("  ├──────────────────────────────────────────────────────────┤");
        System.out.println("  │  【耗时对比】                                            │");
        System.out.printf("  │  人工逐条核对耗时:         %8.1f 分钟 (%5.1f 小时)    │\n",
            manualTotalMinutes, manualTotalHours);
        System.out.printf("  │  自动化校验耗时:           %8.2f 分钟 (%5.3f 小时)    │\n",
            autoTotalMinutes, autoTotalMinutes);
        System.out.printf("  │  ⏱ 时间节省:               %7.1f%%                       │\n",
            timeSavedPct);
        System.out.println("  ├──────────────────────────────────────────────────────────┤");
        System.out.println("  │  【准确率对比】                                          │");
        System.out.printf("  │  人工核对准确率:           %6.1f%% (出错率 %.1f%%)      │\n",
            (1 - MANUAL_ERROR_RATE) * 100, MANUAL_ERROR_RATE * 100);
        System.out.printf("  │  自动化校验准确率:         %6.1f%% (出错率 %.1f%%)      │\n",
            (1 - AUTOMATED_ERROR_RATE) * 100, AUTOMATED_ERROR_RATE * 100);
        System.out.printf("  │  预估人工出错数:           %6.0f 条                     │\n",
            manualErrors);
        System.out.printf("  │  预估自动出错数:           %6.0f 条                     │\n",
            autoErrors);
        System.out.printf("  │  📈 错误减少率:             %7.1f%%                       │\n",
            errorReductionPct);
        System.out.println("  ├──────────────────────────────────────────────────────────┤");
        System.out.println("  │  【数据交付周期】                                        │");
        System.out.printf("  │  人工核对交付周期:         %6.2f 个工作日              │\n",
            manualDays);
        System.out.printf("  │  自动化交付周期:           %6.3f 个工作日              │\n",
            autoDays);
        System.out.printf("  │  📅 交付周期缩短:          %7.1f%%                       │\n",
            workloadReduction);
        System.out.println("  ├──────────────────────────────────────────────────────────┤");
        System.out.println("  │  【🎯 核心结论】                                         │");
        System.out.printf("  │  ▸ 自动化减少人工核对工作量:  %5.1f%%                    │\n",
            workloadReduction);
        System.out.printf("  │  ▸ 数据校验错误减少率:        %5.1f%%                    │\n",
            errorReductionPct);
        System.out.printf("  │  ▸ 数据交付周期缩短:          %5.1f%%                    │\n",
            workloadReduction);
        System.out.println("  │  ▸ 自动化校验可节省人力成本: 显著                       │");
        System.out.println("  └──────────────────────────────────────────────────────────┘");

        System.out.println("\n  💡 总结：自动化数据校验在报名管理场景中，可大幅减少人工逐条核对");
        System.out.println("      的工作量，显著缩短数据交付周期，并有效降低人为出错概率。");

        log("INFO", "查看量化对比 | 数据量=" + totalRegs +
            " | 工作量减少=" + String.format("%.1f%%", workloadReduction) +
            " | 错误减少=" + String.format("%.1f%%", errorReductionPct));
    }

    // ╔══════════════════════════════════════════════════════════════════╗
    // ║ 11. 操作日志（功能 7）                                           ║
    // ╚══════════════════════════════════════════════════════════════════╝

    static void showOpLogs() {
        System.out.println("  ══════════════════════════════");
        System.out.println("    📝 操作日志（最近 30 条）");
        System.out.println("  ══════════════════════════════");

        // 显示内存中的日志
        synchronized (opLogs) {
            int start = Math.max(0, opLogs.size() - 30);
            for (int i = start; i < opLogs.size(); i++) {
                System.out.println("  " + opLogs.get(i));
            }
            System.out.println("\n  日志总数: " + opLogs.size());
        }

        // 日志统计
        int infoCount = 0, warnCount = 0, errCount = 0;
        synchronized (opLogs) {
            for (String entry : opLogs) {
                if (entry.contains("[INFO]"))  infoCount++;
                if (entry.contains("[WARN]"))  warnCount++;
                if (entry.contains("[ERROR]")) errCount++;
            }
        }
        System.out.println("  统计: INFO=" + infoCount + " | WARN=" + warnCount + " | ERROR=" + errCount);
    }

    // ╔══════════════════════════════════════════════════════════════════╗
    // ║ 12. 重置数据（功能 8）                                           ║
    // ╚══════════════════════════════════════════════════════════════════╝

    static void resetAllData(Scanner sc) {
        System.out.println("  ⚠️⚠️⚠️  警告：此操作将删除所有报名数据和操作日志！  ⚠️⚠️⚠️");
        System.out.print("  请输入 'DELETE' 确认: ");
        String confirm = sc.nextLine().trim();
        if (!"DELETE".equals(confirm)) {
            System.out.println("  ❌ 已取消");
            return;
        }

        synchronized (DB_WRITE_LOCK) {
            try (Statement stmt = conn.createStatement()) {
                stmt.execute("DELETE FROM registrations");
                stmt.execute("DELETE FROM operation_logs");
            } catch (SQLException e) {
                System.out.println("  ❌ 重置失败: " + e.getMessage());
                return;
            }
        }
        opLogs.clear();
        System.out.println("  ✅ 所有数据已清空！");
        log("WARN", "数据重置——所有记录已删除");
    }

    // ╔══════════════════════════════════════════════════════════════════╗
    // ║ 13. 工具方法                                                     ║
    // ╚══════════════════════════════════════════════════════════════════╝

    static String truncate(String s, int maxLen) {
        if (s == null) return "";
        return s.length() <= maxLen ? s : s.substring(0, maxLen - 1) + "…";
    }

    static String csvEscape(String s) {
        if (s == null) return "";
        if (s.contains(",") || s.contains("\"") || s.contains("\n")) {
            return "\"" + s.replace("\"", "\"\"") + "\"";
        }
        return s;
    }

    static String jsonEscape(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }
}
