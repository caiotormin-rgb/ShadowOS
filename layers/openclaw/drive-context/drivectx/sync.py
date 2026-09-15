"""Synchronization: bounded initial listing, then incremental changes.

Three invariants carry the design:

* The cursor is captured *before* the initial listing and stored *after* the
  data transaction commits. Capturing it afterwards would silently drop every
  change that landed during the listing; storing it first would drop a page on
  any crash. Both orders are wrong in ways that are invisible until someone
  notices a missing file months later.
* Every write is idempotent, so an interrupted run is resumed by running it
  again -- there is no repair path to get wrong.
* An expired page token is an expected condition with a bounded recovery, not a
  failure. Drive rejects a stale cursor rather than serving it; the answer is
  one bounded resynchronization, recorded as such, and at most one per run.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Sequence

from .drive import DriveReadOnly, TransportError
from .sharing import summarize
from .store import DriveFile, Store

DEFAULT_PAGE_SIZE = 1000

# A resync re-lists the corpus, so it needs a ceiling. 200 pages x 1000 files
# bounds it at 200,000 files; past that the run fails and leaves the previous
# good index in place rather than half-rebuilding it.
MAX_RESYNC_PAGES = 200

# Concurrency for the explicit re-check path only. Drive returns full metadata
# inline in files.list and changes.list, so the ordinary sync has no per-item
# second request to parallelise -- unlike Gmail, where the N+1 metadata fetch is
# the whole cost of a load.
DEFAULT_CONCURRENCY = 8


@dataclass
class SyncResult:
    kind: str
    added: int = 0
    updated: int = 0
    deleted: int = 0
    pruned: int = 0
    excluded: int = 0
    pages: int = 0
    status: str = "ok"
    error_class: str | None = None
    elapsed: float = 0.0
    resynced: bool = False
    complete: bool = True

    def summary(self) -> str:
        return (f"{self.kind}: +{self.added} ~{self.updated} -{self.deleted} "
                f"pruned={self.pruned} excluded={self.excluded} pages={self.pages} "
                f"in {self.elapsed:.1f}s "
                f"[{self.status}{'/' + self.error_class if self.error_class else ''}]")


def _ts(value: str | None) -> int | None:
    """RFC 3339 -> epoch seconds. Drive sends 'Z', which fromisoformat accepts
    from 3.11 on; a naive timestamp is read as UTC rather than as local time,
    because Drive never sends local time and guessing would shift every
    timestamp by the machine's offset."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def parse_file(raw: dict) -> DriveFile | None:
    """Drive file JSON -> DriveFile.

    Never reads content: the field mask in drivectx.drive means Google does not
    send any, so there is nothing here to accidentally log or cache. The
    permissions array is reduced to a summary on the way in and the array itself
    is dropped on the floor.
    """
    file_id = raw.get("id")
    if not file_id:
        return None

    size = raw.get("size")
    try:
        size_bytes = int(size) if size is not None else None
    except (TypeError, ValueError):
        size_bytes = None

    shortcut = raw.get("shortcutDetails") or {}
    owners = tuple(
        (o.get("displayName", ""), o.get("emailAddress", ""))
        for o in (raw.get("owners") or []) if isinstance(o, dict)
    )

    return DriveFile(
        file_id=file_id,
        name=raw.get("name", ""),
        mime_type=raw.get("mimeType", ""),
        created_ts=_ts(raw.get("createdTime")),
        modified_ts=_ts(raw.get("modifiedTime")),
        size_bytes=size_bytes,
        trashed=bool(raw.get("trashed")),
        explicitly_trashed=bool(raw.get("explicitlyTrashed")),
        starred=bool(raw.get("starred")),
        owned_by_me=raw.get("ownedByMe"),
        web_view_link=raw.get("webViewLink", "") or "",
        shortcut_target_id=shortcut.get("targetId"),
        shortcut_target_mime=shortcut.get("targetMimeType"),
        parents=tuple(raw.get("parents") or ()),
        owners=owners,
        sharing=summarize(raw),
    )


class Syncer:
    def __init__(self, store: Store, api: DriveReadOnly, *,
                 include_shared_drives: bool = False,
                 page_size: int = DEFAULT_PAGE_SIZE,
                 concurrency: int = DEFAULT_CONCURRENCY,
                 resume: bool = True,
                 max_resync_pages: int = MAX_RESYNC_PAGES,
                 on_progress: Callable[[str], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        """include_shared_drives is a named setting defaulting to false.

        It is enforced twice on purpose: as request parameters in
        drivectx.drive, and per row here. The API flags are the efficient
        boundary and the row check is the honest one -- Drive has historically
        returned shared-drive items through combinations of flags that were
        supposed to exclude them, and a row carrying a driveId is a row this
        layer does not index.
        """
        self.store = store
        self.api = api
        self.include_shared_drives = bool(include_shared_drives)
        api.include_shared_drives = self.include_shared_drives
        self.page_size = page_size
        self.concurrency = max(1, concurrency)
        self.resume = resume
        self.max_resync_pages = max_resync_pages
        self._progress = on_progress or (lambda _msg: None)
        self._sleep = sleep
        self.skipped = 0

    # -- helpers ---------------------------------------------------------
    def _included(self, raw: dict) -> bool:
        """A row carrying a driveId belongs to a shared drive, which is out of
        scope unless the operator turned the setting on."""
        if self.include_shared_drives:
            return True
        return not raw.get("driveId")

    def _retrying(self, fn: Callable[[], dict]) -> dict:
        """One call, with the failure that is normal rather than fatal."""
        for attempt in range(4):
            try:
                return fn()
            except TransportError as exc:
                if exc.error_class == "rate_limited":
                    self._sleep(2.0 * (2 ** attempt))
                    continue
                raise
        raise TransportError("rate_limited_persistent")

    def _apply_page(self, rows: Sequence[dict], res: SyncResult, *,
                    skip_existing: bool = False, run_id: int | None = None) -> None:
        """Upsert one page inside one transaction.

        skip_existing is for the initial listing ONLY. An incremental run must
        never skip: Drive told us the item changed, and the already-present row
        is exactly the stale one.
        """
        parsed = [f for f in (parse_file(r) for r in rows) if f is not None]
        if skip_existing and self.resume:
            already = self.store.existing_ids([f.file_id for f in parsed])
            if already:
                self.skipped += len(already)
                parsed = [f for f in parsed if f.file_id not in already]
        if not parsed:
            return
        self.store.begin()
        for f in parsed:
            if self.store.upsert_file(f, run_id=run_id) == "added":
                res.added += 1
            else:
                res.updated += 1
        self.store.commit()

    # -- initial / resync -------------------------------------------------
    def initial_sync(self, *, kind: str = "initial", max_files: int | None = None,
                     max_pages: int | None = None) -> SyncResult:
        """Bounded listing of the personal corpus.

        kind='resync' is the page-token-expiry recovery. It differs in three
        ways: it is bounded by a page budget, it never uses the resume skip
        (a reconciliation pass must touch every row), and on completion it
        sweeps rows the listing did not see.
        """
        t0 = time.time()
        res = SyncResult(kind=kind)
        run_id = self.store.start_run(kind)
        budget = max_pages if max_pages is not None else (
            self.max_resync_pages if kind == "resync" else None)
        skip_existing = self.resume and kind == "initial"

        try:
            # Cursor FIRST: anything that changes during the listing is replayed
            # by the next incremental run rather than lost.
            cursor = self._retrying(self.api.start_page_token)
            root = self._retrying(self.api.root_folder_id)
            self.store.begin()
            self.store.set_root_folder_id(root)
            self.store.commit()

            token, seen = None, 0
            while True:
                page = self._retrying(
                    lambda: self.api.list_files(page_token=token, page_size=self.page_size))
                res.pages += 1
                rows = page.get("files", [])
                kept = [r for r in rows if self._included(r)]
                res.excluded += len(rows) - len(kept)

                # Stamping each row with this run's id is what lets the sweep
                # below say exactly which rows the listing never saw.
                self._apply_page(kept, res, skip_existing=skip_existing, run_id=run_id)
                seen += len(rows)
                self._progress(f"{seen:,} items, page {res.pages}")

                if max_files is not None and seen >= max_files:
                    res.complete = False
                    break
                if budget is not None and res.pages >= budget and page.get("nextPageToken"):
                    # The listing outran its ceiling. Fail the run, leave the
                    # cursor untouched, and leave the previous good index in
                    # place. Rows already written are refreshed truth, not
                    # damage -- what is missing is the proof of completeness.
                    res.complete = False
                    raise _BudgetExceeded()
                token = page.get("nextPageToken")
                if not token:
                    break

            if kind == "resync" and res.complete:
                self.store.begin()
                res.deleted += self.store.sweep_missing(run_id=run_id)
                self.store.commit()

            self.store.begin()
            self.store.advance_cursor(cursor, kind=kind)
            if res.complete:
                # Only a listing that reached the end may claim completeness.
                self.store.mark_initial_complete()
            self.store.commit()

        except _BudgetExceeded:
            res.status, res.error_class = "error", "resync_budget_exceeded"
            self.store.abort()
            self.store.begin(); self.store.record_failure(res.error_class); self.store.commit()
        except TransportError as exc:
            res.status, res.error_class = "error", exc.error_class
            self.store.abort()
            self.store.begin(); self.store.record_failure(exc.error_class); self.store.commit()
        except Exception:
            res.status, res.error_class = "error", "internal"
            self.store.abort()
            self.store.begin(); self.store.record_failure("internal"); self.store.commit()
            raise
        finally:
            res.elapsed = time.time() - t0
            self.store.finish_run(run_id, status=res.status, added=res.added,
                                  updated=res.updated, deleted=res.deleted,
                                  excluded=res.excluded, error_class=res.error_class)
        return res

    # -- incremental -----------------------------------------------------
    def incremental_sync(self, *, allow_resync: bool = True) -> SyncResult:
        t0 = time.time()
        res = SyncResult(kind="incremental")
        start = self.store.cursor()

        if not start:
            run_id = self.store.start_run("incremental")
            self.store.finish_run(run_id, status="skipped", error_class="no_cursor")
            res.status, res.error_class = "skipped", "no_cursor"
            res.elapsed = time.time() - t0
            return res

        run_id = self.store.start_run("incremental")
        try:
            token = start
            while True:
                page = self._retrying(
                    lambda: self.api.list_changes(token, page_size=self.page_size))
                res.pages += 1

                upserts: list[dict] = []
                removals: list[str] = []
                for change in page.get("changes", []):
                    self._triage(change, upserts, removals, res)

                # One transaction per page for the data...
                if upserts or removals:
                    self.store.begin()
                    for raw in upserts:
                        f = parse_file(raw)
                        if f is None:
                            continue
                        if self.store.upsert_file(f) == "added":
                            res.added += 1
                        else:
                            res.updated += 1
                    for file_id in removals:
                        if self.store.tombstone_file(file_id):
                            res.deleted += 1
                    self.store.commit()

                # ...then, and only then, the cursor for that page.
                next_token = page.get("nextPageToken")
                new_start = page.get("newStartPageToken")
                self.store.begin()
                self.store.advance_cursor(next_token or new_start or token)
                self.store.commit()

                if not next_token:
                    break
                token = next_token

            self.store.begin()
            res.pruned = self.store.prune()
            self.store.commit()

        except TransportError as exc:
            if exc.error_class == "stale_page_token" and allow_resync:
                # Documented recovery, bounded to one resync per run. res.resynced
                # is set BEFORE the recursive call so the finally block below does
                # not overwrite this run's recorded status with the default 'ok'
                # on the way out -- `return` inside `except` still runs `finally`.
                res.resynced = True
                self.store.abort()
                self.store.begin(); self.store.clear_cursor(); self.store.commit()
                self.store.finish_run(run_id, status="resync", error_class="stale_page_token")
                out = self.initial_sync(kind="resync")
                out.resynced = True
                out.elapsed = time.time() - t0
                return out
            res.status, res.error_class = "error", exc.error_class
            self.store.abort()
            self.store.begin(); self.store.record_failure(exc.error_class); self.store.commit()
        except Exception:
            res.status, res.error_class = "error", "internal"
            self.store.abort()
            self.store.begin(); self.store.record_failure("internal"); self.store.commit()
            raise
        finally:
            res.elapsed = time.time() - t0
            if not res.resynced:
                self.store.finish_run(run_id, status=res.status, added=res.added,
                                      updated=res.updated, deleted=res.deleted,
                                      pruned=res.pruned, excluded=res.excluded,
                                      error_class=res.error_class)
        return res

    def _triage(self, change: dict, upserts: list[dict], removals: list[str],
                res: SyncResult) -> None:
        """Sort one change record into upsert, removal, or ignored."""
        if change.get("changeType") == "drive":
            # A change to a shared drive itself, not to a file. Never indexed.
            res.excluded += 1
            return
        file_id = change.get("fileId")
        raw = change.get("file") or {}
        if change.get("removed") or not raw:
            # `removed` covers deletion and loss of access alike; both mean the
            # account can no longer see it, which is what the index records.
            if file_id:
                removals.append(file_id)
            return
        if not self._included(raw) or change.get("driveId"):
            # A file that moved into a shared drive leaves this layer's corpus.
            # Excluding it from future writes is not enough -- the row already
            # in the index would otherwise go stale forever.
            res.excluded += 1
            if file_id:
                removals.append(file_id)
            return
        upserts.append(raw)

    # -- explicit re-check ------------------------------------------------
    def refresh_files(self, file_ids: Iterable[str]) -> SyncResult:
        """Re-fetch specific files now.

        This is the plan's "operator explicitly asks for a re-check" path, and
        the one place where concurrency buys anything: each id is a separate
        latency-bound GET. Workers fetch; the calling thread writes. The SQLite
        connection is not thread-safe and never leaves this thread.
        """
        t0 = time.time()
        res = SyncResult(kind="recheck")
        ids = [i for i in file_ids if i]
        run_id = self.store.start_run("recheck")
        try:
            if ids:
                if self.concurrency == 1 or len(ids) == 1:
                    payloads = [self._get_one(i) for i in ids]
                else:
                    with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                        payloads = list(pool.map(self._get_one, ids))
                self.store.begin()
                for file_id, raw in zip(ids, payloads):
                    if raw is None:
                        if self.store.tombstone_file(file_id):
                            res.deleted += 1
                        continue
                    if not self._included(raw):
                        res.excluded += 1
                        continue
                    f = parse_file(raw)
                    if f is None:
                        continue
                    if self.store.upsert_file(f) == "added":
                        res.added += 1
                    else:
                        res.updated += 1
                self.store.commit()
        except TransportError as exc:
            res.status, res.error_class = "error", exc.error_class
            self.store.abort()
        finally:
            res.elapsed = time.time() - t0
            self.store.finish_run(run_id, status=res.status, added=res.added,
                                  updated=res.updated, deleted=res.deleted,
                                  excluded=res.excluded, error_class=res.error_class)
        return res

    def _get_one(self, file_id: str) -> dict | None:
        try:
            return self._retrying(lambda: self.api.get_file(file_id))
        except TransportError as exc:
            if exc.error_class == "not_found":
                return None  # deleted between listing and fetching
            raise

    # -- entry point ------------------------------------------------------
    def sync(self) -> SyncResult:
        """What the timer calls: incremental once a full listing has finished,
        otherwise keep listing.

        Dispatching on the cursor alone would be wrong: the cursor is captured
        before the listing starts, so a bounded or interrupted load leaves one
        behind and the remainder would never be indexed.
        """
        if self.store.cursor() and self.store.initial_complete():
            return self.incremental_sync()
        return self.initial_sync()


class _BudgetExceeded(Exception):
    """Internal: a bounded listing outran its page budget."""
