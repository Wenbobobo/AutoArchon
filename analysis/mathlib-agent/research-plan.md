# Mathlib Agent Research Plan

## Goal

Design a separate agent that can help when formalization or proving repeatedly hits missing Mathlib coverage, missing abstractions, or missing reusable local lemma packs.

## Non-Goals For The Current Runtime

- not part of the default benchmark-faithful path
- does not own proof acceptance
- does not mutate the active proving workflow silently

## Inputs

- `reports/final/lessons/lesson-records.jsonl`
- `reports/postmortem/lessons/lesson-records.jsonl`
- validation payloads showing repeated blocker patterns
- helper notes that mention missing infrastructure

## Candidate Outputs

- hint packs for theorem search and lemma discovery
- clustered missing-mathlib reports
- future candidate contribution backlogs for Mathlib PR work

## First Experiments

1. cluster repeated `missing_infrastructure` and similar signals across campaigns
2. map those clusters to theorem-search or local search recipes
3. separate benchmark-only reuse from broader formalization support
4. prototype a read-only advisory contract before any runtime insertion
