#!/usr/bin/env python3
"""Clash of Clans multiplayer battle loop (home village, TH >= MIN_TH only).

Loop: home -> Attack -> Find a Match -> army Attack! -> scout (detect TH level)
      -> Next if TH < MIN_TH, else deploy everything at one corner -> monitor -> Return Home.

Deploy: heroes as single taps at distinct points near a randomly chosen, red-zone-checked
corner anchor; troops by press-and-hold with the finger drifting a few px; spells on the TH.
Every tap position and wait is randomized. Screen is the 1200x540 `wm size` override.

Run:   bot/.venv/bin/python -u bot/bot.py >> /tmp/coc-bot/bot.log 2>&1 &
Stop:  touch /tmp/coc-bot/STOP   (exits at the next home screen)
Env:   MIN_TH (default 15), ONCE=1 (stop after one battle), COC_BOT_LOG (default /tmp/coc-bot)
"""
import os, sys, math, time, random, subprocess, datetime
import cv2, numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(BASE, 'tpl')
TH_REF = os.path.join(BASE, '..', 'townhalls.png')
LOG = os.environ.get('COC_BOT_LOG', '/tmp/coc-bot')
for d in ('th', 'res', 'unk', 'plan'):
    os.makedirs(os.path.join(LOG, d), exist_ok=True)

MIN_TH = int(os.environ.get('MIN_TH', 15))
PKG = 'com.supercell.clashofclans'
STOP_FILE = os.path.join(LOG, 'STOP')

stats = {'battles': 0, 'skipped': 0, 'start': time.time(), 'progress': time.time()}


def log(*a):
    print(datetime.datetime.now().strftime('%m-%d %H:%M:%S'), *a, flush=True)


def ts():
    return datetime.datetime.now().strftime('%m%d_%H%M%S')


def rs(a, b):
    time.sleep(random.uniform(a, b))


# ---------------------------------------------------------------- adb
def cap():
    for _ in range(5):
        try:
            out = subprocess.run(['adb', 'exec-out', 'screencap', '-p'],
                                 capture_output=True, timeout=20).stdout
            img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
            if img is not None and img.shape[1] == 1200:
                return img
        except Exception as e:
            log('cap error', e)
        time.sleep(2)
    raise RuntimeError('screencap failed')


def sh(cmd, timeout=120):
    subprocess.run(['adb', 'shell', cmd], timeout=timeout,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def tap(x, y):
    sh(f'input tap {int(round(x))} {int(round(y))}')


def tap_box(cx, cy, w, h):
    """Tap a random spot inside the central part of a button."""
    tap(cx + random.uniform(-w / 4, w / 4), cy + random.uniform(-h / 4, h / 4))


def run_cmds(cmds):
    """Run shell commands as one adb script so in-script sleeps set the real timing."""
    for i in range(0, len(cmds), 80):
        sh('; '.join(cmds[i:i + 80]))


# ---------------------------------------------------------------- UI templates
def load(name):
    p = os.path.join(TPL, name + '.png')
    return cv2.imread(p) if os.path.exists(p) else None


T = {n: load(n) for n in ['home_attack', 'findmatch', 'army_attack', 'next',
                          'endbattle', 'surrender', 'return', 'speed1x']}
HEROES = ['king', 'queen', 'warden', 'rc']

# where each UI template normally sits (x0, y0) -> searched in a +-pad window
UI_POS = {'home_attack': (68, 440), 'findmatch': (105, 367), 'army_attack': (890, 470),
          'next': (995, 352), 'endbattle': (65, 388), 'surrender': (65, 388),
          'return': (542, 440), 'speed1x': None}


def match_at(img, name, pad=25, thr=0.82):
    t = T.get(name)
    if t is None:
        return None
    h, w = t.shape[:2]
    pos = UI_POS.get(name)
    if pos is None:
        x0, y0, x1, y1 = 900, 150, 1200, 440
    else:
        x0, y0 = max(0, pos[0] - pad), max(0, pos[1] - pad)
        x1, y1 = min(1200, pos[0] + w + pad), min(540, pos[1] + h + pad)
    r = cv2.matchTemplate(img[y0:y1, x0:x1], t, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(r)
    if mx >= thr:
        return (x0 + loc[0] + w / 2, y0 + loc[1] + h / 2, w, h, mx)
    return None


def save_tpl(name, crop):
    """Bootstrap a missing template from the live screen (after a reboot wiped them etc)."""
    cv2.imwrite(os.path.join(TPL, name + '.png'), crop)
    T[name] = crop.copy()
    log(f'captured missing template {name}')


def green_return_button(img):
    """Return Home button green (~RGB 196,235,95) on both side strips of the button."""
    def frac(reg):
        b, g, r = reg[:, :, 0], reg[:, :, 1], reg[:, :, 2]
        return ((g > 200) & (g - b > 100) & (r > 150) & (r < g)).mean()
    im = img.astype(np.int16)
    return frac(im[445:485, 544:558]) > 0.4 and frac(im[445:485, 646:660]) > 0.4


def state(img):
    for n, st in [('return', 'result'), ('next', 'scout'), ('endbattle', 'battle'),
                  ('surrender', 'battle'), ('army_attack', 'army'),
                  ('findmatch', 'menu'), ('home_attack', 'home')]:
        m = match_at(img, n)
        if m:
            return st, m
    if T.get('return') is None and green_return_button(img):
        rs(2.5, 3.5)  # wait for the result screen to settle, then learn the button
        img = cap()
        save_tpl('return', img[440:488, 542:662])
        return 'result', match_at(img, 'return', thr=0.5)
    return 'unknown', None


# ---------------------------------------------------------------- TH detection
def build_th_refs():
    """Masked TH templates cut from townhalls.png (levels 9-18, white background)."""
    im = cv2.imread(TH_REF, cv2.IMREAD_UNCHANGED)
    bgr, a = im[:, :, :3], im[:, :, 3]
    fg = ((bgr.min(axis=2) < 235) & (a > 0)).astype(np.uint8)
    cells = {9: (60, 0, 300, 250), 10: (320, 0, 570, 250), 11: (580, 0, 830, 250),
             12: (840, 0, 1090, 250), 13: (60, 310, 300, 550), 14: (320, 310, 570, 550),
             15: (580, 310, 830, 550), 16: (840, 300, 1090, 550), 17: (310, 590, 570, 860),
             18: (570, 590, 830, 860)}
    refs = {}
    for lv, (x0, y0, x1, y1) in cells.items():
        n, lab, st, _ = cv2.connectedComponentsWithStats(fg[y0:y1, x0:x1])
        k = 1 + np.argmax(st[1:, cv2.CC_STAT_AREA])  # the building, not the label
        ys, xs = np.where(lab == k)
        crop = bgr[y0 + ys.min():y0 + ys.max() + 1, x0 + xs.min():x0 + xs.max() + 1]
        mask = (lab[ys.min():ys.max() + 1, xs.min():xs.max() + 1] == k).astype(np.uint8) * 255
        refs[lv] = [(cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_AREA),
                     cv2.resize(mask, None, fx=s, fy=s, interpolation=cv2.INTER_NEAREST))
                    for s in (0.28, 0.30, 0.32)]
    return refs


REFS = build_th_refs()


def detect_th(img):
    """[(score, level, (x, y)), ...] best first."""
    x0, y0, x1, y1 = 150, 0, 1060, 435
    roi = img[y0:y1, x0:x1]
    res = []
    for lv, lst in REFS.items():
        best = (-1, None)
        for tt, mm in lst:
            r = cv2.matchTemplate(roi, tt, cv2.TM_CCORR_NORMED, mask=mm)
            r = np.nan_to_num(r, nan=-1, posinf=-1, neginf=-1)
            _, mx, _, loc = cv2.minMaxLoc(r)
            if mx > best[0]:
                best = (mx, (x0 + loc[0] + tt.shape[1] // 2, y0 + loc[1] + tt.shape[0] // 2))
        res.append((best[0], lv, best[1]))
    res.sort(reverse=True)
    return res


# ---------------------------------------------------------------- troop bar
CARD_NAMES = ['kane', 'edrag', 'siege', 'cc', 'king', 'queen', 'warden', 'rc',
              'freeze', 'ccspell', 'spell2']
CARDS = {n: load('card_' + n) for n in CARD_NAMES}
SPELL_NAMES = {'freeze', 'ccspell', 'spell2'}
BODY_NAMES = {'siege', 'cc'}  # single-unit cards that get a health bar like heroes


def bar_slots(img):
    """Card centers in the troop bar: runs of non-dark columns ~66px wide."""
    hsv = cv2.cvtColor(img[445:535], cv2.COLOR_BGR2HSV)
    gap = (hsv[:, :, 2] < 60).mean(axis=0) > 0.5
    out, s = [], None
    for x in range(170, 1200):
        if not gap[x] and s is None:
            s = x
        elif gap[x] and s is not None:
            if 55 <= x - s <= 78:
                seg = hsv[10:80, s:x]
                forest = ((seg[:, :, 0] >= 30) & (seg[:, :, 0] <= 90) & (seg[:, :, 1] > 60)).mean()
                if forest < 0.45:  # empty dashed slots show the green forest behind
                    out.append((s + x) / 2)
            s = None
    return out


def find_cards(img):
    """name -> (cx, cy). Extra unrecognised cards become 'utroop@x' / 'uspell@x'."""
    bar = img[440:540]
    hits = []
    for n, t in CARDS.items():
        if t is None:
            continue
        r = cv2.matchTemplate(bar, t, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(r)
        if mx > 0.8:
            hits.append((mx, n, loc[0] + t.shape[1] / 2))
    found = {}
    for mx, n, cx in sorted(hits, reverse=True):  # one name per slot
        if all(abs(cx - v[0]) > 30 for v in found.values()):
            found[n] = (cx, 488.0)
    hx = [found[h][0] for h in HEROES if h in found]
    last_hero = max(hx) if hx else 700
    for cx in bar_slots(img):
        if all(abs(cx - v[0]) > 30 for v in found.values()):
            found[('uspell@%d' if cx > last_hero else 'utroop@%d') % cx] = (cx, 488.0)
    return found


def is_spell(name):
    return name in SPELL_NAMES or name.startswith('uspell')


def card_sat(img, cx):
    hsv = cv2.cvtColor(img[460:520, int(cx) - 25:int(cx) + 25], cv2.COLOR_BGR2HSV)
    return hsv[:, :, 1].mean()


def card_tap_pt(cx):
    # upper-middle of card: avoids the warden/siege toggle and the level badge
    return cx + random.uniform(-12, 12), random.uniform(462, 482)


def hero_deployed(img, cx):
    # deployed heroes/sieges get a bright green health bar above the card
    strip = img[428:441, int(cx) - 25:int(cx) + 25].astype(np.int16)
    b, g, r = strip[:, :, 0], strip[:, :, 1], strip[:, :, 2]
    m = (g > 195) & (b < 60) & (r < 160) & (g - r > 80)
    return m.mean(axis=1).max() > 0.2


# ---------------------------------------------------------------- map geometry
# Max-zoomed-out battle view. Side corners of the map rim at (100,275) / (1103,275);
# the camera has perspective, so the upper edges are NOT reliably deployable -> we
# only use the side corners and the start of the lower edges.
RIM_L, RIM_R, RIM_Y, K = 100.0, 1103.0, 275.0, 0.68
CENTER_X = 602.0
EDGE_Y = {'UL': 14.0, 'UR': 14.0, 'LL': 420.0, 'LR': 420.0}
UI_BOXES = [(0, 0, 185, 165), (530, 0, 675, 58), (985, 0, 1200, 110),
            (50, 375, 185, 430), (980, 340, 1160, 430)]


def in_ui(x, y):
    return any(a <= x <= c and b <= y <= d for a, b, c, d in UI_BOXES)


def rim_x(y, side):
    d = abs(y - RIM_Y) / K
    return RIM_L + d if side == 'L' else RIM_R - d


class Zone:
    """No-deploy info for this battle (red outline mask), or None."""

    def __init__(self, red=None):
        self.red = red
        self.near = cv2.dilate(red, np.ones((13, 13), np.uint8)) if red is not None else None

    def valid(self, x, y, side):
        if not (14 <= y <= 425) or in_ui(x, y):
            return False
        rx = rim_x(y, side)
        if (side == 'L' and x < rx + 8) or (side == 'R' and x > rx - 8):
            return False
        if self.red is None:
            return True
        xi, yi = int(x), int(y)
        if self.near[yi, xi]:
            return False
        xa, xb = sorted((int(rx), xi))
        # nothing red between the point and the rim -> point is outside every red zone
        return not self.red[max(0, yi - 1):yi + 2, max(0, xa):xb + 1].any()


class Pts:
    def __init__(self, min_d=11):
        self.used = []
        self.min_d = min_d

    def ok(self, p):
        return all((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 >= self.min_d ** 2 for q in self.used)

    def add(self, gen, tries=40):
        for _ in range(tries):
            p = gen()
            if p is not None and self.ok(p):
                self.used.append(p)
                return p
        return None


def reveal_red(img0, th_pos):
    """Tap the TH (never deployable) so the game draws the red no-deploy outline."""
    tap(th_pos[0] + random.uniform(-5, 5), th_pos[1] + random.uniform(-5, 5))
    rs(0.2, 0.35)
    img1 = cap()
    d = np.abs(img0.astype(np.int16) - img1.astype(np.int16)).sum(axis=2)
    b, g, r = [img1[:, :, i].astype(np.int16) for i in range(3)]
    red = ((r > 150) & (g < 125) & (b < 105) & (r - g > 70) & (d > 40)).astype(np.uint8)
    red[432:] = 0
    n, lab, st, _ = cv2.connectedComponentsWithStats(red)
    keep = np.zeros(n, bool)
    keep[1:] = st[1:, cv2.CC_STAT_AREA] >= 6
    red = keep[lab].astype(np.uint8)
    cnt = int(red.sum())
    if cnt < 250:
        log(f'red outline not found ({cnt}px) -> static band')
        return Zone(None), img1
    return Zone(cv2.dilate(red, np.ones((3, 3), np.uint8))), img1


# ---------------------------------------------------------------- deploy (one corner)
HOLD_RADIUS = 10
HOLD_SECS = {'kane': 6.0, 'edrag': 2.0}  # hold deploys ~7.5 troops/s


def pick_anchor(zone, side):
    """Random point near the side corner (slightly down the lower edge) whose whole
    drift circle is deployable."""
    e = 'L' + side
    for i in range(200):
        t = random.uniform(0.03, 0.22 + i * 0.002)
        y = RIM_Y + (EDGE_Y[e] - RIM_Y) * t
        d = random.uniform(16, 30)
        x = rim_x(y, side) + (d if side == 'L' else -d)
        ring = [(x + HOLD_RADIUS * math.cos(a), y + HOLD_RADIUS * 0.8 * math.sin(a))
                for a in np.linspace(0, 2 * math.pi, 12, endpoint=False)]
        if zone.valid(x, y, side) and all(zone.valid(px, py, side) for px, py in ring):
            return x, y
    log('no clean anchor found; using default corner point')
    return (rim_x(285, side) + (22 if side == 'L' else -22), 285.0)


def near(anchor, r=HOLD_RADIUS):
    a, k = random.uniform(0, 2 * math.pi), random.uniform(0.3, 1.0) * r
    return anchor[0] + k * math.cos(a), anchor[1] + 0.8 * k * math.sin(a)


def hold_cmds(anchor, dur):
    """One continuous press around the anchor for ~dur s; the finger drifts in small
    random steps (bounded to HOLD_RADIUS) so troops never share a point."""
    x, y = near(anchor, HOLD_RADIUS * 0.6)
    cmds = [f'input motionevent DOWN {x:.0f} {y:.0f}']
    t = 0.0
    while t < dur:
        d = random.uniform(0.03, 0.07)
        nx, ny = x + random.uniform(-2.5, 2.5), y + random.uniform(-2.0, 2.0)
        if math.hypot(nx - anchor[0], (ny - anchor[1]) / 0.8) <= HOLD_RADIUS:
            x, y = nx, ny
        cmds += [f'sleep {d:.3f}', f'input motionevent MOVE {x:.0f} {y:.0f}']
        t += d + 0.06  # ~60ms per input call on the phone
    cmds.append(f'input motionevent UP {x:.0f} {y:.0f}')
    return cmds


def tap_cmds(x, y, lo=0.15, hi=0.2):
    return [f'input tap {x:.0f} {y:.0f}', f'sleep {random.uniform(lo, hi):.3f}']


def spell_pt(sp, th):
    th_x, th_y = th
    return sp.add(lambda: (min(1040, max(190, th_x + random.uniform(-85, 85))),
                           min(410, max(60, th_y + random.uniform(-65, 65)))))


def deploy(img, th, zone):
    cards = find_cards(img)
    log('cards:', {k: int(v[0]) for k, v in cards.items()})
    th_pos = th[2]
    if abs(th_pos[0] - CENTER_X) < 40:
        side = random.choice('LR')
    else:
        side = 'L' if th_pos[0] < CENTER_X else 'R'
    anchor = pick_anchor(zone, side)
    pts, sp = Pts(min_d=7), Pts(min_d=38)
    cmds, marks = [], []

    def pick(card):
        cmds.extend(tap_cmds(*card_tap_pt(cards[card][0])))

    # 1) heroes: single taps at distinct points around the anchor
    heroes = [h for h in HEROES if h in cards]
    random.shuffle(heroes)
    for h in heroes:
        pick(h)
        p = pts.add(lambda: near(anchor, 16))
        cmds.extend(tap_cmds(*p))
        marks.append(p)
    # 2) troops: press-and-hold around the anchor
    for c in ('kane', 'edrag'):
        if c in cards:
            pick(c)
            cmds.extend(hold_cmds(anchor, HOLD_SECS[c] * random.uniform(0.95, 1.15)))
            cmds.append(f'sleep {random.uniform(0.15, 0.25):.3f}')
    for c in cards:
        if c in BODY_NAMES or c.startswith('utroop'):
            pick(c)
            p = pts.add(lambda: near(anchor, 16))
            cmds.extend(tap_cmds(*p))
            marks.append(p)
    # 3) spells around the TH / core
    for c in cards:
        if is_spell(c):
            pick(c)
            for _ in range({'freeze': 3, 'ccspell': 3}.get(c, 1)):
                p = spell_pt(sp, th_pos)
                cmds.extend(tap_cmds(*p))
                marks.append(p)
    t0 = time.time()
    if (T.get('surrender') is None or load('dmg0') is None) and len(cmds) > 4:
        run_cmds(cmds[:4])  # first hero only -> 'Surrender' + 'Overall Damage 0%' visible
        rs(0.4, 0.6)
        b = cap()
        save_tpl('surrender', b[388:418, 65:170])
        save_tpl('dmg0', b[393:419, 1098:1142])
        cmds = cmds[4:]
    run_cmds(cmds)
    log(f'deployed in {time.time() - t0:.1f}s side={side} anchor=({anchor[0]:.0f},{anchor[1]:.0f}) '
        f'red={zone.red is not None}')
    return cards, time.time(), marks + [anchor], side, anchor


def fixup(cards, zone, th_pos, anchor):
    """Redeploy/cast anything left over."""
    tries = {}
    for attempt in range(4):
        rs(0.8, 1.6)
        img = cap()
        if state(img)[0] != 'battle':
            return
        left = []
        for c, (cx, _) in cards.items():
            if card_sat(img, cx) <= 25:
                continue
            if (c in HEROES or c in BODY_NAMES or c.startswith('utroop')) and hero_deployed(img, cx):
                continue
            tries[c] = tries.get(c, 0) + 1
            if c.startswith('u') and tries[c] > 2:  # unknown card that never empties
                continue
            left.append(c)
        if not left:
            return
        log('leftover cards:', left)
        sp = Pts(min_d=38)
        cmds = []
        for c in left:
            cmds.extend(tap_cmds(*card_tap_pt(cards[c][0])))
            if is_spell(c):
                cmds.extend(tap_cmds(*spell_pt(sp, th_pos)))
            elif c in HEROES or c in BODY_NAMES:
                cmds.extend(tap_cmds(*near(anchor, 16)))
            else:
                cmds.extend(hold_cmds(anchor, random.uniform(2.0, 3.0)))
                cmds.append(f'sleep {random.uniform(0.15, 0.25):.3f}')
        run_cmds(cmds)


def abilities(cards, t_deploy):
    order = [h for h in ('king', 'queen', 'rc') if h in cards]
    random.shuffle(order)
    for h in order:
        tap(*card_tap_pt(cards[h][0]))
        rs(0.3, 0.8)
    if 'warden' in cards:  # warden ability >= 5s after deploy
        wait = 5 + random.uniform(0.3, 1.5) - (time.time() - t_deploy)
        if wait > 0:
            time.sleep(wait)
        tap(*card_tap_pt(cards['warden'][0]))


def save_plan(img, marks, name):
    vis = img.copy()
    for x, y in marks:
        if y < 440:
            cv2.circle(vis, (int(x), int(y)), 3, (255, 0, 255), -1)
    cv2.imwrite(os.path.join(LOG, 'plan', name), cv2.resize(vis, None, fx=0.75, fy=0.75),
                [cv2.IMWRITE_JPEG_QUALITY, 70])


# ---------------------------------------------------------------- battle flow
def monitor(t_start):
    saved_late = False
    while True:
        rs(1.8, 3.2)
        img = cap()
        st, m = state(img)
        el = time.time() - t_start
        if st == 'result':
            rs(2.5, 3.5)  # let the star/percent counters finish
            return cap()
        if st == 'battle':
            s = match_at(img, 'speed1x', thr=0.85) if T.get('speed1x') is not None else None
            if s:
                log('pressing 1X speed button')
                tap_box(s[0], s[1], s[2], s[3])
            elif el > 130 and not saved_late:
                cv2.imwrite(os.path.join(LOG, 'unk', f'late_{ts()}.png'), img)
                saved_late = True
        elif st in ('home', 'menu', 'army'):
            log('monitor: unexpected state', st)
            return img
        if el > 260:
            log('monitor timeout')
            return img


_d0 = load('dmg0')
DMG0 = (_d0.min(axis=2) > 200).astype(np.float32) if _d0 is not None else None


def damage_zero(img):
    """'Overall Damage 0%' (white text, right aligned) with no digit before the 0."""
    if DMG0 is None:
        return False
    roi = (img[386:426, 1085:1155].min(axis=2) > 200).astype(np.float32)
    r = cv2.matchTemplate(roi, DMG0, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(r)
    if mx < 0.75:
        return False
    x = 1085 + loc[0]
    cols = np.where((img[392:420, x - 40:x + 45].min(axis=2) > 200).sum(axis=0) >= 2)[0]
    return len(cols) > 0 and x - 40 + cols[0] >= x + 3  # nothing white left of the '0'


def fresh_battle(img):
    """Battle view with nothing deployed yet: 'End Battle' (not Surrender) and 0% damage."""
    e, s = match_at(img, 'endbattle', thr=0), match_at(img, 'surrender', thr=0)
    if e is None:
        return False
    return e[4] > 0.9 and (s is None or e[4] > s[4] + 0.1) and damage_zero(img)


def attack_or_next(img, started=False):
    rs(0.6, 1.8)
    img = cap()
    res = detect_th(img)
    (s1, lv, pos), (s2, lv2, _) = res[0], res[1]
    crop = img[max(0, pos[1] - 50):pos[1] + 50, max(0, pos[0] - 50):pos[0] + 50]
    cv2.imwrite(os.path.join(LOG, 'th', f'{ts()}_th{lv}_{s1:.3f}.png'), crop)
    go = lv >= MIN_TH and s1 >= 0.90
    log(f'TH{lv} score={s1:.3f} (2nd TH{lv2} {s2:.3f}) at {pos} -> {"ATTACK" if go else "next"}')
    if not go:
        stats['skipped'] += 1
        stats['progress'] = time.time()
        rs(0.4, 1.6)
        m = match_at(img, 'endbattle' if started else 'next')
        if m:
            if started:
                log('scout time over and TH too low -> End Battle (nothing deployed)')
            tap_box(m[0], m[1], m[2], m[3])
        rs(2.5, 4.0)
        return
    zone, img_red = reveal_red(img, pos)
    cards, t_dep, marks, side, anchor = deploy(img, res[0], zone)
    abilities(cards, t_dep)
    fixup(cards, zone, pos, anchor)
    if stats['battles'] < 30:
        save_plan(img_red, marks, f'{ts()}_th{lv}.jpg')
    img = monitor(t_dep)
    stats['battles'] += 1
    stats['progress'] = time.time()
    small = cv2.resize(img, None, fx=0.5, fy=0.5)
    cv2.imwrite(os.path.join(LOG, 'res', f'{ts()}_th{lv}.jpg'), small, [cv2.IMWRITE_JPEG_QUALITY, 70])
    hrs = (time.time() - stats['start']) / 3600
    log(f'battle done #{stats["battles"]} (skipped {stats["skipped"]}) {hrs:.2f}h')
    if os.environ.get('ONCE'):
        open(STOP_FILE, 'w').close()


def restart_app():
    log('restarting app')
    sh(f'am force-stop {PKG}')
    rs(2, 4)
    sh(f'monkey -p {PKG} -c android.intent.category.LAUNCHER 1')
    rs(20, 30)


def single_instance():
    pidf = os.path.join(LOG, 'bot.pid')
    try:
        old = int(open(pidf).read())
        os.kill(old, 0)
        if old != os.getpid():
            log(f'another bot is running (pid {old}); exiting')
            sys.exit(1)
    except (OSError, ValueError):
        pass
    open(pidf, 'w').write(str(os.getpid()))


def main():
    single_instance()
    unknown_since = None
    unk_saved = False
    log(f'bot start MIN_TH={MIN_TH}')
    while True:
        img = cap()
        st, m = state(img)
        if st == 'home' and os.path.exists(STOP_FILE):  # only stop at a safe point
            log('STOP file found at home screen, exiting')
            return
        if st != 'unknown':
            unknown_since, unk_saved = None, False
        if time.time() - stats['progress'] > 900:  # watchdog: nothing happened for 15 min
            cv2.imwrite(os.path.join(LOG, 'unk', f'watchdog_{ts()}.png'), img)
            log('watchdog: no progress for 15 min')
            restart_app()
            stats['progress'] = time.time()
            continue
        if st == 'home':
            rs(0.5, 2.0)
            tap_box(m[0], m[1], m[2], m[3])
            rs(1.2, 2.2)
        elif st == 'menu':
            rs(0.3, 1.0)
            tap_box(m[0], m[1], m[2], m[3])
            rs(1.2, 2.2)
        elif st == 'army':
            rs(0.4, 1.2)
            tap_box(m[0], m[1], m[2], m[3])
            rs(3.0, 5.0)
        elif st == 'scout':
            attack_or_next(img)
        elif st == 'battle':
            rs(0.8, 1.4)  # the Next button slides in after End Battle; look again
            img = cap()
            st, m = state(img)
            if st == 'scout':
                attack_or_next(img)
            elif st == 'battle' and fresh_battle(img):
                log('battle view with nothing deployed -> treating as scout')
                attack_or_next(img, started=True)
            elif st == 'battle':
                log('battle already in progress -> monitoring')
                monitor(time.time())
        elif st == 'result':
            rs(1.0, 3.0)
            tap_box(m[0], m[1], m[2], m[3])
            rs(3.5, 5.5)
        else:
            now = time.time()
            if unknown_since is None:
                unknown_since = now
            dur = now - unknown_since
            if dur > 20 and not unk_saved:
                p = os.path.join(LOG, 'unk', f'unk_{ts()}.png')
                cv2.imwrite(p, img)
                log('unknown state for 20s, saved', p)
                unk_saved = True
            if dur > 300:  # disconnect ("another device") etc: wait 5 min then reload
                restart_app()
                unknown_since, unk_saved = None, False
            rs(2.0, 4.0)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
