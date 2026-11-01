// Sensor check dialog: choose the sensor for each role, search for new ones, see what is live.
const $ = (id) => document.getElementById(id);

const ROLE_LABEL = { heart_rate: "Heart rate", speed: "Speed", cadence: "Cadence" };
const ROLE_TYPES = { heart_rate: [120], speed: [121, 123], cadence: [121, 122] };
// Search types per role. Speed also listens for a combined speed & cadence sensor (121).
const ROLE_SEARCH = { heart_rate: [120], speed: [121, 123], cadence: [121] };
// A combined sensor (121) feeds speed and cadence on one channel; these are the paired roles.
const COMBINED_PARTNER = { speed: "cadence", cadence: "speed" };

let timer = null;
let built = false;
let onStart = () => {};

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

function optionsFor(role, devices, current) {
  const types = ROLE_TYPES[role];
  const opts = [{ value: `${types[0]}:0`, label: "Any (first heard)" }];
  for (const d of devices) {
    if (types.includes(d.devtype)) opts.push({ value: `${d.devtype}:${d.devnum}`, label: `${d.name} #${d.devnum}` });
  }
  const cur = `${current.devtype}:${current.devnum}`;
  if (!opts.some((o) => o.value === cur)) opts.push({ value: cur, label: `#${current.devnum} (not heard)` });
  return opts;
}

async function refresh() {
  const s = await (await fetch("/api/sensors")).json();
  for (const role of Object.keys(ROLE_LABEL)) {
    const sel = $("s_" + role), pill = $("p_" + role);
    const cur = s.roles[role];
    const opts = optionsFor(role, s.devices, cur);
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
    if (document.activeElement !== sel) sel.value = `${cur.devtype}:${cur.devnum}`;
    pill.textContent = cur.live ? `live · ${cur.age_s} s` : cur.age_s == null ? "no signal" : `stale · ${cur.age_s} s`;
    pill.className = "pill " + (cur.live ? "ok" : "bad");
  }
  const missing = Object.entries(s.roles).filter(([, v]) => v.required && !v.live).map(([r]) => ROLE_LABEL[r]);
  if (!s.connected && !s.ble.running) {
    $("sensorMsg").innerHTML = `<span class="warn-text">Stick not connected - press Connect stick.</span>`;
  } else if (missing.length) {
    $("sensorMsg").innerHTML = `<span class="warn-text">Not ready: ${missing.join(", ")}</span>`;
  } else {
    $("sensorMsg").innerHTML = `<span class="ok-text">Ready to ride.</span>`;
  }
}

async function select(role, value) {
  const [devtype, devnum] = value.split(":").map(Number);
  try {
    await post("/api/sensors/select", { role, devtype, devnum });
    // A combined sensor also supplies the partner role. Pair it there too, but only when the
    // partner role is still on the combined type (or "any"), so a dedicated sensor is not replaced.
    const partner = COMBINED_PARTNER[role];
    if (devtype === 121 && partner) {
      const s = await (await fetch("/api/sensors")).json();
      const cur = s.roles[partner];
      if (cur.devtype === 121) {
        await post("/api/sensors/select", { role: partner, devtype, devnum });
      }
    }
  } catch (e) { alert(e.message); }
  refresh();
}

async function search(role) {
  try {
    for (const devtype of ROLE_SEARCH[role]) await post("/api/sensors/search", { devtype });
    $("sensorMsg").textContent = `Listening for ${ROLE_LABEL[role]} sensors - switch one on now.`;
  } catch (e) { alert(e.message); }
}

function build() {
  const box = $("sensorRows");
  for (const role of Object.keys(ROLE_LABEL)) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<label>${ROLE_LABEL[role]}${role === "cadence" ? "" : " *"}</label>
      <select id="s_${role}" style="min-width:250px"></select>
      <span class="pill" id="p_${role}">-</span>
      <button id="b_${role}">Search</button>`;
    box.appendChild(row);
    $("b_" + role).onclick = () => search(role);
    $("s_" + role).onchange = (e) => select(role, e.target.value);
  }
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
