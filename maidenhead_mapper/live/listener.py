"""UDP listener for WSJT-X messages, on the Qt event loop (no threads)."""

from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.PyQt.QtNetwork import QAbstractSocket, QHostAddress, QNetworkInterface, QUdpSocket

from ..core import wsjtx_udp


class WsjtxListener(QObject):
    """Emits `message(obj)` for each decoded WSJT-X datagram.

    Multicast (recommended): binds 0.0.0.0:port with address sharing and joins
    the group on every multicast-capable IPv4 interface, so WSJT-X can feed
    QGIS, JTAlert and GridTracker at once.
    Unicast: binds the given address:port; usually only one program can.
    """

    message = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.socket = None
        self.bad_datagrams = 0
        self.joined = []

    def is_listening(self):
        return self.socket is not None

    def start(self, address: str, port: int, multicast: bool):
        """Start listening. Raises ValueError/OSError with a readable message."""
        self.stop()
        addr = QHostAddress(address)
        if addr.isNull():
            raise ValueError(f"'{address}' is not an IP address")
        if multicast and not addr.isMulticast():
            raise ValueError(f"{address} is not a multicast address (224.0.0.0 to 239.255.255.255)")

        sock = QUdpSocket(self)
        flags = QAbstractSocket.BindFlag.ShareAddress | QAbstractSocket.BindFlag.ReuseAddressHint
        bind_to = QHostAddress(QHostAddress.SpecialAddress.AnyIPv4) if multicast else addr
        if not sock.bind(bind_to, port, flags):
            err = sock.errorString()
            sock.deleteLater()
            hint = "" if multicast else " Another program (JTAlert, GridTracker, a logger) may own it; try multicast."
            raise OSError(f"Could not listen on {address}:{port}: {err}.{hint}")

        self.joined = []
        if multicast:
            Flag = QNetworkInterface.InterfaceFlag
            ipv4 = QAbstractSocket.NetworkLayerProtocol.IPv4Protocol
            for iface in QNetworkInterface.allInterfaces():
                f = iface.flags()
                if not (f & Flag.IsUp) or not (f & Flag.CanMulticast):
                    continue
                if not any(e.ip().protocol() == ipv4 for e in iface.addressEntries()):
                    continue
                if sock.joinMulticastGroup(addr, iface):
                    self.joined.append(iface.humanReadableName())
            if not self.joined and sock.joinMulticastGroup(addr):
                self.joined.append("default")
            if not self.joined:
                err = sock.errorString()
                sock.close()
                sock.deleteLater()
                raise OSError(f"Could not join multicast group {address}: {err}")

        sock.readyRead.connect(self._read)
        self.socket = sock

    def stop(self):
        if self.socket is not None:
            try:
                self.socket.readyRead.disconnect(self._read)
            except TypeError:
                pass
            self.socket.close()
            self.socket.deleteLater()
            self.socket = None

    def _read(self):
        sock = self.socket
        while sock is not None and sock.hasPendingDatagrams():
            dg = sock.receiveDatagram()
            try:
                msg = wsjtx_udp.parse(bytes(dg.data()))
            except wsjtx_udp.ProtocolError:
                self.bad_datagrams += 1
                continue
            if msg is not None:
                self.message.emit(msg)
