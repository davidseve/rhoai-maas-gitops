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
     │                                │  │ (tokens, │    │  (vLLM CPU)  │  │
     │                                │  │  tiers)  │    │              │  │
     │                                │  └──────────┘    └──────────────┘  │
     │                                └─────────────────────────────────────┘
```

## Repository Structure

```
argocd/
  app-of-apps.yaml          # Root Application — deploy this one
  operators.yaml             # Wave 0: operator subscriptions + CRs
  maas-platform.yaml         # Wave 1: DSCI, DSC, Gateway, Route, Dashboard
  maas-model.yaml            # Wave 2: LLMInferenceService, RBAC, AuthPolicy

charts/
  operators/                 # Helm chart — prerequisite operators
  maas-platform/             # Helm chart — platform config + Kuadrant readiness hook
  maas-model/                # Helm chart — model deployment
```


| ArgoCD Application | Wave | Chart                   | What it deploys                                                        |
| ------------------ | ---- | ----------------------- | ---------------------------------------------------------------------- |
| `maas-gitops`      | —    | `argocd/` (app-of-apps) | Creates the 3 child Applications below                                 |
| `maas-operators`   | 0    | `charts/operators/`     | RHOAI 3.3, Kuadrant, LeaderWorkerSet subscriptions + CRs               |
| `maas-platform`    | 1    | `charts/maas-platform/` | DSCInitialization, DSC, Gateway, Route, DashboardConfig, Kuadrant readiness hook |
| `maas-model`       | 2    | `charts/maas-model/`    | Namespace, LLMInferenceService, RBAC, AuthPolicy fix                             |


Sync-waves ensure ordered deployment: operators install first (wave 0), then platform resources that depend on operator CRDs (wave 1), then the model that depends on KServe and the Gateway (wave 2).

---

## Deployment with ArgoCD (recommended)

A single command deploys everything. ArgoCD handles operator installation, CRD availability, namespace creation, and retry logic automatically.

### Step 1: Log in to the cluster

```bash
oc login -u <admin-user> <api-server-url>
```

### Step 2: Update cluster domain

Edit `argocd/app-of-apps.yaml` and set your cluster domain:

```yaml
spec:
  source:
    helm:
      parameters:
        - name: clusterDomain
          value: apps.your-cluster.example.com   # <-- change this
```

### Step 3: Deploy

```bash
oc apply -f argocd/app-of-apps.yaml
```

That's it. ArgoCD will:

1. Create 3 child Applications in wave order (0 → 1 → 2)
2. **Wave 0:** Install RHOAI, Kuadrant, and LeaderWorkerSet operators; wait for CRDs; create operator CRs
3. **Wave 1:** Create DSCInitialization, DataScienceCluster, Gateway, Route, and DashboardConfig
4. **Wave 1 PostSync:** Run the Kuadrant readiness hook (auto-restarts operator if stuck in `MissingDependency`)
5. **Wave 2:** Create the model namespace, deploy LLMInferenceService with CPU vLLM, configure RBAC and fix the AuthPolicy audience

### Step 4: Monitor progress

```bash
watch oc get applications.argoproj.io -n openshift-gitops

# Expected final state (after ~3-5 minutes):
# maas-gitops      Synced   Healthy
# maas-operators   Synced   Healthy
# maas-platform    Synced   Healthy
# maas-model       Synced   Healthy
```

### Step 5: Verify

```bash
# Check the model is ready
oc get llminferenceservice -n maas-models
# NAME             READY   REASON
# tinyllama-test   True

# Check the pod is running
oc get pods -n maas-models
# tinyllama-test-kserve-...   2/2   Running
```

### Step 6: Test MaaS token + inference

```bash
MAAS_HOST="maas.$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')"

# Generate a MaaS token (10 min expiry)
MAAS_TOKEN=$(curl -sk \
  -X POST "https://$MAAS_HOST/maas-api/v1/tokens" \
  -H "Authorization: Bearer $(oc whoami -t)" \
  -H "Content-Type: application/json" \
  -d '{"expiration":"10m"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

# Inference with the MaaS token
curl -sk "https://$MAAS_HOST/maas-models/tinyllama-test/v1/chat/completions" \
  -H "Authorization: Bearer $MAAS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"tinyllama-test","messages":[{"role":"user","content":"What is OpenShift?"}],"max_tokens":50}' \
  | python3 -m json.tool
```

### Timeline


| Time    | What happens                                                        |
| ------- | ------------------------------------------------------------------- |
| T+0s    | `oc apply` of the app-of-apps                                       |
| T+30s   | Wave 0: operator namespaces and subscriptions created               |
| T+1m30s | Wave 0: operators installed, CRs created (Kuadrant, LWS)            |
| T+2m    | Wave 1: DSCI + DSC + Gateway + Route applied                        |
| T+2m30s | Wave 1 PostSync: readiness hook checks Kuadrant, restarts if needed |
| T+3m    | Wave 2: LLMInferenceService + RBAC + AuthPolicy applied             |
| T+3-5m  | All apps `Synced + Healthy`, model responding                       |


### Kuadrant readiness hook (automatic)

The Kuadrant operator frequently starts before KServe finishes deploying Istio (the Gateway API provider). When this happens, Kuadrant enters a `MissingDependency` state and never recovers on its own — all AuthPolicies remain `Accepted: False`, breaking MaaS token generation and inference.

The `maas-platform` chart includes an ArgoCD **PostSync hook** (`kuadrant-readiness-hook.yaml`) that runs automatically after every sync:

1. Waits for the Kuadrant CR to exist
2. Polls the `Ready` condition up to 30 times (5 minutes total)
3. If `Ready=True` → exits successfully (nothing to do)
4. If `reason=MissingDependency` → restarts the Kuadrant operator pod, then waits for reconciliation
5. If Kuadrant never becomes ready → the Job fails, and ArgoCD marks the sync as `PostSync Failed`

The hook creates its own `ServiceAccount`, `ClusterRole`, and `ClusterRoleBinding` scoped to only the permissions it needs (`get` on Kuadrant CRs, `list`/`delete` on pods). All hook resources use `argocd.argoproj.io/hook-delete-policy: BeforeHookCreation` so they are cleaned up on the next sync.

In manual deployments (without ArgoCD), this hook does not run. Instead, restart the operator manually if you see `MissingDependency`:

```bash
oc delete pod -n kuadrant-system -l control-plane=controller-manager
```

---

## What the model chart deploys

The `maas-model` chart (`charts/maas-model/`) deploys a complete MaaS-integrated model using `LLMInferenceService`. This is the KServe CRD that provides native MaaS integration with tier-based access, token authentication, and Gateway routing.

### Resources created


| Resource              | Name                         | Purpose                                               |
| --------------------- | ---------------------------- | ----------------------------------------------------- |
| `Namespace`           | `maas-models`                | Dedicated namespace for model workloads               |
| `LLMInferenceService` | `tinyllama-test`             | Model deployment with CPU vLLM, registered in MaaS    |
| `Role`                | `tinyllama-test-maas-access` | Allows `get` + `post` on the LLMInferenceService      |
| `RoleBinding`         | `tinyllama-test-maas-access` | Binds the role to tier ServiceAccount groups          |
| `AuthPolicy`          | `maas-default-gateway-authn` | Fixes the audience list for MaaS token authentication |


### CPU vLLM override

The default `LLMInferenceService` runtime (`llm-d`) uses a GPU (CUDA) image. For CPU-only clusters, the chart overrides the container image and entrypoint:

```yaml
template:
  containers:
  - name: main
    image: quay.io/rh-aiservices-bu/vllm-cpu-openai-ubi9:0.3
    command: [python, -m, vllm.entrypoints.openai.api_server]
    args:
    - --model
    - /mnt/models
    - --port
    - "8000"
    - --ssl-certfile
    - /var/run/kserve/tls/tls.crt
    - --ssl-keyfile
    - /var/run/kserve/tls/tls.key
    - --max-model-len
    - "2048"
    - --served-model-name
    - tinyllama-test
```

Key details:

- `**command` override**: The CPU image has `ENTRYPOINT ["/bin/bash", "-c"]`, so we must set `command` explicitly to invoke vLLM directly.
- **TLS args**: KServe injects HTTPS readiness/liveness probes. vLLM must serve TLS using the certificates mounted at `/var/run/kserve/tls/` by the KServe operator.

### AuthPolicy governance patch (PostSync hook)

The `odh-model-controller` creates `maas-default-gateway-authn` automatically when a Gateway + LLMInferenceService exist. This basic AuthPolicy only includes `https://kubernetes.default.svc` as audience — missing `maas-default-gateway-sa` required by MaaS tokens.

Additionally, for the governance stack, this AuthPolicy needs:
- Tier resolution via HTTP metadata call to `maas-api`
- Response filters injecting `tier` and `userid` for rate limiting
- Custom authorization with SubjectAccessReview

The chart includes a **PostSync hook** (`cleanup-authn-hook.yaml`) that patches `maas-default-gateway-authn` after every ArgoCD sync with the complete governance configuration. This approach works because:
- Deleting the policy is not viable (`odh-model-controller` recreates it immediately)
- The `opendatahub.io/managed: "false"` annotation is ignored in RHOAI 3.3.1
- Patching preserves the controller's ownership while adding governance fields

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for full technical details.

### RBAC for tier access

The operator auto-creates a Role with only the `post` verb, but the Gateway AuthPolicy checks `get` access via `kubernetesSubjectAccessReview`. The chart creates a Role with both `get` and `post` verbs, bound to the tier ServiceAccount groups:

- `system:serviceaccounts:maas-default-gateway-tier-free`
- `system:serviceaccounts:maas-default-gateway-tier-premium`
- `system:serviceaccounts:maas-default-gateway-tier-enterprise`

### Customizing the model

Edit `charts/maas-model/values.yaml`:

```yaml
global:
  name: my-llama              # Model name
  namespace: my-project        # Target namespace

model:
  storageUri: "oci://quay.io/my-org/my-model:v1"
  servedName: my-llama
  maxModelLen: 4096

images:
  vllm:
    repository: "quay.io/rh-aiservices-bu/vllm-cpu-openai-ubi9"
    tag: "0.3"

resources:
  requests:
    cpu: "4"
    memory: "8Gi"
  limits:
    cpu: "16"
    memory: "16Gi"
```

For GPU clusters, change the image to a CUDA-based vLLM and remove the `command` override in the template.

Add the model namespace to the Gateway's allowed routes in `charts/maas-platform/values.yaml`:

```yaml
gateway:
  modelNamespaces:
    - maas-models
    - my-project
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


| Platform         | Secret name                                     | Notes                     |
| ---------------- | ----------------------------------------------- | ------------------------- |
| AWS (ROSA, IPI)  | `ingress-certs`                                 | Let's Encrypt or ACM cert |
| Bare-metal / UPI | `router-certs-default`                          | Self-signed or custom CA  |
| Custom           | `oc get secret -n openshift-ingress | grep tls` | Check your cluster        |


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


| Scenario                               | Mode            | Gateway cert               | Why                    |
| -------------------------------------- | --------------- | -------------------------- | ---------------------- |
| AWS with known wildcard cert           | **passthrough** | `ingress-certs`            | Simple, no extra steps |
| Bare-metal with `router-certs-default` | **passthrough** | `router-certs-default`     | Simple                 |
| Unknown platform / multi-cluster       | **reencrypt**   | `maas-gateway-service-tls` | Platform-independent   |


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


| Operator                             | Package             | Channel       | Namespace             |
| ------------------------------------ | ------------------- | ------------- | --------------------- |
| Red Hat OpenShift AI 3.3             | `rhods-operator`    | `fast-3.x`    | `redhat-ods-operator` |
| Red Hat Connectivity Link (Kuadrant) | `rhcl-operator`     | `stable`      | `kuadrant-system`     |
| LeaderWorkerSet                      | `leader-worker-set` | `stable-v1.0` | `leader-worker-set`   |


The Kuadrant CR and LeaderWorkerSet CR require their CRDs to exist first. If the initial apply fails on these resources, wait and re-run:

```bash
# Wait for operators
oc get csv -n redhat-ods-operator | grep rhods     # Succeeded
oc get csv -n kuadrant-system | grep rhcl          # Succeeded
oc get csv -n leader-worker-set | grep leader      # Succeeded

# Re-apply to create CRs
helm template operators charts/operators/ | oc apply -f -
```

If Kuadrant shows `MissingDependency` (Gateway API provider), restart its pod after RHOAI finishes installing Istio. This is the same race condition that the ArgoCD PostSync hook handles automatically:

```bash
oc delete pod -n kuadrant-system -l control-plane=controller-manager
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
```

### Step 4: Deploy a model

```bash
helm template tinyllama charts/maas-model/ | oc apply -f -
```

### Step 5: Patch AuthPolicy (manual only)

When deploying without ArgoCD, you must manually patch `maas-default-gateway-authn` to add the MaaS audience and governance fields:

```bash
oc patch authpolicy maas-default-gateway-authn -n openshift-ingress \
  --type=merge -p '{"spec":{"rules":{"authentication":{"service-accounts":{"kubernetesTokenReview":{"audiences":["https://kubernetes.default.svc","maas-default-gateway-sa"]}}}}}}'
```

This is needed because `odh-model-controller` creates this AuthPolicy with only `https://kubernetes.default.svc` as audience. MaaS tokens use audience `maas-default-gateway-sa`, so without this patch, inference returns `401`.

For full governance (tier resolution, per-user rate limiting), see the complete patch in `charts/maas-model/templates/cleanup-authn-hook.yaml`.

> **Note:** With ArgoCD deployment, the PostSync hook applies this patch automatically on every sync.

### Step 6: Verify

```bash
MAAS_HOST="maas.${CLUSTER_DOMAIN}"

# Generate token
curl -sk -X POST "https://$MAAS_HOST/maas-api/v1/tokens" \
  -H "Authorization: Bearer $(oc whoami -t)" \
  -H "Content-Type: application/json" \
  -d '{"expiration":"10m"}' | python3 -m json.tool
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

- **CRD not yet installed**: The operator hasn't created the CRD yet. ArgoCD retries automatically (up to 10–30 attempts with exponential backoff).
- **Namespace not found**: A resource targets a namespace that doesn't exist yet (e.g. `redhat-ods-applications` before DSC creates it). The `SkipDryRunOnMissingResource` sync option handles this.
- **Stuck retry with old revision**: If a new git push doesn't take effect because ArgoCD is still retrying the old revision, clear the operation and force refresh:

```bash
oc patch applications.argoproj.io <app-name> -n openshift-gitops --type merge -p '{"operation": null}'
oc annotate applications.argoproj.io <app-name> -n openshift-gitops argocd.argoproj.io/refresh=hard --overwrite
```

### MaaS token returns 401 on inference

The AuthPolicy `maas-default-gateway-authn` may be missing the `maas-default-gateway-sa` audience. Verify:

```bash
oc get authpolicy maas-default-gateway-authn -n openshift-ingress \
  -o jsonpath='{.spec.rules.authentication.kubernetes-user.kubernetesTokenReview.audiences}'
# Expected: ["https://kubernetes.default.svc","maas-default-gateway-sa"]
```

If the audience is missing, patch it:

```bash
oc patch authpolicy maas-default-gateway-authn -n openshift-ingress \
  --type=merge -p '{"spec":{"rules":{"authentication":{"kubernetes-user":{"kubernetesTokenReview":{"audiences":["https://kubernetes.default.svc","maas-default-gateway-sa"]}}}}}}'
```

With ArgoCD, the `authpolicy-patch.yaml` template applies this fix automatically. If the operator reverts it, ArgoCD's `selfHeal` will re-apply.

### MaaS token returns 403 on inference

The tier ServiceAccount lacks `get` permission on the LLMInferenceService. Verify:

```bash
oc get role -n maas-models | grep maas-access
# Expected: tinyllama-test-maas-access (with get + post verbs)
```

The operator creates a Role with only `post`, but the AuthPolicy authorization checks `get`. The chart's `rbac.yaml` creates a Role with both verbs.

### Kuadrant AuthPolicy shows MissingDependency

```bash
oc get authpolicy -n openshift-ingress -o jsonpath='{range .items[*]}{.metadata.name}: {.status.conditions[0].reason}{"\n"}{end}'
```

If it shows `MissingDependency` for "Gateway API provider (istio / envoy gateway)", the RHOAI operator hasn't finished deploying Istio when Kuadrant started.

**With ArgoCD:** This is handled automatically by the PostSync readiness hook in the `maas-platform` chart. Check the hook Job status:

```bash
oc get job kuadrant-readiness-check -n kuadrant-system
oc logs job/kuadrant-readiness-check -n kuadrant-system
```

If the hook Job failed or is not present, restart the Kuadrant operator manually:

```bash
oc delete pod -n kuadrant-system -l control-plane=controller-manager
```

**Without ArgoCD:** Wait for the DSC to be `Ready`, then restart the Kuadrant operator manually with the command above.

### vLLM pod CrashLoopBackOff with "invalid option"

The CPU vLLM image (`vllm-cpu-openai-ubi9`) has `ENTRYPOINT ["/bin/bash", "-c"]`. If `command` is not overridden, the `args` are passed to `bash -c` as flags, producing:

```
/bin/bash: --: invalid option
```

The fix is to set `command: [python, -m, vllm.entrypoints.openai.api_server]` in the container spec. This is already done in the chart.

### vLLM pod stuck at 1/2 Ready

KServe injects HTTPS readiness probes (`https://:8000/health`), but vLLM serves plain HTTP by default. The logs show:

```
http: server gave HTTP response to HTTPS client
```

The fix is to add `--ssl-certfile` and `--ssl-keyfile` args pointing to the KServe-mounted TLS certs at `/var/run/kserve/tls/`. This is already done in the chart.

### DSC schema errors

The DSC CRD has two API versions with **different field names**:


| v1 field               | v2 field         |
| ---------------------- | ---------------- |
| `datasciencepipelines` | `aipipelines`    |
| `modelmeshserving`     | *(removed)*      |
| `codeflare`            | *(removed)*      |
| *(none)*               | `mlflowoperator` |
| *(none)*               | `trainer`        |


This chart uses `apiVersion: v2`. If you see "field not declared in schema", verify you're using the v2 field names.

---

## Tested Versions


| Component                 | Version                |
| ------------------------- | ---------------------- |
| OpenShift                 | 4.20.8                 |
| RHOAI                     | 3.3.1                  |
| Red Hat Connectivity Link | 1.3.2                  |
| cert-manager              | 1.18.1 (pre-installed) |
| LeaderWorkerSet           | 1.0.0                  |
| OpenShift GitOps (ArgoCD) | 1.20.1                 |


