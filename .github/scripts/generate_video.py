import os, sys, subprocess, random

def main():
    try:
        AUDIO_DURATION = float(os.environ.get("AUDIO_DURATION", 0))
        RESOLUTION = os.environ.get("RESOLUTION", "720")
    except ValueError:
        print("❌ Invalid AUDIO_DURATION")
        sys.exit(1)

    audio_files = [os.path.join('audio', f) for f in os.listdir('audio') if f.endswith('.mp3')]
    if not audio_files:
        print("❌ No audio file found")
        sys.exit(1)
    AUDIO_FILE = audio_files[0]

    video_dir = "source_videos"
    videos = [os.path.join(video_dir, f) for f in os.listdir(video_dir)
             if f.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm'))]

    if not videos:
        print("❌ No video files found")
        sys.exit(1)

    print(f"🎬 Found {len(videos)} source videos")
    print(f"🎵 Audio duration: {AUDIO_DURATION}s")
    print(f"📐 Resolution: {RESOLUTION}")

    video_info = []
    for v in videos:
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', v],
                capture_output=True, text=True, timeout=10
            )
            duration = float(result.stdout.strip())
            video_info.append({'path': v, 'duration': duration})
            print(f"  📹 {os.path.basename(v)}: {duration:.1f}s")
        except Exception as e:
            print(f"  ⚠️  Skipping {v}: {e}")

    sequence = []
    total_duration = 0.0
    while total_duration < AUDIO_DURATION:
        vid = random.choice(video_info)
        sequence.append(vid)
        total_duration += vid['duration']

    print(f"\n🎲 Random sequence: {len(sequence)} clips, total {total_duration:.1f}s")

    with open('video_concat.txt', 'w') as f:
        for vid in sequence:
            f.write(f"file '{vid['path']}'\n")

    scale_map = {
        "original": "",
        "1080": "-vf scale=-2:1080",
        "720": "-vf scale=-2:720",
        "480": "-vf scale=-2:480",
        "360": "-vf scale=-2:360",
        "240": "-vf scale=-2:240",
        "144": "-vf scale=-2:144",
    }
    scale_filter = scale_map.get(RESOLUTION, "-vf scale=-2:720")

    print("\n🔗 Concatenating videos...")
    concat_cmd = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', 'video_concat.txt']
    if scale_filter:
        concat_cmd.extend(scale_filter.split())
    concat_cmd.extend(['-c:v', 'libx264', '-preset', 'fast', '-t', str(AUDIO_DURATION), 'temp_video.mp4'])
    subprocess.run(concat_cmd, check=True)

    print("🎵 Merging audio...")
    subprocess.run([
        'ffmpeg', '-y',
        '-i', 'temp_video.mp4',
        '-i', AUDIO_FILE,
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-map', '0:v:0',
        '-map', '1:a:0',
        '-shortest',
        'output/final.mp4'
    ], check=True)

    size_mb = os.path.getsize('output/final.mp4') / (1024 * 1024)
    print(f"\n📦 Video size: {size_mb:.1f} MB")

    if size_mb > 1800:
        print("⚠️  Size > 1.8GB, compressing...")
        subprocess.run([
            'ffmpeg', '-y',
            '-i', 'output/final.mp4',
            '-c:v', 'libx264',
            '-crf', '28',
            '-preset', 'fast',
            '-c:a', 'aac',
            '-b:a', '96k',
            'output/final_compressed.mp4'
        ], check=True)
        os.remove('output/final.mp4')
        os.rename('output/final_compressed.mp4', 'output/final.mp4')
        new_size = os.path.getsize('output/final.mp4') / (1024 * 1024)
        print(f"✅ Compressed to {new_size:.1f} MB")

    print("✅ Video generation complete")

if __name__ == "__main__":
    main()
