# -*- coding: utf-8 -*-
"""
每日热点 —— 数据库初始化脚本（Docker / 全新部署用）

容器启动时 MySQL 通常比应用慢几秒就绪，直接跑 app.py 会连不上库。
本脚本负责：
  1. 等待 MySQL 端口可连接（默认最多等 90 秒）
  2. 创建数据库（不存在时）
  3. 全新库：执行 schema.sql 建表（已有表时跳过，绝不 DROP 现有数据）
  4. 依次执行 migrate_v2~v6 增量迁移（全部幂等，可重复执行）

可单独运行：   python init_db.py
容器内由 docker-entrypoint.sh 自动调用；
设置环境变量 SKIP_DB_INIT=1 可跳过（例如数据库由外部托管时）。

实现说明：不使用 MySQL 多语句（MULTI_STATEMENTS），而是把 .sql 文件
按分号拆成单条语句逐条执行——既兼容 PREPARE/EXECUTE，也不会出现
"Commands out of sync"。
"""
import glob
import os
import re
import sys
import time

import pymysql
from pymysql.cursors import DictCursor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_HOST = os.environ.get('DB_HOST', '127.0.0.1')
DB_PORT = int(os.environ.get('DB_PORT', 3306))
DB_USER = os.environ.get('DB_USER', 'root')
DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
DB_NAME = os.environ.get('DB_NAME', 'daily_hot')
DB_CHARSET = os.environ.get('DB_CHARSET', 'utf8mb4')
WAIT_TIMEOUT = int(os.environ.get('DB_WAIT_TIMEOUT', 90))

# 迁移文件按版本号排序（migrate_v2.sql -> migrate_v6.sql）
MIGRATIONS = sorted(
    os.path.basename(p)
    for p in glob.glob(os.path.join(BASE_DIR, 'migrate_v*.sql'))
)


def log(msg):
    print('[init_db] %s' % msg, flush=True)


def _connect(database=None):
    kwargs = dict(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        charset=DB_CHARSET, cursorclass=DictCursor,
        autocommit=True, connect_timeout=5,
    )
    if database:
        kwargs['database'] = database
    return pymysql.connect(**kwargs)


def wait_for_mysql():
    """轮询等待 MySQL 就绪，返回 True/False。"""
    deadline = time.time() + WAIT_TIMEOUT
    last_err = None
    while time.time() < deadline:
        try:
            conn = _connect()
            conn.close()
            log('MySQL %s:%s 已就绪' % (DB_HOST, DB_PORT))
            return True
        except Exception as exc:
            last_err = exc
            log('等待 MySQL %s:%s ... (%s)' % (DB_HOST, DB_PORT, type(exc).__name__))
            time.sleep(2)
    log('等待 MySQL 超时（%d 秒）：%s' % (WAIT_TIMEOUT, last_err))
    return False


def split_statements(sql):
    """
    把整段 SQL 拆成单条语句列表。

    会跳过注释，并正确处理字符串字面量里的分号
    （含 SQL 标准的两写单引号转义 ''，以及反斜杠转义）。
    """
    statements = []
    buf = []
    i = 0
    n = len(sql)
    quote = None          # 当前所处的引号字符：' " ` 之一

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ''

        if quote:
            buf.append(ch)
            if ch == '\\' and quote in ("'", '"'):
                # 反斜杠转义：连同下一个字符一起吞掉
                if i + 1 < n:
                    buf.append(sql[i + 1])
                    i += 2
                    continue
            elif ch == quote:
                if nxt == quote:          # SQL 里的 '' 表示字面单引号
                    buf.append(nxt)
                    i += 2
                    continue
                quote = None
            i += 1
            continue

        if ch == '-' and nxt == '-':      # 行注释
            while i < n and sql[i] != '\n':
                i += 1
            continue
        if ch == '#':                     # MySQL 行注释
            while i < n and sql[i] != '\n':
                i += 1
            continue
        if ch == '/' and nxt == '*':      # 块注释
            i += 2
            while i < n and not (sql[i] == '*' and i + 1 < n and sql[i + 1] == '/'):
                i += 1
            i += 2
            continue
        if ch in ("'", '"', '`'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == ';':
            stmt = ''.join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = ''.join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


# 需要忽略的语句：schema.sql / 迁移文件里硬编码了 `USE daily_hot;` 和
# `CREATE DATABASE daily_hot;`，若照搬会无视 DB_NAME 切到固定库，
# 全新库场景下甚至可能 DROP 掉真实数据。库名由本脚本统一控制。
_SKIP_PATTERNS = (
    re.compile(r'^\s*USE\s+\S+\s*$', re.I),
    re.compile(r'^\s*CREATE\s+DATABASE\b.*$', re.I | re.S),
    re.compile(r'^\s*DROP\s+DATABASE\b.*$', re.I | re.S),
)


def run_sql_file(conn, filename):
    """执行一个 .sql 文件（逐条语句执行）。返回 True/False。"""
    path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(path):
        log('跳过（文件不存在）：%s' % filename)
        return True

    with open(path, 'r', encoding='utf-8') as fh:
        raw = split_statements(fh.read())

    statements = []
    for stmt in raw:
        if any(p.match(stmt) for p in _SKIP_PATTERNS):
            log('  忽略硬编码语句：%s' % ' '.join(stmt.split())[:60])
            continue
        statements.append(stmt)

    ok = True
    for idx, stmt in enumerate(statements, 1):
        try:
            with conn.cursor() as cur:
                cur.execute(stmt)
        except Exception as exc:
            ok = False
            log('  %s 第 %d 条语句失败：%s' % (filename, idx, exc))
            log('  语句片段：%s' % stmt[:160].replace('\n', ' '))
    if ok:
        log('已执行 %s（%d 条语句）' % (filename, len(statements)))
    else:
        log('执行 %s 完成，但有语句失败' % filename)
    return ok


def table_exists(conn, table):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS c FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name=%s", (DB_NAME, table))
        return cur.fetchone()['c'] > 0


def main():
    if os.environ.get('SKIP_DB_INIT') == '1':
        log('SKIP_DB_INIT=1，跳过数据库初始化')
        return 0

    if not re.match(r'^[A-Za-z0-9_]+$', DB_NAME or ''):
        log('DB_NAME 含非法字符：%r（仅允许字母/数字/下划线）' % DB_NAME)
        return 1

    if not wait_for_mysql():
        return 1

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE DATABASE IF NOT EXISTS `%s` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                % DB_NAME)
            # 文件里的 USE 语句已被忽略，这里显式切换到目标库
            cur.execute("USE `%s`" % DB_NAME)
        log('数据库 `%s` 已就绪' % DB_NAME)

        if table_exists(conn, 'platforms'):
            log('检测到已有数据表，跳过 schema.sql（保留现有数据）')
        else:
            log('全新数据库，执行 schema.sql 建表...')
            if not run_sql_file(conn, 'schema.sql'):
                return 1

        failed = []
        for name in MIGRATIONS:
            if not run_sql_file(conn, name):
                failed.append(name)
        if failed:
            log('以下迁移存在失败语句（不影响启动，可稍后重试）：%s'
                % ', '.join(failed))
        else:
            log('增量迁移全部完成（共 %d 个）' % len(MIGRATIONS))
    finally:
        conn.close()

    log('数据库初始化完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())
