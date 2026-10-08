import os, sys, time, requests, subprocess, asyncio

# Environment variables passed from GH Actions
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")
WORKER_ID = os.environ.get("WORKER_ID", "unknown")
VOICE = "en-US-AndrewMultilingualNeural"
CHUNK_SIZE = 5000
BATCH_SIZE = 5
BATCH_PAUSE = 3

def supabase_rpc(function_name, payload):
    """Make a Supabase RPC call and return parsed JSON or None on failure."""
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        print("❌ Supabase URL or secret key not set")
        return None
    url = f"{SUPABASE_URL}/rest/v1/rpc/{function_name}"
    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        # Check if response is JSON
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        else:
            # Not JSON, print status and first 150 chars of body
            print(f"❌ Supabase RPC {function_name} returned non-JSON: {r.status} {r.text[:150]}")
            return None
    except Exception as e:
        print(f"❌ Supabase RPC {function_name} request failed: {e}")
        return None

def claim_batch():
    """Claim a batch of chunks via Supabase RPC."""
    result = supabase_rpc("claim_batch", {"p_count": BATCH_SIZE})
    if result is None:
        return None
    # The RPC returns either:
    #   {"chunks": [...], "total": N}   -> normal batch
    #   {"done": true}                  -> no more work
    #   {"chunks": [], "total": N, "waiting": true} -> wait
    return result

def mark_done_batch(chunks):
    """Mark chunks as done via Supabase RPC."""
    supabase_rpc("done_batch", {"p_chunks": chunks})

def release_batch(chunks):
    """Release chunks back to the pool via Supabase RPC."""
    for c in chunks:
        supabase_rpc("release_chunk", {"p_chunk": c})

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

async def main():
    if not os.path.exists("input.txt"):
        print("❌ input.txt not found")
        sys.exit(1)

    with open("input.txt", "r", encoding="utf-8") as f:
        text = f.read().strip()

    all_chunks = [text[i:i+CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]

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
            # Either waiting or empty batch; treat as no work and pause
            print("  ⏳ No chunks available (waiting or empty)")
            await asyncio.sleep(BATCH_PAUSE)
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