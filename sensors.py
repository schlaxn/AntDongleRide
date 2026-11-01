"""Sensor roles: which device feeds heart rate, speed and cadence.

Each role is a (device type, device number):
    devnum  0   any sensor of that type (the first one heard)
    devnum  n   only the sensor with device number n
    devnum -1   the Bluetooth heart-rate watch (received by the laptop, not the stick)

The ride only uses samples that match the role; everything else is still recorded in
samples.csv. A combined speed/cadence sensor (type 121) can supply both roles.
"""
ROLE_TYPES = {"heart_rate": (120,), "speed": (121, 123), "cadence": (121, 122)}
ROLE_SEARCH_TYPE = {"heart_rate": 120, "speed": 121, "cadence": 121}
REQUIRED = ("speed",)  # a live speed sensor is enough to ride; heart rate and cadence are optional
LIVE_S = 3.0                        # a device counts as live if it sent data this recently
BLE_DEVNUM = -1


class SensorRoles:
    def __init__(self):
        self.selection = {r: {"devtype": ROLE_TYPES[r][0], "devnum": 0} for r in ROLE_TYPES}

    def select(self, role: str, devtype: int, devnum: int) -> None:
        if role not in ROLE_TYPES:
            raise ValueError(f"Unknown sensor role {role!r}")
        if devtype not in ROLE_TYPES[role]:
            raise ValueError(f"Device type {devtype} cannot supply {role.replace('_', ' ')}")
        self.selection[role] = {"devtype": int(devtype), "devnum": int(devnum)}

    def accepts(self, sample: dict) -> bool:
        """True if this decoded sample should drive the ride."""
        role = sample.get("metric")
        if role not in ROLE_TYPES:
            return False
        sel = self.selection[role]
        if sample.get("devtype") != sel["devtype"]:
            return False
        return sel["devnum"] == 0 or sample.get("devnum") == sel["devnum"]


def role_status(roles: SensorRoles, devices: list[dict], now: float) -> dict:
    """Per role: the selection, whether a matching device is live, and how old its data is."""
    out = {}
    for role, sel in roles.selection.items():
        match = [d for d in devices
                 if d["devtype"] == sel["devtype"] and (sel["devnum"] == 0 or d["devnum"] == sel["devnum"])]
        last = max((d.get("last", 0) for d in match), default=None)
        age = None if last is None else now - last
        out[role] = {
            **sel,
            "required": role in REQUIRED,
            "live": age is not None and age <= LIVE_S,
            "age_s": None if age is None else round(age, 1),
        }
    return out
