"""OpenAI-compatible Chat Completions adapter for model servers on premises.

vLLM, Ollama, llama.cpp and LM Studio serve ``POST {base_url}/chat/completions``. The
Agent loop speaks the Responses format, so this adapter translates each request into
chat messages and each reply back into Responses output items. It runs in the Agent
process, imports only the standard library and never retries: Runtime decides attempts.

The profile's ``base_url`` must name this machine or a private network (checked by
``ModelProfile``), so prompts, market data and replies never leave the premises.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Set

from sim_platform.domain.json_values import copy_json
from sim_platform.models.contracts import (
    ModelPayload,
    ModelProfile,
    ProviderError,
    ReceivedHook,
    local_server,
)
from .openai import ModelReply, post_json


def _text(content: Any) -> str:
    """Plain text of a Responses message content (text or a list of text parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(
            part.get('text', '')
            for part in content
            if isinstance(part, dict) and isinstance(part.get('text'), str)
        )
    return ''


def chat_messages(payload: ModelPayload) -> List[Dict[str, Any]]:
    """Translate a Responses request's instructions and input items into chat messages.

    Consecutive function calls become one assistant message with ``tool_calls``;
    function outputs become ``tool`` messages; reasoning items are dropped because chat
    servers cannot read them back.
    """
    messages: List[Dict[str, Any]] = []
    if payload.get('instructions'):
        messages.append(dict(role='system', content=payload['instructions']))
    items = payload['input']
    if isinstance(items, str):
        return messages + [dict(role='user', content=items)]
    for item in items:
        kind = item.get('type')
        if kind == 'function_call':
            call = dict(
                id=item['call_id'],
                type='function',
                function=dict(name=item['name'], arguments=item['arguments']),
            )
            last = messages[-1] if messages else None
            if last is not None and last['role'] == 'assistant' and 'tool_calls' in last:
                last['tool_calls'].append(call)
            else:
                messages.append(dict(role='assistant', content=None, tool_calls=[call]))
        elif kind == 'function_call_output':
            output = item.get('output')
            messages.append(
                dict(
                    role='tool',
                    tool_call_id=item['call_id'],
                    content=output if isinstance(output, str) else json.dumps(output),
                )
            )
        elif kind == 'reasoning':
            continue
        else:
            role = item.get('role', 'user')
            role = 'system' if role == 'developer' else role
            messages.append(dict(role=role, content=_text(item.get('content'))))
    return messages


def chat_tools(payload: ModelPayload) -> List[Dict[str, Any]]:
    """Translate Responses function tools into chat ``tools`` entries."""
    return [
        dict(
            type='function',
            function=dict(
                name=tool['name'],
                description=tool.get('description', ''),
                parameters=tool['parameters'],
            ),
        )
        for tool in payload.get('tools') or []
    ]


class OpenAICompatibleChat:
    """Stateless apart from call identities; one instance per experiment.

    Local servers sometimes omit or repeat tool call IDs. The Agent loop needs every
    call ID to be unique within a run, so a missing or repeated ID is replaced with a
    generated one; the replacement is what the conversation records and sends back.
    """

    def __init__(self) -> None:
        self._issued: Set[str] = set()
        self._count = 0

    def _call_id(self, value: Any) -> str:
        if isinstance(value, str) and value and value not in self._issued:
            identity = value
        else:
            self._count += 1
            identity = f'call-local-{self._count}'
            while identity in self._issued:
                self._count += 1
                identity = f'call-local-{self._count}'
        self._issued.add(identity)
        return identity

    def body(self, profile: ModelProfile, value: ModelPayload) -> Dict[str, Any]:
        """The Chat Completions request for a Responses payload and profile."""
        body: Dict[str, Any] = dict(
            model=profile.model,
            messages=chat_messages(value),
            stream=False,
            max_tokens=profile.max_output_tokens,
        )
        tools = chat_tools(value)
        if tools:
            body['tools'] = tools
            if 'parallel_tool_calls' in value:
                body['parallel_tool_calls'] = value['parallel_tool_calls']
        if profile.temperature is not None:
            body['temperature'] = profile.temperature
        return body

    def reply(self, data: Dict[str, Any]) -> ModelReply:
        """Translate a Chat Completions body into Responses output items and usage."""
        choices = data.get('choices')
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError('FORMAT_ERROR')
        message = choices[0].get('message')
        if not isinstance(message, dict):
            raise ProviderError('FORMAT_ERROR')
        output: List[Dict[str, Any]] = []
        text = message.get('content')
        if isinstance(text, str) and text.strip():
            output.append(
                dict(
                    type='message',
                    role='assistant',
                    content=[dict(type='output_text', text=text)],
                )
            )
        for call in message.get('tool_calls') or []:
            function = call.get('function') if isinstance(call, dict) else None
            if not isinstance(function, dict) or not isinstance(function.get('name'), str):
                raise ProviderError('FORMAT_ERROR')
            arguments = function.get('arguments')
            if isinstance(arguments, dict):
                arguments = json.dumps(arguments)  # Ollama may send an object.
            if not isinstance(arguments, str):
                raise ProviderError('FORMAT_ERROR')
            output.append(
                dict(
                    type='function_call',
                    call_id=self._call_id(call.get('id')),
                    name=function['name'],
                    arguments=arguments,
                )
            )
        raw = data.get('usage')
        usage: Dict[str, Any] = raw if isinstance(raw, dict) else {}
        counts = {
            target: usage[source]
            for (source, target) in (
                ('prompt_tokens', 'input_tokens'),
                ('completion_tokens', 'output_tokens'),
                ('total_tokens', 'total_tokens'),
            )
            if type(usage.get(source)) is int
        }
        reply: ModelReply = dict(output=copy_json(output), usage=counts)
        if isinstance(data.get('model'), str):
            reply['model'] = data['model']
        return reply

    async def request(self, profile: ModelProfile, value: ModelPayload, secret: str) -> ModelReply:
        return await self.request_received(profile, value, secret, None)

    async def request_received(
        self,
        profile: ModelProfile,
        value: ModelPayload,
        secret: str,
        received: Optional[ReceivedHook],
    ) -> ModelReply:
        """Send one Chat Completions request to the profile's local server."""
        if profile.base_url is None:
            raise ProviderError('MODEL_UNSUPPORTED')
        (tls, host, port, path) = local_server(profile.base_url)
        try:
            data = await asyncio.wait_for(
                post_json(
                    host,
                    port,
                    path + '/chat/completions',
                    self.body(profile, value),
                    tls=tls,
                    secret=secret,
                    received=received,
                ),
                profile.network_timeout_seconds,
            )
        except ProviderError:
            raise
        except asyncio.TimeoutError:
            raise ProviderError('TRANSPORT_ERROR') from None
        except (OSError, ValueError, IndexError, asyncio.IncompleteReadError):
            raise ProviderError('TRANSPORT_ERROR') from None
        return self.reply(data)


def default_provider(profile: ModelProfile) -> Any:
    """The built-in adapter for a profile's provider name."""
    if profile.provider == 'openai-compatible':
        return OpenAICompatibleChat()
    from .openai import OpenAIResponses

    return OpenAIResponses()
