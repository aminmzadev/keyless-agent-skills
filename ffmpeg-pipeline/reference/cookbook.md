# ffmpeg-pipeline — cookbook

Every recipe here is what `vidkit.py` runs internally. Keep it handy when you
need to deviate.

## Ken Burns push-in on a still

```bash
ffmpeg -loop 1 -i shot.jpg -t 4 \
  -vf "scale=2560:-2,zoompan=z='min(zoom+0.0015,1.18)':d=100:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1280x720:fps=25,format=yuv420p" \
  -c:v libx264 -preset veryfast -crf 21 clip.mp4
```

`d` = frames = seconds × fps. `z` increments per frame; `1.18` is the ceiling —
past that the image visibly softens.

## Crossfade N clips

```bash
ffmpeg -i c1.mp4 -i c2.mp4 -i c3.mp4 -filter_complex \
 "[0:v][1:v]xfade=transition=fade:duration=0.8:offset=3.2[v1];\
  [v1][2:v]xfade=transition=fade:duration=0.8:offset=6.4[v2]" \
 -map "[v2]" -c:v libx264 -preset veryfast -crf 21 -pix_fmt yuv420p out.mp4
```

Offset of clip *i* = `i*(seconds - fade)`. Wrong offsets are the #1 cause of
"my crossfade jumped".

## Narration that fits the picture

```bash
# voice shorter than video → pad with silence, never truncate the picture
ffmpeg -i silent.mp4 -i voice.mp3 -filter_complex "[1:a]apad[a]" -map 0:v -map "[a]" \
  -c:v libx264 -c:a aac -shortest out.mp4

# voice longer than video → loop the picture
ffmpeg -stream_loop 2 -i silent.mp4 -i voice.mp3 -map 0:v -map 1:a \
  -c:v libx264 -c:a aac -shortest out.mp4
```

## Scale-to-cover (no letterboxing)

```
scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,setsar=1
```

## Loudness normalisation to −16 LUFS

```bash
ffmpeg -i in.mp3 -af "loudnorm=I=-16:TP=-1.5:LRA=11" -c:a libmp3lame -b:a 128k out.mp3
```

## Speed without pitch shift

`atempo` accepts 0.5–2.0 per stage; chain for more:

```bash
ffmpeg -i in.mp3 -filter:a "atempo=1.25" -c:a libmp3lame out.mp3
```

## Contact sheet from a video

```bash
vidkit.py sheet in.mp4 -o sheet.jpg --cols 4 --count 8    # evenly spaced + timestamps
```

## GIF that doesn't look like 2005

```bash
ffmpeg -ss 1 -t 3 -i in.mp4 -vf "fps=12,scale=480:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse" -loop 0 out.gif
```

## Gotchas

- Always end filter chains with `format=yuv420p` — otherwise Safari/mobile refuse the file.
- Keep all dimensions even; H.264 rejects odd values.
- `-crf 21` + `-preset veryfast` is the right quality/speed point for agent work.
- `-shortest` truncates the *video* to the *audio*; that is rarely what you want.
