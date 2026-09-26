# 0010. Tests reach no network and pin causes, not clocks

Status: Accepted
Decided: Batch 20. Recorded 2026-09-26 (Batch 22).

## Context

Two tests failed at random on a loaded Windows machine. Saving an event waited
for api.telegram.org with the test token, so the test was as fast as the
network. A render-speed bar measured the machine: 109 ms of CPU, in Windows'
15.6 ms ticks, on a laptop whose clock slowed after the browser suite.

## Decision

The browser harness stands in for Telegram in every module that talks to it,
and a test fails if a new one is missing. A test about cost pins its causes
without a clock (fonts and backdrop cached, no PNG optimize), each proved by a
mutation that breaks the cause; only a coarse 500 ms CPU net stays.

## Consequences

The gate stops only for real reasons, on any machine. A new slow path is
caught by the coarse net or by a cause test written for it, not by a tight
timing bar.
