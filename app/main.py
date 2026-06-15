import asyncio
import logging
import os
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel




from .database import SHEETS_DIR, UPLOAD_DIR, get_db, init_db
from .drive_service import upload_to_drive
from .gemini_service import generate_battle_card
from .image_processing import apply_text_overlay, generate_ad_card, generate_card_sheet, get_template_bytes
from .naming import generate_card_name
from .vision_service import validate_face

logger = logging.getLogger("uvicorn.error")

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
# 1.5 POST /api/validate  — Cloud Vision face validation
# ---------------------------------------------------------------------
@app.post("/api/validate")
async def validate_image(
    image: UploadFile = File(...),
):
    """Validate that the uploaded image has exactly one front-facing person.

    Uses Google Cloud Vision Face Detection.
    Returns {"valid": true} or {"valid": false, "error": "..."}.
    """
    contents = await image.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="ファイルサイズが5MBを超えています")

    result = await asyncio.get_event_loop().run_in_executor(
        None, validate_face, contents
    )

    if result.valid:
        return {"valid": True}
    return {"valid": False, "error": result.error}


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
    ad_message: str = ""
    ad_store_name: str = ""
    ad_company_name: str = ""


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
            ad_message=req.ad_message,
            ad_store_name=req.ad_store_name,
            ad_company_name=req.ad_company_name,
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
    ad_message: str = "",
    ad_store_name: str = "",
    ad_company_name: str = "",
) -> bool:
    """Generate a single card. Returns True if successful."""
    # Card 6 with advertise=True: generate ad card with rotation + text overlay
    if advertise and card_idx == 6:
        try:
            ad_bytes = await asyncio.get_event_loop().run_in_executor(
                None,
                generate_ad_card,
                ad_message,
                ad_store_name,
                ad_company_name,
            )
            out_path = os.path.join(generated_dir, f"{job_id}_{card_idx}.png")
            with open(out_path, "wb") as f:
                f.write(ad_bytes)
            with get_db() as db:
                db.execute(
                    "INSERT INTO generated_images (job_id, idx, file_path) VALUES (?, ?, ?)",
                    (job_id, card_idx, out_path),
                )
            return True
        except Exception as e:
            logger.error("[GENERATE] Ad card generation failed: %s", e)
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
    ad_message: str = "",
    ad_store_name: str = "",
    ad_company_name: str = "",
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
                ad_message=ad_message,
                ad_store_name=ad_store_name,
                ad_company_name=ad_company_name,
            )
            tasks.append(task)

        # Update progress as each card completes
        completed = 0
        for coro in asyncio.as_completed(tasks):
            try:
                await coro
            except Exception as e:
                print(f"Card generation error for job {job_id}: {e}")
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
    ad_message: str = ""
    ad_store_name: str = ""
    ad_company_name: str = ""


@app.post("/api/finalize")
async def finalize(req: FinalizeRequest):
    print(f"[FINALIZE] job_id={req.job_id}, first_name={req.first_name}")  # ← この行を追加

    """Apply naming logic and text overlay to generated images.

    This endpoint should be called after AI generation is complete and
    the user has entered their name. It reads the generated images,
    applies a battle card name (from the naming logic) and location text
    overlay to each card, and saves the finalized images.

    The finalized images replace the original generated images, so the
    same /api/images/{job_id}/{index} endpoint serves the final result.
    """
    logger.info("[FINALIZE] job_id=%s, first_name=%s", req.job_id, req.first_name)

    # Atomically claim the job to prevent concurrent finalization (TOCTOU guard)
    # Use a short DB transaction — release connection before any long work.
    claimed = False
    current_status = None
    is_advertise = False
    with get_db() as db:
        cursor = db.execute(
            "UPDATE jobs SET status = 'finalizing' WHERE id = ? AND status = 'completed'",
            (req.job_id,),
        )
        if cursor.rowcount > 0:
            claimed = True
        else:
            job = db.execute(
                "SELECT status FROM jobs WHERE id = ?",
                (req.job_id,),
            ).fetchone()
            if not job:
                logger.error("[FINALIZE] Job not found: %s", req.job_id)
                raise HTTPException(status_code=404, detail="ジョブが見つかりません")
            current_status = dict(job)['status']
        # Fetch advertise flag for card 6 handling
        ad_row = db.execute(
            "SELECT advertise FROM jobs WHERE id = ?",
            (req.job_id,),
        ).fetchone()
        if ad_row:
            is_advertise = bool(dict(ad_row)['advertise'])

    # --- DB connection is now closed — handle non-claimed cases outside the transaction ---

    if not claimed:
        # If already finalizing, wait for it to complete instead of returning 400
        if current_status == 'finalizing':
            logger.info("[FINALIZE] Job %s already finalizing, waiting for completion...", req.job_id)
            for _ in range(120):  # wait up to 60 seconds
                await asyncio.sleep(0.5)
                with get_db() as poll_db:
                    poll_job = poll_db.execute(
                        "SELECT status FROM jobs WHERE id = ?",
                        (req.job_id,),
                    ).fetchone()
                    poll_status = dict(poll_job)['status'] if poll_job else None
                if poll_status == 'finalized':
                    with get_db() as result_db:
                        images = result_db.execute(
                            "SELECT idx, file_path FROM generated_images WHERE job_id = ? ORDER BY idx",
                            (req.job_id,),
                        ).fetchall()
                    image_urls = [f"/api/images/{req.job_id}/{dict(img)['idx']}" for img in images]
                    logger.info("[FINALIZE] Job %s finalization completed (waited)", req.job_id)
                    result: dict = {
                        "status": "finalized",
                        "finalized_count": len(images),
                        "images": image_urls,
                    }
                    sheet_file = os.path.join(SHEETS_DIR, f"{req.job_id}_sheet.jpg")
                    if os.path.exists(sheet_file):
                        result["card_sheet_url"] = f"/api/card-sheet/{req.job_id}"
                    return result
                if poll_status == 'completed':
                    # First attempt failed and reverted — let caller retry
                    logger.info("[FINALIZE] Job %s reverted to completed, retrying...", req.job_id)
                    break
            else:
                logger.error("[FINALIZE] Timed out waiting for job %s to finish finalizing", req.job_id)
                raise HTTPException(status_code=409, detail="Finalize処理がタイムアウトしました。もう一度お試しください。")

            # Retry claiming after the first attempt reverted
            with get_db() as db:
                cursor = db.execute(
                    "UPDATE jobs SET status = 'finalizing' WHERE id = ? AND status = 'completed'",
                    (req.job_id,),
                )
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=409, detail="Finalize処理の再取得に失敗しました。もう一度お試しください。")

        # If already finalized, return success with existing images
        elif current_status == 'finalized':
            logger.info("[FINALIZE] Job %s already finalized, returning existing result", req.job_id)
            with get_db() as result_db:
                images = result_db.execute(
                    "SELECT idx, file_path FROM generated_images WHERE job_id = ? ORDER BY idx",
                    (req.job_id,),
                ).fetchall()
            image_urls = [f"/api/images/{req.job_id}/{dict(img)['idx']}" for img in images]
            result: dict = {
                "status": "finalized",
                "finalized_count": len(images),
                "images": image_urls,
            }
            sheet_file = os.path.join(SHEETS_DIR, f"{req.job_id}_sheet.jpg")
            if os.path.exists(sheet_file):
                result["card_sheet_url"] = f"/api/card-sheet/{req.job_id}"
            return result
        else:
            logger.error("[FINALIZE] Job %s has status '%s', expected 'completed'", req.job_id, current_status)
            raise HTTPException(
                status_code=400,
                detail=f"ジョブがまだ完了していないか、既にfinalize済みです (status: {current_status})",
            )

    # Track temp files so we can clean up on failure
    temp_files: list[tuple[str, str]] = []  # (temp_path, original_path)

    try:
        with get_db() as db:
            images = db.execute(
                "SELECT idx, file_path FROM generated_images WHERE job_id = ? ORDER BY idx",
                (req.job_id,),
            ).fetchall()

        if not images:
            logger.error("[FINALIZE] No generated images for job %s", req.job_id)
            raise HTTPException(status_code=400, detail="生成された画像がありません")

        # Phase 1: Apply text overlay and write to temp files (originals untouched)
        for img_row in images:
            img_dict = dict(img_row)
            card_idx = img_dict["idx"]
            filepath = img_dict["file_path"]

            if not os.path.exists(filepath):
                continue

            # Read the original generated image
            with open(filepath, "rb") as f:
                image_bytes = f.read()

            # Card 6 (ad card): apply ad text overlay instead of battle card overlay
            if card_idx == 6 and is_advertise:
                finalized_bytes = await asyncio.get_event_loop().run_in_executor(
                    None,
                    generate_ad_card,
                    req.ad_message,
                    req.ad_store_name,
                    req.ad_company_name,
                )
            else:
                # Generate battle card name from the naming convention
                card_name = generate_card_name(
                    req.first_name,
                    card_idx,
                    seed=f"{req.job_id}-{card_idx}",
                )
                # Apply text overlay (card name fills the full banner)
                finalized_bytes = await asyncio.get_event_loop().run_in_executor(
                    None,
                    apply_text_overlay,
                    image_bytes,
                    card_name.display,
                    card_idx,
                    card_name.ruby_target,
                    card_name.ruby_reading,
                )

            # Write to temp file (original is preserved)
            temp_path = filepath + ".finalized"
            with open(temp_path, "wb") as f:
                f.write(finalized_bytes)
            temp_files.append((temp_path, filepath))

        # Phase 2: All overlays succeeded — atomically replace originals
        for temp_path, original_path in temp_files:
            os.replace(temp_path, original_path)

        # Phase 2.5: Generate the card sheet (all 6 cards composited)
        try:
            card_bytes_map: dict[int, bytes] = {}
            for img_row in images:
                img_dict = dict(img_row)
                fpath = img_dict["file_path"]
                if os.path.exists(fpath):
                    with open(fpath, "rb") as f:
                        card_bytes_map[img_dict["idx"]] = f.read()
            if card_bytes_map:
                sheet_bytes = await asyncio.get_event_loop().run_in_executor(
                    None, generate_card_sheet, card_bytes_map,
                )
                sheet_path = os.path.join(
                    SHEETS_DIR, f"{req.job_id}_sheet.jpg",
                )
                with open(sheet_path, "wb") as f:
                    f.write(sheet_bytes)
                logger.info("[FINALIZE] Card sheet generated: %s", sheet_path)
                # Upload to Google Drive (non-blocking, non-fatal)
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        upload_to_drive,
                        sheet_path,
                        f"{req.job_id}_sheet.jpg",
                    )
                except Exception as ue:
                    logger.warning("[FINALIZE] Drive upload failed (non-fatal): %s", ue)
        except Exception as e:
            logger.warning("[FINALIZE] Card sheet generation failed (non-fatal): %s", e)

        # Phase 3: Update job status
        with get_db() as db:
            db.execute(
                "UPDATE jobs SET name = ?, location = ?, status = 'finalized' WHERE id = ?",
                (req.first_name, req.location, req.job_id),
            )
    except HTTPException:
        # Clean up temp files and revert status so the user can retry
        for temp_path, _ in temp_files:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        with get_db() as db:
            db.execute(
                "UPDATE jobs SET status = 'completed' WHERE id = ?",
                (req.job_id,),
            )
        raise
    except Exception:
        # Clean up temp files and revert status so the user can retry
        for temp_path, _ in temp_files:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        with get_db() as db:
            db.execute(
                "UPDATE jobs SET status = 'completed' WHERE id = ?",
                (req.job_id,),
            )
        raise HTTPException(status_code=500, detail="Finalization failed")

    image_urls = [f"/api/images/{req.job_id}/{dict(img)['idx']}" for img in images]

    result: dict = {
        "status": "finalized",
        "finalized_count": len(temp_files),
        "images": image_urls,
    }
    sheet_file = os.path.join(SHEETS_DIR, f"{req.job_id}_sheet.jpg")
    if os.path.exists(sheet_file):
        result["card_sheet_url"] = f"/api/card-sheet/{req.job_id}"
    return result


# ---------------------------------------------------------------------
# 6. GET /api/card-sheet/{job_id} — Serve the composited card sheet
# ---------------------------------------------------------------------
@app.get("/api/card-sheet/{job_id}")
async def get_card_sheet(job_id: str):
    sheet_path = os.path.join(SHEETS_DIR, f"{job_id}_sheet.jpg")
    if not os.path.exists(sheet_path):
        raise HTTPException(status_code=404, detail="カードシートが見つかりません")
    return FileResponse(sheet_path, media_type="image/jpeg")


#　テスト
@app.get("/test")
async def test():
    return {"message": "hello world"}

@app.get("/")
def root():
    return {"message": "hello"}