#!/usr/bin/env node
// gates-verify.mjs — runnable oracles for GATES.md
// Usage: node scripts/gates-verify.mjs <mode>
// Modes: baseline | health | dns | multistream | browser-speed | geo | report
// Prints "<mode> verification passed" ONLY after every assertion in that mode passes.
import fs from 'node:fs';
import path from 'node:path';

const mode = process.argv[2];
const DIR = 'artifacts/ab-bench';
const read = p => fs.readFileSync(p, 'utf8');
const exists = p => fs.existsSync(p);
const latest = (prefix, ext) => {
  const files = fs.readdirSync(DIR).filter(f => f.startsWith(prefix) && f.endsWith(ext)).sort();
  if (!files.length) throw new Error(`no artifact matching ${prefix}*${ext} in ${DIR}`);
  return path.join(DIR, files[files.length - 1]);
};
const fail = msg => { console.error(`[${mode}] FAIL: ${msg}`); process.exit(1); };
const ok = () => { console.log(`${mode} verification passed`); };

try {
  if (mode === 'baseline') {
    const f = latest('baseline-cli-', '.json');
    const j = JSON.parse(read(f));
    if (!(j.downMbps > 0)) fail('baseline download missing');
    if (!Array.isArray(j.downPerStream) || j.downPerStream.length < 2) fail('per-stream data missing');
    const active = j.downPerStream.filter(v => v > 5).length;
    if (active < 2) fail(`expected >=2 active streams, got ${active}`);
    if (!j.geo || !j.geo.ip) fail('geo not embedded in baseline');
    console.error(`[${mode}] baseline=${j.downMbps} Mbps activeStreams=${active} ip=${j.geo.ip}`);
    ok();
  }

  else if (mode === 'health') {
    // health-* files are named before/after, so alphabetical sort is wrong;
    // pick the newest by the capturedAt timestamp inside each file.
    const f = fs.readdirSync(DIR)
      .filter(x => x.startsWith('health-') && x.endsWith('.json'))
      .map(x => ({ x, t: (() => { try { return JSON.parse(read(path.join(DIR, x))).capturedAt || ''; } catch { return ''; } })() }))
      .sort((a, b) => a.t < b.t ? -1 : a.t > b.t ? 1 : a.x < b.x ? -1 : 1)
      .pop().x;
    const j = JSON.parse(read(path.join(DIR, f)));
    // Every applied key must be recorded with before/after, or in manualOnly with a reason.
    const applied = j.applied || {};
    const manual = j.manualOnly || {};
    if (!Object.keys(applied).length && !Object.keys(manual).length) fail('no fixes and no manual-only records');
    for (const [k, v] of Object.entries(applied)) {
      if (!('before' in v) || !('after' in v)) fail(`applied fix ${k} lacks before/after`);
    }
    for (const [k, v] of Object.entries(manual)) {
      if (!v.reason) fail(`manualOnly ${k} lacks reason`);
    }
    if (!j.linkMbps) fail('link rate not captured');
    console.error(`[${mode}] applied=${Object.keys(applied).join(',')} manual=${Object.keys(manual).join(',')} link=${j.linkMbps}Mbps`);
    ok();
  }

  else if (mode === 'dns') {
    const f = latest('dns-bench-', '.json');
    const j = JSON.parse(read(f));
    if (!j.candidates || Object.keys(j.candidates).length < 3) fail('fewer than 3 resolvers measured');
    if (!j.fastest) fail('fastest resolver not recorded');
    if (!j.applied) fail('resolver not applied');
    if (j.candidates[j.fastest] == null) fail('fastest not among measured candidates');
    if (!j.persisted) fail('resolver choice not confirmed persisted');
    if (!j.leak || !j.leak.usesChosen) fail(`resolver leak: system still uses ${JSON.stringify(j.leak)}`);
    console.error(`[${mode}] fastest=${j.fastest} applied=${j.applied} persisted=${j.persisted}`);
    ok();
  }

  else if (mode === 'multistream') {
    const f = latest('post-cli-', '.json');
    const j = JSON.parse(read(f));
    if (!(j.downMbps > 0)) fail('post-change download missing');
    if (!j.upMbps) fail('upload missing');
    console.error(`[${mode}] post down=${j.downMbps} up=${j.upMbps}`);
    ok();
  }

  else if (mode === 'geo') {
    const provs = ['ipinfo', 'ip-api', 'ipwho.is'];
    const found = provs.filter(p => exists(path.join(DIR, `geo-${p.replace(/[^a-z]/gi, '')}.json`)));
    if (found.length < 3) fail(`only ${found.length}/3 geo providers archived`);
    for (const p of found) {
      const j = JSON.parse(read(path.join(DIR, `geo-${p.replace(/[^a-z]/gi, '')}.json`)));
      const c = j.country || j.countryCode;
      if (!c) fail(`provider ${p} missing country`);
    }
    if (!exists(path.join(DIR, 'speedtest-browser.json'))) fail('browser speed-test observation not archived');
    const st = JSON.parse(read(path.join(DIR, 'speedtest-browser.json')));
    if (!st.serverLocation) fail('speed-test server location not recorded');
    console.error(`[${mode}] providers=${found.join(',')} speedtestServer=${st.serverLocation} speedtestClient=${st.clientLocation || 'n/a'}`);
    ok();
  }

  else if (mode === 'browser-speed') {
    const f = latest('browser-speed-', '.json');
    const j = JSON.parse(read(f));
    if (typeof j.downloadMbps !== 'number') fail('browser download result missing');
    if (j.downloadMbps >= 850) {
      console.error(`[${mode}] TARGET MET: ${j.downloadMbps} Mbps >= 850`);
      ok();
    } else {
      if (!j.rootCause) fail(`below target (${j.downloadMbps} < 850) and no rootCause recorded`);
      if (!j.verdict) fail('below target and no hardware-honest verdict recorded');
      console.error(`[${mode}] BELOW TARGET with evidence: ${j.downloadMbps} Mbps; cause=${j.rootCause}; verdict=${j.verdict}`);
      ok();
    }
  }

  else if (mode === 'report') {
    if (!exists('docs/broadband-speed-remediation.md')) fail('report missing');
    const md = read('docs/broadband-speed-remediation.md');
    const nums = (md.match(/\b\d{2,4}\s?Mbps/gi) || []).length;
    if (nums < 5) fail(`report cites only ${nums} measured Mbps figures`);
    for (const section of ['Root cause', 'What was changed', 'Measured results', 'Honest verdict', 'Next steps']) {
      if (!md.includes(section)) fail(`report missing section: ${section}`);
    }
    if (!exists('docs/broadband-speed-remediation.md')) fail('unreachable');
    console.error(`[${mode}] report cites ${nums} Mbps figures, all sections present`);
    ok();
  }

  else {
    fail(`unknown mode: ${mode}`);
  }
} catch (e) {
  fail(e.message);
}
