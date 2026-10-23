"""ECHONET payloads, the FA control frame, and boxInfo parsing.

The integer setpoint command matches a live B3 write. Half-degree writes, fan,
vanes, and the FA on/off flags follow the app's frame layout.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field, replace

from .const import (
    B2C_AUTHORIZE,
    B2C_CLIENT_ID,
    B2C_REDIRECT,
    B2C_SCOPE,
    FA_COCORO,
    FA_SETPOINT,
    FA_SLEEP,
    FA_SOFT,
    FA_SPEED,
    FA_SUPER_JET,
    FA_VANE_HORIZONTAL,
    FA_VANE_VERTICAL,
    FAN_MODES,
    FAULT_CODE,
    MODE_AUTO,
    MODE_COOL,
    MODE_HEAT,
    MODES,
    PLASMACLUSTER_OFF,
    PLASMACLUSTER_ON,
    POWER_OFF,
    POWER_ON,
    VANE_HORIZONTAL,
    VANE_VERTICAL,
)

_CODE_RE = re.compile(r"^[A-Za-z0-9._~+/=\-]+$")

# Change-bitmap positions from the app's control-frame table (C2/c, f346f).
_F346F: dict[int, int | None] = {i: (i - 16 if 17 <= i <= 52 else None) for i in range(1, 81)}
_F346F.update({53: 36, 54: 36})
for _index in range(55, 81):
    _F346F[_index] = _index - 18

_MODE_BY_CODE = {code: name for name, code in MODES}
_FAN_BY_CODE = {code: name for name, code in FAN_MODES}
_VANE_V_BY_CODE = {code.upper(): name for name, code in VANE_VERTICAL}
_VANE_H_BY_CODE = {code.upper(): name for name, code in VANE_HORIZONTAL}

# BB and BE. 0x7E, 0x7F, and 0x80 mean the unit has no reading.
# Other bytes are signed: 0xF6 is -10 °C.
_INVALID_TEMPS = {0x7E, 0x7F, 0x80}
_INVALID_HUMIDITY = {0xFD, 0xFE, 0xFF}


def authorize_url() -> str:
    """Azure AD B2C authorize URL. No PKCE and no client secret."""
    query = urllib.parse.urlencode(
        {
            "client_id": B2C_CLIENT_ID,
            "redirect_uri": B2C_REDIRECT,
            "scope": B2C_SCOPE,
            "response_type": "code",
        },
        quote_via=urllib.parse.quote,
    )
    return f"{B2C_AUTHORIZE}?{query}"


def extract_auth_code(value: str) -> str | None:
    """Pull a B2C code out of a redirect URL or a pasted code."""
    text = value.strip()
    if not text or any(char.isspace() for char in text):
        return None
    if "://" not in text and "code=" in text:
        query = text.split("?", 1)[-1]
        text = f"https://sharp-cocoroair-egypt/?{query}"
    if "://" in text or text.startswith("http"):
        parsed = urllib.parse.urlparse(text)
        codes = urllib.parse.parse_qs(parsed.query).get("code")
        if not codes and parsed.fragment:
            codes = urllib.parse.parse_qs(parsed.fragment).get("code")
        if not codes or not codes[0]:
            return None
        return codes[0]
    if len(text) >= 8 and _CODE_RE.fullmatch(text):
        return text
    return None


def prop_single(epc: int, code: str) -> dict:
    """One valueSingle property. Status codes are lowercase, as sent live."""
    return {
        "statusCode": f"{epc:02x}",
        "valueType": "valueSingle",
        "valueSingle": {"code": code},
    }


def prop_range(epc: int, value: int) -> dict:
    """One valueRange property. Integer setpoints use this shape."""
    return {
        "statusCode": f"{epc:02x}",
        "valueType": "valueRange",
        "valueRange": {"type": "int", "code": str(value)},
    }


def prop_binary(epc: int, code_hex: str) -> dict:
    """One valueBinary property."""
    return {
        "statusCode": f"{epc:02x}",
        "valueType": "valueBinary",
        "valueBinary": {"code": code_hex},
    }


class ControlFrame:
    """FA payload: checksum, a zero byte, a 14-byte change bitmap, then data.

    Indexes passed to ``set`` are 1-based, matching the app. Only the indexes
    that are set get a bit in the bitmap, so the unit leaves the other bytes.
    """

    def __init__(self, end_index: int) -> None:
        """Build an empty frame covering bytes 17 through ``end_index``."""
        self.start = 17
        self.end = end_index
        self.items: dict[int, str] = {}

    def set(self, index: int, byte_hex: str) -> ControlFrame:
        """Mark one byte as changing."""
        self.items[index] = byte_hex.lower()
        return self

    def encode(self) -> str:
        """Return the even-length hex string the cloud expects."""
        bits = ["0"] * (14 * 8)
        for index in self.items:
            position = _F346F.get(index)
            if position is not None:
                bits[position - 1] = "1"
        bitmap = "".join(f"{int(''.join(bits[i : i + 4]), 2):x}" for i in range(0, len(bits), 4))
        data = "".join(self.items.get(i, "00") for i in range(self.start, self.end + 1))
        body = "00" + bitmap + data
        checksum = sum(int(body[i : i + 2], 16) for i in range(0, len(body), 2)) % 256
        return f"{checksum:02x}{body}"


def fa_end_index(model_gen: int | None, fa_length: int | None) -> int:
    """Pick the FA frame length. A captured status frame wins over the generation."""
    if fa_length in (80, 128):
        return fa_length
    if model_gen is not None and model_gen < 19:
        return 128
    return 80


def cmd_power(on: bool) -> list[dict]:
    """Power on or off. Off is a single EPC 80 write."""
    return [prop_single(0x80, POWER_ON if on else POWER_OFF)]


def cmd_mode(mode: str) -> list[dict]:
    """Turn on and select a mode. Plasmacluster is left untouched."""
    code = dict(MODES)[mode]
    return [prop_single(0x80, POWER_ON), prop_single(0xB0, code)]


def cmd_setpoint(temp_c: float, *, half_degree: bool, frame_end: int) -> list[dict]:
    """Set the target temperature.

    Whole degrees use EPC B3, which was confirmed live. A half degree uses the
    FA frame, and only when this unit's F0 blob says half degrees exist.
    """
    snapped = round(temp_c * 2) / 2
    if abs(snapped - round(snapped)) > 1e-6:
        if not half_degree:
            raise ValueError("This unit does not support half-degree setpoints")
        encoded = round(snapped * 2)
        frame = ControlFrame(frame_end).set(FA_SETPOINT, f"{encoded:02x}")
        return [prop_binary(0xFA, frame.encode())]
    return [prop_range(0xB3, round(snapped))]


def cmd_fan(fan: str) -> list[dict]:
    """Set the fan speed with EPC A0."""
    return [prop_single(0xA0, dict(FAN_MODES)[fan])]


def cmd_vanes(
    *,
    vertical: str | None,
    horizontal: str | None,
    frame_end: int,
) -> list[dict]:
    """Set vertical and/or horizontal vanes. Unset vanes are left alone."""
    frame = ControlFrame(frame_end)
    if vertical is not None:
        frame.set(FA_VANE_VERTICAL, dict(VANE_VERTICAL)[vertical])
    if horizontal is not None:
        frame.set(FA_VANE_HORIZONTAL, dict(VANE_HORIZONTAL)[horizontal])
    return [prop_binary(0xFA, frame.encode())]


def cmd_plasmacluster(on: bool) -> list[dict]:
    """Turn Plasmacluster ions on or off."""
    return [prop_single(0xCF, PLASMACLUSTER_ON if on else PLASMACLUSTER_OFF)]


def _cmd_flag(index: int, on: bool, frame_end: int) -> list[dict]:
    frame = ControlFrame(frame_end).set(index, "01" if on else "00")
    return [prop_binary(0xFA, frame.encode())]


def cmd_sleep(on: bool, frame_end: int) -> list[dict]:
    """Sleep flag in the FA frame."""
    return _cmd_flag(FA_SLEEP, on, frame_end)


def cmd_soft(on: bool, frame_end: int) -> list[dict]:
    """Soft / やさしさ flag. Only sent for units whose F0 bit says it exists."""
    return _cmd_flag(FA_SOFT, on, frame_end)


def cmd_speed(on: bool, frame_end: int) -> list[dict]:
    """Speed flag. Only sent for units whose F0 bit says it exists."""
    return _cmd_flag(FA_SPEED, on, frame_end)


def cmd_cocoro(on: bool, frame_end: int) -> list[dict]:
    """Cocoro Air app setting. Only sent when the F0 bit says it exists."""
    return _cmd_flag(FA_COCORO, on, frame_end)


def control_body(
    device_id: int,
    echonet_node: str,
    echonet_object: str,
    status: list[dict],
) -> dict:
    """Body of POST control/deviceControl."""
    return {
        "controlList": [
            {
                "deviceId": device_id,
                "echonetNode": echonet_node,
                "echonetObject": echonet_object,
                "status": status,
            }
        ]
    }


@dataclass(frozen=True)
class Capabilities:
    """Feature flags taken from the F0 capability blob."""

    model_gen: int | None = None
    half_degree: bool = False
    soft: bool = False
    speed: bool = False
    cocoro: bool = False
    auto_has_setpoint: bool = False
    a04: bool = False
    patrol: bool = False


@dataclass
class Aircon:
    """One indoor unit, decoded from boxInfo."""

    box_id: str
    device_id: int
    echonet_node: str
    echonet_object: str
    name: str
    model: str | None
    power: bool | None = None
    mode: str | None = None
    setpoint: float | None = None
    room_temp: float | None = None
    humidity: int | None = None
    outdoor_temp: float | None = None
    # True while outdoor_temp is the last real reading, held because the unit is off.
    outdoor_stale: bool = False
    fan: str | None = None
    vane_vertical: str | None = None
    vane_horizontal: str | None = None
    plasmacluster: bool | None = None
    power_w: int | None = None
    energy_kwh: float | None = None
    fault: bool | None = None
    sleep: bool | None = None
    soft: bool | None = None
    speed: bool | None = None
    cocoro_air: bool | None = None
    super_jet: bool | None = None
    caps: Capabilities = field(default_factory=Capabilities)
    frame_end: int = 80

    @property
    def unique_key(self) -> str:
        """Stable id for the entity registry.

        ``deviceId`` is assigned by the cloud and stays with the indoor unit.
        The box URL is not part of it.
        """
        return f"{self.device_id}|{self.echonet_object}"


def mode_has_setpoint(device: Aircon) -> bool:
    """Whether the current mode uses a temperature setpoint."""
    if device.mode == MODE_AUTO:
        return device.caps.auto_has_setpoint
    return device.mode in {MODE_COOL, MODE_HEAT}


def with_updates(device: Aircon, **updates: object) -> Aircon:
    """Copy a device with a few fields replaced. Used after a command succeeds."""
    return replace(device, **updates)


def parse_capabilities(blob: bytes | None) -> Capabilities:
    """Read the F0 bits the app uses to show or hide controls."""
    if not blob or len(blob) < 16:
        return Capabilities()
    byte8 = blob[8]
    return Capabilities(
        model_gen=blob[4],
        half_degree=bool(byte8 & 0x10),
        soft=bool(byte8 & 0x20),
        speed=bool(byte8 & 0x40),
        cocoro=bool(byte8 & 0x06),
        auto_has_setpoint=blob[13] == 0x05,
        a04=len(blob) > 29 and blob[29] >= 0x38,
        patrol=blob[6] == 0x41,
    )


def parse_echonet_property(hex_text: str) -> dict[int, bytes]:
    """Decode a boxInfo echonetProperty string.

    Captured frames are ``EOJ (3) | 05 ff | 01 | ESV 0x72 | OPC | EPC/PDC/EDT``.
    The extra ``01`` sits between the two header bytes and the ESV.
    """
    try:
        raw = bytes.fromhex(hex_text)
    except ValueError as err:
        raise ValueError("Unreadable echonet property") from err
    if len(raw) < 8 or raw[6] != 0x72:
        raise ValueError("Unreadable echonet property")
    count = raw[7]
    offset = 8
    props: dict[int, bytes] = {}
    for _ in range(count):
        if offset + 2 > len(raw):
            raise ValueError("Truncated echonet property")
        epc = raw[offset]
        length = raw[offset + 1]
        start = offset + 2
        end = start + length
        if end > len(raw):
            raise ValueError("Truncated echonet property")
        props[epc] = raw[start:end]
        offset = end
    if offset != len(raw):
        raise ValueError("Unreadable echonet property")
    return props


def _temp_byte(value: int) -> float | None:
    """Decode one BB or BE byte. Negative temperatures are raw - 256."""
    if value in _INVALID_TEMPS:
        return None
    if value >= 0x80:
        return float(value - 256)
    return float(value)


def _unsigned(data: bytes) -> int:
    return int.from_bytes(data, "big")


def _lookup(table: dict[str, str], code: str | None) -> str | None:
    if code is None:
        return None
    folded = {key.upper(): value for key, value in table.items()}
    return folded.get(code.upper())


def _flag(byte: int | None) -> bool | None:
    if byte is None:
        return None
    if byte == 0x01:
        return True
    if byte == 0x00:
        return False
    return None


def aircon_from_parts(
    *,
    box_id: str,
    device_id: int,
    echonet_node: str,
    echonet_object: str,
    name: str,
    model: str | None,
    props: dict[int, bytes],
) -> Aircon:
    """Build an air conditioner from already-split ECHONET properties."""
    caps = parse_capabilities(props.get(0xF0))
    fa = props.get(0xFA)
    frame_end = fa_end_index(caps.model_gen, len(fa) if fa is not None else None)

    def fa_byte(index: int) -> int | None:
        if fa is None:
            return None
        offset = index - 1
        if offset < 0 or offset >= len(fa):
            return None
        return fa[offset]

    setpoint: float | None = None
    if props.get(0xB3):
        setpoint = float(props[0xB3][0])
    if caps.half_degree:
        half_byte = fa_byte(FA_SETPOINT)
        if half_byte is not None:
            half = half_byte / 2
            # FA byte 26 can lag behind B3. Keep B3 unless FA agrees within 0.5 °C.
            if 10 <= half <= 40 and (setpoint is None or abs(half - setpoint) <= 0.5):
                setpoint = half

    humidity: int | None = None
    if props.get(0xBA):
        raw_humidity = props[0xBA][0]
        if raw_humidity not in _INVALID_HUMIDITY and raw_humidity <= 100:
            humidity = raw_humidity

    power_w = _unsigned(props[0x84]) if props.get(0x84) else None
    energy = None
    if 0x85 in props and len(props[0x85]) >= 4:
        # ECHONET cumulative energy is 0.001 kWh per count.
        energy = _unsigned(props[0x85][:4]) / 1000

    fault: bool | None = None
    if props.get(0x88):
        fault = props[0x88][0] == int(FAULT_CODE, 16)

    plasmacluster: bool | None = None
    if props.get(0xCF):
        code = f"{props[0xCF][0]:02x}"
        if code == PLASMACLUSTER_ON:
            plasmacluster = True
        elif code == PLASMACLUSTER_OFF:
            plasmacluster = False

    power: bool | None = None
    if props.get(0x80):
        code = f"{props[0x80][0]:02x}"
        if code == POWER_ON:
            power = True
        elif code == POWER_OFF:
            power = False

    mode = None
    if props.get(0xB0):
        mode = _lookup(_MODE_BY_CODE, f"{props[0xB0][0]:02x}")

    fan = None
    if props.get(0xA0):
        fan = _lookup(_FAN_BY_CODE, f"{props[0xA0][0]:02x}")

    room = _temp_byte(props[0xBB][0]) if props.get(0xBB) else None
    outdoor = _temp_byte(props[0xBE][0]) if props.get(0xBE) else None

    vane_v = None
    vane_byte = fa_byte(FA_VANE_VERTICAL)
    if vane_byte is not None:
        vane_v = _VANE_V_BY_CODE.get(f"{vane_byte:02x}".upper())
    vane_h = None
    vane_byte = fa_byte(FA_VANE_HORIZONTAL)
    if vane_byte is not None:
        vane_h = _VANE_H_BY_CODE.get(f"{vane_byte:02x}".upper())

    return Aircon(
        box_id=box_id,
        device_id=device_id,
        echonet_node=echonet_node,
        echonet_object=echonet_object,
        name=name,
        model=model,
        power=power,
        mode=mode,
        setpoint=setpoint,
        room_temp=room,
        humidity=humidity,
        outdoor_temp=outdoor,
        fan=fan,
        vane_vertical=vane_v,
        vane_horizontal=vane_h,
        plasmacluster=plasmacluster,
        power_w=power_w,
        energy_kwh=energy,
        fault=fault,
        sleep=_flag(fa_byte(FA_SLEEP)),
        soft=_flag(fa_byte(FA_SOFT)),
        speed=_flag(fa_byte(FA_SPEED)),
        cocoro_air=_flag(fa_byte(FA_COCORO)),
        super_jet=_flag(fa_byte(FA_SUPER_JET)),
        caps=caps,
        frame_end=frame_end,
    )


def _iter_records(payload: dict):
    """Yield (box id, device record) from a raw boxInfo body or the fixture."""
    if "devices" in payload:
        for item in payload["devices"]:
            yield item.get("boxId"), item
        return
    for box in payload.get("box") or []:
        box_id = box.get("boxId")
        data = box.get("echonetData")
        if not data:
            yield box_id, box
            continue
        for item in data:
            yield box_id, item


def aircon_from_record(box_id: str | None, item: dict) -> Aircon | None:
    """Parse one device record. Returns None when it is not an air conditioner."""
    if not box_id:
        return None
    label = item.get("labelData") or {}
    device_type = label.get("deviceType")
    echonet_object = str(item.get("echonetObject") or "")
    if device_type and device_type != "AIR_CON":
        return None
    if not device_type and not echonet_object.startswith("0130"):
        return None
    hex_text = item.get("echonetProperty")
    node = item.get("echonetNode")
    device_id = item.get("deviceId")
    if not hex_text or not node or device_id is None or not echonet_object:
        return None
    name = str(label.get("name") or item.get("model") or "Sharp AC").strip() or "Sharp AC"
    model = item.get("model")
    props = parse_echonet_property(str(hex_text))
    return aircon_from_parts(
        box_id=str(box_id),
        device_id=int(device_id),
        echonet_node=str(node),
        echonet_object=echonet_object,
        name=name,
        model=str(model) if model else None,
        props=props,
    )


def terminal_listed(info: object, terminal_app_id: str) -> bool:
    """True when ``terminalAppInfo`` already contains this terminal id."""
    if isinstance(info, str):
        return info == terminal_app_id
    if isinstance(info, dict):
        if info.get("terminalAppId") == terminal_app_id:
            return True
        return any(terminal_listed(value, terminal_app_id) for value in info.values())
    if isinstance(info, list):
        return any(terminal_listed(item, terminal_app_id) for item in info)
    return False


def _box_records(payload: dict) -> list[tuple[str, dict]]:
    """Box id plus the object that carries ``terminalAppInfo``."""
    if "box" in payload:
        return [
            (str(box["boxId"]), box)
            for box in payload.get("box") or []
            if isinstance(box, dict) and box.get("boxId")
        ]
    return [
        (str(item["boxId"]), item)
        for item in payload.get("devices") or []
        if isinstance(item, dict) and item.get("boxId")
    ]


def boxes_missing_terminal(payload: dict, terminal_app_id: str | None) -> list[str]:
    """Box ids that do not already list this terminal.

    A missing ``terminalAppInfo`` means the terminal is not paired. Pairing is
    skipped only when the id is actually present.
    """
    missing: list[str] = []
    for box_id, record in _box_records(payload):
        if terminal_app_id and terminal_listed(record.get("terminalAppInfo"), terminal_app_id):
            continue
        if box_id not in missing:
            missing.append(box_id)
    return missing


def box_lists_terminal(payload: dict, box_id: str, terminal_app_id: str) -> bool:
    """True when one box's ``terminalAppInfo`` contains this terminal."""
    for found_id, record in _box_records(payload):
        if found_id == box_id and terminal_listed(record.get("terminalAppInfo"), terminal_app_id):
            return True
    return False


def split_box_info(payload: dict) -> tuple[list[str], list[Aircon]]:
    """Return every box id, plus the air conditioners that decoded."""
    box_ids: list[str] = []
    devices: list[Aircon] = []
    for box_id, item in _iter_records(payload):
        if box_id and box_id not in box_ids:
            box_ids.append(str(box_id))
        if not isinstance(item, dict):
            continue
        try:
            device = aircon_from_record(str(box_id) if box_id else None, item)
        except ValueError:
            continue
        if device is not None:
            devices.append(device)
    return box_ids, devices


def device_diagnostics(device: Aircon) -> dict:
    """Status safe to download. Identifiers that locate the unit are omitted."""
    return {
        "name": device.name,
        "model": device.model,
        "power": device.power,
        "mode": device.mode,
        "setpoint": device.setpoint,
        "room_temp": device.room_temp,
        "humidity": device.humidity,
        "outdoor_temp": device.outdoor_temp,
        "outdoor_stale": device.outdoor_stale,
        "fan": device.fan,
        "vane_vertical": device.vane_vertical,
        "vane_horizontal": device.vane_horizontal,
        "plasmacluster": device.plasmacluster,
        "power_w": device.power_w,
        "energy_kwh": device.energy_kwh,
        "fault": device.fault,
        "sleep": device.sleep,
        "soft": device.soft,
        "speed": device.speed,
        "cocoro_air": device.cocoro_air,
        "super_jet": device.super_jet,
        "half_degree": device.caps.half_degree,
        "soft_capable": device.caps.soft,
        "speed_capable": device.caps.speed,
        "cocoro_capable": device.caps.cocoro,
        "auto_has_setpoint": device.caps.auto_has_setpoint,
        "frame_end": device.frame_end,
    }
