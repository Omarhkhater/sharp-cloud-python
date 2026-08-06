"""
Global constants for the Sharp COCORO AIR SDK.
"""

API_BASE_URL = "https://eg-hms.cloudlabs.sharp.co.jp"

COCORO_BASE_URL = "https://cboard.cloudlabs.sharp.co.jp"

AZURE_B2C_BASE = (
    "https://elaiotb2cprd.b2clogin.com/"
    "elaiotb2cprd.onmicrosoft.com/"
    "b2c_1a_signup_signin/oauth2/v2.0"
)

AUTHORIZE_ENDPOINT = f"{AZURE_B2C_BASE}/authorize"
TOKEN_ENDPOINT = f"{AZURE_B2C_BASE}/token"

CLIENT_ID = "66e7f408-b1bf-46a8-b467-32204ab52f93"

REDIRECT_URI = "https://sharp-cocoroair-egypt"

SCOPE = "openid offline_access"