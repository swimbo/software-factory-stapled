"""HTTPS connection using a pinned IPv4 address and the original TLS hostname.

The caller is responsible for obtaining the address from a trusted source and
installing a matching network firewall before using this connection.
"""
import http.client
import ipaddress
import socket


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port, *, gateway_ip, timeout=90):
        try:
            address = ipaddress.IPv4Address(gateway_ip)
        except (ValueError, TypeError) as exc:
            raise ValueError('a pinned IPv4 address is required') from exc
        if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local or int(address) == 0xffffffff:
            raise ValueError('invalid gateway address')
        super().__init__(host, port, timeout=timeout)
        self.gateway_ip = str(address)

    def connect(self):
        if self._tunnel_host is not None:
            raise ValueError('HTTP tunneling is not supported')
        sock = socket.create_connection((self.gateway_ip, self.port), self.timeout, self.source_address)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise
