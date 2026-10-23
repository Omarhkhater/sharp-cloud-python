"""Parser, FA frame, and command builders against the redacted capture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.sharp_cocoro_air.const import APP_SECRET, B2C_CLIENT_ID
from custom_components.sharp_cocoro_air.protocol import (
    ControlFrame,
    aircon_from_record,
    authorize_url,
    cmd_fan,
    cmd_mode,
    cmd_plasmacluster,
    cmd_power,
    cmd_setpoint,
    cmd_sleep,
    cmd_vanes,
    control_body,
    device_diagnostics,
    extract_auth_code,
    parse_echonet_property,
    split_box_info,
    with_updates,
)

ROOT = Path(__file__).resolve().parents[1]


def _by_name(devices, name):
    return next(device for device in devices if device.name == name)


def test_fixture_matches_the_live_decode(boxinfo):
    """The three captured units decode to the states that matched reality."""
    _box_ids, devices = split_box_info(boxinfo)
    assert len(devices) == 3
    living = _by_name(devices, "Living Room AC")
    kids = _by_name(devices, "Kid’s Bedroom AC")
    bedroom = _by_name(devices, "Bedroom")

    assert living.model == "AY-XP12YHE"
    assert living.power is True
    assert living.mode == "cool"
    assert living.setpoint == 21.0
    assert living.room_temp == 23
    assert living.humidity == 53
    assert living.outdoor_temp == 26
    assert living.fan == "auto"
    assert living.plasmacluster is True
    assert living.power_w == 806
    assert living.energy_kwh == pytest.approx(2367.458)
    assert living.fault is False
    assert living.caps.half_degree is True
    assert living.caps.soft is False
    assert living.caps.speed is False
    assert living.caps.cocoro is False
    assert living.caps.auto_has_setpoint is False
    assert living.caps.a04 is False
    assert living.caps.model_gen == 0x21
    assert living.frame_end == 80
    assert living.vane_vertical == "auto"
    assert living.vane_horizontal == "straight"
    assert living.sleep is False

    props = parse_echonet_property(boxinfo["devices"][0]["echonetProperty"])
    # 1-based FA index 27 is byte 26, and it agrees with the integer setpoint.
    assert props[0xFA][26] == 0x2A
    # Vertical vane is 1-based index 49 (byte 48, hex chars 96:98).
    assert props[0xFA][48] == 0x00
    assert props[0xFA][49] == 0x01

    assert kids.model == "AY-XP12YHE"
    assert kids.power is False
    assert kids.mode == "cool"
    assert kids.setpoint == 23.0
    assert kids.room_temp == 25
    assert kids.humidity == 55
    assert kids.outdoor_temp is None
    assert kids.fan == "very_weak"
    assert kids.plasmacluster is False
    assert kids.power_w == 1
    assert kids.energy_kwh == pytest.approx(1693.654)
    assert kids.vane_vertical == "up"

    assert bedroom.model == "AY-XP18YHE"
    assert bedroom.power is True
    assert bedroom.setpoint == 21.0
    assert bedroom.room_temp == 21
    assert bedroom.humidity == 62
    assert bedroom.outdoor_temp == 25
    assert bedroom.fan == "auto"
    assert bedroom.plasmacluster is True
    assert bedroom.power_w == 209
    assert bedroom.energy_kwh == pytest.approx(4768.867)
    assert bedroom.caps.half_degree is True


def test_raw_boxinfo_shape_parses_the_same_device(boxinfo):
    """The cloud returns box[].echonetData[], not the flattened fixture."""
    item = boxinfo["devices"][0]
    payload = {"box": [{"boxId": item["boxId"], "echonetData": [item]}]}
    box_ids, devices = split_box_info(payload)
    assert box_ids == [item["boxId"]]
    assert devices[0].name == "Living Room AC"
    assert devices[0].setpoint == 21.0


def test_invalid_sensor_bytes_are_unavailable():
    """125/126 and the humidity sentinels are not real readings."""
    body = bytearray([0x01, 0x30, 0x01, 0x05, 0xFF, 0x01, 0x72, 5])
    for epc, value in ((0x80, 0x31), (0xB3, 22), (0xBA, 0xFD), (0xBB, 126), (0xBE, 125)):
        body.extend((epc, 1, value))
    # Fix OPC: we wrote 5 properties. The header OPC was set to 5. Good.
    device = aircon_from_record(
        "box-1",
        {
            "boxId": "box-1",
            "deviceId": 1,
            "echonetNode": "00-11-22-33-44-55",
            "echonetObject": "013001",
            "model": "AY-XP12YHE",
            "echonetProperty": body.hex(),
            "labelData": {"name": "Test", "deviceType": "AIR_CON"},
        },
    )
    assert device is not None
    assert device.power is False
    assert device.setpoint == 22
    assert device.humidity is None
    assert device.room_temp is None
    assert device.outdoor_temp is None


def test_fault_code_and_half_degree_frame():
    """EPC 88 value 41 is a fault, and FA byte 26 is the half-degree setpoint."""
    fa = bytearray(80)
    fa[26] = 0x35  # 26.5 C
    f0 = bytearray(32)
    f0[8] = 0x10
    f0[4] = 21
    body = bytearray([0x01, 0x30, 0x01, 0x05, 0xFF, 0x01, 0x72, 3])
    for epc, edt in ((0x88, bytes([0x41])), (0xF0, bytes(f0)), (0xFA, bytes(fa))):
        body.append(epc)
        body.append(len(edt))
        body.extend(edt)
    device = aircon_from_record(
        "box-1",
        {
            "deviceId": 4,
            "echonetNode": "00-11-22-33-44-55",
            "echonetObject": "013001",
            "echonetProperty": body.hex(),
            "labelData": {"name": "Half", "deviceType": "AIR_CON"},
        },
    )
    assert device is not None
    assert device.fault is True
    assert device.caps.half_degree is True
    assert device.setpoint == 26.5


def test_empty_frame_and_half_degree_byte():
    """An empty 80-byte frame is all zeros, and 26.5 C lands on byte 26."""
    assert ControlFrame(80).encode() == "00" * 80
    encoded = ControlFrame(80).set(27, "35").encode()
    raw = bytes.fromhex(encoded)
    assert len(raw) == 80
    assert raw[26] == 0x35
    assert raw[0] == sum(raw[1:]) % 256
    assert set(raw[16:26]) == {0}
    assert set(raw[27:]) == {0}


def test_integer_setpoint_matches_the_live_payload():
    """Whole degrees are a single B3 valueRange, including when half degrees exist."""
    status = cmd_setpoint(22, half_degree=True, frame_end=80)
    assert status == [
        {
            "statusCode": "b3",
            "valueType": "valueRange",
            "valueRange": {"type": "int", "code": "22"},
        }
    ]
    body = control_body(1, "00-11-22-33-44-55", "013001", status)
    assert body == {
        "controlList": [
            {
                "deviceId": 1,
                "echonetNode": "00-11-22-33-44-55",
                "echonetObject": "013001",
                "status": status,
            }
        ]
    }


def test_half_degree_setpoint_uses_the_fa_frame_only():
    """0.5 C steps are an FA frame, not B3."""
    status = cmd_setpoint(26.5, half_degree=True, frame_end=80)
    assert status[0]["statusCode"] == "fa"
    assert status[0]["valueType"] == "valueBinary"
    raw = bytes.fromhex(status[0]["valueBinary"]["code"])
    assert raw[26] == 0x35
    assert "b3" not in json.dumps(status)
    with pytest.raises(ValueError):
        cmd_setpoint(26.5, half_degree=False, frame_end=80)


def test_mode_power_fan_and_vanes():
    """Commands follow the EPC tables and do not force Plasmacluster on."""
    assert cmd_power(False) == [
        {"statusCode": "80", "valueType": "valueSingle", "valueSingle": {"code": "31"}}
    ]
    assert cmd_power(True)[0]["valueSingle"]["code"] == "30"
    mode = cmd_mode("cool")
    assert [item["statusCode"] for item in mode] == ["80", "b0"]
    assert mode[1]["valueSingle"]["code"] == "42"
    assert "cf" not in json.dumps(mode)

    fan = cmd_fan("quiet")
    assert fan[0]["statusCode"] == "a0"
    assert fan[0]["valueSingle"]["code"] == "31"

    vanes = cmd_vanes(vertical="swing", horizontal="auto", frame_end=80)
    raw = bytes.fromhex(vanes[0]["valueBinary"]["code"])
    assert raw[48] == 0x07
    assert raw[49] == 0x0A

    sleep = cmd_sleep(True, 80)
    assert bytes.fromhex(sleep[0]["valueBinary"]["code"])[42] == 0x01

    plasma = cmd_plasmacluster(False)
    assert plasma[0]["statusCode"] == "cf"
    assert plasma[0]["valueSingle"]["code"] == "42"


def test_optimistic_update_changes_only_the_setpoint(boxinfo):
    """A successful command keeps the rest of the last reading."""
    _ids, devices = split_box_info(boxinfo)
    living = _by_name(devices, "Living Room AC")
    updated = with_updates(living, setpoint=22.0)
    assert updated.setpoint == 22.0
    assert updated.room_temp == living.room_temp
    assert updated.mode == "cool"
    assert living.setpoint == 21.0


def test_authorize_url_has_no_pkce_or_secret():
    """The browser link is the public authorize URL."""
    url = authorize_url()
    assert "elaiotb2cprd.b2clogin.com" in url
    assert B2C_CLIENT_ID in url
    assert "redirect_uri=https%3A%2F%2Fsharp-cocoroair-egypt" in url
    assert "response_type=code" in url
    assert "code_challenge" not in url
    assert "client_secret" not in url
    assert APP_SECRET not in url


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://sharp-cocoroair-egypt/?code=abc.def-123", "abc.def-123"),
        ("https://sharp-cocoroair-egypt?code=abc%2Fdef&state=1", "abc/def"),
        ("sharp-cocoroair-egypt://callback?code=from-app", "from-app"),
        ("?code=just-the-query", "just-the-query"),
        ("longenoughcode", "longenoughcode"),
        ("https://sharp-cocoroair-egypt/", None),
        ("not a code", None),
        ("", None),
        ("short", None),
    ],
)
def test_extract_auth_code(value, expected):
    """Accept the unreachable redirect URL or a bare code."""
    assert extract_auth_code(value) == expected


def test_diagnostics_omit_identifiers(boxinfo):
    """Downloaded diagnostics keep status and drop locating ids."""
    _ids, devices = split_box_info(boxinfo)
    text = json.dumps([device_diagnostics(device) for device in devices])
    assert "BOX_ID" not in text
    assert "<NODE>" not in text
    assert "device_id" not in text
    assert "echonet_node" not in text
    assert "Living Room AC" in text
    assert "806" in text


def test_package_metadata():
    """HACS and the manifest describe a cloud-polling config-flow integration."""
    manifest = json.loads(
        (ROOT / "custom_components" / "sharp_cocoro_air" / "manifest.json").read_text()
    )
    hacs = json.loads((ROOT / "hacs.json").read_text())
    strings = (ROOT / "custom_components" / "sharp_cocoro_air" / "strings.json").read_text()
    translations = (
        ROOT / "custom_components" / "sharp_cocoro_air" / "translations" / "en.json"
    ).read_text()
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["config_flow"] is True
    assert manifest["version"]
    assert "quality_scale" not in manifest
    assert hacs["domains"] == ["sharp_cocoro_air"]
    assert strings == translations
    assert "password" not in strings.lower()
