"""Sensor roles for the rowing app (the rower twin of sensors.py).

Roles:
  heart_rate  Bluetooth or ANT heart-rate strap (devtype 120; BLE uses devnum -1)
  rower       the S4 decoder (devtype 200): speed, stroke events, power, split

devnum 0 means "any device of this type"; a positive devnum pins one device.
"""
ROLE_TYPES = {"heart_rate": (120,), "rower": (200,)}
ROLE_OF_METRIC = {
    "heart_rate": "heart_rate",
    "speed": "rower",
    "code": "rower",
    "stroke": "rower",
    "stroke_rate": "rower",
    "power": "rower",
    "split_500m": "rower",
}
REQUIRED = ("rower",)
LIVE_S = 3.0


class RowRoles:
    def __init__(self):
        self.selection: dict[str, dict] = {
            role: {"devtype": types[0], "devnum": 0} for role, types in ROLE_TYPES.items()
        }

    def select(self, role: str, devtype: int, devnum: int) -> None:
        if role not in ROLE_TYPES:
            raise ValueError(f"Unknown role {role!r}")
        if devtype not in ROLE_TYPES[role]:
            raise ValueError(f"Device type {devtype} cannot fill role {role!r}")
        self.selection[role] = {"devtype": int(devtype), "devnum": int(devnum)}

    def accepts(self, sample: dict) -> bool:
        role = ROLE_OF_METRIC.get(sample.get("metric"))
        if role is None:
            return False
        sel = self.selection[role]
        if sample.get("devtype") != sel["devtype"]:
            return False
        return sel["devnum"] == 0 or sample.get("devnum") == sel["devnum"]


def role_status(roles: RowRoles, devices: list[dict] | dict, now: float) -> dict:
    """Per role: the selected device, whether it is live (seen within LIVE_S) and its age."""
    rows = devices.values() if isinstance(devices, dict) else devices
    out = {}
    for role, sel in roles.selection.items():
        match = [d for d in rows
                 if d.get("devtype") == sel["devtype"]
                 and (sel["devnum"] == 0 or d.get("devnum") == sel["devnum"])]
        last = max((d.get("last", 0.0) for d in match), default=None)
        age = None if last is None else round(now - last, 1)
        out[role] = {
            "devtype": sel["devtype"],
            "devnum": sel["devnum"],
            "required": role in REQUIRED,
            "live": age is not None and age <= LIVE_S,
            "age_s": age,
        }
    return out
