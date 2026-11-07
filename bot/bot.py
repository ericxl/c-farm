#!/usr/bin/env python3
"""Clash of Clans multiplayer battle loop (home village, TH >= MIN_TH only).

Loop: home -> Attack -> Find a Match -> army Attack! -> scout (detect TH level)
      -> Next if TH < MIN_TH, else deploy everything at one corner -> monitor -> Return Home.

Deploy: heroes as single taps at distinct points near a randomly chosen, red-zone-checked
corner anchor; troops by press-and-hold with the finger drifting a few px; spells on the TH.
Every tap position and wait is randomized. Screen is the 1200x540 `wm size` override.

Walls (only with --upgrade-walls): between battles, upgrade the lowest-level walls through the
Builder Menu while a builder is free and gold/elixir covers the price. Without the flag the bot
only battles, piling up loot.

Run:   bot/.venv/bin/python -u bot/bot.py [--upgrade-walls] >> /tmp/coc-bot/bot.log 2>&1 &
Stop:  touch /tmp/coc-bot/STOP   (exits at the next home screen)
Env:   MIN_TH (default 16), ONCE=1 (stop after one battle), COC_BOT_LOG (default /tmp/coc-bot)
"""
import os, sys, glob, math, time, random, subprocess, datetime
import cv2, numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(BASE, 'tpl')
TH_REF = os.path.join(BASE, '..', 'townhalls.png')
LOG = os.environ.get('COC_BOT_LOG', '/tmp/coc-bot')
for d in ('th', 'res', 'unk', 'plan'):
    os.makedirs(os.path.join(LOG, d), exist_ok=True)

MIN_TH = int(os.environ.get('MIN_TH', 16))
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
class DeviceLost(Exception):
    """The phone dropped off adb for a while. Whatever sequence was running is stale: abort it
    and let the main loop look at the screen again (never replay queued taps)."""


def cap(wait=False):
    """Screenshot; survives adb/USB drops by waiting and reconnecting. After an outage longer
    than ~20s, raise DeviceLost (unless wait=True, used only by the main loop)."""
    tries, t0, portrait_since = 0, None, None
    while True:
        try:
            out = subprocess.run(['adb', 'exec-out', 'screencap', '-p'],
                                 capture_output=True, timeout=20).stdout
            img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR) if out else None
            # a portrait frame = the game is not in front (closed/crashed, phone on its home
            # screen): screenshots work but would never turn landscape again on their own
            if img is not None and img.shape[:2] == (1200, 540):
                portrait_since = portrait_since or time.time()
                # a lock screen (phone rebooted after its battery died, 2026-10-11) stays
                # portrait forever: wake it and swipe it away (no PIN on this phone) first
                if (time.time() - portrait_since > 30
                        and time.time() - stats.get('unlocked', 0) > 60):
                    stats['unlocked'] = time.time()
                    log('portrait screen: waking the phone and swiping the lock screen away')
                    subprocess.run(['adb', 'shell', 'input keyevent 224; sleep 0.8; '
                                    'input swipe 270 1000 270 300 300'],
                                   capture_output=True, timeout=30)
                if (time.time() - portrait_since > random.uniform(100, 140)
                        and time.time() - stats.get('launched', 0) > 120):
                    stats['launched'] = stats['outage'] = time.time()
                    log('the game is not in front (portrait screen); launching Clash of Clans')
                    subprocess.run(['adb', 'shell', 'monkey', '-p', PKG, '-c',
                                    'android.intent.category.LAUNCHER', '1'],
                                   capture_output=True, timeout=30)
            else:
                portrait_since = None
            if img is not None and img.shape[1] == 1200:
                if t0 is not None and time.time() - t0 > 20:
                    log(f'device back after {time.time() - t0:.0f}s')
                    stats['outage'] = time.time()
                    if not wait:
                        raise DeviceLost()
                return img
        except subprocess.TimeoutExpired as e:
            if tries % 10 == 0:
                log('cap error', str(e).splitlines()[0][:120])
        t0 = t0 or time.time()
        tries += 1
        if tries % 10 == 0:
            log(f'screencap failing for {tries} tries; reconnecting adb')
            try:
                subprocess.run(['adb', 'reconnect'], capture_output=True, timeout=30)
                subprocess.run(['adb', 'wait-for-device'], capture_output=True, timeout=300)
            except subprocess.TimeoutExpired:
                log('device still not back; will keep retrying')
        time.sleep(min(60, 2 * tries))


def device_ok():
    try:
        r = subprocess.run(['adb', 'get-state'], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == 'device'
    except subprocess.TimeoutExpired:
        return False


def sh(cmd, timeout=120):
    """Run an adb shell command, but never queue input for a phone that is not connected
    (adb would deliver it much later, onto whatever screen is showing then)."""
    if not device_ok():
        raise DeviceLost()
    t = time.time()
    try:
        subprocess.run(['adb', 'shell', cmd], timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise DeviceLost()
    if time.time() - t > timeout * 0.9:
        raise DeviceLost()


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


T = {n: load(n) for n in ['findmatch', 'army_attack', 'next',
                          'endbattle', 'surrender', 'return', 'speed1x', 'conn_lost', 'reload',
                          'search_fail_home', 'searching']}
HEROES = ['king', 'queen', 'warden', 'rc']

# where each UI template normally sits (x0, y0) -> searched in a +-pad window
UI_POS = {'findmatch': (105, 367), 'army_attack': (890, 470),
          'next': (995, 352), 'endbattle': (65, 388), 'surrender': (65, 388),
          'return': (542, 440), 'speed1x': None, 'search_fail_home': (66, 436),
          'searching': (432, 282)}


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


HOME_TEXTS = [load('home_text'), load('home_text2')]  # button label: 'Attack!' (old), 'Battle'
HOME_ICON = load('home_icon')  # the button's icon (event skins change it: map, chest, sword)
HOME_DE = load('home_de')  # dark elixir drop in the top-right resource bar


def home_text_score(reg):
    return max(float(cv2.matchTemplate(reg, t, cv2.TM_CCOEFF_NORMED).max()) for t in HOME_TEXTS)


def home_resources(img):
    """Dark elixir row in the top-right bar: home village only (the builder base has none).
    Home evidence that survives event re-skins of the button icon."""
    return float(cv2.matchTemplate(img[60:150, 1080:1180], HOME_DE, cv2.TM_CCOEFF_NORMED).max()) > 0.8


def home_button(img):
    """Home-village Battle button: its label plus the current icon or the dark elixir row (the
    builder base has the same kind of button, but no dark elixir)."""
    reg = img[425:540, 40:190]
    if home_text_score(reg) < 0.9:
        return None
    if cv2.matchTemplate(reg, HOME_ICON, cv2.TM_CCOEFF_NORMED).max() < 0.45 and not home_resources(img):
        return None
    return (110.0, 482.0, 70, 60, 1.0)


def state(img):
    for n, st in [('return', 'result'), ('next', 'scout'), ('endbattle', 'battle'),
                  ('surrender', 'battle'), ('army_attack', 'army'),
                  ('findmatch', 'menu')]:
        m = match_at(img, n)
        if m:
            return st, m
    m = home_button(img)
    if m:
        return 'home', m
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
        # The smallest scale is for a fully zoomed-out camera: after the wall flow's zoom_out the
        # next scout opens ~6% smaller than usual, and TH17s there scored 0.84 and were skipped.
        scales = (0.26, 0.28, 0.30, 0.32)
        if lv == 17:
            # TH17 is drawn smaller in townhalls.png than in-game: at 0.28-0.32 it never matched
            # (every TH17 base was skipped as 'no TH'). Its edge pixels carry the white
            # background, so trim the mask too (0.88 -> 0.92 on a real TH17).
            scales = (0.33, 0.35, 0.36, 0.37)
            mask = cv2.erode(mask, np.ones((5, 5), np.uint8), iterations=2)
        refs[lv] = [(cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_AREA),
                     cv2.resize(mask, None, fx=s, fy=s, interpolation=cv2.INTER_NEAREST))
                    for s in scales]
    return refs


REFS = build_th_refs()


def detect_th(img, peaks=3):
    """[(score, level, (x, y)), ...] best first: the top `peaks` spots per level, >= 40px apart.
    One spot per level is not enough: a fake 'TH18' in the scenery beat the real TH18 by 0.001."""
    x0, y0, x1, y1 = 150, 0, 1060, 435
    roi = img[y0:y1, x0:x1]
    res = []
    for lv, lst in REFS.items():
        cand = []
        for tt, mm in lst:
            r = cv2.matchTemplate(roi, tt, cv2.TM_CCORR_NORMED, mask=mm)
            r = np.nan_to_num(r, nan=-1, posinf=-1, neginf=-1)
            for _ in range(peaks):
                _, mx, _, (lx, ly) = cv2.minMaxLoc(r)
                cand.append((mx, (x0 + lx + tt.shape[1] // 2, y0 + ly + tt.shape[0] // 2)))
                r[max(0, ly - 35):ly + 35, max(0, lx - 35):lx + 35] = -1
        kept = []
        for s, p in sorted(cand, reverse=True):
            if len(kept) < peaks and all(max(abs(p[0] - q[0]), abs(p[1] - q[1])) >= 40 for _, q in kept):
                kept.append((s, p))
        res += [(s, lv, p) for s, p in kept]
    res.sort(reverse=True)
    return res


def th_shape(crop, lv):
    """Second opinion on a TH match: mean-subtracted correlation around the match.

    TM_CCORR_NORMED ignores brightness offsets, so a plain patch of grass, dirt or dark
    scenery can reach 0.90 against a TH template and got attacked as a 'TH16'. With the mean
    removed those patches score 0.28-0.46, real Town Halls (even half covered by the scout
    timer) 0.55+, confident ones 0.70+."""
    best = -1.0
    for tt, mm in REFS[lv]:
        if tt.shape[0] > crop.shape[0] or tt.shape[1] > crop.shape[1]:
            continue
        r = cv2.matchTemplate(crop, tt, cv2.TM_CCOEFF_NORMED, mask=mm)
        best = max(best, float(np.nan_to_num(r, nan=-1, posinf=-1, neginf=-1).max()))
    return best


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
            if 55 <= x - s <= 92:  # a selected/deployed neighbour's glow can fill the gap
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
        # heroes and the siege card animate/change a little: King 0.78, swaying Blimp 0.76-0.79
        if mx > (0.75 if n in HEROES or n == 'siege' else 0.8):
            hits.append((mx, n, loc[0] + t.shape[1] / 2))
    found = {}
    for mx, n, cx in sorted(hits, reverse=True):  # one name per slot
        if all(abs(cx - v[0]) > 30 for v in found.values()):
            found[n] = (cx, 488.0)
    hx = [found[h][0] for h in HEROES if h in found]
    last_hero = max(hx) if hx else 700
    # cards form one contiguous row (~72-82px apart): an extra slot only counts if it is next
    # to a known card, otherwise it is scenery behind the bar (e.g. a big event logo)
    # same appearance-based finder as the in-battle sweep (bar_slots alone missed cards)
    runs = card_runs(img)
    spell_x = max([found[n][0] for n in found if is_spell(n)], default=1e9)
    snapped = [card_body(img, x, runs) for x in bar_candidates(img)]
    merged_at = {round(b[0]) for b in snapped if b is not None and b[1]}
    slots = sorted(set(bar_slots(img)) | {b[0] for b in snapped if b is not None})
    added = True
    while added:  # repeat: a slot may only become adjacent once its neighbour was added
        added = False
        for cx in slots:
            if not all(abs(cx - v[0]) > 30 for v in found.values()):
                continue
            if not any(abs(cx - v[0]) < 95 for v in found.values()):
                continue
            no_badge = level_badge(img, cx) < 0.05 and not dark_badge(img, cx)
            if no_badge and (round(cx) not in merged_at or cx > spell_x + 30):
                continue  # empty dashed slot: no level badge at all
            found[('uspell@%d' if cx > last_hero else 'utroop@%d') % cx] = (cx, 488.0)
            added = True
    return found


def badge_at(img, x):
    """Black level badge with white digits whose left edge is at x (rows 511-524, 16px wide)."""
    reg = img[511:524, int(x):int(x) + 16].astype(np.int16)
    return ((reg.max(axis=2) < 70).mean() > 0.45
            and 0.04 < (reg.min(axis=2) > 200).mean() < 0.45)


def dark_badge(img, cx):
    """Black level badge (heroes, sieges, the x50 mega troop, event troops). The slot centre
    can be off by several px (a selected card's glow widens its run), so look around."""
    return any(badge_at(img, cx - 28 + d) for d in range(-10, 11, 2))


def level_badge(img, cx):
    """Share of the light-blue level badge in a card's bottom-left corner."""
    reg = img[500:524, int(cx) - 34:int(cx) - 12]
    hsv = cv2.cvtColor(reg, cv2.COLOR_BGR2HSV)
    m = (hsv[:, :, 0] >= 95) & (hsv[:, :, 0] <= 115) & (hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 150)
    return float(m.mean())


def is_spell(name):
    return name in SPELL_NAMES or name.startswith('uspell')


def card_sat(img, cx):
    hsv = cv2.cvtColor(img[460:520, int(cx) - 25:int(cx) + 25], cv2.COLOR_BGR2HSV)
    return hsv[:, :, 1].mean()


def card_tap_pt(cx):
    # upper-middle of card: avoids the warden/siege toggle and the level badge
    return cx + random.uniform(-12, 12), random.uniform(462, 482)


def hero_deployed(img, cx):
    # deployed heroes/sieges get a health bar above the card
    strip = img[428:441, int(cx) - 25:int(cx) + 25].astype(np.int16)
    b, g, r = strip[:, :, 0], strip[:, :, 1], strip[:, :, 2]
    m = (g > 195) & (b < 60) & (r < 160) & (g - r > 80)
    if m.mean(axis=1).max() > 0.2:
        return True
    # a damaged unit's bar turns yellow, then red, and only its left part stays filled. Missing
    # that re-tapped a live Battle Blimp, which makes it drop its Clan Castle troops early.
    s = img[426:444, int(cx) - 25:int(cx) + 25].astype(np.int16)
    b, g, r = s[:, :, 0], s[:, :, 1], s[:, :, 2]
    # any warm fill from green through yellow and orange to red (the frame under an orange
    # or red bar is tinted too, so only the black line above it is required)
    fill = (b < 70) & (np.maximum(r, g) > 150) & (np.maximum(r, g) - b > 110)
    dark = (s.max(axis=2) < 50).mean(axis=1) > 0.7  # the bar's black top frame line
    for y in range(2, 16):
        if fill[y, :8].mean() > 0.6 and dark[max(0, y - 5):y].any():
            return True
    # nearly dead: only a 2px sliver is filled, but the bar's frame (a black line with a
    # bright highlight line right under it, across the whole card) is still there
    # (rows 426-437 only: the selected card's own top border, a black line over pure white at
    # y~440, looks the same; the bar's highlight is pale teal, R~155, not white)
    s = img[426:438, int(cx) - 22:int(cx) + 22].astype(np.int16)
    dark = (s.max(axis=2) < 50).mean(axis=1) > 0.8
    light = ((s.min(axis=2) > 140) & (s[:, :, 2] < 205)).mean(axis=1) > 0.8
    return any(dark[y] and light[y + 1] for y in range(len(dark) - 1))


# ---------------------------------------------------------------- map geometry
# Max-zoomed-out battle view. Side corners of the map rim at (100,275) / (1103,275);
# the camera has perspective, so the upper edges are NOT reliably deployable -> we
# only use the side corners and the start of the lower edges.
RIM_L, RIM_R, RIM_Y, K = 100.0, 1103.0, 275.0, 0.68
CENTER_X = 602.0
EDGE_Y = {'UL': 14.0, 'UR': 14.0, 'LL': 420.0, 'LR': 420.0}
UI_BOXES = [(0, 0, 185, 165), (530, 0, 675, 58), (985, 0, 1200, 110), (180, 375, 500, 432),
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
    """Spell drop around the TH, kept above the battle's boost buttons (y 383-420). Never None:
    if spacing can't be satisfied (TH low on screen squeezes the area) take any point."""
    th_x, th_y = th
    cy = min(th_y, 320)  # low TH: aim at the base above it rather than a flat line at the cap

    def gen():
        return (min(1040, max(190, th_x + random.uniform(-85, 85))),
                min(365, max(60, cy + random.uniform(-65, 65))))
    return sp.add(gen) or gen()


def deploy(img, th, zone):
    cards = find_cards(img)
    log('cards:', {k: int(v[0]) for k, v in cards.items()})
    stats['scout_img'] = img
    if 'siege' not in cards and stats.get('nosiege', 0) < 4:  # learn the template's blind spot
        stats['nosiege'] = stats.get('nosiege', 0) + 1
        cv2.imwrite(os.path.join(LOG, 'unk', f'nosiege_{ts()}.png'), img)
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
        if c in BODY_NAMES:
            # short hold: drops every unit on the card (e.g. 3 blimps), still one for a CC
            pick(c)
            cmds.extend(hold_cmds(anchor, random.uniform(0.7, 1.0)))
            cmds.append(f'sleep {random.uniform(0.15, 0.25):.3f}')
            marks.append(anchor)
    t0 = time.time()
    if (T.get('surrender') is None or load('dmg0') is None) and len(cmds) > 4:
        run_cmds(cmds[:4])  # first hero only -> 'Surrender' + 'Overall Damage 0%' visible
        rs(0.4, 0.6)
        b = cap()
        save_tpl('surrender', b[388:418, 65:170])
        save_tpl('dmg0', b[393:419, 1098:1142])
        cmds = cmds[4:]
    run_cmds(cmds)
    # unknown troops (any count): short holds, re-checking until the card is used up
    for c in [c for c in cards if c.startswith('utroop')]:
        cx = cards[c][0]
        for _ in range(10):  # ~7 units/s: a x50 card needs ~7s of holding
            run_cmds(tap_cmds(*card_tap_pt(cx)) + hold_cmds(anchor, random.uniform(1.0, 1.4)))
            rs(0.15, 0.25)
            im = cap()
            # a siege machine the card templates missed never greys out once deployed; it shows
            # a health bar instead, and tapping it again would release its Clan Castle troops
            if card_sat(im, cx) <= 25 or hero_deployed(im, cx):
                break
        marks.append(anchor)
    # spells around the TH: cast one at a time until the card is used up (x1 ... x5)
    for c in [c for c in cards if is_spell(c)]:
        cx = cards[c][0]
        run_cmds(tap_cmds(*card_tap_pt(cx)))
        for _ in range(7):
            p = spell_pt(sp, th_pos)
            run_cmds(tap_cmds(*p, lo=0.2, hi=0.3))
            marks.append(p)
            if card_sat(cap(), cx) <= 25:
                break
    log(f'deployed in {time.time() - t0:.1f}s side={side} anchor=({anchor[0]:.0f},{anchor[1]:.0f}) '
        f'red={zone.red is not None}')
    return cards, time.time(), marks + [anchor], side, anchor


def fixup(cards, zone, th_pos, anchor):
    """Redeploy/cast anything left over."""
    tries = {}
    for attempt in range(4):
        rs(0.8, 1.6)
        img = cap()
        if state(img)[0] != 'battle' or connection_lost(img):
            return  # the dimmed battle behind a 'Connection lost' dialog still reads as 'battle'
        if pick_reward(img):  # the popup dims the bar: health bars would look absent
            continue
        left, where = [], {c: card_x(img, cards, c) for c in cards}
        for c, cx in where.items():
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
        if any(c in BODY_NAMES for c in left) and stats.get('bodyshots', 0) < 5:
            stats['bodyshots'] = stats.get('bodyshots', 0) + 1  # why does the siege look undeployed?
            cv2.imwrite(os.path.join(LOG, 'unk', f'leftover_{ts()}.png'), img)
        if attempt >= 1 and any(not is_spell(c) for c in left):
            # the same spot failed twice: the corner is probably walled in -> move
            side = 'R' if anchor[0] < CENTER_X else 'L'
            if attempt >= 2:
                side = random.choice('LR')
            anchor = pick_anchor(zone, side)
            log(f'fixup: trying another spot ({side}) at ({anchor[0]:.0f},{anchor[1]:.0f})')
        sp = Pts(min_d=38)
        cmds = []
        for c in left:
            cmds.extend(tap_cmds(*card_tap_pt(where[c])))
            if is_spell(c):
                cmds.extend(tap_cmds(*spell_pt(sp, th_pos)))
            elif c in HEROES or c in BODY_NAMES:
                cmds.extend(tap_cmds(*near(anchor, 16)))
            else:
                cmds.extend(hold_cmds(anchor, random.uniform(2.0, 3.0)))
                cmds.append(f'sleep {random.uniform(0.15, 0.25):.3f}')
        run_cmds(cmds)


def card_x(img, cards, name):
    """Where a card is now: bonus cards inserted mid-battle shift the whole bar, so named cards
    are looked up again (the scout-time position is the fallback, e.g. for greyed cards)."""
    t = CARDS.get(name)
    if t is not None:
        _, mx, _, loc = cv2.minMaxLoc(cv2.matchTemplate(img[440:540], t, cv2.TM_CCOEFF_NORMED))
        if mx > 0.75:
            return loc[0] + t.shape[1] / 2
    return cards[name][0]


def abilities(cards, t_deploy):
    order = [h for h in ('king', 'queen', 'rc') if h in cards]
    random.shuffle(order)
    img = cap()
    for h in order:
        tap(*card_tap_pt(card_x(img, cards, h)))
        rs(0.3, 0.8)
    if 'warden' in cards:  # warden ability >= 5s after deploy
        wait = 5 + random.uniform(0.3, 1.5) - (time.time() - t_deploy)
        if wait > 0:
            time.sleep(wait)
        tap(*card_tap_pt(card_x(cap(), cards, 'warden')))


def save_plan(img, marks, name):
    vis = img.copy()
    for x, y in marks:
        if y < 440:
            cv2.circle(vis, (int(x), int(y)), 3, (255, 0, 255), -1)
    cv2.imwrite(os.path.join(LOG, 'plan', name), cv2.resize(vis, None, fx=0.75, fy=0.75),
                [cv2.IMWRITE_JPEG_QUALITY, 70])


# ---------------------------------------------------------------- battle flow
def speed_button(img):
    """Green '1x' button that appears on the right late in a battle (tap -> faster)."""
    t = T.get('speed1x')
    if t is None:
        return None
    r = cv2.matchTemplate(img[150:440, 950:1200], t, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(r)
    if mx < 0.85:
        return None
    h, w = t.shape[:2]
    return (950 + loc[0] + w / 2, 150 + loc[1] + h / 2, w, h, mx)


def card_runs(img):
    """Card bodies in the troop bar as (start, end, merged): runs of non-dark columns. A
    selected card's glow or a bright neighbour can fill the dark gaps, merging 2-4 cards
    into one run (227px = baby dragon + siege + King); split those by the ~73px card pitch."""
    hsv = cv2.cvtColor(img[445:535], cv2.COLOR_BGR2HSV)
    gap = (hsv[:, :, 2] < 60).mean(axis=0) > 0.5

    def forest(a, b):
        """Empty dashed slot: almost all forest green behind the bar and no level badge
        (a Baby Dragon card is green too, ~0.43, but it carries a badge)."""
        seg = hsv[10:80, int(a):int(b)]
        g = ((seg[:, :, 0] >= 30) & (seg[:, :, 0] <= 90) & (seg[:, :, 1] > 60)).mean()
        cx = (a + b) / 2
        return g >= 0.7 or (g >= 0.45 and level_badge(img, cx) < 0.05 and not dark_badge(img, cx))

    out, s = [], None
    for x in range(100, 1010):
        if not gap[x] and s is None:
            s = x
        elif gap[x] and s is not None:
            w = x - s
            if 55 <= w <= 92:
                pieces = [(s, x, False)]
            elif 93 <= w <= 320:
                n = max(2, round((w + 7) / 73))
                step = w / n
                pieces = [(s + i * step, s + (i + 1) * step, True) for i in range(n)]
            else:
                pieces = []
            out += [p for p in pieces if not forest(p[0], p[1])]
            s = None
    return out


def bar_candidates(img):
    """Centres of every card in the troop bar, by appearance only (no fixed slots: the order
    changes between battles and bonus cards push everything right mid-battle)."""
    xs = [(a + b) / 2 for a, b, _ in card_runs(img)]
    xs += [x + 28.0 for x in range(100, 1000) if badge_at(img, x)]  # black level badges
    out = []
    for cx in sorted(xs):
        if not out or cx - out[-1] >= 45:  # cards are ~75px apart
            out.append(cx)
    return out


def alive_unit(img, cx):
    """A deployed hero/siege card: health bar above it (searched around, the centre can be off)."""
    return any(hero_deployed(img, cx + d) for d in (-12, -6, 0, 6, 12))


def is_spell_card(img, cx):
    x0 = max(0, int(cx) - 60)
    bar = img[440:540, x0:int(cx) + 60]
    for n in SPELL_NAMES:
        t = CARDS.get(n)
        if t is None or t.shape[1] > bar.shape[1]:
            continue
        if cv2.minMaxLoc(cv2.matchTemplate(bar, t, cv2.TM_CCOEFF_NORMED))[1] > 0.7:
            return True
    return False


def card_body(img, cx, runs=None):
    """Snap a candidate to the card under it -> (centre, merged) or None (no card-sized body
    there, e.g. grass between two cards)."""
    for a, b, merged in (runs if runs is not None else card_runs(img)):
        if a <= cx < b:
            return (a + b) / 2, merged
    return None


def last_spell_x(img):
    """x of the rightmost spell card (freeze/spell templates) or None. The bar order is
    troops, siege, heroes, spells, then empty slots, so nothing to its right is a troop."""
    bar, best = img[440:540], None
    for n in SPELL_NAMES:
        t = CARDS.get(n)
        if t is None:
            continue
        r = cv2.matchTemplate(bar, t, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(r > 0.75)
        if len(xs):
            x = float(xs.max() + t.shape[1] / 2)
            best = x if best is None else max(best, x)
    return best


def undeployed_troops(img):
    """Cards that still hold units after the main deploy, wherever they sit in the bar: the x50
    mega troop, cards the scout-time scan missed, and bonus troops that appear mid-battle
    (x69 pumpkins, x12 ghosts ...). Skips used cards (grey), live heroes/sieges (tapping them
    fires an ability / drops the Clan Castle troops) and spells (they would land at the edge)."""
    runs = card_runs(img)
    spell_x = last_spell_x(img)
    out = []
    for c in bar_candidates(img):
        if spell_x is not None and c > spell_x - 20:
            continue  # spells themselves and the empty slots after them
        body = card_body(img, c, runs)
        if body is None or any(abs(body[0] - o) < 45 for o in out):
            continue
        cx, merged = body
        if card_sat(img, cx) <= 30:
            continue
        # a piece split out of a merged run sits between cards: it is one (its badge can be
        # recoloured when selected); a lone run must show a level badge (not scenery/empty slot)
        if not (merged or dark_badge(img, cx) or level_badge(img, cx) >= 0.05):
            continue
        if alive_unit(img, cx) or is_spell_card(img, cx):
            continue
        out.append(cx)
    return out


def drop_bonus(img, anchor):
    cards = undeployed_troops(img)
    if not cards:
        return False
    log('undeployed cards in battle:', [int(c) for c in cards])
    if stats.get('scout_img') is not None and stats.get('missed', 0) < 4:
        stats['missed'] = stats.get('missed', 0) + 1  # the scout-time scan missed these
        cv2.imwrite(os.path.join(LOG, 'unk', f'missed_{ts()}.png'), stats['scout_img'])
        stats['scout_img'] = None
    for cx in cards:
        for _ in range(6):  # holds until the card is used up (x50/x69 take a few)
            run_cmds(tap_cmds(*card_tap_pt(cx)) + hold_cmds(near(anchor, 12), random.uniform(1.8, 2.8)))
            rs(0.2, 0.4)
            im = cap()
            if card_sat(im, cx) <= 30 or alive_unit(im, cx):
                break
    return True


def pick_reward(img):
    """Mid-battle 'Pick a Reward!' popup (since 2026-10): three random offers (troops, gold ...).
    Always take the last one. True if the popup was handled."""
    if mmatch(img, 'lbl_reward', (440, 60, 820, 120)) is None:
        return False
    log('reward popup: taking the last offer')
    rs(0.4, 1.0)
    tap(795 + random.uniform(-30, 30), random.uniform(250, 340))
    rs(0.8, 1.2)
    return True


def monitor(t_start, anchor=None):
    saved_late = False
    last_unk = []
    sped = False
    last_sweep, sweeps = 0.0, 0
    while True:
        rs(1.8, 3.2)
        img = cap()
        if connection_lost(img):  # another device took the village: main loop waits + reloads
            log('monitor: connection lost during battle')
            return img
        st, m = state(img)
        el = time.time() - t_start
        if st == 'result':
            rs(2.5, 3.5)  # let the star/percent counters finish
            return cap()
        if st == 'battle':
            if pick_reward(img):
                continue
            if anchor and el > 10 and time.time() - last_sweep > random.uniform(5, 8) and sweeps < 10:
                last_sweep, sweeps = time.time(), sweeps + 1
                if drop_bonus(img, anchor):
                    continue
            s = None if sped else speed_button(img)
            if s:
                log('pressing 1X speed button')
                rs(0.2, 0.8)
                tap_box(s[0], s[1], s[2], s[3])
                sped = True
            elif el > 130 and not saved_late and not sped and stats.get('late', 0) < 5:
                # only interesting when the 1X button never showed up (battles now run ~150s)
                cv2.imwrite(os.path.join(LOG, 'unk', f'late_{ts()}.png'), img)
                saved_late = True
                stats['late'] = stats.get('late', 0) + 1
        elif st in ('home', 'menu', 'army'):
            log('monitor: unexpected state', st)
            for i, fr in enumerate(last_unk):  # the screens just before we lost the battle view
                cv2.imwrite(os.path.join(LOG, 'unk', f'monunk_{ts()}_{i}.png'), fr)
            return img
        elif st == 'unknown':
            last_unk = (last_unk + [img])[-4:]
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


def zoom_out():
    """Two-finger pinch via scrcpy's server (the camera zoom is shared by village and battle)."""
    try:
        import pinch
        pinch.zoom_out(random.randint(2, 3))
        log('zoomed camera out')
        return True
    except Exception as e:
        log('zoom out failed:', e)
        return False


def find_th(img, res):
    """The Town Hall: the best-scoring match with a confident th_shape (>= 0.60), else the
    best-scoring one that still passes (>= 0.50), else None.
    The overall best match can be a fake (grass at the map edge scored 0.89 as a 'TH16'; a cliff
    scored 0.92 with shape 0.50 above a real TH17 with 0.74) while a real TH17 half-covered by a
    clan label scored 0.86 (shape 0.53), so the top few are checked in order.
    -> (score, level, (x, y), shape)"""
    weak = []
    for s, lv, pos in res:
        if s < 0.85:
            break
        shape = th_shape(img[max(0, pos[1] - 50):pos[1] + 50, max(0, pos[0] - 50):pos[0] + 50], lv)
        if shape >= 0.60:
            return s, lv, pos, shape
        weak.append((s, lv, pos, shape))
    return next((t for t in weak if t[3] >= 0.50), None)


def attack_or_next(img, started=False):
    rs(0.6, 1.8)
    img = cap()
    st_now = state(img)[0]
    if st_now not in ('scout', 'battle'):  # screen changed under us (e.g. back home): no taps
        log(f'attack aborted: screen is now {st_now}')
        return
    res = detect_th(img)
    th = find_th(img, res)
    if th is None and zoom_out():  # no Town Hall found: the camera was probably zoomed in
        rs(0.5, 0.9)
        img = cap()
        res = detect_th(img)
        th = find_th(img, res)
    s1, lv, pos = th[:3] if th else res[0]
    cv2.imwrite(os.path.join(LOG, 'th', f'{ts()}_th{lv}_{s1:.3f}.png'),
                img[max(0, pos[1] - 50):pos[1] + 50, max(0, pos[0] - 50):pos[0] + 50])
    go = th is not None and lv >= MIN_TH
    extra = f' shape={th[3]:.2f}' if th else ' (no Town Hall confirmed)'
    log(f'TH{lv} score={s1:.3f}{extra} at {pos} -> {"ATTACK" if go else "next"}')
    if not go:
        if th is None and stats['skipped'] < 8:  # no TH found at all: keep a few frames to see why
            cv2.imwrite(os.path.join(LOG, 'unk', f'noth_{ts()}.png'), img)
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
    if state(cap())[0] not in ('scout', 'battle'):
        log('attack aborted before deploy: not in a battle view')
        return
    zone, img_red = reveal_red(img, pos)
    cards, t_dep, marks, side, anchor = deploy(img, th, zone)
    abilities(cards, t_dep)
    fixup(cards, zone, pos, anchor)
    if stats['battles'] < 30:
        save_plan(img_red, marks, f'{ts()}_th{lv}.jpg')
    img = monitor(t_dep, anchor)
    stats['battles'] += 1
    stats['progress'] = time.time()
    small = cv2.resize(img, None, fx=0.5, fy=0.5)
    cv2.imwrite(os.path.join(LOG, 'res', f'{ts()}_th{lv}.jpg'), small, [cv2.IMWRITE_JPEG_QUALITY, 70])
    hrs = (time.time() - stats['start']) / 3600
    log(f'battle done #{stats["battles"]} (skipped {stats["skipped"]}) {hrs:.2f}h')
    if os.environ.get('ONCE'):
        open(STOP_FILE, 'w').close()



# ---------------------------------------------------------------- home walls
# Builder Menu (game 18.647): the Build button opens a 'Buildings & Heroes' panel on the right.
# Tap the cheapest 'Wall xN' row (= lowest wall level) -> the wall's bar -> an affordable gold or
# elixir 'Upgrade' -> 'Upgrade Wall to Level N?' -> Confirm. Only while a builder is free.
# Never 'Upgrade More'; never a price that is not gold/elixir (no gems).
W = {n: load(n) for n in ['dlg_hv_title', 'dlg_confirm', 'btn_close', 'bb_cave']}
M = {n: cv2.imread(os.path.join(TPL, n + '.png'), 0)  # binary text masks
     for n in ['menu_title', 'lbl_wall', 'title_wall', 'lbl_upgrade', 'bdigit_0', 'lbl_reward']}
# Top of 'Wall (Le' only: a clan label under the bar (e.g. 'Golden Army') can cover the bottom of
# the title (full template 0.79 -> refused a real wall; top rows 0.90; 'Archer Tower (Level 3)' 0.64).
M['title_wall'] = M['title_wall'][:19]
RDIGITS = {str(d): cv2.imread(os.path.join(TPL, f'digit_{d}.png'), 0) for d in range(10)}
PDIGITS = {os.path.basename(p)[7:-4]: cv2.imread(p, 0).astype(np.float32) / 255  # '0'-'9', '11'
           for p in glob.glob(os.path.join(TPL, 'pdigit_*.png'))}
BUILD_BTN = (1090, 482)
GOLD_RESERVE = 100_000  # walls never spend this: every search costs gold (1,600 at TH17)


def white_mask(img):
    return ((img.min(axis=2) > 200) * 255).astype(np.uint8)


def cream_mask(img):
    """The selection bar's pale-yellow title text."""
    i = img.astype(np.int16)
    return (((i[:, :, 1] > 215) & (i[:, :, 2] > 215)) * 255).astype(np.uint8)


def mmatch(img, name, region, thr=0.8, mask=white_mask):
    """Text-mask template match in region (x0, y0, x1, y1) -> (cx, cy, w, h, score) or None."""
    x0, y0, x1, y1 = region
    t = M[name]
    r = cv2.matchTemplate(mask(img[y0:y1, x0:x1]), t, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(r)
    if mx < thr:
        return None
    h, w = t.shape
    return (x0 + loc[0] + w / 2, y0 + loc[1] + h / 2, w, h, mx)


def wmatch(img, name, region, thr=0.8):
    """Colour template match in region (x0, y0, x1, y1) -> (cx, cy, w, h, score) or None."""
    x0, y0, x1, y1 = region
    t = W[name]
    r = cv2.matchTemplate(img[y0:y1, x0:x1], t, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(r)
    if mx < thr:
        return None
    h, w = t.shape[:2]
    return (x0 + loc[0] + w / 2, y0 + loc[1] + h / 2, w, h, mx)


def read_counter(img, y0, y1, x0=940, x1=1100):
    """Top-bar gold/elixir counter (white digits). None if unreadable or doubtful.

    Only the digit rows are used: the resource bar's bright fill/highlight sits just above
    and below them and, when it touches the leading digits, merges them into one big blob
    (that dropped the '6 8' of 6,863,366 and read it as 63,366)."""
    d0, d1 = y0 + 5, y0 + 20
    m = (img[d0:d1, x0:x1].min(axis=2) > 200).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    gl, accepted = [], set()  # (x, w, mask)
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if h < 10 or a < 15:
            continue
        g = (lab[y:y + h, x:x + w] == i).astype(np.float32)
        if w <= 16:
            gl.append((x, w, g))
        elif w <= 26:  # two touching digits ('808' merged the 0 and 8): cut at the thinnest column
            cols = g.sum(axis=0)
            c = int(np.argmin(cols[w // 2 - 3:w // 2 + 4])) + w // 2 - 3
            for gx, gg in ((x, g[:, :c]), (x + c, g[:, c:])):
                keep = np.where(gg.sum(axis=0) > 0)[0]
                if len(keep) == 0:
                    return None
                gg = gg[:, keep[0]:keep[-1] + 1]
                rows = np.where(gg.sum(axis=1) > 0)[0]
                gl.append((gx + keep[0], gg.shape[1], gg[rows[0]:rows[-1] + 1]))
        else:
            continue
        accepted.add(i)
    gl.sort(key=lambda t: t[0])
    if not gl:
        return None
    # anything white left of the first digit that we did not accept -> don't trust it
    # a dropped leading digit is a tall blob (>=8px) right next to the number; thin glints,
    # sparkles and the bar's end/fill edge are not
    first = gl[0][0]
    last = gl[-1][0] + gl[-1][1]
    for i in range(1, n):
        x, y, w, h = st[i][:4]
        if h >= 8 and i not in accepted and x < last and x + w > first - 14:
            return None  # a digit-sized blob we could not read (merged/lit digits)
    for (xa, wa, _), (xb, _, _) in zip(gl, gl[1:]):
        if xb - (xa + wa) > 10:  # digit gap <=4, thousands gap ~6-9: wider = digit missing
            return None
    out = ''
    for x, w, g in gl:
        best = (0.6, None)
        for d, t in RDIGITS.items():
            if abs(t.shape[1] - w) > 3:
                continue
            sc = 1 - np.abs(cv2.resize(g, (t.shape[1], t.shape[0])) - t / 255.0).mean()
            if sc > best[0]:
                best = (sc, d)
        if best[1] is None:
            return None
        out += best[1]
    return int(out)


def read_row(img, y0):
    """Top-bar counter whose digit band starts at y0+5; two of three row offsets must agree."""
    seen = {}
    for d in (0, -2, 2):
        v = read_counter(img, y0 + d, y0 + d + 24, 960, 1106)
        if v is not None:
            seen[v] = seen.get(v, 0) + 1
            if seen[v] >= 2:
                return v
    return None


def resources(img):
    # game 18.647 (2026-10-13): gold y~28, elixir y~66 (was 30 / 82), digits end at x~1105
    return {'gold': read_row(img, 16), 'elixir': read_row(img, 53)}


def stable_resources():
    """Counters animate after a purchase or loot: read until two readings agree."""
    prev = resources(cap())
    for _ in range(12):  # a 4M drop can take several seconds to finish counting down
        rs(0.5, 0.8)
        cur = resources(cap())
        if cur == prev and None not in cur.values():
            return cur
        prev = cur
    if None in prev.values():
        cv2.imwrite(os.path.join(LOG, 'unk', f'counter_{ts()}.png'), cap())
    return prev


def builder_free(img):
    """Free-builder count 'N/M' above the Build button (bottom right; the second 'N/M' to its
    right is the helper's). False when N is 0 or the count is unreadable (be safe)."""
    m = (img[402:428, 1040:1095].min(axis=2) > 200).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    gl = sorted((st[i][0], i) for i in range(1, n) if st[i][3] >= 10 and st[i][4] >= 10)
    if len(gl) < 3:  # 'N', '/', 'M'
        return False
    i = gl[0][1]
    x, y, w, h = st[i][:4]
    g = (lab[y:y + h, x:x + w] == i).astype(np.float32)
    t = M['bdigit_0'].astype(np.float32) / 255
    zero = abs(w - t.shape[1]) <= 2 and 1 - np.abs(cv2.resize(g, (t.shape[1], t.shape[0])) - t).mean() > 0.85
    return not zero


def pricemask(img):
    """Price text: white (affordable) or salmon red (not); excludes the coloured icons."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    v, s, hh = hsv[:, :, 2], hsv[:, :, 1], hsv[:, :, 0]
    red = (hh < 12) | (hh > 170)
    return ((v > 170) & ((s < 70) | (red & (s < 150)))).astype(np.uint8)


def read_glyph(g):
    """One price blob -> (score, digits); lower is better. Touching digits ('00', '20', '11')
    form one wide blob: every cut is tried and the best two-glyph reading wins."""
    best = (1.0, None)
    for d, t in PDIGITS.items():
        h, w = t.shape
        if abs(g.shape[1] - w) <= 3 and abs(g.shape[0] - h) <= 2:
            a = cv2.resize(g, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
            best = min(best, (float(np.abs(a - t).mean()), d))
    if g.shape[1] >= 12:
        for c in range(3, g.shape[1] - 3):
            parts = [p[:, np.where(p.any(axis=0))[0]] for p in (g[:, :c], g[:, c:])]
            if all(p.shape[1] for p in parts):
                (sa, da), (sb, db) = read_glyph(parts[0]), read_glyph(parts[1])
                if da and db and max(sa, sb) < best[0]:
                    best = (max(sa, sb), da + db)
    return best


def read_price(img, y0, y1, x0=1035, x1=1130):
    """Builder Menu price like '5 600 000' -> 5600000, None if unsure."""
    n, lab, st, _ = cv2.connectedComponentsWithStats(pricemask(img[y0:y1, x0:x1]))
    out = ''
    for _, i in sorted((st[i][0], i) for i in range(1, n) if st[i][3] >= 7 and st[i][4] >= 12):
        x, y, w, h = st[i][:4]
        sc, d = read_glyph((lab[y:y + h, x:x + w] == i).astype(np.uint8) * 255)
        if d is None or sc > 0.32:
            return None
        out += d
    return int(out) if out else None


def red_frac(img):
    """Share of red among the white/red digit pixels: a price we cannot afford is red."""
    r = img.astype(np.int16)
    red = (r[:, :, 2] > 200) & (r[:, :, 2] - r[:, :, 1] > 45) & (r[:, :, 2] - r[:, :, 0] > 40)
    white = r.min(axis=2) > 200
    return red.sum() / max(1, red.sum() + white.sum())


def resource_icon(reg):
    """'gold' / 'elixir' for a price icon crop, None for anything else (gems!)."""
    p = reg.reshape(-1, 3).astype(np.int16)
    b, g, r = p[:, 0], p[:, 1], p[:, 2]
    gold = ((r > 200) & (g > 150) & (b < 90)).mean()
    elix = ((r > 130) & (b > 170) & (g < 110)).mean()
    if max(gold, elix) < 0.15:
        return None
    return 'gold' if gold > elix else 'elixir'


def menu_open(img):
    return mmatch(img, 'menu_title', (880, 0, 1100, 45)) is not None


def open_menu():
    """Build button -> Builder Menu. A frame of the open menu, or None."""
    img = cap()
    if not menu_open(img):
        tap(BUILD_BTN[0] + random.uniform(-25, 25), BUILD_BTN[1] + random.uniform(-12, 12))
        rs(1.2, 1.8)
        img = cap()
    return img if menu_open(img) else None


def close_menu():
    img = cap()
    x = find_red_x(img) if menu_open(img) else None
    if x:
        tap(x[0] + random.uniform(-6, 6), x[1] + random.uniform(-6, 6))
        rs(0.8, 1.2)


def scroll_menu(up=False):
    """~200px scroll. Grabs the panel at its right edge, beside the prices: a swipe that lands
    as a tap on a row only selects that building, and the bar is checked before any Upgrade."""
    d, a, x = random.uniform(185, 205), random.uniform(375, 395), random.uniform(1105, 1125)
    y0, y1 = (a - d, a) if up else (a, a - d)
    sh(f'input swipe {x:.0f} {y0:.0f} {x + random.uniform(-4, 4):.0f} {y1:.0f} '
       f'{random.randint(550, 700)}')
    rs(0.9, 1.2)


def same_panel(a, b):
    """The menu did not move (its top or bottom end was reached)."""
    return np.abs(a[55:465, 840:1145].astype(np.int16) - b[55:465, 840:1145]).mean() < 3


def wall_rows(img):
    """[(y, price)] for the 'Wall xN' rows visible in the menu ('Wall' must start the row)."""
    r = cv2.matchTemplate(white_mask(img[50:448, 876:930]), M['lbl_wall'], cv2.TM_CCOEFF_NORMED)
    tops = []
    for y in np.argsort(-r.max(axis=1)):
        if r[y].max() < 0.9:
            break
        if all(abs(y - q) > 8 for q in tops):
            tops.append(y)
    # name on the first line (top..top+16), price on the second (~20px lower)
    return [(50 + y + 8, read_price(img, 50 + y + 16, 50 + y + 33)) for y in sorted(tops)]


known = {'cheapest': None, 'empty': 0, 'pause_until': 0}


def find_wall(target):
    """Look through the open menu (current view, then top to bottom) and tap the first 'Wall'
    row priced `target` (None: only collect prices) -> (tapped price or None, prices seen)."""
    seen = set()

    def look(img):
        for y, price in wall_rows(img):
            seen.add(price)
            if target is not None and price == target:
                # the list may still be gliding: the row must not have moved
                if any(abs(y2 - y) <= 3 and p2 == price for y2, p2 in wall_rows(cap())):
                    tap(random.uniform(892, 950), y + random.uniform(-4, 4))
                    rs(1.0, 1.5)
                    return True
        return False

    if target is not None and look(cap()):
        return target, seen
    for _ in range(8):  # back to the top (the menu keeps its scroll position)
        before = cap()
        scroll_menu(up=True)
        if same_panel(before, cap()):
            break
    img = cap()
    for _ in range(20):
        if look(img):
            return target, seen
        scroll_menu()
        new = cap()
        if same_panel(img, new):
            break
        img = new
    seen.discard(None)
    return None, seen


def select_wall(res):
    """Open the menu and tap the cheapest 'Wall' row (= the lowest wall level) -> its price."""
    if open_menu() is None:
        log('walls: builder menu did not open')
        cv2.imwrite(os.path.join(LOG, 'unk', f'nomenu_{ts()}.png'), cap())
        return None
    if known['cheapest']:
        got, _ = find_wall(known['cheapest'])
        if got:
            return got
    _, seen = find_wall(None)  # level finished or first visit: full pass
    if not seen:
        known['cheapest'] = None
        known['empty'] += 1
        log('walls: no wall rows found')
        cv2.imwrite(os.path.join(LOG, 'unk', f'nowallrows_{ts()}.png'), cap())
        if known['empty'] >= 2:  # every wall is maxed for this Town Hall: look again later
            hrs = random.uniform(3, 4)
            known['pause_until'] = time.time() + hrs * 3600
            log(f'walls: none left to upgrade; next check in {hrs:.1f}h')
        return None
    known['empty'] = 0
    known['cheapest'] = min(seen)
    log(f'walls: wall prices in menu {sorted(seen)} -> {known["cheapest"]:,}')
    if max(res.values()) < known['cheapest']:
        return None  # cannot afford it yet
    got, _ = find_wall(known['cheapest'])
    return got


def upgrade_btns(img):
    """[(x, resource, affordable)] for the selected building's gold/elixir 'Upgrade' buttons.
    'Upgrade More' is excluded: its label sits higher and it has no price."""
    x0, y0 = 150, 436
    t = M['lbl_upgrade']
    r = cv2.matchTemplate(white_mask(img[y0:466, x0:830]), t, cv2.TM_CCOEFF_NORMED)
    out = []
    while True:
        _, mx, _, loc = cv2.minMaxLoc(r)
        if mx < 0.8:
            break
        r[:, max(0, loc[0] - 45):loc[0] + 45] = -1
        cx = x0 + loc[0] + t.shape[1] / 2
        kind = resource_icon(img[386:404, int(cx) + 26:int(cx) + 42])
        if kind:  # price shown above the hammer, red when we cannot afford it
            out.append((cx, kind, red_frac(img[392:401, int(cx) - 36:int(cx) + 22]) < 0.3))
    return sorted(out)


def wall_dialog_close(img, why):
    """Close a dialog via its red X (Back on the plain village can open 'quit game?')."""
    log(f'walls: {why} -> closing, no purchase')
    cv2.imwrite(os.path.join(LOG, 'unk', f'walldlg_{ts()}.png'), img)
    x = wmatch(img, 'btn_close', (600, 0, 1200, 140), thr=0.75)
    if x:
        tap(x[0] + random.uniform(-6, 6), x[1] + random.uniform(-6, 6))
        rs(0.8, 1.2)
    return None


def confirm_wall_upgrade(res, price):
    """The selection must be a wall: press its affordable gold/elixir Upgrade (paying with the
    resource we hold more of, if our budget `res` covers `price`), then Confirm after checking
    the dialog's price. -> resource used."""
    img = cap()
    if mmatch(img, 'title_wall', (150, 320, 820, 390), mask=cream_mask) is None:
        log('walls: selection is not a wall; not touching it')
        cv2.imwrite(os.path.join(LOG, 'unk', f'notwall_{ts()}.png'), img)
        return None
    btns = [b for b in upgrade_btns(img) if b[2] and (res.get(b[1]) or 0) >= price]
    if not btns:
        log('walls: upgrade buttons not affordable')
        return None
    bx, kind, _ = max(btns, key=lambda b: res.get(b[1]) or 0)
    tap(bx + random.uniform(-18, 18), random.uniform(410, 455))
    for _ in range(4):  # the dialog can take a moment to appear
        rs(0.7, 1.0)
        img = cap()
        if wmatch(img, 'dlg_hv_title', (300, 0, 900, 80)) and wmatch(img, 'dlg_confirm', (600, 400, 950, 490)):
            break
    else:
        if mmatch(img, 'title_wall', (150, 320, 820, 390), mask=cream_mask):
            log('walls: confirm dialog did not open; will retry next visit')
            return None  # still just the wall's bar: nothing to close
        return wall_dialog_close(img, 'unexpected dialog after Upgrade')
    # 'Upgrade Wall to Level N?': green Confirm button with <price><icon>
    reg = img[400:530, 600:960].astype(np.int16)
    b, g, r = reg[:, :, 0], reg[:, :, 1], reg[:, :, 2]
    n, lab, st, _ = cv2.connectedComponentsWithStats(((g > 170) & (g - r > 25) & (g - b > 80)).astype(np.uint8))
    if n < 2:
        return wall_dialog_close(img, 'no Confirm button')
    k = 1 + np.argmax(st[1:, cv2.CC_STAT_AREA])
    x, y, w, h, area = st[k]
    x, y = x + 600, y + 400
    if area < 2500 or resource_icon(img[y + 22:y + h - 6, x + w - 40:x + w - 8]) is None:
        return wall_dialog_close(img, 'Confirm price is not gold/elixir (gems?)')
    # main price line only (the struck-out old price below it is always red)
    if red_frac(img[y + 20:y + 40, x + 6:x + w - 42]) > 0.3:
        return wall_dialog_close(img, 'price shown in red')
    tap(x + w * random.uniform(0.3, 0.6), y + h * random.uniform(0.35, 0.7))
    rs(1.0, 1.6)
    return kind


def upgrade_walls():
    """Upgrade walls one at a time, lowest level (cheapest) first, while a builder is free and
    gold or elixir covers the price."""
    if time.time() < known['pause_until']:
        return 0
    img = cap()
    if state(img)[0] != 'home':  # only from the plain home-village screen
        return 0
    if not builder_free(img):
        log('walls: no free builder')
        return 0
    res = stable_resources()
    if None in res.values():
        log('walls: could not read gold/elixir')
        return 0
    budget = dict(res, gold=res['gold'] - GOLD_RESERVE)
    price = known['cheapest']
    if price and max(budget.values()) < price:
        log(f"walls: cheapest wall {price:,}; have gold {res['gold']:,} elixir {res['elixir']:,}")
        return 0
    done, tapped = 0, False
    try:
        for _ in range(30):
            got = select_wall(budget)
            if not got:
                break
            tapped = True
            kind = confirm_wall_upgrade(budget, got)
            if not kind:
                break
            budget[kind] -= got
            done += 1
            log(f'walls: upgraded a {got:,} wall with {kind}')
            if max(budget.values()) < got:
                break
    finally:
        close_menu()
        if tapped:  # tapping a menu row zoomed the camera onto that wall: battles need it zoomed out
            zoom_out()
    if done:
        log(f'walls: {done} upgraded this visit')
        stats['progress'] = time.time()
    return done


def connection_lost(img):
    """'Connection lost / Another device is connecting' dialog -> Reload button (x, y) or None."""
    t = T.get('conn_lost')
    if t is None:
        return None
    r = cv2.matchTemplate(img[120:300, 150:700], t, cv2.TM_CCOEFF_NORMED)
    if r.max() < 0.8:
        return None
    rl = cv2.matchTemplate(img[250:420, 150:700], T['reload'], cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(rl)
    h, w = T['reload'].shape[:2]
    return (150 + loc[0] + w / 2, 250 + loc[1] + h / 2) if mx > 0.7 else (331, 341)


def handle_connection_lost(img):
    """Another device took the village: wait ~5 min (per AGENTS.md), then press Reload."""
    btn = connection_lost(img)
    if not btn:
        return False
    wait = random.uniform(300, 360)
    log(f'connection lost (another device) -> waiting {wait:.0f}s, then Reload')
    stats['outage'] = time.time()
    time.sleep(wait)
    img = cap()
    btn = connection_lost(img) or btn
    tap(btn[0] + random.uniform(-15, 15), btn[1] + random.uniform(-5, 5))
    rs(15, 25)  # village reload
    stats['progress'] = time.time()
    return True


def safe_attack(img, started=False):
    """attack_or_next, but an unexpected error is logged instead of killing the bot."""
    try:
        attack_or_next(img, started)
    except DeviceLost:
        raise
    except Exception:
        import traceback
        log('attack error (continuing):', traceback.format_exc().strip().splitlines()[-1])
        cv2.imwrite(os.path.join(LOG, 'unk', f'error_{ts()}.png'), cap())


def find_red_x(img):
    """Red square close button with a white X (any window style), top-right area. (x, y) or None."""
    x0, y0 = 600, 0
    reg = img[0:150, 600:1200].astype(np.int16)
    b, g, r = reg[:, :, 0], reg[:, :, 1], reg[:, :, 2]
    red = ((r > 170) & (g < 90) & (b < 90)).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(red)
    best = None
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if not (26 <= w <= 56 and 26 <= h <= 56 and 0.7 < w / h < 1.4):
            continue
        box = img[y:y + h, x0 + x:x0 + x + w]
        white = (box.min(axis=2) > 200).mean()
        if 0.08 < white < 0.5 and (best is None or a > best[2]):
            best = (x0 + x + w / 2, y0 + y + h / 2, a)
    return best[:2] if best else None


def in_builder_base(img):
    """Builder base village: 'Attack!' button with the crossed-axes icon (not the map)."""
    reg = img[425:540, 40:190]
    if home_text_score(reg) < 0.9 or menu_open(img):  # the Builder Menu hides the resource bar
        return False
    # an unknown icon alone is not proof (a new event skin made the bot 'sail home' from the
    # home village): the builder base also has no dark elixir row
    return cv2.matchTemplate(reg, HOME_ICON, cv2.TM_CCOEFF_NORMED).max() < 0.45 and not home_resources(img)


def find_bb_cave(img):
    best = (0.72, None)
    for sc in (1.0, 0.9, 1.1, 0.8, 1.2, 0.7, 0.6):
        t = cv2.resize(W['bb_cave'], None, fx=sc, fy=sc)
        r = cv2.matchTemplate(img[0:440], t, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(r)
        if mx > best[0]:
            best = (mx, (loc[0] + t.shape[1] / 2, loc[1] + t.shape[0] / 2, sc))
    return best[1]


def slow_drag(x0, y0, x1, y1):
    j = lambda v, r: v + random.uniform(-r, r)
    sh(f'input swipe {j(x0, 25):.0f} {j(y0, 12):.0f} {j(x1, 25):.0f} {j(y1, 12):.0f} '
       f'{random.randint(1300, 1700)}')


BB_BOAT_OFFSET = (897, 50)  # builder-base boat relative to the cave gate (scale 1.0)


def go_home_from_builder_base():
    """Builder base -> home village: find the cave landmark, bring the boat into view, tap it."""
    log('in builder base -> sailing back to the home village')
    img = cap()
    x = find_red_x(img)
    if x:  # a window is open over the village: close it first
        tap(x[0] + random.uniform(-6, 6), x[1] + random.uniform(-6, 6))
        rs(1.0, 1.5)
    else:
        tap(random.uniform(930, 1000), random.uniform(170, 220))  # drop any selection
        rs(0.6, 1.0)
    blind = [(0, 340), (-420, -200), (-420, -200), (0, 300), (420, 0), (420, 0)]
    for _ in range(10):
        img = cap()
        if not in_builder_base(img):
            return True
        cave = find_bb_cave(img)
        if cave:
            bx = cave[0] + BB_BOAT_OFFSET[0] * cave[2]
            by = cave[1] + BB_BOAT_OFFSET[1] * cave[2]
            if 250 < bx < 1060 and 120 < by < 400:
                tap(bx + random.uniform(-12, 12), by + random.uniform(-8, 8))
                rs(6, 9)
                stats['progress'] = time.time()
                if not in_builder_base(cap()):
                    return True
                continue
            dx = max(-450, min(450, 700 - bx))
            dy = max(-300, min(300, 250 - by))
        elif blind:
            dx, dy = blind.pop(0)
        else:
            break
        slow_drag(600 - dx / 2, 270 - dy / 2, 600 + dx / 2, 270 + dy / 2)
        rs(0.8, 1.3)
    log('could not get back to the home village')
    cv2.imwrite(os.path.join(LOG, 'unk', f'bbstuck_{ts()}.png'), cap())
    return False


def restart_app():
    log('restarting app')
    sh(f'am force-stop {PKG}')
    rs(2, 4)
    sh(f'monkey -p {PKG} -c android.intent.category.LAUNCHER 1')
    rs(20, 30)


def prune_debug(days=2):
    """Debug frames pile up (a TH crop per scout, a result per battle): keep the last few days."""
    cutoff = time.time() - days * 86400
    for f in glob.glob(os.path.join(LOG, '*', '*.*')):
        if os.path.getmtime(f) < cutoff:
            os.remove(f)


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


def read_gems(img):
    """Gem counter on the home screen (row below dark elixir)."""
    # 18.647 (2026-10-13) moved the gem counter from y~176 up to y~140; a wrong row can still
    # read a stray glyph as a number (a '0' from the old rows), so two offsets must agree
    seen = {}
    for y0 in (128, 130, 126, 132, 164, 163, 165, 162, 166):
        v = read_counter(img, y0, y0 + 24, 1010, 1110)
        if v is not None:
            seen[v] = seen.get(v, 0) + 1
            if seen[v] >= 2:
                return v
    return None


def gems_spent(img):
    """True if gems went down while only the bot had the phone (it must never spend gems).
    A drop across an adb outage / another device / a crash is someone else's doing: logged only."""
    g = read_gems(img)
    if g is None:
        return False
    prev, t_prev = stats.get('gems'), stats.get('gems_t', 0)
    stats['gems'], stats['gems_t'] = g, time.time()
    if prev is None or g >= prev:
        return False
    # the counter animates while a reward is being added (2026-10-11: 4,630 read mid-count
    # on the way up to 4,814 after the user collected something): look again before stopping
    time.sleep(random.uniform(3, 4))
    again = read_gems(cap())
    if again is not None and again >= prev:
        stats['gems'] = again
        return False
    if stats.get('outage', 0) > t_prev:
        log(f'gems {prev:,} -> {g:,} (the phone was out of the bot\'s hands meanwhile)')
        return False
    log(f'GEMS DROPPED {prev:,} -> {g:,} while the bot was in control: stopping')
    cv2.imwrite(os.path.join(LOG, 'unk', f'gems_{ts()}.png'), img)
    return True


def main(walls=False):
    single_instance()
    prune_debug()
    walls_checked = False
    zoomed = False
    unknown_since = None
    unk_saved = False
    log(f'bot start MIN_TH={MIN_TH} walls={"on" if walls else "off"}')
    while True:
        img = cap(wait=True)
        if handle_connection_lost(img):
            continue
        st, m = state(img)
        if st == 'home' and os.path.exists(STOP_FILE):  # only stop at a safe point
            log('STOP file found at home screen, exiting')
            return
        if st != 'unknown':
            unknown_since, unk_saved = None, False
        if st not in ('home', 'unknown'):
            walls_checked = False  # next time we are home, look at walls again
        if time.time() - stats['progress'] > 900:  # watchdog: nothing happened for 15 min
            cv2.imwrite(os.path.join(LOG, 'unk', f'watchdog_{ts()}.png'), img)
            log('watchdog: no progress for 15 min')
            restart_app()
            stats['progress'] = time.time()
            continue
        if st == 'home':
            if not zoomed:  # after (re)start the camera zoom may have been reset
                zoomed = zoom_out()
                continue
            if gems_spent(img):
                return
            if walls and not walls_checked:
                walls_checked = True
                try:
                    upgrade_walls()
                except DeviceLost:
                    raise
                except Exception as e:  # never let the wall step kill the battle loop
                    log('walls: error', e)
                continue
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
            safe_attack(img)
        elif st == 'battle':
            rs(0.8, 1.4)  # the Next button slides in after End Battle; look again
            img = cap()
            st, m = state(img)
            if st == 'scout':
                safe_attack(img)
            elif st == 'battle' and fresh_battle(img):
                log('battle view with nothing deployed -> treating as scout')
                safe_attack(img, started=True)
            elif st == 'battle':
                log('battle already in progress -> monitoring')
                monitor(time.time())
        elif st == 'result':
            rs(1.0, 3.0)
            tap_box(m[0], m[1], m[2], m[3])
            walls_checked = False  # check walls again at the next home visit
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
            if in_builder_base(img):
                go_home_from_builder_base()
                unknown_since, unk_saved = None, False
                continue
            m = match_at(img, 'search_fail_home')
            # "Unable to attack Village! Try again later." -> Return Home. A slow 'Searching for
            # opponents...' shows the same button: leave that one alone for a minute.
            if m and dur > (60 if match_at(img, 'searching') else 3):
                log('search failed ("unable to attack"); returning home')
                rs(0.5, 1.5)
                tap_box(m[0], m[1], m[2], m[3])
                rs(3, 6)
                unknown_since, unk_saved = None, False
                continue
            x = find_red_x(img) if dur > 10 else None
            if x:  # a stray window (rankings, info, shop...): close it with its red X
                log('closing unknown window via its X')
                tap(x[0] + random.uniform(-6, 6), x[1] + random.uniform(-6, 6))
                rs(1.0, 1.8)
                continue
            if dur > 300:  # disconnect ("another device") etc: wait 5 min then reload
                restart_app()
                unknown_since, unk_saved = None, False
            rs(2.0, 4.0)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Clash of Clans home-village battle loop')
    ap.add_argument('--upgrade-walls', action='store_true',
                    help='also upgrade the lowest-level walls between battles')
    args = ap.parse_args()
    while True:
        try:
            main(walls=args.upgrade_walls)
            break  # STOP file
        except KeyboardInterrupt:
            break
        except DeviceLost:
            log('device was lost mid-action; aborted the stale action, resuming from the screen')
            stats['outage'] = time.time()
            try:
                os.remove(os.path.join(LOG, 'bot.pid'))
            except OSError:
                pass
        except Exception:
            import traceback
            log('unexpected error, restarting loop:', traceback.format_exc().strip().splitlines()[-1])
            try:
                os.remove(os.path.join(LOG, 'bot.pid'))
            except OSError:
                pass
            time.sleep(5)
