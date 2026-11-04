from ipaddress import ip_address

from django.conf import settings


class TrustedProxyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        remote_addr = request.META.get("REMOTE_ADDR", "")
        try:
            remote_ip = ip_address(remote_addr)
        except ValueError:
            remote_ip = None

        trusted = remote_ip is not None and any(
            remote_ip.version == network.version and remote_ip in network
            for network in settings.TRUSTED_PROXY_IPS
        )
        if not trusted:
            request.META.pop("HTTP_X_FORWARDED_PROTO", None)
            request.META.pop("HTTP_X_FORWARDED_FOR", None)

        return self.get_response(request)
