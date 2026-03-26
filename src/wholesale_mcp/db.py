"""SQLite 매출원장 관리 모듈.

DATA_DIR/wholesale.db에 매출원장과 약품 마스터를 누적 저장.
기존 JSON 파일과 공존하며, SQL 검색으로 토큰 절약.
"""

import sqlite3
import json
import os
from pathlib import Path
from datetime import datetime, timedelta


class WholesaleDB:
    """SQLite 기반 매출원장 + 약품 마스터 관리"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sales_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id TEXT NOT NULL,
                    transaction_date TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    pack_unit TEXT DEFAULT '',
                    quantity REAL DEFAULT 0,
                    unit_price REAL DEFAULT 0,
                    sales_amount REAL DEFAULT 0,
                    balance REAL DEFAULT 0,
                    manufacturer TEXT DEFAULT '',
                    edi_code TEXT DEFAULT '',
                    synced_at TEXT NOT NULL,
                    UNIQUE(site_id, transaction_date, product_name, sales_amount)
                );

                CREATE INDEX IF NOT EXISTS idx_site_date ON sales_ledger(site_id, transaction_date);
                CREATE INDEX IF NOT EXISTS idx_product ON sales_ledger(product_name);
                CREATE INDEX IF NOT EXISTS idx_edi ON sales_ledger(edi_code);

                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id TEXT NOT NULL,
                    product_code TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    manufacturer TEXT DEFAULT '',
                    pack_unit TEXT DEFAULT '',
                    edi_code TEXT DEFAULT '',
                    last_price REAL DEFAULT 0,
                    last_stock INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    UNIQUE(site_id, product_code)
                );

                CREATE INDEX IF NOT EXISTS idx_prod_name ON products(product_name);
                CREATE INDEX IF NOT EXISTS idx_prod_edi ON products(edi_code);
            """)

    def _period_to_date(self, period: str) -> str:
        """기간 문자열 → 시작 날짜"""
        now = datetime.now()
        mapping = {
            '1w': 7, '2w': 14, '1m': 30, '3m': 90,
            '6m': 180, '1y': 365, '2y': 730, '3y': 1095
        }
        days = mapping.get(period, 90)
        start = now - timedelta(days=days)
        return start.strftime('%Y-%m-%d')

    # ── 매출원장 UPSERT ──

    def upsert_ledger(self, entries: list[dict], site_id: str) -> dict:
        """매출원장 데이터를 DB에 저장. 중복은 스킵."""
        now = datetime.now().isoformat()
        new_count = 0
        skip_count = 0

        with self._conn() as conn:
            for e in entries:
                try:
                    conn.execute("""
                        INSERT INTO sales_ledger
                            (site_id, transaction_date, product_name, pack_unit,
                             quantity, unit_price, sales_amount, balance,
                             manufacturer, edi_code, synced_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        site_id,
                        e.get('date', e.get('transaction_date', '')),
                        e.get('product_name', ''),
                        e.get('pack_unit', ''),
                        e.get('quantity', 0) or 0,
                        e.get('unit_price', 0) or 0,
                        e.get('sales_amount', 0) or 0,
                        e.get('balance', 0) or 0,
                        e.get('manufacturer', ''),
                        e.get('edi_code', ''),
                        now,
                    ))
                    new_count += 1
                except sqlite3.IntegrityError:
                    skip_count += 1

            total = conn.execute(
                "SELECT COUNT(*) FROM sales_ledger WHERE site_id = ?", (site_id,)
            ).fetchone()[0]

        return {"new": new_count, "skipped": skip_count, "total_in_db": total}

    # ── 약품 마스터 UPSERT ──

    def upsert_products(self, products: list[dict], site_id: str):
        """검색 결과를 약품 마스터에 저장."""
        now = datetime.now().isoformat()
        with self._conn() as conn:
            for p in products:
                code = p.get('product_code', '')
                if not code:
                    continue
                conn.execute("""
                    INSERT INTO products
                        (site_id, product_code, product_name, manufacturer,
                         pack_unit, edi_code, last_price, last_stock, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(site_id, product_code) DO UPDATE SET
                        product_name=excluded.product_name,
                        manufacturer=excluded.manufacturer,
                        pack_unit=excluded.pack_unit,
                        edi_code=excluded.edi_code,
                        last_price=excluded.last_price,
                        last_stock=excluded.last_stock,
                        updated_at=excluded.updated_at
                """, (
                    site_id,
                    code,
                    p.get('product_name', ''),
                    p.get('manufacturer', ''),
                    p.get('pack_unit', ''),
                    p.get('edi_code', ''),
                    p.get('unit_price', 0) or 0,
                    p.get('stock_quantity', 0) or 0,
                    now,
                ))

    # ── 검색 ──

    def search(self, keyword: str, site_id: str = "", period: str = "3m",
               limit: int = 100) -> list[dict]:
        """약품명으로 매출원장 검색"""
        start_date = self._period_to_date(period)
        sql = """
            SELECT site_id, transaction_date, product_name, pack_unit,
                   quantity, unit_price, sales_amount, manufacturer, edi_code
            FROM sales_ledger
            WHERE product_name LIKE ? AND transaction_date >= ?
        """
        params = [f'%{keyword}%', start_date]

        if site_id and site_id != "all":
            sql += " AND site_id = ?"
            params.append(site_id)

        sql += " ORDER BY transaction_date DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── 약품별 합계 ──

    def summary(self, site_id: str = "all", period: str = "1m",
                top_n: int = 20) -> list[dict]:
        """약품별 주문 합계 (TOP N)"""
        start_date = self._period_to_date(period)
        sql = """
            SELECT product_name, pack_unit,
                   COUNT(*) as order_count,
                   SUM(quantity) as total_qty,
                   AVG(unit_price) as avg_price,
                   SUM(sales_amount) as total_amount
            FROM sales_ledger
            WHERE transaction_date >= ?
        """
        params = [start_date]

        if site_id and site_id != "all":
            sql += " AND site_id = ?"
            params.append(site_id)

        sql += " GROUP BY product_name, pack_unit ORDER BY total_amount DESC LIMIT ?"
        params.append(top_n)

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── 도매별 가격 비교 ──

    def compare(self, keyword: str, period: str = "3m") -> list[dict]:
        """도매별 같은 약품 가격 비교"""
        start_date = self._period_to_date(period)
        sql = """
            SELECT site_id, product_name, pack_unit,
                   COUNT(*) as order_count,
                   AVG(unit_price) as avg_price,
                   MIN(unit_price) as min_price,
                   MAX(unit_price) as max_price,
                   SUM(quantity) as total_qty,
                   SUM(sales_amount) as total_amount
            FROM sales_ledger
            WHERE product_name LIKE ? AND transaction_date >= ?
            GROUP BY site_id, product_name
            ORDER BY avg_price ASC
        """
        with self._conn() as conn:
            rows = conn.execute(sql, [f'%{keyword}%', start_date]).fetchall()
        return [dict(r) for r in rows]

    # ── 월별 추이 ──

    def trend(self, keyword: str = "", site_id: str = "all",
              period: str = "6m") -> list[dict]:
        """월별 주문 추이"""
        start_date = self._period_to_date(period)
        sql = """
            SELECT substr(transaction_date, 1, 7) as month,
                   COUNT(*) as order_count,
                   SUM(quantity) as total_qty,
                   SUM(sales_amount) as total_amount
            FROM sales_ledger
            WHERE transaction_date >= ?
        """
        params = [start_date]

        if keyword:
            sql += " AND product_name LIKE ?"
            params.append(f'%{keyword}%')

        if site_id and site_id != "all":
            sql += " AND site_id = ?"
            params.append(site_id)

        sql += " GROUP BY month ORDER BY month"

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── DB 현황 ──

    def stats(self) -> dict:
        """DB 현황 통계"""
        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0

        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM sales_ledger").fetchone()[0]

            sites = conn.execute("""
                SELECT site_id, COUNT(*) as cnt,
                       MIN(transaction_date) as first_date,
                       MAX(transaction_date) as last_date,
                       MAX(synced_at) as last_sync
                FROM sales_ledger GROUP BY site_id
            """).fetchall()

            products_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]

        return {
            "db_size_bytes": db_size,
            "db_size_mb": round(db_size / 1024 / 1024, 2),
            "total_ledger_entries": total,
            "total_products": products_count,
            "sites": [dict(s) for s in sites],
        }
