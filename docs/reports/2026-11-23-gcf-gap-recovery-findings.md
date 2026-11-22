# GCF over UDP: gap recovery - what is known (2026-11-23)

Not built yet. Written down so the work is not lost. Found by black-box tests on this PC only
(packets sent and read, the earlier viewer's own logs and settings files read); the earlier
viewer's program was never opened, decompiled or changed, and no instrument was contacted for it.

## Packet layout (UDP, port 1567) - certain

From real instrument datagrams (`tests/data/fortimus_udp.bin`) and the earlier viewer's logs:

    GCF block (16-byte header + body; its size follows from the record count - NOT padded to 1024)
    + trailer, 65 bytes:
        byte 0       0x2d (45)  protocol version - the earlier viewer writes "v45"
        bytes 1-3    block number, big-endian, +1 per packet, one counter for all of a server's
                     streams (the earlier viewer logs the low byte, e.g. "$D0"; its Control pane
                     shows the full number, e.g. 7939702)
        byte 4       length n of the source name
        bytes 5..    source name, then 0x00, e.g. "A46D-ACC/Ainstrument/FMUS-A46D"
        rest         padding up to 65 bytes (leftover bytes)

The earlier viewer's own UDP replies end in a zero byte: `GCFACKN\0`, `GCFSEND:B\0`, `GCFPING\0`.

## Recovery - the sequence, not yet the bytes

Seen in the earlier viewer's Control pane while it recovered blocks from the user's instruments:

    Expected 7939702, got 7939703        a gap in the block numbers
    Connecting to server 10.8.0.3:1567   a TCP connection to the same address and port
    Connected to server 10.8.0.3:1567
    Request oldest v45                   asks for the oldest block the server still holds
    Received oldest v45 = 7929464
    Request block 7939702                asks for each missing block by number
    Received 7939702 "7461-VELN0"        the block, with its source name

The byte format of "Request oldest", "Request block N" and the replies is NOT known. It is in
the protocol document SWA-RFC-SCRM (not on this PC). The earlier viewer's TCP server on port 1567
resets the connection on a text command (`GCFPING\0`), so it is binary.

## How to finish it (when wanted)

The lab tools (session scratchpad, re-creatable): a local test server that sends packets in the
layout above (made-up system ZTEST, stream TSTZ0) with numbers left out on purpose, and logs
every UDP and TCP byte. The earlier viewer pulls from it after "Add UDP Server 127.0.0.1:1570";
once it accepts the blocks (they must not be padded; stream ids must stay below 2^31) it should
connect over TCP and send "Request oldest" - that capture gives the bytes. Note: the earlier
viewer binds the server's port number on all addresses, so a test server on 127.0.0.1 must
answer through another address of this PC.

Alternative for the two GCF instruments (IIT R 10.8.0.163, IIT Roorkee 3 10.8.0.121): they did not
answer SeedLink on 18000 on 2026-11-23. If SeedLink is switched on in their own web pages, adding
them with port 18000 gives full backfill at once (like IIT Roorkee 1, 10.8.0.119).
