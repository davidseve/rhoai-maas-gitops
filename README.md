# Red Hat OpenShift AI - Models-as-a-Service (MaaS) Deployment

Helm charts for deploying RHOAI 3.3 with Models-as-a-Service on OpenShift 4.20+.

Reference: [Official RHOAI 3.3 MaaS Documentation](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.3/html-single/govern_llm_access_with_models-as-a-service/index)

## Prerequisites

- OpenShift 4.20 or later
- Cluster admin access
- `oc` CLI installed and logged in
- `helm` CLI installed

## Architecture

```
                                      ┌─────────────────────────────────────┐
                                      │         OpenShift Cluster           │
   Client                             │                                     │
     │                                │  ┌──────────┐    ┌──────────────┐  │
     │  TLS                           │  │ OpenShift │    │   Gateway    │  │
     ├──────►  Route (passthrough  ───┼──► Router    ├───►│ (Istio/Envoy)│  │
     │         or reencrypt)          │  └──────────┘    └──────┬───────┘  │
     │                                │                         │          │
     │                                │              ┌──────────┴────────┐ │
     │                                │              │   AuthPolicy      │ │
     │                                │              │   (Kuadrant)      │ │
     │                                │              │  - Token auth     │ │
     │                                │              │  - Tier RBAC      │ │
     │                                │              │  - Rate limits    │ │
     │                                │              └──────────┬────────┘ │
     │                                │                         │          │
     │                                │     ┌───────────────────┤          │
     │                                │     │                   │          │
     │                                │  ┌──▼──────┐    ┌──────▼───────┐  │
     │                                │  │ MaaS API │    │  Model Pod   │  │
     │                                │  │ (tokens, │    │  (vLLM/llm-d)│  │
     │                                │  │  tiers)  │    │              │  │
     │                                │  └──────────┘    └──────────────┘  │
     │                                └─────────────────────────────────────┘
```

## Charts

| Chart | Description |
|-------|-------------|
| `charts/operators/` | Installs the 4 prerequisite operators |
| `charts/maas-platform/` | DSC, Gateway, Route, and dashboard config |
| `charts/maas-model/` | Model deployment + RBAC (per model) |

## Step-by-Step Installation

### Step 1: Log in to the cluster

```bash
oc login -u <admin-user> <api-server-url>
```

### Step 2: Determine your cluster domain

```bash
export CLUSTER_DOMAIN=$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')
echo $CLUSTER_DOMAIN
```

### Step 3: Install prerequisite operators

```bash
helm template operators charts/operators/ | oc apply -f -
```

This installs four operators:

| Operator | Package | Channel | Namespace |
|----------|---------|---------|-----------|
| Red Hat OpenShift AI 3.3 | `rhods-operator` | `fast-3.x` | `redhat-ods-operator` |
| Red Hat Connectivity Link (Kuadrant) | `rhcl-operator` | `stable` | `kuadrant-system` |
| cert-manager | (pre-installed) | `stable-v1` | `cert-manager-operator` |
| LeaderWorkerSet | `leader-worker-set` | `stable-v1.0` | `leader-worker-set` |

**Important:** The Kuadrant CR (`kuadrant.io/v1beta1/Kuadrant`) and the LeaderWorkerSet CR (`operator.openshift.io/v1/LeaderWorkerSetOperator`) require their CRDs to exist first. If the initial `oc apply` fails on these resources, wait for the operators to install and re-run the command:

```bash
# Wait for operators to be ready
oc get csv -n redhat-ods-operator | grep rhods     # Succeeded
oc get csv -n kuadrant-system | grep rhcl          # Succeeded
oc get csv -n leader-worker-set | grep leader      # Succeeded

# Re-apply to create the CRs
helm template operators charts/operators/ | oc apply -f -
```

Wait for Kuadrant to become Ready:

```bash
oc wait Kuadrant -n kuadrant-system kuadrant --for=condition=Ready --timeout=5m
```

If Kuadrant shows `MissingDependency` (Gateway API provider not found), restart the Kuadrant operator pod after RHOAI finishes installing Istio:

```bash
oc delete pod -n kuadrant-system -l app.kubernetes.io/name=kuadrant-operator
```

### Step 4: Deploy MaaS platform

The `maas-default-gateway` Gateway **must exist before** the DSC can enable MaaS. Install the full platform chart at once:

```bash
helm template maas-platform charts/maas-platform/ \
  --set clusterDomain=$CLUSTER_DOMAIN \
  | oc apply -f -
```

Wait for the DSC to be Ready:

```bash
# Check DSC status (may take 2-3 minutes)
oc get datasciencecluster default-dsc -o jsonpath='{.status.phase}'
# Expected: Ready

# Verify MaaS is running
oc get pods -n redhat-ods-applications -l app.kubernetes.io/name=maas-api
# Expected: 1/1 Running

# Verify tier configuration
oc get configmap tier-to-group-mapping -n redhat-ods-applications
```

### Step 5: Verify end-to-end connectivity

```bash
# Get a MaaS token using your OpenShift credentials
HOST="https://maas.${CLUSTER_DOMAIN}"

TOKEN_RESPONSE=$(curl -sSk \
  -H "Authorization: Bearer $(oc whoami -t)" \
  -H "Content-Type: application/json" \
  -X POST -d '{"expiration":"10m"}' \
  "${HOST}/maas-api/v1/tokens")

echo $TOKEN_RESPONSE | python3 -m json.tool
```

If this returns a JSON with a `token` field, the platform is working.

### Step 6: Deploy a model

Add the model namespace to the Gateway's allowed routes, then deploy:

```bash
# Allow the model namespace in the Gateway
oc patch gateway maas-default-gateway -n openshift-ingress --type=json \
  -p '[{"op":"add","path":"/spec/listeners/0/allowedRoutes/namespaces/selector/matchExpressions/0/values/-","value":"maas-models"}]'

# CPU-only cluster (InferenceService mode)
helm template tinyllama charts/maas-model/ | oc apply -f -

# GPU cluster (LLMInferenceService mode, full MaaS integration)
helm template tinyllama charts/maas-model/ \
  --set mode=llminferenceservice \
  --set rbac.enabled=true \
  | oc apply -f -
```

Wait for the model to be ready:

```bash
# InferenceService mode
oc wait inferenceservice tinyllama-test -n maas-models --for=condition=Ready --timeout=5m

# LLMInferenceService mode
oc wait llminferenceservice tinyllama-test -n maas-models --for=condition=Ready --timeout=5m
```

---

## Gateway and Route Configuration

The MaaS Gateway needs to be exposed externally via an OpenShift Route. There are two TLS termination strategies, each with trade-offs. The chart supports both through `values.yaml`.

### How TLS works in each mode

```
PASSTHROUGH:
  Client ──TLS──► OpenShift Router ──TLS (same)──► Gateway (Istio/Envoy)
  The router does NOT terminate TLS. It forwards the encrypted traffic
  directly to the Gateway based on SNI (Server Name Indication).
  The Gateway's TLS certificate must match the external hostname.

REENCRYPT:
  Client ──TLS──► OpenShift Router ──new TLS──► Gateway (Istio/Envoy)
  The router terminates the client's TLS using its own wildcard cert,
  then opens a NEW TLS connection to the Gateway using a separate cert.
  The Gateway's cert does NOT need to match the external hostname.
```

### Option A: Passthrough (default)

The simplest configuration. TLS goes from the client directly to the Gateway. The OpenShift Router acts as a TCP proxy.

**Requirements:**
- The Gateway must use a TLS certificate that matches the external hostname `maas.<clusterDomain>`.
- This is typically the cluster's wildcard certificate (`*.apps.<clusterDomain>`).

**Configuration:**

```yaml
# values.yaml
gateway:
  tlsSecretName: ingress-certs     # AWS clusters
  # tlsSecretName: router-certs-default  # bare-metal clusters

route:
  tlsTermination: passthrough
```

**Wildcard certificate secret name varies by platform:**

| Platform | Secret name | Namespace | Notes |
|----------|-------------|-----------|-------|
| AWS (ROSA, IPI) | `ingress-certs` | `openshift-ingress` | Let's Encrypt or ACM cert |
| Bare-metal / UPI | `router-certs-default` | `openshift-ingress` | Self-signed or custom CA |
| Custom | Check your cluster | `openshift-ingress` | `oc get secret -n openshift-ingress \| grep tls` |

**Pros:**
- Simple configuration, no extra annotations needed.
- Full end-to-end encryption with a single TLS session.
- No certificate validation issues between Router and Gateway.

**Cons:**
- The Gateway cert must be the wildcard cert -- you need to know the secret name (varies by platform).
- If the wildcard cert is managed/rotated externally, the Gateway picks up changes automatically only if the Secret is updated in place.

### Option B: Reencrypt

The OpenShift Router terminates the external TLS and establishes a new TLS connection to the Gateway. This is how the RHOAI `data-science-gateway` works out of the box.

**Requirements:**
- The Gateway uses a certificate signed by the OpenShift service-ca (internal CA).
- The service-ca certificate is generated automatically by annotating the Gateway's Service.

**Configuration:**

```yaml
# values.yaml
gateway:
  tlsSecretName: maas-gateway-service-tls

route:
  tlsTermination: reencrypt
```

**Additional manual step** (must run after the Gateway Service is created):

```bash
# Annotate the Gateway Service to generate a service-ca certificate
oc annotate svc maas-default-gateway-data-science-gateway-class \
  -n openshift-ingress \
  service.beta.openshift.io/serving-cert-secret-name=maas-gateway-service-tls

# Verify the secret was created
oc get secret maas-gateway-service-tls -n openshift-ingress
```

The Route template automatically adds the `router.openshift.io/service-ca-certificate: "true"` annotation when `reencrypt` is selected, which tells the Router to trust the OpenShift service-ca for backend validation.

**Pros:**
- Works identically on AWS, bare-metal, and any other platform.
- No need to know the wildcard certificate secret name.
- The service-ca certificate is auto-generated and auto-rotated.

**Cons:**
- Requires the extra annotation step on the Service (not yet automatable in the Helm chart since the Service is created by the Gateway controller, not by Helm).
- Two TLS sessions instead of one (negligible performance impact).

### Decision guide

| Scenario | Recommended mode | Gateway cert | Why |
|----------|-----------------|--------------|-----|
| AWS / cloud with known wildcard cert | **passthrough** | `ingress-certs` | Simple, no extra steps |
| Bare-metal with `router-certs-default` | **passthrough** | `router-certs-default` | Simple, no extra steps |
| Unknown platform / multi-cluster GitOps | **reencrypt** | `maas-gateway-service-tls` | Platform-independent |
| Wildcard cert name unknown | **reencrypt** | `maas-gateway-service-tls` | Avoids guessing the secret |

### Switching between modes

To switch from passthrough to reencrypt (or vice versa):

```bash
# 1. Delete the existing Route
oc delete route maas-default-gateway -n openshift-ingress

# 2. For reencrypt: annotate the service (skip for passthrough)
oc annotate svc maas-default-gateway-data-science-gateway-class \
  -n openshift-ingress \
  service.beta.openshift.io/serving-cert-secret-name=maas-gateway-service-tls

# 3. Update the Gateway certificate
oc patch gateway maas-default-gateway -n openshift-ingress --type=json \
  -p '[{"op":"replace","path":"/spec/listeners/0/tls/certificateRefs/0/name","value":"<new-secret-name>"}]'

# 4. Re-apply the platform chart with new values
helm template maas-platform charts/maas-platform/ \
  --set clusterDomain=$CLUSTER_DOMAIN \
  --set route.tlsTermination=reencrypt \
  --set gateway.tlsSecretName=maas-gateway-service-tls \
  | oc apply -f -
```

---

## Deploying a Model (`charts/maas-model`)

The `maas-model` chart supports two modes controlled by `mode` in `values.yaml`:

| Mode | Runtime | GPU Required | MaaS Integration | Use case |
|------|---------|--------------|-----------------|----------|
| `inferenceservice` (default) | vLLM CPU | No | No (direct access) | CPU-only clusters, testing |
| `llminferenceservice` | llm-d (vLLM CUDA) | Yes | Yes (tiers, tokens, rate limits) | Production with GPUs |

### Mode A: InferenceService (CPU, no MaaS tiers)

```bash
helm template my-model charts/maas-model/ | oc apply -f -
```

This deploys a vLLM CPU ServingRuntime + InferenceService with a passthrough Route for direct access. Authentication uses the OpenShift token (`oc whoami -t`), not MaaS tokens.

```bash
# Test the model
CLUSTER_DOMAIN=$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')
curl -sSk "https://tinyllama-test.${CLUSTER_DOMAIN}/v1/chat/completions" \
  -H "Authorization: Bearer $(oc whoami -t)" \
  -H "Content-Type: application/json" \
  -d '{"model":"tinyllama-test","messages":[{"role":"user","content":"Hello"}],"max_tokens":20}'
```

### Mode B: LLMInferenceService (GPU, full MaaS integration)

```bash
helm template my-model charts/maas-model/ \
  --set mode=llminferenceservice \
  --set rbac.enabled=true \
  | oc apply -f -
```

This deploys an LLMInferenceService with llm-d runtime, tier annotations, and RBAC for MaaS access. The model appears in the MaaS dashboard and supports tier-based tokens.

**Important:** The default llm-d runtime uses a CUDA (GPU) vLLM image. It will **not** work on CPU-only clusters.

```bash
# Test via MaaS
CLUSTER_DOMAIN=$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')
HOST="https://maas.${CLUSTER_DOMAIN}"

# Get a MaaS token
TOKEN=$(curl -sSk -H "Authorization: Bearer $(oc whoami -t)" \
  -H "Content-Type: application/json" -X POST -d '{"expiration":"10m"}' \
  "${HOST}/maas-api/v1/tokens" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

# Invoke the model through MaaS
curl -sSk "${HOST}/maas-models/tinyllama-test/v1/chat/completions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"tinyllama-test","messages":[{"role":"user","content":"Hello"}],"max_tokens":20}'
```

### Gateway namespace allowlist

The MaaS Gateway only routes traffic to namespaces in its `allowedRoutes` list. You must add your model namespace:

```bash
oc patch gateway maas-default-gateway -n openshift-ingress --type=json \
  -p '[{"op":"add","path":"/spec/listeners/0/allowedRoutes/namespaces/selector/matchExpressions/0/values/-","value":"maas-models"}]'
```

### Customizing the model

Override values to deploy a different model:

```bash
helm template my-model charts/maas-model/ \
  --set global.name=my-llama \
  --set global.namespace=my-project \
  --set model.storageUri="oci://quay.io/my-org/my-model:v1" \
  --set model.servedName=my-llama \
  --set model.maxModelLen=4096 \
  --set resources.requests.cpu=4 \
  --set resources.requests.memory=8Gi \
  --set resources.limits.cpu=16 \
  --set resources.limits.memory=16Gi \
  | oc apply -f -
```

---

## Troubleshooting

### MaaS component not ready

```bash
oc get datasciencecluster default-dsc -o yaml | grep -A5 ModelsAsServiceReady
```

Common causes:
- **"gateway not found"**: The `maas-default-gateway` Gateway must exist in `openshift-ingress` before enabling MaaS in the DSC.
- **"DeploymentsNotReady"**: Wait 2-3 minutes for the maas-api pod to start.

### Kuadrant not ready

```bash
oc get kuadrant kuadrant -n kuadrant-system -o yaml | grep -A5 'type: Ready'
```

- **"MissingDependency" (Gateway API provider)**: RHOAI has not finished installing Istio yet. Wait for the RHOAI operator to finish, then restart the Kuadrant operator pod.

### LeaderWorkerSet CRD missing

If `LLMInferenceService` shows `ReconcileMultiNodeWorkloadError`:

```bash
oc get crd leaderworkersets.leaderworkerset.x-k8s.io
```

The LWS operator requires a `LeaderWorkerSetOperator` CR to deploy the actual controller:

```bash
oc apply -f - <<EOF
apiVersion: operator.openshift.io/v1
kind: LeaderWorkerSetOperator
metadata:
  name: cluster
spec:
  managementState: Managed
EOF
```

### Route returns "Application is not available"

The Route cannot reach the Gateway backend. Check:

1. The Gateway Service exists: `oc get svc -n openshift-ingress | grep maas`
2. For reencrypt: the service-ca cert exists: `oc get secret maas-gateway-service-tls -n openshift-ingress`
3. For passthrough: the Gateway cert matches the wildcard: check `gateway.tlsSecretName` value

### 401 Unauthorized on token generation

Verify the AuthPolicy audiences:

```bash
oc get authpolicy maas-api-auth-policy -n redhat-ods-applications \
  -o jsonpath='{.spec.rules.authentication.openshift-identities.kubernetesTokenReview.audiences}'
```

Should include both `https://kubernetes.default.svc` and `maas-default-gateway-sa`. In RHOAI 3.3.1, this is configured correctly by default.

---

## Tested Versions

| Component | Version |
|-----------|---------|
| OpenShift | 4.20.8 |
| RHOAI | 3.3.1 |
| Red Hat Connectivity Link | 1.3.2 |
| cert-manager | 1.18.1 |
| LeaderWorkerSet | 1.0.0 |
