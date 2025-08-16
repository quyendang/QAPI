import os
import logging
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from supabase import create_client, Client

# Initialize FastAPI app
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Logging
logging.basicConfig(level=logging.INFO)

# Initialize Supabase client
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(supabase_url, supabase_key)

@app.get("/share", response_class=HTMLResponse)
async def share_lesson(request: Request, id: str = Query(...)):
    try:
        response = (
            supabase.table("words")
            .select("*")
            .eq("lesson_id", id)   # dùng id query param thay vì fix cứng
            .execute()
        )
        logging.info(f"response supabase: {response.data}")

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
            }
            for row in response.data
        ]
    except Exception as e:
        logging.error(f"Error fetching words: {str(e)}")
        words_list = []

    return templates.TemplateResponse(
        "share.html",
        {"request": request, "words": words_list, "lesson_id": id},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), workers=1
    )
