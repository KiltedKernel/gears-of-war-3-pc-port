"""UnrealScript bytecode of cooked Gears of War 3 (engine 828) packages as readable pseudo-code (offline).

A Function/State export: +12 Next, +16 SuperStruct, +20 Children, +24 script size in memory, +28 script size on disk,
then the script, then (functions) iNative u16, OperPrecedence byte, FunctionFlags u32. In memory an object reference
takes 8 bytes (4 on disk), a name 8: jump targets are memory offsets, tracked while reading.
Native functions are named from every package given (exports whose trailing iNative is set).
usage: python -P uscript.py <package> <function path substring> [more packages for native names]"""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk

NATIVES = {}


def native_names(packages):
    for p in packages:
        for i, e in enumerate(p.exports):
            if p.class_name(e['class']) != 'Function': continue
            d = p.export_data(i)
            if len(d) < 32: continue
            disk = struct.unpack_from('>i', d, 28)[0]
            t = 32 + disk
            if t + 7 > len(d): continue
            n = struct.unpack_from('>H', d, t)[0]
            if n: NATIVES.setdefault(n, e['name'])


class Reader:
    def __init__(self, p, d, start, size):
        self.p, self.d, self.o, self.end, self.mem = p, d, start, start + size, 0
        self.start = start

    def u8(self): v = self.d[self.o]; self.o += 1; self.mem += 1; return v
    def u16(self): v = struct.unpack_from('>H', self.d, self.o)[0]; self.o += 2; self.mem += 2; return v
    def i32(self): v = struct.unpack_from('>i', self.d, self.o)[0]; self.o += 4; self.mem += 4; return v
    def f32(self): v = struct.unpack_from('>f', self.d, self.o)[0]; self.o += 4; self.mem += 4; return v

    def obj(self):
        v = struct.unpack_from('>i', self.d, self.o)[0]; self.o += 4; self.mem += 8
        if v > 0: return self.p.exports[v - 1]['name']
        if v < 0: return self.p.imports[-v - 1]['name'] if hasattr(self.p, 'imports') else f'import{v}'
        return 'None'

    def name(self):
        i, n = struct.unpack_from('>ii', self.d, self.o); self.o += 8; self.mem += 8
        return self.p.fname(i, n)

    def zstr(self):
        e = self.d.index(b'\0', self.o); s = self.d[self.o:e].decode('latin-1'); self.mem += e + 1 - self.o; self.o = e + 1
        return s

    def wstr(self):
        k = self.o
        while self.d[k:k + 2] != b'\0\0': k += 2
        s = self.d[self.o:k].decode('utf-16-be'); self.mem += k + 2 - self.o; self.o = k + 2
        return s

    def params(self):
        args = []
        while self.d[self.o] != 0x16:
            args.append(self.expr())
        self.u8()
        return args

    def expr(self):
        t = self.u8()
        if t >= 0x70: return f'{NATIVES.get(t, "native%d" % t)}({", ".join(self.params())})'
        if 0x60 <= t < 0x70:
            n = ((t - 0x60) << 8) + self.u8()
            return f'{NATIVES.get(n, "native%d" % n)}({", ".join(self.params())})'
        if t in (0x00, 0x01, 0x02, 0x03, 0x48): return {0: '', 1: 'self.', 2: 'default.', 3: 'state.', 0x48: 'out '}[t] + self.obj()
        if t == 0x04: return 'return ' + self.expr()
        if t == 0x05:
            self.obj(); self.u8(); return 'switch (' + self.expr() + ')'
        if t == 0x06: return f'goto @{self.u16():04X}'
        if t == 0x07:
            j = self.u16(); return f'if !({self.expr()}) goto @{j:04X}'
        if t == 0x08: return 'stop'
        if t == 0x09:
            self.u16(); self.u8(); return 'assert(' + self.expr() + ')'
        if t == 0x0A:
            n = self.u16(); return 'default:' if n == 0xFFFF else f'case {self.expr()}: (next @{n:04X})'
        if t == 0x0B: return 'nothing'
        if t == 0x0C:
            out = []
            while True:
                nm = self.name(); off = self.i32()
                if nm == 'None': break
                out.append(f'{nm}@{off:04X}')
            return 'labels ' + ', '.join(out)
        if t == 0x0D: return 'goto label ' + self.expr()
        if t == 0x0E: self.obj(); return self.expr()
        if t == 0x0F:
            a = self.expr(); return f'{a} = {self.expr()}'
        if t == 0x10:
            i = self.expr(); return f'{self.expr()}[{i}]'
        if t == 0x11: return 'new(' + ', '.join(self.expr() for _ in range(4)) + ')'
        if t in (0x12, 0x19):
            a = self.expr(); self.u16(); self.obj(); self.u8()
            return f'{a}{".static." if t == 0x12 else "."}{self.expr()}'
        if t == 0x13:
            c = self.obj(); return f'class<{c}>({self.expr()})'
        if t == 0x14:
            a = self.expr(); return f'{a} = {self.expr()}'
        if t == 0x15: return 'endparmvalue'
        if t == 0x16: return ')'
        if t == 0x17: return 'self'
        if t == 0x18:
            self.u16(); return self.expr()
        if t == 0x1A:
            i = self.expr(); return f'{self.expr()}[{i}]'
        if t == 0x1B:
            n = self.name(); return f'{n}({", ".join(self.params())})'
        if t == 0x1C:
            f = self.obj(); return f'{f}({", ".join(self.params())})'
        if t == 0x1D: return str(self.i32())
        if t == 0x1E: return repr(round(self.f32(), 4))
        if t == 0x1F: return repr(self.zstr())
        if t == 0x20: return self.obj()
        if t == 0x21: return "'" + self.name() + "'"
        if t == 0x22: return f'rot({self.i32()}, {self.i32()}, {self.i32()})'
        if t == 0x23: return f'vect({self.f32():g}, {self.f32():g}, {self.f32():g})'
        if t == 0x24: return str(self.u8())
        if t == 0x25: return '0'
        if t == 0x26: return '1'
        if t == 0x27: return 'true'
        if t == 0x28: return 'false'
        if t == 0x29: return 'nativeparm ' + self.obj()
        if t == 0x2A: return 'None'
        if t == 0x2C: return str(self.u8())
        if t == 0x2D: return self.expr()
        if t == 0x2E:
            c = self.obj(); return f'{c}({self.expr()})'
        if t == 0x2F:
            a = self.expr(); j = self.u16(); return f'foreach {a} (end @{j:04X})'
        if t == 0x30: return 'iteratorpop'
        if t == 0x31: return 'iteratornext'
        if t in (0x32, 0x33):
            self.obj(); a = self.expr(); return f'{a} {"==" if t == 0x32 else "!="} {self.expr()}'
        if t == 0x34: return repr(self.wstr())
        if t == 0x35:
            m = self.obj(); self.obj(); self.u8(); self.u8(); return f'{self.expr()}.{m}'
        if t == 0x36: return self.expr() + '.Length'
        if t == 0x37:
            n = self.name(); return f'global.{n}({", ".join(self.params())})'
        if t == 0x38:
            self.u8(); return self.expr()
        if t == 0x39:
            a = self.expr(); b = self.expr(); c = self.expr(); return f'{a}.Insert({b}, {c})'
        if t == 0x3A: self.obj(); return 'return'
        if t in (0x3B, 0x3C, 0x3D, 0x3E):
            a = self.expr(); b = self.expr(); self.u8(); return f'{a} {"==" if t in (0x3B, 0x3D) else "!="} {b}'
        if t == 0x3F: return 'None'
        if t == 0x40:
            a = self.expr(); b = self.expr(); c = self.expr(); return f'{a}.Remove({b}, {c})'
        if t == 0x41:
            self.i32(); self.i32(); self.i32(); self.u8(); return None
        if t == 0x42:
            self.u8(); self.obj(); n = self.name(); return f'{n}({", ".join(self.params())})'
        if t == 0x43:
            n = self.name(); self.obj(); return n
        if t == 0x44:
            a = self.expr(); return f'{a} = {self.expr()}'
        if t == 0x45:
            c = self.expr(); self.u16(); a = self.expr(); self.u16(); return f'({c} ? {a} : {self.expr()})'
        if t == 0x46:
            a = self.expr(); self.u16(); return f'{a}.Find({self.expr()})'
        if t == 0x47:
            a = self.expr(); self.u16(); b = self.expr(); return f'{a}.Find({b}, {self.expr()})'
        if t == 0x49:
            self.u16(); v = self.expr(); self.u8(); return f'default parm {v}'
        if t == 0x4A: return 'emptyparm'
        if t == 0x4B: return self.name()
        if t == 0x51: return self.expr()
        if t == 0x52:
            c = self.obj(); return f'{c}({self.expr()})'
        if t == 0x53: return 'end'
        if t in (0x54, 0x55, 0x56):
            a = self.expr(); self.u16(); b = self.expr(); self.u8()
            return f'{a}.{ {0x54: "Add", 0x55: "AddItem", 0x56: "RemoveItem"}[t]}({b})'
        if t == 0x57:
            a = self.expr(); self.u16(); b = self.expr(); c = self.expr(); self.u8(); return f'{a}.InsertItem({b}, {c})'
        if t == 0x58:
            a = self.expr(); b = self.expr(); self.u8(); c = self.expr(); self.u16(); return f'foreach {a}({b}, {c})'
        if t == 0x59:
            a = self.expr(); self.u16(); b = self.expr(); self.u8(); return f'{a}.Sort({b})'
        if t == 0x5A:
            self.u16(); return 'editoronly ' + self.expr()
        raise ValueError(f'token {t:02X} at disk +{self.o - 1 - self.start}')


def disassemble(p, i):
    d = p.export_data(i)
    mem, disk = struct.unpack_from('>ii', d, 24)
    r = Reader(p, d, 32, disk)
    lines = []
    while r.o < r.end:
        at = r.mem
        try:
            e = r.expr()
        except Exception as ex:
            lines.append(f'  @{at:04X}  <{ex}>'); break
        if e is not None and e != 'end': lines.append(f'  @{at:04X}  {e}')
    return lines


if __name__ == '__main__':
    p = upk.Package(sys.argv[1])
    native_names([p] + [upk.Package(f) for f in sys.argv[3:]])
    for i, e in enumerate(p.exports):
        if p.class_name(e['class']) in ('Function', 'State') and sys.argv[2] in p.path(i):
            print(p.path(i))
            for line in disassemble(p, i): print(line)
