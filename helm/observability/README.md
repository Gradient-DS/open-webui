# Observability Stack

This directory contains Helm values for the central observability stack.

## Architecture

Central multi-tenant observability using Grafana LGTM stack:
- **Mimir**: Prometheus-compatible metrics storage (multi-tenant)
- **Loki**: Log aggregation (multi-tenant)
- **Tempo**: Distributed tracing (multi-tenant)
- **Alloy**: OpenTelemetry collector and log shipper
- **Grafana**: Visualization (admin instance)

## Multi-Tenancy

All backends use `X-Scope-OrgID` header for tenant isolation:
- `soev` - Main soev.ai deployment
- `enterprise-{name}` - Enterprise customer deployments

Each deployment's Alloy instance injects the appropriate tenant ID.

## Deployment

```bash
# Add Grafana Helm repository
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

# Deploy in order
helm install mimir grafana/mimir-distributed -n observability -f mimir-values.yaml --wait
helm install loki grafana/loki -n observability -f loki-values.yaml --wait
helm install tempo grafana/tempo -n observability -f tempo-values.yaml --wait
helm install alloy grafana/alloy -n observability -f alloy-values.yaml --wait
helm install grafana grafana/grafana -n observability -f grafana-values.yaml --wait
```

## Access

- **Grafana**: https://grafana.soev.ai (admin console)
- **Port-forward**: `kubectl port-forward -n observability svc/grafana 3000:80`

## Adding New Tenants

1. No backend changes needed (multi-tenancy is automatic)
2. Deploy Alloy in the new namespace with tenant ID configured
3. Optionally deploy tenant-specific Grafana instance

## Files

| File | Purpose |
|------|---------|
| `mimir-values.yaml` | Metrics storage configuration |
| `loki-values.yaml` | Log storage configuration |
| `tempo-values.yaml` | Trace storage configuration |
| `alloy-values.yaml` | Central Alloy for observability namespace |
| `grafana-values.yaml` | Admin Grafana configuration |

## Verification

```bash
# Check all pods are running
kubectl get pods -n observability

# Check Mimir health
kubectl exec -n observability deploy/mimir -- wget -qO- http://localhost:9009/ready

# Check Loki health
kubectl exec -n observability deploy/loki -- wget -qO- http://localhost:3100/ready

# Check Tempo health
kubectl exec -n observability deploy/tempo -- wget -qO- http://localhost:3200/ready

# View Alloy logs
kubectl logs -n observability deploy/alloy --tail=50
```

## Troubleshooting

### No data in Grafana
1. Check Alloy is receiving data: `kubectl logs -n observability deploy/alloy`
2. Verify tenant header: Ensure `X-Scope-OrgID` matches in datasource configuration
3. Check backend connectivity: Port-forward and test directly

### High memory usage
- Increase resource limits in values files
- Consider distributed mode for high-volume workloads

### Missing traces/logs correlation
- Ensure trace_id is present in log context
- Check Grafana datasource derived fields configuration
