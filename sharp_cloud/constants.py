"""Global constants for the Sharp COCORO AIR SDK."""

from enum import StrEnum

PORTAL_BASE_URL = "https://cboard.cloudlabs.sharp.co.jp/"
COCORO_APP_BASE_URL = "https://cboard.cloudlabs.sharp.co.jp/cocoroapp/"
PROPERTY_URL_EGYPT = (
    "https://cboard.cloudlabs.sharp.co.jp/cocoroapp/static/air/property/egypt/"
)

API_BASE_URL = "https://eg-hms.cloudlabs.sharp.co.jp/hems/pfApi/ta/"
API_HOST = "https://eg-hms.cloudlabs.sharp.co.jp/"

B2C_BASE_URL = (
    "https://elaiotb2cprd.b2clogin.com/"
    "elaiotb2cprd.onmicrosoft.com/"
    "b2c_1a_signup_signin/oauth2/v2.0/"
)
B2C_AUTHORIZE_URL = f"{B2C_BASE_URL}authorize"
B2C_TOKEN_URL = f"{B2C_BASE_URL}token"

B2C_DELETE_ACCOUNT_URL = (
    "https://elaiotb2cprd.b2clogin.com/"
    "elaiotb2cprd.onmicrosoft.com/oauth2/v2.0/authorize"
    "?p=B2C_1A_DEMO_DELETE_MY_ACCOUNT"
    "&client_id=66e7f408-b1bf-46a8-b467-32204ab52f93"
    "&redirect_uri=https%3A%2F%2Fsharp-cocoroair-egypt"
    "&scope=openid"
    "&response_type=id_token"
    "&prompt=login"
)

CLIENT_ID = "66e7f408-b1bf-46a8-b467-32204ab52f93"
CLIENT_SECRET = "CocoroAppAPI/b4bf45643bacc496f91b360a6dd888fd"

SCOPE = "openid offline_access"

REDIRECT_URI = "https://sharp-cocoroair-egypt"
TOKEN_REDIRECT_URI = "sharp-cocoroair-taiwan://login"

SERVICE_NAME_EGYPT = "sharp-egy"

SETTING_LOGIN_PATH = "setting/login/"
SETTING_TERMINAL_PATH = "setting/terminal/"
SETTING_TERMINAL_APP_ID_PATH = "setting/terminalAppId/"
SETTING_USER_INFO_PATH = "setting/userInfo"
SETTING_PAIRING_PATH = "setting/pairing/"

CONTROL_DEVICE_PROPERTY_PATH = "control/deviceProperty"
CONTROL_DEVICE_STATUS_PATH = "control/deviceStatus"
CONTROL_DEVICE_CONTROL_PATH = "control/deviceControl"
CONTROL_RESULT_PATH = "control/controlResult"


class Region(StrEnum):
    """Supported Sharp Cloud regions."""

    EGYPT = "egypt"


class Endpoint(StrEnum):
    """Sharp Cloud API endpoints."""

    SETTING_LOGIN = SETTING_LOGIN_PATH
    SETTING_TERMINAL = SETTING_TERMINAL_PATH
    SETTING_TERMINAL_APP_ID = SETTING_TERMINAL_APP_ID_PATH
    SETTING_USER_INFO = SETTING_USER_INFO_PATH
    SETTING_PAIRING = SETTING_PAIRING_PATH

    CONTROL_DEVICE_PROPERTY = CONTROL_DEVICE_PROPERTY_PATH
    CONTROL_DEVICE_STATUS = CONTROL_DEVICE_STATUS_PATH
    CONTROL_DEVICE_CONTROL = CONTROL_DEVICE_CONTROL_PATH
    CONTROL_RESULT = CONTROL_RESULT_PATH