"""Governance enforcement: authentication, authorization, and rate limits."""

import pytest
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

RATE_LIMIT_BURST = int(
    __import__("os").getenv("MAAS_RATE_LIMIT_BURST", "120")
)


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


class TestRateLimiting:
    """Send a burst of parallel requests to trigger the per-tier rate limit.

    The free tier allows 100 req/60s.  Requests must be sent concurrently
    because each inference call takes ~0.5-1s; sequential sends would let
    the 60s window rotate before reaching the limit.
    """

    WORKERS = 30

    @staticmethod
    def _fire_one(url, headers, payload):
        try:
            r = requests.post(
                url, headers=headers, json=payload,
                verify=False, timeout=30,
            )
            return r.status_code
        except requests.RequestException:
            return 0

    def test_rate_limit_triggers_429(
        self, maas_url, maas_token, inference_path, chat_payload
    ):
        """Send RATE_LIMIT_BURST parallel requests; at least one must be 429."""
        from concurrent.futures import ThreadPoolExecutor

        url = f"{maas_url}{inference_path}"
        headers = {
            "Authorization": f"Bearer {maas_token}",
            "Content-Type": "application/json",
        }
        with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            futures = [
                pool.submit(self._fire_one, url, headers, chat_payload)
                for _ in range(RATE_LIMIT_BURST)
            ]
            statuses = [f.result() for f in futures]

        got_429 = statuses.count(429)
        got_200 = statuses.count(200)
        assert got_429 > 0, (
            f"Expected at least one 429 after {RATE_LIMIT_BURST} requests. "
            f"Status distribution: 200={got_200}, 429={got_429}, "
            f"other={len(statuses) - got_200 - got_429}"
        )

    def test_after_rate_limit_still_blocked(
        self, maas_url, maas_token, inference_path, chat_payload
    ):
        """After hitting the limit, the very next request should be 429."""
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


class TestGovernanceResources:
    """Verify governance Kubernetes resources exist."""

    def test_authpolicy_exists(self, oc, gateway_namespace):
        out = oc(f"get authpolicy -n {gateway_namespace} --no-headers")
        assert "maas-default-gateway-authn" in out

    def test_ratelimitpolicy_exists(self, oc, gateway_namespace):
        out = oc(f"get ratelimitpolicy -n {gateway_namespace} --no-headers")
        assert "gateway-rate-limits" in out

    def test_tokenratelimitpolicy_exists(self, oc, gateway_namespace):
        out = oc(
            f"get tokenratelimitpolicy -n {gateway_namespace} --no-headers"
        )
        assert "gateway-token-rate-limits" in out

    def test_telemetrypolicy_exists(self, oc, gateway_namespace):
        out = oc(f"get telemetrypolicy -n {gateway_namespace} --no-headers")
        assert "user-group" in out

    def test_tier_groups_exist(self, oc):
        out = oc("get groups --no-headers")
        assert "tier-premium-users" in out
        assert "tier-enterprise-users" in out
