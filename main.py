import os
import logging
import json
from fastapi import FastAPI, Query, Request, Body
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from supabase import create_client, Client
from typing import List, Dict, Any
import uuid
from datetime import datetime
import asyncio

app = FastAPI()
templates = Jinja2Templates(directory="templates")

logging.basicConfig(level=logging.INFO)

supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    raise ValueError("SUPABASE_URL và SUPABASE_KEY phải được thiết lập trong biến môi trường.")

supabase: Client = create_client(supabase_url, supabase_key)

# Helper for bulk insert
async def bulk_insert_with_chunk(table_name: str, rows: List[Dict], chunk_size: int = 100):
    for i in range(0, len(rows), chunk_size):
        batch = rows[i:i + chunk_size]
        try:
            supabase.table(table_name).insert(batch).execute()
            logging.info(f"Inserted batch {i//chunk_size + 1}: {len(batch)} rows into {table_name}")
        except Exception as e:
            logging.error(f"Error inserting batch into {table_name}: {str(e)}")
            raise e
        await asyncio.sleep(0.1)  # Avoid rate limiting

# New endpoints for batch imports
@app.post("/import/groups")
async def import_groups_batch(data: Dict[str, Any] = Body(...)):
    try:
        import_user_id = data.get("user_id")
        group_records = data.get("groups", [])
        if not import_user_id or not group_records:
            return JSONResponse(status_code=400, content={"error": "Missing user_id or groups"})
        
        # Adjust IDs
        for group in group_records:
            group['id'] = str(uuid.uuid5(uuid.NAMESPACE_DNS, group['original_id'] + import_user_id))
            group['user_id'] = import_user_id
            del group['original_id']  # Clean up
        
        await bulk_insert_with_chunk('groups', group_records)
        return {"status": "success", "message": f"Inserted {len(group_records)} groups"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/import/lessons")
async def import_lessons_batch(data: Dict[str, Any] = Body(...)):
    try:
        import_user_id = data.get("user_id")
        lesson_records = data.get("lessons", [])
        if not import_user_id or not lesson_records:
            return JSONResponse(status_code=400, content={"error": "Missing user_id or lessons"})
        
        # Adjust IDs
        for lesson in lesson_records:
            lesson['id'] = str(uuid.uuid5(uuid.NAMESPACE_DNS, lesson['original_id'] + import_user_id))
            lesson['group_id'] = str(uuid.uuid5(uuid.NAMESPACE_DNS, lesson['original_group_id'] + import_user_id))
            del lesson['original_id']
            del lesson['original_group_id']
        
        await bulk_insert_with_chunk('lessons', lesson_records)
        return {"status": "success", "message": f"Inserted {len(lesson_records)} lessons"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/import/words")
async def import_words_batch(data: Dict[str, Any] = Body(...)):
    try:
        import_user_id = data.get("user_id")
        word_records = data.get("words", [])
        if not import_user_id or not word_records:
            return JSONResponse(status_code=400, content={"error": "Missing user_id or words"})
        
        # Adjust IDs and timestamps
        for word in word_records:
            word['id'] = str(uuid.uuid4())
            word['lesson_id'] = str(uuid.uuid5(uuid.NAMESPACE_DNS, word['original_lesson_id'] + import_user_id))
            ts_str = word.get('time', '')
            created_at = datetime.now().isoformat()
            if ts_str:
                try:
                    created_at = datetime.fromtimestamp(int(ts_str) / 1000).isoformat()
                except ValueError:
                    pass
            word['create_at'] = created_at
            word['latest_update'] = created_at
            del word['original_lesson_id']
            del word['time']  # Clean up
        
        await bulk_insert_with_chunk('words', word_records)
        return {"status": "success", "message": f"Inserted {len(word_records)} words"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# Các endpoints cũ (giữ nguyên, bỏ phần import cũ nếu không cần)
# ... (homepage, share_lesson, import_page)

@app.get("/import", response_class=HTMLResponse)
async def import_page(request: Request):
    return templates.TemplateResponse("import.html", {"request": request})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        reload=True,
        workers=2,
        timeout_keep_alive=600,  # Tăng timeout cho batches
    )
