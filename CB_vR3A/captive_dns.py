import usocket as socket
import gc

from server import Server


class DNSQuery:
    def __init__(self, data):
        self.data = data
        self.domain = ""
        if len(data) < 17 or data[2] & 0x80 or data[4:6] != b"\x00\x01":
            raise ValueError("Expected one DNS question")
        head = 12
        while True:
            if head >= len(data):
                raise ValueError("Truncated DNS name")
            length = data[head]
            head += 1
            if length == 0:
                break
            if length > 63 or head + length > len(data):
                raise ValueError("Invalid DNS label")
            head += length
        if head + 4 > len(data):
            raise ValueError("Truncated DNS question")
        self.question_end = head + 4
        self.is_ipv4 = data[head:head + 4] == b"\x00\x01\x00\x01"

    def answer(self, ip_addr):
        # Return an A record for IPv4 queries. AAAA/other types receive an
        # empty successful answer, never an incorrectly typed IPv4 record.
        count = b"\x00\x01" if self.is_ipv4 else b"\x00\x00"
        packet = self.data[:2] + b"\x81\x80\x00\x01" + count + b"\x00\x00\x00\x00"
        packet += self.data[12:self.question_end]
        if self.is_ipv4:
            packet += b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04"
            packet += bytes(map(int, ip_addr.split(".")))
        return packet


class DNSServer(Server):
    def __init__(self, poller, ip_addr):
        super().__init__(poller, 53, socket.SOCK_DGRAM, "DNS Server")
        self.ip_addr = ip_addr
        try:
            self.sock.setblocking(False)
        except Exception:
            self.stop(poller)
            raise

    def serve_once(self):
        try:
            data, sender = self.sock.recvfrom(512)
        except OSError:
            return
        try:
            reply = DNSQuery(data).answer(self.ip_addr)
            self.sock.sendto(reply, sender)
        except (ValueError, OSError):
            # Malformed packets or a disconnected client must not stop HTTP.
            pass

    def handle(self, sock, event, others):
        # server doesn't spawn other sockets, so only respond to its own socket
        if sock is not self.sock:
            return

        # check the DNS question, and respond with an answer
        try:
            data, sender = sock.recvfrom(1024)
            request = DNSQuery(data)

            print("Sending {:s} -> {:s}".format(request.domain, self.ip_addr))
            sock.sendto(request.answer(self.ip_addr), sender)

            # help MicroPython with memory management
            del request
            gc.collect()
        except Exception as e:
            print("DNS server exception:", e)
