"""Own reusable provider HTTP clients within one application's event-loop lifespan.

Standalone helper calls retain a scoped client. Credentials stay on individual
requests, and clients are separated by provider and timeout contract.
"""

import asyncio
from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary

import httpx
from anyio import CancelScope
from starlette.responses import StreamingResponse

from proxy.logging import debug_log

_LIFESPANS: WeakKeyDictionary = WeakKeyDictionary()


def acquire_http_client(provider: str, *, timeout: httpx.Timeout | float) -> tuple[httpx.AsyncClient, bool]:
    """Return a client and whether the caller must close it after use."""
    clients = _LIFESPANS.get(asyncio.get_running_loop())
    if clients is None:
        return httpx.AsyncClient(timeout=timeout), True
    normalized = timeout if isinstance(timeout, httpx.Timeout) else httpx.Timeout(timeout)
    key = (provider, tuple(sorted(normalized.as_dict().items())))
    client = clients.get(key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=normalized)
        clients[key] = client
    return client, False


@asynccontextmanager
async def http_client(provider: str, *, timeout: httpx.Timeout | float):
    """Borrow a lifespan client or close a standalone client on every exit path."""
    client, owned = acquire_http_client(provider, timeout=timeout)
    try:
        yield client
    finally:
        if owned:
            with CancelScope(shield=True):
                await client.aclose()


async def close_http_stream(response: httpx.Response | None, client: httpx.AsyncClient, owned: bool) -> None:
    """Finish stream cleanup even when the ASGI disconnect scope is cancelled."""
    with CancelScope(shield=True):
        try:
            if response is not None:
                await response.aclose()
        finally:
            if owned:
                await client.aclose()


class ProviderStreamingResponse(StreamingResponse):
    """Release upstream connections on ASGI send failure as well as iterator exit."""

    def __init__(self, content, *, upstream: httpx.Response, client: httpx.AsyncClient, owned: bool, **kwargs):
        super().__init__(content, **kwargs)
        self._upstream = upstream
        self._client = client
        self._owned_client = owned

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with CancelScope(shield=True):
                try:
                    close_iterator = getattr(self.body_iterator, "aclose", None)
                    if close_iterator is not None:
                        await close_iterator()
                finally:
                    await close_http_stream(self._upstream, self._client, self._owned_client)


@asynccontextmanager
async def http_client_lifespan():
    """Keep clients local to the running loop and close every pool on shutdown."""
    loop = asyncio.get_running_loop()
    if loop in _LIFESPANS:
        raise RuntimeError("Provider client lifespan is already active on this event loop")
    clients: dict = {}
    _LIFESPANS[loop] = clients
    try:
        yield
    finally:
        _LIFESPANS.pop(loop, None)
        with CancelScope(shield=True):
            outcomes = await asyncio.gather(*(client.aclose() for client in clients.values()), return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                debug_log("provider_client_close_failed", error_type=type(outcome).__name__)
