"""Sharp Cloud exceptions."""


class SharpCloudError(Exception):
    """Base SDK exception."""


class AuthenticationError(SharpCloudError):
    """Authentication failed."""


class AuthorizationError(SharpCloudError):
    """Authorization failed."""


class ApiError(SharpCloudError):
    """Sharp API returned an error."""


class DeviceNotFound(ApiError):
    """Requested device was not found."""


class ControlFailed(ApiError):
    """Device control failed."""


class PairingRequired(ApiError):
    """Device is not paired."""