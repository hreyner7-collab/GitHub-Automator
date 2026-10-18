# Gates: FreeTheAI tool shim — verification debt

OWNS: ftai_tool_shim.py, test_ftai_shim_behavior.py, test_ftai_shim_edge_behavior.py, test_ftai_e2e_runner.py, ftai_live_e2e_after_reset.py, check_shim_health.py, check_live_e2e.py, GATES.md

Scope: Prove the shim's shipped behavior (auto-tool loop, edge handling, runner verdicts, live process health) and surface the quota-gated live end-to-end as an explicit pending gate.

- [x] G1: main behavioral suite drives the shipped shim handler over real HTTP (tool loop, honesty, passthrough, SSE, retries) and every assertion passes
  CHECK: .venv\Scripts\python.exe test_ftai_shim_behavior.py
  EXPECT: ALL BEHAVIORAL CHECKS PASSED
  EVIDENCE: automatic-evidence=v1; definition-sha256=180cf107d133c227db6957499adb525593ef12a7074ed8b94986077c9c909c7e; exit=0; EXPECT=matched; output-sha256=ad6ba458baa4fef161d705adffd684d8d817e62b8cf1ba6dde72920be32d1873; output-bytes=734; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\henry\Documents\free\app\Cruality Pipeline; path=4a1ac152ae88/25 entries

- [x] G2: edge suite covers collision exit, malformed args, concurrency, GET relay, malformed fields, transport failure, unusable completions — all pass
  CHECK: .venv\Scripts\python.exe test_ftai_shim_edge_behavior.py
  EXPECT: ALL EDGE BEHAVIORAL CHECKS PASSED
  EVIDENCE: automatic-evidence=v1; definition-sha256=e2f96888f32758ca46cec2156673fbc796c900b198be37ac9da68b96f4699b71; exit=0; EXPECT=matched; output-sha256=aee438ab68816fc17587164cce2392124326a8c1216869ee7be4778b1b96af7c; output-bytes=438; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\henry\Documents\free\app\Cruality Pipeline; path=4a1ac152ae88/25 entries

- [x] G3: runner suite proves the quota-reset runner's verdict logic (blocked, pass, downgraded paths) in all three modes
  CHECK: .venv\Scripts\python.exe test_ftai_e2e_runner.py
  EXPECT: RUNNER BEHAVIORAL CHECKS PASSED
  EVIDENCE: automatic-evidence=v1; definition-sha256=3f5804b681f1ba5d948278c751af7e19cde5bbc7c3d7d74369b1137425f39032; exit=0; EXPECT=matched; output-sha256=f5e84c972eecfe0e0eb71b1688200604cefbe0583d58441a86712431165f64cc; output-bytes=2436; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\henry\Documents\free\app\Cruality Pipeline; path=4a1ac152ae88/25 entries

- [x] G4: the live shim process answers /v1/models with HTTP 200 on 127.0.0.1:4010
  CHECK: .venv\Scripts\python.exe check_shim_health.py
  EXPECT: SHIM HEALTH OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=d69e946b147994985476a98c4547c5081559665665dca5aae0abe69d148507f4; exit=0; EXPECT=matched; output-sha256=19f7b1a682e8d364affc0c5b277689c5e35f9b192b10ff6e2b566a5310ef0dce; output-bytes=16; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\henry\Documents\free\app\Cruality Pipeline; path=4a1ac152ae88/25 entries

- [ ] G5: live end-to-end at the 00:00 UTC quota window — results file must show the composed tool loop passing on lion and dog with no fake data
  CHECK: .venv\Scripts\python.exe check_live_e2e.py
  EXPECT: LIVE E2E PASS
  EVIDENCE: pending
