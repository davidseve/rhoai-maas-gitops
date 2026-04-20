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

docs/
  ARCHITECTURE.md            # Architecture decisions and trade-offs
  GATEWAY-AND-ROUTE.md       # TLS termination strategies (passthrough vs reencrypt)
  KUADRANT-READINESS-HOOK.md # PostSync hook for Kuadrant MissingDependency race
  IN-CLUSTER-ACCESS.md       # Consuming models from inside the cluster
  TROUBLESHOOTING.md         # Common issues and fixes
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

---

## Documentation

| Topic | Link |
| --- | --- |
| Architecture decisions and trade-offs | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Gateway and Route TLS configuration | [docs/GATEWAY-AND-ROUTE.md](docs/GATEWAY-AND-ROUTE.md) |
| Kuadrant readiness hook | [docs/KUADRANT-READINESS-HOOK.md](docs/KUADRANT-READINESS-HOOK.md) |
| In-cluster access for agents | [docs/IN-CLUSTER-ACCESS.md](docs/IN-CLUSTER-ACCESS.md) |
| Troubleshooting | [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) |

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


