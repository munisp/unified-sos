# ADR-005: Workflow Orchestration — Temporal

**Status:** Accepted · **Domain:** Workflow

## Decision

**Temporal Workflow Engine** for durable, code-defined long-running workflows: multi-tier C-of-O land titling approvals (Surveyor → Town Planning → Attorney General → Governor digital signature), mining concessions, automated debt escalation, OBC/FBC evaluation, CAD dispatch.

## Rationale

Replaces fragile hand-rolled state-machine code with durable execution, exactly-once activity semantics, and built-in SLA instrumentation (Benue's 60–90-day titling directive, Osun's 45-day commitment, Lagos consent SLAs — one workflow class, different SLA parameters).

## Tradeoff

Requires a dedicated Temporal cluster with persistence workers backed by Postgres.
