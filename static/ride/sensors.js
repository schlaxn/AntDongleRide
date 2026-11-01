// Sensor check dialog. Three rows: heart rate, speed, cadence. Each row lists the sensors that can
// fill it, from ANT and from Bluetooth. One Search button listens on ANT and scans Bluetooth.
const $ = (id) => document.getElementById(id);

const ROLE_LABEL = { heart_rate: "Heart rate", speed: "Speed", cadence: "Cadence" };
const ROLE_TYPES = { heart_rate: [120], speed: [121, 123], cadence: [121, 122] };
const ANT_SEARCH = [120, 121, 123];                         // ANT types listened to by Search
const COMBINED_PARTNER = { speed: "cadence", cadence: "speed" };
// Bluetooth: the server connection each role uses, the scan flag that marks its sensors, and the
// (device type, device number) the role takes from that connection
const BT = {
  heart_rate: { connect: "/api/ble/start", state: "ble", flag: "heart_rate", devtype: 120, devnum: -1 },
  speed: { connect: "/api/ble/csc/start", state: "ble_csc", flag: "csc", devtype: 121, devnum: -2 },
  cadence: { connect: "/api/ble/csc/start", state: "ble_csc", flag: "csc", devtype: 121, devnum: -2 },
};

let timer = null;
let built = false;
let onStart = () => {};
let btScanned = [];   // last Bluetooth scan: {address, name, rssi, heart_rate, csc}

async function post(url, body = {}) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

export async function missingRoles() {
  const s = await (await fetch("/api/sensors")).json();
  return Object.entries(s.roles).filter(([, v]) => v.required && !v.live).map(([r]) => ROLE_LABEL[r]);
}

// the Bluetooth connection for a role, if it is running
function activeBt(role, s) {
  const conn = s[BT[role].state];
  return conn && conn.running && conn.address ? conn.address : null;
}

// the value the dropdown should show for a role: "bt:<address>" or "<devtype>:<devnum>"
function currentValue(role, s) {
  const cur = s.roles[role];
  const b = BT[role];
  const addr = activeBt(role, s);
  if (cur.devtype === b.devtype && cur.devnum === b.devnum && addr) return `bt:${addr}`;
  return `${cur.devtype}:${cur.devnum}`;
}

function optionsFor(role, s) {
  const types = ROLE_TYPES[role];
  const b = BT[role];
  const opts = [{ value: `${types[0]}:0`, label: "Any (first heard)" }];
  for (const d of s.devices) {
    if (d.devnum >= 0 && types.includes(d.devtype)) {
      opts.push({ value: `${d.devtype}:${d.devnum}`, label: `ANT · ${d.name} #${d.devnum}` });
    }
  }
  const addr = activeBt(role, s);
  // cadence needs a sensor that reports crank data; a speed-only Bluetooth sensor is not offered
  const cadenceOk = role !== "cadence" || (s.ble_csc && s.ble_csc.has_crank === true);
  if (addr && cadenceOk) {
    const known = btScanned.find((d) => d.address === addr);
    opts.push({ value: `bt:${addr}`, label: `Bluetooth · ${known ? known.name : "connected sensor"}` });
  }
  if (role !== "cadence") {
    for (const d of btScanned) {
      if (d[b.flag] && d.address !== addr) opts.push({ value: `bt:${d.address}`, label: `Bluetooth · ${d.name}` });
    }
  }
  return opts;
}

async function refresh() {
  const s = await (await fetch("/api/sensors")).json();
  for (const role of Object.keys(ROLE_LABEL)) {
    const sel = $("s_" + role), pill = $("p_" + role);
    const cur = s.roles[role];
    const value = currentValue(role, s);
    const opts = optionsFor(role, s);
    if (!opts.some((o) => o.value === value)) opts.push({ value, label: `${cur.devtype}/${cur.devnum} (not heard)` });
    const key = opts.map((o) => o.value + "|" + o.label).join(";");
    if (sel.dataset.key !== key) {
      sel.innerHTML = "";
      for (const o of opts) {
        const el = document.createElement("option");
        el.value = o.value;
        el.textContent = o.label;
        sel.appendChild(el);
      }
      sel.dataset.key = key;
    }
    // do not fight the user while they have the dropdown open
    if (document.activeElement !== sel) sel.value = value;
    pill.textContent = cur.live ? `live · ${cur.age_s} s` : cur.age_s == null ? "no signal" : `stale · ${cur.age_s} s`;
    pill.className = "pill " + (cur.live ? "ok" : "bad");
  }
  const missing = Object.entries(s.roles).filter(([, v]) => v.required && !v.live).map(([r]) => ROLE_LABEL[r]);
  const anySource = s.connected || s.ble.running || s.ble_csc.running;
  if (!anySource) {
    $("sensorMsg").innerHTML = `<span class="warn-text">No sensor source yet: press Search, or connect the ANT stick.</span>`;
  } else if (missing.length) {
    $("sensorMsg").innerHTML = `<span class="warn-text">Not ready: ${missing.join(", ")}</span>`;
  } else {
    $("sensorMsg").innerHTML = `<span class="ok-text">Ready to ride.</span>`;
  }
}

// a combined sensor (type 121) also feeds the partner role, when the partner is on the combined type
async function partnerSync(role, devtype, devnum) {
  const partner = COMBINED_PARTNER[role];
  if (devtype !== 121 || !partner) return;
  const s = await (await fetch("/api/sensors")).json();
  if (s.roles[partner].devtype === 121) await post("/api/sensors/select", { role: partner, devtype, devnum });
}

async function select(role, value) {
  try {
    if (value.startsWith("bt:")) {
      const address = value.slice(3);
      const b = BT[role];
      const s = await (await fetch("/api/sensors")).json();
      if (activeBt(role, s) !== address) {
        $("sensorNote").textContent = "Connecting to the Bluetooth sensor...";
        await post(b.connect, { address });
      }
      await post("/api/sensors/select", { role, devtype: b.devtype, devnum: b.devnum });
      await partnerSync(role, b.devtype, b.devnum);
    } else {
      const [devtype, devnum] = value.split(":").map(Number);
      await post("/api/sensors/select", { role, devtype, devnum });
      await partnerSync(role, devtype, devnum);
    }
  } catch (e) { alert(e.message); }
  refresh();
}

// one button: listen on ANT for each type, and scan Bluetooth
async function search() {
  const msgs = [];
  $("sensorNote").textContent = "Searching on ANT and scanning Bluetooth (about 6 s)...";
  for (const t of ANT_SEARCH) {
    try { await post("/api/sensors/search", { devtype: t }); } catch (e) { msgs.push(`ANT: ${e.message}`); break; }
  }
  try {
    btScanned = (await post("/api/ble/scan")).devices;
  } catch (e) { msgs.push(`Bluetooth: ${e.message}`); }
  $("sensorNote").textContent = msgs.length ? msgs.join(" · ") : "Switch your sensors on, then pick one for each row.";
  refresh();
}

function build() {
  const box = $("sensorRows");
  box.innerHTML = "";
  const top = document.createElement("div");
  top.className = "row";
  top.innerHTML = `<button id="btnSearch">Search (ANT + Bluetooth)</button><span class="muted" id="sensorNote" style="font-size:12px"></span>`;
  box.appendChild(top);
  for (const role of Object.keys(ROLE_LABEL)) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<label>${ROLE_LABEL[role]}${role === "speed" ? " *" : ""}</label>
      <select id="s_${role}" style="min-width:300px"></select>
      <span class="pill" id="p_${role}">-</span>`;
    box.appendChild(row);
    $("s_" + role).onchange = (e) => select(role, e.target.value);
  }
  $("btnSearch").onclick = search;
  $("btnSensorConnect").onclick = async () => {
    try { await post("/api/connect"); } catch (e) { alert(e.message); }
    refresh();
  };
  $("btnSensorClose").onclick = () => $("sensorDlg").close();
  $("btnSensorStart").onclick = () => { $("sensorDlg").close(); onStart(); };
  $("sensorDlg").addEventListener("close", () => clearInterval(timer));
  built = true;
}

export function openSensorDialog(startCallback) {
  if (!built) build();
  onStart = startCallback;
  $("sensorDlg").showModal();
  refresh().catch(() => {});
  clearInterval(timer);
  timer = setInterval(() => refresh().catch(() => {}), 1000);
}
