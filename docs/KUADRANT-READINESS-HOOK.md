# Kuadrant Readiness Hook

The Kuadrant operator frequently starts before KServe finishes deploying Istio (the Gateway API provider). When this happens, Kuadrant enters a `MissingDependency` state and never recovers on its own -- all AuthPolicies remain `Accepted: False`, breaking MaaS token generation and inference.

## How it works

The `maas-platform` chart includes an ArgoCD **PostSync hook** (`kuadrant-readiness-hook.yaml`) that runs automatically after every sync:

1. Waits for the Kuadrant CR to exist
2. Polls the `Ready` condition up to 30 times (5 minutes total)
3. If `Ready=True` -- exits successfully (nothing to do)
4. If `reason=MissingDependency` -- restarts the Kuadrant operator pod, then waits for reconciliation
5. If Kuadrant never becomes ready -- the Job fails, and ArgoCD marks the sync as `PostSync Failed`

## RBAC and cleanup

The hook creates its own `ServiceAccount`, `ClusterRole`, and `ClusterRoleBinding` scoped to only the permissions it needs (`get` on Kuadrant CRs, `list`/`delete` on pods). All hook resources use `argocd.argoproj.io/hook-delete-policy: BeforeHookCreation` so they are cleaned up on the next sync.

## Manual deployments (without ArgoCD)

The hook does not run when deploying with `helm template`. Instead, restart the operator manually if you see `MissingDependency`:

```bash
oc delete pod -n kuadrant-system -l control-plane=controller-manager
```

## Diagnosing the issue

```bash
oc get authpolicy -n openshift-ingress \
  -o jsonpath='{range .items[*]}{.metadata.name}: {.status.conditions[0].reason}{"\n"}{end}'
```

If it shows `MissingDependency` for "Gateway API provider (istio / envoy gateway)", restart the operator as above.

Check the hook Job status (ArgoCD deployments):

```bash
oc get job kuadrant-readiness-check -n kuadrant-system
oc logs job/kuadrant-readiness-check -n kuadrant-system
```
