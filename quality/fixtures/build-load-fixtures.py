#!/usr/bin/env python3
"""Build synthetic, repeatable load RRPairs without credentials or captured user data."""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import uuid
from urllib.parse import parse_qs, urlsplit


def pair(service, direction, host, port, method, uri, request, response, status=200):
    def body(value):
        return base64.b64encode(json.dumps(value, separators=(',', ':')).encode()).decode() if value is not None else ''
    return {
        'msgType': 'rrpair', 'resource': service, 'ts': datetime.now(timezone.utc).isoformat(),
        'uuid': base64.b64encode(uuid.uuid4().bytes).decode(), 'direction': direction,
        'cluster': 'dev-decoy', 'namespace': 'banking-replay', 'service': service,
        'l7protocol': 'https' if port == 443 else 'http', 'isTls': port == 443, 'tech': 'HTTP,JSON', 'command': method,
        'location': uri.split('?')[0], 'status': str(status), 'duration': 1,
        'tags': {'k8sAppLabel': service, 'k8sAppPodNamespace': 'banking-replay',
                 'k8sClusterName': 'dev-decoy', 'proxyLocation': direction.lower(), 'source': 'synthetic'},
        'http': {
            'req': {'url': uri.split('?')[0], 'uri': uri, 'host': host, 'method': method,
                    'version': 'HTTP/1.1', 'headers': {'Content-Type': ['application/json']},
                    'bodyBase64': body(request), 'queryParams': parse_qs(urlsplit(uri).query, keep_blank_values=True)},
            'res': {'statusCode': status, 'statusMessage': 'OK' if status == 200 else 'Unauthorized',
                    'contentType': 'application/json', 'headers': {'Content-Type': ['application/json']},
                    'bodyBase64': body(response)},
        },
        'netinfo': {'downstream': {'ipAddress': '127.0.0.1', 'port': 40000},
                    'upstream': {'hostname': host, 'ipAddress': '127.0.0.1', 'port': port},
                    'direction': 'INGRESS' if direction == 'IN' else 'EGRESS'},
    }


def ai_pairs():
    system = "You are a helpful banking assistant for Apex Banking. Answer questions about the user's accounts and transactions. Be concise and professional."
    providers = [
        ('anthropic', 'Anthropic Claude', 'claude-sonnet-4-20250514', 'api.anthropic.com', '/v1/messages'),
        ('openai', 'OpenAI GPT-4o Mini', 'gpt-4o-mini', 'api.openai.com', '/v1/chat/completions'),
        ('gemini', 'Google Gemini', 'gemini-2.0-flash', 'generativelanguage.googleapis.com', '/v1beta/models/gemini-2.0-flash:generateContent?key=mock-gemini-key-served-by-speedscale-responder'),
        ('xai', 'xAI Grok', 'grok-3-mini', 'api.x.ai', '/v1/chat/completions'),
        ('openrouter', 'OpenRouter Mistral', 'mistralai/mistral-small-3.2-24b-instruct', 'openrouter.ai', '/api/v1/chat/completions'),
    ]
    pairs = []
    for question in ['Explain compound interest.', 'Explain card security.', 'Explain a savings account.']:
        content = 'User locale: en-US. Respond in the language and conventions for this locale.\n\n' + question
        results = []
        for provider, name, model, host, uri in providers:
            text = f'Demo {provider} response: {question}'
            if provider == 'anthropic':
                request = {'model': model, 'max_tokens': 1024, 'system': system, 'messages': [{'role': 'user', 'content': content}]}
                response = {'content': [{'type': 'text', 'text': text}]}
            elif provider == 'gemini':
                request = {'system_instruction': {'parts': [{'text': system}]}, 'contents': [{'role': 'user', 'parts': [{'text': content}]}]}
                response = {'candidates': [{'content': {'parts': [{'text': text}]}}]}
            else:
                request = {'model': model, 'max_tokens': 1024, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}]}
                response = {'choices': [{'message': {'role': 'assistant', 'content': text}}]}
            pairs.append(pair('banking-ai', 'OUT', host, 443, 'POST', uri, request, response))
            results.append({'provider': provider, 'name': name, 'model': model, 'message': text, 'error': None, 'durationMs': 0})
        pairs.append(pair('banking-ai', 'IN', 'banking-ai', 8080, 'POST', '/api/chat',
                          {'message': question, 'locale': 'en-US'}, {'results': results}))
    return pairs


def gateway_pairs():
    pairs = []
    for field in ['username', 'email']:
        for suffix in ['alpha', 'beta']:
            value = f'load-fixture-{suffix}' + ('@example.invalid' if field == 'email' else '')
            uri = f'/api/users/check-{field}?{field}={value}'
            response = {'success': True, 'available': True, 'message': f'{field.title()} is available'}
            pairs.append(pair('banking-gateway', 'OUT', 'banking-user', 80, 'GET', uri, None, response))
            pairs.append(pair('banking-gateway', 'IN', 'banking-gateway', 8080, 'GET', uri, None, response))
    pairs.append(pair('banking-gateway', 'IN', 'banking-gateway', 8080, 'GET', '/api/accounts', None, None, 401))
    return pairs


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    for name, pairs in [('ai', ai_pairs()), ('gateway', gateway_pairs())]:
        directory = args.destination / name
        directory.mkdir(parents=True, exist_ok=True)
        # Preserve a request order without replaying a captured day's timing gaps.
        start = datetime.now(timezone.utc) - timedelta(seconds=len(pairs))
        for index, rrpair in enumerate(pairs):
            rrpair['ts'] = (start + timedelta(milliseconds=index)).isoformat()
            (directory / f'{index:03d}.json').write_text(json.dumps(rrpair, indent=2) + '\n')
        print(f'{name}: {len(pairs)} synthetic RRPairs in {directory}')
