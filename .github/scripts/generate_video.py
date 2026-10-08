import os, sys, subprocess, random, time
from collections import defaultdict

def main():
    try:
        AUDIO_DURATION = float(os.environ.get("AUDIO_DURATION", 0))
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

    # --- Clip Matching System ---
    t_start = time.time()
    video_signatures = defaultdict(list)

    print("\n🔍 Analyzing video signatures for stream copy compatibility...")
    for v in videos:
        try:
            # Extract codec, width, height, pix_fmt, and frame rate
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                 '-show_entries', 'stream=codec_name,width,height,pix_fmt,r_frame_rate',
                 '-of', 'csv=p=0', v],
                capture_output=True, text=True, timeout=10
            )
            sig = result.stdout.strip()
            if sig:
                video_signatures[sig].append(v)
        except Exception as e:
            print(f"  ⚠️  Skipping {v}: {e}")

    if not video_signatures:
        print("❌ No valid video signatures found")
        sys.exit(1)

    # Keep the largest group of matching clips
    best_sig = max(video_signatures, key=lambda k: len(video_signatures[k]))
    kept_videos = video_signatures[best_sig]

    skipped_count = 0
    for sig, paths in video_signatures.items():
        if sig != best_sig:
            for p in paths:
                print(f"  ⚠️  Skipping {os.path.basename(p)}: signature mismatch")
                skipped_count += 1

    print(f"\n✅ Kept {len(kept_videos)} clips | Skipped {skipped_count} clips")

    # Build sequence
    sequence = []
    total_duration = 0.0

    # Need durations for the kept videos
    video_info = []
    for v in kept_videos:
        try:
            res = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', v],
                capture_output=True, text=True, timeout=10
            )
            video_info.append({'path': v, 'duration': float(res.stdout.strip())})
        except:
            print(f"  ⚠️  Skipping {v}: could not read duration")

    if not video_info:
        print("❌ No valid video clips with durations found")
        sys.exit(1)

    while total_duration < AUDIO_DURATION:
        vid = random.choice(video_info)
        sequence.append(vid['path'])
        total_duration += vid['duration']

    print(f"\n🎲 Random sequence: {len(sequence)} clips, total {total_duration:.1f}s")

    with open('video_concat.txt', 'w') as f:
        for path in sequence:
            f.write(f"file '{path}'\n")

    # --- Single-Pass Stream Copy Merge ---
    print("\n🔗 Merging audio and video (Stream Copy)...")
    os.makedirs('output', exist_ok=True)

    # -c:v copy: no re-encode video
    # -c:a copy: no re-encode audio (mp3 to mkv is fine)
    # -avoid_negative_ts make_zero: ensures timestamps start at 0 for concat
    merge_cmd = [
        'ffmpeg', '-y',
        '-f', 'concat', '-safe', '0', '-i', 'video_concat.txt',
        '-i', AUDIO_FILE,
        '-map', '0:v:0',
        '-map', '1:a:0',
        '-c:v', 'copy',
        '-c:a', 'copy',
        '-t', str(AUDIO_DURATION),
        '-avoid_negative_ts', 'make_zero',
        'output/final.mp4'
    ]

    subprocess.run(merge_cmd, check=True)

    t_end = time.time()
    size_mb = os.path.getsize('output/final.mkv') / (1024 * 1024)

    print(f"\n📦 Final Video: output/final.mkv")
    print(f"⏱️  Elapsed: {t_end - t_start:.2f}s")
    print(f"📏 Size: {size_mb:.1f} MB")
    if size_mb > 1800:
        print("⚠️  Warning: File size exceeds 1.8GB")

    print("✅ Video generation complete")

if __name__ == "__main__":
    main()
