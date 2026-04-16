# Red Hat OpenShift AI - Models-as-a-Service (MaaS) GitOps

GitOps deployment of RHOAI 3.3 with Models-as-a-Service on OpenShift 4.20+, managed by ArgoCD.

Reference: [Official RHOAI 3.3 MaaS Documentation](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.3/html-single/govern_llm_access_with_models-as-a-service/index)

## Prerequisites

- OpenShift 4.20 or later
- Cluster admin access
- `oc` CLI installed and logged in
- OpenShift GitOps (ArgoCD) installed on the cluster

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

## Repository Structure

```
argocd/
  app-of-apps.yaml          # Root Application — deploy this one
  operators.yaml             # Child: operator subscriptions + CRs
  maas-platform.yaml         # Child: DSCI, DSC, Gateway, Route, Dashboard
  maas-model.yaml            # Child: model namespace + InferenceService

charts/
  operators/                 # Helm chart — prerequisite operators
  maas-platform/             # Helm chart — platform configuration
  maas-model/                # Helm chart — model deployment
```

| ArgoCD Application | Chart | What it deploys |
|---|---|---|
| `maas-gitops` | `argocd/` (app-of-apps) | Creates the 3 child Applications below |
| `maas-operators` | `charts/operators/` | RHOAI 3.3, Kuadrant, LeaderWorkerSet subscriptions + CRs |
| `maas-platform` | `charts/maas-platform/` | DSCInitialization, DataScienceCluster, Gateway, Route, DashboardConfig |
| `maas-model` | `charts/maas-model/` | Namespace, ServingRuntime, InferenceService (or LLMInferenceService) |

---

## Deployment with ArgoCD (recommended)

A single command deploys everything. ArgoCD handles operator installation, CRD availability, namespace creation, and retry logic automatically.

### Step 1: Log in to the cluster

```bash
oc login -u <admin-user> <api-server-url>
```

### Step 2: Deploy

```bash
oc apply -f https://raw.githubusercontent.com/davidseve/rhoai-maas-gitops/main/argocd/app-of-apps.yaml
```

That's it. ArgoCD will:

1. Create 3 child Applications (`maas-operators`, `maas-platform`, `maas-model`)
2. Install the prerequisite operators (RHOAI, Kuadrant, LeaderWorkerSet)
3. Wait for CRDs to become available (via retry with exponential backoff)
4. Create the DSCInitialization, DataScienceCluster, Gateway, Route, and DashboardConfig
5. Deploy the test model (TinyLlama on CPU with vLLM)

### Step 3: Monitor progress

```bash
# Watch ArgoCD applications
watch oc get applications -n openshift-gitops

# Expected final state (after ~6 minutes):
# maas-gitops      Synced   Healthy
# maas-operators   Synced   Healthy
# maas-platform    Synced   Healthy
# maas-model       Synced   Healthy
```

### Step 4: Verify

```bash
export CLUSTER_DOMAIN=$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')

# MaaS token
curl -sSk \
  -H "Authorization: Bearer $(oc whoami -t)" \
  -H "Content-Type: application/json" \
  -X POST -d '{"expiration":"10m"}' \
  "https://maas.${CLUSTER_DOMAIN}/maas-api/v1/tokens" | python3 -m json.tool
```

### Timeline

| Time | What happens |
|------|-------------|
| T+0s | `oc apply` of the app-of-apps |
| T+30s | 4 ArgoCD Applications created, operators installing |
| T+1m30s | Operators `Succeeded`, retries syncing CRs and platform |
| T+3m | DSCI + DSC + Gateway + Route applied |
| T+5m | DSC `Ready`, MaaS API running, model deploying |
| T+6m | All apps `Synced + Healthy`, model responding |

### Customizing for your cluster

The `clusterDomain` is set in `argocd/maas-platform.yaml`. To use a different cluster, edit that file before applying:

```yaml
# argocd/maas-platform.yaml
spec:
  source:
    helm:
      parameters:
        - name: clusterDomain
          value: apps.your-cluster.example.com   # <-- change this
```

The Gateway TLS secret is configured in `charts/maas-platform/values.yaml`:

```yaml
gateway:
  tlsSecretName: ingress-certs      # AWS clusters
  # tlsSecretName: router-certs-default  # bare-metal clusters
```

---

## Manual Deployment (without ArgoCD)

If you prefer to deploy without ArgoCD, use `helm template` + `oc apply` directly.

### Step 1: Log in and get cluster domain

```bash
oc login -u <admin-user> <api-server-url>
export CLUSTER_DOMAIN=$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')
```

### Step 2: Install prerequisite operators

```bash
helm template operators charts/operators/ | oc apply -f -
```

This installs:

| Operator | Package | Channel | Namespace |
|----------|---------|---------|-----------|
| Red Hat OpenShift AI 3.3 | `rhods-operator` | `fast-3.x` | `redhat-ods-operator` |
| Red Hat Connectivity Link (Kuadrant) | `rhcl-operator` | `stable` | `kuadrant-system` |
| LeaderWorkerSet | `leader-worker-set` | `stable-v1.0` | `leader-worker-set` |

The Kuadrant CR and LeaderWorkerSet CR require their CRDs to exist first. If the initial apply fails on these resources, wait and re-run:

```bash
# Wait for operators
oc get csv -n redhat-ods-operator | grep rhods     # Succeeded
oc get csv -n kuadrant-system | grep rhcl          # Succeeded
oc get csv -n leader-worker-set | grep leader      # Succeeded

# Re-apply to create CRs
helm template operators charts/operators/ | oc apply -f -

# Wait for Kuadrant
oc wait Kuadrant -n kuadrant-system kuadrant --for=condition=Ready --timeout=5m
```

If Kuadrant shows `MissingDependency` (Gateway API provider), restart its pod after RHOAI finishes installing Istio:

```bash
oc delete pod -n kuadrant-system -l app.kubernetes.io/name=kuadrant-operator
```

### Step 3: Deploy MaaS platform

```bash
helm template maas-platform charts/maas-platform/ \
  --set clusterDomain=$CLUSTER_DOMAIN \
  | oc apply -f -
```

Wait for DSC to be Ready:

```bash
oc get datasciencecluster default-dsc -o jsonpath='{.status.phase}'
# Expected: Ready

oc get pods -n redhat-ods-applications -l app.kubernetes.io/name=maas-api
# Expected: 1/1 Running
```

### Step 4: Deploy a model

```bash
helm template tinyllama charts/maas-model/ | oc apply -f -
```

### Step 5: Verify

```bash
HOST="https://maas.${CLUSTER_DOMAIN}"

curl -sSk \
  -H "Authorization: Bearer $(oc whoami -t)" \
  -H "Content-Type: application/json" \
  -X POST -d '{"expiration":"10m"}' \
  "${HOST}/maas-api/v1/tokens" | python3 -m json.tool
```

---

## Gateway and Route Configuration

The MaaS Gateway is exposed externally via an OpenShift Route. There are two TLS termination strategies.

### How TLS works in each mode

```
PASSTHROUGH:
  Client ──TLS──► OpenShift Router ──TLS (same)──► Gateway (Istio/Envoy)
  The router does NOT terminate TLS. It forwards encrypted traffic
  directly to the Gateway based on SNI.
  The Gateway's TLS certificate must match the external hostname.

REENCRYPT:
  Client ──TLS──► OpenShift Router ──new TLS──► Gateway (Istio/Envoy)
  The router terminates the client's TLS using its own wildcard cert,
  then opens a NEW TLS connection to the Gateway using a separate cert.
  The Gateway's cert does NOT need to match the external hostname.
```

### Option A: Passthrough (default)

TLS goes from the client directly to the Gateway. The OpenShift Router acts as a TCP proxy.

**Requirements:**
- The Gateway must use a TLS certificate matching `maas.<clusterDomain>`.
- This is typically the cluster's wildcard certificate (`*.apps.<clusterDomain>`).

```yaml
gateway:
  tlsSecretName: ingress-certs          # AWS
  # tlsSecretName: router-certs-default # bare-metal

route:
  tlsTermination: passthrough
```

**Wildcard certificate secret by platform:**

| Platform | Secret name | Notes |
|----------|-------------|-------|
| AWS (ROSA, IPI) | `ingress-certs` | Let's Encrypt or ACM cert |
| Bare-metal / UPI | `router-certs-default` | Self-signed or custom CA |
| Custom | `oc get secret -n openshift-ingress \| grep tls` | Check your cluster |

### Option B: Reencrypt

The OpenShift Router terminates external TLS and establishes a new TLS connection to the Gateway using a service-ca certificate.

```yaml
gateway:
  tlsSecretName: maas-gateway-service-tls

route:
  tlsTermination: reencrypt
```

**Additional step** (after Gateway Service is created):

```bash
oc annotate svc maas-default-gateway-data-science-gateway-class \
  -n openshift-ingress \
  service.beta.openshift.io/serving-cert-secret-name=maas-gateway-service-tls
```

### Decision guide

| Scenario | Mode | Gateway cert | Why |
|----------|------|-------------|-----|
| AWS with known wildcard cert | **passthrough** | `ingress-certs` | Simple, no extra steps |
| Bare-metal with `router-certs-default` | **passthrough** | `router-certs-default` | Simple |
| Unknown platform / multi-cluster | **reencrypt** | `maas-gateway-service-tls` | Platform-independent |

---

## Deploying a Model (`charts/maas-model`)

The `maas-model` chart supports two modes:

| Mode | Runtime | GPU Required | MaaS Integration | Use case |
|------|---------|--------------|-----------------|----------|
| `inferenceservice` (default) | vLLM CPU | No | No | CPU-only clusters, testing |
| `llminferenceservice` | llm-d (vLLM CUDA) | Yes | Yes (tiers, tokens, rate limits) | Production with GPUs |

### Mode A: InferenceService (CPU)

```bash
helm template my-model charts/maas-model/ | oc apply -f -
```

Deploys a vLLM CPU ServingRuntime + InferenceService. The model is accessible internally within the cluster. Authentication uses `kube-rbac-proxy` with OpenShift tokens.

### Mode B: LLMInferenceService (GPU, full MaaS)

```bash
helm template my-model charts/maas-model/ \
  --set mode=llminferenceservice \
  --set rbac.enabled=true \
  | oc apply -f -
```

Deploys an LLMInferenceService with llm-d runtime. The model registers in MaaS and supports tier-based access tokens.

**Important:** The llm-d runtime uses a CUDA (GPU) image and will **not** work on CPU-only clusters.

### Customizing the model

```bash
helm template my-model charts/maas-model/ \
  --set global.name=my-llama \
  --set global.namespace=my-project \
  --set model.storageUri="oci://quay.io/my-org/my-model:v1" \
  --set model.servedName=my-llama \
  --set model.maxModelLen=4096 \
  --set resources.requests.cpu=4 \
  --set resources.requests.memory=8Gi \
  | oc apply -f -
```

Add model namespaces to the Gateway via `charts/maas-platform/values.yaml`:

```yaml
gateway:
  modelNamespaces:
    - maas-models
    - my-project
```

---

## Troubleshooting

### ArgoCD app stuck in OutOfSync

Check which resource is failing:

```bash
oc get application <app-name> -n openshift-gitops \
  -o jsonpath='{.status.operationState.message}'
```

Common causes:
- **CRD not yet installed**: The operator hasn't created the CRD yet. ArgoCD retries automatically (up to 30 attempts with exponential backoff).
- **Namespace not found**: A resource targets a namespace that doesn't exist yet (e.g. `redhat-ods-applications` before DSC creates it). The `SkipDryRunOnMissingResource` sync option handles this.

### MaaS component not ready

```bash
oc get datasciencecluster default-dsc -o yaml | grep -A5 ModelsAsServiceReady
```

- **"gateway not found"**: The `maas-default-gateway` must exist in `openshift-ingress` before enabling MaaS.
- **"DeploymentsNotReady"**: Wait 2-3 minutes for the maas-api pod to start.

### Kuadrant not ready

```bash
oc get kuadrant kuadrant -n kuadrant-system -o jsonpath='{.status.conditions}'
```

- **"MissingDependency" (Gateway API provider)**: RHOAI has not finished installing Istio. Wait for RHOAI, then restart the Kuadrant operator pod.

### LeaderWorkerSet CRD missing

If `LLMInferenceService` shows `ReconcileMultiNodeWorkloadError`, the LWS operator needs its CR:

```bash
oc get crd leaderworkersets.leaderworkerset.x-k8s.io
```

The `charts/operators/` chart includes the `LeaderWorkerSetOperator` CR. If it wasn't applied (CRD not ready on first pass), ArgoCD retries automatically.

### Route returns "Application is not available"

1. The Gateway Service exists: `oc get svc -n openshift-ingress | grep maas`
2. For reencrypt: `oc get secret maas-gateway-service-tls -n openshift-ingress`
3. For passthrough: verify `gateway.tlsSecretName` matches the wildcard cert

### DSC schema errors

The DSC CRD has two API versions with **different field names**:

| v1 field | v2 field |
|----------|----------|
| `datasciencepipelines` | `aipipelines` |
| `modelmeshserving` | _(removed)_ |
| `codeflare` | _(removed)_ |
| _(none)_ | `mlflowoperator` |
| _(none)_ | `trainer` |

This chart uses `apiVersion: v2`. If you see "field not declared in schema", verify you're using the v2 field names.

---

## Tested Versions

| Component | Version |
|-----------|---------|
| OpenShift | 4.20.8 |
| RHOAI | 3.3.1 |
| Red Hat Connectivity Link | 1.3.2 |
| cert-manager | 1.18.1 (pre-installed) |
| LeaderWorkerSet | 1.0.0 |
| OpenShift GitOps (ArgoCD) | 1.20.1 |
