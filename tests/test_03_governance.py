"""Governance enforcement: authentication, authorization, and rate limits.

Tests per-model request rate limits, token rate limits, and cross-model
isolation.  tinyllama-test has restrictive limits (10 req/1m, 5000 tok/1m)
and tinyllama-fast has generous limits (100 req/1m, 10000 tok/1m).
"""

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

REQUEST_RATE_BURST = int(os.getenv("MAAS_RATE_LIMIT_BURST", "15"))
TOKEN_RATE_BURST = int(os.getenv("MAAS_TOKEN_RATE_BURST", "20"))


# ---------------------------------------------------------------------------
# Auth enforcement (model-agnostic -- uses model 1 path)
# ---------------------------------------------------------------------------

class TestAuthEnforcement:

    def test_no_auth_header_returns_401(
        self, maas_url, inference_path, chat_payload
    ):
        resp = requests.post(
            f"{maas_url}{inference_path}",
            headers={"Content-Type": "application/json"},
            json=chat_payload,
            verify=False,
            timeout=15,
        )
        assert resp.status_code in (401, 403)

    def test_invalid_bearer_token_returns_401(
        self, maas_url, inference_path, chat_payload
    ):
        resp = requests.post(
            f"{maas_url}{inference_path}",
            headers={
                "Authorization": "Bearer totally-fake-invalid-token",
                "Content-Type": "application/json",
            },
            json=chat_payload,
            verify=False,
            timeout=15,
        )
        assert resp.status_code in (401, 403)

    def test_empty_bearer_token_returns_401(
        self, maas_url, inference_path, chat_payload
    ):
        resp = requests.post(
            f"{maas_url}{inference_path}",
            headers={
                "Authorization": "Bearer ",
                "Content-Type": "application/json",
            },
            json=chat_payload,
            verify=False,
            timeout=15,
        )
        assert resp.status_code in (401, 403)

    def test_malformed_auth_header_returns_401(
        self, maas_url, inference_path, chat_payload
    ):
        resp = requests.post(
            f"{maas_url}{inference_path}",
            headers={
                "Authorization": "NotBearer some-token",
                "Content-Type": "application/json",
            },
            json=chat_payload,
            verify=False,
            timeout=15,
        )
        assert resp.status_code in (401, 403)


class TestTokenEndpointAuth:

    def test_token_endpoint_rejects_no_auth(self, maas_url):
        resp = requests.post(
            f"{maas_url}/maas-api/v1/tokens",
            headers={"Content-Type": "application/json"},
            json={"expiration": "10m"},
            verify=False,
            timeout=15,
        )
        assert resp.status_code in (401, 403)

    def test_token_endpoint_rejects_invalid_token(self, maas_url):
        resp = requests.post(
            f"{maas_url}/maas-api/v1/tokens",
            headers={
                "Authorization": "Bearer invalid-token-xyz",
                "Content-Type": "application/json",
            },
            json={"expiration": "10m"},
            verify=False,
            timeout=15,
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Request rate limiting -- per-model
# ---------------------------------------------------------------------------

def _fire_one(url, headers, payload):
    try:
        r = requests.post(
            url, headers=headers, json=payload,
            verify=False, timeout=30,
        )
        return r.status_code
    except requests.RequestException:
        return 0


class TestRequestRateLimiting:
    """tinyllama-test free tier: 10 req/1m.

    Send REQUEST_RATE_BURST (15) parallel requests; at least one must be 429.
    """

    WORKERS = 15

    def test_request_rate_limit_triggers_429(
        self, maas_url, maas_token, inference_path, chat_payload
    ):
        url = f"{maas_url}{inference_path}"
        headers = {
            "Authorization": f"Bearer {maas_token}",
            "Content-Type": "application/json",
        }
        with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            futures = [
                pool.submit(_fire_one, url, headers, chat_payload)
                for _ in range(REQUEST_RATE_BURST)
            ]
            statuses = [f.result() for f in futures]

        got_429 = statuses.count(429)
        got_200 = statuses.count(200)
        assert got_429 > 0, (
            f"Expected at least one 429 after {REQUEST_RATE_BURST} requests "
            f"(model1 free limit=10 req/1m). "
            f"Status distribution: 200={got_200}, 429={got_429}, "
            f"other={len(statuses) - got_200 - got_429}"
        )

    def test_after_request_rate_limit_still_blocked(
        self, maas_url, maas_token, inference_path, chat_payload
    ):
        """After exhausting model1, the next request should be 429."""
        url = f"{maas_url}{inference_path}"
        headers = {
            "Authorization": f"Bearer {maas_token}",
            "Content-Type": "application/json",
        }
        resp = requests.post(
            url, headers=headers, json=chat_payload,
            verify=False, timeout=30,
        )
        assert resp.status_code == 429, (
            f"Expected 429 (still rate-limited), got {resp.status_code}"
        )


class TestRequestRateLimitIsolation:
    """After exhausting model1 (10 req/1m), model2 (100 req/1m) must still work."""

    def test_model2_not_rate_limited(
        self, maas_url, maas_token, inference_path_model2, chat_payload_model2
    ):
        url = f"{maas_url}{inference_path_model2}"
        headers = {
            "Authorization": f"Bearer {maas_token}",
            "Content-Type": "application/json",
        }
        statuses = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [
                pool.submit(_fire_one, url, headers, chat_payload_model2)
                for _ in range(5)
            ]
            statuses = [f.result() for f in futures]

        got_200 = statuses.count(200)
        assert got_200 == 5, (
            f"Expected all 5 requests to model2 to succeed (limit=100 req/1m). "
            f"Got: 200={got_200}, other={statuses}"
        )


# ---------------------------------------------------------------------------
# Token rate limiting -- per-model
# ---------------------------------------------------------------------------

class TestTokenRateLimiting:
    """tinyllama-test free tier: 5000 tok/1m.

    Each request with max_tokens=500 consumes ~520 tokens (prompt+completion).
    ~10 successful responses exhaust the budget.  Send TOKEN_RATE_BURST (20)
    parallel requests and assert at least one returns 429.
    """

    WORKERS = 15

    def test_token_rate_limit_triggers_429(
        self, maas_url, maas_token, inference_path
    ):
        url = f"{maas_url}{inference_path}"
        headers = {
            "Authorization": f"Bearer {maas_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "tinyllama-test",
            "messages": [{"role": "user", "content": "Write a long story about dragons"}],
            "max_tokens": 500,
        }
        with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            futures = [
                pool.submit(_fire_one, url, headers, payload)
                for _ in range(TOKEN_RATE_BURST)
            ]
            statuses = [f.result() for f in futures]

        got_429 = statuses.count(429)
        got_200 = statuses.count(200)
        assert got_429 > 0, (
            f"Expected at least one 429 from token rate limit after "
            f"{TOKEN_RATE_BURST} requests with max_tokens=500 "
            f"(model1 free token limit=5000 tok/1m). "
            f"Status distribution: 200={got_200}, 429={got_429}, "
            f"other={len(statuses) - got_200 - got_429}"
        )


class TestTokenRateLimitIsolation:
    """model2 (10000 tok/1m) should not be affected by model1's token exhaustion."""

    def test_model2_tokens_not_exhausted(
        self, maas_url, maas_token, inference_path_model2
    ):
        url = f"{maas_url}{inference_path_model2}"
        headers = {
            "Authorization": f"Bearer {maas_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "tinyllama-fast",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 20,
        }
        statuses = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [
                pool.submit(_fire_one, url, headers, payload)
                for _ in range(3)
            ]
            statuses = [f.result() for f in futures]

        got_200 = statuses.count(200)
        assert got_200 == 3, (
            f"Expected all 3 requests to model2 to succeed "
            f"(token limit=10000 tok/1m, not shared with model1). "
            f"Got: {statuses}"
        )


# ---------------------------------------------------------------------------
# Governance K8s resources
# ---------------------------------------------------------------------------

class TestGovernanceResources:
    """Verify governance Kubernetes resources exist."""

    def test_authpolicy_exists(self, oc, gateway_namespace):
        out = oc(f"get authpolicy -n {gateway_namespace} --no-headers")
        assert "maas-default-gateway-authn" in out

    def test_ratelimitpolicy_model1_exists(self, oc, model_namespace):
        out = oc(f"get ratelimitpolicy -n {model_namespace} --no-headers")
        assert "tinyllama-test-rate-limits" in out

    def test_ratelimitpolicy_model2_exists(self, oc, model_namespace):
        out = oc(f"get ratelimitpolicy -n {model_namespace} --no-headers")
        assert "tinyllama-fast-rate-limits" in out

    def test_tokenratelimitpolicy_model1_exists(self, oc, model_namespace):
        out = oc(
            f"get tokenratelimitpolicy -n {model_namespace} --no-headers"
        )
        assert "tinyllama-test-token-rate-limits" in out

    def test_tokenratelimitpolicy_model2_exists(self, oc, model_namespace):
        out = oc(
            f"get tokenratelimitpolicy -n {model_namespace} --no-headers"
        )
        assert "tinyllama-fast-token-rate-limits" in out

    def test_telemetrypolicy_exists(self, oc, gateway_namespace):
        out = oc(f"get telemetrypolicy -n {gateway_namespace} --no-headers")
        assert "user-group" in out

    def test_tier_groups_exist(self, oc):
        out = oc("get groups --no-headers")
        assert "tier-premium-users" in out
