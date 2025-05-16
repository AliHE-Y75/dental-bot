#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import asyncio

import datetime as _dt, logging, os, sqlite3, sys
from pathlib import Path
from typing import List, Tuple

try:
    import ssl
except ModuleNotFoundError as e:
    sys.stderr.write("Python built without ssl module\n"); raise SystemExit(1) from e
try:
    from aiogram import Bot, Dispatcher, types
    from aiogram.utils import executor
    from aiogram.contrib.fsm_storage.memory import MemoryStorage
    from aiogram.dispatcher import FSMContext
    from aiogram.dispatcher.filters.state import State, StatesGroup
except ImportError as e:
    sys.stderr.write('install with: pip install "aiogram<3"\n'); raise SystemExit(1) from e

DB_PATH, DATE_FMT = Path("dental_bot.db"), "%Y-%m-%d"

def _connect() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
    c.execute("PRAGMA foreign_keys = 1")
    return c

def init_db() -> None:
    _connect().executescript("""
        CREATE TABLE IF NOT EXISTS clinics(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT, province TEXT, city TEXT,
          UNIQUE(name,province,city) ON CONFLICT IGNORE);
        CREATE TABLE IF NOT EXISTS experiences(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          clinic_id INTEGER,user_id INTEGER,
          start_date TEXT,end_date TEXT,payment TEXT,contract_signed INTEGER,
          patient_culture TEXT,patient_count TEXT,insurance_status TEXT,
          environment TEXT,rating INTEGER,comment TEXT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(clinic_id) REFERENCES clinics(id) ON DELETE CASCADE);""")

def get_or_create_clinic(n:str,p:str,c:str)->int:
    con=_connect();cur=con.cursor()
    cur.execute("SELECT id FROM clinics WHERE name=? AND province=? AND city=?",
                (n.strip(),p.strip(),c.strip()))
    row=cur.fetchone()
    if row:
        cid=row[0]
    else:
        cur.execute("INSERT INTO clinics(name,province,city) VALUES(?,?,?)",
                    (n.strip(),p.strip(),c.strip()))
        cid=cur.lastrowid
    con.close(); return cid

def save_experience(d:dict)->None:
    _connect().execute("""INSERT INTO experiences(
        clinic_id,user_id,start_date,end_date,payment,contract_signed,
        patient_culture,patient_count,insurance_status,environment,
        rating,comment) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (d["clinic_id"],d["user_id"],d.get("start_date"),d.get("end_date"),
         d.get("payment"),d.get("contract_signed"),d.get("patient_culture"),
         d.get("patient_count"),d.get("insurance_status"),d.get("environment"),
         d.get("rating"),d.get("comment","")))

def clinic_stats_by_province(p:str)->List[Tuple[int,str,str,float,int]]:
    cur=_connect().cursor()
    cur.execute("""SELECT cl.id,cl.name,cl.city,
        COALESCE(ROUND(AVG(ex.rating),1),0),COUNT(ex.id)
        FROM clinics cl LEFT JOIN experiences ex ON cl.id=ex.clinic_id
        WHERE cl.province=? GROUP BY cl.id ORDER BY 4 DESC""",(p.strip(),))
    r=cur.fetchall(); cur.connection.close(); return r

def get_experiences_by_clinic(cid:int)->List[Tuple]:
    cur=_connect().cursor()
    cur.execute("""SELECT start_date,end_date,payment,contract_signed,
        patient_culture,patient_count,insurance_status,environment,rating,
        comment,created_at FROM experiences WHERE clinic_id=? ORDER BY created_at DESC""",(cid,))
    r=cur.fetchall(); cur.connection.close(); return r

def get_clinic_by_id(cid:int)->Tuple|None:
    cur=_connect().cursor()
    cur.execute("SELECT name,province,city FROM clinics WHERE id=?",(cid,))
    row=cur.fetchone(); cur.connection.close(); return row

def stars(a:float)->str:
    f=int(a); h=1 if round(a-f,1)>=0.5 else 0; e=5-f-h
    return "★"*f + ("⭑" if h else "") + "☆"*e

def _valid_date(t:str)->bool:
    try: _dt.datetime.strptime(t,DATE_FMT); return True
    except ValueError: return False

class AddExperienceStates(StatesGroup):
    waiting_for_clinic_name=State(); waiting_for_province=State(); waiting_for_city=State()
    waiting_for_start_date=State(); waiting_for_end_date=State(); waiting_for_payment=State()
    waiting_for_contract=State(); waiting_for_patient_culture=State()
    waiting_for_patient_count=State(); waiting_for_insurance_status=State()
    waiting_for_environment=State(); waiting_for_rating=State(); waiting_for_comment=State()

class ViewExperienceStates(StatesGroup):
    waiting_for_province=State(); waiting_for_clinic_selection=State()

TOKEN=os.getenv("BOT_TOKEN")
if not TOKEN: sys.stderr.write("BOT_TOKEN env missing\n"); sys.exit(1)

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
bot=Bot(token=TOKEN,parse_mode=types.ParseMode.HTML)
dp=Dispatcher(bot,storage=MemoryStorage())

PROVINCES=["آذربایجان شرقی","آذربایجان غربی","اردبیل","اصفهان","البرز","ایلام","بوشهر","تهران",
    "چهارمحال و بختیاری","خراسان جنوبی","خراسان رضوی","خراسان شمالی","خوزستان","زنجان","سمنان",
    "سیستان و بلوچستان","فارس","قزوین","قم","کردستان","کرمان","کرمانشاه","کهگیلویه و بویراحمد",
    "گلستان","گیلان","لرستان","مازندران","مرکزی","هرمزگان","همدان","یزد"]
CONTRACT_OPTIONS=["بله","خیر"]

@dp.message_handler(commands=["start"])
async def _start(m:types.Message):
    await m.reply("سلام\n/add_experience\n/view_experiences\n/cancel")

@dp.message_handler(commands=["cancel"],state="*")
@dp.message_handler(lambda x:x.text=="لغو",state="*")
async def _cancel(m:types.Message,s:FSMContext):
    if await s.get_state():
        await s.finish(); await m.reply("لغو شد",reply_markup=types.ReplyKeyboardRemove())
    else:
        await m.reply("عملیاتی نیست")

@dp.message_handler(commands=["add_experience"],state=None)
async def add_start(m:types.Message):
    await AddExperienceStates.waiting_for_clinic_name.set()
    await m.reply("نام کلینیک؟")

@dp.message_handler(state=AddExperienceStates.waiting_for_clinic_name)
async def add_clinic_name(m:types.Message,s:FSMContext):
    async with s.proxy() as d: d["clinic_name"]=m.text.strip()
    kb=types.ReplyKeyboardMarkup(row_width=3,resize_keyboard=True).add(*PROVINCES)
    await AddExperienceStates.waiting_for_province.set()
    await m.reply("استان؟",reply_markup=kb)

@dp.message_handler(state=AddExperienceStates.waiting_for_province)
async def add_province(m:types.Message,s:FSMContext):
    t=m.text.strip()
    if t not in PROVINCES: await m.reply("استان نامعتبر"); return
    async with s.proxy() as d: d["province"]=t
    await AddExperienceStates.waiting_for_city.set()
    await m.reply("شهر؟",reply_markup=types.ReplyKeyboardRemove())

@dp.message_handler(state=AddExperienceStates.waiting_for_city)
async def add_city(m:types.Message,s:FSMContext):
    async with s.proxy() as d: d["city"]=m.text.strip()
    await AddExperienceStates.waiting_for_start_date.set()
    await m.reply(f"تاریخ شروع {DATE_FMT}")

@dp.message_handler(state=AddExperienceStates.waiting_for_start_date)
async def add_start_date(m:types.Message,s:FSMContext):
    if not _valid_date(m.text.strip()): await m.reply("فرمت غلط"); return
    async with s.proxy() as d: d["start_date"]=m.text.strip()
    await AddExperienceStates.waiting_for_end_date.set()
    await m.reply("تاریخ پایان یا 'نامشخص'")

@dp.message_handler(state=AddExperienceStates.waiting_for_end_date)
async def add_end_date(m:types.Message,s:FSMContext):
    t=m.text.strip()
    if t.lower() not in {"نامشخص","نامعلوم"} and not _valid_date(t): await m.reply("فرمت غلط"); return
    async with s.proxy() as d: d["end_date"]=None if t.lower() in {"نامشخص","نامعلوم"} else t
    await AddExperienceStates.waiting_for_payment.set()
    await m.reply("وضعیت پرداخت؟")

@dp.message_handler(state=AddExperienceStates.waiting_for_payment)
async def add_payment(m:types.Message,s:FSMContext):
    async with s.proxy() as d: d["payment"]=m.text.strip()
    kb=types.ReplyKeyboardMarkup(row_width=2,resize_keyboard=True).add(*CONTRACT_OPTIONS)
    await AddExperienceStates.waiting_for_contract.set()
    await m.reply("قرارداد کتبی؟",reply_markup=kb)

@dp.message_handler(state=AddExperienceStates.waiting_for_contract)
async def add_contract(m:types.Message,s:FSMContext):
    if m.text.strip() not in CONTRACT_OPTIONS: await m.reply("بله/خیر"); return
    async with s.proxy() as d: d["contract_signed"]=1 if m.text.strip()=="بله" else 0
    await AddExperienceStates.waiting_for_patient_culture.set()
    await m.reply("فرهنگ بیماران؟",reply_markup=types.ReplyKeyboardRemove())

@dp.message_handler(state=AddExperienceStates.waiting_for_patient_culture)
async def add_pculture(m:types.Message,s:FSMContext):
    async with s.proxy() as d: d["patient_culture"]=m.text.strip()
    await AddExperienceStates.waiting_for_patient_count.set()
    await m.reply("تعداد بیماران؟")

@dp.message_handler(state=AddExperienceStates.waiting_for_patient_count)
async def add_pcount(m:types.Message,s:FSMContext):
    async with s.proxy() as d: d["patient_count"]=m.text.strip()
    await AddExperienceStates.waiting_for_insurance_status.set()
    await m.reply("وضعیت بیمه‌ها؟")

@dp.message_handler(state=AddExperienceStates.waiting_for_insurance_status)
async def add_ins(m:types.Message,s:FSMContext):
    async with s.proxy() as d: d["insurance_status"]=m.text.strip()
    await AddExperienceStates.waiting_for_environment.set()
    await m.reply("محیط کاری؟")

@dp.message_handler(state=AddExperienceStates.waiting_for_environment)
async def add_env(m:types.Message,s:FSMContext):
    async with s.proxy() as d: d["environment"]=m.text.strip()
    kb=types.ReplyKeyboardMarkup(row_width=5,resize_keyboard=True).add(*map(str,range(1,6)))
    await AddExperienceStates.waiting_for_rating.set()
    await m.reply("امتیاز 1-5",reply_markup=kb)

@dp.message_handler(state=AddExperienceStates.waiting_for_rating)
async def add_rating(m:types.Message,s:FSMContext):
    try: r=int(m.text.strip()); assert 1<=r<=5
    except: await m.reply("عدد 1 تا 5"); return
    async with s.proxy() as d: d["rating"]=r
    kb=types.ReplyKeyboardMarkup(resize_keyboard=True).add("رد شدن")
    await AddExperienceStates.waiting_for_comment.set()
    await m.reply("توضیحات یا 'رد شدن'",reply_markup=kb)

@dp.message_handler(state=AddExperienceStates.waiting_for_comment)
async def add_comment(m:types.Message,s:FSMContext):
    comment = "" if m.text.strip().lower()=="رد شدن" else m.text.strip()
    async with s.proxy() as d:
        d["comment"]=comment; d["user_id"]=m.from_user.id
        d["clinic_id"]=get_or_create_clinic(d["clinic_name"],d["province"],d["city"])
        save_experience(d)
    await m.reply("ثبت شد",reply_markup=types.ReplyKeyboardRemove())
    await s.finish()

@dp.message_handler(commands=["view_experiences"],state=None)
async def view_start(m:types.Message):
    kb=types.ReplyKeyboardMarkup(row_width=3,resize_keyboard=True).add(*PROVINCES)
    await ViewExperienceStates.waiting_for_province.set()
    await m.reply("استان؟",reply_markup=kb)

@dp.message_handler(state=ViewExperienceStates.waiting_for_province)
async def view_province(m:types.Message,s:FSMContext):
    p=m.text.strip()
    if p not in PROVINCES: await m.reply("نامعتبر"); return
    await s.update_data(province=p)
    stats=clinic_stats_by_province(p)
    if not stats:
        await m.reply("هیچ تجربه‌ای نیست",reply_markup=types.ReplyKeyboardRemove())
        await s.finish(); return
    kb=types.InlineKeyboardMarkup(row_width=1); text=""
    for cid,n,c,a,v in stats:
        text+=f"{n} ({c}) {stars(a)} ({v})\n"
        kb.insert(types.InlineKeyboardButton(n,callback_data=f"v_{cid}"))
    await m.reply(text,reply_markup=kb)
    await ViewExperienceStates.waiting_for_clinic_selection.set()

@dp.callback_query_handler(lambda c:c.data.startswith("v_"),
                           state=ViewExperienceStates.waiting_for_clinic_selection)
async def view_clinic(call:types.CallbackQuery,s:FSMContext):
    cid=int(call.data[2:])
    info=get_clinic_by_id(cid)
    if not info: await call.answer("نیست"); return
    exps=get_experiences_by_clinic(cid)
    name,prov,city=info
    header=f"{name} ({city}، {prov})\n\n"
    parts,buf=[header], ""
    for e in exps:
        st,en,pa,ct,pc,pcnt,ins,env,rt,com,cr=e
        lines=[
            stars(float(rt)),
            f"{st}-{en or 'نامشخص'}",
            pa or "-",
            "بله" if ct else "خیر",
            pc or "-",
            pcnt or "-",
            ins or "-",
            env or "-"
        ]
        if com: lines.append(com)
        lines.append(cr)
        blk="\n".join(lines) + "\n-----\n"
        if len(buf)+len(blk)>3800:
            parts.append(buf); buf=blk
        else:
            buf+=blk
    parts.append(buf)
    for p in parts: await bot.send_message(call.from_user.id,p)
    await call.answer(); await s.finish()

# ==[1]== وارد کردن FastAPI و threading
from fastapi import FastAPI
import threading

# ==[2]== تعریف شیء FastAPI
app = FastAPI()

# ---------------------------------
# ... (کدهای قبلی Aiogram؛ مثل importها، تعریف bot و dp، هندلرها ...)
# ---------------------------------

# ==[3]== تابعی که ربات را اجرا می‌کند
def _start_bot():
    # توجه: dp را همین فایل ساخته است، بنابراین مستقیم استفاده می‌شود
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    from aiogram import executor
    executor.start_polling(dp, skip_updates=True, loop=loop)

# ==[4]== 이벤트 استارتاپ FastAPI
@app.on_event("startup")
async def on_startup():
    threading.Thread(target=_start_bot, daemon=True).start()

# ==[5]== یک روت ساده برای health-check
@app.get("/")
async def root():
    return {"status": "ok"}

# ==[6]==  ⬇️ این دو خط قدیمی را **حذف یا کامنت** کنید
# if __name__ == "__main__":
#     executor.start_polling(dp, skip_updates=True)

