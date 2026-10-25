"""Graph runs — the Celery task behind ``POST /runs``.

The message carries the run id only (``graphwright.contract``). The task claims
the ``run`` row, loads and compiles the graph, redeems the run's credential
reference through the configured provider, wraps every credential value in a
``Secret``, and steps a ``GraphSession`` until it reaches a terminal node, fails,
or the run is cancelled. Values that expire (2FA codes) are not fetched here —
the graph's requests fetch them when an action needs them.

Nothing that reaches Redis, Postgres or the log escapes the run's ``Redactor``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any
from urllib.parse import urlparse

from graphwright import evidence
from graphwright.catalog import GraphInvalid, GraphNotFound, InputsInvalid, build_inputs, load_graph
from graphwright.config import settings
from graphwright.contract import TASK_RUN_GRAPH
from graphwright.credentials import Credential, CredentialRejected, get_provider
from graphwright.engine.compiler import CompiledGraph
from graphwright.engine.requests import RequestServices
from graphwright.engine.runtime import Event, GraphSession
from graphwright.engine.secrets import Redactor, SecretInputs
from graphwright.queue.app import celery
from graphwright.storage import cache, db
from graphwright.worker.hooks import get_loop_and_pool

log = logging.getLogger(__name__)

# Event kinds kept in run_event — the decision trail, not every observation.
DURABLE_EVENTS = frozenset({
    "goto", "move", "jump", "resync", "request", "fill", "select", "extract",
    "retry", "on_fail", "failure", "terminal", "cancelled",
})
_CANCEL_CHECK_EVERY = 1  # steps between cancellation checks (one cheap SELECT)


class RunRejected(Exception):
    """The run can't start (graph missing/invalid, credential refused, inputs).
    ``code`` is stored in ``run.error_type``; the message carries no value."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GraphRunFailed(Exception):
    """The graph stopped short of a terminal node. ``failure`` is already redacted."""

    def __init__(self, failure: dict[str, Any], status: str) -> None:
        super().__init__(f"{failure.get('type')}: {failure.get('message')}")
        self.failure = failure
        self.status = status


class RunCancelled(Exception):
    pass


def _domain(graph: CompiledGraph) -> str:
    url = graph.workflow.entry_url or ""
    return (urlparse(url).hostname or "") if "{" not in url else ""


async def _drive(
    pool: Any, graph: CompiledGraph, inputs: SecretInputs, redactor: Redactor, *,
    credential: Credential | None, on_event: Any, cancelled: Any, run_id: uuid.UUID, attempt: int,
) -> dict[str, Any]:
    async with pool.context_for(
        _domain(graph),
        persist_state=False,
        client_certificates=credential.client_certificates() if credential else None,
        authenticated=credential is not None,
    ) as context:
        page = await context.new_page()
        services = RequestServices(totp=credential.totp if credential else None, on_secret=redactor.add)
        session = GraphSession(graph, page, inputs, redactor=redactor, on_event=on_event, services=services)
        try:
            await session.start()
            steps = 0
            while True:
                result = await session.step()
                steps += 1
                if result.status in ("done", "failed", "needs_repair"):
                    break
                if steps % _CANCEL_CHECK_EVERY == 0 and await cancelled():
                    raise RunCancelled()
        except RunCancelled:
            raise
        except Exception as exc:
            # Not an engine failure (those end the loop above): a browser crash, a
            # navigation error… The page may still show why.
            if evidence.wanted("failed"):
                exc.gw_evidence = await evidence.capture(  # type: ignore[attr-defined]
                    session, run_id, attempt, reason="error",
                    failure={"type": type(exc).__name__, "message": redactor.text(str(exc))},
                )
            raise
        # The page is still open here — this is the only place evidence can come from.
        if session.status != "done":
            failure = dict(session.failure or {"type": "unknown", "message": session.status})
            if evidence.wanted("failed"):
                failure["evidence"] = await evidence.capture(session, run_id, attempt, reason="failed", failure=failure)
            raise GraphRunFailed(failure, session.status)
        result = dict(session.result or {})
        outcome = "failure" if result.get("terminal") == "failure" else "success"
        if evidence.wanted(outcome):
            result["evidence"] = await evidence.capture(session, run_id, attempt, reason=outcome)
        return result


@celery.task(bind=True, name=TASK_RUN_GRAPH)
def run_graph(self, run_id: str) -> dict[str, Any]:
    loop, pool = get_loop_and_pool()
    rid = uuid.UUID(run_id)
    claimed = loop.run_until_complete(
        db.claim_run(rid, celery_task_id=self.request.id, browser_profile=settings.browser_default_profile)
    )
    if claimed is None:
        log.info("run %s: nothing to do (finished, cancelled or unknown)", run_id)
        return {"run_id": run_id, "skipped": True}

    redactor = Redactor()
    steps = 0
    node: str | None = None
    seq = 0
    durable: list[dict[str, Any]] = []

    def progress(status: str, **extra: Any) -> None:
        cache.set_run_progress(run_id, redactor({
            "status": status, "graph": claimed.graph, "node": node, "steps": steps, "attempt": claimed.attempt, **extra,
        }))

    pending: list[asyncio.Task[None]] = []

    def on_event(ev: Event) -> None:
        nonlocal steps, node, seq
        seq += 1
        if ev.kind == "move":
            steps += 1
            node = ev.data.get("to")
            # on_event is sync and runs inside the session's loop; the write is awaited
            # (and its errors surfaced) by _flush before the run's outcome is stored.
            pending.append(loop.create_task(db.update_run_progress(rid, steps_completed=steps, node=node)))
        if ev.kind in DURABLE_EVENTS:
            data = {"seq": seq, **ev.as_dict()}
            durable.append(data)
            progress("running", event={k: data[k] for k in ("kind", "node", "ts") if k in data})

    async def cancelled() -> bool:
        await _flush()
        return (await db.run_status(rid)) == "cancelled"

    async def _flush() -> None:
        while pending:
            task = pending.pop(0)
            try:
                await task
            except Exception:  # noqa: BLE001 — progress is best effort; the outcome write is not
                log.warning("run %s: progress write failed", run_id, exc_info=True)

    progress("running")
    credential: Credential | None = None
    try:
        try:
            graph = load_graph(claimed.graph)
        except GraphNotFound as exc:
            raise RunRejected("graph_not_found", str(exc)) from exc
        except GraphInvalid as exc:
            raise RunRejected("graph_invalid", str(exc)) from exc
        loop.run_until_complete(db.set_run_graph_version(rid, graph.workflow.version))

        if claimed.credential_ref:
            try:
                credential = get_provider().redeem(claimed.credential_ref, run_id=run_id)
            except CredentialRejected as exc:
                raise RunRejected(exc.code, "the credential provider did not release the credential for this run") from exc
            redactor.add(*credential.plaintexts)
            if credential.portal and credential.portal != graph.workflow.portal:
                raise RunRejected(
                    "portal_mismatch",
                    f"credential is for {credential.portal!r}, graph is for {graph.workflow.portal!r}",
                )
        try:
            inputs = build_inputs(graph.workflow, claimed.inputs, credential)
        except InputsInvalid as exc:
            raise RunRejected("missing_inputs", str(exc)) from exc

        result = loop.run_until_complete(_drive(
            pool, graph, inputs, redactor, credential=credential, on_event=on_event, cancelled=cancelled,
            run_id=rid, attempt=claimed.attempt,
        ))
    except RunCancelled:
        loop.run_until_complete(_flush())
        durable.append({"seq": seq + 1, "kind": "cancelled", "node": node})
        loop.run_until_complete(db.insert_run_events(rid, durable))
        progress("cancelled")
        return {"run_id": run_id, "status": "cancelled"}
    except BaseException as exc:
        handled = isinstance(exc, (RunRejected, GraphRunFailed))
        error_type = exc.code if isinstance(exc, RunRejected) else (
            f"graph_{exc.status}" if isinstance(exc, GraphRunFailed) else type(exc).__name__
        )
        failure = exc.failure if isinstance(exc, GraphRunFailed) else (
            {"type": type(exc).__name__, "evidence": exc.gw_evidence} if getattr(exc, "gw_evidence", None) else None
        )
        loop.run_until_complete(_flush())
        loop.run_until_complete(db.insert_run_events(rid, durable))
        loop.run_until_complete(db.mark_run_failed(
            rid, exc=exc, steps_completed=steps, scrub=redactor.text, error_type=error_type, failure=failure,
        ))
        progress("failed", error_type=error_type, error=redactor.text(str(exc)))
        if handled:
            return {"run_id": run_id, "status": "failed", "error_type": error_type}
        raise
    finally:
        credential = inputs = None  # type: ignore[assignment]  # plaintext now lives only in the Secrets

    result = redactor(result)
    loop.run_until_complete(_flush())
    loop.run_until_complete(db.insert_run_events(rid, durable))
    loop.run_until_complete(db.mark_run_completed(rid, result=result, steps_completed=steps))
    progress("completed", terminal=result.get("terminal"))
    return {"run_id": run_id, "status": "completed", "terminal": result.get("terminal")}
