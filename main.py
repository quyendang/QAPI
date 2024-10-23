from fastapi import FastAPI
from datetime import datetime, timedelta
import psycopg2
import os
import logging

app = FastAPI()

# Cấu hình logging
logging.basicConfig(level=logging.INFO)

# Hàm tạo kết nối đến database PostgreSQL
def get_db_connection():
    conn = psycopg2.connect(
        dbname=os.environ.get("DB_NAME"),
        user=os.environ.get("DB_USER"),
        password=os.environ.get("DB_PASSWORD"),
        host=os.environ.get("DB_HOST"),
        port=os.environ.get("DB_PORT", 5432)  # Mặc định cổng PostgreSQL là 5432
    )
    return conn

# Tạo bảng IP nếu chưa tồn tại
def create_table():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ip_logs (
        ip TEXT PRIMARY KEY,
        last_checked TIMESTAMP
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
        cursor.execute("SELECT last_checked FROM ip_logs WHERE ip = %s", (ip,))
        row = cursor.fetchone()

        if row:
            last_checked = row[0]  # Đã là timestamp
            if datetime.now() - last_checked < timedelta(hours=24):
                return {"allow": False}

        # Lưu IP vào database nếu chưa có hoặc đã quá 24 giờ
        cursor.execute("INSERT INTO ip_logs (ip, last_checked) VALUES (%s, %s) ON CONFLICT (ip) DO UPDATE SET last_checked = EXCLUDED.last_checked",
                       (ip, datetime.now()))
        conn.commit()
        return {"allow": True}
    except Exception as e:
        logging.error(f"Error occurred: {str(e)}")  # Ghi lại lỗi
        return {"error": str(e)}
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    import uvicorn
    # Chạy ứng dụng trên cổng 10000, cổng mặc định trên Render
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
