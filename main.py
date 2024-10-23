from fastapi import FastAPI
from datetime import datetime, timedelta
import sqlite3
import os
import logging

app = FastAPI()

# Cấu hình logging
logging.basicConfig(level=logging.INFO)

# Hàm tạo kết nối đến database
def get_db_connection():
    conn = sqlite3.connect('ip_data.db', check_same_thread=False)
    return conn

# Tạo bảng IP nếu chưa tồn tại
def create_table():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ip_logs (
        ip TEXT PRIMARY KEY,
        last_checked DATETIME
    )
    ''')
    conn.commit()
    conn.close()

# Gọi hàm tạo bảng khi khởi động ứng dụng
create_table()

@app.get("/check")
def check_ip(ip: str):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Kiểm tra IP trong database
        cursor.execute("SELECT last_checked FROM ip_logs WHERE ip = ?", (ip,))
        row = cursor.fetchone()

        if row:
            last_checked_str = row[0]
            last_checked = datetime.strptime(last_checked_str, '%Y-%m-%d %H:%M:%S')
            if datetime.now() - last_checked < timedelta(hours=24):
                return {"allow": False}

        # Lưu IP vào database nếu chưa có hoặc đã quá 24 giờ
        cursor.execute("INSERT OR REPLACE INTO ip_logs (ip, last_checked) VALUES (?, ?)", 
                       (ip, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        return {"allow": True}
    except Exception as e:
        logging.error(f"Error occurred: {str(e)}")  # Ghi lại lỗi
        return {"error": str(e)}
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    # Chạy ứng dụng trên cổng 10000, cổng mặc định trên Render
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
