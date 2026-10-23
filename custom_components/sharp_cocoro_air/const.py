"""Constants for the Sharp Air Egypt integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "sharp_cocoro_air"

MANUFACTURER = "Sharp"

# Public app constants from the SHARP Air Control APK. These ship in every
# copy of the app; they are not account credentials.
HMS_API = "https://eg-hms.cloudlabs.sharp.co.jp/hems/pfApi/ta/"
APP_SECRET = "pngtfljRoYsJE9NW7opn1t2cXA5MtZDKbwon368hs80="
SERVICE_NAME = "sharp-egy"
USER_AGENT = "smartlink_v200a_eg HomeAssistant"
# The cloud records this appName on the paired terminal. Keep it aligned with
# the Android build the live pairing test used.
APP_NAME = "spremote_a_eg:1:1.0.4"

B2C_AUTHORIZE = (
    "https://elaiotb2cprd.b2clogin.com/elaiotb2cprd.onmicrosoft.com/"
    "b2c_1a_signup_signin/oauth2/v2.0/authorize"
)
B2C_CLIENT_ID = "66e7f408-b1bf-46a8-b467-32204ab52f93"
B2C_REDIRECT = "https://sharp-cocoroair-egypt"
B2C_SCOPE = "openid offline_access"

SCAN_INTERVAL = timedelta(seconds=60)
# A box that rejects pairing is not tried again on the 60-second poll.
PAIR_BACKOFF_SECONDS = 60 * 60
# The outdoor coil is not measured while the indoor unit is off (BE = 0x7E).
# Keep the last reading this long, then let the sensor go unknown.
OUTDOOR_HOLD_S = 6 * 60 * 60

# Rapid temperature taps collapse into one write. The unit keeps applying a
# command for several seconds after controlResult, so the next write to the
# same box waits, and an unmatch is checked against a later status read.
SETPOINT_DEBOUNCE_S = 1.5
WRITE_SETTLE_S = 2.0
VERIFY_DELAY_S = 12.0
PENDING_GRACE_S = 30.0
# Single-value EPCs that may be sent once more after unmatch. FA frames are not.
RETRY_EPCS = {"80", "b0", "a0", "b3", "cf"}

# F0 byte 13 selects a per-model range table that was not in the capture.
# The slider is the usual Sharp remote span. The cloud rejects anything outside it.
TEMP_MIN = 16.0
TEMP_MAX = 30.0

CONF_REDIRECT_URL = "redirect_url"
CONF_TERMINAL_APP_ID = "terminal_app_id"
CONF_COOKIES = "cookies"
CONF_USER_ID = "user_id"
# Off unless the user opts in. Covers FA-frame writes that were not confirmed live.
CONF_EXPERIMENTAL = "experimental_controls"

# ECHONET operation-mode codes (EPC B0).
MODE_AUTO = "auto"
MODE_COOL = "cool"
MODE_HEAT = "heat"
MODE_DRY = "dry"
MODE_FAN = "fan_only"

MODES: tuple[tuple[str, str], ...] = (
    (MODE_AUTO, "41"),
    (MODE_COOL, "42"),
    (MODE_HEAT, "43"),
    (MODE_DRY, "44"),
    (MODE_FAN, "45"),
)

# EPC A0. Names follow the app's fan table.
# Rapid (38) was rejected live: controlResult came back "unmatch", and the
# unit briefly reported 37. It is not in the default list.
FAN_RAPID = "rapid"
FAN_MODES: tuple[tuple[str, str], ...] = (
    ("auto", "41"),
    ("quiet", "31"),
    ("very_weak", "33"),
    ("weak", "35"),
    ("strong", "37"),
    (FAN_RAPID, "38"),
)

# FA frame, 1-based index 49 (0-based byte 48). Section 4.2's table says 48,
# and section 4.4 says 49. The captured frames put vertical auto/up here.
VANE_VERTICAL: tuple[tuple[str, str], ...] = (
    ("auto", "00"),
    ("up", "01"),
    ("horizontal", "02"),
    ("down1", "03"),
    ("down2", "04"),
    ("down", "05"),
    ("swing", "07"),
)

# FA frame, 1-based index 50 (0-based byte 49). The captured frames put
# horizontal straight (01) here, which is what section 4.4 specifies.
VANE_HORIZONTAL: tuple[tuple[str, str], ...] = (
    ("auto", "0A"),
    ("straight", "01"),
    ("left", "02"),
    ("right", "03"),
    ("wide", "07"),
    ("enclose", "08"),
    ("swing", "0F"),
)

POWER_ON = "30"
POWER_OFF = "31"
PLASMACLUSTER_ON = "41"
PLASMACLUSTER_OFF = "42"
FAULT_CODE = "41"

# 1-based indexes inside the FA payload, as used by the app's frame builder.
FA_COCORO = 17
FA_SPEED = 19
FA_SETPOINT = 27
FA_SOFT = 42
FA_SLEEP = 43
FA_VANE_VERTICAL = 49
FA_VANE_HORIZONTAL = 50
# 1-based index 51 is 0-based byte 50. Live read: 01 on, 00 off.
# A sparse write of idx 51 = 01 was rejected (controlResult "unmatch").
FA_SUPER_JET = 51
