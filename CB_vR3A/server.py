import usocket as socket
import uselect as select


class Server:
    def __init__(self, poller, port, sock_type, name):
        self.name = name
        # create socket with correct type: stream (TCP) or datagram (UDP)
        self.sock = socket.socket(socket.AF_INET, sock_type)

        self.poller = poller
        try:
            addr = ("0.0.0.0", port)
            # allow new requests while still sending last response
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind(addr)
            # register to get event updates only after bind succeeds
            if self.poller is not None:
                self.poller.register(self.sock, select.POLLIN)
        except Exception:
            try:
                self.sock.close()
            except Exception:
                pass
            raise

        print(self.name, "listening on", addr)

    def stop(self, poller):
        try:
            if poller is not None:
                poller.unregister(self.sock)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass
        print(self.name, "stopped")
