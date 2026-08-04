# Transport design

MTGNP runs over a TCP byte stream, so one `recv()` call is not assumed to equal
one PDU. `receive_exact()` loops until the requested number of bytes has been
collected or the peer closes the connection. `receive_frame()` first obtains
the complete four-byte, big-endian length prefix and then reads exactly that
many payload bytes.

Frames declaring more than 65,535 payload bytes are rejected before their body
is read. JSON parsing begins only after the complete payload is available.
Back-to-back frames remain separate because a receiver never reads beyond the
declared payload length.

`FramedConnection` combines framing with the shared PDU encoder/validator. It
knows whether its local process is a client or server, so outbound and inbound
message directions are checked automatically. Independent send and receive
locks allow full-duplex use while preventing two producer threads from
interleaving frame bytes.

`PDUTracer` is shared by the connection layer. It can be enabled at startup or
toggled at runtime, and it prints the complete PDU with timestamp, action,
wire direction, peer label, message type, and sequence number. A lock keeps
logs readable when multiple connection threads share one tracer.

