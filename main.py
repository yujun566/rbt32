"""
Discord RPG Bot v3 - 통합 main.py
모든 기능을 단일 파일로 통합 (개발자 명령어 외 모두 버튼 방식)
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import re
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ──────────────────────────────────────────────────────────────────────────
# 0. .env 파일 로드
# ──────────────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    load_dotenv(_env_path)
except ImportError:
    pass

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("rpg_bot")

# ══════════════════════════════════════════════════════════════════════════
# 1. 설정 (Settings)
# ══════════════════════════════════════════════════════════════════════════
@dataclass
class Settings:
    @property
    def token(self) -> str:
        return os.getenv("DISCORD_TOKEN", "TOKEN_HERE")
    @property
    def database_path(self) -> str:
        return os.getenv("RPG_DB_PATH", "discord_rpg.sqlite3")
    @property
    def dev_ids(self) -> List[int]:
        ids = os.getenv("DEV_IDS", "")
        return [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
    @property
    def bug_channel_id(self) -> Optional[int]:
        v = os.getenv("BUG_CHANNEL_ID", "")
        return int(v) if v.isdigit() else None
    @property
    def announce_channel_ids(self) -> List[int]:
        ids = os.getenv("ANNOUNCE_CHANNEL_IDS", "")
        return [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]

    world_width: int = int(os.getenv("WORLD_WIDTH", "200"))
    world_height: int = int(os.getenv("WORLD_HEIGHT", "200"))
    trade_tax_percent: int = 5
    season_number: int = 1

settings = Settings()
WORLD_W, WORLD_H = settings.world_width, settings.world_height

# ══════════════════════════════════════════════════════════════════════════
# 2. 레이트리밋 (쿨다운 관리)
# ══════════════════════════════════════════════════════════════════════════
_cooldowns: Dict[str, Dict[int, float]] = defaultdict(dict)

def check_cooldown(cmd_key: str, uid: int, seconds: float) -> Optional[float]:
    now = time.time()
    last = _cooldowns[cmd_key].get(uid, 0)
    remaining = seconds - (now - last)
    if remaining > 0:
        return remaining
    _cooldowns[cmd_key][uid] = now
    return None

# ══════════════════════════════════════════════════════════════════════════
# 3. 데이터베이스 계층
# ══════════════════════════════════════════════════════════════════════════
DB_PATH = Path(settings.database_path)
_db_conn: Optional[aiosqlite.Connection] = None
_db_lock = asyncio.Lock()

async def _get_conn():
    global _db_conn
    if _db_conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _db_conn = await aiosqlite.connect(DB_PATH, timeout=30)
        _db_conn.row_factory = aiosqlite.Row
        await _db_conn.execute("PRAGMA journal_mode=WAL;")
        await _db_conn.commit()
    return _db_conn

async def execute(query, params=()):
    async with _db_lock:
        db = await _get_conn()
        await db.execute(query, tuple(params))
        await db.commit()

async def execute_insert(query, params=()):
    async with _db_lock:
        db = await _get_conn()
        cursor = await db.execute(query, tuple(params))
        await db.commit()
        return cursor.lastrowid

async def fetch_one(query, params=()):
    async with _db_lock:
        db = await _get_conn()
        cursor = await db.execute(query, tuple(params))
        return await cursor.fetchone()

async def fetch_all(query, params=()):
    async with _db_lock:
        db = await _get_conn()
        cursor = await db.execute(query, tuple(params))
        return await cursor.fetchall()

def j(d): return json.dumps(d, ensure_ascii=False)
def uj(s): return json.loads(s) if s else {}


# ══════════════════════════════════════════════════════════════════════════
# 4. DB 초기화 (전체 테이블)
# ══════════════════════════════════════════════════════════════════════════
async def init_db():
    async with _db_lock:
        db = await _get_conn()
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS players (
                user_id INTEGER PRIMARY KEY, username TEXT, guild_id TEXT,
                job TEXT DEFAULT '초보자', title TEXT DEFAULT '',
                x INTEGER DEFAULT 100, y INTEGER DEFAULT 100,
                hp INTEGER DEFAULT 150, max_hp INTEGER DEFAULT 150,
                mp INTEGER DEFAULT 50, max_mp INTEGER DEFAULT 50,
                stamina INTEGER DEFAULT 100, max_stamina INTEGER DEFAULT 100,
                level INTEGER DEFAULT 1, exp INTEGER DEFAULT 0,
                coins INTEGER DEFAULT 1000, gems INTEGER DEFAULT 10,
                attack INTEGER DEFAULT 10, defense INTEGER DEFAULT 5,
                crit INTEGER DEFAULT 5, facing TEXT DEFAULT 'S',
                biome TEXT DEFAULT '평원',
                equipment_json TEXT DEFAULT '{}',
                state_json TEXT DEFAULT '{}',
                appearance_json TEXT DEFAULT '{}',
                achievements_json TEXT DEFAULT '[]',
                tutorial_step INTEGER DEFAULT 0,
                season_bp_level INTEGER DEFAULT 0,
                season_bp_exp INTEGER DEFAULT 0,
                partner_id INTEGER DEFAULT 0,
                brother_id INTEGER DEFAULT 0,
                invite_code TEXT DEFAULT '',
                invited_by INTEGER DEFAULT 0,
                voice_bonus_until TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS inventory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, item_code TEXT, item_name TEXT,
                item_type TEXT, rarity TEXT, qty INTEGER DEFAULT 1,
                power INTEGER DEFAULT 0, defense INTEGER DEFAULT 0,
                enchant_level INTEGER DEFAULT 0, meta_json TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS world_tiles (
                x INTEGER, y INTEGER, tile_type TEXT,
                PRIMARY KEY(x,y)
            );
            CREATE TABLE IF NOT EXISTS guilds (
                guild_name TEXT PRIMARY KEY, owner_id INTEGER,
                treasury INTEGER DEFAULT 0, notice TEXT DEFAULT '',
                members_json TEXT DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS battle_sessions (
                battle_id TEXT PRIMARY KEY,
                challenger_id INTEGER,
                target_id INTEGER DEFAULT 0,
                session_type TEXT DEFAULT 'dungeon',
                state_json TEXT
            );
            CREATE TABLE IF NOT EXISTS auction_house (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_id INTEGER, item_json TEXT,
                min_bid INTEGER DEFAULT 0, current_bid INTEGER DEFAULT 0,
                highest_bidder INTEGER DEFAULT 0,
                end_at TEXT, watch_list_json TEXT DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS raid_sessions (
                raid_id TEXT PRIMARY KEY,
                boss_name TEXT, boss_hp INTEGER, boss_max_hp INTEGER,
                participants_json TEXT DEFAULT '{}',
                state TEXT DEFAULT 'active',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS quests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, quest_code TEXT,
                progress INTEGER DEFAULT 0, completed INTEGER DEFAULT 0,
                accepted_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS fishing_contest (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, score INTEGER DEFAULT 0,
                season_week TEXT
            );
            CREATE TABLE IF NOT EXISTS rankings (
                user_id INTEGER, category TEXT, score INTEGER DEFAULT 0,
                season TEXT DEFAULT 'global',
                PRIMARY KEY(user_id, category, season)
            );
            CREATE TABLE IF NOT EXISTS houses (
                x INTEGER, y INTEGER, owner_id INTEGER,
                furniture_json TEXT DEFAULT '[]',
                PRIMARY KEY(x, y)
            );
            CREATE TABLE IF NOT EXISTS marriages (
                user_id INTEGER PRIMARY KEY,
                partner_id INTEGER, married_at TEXT
            );
            CREATE TABLE IF NOT EXISTS error_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT, error_text TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS battlepass (
                user_id INTEGER PRIMARY KEY,
                season INTEGER DEFAULT 1,
                premium INTEGER DEFAULT 0,
                level INTEGER DEFAULT 0,
                exp INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS global_chat_channels (
                guild_id TEXT PRIMARY KEY,
                channel_id TEXT
            );
            CREATE TABLE IF NOT EXISTS player_settings (
                user_id INTEGER PRIMARY KEY,
                auto_refresh INTEGER DEFAULT 0,
                theme TEXT DEFAULT 'default'
            );
        """)
        await db.commit()

# ══════════════════════════════════════════════════════════════════════════
# 5. 아이템 카탈로그 (600종+)
# ══════════════════════════════════════════════════════════════════════════
@dataclass(slots=True)
class Item:
    code: str; name: str; item_type: str; rarity: str
    power: int = 0; defense: int = 0; meta: dict = field(default_factory=dict)

ITEM_CATALOG: Dict[str, Item] = {}
RARITIES = ["일반", "희귀", "영웅", "전설", "신화", "초월"]
WEAPON_TYPES = ["검", "활", "지팡이", "단검", "둔기", "창"]
ARMOR_TYPES = ["갑옷", "투구", "장갑", "신발"]
RARITY_EMOJI = {"일반":"⚪","희귀":"🔵","영웅":"🟣","전설":"🟡","신화":"🔴","초월":"🌈"}

def _build_items():
    mats = ["나무","돌","철","금","다이아몬드","미스릴","오리할콘","드래곤하트","마나수정","고대유물"]
    for m in mats:
        ITEM_CATALOG[f"mat_{m}"] = Item(f"mat_{m}", f"📦 {m}", "재료", "일반")

    consumables = [
        ("potion_hp_s","🧪 소형 HP 포션","소비","일반",0,0,{"heal":50}),
        ("potion_hp_m","🧪 중형 HP 포션","소비","희귀",0,0,{"heal":150}),
        ("potion_hp_l","🧪 대형 HP 포션","소비","영웅",0,0,{"heal":400}),
        ("potion_mp_s","💧 소형 MP 포션","소비","일반",0,0,{"mp_restore":30}),
        ("potion_mp_m","💧 중형 MP 포션","소비","희귀",0,0,{"mp_restore":100}),
        ("scroll_teleport","📜 귀환 스크롤","소비","일반",0,0,{"teleport":"town"}),
        ("scroll_dungeon","📜 던전 스크롤","소비","희귀",0,0,{"teleport":"dungeon"}),
        ("fishing_rod","🎣 낚싯대","도구","일반",0,0,{}),
        ("fishing_rod_gold","🎣 황금 낚싯대","도구","전설",0,0,{"bonus":2}),
        ("invite_token","🎟️ 초대 토큰","소비","일반",0,0,{}),
        ("event_ticket","🎫 이벤트 티켓","소비","희귀",0,0,{}),
    ]
    for c in consumables:
        ITEM_CATALOG[c[0]] = Item(c[0],c[1],c[2],c[3],c[4],c[5],c[6])

    for t_idx, t in enumerate(WEAPON_TYPES):
        for lv in range(1, 51):
            for r_idx, r in enumerate(RARITIES):
                code = f"w_{t_idx}_{lv}_{r_idx}"
                pwr = lv * 10 + r_idx * 25
                ITEM_CATALOG[code] = Item(code, f"{RARITY_EMOJI[r]} {r} {t} Lv.{lv}", "무기", r, power=pwr)

    for t_idx, t in enumerate(ARMOR_TYPES):
        for lv in range(1, 51):
            for r_idx, r in enumerate(RARITIES):
                code = f"a_{t_idx}_{lv}_{r_idx}"
                df = lv * 5 + r_idx * 15
                ITEM_CATALOG[code] = Item(code, f"{RARITY_EMOJI[r]} {r} {t} Lv.{lv}", "방어구", r, defense=df)

    craft_items = [
        ("craft_fire_sword","🔥 화염검","무기","영웅",300,0,{"element":"fire"}),
        ("craft_ice_bow","❄️ 빙결활","무기","영웅",280,0,{"element":"ice"}),
        ("craft_thunder_staff","⚡ 번개 지팡이","무기","전설",500,0,{"element":"thunder"}),
        ("craft_dragon_armor","🐉 드래곤 갑옷","방어구","전설",0,400,{"element":"dragon"}),
        ("craft_shadow_cloak","🌑 그림자 망토","방어구","신화",0,600,{"element":"dark"}),
        ("craft_holy_shield","✨ 성스러운 방패","방어구","신화",50,700,{"element":"holy"}),
        ("dev_god_armor","👑 신의 갑옷","방어구","초월",0,9999999,{"dev":True, "hp_inf":True}),
    ]
    for c in craft_items:
        ITEM_CATALOG[c[0]] = Item(c[0],c[1],c[2],c[3],c[4],c[5],c[6])

_build_items()

# 제작 레시피 (전체 아이템 제작법)
CRAFT_RECIPES = {
    "craft_fire_sword": {"재료": {"mat_철":10, "mat_드래곤하트":2}, "코인":5000},
    "craft_ice_bow":    {"재료": {"mat_미스릴":10, "mat_마나수정":3}, "코인":5000},
    "craft_thunder_staff":{"재료": {"mat_오리할콘":10, "mat_마나수정":5}, "코인":10000},
    "craft_dragon_armor":{"재료": {"mat_드래곤하트":10, "mat_철":20}, "코인":15000},
    "craft_shadow_cloak":{"재료": {"mat_고대유물":10, "mat_미스릴":15}, "코인":20000},
    "craft_holy_shield": {"재료": {"mat_마나수정":20, "mat_오리할콘":10}, "코인":25000},
    "potion_hp_m":      {"재료": {"mat_나무":5, "mat_돌":5}, "코인":500},
    "potion_hp_l":      {"재료": {"mat_철":5, "mat_금":2}, "코인":2000},
    "fishing_rod_gold": {"재료": {"mat_금":10, "mat_다이아몬드":2}, "코인":10000},
}

# ══════════════════════════════════════════════════════════════════════════
# 6. 스킬 트리
# ══════════════════════════════════════════════════════════════════════════
SKILL_TREE = {
    "전사": {
        "power_strike": {"name":"💥 파워 스트라이크","mp":10,"mult":2.0,"desc":"강력한 일격"},
        "shield_bash":  {"name":"🛡️ 쉴드 배쉬","mp":15,"mult":1.5,"stun":True,"desc":"기절 유발"},
        "berserk":      {"name":"😤 광전사","mp":20,"mult":3.0,"desc":"HP 30% 이하 시 3배 데미지"},
        "war_cry":      {"name":"📣 전투 함성","mp":25,"mult":1.0,"team_atk":1.3,"desc":"파티 공격력 30% 증가"},
    },
    "궁수": {
        "double_shot":  {"name":"🏹 더블 샷","mp":12,"mult":2.2,"desc":"두 번 공격"},
        "poison_arrow": {"name":"🧪 독 화살","mp":18,"mult":1.8,"dot":True,"desc":"독 데미지 지속"},
        "eagle_eye":    {"name":"🦅 독수리 눈","mp":15,"mult":2.5,"crit_boost":30,"desc":"치명타율 30% 증가"},
        "rain_of_arrows":{"name":"🌧️ 화살비","mp":30,"mult":1.5,"aoe":True,"desc":"전체 공격"},
    },
    "마법사": {
        "fireball":     {"name":"🔥 파이어볼","mp":20,"mult":3.0,"desc":"강력한 화염 공격"},
        "mana_shield":  {"name":"🌀 마나 쉴드","mp":25,"def_boost":50,"desc":"방어력 50 증가"},
        "blizzard":     {"name":"❄️ 블리자드","mp":35,"mult":2.5,"slow":True,"desc":"광역 빙결"},
        "meteor":       {"name":"☄️ 메테오","mp":50,"mult":5.0,"desc":"최강 마법 공격"},
    },
    "성직자": {
        "heal":         {"name":"💚 힐","mp":15,"heal":100,"desc":"HP 회복"},
        "holy_light":   {"name":"✨ 성광","mp":20,"mult":2.0,"desc":"언데드 특효"},
        "resurrection": {"name":"🌟 부활","mp":50,"revive":True,"desc":"전투 중 부활"},
        "blessing":     {"name":"🙏 축복","mp":30,"team_def":1.3,"desc":"파티 방어력 30% 증가"},
    },
    "도적": {
        "backstab":     {"name":"🗡️ 백스탭","mp":10,"mult":3.5,"desc":"뒤에서 치명타"},
        "smoke_bomb":   {"name":"💨 연막탄","mp":15,"evade":True,"desc":"1턴 회피"},
        "steal":        {"name":"💰 도둑질","mp":12,"steal":True,"desc":"적 코인 탈취"},
        "shadow_step":  {"name":"👣 그림자 발걸음","mp":20,"mult":2.0,"first":True,"desc":"선제 공격"},
    },
}

# ══════════════════════════════════════════════════════════════════════════
# 7. 몬스터 및 보스 데이터
# ══════════════════════════════════════════════════════════════════════════
MONSTERS = [
    {"name":"🐺 늑대","hp":80,"atk":12,"def":3,"exp":20,"coins":15,"drop_rate":0.3},
    {"name":"🐗 멧돼지","hp":120,"atk":18,"def":5,"exp":35,"coins":25,"drop_rate":0.35},
    {"name":"💀 해골병사","hp":100,"atk":22,"def":8,"exp":45,"coins":30,"drop_rate":0.4},
    {"name":"🧟 좀비","hp":150,"atk":15,"def":10,"exp":50,"coins":35,"drop_rate":0.4},
    {"name":"🧙 다크 마법사","hp":90,"atk":35,"def":5,"exp":70,"coins":50,"drop_rate":0.45},
    {"name":"🐉 드래곤 새끼","hp":300,"atk":40,"def":20,"exp":150,"coins":100,"drop_rate":0.6},
    {"name":"👹 오크 전사","hp":200,"atk":30,"def":15,"exp":80,"coins":60,"drop_rate":0.5},
    {"name":"🦇 흡혈귀","hp":180,"atk":28,"def":12,"exp":90,"coins":70,"drop_rate":0.5},
    {"name":"🕷️ 거대 거미","hp":160,"atk":25,"def":8,"exp":75,"coins":55,"drop_rate":0.45},
    {"name":"🌊 워터 엘리멘탈","hp":220,"atk":32,"def":18,"exp":110,"coins":80,"drop_rate":0.55},
]

BOSSES = [
    {"name":"🐲 고룡 발로스","hp":5000,"atk":120,"def":60,"exp":2000,"coins":5000},
    {"name":"💀 리치 왕","hp":8000,"atk":150,"def":40,"exp":3000,"coins":8000},
    {"name":"👿 마왕 제라스","hp":15000,"atk":200,"def":80,"exp":5000,"coins":15000},
    {"name":"🌑 어둠의 신","hp":30000,"atk":300,"def":120,"exp":10000,"coins":30000},
]

# ══════════════════════════════════════════════════════════════════════════
# 8. NPC 및 랜드마크 데이터
# ══════════════════════════════════════════════════════════════════════════
NPCS = {
    "quest_npc_1": {
        "name":"📜 퀘스트 마스터 에리온",
        "x":100,"y":98,
        "dialogue":"용사여, 마을을 위협하는 몬스터들을 처치해주시오!",
        "quests":["kill_wolf_10","kill_boss_1","collect_mat_5"]
    },
    "shop_npc_1": {
        "name":"🏪 상인 마르코",
        "x":102,"y":100,
        "dialogue":"어서오세요! 좋은 물건 많습니다.",
        "shop_items":["potion_hp_s","potion_hp_m","potion_mp_s","scroll_teleport","fishing_rod"]
    },
    "blacksmith_npc": {
        "name":"⚒️ 대장장이 볼드",
        "x":98,"y":100,
        "dialogue":"강화와 제작은 저에게 맡기세요!",
        "services":["enchant","craft"]
    },
    "guild_npc": {
        "name":"🏰 길드 관리인 세라",
        "x":100,"y":102,
        "dialogue":"길드를 창설하거나 가입하시겠습니까?",
        "services":["guild"]
    },
    "pvp_npc": {
        "name":"⚔️ 콜로세움 관리인 마르스",
        "x":150,"y":150,
        "dialogue":"콜로세움에 오신 것을 환영합니다! 실력을 겨뤄보시죠.",
        "services":["pvp"]
    },
}

QUEST_DATA = {
    "kill_wolf_10": {"name":"늑대 사냥","desc":"늑대 10마리 처치","target":10,"type":"kill","monster":"늑대","reward_coins":500,"reward_exp":200,"reward_item":"potion_hp_m"},
    "kill_boss_1":  {"name":"보스 토벌","desc":"던전 보스 1마리 처치","target":1,"type":"kill_boss","reward_coins":2000,"reward_exp":1000,"reward_item":"w_0_10_2"},
    "collect_mat_5":{"name":"재료 수집","desc":"철 재료 5개 수집","target":5,"type":"collect","item":"mat_철","reward_coins":300,"reward_exp":150,"reward_item":"potion_mp_m"},
    "fish_10":      {"name":"낚시왕","desc":"물고기 10마리 잡기","target":10,"type":"fish","reward_coins":400,"reward_exp":180,"reward_item":"fishing_rod_gold"},
    "explore_50":   {"name":"탐험가","desc":"50칸 이동","target":50,"type":"move","reward_coins":200,"reward_exp":100,"reward_item":"scroll_teleport"},
}

LANDMARKS = {
    (100,100): {"type":"town","name":"🏘️ 시작 마을","desc":"안전 지역. 회복 가능."},
    (50,50):   {"type":"dungeon","name":"🏰 고대 던전","desc":"위험! 강한 몬스터 출현."},
    (150,150): {"type":"colosseum","name":"⚔️ 콜로세움","desc":"PVP 전용 구역."},
    (30,170):  {"type":"shop","name":"🏪 대상인 거리","desc":"희귀 아이템 거래 가능."},
    (170,30):  {"type":"fishing","name":"🎣 낚시터","desc":"다양한 물고기 서식."},
    (100,50):  {"type":"halloween","name":"🎃 할로윈 존","desc":"한정 이벤트 지역."},
    (100,150): {"type":"christmas","name":"🎄 크리스마스 존","desc":"한정 이벤트 지역."},
}

FISH_TABLE = [
    ("🐟 잡어",5,10),("🐠 열대어",15,30),("🦈 상어",50,100),
    ("🐙 문어",80,150),("🐋 고래",200,500),("✨ 전설의 물고기",1000,3000),
]

SHOP_ITEMS = {
    "potion_hp_s":  {"price":100},
    "potion_hp_m":  {"price":300},
    "potion_hp_l":  {"price":800},
    "potion_mp_s":  {"price":80},
    "potion_mp_m":  {"price":250},
    "scroll_teleport":{"price":200},
    "scroll_dungeon":{"price":500},
    "fishing_rod":  {"price":150},
}

TITLES = {
    "몬스터 헌터": {"desc":"몬스터 100마리 처치","condition":"kill_count>=100"},
    "대상인":       {"desc":"거래소 거래 50회","condition":"trade_count>=50"},
    "탐험가":       {"desc":"1000칸 이동","condition":"move_count>=1000"},
    "낚시왕":       {"desc":"물고기 100마리","condition":"fish_count>=100"},
    "레이드 영웅":  {"desc":"레이드 보스 10회 격파","condition":"raid_count>=10"},
    "PVP 챔피언":   {"desc":"PVP 50승","condition":"pvp_win>=50"},
    "장인":         {"desc":"아이템 제작 20회","condition":"craft_count>=20"},
    "부자":         {"desc":"코인 100만 보유","condition":"coins>=1000000"},
}


# ══════════════════════════════════════════════════════════════════════════
# 9. 플레이어 레코드
# ══════════════════════════════════════════════════════════════════════════
@dataclass(slots=True)
class PlayerRecord:
    user_id: int; username: str; guild_id: Optional[str] = None
    job: str = "초보자"; title: str = ""
    x: int = 100; y: int = 100
    hp: int = 150; max_hp: int = 150
    mp: int = 50; max_mp: int = 50
    stamina: int = 100; max_stamina: int = 100
    level: int = 1; exp: int = 0
    coins: int = 1000; gems: int = 10
    attack: int = 10; defense: int = 5; crit: int = 5
    facing: str = "S"; biome: str = "평원"
    equipment: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    appearance: dict = field(default_factory=dict)
    achievements: list = field(default_factory=list)
    tutorial_step: int = 0
    season_bp_level: int = 0; season_bp_exp: int = 0
    partner_id: int = 0; brother_id: int = 0
    invite_code: str = ""; invited_by: int = 0
    voice_bonus_until: str = ""

    @classmethod
    def from_row(cls, r):
        # r이 dict가 아닐 경우 처리 (Row 객체 대응)
        d = dict(r) if not isinstance(r, dict) else r
        return cls(
            user_id=d["user_id"], username=d["username"], guild_id=d.get("guild_id"),
            job=d["job"], title=d["title"] or "",
            x=d["x"], y=d["y"], hp=d["hp"], max_hp=d["max_hp"],
            mp=d["mp"], max_mp=d["max_mp"],
            stamina=d["stamina"], max_stamina=d["max_stamina"],
            level=d["level"], exp=d["exp"],
            coins=d["coins"], gems=d["gems"],
            attack=d["attack"], defense=d["defense"], crit=d["crit"],
            facing=d["facing"], biome=d["biome"],
            equipment=uj(d.get("equipment_json", "{}")),
            state=uj(d.get("state_json", "{}")),
            appearance=uj(d.get("appearance_json", "{}")),
            achievements=uj(d.get("achievements_json", "[]")) if d.get("achievements_json") else [],
            tutorial_step=d.get("tutorial_step") or 0,
            season_bp_level=d.get("season_bp_level") or 0,
            season_bp_exp=d.get("season_bp_exp") or 0,
            partner_id=d.get("partner_id") or 0,
            brother_id=d.get("brother_id") or 0,
            invite_code=d.get("invite_code") or "",
            invited_by=d.get("invited_by") or 0,
            voice_bonus_until=d.get("voice_bonus_until") or ""
        )

async def save_player(p: PlayerRecord):
    await execute("""
        UPDATE players SET username=?,guild_id=?,job=?,title=?,x=?,y=?,hp=?,max_hp=?,mp=?,max_mp=?,
        stamina=?,max_stamina=?,level=?,exp=?,coins=?,gems=?,attack=?,defense=?,crit=?,
        facing=?,biome=?,equipment_json=?,state_json=?,appearance_json=?,achievements_json=?,
        tutorial_step=?,season_bp_level=?,season_bp_exp=?,partner_id=?,brother_id=?,
        invite_code=?,invited_by=?,voice_bonus_until=? WHERE user_id=?
    """, (p.username,p.guild_id,p.job,p.title,p.x,p.y,p.hp,p.max_hp,p.mp,p.max_mp,
          p.stamina,p.max_stamina,p.level,p.exp,p.coins,p.gems,p.attack,p.defense,p.crit,
          p.facing,p.biome,j(p.equipment),j(p.state),j(p.appearance),j(p.achievements),
          p.tutorial_step,p.season_bp_level,p.season_bp_exp,p.partner_id,p.brother_id,
          p.invite_code,p.invited_by,p.voice_bonus_until,p.user_id))

async def ensure_player(uid: int, name: str, gid=None) -> PlayerRecord:
    row = await fetch_one("SELECT * FROM players WHERE user_id=?", (uid,))
    if row: return PlayerRecord.from_row(row)
    code = uuid.uuid4().hex[:8].upper()
    await execute(
        "INSERT INTO players (user_id,username,guild_id,invite_code) VALUES (?,?,?,?)",
        (uid, name, gid, code)
    )
    return await ensure_player(uid, name, gid)

async def add_exp(p: PlayerRecord, exp: int):
    p.exp += exp
    needed = p.level * 100
    leveled = False
    while p.exp >= needed:
        p.exp -= needed
        p.level += 1
        p.max_hp += 20; p.hp = p.max_hp
        p.max_mp += 10; p.mp = p.max_mp
        p.attack += 3; p.defense += 2; p.crit += 1
        needed = p.level * 100
        leveled = True
    await save_player(p)
    return leveled

async def add_item(uid: int, item_code: str, qty: int = 1):
    item = ITEM_CATALOG.get(item_code)
    if not item: return False
    row = await fetch_one(
        "SELECT id,qty FROM inventory_items WHERE user_id=? AND item_code=?", (uid, item_code)
    )
    if row:
        await execute("UPDATE inventory_items SET qty=qty+? WHERE id=?", (qty, row["id"]))
    else:
        await execute(
            "INSERT INTO inventory_items (user_id,item_code,item_name,item_type,rarity,qty,power,defense) VALUES (?,?,?,?,?,?,?,?)",
            (uid, item_code, item.name, item.item_type, item.rarity, qty, item.power, item.defense)
        )
    return True

# ══════════════════════════════════════════════════════════════════════════
# 10. 월드 렌더링 (미니맵 포함)
# ══════════════════════════════════════════════════════════════════════════
BIOME_TILES = {
    "평원": "🟫", "숲": "🌲", "사막": "🏜️", "설원": "❄️",
    "화산": "🌋", "바다": "🌊", "동굴": "🕳️",
}

def _get_biome(x: int, y: int) -> str:
    v = (x * 3 + y * 7) % 100
    if v < 40: return "평원"
    elif v < 60: return "숲"
    elif v < 70: return "사막"
    elif v < 78: return "설원"
    elif v < 84: return "화산"
    elif v < 90: return "바다"
    else: return "동굴"

def _get_tile(x: int, y: int, px: int, py: int) -> str:
    if x == px and y == py: return "😺"
    pos = (x, y)
    for (lx, ly), lm in LANDMARKS.items():
        if x == lx and y == ly:
            t = lm["type"]
            return {"town":"🏘️","dungeon":"🏰","colosseum":"⚔️","shop":"🏪","fishing":"🎣","halloween":"🎃","christmas":"🎄"}.get(t,"❓")
    for npc in NPCS.values():
        if x == npc["x"] and y == npc["y"]: return "💬"
    # 집 체크
    biome = _get_biome(x, y)
    if (x + y) % 23 == 0: return "🏠"
    return BIOME_TILES.get(biome, "🟫")

def render_map(p: PlayerRecord, view_dist: int = 4) -> str:
    # state가 dict가 아닐 경우 초기화
    if not isinstance(p.state, dict): p.state = {}
    # 레이드 중이면 레이드 화면 출력
    if p.state.get("in_raid"):
        return render_raid_screen(p)
    # 집에 있으면 집 화면 출력
    if p.state.get("in_house"):
        return render_house_screen(p)

    lines = ["```"]
    title_str = f"[{p.title}] " if p.title else ""
    lines.append(f"╔══ {title_str}{p.username} | Lv.{p.level} {p.job} ══╗")
    lines.append(f"║ 📍 위치: ({p.x},{p.y}) | {p.biome} ║")
    for row_y in range(p.y - view_dist, p.y + view_dist + 1):
        row = "║ "
        for col_x in range(p.x - view_dist, p.x + view_dist + 1):
            if 0 <= col_x < WORLD_W and 0 <= row_y < WORLD_H:
                row += _get_tile(col_x, row_y, p.x, p.y)
            else:
                row += "🌌"
        lines.append(row + " ║")
    lines.append(f"╠══ ❤️{p.hp}/{p.max_hp} 💙{p.mp}/{p.max_mp} ⚡{p.stamina}/{p.max_stamina} ══╣")
    lines.append(f"║ 💰{p.coins:,}  💎{p.gems}  ⚔️{p.attack}  🛡️{p.defense}  🎯{p.crit}% ║")
    lines.append("╚══════════════════════════════════════╝")
    lines.append("```")
    return "\n".join(lines)

def render_house_screen(p: PlayerRecord) -> str:
    lines = ["```", "╔════════════ 🏠 내 집 ════════════╗"]
    lines.append(f"║ 플레이어: {p.username}의 아늑한 보금자리  ║")
    lines.append("║                                      ║")
    lines.append("║      🛏️        📺        🪑      ║")
    lines.append("║     침대      TV      의자      ║")
    lines.append("║                                      ║")
    lines.append(f"╠══ ❤️{p.hp}/{p.max_hp} 💙{p.mp}/{p.max_mp} ⚡{p.stamina}/{p.max_stamina} ══╣")
    lines.append("╚══════════════════════════════════════╝")
    lines.append("```")
    return "\n".join(lines)

def render_raid_screen(p: PlayerRecord) -> str:
    lines = ["```", "╔════════════ 🐲 레이드 ════════════╗"]
    lines.append(f"║      🔥 전장의 한복판! 🔥          ║")
    lines.append("║                                      ║")
    lines.append("║          🐲 거대 보스 🐲            ║")
    lines.append("║          ⚔️⚔️⚔️⚔️⚔️⚔️⚔️⚔️            ║")
    lines.append("║                                      ║")
    lines.append(f"╠══ ❤️{p.hp}/{p.max_hp} 💙{p.mp}/{p.max_mp} ⚡{p.stamina}/{p.max_stamina} ══╣")
    lines.append("╚══════════════════════════════════════╝")
    lines.append("```")
    return "\n".join(lines)

def render_minimap(p: PlayerRecord) -> str:
    """전체 월드 축소맵 (20x20 격자)"""
    MINI_SIZE = 20
    step_x = WORLD_W // MINI_SIZE
    step_y = WORLD_H // MINI_SIZE
    lines = ["```", "╔══════ 🗺️ 월드 미니맵 ══════╗"]
    for my in range(MINI_SIZE):
        row = "║"
        for mx in range(MINI_SIZE):
            wx = mx * step_x + step_x // 2
            wy = my * step_y + step_y // 2
            # 플레이어 위치
            if abs(wx - p.x) <= step_x // 2 and abs(wy - p.y) <= step_y // 2:
                row += "😺"
                continue
            # 랜드마크
            found = False
            for (lx, ly), lm in LANDMARKS.items():
                if abs(wx - lx) <= step_x and abs(wy - ly) <= step_y:
                    t = lm["type"]
                    row += {"town":"🏘","dungeon":"🏰","colosseum":"⚔","shop":"🏪","fishing":"🎣","halloween":"🎃","christmas":"🎄"}.get(t,"❓")
                    found = True; break
            if not found:
                biome = _get_biome(wx, wy)
                row += {"평원":"🟩","숲":"🌲","사막":"🟨","설원":"⬜","화산":"🟥","바다":"🟦","동굴":"⬛"}.get(biome,"🟩")
        lines.append(row + "║")
    lines.append("╚══════════════════════════════╝")
    lines.append(f"  😺=나  🏘=마을  🏰=던전  ⚔=콜로세움")
    lines.append("```")
    return "\n".join(lines)

async def try_move(p: PlayerRecord, d: str) -> Tuple[bool, str]:
    dx, dy = {"W":(0,-1),"A":(-1,0),"S":(0,1),"D":(1,0)}.get(d, (0,0))
    nx, ny = max(0, min(WORLD_W-1, p.x+dx)), max(0, min(WORLD_H-1, p.y+dy))
    row = await fetch_one("SELECT tile_type FROM world_tiles WHERE x=? AND y=?", (nx, ny))
    if row and row["tile_type"] in ["wall","water"]:
        return False, f"🚫 이동 불가 ({row['tile_type']})"
    p.x, p.y, p.facing = nx, ny, d
    p.stamina = max(0, p.stamina - 1)
    p.biome = _get_biome(nx, ny)
    # 이동 퀘스트 진행
    state = p.state
    state["move_count"] = state.get("move_count", 0) + 1
    p.state = state
    await save_player(p)
    # 랜드마크 도착 메시지
    lm = LANDMARKS.get((nx, ny))
    if lm:
        return True, f"📍 {lm['name']} 도착!\n{lm['desc']}"
    return True, f"📍 ({nx},{ny}) {p.biome}"


# ══════════════════════════════════════════════════════════════════════════
# 11. 전투 시스템
# ══════════════════════════════════════════════════════════════════════════
def calc_damage(atk: int, def_: int, crit: int) -> Tuple[int, bool]:
    is_crit = random.randint(1, 100) <= crit
    dmg = max(1, atk - def_ + random.randint(-3, 3))
    if is_crit: dmg = int(dmg * 1.8)
    return dmg, is_crit

async def fight_monster(p: PlayerRecord, monster: dict) -> dict:
    mob = dict(monster)
    mob_hp = mob["hp"]
    log_lines = [f"⚔️ **{mob['name']}** 와(과) 전투 시작!"]
    rounds = 0
    while p.hp > 0 and mob_hp > 0 and rounds < 20:
        # 플레이어 공격
        dmg, crit = calc_damage(p.attack, mob["def"], p.crit)
        mob_hp -= dmg
        log_lines.append(f"{'💥 치명타! ' if crit else ''}내 공격: **{dmg}** 데미지 → 몬스터 HP: {max(0,mob_hp)}")
        if mob_hp <= 0: break
        # 몬스터 공격
        m_dmg, _ = calc_damage(mob["atk"], p.defense, 5)
        p.hp = max(0, p.hp - m_dmg)
        log_lines.append(f"몬스터 공격: **{m_dmg}** 데미지 → 내 HP: {p.hp}")
        rounds += 1

    if mob_hp <= 0:
        # 승리
        exp_gain = mob["exp"]
        coin_gain = mob["coins"] + random.randint(0, mob["coins"] // 2)
        leveled = await add_exp(p, exp_gain)
        p.coins += coin_gain
        p.state["kill_count"] = p.state.get("kill_count", 0) + 1
        await save_player(p)
        # 아이템 드롭
        drop_item = None
        if random.random() < mob.get("drop_rate", 0.3):
            drop_code = random.choice(list(ITEM_CATALOG.keys()))
            await add_item(p.user_id, drop_code)
            drop_item = ITEM_CATALOG[drop_code].name
        log_lines.append(f"\n🏆 **승리!** EXP +{exp_gain} | 💰 +{coin_gain}")
        if leveled: log_lines.append(f"🎉 **레벨 업! Lv.{p.level}**")
        if drop_item: log_lines.append(f"📦 드롭: {drop_item}")
        return {"win": True, "log": "\n".join(log_lines)}
    else:
        p.hp = max(1, p.hp)  # 사망 방지 (부활 포션 없으면 HP 1 유지)
        await save_player(p)
        log_lines.append(f"\n💀 **패배!** HP가 1로 유지됩니다.")
        return {"win": False, "log": "\n".join(log_lines)}

async def fight_pvp(p1: PlayerRecord, p2: PlayerRecord) -> dict:
    p1_hp, p2_hp = p1.hp, p2.hp
    logs = [f"⚔️ **{p1.username}** vs **{p2.username}** PVP 시작!"]
    for _ in range(30):
        d1, c1 = calc_damage(p1.attack, p2.defense, p1.crit)
        p2_hp -= d1
        logs.append(f"{'💥' if c1 else ''}**{p1.username}** → {d1} 데미지")
        if p2_hp <= 0: break
        d2, c2 = calc_damage(p2.attack, p1.defense, p2.crit)
        p1_hp -= d2
        logs.append(f"{'💥' if c2 else ''}**{p2.username}** → {d2} 데미지")
        if p1_hp <= 0: break
    winner = p1 if p2_hp <= 0 else p2
    loser = p2 if p2_hp <= 0 else p1
    prize = min(500, loser.coins // 10)
    winner.coins += prize; loser.coins = max(0, loser.coins - prize)
    winner.state["pvp_win"] = winner.state.get("pvp_win", 0) + 1
    await save_player(winner); await save_player(loser)
    logs.append(f"\n🏆 **{winner.username}** 승리! 💰 +{prize}")
    return {"winner": winner.user_id, "log": "\n".join(logs)}

# ══════════════════════════════════════════════════════════════════════════
# 12. 던전 시스템
# ══════════════════════════════════════════════════════════════════════════
async def dungeon_fight(p: PlayerRecord, floor: int) -> dict:
    level_range = min(floor * 2, len(MONSTERS) - 1)
    mob = MONSTERS[min(floor - 1, len(MONSTERS) - 1)]
    # 층수에 따라 강화
    scaled = dict(mob)
    scaled["hp"] = mob["hp"] + floor * 30
    scaled["atk"] = mob["atk"] + floor * 5
    scaled["def"] = mob["def"] + floor * 2
    scaled["exp"] = mob["exp"] + floor * 20
    scaled["coins"] = mob["coins"] + floor * 15
    return await fight_monster(p, scaled)

async def dungeon_boss_fight(p: PlayerRecord) -> dict:
    boss = random.choice(BOSSES)
    result = await fight_monster(p, {**boss, "def": boss["def"], "drop_rate": 0.9})
    if result["win"]:
        p.state["raid_count"] = p.state.get("raid_count", 0) + 1
        await save_player(p)
    return result

# ══════════════════════════════════════════════════════════════════════════
# 13. 레이드 시스템 (협동 보스전)
# ══════════════════════════════════════════════════════════════════════════
async def create_raid(boss_idx: int = 0) -> str:
    boss = BOSSES[boss_idx % len(BOSSES)]
    raid_id = uuid.uuid4().hex[:8]
    await execute(
        "INSERT INTO raid_sessions (raid_id,boss_name,boss_hp,boss_max_hp,participants_json) VALUES (?,?,?,?,?)",
        (raid_id, boss["name"], boss["hp"], boss["hp"], j({}))
    )
    return raid_id

async def join_raid(raid_id: str, p: PlayerRecord) -> Tuple[bool, str]:
    row = await fetch_one("SELECT * FROM raid_sessions WHERE raid_id=? AND state='active'", (raid_id,))
    if not row: return False, "레이드를 찾을 수 없습니다."
    participants = uj(row["participants_json"])
    if str(p.user_id) in participants:
        return False, "이미 참가 중입니다."
    participants[str(p.user_id)] = {"name": p.username, "dmg": 0}
    await execute("UPDATE raid_sessions SET participants_json=? WHERE raid_id=?", (j(participants), raid_id))
    return True, f"✅ 레이드 참가! 보스: {row['boss_name']} | HP: {row['boss_hp']:,}"

async def attack_raid(raid_id: str, p: PlayerRecord) -> Tuple[bool, str]:
    row = await fetch_one("SELECT * FROM raid_sessions WHERE raid_id=? AND state='active'", (raid_id,))
    if not row: return False, "레이드가 종료되었습니다."
    participants = uj(row["participants_json"])
    if str(p.user_id) not in participants:
        return False, "먼저 레이드에 참가하세요."
    dmg, crit = calc_damage(p.attack * 2, 20, p.crit)
    boss_hp = row["boss_hp"] - dmg
    participants[str(p.user_id)]["dmg"] += dmg
    msg = f"{'💥 치명타! ' if crit else ''}**{dmg}** 데미지! 보스 HP: {max(0, boss_hp):,}"
    if boss_hp <= 0:
        # 레이드 클리어
        await execute("UPDATE raid_sessions SET state='clear',boss_hp=0,participants_json=? WHERE raid_id=?",
                      (j(participants), raid_id))
        # 보상 지급
        boss_data = next((b for b in BOSSES if b["name"] == row["boss_name"]), BOSSES[0])
        total_dmg = sum(v["dmg"] for v in participants.values())
        rewards = []
        last_hitter = p.user_id
        for uid_str, data in participants.items():
            uid = int(uid_str)
            ratio = data["dmg"] / max(1, total_dmg)
            coin_reward = int(boss_data["coins"] * ratio)
            exp_reward = int(boss_data["exp"] * ratio)
            pp = await fetch_one("SELECT * FROM players WHERE user_id=?", (uid,))
            if pp:
                pr = PlayerRecord.from_row(pp)
                pr.coins += coin_reward
                if uid == p.user_id:
                    pr.state["raid_count"] = pr.state.get("raid_count", 0) + 1
                await add_exp(pr, exp_reward)
                rewards.append(f"  {data['name']}: 💰+{coin_reward:,} EXP+{exp_reward}")
        msg += f"\n🎉 **레이드 클리어!**\n" + "\n".join(rewards)
        return True, msg
    else:
        await execute("UPDATE raid_sessions SET boss_hp=?,participants_json=? WHERE raid_id=?",
                      (boss_hp, j(participants), raid_id))
        return True, msg

# ══════════════════════════════════════════════════════════════════════════
# 14. 강화 및 제작
# ══════════════════════════════════════════════════════════════════════════
async def enchant_item(uid: int, inv_id: int) -> Tuple[bool, str]:
    row = await fetch_one("SELECT * FROM inventory_items WHERE id=? AND user_id=?", (inv_id, uid))
    if not row: return False, "아이템을 찾을 수 없습니다."
    lv = row["enchant_level"]
    if lv >= 15: return False, "최대 강화 수치(+15)입니다."
    cost = (lv + 1) * 200
    p = await fetch_one("SELECT coins FROM players WHERE user_id=?", (uid,))
    if not p or p["coins"] < cost: return False, f"코인 부족 (필요: {cost})"
    await execute("UPDATE players SET coins=coins-? WHERE user_id=?", (cost, uid))
    rate = max(10, 100 - lv * 7)
    if random.randint(1, 100) <= rate:
        new_lv = lv + 1
        await execute("UPDATE inventory_items SET enchant_level=?,power=power+15,defense=defense+8 WHERE id=?",
                      (new_lv, inv_id))
        return True, f"✨ 강화 성공! (+{new_lv}) 💰 -{cost}"
    return False, f"❌ 강화 실패 (확률: {rate}%) 💰 -{cost}"

async def craft_item(p: PlayerRecord, item_code: str) -> Tuple[bool, str]:
    recipe = CRAFT_RECIPES.get(item_code)
    if not recipe: return False, "제작 레시피가 없습니다."
    if p.coins < recipe["코인"]: return False, f"코인 부족 (필요: {recipe['코인']:,})"
    for mat_code, qty in recipe["재료"].items():
        row = await fetch_one(
            "SELECT qty FROM inventory_items WHERE user_id=? AND item_code=?", (p.user_id, mat_code)
        )
        if not row or row["qty"] < qty:
            mat_name = ITEM_CATALOG.get(mat_code, Item(mat_code, mat_code, "", "")).name
            return False, f"재료 부족: {mat_name} (필요: {qty})"
    # 재료 차감
    for mat_code, qty in recipe["재료"].items():
        await execute(
            "UPDATE inventory_items SET qty=qty-? WHERE user_id=? AND item_code=?",
            (qty, p.user_id, mat_code)
        )
        await execute("DELETE FROM inventory_items WHERE user_id=? AND item_code=? AND qty<=0",
                      (p.user_id, mat_code))
    p.coins -= recipe["코인"]
    p.state["craft_count"] = p.state.get("craft_count", 0) + 1
    await save_player(p)
    await add_item(p.user_id, item_code)
    item = ITEM_CATALOG[item_code]
    return True, f"⚒️ **{item.name}** 제작 완료!"


# ══════════════════════════════════════════════════════════════════════════
# 15. 낚시 시스템 (타이밍 리액션)
# ══════════════════════════════════════════════════════════════════════════
_fishing_sessions: Dict[int, dict] = {}

async def start_fishing(uid: int) -> str:
    delay = random.uniform(3, 8)
    _fishing_sessions[uid] = {
        "state": "waiting",
        "started_at": time.time(),
        "delay": delay,
        "fish_at": time.time() + delay,
    }
    return f"🎣 낚싯대를 드리웠습니다... {delay:.1f}초 후 버튼이 나타납니다!"

async def catch_fish(uid: int) -> Tuple[bool, str]:
    session = _fishing_sessions.get(uid)
    if not session: return False, "낚시 중이 아닙니다."
    now = time.time()
    if now < session["fish_at"]: return False, "⏳ 아직 입질이 없습니다! 기다리세요."
    if now > session["fish_at"] + 3: # 3초 이내에 잡아야 함
        del _fishing_sessions[uid]
        return False, "🐟 놓쳤습니다! 물고기가 도망갔어요."
    del _fishing_sessions[uid]
    # 물고기 결정
    weights = [50, 25, 12, 7, 4, 2]
    fish = random.choices(FISH_TABLE, weights=weights)[0]
    coin_gain = random.randint(fish[1], fish[2])
    p = await fetch_one("SELECT * FROM players WHERE user_id=?", (uid,))
    if p:
        pr = PlayerRecord.from_row(p)
        pr.coins += coin_gain
        pr.state["fish_count"] = pr.state.get("fish_count", 0) + 1
        await save_player(pr)
        # 낚시 대회 기록
        week = datetime.now(timezone.utc).strftime("%Y-W%U")
        row = await fetch_one("SELECT id,score FROM fishing_contest WHERE user_id=? AND season_week=?", (uid, week))
        if row:
            await execute("UPDATE fishing_contest SET score=score+? WHERE id=?", (coin_gain, row["id"]))
        else:
            await execute("INSERT INTO fishing_contest (user_id,score,season_week) VALUES (?,?,?)", (uid, coin_gain, week))
    return True, f"🎣 **{fish[0]}** 낚음! 💰 +{coin_gain}"

# ══════════════════════════════════════════════════════════════════════════
# 16. 슬롯머신 & 주사위 도박
# ══════════════════════════════════════════════════════════════════════════
SLOT_SYMBOLS = ["🍒","🍋","🍊","🍇","⭐","💎","7️⃣"]
SLOT_WEIGHTS  = [30,  25,  20,  15,  6,   3,   1  ]

async def play_slots(p: PlayerRecord, bet: int) -> Tuple[bool, str]:
    cd = check_cooldown("slots", p.user_id, 30)
    if cd: return False, f"⏳ 슬롯머신 쿨다운: {cd:.0f}초"
    daily = p.state.get("slots_today", {})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if daily.get("date") == today and daily.get("count", 0) >= 10:
        return False, "오늘 슬롯머신 횟수(10회)를 모두 사용했습니다."
    if p.coins < bet: return False, "코인이 부족합니다."
    p.coins -= bet
    reels = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=3)
    display = " | ".join(reels)
    if reels[0] == reels[1] == reels[2]:
        mult = {"7️⃣":50,"💎":20,"⭐":10,"🍇":5,"🍊":3,"🍋":2,"🍒":1.5}.get(reels[0], 2)
        win = int(bet * mult)
        p.coins += win
        msg = f"🎰 {display}\n🎉 **잭팟!** 💰 +{win} (x{mult})"
    elif reels[0] == reels[1] or reels[1] == reels[2]:
        win = int(bet * 1.5)
        p.coins += win
        msg = f"🎰 {display}\n✨ 2개 일치! 💰 +{win}"
    else:
        msg = f"🎰 {display}\n😢 꽝! 💰 -{bet}"
    # 일일 횟수 업데이트
    if daily.get("date") != today:
        p.state["slots_today"] = {"date": today, "count": 1}
    else:
        p.state["slots_today"]["count"] = daily.get("count", 0) + 1
    await save_player(p)
    return True, msg

async def play_dice(p: PlayerRecord, bet: int, guess: int) -> Tuple[bool, str]:
    cd = check_cooldown("dice", p.user_id, 10)
    if cd: return False, f"⏳ 주사위 쿨다운: {cd:.0f}초"
    if p.coins < bet: return False, "코인이 부족합니다."
    if not 1 <= guess <= 6: return False, "1~6 사이 숫자를 선택하세요."
    p.coins -= bet
    result = random.randint(1, 6)
    if result == guess:
        win = bet * 5
        p.coins += win
        msg = f"🎲 결과: **{result}** | 예측: {guess} → 🎉 맞췄습니다! 💰 +{win}"
    else:
        msg = f"🎲 결과: **{result}** | 예측: {guess} → 😢 틀렸습니다. 💰 -{bet}"
    await save_player(p)
    return True, msg

# ══════════════════════════════════════════════════════════════════════════
# 17. 거래소 (경매장)
# ══════════════════════════════════════════════════════════════════════════
async def list_auction(seller_id: int, inv_id: int, min_bid: int) -> Tuple[bool, str]:
    row = await fetch_one("SELECT * FROM inventory_items WHERE id=? AND user_id=?", (inv_id, seller_id))
    if not row: return False, "아이템을 찾을 수 없습니다."
    end_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    item_data = dict(row)
    await execute(
        "INSERT INTO auction_house (seller_id,item_json,min_bid,current_bid,end_at) VALUES (?,?,?,?,?)",
        (seller_id, j(item_data), min_bid, min_bid, end_at)
    )
    await execute("DELETE FROM inventory_items WHERE id=?", (inv_id,))
    return True, f"📦 **{row['item_name']}** 거래소 등록 완료! 시작가: {min_bid:,}코인 (24시간)"

async def bid_auction(buyer: PlayerRecord, auction_id: int, bid: int) -> Tuple[bool, str]:
    row = await fetch_one("SELECT * FROM auction_house WHERE id=?", (auction_id,))
    if not row: return False, "매물을 찾을 수 없습니다."
    if datetime.fromisoformat(row["end_at"]) < datetime.now(timezone.utc):
        return False, "경매가 종료되었습니다."
    if bid <= row["current_bid"]: return False, f"현재 최고가({row['current_bid']:,})보다 높아야 합니다."
    if buyer.coins < bid: return False, "코인이 부족합니다."
    # 이전 최고 입찰자 환불
    if row["highest_bidder"]:
        await execute("UPDATE players SET coins=coins+? WHERE user_id=?",
                      (row["current_bid"], row["highest_bidder"]))
    buyer.coins -= bid
    await save_player(buyer)
    await execute("UPDATE auction_house SET current_bid=?,highest_bidder=? WHERE id=?",
                  (bid, buyer.user_id, auction_id))
    item_data = uj(row["item_json"])
    return True, f"🏷️ **{item_data.get('item_name','')}** 입찰 완료! {bid:,}코인"

async def set_auction_watch(uid: int, item_name_keyword: str) -> str:
    await execute(
        "INSERT OR REPLACE INTO rankings (user_id,category,score,season) VALUES (?,?,?,?)",
        (uid, f"watch_{item_name_keyword}", 1, "watch")
    )
    return f"🔔 '{item_name_keyword}' 아이템이 거래소에 등록되면 DM으로 알림을 보내드립니다."

# ══════════════════════════════════════════════════════════════════════════
# 18. 배틀패스 시스템
# ══════════════════════════════════════════════════════════════════════════
BP_REWARDS = {
    1:  {"free":"potion_hp_s","premium":"potion_hp_m"},
    5:  {"free":"potion_mp_s","premium":"w_0_5_2"},
    10: {"free":"scroll_teleport","premium":"w_0_10_3"},
    20: {"free":"mat_미스릴","premium":"a_0_15_3"},
    30: {"free":"fishing_rod","premium":"craft_fire_sword"},
    50: {"free":"w_0_20_2","premium":"craft_dragon_armor"},
}

async def add_bp_exp(uid: int, exp: int) -> Optional[str]:
    row = await fetch_one("SELECT * FROM battlepass WHERE user_id=?", (uid,))
    if not row:
        await execute("INSERT INTO battlepass (user_id,season,exp) VALUES (?,?,?)",
                      (uid, settings.season_number, exp))
        return None
    new_exp = row["exp"] + exp
    new_lv = row["level"]
    msg = None
    while new_exp >= 100:
        new_exp -= 100
        new_lv += 1
        reward = BP_REWARDS.get(new_lv)
        if reward:
            await add_item(uid, reward["free"])
            if row["premium"]: await add_item(uid, reward["premium"])
            msg = f"🎫 배틀패스 Lv.{new_lv} 달성! 보상 지급 완료."
    await execute("UPDATE battlepass SET level=?,exp=? WHERE user_id=?", (new_lv, new_exp, uid))
    return msg


# ══════════════════════════════════════════════════════════════════════════
# 19. 결혼 / 의형제 시스템
# ══════════════════════════════════════════════════════════════════════════
async def propose_marriage(p1: PlayerRecord, p2_id: int) -> Tuple[bool, str]:
    if p1.partner_id: return False, "이미 결혼한 상태입니다."
    row = await fetch_one("SELECT partner_id FROM players WHERE user_id=?", (p2_id,))
    if not row: return False, "상대방을 찾을 수 없습니다."
    if row["partner_id"]: return False, "상대방이 이미 결혼한 상태입니다."
    return True, "PENDING"  # View에서 수락 버튼 처리

async def confirm_marriage(p1_id: int, p2_id: int) -> str:
    now = datetime.now(timezone.utc).isoformat()
    await execute("UPDATE players SET partner_id=? WHERE user_id=?", (p2_id, p1_id))
    await execute("UPDATE players SET partner_id=? WHERE user_id=?", (p1_id, p2_id))
    await execute("INSERT OR REPLACE INTO marriages (user_id,partner_id,married_at) VALUES (?,?,?)",
                  (p1_id, p2_id, now))
    return "💍 결혼이 성사되었습니다! 축하합니다!"

async def propose_brotherhood(p1: PlayerRecord, p2_id: int) -> Tuple[bool, str]:
    if p1.brother_id: return False, "이미 의형제가 있습니다."
    return True, "PENDING"

async def confirm_brotherhood(p1_id: int, p2_id: int) -> str:
    await execute("UPDATE players SET brother_id=? WHERE user_id=?", (p2_id, p1_id))
    await execute("UPDATE players SET brother_id=? WHERE user_id=?", (p1_id, p2_id))
    return "🤝 의형제를 맺었습니다!"

# ══════════════════════════════════════════════════════════════════════════
# 20. 퀘스트 시스템
# ══════════════════════════════════════════════════════════════════════════
async def accept_quest(uid: int, quest_code: str) -> Tuple[bool, str]:
    if quest_code not in QUEST_DATA: return False, "존재하지 않는 퀘스트입니다."
    row = await fetch_one("SELECT id,completed FROM quests WHERE user_id=? AND quest_code=?", (uid, quest_code))
    if row:
        if row["completed"]: return False, "이미 완료한 퀘스트입니다."
        return False, "이미 진행 중인 퀘스트입니다."
    await execute("INSERT INTO quests (user_id,quest_code) VALUES (?,?)", (uid, quest_code))
    q = QUEST_DATA[quest_code]
    return True, f"📜 퀘스트 수락: **{q['name']}**\n{q['desc']}"

async def check_quest_progress(uid: int, quest_type: str, value: int = 1) -> Optional[str]:
    rows = await fetch_all("SELECT * FROM quests WHERE user_id=? AND completed=0", (uid,))
    msgs = []
    for row in rows:
        q = QUEST_DATA.get(row["quest_code"])
        if not q or q["type"] != quest_type: continue
        new_prog = row["progress"] + value
        if new_prog >= q["target"]:
            await execute("UPDATE quests SET progress=?,completed=1 WHERE id=?", (q["target"], row["id"]))
            # 보상 지급
            p = await fetch_one("SELECT * FROM players WHERE user_id=?", (uid,))
            if p:
                pr = PlayerRecord.from_row(p)
                pr.coins += q["reward_coins"]
                await add_exp(pr, q["reward_exp"])
                await add_item(uid, q["reward_item"])
                await save_player(pr)
            msgs.append(f"✅ 퀘스트 완료: **{q['name']}**! 💰+{q['reward_coins']} EXP+{q['reward_exp']}")
        else:
            await execute("UPDATE quests SET progress=? WHERE id=?", (new_prog, row["id"]))
    return "\n".join(msgs) if msgs else None

# ══════════════════════════════════════════════════════════════════════════
# 21. 업적 및 칭호 시스템
# ══════════════════════════════════════════════════════════════════════════
async def check_achievements(p: PlayerRecord) -> List[str]:
    earned = []
    state = p.state
    checks = {
        "몬스터 헌터": state.get("kill_count", 0) >= 100,
        "탐험가": state.get("move_count", 0) >= 1000,
        "낚시왕": state.get("fish_count", 0) >= 100,
        "레이드 영웅": state.get("raid_count", 0) >= 10,
        "PVP 챔피언": state.get("pvp_win", 0) >= 50,
        "장인": state.get("craft_count", 0) >= 20,
        "부자": p.coins >= 1_000_000,
    }
    for title, cond in checks.items():
        if cond and title not in p.achievements:
            p.achievements.append(title)
            earned.append(title)
    if earned:
        await save_player(p)
    return earned

# ══════════════════════════════════════════════════════════════════════════
# 22. 집 상호작용
# ══════════════════════════════════════════════════════════════════════════
async def enter_house(p: PlayerRecord) -> Tuple[bool, str]:
    # 현재 위치에 집이 있는지 확인
    is_house_tile = (p.x + p.y) % 23 == 0
    row = await fetch_one("SELECT * FROM houses WHERE x=? AND y=?", (p.x, p.y))
    if not is_house_tile and not row:
        return False, "이 위치에 집이 없습니다."
    if row:
        if row["owner_id"] == p.user_id:
            furniture = uj(row["furniture_json"])
            return True, f"🏠 **내 집에 입장했습니다!**\n가구: {', '.join(furniture) if furniture else '없음'}\n(HP/MP 완전 회복)"
        else:
            owner = await fetch_one("SELECT username FROM players WHERE user_id=?", (row["owner_id"],))
            name = owner["username"] if owner else "알 수 없음"
            return True, f"🏠 **{name}의 집에 방문했습니다!**"
    # 집 없으면 구매 옵션
    return True, f"🏠 빈 집이 있습니다! 구매하시겠습니까? (💰 5,000코인)"

async def buy_house(p: PlayerRecord) -> Tuple[bool, str]:
    if p.coins < 5000: return False, "코인이 부족합니다. (필요: 5,000)"
    row = await fetch_one("SELECT * FROM houses WHERE x=? AND y=?", (p.x, p.y))
    if row: return False, "이미 누군가의 집입니다."
    p.coins -= 5000
    await execute("INSERT INTO houses (x,y,owner_id) VALUES (?,?,?)", (p.x, p.y, p.user_id))
    await save_player(p)
    return True, f"🏠 집을 구매했습니다! ({p.x},{p.y})"

async def rest_at_home(p: PlayerRecord) -> Tuple[bool, str]:
    row = await fetch_one("SELECT * FROM houses WHERE owner_id=?", (p.user_id,))
    if not row: return False, "소유한 집이 없습니다."
    p.hp = p.max_hp; p.mp = p.max_mp; p.stamina = p.max_stamina
    await save_player(p)
    return True, "🏠 집에서 휴식! HP/MP/스태미나 완전 회복!"


# ══════════════════════════════════════════════════════════════════════════
# 23. UI Views (버튼 인터페이스)
# ══════════════════════════════════════════════════════════════════════════

class RPGMainView(discord.ui.View):
    """메인 RPG 화면 버튼"""
    def __init__(self, cog, uid: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.uid = uid
        self.refresh_task = None

    async def start_refresh(self, interaction: discord.Interaction):
        row = await fetch_one("SELECT auto_refresh FROM player_settings WHERE user_id=?", (self.uid,))
        if row and row["auto_refresh"]:
            if self.refresh_task is None:
                self.refresh_task = self.bot_refresh.start(interaction)

    @tasks.loop(seconds=5)
    async def bot_refresh(self, interaction: discord.Interaction):
        try:
            p = await ensure_player(self.uid, interaction.user.display_name)
            content = render_map(p)
            await interaction.edit_original_response(content=content, view=self)
        except:
            self.bot_refresh.stop()

    def stop_refresh(self):
        if self.refresh_task:
            self.bot_refresh.stop()

    async def _check(self, i: discord.Interaction) -> bool:
        if i.user.id != self.uid:
            await i.response.send_message("자신의 캐릭터만 조작 가능합니다!", ephemeral=True)
            return False
        return True

    # ── 이동 버튼 ──
    async def _do_move(self, i: discord.Interaction, direction: str):
        """이동 처리 후 맵 메시지 업데이트"""
        await i.response.defer()
        p = await ensure_player(i.user.id, i.user.display_name)
        if p.stamina <= 0:
            await i.followup.send("⚡ 스태미나가 부족합니다! 잠시 기다리세요.", ephemeral=True)
            return
        ok, move_msg = await try_move(p, direction)
        new_map = render_map(p)
        # 랜드마크 도착 메시지가 있으면 별도 ephemeral 메시지로 전송
        if ok and move_msg and "도착" in move_msg:
            await i.followup.send(move_msg, ephemeral=True)
        elif not ok:
            await i.followup.send(move_msg, ephemeral=True)
        # 원본 메시지(맵)를 새 좌표로 업데이트
        try:
            await i.edit_original_response(content=new_map)
        except Exception:
            pass

    @discord.ui.button(label="↑", style=discord.ButtonStyle.primary, row=0, custom_id="move_W")
    async def move_up(self, i, b):
        if not await self._check(i): return
        await self._do_move(i, "W")

    @discord.ui.button(label="←", style=discord.ButtonStyle.primary, row=1, custom_id="move_A")
    async def move_left(self, i, b):
        if not await self._check(i): return
        await self._do_move(i, "A")

    @discord.ui.button(label="↓", style=discord.ButtonStyle.primary, row=1, custom_id="move_S")
    async def move_down(self, i, b):
        if not await self._check(i): return
        await self._do_move(i, "S")

    @discord.ui.button(label="→", style=discord.ButtonStyle.primary, row=1, custom_id="move_D")
    async def move_right(self, i, b):
        if not await self._check(i): return
        await self._do_move(i, "D")

    @discord.ui.button(label="🗺️ 미니맵", style=discord.ButtonStyle.secondary, row=0, custom_id="minimap")
    async def minimap(self, i, b):
        if not await self._check(i): return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        await i.followup.send(render_minimap(p), ephemeral=True)

    @discord.ui.button(label="🏠 상호작용", style=discord.ButtonStyle.secondary, row=0, custom_id="interact")
    async def interact(self, i, b):
        if not await self._check(i): return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        # NPC 체크
        for npc_key, npc in NPCS.items():
            if abs(p.x - npc["x"]) <= 1 and abs(p.y - npc["y"]) <= 1:
                view = NPCView(self.cog, p.user_id, npc_key, npc)
                await i.followup.send(f"💬 **{npc['name']}**: {npc['dialogue']}", view=view, ephemeral=True)
                return
        # 집 체크
        ok, msg = await enter_house(p)
        if ok:
            if "입장" in msg:
                p.state["in_house"] = True
                await save_player(p)
                await i.edit_original_response(content=render_map(p))
            view = HouseView(self.cog, p.user_id)
            await i.followup.send(msg, view=view, ephemeral=True)
        else:
            await i.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="⚔️ 전투", style=discord.ButtonStyle.danger, row=2, custom_id="battle")
    async def battle(self, i, b):
        if not await self._check(i): return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        if p.hp <= 0:
            await i.followup.send("HP가 없습니다! 먼저 회복하세요.", ephemeral=True); return
        view = BattleSelectView(self.cog, p.user_id)
        await i.followup.send("⚔️ 전투 유형을 선택하세요:", view=view, ephemeral=True)

    @discord.ui.button(label="🎒 인벤토리", style=discord.ButtonStyle.success, row=2, custom_id="inventory")
    async def inventory(self, i, b):
        if not await self._check(i): return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        rows = await fetch_all("SELECT * FROM inventory_items WHERE user_id=? LIMIT 20", (p.user_id,))
        if not rows:
            await i.followup.send("인벤토리가 비어있습니다.", ephemeral=True); return
        lines = ["**🎒 인벤토리**"]
        for r in rows:
            ench = f" (+{r['enchant_level']})" if r["enchant_level"] > 0 else ""
            lines.append(f"`ID:{r['id']}` {r['item_name']}{ench} x{r['qty']}")
        view = InventoryActionView(self.cog, p.user_id)
        await i.followup.send("\n".join(lines), view=view, ephemeral=True)

    @discord.ui.button(label="📊 스탯", style=discord.ButtonStyle.success, row=2, custom_id="stat")
    async def stat(self, i, b):
        if not await self._check(i): return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        title_str = f"[{p.title}] " if p.title else ""
        txt = (
            f"**{title_str}{p.username}의 스탯**\n"
            f"직업: {p.job} | Lv.{p.level} | EXP: {p.exp}/{p.level*100}\n"
            f"❤️ HP: {p.hp}/{p.max_hp} | 💙 MP: {p.mp}/{p.max_mp}\n"
            f"⚔️ 공격: {p.attack} | 🛡️ 방어: {p.defense} | 🎯 치명타: {p.crit}%\n"
            f"💰 코인: {p.coins:,} | 💎 젬: {p.gems}\n"
            f"🏆 업적: {', '.join(p.achievements) if p.achievements else '없음'}"
        )
        view = StatMenuView(self.cog, p.user_id)
        await i.followup.send(txt, view=view, ephemeral=True)

    @discord.ui.button(label="🎮 미니게임", style=discord.ButtonStyle.secondary, row=3, custom_id="minigame")
    async def minigame(self, i, b):
        if not await self._check(i): return
        await i.response.defer(ephemeral=True)
        view = MinigameView(self.cog, i.user.id)
        await i.followup.send("🎮 **미니게임**을 선택하세요:", view=view, ephemeral=True)

    @discord.ui.button(label="🏪 거래소", style=discord.ButtonStyle.secondary, row=3, custom_id="auction")
    async def auction(self, i, b):
        if not await self._check(i): return
        await i.response.defer(ephemeral=True)
        view = AuctionView(self.cog, i.user.id)
        await i.followup.send("🏪 **거래소**", view=view, ephemeral=True)

    @discord.ui.button(label="❓ 도움말", style=discord.ButtonStyle.secondary, row=3, custom_id="help")
    async def help_btn(self, i, b):
        await i.response.defer(ephemeral=True)
        view = HelpView()
        await i.followup.send("❓ **도움말** - 카테고리를 선택하세요:", view=view, ephemeral=True)

    @discord.ui.button(label="⚙️ 설정", style=discord.ButtonStyle.secondary, row=4, custom_id="settings")
    async def settings(self, i, b):
        if not await self._check(i): return
        view = SettingsView(self.uid)
        await i.response.send_message("⚙️ **개인 설정**", view=view, ephemeral=True)

    @discord.ui.button(label="🐲 레이드", style=discord.ButtonStyle.danger, row=4, custom_id="raid_menu")
    async def raid_menu(self, i, b):
        if not await self._check(i): return
        await i.response.defer(ephemeral=True)
        raid_id = await create_raid(random.randint(0, len(BOSSES)-1))
        boss = BOSSES[0]
        view = RaidView(self.cog, i.user.id, raid_id)
        await i.followup.send(
            f"🐲 **레이드 보스 등장!**\n보스: {boss['name']}\nHP: {boss['hp']:,}\n"
            f"레이드 ID: `{raid_id}`\n아래 버튼으로 참가 및 공격하세요!",
            view=view, ephemeral=True
        )

    @discord.ui.button(label="🤝 소셜", style=discord.ButtonStyle.success, row=4, custom_id="social_menu")
    async def social_menu(self, i, b):
        if not await self._check(i): return
        view = SocialMenuView(self.cog, self.uid)
        await i.response.send_message("🤝 **소셜 메뉴**", view=view, ephemeral=True)

class SocialMenuView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid

    @discord.ui.button(label="💍 결혼 제안", style=discord.ButtonStyle.primary)
    async def marry(self, i, b):
        await i.response.send_message("결혼하고 싶은 유저를 멘션하여 `/결혼제안 @유저` 명령어를 사용해주세요.", ephemeral=True)

    @discord.ui.button(label="🤝 의형제 제안", style=discord.ButtonStyle.primary)
    async def brotherhood(self, i, b):
        await i.response.send_message("의형제를 맺고 싶은 유저를 멘션하여 `/의형제 @유저` 명령어를 사용해주세요.", ephemeral=True)

    @discord.ui.button(label="👋 인사", style=discord.ButtonStyle.secondary)
    async def wave(self, i, b):
        p = await ensure_player(self.uid, i.user.display_name)
        await i.response.send_message(f"👋 **{p.username}**이(가) 인사를 합니다!")

    @discord.ui.button(label="💃 춤", style=discord.ButtonStyle.secondary)
    async def dance(self, i, b):
        p = await ensure_player(self.uid, i.user.display_name)
        await i.response.send_message(f"💃 **{p.username}**이(가) 신나게 춤을 춥니다!")

    @discord.ui.button(label="🏆 랭킹", style=discord.ButtonStyle.secondary)
    async def ranking(self, i, b):
        await i.response.defer(ephemeral=True)
        rows = await fetch_all("SELECT username,level,coins FROM players ORDER BY level DESC, coins DESC LIMIT 10")
        lines = ["**🏆 글로벌 랭킹 (레벨 기준)**"]
        for idx, r in enumerate(rows, 1):
            lines.append(f"{idx}. **{r['username']}** | Lv.{r['level']} | 💰{r['coins']:,}")
        await i.followup.send("\n".join(lines), ephemeral=True)

    @discord.ui.button(label="🎟️ 초대", style=discord.ButtonStyle.secondary)
    async def invite(self, i, b):
        await i.response.defer(ephemeral=True)
        p = await ensure_player(self.uid, i.user.display_name)
        if not p.invite_code:
            p.invite_code = uuid.uuid4().hex[:8].upper()
            await save_player(p)
        invited_count = await fetch_one("SELECT COUNT(*) as cnt FROM players WHERE invited_by=?", (p.user_id,))
        cnt = invited_count["cnt"] if invited_count else 0
        await i.followup.send(
            f"**🎟️ 초대 코드: `{p.invite_code}`**\n"
            f"초대한 친구 수: {cnt}명\n"
            f"초대 보상: 친구 1명당 💰500코인 + 💎5젬\n"
            f"친구가 `/초대등록 {p.invite_code}`를 입력하면 보상이 지급됩니다!",
            ephemeral=True
        )

class SettingsView(discord.ui.View):
    def __init__(self, uid):
        super().__init__(timeout=60)
        self.uid = uid

    @discord.ui.button(label="🔄 자동 새로고침 ON/OFF", style=discord.ButtonStyle.primary)
    async def toggle_refresh(self, i, b):
        row = await fetch_one("SELECT auto_refresh FROM player_settings WHERE user_id=?", (self.uid,))
        current = row["auto_refresh"] if row else 0
        new_val = 1 if current == 0 else 0
        await execute("INSERT OR REPLACE INTO player_settings (user_id, auto_refresh) VALUES (?,?)", (self.uid, new_val))
        status = "ON" if new_val else "OFF"
        await i.response.send_message(f"✅ 자동 새로고침이 **{status}** 되었습니다. (게임 재시작 시 적용)", ephemeral=True)


class BattleSelectView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid

    @discord.ui.button(label="🐺 일반 사냥", style=discord.ButtonStyle.danger)
    async def hunt(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        mob = random.choice(MONSTERS)
        result = await fight_monster(p, mob)
        q_msg = await check_quest_progress(p.user_id, "kill") or ""
        await add_bp_exp(p.user_id, 5)
        new_ach = await check_achievements(p)
        extra = ""
        if new_ach: extra = f"\n🏆 새 업적: {', '.join(new_ach)}"
        await i.followup.send(result["log"] + q_msg + extra, ephemeral=True)

    @discord.ui.button(label="🏰 던전", style=discord.ButtonStyle.danger)
    async def dungeon(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        floor = p.state.get("dungeon_floor", 1)
        result = await dungeon_fight(p, floor)
        if result["win"]:
            if floor >= 5:
                boss_result = await dungeon_boss_fight(p)
                p.state["dungeon_floor"] = 1
                await save_player(p)
                await i.followup.send(result["log"] + "\n\n" + boss_result["log"], ephemeral=True)
            else:
                p.state["dungeon_floor"] = floor + 1
                await save_player(p)
                await i.followup.send(result["log"] + f"\n\n🏰 다음 층: **{floor+1}층**", ephemeral=True)
        else:
            p.state["dungeon_floor"] = 1
            await save_player(p)
            await i.followup.send(result["log"], ephemeral=True)

    @discord.ui.button(label="⚔️ 스킬 사용", style=discord.ButtonStyle.primary)
    async def skill(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        skills = SKILL_TREE.get(p.job, {})
        if not skills:
            await i.followup.send("현재 직업에 스킬이 없습니다.", ephemeral=True); return
        view = SkillView(self.cog, p.user_id, skills)
        await i.followup.send("⚡ **스킬 선택**:", view=view, ephemeral=True)


class SkillView(discord.ui.View):
    def __init__(self, cog, uid, skills: dict):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid; self.skills = skills
        for code, sk in list(skills.items())[:5]:
            btn = discord.ui.Button(label=sk["name"][:20], style=discord.ButtonStyle.primary, custom_id=f"sk_{code}")
            btn.callback = self._make_cb(code, sk)
            self.add_item(btn)

    def _make_cb(self, code, sk):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid: return
            await i.response.defer(ephemeral=True)
            p = await ensure_player(i.user.id, i.user.display_name)
            if p.mp < sk.get("mp", 0):
                await i.followup.send(f"MP 부족! (필요: {sk['mp']})", ephemeral=True); return
            p.mp -= sk.get("mp", 0)
            mob = random.choice(MONSTERS)
            mob_hp = mob["hp"]
            mult = sk.get("mult", 1.0)
            dmg = int(max(1, p.attack * mult - mob["def"]))
            mob_hp -= dmg
            heal = sk.get("heal", 0)
            if heal: p.hp = min(p.max_hp, p.hp + heal)
            result_msg = f"⚡ **{sk['name']}** 발동!\n{mob['name']}에게 **{dmg}** 데미지!"
            if heal: result_msg += f"\n💚 HP +{heal}"
            if mob_hp <= 0:
                p.coins += mob["coins"]
                await add_exp(p, mob["exp"])
                result_msg += f"\n🏆 처치! 💰+{mob['coins']}"
            await save_player(p)
            await i.followup.send(result_msg, ephemeral=True)
        return cb


class InventoryActionView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid

    @discord.ui.button(label="💊 포션 사용", style=discord.ButtonStyle.success)
    async def use_potion(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        row = await fetch_one(
            "SELECT * FROM inventory_items WHERE user_id=? AND item_type='소비' ORDER BY id LIMIT 1", (p.user_id,)
        )
        if not row:
            await i.followup.send("사용 가능한 소비 아이템이 없습니다.", ephemeral=True); return
        meta = uj(row["meta_json"])
        item = ITEM_CATALOG.get(row["item_code"])
        if item and item.meta.get("heal"):
            p.hp = min(p.max_hp, p.hp + item.meta["heal"])
        elif item and item.meta.get("mp_restore"):
            p.mp = min(p.max_mp, p.mp + item.meta["mp_restore"])
        elif item and item.meta.get("teleport") == "town":
            p.x, p.y = 100, 100
        await save_player(p)
        if row["qty"] <= 1:
            await execute("DELETE FROM inventory_items WHERE id=?", (row["id"],))
        else:
            await execute("UPDATE inventory_items SET qty=qty-1 WHERE id=?", (row["id"],))
        await i.followup.send(f"✅ {row['item_name']} 사용!", ephemeral=True)

    @discord.ui.button(label="💎 강화", style=discord.ButtonStyle.primary)
    async def enchant(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        rows = await fetch_all(
            "SELECT * FROM inventory_items WHERE user_id=? AND item_type IN ('무기','방어구') LIMIT 5", (i.user.id,)
        )
        if not rows:
            await i.followup.send("강화할 장비가 없습니다.", ephemeral=True); return
        view = EnchantSelectView(self.cog, i.user.id, rows)
        await i.followup.send("💎 강화할 아이템을 선택하세요:", view=view, ephemeral=True)

    @discord.ui.button(label="🏪 거래소 등록", style=discord.ButtonStyle.secondary)
    async def list_item(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        rows = await fetch_all("SELECT * FROM inventory_items WHERE user_id=? LIMIT 5", (i.user.id,))
        if not rows:
            await i.followup.send("인벤토리가 비어있습니다.", ephemeral=True); return
        view = AuctionListView(self.cog, i.user.id, rows)
        await i.followup.send("🏪 거래소에 등록할 아이템을 선택하세요:", view=view, ephemeral=True)


class EnchantSelectView(discord.ui.View):
    def __init__(self, cog, uid, rows):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid
        for row in rows[:5]:
            ench = f"+{row['enchant_level']}" if row["enchant_level"] > 0 else ""
            btn = discord.ui.Button(
                label=f"{row['item_name'][:15]}{ench}",
                style=discord.ButtonStyle.primary,
                custom_id=f"ench_{row['id']}"
            )
            btn.callback = self._make_cb(row["id"])
            self.add_item(btn)

    def _make_cb(self, inv_id):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid: return
            await i.response.defer(ephemeral=True)
            ok, msg = await enchant_item(i.user.id, inv_id)
            await i.followup.send(msg, ephemeral=True)
        return cb


class AuctionListView(discord.ui.View):
    def __init__(self, cog, uid, rows):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid
        for row in rows[:5]:
            btn = discord.ui.Button(
                label=row["item_name"][:20],
                style=discord.ButtonStyle.secondary,
                custom_id=f"alist_{row['id']}"
            )
            btn.callback = self._make_cb(row["id"])
            self.add_item(btn)

    def _make_cb(self, inv_id):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid: return
            await i.response.send_modal(AuctionPriceModal(self.uid, inv_id))
        return cb


class AuctionPriceModal(discord.ui.Modal, title="거래소 등록 가격 설정"):
    price = discord.ui.TextInput(label="시작 가격 (코인)", placeholder="예: 1000", min_length=1, max_length=10)

    def __init__(self, uid, inv_id):
        super().__init__()
        self.uid = uid; self.inv_id = inv_id

    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        try:
            min_bid = int(self.price.value)
        except ValueError:
            await i.followup.send("올바른 숫자를 입력하세요.", ephemeral=True); return
        ok, msg = await list_auction(self.uid, self.inv_id, min_bid)
        await i.followup.send(msg, ephemeral=True)


class StatMenuView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid

    @discord.ui.button(label="🎭 직업 변경", style=discord.ButtonStyle.primary)
    async def change_job(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        if p.level < 10:
            await i.followup.send("레벨 10 이상만 전직 가능합니다.", ephemeral=True); return
        view = JobSelectView(self.cog, p.user_id)
        await i.followup.send("🎭 직업을 선택하세요:", view=view, ephemeral=True)

    @discord.ui.button(label="🏷️ 칭호 변경", style=discord.ButtonStyle.secondary)
    async def change_title(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        if not p.achievements:
            await i.followup.send("획득한 칭호가 없습니다.", ephemeral=True); return
        view = TitleSelectView(self.cog, p.user_id, p.achievements)
        await i.followup.send("🏷️ 칭호를 선택하세요:", view=view, ephemeral=True)

    @discord.ui.button(label="🎨 외형 변경", style=discord.ButtonStyle.secondary)
    async def appearance(self, i, b):
        if i.user.id != self.uid: return
        await i.response.send_modal(AppearanceModal(self.uid))

    @discord.ui.button(label="📜 퀘스트", style=discord.ButtonStyle.success)
    async def quests(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        rows = await fetch_all("SELECT * FROM quests WHERE user_id=? AND completed=0", (i.user.id,))
        if not rows:
            await i.followup.send("진행 중인 퀘스트가 없습니다.", ephemeral=True); return
        lines = ["**📜 진행 중인 퀘스트**"]
        for r in rows:
            q = QUEST_DATA.get(r["quest_code"], {})
            lines.append(f"• **{q.get('name',r['quest_code'])}**: {r['progress']}/{q.get('target',1)}")
        await i.followup.send("\n".join(lines), ephemeral=True)

    @discord.ui.button(label="🎫 배틀패스", style=discord.ButtonStyle.success)
    async def battlepass(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        row = await fetch_one("SELECT * FROM battlepass WHERE user_id=?", (i.user.id,))
        if not row:
            await i.followup.send("배틀패스 정보가 없습니다. 활동을 시작하면 자동 생성됩니다.", ephemeral=True); return
        premium = "✅ 프리미엄" if row["premium"] else "❌ 무료"
        lines = [f"**🎫 시즌 {row['season']} 배틀패스**",
                 f"등급: {premium} | Lv.{row['level']} | EXP: {row['exp']}/100"]
        next_rewards = [(lv, r) for lv, r in BP_REWARDS.items() if lv > row["level"]][:3]
        if next_rewards:
            lines.append("**다음 보상:**")
            for lv, r in next_rewards:
                free_item = ITEM_CATALOG.get(r["free"])
                lines.append(f"  Lv.{lv}: {free_item.name if free_item else r['free']}")
        await i.followup.send("\n".join(lines), ephemeral=True)


class JobSelectView(discord.ui.View):
    JOBS = ["전사","궁수","마법사","성직자","도적"]
    def __init__(self, cog, uid):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid
        for job in self.JOBS:
            btn = discord.ui.Button(label=job, style=discord.ButtonStyle.primary, custom_id=f"job_{job}")
            btn.callback = self._make_cb(job)
            self.add_item(btn)

    def _make_cb(self, job):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid: return
            await i.response.defer(ephemeral=True)
            p = await ensure_player(i.user.id, i.user.display_name)
            p.job = job
            await save_player(p)
            await i.followup.send(f"🎉 **{job}**(으)로 전직 완료!", ephemeral=True)
        return cb


class TitleSelectView(discord.ui.View):
    def __init__(self, cog, uid, titles: list):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid
        for title in titles[:5]:
            btn = discord.ui.Button(label=title, style=discord.ButtonStyle.secondary, custom_id=f"title_{title}")
            btn.callback = self._make_cb(title)
            self.add_item(btn)

    def _make_cb(self, title):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid: return
            await i.response.defer(ephemeral=True)
            p = await ensure_player(i.user.id, i.user.display_name)
            p.title = title
            await save_player(p)
            await i.followup.send(f"🏷️ 칭호를 **[{title}]**으로 변경했습니다!", ephemeral=True)
        return cb


class AppearanceModal(discord.ui.Modal, title="외형 커스터마이징"):
    color = discord.ui.TextInput(label="캐릭터 색상", placeholder="예: 빨강, 파랑, 초록", max_length=20)
    accessory = discord.ui.TextInput(label="악세서리", placeholder="예: 왕관, 안경, 날개", max_length=20, required=False)

    def __init__(self, uid):
        super().__init__()
        self.uid = uid

    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        p.appearance = {"color": self.color.value, "accessory": self.accessory.value or "없음"}
        await save_player(p)
        await i.followup.send(f"🎨 외형 변경 완료!\n색상: {self.color.value} | 악세서리: {self.accessory.value or '없음'}", ephemeral=True)


class NPCView(discord.ui.View):
    def __init__(self, cog, uid, npc_key, npc):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid; self.npc_key = npc_key; self.npc = npc
        services = npc.get("services", [])
        quests = npc.get("quests", [])
        shop_items = npc.get("shop_items", [])
        if quests:
            btn = discord.ui.Button(label="📜 퀘스트 목록", style=discord.ButtonStyle.primary)
            btn.callback = self.show_quests
            self.add_item(btn)
        if "enchant" in services:
            btn = discord.ui.Button(label="💎 강화", style=discord.ButtonStyle.primary)
            btn.callback = self.do_enchant
            self.add_item(btn)
        if "craft" in services:
            btn = discord.ui.Button(label="⚒️ 제작", style=discord.ButtonStyle.success)
            btn.callback = self.show_craft
            self.add_item(btn)
        if "guild" in services:
            btn = discord.ui.Button(label="🏰 길드", style=discord.ButtonStyle.secondary)
            btn.callback = self.show_guild
            self.add_item(btn)
        if "pvp" in services:
            btn = discord.ui.Button(label="⚔️ PVP", style=discord.ButtonStyle.danger)
            btn.callback = self.start_pvp
            self.add_item(btn)
        if shop_items:
            btn = discord.ui.Button(label="🛒 상점", style=discord.ButtonStyle.success)
            btn.callback = self.show_shop
            self.add_item(btn)

    async def show_quests(self, i: discord.Interaction):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        quests = self.npc.get("quests", [])
        view = QuestListView(self.cog, i.user.id, quests)
        lines = ["**📜 퀘스트 목록**"]
        for qc in quests:
            q = QUEST_DATA.get(qc, {})
            lines.append(f"• **{q.get('name', qc)}**: {q.get('desc', '')}")
        await i.followup.send("\n".join(lines), view=view, ephemeral=True)

    async def do_enchant(self, i: discord.Interaction):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        rows = await fetch_all(
            "SELECT * FROM inventory_items WHERE user_id=? AND item_type IN ('무기','방어구') LIMIT 5", (i.user.id,)
        )
        if not rows:
            await i.followup.send("강화할 장비가 없습니다.", ephemeral=True); return
        view = EnchantSelectView(self.cog, i.user.id, rows)
        await i.followup.send("💎 강화할 아이템을 선택하세요:", view=view, ephemeral=True)

    async def show_craft(self, i: discord.Interaction):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        lines = ["**⚒️ 제작 목록**"]
        for code, recipe in CRAFT_RECIPES.items():
            item = ITEM_CATALOG.get(code)
            if not item: continue
            mats = ", ".join(f"{ITEM_CATALOG.get(k, Item(k,k,'','',0,0,{})).name} x{v}" for k, v in recipe["재료"].items())
            lines.append(f"• **{item.name}**: {mats} + 💰{recipe['코인']:,}")
        view = CraftView(self.cog, i.user.id)
        await i.followup.send("\n".join(lines), view=view, ephemeral=True)

    async def show_guild(self, i: discord.Interaction):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        view = GuildView(self.cog, i.user.id)
        await i.followup.send("🏰 **길드 메뉴**", view=view, ephemeral=True)

    async def start_pvp(self, i: discord.Interaction):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        lm = LANDMARKS.get((p.x, p.y))
        if not lm or lm["type"] != "colosseum":
            await i.followup.send("⚔️ 콜로세움에서만 PVP가 가능합니다! (150,150)", ephemeral=True); return
        await i.followup.send("⚔️ PVP 기능: /pvp_challenge @상대방 으로 도전하세요!", ephemeral=True)

    async def show_shop(self, i: discord.Interaction):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        shop_items = self.npc.get("shop_items", [])
        view = ShopView(self.cog, i.user.id, shop_items)
        lines = ["**🛒 상점**"]
        for code in shop_items:
            item = ITEM_CATALOG.get(code)
            price = SHOP_ITEMS.get(code, {}).get("price", 0)
            if item: lines.append(f"• {item.name}: 💰 {price:,}")
        await i.followup.send("\n".join(lines), view=view, ephemeral=True)


class QuestListView(discord.ui.View):
    def __init__(self, cog, uid, quest_codes):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid
        for qc in quest_codes[:5]:
            q = QUEST_DATA.get(qc, {})
            btn = discord.ui.Button(label=q.get("name", qc)[:20], style=discord.ButtonStyle.primary, custom_id=f"quest_{qc}")
            btn.callback = self._make_cb(qc)
            self.add_item(btn)

    def _make_cb(self, qc):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid: return
            await i.response.defer(ephemeral=True)
            ok, msg = await accept_quest(i.user.id, qc)
            await i.followup.send(msg, ephemeral=True)
        return cb


class CraftView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid
        for code in list(CRAFT_RECIPES.keys())[:5]:
            item = ITEM_CATALOG.get(code)
            if not item: continue
            btn = discord.ui.Button(label=item.name[:20], style=discord.ButtonStyle.success, custom_id=f"craft_{code}")
            btn.callback = self._make_cb(code)
            self.add_item(btn)

    def _make_cb(self, code):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid: return
            await i.response.defer(ephemeral=True)
            p = await ensure_player(i.user.id, i.user.display_name)
            ok, msg = await craft_item(p, code)
            await i.followup.send(msg, ephemeral=True)
        return cb


class ShopView(discord.ui.View):
    def __init__(self, cog, uid, shop_items):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid
        for code in shop_items[:5]:
            item = ITEM_CATALOG.get(code)
            price = SHOP_ITEMS.get(code, {}).get("price", 0)
            if not item: continue
            btn = discord.ui.Button(
                label=f"{item.name[:15]} ({price}💰)",
                style=discord.ButtonStyle.success, custom_id=f"shop_{code}"
            )
            btn.callback = self._make_cb(code, price)
            self.add_item(btn)

    def _make_cb(self, code, price):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid: return
            await i.response.defer(ephemeral=True)
            p = await ensure_player(i.user.id, i.user.display_name)
            if p.coins < price:
                await i.followup.send(f"코인 부족! (필요: {price:,})", ephemeral=True); return
            p.coins -= price
            await add_item(p.user_id, code)
            await save_player(p)
            item = ITEM_CATALOG[code]
            await i.followup.send(f"✅ **{item.name}** 구매 완료! 💰 -{price:,}", ephemeral=True)
        return cb


class HouseView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid

    @discord.ui.button(label="🏠 집 구매", style=discord.ButtonStyle.success)
    async def buy(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        ok, msg = await buy_house(p)
        await i.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="😴 집에서 휴식", style=discord.ButtonStyle.primary)
    async def rest(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        ok, msg = await rest_at_home(p)
        await i.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="🚪 집 나가기", style=discord.ButtonStyle.secondary)
    async def leave(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        p.state["in_house"] = False
        await save_player(p)
        await i.followup.send("집에서 나왔습니다.", ephemeral=True)


class GuildView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid

    @discord.ui.button(label="🏰 길드 창설", style=discord.ButtonStyle.success)
    async def create_guild(self, i, b):
        if i.user.id != self.uid: return
        await i.response.send_modal(GuildCreateModal(self.uid))

    @discord.ui.button(label="🔍 길드 가입", style=discord.ButtonStyle.primary)
    async def join_guild(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        rows = await fetch_all("SELECT guild_name,owner_id FROM guilds LIMIT 10")
        if not rows:
            await i.followup.send("등록된 길드가 없습니다.", ephemeral=True); return
        view = GuildJoinView(self.cog, i.user.id, rows)
        lines = ["**🏰 길드 목록**"] + [f"• {r['guild_name']}" for r in rows]
        await i.followup.send("\n".join(lines), view=view, ephemeral=True)

    @discord.ui.button(label="📋 내 길드", style=discord.ButtonStyle.secondary)
    async def my_guild(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        if not p.guild_id:
            await i.followup.send("길드에 가입되어 있지 않습니다.", ephemeral=True); return
        row = await fetch_one("SELECT * FROM guilds WHERE guild_name=?", (p.guild_id,))
        if not row:
            await i.followup.send("길드 정보를 찾을 수 없습니다.", ephemeral=True); return
        members = uj(row["members_json"])
        await i.followup.send(
            f"**🏰 {row['guild_name']}**\n공지: {row['notice'] or '없음'}\n금고: 💰{row['treasury']:,}\n멤버: {len(members)}명",
            ephemeral=True
        )


class GuildCreateModal(discord.ui.Modal, title="길드 창설"):
    name = discord.ui.TextInput(label="길드 이름", max_length=20)
    notice = discord.ui.TextInput(label="길드 공지", max_length=100, required=False)

    def __init__(self, uid):
        super().__init__()
        self.uid = uid

    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        if p.coins < 10000:
            await i.followup.send("길드 창설 비용: 💰 10,000코인 (부족)", ephemeral=True); return
        existing = await fetch_one("SELECT guild_name FROM guilds WHERE guild_name=?", (self.name.value,))
        if existing:
            await i.followup.send("이미 존재하는 길드 이름입니다.", ephemeral=True); return
        p.coins -= 10000
        p.guild_id = self.name.value
        await execute(
            "INSERT INTO guilds (guild_name,owner_id,notice,members_json) VALUES (?,?,?,?)",
            (self.name.value, p.user_id, self.notice.value or "", j([p.user_id]))
        )
        await save_player(p)
        await i.followup.send(f"🏰 **{self.name.value}** 길드 창설 완료!", ephemeral=True)


class GuildJoinView(discord.ui.View):
    def __init__(self, cog, uid, rows):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid
        for row in rows[:5]:
            btn = discord.ui.Button(label=row["guild_name"][:20], style=discord.ButtonStyle.primary)
            btn.callback = self._make_cb(row["guild_name"])
            self.add_item(btn)

    def _make_cb(self, guild_name):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid: return
            await i.response.defer(ephemeral=True)
            p = await ensure_player(i.user.id, i.user.display_name)
            row = await fetch_one("SELECT * FROM guilds WHERE guild_name=?", (guild_name,))
            if not row:
                await i.followup.send("길드를 찾을 수 없습니다.", ephemeral=True); return
            members = uj(row["members_json"])
            if p.user_id not in members:
                members.append(p.user_id)
                await execute("UPDATE guilds SET members_json=? WHERE guild_name=?", (j(members), guild_name))
            p.guild_id = guild_name
            await save_player(p)
            await i.followup.send(f"🏰 **{guild_name}** 길드에 가입했습니다!", ephemeral=True)
        return cb


class MinigameView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid

    @discord.ui.button(label="🎣 낚시", style=discord.ButtonStyle.primary)
    async def fishing(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        msg = await start_fishing(i.user.id)
        view = FishingReactView(self.cog, i.user.id)
        await i.followup.send(msg, view=view, ephemeral=True)

    @discord.ui.button(label="🎰 슬롯머신", style=discord.ButtonStyle.danger)
    async def slots(self, i, b):
        if i.user.id != self.uid: return
        await i.response.send_modal(SlotBetModal(i.user.id))

    @discord.ui.button(label="🎲 주사위", style=discord.ButtonStyle.secondary)
    async def dice(self, i, b):
        if i.user.id != self.uid: return
        await i.response.send_modal(DiceModal(i.user.id))

    @discord.ui.button(label="🏆 낚시 대회 순위", style=discord.ButtonStyle.secondary)
    async def fishing_rank(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        week = datetime.now(timezone.utc).strftime("%Y-W%U")
        rows = await fetch_all(
            "SELECT fc.user_id,fc.score,p.username FROM fishing_contest fc JOIN players p ON fc.user_id=p.user_id WHERE fc.season_week=? ORDER BY fc.score DESC LIMIT 10",
            (week,)
        )
        if not rows:
            await i.followup.send("이번 주 낚시 대회 기록이 없습니다.", ephemeral=True); return
        lines = [f"**🎣 낚시 대회 순위 (이번 주)**"]
        for idx, r in enumerate(rows, 1):
            lines.append(f"{idx}. {r['username']}: {r['score']:,}점")
        await i.followup.send("\n".join(lines), ephemeral=True)


class FishingReactView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=15)
        self.cog = cog; self.uid = uid

    @discord.ui.button(label="🎣 낚아채기!", style=discord.ButtonStyle.success)
    async def catch(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        ok, msg = await catch_fish(i.user.id)
        if ok:
            q_msg = await check_quest_progress(i.user.id, "fish") or ""
            await i.followup.send(msg + q_msg, ephemeral=True)
        else:
            await i.followup.send(msg, ephemeral=True)
        self.stop()


class SlotBetModal(discord.ui.Modal, title="슬롯머신 베팅"):
    bet = discord.ui.TextInput(label="베팅 코인", placeholder="예: 100", min_length=1, max_length=8)

    def __init__(self, uid):
        super().__init__()
        self.uid = uid

    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        try:
            bet = int(self.bet.value)
        except ValueError:
            await i.followup.send("올바른 숫자를 입력하세요.", ephemeral=True); return
        if bet < 10:
            await i.followup.send("최소 베팅: 10코인", ephemeral=True); return
        p = await ensure_player(i.user.id, i.user.display_name)
        ok, msg = await play_slots(p, bet)
        await i.followup.send(msg, ephemeral=True)


class DiceModal(discord.ui.Modal, title="주사위 도박"):
    bet = discord.ui.TextInput(label="베팅 코인", placeholder="예: 100", min_length=1, max_length=8)
    guess = discord.ui.TextInput(label="예측 숫자 (1~6)", placeholder="예: 3", min_length=1, max_length=1)

    def __init__(self, uid):
        super().__init__()
        self.uid = uid

    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        try:
            bet = int(self.bet.value)
            guess = int(self.guess.value)
        except ValueError:
            await i.followup.send("올바른 숫자를 입력하세요.", ephemeral=True); return
        p = await ensure_player(i.user.id, i.user.display_name)
        ok, msg = await play_dice(p, bet, guess)
        await i.followup.send(msg, ephemeral=True)


class AuctionView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid

    @discord.ui.button(label="📋 매물 목록", style=discord.ButtonStyle.primary)
    async def list_items(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        now = datetime.now(timezone.utc).isoformat()
        rows = await fetch_all("SELECT * FROM auction_house WHERE end_at > ? ORDER BY id DESC LIMIT 10", (now,))
        if not rows:
            await i.followup.send("현재 거래소에 매물이 없습니다.", ephemeral=True); return
        lines = ["**🏪 거래소 매물**"]
        for r in rows:
            item_data = uj(r["item_json"])
            lines.append(f"`ID:{r['id']}` {item_data.get('item_name','?')} | 현재가: {r['current_bid']:,}💰")
        view = AuctionBidView(self.cog, i.user.id, rows)
        await i.followup.send("\n".join(lines), view=view, ephemeral=True)

    @discord.ui.button(label="📦 내 아이템 등록", style=discord.ButtonStyle.success)
    async def sell_item(self, i, b):
        if i.user.id != self.uid: return
        await i.response.defer(ephemeral=True)
        rows = await fetch_all("SELECT * FROM inventory_items WHERE user_id=? LIMIT 10", (i.user.id,))
        if not rows:
            await i.followup.send("등록할 아이템이 없습니다.", ephemeral=True); return
        view = AuctionSellListView(self.cog, i.user.id, rows)
        await i.followup.send("📦 거래소에 등록할 아이템을 선택하세요:", view=view, ephemeral=True)

    @discord.ui.button(label="🔔 알림 설정", style=discord.ButtonStyle.secondary)
    async def watch(self, i, b):
        if i.user.id != self.uid: return
        await i.response.send_modal(AuctionWatchModal(i.user.id))

class AuctionSellListView(discord.ui.View):
    def __init__(self, cog, uid, rows):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid
        for row in rows:
            btn = discord.ui.Button(label=row["item_name"][:20], style=discord.ButtonStyle.secondary)
            btn.callback = self._make_cb(row["id"])
            self.add_item(btn)

    def _make_cb(self, inv_id):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid: return
            await i.response.send_modal(AuctionPriceModal(self.uid, inv_id))
        return cb


class AuctionBidView(discord.ui.View):
    def __init__(self, cog, uid, rows):
        super().__init__(timeout=60)
        self.cog = cog; self.uid = uid
        for row in rows[:5]:
            item_data = uj(row["item_json"])
            btn = discord.ui.Button(
                label=f"입찰: {item_data.get('item_name','?')[:15]}",
                style=discord.ButtonStyle.danger, custom_id=f"bid_{row['id']}"
            )
            btn.callback = self._make_cb(row["id"])
            self.add_item(btn)

    def _make_cb(self, auction_id):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid: return
            await i.response.send_modal(BidModal(i.user.id, auction_id))
        return cb


class BidModal(discord.ui.Modal, title="입찰"):
    bid = discord.ui.TextInput(label="입찰 금액 (코인)", placeholder="현재가보다 높게 입력", min_length=1, max_length=10)

    def __init__(self, uid, auction_id):
        super().__init__()
        self.uid = uid; self.auction_id = auction_id

    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        try:
            bid = int(self.bid.value)
        except ValueError:
            await i.followup.send("올바른 숫자를 입력하세요.", ephemeral=True); return
        p = await ensure_player(i.user.id, i.user.display_name)
        ok, msg = await bid_auction(p, self.auction_id, bid)
        await i.followup.send(msg, ephemeral=True)


class AuctionWatchModal(discord.ui.Modal, title="거래소 알림 설정"):
    keyword = discord.ui.TextInput(label="아이템 키워드", placeholder="예: 드래곤, 전설", max_length=20)

    def __init__(self, uid):
        super().__init__()
        self.uid = uid

    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        msg = await set_auction_watch(i.user.id, self.keyword.value)
        await i.followup.send(msg, ephemeral=True)


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="🗺️ 이동/탐험", style=discord.ButtonStyle.primary)
    async def help_move(self, i, b):
        await i.response.send_message(
            "**🗺️ 이동/탐험**\n"
            "• ↑↓←→ 버튼으로 이동\n"
            "• 🏠 상호작용으로 NPC/집 상호작용\n"
            "• 🗺️ 미니맵으로 전체 월드 확인\n"
            "• 랜드마크: 마을(100,100), 던전(50,50), 콜로세움(150,150)\n"
            "• 귀환 스크롤 사용 시 마을로 순간이동",
            ephemeral=True
        )

    @discord.ui.button(label="⚔️ 전투/스킬", style=discord.ButtonStyle.danger)
    async def help_battle(self, i, b):
        await i.response.send_message(
            "**⚔️ 전투/스킬**\n"
            "• 전투 버튼 → 일반 사냥/던전/스킬 선택\n"
            "• 던전은 5층 클리어 시 보스 등장\n"
            "• 스킬은 MP 소모, 직업별 스킬 상이\n"
            "• 레이드: /레이드생성 → 여러 명이 협동 공격",
            ephemeral=True
        )

    @discord.ui.button(label="🏪 거래/제작", style=discord.ButtonStyle.secondary)
    async def help_trade(self, i, b):
        await i.response.send_message(
            "**🏪 거래/제작**\n"
            "• 거래소 버튼 → 매물 목록/입찰\n"
            "• 인벤토리 → 거래소 등록으로 아이템 판매\n"
            "• NPC 대장장이에서 제작 가능\n"
            "• 제작 아이템: 화염검, 빙결활, 번개지팡이 등",
            ephemeral=True
        )

    @discord.ui.button(label="👥 파티/소셜", style=discord.ButtonStyle.success)
    async def help_social(self, i, b):
        await i.response.send_message(
            "**👥 파티/소셜**\n"
            "• /결혼제안 @상대방 - 결혼 시스템\n"
            "• /의형제 @상대방 - 의형제 맺기\n"
            "• /감정표현 [동작] - 이모트 출력\n"
            "• /초대 - 초대 링크 생성 (보상 있음)\n"
            "• 길드 NPC에서 길드 창설/가입",
            ephemeral=True
        )


# ══════════════════════════════════════════════════════════════════════════
# 24. RPG Cog (슬래시 명령어 - 개발자 명령어 + 진입점)
# ══════════════════════════════════════════════════════════════════════════
class RPGCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._active_raids: Dict[str, str] = {}  # guild_id -> raid_id
        self._quiz_active: Dict[str, dict] = {}  # channel_id -> quiz

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot: return
        # 글로벌 채팅 처리
        row = await fetch_one("SELECT channel_id FROM global_chat_channels WHERE guild_id=?", (str(message.guild.id) if message.guild else "DM",))
        if row and str(message.channel.id) == row["channel_id"]:
            await self.broadcast_global_chat(message)
        
        # 퀴즈 정답 체크 (기존 로직 유지 가능 시)
        ch_id = str(message.channel.id)
        if ch_id in self._quiz_active:
            q = self._quiz_active[ch_id]
            if message.author.id not in q["answered"] and message.content.strip() == q["answer"]:
                q["answered"].add(message.author.id)
                p = await ensure_player(message.author.id, message.author.display_name)
                p.coins += q["reward"]
                await save_player(p)
                await message.reply(f"🎉 정답입니다! 💰{q['reward']:,}코인을 획득했습니다!")

    async def broadcast_global_chat(self, message: discord.Message):
        rows = await fetch_all("SELECT guild_id, channel_id FROM global_chat_channels")
        p = await ensure_player(message.author.id, message.author.display_name)
        title_str = f"[{p.title}] " if p.title else ""
        embed = discord.Embed(
            description=message.content,
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.set_author(name=f"{title_str}{p.username} (Lv.{p.level})", icon_url=message.author.display_avatar.url)
        embed.set_footer(text=f"서버: {message.guild.name if message.guild else 'DM'}")

        for r in rows:
            if r["channel_id"] == str(message.channel.id): continue # 보낸 채널 제외
            target_ch = self.bot.get_channel(int(r["channel_id"]))
            if target_ch:
                try:
                    await target_ch.send(embed=embed)
                except:
                    pass

    @app_commands.command(name="글로벌채팅설정", description="(관리자) 현재 채널을 글로벌 채팅 채널로 설정")
    @app_commands.default_permissions(administrator=True)
    async def set_global_chat(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await execute("INSERT OR REPLACE INTO global_chat_channels (guild_id, channel_id) VALUES (?,?)",
                      (str(i.guild_id) if i.guild_id else "DM", str(i.channel_id)))
        await i.followup.send("✅ 이 채널이 글로벌 채팅 채널로 설정되었습니다!", ephemeral=True)

    # ── 진입점 ──
    @app_commands.command(name="rpg", description="RPG 게임 시작")
    async def rpg(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name, str(i.guild_id) if i.guild_id else None)
        # 튜토리얼
        if p.tutorial_step == 0:
            p.tutorial_step = 1
            await save_player(p)
            await i.followup.send(
                "**🎮 RPG 세계에 오신 것을 환영합니다!**\n"
                "↑↓←→ 버튼으로 이동하고, 🏠 상호작용으로 NPC와 대화하세요.\n"
                "⚔️ 전투 버튼으로 몬스터와 싸우고 레벨을 올려보세요!\n"
                "목표: 마을(100,100)에서 퀘스트 마스터를 찾아보세요!",
                ephemeral=True
            )
        content = render_map(p)
        view = RPGMainView(self, p.user_id)
        msg = await i.followup.send(content, view=view)
        await view.start_refresh(i)

    @app_commands.command(name="랭킹", description="랭킹 확인")
    async def ranking(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        rows = await fetch_all("SELECT username,level,coins FROM players ORDER BY level DESC, coins DESC LIMIT 10")
        lines = ["**🏆 글로벌 랭킹 (레벨 기준)**"]
        for idx, r in enumerate(rows, 1):
            lines.append(f"{idx}. **{r['username']}** | Lv.{r['level']} | 💰{r['coins']:,}")
        # 서버 랭킹
        if i.guild_id:
            server_rows = await fetch_all(
                "SELECT username,level FROM players WHERE guild_id=? ORDER BY level DESC LIMIT 5",
                (str(i.guild_id),)
            )
            if server_rows:
                lines.append(f"\n**🏠 서버 랭킹**")
                for idx, r in enumerate(server_rows, 1):
                    lines.append(f"{idx}. {r['username']} Lv.{r['level']}")
        await i.followup.send("\n".join(lines), ephemeral=True)

    @app_commands.command(name="레이드생성", description="협동 레이드 보스 생성")
    async def create_raid_cmd(self, i: discord.Interaction):
        await i.response.defer()
        raid_id = await create_raid(random.randint(0, len(BOSSES)-1))
        self._active_raids[str(i.guild_id)] = raid_id
        boss = BOSSES[0]
        view = RaidView(self, i.user.id, raid_id)
        await i.followup.send(
            f"🐲 **레이드 보스 등장!**\n보스: {boss['name']}\nHP: {boss['hp']:,}\n"
            f"레이드 ID: `{raid_id}`\n아래 버튼으로 참가 및 공격하세요!",
            view=view
        )

    @app_commands.command(name="결혼제안", description="다른 유저에게 결혼 제안")
    async def marry(self, i: discord.Interaction, 상대방: discord.Member):
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        ok, msg = await propose_marriage(p, 상대방.id)
        if not ok:
            await i.followup.send(msg, ephemeral=True); return
        view = MarriageConfirmView(i.user.id, 상대방.id)
        await i.followup.send(
            f"💍 **{i.user.display_name}**이(가) **{상대방.display_name}**에게 결혼을 제안했습니다!",
            view=view
        )

    @app_commands.command(name="의형제", description="다른 유저와 의형제 맺기")
    async def brotherhood(self, i: discord.Interaction, 상대방: discord.Member):
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        ok, msg = await propose_brotherhood(p, 상대방.id)
        if not ok:
            await i.followup.send(msg, ephemeral=True); return
        view = BrotherhoodConfirmView(i.user.id, 상대방.id)
        await i.followup.send(
            f"🤝 **{i.user.display_name}**이(가) **{상대방.display_name}**에게 의형제를 제안했습니다!",
            view=view
        )

    @app_commands.command(name="감정표현", description="감정 표현")
    async def emote(self, i: discord.Interaction, 동작: str):
        p = await ensure_player(i.user.id, i.user.display_name)
        title_str = f"[{p.title}] " if p.title else ""
        emotes = {
            "인사": f"👋 **{title_str}{p.username}**이(가) 인사를 합니다!",
            "춤": f"💃 **{title_str}{p.username}**이(가) 신나게 춤을 춥니다!",
            "웃음": f"😄 **{title_str}{p.username}**이(가) 크게 웃습니다!",
            "슬픔": f"😢 **{title_str}{p.username}**이(가) 슬퍼합니다...",
            "분노": f"😤 **{title_str}{p.username}**이(가) 분노합니다!",
            "포즈": f"😎 **{title_str}{p.username}**이(가) 멋진 포즈를 취합니다!",
        }
        msg = emotes.get(동작, f"✨ **{title_str}{p.username}**: {동작}")
        await i.response.send_message(msg)

    @app_commands.command(name="초대", description="초대 링크 생성 및 보상 확인")
    async def invite(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        if not p.invite_code:
            p.invite_code = uuid.uuid4().hex[:8].upper()
            await save_player(p)
        invited_count = await fetch_one(
            "SELECT COUNT(*) as cnt FROM players WHERE invited_by=?", (p.user_id,)
        )
        cnt = invited_count["cnt"] if invited_count else 0
        await i.followup.send(
            f"**🎟️ 초대 코드: `{p.invite_code}`**\n"
            f"초대한 친구 수: {cnt}명\n"
            f"초대 보상: 친구 1명당 💰500코인 + 💎5젬\n"
            f"친구가 `/초대등록 {p.invite_code}`를 입력하면 보상이 지급됩니다!",
            ephemeral=True
        )

    @app_commands.command(name="초대등록", description="초대 코드 등록")
    async def register_invite(self, i: discord.Interaction, 코드: str):
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        if p.invited_by:
            await i.followup.send("이미 초대 코드를 등록했습니다.", ephemeral=True); return
        inviter = await fetch_one("SELECT * FROM players WHERE invite_code=?", (코드.upper(),))
        if not inviter:
            await i.followup.send("유효하지 않은 초대 코드입니다.", ephemeral=True); return
        if inviter["user_id"] == p.user_id:
            await i.followup.send("자신의 초대 코드는 사용할 수 없습니다.", ephemeral=True); return
        # 보상 지급
        p.invited_by = inviter["user_id"]
        p.coins += 500; p.gems += 5
        await save_player(p)
        await execute("UPDATE players SET coins=coins+500, gems=gems+5 WHERE user_id=?", (inviter["user_id"],))
        await i.followup.send(f"✅ 초대 코드 등록 완료! 💰+500 💎+5 지급!", ephemeral=True)

    @app_commands.command(name="버그제보", description="버그를 제보합니다")
    async def bug_report(self, i: discord.Interaction, 내용: str):
        await i.response.defer(ephemeral=True)
        await execute("INSERT INTO error_logs (guild_id,error_text) VALUES (?,?)",
                      (str(i.guild_id) if i.guild_id else "DM", f"[{i.user}] {내용}"))
        bug_ch_id = settings.bug_channel_id
        if bug_ch_id:
            ch = self.bot.get_channel(bug_ch_id)
            if ch:
                await ch.send(f"🐛 **버그 제보** by {i.user.mention}\n{내용}")
        await i.followup.send("✅ 버그 제보가 접수되었습니다. 감사합니다!", ephemeral=True)

    @app_commands.command(name="퀴즈", description="(관리자) OX 퀴즈 이벤트 시작")
    @app_commands.default_permissions(administrator=True)
    async def quiz_event(self, i: discord.Interaction, 문제: str, 정답: str, 보상코인: int = 100):
        await i.response.defer()
        ch_id = str(i.channel_id)
        self._quiz_active[ch_id] = {"answer": 정답.strip(), "reward": 보상코인, "answered": set()}
        await i.followup.send(
            f"**❓ 퀴즈 이벤트!**\n{문제}\n\n먼저 정답을 채팅에 입력하면 💰{보상코인:,}코인 획득!"
        )

    @app_commands.command(name="공지", description="(개발자) 모든 서버에 공지 브로드캐스트")
    async def announce(self, i: discord.Interaction, 내용: str):
        if i.user.id not in settings.dev_ids:
            await i.response.send_message("개발자 전용 명령어입니다.", ephemeral=True); return
        await i.response.defer(ephemeral=True)
        sent = 0
        for ch_id in settings.announce_channel_ids:
            ch = self.bot.get_channel(ch_id)
            if ch:
                try:
                    await ch.send(f"📢 **공지사항**\n{내용}")
                    sent += 1
                except Exception:
                    pass
        await i.followup.send(f"✅ {sent}개 채널에 공지 완료.", ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════
    # 개발자 전용 명령어
    # ══════════════════════════════════════════════════════════════════════
    def _is_dev(self, uid: int) -> bool:
        return uid in settings.dev_ids

    @app_commands.command(name="dev_give_item", description="[DEV] 아이템 지급")
    async def dev_give_item(self, i: discord.Interaction, 유저: discord.User, 아이템코드: str, 수량: int = 1):
        if not self._is_dev(i.user.id):
            await i.response.send_message("❌ 개발자 전용 명령어입니다.", ephemeral=True); return
        await i.response.defer(ephemeral=True)
        # 아이템 존재 여부 먼저 확인
        item = ITEM_CATALOG.get(아이템코드)
        if not item:
            # 부분 일치 검색 시도
            matches = [code for code, it in ITEM_CATALOG.items() if 아이템코드.lower() in it.name.lower()]
            if matches:
                아이템코드 = matches[0]
                item = ITEM_CATALOG[아이템코드]
            else:
                await i.followup.send(f"❌ 아이템 '{아이템코드}'를 찾을 수 없습니다.", ephemeral=True)
                return

        ok = await add_item(유저.id, 아이템코드, 수량)
        if ok:
            await i.followup.send(f"✅ {유저.display_name}에게 **{item.name}** (`{아이템코드}`) x{수량} 지급 완료.", ephemeral=True)
        else:
            await i.followup.send(f"❌ 아이템 지급 실패.", ephemeral=True)

    @app_commands.command(name="dev_give_coin", description="[DEV] 코인 지급")
    async def dev_give_coin(self, i: discord.Interaction, 유저: discord.User, 코인: int):
        if not self._is_dev(i.user.id):
            await i.response.send_message("❌ 개발자 전용 명령어입니다.", ephemeral=True); return
        await i.response.defer(ephemeral=True)
        await execute("UPDATE players SET coins=coins+? WHERE user_id=?", (코인, 유저.id))
        await i.followup.send(f"✅ {유저.display_name}에게 💰{코인:,}코인 지급 완료.", ephemeral=True)

    @app_commands.command(name="dev_give_gem", description="[DEV] 젬 지급")
    async def dev_give_gem(self, i: discord.Interaction, 유저: discord.User, 젬: int):
        if not self._is_dev(i.user.id):
            await i.response.send_message("❌ 개발자 전용 명령어입니다.", ephemeral=True); return
        await i.response.defer(ephemeral=True)
        await execute("UPDATE players SET gems=gems+? WHERE user_id=?", (젬, 유저.id))
        await i.followup.send(f"✅ {유저.display_name}에게 💎{젬}젬 지급 완료.", ephemeral=True)

    @app_commands.command(name="dev_item_list", description="[DEV] 아이템 목록 조회")
    async def dev_item_list(self, i: discord.Interaction, 검색: str = ""):
        if not self._is_dev(i.user.id):
            await i.response.send_message("❌ 개발자 전용 명령어입니다.", ephemeral=True); return
        await i.response.defer(ephemeral=True)
        items = [(code, item) for code, item in ITEM_CATALOG.items()
                 if not 검색 or 검색.lower() in item.name.lower() or 검색 in code][:30]
        lines = [f"**📦 아이템 목록** (검색: '{검색}' | {len(items)}개)"]
        for code, item in items:
            lines.append(f"`{code}` {item.name} [{item.rarity}] ATK:{item.power} DEF:{item.defense}")
        await i.followup.send("\n".join(lines), ephemeral=True)

    @app_commands.command(name="dev_admin_report", description="[DEV] 관리자 리포트")
    async def dev_admin_report(self, i: discord.Interaction):
        if not self._is_dev(i.user.id):
            await i.response.send_message("❌ 개발자 전용 명령어입니다.", ephemeral=True); return
        await i.response.defer(ephemeral=True)
        total_players = await fetch_one("SELECT COUNT(*) as cnt FROM players")
        total_items = await fetch_one("SELECT COUNT(*) as cnt FROM inventory_items")
        total_auctions = await fetch_one("SELECT COUNT(*) as cnt FROM auction_house")
        total_errors = await fetch_one("SELECT COUNT(*) as cnt FROM error_logs")
        active_raids = await fetch_all("SELECT COUNT(*) as cnt FROM raid_sessions WHERE state='active'")
        db_size = DB_PATH.stat().st_size // 1024 if DB_PATH.exists() else 0
        report = (
            f"**📊 관리자 리포트**\n"
            f"총 플레이어: {total_players['cnt'] if total_players else 0}명\n"
            f"총 아이템: {total_items['cnt'] if total_items else 0}개\n"
            f"거래소 매물: {total_auctions['cnt'] if total_auctions else 0}개\n"
            f"에러 로그: {total_errors['cnt'] if total_errors else 0}건\n"
            f"활성 레이드: {active_raids[0]['cnt'] if active_raids else 0}개\n"
            f"DB 용량: {db_size}KB"
        )
        try:
            await i.user.send(report)
            await i.followup.send("✅ DM으로 리포트를 전송했습니다.", ephemeral=True)
        except Exception:
            await i.followup.send(report, ephemeral=True)

    @app_commands.command(name="dev_reset_ranking", description="[DEV] 랭킹 초기화")
    async def dev_reset_ranking(self, i: discord.Interaction):
        if not self._is_dev(i.user.id):
            await i.response.send_message("❌ 개발자 전용 명령어입니다.", ephemeral=True); return
        await i.response.defer(ephemeral=True)
        await execute("DELETE FROM rankings WHERE season != 'watch'")
        await i.followup.send("✅ 랭킹이 초기화되었습니다.", ephemeral=True)

    @app_commands.command(name="dev_season_reset", description="[DEV] 시즌 리셋 및 배틀패스 보상 지급")
    async def dev_season_reset(self, i: discord.Interaction):
        if not self._is_dev(i.user.id):
            await i.response.send_message("❌ 개발자 전용 명령어입니다.", ephemeral=True); return
        await i.response.defer(ephemeral=True)
        # 배틀패스 상위권 보상
        top_bp = await fetch_all("SELECT user_id,level FROM battlepass ORDER BY level DESC LIMIT 3")
        rewards = [10000, 5000, 2000]
        for idx, row in enumerate(top_bp):
            r = rewards[idx] if idx < len(rewards) else 500
            await execute("UPDATE players SET coins=coins+? WHERE user_id=?", (r, row["user_id"]))
        # 배틀패스 초기화
        await execute("UPDATE battlepass SET level=0,exp=0,season=season+1")
        await i.followup.send(f"✅ 시즌 리셋 완료! 상위 {len(top_bp)}명에게 보상 지급.", ephemeral=True)

    # 메시지 이벤트 (퀴즈 답변 체크)
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot: return
        ch_id = str(message.channel.id)
        quiz = self._quiz_active.get(ch_id)
        if quiz and message.author.id not in quiz["answered"]:
            if message.content.strip().lower() == quiz["answer"].lower():
                quiz["answered"].add(message.author.id)
                p = await ensure_player(message.author.id, message.author.display_name)
                p.coins += quiz["reward"]
                await save_player(p)
                await message.channel.send(
                    f"🎉 **{message.author.display_name}** 정답! 💰{quiz['reward']:,}코인 획득!"
                )
                del self._quiz_active[ch_id]

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before, after):
        """음성 채널 연동 - 같은 채널에 파티원이 있으면 경험치 보너스"""
        if after.channel and len(after.channel.members) >= 2:
            p = await ensure_player(member.id, member.display_name)
            bonus_until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            p.voice_bonus_until = bonus_until
            await save_player(p)


# ══════════════════════════════════════════════════════════════════════════
# 25. 레이드 View
# ══════════════════════════════════════════════════════════════════════════
class RaidView(discord.ui.View):
    def __init__(self, cog, uid, raid_id):
        super().__init__(timeout=None)
        self.cog = cog; self.uid = uid; self.raid_id = raid_id

    @discord.ui.button(label="⚔️ 레이드 참가", style=discord.ButtonStyle.success)
    async def join(self, i, b):
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        ok, msg = await join_raid(self.raid_id, p)
        await i.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="💥 보스 공격!", style=discord.ButtonStyle.danger)
    async def attack(self, i, b):
        await i.response.defer()
        p = await ensure_player(i.user.id, i.user.display_name)
        cd = check_cooldown(f"raid_{self.raid_id}", i.user.id, 5)
        if cd:
            await i.followup.send(f"⏳ {cd:.0f}초 후 다시 공격 가능!", ephemeral=True); return
        ok, msg = await attack_raid(self.raid_id, p)
        await i.followup.send(msg)

    @discord.ui.button(label="📊 레이드 현황", style=discord.ButtonStyle.secondary)
    async def status(self, i, b):
        await i.response.defer(ephemeral=True)
        row = await fetch_one("SELECT * FROM raid_sessions WHERE raid_id=?", (self.raid_id,))
        if not row:
            await i.followup.send("레이드 정보를 찾을 수 없습니다.", ephemeral=True); return
        participants = uj(row["participants_json"])
        lines = [f"**🐲 {row['boss_name']}**",
                 f"HP: {row['boss_hp']:,} / {row['boss_max_hp']:,}",
                 f"참가자: {len(participants)}명"]
        sorted_p = sorted(participants.items(), key=lambda x: x[1]["dmg"], reverse=True)
        for uid_str, data in sorted_p[:5]:
            lines.append(f"  {data['name']}: {data['dmg']:,} 데미지")
        await i.followup.send("\n".join(lines), ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════
# 26. 결혼 / 의형제 확인 View
# ══════════════════════════════════════════════════════════════════════════
class MarriageConfirmView(discord.ui.View):
    def __init__(self, proposer_id, target_id):
        super().__init__(timeout=120)
        self.proposer_id = proposer_id
        self.target_id = target_id

    @discord.ui.button(label="💍 수락", style=discord.ButtonStyle.success)
    async def accept(self, i, b):
        if i.user.id != self.target_id:
            await i.response.send_message("이 요청의 대상이 아닙니다.", ephemeral=True); return
        await i.response.defer()
        msg = await confirm_marriage(self.proposer_id, self.target_id)
        await i.followup.send(msg)
        self.stop()

    @discord.ui.button(label="❌ 거절", style=discord.ButtonStyle.danger)
    async def reject(self, i, b):
        if i.user.id != self.target_id:
            await i.response.send_message("이 요청의 대상이 아닙니다.", ephemeral=True); return
        await i.response.send_message("💔 결혼 제안을 거절했습니다.")
        self.stop()


class BrotherhoodConfirmView(discord.ui.View):
    def __init__(self, proposer_id, target_id):
        super().__init__(timeout=120)
        self.proposer_id = proposer_id
        self.target_id = target_id

    @discord.ui.button(label="🤝 수락", style=discord.ButtonStyle.success)
    async def accept(self, i, b):
        if i.user.id != self.target_id:
            await i.response.send_message("이 요청의 대상이 아닙니다.", ephemeral=True); return
        await i.response.defer()
        msg = await confirm_brotherhood(self.proposer_id, self.target_id)
        await i.followup.send(msg)
        self.stop()

    @discord.ui.button(label="❌ 거절", style=discord.ButtonStyle.danger)
    async def reject(self, i, b):
        if i.user.id != self.target_id:
            await i.response.send_message("이 요청의 대상이 아닙니다.", ephemeral=True); return
        await i.response.send_message("❌ 의형제 제안을 거절했습니다.")
        self.stop()


# ══════════════════════════════════════════════════════════════════════════
# 27. 주기적 작업 (Tasks)
# ══════════════════════════════════════════════════════════════════════════
class TaskCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.stamina_regen.start()
        self.auction_expire.start()

    def cog_unload(self):
        self.stamina_regen.cancel()
        self.auction_expire.cancel()

    @tasks.loop(minutes=5)
    async def stamina_regen(self):
        """5분마다 모든 플레이어 스태미나 회복"""
        try:
            await execute("UPDATE players SET stamina=MIN(max_stamina, stamina+5)")
        except Exception as e:
            log.error(f"Stamina regen error: {e}")

    @tasks.loop(hours=1)
    async def auction_expire(self):
        """만료된 경매 처리"""
        try:
            now = datetime.now(timezone.utc).isoformat()
            expired = await fetch_all(
                "SELECT * FROM auction_house WHERE end_at <= ? AND highest_bidder > 0", (now,)
            )
            for row in expired:
                # 낙찰자에게 아이템 지급
                item_data = uj(row["item_json"])
                if row["highest_bidder"]:
                    await execute(
                        "INSERT INTO inventory_items (user_id,item_code,item_name,item_type,rarity,qty,power,defense) VALUES (?,?,?,?,?,?,?,?)",
                        (row["highest_bidder"], item_data.get("item_code",""), item_data.get("item_name",""),
                         item_data.get("item_type",""), item_data.get("rarity","일반"), 1,
                         item_data.get("power",0), item_data.get("defense",0))
                    )
                    # 판매자에게 코인 지급 (세금 차감)
                    tax = int(row["current_bid"] * settings.trade_tax_percent / 100)
                    net = row["current_bid"] - tax
                    await execute("UPDATE players SET coins=coins+? WHERE user_id=?", (net, row["seller_id"]))
                await execute("DELETE FROM auction_house WHERE id=?", (row["id"],))
        except Exception as e:
            log.error(f"Auction expire error: {e}")


# ══════════════════════════════════════════════════════════════════════════
# 28. Bot 클래스
# ══════════════════════════════════════════════════════════════════════════
class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await init_db()
        await self.add_cog(RPGCog(self))
        await self.add_cog(TaskCog(self))
        await self.tree.sync()
        log.info("✅ 봇 초기화 완료 - 모든 슬래시 명령어 동기화됨")

    async def on_ready(self):
        log.info(f"✅ {self.user} 로그인 완료! 서버 수: {len(self.guilds)}")
        await self.change_presence(
            activity=discord.Game(name="RPG 어드벤처 | /rpg")
        )

    async def on_error(self, event, *args, **kwargs):
        log.exception(f"이벤트 오류: {event}")


# ══════════════════════════════════════════════════════════════════════════
# 29. 진입점
# ══════════════════════════════════════════════════════════════════════════
bot = Bot()

if __name__ == "__main__":
    token = settings.token
    if token == "TOKEN_HERE":
        log.error("❌ DISCORD_TOKEN이 설정되지 않았습니다! .env 파일을 확인하세요.")
        sys.exit(1)
    bot.run(token, log_handler=None)

