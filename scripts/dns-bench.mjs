#!/usr/bin/env bun
// dns-bench.mjs — evidence-based DNS resolver selection.
// Modes:
//   measure   — RTT-measure every candidate with raw UDP DNS queries,
//               write artifacts/ab-bench/.dns-draft.json (candidates + fastest)
//   confirm   — after the (elevated) apply step, verify persistence + leak,
//               write artifacts/ab-bench/dns-bench-<ts>.json for the gate
import dgram from 'node:dgram';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';

const DIR = 'artifacts/ab-bench';
const DRAFT = `${DIR}/.dns-draft.json`;
const ps = (script) => {
  try {
    return execFileSync('powershell', ['-NoProfile', '-Command', script], { encoding: 'utf8', timeout: 45000, windowsHide: true }).trim();
  } catch (e) { return `ERROR: ${String(e.stderr || e.message).slice(0, 300)}`; }
};

// ---- raw DNS query -------------------------------------------------------
function buildQuery(name, id) {
  const q = Buffer.alloc(512);
  q.writeUInt16BE(id, 0);
  q.writeUInt16BE(0x0100, 2);   // recursion desired
  q.writeUInt16BE(1, 4);        // QDCOUNT
  let off = 12;
  for (const label of name.split('.')) {
    if (!label) continue;
    q[off++] = label.length;
    q.write(label, off); off += label.length;
  }
  q[off++] = 0;
  q.writeUInt16BE(1, off);      // QTYPE A
  q.writeUInt16BE(1, off + 2);  // QCLASS IN
  return q.subarray(0, off + 4);
}

function queryOnce(server, name, id, timeoutMs = 2000) {
  return new Promise((resolve) => {
    const sock = dgram.createSocket('udp4');
    const t0 = performance.now();
    let done = false;
    const finish = (ok, rtt) => {
      if (done) return; done = true;
      clearTimeout(to); try { sock.close(); } catch {}
      resolve(ok ? rtt : null);
    };
    const to = setTimeout(() => finish(false, null), timeoutMs);
    sock.on('message', (msg) => {
      if (msg.length >= 12 && msg.readUInt16BE(0) === id) finish(true, performance.now() - t0);
    });
    sock.on('error', () => finish(false, null));
    sock.send(buildQuery(name, id), 53, server, (err) => { if (err) finish(false, null); });
  });
}

async function measureResolver(server, n = 7) {
  const samples = [];
  let lost = 0;
  for (let i = 0; i < n; i++) {
    const name = `probe-${Math.random().toString(36).slice(2, 10)}.example.com`;
    const rtt = await queryOnce(server, name, (i * 7919 + 1000) & 0xffff);
    if (rtt == null) lost++; else if (i > 0) samples.push(rtt); // drop warmup
  }
  samples.sort((a, b) => a - b);
  const med = samples.length ? samples[Math.floor(samples.length / 2)] : null;
  return { medianMs: med != null ? +med.toFixed(1) : null, minMs: samples.length ? +samples[0].toFixed(1) : null, maxMs: samples.length ? +samples[samples.length - 1].toFixed(1) : null, lost, sent: n };
}

// ---- candidates ----------------------------------------------------------
const current = (() => {
  const raw = ps("Get-DnsClientServerAddress -InterfaceAlias WiFi -AddressFamily IPv4 | Select-Object -ExpandProperty ServerAddresses");
  const m = raw.match(/\d+\.\d+\.\d+\.\d+/g);
  return m ? [...new Set(m)] : [];
})();
const candidates = [...new Set([...current, '1.1.1.1', '8.8.8.8', '9.9.9.9', '8.8.4.4'])];

if (process.argv[2] === 'measure') {
  const results = {};
  for (const c of candidates) results[c] = await measureResolver(c);
  const ranked = Object.entries(results).filter(([, v]) => v.medianMs != null).sort((a, b) => a[1].medianMs - b[1].medianMs);
  if (!ranked.length) { console.error('no resolver answered'); process.exit(1); }
  const fastest = ranked[0][0];
  const draft = { measuredAt: new Date().toISOString(), candidates: results, fastest, ranking: ranked.map(([s, v]) => ({ server: s, medianMs: v.medianMs })) };
  fs.writeFileSync(DRAFT, JSON.stringify(draft, null, 2));
  console.log('DNS-BENCH ' + JSON.stringify({ fastest, ranking: draft.ranking }));
  console.log(`Apply with: powershell -NoProfile -Command "Set-DnsClientServerAddress -InterfaceAlias WiFi -ServerAddresses ('${fastest}','${ranked[1] ? ranked[1][0] : fastest}')"`);
} else if (process.argv[2] === 'confirm') {
  const draft = JSON.parse(fs.readFileSync(DRAFT, 'utf8'));
  const fastest = draft.fastest;
  const second = draft.ranking[1] ? draft.ranking[1].server : fastest;
  const applied = [fastest, second];
  // persistence: read back the interface config
  const liveRaw = ps("Get-DnsClientServerAddress -InterfaceAlias WiFi -AddressFamily IPv4 | Select-Object -ExpandProperty ServerAddresses");
  const live = (liveRaw.match(/\d+\.\d+\.\d+\.\d+/g) || []);
  const persisted = live.includes(fastest);
  // leak check: what does the SYSTEM resolver actually use? (nslookup reports the server it queries)
  // Run via cmd /c so PowerShell doesn't wrap native stderr into error records.
  let ns = '';
  try { ns = execFileSync('cmd', ['/c', 'nslookup', 'example.com'], { encoding: 'utf8', timeout: 20000 }); } catch (e) { ns = String(e.stdout || e.message); }
  const lines = ns.split(/\r?\n/).map(l => l.trim());
  const serverLine = lines.find(l => /^Server:/i.test(l));
  const addrAfterServer = serverLine != null ? lines[lines.indexOf(serverLine) + 1] : '';
  const addrMatch = (addrAfterServer || '').match(/Address:\s*(\d+\.\d+\.\d+\.\d+)/i);
  const leakServer = addrMatch ? addrMatch[1] : null;
  const usesChosen = leakServer != null && applied.includes(leakServer);
  const out = {
    ts: new Date().toISOString(),
    candidates: draft.candidates,
    ranking: draft.ranking,
    fastest,
    applied: applied.join(', '),
    appliedList: applied,
    persisted,
    persistedRaw: live,
    leak: { server: leakServer, usesChosen, raw: ns.replace(/\s+/g, ' ').slice(0, 300) },
    method: 'raw UDP DNS A-query RTT, 7 probes per resolver (1 warmup dropped), median',
    queryName: 'random-label .example.com (cache-busting)',
  };
  const file = `${DIR}/dns-bench-${new Date().toISOString().replace(/[:.]/g, '').slice(0, 15)}.json`;
  fs.writeFileSync(file, JSON.stringify(out, null, 2));
  console.log(`DNS-CONFIRM fastest=${fastest} persisted=${persisted} leakServer=${leakServer} usesChosen=${usesChosen}`);
  console.log('wrote ' + file);
  if (!persisted || !usesChosen) process.exit(2);
} else {
  console.error('usage: dns-bench.mjs measure|confirm');
  process.exit(1);
}
