import asyncio, os, time, sys, re
import edge_tts

VOICE = "en-US-AndrewMultilingualNeural"
OUTPUT_SRT = "srt/subtitles.srt"
OUTPUT_DIR = "output_chunks"
INPUT_FILE = "input.txt"
WORDS_PER_LINE = 8
LINES_PER_ENTRY = 2
CONCURRENCY = 8
MAX_RETRIES = 3
GAP_MS = 0
AVG_MS_PER_WORD = 350
YELLOW = "#FFFF00"

def fmt_time(ms):
    ms = max(0, int(ms))
    h = ms // 3_600_000; ms -= h * 3_600_000
    m = ms // 60_000; ms -= m * 60_000
    s = ms // 1_000; ms -= s * 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def get_real_duration_ms(file_path):
    if not os.path.exists(file_path):
        return None
    try:
        from pydub import AudioSegment
        return len(AudioSegment.from_file(file_path))
    except Exception:
        return None

async def collect_sentence_events(text, semaphore, idx):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with semaphore:
                sentence_events = []
                current_sentence_words = []
                current_sentence_data = None
                communicate = edge_tts.Communicate(text, VOICE)
                async for item in communicate.stream():
                    if item.get("type") == "SentenceBoundary":
                        if current_sentence_data:
                            current_sentence_data["word_events"] = current_sentence_words
                            sentence_events.append(current_sentence_data)
                            current_sentence_words = []
                        current_sentence_data = {
                            "offset_ms": item["offset"] // 10_000,
                            "duration_ms": item["duration"] // 10_000,
                            "tts_text": item.get("text", "").strip(),
                            "word_events": []
                        }
                    elif item.get("type") == "WordBoundary" and current_sentence_data:
                        current_sentence_words.append({
                            "offset_ms": item["offset"] // 10_000,
                            "duration_ms": item["duration"] // 10_000,
                            "text": item.get("text", "").strip()
                        })
                if current_sentence_data:
                    current_sentence_data["word_events"] = current_sentence_words
                    sentence_events.append(current_sentence_data)
                return (idx, sentence_events, text)
        except Exception as e:
            if attempt == MAX_RETRIES:
                print(f"  Warning: Chunk {idx+1} failed: {e}")
                return (idx, [], text)
            await asyncio.sleep(2 ** attempt)

def slice_original_words(chunk_text, tts_sentences):
    original_words = chunk_text.split()
    sentence_word_counts = [len(s.split()) for s in tts_sentences]
    total_tts_words = sum(sentence_word_counts)
    if total_tts_words == 0 or total_tts_words != len(original_words):
        return [s.split() for s in tts_sentences]
    sliced = []
    cursor = 0
    for count in sentence_word_counts:
        sliced.append(original_words[cursor: cursor + count])
        cursor += count
    return sliced

async def main():
    t0 = time.time()
    # Handle plain SRT for burn-in mode
    srt_plain = os.environ.get("SRT_PLAIN") == "1"

    test_comm = edge_tts.Communicate("Hello world. This is a test.", VOICE)
    test_evts = [i async for i in test_comm.stream() if i.get("type") == "SentenceBoundary"]
    if test_evts:
        print("  SentenceBoundary working")
    else:
        print("  ERROR: No SentenceBoundary events")
        return

    print("  Reading input text and chunks...")
    if not os.path.exists(INPUT_FILE):
        print(f"  ERROR: {INPUT_FILE} not found")
        return
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        full_text = f.read().strip()

    # Slice text into 5000-char chunks matching the process job
    all_chunks_text = [full_text[i:i+5000] for i in range(0, len(full_text), 5000)]

    chunks = []
    if not os.path.exists(OUTPUT_DIR):
        print(f"  ERROR: Directory {OUTPUT_DIR} not found")
        return
    for f in sorted(os.listdir(OUTPUT_DIR)):
        if f.startswith("chunk_") and f.endswith(".mp3"):
            idx = int(f.split("_")[1].split(".")[0])
            if idx < len(all_chunks_text):
                chunks.append((idx, all_chunks_text[idx]))
            else:
                print(f"  Warning: Chunk index {idx} out of range for input text")
    chunks.sort(key=lambda x: x[0])

    if not chunks:
        print("  ERROR: No chunk files found")
        return

    total = len(chunks)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    done = 0
    failed = 0
    results = []

    async def tracked(chunk, i):
        nonlocal done, failed
        idx, text = chunk
        result = await collect_sentence_events(text, semaphore, idx)
        done += 1
        if not result[1]:
            failed += 1
        pct = done / total
        bar_len = 20
        filled = int(bar_len * pct)
        bar = "#" * filled + "." * (bar_len - filled)
        sys.stdout.write(f"\r  [{bar}] {done}/{total}  {pct*100:.1f}%")
        sys.stdout.flush()
        results.append(result)
        return result

    tasks = [tracked(chunk, i) for i, chunk in enumerate(chunks)]
    await asyncio.gather(*tasks)
    print()
    results.sort(key=lambda x: x[0])

    chunks_by_idx = dict(chunks)
    entries = []
    entry_id = 1
    clock_ms = 0.0
    words_per_entry_max = WORDS_PER_LINE * LINES_PER_ENTRY
    for idx, evts, text in results:
        chunk_text = chunks_by_idx[idx]
        real_file = f"{OUTPUT_DIR}/chunk_{idx:05d}.mp3"
        real_duration_ms = get_real_duration_ms(real_file)
        if real_duration_ms is None:
            print(f"  Warning: Chunk {idx}: missing - using estimate")
            real_duration_ms = max(len(chunk_text.split()) * AVG_MS_PER_WORD, 500)
        if not evts:
            clock_ms += real_duration_ms + GAP_MS
            continue
        stream_total_ms = evts[-1]["offset_ms"] + evts[-1]["duration_ms"]
        scale = (real_duration_ms / stream_total_ms) if stream_total_ms > 0 else 1.0
        sentence_word_lists = slice_original_words(chunk_text, [e["tts_text"] for e in evts])
        for e, word_list in zip(evts, sentence_word_lists):
            if not word_list:
                continue
            s_start = clock_ms + e["offset_ms"] * scale
            s_end = clock_ms + (e["offset_ms"] + e["duration_ms"]) * scale
            s_dur = max(s_end - s_start, 1.0)
            i = 0
            while i < len(word_list):
                group = word_list[i: i + words_per_entry_max]
                frac_start = i / len(word_list)
                frac_end = min((i + len(group)) / len(word_list), 1.0)
                group_start = s_start + frac_start * s_dur
                group_end = s_start + frac_end * s_dur
                lines = []
                for ln in range(0, len(group), WORDS_PER_LINE):
                    line_text = " ".join(group[ln: ln + WORDS_PER_LINE])
                    line_text = line_text.replace("<", "&lt;").replace(">", "&gt;")
                    if srt_plain:
                        lines.append(line_text)
                    else:
                        lines.append(f'<font color="{YELLOW}">{line_text}</font>')
                entries.append(
                    f"{entry_id}\n{fmt_time(group_start)} --> {fmt_time(group_end)}\n"
                    + "\n".join(lines) + "\n"
                )
                entry_id += 1
                i += len(group)
        clock_ms += real_duration_ms + GAP_MS

    with open(OUTPUT_SRT, "w", encoding="utf-8") as f:
        f.write("\n".join(entries))
    elapsed = time.time() - t0
    n_entries = len(entries)
    print(f"  Done in {elapsed:.1f}s | {n_entries} entries")
    if failed:
        print(f"  Warning: {failed} chunks used estimates")

if __name__ == "__main__":
    asyncio.run(main())
