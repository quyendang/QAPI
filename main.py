import os
import logging
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from supabase import create_client, Client

# ========================
# Khởi tạo ứng dụng FastAPI
# ========================
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Logging
logging.basicConfig(level=logging.INFO)

# ========================
# Kết nối Supabase
# ========================
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    raise ValueError("SUPABASE_URL và SUPABASE_KEY phải được thiết lập trong biến môi trường.")

supabase: Client = create_client(supabase_url, supabase_key)


# ========================
# API hiển thị lesson share
# ========================
@app.get("/share", response_class=HTMLResponse)
async def share_lesson(request: Request, id: str = Query(..., description="Lesson ID")):
    try:
        # Lấy danh sách words theo lesson_id
        response = (
            supabase.table("words")
            .select("*")
            .eq("lesson_id", id)
            .execute()
        )
        logging.info(f"[Supabase] words: {response.data}")

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

        # Lấy lesson name từ bảng lessons
        lesson_resp = (
            supabase.table("lessons")
            .select("name")
            .eq("id", id)
            .single()
            .execute()
        )

        lesson_name = lesson_resp.data.get("name") if lesson_resp.data else f"Lesson {id}"

    except Exception as e:
        logging.error(f"[ERROR] Fetching data: {str(e)}")
        words_list = []
        lesson_name = f"Lesson {id}"

    return templates.TemplateResponse(
        "share.html",
        {
            "request": request,
            "words": words_list,
            "lesson_id": id,
            "lesson_name": lesson_name,
        },
    )


# ========================
# Chạy ứng dụng
# ========================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        reload=True,  # bật reload khi dev
        workers=1
    )
