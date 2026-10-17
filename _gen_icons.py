# -*- coding: utf-8 -*-
"""生成 PWA 应用图标（PNG），零第三方依赖。

用法：python _gen_icons.py
输出：static/icon-192.png、icon-512.png、icon-maskable.png、apple-touch-icon.png
图案：热榜红渐变圆角方块 + 白色火焰字形（纯几何绘制，避免中文字体依赖）
"""
import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')


def write_png(path, width, height, pixels):
    """pixels: list of rows, each row list of (r,g,b) tuples."""
    raw = b''
    for row in pixels:
        raw += b'\x00' + b''.join(struct.pack('BBB', *px) for px in row)

    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)

    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(raw, 9))
           + chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(png)
    return len(png)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_icon(size, radius_ratio=0.22, maskable=False):
    """绘制圆角方块 + 火焰图案。maskable 时留出安全边距（内容缩到 ~60%）。"""
    c1 = (0xd5, 0x49, 0x41)   # 热榜红
    c2 = (0xf7, 0x6b, 0x5e)   # 亮红
    r = int(size * radius_ratio)
    inset = int(size * 0.20) if maskable else 0

    # 火焰形状：尖顶 + 收窄的圆润底部（小火苗更易辨识）
    def in_flame(nx, ny):
        if ny < -0.78:
            return False
        if ny < 0.30:                      # 尖顶段：随 y 收窄
            t = (ny + 0.78) / 1.08         # 0 -> 1
            half = 0.045 + 0.42 * t ** 1.6
            return abs(nx) <= half
        # 下部圆润（略扁）
        return (nx / 0.47) ** 2 + ((ny - 0.42) / 0.42) ** 2 <= 1.0

    # 内焰（小三角）
    def in_inner(nx, ny):
        if ny < 0.30:
            t = (ny + 0.10) / 0.55
            half = 0.03 + 0.20 * t
            return abs(nx) <= half
        return (nx / 0.22) ** 2 + ((ny - 0.50) / 0.24) ** 2 <= 1.0

    px = []
    for y in range(size):
        row = []
        for x in range(size):
            # 圆角判定
            dx = min(x, size - 1 - x)
            dy = min(y, size - 1 - y)
            corner = 0
            if dx < r and dy < r:
                corner = ((r - dx) ** 2 + (r - dy) ** 2) ** 0.5 - r
            if corner > 0:
                row.append((241, 242, 244))
                continue

            t = (x + y) / (2.0 * size)
            base = lerp(c1, c2, min(1.0, t * 1.35))

            # 内容区（maskable 留安全边距）
            cx, cy = size / 2.0, size / 2.0
            half_extent = (size / 2.0) - inset
            nx = (x - cx) / half_extent
            ny = (y - cy) / half_extent

            if in_inner(nx, ny):
                row.append((255, 245, 235))
            elif in_flame(nx, ny):
                row.append((255, 255, 255))
            else:
                row.append(base)
        px.append(row)
    return px


def main():
    targets = [
        ('icon-192.png', 192, False),
        ('icon-512.png', 512, False),
        ('icon-maskable.png', 512, True),
        ('apple-touch-icon.png', 180, False),
    ]
    for name, size, maskable in targets:
        pixels = make_icon(size, maskable=maskable)
        n = write_png(os.path.join(OUT, name), size, size, pixels)
        print(f'{name}: {size}x{size} -> {n} bytes')


if __name__ == '__main__':
    main()
