import asyncio
import os
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .database import UPLOAD_DIR, get_db, init_db
from .gemini_service import generate_battle_card
from .image_processing import apply_text_overlay, get_template_bytes
from .naming import generate_card_name

app = FastAPI()

# Disable CORS. Do not remove this for full-stack development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5MB

# In-memory job tracking for background tasks
_active_jobs: dict[str, asyncio.Task] = {}


@app.on_event("startup")
async def startup():
    init_db()
    os.makedirs(os.path.join(UPLOAD_DIR, "generated"), exist_ok=True)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# ---------------------------------------------------------------------
# 1. POST /api/session
# ---------------------------------------------------------------------
@app.post("/api/session")
async def create_session():
    session_id = str(uuid.uuid4())
    with get_db() as db:
        db.execute("INSERT INTO sessions (id) VALUES (?)", (session_id,))
    return {"session_id": session_id}


# ---------------------------------------------------------------------
# 2. POST /api/upload
# ---------------------------------------------------------------------
@app.post("/api/upload")
async def upload_image(
    image: UploadFile = File(...),
    index: int = Form(...),
    session_id: str = Form(...),
):
    with get_db() as db:
        row = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="セッションが見つかりません")

    if index < 1 or index > 6:
        raise HTTPException(status_code=400, detail="indexは1〜6の範囲で指定してください")

    contents = await image.read()
    if len(contents) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="ファイルサイズが5MBを超えています")

    mime_type = image.content_type or "image/jpeg"
    ext = "png" if "png" in mime_type else "jpg"
    filename = f"{session_id}_{index}.{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(contents)

    with get_db() as db:
        db.execute(
            """
            INSERT INTO uploads (session_id, idx, file_path, mime_type)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id, idx) DO UPDATE SET
                file_path = excluded.file_path,
                mime_type = excluded.mime_type,
                created_at = CURRENT_TIMESTAMP
            """,
            (session_id, index, filepath, mime_type),
        )

    return {"status": "ok"}


# ---------------------------------------------------------------------
# 3. POST /api/generate
# ---------------------------------------------------------------------
class GenerateRequest(BaseModel):
    session_id: str
    name: str = ""
    location: str = ""
    advertise: bool = False
    mode: str = "single"


@app.post("/api/generate")
async def generate(req: GenerateRequest):
    with get_db() as db:
        row = db.execute("SELECT id FROM sessions WHERE id = ?", (req.session_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="セッションが見つかりません")

    if req.mode not in ("single", "multi"):
        raise HTTPException(status_code=400, detail="modeは 'single' または 'multi' を指定してください")

    with get_db() as db:
        uploads = db.execute(
            "SELECT idx, file_path, mime_type FROM uploads WHERE session_id = ? ORDER BY idx",
            (req.session_id,),
        ).fetchall()

    if not uploads:
        raise HTTPException(status_code=400, detail="アップロードされた画像がありません")

    job_id = str(uuid.uuid4())
    with get_db() as db:
        db.execute(
            """
            INSERT INTO jobs (id, session_id, name, location, advertise, mode, status, progress)
            VALUES (?, ?, ?, ?, ?, ?, 'processing', 0)
            """,
            (job_id, req.session_id, req.name, req.location, int(req.advertise), req.mode),
        )

    task = asyncio.create_task(
        _run_generation(
            job_id=job_id,
            session_id=req.session_id,
            name=req.name,
            location=req.location,
            advertise=req.advertise,
            mode=req.mode,
            uploads=[(dict(u)["idx"], dict(u)["file_path"], dict(u)["mime_type"]) for u in uploads],
        )
    )
    _active_jobs[job_id] = task

    return {"job_id": job_id}


async def _generate_single_card(
    job_id: str,
    card_idx: int,
    total_cards: int,
    name: str,
    location: str,
    advertise: bool,
    mode: str,
    uploads: list[tuple[int, str, str]],
    generated_dir: str,
) -> bool:
    """Generate a single card. Returns True if successful."""
    # Card 6 with advertise=True: use the uploaded ad image directly
    if advertise and card_idx == 6:
        ad_upload = None
        for idx, fpath, mtype in uploads:
            if idx == 6:
                ad_upload = (fpath, mtype)
                break

        if ad_upload:
            out_path = os.path.join(generated_dir, f"{job_id}_{card_idx}.png")
            with open(ad_upload[0], "rb") as src:
                with open(out_path, "wb") as dst:
                    dst.write(src.read())

            with get_db() as db:
                db.execute(
                    "INSERT INTO generated_images (job_id, idx, file_path) VALUES (?, ?, ?)",
                    (job_id, card_idx, out_path),
                )
            return True
        return False

    # Determine source image
    if mode == "single":
        source_path, source_mime = uploads[0][1], uploads[0][2]
    else:
        source_upload = None
        for idx, fpath, mtype in uploads:
            if idx == card_idx:
                source_upload = (fpath, mtype)
                break
        if source_upload is None:
            source_upload = (uploads[0][1], uploads[0][2])
        source_path, source_mime = source_upload

    with open(source_path, "rb") as f:
        image_bytes = f.read()

    # Load template image for this card
    template_bytes = get_template_bytes(card_idx)

    # Generate via Gemini (run in thread to avoid blocking)
    result_bytes = await asyncio.get_event_loop().run_in_executor(
        None,
        generate_battle_card,
        image_bytes,
        source_mime,
        name,
        location,
        card_idx,
        total_cards if not advertise else total_cards - 1,
        template_bytes,
    )

    if result_bytes:
        out_path = os.path.join(generated_dir, f"{job_id}_{card_idx}.png")
        with open(out_path, "wb") as f:
            f.write(result_bytes)

        with get_db() as db:
            db.execute(
                "INSERT INTO generated_images (job_id, idx, file_path) VALUES (?, ?, ?)",
                (job_id, card_idx, out_path),
            )
        return True

    return False


async def _run_generation(
    job_id: str,
    session_id: str,
    name: str,
    location: str,
    advertise: bool,
    mode: str,
    uploads: list[tuple[int, str, str]],
) -> None:
    """Background task: generate 6 battle card images concurrently."""
    total_cards = 6
    generated_dir = os.path.join(UPLOAD_DIR, "generated")
    os.makedirs(generated_dir, exist_ok=True)

    try:
        # Launch all card generation tasks concurrently
        tasks = []
        for card_idx in range(1, total_cards + 1):
            task = _generate_single_card(
                job_id=job_id,
                card_idx=card_idx,
                total_cards=total_cards,
                name=name,
                location=location,
                advertise=advertise,
                mode=mode,
                uploads=uploads,
                generated_dir=generated_dir,
            )
            tasks.append(task)

        # Update progress as each card completes
        completed = 0
        for coro in asyncio.as_completed(tasks):
            await coro
            completed += 1
            progress = int(completed / total_cards * 100)
            with get_db() as db:
                db.execute("UPDATE jobs SET progress = ? WHERE id = ?", (progress, job_id))

        # Count successful generations
        with get_db() as db:
            generated_count = db.execute(
                "SELECT COUNT(*) FROM generated_images WHERE job_id = ?", (job_id,)
            ).fetchone()[0]

        # Mark completed or failed based on generation results
        if generated_count == 0:
            with get_db() as db:
                db.execute(
                    "UPDATE jobs SET status = 'failed', progress = 100, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (job_id,),
                )
        else:
            with get_db() as db:
                db.execute(
                    "UPDATE jobs SET status = 'completed', progress = 100, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (job_id,),
                )

    except Exception as e:
        print(f"Generation error for job {job_id}: {e}")
        with get_db() as db:
            db.execute("UPDATE jobs SET status = 'failed' WHERE id = ?", (job_id,))
    finally:
        _active_jobs.pop(job_id, None)


# ---------------------------------------------------------------------
# 4. GET /api/status
# ---------------------------------------------------------------------
@app.get("/api/status")
async def get_status(job_id: str):
    with get_db() as db:
        job = db.execute(
            "SELECT progress, status FROM jobs WHERE id = ?", (job_id,),
        ).fetchone()

        if not job:
            raise HTTPException(status_code=404, detail="ジョブが見つかりません")

        images = db.execute(
            "SELECT idx, file_path FROM generated_images WHERE job_id = ? ORDER BY idx",
            (job_id,),
        ).fetchall()

    image_urls = [f"/api/images/{job_id}/{dict(img)['idx']}" for img in images]
    job_dict = dict(job)

    return {
        "progress": job_dict["progress"],
        "images": image_urls,
        "status": job_dict["status"],
    }


# ---------------------------------------------------------------------
# Serve generated images
# ---------------------------------------------------------------------
@app.get("/api/images/{job_id}/{index}")
async def get_image(job_id: str, index: int):
    with get_db() as db:
        img = db.execute(
            "SELECT file_path FROM generated_images WHERE job_id = ? AND idx = ?",
            (job_id, index),
        ).fetchone()

    if not img:
        raise HTTPException(status_code=404, detail="画像が見つかりません")

    filepath = dict(img)["file_path"]
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="画像ファイルが見つかりません")

    return FileResponse(filepath, media_type="image/png")


# ---------------------------------------------------------------------
# 5. POST /api/finalize — Apply naming logic + text overlay
# ---------------------------------------------------------------------
class FinalizeRequest(BaseModel):
    job_id: str
    first_name: str
    location: str


@app.post("/api/finalize")
async def finalize(req: FinalizeRequest):
    """Apply naming logic and text overlay to generated images.

    This endpoint should be called after AI generation is complete and
    the user has entered their name. It reads the generated images,
    applies a battle card name (from the naming logic) and location text
    overlay to each card, and saves the finalized images.

    The finalized images replace the original generated images, so the
    same /api/images/{job_id}/{index} endpoint serves the final result.
    """
    with get_db() as db:
        job = db.execute(
            "SELECT status, name, location FROM jobs WHERE id = ?",
            (req.job_id,),
        ).fetchone()

    if not job:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")

    job_dict = dict(job)
    if job_dict["status"] != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"ジョブがまだ完了していません (status: {job_dict['status']})",
        )

    with get_db() as db:
        images = db.execute(
            "SELECT idx, file_path FROM generated_images WHERE job_id = ? ORDER BY idx",
            (req.job_id,),
        ).fetchall()

    if not images:
        raise HTTPException(status_code=400, detail="生成された画像がありません")

    # Apply text overlay to each generated image
    finalized_count = 0
    for img_row in images:
        img_dict = dict(img_row)
        card_idx = img_dict["idx"]
        filepath = img_dict["file_path"]

        if not os.path.exists(filepath):
            continue

        # Generate battle card name from naming logic
        card_name = generate_card_name(req.first_name, card_idx)

        # Read the generated image
        with open(filepath, "rb") as f:
            image_bytes = f.read()

        # Apply text overlay
        finalized_bytes = await asyncio.get_event_loop().run_in_executor(
            None,
            apply_text_overlay,
            image_bytes,
            card_name,
            req.location,
            card_idx,
        )

        # Overwrite with finalized image
        with open(filepath, "wb") as f:
            f.write(finalized_bytes)

        finalized_count += 1

    # Update job with name and location
    with get_db() as db:
        db.execute(
            "UPDATE jobs SET name = ?, location = ? WHERE id = ?",
            (req.first_name, req.location, req.job_id),
        )

    image_urls = [f"/api/images/{req.job_id}/{dict(img)['idx']}" for img in images]

    return {
        "status": "finalized",
        "finalized_count": finalized_count,
        "images": image_urls,
    }
