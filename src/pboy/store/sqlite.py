"""SQLite storage backend with AES-256-GCM encrypted secrets."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Final

import aiosqlite
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from loguru import logger

from pboy.models.cloudevent import CloudEvent, current_iso_time
from pboy.models.execution import ExecutionData
from pboy.models.hook import Hook, HookCreate, HookUpdate
from pboy.store.base import StoreBase

_GCM_NONCE_BYTES = 12
VALID_HOOK_COLUMNS: Final[frozenset[str]] = frozenset(
    {"topic", "filter", "exec", "concurrency", "timeout", "status"}
)


def _derive_32byte_key(secret_key: str) -> bytes:
    """Derive 32-byte encryption key using SHA-256."""
    return hashlib.sha256(secret_key.encode("utf-8")).digest()


def _encrypt_aes_gcm(plaintext: str, key_bytes: bytes) -> tuple[str, str]:
    """Encrypt plaintext using AES-256-GCM. Returns (b64_ciphertext, b64_nonce)."""
    nonce = os.urandom(_GCM_NONCE_BYTES)
    aesgcm = AESGCM(key_bytes)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(ciphertext).decode("ascii"), base64.b64encode(nonce).decode("ascii")


def _decrypt_aes_gcm(b64_ciphertext: str, b64_nonce: str, key_bytes: bytes) -> str:
    """Decrypt ciphertext using AES-256-GCM."""
    ciphertext = base64.b64decode(b64_ciphertext.encode("ascii"))
    nonce = base64.b64decode(b64_nonce.encode("ascii"))
    aesgcm = AESGCM(key_bytes)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")


class SQLiteStore(StoreBase):
    """Async SQLite storage implementation conforming to StoreBase."""

    def __init__(self, db_path: str | Path | None = None, secret_key: str | None = None) -> None:
        from pboy.core.config import get_settings

        settings = get_settings()
        self.db_path = Path(db_path) if db_path else settings.db_path
        self._secret_key = secret_key or settings.secret_key or self._load_or_create_key()
        self._key_bytes = _derive_32byte_key(self._secret_key)
        self._db: aiosqlite.Connection | None = None

    def _load_or_create_key(self) -> str:
        """Load secret key from file or generate a persistent key."""
        from pboy.core.config import get_settings

        settings = get_settings()
        key_path = settings.secret_key_path
        if key_path.exists():
            return key_path.read_text(encoding="utf-8").strip()
        key = base64.b64encode(os.urandom(32)).decode("ascii")
        try:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(
                str(key_path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                stat.S_IRUSR | stat.S_IWUSR,  # 0o600
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(key)
            os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
        except FileExistsError:
            return key_path.read_text(encoding="utf-8").strip()
        except OSError:
            logger.warning(f"Could not persist secret key to {key_path}, using in-memory key.")
        return key

    async def init_db(self) -> None:
        """Initialize SQLite connection and execute table schemas."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._execute_schema()

    async def close(self) -> None:
        """Close SQLite database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    async def _execute_schema(self) -> None:
        if not self._db:
            raise RuntimeError("Database not initialized")

        schema = """
        CREATE TABLE IF NOT EXISTS hooks (
            name TEXT PRIMARY KEY,
            topic TEXT NOT NULL,
            filter TEXT,
            exec TEXT NOT NULL,
            concurrency INTEGER NOT NULL DEFAULT 1,
            timeout INTEGER NOT NULL DEFAULT 180,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
        );

        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            specversion TEXT NOT NULL,
            source TEXT NOT NULL,
            type TEXT NOT NULL,
            time TEXT NOT NULL,
            datacontenttype TEXT NOT NULL,
            data TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS executions (
            execution_id TEXT PRIMARY KEY,
            hook_name TEXT NOT NULL,
            event_id TEXT NOT NULL,
            pid INTEGER,
            status TEXT NOT NULL,
            exit_code INTEGER,
            stdout TEXT,
            stderr TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT
        );

        CREATE TABLE IF NOT EXISTS state (
            event_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (event_id, key)
        );

        CREATE TABLE IF NOT EXISTS secrets (
            key TEXT PRIMARY KEY,
            secret_value TEXT NOT NULL,
            iv TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_events_time ON events(time DESC);
        CREATE INDEX IF NOT EXISTS idx_executions_started_at ON executions(started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_hooks_topic ON hooks(topic);
        CREATE INDEX IF NOT EXISTS idx_hooks_created_at ON hooks(created_at DESC);
        """
        await self._db.executescript(schema)
        await self._db.commit()

    # ── Hooks ────────────────────────────────────────────────────────────────
    async def create_hook(self, data: HookCreate) -> Hook:
        if not self._db:
            raise RuntimeError("Database not initialized")
        hook = Hook(**data.model_dump())
        await self._db.execute(
            """
            INSERT INTO hooks (name, topic, filter, exec, concurrency, timeout, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hook.name,
                hook.topic,
                hook.filter,
                hook.exec,
                hook.concurrency,
                hook.timeout,
                hook.created_at,
                hook.status,
            ),
        )
        await self._db.commit()
        return hook

    async def get_hook(self, name: str) -> Hook | None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._db.execute(
            "SELECT name, topic, filter, exec, concurrency, timeout, created_at, status FROM hooks WHERE name = ?",
            (name,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return Hook(
                name=row["name"],
                topic=row["topic"],
                filter=row["filter"],
                exec=row["exec"],
                concurrency=row["concurrency"],
                timeout=row["timeout"],
                created_at=row["created_at"],
                status=row["status"],
            )

    async def list_hooks(self) -> list[Hook]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._db.execute(
            "SELECT name, topic, filter, exec, concurrency, timeout, created_at, status FROM hooks ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                Hook(
                    name=r["name"],
                    topic=r["topic"],
                    filter=r["filter"],
                    exec=r["exec"],
                    concurrency=r["concurrency"],
                    timeout=r["timeout"],
                    created_at=r["created_at"],
                    status=r["status"],
                )
                for r in rows
            ]

    async def update_hook(self, name: str, data: HookUpdate) -> Hook | None:
        hook = await self.get_hook(name)
        if not hook or not self._db:
            return None
        dump = data.model_dump(exclude_unset=True)
        if not dump:
            return hook
        fields = []
        values = []
        for k, v in dump.items():
            if k not in VALID_HOOK_COLUMNS:
                continue
            fields.append(f"{k} = ?")
            values.append(v)
        if not fields:
            return hook
        values.append(name)
        await self._db.execute(
            f"UPDATE hooks SET {', '.join(fields)} WHERE name = ?",
            values,
        )
        await self._db.commit()
        return await self.get_hook(name)

    async def delete_hook(self, name: str) -> bool:
        if not self._db:
            raise RuntimeError("Database not initialized")
        cursor = await self._db.execute("DELETE FROM hooks WHERE name = ?", (name,))
        await self._db.commit()
        return cursor.rowcount > 0

    # ── Events ───────────────────────────────────────────────────────────────
    async def create_event(self, event: CloudEvent) -> None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        await self._db.execute(
            """
            INSERT OR REPLACE INTO events (id, specversion, source, type, time, datacontenttype, data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.specversion,
                event.source,
                event.type,
                event.time,
                event.datacontenttype,
                json.dumps(event.data),
            ),
        )
        await self._db.commit()

    async def get_event(self, event_id: str) -> CloudEvent | None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._db.execute(
            "SELECT id, specversion, source, type, time, datacontenttype, data FROM events WHERE id = ?",
            (event_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return CloudEvent(
                id=row["id"],
                specversion=row["specversion"],
                source=row["source"],
                type=row["type"],
                time=row["time"],
                datacontenttype=row["datacontenttype"],
                data=json.loads(row["data"]),
            )

    async def list_events(self, limit: int = 50, offset: int = 0) -> list[CloudEvent]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._db.execute(
            "SELECT id, specversion, source, type, time, datacontenttype, data FROM events ORDER BY time DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                CloudEvent(
                    id=r["id"],
                    specversion=r["specversion"],
                    source=r["source"],
                    type=r["type"],
                    time=r["time"],
                    datacontenttype=r["datacontenttype"],
                    data=json.loads(r["data"]),
                )
                for r in rows
            ]

    # ── Executions ───────────────────────────────────────────────────────────
    async def record_execution(self, exec_data: ExecutionData) -> None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        await self._db.execute(
            """
            INSERT OR REPLACE INTO executions (
                execution_id, hook_name, event_id, pid, status, exit_code, stdout, stderr, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                exec_data.execution_id,
                exec_data.hook_name,
                exec_data.event_id,
                exec_data.pid,
                exec_data.status,
                exec_data.exit_code,
                exec_data.stdout,
                exec_data.stderr,
                exec_data.started_at,
                exec_data.finished_at,
            ),
        )
        await self._db.commit()

    async def get_execution(self, exec_id: str) -> ExecutionData | None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._db.execute(
            """
            SELECT execution_id, hook_name, event_id, pid, status, exit_code, stdout, stderr, started_at, finished_at
            FROM executions WHERE execution_id = ?
            """,
            (exec_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return ExecutionData(
                execution_id=row["execution_id"],
                hook_name=row["hook_name"],
                event_id=row["event_id"],
                pid=row["pid"],
                status=row["status"],
                exit_code=row["exit_code"],
                stdout=row["stdout"],
                stderr=row["stderr"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
            )

    async def list_executions(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        event_id: str | None = None,
    ) -> list[ExecutionData]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        if event_id is None:
            query = """
                SELECT execution_id, hook_name, event_id, pid, status, exit_code, stdout, stderr,
                       started_at, finished_at
                FROM executions ORDER BY started_at DESC LIMIT ? OFFSET ?
                """
            params: tuple[Any, ...] = (limit, offset)
        else:
            query = """
                SELECT execution_id, hook_name, event_id, pid, status, exit_code, stdout, stderr,
                       started_at, finished_at
                FROM executions WHERE event_id = ?
                ORDER BY started_at DESC LIMIT ? OFFSET ?
                """
            params = (event_id, limit, offset)
        async with self._db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [
                ExecutionData(
                    execution_id=r["execution_id"],
                    hook_name=r["hook_name"],
                    event_id=r["event_id"],
                    pid=r["pid"],
                    status=r["status"],
                    exit_code=r["exit_code"],
                    stdout=r["stdout"],
                    stderr=r["stderr"],
                    started_at=r["started_at"],
                    finished_at=r["finished_at"],
                )
                for r in rows
            ]

    # ── State ────────────────────────────────────────────────────────────────
    async def put_state(self, event_id: str, key: str, value: dict[str, Any]) -> None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        now_str = current_iso_time()
        await self._db.execute(
            """
            INSERT OR REPLACE INTO state (event_id, key, value, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (event_id, key, json.dumps(value), now_str),
        )
        await self._db.commit()

    async def get_state(self, event_id: str, key: str) -> dict[str, Any] | None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._db.execute(
            "SELECT value FROM state WHERE event_id = ? AND key = ?",
            (event_id, key),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            loaded = json.loads(row["value"])
            if not isinstance(loaded, dict):
                raise ValueError(f"State value for {event_id}/{key} must be a JSON object")
            return loaded

    # ── Secrets (AES-256-GCM Encrypted) ──────────────────────────────────────
    async def put_secret(self, key: str, value: str) -> None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        cipher_b64, nonce_b64 = _encrypt_aes_gcm(value, self._key_bytes)
        now_str = current_iso_time()
        await self._db.execute(
            """
            INSERT OR REPLACE INTO secrets (key, secret_value, iv, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (key, cipher_b64, nonce_b64, now_str),
        )
        await self._db.commit()

    async def get_secret(self, key: str) -> str | None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._db.execute(
            "SELECT secret_value, iv FROM secrets WHERE key = ?",
            (key,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return _decrypt_aes_gcm(row["secret_value"], row["iv"], self._key_bytes)


__all__ = ["SQLiteStore"]
