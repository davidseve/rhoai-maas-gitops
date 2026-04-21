"""In-cluster access: Gateway ClusterIP and direct model bypass.

Uses ephemeral pods via ``oc run --rm`` to test from inside the cluster.
"""

import json
import subprocess
import os
import uuid

import pytest

MODEL_NAME = os.getenv("MAAS_MODEL_NAME", "tinyllama-test")
MODEL_NAMESPACE = os.getenv("MAAS_MODEL_NAMESPACE", "maas-models")
GATEWAY_NAME = os.getenv("MAAS_GATEWAY_NAME", "maas-default-gateway")
GATEWAY_NAMESPACE = os.getenv("MAAS_GATEWAY_NAMESPACE", "openshift-ingress")
GATEWAY_CLASS = os.getenv("MAAS_GATEWAY_CLASS", "openshift-default")
INCLUSTER_IMAGE = os.getenv(
    "MAAS_INCLUSTER_IMAGE", "registry.redhat.io/openshift4/ose-cli:latest"
)

GATEWAY_SVC = f"{GATEWAY_NAME}-{GATEWAY_CLASS}"
GATEWAY_INTERNAL = (
    f"https://{GATEWAY_SVC}.{GATEWAY_NAMESPACE}.svc.cluster.local"
)
MODEL_SVC = (
    f"https://{MODEL_NAME}-kserve-workload-svc"
    f".{MODEL_NAMESPACE}.svc.cluster.local:8000"
)

def _extract_json(text: str) -> dict:
    """Extract the largest valid JSON object from mixed oc run output."""
    best = None
    for i, ch in enumerate(text):
        if ch != '{':
            continue
        depth = 0
        for j in range(i, len(text)):
            if text[j] == '{':
                depth += 1
            elif text[j] == '}':
                depth -= 1
            if depth == 0:
                candidate = text[i:j + 1]
                try:
                    obj = json.loads(candidate)
                    if best is None or len(candidate) > len(best[1]):
                        best = (obj, candidate)
                except json.JSONDecodeError:
                    pass
                break
    if best is not None:
        return best[0]
    pytest.fail(f"No valid JSON found in output:\n{text[:500]}")


def _run_in_cluster(script: str, timeout: int = 90) -> str:
    """Run a bash script inside an ephemeral pod and return stdout.

    Each invocation uses a unique pod name to avoid collisions when a
    previous pod was not cleaned up properly (e.g. after a timeout).
    """
    pod_name = f"e2e-incluster-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["oc", "delete", "pod", pod_name, "--ignore-not-found"],
        capture_output=True, timeout=15,
    )
    cmd = [
        "oc", "run", pod_name,
        "--rm", "-i", "--restart=Never",
        f"--image={INCLUSTER_IMAGE}",
        "--", "bash", "-c", script,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout
    )
    if not result.stdout.strip():
        pytest.fail(
            f"oc run returned empty stdout.\n"
            f"stderr: {result.stderr[:500]}\n"
            f"returncode: {result.returncode}"
        )
    return result.stdout


class TestInClusterGateway:

    def test_gateway_clusterip_service_exists(self, oc, gateway_namespace):
        out = oc(f"get svc {GATEWAY_SVC} -n {gateway_namespace} --no-headers")
        assert GATEWAY_SVC in out

    def test_token_via_internal_gateway(self):
        script = f"""
SA_TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
curl -sk -X POST "{GATEWAY_INTERNAL}/maas-api/v1/tokens" \
  -H "Authorization: Bearer $SA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{{"expiration":"10m"}}'
"""
        out = _run_in_cluster(script)
        body = _extract_json(out)
        assert "token" in body, f"No token in response: {body}"
        assert len(body["token"]) > 50

    def test_inference_via_internal_gateway(self):
        script = f"""
SA_TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
MAAS_TOKEN=$(curl -sk -X POST "{GATEWAY_INTERNAL}/maas-api/v1/tokens" \
  -H "Authorization: Bearer $SA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{{"expiration":"10m"}}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
curl -sk "{GATEWAY_INTERNAL}/maas-models/{MODEL_NAME}/v1/chat/completions" \
  -H "Authorization: Bearer $MAAS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{{"model":"{MODEL_NAME}","messages":[{{"role":"user","content":"Hi"}}],"max_tokens":10}}'
"""
        out = _run_in_cluster(script)
        body = _extract_json(out)
        assert "choices" in body, f"No choices in response: {body}"
        assert body["choices"][0]["message"]["content"]


class TestDirectModelAccess:

    def test_direct_model_bypass_no_auth(self):
        """Access model directly without governance -- no token needed."""
        script = f"""
curl -sk "{MODEL_SVC}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{{"model":"{MODEL_NAME}","messages":[{{"role":"user","content":"Hi"}}],"max_tokens":10}}'
"""
        out = _run_in_cluster(script)
        body = _extract_json(out)
        assert "choices" in body, f"No choices in direct response: {body}"
        assert body["model"] == MODEL_NAME
