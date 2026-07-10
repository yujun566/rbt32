from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

# ═══════════════════════════════════════════════════════════════════════
# 설정 / 공용 도우미
# ═══════════════════════════════════════════════════════════════════════
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

_cooldowns: Dict[str, Dict[int, float]] = defaultdict(dict)

def check_cooldown(cmd_key: str, uid: int, seconds: float) -> Optional[float]:
    now = time.time()
    last = _cooldowns[cmd_key].get(uid, 0)
    remain = seconds - (now - last)
    if remain > 0:
        return remain
    _cooldowns[cmd_key][uid] = now
    return None

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
        cur = await db.execute(query, tuple(params))
        await db.commit()
        return cur.lastrowid

async def fetch_one(query, params=()):
    async with _db_lock:
        db = await _get_conn()
        cur = await db.execute(query, tuple(params))
        return await cur.fetchone()

async def fetch_all(query, params=()):
    async with _db_lock:
        db = await _get_conn()
        cur = await db.execute(query, tuple(params))
        return await cur.fetchall()

def j(d):
    return json.dumps(d, ensure_ascii=False)

def uj(s):
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {}

async def init_db():
    async with _db_lock:
        db = await _get_conn()
        await db.executescript(
            """
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
            """
        )
        await db.commit()

# ═══════════════════════════════════════════════════════════════════════
# 데이터 카탈로그
# ═══════════════════════════════════════════════════════════════════════
@dataclass(slots=True)
class Item:
    code: str
    name: str
    item_type: str
    rarity: str
    power: int = 0
    defense: int = 0
    meta: dict = field(default_factory=dict)

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
        ITEM_CATALOG[c[0]] = Item(c[0], c[1], c[2], c[3], c[4], c[5], c[6])

    for t_idx, t in enumerate(WEAPON_TYPES):
        for lv in range(1, 51):
            for r_idx, r in enumerate(RARITIES):
                code = f"w_{t_idx}_{lv}_{r_idx}"
                ITEM_CATALOG[code] = Item(code, f"{RARITY_EMOJI[r]} {r} {t} Lv.{lv}", "무기", r, power=lv * 10 + r_idx * 25)

    for t_idx, t in enumerate(ARMOR_TYPES):
        for lv in range(1, 51):
            for r_idx, r in enumerate(RARITIES):
                code = f"a_{t_idx}_{lv}_{r_idx}"
                ITEM_CATALOG[code] = Item(code, f"{RARITY_EMOJI[r]} {r} {t} Lv.{lv}", "방어구", r, defense=lv * 5 + r_idx * 15)

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
        ITEM_CATALOG[c[0]] = Item(c[0], c[1], c[2], c[3], c[4], c[5], c[6])

_build_items()

CRAFT_RECIPES = {
    "craft_fire_sword": {"재료": {"mat_철":10, "mat_드래곤하트":2}, "코인":5000},
    "craft_ice_bow":    {"재료": {"mat_미스릴":10, "mat_마나수정":3}, "코인":5000},
    "craft_thunder_staff": {"재료": {"mat_오리할콘":10, "mat_마나수정":5}, "코인":10000},
    "craft_dragon_armor": {"재료": {"mat_드래곤하트":10, "mat_철":20}, "코인":15000},
    "craft_shadow_cloak": {"재료": {"mat_고대유물":10, "mat_미스릴":15}, "코인":20000},
    "craft_holy_shield": {"재료": {"mat_마나수정":20, "mat_오리할콘":10}, "코인":25000},
    "potion_hp_m": {"재료": {"mat_나무":5, "mat_돌":5}, "코인":500},
    "potion_hp_l": {"재료": {"mat_철":5, "mat_금":2}, "코인":2000},
    "fishing_rod_gold": {"재료": {"mat_금":10, "mat_다이아몬드":2}, "코인":10000},
}

SKILL_TREE = {
    "전사": {
        "power_strike": {"name":"💥 파워 스트라이크","mp":10,"mult":2.0,"desc":"강력한 일격"},
        "shield_bash": {"name":"🛡️ 쉴드 배쉬","mp":15,"mult":1.5,"stun":True,"desc":"기절 유발"},
        "berserk": {"name":"😤 광전사","mp":20,"mult":3.0,"desc":"HP 30% 이하 시 3배 데미지"},
        "war_cry": {"name":"📣 전투 함성","mp":25,"mult":1.0,"team_atk":1.3,"desc":"파티 공격력 30% 증가"},
    },
    "궁수": {
        "double_shot": {"name":"🏹 더블 샷","mp":12,"mult":2.2,"desc":"두 번 공격"},
        "poison_arrow": {"name":"🧪 독 화살","mp":18,"mult":1.8,"dot":True,"desc":"독 데미지 지속"},
        "eagle_eye": {"name":"🦅 독수리 눈","mp":15,"mult":2.5,"crit_boost":30,"desc":"치명타율 30% 증가"},
        "rain_of_arrows": {"name":"🌧️ 화살비","mp":30,"mult":1.5,"aoe":True,"desc":"전체 공격"},
    },
    "마법사": {
        "fireball": {"name":"🔥 파이어볼","mp":20,"mult":3.0,"desc":"강력한 화염 공격"},
        "mana_shield": {"name":"🌀 마나 쉴드","mp":25,"def_boost":50,"desc":"방어력 50 증가"},
        "blizzard": {"name":"❄️ 블리자드","mp":35,"mult":2.5,"slow":True,"desc":"광역 빙결"},
        "meteor": {"name":"☄️ 메테오","mp":50,"mult":5.0,"desc":"최강 마법 공격"},
    },
    "성직자": {
        "heal": {"name":"💚 힐","mp":15,"heal":100,"desc":"HP 회복"},
        "holy_light": {"name":"✨ 성광","mp":20,"mult":2.0,"desc":"언데드 특효"},
        "resurrection": {"name":"🌟 부활","mp":50,"revive":True,"desc":"전투 중 부활"},
        "blessing": {"name":"🙏 축복","mp":30,"team_def":1.3,"desc":"파티 방어력 30% 증가"},
    },
    "도적": {
        "backstab": {"name":"🗡️ 백스탭","mp":10,"mult":3.5,"desc":"뒤에서 치명타"},
        "smoke_bomb": {"name":"💨 연막탄","mp":15,"evade":True,"desc":"1턴 회피"},
        "steal": {"name":"💰 도둑질","mp":12,"steal":True,"desc":"적 코인 탈취"},
        "shadow_step": {"name":"👣 그림자 발걸음","mp":20,"mult":2.0,"first":True,"desc":"선제 공격"},
    },
    "기사": {
        "holy_slash": {"name":"⚔️ 홀리 슬래시","mp":18,"mult":2.8,"desc":"성스러운 일격"},
        "fortress": {"name":"🏰 철벽","mp":20,"def_boost":70,"desc":"압도적 방어"},
    },
    "암흑기사": {
        "dark_crush": {"name":"🌑 다크 크러시","mp":24,"mult":3.5,"desc":"어둠의 강타"},
        "blood_armor": {"name":"🩸 피의 갑주","mp":20,"heal":60,"desc":"흡혈 방어"},
    },
    "성궁수": {
        "sun_burst": {"name":"☀️ 태양 연사","mp":22,"mult":3.0,"desc":"빛의 연사"},
    },
    "정령술사": {
        "spirit_call": {"name":"🧚 정령 소환","mp":26,"mult":3.2,"desc":"정령의 일격"},
    },
    "대사제": {
        "sacred_prayer": {"name":"⛪ 성역 기도","mp":32,"heal":220,"desc":"강력한 치유"},
    },
    "그림자군주": {
        "night_reap": {"name":"🌘 나이트 리프","mp":28,"mult":4.0,"desc":"그림자 수확"},
    },
    "용기사": {
        "dragon_drive": {"name":"🐉 드래곤 드라이브","mp":35,"mult":4.5,"desc":"용의 돌진"},
    },
    "시공마도사": {
        "time_break": {"name":"🕰️ 타임 브레이크","mp":40,"mult":4.8,"desc":"시공 붕괴"},
    },
    "심판자": {
        "judgement": {"name":"⚖️ 저지먼트","mp":38,"mult":4.6,"desc":"심판의 빛"},
    },
    "재앙의 그림자": {
        "catastrophe": {"name":"☠️ 카타스트로피","mp":42,"mult":5.2,"desc":"재앙의 일격"},
    },
}

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

NPCS = {
    "quest_npc_1": {"name":"📜 퀘스트 마스터 에리온","x":100,"y":98,"dialogue":"용사여, 마을을 위협하는 몬스터들을 처치해주시오!","quests":["kill_wolf_10","kill_boss_1","collect_mat_5","fish_10","explore_50"]},
    "shop_npc_1": {"name":"🏪 상인 마르코","x":102,"y":100,"dialogue":"어서오세요! 좋은 물건 많습니다.","shop_items":["potion_hp_s","potion_hp_m","potion_mp_s","scroll_teleport","fishing_rod"]},
    "blacksmith_npc": {"name":"⚒️ 대장장이 볼드","x":98,"y":100,"dialogue":"강화와 제작은 저에게 맡기세요!","services":["enchant","craft"]},
    "guild_npc": {"name":"🏰 길드 관리인 세라","x":100,"y":102,"dialogue":"길드를 창설하거나 가입하시겠습니까?","services":["guild"]},
    "pvp_npc": {"name":"⚔️ 콜로세움 관리인 마르스","x":150,"y":150,"dialogue":"실력을 증명해보세요. PVP와 전직의 전설이 여기서 시작됩니다.","services":["pvp"]},
}

QUEST_DATA = {
    "kill_wolf_10": {"name":"늑대 사냥","desc":"늑대 10마리 처치","target":10,"type":"kill","reward_coins":500,"reward_exp":200,"reward_item":"potion_hp_m"},
    "kill_boss_1": {"name":"보스 토벌","desc":"던전 보스 1마리 처치","target":1,"type":"kill_boss","reward_coins":2000,"reward_exp":1000,"reward_item":"w_0_10_2"},
    "collect_mat_5": {"name":"재료 수집","desc":"철 재료 5개 수집","target":5,"type":"collect","item":"mat_철","reward_coins":300,"reward_exp":150,"reward_item":"potion_mp_m"},
    "fish_10": {"name":"낚시왕","desc":"물고기 10마리 잡기","target":10,"type":"fish","reward_coins":400,"reward_exp":180,"reward_item":"fishing_rod_gold"},
    "explore_50": {"name":"탐험가","desc":"50칸 이동","target":50,"type":"move","reward_coins":200,"reward_exp":100,"reward_item":"scroll_teleport"},
}

LANDMARKS = {
    (100,100): {"type":"town","name":"🏘️ 시작 마을","desc":"안전 지역. 회복 가능."},
    (50,50): {"type":"dungeon","name":"🏰 고대 던전","desc":"위험! 강한 몬스터 출현."},
    (150,150): {"type":"colosseum","name":"⚔️ 콜로세움","desc":"PVP와 전직 도전자의 성지."},
    (30,170): {"type":"shop","name":"🏪 대상인 거리","desc":"희귀 아이템 거래 가능."},
    (170,30): {"type":"fishing","name":"🎣 낚시터","desc":"다양한 물고기 서식."},
    (100,50): {"type":"halloween","name":"🎃 할로윈 존","desc":"한정 이벤트 지역."},
    (100,150): {"type":"christmas","name":"🎄 크리스마스 존","desc":"한정 이벤트 지역."},
}

FISH_TABLE = [("🐟 잡어",5,10),("🐠 열대어",15,30),("🦈 상어",50,100),("🐙 문어",80,150),("🐋 고래",200,500),("✨ 전설의 물고기",1000,3000)]
SHOP_ITEMS = {
    "potion_hp_s": {"price":100}, "potion_hp_m": {"price":300}, "potion_hp_l": {"price":800},
    "potion_mp_s": {"price":80}, "potion_mp_m": {"price":250}, "scroll_teleport": {"price":200},
    "scroll_dungeon": {"price":500}, "fishing_rod": {"price":150},
}
BP_REWARDS = {
    1: {"free":"potion_hp_s","premium":"potion_hp_m"},
    5: {"free":"potion_mp_s","premium":"w_0_5_2"},
    10: {"free":"scroll_teleport","premium":"w_0_10_3"},
    20: {"free":"mat_미스릴","premium":"a_0_15_3"},
    30: {"free":"fishing_rod","premium":"craft_fire_sword"},
    50: {"free":"w_0_20_2","premium":"craft_dragon_armor"},
}

JOB_DATA = {
    "초보자": {"tier":"기본","parent":None,"bonus":{},"desc":"모든 전직의 시작."},
    "전사": {"tier":"일반","parent":"초보자","bonus":{"attack":12,"defense":8,"max_hp":40},"requirements":{"level":10},"desc":"안정적인 근접 전투 전문가."},
    "궁수": {"tier":"일반","parent":"초보자","bonus":{"attack":14,"crit":6,"max_mp":10},"requirements":{"level":10},"desc":"치명타와 원거리 공격에 특화."},
    "마법사": {"tier":"일반","parent":"초보자","bonus":{"attack":16,"max_mp":40},"requirements":{"level":10},"desc":"강력한 마법 화력 보유."},
    "성직자": {"tier":"일반","parent":"초보자","bonus":{"defense":5,"max_hp":20,"max_mp":30},"requirements":{"level":10},"desc":"회복과 축복의 전문가."},
    "도적": {"tier":"일반","parent":"초보자","bonus":{"attack":10,"crit":10},"requirements":{"level":10},"desc":"빠른 일격과 높은 치명타."},
    "기사": {"tier":"히든","parent":"전사","bonus":{"attack":10,"defense":15,"max_hp":60},"requirements":{"level":20,"defense":35},"desc":"방패와 명예를 중시하는 수호자."},
    "암흑기사": {"tier":"시크릿","parent":"기사","bonus":{"attack":20,"defense":10,"max_hp":80},"requirements":{"level":35,"kill_count":180},"desc":"어둠의 힘을 받아들인 기사."},
    "성궁수": {"tier":"히든","parent":"궁수","bonus":{"attack":18,"crit":12},"requirements":{"level":22,"pvp_kill":3},"desc":"신성한 화살을 다루는 궁수."},
    "정령술사": {"tier":"히든","parent":"마법사","bonus":{"attack":22,"max_mp":70},"requirements":{"level":22,"fish_count":20},"desc":"정령과 교감하는 마도사."},
    "대사제": {"tier":"히든","parent":"성직자","bonus":{"defense":12,"max_hp":50,"max_mp":60},"requirements":{"level":22,"raid_count":2},"desc":"빛의 사제를 넘어선 존재."},
    "그림자군주": {"tier":"히든","parent":"도적","bonus":{"attack":20,"crit":15},"requirements":{"level":22,"kill_count":120},"desc":"그림자 세계의 지배자."},
    "용기사": {"tier":"시크릿","parent":"기사","bonus":{"attack":28,"defense":20,"max_hp":100},"requirements":{"level":40,"item":"craft_dragon_armor"},"desc":"용의 힘을 계승한 전설의 기사."},
    "시공마도사": {"tier":"시크릿","parent":"정령술사","bonus":{"attack":30,"max_mp":120},"requirements":{"level":40,"item":"craft_thunder_staff"},"desc":"시공간을 비트는 초월 마도사."},
    "심판자": {"tier":"시크릿","parent":"대사제","bonus":{"attack":24,"defense":18,"max_hp":90},"requirements":{"level":40,"pvp_kill":10},"desc":"빛의 심판을 내리는 사도."},
    "재앙의 그림자": {"tier":"시크릿","parent":"그림자군주","bonus":{"attack":34,"crit":20},"requirements":{"level":42,"kill_count":300},"desc":"밤을 집어삼키는 암살 군주."},
}

TITLES = {
    "몬스터 헌터": "몬스터 100마리 처치",
    "대상인": "거래소 거래 50회",
    "탐험가": "1000칸 이동",
    "낚시왕": "물고기 100마리",
    "레이드 영웅": "레이드 보스 10회 격파",
    "PVP 챔피언": "PVP 50승",
    "장인": "아이템 제작 20회",
    "부자": "코인 100만 보유",
}

# ═══════════════════════════════════════════════════════════════════════
# 플레이어 / 랭킹 / 전직 헬퍼
# ═══════════════════════════════════════════════════════════════════════
@dataclass(slots=True)
class PlayerRecord:
    user_id: int
    username: str
    guild_id: Optional[str] = None
    job: str = "초보자"
    title: str = ""
    x: int = 100
    y: int = 100
    hp: int = 150
    max_hp: int = 150
    mp: int = 50
    max_mp: int = 50
    stamina: int = 100
    max_stamina: int = 100
    level: int = 1
    exp: int = 0
    coins: int = 1000
    gems: int = 10
    attack: int = 10
    defense: int = 5
    crit: int = 5
    facing: str = "S"
    biome: str = "평원"
    equipment: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    appearance: dict = field(default_factory=dict)
    achievements: list = field(default_factory=list)
    tutorial_step: int = 0
    season_bp_level: int = 0
    season_bp_exp: int = 0
    partner_id: int = 0
    brother_id: int = 0
    invite_code: str = ""
    invited_by: int = 0
    voice_bonus_until: str = ""

    @classmethod
    def from_row(cls, r):
        d = dict(r) if not isinstance(r, dict) else r
        return cls(
            user_id=d["user_id"], username=d["username"], guild_id=d.get("guild_id"),
            job=d["job"], title=d["title"] or "", x=d["x"], y=d["y"],
            hp=d["hp"], max_hp=d["max_hp"], mp=d["mp"], max_mp=d["max_mp"],
            stamina=d["stamina"], max_stamina=d["max_stamina"], level=d["level"], exp=d["exp"],
            coins=d["coins"], gems=d["gems"], attack=d["attack"], defense=d["defense"], crit=d["crit"],
            facing=d["facing"], biome=d["biome"], equipment=uj(d.get("equipment_json", "{}")),
            state=uj(d.get("state_json", "{}")), appearance=uj(d.get("appearance_json", "{}")),
            achievements=uj(d.get("achievements_json", "[]")) if d.get("achievements_json") else [],
            tutorial_step=d.get("tutorial_step") or 0, season_bp_level=d.get("season_bp_level") or 0,
            season_bp_exp=d.get("season_bp_exp") or 0, partner_id=d.get("partner_id") or 0,
            brother_id=d.get("brother_id") or 0, invite_code=d.get("invite_code") or "",
            invited_by=d.get("invited_by") or 0, voice_bonus_until=d.get("voice_bonus_until") or "",
        )


def ensure_state_defaults(p: PlayerRecord):
    if not isinstance(p.state, dict):
        p.state = {}
    p.state.setdefault("kill_count", 0)
    p.state.setdefault("move_count", 0)
    p.state.setdefault("fish_count", 0)
    p.state.setdefault("raid_count", 0)
    p.state.setdefault("pvp_win", 0)
    p.state.setdefault("pvp_loss", 0)
    p.state.setdefault("pvp_kill", 0)
    p.state.setdefault("craft_count", 0)
    p.state.setdefault("trade_count", 0)
    p.state.setdefault("job_history", [p.job] if p.job else ["초보자"])
    p.state.setdefault("job_bonus", {})
    p.state.setdefault("slots_today", {})


async def save_player(p: PlayerRecord):
    ensure_state_defaults(p)
    await execute(
        """
        UPDATE players SET username=?,guild_id=?,job=?,title=?,x=?,y=?,hp=?,max_hp=?,mp=?,max_mp=?,
        stamina=?,max_stamina=?,level=?,exp=?,coins=?,gems=?,attack=?,defense=?,crit=?,
        facing=?,biome=?,equipment_json=?,state_json=?,appearance_json=?,achievements_json=?,
        tutorial_step=?,season_bp_level=?,season_bp_exp=?,partner_id=?,brother_id=?,
        invite_code=?,invited_by=?,voice_bonus_until=? WHERE user_id=?
        """,
        (
            p.username,p.guild_id,p.job,p.title,p.x,p.y,p.hp,p.max_hp,p.mp,p.max_mp,
            p.stamina,p.max_stamina,p.level,p.exp,p.coins,p.gems,p.attack,p.defense,p.crit,
            p.facing,p.biome,j(p.equipment),j(p.state),j(p.appearance),j(p.achievements),
            p.tutorial_step,p.season_bp_level,p.season_bp_exp,p.partner_id,p.brother_id,
            p.invite_code,p.invited_by,p.voice_bonus_until,p.user_id,
        ),
    )


async def ensure_player(uid: int, name: str, gid=None) -> PlayerRecord:
    row = await fetch_one("SELECT * FROM players WHERE user_id=?", (uid,))
    if row:
        p = PlayerRecord.from_row(row)
        ensure_state_defaults(p)
        return p
    code = uuid.uuid4().hex[:8].upper()
    await execute("INSERT INTO players (user_id,username,guild_id,invite_code) VALUES (?,?,?,?)", (uid, name, gid, code))
    p = await ensure_player(uid, name, gid)
    await save_player(p)
    return p


async def add_exp(p: PlayerRecord, exp: int):
    p.exp += exp
    need = p.level * 100
    leveled = False
    while p.exp >= need:
        p.exp -= need
        p.level += 1
        p.max_hp += 20
        p.hp = p.max_hp
        p.max_mp += 10
        p.mp = p.max_mp
        p.attack += 3
        p.defense += 2
        p.crit += 1
        need = p.level * 100
        leveled = True
    await save_player(p)
    return leveled


async def add_item(uid: int, item_code: str, qty: int = 1):
    item = ITEM_CATALOG.get(item_code)
    if not item:
        return False
    row = await fetch_one("SELECT id,qty FROM inventory_items WHERE user_id=? AND item_code=?", (uid, item_code))
    if row:
        await execute("UPDATE inventory_items SET qty=qty+? WHERE id=?", (qty, row["id"]))
    else:
        await execute(
            "INSERT INTO inventory_items (user_id,item_code,item_name,item_type,rarity,qty,power,defense,meta_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (uid, item_code, item.name, item.item_type, item.rarity, qty, item.power, item.defense, j(item.meta)),
        )
    return True


async def has_item(uid: int, item_code: str) -> bool:
    row = await fetch_one("SELECT qty FROM inventory_items WHERE user_id=? AND item_code=?", (uid, item_code))
    return bool(row and row["qty"] > 0)


async def add_rank_score(uid: int, category: str, delta: int, season: str = "global"):
    await execute(
        """
        INSERT INTO rankings (user_id,category,score,season) VALUES (?,?,?,?)
        ON CONFLICT(user_id,category,season) DO UPDATE SET score=score+excluded.score
        """,
        (uid, category, delta, season),
    )


async def set_rank_score(uid: int, category: str, score: int, season: str = "global"):
    await execute(
        """
        INSERT INTO rankings (user_id,category,score,season) VALUES (?,?,?,?)
        ON CONFLICT(user_id,category,season) DO UPDATE SET score=excluded.score
        """,
        (uid, category, score, season),
    )


async def get_leaderboard(category: str, limit: int = 10) -> List[aiosqlite.Row]:
    return await fetch_all(
        """
        SELECT p.username, p.level, r.score
        FROM rankings r
        JOIN players p ON p.user_id = r.user_id
        WHERE r.category=? AND r.season='global'
        ORDER BY r.score DESC, p.level DESC, p.username ASC
        LIMIT ?
        """,
        (category, limit),
    )


def get_job_bonus(job_name: str) -> dict:
    return JOB_DATA.get(job_name, {}).get("bonus", {})


def job_skill_key(job_name: str) -> str:
    if job_name in SKILL_TREE:
        return job_name
    parent = JOB_DATA.get(job_name, {}).get("parent")
    while parent:
        if parent in SKILL_TREE:
            return parent
        parent = JOB_DATA.get(parent, {}).get("parent")
    return job_name


def check_job_requirements(p: PlayerRecord, job_name: str) -> Tuple[bool, str]:
    info = JOB_DATA.get(job_name)
    if not info:
        return False, "존재하지 않는 전직입니다."
    req = info.get("requirements", {})
    if p.level < req.get("level", 0):
        return False, f"레벨 {req['level']} 이상 필요"
    if req.get("defense") and p.defense < req["defense"]:
        return False, f"방어력 {req['defense']} 이상 필요"
    if req.get("kill_count") and p.state.get("kill_count", 0) < req["kill_count"]:
        return False, f"몬스터 처치 {req['kill_count']}회 필요"
    if req.get("pvp_kill") and p.state.get("pvp_kill", 0) < req["pvp_kill"]:
        return False, f"PVP 킬 {req['pvp_kill']}회 필요"
    if req.get("fish_count") and p.state.get("fish_count", 0) < req["fish_count"]:
        return False, f"낚시 {req['fish_count']}회 필요"
    if req.get("raid_count") and p.state.get("raid_count", 0) < req["raid_count"]:
        return False, f"레이드 {req['raid_count']}회 필요"
    if req.get("item"):
        equipped = bool(p.equipment and req["item"] in p.equipment.values())
        if not equipped:
            return False, f"장비/소지 조건 필요: {ITEM_CATALOG.get(req['item'], Item(req['item'], req['item'], '', '')).name}"
    parent = info.get("parent")
    if parent and parent != "초보자" and p.job != parent:
        return False, f"{parent} 계열에서만 전직 가능"
    return True, "가능"


async def apply_job_change(p: PlayerRecord, new_job: str, learned_from: int = 0) -> Tuple[bool, str]:
    if new_job == p.job:
        return False, "이미 해당 직업입니다."
    ensure_state_defaults(p)
    old_bonus = p.state.get("job_bonus", {})
    p.attack -= old_bonus.get("attack", 0)
    p.defense -= old_bonus.get("defense", 0)
    p.crit -= old_bonus.get("crit", 0)
    p.max_hp -= old_bonus.get("max_hp", 0)
    p.max_mp -= old_bonus.get("max_mp", 0)
    p.max_hp = max(50, p.max_hp)
    p.max_mp = max(10, p.max_mp)
    p.hp = min(p.hp, p.max_hp)
    p.mp = min(p.mp, p.max_mp)

    new_bonus = get_job_bonus(new_job)
    p.attack += new_bonus.get("attack", 0)
    p.defense += new_bonus.get("defense", 0)
    p.crit += new_bonus.get("crit", 0)
    p.max_hp += new_bonus.get("max_hp", 0)
    p.max_mp += new_bonus.get("max_mp", 0)
    p.hp = p.max_hp
    p.mp = p.max_mp
    p.job = new_job
    p.state["job_bonus"] = new_bonus
    if new_job not in p.state["job_history"]:
        p.state["job_history"].append(new_job)
    if learned_from:
        p.state["last_teacher_id"] = learned_from
    await save_player(p)
    return True, f"🎓 **{new_job}** 전직 완료!"


async def get_available_jobs(p: PlayerRecord) -> Dict[str, List[Tuple[str, str, bool]]]:
    ensure_state_defaults(p)
    result = {"일반": [], "히든": [], "시크릿": []}
    for job_name, info in JOB_DATA.items():
        tier = info.get("tier")
        if tier not in result:
            continue
        ok, reason = check_job_requirements(p, job_name)
        result[tier].append((job_name, reason, ok))
    return result

# ═══════════════════════════════════════════════════════════════════════
# 월드 / 전투 / 시스템 함수
# ═══════════════════════════════════════════════════════════════════════
BIOME_TILES = {"평원":"🟫","숲":"🌲","사막":"🏜️","설원":"❄️","화산":"🌋","바다":"🌊","동굴":"🕳️"}


def _get_biome(x: int, y: int) -> str:
    v = (x * 3 + y * 7) % 100
    if v < 40: return "평원"
    if v < 60: return "숲"
    if v < 70: return "사막"
    if v < 78: return "설원"
    if v < 84: return "화산"
    if v < 90: return "바다"
    return "동굴"


def _get_tile(x: int, y: int, px: int, py: int) -> str:
    if x == px and y == py:
        return "😺"
    if (x, y) in LANDMARKS:
        return {"town":"🏘️","dungeon":"🏰","colosseum":"⚔️","shop":"🏪","fishing":"🎣","halloween":"🎃","christmas":"🎄"}.get(LANDMARKS[(x, y)]["type"], "❓")
    for npc in NPCS.values():
        if x == npc["x"] and y == npc["y"]:
            return "💬"
    if (x + y) % 23 == 0:
        return "🏠"
    return BIOME_TILES.get(_get_biome(x, y), "🟫")


def render_raid_screen(p: PlayerRecord) -> str:
    return "\n".join([
        "```",
        "╔════════════ 🐲 레이드 ════════════╗",
        "║      🔥 전장의 한복판! 🔥          ║",
        "║                                      ║",
        "║          🐲 거대 보스 🐲            ║",
        "║          ⚔️⚔️⚔️⚔️⚔️⚔️⚔️⚔️            ║",
        "║                                      ║",
        f"╠══ ❤️{p.hp}/{p.max_hp} 💙{p.mp}/{p.max_mp} ⚡{p.stamina}/{p.max_stamina} ══╣",
        "╚══════════════════════════════════════╝",
        "```",
    ])


def render_house_screen(p: PlayerRecord) -> str:
    return "\n".join([
        "```",
        "╔════════════ 🏠 내 집 ════════════╗",
        f"║ 플레이어: {p.username}의 아늑한 보금자리  ║",
        "║                                      ║",
        "║      🛏️        📺        🪑      ║",
        "║     침대      TV      의자      ║",
        "║                                      ║",
        f"╠══ ❤️{p.hp}/{p.max_hp} 💙{p.mp}/{p.max_mp} ⚡{p.stamina}/{p.max_stamina} ══╣",
        "╚══════════════════════════════════════╝",
        "```",
    ])


def render_map(p: PlayerRecord, view_dist: int = 4) -> str:
    ensure_state_defaults(p)
    if p.state.get("in_raid"):
        return render_raid_screen(p)
    if p.state.get("in_house"):
        return render_house_screen(p)
    title_str = f"[{p.title}] " if p.title else ""
    lines = ["```", f"╔══ {title_str}{p.username} | Lv.{p.level} {p.job} ══╗", f"║ 📍 위치: ({p.x},{p.y}) | {p.biome} ║"]
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


def render_minimap(p: PlayerRecord) -> str:
    mini = 20
    step_x = WORLD_W // mini
    step_y = WORLD_H // mini
    lines = ["```", "╔══════ 🗺️ 월드 미니맵 ══════╗"]
    for my in range(mini):
        row = "║"
        for mx in range(mini):
            wx = mx * step_x + step_x // 2
            wy = my * step_y + step_y // 2
            if abs(wx - p.x) <= step_x // 2 and abs(wy - p.y) <= step_y // 2:
                row += "😺"
                continue
            found = False
            for (lx, ly), lm in LANDMARKS.items():
                if abs(wx - lx) <= step_x and abs(wy - ly) <= step_y:
                    row += {"town":"🏘","dungeon":"🏰","colosseum":"⚔","shop":"🏪","fishing":"🎣","halloween":"🎃","christmas":"🎄"}.get(lm["type"], "❓")
                    found = True
                    break
            if not found:
                row += {"평원":"🟩","숲":"🌲","사막":"🟨","설원":"⬜","화산":"🟥","바다":"🟦","동굴":"⬛"}.get(_get_biome(wx, wy), "🟩")
        lines.append(row + "║")
    lines.extend(["╚══════════════════════════════╝", "  😺=나  🏘=마을  🏰=던전  ⚔=콜로세움", "```"])
    return "\n".join(lines)


async def try_move(p: PlayerRecord, d: str) -> Tuple[bool, str]:
    dx, dy = {"W":(0,-1),"A":(-1,0),"S":(0,1),"D":(1,0)}.get(d, (0,0))
    nx, ny = max(0, min(WORLD_W-1, p.x + dx)), max(0, min(WORLD_H-1, p.y + dy))
    row = await fetch_one("SELECT tile_type FROM world_tiles WHERE x=? AND y=?", (nx, ny))
    if row and row["tile_type"] in ["wall", "water"]:
        return False, f"🚫 이동 불가 ({row['tile_type']})"
    p.x, p.y, p.facing = nx, ny, d
    p.stamina = max(0, p.stamina - 1)
    p.biome = _get_biome(nx, ny)
    p.state["move_count"] = p.state.get("move_count", 0) + 1
    await save_player(p)
    q_msg = await check_quest_progress(p.user_id, "move") or ""
    lm = LANDMARKS.get((nx, ny))
    if lm:
        return True, f"📍 {lm['name']} 도착!\n{lm['desc']}\n{q_msg}".strip()
    return True, f"📍 ({nx},{ny}) {p.biome}\n{q_msg}".strip()


def calc_damage(atk: int, def_: int, crit: int) -> Tuple[int, bool]:
    is_crit = random.randint(1, 100) <= crit
    dmg = max(1, atk - def_ + random.randint(-3, 3))
    if is_crit:
        dmg = int(dmg * 1.8)
    return dmg, is_crit


async def fight_monster(p: PlayerRecord, monster: dict) -> dict:
    mob = dict(monster)
    mob_hp = mob["hp"]
    lines = [f"⚔️ **{mob['name']}** 와(과) 전투 시작!"]
    rounds = 0
    while p.hp > 0 and mob_hp > 0 and rounds < 20:
        dmg, crit = calc_damage(p.attack, mob["def"], p.crit)
        mob_hp -= dmg
        lines.append(f"{'💥 치명타! ' if crit else ''}내 공격: **{dmg}** → 몬스터 HP: {max(0,mob_hp)}")
        if mob_hp <= 0:
            break
        m_dmg, _ = calc_damage(mob["atk"], p.defense, 5)
        p.hp = max(0, p.hp - m_dmg)
        lines.append(f"몬스터 공격: **{m_dmg}** → 내 HP: {p.hp}")
        rounds += 1
    if mob_hp <= 0:
        exp_gain = mob["exp"]
        coin_gain = mob["coins"] + random.randint(0, max(1, mob["coins"] // 2))
        leveled = await add_exp(p, exp_gain)
        p.coins += coin_gain
        p.state["kill_count"] = p.state.get("kill_count", 0) + 1
        await set_rank_score(p.user_id, "level", p.level)
        await save_player(p)
        q_msg = await check_quest_progress(p.user_id, "kill") or ""
        drop_item = None
        if random.random() < mob.get("drop_rate", 0.3):
            drop_code = random.choice(list(ITEM_CATALOG.keys()))
            await add_item(p.user_id, drop_code)
            drop_item = ITEM_CATALOG[drop_code].name
        lines.append(f"\n🏆 **승리!** EXP +{exp_gain} | 💰 +{coin_gain}")
        if leveled:
            lines.append(f"🎉 **레벨 업! Lv.{p.level}**")
        if drop_item:
            lines.append(f"📦 드롭: {drop_item}")
        if q_msg:
            lines.append(q_msg)
        return {"win": True, "log": "\n".join(lines)}
    p.hp = max(1, p.hp)
    await save_player(p)
    lines.append("\n💀 **패배!** HP가 1로 유지됩니다.")
    return {"win": False, "log": "\n".join(lines)}


async def fight_pvp(p1: PlayerRecord, p2: PlayerRecord) -> dict:
    p1_hp, p2_hp = p1.hp, p2.hp
    logs = [f"⚔️ **{p1.username}** vs **{p2.username}** PVP 시작!"]
    for _ in range(30):
        d1, c1 = calc_damage(p1.attack, p2.defense, p1.crit)
        p2_hp -= d1
        logs.append(f"{'💥 ' if c1 else ''}**{p1.username}** → {d1} 데미지")
        if p2_hp <= 0:
            break
        d2, c2 = calc_damage(p2.attack, p1.defense, p2.crit)
        p1_hp -= d2
        logs.append(f"{'💥 ' if c2 else ''}**{p2.username}** → {d2} 데미지")
        if p1_hp <= 0:
            break
    winner = p1 if p2_hp <= 0 else p2
    loser = p2 if winner.user_id == p1.user_id else p1
    prize = min(500, loser.coins // 10)
    winner.coins += prize
    loser.coins = max(0, loser.coins - prize)
    winner.state["pvp_win"] = winner.state.get("pvp_win", 0) + 1
    winner.state["pvp_kill"] = winner.state.get("pvp_kill", 0) + 1
    loser.state["pvp_loss"] = loser.state.get("pvp_loss", 0) + 1
    await add_rank_score(winner.user_id, "pvp_kill", 1)
    await add_rank_score(winner.user_id, "pvp_win", 1)
    await save_player(winner)
    await save_player(loser)
    logs.append(f"\n🏆 **{winner.username}** 승리! 💰 +{prize}")
    return {"winner": winner.user_id, "log": "\n".join(logs)}


async def dungeon_fight(p: PlayerRecord, floor: int) -> dict:
    mob = MONSTERS[min(floor - 1, len(MONSTERS) - 1)]
    scaled = dict(mob)
    scaled["hp"] = mob["hp"] + floor * 30
    scaled["atk"] = mob["atk"] + floor * 5
    scaled["def"] = mob["def"] + floor * 2
    scaled["exp"] = mob["exp"] + floor * 20
    scaled["coins"] = mob["coins"] + floor * 15
    return await fight_monster(p, scaled)


async def dungeon_boss_fight(p: PlayerRecord) -> dict:
    boss = random.choice(BOSSES)
    result = await fight_monster(p, {**boss, "drop_rate": 0.9})
    if result["win"]:
        p.state["raid_count"] = p.state.get("raid_count", 0) + 1
        await save_player(p)
    return result


async def create_raid(boss_idx: int = 0) -> str:
    boss = BOSSES[boss_idx % len(BOSSES)]
    raid_id = uuid.uuid4().hex[:8]
    await execute("INSERT INTO raid_sessions (raid_id,boss_name,boss_hp,boss_max_hp,participants_json) VALUES (?,?,?,?,?)", (raid_id, boss["name"], boss["hp"], boss["hp"], j({})))
    return raid_id


async def join_raid(raid_id: str, p: PlayerRecord) -> Tuple[bool, str]:
    row = await fetch_one("SELECT * FROM raid_sessions WHERE raid_id=? AND state='active'", (raid_id,))
    if not row:
        return False, "레이드를 찾을 수 없습니다."
    participants = uj(row["participants_json"])
    if str(p.user_id) in participants:
        return False, "이미 참가 중입니다."
    participants[str(p.user_id)] = {"name": p.username, "dmg": 0}
    await execute("UPDATE raid_sessions SET participants_json=? WHERE raid_id=?", (j(participants), raid_id))
    return True, f"✅ 레이드 참가! 보스: {row['boss_name']} | HP: {row['boss_hp']:,}"


async def attack_raid(raid_id: str, p: PlayerRecord) -> Tuple[bool, str]:
    row = await fetch_one("SELECT * FROM raid_sessions WHERE raid_id=? AND state='active'", (raid_id,))
    if not row:
        return False, "레이드가 종료되었습니다."
    participants = uj(row["participants_json"])
    if str(p.user_id) not in participants:
        return False, "먼저 레이드에 참가하세요."
    dmg, crit = calc_damage(p.attack * 2, 20, p.crit)
    boss_hp = row["boss_hp"] - dmg
    participants[str(p.user_id)]["dmg"] += dmg
    msg = f"{'💥 치명타! ' if crit else ''}**{dmg}** 데미지! 보스 HP: {max(0, boss_hp):,}"
    if boss_hp <= 0:
        await execute("UPDATE raid_sessions SET state='clear',boss_hp=0,participants_json=? WHERE raid_id=?", (j(participants), raid_id))
        boss_data = next((b for b in BOSSES if b["name"] == row["boss_name"]), BOSSES[0])
        total_dmg = sum(v["dmg"] for v in participants.values())
        rewards = []
        for uid_str, data in participants.items():
            uid = int(uid_str)
            ratio = data["dmg"] / max(1, total_dmg)
            coin_reward = int(boss_data["coins"] * ratio)
            exp_reward = int(boss_data["exp"] * ratio)
            prow = await fetch_one("SELECT * FROM players WHERE user_id=?", (uid,))
            if prow:
                pr = PlayerRecord.from_row(prow)
                ensure_state_defaults(pr)
                pr.coins += coin_reward
                pr.state["raid_count"] = pr.state.get("raid_count", 0) + 1
                await add_exp(pr, exp_reward)
                await save_player(pr)
                rewards.append(f"  {data['name']}: 💰+{coin_reward:,} EXP+{exp_reward}")
        return True, msg + "\n🎉 **레이드 클리어!**\n" + "\n".join(rewards)
    await execute("UPDATE raid_sessions SET boss_hp=?,participants_json=? WHERE raid_id=?", (boss_hp, j(participants), raid_id))
    return True, msg


async def enchant_item(uid: int, inv_id: int) -> Tuple[bool, str]:
    row = await fetch_one("SELECT * FROM inventory_items WHERE id=? AND user_id=?", (inv_id, uid))
    if not row:
        return False, "아이템을 찾을 수 없습니다."
    lv = row["enchant_level"]
    if lv >= 15:
        return False, "최대 강화 수치(+15)입니다."
    cost = (lv + 1) * 200
    prow = await fetch_one("SELECT * FROM players WHERE user_id=?", (uid,))
    if not prow:
        return False, "플레이어를 찾을 수 없습니다."
    p = PlayerRecord.from_row(prow)
    if p.coins < cost:
        return False, f"코인 부족 (필요: {cost})"
    p.coins -= cost
    rate = max(10, 100 - lv * 7)
    if random.randint(1, 100) <= rate:
        await execute("UPDATE inventory_items SET enchant_level=?,power=power+15,defense=defense+8 WHERE id=?", (lv + 1, inv_id))
        await save_player(p)
        return True, f"✨ 강화 성공! (+{lv + 1}) 💰 -{cost}"
    await save_player(p)
    return False, f"❌ 강화 실패 (확률: {rate}%) 💰 -{cost}"


async def craft_item(p: PlayerRecord, item_code: str) -> Tuple[bool, str]:
    recipe = CRAFT_RECIPES.get(item_code)
    if not recipe:
        return False, "제작 레시피가 없습니다."
    if p.coins < recipe["코인"]:
        return False, f"코인 부족 (필요: {recipe['코인']:,})"
    for mat_code, qty in recipe["재료"].items():
        row = await fetch_one("SELECT qty FROM inventory_items WHERE user_id=? AND item_code=?", (p.user_id, mat_code))
        if not row or row["qty"] < qty:
            return False, f"재료 부족: {ITEM_CATALOG.get(mat_code, Item(mat_code, mat_code, '', '')).name} x{qty}"
    for mat_code, qty in recipe["재료"].items():
        await execute("UPDATE inventory_items SET qty=qty-? WHERE user_id=? AND item_code=?", (qty, p.user_id, mat_code))
        await execute("DELETE FROM inventory_items WHERE user_id=? AND item_code=? AND qty<=0", (p.user_id, mat_code))
    p.coins -= recipe["코인"]
    p.state["craft_count"] = p.state.get("craft_count", 0) + 1
    await save_player(p)
    await add_item(p.user_id, item_code)
    return True, f"⚒️ **{ITEM_CATALOG[item_code].name}** 제작 완료!"


_fishing_sessions: Dict[int, dict] = {}

async def start_fishing(uid: int) -> str:
    delay = random.uniform(3, 8)
    _fishing_sessions[uid] = {"fish_at": time.time() + delay}
    return f"🎣 낚싯대를 드리웠습니다... {delay:.1f}초 후 버튼이 반짝입니다!"


async def catch_fish(uid: int) -> Tuple[bool, str]:
    session = _fishing_sessions.get(uid)
    if not session:
        return False, "낚시 중이 아닙니다."
    now = time.time()
    if now < session["fish_at"]:
        return False, "⏳ 아직 입질이 없습니다!"
    if now > session["fish_at"] + 3:
        del _fishing_sessions[uid]
        return False, "🐟 놓쳤습니다!"
    del _fishing_sessions[uid]
    fish = random.choices(FISH_TABLE, weights=[50,25,12,7,4,2])[0]
    gain = random.randint(fish[1], fish[2])
    row = await fetch_one("SELECT * FROM players WHERE user_id=?", (uid,))
    if row:
        p = PlayerRecord.from_row(row)
        ensure_state_defaults(p)
        p.coins += gain
        p.state["fish_count"] = p.state.get("fish_count", 0) + 1
        await save_player(p)
        week = datetime.now(timezone.utc).strftime("%Y-W%U")
        old = await fetch_one("SELECT id,score FROM fishing_contest WHERE user_id=? AND season_week=?", (uid, week))
        if old:
            await execute("UPDATE fishing_contest SET score=score+? WHERE id=?", (gain, old["id"]))
        else:
            await execute("INSERT INTO fishing_contest (user_id,score,season_week) VALUES (?,?,?)", (uid, gain, week))
    q_msg = await check_quest_progress(uid, "fish") or ""
    return True, f"🎣 **{fish[0]}** 낚음! 💰 +{gain}\n{q_msg}".strip()


SLOT_SYMBOLS = ["🍒","🍋","🍊","🍇","⭐","💎","7️⃣"]
SLOT_WEIGHTS = [30,25,20,15,6,3,1]

async def play_slots(p: PlayerRecord, bet: int) -> Tuple[bool, str]:
    cd = check_cooldown("slots", p.user_id, 30)
    if cd:
        return False, f"⏳ 슬롯머신 쿨다운: {cd:.0f}초"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily = p.state.get("slots_today", {})
    if daily.get("date") == today and daily.get("count", 0) >= 10:
        return False, "오늘 슬롯머신 횟수(10회)를 모두 사용했습니다."
    if p.coins < bet:
        return False, "코인이 부족합니다."
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
    p.state["slots_today"] = {"date": today, "count": 1 if daily.get('date') != today else daily.get('count', 0) + 1}
    await save_player(p)
    return True, msg


async def play_dice(p: PlayerRecord, bet: int, guess: int) -> Tuple[bool, str]:
    cd = check_cooldown("dice", p.user_id, 10)
    if cd:
        return False, f"⏳ 주사위 쿨다운: {cd:.0f}초"
    if p.coins < bet:
        return False, "코인이 부족합니다."
    if guess < 1 or guess > 6:
        return False, "1~6 사이 숫자를 선택하세요."
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

async def list_auction(seller_id: int, inv_id: int, min_bid: int) -> Tuple[bool, str]:
    row = await fetch_one("SELECT * FROM inventory_items WHERE id=? AND user_id=?", (inv_id, seller_id))
    if not row:
        return False, "아이템을 찾을 수 없습니다."
    end_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    await execute("INSERT INTO auction_house (seller_id,item_json,min_bid,current_bid,end_at) VALUES (?,?,?,?,?)", (seller_id, j(dict(row)), min_bid, min_bid, end_at))
    await execute("DELETE FROM inventory_items WHERE id=?", (inv_id,))
    return True, f"📦 **{row['item_name']}** 거래소 등록 완료! 시작가: {min_bid:,}코인"


async def bid_auction(buyer: PlayerRecord, auction_id: int, bid: int) -> Tuple[bool, str]:
    row = await fetch_one("SELECT * FROM auction_house WHERE id=?", (auction_id,))
    if not row:
        return False, "매물을 찾을 수 없습니다."
    if datetime.fromisoformat(row["end_at"]) < datetime.now(timezone.utc):
        return False, "경매가 종료되었습니다."
    if bid <= row["current_bid"]:
        return False, f"현재 최고가({row['current_bid']:,})보다 높아야 합니다."
    if buyer.coins < bid:
        return False, "코인이 부족합니다."
    if row["highest_bidder"]:
        await execute("UPDATE players SET coins=coins+? WHERE user_id=?", (row["current_bid"], row["highest_bidder"]))
    buyer.coins -= bid
    buyer.state["trade_count"] = buyer.state.get("trade_count", 0) + 1
    await save_player(buyer)
    await execute("UPDATE auction_house SET current_bid=?,highest_bidder=? WHERE id=?", (bid, buyer.user_id, auction_id))
    item_data = uj(row["item_json"])
    return True, f"🏷️ **{item_data.get('item_name','?')}** 입찰 완료! {bid:,}코인"


async def set_auction_watch(uid: int, keyword: str) -> str:
    await set_rank_score(uid, f"watch_{keyword}", 1, season="watch")
    return f"🔔 '{keyword}' 알림이 등록되었습니다."


async def add_bp_exp(uid: int, exp: int) -> Optional[str]:
    row = await fetch_one("SELECT * FROM battlepass WHERE user_id=?", (uid,))
    if not row:
        await execute("INSERT INTO battlepass (user_id,season,exp) VALUES (?,?,?)", (uid, settings.season_number, exp))
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
            if row["premium"]:
                await add_item(uid, reward["premium"])
            msg = f"🎫 배틀패스 Lv.{new_lv} 달성!"
    await execute("UPDATE battlepass SET level=?,exp=? WHERE user_id=?", (new_lv, new_exp, uid))
    return msg


async def propose_marriage(p1: PlayerRecord, p2_id: int) -> Tuple[bool, str]:
    if p1.partner_id:
        return False, "이미 결혼한 상태입니다."
    row = await fetch_one("SELECT partner_id FROM players WHERE user_id=?", (p2_id,))
    if not row:
        return False, "상대방을 찾을 수 없습니다."
    if row["partner_id"]:
        return False, "상대방이 이미 결혼한 상태입니다."
    return True, "PENDING"


async def confirm_marriage(p1_id: int, p2_id: int) -> str:
    now = datetime.now(timezone.utc).isoformat()
    await execute("UPDATE players SET partner_id=? WHERE user_id=?", (p2_id, p1_id))
    await execute("UPDATE players SET partner_id=? WHERE user_id=?", (p1_id, p2_id))
    await execute("INSERT OR REPLACE INTO marriages (user_id,partner_id,married_at) VALUES (?,?,?)", (p1_id, p2_id, now))
    return "💍 결혼이 성사되었습니다! 축하합니다!"


async def propose_brotherhood(p1: PlayerRecord, p2_id: int) -> Tuple[bool, str]:
    if p1.brother_id:
        return False, "이미 의형제가 있습니다."
    row = await fetch_one("SELECT user_id FROM players WHERE user_id=?", (p2_id,))
    if not row:
        return False, "상대방을 찾을 수 없습니다."
    return True, "PENDING"


async def confirm_brotherhood(p1_id: int, p2_id: int) -> str:
    await execute("UPDATE players SET brother_id=? WHERE user_id=?", (p2_id, p1_id))
    await execute("UPDATE players SET brother_id=? WHERE user_id=?", (p1_id, p2_id))
    return "🤝 의형제를 맺었습니다!"


async def accept_quest(uid: int, quest_code: str) -> Tuple[bool, str]:
    if quest_code not in QUEST_DATA:
        return False, "존재하지 않는 퀘스트입니다."
    row = await fetch_one("SELECT id,completed FROM quests WHERE user_id=? AND quest_code=?", (uid, quest_code))
    if row:
        return False, "이미 진행 중이거나 완료한 퀘스트입니다."
    await execute("INSERT INTO quests (user_id,quest_code) VALUES (?,?)", (uid, quest_code))
    q = QUEST_DATA[quest_code]
    return True, f"📜 퀘스트 수락: **{q['name']}**\n{q['desc']}"


async def check_quest_progress(uid: int, quest_type: str, value: int = 1) -> Optional[str]:
    rows = await fetch_all("SELECT * FROM quests WHERE user_id=? AND completed=0", (uid,))
    msgs = []
    for row in rows:
        q = QUEST_DATA.get(row["quest_code"])
        if not q or q["type"] != quest_type:
            continue
        new_prog = row["progress"] + value
        if new_prog >= q["target"]:
            await execute("UPDATE quests SET progress=?,completed=1 WHERE id=?", (q["target"], row["id"]))
            prow = await fetch_one("SELECT * FROM players WHERE user_id=?", (uid,))
            if prow:
                pr = PlayerRecord.from_row(prow)
                pr.coins += q["reward_coins"]
                await add_exp(pr, q["reward_exp"])
                await add_item(uid, q["reward_item"])
                await save_player(pr)
            msgs.append(f"✅ 퀘스트 완료: **{q['name']}**! 💰+{q['reward_coins']} EXP+{q['reward_exp']}")
        else:
            await execute("UPDATE quests SET progress=? WHERE id=?", (new_prog, row["id"]))
    return "\n".join(msgs) if msgs else None


async def check_achievements(p: PlayerRecord) -> List[str]:
    ensure_state_defaults(p)
    earned = []
    checks = {
        "몬스터 헌터": p.state.get("kill_count", 0) >= 100,
        "탐험가": p.state.get("move_count", 0) >= 1000,
        "낚시왕": p.state.get("fish_count", 0) >= 100,
        "레이드 영웅": p.state.get("raid_count", 0) >= 10,
        "PVP 챔피언": p.state.get("pvp_win", 0) >= 50,
        "장인": p.state.get("craft_count", 0) >= 20,
        "부자": p.coins >= 1_000_000,
    }
    for title, cond in checks.items():
        if cond and title not in p.achievements:
            p.achievements.append(title)
            earned.append(title)
    if earned:
        await save_player(p)
    return earned


async def enter_house(p: PlayerRecord) -> Tuple[bool, str]:
    is_house_tile = (p.x + p.y) % 23 == 0
    row = await fetch_one("SELECT * FROM houses WHERE x=? AND y=?", (p.x, p.y))
    if not is_house_tile and not row:
        return False, "이 위치에 집이 없습니다."
    if row:
        if row["owner_id"] == p.user_id:
            furn = uj(row["furniture_json"])
            return True, f"🏠 **내 집에 입장했습니다!**\n가구: {', '.join(furn) if furn else '없음'}"
        owner = await fetch_one("SELECT username FROM players WHERE user_id=?", (row["owner_id"],))
        return True, f"🏠 **{owner['username'] if owner else '알 수 없음'}의 집에 방문했습니다!**"
    return True, "🏠 빈 집이 있습니다! 구매하시겠습니까? (💰 5,000코인)"


async def buy_house(p: PlayerRecord) -> Tuple[bool, str]:
    if p.coins < 5000:
        return False, "코인이 부족합니다. (필요: 5,000)"
    row = await fetch_one("SELECT * FROM houses WHERE x=? AND y=?", (p.x, p.y))
    if row:
        return False, "이미 누군가의 집입니다."
    p.coins -= 5000
    await execute("INSERT INTO houses (x,y,owner_id) VALUES (?,?,?)", (p.x, p.y, p.user_id))
    await save_player(p)
    return True, f"🏠 집을 구매했습니다! ({p.x},{p.y})"


async def rest_at_home(p: PlayerRecord) -> Tuple[bool, str]:
    row = await fetch_one("SELECT * FROM houses WHERE owner_id=?", (p.user_id,))
    if not row:
        return False, "소유한 집이 없습니다."
    p.hp = p.max_hp
    p.mp = p.max_mp
    p.stamina = p.max_stamina
    await save_player(p)
    return True, "🏠 집에서 휴식! HP/MP/스태미나 완전 회복!"

# ═══════════════════════════════════════════════════════════════════════
# UI - 공용 로비 / 메인 게임
# ═══════════════════════════════════════════════════════════════════════
class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="🗺️ 이동/탐험", style=discord.ButtonStyle.primary)
    async def help_move(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.send_message("**🗺️ 이동/탐험**\n• 방향 버튼으로 이동\n• NPC/집 상호작용\n• 미니맵 확인\n• /rpg는 자판기를 열고 실제 게임은 개인 창으로 열립니다.", ephemeral=True)

    @discord.ui.button(label="⚔️ 전투/PVP", style=discord.ButtonStyle.danger)
    async def help_battle(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.send_message("**⚔️ 전투/PVP**\n• 일반 사냥 / 던전 / 스킬 사용\n• PVP는 버튼으로 도전 요청 가능\n• PVP 킬 수는 랭킹보드에 기록됩니다.", ephemeral=True)

    @discord.ui.button(label="🎓 전직", style=discord.ButtonStyle.success)
    async def help_job(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.send_message("**🎓 전직**\n• 일반/히든/시크릿 전직 존재\n• 조건 충족 시 직접 전직\n• 이미 전직한 플레이어에게 배워서 전직 가능", ephemeral=True)


class BugReportModal(discord.ui.Modal, title="버그 제보"):
    bug = discord.ui.TextInput(label="버그 내용", style=discord.TextStyle.long, max_length=500)

    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await execute("INSERT INTO error_logs (guild_id,error_text) VALUES (?,?)", (str(i.guild_id) if i.guild_id else "DM", f"[{i.user}] {self.bug.value}"))
        if settings.bug_channel_id:
            ch = i.client.get_channel(settings.bug_channel_id)
            if ch:
                try:
                    await ch.send(f"🐛 **버그 제보** by {i.user.mention}\n{self.bug.value}")
                except Exception:
                    pass
        await i.followup.send("✅ 버그 제보가 접수되었습니다.", ephemeral=True)


class InviteRegisterModal(discord.ui.Modal, title="초대 코드 등록"):
    code = discord.ui.TextInput(label="초대 코드", max_length=20)

    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name, str(i.guild_id) if i.guild_id else None)
        if p.invited_by:
            await i.followup.send("이미 초대 코드를 등록했습니다.", ephemeral=True)
            return
        inviter = await fetch_one("SELECT * FROM players WHERE invite_code=?", (self.code.value.upper(),))
        if not inviter:
            await i.followup.send("유효하지 않은 초대 코드입니다.", ephemeral=True)
            return
        if inviter["user_id"] == p.user_id:
            await i.followup.send("자신의 초대 코드는 사용할 수 없습니다.", ephemeral=True)
            return
        p.invited_by = inviter["user_id"]
        p.coins += 500
        p.gems += 5
        await save_player(p)
        await execute("UPDATE players SET coins=coins+500, gems=gems+5 WHERE user_id=?", (inviter["user_id"],))
        await i.followup.send("✅ 초대 코드 등록 완료! 💰+500 💎+5 지급!", ephemeral=True)


class RPGLobbyView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="A1 게임 시작", style=discord.ButtonStyle.success, row=0)
    async def start_game(self, i: discord.Interaction, b: discord.ui.Button):
        p = await ensure_player(i.user.id, i.user.display_name, str(i.guild_id) if i.guild_id else None)
        if p.tutorial_step == 0:
            p.tutorial_step = 1
            await save_player(p)
        content = render_map(p)
        view = RPGMainView(self.cog, p.user_id)
        await i.response.send_message(
            "**🎮 개인 RPG 창이 열렸습니다.**\n이 창은 본인만 볼 수 있습니다.\n" + content,
            view=view,
            ephemeral=True,
        )
        await view.start_refresh(i)

    @discord.ui.button(label="A2 전체 랭킹", style=discord.ButtonStyle.primary, row=0)
    async def show_ranking(self, i: discord.Interaction, b: discord.ui.Button):
        rows = await fetch_all("SELECT username,level,coins FROM players ORDER BY level DESC, coins DESC LIMIT 10")
        lines = ["**🏆 글로벌 랭킹 (레벨 기준)**"]
        for idx, r in enumerate(rows, 1):
            lines.append(f"{idx}. **{r['username']}** | Lv.{r['level']} | 💰{r['coins']:,}")
        await i.response.send_message("\n".join(lines), ephemeral=True)

    @discord.ui.button(label="B1 PVP 킬보드", style=discord.ButtonStyle.danger, row=1)
    async def show_pvp_board(self, i: discord.Interaction, b: discord.ui.Button):
        rows = await get_leaderboard("pvp_kill", 10)
        if not rows:
            await i.response.send_message("아직 PVP 기록이 없습니다.", ephemeral=True)
            return
        lines = ["**⚔️ PVP 킬 랭킹보드**"]
        for idx, r in enumerate(rows, 1):
            lines.append(f"{idx}. **{r['username']}** | 킬 {r['score']} | Lv.{r['level']}")
        await i.response.send_message("\n".join(lines), ephemeral=True)

    @discord.ui.button(label="B2 도움말", style=discord.ButtonStyle.secondary, row=1)
    async def help_btn(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.send_message("❓ **RPG 자판기 도움말**", view=HelpView(), ephemeral=True)

    @discord.ui.button(label="C1 초대", style=discord.ButtonStyle.secondary, row=2)
    async def invite_btn(self, i: discord.Interaction, b: discord.ui.Button):
        p = await ensure_player(i.user.id, i.user.display_name, str(i.guild_id) if i.guild_id else None)
        if not p.invite_code:
            p.invite_code = uuid.uuid4().hex[:8].upper()
            await save_player(p)
        invited = await fetch_one("SELECT COUNT(*) as cnt FROM players WHERE invited_by=?", (p.user_id,))
        await i.response.send_message(
            f"**🎟️ 초대 코드: `{p.invite_code}`**\n초대한 친구 수: {invited['cnt'] if invited else 0}명\n친구는 버튼의 코드 등록으로 입력 가능!",
            ephemeral=True,
            view=InviteLobbyView(),
        )

    @discord.ui.button(label="C2 버그 제보", style=discord.ButtonStyle.secondary, row=2)
    async def bug_btn(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.send_modal(BugReportModal())


class InviteLobbyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="초대 코드 등록", style=discord.ButtonStyle.primary)
    async def register(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.send_modal(InviteRegisterModal())


class RPGMainView(discord.ui.View):
    def __init__(self, cog, uid: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.uid = uid

    async def start_refresh(self, interaction: discord.Interaction):
        row = await fetch_one("SELECT auto_refresh FROM player_settings WHERE user_id=?", (self.uid,))
        if row and row["auto_refresh"] and not self.bot_refresh.is_running():
            self.bot_refresh.start(interaction)

    @tasks.loop(seconds=5)
    async def bot_refresh(self, interaction: discord.Interaction):
        try:
            p = await ensure_player(self.uid, interaction.user.display_name)
            await interaction.edit_original_response(content="**🎮 개인 RPG 창이 열렸습니다.**\n이 창은 본인만 볼 수 있습니다.\n" + render_map(p), view=self)
        except Exception:
            if self.bot_refresh.is_running():
                self.bot_refresh.cancel()

    async def _check(self, i: discord.Interaction) -> bool:
        if i.user.id != self.uid:
            await i.response.send_message("자신의 캐릭터만 조작 가능합니다!", ephemeral=True)
            return False
        return True

    async def _refresh_map(self, i: discord.Interaction, extra: str = ""):
        p = await ensure_player(i.user.id, i.user.display_name)
        content = "**🎮 개인 RPG 창이 열렸습니다.**\n이 창은 본인만 볼 수 있습니다.\n" + render_map(p)
        if extra:
            content += f"\n{extra}"
        await i.edit_original_response(content=content, view=self)

    async def _do_move(self, i: discord.Interaction, direction: str):
        await i.response.defer()
        p = await ensure_player(i.user.id, i.user.display_name)
        if p.stamina <= 0:
            await i.followup.send("⚡ 스태미나가 부족합니다!", ephemeral=True)
            return
        ok, msg = await try_move(p, direction)
        await self._refresh_map(i)
        if msg:
            await i.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="↑", style=discord.ButtonStyle.primary, row=0)
    async def move_up(self, i: discord.Interaction, b: discord.ui.Button):
        if await self._check(i):
            await self._do_move(i, "W")

    @discord.ui.button(label="←", style=discord.ButtonStyle.primary, row=1)
    async def move_left(self, i: discord.Interaction, b: discord.ui.Button):
        if await self._check(i):
            await self._do_move(i, "A")

    @discord.ui.button(label="↓", style=discord.ButtonStyle.primary, row=1)
    async def move_down(self, i: discord.Interaction, b: discord.ui.Button):
        if await self._check(i):
            await self._do_move(i, "S")

    @discord.ui.button(label="→", style=discord.ButtonStyle.primary, row=1)
    async def move_right(self, i: discord.Interaction, b: discord.ui.Button):
        if await self._check(i):
            await self._do_move(i, "D")

    @discord.ui.button(label="🗺️ 미니맵", style=discord.ButtonStyle.secondary, row=0)
    async def minimap(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i):
            return
        p = await ensure_player(i.user.id, i.user.display_name)
        await i.response.send_message(render_minimap(p), ephemeral=True)

    @discord.ui.button(label="🏠 상호작용", style=discord.ButtonStyle.secondary, row=0)
    async def interact(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i):
            return
        p = await ensure_player(i.user.id, i.user.display_name)
        for npc_key, npc in NPCS.items():
            if abs(p.x - npc["x"]) <= 1 and abs(p.y - npc["y"]) <= 1:
                await i.response.send_message(f"💬 **{npc['name']}**: {npc['dialogue']}", view=NPCView(self.cog, p.user_id, npc_key, npc), ephemeral=True)
                return
        ok, msg = await enter_house(p)
        if ok and "입장" in msg:
            p.state["in_house"] = True
            await save_player(p)
            await self._refresh_map(i)
            await i.followup.send(msg, view=HouseView(self.cog, p.user_id), ephemeral=True)
            return
        await i.response.send_message(msg, view=HouseView(self.cog, p.user_id) if ok else None, ephemeral=True)

    @discord.ui.button(label="⚔️ 전투", style=discord.ButtonStyle.danger, row=2)
    async def battle(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i):
            return
        p = await ensure_player(i.user.id, i.user.display_name)
        if p.hp <= 0:
            await i.response.send_message("HP가 없습니다! 회복하세요.", ephemeral=True)
            return
        await i.response.send_message("⚔️ 전투 유형을 선택하세요:", view=BattleSelectView(self.cog, p.user_id), ephemeral=True)

    @discord.ui.button(label="🎒 인벤토리", style=discord.ButtonStyle.success, row=2)
    async def inventory(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i):
            return
        rows = await fetch_all("SELECT * FROM inventory_items WHERE user_id=? LIMIT 20", (self.uid,))
        if not rows:
            await i.response.send_message("인벤토리가 비어있습니다.", ephemeral=True)
            return
        lines = ["**🎒 인벤토리**"]
        for r in rows:
            ench = f" (+{r['enchant_level']})" if r["enchant_level"] > 0 else ""
            lines.append(f"`ID:{r['id']}` {r['item_name']}{ench} x{r['qty']}")
        await i.response.send_message("\n".join(lines), view=InventoryActionView(self.cog, self.uid), ephemeral=True)

    @discord.ui.button(label="📊 스탯", style=discord.ButtonStyle.success, row=2)
    async def stat(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i):
            return
        p = await ensure_player(i.user.id, i.user.display_name)
        title = f"[{p.title}] " if p.title else ""
        txt = (
            f"**{title}{p.username}의 스탯**\n"
            f"직업: {p.job} | Lv.{p.level} | EXP: {p.exp}/{p.level*100}\n"
            f"❤️ HP: {p.hp}/{p.max_hp} | 💙 MP: {p.mp}/{p.max_mp}\n"
            f"⚔️ 공격: {p.attack} | 🛡️ 방어: {p.defense} | 🎯 치명타: {p.crit}%\n"
            f"💰 코인: {p.coins:,} | 💎 젬: {p.gems}\n"
            f"⚔️ PVP 킬: {p.state.get('pvp_kill',0)} | 승: {p.state.get('pvp_win',0)} | 패: {p.state.get('pvp_loss',0)}\n"
            f"🏆 업적: {', '.join(p.achievements) if p.achievements else '없음'}"
        )
        await i.response.send_message(txt, view=StatMenuView(self.cog, self.uid), ephemeral=True)

    @discord.ui.button(label="🎮 미니게임", style=discord.ButtonStyle.secondary, row=3)
    async def minigame(self, i: discord.Interaction, b: discord.ui.Button):
        if await self._check(i):
            await i.response.send_message("🎮 미니게임을 선택하세요:", view=MinigameView(self.cog, self.uid), ephemeral=True)

    @discord.ui.button(label="🏪 거래소", style=discord.ButtonStyle.secondary, row=3)
    async def auction(self, i: discord.Interaction, b: discord.ui.Button):
        if await self._check(i):
            await i.response.send_message("🏪 거래소 메뉴", view=AuctionView(self.cog, self.uid), ephemeral=True)

    @discord.ui.button(label="🐲 레이드", style=discord.ButtonStyle.danger, row=4)
    async def raid_btn(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i):
            return
        raid_id = await create_raid(random.randint(0, len(BOSSES)-1))
        boss = BOSSES[0]
        await i.response.send_message(
            f"🐲 **레이드 보스 등장!**\n보스: {boss['name']}\nHP: {boss['hp']:,}\n레이드 ID: `{raid_id}`",
            view=RaidView(self.cog, self.uid, raid_id), ephemeral=True,
        )

    @discord.ui.button(label="🤝 소셜", style=discord.ButtonStyle.success, row=4)
    async def social(self, i: discord.Interaction, b: discord.ui.Button):
        if await self._check(i):
            await i.response.send_message("🤝 소셜 메뉴", view=SocialMenuView(self.cog, self.uid), ephemeral=True)

    @discord.ui.button(label="❓ 도움말", style=discord.ButtonStyle.secondary, row=4)
    async def help_btn(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.send_message("❓ 도움말", view=HelpView(), ephemeral=True)

class BattleSelectView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=120)
        self.cog = cog
        self.uid = uid

    @discord.ui.button(label="🐺 일반 사냥", style=discord.ButtonStyle.danger)
    async def hunt(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        result = await fight_monster(p, random.choice(MONSTERS))
        bp_msg = await add_bp_exp(p.user_id, 5)
        ach = await check_achievements(p)
        extra = []
        if bp_msg: extra.append(bp_msg)
        if ach: extra.append("🏆 새 업적: " + ", ".join(ach))
        await i.followup.send(result["log"] + ("\n" + "\n".join(extra) if extra else ""), ephemeral=True)

    @discord.ui.button(label="🏰 던전", style=discord.ButtonStyle.danger)
    async def dungeon(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        floor = p.state.get("dungeon_floor", 1)
        result = await dungeon_fight(p, floor)
        if result["win"]:
            if floor >= 5:
                boss = await dungeon_boss_fight(p)
                p.state["dungeon_floor"] = 1
                await save_player(p)
                await i.followup.send(result["log"] + "\n\n" + boss["log"], ephemeral=True)
            else:
                p.state["dungeon_floor"] = floor + 1
                await save_player(p)
                await i.followup.send(result["log"] + f"\n\n🏰 다음 층: **{floor+1}층**", ephemeral=True)
        else:
            p.state["dungeon_floor"] = 1
            await save_player(p)
            await i.followup.send(result["log"], ephemeral=True)

    @discord.ui.button(label="⚡ 스킬 사용", style=discord.ButtonStyle.primary)
    async def skill(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        p = await ensure_player(i.user.id, i.user.display_name)
        skills = SKILL_TREE.get(job_skill_key(p.job), {})
        if not skills:
            await i.response.send_message("현재 직업에 사용할 수 있는 스킬이 없습니다.", ephemeral=True)
            return
        await i.response.send_message("⚡ 스킬을 선택하세요:", view=SkillView(self.cog, self.uid, skills), ephemeral=True)

    @discord.ui.button(label="⚔️ PVP", style=discord.ButtonStyle.secondary)
    async def pvp(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        await i.response.send_message("⚔️ PVP 메뉴", view=PVPMenuView(self.cog, self.uid), ephemeral=True)


class SkillView(discord.ui.View):
    def __init__(self, cog, uid, skills: dict):
        super().__init__(timeout=120)
        self.cog = cog
        self.uid = uid
        for code, sk in list(skills.items())[:5]:
            btn = discord.ui.Button(label=sk["name"][:20], style=discord.ButtonStyle.primary)
            btn.callback = self._make_cb(code, sk)
            self.add_item(btn)

    def _make_cb(self, code, sk):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid:
                return
            await i.response.defer(ephemeral=True)
            p = await ensure_player(i.user.id, i.user.display_name)
            if p.mp < sk.get("mp", 0):
                await i.followup.send(f"MP 부족! (필요: {sk['mp']})", ephemeral=True)
                return
            p.mp -= sk.get("mp", 0)
            mob = random.choice(MONSTERS)
            dmg = int(max(1, p.attack * sk.get("mult", 1.0) - mob["def"]))
            mob_hp = mob["hp"] - dmg
            heal = sk.get("heal", 0)
            if heal:
                p.hp = min(p.max_hp, p.hp + heal)
            msg = f"⚡ **{sk['name']}** 발동!\n{mob['name']}에게 **{dmg}** 데미지!"
            if heal:
                msg += f"\n💚 HP +{heal}"
            if mob_hp <= 0:
                p.coins += mob["coins"]
                await add_exp(p, mob["exp"])
                msg += f"\n🏆 처치! 💰+{mob['coins']}"
            await save_player(p)
            await i.followup.send(msg, ephemeral=True)
        return cb


class InventoryActionView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=120)
        self.cog = cog
        self.uid = uid

    @discord.ui.button(label="💊 포션 사용", style=discord.ButtonStyle.success)
    async def use_potion(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        row = await fetch_one("SELECT * FROM inventory_items WHERE user_id=? AND item_type='소비' ORDER BY id LIMIT 1", (p.user_id,))
        if not row:
            await i.followup.send("사용 가능한 소비 아이템이 없습니다.", ephemeral=True)
            return
        item = ITEM_CATALOG.get(row["item_code"])
        if item and item.meta.get("heal"):
            p.hp = min(p.max_hp, p.hp + item.meta["heal"])
        elif item and item.meta.get("mp_restore"):
            p.mp = min(p.max_mp, p.mp + item.meta["mp_restore"])
        elif item and item.meta.get("teleport") == "town":
            p.x, p.y = 100, 100
            p.biome = _get_biome(100, 100)
        await save_player(p)
        if row["qty"] <= 1:
            await execute("DELETE FROM inventory_items WHERE id=?", (row["id"],))
        else:
            await execute("UPDATE inventory_items SET qty=qty-1 WHERE id=?", (row["id"],))
        await i.followup.send(f"✅ {row['item_name']} 사용!", ephemeral=True)

    @discord.ui.button(label="💎 강화", style=discord.ButtonStyle.primary)
    async def enchant(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        rows = await fetch_all("SELECT * FROM inventory_items WHERE user_id=? AND item_type IN ('무기','방어구') LIMIT 5", (i.user.id,))
        if not rows:
            await i.response.send_message("강화할 장비가 없습니다.", ephemeral=True)
            return
        await i.response.send_message("💎 강화할 아이템을 선택하세요:", view=EnchantSelectView(self.cog, self.uid, rows), ephemeral=True)

    @discord.ui.button(label="⚔️ 장착", style=discord.ButtonStyle.primary)
    async def equip(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        rows = await fetch_all("SELECT * FROM inventory_items WHERE user_id=? AND item_type IN ('무기','방어구') LIMIT 10", (i.user.id,))
        if not rows:
            await i.response.send_message("장착할 장비가 없습니다.", ephemeral=True)
            return
        await i.response.send_message("⚔️ 장착할 아이템을 선택하세요:", view=EquipSelectView(self.cog, self.uid, rows), ephemeral=True)

    @discord.ui.button(label="🏪 거래소 등록", style=discord.ButtonStyle.secondary)
    async def list_item(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        rows = await fetch_all("SELECT * FROM inventory_items WHERE user_id=? LIMIT 5", (i.user.id,))
        if not rows:
            await i.response.send_message("등록할 아이템이 없습니다.", ephemeral=True)
            return
        await i.response.send_message("📦 거래소에 등록할 아이템을 선택하세요:", view=AuctionListView(self.cog, self.uid, rows), ephemeral=True)


class EquipSelectView(discord.ui.View):
    def __init__(self, cog, uid, rows):
        super().__init__(timeout=120)
        self.uid = uid
        for row in rows:
            btn = discord.ui.Button(label=row["item_name"][:20], style=discord.ButtonStyle.secondary)
            btn.callback = self._make_cb(row)
            self.add_item(btn)

    def _make_cb(self, item_row):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid:
                return
            await i.response.defer(ephemeral=True)
            p = await ensure_player(i.user.id, i.user.display_name)
            slot = "weapon" if item_row["item_type"] == "무기" else "armor"
            old_code = p.equipment.get(slot)
            if old_code:
                old = ITEM_CATALOG.get(old_code)
                if old:
                    p.attack -= old.power
                    p.defense -= old.defense
            p.equipment[slot] = item_row["item_code"]
            p.attack += item_row["power"]
            p.defense += item_row["defense"]
            await save_player(p)
            await i.followup.send(f"✅ **{item_row['item_name']}** 장착 완료!", ephemeral=True)
        return cb


class EnchantSelectView(discord.ui.View):
    def __init__(self, cog, uid, rows):
        super().__init__(timeout=120)
        self.uid = uid
        for row in rows[:5]:
            ench = f"+{row['enchant_level']}" if row["enchant_level"] > 0 else ""
            btn = discord.ui.Button(label=f"{row['item_name'][:15]}{ench}", style=discord.ButtonStyle.primary)
            btn.callback = self._make_cb(row["id"])
            self.add_item(btn)

    def _make_cb(self, inv_id):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid:
                return
            await i.response.defer(ephemeral=True)
            ok, msg = await enchant_item(i.user.id, inv_id)
            await i.followup.send(msg, ephemeral=True)
        return cb


class AuctionPriceModal(discord.ui.Modal, title="거래소 등록 가격 설정"):
    price = discord.ui.TextInput(label="시작 가격 (코인)", placeholder="예: 1000", min_length=1, max_length=10)
    def __init__(self, uid, inv_id):
        super().__init__()
        self.uid = uid
        self.inv_id = inv_id
    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        try:
            min_bid = int(self.price.value)
        except ValueError:
            await i.followup.send("올바른 숫자를 입력하세요.", ephemeral=True)
            return
        ok, msg = await list_auction(self.uid, self.inv_id, min_bid)
        await i.followup.send(msg, ephemeral=True)


class AuctionListView(discord.ui.View):
    def __init__(self, cog, uid, rows):
        super().__init__(timeout=120)
        self.uid = uid
        for row in rows[:5]:
            btn = discord.ui.Button(label=row["item_name"][:20], style=discord.ButtonStyle.secondary)
            btn.callback = self._make_cb(row["id"])
            self.add_item(btn)
    def _make_cb(self, inv_id):
        async def cb(i: discord.Interaction):
            if i.user.id == self.uid:
                await i.response.send_modal(AuctionPriceModal(self.uid, inv_id))
        return cb


class BidModal(discord.ui.Modal, title="입찰"):
    bid = discord.ui.TextInput(label="입찰 금액 (코인)", min_length=1, max_length=10)
    def __init__(self, uid, auction_id):
        super().__init__()
        self.uid = uid
        self.auction_id = auction_id
    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        try:
            bid = int(self.bid.value)
        except ValueError:
            await i.followup.send("올바른 숫자를 입력하세요.", ephemeral=True)
            return
        p = await ensure_player(i.user.id, i.user.display_name)
        ok, msg = await bid_auction(p, self.auction_id, bid)
        await i.followup.send(msg, ephemeral=True)


class AuctionBidView(discord.ui.View):
    def __init__(self, cog, uid, rows):
        super().__init__(timeout=120)
        self.uid = uid
        for row in rows[:5]:
            item = uj(row["item_json"])
            btn = discord.ui.Button(label=f"입찰: {item.get('item_name','?')[:15]}", style=discord.ButtonStyle.danger)
            btn.callback = self._make_cb(row["id"])
            self.add_item(btn)
    def _make_cb(self, auction_id):
        async def cb(i: discord.Interaction):
            if i.user.id == self.uid:
                await i.response.send_modal(BidModal(self.uid, auction_id))
        return cb


class AuctionWatchModal(discord.ui.Modal, title="거래소 알림 설정"):
    keyword = discord.ui.TextInput(label="아이템 키워드", max_length=20)
    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await i.followup.send(await set_auction_watch(i.user.id, self.keyword.value), ephemeral=True)


class AuctionView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=120)
        self.uid = uid

    @discord.ui.button(label="📋 매물 목록", style=discord.ButtonStyle.primary)
    async def list_items(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        now = datetime.now(timezone.utc).isoformat()
        rows = await fetch_all("SELECT * FROM auction_house WHERE end_at>? ORDER BY id DESC LIMIT 10", (now,))
        if not rows:
            await i.response.send_message("현재 거래소에 매물이 없습니다.", ephemeral=True)
            return
        lines = ["**🏪 거래소 매물**"]
        for r in rows:
            item = uj(r["item_json"])
            lines.append(f"`ID:{r['id']}` {item.get('item_name','?')} | 현재가: {r['current_bid']:,}💰")
        await i.response.send_message("\n".join(lines), view=AuctionBidView(None, self.uid, rows), ephemeral=True)

    @discord.ui.button(label="🔔 알림 설정", style=discord.ButtonStyle.secondary)
    async def watch(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id == self.uid:
            await i.response.send_modal(AuctionWatchModal())


class AppearanceModal(discord.ui.Modal, title="외형 커스터마이징"):
    color = discord.ui.TextInput(label="캐릭터 색상", max_length=20)
    accessory = discord.ui.TextInput(label="악세서리", max_length=20, required=False)
    def __init__(self, uid):
        super().__init__()
        self.uid = uid
    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        p.appearance = {"color": self.color.value, "accessory": self.accessory.value or "없음"}
        await save_player(p)
        await i.followup.send(f"🎨 외형 변경 완료!\n색상: {self.color.value} | 악세서리: {self.accessory.value or '없음'}", ephemeral=True)


class TitleSelectView(discord.ui.View):
    def __init__(self, cog, uid, titles: list):
        super().__init__(timeout=120)
        self.uid = uid
        for title in titles[:8]:
            btn = discord.ui.Button(label=title, style=discord.ButtonStyle.secondary)
            btn.callback = self._make_cb(title)
            self.add_item(btn)
    def _make_cb(self, title):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid:
                return
            await i.response.defer(ephemeral=True)
            p = await ensure_player(i.user.id, i.user.display_name)
            p.title = title
            await save_player(p)
            await i.followup.send(f"🏷️ 칭호를 **[{title}]** 으로 변경했습니다!", ephemeral=True)
        return cb


class TeachJobModal(discord.ui.Modal, title="플레이어에게 배우기"):
    teacher_id = discord.ui.TextInput(label="스승 유저 ID", max_length=20)
    def __init__(self, cog, student_uid):
        super().__init__()
        self.cog = cog
        self.student_uid = student_uid
    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        try:
            tid = int(self.teacher_id.value)
        except ValueError:
            await i.followup.send("올바른 유저 ID를 입력하세요.", ephemeral=True)
            return
        teacher_row = await fetch_one("SELECT * FROM players WHERE user_id=?", (tid,))
        if not teacher_row:
            await i.followup.send("해당 스승을 찾을 수 없습니다.", ephemeral=True)
            return
        teacher = PlayerRecord.from_row(teacher_row)
        if teacher.job == "초보자":
            await i.followup.send("스승이 아직 전직하지 않았습니다.", ephemeral=True)
            return
        req_id = uuid.uuid4().hex[:8]
        self.cog._teach_requests[req_id] = {"teacher_id": tid, "student_id": self.student_uid, "job": teacher.job, "channel_id": i.channel_id}
        await i.followup.send(f"전직 전수 요청이 생성되었습니다. <@{tid}> 님이 수락하면 **{teacher.job}** 을 배울 수 있습니다.", ephemeral=True)
        await i.channel.send(f"🎓 <@{tid}> 님, <@{self.student_uid}> 님이 **{teacher.job}** 전직 전수를 요청했습니다!", view=TeachJobConfirmView(self.cog, req_id))


class JobCenterView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=120)
        self.cog = cog
        self.uid = uid

    @discord.ui.button(label="일반 전직", style=discord.ButtonStyle.primary)
    async def normal(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        p = await ensure_player(i.user.id, i.user.display_name)
        await i.response.send_message("🎓 일반 전직", view=JobSelectView(self.cog, self.uid, "일반"), ephemeral=True)

    @discord.ui.button(label="히든 전직", style=discord.ButtonStyle.success)
    async def hidden(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        await i.response.send_message("🕵️ 히든 전직", view=JobSelectView(self.cog, self.uid, "히든"), ephemeral=True)

    @discord.ui.button(label="시크릿 전직", style=discord.ButtonStyle.danger)
    async def secret(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        await i.response.send_message("🔐 시크릿 전직", view=JobSelectView(self.cog, self.uid, "시크릿"), ephemeral=True)

    @discord.ui.button(label="플레이어에게 배우기", style=discord.ButtonStyle.secondary)
    async def learn_from_player(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id == self.uid:
            await i.response.send_modal(TeachJobModal(self.cog, self.uid))


class JobSelectView(discord.ui.View):
    def __init__(self, cog, uid, tier: str):
        super().__init__(timeout=120)
        self.cog = cog
        self.uid = uid
        self.tier = tier
        for name, info in JOB_DATA.items():
            if info.get("tier") != tier:
                continue
            btn = discord.ui.Button(label=name[:20], style=discord.ButtonStyle.primary if tier == "일반" else discord.ButtonStyle.secondary)
            btn.callback = self._make_cb(name)
            self.add_item(btn)
    def _make_cb(self, job_name):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid:
                return
            await i.response.defer(ephemeral=True)
            p = await ensure_player(i.user.id, i.user.display_name)
            ok, reason = check_job_requirements(p, job_name)
            if not ok:
                await i.followup.send(f"❌ {job_name} 전직 불가: {reason}", ephemeral=True)
                return
            ok2, msg = await apply_job_change(p, job_name)
            await i.followup.send(msg, ephemeral=True)
        return cb


class StatMenuView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=120)
        self.cog = cog
        self.uid = uid

    @discord.ui.button(label="🎓 전직 센터", style=discord.ButtonStyle.primary)
    async def change_job(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id == self.uid:
            await i.response.send_message("🎓 전직 센터", view=JobCenterView(self.cog, self.uid), ephemeral=True)

    @discord.ui.button(label="🏷️ 칭호 변경", style=discord.ButtonStyle.secondary)
    async def change_title(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        p = await ensure_player(i.user.id, i.user.display_name)
        if not p.achievements:
            await i.response.send_message("획득한 칭호가 없습니다.", ephemeral=True)
            return
        await i.response.send_message("🏷️ 칭호를 선택하세요:", view=TitleSelectView(self.cog, self.uid, p.achievements), ephemeral=True)

    @discord.ui.button(label="🎨 외형 변경", style=discord.ButtonStyle.secondary)
    async def appearance(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id == self.uid:
            await i.response.send_modal(AppearanceModal(self.uid))

    @discord.ui.button(label="📜 퀘스트", style=discord.ButtonStyle.success)
    async def quests(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        rows = await fetch_all("SELECT * FROM quests WHERE user_id=? AND completed=0", (self.uid,))
        if not rows:
            await i.response.send_message("진행 중인 퀘스트가 없습니다.", ephemeral=True)
            return
        lines = ["**📜 진행 중인 퀘스트**"]
        for r in rows:
            q = QUEST_DATA.get(r["quest_code"], {})
            lines.append(f"• **{q.get('name', r['quest_code'])}**: {r['progress']}/{q.get('target', 1)}")
        await i.response.send_message("\n".join(lines), ephemeral=True)

    @discord.ui.button(label="🎫 배틀패스", style=discord.ButtonStyle.success)
    async def battlepass(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        row = await fetch_one("SELECT * FROM battlepass WHERE user_id=?", (self.uid,))
        if not row:
            await i.response.send_message("배틀패스 정보가 없습니다.", ephemeral=True)
            return
        premium = "✅ 프리미엄" if row["premium"] else "❌ 무료"
        lines = [f"**🎫 시즌 {row['season']} 배틀패스**", f"등급: {premium} | Lv.{row['level']} | EXP: {row['exp']}/100"]
        await i.response.send_message("\n".join(lines), ephemeral=True)


class RelationTargetModal(discord.ui.Modal):
    target_id = discord.ui.TextInput(label="상대 유저 ID", max_length=20)
    def __init__(self, cog, proposer_id: int, mode: str):
        super().__init__(title="결혼 제안" if mode == "marry" else "의형제 제안")
        self.cog = cog
        self.proposer_id = proposer_id
        self.mode = mode
    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        try:
            tid = int(self.target_id.value)
        except ValueError:
            await i.followup.send("올바른 유저 ID를 입력하세요.", ephemeral=True)
            return
        p = await ensure_player(self.proposer_id, i.user.display_name)
        if self.mode == "marry":
            ok, msg = await propose_marriage(p, tid)
            if not ok:
                await i.followup.send(msg, ephemeral=True)
                return
            await i.channel.send(f"💍 <@{self.proposer_id}> 님이 <@{tid}> 님에게 결혼을 제안했습니다!", view=MarriageConfirmView(self.proposer_id, tid))
        else:
            ok, msg = await propose_brotherhood(p, tid)
            if not ok:
                await i.followup.send(msg, ephemeral=True)
                return
            await i.channel.send(f"🤝 <@{self.proposer_id}> 님이 <@{tid}> 님에게 의형제를 제안했습니다!", view=BrotherhoodConfirmView(self.proposer_id, tid))
        await i.followup.send("요청을 보냈습니다. 상대방이 버튼으로 응답할 수 있습니다.", ephemeral=True)


class PVPChallengeModal(discord.ui.Modal, title="PVP 도전"):
    target_id = discord.ui.TextInput(label="상대 유저 ID", max_length=20)
    def __init__(self, cog, challenger_id):
        super().__init__()
        self.cog = cog
        self.challenger_id = challenger_id
    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        try:
            tid = int(self.target_id.value)
        except ValueError:
            await i.followup.send("올바른 유저 ID를 입력하세요.", ephemeral=True)
            return
        if tid == self.challenger_id:
            await i.followup.send("자기 자신에게 도전할 수 없습니다.", ephemeral=True)
            return
        row = await fetch_one("SELECT user_id FROM players WHERE user_id=?", (tid,))
        if not row:
            await i.followup.send("상대 플레이어를 찾을 수 없습니다. 먼저 게임을 시작한 유저여야 합니다.", ephemeral=True)
            return
        cid = uuid.uuid4().hex[:8]
        self.cog._pvp_requests[cid] = {"challenger_id": self.challenger_id, "target_id": tid, "channel_id": i.channel_id}
        await i.followup.send("PVP 도전 요청을 보냈습니다.", ephemeral=True)
        await i.channel.send(f"⚔️ <@{self.challenger_id}> 님이 <@{tid}> 님에게 PVP를 신청했습니다!", view=PVPConfirmView(self.cog, cid))


class PVPMenuView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=120)
        self.cog = cog
        self.uid = uid
    @discord.ui.button(label="⚔️ 플레이어 도전", style=discord.ButtonStyle.danger)
    async def challenge(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id == self.uid:
            await i.response.send_modal(PVPChallengeModal(self.cog, self.uid))
    @discord.ui.button(label="🏆 PVP 킬 랭킹", style=discord.ButtonStyle.secondary)
    async def pvp_rank(self, i: discord.Interaction, b: discord.ui.Button):
        rows = await get_leaderboard("pvp_kill", 10)
        if not rows:
            await i.response.send_message("아직 PVP 기록이 없습니다.", ephemeral=True)
            return
        lines = ["**⚔️ PVP 킬 랭킹보드**"]
        for idx, r in enumerate(rows, 1):
            lines.append(f"{idx}. **{r['username']}** | 킬 {r['score']} | Lv.{r['level']}")
        await i.response.send_message("\n".join(lines), ephemeral=True)


class SocialMenuView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=120)
        self.cog = cog
        self.uid = uid
    @discord.ui.button(label="💍 결혼 제안", style=discord.ButtonStyle.primary)
    async def marry(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id == self.uid:
            await i.response.send_modal(RelationTargetModal(self.cog, self.uid, "marry"))
    @discord.ui.button(label="🤝 의형제 제안", style=discord.ButtonStyle.primary)
    async def brotherhood(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id == self.uid:
            await i.response.send_modal(RelationTargetModal(self.cog, self.uid, "brother"))
    @discord.ui.button(label="👋 인사", style=discord.ButtonStyle.secondary)
    async def wave(self, i: discord.Interaction, b: discord.ui.Button):
        p = await ensure_player(self.uid, i.user.display_name)
        await i.response.send_message(f"👋 **{p.username}**이(가) 인사를 합니다!")
    @discord.ui.button(label="💃 춤", style=discord.ButtonStyle.secondary)
    async def dance(self, i: discord.Interaction, b: discord.ui.Button):
        p = await ensure_player(self.uid, i.user.display_name)
        await i.response.send_message(f"💃 **{p.username}**이(가) 신나게 춤을 춥니다!")
    @discord.ui.button(label="🎟️ 초대/등록", style=discord.ButtonStyle.secondary)
    async def invite(self, i: discord.Interaction, b: discord.ui.Button):
        p = await ensure_player(self.uid, i.user.display_name)
        if not p.invite_code:
            p.invite_code = uuid.uuid4().hex[:8].upper()
            await save_player(p)
        invited_count = await fetch_one("SELECT COUNT(*) as cnt FROM players WHERE invited_by=?", (p.user_id,))
        await i.response.send_message(f"**🎟️ 초대 코드: `{p.invite_code}`**\n초대한 친구 수: {invited_count['cnt'] if invited_count else 0}명", view=InviteLobbyView(), ephemeral=True)
    @discord.ui.button(label="⚔️ PVP 랭킹", style=discord.ButtonStyle.danger)
    async def pvp_rank(self, i: discord.Interaction, b: discord.ui.Button):
        await PVPMenuView(self.cog, self.uid).pvp_rank(i, b)


class QuestListView(discord.ui.View):
    def __init__(self, cog, uid, quest_codes):
        super().__init__(timeout=120)
        self.uid = uid
        for qc in quest_codes[:5]:
            btn = discord.ui.Button(label=QUEST_DATA.get(qc, {}).get("name", qc)[:20], style=discord.ButtonStyle.primary)
            btn.callback = self._make_cb(qc)
            self.add_item(btn)
    def _make_cb(self, qc):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid:
                return
            await i.response.defer(ephemeral=True)
            ok, msg = await accept_quest(i.user.id, qc)
            await i.followup.send(msg, ephemeral=True)
        return cb


class CraftView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=120)
        self.uid = uid
        for code in list(CRAFT_RECIPES.keys())[:8]:
            item = ITEM_CATALOG.get(code)
            if item:
                btn = discord.ui.Button(label=item.name[:20], style=discord.ButtonStyle.success)
                btn.callback = self._make_cb(code)
                self.add_item(btn)
    def _make_cb(self, code):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid:
                return
            await i.response.defer(ephemeral=True)
            p = await ensure_player(i.user.id, i.user.display_name)
            ok, msg = await craft_item(p, code)
            await i.followup.send(msg, ephemeral=True)
        return cb


class ShopView(discord.ui.View):
    def __init__(self, cog, uid, shop_items):
        super().__init__(timeout=120)
        self.uid = uid
        for code in shop_items[:5]:
            item = ITEM_CATALOG.get(code)
            price = SHOP_ITEMS.get(code, {}).get("price", 0)
            if item:
                btn = discord.ui.Button(label=f"{item.name[:15]} ({price}💰)", style=discord.ButtonStyle.success)
                btn.callback = self._make_cb(code, price)
                self.add_item(btn)
    def _make_cb(self, code, price):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid:
                return
            await i.response.defer(ephemeral=True)
            p = await ensure_player(i.user.id, i.user.display_name)
            if p.coins < price:
                await i.followup.send(f"코인 부족! (필요: {price:,})", ephemeral=True)
                return
            p.coins -= price
            await add_item(p.user_id, code)
            await save_player(p)
            await i.followup.send(f"✅ **{ITEM_CATALOG[code].name}** 구매 완료! 💰 -{price:,}", ephemeral=True)
        return cb


class HouseView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=120)
        self.uid = uid
    @discord.ui.button(label="🏠 집 구매", style=discord.ButtonStyle.success)
    async def buy(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        ok, msg = await buy_house(p)
        await i.followup.send(msg, ephemeral=True)
    @discord.ui.button(label="😴 집에서 휴식", style=discord.ButtonStyle.primary)
    async def rest(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        ok, msg = await rest_at_home(p)
        await i.followup.send(msg, ephemeral=True)
    @discord.ui.button(label="🚪 집 나가기", style=discord.ButtonStyle.secondary)
    async def leave(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        await i.response.defer(ephemeral=True)
        p = await ensure_player(i.user.id, i.user.display_name)
        p.state["in_house"] = False
        await save_player(p)
        await i.followup.send("집에서 나왔습니다.", ephemeral=True)


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
            await i.followup.send("길드 창설 비용: 💰 10,000코인", ephemeral=True)
            return
        existing = await fetch_one("SELECT guild_name FROM guilds WHERE guild_name=?", (self.name.value,))
        if existing:
            await i.followup.send("이미 존재하는 길드 이름입니다.", ephemeral=True)
            return
        p.coins -= 10000
        p.guild_id = self.name.value
        await execute("INSERT INTO guilds (guild_name,owner_id,notice,members_json) VALUES (?,?,?,?)", (self.name.value, p.user_id, self.notice.value or "", j([p.user_id])))
        await save_player(p)
        await i.followup.send(f"🏰 **{self.name.value}** 길드 창설 완료!", ephemeral=True)


class GuildJoinView(discord.ui.View):
    def __init__(self, cog, uid, rows):
        super().__init__(timeout=120)
        self.uid = uid
        for row in rows[:5]:
            btn = discord.ui.Button(label=row["guild_name"][:20], style=discord.ButtonStyle.primary)
            btn.callback = self._make_cb(row["guild_name"])
            self.add_item(btn)
    def _make_cb(self, guild_name):
        async def cb(i: discord.Interaction):
            if i.user.id != self.uid:
                return
            await i.response.defer(ephemeral=True)
            p = await ensure_player(i.user.id, i.user.display_name)
            row = await fetch_one("SELECT * FROM guilds WHERE guild_name=?", (guild_name,))
            if not row:
                await i.followup.send("길드를 찾을 수 없습니다.", ephemeral=True)
                return
            members = uj(row["members_json"])
            if p.user_id not in members:
                members.append(p.user_id)
                await execute("UPDATE guilds SET members_json=? WHERE guild_name=?", (j(members), guild_name))
            p.guild_id = guild_name
            await save_player(p)
            await i.followup.send(f"🏰 **{guild_name}** 길드에 가입했습니다!", ephemeral=True)
        return cb


class GuildView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=120)
        self.uid = uid
    @discord.ui.button(label="🏰 길드 창설", style=discord.ButtonStyle.success)
    async def create_guild(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id == self.uid:
            await i.response.send_modal(GuildCreateModal(self.uid))
    @discord.ui.button(label="🔍 길드 가입", style=discord.ButtonStyle.primary)
    async def join_guild(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        rows = await fetch_all("SELECT guild_name,owner_id FROM guilds LIMIT 10")
        if not rows:
            await i.response.send_message("등록된 길드가 없습니다.", ephemeral=True)
            return
        await i.response.send_message("🏰 길드 목록", view=GuildJoinView(None, self.uid, rows), ephemeral=True)
    @discord.ui.button(label="📋 내 길드", style=discord.ButtonStyle.secondary)
    async def my_guild(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        p = await ensure_player(i.user.id, i.user.display_name)
        if not p.guild_id:
            await i.response.send_message("길드에 가입되어 있지 않습니다.", ephemeral=True)
            return
        row = await fetch_one("SELECT * FROM guilds WHERE guild_name=?", (p.guild_id,))
        if not row:
            await i.response.send_message("길드 정보를 찾을 수 없습니다.", ephemeral=True)
            return
        members = uj(row["members_json"])
        await i.response.send_message(f"**🏰 {row['guild_name']}**\n공지: {row['notice'] or '없음'}\n금고: 💰{row['treasury']:,}\n멤버: {len(members)}명", ephemeral=True)


class NPCView(discord.ui.View):
    def __init__(self, cog, uid, npc_key, npc):
        super().__init__(timeout=120)
        self.cog = cog
        self.uid = uid
        self.npc = npc
        if npc.get("quests"):
            btn = discord.ui.Button(label="📜 퀘스트", style=discord.ButtonStyle.primary)
            btn.callback = self.show_quests
            self.add_item(btn)
        if "enchant" in npc.get("services", []):
            btn = discord.ui.Button(label="💎 강화", style=discord.ButtonStyle.primary)
            btn.callback = self.do_enchant
            self.add_item(btn)
        if "craft" in npc.get("services", []):
            btn = discord.ui.Button(label="⚒️ 제작", style=discord.ButtonStyle.success)
            btn.callback = self.show_craft
            self.add_item(btn)
        if "guild" in npc.get("services", []):
            btn = discord.ui.Button(label="🏰 길드", style=discord.ButtonStyle.secondary)
            btn.callback = self.show_guild
            self.add_item(btn)
        if "pvp" in npc.get("services", []):
            btn = discord.ui.Button(label="⚔️ PVP", style=discord.ButtonStyle.danger)
            btn.callback = self.show_pvp
            self.add_item(btn)
        if npc.get("shop_items"):
            btn = discord.ui.Button(label="🛒 상점", style=discord.ButtonStyle.success)
            btn.callback = self.show_shop
            self.add_item(btn)
    async def show_quests(self, i: discord.Interaction):
        if i.user.id == self.uid:
            quests = self.npc.get("quests", [])
            lines = ["**📜 퀘스트 목록**"] + [f"• **{QUEST_DATA.get(qc,{}).get('name',qc)}**: {QUEST_DATA.get(qc,{}).get('desc','')}" for qc in quests]
            await i.response.send_message("\n".join(lines), view=QuestListView(None, self.uid, quests), ephemeral=True)
    async def do_enchant(self, i: discord.Interaction):
        if i.user.id != self.uid:
            return
        rows = await fetch_all("SELECT * FROM inventory_items WHERE user_id=? AND item_type IN ('무기','방어구') LIMIT 5", (self.uid,))
        if not rows:
            await i.response.send_message("강화할 장비가 없습니다.", ephemeral=True)
            return
        await i.response.send_message("💎 강화할 아이템을 선택하세요:", view=EnchantSelectView(None, self.uid, rows), ephemeral=True)
    async def show_craft(self, i: discord.Interaction):
        if i.user.id != self.uid:
            return
        lines = ["**⚒️ 제작 목록**"]
        for code, recipe in CRAFT_RECIPES.items():
            item = ITEM_CATALOG.get(code)
            if item:
                mats = ", ".join(f"{ITEM_CATALOG.get(k, Item(k,k,'','')).name} x{v}" for k, v in recipe["재료"].items())
                lines.append(f"• **{item.name}**: {mats} + 💰{recipe['코인']:,}")
        await i.response.send_message("\n".join(lines), view=CraftView(None, self.uid), ephemeral=True)
    async def show_guild(self, i: discord.Interaction):
        if i.user.id == self.uid:
            await i.response.send_message("🏰 길드 메뉴", view=GuildView(None, self.uid), ephemeral=True)
    async def show_pvp(self, i: discord.Interaction):
        if i.user.id == self.uid:
            await i.response.send_message("⚔️ PVP 메뉴", view=PVPMenuView(self.cog, self.uid), ephemeral=True)
    async def show_shop(self, i: discord.Interaction):
        if i.user.id == self.uid:
            shop_items = self.npc.get("shop_items", [])
            lines = ["**🛒 상점**"]
            for code in shop_items:
                if code in ITEM_CATALOG:
                    lines.append(f"• {ITEM_CATALOG[code].name}: 💰 {SHOP_ITEMS.get(code,{}).get('price',0):,}")
            await i.response.send_message("\n".join(lines), view=ShopView(None, self.uid, shop_items), ephemeral=True)


class MinigameView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=120)
        self.uid = uid
    @discord.ui.button(label="🎣 낚시", style=discord.ButtonStyle.primary)
    async def fishing(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        msg = await start_fishing(self.uid)
        await i.response.send_message(msg, view=FishingReactView(None, self.uid), ephemeral=True)
    @discord.ui.button(label="🎰 슬롯머신", style=discord.ButtonStyle.danger)
    async def slots(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id == self.uid:
            await i.response.send_modal(SlotBetModal(self.uid))
    @discord.ui.button(label="🎲 주사위", style=discord.ButtonStyle.secondary)
    async def dice(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id == self.uid:
            await i.response.send_modal(DiceModal(self.uid))


class FishingReactView(discord.ui.View):
    def __init__(self, cog, uid):
        super().__init__(timeout=15)
        self.uid = uid
    @discord.ui.button(label="🎣 낚아채기!", style=discord.ButtonStyle.success)
    async def catch(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.uid:
            return
        await i.response.defer(ephemeral=True)
        ok, msg = await catch_fish(self.uid)
        await i.followup.send(msg, ephemeral=True)
        self.stop()


class SlotBetModal(discord.ui.Modal, title="슬롯머신 베팅"):
    bet = discord.ui.TextInput(label="베팅 코인", min_length=1, max_length=8)
    def __init__(self, uid):
        super().__init__()
        self.uid = uid
    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        try:
            bet = int(self.bet.value)
        except ValueError:
            await i.followup.send("올바른 숫자를 입력하세요.", ephemeral=True)
            return
        p = await ensure_player(i.user.id, i.user.display_name)
        ok, msg = await play_slots(p, bet)
        await i.followup.send(msg, ephemeral=True)


class DiceModal(discord.ui.Modal, title="주사위 도박"):
    bet = discord.ui.TextInput(label="베팅 코인", min_length=1, max_length=8)
    guess = discord.ui.TextInput(label="예측 숫자 (1~6)", min_length=1, max_length=1)
    def __init__(self, uid):
        super().__init__()
        self.uid = uid
    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        try:
            bet = int(self.bet.value)
            guess = int(self.guess.value)
        except ValueError:
            await i.followup.send("올바른 숫자를 입력하세요.", ephemeral=True)
            return
        p = await ensure_player(i.user.id, i.user.display_name)
        ok, msg = await play_dice(p, bet, guess)
        await i.followup.send(msg, ephemeral=True)



# ═══════════════════════════════════════════════════════════════════════
# 확인 / 수락 View
# ═══════════════════════════════════════════════════════════════════════
class MarriageConfirmView(discord.ui.View):
    def __init__(self, proposer_id: int, target_id: int):
        super().__init__(timeout=120)
        self.proposer_id = proposer_id
        self.target_id = target_id

    @discord.ui.button(label="💍 수락", style=discord.ButtonStyle.success)
    async def accept(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.target_id:
            await i.response.send_message("이 요청의 대상이 아닙니다.", ephemeral=True)
            return
        await i.response.defer()
        msg = await confirm_marriage(self.proposer_id, self.target_id)
        await i.followup.send(msg)
        self.stop()

    @discord.ui.button(label="❌ 거절", style=discord.ButtonStyle.danger)
    async def reject(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.target_id:
            await i.response.send_message("이 요청의 대상이 아닙니다.", ephemeral=True)
            return
        await i.response.send_message("💔 결혼 제안을 거절했습니다.")
        self.stop()


class BrotherhoodConfirmView(discord.ui.View):
    def __init__(self, proposer_id: int, target_id: int):
        super().__init__(timeout=120)
        self.proposer_id = proposer_id
        self.target_id = target_id

    @discord.ui.button(label="🤝 수락", style=discord.ButtonStyle.success)
    async def accept(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.target_id:
            await i.response.send_message("이 요청의 대상이 아닙니다.", ephemeral=True)
            return
        await i.response.defer()
        msg = await confirm_brotherhood(self.proposer_id, self.target_id)
        await i.followup.send(msg)
        self.stop()

    @discord.ui.button(label="❌ 거절", style=discord.ButtonStyle.danger)
    async def reject(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.target_id:
            await i.response.send_message("이 요청의 대상이 아닙니다.", ephemeral=True)
            return
        await i.response.send_message("❌ 의형제 제안을 거절했습니다.")
        self.stop()


class TeachJobConfirmView(discord.ui.View):
    def __init__(self, cog, request_id: str):
        super().__init__(timeout=120)
        self.cog = cog
        self.request_id = request_id

    @discord.ui.button(label="🎓 전직 전수 승인", style=discord.ButtonStyle.success)
    async def accept(self, i: discord.Interaction, b: discord.ui.Button):
        req = self.cog._teach_requests.get(self.request_id)
        if not req:
            await i.response.send_message("요청이 만료되었거나 존재하지 않습니다.", ephemeral=True)
            return
        if i.user.id != req["teacher_id"]:
            await i.response.send_message("스승만 승인할 수 있습니다.", ephemeral=True)
            return
        await i.response.defer()
        student_row = await fetch_one("SELECT * FROM players WHERE user_id=?", (req["student_id"],))
        if not student_row:
            await i.followup.send("제자를 찾을 수 없습니다.", ephemeral=True)
            self.cog._teach_requests.pop(self.request_id, None)
            self.stop()
            return
        student = PlayerRecord.from_row(student_row)
        ok, reason = check_job_requirements(student, req["job"])
        if not ok:
            await i.followup.send(f"제자가 아직 조건을 충족하지 못했습니다: {reason}", ephemeral=True)
            return
        ok2, msg = await apply_job_change(student, req["job"], learned_from=req["teacher_id"])
        self.cog._teach_requests.pop(self.request_id, None)
        self.stop()
        await i.followup.send(f"✅ 전수 완료: {msg}")

    @discord.ui.button(label="❌ 전수 거절", style=discord.ButtonStyle.danger)
    async def reject(self, i: discord.Interaction, b: discord.ui.Button):
        req = self.cog._teach_requests.get(self.request_id)
        if not req:
            await i.response.send_message("요청이 이미 처리되었습니다.", ephemeral=True)
            return
        if i.user.id != req["teacher_id"]:
            await i.response.send_message("스승만 거절할 수 있습니다.", ephemeral=True)
            return
        self.cog._teach_requests.pop(self.request_id, None)
        self.stop()
        await i.response.send_message("전직 전수를 거절했습니다.")


class PVPConfirmView(discord.ui.View):
    def __init__(self, cog, request_id: str):
        super().__init__(timeout=120)
        self.cog = cog
        self.request_id = request_id

    @discord.ui.button(label="⚔️ 결투 수락", style=discord.ButtonStyle.success)
    async def accept(self, i: discord.Interaction, b: discord.ui.Button):
        req = self.cog._pvp_requests.get(self.request_id)
        if not req:
            await i.response.send_message("PVP 요청이 만료되었거나 존재하지 않습니다.", ephemeral=True)
            return
        if i.user.id != req["target_id"]:
            await i.response.send_message("도전 받은 플레이어만 수락할 수 있습니다.", ephemeral=True)
            return
        await i.response.defer()
        p1 = await ensure_player(req["challenger_id"], f"User-{req['challenger_id']}")
        p2 = await ensure_player(req["target_id"], i.user.display_name)
        result = await fight_pvp(p1, p2)
        self.cog._pvp_requests.pop(self.request_id, None)
        self.stop()
        await i.followup.send(result["log"])

    @discord.ui.button(label="❌ 결투 거절", style=discord.ButtonStyle.danger)
    async def reject(self, i: discord.Interaction, b: discord.ui.Button):
        req = self.cog._pvp_requests.get(self.request_id)
        if not req:
            await i.response.send_message("PVP 요청이 이미 처리되었습니다.", ephemeral=True)
            return
        if i.user.id != req["target_id"]:
            await i.response.send_message("도전 받은 플레이어만 거절할 수 있습니다.", ephemeral=True)
            return
        self.cog._pvp_requests.pop(self.request_id, None)
        self.stop()
        await i.response.send_message("⚠️ PVP 도전을 거절했습니다.")


# ═══════════════════════════════════════════════════════════════════════
# 코그 / 명령어 / 이벤트
# ═══════════════════════════════════════════════════════════════════════
class RPGCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._teach_requests: Dict[str, dict] = {}
        self._pvp_requests: Dict[str, dict] = {}
        self._quiz_active: Dict[str, dict] = {}

    def _is_dev(self, uid: int) -> bool:
        return uid in settings.dev_ids

    async def broadcast_global_chat(self, message: discord.Message):
        rows = await fetch_all("SELECT guild_id, channel_id FROM global_chat_channels")
        p = await ensure_player(message.author.id, message.author.display_name)
        title_str = f"[{p.title}] " if p.title else ""
        embed = discord.Embed(
            description=message.content,
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(name=f"{title_str}{p.username} (Lv.{p.level})", icon_url=message.author.display_avatar.url)
        embed.set_footer(text=f"서버: {message.guild.name if message.guild else 'DM'}")
        for r in rows:
            if r["channel_id"] == str(message.channel.id):
                continue
            ch = self.bot.get_channel(int(r["channel_id"]))
            if ch:
                try:
                    await ch.send(embed=embed)
                except Exception:
                    pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.guild:
            row = await fetch_one(
                "SELECT channel_id FROM global_chat_channels WHERE guild_id=?",
                (str(message.guild.id),),
            )
            if row and str(message.channel.id) == row["channel_id"]:
                await self.broadcast_global_chat(message)
        ch_id = str(message.channel.id)
        quiz = self._quiz_active.get(ch_id)
        if quiz and message.author.id not in quiz["answered"]:
            if message.content.strip().lower() == quiz["answer"].strip().lower():
                quiz["answered"].add(message.author.id)
                p = await ensure_player(message.author.id, message.author.display_name)
                p.coins += quiz["reward"]
                await save_player(p)
                await message.channel.send(f"🎉 **{message.author.display_name}** 정답! 💰{quiz['reward']:,}코인 획득!")
                self._quiz_active.pop(ch_id, None)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before, after):
        if after.channel and len(after.channel.members) >= 2:
            p = await ensure_player(member.id, member.display_name)
            p.voice_bonus_until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            await save_player(p)

    @app_commands.command(name="rpg", description="RPG 자판기를 열고 개인 게임 창을 시작합니다")
    async def rpg(self, i: discord.Interaction):
        kiosk = (
            "```\n"
            "╔════════════════ 🎮 RPG 자판기 ════════════════╗\n"
            "║ A1 게임 시작   A2 전체 랭킹                  ║\n"
            "║ B1 PVP 킬보드  B2 도움말                    ║\n"
            "║ C1 초대        C2 버그 제보                 ║\n"
            "╚══════════════════════════════════════════════╝\n"
            "```\n"
            "버튼은 모두에게 보이지만, 실제 RPG 플레이 화면은 버튼을 누른 본인에게만 비공개로 열립니다."
        )
        await i.response.send_message(kiosk, view=RPGLobbyView(self), ephemeral=False)

    @app_commands.command(name="글로벌채팅설정", description="(관리자) 현재 채널을 글로벌 채팅 채널로 설정")
    @app_commands.default_permissions(administrator=True)
    async def set_global_chat(self, i: discord.Interaction):
        await execute(
            "INSERT OR REPLACE INTO global_chat_channels (guild_id, channel_id) VALUES (?,?)",
            (str(i.guild_id) if i.guild_id else "DM", str(i.channel_id)),
        )
        await i.response.send_message("✅ 이 채널이 글로벌 채팅 채널로 설정되었습니다!", ephemeral=True)

    @app_commands.command(name="퀴즈", description="(관리자) OX/주관식 퀴즈 이벤트 시작")
    @app_commands.default_permissions(administrator=True)
    async def quiz_event(self, i: discord.Interaction, 문제: str, 정답: str, 보상코인: int = 100):
        ch_id = str(i.channel_id)
        self._quiz_active[ch_id] = {"answer": 정답, "reward": 보상코인, "answered": set()}
        await i.response.send_message(
            f"**❓ 퀴즈 이벤트!**\n{문제}\n\n먼저 정답을 입력하면 💰{보상코인:,}코인을 획득합니다!"
        )

    @app_commands.command(name="공지", description="[DEV] 공지 브로드캐스트")
    async def announce(self, i: discord.Interaction, 내용: str):
        if not self._is_dev(i.user.id):
            await i.response.send_message("개발자 전용 명령어입니다.", ephemeral=True)
            return
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
        await i.followup.send(f"✅ {sent}개 채널에 공지를 전송했습니다.", ephemeral=True)

    @app_commands.command(name="dev_give_item", description="[DEV] 아이템 지급")
    async def dev_give_item(self, i: discord.Interaction, 유저: discord.User, 아이템코드: str, 수량: int = 1):
        if not self._is_dev(i.user.id):
            await i.response.send_message("❌ 개발자 전용 명령어입니다.", ephemeral=True)
            return
        item = ITEM_CATALOG.get(아이템코드)
        if not item:
            matches = [code for code, it in ITEM_CATALOG.items() if 아이템코드.lower() in code.lower() or 아이템코드.lower() in it.name.lower()]
            if matches:
                아이템코드 = matches[0]
                item = ITEM_CATALOG[아이템코드]
        if not item:
            await i.response.send_message(f"❌ 아이템 '{아이템코드}'를 찾을 수 없습니다.", ephemeral=True)
            return
        ok = await add_item(유저.id, 아이템코드, 수량)
        if ok:
            await i.response.send_message(f"✅ {유저.display_name}에게 **{item.name}** x{수량} 지급 완료.", ephemeral=True)
        else:
            await i.response.send_message("❌ 아이템 지급 실패.", ephemeral=True)

    @app_commands.command(name="dev_give_coin", description="[DEV] 코인 지급")
    async def dev_give_coin(self, i: discord.Interaction, 유저: discord.User, 코인: int):
        if not self._is_dev(i.user.id):
            await i.response.send_message("❌ 개발자 전용 명령어입니다.", ephemeral=True)
            return
        await execute("UPDATE players SET coins=coins+? WHERE user_id=?", (코인, 유저.id))
        await i.response.send_message(f"✅ {유저.display_name}에게 💰{코인:,}코인 지급 완료.", ephemeral=True)

    @app_commands.command(name="dev_give_gem", description="[DEV] 젬 지급")
    async def dev_give_gem(self, i: discord.Interaction, 유저: discord.User, 젬: int):
        if not self._is_dev(i.user.id):
            await i.response.send_message("❌ 개발자 전용 명령어입니다.", ephemeral=True)
            return
        await execute("UPDATE players SET gems=gems+? WHERE user_id=?", (젬, 유저.id))
        await i.response.send_message(f"✅ {유저.display_name}에게 💎{젬}젬 지급 완료.", ephemeral=True)

    @app_commands.command(name="dev_item_list", description="[DEV] 아이템 목록 조회")
    async def dev_item_list(self, i: discord.Interaction, 검색: str = ""):
        if not self._is_dev(i.user.id):
            await i.response.send_message("❌ 개발자 전용 명령어입니다.", ephemeral=True)
            return
        items = [(code, item) for code, item in ITEM_CATALOG.items() if not 검색 or 검색.lower() in code.lower() or 검색.lower() in item.name.lower()][:40]
        lines = [f"**📦 아이템 목록** (검색: {검색 or '전체'})"]
        for code, item in items:
            lines.append(f"`{code}` {item.name} [{item.rarity}] ATK:{item.power} DEF:{item.defense}")
        await i.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="dev_admin_report", description="[DEV] 관리자 리포트")
    async def dev_admin_report(self, i: discord.Interaction):
        if not self._is_dev(i.user.id):
            await i.response.send_message("❌ 개발자 전용 명령어입니다.", ephemeral=True)
            return
        total_players = await fetch_one("SELECT COUNT(*) as cnt FROM players")
        total_items = await fetch_one("SELECT COUNT(*) as cnt FROM inventory_items")
        total_auctions = await fetch_one("SELECT COUNT(*) as cnt FROM auction_house")
        total_errors = await fetch_one("SELECT COUNT(*) as cnt FROM error_logs")
        active_raids = await fetch_one("SELECT COUNT(*) as cnt FROM raid_sessions WHERE state='active'")
        db_size = DB_PATH.stat().st_size // 1024 if DB_PATH.exists() else 0
        report = (
            f"**📊 관리자 리포트**\n"
            f"총 플레이어: {total_players['cnt'] if total_players else 0}명\n"
            f"총 아이템: {total_items['cnt'] if total_items else 0}개\n"
            f"거래소 매물: {total_auctions['cnt'] if total_auctions else 0}개\n"
            f"에러 로그: {total_errors['cnt'] if total_errors else 0}건\n"
            f"활성 레이드: {active_raids['cnt'] if active_raids else 0}개\n"
            f"DB 용량: {db_size}KB"
        )
        try:
            await i.user.send(report)
            await i.response.send_message("✅ DM으로 리포트를 전송했습니다.", ephemeral=True)
        except Exception:
            await i.response.send_message(report, ephemeral=True)

    @app_commands.command(name="dev_reset_ranking", description="[DEV] 랭킹 초기화")
    async def dev_reset_ranking(self, i: discord.Interaction):
        if not self._is_dev(i.user.id):
            await i.response.send_message("❌ 개발자 전용 명령어입니다.", ephemeral=True)
            return
        await execute("DELETE FROM rankings WHERE season != 'watch'")
        await i.response.send_message("✅ 랭킹이 초기화되었습니다.", ephemeral=True)

    @app_commands.command(name="dev_season_reset", description="[DEV] 시즌 리셋 및 배틀패스 초기화")
    async def dev_season_reset(self, i: discord.Interaction):
        if not self._is_dev(i.user.id):
            await i.response.send_message("❌ 개발자 전용 명령어입니다.", ephemeral=True)
            return
        top_bp = await fetch_all("SELECT user_id,level FROM battlepass ORDER BY level DESC LIMIT 3")
        rewards = [10000, 5000, 2000]
        for idx, row in enumerate(top_bp):
            await execute("UPDATE players SET coins=coins+? WHERE user_id=?", (rewards[idx], row["user_id"]))
        await execute("UPDATE battlepass SET level=0,exp=0,season=season+1")
        await i.response.send_message(f"✅ 시즌 리셋 완료! 상위 {len(top_bp)}명에게 보상을 지급했습니다.", ephemeral=True)


class TaskCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.stamina_regen.start()
        self.auction_expire.start()

    def cog_unload(self):
        self.stamina_regen.cancel()
        self.auction_expire.cancel()

    @tasks.loop(minutes=5)
    async def stamina_regen(self):
        try:
            await execute("UPDATE players SET stamina=MIN(max_stamina, stamina+5)")
        except Exception as e:
            log.error(f"Stamina regen error: {e}")

    @tasks.loop(hours=1)
    async def auction_expire(self):
        try:
            now = datetime.now(timezone.utc).isoformat()
            expired = await fetch_all("SELECT * FROM auction_house WHERE end_at <= ?", (now,))
            for row in expired:
                item_data = uj(row["item_json"])
                if row["highest_bidder"]:
                    await execute(
                        "INSERT INTO inventory_items (user_id,item_code,item_name,item_type,rarity,qty,power,defense,meta_json) VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            row["highest_bidder"],
                            item_data.get("item_code", ""),
                            item_data.get("item_name", ""),
                            item_data.get("item_type", ""),
                            item_data.get("rarity", "일반"),
                            item_data.get("qty", 1),
                            item_data.get("power", 0),
                            item_data.get("defense", 0),
                            j(item_data.get("meta_json", {})) if isinstance(item_data.get("meta_json"), dict) else item_data.get("meta_json", "{}"),
                        ),
                    )
                    tax = int(row["current_bid"] * settings.trade_tax_percent / 100)
                    net = row["current_bid"] - tax
                    await execute("UPDATE players SET coins=coins+? WHERE user_id=?", (net, row["seller_id"]))
                else:
                    await execute(
                        "INSERT INTO inventory_items (user_id,item_code,item_name,item_type,rarity,qty,power,defense,meta_json) VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            row["seller_id"],
                            item_data.get("item_code", ""),
                            item_data.get("item_name", ""),
                            item_data.get("item_type", ""),
                            item_data.get("rarity", "일반"),
                            item_data.get("qty", 1),
                            item_data.get("power", 0),
                            item_data.get("defense", 0),
                            j(item_data.get("meta_json", {})) if isinstance(item_data.get("meta_json"), dict) else item_data.get("meta_json", "{}"),
                        ),
                    )
                await execute("DELETE FROM auction_house WHERE id=?", (row["id"],))
        except Exception as e:
            log.error(f"Auction expire error: {e}")


class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await init_db()
        self.rpg_cog = RPGCog(self)
        await self.add_cog(self.rpg_cog)
        await self.add_cog(TaskCog(self))
        await self.tree.sync()
        log.info("✅ 봇 초기화 완료 - 명령어 동기화 완료")

    async def on_ready(self):
        log.info(f"✅ {self.user} 로그인 완료! 서버 수: {len(self.guilds)}")
        await self.change_presence(activity=discord.Game(name="RPG 자판기 | /rpg"))

    async def on_error(self, event, *args, **kwargs):
        log.exception(f"이벤트 오류: {event}")


bot = Bot()

if __name__ == "__main__":
    token = settings.token
    if token == "TOKEN_HERE":
        log.error("❌ DISCORD_TOKEN이 설정되지 않았습니다! .env 파일을 확인하세요.")
        sys.exit(1)
    bot.run(token, log_handler=None)
