import os
import logging
import json
from fastapi import FastAPI, Query, Request, File, UploadFile, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from supabase import create_client, Client
from typing import Dict, Any
import uuid
from datetime import datetime
import asyncio
import ijson  # For streaming JSON parsing
from starlette.background import BackgroundTask
from starlette.responses import RedirectResponse

app = FastAPI()
templates = Jinja2Templates(directory="templates")

logging.basicConfig(level=logging.INFO)

supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    raise ValueError("SUPABASE_URL và SUPABASE_KEY phải được thiết lập trong biến môi trường.")

supabase: Client = create_client(supabase_url, supabase_key)

# Helper functions for importing data
def import_groups(data: Dict[str, Any], import_user_id: str):
    group_records = []
    try:
        users = data.get('users', {})
        for user_id, user_data in users.items():
            if user_id == 'F4pm4km5TiY3NPEegMOkXaPYcKt2':
                groups = user_data.get('groups', {})
                for group_id, group_data in groups.items():
                    group_records.append({
                        'id': str(uuid.uuid5(uuid.NAMESPACE_DNS, group_id + import_user_id)),
                        'user_id': import_user_id,
                        'name': group_data.get('groupName', '')
                    })

        if group_records:
            supabase.table('groups').insert(group_records).execute()
            logging.info(f"Inserted {len(group_records)} groups for user: {import_user_id}")
            return {"status": "success", "message": f"Inserted {len(group_records)} groups"}
        return {"status": "success", "message": "No groups to insert"}
    except Exception as e:
        logging.error(f"Error inserting groups: {str(e)}")
        return {"status": "error", "message": f"Error inserting groups: {str(e)}"}

def import_lessons(data: Dict[str, Any], import_user_id: str):
    lesson_records = []
    try:
        users = data.get('users', {})
        for user_id, user_data in users.items():
            if user_id == 'F4pm4km5TiY3NPEegMOkXaPYcKt2':
                groups = user_data.get('groups', {})
                for group_id, group_data in groups.items():
                    lessons = group_data.get('lessons', {})
                    for lesson_id, lesson_data in lessons.items():
                        lesson_records.append({
                            'id': str(uuid.uuid5(uuid.NAMESPACE_DNS, lesson_id + import_user_id)),
                            'group_id': str(uuid.uuid5(uuid.NAMESPACE_DNS, group_id + import_user_id)),
                            'name': lesson_data.get('lessonName', '')
                        })

        if lesson_records:
            supabase.table('lessons').insert(lesson_records).execute()
            logging.info(f"Inserted {len(lesson_records)} lessons for user: {import_user_id}")
            return {"status": "success", "message": f"Inserted {len(lesson_records)} lessons"}
        return {"status": "success", "message": "No lessons to insert"}
    except Exception as e:
        logging.error(f"Error inserting lessons: {str(e)}")
        return {"status": "error", "message": f"Error inserting lessons: {str(e)}"}

async def bulk_insert_with_chunk(table_name, rows, chunk_size=100):
    for i in range(0, len(rows), chunk_size):
        batch = rows[i:i + chunk_size]
        try:
            supabase.table(table_name).insert(batch).execute()
            logging.info(f"Inserted batch {i//chunk_size + 1}: {len(batch)} rows")
        except Exception as e:
            logging.error(f"Error inserting batch {i//chunk_size + 1}: {str(e)}")
            raise e
        await asyncio.sleep(0.1)  # Prevent overwhelming Supabase

async def import_words_streaming(file, import_user_id: str):
    word_records = []
    try:
        # Use ijson to parse JSON stream
        parser = ijson.parse(file)
        current_user = None
        current_group = None
        current_lesson = None
        current_word = None
        word_data = {}
        path = []

        for prefix, event, value in parser:
            if event == 'map_key':
                path = path[:path.index(prefix) + 1] if prefix in path else path + [prefix]
                if prefix == 'F4pm4km5TiY3NPEegMOkXaPYcKt2':
                    current_user = prefix
                elif len(path) >= 3 and path[1] == 'users' and path[2] == current_user and path[3] == 'groups':
                    current_group = prefix
                elif len(path) >= 5 and path[4] == 'lessons':
                    current_lesson = prefix
                elif len(path) >= 7 and path[6] == 'words':
                    current_word = prefix
            elif event in ('string', 'number'):
                if current_word and len(path) >= 7 and path[6] == 'words':
                    word_data[prefix] = value
            elif event == 'end_map' and current_word:
                ts_str = word_data.get('time', '')
                created_at = datetime.now().isoformat()
                if ts_str:
                    try:
                        created_at = datetime.fromtimestamp(int(ts_str) / 1000).isoformat()
                    except ValueError:
                        pass
                word_records.append({
                    'id': str(uuid.uuid4()),
                    'lesson_id': str(uuid.uuid5(uuid.NAMESPACE_DNS, current_lesson + import_user_id)),
                    'word': word_data.get('word', ''),
                    'type': word_data.get('wordType', ''),
                    'pronunciation': word_data.get('pronunciation', ''),
                    'meaning': word_data.get('meaning', ''),
                    'translate': word_data.get('eg', ''),
                    'example': word_data.get('eg2', ''),
                    'word_voice': word_data.get('usVoice', ''),
                    'df_voice': word_data.get('dfVoice', ''),
                    'eg_voice': word_data.get('egVoice', ''),
                    'create_at': created_at,
                    'latest_update': created_at
                })
                word_data = {}
                current_word = None
                if len(word_records) >= 100:  # Process in smaller chunks
                    await bulk_insert_with_chunk('words', word_records, chunk_size=100)
                    word_records = []

        if word_records:  # Insert remaining records
            await bulk_insert_with_chunk('words', word_records, chunk_size=100)
        logging.info(f"Inserted total {len(word_records)} words for user: {import_user_id}")
        return {"status": "success", "message": f"Inserted {len(word_records)} words"}
    except Exception as e:
        logging.error(f"Error inserting words: {str(e)}")
        return {"status": "error", "message": f"Error inserting words: {str(e)}"}

@app.get("/", response_class=HTMLResponse)
def homepage(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request})

@app.get("/share", response_class=HTMLResponse)
async def share_lesson(
    request: Request,
    id: str = Query(..., description="Lesson short_id"),
    c: str = Query("", description="Ẩn nội dung cột khi hiển thị, vd: 1,2,4"),
    p: str = Query("", description="Ẩn nội dung cột khi in, vd: 4,5"),
):
    try:
        lesson_resp = (
            supabase.table("lessons")
            .select("id, name")
            .eq("short_id", id)
            .single()
            .execute()
        )

        if not lesson_resp.data:
            raise ValueError(f"Lesson with short_id={id} not found")

        lesson_id = lesson_resp.data["id"]
        lesson_name = lesson_resp.data.get("name", f"Lesson {id}")

        response = (
            supabase.table("words")
            .select("*")
            .eq("lesson_id", lesson_id)
            .execute()
        )

        words_list = [
            {
                "word": row.get("word"),
                "type": row.get("type"),
                "pronunciation": row.get("pronunciation"),
                "meaning": row.get("meaning"),
                "translate": row.get("translate"),
                "example": row.get("example"),
                "word_voice": row.get("word_voice"),
                "eg_voice": row.get("eg_voice"),
                "trans_voice": row.get("trans_voice"),
                "df_voice": row.get("df_voice"),
            }
            for row in response.data
        ]

        hide_columns = [int(x) for x in c.split(",") if x.isdigit()]
        hide_columns_print = [int(x) for x in p.split(",") if x.isdigit()]

    except Exception as e:
        logging.error(f"[ERROR] Fetching data: {str(e)}")
        return templates.TemplateResponse(
            "error.html",
            {
                "request": request,
                "error": str(e)
            },
        )

    return templates.TemplateResponse(
        "share.html",
        {
            "request": request,
            "words": words_list,
            "lesson_id": id,
            "lesson_name": lesson_name,
            "hide_columns": hide_columns,
            "hide_columns_print": hide_columns_print,
        },
    )

@app.get("/import", response_class=HTMLResponse)
async def import_page(request: Request):
    return templates.TemplateResponse("import.html", {"request": request})

@app.post("/import", response_class=HTMLResponse)
async def import_data(request: Request, user_id: str = Form(...), file: UploadFile = File(...)):
    try:
        if not file.filename.endswith('.json'):
            return templates.TemplateResponse(
                "import.html",
                {"request": request, "error": "Please upload a valid JSON file."}
            )

        # Validate user_id
        if not user_id:
            return templates.TemplateResponse(
                "import.html",
                {"request": request, "error": "User ID is required."}
            )

        # Read JSON file in chunks
        content = await file.read()
        data = json.loads(content.decode('utf-8'))

        # Import groups and lessons
        groups_result = import_groups(data, user_id)
        if groups_result["status"] == "error":
            return templates.TemplateResponse(
                "import.html",
                {"request": request, "error": groups_result["message"]}
            )

        lessons_result = import_lessons(data, user_id)
        if lessons_result["status"] == "error":
            return templates.TemplateResponse(
                "import.html",
                {"request": request, "error": lessons_result["message"]}
            )

        # Stream words import
        await file.seek(0)  # Reset file pointer for streaming
        words_result = await import_words_streaming(file.file, user_id)
        if words_result["status"] == "error":
            return templates.TemplateResponse(
                "import.html",
                {"request": request, "error": words_result["message"]}
            )

        return templates.TemplateResponse(
            "import.html",
            {
                "request": request,
                "success": "Data imported successfully!",
                "groups_message": groups_result["message"],
                "lessons_message": lessons_result["message"],
                "words_message": words_result["message"]
            }
        )

    except Exception as e:
        logging.error(f"Error processing import: {str(e)}")
        return templates.TemplateResponse(
            "import.html",
            {"request": request, "error": f"Error processing import: {str(e)}"}
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        reload=True,
        workers=2,  # Increased workers for better handling
        timeout_keep_alive=300,  # Increased timeout for large uploads
        limit_max_requests=1000,
        limit_concurrency=100
    )
