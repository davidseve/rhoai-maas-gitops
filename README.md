# Red Hat OpenShift AI - Models-as-a-Service (MaaS) GitOps

GitOps deployment of RHOAI 3.4 EA1 with Models-as-a-Service on OpenShift 4.20+, managed by ArgoCD.

Reference: [Official RHOAI MaaS Documentation](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.4/html-single/govern_llm_access_with_models-as-a-service/index)

## What changed from 3.3 to 3.4

RHOAI 3.4 EA1 introduces the **maas-controller**, which automates much of the MaaS governance stack. This replaces several manual workarounds from the 3.3 branch:

| 3.3 (manual) | 3.4 (automatic) |
| --- | --- |
| GatewayClass template | Created by RHOAI operator (`data-science-gateway-class`) |
| OpenShift Groups for tiers | Tier namespaces created automatically (`maas-default-gateway-tier-*`) |
| tier-to-group-mapping ConfigMap | Created by maas-controller |
| RBAC Role + RoleBinding per model | Auto-created from `alpha.maas.opendatahub.io/tiers` annotation |
| RateLimitPolicy per tier | Not yet automated in EA1 (planned for GA) |
| TokenRateLimitPolicy per tier | Not yet automated in EA1 (planned for GA) |
| TelemetryPolicy | Not yet automated in EA1 (planned for GA) |
| Limitador exhaustiveTelemetry patch | Not yet automated in EA1 |
| gateway-auth-policy (full governance) | Created by maas-controller (Overridden -- see workaround below) |
| PostSync hook to patch AuthPolicy | Replaced by route-level AuthPolicy (see below) |
| maas-api deployment | Deployed automatically by the maas-controller |

### Remaining workaround: route-level AuthPolicy

The `odh-model-controller` creates `maas-default-gateway-authn` AuthPolicy at Gateway level with audience `https://kubernetes.default.svc` and verb `get`. The maas-controller creates `gateway-auth-policy` with the correct audience (`maas-default-gateway-sa`) and verb (`post`), but it gets `Overridden` by the former. The odh-model-controller reconciles continuously, reverting patches.

The solution is a **route-level AuthPolicy** (`authpolicy-patch.yaml`) that targets the model's HTTPRoute directly. HTTPRoute-level policies take precedence over Gateway-level policies in Kuadrant, so this cleanly resolves the conflict without fighting either controller.

## Prerequisites

- OpenShift 4.20 or later
- Cluster admin access
- `oc` CLI installed and logged in
- OpenShift GitOps (ArgoCD) installed on the cluster

## Architecture

```
                                      +-------------------------------------+
                                      |         OpenShift Cluster           |
   Client                             |                                     |
     |                                |  +----------+    +--------------+  |
     |  TLS                           |  | OpenShift |    |   Gateway    |  |
     +------>  Route (passthrough) ---+--+ Router    +--->| (Istio/Envoy)|  |
     |                                |  +----------+    +------+-------+  |
     |                                |                         |          |
     |                                |              +----------+--------+ |
     |                                |              |   AuthPolicy      | |
     |                                |              |   (route-level)   | |
     |                                |              |  - MaaS token     | |
     |                                |              |  - Tier RBAC      | |
     |                                |              +----------+--------+ |
     |                                |                         |          |
     |                                |     +-------------------+          |
     |                                |     |                   |          |
     |                                |  +--v------+    +-------v------+  |
     |                                |  | MaaS API |    |  Model Pod   |  |
     |                                |  | (auto)   |    |  (vLLM CPU)  |  |
     |                                |  +----------+    +--------------+  |
     |                                +-------------------------------------+
```

## Repository Structure

```
argocd/
  app-of-apps.yaml          # Root Application -- deploy this one
  apps/templates/
    operators.yaml           # Wave 0: operator subscriptions + CRs
    maas-platform.yaml       # Wave 1: DSCI, DSC, Gateway, Route, Dashboard
    maas-model.yaml          # Wave 2: LLMInferenceService + route-level AuthPolicy

charts/
  operators/                 # Helm chart -- prerequisite operators
  maas-platform/             # Helm chart -- platform config + Kuadrant readiness hook
  maas-model/                # Helm chart -- model deployment
```

| ArgoCD Application | Wave | Chart | What it deploys |
| --- | --- | --- | --- |
| `maas-gitops` | -- | `argocd/` (app-of-apps) | Creates the 3 child Applications below |
| `maas-operators` | 0 | `charts/operators/` | RHOAI 3.4 EA1 (beta channel), RHCL, LWS subscriptions + CRs |
| `maas-platform` | 1 | `charts/maas-platform/` | DSCInitialization, DSC, Gateway, Route, DashboardConfig, Kuadrant readiness hook |
| `maas-model` | 2 | `charts/maas-model/` | Namespace, LLMInferenceService, route-level AuthPolicy |

Sync-waves ensure ordered deployment: operators install first (wave 0), then platform resources that depend on operator CRDs (wave 1), then the model that depends on KServe and the Gateway (wave 2).

---

## Deployment with ArgoCD (recommended)

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

ArgoCD will:

1. Create 3 child Applications in wave order (0 -> 1 -> 2)
2. **Wave 0:** Install RHOAI 3.4 EA1, RHCL, and LWS operators; create Kuadrant + LWS CRs
3. **Wave 1:** Create DSCInitialization, DataScienceCluster (with `modelsAsService: Managed`), Gateway, Route
4. **Wave 1 PostSync:** Run the Kuadrant readiness hook (auto-restarts operator if stuck in `MissingDependency`)
5. **Wave 2:** Create the model namespace, deploy LLMInferenceService, apply route-level AuthPolicy

### Step 4: Monitor progress

```bash
watch oc get applications.argoproj.io -n openshift-gitops
```

### Step 5: Verify

```bash
oc get llminferenceservice -n maas-models
# NAME             READY   REASON
# tinyllama-test   True

oc get pods -n maas-models
# tinyllama-test-kserve-...   2/2   Running

oc get authpolicy -A -o custom-columns='NAME:.metadata.name,NS:.metadata.namespace,ENFORCED:.status.conditions[?(@.type=="Enforced")].status'
# tinyllama-test-maas-auth   maas-models     True
# maas-api-auth-policy       redhat-ods...   True
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

---

## What the model chart deploys

The `maas-model` chart deploys a complete MaaS-integrated model using `LLMInferenceService`.

### Resources created

| Resource | Name | Purpose |
| --- | --- | --- |
| `Namespace` | `maas-models` | Dedicated namespace for model workloads |
| `LLMInferenceService` | `tinyllama-test` | Model with CPU vLLM, registered in MaaS with tier annotations |
| `AuthPolicy` | `tinyllama-test-maas-auth` | Route-level auth with correct MaaS audience and RBAC verb |
| `ServiceMonitor` | `limitador-metrics` | Scrapes Limitador metrics |
| `PrometheusRule` | `maas-alerts` | Alerts for Limitador health and high rejection rates |

### Auto-created resources (by maas-controller and odh-model-controller)

| Resource | Name | Created by |
| --- | --- | --- |
| `Role` | `tinyllama-test-model-post-access` | odh-model-controller (from tiers annotation) |
| `RoleBinding` | `tinyllama-test-model-post-access-tier-binding` | odh-model-controller |
| `HTTPRoute` | `tinyllama-test-kserve-route` | KServe |
| `AuthPolicy` | `gateway-auth-policy` | maas-controller (Overridden at Gateway level) |
| `AuthPolicy` | `maas-default-gateway-authn` | odh-model-controller (Overridden at Gateway level) |
| `AuthPolicy` | `maas-api-auth-policy` | maas-controller (Enforced at HTTPRoute level) |
| Tier namespace | `maas-default-gateway-tier-free` | maas-controller |
| ConfigMap | `tier-to-group-mapping` | maas-controller |

### CPU vLLM override

The default `LLMInferenceService` runtime uses a GPU image. For CPU-only clusters, the chart overrides the container image and entrypoint:

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

### Customizing the model

Edit `charts/maas-model/values.yaml`:

```yaml
global:
  name: my-llama
  namespace: my-project

model:
  storageUri: "oci://quay.io/my-org/my-model:v1"
  servedName: my-llama
  maxModelLen: 4096
```

Add the model namespace to the Gateway's allowed routes in `charts/maas-platform/values.yaml`:

```yaml
gateway:
  modelNamespaces:
    - maas-models
    - my-project
```

---

## Gateway and Route Configuration

The MaaS Gateway is a **prerequisite** for `modelsAsService` in the DataScienceCluster. The DSC will not reconcile until the Gateway exists. The chart creates it in `charts/maas-platform/templates/gateway.yaml`.

The Gateway is exposed externally via an OpenShift Route. The default mode is passthrough.

### Passthrough (default)

```yaml
gateway:
  tlsSecretName: ingress-certs          # AWS
  # tlsSecretName: router-certs-default # bare-metal

route:
  tlsTermination: passthrough
```

---

## Manual Deployment (without ArgoCD)

### Step 1: Login and get cluster domain

```bash
oc login -u <admin-user> <api-server-url>
export CLUSTER_DOMAIN=$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')
```

### Step 2: Install operators

```bash
helm template operators charts/operators/ | oc apply -f -
```

Wait for operators:

```bash
oc get csv -n redhat-ods-operator | grep rhods     # 3.4.0-ea.1 Succeeded
oc get csv -n kuadrant-system | grep rhcl          # 1.3.x Succeeded
```

If Kuadrant shows `MissingDependency`:

```bash
oc delete pod -n kuadrant-system -l control-plane=controller-manager
```

### Step 3: Deploy platform

```bash
helm template maas-platform charts/maas-platform/ \
  --set clusterDomain=$CLUSTER_DOMAIN \
  | oc apply -f -
```

Wait for DSC:

```bash
oc get datasciencecluster default-dsc -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'
# True
```

### Step 4: Deploy model

```bash
helm template tinyllama charts/maas-model/ | oc apply -f -
```

### Step 5: Verify

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

### MaaS token returns 401 on inference

Check that the route-level AuthPolicy is Enforced:

```bash
oc get authpolicy -A -o custom-columns='NAME:.metadata.name,TARGET:.spec.targetRef.kind,ENFORCED:.status.conditions[?(@.type=="Enforced")].status'
```

The `tinyllama-test-maas-auth` (HTTPRoute-level) should be `Enforced: True`. If not, verify the HTTPRoute exists:

```bash
oc get httproute -n maas-models
```

### MaaS token returns 403 on inference

The tier SA lacks `post` permission. Verify auto-created RBAC:

```bash
oc get role,rolebinding -n maas-models
```

The annotation `alpha.maas.opendatahub.io/tiers` on the LLMInferenceService triggers automatic RBAC creation.

### Kuadrant AuthPolicy shows MissingDependency

RHOAI hasn't finished deploying Istio when Kuadrant started.

With ArgoCD, this is handled by the PostSync readiness hook. Without ArgoCD:

```bash
oc delete pod -n kuadrant-system -l control-plane=controller-manager
```

### DSC shows ModelsAsServiceReady: Error (gateway not found)

The Gateway must exist before enabling `modelsAsService: Managed` in the DSC. Verify:

```bash
oc get gateway -n openshift-ingress maas-default-gateway
```

If missing, deploy the platform chart first (Step 3 above).

---

## Tested Versions

| Component | Version |
| --- | --- |
| OpenShift | 4.20.8 |
| RHOAI | 3.4.0-ea.1 (beta channel) |
| Red Hat Connectivity Link | 1.3.2 |
| cert-manager | 1.18.1 (pre-installed) |
| LeaderWorkerSet | 1.0.0 |
| OpenShift GitOps (ArgoCD) | 1.20.1 |
