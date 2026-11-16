"""Bounded model requests; credential material is never part of this contract."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from math import isfinite
from urllib.parse import urlsplit
from typing import Any, Awaitable, Callable, Dict, Optional, Protocol, Tuple

from sim_platform.domain.models import ContractError, label, integer
from sim_platform.domain.json_values import copy_json, encode

# An encoded model request payload, or a redacted model reply, may not exceed 64 KiB.
MAX_PAYLOAD_BYTES = 65536
# Longest model name or credential reference.
REFERENCE_MAX_LENGTH = 128
# Upper bound on one provider call's network wall time.
MAX_NETWORK_TIMEOUT_SECONDS = 300
# Largest output-token budget a profile may request.
MAX_OUTPUT_TOKENS = 16384
# Most function tools one request may describe.
# Functions offered to the model in one request: every platform tool plus local tools.
MAX_TOOLS = 32

# Providers a ModelProfile may name. 'openai-responses' is the hosted OpenAI Responses
# API; 'openai-compatible' is a Chat Completions server on this machine or the local
# network (vLLM, Ollama, llama.cpp, LM Studio), so prompts and data stay on premises.
PROVIDERS = ('openai-responses', 'openai-compatible')
# Host names accepted for a local server besides loopback and private IP literals.
LOCAL_HOST_NAMES = ('localhost',)


def local_server(url: str) -> Tuple[bool, str, int, str]:
    """Return (tls, host, port, path) for a local model server URL, or raise.

    The URL must be http or https with a host on this machine or a private network:
    ``localhost``, a loopback, private or link-local IP address. Public hosts are
    refused so a local profile can never send research data off premises.
    """
    if not isinstance(url, str) or len(url) > 512:
        raise ContractError('base_url must be text of at most 512 characters')
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ContractError('base_url must be an http or https URL')
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ContractError('base_url may not carry a query, fragment or user name')
    host = parts.hostname
    if host not in LOCAL_HOST_NAMES:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise ContractError('base_url host must be localhost or a private IP address') from None
        if not (address.is_loopback or address.is_private or address.is_link_local):
            raise ContractError('base_url host must be on this machine or a private network')
    try:
        port = parts.port or (443 if parts.scheme == 'https' else 80)
    except ValueError:
        raise ContractError('base_url port is invalid') from None
    path = parts.path.rstrip('/') or '/v1'
    return (parts.scheme == 'https', host, port, path)


# A validated model request: 'input' (text or a list of items), and optionally
# 'instructions', 'tools' and 'parallel_tool_calls'.
ModelPayload = Dict[str, Any]


@dataclass(frozen=True)
class ModelProfile:
    """Which provider and model an Agent uses, and the credential it is stored under.

    Holds no secret: ``credential_ref`` names a CredentialStore entry. A local
    ``openai-compatible`` profile sets ``base_url`` (for example
    ``http://127.0.0.1:11434/v1`` for Ollama or ``http://localhost:8000/v1`` for vLLM)
    and may leave ``credential_ref`` empty when the server needs no key.
    """

    provider: str
    model: str
    credential_ref: str = ''
    network_timeout_seconds: float = 60
    max_output_tokens: int = 4096
    # Sampling temperature sent to the provider; None leaves the provider's default.
    # Recording it lets a run be repeated with the same settings.
    temperature: Optional[float] = None
    # Server root of an 'openai-compatible' provider; None for the hosted provider.
    base_url: Optional[str] = None

    def __post_init__(self) -> None:
        if self.provider not in PROVIDERS:
            raise ContractError('unsupported model provider')
        label(self.model, 'model', REFERENCE_MAX_LENGTH)
        if self.provider == 'openai-compatible':
            if self.base_url is None:
                raise ContractError('an openai-compatible profile needs base_url')
            local_server(self.base_url)
            if self.credential_ref:
                label(self.credential_ref, 'credential reference', REFERENCE_MAX_LENGTH)
        else:
            if self.base_url is not None:
                raise ContractError('base_url is only for an openai-compatible provider')
            label(self.credential_ref, 'credential reference', REFERENCE_MAX_LENGTH)
        if (
            type(self.network_timeout_seconds) not in (int, float)
            or not isfinite(self.network_timeout_seconds)
            or not 0 < self.network_timeout_seconds <= MAX_NETWORK_TIMEOUT_SECONDS
        ):
            raise ContractError('invalid network timeout')
        integer(self.max_output_tokens, 'output tokens', 1)
        if self.max_output_tokens > MAX_OUTPUT_TOKENS:
            raise ContractError('output exceeds supported bound')
        if self.temperature is not None and (
            type(self.temperature) not in (int, float)
            or not isfinite(self.temperature)
            or not 0 <= self.temperature <= 2
        ):
            raise ContractError('temperature must be a number from 0 to 2')


class ProviderError(ContractError):
    """A classified model failure; ``code`` (for example 'RATE_LIMIT') is safe to log.

    ``retry_after_seconds`` is the provider's suggested wait as decimal text, or None.
    """

    def __init__(self, code: str, retry_after_seconds: Optional[str] = None) -> None:
        self.code, self.retry_after_seconds = code, retry_after_seconds
        super().__init__(code)  # Never copy raw provider error/body/header/key.


class ModelProvider(Protocol):
    """A model adapter run in the Agent process (for example OpenAIResponses).

    Adapters may also provide ``request_received(profile, payload, secret, received)``,
    which calls ``received()`` once the complete raw reply has arrived.
    """

    def request(
        self, profile: ModelProfile, value: ModelPayload, secret: str
    ) -> Awaitable[Any]: ...


# Called once when the complete raw reply has arrived, before it is parsed.
ReceivedHook = Callable[[], None]


def validate_input(value: Any) -> ModelPayload:
    """Return a detached copy of a model request, or raise if it is unsupported or too large."""
    value = copy_json(value)
    fields = {'input', 'instructions', 'tools', 'parallel_tool_calls'}
    if type(value) is not dict or set(value) - fields or 'input' not in value:
        raise ContractError('model payload requires input and supported model options')
    if type(value['input']) not in (str, list):
        raise ContractError('unsupported model input')
    if len(encode(value).encode()) > MAX_PAYLOAD_BYTES:
        raise ProviderError('CONTEXT_TOO_LONG')
    if 'instructions' in value and type(value['instructions']) is not str:
        raise ContractError('model instructions must be text')
    if 'tools' in value:
        if type(value['tools']) is not list or len(value['tools']) > MAX_TOOLS:
            raise ContractError('model tools must be a bounded list of functions')
        for tool in value['tools']:
            if (
                type(tool) is not dict
                or tool.get('type') != 'function'
                or not isinstance(tool.get('name'), str)
                or type(tool.get('parameters')) is not dict
            ):
                raise ContractError('model tool must describe a platform function')
    if 'parallel_tool_calls' in value and type(value['parallel_tool_calls']) is not bool:
        raise ContractError('parallel_tool_calls must be boolean')
    return value
