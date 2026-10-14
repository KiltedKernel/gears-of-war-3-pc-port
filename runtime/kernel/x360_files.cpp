// Gears of War 3 kernel: file I/O (Nt*File). Sonic's port never needed these (it hooked XAPI's CreateFile), so they
// are written here from the Xbox 360 kernel semantics documented by Xenia (xboxkrnl_io.cc / xboxkrnl_io_info.cc).
// Guest paths map to host folders:
//   \Device\Cdrom0\..., game:\..., d:\...   -> the player's disc files (GetGamePath())
//   cache:\..., \Device\Harddisk0\Cache0\... -> %LOCALAPPDATA%\GoW3Recomp\cache
//   <content root>:\...                       -> XContent roots registered with XamContentCreateEx (saves)
#include <stdafx.h>
void LogCurrentStack(const std::string& why);   // crash.cpp
#include "function.h"
#include "memory.h"
#include "xdm.h"
#include "xam.h"
#include "mods.h"
#include <user/paths.h>
#include <os/logger.h>
#include <user/settings.h>

extern bool g_skipIntroArg;   // main.cpp: -nointro

#include <shlwapi.h>
#pragma comment(lib, "shlwapi.lib")
uint32_t SignalGuestEvent(uint32_t handle);   // kernel/imports.cpp

namespace x360k
{

constexpr uint32_t STATUS_SUCCESS_ = 0, STATUS_NO_SUCH_FILE_ = 0xC000000F, STATUS_END_OF_FILE_ = 0xC0000011,
    STATUS_OBJECT_NAME_NOT_FOUND_ = 0xC0000034, STATUS_OBJECT_PATH_NOT_FOUND_ = 0xC000003A,
    STATUS_OBJECT_NAME_COLLISION_ = 0xC0000035, STATUS_INVALID_PARAMETER_ = 0xC000000D,
    STATUS_NO_MORE_FILES_ = 0x80000006, STATUS_ACCESS_DENIED_ = 0xC0000022, STATUS_INVALID_HANDLE_ = 0xC0000008,
    STATUS_NOT_IMPLEMENTED_ = 0xC0000002, STATUS_INFO_LENGTH_MISMATCH_ = 0xC0000004;

struct NtFile final : KernelObject
{
    HANDLE handle = INVALID_HANDLE_VALUE;
    std::filesystem::path path;
    std::string guestPath;
    bool directory = false;
    bool shortMovie = false;   // -nointro: a logo movie whose Bink header says 1 frame (PatchMovieHeader)
    uint64_t position = 0;
    // directory enumeration
    std::vector<std::filesystem::directory_entry> entries;
    size_t nextEntry = 0;
    bool enumerated = false;

    ~NtFile() override { if (handle != INVALID_HANDLE_VALUE) CloseHandle(handle); }
    uint32_t Wait(uint32_t) override { return STATUS_SUCCESS_; }
};

static std::string Lower(std::string s) { for (auto& c : s) c = (char)tolower((unsigned char)c); return s; }

// guest path -> host path ("" if not mapped)
// skip the intro logos: the startup movie Open_Logos (Gears 3; Bink 2 "KB2a", frame counts at the same offsets) opens normally but
// their Bink headers read as 1 frame long, so each ends at once (a missing file crashes the movie player).
// LoadingMovie (the loading screen) and the story cinematics stay.
static bool SkippedMovie(const std::string& guest)
{
    if (!::g_skipIntroArg && !settings::Get().skipIntroMovies) return false;
    std::string l = guest;
    for (char& c : l) c = char(tolower((unsigned char)c));
    for (const char* m : { "\\movies\\open_logos.bik" })   // Gears of War 3: one Bink 2 movie with every logo
        if (l.size() >= strlen(m) && l.compare(l.size() - strlen(m), std::string::npos, m) == 0) return true;
    return false;
}

// Bink 1 header ("BIK?"): u32 frame count at +8 and again at +16 (little-endian)
static void PatchMovieHeader(const NtFile* f, uint64_t pos, uint8_t* data, uint64_t size)
{
    if (!f->shortMovie) return;
    for (uint64_t field : { 8ull, 16ull })
        if (field >= pos && field + 4 <= pos + size)
        {
            uint8_t* p = data + (field - pos);
            p[0] = 1; p[1] = p[2] = p[3] = 0;
        }
}

static std::filesystem::path ResolveGuestPath(std::string p)
{
    std::replace(p.begin(), p.end(), '/', '\\');
    if (p.starts_with("\\??\\")) p = p.substr(4);
    std::string l = Lower(p);
    auto under = [&](const std::filesystem::path& root, size_t cut) {
        std::string rest = p.substr(cut);
        while (!rest.empty() && rest[0] == '\\') rest.erase(0, 1);
        return rest.empty() ? root : root / std::filesystem::path(rest);
    };
    for (const char* pre : { "\\device\\cdrom0", "game:", "d:" })
        if (l.starts_with(pre))
        {
            // mods\ next to the executable: each enabled mod's files override the disc's at the same relative path
            // (highest priority wins), and the game's TOC is merged from the disc's and the mods' files
            // (kernel/mods.cpp, docs/MODDING.md). Files only in mods are not listed by directory enumeration; the
            // game finds packages through Xbox360TOC.txt.
            {
                const auto mod = mods::Resolve(p.substr(strlen(pre)), GetGamePath());
                if (!mod.empty())
                {
                    static std::mutex m;
                    static std::set<std::string> logged;
                    std::lock_guard lock(m);
                    if (logged.insert(l).second) LOGFN("files: mod override {}", mod.string());
                    return mod;
                }
            }
            return under(GetGamePath(), strlen(pre));
        }
    for (const char* pre : { "\\device\\harddisk0\\cache0", "\\device\\harddisk0\\cache1", "cache:" })
        if (l.starts_with(pre)) return under(GetUserPath() / "cache", strlen(pre));
    // the hard disk itself: titles open the raw partition to check that a drive is attached
    for (const char* pre : { "\\device\\harddisk0\\partition0", "\\device\\harddisk0\\partition1", "hdd:" })
        if (l.starts_with(pre)) return under(GetUserPath() / "hdd", strlen(pre));
    size_t colon = p.find(":\\");
    if (colon == std::string::npos && p.ends_with(':')) colon = p.size() - 1;
    if (colon != std::string::npos)
    {
        std::string_view root = XamGetRootPath(std::string_view(p).substr(0, colon));
        if (!root.empty())
            return under(std::filesystem::path(std::u8string_view((const char8_t*)root.data(), root.size())), colon + 1);
    }
    return {};
}

static std::string GuestName(XOBJECT_ATTRIBUTES* attrs)
{
    if (!attrs || !attrs->Name.get() || !attrs->Name->Buffer.get())
        return {};
    std::string name(attrs->Name->Buffer.get(), attrs->Name->Length);
    if (attrs->RootDirectory && attrs->RootDirectory != 0xFFFFFFFD && IsKernelObject(attrs->RootDirectory))
    {
        auto* dir = GetKernelObject<NtFile>(attrs->RootDirectory);
        name = dir->guestPath + (dir->guestPath.ends_with("\\") ? "" : "\\") + name;
    }
    return name;
}

static void FillTimes(uint8_t* out, const WIN32_FILE_ATTRIBUTE_DATA& d)
{
    auto put = [&](int off, FILETIME t) { *(be<uint64_t>*)(out + off) = (uint64_t(t.dwHighDateTime) << 32) | t.dwLowDateTime; };
    put(0, d.ftCreationTime); put(8, d.ftLastAccessTime); put(16, d.ftLastWriteTime); put(24, d.ftLastWriteTime);
}

static uint32_t XboxAttributes(DWORD a) { return (a & FILE_ATTRIBUTE_DIRECTORY) ? 0x10 : (a & FILE_ATTRIBUTE_READONLY ? 0x81 : 0x80); }

// FILE_NETWORK_OPEN_INFORMATION: 4 times, AllocationSize, EndOfFile, FileAttributes, pad (56 bytes)
static void FillNetworkOpenInfo(uint8_t* out, const std::filesystem::path& host)
{
    WIN32_FILE_ATTRIBUTE_DATA d{};
    GetFileAttributesExW(host.c_str(), GetFileExInfoStandard, &d);
    uint64_t size = (uint64_t(d.nFileSizeHigh) << 32) | d.nFileSizeLow;
    memset(out, 0, 56);
    FillTimes(out, d);
    *(be<uint64_t>*)(out + 32) = (size + 4095) & ~4095ull;
    *(be<uint64_t>*)(out + 40) = size;
    *(be<uint32_t>*)(out + 48) = XboxAttributes(d.dwFileAttributes);
}

static uint32_t g_openLogged = 0;

uint32_t NtCreateFile(be<uint32_t>* fileHandle, uint32_t desiredAccess, XOBJECT_ATTRIBUTES* attrs, XIO_STATUS_BLOCK* io,
    be<uint64_t>* allocationSize, uint32_t fileAttributes, uint32_t shareAccess, uint32_t disposition, uint32_t createOptions)
{
    const std::string guest = GuestName(attrs);
    const std::filesystem::path host = ResolveGuestPath(guest);
    *fileHandle = 0;

    if (host.empty())
    {
        LOGFN_WARNING("NtCreateFile: unmapped path {}", guest);
        if (io) { io->Status = STATUS_OBJECT_PATH_NOT_FOUND_; io->Information = 0; }
        return STATUS_OBJECT_PATH_NOT_FOUND_;
    }

    std::error_code ec;
    const bool exists = std::filesystem::exists(host, ec);
    const bool isDir = exists && std::filesystem::is_directory(host, ec);
    const bool wantDir = createOptions & 0x1;   // FILE_DIRECTORY_FILE
    uint32_t info = 1;                          // FILE_OPENED

    // FILE_SUPERSEDE 0, FILE_OPEN 1, FILE_CREATE 2, FILE_OPEN_IF 3, FILE_OVERWRITE 4, FILE_OVERWRITE_IF 5
    if (!exists && (disposition == 1 || disposition == 4))
    {
        if (g_openLogged++ < 200) LOGFN("NtCreateFile: not found {}", guest);
        if (io) { io->Status = STATUS_OBJECT_NAME_NOT_FOUND_; io->Information = 0; }
        return STATUS_OBJECT_NAME_NOT_FOUND_;
    }
    if (exists && disposition == 2)
    {
        if (io) { io->Status = STATUS_OBJECT_NAME_COLLISION_; io->Information = 0; }
        return STATUS_OBJECT_NAME_COLLISION_;
    }

    auto* file = CreateKernelObject<NtFile>();
    file->shortMovie = SkippedMovie(guest);
    if (file->shortMovie)
    {
        static bool logged = false;
        if (!logged) { logged = true; LOGFN("files: skipping the intro movies (-nointro / skip_intro_movies)"); }
    }
    file->path = host;
    file->guestPath = guest;
    if (isDir || (wantDir && !exists))
    {
        if (!exists) { std::filesystem::create_directories(host, ec); info = 2; }
        file->directory = true;
    }
    else
    {
        const bool write = (desiredAccess & (0x40000000 | 0x2 | 0x4 | 0x10000000)) != 0 || disposition != 1;
        DWORD create = OPEN_EXISTING;
        if (disposition == 0 || disposition == 5) { create = exists ? TRUNCATE_EXISTING : CREATE_NEW; info = exists ? 0 /*superseded*/ : 2; }
        else if (disposition == 2) { create = CREATE_NEW; info = 2; }
        else if (disposition == 3) { create = OPEN_ALWAYS; info = exists ? 1 : 2; }
        else if (disposition == 4) { create = TRUNCATE_EXISTING; info = 3; }
        if (!exists) std::filesystem::create_directories(host.parent_path(), ec);
        file->handle = CreateFileW(host.c_str(), GENERIC_READ | (write ? GENERIC_WRITE : 0), FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            nullptr, create, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file->handle == INVALID_HANDLE_VALUE && write)   // read-only disc files opened with write access
            file->handle = CreateFileW(host.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file->handle == INVALID_HANDLE_VALUE)
        {
            LOGFN_WARNING("NtCreateFile: host open failed ({}) for {}", GetLastError(), guest);
            DestroyKernelObject(file);
            if (io) { io->Status = STATUS_ACCESS_DENIED_; io->Information = 0; }
            return STATUS_ACCESS_DENIED_;
        }
    }
    if (g_openLogged++ < 200) LOGFN("NtCreateFile: {} -> {}", guest, host.string());
    static const char* stackFor = getenv("GOW3_FILE_STACK");   // (debugging) who opens files whose name has this
    if (stackFor && *stackFor)
    {
        std::string lower = guest; for (char& ch : lower) ch = char(tolower(ch));
        std::string want = stackFor; for (char& ch : want) ch = char(tolower(ch));
        if (lower.find(want) != std::string::npos) LogCurrentStack("file stack " + guest);
    }
    *fileHandle = GetKernelHandle(file);
    if (io) { io->Status = STATUS_SUCCESS_; io->Information = info; }
    return STATUS_SUCCESS_;
}

uint32_t NtOpenFile(be<uint32_t>* fileHandle, uint32_t desiredAccess, XOBJECT_ATTRIBUTES* attrs, XIO_STATUS_BLOCK* io,
    uint32_t shareAccess, uint32_t openOptions)
{
    return NtCreateFile(fileHandle, desiredAccess, attrs, io, nullptr, 0, shareAccess, 1, openOptions);
}

static NtFile* GetFile(uint32_t h)
{
    return (h && h != GUEST_INVALID_HANDLE_VALUE && IsKernelObject(h)) ? GetKernelObject<NtFile>(h) : nullptr;
}

static void Complete(uint32_t event, XIO_STATUS_BLOCK* io, uint32_t status, uint32_t info)
{
    if (io) { io->Status = status; io->Information = info; }
    if (event && IsKernelObject(event))
    {
        // the game's own Event object: signal it like the I/O completion would
        ::SignalGuestEvent(event);
    }
}

uint32_t NtReadFile(uint32_t fileHandle, uint32_t event, uint32_t apcRoutine, uint32_t apcContext, XIO_STATUS_BLOCK* io,
    uint8_t* buffer, uint32_t length, be<uint64_t>* byteOffset)
{
    NtFile* f = GetFile(fileHandle);
    if (!f || f->handle == INVALID_HANDLE_VALUE) return STATUS_INVALID_HANDLE_;
    uint64_t pos = byteOffset && uint64_t(*byteOffset) != 0xFFFFFFFFFFFFFFFEull ? uint64_t(*byteOffset) : f->position;
    OVERLAPPED ov{}; ov.Offset = DWORD(pos); ov.OffsetHigh = DWORD(pos >> 32);
    DWORD got = 0;
    BOOL ok = ReadFile(f->handle, buffer, length, &got, &ov);
    if (!ok && GetLastError() != ERROR_HANDLE_EOF)
        LOGFN_WARNING("NtReadFile: host read failed ({}) {}", GetLastError(), f->guestPath);
    PatchMovieHeader(f, pos, buffer, got);
    f->position = pos + got;
    uint32_t status = (got == 0 && length != 0) ? STATUS_END_OF_FILE_ : STATUS_SUCCESS_;
    Complete(event, io, status, got);
    if (apcRoutine)
        LOGFN_WARNING("NtReadFile: APC completion routine {:08X} not called (not implemented)", apcRoutine);
    return status;
}

// scatter read: the buffer is a list of 4 KB page pointers (FILE_SEGMENT_ELEMENT, 8 bytes each)
uint32_t NtReadFileScatter(uint32_t fileHandle, uint32_t event, uint32_t apcRoutine, uint32_t apcContext, XIO_STATUS_BLOCK* io,
    be<uint64_t>* segments, uint32_t length, be<uint64_t>* byteOffset)
{
    NtFile* f = GetFile(fileHandle);
    if (!f || f->handle == INVALID_HANDLE_VALUE) return STATUS_INVALID_HANDLE_;
    uint64_t pos = byteOffset ? uint64_t(*byteOffset) : f->position;
    uint32_t total = 0;
    for (uint32_t i = 0; total < length; i++)
    {
        uint32_t page = uint32_t(uint64_t(segments[i]));
        uint32_t chunk = std::min<uint32_t>(0x1000, length - total);
        OVERLAPPED ov{}; ov.Offset = DWORD(pos + total); ov.OffsetHigh = DWORD((pos + total) >> 32);
        DWORD got = 0;
        ReadFile(f->handle, g_memory.Translate(page), chunk, &got, &ov);
        PatchMovieHeader(f, pos + total, (uint8_t*)g_memory.Translate(page), got);
        total += got;
        if (got < chunk) break;
    }
    f->position = pos + total;
    uint32_t status = total == 0 && length ? STATUS_END_OF_FILE_ : STATUS_SUCCESS_;
    Complete(event, io, status, total);
    return status;
}

uint32_t NtWriteFile(uint32_t fileHandle, uint32_t event, uint32_t apcRoutine, uint32_t apcContext, XIO_STATUS_BLOCK* io,
    uint8_t* buffer, uint32_t length, be<uint64_t>* byteOffset)
{
    NtFile* f = GetFile(fileHandle);
    if (!f || f->handle == INVALID_HANDLE_VALUE) return STATUS_INVALID_HANDLE_;
    uint64_t pos = byteOffset && uint64_t(*byteOffset) != 0xFFFFFFFFFFFFFFFEull ? uint64_t(*byteOffset) : f->position;
    OVERLAPPED ov{}; ov.Offset = DWORD(pos); ov.OffsetHigh = DWORD(pos >> 32);
    DWORD put = 0;
    WriteFile(f->handle, buffer, length, &put, &ov);
    f->position = pos + put;
    Complete(event, io, STATUS_SUCCESS_, put);
    return STATUS_SUCCESS_;
}

uint32_t NtFlushBuffersFile(uint32_t fileHandle, XIO_STATUS_BLOCK* io)
{
    if (NtFile* f = GetFile(fileHandle); f && f->handle != INVALID_HANDLE_VALUE) FlushFileBuffers(f->handle);
    if (io) { io->Status = 0; io->Information = 0; }
    return 0;
}

static uint64_t HostSize(NtFile* f)
{
    if (f->handle != INVALID_HANDLE_VALUE) { LARGE_INTEGER s; GetFileSizeEx(f->handle, &s); return s.QuadPart; }
    return 0;
}

uint32_t NtQueryInformationFile(uint32_t fileHandle, XIO_STATUS_BLOCK* io, uint8_t* out, uint32_t length, uint32_t infoClass)
{
    NtFile* f = GetFile(fileHandle);
    if (!f) return STATUS_INVALID_HANDLE_;
    uint32_t used = 0;
    switch (infoClass)
    {
    case 5:   // FileStandardInformation: AllocationSize, EndOfFile, NumberOfLinks, DeletePending, Directory
    {
        uint64_t size = HostSize(f);
        memset(out, 0, 24);
        *(be<uint64_t>*)(out + 0) = (size + 4095) & ~4095ull;
        *(be<uint64_t>*)(out + 8) = size;
        *(be<uint32_t>*)(out + 16) = 1;
        out[21] = f->directory;
        used = 24;
        break;
    }
    case 6:   // FileInternalInformation: a stable index
        *(be<uint64_t>*)out = std::hash<std::string>{}(f->path.string());
        used = 8; break;
    case 14:  // FilePositionInformation
        *(be<uint64_t>*)out = f->position; used = 8; break;
    case 20:  // FileEndOfFileInformation
        *(be<uint64_t>*)out = HostSize(f); used = 8; break;
    case 34:  // FileNetworkOpenInformation
        FillNetworkOpenInfo(out, f->path); used = 56; break;
    case 35:  // FileAttributeTagInformation
        *(be<uint32_t>*)out = f->directory ? 0x10 : 0x80; *(be<uint32_t>*)(out + 4) = 0; used = 8; break;
    default:
        LOGFN_WARNING("NtQueryInformationFile: class {} not implemented ({})", infoClass, f->guestPath);
        if (io) { io->Status = STATUS_NOT_IMPLEMENTED_; io->Information = 0; }
        return STATUS_NOT_IMPLEMENTED_;
    }
    if (io) { io->Status = 0; io->Information = used; }
    return 0;
}

uint32_t NtSetInformationFile(uint32_t fileHandle, XIO_STATUS_BLOCK* io, uint8_t* in, uint32_t length, uint32_t infoClass)
{
    NtFile* f = GetFile(fileHandle);
    if (!f) return STATUS_INVALID_HANDLE_;
    switch (infoClass)
    {
    case 14: f->position = *(be<uint64_t>*)in; break;   // FilePositionInformation
    case 19:                                            // FileAllocationInformation
    case 20:                                            // FileEndOfFileInformation
        if (f->handle != INVALID_HANDLE_VALUE)
        {
            FILE_END_OF_FILE_INFO e; e.EndOfFile.QuadPart = int64_t(uint64_t(*(be<uint64_t>*)in));
            if (infoClass == 20) SetFileInformationByHandle(f->handle, FileEndOfFileInfo, &e, sizeof(e));
        }
        break;
    case 13:  // FileDispositionInformation: delete on close
        if (in[0]) { if (f->handle != INVALID_HANDLE_VALUE) { CloseHandle(f->handle); f->handle = INVALID_HANDLE_VALUE; } std::error_code ec; std::filesystem::remove(f->path, ec); }
        break;
    default:
        LOGFN_WARNING("NtSetInformationFile: class {} not implemented ({})", infoClass, f->guestPath);
        break;
    }
    if (io) { io->Status = 0; io->Information = 0; }
    return 0;
}

uint32_t NtQueryFullAttributesFile(XOBJECT_ATTRIBUTES* attrs, uint8_t* out)
{
    const std::string guest = GuestName(attrs);
    const std::filesystem::path host = ResolveGuestPath(guest);
    std::error_code ec;
    if (host.empty() || !std::filesystem::exists(host, ec))
        return STATUS_OBJECT_NAME_NOT_FOUND_;
    FillNetworkOpenInfo(out, host);
    return 0;
}

// FILE_DIRECTORY_INFORMATION, one entry per call (like Xenia): NextEntryOffset, FileIndex, 4 times, EndOfFile,
// AllocationSize, FileAttributes, FileNameLength, FileName (ANSI on the 360)
uint32_t NtQueryDirectoryFile(uint32_t fileHandle, uint32_t event, uint32_t apcRoutine, uint32_t apcContext, XIO_STATUS_BLOCK* io,
    uint8_t* out, uint32_t length, XANSI_STRING* mask, uint32_t restartScan)
{
    NtFile* f = GetFile(fileHandle);
    if (!f || !f->directory) return STATUS_INVALID_HANDLE_;
    if (!f->enumerated || restartScan)
    {
        f->entries.clear(); f->nextEntry = 0; f->enumerated = true;
        std::string pattern = (mask && mask->Buffer.get()) ? Lower(std::string(mask->Buffer.get(), mask->Length)) : "*";
        std::error_code ec;
        for (auto& e : std::filesystem::directory_iterator(f->path, ec))
        {
            std::string n = Lower(e.path().filename().string());
            bool match = pattern == "*" || pattern == "*.*" || PathMatchSpecA(n.c_str(), pattern.c_str());
            if (match) f->entries.push_back(e);
        }
    }
    if (f->nextEntry >= f->entries.size())
    {
        if (io) { io->Status = STATUS_NO_MORE_FILES_; io->Information = 0; }
        return STATUS_NO_MORE_FILES_;
    }
    auto& e = f->entries[f->nextEntry];
    std::string name = e.path().filename().string();
    const uint32_t need = 0x40 + uint32_t(name.size());
    if (length < need) return STATUS_INFO_LENGTH_MISMATCH_;
    f->nextEntry++;
    WIN32_FILE_ATTRIBUTE_DATA d{};
    GetFileAttributesExW(e.path().c_str(), GetFileExInfoStandard, &d);
    uint64_t size = (uint64_t(d.nFileSizeHigh) << 32) | d.nFileSizeLow;
    memset(out, 0, need);
    *(be<uint32_t>*)(out + 0) = 0;                       // last entry of this call
    *(be<uint32_t>*)(out + 4) = uint32_t(f->nextEntry);
    FillTimes(out + 8, d);
    *(be<uint64_t>*)(out + 0x28) = size;
    *(be<uint64_t>*)(out + 0x30) = (size + 4095) & ~4095ull;
    *(be<uint32_t>*)(out + 0x38) = XboxAttributes(d.dwFileAttributes);
    *(be<uint32_t>*)(out + 0x3C) = uint32_t(name.size());
    memcpy(out + 0x40, name.data(), name.size());
    if (io) { io->Status = 0; io->Information = need; }
    return 0;
}

uint32_t NtQueryVolumeInformationFile(uint32_t fileHandle, XIO_STATUS_BLOCK* io, uint8_t* out, uint32_t length, uint32_t infoClass)
{
    uint32_t used = 0;
    switch (infoClass)
    {
    case 1:   // FileFsVolumeInformation: creation time, serial, label length, supports objects, label
        memset(out, 0, 24); *(be<uint32_t>*)(out + 8) = 0x12345678; used = 24; break;
    case 3:   // FileFsSizeInformation: total units, free units, sectors per unit, bytes per sector
        *(be<uint64_t>*)(out + 0) = 0x200000; *(be<uint64_t>*)(out + 8) = 0x100000;
        *(be<uint32_t>*)(out + 16) = 8; *(be<uint32_t>*)(out + 20) = 512; used = 24; break;
    case 5:   // FileFsAttributeInformation: attributes, max component length, name length, name
    {
        const char* fs = "FATX";
        *(be<uint32_t>*)(out + 0) = 0x2; *(be<uint32_t>*)(out + 4) = 42; *(be<uint32_t>*)(out + 8) = 4;
        memcpy(out + 12, fs, 4); used = 16; break;
    }
    default:
        LOGFN_WARNING("NtQueryVolumeInformationFile: class {} not implemented", infoClass);
        if (io) { io->Status = STATUS_NOT_IMPLEMENTED_; io->Information = 0; }
        return STATUS_NOT_IMPLEMENTED_;
    }
    if (io) { io->Status = 0; io->Information = used; }
    return 0;
}

uint32_t RtlNtStatusToDosError(uint32_t status)
{
    switch (status)
    {
    case 0: return 0;
    case STATUS_OBJECT_NAME_NOT_FOUND_: case STATUS_NO_SUCH_FILE_: return 2;   // ERROR_FILE_NOT_FOUND
    case STATUS_OBJECT_PATH_NOT_FOUND_: return 3;                              // ERROR_PATH_NOT_FOUND
    case STATUS_ACCESS_DENIED_: return 5;
    case STATUS_INVALID_HANDLE_: return 6;
    case STATUS_END_OF_FILE_: return 38;                                        // ERROR_HANDLE_EOF
    case STATUS_OBJECT_NAME_COLLISION_: return 183;                             // ERROR_ALREADY_EXISTS
    case STATUS_NO_MORE_FILES_: return 18;
    case STATUS_INVALID_PARAMETER_: return 87;
    case 0x00000102: return 1460;                                               // STATUS_TIMEOUT -> ERROR_TIMEOUT
    case 0x80000005: return 234;                                                // STATUS_BUFFER_OVERFLOW -> ERROR_MORE_DATA
    case STATUS_INFO_LENGTH_MISMATCH_: return 24;
    default:
        if ((status & 0xC0000000) == 0xC0000000) return 317;                    // ERROR_MR_MID_NOT_FOUND
        return status & 0xFFFF;
    }
}

}

GUEST_FUNCTION_HOOK(__imp__NtCreateFile, x360k::NtCreateFile);
GUEST_FUNCTION_HOOK(__imp__NtOpenFile, x360k::NtOpenFile);
GUEST_FUNCTION_HOOK(__imp__NtReadFile, x360k::NtReadFile);
GUEST_FUNCTION_HOOK(__imp__NtReadFileScatter, x360k::NtReadFileScatter);
GUEST_FUNCTION_HOOK(__imp__NtWriteFile, x360k::NtWriteFile);
GUEST_FUNCTION_HOOK(__imp__NtFlushBuffersFile, x360k::NtFlushBuffersFile);
GUEST_FUNCTION_HOOK(__imp__NtQueryInformationFile, x360k::NtQueryInformationFile);
GUEST_FUNCTION_HOOK(__imp__NtSetInformationFile, x360k::NtSetInformationFile);
GUEST_FUNCTION_HOOK(__imp__NtQueryFullAttributesFile, x360k::NtQueryFullAttributesFile);
GUEST_FUNCTION_HOOK(__imp__NtQueryDirectoryFile, x360k::NtQueryDirectoryFile);
GUEST_FUNCTION_HOOK(__imp__NtQueryVolumeInformationFile, x360k::NtQueryVolumeInformationFile);
GUEST_FUNCTION_HOOK(__imp__RtlNtStatusToDosError, x360k::RtlNtStatusToDosError);
