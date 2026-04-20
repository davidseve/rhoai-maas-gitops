# Troubleshooting

## ArgoCD app stuck in OutOfSync

Check which resource is failing:

```bash
oc get application <app-name> -n openshift-gitops \
  -o jsonpath='{.status.operationState.message}'
```

Common causes:

- **CRD not yet installed**: The operator hasn't created the CRD yet. ArgoCD retries automatically (up to 10-30 attempts with exponential backoff).
- **Namespace not found**: A resource targets a namespace that doesn't exist yet (e.g. `redhat-ods-applications` before DSC creates it). The `SkipDryRunOnMissingResource` sync option handles this.
- **Stuck retry with old revision**: If a new git push doesn't take effect because ArgoCD is still retrying the old revision, clear the operation and force refresh:

```bash
oc patch applications.argoproj.io <app-name> -n openshift-gitops --type merge -p '{"operation": null}'
oc annotate applications.argoproj.io <app-name> -n openshift-gitops argocd.argoproj.io/refresh=hard --overwrite
```

## MaaS token returns 401 on inference

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

## MaaS token returns 403 on inference

The tier ServiceAccount lacks `get` permission on the LLMInferenceService. Verify:

```bash
oc get role -n maas-models | grep maas-access
# Expected: tinyllama-test-maas-access (with get + post verbs)
```

The operator creates a Role with only `post`, but the AuthPolicy authorization checks `get`. The chart's `rbac.yaml` creates a Role with both verbs.

## Kuadrant AuthPolicy shows MissingDependency

See [KUADRANT-READINESS-HOOK.md](KUADRANT-READINESS-HOOK.md) for the full explanation and fix.

Quick fix:

```bash
oc delete pod -n kuadrant-system -l control-plane=controller-manager
```

## vLLM pod CrashLoopBackOff with "invalid option"

The CPU vLLM image (`vllm-cpu-openai-ubi9`) has `ENTRYPOINT ["/bin/bash", "-c"]`. If `command` is not overridden, the `args` are passed to `bash -c` as flags, producing:

```
/bin/bash: --: invalid option
```

The fix is to set `command: [python, -m, vllm.entrypoints.openai.api_server]` in the container spec. This is already done in the chart.

## vLLM pod stuck at 1/2 Ready

KServe injects HTTPS readiness probes (`https://:8000/health`), but vLLM serves plain HTTP by default. The logs show:

```
http: server gave HTTP response to HTTPS client
```

The fix is to add `--ssl-certfile` and `--ssl-keyfile` args pointing to the KServe-mounted TLS certs at `/var/run/kserve/tls/`. This is already done in the chart.

## DSC schema errors

The DSC CRD has two API versions with **different field names**:

| v1 field | v2 field |
| --- | --- |
| `datasciencepipelines` | `aipipelines` |
| `modelmeshserving` | *(removed)* |
| `codeflare` | *(removed)* |
| *(none)* | `mlflowoperator` |
| *(none)* | `trainer` |

This chart uses `apiVersion: v2`. If you see "field not declared in schema", verify you're using the v2 field names.
