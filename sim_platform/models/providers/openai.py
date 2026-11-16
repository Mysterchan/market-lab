"""OpenAI Responses v1 HTTP adapter; nonstreaming, no built-in retries.

Uses standard-library asynchronous TLS, not owner threads or a hidden SDK retry.
Fixed official endpoint prevents sending credentials to user-supplied URLs.
"""

from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
import json
import ssl
from typing import Any, Dict, List, Optional

from sim_platform.domain.json_values import encode, copy_json
from sim_platform.domain.models import ContractError
from sim_platform.models.contracts import ModelPayload, ModelProfile, ProviderError, ReceivedHook

# Largest provider response body accepted, in bytes.
MAX_RESPONSE = 65536
# A Retry-After hint is clamped to at most this many seconds.
MAX_RETRY_AFTER_SECONDS = 300
# Bytes read per chunk, and the stream reader's line limit.
READ_CHUNK_BYTES = 8192
# More response header lines than this is treated as a transport error.
MAX_HEADER_LINES = 100
# How long close() waits for the TLS connection to shut down.
CLOSE_WAIT_SECONDS = 0.5

# A parsed reply: 'output' (the response items) and 'usage'.
ModelReply = Dict[str, Any]


def retry_after(value: Optional[str]) -> Optional[str]:
    """Convert a Retry-After header (seconds or HTTP date) to clamped decimal seconds."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    if not __import__('math').isfinite(seconds):
        return None
    return str(min(MAX_RETRY_AFTER_SECONDS, max(0, seconds)))


def decode_json(status: int, headers: Dict[str, str], body: bytes) -> Dict[str, Any]:
    """Classify an HTTP reply: return its JSON object, or raise ProviderError with a code."""
    try:
        data = copy_json(json.loads(body))
    except (ValueError, UnicodeError, ContractError):
        raise ProviderError('FORMAT_ERROR') from None
    if status != 200:
        error = data.get('error') if isinstance(data, dict) else None
        code = error.get('code') if isinstance(error, dict) else None
        if status in (401, 403):
            raise ProviderError('AUTH_FAILED')
        if status == 429:
            raise ProviderError('RATE_LIMIT', retry_after(headers.get('retry-after')))
        if status >= 500 or status == 408:
            raise ProviderError('PROVIDER_TEMPORARY')
        raise ProviderError(
            'CONTEXT_TOO_LONG' if code == 'context_length_exceeded' else 'MODEL_UNSUPPORTED'
        )
    if not isinstance(data, dict):
        raise ProviderError('FORMAT_ERROR')
    return data


def responses_reply(data: Dict[str, Any]) -> ModelReply:
    """Keep a completed Responses body's output items, usage and served model."""
    if data.get('status') != 'completed' or not isinstance(data.get('output'), list):
        raise ProviderError('FORMAT_ERROR')
    output = copy_json(data['output'])
    # Only detached response items/usage; raw errors or headers never leave Agent.
    reply = dict(output=output, usage=copy_json(data.get('usage', {})))
    if isinstance(data.get('model'), str):
        # The dated model version that served the reply, kept for reproducibility.
        reply['model'] = data['model']
    return reply


def decode_response(status: int, headers: Dict[str, str], body: bytes) -> ModelReply:
    """Classify a Responses HTTP reply: return its output, or raise ProviderError."""
    return responses_reply(decode_json(status, headers, body))


async def read_body(reader: asyncio.StreamReader, headers: Dict[str, str]) -> bytes:
    """Read a chunked, sized or close-delimited body, refusing more than MAX_RESPONSE."""
    chunks: List[bytes] = []
    size = 0
    if headers.get('transfer-encoding', '').lower() == 'chunked':
        while True:
            line = await reader.readline()
            count = int(line.split(b';')[0].strip(), 16)
            if count == 0:
                break
            size += count
            if size > MAX_RESPONSE:
                raise ProviderError('FORMAT_ERROR')
            chunks.append(await reader.readexactly(count))
            if await reader.readexactly(2) != b'\r\n':
                raise ProviderError('TRANSPORT_ERROR')
        return b''.join(chunks)
    length = headers.get('content-length')
    if length is not None:
        count = int(length)
        if not 0 <= count <= MAX_RESPONSE:
            raise ProviderError('FORMAT_ERROR')
        return await reader.readexactly(count)
    while True:
        chunk = await reader.read(min(READ_CHUNK_BYTES, MAX_RESPONSE + 1 - size))
        if not chunk:
            return b''.join(chunks)
        size += len(chunk)
        if size > MAX_RESPONSE:
            raise ProviderError('FORMAT_ERROR')
        chunks.append(chunk)


async def post_json(
    host: str,
    port: int,
    path: str,
    payload: Dict[str, Any],
    *,
    tls: bool,
    secret: str = '',
    received: Optional[ReceivedHook] = None,
) -> Dict[str, Any]:
    """POST one JSON body over HTTP/1.1 and return the reply's JSON object.

    ``secret``, when given, is sent as a Bearer token. ``received`` fires once the raw
    body has arrived, before it is decoded. One connection per request, closed after.
    """
    reader, writer = await asyncio.open_connection(
        host,
        port,
        ssl=ssl.create_default_context() if tls else None,
        server_hostname=host if tls else None,
        limit=READ_CHUNK_BYTES,
    )
    try:
        body = encode(payload).encode()
        authorization = 'Authorization: Bearer ' + secret + '\r\n' if secret else ''
        header = (
            'POST ' + path + ' HTTP/1.1\r\nHost: ' + host + '\r\n'
            'Content-Type: application/json\r\nConnection: close\r\n'
            + authorization
            + 'Content-Length: '
            + str(len(body))
            + '\r\n\r\n'
        )
        writer.write(header.encode() + body)
        await writer.drain()
        status_line = await reader.readline()
        status = int(status_line.split()[1])
        headers: Dict[str, str] = {}
        for _ in range(MAX_HEADER_LINES):
            line = await reader.readline()
            if line == b'\r\n':
                break
            key, value = line.decode('ascii').split(':', 1)
            if key.lower() in headers:
                raise ProviderError('TRANSPORT_ERROR')
            headers[key.lower()] = value.strip()
        else:
            raise ProviderError('TRANSPORT_ERROR')
        raw = await read_body(reader, headers)
        if received is not None:
            received()  # Complete raw body receipt, before JSON decode/validation.
        return decode_json(status, headers, raw)
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), CLOSE_WAIT_SECONDS)
        except (asyncio.TimeoutError, OSError):
            pass


class OpenAIResponses:
    """Stateless adapter for the OpenAI Responses API; one TLS connection per request.

    Runs in the Agent process. It never retries: Runtime decides attempts.
    """

    async def _send(
        self, payload: Dict[str, Any], secret: str, received: Optional[ReceivedHook] = None
    ) -> ModelReply:
        """POST one request and read the reply; ``received`` fires after the raw body."""
        data = await post_json(
            'api.openai.com',
            443,
            '/v1/responses',
            payload,
            tls=True,
            secret=secret,
            received=received,
        )
        return responses_reply(data)

    async def request(self, profile: ModelProfile, value: ModelPayload, secret: str) -> ModelReply:
        return await self.request_received(profile, value, secret, None)

    async def request_received(
        self,
        profile: ModelProfile,
        value: ModelPayload,
        secret: str,
        received: Optional[ReceivedHook],
    ) -> ModelReply:
        """Add the profile's model settings to the payload and send it within its timeout."""
        payload = dict(
            value,
            model=profile.model,
            store=False,
            stream=False,
            truncation='disabled',
            max_output_tokens=profile.max_output_tokens,
        )
        if profile.temperature is not None:
            payload['temperature'] = profile.temperature
        try:
            return await asyncio.wait_for(
                self._send(payload, secret, received), profile.network_timeout_seconds
            )
        except ProviderError:
            raise
        except asyncio.TimeoutError:
            raise ProviderError('TRANSPORT_ERROR') from None
        except (OSError, ValueError, IndexError, asyncio.IncompleteReadError):
            raise ProviderError('TRANSPORT_ERROR') from None
