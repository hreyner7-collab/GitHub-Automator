#!/usr/bin/env node
// health-snapshot.mjs — captures Windows network health state as JSON.
// Usage: node scripts/health-snapshot.mjs <outfile.json>
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';

const out = process.argv[2] || 'health.json';
const ps = script => {
  try {
    return execFileSync('powershell', ['-NoProfile', '-Command', script], { encoding: 'utf8', timeout: 30000, windowsHide: true }).trim();
  } catch (e) { return `ERROR: ${String(e.message).slice(0, 200)}`; }
};
const j = script => {
  const raw = ps(`${script} | ConvertTo-Json -Depth 4 -Compress`);
  try { return JSON.parse(raw); } catch { return { raw: raw.slice(0, 500) }; }
};

const snapshot = {
  capturedAt: new Date().toISOString(),
  linkMbps: (() => {
    const raw = ps("(Get-NetAdapter -Name WiFi).ReceiveLinkSpeed");
    return parseFloat(raw) || null;
  })(),
  wlan: ps('netsh wlan show interfaces'),
  adapterAdvanced: j('Get-NetAdapterAdvancedProperty -Name WiFi | Select-Object DisplayName,DisplayValue,RegistryValue'),
  adapterPower: j('Get-NetAdapterPowerManagement -Name WiFi | Select-Object AllowComputerToTurnOffDevice,DeviceSleepOnDisconnect,SelectiveSuspend'),
  tcpGlobal: ps('netsh int tcp show global'),
  tcpSettings: j('Get-NetTCPSetting -SettingIndex 0 | Select-Object AutoTuningLevelLocal,EcnCapability,Timestamps,PacingProfile,NonSackRttResiliency'),
  dns: j('Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object {$_.ServerAddresses} | Select-Object InterfaceAlias,ServerAddresses'),
  ipInterfaces: j('Get-NetIPInterface -InterfaceAlias WiFi | Select-Object AddressFamily,NlMtu,ConnectionState'),
  winhttpProxy: ps('netsh winhttp show proxy'),
};

fs.writeFileSync(out, JSON.stringify(snapshot, null, 2));
console.log(`HEALTH-SNAPSHOT written ${out}`);
