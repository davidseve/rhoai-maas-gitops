"""Shared fixtures for MaaS E2E tests.

Requires: `oc` CLI logged into the target cluster.
"""

import json
import os
import subprocess
import ssl

import pytest
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Configuration (env vars with defaults matching charts/values.yaml)
# ---------------------------------------------------------------------------

MODEL_NAME = os.getenv("MAAS_MODEL_NAME", "tinyllama-test")
MODEL2_NAME = os.getenv("MAAS_MODEL2_NAME", "tinyllama-fast")
MODEL_NAMESPACE = os.getenv("MAAS_MODEL_NAMESPACE", "maas-models")
GATEWAY_NAME = os.getenv("MAAS_GATEWAY_NAME", "maas-default-gateway")
GATEWAY_NAMESPACE = os.getenv("MAAS_GATEWAY_NAMESPACE", "openshift-ingress")
GATEWAY_CLASS = os.getenv("MAAS_GATEWAY_CLASS", "openshift-default")


def _run(cmd: str, *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, shell=True, capture_output=True, text=True, check=check
    )


# ---------------------------------------------------------------------------
# oc helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def oc():
    """Run an arbitrary ``oc`` command and return stdout (stripped)."""

    def _oc(cmd: str) -> str:
        result = _run(f"oc {cmd}")
        return result.stdout.strip()

    _oc("whoami")
    return _oc


@pytest.fixture(scope="session")
def oc_json(oc):
    """Run an ``oc get … -o json`` command and return parsed dict."""

    def _oc_json(cmd: str) -> dict:
        out = oc(f"{cmd} -o json")
        return json.loads(out)

    return _oc_json


# ---------------------------------------------------------------------------
# Cluster / MaaS coordinates
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def cluster_domain(oc):
    return oc(
        "get ingresses.config.openshift.io cluster "
        "-o jsonpath='{.spec.domain}'"
    ).strip("'")


@pytest.fixture(scope="session")
def maas_host(cluster_domain):
    return f"maas.{cluster_domain}"


@pytest.fixture(scope="session")
def maas_url(maas_host):
    return f"https://{maas_host}"


@pytest.fixture(scope="session")
def gateway_internal_host():
    svc = f"{GATEWAY_NAME}-{GATEWAY_CLASS}"
    return f"{svc}.{GATEWAY_NAMESPACE}.svc.cluster.local"


@pytest.fixture(scope="session")
def gateway_internal_url(gateway_internal_host):
    return f"https://{gateway_internal_host}"


# ---------------------------------------------------------------------------
# Authentication tokens
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def oc_token(oc):
    """Current ``oc`` session token (admin)."""
    return oc("whoami -t")


@pytest.fixture(scope="session")
def maas_token(maas_url, oc_token):
    """Generate a MaaS token via the external Route (cached per session)."""
    resp = requests.post(
        f"{maas_url}/maas-api/v1/tokens",
        headers={
            "Authorization": f"Bearer {oc_token}",
            "Content-Type": "application/json",
        },
        json={"expiration": "30m"},
        verify=False,
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json()["token"]
    assert token, "MaaS token is empty"
    return token


# ---------------------------------------------------------------------------
# Helpers exposed as fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def model_name():
    return MODEL_NAME


@pytest.fixture(scope="session")
def model_namespace():
    return MODEL_NAMESPACE


@pytest.fixture(scope="session")
def gateway_name():
    return GATEWAY_NAME


@pytest.fixture(scope="session")
def gateway_namespace():
    return GATEWAY_NAMESPACE


@pytest.fixture(scope="session")
def inference_path():
    """URL path segment for chat completions (model 1)."""
    return f"/maas-models/{MODEL_NAME}/v1/chat/completions"


@pytest.fixture(scope="session")
def chat_payload():
    """Minimal chat completion request body (model 1)."""
    return {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": "Say hello in one word"}],
        "max_tokens": 20,
    }


# ---------------------------------------------------------------------------
# Model 2 fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def model2_name():
    return MODEL2_NAME


@pytest.fixture(scope="session")
def inference_path_model2():
    """URL path segment for chat completions (model 2)."""
    return f"/maas-models/{MODEL2_NAME}/v1/chat/completions"


@pytest.fixture(scope="session")
def chat_payload_model2():
    """Minimal chat completion request body (model 2)."""
    return {
        "model": MODEL2_NAME,
        "messages": [{"role": "user", "content": "Say hello in one word"}],
        "max_tokens": 20,
    }
