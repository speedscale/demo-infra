#!/usr/bin/env python3
"""Wait for an authenticated read before starting a measured replay."""
import argparse
from pathlib import Path
import re
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from urllib.error import HTTPError, URLError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


def select_probe(snapshot, path):
    for file in sorted(snapshot.rglob('*.md')):
        text = file.read_text()
        if not re.search(r'^direction:\s+IN\s*$', text, re.M):
            continue
        request, separator, response = text.partition('### RESPONSE ###')
        method = re.search(r'^GET\s+(\S+)', request, re.M)
        authorization = re.search(r'^Authorization:\s*(Bearer [^\r\n]+)', request, re.M | re.I)
        status = re.search(r'^HTTP/\d(?:\.\d)?\s+(\d{3})', response, re.M)
        if not separator or not method or not authorization or not status:
            continue
        url = urlsplit(method[1])
        if url.path == path and 200 <= int(status[1]) < 300:
            return url, authorization[1].strip(), int(status[1])
    raise ValueError('snapshot has no successful authenticated GET for the readiness path')


def wait(snapshot, target, path, timeout, deadline, interval):
    url, authorization, expected = select_probe(snapshot, path)
    destination = urlsplit(target)
    if destination.scheme not in ('http', 'https') or not destination.hostname:
        raise ValueError('readiness target must be an HTTP URL')
    request = Request(urlunsplit((destination.scheme, destination.netloc, url.path, url.query, '')),
                      headers={'Authorization': authorization})
    opener = build_opener(ProxyHandler({}), NoRedirect())
    expires = time.monotonic() + deadline
    consecutive = 0
    attempt = 0
    while time.monotonic() < expires:
        attempt += 1
        try:
            with opener.open(request, timeout=min(timeout, max(.001, expires - time.monotonic()))) as response:
                response.read()
                status = response.status
        except HTTPError as error:
            status = error.code
            error.close()
        except (URLError, TimeoutError, OSError):
            status = 0
        print(f'HTTP readiness attempt {attempt}: status={status}', flush=True)
        if 400 <= status < 500:
            raise ValueError(f'authenticated readiness request returned HTTP {status}')
        consecutive = consecutive + 1 if status == expected else 0
        if consecutive >= 2:
            print('Authenticated HTTP readiness passed', flush=True)
            return
        time.sleep(min(interval, max(0, expires - time.monotonic())))
    raise ValueError('authenticated HTTP readiness did not pass before its deadline')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('target')
    parser.add_argument('path')
    parser.add_argument('--timeout', type=float, default=10)
    parser.add_argument('--deadline', type=float, default=60)
    parser.add_argument('--interval', type=float, default=2)
    args = parser.parse_args()
    if args.timeout <= 0 or args.deadline <= 0 or args.interval < 0:
        parser.error('timeouts must be positive and interval must not be negative')
    try:
        wait(args.snapshot, args.target, args.path, args.timeout, args.deadline, args.interval)
    except ValueError as error:
        parser.exit(1, f'Readiness failed: {error}\n')
