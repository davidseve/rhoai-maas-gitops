# Kuadrant Readiness Hook

The `maas-platform` chart includes an ArgoCD **PostSync hook** (`kuadrant-readiness-hook.yaml`) that handles two critical post-deployment tasks:

1. **MissingDependency recovery** -- Kuadrant often starts before KServe finishes deploying the Gateway API provider, entering an unrecoverable `MissingDependency` state.
2. **Authorino TLS setup** -- Follows the [official MaaS `setup-authorino-tls.sh`](https://github.com/opendatahub-io/models-as-a-service/blob/main/scripts/setup-authorino-tls.sh) to enable listener TLS and outbound `service-ca` trust so Authorino can call `maas-api` for tier metadata lookup.

## How it works

The hook runs automatically after every ArgoCD sync:

1. **Kuadrant readiness** -- Waits for the Kuadrant CR to exist and reach `Ready=True`. If stuck in `MissingDependency`, restarts the operator pod to trigger reconciliation.
2. **Service annotation** -- Annotates the `authorino-authorino-authorization` service with `serving-cert-secret-name` so OpenShift's service-ca operator generates a TLS serving certificate.
3. **Listener TLS + volume mount** -- Patches the Authorino CR to enable `listener.tls.enabled: true` with the generated cert secret, and mounts the `openshift-service-ca.crt` ConfigMap at `/etc/ssl/certs/openshift-service-ca`.
4. **Environment variables** -- Sets `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` on the Authorino deployment, pointing to the mounted `service-ca.crt`. This is the official method for outbound `service-ca` trust (the Authorino CR does not support env vars directly).
5. **Component restart** -- Restarts Limitador pods and waits for both Authorino and Limitador rollouts to complete with fresh configuration.
6. **Envoy Gateway restart** -- Restarts the Envoy Gateway pods so they reload the WasmPlugin configuration with the updated Authorino/Limitador endpoints. Without this, rate limiting may not take effect after a clean deploy.

Without steps 2-4, Authorino fails with `tls: failed to verify certificate: x509: certificate signed by unknown authority` when resolving `auth.identity.tier` via the `maas-api` metadata lookup, and all RateLimitPolicy predicates silently fail to match (no 429s).

## Why this is needed

The Authorino base image ships ~148 public CA certificates but does **not** include the OpenShift `service-ca` certificate. Since `maas-api` uses a `service-ca`-signed TLS cert (via `service.beta.openshift.io/serving-cert-secret-name`), Authorino cannot verify it out of the box.

The official `setup-authorino-tls.sh` script solves this by:
- Generating a serving cert for Authorino's listener (Gateway->Authorino mTLS)
- Setting `SSL_CERT_FILE` env var so Go's `crypto/tls` trusts `service-ca` for outbound calls (Authorino->maas-api)

## RBAC

The hook creates its own `ServiceAccount`, `ClusterRole`, and `ClusterRoleBinding` with permissions for:
- Kuadrant CRs (`get`), Pods (`list`/`delete`/`get`), Services (`get`/`patch`), Deployments (`get`/`list`/`patch`), Deployments/scale (`patch`), Authorino CRs (`get`/`patch`)

All hook resources use `argocd.argoproj.io/hook-delete-policy: BeforeHookCreation` for cleanup.

## Manual deployments (without ArgoCD)

The hook does not run when deploying with `helm template`. Run the equivalent steps manually:

```bash
NS=kuadrant-system
SVC=authorino-authorino-authorization
CERT_SECRET=authorino-server-cert

# 1. Fix MissingDependency (if needed)
oc delete pod -n $NS -l control-plane=controller-manager

# 2. Annotate service for serving cert
oc annotate service $SVC -n $NS \
  "service.beta.openshift.io/serving-cert-secret-name=$CERT_SECRET" --overwrite

# 3. Enable listener TLS + mount service-ca ConfigMap
oc patch authorino authorino -n $NS --type=merge -p '{
  "spec": {
    "listener": {
      "tls": {
        "enabled": true,
        "certSecretRef": {"name": "'"$CERT_SECRET"'"}
      }
    },
    "volumes": {
      "items": [{
        "name": "service-ca",
        "configMaps": ["openshift-service-ca.crt"],
        "mountPath": "/etc/ssl/certs/openshift-service-ca"
      }]
    }
  }
}'

# 4. Set env vars for outbound trust
oc set env deployment/authorino -n $NS \
  SSL_CERT_FILE=/etc/ssl/certs/openshift-service-ca/service-ca.crt \
  REQUESTS_CA_BUNDLE=/etc/ssl/certs/openshift-service-ca/service-ca.crt

# 5. Restart Limitador and wait
oc delete pod -n $NS -l limitador-resource=limitador,app=limitador
oc rollout status deployment/authorino -n $NS
oc rollout status deployment/limitador-limitador -n $NS
```

## Diagnosing issues

```bash
# Check Kuadrant readiness
oc get kuadrant kuadrant -n kuadrant-system -o jsonpath='{.status.conditions}'

# Check Authorino TLS config
oc get authorino authorino -n kuadrant-system -o jsonpath='{.spec.listener.tls}'

# Check env vars on Authorino deployment
oc get deployment authorino -n kuadrant-system \
  -o jsonpath='{.spec.template.spec.containers[0].env}'

# Check Authorino TLS errors (should show NO "cannot fetch metadata" after fix)
oc logs deployment/authorino -n kuadrant-system --tail=50 | grep "cannot fetch metadata"

# Check AuthPolicy status
oc get authpolicy -n openshift-ingress \
  -o jsonpath='{range .items[*]}{.metadata.name}: {.status.conditions[0].reason}{"\n"}{end}'

# Check hook Job logs
oc logs job/kuadrant-readiness-check -n kuadrant-system
```
