import os, sys, time, requests, subprocess, asyncio

# Environment variables passed from GH Actions
COORD_URL = os.environ.get("COORD_URL", "")
COORD_SECRET = os.environ.get("COORD_SECRET", "")
WORKER_ID = os.environ.get("WORKER_ID", "unknown")
VOICE = "en-US-AndrewMultilingualNeural"
CHUNK_SIZE = 5000
BATCH_SIZE = 5
BATCH_PAUSE = 3

async def main():
    if not os.path.exists("input.txt"):
        print("❌ input.txt not found")
        sys.exit(1)

    with open("input.txt", "r", encoding="utf-8") as f:
        text = f.read().strip()

    all_chunks = [text[i:i+CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]

    def claim_batch():
        try:
            r = requests.post(f"{COORD_URL}/claim-batch?count={BATCH_SIZE}",
                               headers={"x-auth": COORD_SECRET}, timeout=10)
            return r.json()
        except Exception as e:
            print(f"❌ Claim error: {e}")
            return None

    def mark_done_batch(chunks):
        try:
            requests.post(f"{COORD_URL}/done-batch",
                               headers={"x-auth": COORD_SECRET},
                               json={"chunks": chunks}, timeout=10)
        except Exception as e:
            print(f"❌ Mark done error: {e}")

    def release_batch(chunks):
        for c in chunks:
            try:
                requests.post(f"{COORD_URL}/release",
                               headers={"x-auth": COORD_SECRET},
                               json={"chunk": c}, timeout=10)
            except: pass

    async def process_chunk(idx, chunk_text):
        out_path = f"chunks/chunk_{str(idx).zfill(5)}.mp3"
        for attempt in range(3):
            try:
                cmd = ["edge-tts", "--voice", VOICE, "--text", chunk_text, "--write-media", out_path]
                proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await proc.communicate()
                if proc.returncode == 0 and os.path.exists(out_path):
                    return idx
            except Exception as e:
                print(f"  ❌ Chunk {idx} attempt {attempt+1} failed: {e}")
            await asyncio.sleep(2)
        return None

    print(f"👷 Worker {WORKER_ID} starting...")
    batch_count = 0

    while True:
        claim = claim_batch()
        if not claim:
            await asyncio.sleep(2)
            continue

        if claim.get("done"):
            print("🏁 All chunks claimed!")
            break

        chunk_ids = claim.get("chunks", [])
        if not chunk_ids:
            await asyncio.sleep(2)
            continue

        batch_count += 1
        print(f"\n📦 Batch {batch_count}: claimed chunks {chunk_ids}")

        tasks = [process_chunk(idx, all_chunks[idx]) for idx in chunk_ids if idx < len(all_chunks)]
        results = await asyncio.gather(*tasks)

        successful = [r for r in results if r is not None]
        failed = [chunk_ids[i] for i, r in enumerate(results) if r is None]

        if successful:
            mark_done_batch(successful)
            print(f"  ✅ Done: {len(successful)} chunks")

        if failed:
            release_batch(failed)
            print(f"  ⚠️  Released {len(failed)} failed chunks back to pool")

        print(f"  ⏸️  Pausing {BATCH_PAUSE}s...")
        await asyncio.sleep(BATCH_PAUSE)

if __name__ == "__main__":
    asyncio.run(main())
