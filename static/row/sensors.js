// Sensor dialog: pick the S4 serial port and a Bluetooth heart-rate strap.

const $ = (id) => document.getElementById(id);

async function api(url, body) {
  const opt = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const r = await fetch(url, opt);
  let j = {};
  try { j = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

function msg(text, ok) {
  const el = $("sensorMsg");
  el.textContent = text;
  el.className = "pill " + (ok === true ? "ok" : ok === false ? "bad" : "");
}

export function openSensorDialog() {
  const dlg = $("sensorDlg");
  if (!dlg.open) dlg.showModal();
  refresh();
}

export async function refresh() {
  try {
    const s = await api("/api/row/sensors");
    const r = s.roles.rower;
    const pill = $("s4Pill");
    pill.textContent = s.connected ? (r.live ? "live" : "connected, no data") : "not connected";
    pill.className = "pill " + (r.live ? "ok" : s.connected ? "" : "bad");
    const hr = s.roles.heart_rate;
    const hp = $("hrPill");
    hp.textContent = hr.live ? "live" : (hr.age_s == null ? "not connected" : "stale");
    hp.className = "pill " + (hr.live ? "ok" : "");
    return s;
  } catch (e) {
    msg(e.message, false);
    return null;
  }
}

export function sensorsLive(status) {
  return !!(status && status.roles && status.roles.rower.live);
}

export const SIM_PORT = "__sim__";

async function scanPorts() {
  const sel = $("portSel");
  sel.innerHTML = "";
  const simOpt = document.createElement("option");
  simOpt.value = SIM_PORT;
  simOpt.textContent = "Simulated S4 (no hardware)";
  sel.appendChild(simOpt);
  try {
    const j = await api("/api/row/ports");
    if (!j.ports.length) {
      const none = document.createElement("option");
      none.value = "";
      none.textContent = "(no serial ports)";
      sel.appendChild(none);
    }
    for (const p of j.ports) {
      const o = document.createElement("option");
      o.value = p.device;
      o.textContent = `${p.device} - ${p.description}${p.vid ? ` [${p.vid}:${p.pid}]` : ""}`;
      sel.appendChild(o);
    }
    msg(`${j.ports.length} port(s) found`, true);
  } catch (e) { msg(e.message, false); }
}

async function scanHr() {
  const sel = $("hrSel");
  sel.innerHTML = "";
  msg("Scanning Bluetooth for 6 s…", null);
  try {
    const j = await api("/api/row/ble/scan", {});
    const devices = j.devices.filter((d) => d.heart_rate);
    if (!devices.length) sel.innerHTML = '<option value="">(no heart-rate devices)</option>';
    for (const d of devices) {
      const o = document.createElement("option");
      o.value = d.address;
      o.textContent = `${d.name || "(unnamed)"} · ${d.address} · ${d.rssi} dBm`;
      sel.appendChild(o);
    }
    msg(`${devices.length} heart-rate device(s)`, true);
  } catch (e) { msg(e.message, false); }
}

export function wireSensors() {
  $("btnSensorClose").onclick = () => $("sensorDlg").close();
  $("btnPorts").onclick = scanPorts;
  $("btnHrScan").onclick = scanHr;
  $("btnS4Connect").onclick = async () => {
    const port = $("portSel").value;
    if (!port) return msg("Scan ports and pick one first", false);
    try {
      if (port === SIM_PORT) {
        await api("/api/row/sim/start", {});
        msg("Simulated S4 running", true);
      } else {
        await api("/api/row/connect", { port });
        msg(`Connected to ${port}`, true);
      }
      refresh();
    } catch (e) { msg(e.message, false); }
  };
  $("btnHrConnect").onclick = async () => {
    const address = $("hrSel").value;
    if (!address) return msg("Scan Bluetooth and pick a strap first", false);
    try {
      await api("/api/row/ble/start", { address });
      await api("/api/row/sensors/select", { role: "heart_rate", devtype: 120, devnum: -1 });
      msg(`Heart rate from ${address}`, true);
      refresh();
    } catch (e) { msg(e.message, false); }
  };
}
