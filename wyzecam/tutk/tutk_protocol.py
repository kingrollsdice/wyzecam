import typing
from typing import Optional

import json
import pathlib
from ctypes import LittleEndianStructure, c_char, c_uint16, c_uint32

import xxtea

from . import tutk

project_root = pathlib.Path(__file__).parent


class TutkWyzeProtocolError(tutk.TutkError):
    pass


class TutkWyzeProtocolHeader(LittleEndianStructure):
    """
    Struct representing the first 16 bytes of messages sent back and forth between the camera
    and a client over a [TutkIOCtrlMux][wyzecam.tutk.tutk_ioctl_mux.TutkIOCtrlMux].

    :var prefix: the first two bytes of the header, always `HL`.
    :vartype prefix: str
    :var protocol: the protocol version being spoken by the client or camera. This varies quite a bit
                   depending on the firmware version of the camera.
    :vartype protocol: int
    :var code: The 2-byte "command" being issued, either by the camera, or the client.  By convention,
               it appears commands sent from a client to the camera are even numbered 'codes', whereas
               responses from the camera back to the client are always odd.
    :vartype code: int
    :var txt_len: the length of the payload of the message, i.e. the contents just after this header
    :vartype txt_len: int
    """

    _pack_ = 1
    _fields_ = [
        ("prefix", c_char * 2),  # 0:2
        ("protocol", c_uint16),  # 2:4
        ("code", c_uint16),  # 4:6
        ("txt_len", c_uint32),  # 6:10
        ("reserved2", c_uint16),  # 10:12
        ("reserved3", c_uint32),  # 12:16
    ]

    def __repr__(self):
        classname = self.__class__.__name__
        return (
            f"<{classname} "
            f"prefix={self.prefix} "
            f"protocol={self.protocol} "
            f"code={self.code} "
            f"txt_len={self.txt_len}>"
        )


class TutkWyzeProtocolMessage:
    """
    An abstract class representing a command sent from the client to
    the camera.  Subclasses implement particular codes.

    :var code: the 2 digit code representing this message
    :vartype code: int
    :var expected_response_code: the code of the message expected to
                                 be the 'response' to this one, from
                                 the camera.
    :vartype expected_response_code: int is always code + 1
    """

    def __init__(self, code: int) -> None:
        """Construct a new TutkWyzeProtocolMessage

        :param code: The 2-byte "command" being issued, either by the camera, or the client.  By convention,
                   it appears commands sent from a client to the camera are even numbered 'codes', whereas
                   responses from the camera back to the client are always odd.
        """
        self.code = code
        self.expected_response_code = code + 1

    def encode(self) -> bytes:
        """
        Translates this protocol message into a series of bytes,
        including the appropriate
        [16 byte header][wyzecam.tutk.tutk_protocol.TutkWyzeProtocolHeader].
        """
        return encode(self.code, 0, None)

    def parse_response(self, resp_data: bytes) -> typing.Any:
        """
        Called by [TutkIOCtrlMux][wyzecam.tutk.tutk_ioctl_mux.TutkIOCtrlMux] upon receipt
        of the corresponding
        [expected_response_code][wyzecam.tutk.tutk_protocol.TutkWyzeProtocolMessage]
        of this message.
        """
        return resp_data

    def __repr__(self):
        return f"<{self.__class__.__name__} code={self.code} resp_code={self.expected_response_code}>"


class K10000ConnectRequest(TutkWyzeProtocolMessage):
    """
    The "connect request" sent by a client to a camera when the client first connects.  This command
    initiates the handshake that authenticates the client to the camera.

    The expected response to this command is `10001`, in which the camera provides a set of 16 random
    bytes for the client to sign with the 'enr' of the camera.
    """

    def __init__(self):
        """Construct a new K10000ConnectRequest"""
        super().__init__(10000)

    def parse_response(self, resp_data):
        return resp_data


class K10002ConnectAuth(TutkWyzeProtocolMessage):
    """
    The "challenge response" sent by a client to a camera as part of the authentication handshake when
    the client first connects.  This command is deprecated, and is replaced by
    [K10008ConnectUserAuth][wyzecam.tutk.tutk_protocol.K10008ConnectUserAuth] on newer devices.  We
    need to continue supporting this for older firmwares, however.

    The expected response to this command is `10003`, in which the camera provides a json object
    with the result of the authentication exchange (and if successful, a bunch of device information).
    """

    def __init__(
        self,
        challenge_response: bytes,
        mac: str,
        open_video: bool = True,
        open_audio: bool = True,
    ) -> None:
        """
        Constructs a new K10002ConnectAuth message

        :param challenge_response: the xxtea-encrypted response to the challenge bytes
                                   recieved as part of message 10001.
        :param mac: the mac address of the camera
        :param open_video: True if we wish to start streaming video after authentication is successful.
        :param open_audio: True if we wish to start streaming audio after authentication is successful.
        """
        super().__init__(10002)

        assert (
            len(challenge_response) == 16
        ), "expected challenge response to be 16 bytes long"

        if len(mac) < 4:
            mac = mac + "1234"

        self.challenge_response = challenge_response
        self.username = mac
        self.open_video = open_video
        self.open_audio = open_audio

    def encode(self) -> bytes:
        data = bytearray([0] * 22)
        data[0:16] = self.challenge_response
        data[16:20] = self.username.encode("ascii")[0:4]
        data[20:21] = bytes([1] if self.open_video else [0])
        data[21:22] = bytes([1] if self.open_audio else [0])

        return encode(self.code, len(data), bytes(data))

    def parse_response(self, resp_data):
        return json.loads(resp_data)


class K10008ConnectUserAuth(TutkWyzeProtocolMessage):
    """
    The "challenge response" sent by a client to a camera as part of the authentication handshake when
    the client first connects.  This command is a newer version of
    [K10008ConnectUserAuth][wyzecam.tutk.tutk_protocol.K10002ConnectAuth], and it sends the 'open_user_id'
    as part of the authentication response.

    The expected response to this command is `10009`, in which the camera provides a json object
    with the result of the authentication exchange (and if successful, a bunch of device information).

    """

    def __init__(
        self,
        challenge_response: bytes,
        phone_id: str,
        open_userid: str,
        open_video: bool = True,
        open_audio: bool = True,
    ) -> None:
        """
        Constructs a new K10008ConnectAuth message

        :param challenge_response: the xxtea-encrypted response to the challenge bytes
                                   recieved as part of message 10001.
        :param phone_id: the phone id of the client
        :param open_userid: the open_user_id associated with the user authenticating.
        :param open_video: True if we wish to start streaming video after authentication is successful.
        :param open_audio: True if we wish to start streaming audio after authentication is successful.
        """
        super().__init__(10008)

        assert (
            len(challenge_response) == 16
        ), "expected challenge response to be 16 bytes long"

        if len(phone_id) < 4:
            phone_id = phone_id + "1234"

        self.challenge_response = challenge_response
        self.username = phone_id
        self.open_userid = open_userid
        self.open_video = open_video
        self.open_audio = open_audio

    def encode(self) -> bytes:
        data = bytearray()
        data.extend(self.challenge_response)

        data.extend(self.username.encode("utf-8")[:4])
        data.append(0x01 if self.open_video else 0x00)
        data.append(0x01 if self.open_audio else 0x00)

        open_userid_utf8 = self.open_userid.encode("utf-8")
        data.append(len(open_userid_utf8))
        data.extend(open_userid_utf8)

        return encode(self.code, len(data), bytes(data))

    def parse_response(self, resp_data):
        return json.loads(resp_data)


class K10010ControlChannel(TutkWyzeProtocolMessage):
    """
    A command used frequently by the mobile app to configure settings on the camera.

    Not terribly well understood.
    """

    def __init__(self, k: int = 1, v: int = 2):
        super().__init__(10010)
        assert k < 256, "control channel key must be < 256"
        assert v < 256, "control channel value must be < 256"
        self.k = k
        self.v = v

    def encode(self) -> bytes:
        return encode(10010, 2, bytes([self.k, self.v]))


class K10020CheckCameraInfo(TutkWyzeProtocolMessage):
    """
    A command used to read the current settings of the camera.

    Not terribly well understood.
    """

    def __init__(self):
        super().__init__(10020)

    def encode(self) -> bytes:
        arr = bytearray()
        arr.append(50)
        arr.extend(range(1, 51))
        return encode(self.code, len(arr), arr)

    def parse_response(self, resp_data):
        return json.loads(resp_data)


class K10056SetResolvingBit(TutkWyzeProtocolMessage):
    """
    A message used to set the resolution and bitrate of the camera.

    This is sent automatically after the authentication handshake completes successfully.
    """

    def __init__(
        self, frame_size=tutk.FRAME_SIZE_1080P, bitrate=tutk.BITRATE_HD
    ):
        """
        Construct a K10056SetResolvingBit message, with a given frame size and bitrate.

        Possible frame sizes are:

         - `tutk.FRAME_SIZE_1080P`: 1080P, or 1920 x 1080
         - `tutk.FRAME_SIZE_360P`: 360P, or 640 x 360

        Possible bit rates are:

         - `tutk.BITRATE_360P`: the bitrate chosen when selecting '360P' in the app; 30 KB/s
         - `tutk.BITRATE_SD`: the bitrate chosen when selecting 'SD' in the app; 60 KB/s
         - `tutk.BITRATE_HD`: the bitrate chosen when selecting 'HD' in the app; 120 KB/s
         - `tutk.BITRATE_SUPER_HD`: an even higher bitrate than ever asked for by the app; 150 KB/s
         - `tutk.BITRATE_SUPER_SUPER_HD`: an even higher bitrate than ever asked for by the app; 240 KB/s

        :param frame_size: the dimensions of the video to stream.
        :param bitrate: the bit rate, in KB/s to target in the h264/h265 encoder.
        """
        super().__init__(10056)
        self.frame_size = frame_size
        self.bitrate = bitrate

    def encode(self) -> bytes:
        payload = bytes([1 + self.frame_size, self.bitrate, 0])

        return encode(10056, 3, payload)

    def parse_response(self, resp_data):
        return resp_data == b"\x01"


class K10620CheckNight(TutkWyzeProtocolMessage):
    """
    A message used to check the night mode settings of the camera.

    Not terribly well understood.
    """

    def __init__(self):
        super().__init__(10620)


class K10640GetSpotlightStatus(TutkWyzeProtocolMessage):
    """
    A message used to check the spotlight settings of the camera.

    Not terribly well understood.
    """

    def __init__(self):
        super().__init__(10640)


def encode(code: int, data_len: int, data: Optional[bytes]) -> bytes:
    assert (data is None and data_len == 0) or (
        data is not None and data_len == len(data)
    )

    data_len = max(0, data_len)
    encoded_msg = bytearray([0] * (16 + data_len))
    encoded_msg[0:2] = [72, 76]
    encoded_msg[2:4] = int(1).to_bytes(2, byteorder="little", signed=False)
    encoded_msg[4:6] = int(code).to_bytes(2, byteorder="little", signed=False)
    encoded_msg[6:8] = int(data_len).to_bytes(
        2, byteorder="little", signed=False
    )
    if data is not None and data_len > 0:
        encoded_msg[16 : len(encoded_msg)] = data
    return bytes(encoded_msg)


def decode(buf):
    if len(buf) < 16:
        raise TutkWyzeProtocolError("IOCtrl message too short")

    header = TutkWyzeProtocolHeader.from_buffer_copy(buf)

    if header.prefix != b"HL":
        raise TutkWyzeProtocolError(
            "IOCtrl message begin with the prefix (Expected 'HL')"
        )

    if header.txt_len + 16 != len(buf):
        raise TutkWyzeProtocolError(
            f"Encoded length doesn't match message size "
            f"(header says {header.txt_len + 16}, "
            f"got message of len {len(buf)}"
        )

    data = None
    if header.txt_len > 0:
        data = buf[16 : header.txt_len + 16]
    return header, data


def respond_to_ioctrl_10001(
    data: bytes,
    protocol: int,
    enr: str,
    product_model: str,
    mac: str,
    phone_id: str,
    open_userid: str,
) -> Optional[TutkWyzeProtocolMessage]:
    camera_status = data[0]
    if camera_status == 2:
        print("Camera is updating, can't auth.")
        return None
    elif camera_status == 4:
        print("Camera is checking enr, can't auth.")
        return None
    elif camera_status not in [1, 3]:
        print(
            f"Unexpected mode for connect challenge response (10001): {camera_status}"
        )
        return None

    camera_enr_b = data[1:17]
    camera_secret_key = b"FFFFFFFFFFFFFFFF"
    if camera_status == 3:
        assert len(enr.encode("ascii")) >= 16, "Enr expected to be 16 bytes"
        camera_secret_key = enr.encode("ascii")[0:16]

    challenge_response = xxtea.decrypt(
        camera_enr_b, camera_secret_key, padding=False
    )

    if supports(product_model, protocol, 10008):
        response: TutkWyzeProtocolMessage = K10008ConnectUserAuth(
            challenge_response, phone_id, open_userid
        )
    else:
        response = K10002ConnectAuth(challenge_response, mac)
    print(f"Sending response:", response)
    return response


def supports(product_model, protocol, command):
    device_config = json.load(open(project_root / "device_config.json"))
    commands_db = device_config["supportedCommands"]
    supported_commands = []

    for k in commands_db["default"]:
        if int(k) <= int(protocol):
            supported_commands.extend(commands_db["default"][k])

    if product_model in commands_db:
        for k in commands_db[product_model]:
            if int(k) <= int(protocol):
                supported_commands.extend(commands_db[product_model][k])

    return str(command) in supported_commands
