# MaaS E2E Tests

End-to-end tests for the MaaS deployment on OpenShift. Tests validate inference, governance enforcement, and in-cluster access against a live cluster.

## Prerequisites

- `oc` CLI logged into the target cluster with admin access
- MaaS fully deployed (operators, platform, model)
- Model pod running and `LLMInferenceService` Ready

## Setup

```bash
cd tests
pip install -r requirements.txt
```

## Running

```bash
pytest -v                         # All tests
pytest -v test_inference.py       # Just inference
pytest -v test_governance.py      # Just governance
pytest -v test_incluster.py       # Just in-cluster access
pytest -v --tb=short              # Compact failure output
pytest -v -x                      # Stop on first failure
```

## Configuration

Override defaults via environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `MAAS_MODEL_NAME` | `tinyllama-test` | Model name |
| `MAAS_MODEL_NAMESPACE` | `maas-models` | Model namespace |
| `MAAS_GATEWAY_NAME` | `maas-default-gateway` | Gateway name |
| `MAAS_GATEWAY_NAMESPACE` | `openshift-ingress` | Gateway namespace |
| `MAAS_GATEWAY_CLASS` | `openshift-default` | GatewayClass name |
| `MAAS_INCLUSTER_IMAGE` | `registry.redhat.io/openshift4/ose-cli:latest` | Image for in-cluster test pods |

## Test Modules

| Module | What it tests |
| --- | --- |
| `test_inference.py` | Token generation + chat completions via external Route |
| `test_governance.py` | Auth enforcement (401/403), governance resources exist |
| `test_incluster.py` | Internal Gateway ClusterIP access + direct model bypass |

## Notes

- **In-cluster tests** create ephemeral pods (`oc run --rm`) -- these take a few seconds each
- **Governance tests** check that unauthenticated/invalid requests are rejected and governance CRs exist
- Tests use `verify=False` for TLS since clusters typically use self-signed certs
