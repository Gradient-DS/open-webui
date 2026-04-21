# Kind local HA harness

Single-command local reproduction of the production Helm chart in a 2-replica
HA topology, for verifying the Redis event-loop fix (see
`thoughts/shared/research/2026-04-20-redis-ha-loop-bug-and-kind-repro.md`).

## Prereqs

    brew install kind kubectl helm hey

## Quickstart

    make -C hack/kind up              # ~5 min first run
    # open http://soev.local:8080 and sign up
    TOKEN=<jwt> make -C hack/kind repro

## Iteration loop (after editing code)

    make -C hack/kind image upgrade   # rebuild, reload, rollout
    TOKEN=<jwt> make -C hack/kind repro

## Teardown

    make -C hack/kind down
