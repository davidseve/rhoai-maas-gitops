"""Governance enforcement: authentication and authorization."""

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


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
