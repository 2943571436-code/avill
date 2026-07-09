"""数字记忆挑战 — 极简 Python 小游戏 (97行) | 运行: python memory_game.py | 零依赖"""
import sqlite3, random, time, sys, io
if sys.stdout.encoding != 'utf-8':  # Windows GBK → UTF-8
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DB = 'memory_game.db'

# ── 数据库：单行记录累计统计 ──
def init_db():
    with sqlite3.connect(DB) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY CHECK(id=1),
            high_score INTEGER DEFAULT 0, total_games INTEGER DEFAULT 0,
            total_score INTEGER DEFAULT 0)''')
        c.execute('INSERT OR IGNORE INTO stats (id) VALUES (1)')
        c.commit()

def load():
    with sqlite3.connect(DB) as c:
        return c.execute('SELECT high_score,total_games,total_score FROM stats WHERE id=1').fetchone()

def save(score):
    with sqlite3.connect(DB) as c:
        c.execute('UPDATE stats SET high_score=MAX(high_score,?),total_games=total_games+1,total_score=total_score+? WHERE id=1',(score,score))
        c.commit()

# ── 游戏核心 ──
def play():
    print('\n  🧠 记住闪现的数字，逐位加长，输错结束！\n')
    seq, pts = '', 0
    while True:
        seq += str(random.randint(0, 9))
        t = max(0.8, len(seq) * 0.3)
        print(f'  🔢 记住:  \033[1;33m{seq}\033[0m', end='', flush=True)
        time.sleep(t)
        print(f'\r  🔢 记住:  {"·"*len(seq)}  ', flush=True)
        try:
            u = input('\n  ✏️  输入: ').strip()
        except (EOFError, KeyboardInterrupt):
            print('\n  👋 中断'); return pts
        if not u:
            print('  ❌ 空输入，结束！'); break
        if not u.isdigit():
            print(f'  ❌ 非法输入"{u}"，仅接受数字！'); break
        if u == seq:
            pts += 1; print(f'  ✅ 正确！得分:{pts}  长度:{len(seq)}')
        else:
            print(f'  ❌ 错误！正确:{seq}  你输入:{u}'); break
    return pts

# ── 主菜单 ──
def main():
    init_db()
    while True:
        hs, tg, ts = load()
        avg = round(ts/tg, 1) if tg > 0 else 0
        print(f'\n  ╔══════════════════════════╗')
        print(f'  ║   🧠 数字记忆挑战 v1.0  ║')
        print(f'  ╠══════════════════════════╣')
        print(f'  ║  🏆 最高分: {hs:<13} ║')
        print(f'  ║  🎮 游玩次数: {tg:<11} ║')
        print(f'  ║  📊 平均分: {avg:<13} ║')
        print(f'  ╠══════════════════════════╣')
        print(f'  ║  1. 开始  2. 退出       ║')
        print(f'  ╚══════════════════════════╝')
        try:
            opt = input('  👉 选择: ').strip()
        except (EOFError, KeyboardInterrupt):
            print('\n  👋 再见！'); break
        if opt == '1':
            s = play()
            if s > 0:
                save(s)
                print(f'\n  📊 得分:{s} | 记忆:{s}位数 | 最高:{max(hs,s)} | 平均:{round((ts+s)/(tg+1),1)}')
        elif opt == '2':
            print('  👋 再见！'); break
        else:
            print('  ❌ 无效！')
        input('\n  按 Enter 继续...')

if __name__ == '__main__':
    try: main()
    except Exception as e: print(f'\n  ❌ 异常: {e}')
