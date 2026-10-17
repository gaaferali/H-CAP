from rest_framework.throttling import AnonRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    scope = "login"


class RefreshRateThrottle(AnonRateThrottle):
    scope = "refresh"
