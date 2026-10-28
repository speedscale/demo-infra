"""Exercise signature and identity checks for newly generated login tokens."""
import base64
import hashlib
import hmac
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/validate-replay-jwt.py"
spec = importlib.util.spec_from_file_location("replay_jwt", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def encode(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


def token(claims, secret=b"synthetic-test-key"):
    data = encode({"alg": "HS256", "typ": "JWT"}) + "." + encode(claims)
    signature = base64.urlsafe_b64encode(hmac.new(secret, data.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return data + "." + signature


class ReplayJWTTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000
        self.claims = {"sub": "quality-alice", "userId": "7", "roles": "USER", "exp": self.now + 86400}
        self.body = {"token": token(self.claims), "id": 7, "username": "quality-alice", "roles": "USER"}

    def test_accepts_signed_current_token_for_the_returned_identity(self):
        module.verify(self.body, b"synthetic-test-key", self.now)

    def test_rejects_bad_signature_expiry_identity_and_roles(self):
        cases = [{"exp": self.now - 1}, {"exp": self.now + 365 * 86400}, {"sub": "another-user"}, {"userId": "8"}, {"roles": "ADMIN"}]
        for override in cases:
            with self.subTest(override=override):
                body = dict(self.body, token=token(dict(self.claims, **override)))
                with self.assertRaises(ValueError):
                    module.verify(body, b"synthetic-test-key", self.now)
        with self.assertRaises(ValueError):
            module.verify(self.body, b"wrong-key", self.now)

    def test_missing_successful_login_or_token_cannot_pass(self):
        with self.assertRaises(ValueError):
            module.validate([], b"synthetic-test-key", self.now)
        body = dict(self.body)
        body.pop("token")
        with self.assertRaises(ValueError):
            module.verify(body, b"synthetic-test-key", self.now)

    def test_registration_timestamp_must_match_replay_time(self):
        def response(url, status, body):
            return {"ts": datetime.fromtimestamp(self.now, timezone.utc).isoformat(), "direction": "IN", "tags": {"source": "generator"},
                    "http": {"req": {"url": url}, "res": {"statusCode": status, "bodyBase64": base64.b64encode(json.dumps(body).encode()).decode()}}}
        login = response("/api/users/login", 200, self.body)
        registration = response("/api/users/register", 201, {"createdAt": datetime.fromtimestamp(self.now, timezone.utc).isoformat()})
        self.assertEqual(1, module.validate([login, registration], b"synthetic-test-key", self.now))
        stale = response("/api/users/register", 201, {"createdAt": "2000-01-01T00:00:00Z"})
        with self.assertRaises(ValueError):
            module.validate([login, stale], b"synthetic-test-key", self.now)
