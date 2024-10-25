from fastapi import FastAPI
from datetime import datetime, timedelta
import pytz
import psycopg2
import os
import logging
from apscheduler.schedulers.background import BackgroundScheduler

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
    
    # Tạo bảng lưu thông tin IP từ API /check
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ip_logs (
        ip TEXT PRIMARY KEY,
        last_checked TIMESTAMP
    )
    ''')
    
    # Tạo bảng lưu thông tin IP từ API /ip
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ip_records (
        ip TEXT PRIMARY KEY,
        last_checked TIMESTAMP
    )
    ''')
    
    # Tạo bảng lưu thống kê số lần allow, notAllow, fresh, duplicate
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ip_stats (
        id SERIAL PRIMARY KEY,
        allow_count INTEGER DEFAULT 0,
        not_allow_count INTEGER DEFAULT 0,
        fresh_count INTEGER DEFAULT 0,
        duplicate_count INTEGER DEFAULT 0
    )
    ''')
    
    # Khởi tạo một dòng mặc định nếu bảng thống kê chưa có dữ liệu
    cursor.execute("INSERT INTO ip_stats (allow_count, not_allow_count, fresh_count, duplicate_count) SELECT 0, 0, 0, 0 WHERE NOT EXISTS (SELECT 1 FROM ip_stats)")
    
    conn.commit()
    conn.close()

# Gọi hàm tạo bảng khi khởi động ứng dụng
create_tables()

# Hàm xóa IP đã quá hạn (hơn 2 ngày) khỏi ip_records
def delete_old_ips():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Tính toán thời gian 2 ngày trước
        time_threshold = datetime.now() - timedelta(days=1)
        # Xóa các IP có last_checked > 2 ngày
        cursor.execute("DELETE FROM ip_records WHERE last_checked < %s", (time_threshold,))
        deleted_rows = cursor.rowcount
        conn.commit()
        logging.info(f"Deleted {deleted_rows} old IPs from ip_records.")
    except Exception as e:
        logging.error(f"Error occurred while deleting old IPs: {str(e)}")
    finally:
        cursor.close()
        conn.close()

# Scheduler để lên lịch thực hiện công việc xóa IP cũ mỗi 12 giờ
scheduler = BackgroundScheduler()
scheduler.add_job(delete_old_ips, 'interval', hours=12)
scheduler.start()

@app.get("/check")
def check_ip(ip: str):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Kiểm tra IP trong database /check
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
        
@app.get("/delete")
def delete_old_ips_24h():
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Tính toán thời gian 24 giờ trước
        time_threshold = datetime.now() - timedelta(hours=24)
        # Xóa các IP có last_checked hơn 24 giờ
        cursor.execute("DELETE FROM ip_records WHERE last_checked < %s", (time_threshold,))
        deleted_rows = cursor.rowcount
        conn.commit()
        logging.info(f"Deleted {deleted_rows} old IPs from ip_records.")
        
        return {"message": f"Deleted {deleted_rows} IP(s) last checked over 24 hours ago."}
    except Exception as e:
        logging.error(f"Error occurred while deleting old IPs: {str(e)}")
        return {"error": str(e)}
    finally:
        cursor.close()
        conn.close()

@app.get("/ip")
def log_ip(ip: str, time: int = 15):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Kiểm tra IP trong database /ip
        cursor.execute("SELECT last_checked FROM ip_records WHERE ip = %s", (ip,))
        row = cursor.fetchone()

        if row:
            last_checked = row[0]  # Đã là timestamp
            if datetime.now() - last_checked < timedelta(hours=int(time)):
                # Nếu IP đã được check trong vòng 24 giờ, tăng duplicate_count
                cursor.execute("UPDATE ip_stats SET duplicate_count = duplicate_count + 1 WHERE id = 1")
                conn.commit()
                gmt = pytz.timezone('GMT')
                gmt_plus_7 = pytz.timezone('Asia/Bangkok')
                last_checked_gmt = gmt.localize(last_checked).astimezone(gmt_plus_7)
                # Tính thời gian "time ago"
                now_gmt_plus_7 = datetime.now(pytz.timezone('Asia/Bangkok'))
                time_difference = now_gmt_plus_7 - last_checked_gmt
    
                # Format thời gian "time ago"
                time_ago = ''
                if time_difference.days > 0:
                    time_ago = f"{time_difference.days} days ago"
                elif time_difference.seconds >= 3600:
                    time_ago = f"{time_difference.seconds // 3600} hours ago"
                elif time_difference.seconds >= 60:
                    time_ago = f"{time_difference.seconds // 60} minutes ago"
                else:
                    time_ago = f"{time_difference.seconds} seconds ago"
                
                return {"allow": False, "last_checked": time_ago if last_checked else None}

        # Nếu IP mới hoặc đã quá 24 giờ, lưu lại và tăng fresh_count
        cursor.execute("INSERT INTO ip_records (ip, last_checked) VALUES (%s, %s) ON CONFLICT (ip) DO UPDATE SET last_checked = EXCLUDED.last_checked",
                       (ip, datetime.now()))
        cursor.execute("UPDATE ip_stats SET fresh_count = fresh_count + 1 WHERE id = 1")
        conn.commit()
        return {"allow": True, "last_checked": None}
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
        cursor.execute("SELECT allow_count, not_allow_count, fresh_count, duplicate_count FROM ip_stats WHERE id = 1")
        row = cursor.fetchone()
        allow_count = row[0]
        not_allow_count = row[1]
        fresh_count = row[2]
        duplicate_count = row[3]

        return {
            "allow": allow_count,
            "notAllow": not_allow_count,
            "fresh": fresh_count,
            "duplicate": duplicate_count
        }
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
