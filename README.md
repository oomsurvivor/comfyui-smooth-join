# Smooth Join Chain (ComfyUI)

Joins chained AI video clips into one video **without the jump at every seam**.

Chained generators (MiniMax H3 with Clown Context / H3 Motion Context, and similar
"continue from the last frames" setups) start each new clip with a copy of the previous
clip's last frames. Put those clips back to back and the video jumps back in time at
every seam. This node fixes that automatically:

1. **Finds the overlap**: compares the tail of clip A with the head of clip B and finds
   how many frames B repeats (e.g. 39 frames for Clown Context's default `head_frames`).
2. **Cuts in the middle of the match**: the repeated frames are only identical for part
   of the overlap, so it cuts where both clips agree best.
3. **Crossfades** video (`xfade`) and audio (`acrossfade`) over a few frames there.

Clips without an overlap are joined with a plain cut, so mixed timelines are fine.

## Install

```
cd ComfyUI/custom_nodes
git clone https://github.com/oomsurvivor/comfyui-smooth-join
```

Requirements: `numpy` (already in ComfyUI) and `ffmpeg`, found on `PATH`, via
`imageio-ffmpeg`, or at `C:\ffmpeg_exe\ffmpeg.exe`. Restart ComfyUI after installing.

## Node: Smooth Join Chain (drop overlap + crossfade)

Category `video/smooth join`. Put it after the node that saves each clip.

| Input | |
|---|---|
| `filenames` | `Filenames` output of VideoHelperSuite's **Video Combine** (with `save_output` on). The `-audio.mp4` file is used when there is one. |
| `shot_number` | Shot number of this clip, e.g. from Clown Shot Counter. |
| `fade_frames` | Crossfade length at each seam (default 8). Longer = softer, but more visible ghosting. |
| `chain_name` | Name of the manifest file. Use different names for chains that run in parallel. |

Output: `joined_path` (STRING), plus a text summary and a preview on the node.

How it behaves:

- Every run records "shot N = this clip" in `<chain_name>_shots.json` next to the clips.
- From shot 2 on, it rebuilds `<shot 1 name> (smooth join).mp4` from shots 1..N, overwriting
  the previous join, so there is always one up-to-date joined file.
- Re-rolling shot N replaces its old take and drops every later shot from the chain.
  Going back to shot 1 (Reset) starts a new chain. Individual clips are never deleted.

## Example workflow

[`examples/clown_3clip_test_SmoothJoin.json`](examples/clown_3clip_test_SmoothJoin.json) is
roychodraws' Clown Context Suite example (`sane_clown_example.json`) with Smooth Join Chain
wired in after the clip saver. It needs the Clown Context Suite and the MiniMax H3 nodes;
load your own reference pictures in place of the two image loaders.

## Command line

The core works without ComfyUI:

```
python smooth_join_core.py clip1.mp4 clip2.mp4 clip3.mp4 joined.mp4
```

It prints, per seam, the detected overlap, the cut point and the fade length.

## License

MIT
