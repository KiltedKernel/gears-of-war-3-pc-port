// Dump the decrypted, decompressed memory image of an XEX (for analysis only; stays in D:\gow3-decomp).
#include <cstdio>
#include <vector>
#include <cstring>
#include <algorithm>
#include <image.h>
#include <file.h>
int main(int argc, char** argv)
{
    if (argc < 3) { printf("usage: xexdump in.xex out.bin\n"); return 1; }
    auto file = LoadFile(argv[1]);
    if (file.empty()) { printf("cannot read %s\n", argv[1]); return 1; }
    Image image = Image::ParseImage(file.data(), file.size());
    // flat image from base: every section copied to (section.base - image.base)
    size_t end = 0;
    const size_t imageSize = argc > 3 ? strtoul(argv[3], nullptr, 16) : 0x1230000; // XEX security info image size
    for (auto& s : image.sections) end = std::max(end, s.base + s.size - image.base);
    end = std::min(end, imageSize);
    std::vector<uint8_t> flat(end);
    for (auto& s : image.sections)
        if (s.data && s.base - image.base < end)
            memcpy(flat.data() + (s.base - image.base), s.data, std::min<size_t>(s.size, end - (s.base - image.base)));
    FILE* f = fopen(argv[2], "wb");
    fwrite(flat.data(), 1, flat.size(), f);
    fclose(f);
    printf("base %zx size %x entry %zx\n", image.base, image.size, (size_t)image.entry_point);
    for (auto& s : image.sections)
        printf("section %-8s %08zx %08x flags %x\n", s.name.c_str(), (size_t)s.base, s.size, s.flags);
    return 0;
}
