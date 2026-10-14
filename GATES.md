# Gates: Broadband speed + Amsterdam geolocation remediation

OWNS: artifacts/ab-bench/**, scripts/gates-verify.mjs, scripts/net-bench.mjs, GATES.md, docs/broadband-speed-remediation.md

Scope: Diagnose and remediate the user's Windows host + network config so the subscribed gigabit line is evidenced by measurement, confirm the speed-test "Amsterdam" symptom is root-caused, switch DNS to the measured-fastest resolver, and leave an evidence trail. Gates G1-G5 are order-independent facts; G6 depends on applied fixes.

- [ ] G1: baseline throughput and per-stream balance are measured and archived before any change
  CHECK: bun scripts/gates-verify.mjs baseline
  EXPECT: baseline verification passed
  EVIDENCE: pending

- [ ] G2: Windows network health posture is captured and every auto-fixable defect is either fixed or explicitly recorded as manual-only
  CHECK: bun scripts/gates-verify.mjs health
  EXPECT: health verification passed
  EVIDENCE: pending

- [ ] G3: DNS resolver choice is evidence-based: all candidates measured, fastest applied, and persistence confirmed
  CHECK: bun scripts/gates-verify.mjs dns
  EXPECT: dns verification passed
  EVIDENCE: pending

- [ ] G4: multi-stream CLI throughput re-measurement exists as a post-change datapoint
  CHECK: bun scripts/gates-verify.mjs multistream
  EXPECT: multistream verification passed
  EVIDENCE: pending

- [ ] G5: public geolocation evidence is archived from at least 3 independent providers plus the speed-test surface the user actually saw
  CHECK: bun scripts/gates-verify.mjs geo
  EXPECT: geo verification passed
  EVIDENCE: pending

- [ ] G6: post-remediation browser speed test reports at least 850 Mbps download, or the shortfall is root-caused with evidence and an explicit hardware-honest verdict
  CHECK: bun scripts/gates-verify.mjs browser-speed
  EXPECT: browser-speed verification passed
  EVIDENCE: pending

- [ ] G7: remediation report exists, cites every measured number, and states honestly whether the 1000 Mbps target was achievable
  CHECK: bun scripts/gates-verify.mjs report
  EXPECT: report verification passed
  EVIDENCE: pending
