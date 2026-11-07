#!/usr/bin/env python3
"""Two-finger pinch on the phone via scrcpy's on-device server (adb `input` is single-touch only).

Starts scrcpy-server with video (the server needs the video size to map touch coordinates),
discards the video stream, and injects touch events on the control socket.

Usage: pinch.py out|in [times]      (out = zoom out)
"""
import os, sys, time, socket, struct, random, subprocess, threading, glob

SERVER_JAR = (glob.glob('/opt/homebrew/Cellar/scrcpy/*/share/scrcpy/scrcpy-server') or
              ['/opt/homebrew/share/scrcpy/scrcpy-server'])[-1]
REMOTE = '/data/local/tmp/scrcpy-server-bot.jar'
PORT = 27199


def version():
    out = subprocess.run(['scrcpy', '--version'], capture_output=True, text=True).stdout
    return out.split()[1]


def adb(*a, **kw):
    return subprocess.run(['adb', *a], capture_output=True, text=True, **kw)


def recv_exact(s, n):
    buf = b''
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('socket closed')
        buf += chunk
    return buf


class Touch:
    def __init__(self):
        adb('shell', 'pkill -f scrcpy-server-bot.jar')  # leftovers from an earlier run
        SCID = '%08x' % random.randrange(1 << 31)
        adb('push', SERVER_JAR, REMOTE)
        adb('forward', f'tcp:{PORT}', f'localabstract:scrcpy_{SCID}')
        args = ['shell', f'CLASSPATH={REMOTE}', 'app_process', '/', 'com.genymobile.scrcpy.Server',
                version(), f'scid={SCID}', 'log_level=warn', 'tunnel_forward=true', 'video=true',
                'audio=false', 'control=true', 'max_size=600', 'video_bit_rate=500000',
                'send_device_meta=false', 'send_dummy_byte=true', 'send_stream_meta=true',
                'send_frame_meta=false', 'cleanup=false']
        self.proc = subprocess.Popen(['adb', *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = time.time() + 15
        while True:
            try:
                self.video = socket.create_connection(('127.0.0.1', PORT), timeout=5)
                if self.video.recv(1):  # dummy byte: server is up
                    break
                self.video.close()  # adb accepted but nothing is listening yet
            except OSError:
                pass
            if time.time() > deadline or self.proc.poll() is not None:
                self.close()
                raise RuntimeError('scrcpy server did not start')
            time.sleep(0.25)
        self.control = socket.create_connection(('127.0.0.1', PORT), timeout=5)
        self.video.settimeout(10)
        # scrcpy 4.x stream meta: codec id, flags, width, height (4 bytes each)
        codec, _flags, self.w, self.h = struct.unpack('>IIII', recv_exact(self.video, 16))
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        self.video.settimeout(None)
        try:
            while self.video.recv(65536):
                pass
        except OSError:
            pass

    def event(self, action, pid, x, y, pressure=1.0):
        msg = struct.pack('>BBqiiHHHII', 2, action, pid, int(x), int(y), self.w, self.h,
                          0xffff if pressure else 0, 0, 0)
        self.control.sendall(msg)

    def pinch(self, zoom_out=True, steps=14):
        """Fingers from spread-out to close together around the centre (= zoom out)."""
        cx, cy = self.w / 2 + random.uniform(-8, 8), self.h / 2 + random.uniform(-6, 6)
        far, near = self.w * random.uniform(0.30, 0.34), self.w * random.uniform(0.04, 0.06)
        a, b = (far, near) if zoom_out else (near, far)
        self.event(0, 7, cx - a, cy)
        self.event(0, 8, cx + a, cy)  # second DOWN becomes POINTER_DOWN on the server
        for i in range(1, steps + 1):
            d = a + (b - a) * i / steps
            time.sleep(random.uniform(0.015, 0.03))
            self.event(2, 7, cx - d, cy + random.uniform(-1, 1))
            self.event(2, 8, cx + d, cy + random.uniform(-1, 1))
        time.sleep(0.05)
        self.event(1, 8, cx + b, cy, 0)
        self.event(1, 7, cx - b, cy, 0)

    def close(self):
        for s in (getattr(self, 'control', None), getattr(self, 'video', None)):
            if s is None:
                continue
            try:
                s.close()
            except OSError:
                pass
        self.proc.terminate()
        adb('shell', 'pkill -f scrcpy-server-bot.jar')
        adb('forward', '--remove', f'tcp:{PORT}')


def zoom_out(times=3):
    t = Touch()
    try:
        for _ in range(times):
            t.pinch(True)
            time.sleep(random.uniform(0.35, 0.6))
    finally:
        t.close()


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'out'
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    t = Touch()
    print('video size', t.w, t.h)
    try:
        for _ in range(n):
            t.pinch(mode == 'out')
            time.sleep(0.5)
    finally:
        t.close()
