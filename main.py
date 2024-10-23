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

# Tạo bảng IP và bảng thống kê nếu chưa tồn tại
def create_tables():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Tạo bảng lưu thông tin IP
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ip_logs (
        ip TEXT PRIMARY KEY,
        last_checked TIMESTAMP
    )
    ''')
    # Tạo bảng lưu thống kê số lần allow và notAllow
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ip_stats (
        id SERIAL PRIMARY KEY,
        allow_count INTEGER DEFAULT 0,
        not_allow_count INTEGER DEFAULT 0
    )
    ''')
    # Khởi tạo một dòng mặc định nếu bảng thống kê chưa có dữ liệu
    cursor.execute("INSERT INTO ip_stats (allow_count, not_allow_count) SELECT 0, 0 WHERE NOT EXISTS (SELECT 1 FROM ip_stats)")
    conn.commit()
    conn.close()

# Gọi hàm tạo bảng khi khởi động ứng dụng
create_tables()

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
                # Cập nhật số lượng notAllow
                cursor.execute("UPDATE ip_stats SET not_allow_count = not_allow_count + 1 WHERE id = 1")
                conn.commit()
                return {"allow": False}

        # Lưu IP vào database nếu chưa có hoặc đã quá 24 giờ
        cursor.execute("INSERT INTO ip_logs (ip, last_checked) VALUES (%s, %s) ON CONFLICT (ip) DO UPDATE SET last_checked = EXCLUDED.last_checked",
                       (ip, datetime.now()))
        # Cập nhật số lượng allow
        cursor.execute("UPDATE ip_stats SET allow_count = allow_count + 1 WHERE id = 1")
        conn.commit()
        return {"allow": True}
    except Exception as e:
        logging.error(f"Error occurred: {str(e)}")  # Ghi lại lỗi
        return {"error": str(e)}
    finally:
        cursor.close()
        conn.close()

@app.get("/info")
def get_info():
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Lấy thông tin từ bảng thống kê
        cursor.execute("SELECT allow_count, not_allow_count FROM ip_stats WHERE id = 1")
        row = cursor.fetchone()
        allow_count = row[0]
        not_allow_count = row[1]

        return {"allow": allow_count, "notAllow": not_allow_count}
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
