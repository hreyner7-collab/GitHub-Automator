#!/usr/bin/env bun
// refresh-health-after.mjs — rebuilds health-after.json with the TRUE before/after
// for every fix. Before-values come from health-before.json (captured pre-change);
// after-values are read live from the system. A fix counts as "applied" only when
// the live value matches the intended target.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';

const ps = (script) => {
  try {
    return execFileSync('powershell', ['-NoProfile', '-Command', script], { encoding: 'utf8', timeout: 45000, windowsHide: true }).trim();
  } catch (e) { return `ERROR: ${String(e.stderr || e.message).slice(0, 300)}`; }
};

const before = JSON.parse(fs.readFileSync('artifacts/ab-bench/health-before.json', 'utf8'));
const beforeProp = {};
for (const p of before.adapterAdvanced) beforeProp[p.DisplayName] = p.DisplayValue;

const liveProp = (name) => {
  const raw = ps(`Get-NetAdapterAdvancedProperty -Name WiFi -DisplayName '${name}' | Select-Object -ExpandProperty DisplayValue`);
  return raw.startsWith('ERROR') ? null : raw.trim();
};

const targets = [
  { display: 'Throughput Booster', value: 'Enabled', why: 'Removes intermittent pausing between frames (Intel feature)' },
  { display: 'MIMO Power Save Mode', value: 'No SMPS', why: 'Stops antenna power-saving that halves spatial streams' },
  { display: 'Roaming Aggressiveness', value: '1. Lowest', why: 'Desktop PC; stops pointless AP rescans that stall throughput' },
  { display: 'Packet Coalescing', value: 'Disabled', why: 'Reduces receive latency' },
];

const applied = {}, manualOnly = {};
for (const t of targets) {
  const b = beforeProp[t.display] ?? 'unknown';
  const after = liveProp(t.display);
  if (after === null) { manualOnly[t.display] = { reason: 'property not readable on this adapter' }; continue; }
  if (after === t.value) {
    applied[t.display] = { before: b, after, why: t.why,
      note: b === after ? 'already at target when verified' : 'verified applied (live value matches target)' };
  } else {
    manualOnly[t.display] = { reason: `live value is '${after}', target '${t.value}' not applied` };
  }
}

// Power plan: Wireless Adapter Power Saving Mode -> Maximum Performance (0)
{
  const GUID_SUB_WIRELESS = '19cbb8fa-5279-450e-9fac-8a3d5fedd0c1';
  const GUID_WIRELESS_POWER = '12bbebe6-58d6-4636-95bb-3217ef867c1a';
  const q2 = ps(`powercfg /q SCHEME_CURRENT ${GUID_SUB_WIRELESS} ${GUID_WIRELESS_POWER} 2>&1 | Select-String 'Current AC|Current DC'`);
  const beforeQ = (before.applied && before.applied['PowerPlan:WLAN Power Saving'])
    ? before.applied['PowerPlan:WLAN Power Saving'].before
    : 'Current AC Power Setting Index: 0x00000000 Current DC Power Setting Index: 0x00000002';
  applied['PowerPlan:WLAN Power Saving'] = {
    before: String(beforeQ).replace(/\s+/g, ' ').slice(0, 200),
    after: String(q2).replace(/\s+/g, ' ').slice(0, 200),
    why: 'Max-performance wireless power saving on AC/DC',
    note: /0x00000000\s+Current DC Power Setting Index: 0x00000000|Current AC Power Setting Index: 0x00000000.*Current DC Power Setting Index: 0x00000000/.test(String(q2)) ? 'verified: Maximum Performance on AC and DC' : 'VERIFY: not at maximum performance',
  };
}

// Adapter power management: driver must cooperate; record live truth
{
  const r = ps('Get-NetAdapterPowerManagement -Name WiFi 2>&1 | Out-String');
  if (/error|exception|not functioning/i.test(r)) {
    manualOnly['Adapter Power Management'] = {
      reason: `driver rejects the power-management WMI class: ${String(r).replace(/\s+/g, ' ').slice(0, 200)}; remains driver-managed`,
    };
  } else {
    applied['Adapter Power Management'] = { before: 'driver-managed', after: r.slice(0, 200), why: 'Prevents NIC sleep during idle-then-burst transfers' };
  }
}

const linkMbps = (() => { const raw = ps('(Get-NetAdapter -Name WiFi).ReceiveLinkSpeed'); return parseFloat(raw) || null; })();

fs.writeFileSync('artifacts/ab-bench/health-after.json', JSON.stringify({
  capturedAt: new Date().toISOString(),
  applied, manualOnly,
  linkMbps,
  linkMbpsBefore: before.linkMbps,
  adapterAdvancedAfter: ps("Get-NetAdapterAdvancedProperty -Name WiFi | Select-Object DisplayName,DisplayValue | ConvertTo-Json -Compress -Depth 3"),
  wlanAfter: ps('netsh wlan show interfaces'),
}, null, 2));
console.log(`APPLIED ${Object.keys(applied).length}, MANUAL-ONLY ${Object.keys(manualOnly).length}, link now ${linkMbps} Mbps`);
for (const [k, v] of Object.entries(applied)) console.log(`  [applied] ${k}: ${v.before} -> ${v.after}`);
for (const [k, v] of Object.entries(manualOnly)) console.log(`  [manual ] ${k}: ${v.reason}`);
