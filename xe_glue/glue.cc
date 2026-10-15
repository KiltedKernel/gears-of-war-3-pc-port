// Port: glue between the recompiled game's runtime and Xenia's D3D12 GPU (Xenia is BSD-3: THIRD_PARTY_NOTICES.md).
// Built inside Xenia's CMake build (MSVC), with XE_PORT=1 (patched Xenia sources: tools/xenia-gow3.patch).
//
// - xe::Memory: the runtime's guest address space has Xenia's layout (physical memory viewed at 0xA0000000,
//   0xC0000000 and 0xE0000000, the E0 view shifted by 4 KB), so the GPU's Memory is a zeroed object whose two base
//   pointers point into it; only the member functions the GPU calls are defined here.
// - CPU-write watches: Xenia's caches arm read-only protection on physical pages they mirror on the GPU; the
//   runtime's exception handler reports faults to HandleAccessViolation, which invalidates and unprotects.
// - A UI thread owns the window and its message loop; the graphics system presents into it.
#include "port_gpu.h"

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "third_party/stb/stb_image_write.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <deque>
#include <filesystem>
#include <future>
#include <set>
#include <mutex>
#include <thread>
#include <vector>

#include "xenia/base/cvar.h"
#include "xenia/base/logging.h"
#include "xenia/base/memory.h"
#include "xenia/base/mutex.h"
#include "xenia/base/platform_win.h"
#include "xenia/apu/audio_system.h"
#include "xenia/apu/xaudio2/xaudio2_audio_system.h"
#include "xenia/apu/xma_decoder.h"
#include "xenia/gpu/command_processor.h"
#include "xenia/gpu/d3d12/d3d12_command_processor.h"
#include "xenia/gpu/d3d12/d3d12_graphics_system.h"
#include "xenia/memory.h"
#include "xenia/ui/graphics_provider.h"
#include "xenia/ui/immediate_drawer.h"
#include "xenia/ui/presenter.h"
#include "xenia/ui/ui_drawer.h"
#include "xenia/ui/window.h"
#include "xenia/ui/window_win.h"
#include "xenia/ui/windowed_app_context_win.h"

namespace {

portgpu::Host g_host;
alignas(64) uint8_t g_memory_storage[sizeof(xe::Memory)];   // zeroed static: never constructed (see above)
xe::Memory* g_memory = nullptr;

// ---- physical page watches (4 KB pages over 512 MB) ----
constexpr uint32_t kPageShift = 12, kPageSize = 1u << kPageShift, kPhysicalSize = 0x20000000;
constexpr uint32_t kPageCount = kPhysicalSize >> kPageShift;
std::vector<uint64_t> g_watched(kPageCount / 64);
// watches are guarded by Xenia's global critical region (the GPU holds it while arming watches; its invalidation
// callbacks take it again, recursively)
std::vector<std::pair<xe::Memory::PhysicalMemoryInvalidationCallback, void*>*> g_invalidation_callbacks;

// every host view of a physical page: A0 full; C0 all but the last 64 KB; E0 shifted down by 4 KB (the last page is
// not visible through it)
void ForEachView(uint32_t physical, uint32_t length, const std::function<void(uint8_t*, size_t)>& fn) {
  uint8_t* base = g_host.guestBase;
  fn(base + 0xA0000000u + physical, length);
  {
    uint32_t end = std::min<uint32_t>(physical + length, kPhysicalSize - 0x10000);
    if (end > physical) fn(base + 0xC0000000u + physical, end - physical);
  }
  {
    // virtual E0000000 + x = physical x + 0x1000
    if (physical + length > 0x1000) {
      uint32_t start = std::max<uint32_t>(physical, 0x1000);
      uint32_t end = std::min<uint32_t>(physical + length, kPhysicalSize);
      if (end > start) fn(base + 0xE0000000u + (start - 0x1000), end - start);
    }
  }
}

void Protect(uint32_t page_first, uint32_t page_count, bool read_only) {
  ForEachView(page_first << kPageShift, page_count << kPageShift, [&](uint8_t* p, size_t n) {
    xe::memory::Protect(p, n, read_only ? xe::memory::PageAccess::kReadOnly : xe::memory::PageAccess::kReadWrite);
  });
}

bool IsWatched(uint32_t page) { return (g_watched[page >> 6] >> (page & 63)) & 1; }
void SetWatched(uint32_t page, bool on) {
  if (on) g_watched[page >> 6] |= uint64_t(1) << (page & 63);
  else g_watched[page >> 6] &= ~(uint64_t(1) << (page & 63));
}

// unwatch + unprotect watched pages in [first, last]
void Unwatch(uint32_t first, uint32_t last) {
  uint32_t run = UINT32_MAX;
  for (uint32_t i = first; i <= last + 1; i++) {
    bool w = i <= last && IsWatched(i);
    if (w) {
      SetWatched(i, false);
      if (run == UINT32_MAX) run = i;
    } else if (run != UINT32_MAX) {
      Protect(run, i - run, false);
      run = UINT32_MAX;
    }
  }
}

// Xenia's PhysicalHeap::TriggerCallbacks for a write to [physical, physical+length)
bool TriggerWrite(uint32_t physical, uint32_t length, bool unwatch_exact_range) {
  uint32_t first = physical >> kPageShift, last = (physical + std::max<uint32_t>(length, 1) - 1) >> kPageShift;
  last = std::min(last, kPageCount - 1);
  bool any = false;
  for (uint32_t i = first; i <= last && !any; i++) any = IsWatched(i);
  if (!any) return false;
  const uint32_t start = first << kPageShift, len = (last - first + 1) << kPageShift;
  uint32_t unwatch_first = 0, unwatch_last = UINT32_MAX;
  for (auto* cb : g_invalidation_callbacks) {
    auto range = cb->first(cb->second, start, len, unwatch_exact_range);
    if (!unwatch_exact_range) {
      unwatch_first = std::max(unwatch_first, range.first);
      unwatch_last = std::min(unwatch_last, xe::sat_add(range.first, std::max(range.second, uint32_t(1)) - 1));
    }
  }
  uint32_t page_first = first, page_last = last;
  if (!unwatch_exact_range) {
    constexpr uint32_t kMaxUnwatchExcess = 4 * 1024 * 1024;
    unwatch_first = std::min(unwatch_first, start);
    unwatch_last = std::max(unwatch_last, start + len - 1);
    unwatch_first = std::max(unwatch_first, start & ~(kMaxUnwatchExcess - 1));
    unwatch_last = std::min(unwatch_last, (start + len - 1) | (kMaxUnwatchExcess - 1));
    page_first = unwatch_first >> kPageShift;
    page_last = std::min(unwatch_last >> kPageShift, kPageCount - 1);
  }
  Unwatch(page_first, page_last);
  return true;
}

// the graphics system with register access for the runtime's MMIO forwarding
class PortGraphicsSystem : public xe::gpu::d3d12::D3D12GraphicsSystem {
 public:
  using GraphicsSystem::ReadRegister;
  using GraphicsSystem::WriteRegister;
};

std::unique_ptr<PortGraphicsSystem> g_graphics;
std::thread g_ui_thread;
xe::ui::Win32WindowedAppContext* g_app_context = nullptr;
std::unique_ptr<xe::ui::Window> g_window;

// closing the window ends the game, like switching the console off (game threads are not joined; a hard exit
// avoids hanging on them, and the game shows its own "don't turn off" icon while it saves)
class CloseListener : public xe::ui::WindowListener {
 public:
  void OnClosing(xe::ui::UIEvent& e) override {
    XELOGI("port: window closed, exiting");
    if (g_host.onExit) g_host.onExit();   // multiplayer: say goodbye, give the router's port back
    xe::FlushLog();
    TerminateProcess(GetCurrentProcess(), 0);
  }
};
CloseListener g_close_listener;
std::atomic<bool> g_running{false};

// ---- notices: short messages over the game (multiplayer: joined, cannot reach...) ----
// Each is drawn once with GDI into a bitmap (a dark panel with a green bar, a bold title and wrapped text, sized for
// the window), then shown one after another at the bottom centre by a UI drawer of the presenter, fading in and out.
struct PendingNotice {
  std::vector<uint8_t> rgba;
  uint32_t width = 0, height = 0;
  int seconds = 6;
  std::unique_ptr<xe::ui::ImmediateTexture> texture;
  uint64_t shown_ms = 0;
};
std::mutex g_notice_mutex;
std::deque<PendingNotice> g_notices;
std::unique_ptr<xe::ui::ImmediateDrawer> g_immediate_drawer;

PendingNotice RenderNotice(const std::string& title, const std::string& text, int seconds) {
  PendingNotice n;
  n.seconds = std::clamp(seconds, 2, 30);
  RECT client{0, 0, 1280, 720};
  if (HWND w = (HWND)portgpu::GetWindowHandle()) GetClientRect(w, &client);
  const float scale = std::max(0.6f, float(client.bottom - client.top) / 1080.0f);
  const int pad = int(20 * scale), bar = int(6 * scale), max_text = int(620 * scale);
  HDC dc = CreateCompatibleDC(nullptr);
  auto font = [&](int px, int weight) {
    return CreateFontW(-px, 0, 0, 0, weight, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                       ANTIALIASED_QUALITY, DEFAULT_PITCH, L"Segoe UI");
  };
  HFONT title_font = font(int(30 * scale), FW_SEMIBOLD), text_font = font(int(23 * scale), FW_NORMAL);
  auto wide = [](const std::string& s) {
    std::wstring w(s.size(), L'\0');
    w.resize(size_t(MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), w.data(), int(w.size()))));
    return w;
  };
  const std::wstring wt = wide(title), wx = wide(text);
  RECT tr{0, 0, max_text, 0}, xr{0, 0, max_text, 0};
  SelectObject(dc, title_font);
  DrawTextW(dc, wt.c_str(), int(wt.size()), &tr, DT_CALCRECT | DT_SINGLELINE | DT_NOPREFIX);
  SelectObject(dc, text_font);
  if (!wx.empty()) DrawTextW(dc, wx.c_str(), int(wx.size()), &xr, DT_CALCRECT | DT_WORDBREAK | DT_NOPREFIX);
  const int inner_w = std::min<int>(max_text, std::max<int>(tr.right, xr.right));
  const int gap = wx.empty() ? 0 : int(6 * scale);
  n.width = uint32_t(bar + pad * 2 + inner_w);
  n.height = uint32_t(pad * 2 + tr.bottom + gap + xr.bottom);
  BITMAPINFO bi{{sizeof(BITMAPINFOHEADER), LONG(n.width), -LONG(n.height), 1, 32, BI_RGB}};
  void* bits = nullptr;
  HBITMAP bmp = CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
  HGDIOBJ old = SelectObject(dc, bmp);
  RECT all{0, 0, LONG(n.width), LONG(n.height)}, accent{0, 0, bar, LONG(n.height)};
  HBRUSH panel = CreateSolidBrush(RGB(18, 20, 18)), green = CreateSolidBrush(RGB(110, 190, 60));
  FillRect(dc, &all, panel);
  FillRect(dc, &accent, green);
  SetBkMode(dc, TRANSPARENT);
  SelectObject(dc, title_font);
  SetTextColor(dc, RGB(150, 220, 90));
  RECT t{bar + pad, pad, bar + pad + inner_w, pad + tr.bottom};
  DrawTextW(dc, wt.c_str(), int(wt.size()), &t, DT_SINGLELINE | DT_NOPREFIX | DT_END_ELLIPSIS);
  SelectObject(dc, text_font);
  SetTextColor(dc, RGB(235, 235, 235));
  RECT x{bar + pad, pad + tr.bottom + gap, bar + pad + inner_w, LONG(n.height) - pad};
  if (!wx.empty()) DrawTextW(dc, wx.c_str(), int(wx.size()), &x, DT_WORDBREAK | DT_NOPREFIX);
  GdiFlush();
  n.rgba.resize(size_t(n.width) * n.height * 4);
  const uint8_t* src = (const uint8_t*)bits;   // BGRX
  for (size_t i = 0; i < size_t(n.width) * n.height; i++) {
    n.rgba[i * 4 + 0] = src[i * 4 + 2];
    n.rgba[i * 4 + 1] = src[i * 4 + 1];
    n.rgba[i * 4 + 2] = src[i * 4 + 0];
    n.rgba[i * 4 + 3] = 248;
  }
  SelectObject(dc, old);
  DeleteObject(bmp);
  DeleteObject(panel);
  DeleteObject(green);
  DeleteObject(title_font);
  DeleteObject(text_font);
  DeleteDC(dc);
  return n;
}

class NoticeDrawer : public xe::ui::UIDrawer {
 public:
  void Draw(xe::ui::UIDrawContext& context) override {
    if (!g_immediate_drawer) return;
    std::lock_guard<std::mutex> lock(g_notice_mutex);
    if (g_notices.empty()) return;
    PendingNotice& n = g_notices.front();
    const uint64_t now = GetTickCount64();
    if (!n.texture) {
      n.texture = g_immediate_drawer->CreateTexture(n.width, n.height, xe::ui::ImmediateTextureFilter::kLinear, false,
                                                    n.rgba.data());
      n.shown_ms = now;
      if (!n.texture) { g_notices.pop_front(); return; }
    }
    const float t = float(now - n.shown_ms) / 1000.0f, life = float(n.seconds);
    if (t >= life) {
      g_notices.pop_front();
      context.presenter().RequestUIPaintFromUIThread();
      return;
    }
    const float alpha = std::clamp(std::min(t / 0.25f, (life - t) / 0.5f), 0.0f, 1.0f);
    const float w = float(context.render_target_width()), h = float(context.render_target_height());
    const float left = std::floor((w - float(n.width)) * 0.5f), top = std::floor(h * 0.94f - float(n.height));
    const float right = left + float(n.width), bottom = top + float(n.height);
    const uint32_t color = (uint32_t(alpha * 255.0f) << 24) | 0x00FFFFFF;
    const xe::ui::ImmediateVertex v[4] = {{left, top, 0, 0, color}, {right, top, 1, 0, color},
                                          {right, bottom, 1, 1, color}, {left, bottom, 0, 1, color}};
    const uint16_t indices[6] = {0, 1, 2, 0, 2, 3};
    g_immediate_drawer->Begin(context, w, h);
    xe::ui::ImmediateDrawBatch batch;
    batch.vertices = v;
    batch.vertex_count = 4;
    batch.indices = indices;
    batch.index_count = 6;
    g_immediate_drawer->BeginDrawBatch(batch);
    xe::ui::ImmediateDraw draw;
    draw.primitive_type = xe::ui::ImmediatePrimitiveType::kTriangles;
    draw.count = 6;
    draw.texture = n.texture.get();
    g_immediate_drawer->Draw(draw);
    g_immediate_drawer->EndDrawBatch();
    g_immediate_drawer->End();
    context.presenter().RequestUIPaintFromUIThread();
  }
};
NoticeDrawer g_notice_drawer;

}  // namespace

// ---- xe::Memory: the members the GPU uses ----
namespace xe {

bool Memory::Initialize() {
  system_page_size_ = uint32_t(xe::memory::page_size());
  system_allocation_granularity_ = uint32_t(xe::memory::allocation_granularity());
  virtual_membase_ = g_host.guestBase;
  physical_membase_ = g_host.guestBase + 0xA0000000u;
  return true;
}

void* Memory::RegisterPhysicalMemoryInvalidationCallback(PhysicalMemoryInvalidationCallback callback,
                                                         void* callback_context) {
  auto lock = xe::global_critical_region::AcquireDirect();   // Xenia's lock order: global region first
  auto* entry = new std::pair<PhysicalMemoryInvalidationCallback, void*>(callback, callback_context);
  g_invalidation_callbacks.push_back(entry);
  return entry;
}

void Memory::UnregisterPhysicalMemoryInvalidationCallback(void* callback_handle) {
  auto lock = xe::global_critical_region::AcquireDirect();   // Xenia's lock order: global region first
  auto* entry = static_cast<std::pair<PhysicalMemoryInvalidationCallback, void*>*>(callback_handle);
  auto it = std::find(g_invalidation_callbacks.begin(), g_invalidation_callbacks.end(), entry);
  if (it != g_invalidation_callbacks.end()) {
    g_invalidation_callbacks.erase(it);
    delete entry;
  }
}

void Memory::EnablePhysicalMemoryAccessCallbacks(uint32_t physical_address, uint32_t length,
                                                 bool enable_invalidation_notifications,
                                                 bool enable_data_providers) {
  if (!enable_invalidation_notifications || !length) return;
  auto lock = xe::global_critical_region::AcquireDirect();   // Xenia's lock order: global region first
  physical_address &= kPhysicalSize - 1;
  uint32_t first = physical_address >> kPageShift;
  uint32_t last = std::min((physical_address + length - 1) >> kPageShift, kPageCount - 1);
  uint32_t run = UINT32_MAX;
  for (uint32_t i = first; i <= last + 1; i++) {
    bool arm = i <= last && !IsWatched(i);
    if (arm) {
      SetWatched(i, true);
      if (run == UINT32_MAX) run = i;
    } else if (run != UINT32_MAX) {
      Protect(run, i - run, true);
      run = UINT32_MAX;
    }
  }
}

// The GPU asks the physical heap whether a range is mapped and writable before uploads and resolves. The title's
// physical memory is all committed read-write here, so the heap is a placeholder object and every query says so.
alignas(64) static uint8_t g_physical_heap_storage[sizeof(VirtualHeap)];
VirtualHeap* Memory::GetPhysicalHeap() { return reinterpret_cast<VirtualHeap*>(g_physical_heap_storage); }

bool BaseHeap::QueryRegionInfo(uint32_t base_address, HeapAllocationInfo* out_info) {
  *out_info = {};
  out_info->base_address = 0;
  out_info->allocation_base = 0;
  out_info->allocation_protect = kMemoryProtectRead | kMemoryProtectWrite;
  out_info->allocation_size = kPhysicalSize;
  out_info->region_size = kPhysicalSize;
  out_info->state = kMemoryAllocationReserve | kMemoryAllocationCommit;
  out_info->protect = kMemoryProtectRead | kMemoryProtectWrite;
  return true;
}

xe::memory::PageAccess BaseHeap::QueryRangeAccess(uint32_t low_address, uint32_t high_address) {
  return xe::memory::PageAccess::kReadWrite;
}

bool Memory::TriggerPhysicalMemoryCallbacks(global_unique_lock_type global_lock_locked_once, uint32_t virtual_address,
                                            uint32_t length, bool is_write, bool unwatch_exact_range,
                                            bool unprotect) {
  if (!is_write) return false;
  auto lock = xe::global_critical_region::AcquireDirect();   // Xenia's lock order: global region first
  uint32_t physical = virtual_address >= 0xE0000000u ? (virtual_address - 0xE0000000u + 0x1000)
                                                      : (virtual_address & (kPhysicalSize - 1));
  return TriggerWrite(physical, length, unwatch_exact_range);
}

}  // namespace xe

namespace xe {
// Xenia's system heap (XMA context array, audio callback arguments): the runtime's heaps
uint32_t Memory::SystemHeapAlloc(uint32_t size, uint32_t alignment, uint32_t system_heap_flags) {
  uint32_t address = g_host.systemAlloc(size, alignment, (system_heap_flags & kSystemHeapPhysical) != 0);
  if (address) std::memset(TranslateVirtual(address), 0, size);
  return address;
}
void Memory::SystemHeapFree(uint32_t address, uint32_t* out_region_size) {
  if (address) g_host.systemFree(address);
  if (out_region_size) *out_region_size = 0;
}
// physical windows only (the GPU and XMA only take physical memory); E0 is shifted by 4 KB
uint32_t Memory::GetPhysicalAddress(uint32_t address) const {
  if (address < 0xA0000000u) return UINT32_MAX;
  return address >= 0xE0000000u ? address - 0xE0000000u + 0x1000 : address & 0x1FFFFFFF;
}
}  // namespace xe

// ---- glue entry points used by the patched Xenia sources ----
namespace xe::port {
Memory* GuestMemory() { return g_memory; }

// texture hook (texture_cache.cc): GOW3_TEXTURE_DUMP=1 writes each new 2D texture's raw guest data once to
// texdump\<address>_f<format>_<w>x<h>_p<pitch>_<t|l>.bin (decode offline: tools/texdecode.py)
void OnTextureLoad(uint32_t guest_address, uint32_t size, uint32_t format, uint32_t width, uint32_t height,
                   uint32_t pitch, bool tiled, uint32_t dimension) {
  if (g_host.onTextureLoad && dimension == 1) g_host.onTextureLoad(guest_address, size, format, width, height, tiled);
  static const bool dump = getenv("GOW3_TEXTURE_DUMP") != nullptr;
  if (!dump || dimension != 1 || size > (16u << 20)) return;
  static std::mutex m;
  static std::set<uint64_t> seen;
  std::lock_guard lock(m);
  const uint64_t key = (uint64_t(guest_address) << 32) ^ (uint64_t(format) << 26) ^ (uint64_t(width) << 13) ^ height;
  if (!seen.insert(key).second) return;
  CreateDirectoryW(L"texdump", nullptr);
  char name[160];
  snprintf(name, sizeof(name), "texdump\\%08X_f%u_%ux%u_p%u_%c.bin", guest_address, format, width, height, pitch,
           tiled ? 't' : 'l');
  if (FILE* f = fopen(name, "wb")) {
    fwrite(g_memory->TranslatePhysical<uint8_t*>(guest_address), 1, size, f);
    fclose(f);
  }
}
uint32_t VblankIntervalMicroseconds() {
  return uint32_t(1000000 / std::max(30, g_host.vblankHz ? g_host.vblankHz : 60));
}
void CallGuest(uint32_t function, const uint64_t* args, size_t arg_count) {
  g_host.callGuest(function, args, arg_count);
}
void DispatchInterrupt(uint32_t callback, uint32_t user_data, uint32_t source, uint32_t cpu) {
  g_host.dispatchInterrupt(callback, user_data, source, cpu);
}
}  // namespace xe::port

// startup trace (gow3-gpu-start.txt next to the executable): which step of Start failed
static void StartTrace(const char* step) {
  static FILE* f = fopen("gow3-gpu-start.txt", "w");
  if (f) { fprintf(f, "%s\n", step); fflush(f); }
}

DECLARE_int32(draw_resolution_scale_x);
DECLARE_int32(draw_resolution_scale_y);
DECLARE_bool(vsync);
DECLARE_bool(xenos_sample_positions);
DECLARE_bool(draw_resolution_scaled_texture_offsets);
DECLARE_bool(resolve_resolution_scale_fill_half_pixel_offset);
DECLARE_uint32(draw_resolution_scale_threshold);

// ---- runtime-facing API ----
bool portgpu::Start(const Host& host) {
  g_host = host;
  StartTrace("logging");
  xe::InitializeLogging("gow3-gpu");
  StartTrace("memory");
  // display options, before anything reads them
  cvars::draw_resolution_scale_x = cvars::draw_resolution_scale_y = std::clamp(host.resolutionScale, 1, 3);
  cvars::vsync = host.vsync;
  cvars::xenos_sample_positions = host.xenosSamplePositions;
  // resolution-scaling hacks, for experiments (environment, both ports): GOW_SCALED_TEXTURE_OFFSETS=0/1,
  // GOW_RESOLVE_HALF_PIXEL=0/1, GOW_SCALE_THRESHOLD=<pitch in pixels> (render targets this narrow stay unscaled)
  if (const char* e = getenv("GOW_SCALED_TEXTURE_OFFSETS")) cvars::draw_resolution_scaled_texture_offsets = atoi(e) != 0;
  if (const char* e = getenv("GOW_RESOLVE_HALF_PIXEL")) cvars::resolve_resolution_scale_fill_half_pixel_offset = atoi(e) != 0;
  if (const char* e = getenv("GOW_SCALE_THRESHOLD")) cvars::draw_resolution_scale_threshold = uint32_t(atoi(e));
  if (host.scaleThreshold) cvars::draw_resolution_scale_threshold = host.scaleThreshold;
  g_memory = reinterpret_cast<xe::Memory*>(g_memory_storage);
  g_memory->Initialize();

  StartTrace("d3d12 available?");
  if (!xe::gpu::d3d12::D3D12GraphicsSystem::IsAvailable()) {
    XELOGE("port: Direct3D 12 is not available");
    return false;
  }

  std::promise<bool> window_ready;
  g_ui_thread = std::thread([&window_ready, title = std::string(host.windowTitle ? host.windowTitle : "Gears of War 3")]() {
    auto context = std::make_unique<xe::ui::Win32WindowedAppContext>(GetModuleHandleW(nullptr), SW_SHOWDEFAULT);
    if (!context->Initialize()) {
      window_ready.set_value(false);
      return;
    }
    g_app_context = context.get();
    g_window = xe::ui::Window::Create(*context, title, std::max(640, g_host.windowWidth ? g_host.windowWidth : 1280),
                                      std::max(360, g_host.windowHeight ? g_host.windowHeight : 720));
    if (g_window) g_window->AddListener(&g_close_listener);
    if (!g_window || !g_window->Open()) {
      window_ready.set_value(false);
      return;
    }
    g_running = true;
    window_ready.set_value(true);
    context->RunMainMessageLoop();
    g_running = false;
  });
  StartTrace("window thread started");
  if (!window_ready.get_future().get()) {
    XELOGE("port: could not open the window");
    return false;
  }

  StartTrace("graphics system");
  g_graphics = std::make_unique<PortGraphicsSystem>();
  if (g_graphics->Setup(nullptr, nullptr, g_app_context, true) != 0) {   // X_STATUS_SUCCESS
    XELOGE("port: graphics system setup failed");
    return false;
  }
  // shaders and pipelines from earlier runs: compiled in the background (draws wait for them, no hitch), and new
  // ones are appended as the game meets them
  if (host.shaderCacheRoot) {
    StartTrace("shader storage");
    g_graphics->InitializeShaderStorage(std::filesystem::path(host.shaderCacheRoot), 0x4D53082D, false, nullptr);
  }
  StartTrace("presenter");
  const bool fullscreen = host.fullscreen;
  const int upscaling = host.upscaling;
  const float sharpness = host.sharpness;
  g_app_context->CallInUIThreadSynchronous([fullscreen, upscaling, sharpness]() {
    g_window->SetPresenter(g_graphics->presenter());
    // scaling to the window (what Xenia's Display menu sets)
    auto paint = g_graphics->presenter()->GetGuestOutputPaintConfigFromUIThread();
    using Effect = xe::ui::Presenter::GuestOutputPaintConfig::Effect;
    paint.SetEffect(upscaling == 0 ? Effect::kBilinear : upscaling == 1 ? Effect::kCas : Effect::kFsr);
    paint.SetCasAdditionalSharpness(sharpness);
    g_graphics->presenter()->SetGuestOutputPaintConfigFromUIThread(paint);
    if (fullscreen) g_window->SetFullscreen(true);
    // notices over the game (portgpu::ShowNotice)
    g_immediate_drawer = g_graphics->provider()->CreateImmediateDrawer();
    if (g_immediate_drawer) {
      g_immediate_drawer->SetPresenter(g_graphics->presenter());
      g_graphics->presenter()->AddUIDrawerFromUIThread(&g_notice_drawer, 1000);
    }
  });
  using SwapPostEffect = xe::gpu::CommandProcessor::SwapPostEffect;
  g_graphics->command_processor()->SetDesiredSwapPostEffect(host.antialiasing == 1   ? SwapPostEffect::kFxaa
                                                            : host.antialiasing == 2 ? SwapPostEffect::kFxaaExtreme
                                                                                     : SwapPostEffect::kNone);
  StartTrace("started");
  return true;
}

void portgpu::Shutdown() {
  if (g_graphics) {
    if (g_app_context)
      g_app_context->CallInUIThreadSynchronous([]() {
        if (g_immediate_drawer) {
          g_graphics->presenter()->RemoveUIDrawerFromUIThread(&g_notice_drawer);
          g_immediate_drawer->SetPresenter(nullptr);
          std::lock_guard<std::mutex> lock(g_notice_mutex);
          g_notices.clear();
          g_immediate_drawer.reset();
        }
        g_window->SetPresenter(nullptr);
      });
    g_graphics->Shutdown();
    g_graphics.reset();
  }
}

void portgpu::ShowNotice(const char* title, const char* text, int seconds) {
  PendingNotice n = RenderNotice(title ? title : "", text ? text : "", seconds);
  {
    std::lock_guard<std::mutex> lock(g_notice_mutex);
    while (g_notices.size() > 4) g_notices.erase(g_notices.begin() + 1);   // a burst: keep the one shown and the newest
    if (!g_notices.empty() && g_notices.front().texture) {
      // the one on screen gives way sooner when others wait
      const int shown = int((GetTickCount64() - g_notices.front().shown_ms) / 1000);
      g_notices.front().seconds = std::min(g_notices.front().seconds, shown + 3);
    }
    g_notices.push_back(std::move(n));
  }
  if (g_app_context)
    g_app_context->CallInUIThread([]() {
      if (g_graphics && g_graphics->presenter()) g_graphics->presenter()->RequestUIPaintFromUIThread();
    });
}

void portgpu::InitializeRingBuffer(uint32_t ptr, uint32_t size_log2) { g_graphics->InitializeRingBuffer(ptr, size_log2); }
void portgpu::EnableReadPointerWriteBack(uint32_t ptr, uint32_t block_size_log2) {
  g_graphics->EnableReadPointerWriteBack(ptr, block_size_log2);
}
void portgpu::SetInterruptCallback(uint32_t callback, uint32_t user_data) {
  g_graphics->SetInterruptCallback(callback, user_data);
}
void portgpu::WriteRegister(uint32_t index, uint32_t value) { g_graphics->WriteRegister(index * 4, value); }
uint32_t portgpu::ReadRegister(uint32_t index) { return g_graphics->ReadRegister(index * 4); }

bool portgpu::HandleAccessViolation(void* host_address, bool is_write) {
  if (!is_write || !g_memory) return false;
  uintptr_t offset = uintptr_t(host_address) - uintptr_t(g_host.guestBase);
  if (offset < 0xA0000000u || offset >= 0x100000000ull) return false;
  uint32_t physical = offset >= 0xE0000000u ? uint32_t(offset - 0xE0000000u + 0x1000) : uint32_t(offset & (kPhysicalSize - 1));
  auto lock = xe::global_critical_region::AcquireDirect();   // Xenia's lock order: global region first
  if (TriggerWrite(physical, 1, false)) return true;
  // raced with another thread that already unwatched it: retry if the page is writable again
  MEMORY_BASIC_INFORMATION mbi;
  return VirtualQuery(host_address, &mbi, sizeof(mbi)) && mbi.Protect == PAGE_READWRITE;
}

bool portgpu::IsRunning() { return g_running; }

// ---- audio ----
namespace {
std::unique_ptr<xe::apu::AudioSystem> g_audio;
}

bool portgpu::StartAudio() {
  if (!g_memory) {
    g_memory = reinterpret_cast<xe::Memory*>(g_memory_storage);
    g_memory->Initialize();
  }
  g_audio = xe::apu::xaudio2::XAudio2AudioSystem::Create(nullptr);
  if (!g_audio || g_audio->Setup(nullptr) != 0) {
    XELOGE("port: audio system setup failed");
    g_audio.reset();
    return false;
  }
  return true;
}

int portgpu::AudioRegisterClient(uint32_t callback, uint32_t callback_arg) {
  if (!g_audio) return -1;
  size_t index = 0;
  return g_audio->RegisterClient(callback, callback_arg, &index) == 0 ? int(index) : -1;
}
void portgpu::AudioUnregisterClient(int index) {
  if (g_audio && index >= 0) g_audio->UnregisterClient(size_t(index));
}
void portgpu::AudioSubmitFrame(int index, uint32_t samples) {
  if (g_audio && index >= 0) g_audio->SubmitFrame(size_t(index), g_memory->TranslateVirtual<float*>(samples));
}
uint32_t portgpu::XmaAllocateContext() { return g_audio ? g_audio->xma_decoder()->AllocateContext() : 0; }
void portgpu::XmaReleaseContext(uint32_t context) {
  if (g_audio) g_audio->xma_decoder()->ReleaseContext(context);
}
uint32_t portgpu::XmaReadRegister(uint32_t index) {
  return g_audio ? g_audio->xma_decoder()->ReadRegister(index * 4) : 0;
}
void portgpu::XmaWriteRegister(uint32_t index, uint32_t raw_value) {
  if (g_audio) g_audio->xma_decoder()->WriteRegister(index * 4, raw_value);
}

bool portgpu::CaptureScreenshot(const char* png_path) {
  if (!g_graphics || !g_graphics->presenter()) return false;
  xe::ui::RawImage image;
  if (!g_graphics->presenter()->CaptureGuestOutput(image) || !image.width) return false;
  for (size_t i = 3; i < image.data.size(); i += 4) image.data[i] = 0xFF;   // X8 -> opaque
  return stbi_write_png(png_path, int(image.width), int(image.height), 4, image.data.data(), int(image.stride)) != 0;
}

void portgpu::ToggleFullscreen() {
  if (!g_app_context || !g_window) return;
  g_app_context->CallInUIThread([]() {
    if (g_window) g_window->SetFullscreen(!g_window->IsFullscreen());
  });
}

void portgpu::SetMouseCaptured(bool captured) {
  if (!g_app_context || !g_window) return;
  g_app_context->CallInUIThread([captured]() {
    if (!g_window) return;
    g_window->SetCursorVisibility(captured ? xe::ui::Window::CursorVisibility::kHidden
                                           : xe::ui::Window::CursorVisibility::kVisible);
    // clipping: the runtime pins the cursor to the window center while captured (hid.cpp)
  });
}

void* portgpu::GetWindowHandle() {
  return g_window ? static_cast<xe::ui::Win32Window*>(g_window.get())->hwnd() : nullptr;
}

void portgpu::ReadPhysical(uint32_t address, void* out, size_t size) {
  memcpy(out, g_memory->TranslatePhysical<uint8_t*>(address), size);
}

void portgpu::WritePhysical(uint32_t address, const void* data, size_t size, bool invalidate) {
  memcpy(g_memory->TranslatePhysical<uint8_t*>(address), data, size);
  if (invalidate && g_graphics) {
    auto* cp = static_cast<xe::gpu::d3d12::D3D12CommandProcessor*>(g_graphics->command_processor());
    cp->port_shared_memory().MemoryInvalidationCallback(address, uint32_t(size), true);
  }
}
