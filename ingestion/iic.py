"""Ikaros Ingestion Control (IIC): persistent Snowflake batch checkpoints.

This initial implementation assumes one IIC writer at a time. Snowflake's
standard-table UNIQUE constraints are informational, not enforced. Use a
single scheduler until an atomic claim mechanism is introduced.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Protocol
import uuid


class Status(str, Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    NO_DATA = "NO_DATA"
    FAILED = "FAILED"


@dataclass(frozen=True)
class BatchKey:
    dataset: str
    symbol: str
    provider: str
    start: date
    end: date

    def __post_init__(self):
        if self.dataset not in {"FX", "GPW"}:
            raise ValueError("dataset must be FX or GPW")
        if not self.symbol or not self.provider:
            raise ValueError("symbol/provider cannot be empty")
        if self.start > self.end:
            raise ValueError("start cannot be after end")


@dataclass(frozen=True)
class BatchState:
    status: Status
    started_at: datetime | None = None
    attempts: int = 0


def should_run(
    state: BatchState | None,
    *,
    now: datetime,
    lease_minutes: int = 90,
    force: bool = False,
    retry_no_data: bool = False,
) -> tuple[bool, str]:
    """Pure planning rule, independent of Snowflake."""
    if lease_minutes < 1:
        raise ValueError("lease_minutes must be positive")
    if state is None:
        return True, "NEW"
    if state.status is Status.RUNNING and state.started_at is not None:
        started = state.started_at.replace(tzinfo=timezone.utc) if state.started_at.tzinfo is None else state.started_at
        reference = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
        if reference - started < timedelta(minutes=lease_minutes):
            return False, "IN_PROGRESS"
    if state.status is Status.SUCCESS and not force:
        return False, "ALREADY_SUCCESSFUL"
    if state.status is Status.NO_DATA and not (retry_no_data or force):
        return False, "NO_DATA_CACHED"
    return True, "RETRY" if state.status in (Status.FAILED, Status.RUNNING) else "RECHECK"


class Cursor(Protocol):
    def execute(self, sql: str, params: tuple = ()) -> object: ...
    def fetchall(self) -> list[tuple]: ...
    @property
    def rowcount(self) -> int: ...


DDL = '''CREATE TABLE IF NOT EXISTS "IIC_BATCHES" (
    "BATCH_ID" VARCHAR NOT NULL,
    "DATASET" VARCHAR NOT NULL,
    "SYMBOL" VARCHAR NOT NULL,
    "PROVIDER" VARCHAR NOT NULL,
    "START_DATE" DATE NOT NULL,
    "END_DATE" DATE NOT NULL,
    "STATUS" VARCHAR NOT NULL,
    "ATTEMPTS" NUMBER(38,0) NOT NULL DEFAULT 0,
    "FETCHED_ROWS" NUMBER(38,0),
    "CANDIDATE_ROWS" NUMBER(38,0),
    "ERROR_MESSAGE" VARCHAR,
    "STARTED_AT" TIMESTAMP_NTZ,
    "FINISHED_AT" TIMESTAMP_NTZ,
    "UPDATED_AT" TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
)'''

KEY_WHERE = ('"DATASET" = %s AND "SYMBOL" = %s AND "PROVIDER" = %s '
             'AND "START_DATE" = TO_DATE(%s) AND "END_DATE" = TO_DATE(%s)')


def _params(key: BatchKey) -> tuple:
    return key.dataset, key.symbol, key.provider, key.start.isoformat(), key.end.isoformat()


class SnowflakeIIC:
    """Simple checkpoint store. Does not own the Snowflake connection.

    No transaction spans an HTTP provider call. A crash after market-data MERGE
    but before marking SUCCESS is safe: the next run repeats the idempotent MERGE.
    """

    def __init__(self, connection):
        self.connection = connection

    def prepare(self) -> None:
        with closing(self.connection.cursor()) as cur:
            cur.execute(DDL)

    def read(self, key: BatchKey) -> tuple[str, BatchState] | None:
        with closing(self.connection.cursor()) as cur:
            cur.execute(
                'SELECT "BATCH_ID", "STATUS", "STARTED_AT", "ATTEMPTS" '
                'FROM "IIC_BATCHES" WHERE ' + KEY_WHERE,
                _params(key),
            )
            records = cur.fetchall()
        if len(records) > 1:
            raise RuntimeError(f"Duplicate IIC state rows for {key}")
        if not records:
            return None
        batch_id, status, started, attempts = records[0]
        return str(batch_id), BatchState(Status(status), started, int(attempts))

    def start(self, key: BatchKey, *, force: bool = False,
              retry_no_data: bool = False, lease_minutes: int = 90) -> tuple[bool, str]:
        current = self.read(key)
        eligible, reason = should_run(
            current[1] if current else None, now=datetime.now(timezone.utc),
            lease_minutes=lease_minutes, force=force, retry_no_data=retry_no_data,
        )
        if not eligible:
            return False, reason
        with closing(self.connection.cursor()) as cur:
            if current is None:
                batch_id = str(uuid.uuid4())
                cur.execute(
                    'INSERT INTO "IIC_BATCHES" '
                    '("BATCH_ID", "DATASET", "SYMBOL", "PROVIDER", "START_DATE", '
                    '"END_DATE", "STATUS", "ATTEMPTS", "STARTED_AT", "UPDATED_AT") '
                    'VALUES (%s, %s, %s, %s, TO_DATE(%s), TO_DATE(%s), '
                    "'RUNNING', 1, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())",
                    (batch_id, *_params(key)),
                )
            else:
                batch_id = current[0]
                cur.execute(
                    'UPDATE "IIC_BATCHES" SET "STATUS" = %s, '
                    '"ATTEMPTS" = "ATTEMPTS" + 1, '
                    '"STARTED_AT" = CURRENT_TIMESTAMP(), "FINISHED_AT" = NULL, '
                    '"FETCHED_ROWS" = NULL, "CANDIDATE_ROWS" = NULL, '
                    '"ERROR_MESSAGE" = NULL, "UPDATED_AT" = CURRENT_TIMESTAMP() '
                    'WHERE "BATCH_ID" = %s',
                    (Status.RUNNING.value, batch_id),
                )
        return True, reason

    def finish(self, key: BatchKey, status: Status, *, fetched: int = 0,
               candidate: int = 0, error: str | None = None) -> None:
        if status is Status.RUNNING:
            raise ValueError("Cannot finish with RUNNING")
        if fetched < 0 or candidate < 0:
            raise ValueError("Row counts must be nonnegative")
        found = self.read(key)
        if found is None or found[1].status is not Status.RUNNING:
            raise RuntimeError(f"Batch is not RUNNING: {key}")
        with closing(self.connection.cursor()) as cur:
            cur.execute(
                'UPDATE "IIC_BATCHES" SET "STATUS" = %s, '
                '"FETCHED_ROWS" = %s, "CANDIDATE_ROWS" = %s, '
                '"ERROR_MESSAGE" = %s, "FINISHED_AT" = CURRENT_TIMESTAMP(), '
                '"UPDATED_AT" = CURRENT_TIMESTAMP() WHERE "BATCH_ID" = %s',
                (status.value, fetched, candidate, (error or "")[:4000] or None, found[0]),
            )
