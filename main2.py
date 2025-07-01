import requests
from fastapi import FastAPI, Query, WebSocket, Request, Form, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict
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
from netaddr import IPAddress, IPNetwork
import psutil
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import ipapi
import socket
import geoip2.database

class Device(BaseModel):
    id: str  # Required, no default
    ip: str | None = None
    country_code: str | None = None
    ram_total: int | None = 0
    ram_used: int | None = 0
    cpu_percent: float | None = 0
    description: str | None = None
    last_update: int | None = None
    counter1: int | None = 0
    counter2: int | None = 0
    counter3: int | None = 0
    counter4: int | None = 0
    counter5: int | None = 0
    runtime: int | None = 0
    restart: bool = False

app = FastAPI()
reader = geoip2.database.Reader('GeoLite2-City.mmdb')
templates = Jinja2Templates(directory="templates")
connected_websockets = set()
country_data = []
language_map: Dict[str, str] = {}  # countryCode -> languages
offset_map: Dict[str, int] = {}    # zoneName -> gmtOffset (seconds)
country_offset_map: Dict[str, list] = {}  # countryCode -> list of gmtOffsets
EUROPEAN_COUNTRY_CODES = {
    'AL', 'AD', 'AT', 'BY', 'BE', 'BA', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR', 'DE', 
    'GR', 'HU', 'IS', 'IE', 'IT', 'LV', 'LI', 'LT', 'LU', 'MT', 'MD', 'MC', 'ME', 'NL', 'MK', 
    'NO', 'PL', 'PT', 'RO', 'RU', 'SM', 'RS', 'SK', 'SI', 'ES', 'SE', 'CH', 'UA', 'GB', 'VA'
}

def load_country_data():
    global country_data
    try:
        with open('countrydata.json', 'r', encoding='utf-8') as file:
            country_data = json.load(file)
        for entry in country_data:
            country_code = entry['countryCode']
            zone_name = entry['zoneName']
            gmt_offset = entry['gmtOffset']
            languages = entry['languages']
            if country_code not in language_map:
                language_map[country_code] = languages
            offset_map[zone_name] = gmt_offset
            if country_code not in country_offset_map:
                country_offset_map[country_code] = []
            country_offset_map[country_code].append(gmt_offset)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="countrydata.json not found")
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Invalid countrydata.json format")

load_country_data()

def datetime_from_timestamp(timestamp):
    if timestamp is None:
        return None
    gmt_plus_7 = pytz.timezone('Asia/Bangkok')
    return datetime.fromtimestamp(timestamp, tz=gmt_plus_7)

def time_ago(dt):
    if dt is None:
        return "N/A"
    now = datetime.now(pytz.timezone('Asia/Bangkok'))
    time_difference = now - dt
    seconds = time_difference.total_seconds()
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m"
    elif seconds < 86400:
        return f"{int(seconds // 3600)}h"
    else:
        return f"{int(seconds // 86400)}d"

def run_time(seconds):
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m"
    elif seconds < 86400:
        return f"{int(seconds // 3600)}h"
    else:
        return f"{int(seconds // 86400)}d"

templates.env.filters['datetime_from_timestamp'] = datetime_from_timestamp
templates.env.filters['time_ago'] = time_ago
templates.env.filters['run_time'] = run_time
proxy_country_mapping = defaultdict(list)
logging.basicConfig(level=logging.INFO)
uk_ip_ranges = []

def get_db_connection():
    conn = psycopg2.connect(
        dbname=os.environ.get("DB_NAME"),
        user=os.environ.get("DB_USER"),
        password=os.environ.get("DB_PASSWORD"),
        host=os.environ.get("DB_HOST"),
        port=os.environ.get("DB_PORT", 5432)
    )
    return conn

def create_tables():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS devices (
        id TEXT PRIMARY KEY,
        ip TEXT,
        country_code TEXT,
        ram_total INTEGER DEFAULT 0,
        ram_used INTEGER DEFAULT 0,
        cpu_percent DOUBLE PRECISION DEFAULT 0,
        description TEXT,
        last_update INTEGER,
        counter1 INTEGER DEFAULT 0,
        counter2 INTEGER DEFAULT 0,
        counter3 INTEGER DEFAULT 0,
        counter4 INTEGER DEFAULT 0,
        counter5 INTEGER DEFAULT 0,
        runtime INTEGER DEFAULT 0,
        restart BOOLEAN DEFAULT FALSE
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ip_logs (
        ip TEXT PRIMARY KEY,
        last_checked TIMESTAMP
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ip_records (
        ip TEXT PRIMARY KEY,
        last_checked TIMESTAMP
    )
    ''')
    cursor.execute("""
    ALTER TABLE ip_records 
    ADD COLUMN IF NOT EXISTS groupId TEXT
    """)
    cursor.execute("""
    ALTER TABLE devices 
    ADD COLUMN IF NOT EXISTS runtime INTEGER DEFAULT 0
    """)
    cursor.execute("""
    ALTER TABLE devices 
    ADD COLUMN IF NOT EXISTS counter5 INTEGER DEFAULT 0
    """)
    cursor.execute("""
    UPDATE devices 
    SET ram_total = COALESCE(ram_total, 0),
        ram_used = COALESCE(ram_used, 0),
        cpu_percent = COALESCE(cpu_percent, 0),
        counter1 = COALESCE(counter1, 0),
        counter2 = COALESCE(counter2, 0),
        counter3 = COALESCE(counter3, 0),
        counter4 = COALESCE(counter4, 0),
        counter5 = COALESCE(counter5, 0),
        runtime = COALESCE(runtime, 0),
        restart = COALESCE(restart, FALSE)
    """)
    conn.commit()
    conn.close()

create_tables()

def check_devices():
    try:
        response = requests.get("https://musik-9b557-default-rtdb.asia-southeast1.firebasedatabase.app/devices.json")
        devices = response.json()
        if not devices:
            logging.info("No devices found in the response")
            return
        current_time = datetime.now(pytz.UTC)
        outdated_devices = []
        for device_id, device_info in devices.items():
            if not isinstance(device_info, dict) or 'lastUpdateTime' not in device_info:
                continue
            last_update_time = datetime.fromtimestamp(device_info['lastUpdateTime'] / 1000, pytz.UTC)
            time_diff = current_time - last_update_time
            if time_diff > timedelta(minutes=30):
                ip = device_info.get('ip', 'Unknown IP')
                pub = device_info.get('pub', 'Unknown')
                outdated_devices.append(f"{ip} ({pub})")
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

def check_outdated_devices():
    outdated_ips = []
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        current_timestamp = int(datetime.now(pytz.timezone('Asia/Bangkok')).timestamp())
        time_threshold = current_timestamp - 15 * 60
        cursor.execute("""
            SELECT id, ip, last_update 
            FROM devices 
            WHERE last_update < %s OR last_update IS NULL
        """, (time_threshold,))
        rows = cursor.fetchall()
        if rows:
            for row in rows:
                device_id, ip, last_update = row
                if ip:
                    outdated_ips.append(ip)
                last_update_str = (
                    datetime.fromtimestamp(last_update, pytz.timezone('Asia/Bangkok')).strftime('%Y-%m-%d %H:%M:%S')
                    if last_update else "Never updated"
                )
                logging.info(f"Device ID: {device_id}, IP: {ip}, Last Update: {last_update_str}")
            logging.info(f"Outdated device IPs: {outdated_ips}")
            message = f"⛔ {len(outdated_ips)} devices offline : {', '.join(outdated_ips)}"
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
        else:
            logging.info("No outdated devices found. Outdated device IPs: []")
    except Exception as e:
        logging.error(f"Error in check_outdated_devices: {str(e)}")
    finally:
        cursor.close()
        conn.close()

def delete_old_ips():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        time_threshold = datetime.now() - timedelta(days=1)
        cursor.execute("DELETE FROM ip_records WHERE last_checked < %s", (time_threshold,))
        deleted_rows = cursor.rowcount
        conn.commit()
        logging.info(f"Deleted {deleted_rows} old IPs from ip_records.")
    except Exception as e:
        logging.error(f"Error occurred while deleting old IPs: {str(e)}")
    finally:
        cursor.close()
        conn.close()

scheduler = BackgroundScheduler()
scheduler.add_job(delete_old_ips, 'interval', hours=12)
scheduler.add_job(check_devices, 'interval', minutes=15)
scheduler.start()

@app.get("/device", response_model=List[Device])
async def get_devices(id: Optional[str] = Query(None)):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if id:
            cursor.execute("""
                SELECT id, ip, country_code, ram_total, ram_used, cpu_percent, description, 
                       last_update, counter1, counter2, counter3, counter4, counter5, runtime, restart 
                FROM devices WHERE id = %s
            """, (id,))
            row = cursor.fetchone()
            if not row:
                return []
            return [Device(
                id=row[0],
                ip=row[1],
                country_code=row[2],
                ram_total=row[3],
                ram_used=row[4],
                cpu_percent=row[5],
                description=row[6],
                last_update=row[7],
                counter1=row[8],
                counter2=row[9],
                counter3=row[10],
                counter4=row[11],
                counter5=row[12],
                runtime=row[13],
                restart=row[14]
            )]
        else:
            cursor.execute("""
                SELECT id, ip, country_code, ram_total, ram_used, cpu_percent, description, 
                       last_update, counter1, counter2, counter3, counter4, counter5, runtime, restart 
                FROM devices
            """)
            rows = cursor.fetchall()
            devices = [
                Device(
                    id=row[0],
                    ip=row[1],
                    country_code=row[2],
                    ram_total=row[3],
                    ram_used=row[4],
                    cpu_percent=row[5],
                    description=row[6],
                    last_update=row[7],
                    counter1=row[8],
                    counter2=row[9],
                    counter3=row[10],
                    counter4=row[11],
                    counter5=row[12],
                    runtime=row[13],
                    restart=row[14]
                ) for row in rows
            ]
            return devices
    except Exception as e:
        logging.error(f"Error fetching devices: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/device", response_model=List[Device])
async def add_or_update_device(device: Device):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        current_timestamp = int(datetime.now(pytz.timezone('Asia/Bangkok')).timestamp())
        device_dict = device.dict(exclude_unset=True)
        device_dict['last_update'] = current_timestamp
        cursor.execute("SELECT id FROM devices WHERE id = %s", (device.id,))
        exists = cursor.fetchone()
        if exists:
            update_fields = []
            update_values = []
            for field, value in device_dict.items():
                if field != "id":
                    update_fields.append(f"{field} = %s")
                    update_values.append(value)
            if update_fields:
                update_values.append(device.id)
                query = f"UPDATE devices SET {', '.join(update_fields)} WHERE id = %s"
                cursor.execute(query, update_values)
                conn.commit()
                cursor.execute("""
                    SELECT id, ip, country_code, ram_total, ram_used, cpu_percent, description, 
                           last_update, counter1, counter2, counter3, counter4, counter5, runtime, restart 
                    FROM devices WHERE id = %s
                """, (device.id,))
                row = cursor.fetchone()
                if row:
                    return [Device(
                        id=row[0],
                        ip=row[1],
                        country_code=row[2],
                        ram_total=row[3],
                        ram_used=row[4],
                        cpu_percent=row[5],
                        description=row[6],
                        last_update=row[7],
                        counter1=row[8],
                        counter2=row[9],
                        counter3=row[10],
                        counter4=row[11],
                        counter5=row[12],
                        runtime=row[13],
                        restart=row[14]
                    )]
                else:
                    raise HTTPException(status_code=404, detail=f"Device {device.id} not found after update")
            else:
                cursor.execute("""
                    SELECT id, ip, country_code, ram_total, ram_used, cpu_percent, description, 
                           last_update, counter1, counter2, counter3, counter4, counter5, runtime, restart 
                    FROM devices WHERE id = %s
                """, (device.id,))
                row = cursor.fetchone()
                if row:
                    return [Device(
                        id=row[0],
                        ip=row[1],
                        country_code=row[2],
                        ram_total=row[3],
                        ram_used=row[4],
                        cpu_percent=row[5],
                        description=row[6],
                        last_update=row[7],
                        counter1=row[8],
                        counter2=row[9],
                        counter3=row[10],
                        counter4=row[11],
                        counter5=row[12],
                        runtime=row[13],
                        restart=row[14]
                    )]
                else:
                    raise HTTPException(status_code=404, detail=f"Device {device.id} not found")
        else:
            fields = []
            placeholders = []
            values = []
            for field, value in device_dict.items():
                fields.append(field)
                placeholders.append("%s")
                values.append(value)
            query = f"INSERT INTO devices ({', '.join(fields)}) VALUES ({', '.join(placeholders)}) RETURNING *"
            cursor.execute(query, values)
            row = cursor.fetchone()
            conn.commit()
            return [Device(
                id=row[0],
                ip=row[1],
                country_code=row[2],
                ram_total=row[3],
                ram_used=row[4],
                cpu_percent=row[5],
                description=row[6],
                last_update=row[7],
                counter1=row[8],
                counter2=row[9],
                counter3=row[10],
                counter4=row[11],
                counter5=row[12],
                runtime=row[13],
                restart=row[14]
            )]
    except Exception as e:
        conn.rollback()
        logging.error(f"Error occurred: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.patch("/device", response_model=List[Device])
async def patch_device(device: Device):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM devices WHERE id = %s", (device.id,))
        exists = cursor.fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail=f"Device {device.id} not found")
        current_timestamp = int(datetime.now(pytz.timezone('Asia/Bangkok')).timestamp())
        device_dict = device.dict(exclude_unset=True)
        device_dict['last_update'] = current_timestamp
        update_fields = []
        update_values = []
        for field, value in device_dict.items():
            if field != "id":
                update_fields.append(f"{field} = %s")
                update_values.append(value)
        if update_fields:
            update_values.append(device.id)
            query = f"UPDATE devices SET {', '.join(update_fields)} WHERE id = %s"
            cursor.execute(query, update_values)
            conn.commit()
        cursor.execute("""
            SELECT id, ip, country_code, ram_total, ram_used, cpu_percent, description, 
                   last_update, counter1, counter2, counter3, counter4, counter5, runtime, restart 
            FROM devices WHERE id = %s
        """, (device.id,))
        row = cursor.fetchone()
        if row:
            return [Device(
                id=row[0], ip=row[1], country_code=row[2], ram_total=row[3], ram_used=row[4],
                cpu_percent=row[5], description=row[6], last_update=row[7], counter1=row[8],
                counter2=row[9], counter3=row[10], counter4=row[11], counter5=row[12], runtime=row[13], restart=row[14]
            )]
        else:
            raise HTTPException(status_code=404, detail=f"Device {device.id} not found after update")
    except Exception as e:
        conn.rollback()
        logging.error(f"Error occurred: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.delete("/device")
async def delete_device(id: str = Query(...)):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM devices WHERE id = %s", (id,))
        exists = cursor.fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail=f"Device {id} not found")
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
async def homepage(request: Request):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM ip_records")
        total_ips = cursor.fetchone()[0]
        cursor.execute("SELECT id, ip, country_code, ram_total, ram_used, cpu_percent, description, last_update, counter1, counter2, counter3, counter4, counter5, runtime, restart FROM devices")
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
                "counter4": row[11],
                "counter5": row[12],
                "runtime": row[13],
                "restart": row[14]
            } for row in devices
        ]
    except Exception as e:
        total_ips = "Error"
        device_list = []
        logging.error(f"Error fetching IP count: {str(e)}")
    finally:
        cursor.close()
        conn.close()
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    memory_total = round(memory.total / (1024 ** 3), 2)
    memory_used = round(memory.used / (1024 ** 3), 2)
    memory_percent = memory.percent
    return templates.TemplateResponse("index.html", {
        "request": request,
        "total_ips": total_ips,
        "cpu_percent": cpu_percent,
        "memory_total": memory_total,
        "memory_used": memory_used,
        "memory_percent": memory_percent,
        "devices": sorted(device_list, key=lambda x: x["id"]),
        "now": int(datetime.now(pytz.timezone('Asia/Bangkok')).timestamp())
    })

@app.post("/delete-ips")
def delete_ips_from_button():
    delete_old_ips()
    return RedirectResponse("/", status_code=303)

@app.get("/country")
def get_country(countrys: str, proxy: str):
    country_list = countrys.split(",")
    if proxy not in proxy_country_mapping or not proxy_country_mapping[proxy]:
        proxy_country_mapping[proxy] = random.sample(country_list, len(country_list))
    selected_country = proxy_country_mapping[proxy].pop(0)
    if not proxy_country_mapping[proxy]:
        proxy_country_mapping[proxy] = random.sample(country_list, len(country_list))
    return {"proxy": proxy, "country": selected_country}

@app.get("/geteid")
def get_eid(version: str = "v9.2.0"):
    try:
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
        sdkLoaderEID_match = re.search(r'var sdkLoaderEID = "([^"]+)"', html_content)
        sdkLoaderEID2_match = re.search(r'f.includes\("([^"]+)"\)', html_content)
        sdkLoaderEID = sdkLoaderEID_match.group(1) if sdkLoaderEID_match else None
        sdkLoaderEID2 = sdkLoaderEID2_match.group(1) if sdkLoaderEID2_match else None
        return {"sdkLoaderEID": sdkLoaderEID, "sdkLoaderEID2": sdkLoaderEID2, "version": version}
    except Exception as e:
        logging.error(f"Error fetching EID values: {str(e)}")
        return {"sdkLoaderEID":"318502621","sdkLoaderEID2":"318500618","version":"v9.2.0"}

@app.get("/check")
def check_ip(ip: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT last_checked FROM ip_logs WHERE ip = %s", (ip,))
        row = cursor.fetchone()
        if row:
            last_checked = row[0]
            if datetime.now() - last_checked < timedelta(hours=1):
                return {"allow": False}
        cursor.execute("INSERT INTO ip_logs (ip, last_checked) VALUES (%s, %s) ON CONFLICT (ip) DO UPDATE SET last_checked = EXCLUDED.last_checked",
                       (ip, datetime.now()))
        conn.commit()
        return {"allow": True}
    except Exception as e:
        logging.error(f"Error occurred: {str(e)}")
        return {"error": str(e)}
    finally:
        cursor.close()
        conn.close()

@app.get("/delete")
def delete_old_ips_time(time: int = 24):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        time_threshold = datetime.now() - timedelta(hours=int(time))
        cursor.execute("DELETE FROM ip_records WHERE last_checked < %s", (time_threshold,))
        deleted_rows = cursor.rowcount
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
        ip_obj = IPAddress(ip)
        if groupId:
            cursor.execute("SELECT last_checked FROM ip_records WHERE ip = %s AND groupId = %s", (ip, groupId))
        else:
            cursor.execute("SELECT last_checked FROM ip_records WHERE ip = %s AND groupId IS NULL", (ip,))
        row = cursor.fetchone()
        if row:
            last_checked = row[0]
            if datetime.now() - last_checked < timedelta(hours=int(time)):
                gmt = pytz.timezone('GMT')
                gmt_plus_7 = pytz.timezone('Asia/Bangkok')
                last_checked_gmt = gmt.localize(last_checked).astimezone(gmt_plus_7)
                now_gmt_plus_7 = datetime.now(pytz.timezone('Asia/Bangkok'))
                time_difference = now_gmt_plus_7 - last_checked_gmt
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
        if groupId:
            cursor.execute("INSERT INTO ip_records (ip, last_checked, groupId) VALUES (%s, %s, %s) ON CONFLICT (ip) DO UPDATE SET last_checked = EXCLUDED.last_checked",
                           (ip, datetime.now(), groupId))
        else:
            cursor.execute("INSERT INTO ip_records (ip, last_checked, groupId) VALUES (%s, %s, NULL) ON CONFLICT (ip) DO UPDATE SET last_checked = EXCLUDED.last_checked",
                           (ip, datetime.now()))
        conn.commit()
        return {"allow": True, "last_checked": None}
    except Exception as e:
        logging.error(f"Error occurred: {str(e)}")
        return {"error": str(e)}
    finally:
        cursor.close()
        conn.close()

@app.get("/ipinfo")
async def get_ip_info(request: Request, time: int = 5, groupId: str = None):
    ip = request.query_params.get("ip") or request.client.host
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        response = reader.city(ip)
        country_code = response.country.iso_code
        timezone = response.location.time_zone
        gmt_offset_minutes = 0
        languages = language_map.get(country_code.upper() if country_code else None, 'en')
        if 'en' not in languages.split(','):
            languages = f'en,{languages}'
        if country_code and timezone:
            gmt_offset_seconds = offset_map.get(timezone)
            if gmt_offset_seconds is None:
                if country_code.upper() in country_offset_map and country_offset_map[country_code.upper()]:
                    gmt_offset_seconds = country_offset_map[country_code.upper()][0]
                else:
                    gmt_offset_seconds = 0
            gmt_offset_minutes = gmt_offset_seconds // 60
        in_eu = country_code.upper() in EUROPEAN_COUNTRY_CODES if country_code else False
        ip_obj = IPAddress(ip)
        if groupId:
            cursor.execute("SELECT last_checked FROM ip_records WHERE ip = %s AND groupId = %s", (ip, groupId))
        else:
            cursor.execute("SELECT last_checked FROM ip_records WHERE ip = %s AND groupId IS NULL", (ip,))
        row = cursor.fetchone()
        allow = True
        if row:
            last_checked = row[0]
            if datetime.now() - last_checked < timedelta(hours=int(time)):
                allow = False
        if allow:
            if groupId:
                cursor.execute("INSERT INTO ip_records (ip, last_checked, groupId) VALUES (%s, %s, %s) ON CONFLICT (ip) DO UPDATE SET last_checked = EXCLUDED.last_checked",
                               (ip, datetime.now(), groupId))
            else:
                cursor.execute("INSERT INTO ip_records (ip, last_checked, groupId) VALUES (%s, %s, NULL) ON CONFLICT (ip) DO UPDATE SET last_checked = EXCLUDED.last_checked",
                               (ip, datetime.now()))
            conn.commit()
        data = {
            "ip": ip,
            "country_code": country_code,
            "timezone": timezone,
            "utc_offset": gmt_offset_minutes,
            "languages": languages,
            "in_eu": in_eu,
            "allow": allow
        }
    except geoip2.errors.AddressNotFoundError:
        data = {"error": "IP not found"}
    except Exception as e:
        logging.error(f"Error occurred: {str(e)}")
        data = {"error": str(e)}
    finally:
        cursor.close()
        conn.close()
    return data

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))