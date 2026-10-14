#!/usr/bin/env bun
// apply-fixes.mjs — applies WiFi adapter + power-plan optimizations, records before/after into health-after.json
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';

const ps = (script) => {
  try {
    return execFileSync('powershell', ['-NoProfile', '-Command', script], { encoding: 'utf8', timeout: 45000, windowsHide: true }).trim();
  } catch (e) { return `ERROR: ${String(e.stderr || e.message).slice(0, 300)}`; }
};

const before = JSON.parse(fs.readFileSync('artifacts/ab-bench/health-before.json', 'utf8'));
const applied = {}, manualOnly = {};

const getProp = (name) => {
  const raw = ps(`Get-NetAdapterAdvancedProperty -Name WiFi -DisplayName '${name}' | Select-Object -ExpandProperty DisplayValue`);
  return raw.startsWith('ERROR') ? null : raw.trim();
};

const fixes = [
  { display: 'Throughput Booster', value: 'Enabled', why: 'Removes intermittent pausing between frames (Intel feature)' },
  { display: 'MIMO Power Save Mode', value: 'No SMPS', why: 'Stops antenna power-saving that halves spatial streams' },
  { display: 'Roaming Aggressiveness', value: '1. Lowest', why: 'Desktop PC; stops pointless AP rescans that stall throughput' },
  { display: 'Packet Coalescing', value: 'Disabled', why: 'Reduces receive latency' },
];

for (const f of fixes) {
  const beforeVal = getProp(f.display);
  if (beforeVal == null) { manualOnly[f.display] = { reason: 'property not found/readable on this adapter' }; continue; }
  if (beforeVal === f.value) { applied[f.display] = { before: beforeVal, after: beforeVal, note: 'already optimal', why: f.why }; continue; }
  const res = ps(`Set-NetAdapterAdvancedProperty -Name WiFi -DisplayName '${f.display}' -DisplayValue '${f.value}'`);
  const afterVal = getProp(f.display);
  if (afterVal === f.value) {
    applied[f.display] = { before: beforeVal, after: afterVal, why: f.why };
  } else {
    manualOnly[f.display] = { reason: `set failed: ${String(res).slice(0, 150)}; remains ${beforeVal}` };
  }
}

// Windows power plan: "Power Saving Mode" for Wireless Adapter -> Maximum Performance (0)
{
  const GUID_SUB_WIRELESS = '19cbb8fa-5279-450e-9fac-8a3d5fedd0c1';
  const GUID_WIRELESS_POWER = '12bbebe6-58d6-4636-95bb-3217ef867c1a';
  const q = ps(`powercfg /q SCHEME_CURRENT ${GUID_SUB_WIRELESS} ${GUID_WIRELESS_POWER} 2>&1 | Select-String 'Current AC|Current DC'`);
  ps(`powercfg /setacvalueindex SCHEME_CURRENT ${GUID_SUB_WIRELESS} ${GUID_WIRELESS_POWER} 0; powercfg /setdcvalueindex SCHEME_CURRENT ${GUID_SUB_WIRELESS} ${GUID_WIRELESS_POWER} 0; powercfg /setactive SCHEME_CURRENT`);
  const q2 = ps(`powercfg /q SCHEME_CURRENT ${GUID_SUB_WIRELESS} ${GUID_WIRELESS_POWER} 2>&1 | Select-String 'Current AC|Current DC'`);
  applied['PowerPlan:WLAN Power Saving'] = { before: String(q).replace(/\s+/g, ' ').slice(0, 200), after: String(q2).replace(/\s+/g, ' ').slice(0, 200), why: 'Max-performance wireless power saving on AC/DC' };
}

// Adapter power management: stop Windows powering down the NIC (fails on some drivers)
{
  const r = ps('Disable-NetAdapterPowerManagement -Name WiFi -NoRestart 2>&1 | Out-String');
  if (r.startsWith('ERROR') || /error|exception|not recognized|fail/i.test(r)) {
    manualOnly['Adapter Power Management'] = { reason: `cmdlet rejected by driver: ${String(r).replace(/\s+/g, ' ').slice(0, 180)}` };
  } else {
    applied['Adapter Power Management'] = { before: 'driver-managed', after: 'power management disabled (Disable-NetAdapterPowerManagement)', why: 'Prevents NIC sleep during idle-then-burst transfers' };
  }
}

// Post-change verification state
const linkMbps = (() => {
  const raw = ps('(Get-NetAdapter -Name WiFi).ReceiveLinkSpeed');
  return parseFloat(raw) || null;
})();

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
