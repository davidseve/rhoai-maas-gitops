# MaaS Governance Stack — Architecture Notes

## GatewayClass Decision

This deployment uses `gatewayClassName: openshift-default` instead of the RHOAI-provided `data-science-gateway-class`.

Both GatewayClasses use the same controller (`openshift.io/gateway-controller/v1`) and produce identical Gateway behavior. The choice is driven by how the `odh-model-controller` behaves:

| GatewayClass                   | `odh-model-controller` behavior                  |
| ------------------------------ | ------------------------------------------------ |
| `data-science-gateway-class`   | Creates `maas-default-gateway-authn` AuthPolicy  |
| `openshift-default`            | Still creates `maas-default-gateway-authn`       |

**Finding:** In RHOAI 3.3.1, `odh-model-controller` creates the conflicting `maas-default-gateway-authn` AuthPolicy regardless of the GatewayClass. The `opendatahub.io/managed: "false"` annotation is also ignored.

We use `openshift-default` because:
1. It aligns with the [official reference implementation](https://github.com/opendatahub-io/models-as-a-service)
2. It doesn't require the `data-science-gateway-class` GatewayClass to pre-exist
3. It's semantically cleaner (no ODH-specific naming)

## Authorino TLS Bootstrap

The annotation `security.opendatahub.io/authorino-tls-bootstrap: "true"` on the Gateway triggers the RHOAI platform to automatically configure an EnvoyFilter that enables TLS communication between the Gateway (Envoy) and Authorino.

Without this annotation, Authorino rejects requests with TLS certificate errors because the Gateway's sidecar doesn't trust the service-ca-signed certificate that Authorino uses.

This replaces three manual workarounds that were previously needed:
- `authorino-ca.yaml` (ConfigMap with service-ca bundle)
- `authorino-patch-job.yaml` (Job to mount CA and set SSL_CERT_DIR)
- `authorino.serviceCA` values block

## AuthPolicy Conflict (odh-model-controller)

### The Problem

When an `LLMInferenceService` is deployed, `odh-model-controller` automatically creates:

| Resource                       | Namespace             | Purpose                      |
| ------------------------------ | --------------------- | ---------------------------- |
| `maas-default-gateway-authn`   | `openshift-ingress`   | Gateway-level auth (basic)   |
| `gateway-auth-policy`          | `openshift-ingress`   | Platform MaaS auth (full)    |

Both target the same Gateway. Kuadrant enforces only one — `maas-default-gateway-authn` wins due to specificity (it's owned by the Gateway object).

The platform's `gateway-auth-policy` (created by the `maas-controller`) contains the correct governance configuration (tier resolution, response filters, etc.) but is **overridden** and never enforced.

### The Solution: PostSync Hook

The `cleanup-authn-hook.yaml` template is an ArgoCD PostSync hook that patches `maas-default-gateway-authn` with the complete MaaS governance logic:

1. **Authentication**: Adds `maas-default-gateway-sa` audience (required for MaaS tokens)
2. **Authorization**: SubjectAccessReview with tier-group RBAC
3. **Metadata**: HTTP call to `maas-api` for tier resolution
4. **Response filters**: Injects `tier` and `userid` into the request context (used by RateLimitPolicy counters)

This approach:
- Doesn't fight the controller (patching instead of deleting/replacing)
- Survives controller reconciliation (the hook runs on every ArgoCD sync)
- Requires minimal RBAC (only `get` + `patch` on AuthPolicies)

### Why Not Just Delete It?

If `maas-default-gateway-authn` is deleted, `odh-model-controller` immediately recreates it (within seconds). Deletion is not a viable approach.

### Why Not Use a Different Annotation?

The `opendatahub.io/managed: "false"` annotation on the Gateway was expected to prevent `odh-model-controller` from managing AuthPolicies. **In RHOAI 3.3.1, this annotation is ignored.** The controller creates `maas-default-gateway-authn` regardless.

## RBAC Verbs (get + post)

The model's RBAC Role includes both `get` and `post` verbs on `llminferenceservices`:

- `get`: Used by the basic `kubernetes-user` auth in `maas-default-gateway-authn`
- `post`: Used by the `tier-access` SubjectAccessReview in the patched policy

The reference repo only uses `get` because they don't implement tier-based governance (no custom authorization rules).

## Rate Limiting Architecture

Rate limits are enforced at the Gateway level using Kuadrant policies:

```
RateLimitPolicy (request-based)    TokenRateLimitPolicy (token-based)
        │                                     │
        ▼                                     ▼
   Limitador                             Limitador
   (per-tier request counters)           (per-tier token counters)
        │                                     │
        └─────────── Metrics ─────────────────┘
                        │
                   ServiceMonitor
                        │
                   Prometheus
                        │
                   PrometheusRule (alerts)
```

The `tier` and `userid` values injected by the AuthPolicy's response filters are used as counter keys in the rate limit policies, enabling per-user-per-tier enforcement.

## Telemetry Labels

The `TelemetryPolicy` adds a `user` label to Limitador metrics using the `auth.identity.userid` expression. Combined with `exhaustiveTelemetry` on Limitador, this produces metrics with `tier`, `user`, and `model` labels for observability.

## Comparison with Reference Implementation

The [official reference](https://github.com/opendatahub-io/models-as-a-service) (RHOAI 3.3.0) provides basic MaaS without governance:

| Feature                     | Reference | This repo |
| --------------------------- | --------- | --------- |
| GatewayClass                | `openshift-default` | `openshift-default` |
| Tier resolution             | No        | Yes (HTTP metadata call) |
| Per-user rate limiting      | No        | Yes (RLP + TRLP) |
| Token rate limiting         | No        | Yes (TRLP v1alpha1) |
| Telemetry labels            | No        | Yes (TelemetryPolicy) |
| PrometheusRule alerts       | No        | Yes |
| PostSync hook for AuthPolicy| No        | Yes (required for governance) |
| RBAC verbs                  | `get` only | `get` + `post` |

The reference doesn't need the PostSync hook because they accept the basic AuthPolicy that `odh-model-controller` creates — it already has the correct audience and RBAC for simple access control without tiers.

## Known Limitations (RHOAI 3.3.1)

1. **`opendatahub.io/managed: "false"` is ignored** — Cannot prevent `odh-model-controller` from creating `maas-default-gateway-authn`
2. **`gateway-auth-policy` always overridden** — The platform's full AuthPolicy never enforces; must merge logic into the controller's policy via patch
3. **PostSync hook required on every sync** — If the controller reconciles between syncs, the patch may be reverted until next ArgoCD sync
4. **RHOAI 3.4 may fix this** — The `maas-controller` is expected to handle AuthPolicy management properly, eliminating the need for the hook
