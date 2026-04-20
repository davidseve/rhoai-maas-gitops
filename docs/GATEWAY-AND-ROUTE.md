# Gateway and Route Configuration

The MaaS Gateway is exposed externally via an OpenShift Route. There are two TLS termination strategies.

## How TLS works in each mode

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

## Option A: Passthrough (default)

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
| --- | --- | --- |
| AWS (ROSA, IPI) | `ingress-certs` | Let's Encrypt or ACM cert |
| Bare-metal / UPI | `router-certs-default` | Self-signed or custom CA |
| Custom | `oc get secret -n openshift-ingress \| grep tls` | Check your cluster |

## Option B: Reencrypt

The OpenShift Router terminates external TLS and establishes a new TLS connection to the Gateway using a service-ca certificate.

```yaml
gateway:
  tlsSecretName: maas-gateway-service-tls

route:
  tlsTermination: reencrypt
```

**Additional step** (after Gateway Service is created):

```bash
oc annotate svc maas-default-gateway-openshift-default \
  -n openshift-ingress \
  service.beta.openshift.io/serving-cert-secret-name=maas-gateway-service-tls
```

## Decision guide

| Scenario | Mode | Gateway cert | Why |
| --- | --- | --- | --- |
| AWS with known wildcard cert | **passthrough** | `ingress-certs` | Simple, no extra steps |
| Bare-metal with `router-certs-default` | **passthrough** | `router-certs-default` | Simple |
| Unknown platform / multi-cluster | **reencrypt** | `maas-gateway-service-tls` | Platform-independent |
