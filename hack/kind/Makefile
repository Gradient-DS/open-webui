# hack/kind/Makefile — local HA reproduction for thoughts/shared/research/2026-04-20-redis-ha-loop-bug-and-kind-repro.md
#
# Usage:
#   make -C hack/kind up        # full bringup (cluster, ingress, build, install)
#   make -C hack/kind image     # rebuild + reload open-webui image only
#   make -C hack/kind upgrade   # helm upgrade with current values
#   make -C hack/kind repro     # run load-gen and grep pod logs
#   make -C hack/kind logs      # tail both replica logs, greppable
#   make -C hack/kind down      # delete cluster

SHELL := /bin/bash
CLUSTER := soev-ha
NAMESPACE := soev-local
RELEASE := soev-local
IMAGE := open-webui-local:dev
REPO_ROOT := $(abspath $(CURDIR)/../..)

.PHONY: up image ingress install upgrade wait hosts repro logs down clean cluster

up: cluster ingress image install wait hosts
	@echo ""
	@echo "==> HA reproduction cluster ready."
	@echo "==> curl http://soev.local:8080/health"
	@echo "==> make -C hack/kind repro   # trigger load, grep for loop errors"

cluster:
	@kind get clusters 2>/dev/null | grep -qx $(CLUSTER) || \
		kind create cluster --name $(CLUSTER) --config $(CURDIR)/kind-config.yaml

ingress:
	@kubectl --context kind-$(CLUSTER) apply -f \
		https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.2/deploy/static/provider/kind/deploy.yaml
	@kubectl --context kind-$(CLUSTER) wait --namespace ingress-nginx \
		--for=condition=ready pod --selector=app.kubernetes.io/component=controller \
		--timeout=180s

image:
	@echo "==> Building open-webui image (slim target)…"
	@cd $(REPO_ROOT) && docker build -t $(IMAGE) --build-arg USE_SLIM=true .
	@kind load docker-image $(IMAGE) --name $(CLUSTER)

install:
	@kubectl --context kind-$(CLUSTER) create namespace $(NAMESPACE) --dry-run=client -o yaml | kubectl --context kind-$(CLUSTER) apply -f -
	@if helm --kube-context kind-$(CLUSTER) -n $(NAMESPACE) status $(RELEASE) >/dev/null 2>&1; then echo "==> helm upgrade (release exists)"; helm --kube-context kind-$(CLUSTER) upgrade $(RELEASE) $(REPO_ROOT)/helm/open-webui-tenant -f $(CURDIR)/values-local.yaml --namespace $(NAMESPACE); else echo "==> First install: bootstrap with replicaCount=1 + ENABLE_DB_MIGRATIONS=true (chart's migrate Job is pre-upgrade only)"; helm --kube-context kind-$(CLUSTER) install $(RELEASE) $(REPO_ROOT)/helm/open-webui-tenant -f $(CURDIR)/values-local.yaml --namespace $(NAMESPACE) --set openWebui.replicaCount=1 --set openWebui.migrationJob.enabled=false --wait --timeout 8m; echo "==> Upgrading to HA topology (replicaCount=2 + migrate Job)"; helm --kube-context kind-$(CLUSTER) upgrade $(RELEASE) $(REPO_ROOT)/helm/open-webui-tenant -f $(CURDIR)/values-local.yaml --namespace $(NAMESPACE); fi

upgrade:
	@helm --kube-context kind-$(CLUSTER) upgrade $(RELEASE) $(REPO_ROOT)/helm/open-webui-tenant -f $(CURDIR)/values-local.yaml --namespace $(NAMESPACE)

wait:
	@kubectl --context kind-$(CLUSTER) -n $(NAMESPACE) rollout status deploy/$(RELEASE)-open-webui --timeout=5m

hosts:
	@grep -q "^127.0.0.1 soev.local" /etc/hosts || \
		(echo "==> Adding 127.0.0.1 soev.local to /etc/hosts (may prompt for sudo)" && \
		 echo "127.0.0.1 soev.local" | sudo tee -a /etc/hosts > /dev/null)

repro:
	@bash $(CURDIR)/load-gen.sh

logs:
	@kubectl --context kind-$(CLUSTER) -n $(NAMESPACE) logs -f \
		-l app.kubernetes.io/name=open-webui-tenant,app.kubernetes.io/component=open-webui --all-containers=true --tail=100

down:
	@kind delete cluster --name $(CLUSTER)

clean: down
