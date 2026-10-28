"""Smooth Join Chain: joins chained H3 / Clown clips into one video without seam jumps.

Put it after the clip's Video Combine node. Every run records "shot N -> this clip" in a small
manifest next to the clips (a re-rolled shot replaces its old take, Reset/shot 1 starts over),
then rebuilds "<shot 1 name> (ghép mượt).mp4" from shots 1..N: the frames each clip repeats
from the previous one are dropped and the seam is crossfaded (see smooth_join_core.py).
"""
import json
import os

import folder_paths

from . import smooth_join_core as sj

VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv")


def _pick_video(files):
    vids = [f for f in files if str(f).lower().endswith(VIDEO_EXT)]
    with_audio = [f for f in vids if os.path.splitext(f)[0].endswith("-audio")]
    return (with_audio or vids or [None])[-1]


class SmoothJoinChain:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "filenames": ("VHS_FILENAMES", {"tooltip": "Filenames output of the node that saves each clip"}),
                "shot_number": ("INT", {"forceInput": True, "tooltip": "Shot number of this clip (Clown Shot Counter)"}),
                "fade_frames": ("INT", {"default": 8, "min": 1, "max": 24,
                                        "tooltip": "Crossfade length at each seam, in frames"}),
                "chain_name": ("STRING", {"default": "clown_chain",
                                          "tooltip": "Manifest name; use different names for parallel chains"}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("joined_path",)
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "video/smooth join"

    @classmethod
    def IS_CHANGED(cls, **kw):
        return float("nan")

    def run(self, filenames, shot_number, fade_frames, chain_name):
        clip = _pick_video(list(filenames[1]) if filenames else [])
        if not clip or not os.path.isfile(clip):
            raise RuntimeError("Smooth Join Chain: no saved video in filenames (is save_output on?)")
        folder = os.path.dirname(clip)
        man_path = os.path.join(folder, f"{chain_name}_shots.json")
        try:
            with open(man_path, encoding="utf-8") as f:
                shots = json.load(f)
        except (OSError, ValueError):
            shots = {}
        n = int(shot_number)
        shots = {k: v for k, v in shots.items() if int(k) < n}   # re-roll / reset drops later shots
        shots[str(n)] = clip
        with open(man_path, "w", encoding="utf-8") as f:
            json.dump(shots, f, ensure_ascii=False, indent=1)

        order = [shots.get(str(i)) for i in range(1, n + 1)]
        missing = [i for i, p in enumerate(order, 1) if not p or not os.path.isfile(p)]
        if n < 2 or missing:
            note = f"shot {n} saved" + (f"; missing shots {missing}, join skipped" if missing else "")
            print(f"[Smooth Join] {note}")
            return {"ui": {"text": [note]}, "result": (clip,)}

        out = os.path.join(folder, os.path.splitext(os.path.basename(order[0]))[0] + " (ghép mượt).mp4")
        r = sj.join(order, out, fade_frames=fade_frames)
        seams = ", ".join(f"{s['shift']}f overlap/{s['fade']}f fade" if s else "plain cut" for s in r["seams"])
        print(f"[Smooth Join] shots 1-{n} -> {out} ({r['frames']} frames; {seams})")
        sub = os.path.relpath(folder, folder_paths.get_output_directory())
        ui = {"text": [f"{n} shots, {r['frames'] / r['fps']:.2f}s: {seams}"]}
        if not sub.startswith(".."):
            ui["images"] = [{"filename": os.path.basename(out), "subfolder": "" if sub == "." else sub,
                             "type": "output"}]
            ui["animated"] = [True]
        return {"ui": ui, "result": (out,)}


NODE_CLASS_MAPPINGS = {"SmoothJoinChain": SmoothJoinChain}
NODE_DISPLAY_NAME_MAPPINGS = {"SmoothJoinChain": "Smooth Join Chain (drop overlap + crossfade)"}
