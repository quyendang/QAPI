import requests
from fastapi import FastAPI, Query
from pydantic import BaseModel
from typing import List
from collections import defaultdict
import random
from datetime import datetime, timedelta
import pytz
import psycopg2
import os
import logging
from apscheduler.schedulers.background import BackgroundScheduler
import re
import json
import pytz
from netaddr import IPAddress, IPNetwork
import psutil
from fastapi import Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles


# Định nghĩa model cho dữ liệu thiết bị
class Device(BaseModel):
    id: str
    ip: str | None = None
    country_code: str | None = None
    ram_total: int | None = None
    ram_used: int | None = None
    cpu_percent: float | None = None
    description: str | None = None
    last_update: int | None = None
    counter1: int | None = None
    counter2: int | None = None
    counter3: int | None = None
    counter4: int | None = None


app = FastAPI()
templates = Jinja2Templates(directory="templates")
# Định nghĩa các filter tùy chỉnh
def datetime_from_timestamp(timestamp):
    """Chuyển Unix timestamp sang datetime object múi giờ GMT+7."""
    if timestamp is None:
        return None
    gmt_plus_7 = pytz.timezone('Asia/Bangkok')
    return datetime.fromtimestamp(timestamp, tz=gmt_plus_7)

def time_ago(dt):
    """Tính toán thời gian 'time ago' từ datetime object."""
    if dt is None:
        return "N/A"
    now = datetime.now(pytz.timezone('Asia/Bangkok'))
    time_difference = now - dt

    seconds = time_difference.total_seconds()
    if seconds < 60:
        return f"{int(seconds)} seconds ago"
    elif seconds < 3600:
        return f"{int(seconds // 60)} minutes ago"
    elif seconds < 86400:
        return f"{int(seconds // 3600)} hours ago"
    else:
        return f"{int(seconds // 86400)} days ago"

# Thêm các filter vào environment của Jinja2Templates
templates.env.filters['datetime_from_timestamp'] = datetime_from_timestamp
templates.env.filters['time_ago'] = time_ago
proxy_country_mapping = defaultdict(list)
# Cấu hình logging
logging.basicConfig(level=logging.INFO)
uk_ip_ranges = []

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

    # Tạo bảng lưu thông tin thiết bị
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS devices (
        id TEXT PRIMARY KEY,
        ip TEXT NOT NULL,
        country_code TEXT,
        ram_total INTEGER,
        ram_used INTEGER,
        cpu_percent DOUBLE PRECISION,
        description TEXT,
        last_update INTEGER,
        counter1 INTEGER,
        counter2 INTEGER,
        counter3 INTEGER,
        counter4 INTEGER
    )
    ''')
    
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
    
    cursor.execute("""
    ALTER TABLE ip_stats 
    ADD COLUMN IF NOT EXISTS last_delete TEXT
    """)

    # Thêm cột group_id nếu chưa tồn tại
    cursor.execute("""
    ALTER TABLE ip_records 
    ADD COLUMN IF NOT EXISTS groupId TEXT
    """)
    conn.commit()
    conn.close()

# Gọi hàm tạo bảng khi khởi động ứng dụng
create_tables()

# Gọi hàm này khi khởi động
# def fetch_uk_ip_ranges():
#     global uk_ip_ranges
#     url = "https://www.ipdeny.com/ipblocks/data/countries/gb.zone"
#     try:
#         response = requests.get(url)
#         response.raise_for_status()
#         uk_ip_ranges = response.text.strip().splitlines()
#         print(f"Loaded {len(uk_ip_ranges)} UK IP ranges")
#     except Exception as e:
#         print(f"Failed to fetch UK IP ranges: {e}")
#         uk_ip_ranges = []

# fetch_uk_ip_ranges()
# Hàm kiểm tra devices và gửi thông báo qua Pushover
def check_devices():
    try:
        # Gọi API để lấy dữ liệu devices
        response = requests.get("https://musik-9b557-default-rtdb.asia-southeast1.firebasedatabase.app/devices.json")
        devices = response.json()
        
        if not devices:
            logging.info("No devices found in the response")
            return

        current_time = datetime.now(pytz.UTC)
        outdated_devices = []
        
        # Kiểm tra từng device
        for device_id, device_info in devices.items():
            if not isinstance(device_info, dict) or 'lastUpdateTime' not in device_info:
                continue
                
            last_update_time = datetime.fromtimestamp(device_info['lastUpdateTime'] / 1000, pytz.UTC)
            time_diff = current_time - last_update_time
            
            if time_diff > timedelta(minutes=30):
                ip = device_info.get('ip', 'Unknown IP')
                pub = device_info.get('pub', 'Unknown')
                outdated_devices.append(f"{ip} ({pub})")
        
        # Nếu có device quá hạn, gửi thông báo qua Pushover
        if outdated_devices:
            message = f"⛔ {len(outdated_devices)} devices offline : {', '.join(outdated_devices)}"
            pushover_data = {
                "token": "ah2hby41xn2viu41syq295ipeoss4e",
                "user": "uqyjaksy71vin1ftoafoujqqg1s8rz",
                "sound": "anhoi",
                "title": "Device Warning",
                "message": message
            }
            
            response = requests.post(
                "https://api.pushover.net/1/messages.json",
                data=pushover_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if response.status_code == 200:
                logging.info(f"Pushover notification sent successfully: {message}")
            else:
                logging.error(f"Failed to send Pushover notification: {response.text}")
                
    except Exception as e:
        logging.error(f"Error in check_devices: {str(e)}")

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
        # Update last_delete in ip_stats
        last_delete_message = f"Deleted {deleted_rows} old IPs at {datetime.now()}"
        cursor.execute("UPDATE ip_stats SET last_delete = %s WHERE id = 1", (last_delete_message,))
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
scheduler.add_job(check_devices, 'interval', minutes=15)
scheduler.start()


@app.post("/device")
async def add_or_update_device(device: Device):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Kiểm tra xem thiết bị đã tồn tại chưa
        cursor.execute("SELECT id FROM devices WHERE id = %s", (device.id,))
        exists = cursor.fetchone()

        if exists:
            # Cập nhật thiết bị nếu đã tồn tại
            update_fields = []
            update_values = []
            for field, value in device.dict(exclude_unset=True).items():
                if field != "id" and value is not None:
                    update_fields.append(f"{field} = %s")
                    update_values.append(value)
            
            if update_fields:
                update_values.append(device.id)
                query = f"UPDATE devices SET {', '.join(update_fields)} WHERE id = %s"
                cursor.execute(query, update_values)
                conn.commit()
                return {"message": f"Device {device.id} updated successfully"}
            else:
                return {"message": f"No fields to update for device {device.id}"}
        else:
            # Thêm thiết bị mới
            fields = []
            placeholders = []
            values = []
            for field, value in device.dict(exclude_unset=True).items():
                fields.append(field)
                placeholders.append("%s")
                values.append(value)
            
            query = f"INSERT INTO devices ({', '.join(fields)}) VALUES ({', '.join(placeholders)})"
            cursor.execute(query, values)
            conn.commit()
            return {"message": f"Device {device.id} added successfully"}
    except Exception as e:
        conn.rollback()
        logging.error(f"Error occurred: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.delete("/deleteDevice")
async def delete_device(id: str = Query(...)):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Kiểm tra xem thiết bị có tồn tại không
        cursor.execute("SELECT id FROM devices WHERE id = %s", (id,))
        exists = cursor.fetchone()

        if not exists:
            raise HTTPException(status_code=404, detail=f"Device {id} not found")

        # Xóa thiết bị
        cursor.execute("DELETE FROM devices WHERE id = %s", (id,))
        conn.commit()
        return {"message": f"Device {id} deleted successfully"}
    except Exception as e:
        conn.rollback()
        logging.error(f"Error occurred: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()



@app.get("/", response_class=HTMLResponse)
def homepage(request: Request):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM ip_records")
        total_ips = cursor.fetchone()[0]
        # Lấy danh sách thiết bị
        cursor.execute("SELECT id, ip, country_code, ram_total, ram_used, cpu_percent, description, last_update, counter1, counter2, counter3, counter4 FROM devices")
        devices = cursor.fetchall()
        device_list = [
            {
                "id": row[0],
                "ip": row[1],
                "country_code": row[2],
                "ram_total": row[3],
                "ram_used": row[4],
                "cpu_percent": row[5],
                "description": row[6],
                "last_update": row[7],
                "counter1": row[8],
                "counter2": row[9],
                "counter3": row[10],
                "counter4": row[11]
            } for row in devices
        ]
    except Exception as e:
        total_ips = "Error"
        device_list = []
        logging.error(f"Error fetching IP count: {str(e)}")
    finally:
        cursor.close()
        conn.close()
    
    # Lấy thông tin CPU và Memory
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    memory_total = round(memory.total / (1024 ** 3), 2)  # GB
    memory_used = round(memory.used / (1024 ** 3), 2)    # GB
    memory_percent = memory.percent

    return templates.TemplateResponse("index.html", {
        "request": request,
        "total_ips": total_ips,
        "cpu_percent": cpu_percent,
        "memory_total": memory_total,
        "memory_used": memory_used,
        "memory_percent": memory_percent,
        "devices": device_list
    })

@app.post("/delete-ips")
def delete_ips_from_button():
    delete_old_ips()
    return RedirectResponse("/", status_code=303)
    
@app.get("/country")
def get_country(countrys: str, proxy: str):
    # Parse the country list from the query parameter
    country_list = countrys.split(",")
    
    # Check if the proxy already has a randomized list
    if proxy not in proxy_country_mapping or not proxy_country_mapping[proxy]:
        # Create a new randomized list for this proxy
        proxy_country_mapping[proxy] = random.sample(country_list, len(country_list))
    
    # Return the next country in the list
    selected_country = proxy_country_mapping[proxy].pop(0)

    # If the list is exhausted, reset with a new randomized order
    if not proxy_country_mapping[proxy]:
        proxy_country_mapping[proxy] = random.sample(country_list, len(country_list))
    
    return {"proxy": proxy, "country": selected_country}
    
@app.get("/geteid")
def get_eid(version: str = "v9.2.0"):
    try:
        # Making a request to fetch the HTML content
        headers = {
            'Sec-Fetch-Site': 'none',
            'Connection': 'keep-alive',
            'Sec-Fetch-Mode': 'navigate',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148',
            'Accept-Language': 'en-GB,en-US;q=0.9,en;q=0.8',
            'Sec-Fetch-Dest': 'document'
        }
        url = f'https://googleads.g.doubleclick.net/mads/static/sdk/native/sdk-core-v40.html?sdk=afma-sdk-i-{version}'
        response = requests.get(url, headers=headers)
        html_content = response.text

        # Parsing the HTML content for sdkLoaderEID and sdkLoaderEID2
        sdkLoaderEID_match = re.search(r'var sdkLoaderEID = "([^"]+)"', html_content)
        sdkLoaderEID2_match = re.search(r'f.includes\("([^"]+)"\)', html_content)

        sdkLoaderEID = sdkLoaderEID_match.group(1) if sdkLoaderEID_match else None
        sdkLoaderEID2 = sdkLoaderEID2_match.group(1) if sdkLoaderEID2_match else None

        # Return the JSON response
        return {"sdkLoaderEID": sdkLoaderEID, "sdkLoaderEID2": sdkLoaderEID2, "version": version}
    except Exception as e:
        logging.error(f"Error fetching EID values: {str(e)}")
        return {"sdkLoaderEID":"318502621","sdkLoaderEID2":"318500618","version":"v9.2.0"}


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
            if datetime.now() - last_checked < timedelta(hours=1):
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
def delete_old_ips_time(time: int = 24):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Tính toán thời gian 24 giờ trước
        time_threshold = datetime.now() - timedelta(hours=int(time))
        # Xóa các IP có last_checked hơn 24 giờ
        cursor.execute("DELETE FROM ip_records WHERE last_checked < %s", (time_threshold,))
        deleted_rows = cursor.rowcount
        conn.commit()
        # Update last_delete in ip_stats
        last_delete_message = f"Deleted {deleted_rows} old IPs at {datetime.now()}"
        cursor.execute("UPDATE ip_stats SET last_delete = %s WHERE id = 1", (last_delete_message,))
        conn.commit()
        logging.info(f"Deleted {deleted_rows} old IPs from ip_records.")
        
        return {"message": f"Deleted {deleted_rows} IP(s) last checked over {time} hours ago."}
    except Exception as e:
        logging.error(f"Error occurred while deleting old IPs: {str(e)}")
        return {"error": str(e)}
    finally:
        cursor.close()
        conn.close()

@app.get("/ip")
def log_ip(ip: str, time: int = 5, groupId: str = None):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Kiểm tra IP trong database với groupId
        ip_obj = IPAddress(ip)
        if groupId:
            cursor.execute("SELECT last_checked FROM ip_records WHERE ip = %s AND groupId = %s", (ip, groupId))
        else:
            cursor.execute("SELECT last_checked FROM ip_records WHERE ip = %s AND groupId IS NULL", (ip,))
        row = cursor.fetchone()

        if row:
            last_checked = row[0]  # Đã là timestamp
            if datetime.now() - last_checked < timedelta(hours=int(time)):
                # Nếu IP đã được check trong khoảng thời gian, tăng duplicate_count
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

        # Nếu IP mới hoặc đã quá thời gian kiểm tra, lưu lại
        if groupId:
            cursor.execute("INSERT INTO ip_records (ip, last_checked, groupId) VALUES (%s, %s, %s) ON CONFLICT (ip) DO UPDATE SET last_checked = EXCLUDED.last_checked",
                           (ip, datetime.now(), groupId))
        else:
            cursor.execute("INSERT INTO ip_records (ip, last_checked, groupId) VALUES (%s, %s, NULL) ON CONFLICT (ip) DO UPDATE SET last_checked = EXCLUDED.last_checked",
                           (ip, datetime.now()))
        
        # Tăng fresh_count
        cursor.execute("UPDATE ip_stats SET fresh_count = fresh_count + 1 WHERE id = 1")
        conn.commit()
        # for cidr in uk_ip_ranges:
        #     if ip_obj in IPNetwork(cidr):
        #         print(f"UK IP: {ip}")
        #         return {"allow": False, "last_checked": None}
                
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
        cursor.execute("SELECT allow_count, not_allow_count, fresh_count, duplicate_count, last_delete FROM ip_stats WHERE id = 1")
        row = cursor.fetchone()
        allow_count = row[0]
        not_allow_count = row[1]
        fresh_count = row[2]
        duplicate_count = row[3]
        last_delete = row[4]

        return {
            "allow": allow_count,
            "notAllow": not_allow_count,
            "fresh": fresh_count,
            "duplicate": duplicate_count,
            "last_delete": last_delete
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
