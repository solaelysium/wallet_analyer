import os
import sqlite3
import threading
from datetime import date, datetime, timezone

_thread_local = threading.local()


def _metadata_dir() -> str:
    """
    Get the path to the metadata directory
    """
    project_root = os.path.dirname(os.path.dirname(__file__))
    path = os.path.join(project_root, "metadata")
    os.makedirs(path, exist_ok=True)
    return path


def _tokens_db_path() -> str:
    """
    Get the path to the tokens database
    """
    return os.path.join(_metadata_dir(), "tokens.db")


def _quotes_db_path() -> str:
    """
    Get the path to the quotes database
    """
    return os.path.join(_metadata_dir(), "quotes.db")


def _excluded_db_path() -> str:
    """
    Get the path to the excluded tokens database
    """
    return os.path.join(_metadata_dir(), "excluded_quotes.db")


def _init_tokens_db(conn: sqlite3.Connection) -> None:
    """
    Initialize the tokens table
    """
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tokens (
            address TEXT PRIMARY KEY,
            symbol  TEXT,
            decimals INTEGER
        )
        """
    )


def _init_quotes_db(conn: sqlite3.Connection) -> None:
    """
    Initialize the quotes table (fresh schema, no migration).
    """
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quotes (
            blockchain    TEXT NOT NULL,
            token_address TEXT NOT NULL,
            timestamp_ms  INTEGER NOT NULL,
            price         REAL,
            PRIMARY KEY (blockchain, token_address, timestamp_ms)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quotes_token_ts ON quotes(blockchain, token_address, timestamp_ms)")


def _init_excluded_db(conn: sqlite3.Connection) -> None:
    """
    Initialize the excluded tokens table (fresh schema, no migration).
    """
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS excluded_tokens (
            blockchain TEXT NOT NULL,
            address    TEXT NOT NULL,
            reason     TEXT,
            PRIMARY KEY (blockchain, address)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_excluded_blockchain_address ON excluded_tokens(blockchain, address)")


def _tokens_conn() -> sqlite3.Connection:
    """
    Get a connection to the tokens table
    """
    conn = getattr(_thread_local, "tokens_conn", None)
    if conn is None:
        conn = sqlite3.connect(_tokens_db_path(), timeout=30)
        _init_tokens_db(conn)
        _thread_local.tokens_conn = conn
    return conn


def _quotes_conn() -> sqlite3.Connection:
    """
    Get a connection to the quotes table
    """
    conn = getattr(_thread_local, "quotes_conn", None)
    if conn is None:
        conn = sqlite3.connect(_quotes_db_path(), timeout=30)
        _init_quotes_db(conn)
        _thread_local.quotes_conn = conn
    return conn


def _excluded_conn() -> sqlite3.Connection:
    """
    Get a connection to the excluded tokens table
    """
    conn = getattr(_thread_local, "excluded_conn", None)
    if conn is None:
        conn = sqlite3.connect(_excluded_db_path(), timeout=30)
        _init_excluded_db(conn)
        _thread_local.excluded_conn = conn
    return conn


class DB:
    @staticmethod
    def get_token(addr_key: str) -> tuple[str, int] | None:
        """
        Get a token from the tokens table

        Args:
        - `addr_key (str)`: Address of the token
        """
        row = (
            _tokens_conn()
            .execute(
                "SELECT symbol, decimals FROM tokens WHERE address=?",
                (addr_key,),
            )
            .fetchone()
        )
        if row is None:
            return None
        sym = str(row[0]) if row[0] is not None else addr_key
        try:
            dec = int(row[1]) if row[1] is not None else 18
        except Exception:
            dec = 18
        return (sym, dec)

    @staticmethod
    def upsert_token(addr_key: str, symbol: str, decimals: int) -> None:
        """
        Upsert a token into the tokens table

        Args:
        - `addr_key (str)`: Address of the token
        - `symbol (str)`: Symbol of the token
        - `decimals (int)`: Decimals of the token
        """
        try:
            with _tokens_conn():
                _tokens_conn().execute(
                    """
                    INSERT INTO tokens(address, symbol, decimals)
                    VALUES(?, ?, ?)
                    ON CONFLICT(address) DO UPDATE SET symbol=excluded.symbol, decimals=excluded.decimals
                    """,
                    (addr_key, symbol, int(decimals)),
                )
        except Exception:
            pass

    @staticmethod
    def has_quotes_for_day(addr_key: str, d: date, blockchain: str = "ethereum") -> bool:
        """
        Check if the token has quotes for a given day

        Args:
        - `addr_key (str)`: Address of the token
        - `d (date)`: Date
        """
        start_ms = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = start_ms + 86_400_000 - 1
        row = (
            _quotes_conn()
            .execute(
                "SELECT 1 FROM quotes WHERE blockchain=? AND token_address=? AND timestamp_ms BETWEEN ? AND ? LIMIT 1",
                (blockchain, addr_key, start_ms, end_ms),
            )
            .fetchone()
        )
        return row is not None

    @staticmethod
    def has_any_quotes(addr_key: str, blockchain: str = "ethereum") -> bool:
        """
        Check if the token has any quotes at all for a given blockchain

        Args:
        - `addr_key (str)`: Address of the token
        - `blockchain (str)`: Blockchain key
        """
        row = (
            _quotes_conn()
            .execute(
                "SELECT 1 FROM quotes WHERE blockchain=? AND token_address=? LIMIT 1",
                (blockchain, addr_key),
            )
            .fetchone()
        )
        return row is not None

    @staticmethod
    def insert_quotes_rows(addr_key: str, rows: list[tuple[int, float]], blockchain: str = "ethereum") -> None:
        """
        Insert quotes rows into the quotes table

        Args:
        - `addr_key (str)`: Address of the token
        - `rows (list[tuple[int, float]])`: List of tuples containing timestamp and price
        """
        if not rows:
            return
        with _quotes_conn():
            _quotes_conn().executemany(
                """
                INSERT INTO quotes(blockchain, token_address, timestamp_ms, price)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(blockchain, token_address, timestamp_ms) DO UPDATE SET price=excluded.price
                """,
                [(blockchain, addr_key, int(ts), float(px)) for ts, px in rows],
            )

    @staticmethod
    def query_nearest_price(addr_key: str, day_start_ms: int, day_end_ms: int, target_ms: int, blockchain: str = "ethereum") -> float | None:
        """
        Query the nearest price for a given token address and timestamp

        Args:
        - `addr_key (str)`: Address of the token
        - `day_start_ms (int)`: Start of the day in milliseconds
        - `day_end_ms (int)`: End of the day in milliseconds
        - `target_ms (int)`: Target timestamp in milliseconds
        """
        row = (
            _quotes_conn()
            .execute(
                """
            SELECT timestamp_ms, price
            FROM quotes
            WHERE blockchain=? AND token_address=? AND timestamp_ms BETWEEN ? AND ?
            ORDER BY ABS(timestamp_ms - ?) ASC
            LIMIT 1
            """,
                (blockchain, addr_key, day_start_ms, day_end_ms, target_ms),
            )
            .fetchone()
        )
        if row is None:
            return None
        return float(row[1]) if row[1] is not None else None

    @staticmethod
    def is_excluded(addr_key: str, blockchain: str = "ethereum") -> bool:
        """
        Check if the token is excluded

        Args:
        - `addr_key (str)`: Address of the token
        """
        row = (
            _excluded_conn()
            .execute(
                "SELECT 1 FROM excluded_tokens WHERE blockchain=? AND address=? LIMIT 1",
                (blockchain, addr_key),
            )
            .fetchone()
        )
        return row is not None

    @staticmethod
    def add_excluded(addr_key: str, reason: str, blockchain: str = "ethereum") -> None:
        """
        Add a token to the excluded tokens table

        Args:
        - `addr_key (str)`: Address of the token
        - `reason (str)`: Reason for exclusion
        """
        try:
            with _excluded_conn():
                _excluded_conn().execute(
                    """
                    INSERT INTO excluded_tokens(blockchain, address, reason)
                    VALUES(?, ?, ?)
                    ON CONFLICT(blockchain, address) DO UPDATE SET reason=excluded.reason
                    """,
                    (blockchain, addr_key, reason),
                )
        except Exception:
            pass
