"""Gears of War 1 sound data (XMA1) -> Gears 3's (XMA2), offline.

Gears 1 SoundNodeWave native data (after the tagged properties): an FName, then one bulk block (flags, count, size,
offset) holding:  u32 EncodedBufferFormatSize (32), u32 SeekTableSize, u32 EncodedBufferSize (big-endian), an
XMAWAVEFORMAT (little-endian: FormatTag 0x0165, BitsPerSample, EncodeOptions, LargestSkip, NumStreams, LoopCount,
Version, then per stream PsuedoBytesPerSec, SampleRate, LoopStart, LoopEnd (bits), SubframeData, Channels,
ChannelMask), the seek table, the XMA packets.
Gears 3 SoundNodeWave native data: four bulk blocks (RawData, CompressedPCData, CompressedXbox360Data,
CompressedPS3Data); the Xbox one: u32 EncodedBufferFormatSize (52), u32 SeekTableSize, u32 EncodedBufferSize, an
XMA2WAVEFORMATEX (big-endian: wFormatTag 0x0166, nChannels, nSamplesPerSec, nAvgBytesPerSec, nBlockAlign,
wBitsPerSample, cbSize 34, NumStreams, ChannelMask, SamplesEncoded, BytesPerBlock, PlayBegin, PlayLength, LoopBegin,
LoopLength, LoopCount, EncoderVersion, BlockCount), the seek table, the packets.
Packets (2048 bytes) carry the same compressed frames in both; the 32-bit packet header is
  6 bits (XMA2: frames starting in this packet; XMA1: a sequence number), 15 bits offset of the first frame start
  (in bits, after the header), 3 bits metadata, 8 bits packets to skip.
A frame starts with its length in bits (15 bits, the length field included) and continues across packets after
their headers; 0x7FFF ends the stream. Each frame decodes to 512 samples per channel."""
import struct

PACKET = 2048
PAYLOAD_BITS = (PACKET - 4) * 8


def read_g1_wave(tail):
    """Gears 1 SoundNodeWave native data -> dict (format fields, packets)"""
    o = 8                                               # (an FName)
    flags, count, size, off = struct.unpack_from('>4i', tail, o); o += 16
    data = tail[o:o + size]
    fmt_size, seek_size, buf_size = struct.unpack_from('>3I', data, 0)
    f = data[12:12 + fmt_size]
    (tag, bits, options, largest_skip, streams, loop_count, version) = struct.unpack_from('<5H2B', f, 0)
    pbps, rate, loop_start, loop_end, subframe, channels, mask = struct.unpack_from('<4I2BH', f, 12)
    packets = data[12 + fmt_size + seek_size:12 + fmt_size + seek_size + buf_size]
    return {'tag': tag, 'streams': streams, 'rate': rate, 'channels': channels, 'loop_start': loop_start,
            'loop_end': loop_end, 'loop_count': loop_count, 'packets': packets, 'rest': tail[o + size:]}


def frame_counts(packets):
    """(frames starting in each packet, total frames): walks the frame chain from packet 0's first frame"""
    n = len(packets) // PACKET
    if n == 0: return [], 0
    def bits(pos, width):                               # pos: bit position in the payload stream (headers skipped)
        v = 0
        for k in range(width):
            p = pos + k
            pk, b = divmod(p, PAYLOAD_BITS)
            if pk >= n: return None
            byte = packets[pk * PACKET + 4 + b // 8]
            v = (v << 1) | ((byte >> (7 - b % 8)) & 1)
        return v
    offset = lambda k: (struct.unpack_from('>I', packets, k * PACKET)[0] >> 11) & 0x7FFF
    pos = offset(0)                                     # first frame, in bits after packet 0's header
    counts = [0] * n
    total = 0
    starts = {}                                         # packet -> first frame start found by the walk
    while True:
        pk = pos // PAYLOAD_BITS
        if pk >= n: break
        length = bits(pos, 15)
        if length is None: break
        if length == 0x7FFF or length < 15:             # padding to the end of the packet: the next packet's
            pk += 1                                     # first frame (its header's offset) continues
            if pk >= n or offset(pk) == 0x7FFF: break
            pos = pk * PAYLOAD_BITS + offset(pk)
            continue
        starts.setdefault(pk, pos - pk * PAYLOAD_BITS)
        counts[pk] += 1; total += 1
        pos += length
    frame_counts.starts = starts
    return counts, total


def to_g3_xbox_data(w1):
    """XMA2 'CompressedXbox360Data' bytes for a Gears 1 wave (read_g1_wave), and (channels, rate, samples)"""
    if w1['tag'] != 0x0165 or w1['streams'] != 1:
        raise ValueError(f"not a single-stream XMA1 wave (format {w1['tag']:#x}, {w1['streams']} streams)")
    pk = bytearray(w1['packets'])
    counts, total = frame_counts(bytes(pk))
    for k, c in enumerate(counts):                      # XMA2: frames starting in the packet in the top 6 bits
        h = struct.unpack_from('>I', pk, k * PACKET)[0]
        struct.pack_into('>I', pk, k * PACKET, (h & 0x03FFFFFF) | (min(c, 63) << 26))
    ch, rate = w1['channels'], w1['rate']
    samples = total * 512
    blocks = len(pk) // PACKET
    fmt = struct.pack('>HHIIHHH', 0x0166, ch, rate, rate * ch * 2, 2 * ch, 16, 34)
    fmt += struct.pack('>HIIIIIIIBBH', 1, 0, samples, PACKET, 0, samples, 0, samples, 0, 4, blocks)
    assert len(fmt) == 52
    return struct.pack('>3I', len(fmt), 0, len(pk)) + fmt + bytes(pk), (ch, rate, samples)
