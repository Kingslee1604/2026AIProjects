"""Animated replay of a real crash-test run, for the README.

Kill times, outages, consumer stalls and every final count come from the run's results.json.
Counts between the start and the end are interpolated. Run from event-platform/:

    uv run --no-project --with pillow==11.3.0 python bench/demo_gif.py
"""

import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont  # pyright: ignore[reportMissingImports]

RUN = Path("bench/results/2026-11-07-Kingslee-crash")
SCENARIO = "mixed"
OUT = Path("docs/images/crash-replay.gif")

W, H, S = 960, 540, 2  # logical size, supersampling factor
FPS = 10
SPEEDUP = 5.0  # 60 s of the run in 12 s
HOLD_START, HOLD_END = 0.8, 4.0  # seconds of animation

BG = "#06161b"
GRID = "#0b2128"
PANEL = "#0b2229"
EDGE = "#173b42"
TEXT = "#e6fffb"
MUTED = "#8fb8bd"
TEAL = "#2dd4bf"
SKY = "#38bdf8"
RED = "#f87171"
AMBER = "#fbbf24"
GREEN = "#4ade80"
OK_LINE = "#2b6f73"

FONT_DIRS = [Path("/mnt/c/Windows/Fonts"), Path("C:/Windows/Fonts")]


def font(names: list[str], size: int) -> ImageFont.FreeTypeFont:
    for d in FONT_DIRS:
        for n in names:
            if (d / n).exists():
                return ImageFont.truetype(str(d / n), size * S)
    return ImageFont.truetype("DejaVuSans.ttf", size * S)


F_TITLE = font(["segoeuib.ttf"], 22)
F_BOLD = font(["segoeuib.ttf"], 14)
F_TEXT = font(["segoeui.ttf"], 13)
F_SMALL = font(["segoeui.ttf"], 11)
F_MONO = font(["CascadiaMono.ttf", "consola.ttf"], 13)
F_BIG = font(["segoeuib.ttf"], 26)
F_NUM = font(["segoeuib.ttf"], 20)

data = json.loads((RUN / "results.json").read_text())
sc = data["scenarios"][SCENARIO]
LOAD_S = sc["load_s"]
assert sc["passed"]
assert sc["lost"] == sc["extra"] == sc["order_violations"] == 0

# Restart times are not recorded for gateways and workers; they come back in 1-2 s (ADR-0004).
WORKER_RESTART_S = 2.0
outages: dict[str, list[tuple[float, float, float]]] = {}  # node -> (killed, restarting, healthy)
for b in sc["breaks"]:
    node, at = b["sigkill"], b["at_s"]
    if "down_s" in b:
        up = at + b["down_s"]
        outages.setdefault(node, []).append((at, up, up + b["healthy_after_restart_s"]))
    else:
        outages.setdefault(node, []).append((at, at + WORKER_RESTART_S, at + WORKER_RESTART_S))

stalls = {}
for name, st in sc["consumer_stalls"].items():
    stalls[name] = (st["from_s"], st["from_s"] + st["longest_s"], st["peak_lag"])


def state(node: str, t: float) -> str:
    for killed, restarting, healthy in outages.get(node, []):
        if killed <= t < restarting:
            return "killed"
        if restarting <= t < healthy:
            return "restarting"
    return "ok"


def lag(name: str, t: float) -> int:
    if name not in stalls:
        return 0
    a, b, peak = stalls[name]
    drain = 1.5
    if a <= t < b:
        return int(peak * (t - a) / (b - a))
    if b <= t < b + drain:
        return int(peak * (1 - (t - b) / drain))
    return 0


# Layout (logical px): box centres
GW = {f"gateway-{i}": (265, 175 + 85 * (i - 1)) for i in (1, 2, 3)}
KF = {f"kafka-{i}": (470, 175 + 85 * (i - 1)) for i in (1, 2, 3)}
CONS = {"ledger": (680, 195), "totals": (680, 325)}
SINK = {"ledger": (868, 195), "totals": (868, 325)}
CLIENTS = (80, 260)
BOX_W, BOX_H = 124, 54


def box(d: ImageDraw.ImageDraw, c, title, sub, st="ok", w=BOX_W, h=BOX_H, accent=None):
    x, y = c
    line = {"ok": accent or OK_LINE, "killed": RED, "restarting": AMBER, "stalled": AMBER}[st]
    fill = {"ok": PANEL, "killed": "#2a1216", "restarting": "#2a2210", "stalled": "#2a2210"}[st]
    d.rounded_rectangle(
        [(x - w / 2) * S, (y - h / 2) * S, (x + w / 2) * S, (y + h / 2) * S],
        radius=10 * S,
        fill=fill,
        outline=line,
        width=2 * S,
    )
    d.text((x * S, (y - 9) * S), title, font=F_BOLD, fill=TEXT, anchor="mm")
    sub_color = {"ok": MUTED, "killed": RED, "restarting": AMBER, "stalled": AMBER}[st]
    d.text((x * S, (y + 11) * S), sub, font=F_SMALL, fill=sub_color, anchor="mm")


def line(d, a, b, color=EDGE, w=1):
    d.line([a[0] * S, a[1] * S, b[0] * S, b[1] * S], fill=color, width=w * S)


def right(c, w=BOX_W):
    return (c[0] + w / 2, c[1])


def left(c, w=BOX_W):
    return (c[0] - w / 2, c[1])


# Records: each one's route and timing, in run seconds.
SEG = 1.4  # run seconds per hop (0.28 s of animation)
rng = random.Random(7)  # noqa: S311 - layout jitter, not security
particles = []
for i in range(int(LOAD_S * 7)):
    t0 = i / 7 + rng.uniform(0, 0.12)
    gws = [g for g in GW if state(g, t0) == "ok"]
    brokers = [k for k in KF if state(k, t0 + SEG) == "ok"]
    g, k = rng.choice(gws), rng.choice(brokers)
    cons = "ledger" if i % 2 else "totals"
    leave = t0 + 2 * SEG
    if cons in stalls:
        a, b, _ = stalls[cons]
        if a <= leave < b:  # waits in Kafka until the consumer is back, then catches up fast
            leave = b + (leave - a) * 0.25
    pts = [
        right(CLIENTS, 110),
        left(GW[g]),
        right(GW[g]),
        left(KF[k]),
        right(KF[k]),
        left(CONS[cons]),
        right(CONS[cons]),
        left(SINK[cons], 128),
    ]
    times = [
        t0,
        t0 + SEG * 0.8,
        t0 + SEG,
        t0 + 1.8 * SEG,
        leave,
        leave + 0.8 * SEG,
        leave + SEG,
        leave + 1.8 * SEG,
    ]
    particles.append((pts, times, cons))


def particle_pos(pts, times, t):
    if t < times[0] or t >= times[-1]:
        return None
    for j in range(len(times) - 1):
        if times[j] <= t < times[j + 1]:
            if j == 3 and times[4] - times[3] > 0.01 and t > times[3] + 0.01:
                return None  # waiting in the Kafka log
            f = (t - times[j]) / (times[j + 1] - times[j])
            return (
                pts[j][0] + (pts[j + 1][0] - pts[j][0]) * f,
                pts[j][1] + (pts[j + 1][1] - pts[j][1]) * f,
            )
    return None


def fmt(n: float) -> str:
    return f"{int(n):,}"


def frame(anim_t: float) -> Image.Image:
    t = min(max((anim_t - HOLD_START) * SPEEDUP, 0.0), LOAD_S)
    done = anim_t >= HOLD_START + LOAD_S / SPEEDUP
    im = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(im)
    for x in range(0, W, 40):
        d.line([x * S, 0, x * S, H * S], fill=GRID, width=1)
    for y in range(0, H, 40):
        d.line([0, y * S, W * S, y * S], fill=GRID, width=1)

    d.text((28 * S, 22 * S), "Crash test, replayed", font=F_TITLE, fill=TEXT)
    d.text(
        (28 * S, 54 * S),
        f"3 Kafka nodes · {sc['clients']} clients retrying on error · every kill is a real SIGKILL",
        font=F_TEXT,
        fill=MUTED,
    )
    d.text(
        ((W - 28) * S, 30 * S),
        f"t = {t:4.1f} s of {LOAD_S:.0f} s",
        font=F_MONO,
        fill=TEAL,
        anchor="ra",
    )

    # edges
    for g in GW.values():
        line(d, right(CLIENTS, 110), left(g))
        for k in KF.values():
            line(d, right(g), left(k))
    for k in KF.values():
        for c in CONS.values():
            line(d, right(k), left(c))
    for name in CONS:
        line(d, right(CONS[name]), left(SINK[name], 128))

    # records in flight
    for pts, times, _cons in particles:
        p = particle_pos(pts, times, t) if not done else None
        if p:
            col = SKY if times[3] > t else TEAL
            r = 3.2
            d.ellipse([(p[0] - r) * S, (p[1] - r) * S, (p[0] + r) * S, (p[1] + r) * S], fill=col)

    # nodes
    box(d, CLIENTS, "AI apps", "send usage", w=110, h=70, accent="#3a5f66")
    for name, c in GW.items():
        st = state(name, t)
        box(d, c, name, {"ok": "running", "killed": "SIGKILL", "restarting": "restarting"}[st], st)
    for name, c in KF.items():
        st = state(name, t)
        box(
            d,
            c,
            name,
            {"ok": "broker", "killed": "SIGKILL · down", "restarting": "rejoining"}[st],
            st,
        )
    d.text((470 * S, 128 * S), "Kafka · replication 3", font=F_SMALL, fill=MUTED, anchor="mm")
    for name, c in CONS.items():
        st, lg = state(name, t), lag(name, t)
        title = "Ledger writer" if name == "ledger" else "Totals service"
        if st != "ok":
            box(d, c, title, {"killed": "SIGKILL", "restarting": "restarting"}[st], st)
        elif lg:
            box(d, c, title, f"catching up · lag {fmt(lg)}", "stalled")
        else:
            box(d, c, title, "billing rows" if name == "ledger" else "Kafka transactions")
    box(d, SINK["ledger"], "Postgres", "the ledger (billed)", w=128, accent="#2f5f8a")
    box(d, SINK["totals"], "usage.totals", "live totals topic", w=128, accent="#2f5f8a")

    # counters
    acked = sc["records_acked"] * t / LOAD_S
    retries = sc["resends_acked"] * t / LOAD_S
    ledger_rows = (
        max(0.0, acked - sc["acked_per_s"] * 0.3 - lag("ledger", t))
        if not done
        else sc["records_acked"]
    )
    cy = 402
    cols = [
        ("acknowledged (202)", fmt(acked), TEXT),
        ("client retries", fmt(retries), TEXT),
        ("rows in the ledger", fmt(ledger_rows), TEXT),
        ("lost", "0" if done else "…", GREEN if done else MUTED),
        ("counted twice", "0" if done else "…", GREEN if done else MUTED),
        ("out of order", "0" if done else "…", GREEN if done else MUTED),
    ]
    cw = (W - 56) / len(cols)
    for i, (label, value, col) in enumerate(cols):
        x = 28 + cw * i
        d.rounded_rectangle(
            [x * S, cy * S, (x + cw - 10) * S, (cy + 58) * S],
            radius=8 * S,
            fill=PANEL,
            outline=EDGE,
            width=1 * S,
        )
        d.text(((x + 12) * S, (cy + 8) * S), label, font=F_SMALL, fill=MUTED)
        d.text(((x + 12) * S, (cy + 26) * S), value, font=F_NUM, fill=col)

    # timeline with the real kill times
    ty, tx0, tx1 = 486, 28, W - 28
    d.rounded_rectangle([tx0 * S, (ty - 2) * S, tx1 * S, (ty + 2) * S], radius=2 * S, fill=EDGE)
    px = tx0 + (tx1 - tx0) * t / LOAD_S
    d.rounded_rectangle([tx0 * S, (ty - 2) * S, px * S, (ty + 2) * S], radius=2 * S, fill=TEAL)
    for b in sc["breaks"]:
        bx = tx0 + (tx1 - tx0) * b["at_s"] / LOAD_S
        hit = t >= b["at_s"]
        d.line(
            [bx * S, (ty - 8) * S, bx * S, (ty + 8) * S],
            fill=RED if hit else "#7a3b40",
            width=2 * S,
        )
        d.text(
            (bx * S, (ty + 12) * S),
            f"kill {b['sigkill']}",
            font=F_SMALL,
            fill=RED if hit else "#7a5a5e",
            anchor="ma",
        )
    d.ellipse([(px - 5) * S, (ty - 5) * S, (px + 5) * S, (ty + 5) * S], fill=TEAL)
    d.text(
        (28 * S, (H - 18) * S),
        'Replay of a real run (2026-11-07, scenario "mixed"): kill times, stalls and final '
        "counts from its results.json; counts in between are interpolated. 60 s shown in 12 s.",
        font=F_SMALL,
        fill="#5f8a90",
        anchor="ls",
    )

    if done:
        ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        bx0, by0, bx1, by1 = 170, 120, W - 170, 380
        od.rounded_rectangle(
            [bx0 * S, by0 * S, bx1 * S, by1 * S],
            radius=16 * S,
            fill=(6, 22, 27, 250),
            outline=GREEN,
            width=2 * S,
        )
        im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
        d = ImageDraw.Draw(im)
        cx = W / 2
        d.text((cx * S, 150 * S), "Checked after the run", font=F_TEXT, fill=MUTED, anchor="mm")
        d.text(
            (cx * S, 190 * S),
            "0 lost · 0 counted twice · 0 out of order",
            font=F_BIG,
            fill=GREEN,
            anchor="mm",
        )
        assert sc["kafka"]["exact"]
        assert sc["database"]["exact"]
        assert sc["database"]["tokens_counted"] == sc["database"]["tokens_expected"]
        acked, resent = fmt(sc["records_acked"]), fmt(sc["resends_acked"])
        lines = [
            f"{acked} records acknowledged, {resent} client retries",
            f"{len(sc['breaks'])} SIGKILLs: a gateway, the totals service, a Kafka broker twice",
            f"Ledger and Kafka totals exact: {fmt(sc['database']['tokens_counted'])} tokens",
        ]
        for i, s in enumerate(lines):
            d.text((cx * S, (238 + 30 * i) * S), s, font=F_TEXT, fill=TEXT, anchor="mm")
        d.text(
            (cx * S, 350 * S),
            "bench/crash.py · results in bench/results/",
            font=F_MONO,
            fill=MUTED,
            anchor="mm",
        )

    return im.resize((W, H), Image.LANCZOS)


def main() -> None:
    total = HOLD_START + LOAD_S / SPEEDUP + HOLD_END
    n = int(total * FPS)
    frames = [frame(i / FPS) for i in range(n)]
    # One shared palette, so unchanged regions stay identical between frames (smaller GIF).
    sample = Image.new("RGB", (W, H * 3))
    for i, f in enumerate([frames[n // 3], frames[int(n * 0.45)], frames[-1]]):
        sample.paste(f, (0, H * i))
    pal = sample.quantize(colors=63, method=Image.Quantize.MEDIANCUT)
    q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    # Each later frame keeps only the pixels that changed; the rest is transparent (index 63),
    # so the GIF stores little more than the moving records and counters.
    clear = 63
    palette = q[0].getpalette()[: 63 * 3] + [255, 0, 255] + [0, 0, 0] * 192
    out = []
    for i, f in enumerate(q):
        f.putpalette(palette)
        if i == 0:
            out.append(f)
            continue
        prev = q[i - 1].tobytes()
        cur = bytearray(f.tobytes())
        for j, (a, b) in enumerate(zip(prev, cur, strict=True)):
            if a == b:
                cur[j] = clear
        g = Image.frombytes("P", f.size, bytes(cur))
        g.putpalette(palette)
        out.append(g)
    durations = [int(1000 / FPS)] * n
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out[0].save(
        OUT,
        save_all=True,
        append_images=out[1:],
        duration=durations,
        loop=0,
        optimize=False,
        disposal=1,
        transparency=clear,
    )
    print(f"{OUT}: {n} frames, {OUT.stat().st_size / 1024:.0f} KiB")
    if "--stills" in sys.argv:
        for name, at in [("mid", 0.45), ("end", 0.99)]:
            frames[int(n * at)].save(OUT.with_name(f"crash-replay-{name}.png"))


if __name__ == "__main__":
    main()
