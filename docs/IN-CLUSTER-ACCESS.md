# In-Cluster Access to MaaS Models

How to consume MaaS-governed models from workloads running inside the same OpenShift cluster without going through the external Route.

## Problem

The default MaaS access path routes traffic externally:

```
Agent Pod -> Route (external DNS) -> Load Balancer -> Gateway (Envoy) -> AuthPolicy -> Model Pod
```

For in-cluster agents this adds unnecessary latency: traffic exits the cluster, hits the external load balancer, re-enters, and traverses TLS termination twice.

## Solution: Use the Gateway's Internal ClusterIP

When OpenShift creates a Gateway, the Gateway controller also creates a backing Kubernetes Service with a ClusterIP in the same namespace. This Service is reachable from any pod inside the cluster.

```
Agent Pod -> Gateway Service (ClusterIP) -> AuthPolicy -> Model Pod
```

### Internal Service Details

| Resource | Name | Namespace | Port |
| --- | --- | --- | --- |
| Gateway Service | `maas-default-gateway-openshift-default` | `openshift-ingress` | 443 (HTTPS) |
| Direct model Service | `<model>-kserve-workload-svc` | model namespace | 8000 (HTTPS) |

The Gateway Service name follows the pattern `<gateway-name>-<gatewayclass-name>`. Since we use `maas-default-gateway` with class `openshift-default`, the Service is `maas-default-gateway-openshift-default`.

Verify in your cluster:

```bash
oc get svc -n openshift-ingress | grep maas
```

### Internal DNS

```
maas-default-gateway-openshift-default.openshift-ingress.svc.cluster.local:443
```

## Usage from an In-Cluster Agent

### Step 1: Get a MaaS token

The agent uses its own Kubernetes ServiceAccount token (automatically mounted at `/var/run/secrets/kubernetes.io/serviceaccount/token`) to request a MaaS token via the internal Gateway.

The ServiceAccount must be a member of a MaaS tier group (e.g., `maas-default-gateway-tier-free-users`). The `maas-api` resolves the user's tier from group membership and generates a scoped token.

```python
import requests

GATEWAY = "https://maas-default-gateway-openshift-default.openshift-ingress.svc.cluster.local"

with open("/var/run/secrets/kubernetes.io/serviceaccount/token") as f:
    sa_token = f.read()

response = requests.post(
    f"{GATEWAY}/maas-api/v1/tokens",
    headers={"Authorization": f"Bearer {sa_token}"},
    json={"expiration": "1h"},
    verify=False,  # Gateway uses cluster-internal TLS
)
maas_token = response.json()["token"]
```

### Step 2: Call the model

```python
response = requests.post(
    f"{GATEWAY}/maas-models/tinyllama-test/v1/chat/completions",
    headers={
        "Authorization": f"Bearer {maas_token}",
        "Content-Type": "application/json",
    },
    json={
        "model": "tinyllama-test",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 50,
    },
    verify=False,
)
print(response.json())
```

### Using curl from a pod

```bash
GATEWAY="https://maas-default-gateway-openshift-default.openshift-ingress.svc.cluster.local"
SA_TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)

# Get MaaS token
MAAS_TOKEN=$(curl -sk -X POST "$GATEWAY/maas-api/v1/tokens" \
  -H "Authorization: Bearer $SA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"expiration":"1h"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

# Inference
curl -sk "$GATEWAY/maas-models/tinyllama-test/v1/chat/completions" \
  -H "Authorization: Bearer $MAAS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"tinyllama-test","messages":[{"role":"user","content":"Hello"}],"max_tokens":50}'
```

## OpenAI SDK Compatibility

MaaS exposes an OpenAI-compatible API. The internal Gateway URL works as a drop-in `base_url` for the OpenAI Python SDK:

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://maas-default-gateway-openshift-default.openshift-ingress.svc.cluster.local/maas-models/tinyllama-test/v1",
    api_key=maas_token,  # MaaS token obtained in Step 1
)

# Disable SSL verification for cluster-internal TLS
import httpx
client._client = httpx.Client(base_url=client.base_url, verify=False)

response = client.chat.completions.create(
    model="tinyllama-test",
    messages=[{"role": "user", "content": "Hello"}],
    max_tokens=50,
)
```

## TLS Certificate Handling

The Gateway uses cluster-internal TLS certificates signed by the OpenShift service CA. For in-cluster workloads there are two approaches:

**Option 1: Skip verification (simplest)**

Set `verify=False` (Python) or `-k` (curl). Acceptable for cluster-internal traffic where the network is trusted.

**Option 2: Trust the service CA**

Mount the OpenShift service CA bundle and point your client to it:

```yaml
volumes:
- name: service-ca
  configMap:
    name: openshift-service-ca.crt
containers:
- name: agent
  volumeMounts:
  - name: service-ca
    mountPath: /etc/pki/tls/service-ca
    readOnly: true
  env:
  - name: REQUESTS_CA_BUNDLE
    value: /etc/pki/tls/service-ca/service-ca.crt
```

## Latency Comparison

Measured from a pod inside the cluster (`curl -sk -w "%{time_total}" ...`):

| Path | Latency | Governance |
| --- | --- | --- |
| Internal Gateway (ClusterIP) | ~18ms | Full (auth, RBAC, rate limiting) |
| Direct model Service (bypass) | ~13ms | None |
| External Route | ~24ms | Full |

The internal Gateway path eliminates the external load balancer round-trip while preserving all MaaS governance.

## Alternative: Direct Model Access (No Governance)

KServe creates an internal Service for each model. Accessing it directly bypasses all MaaS governance:

```
<model>-kserve-workload-svc.<namespace>.svc.cluster.local:8000
```

```bash
curl -sk "https://tinyllama-test-kserve-workload-svc.maas-models.svc.cluster.local:8000/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model":"tinyllama-test","messages":[{"role":"user","content":"Hello"}],"max_tokens":50}'
```

**Use only for trusted internal workloads** where authentication, rate limiting, and audit are not required. Consider adding a `NetworkPolicy` to restrict which namespaces can reach the model Service.

## Architecture Diagram

```
                        +---------------------------------------------------+
                        |              OpenShift Cluster                     |
                        |                                                   |
  External              |   +------------------------------------------+    |
  Client ----Route------|-->| Gateway Service (ClusterIP: 172.30.x.x)  |    |
                        |   | maas-default-gateway-openshift-default   |    |
  In-Cluster            |   +--------------------+---------------------+    |
  Agent Pod ------------|-->|                    |                     |    |
     (fast path)        |   |           +--------v---------+          |    |
                        |   |           | Envoy + Authorino|          |    |
                        |   |           | (AuthPolicy)     |          |    |
                        |   |           +--------+---------+          |    |
                        |   |                    |                    |    |
                        |   |         +----------+----------+         |    |
                        |   |         |                     |         |    |
                        |   |    +----v-----+       +-------v------+  |    |
                        |   |    | MaaS API |       | Model Pod    |  |    |
                        |   |    | /maas-api|       | /maas-models |  |    |
                        |   |    +----------+       +--------------+  |    |
                        |   +------------------------------------------+    |
                        +---------------------------------------------------+
```

The external client traverses: Route -> Load Balancer -> Gateway -> Auth -> Model.
The in-cluster agent traverses: Gateway (ClusterIP) -> Auth -> Model, skipping the Route and load balancer entirely.
