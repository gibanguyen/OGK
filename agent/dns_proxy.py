"""Loopback-only UDP DNS filtering proxy for explicitly enabled lab demos."""
import ipaddress
import socket
import socketserver
import threading

from dnslib import DNSRecord, QTYPE, RCODE

from shared.domain_catalog import (
    PERMANENT_ALLOW_DOMAINS, category_for, matches_domain, root_domain,
)


def serve_dns(policy_provider, event_callback, upstream: str | None, port: int = 5353) -> None:
    if upstream:
        try:
            ipaddress.IPv4Address(upstream)
        except ipaddress.AddressValueError as exc:
            raise ValueError("OGK_DNS_UPSTREAM must be an IPv4 address to avoid system-DNS lookups") from exc

    class DNSHandler(socketserver.BaseRequestHandler):
        def handle(self):
            packet, client_socket = self.request
            try:
                query = DNSRecord.parse(packet)
                qname = str(query.q.qname).rstrip(".").lower()
                policy_record = policy_provider()
                policy = policy_record.get("payload", {}) if policy_record else {}
                version = int(policy_record.get("version", 0)) if policy_record else 0
                safe_root = root_domain(qname)
                if query.q.qtype in {QTYPE.A, QTYPE.AAAA, QTYPE.CNAME} and "." in qname and not qname.endswith(".in-addr.arpa"):
                    event_callback("domain_query", safe_root, version)

                allow_list = list(PERMANENT_ALLOW_DOMAINS) + list(policy.get("allow_domains", []))
                explicit_allow = matches_domain(qname, allow_list)
                explicit_block = matches_domain(qname, policy.get("blocked_domains", []))
                category = category_for(qname)
                category_block = category in policy.get("blocked_categories", [])
                blocked = not explicit_allow and (explicit_block is not None or category_block)

                if blocked:
                    reason = f"explicit domain rule {explicit_block}" if explicit_block else f"category {category}"
                    print(f"DNS blocked {safe_root}; reason: {reason}")
                    reply = query.reply()
                    reply.header.rcode = RCODE.NXDOMAIN
                    client_socket.sendto(reply.pack(), self.client_address)
                    if query.q.qtype in {QTYPE.A, QTYPE.AAAA, QTYPE.CNAME} and "." in qname:
                        event_callback("blocked_domain", safe_root, version)
                    return

                if not upstream:
                    reply = query.reply()
                    reply.header.rcode = RCODE.SERVFAIL
                    client_socket.sendto(reply.pack(), self.client_address)
                    return

                # Forward only to an IP address explicitly supplied by the operator.
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as resolver:
                    resolver.settimeout(3)
                    resolver.sendto(packet, (upstream, 53))
                    response, _ = resolver.recvfrom(4096)
                client_socket.sendto(response, self.client_address)
            except Exception:
                try:
                    query = DNSRecord.parse(packet)
                    reply = query.reply()
                    reply.header.rcode = RCODE.SERVFAIL
                    client_socket.sendto(reply.pack(), self.client_address)
                except Exception:
                    return

    class LoopbackUDPServer(socketserver.ThreadingUDPServer):
        allow_reuse_address = True
        daemon_threads = True

    server = LoopbackUDPServer(("127.0.0.1", port), DNSHandler)
    print(f"DNS proxy listening only on 127.0.0.1:{port}; system DNS settings were not changed.")
    if port != 53:
        print("This port is for direct demo queries only; Windows and browsers normally query DNS on port 53.")
    if upstream:
        print(f"Allowed DNS queries will be forwarded to explicitly configured resolver {upstream}.")
    else:
        print("No upstream configured: blocked names get NXDOMAIN; other names get SERVFAIL.")
    server.serve_forever(poll_interval=0.5)
