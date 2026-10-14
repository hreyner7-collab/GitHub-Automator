#!/usr/bin/env node
// net-bench.mjs — measures real throughput & latency from this machine.
// Usage: node scripts/net-bench.mjs
// Prints "NETBENCH <json>" summary line. Exit 0 always on completed run (results are data, not assertions).

const N_STREAMS = 8;
const TEST_MS = 5000;          // per download/upload phase
const CHUNK_BYTES = 50 * 1024 * 1024; // CF __down accepts up to ~60MB; use 50MiB chunks, looped per stream
const START = Date.now();
const MB = 1024 * 1024;

const fmt = (x, d = 0) => Number.isFinite(x) ? +x.toFixed(d) : null;

const DL_ENDPOINTS = [
  ['cloudflare', `https://speed.cloudflare.com/__down?bytes=${CHUNK_BYTES}`],
  ['cachefly', 'https://cachefly.cachefly.net/100mb.test'],
  ['ovh-fr', 'https://proof.ovh.net/files/100Mb.dat'],
  ['thinkbroadband-uk', 'https://ipv4.download.thinkbroadband.com/200MB.zip'],
  ['hetzner-de', 'https://speed.hetzner.de/100MB.bin'],
];

async function pickEndpoint() {
  // Probe each endpoint with a short GET; first 2xx with a body wins.
  const ua = { 'user-agent': 'Mozilla/5.0 netbench/1.0' };
  const forced = process.env.NETBENCH_ENDPOINT;
  if (forced) {
    const hit = DL_ENDPOINTS.find(([name]) => name === forced);
    if (hit) return { name: hit[0], url: hit[1] };
  }
  for (const [name, url] of DL_ENDPOINTS) {
    try {
      const r = await fetch(url, { headers: ua, cache: 'no-store', signal: AbortSignal.timeout(6000) });
      if (r.ok && r.body) {
        const reader = r.body.getReader();
        const first = await Promise.race([reader.read(), new Promise((_, rej) => setTimeout(() => rej(new Error('stall')), 6000))]);
        try { await reader.cancel(); } catch {}
        if (first && !first.done) return { name, url };
      }
    } catch {}
  }
  return null;
}

async function dlPhase() {
  const per = new Array(N_STREAMS).fill(0);
  const phaseStart = Date.now();
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), TEST_MS);
  const ua = { 'user-agent': 'Mozilla/5.0 netbench/1.0' };
  const ep = await pickEndpoint();
  if (!ep) return { mbps: 0, perStream: per.map(() => 0), endpoint: 'none-reachable' };
  const jobs = per.map((_, i) => (async () => {
    while (!ctrl.signal.aborted) {
      try {
        const r = await fetch(ep.url, { headers: ua, signal: ctrl.signal, cache: 'no-store' });
        if (!r.ok || !r.body) break;
        const reader = r.body.getReader();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          per[i] += value.length;
          if (ctrl.signal.aborted) { try { await reader.cancel(); } catch {} break; }
        }
      } catch { break; }
    }
  })());
  await Promise.all(jobs);
  clearTimeout(t);
  const secs = Math.min(Date.now() - phaseStart, TEST_MS) / 1000;
  const total = per.reduce((a, b) => a + b, 0);
  const mbps = (total * 8) / MB / secs;
  return { mbps: fmt(mbps, 1), perStream: per.map(p => fmt((p * 8) / MB / secs, 1)), endpoint: ep.name };
}

async function ulPhase() {
  // HTTP PUT-style upload: cloudflare __up accepts POST body
  const CHUNK = 1024 * 1024;
  const buf = new Uint8Array(CHUNK); // zeros compress? CF counts bytes received, fine.
  const ctrl = new AbortController();
  const t0 = Date.now();
  let sent = 0;
  const senders = new Array(4).fill(0).map(async () => {
    while (Date.now() - t0 < TEST_MS && !ctrl.signal.aborted) {
      try {
        await fetch('https://speed.cloudflare.com/__up', {
          method: 'POST',
          body: buf,
          headers: { 'content-type': 'application/octet-stream', 'user-agent': 'Mozilla/5.0 netbench/1.0' },
          signal: ctrl.signal,
        });
        sent += CHUNK;
      } catch { break; }
    }
  });
  await Promise.race([Promise.all(senders), new Promise(r => setTimeout(r, TEST_MS + 5000))]);
  ctrl.abort();
  const secs = Math.max((Date.now() - t0) / 1000, 0.5);
  return { mbps: fmt((sent * 8) / MB / secs, 1) };
}

async function latency() {
  const targets = [
    ['gateway', 'http://192.168.0.1/'], // will fail fast, but TCP connect time is measured via error timing? use ping instead
  ];
  // HTTP TTFB to anycast edges/resolvers:
  const urls = [
    ['one.one.one.one', 'https://1.1.1.1/cdn-cgi/trace'],
    ['dns.google', 'https://8.8.8.8/generate_204'],
    ['dns.quad9.net', 'https://dns.quad9.net:5053/dns-query?name=example.com&type=A'],
    ['cloudflare-speed', 'https://speed.cloudflare.com/__down?bytes=0'],
  ];
  const out = {};
  for (const [name, url] of urls) {
    const samples = [];
    for (let i = 0; i < 5; i++) {
      const t = performance.now();
      try { await fetch(url, { cache: 'no-store', headers: { 'user-agent': 'Mozilla/5.0 netbench/1.0' }, signal: AbortSignal.timeout(4000) }); } catch {}
      samples.push(performance.now() - t);
      await new Promise(r => setTimeout(r, 60));
    }
    samples.sort((a, b) => a - b);
    out[name] = { min: fmt(samples[0], 1), med: fmt(samples[2], 1), max: fmt(samples[4], 1) };
  }
  return out;
}async function geo() {
  const pick = j => ({
    ip: j.ip || j.clientIp || j.query,
    colo: j.colo,
    asOrganization: j.asOrganization || j.org || j.isp,
    city: j.city,
    country: j.country || j.countryCode,
  });
  try {
    const r = await fetch('https://speed.cloudflare.com/meta', { headers: { 'user-agent': 'Mozilla/5.0 netbench/1.0' }, signal: AbortSignal.timeout(5000) });
    const j = await r.json();
    const out = pick(j);
    if (out.ip) return out;
  } catch {}
  try {
    const r = await fetch('https://ipwho.is/', { headers: { 'user-agent': 'Mozilla/5.0 netbench/1.0' }, signal: AbortSignal.timeout(5000) });
    return pick(await r.json());
  } catch (e) { return { error: String(e.message || e) };
}
}

const meta = await geo();
const lat = await latency();
const down = await dlPhase();
const up = await ulPhase();const summary = {
  ts: new Date().toISOString(),
  downMbps: down.mbps,
  downPerStream: down.perStream,
  downEndpoint: down.endpoint || 'unknown',
  upMbps: up.mbps,
  latency: lat,
  geo: meta,
  runtimeS: fmt((Date.now() - START) / 1000, 1),
};
console.log('NETBENCH ' + JSON.stringify(summary));
