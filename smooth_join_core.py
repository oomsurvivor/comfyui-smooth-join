"""Smooth join for chained AI clips (H3 / Clown Context and similar).

Chained generators start each clip with the last frames of the previous one, so a
plain concat jumps back in time at every seam. For each pair of neighbours this:
  1. finds the overlap: the shift where the head of clip B repeats the tail of clip A,
  2. picks a cut point in the middle of the stretch where both are nearly identical,
  3. crossfades video (xfade) and audio (acrossfade) over a few frames there.
Pairs without an overlap are joined with a plain cut. Needs only ffmpeg + numpy.
Also works standalone: python smooth_join_core.py a.mp4 b.mp4 c.mp4 out.mp4
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

import numpy as np

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MATCH_DIFF = 2.5      # mean abs gray diff (0-255) under which two frames count as "the same"
PROBE_W = 96          # analysis width in px
WINDOW_S = 4.0        # seconds of tail/head compared


def find_ffmpeg() -> str:
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    for p in (r"C:\ffmpeg_exe\ffmpeg.exe",):
        if os.path.isfile(p):
            return p
    raise RuntimeError("ffmpeg not found")


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW, **kw)


def probe(ffmpeg: str, path: str) -> dict:
    """width, height, fps, exact video frame count, has_audio."""
    err = _run([ffmpeg, "-hide_banner", "-i", path]).stderr.decode("utf-8", "replace")
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    f = re.search(r"([\d.]+) fps", err) or re.search(r"([\d.]+) tbr", err)
    if not m:
        raise RuntimeError(f"No video stream in {os.path.basename(path)}")
    cnt = _run([ffmpeg, "-hide_banner", "-i", path, "-map", "0:v:0", "-c", "copy",
                "-f", "null", "-"]).stderr.decode("utf-8", "replace")
    n = re.findall(r"frame=\s*(\d+)", cnt)
    return {"w": int(m.group(1)), "h": int(m.group(2)), "fps": float(f.group(1)) if f else 24.0,
            "frames": int(n[-1]) if n else 0, "audio": "Audio:" in err}


def _gray(ffmpeg, path, info, tail):
    h = max(2, round(PROBE_W * info["h"] / info["w"] / 2) * 2)
    pos = ["-sseof", f"-{WINDOW_S}"] if tail else []
    dur = [] if tail else ["-t", f"{WINDOW_S}"]
    raw = _run([ffmpeg, "-v", "error", *pos, "-i", path, *dur, "-map", "0:v:0",
                "-vf", f"scale={PROBE_W}:{h},format=gray", "-f", "rawvideo", "-"]).stdout
    a = np.frombuffer(raw, np.uint8)
    return a[: a.size // (PROBE_W * h) * PROBE_W * h].reshape(-1, h, PROBE_W).astype(np.float32)


def find_overlap(a_tail, b_head, fade_frames=8):
    """-> None (plain cut) or dict(shift, run, k0, fade): B[i] == A[len-shift+i] for i < run."""
    if len(a_tail) < 3 or len(b_head) < 3:
        return None
    diff = lambda x, y: float(np.abs(x - y).mean())
    base = diff(a_tail[-1], b_head[0])            # jump a plain cut would show
    best = None
    for s in range(3, min(len(a_tail), len(b_head)) + 1):
        m = min(s, 6)
        sc = sum(diff(b_head[i], a_tail[-s + i]) for i in range(m)) / m
        if best is None or sc < best[0]:
            best = (sc, s)
    score, s = best
    if score > MATCH_DIFF or base < MATCH_DIFF or base < 2 * score:
        return None                               # no overlap, or the cut is already seamless
    run = 0
    while run < s and diff(b_head[run], a_tail[-s + run]) < MATCH_DIFF:
        run += 1
    fade = min(fade_frames, max(1, run - 2))
    return {"shift": s, "run": run, "k0": max(0, (run - fade) // 2), "fade": fade, "score": score}


def plan(ffmpeg, paths, fade_frames=8, infos=None):
    """Probe clips and decide every seam. Returns (infos, seams) with len(seams) == len(paths)-1."""
    infos = infos or [probe(ffmpeg, p) for p in paths]
    seams = []
    for i in range(len(paths) - 1):
        a = _gray(ffmpeg, paths[i], infos[i], tail=True)
        b = _gray(ffmpeg, paths[i + 1], infos[i + 1], tail=False)
        seams.append(find_overlap(a, b, fade_frames))
    return infos, seams


def build_cmd(ffmpeg, paths, infos, seams, out, venc=None):
    """ffmpeg command: trim each clip at the seams, xfade/acrossfade the overlaps, cut the rest."""
    fps = infos[0]["fps"]
    W, H = infos[0]["w"] // 2 * 2, infos[0]["h"] // 2 * 2
    n = len(paths)
    st, en = [0] * n, [i["frames"] for i in infos]
    for i, sm in enumerate(seams):
        if sm:
            en[i] = infos[i]["frames"] - sm["shift"] + sm["k0"] + sm["fade"]
            st[i + 1] = sm["k0"]
    audio = any(i["audio"] for i in infos)
    cmd = [ffmpeg, "-hide_banner", "-y"]
    for p in paths:
        cmd += ["-i", p]
    g, ain, extra = [], [], n
    for i, inf in enumerate(infos):
        ln = en[i] - st[i]
        g.append(f"[{i}:v]trim=start_frame={st[i]}:end_frame={en[i]},setpts=PTS-STARTPTS,"
                 f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
                 f"setsar=1,fps={fps:g},settb=AVTB,format=yuv420p[v{i}]")
        if not audio:
            continue
        if inf["audio"]:
            src = f"[{i}:a]"
        else:                                     # silent clip -> generated silence
            cmd += ["-f", "lavfi", "-t", f"{inf['frames'] / fps:.6f}", "-i",
                    "anullsrc=r=48000:cl=stereo"]
            src, extra = f"[{extra}:a]", extra + 1
        g.append(f"{src}aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                 f"atrim=start={st[i] / fps:.6f}:end={en[i] / fps:.6f},asetpts=PTS-STARTPTS,"
                 f"apad=whole_dur={ln / fps:.6f},atrim=end={ln / fps:.6f}[a{i}]")
    cv, ca, cur = "v0", "a0", en[0] - st[0]
    for i, sm in enumerate(seams, start=1):
        ln = en[i] - st[i]
        if sm:
            d = sm["fade"] / fps
            g.append(f"[{cv}][v{i}]xfade=transition=fade:duration={d:.6f}:"
                     f"offset={(cur - sm['fade']) / fps:.6f}[x{i}]")
            if audio:
                g.append(f"[{ca}][a{i}]acrossfade=d={d:.6f}[y{i}]")
            cur += ln - sm["fade"]
        else:
            g.append(f"[{cv}][v{i}]concat=n=2:v=1:a=0[x{i}]")
            if audio:
                g.append(f"[{ca}][a{i}]concat=n=2:v=0:a=1[y{i}]")
            cur += ln
        cv, ca = f"x{i}", f"y{i}"
    cmd += ["-filter_complex", ";".join(g), "-map", f"[{cv}]"]
    if audio:
        cmd += ["-map", f"[{ca}]", "-c:a", "aac", "-b:a", "192k"]
    cmd += (venc or ["-c:v", "libx264", "-preset", "medium", "-crf", "16"])
    cmd += ["-pix_fmt", "yuv420p", "-r", f"{fps:g}", "-movflags", "+faststart", out]
    return cmd, cur


def join(paths, out, ffmpeg=None, fade_frames=8, venc=None, runner=None, infos=None):
    """Join clips smoothly into `out`. runner(cmd) -> returncode lets callers track/cancel ffmpeg.
    Returns dict(out, seams, frames)."""
    ffmpeg = ffmpeg or find_ffmpeg()
    infos, seams = plan(ffmpeg, paths, fade_frames, infos)
    cmd, frames = build_cmd(ffmpeg, paths, infos, seams, out, venc)
    rc = runner(cmd) if runner else _run(cmd).returncode
    if rc != 0 or not os.path.isfile(out):
        raise RuntimeError("smooth join: ffmpeg failed")
    return {"out": out, "seams": seams, "frames": frames, "fps": infos[0]["fps"]}


if __name__ == "__main__":
    import sys
    r = join(sys.argv[1:-1], sys.argv[-1])
    for i, s in enumerate(r["seams"], 1):
        print(f"seam {i}:", f"overlap {s['shift']}f, cut at +{s['k0']}, fade {s['fade']}f" if s else "plain cut")
    print(r["out"], r["frames"], "frames")
